"""Treat narration, schedule music cues, and master the final AAC mix."""

import json
import shutil
import subprocess
import wave
from bisect import bisect_left
from dataclasses import dataclass, replace
from fractions import Fraction
from math import ceil, isfinite
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from core.encoder import run_ffmpeg
from core.music_generator import MusicSelection, adaptive_music, procedural_bed
from core.schema import SAMPLE_RATE
from plugins._loudness import MAX_TRUE_PEAK, TARGET_LUFS, measure

FADE_SAMPLES = 30 * SAMPLE_RATE // 1000
MIX_FORMAT = f"aformat=sample_fmts=fltp:sample_rates={SAMPLE_RATE}:channel_layouts=stereo"
# Feed the compressor equally sized blocks so its main and sidechain inputs stay synchronized.
DUCK_BLOCKS = f"asetnsamples=n={SAMPLE_RATE // 100}:p=0"
DUCK_FLOOR = 10 ** (-12 / 20)
LUFS_TOLERANCE = 0.3
VOICE_FILTERS = ("highpass=f=80,lowshelf=f=140:g=1.5,highshelf=f=6000:g=1.5,"
                 "acompressor=threshold=0.12589254:ratio=3:attack=8:release=120:makeup=1,"
                 "alimiter=limit=0.84139514:attack=5:release=50:level=false:latency=true")


@dataclass(frozen=True)
class MusicBed:
    path: Path
    duration: float
    beats: tuple[float, ...]
    method: str = "provided"
    license: str | None = None
    bpm: int | None = None
    musical_key: str | None = None
    model: str | None = None
    model_revision: str | None = None
    text_encoder: str | None = None

    def beats_until(self, duration):
        if not self.beats:
            return ()
        return tuple(beat + repeat * self.duration
                     for repeat in range(int(duration / self.duration) + 1)
                     for beat in self.beats if beat + repeat * self.duration < duration)


def _write_pcm(output, samples, channels):
    pcm = np.rint(np.clip(samples, -1.0, 32767 / 32768) * 32768).astype("<i2")
    with wave.open(str(output), "wb") as destination:
        destination.setnchannels(channels)
        destination.setsampwidth(2)
        destination.setframerate(SAMPLE_RATE)
        destination.writeframes(pcm.tobytes())


def probe_duration(path):
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                             "-of", "json", str(path)], check=True, capture_output=True, text=True)
    return float(json.loads(result.stdout)["format"]["duration"])


def prepare_music(choice, configured, assets_root, work, *, topic="", tone="neutral", moods=(),
                  duration_sec=None, studio=False):
    if studio and choice is None and not configured:
        choice = "adaptive"
    if choice is None and not configured:
        return None
    if choice == "auto":
        path = work / "studio-bed.wav"
        procedural_bed(path)
        selection = MusicSelection(path, "procedural_cc0", "CC0-1.0", 120, "C major")
    elif choice == "adaptive":
        selection = adaptive_music(work / "studio-bed.wav", topic=topic, tone=tone, moods=moods,
                                   duration_sec=duration_sec)
        path = selection.path
    else:
        path = Path(choice).expanduser().resolve() if choice is not None else (assets_root / configured).resolve()
        selection = MusicSelection(path, "provided")
    if not path.is_file():
        raise ValueError(f"Trilha inexistente: {path}")
    duration = probe_duration(path)
    if not isfinite(duration) or duration <= 0:
        raise ValueError("A trilha precisa ter duração positiva")
    try:
        import librosa
    except ImportError:
        beats = ()
    else:
        pcm, rate = librosa.load(path, sr=22_050, mono=True)
        _, times = librosa.beat.beat_track(y=pcm, sr=rate, trim=False, units="time")
        beats = tuple(float(at) for at in times if 0 <= at < duration)
        if len(beats) > 2 and duration - beats[-1] + beats[0] < min(.15, np.median(np.diff(beats)) / 3):
            beats = beats[:-1]
    return MusicBed(path, duration, beats, selection.method, selection.license, selection.bpm, selection.musical_key,
                    selection.model, selection.model_revision, selection.text_encoder)


