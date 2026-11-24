"""Paper stock, torn fringes, contact shadows, tape and overprinted red ink."""

from functools import lru_cache
from math import cos, pi, sin
from pathlib import Path

import numpy as np
from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont

from core.text_utils import fit_text

ASSETS = Path(__file__).resolve().parents[2] / "assets" / "cutout_paper"
FONTS = ASSETS / "fonts"
SERIF = FONTS / "IMFeENrm28P.ttf"
DISPLAY = FONTS / "BarlowCondensed-Bold.ttf"
CAPTION = FONTS / "BarlowCondensed-Medium.ttf"
TYPEWRITER = FONTS / "CourierPrime-Regular.ttf"
INK = "#2d2a22"
RED = "#9f352b"
IVORY = "#f7efd9"


@lru_cache(maxsize=48)
def _noise(size, seed, spacing):
    w, h = size
    rng = np.random.default_rng(seed)
    field = rng.integers(0, 256, (max(2, h // spacing), max(2, w // spacing)), dtype=np.uint8)
    return np.asarray(Image.fromarray(field).resize(size, Image.Resampling.BILINEAR), dtype=np.float32) / 255


def _fibers(size, seed):
    """Multiply ink and paper by coarse mottling, fine tooth and short fibers."""
    w, h = size
    rng = np.random.default_rng(seed)
    fiber = Image.new("L", size, 128)
    draw = ImageDraw.Draw(fiber)
    for _ in range(max(14, w * h // 640)):
        x, y = rng.integers((0, 0), (w, h))
        angle, length = rng.uniform(-.4, pi), rng.uniform(2, 10)
        draw.line((int(x), int(y), x + length * cos(angle), y + length * sin(angle)),
                  fill=int(rng.integers(101, 154)))
    return (.949 + .062 * _noise(size, seed, 27) + .027 * _noise(size, seed + 1, 6)
            + rng.normal(0, .013, (h, w)).astype(np.float32)
            + (np.asarray(fiber, dtype=np.float32) - 128) / 340)


def _multiply(image, factor):
    pixels = np.array(image)
    pixels[..., :3] = np.clip(pixels[..., :3] * factor[..., None], 0, 255).astype(np.uint8)
    return Image.fromarray(pixels)


@lru_cache(maxsize=12)
def tabletop(size, stock, seed):
    w, h = size
    base = "#d8b78b" if stock == "kraft" else "#e0d4b3"
    paper = Image.new("RGB", size, base)
    if stock == "newsprint":
        draw = ImageDraw.Draw(paper)
        # Columns of faint typeset texture leave the actual editorial copy distinct.
        rng = np.random.default_rng(seed + 3)
        font = ImageFont.truetype(str(TYPEWRITER), max(7, min(w, h) // 63))
        column = max(32, w // 4)
        lines = ("Correspondência de campo", "Relatório rural e agrícola", "Notas do oeste australiano",
                 "Registros e observações", "Colheitas e clima")
        for x in range(-column // 4, w, column):
            for y in range(-9, h, font.size + 5):
                draw.text((x, y), lines[int(rng.integers(len(lines)))], font=font, fill="#cbbea1")
            draw.line((x - 6, 0, x - 6, h), fill="#c7b99d")
    y, x = np.ogrid[-1:1:complex(0, h), -1:1:complex(0, w)]
    vignette = 1 - .115 * (x * x + y * y) / 2
    return _multiply(paper, _fibers(size, seed) * vignette).convert("RGBA")


def cutout(art, *, border=3, seed=11):
    """Finish a transparent piece with a 4–8 px white fiber edge at 2x output scale."""
    art = art.convert("RGBA")
    pad = max(7, 3 * border + 4)
    size = art.width + 2 * pad, art.height + 2 * pad
    body = Image.new("L", size)
    body.paste(art.getchannel("A"), (pad, pad))
    lo = body.filter(ImageFilter.MaxFilter(2 * max(1, border - 1) + 1))
    hi = body.filter(ImageFilter.MaxFilter(2 * (border + 1) + 1))
    fray = _noise(size, seed, 4) > .48
    edge = Image.fromarray(np.where(fray, np.asarray(hi), np.asarray(lo)).astype(np.uint8))
    soft = edge.filter(ImageFilter.GaussianBlur(max(1.3, border * 1.2)))
    shifted = Image.new("L", size)
    distance = max(2, round(border * 1.45))
    # 135 degrees clockwise from screen right casts the shadow down and left.
    shifted.paste(soft, (-distance, distance))
    shadow = Image.new("RGBA", size, "#392d22")
    shadow.putalpha(shifted.point(lambda alpha: round(alpha * .32)))
    white = Image.new("RGBA", size, "#faf4e7")
    white.putalpha(edge)
    shadow.alpha_composite(white)
    shadow.alpha_composite(_multiply(art, _fibers(art.size, seed)), (pad, pad))
    return shadow


def paper_card(size, fill, seed):
    w, h = size
    image = Image.new("RGBA", size)
    rng = np.random.default_rng(seed)
    hops = max(3, round(min(size) / 18))
    points = [(int(x), int(rng.integers(1, 5))) for x in np.linspace(2, w - 3, hops)]
    points += [(w - int(rng.integers(2, 5)), int(y)) for y in np.linspace(6, h - 7, hops)]
    points += [(int(x), h - int(rng.integers(2, 6))) for x in np.linspace(w - 3, 2, hops)]
    points += [(int(rng.integers(1, 5)), int(y)) for y in np.linspace(h - 7, 6, hops)]
    ImageDraw.Draw(image).polygon(points, fill=fill)
    return image


@lru_cache(maxsize=30)
def tape(width, angle, seed):
    width = max(12, width)
    image = paper_card((width, max(9, round(width * .28))), (234, 203, 134, 161), seed)
    d = ImageDraw.Draw(image)
    d.line((4, 4, width - 4, 5), fill=(255, 235, 184, 120))
    d.line((4, image.height - 4, width - 4, image.height - 4), fill=(176, 132, 76, 90))
    return _multiply(image, _fibers(image.size, seed)).rotate(angle, Image.Resampling.BICUBIC, expand=True)


@lru_cache(maxsize=24)
def stamp(text, width, seed):
    width = max(32, width)
    height = max(22, round(width * (.28 if len(text) > 6 else .46)))
    margin = max(2, width // 24)
    alpha = Image.new("L", (width, height))
    draw = ImageDraw.Draw(alpha)
    stroke = max(1, width // 67)
    draw.rectangle((2, 2, width - 3, height - 3), outline=238, width=stroke)
    draw.rectangle((stroke + 5, stroke + 5, width - stroke - 6, height - stroke - 6), outline=150)
    layout = fit_text(text.upper(), (width - 4 * margin, height - 2 * margin), font_path=DISPLAY,
                      max_size=max(6, round(height * .67)), min_size=4, spacing=1)
    l, t, r, b = layout.bbox
    draw.multiline_text(((width - r + l) / 2 - l, (height - b + t) / 2 - t), layout.text,
                        font=layout.font, fill=245, spacing=1, align="center")
    ink = Image.new("RGBA", alpha.size, ImageColor.getrgb(RED))
    rng = np.random.default_rng(seed)
    wear = np.where(rng.random((height, width)) > .084, .93 + .07 * _noise(alpha.size, seed, 5), .22)
    ink.putalpha(Image.fromarray((np.asarray(alpha) * wear).astype(np.uint8)))
    return ink
