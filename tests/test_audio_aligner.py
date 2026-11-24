import math
import wave
from array import array
from pathlib import Path
from types import SimpleNamespace

import pytest

from cartridges.cutout_paper.renderer import Options, stamp_time
from core import audio_aligner, registry
from core.audio_aligner import Boundary, _automatic_cuts, caption_blocks
from core.encoder import run_ffmpeg
from core.engine import render
from core.schema import SAMPLE_RATE, AudioTrackSpec, EpisodeSpec, SceneRender, SceneTiming, Speech, WordTiming
from plugins.audio.master_dsp import _mix


def _episode():
    return EpisodeSpec("Narração", "fixture", width=160, height=90, tts_plugin="fixture_continuous",
                       tts_fallback_plugin=None, tts_locale="pt-BR",
                       scenes=[
                           {"id": "a", "plugin": "fixture_continuous", "narration": "A água encheu o vaso.",
                            "pause_after_sec": 4.0},
                           {"id": "b", "plugin": "fixture_continuous", "narration": "Depois a chuva passou.",
                            "pause_after_sec": 3.0},
                       ])


def _tone(path, duration):
    data = array("h", (round(6600 * math.sin(2 * math.pi * 375 * i / SAMPLE_RATE))
                       for i in range(round(duration * SAMPLE_RATE))))
    with wave.open(str(path), "wb") as file:
        file.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        file.writeframes(data.tobytes())
    return path


def _transcript():
    return tuple(WordTiming(text, at, at + .12) for text, at in (
        ("A", .12), ("água", .25), ("enxou", .46), ("o", .79), ("vaso.", .9),
        ("Depois", 1.27), ("a", 1.45), ("chuva", 1.63), ("passou.", 1.85),
    ))


def test_one_tts_take_and_frames_follow_audio_instead_of_scene_pauses(tmp_path, monkeypatch):
    spec = _episode()
    calls = []
    scenes = []

    def synthesize(text, *, voice_id, speech_style, output, locale, continuous=False):
        assert continuous is True
        calls.append(text)
        return Speech(_tone(output.with_suffix(".wav"), 2.075), locale=locale)

    def style(spec, scene, timing, media):
        scenes.append(timing)
        frame = bytes((190, 170, 120)) * (spec.width * spec.height)
        return SceneRender(frame for _ in range(timing.frame_count))

    monkeypatch.setitem(registry._plugins, ("tts", spec.tts_plugin), synthesize)
    monkeypatch.setitem(registry._plugins, ("style", spec.scenes[0].plugin), style)
    monkeypatch.setattr(audio_aligner, "transcribe_words", lambda *args: _transcript())
    _, report = render(spec, tmp_path / "continuous.mp4", continuous=True, alignment=tmp_path / "alignment.json")
    assert calls == ["A água encheu o vaso. Depois a chuva passou."]
    assert report.duration_sec == 50 / 24
    assert report.integrated_lufs == pytest.approx(-14, abs=.5)
    first, second = scenes
    assert first.audio.path == second.audio.path
    assert first.audio.start_sample == 0 and second.audio.start_sample == first.audio.samples
    assert first.end_sec == second.start_sec and first.progress(first.duration_sec * .5) == .5
    assert first.progress(-1) == 0 and second.progress(100) == 1
    assert first.audio.words[-1].text == "vaso." and second.audio.words[0].text == "Depois"
    assert first.caption_blocks[0].text == "A água encheu o vaso."
    assert "caption_blocks" in (tmp_path / "alignment.json").read_text()


