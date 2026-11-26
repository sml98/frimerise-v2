"""Render paper rigs and the three-plane camera at presentation fps."""

import re
from bisect import bisect_right
from dataclasses import dataclass
from functools import lru_cache
from math import ceil, cos, floor, hypot, sin, tau
from pathlib import Path
from typing import Annotated, Literal
from zlib import crc32

from PIL import Image, ImageDraw
from pydantic import BaseModel, Field

from core.audio_master import safe_beat
from core.media_fetcher import fetch_broll, local_media_file, media_credit
from core.motion import camera_accents, ease_in_out_cubic, ease_out_back, visual_cues
from core.schema import SceneRender
from core.text_utils import fit_text
from plugins.styles.paint_captions import word_key, word_starts

from . import assets_procedural as art
from .foley import effects
from .magazine import MagazineOptions, draw_magazine, film_cards, prepare_magazine
from .paper_fx import CAPTION, INK, IVORY, RED, SERIF, TYPEWRITER, cutout, paper_card, stamp, tabletop

DRAW_FPS = 12
PARALLAX = (.30, 1.0, 1.20)
REFERENCE_SECONDS = 8.0
PORTRAIT_BANDS = {"header": (.07, .18), "stamp": (.19, .26), "action": (.26, .66), "caption": (.72, .81)}
DANGLING = {"a", "o", "as", "os", "de", "do", "da", "dos", "das", "em", "e", "um", "uma",
            "no", "na", "nos", "nas", "por", "para", "com", "ao", "aos", "que", "mas"}
Coordinate = Annotated[float, Field(ge=0, le=1)]
Foley = Literal["paper_slide", "paper_rustle", "paper_pop", "stamp_hit", "camera_click"]


class ExternalCutout(BaseModel):
    asset: str
    layer: Literal["bg", "mid", "fg"] = "fg"
    x: Coordinate = .5
    y: Coordinate = .46
    width: Annotated[float, Field(gt=0, le=1)] = .33
    motion: Literal["slide", "pop", "hold"] = "pop"
    delay_sec: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0
    rotation: Annotated[float, Field(ge=-180, le=180)] = 0


class Options(MagazineOptions):
    layout: Literal["map", "field", "expedition", "routes", "portrait", "custom"] = "custom"
    paper: Literal["kraft", "newsprint"] = "kraft"
    headline: str = ""
    show_header: bool = False
    label: str = ""
    camera: Literal["zoom_in_slow", "pan_right", "static_punch", "zoom_out_subtle", "zoom_in_end", "static"] = "static"
    stamp: str = ""
    stamp_at_sec: Annotated[float, Field(ge=0, allow_inf_nan=False)] = .8
    stamp_at_word: str | None = None
    stamp_at_progress: Coordinate | None = None
    foley: list[Foley] = Field(default_factory=lambda: ["paper_slide", "stamp_hit"])
    cutouts: list[ExternalCutout] = Field(default_factory=list)
    caption_numbers: dict[str, str] = Field(default_factory=dict)
    setting: Literal["graveyard_night", "village_road", "archive_desk", "open_field", "city_street"] = "graveyard_night"
    action: Literal["walk_to_target", "inspect_with_lantern", "dig_or_open", "flee_run", "crowd_gather"] = "walk_to_target"
    focus_x: Coordinate = .5
    focus_y: Coordinate = .5
    mark: Literal["circle", "underline"] = "circle"
    impact_text: str = ""
    hold_impact_through_sentence: bool = False
    lantern_focus: bool = False


def drawing_step(frame, fps):
    return frame * DRAW_FPS // fps


def impact_frame(seconds, fps, frame_count):
    return min(ceil(seconds * fps - 1e-9), frame_count - 1)


def stamp_time(options, timing):
    if timing.continuous:
        anchor = tuple(map(word_key, (options.stamp_at_word or "").split()))
        words = timing.audio.words
        if anchor:
            keys = tuple(word_key(word.text) for word in words)
            for i, word in enumerate(words):
                if keys[i:i + len(anchor)] == anchor:
                    return word.start_sec
    if options.stamp_at_progress is not None:
        return timing.duration_sec * options.stamp_at_progress
    return timing.authored_time(options.stamp_at_sec, reference=REFERENCE_SECONDS)


