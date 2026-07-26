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

# Limites configuráveis para evitar que uploads gigantes prendam o servidor.
MAX_VIDEO_UPLOAD_MB = int(os.environ.get("MAX_VIDEO_UPLOAD_MB", "200"))
MAX_LEGENDA_BODY_MB = int(os.environ.get("MAX_LEGENDA_BODY_MB", "15"))
MAX_VIDEO_UPLOAD_BYTES = MAX_VIDEO_UPLOAD_MB * 1024 * 1024
MAX_LEGENDA_BODY_BYTES = MAX_LEGENDA_BODY_MB * 1024 * 1024
PREVIEW_MAX_WIDTH = int(os.environ.get("PREVIEW_MAX_WIDTH", "720"))
app.config["MAX_CONTENT_LENGTH"] = MAX_VIDEO_UPLOAD_BYTES

WORK_DIR = os.path.join(tempfile.gettempdir(), "gerador_memes")
os.makedirs(WORK_DIR, exist_ok=True)

RESULTS = {}
UPLOADS = {}

# O meme_maker altera configurações globais ao trocar de perfil.
# Este lock impede que duas gerações simultâneas misturem os perfis.
GERACAO_LOCK = threading.Lock()
# Evita que um plano pequeno do Railway tente analisar vários vídeos ao mesmo tempo.
DETECCAO_SEMAFORO = threading.Semaphore(2)
OCR_SEMAFORO = threading.Semaphore(2)

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


# O HTML antigo dispara todos os uploads e todas as leituras ao mesmo tempo.
# Este patch mantém compatibilidade com o template atual, mas organiza tudo em
# uma fila, compacta o recorte no navegador e aplica limites de tempo claros.
INDEX_OTIMIZACAO_JS = r"""
<script>
(() => {
  const filaAnaliseOtimizada = [];
  let filaAnaliseRodando = false;

  async function fetchComTimeout(url, options, timeoutMs) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await fetch(url, {...options, signal: controller.signal});
    } finally {
      clearTimeout(timer);
    }
  }

  async function respostaJson(resp) {
    const texto = await resp.text();
    try { return JSON.parse(texto); }
    catch (_) {
      throw new Error(resp.ok ? 'Resposta inválida do servidor.' : `Servidor retornou erro ${resp.status}.`);
    }
  }

  adicionar = function(fileList) {
    let adicionados = 0;
    for (const f of fileList) {
      if (!f.type.startsWith('video/')) continue;
      if (f.size > 200 * 1024 * 1024) {
        alert(`${f.name}: o limite é 200 MB.`);
        continue;
      }
      const it = {
        localId: proximoId++, file: f, nome: f.name,
        estado: 'analisando', jobId: null, legenda: '',
        vw: 0, vh: 0, frame: null, box: null, conf: 0,
        _lendoLegenda: false
      };
      itens.push(it);
      filaAnaliseOtimizada.push(it);
      adicionados++;
    }
    if (adicionados) {
      render();
      processarFilaAnaliseOtimizada();
    }
  };

  async function processarFilaAnaliseOtimizada() {
    if (filaAnaliseRodando) return;
    filaAnaliseRodando = true;
    try {
      while (filaAnaliseOtimizada.length) {
        const it = filaAnaliseOtimizada.shift();
        if (!itens.includes(it)) continue;
        await analisar(it);
      }
    } finally {
      filaAnaliseRodando = false;
    }
  }

  analisar = async function(it) {
    const fd = new FormData();
    fd.append('video', it.file);
    it.estado = 'analisando';
    it.msgErro = '';
    render();

    try {
      const resp = await fetchComTimeout('/detectar', {
        method: 'POST', body: fd
      }, 240000);
      const j = await respostaJson(resp);
      if (!resp.ok) throw new Error(j.erro || 'Erro ao analisar o vídeo.');

      it.jobId = j.id;
      it.vw = j.largura;
      it.vh = j.altura;
      it.frame = j.frame;
      it.box = j.box;
      it.conf = j.confianca;
      it.estado = j.confianca >= 0.6 ? 'auto' : 'conferir';
      render();

      if (it.frame) await lerLegenda(it);
    } catch (err) {
      it.estado = 'erro_analise';
      it.msgErro = err && err.name === 'AbortError'
        ? 'O envio demorou demais. Tente um vídeo menor ou uma conexão mais estável.'
        : (err.message || 'Falha ao analisar.');
    }
    render();
  };

  lerLegenda = async function(it) {
    if (!it.frame || it._lendoLegenda) return;
    it._lendoLegenda = true;
    try {
      const imgEl = new Image();
      await new Promise((resolve, reject) => {
        imgEl.onload = resolve;
        imgEl.onerror = reject;
        imgEl.src = it.frame;
      });

      const scaleY = imgEl.naturalHeight / it.vh;
      const faixaOriginal = Math.round(it.box.y * scaleY);
      if (faixaOriginal < 20) return;

      const maxW = 720;
      const fator = Math.min(1, maxW / imgEl.naturalWidth);
      const canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.round(imgEl.naturalWidth * fator));
      canvas.height = Math.max(1, Math.round(faixaOriginal * fator));
      canvas.getContext('2d').drawImage(
        imgEl,
        0, 0, imgEl.naturalWidth, faixaOriginal,
        0, 0, canvas.width, canvas.height
      );

      const b64 = canvas.toDataURL('image/jpeg', 0.78).split(',')[1];
      const resp = await fetchComTimeout('/ler-legenda', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({imagem: b64})
      }, 50000);
      const j = await respostaJson(resp);
      if (!resp.ok) throw new Error(j.erro || 'Falha ao ler a legenda.');

      const texto = (j.texto || '').trim();
      if (texto) it.legenda = texto;
    } catch (err) {
      console.error('Leitura de legenda:', err);
      it.msgErroLegenda = err && err.name === 'AbortError'
        ? 'A leitura da legenda excedeu 50 segundos.'
        : (err.message || 'Falha ao ler a legenda.');
    } finally {
      it._lendoLegenda = false;
      render();
    }
  };
})();
</script>
"""


