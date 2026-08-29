#!/usr/bin/env python3
"""
Servidor web local do Gerador de Memes (Adulto Sofrido).
"""

import os
import sys
import uuid
import zipfile
import tempfile
import threading
import time
import json
import urllib.request
import urllib.error
import hashlib
import subprocess
import heapq
import itertools
import gc
import ctypes
import binascii
import base64
from flask import Flask, request, send_file, render_template, jsonify
from werkzeug.utils import secure_filename

app = Flask(__name__)

# Limites configuráveis para evitar que uploads gigantes prendam o servidor.
MAX_VIDEO_UPLOAD_MB = int(os.environ.get("MAX_VIDEO_UPLOAD_MB", "200"))
MAX_LEGENDA_BODY_MB = int(os.environ.get("MAX_LEGENDA_BODY_MB", "15"))
MAX_VIDEO_UPLOAD_BYTES = MAX_VIDEO_UPLOAD_MB * 1024 * 1024
MAX_LEGENDA_BODY_BYTES = MAX_LEGENDA_BODY_MB * 1024 * 1024
app.config["MAX_CONTENT_LENGTH"] = MAX_VIDEO_UPLOAD_BYTES

WORK_DIR = os.path.join(tempfile.gettempdir(), "gerador_memes")
os.makedirs(WORK_DIR, exist_ok=True)
PREVIEW_DIR = os.path.join(WORK_DIR, "previews")
os.makedirs(PREVIEW_DIR, exist_ok=True)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ANALYSIS_WORKER = os.path.join(BASE_DIR, "analysis_worker.py")
RENDER_WORKER = os.path.join(BASE_DIR, "render_worker.py")
INSTAGRAM_WORKER = os.path.join(BASE_DIR, "instagram_worker.py")

RESULTS = {}
UPLOADS = {}

# O meme_maker altera configurações globais ao trocar de perfil.
# Este lock impede que duas gerações simultâneas misturem os perfis.
GERACAO_LOCK = threading.Lock()

# Evita que vários uploads executem FFmpeg/OpenCV ao mesmo tempo e estourem
# a memória do Railway. A interface também usa fila, mas este lock protege
# o servidor caso existam duas abas ou dois usuários simultâneos.
ANALISE_LOCK = threading.Lock()

# ----------------- AUTOMAÇÃO REMOTA DA ABA DE REELS -----------------
# O Railway guarda a tarefa e recebe os vídeos já baixados pelo PC.
# O agente local abre o Chrome, coleta/filtra os Reels, baixa no próprio PC
# e envia os arquivos para o Railway analisar. O Railway não precisa acessar
# o Instagram durante a automação.
AUTOMACAO_TOKEN = os.environ.get("AUTOMACAO_TOKEN", "").strip()

AUTOMACAO_LOCK = threading.Lock()
AUTOMACAO_STATUS = {
    "rodando": False,
    "estado": "parado",
    "mensagem": "Pronto para começar.",
    "job_id": None,
    "quantidade": 0,
    "filtro_padrao": False,
    "encontrados": 0,
    "links": [],
    "itens": [],
    "falhas": [],
    "erro": None,
    "agente_ultimo_ping": 0.0,
    "agente_nome": None,
    "agente_sessao": None,
    "capacidades": {},
}


# Fila de renderização local. Se o PC estiver disponível e tiver FFmpeg,
# o site usa o agente; caso contrário, a interface cai automaticamente
# para a rota /gerar do Railway.
GERACAO_PC_LOCK = threading.Lock()
GERACAO_PC_FILA = []
GERACAO_PC_STATUS = {}
COLETA_STALE_SECONDS = int(os.environ.get("COLETA_STALE_SECONDS", "120"))


def _reencaminhar_geracoes_de_sessao_antiga(nova_sessao):
    """
    Se o agente foi fechado/reaberto, tarefas que estavam marcadas como
    processando pertencem ao processo antigo. Recoloca na fila sem criar
    uma segunda tarefa nem cobrar renderização no Railway.
    """
    if not nova_sessao:
        return 0

    reencaminhadas = 0
    with GERACAO_PC_LOCK:
        for task_id, tarefa in GERACAO_PC_STATUS.items():
            if tarefa.get("estado") != "processando_pc":
                continue
            if tarefa.get("agente_sessao") == nova_sessao:
                continue

            tarefa["estado"] = "aguardando_agente"
            tarefa["mensagem"] = "Agente reiniciado. Tarefa recolocada na fila."
            tarefa["agente_sessao"] = None
            tarefa["atualizado_em"] = time.time()
            if task_id not in GERACAO_PC_FILA:
                GERACAO_PC_FILA.append(task_id)
            reencaminhadas += 1

    return reencaminhadas


def _agente_pode_gerar_locked():
    caps = AUTOMACAO_STATUS.get("capacidades") or {}
    return _agente_online_locked() and bool(caps.get("geracao_local"))


def _liberar_coleta_abandonada_locked():
    """Libera coleta presa quando o agente sumiu por tempo suficiente."""
    if not AUTOMACAO_STATUS.get("rodando"):
        return False

    ultimo = float(AUTOMACAO_STATUS.get("agente_ultimo_ping") or 0)
    if ultimo and (time.time() - ultimo) <= COLETA_STALE_SECONDS:
        return False

    AUTOMACAO_STATUS.update({
        "rodando": False,
        "estado": "cancelado",
        "mensagem": "Coleta anterior liberada automaticamente porque o agente ficou offline.",
        "erro": None,
    })
    return True


def _token_recebido():
    bearer = (request.headers.get("Authorization") or "").strip()
    if bearer.lower().startswith("bearer "):
        return bearer[7:].strip()
    return (request.headers.get("X-Automacao-Key") or "").strip()


def _autorizar_automacao():
    if not AUTOMACAO_TOKEN:
        return False, (
            "A variável AUTOMACAO_TOKEN não está configurada no Railway."
        )
    if _token_recebido() != AUTOMACAO_TOKEN:
        return False, "Chave da automação inválida."
    return True, None


def _agente_online_locked():
    ultimo = float(AUTOMACAO_STATUS.get("agente_ultimo_ping") or 0)
    return (time.time() - ultimo) <= 20


