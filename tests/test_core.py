import base64
import json
import math
import shutil
import sqlite3
import wave
from array import array
from contextlib import closing
from fractions import Fraction
from io import BytesIO
from pathlib import Path

import pytest
import edge_tts
from PIL import Image
from pydantic import ValidationError

import plugins
from core import encoder, registry
from core.engine import _prepare_audio, render
from core.schema import SAMPLE_RATE, EpisodeSpec, ExportClip, SceneRender, SoundEffect, Speech, WordTiming
from core.text_utils import fit_text
from plugins.audio.master_dsp import _mix
from plugins.qc.ffprobe_audit import audit
from plugins.tts import edge_kokoro_tts, gemini_tts


def episode(**changes):
    data = {
        "title": "Teste de integração",
        "niche_skill": "test",
        "width": 160,
        "height": 90,
        "tts_plugin": "test_audio_first",
        "scenes": [{"id": "s01", "plugin": "test_style", "narration": "Primeira", "pause_after_sec": 0.35}],
    }
    return EpisodeSpec.from_json(json.dumps({**data, **changes}))


def wav_fixture(path, size, *, frequency=253, amplitude=6400, stereo=False):
    samples = array("h")
    for index in range(size):
        sample = round(amplitude * math.cos(2 * math.pi * frequency * index / SAMPLE_RATE))
        samples.append(sample)
        if stereo:
            samples.append(-sample)
    with wave.open(str(path), "wb") as audio:
        audio.setparams((2 if stereo else 1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        audio.writeframes(samples.tobytes())
    return path


@pytest.fixture
def ffmpeg_tools():
    assert shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg and FFprobe are required"


def test_discovery(tmp_path, monkeypatch):
    (tmp_path / "phase_one_test_style.py").write_text(
        "from core.registry import register_style\n"
        "@register_style('phase_one_test_style')\n"
        "def draw():\n"
        "    return 'discovered'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(plugins, "__path__", [*plugins.__path__, str(tmp_path)])
    registry.discover()
    assert registry.get_plugin("style", "phase_one_test_style")() == "discovered"
    for category, name in (
        ("tts", "gemini_tts"), ("tts", "edge_kokoro_tts"), ("transcriber", "whisper"),
        ("media", "local_pack"), ("audio", "master_dsp"), ("qc", "ffprobe_audit"),
    ):
        assert callable(registry.get_plugin(category, name))


def test_json_contract_and_text_fitting():
    valid = episode(scenes=[{"id": "s1", "plugin": "anything", "narration": "Olá", "background": "paper"}])
    assert valid.scenes[0].options == {"background": "paper"}
    with pytest.raises(ValidationError, match="match aspect_ratio"):
        episode(width=162)
    with pytest.raises(ValidationError, match="pause_after_sec"):
        episode(scenes=[{"id": "s1", "plugin": "anything", "narration": "Olá", "pause_after_sec": -1}])
    with pytest.raises(ValidationError, match="IDs must be unique"):
        episode(scenes=[{"id": "s1", "plugin": "anything", "narration": "Olá"}] * 2)

    text = "Narração legível supercalifragilisticamente em toda a tela"
    layout = fit_text(text, (150, 130), max_size=24, stroke_width=2)
    assert "\n" in layout.text
    assert "".join(layout.text.split()) == "".join(text.split())
    left, top, right, bottom = layout.bbox
    assert right - left <= 150 and bottom - top <= 130


def test_encoder_uses_first_working_backend(monkeypatch):
    monkeypatch.setattr(Path, "glob", lambda *_: [Path("/dev/dri/renderD128")])
    tried = []

    def probe(*args):
        name = args[args.index("-c:v") + 1]
        tried.append(name)
        if name == "h264_nvenc":
            raise RuntimeError("no NVIDIA device")

    monkeypatch.setattr(encoder, "run_ffmpeg", probe)
    assert encoder.select_encoder(160, 90, 24)[0] == "h264_vaapi"
    assert tried == ["h264_nvenc", "h264_vaapi"]


def test_audio_first_pipeline(tmp_path, monkeypatch, ffmpeg_tools):
    root = tmp_path / "assets"
    root.mkdir()
    wav_fixture(root / "music.wav", SAMPLE_RATE, frequency=401)
    wav_fixture(root / "fx.wav", 4800, frequency=787, amplitude=4000)
    observed = []

    def synthesize(text, *, voice_id, speech_style, output):
        samples = {"Primeira": 60_001, "Segunda": 72_000}[text]
        observed.append(text)
        words = (WordTiming(text, 0.0, 0.7),) if text == "Primeira" else ()
        return Speech(wav_fixture(output.with_suffix(".wav"), samples), words)

    def transcribe(path):
        with wave.open(str(path)) as audio:
            assert audio.getnframes() == 72_000
        return (WordTiming("Segunda", 0.0, 0.8),)

    def style(spec, scene, timing, media):
        assert observed[:2] == ["Primeira", "Segunda"]
        assert timing.audio.words[0].text == scene.narration
        assert timing.audio.path.is_file()
        observed.append((timing.start_frame, timing.frame_count, timing.audio.samples))
        color = (201, 22, 15) if scene.id == "s1" else (16, 22, 211)
        frame = bytes(color) * (spec.width * spec.height)
        return SceneRender((frame for _ in range(timing.frame_count)), (SoundEffect(media["beat"], 0.4),))

    monkeypatch.setitem(registry._plugins, ("tts", "test_audio_first"), synthesize)
    monkeypatch.setitem(registry._plugins, ("transcriber", "test_transcribe"), transcribe)
    monkeypatch.setitem(registry._plugins, ("style", "test_style"), style)
    monkeypatch.setitem(registry._plugins, ("exports", "test"),
                        lambda spec, timeline: [ExportClip("short_01", 1, 2, "9:16", 90, 160)])
    scenes = [
        {"id": "s1", "plugin": "test_style", "narration": "Primeira", "pause_after_sec": 0.35},
        {"id": "s2", "plugin": "test_style", "narration": "Segunda", "pause_after_sec": 0.25,
         "cut_music_for_punchline": True},
    ]
    for scene in scenes:
        scene["media"] = {"beat": "fx.wav"}
    spec = episode(scenes=scenes, media_plugin="local_pack", music_track="music.wav",
                   transcriber_plugin="test_transcribe", export_shorts_clips=True)
    video = tmp_path / "episode.mp4"
    name, report = render(spec, video, assets_root=root, keyframes=tmp_path / "frames")

    assert name in {"h264_nvenc", "h264_vaapi", "libx264"}
    assert observed[2:] == [(0, 39, 60_001), (39, 42, 72_000), (0, 42, 72_000)]
    assert report.frame_count == 81
    assert report.duration_sec == float(Fraction(81, 24))
    assert report.integrated_lufs == pytest.approx(-14.0, abs=0.5)
    assert report.true_peak_dbtp <= -1.0
    assert video.stat().st_size > 0
    assert audit(tmp_path / "episode_short_01.mp4", frame_count=42).duration_sec == 42 / 24
    assert [Path(path).name for path in report.keyframes] == ["10.png", "50.png", "90.png"]
    for path in report.keyframes:
        with Image.open(path) as image:
            assert image.size == (160, 90)
            red, _, blue = image.convert("RGB").getpixel((80, 45))
            assert (red > blue) == (Path(path).name == "10.png")


def test_tts_fallback_keeps_voice_consistent(tmp_path, monkeypatch, ffmpeg_tools):
    seen = []

    def primary(text, *, voice_id, speech_style, output):
        seen.append((text, voice_id))
        if text == "B":
            raise registry.TTSUnavailable("test failure")
        return Speech(wav_fixture(output.with_suffix(".wav"), SAMPLE_RATE))

    def fallback(text, *, voice_id, speech_style, output):
        seen.append((text, voice_id))
        return Speech(wav_fixture(output.with_suffix(".wav"), 36_000), (WordTiming(text, 0.0, 0.5),))

    monkeypatch.setitem(registry._plugins, ("tts", "test_primary"), primary)
    monkeypatch.setitem(registry._plugins, ("tts", "test_fallback"), fallback)
    spec = episode(tts_plugin="test_primary", voice_id="primary", tts_fallback_plugin="test_fallback",
                   fallback_voice_id="fallback", scenes=[
                       {"id": text, "plugin": "test_style", "narration": text, "pause_after_sec": 0}
                       for text in ("A", "B")
                   ])
    timeline = _prepare_audio(spec, tmp_path)
    assert seen == [("A", "primary"), ("B", "primary"), ("A", "fallback"), ("B", "fallback")]
    assert [scene.frame_count for scene in timeline] == [18, 18]


def test_gemini_tts_request_and_sqlite_key(tmp_path, monkeypatch):
    wav = wav_fixture(tmp_path / "provider.wav", 480)
    audio = base64.b64encode(wav.read_bytes()).decode()
    db = tmp_path / "frimerise.db"
    with closing(sqlite3.connect(db)) as connection, connection:
        connection.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO settings VALUES (?, ?)", ("gemini_api_key", "fixture-key"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("FRIMERISE_DB", str(db))

    def request(call, timeout):
        assert call.get_header("X-goog-api-key") == "fixture-key"
        body = json.loads(call.data)
        assert body["generation_config"]["speech_config"] == [{"voice": "Orus"}]
        assert body["input"][0]["content"] == [
            {"type": "text", "text": "Olá", "annotations": [{"type": "speech_metadata", "style": "calmo"}]},
        ]
        return BytesIO(json.dumps({"steps": [{"type": "model_output", "content": [
            {"type": "audio", "data": audio},
        ]}]}).encode())

    monkeypatch.setattr(gemini_tts, "urlopen", request)
    result = gemini_tts.synthesize("Olá", voice_id="Orus", speech_style="calmo", output=tmp_path / "result")
    assert result.path.read_bytes() == wav.read_bytes()


def test_edge_failure_propagates(tmp_path, monkeypatch):
    async def failing_stream(self):
        yield {"type": "WordBoundary", "offset": 0, "duration": 20_000, "text": "Olá"}
        raise TimeoutError("test timeout")

    monkeypatch.setattr(edge_tts.Communicate, "stream", failing_stream)
    with pytest.raises(registry.TTSUnavailable, match="Edge TTS"):
        edge_kokoro_tts.synthesize("Olá", voice_id=None, speech_style=None, output=tmp_path / "speech")


@pytest.mark.parametrize("voice", ["pt-PT-RaquelNeural", "pt-BR-ThalitaMultilingualNeural", "kokoro:pm_alex"])
def test_strict_pt_br_rejects_unapproved_voices(tmp_path, voice):
    with pytest.raises(registry.TTSUnavailable, match="strict locale pt-BR"):
        edge_kokoro_tts.synthesize("Olá", voice_id=voice, speech_style=None, output=tmp_path / "speech", locale="pt-BR")


def test_dsp_mix_timing_and_levels(tmp_path, monkeypatch, ffmpeg_tools):
    wav_fixture(tmp_path / "music.wav", SAMPLE_RATE // 2, frequency=400, stereo=True)
    effect = wav_fixture(tmp_path / "effect.wav", 2400, frequency=800)

    def synthesize(text, *, voice_id, speech_style, output):
        return Speech(wav_fixture(output.with_suffix(".wav"), SAMPLE_RATE), (WordTiming(text, 0.0, 0.8),))

    monkeypatch.setitem(registry._plugins, ("tts", "test_audio_first"), synthesize)
    spec = episode(music_track="music.wav", scenes=[
        {"id": "s1", "plugin": "test_style", "narration": "Primeira", "pause_after_sec": 0.5,
         "cut_music_for_punchline": True},
    ])
    timeline = _prepare_audio(spec, tmp_path)
    mix = tmp_path / "mix.wav"
    _mix(spec, timeline, [SceneRender((), (SoundEffect(effect, 1.2),), 0.8525)], tmp_path, mix)
    pcm = tmp_path / "mix.pcm"
    encoder.run_ffmpeg("-v", "error", "-i", str(mix), "-f", "s16le", str(pcm))
    samples = array("h", pcm.read_bytes())

    def channels(at):
        start = 2 * round(at * SAMPLE_RATE)
        return samples[start], samples[start + 1]

    def voice(at):
        left, right = channels(at)
        return (left + right) / 2

    def music_sample(at):
        left, right = channels(at)
        return (left - right) / 2

    def music_level(start):
        return max(abs(music_sample(start + index / SAMPLE_RATE)) for index in range(1200))

    assert len(samples) == 2 * round(1.5 * SAMPLE_RATE)
    assert voice(0) == 0 and abs(voice(0.99)) < abs(voice(0.5)) / 2
    assert music_level(0.4) < music_level(1.45) / 2
    assert music_level(0.6) == 0
    assert music_sample(0.5525 - 1 / SAMPLE_RATE) != 0
    assert music_sample(0.5525) == 0
    assert music_sample(0.8525 - 1 / SAMPLE_RATE) == 0
    assert music_sample(0.8525) != 0
    assert music_level(1.45) == pytest.approx(6400 * 10 ** (-24 / 20), abs=4)
    assert voice(1.19) == 0 and voice(1.2) > 3000 and voice(1.25) == 0
