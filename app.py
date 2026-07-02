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