def snap_to_beat(t_sec, beats_sec, max_shift=0.15):
    """Pick the nearest beat within max_shift, with earlier beats winning ties."""
    if len(beats_sec) == 0:
        return t_sec
    index = bisect_left(beats_sec, t_sec)
    candidates = beats_sec[max(0, index - 1):index + 1]
    beat = min(candidates, key=lambda at: (round(abs(at - t_sec), 10), at))
    return beat if abs(beat - t_sec) <= max_shift + 1e-9 else t_sec


def safe_beat(timing, at, *, earliest=0.0, latest=None, grid_fps=None):
    """Snap a visual cue only to a reachable beat in the gap between spoken words."""
    latest = timing.duration_sec if latest is None else latest
    candidates = []
    for beat in timing.beats_sec:
        if abs(beat - at) > .15:
            continue
        # Some cutout poses are only available on the 12 fps drawing grid.
        actual = round(beat * grid_fps) / grid_fps if grid_fps else beat
        frame_time = ceil(actual * timing.fps - 1e-9) / timing.fps
        if (earliest <= frame_time <= latest and abs(frame_time - at) <= .15
                and not any(word.start_sec - .018 <= frame_time <= word.end_sec + .018 for word in timing.audio.words)):
            candidates.append(actual if grid_fps else frame_time)
    return snap_to_beat(at, candidates)


def attach_beats(timeline, music):
    if music is None or not music.beats:
        return timeline
    beats = music.beats_until(timeline[-1].end_sec)
    return [replace(timing, beats_sec=tuple(at - timing.start_sec for at in beats
                                          if timing.start_sec <= at < timing.end_sec)) for timing in timeline]


def _denoise_voice(source, output):
    if executable := shutil.which("deep-filter"):
        with TemporaryDirectory(dir=output.parent) as directory:
            root = Path(directory)
            padded = root / "input.wav"
            filtered = root / "filtered"
            with wave.open(str(source), "rb") as voice:
                samples = voice.getnframes()
            # The standalone build drops its lookahead tail when compensating for latency.
            # Feed 60 ms of silence so the original last syllable survives that compensation.
            run_ffmpeg("-v", "error", "-i", str(source), "-af", f"apad=pad_len={2 * FADE_SAMPLES}",
                       "-c:a", "pcm_s16le", str(padded))
            subprocess.run([executable, "--compensate-delay", "--output-dir", str(filtered), str(padded)],
                           check=True, capture_output=True)
            run_ffmpeg("-v", "error", "-i", str(filtered / padded.name), "-af",
                       f"apad=whole_len={samples},atrim=end_sample={samples}", "-c:a", "pcm_s16le", str(output))
        return True
    try:
        from df.enhance import enhance, init_df
        import torch
    except (ImportError, OSError):
        return False
    with wave.open(str(source), "rb") as voice:
        channels = voice.getnchannels()
        samples = np.frombuffer(voice.readframes(voice.getnframes()), dtype="<i2").reshape(-1, channels)
    model, state, _ = init_df(log_level="none", log_file=None)
    if state.sr() != SAMPLE_RATE:
        raise RuntimeError("DeepFilterNet precisa de um modelo de 48 kHz")
    audio = torch.from_numpy((samples.T / 32768).astype(np.float32))
    cleaned = enhance(model, state, audio, pad=True).detach().cpu().numpy().T
    if cleaned.shape != samples.shape:
        raise RuntimeError("A limpeza alterou a duração da narração")
    _write_pcm(output, cleaned, channels)
    return True


