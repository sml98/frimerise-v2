"""JSON-driven photo, film, newspaper and board compositions for the paper stage."""

import subprocess
from dataclasses import dataclass
from math import sin, tau
from pathlib import Path
from tempfile import TemporaryFile
from typing import Literal

from PIL import Image, ImageDraw, ImageOps
from pydantic import BaseModel, Field, model_validator

from core.color_grade import LutName, grade_photo, lut_filter, lut_name
from core.cutout_matting import matting_model, remove_background
from core.encoder import FFMPEG
from core.media_fetcher import fetch_image
from core.motion import ease_in_out_cubic, ease_out_back, visual_cues
from core.text_utils import fit_text

from .paper_fx import DISPLAY, INK, IVORY, RED, SERIF, TYPEWRITER, cutout, edge_textures, paper_card, stamp, tape_card_corner

class Photo(BaseModel):
    media_query: str = Field(default="", max_length=220)
    media_file: str | None = None
    media_source: Literal["auto", "wikimedia", "pixabay"] = "auto"
    media_orientation: Literal["horizontal", "vertical"] | None = None
    label: str = Field(default="", max_length=60)

    @model_validator(mode="after")
    def check_source(self):
        if not (self.media_query or self.media_file):
            raise ValueError("Cada foto requer media_query ou media_file")
        return self


class MagazineOptions(BaseModel):
    module: Literal["photo_cutout", "newspaper_clipping", "collage_board"] | None = None
    media_query: str = Field(default="", max_length=220)
    media_file: str | None = None
    media_source: Literal["auto", "wikimedia", "pixabay"] = "auto"
    media_orientation: Literal["horizontal", "vertical"] | None = None
    photo_label: str = Field(default="", max_length=60)
    photos: list[Photo] = Field(default_factory=list, max_length=3)
    publication: str = Field(default="", max_length=40)
    clipping_headline: str = Field(default="", max_length=72)
    clipping_text: str = Field(default="", max_length=180)
    highlight: str = Field(default="", max_length=40)
    board_stamp: str = Field(default="", max_length=24)
    lut: LutName | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_lut(cls, values):
        if isinstance(values, dict) and values.get("lut") is not None:
            return {**values, "lut": lut_name(values["lut"])}
        return values

    @model_validator(mode="after")
    def check_magazine(self):
        if self.module == "collage_board" and not 2 <= len(self.photos) <= 3:
            raise ValueError("collage_board requer duas ou três fotos")
        if self.module in {"photo_cutout", "newspaper_clipping"} and not (self.media_query or self.media_file):
            raise ValueError(f"{self.module} requer media_query ou media_file")
        if self.module == "newspaper_clipping" and not self.clipping_headline:
            raise ValueError("newspaper_clipping requer clipping_headline")
        return self


@dataclass(frozen=True)
class Piece:
    textures: tuple[Image.Image, ...]
    label: Image.Image | None


@dataclass(frozen=True)
class Magazine:
    bounds: tuple[int, int, int, int]
    pieces: tuple[Piece | None, ...]
    models: tuple[str, ...]
    lut: str | None
    clipping: Image.Image | None = None
    underline: tuple[tuple[int, int], ...] = ()
    board_stamp: Image.Image | None = None


def _copy(source: Path, lut, limit, seed):
    matte = remove_background(source)
    graded = grade_photo(matte, lut)
    with Image.open(graded) as image:
        rgba = image.convert("RGBA")
    # Crop to the silhouette here. The shared matte retains the original canvas for depth titles.
    rgba = ImageOps.contain(rgba.crop(rgba.getchannel("A").getbbox()), limit, Image.Resampling.LANCZOS)
    return cutout(rgba, border=max(2, round(min(limit) * .02)), seed=seed), matting_model(matte)