def camera_shake(index, impact, unit):
    pulse = ((-.011, .007), (.006, -.004))
    if impact is None or not 0 <= index - impact < len(pulse):
        return 0.0, 0.0
    x, y = pulse[index - impact]
    return x * unit, y * unit


_ease = ease_in_out_cubic


def _pop(value):
    return .88 + .12 * ease_out_back(value / .32)


def _camera(name, time, duration, unit):
    u = _ease(time / duration)
    if name == "pan_right":
        return 1.052, (u - .5) * unit * .051, 0
    if name == "zoom_out_subtle":
        return 1.077 - .047 * u, (1 - u) * unit * -.016, unit * .013 * u
    if name == "zoom_in_end":
        return 1.033 + .075 * u, unit * .016 * u, -unit * .027 * u
    if name == "static_punch":
        return 1.04 + .038 * _ease((time - .3) / 1.65), 0, unit * .006 * u
    if name == "zoom_in_slow":
        return 1.032 + .041 * u, unit * .016 * u, unit * -.012 * u
    return 1.027, 0, 0


def _view(layer, motion, depth, shake):
    w, h = layer.size
    zoom, x, y = motion
    inverse = 1 / (1 + (zoom - 1) * depth)
    return layer.transform(layer.size, Image.Transform.AFFINE,
                           (inverse, 0, (w / 2) * (1 - inverse) + (x * depth - shake[0]) * inverse,
                            0, inverse, (h / 2) * (1 - inverse) + (y * depth - shake[1]) * inverse),
                           Image.Resampling.BILINEAR)


@dataclass(frozen=True)
class Stage:
    size: tuple[int, int]

    @property
    def unit(self):
        return min(self.size)

    @property
    def vertical(self):
        return self.size[1] > self.size[0]

    def point(self, x, y):
        return self.size[0] * x, self.size[1] * y

    def at(self, x, y):
        return self.point(x, y if self.vertical else (y - .41) * 1.52 + .52)

    def width(self, value):
        return max(8, round(self.unit * value))

    def blank(self):
        return Image.new("RGBA", self.size)

    @property
    def safe_edges(self):
        return (.16, .84, .81) if self.vertical else (.069, .931, .943)

    def band(self, name):
        w, h = self.size
        top, bottom = PORTRAIT_BANDS[name]
        margin = ceil(w * 40 / 1080)
        return margin, ceil(h * top), w - margin, floor(h * bottom)

    @property
    def action_inset(self):
        # Keep extra room within the screen band for the most extreme camera and impact pose.
        w, h = self.size
        return (ceil(.078 * w), ceil(.286 * h), floor(.922 * w), floor(.630 * h)) if self.vertical else None


def _resized(image, scale):
    if scale != 1:
        return image.resize(tuple(max(1, round(length * scale)) for length in image.size), Image.Resampling.LANCZOS)
    return image


def _fit_piece(image, width, height):
    scale = min(1.0, width / image.width, height / image.height)
    return _resized(image, scale), scale


def _center_piece(target, image, center, *, angle=0, scale=1, bounds=None):
    image = _resized(image, scale)
    if angle:
        image = image.rotate(angle, Image.Resampling.BICUBIC, expand=True)
    shrink = 1.0
    if bounds is not None:
        left, top, right, bottom = bounds
        image, shrink = _fit_piece(image, right - left, bottom - top)
        dx, dy = image.width / 2, image.height / 2
        center = (min(right - dx, max(left + dx, center[0])),
                  min(bottom - dy, max(top + dy, center[1])))
    art.place(target, image, center)
    return center, shrink


