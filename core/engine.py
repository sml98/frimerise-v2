"""Render an episode using the measured narration as the timeline."""

import argparse
import json
import logging
import re
import wave
from dataclasses import asdict, replace
from fractions import Fraction
from inspect import signature
from math import ceil
from pathlib import Path
from tempfile import TemporaryDirectory

from .audio_aligner import align_audio
from .encoder import encode_frames, run_ffmpeg
from .registry import TTSUnavailable, discover, get_plugin
from .schema import SAMPLE_RATE, AudioTrackSpec, EpisodeSpec, SceneTiming


def _synthesize(spec, work, backend, voice, *, continuous=False):
    synthesize = get_plugin("tts", backend)
    options = {"locale": spec.tts_locale} if spec.tts_locale else {}
    if continuous and "continuous" in signature(synthesize).parameters:
        options["continuous"] = True
    texts = [scene.narration for scene in spec.scenes]
    if continuous:
        texts = [" ".join(texts)]
    speeches = [
        synthesize(text, voice_id=voice, speech_style=spec.speech_style, output=work / f"tts-{index}", **options)
        for index, text in enumerate(texts)
    ]
    if spec.tts_locale and any(speech.locale != spec.tts_locale for speech in speeches):
        raise TTSUnavailable(f"{backend} did not confirm the required narration locale {spec.tts_locale}")
    return speeches


def _takes(spec, work, *, continuous=False):
    try:
        return _synthesize(spec, work, spec.tts_plugin, spec.voice_id, continuous=continuous)
    except TTSUnavailable as error:
        fallback = spec.tts_fallback_plugin
        if not fallback or fallback == spec.tts_plugin:
            raise
        logging.warning("%s failed (%s). Synthesizing the episode with %s.", spec.tts_plugin, error, fallback)
        return _synthesize(spec, work, fallback, spec.fallback_voice_id, continuous=continuous)


def _prepare_audio(spec, work):
    speeches = _takes(spec, work)
    timeline = []
    start = 0
    for index, (scene, speech) in enumerate(zip(spec.scenes, speeches, strict=True)):
        wav = work / f"speech-{index}.wav"
        run_ffmpeg("-v", "error", "-i", str(speech.path), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), str(wav))
        with wave.open(str(wav)) as voice:
            samples = voice.getnframes()
        if not samples:
            raise ValueError(f"Scene {scene.id!r} has no narration samples")
        words = speech.words or get_plugin("transcriber", spec.transcriber_plugin)(wav)
        audio = AudioTrackSpec(wav, tuple(words), samples=samples, locale=speech.locale)
        duration = Fraction(samples, SAMPLE_RATE) + Fraction(str(scene.pause_after_sec))
        count = ceil(duration * spec.fps)
        timeline.append(SceneTiming(audio, start, count, spec.fps))
        start += count
    return timeline


def _frames(timeline, renders):
    for timing, rendered in zip(timeline, renders, strict=True):
        for _, frame in zip(range(timing.frame_count), rendered.frames, strict=True):
            yield frame


def _produce(spec, timeline, assets, services, directory, keyframes=None):
    styles, media, master, audit = services
    resources = [media(scene, timing, assets) if media else {}
                 for scene, timing in zip(spec.scenes, timeline, strict=True)]
    renders = [styles[scene.plugin](spec, scene, timing, resource)
               for scene, timing, resource in zip(spec.scenes, timeline, resources, strict=True)]
    count = sum(timing.frame_count for timing in timeline)
    directory.mkdir()
    video, final = directory / "video.mp4", directory / "final.mp4"
    encoder = encode_frames(_frames(timeline, renders), width=spec.width, height=spec.height,
                            fps=spec.fps, frame_count=count, output=video)
    master(spec, timeline, renders, assets, video, final)
    return final, encoder, audit(final, spec=spec, frame_count=count, keyframes=keyframes)


