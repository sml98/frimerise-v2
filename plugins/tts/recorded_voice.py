"""Use previously recorded dialogue and word timings from a voice manifest."""

import json
from functools import lru_cache
from pathlib import Path

from core.registry import TTSUnavailable, register_tts
from core.schema import Speech, WordTiming
from plugins.tts.edge_kokoro_tts import NATIVE_PT_BR_VOICES

ASSETS = Path(__file__).resolve().parents[2] / "assets"


@lru_cache(maxsize=8)
def _read_manifest(path, modified):
    with path.open(encoding="utf-8") as source:
        data = json.load(source)
    if isinstance(data, list):
        return {}, {entry["text"]: entry for entry in data}
    return data, {entry["text"]: entry for entry in data["recordings"]}


@register_tts("recorded_voice")
def synthesize(text, *, voice_id, speech_style, output, locale=None):
    if not voice_id:
        raise TTSUnavailable("recorded_voice requires a manifest path in voice_id")
    manifest = (ASSETS / voice_id).resolve()
    try:
        metadata, recordings = _read_manifest(manifest, manifest.stat().st_mtime_ns)
        declared_locale = metadata.get("locale")
        if locale and declared_locale != locale:
            raise TTSUnavailable(f"{manifest.name} does not declare the required locale {locale}")
        if (locale == "pt-BR" and metadata.get("provider") == "edge_tts"
                and metadata.get("voice") not in NATIVE_PT_BR_VOICES):
            raise TTSUnavailable(f"{manifest.name} does not use an approved native Edge TTS voice for pt-BR")
        entry = recordings[text]
        audio = manifest.parent / entry["audio"]
        words = tuple(WordTiming(*word) for word in entry.get("words", ()))
        if not audio.is_file():
            raise FileNotFoundError(audio)
    except (OSError, KeyError, ValueError, TypeError) as error:
        raise TTSUnavailable(f"No usable recording for the requested text in {manifest.name}") from error
    return Speech(audio, words, locale=declared_locale)