@lru_cache(maxsize=48)
def _panel(text, width, height, font, size, seed, fill=IVORY, align="left"):
    pad = max(6, round(size * .30))
    spacing = max(1, size // 9)
    layout = fit_text(text, (max(2, width - 2 * pad), max(2, height - 2 * pad)),
                      font_path=font, max_size=size, min_size=max(5, size // 3), spacing=spacing)
    left, top, right, bottom = layout.bbox
    paper = paper_card((round(right - left) + 2 * pad, round(bottom - top) + 2 * pad), fill, seed)
    ImageDraw.Draw(paper).multiline_text((pad - left, pad - top), layout.text, font=layout.font, fill=INK,
                                        spacing=spacing, align=align)
    return cutout(paper, border=2, seed=seed)


def _header(stage, options):
    left, right, _ = stage.safe_edges
    max_w = round((right - left) * stage.size[0]) - 24
    top = round(stage.size[1] * (.150 if stage.vertical else .055))
    x = round(stage.size[0] * left)
    layers = []
    if options.headline:
        heading = _panel(options.headline, max_w, round(stage.size[1] * .16), SERIF,
                         max(12, stage.width(.109 if "\n" in options.headline else .080)), 31)
        layers.append((heading, (x, top)))
        top += heading.height - 10
    if options.label:
        tag = _panel(options.label.upper(), max_w, round(stage.size[1] * .078), TYPEWRITER,
                     max(8, stage.width(.035)), 45, "#e5c99a")
        layers.append((tag, (x + 6, top)))
    return layers


def _portrait_header(stage, options):
    left, top, right, bottom = stage.band("header")
    width = right - left
    height = bottom - top
    x = (left + right) / 2
    parts = []
    if options.headline:
        title_height = round(height * (.67 if options.label else .89))
        text = " ".join(options.headline.split())
        image = _panel(text, width - 24, height, SERIF, stage.width(.104), 31, align="center")
        parts.append(_fit_piece(image, width, title_height)[0])
    if options.label:
        label_height = round(height * (.32 if parts else .56))
        image = _panel(options.label.upper(), width - 24, height, TYPEWRITER, stage.width(.034), 45, "#e5c99a")
        parts.append(_fit_piece(image, width, label_height)[0])
    y = (top + bottom - sum(image.height for image in parts)) / 2
    layers = []
    for image in parts:
        layers.append((image, (round(x - image.width / 2), round(y))))
        y += image.height
    return layers


def _caption_tokens(narration, replacements):
    words = narration.split()
    keys = tuple(map(word_key, words))
    aliases = sorted(((tuple(map(word_key, phrase.split())), value) for phrase, value in replacements.items()),
                     key=lambda item: -len(item[0]))
    tokens = []
    index = 0
    while index < len(words):
        match = next(((len(phrase), replacement) for phrase, replacement in aliases
                      if phrase and keys[index:index + len(phrase)] == phrase), None)
        if match is None:
            tokens.append((index, words[index]))
            index += 1
            continue
        count, replacement = match
        suffix = re.search(r"[^\w]+$", words[index + count - 1])
        tokens.append((index, replacement + (suffix[0] if suffix else "")))
        index += count
    return tokens


def _caption_blocks(narration, replacements, width):
    tokens = _caption_tokens(narration, replacements)
    start = 0
    while start < len(tokens):
        end = min(start + 2, len(tokens))
        last_break = None
        while end <= len(tokens):
            group = [word for _, word in tokens[start:end]]
            if len(" ".join(group)) > width and last_break is not None:
                break
            if word_key(group[-1]) not in DANGLING:
                last_break = end
                if (len(group) >= 4 and group[-1].endswith((".", "?", "!", ";"))) or end == len(tokens):
                    break
            end += 1
        end = last_break or min(end, len(tokens))
        # A final one- or two-word clause reads with its preceding block.
        if len(tokens) - end <= 2:
            end = len(tokens)
        yield tokens[start][0], " ".join(word for _, word in tokens[start:end])
        start = end


def _caption_panel(stage, text):
    w, h = stage.size
    left, right, bottom = stage.safe_edges
    caption_width = round((right - left) * w) - 22
    image = _panel(text, caption_width, round(h * (.10 if stage.vertical else .137)), CAPTION,
                   max(11, stage.width(.057)), 96, "#fbf2dc", "center")
    if stage.vertical:
        _, top_y, _, end_y = stage.band("caption")
        image, _ = _fit_piece(image, round((right - left) * w), end_y - top_y)
        y = round((top_y + end_y - image.height) / 2)
    else:
        y = round(bottom * h) - image.height
    x = round((left + right) * w / 2 - image.width / 2)
    return image, (x, y)


def _captions(stage, narration, audio, replacements=None):
    times = word_starts(narration, audio)
    for index, group in _caption_blocks(narration, replacements or {}, 49 if stage.vertical else 68):
        yield max(0, times[index][1] - .055), *_caption_panel(stage, group)


def _set_map(stage, layer, x, y, fraction, at):
    width = stage.width(fraction)
    center = stage.at(x - .13 * (1 - ease_out_back(at / .57)), y)
    image = art.australia(width)
    center, shrink = _center_piece(layer, image, center, angle=-5, bounds=stage.action_inset)
    width *= shrink
    return center, width


def _pinned_background(stage, layers, layout):
    w, h = stage.size
    if layout not in {"field", "expedition", "portrait"}:
        return
    ridge = art.terrain(round(w * 1.10), round(.47 * h), "#aa9e78", 52)
    art.place(layers[0], ridge, stage.point(.49, .86 if stage.vertical else .94))
    foreground = art.terrain(round(w * 1.12), round(.41 * h), "#ad844e", 79)
    art.place(layers[1], foreground, stage.point(.49, .92 if stage.vertical else .99))


def _intro(stage, layers, t, step):
    _, mid, fg = layers
    _set_map(stage, mid, .50 if stage.vertical else .38, .460 if stage.vertical else .415,
             .80 if stage.vertical else 1.00, t)
    arrival = ease_out_back((t + .11) / .50)
    if arrival:
        hero = art.emu(stage.width(.59 if stage.vertical else .69), step % 42, "look")
        x, y = (.652, .478) if stage.vertical else (.595, .395)
        _center_piece(fg, hero, stage.at(x + .14 * (1 - arrival), y + .027 * (1 - arrival)),
                      angle=-5 * (1 - arrival), scale=.9 + .1 * arrival, bounds=stage.action_inset)


def _field(stage, layers, t, step, progress):
    bg, mid, fg = layers
    wheat = art.prop("wheat", stage.width(.67 if stage.vertical else .76))
    for x, tilt in ((.21, -6), (.58, 5), (.93, -2)):
        _center_piece(bg, wheat, stage.at(x, .515 if stage.vertical else .498),
                      angle=tilt + sin(step * .16), bounds=stage.action_inset)
    fall = _ease((progress - .38) / .17)
    fence = art.prop("fence", stage.width(.70 if stage.vertical else .83))
    _center_piece(mid, fence, stage.at(.50 if stage.vertical else .53, .495 + .038 * fall),
                  angle=-3 + 37 * fall, bounds=stage.action_inset)
    for index in range(5):
        travel = (progress * (1.28 + index * .049) + index * .226 + .03)
        x = .16 + .68 * (travel % 1) if stage.vertical else travel % 1.39 - .17
        y = (.405 if stage.vertical else .412) + .037 * index
        lift = abs(sin((step + index * 2) * .55)) * .009
        width = stage.width((.215 if stage.vertical else .25) + .041 * index)
        bird = art.emu(width, (step + 3 * index) % 12, variant=index % 3)
        _center_piece(fg if index > 1 else mid, bird, stage.at(x, y - lift),
                      angle=2 * sin(step * .44 + index), bounds=stage.action_inset)
    for x, angle in ((.04, -11), (.87, 10)):
        piece = art.prop("wheat", stage.width(.29)) if stage.vertical else wheat
        y = .615 if stage.vertical else .61
        _center_piece(fg, piece, stage.at(x, y), angle=angle + 2 * sin(t * 3), bounds=stage.action_inset)


def _expedition(stage, layers, t, step):
    _, mid, fg = layers
    move = ease_out_back(t / .72)
    truck = art.prop("truck", stage.width(.70 if stage.vertical else .91))
    _center_piece(mid, truck, stage.at(.55 + .47 * (1 - move), .389 if stage.vertical else .449),
                  angle=2 * sin(step * .64) * (1 - move), bounds=stage.action_inset)
    for index in range(3):
        elapsed = t - .076 * index
        if elapsed < 0:
            continue
        enter = ease_out_back(elapsed / .60)
        soldier = art.soldier(stage.width(.145 if stage.vertical else .175), (step + 2 * index) % 12 if enter < 1 else
                              .54 * sin(step * .42 + index), officer=index == 0)
        y = (.426 if stage.vertical else .423) + index * .022
        _center_piece(fg, soldier, stage.at(.195 + index * .127 - .38 * (1 - enter), y),
                      angle=-4 * (1 - enter), scale=_pop(elapsed), bounds=stage.action_inset)
    stand = art.prop("tripod", stage.width(.27 if stage.vertical else .33))
    shot = (step - 13) % 13 if step >= 13 else 12
    recoil = 1 - _ease(shot / 3) if shot < 3 else 0
    x, y = (.643, .528) if stage.vertical else (.65, .49)
    _center_piece(fg, stand, stage.at(x + .24 * (1 - move) - .025 * recoil, y),
                  angle=5.5 * recoil, bounds=stage.action_inset)
    if shot < 7:
        cloud = art.discharge(stage.width(.147 + .006 * shot), floor(shot))
        _center_piece(fg, cloud, stage.at(x + .16 + .006 * shot, y - .023 - .006 * shot),
                      angle=-8 + shot * 2, bounds=stage.action_inset)
    if step >= 3:
        age = step - 3
        fall = min(1, age / 6) ** 2
        bounce = .027 * abs(sin((age - 6) * tau / 5.2)) * max(0, 1 - (age - 6) / 5.6) if age > 6 else 0
        box = art.prop("box", stage.width(.19 if stage.vertical else .24))
        landing = .583 if stage.vertical else .52
        _center_piece(fg, box, stage.at(.44, landing - .185 * (1 - fall) - bounce),
                      angle=4 + 15 * (1 - fall) - 110 * bounce, bounds=stage.action_inset)


def _dash_route(draw, points, progress, unit):
    full = sum(hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:]))
    left = full * progress
    pitch = max(6, unit * .024)
    offset = 0.0
    for a, b in zip(points, points[1:]):
        length = hypot(b[0] - a[0], b[1] - a[1])
        if left <= 0 or not length:
            break
        x, y = (b[0] - a[0]) / length, (b[1] - a[1]) / length
        for i in range(floor(offset / pitch), ceil((offset + min(left, length)) / pitch)):
            start = max(0, i * pitch - offset)
            stop = min(length, left, i * pitch + pitch * .58 - offset)
            if stop > start:
                draw.line((a[0] + x * start, a[1] + y * start, a[0] + x * stop, a[1] + y * stop),
                          fill=RED, width=max(2, round(unit * .007)))
        offset += length
        left -= length
        if left <= 0 or b == points[-1]:
            traveled = max(0, min(length, length + left))
            ex, ey = a[0] + x * traveled, a[1] + y * traveled
            arrow = pitch * .78
            draw.polygon(((ex, ey), (ex - x * arrow - y * arrow * .45, ey - y * arrow + x * arrow * .45),
                          (ex - x * arrow + y * arrow * .45, ey - y * arrow - x * arrow * .45)),
                         fill=RED)
            break


def _routes(stage, layers, t, step):
    _, mid, fg = layers
    (cx, cy), width = _set_map(stage, mid, .50, .462 if stage.vertical else .42,
                               .84 if stage.vertical else 1.08, t)
    draw = ImageDraw.Draw(mid)
    root = cx - .23 * width, cy + .10 * width
    legs = (((-.06, .005), (.15, -.10), (.285, -.072)),
            ((-.05, .11), (.09, .084), (.25, .161)),
            ((-.20, -.03), (-.16, -.16), (-.03, -.205)))
    for index, route in enumerate(legs):
        points = (root, *((cx + x * width, cy + y * width) for x, y in route))
        u = _ease((t - .4 - .24 * index) / 2.5)
        if u:
            _dash_route(draw, points, u, stage.unit)
        if u > .35:
            x, y = points[-1]
            bird = art.emu(stage.width(.118 if stage.vertical else .117), (step + index * 4) % 12, variant=index)
            hop = abs(sin(step * .39 + index * 2))
            _center_piece(fg, bird, (x + (u - 1) * width * .15 + 3 * sin(step * .55), y - hop * 4),
                          bounds=stage.action_inset)
    scout = art.soldier(stage.width(.11 if stage.vertical else .13), (step / 3) % 12, flip=True)
    _center_piece(fg, scout, (root[0] - stage.unit * .047, root[1] + stage.unit * .016),
                  angle=7 * sin(step * .3), bounds=stage.action_inset)


def _portrait(stage, layers, t, step, duration):
    _, mid, fg = layers
    for index in range(2):
        span = duration * (.46 + .25 * index)
        u = max(0, min(1, (t - .22 * index) / span))
        if u >= 1:
            continue
        soldier = art.soldier(stage.width(.134 if stage.vertical else .165), (step + index * 3) % 12, flip=True)
        if stage.vertical:
            # The retreat uses its own lower lane. The report is already pinned above it.
            x = .30 + .15 * index - (.157 + .15 * index) * u
            y = .565 + .018 * index - .005 * abs(sin(step * .64 + index))
            exit_scale = 1 - .60 * _ease((u - .80) / .20)
            _center_piece(mid, soldier, stage.at(x, y), angle=5, scale=exit_scale, bounds=stage.action_inset)
        else:
            _center_piece(mid, soldier, stage.at(.31 + .15 * index - .55 * u, .464 - .03 * index), angle=5)
    stats = _panel("9.860 DISPAROS\n986 AVES ABATIDAS", stage.width(.57), stage.width(.31),
                   TYPEWRITER, max(9, stage.width(.032)), 43, "#ded1aa")
    hero = art.emu(stage.width(.64 if stage.vertical else .70), step % 24, "proud", flip=True)
    hero_y = .464 if stage.vertical else .397
    rise = .009 * cos(step * tau / 24)
    _center_piece(fg, hero, stage.at(.651 if stage.vertical else .62, hero_y + rise),
                  angle=2 * sin(step * .30), scale=.9 + .10 * _ease(t / .6), bounds=stage.action_inset)
    x, y = (.25, .393) if stage.vertical else (.285, .422)
    _center_piece(fg, stats, stage.at(x - .105 * (1 - _ease((t - .5) / .55)), y),
                  angle=6, bounds=stage.action_inset)


def _stage_frame(stage, options, old, external, step, duration, impact_step, pace=1, magazine=None):
    t = step / DRAW_FPS / pace
    at = step / DRAW_FPS
    action_step = step / pace
    layers = tuple(layer.copy() for layer in old)
    if magazine is not None:
        draw_magazine(layers[1], magazine, options.module or "illustration", at, duration, _center_piece)
    elif options.layout == "map":
        _intro(stage, layers, t, action_step)
    elif options.layout == "field":
        _field(stage, layers, t, action_step, step / DRAW_FPS / duration)
    elif options.layout == "expedition":
        _expedition(stage, layers, t, action_step)
    elif options.layout == "routes":
        _routes(stage, layers, t, action_step)
    elif options.layout == "portrait":
        _portrait(stage, layers, t, action_step, duration / pace)
    fg = layers[2]
    if not stage.vertical and options.show_header:
        for image, pos in _header(stage, options):
            fg.alpha_composite(image, (pos[0] - round(stage.unit * .085 * (1 - _ease(t / .39))), pos[1]))
    for item, image in external:
        elapsed = t - item.delay_sec
        if elapsed >= 0:
            entry = -.12 * (1 - ease_out_back(elapsed / .36)) if item.motion == "slide" else 0
            target = layers[("bg", "mid", "fg").index(item.layer)]
            _center_piece(target, image, stage.point(item.x + entry, item.y), angle=item.rotation,
                          scale=_pop(elapsed) if item.motion == "pop" else 1, bounds=stage.action_inset)
    if not stage.vertical and impact_step is not None and step >= impact_step:
        age = (step - impact_step) / DRAW_FPS / pace
        big = len(options.stamp) > 6
        paper = stamp(options.stamp, stage.width(.48 if big else .23), 39)
        stamp_x, stamp_y = (.282, .518) if options.layout == "map" else (.675, .532)
        _center_piece(fg, paper, stage.at(stamp_x, stamp_y), angle=7 - 2 * (1 - _ease(age / .26)), scale=_pop(age))
    for index, cue in enumerate(visual_cues(duration)):
        age = at - cue
        if index % 3 == 1 and 0 <= age <= 1.3:
            # Draw a brief editorial arrow beside the action while the secondary piece enters.
            x, y = stage.at(.19, .586)
            progress = ease_out_back(age / .50)
            fade = 1 - _ease((age - .67) / .63)
            tip = (x + stage.unit * .13 * progress, y - stage.unit * .033 * progress)
            draw = ImageDraw.Draw(fg)
            ink = (159, 53, 43, round(223 * fade))
            draw.line((x, y, *tip), fill=ink, width=max(2, stage.unit // 104))
            if progress > .72:
                draw.line((tip[0] - stage.unit * .025, tip[1] - stage.unit * .019, *tip,
                           tip[0] - stage.unit * .023, tip[1] + stage.unit * .027), fill=ink, width=max(2, stage.unit // 104))
    return layers


@lru_cache(maxsize=28)
def _portrait_stamp(stage, text, frame_age, seed, fps=30):
    left, top, right, bottom = stage.band("stamp")
    room = bottom - top
    art_width = stage.width(.56 if len(text) > 6 else .264)
    ink = stamp(text, art_width, 39)
    if ink.height > room * .72:
        ink = _resized(ink, room * .72 / ink.height)
    age = frame_age / fps
    lean = (1 if seed % 2 else -1) * (3.3 + 1.2 * (1 - _ease(age / .28)))
    ink = _resized(ink, _pop(age)).rotate(lean, Image.Resampling.BICUBIC, expand=True)
    ink, _ = _fit_piece(ink, right - left - 14, room)
    return ink, (right - ink.width - ceil(stage.size[0] * .021), round((top + bottom - ink.height) / 2))


def _frames(stage, spec, options, scene, timing, seed, external, impact, magazine=None, film=None):
    w, h = stage.size
    old = (tabletop(stage.size, options.paper, seed), stage.blank(), stage.blank())
    # Keep the fixed stock cached while positions and articulated poses advance at output fps.
    old = tuple(layer.copy() for layer in old)
    if magazine is None:
        _pinned_background(stage, old, options.layout)
    if scene.captions_hidden:
        captions = ()
    elif timing.continuous:
        captions = tuple((block.start_sec, *_caption_panel(stage, block.text)) for block in timing.caption_blocks)
    else:
        captions = tuple(_captions(stage, scene.narration, timing.audio, options.caption_numbers))
    starts = tuple(caption[0] for caption in captions)
    header = _portrait_header(stage, options) if stage.vertical and options.show_header else ()
    hit_step = impact * DRAW_FPS / timing.fps if impact is not None else None
    pace = timing.authored_time(1, reference=REFERENCE_SECONDS)
    for index in range(timing.frame_count):
        at = index / timing.fps
        layers = _stage_frame(stage, options, old, external, at * DRAW_FPS, timing.duration_sec, hit_step, pace, magazine)
        if film is not None:
            _center_piece(layers[1], next(film), stage.at(.50, .461), angle=-2, scale=_pop(at + .10),
                          bounds=stage.action_inset)
        camera = _camera(options.camera, at / pace, timing.duration_sec / pace, min(w, h))
        pulse, pan = camera_accents(at, timing.duration_sec)
        zoom, x, y = camera
        shake = camera_shake(index, impact, stage.unit)
        if stage.vertical:
            camera = (1 + (zoom - 1) * .32 + pulse * .46, x * .55 + pan * stage.unit * .62, y * .49)
        else:
            camera = (zoom + pulse, x + pan * stage.unit, y)
        # The table uses edge extension. Transparent midground and foreground keep their silhouettes.
        background = _view(layers[0], camera, PARALLAX[0], shake)
        if background.getchannel("A").getextrema()[0] != 255:
            plate = old[0].copy()
            plate.alpha_composite(background)
        else:
            plate = background
        for layer, plane in zip(layers[1:], PARALLAX[1:], strict=True):
            plate.alpha_composite(_view(layer, camera, plane, shake))
        for image, position in header:
            plate.alpha_composite(image, position)
        if stage.vertical and impact is not None and index >= impact:
            image, position = _portrait_stamp(stage, options.stamp, min(14, round((index - impact) / pace)), seed, timing.fps)
            plate.alpha_composite(image, position)
        caption_index = bisect_right(starts, at) - 1
        if caption_index >= 0 and (not timing.continuous or at < timing.caption_blocks[caption_index].end_sec):
            _, image, pos = captions[caption_index]
            plate.alpha_composite(image, pos)
        plate = plate.convert("RGB")
        if plate.size != (spec.width, spec.height):
            plate = plate.resize((spec.width, spec.height), Image.Resampling.BICUBIC)
        yield plate.tobytes()
    if film is not None:
        next(film, None)  # Let FFmpeg report its exit status after sending the last frame.


def render_scene(spec, scene, timing, media):
    from .sequence import BROWSER_MODULES, render_sequence

    options = Options.model_validate(scene.options)
    if options.module == "photo_25d_parallax":
        from .photo_25d import render_photo_25d

        return render_photo_25d(spec, scene, timing, options)
    if options.module in BROWSER_MODULES or spec.dynamic_edit and options.module in {"newspaper_clipping", "photo_cutout"}:
        return render_sequence(spec, scene, timing, options)
    scale = min(1, 960 / max(spec.width, spec.height))
    stage = Stage((max(96, round(spec.width * scale)), max(96, round(spec.height * scale))))
    seed = crc32(scene.id.encode())
    impact = None
    if options.stamp:
        original = stamp_time(options, timing)
        at = safe_beat(timing, original, latest=(timing.frame_count - 1) / timing.fps)
        impact = impact_frame(at, timing.fps, timing.frame_count)
    external = []
    for item in options.cutouts:
        if item.asset not in media:
            raise ValueError(f"Scene {scene.id!r}: missing local_pack asset {item.asset!r}")
        path = Path(media[item.asset])
        external.append((item, art.supplied(path, stage.width(item.width), path.stat().st_mtime_ns)))
    foley = effects(options.foley, timing, impact / timing.fps if impact is not None else None,
                    timing.authored_time(1, reference=REFERENCE_SECONDS))
    local = local_media_file(options.media_file) if options.media_file else None
    selection = None
    film = None
    lut = options.lut or "vintage_paper_warm"
    illustration = not options.module and options.layout == "custom" and bool(options.media_query)
    if local and not options.module:
        if local.suffix.lower() == ".mp4":
            video = fetch_broll(media_file=local, visual=options.headline, narration=scene.narration)
            film = film_cards(video, stage, timing, lut, seed)
            credit = media_credit(video)
            if picked := credit.get("selection"):
                selection = {"scene": scene.id, **picked, "source": credit["title"]}
        elif not illustration:
            options = options.model_copy(update={"module": "photo_cutout"})
    magazine = prepare_magazine(stage, options, seed, scene.narration) if options.module or (illustration and film is None) else None
    luts = (lut,) if film is not None else (magazine.lut,) if magazine and magazine.lut else ()
    display = (tuple(text for _, text in _caption_blocks(scene.narration, options.caption_numbers, 49 if stage.vertical else 68))
               if not timing.continuous else None)
    return SceneRender(_frames(stage, spec, options, scene, timing, seed, external, impact, magazine, film), foley,
                       impact / timing.fps if impact is not None else None,
                       matting_models=magazine.models if magazine else (),
                       color_luts=luts, caption_texts=display, broll_selection=selection)
