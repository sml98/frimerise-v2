"""Compose an archival depth plate over its inpainted background."""

from core.color_grade import grade_photo
from core.depth_25d import DEPTH_LICENSE, MIGAN_LICENSE, MIGAN_REPO, prepare_depth_plate
from core.html_adapter import presentation_captions, render_frames
from core.media_fetcher import fetch_image
from core.schema import SceneRender

from .sequence import DIRECTORY, FONTS


def render_photo_25d(spec, scene, timing, options):
    image = fetch_image(options.media_query, options.media_source, options.media_orientation,
                        media_file=options.media_file, visual=options.headline, narration=scene.narration)
    if image is None:
        raise ValueError(f"Cena {scene.id}: a imagem de photo_25d_parallax não foi encontrada.")
    plate = prepare_depth_plate(image, model=options.depth_model, device=options.depth_device)
    lut = options.lut or "vintage_paper_warm"
    assets = {
        "artwork.png": grade_photo(plate.source, lut),
        "bg_inpainted.png": grade_photo(plate.background, lut),
        "depth-grid.bin": plate.vertex_depth,
        "barlow.ttf": FONTS / "BarlowCondensed-Bold.ttf",
        "courier.ttf": FONTS / "CourierPrime-Regular.ttf",
    }
    payload = {
        "duration": timing.duration_sec,
        "fps": timing.fps,
        "gridSize": plate.grid_size,
        "headline": options.headline if options.show_header else options.label,
        "credit": options.photo_label,
        "caption": presentation_captions(timing, scene.captions_hidden),
    }
    layout = {}
    frames = render_frames(DIRECTORY / "photo_25d.html", width=spec.width, height=spec.height, timing=timing,
                           payload=payload, assets=assets, locale=spec.tts_locale or "pt-BR", frame_format="jpeg",
                           transition=scene.transition, layout_audit=layout, webgl=True)
    return SceneRender(frames, web=True, frame_format="jpeg", color_luts=(lut,), layout_audit=layout,
                       depth_parallax={
                           "scene": scene.id, "depth_model": f"depth-anything/{plate.model}",
                           "depth_license": DEPTH_LICENSE, "backend": plate.backend,
                           "inpaint_model": MIGAN_REPO, "inpaint_license": MIGAN_LICENSE,
                           "inpaint_applied": plate.mask_fraction > 0,
                           "inpaint_mask_fraction": round(plate.mask_fraction, 5),
                           "depth_png": str(plate.depth), "bg_inpainted_png": str(plate.background),
                       })
