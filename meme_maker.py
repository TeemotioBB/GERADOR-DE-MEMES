#!/usr/bin/env python3
"""
Gerador de posts estilo tweet para a pagina de meme.
Recebe um video + legenda e devolve o video montado no template (fundo branco 9:16).
"""

import os
import sys
import json
import subprocess
import tempfile
import textwrap
from PIL import Image, ImageDraw, ImageFont, ImageChops

# ----------------- CONFIGURACOES FIXAS DO TEMPLATE -----------------
CANVAS_W = 1080          # largura final (9:16)
CANVAS_H = 1920          # altura final
BG_COLOR = (255, 255, 255)

_BASE = os.path.dirname(os.path.abspath(__file__))

# ----------------- PERFIS DISPONIVEIS -----------------
# Cada perfil tem nome, @ e o arquivo de avatar (foto de perfil).
# Para adicionar mais paginas no futuro, e so copiar um bloco aqui.
PERFIS = {
    "adultosofrido": {
        "nome": "Adulto Sofrido",
        "handle": "@adultosofrido",
        "avatar": os.path.join(_BASE, "avatar.png"),
    },
    "achadinhosofcs": {
        "nome": "achadinhosofcs",
        "handle": "@achadinhosofcs",
        "avatar": os.path.join(_BASE, "avatar2.png"),
    },
}
PERFIL_PADRAO = "adultosofrido"

# Valores ativos (preenchidos por set_perfil). Comecam no padrao.
PROFILE_NAME = PERFIS[PERFIL_PADRAO]["nome"]
PROFILE_HANDLE = PERFIS[PERFIL_PADRAO]["handle"]
AVATAR_PATH = PERFIS[PERFIL_PADRAO]["avatar"]


def set_perfil(chave):
    """Troca o perfil ativo (nome, @ e avatar) pelo de chave dada.
    Se a chave nao existir, mantem o padrao."""
    global PROFILE_NAME, PROFILE_HANDLE, AVATAR_PATH
    p = PERFIS.get(chave) or PERFIS[PERFIL_PADRAO]
    PROFILE_NAME = p["nome"]
    PROFILE_HANDLE = p["handle"]
    AVATAR_PATH = p["avatar"]

# Fontes (Liberation Sans = identica a Arial/Helvetica, parecida com a do X)
def _achar_fonte(*nomes):
    """Procura um arquivo de fonte em varios locais (Linux, Windows, Mac).
    Recebe nomes de arquivo candidatos e retorna o primeiro caminho que existir.
    Se nada for encontrado, retorna None (Pillow usa a fonte padrao)."""
    pastas = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "fontes"),  # empacotada (prioridade)
        "/usr/share/fonts/truetype/liberation",          # Linux
        "/usr/share/fonts/truetype/dejavu",              # Linux (alternativa)
        os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts"),  # Windows
        "/Library/Fonts", "/System/Library/Fonts", "/System/Library/Fonts/Supplemental",  # Mac
        os.path.dirname(os.path.abspath(__file__)),      # junto do script
    ]
    for nome in nomes:
        # caminho absoluto direto
        if os.path.isabs(nome) and os.path.exists(nome):
            return nome
        for pasta in pastas:
            caminho = os.path.join(pasta, nome)
            if os.path.exists(caminho):
                return caminho
    return None


# Fontes: tenta Liberation Sans (Linux), depois Arial (Windows), depois
# DejaVu/Helvetica como ultimo recurso. Todas sao sans-serif parecidas com a do X.
FONT_BOLD = _achar_fonte(
    "LiberationSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf",
    "DejaVuSans-Bold.ttf", "Helvetica.ttc"
)
FONT_REG = _achar_fonte(
    "LiberationSans-Regular.ttf", "arial.ttf", "Arial.ttf",
    "DejaVuSans.ttf", "Helvetica.ttc"
)


def _font(caminho, tamanho):
    """Carrega a fonte; se o caminho for None, usa a fonte padrao do Pillow."""
    if caminho:
        return ImageFont.truetype(caminho, tamanho)
    return ImageFont.load_default()

# Cores de texto
COLOR_NAME = (15, 20, 25)        # nome (preto X)
COLOR_HANDLE = (83, 100, 113)    # @handle (cinza X)
COLOR_CAPTION = (15, 20, 25)     # legenda

# Layout (coordenadas no canvas 1080x1920)
MARGIN_X = 90
HEADER_Y = 560           # topo do cabecalho (avatar/nome)
AVATAR_SIZE = 110
CARD_RADIUS = 28         # raio dos cantos arredondados do video
# -------------------------------------------------------------------


def run(cmd):
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Comando falhou:\n{' '.join(cmd)}\n\n{res.stderr[-2000:]}")
    return res


def get_video_size(path):
    res = run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "json", path
    ])
    info = json.loads(res.stdout)["streams"][0]
    return int(info["width"]), int(info["height"])


def has_audio(path):
    res = run([
        "ffprobe", "-v", "error", "-select_streams", "a",
        "-show_entries", "stream=index", "-of", "json", path
    ])
    return len(json.loads(res.stdout).get("streams", [])) > 0


