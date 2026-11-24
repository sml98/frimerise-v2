"""Audit the encoded MP4, including loudness measured after AAC decoding."""

import argparse
import json
import subprocess
from dataclasses import asdict, dataclass
from fractions import Fraction
from math import isfinite
from pathlib import Path

from core.encoder import run_ffmpeg
from core.registry import register_qc
from core.schema import SAMPLE_RATE, EpisodeSpec
from plugins._loudness import MAX_TRUE_PEAK, TARGET_LUFS, measure


@dataclass(frozen=True, slots=True)
class AuditReport:
    duration_sec: float
    frame_count: int
    integrated_lufs: float
    true_peak_dbtp: float
    keyframes: tuple[str, ...]


def _streams(path):
    result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
                            capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"FFprobe failed: {result.stderr.strip()}")
    streams = json.loads(result.stdout)["streams"]
    video = [stream for stream in streams if stream["codec_type"] == "video"]
    audio = [stream for stream in streams if stream["codec_type"] == "audio"]
    if len(video) != 1 or len(audio) != 1:
        raise ValueError("Expected one video stream and one audio stream")
    return video[0], audio[0]


def _duration(stream):
    return int(stream["duration_ts"]) * Fraction(stream["time_base"])


@register_qc("ffprobe_audit")
def audit(path, *, spec=None, frame_count=None, keyframes=None):
    video, audio = _streams(path)
    frames = int(video["nb_frames"])
    fps = Fraction(video["avg_frame_rate"])
    duration = _duration(video)
    if video["codec_name"] != "h264" or audio["codec_name"] != "aac":
        raise ValueError("Expected H.264 video and AAC audio")
    if spec and (video["width"], video["height"], fps) != (spec.width, spec.height, spec.fps):
        raise ValueError("Video dimensions or frame rate differ from the episode specification")
    if frames <= 0 or (frame_count is not None and frames != frame_count):
        raise ValueError(f"Unexpected video frame count: {frames} (expected {frame_count})")
    if duration != frames / fps:
        raise ValueError("Video duration differs from the frame timeline")
    if int(audio["sample_rate"]) != SAMPLE_RATE or audio["channels"] != 2:
        raise ValueError("Expected 48 kHz stereo audio")
    if abs(_duration(audio) - duration) > Fraction(1, SAMPLE_RATE):
        raise ValueError(f"Audio and video durations differ: {float(_duration(audio))} vs {float(duration)} s")

    stats = measure(path)
    loudness, peak = float(stats["input_i"]), float(stats["input_tp"])
    if not isfinite(loudness) or not isfinite(peak) or abs(loudness - TARGET_LUFS) > 0.5 or peak > MAX_TRUE_PEAK:
        raise ValueError(f"Audio is outside target: {loudness} LUFS, {peak} dBTP")

    images = []
    if keyframes is not None:
        keyframes = Path(keyframes)
        keyframes.mkdir(parents=True, exist_ok=True)
        for percent in (10, 50, 90):
            index = frames * percent // 100
            # FFmpeg accepts microsecond seeks. Round down to select the intended frame.
            timestamp = int(index / fps * 1_000_000) / 1_000_000
            image = keyframes / f"{percent:02d}.png"
            run_ffmpeg("-v", "error", "-y", "-ss", f"{timestamp:.6f}", "-i", str(path), "-map", "0:v:0",
                       "-frames:v", "1", "-update", "1", str(image))
            images.append(str(image))
    return AuditReport(float(duration), frames, loudness, peak, tuple(images))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--episode", type=Path)
    parser.add_argument("--frames", type=int, help="expected Audio-First frame count")
    parser.add_argument("--keyframes", type=Path)
    args = parser.parse_args()
    spec = EpisodeSpec.from_json(args.episode.read_bytes()) if args.episode else None
    keyframes = args.keyframes or args.video.with_name(f"{args.video.stem}_keyframes")
    print(json.dumps(asdict(audit(args.video, spec=spec, frame_count=args.frames, keyframes=keyframes)), indent=2))


if __name__ == "__main__":
    main()
