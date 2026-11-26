"""Adaptive instrumental beds with an isolated, optional ACE-Step L4 worker."""

import json
import os
import subprocess
import sys
import wave
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from .schema import SAMPLE_RATE

ACE_MODEL = "ACE-Step/Ace-Step1.5"
ACE_REVISION = "19671f406d603126926c1b7e2adc169acbcade22"
ACE_CODE_REVISION = "ca1e85fe9430179831e6bc6be790c332190a3866"
ACE_DIT = "acestep-v15-turbo"
# The official ACE-Step 1.5 checkpoints share this Apache-2.0 text encoder.
ACE_TEXT_ENCODER = "Qwen/Qwen3-Embedding-0.6B"
MOOD_DIRECTION = {
    "tense_low": "restrained low pulse, bowed bass and dark room tone",
    "pulse_build": "build with muted percussion and short string figures",
    "drop_silence": "suspend the pulse before the reveal",
    "resolve": "soft unresolved strings and a dry final chord",
}
DARK_TONES = {"dark_mystery", "historical_suspense", "historical_documentary", "suspense", "horror"}


@dataclass(frozen=True)
class MusicSelection:
    path: Path
    method: str
    license: str | None = None
    bpm: int | None = None
    musical_key: str | None = None
    model: str | None = None
    model_revision: str | None = None
    text_encoder: str | None = None


