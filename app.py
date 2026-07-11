from __future__ import annotations

import os
from pathlib import Path

import gradio as gr
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from core.config import APP_NAME, WORK_ROOT
from core.media import MediaError, probe_video
from core.processor import cleanup_old_jobs, process_video
from core.transition import detect_static_intro_end

cleanup_old_jobs()

CSS = """
.gradio-container {max-width: 1180px !important;}
.hero {padding: 18px 20px; border: 1px solid var(--border-color-primary); border-radius: 18px;}
.hero h1 {margin-bottom: 6px !important;}
.small-note {font-size: 0.92rem; opacity: 0.82;}
#generate-btn {min-height: 48px; font-weight: 700;}
"""


def transition_visibility(choice: str):
    return gr.update(visible=choice == "Informar o segundo manualmente")


def caption_visibility(choice: str):
    return gr.update(visible=choice == "Usar um texto fixo")


def analyze_video(video_path: str | None):
    if not video_path:
        raise gr.Error("Envie o vídeo primeiro.")
    try:
        info = probe_video(video_path)
        detected = detect_static_intro_end(video_path)
    except MediaError as exc:
        raise gr.Error(str(exc)) from exc

    if detected.seconds is None:
        text = (
            "### Análise do vídeo\n"
            f"- Duração: {info.duration:.2f}s\n"
            f"- Tamanho original: {info.width}×{info.height}\n"
            f"- FPS: {info.fps:.2f}\n"
            f"- Áudio: {'sim' if info.has_audio else 'não'}\n"
            f"- Resultado: {detected.message}\n\n"
            "Você pode escolher **Sem vídeo de continuação** ou informar o segundo manualmente."
        )
        return text, None

    text = (
        "### Análise do vídeo\n"
        f"- Duração: {info.duration:.2f}s\n"
        f"- Tamanho original: {info.width}×{info.height}\n"
        f"- FPS: {info.fps:.2f}\n"
        f"- Áudio: {'sim' if info.has_audio else 'não'}\n"
        f"- Transição provável: **{detected.seconds:.2f}s**\n"
        f"- Confiança aproximada: {detected.confidence:.0%}\n\n"
        "A geração automática usará esse ponto. Se estiver errado, selecione o modo manual."
    )
    return text, detected.seconds


def generate_video(
    photo_path: str | None,
    video_path: str | None,
    transition_mode: str,
    manual_transition_seconds: float | None,
    caption_mode: str,
    manual_caption_text: str,
    caption_position: str,
    caption_font_percent: float,
    continuation_fit_mode: str,
    language_label: str,
    progress=gr.Progress(),
):
    language_map = {
        "Português": "pt",
        "Detectar automaticamente": "",
        "Inglês": "en",
        "Espanhol": "es",
    }

    try:
        result = process_video(
            photo_path=photo_path or "",
            video_path=video_path or "",
            transition_mode=transition_mode,
            manual_transition_seconds=manual_transition_seconds,
            caption_mode=caption_mode,
            manual_caption_text=manual_caption_text or "",
            caption_position=caption_position,
            caption_font_percent=float(caption_font_percent),
            continuation_fit_mode=continuation_fit_mode,
            language=language_map.get(language_label, "pt"),
            progress=lambda value, description: progress(value, desc=description),
        )
    except MediaError as exc:
        raise gr.Error(str(exc)) from exc
    except Exception as exc:
        raise gr.Error(f"Erro inesperado: {exc}") from exc

    return result.output_path, result.report, result.transition_seconds