def _tag(text, width, seed):
    if not text:
        return None
    pad = max(4, width // 18)
    fit = fit_text(text, (width - 2 * pad, max(16, width // 3)), font_path=TYPEWRITER,
                   max_size=max(10, width // 10), min_size=6, spacing=1)
    l, t, r, b = fit.bbox
    sheet = paper_card((max(18, r - l + 2 * pad), max(15, b - t + 2 * pad)), IVORY, seed)
    ImageDraw.Draw(sheet).multiline_text((pad - l, pad - t), fit.text, font=fit.font, fill=INK, spacing=1)
    return sheet


def _newspaper(options, width, height, seed):
    paper = paper_card((width, height), "#e8d9ae", seed)
    draw = ImageDraw.Draw(paper)
    pad = max(6, round(width * .072))
    span = width - 2 * pad
    rule_y = round(height * .22)
    draw.line((pad, rule_y, width - pad, rule_y), fill=INK, width=1)

    def text(value, face, top, room, font_size):
        if not value:
            return None
        fit = fit_text(value, (span, max(8, room)), font_path=face,
                       max_size=max(8, font_size), min_size=5, spacing=2)
        l, t, r, b = fit.bbox
        draw.multiline_text((pad - l, top - t), fit.text, font=fit.font, fill=INK, spacing=2)
        return r - l

    text(options.publication.upper(), TYPEWRITER, pad, height // 8, width // 12)
    text(options.clipping_headline, SERIF, rule_y + pad, round(height * .31), width // 6)
    text(options.clipping_text, SERIF, round(height * .59), round(height * .23), width // 12)
    baseline = round(height * .85)
    length = text(options.highlight.upper(), DISPLAY, baseline, round(height * .10), width // 11)
    edge = cutout(paper, border=2, seed=seed)
    inset = (edge.width - paper.width) // 2
    underline = tuple((inset + pad + round(length * fraction), inset + baseline + round(height * y))
                      for fraction, y in ((0, .101), (.27, .107), (.55, .096), (.82, .105), (1, .091))) if length else ()
    return edge, underline


def prepare_magazine(stage, options, seed, narration=""):
    w, h = stage.size
    bounds = stage.action_inset or (round(w * .10), round(h * .23), round(w * .90), round(h * .76))
    width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
    mode = options.module
    lut = options.lut or "vintage_paper_warm"
    hero_limit = (.94, .99) if stage.vertical else (.79, .84)
    multipliers = {"photo_cutout": hero_limit, "newspaper_clipping": (.53, .69), "collage_board": (.41, .48)}
    lim = tuple(max(8, round(size * factor)) for size, factor in zip((width, height), multipliers[mode], strict=True))
    lookups = options.photos if mode == "collage_board" else [Photo(
        media_query=options.media_query, media_file=options.media_file, media_source=options.media_source,
        media_orientation=options.media_orientation, label=options.photo_label,
    )]
    if mode == "collage_board" and options.media_file:
        lookups = [lookups[0].model_copy(update={"media_file": options.media_file}), *lookups[1:]]
    pieces, models = [], []
    for index, lookup in enumerate(lookups):
        source = fetch_image(lookup.media_query, lookup.media_source, lookup.media_orientation,
                             **({"media_file": lookup.media_file, "visual": options.headline, "narration": narration}
                                if lookup.media_file else {}))
        if source is None:
            pieces.append(None)
            continue
        art, model = _copy(source, lut, lim, seed + index)
        label = _tag(lookup.label, max(20, round(width * .37)), seed + index)
        pieces.append(Piece(edge_textures(art, seed + index), label))
        models.append(model)
    clipping, underline = (_newspaper(options, max(30, round(width * .55)), max(48, round(height * .88)), seed)
                           if mode == "newspaper_clipping" else (None, ()))
    badge = stamp(options.board_stamp, max(20, round(width * .38)), seed + 9) if options.board_stamp else None
    return Magazine(bounds, tuple(pieces), tuple(dict.fromkeys(models)), lut if models else None, clipping, underline, badge)


def film_cards(path, stage, timing, lut, seed):
    """Decode one graded, mounted film frame per output frame with bounded memory."""
    if stage.action_inset:
        left, top, right, bottom = stage.action_inset
        width, height = round((right - left) * .86), round((bottom - top) * .65)
    else:
        width, height = round(stage.size[0] * .66), round(stage.size[1] * .53)
    pad = max(4, stage.unit // 55)
    stock = paper_card((width + 2 * pad, height + 2 * pad), IVORY, seed)
    mat = cutout(stock, border=max(2, pad // 3), seed=seed)
    offset = ((mat.width - width) // 2, (mat.height - height) // 2)
    picture = (f"fps={timing.fps},scale={width}:{height}:force_original_aspect_ratio=increase,"
               f"crop={width}:{height},{lut_filter(lut)}")
    command = [*FFMPEG, "-v", "error", "-stream_loop", "-1", "-threads", "2", "-i", str(path),
               "-an", "-filter_threads", "1", "-vf", picture, "-frames:v", str(timing.frame_count),
               "-threads", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]
    count = width * height * 3
    with TemporaryFile() as errors, subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors) as process:
        for _ in range(timing.frame_count):
            data = process.stdout.read(count)
            if len(data) != count:
                errors.seek(0)
                raise RuntimeError(f"Quadro de media_file incompleto: {errors.read().decode(errors='replace')}")
            card = mat.copy()
            card.paste(Image.frombytes("RGB", (width, height), data), offset)
            yield card
        if process.wait():
            errors.seek(0)
            raise RuntimeError(errors.read().decode(errors="replace"))


def _arrive(at, start, span=.65):
    return ease_out_back((at - start) / span)


def _arrow(draw, a, b, p, unit):
    tip = (a[0] + p * (b[0] - a[0]), a[1] + p * (b[1] - a[1]))
    draw.line((a, tip), fill=RED, width=max(1, unit // 112))
    if p >= .88:
        dx, dy = (b[0] - a[0]) * .12, (b[1] - a[1]) * .12
        draw.line(((tip[0] - dx + dy, tip[1] - dy - dx), tip, (tip[0] - dx - dy, tip[1] - dy + dx)),
                  fill=RED, width=max(1, unit // 112))


def draw_magazine(layer, magazine, mode, at, duration, place):
    left, top, right, bottom = magazine.bounds
    width, height = right - left, bottom - top

    def xy(x, y):
        return left + width * x, top + height * y

    cues = visual_cues(duration)
    emphasis = cues[0] if cues else duration * .32
    secondary = cues[1] if len(cues) > 1 else duration * .59
    clipping_bounds = None
    if magazine.clipping is not None:
        news = magazine.clipping.copy()
        segments = max(0, len(magazine.underline) - 1)
        span = ease_in_out_cubic((at - emphasis) / .73) * segments
        count = int(span)
        strokes = magazine.underline[:count + 1]
        if count < segments:
            first, end = magazine.underline[count:count + 2]
            strokes += (tuple(a + (b - a) * (span - count) for a, b in zip(first, end, strict=True)),)
        if span > 0:
            draw = ImageDraw.Draw(news)
            draw.line([(x, y - 2) for x, y in strokes], fill="#d89b55", width=max(3, width // 48))
            draw.line(strokes, fill=RED, width=max(2, width // 115))
        center, scale = place(layer, news, xy(.28, .49), angle=3,
                              scale=.97 + .03 * _arrive(at, 0), bounds=magazine.bounds)
        dx, dy = news.width * scale * .41, news.height * scale * .43
        clipping_bounds = (round(center[0] - dx), round(center[1] - dy), round(center[0] + dx), round(center[1] + dy))

    if mode == "photo_cutout":
        slots = ((.50, .45, -4),)
    elif mode == "newspaper_clipping":
        slots = ((.72, .53, -6),)
    elif len(magazine.pieces) == 2:
        slots = ((.25, .36, 6), (.74, .59, -7))
    else:
        slots = ((.24, .37, 6), (.76, .31, -8), (.53, .71, 3))

    silhouettes = Image.new("RGBA", layer.size)
    labels = []
    for index, ((x, y, lean), piece) in enumerate(zip(slots, magazine.pieces, strict=True)):
        start = -.20 if not index else (emphasis if index == 1 else secondary)
        if piece is None or at < start:
            continue
        arrive = _arrive(at, start)
        if index and magazine.pieces[index - 1] is not None:
            last_x, last_y, _ = slots[index - 1]
            _arrow(ImageDraw.Draw(layer), xy((last_x * 2 + x) / 3, last_y),
                   xy((last_x + x * 2) / 3, y), ease_in_out_cubic((at - start) / .65), width)
        sway = sin(at * tau * .68 + index)
        art = piece.textures[int(at * 12) % len(piece.textures)]
        center, shrink = place(silhouettes, art,
                               xy(x + .11 * (1 - arrive), y + .055 * (1 - arrive) + sway * .011),
                               angle=lean + sway * 1.5, scale=.64 + .36 * arrive, bounds=magazine.bounds)
        if piece.label and (mode != "photo_cutout" or at >= secondary):
            labels.append((piece.label, (center[0], center[1] + art.height * shrink * .48), lean / 2))
    if clipping_bounds is not None:
        blocked = silhouettes.getbbox()
        tape_card_corner(layer, clipping_bounds, max(12, width // 10), blocked=(blocked,) if blocked else (), seed=21)
    layer.alpha_composite(silhouettes)
    for label, center, angle in labels:
        place(layer, label, center, angle=angle, bounds=magazine.bounds)
    if magazine.board_stamp and at > secondary:
        pop = _arrive(at, secondary, .35)
        place(layer, magazine.board_stamp, xy(.79, .86), angle=-6, scale=1.16 - .16 * pop, bounds=magazine.bounds)
