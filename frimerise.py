"""FrimeRise command line."""

import argparse
import json
import re
import shutil
import unicodedata
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import get_args

from pydantic import ValidationError

from core.director import DIMENSIONS, PROVIDERS, DirectorError, Plan, Usage, configure_production, plan_episode, validate_episode
from core.engine import add_audio_arguments, audio_overrides, render
from core.schema import EpisodeSpec, ProductionProfile


def _slug(value):
    plain = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", plain).strip("_")[:76].rstrip("_") or "episodio"


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _produce(args):
    args.assets_root = args.assets_root.expanduser().resolve()
    if args.episode:
        try:
            spec = EpisodeSpec.from_json(args.episode.expanduser().read_bytes())
        except ValidationError:
            raise DirectorError("O JSON editado não segue o esquema de episódio.") from None
        if ((args.cartridge and args.cartridge != spec.niche_skill)
                or (args.aspect and args.aspect != spec.aspect_ratio)):
            raise DirectorError("O cartucho e o formato devem corresponder ao JSON editado.")
    elif not args.cartridge or not args.aspect:
        raise DirectorError("Informe --cartridge e --aspect para gerar o plano.")
    subject = spec.title if args.episode else args.topic or args.audio.stem
    output_dir = (args.output_dir or Path("exports") / _slug(subject)).expanduser().resolve()
    if args.audio:
        args.audio = args.audio.expanduser().resolve()
        if not args.audio.is_file() or args.audio.suffix.lower() not in {".mp3", ".wav"}:
            raise DirectorError("Informe um arquivo MP3 ou WAV válido para --audio.")
    if args.audio and (args.duration is not None or args.voice_ref is not None or args.voice or args.tts_engine or args.tts):
        raise DirectorError("Com --audio, a faixa original define a duração e a voz.")
    if args.duration is not None and not 3 <= args.duration <= 600:
        raise DirectorError("--duration deve estar entre 3 e 600 segundos.")
    with TemporaryDirectory(prefix="frimerise-director-") as temporary:
        work = Path(temporary)
        if args.episode:
            spec = configure_production(spec, args.production_profile)
            plan = Plan(spec, validate_episode(spec, assets_root=args.assets_root), Usage())
        else:
            plan = plan_episode(args.cartridge, args.aspect, topic=args.topic, duration=args.duration,
                                audio=args.audio, work=work, provider=PROVIDERS[args.provider](), tone=args.tone,
                                production_profile=args.production_profile or "standard")
        spec = audio_overrides(plan.episode, args)
        output_dir.mkdir(parents=True, exist_ok=True)
        script = output_dir / "episodio_gerado.json"
        _write_json(script, asdict(spec))
        input_mode = "episode" if args.episode else "audio" if args.audio else "topic"
        summary = {**asdict(plan.usage), "input_mode": input_mode,
                   "provider": None if args.episode else args.provider, "topic": args.topic,
                   "modules_chosen": plan.modules, "episode": str(script), "production_profile": spec.production_profile}
        if args.plan_only:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return

        output = output_dir / "episodio.mp4"
        report = output_dir / "relatorio_director.json"
        _write_json(report, {**summary, "qc_passed": False})
        print(f"Plano salvo em {script}. Renderizando {len(spec.scenes)} cenas.", flush=True)
        audio_report = args.audio_report or work / "studio.json"
        render(spec, output, assets_root=args.assets_root, audio=args.audio, continuous=True,
               aligned_timeline=plan.timeline or None, target_duration_sec=args.duration,
               workers=args.workers, bgm=args.bgm, denoise=args.denoise, voice_ref=args.voice_ref,
               audio_report=audio_report, alignment=args.alignment, keyframes=args.keyframes,
               semantic_cpu=args.semantic_cpu, public_domain_only=not args.allow_licensed_media,
               skip_vlm_qc=args.skip_vlm_qc)
        qc_dir = output_dir / "episodio_qc"
        qc_report = qc_dir / "relatorio_qc_blindagem.json"
        qc = json.loads(qc_report.read_text(encoding="utf-8"))
        if spec.dynamic_edit or spec.production_profile == "studio_sota":
            shutil.copyfile(qc_report, output_dir / qc_report.name)
        studio = json.loads(audio_report.read_text(encoding="utf-8"))
        grid = output_dir / "qa_grid.png"
        shutil.copyfile(qc_dir / grid.name, grid)
        for page in qc.get("qa_grid_pages", ()):
            filename = Path(page).name
            if filename != grid.name:
                shutil.copyfile(qc_dir / filename, output_dir / filename)
        credits = output_dir / "episodio_fontes.md"
        if not credits.is_file():
            credits.write_text("# Créditos da mídia usada no render\n\nNenhuma mídia externa foi usada.\n", encoding="utf-8")
        result = {**summary, **studio, "qc_passed": qc["passed"],
                  "monotony_windows_failed": qc["monotony_windows_failed"], "fps": qc["fps"],
                  "video": str(output), "credits": str(credits), "qa_grid": str(grid)}
        if "editorial_vlm_audit" in qc:
            result["editorial_vlm_audit"] = qc["editorial_vlm_audit"]
        if spec.dynamic_edit:
            result["checks"] = qc["checks"]
            result["scene_edits"] = [
                {key: scene[key] for key in ("id", "module", "duration_sec", "transition", "music_mood", "beat_cut", "sfx")}
                for scene in qc["scenes"]
            ]
        _write_json(report, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    produce = commands.add_parser("produce", help="plan and render an Audio-First episode")
    produce.add_argument("--topic", help="topic for a PT-BR script")
    produce.add_argument("--episode", type=Path, help="render an edited episode JSON without calling the director")
    produce.add_argument("--cartridge", choices=("crayon_book", "cutout_paper", "motion_editorial"))
    produce.add_argument("--aspect", choices=DIMENSIONS)
    produce.add_argument("--duration", type=float, help="target duration for synthesized narration, in seconds")
    produce.add_argument("--provider", choices=PROVIDERS, default="deepseek")
    produce.add_argument("--production-profile", choices=get_args(ProductionProfile))
    produce.add_argument("--plan-only", action="store_true", help="save only the episode JSON for review")
    produce.add_argument("--output-dir", type=Path)
    produce.add_argument("--assets-root", type=Path, default=Path.cwd())
    produce.add_argument("--workers", type=int)
    produce.add_argument("--keyframes", type=Path)
    produce.add_argument("--semantic-cpu", action="store_true")
    produce.add_argument("--allow-licensed-media", action="store_true",
                         help="allow commercial stock and appropriately attributed CC BY/CC BY-SA")
    add_audio_arguments(produce)
    args = parser.parse_args()
    if args.topic is not None and (args.episode or args.audio):
        produce.error("--topic não pode ser combinado com --episode ou --audio")
    if args.topic is None and args.episode is None and args.audio is None:
        produce.error("informe --topic, --audio ou --episode")
    try:
        _produce(args)
    except DirectorError as error:
        mode = " --topic" if args.topic is not None else ""
        produce.exit(1, f"produce{mode}: {error}\n")
    except (ValueError, OSError) as error:
        produce.exit(1, f"produce: {error}\n")


if __name__ == "__main__":
    main()
