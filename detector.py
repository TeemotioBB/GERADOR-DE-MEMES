#!/usr/bin/env python3
"""
Detecta automaticamente o retangulo do video (card interno) dentro de um
print/quadro de reels de concorrente. Retorna (x, y, w, h).

V2 - refinamento de bordas:
- continua usando a deteccao robusta por fundo/contorno;
- depois refina as bordas do retangulo usando a mascara ORIGINAL, antes da
  morfologia;
- isso evita que textos muito proximos do video sejam "colados" ao card pelo
  fechamento morfologico, causando alguns pixels extras no topo/baixo/laterais.

O refinamento e propositalmente conservador: ele so corrige pequenos excessos.
"""

import sys
import cv2
import numpy as np


def _maior_run(vetor_bool):
    """Retorna (inicio, fim) da maior sequencia True; fim e exclusivo."""
    melhor_ini = None
    melhor_fim = None
    melhor_tam = 0

    ini = None
    for i, valor in enumerate(vetor_bool):
        if valor and ini is None:
            ini = i

        terminou = (not valor) or (i == len(vetor_bool) - 1)
        if ini is not None and terminou:
            fim = i if not valor else i + 1
            tam = fim - ini
            if tam > melhor_tam:
                melhor_tam = tam
                melhor_ini = ini
                melhor_fim = fim
            ini = None

    return melhor_ini, melhor_fim


def _suavizar(vetor, janela=7):
    janela = max(3, int(janela))
    if janela % 2 == 0:
        janela += 1

    if len(vetor) < janela:
        return vetor.astype(np.float32)

    kernel = np.ones(janela, np.float32) / janela
    return np.convolve(vetor.astype(np.float32), kernel, mode="same")


def _achar_inicio_sustentado(sinal, limite_busca, threshold, run_min):
    """
    Procura, no comeco do sinal, uma sequencia sustentada acima do threshold.
    Retorna o primeiro indice dessa sequencia.
    """
    limite_busca = max(1, min(len(sinal), int(limite_busca)))
    ativos = sinal[:limite_busca] >= threshold

    i = 0
    while i < len(ativos):
        if not ativos[i]:
            i += 1
            continue

        j = i
        while j < len(ativos) and ativos[j]:
            j += 1

        if (j - i) >= run_min:
            return i

        i = j

    return None


