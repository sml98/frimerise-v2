"""Render an episode using the measured narration as the timeline."""

import argparse
import json
import logging
import re
import wave
from dataclasses import asdict, replace
from fractions import Fraction
from math import ceil, isfinite
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import get_args

from .audio_aligner import add_authored_pauses, align_audio, caption_blocks
from .audio_master import attach_beats, prepare_music, prepare_voice, probe_duration
from .cutout_matting import APPROVED_MODELS
from .director import configure_production
from .editing import attach_visual_cues, beat_cuts, raster_transition
from .encoder import encode_frames, run_ffmpeg
from .html_adapter import encode_scenes, render_broll_scene
from .media_fetcher import capture_media_credits, capture_scene_images, write_media_credits
from .qc_gate import CAPTION_BANDS, qc_gate
from .registry import TTSUnavailable, discover, get_plugin
from .schema import SAMPLE_RATE, AudioTrackSpec, EpisodeSpec, SceneTiming, Tone
from .sfx_engine import sound_cues
from .tts_engine import ENGINES, synthesize_one


def _synthesize(spec, work, backend, voice, *, continuous=False, voice_ref=None):
    texts = [scene.narration for scene in spec.scenes]
    if continuous:
        texts = [" ".join(texts)]
    speeches = [
        synthesize_one(backend, text, voice_id=voice, speech_style=spec.speech_style, output=work / f"tts-{index}",
                       locale=spec.tts_locale, continuous=continuous, voice_ref=voice_ref,
                       pronunciation_overrides=spec.pronunciation_overrides, tone=spec.tone)
        for index, text in enumerate(texts)
    ]
    if spec.tts_locale and any(speech.locale != spec.tts_locale for speech in speeches):
        raise TTSUnavailable(f"{backend} did not confirm the required narration locale {spec.tts_locale}")
    return speeches


def _takes(spec, work, *, continuous=False, voice_ref=None):
    try:
        return _synthesize(spec, work, spec.tts_plugin, spec.voice_id, continuous=continuous, voice_ref=voice_ref)
    except TTSUnavailable as error:
        fallback = spec.tts_fallback_plugin
        if not fallback or fallback == spec.tts_plugin or voice_ref:
            raise
        logging.warning("%s failed (%s). Synthesizing the episode with %s.", spec.tts_plugin, error, fallback)
        return _synthesize(spec, work, fallback, spec.fallback_voice_id, continuous=continuous)


def _prepare_audio(spec, work, *, voice_ref=None):
    speeches = _takes(spec, work, voice_ref=voice_ref)
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


def _produce(spec, timeline, assets, services, directory, keyframes=None, workers=None, *, music=None, voice_paths=None):
    styles, media, master, audit = services
    timeline = attach_beats(timeline, music)
    resources = [media(scene, timing, assets) if media else {}
                 for scene, timing in zip(spec.scenes, timeline, strict=True)]
    renders = []
    for scene, timing, resource in zip(spec.scenes, timeline, resources, strict=True):
        with capture_scene_images() as images:
            rendered = (render_broll_scene(spec, scene, timing) if scene.broll_clip
                        else styles[scene.style](spec, scene, timing, resource))
        frames = rendered.frames
        if scene.transition and not rendered.web:
            frames = raster_transition(frames, (spec.width, spec.height), spec.fps, scene.transition)
        cues = sound_cues(scene, timing) if spec.dynamic_edit or scene.transition else ()
        renders.append(replace(rendered, frames=frames, effects=rendered.effects + cues,
                               image_ids=tuple(sorted(set(images.values())))))
    count = sum(timing.frame_count for timing in timeline)
    directory.mkdir()
    video, final = directory / "video.mp4", directory / "final.mp4"
    if any(rendered.web for rendered in renders):
        encoder = encode_scenes(timeline, renders, width=spec.width, height=spec.height, fps=spec.fps,
                                output=video, workers=workers)
    else:
        encoder = encode_frames(_frames(timeline, renders), width=spec.width, height=spec.height,
                                fps=spec.fps, frame_count=count, output=video)
    if voice_paths:
        timeline = [replace(timing, audio=replace(timing.audio, path=voice_paths[timing.audio.path]))
                    for timing in timeline]
    master(spec, timeline, renders, assets, video, final)
    return final, encoder, audit(final, spec=spec, frame_count=count, keyframes=keyframes), renders


