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


def encode_frames(frames, *, width, height, fps, frame_count, output, settings=None, chunk=False,
                  frame_format="rgb24", background=None):
    name, device, options = settings or select_encoder(width, height, fps)
    if chunk:
        options = [*options, "-threads", "3", "-bf", "0", "-video_track_timescale", str(fps),
                   "-movie_timescale", str(fps)]
    if frame_format == "rgb24":
        video_input = ["-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", f"{width}x{height}"]
    else:
        video_input = ["-f", "image2pipe", "-c:v", "mjpeg" if frame_format == "jpeg" else "png"]
    command = [*FFMPEG, "-v", "error", "-y", *device, "-threads", "2",
               *video_input, "-framerate", str(fps), "-i", "pipe:0"]
    if background:
        if frame_format != "png":
            raise ValueError("B-roll compositing requires PNG frames with alpha")
        if name == "h264_vaapi":
            options = options[:2] + options[4:]
        output_filter = "format=nv12,hwupload" if name == "h264_vaapi" else "format=yuv420p"
        composition = (
            f"[1:v]fps={fps},scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,setpts=PTS-STARTPTS,format=yuv420p,"
            "eq=brightness=-0.16:contrast=1.06:saturation=0.42,"
            "colorbalance=rs=-0.028:bs=0.030:bm=0.015,vignette=PI/5[plate];"
            f"[0:v]format=rgba,setsar=1,setpts=PTS-STARTPTS[graphics];"
            f"[plate][graphics]overlay=shortest=1:format=auto,{output_filter}[composited]"
        )
        command.extend(("-stream_loop", "-1", "-threads", "2", "-i", str(background), "-filter_complex_threads", "2",
                        "-filter_complex", composition, "-map", "[composited]"))
    command.extend((*options, str(output)))
    size = width * height * 3
    with TemporaryFile() as errors, subprocess.Popen(command, stdin=subprocess.PIPE, stderr=errors) as pipe:
        count = 0
        try:
            for frame in frames:
                count += 1
                valid = (len(frame) == size if frame_format == "rgb24" else
                         frame.startswith(b"\xff\xd8" if frame_format == "jpeg" else b"\x89PNG\r\n\x1a\n"))
                if not valid or count > frame_count:
                    raise ValueError(f"Invalid {frame_format} frame {count}. Expected {frame_count} frames")
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
