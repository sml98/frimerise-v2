"""Capture a local web composition as the RGB24 stream consumed by the encoder."""

from base64 import b64decode
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PIL import Image

ORIGIN = "https://frimerise.invalid"
EPOCH = datetime(2024, 1, 1, tzinfo=timezone.utc)


@contextmanager
def open_composition(template, *, width, height, payload, locale="pt-BR"):
    """Yield a capture function accepting scene-local seconds and normalized progress."""
    from playwright.sync_api import Error, sync_playwright

    template = Path(template).resolve(strict=True)
    root = template.parent

    def serve(route):
        request = urlsplit(route.request.url)
        resource = (root / unquote(request.path).lstrip("/")).resolve()
        if (f"{request.scheme}://{request.netloc}" == ORIGIN and resource.is_relative_to(root)
                and resource.is_file()):
            route.fulfill(path=resource)
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
            capture = {"format": "png", "fromSurface": True, "optimizeForSpeed": True}

            def frame_at(seconds, progress):
                page.evaluate("""async ([seconds, progress]) => {
                    await window.renderAt(seconds, progress, window.__frimerisePayload);
                }""", [seconds, progress])
                if page_errors:
                    raise RuntimeError(f"Web composition {template.name}: {page_errors[0]}")
                screenshot = cdp.send("Page.captureScreenshot", capture)["data"]
                with Image.open(BytesIO(b64decode(screenshot))) as image:
                    if image.size != (width, height):
                        raise ValueError(f"Chromium returned {image.size}, expected {(width, height)}")
                    return image.convert("RGB").tobytes()

            yield frame_at
        finally:
            browser.close()


def render_frames(template, *, width, height, timing, payload, locale="pt-BR"):
    """Seek and yield one frame at a time for core.encoder's FFmpeg stdin."""
    with open_composition(template, width=width, height=height, payload=payload, locale=locale) as capture:
        for index in range(timing.frame_count):
            seconds = index / timing.fps
            yield capture(seconds, timing.progress(seconds))
