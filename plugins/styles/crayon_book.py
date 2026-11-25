"""An illustrated paper cartridge with continuous rigs and low-rate pigment boil."""

import sys
import wave
from array import array
from bisect import bisect_right
from dataclasses import dataclass, field
from functools import lru_cache
from math import cos, radians, sin, sqrt, tau
from pathlib import Path
from random import Random
from typing import Annotated, Literal

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps
from pydantic import BaseModel, Field

from core.motion import camera_accents, ease_in_out_cubic, ease_out_back, visual_cues
from core.registry import register_style
from core.schema import SceneRender
from plugins.media.openmoji_stickers import sticker_path
from plugins.styles import paint_art as art
from plugins.styles.crayon_draw_on import OPENING_SECONDS, DrawOn, Stroke
from plugins.styles.crayon_materials import BROWN, INDIGO, WHITE_WAX, Sheet, smoothstep, wash_pigment
from plugins.styles.crayon_motion import blink, body_sway, flight_position, gait, rocking, walk_position, walking_animal, wing_pose
from plugins.styles.crayon_pages import aligned_caption, caption_pages, captions, mobile_safe_box, page_turn, title
from plugins.styles.paint_captions import word_key, word_starts
from plugins.styles.paint_motion import boil_offset, camera, drawing_step, saw_wrist

ASSETS = Path(__file__).resolve().parents[2] / "assets"

PENCIL = {
    art.INK: INDIGO, art.SKIN: "#f4c7a4", art.RED: "#d4483c", art.JADE: "#62a24c",
    "#697bb0": "#6a5aa6", "#e9a33a": "#ec8a3c", "#e9dec6": "#e7ddc7", "#fff4d5": "#f9ebcd",
    "#edac56": "#f5c63c", "#a44d46": "#b54e46", "#bd7249": "#b86a3e", "#e9aa63": "#e7ae64",
    "#6c403b": BROWN, "#604653": BROWN, "#523c3a": BROWN, "#5b3539": BROWN,
    "#a79a9d": "#8a7796", "#798fb9": "#849ccc", "#b3bdc3": "#b3bec5", "#c1c5d4": "#8f96c0",
    "#d76757": "#d4483c", "#e8a95c": "#ec8a3c", "#efd286": "#f5c63c", "#72a87d": "#62a24c",
    "#778fbb": "#79acd9", "#b0a9af": "#ab96b5", "#828596": "#847fac", "#70a47d": "#62a24c",
}

# Crayon-box colors, in order from the far ridge to the foreground.
GROUNDS = {
    "yard": ("#edcb7a", "#e8b76a", "#eec982"),
    "workshop": ("#abc588", "#91b477", "#b9c991"),
    "parade": ("#a5b995", "#8fb187", "#c8cd8d"),
    "storm": ("#9c9bbd", "#9e9bb3", "#c3a99a"),
    "sea": ("#789cbe", "#6998b6", "#7fb4bf"),
    "mountains": ("#abb0bf", "#9da0be", "#acbf96"),
    "shore": ("#b6be83", "#94b783", "#bfd295"),
}


Coordinate = Annotated[float, Field(ge=0, le=1)]
Scale = Annotated[float, Field(gt=0, le=2.75)]
Seconds = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class Position(BaseModel):
    x: Coordinate
    y: Coordinate
    scale: Scale = 1.0


class Character(Position):
    name: str = "actor"
    pose: str = "neutral"
    expression: str = "neutral"
    accessory: str | None = None
    color: str = "clay"
    talking: bool = False


class Prop(Position):
    kind: str
    x: Coordinate = .5
    y: Coordinate = .75
    layer: Literal["background", "foreground", "ark_hull"] = "background"
    attached_to: str | None = None
    at_word: str | None = None


class Sticker(Position):
    kind: str
    source: Literal["openmoji", "twemoji"] = "openmoji"
    format: Literal["png", "svg"] = "svg"
    at_word: str | None = None
    at_sec: Seconds | None = None
    sfx: str | None = None


class BookSticker(Sticker):
    rotation: float = Field(default=0, ge=-180, le=180)
    flight_from: tuple[float, float] | None = None
    flight_seconds: float = Field(default=1.6, gt=0)
    on_dove: bool = False