def _snapshot_automacao():
    with AUTOMACAO_LOCK:
        _liberar_coleta_abandonada_locked()
        dados = dict(AUTOMACAO_STATUS)
        dados["agente_online"] = _agente_online_locked()
        dados["geracao_local_disponivel"] = _agente_pode_gerar_locked()

        # Os itens podem conter frames em base64. Não devolvemos tudo no /status
        # a cada 1 segundo; a interface busca incrementalmente em /itens.
        itens = AUTOMACAO_STATUS.get("itens") or []
        dados["itens_prontos"] = len(itens)
        dados.pop("itens", None)

        # Nunca devolve dados internos/sensíveis.
        dados.pop("agente_ultimo_ping", None)
        dados.pop("agente_sessao", None)
        return dados


def _atualizar_automacao(**campos):
    with AUTOMACAO_LOCK:
        AUTOMACAO_STATUS.update(campos)


# A chave fica somente no servidor/Railway e nunca é enviada ao navegador.
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "").strip()
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001").strip()


# ----------------- ECONOMIA DE RAM / LIMPEZA -----------------
# Antes era criada UMA THREAD por arquivo para esperar 1-2 horas e apagá-lo.
# Agora existe apenas uma thread de limpeza para o processo inteiro.
_CLEANUP_COND = threading.Condition()
_CLEANUP_HEAP = []
_CLEANUP_SEQ = itertools.count()


def _cleanup_loop():
    while True:
        with _CLEANUP_COND:
            while not _CLEANUP_HEAP:
                _CLEANUP_COND.wait()

            quando, _seq, tarefa = _CLEANUP_HEAP[0]
            espera = quando - time.time()
            if espera > 0:
                _CLEANUP_COND.wait(timeout=espera)
                continue

            heapq.heappop(_CLEANUP_HEAP)

        for p in tarefa.get("paths", ()):
            try:
                os.remove(p)
            except OSError:
                pass

        for job_id in tarefa.get("upload_ids", ()):
            UPLOADS.pop(job_id, None)

        for job_id in tarefa.get("result_ids", ()):
            RESULTS.pop(job_id, None)

        task_ids = tarefa.get("task_ids", ())
        if task_ids:
            with GERACAO_PC_LOCK:
                for task_id in task_ids:
                    GERACAO_PC_STATUS.pop(task_id, None)
                    try:
                        while task_id in GERACAO_PC_FILA:
                            GERACAO_PC_FILA.remove(task_id)
                    except ValueError:
                        pass


def _limpar_depois(paths=(), delay=3600, upload_ids=(), result_ids=(), task_ids=()):
    tarefa = {
        "paths": tuple(p for p in paths if p),
        "upload_ids": tuple(upload_ids),
        "result_ids": tuple(result_ids),
        "task_ids": tuple(task_ids),
    }
    item = (time.time() + max(1, int(delay)), next(_CLEANUP_SEQ), tarefa)
    with _CLEANUP_COND:
        heapq.heappush(_CLEANUP_HEAP, item)
        _CLEANUP_COND.notify()


threading.Thread(target=_cleanup_loop, daemon=True, name="cleanup-gerador").start()


def _liberar_memoria():
    """Pede ao Python/glibc para devolver páginas livres ao Linux/Railway."""
    try:
        gc.collect()
    except Exception:
        pass

    if os.name == "posix":
        try:
            libc = ctypes.CDLL(None)
            malloc_trim = getattr(libc, "malloc_trim", None)
            if malloc_trim is not None:
                malloc_trim(0)
        except Exception:
            pass


def _executar(cmd, timeout=None):
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        detalhe = (proc.stderr or proc.stdout or "").strip()[-3000:]
        raise RuntimeError(detalhe or f"Comando terminou com código {proc.returncode}.")
    return proc


def _ffprobe_video(path):
    proc = _executar([
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,duration:format=duration",
        "-of", "json",
        path,
    ], timeout=60)
    info = json.loads(proc.stdout or "{}")
    streams = info.get("streams") or []
    if not streams:
        raise RuntimeError("O FFprobe não encontrou uma faixa de vídeo.")
    stream = streams[0]
    vw = int(stream.get("width") or 0)
    vh = int(stream.get("height") or 0)
    dur_raw = (info.get("format") or {}).get("duration") or stream.get("duration") or 0
    dur = float(dur_raw or 0)
    if vw <= 0 or vh <= 0:
        raise RuntimeError("Não foi possível identificar o tamanho do vídeo.")
    return vw, vh, max(0.0, dur)


