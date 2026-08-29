#!/usr/bin/env python3
"""
Gerador de posts estilo tweet para a pagina de meme.
"""

import os
import sys
import json
import subprocess
import tempfile
import random
import base64
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageChops

try:
    from pilmoji import Pilmoji
    from pilmoji.source import BaseSource, TwitterEmojiSource
except ImportError:
    Pilmoji = None
    BaseSource = object
    TwitterEmojiSource = None

# ----------------- CONFIGURACOES FIXAS DO TEMPLATE -----------------
CANVAS_W = 1080
CANVAS_H = 1920
BG_COLOR = (255, 255, 255)

_BASE = os.path.dirname(os.path.abspath(__file__))

# Fallback embutido do avatar da Adulta Sofrida.
# Assim o perfil continua exibindo a foto mesmo se o avatar4.png
# não estiver presente no Railway ou no agente local.
_AVATAR_ADULTA_SOFRIDA_B64 = "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"

# ----------------- PERFIS DISPONIVEIS -----------------
# Cada perfil pode sobrescrever apenas os valores visuais que quiser.
# Assim, os dois perfis antigos permanecem exatamente como estavam.
LAYOUT_PADRAO = {
    "margin_x": 90,
    "avatar_size": 110,
    "text_gap_x": 28,
    "gap_header_caption": 50,
    "gap_caption_video": 40,
    "card_radius": 28,
    "safe_margin_y": 80,
}

PERFIS = {
    "adultosofrido": {
        "nome": "Adulto Sofrido",
        "handle": "@adultosofrido",
        "avatar": os.path.join(_BASE, "avatar.png"),
        "logo": os.path.join(_BASE, "logo_adultosofrido.png"),
        "logo_opacity": 0.25,
        "logo_width": 130,   # largura em pixels dentro do card
    },
    "adultasofrida": {
        "nome": "Adulta Sofrida",
        "handle": "@AdultaSofrida",
        "avatar": os.path.join(_BASE, "avatar4.png"),
        "logo": os.path.join(_BASE, "logo_adultasofrida.png"),
        "logo_opacity": 0.25,
        "logo_width": 130,
    },
    "achadinhosofcs": {
        "nome": "achadinhosofcs",
        "handle": "@achadinhosofcs",
        "avatar": os.path.join(_BASE, "avatar2.png"),
    },
    "viajantesofrida": {
        "nome": "Viajante Sofrida",
        "handle": "@viajantesofrida",
        "avatar": os.path.join(_BASE, "avatar3.png"),
        # Layout exclusivo deste perfil:
        # vídeo mais largo, avatar menor e mais distância antes do vídeo.
        "layout": {
            "margin_x": 70,
            "avatar_size": 96,
            "text_gap_x": 22,
            "gap_header_caption": 38,
            "gap_caption_video": 62,
            "card_radius": 18,
            "safe_margin_y": 70,
        },
    },
}
PERFIL_PADRAO = "adultosofrido"

PROFILE_NAME = PERFIS[PERFIL_PADRAO]["nome"]
PROFILE_HANDLE = PERFIS[PERFIL_PADRAO]["handle"]
AVATAR_PATH = PERFIS[PERFIL_PADRAO]["avatar"]

_layout_inicial = {**LAYOUT_PADRAO, **PERFIS[PERFIL_PADRAO].get("layout", {})}
MARGIN_X = _layout_inicial["margin_x"]
AVATAR_SIZE = _layout_inicial["avatar_size"]
TEXT_GAP_X = _layout_inicial["text_gap_x"]
GAP_HEADER_CAP = _layout_inicial["gap_header_caption"]
GAP_CAP_VIDEO = _layout_inicial["gap_caption_video"]
CARD_RADIUS = _layout_inicial["card_radius"]
SAFE_MARGIN_Y = _layout_inicial["safe_margin_y"]


PERFIL_ATUAL = PERFIL_PADRAO

