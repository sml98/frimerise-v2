"""Handwritten paper captions and the strip-projected page turn from Lemo-Opuscar."""

import textwrap
from dataclasses import dataclass
from functools import lru_cache
from math import ceil, copysign, cos, floor, pi, sin, tau
from pathlib import Path
from random import Random

import numpy as np
from PIL import Image, ImageColor, ImageDraw, ImageOps

from core.text_utils import fit_text
from plugins.styles.crayon_materials import BROWN, INDIGO, smoothstep, stock

FONT = Path(__file__).resolve().parents[2] / "assets" / "crayon_book" / "fonts" / "PatrickHand-Regular.ttf"


def mobile_safe_box(size):
    """Intersection of the HyperFrames Reels and Shorts safe areas."""
    width, height = size
    return (ceil(width * .06), ceil(height * .15), floor(width * 888 / 1080), floor(height * .65))


@dataclass(frozen=True)
class Letter:
    mask: Image.Image
    position: tuple[int, int]


@dataclass(frozen=True)
class Writing:
    paper: Image.Image
    position: tuple[int, int]
    letters: tuple[Letter, ...]
    ink: str
    start: float
    seconds: float

    def draw(self, frame, at, step):
        progress = max(0, min(len(self.letters), (at - self.start) / self.seconds * len(self.letters) + .15))
        panel = _paper(self.paper.size).copy()
        shift = step % 3 - 1
        for index in range(ceil(progress)):
            letter = self.letters[index]
            x, y = letter.position
            mask = letter.mask
            if progress - index < 1:
                mask = mask.crop((0, 0, round(mask.width * (progress - index)), mask.height))
            panel.paste(self.ink, (x, y + (shift if index % 3 == 0 else 0)), mask)
        frame.paste(panel, self.position, self.paper)