class PoseCue(BaseModel):
    at_word: str | None = None
    at_sec: float = Field(default=0, ge=0)
    pose: str
    expression: str | None = None


class BookCharacter(Character):
    key_poses: list[PoseCue] = Field(default_factory=list)


class BookProp(Prop):
    motion: Literal["bob", "drift"] | None = None
    walk_to: tuple[float, float] | None = None
    walk_seconds: float = Field(default=2.45, gt=0)


class BookOptions(BaseModel):
    background: Literal["yard", "workshop", "parade", "storm", "sea", "mountains", "shore"] = "yard"
    camera: Literal["static", "zoom_in_fast", "zoom_out", "pan_left", "pan_right", "float"] = "float"
    headline: str = ""
    characters: list[BookCharacter] = Field(default_factory=list)
    props: list[BookProp] = Field(default_factory=list)
    stickers: list[BookSticker] = Field(default_factory=list)
    wash: Literal["rain", "stars"] | None = None
    wash_at_word: str | None = None
    wash_prepainted: bool = False
    punchline_at_word: str | None = None


@dataclass(frozen=True)
class PlacedProp:
    prop: BookProp
    image: Image.Image
    position: tuple[int, int]
    start: float


@dataclass(frozen=True)
class PlacedSticker:
    sticker: BookSticker
    image: Image.Image
    start: float


@dataclass
class Layers:
    back: list[PlacedProp] = field(default_factory=list)
    front: list[PlacedProp] = field(default_factory=list)
    saws: dict[str, tuple[Image.Image, tuple[float, float], float]] = field(default_factory=dict)


def _bounds_mask(sheet, mask, color, **options):
    if bounds := mask.getbbox():
        sheet.paint(mask.crop(bounds), color, origin=bounds[:2], **options)


def _point(size, x, y):
    return round(size[0] * x), round(size[1] * y)


def _ridge(size, x, theme, level):
    if theme == "sea":
        y = .61 + .089 * level + .015 * sin(5 * tau * x + 1.4 * level) + .007 * sin(9 * tau * x + level)
    else:
        y = .64 + .073 * level + .049 * sin(2 * tau * x + .75 * level) + .025 * cos(4 * tau * x - .9 * level)
    return .23 + .46 * y if size[1] > size[0] else y


@lru_cache(maxsize=16)
def _landscape(size, theme):
    sheet = Sheet(size)
    width, height = size
    unit = min(size)
    rng = Random(theme)
    if theme in {"yard", "workshop", "parade", "mountains"}:
        sun_x, sun_y = _point(size, .745 if theme == "yard" and height > width else
                              .805 if theme == "yard" else .16, .284 if height > width else .24)
        radius = unit * .084
        sun = Image.new("L", size)
        draw = ImageDraw.Draw(sun)
        irregular = [(sun_x + (radius + 1.7 * sin(index * .8)) * cos(index * tau / 60),
                      sun_y + (radius + 1.7 * sin(index * .8)) * sin(index * tau / 60)) for index in range(60)]
        draw.polygon(irregular, fill=255)
        _bounds_mask(sheet, sun, "#f5c63c", strength=.51, hatch=.22)
        rays = Image.new("L", size)
        draw = ImageDraw.Draw(rays)
        for index in range(11):
            angle = index * tau / 11 + rng.uniform(-.10, .10)
            draw.line([(sun_x + radius * r * cos(angle), sun_y + radius * r * sin(angle))
                       for r in (1.18, rng.uniform(1.31, 1.52))], fill=255, width=max(1, round(unit * .006)))
        _bounds_mask(sheet, rays, "#ec8a3c", strength=.67)

    for level, pigment in enumerate(GROUNDS[theme]):
        ridge = [_point(size, x / 112, _ridge(size, x / 112, theme, level)) for x in range(113)]
        fill = Image.new("L", size)
        ImageDraw.Draw(fill).polygon([*ridge, (width, height), (0, height)], fill=255)
        _bounds_mask(sheet, fill, pigment, strength=.45 + level * .045, hatch=.24)
        line = Image.new("L", size)
        ImageDraw.Draw(line).line(ridge, fill=230, width=max(1, round(unit * (.005 if level < 2 else .004))))
        _bounds_mask(sheet, line, INDIGO if theme in {"sea", "storm", "mountains"} else BROWN, strength=.44)

    marks = Image.new("L", size)
    draw = ImageDraw.Draw(marks)
    for _ in range(54):
        x, y = rng.random(), rng.uniform(.60 if height > width else .73, 1.01)
        if theme == "sea":
            points = [(x - .031, y), (x - .014, y - .008), (x + .003, y + .001), (x + .031, y - .003)]
        elif theme in {"yard", "storm"}:
            points = [(x - .01, y), (x, y - .005), (x + .009, y)]
        else:
            points = [(x - .009, y - .017), (x, y), (x + .009, y - .023)]
        draw.line([_point(size, px, py) for px, py in points],
                  fill=rng.randrange(145, 240), width=max(1, round(unit * .004)))
    _bounds_mask(sheet, marks, "#427d91" if theme == "sea" else "#7f784b", strength=.74)
    return sheet