def set_perfil(chave):
    global PROFILE_NAME, PROFILE_HANDLE, AVATAR_PATH, PERFIL_ATUAL
    global MARGIN_X, AVATAR_SIZE, TEXT_GAP_X
    global GAP_HEADER_CAP, GAP_CAP_VIDEO, CARD_RADIUS, SAFE_MARGIN_Y

    p = PERFIS.get(chave) or PERFIS[PERFIL_PADRAO]
    layout = {**LAYOUT_PADRAO, **p.get("layout", {})}

    PERFIL_ATUAL = chave if chave in PERFIS else PERFIL_PADRAO
    PROFILE_NAME = p["nome"]
    PROFILE_HANDLE = p["handle"]
    AVATAR_PATH = p["avatar"]
    MARGIN_X = layout["margin_x"]
    AVATAR_SIZE = layout["avatar_size"]
    TEXT_GAP_X = layout["text_gap_x"]
    GAP_HEADER_CAP = layout["gap_header_caption"]
    GAP_CAP_VIDEO = layout["gap_caption_video"]
    CARD_RADIUS = layout["card_radius"]
    SAFE_MARGIN_Y = layout["safe_margin_y"]


def _achar_fonte(*nomes):
    pastas = [
        os.path.join(_BASE, "fontes"),
        "/usr/share/fonts/truetype/liberation",
        "/usr/share/fonts/truetype/dejavu",
        os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts"),
        "/Library/Fonts", "/System/Library/Fonts", "/System/Library/Fonts/Supplemental",
        _BASE,
    ]
    for nome in nomes:
        if os.path.isabs(nome) and os.path.exists(nome):
            return nome
        for pasta in pastas:
            caminho = os.path.join(pasta, nome)
            if os.path.exists(caminho):
                return caminho
    return None


FONT_BOLD = _achar_fonte("LiberationSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf")
FONT_REG = _achar_fonte("LiberationSans-Regular.ttf", "arial.ttf", "Arial.ttf", "DejaVuSans.ttf")


def _font(caminho, tamanho):
    if caminho:
        return ImageFont.truetype(caminho, tamanho)
    return ImageFont.load_default()


def _achar_fonte_emoji():
    """Procura uma fonte colorida de emojis instalada no sistema."""
    caminhos = [
        os.path.join(_BASE, "fontes", "NotoColorEmoji.ttf"),
        "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
        "/usr/share/fonts/noto/NotoColorEmoji.ttf",
        "/usr/local/share/fonts/NotoColorEmoji.ttf",
    ]
    for caminho in caminhos:
        if os.path.exists(caminho):
            return caminho
    return None


class _FonteEmojiLocal(BaseSource):
    """Transforma emojis da Noto Color Emoji em PNG para o Pilmoji."""

    def __init__(self, caminho_fonte):
        self.fonte = ImageFont.truetype(caminho_fonte, 109)
        self.cache = {}

    def get_emoji(self, emoji, /):
        if emoji in self.cache:
            return BytesIO(self.cache[emoji])

        tamanho = 160
        asset = Image.new("RGBA", (tamanho, tamanho), (0, 0, 0, 0))
        draw = ImageDraw.Draw(asset)
        try:
            bbox = draw.textbbox((0, 0), emoji, font=self.fonte, embedded_color=True)
            largura = bbox[2] - bbox[0]
            altura = bbox[3] - bbox[1]
            x = (tamanho - largura) / 2 - bbox[0]
            y = (tamanho - altura) / 2 - bbox[1]
            draw.text((x, y), emoji, font=self.fonte, embedded_color=True)
        except Exception:
            return None

        if asset.getbbox() is None:
            return None

        buf = BytesIO()
        asset.save(buf, format="PNG")
        dados = buf.getvalue()
        self.cache[emoji] = dados
        return BytesIO(dados)

    def get_discord_emoji(self, id, /):
        return None


def _criar_fonte_emoji():
    caminho = _achar_fonte_emoji()
    if caminho and Pilmoji is not None:
        try:
            return _FonteEmojiLocal(caminho)
        except Exception:
            pass
    return TwitterEmojiSource if TwitterEmojiSource is not None else None