def _dasp_compressor():
    try:
        import torch
        from dasp_pytorch import compressor
    except (ImportError, OSError):
        return None
    controls = tuple(torch.tensor([value]) for value in (-18.0, 3.0, 8.0, 120.0, 3.0, 0.0))

    def compress(samples):
        # Keep FFT work bounded and feed the previous release tail to each block.
        output = np.empty_like(samples)
        block, history = 4 * SAMPLE_RATE, SAMPLE_RATE // 2
        with torch.inference_mode():
            for start in range(0, len(samples), block):
                end = min(start + block, len(samples))
                left = max(0, start - history)
                chunk = torch.from_numpy(np.ascontiguousarray(samples[left:end].T[None]))
                treated = compressor(chunk, SAMPLE_RATE, *controls)[0].detach().cpu().numpy().T
                output[start:end] = treated[start - left:]
        return output

    return compress


def _compress_rms(samples):
    hop = SAMPLE_RATE // 100
    starts = np.arange(0, len(samples), hop)
    levels = np.mean(samples ** 2, axis=1)
    powers = np.add.reduceat(levels, starts) / np.diff(np.append(starts, len(samples)))
    level_db = 10 * np.log10(np.maximum(1e-10, powers))
    targets = 10 ** (-np.maximum(level_db + 18, 0) * (1 - 1 / 3) / 20)
    attack, release = np.exp(-.01 / .008), np.exp(-.01 / .120)
    gain = 1.0
    smoothed = []
    for target in targets:
        smoothing = attack if target < gain else release
        gain = smoothing * gain + (1 - smoothing) * target
        smoothed.append(gain)
    return samples * np.interp(np.arange(len(samples)), starts, smoothed)[:, None]