def procedural_bed(output, *, bpm=120, minor=False, seed=2026, moods=()):
    """Compose an original CC0 stereo loop from oscillators and seeded brush pulses."""
    beat_size = round(60 * SAMPLE_RATE / bpm)
    seconds = np.arange(beat_size) / SAMPLE_RATE
    envelope = ((1 - np.exp(-seconds * 190)) * np.exp(-seconds * 11)
                * np.clip((beat_size / SAMPLE_RATE - seconds) / .035, 0, 1))
    rng = np.random.default_rng(seed)
    loop = np.zeros((32 * beat_size, 2), dtype=np.float32)
    chords = ((45, 52, 57, 60), (41, 48, 53, 57), (43, 50, 55, 59), (40, 47, 52, 56)) if minor else (
        (48, 55, 60, 64), (45, 52, 57, 60), (41, 48, 53, 57), (43, 50, 55, 59)
    )
    energy = {"tense_low": .65, "pulse_build": 1.18, "drop_silence": .40, "resolve": .78}
    for index in range(32):
        pitch = chords[index // 8][index % 4]
        frequency = 440 * 2 ** ((pitch - 69) / 12)
        phase = 2 * np.pi * frequency * seconds
        note = (.27 * np.sin(phase) + .071 * np.sin(2 * phase) + .022 * np.sin(3 * phase)) * envelope
        brush = rng.normal(0, .115, beat_size) * np.exp(-seconds * 82)
        pulse = (.20 + .095 * (index % 4 == 0)) * np.sin(
            2 * np.pi * (55 * seconds + .53 * (1 - np.exp(-34 * seconds)))
        ) * np.exp(-seconds * 28)
        gain = energy.get(moods[index * len(moods) // 32], 1) if moods else 1
        start = index * beat_size
        loop[start:start + beat_size, 0] = (note * .88 + .43 * brush + pulse) * gain
        loop[start:start + beat_size, 1] = (note + .37 * brush + pulse) * gain
    pcm = np.rint(np.clip(loop, -1, 32767 / 32768) * 32768).astype("<i2")
    with wave.open(str(output), "wb") as destination:
        destination.setparams((2, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        destination.writeframes(pcm.tobytes())


def adaptive_music(output, *, topic="", tone="neutral", moods=(), duration_sec=None):
    dark = tone in DARK_TONES
    bpm, key = (88, "A minor") if dark else (108, "C major")
    moods = tuple(moods)
    seed_material = json.dumps([topic, tone, moods], ensure_ascii=False).encode()
    seed = int.from_bytes(sha256(seed_material).digest()[:4], "big") & 0x7FFFFFFF
    duration = float(duration_sec if duration_sec is not None else 30)
    if not isfinite(duration) or duration <= 0:
        raise ValueError("A trilha adaptativa requer duração positiva.")
    opening = "Dark suspense documentary" if dark else "Restrained documentary"
    direction = ", then ".join(dict.fromkeys(MOOD_DIRECTION.get(mood, "measured instrumental bed") for mood in moods))
    context = " ".join(topic.split())[:120]
    caption = f"{opening} underscore for spoken narration. Act cues: {direction}. Context: {context}."
    if _try_ace_step(output, caption[:512], duration, bpm, key, seed):
        return MusicSelection(output, "ace_step_1_5", "MIT", bpm, key, ACE_MODEL, ACE_REVISION, ACE_TEXT_ENCODER)
    procedural_bed(output, bpm=bpm, minor=dark, seed=seed, moods=moods)
    return MusicSelection(output, "procedural_cc0", "CC0-1.0", bpm, key)


def _try_ace_step(output, caption, duration, bpm, key, seed):
    if not os.environ.get("FRIMERISE_COLAB") or not os.environ.get("FRIMERISE_ACE_STEP_ROOT"):
        return False
    command = [os.environ.get("FRIMERISE_ACE_STEP_PYTHON", sys.executable), "-m", "core.music_generator"]
    request = json.dumps([str(output.resolve()), caption, duration, bpm, key, seed]).encode()
    env = {**os.environ, "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1", "MPLBACKEND": "Agg"}
    try:
        subprocess.run(command, input=request, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=True, timeout=900, env=env)
    except (OSError, subprocess.SubprocessError):
        return False
    return output.is_file() and output.stat().st_size > 44


def _ace_worker(output, caption, duration, bpm, key, seed):
    import torch
    from huggingface_hub import snapshot_download

    if (not os.environ.get("FRIMERISE_COLAB") or not torch.cuda.is_available()
            or "L4" not in torch.cuda.get_device_name(0).split()):
        raise RuntimeError("A geração ACE-Step requer a NVIDIA L4 no Colab.")
    root = Path(os.environ["FRIMERISE_ACE_STEP_ROOT"]).resolve()
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if revision != ACE_CODE_REVISION:
        raise ValueError("Use a revisão aprovada do código MIT do ACE-Step.")
    sys.path.insert(0, str(root))
    from acestep.handler import AceStepHandler
    from acestep.inference import GenerationConfig, GenerationParams, generate_music
    from acestep.llm_inference import LLMHandler

    checkpoints = root / "checkpoints" / ACE_REVISION
    snapshot_download(
        ACE_MODEL, revision=ACE_REVISION, token=False, local_dir=checkpoints,
        # The upstream initializer checks for the bundled 1.7B LM even in DiT-only mode.
        allow_patterns=[f"{ACE_DIT}/*", "vae/*", "Qwen3-Embedding-0.6B/*", "acestep-5Hz-lm-1.7B/*"],
    )
    os.environ["ACESTEP_CHECKPOINTS_DIR"] = str(checkpoints)
    dit_handler = AceStepHandler()
    _, initialized = dit_handler.initialize_service(project_root=str(root), config_path=ACE_DIT, device="cuda",
                                                     use_flash_attention=False, prefer_source="huggingface")
    if not initialized:
        raise RuntimeError("Falha ao inicializar o ACE-Step.")
    params = GenerationParams(
        caption=caption, lyrics="[Instrumental]", instrumental=True, bpm=bpm, keyscale=key,
        duration=min(600, max(10, duration)), inference_steps=8, shift=3.0, seed=seed, thinking=False,
    )
    config = GenerationConfig(batch_size=1, use_random_seed=False, seeds=[seed], audio_format="wav")
    with TemporaryDirectory(dir=Path(output).parent) as staging:
        result = generate_music(dit_handler, LLMHandler(), params, config, save_dir=staging)
        if not result.success or not result.audios:
            raise RuntimeError("Nenhuma faixa ACE-Step gerada.")
        staged = Path(staging) / "bed48.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(result.audios[0]["path"]),
                        "-vn", "-ar", str(SAMPLE_RATE), "-ac", "2", "-c:a", "pcm_s16le", str(staged)],
                       check=True)
        staged.replace(output)


if __name__ == "__main__":
    _ace_worker(*json.load(sys.stdin))