def _recolor(image, replacements=PENCIL):
    # Recolor the flat sprite before resampling so every ink edge stays indigo or brown.
    data = np.array(image)
    original = data[:, :, :3].copy()
    for source, color in replacements.items():
        selector = np.all(original == tuple(bytes.fromhex(source[1:])), axis=2)
        data[selector, :3] = tuple(bytes.fromhex(color[1:]))
    return Image.fromarray(data)


@lru_cache(maxsize=144)
def _actor(pose, color, expression, accessory, mouth, height, wrist, eyes_closed=False):
    image = art.body(pose, color, wrist)
    if accessory:
        image.alpha_composite(art.accessory(accessory))
    face = art.expression(expression)
    if eyes_closed:
        draw = ImageDraw.Draw(face)
        draw.rectangle((87, 87, 149, 107), fill=(0, 0, 0, 0))
        for x in (99, 138):
            draw.line(((x - 7, 95), (x - 1, 98), (x + 6, 95)), fill=art.INK, width=4, joint="curve")
    image.alpha_composite(face)
    image.alpha_composite(art.mouth(mouth))
    image = _recolor(image)
    return image.resize((max(1, round(height * art.TILE[0] / art.TILE[1])), height), Image.Resampling.LANCZOS)


@lru_cache(maxsize=64)
def _prop(kind, width, hull=False):
    original = art.prop(kind)
    image = _recolor(art.ark_hull() if hull else original).crop(original.getbbox())
    return image.resize((width, max(1, round(width * image.height / image.width))), Image.Resampling.LANCZOS)


def _mark(sheet, image, position, step=0, *, knock=True):
    bounds = image.getbbox()
    sheet.paint(image.getchannel("A").crop(bounds), image.convert("RGB").crop(bounds),
                origin=(position[0] + bounds[0], position[1] + bounds[1]),
                strength=.62, hatch=.21, step=step, knock=knock)


def _cue(words, word, second=None, default=0.0):
    if second is not None:
        return second
    token = word_key(word) if word else ""
    return next((at for key, at in words if key == token), default)


def _saw_sprite(image):
    mirrored = ImageOps.mirror(image)
    angle = -17
    turned = mirrored.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
    # Rotate the handle with the same local-to-world transform as the sprite.
    x, y = -.385 * image.width, -.05 * image.height
    c, s = cos(radians(angle)), sin(radians(angle))
    grip = (turned.width / 2 + c * x + s * y, turned.height / 2 - s * x + c * y)
    return turned, grip


def _rms_mouths(path, start_sample=0, sample_count=None, fps=30):
    levels = []
    with wave.open(str(path)) as audio:
        if audio.getsampwidth() != 2:
            raise ValueError("Crayon Book expects 16-bit PCM narration")
        frames = audio.getframerate() // fps
        audio.setpos(start_sample)
        remaining = audio.getnframes() - start_sample if sample_count is None else sample_count
        while remaining > 0:
            count = min(frames, remaining)
            chunk = audio.readframes(count)
            if not chunk:
                break
            pcm = array("h", chunk)
            if sys.byteorder != "little":
                pcm.byteswap()
            levels.append(sqrt(sum(sample * sample for sample in pcm) / len(pcm)))
            remaining -= count
    if not levels:
        return ()
    reference = sorted(levels)[round((len(levels) - 1) * 0.80)]
    quiet, strong = max(150, reference * 0.12), max(400, reference * 0.57)
    return tuple("closed" if level < quiet else "half" if level < strong else "open" for level in levels)


