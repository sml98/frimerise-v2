"""Validate the final encoded video and create a scene contact sheet."""

import argparse
import json
import re
import subprocess
from bisect import bisect_right
from fractions import Fraction
from math import ceil, isfinite
from pathlib import Path
from tempfile import TemporaryFile

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from core.audio_aligner import _ends_sentence
from core.encoder import FFMPEG, run_ffmpeg
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
        if scene.get("captions_expected", True):
            tail = scene.get("sentence_end", "") or next((word["word"] for word in reversed(scene.get("words", ()))
                                                         if word_key(word["word"])), "")
            if tail and not _ends_sentence(tail):
                boundaries.append({"scene": scene["id"], "at_sec": scene["end_sec"], "last_word": tail})
    return orphans, boundaries


def _window_ranges(scene):
    start, end = scene["start_sec"], scene["end_sec"]
    if end - start <= 5:
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


def _samples(video, fps, width, height):
    ratio = 320 / max(width, height)
    w, h = max(2, round(width * ratio)), max(2, round(height * ratio))
    step = max(1, int(fps / 6))
    count = w * h * 3
    command = [*FFMPEG, "-v", "error", "-threads", "2", "-i", str(video), "-map", "0:v:0", "-an",
               "-filter_threads", "1", "-vf", f"select=not(mod(n\\,{step})),scale={w}:{h}:flags=area",
               "-fps_mode", "passthrough", "-threads", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]
    with TemporaryFile() as errors, subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors) as proc:
        index = 0
        while data := proc.stdout.read(count):
            if len(data) != count:
                raise RuntimeError("QC: FFmpeg devolveu um quadro incompleto")
            yield float(index * step / fps), np.frombuffer(data, dtype=np.uint8).reshape(h, w, 3)
            index += 1
        if proc.wait():
            errors.seek(0)
            raise RuntimeError(f"QC: {errors.read().decode(errors='replace')}")


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


def _grid(path, first, frames, scenes):
    cells = [("Frame 0", first), *((f"{scene['id']}  ·  {scene['start_sec']:.2f}s", frame)
                                    for scene, frame in zip(scenes, frames, strict=True))]
    exemplar = next(frame for _, frame in cells if frame is not None)
    h, w = exemplar.shape[:2]
    columns = min(4, len(cells))
    margin, label = 10, 28
    rows = ceil(len(cells) / columns)
    sheet = Image.new("RGB", (columns * (w + margin) + margin, rows * (h + label + margin) + margin), "#171b21")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=15)
    for index, (name, frame) in enumerate(cells):
        x = margin + index % columns * (w + margin)
        y = margin + index // columns * (h + label + margin)
        draw.text((x, y + 4), name, font=font, fill="#fff2dc")
        if frame is not None:
            sheet.paste(Image.fromarray(frame), (x, y + label))
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)


def qc_gate(video, scenes, reference_duration, *, report_path, grid_path, measured=None):
    """Return a verdict and publish QA even when a candidate fails."""
    video = Path(video)
    if not scenes or not isfinite(reference_duration) or reference_duration <= 0:
        raise ValueError("QC requer uma linha do tempo e duração positiva da faixa de referência")
    stream, audio = _streams(video)
    fps = Fraction(stream["avg_frame_rate"])
    duration = float(_duration(stream))
    found = _intervals(video, duration)
    stats, dead, first, frames = monotony_check(_samples(video, fps, stream["width"], stream["height"]), scenes)
    if first is not None:
        _grid(Path(grid_path), first, frames, scenes)
    orphans, boundaries = caption_errors(scenes)
    opening = _has_opening(_mask(first, scenes[0].get("plugin"))) if first is not None else False
    delta = abs(duration - reference_duration)
    if measured is None:
        stats_audio = measure(video)
        lufs, peak = float(stats_audio["input_i"]), float(stats_audio["input_tp"])
    else:
        lufs, peak = measured.integrated_lufs, measured.true_peak_dbtp
    checks = {"blackdetect": not found["black"], "freezedetect": not found["freeze"],
              "silencedetect": not found["silence"], "loudness": isfinite(lufs) and abs(lufs + 14) <= .300000001,
              "true_peak": isfinite(peak) and peak <= -1, "duration": delta <= .15,
              "encoded_av_sync": abs(float(_duration(audio)) - duration) <= .15,
              "captions": orphans == 0 and not boundaries, "frame_0": opening, "monotony": not dead}
    result = {"passed": all(checks.values()), "checks": checks, "fps": float(fps), "video_duration_sec": duration,
              "reference_audio_duration_sec": reference_duration, "lufs": lufs if isfinite(lufs) else None,
              "true_peak_dbtp": peak if isfinite(peak) else None,
              "delta_sec": delta, "orphan_caption_blocks": orphans, "scene_boundary_failures": boundaries,
              "frame_0_nonempty": opening, "monotony_windows_failed": len(dead), "monotony_failures": dead,
              "monotony_windows": stats, "ffmpeg_intervals": found, "scenes": scenes}
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
    args = parser.parse_args()
    folder = args.video.with_name(f"{args.video.stem}_qc")
    target = args.report or folder / "relatorio_qc_blindagem.json"
    metadata = json.loads((args.alignment or target).read_text(encoding="utf-8"))
    reference = args.reference_duration if args.reference_duration is not None else metadata.get("reference_audio_duration_sec")
    if reference is None:
        parser.error("informe --reference-duration quando usar um alinhamento avulso")
    result = qc_gate(args.video, metadata["scenes"], reference, report_path=target, grid_path=args.grid or folder / "qa_grid.png")
    summary = {key: result[key] for key in ("passed", "monotony_windows_failed", "orphan_caption_blocks", "lufs", "delta_sec")}
    print(json.dumps(summary, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