def _treat_voice(source, output, compress):
    try:
        from scipy.signal import butter, sosfilt
    except ImportError:
        with wave.open(str(source), "rb") as input_wave:
            samples = input_wave.getnframes()
        run_ffmpeg("-v", "error", "-i", str(source), "-af",
                   f"{VOICE_FILTERS},apad=whole_len={samples},atrim=end_sample={samples}",
                   "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(output))
        return False
    with wave.open(str(source), "rb") as input_wave:
        channels = input_wave.getnchannels()
        if (input_wave.getsampwidth(), input_wave.getframerate()) != (2, SAMPLE_RATE):
            raise ValueError("O DSP requer WAV PCM 16 bits de 48 kHz.")
        raw = input_wave.readframes(input_wave.getnframes())
    samples = np.frombuffer(raw, dtype="<i2").reshape(-1, channels).astype(np.float32) / 32768
    if not len(samples):
        _write_pcm(output, samples, channels)
        return False
    filtered = sosfilt(butter(2, 80, btype="highpass", fs=SAMPLE_RATE, output="sos"), samples, axis=0)
    warmth = sosfilt(butter(1, 140, fs=SAMPLE_RATE, output="sos"), filtered, axis=0)
    air = sosfilt(butter(1, 6000, btype="highpass", fs=SAMPLE_RATE, output="sos"), filtered, axis=0)
    equalized = (filtered + (10 ** (1.5 / 20) - 1) * (warmth + air)).astype(np.float32)
    treated = compress(equalized) if compress is not None else _compress_rms(equalized)
    if treated.shape != samples.shape or not np.isfinite(treated).all():
        raise RuntimeError("A compressão alterou a duração da narração ou gerou amostras inválidas.")
    ceiling = 10 ** (-1.5 / 20)
    _write_pcm(output, np.clip(treated, -ceiling, ceiling), channels)
    return compress is not None


def prepare_voice(timeline, work, denoise=False):
    sources = tuple(dict.fromkeys(timing.audio.path for timing in timeline))
    processed = {}
    cleaned_any = False
    dasp_applied = False
    compress = _dasp_compressor()
    for index, source in enumerate(sources):
        cleaned = work / f"clean-{index}.wav"
        if denoise and _denoise_voice(source, cleaned):
            source_audio = cleaned
            cleaned_any = True
        else:
            source_audio = source
        output = work / f"studio-voice-{index}.wav"
        dasp_applied |= _treat_voice(source_audio, output, compress)
        processed[source] = output
    return processed, dasp_applied, cleaned_any


def _pyloudnorm_lufs(path):
    try:
        import pyloudnorm
        import soundfile
    except ImportError:
        return None
    samples, rate = soundfile.read(path)
    # pyloudnorm requires one full BS.1770 measurement block.
    if len(samples) < round(rate * .4):
        return None
    return float(pyloudnorm.Meter(rate).integrated_loudness(samples))


def _sample_at(frame, fps):
    return round(Fraction(frame * SAMPLE_RATE, fps))


def music_expression(scenes, timeline):
    curves = {
        "tense_low": ".58",
        "pulse_build": ".68+.77*(n-{start})/{span}",
        "drop_silence": "0",
        "resolve": "1.08-.48*(n-{start})/{span}",
    }
    parts = []
    for scene, timing in zip(scenes, timeline, strict=True):
        start = _sample_at(timing.start_frame, timing.fps)
        end = _sample_at(timing.start_frame + timing.frame_count, timing.fps)
        envelope = curves.get(scene.music_mood, "1").format(start=start, span=end - start)
        parts.append(f"gte(n,{start})*lt(n,{end})*({envelope})")
    return "+".join(parts)


def _mix(spec, timeline, renders, assets_root, output):
    inputs, graph, narration, effects, music_cuts = [], [], [], [], []

    def input_audio(path, *, loop=False):
        label = len(inputs)
        loop_args = ["-stream_loop", "-1"] if loop else []
        inputs.append([*loop_args, "-i", str(assets_root / path)])
        return f"[{label}:a]"

    continuous = timeline[0].continuous
    for index, (scene, timing, rendered) in enumerate(zip(spec.scenes, timeline, renders, strict=True)):
        start = _sample_at(timing.start_frame, timing.fps)
        end = _sample_at(timing.start_frame + timing.frame_count, timing.fps)
        voice = timing.audio
        if not continuous:
            label = f"[voice{index}]"
            graph.append(
                f"{input_audio(voice.path)}{MIX_FORMAT},afade=t=in:ns={FADE_SAMPLES},"
                f"afade=t=out:ss={max(0, voice.samples - FADE_SAMPLES)}:ns={FADE_SAMPLES},"
                f"apad=whole_len={end - start},atrim=end_sample={end - start},asetpts=N/SR/TB{label}"
            )
            narration.append(label)
        for effect in rendered.effects:
            if not 0 <= effect.at_sec < timing.duration_sec or not isfinite(effect.gain_db):
                raise ValueError(f"Invalid sound effect timing or gain in scene {scene.id!r}")
            at = start + round(effect.at_sec * SAMPLE_RATE)
            label = f"[effect{len(effects)}]"
            graph.append(f"{input_audio(effect.path)}{MIX_FORMAT},volume={effect.gain_db}dB,adelay={at}S:all=1{label}")
            effects.append(label)
        if scene.cut_music_for_punchline:
            at = rendered.punchline_at_sec if rendered.punchline_at_sec is not None else voice.duration_sec
            if not 0 <= at <= timing.duration_sec:
                raise ValueError(f"Punchline falls outside scene {scene.id!r}")
            cut_end = start + round(at * SAMPLE_RATE)
            music_cuts.append(f"gte(n,{max(0, cut_end - 3 * SAMPLE_RATE // 10)})*lt(n,{cut_end})")

    total = end
    if continuous:
        first, last = timeline[0].audio, timeline[-1].audio
        source_end = last.start_sample + last.samples
        length = source_end - first.start_sample
        graph.append(
            f"{input_audio(first.path)}{MIX_FORMAT},atrim=start_sample={first.start_sample}:end_sample={source_end},"
            f"asetpts=N/SR/TB,afade=t=in:ns={FADE_SAMPLES},"
            f"afade=t=out:ss={max(0, length - FADE_SAMPLES)}:ns={FADE_SAMPLES},"
            f"apad=whole_len={total},atrim=end_sample={total}[voice]"
        )
    else:
        graph.append(f"{''.join(narration)}concat=n={len(narration)}:v=0:a=1[voice]")
    tracks = ["[voice]", *effects]
    if spec.music_track:
        music = f"{input_audio(spec.music_track, loop=True)}{MIX_FORMAT},volume=-20dB,atrim=end_sample={total}"
        has_moods = any(scene.music_mood for scene in spec.scenes)
        if music_cuts or has_moods:
            gain = music_expression(spec.scenes, timeline) if has_moods else "1"
            if music_cuts:
                gain = f"if({'+'.join(music_cuts)},0,{gain})"
            music += f",aeval=exprs='val(0)*({gain})|val(1)*({gain})':channel_layout=stereo"
        graph.append(f"{music},asetpts=N/SR/TB,{DUCK_BLOCKS},asplit[bed_dry][bed_wet]")
        graph.append(f"[voice]{DUCK_BLOCKS},asplit[voice_mix][duck]")
        graph.append(f"[bed_dry]volume={DUCK_FLOOR}[floor]")
        graph.append(f"[bed_wet]volume={1 - DUCK_FLOOR}[wet]")
        graph.append("[wet][duck]sidechaincompress=threshold=0.003:ratio=14:attack=60:release=350:detection=rms[reduced]")
        graph.append("[floor][reduced]amix=inputs=2:duration=first:normalize=0[ducked]")
        tracks = ["[voice_mix]", *effects, "[ducked]"]

    graph.append(f"{''.join(tracks)}amix=inputs={len(tracks)}:duration=first:normalize=0,"
                 f"apad=whole_len={total},atrim=end_sample={total},asetpts=N/SR/TB[mix]")
    run_ffmpeg(
        "-v", "error", *(arg for entry in inputs for arg in entry), "-filter_complex", ";".join(graph),
        "-map", "[mix]", "-c:a", "pcm_f32le", str(output),
    )


def master(spec, timeline, renders, assets_root, video, output):
    mix = video.with_name("mix.wav")
    _mix(spec, timeline, renders, assets_root, mix)
    total = _sample_at(timeline[-1].start_frame + timeline[-1].frame_count, spec.fps)
    ceiling = -1.5 if spec.dynamic_edit or getattr(spec, "production_profile", None) == "studio_sota" else MAX_TRUE_PEAK
    target, peak_limit = TARGET_LUFS, ceiling - .7
    measured_lufs = _pyloudnorm_lufs(mix)
    for attempt in range(3):
        profile = f"I={target:.2f}:TP={peak_limit:.2f}:LRA=11"
        stats = measure(mix, profile)
        if not isfinite(float(stats["input_i"])):
            raise ValueError("The mixed audio is too quiet or short to measure integrated loudness")
        settings = (
            f"{profile}:measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:"
            f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:"
            f"offset={stats['target_offset']}:linear=true"
        )
        level_filter = f"loudnorm={settings}"
        if attempt == 0 and measured_lufs is not None and isfinite(measured_lufs):
            gain = target - measured_lufs
            if float(stats["input_tp"]) + gain <= peak_limit:
                level_filter = f"volume={gain:.6f}dB"
        run_ffmpeg(
            "-v", "error", "-y", "-i", str(video), "-i", str(mix), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
            "-af", f"{level_filter},aresample={SAMPLE_RATE},apad=whole_len={total},"
            f"atrim=end_sample={total},afade=t=in:ns={FADE_SAMPLES},"
            f"afade=t=out:ss={max(0, total - FADE_SAMPLES)}:ns={FADE_SAMPLES},asetpts=N/SR/TB",
            "-c:a", "aac", "-b:a", "192k", "-movie_timescale", str(SAMPLE_RATE), "-movflags", "+faststart", str(output),
        )
        encoded = measure(output)
        lufs, peak = float(encoded["input_i"]), float(encoded["input_tp"])
        if abs(lufs - TARGET_LUFS) <= LUFS_TOLERANCE + 1e-9 and peak <= ceiling:
            return
        if not isfinite(lufs) or not isfinite(peak):
            break
        target += TARGET_LUFS - lufs
        peak_limit -= max(0, peak - ceiling + .2)
    raise ValueError(f"Master outside the audio specification: {lufs} LUFS, {peak} dBTP")