def _actor_height(actor, size):
    return max(1, round(min(size) * .60 * actor.scale))


@lru_cache(maxsize=1)
def _ark_aspect():
    left, top, right, bottom = art.prop("ark").getbbox()
    return (right - left) / (bottom - top)


def _ark_width(options, size):
    tallest = 0
    for actor in options.characters:
        sprite = _actor(actor.pose, actor.color, actor.expression, actor.accessory, "closed", _actor_height(actor, size), 0)
        _, top, _, bottom = sprite.getbbox()
        tallest = max(tallest, bottom - top)
    return round(2.2 * tallest * _ark_aspect()) if tallest else None


def _portrait_options(options, size):
    """Reflow landscape blocking into a protected portrait stage, preserving the Ark rig."""
    options = options.model_copy(deep=True)
    width, height = size
    safe_left, _, safe_right, _ = mobile_safe_box(size)
    stage_left = safe_left / width + .025
    stage_right = safe_right / width - .032
    stage_width = stage_right - stage_left
    ark = next((prop for prop in options.props if prop.kind == "ark"), None)
    raw_ark_width = _ark_width(options, size) if ark else None
    actor_scale = min(.73, stage_width * width / raw_ark_width) if raw_ark_width else .73

    def plot(x, y):
        return stage_left + stage_width * x, .15 + .43 * y

    if ark:
        original_anchor = (ark.x, ark.y)
        ark_x, ark_y = plot(*original_anchor)
        half = min(stage_width / 2, (raw_ark_width or width * .70) * actor_scale / (2 * width))
        ark_x = min(max(ark_x, stage_left + half), stage_right - half)
    for actor in options.characters:
        if ark and ark.layer == "ark_hull":
            actor.x = ark_x + (actor.x - original_anchor[0]) * 16 / 9 * actor_scale
            actor.y = ark_y + (actor.y - original_anchor[1]) * min(size) / height * actor_scale
        else:
            actor.x, actor.y = plot(actor.x, actor.y)
        actor.scale *= actor_scale
    for prop in options.props:
        if prop is ark:
            prop.x, prop.y = ark_x, ark_y
            if raw_ark_width is None:
                prop.scale = min(prop.scale, stage_width * width / (min(size) * .76))
        else:
            prop.x, prop.y = plot(prop.x, prop.y)
            prop.scale *= .60
            if prop.walk_to:
                prop.walk_to = plot(*prop.walk_to)
            if prop.kind not in {"animals", "saw"}:
                half = min(stage_width / 2, min(size) * .76 * prop.scale / (2 * width))
                prop.x = min(max(prop.x, stage_left + half), stage_right - half)
    for sticker in options.stickers:
        sticker.x, sticker.y = plot(sticker.x, sticker.y)
        sticker.scale *= .77
        if sticker.flight_from:
            sticker.flight_from = plot(*sticker.flight_from)
            sticker.x = min(stage_right, sticker.x + .05)
            sticker.y -= .018
    return options


