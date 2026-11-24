"""Free TTS with Edge voices or optional local Kokoro voices."""

import json
import wave
from functools import cache

from core.registry import TTSUnavailable, register_tts
from core.schema import Speech, WordTiming

NATIVE_PT_BR_VOICES = frozenset({"pt-BR-AntonioNeural", "pt-BR-FranciscaNeural"})


@cache
def _pipeline(lang_code):
    from kokoro import KPipeline

    return KPipeline(lang_code=lang_code, device="cpu")


def _kokoro(text, voice, output, *, continuous=False):
    path = output.with_suffix(".wav")
    try:
        pipeline = _pipeline(voice[0])
        with wave.open(str(path), "wb") as audio:
            audio.setparams((1, 2, 24_000, 0, "NONE", "not compressed"))
            for index, (_, _, samples) in enumerate(pipeline(text, voice=voice)):
                if continuous and index:
                    raise TTSUnavailable("O Kokoro dividiu o roteiro em várias tomadas. Use outro provedor ou --audio.")
                pcm = (samples.detach().cpu().numpy().clip(-1, 1) * 32767).astype("<i2")
                audio.writeframesraw(pcm.tobytes())
    except ImportError as error:
        raise TTSUnavailable("Kokoro voices require the optional kokoro package and eSpeak NG") from error
    except (IndexError, KeyError, RuntimeError, ValueError) as error:
        raise TTSUnavailable(f"Kokoro could not synthesize with voice {voice!r}") from error
    return Speech(path)


@register_tts("edge_kokoro_tts")
def synthesize(text, *, voice_id, speech_style, output, locale=None, continuous=False):
    voice = voice_id or "pt-BR-AntonioNeural"
    native_locale = "pt-BR" if voice in NATIVE_PT_BR_VOICES else None
    if locale and locale != native_locale:
        raise TTSUnavailable(f"Edge TTS voice {voice!r} is not approved for strict locale {locale}")
    if voice_id and voice_id.startswith("kokoro:"):
        return _kokoro(text, voice_id.removeprefix("kokoro:"), output, continuous=continuous)

    import edge_tts
    from aiohttp import ClientError
    from edge_tts.exceptions import EdgeTTSException

    path = output.with_suffix(".mp3")
    metadata = output.with_suffix(".jsonl")
    try:
        speech = edge_tts.Communicate(text, voice, boundary="WordBoundary", receive_timeout=20)
        if continuous:
            requests = tuple(speech.texts)
            if len(requests) != 1:
                raise TTSUnavailable("O texto excede uma requisição do Edge TTS. Use outro provedor ou --audio.")
            speech.texts = iter(requests)
        speech.save_sync(str(path), str(metadata))
        chunks = [json.loads(line) for line in metadata.read_text(encoding="utf-8").splitlines()]
        words = tuple(
            WordTiming(chunk["text"], chunk["offset"] / 10_000_000,
                       (chunk["offset"] + chunk["duration"]) / 10_000_000)
            for chunk in chunks if chunk["type"] == "WordBoundary"
        )
    except (ClientError, EdgeTTSException, TimeoutError, ValueError, KeyError) as error:
        raise TTSUnavailable(f"Edge TTS could not synthesize with voice {voice!r}") from error
    if not path.stat().st_size:
        raise TTSUnavailable(f"Edge TTS returned no audio for voice {voice!r}")
    return Speech(path, words, locale=native_locale)
