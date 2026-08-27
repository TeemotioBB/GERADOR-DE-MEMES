#!/usr/bin/env python3
"""
Coletor local da aba personalizada de Reels do Instagram.

Iniciado pelo botão "Rodar" da página Flask.
Abre uma janela real do Chrome/Edge no computador e coleta links únicos
da aba personalizada de Reels.

IMPORTANTE:
- precisa rodar em localhost;
- não usa o Railway para acessar o feed personalizado;
- no primeiro uso, se o Instagram pedir login, o coletor aguarda o login.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


BASE_DIR = Path(__file__).resolve().parent
PERFIL_BROWSER = BASE_DIR / ".instagram_automacao"

REEL_RE = re.compile(r"^/(?:reel|reels)/([A-Za-z0-9_-]+)/?$")


def normalizar_reel(url: str) -> str | None:
    if not url:
        return None

    url = url.strip()

    if url.startswith("/"):
        caminho = urlparse("https://www.instagram.com" + url).path
    else:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host not in {"instagram.com", "www.instagram.com", "m.instagram.com"}:
            return None
        caminho = parsed.path

    caminho = caminho.rstrip("/") + "/"
    match = REEL_RE.match(caminho)
    if not match:
        return None

    return f"https://www.instagram.com/reel/{match.group(1)}/"


def _status(callback, estado: str, mensagem: str, encontrados: int = 0):
    if callback:
        callback(
            estado=estado,
            mensagem=mensagem,
            encontrados=encontrados,
        )


def extrair_links_visiveis(page) -> list[str]:
    encontrados: list[str] = []

    atual = normalizar_reel(page.url)
    if atual:
        encontrados.append(atual)

    try:
        hrefs = page.locator(
            'a[href*="/reel/"], a[href*="/reels/"]'
        ).evaluate_all(
            """els => els.map(el => el.href || el.getAttribute('href') || '')"""
        )
    except Exception:
        hrefs = []

    for href in hrefs:
        link = normalizar_reel(href)
        if link:
            encontrados.append(link)

    return encontrados


def precisa_login(page) -> bool:
    url = (page.url or "").lower()
    if "/accounts/login" in url:
        return True

    try:
        return page.locator('input[name="username"]').count() > 0
    except Exception:
        return False


def abrir_contexto(playwright):
    """
    Usa um perfil separado para a automação e preserva o login.
    Tenta Chrome e depois Edge, sem precisar instalar Chromium do Playwright.
    """
    PERFIL_BROWSER.mkdir(parents=True, exist_ok=True)

    argumentos = {
        "user_data_dir": str(PERFIL_BROWSER),
        "headless": False,
        "viewport": {"width": 1280, "height": 900},
    }

    for canal in ("chrome", "msedge"):
        try:
            return playwright.chromium.launch_persistent_context(
                channel=canal,
                **argumentos,
            )
        except Exception:
            pass

    raise RuntimeError(
        "Não consegui abrir o Chrome ou Edge instalado no computador."
    )


def coletar(
    quantidade: int,
    callback=None,
    espera: float = 1.4,
    timeout_login: int = 300,
) -> list[str]:
    quantidade = int(quantidade)
    if quantidade <= 0:
        raise ValueError("A quantidade precisa ser maior que zero.")

    with sync_playwright() as p:
        _status(callback, "abrindo", "Abrindo o Chrome...", 0)
        context = abrir_contexto(p)

        try:
            page = context.pages[0] if context.pages else context.new_page()

            _status(callback, "abrindo", "Abrindo sua aba de Reels...", 0)
            page.goto(
                "https://www.instagram.com/reels/",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            time.sleep(2)

            if precisa_login(page):
                _status(
                    callback,
                    "aguardando_login",
                    "Faça login na janela do Instagram que abriu. "
                    "Depois a coleta continua sozinha.",
                    0,
                )

                limite = time.time() + timeout_login
                while time.time() < limite:
                    time.sleep(2)
                    if not precisa_login(page):
                        break

                if precisa_login(page):
                    raise RuntimeError(
                        "Tempo de login esgotado. Clique em Rodar novamente."
                    )

                page.goto(
                    "https://www.instagram.com/reels/",
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )
                time.sleep(3)

            encontrados: list[str] = []
            vistos: set[str] = set()
            sem_novos = 0
            max_movimentos = max(80, quantidade * 15)

            _status(
                callback,
                "coletando",
                f"Coletando Reels... 0/{quantidade}",
                0,
            )

            for _ in range(max_movimentos):
                novos = 0

                for link in extrair_links_visiveis(page):
                    if link in vistos:
                        continue

                    vistos.add(link)
                    encontrados.append(link)
                    novos += 1

                    _status(
                        callback,
                        "coletando",
                        f"Coletando Reels... {len(encontrados)}/{quantidade}",
                        len(encontrados),
                    )

                    if len(encontrados) >= quantidade:
                        break

                if len(encontrados) >= quantidade:
                    break

                if novos:
                    sem_novos = 0
                else:
                    sem_novos += 1

                try:
                    page.keyboard.press("ArrowDown")
                except Exception:
                    pass

                try:
                    page.mouse.wheel(0, 920)
                except Exception:
                    pass

                time.sleep(espera)

                if sem_novos and sem_novos % 8 == 0:
                    _status(
                        callback,
                        "coletando",
                        f"Aguardando novos Reels... {len(encontrados)}/{quantidade}",
                        len(encontrados),
                    )
                    time.sleep(3)

            return encontrados[:quantidade]

        finally:
            context.close()
