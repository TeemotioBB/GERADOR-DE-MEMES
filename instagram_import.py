#!/usr/bin/env python3
"""Importação segura de Reels públicos do Instagram usando yt-dlp."""

import os
import re
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp


class InstagramImportError(RuntimeError):
    pass


_DOMINIOS = {"instagram.com", "www.instagram.com", "m.instagram.com"}
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

    caminho = parsed.path.rstrip("/") + "/"
    if not _PADRAO_REEL.match(caminho.rstrip("/")):
        raise InstagramImportError("Esse link não parece ser de um Reels ou publicação do Instagram.")

    # Remove parâmetros de rastreamento como ?igsh=...
    return f"https://www.instagram.com{caminho}"


def baixar_video_instagram(url: str, pasta_destino: str, identificador: str,
                            limite_mb: int = 200) -> tuple[str, str]:
    """Baixa uma mídia pública e retorna (caminho_mp4, nome_amigavel)."""
    url_limpa = normalizar_url_instagram(url)
    pasta = Path(pasta_destino)
    pasta.mkdir(parents=True, exist_ok=True)

    prefixo = pasta / f"{identificador}_instagram"
    template = str(prefixo) + ".%(ext)s"
    limite_bytes = int(limite_mb) * 1024 * 1024

    def rejeitar_grande(info, *, incomplete=False):
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
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 2,
        "fragment_retries": 2,
        "match_filter": rejeitar_grande,
        "restrictfilenames": True,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
                "AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1"
            )
        },
    }

    try:
        with yt_dlp.YoutubeDL(opcoes) as ydl:
            info = ydl.extract_info(url_limpa, download=True)
            titulo = (info.get("title") or info.get("id") or "reel_instagram").strip()
    except yt_dlp.utils.DownloadError as exc:
        mensagem = str(exc)
        if "limite" in mensagem.lower() or "too large" in mensagem.lower():
            raise InstagramImportError(f"O vídeo ultrapassa o limite de {limite_mb} MB.") from exc
        if "login" in mensagem.lower() or "cookies" in mensagem.lower():
            raise InstagramImportError(
                "O Instagram exigiu login para acessar esse vídeo. Tente outro link ou use o upload manual."
            ) from exc
        raise InstagramImportError(
            "Não foi possível importar esse Reels. Confirme se ele está público e tente novamente."
        ) from exc

    candidatos = sorted(pasta.glob(f"{identificador}_instagram.*"))
    arquivo = next((p for p in candidatos if p.suffix.lower() == ".mp4"), None)
    if arquivo is None:
        arquivo = next((p for p in candidatos if p.is_file()), None)
    if arquivo is None or not arquivo.exists():
        raise InstagramImportError("O download terminou, mas o arquivo de vídeo não foi encontrado.")

    if arquivo.stat().st_size > limite_bytes:
        try:
            arquivo.unlink()
        except OSError:
            pass
        raise InstagramImportError(f"O vídeo ultrapassa o limite de {limite_mb} MB.")

    nome = re.sub(r"[^A-Za-z0-9._-]+", "_", titulo).strip("._")[:80] or "reel_instagram"
    return str(arquivo), f"{nome}.mp4"
