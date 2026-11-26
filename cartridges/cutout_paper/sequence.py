"""Prepare rights-checked archive images for the deterministic paper stage."""

from pathlib import Path

from PIL import Image

from core.color_grade import grade_photo
from core.cutout_matting import matting_model, remove_background
from core.html_adapter import presentation_captions, render_frames
from core.media_fetcher import fetch_image
from core.schema import SceneRender
from core.sfx_engine import ACTION_STOP, RUN_PERIOD, STEP_PERIOD, STEP_START, marker_cue

DIRECTORY = Path(__file__).resolve().parent
FONTS = DIRECTORY.parents[1] / "assets" / "cutout_paper" / "fonts"
BROWSER_MODULES = frozenset({"action_cutout_puppet", "full_bleed_macro", "dark_archive", "impact_slam", "evidence_pan"})


def render_sequence(spec, scene, timing, options):
    assets = {"barlow.ttf": FONTS / "BarlowCondensed-Bold.ttf", "courier.ttf": FONTS / "CourierPrime-Regular.ttf"}
    models = []
    lut = options.lut or ("doc_clean_neutral" if options.module == "full_bleed_macro" else "vintage_paper_warm")

    def prepare(key, query="", source="auto", orientation=None, local=None, *, matte=False, required=True,
                preserve_alpha=False):
        image = fetch_image(query, source, orientation, media_file=local, visual=options.headline, narration=scene.narration)
        if image is None:
            if required:
                raise ValueError(f"Cena {scene.id}: a imagem {key} não foi encontrada.")
            return None
        has_alpha = False
        if preserve_alpha:
            with Image.open(image) as source_image:
                alpha_min, alpha_max = source_image.convert("RGBA").getchannel("A").getextrema()
                has_alpha = alpha_min < 255 and alpha_max > 0
        if matte and not has_alpha:
            image = remove_background(image)
            models.append(matting_model(image))
        name = f"{key}.png"
        assets[name] = grade_photo(image, lut)
        return "/_media/" + name

    module = options.module
    pictures = {}
    if module == "action_cutout_puppet":
        actors = []
        if options.media_query or options.media_file:
            actors.append(prepare("actor-primary", options.media_query, options.media_source,
                                  options.media_orientation, options.media_file, matte=True, preserve_alpha=True))
        for index, photo in enumerate(options.photos):
            actors.append(prepare(f"actor-{index}", photo.media_query, photo.media_source,
                                  photo.media_orientation, photo.media_file, matte=True, preserve_alpha=True))
        pictures["actors"] = actors
    if module in {"photo_cutout", "newspaper_clipping", "full_bleed_macro", "dark_archive"}:
        if options.media_query or options.media_file:
            pictures["primary"] = prepare("primary", options.media_query, options.media_source,
                                          options.media_orientation, options.media_file,
                                          matte=module == "photo_cutout", required=module != "newspaper_clipping")
    if module == "evidence_pan":
        for name, photo in zip(("primary", "secondary"), options.photos, strict=True):
            pictures[name] = prepare(name, photo.media_query, photo.media_source,
                                     photo.media_orientation, photo.media_file)
    if module == "impact_slam":
        maximum = 5.5 if options.hold_impact_through_sentence and scene.visual_anchor else 3.5
        if not 2 <= timing.duration_sec <= maximum + 1e-8:
            raise ValueError(f"Cena {scene.id}: impact_slam precisa ter entre 2 e {maximum:g} segundos medidos.")
    payload = {
        "module": module, "duration": timing.duration_sec, "assets": pictures,
        "headline": options.headline, "label": options.label, "showHeader": options.show_header,
        "impact": options.impact_text or options.headline or options.stamp,
        "publication": options.publication, "clippingHeadline": options.clipping_headline,
        "clippingText": options.clipping_text, "highlight": options.highlight, "photoLabel": options.photo_label,
        "setting": options.setting, "action": options.action, "photoBackdrop": options.photo_backdrop,
        "lanternFocus": options.lantern_focus,
        "focus": {"x": options.focus_x, "y": options.focus_y}, "mark": options.mark,
        "highlightAt": marker_cue(timing),
        "impactAt": timing.visual_cue_at if timing.visual_cue_at is not None else .1,
        "cueAt": timing.visual_cue_at if timing.visual_cue_at is not None else 0,
        "stepStart": STEP_START, "stepPeriod": STEP_PERIOD, "runPeriod": RUN_PERIOD, "actionStop": ACTION_STOP,
        "captions": presentation_captions(timing, scene.captions_hidden),
    }
    layout = {}
    frames = render_frames(DIRECTORY / "sequence.html", width=spec.width, height=spec.height, timing=timing,
                           payload=payload, assets=assets, locale=spec.tts_locale or "pt-BR", frame_format="jpeg",
                           transition=scene.transition, layout_audit=layout)
    return SceneRender(frames, web=True, frame_format="jpeg", layout_audit=layout,
                       matting_models=tuple(dict.fromkeys(models)), color_luts=(lut,) if pictures else ())
