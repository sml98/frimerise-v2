import wave
from types import SimpleNamespace

import numpy as np
import pytest

from cartridges.motion_editorial.foley import transition_times
from core import tts_engine
from core.audio_master import MusicBed, attach_beats, safe_beat, snap_to_beat
from core.schema import SAMPLE_RATE, AudioTrackSpec, SceneTiming, Speech, WordTiming
from core.tts_pronunciation import pronounce


def test_beat_cues_keep_scene_endpoints_and_avoid_spoken_frames(tmp_path):
    voice = AudioTrackSpec(tmp_path / "voice.wav", (WordTiming("fala", .42, .58),), samples=SAMPLE_RATE)
    timing = SceneTiming(voice, 30, 30, 30, continuous=True)
    music = MusicBed(tmp_path / "music.wav", 1.0, (.10, .48, .62, .67))
    (aligned,) = attach_beats([timing], music)
    assert (aligned.start_frame, aligned.end_sec) == (timing.start_frame, timing.end_sec)
    assert snap_to_beat(.50, np.array([.40, .60])) == .40
    assert snap_to_beat(.84, []) == .84 and snap_to_beat(.84, [.50]) == .84
    assert safe_beat(aligned, .52) == pytest.approx(19 / 30)
    assert safe_beat(aligned, .48, latest=.58) == .48
    assert transition_times(aligned) == pytest.approx((.10, .70))


def test_pronunciation_is_only_sent_to_the_synthesizer(monkeypatch, tmp_path):
    original = "O QR Code chegou depois de ARPANET, NSFNET, World Wide Web e NeXT."
    expected = "O Quê Érre Côud chegou depois de Arpanét, Ene Esse Efe Nét, Uórld Uáid Uéb e Nékst."

    def synthesize(text, *, voice_id, speech_style, output, locale):
        assert text == expected and locale == "pt-BR"
        words = tuple(WordTiming(word, i / 5, (i + 1) / 5) for i, word in enumerate(text.split()))
        return Speech(output, words, locale=locale)

    monkeypatch.setattr(tts_engine, "get_plugin", lambda *_: synthesize)
    speech = tts_engine.synthesize_one("edge_kokoro_tts", original, output=tmp_path / "voice.wav",
                                      voice_id=None, speech_style=None, locale="pt-BR")
    assert speech.spoken_text == expected
    assert " ".join(word.text for word in speech.words) == original
    assert pronounce("next XARPANET", "pt-BR") == "next XARPANET"
    assert pronounce(original, "en-US") == original


def test_voxcpm2_passes_reference_and_writes_48khz_without_importing_torch(monkeypatch, tmp_path):
    ref = tmp_path / "reference.wav"
    with wave.open(str(ref), "wb") as audio:
        audio.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0\0" * 480)

    def generate(*, text, reference_wav_path):
        assert reference_wav_path == str(ref)
        assert "Brazilian Portuguese" in text and text.endswith("Uma história.")
        return np.ones(960, dtype=np.float32) / 4

    monkeypatch.setattr(tts_engine, "_vox_model", lambda: SimpleNamespace(
        generate=generate, tts_model=SimpleNamespace(sample_rate=SAMPLE_RATE),
    ))
    result = tts_engine.voxcpm2("Uma história.", voice_id=None, speech_style=None, output=tmp_path / "voice",
                                 locale="pt-BR", continuous=True, voice_ref=ref)
    with wave.open(str(result.path), "rb") as audio:
        assert (audio.getframerate(), audio.getnframes(), result.locale) == (48_000, 960, "pt-BR")
