"""Gemini single-speaker TTS over the REST Interactions API."""

import base64
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from core.registry import TTSUnavailable, register_tts
from core.schema import Speech


def _api_key():
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if key and key.strip():
        return key.strip()
    db = Path(os.environ.get("FRIMERISE_DB", "frimerise.db")).expanduser().resolve()
    if db.is_file():
        try:
            with closing(sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)) as connection:
                row = connection.execute(
                    "SELECT value FROM settings WHERE upper(key) = 'GEMINI_API_KEY' LIMIT 1"
                ).fetchone()
        except sqlite3.Error as error:
            raise TTSUnavailable("Could not read GEMINI_API_KEY from frimerise.db settings") from error
        if row and isinstance(row[0], str) and row[0].strip():
            return row[0].strip()
    raise TTSUnavailable("Set GEMINI_API_KEY or add it to the frimerise.db settings table")


@register_tts("gemini_tts")
def synthesize(text, *, voice_id, speech_style, output, locale=None):
    if locale:
        raise TTSUnavailable(f"Gemini TTS cannot enforce a fixed narration locale ({locale})")
    content = {"type": "text", "text": text}
    if speech_style:
        content["annotations"] = [{"type": "speech_metadata", "style": speech_style}]
    body = {
        "model": os.environ.get("FRIMERISE_GEMINI_TTS_MODEL", "gemini-3.8-flash-tts"),
        "input": [{"type": "user_input", "content": [content]}],
        "response_format": {"type": "audio", "mime_type": "audio/wav"},
        "generation_config": {"speech_config": [{"voice": voice_id or "Orus"}]},
        "store": False,
    }
    request = Request(
        "https://generativelanguage.googleapis.com/v1beta/interactions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": _api_key()},
    )
    try:
        with urlopen(request, timeout=90) as response:
            result = json.load(response)
        audio = [part["data"] for step in result.get("steps", []) if step["type"] == "model_output"
                 for part in step["content"] if part["type"] == "audio"]
        data = base64.b64decode(audio[-1], validate=True)
    except HTTPError as error:
        raise TTSUnavailable(f"Gemini TTS returned HTTP {error.code}") from error
    except (URLError, TimeoutError, AttributeError, IndexError, KeyError, TypeError, ValueError) as error:
        raise TTSUnavailable("Gemini TTS did not return usable audio") from error
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise TTSUnavailable("Gemini TTS did not return a WAV file")
    path = output.with_suffix(".wav")
    path.write_bytes(data)
    return Speech(path)
