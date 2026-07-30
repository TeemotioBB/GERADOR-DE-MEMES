#!/usr/bin/env python3
"""
Servidor web local do Gerador de Memes (Adulto Sofrido).
"""

import os
import io
import uuid
import zipfile
import tempfile
import threading
import time
import json
import urllib.request
import urllib.error
from flask import Flask, request, send_file, render_template, jsonify
from werkzeug.utils import secure_filename

import meme_maker
import detector
import instagram_import
import cv2
import base64

app = Flask(__name__)

# Limites configuráveis para evitar que uploads gigantes prendam o servidor.
MAX_VIDEO_UPLOAD_MB = int(os.environ.get("MAX_VIDEO_UPLOAD_MB", "200"))
MAX_LEGENDA_BODY_MB = int(os.environ.get("MAX_LEGENDA_BODY_MB", "15"))
MAX_VIDEO_UPLOAD_BYTES = MAX_VIDEO_UPLOAD_MB * 1024 * 1024
MAX_LEGENDA_BODY_BYTES = MAX_LEGENDA_BODY_MB * 1024 * 1024
app.config["MAX_CONTENT_LENGTH"] = MAX_VIDEO_UPLOAD_BYTES

WORK_DIR = os.path.join(tempfile.gettempdir(), "gerador_memes")
os.makedirs(WORK_DIR, exist_ok=True)

RESULTS = {}
UPLOADS = {}

# O meme_maker altera configurações globais ao trocar de perfil.
# Este lock impede que duas gerações simultâneas misturem os perfis.
GERACAO_LOCK = threading.Lock()

# Evita que vários uploads executem FFmpeg/OpenCV ao mesmo tempo e estourem
# a memória do Railway. A interface também usa fila, mas este lock protege
# o servidor caso existam duas abas ou dois usuários simultâneos.
ANALISE_LOCK = threading.Lock()

# A chave fica somente no servidor/Railway e nunca é enviada ao navegador.
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "").strip()
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001").strip()


def _limpar_depois(paths, delay=3600):
    def job():
        time.sleep(delay)
        for p in paths:
            try:
                os.remove(p)
            except OSError:
                pass
    threading.Thread(target=job, daemon=True).start()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.errorhandler(413)
def arquivo_grande(_erro):
    return jsonify({
        "erro": f"Arquivo muito grande. O limite atual é {MAX_VIDEO_UPLOAD_MB} MB."
    }), 413


def _corpo_maior_que(limite_bytes):
    tamanho = request.content_length
    return tamanho is not None and tamanho > limite_bytes


