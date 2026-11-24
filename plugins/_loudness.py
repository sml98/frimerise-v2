"""Shared EBU R128 measurement for audio mastering and independent QC."""

import json

from core.encoder import run_ffmpeg

TARGET_LUFS = -14.0
MAX_TRUE_PEAK = -1.0


def measure(path, settings=f"I={TARGET_LUFS}:TP={MAX_TRUE_PEAK}:LRA=11"):
    log = run_ffmpeg("-i", str(path), "-vn", "-af", f"loudnorm={settings}:print_format=json", "-f", "null", "-")
    return json.loads(log[log.rindex("{"):log.rindex("}") + 1])
