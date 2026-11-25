"""JSON-driven photo, newspaper and board compositions for the paper stage."""

from dataclasses import dataclass
from math import sin, tau
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageOps
from pydantic import BaseModel, Field, model_validator

from core.color_grade import LutName, grade_photo, lut_name
from core.cutout_matting import matting_model, remove_background
from core.media_fetcher import fetch_image
from core.text_utils import fit_text

from .paper_fx import DISPLAY, INK, IVORY, RED, SERIF, TYPEWRITER, cutout, paper_card, stamp, tape

class Photo(BaseModel):
    media_query: str = Field(min_length=1, max_length=220)
    media_source: Literal["auto", "wikimedia", "pixabay"] = "auto"
    media_orientation: Literal["horizontal", "vertical"] | None = None
    label: str = Field(default="", max_length=60)


class MagazineOptions(BaseModel):
    module: Literal["photo_cutout", "newspaper_clipping", "collage_board"] | None = None
    media_query: str = Field(default="", max_length=220)
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
        if self.module in {"photo_cutout", "newspaper_clipping"} and not self.media_query:
            raise ValueError(f"{self.module} requer media_query")
        if self.module == "newspaper_clipping" and not self.clipping_headline:
            raise ValueError("newspaper_clipping requer clipping_headline")
        return self


@dataclass(frozen=True)
class Piece:
    art: Image.Image
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


def prepare_magazine(stage, options, seed):
    w, h = stage.size
    bounds = stage.action_inset or (round(w * .10), round(h * .23), round(w * .90), round(h * .76))
    width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
    mode = options.module
    lut = options.lut or "vintage_paper_warm"
    multipliers = {"photo_cutout": (.79, .84), "newspaper_clipping": (.53, .69), "collage_board": (.41, .48)}
    lim = tuple(max(8, round(size * factor)) for size, factor in zip((width, height), multipliers[mode], strict=True))
    lookups = options.photos if mode == "collage_board" else [Photo(
        media_query=options.media_query, media_source=options.media_source,
        media_orientation=options.media_orientation, label=options.photo_label,
    )]
    pieces, models = [], []
    for index, lookup in enumerate(lookups):
        source = fetch_image(lookup.media_query, lookup.media_source, lookup.media_orientation)
        if source is None:
            pieces.append(None)
            continue
        art, model = _copy(source, lut, lim, seed + index)
        label = _tag(lookup.label, max(20, round(width * .37)), seed + index)
        pieces.append(Piece(art, label))
        models.append(model)
    clipping, underline = (_newspaper(options, max(30, round(width * .55)), max(48, round(height * .88)), seed)
                           if mode == "newspaper_clipping" else (None, ()))
    badge = stamp(options.board_stamp, max(20, round(width * .38)), seed + 9) if options.board_stamp else None
    return Magazine(bounds, tuple(pieces), tuple(dict.fromkeys(models)), lut if models else None, clipping, underline, badge)


def _arrive(at, start, span=.19):
    p = min(1, max(0, (at - start) / span))
    return 1 - (1 - p) ** 3


def _arrow(draw, a, b, p, unit):
    tip = (a[0] + p * (b[0] - a[0]), a[1] + p * (b[1] - a[1]))
    draw.line((a, tip), fill=RED, width=max(1, unit // 112))
    if p >= .88:
        dx, dy = (b[0] - a[0]) * .12, (b[1] - a[1]) * .12
        draw.line(((tip[0] - dx + dy, tip[1] - dy - dx), tip, (tip[0] - dx - dy, tip[1] - dy + dx)),
                  fill=RED, width=max(1, unit // 112))


def draw_magazine(layer, magazine, mode, progress, beat, place):
    left, top, right, bottom = magazine.bounds
    width, height = right - left, bottom - top

    def xy(x, y):
        return left + width * x, top + height * y

    if magazine.clipping is not None:
        news = magazine.clipping.copy()
        strokes = magazine.underline[:1 + int(_arrive(progress, .25, .23) * (len(magazine.underline) - 1))]
        if len(strokes) > 1:
            draw = ImageDraw.Draw(news)
            draw.line([(x, y - 2) for x, y in strokes], fill="#d89b55", width=max(3, width // 48))
            draw.line(strokes, fill=RED, width=max(2, width // 115))
        place(layer, news, xy(.28, .49), angle=3, bounds=magazine.bounds)

    if mode == "photo_cutout":
        slots = ((.50, .45, -4),)
    elif mode == "newspaper_clipping":
        slots = ((.72, .53, -6),)
    elif len(magazine.pieces) == 2:
        slots = ((.25, .36, 6), (.74, .59, -7))
    else:
        slots = ((.24, .37, 6), (.76, .31, -8), (.53, .71, 3))

    for index, ((x, y, lean), piece) in enumerate(zip(slots, magazine.pieces, strict=True)):
        start = .055 + index * .17
        arrive = _arrive(progress, start)
        if piece is None or arrive == 0:
            continue
        if index and magazine.pieces[index - 1] is not None:
            last_x, last_y, _ = slots[index - 1]
            _arrow(ImageDraw.Draw(layer), xy((last_x * 2 + x) / 3, last_y),
                   xy((last_x + x * 2) / 3, y), _arrive(progress, start - .06, .11), width)
        sway = sin(beat / 12 * tau * .68 + index)
        center, shrink = place(layer, piece.art, xy(x + .065 * (1 - arrive), y + .045 * (1 - arrive) + sway * .008),
                               angle=lean + sway * 1.25, scale=.84 + .16 * arrive, bounds=magazine.bounds)
        tape_width = max(14, round(piece.art.width * shrink * .30))
        place(layer, tape(tape_width, -12 + index * 8, 21 + index),
              (center[0], center[1] - piece.art.height * shrink * .40), bounds=magazine.bounds)
        if piece.label:
            place(layer, piece.label, (center[0], center[1] + piece.art.height * shrink * .48),
                  angle=lean / 2, bounds=magazine.bounds)
    if magazine.board_stamp and progress > .53:
        pop = _arrive(progress, .53, .11)
        place(layer, magazine.board_stamp, xy(.79, .86), angle=-6, scale=1.16 - .16 * pop, bounds=magazine.bounds)
