#!/usr/bin/env python3
"""Importação de Reels públicos do Instagram usando yt-dlp.

O Instagram pode devolver uma resposta vazia para IPs de datacenter, mesmo
quando o Reels é público. Este módulo prioriza cookies armazenados em variável do Railway, quando presentes,
e tenta anonimamente apenas quando não existe sessão configurada.

Variáveis aceitas:
- INSTAGRAM_COOKIES_B64: conteúdo de cookies.txt codificado em Base64 (recomendado)
- INSTAGRAM_COOKIES: conteúdo bruto do cookies.txt no formato Netscape
- INSTAGRAM_FORCE_IPV4: "1" para forçar IPv4
"""

from __future__ import annotations

import base64
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp

# curl-cffi permite ao yt-dlp imitar o TLS de um navegador real.
try:
    import curl_cffi  # noqa: F401
    from yt_dlp.networking.impersonate import ImpersonateTarget
    _TEM_IMPERSONATE = True
except Exception:
    ImpersonateTarget = None
    _TEM_IMPERSONATE = False


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
        "http error 401",
        "401: unauthorized",
        "http error 403",
        "403: forbidden",
    )
    return any(sinal in texto for sinal in sinais)


def _detalhe(mensagem: str) -> str:
    """Trecho final da mensagem real do yt-dlp, sem códigos de cor ANSI."""
    limpa = re.sub(r"\x1b\[[0-9;]*m", "", mensagem or "").strip()
    return limpa[-300:]


def _opcoes_yt_dlp(
    template: str,
    limite_bytes: int,
    limite_mb: int,
    cookiefile: str | None,
    usar_impersonate: bool = True,
) -> dict:
    def rejeitar_grande(info, *, incomplete=False):
        del incomplete
        tamanho = info.get("filesize") or info.get("filesize_approx")
        if tamanho and tamanho > limite_bytes:
            return f"O vídeo ultrapassa o limite de {limite_mb} MB."
        return None

    opcoes = {
        "outtmpl": template,
        # Fallbacks amplos: se o Instagram mudar os formatos, não falha por
        # "Requested format is not available".
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b/best",
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
        # Sem User-Agent fixo: um UA que não bate com a impressão digital TLS
        # é sinal clássico de bot. O yt-dlp/curl-cffi cuida disso.
        "http_headers": {
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        },
    }

    if usar_impersonate and _TEM_IMPERSONATE:
        opcoes["impersonate"] = ImpersonateTarget("chrome")

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
    def _rodar(usar_impersonate: bool) -> dict:
        opcoes = _opcoes_yt_dlp(
            template=template,
            limite_bytes=limite_bytes,
            limite_mb=limite_mb,
            cookiefile=cookiefile,
            usar_impersonate=usar_impersonate,
        )
        with yt_dlp.YoutubeDL(opcoes) as ydl:
            return ydl.extract_info(url, download=True)

    if not _TEM_IMPERSONATE:
        return _rodar(False)

    try:
        return _rodar(True)
    except Exception as exc:
        # Algumas versões do extractor e servidores respondem diferentemente
        # com e sem impersonação TLS. Em 401/403, testar o transporte padrão
        # uma vez antes de declarar a sessão recusada.
        motivo = str(exc).lower()
        if ("impersonat" in motivo or "401" in motivo or "403" in motivo):
            return _rodar(False)
        raise


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
        # Evita solicitar posts anonimamente quando há uma sessão configurada.
        cookiefile = _criar_arquivo_cookies_temporario()
        if cookiefile:
            # Validar estrutura antes da solicitação; não imprimir cookies nem valores.
            with open(cookiefile, encoding="utf-8") as f:
                linhas = [l.rstrip("\n") for l in f if l.strip() and not l.startswith("#")]
            entradas = [l.split("\t") for l in linhas]
            cookies_ig = [e for e in entradas if len(e) >= 7 and e[0].lstrip(".").endswith("instagram.com")]
            nomes = {e[5] for e in cookies_ig}
            if "sessionid" not in nomes:
                raise InstagramImportError(
                    "INSTAGRAM_COOKIES_B64 foi lido, mas não contém sessionid para instagram.com. "
                    "Exporte novamente os cookies com o Instagram conectado."
                )
        try:
            info = _executar_download(
                url_limpa, template, limite_bytes, limite_mb, cookiefile=cookiefile
            )
        except yt_dlp.utils.DownloadError as erro:
            mensagem = str(erro)
            if "limite" in mensagem.lower() or "too large" in mensagem.lower():
                raise InstagramImportError(
                    f"O vídeo ultrapassa o limite de {limite_mb} MB."
                ) from erro
            if _erro_pede_autenticacao(mensagem):
                if cookiefile:
                    raise InstagramImportError(
                        "O Instagram recusou a sessão enviada pelo Railway. "
                        "O cookie sessionid está presente, mas pode ter expirado ou sido bloqueado, "
                        "ou o IP do servidor pode estar limitado. Atualize os cookies e tente novamente mais tarde. "
                        f"Detalhe: {_detalhe(mensagem)}"
                    ) from erro
                raise InstagramImportError(
                    "O Instagram exige autenticação para esse download. "
                    "Configure INSTAGRAM_COOKIES_B64 com os cookies de uma sessão ativa. "
                    f"Detalhe: {_detalhe(mensagem)}"
                ) from erro
            raise InstagramImportError(
                "Não foi possível importar esse Reels. Confirme o link e tente novamente. "
                f"Detalhe: {_detalhe(mensagem)}"
            ) from erro

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
