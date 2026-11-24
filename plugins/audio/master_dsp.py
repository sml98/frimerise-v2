"""Mix scene audio and master the export with a two-pass loudness filter."""

from fractions import Fraction
from math import isfinite

from core.encoder import run_ffmpeg
from core.registry import register_audio
from core.schema import SAMPLE_RATE
from plugins._loudness import MAX_TRUE_PEAK, TARGET_LUFS, measure

FADE_SAMPLES = 30 * SAMPLE_RATE // 1000
MIX_FORMAT = f"aformat=sample_fmts=fltp:sample_rates={SAMPLE_RATE}:channel_layouts=stereo"
# Feed the compressor equally sized blocks so its main and sidechain inputs stay synchronized.
DUCK_BLOCKS = f"asetnsamples=n={SAMPLE_RATE // 100}:p=0"
# Keep headroom for changes to true peak during AAC encoding and decoding.
MASTER_SETTINGS = f"I={TARGET_LUFS}:TP={MAX_TRUE_PEAK - 0.7}:LRA=11"


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
        music = f"{input_audio(spec.music_track, loop=True)}{MIX_FORMAT},volume=-24dB,atrim=end_sample={total}"
        if music_cuts:
            condition = "+".join(music_cuts)
            music += f",aeval=exprs='if({condition},0,val(0))|if({condition},0,val(1))':channel_layout=stereo"
        graph.append(f"{music},asetpts=N/SR/TB,{DUCK_BLOCKS}[music]")
        graph.append(f"[voice]{DUCK_BLOCKS},asplit[voice_mix][duck]")
        graph.append("[music][duck]sidechaincompress=threshold=0.015:ratio=8:attack=10:release=250[ducked]")
        tracks = ["[voice_mix]", *effects, "[ducked]"]

    graph.append(f"{''.join(tracks)}amix=inputs={len(tracks)}:duration=first:normalize=0,"
                 f"apad=whole_len={total},atrim=end_sample={total},asetpts=N/SR/TB[mix]")
    run_ffmpeg(
        "-v", "error", *(arg for entry in inputs for arg in entry), "-filter_complex", ";".join(graph),
        "-map", "[mix]", "-c:a", "pcm_f32le", str(output),
    )


@register_audio("master_dsp")
def master(spec, timeline, renders, assets_root, video, output):
    mix = video.with_name("mix.wav")
    _mix(spec, timeline, renders, assets_root, mix)
    stats = measure(mix, MASTER_SETTINGS)
    if not isfinite(float(stats["input_i"])):
        raise ValueError("The mixed audio is too quiet or short to measure integrated loudness")
    settings = (
        f"{MASTER_SETTINGS}:measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:"
        f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:"
        f"offset={stats['target_offset']}:linear=true"
    )
    total = _sample_at(timeline[-1].start_frame + timeline[-1].frame_count, spec.fps)
    run_ffmpeg(
        "-v", "error", "-i", str(video), "-i", str(mix), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
        "-af", f"loudnorm={settings},aresample={SAMPLE_RATE},apad=whole_len={total},"
        f"atrim=end_sample={total},asetpts=N/SR/TB",
        "-c:a", "aac", "-b:a", "192k", "-movie_timescale", str(SAMPLE_RATE), "-movflags", "+faststart", str(output),
    )