def _layers(options, size, words):
    layers = Layers()
    actors = {actor.name: actor for actor in options.characters}
    ark_width = _ark_width(options, size) if any(prop.kind == "ark" for prop in options.props) else None
    for prop in options.props:
        if prop.kind not in art.PROPS:
            raise ValueError(f"Unknown Crayon Book prop {prop.kind!r}")
        width = max(1, round(min(size) * .76 * prop.scale))
        if prop.kind == "ark" and ark_width is not None:
            width = ark_width
        sprite = _prop(prop.kind, width)
        x, y = _point(size, prop.x, prop.y)
        position = (x - sprite.width // 2, y - sprite.height)
        at = _cue(words, prop.at_word, default=float("inf") if prop.at_word and not words else 0.0)
        if prop.attached_to:
            actor = actors.get(prop.attached_to)
            if prop.kind != "saw" or actor is None or prop.attached_to in layers.saws:
                raise ValueError("A saw needs a character with an unused hand")
            tool, grip = _saw_sprite(sprite)
            layers.saws[actor.name] = tool, grip, at
        elif prop.layer == "foreground":
            layers.front.append(PlacedProp(prop, sprite, position, at))
        else:
            layers.back.append(PlacedProp(prop, sprite, position, at))
            if prop.layer == "ark_hull":
                if prop.kind != "ark":
                    raise ValueError("The ark_hull layer is only available for the ark prop")
                layers.front.append(PlacedProp(prop, _prop(prop.kind, width, hull=True), position, at))
    return layers


def _actor_sprite(actor, size, pose, expression, mouth, wrist=0, eyes_closed=False):
    return _actor(pose, actor.color, expression, actor.accessory, mouth if actor.talking else "closed",
                  _actor_height(actor, size), wrist, eyes_closed)


def _actor_position(size, actor, sprite):
    x, y = _point(size, actor.x, actor.y)
    return x - sprite.width / 2, y - sprite.height * .925


def _pose_tracks(characters, words, pace=1):
    return [tuple(sorted(((_cue(words, cue.at_word, default=cue.at_sec * pace), cue) for cue in actor.key_poses),
                         key=lambda pair: pair[0])) for actor in characters]


def _actor_and_tool(sheet, actor, index, at, step, mouth, saw, shocked, track, together=(0, 0), *, pace=1):
    saw = saw if saw is not None and at >= saw[2] else None
    wrist = saw_wrist((at - saw[2]) / pace) if saw else 0
    pose = actor.pose
    expression = actor.expression
    for cue_at, cue in track:
        if at < cue_at:
            break
        pose, expression = cue.pose, cue.expression or expression
    if saw:
        pose = "pointing"
    if shocked:
        expression = "shocked"
    puppet = _actor_sprite(actor, sheet.size, pose, expression, mouth, wrist, blink(at / pace, index))
    lean, rise = body_sway(at / pace, puppet.height, index, mouth != "closed")
    pivot = puppet.height * .90
    puppet = puppet.transform(puppet.size, Image.Transform.AFFINE, (1, -lean, lean * pivot, 0, 1, 0),
                              Image.Resampling.BICUBIC)
    x, y = _actor_position(sheet.size, actor, puppet)
    left, top = x + together[0], y + rise + together[1]
    if saw:
        tool, grip, _ = saw
        hand_x, hand_y = art.POSES[pose][1][-1]
        grip_y = hand_y / art.TILE[1] * puppet.height
        grip_x = (hand_x + wrist) / art.TILE[0] * puppet.width + lean * (grip_y - pivot)
        _mark(sheet, tool, (round(left + grip_x - grip[0]), round(top + grip_y - grip[1])), step)
    _mark(sheet, puppet, (round(left), round(top)), step)


@lru_cache(maxsize=16)
def _icon(path, width):
    with Image.open(path) as source:
        image = source.convert("RGBA")
    image = image.crop(image.getbbox())
    image = _recolor(image, {"#000000": INDIGO, "#ffffff": WHITE_WAX, "#d0cfce": "#d5cbd3",
                             "#b1cc33": "#82a347", "#5c9e31": "#62a24c"})
    return image.resize((width, max(1, round(width * image.height / image.width))), Image.Resampling.LANCZOS)


def _stickers(options, words, size, media, pace=1):
    for index, sticker in enumerate(options.stickers):
        source = media.get(f"sticker:{index}")
        if source is None:
            source = sticker_path(sticker.kind, ASSETS.parent, source=sticker.source, format=sticker.format)
        image = _icon(source, max(1, round(min(size) * .23 * sticker.scale)))
        if sticker.rotation:
            image = image.rotate(sticker.rotation, Image.Resampling.BICUBIC, expand=True)
        seconds = sticker.at_sec * pace if sticker.at_sec is not None else None
        yield PlacedSticker(sticker, image, _cue(words, sticker.at_word, seconds, .25 * pace))


def _centered(image, x, y):
    return round(x - image.width / 2), round(y - image.height / 2)


def _draw_stickers(sheet, stickers, at, step, pace=1):
    dove = next((item for item in stickers if item.sticker.kind == "dove" and at >= item.start), None)
    center = None
    if dove:
        sticker, image = dove.sticker, dove.image
        route = (sticker.x, sticker.y)
        if sticker.flight_from:
            elapsed = (at - dove.start) / pace
            route = flight_position(sticker.flight_from, route, elapsed, sticker.flight_seconds)
            phase = (elapsed * 12) % 6 if elapsed < sticker.flight_seconds else 2
            image = wing_pose(image.tobytes(), image.size, phase)
        x, y = center = _point(sheet.size, *route)
        _mark(sheet, image, _centered(image, x, y), step)
    for item in stickers:
        sticker, image = item.sticker, item.image
        if item is dove or at < item.start or (sticker.on_dove and center is None):
            continue
        if sticker.on_dove:
            x = center[0] - dove.image.width * .64
            y = center[1] + dove.image.height * .20
        else:
            x, y = _point(sheet.size, sticker.x, sticker.y)
            scale = .86 + .14 * ease_out_back((at - item.start) / (.32 * pace))
            image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                                 Image.Resampling.BICUBIC)
        _mark(sheet, image, _centered(image, x, y), step)