@app.route("/")
def index():
    html = render_template("index.html")
    if "</body>" in html:
        html = html.replace("</body>", INDEX_OTIMIZACAO_JS + "\n</body>", 1)
    return html


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


@app.route("/detectar", methods=["POST"])
def detectar():
    if _corpo_maior_que(MAX_VIDEO_UPLOAD_BYTES):
        return jsonify({
            "erro": f"Vídeo muito grande. Envie um arquivo de até {MAX_VIDEO_UPLOAD_MB} MB."
        }), 413

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
        # O upload continua sendo salvo em qualidade original, mas o frame enviado
        # ao navegador é uma prévia JPEG pequena. Antes era um PNG na resolução
        # total do vídeo, o que deixava a página e a leitura da legenda muito lentas.
        with DETECCAO_SEMAFORO:
            vw, vh = meme_maker.get_video_size(entrada)
            dur = meme_maker.get_duration(entrada)
            frame_path = os.path.join(WORK_DIR, f"{job_id}_frame.jpg")

            meme_maker.run([
                "ffmpeg", "-y", "-ss", f"{dur/2:.2f}", "-i", entrada,
                "-frames:v", "1",
                "-vf", f"scale='min({PREVIEW_MAX_WIDTH},iw)':-2",
                "-q:v", "5",
                frame_path
            ])

            img = cv2.imread(frame_path)
            if img is None:
                raise RuntimeError("não foi possível criar a prévia do vídeo")

            ph, pw = img.shape[:2]
            box_preview = detector.detectar_card(img)
            conf = detector.confianca(img, box_preview)

            if box_preview is None:
                box = (
                    int(vw * 0.08), int(vh * 0.30),
                    int(vw * 0.84), int(vw * 0.84)
                )
                conf = 0.0
            else:
                # Converte o recorte detectado na prévia para as coordenadas
                # do vídeo original, usadas depois na geração final.
                sx = vw / pw
                sy = vh / ph
                x, y, bw, bh = box_preview
                box = (
                    int(round(x * sx)), int(round(y * sy)),
                    int(round(bw * sx)), int(round(bh * sy))
                )

            with open(frame_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
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
        "frame": "data:image/jpeg;base64," + b64,
    })


def _compactar_imagem_ocr(imagem_b64):
    """Reduz a imagem antes de enviá-la ao Claude para ganhar velocidade."""
    try:
        bruto = base64.b64decode(imagem_b64, validate=False)
        arr = __import__("numpy").frombuffer(bruto, dtype=__import__("numpy").uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("imagem inválida")

        h, w = img.shape[:2]
        limite = 900
        if w > limite:
            escala = limite / w
            img = cv2.resize(
                img,
                (limite, max(1, int(round(h * escala)))),
                interpolation=cv2.INTER_AREA,
            )

        ok, jpg = cv2.imencode(
            ".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 80]
        )
        if not ok:
            raise ValueError("falha ao compactar imagem")
        return base64.b64encode(jpg.tobytes()).decode("ascii"), "image/jpeg"
    except Exception as e:
        raise ValueError(f"Não foi possível preparar a imagem: {e}") from e


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
    # Aceita tanto base64 puro quanto data:image/png;base64,...
    if imagem_b64.startswith("data:image/") and "," in imagem_b64:
        imagem_b64 = imagem_b64.split(",", 1)[1].strip()

    if not imagem_b64:
        return jsonify({"erro": "Nenhuma imagem foi enviada para leitura."}), 400

    try:
        imagem_b64, media_type = _compactar_imagem_ocr(imagem_b64)
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400

    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": 256,
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
        with OCR_SEMAFORO:
            with urllib.request.urlopen(req, timeout=35) as resposta:
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
            "subtle_grain": False,
            "speed_factor": 1.01,
            "crf": 18,
            "preset": "veryfast",
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
