"""Original deterministic foley and frame-aligned edit cues."""

import argparse
import json
import os
import subprocess
import sys
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from .schema import SAMPLE_RATE, SoundEffect

KIT = Path(__file__).resolve().parents[1] / "assets" / "sfx"
TRANSITION_CUES = {
    "cut_on_beat": ("stamp_hit", -18),
    "zoom_punch": ("boom_thud", -15),
    "whip_pan": ("whoosh", -15),
    "paper_rip": ("paper_rip", -13),
    "flash_cut": ("whoosh", -17),
}
STEP_START = .16
STEP_PERIOD = .36
RUN_PERIOD = .22
ACTION_STOP = .83
EZ_AUDIO_MODEL = "OpenSound/EzAudio"
EZ_AUDIO_REVISION = "58579e864ba3220a2962d3db4e11f903272bc999"
EZ_AUDIO_CODE_REVISION = "86f18268117dde73bb274401c610e639e4a8fae1"
EZ_AUDIO_CUES = {
    "whoosh": ("A short clean cinematic air whoosh, isolated with no music", .34),
    "paper_rip": ("A single quick rough paper tear, isolated with no music", .36),
    "boom_thud": ("One cinematic soft low thud, isolated with no music", .45),
    "marker_scratch": ("A dry felt-tip marker stroke on paper, isolated with no music", .48),
    "riser": ("A brief cinematic dark air swell, isolated with no music", .60),
    "stamp_hit": ("One hard rubber stamp strike on paper, isolated with no music", .19),
}


def generate_kit(directory=KIT, *, use_ezaudio=False):
    """Write original 48 kHz PCM. Existing project whoosh is retained."""
    try:
        from scipy.signal import convolve
    except ImportError:
        convolve = np.convolve
    directory.mkdir(parents=True, exist_ok=True)
    if use_ezaudio:
        _try_ezaudio(directory)
    lengths = {"whoosh": .34, "paper_rip": .36, "boom_thud": .45, "marker_scratch": .48,
               "riser": .60, "stamp_hit": .19, "footsteps_gravel": .18, "creak_ambience": .71}
    for index, (name, seconds) in enumerate(lengths.items()):
        destination = directory / f"{name}.wav"
        if destination.exists():
            continue
        t = np.arange(round(seconds * SAMPLE_RATE)) / SAMPLE_RATE
        rng = np.random.default_rng(1725 + index)
        noise = rng.normal(0, 1, len(t))
        smooth = convolve(noise, np.ones(33) / 33, mode="same")
        highs = noise - convolve(noise, np.ones(7) / 7, mode="same")
        attack = 1 - np.exp(-t * 145)
        decay = np.exp(-t * 13)
        if name in {"boom_thud", "stamp_hit"}:
            body = np.sin(2 * np.pi * (47 * t + 4 * (1 - np.exp(-t * 14))))
            signal = (body * .85 + smooth * .9 + highs * .12 * np.exp(-t * 30)) * decay
        elif name == "footsteps_gravel":
            scatter = sum(np.exp(-((t - at) / .012) ** 2) for at in (.015, .041, .076))
            signal = smooth * scatter + .25 * highs * np.exp(-t * 19)
        elif name == "paper_rip":
            teeth = .36 + .64 * np.sin(2 * np.pi * (24 * t - 13 * t * t)) ** 2
            signal = (.34 * highs + smooth) * teeth * np.exp(-t * 5)
        elif name == "marker_scratch":
            signal = (.24 * highs + smooth) * (.65 + .35 * np.sin(2 * np.pi * 31 * t)) * (1 - t / seconds)
        elif name == "creak_ambience":
            phase = 2 * np.pi * (110 * t + 210 * t * t + .7 * np.sin(32 * t))
            signal = (.55 * np.sin(phase) + .24 * np.sin(2.05 * phase) + .14 * smooth) * np.sin(np.pi * t / seconds) ** 2
        else:
            direction = (t / seconds) if name == "riser" else np.sin(np.pi * t / seconds)
            signal = (smooth * 2 + highs * .23) * direction
        signal *= attack * np.minimum(1, (seconds - t) / .025)
        signal *= .72 / max(.72, np.max(np.abs(signal)))
        with wave.open(str(destination), "wb") as output:
            output.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
            output.writeframes(np.rint(signal * 32767).astype("<i2").tobytes())


def ezaudio_resample(samples, sample_rate):
    from scipy.signal import resample_poly

    signal = np.asarray(samples, dtype=np.float32)
    if sample_rate != 24_000 or signal.ndim != 1 or not len(signal) or not np.isfinite(signal).all():
        raise ValueError("O EzAudio precisa produzir um efeito mono válido de 24 kHz.")
    return resample_poly(signal, 2, 1)


def _try_ezaudio(directory):
    if not os.environ.get("FRIMERISE_COLAB") or not os.environ.get("FRIMERISE_EZAUDIO_ROOT"):
        return
    cue_names = [name for name in EZ_AUDIO_CUES if not (directory / f"{name}.wav").exists()]
    if not cue_names:
        return
    executable = os.environ.get("FRIMERISE_EZAUDIO_PYTHON", sys.executable)
    request = json.dumps([str(directory.resolve()), cue_names]).encode()
    try:
        subprocess.run([executable, "-m", "core.sfx_engine", "--ezaudio-worker"],
                       input=request, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=900, check=True, env={**os.environ, "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1"})
    except (OSError, subprocess.SubprocessError):
        return