COLOR_NAME = (15, 20, 25)
COLOR_HANDLE = (83, 100, 113)
COLOR_CAPTION = (15, 20, 25)



def run(cmd):
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Comando falhou:\n{' '.join(cmd)}\n\n{res.stderr[-2000:]}")
    return res


def get_video_size(path):
    res = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", path])
    info = json.loads(res.stdout)["streams"][0]
    return int(info["width"]), int(info["height"])


def has_audio(path):
    res = run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "json", path])
    return len(json.loads(res.stdout).get("streams", [])) > 0


def get_duration(path):
    res = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path])
    return float(json.loads(res.stdout)["format"]["duration"])


# ====================== PRIVACIDADE E QUALIDADE VISUAL ======================
def _metadata_clean_args():
    """Impede a cópia de dados pessoais e tags do arquivo de origem."""
    return [
        "-map_metadata", "-1",
        "-map_chapters", "-1",
        "-metadata", "title=",
        "-metadata", "artist=",
        "-metadata", "author=",
        "-metadata", "comment=",
        "-metadata", "description=",
        "-metadata", "copyright=",
        "-metadata", "creation_time=",
        "-metadata", "date=",
        "-metadata", "location=",
        "-metadata", "location-eng=",
        "-metadata", "make=",
        "-metadata", "model=",
        "-metadata", "software=",
        "-metadata", "encoder=",
        "-metadata:s:v:0", "title=",
        "-metadata:s:v:0", "encoder=",
        "-metadata:s:v:0", "handler_name=",
        "-metadata:s:a:0", "title=",
        "-metadata:s:a:0", "encoder=",
        "-metadata:s:a:0", "handler_name=",
    ]


def deep_clean_mp4(input_path, output_path, remove_sei=True):
    """Limpeza final por stream copy: não decodifica nem recomprime o vídeo."""
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-map", "0:v:0",
        "-map", "0:a:0?",
        "-c", "copy",
        "-sn", "-dn",
    ]
    cmd += _metadata_clean_args()
    cmd += ["-fflags", "+bitexact"]

    # Como a saída principal é sempre H.264, remove mensagens SEI internas.
    # Pode remover closed captions embutidas, mas não textos visíveis no vídeo.
    if remove_sei:
        cmd += ["-bsf:v", "filter_units=remove_types=6"]

    cmd += ["-movflags", "+faststart", output_path]
    run(cmd)
    return output_path


def _normalizar_opcoes_uniqueness(options):
    """Normaliza opções de uniqueness.

    Chaves principais (desligadas por padrão):
    - edicoes_extras: ativa flip, cor, grão, vinheta, zoom, crop aleatório e velocidade aleatória
    - usar_logo: ativa a logomarca do perfil atual
    """
    options = dict(options or {})

    try:
        crf_solicitado = int(options.get("crf", 18))
    except (TypeError, ValueError):
        crf_solicitado = 18
    crf = max(0, min(crf_solicitado, 18))

    # Chave mestre das edições extras (OFF por padrão)
    edicoes_extras = bool(options.get("edicoes_extras", False))

    # Chave da logomarca (OFF por padrão)
    usar_logo = bool(options.get("usar_logo", False))

    # Velocidade: só randomiza se edicoes_extras estiver ligada
    try:
        speed = float(options.get("speed_factor", 1.0))
    except (TypeError, ValueError):
        speed = 1.0

    if edicoes_extras:
        # Se veio 0 ou 1.0, randomiza; senão respeita o valor enviado
        if speed <= 0 or abs(speed - 1.0) < 0.001:
            speed = round(random.uniform(0.97, 1.04), 4)
    else:
        speed = 1.0

    if speed <= 0:
        speed = 1.0

    return {
        "edicoes_extras": edicoes_extras,
        "usar_logo": usar_logo,
        # Sub-opções só fazem efeito se edicoes_extras=True
        "light_crop": bool(options.get("light_crop", True)),
        "color_adjust": bool(options.get("color_adjust", True)),
        "subtle_grain": bool(options.get("subtle_grain", True)),
        "stronger_visuals": bool(options.get("stronger_visuals", True)),
        "random_flip": bool(options.get("random_flip", True)),
        "vignette": bool(options.get("vignette", True)),
        "dynamic_zoom": bool(options.get("dynamic_zoom", True)),
        "speed_factor": speed,
        "crf": crf,
        "preset": str(options.get("preset", "slow") or "slow"),
        "deep_metadata_clean": bool(options.get("deep_metadata_clean", True)),
        "remove_h264_sei": bool(options.get("remove_h264_sei", True)),
    }


