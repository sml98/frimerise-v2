"""Select lightweight and optional speech engines through the existing plugin registry."""

import os
import wave
from dataclasses import replace
from functools import cache
from inspect import signature

import numpy as np

from .audio_aligner import _verified_words
from .registry import TTSUnavailable, get_plugin, register_tts
from .schema import SAMPLE_RATE, Speech
from .tts_pronunciation import pronounce

ENGINES = {"edge": "edge_kokoro_tts", "kokoro": "kokoro_tts", "voxcpm2": "voxcpm2_tts",
           "chatterbox_ptbr": "chatterbox_ptbr"}
PHONETIC_ENGINES = frozenset((*ENGINES.values(), "gemini_tts"))
GRAVE_TONES = frozenset({"dark_mystery", "historical_suspense", "historical_documentary", "suspense", "horror"})


def default_edge_voice(tone):
    return "pt-BR-AntonioNeural" if tone in GRAVE_TONES else "pt-BR-FranciscaNeural"


def synthesize_one(backend, text, *, voice_id, speech_style, output, locale=None, continuous=False, voice_ref=None,
                   pronunciation_overrides=None, tone="neutral"):
    synthesize = get_plugin("tts", backend)
    parameters = signature(synthesize).parameters
    options = {"locale": locale} if locale else {}
    if backend == "edge_kokoro_tts" and tone in GRAVE_TONES and locale in (None, "pt-BR"):
        voice_id = voice_id or default_edge_voice(tone)
        if "pitch" in parameters and "rate" in parameters:
            options.update(pitch="-6Hz", rate="-4%")
    if continuous and "continuous" in parameters:
        options["continuous"] = True
    if "tone" in parameters:
        options["tone"] = tone
    if voice_ref is not None:
        if "voice_ref" not in parameters:
            raise ValueError("O backend escolhido não aceita --voice-ref")
        options["voice_ref"] = voice_ref
    native_default = backend in {"kokoro_tts", "voxcpm2_tts", "chatterbox_ptbr"} or (
        backend == "edge_kokoro_tts" and (voice_id is None or voice_id.lower().startswith("pt-br-"))
    )
    tts_locale = locale or ("pt-BR" if native_default else None)
    spoken = pronounce(text, tts_locale, pronunciation_overrides) if backend in PHONETIC_ENGINES else text
    speech = replace(synthesize(spoken, voice_id=voice_id, speech_style=speech_style, output=output, **options),
                     backend=backend)
    if spoken == text:
        return speech
    words = speech.words
    if words:
        duration = max(word.end_sec for word in words)
        try:
            spoken_words = _verified_words(words, spoken, duration)
            words = _verified_words(spoken_words, text, duration, minimum_match=0)
        except ValueError:
            words = ()
    return replace(speech, words=tuple(words), spoken_text=spoken)


@register_tts("chatterbox_ptbr")
def chatterbox_ptbr(text, *, voice_id, speech_style, output, locale=None, continuous=False, voice_ref=None, tone="neutral"):
    from .tts_plugins.chatterbox_ptbr import synthesize

    return synthesize(text, voice_id=voice_id, speech_style=speech_style, output=output, locale=locale,
                      continuous=continuous, voice_ref=voice_ref, tone=tone)


@register_tts("kokoro_tts")
def kokoro(text, *, voice_id, speech_style, output, locale=None, continuous=False):
    from plugins.tts.edge_kokoro_tts import _kokoro

    voice = (voice_id or "pf_dora").removeprefix("kokoro:")
    if locale not in (None, "pt-BR") or not voice.startswith(("pf_", "pm_")):
        raise TTSUnavailable("Escolha uma voz Kokoro nativa em português brasileiro, como pf_dora.")
    speech = _kokoro(text, voice, output, continuous=continuous)
    return replace(speech, locale="pt-BR")


@cache
def _vox_model():
    try:
        import torch
        from voxcpm import VoxCPM
    except (ImportError, OSError) as error:
        raise TTSUnavailable("VoxCPM2 requer as dependências opcionais do Colab.") from error
    if not torch.cuda.is_available():
        raise TTSUnavailable("VoxCPM2 requer uma GPU CUDA, como a L4 do Colab.")
    return VoxCPM.from_pretrained(hf_model_id=os.environ.get("FRIMERISE_VOXCPM_MODEL", "openbmb/VoxCPM2"),
                                  load_denoiser=False, optimize=False, device="cuda")


@register_tts("voxcpm2_tts")
def voxcpm2(text, *, voice_id, speech_style, output, locale=None, continuous=False, voice_ref=None):
    if locale not in (None, "pt-BR"):
        raise TTSUnavailable("O perfil VoxCPM2 do projeto usa português brasileiro.")
    if voice_ref is not None:
        try:
            with wave.open(str(voice_ref), "rb") as reference:
                if not reference.getnframes():
                    raise ValueError("A voz de referência está vazia.")
        except (OSError, EOFError, wave.Error) as error:
            raise TTSUnavailable("Forneça um WAV PCM válido em --voice-ref.") from error
    control = "Natural Brazilian Portuguese (pt-BR) narration with clear diction and natural pauses"
    if speech_style:
        control += ", " + speech_style.replace("(", "").replace(")", "")
    model = _vox_model()
    samples = model.generate(text=f"({control}){text}",
                             reference_wav_path=str(voice_ref) if voice_ref else None)
    rate = model.tts_model.sample_rate
    signal = np.asarray(samples, dtype=np.float32).reshape(-1)
    if rate != SAMPLE_RATE or not signal.size:
        raise TTSUnavailable("VoxCPM2 precisa retornar fala válida em 48 kHz.")
    path = output.with_suffix(".wav")
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(np.rint(np.clip(signal, -1, 32767 / 32768) * 32768).astype("<i2").tobytes())
    return Speech(path, locale="pt-BR")
