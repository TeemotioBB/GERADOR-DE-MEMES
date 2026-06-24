#!/usr/bin/env python3
"""
Detecta automaticamente o retangulo do video (card interno) dentro de um
print/quadro de reels de concorrente. Retorna (x, y, w, h).

Estrategia:
- O fundo da postagem costuma ser uma cor uniforme (branco ou preto) nas
  laterais. Detectamos essa cor amostrando as bordas.
- Criamos uma mascara do que NAO e fundo.
- O card do video e o maior componente retangular dessa mascara, geralmente
  centralizado horizontalmente e ocupando boa largura.
"""

import sys
import cv2
import numpy as np


def detectar_card(img_bgr):
    h, w = img_bgr.shape[:2]
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

    # 1. Cor de fundo: cor MAIS FREQUENTE nas bordas (moda), que separa
    #    melhor fundos preto/branco do que a mediana.
    m = max(2, int(min(h, w) * 0.02))
    bordas = np.concatenate([
        gray[:m, :].ravel(), gray[-m:, :].ravel(),
        gray[:, :m].ravel(), gray[:, -m:].ravel()
    ])
    hist = np.bincount(bordas, minlength=256)
    fundo = int(np.argmax(hist))

    # 2. Mascara: pixels que diferem do fundo
    diff = cv2.absdiff(gray, np.full_like(gray, fundo))
    _, mask = cv2.threshold(diff, 28, 255, cv2.THRESH_BINARY)

    # 3. Fecha buracos e remove ruido
    k = np.ones((15, 15), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))

    # 4. Maior contorno
    contornos, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contornos:
        return None

    melhor = None
    melhor_area = 0
    for c in contornos:
        x, y, cw, ch = cv2.boundingRect(c)
        area = cw * ch
        # filtros: tem que ser razoavelmente grande e nao a imagem inteira
        if area < (w * h) * 0.06:
            continue
        if cw > w * 0.99 and ch > h * 0.99:
            continue
        # o card de video costuma ser largo (>40% da largura) e alto (>20%)
        if cw < w * 0.4 or ch < h * 0.18:
            continue
        if area > melhor_area:
            melhor_area = area
            melhor = (x, y, cw, ch)

    return melhor


def confianca(img_bgr, box):
    """Heuristica de quao confiavel foi a deteccao (0 a 1).
    Baixa confianca = avisar o usuario para conferir/ajustar."""
    if box is None:
        return 0.0
    h, w = img_bgr.shape[:2]
    x, y, cw, ch = box
    conf = 1.0

    # 1. centralizacao horizontal
    centro_box = x + cw / 2
    desvio = abs(centro_box - w / 2) / (w / 2)
    conf *= (1.0 - min(desvio, 1.0))

    # 2. caixa grande demais (provavel que pegou texto/tarjas junto):
    #    se ocupa quase toda a altura, e suspeito
    if ch > h * 0.78:
        conf *= 0.3
    # 3. proporcao muito alongada verticalmente tambem e suspeita
    if ch > 0 and (cw / ch) < 0.45:
        conf *= 0.5
    # 4. recorte estreito
    if cw < w * 0.5:
        conf *= 0.8

    return round(conf, 2)


if __name__ == "__main__":
    caminho = sys.argv[1]
    img = cv2.imread(caminho)
    box = detectar_card(img)
    print(f"{caminho}: box={box} confianca={confianca(img, box)}")
