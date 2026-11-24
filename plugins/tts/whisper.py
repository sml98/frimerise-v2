"""Expose the shared word timestamp transcriber to legacy episode plugins."""

from core.audio_aligner import transcribe_words
from core.registry import register_transcriber

register_transcriber("whisper")(transcribe_words)
