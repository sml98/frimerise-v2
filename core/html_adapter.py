"""Capture a local web composition as the RGB24 stream consumed by the encoder."""

import os
from base64 import b64decode
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import copy_context
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PIL import Image

from .encoder import encode_frames, run_ffmpeg, select_encoder

ORIGIN = "https://frimerise.invalid"
EPOCH = datetime(2024, 1, 1, tzinfo=timezone.utc)


@contextmanager
def open_composition(template, *, width, height, payload, locale="pt-BR", assets=None, frame_format="rgb24"):
    """Yield a capture function accepting scene-local seconds and normalized progress."""
    from playwright.sync_api import Error, sync_playwright

    template = Path(template).resolve(strict=True)
    root = template.parent
    assets = {"/_media/" + name: Path(filename).resolve() for name, filename in (assets or {}).items()}

    def serve(route):
        request = urlsplit(route.request.url)
        if f"{request.scheme}://{request.netloc}" != ORIGIN:
            route.abort()
            return
        path = unquote(request.path)
        resource = (root / path.lstrip("/")).resolve()
        filename = assets.get(path) or (resource if resource.is_relative_to(root) else None)
        if filename and filename.is_file():
            route.fulfill(path=filename)
        else:
            route.abort()

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Error as error:
            raise RuntimeError("Chromium could not start. Run python -m playwright install --only-shell chromium.") from error
        try:
            context = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=1,
                                          locale=locale, timezone_id="UTC", service_workers="block")
            context.route("**/*", serve)
            page = context.new_page()
            page_errors = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.clock.install(time=EPOCH)
            page.clock.pause_at(EPOCH + timedelta(seconds=1))
            page.goto(f"{ORIGIN}/{template.name}", wait_until="load")
            page.evaluate("""async (payload) => {
                if (typeof window.renderAt !== "function") throw new Error("Missing window.renderAt");
                window.__frimerisePayload = payload;
                await window.renderAt(0, 0, payload);
                await document.fonts.ready;
                await Promise.all(Array.from(document.images, (image) => image.decode()));
            }""", payload)
            cdp = context.new_cdp_session(page)
            capture = {"format": "jpeg" if frame_format == "jpeg" else "png", "fromSurface": True,
                       "optimizeForSpeed": True}
            if frame_format == "jpeg":
                capture["quality"] = 88
            elif frame_format == "png":
                # Keep Playwright's omit_background behavior for every capture
                # without reapplying the override or waiting for two paint ticks.
                cdp.send("Emulation.setDefaultBackgroundColorOverride", {
                    "color": {"r": 0, "g": 0, "b": 0, "a": 0},
                })

            def frame_at(seconds, progress):
                page.evaluate("""async ([seconds, progress]) => {
                    await window.renderAt(seconds, progress, window.__frimerisePayload);
                }""", [seconds, progress])
                if page_errors:
                    raise RuntimeError(f"Web composition {template.name}: {page_errors[0]}")
                screenshot = b64decode(cdp.send("Page.captureScreenshot", capture)["data"])
                if frame_format != "rgb24":
                    return screenshot
                with Image.open(BytesIO(screenshot)) as image:
                    if image.size != (width, height):
                        raise ValueError(f"Chromium returned {image.size}, expected {(width, height)}")
                    return image.convert("RGB").tobytes()

            yield frame_at
        finally:
            browser.close()


def render_frames(template, *, width, height, timing, payload, locale="pt-BR", assets=None, frame_format="rgb24"):
    """Seek and yield one frame at a time for core.encoder's FFmpeg stdin."""
    with open_composition(template, width=width, height=height, payload=payload, locale=locale,
                          assets=assets, frame_format=frame_format) as capture:
        for index in range(timing.frame_count):
            seconds = index / timing.fps
            yield capture(seconds, timing.progress(seconds))


def encode_scenes(timeline, renders, *, width, height, fps, output, workers=None):
    """Render each web scene with its own Chromium page and assemble in script order."""
    if workers is None:
        workers = 4 if os.environ.get("FRIMERISE_COLAB") == "1" or os.environ.get("COLAB_RELEASE_TAG") else 2
    settings = select_encoder(width, height, fps)
    folder = output.parent
    jobs = [(timing, rendered, folder / f"scene-{index:05d}.mp4")
            for index, (timing, rendered) in enumerate(zip(timeline, renders, strict=True))]

    def encode(job):
        timing, rendered, path = job
        return encode_frames(rendered.frames, width=width, height=height, fps=fps, frame_count=timing.frame_count,
                             output=path, settings=settings, chunk=True, frame_format=rendered.frame_format,
                             background=rendered.background)

    with ThreadPoolExecutor(max_workers=min(workers, len(jobs)), thread_name_prefix="frimerise-web") as pool:
        futures = [pool.submit(copy_context().run, encode, job) for job in jobs if job[1].web]
        try:
            for job in jobs:
                if not job[1].web:
                    encode(job)
            for future in futures:
                future.result()
        except BaseException:
            for future in futures:
                future.cancel()
            raise

    listing = folder / "scenes.ffconcat"
    listing.write_text("ffconcat version 1.0\n" + "".join(
        f"file {path.name}\nduration {timing.frame_count / fps:.9f}\n" for timing, _, path in jobs
    ), encoding="utf-8")
    run_ffmpeg("-v", "error", "-f", "concat", "-i", str(listing), "-map", "0:v:0", "-c", "copy",
               "-video_track_timescale", str(fps), "-movie_timescale", str(fps), str(output))
    return settings[0]