def test_external_audio_preserves_asr_words_and_has_no_fade_at_the_visual_cut(tmp_path, monkeypatch):
    spec = _episode()
    audio = _tone(tmp_path / "external.wav", 2.075)
    monkeypatch.setattr(audio_aligner, "transcribe_words", lambda *args: _transcript())
    spec, timeline = audio_aligner.align_audio(spec, audio, tmp_path)
    assert timeline[0].caption_blocks[0].text == "A água enxou o vaso."
    _mix(spec, timeline, [SceneRender(()), SceneRender(())], tmp_path, tmp_path / "mix.wav")
    raw = tmp_path / "mix.pcm"
    run_ffmpeg("-v", "error", "-i", str(tmp_path / "mix.wav"), "-ac", "1", "-f", "s16le", str(raw))
    samples = array("h", raw.read_bytes())
    cut = timeline[1].audio.start_sample
    assert max(abs(value) for value in samples[:80]) < 1000
    assert max(abs(value) for value in samples[cut - 80:cut + 80]) > 4000
    assert len(samples) == 50 * (SAMPLE_RATE // 24)


def test_automatic_ten_minute_timeline_prefers_natural_pauses():
    fps = 30
    candidates = [Boundary(at * fps, at * 2, .35 if at % 7 == 0 else .04, at % 7 == 0)
                  for at in range(1, 600)]
    cuts = _automatic_cuts(candidates, 600 * fps, fps, True)
    boundaries = [0, *(cut.frame for cut in cuts), 600 * fps]
    durations = [(right - left) / fps for left, right in zip(boundaries, boundaries[1:])]
    assert sum(durations) == 600 and 5 <= min(durations) <= max(durations) <= 9
    assert all(cut.sentence for cut in cuts)


def test_automatic_uses_a_later_pause_to_avoid_a_short_middle_scene():
    fps = 24
    cuts = _automatic_cuts(
        [Boundary(198, 20, .46, True), Boundary(291, 32, .36, True), Boundary(326, 36, .20)],
        451, fps, True,
    )
    assert [cut.frame for cut in cuts] == [198, 326]


def test_caption_branch_does_not_end_a_block_on_a_connector():
    text = "O barco encontrou terra no fim do dia e a tripulação desceu."
    words = tuple(WordTiming(token, index * .25, index * .25 + .2) for index, token in enumerate(text.split()))
    blocks = caption_blocks(words)
    assert " ".join(block.text for block in blocks) == text
    assert all(audio_aligner.word_key(block.words[-1].text) not in audio_aligner.DANGLING for block in blocks)


def test_caption_boundary_respects_overlapping_recognizer_timestamps():
    words = (
        WordTiming("O", .1, .3),
        WordTiming("falante", .3, .55),
        WordTiming("terminou.", .55, .8),
        WordTiming("Depois", .74, .99),
        WordTiming("voltou.", 1, 1.3),
    )
    blocks = caption_blocks(words)
    assert tuple(word for block in blocks for word in block.words) == words
    assert all(first.end_sec <= second.start_sec for first, second in zip(blocks, blocks[1:]))


def test_stamp_cues_follow_duration_or_the_aligned_word():
    audio = AudioTrackSpec(Path("source.wav"), (WordTiming("munições", 2.25, 2.55),), samples=4 * SAMPLE_RATE)
    short = SceneTiming(audio, 0, 3 * 30, 30, continuous=True)
    long = SceneTiming(audio, 90, 25 * 30, 30, continuous=True)
    options = Options(stamp_at_sec=4)
    assert stamp_time(options, short) == 1.5
    assert stamp_time(options, long) == 12.5
    assert stamp_time(Options(stamp_at_word="munições"), long) == 2.25


def test_whisper_retries_cpu_if_cuda_fails_while_iterating(monkeypatch):
    import ctranslate2

    seen = []

    class Model:
        def __init__(self, device):
            self.device = device

        def transcribe(self, path, **kwargs):
            seen.append((self.device, kwargs))

            def segments():
                if self.device == "cuda":
                    raise RuntimeError("CUDA library missing")
                yield SimpleNamespace(words=[SimpleNamespace(word=" voz", start=.23, end=.52)])

            return segments(), None

    def model(name, device):
        return Model(device)

    model.cache_clear = lambda: None
    monkeypatch.setattr(audio_aligner, "_whisper_model", model)
    monkeypatch.setattr(ctranslate2, "get_cuda_device_count", lambda: 1)
    assert audio_aligner.transcribe_words(Path("source.wav"), "pt-BR") == (WordTiming("voz", .23, .52),)
    assert [device for device, _ in seen] == ["cuda", "cpu"]
    assert all(options["word_timestamps"] and options["language"] == "pt" for _, options in seen)
