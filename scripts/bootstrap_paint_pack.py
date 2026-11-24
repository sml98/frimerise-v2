"""Generate the original Comedy Paint sprites, sound effects and music loop."""

import argparse
import sys
import wave
from array import array
from math import exp, pi, sin, tau
from pathlib import Path
from random import Random

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if not __package__:
    sys.path.insert(0, str(ROOT))

from plugins.styles import paint_art as art

SAMPLE_RATE = 24_000
SFX = {
    "pop": (0.16, lambda t, n: 0.76 * sin(tau * (220 * t + 74 * (1 - exp(-33 * t)) / 33)) * exp(-25 * t) + 0.12 * n),
    "whoosh": (0.33, lambda t, n: 0.50 * n * sin(pi * t / 0.33) ** 1.5 + 0.16 * sin(tau * (94 * t + 390 * t * t))),
    "vine_boom": (0.50, lambda t, n: 0.77 * sin(tau * (42 * t + 88 * (1 - exp(-18 * t)) / 18)) * exp(-8 * t) + 0.20 * n * exp(-24 * t)),
    "record_scratch": (0.29, lambda t, n: 0.48 * n * (0.60 + 0.40 * sin(tau * (19 * t - 19 * t * t))) + 0.23 * sin(tau * (155 * t - 177 * t * t))),
    "ding": (0.47, lambda t, n: (0.62 * sin(tau * 880 * t) + 0.20 * sin(tau * 1319 * t)) * exp(-8 * t)),
    "scribble": (0.28, lambda t, n: n * (0.37 + 0.26 * sin(tau * 17 * t)) + 0.14 * sin(tau * (96 * t + 113 * t * t))),
}


def atlas(path, images, columns):
    width, height = images[0].size
    rows = (len(images) + columns - 1) // columns
    sheet = Image.new("RGBA", (width * columns, height * rows))
    for index, image in enumerate(images):
        sheet.paste(image, (index % columns * width, index // columns * height))
    sheet.save(path, optimize=True)


def wavefile(path, values, channels=1):
    pcm = array("h", (round(32767 * min(1, max(-1, value))) for value in values))
    if sys.byteorder != "little":
        pcm.byteswap()
    with wave.open(str(path), "wb") as output:
        output.setparams((channels, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        output.writeframes(pcm.tobytes())


def effect(duration, signal):
    random = Random(101)
    noise = 0.0
    length = round(duration * SAMPLE_RATE)
    for index in range(length):
        t = index / SAMPLE_RATE
        noise = 0.51 * noise + 0.49 * random.uniform(-1.0, 1.0)
        edge = min(1.0, t / 0.009, (length - 1 - index) / SAMPLE_RATE / 0.034)
        yield 0.65 * sin(pi / 2 * edge) ** 2 * signal(t, noise)


def music_loop(path):
    beat = 0.48
    bars = ((48, 55), (45, 52), (41, 48), (43, 50))
    melody = ((72, 76, 79, 76), (69, 72, 76, 72), (69, 72, 77, 72), (67, 71, 74, 71))
    length = round(4 * len(bars) * beat * SAMPLE_RATE)
    left, right = [0.0] * length, [0.0] * length

    def pluck(at, note, level, pan, sustain):
        onset = round(SAMPLE_RATE * at)
        frequency = 440 * 2 ** ((note - 69) / 12)
        attack = 0.012
        duration = round(SAMPLE_RATE * sustain)
        for index in range(duration):
            t = index / SAMPLE_RATE
            phase = tau * frequency * t
            env = min(1.0, t / attack, (duration - 1 - index) / SAMPLE_RATE / 0.035) * exp(-7 * t)
            value = level * env * (sin(phase) + 0.33 * sin(2 * phase) * exp(-9 * t) + 0.13 * sin(3 * phase))
            left[onset + index] += value * (1 - pan)
            right[onset + index] += value * pan

    for bar, (bass, fifth) in enumerate(bars):
        for count in range(4):
            at = (bar * 4 + count) * beat
            pluck(at, bass if count % 2 == 0 else fifth, 0.32, 0.40, beat * 0.82)
            pluck(at + beat * 0.44, melody[bar][count], 0.23, 0.61, beat * 0.50)
    wavefile(path, (value for pair in zip(left, right, strict=True) for value in pair), channels=2)


def bootstrap(assets):
    paint, sfx, music = (assets / name for name in ("paint_pack", "sfx", "music"))
    for directory in (paint, sfx, music):
        directory.mkdir(parents=True, exist_ok=True)
    atlas(paint / "bodies.png", [art.body(pose, color) for color in art.COLORS for pose in art.POSES], len(art.POSES))
    atlas(paint / "expressions.png", [art.expression(name) for name in art.EXPRESSIONS], 4)
    atlas(paint / "mouths.png", [art.mouth(name) for name in art.MOUTHS], 3)
    atlas(paint / "accessories.png", [art.accessory(name) for name in art.ACCESSORIES], 3)
    atlas(paint / "props.png", [art.prop(name) for name in art.PROPS], 4)
    art.paper_texture().save(paint / "paper.png", optimize=True)
    for name, (duration, signal) in SFX.items():
        wavefile(sfx / f"{name}.wav", effect(duration, signal))
    music_loop(music / "comedy_pizzicato.wav")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets-dir", type=Path, default=ROOT / "assets")
    bootstrap(parser.parse_args().assets_dir)