def _worker_json(worker_path, payload, timeout):
    if not os.path.isfile(worker_path):
        raise RuntimeError(f"Arquivo auxiliar ausente: {os.path.basename(worker_path)}")

    fd, payload_path = tempfile.mkstemp(prefix="worker_", suffix=".json", dir=WORK_DIR)
    os.close(fd)
    try:
        with open(payload_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

        proc = subprocess.run(
            [sys.executable, worker_path, payload_path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        saida = (proc.stdout or "").strip()
        retorno = None
        if saida:
            try:
                retorno = json.loads(saida.splitlines()[-1])
            except Exception:
                retorno = None

        if proc.returncode != 0:
            if isinstance(retorno, dict) and retorno.get("erro"):
                raise RuntimeError(str(retorno["erro"]))
            detalhe = (proc.stderr or saida or "").strip()[-3000:]
            raise RuntimeError(detalhe or f"Worker terminou com código {proc.returncode}.")

        if not isinstance(retorno, dict):
            raise RuntimeError("O worker não retornou uma resposta válida.")
        return retorno
    finally:
        try:
            os.remove(payload_path)
        except OSError:
            pass
        _liberar_memoria()


def _preview_path(job_id):
    return os.path.join(PREVIEW_DIR, f"{job_id}.jpg")


def _preview_url(job_id):
    return f"/frame/{job_id}"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/automacao-reels/iniciar", methods=["POST"])
def iniciar_automacao_reels():
    autorizado, erro = _autorizar_automacao()
    if not autorizado:
        return jsonify({"erro": erro}), 401

    dados = request.get_json(silent=True) or {}
    try:
        quantidade = int(dados.get("quantidade", 10))
    except (TypeError, ValueError):
        quantidade = 10
    quantidade = max(1, min(quantidade, 100))
    filtro_padrao = bool(dados.get("filtro_padrao", False))

    with AUTOMACAO_LOCK:
        _liberar_coleta_abandonada_locked()

        if not _agente_online_locked():
            return jsonify({
                "erro": (
                    "O agente do seu computador está offline. "
                    "Abra INICIAR_AGENTE.bat no PC e tente novamente."
                )
            }), 503

        if AUTOMACAO_STATUS.get("rodando"):
            return jsonify({"erro": "Já existe uma coleta em andamento."}), 409

        job_id = uuid.uuid4().hex
        AUTOMACAO_STATUS.update({
            "rodando": True,
            "estado": "aguardando_agente",
            "mensagem": "Tarefa enviada. Aguardando o PC começar a coleta...",
            "job_id": job_id,
            "quantidade": quantidade,
            "filtro_padrao": filtro_padrao,
            "encontrados": 0,
            "links": [],
            "itens": [],
            "falhas": [],
            "erro": None,
        })

    return jsonify({
        "ok": True,
        "job_id": job_id,
        "quantidade": quantidade,
        "filtro_padrao": filtro_padrao,
        "mensagem": "Tarefa enviada ao agente do PC.",
    })


@app.route("/automacao-reels/status")
def status_automacao_reels():
    autorizado, erro = _autorizar_automacao()
    if not autorizado:
        return jsonify({"erro": erro}), 401
    return jsonify(_snapshot_automacao())


@app.route("/automacao-reels/cancelar", methods=["POST"])
def cancelar_automacao_reels():
    autorizado, erro = _autorizar_automacao()
    if not autorizado:
        return jsonify({"erro": erro}), 401

    with AUTOMACAO_LOCK:
        estava_rodando = bool(AUTOMACAO_STATUS.get("rodando"))
        AUTOMACAO_STATUS.update({
            "rodando": False,
            "estado": "cancelado",
            "mensagem": "Coleta cancelada. Você já pode iniciar outra.",
            "erro": None,
        })

    return jsonify({"ok": True, "estava_rodando": estava_rodando})


@app.route("/automacao-reels/itens")
def itens_automacao_reels():
    autorizado, erro = _autorizar_automacao()
    if not autorizado:
        return jsonify({"erro": erro}), 401

    try:
        inicio = int(request.args.get("from", "0"))
    except (TypeError, ValueError):
        inicio = 0
    inicio = max(0, inicio)

    with AUTOMACAO_LOCK:
        itens = AUTOMACAO_STATUS.get("itens") or []
        lote = itens[inicio:inicio + 20]
        total = len(itens)
        job_id = AUTOMACAO_STATUS.get("job_id")

    return jsonify({
        "job_id": job_id,
        "from": inicio,
        "total": total,
        "itens": lote,
        "proximo": inicio + len(lote),
    })


@app.route("/agente/heartbeat", methods=["POST"])
def agente_heartbeat():
    autorizado, erro = _autorizar_automacao()
    if not autorizado:
        return jsonify({"erro": erro}), 401

    dados = request.get_json(silent=True) or {}
    nome = (dados.get("nome") or "PC").strip()[:80]
    capacidades = dados.get("capacidades") if isinstance(dados.get("capacidades"), dict) else {}
    sessao = str(dados.get("sessao") or "").strip()[:120]

    sessao_anterior = None
    with AUTOMACAO_LOCK:
        sessao_anterior = AUTOMACAO_STATUS.get("agente_sessao")
        AUTOMACAO_STATUS["agente_ultimo_ping"] = time.time()
        AUTOMACAO_STATUS["agente_nome"] = nome
        AUTOMACAO_STATUS["agente_sessao"] = sessao or sessao_anterior
        AUTOMACAO_STATUS["capacidades"] = capacidades

    reencaminhadas = 0
    if sessao and sessao_anterior and sessao != sessao_anterior:
        reencaminhadas = _reencaminhar_geracoes_de_sessao_antiga(sessao)

    return jsonify({"ok": True, "reencaminhadas": reencaminhadas})


@app.route("/agente/tarefa")
def agente_tarefa():
    autorizado, erro = _autorizar_automacao()
    if not autorizado:
        return jsonify({"erro": erro}), 401

    with AUTOMACAO_LOCK:
        AUTOMACAO_STATUS["agente_ultimo_ping"] = time.time()
        agente_sessao_atual = AUTOMACAO_STATUS.get("agente_sessao")

    # Renderizações individuais têm prioridade para o botão Gerar todos
    # continuar responsivo mesmo quando o agente também é usado para coleta.
    with GERACAO_PC_LOCK:
        while GERACAO_PC_FILA:
            task_id = GERACAO_PC_FILA.pop(0)
            tarefa = GERACAO_PC_STATUS.get(task_id)
            if not tarefa or tarefa.get("estado") != "aguardando_agente":
                continue

            tarefa["estado"] = "processando_pc"
            tarefa["mensagem"] = "PC recebeu a tarefa de renderização."
            tarefa["agente_sessao"] = agente_sessao_atual
            tarefa["atualizado_em"] = time.time()
            return jsonify({
                "tem_tarefa": True,
                "tipo": "geracao",
                "task_id": task_id,
                "dados": tarefa["dados"],
            })

    with AUTOMACAO_LOCK:
        _liberar_coleta_abandonada_locked()
        if (
            AUTOMACAO_STATUS.get("rodando")
            and AUTOMACAO_STATUS.get("estado") == "aguardando_agente"
        ):
            AUTOMACAO_STATUS["estado"] = "coletando"
            AUTOMACAO_STATUS["mensagem"] = "PC conectado. Abrindo sua aba de Reels..."
            return jsonify({
                "tem_tarefa": True,
                "tipo": "coleta",
                "job_id": AUTOMACAO_STATUS["job_id"],
                "quantidade": AUTOMACAO_STATUS["quantidade"],
                "filtro_padrao": bool(AUTOMACAO_STATUS.get("filtro_padrao", False)),
            })

    return jsonify({"tem_tarefa": False})


@app.route("/agente/progresso", methods=["POST"])
def agente_progresso():
    autorizado, erro = _autorizar_automacao()
    if not autorizado:
        return jsonify({"erro": erro}), 401

    dados = request.get_json(silent=True) or {}
    job_id = dados.get("job_id")
    estado = (dados.get("estado") or "coletando").strip()[:50]
    mensagem = (dados.get("mensagem") or "Coletando...").strip()[:500]
    try:
        encontrados = int(dados.get("encontrados", 0))
    except (TypeError, ValueError):
        encontrados = 0

    with AUTOMACAO_LOCK:
        AUTOMACAO_STATUS["agente_ultimo_ping"] = time.time()

        if job_id != AUTOMACAO_STATUS.get("job_id"):
            return jsonify({"erro": "Essa tarefa não é mais a tarefa atual."}), 409

        if not AUTOMACAO_STATUS.get("rodando"):
            return jsonify({"erro": "Não existe coleta ativa."}), 409

        AUTOMACAO_STATUS.update({
            "estado": estado,
            "mensagem": mensagem,
            "encontrados": max(0, encontrados),
            "erro": None,
        })

    return jsonify({"ok": True})


@app.route("/agente/concluir", methods=["POST"])
def agente_concluir():
    autorizado, erro = _autorizar_automacao()
    if not autorizado:
        return jsonify({"erro": erro}), 401

    dados = request.get_json(silent=True) or {}
    job_id = dados.get("job_id")
    links = dados.get("links") or []
    falhas = dados.get("falhas") or []

    if not isinstance(links, list):
        links = []
    if not isinstance(falhas, list):
        falhas = []

    links_limpos = []
    vistos = set()
    for link in links[:100]:
        if not isinstance(link, str):
            continue
        link = link.strip()
        if not link or link in vistos:
            continue
        if not link.startswith((
            "https://www.instagram.com/reel/",
            "https://instagram.com/reel/",
        )):
            continue
        vistos.add(link)
        links_limpos.append(link)

    falhas_limpas = []
    for falha in falhas[:100]:
        if not isinstance(falha, dict):
            continue
        falhas_limpas.append({
            "url": str(falha.get("url") or "")[:500],
            "erro": str(falha.get("erro") or "Falha")[:500],
        })

    with AUTOMACAO_LOCK:
        AUTOMACAO_STATUS["agente_ultimo_ping"] = time.time()

        if job_id != AUTOMACAO_STATUS.get("job_id"):
            return jsonify({"erro": "Essa tarefa não é mais a tarefa atual."}), 409

        prontos = len(AUTOMACAO_STATUS.get("itens") or [])
        total = int(AUTOMACAO_STATUS.get("quantidade") or 0)

        mensagem = f"Concluído: {prontos}/{total} Reel(s) baixados e analisados no PC."
        if falhas_limpas:
            mensagem += f" {len(falhas_limpas)} falhou(aram) no PC/registro."

        AUTOMACAO_STATUS.update({
            "rodando": False,
            "estado": "concluido",
            "mensagem": mensagem,
            "encontrados": len(links_limpos),
            "links": links_limpos,
            "falhas": falhas_limpas,
            "erro": None,
        })

    return jsonify({
        "ok": True,
        "coletados": len(links_limpos),
        "prontos": prontos,
        "falhas": len(falhas_limpas),
    })


@app.route("/agente/erro", methods=["POST"])
def agente_erro():
    autorizado, erro = _autorizar_automacao()
    if not autorizado:
        return jsonify({"erro": erro}), 401

    dados = request.get_json(silent=True) or {}
    job_id = dados.get("job_id")
    mensagem = (dados.get("erro") or "Falha desconhecida no agente.").strip()[:1000]

    with AUTOMACAO_LOCK:
        AUTOMACAO_STATUS["agente_ultimo_ping"] = time.time()

        if job_id != AUTOMACAO_STATUS.get("job_id"):
            return jsonify({"erro": "Essa tarefa não é mais a tarefa atual."}), 409

        AUTOMACAO_STATUS.update({
            "rodando": False,
            "estado": "erro",
            "mensagem": f"Falha na coleta: {mensagem}",
            "erro": mensagem,
        })

    return jsonify({"ok": True})


@app.errorhandler(413)
def arquivo_grande(_erro):
    return jsonify({
        "erro": f"Arquivo muito grande. O limite atual é {MAX_VIDEO_UPLOAD_MB} MB."
    }), 413


def _corpo_maior_que(limite_bytes):
    tamanho = request.content_length
    return tamanho is not None and tamanho > limite_bytes


def _analisar_arquivo(entrada, nome_seguro, job_id):
    """Analisa sem carregar OpenCV/Numpy/Pillow no processo web permanente."""
    frame_path = _preview_path(job_id)

    try:
        with ANALISE_LOCK:
            vw, vh, dur = _ffprobe_video(entrada)

            _executar([
                "ffmpeg", "-y",
                "-ss", f"{max(0.0, dur / 2):.2f}",
                "-i", entrada,
                "-frames:v", "1",
                "-vf", "scale=720:-2:force_original_aspect_ratio=decrease",
                "-q:v", "4",
                frame_path,
            ], timeout=180)

            analise = _worker_json(
                ANALYSIS_WORKER,
                {"frame_path": frame_path},
                timeout=120,
            )

        frame_w = int(analise.get("frame_w") or 0)
        frame_h = int(analise.get("frame_h") or 0)
        box_frame = analise.get("box")
        conf = float(analise.get("confianca") or 0.0)

        if frame_w <= 0 or frame_h <= 0:
            raise RuntimeError("O worker de análise retornou dimensões inválidas.")

        if not box_frame:
            box = (
                int(vw * 0.08), int(vh * 0.30),
                int(vw * 0.84), int(vw * 0.84),
            )
            conf = 0.0
        else:
            escala_x = vw / frame_w
            escala_y = vh / frame_h
            x, y, bw, bh = [int(v) for v in box_frame]
            box = (
                int(round(x * escala_x)), int(round(y * escala_y)),
                int(round(bw * escala_x)), int(round(bh * escala_y)),
            )

        UPLOADS[job_id] = {"path": entrada, "nome": nome_seguro}
        # Mantém o mesmo prazo de 1h para o vídeo fonte, mas também remove o
        # metadata da RAM quando ele expira.
        _limpar_depois([entrada], delay=3600, upload_ids=[job_id])
        # Preview é pequeno e fica no disco, não na RAM. 24h evita quebrar a
        # miniatura se a página ficar aberta por bastante tempo.
        _limpar_depois([frame_path], delay=86400)

        return {
            "id": job_id,
            "largura": vw,
            "altura": vh,
            "box": {"x": box[0], "y": box[1], "w": box[2], "h": box[3]},
            "confianca": round(max(0.0, min(conf, 1.0)), 2),
            "frame": _preview_url(job_id),
            "nome": nome_seguro,
        }
    except Exception:
        try:
            os.remove(frame_path)
        except OSError:
            pass
        raise
    finally:
        _liberar_memoria()


@app.route("/frame/<job_id>")
def frame_preview(job_id):
    # job_id nasce de uuid.hex, então aceitamos apenas hex para impedir path traversal.
    if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id.lower()):
        return "Preview inválido.", 404
    caminho = _preview_path(job_id)
    if not os.path.isfile(caminho):
        return "Preview expirado.", 404
    return send_file(
        caminho,
        mimetype="image/jpeg",
        conditional=True,
        max_age=3600,
    )


@app.route("/agente/registrar-video-local", methods=["POST"])
def agente_registrar_video_local():
    """
    Registra somente preview + medidas. O MP4 original continua no PC.
    O preview é convertido de Base64 para JPEG no disco imediatamente, evitando
    que dezenas/centenas de strings Base64 fiquem residentes na RAM do Railway.
    """
    autorizado, erro = _autorizar_automacao()
    if not autorizado:
        return jsonify({"erro": erro}), 401

    dados = request.get_json(silent=True) or {}
    task_id = str(dados.get("job_id") or "").strip()
    reel_url = str(dados.get("reel_url") or "").strip()[:1000]
    nome_seguro = secure_filename(str(dados.get("nome") or "reel.mp4")) or "reel.mp4"

    try:
        largura = max(1, int(dados.get("largura") or 0))
        altura = max(1, int(dados.get("altura") or 0))
        confianca = float(dados.get("confianca") or 0)
    except (TypeError, ValueError):
        return jsonify({"erro": "Metadados de vídeo inválidos."}), 400

    box = dados.get("box") if isinstance(dados.get("box"), dict) else None
    if not box or not all(k in box for k in ("x", "y", "w", "h")):
        return jsonify({"erro": "Recorte do vídeo ausente."}), 400

    try:
        box = {
            "x": int(box["x"]), "y": int(box["y"]),
            "w": int(box["w"]), "h": int(box["h"]),
        }
    except (TypeError, ValueError):
        return jsonify({"erro": "Recorte do vídeo inválido."}), 400

    frame_data = str(dados.get("frame") or "")
    prefixo = "data:image/jpeg;base64,"
    if not frame_data.startswith(prefixo):
        return jsonify({"erro": "Preview JPEG ausente."}), 400
    if len(frame_data) > 2_000_000:
        return jsonify({"erro": "Preview grande demais."}), 413

    # Primeiro confere tarefa/retry. Assim retry não decodifica nem grava de novo.
    with AUTOMACAO_LOCK:
        AUTOMACAO_STATUS["agente_ultimo_ping"] = time.time()
        if task_id != AUTOMACAO_STATUS.get("job_id"):
            return jsonify({"erro": "Essa tarefa não é mais a tarefa atual."}), 409
        if not AUTOMACAO_STATUS.get("rodando"):
            return jsonify({"erro": "Não existe coleta ativa."}), 409
        for existente in AUTOMACAO_STATUS.get("itens") or []:
            if reel_url and existente.get("reel_url") == reel_url:
                return jsonify(existente)

    video_job_id = uuid.uuid4().hex
    frame_path = _preview_path(video_job_id)
    try:
        frame_bytes = base64.b64decode(frame_data[len(prefixo):], validate=True)
    except (binascii.Error, ValueError):
        return jsonify({"erro": "Preview JPEG inválido."}), 400

    if not frame_bytes.startswith(b"\xff\xd8\xff"):
        return jsonify({"erro": "O preview recebido não é JPEG válido."}), 400

    try:
        with open(frame_path, "wb") as f:
            f.write(frame_bytes)
    except OSError as exc:
        return jsonify({"erro": f"Não foi possível salvar o preview: {exc}"}), 500

    # Solta referências grandes o quanto antes.
    frame_bytes = None
    frame_data = None
    dados.pop("frame", None)

    with AUTOMACAO_LOCK:
        # A tarefa pode ter mudado enquanto o JPEG era salvo.
        if task_id != AUTOMACAO_STATUS.get("job_id") or not AUTOMACAO_STATUS.get("rodando"):
            try:
                os.remove(frame_path)
            except OSError:
                pass
            return jsonify({"erro": "A tarefa mudou durante o registro do preview."}), 409

        # Segunda checagem idempotente para corrida rara entre dois retries.
        for existente in AUTOMACAO_STATUS.get("itens") or []:
            if reel_url and existente.get("reel_url") == reel_url:
                try:
                    os.remove(frame_path)
                except OSError:
                    pass
                return jsonify(existente)

        UPLOADS[video_job_id] = {
            "path": None,
            "nome": nome_seguro,
            "local_only": True,
            "reel_url": reel_url,
        }

        item = {
            "id": video_job_id,
            "largura": largura,
            "altura": altura,
            "box": box,
            "confianca": round(max(0.0, min(confianca, 1.0)), 2),
            "frame": _preview_url(video_job_id),
            "nome": nome_seguro,
            "reel_url": reel_url,
            "origem": "agente_pc_local",
            "local_only": True,
        }

        AUTOMACAO_STATUS.setdefault("itens", []).append(item)
        prontos = len(AUTOMACAO_STATUS["itens"])
        total = int(AUTOMACAO_STATUS.get("quantidade") or 0)
        AUTOMACAO_STATUS["mensagem"] = (
            f"PC baixando/analisando • {prontos}/{total} pronto(s) • "
            "originais ficam no PC"
        )

    # Metadados do Reel local e preview podem ficar 24h; não existe MP4 no Railway.
    _limpar_depois([frame_path], delay=86400, upload_ids=[video_job_id])
    _liberar_memoria()
    return jsonify(item)


@app.route("/agente/upload-video", methods=["POST"])

def agente_upload_video():
    autorizado, erro = _autorizar_automacao()
    if not autorizado:
        return jsonify({"erro": erro}), 401

    if _corpo_maior_que(MAX_VIDEO_UPLOAD_BYTES):
        return jsonify({
            "erro": f"Vídeo muito grande. Limite: {MAX_VIDEO_UPLOAD_MB} MB."
        }), 413

    task_id = (request.form.get("job_id") or "").strip()
    reel_url = (request.form.get("reel_url") or "").strip()

    if "video" not in request.files:
        return jsonify({"erro": "Nenhum vídeo enviado pelo agente."}), 400

    video = request.files["video"]
    if not video.filename:
        return jsonify({"erro": "Arquivo de vídeo sem nome."}), 400

    # Confere se o upload pertence à tarefa atual.
    with AUTOMACAO_LOCK:
        AUTOMACAO_STATUS["agente_ultimo_ping"] = time.time()

        if task_id != AUTOMACAO_STATUS.get("job_id"):
            return jsonify({"erro": "Essa tarefa não é mais a tarefa atual."}), 409

        if not AUTOMACAO_STATUS.get("rodando"):
            return jsonify({"erro": "Não existe coleta ativa."}), 409

        # Retry seguro: se o agente reenviar o mesmo Reel porque a resposta caiu,
        # devolvemos o item já analisado em vez de duplicar.
        for existente in AUTOMACAO_STATUS.get("itens") or []:
            if reel_url and existente.get("reel_url") == reel_url:
                return jsonify(existente)

    video_job_id = uuid.uuid4().hex
    nome_seguro = secure_filename(video.filename) or f"reel_{video_job_id}.mp4"
    entrada = os.path.join(
        WORK_DIR,
        f"{video_job_id}_agente_{nome_seguro}"
    )

    video.save(entrada)

    try:
        analisado = _analisar_arquivo(
            entrada,
            nome_seguro,
            video_job_id,
        )
    except Exception as e:
        try:
            os.remove(entrada)
        except OSError:
            pass
        return jsonify({"erro": f"Falha ao analisar vídeo enviado pelo PC: {e}"}), 500

    item = dict(analisado)
    item["reel_url"] = reel_url
    item["origem"] = "agente_pc"

    with AUTOMACAO_LOCK:
        # A tarefa pode ter sido trocada enquanto o FFmpeg analisava.
        if task_id != AUTOMACAO_STATUS.get("job_id"):
            return jsonify({"erro": "A tarefa mudou durante a análise."}), 409

        AUTOMACAO_STATUS.setdefault("itens", []).append(item)
        prontos = len(AUTOMACAO_STATUS["itens"])
        total = int(AUTOMACAO_STATUS.get("quantidade") or 0)
        AUTOMACAO_STATUS["mensagem"] = (
            f"PC baixando/enviando • {prontos}/{total} analisado(s) no Railway"
        )

    return jsonify(item)


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
        retorno = _worker_json(
            INSTAGRAM_WORKER,
            {
                "url": url,
                "pasta_destino": WORK_DIR,
                "identificador": job_id,
                "limite_mb": MAX_VIDEO_UPLOAD_MB,
            },
            timeout=300,
        )
        if not retorno.get("ok"):
            return jsonify({"erro": str(retorno.get("erro") or "Falha ao importar o Reels.")}), 400

        entrada = str(retorno.get("path") or "")
        nome = str(retorno.get("nome") or "reel.mp4")
        if not entrada or not os.path.isfile(entrada):
            raise RuntimeError("O importador não retornou o vídeo baixado.")

        return jsonify(_analisar_arquivo(entrada, nome, job_id))
    except Exception as e:
        if entrada:
            try:
                os.remove(entrada)
            except OSError:
                pass
        return jsonify({"erro": f"Falha ao importar o Reels: {e}"}), 500
    finally:
        _liberar_memoria()


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


# ----------------- GERAÇÃO HÍBRIDA: PC QUANDO DISPONÍVEL -----------------

def _runtime_manifest_local():
    base = os.path.dirname(os.path.abspath(__file__))
    candidatos = [
        "meme_maker.py",
        "avatar.png", "avatar2.png", "avatar3.png", "avatar4.png",
        "logo_adultosofrido.png", "logo_adultasofrida.png",
    ]

    fonte_dir = os.path.join(base, "fontes")
    if os.path.isdir(fonte_dir):
        for raiz, _dirs, arquivos in os.walk(fonte_dir):
            for nome in arquivos:
                if nome.lower().endswith((".ttf", ".otf")):
                    rel = os.path.relpath(os.path.join(raiz, nome), base)
                    candidatos.append(rel)

    itens = []
    vistos = set()
    for rel in candidatos:
        rel = rel.replace("\\", "/").lstrip("/")
        if rel in vistos:
            continue
        vistos.add(rel)
        caminho = os.path.abspath(os.path.join(base, rel))
        if not caminho.startswith(os.path.abspath(base) + os.sep):
            continue
        if not os.path.isfile(caminho):
            continue
        h = hashlib.sha256()
        with open(caminho, "rb") as f:
            for bloco in iter(lambda: f.read(1024 * 1024), b""):
                h.update(bloco)
        itens.append({
            "path": rel,
            "size": os.path.getsize(caminho),
            "sha256": h.hexdigest(),
        })
    return itens


@app.route("/agente/runtime/manifest")
def agente_runtime_manifest():
    autorizado, erro = _autorizar_automacao()
    if not autorizado:
        return jsonify({"erro": erro}), 401
    return jsonify({"arquivos": _runtime_manifest_local()})


@app.route("/agente/runtime/<path:rel>")
def agente_runtime_arquivo(rel):
    autorizado, erro = _autorizar_automacao()
    if not autorizado:
        return jsonify({"erro": erro}), 401

    permitido = {item["path"] for item in _runtime_manifest_local()}
    rel = rel.replace("\\", "/").lstrip("/")
    if rel not in permitido:
        return jsonify({"erro": "Arquivo de runtime não permitido."}), 404

    base = os.path.dirname(os.path.abspath(__file__))
    caminho = os.path.join(base, rel)
    return send_file(caminho, as_attachment=True, download_name=os.path.basename(rel))


@app.route("/agente/fonte/<job_id>")
def agente_fonte(job_id):
    autorizado, erro = _autorizar_automacao()
    if not autorizado:
        return jsonify({"erro": erro}), 401

    item = UPLOADS.get(job_id)
    if not item:
        return jsonify({"erro": "Vídeo fonte expirado ou inexistente."}), 404
    if item.get("local_only"):
        return jsonify({
            "erro": "Este vídeo fonte está somente no cache do PC.",
            "codigo": "FONTE_LOCAL"
        }), 409
    if not item.get("path") or not os.path.exists(item["path"]):
        return jsonify({"erro": "Vídeo fonte expirado ou inexistente."}), 404

    return send_file(
        item["path"],
        as_attachment=True,
        download_name=item.get("nome") or f"{job_id}.mp4",
    )


@app.route("/gerar-hibrido", methods=["POST"])
def gerar_hibrido():
    autorizado, erro = _autorizar_automacao()
    if not autorizado:
        return jsonify({"erro": erro}), 401

    dados = request.get_json(silent=True) or {}
    job_id = dados.get("id")
    legenda = (dados.get("legenda") or "").strip()

    item = UPLOADS.get(job_id)
    if not item:
        return jsonify({"erro": "Vídeo expirado. Adicione novamente."}), 404

    local_only = bool(item.get("local_only"))
    if not local_only and (not item.get("path") or not os.path.exists(item["path"])):
        return jsonify({"erro": "Vídeo expirado. Adicione novamente."}), 404
    if not legenda:
        return jsonify({"erro": "Digite uma legenda."}), 400

    with AUTOMACAO_LOCK:
        usar_pc = _agente_pode_gerar_locked()

    if not usar_pc:
        if local_only:
            return jsonify({
                "modo": "aguardar_pc",
                "aguardar_pc": True,
                "obrigatorio_pc": True,
                "mensagem": "Este Reel está no PC. Aguardando o agente reconectar.",
            }), 503
        return jsonify({
            "modo": "railway",
            "mensagem": "PC indisponível para renderização; use o Railway.",
        })

    task_id = uuid.uuid4().hex
    agora = time.time()
    with GERACAO_PC_LOCK:
        GERACAO_PC_STATUS[task_id] = {
            "task_id": task_id,
            "job_id": job_id,
            "estado": "aguardando_agente",
            "mensagem": "Aguardando o PC iniciar a renderização...",
            "erro": None,
            "criado_em": agora,
            "atualizado_em": agora,
            "dados": {
                "id": job_id,
                "legenda": legenda,
                "crop": dados.get("crop"),
                "perfil": dados.get("perfil"),
                "uniqueness": dados.get("uniqueness") or {},
                "nome": item.get("nome") or "video.mp4",
                "local_only": local_only,
            },
            "agente_sessao": None,
        }
        GERACAO_PC_FILA.append(task_id)

    # Evita crescimento infinito de tarefas abandonadas em RAM.
    _limpar_depois(delay=86400, task_ids=[task_id])

    return jsonify({
        "modo": "pc",
        "task_id": task_id,
        "mensagem": "Renderização enviada ao PC.",
    })


@app.route("/geracao-hibrida/status/<task_id>")
def geracao_hibrida_status(task_id):
    autorizado, erro = _autorizar_automacao()
    if not autorizado:
        return jsonify({"erro": erro}), 401

    with AUTOMACAO_LOCK:
        agente_online = _agente_online_locked()

    with GERACAO_PC_LOCK:
        tarefa = GERACAO_PC_STATUS.get(task_id)
        if not tarefa:
            return jsonify({"erro": "Tarefa de geração não encontrada."}), 404

        mensagem = tarefa.get("mensagem")
        if tarefa.get("estado") == "processando_pc" and not agente_online:
            mensagem = "⏸ Agente desconectado. O lote aguarda o PC reconectar."

        return jsonify({
            "task_id": task_id,
            "job_id": tarefa.get("job_id"),
            "estado": tarefa.get("estado"),
            "mensagem": mensagem,
            "erro": tarefa.get("erro"),
            "agente_online": agente_online,
        })


@app.route("/agente/geracao-progresso", methods=["POST"])
def agente_geracao_progresso():
    autorizado, erro = _autorizar_automacao()
    if not autorizado:
        return jsonify({"erro": erro}), 401

    dados = request.get_json(silent=True) or {}
    task_id = dados.get("task_id")
    mensagem = str(dados.get("mensagem") or "Processando no PC...")[:500]

    with AUTOMACAO_LOCK:
        AUTOMACAO_STATUS["agente_ultimo_ping"] = time.time()

    with GERACAO_PC_LOCK:
        tarefa = GERACAO_PC_STATUS.get(task_id)
        if not tarefa:
            return jsonify({"erro": "Tarefa inexistente."}), 404
        tarefa["mensagem"] = mensagem
        tarefa["estado"] = "processando_pc"
        tarefa["atualizado_em"] = time.time()

    return jsonify({"ok": True})


@app.route("/agente/resultado-geracao", methods=["POST"])
def agente_resultado_geracao():
    autorizado, erro = _autorizar_automacao()
    if not autorizado:
        return jsonify({"erro": erro}), 401

    task_id = (request.form.get("task_id") or "").strip()
    if "video" not in request.files:
        return jsonify({"erro": "Vídeo final não enviado."}), 400

    with GERACAO_PC_LOCK:
        tarefa = GERACAO_PC_STATUS.get(task_id)
        if not tarefa:
            return jsonify({"erro": "Tarefa inexistente."}), 404
        job_id = tarefa.get("job_id")

    item = UPLOADS.get(job_id)
    if not item:
        return jsonify({"erro": "Item original não encontrado."}), 404

    saida = os.path.join(WORK_DIR, f"{job_id}_post_pc.mp4")
    request.files["video"].save(saida)

    base_nome = os.path.splitext(item.get("nome") or "video")[0]
    nome_saida = f"post_{base_nome}.mp4"
    RESULTS[job_id] = {"path": saida, "nome": nome_saida}
    _limpar_depois([saida], delay=7200, result_ids=[job_id])

    with AUTOMACAO_LOCK:
        AUTOMACAO_STATUS["agente_ultimo_ping"] = time.time()

    with GERACAO_PC_LOCK:
        tarefa = GERACAO_PC_STATUS.get(task_id)
        if tarefa:
            tarefa["estado"] = "concluido"
            tarefa["mensagem"] = "Renderização concluída no PC."
            tarefa["erro"] = None
            tarefa["atualizado_em"] = time.time()

    _limpar_depois(delay=7200, task_ids=[task_id])
    _liberar_memoria()
    return jsonify({"ok": True, "id": job_id})


@app.route("/agente/geracao-erro", methods=["POST"])
def agente_geracao_erro():
    autorizado, erro = _autorizar_automacao()
    if not autorizado:
        return jsonify({"erro": erro}), 401

    dados = request.get_json(silent=True) or {}
    task_id = dados.get("task_id")
    mensagem = str(dados.get("erro") or "Falha na renderização local.")[:1000]

    with GERACAO_PC_LOCK:
        tarefa = GERACAO_PC_STATUS.get(task_id)
        if not tarefa:
            return jsonify({"erro": "Tarefa inexistente."}), 404
        tarefa["estado"] = "erro"
        tarefa["erro"] = mensagem
        tarefa["mensagem"] = mensagem
        tarefa["atualizado_em"] = time.time()

    _limpar_depois(delay=7200, task_ids=[task_id])
    return jsonify({"ok": True})


@app.route("/gerar", methods=["POST"])
def gerar():
    dados = request.json if request.is_json else {}
    job_id = dados.get("id")
    legenda = (dados.get("legenda") or "").strip()
    crop = dados.get("crop")
    perfil = dados.get("perfil")

    # === OPÇÕES DE ANTI-DETECÇÃO / UNIQUENESS ===
    uniqueness = dados.get("uniqueness", {})
    # Padrão: edições extras e logo DESLIGADAS.
    # A interface pode enviar edicoes_extras=true e/ou usar_logo=true.
    if not uniqueness:
        uniqueness = {
            "edicoes_extras": False,
            "usar_logo": False,
            "light_crop": True,
            "color_adjust": True,
            "subtle_grain": True,
            "stronger_visuals": True,
            "random_flip": True,
            "vignette": True,
            "dynamic_zoom": True,
            "speed_factor": 1.0,
            "crf": 18,
            "preset": "slow",
            "deep_metadata_clean": True,
            "remove_h264_sei": True
        }

    item = UPLOADS.get(job_id)
    if not item:
        return jsonify({"erro": "Vídeo expirado. Adicione novamente."}), 404
    if item.get("local_only"):
        return jsonify({
            "erro": "Este Reel fica somente no PC para economizar Railway. Ligue o agente para gerar.",
            "codigo": "FONTE_LOCAL"
        }), 409
    if not item.get("path") or not os.path.exists(item["path"]):
        return jsonify({"erro": "Vídeo expirado. Adicione novamente."}), 404
    if not legenda:
        return jsonify({"erro": "Digite uma legenda."}), 400

    entrada = item["path"]
    saida = os.path.join(WORK_DIR, f"{job_id}_post.mp4")

    try:
        # Renderiza em um Python filho. Pillow/Pilmoji são descarregados quando
        # o worker termina, em vez de ficarem ocupando RAM do Gunicorn por horas.
        with GERACAO_LOCK:
            retorno = _worker_json(
                RENDER_WORKER,
                {
                    "entrada": entrada,
                    "saida": saida,
                    "legenda": legenda,
                    "crop": crop,
                    "perfil": perfil,
                    "uniqueness": uniqueness,
                },
                timeout=1800,
            )
            if not retorno.get("ok"):
                raise RuntimeError(str(retorno.get("erro") or "Falha desconhecida no render."))
    except Exception as e:
        try:
            os.remove(saida)
        except OSError:
            pass
        return jsonify({"erro": f"Falha ao gerar: {e}"}), 500

    if not os.path.isfile(saida):
        return jsonify({"erro": "Falha ao gerar: o arquivo final não foi criado."}), 500

    base = os.path.splitext(item["nome"])[0]
    nome_saida = f"post_{base}.mp4"
    RESULTS[job_id] = {"path": saida, "nome": nome_saida}
    _limpar_depois([saida], delay=3600, result_ids=[job_id])
    _liberar_memoria()
    return jsonify({"id": job_id})


@app.route("/zip", methods=["POST"])
def baixar_zip():
    ids = request.json.get("ids", []) if request.is_json else []
    if not ids:
        return "Nenhum item para baixar.", 400

    # Antes o ZIP inteiro era montado em io.BytesIO, duplicando dezenas/centenas
    # de MB na RAM. Agora ele é criado no disco temporário e enviado por streaming.
    zip_path = os.path.join(WORK_DIR, f"posts_{uuid.uuid4().hex}.zip")
    usados = set()
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
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
            try:
                os.remove(zip_path)
            except OSError:
                pass
            return "Arquivos expirados. Gere novamente.", 404

        # Dez minutos são suficientes para o send_file terminar mesmo em conexão lenta.
        _limpar_depois([zip_path], delay=600)
        _liberar_memoria()
        return send_file(
            zip_path,
            mimetype="application/zip",
            as_attachment=True,
            download_name="posts.zip",
            conditional=True,
        )
    except Exception:
        try:
            os.remove(zip_path)
        except OSError:
            pass
        raise


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