def _writing(text, size, start, seconds, *, heading=False):
    width, height = size
    unit = min(size)
    font_size = max(8, round(unit * (.110 if heading else .061)))
    spacing = max(1, round(unit * .006))
    pad_x, pad_y = max(3, round(unit * .045)), max(3, round(unit * .022))
    portrait = height > width
    if portrait:
        safe_left, safe_top, safe_right, safe_bottom = mobile_safe_box(size)
        available_w = safe_right - safe_left
        available_h = round(height * (.132 if heading else .15))
    else:
        available_w = round(width * (.69 if heading else .88))
        available_h = round(height * (.31 if heading else .23))
    box = (max(1, available_w - 2 * pad_x), max(1, available_h - 2 * pad_y))
    layout = fit_text(text, box, font_path=FONT, max_size=font_size, min_size=max(3, font_size // 3), spacing=spacing)
    left, top, right, bottom = layout.bbox
    panel_w, panel_h = round(right - left) + 2 * pad_x, round(bottom - top) + 2 * pad_y
    paper = Image.new("L", (panel_w, panel_h))
    center_x, center_y = (panel_w - 1) / 2, (panel_h - 1) / 2
    edge = []
    for index in range(96):
        angle = tau * index / 96
        taper = 1 + .045 * sin(angle * 7 + 1.3) + .024 * sin(angle * 17)
        x, y = cos(angle), sin(angle)
        edge.append((center_x + .952 * center_x * copysign(abs(x) ** .28, x) * taper,
                     center_y + .935 * center_y * y * taper))
    ImageDraw.Draw(paper).polygon(edge, fill=255)

    letters = []
    rng = Random(text)
    line_step = layout.font.getbbox("A")[3] + spacing
    for line_index, line in enumerate(layout.text.splitlines()):
        offset = (right - left - layout.font.getlength(line)) / 2
        for index, glyph in enumerate(line):
            if glyph.isspace():
                continue
            ink_left, ink_top, ink_right, ink_bottom = layout.font.getbbox(glyph)
            padding = 3
            tile = Image.new("L", (ink_right - ink_left + 2 * padding, ink_bottom - ink_top + 2 * padding))
            ImageDraw.Draw(tile).text((padding - ink_left, padding - ink_top), glyph, fill=255, font=layout.font)
            rotated = tile.rotate(rng.uniform(-3, 3), Image.Resampling.BICUBIC)
            x = pad_x + offset + layout.font.getlength(line[:index]) + ink_left - padding
            y = pad_y - top + line_index * line_step + ink_top - padding + rng.uniform(-.8, .8)
            letters.append(Letter(rotated, (round(x), round(y))))
    if portrait:
        position = (safe_left if heading else safe_left + (available_w - panel_w) // 2,
                    safe_top if heading else safe_bottom - panel_h)
    else:
        position = (round(width * .047) if heading else (width - panel_w) // 2,
                    round(height * .057) if heading else round(height * .963) - panel_h)
    return Writing(paper, position, tuple(letters), BROWN if heading else INDIGO, start, seconds)


def captions(narration, words, size, duration):
    pages = textwrap.wrap(narration, width=76 if size[1] > size[0] else 72,
                          break_long_words=False, break_on_hyphens=False)
    starts, offset = [], 0
    for page in pages:
        starts.append(words[offset][1] if offset else 0.0)
        offset += len(page.split())
    ends = [*starts[1:], duration]
    return tuple(_writing(page, size, at, max(.08, min(.76, len(page) * .024, (end - at) * .65)))
                 for page, at, end in zip(pages, starts, ends, strict=True))


def aligned_caption(text, size):
    return _writing(text, size, 0, 1)


def title(text, size):
    return _writing(text, size, 0, .74, heading=True)


@lru_cache(maxsize=4)
def _paper(size):
    return Image.fromarray(np.clip(stock(size).paper + (6, 6, 7), 0, 255).astype(np.uint8))


def page_turn(outgoing, incoming, progress):
    """Project a bending sheet onto its successor, with a warm, translucent verso."""
    if progress <= 0:
        return outgoing
    if progress >= 1:
        return incoming
    width, height = outgoing.size
    theta = pi * float(smoothstep(0, 1, progress))
    depth = 2.7 * width
    # Let the book's gutter travel inward as the sheet lifts, keeping a glimpse of its back.
    gutter = width * .12 * sin(theta) ** 2

    def project(u):
        angle = theta - .5 * sin(theta) * u ** 1.6
        lift = u * width * sin(angle)
        scale = depth / (depth - lift)
        return (u * width * cos(angle) - width / 2) * scale + width / 2 + gutter, scale

    result = incoming.copy()
    edge = max(project(index / 48)[0] for index in range(49))
    shade_width = max(1, round(width * .042 * sin(theta)))
    left, right = max(0, floor(edge) - shade_width), min(width, ceil(edge) + shade_width)
    if left < right:
        shadow = Image.new("L", (right - left, 1))
        shadow.putdata([round(98 * sin(theta) * max(0, 1 - abs(x - edge) / shade_width) ** 2)
                        for x in range(left, right)])
        result.paste(ImageColor.getrgb(BROWN), (left, 0, right, height), shadow.resize((right - left, height)))

    front = outgoing.convert("RGBA")
    verso = Image.blend(_paper(outgoing.size), ImageOps.mirror(outgoing), .075).convert("RGBA")
    for index in range(48):
        x0, scale0 = project(index / 48)
        x1, scale1 = project((index + 1) / 48)
        delta = x1 - x0
        if abs(delta) < .12:
            continue
        left, right = max(0, floor(min(x0, x1))), min(width, ceil(max(x0, x1)))
        if left >= right:
            continue
        # Invert the projection across each narrow strip, using its middle scale.
        dx = width / 48 / delta
        mid_scale = (scale0 + scale1) / 2
        top = height / 2 * (1 - scale0)
        slope = height / 2 * (scale0 - scale1) / delta
        start_x = index * width / 48 + (left - x0) * dx
        source = front if delta > 0 else verso
        if delta < 0:
            start_x, dx = width - start_x, -dx
        strip = source.transform((right - left, height), Image.Transform.AFFINE,
                                 (dx, 0, start_x, -slope / mid_scale, 1 / mid_scale,
                                  (-top - slope * (left - x0)) / mid_scale),
                                 Image.Resampling.BILINEAR)
        shade = (.12 if delta > 0 else .23) * sin(theta) * (.55 + .45 * index / 47)
        toned = Image.blend(strip.convert("RGB"), Image.new("RGB", strip.size, BROWN), shade)
        result.paste(toned, (left, 0), strip)
    return result
