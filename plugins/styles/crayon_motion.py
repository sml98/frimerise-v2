"""Continuous picture-book footfalls, sway and flight."""

from functools import lru_cache
from math import cos, floor, pi, sin, tau

from PIL import Image, ImageDraw

from plugins.styles import paint_art as art
from plugins.styles.paint_motion import ease_in_out


def blink(at, actor_index):
    return (at - 1.08 + actor_index * .38) % 2.37 < .14


def body_sway(at, unit, actor_index, speaking):
    beat = at * (1.85 if speaking else .82) + actor_index * .73
    lean = (.055 if speaking else .022) * sin(tau * beat)
    rise = unit * (.006 if speaking else .003) * cos(tau * beat * .85)
    return lean, rise


def rocking(at, unit):
    return round(unit * .006 * sin(tau * at / 2.7)), round(unit * .012 * sin(tau * at / 1.8))


def walk_position(start, end, at, duration):
    fraction = max(0, min(1, at / duration))
    travel = .22 * ease_in_out(fraction) + .78 * fraction
    return tuple(a + (b - a) * travel for a, b in zip(start, end, strict=True))


@lru_cache(maxsize=72)
def walking_animal(index, phase):
    """The same paired animal geometry as paint_art, with alternating foot contacts."""
    image = Image.new("RGBA", (118, 139))
    draw = ImageDraw.Draw(image)
    color, tall = (("#aa825f", True), ("#e7be8c", True), ("#b0a9af", False), ("#828596", False))[index]
    x, y = 50, 76
    stride = 11 * sin(tau * phase / 6)
    for offset, direction in ((-22, 1), (-12, -1), (11, 1), (20, -1)):
        reach = direction * stride
        lifted = min(6.0, max(0.0, reach * .65))
        art._stroke(draw, ((x + offset, y + 22), (x + offset + reach / 2, y + 38),
                          (x + offset + reach, y + 53 - lifted)), color, 7)
    draw.ellipse((x - 35, y - 6, x + 29, y + 34), fill=color, outline=art.INK, width=5)
    high = y - (49 if tall else 22)
    art._stroke(draw, ((x + 15, y + 8), (x + 25, high + 6)), color, 15)
    draw.ellipse((x + 11, high - 12, x + 52, high + 16), fill=color, outline=art.INK, width=4)
    art._polygon(draw, ((x + 18, high - 10), (x + 14, high - 25), (x + 28, high - 14)), color, width=3)
    draw.ellipse((x + 36, high - 3, x + 42, high + 3), fill=art.INK)
    return image


def gait(at, duration, index):
    if not 0 < at < duration:
        return 0, 0.0
    phase = at * 1.65 + index * .22
    return (phase * 6) % 6, abs(sin(tau * phase)) * (2.8 if index < 2 else 2.1)


def flight_position(start, end, at, duration):
    u = max(0, min(1, at / duration))
    travel = .22 * ease_in_out(u) + .78 * u
    x, y = (a + (b - a) * travel for a, b in zip(start, end, strict=True))
    return x, y - .105 * sin(pi * u)


@lru_cache(maxsize=32)
def wing_pose(image_bytes, size, phase):
    image = Image.frombytes("RGBA", size, image_bytes)
    width, height = size
    shoulder = round(height * .59)
    # Leave the beak and tail anchored while the upper wing strokes fold toward the shoulder.
    ratios = (.60, .80, 1.0, .86, .65, .52, .60)
    index = floor(phase)
    ratio = ratios[index] + (ratios[index + 1] - ratios[index]) * ease_in_out(phase - index)
    wing_height = max(1, round(shoulder * ratio))
    wing = image.crop((0, 0, width, shoulder)).resize((width, wing_height), Image.Resampling.BICUBIC)
    pose = Image.new("RGBA", size)
    pose.paste(wing, (0, shoulder - wing_height))
    pose.paste(image.crop((0, shoulder, width, height)), (0, shoulder))
    return pose
