"""Stream RGB frames to an FFmpeg encoder that passes a real encoding probe."""

import subprocess
from pathlib import Path
from tempfile import TemporaryFile

FFMPEG = ["ffmpeg", "-hide_banner", "-nostdin"]


def run_ffmpeg(*args):
    result = subprocess.run([*FFMPEG, *args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"FFmpeg failed: {result.stderr.strip()}")
    return result.stderr


def select_encoder(width, height, fps):
    candidates = [("h264_nvenc", [], ["-pix_fmt", "yuv420p", "-preset", "p4"])]
    for device in sorted(Path("/dev/dri").glob("renderD*")):
        candidates.append(("h264_vaapi", ["-vaapi_device", str(device)], ["-vf", "format=nv12,hwupload"]))
    candidates.append(("libx264", [], ["-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "18"]))
    for name, device, options in candidates:
        output = ["-c:v", name, *options]
        try:
            run_ffmpeg(
                "-v", "error", *device, "-f", "lavfi", "-i", f"testsrc=s={width}x{height}:r={fps}",
                "-frames:v", "1", *output, "-f", "null", "-",
            )
        except RuntimeError as error:
            if name == "libx264":
                raise RuntimeError("No working H.264 encoder") from error
        else:
            return name, device, output


def encode_frames(frames, *, width, height, fps, frame_count, output):
    name, device, options = select_encoder(width, height, fps)
    command = [
        *FFMPEG, "-v", "error", "-y", *device, "-f", "rawvideo", "-pixel_format", "rgb24",
        "-video_size", f"{width}x{height}", "-framerate", str(fps), "-i", "pipe:0", *options, str(output),
    ]
    size = width * height * 3
    with TemporaryFile() as errors, subprocess.Popen(command, stdin=subprocess.PIPE, stderr=errors) as pipe:
        count = 0
        try:
            for frame in frames:
                count += 1
                if len(frame) != size or count > frame_count:
                    raise ValueError(f"Invalid RGB24 frame {count}. Expected {size} bytes and {frame_count} frames")
                pipe.stdin.write(frame)
            pipe.stdin.close()
        except BrokenPipeError:
            pipe.wait()
        except BaseException:
            pipe.kill()
            raise
        if pipe.wait():
            errors.seek(0)
            raise RuntimeError(f"{name} failed: {errors.read().decode(errors='replace').strip()}")
        if count != frame_count:
            raise ValueError(f"Expected {frame_count} frames, received {count}")
    return name
