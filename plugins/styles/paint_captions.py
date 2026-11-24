"""Keep captions inside the frame and match spoken words to animation cues."""

import textwrap
from difflib import SequenceMatcher

from PIL import Image, ImageDraw, ImageFont

from core.text_utils import fit_text, word_key


def word_starts(narration, audio):
    authored = [word_key(word) for word in narration.split()]
    recorded = [word_key(word.text) for word in audio.words]
    anchors = {-1: 0.0, len(authored): audio.duration_sec}
    for left, right, count in SequenceMatcher(a=authored, b=recorded, autojunk=False).get_matching_blocks():
        for offset in range(count):
            anchors[left + offset] = min(audio.duration_sec, max(0.0, audio.words[right + offset].start_sec))
    starts = [0.0] * len(authored)
    marks = sorted(anchors)
    for first, last in zip(marks, marks[1:]):
        start, end = anchors[first], anchors[last]
        for index in range(max(0, first), last):
            starts[index] = start + (end - start) * (index - first) / (last - first)
    starts[0] = 0.0 if not recorded else starts[0]
    return list(zip(authored, starts, strict=True))


def _bold_font():
    for candidate in ("DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"):
        try:
            ImageFont.truetype(candidate, 24)
        except OSError:
            continue
        return candidate
    return None


FONT = _bold_font()


def caption_layers(narration, highlight, words, width, height):
    vertical = height > width
    unit = min(width, height)
    font_size = max(10, round(unit * (0.050 if vertical else 0.053)))
    spacing = max(2, round(unit * 0.007))
    outline = max(1, round(unit * 0.0025))
    pad_x, pad_y = max(5, round(unit * 0.022)), max(4, round(unit * 0.013))
    accent = {word_key(word) for word in highlight.split()}
    pages = textwrap.wrap(narration, width=48 if vertical else 68, break_long_words=False, break_on_hyphens=False)
    at_word = 0
    layers = []
    for page in pages:
        layout = fit_text(page, (round(width * 0.84) - pad_x * 2, round(height * 0.17) - pad_y * 2),
                          font_path=FONT, max_size=font_size, min_size=max(6, font_size // 3),
                          stroke_width=outline, spacing=spacing)
        left, top, right, bottom = layout.bbox
        panel_width = round(right - left) + pad_x * 2
        panel_height = round(bottom - top) + pad_y * 2
        shadow = max(1, round(unit * 0.005))
        panel = Image.new("RGBA", (panel_width + shadow, panel_height + shadow))
        draw = ImageDraw.Draw(panel)
        curve = max(3, round(unit * 0.015))
        draw.rounded_rectangle((shadow, shadow, panel.width - 1, panel.height - 1), curve, fill="#211b2a88")
        draw.rounded_rectangle((0, 0, panel_width - 1, panel_height - 1), curve, fill="#302a32",
                               outline="#744f52", width=max(1, shadow // 3))
        draw.multiline_text((pad_x - left, pad_y - top), layout.text, font=layout.font, spacing=spacing,
                            fill="#fff9ea", stroke_width=outline, stroke_fill="#15131a")
        # Pillow uses the bottom of the A glyph as the reference for line spacing.
        step = layout.font.getbbox("A", stroke_width=outline)[3] + outline + spacing
        for index, line in enumerate(layout.text.split("\n")):
            cursor = 0
            for word in line.split():
                if word_key(word) in accent:
                    x = pad_x - left + layout.font.getlength(line[:cursor + len(word)]) - layout.font.getlength(word)
                    y = pad_y - top + index * step
                    draw.text((x, y), word, font=layout.font, fill="#ffdc76", stroke_width=outline,
                              stroke_fill="#15131a")
                cursor += len(word) + 1
        start = words[at_word][1] if at_word else 0.0
        at_word += len(page.split())
        position = ((width - panel.width) // 2,
                    round(height * (0.835 if vertical else 0.958)) - panel.height)
        layers.append((start, panel, position))
    return layers


def headline_layer(text, width, height):
    unit = min(width, height)
    scale = unit / 1080
    padding = max(5, round(24 * scale))
    spacing = max(2, round(4 * scale))
    layout = fit_text(text, (round(width * 0.84) - padding * 2, round(height * 0.26) - padding * 2),
                      font_path=FONT, max_size=max(10, round(77 * scale)), min_size=max(6, round(22 * scale)),
                      spacing=spacing)
    left, top, right, bottom = layout.bbox
    w, h = round(right - left) + 2 * padding, round(bottom - top) + 2 * padding
    outline = max(1, round(4 * scale))
    tile = Image.new("RGBA", (w + padding, h + padding))
    draw = ImageDraw.Draw(tile)
    points = [(padding // 3, padding // 3), (w, padding // 7), (w + padding // 4, h - padding // 5),
              (w - padding // 4, h + padding // 3), (padding // 3, h), (padding // 8, h // 2)]
    draw.polygon([(x + outline, y + outline * 2) for x, y in points], fill="#67434a64")
    draw.polygon(points, fill="#ffe6a3")
    draw.line([*points, points[0]], fill="#302a32", width=outline, joint="curve")
    draw.multiline_text((padding - left, padding - top), layout.text, fill="#382b35", font=layout.font,
                        align="center", spacing=spacing)
    position = ((width - tile.width) // 2, round(height * (0.11 if height > width else 0.07)))
    return tile, position