def _refinar_box(mask_raw, box):
    """
    Corrige pequenos excessos nas bordas do boundingRect.

    Por que isso ajuda:
    o CLOSE usado na deteccao grossa pode unir letras/legendas muito proximas
    ao card. A mascara raw ainda preserva a separacao entre texto e video.

    A funcao so permite correcoes pequenas, para nao comer o conteudo real.
    """
    if box is None:
        return None

    h, w = mask_raw.shape[:2]
    x, y, cw, ch = map(int, box)

    if cw <= 4 or ch <= 4:
        return box

    # Ignora as pontinhas das laterais ao avaliar linhas.
    # Isso tambem deixa o algoritmo tolerante a cantos arredondados.
    mx = max(2, int(round(cw * 0.045)))
    x1 = max(0, x + mx)
    x2 = min(w, x + cw - mx)

    if x2 <= x1:
        return box

    reg = (mask_raw[y:y + ch, x1:x2] > 0).astype(np.uint8)
    if reg.size == 0:
        return box

    ocupacao_linhas = reg.mean(axis=1)
    ocupacao_linhas = _suavizar(ocupacao_linhas, janela=max(5, int(ch * 0.012)))

    # Threshold dinamico: usa o miolo do proprio candidato como referencia.
    miolo_ini = int(ch * 0.25)
    miolo_fim = max(miolo_ini + 1, int(ch * 0.75))
    referencia = float(np.percentile(
        ocupacao_linhas[miolo_ini:miolo_fim],
        55
    ))

    # Nunca fica permissivo demais e nem exige preenchimento total.
    threshold = float(np.clip(referencia * 0.58, 0.28, 0.58))

    # Precisamos de algumas linhas consecutivas para evitar confundir letras
    # isoladas com o inicio do video.
    run_min = max(3, int(round(ch * 0.012)))

    # So permitimos mexer em uma faixa pequena das extremidades.
    # Assim o refinamento corrige "passou um pouquinho" sem destruir boxes bons.
    max_ajuste_y = max(3, int(round(ch * 0.055)))

    desloc_topo = _achar_inicio_sustentado(
        ocupacao_linhas,
        limite_busca=max_ajuste_y + run_min,
        threshold=threshold,
        run_min=run_min,
    )

    desloc_baixo_rev = _achar_inicio_sustentado(
        ocupacao_linhas[::-1],
        limite_busca=max_ajuste_y + run_min,
        threshold=threshold,
        run_min=run_min,
    )

    novo_y = y
    novo_fim_y = y + ch

    # Mantem 1 px de seguranca para fora da borda detectada.
    if desloc_topo is not None and 2 <= desloc_topo <= max_ajuste_y:
        novo_y = y + max(0, desloc_topo - 1)

    if desloc_baixo_rev is not None and 2 <= desloc_baixo_rev <= max_ajuste_y:
        novo_fim_y = y + ch - max(0, desloc_baixo_rev - 1)

    # Refinamento lateral, ainda mais conservador.
    my = max(2, int(round(ch * 0.06)))
    yy1 = max(0, novo_y + my)
    yy2 = min(h, novo_fim_y - my)

    novo_x = x
    novo_fim_x = x + cw

    if yy2 > yy1:
        reg_cols = (
            mask_raw[yy1:yy2, x:x + cw] > 0
        ).astype(np.uint8)

        ocupacao_cols = reg_cols.mean(axis=0)
        ocupacao_cols = _suavizar(
            ocupacao_cols,
            janela=max(5, int(cw * 0.012))
        )

        referencia_x = float(np.percentile(ocupacao_cols, 55))
        threshold_x = float(np.clip(referencia_x * 0.58, 0.28, 0.58))
        run_min_x = max(3, int(round(cw * 0.012)))
        max_ajuste_x = max(2, int(round(cw * 0.025)))

        desloc_esq = _achar_inicio_sustentado(
            ocupacao_cols,
            limite_busca=max_ajuste_x + run_min_x,
            threshold=threshold_x,
            run_min=run_min_x,
        )

        desloc_dir_rev = _achar_inicio_sustentado(
            ocupacao_cols[::-1],
            limite_busca=max_ajuste_x + run_min_x,
            threshold=threshold_x,
            run_min=run_min_x,
        )

        if desloc_esq is not None and 2 <= desloc_esq <= max_ajuste_x:
            novo_x = x + max(0, desloc_esq - 1)

        if desloc_dir_rev is not None and 2 <= desloc_dir_rev <= max_ajuste_x:
            novo_fim_x = x + cw - max(0, desloc_dir_rev - 1)

    novo_w = novo_fim_x - novo_x
    novo_h = novo_fim_y - novo_y

    # Validacao: nunca aceita uma correcao que encolha demais o candidato.
    if novo_w < cw * 0.90 or novo_h < ch * 0.88:
        return box

    if novo_w < 2 or novo_h < 2:
        return box

    return (
        int(novo_x),
        int(novo_y),
        int(novo_w),
        int(novo_h),
    )


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

    # 2. Mascara RAW: pixels que diferem do fundo.
    # Guardamos essa versao antes da morfologia para fazer o refinamento fino.
    diff = cv2.absdiff(gray, np.full_like(gray, fundo))
    _, mask_raw = cv2.threshold(diff, 28, 255, cv2.THRESH_BINARY)

    # 3. Mascara de deteccao grossa: fecha buracos e remove ruido.
    mask = mask_raw.copy()
    k = np.ones((15, 15), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_OPEN,
        np.ones((7, 7), np.uint8)
    )

    # 4. Maior contorno plausivel.
    contornos, _ = cv2.findContours(
        mask,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE
    )

    if not contornos:
        return None

    melhor = None
    melhor_area = 0

    for c in contornos:
        x, y, cw, ch = cv2.boundingRect(c)
        area = cw * ch

        if area < (w * h) * 0.06:
            continue

        if cw > w * 0.99 and ch > h * 0.99:
            continue

        if cw < w * 0.4 or ch < h * 0.18:
            continue

        if area > melhor_area:
            melhor_area = area
            melhor = (x, y, cw, ch)

    # 5. NOVO: refinamento fino das quatro bordas.
    return _refinar_box(mask_raw, melhor)


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

    # 2. caixa grande demais
    if ch > h * 0.78:
        conf *= 0.3

    # 3. proporcao muito alongada verticalmente
    if ch > 0 and (cw / ch) < 0.45:
        conf *= 0.5

    # 4. recorte estreito
    if cw < w * 0.5:
        conf *= 0.8

    return round(conf, 2)


if __name__ == "__main__":
    caminho = sys.argv[1]
    img = cv2.imread(caminho)

    if img is None:
        raise SystemExit(f"Nao foi possivel abrir a imagem: {caminho}")

    box = detectar_card(img)
    print(
        f"{caminho}: box={box} "
        f"confianca={confianca(img, box)}"
    )
