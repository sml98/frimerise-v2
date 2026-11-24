"""Fit captions using the pixels occupied by the selected font and outline."""

import textwrap
import unicodedata
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont


def word_key(word):
    return "".join(letter for letter in unicodedata.normalize("NFKD", word.casefold()) if letter.isalnum())


@dataclass(frozen=True, slots=True)
class TextLayout:
    text: str
    font: ImageFont.FreeTypeFont
    bbox: tuple[float, float, float, float]


def _wrap(text, fits):
    lines = []
    for paragraph in textwrap.dedent(text).split("\n"):
        line = ""
        for word in paragraph.split():
            candidate = f"{line} {word}" if line else word
            if fits(candidate):
                line = candidate
                continue
            if line:
                lines.append(line)
                line = ""
            while not fits(word):
                count = 1
                if not fits(word[:count]):
                    raise ValueError("a glyph exceeds the available width")
                while count < len(word) and fits(word[:count + 1]):
                    count += 1
                lines.append(word[:count])
                word = word[count:]
            line = word
        lines.append(line)
    return "\n".join(lines)


def fit_text(text, box, *, font_path=None, max_size=64, min_size=12, stroke_width=0, spacing=4):
    """Return wrapped text, font and bbox. Draw at (x - left, y - top) to align the ink."""
    width, height = box
    draw = ImageDraw.Draw(Image.new("L", (1, 1)))
    for size in range(max_size, min_size - 1, -1):
        font = ImageFont.truetype(str(font_path), size) if font_path else ImageFont.load_default(size=size)

        def fits(line):
            left, _, right, _ = draw.textbbox((0, 0), line, font=font, stroke_width=stroke_width)
            return right - left <= width

        try:
            wrapped = _wrap(text, fits)
        except ValueError:
            continue
        bounds = draw.multiline_textbbox((0, 0), wrapped, font=font, stroke_width=stroke_width, spacing=spacing)
        if bounds[2] - bounds[0] <= width and bounds[3] - bounds[1] <= height:
            return TextLayout(wrapped, font, bounds)
    raise ValueError("Text does not fit within the box at the minimum font size")