def _picture_audit(renders, timeline):
    models = sorted({model for scene in renders for model in scene.matting_models})
    if set(models) - APPROVED_MODELS:
        raise ValueError(f"O render usou um modelo de matting fora da allowlist: {models}")
    luts = sorted({lut for scene in renders for lut in scene.color_luts})
    out = {"matting_models": models, "matting_model": models[0] if len(models) == 1 else None,
           "matting_license": "MIT" if models and set(models) <= APPROVED_MODELS else None,
           "license_commercial_safe": bool(models) and set(models) <= APPROVED_MODELS,
           "lut_applied": bool(luts), "luts": luts}
    selections = [scene.broll_selection for scene in renders if scene.broll_selection is not None]
    if selections:
        out["broll_selections"] = selections
    depth = [scene.depth_parallax for scene in renders if scene.depth_parallax is not None]
    if depth:
        out["depth_parallax"] = depth
    if timeline[0].continuous:
        out["orphan_caption_blocks"] = sum(len(block.words) == 1 for timing in timeline for block in timing.caption_blocks)
    return out


def _alignment_data(spec, timeline, renders=None):
    data = []
    for index, (scene, timing) in enumerate(zip(spec.scenes, timeline, strict=True)):
        start = timing.start_sec
        captions_expected = not scene.captions_hidden and (timing.continuous or scene.style in CAPTION_BANDS)
        blocks = timing.caption_blocks if timing.continuous else caption_blocks(timing.audio.words) if captions_expected else ()
        if scene.captions_hidden:
            blocks = ()
        data.append({
            "id": scene.id, "plugin": scene.style, "start_sec": start, "end_sec": timing.end_sec,
            "module": "broll_clip" if scene.broll_clip else scene.options.get("module") or scene.options.get("layout"),
            "transition": scene.transition, "beat_cut": timing.beat_cut, "music_mood": scene.music_mood,
            "authored_pause_sec": scene.pause_after_sec if spec.dynamic_edit else None,
            "show_header": scene.options.get("show_header", False), "dynamic_edit": spec.dynamic_edit,
            "suppress_captions": scene.captions_hidden,
            "narration": scene.narration, "visual_description": scene.visual_description,
            "visual_anchor": scene.visual_anchor,
            "visual_cue_at_sec": (start + timing.visual_cue_at if timing.visual_cue_at is not None else None),
            "duration_sec": timing.duration_sec, "captions_expected": captions_expected,
            "sentence_end": "" if timing.continuous else scene.narration.split()[-1],
            "words": [{"word": word.text, "start_sec": start + word.start_sec, "end_sec": start + word.end_sec}
                      for word in timing.audio.words],
            "caption_blocks": [{"text": block.text, "start_sec": start + block.start_sec, "end_sec": start + block.end_sec}
                               for block in blocks],
        })
        if renders is not None:
            rendered = renders[index]
            if rendered.caption_texts is not None and captions_expected:
                data[-1]["display_captions"] = rendered.caption_texts
            data[-1]["layout_audit"] = rendered.layout_audit
            data[-1]["image_ids"] = rendered.image_ids
            data[-1]["sfx"] = [{"file": effect.path.name, "local_sec": effect.at_sec,
                                "at_sec": start + effect.at_sec, "gain_db": effect.gain_db}
                               for effect in rendered.effects]
    return data