with gr.Blocks(title=APP_NAME) as demo:
    gr.Markdown(
        """
        <div class="hero">
          <h1>Foto + Vídeo Automático</h1>
          <p>Troca somente a foto estática inicial, recria a legenda, mantém o áudio original e preserva o vídeo de continuação.</p>
        </div>
        """
    )

    with gr.Row(equal_height=False):
        with gr.Column(scale=1):
            photo = gr.File(
                label="1. Foto nova da personagem",
                file_types=["image"],
                type="filepath",
            )
            video = gr.File(
                label="2. Vídeo com áudio e possível continuação",
                file_types=["video"],
                type="filepath",
            )
            gr.Markdown(
                "A proporção final segue a foto. No Railway, o maior lado é limitado a 1920 px por padrão para evitar estouro de memória; isso pode ser alterado por variável de ambiente.",
                elem_classes=["small-note"],
            )

            analyze_button = gr.Button("Analisar vídeo antes de gerar", variant="secondary")
            analysis_result = gr.Markdown()

        with gr.Column(scale=1):
            transition_mode = gr.Radio(
                choices=[
                    "Detectar automaticamente",
                    "Informar o segundo manualmente",
                    "Sem vídeo de continuação",
                ],
                value="Detectar automaticamente",
                label="Onde termina a foto estática?",
            )
            manual_transition = gr.Number(
                label="Segundo em que começa o vídeo de continuação",
                value=5.0,
                minimum=0.01,
                visible=False,
            )

            continuation_fit = gr.Radio(
                choices=[
                    "Manter inteiro com fundo desfocado",
                    "Barras pretas",
                    "Preencher a tela (pode cortar bordas)",
                ],
                value="Manter inteiro com fundo desfocado",
                label="Como encaixar o vídeo de continuação no tamanho da foto?",
            )

            caption_mode = gr.Radio(
                choices=[
                    "Transcrever o áudio automaticamente",
                    "Usar um texto fixo",
                    "Sem legenda",
                ],
                value="Transcrever o áudio automaticamente",
                label="Legenda da parte da foto",
            )
            manual_caption = gr.Textbox(
                label="Texto da legenda",
                placeholder="Digite a frase que ficará sobre a foto...",
                lines=3,
                visible=False,
            )

            with gr.Row():
                caption_position = gr.Dropdown(
                    choices=["Centro", "Centro inferior", "Centro superior"],
                    value="Centro",
                    label="Posição",
                )
                caption_size = gr.Slider(
                    minimum=2.5,
                    maximum=8.0,
                    value=4.6,
                    step=0.1,
                    label="Tamanho da fonte (% da altura)",
                )

            language = gr.Dropdown(
                choices=["Português", "Detectar automaticamente", "Inglês", "Espanhol"],
                value="Português",
                label="Idioma da transcrição",
            )

    generate_button = gr.Button("GERAR VÍDEO", variant="primary", elem_id="generate-btn")

    with gr.Row(equal_height=False):
        output_video = gr.Video(label="Vídeo pronto")
        with gr.Column():
            report = gr.Markdown()
            transition_used = gr.Number(label="Segundo da transição usado", interactive=False)

    transition_mode.change(
        fn=transition_visibility,
        inputs=transition_mode,
        outputs=manual_transition,
    )
    caption_mode.change(
        fn=caption_visibility,
        inputs=caption_mode,
        outputs=manual_caption,
    )
    analyze_button.click(
        fn=analyze_video,
        inputs=video,
        outputs=[analysis_result, manual_transition],
    )
    generate_button.click(
        fn=generate_video,
        inputs=[
            photo,
            video,
            transition_mode,
            manual_transition,
            caption_mode,
            manual_caption,
            caption_position,
            caption_size,
            continuation_fit,
            language,
        ],
        outputs=[output_video, report, transition_used],
        api_name="generate",
    )


demo.queue(max_size=8, default_concurrency_limit=1)

fastapi_app = FastAPI(title=APP_NAME)


@fastapi_app.get("/health")
def health():
    return JSONResponse({"status": "ok"})


@fastapi_app.get("/api/info")
def api_info():
    return {
        "name": APP_NAME,
        "work_root": str(WORK_ROOT),
        "status": "online",
    }


username = os.getenv("APP_USERNAME", "").strip()
password = os.getenv("APP_PASSWORD", "").strip()
auth = (username, password) if username and password else None

app = gr.mount_gradio_app(
    fastapi_app,
    demo,
    path="/",
    allowed_paths=[str(Path(WORK_ROOT).resolve())],
    max_file_size=os.getenv("MAX_UPLOAD_SIZE", "500mb"),
    auth=auth,
    show_error=True,
    css=CSS,
)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "7860"))
    uvicorn.run(app, host="0.0.0.0", port=port, proxy_headers=True, forwarded_allow_ips="*")
