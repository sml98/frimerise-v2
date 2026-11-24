"""Short interface sounds timed to the visual beats of each scene."""

import wave
from typing import Literal

import numpy as np

from core.schema import SAMPLE_RATE, SoundEffect

Cue = Literal["whoosh", "click", "pop", "stamp_click"]
CUES = {
    "whoosh": (.28, -22),
    "click": (.085, -20),
    "pop": (.19, -21),
    "stamp_click": (.135, -18),
}
IMPACT = {
    ("barcode_to_qr", "click"): (.078, .188, .298),
    ("barcode_to_qr", "pop"): (.72,),
    ("go_board_markers", "pop"): (.64,),
    ("bar_comparison", "pop"): (.72,),
    ("open_patent_network", "stamp_click"): (.14,),
}


def transition_duration(timing):
    return min(CUES["whoosh"][0], timing.duration_sec / 2)


def _pcm(name):
    duration, _ = CUES[name]
    t = np.arange(round(duration * SAMPLE_RATE)) / SAMPLE_RATE
    rng = np.random.default_rng(803 + list(CUES).index(name))
    noise = rng.standard_normal(len(t))
    if name == "whoosh":
        smoothed = np.convolve(noise, np.ones(11) / 11, mode="same")
        signal = (.40 * noise + .75 * smoothed) * np.sin(np.pi * t / duration) ** 2
    elif name == "pop":
        signal = .72 * np.sin(2 * np.pi * (500 * t - 1180 * t ** 2)) * np.exp(-27 * t)
    elif name == "click":
        signal = (.7 * np.sin(2 * np.pi * 1770 * t) + .35 * noise) * np.exp(-85 * t)
    else:
        signal = .65 * np.sin(2 * np.pi * 188 * t) * np.exp(-53 * t) + .31 * noise * np.exp(-105 * t)
    envelope = np.clip(t / .005, 0, 1) * np.clip((duration - t) / .03, 0, 1)
    return (np.clip(signal * envelope, -1, 1) * 28000).astype("<i2").tobytes()


def effects(names, visual_type, timing):
    directory = timing.audio.path.parent / "motion-editorial-foley"
    output = []
    for name in dict.fromkeys(names):
        directory.mkdir(exist_ok=True)
        path = directory / f"{name}.wav"
        if not path.exists():
            with wave.open(str(path), "wb") as stream:
                stream.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
                stream.writeframes(_pcm(name))
        if name == "whoosh":
            moments = (0.0, timing.duration_sec - transition_duration(timing))
        else:
            moments = tuple(timing.duration_sec * fraction for fraction in IMPACT.get((visual_type, name), (.5,)))
        output.extend(SoundEffect(path, at, CUES[name][1]) for at in moments)
    return tuple(output)