def _write_alignment(path, spec, timeline, renders=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fps": spec.fps, "scenes": _alignment_data(spec, timeline, renders)}, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def _qc_output(final, destination, spec, timeline, reference, report, renders, *, skip_vlm_qc):
    folder = destination.with_name(f"{destination.stem}_qc")
    result = qc_gate(final, _alignment_data(spec, timeline, renders), reference, measured=report,
                     grid_path=folder / "qa_grid.png", report_path=folder / "relatorio_qc_blindagem.json",
                     skip_vlm_qc=skip_vlm_qc, require_vlm=spec.production_profile == "studio_sota")
    if not result["passed"]:
        failures = ", ".join(check for check, passed in result["checks"].items() if not passed)
        raise ValueError(f"QC reprovou {destination.name} ({failures}). Veja {folder / 'relatorio_qc_blindagem.json'}")


def _fit_narration(source, duration_sec, work):
    """Fit a synthesized take to the requested runtime with pitch-preserving tempo."""
    natural = probe_duration(source)
    if natural <= 0:
        raise ValueError("A faixa sintetizada está vazia.")
    tempo = natural / duration_sec
    if not .65 <= tempo <= 1.45:
        raise ValueError("A duração da fala sintetizada está muito distante da duração pedida.")
    fitted = work / "timed-narration.wav"
    run_ffmpeg("-v", "error", "-i", str(source), "-map", "0:a:0", "-vn",
               "-af", f"atempo={tempo:.10f}", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(fitted))
    return fitted


def render(spec, output, *, assets_root=Path.cwd(), keyframes=None, audio=None, continuous=False,
           auto_scenes=False, alignment=None, reference_text=None, workers=None,
           bgm=None, denoise=False, voice_ref=None, audio_report=None, semantic_cpu=False,
           aligned_timeline=None, target_duration_sec=None, public_domain_only=False, skip_vlm_qc=False):
    """Render and audit in a temporary directory before publishing the MP4."""
    discover()
    spec = replace(spec, scenes=[replace(scene, plugin=scene.style) if scene.style != scene.plugin else scene
                                 for scene in spec.scenes])
    output = Path(output).absolute()
    if workers is not None and workers < 1:
        raise ValueError("workers deve ser no mínimo 1")
    if output.suffix.lower() != ".mp4":
        raise ValueError("The render destination must be an .mp4 file")
    continuous = audio is not None or continuous or spec.audio_mode == "continuous"
    if aligned_timeline is not None and (
        audio is None or auto_scenes or reference_text is not None
        or len(aligned_timeline) != len(spec.scenes)
        or any(timing.fps != spec.fps or not timing.continuous or not timing.audio.path.is_file()
               for timing in aligned_timeline)
    ):
        raise ValueError("A linha do tempo alinhada deve corresponder à faixa contínua e às cenas.")
    if target_duration_sec is not None and (
        audio is not None or not continuous or not isfinite(target_duration_sec) or target_duration_sec <= 0
    ):
        raise ValueError("Uma duração alvo requer narração sintetizada em modo contínuo.")
    if voice_ref is not None:
        voice_ref = Path(voice_ref).expanduser().resolve()
        if audio is not None or spec.tts_plugin not in {ENGINES["voxcpm2"], ENGINES["chatterbox_ptbr"]}:
            raise ValueError("--voice-ref requer VoxCPM2 ou Chatterbox PT-BR sem --audio.")
        if not voice_ref.is_file():
            raise ValueError("Arquivo --voice-ref inexistente.")
    if (auto_scenes or alignment) and not continuous:
        raise ValueError("Use --audio ou --continuous para gerar o alinhamento contínuo.")
    if alignment and Path(alignment).resolve() == output:
        raise ValueError("O alinhamento precisa de um destino diferente do MP4.")
    if audio is not None and Path(audio).resolve() in (output, Path(alignment).resolve() if alignment else None):
        raise ValueError("Use destinos diferentes do arquivo de áudio original.")
    if audio_report and Path(audio_report).resolve() in {
        output, *(Path(path).resolve() for path in (audio, alignment, voice_ref) if path),
    }:
        raise ValueError("--audio-report precisa de um destino próprio.")
    output.parent.mkdir(parents=True, exist_ok=True)
    assets_root = Path(assets_root)
    with TemporaryDirectory(prefix=".frimerise-", dir=output.parent) as temporary, capture_media_credits(
        assets_root=assets_root, semantic_cpu=semantic_cpu, public_domain_only=public_domain_only,
        semantic_broll=spec.production_profile == "studio_sota",
    ) as credits:
        work = Path(temporary)
        source = None
        speech = None
        if aligned_timeline is not None:
            source, timeline = Path(audio).resolve(), aligned_timeline
        elif continuous:
            speech = _takes(spec, work, continuous=True, voice_ref=voice_ref)[0] if audio is None else None
            source = speech.path if speech else Path(audio).resolve()
            if target_duration_sec is not None:
                reserved = (sum(round(scene.pause_after_sec * spec.fps) for scene in spec.scenes) / spec.fps
                            if spec.dynamic_edit and not auto_scenes else 0)
                if target_duration_sec <= reserved:
                    raise ValueError("As pausas ocupam toda a duração pedida.")
                source = _fit_narration(source, target_duration_sec - reserved, work)
            spec, timeline = align_audio(spec, source, work, auto_scenes=auto_scenes,
                                         reference_text=reference_text, spoken_text=speech.spoken_text if speech else None)
            if spec.dynamic_edit and not auto_scenes and any(scene.pause_after_sec for scene in spec.scenes):
                source = work / "narration-with-pauses.wav"
                timeline = add_authored_pauses(timeline, spec.scenes, source)
        else:
            timeline = _prepare_audio(spec, work, voice_ref=voice_ref)
        music = prepare_music(bgm, spec.music_track, assets_root, work, topic=spec.title, tone=spec.tone,
                              moods=tuple(scene.music_mood for scene in spec.scenes),
                              duration_sec=timeline[-1].end_sec, studio=spec.production_profile == "studio_sota")
        if music:
            spec = replace(spec, music_track=str(music.path))
            timeline, _ = beat_cuts(spec.scenes, timeline, music)
        timeline = attach_visual_cues(spec.scenes, timeline)
        voices, dasp_applied, cleaned = prepare_voice(timeline, work, denoise=denoise)
        styles = {name: get_plugin("style", name) for name in dict.fromkeys(scene.plugin for scene in spec.scenes)}
        media = get_plugin("media", spec.media_plugin) if spec.media_plugin else None
        master = get_plugin("audio", spec.audio_plugin)
        audit = get_plugin("qc", spec.qc_plugin)
        services = styles, media, master, audit
        plan = get_plugin("exports", spec.niche_skill, optional=True) if spec.export_shorts_clips else None
        final, encoder, report, renders = _produce(spec, timeline, assets_root, services, work / "main", keyframes, workers,
                                                   music=music, voice_paths=voices)
        picture_checks = _picture_audit(renders, timeline)
        reference_duration = probe_duration(source) if source else timeline[-1].end_sec
        _qc_output(final, output, spec, timeline, reference_duration, report, renders, skip_vlm_qc=skip_vlm_qc)
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
            clip_file, _, clip_report, clip_renders = _produce(variant, times, assets_root, services, work / f"clip-{clip.suffix}",
                                                                workers=workers, music=music, voice_paths=voices)
            _picture_audit(clip_renders, times)
            _qc_output(clip_file, destination, variant, times, times[-1].end_sec, clip_report, clip_renders,
                       skip_vlm_qc=skip_vlm_qc)
            exports[destination] = clip_file
        for destination, staged in exports.items():
            staged.replace(destination)
        if alignment:
            _write_alignment(Path(alignment), spec, timeline, renders)
        if audio_report:
            beats = music.beats_until(report.duration_sec) if music else ()
            target = Path(audio_report)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({
                "lufs": report.integrated_lufs, "true_peak_dbtp": report.true_peak_dbtp,
                "delta_sec": abs(report.duration_sec - reference_duration),
                "audio_duration_sec": reference_duration, "video_duration_sec": report.duration_sec,
                "beats_detected": len(beats), "beats_sec": beats, "bgm_ducking_applied": bool(music and spec.audio_plugin == "master_dsp"),
                "pedalboard_applied": False, "dasp_applied": dasp_applied,
                "voice_dsp": "dasp+scipy" if dasp_applied else "scipy", "denoise_applied": cleaned,
                "music": ({"method": music.method, "license": music.license, "bpm": music.bpm,
                           "musical_key": music.musical_key, "model": music.model,
                           "model_revision": music.model_revision, "text_encoder": music.text_encoder}
                          if music else None),
                "tone": spec.tone,
                "production_profile": spec.production_profile,
                "tts_requested_backend": spec.tts_plugin if speech is not None else None,
                "tts_delivery": ({"backend": speech.backend, "voice_id": speech.voice_id, "pitch": speech.pitch, "rate": speech.rate,
                                  "spoken_text": speech.spoken_text} if speech and speech.voice_id else None),
                **picture_checks,
            }, indent=2) + "\n", encoding="utf-8")
        write_media_credits(output.with_name(f"{output.stem}_fontes.md"), credits)
    return encoder, report