def _ezaudio_worker(directory, cue_names):
    import torch
    from huggingface_hub import hf_hub_download

    if (not os.environ.get("FRIMERISE_COLAB") or not torch.cuda.is_available()
            or "L4" not in torch.cuda.get_device_name(0).split()):
        raise RuntimeError("O EzAudio requer a NVIDIA L4 no Colab.")
    root = Path(os.environ["FRIMERISE_EZAUDIO_ROOT"]).resolve()
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if revision != EZ_AUDIO_CODE_REVISION:
        raise ValueError("Use a revisão aprovada do código MIT do EzAudio.")
    sys.path.insert(0, str(root))
    os.chdir(root)
    from api.ezaudio import EzAudio

    weights = hf_hub_download(EZ_AUDIO_MODEL, "ckpts/s3/ezaudio_s3_l.pt", revision=EZ_AUDIO_REVISION, token=False)
    vae = hf_hub_download(EZ_AUDIO_MODEL, "ckpts/vae/1m.pt", revision=EZ_AUDIO_REVISION, token=False)
    model = EzAudio(model_name="s3_l", ckpt_path=weights, vae_path=vae, device="cuda")
    directory = Path(directory)
    with TemporaryDirectory(dir=directory) as temporary:
        for index, name in enumerate(cue_names):
            caption, length = EZ_AUDIO_CUES[name]
            rate, audio = model.generate_audio(caption, length=2, ddim_steps=40, random_seed=1725 + index)
            signal = ezaudio_resample(audio, rate)
            audible = np.flatnonzero(np.abs(signal) > .05 * np.max(np.abs(signal)))
            if not len(audible):
                continue
            count = min(len(signal), round(length * SAMPLE_RATE))
            start = max(0, min(int(audible[0]) - SAMPLE_RATE // 100, len(signal) - count))
            signal = signal[start:start + count]
            fade = min(count // 2, SAMPLE_RATE // 100)
            if fade:
                signal[:fade] *= np.linspace(0, 1, fade)
                signal[-fade:] *= np.linspace(1, 0, fade)
            signal *= .72 / max(.72, np.max(np.abs(signal)))
            staged = Path(temporary) / f"{name}.wav"
            with wave.open(str(staged), "wb") as output:
                output.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
                output.writeframes(np.rint(signal * 32767).astype("<i2").tobytes())
            staged.replace(directory / staged.name)


def action_cues(action, duration):
    if action in {"walk_to_target", "flee_run"}:
        period = STEP_PERIOD if action == "walk_to_target" else RUN_PERIOD
        return [("footsteps_gravel", float(time), -17)
                for time in np.arange(STEP_START, duration * ACTION_STOP, period)]
    if action == "dig_or_open":
        return [("creak_ambience", duration * .29, -16), ("boom_thud", duration * .64, -17)]
    if action == "inspect_with_lantern":
        return [("creak_ambience", duration * .33, -22)]
    if action == "crowd_gather":
        return [("footsteps_gravel", duration * .12, -19), ("paper_rip", duration * .52, -23)]
    return []


def sound_cues(scene, timing):
    module = scene.options.get("module")
    lead = [(*TRANSITION_CUES[scene.transition], 0)] if scene.transition else []
    cues = [(name, at, gain) for name, gain, at in lead]
    if scene.cartridge_override is None and scene.broll_clip is None:
        if module == "action_cutout_puppet":
            cues.extend(action_cues(scene.options.get("action", "walk_to_target"), timing.duration_sec))
        elif module == "impact_slam":
            cues.append(("stamp_hit", timing.visual_cue_at if timing.visual_cue_at is not None else .1, -10))
        elif module in {"full_bleed_macro", "evidence_pan", "newspaper_clipping"} and (
            module != "newspaper_clipping" or scene.options.get("highlight")
        ):
            cues.append(("marker_scratch", marker_cue(timing), -19))
    if scene.music_mood == "pulse_build":
        cues.append(("riser", max(0, timing.duration_sec - .62), -22))
    override = Path(os.environ.get("FRIMERISE_SFX_KIT", KIT))
    return tuple(SoundEffect(_cue_file(name, override), round(at * timing.fps) / timing.fps, gain)
                 for name, at, gain in cues if round(at * timing.fps) < timing.frame_count)


def _cue_file(name, override):
    path = override / f"{name}.wav"
    return path if path.is_file() else KIT / path.name


def highlight_time(duration):
    return round(duration * .28 * 30) / 30


def marker_cue(timing):
    return timing.visual_cue_at if timing.visual_cue_at is not None else highlight_time(timing.duration_sec)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=KIT)
    parser.add_argument("--ezaudio", action="store_true")
    parser.add_argument("--ezaudio-worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.ezaudio_worker:
        _ezaudio_worker(*json.load(sys.stdin))
    else:
        generate_kit(args.directory, use_ezaudio=args.ezaudio)