@lru_cache(maxsize=8)
def _wash(size, kind):
    width, height = size
    sky = Image.new("L", size)
    draw = ImageDraw.Draw(sky)
    boundary = [(round(width * x / 100),
                 round(height * (.67 + .053 * sin(x * .14) + .024 * sin(x * .49)))) for x in range(101)]
    draw.polygon([(0, 0), (width, 0), *reversed(boundary)], fill=179 if kind == "rain" else 149)
    return wash_pigment(sky.filter(ImageFilter.GaussianBlur(max(1, height * .006))),
                        (.15, .24, .50) if kind == "rain" else (.43, .32, .61))


def _white_marks(sheet, kind, at, step):
    size = sheet.size
    unit = min(size)
    mask = Image.new("L", size)
    draw = ImageDraw.Draw(mask)
    rng = Random(kind)
    if kind == "rain":
        for column in range(18):
            for row in range(6):
                x = (column + rng.uniform(.05, .95)) / 18
                y = ((row + rng.random()) / 6 * .73 + at * .228) % .73
                dx = rng.uniform(.009, .017)
                draw.line([_point(size, x, y), _point(size, x - dx, y + rng.uniform(.03, .062))],
                          fill=255, width=max(2, round(unit * rng.uniform(.005, .011))))
    else:
        for x, y, length in ((.08, .21, .033), (.22, .11, .022), (.33, .29, .022), (.68, .15, .022),
                             (.80, .28, .032), (.92, .12, .023), (.91, .43, .028), (.13, .42, .018)):
            px, py = _point(size, x, y)
            dx, dy = boil_offset(step, round(x * 50))
            px, py, radius = px + dx, py + dy, length * unit
            line_width = max(2, round(unit * .008))
            draw.line([(px - radius * .65, py), (px + radius * .65, py)], fill=255, width=line_width)
            draw.line([(px, py - radius), (px, py + radius)], fill=255, width=line_width)
    _bounds_mask(sheet, mask, WHITE_WAX, strength=.97, hatch=.02)


def _sweep(size, progress):
    """Soft wet edge of a brush drawn across the sheet, between the drawing beats."""
    width, height = size
    y, x = np.ogrid[:height, :width]
    center = width * (-.17 + 1.42 * progress)
    edge = center + width * (.10 * (1 - ((y - height * .34) / height * 2) ** 2)
                             + .011 * np.sin(y * .093) + .008 * np.sin(y * .036))
    return Image.fromarray((255 * smoothstep(-.030 * width, .030 * width, edge - x)).astype(np.uint8))


def _prop_offset(prop, at, unit):
    if prop.motion == "bob":
        return rocking(at, unit)
    if prop.motion == "drift":
        return round(unit * .027 * sin(at * .92)), round(unit * .013 * sin(at * 1.2))
    return 0, 0


@lru_cache(maxsize=32)
def _walk_sprite(index, phase, height):
    image = _recolor(walking_animal(index, phase))
    return image.resize((max(1, round(height * image.width / image.height)), height), Image.Resampling.LANCZOS)


