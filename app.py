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
import cv2
import base64

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB

WORK_DIR = os.path.join(tempfile.gettempdir(), "gerador_memes")
os.makedirs(WORK_DIR, exist_ok=True)

RESULTS = {}
UPLOADS = {}

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


@app.route("/detectar", methods=["POST"])
def detectar():
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
        vw, vh = meme_maker.get_video_size(entrada)
        dur = meme_maker.get_duration(entrada)
        frame_path = os.path.join(WORK_DIR, f"{job_id}_frame.png")
        
        meme_maker.run([
            "ffmpeg", "-y", "-ss", f"{dur/2:.2f}", "-i", entrada,
            "-frames:v", "1", "-update", "1", frame_path
        ])
        
        img = cv2.imread(frame_path)
        box = detector.detectar_card(img)
        conf = detector.confianca(img, box)
        
        if box is None:
            box = (int(vw * 0.08), int(vh * 0.30), int(vw * 0.84), int(vw * 0.84))
            conf = 0.0
            
        with open(frame_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        os.remove(frame_path)
    except Exception as e:
        try:
            os.remove(entrada)
        except OSError:
            pass
        return jsonify({"erro": f"Falha ao analisar: {e}"}), 500

    UPLOADS[job_id] = {"path": entrada, "nome": nome_seguro}
    _limpar_depois([entrada])

    return jsonify({
        "id": job_id,
        "largura": vw, "altura": vh,
        "box": {"x": box[0], "y": box[1], "w": box[2], "h": box[3]},
        "confianca": conf,
        "frame": "data:image/png;base64," + b64,
    })


@app.route("/ler-legenda", methods=["POST"])
def ler_legenda():
    if not CLAUDE_API_KEY:
        return jsonify({
            "erro": "A variável CLAUDE_API_KEY não está configurada no Railway."
        }), 500

    dados = request.get_json(silent=True) or {}
    imagem_b64 = (dados.get("imagem") or "").strip()
    if not imagem_b64:
        return jsonify({"erro": "Nenhuma imagem foi enviada para leitura."}), 400

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
                        "media_type": "image/png",
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
            "fade": True,
            "crf": 20
        }

    item = UPLOADS.get(job_id)
    if not item or not os.path.exists(item["path"]):
        return jsonify({"erro": "Vídeo expirado. Adicione novamente."}), 404
    if not legenda:
        return jsonify({"erro": "Digite uma legenda."}), 400

    entrada = item["path"]
    saida = os.path.join(WORK_DIR, f"{job_id}_post.mp4")

    try:
        if crop and all(k in crop for k in ("x", "y", "w", "h")):
            regiao = (crop["x"], crop["y"], crop["w"], crop["h"])
            meme_maker.make_post_from_crop(entrada, legenda, saida, regiao, perfil=perfil, uniqueness=uniqueness)
        else:
            meme_maker.make_post(entrada, legenda, saida, perfil=perfil, uniqueness=uniqueness)
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
