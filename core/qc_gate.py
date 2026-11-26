"""Validate the final encoded video and create a scene contact sheet."""

import argparse
import json
import re
import shutil
import subprocess
from bisect import bisect_right
from collections import Counter
from fractions import Fraction
from math import ceil, isfinite
from pathlib import Path
from tempfile import TemporaryFile

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from core.audio_aligner import _ends_sentence
from core.editing import phrase_start
from core.editorial_vlm_qc import audit_editorial
from core.encoder import FFMPEG, run_ffmpeg
from core.schema import WordTiming
from core.text_utils import word_key
from plugins._loudness import measure
from plugins.qc.ffprobe_audit import _duration, _streams

WINDOW_SEC = 3.0
CAPTION_BANDS = {
    "cutout_paper": ((.71, .82), (.79, .96)),
    "motion_editorial": ((.73, .845), (.74, .94)),
    "crayon_book": ((.49, .66), (.71, .98)),
}


def caption_errors(scenes):
    orphans = 0
    boundaries = []
    for scene in scenes:
        texts = scene.get("display_captions", tuple(block["text"] for block in scene.get("caption_blocks", ())))
        orphans += sum(sum(bool(word_key(token)) for token in text.split()) == 1 for text in texts)
        if scene.get("captions_expected", True) or scene.get("dynamic_edit"):
            tail = scene.get("sentence_end", "") or next((word["word"] for word in reversed(scene.get("words", ()))
                                                         if word_key(word["word"])), "")
            if tail and not _ends_sentence(tail):
                boundaries.append({"scene": scene["id"], "at_sec": scene["end_sec"], "last_word": tail})
    return orphans, boundaries


def _window_ranges(scene):
    start, end = scene["start_sec"], scene["end_sec"]
    if end - start <= (2 if scene.get("dynamic_edit") else 5):
        return ()
    windows = []
    for i in range(ceil((end - start) / WINDOW_SEC - 1e-8)):
        left = start + i * WINDOW_SEC
        if end - left < 1.0:
            left = max(start, end - WINDOW_SEC)
        windows.append((left, min(left + WINDOW_SEC, end)))
    return windows


