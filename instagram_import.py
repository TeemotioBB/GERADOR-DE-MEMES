#!/usr/bin/env python3
"""Importação de Reels públicos do Instagram usando yt-dlp.

O Instagram pode devolver uma resposta vazia para IPs de datacenter, mesmo
quando o Reels é público. Este módulo tenta primeiro sem autenticação e, se
necessário, repete usando cookies armazenados em variável do Railway.

Variáveis aceitas:
- INSTAGRAM_COOKIES_B64: conteúdo de cookies.txt codificado em Base64 (recomendado)
- INSTAGRAM_COOKIES: conteúdo bruto do cookies.txt no formato Netscape
- INSTAGRAM_FORCE_IPV4: "1" para forçar IPv4
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from yt_dlp.version import __version__ as YT_DLP_VERSION


class InstagramImportError(RuntimeError):
    """Erro esperado e seguro para mostrar ao usuário."""


_DOMINIOS = {
    "instagram.com",
    "www.instagram.com",
    "m.instagram.com",
}
_PADRAO_REEL = re.compile(r"^/(?:reel|reels|p)/[A-Za-z0-9_-]+/?$")


def normalizar_url_instagram(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise InstagramImportError("Cole o link do Reels.")

    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise InstagramImportError("Link do Instagram inválido.") from exc

    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in _DOMINIOS:
        raise InstagramImportError("Use um link válido do Instagram.")

    caminho = parsed.path.rstrip("/")
    if not _PADRAO_REEL.match(caminho):
        raise InstagramImportError(
            "Esse link não parece ser de um Reels ou publicação do Instagram."
        )

    # Remove ?igsh=, utm e demais parâmetros de rastreamento.
    return f"https://www.instagram.com{caminho}/"


def _criar_arquivo_cookies_temporario() -> str | None:
    """Cria cookies.txt temporário a partir das variáveis do Railway."""
    conteudo: str | None = None

    cookies_b64 = os.environ.get("INSTAGRAM_COOKIES_B64", "").strip()
    cookies_raw = os.environ.get("INSTAGRAM_COOKIES", "").strip()

    if cookies_b64:
        try:
            conteudo = base64.b64decode(cookies_b64, validate=True).decode("utf-8")
        except Exception as exc:
            raise InstagramImportError(
                "A variável INSTAGRAM_COOKIES_B64 está inválida. Gere novamente o Base64 do cookies.txt."
            ) from exc
    elif cookies_raw:
        # Railway pode salvar quebras de linha como os caracteres literais \n.
        conteudo = cookies_raw.replace("\\n", "\n")

    if not conteudo:
        return None

    if "# Netscape HTTP Cookie File" not in conteudo and "\t.instagram.com\t" not in conteudo:
        raise InstagramImportError(
            "Os cookies do Instagram não parecem estar no formato Netscape cookies.txt."
        )

    arquivo = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix="_instagram_cookies.txt",
        delete=False,
    )
    try:
        arquivo.write(conteudo)
        if not conteudo.endswith("\n"):
            arquivo.write("\n")
        return arquivo.name
    finally:
        arquivo.close()


def _erro_pede_autenticacao(mensagem: str) -> bool:
    texto = mensagem.lower()
    sinais = (
        "empty media response",
        "login",
        "log in",
        "cookies",
        "authentication",
        "main webpage is locked behind the login page",
        "requested content is not available",
    )
    return any(sinal in texto for sinal in sinais)


def _opcoes_yt_dlp(
    template: str,
    limite_bytes: int,
    limite_mb: int,
    cookiefile: str | None,
) -> dict:
    def rejeitar_grande(info, *, incomplete=False):
        del incomplete
        tamanho = info.get("filesize") or info.get("filesize_approx")
        if tamanho and tamanho > limite_bytes:
            return f"O vídeo ultrapassa o limite de {limite_mb} MB."
        return None

    opcoes = {
        "outtmpl": template,
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": False,
        "socket_timeout": 35,
        "retries": 3,
        "fragment_retries": 3,
        "extractor_retries": 2,
        "match_filter": rejeitar_grande,
        "restrictfilenames": True,
        "cachedir": False,
        "http_headers": {
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        },
    }

    if cookiefile:
        opcoes["cookiefile"] = cookiefile

    if os.environ.get("INSTAGRAM_FORCE_IPV4", "").strip() == "1":
        opcoes["source_address"] = "0.0.0.0"

    return opcoes


def _executar_download(
    url: str,
    template: str,
    limite_bytes: int,
    limite_mb: int,
    cookiefile: str | None,
) -> dict:
    opcoes = _opcoes_yt_dlp(
        template=template,
        limite_bytes=limite_bytes,
        limite_mb=limite_mb,
        cookiefile=cookiefile,
    )
    with yt_dlp.YoutubeDL(opcoes) as ydl:
        return ydl.extract_info(url, download=True)


def _diagnosticar(erro: Exception | str) -> str:
    """Classifica causas sem publicar links de mídia assinados ou sessões."""
    mensagem = str(erro).lower()
    if any(x in mensagem for x in ("429", "too many requests", "rate limit", "rate-limit")):
        return "LIMITACAO_429"
    if any(x in mensagem for x in ("403", "forbidden", "access denied")):
        return "ACESSO_403"
    if any(x in mensagem for x in ("404", "not found")):
        return "NAO_ENCONTRADO_404"
    if any(x in mensagem for x in ("401", "login required", "you need to log in", "cookies are invalid", "authentication", "challenge_required")):
        return "AUTENTICACAO"
    if any(x in mensagem for x in ("empty media response", "empty response")):
        return "RESPOSTA_VAZIA"
    if any(x in mensagem for x in ("unsupported url", "unsupported site")):
        return "LINK_NAO_SUPORTADO"
    if any(x in mensagem for x in ("no video formats", "requested format is not available")):
        return "SEM_FORMATO"
    if any(x in mensagem for x in ("ffmpeg", "ffprobe")):
        return "FFMPEG"
    if any(x in mensagem for x in ("timeout", "timed out", "connection", "network", "ssl")):
        return "REDE"
    return "FALHA_EXTRACAO"


def _log_diagnostico(etapa: str, erro: Exception | str = "") -> None:
    """Logs deliberadamente não incluem cookies, URLs, cabeçalhos ou IDs."""
    categoria = "SUCESSO" if etapa.endswith("_sucesso") else _diagnosticar(erro)
    print(
        f"[INSTAGRAM_DIAG] etapa={etapa} categoria={categoria} "
        f"yt_dlp={YT_DLP_VERSION}",
        file=sys.stderr,
        flush=True,
    )


def _executar_gallery_dl(
    url: str,
    pasta: Path,
    identificador: str,
    limite_bytes: int,
    limite_mb: int,
    cookiefile: str | None,
) -> dict:
    """Fallback com extrator independente, mantendo o arquivo MP4 no fluxo existente."""
    pasta_temporaria = pasta / f"{identificador}_galeria"
    pasta_temporaria.mkdir(parents=True, exist_ok=True)
    try:
        cmd = [
            sys.executable, "-m", "gallery_dl", "--config-ignore", "--no-input",
            "--directory", str(pasta_temporaria),
        ]
        if cookiefile:
            cmd.extend(["--cookies", cookiefile])
        cmd.append(url)
        try:
            processo = subprocess.run(cmd, capture_output=True, text=True, timeout=105)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("timeout ao tentar o segundo extrator") from exc
        videos = [
            f for f in pasta_temporaria.rglob("*.mp4")
            if f.is_file() and f.stat().st_size > 0
        ]
        if not videos:
            # O erro bruto pode conter links de mídia temporários e não será
            # retornado nem registrado. Só a classificação fica disponível.
            raise RuntimeError((processo.stderr or processo.stdout or "Nenhum MP4")[-2000:])
        video = max(videos, key=lambda f: f.stat().st_size)
        if video.stat().st_size > limite_bytes:
            raise InstagramImportError(f"O vídeo ultrapassa o limite de {limite_mb} MB.")
        destino = pasta / f"{identificador}_instagram.mp4"
        os.replace(video, destino)
        return {"title": "reel_instagram", "id": identificador}
    finally:
        shutil.rmtree(pasta_temporaria, ignore_errors=True)


def baixar_video_instagram(
    url: str,
    pasta_destino: str,
    identificador: str,
    limite_mb: int = 200,
) -> tuple[str, str]:
    """Baixa um Reels e retorna (caminho_do_arquivo, nome_para_download)."""
    url_limpa = normalizar_url_instagram(url)
    pasta = Path(pasta_destino)
    pasta.mkdir(parents=True, exist_ok=True)

    prefixo = pasta / f"{identificador}_instagram"
    template = str(prefixo) + ".%(ext)s"
    limite_bytes = int(limite_mb) * 1024 * 1024
    cookiefile: str | None = None
    info: dict | None = None

    try:
        erros = []
        # Tentativa 1: acesso público. Não consumimos a sessão se não for preciso.
        try:
            info = _executar_download(
                url_limpa, template, limite_bytes, limite_mb, cookiefile=None
            )
        except yt_dlp.utils.DownloadError as erro_anonimo:
            if "limite" in str(erro_anonimo).lower() or "too large" in str(erro_anonimo).lower():
                raise InstagramImportError(f"O vídeo ultrapassa o limite de {limite_mb} MB.") from erro_anonimo
            erros.append(f"yt_dlp_publico={_diagnosticar(erro_anonimo)}")
            _log_diagnostico("yt_dlp_publico", erro_anonimo)

            # Tentativa 2: mesma ferramenta com cookies, se disponíveis.
            cookiefile = _criar_arquivo_cookies_temporario()
            if cookiefile:
                try:
                    info = _executar_download(
                        url_limpa, template, limite_bytes, limite_mb, cookiefile=cookiefile
                    )
                except yt_dlp.utils.DownloadError as erro_autenticado:
                    if "limite" in str(erro_autenticado).lower() or "too large" in str(erro_autenticado).lower():
                        raise InstagramImportError(f"O vídeo ultrapassa o limite de {limite_mb} MB.") from erro_autenticado
                    erros.append(f"yt_dlp_cookies={_diagnosticar(erro_autenticado)}")
                    _log_diagnostico("yt_dlp_cookies", erro_autenticado)
            else:
                erros.append("cookies=NAO_CONFIGURADOS")

            if info is None:
                # Tentativa 3: mecanismo independente do yt-dlp. Não depende
                # de cookies válidos se o Instagram permitir acesso público.
                try:
                    info = _executar_gallery_dl(
                        url_limpa, pasta, identificador, limite_bytes, limite_mb, cookiefile
                    )
                    _log_diagnostico("gallery_dl_sucesso")
                except InstagramImportError:
                    raise
                except Exception as erro_alternativo:
                    erros.append(f"gallery_dl={_diagnosticar(erro_alternativo)}")
                    _log_diagnostico("gallery_dl", erro_alternativo)
                    detalhes = ", ".join(erros)
                    raise InstagramImportError(
                        "Não foi possível importar este Reels usando nenhum dos dois extratores. "
                        f"Diagnóstico: {detalhes}. "
                        "Se o erro persistir com vídeos públicos, pode haver restrição do Instagram "
                        "ao servidor do Railway."
                    ) from erro_alternativo

        titulo = ((info or {}).get("title") or (info or {}).get("id") or "reel_instagram").strip()

    finally:
        if cookiefile:
            try:
                os.remove(cookiefile)
            except OSError:
                pass

    candidatos = sorted(pasta.glob(f"{identificador}_instagram.*"))
    arquivo = next((p for p in candidatos if p.suffix.lower() == ".mp4"), None)
    if arquivo is None:
        arquivo = next(
            (
                p
                for p in candidatos
                if p.is_file() and p.suffix.lower() not in {".part", ".ytdl"}
            ),
            None,
        )

    if arquivo is None or not arquivo.exists():
        raise InstagramImportError(
            "O download terminou, mas o arquivo de vídeo não foi encontrado."
        )

    if arquivo.stat().st_size > limite_bytes:
        try:
            arquivo.unlink()
        except OSError:
            pass
        raise InstagramImportError(
            f"O vídeo ultrapassa o limite de {limite_mb} MB."
        )

    nome = re.sub(r"[^A-Za-z0-9._-]+", "_", titulo).strip("._")[:80]
    nome = nome or "reel_instagram"
    return str(arquivo), f"{nome}.mp4"
