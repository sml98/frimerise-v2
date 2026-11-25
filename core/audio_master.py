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


def _auto_bed(output):
    """Compose a sparse 120 BPM loop from oscillators and a seeded brush pulse."""
    beat_size = SAMPLE_RATE // 2
    seconds = np.arange(beat_size) / SAMPLE_RATE
    envelope = (1 - np.exp(-seconds * 190)) * np.exp(-seconds * 11) * np.clip((.5 - seconds) / .035, 0, 1)
    rng = np.random.default_rng(2026)
    loop = np.zeros((32 * beat_size, 2), dtype=np.float32)
    chords = ((48, 55, 60, 64), (45, 52, 57, 60), (41, 48, 53, 57), (43, 50, 55, 59))
    for index in range(32):
        pitch = chords[index // 8][index % 4]
        frequency = 440 * 2 ** ((pitch - 69) / 12)
        phase = 2 * np.pi * frequency * seconds
        note = (.27 * np.sin(phase) + .071 * np.sin(2 * phase) + .022 * np.sin(3 * phase)) * envelope
        brush = rng.normal(0, .115, beat_size) * np.exp(-seconds * 82)
        pulse = (.20 + .095 * (index % 4 == 0)) * np.sin(
            2 * np.pi * (55 * seconds + .53 * (1 - np.exp(-34 * seconds)))
        ) * np.exp(-seconds * 28)
        start = index * beat_size
        loop[start:start + beat_size, 0] = note * .88 + .43 * brush + pulse
        loop[start:start + beat_size, 1] = note + .37 * brush + pulse
    _write_pcm(output, loop, 2)


def probe_duration(path):
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                             "-of", "json", str(path)], check=True, capture_output=True, text=True)
    return float(json.loads(result.stdout)["format"]["duration"])


def prepare_music(choice, configured, assets_root, work):
    if choice is None and not configured:
        return None
    if choice == "auto":
        path = work / "studio-bed.wav"
        _auto_bed(path)
    else:
        path = Path(choice).expanduser().resolve() if choice is not None else (assets_root / configured).resolve()
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
    return MusicBed(path, duration, beats)


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


def _voice_board():
    try:
        from pedalboard import Compressor, HighpassFilter, HighShelfFilter, Limiter, LowShelfFilter, Pedalboard
    except (ImportError, OSError):
        return None
    return Pedalboard([HighpassFilter(cutoff_frequency_hz=80), LowShelfFilter(cutoff_frequency_hz=140, gain_db=1.5),
                      HighShelfFilter(cutoff_frequency_hz=6000, gain_db=1.5),
                      Compressor(threshold_db=-18.0, ratio=3.0, attack_ms=8.0, release_ms=120.0),
                      Limiter(threshold_db=-1.5)])


def _treat_voice(source, output, board):
    with wave.open(str(source), "rb") as input_wave:
        channels, samples = input_wave.getnchannels(), input_wave.getnframes()
        if board is not None:
            with wave.open(str(output), "wb") as target:
                target.setparams(input_wave.getparams())
                while data := input_wave.readframes(SAMPLE_RATE):
                    signal = np.frombuffer(data, dtype="<i2").reshape(-1, channels)
                    audio = (signal.T / 32768).astype(np.float32, order="C")
                    treated = board(audio, SAMPLE_RATE, reset=False).T
                    if treated.shape != signal.shape:
                        raise RuntimeError("Pedalboard alterou a duração da narração")
                    pcm = np.rint(np.clip(treated, -1, 32767 / 32768) * 32768).astype("<i2")
                    target.writeframesraw(pcm.tobytes())
            return
    run_ffmpeg("-v", "error", "-i", str(source), "-af",
               f"{VOICE_FILTERS},apad=whole_len={samples},atrim=end_sample={samples}",
               "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(output))


def prepare_voice(timeline, work, denoise=False):
    sources = tuple(dict.fromkeys(timing.audio.path for timing in timeline))
    processed = {}
    cleaned_any = False
    pedalboard_applied = False
    for index, source in enumerate(sources):
        cleaned = work / f"clean-{index}.wav"
        if denoise and _denoise_voice(source, cleaned):
            source_audio = cleaned
            cleaned_any = True
        else:
            source_audio = source
        board = _voice_board()
        output = work / f"studio-voice-{index}.wav"
        _treat_voice(source_audio, output, board)
        processed[source] = output
        pedalboard_applied = board is not None
    return processed, pedalboard_applied, cleaned_any


def _sample_at(frame, fps):
    return round(Fraction(frame * SAMPLE_RATE, fps))


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
        if music_cuts:
            condition = "+".join(music_cuts)
            music += f",aeval=exprs='if({condition},0,val(0))|if({condition},0,val(1))':channel_layout=stereo"
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
    target, peak_limit = TARGET_LUFS, MAX_TRUE_PEAK - .7
    for _ in range(3):
        profile = f"I={target:.2f}:TP={peak_limit:.2f}:LRA=11"
        stats = measure(mix, profile)
        if not isfinite(float(stats["input_i"])):
            raise ValueError("The mixed audio is too quiet or short to measure integrated loudness")
        settings = (
            f"{profile}:measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:"
            f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:"
            f"offset={stats['target_offset']}:linear=true"
        )
        run_ffmpeg(
            "-v", "error", "-y", "-i", str(video), "-i", str(mix), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
            "-af", f"loudnorm={settings},aresample={SAMPLE_RATE},apad=whole_len={total},"
            f"atrim=end_sample={total},afade=t=in:ns={FADE_SAMPLES},"
            f"afade=t=out:ss={max(0, total - FADE_SAMPLES)}:ns={FADE_SAMPLES},asetpts=N/SR/TB",
            "-c:a", "aac", "-b:a", "192k", "-movie_timescale", str(SAMPLE_RATE), "-movflags", "+faststart", str(output),
        )
        encoded = measure(output)
        lufs, peak = float(encoded["input_i"]), float(encoded["input_tp"])
        if abs(lufs - TARGET_LUFS) <= LUFS_TOLERANCE + 1e-9 and peak <= MAX_TRUE_PEAK:
            return
        if not isfinite(lufs) or not isfinite(peak):
            break
        target += TARGET_LUFS - lufs
        peak_limit -= max(0, peak - MAX_TRUE_PEAK + .2)
    raise ValueError(f"Master outside the audio specification: {lufs} LUFS, {peak} dBTP")
