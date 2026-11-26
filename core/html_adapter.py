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
from .schema import SceneRender

ORIGIN = "https://frimerise.invalid"
EPOCH = datetime(2024, 1, 1, tzinfo=timezone.utc)
_EDIT_SCRIPT = Path(__file__).with_name("presentation.js")


@contextmanager
def open_composition(template, *, width, height, payload, locale="pt-BR", assets=None, frame_format="rgb24",
                     transition=None, layout_audit=None, webgl=False):
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
            args = ["--enable-unsafe-swiftshader"] if webgl else []
            browser = playwright.chromium.launch(headless=True, args=args)
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
                if (typeof window.renderAt !== "function" && typeof window.__renderAt !== "function")
                    throw new Error("Missing window.__renderAt(t)");
                window.__frimerisePayload = payload;
                if (window.renderAt) await window.renderAt(0, 0, payload);
                else await window.__renderAt(0, payload);
                await document.fonts.ready;
                await Promise.all(Array.from(document.images, (image) => image.decode()));
            }""", payload)
            if transition or layout_audit is not None:
                page.add_script_tag(content=_EDIT_SCRIPT.read_text(encoding="utf-8"))
            faults = {}
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
                result = page.evaluate("""async ([seconds, progress, transition, inspect]) => {
                    if (window.renderAt) await window.renderAt(seconds, progress, window.__frimerisePayload);
                    else await window.__renderAt(seconds, window.__frimerisePayload);
                    if (transition) window.__editIn(transition, seconds);
                    return inspect ? {violations: window.__layoutViolations(), caption: window.__captionContrast()} : null;
                }""", [seconds, progress, transition, layout_audit is not None])
                if result is not None:
                    for issue in result["violations"]:
                        faults.setdefault(tuple(issue.values()), {"at_sec": seconds, **issue})
                    layout_audit["frames"] = layout_audit.get("frames", 0) + 1
                    layout_audit["violations"] = list(faults.values())
                    if (caption := result["caption"]) is not None:
                        layout_audit["caption_frames"] = layout_audit.get("caption_frames", 0) + 1
                        minimum = layout_audit.get("min_caption_contrast", float("inf"))
                        layout_audit["min_caption_contrast"] = min(minimum, caption["ratio"])
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


def render_frames(template, *, width, height, timing, payload, locale="pt-BR", assets=None, frame_format="rgb24",
                  transition=None, layout_audit=None, webgl=False):
    """Seek and yield one frame at a time for core.encoder's FFmpeg stdin."""
    with open_composition(template, width=width, height=height, payload=payload, locale=locale,
                          assets=assets, frame_format=frame_format, transition=transition,
                          layout_audit=layout_audit, webgl=webgl) as capture:
        for index in range(timing.frame_count):
            seconds = index / timing.fps
            yield capture(seconds, timing.progress(seconds))


def presentation_captions(timing, hidden=False):
    from .audio_aligner import caption_blocks

    blocks = () if hidden else timing.caption_blocks if timing.continuous else caption_blocks(timing.audio.words)
    return [{"start": block.start_sec, "end": block.end_sec,
             "words": [{"text": word.text, "start": word.start_sec, "end": word.end_sec} for word in block.words]}
            for block in blocks]


def render_broll_scene(spec, scene, timing):
    """Use the shared compositor for an authored stock or local full-bleed insert."""
    from .color_grade import lut_name
    from .media_fetcher import fetch_broll, media_credit
    from .schema import BrollClip

    clip = scene.broll_clip
    local = scene.options.get("media_file")
    if isinstance(clip, str):
        if Path(clip).suffix.lower() == ".mp4":
            local = local or clip
            clip = BrollClip()
        else:
            clip = BrollClip(query=clip)
    description = clip.description or scene.options.get("headline", "")
    duration = min(clip.duration_sec, max(2, timing.duration_sec))
    video = fetch_broll(clip.query, clip.source, "horizontal", duration, media_file=local,
                        visual=description, narration=scene.narration)
    if video is None:
        raise ValueError(f"Cena {scene.id}: broll_clip não resolveu para um plano de vídeo.")
    credit = media_credit(video)
    lut = lut_name(scene.options.get("lut") or "doc_clean_neutral")
    auditing = {}
    frames = render_frames(Path(__file__).with_name("broll_template.html"), width=spec.width, height=spec.height,
                           timing=timing, payload={"caption": presentation_captions(timing, scene.captions_hidden),
                                                   "label": scene.options.get("archive_label", "")},
                           locale=spec.tts_locale or "pt-BR", frame_format="png",
                           transition=scene.transition, layout_audit=auditing)
    selection = ({"scene": scene.id, **credit["selection"], "source": credit.get("url") or credit.get("title")}
                 if credit.get("selection") else None)
    return SceneRender(frames, web=True, frame_format="png", background=video, background_lut=lut,
                       broll_selection=selection, color_luts=(lut,), layout_audit=auditing)


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
                             background=rendered.background, background_lut=rendered.background_lut)

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
