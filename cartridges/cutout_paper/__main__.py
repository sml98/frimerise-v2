"""Render a Cut-out Paper episode and optionally extract four editorial inspection frames."""

import argparse
import json
from dataclasses import asdict, replace
from pathlib import Path

from core.director import configure_production
from core.encoder import run_ffmpeg
from core.engine import add_audio_arguments, audio_overrides, render
from core.schema import EpisodeSpec

from .renderer import Options, impact_frame

FORMATS = {"9:16": (1080, 1920), "16:9": (1920, 1080)}


def with_aspect(spec, aspect):
    if aspect is None:
        return spec
    width, height = FORMATS[aspect]
    return replace(spec, aspect_ratio=aspect, width=width, height=height)


def inspect(output, folder, spec, frame_count):
    folder.mkdir(parents=True, exist_ok=True)
    first = Options.model_validate(spec.scenes[0].options)
    stamp = impact_frame(first.stamp_at_sec, spec.fps, frame_count) if first.stamp else frame_count // 4
    picks = (
        ("inicio", min(frame_count - 1, round(.53 * spec.fps))),
        ("meio", frame_count // 2),
        ("carimbo", stamp),
        ("final", max(0, frame_count - round(.31 * spec.fps) - 1)),
    )
    frames = {}
    for name, index in picks:
        path = folder / f"{name}.png"
        seconds = index * 1_000_000 // spec.fps / 1_000_000
        run_ffmpeg("-v", "error", "-y", "-ss", f"{seconds:.6f}", "-i", str(output), "-map", "0:v:0",
                   "-frames:v", "1", "-update", "1", str(path))
        frames[name] = {"frame": index, "path": str(path)}
    return frames


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episode", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--aspect", choices=FORMATS)
    parser.add_argument("--inspect", type=Path, help="folder for four editorial inspection frames")
    parser.add_argument("--assets-root", type=Path, default=Path.cwd())
    parser.add_argument("--semantic-cpu", action="store_true", help="opt into SigLIP 2 on CPU when installed")
    add_audio_arguments(parser)
    args = parser.parse_args()
    spec = with_aspect(EpisodeSpec.from_json(args.episode.read_bytes()), args.aspect)
    encoder, report = render(audio_overrides(configure_production(spec), args), args.output, audio=args.audio,
                             assets_root=args.assets_root,
                             continuous=args.continuous, alignment=args.alignment, bgm=args.bgm,
                             denoise=args.denoise, voice_ref=args.voice_ref, audio_report=args.audio_report,
                             semantic_cpu=args.semantic_cpu, skip_vlm_qc=args.skip_vlm_qc)
    result = {"encoder": encoder, **asdict(report)}
    if args.inspect:
        result["inspection"] = inspect(args.output, args.inspect, spec, report.frame_count)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