def add_audio_arguments(parser):
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--audio", type=Path, help="continuous narration file (MP3, WAV, or another FFmpeg audio format)")
    mode.add_argument("--continuous", action="store_true", help="synthesize the complete script in one take")
    engine = parser.add_mutually_exclusive_group()
    engine.add_argument("--tts", help="override the registered TTS backend")
    engine.add_argument("--tts-engine", choices=ENGINES, help="select the speech backend")
    parser.add_argument("--voice", help="override the TTS voice")
    parser.add_argument("--tone", choices=get_args(Tone), help="voice direction")
    parser.add_argument("--voice-ref", type=Path, help="reference WAV for VoxCPM2 or Chatterbox PT-BR")
    parser.add_argument("--skip-vlm-qc", action="store_true", help="skip the editorial model for offline tests")
    parser.add_argument("--denoise", action="store_true", help="clean narration when DeepFilterNet is installed")
    parser.add_argument("--bgm", help="music file, 'auto' for a procedural bed, or 'adaptive' for studio music")
    parser.add_argument("--audio-report", type=Path, help="write studio audio, beats and media processing results")
    parser.add_argument("--alignment", type=Path, help="write scene, word and caption timestamps to JSON")


def audio_overrides(spec, args):
    updates = {}
    if args.tone:
        updates["tone"] = args.tone
    if args.tts_engine or args.tts:
        updates["tts_plugin"] = ENGINES[args.tts_engine] if args.tts_engine else args.tts
        if args.tts_engine:
            updates["tts_fallback_plugin"] = "edge_kokoro_tts" if args.tts_engine == "chatterbox_ptbr" else None
        if not args.voice and (args.tts or updates["tts_plugin"] != spec.tts_plugin):
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
                       fps=30 if style in CAPTION_BANDS else 24,
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
    parser.add_argument("--workers", type=int, help="parallel web scene workers (default: 2 local, 4 on Colab)")
    parser.add_argument("--semantic-cpu", action="store_true", help="opt into SigLIP 2 on CPU when installed")
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
    encoder, report = render(audio_overrides(configure_production(spec), args), args.output,
                             assets_root=args.assets_root, keyframes=args.keyframes, audio=args.audio,
                             continuous=args.continuous, auto_scenes=automatic or args.auto_scenes,
                             alignment=args.alignment,
                             reference_text=reference, workers=args.workers, bgm=args.bgm, denoise=args.denoise,
                             voice_ref=args.voice_ref, audio_report=args.audio_report, semantic_cpu=args.semantic_cpu,
                             skip_vlm_qc=args.skip_vlm_qc)
    print(json.dumps({"encoder": encoder, **asdict(report)}, indent=2))


if __name__ == "__main__":
    main()