def get_duration(path):
    res = run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "json", path
    ])
    return float(json.loads(res.stdout)["format"]["duration"])


def build_overlay(caption, video_disp_w, video_disp_h, video_y, header_y):
    """
    Monta a imagem PNG transparente com tudo EXCETO o video:
    fundo branco, avatar, nome, handle, legenda, e a moldura do card.
    O video sera colocado por baixo (no buraco do card) pelo FFmpeg.
    header_y = topo do cabecalho (calculado para centralizar o bloco).
    """
    img = Image.new("RGBA", (CANVAS_W, CANVAS_H), BG_COLOR + (255,))
    draw = ImageDraw.Draw(img)

    f_name = _font(FONT_BOLD, 40)
    f_handle = _font(FONT_REG, 36)
    f_caption = _font(FONT_REG, 44)

    # ---- Avatar ----
    if os.path.exists(AVATAR_PATH):
        av = Image.open(AVATAR_PATH).convert("RGBA").resize((AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS)
        # Recorta em circulo automaticamente (funciona com qualquer foto quadrada).
        # Usa supersampling (4x) para a borda do circulo ficar lisa, sem serrilhado.
        escala = 4
        mascara_g = Image.new("L", (AVATAR_SIZE * escala, AVATAR_SIZE * escala), 0)
        ImageDraw.Draw(mascara_g).ellipse(
            (0, 0, AVATAR_SIZE * escala, AVATAR_SIZE * escala), fill=255
        )
        mascara = mascara_g.resize((AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS)
        # combina a transparencia que a foto ja tenha com a mascara circular
        alpha_atual = av.split()[3]
        nova_alpha = ImageChops.multiply(alpha_atual, mascara)
        av.putalpha(nova_alpha)
        img.paste(av, (MARGIN_X, header_y), av)

    # ---- Nome + handle ----
    text_x = MARGIN_X + AVATAR_SIZE + 28
    draw.text((text_x, header_y + 12), PROFILE_NAME, font=f_name, fill=COLOR_NAME)
    draw.text((text_x, header_y + 62), PROFILE_HANDLE, font=f_handle, fill=COLOR_HANDLE)

    # ---- Legenda (respeita as quebras de linha do usuario) ----
    caption_y = header_y + AVATAR_SIZE + 50
    max_w = CANVAS_W - 2 * MARGIN_X
    lines = wrap_text(caption, f_caption, max_w, draw)
    line_h = 58
    for i, line in enumerate(lines):
        draw.text((MARGIN_X, caption_y + i * line_h), line, font=f_caption, fill=COLOR_CAPTION)

    # ---- Moldura do card do video (buraco transparente com cantos arredondados) ----
    card_x = MARGIN_X
    card_w = video_disp_w
    card_h = video_disp_h
    # Mascara do buraco no tamanho do canvas inteiro: branco onde fica o video
    hole_full = Image.new("L", (CANVAS_W, CANVAS_H), 0)
    hole_card = Image.new("L", (card_w, card_h), 0)
    ImageDraw.Draw(hole_card).rounded_rectangle((0, 0, card_w, card_h), radius=CARD_RADIUS, fill=255)
    hole_full.paste(hole_card, (card_x, video_y))
    # alpha final = alpha_atual onde NAO ha buraco; 0 onde ha buraco
    alpha = img.split()[3]
    # onde hole_full==255 -> alpha 0 ; senao mantem
    inv = Image.eval(hole_full, lambda v: 255 - v)  # 0 no buraco, 255 fora
    new_alpha = ImageChops.multiply(alpha, inv.point(lambda v: 255 if v > 127 else 0))
    img.putalpha(new_alpha)

    return img, (card_x, video_y, card_w, card_h)


def wrap_text(text, font, max_w, draw):
    """Quebra o texto em linhas para caber em max_w, MAS respeitando as
    quebras de linha que o usuario digitou (Enter). Uma linha em branco
    digitada pelo usuario vira uma linha em branco no resultado."""
    linhas_finais = []
    # primeiro separa pelos Enters do usuario (cada item e um "paragrafo")
    # normaliza quebras do Windows (\r\n) e Mac antigo (\r)
    texto = text.replace("\r\n", "\n").replace("\r", "\n")
    for paragrafo in texto.split("\n"):
        if paragrafo.strip() == "":
            # linha em branco digitada -> mantem o respiro
            linhas_finais.append("")
            continue
        # dentro do paragrafo, quebra por largura
        cur = ""
        for w in paragrafo.split():
            test = (cur + " " + w).strip()
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] <= max_w:
                cur = test
            else:
                if cur:
                    linhas_finais.append(cur)
                cur = w
        if cur:
            linhas_finais.append(cur)
    return linhas_finais


def make_post(video_path, caption, output_path, perfil=None):
    if perfil:
        set_perfil(perfil)
    return _gerar(video_path, caption, output_path, crop=None)


def make_post_from_crop(video_path, caption, output_path, crop, perfil=None):
    """crop = (x, y, w, h) em pixels do video de entrada: recorta essa regiao
    (mantendo audio) e monta no template."""
    if perfil:
        set_perfil(perfil)
    return _gerar(video_path, caption, output_path, crop=crop)


def _gerar(video_path, caption, output_path, crop=None):
    vw, vh = get_video_size(video_path)
    if crop is not None:
        cx0, cy0, cw0, ch0 = [int(round(v)) for v in crop]
        # garante limites validos dentro do video
        cx0 = max(0, min(cx0, vw - 2))
        cy0 = max(0, min(cy0, vh - 2))
        cw0 = max(2, min(cw0, vw - cx0))
        ch0 = max(2, min(ch0, vh - cy0))
        # H.264 exige dimensoes pares: arredonda para baixo
        cw0 -= cw0 % 2
        ch0 -= ch0 % 2
        cw0 = max(2, cw0)
        ch0 = max(2, ch0)
        aspect = cw0 / ch0
    else:
        aspect = vw / vh

    # Largura do card = largura util; altura proporcional ao video
    card_w = CANVAS_W - 2 * MARGIN_X
    card_h = int(card_w / aspect)

    # Mede a legenda para calcular a altura do bloco
    f_caption = _font(FONT_REG, 44)
    tmp_img = Image.new("RGB", (10, 10))
    tmp_draw = ImageDraw.Draw(tmp_img)
    lines = wrap_text(caption, f_caption, CANVAS_W - 2 * MARGIN_X, tmp_draw)
    caption_block_h = len(lines) * 58

    # Espacamentos internos do bloco
    GAP_HEADER_CAP = 50   # cabecalho -> legenda
    GAP_CAP_VIDEO = 40    # legenda -> video

    # Garante que o card cabe na largura util do canvas (com margem vertical)
    margem_seg = 80
    altura_disp = CANVAS_H - 2 * margem_seg
    # altura total do bloco com o card atual
    def altura_bloco(ch):
        return AVATAR_SIZE + GAP_HEADER_CAP + caption_block_h + GAP_CAP_VIDEO + ch
    if altura_bloco(card_h) > altura_disp:
        # encolhe o card para o bloco caber
        sobra = AVATAR_SIZE + GAP_HEADER_CAP + caption_block_h + GAP_CAP_VIDEO
        card_h = max(2, altura_disp - sobra)
        card_w = int(card_h * aspect)
        if card_w > CANVAS_W - 2 * MARGIN_X:
            card_w = CANVAS_W - 2 * MARGIN_X
            card_h = int(card_w / aspect)

    # H.264 exige dimensoes pares no card
    card_w -= card_w % 2
    card_h -= card_h % 2
    card_w = max(2, card_w)
    card_h = max(2, card_h)

    # Centraliza o bloco inteiro verticalmente
    bloco_h = altura_bloco(card_h)
    header_y = max(margem_seg, (CANVAS_H - bloco_h) // 2)
    video_y = header_y + AVATAR_SIZE + GAP_HEADER_CAP + caption_block_h + GAP_CAP_VIDEO

    overlay, (cx, cy, cw, ch) = build_overlay(caption, card_w, card_h, video_y, header_y)

    with tempfile.TemporaryDirectory() as td:
        overlay_path = os.path.join(td, "overlay.png")
        overlay.save(overlay_path)

        # FFmpeg: escala o video para o card, coloca sobre fundo branco,
        # aplica cantos arredondados, e poe o overlay (template) por cima.
        audio = has_audio(video_path)
        dur = get_duration(video_path)

        # Prefixo de recorte da fonte (quando o usuario recortou o card do
        # video do concorrente). Aplicado ANTES de escalar para o card.
        if crop is not None:
            crop_prefix = f"crop={cw0}:{ch0}:{cx0}:{cy0},"
        else:
            crop_prefix = ""

        # Fundo branco via source 'color' (estavel com video).
        # Camadas: color branco -> video (recortado/escalado) -> template com buraco.
        filter_complex = (
            f"color=white:s={CANVAS_W}x{CANVAS_H}:r=30[bgc];"
            f"[0:v]{crop_prefix}scale={cw}:{ch}:force_original_aspect_ratio=increase,"
            f"crop={cw}:{ch},setsar=1[v];"
            f"[bgc][v]overlay={cx}:{cy}:shortest=1[based];"
            f"[based][1:v]overlay=0:0[outv]"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-framerate", "30", "-loop", "1", "-t", f"{dur}", "-i", overlay_path,
            "-filter_complex", filter_complex,
            "-map", "[outv]",
        ]
        if audio:
            cmd += ["-map", "0:a", "-c:a", "aac", "-b:a", "128k"]
        cmd += [
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-shortest", "-movflags", "+faststart",
            output_path
        ]
        run(cmd)

    return output_path


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Uso: python3 meme_maker.py <video> <legenda> <saida.mp4>")
        sys.exit(1)
    video = sys.argv[1]
    legenda = sys.argv[2]
    saida = sys.argv[3]
    make_post(video, legenda, saida)
    print("Pronto:", saida)
