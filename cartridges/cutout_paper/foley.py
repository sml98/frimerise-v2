"""Short, deterministic foley cues synthesized in the Audio-First job directory."""

import wave
from pathlib import Path

import numpy as np

from core.schema import SAMPLE_RATE, SoundEffect

CUES = {
    "paper_slide": (.38, -17),
    "paper_rustle": (.43, -20),
    "paper_pop": (.21, -18),
    "stamp_hit": (.27, -12),
    "camera_click": (.15, -19),
}


def pcm(name):
    seconds, _ = CUES[name]
    count = round(seconds * SAMPLE_RATE)
    t = np.arange(count) / SAMPLE_RATE
    rng = np.random.default_rng(80 + tuple(CUES).index(name))
    white = rng.standard_normal(count)
    low = np.convolve(white, np.full(21, 1 / 21), mode="same")
    paper = .38 * white + 1.7 * low
    if name == "stamp_hit":
        sound = (.75 * np.sin(2 * np.pi * (118 * t - 105 * t * t)) * np.exp(-32 * t)
                 + .23 * paper * np.exp(-67 * t))
    elif name == "camera_click":
        sound = .46 * (white - low) * (np.exp(-95 * t) + .55 * np.exp(-abs(t - .046) * 148))
    elif name == "paper_rustle":
        sound = .57 * paper * (np.sin(2 * np.pi * 6.5 * t) ** 2 + .09)
    elif name == "paper_pop":
        sound = .44 * paper * np.exp(-22 * t) + .23 * np.sin(2 * np.pi * 185 * t) * np.exp(-35 * t)
    else:
        sound = .48 * paper * (1 + .25 * np.sin(2 * np.pi * 16 * t))
    attack = .003 if name in {"stamp_hit", "camera_click", "paper_pop"} else .043
    envelope = np.minimum(1, t / attack) * np.clip((seconds - t) / .060, 0, 1)
    return (np.tanh(sound) * envelope * 28000).astype("<i2")


def effects(names, timing, impact_sec, pace=1):
    root = timing.audio.path.parent / "cutout-foley"
    scheduled = {"paper_slide": .0, "paper_pop": .083 * pace, "paper_rustle": .42 * pace,
                 "camera_click": .16 * pace, "stamp_hit": impact_sec}
    output = []
    for name in dict.fromkeys(names):
        at = scheduled[name]
        if at is None or at >= timing.duration_sec:
            continue
        root.mkdir(exist_ok=True, parents=True)
        path = Path(root, f"{name}.wav")
        if not path.is_file():
            with wave.open(str(path), "wb") as stream:
                stream.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
                stream.writeframes(pcm(name).tobytes())
        output.append(SoundEffect(path, at, CUES[name][1]))
    return tuple(output)