def _prop_art(item, size, at, pace=1):
    prop = item.prop
    if prop.walk_to is None:
        dx, dy = _prop_offset(prop, at / pace, min(size))
        x, y = item.position
        yield item.image, (x + dx, y + dy)
        return
    if prop.kind != "animals":
        raise ValueError("Only animals accept a walk_to destination")
    for index in range(4):
        # The leading pair reaches the ark first. Each animal keeps its own contact phase.
        elapsed = (at - max(item.start, OPENING_SECONDS * pace)) / pace - .09 * (3 - index)
        x, y = walk_position((prop.x, prop.y), prop.walk_to, elapsed, prop.walk_seconds)
        x, y = _point(size, x + .064 * (index - 1.5), y - .026 * (index % 2))
        phase, rise = gait(elapsed, prop.walk_seconds, index)
        height = max(1, round(min(size) * .30 * prop.scale * (1 - .09 * (index % 2))))
        image = _walk_sprite(index, phase, height)
        yield image, (x - image.width // 2, round(y - image.height - rise))


def _drawing(options, size, layers, stickers, at, step, mouth, punch, tracks, pace=1):
    sheet = _landscape(size, options.background).copy()
    if options.wash:
        _white_marks(sheet, options.wash, at / pace, step)
    for item in layers.back:
        if at >= item.start:
            for image, position in _prop_art(item, size, at, pace):
                _mark(sheet, image, position, step if item.prop.motion or item.prop.walk_to else 0)
    floating_ark = next((item.prop for item in layers.front if item.prop.layer == "ark_hull"), None)
    offset = _prop_offset(floating_ark, at / pace, min(size)) if floating_ark else (0, 0)
    for index, actor in enumerate(options.characters):
        _actor_and_tool(sheet, actor, index, at, step, mouth, layers.saws.get(actor.name),
                        index == 0 and punch is not None and 0 <= at - punch < .55 * pace,
                        tracks[index], offset, pace=pace)
    # Like rig.js's knock layer, the hull clears the pigment of the puppet behind it.
    for item in layers.front:
        if at >= item.start:
            for image, position in _prop_art(item, size, at, pace):
                _mark(sheet, image, position, step)
    _draw_stickers(sheet, stickers, at, step, pace)
    return sheet


def _opening_plan(options, size, layers):
    props = sorted([item for item in (*layers.back, *layers.front) if item.start <= 0],
                   key=lambda item: (item.prop.kind == "ark", item.image.width * item.image.height), reverse=True)
    groups = [entry for item in props for entry in _prop_art(item, size, 0)]
    for actor in options.characters:
        sprite = _actor_sprite(actor, size, actor.pose, actor.expression, "closed")
        position = tuple(map(round, _actor_position(size, actor, sprite)))
        groups.append((sprite, position))
    ridges = [Stroke.from_points(_point(size, x / 112, _ridge(size, x / 112, options.background, level))
                                for x in range(113)) for level in range(3)]
    return DrawOn(size, groups, ridges)


def _camera_image(image, options, at, duration, punch, *, rhythm=(0.0, 0.0)):
    actor = next((actor for actor in options.characters if actor.talking),
                 options.characters[0] if options.characters else None)
    width, height = image.size
    focus = (.5, .44) if actor is None else (actor.x, max(.12, actor.y - min(image.size) / height * .40 * actor.scale))
    zoom, x, y = camera(options.camera, at, duration, punch, focus)
    accent, pan = rhythm
    zoom += accent
    x = max(.05, min(.95, x + pan * 10))
    if height > width:
        zoom = 1 + (zoom - 1) * .38
    left, top = (1 - 1 / zoom) * width * x, (1 - 1 / zoom) * height * y
    return image.resize(image.size, Image.Resampling.BILINEAR, (left, top, left + width / zoom, top + height / zoom))


def _first_page(options, size):
    return _camera_image(_landscape(size, options.background).image(), options, 0, 1, None)


def _frames(spec, timing, options, size, words, layers, stickers, mouths, punch, next_options, narration, pace=1):
    duration = timing.duration_sec
    aligned = timing.caption_blocks
    if timing.continuous:
        subtitles = tuple(aligned_caption(block.text, size) for block in aligned)
        caption_starts = [block.start_sec for block in aligned]
    else:
        subtitles = captions(narration, words, size, duration)
        caption_starts = [caption.start + .18 for caption in subtitles]
    caption_delay = .18
    heading = title(options.headline, size) if options.headline else None
    wash_at = _cue(words, options.wash_at_word, default=.08 * pace)
    opening = _opening_plan(options, size, layers)
    tracks = _pose_tracks(options.characters, words, pace)
    last_frame = (timing.frame_count - 1) / timing.fps
    turn_at = max(0.0, last_frame - .57 * pace) if timing.continuous else max(timing.audio.duration_sec, last_frame - .57)
    incoming = None
    for index in range(timing.frame_count):
        at = index / timing.fps
        step = drawing_step(index, timing.fps)
        mouth = mouths[index] if index < len(mouths) else "closed"
        sheet = _drawing(options, size, layers, stickers, at, step, mouth, punch, tracks, pace)
        dry = sheet.image()
        wet = sheet.image(_wash(size, options.wash)) if options.wash else None
        page = dry
        if wet is not None and options.wash_prepainted:
            page = wet
        elif wet is not None and at > wash_at:
            reveal = (at - wash_at) / (.68 * pace)
            page = wet if reveal >= 1 else Image.composite(wet, dry, _sweep(size, reveal))
        if at < OPENING_SECONDS * pace:
            # Let the first chalk strokes and the page title be visible on the opening frame.
            page = opening.frame(page, .16 + (OPENING_SECONDS - .16) * at / (OPENING_SECONDS * pace))
        frame = _camera_image(page, options, at / pace, duration / pace, None if punch is None else punch / pace,
                              rhythm=camera_accents(at, duration))
        for index_cue, cue in enumerate(visual_cues(duration)):
            age = at - cue
            if index_cue % 3 == 1 and 0 <= age <= .9:
                w, h = frame.size
                draw = ImageDraw.Draw(frame)
                draw.arc((.19 * w, .32 * h, .38 * w, .46 * h), 265,
                         265 + 246 * ease_in_out_cubic(age / .65), fill=BROWN, width=max(2, min(w, h) // 155))
        if heading:
            heading.draw(frame, max(.13, at / pace), step)
        if not timing.continuous and at >= caption_delay:
            subtitles[bisect_right(caption_starts, at) - 1].draw(frame, at - caption_delay, step)
        if next_options is not None and turn_at < at <= last_frame:
            if incoming is None:
                incoming = _first_page(next_options, size)
            frame = page_turn(frame, incoming, (at - turn_at) / (last_frame - turn_at))
        if timing.continuous:
            caption = bisect_right(caption_starts, at) - 1
            if caption >= 0 and at < aligned[caption].end_sec:
                subtitles[caption].draw(frame, 1, step)
        yield frame.resize((spec.width, spec.height), Image.Resampling.BICUBIC).tobytes()


@register_style("crayon_book")
def render_scene(spec, scene, timing, media):
    options = BookOptions.model_validate(scene.options)
    index = next(index for index, item in enumerate(spec.scenes) if item.id == scene.id)
    following = spec.scenes[index + 1] if index + 1 < len(spec.scenes) else None
    next_options = BookOptions.model_validate(following.options) if following and following.plugin == scene.plugin else None
    # Rasterize the tooth in world coordinates before the presentation camera and upscale.
    scale = min(1, 960 / max(spec.width, spec.height))
    size = (max(96, round(spec.width * scale)), max(96, round(spec.height * scale)))
    if size[1] > size[0]:
        options = _portrait_options(options, size)
        if next_options:
            next_options = _portrait_options(next_options, size)
    words = word_starts(scene.narration, timing.audio)
    pace = timing.authored_time(1, reference=4.0)
    punch = None
    if scene.cut_music_for_punchline:
        punch = min((timing.frame_count - 1) / timing.fps,
                    _cue(words, options.punchline_at_word, default=max(0, timing.audio.duration_sec - .25 * pace)))
    mouths = _rms_mouths(timing.audio.path, timing.audio.start_sample, timing.audio.samples, timing.fps) if options.characters else ()
    return SceneRender(_frames(spec, timing, options, size, words, _layers(options, size, words),
                               tuple(_stickers(options, words, size, media, pace)), mouths, punch, next_options,
                               scene.narration, pace), punchline_at_sec=punch,
                       caption_texts=tuple(caption_pages(scene.narration, size)) if not timing.continuous else None)