def _analisar_arquivo(entrada, nome_seguro, job_id):
    """Executa a análise comum para upload manual e importação por URL."""
    frame_path = os.path.join(WORK_DIR, f"{job_id}_frame.jpg")

    try:
        with ANALISE_LOCK:
            vw, vh = meme_maker.get_video_size(entrada)
            dur = meme_maker.get_duration(entrada)

            meme_maker.run([
                "ffmpeg", "-y",
                "-ss", f"{max(0.0, dur / 2):.2f}",
                "-i", entrada,
                "-frames:v", "1",
                "-vf", "scale=720:-2:force_original_aspect_ratio=decrease",
                "-q:v", "4",
                frame_path,
            ])

            img = cv2.imread(frame_path)
            if img is None:
                raise RuntimeError("O FFmpeg não conseguiu criar o frame de análise.")

            frame_h, frame_w = img.shape[:2]
            box_frame = detector.detectar_card(img)
            conf = detector.confianca(img, box_frame)

            if box_frame is None:
                box = (
                    int(vw * 0.08), int(vh * 0.30),
                    int(vw * 0.84), int(vw * 0.84),
                )
                conf = 0.0
            else:
                escala_x = vw / frame_w
                escala_y = vh / frame_h
                x, y, bw, bh = box_frame
                box = (
                    int(round(x * escala_x)), int(round(y * escala_y)),
                    int(round(bw * escala_x)), int(round(bh * escala_y)),
                )

            with open(frame_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
    finally:
        try:
            os.remove(frame_path)
        except OSError:
            pass

    UPLOADS[job_id] = {"path": entrada, "nome": nome_seguro}
    _limpar_depois([entrada])

    return {
        "id": job_id,
        "largura": vw,
        "altura": vh,
        "box": {"x": box[0], "y": box[1], "w": box[2], "h": box[3]},
        "confianca": conf,
        "frame": "data:image/jpeg;base64," + b64,
        "nome": nome_seguro,
    }


@app.route("/detectar", methods=["POST"])
def detectar():
    if _corpo_maior_que(MAX_VIDEO_UPLOAD_BYTES):
        return jsonify({"erro": f"Vídeo muito grande. Envie um arquivo de até {MAX_VIDEO_UPLOAD_MB} MB."}), 413
    if "video" not in request.files:
        return jsonify({"erro": "Nenhum video enviado."}), 400

    video = request.files["video"]
    if video.filename == "":
        return jsonify({"erro": "Nenhum video selecionado."}), 400

    job_id = uuid.uuid4().hex
    nome_seguro = secure_filename(video.filename) or "video.mp4"
    entrada = os.path.join(WORK_DIR, f"{job_id}_in_{nome_seguro}")
    video.save(entrada)

    try:
        return jsonify(_analisar_arquivo(entrada, nome_seguro, job_id))
    except Exception as e:
        try:
            os.remove(entrada)
        except OSError:
            pass
        return jsonify({"erro": f"Falha ao analisar: {e}"}), 500


@app.route("/importar-instagram", methods=["POST"])
def importar_instagram():
    dados = request.get_json(silent=True) or {}
    url = (dados.get("url") or "").strip()
    job_id = uuid.uuid4().hex
    entrada = None

    try:
        entrada, nome = instagram_import.baixar_video_instagram(
            url=url,
            pasta_destino=WORK_DIR,
            identificador=job_id,
            limite_mb=MAX_VIDEO_UPLOAD_MB,
        )
        return jsonify(_analisar_arquivo(entrada, nome, job_id))
    except instagram_import.InstagramImportError as e:
        if entrada:
            try:
                os.remove(entrada)
            except OSError:
                pass
        return jsonify({"erro": str(e)}), 400
    except Exception as e:
        if entrada:
            try:
                os.remove(entrada)
            except OSError:
                pass
        return jsonify({"erro": f"Falha ao importar o Reels: {e}"}), 500


@app.route("/ler-legenda", methods=["POST"])
def ler_legenda():
    if _corpo_maior_que(MAX_LEGENDA_BODY_BYTES):
        return jsonify({
            "erro": "A imagem enviada para leitura ficou grande demais. Tente novamente."
        }), 413

    if not CLAUDE_API_KEY:
        return jsonify({
            "erro": "A variável CLAUDE_API_KEY não está configurada no Railway."
        }), 500

    dados = request.get_json(silent=True) or {}
    imagem_b64 = (dados.get("imagem") or "").strip()

    # Se vier no formato data:image/xxx;base64,..., usa o tipo declarado ali
    # (é o que o navegador realmente gerou) e separa só a parte em base64.
    media_type_declarado = None
    if imagem_b64.startswith("data:image/") and "," in imagem_b64:
        cabecalho, imagem_b64 = imagem_b64.split(",", 1)
        imagem_b64 = imagem_b64.strip()
        # cabecalho é algo como "data:image/jpeg;base64"
        media_type_declarado = cabecalho[len("data:"):].split(";")[0].strip()

    if not imagem_b64:
        return jsonify({"erro": "Nenhuma imagem foi enviada para leitura."}), 400

    # A Claude API valida o media_type contra os bytes reais da imagem, então
    # não basta confiar no que veio do cliente: conferimos a assinatura
    # (magic bytes) do arquivo decodificado e usamos o tipo verdadeiro.
    try:
        cabecalho_bytes = base64.b64decode(imagem_b64[:64] + "==")
    except Exception:
        cabecalho_bytes = b""

    if cabecalho_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        media_type = "image/png"
    elif cabecalho_bytes.startswith(b"\xff\xd8\xff"):
        media_type = "image/jpeg"
    elif cabecalho_bytes.startswith(b"GIF87a") or cabecalho_bytes.startswith(b"GIF89a"):
        media_type = "image/gif"
    elif cabecalho_bytes[:4] == b"RIFF" and cabecalho_bytes[8:12] == b"WEBP":
        media_type = "image/webp"
    else:
        # Não deu para identificar pelos bytes; usa o que o navegador disse,
        # e se nem isso tiver vindo, cai para jpeg (formato mais comum de
        # captura de frame/canvas).
        media_type = media_type_declarado or "image/jpeg"

    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": 512,
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": imagem_b64
                    }
                },
                {
                    "type": "text",
                    "text": (
                        "Essa é a parte de cima de um post de rede social. "
                        "Extraia APENAS o texto da legenda (não o nome nem o @). "
                        "Copie exatamente como está, com emojis e quebras de linha. "
                        "Retorne só o texto, sem explicação."
                    )
                }
            ]
        }]
    }

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-api-key": CLAUDE_API_KEY,
            "anthropic-version": "2023-06-01"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resposta:
            retorno = json.loads(resposta.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detalhe = ""
        try:
            erro_api = json.loads(e.read().decode("utf-8"))
            detalhe = erro_api.get("error", {}).get("message", "")
        except Exception:
            pass
        mensagem = detalhe or f"Claude retornou erro HTTP {e.code}."
        return jsonify({"erro": mensagem}), 502
    except urllib.error.URLError as e:
        return jsonify({"erro": f"Não foi possível conectar ao Claude: {e.reason}"}), 502
    except Exception as e:
        return jsonify({"erro": f"Falha ao consultar o Claude: {e}"}), 500

    blocos = retorno.get("content") or []
    texto = next(
        (bloco.get("text", "").strip() for bloco in blocos
         if bloco.get("type") == "text" and bloco.get("text")),
        ""
    )
    if not texto:
        return jsonify({"erro": "O Claude não retornou uma legenda."}), 502

    return jsonify({"texto": texto})


@app.route("/gerar", methods=["POST"])
def gerar():
    dados = request.json if request.is_json else {}
    job_id = dados.get("id")
    legenda = (dados.get("legenda") or "").strip()
    crop = dados.get("crop")
    perfil = dados.get("perfil")

    # === NOVAS OPÇÕES DE ANTI-DETECÇÃO ===
    uniqueness = dados.get("uniqueness", {})
    # Configuração padrão focada em qualidade
    if not uniqueness:
        uniqueness = {
            "light_crop": True,
            "color_adjust": True,
            "subtle_grain": True,
            "speed_factor": 1.01,
            "crf": 18,
            "preset": "slow",
            "deep_metadata_clean": True,
            "remove_h264_sei": True
        }

    item = UPLOADS.get(job_id)
    if not item or not os.path.exists(item["path"]):
        return jsonify({"erro": "Vídeo expirado. Adicione novamente."}), 404
    if not legenda:
        return jsonify({"erro": "Digite uma legenda."}), 400

    entrada = item["path"]
    saida = os.path.join(WORK_DIR, f"{job_id}_post.mp4")

    try:
        # Gerações ficam em fila para impedir mistura de configurações entre perfis.
        # As demais rotas continuam atendendo graças ao worker gthread do Procfile.
        with GERACAO_LOCK:
            if crop and all(k in crop for k in ("x", "y", "w", "h")):
                regiao = (crop["x"], crop["y"], crop["w"], crop["h"])
                meme_maker.make_post_from_crop(
                    entrada, legenda, saida, regiao,
                    perfil=perfil, uniqueness=uniqueness
                )
            else:
                meme_maker.make_post(
                    entrada, legenda, saida,
                    perfil=perfil, uniqueness=uniqueness
                )
    except Exception as e:
        return jsonify({"erro": f"Falha ao gerar: {e}"}), 500

    base = os.path.splitext(item["nome"])[0]
    nome_saida = f"post_{base}.mp4"
    RESULTS[job_id] = {"path": saida, "nome": nome_saida}
    _limpar_depois([saida])
    return jsonify({"id": job_id})


@app.route("/zip", methods=["POST"])
def baixar_zip():
    ids = request.json.get("ids", []) if request.is_json else []
    if not ids:
        return "Nenhum item para baixar.", 400

    buf = io.BytesIO()
    usados = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for job_id in ids:
            item = RESULTS.get(job_id)
            if not item or not os.path.exists(item["path"]):
                continue
            nome = item["nome"]
            n = nome
            i = 2
            while n in usados:
                base, ext = os.path.splitext(nome)
                n = f"{base}_{i}{ext}"
                i += 1
            usados.add(n)
            zf.write(item["path"], n)

    if not usados:
        return "Arquivos expirados. Gere novamente.", 404

    buf.seek(0)
    return send_file(buf, mimetype="application/zip",
                     as_attachment=True, download_name="posts.zip")


@app.route("/baixar/<job_id>")
def baixar(job_id):
    item = RESULTS.get(job_id)
    if not item or not os.path.exists(item["path"]):
        return "Arquivo expirado ou inexistente. Gere novamente.", 404
    return send_file(item["path"], as_attachment=True, download_name=item["nome"])


@app.route("/preview/<job_id>")
def preview(job_id):
    item = RESULTS.get(job_id)
    if not item or not os.path.exists(item["path"]):
        return "Arquivo expirado.", 404
    return send_file(item["path"], mimetype="video/mp4")


if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 5000))
    na_nuvem = "PORT" in os.environ
    host = "0.0.0.0" if na_nuvem else "127.0.0.1"
    if not na_nuvem:
        print("\n" + "=" * 50)
        print("  Gerador de Memes - Adulto Sofrido")
        print("  Abra no navegador: http://localhost:5000")
        print("=" * 50 + "\n")
    app.run(host=host, port=porta, debug=False)
