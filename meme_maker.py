#!/usr/bin/env python3
"""
Gerador de posts estilo tweet para a pagina de meme.
"""

import os
import sys
import json
import subprocess
import tempfile
from PIL import Image, ImageDraw, ImageFont, ImageChops

# ----------------- CONFIGURACOES FIXAS DO TEMPLATE -----------------
CANVAS_W = 1080
CANVAS_H = 1920
BG_COLOR = (255, 255, 255)

_BASE = os.path.dirname(os.path.abspath(__file__))

# ----------------- PERFIS DISPONIVEIS -----------------
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

PROFILE_NAME = PERFIS[PERFIL_PADRAO]["nome"]
PROFILE_HANDLE = PERFIS[PERFIL_PADRAO]["handle"]
AVATAR_PATH = PERFIS[PERFIL_PADRAO]["avatar"]


def set_perfil(chave):
    global PROFILE_NAME, PROFILE_HANDLE, AVATAR_PATH
    p = PERFIS.get(chave) or PERFIS[PERFIL_PADRAO]
    PROFILE_NAME = p["nome"]
    PROFILE_HANDLE = p["handle"]
    AVATAR_PATH = p["avatar"]


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


COLOR_NAME = (15, 20, 25)
COLOR_HANDLE = (83, 100, 113)
COLOR_CAPTION = (15, 20, 25)

MARGIN_X = 90
HEADER_Y = 560
AVATAR_SIZE = 110
CARD_RADIUS = 28


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


# ====================== ANTI-DETECÇÃO (QUALIDADE VISUAL) ======================
def apply_uniqueness_filters(input_path, output_path, options=None):
    if options is None:
        options = {}

    filters = []
    audio_filters = []

    if options.get("light_crop", True):
        filters.append("crop=iw-4:ih-4")

    if options.get("color_adjust", True):
        filters.append("eq=brightness=0.012:saturation=1.018:contrast=1.008")

    if options.get("subtle_grain", True):
        filters.append("noise=alls=4:allf=t")

    speed = options.get("speed_factor", 1.01)
    if abs(speed - 1.0) > 0.001:
        filters.append(f"setpts={1/speed}*PTS")
        audio_filters.append(f"atempo={speed}")

    # === FADE MAIS SUAVE E RÁPIDO ===
    if options.get("fade", True):
        dur = get_duration(input_path)
        fade_dur = 0.15          # reduzido de 0.3 para 0.15
        fade_out_start = max(0, dur - fade_dur)
        filters.append(f"fade=t=in:st=0:d={fade_dur},fade=t=out:st={fade_out_start:.3f}:d={fade_dur}")

    crf = options.get("crf", 20)
    preset = options.get("preset", "slow")

    cmd = ["ffmpeg", "-y", "-i", input_path]

    if filters:
        cmd += ["-vf", ",".join(filters)]
    if audio_filters:
        cmd += ["-af", ",".join(audio_filters)]

    cmd += [
        "-c:v", "libx264",
        "-crf", str(crf),
        "-preset", preset,
        "-pix_fmt", "yuv420p",
        "-map_metadata", "-1",
        "-movflags", "+faststart"
    ]

    if has_audio(input_path):
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-an"]

    cmd.append(output_path)
    run(cmd)
    return output_path


# ====================== RESTO DO CÓDIGO (mantido limpo) ======================
def build_overlay(caption, video_disp_w, video_disp_h, video_y, header_y):
    img = Image.new("RGBA", (CANVAS_W, CANVAS_H), BG_COLOR + (255,))
    draw = ImageDraw.Draw(img)

    f_name = _font(FONT_BOLD, 40)
    f_handle = _font(FONT_REG, 36)
    f_caption = _font(FONT_REG, 44)

    if os.path.exists(AVATAR_PATH):
        av = Image.open(AVATAR_PATH).convert("RGBA").resize((AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS)
        escala = 4
        mascara_g = Image.new("L", (AVATAR_SIZE * escala, AVATAR_SIZE * escala), 0)
        ImageDraw.Draw(mascara_g).ellipse((0, 0, AVATAR_SIZE * escala, AVATAR_SIZE * escala), fill=255)
        mascara = mascara_g.resize((AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS)
        alpha_atual = av.split()[3]
        nova_alpha = ImageChops.multiply(alpha_atual, mascara)
        av.putalpha(nova_alpha)
        img.paste(av, (MARGIN_X, header_y), av)

    text_x = MARGIN_X + AVATAR_SIZE + 28
    draw.text((text_x, header_y + 12), PROFILE_NAME, font=f_name, fill=COLOR_NAME)
    draw.text((text_x, header_y + 62), PROFILE_HANDLE, font=f_handle, fill=COLOR_HANDLE)

    caption_y = header_y + AVATAR_SIZE + 50
    max_w = CANVAS_W - 2 * MARGIN_X
    lines = wrap_text(caption, f_caption, max_w, draw)
    line_h = 58
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


def wrap_text(text, font, max_w, draw):
    linhas_finais = []
    texto = text.replace("\r\n", "\n").replace("\r", "\n")
    for paragrafo in texto.split("\n"):
        if paragrafo.strip() == "":
            linhas_finais.append("")
            continue
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


def make_post(video_path, caption, output_path, perfil=None, uniqueness=None):
    if perfil:
        set_perfil(perfil)
    return _gerar(video_path, caption, output_path, crop=None, uniqueness=uniqueness)


def make_post_from_crop(video_path, caption, output_path, crop, perfil=None, uniqueness=None):
    if perfil:
        set_perfil(perfil)
    return _gerar(video_path, caption, output_path, crop=crop, uniqueness=uniqueness)


def _gerar(video_path, caption, output_path, crop=None, uniqueness=None):
    vw, vh = get_video_size(video_path)
    
    if crop is not None:
        cx0, cy0, cw0, ch0 = [int(round(v)) for v in crop]
        cx0 = max(0, min(cx0, vw - 2))
        cy0 = max(0, min(cy0, vh - 2))
        cw0 = max(2, min(cw0, vw - cx0))
        ch0 = max(2, min(ch0, vh - cy0))
        cw0 -= cw0 % 2
        ch0 -= ch0 % 2
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

    GAP_HEADER_CAP = 50
    GAP_CAP_VIDEO = 40
    margem_seg = 80
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

    overlay, (cx, cy, cw, ch) = build_overlay(caption, card_w, card_h, video_y, header_y)

    with tempfile.TemporaryDirectory() as td:
        overlay_path = os.path.join(td, "overlay.png")
        overlay.save(overlay_path)

        source_video = video_path
        if uniqueness:
            temp_video = os.path.join(td, "uniquified.mp4")
            apply_uniqueness_filters(video_path, temp_video, uniqueness)
            source_video = temp_video

        if crop is not None:
            crop_prefix = f"crop={cw0}:{ch0}:{cx0}:{cy0},"
        else:
            crop_prefix = ""

        filter_complex = (
            f"color=white:s={CANVAS_W}x{CANVAS_H}:r=30[bgc];"
            f"[0:v]{crop_prefix}scale={cw}:{ch}:force_original_aspect_ratio=increase,"
            f"crop={cw}:{ch},setsar=1[v];"
            f"[bgc][v]overlay={cx}:{cy}:shortest=1[based];"
            f"[based][1:v]overlay=0:0[outv]"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", source_video,
            "-framerate", "30", "-loop", "1", "-t", str(get_duration(source_video)), "-i", overlay_path,
            "-filter_complex", filter_complex,
            "-map", "[outv]",
        ]

        if has_audio(source_video):
            cmd += ["-map", "0:a", "-c:a", "aac", "-b:a", "192k"]

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