def _write_alignment(path, spec, timeline):
    data = []
    for scene, timing in zip(spec.scenes, timeline, strict=True):
        start = timing.start_sec
        data.append({
            "id": scene.id, "start_sec": start, "end_sec": timing.end_sec, "duration_sec": timing.duration_sec,
            "words": [{"word": word.text, "start_sec": start + word.start_sec, "end_sec": start + word.end_sec}
                      for word in timing.audio.words],
            "caption_blocks": [{"text": block.text, "start_sec": start + block.start_sec, "end_sec": start + block.end_sec}
                               for block in timing.caption_blocks],
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fps": spec.fps, "scenes": data}, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def render(spec, output, *, assets_root=Path.cwd(), keyframes=None, audio=None, continuous=False,
           auto_scenes=False, alignment=None, reference_text=None):
    """Render and audit in a temporary directory before publishing the MP4."""
    discover()
    output = Path(output).absolute()
    if output.suffix.lower() != ".mp4":
        raise ValueError("The render destination must be an .mp4 file")
    continuous = audio is not None or continuous or spec.audio_mode == "continuous"
    if (auto_scenes or alignment) and not continuous:
        raise ValueError("Use --audio ou --continuous para gerar o alinhamento contínuo.")
    if alignment and Path(alignment).resolve() == output:
        raise ValueError("O alinhamento precisa de um destino diferente do MP4.")
    if audio is not None and Path(audio).resolve() in (output, Path(alignment).resolve() if alignment else None):
        raise ValueError("Use destinos diferentes do arquivo de áudio original.")
    output.parent.mkdir(parents=True, exist_ok=True)
    assets_root = Path(assets_root)
    with TemporaryDirectory(prefix=".frimerise-", dir=output.parent) as temporary:
        work = Path(temporary)
        if continuous:
            source = Path(audio).resolve() if audio is not None else _takes(spec, work, continuous=True)[0].path
            spec, timeline = align_audio(spec, source, work, auto_scenes=auto_scenes,
                                         reference_text=reference_text)
        else:
            timeline = _prepare_audio(spec, work)
        styles = {name: get_plugin("style", name) for name in dict.fromkeys(scene.plugin for scene in spec.scenes)}
        media = get_plugin("media", spec.media_plugin) if spec.media_plugin else None
        master = get_plugin("audio", spec.audio_plugin)
        audit = get_plugin("qc", spec.qc_plugin)
        services = styles, media, master, audit
        plan = get_plugin("exports", spec.niche_skill, optional=True) if spec.export_shorts_clips else None
        final, encoder, report = _produce(spec, timeline, assets_root, services, work / "main", keyframes)
        exports = {output: final}
        for clip in plan(spec, timeline) if plan else ():
            if (not re.fullmatch(r"[a-z0-9_]+", clip.suffix)
                    or not 0 <= clip.first_scene < clip.end_scene <= len(timeline)):
                raise ValueError(f"Invalid export clip: {clip}")
            destination = output.with_stem(f"{output.stem}_{clip.suffix}")
            if destination in exports:
                raise ValueError(f"Duplicate export destination: {destination}")
            span = slice(clip.first_scene, clip.end_scene)
            variant = replace(spec, scenes=spec.scenes[span], aspect_ratio=clip.aspect_ratio,
                              width=clip.width, height=clip.height, export_shorts_clips=False)
            offset = timeline[clip.first_scene].start_frame
            times = [replace(timing, start_frame=timing.start_frame - offset) for timing in timeline[span]]
            exports[destination], _, _ = _produce(variant, times, assets_root, services, work / f"clip-{clip.suffix}")
        for destination, staged in exports.items():
            staged.replace(destination)
        if alignment:
            _write_alignment(Path(alignment), spec, timeline)
    return encoder, report


def add_audio_arguments(parser):
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--audio", type=Path, help="continuous narration file (MP3, WAV, or another FFmpeg audio format)")
    mode.add_argument("--continuous", action="store_true", help="synthesize the complete script in one take")
    parser.add_argument("--tts", help="override the registered TTS backend")
    parser.add_argument("--voice", help="override the TTS voice")
    parser.add_argument("--alignment", type=Path, help="write scene, word and caption timestamps to JSON")


def audio_overrides(spec, args):
    updates = {}
    if args.tts:
        updates["tts_plugin"] = args.tts
        if not args.voice:
            updates["voice_id"] = None
    if args.voice:
        updates["voice_id"] = args.voice
    return replace(spec, **updates) if updates else spec


def _automatic_spec(args, narration):
    episode = args.episode
    title = (episode or args.audio).stem.replace("_", " ")
    style = args.style or "crayon_book"
    aspect = args.aspect or "16:9"
    width, height = (1080, 1920) if aspect == "9:16" else (1920, 1080)
    return EpisodeSpec(title, style, aspect_ratio=aspect, width=width, height=height,
                       fps=30 if style == "cutout_paper" else 24,
                       tts_plugin="edge_kokoro_tts", tts_locale=args.locale,
                       scenes=[{"id": "s01", "plugin": style, "narration": narration or title}])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episode", type=Path, nargs="?", help="episode JSON or plain-text script")
    parser.add_argument("output", type=Path)
    parser.add_argument("--assets-root", type=Path, default=Path.cwd())
    parser.add_argument("--keyframes", type=Path, help="directory for the 10%%, 50%% and 90%% audit PNGs")
    parser.add_argument("--auto-scenes", action="store_true", help="segment a single-template episode at speech pauses")
    parser.add_argument("--style", help="style for an audio-only or plain-text episode")
    parser.add_argument("--aspect", choices=("9:16", "16:9"))
    parser.add_argument("--locale", help="speech locale for an audio-only or plain-text episode")
    add_audio_arguments(parser)
    args = parser.parse_args()
    automatic = args.episode is None or args.episode.suffix.lower() == ".txt"
    if args.episode is None and args.audio is None:
        parser.error("informe um episódio JSON, um roteiro TXT ou --audio")
    if automatic and args.audio is None and not args.continuous:
        parser.error("um roteiro TXT requer --audio ou --continuous")
    reference = (args.episode.read_text(encoding="utf-8").strip() if args.episode else "") if automatic else None
    if automatic and args.audio is None and not reference:
        parser.error("informe um roteiro TXT com narração")
    spec = _automatic_spec(args, reference) if automatic else EpisodeSpec.from_json(args.episode.read_bytes())
    if not automatic and args.aspect:
        width, height = (1080, 1920) if args.aspect == "9:16" else (1920, 1080)
        spec = replace(spec, aspect_ratio=args.aspect, width=width, height=height)
    encoder, report = render(audio_overrides(spec, args), args.output,
                             assets_root=args.assets_root, keyframes=args.keyframes, audio=args.audio,
                             continuous=args.continuous, auto_scenes=automatic or args.auto_scenes,
                             alignment=args.alignment,
                             reference_text=reference)
    print(json.dumps({"encoder": encoder, **asdict(report)}, indent=2))


if __name__ == "__main__":
    main()