def _mask(image, plugin):
    portrait = image.shape[0] > image.shape[1]
    top, bottom = CAPTION_BANDS.get(plugin, ((.72, .84), (.73, .98)))[not portrait]
    y1, y2 = int(top * image.shape[0]), ceil(bottom * image.shape[0])
    reduced = Image.fromarray(image).resize((image.shape[1] // 2, image.shape[0] // 2), Image.Resampling.BOX)
    rgb = np.asarray(reduced)
    return np.concatenate((rgb[:y1 // 2], rgb[ceil(y2 / 2):]), axis=0).astype(np.int16)


def monotony_check(samples, scenes):
    """Measure coherent picture changes, excluding caption bands and low-amplitude grain."""
    starts = [scene["start_sec"] for scene in scenes]
    targets = [start + (scene["end_sec"] - start) * .49 for start, scene in zip(starts, scenes, strict=True)]
    windows = [[{"scene": scene["id"], "start_sec": left, "end_sec": right, "anchor": None,
                 "area": 0.0, "mean": 0.0, "samples": 0} for left, right in _window_ranges(scene)] for scene in scenes]
    first = None
    frames = [None] * len(scenes)
    closest = [float("inf")] * len(scenes)
    for at, image in samples:
        if first is None:
            first = image
        index = bisect_right(starts, at + 1e-8) - 1
        if index < 0:
            continue
        distance = abs(at - targets[index])
        if distance < closest[index]:
            frames[index], closest[index] = image, distance
        active = [window for window in windows[index] if window["start_sec"] - 1e-8 <= at < window["end_sec"] - 1e-8]
        if not active:
            continue
        rgb = _mask(image, scenes[index].get("plugin"))
        for window in active:
            window["samples"] += 1
            if window["anchor"] is None:
                window["anchor"] = rgb
                continue
            delta = np.max(np.abs(rgb - window["anchor"]), axis=-1)
            window["area"] = max(window["area"], float(np.mean(delta >= 13)))
            window["mean"] = max(window["mean"], float(delta.mean()))
    stats, failed = [], []
    for group in windows:
        for window in group:
            stat = {key: window[key] for key in ("scene", "start_sec", "end_sec", "samples")}
            stat.update(changed_fraction=round(window["area"], 5), mean_rgb_delta=round(window["mean"], 4))
            stat["passed"] = window["samples"] >= 2 and window["area"] >= .025 and window["mean"] >= .65
            stats.append(stat)
            if not stat["passed"]:
                failed.append(stat)
    return stats, failed, first, frames


def editorial_checks(scenes, frames, duration, *, fps=30):
    """Measure visible contrast, spacing and pace from the actual presentation."""
    issues = []
    checked = 0
    contrast_frames = 0
    minimum_contrast = []
    anchors = []
    for scene in scenes:
        audit = scene.get("layout_audit")
        if audit:
            checked += audit.get("frames", 0)
            contrast_frames += audit.get("caption_frames", 0)
            if "min_caption_contrast" in audit:
                minimum_contrast.append(audit["min_caption_contrast"])
            issues.extend({"scene": scene["id"], **item} for item in audit.get("violations", ()))
        elif scene.get("dynamic_edit"):
            issues.append({"scene": scene["id"], "type": "missing_layout_audit"})
        if phrase := scene.get("visual_anchor"):
            words = [WordTiming(word["word"], word["start_sec"], word["end_sec"]) for word in scene.get("words", ())]
            spoken = phrase_start(phrase, words)
            cue = scene.get("visual_cue_at_sec")
            original = scene["narration"].split() == " ".join(word.text for word in words).split()
            passed = bool(original and spoken is not None and cue is not None
                          and scene["start_sec"] <= cue < scene["end_sec"]
                          and abs(spoken - cue) <= 0.5 / fps + 1e-6)
            anchors.append({"scene": scene["id"], "phrase": phrase, "spoken_at_sec": spoken,
                            "visual_cue_at_sec": cue, "original_text": original, "passed": passed})
    reused = Counter(image for scene in scenes for image in set(scene.get("image_ids", ())))
    excess = {image: count for image, count in reused.items() if count > 3}
    palette, luminance = [], []
    for scene, frame in zip(scenes, frames, strict=True):
        if frame is not None:
            color = _mask(frame, scene.get("plugin")).mean(axis=(0, 1)) / 255
            palette.append(color)
            luminance.append({"scene": scene["id"], "luma": round(float(color @ [.2126, .7152, .0722]), 4)})
    colors = np.array(palette)
    contrast = float(np.max(np.ptp(colors, axis=0))) if len(colors) >= 2 else 0.0
    has_dark = any(frame["luma"] <= .33 for frame in luminance)
    has_full_bleed = any(scene.get("module") in {"full_bleed_macro", "broll_clip"} for scene in scenes)
    lengths = [scene["end_sec"] - scene["start_sec"] for scene in scenes]
    dynamic = any(scene.get("dynamic_edit") for scene in scenes)
    mean = sum(lengths) / len(lengths)
    rhythm = (mean <= 5.5 and any(2 <= value <= 3.5 + 1e-8 for value in lengths)
              and any(4 <= value <= 6 + 1e-8 for value in lengths) and max(lengths) - min(lengths) >= 1.25)
    headers = sum(bool(scene.get("show_header")) for scene in scenes)
    checks = {"layout_collision": not issues}
    if contrast_frames:
        checks["caption_contrast"] = not any(issue["type"] == "caption_contrast" for issue in issues)
    if anchors:
        checks["audio_visual_anchors"] = all(anchor["passed"] for anchor in anchors)
    if dynamic:
        checks.update(visual_diversity=duration < 30 or contrast >= .18 and (has_dark or has_full_bleed),
                      image_reuse=not excess, dynamic_rhythm=rhythm, header_frequency=headers / len(scenes) <= .30)
    return checks, {"layout_frames_checked": checked, "layout_violations": issues,
                    "caption_contrast_frames_checked": contrast_frames,
                    "minimum_caption_contrast": round(min(minimum_contrast), 2) if minimum_contrast else None,
                    "audio_visual_anchors": anchors,
                    "scene_luminance": luminance, "palette_delta": round(contrast, 4), "has_dark_scene": has_dark,
                    "has_full_bleed_scene": has_full_bleed, "image_reuse_excess": excess,
                    "max_image_reuse": max(reused.values(), default=0),
                    "mean_scene_duration_sec": round(mean, 4), "scene_duration_span_sec": round(max(lengths)-min(lengths), 4),
                    "header_scenes": headers}


def _scaled_frames(video, width, height, select, *, long_edge):
    ratio = long_edge / max(width, height)
    w, h = max(2, round(width * ratio)), max(2, round(height * ratio))
    count = w * h * 3
    command = [*FFMPEG, "-v", "error", "-threads", "2", "-i", str(video), "-map", "0:v:0", "-an",
               "-filter_threads", "1", "-vf", f"select={select},scale={w}:{h}:flags=area",
               "-fps_mode", "passthrough", "-threads", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]
    with TemporaryFile() as errors, subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors) as proc:
        while data := proc.stdout.read(count):
            if len(data) != count:
                raise RuntimeError("QC: FFmpeg devolveu um quadro incompleto")
            yield np.frombuffer(data, dtype=np.uint8).reshape(h, w, 3)
        if proc.wait():
            errors.seek(0)
            raise RuntimeError(f"QC: {errors.read().decode(errors='replace')}")


def _samples(video, fps, width, height):
    step = max(1, int(fps / 6))
    for index, frame in enumerate(_scaled_frames(video, width, height, f"not(mod(n\\,{step}))", long_edge=320)):
        yield float(index * step / fps), frame


def _intervals(video, duration):
    log = run_ffmpeg("-v", "info", "-threads", "2", "-i", str(video), "-filter_threads", "1",
                     "-vf", "scale=180:-2,blackdetect=d=0.30:pic_th=0.985:pix_th=0.08,freezedetect=n=-60dB:d=2.95",
                     "-af", "silencedetect=n=-48dB:d=2.0", "-f", "null", "-")
    intervals = {key: [] for key in ("black", "freeze", "silence")}
    active = {}
    for kind, edge, value in re.findall(r"\b(black|freeze|silence)_(start|end):\s*(-?\d+(?:\.\d+)?)", log):
        at = max(0.0, float(value))
        if edge == "start":
            active[kind] = at
        elif kind in active:
            intervals[kind].append({"start_sec": active.pop(kind), "end_sec": at})
    for kind, start in active.items():
        intervals[kind].append({"start_sec": start, "end_sec": duration})
    return intervals


def _has_opening(frame):
    if frame is None:
        return False
    rgb = frame.astype(np.float32)
    bright = np.max(rgb, axis=-1)
    return float(np.mean(bright > 45)) > .025 and (float(bright.std()) > 5 or float(np.ptp(rgb.mean(axis=(0, 1)))) > 12)


def _grid_frame_number(scene, fps, duration):
    start, end = scene["start_sec"], scene["end_sec"]
    target = start + (end - start) * .49
    if scene.get("visual_cue_at_sec") is not None:
        target = min(end - .1, scene["visual_cue_at_sec"] + .3)
    captions = scene.get("caption_blocks", ())
    if captions and not any(block["start_sec"] <= target < block["end_sec"] for block in captions):
        target = min(((block["start_sec"] + block["end_sec"]) / 2 for block in captions),
                     key=lambda center: abs(center - target))
    first = ceil(start * fps - 1e-8)
    last = min(ceil(end * fps - 1e-8), round(duration * fps)) - 1
    return max(0, min(last, max(first, round(target * fps))))


def _grid_stamp(scene_id, frame_number, fps):
    minutes, seconds = divmod(int(frame_number / fps), 60)
    return f"[{scene_id} | {minutes:02d}:{seconds:02d}s]"


def _grid(path, video, stream, scenes, fps, duration):
    # Decode a full-size thumbnail for each chosen frame while keeping monotony sampling light.
    fps = float(fps)
    frame_numbers = [0, *(_grid_frame_number(scene, fps, duration) for scene in scenes)]
    selected = sorted(set(frame_numbers))
    select = "+".join(f"eq(n\\,{frame})" for frame in selected)
    frames = dict(zip(selected, _scaled_frames(video, stream["width"], stream["height"], select, long_edge=640),
                      strict=True))
    cells = [(scenes[0]["id"], 0, False), *((scene["id"], frame, True)
                                          for scene, frame in zip(scenes, frame_numbers[1:], strict=True))]
    h, w = frames[0].shape[:2]
    margin = 12
    font = ImageFont.load_default(size=max(13, min(24, round(w * .065))))
    path.parent.mkdir(parents=True, exist_ok=True)
    pages = []
    for offset in range(0, len(cells), 12):
        group = cells[offset:offset + 12]
        columns = min(4, len(group))
        rows = ceil(len(group) / columns)
        sheet = Image.new("RGB", (columns * (w + margin) + margin, rows * (h + margin) + margin), "#171b21")
        draw = ImageDraw.Draw(sheet)
        for index, (scene_id, frame, _) in enumerate(group):
            x = margin + index % columns * (w + margin)
            y = margin + index // columns * (h + margin)
            sheet.paste(Image.fromarray(frames[frame]), (x, y))
            label = _grid_stamp(scene_id, frame, fps)
            left, top = x + 6, y + 4
            _, _, label_width, label_height = draw.textbbox((0, 0), label, font=font, anchor="lt")
            draw.rounded_rectangle((left, top, left + label_width + 14, top + label_height + 10),
                                   radius=4, fill="#080808", outline="#ffffff", width=1)
            draw.text((left + 7, top + 4), label, font=font, fill="#ffffff", anchor="lt")
        page = path.with_name(f"{path.stem}_p{len(pages) + 1}.png")
        sheet.save(page)
        if not pages:
            if path.suffix.lower() == ".png":
                shutil.copyfile(page, path)
            else:
                sheet.save(path)
        pages.append((page, tuple(scene_id for scene_id, _, representative in group if representative)))
    return pages


def qc_gate(video, scenes, reference_duration, *, report_path, grid_path, measured=None,
            skip_vlm_qc=False, require_vlm=False):
    """Return a verdict and publish QA even when a candidate fails."""
    video = Path(video)
    if not scenes or not isfinite(reference_duration) or reference_duration <= 0:
        raise ValueError("QC requer uma linha do tempo e duração positiva da faixa de referência")
    stream, audio = _streams(video)
    fps = Fraction(stream["avg_frame_rate"])
    duration = float(_duration(stream))
    found = _intervals(video, duration)
    stats, dead, first, frames = monotony_check(_samples(video, fps, stream["width"], stream["height"]), scenes)
    pages = _grid(Path(grid_path), video, stream, scenes, fps, duration) if first is not None else ()
    orphans, boundaries = caption_errors(scenes)
    opening = _has_opening(_mask(first, scenes[0].get("plugin"))) if first is not None else False
    delta = abs(duration - reference_duration)
    dynamic = any(scene.get("dynamic_edit") for scene in scenes)
    new_checks, editing = editorial_checks(scenes, frames, duration, fps=float(fps))
    if measured is None:
        stats_audio = measure(video)
        lufs, peak = float(stats_audio["input_i"]), float(stats_audio["input_tp"])
    else:
        lufs, peak = measured.integrated_lufs, measured.true_peak_dbtp
    studio_audio = dynamic or require_vlm
    checks = {"blackdetect": not found["black"], "freezedetect": not found["freeze"],
              "silencedetect": not found["silence"], "loudness": isfinite(lufs) and abs(lufs + 14) <= (.500000001 if studio_audio else .300000001),
              "true_peak": isfinite(peak) and peak <= (-1.5 if studio_audio else -1), "duration": delta <= .15,
              "encoded_av_sync": abs(float(_duration(audio)) - duration) <= .15,
              "captions": orphans == 0 and not boundaries, "frame_0": opening, "monotony": not dead, **new_checks}
    audit = audit_editorial(pages, scenes, skip=skip_vlm_qc, required=require_vlm)
    if audit is not None and audit["status"] != "skipped":
        checks["editorial_vlm"] = audit["overall_passed"]
    result = {"passed": all(checks.values()), "checks": checks, "fps": float(fps), "video_duration_sec": duration,
              "reference_audio_duration_sec": reference_duration, "lufs": lufs if isfinite(lufs) else None,
              "true_peak_dbtp": peak if isfinite(peak) else None,
              "delta_sec": delta, "orphan_caption_blocks": orphans, "scene_boundary_failures": boundaries,
              "frame_0_nonempty": opening, "monotony_windows_failed": len(dead), "monotony_failures": dead,
              "monotony_windows": stats, "ffmpeg_intervals": found, "scenes": scenes,
              "qa_grid_pages": [page.name for page, _ in pages], **editing}
    if audit is not None:
        result["editorial_vlm_audit"] = audit
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--alignment", type=Path, help="scene and captions JSON, or a prior QC report")
    parser.add_argument("--reference-duration", type=float, help="duration of the continuous source audio in seconds")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--grid", type=Path)
    parser.add_argument("--require-vlm", action="store_true", help="require a configured editorial VLM audit")
    parser.add_argument("--skip-vlm-qc", action="store_true", help="skip the editorial VLM for offline tests")
    args = parser.parse_args()
    folder = args.video.with_name(f"{args.video.stem}_qc")
    target = args.report or folder / "relatorio_qc_blindagem.json"
    metadata = json.loads((args.alignment or target).read_text(encoding="utf-8"))
    reference = args.reference_duration if args.reference_duration is not None else metadata.get("reference_audio_duration_sec")
    if reference is None:
        parser.error("informe --reference-duration quando usar um alinhamento avulso")
    result = qc_gate(args.video, metadata["scenes"], reference, report_path=target, grid_path=args.grid or folder / "qa_grid.png",
                     skip_vlm_qc=args.skip_vlm_qc, require_vlm=args.require_vlm)
    summary = {key: result[key] for key in ("passed", "monotony_windows_failed", "orphan_caption_blocks", "lufs", "delta_sec")}
    if "editorial_vlm_audit" in result:
        summary["editorial_vlm_audit"] = result["editorial_vlm_audit"]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