def _atempo_filter(speed):
    """Monta uma cadeia atempo válida mesmo para valores fora de 0.5–2.0."""
    fatores = []
    restante = float(speed)

    while restante > 2.0:
        fatores.append(2.0)
        restante /= 2.0
    while restante < 0.5:
        fatores.append(0.5)
        restante /= 0.5

    fatores.append(restante)
    return ",".join(f"atempo={fator:.8f}" for fator in fatores)


# ====================== RESTO DO CÓDIGO (mantido limpo) ======================
def build_overlay(caption, video_disp_w, video_disp_h, video_y, header_y):
    img = Image.new("RGBA", (CANVAS_W, CANVAS_H), BG_COLOR + (255,))
    draw = ImageDraw.Draw(img)

    f_name = _font(FONT_BOLD, 40)
    f_handle = _font(FONT_REG, 36)
    f_caption = _font(FONT_REG, 44)

    av = None
    if os.path.exists(AVATAR_PATH):
        av = Image.open(AVATAR_PATH).convert("RGBA")
    elif PERFIL_ATUAL == "adultasofrida":
        try:
            av = Image.open(BytesIO(base64.b64decode(_AVATAR_ADULTA_SOFRIDA_B64))).convert("RGBA")
        except Exception:
            av = None

    if av is not None:
        # ImageOps.fit faz um recorte central quadrado antes de aplicar a máscara,
        # evitando deformação e garantindo a foto redondinha.
        from PIL import ImageOps
        av = ImageOps.fit(av, (AVATAR_SIZE, AVATAR_SIZE), method=Image.LANCZOS, centering=(0.5, 0.45))
        escala = 4
        mascara_g = Image.new("L", (AVATAR_SIZE * escala, AVATAR_SIZE * escala), 0)
        ImageDraw.Draw(mascara_g).ellipse((0, 0, AVATAR_SIZE * escala - 1, AVATAR_SIZE * escala - 1), fill=255)
        mascara = mascara_g.resize((AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS)
        alpha_atual = av.split()[3]
        nova_alpha = ImageChops.multiply(alpha_atual, mascara)
        av.putalpha(nova_alpha)
        img.paste(av, (MARGIN_X, header_y), av)

    text_x = MARGIN_X + AVATAR_SIZE + TEXT_GAP_X
    draw.text((text_x, header_y + 12), PROFILE_NAME, font=f_name, fill=COLOR_NAME)
    draw.text((text_x, header_y + 62), PROFILE_HANDLE, font=f_handle, fill=COLOR_HANDLE)

    caption_y = header_y + AVATAR_SIZE + GAP_HEADER_CAP
    max_w = CANVAS_W - 2 * MARGIN_X
    line_h = 58

    # Renderiza a legenda em uma camada separada para aceitar emojis coloridos.
    legenda_renderizada = False
    if Pilmoji is not None:
        camada_legenda = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
        draw_legenda = ImageDraw.Draw(camada_legenda)
        fonte_emoji = _criar_fonte_emoji()
        if fonte_emoji is not None:
            try:
                with Pilmoji(
                    camada_legenda,
                    source=fonte_emoji,
                    draw=draw_legenda,
                    emoji_scale_factor=1.05,
                    emoji_position_offset=(0, 4),
                ) as emoji_draw:
                    lines = wrap_text(caption, f_caption, max_w, draw_legenda, emoji_draw)
                    for i, line in enumerate(lines):
                        emoji_draw.text(
                            (MARGIN_X, caption_y + i * line_h),
                            line,
                            font=f_caption,
                            fill=COLOR_CAPTION,
                            emoji_scale_factor=1.05,
                            emoji_position_offset=(0, 4),
                        )
                img.alpha_composite(camada_legenda)
                legenda_renderizada = True
            except Exception:
                legenda_renderizada = False

    # Fallback: mantém o gerador funcionando mesmo se o serviço de emojis falhar.
    if not legenda_renderizada:
        lines = wrap_text(caption, f_caption, max_w, draw)
        for i, line in enumerate(lines):
            draw.text((MARGIN_X, caption_y + i * line_h), line, font=f_caption, fill=COLOR_CAPTION)

    card_x = MARGIN_X
    card_w = video_disp_w
    card_h = video_disp_h
    hole_full = Image.new("L", (CANVAS_W, CANVAS_H), 0)
    hole_card = Image.new("L", (card_w, card_h), 0)
    ImageDraw.Draw(hole_card).rounded_rectangle((0, 0, card_w, card_h), radius=CARD_RADIUS, fill=255)
    hole_full.paste(hole_card, (card_x, video_y))
    alpha = img.split()[3]
    inv = Image.eval(hole_full, lambda v: 255 - v)
    new_alpha = ImageChops.multiply(alpha, inv.point(lambda v: 255 if v > 127 else 0))
    img.putalpha(new_alpha)

    return img, (card_x, video_y, card_w, card_h)


def _largura_texto(texto, font, draw, emoji_draw=None):
    if emoji_draw is not None:
        try:
            return emoji_draw.getsize(texto, font=font)[0]
        except Exception:
            pass
    bbox = draw.textbbox((0, 0), texto, font=font)
    return bbox[2] - bbox[0]


def wrap_text(text, font, max_w, draw, emoji_draw=None):
    linhas_finais = []
    texto = text.replace("\r\n", "\n").replace("\r", "\n")
    for paragrafo in texto.split("\n"):
        if paragrafo.strip() == "":
            linhas_finais.append("")
            continue
        cur = ""
        for w in paragrafo.split():
            test = (cur + " " + w).strip()
            if _largura_texto(test, font, draw, emoji_draw) <= max_w:
                cur = test
            else:
                if cur:
                    linhas_finais.append(cur)
                cur = w
        if cur:
            linhas_finais.append(cur)
    return linhas_finais


def make_post(video_path, caption, output_path, perfil=None, uniqueness=None):
    if perfil:
        set_perfil(perfil)
    return _gerar(video_path, caption, output_path, crop=None, uniqueness=uniqueness)


def make_post_from_crop(video_path, caption, output_path, crop, perfil=None, uniqueness=None):
    if perfil:
        set_perfil(perfil)
    return _gerar(video_path, caption, output_path, crop=crop, uniqueness=uniqueness)


def _gerar(video_path, caption, output_path, crop=None, uniqueness=None):
    """
    Gera o post com UMA única codificação de vídeo.

    Recorte, cor, grão, velocidade, redimensionamento e template são aplicados
    no mesmo filter_complex. Depois há somente uma passagem de stream copy para
    limpeza profunda, sem perda adicional de qualidade.
    """
    vw, vh = get_video_size(video_path)
    tem_audio = has_audio(video_path)
    opcoes = _normalizar_opcoes_uniqueness(uniqueness)

    if crop is not None:
        cx0, cy0, cw0, ch0 = [int(round(v)) for v in crop]
        cx0 = max(0, min(cx0, vw - 2))
        cy0 = max(0, min(cy0, vh - 2))
        cw0 = max(2, min(cw0, vw - cx0))
        ch0 = max(2, min(ch0, vh - cy0))
        cw0 -= cw0 % 2
        ch0 -= ch0 % 2
        cw0 = max(2, cw0)
        ch0 = max(2, ch0)
        aspect = cw0 / ch0
    else:
        aspect = vw / vh

    card_w = CANVAS_W - 2 * MARGIN_X
    card_h = int(card_w / aspect)

    f_caption = _font(FONT_REG, 44)
    tmp_img = Image.new("RGB", (10, 10))
    tmp_draw = ImageDraw.Draw(tmp_img)
    lines = wrap_text(caption, f_caption, CANVAS_W - 2 * MARGIN_X, tmp_draw)
    caption_block_h = len(lines) * 58

    margem_seg = SAFE_MARGIN_Y
    altura_disp = CANVAS_H - 2 * margem_seg

    def altura_bloco(ch):
        return AVATAR_SIZE + GAP_HEADER_CAP + caption_block_h + GAP_CAP_VIDEO + ch

    if altura_bloco(card_h) > altura_disp:
        sobra = AVATAR_SIZE + GAP_HEADER_CAP + caption_block_h + GAP_CAP_VIDEO
        card_h = max(2, altura_disp - sobra)
        card_w = int(card_h * aspect)
        if card_w > CANVAS_W - 2 * MARGIN_X:
            card_w = CANVAS_W - 2 * MARGIN_X
            card_h = int(card_w / aspect)

    card_w -= card_w % 2
    card_h -= card_h % 2
    card_w = max(2, card_w)
    card_h = max(2, card_h)

    bloco_h = altura_bloco(card_h)
    header_y = max(margem_seg, (CANVAS_H - bloco_h) // 2)
    video_y = header_y + AVATAR_SIZE + GAP_HEADER_CAP + caption_block_h + GAP_CAP_VIDEO

    overlay, (cx, cy, cw, ch) = build_overlay(
        caption, card_w, card_h, video_y, header_y
    )

    # Todos os filtros visuais são acumulados aqui e executados uma única vez.
    # Edições extras só rodam se a chave "edicoes_extras" estiver ligada.
    video_filters = []
    if crop is not None:
        video_filters.append(f"crop={cw0}:{ch0}:{cx0}:{cy0}")

    edicoes = opcoes.get("edicoes_extras", False)

    if edicoes and opcoes["light_crop"]:
        crop_pct = random.uniform(0.01, 0.03)
        video_filters.append(
            f"crop=iw*(1-{crop_pct:.4f}):ih*(1-{crop_pct:.4f})"
        )

    do_flip = False
    if edicoes and opcoes.get("random_flip", True):
        video_filters.append("hflip")
        do_flip = True

    if edicoes and (opcoes["color_adjust"] or opcoes.get("stronger_visuals", True)):
        brightness = round(random.uniform(0.02, 0.06), 3)
        contrast = round(random.uniform(1.03, 1.10), 3)
        saturation = round(random.uniform(1.05, 1.18), 3)
        if do_flip:
            saturation = round(random.uniform(1.08, 1.22), 3)
        hue_shift = round(random.uniform(-6, 6), 1)
        video_filters.append(
            f"eq=brightness={brightness}:contrast={contrast}:saturation={saturation}"
        )
        if abs(hue_shift) > 0.5:
            video_filters.append(f"hue=h={hue_shift}")
        video_filters.append(
            "curves=all='0/0 0.25/0.22 0.5/0.52 0.75/0.78 1/1'"
        )

    if edicoes and (opcoes["subtle_grain"] or opcoes.get("stronger_visuals", True)):
        grain_strength = random.randint(6, 12)
        video_filters.append(f"noise=alls={grain_strength}:allf=t")

    if edicoes and opcoes.get("vignette", True):
        video_filters.append(
            f"vignette=angle=PI/{random.uniform(3.2, 4.8):.2f}:mode=forward"
        )

    if edicoes and opcoes.get("dynamic_zoom", True):
        zoom = round(random.uniform(1.04, 1.10), 3)
        video_filters.append(
            f"scale=iw*{zoom}:ih*{zoom},"
            f"crop=iw/{zoom}:ih/{zoom}:(iw-ow)/2:(ih-oh)/2"
        )

    speed = opcoes["speed_factor"]
    if abs(speed - 1.0) > 0.001:
        video_filters.append(f"setpts={1.0 / speed:.12f}*PTS")

    video_filters += [
        f"scale={cw}:{ch}:force_original_aspect_ratio=increase",
        f"crop={cw}:{ch}",
        "setsar=1",
    ]

    # ---- Logo: usa a logo do perfil atual, se a chave estiver ligada ----
    perfil_cfg = PERFIS.get(PERFIL_ATUAL, {})
    usar_logo = (
        opcoes.get("usar_logo", False)
        and perfil_cfg.get("logo")
        and os.path.exists(perfil_cfg["logo"])
    )
    logo_path_temp = None
    logo_w = 0
    logo_h = 0

    with tempfile.TemporaryDirectory() as td:
        overlay_path = os.path.join(td, "overlay.png")
        encoded_path = os.path.join(td, "post_encoded.mp4")
        overlay.save(overlay_path)

        inputs = ["-i", video_path, "-framerate", "30", "-loop", "1", "-i", overlay_path]
        # índice 0 = vídeo, 1 = template overlay

        if usar_logo:
            # Prepara a logo com tamanho e opacidade
            logo_src = Image.open(perfil_cfg["logo"]).convert("RGBA")
            target_w = int(perfil_cfg.get("logo_width", 130))
            ratio = target_w / logo_src.width
            target_h = max(1, int(logo_src.height * ratio))
            logo_src = logo_src.resize((target_w, target_h), Image.LANCZOS)

            # Aplica opacidade 25%
            opacity = float(perfil_cfg.get("logo_opacity", 0.25))
            alpha = logo_src.split()[3]
            alpha = alpha.point(lambda p: int(p * opacity))
            logo_src.putalpha(alpha)

            logo_path_temp = os.path.join(td, "logo.png")
            logo_src.save(logo_path_temp)
            logo_w, logo_h = logo_src.size
            inputs += ["-loop", "1", "-i", logo_path_temp]
            # índice 2 = logo

        # Monta o filter_complex
        # [0:v] → filtros → [v]
        # depois overlay da logo embaixo-esquerda do card (se houver)
        # depois coloca o card no fundo branco
        # depois o template por cima

        partes = [
            f"color=white:s={CANVAS_W}x{CANVAS_H}:r=30[bgc]",
            f"[0:v:0]{','.join(video_filters)}[v0]",
        ]

        if usar_logo:
            # logo no canto inferior esquerdo do card, com margem de 12px
            margin = 12
            partes.append(
                f"[2:v]format=rgba,scale={logo_w}:{logo_h}[logo];"
                f"[v0][logo]overlay={margin}:{ch - logo_h - margin}:shortest=1[v]"
            )
        else:
            partes.append("[v0]null[v]")

        partes += [
            f"[bgc][v]overlay={cx}:{cy}:shortest=1[based]",
            "[based][1:v:0]overlay=0:0:shortest=1[outv]",
        ]

        if tem_audio:
            if abs(speed - 1.0) > 0.001:
                partes.append(f"[0:a:0]{_atempo_filter(speed)}[outa]")
            else:
                partes.append("[0:a:0]anull[outa]")

        filter_complex = ";".join(partes)

        cmd = [
            "ffmpeg", "-y",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", "[outv]",
        ]

        if tem_audio:
            cmd += ["-map", "[outa]", "-c:a", "aac", "-b:a", "192k"]
        else:
            cmd += ["-an"]

        cmd += [
            "-c:v", "libx264",
            "-crf", str(opcoes["crf"]),
            "-preset", opcoes["preset"],
            "-pix_fmt", "yuv420p",
            "-r", "30",
            "-sn", "-dn",
            "-shortest",
        ]
        cmd += _metadata_clean_args()
        cmd += [
            "-fflags", "+bitexact",
            "-movflags", "+faststart",
            encoded_path,
        ]

        run(cmd)

        if opcoes["deep_metadata_clean"]:
            deep_clean_mp4(
                encoded_path,
                output_path,
                remove_sei=opcoes["remove_h264_sei"],
            )
        else:
            os.replace(encoded_path, output_path)

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
