"""Adapt the core speech timeline to the editorial web composition."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_validator, model_validator

from core.audio_aligner import caption_blocks
from core.color_grade import LutName, grade_photo, lut_name
from core.cutout_matting import matting_model, remove_background
from core.html_adapter import render_frames
from core.media_fetcher import fetch_broll, fetch_image, media_credit
from core.motion import visual_cues
from core.schema import CaptionBlock, SceneRender

from .foley import Cue, effects, stamp_time, transition_duration, transition_times

TEMPLATE = Path(__file__).with_name("template.html")


class Bar(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=42)
    value: int = Field(gt=0)
    display: str = Field(min_length=1, max_length=14)


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=42)
    detail: str = Field(default="", max_length=100)
    date: str = Field(default="", max_length=20)
    icon: Literal["smartphone", "globe", "credit-card", "factory", "ticket", "utensils", "package", "train-front"] = "globe"


class Point(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=18)
    value: FiniteFloat
    peak: bool = False


class ModuleData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: FiniteFloat = 0
    unit: str = Field(default="", max_length=16)
    delta: str = Field(default="", max_length=30)
    context: str = Field(default="", max_length=110)
    items: list[Item] = Field(default_factory=list, max_length=8)
    points: list[Point] = Field(default_factory=list, max_length=12)
    quote: str = Field(default="", max_length=220)
    author: str = Field(default="", max_length=70)
    headline: str = Field(default="", max_length=120)
    excerpt: str = Field(default="", max_length=220)
    highlight: str = Field(default="", max_length=110)
    source: str = Field(default="", max_length=75)
    caption: str = Field(default="", max_length=100)
    focus: tuple[FiniteFloat, FiniteFloat] = (.5, .5)
    lines: list[str] = Field(default_factory=list, max_length=8)


Module = Literal["kpi_hero", "bar_comparison", "line_chart", "timeline_steps", "split_vs",
                 "process_flow", "network_nodes", "checklist_reveal", "quote_callout", "news_headline",
                 "photo_spotlight", "photo_split_kpi", "gauge_meter", "terminal_code", "subject_depth_title"]

ITEM_COUNTS = {"timeline_steps": (3, 5), "split_vs": (2, 2), "process_flow": (3, 5),
               "network_nodes": (4, 8), "checklist_reveal": (3, 5)}
PHOTO_MODULES = {"photo_spotlight", "photo_split_kpi", "subject_depth_title"}


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kicker: str = Field(min_length=1, max_length=40)
    headline: str = Field(min_length=1, max_length=72)
    visual_type: Literal["barcode_to_qr", "go_board_markers", "bar_comparison", "open_patent_network"] | None = None
    module: Module | None = None
    data: ModuleData = Field(default_factory=ModuleData)
    bg_mode: Literal["grid_glow", "photo_duotone", "broll_dark", "broll_video", "radial_dramatic"] = "grid_glow"
    accent: Literal["amber", "emerald", "cyan", "crimson", "violet"] = "amber"
    overlay_fx: Literal["highlight_bar", "stamp_badge", "laser_scan"] | None = None
    media_query: str = Field(default="", max_length=220)
    media_source: Literal["auto", "pixabay", "wikimedia"] = "auto"
    media_orientation: Literal["horizontal", "vertical"] = "horizontal"
    broll_query: str = Field(default="", max_length=220)
    broll_source: Literal["auto", "pixabay", "pexels", "archive", "wikimedia"] = "auto"
    broll_start_sec: FiniteFloat = Field(default=0, ge=0)
    broll_duration_sec: FiniteFloat = Field(default=12, gt=0, le=60)
    lut: LutName | None = None
    metric_badge: str = Field(default="", max_length=42)
    kpi: str = Field(default="", max_length=18)
    subtitle: str = Field(default="", max_length=110)
    bars: list[Bar] = Field(default_factory=list)
    foley: list[Cue] = Field(default_factory=lambda: ["whoosh"])

    @field_validator("lut", mode="before")
    @classmethod
    def normalize_lut(cls, value):
        return lut_name(value) if value is not None else None

    @model_validator(mode="after")
    def check_module(self):
        if bool(self.visual_type) == bool(self.module):
            raise ValueError("Use module or visual_type, one per scene")
        if (self.module or self.visual_type) == "bar_comparison" and not 2 <= len(self.bars) <= 5:
            raise ValueError("bar_comparison requires two to five bars")
        if self.module in ITEM_COUNTS:
            minimum, maximum = ITEM_COUNTS[self.module]
            if not minimum <= len(self.data.items) <= maximum:
                raise ValueError(f"{self.module} requires {minimum}–{maximum} items")
        if self.module == "line_chart" and len(self.data.points) < 3:
            raise ValueError("line_chart requires at least three points")
        if self.module == "gauge_meter" and not 0 <= self.data.value <= 100:
            raise ValueError("gauge_meter requires a value between 0 and 100")
        if self.module == "terminal_code" and not (self.data.lines and all(len(line) <= 90 for line in self.data.lines)):
            raise ValueError("terminal_code requires one to eight lines of up to 90 characters")
        if self.module in PHOTO_MODULES and not all(0 <= axis <= 1 for axis in self.data.focus):
            raise ValueError("photo focus coordinates must be in [0, 1]")
        if self.module == "subject_depth_title" and not self.media_query:
            raise ValueError("subject_depth_title requires media_query")
        return self


def editorial_captions(blocks):
    """Fold singleton blocks into the nearer neighbour, preserving all word times."""
    blocks = list(blocks)
    index = 0
    while len(blocks) > 1 and index < len(blocks):
        current = blocks[index]
        if len(current.words) != 1:
            index += 1
            continue
        before = current.start_sec - blocks[index - 1].end_sec if index else float("inf")
        after = blocks[index + 1].start_sec - current.end_sec if index + 1 < len(blocks) else float("inf")
        first = index - (before <= after)
        blocks[first:first + 2] = [CaptionBlock(blocks[first].words + blocks[first + 1].words)]
        index = first + 1
    return blocks


def composition_payload(scene_id, options, timing, media=None):
    blocks = editorial_captions(timing.caption_blocks or caption_blocks(timing.audio.words))
    enter_at, exit_at = transition_times(timing)
    return {
        **options.model_dump(exclude={"foley", "media_query", "media_source", "media_orientation",
                                      "broll_query", "broll_source", "broll_start_sec", "broll_duration_sec", "lut"}),
        "id": scene_id,
        "durationSec": timing.duration_sec,
        "visualCues": visual_cues(timing.duration_sec),
        "transitionSec": transition_duration(timing),
        "enterAt": enter_at,
        "exitAt": exit_at,
        "stampAt": stamp_time(options.module or options.visual_type, timing, options.overlay_fx),
        "media": media or {},
        "captions": [{
            "start": block.start_sec,
            "end": block.end_sec,
            "words": [{"text": word.text, "start": word.start_sec, "end": word.end_sec} for word in block.words],
        } for block in blocks],
    }


@dataclass
class EditorialMedia:
    assets: dict = field(default_factory=dict)
    payload: dict = field(default_factory=dict)
    video: Path | None = None
    video_lut: str | None = None
    luts: tuple[str, ...] = ()
    models: tuple[str, ...] = ()


def prepare_media(options):
    media = EditorialMedia()
    depth = options.module == "subject_depth_title"
    video_modes = {"broll_video", "broll_dark"}
    if options.bg_mode in video_modes and not depth:
        query = options.broll_query or options.media_query
        source = options.broll_source if options.broll_query else options.media_source
        media.video = fetch_broll(query, source, options.media_orientation, options.broll_duration_sec,
                                  start_sec=options.broll_start_sec)
        if media.video:
            media.payload["videoCredit"] = media_credit(media.video)
            media.video_lut = options.lut or ("editorial_teal_amber" if options.bg_mode == "broll_video" else None)
    if options.media_query and (options.module in PHOTO_MODULES or (
        options.bg_mode in video_modes | {"photo_duotone"} and not media.video
    )):
        photo = fetch_image(options.media_query, options.media_source, options.media_orientation)
        if photo:
            photo_lut = options.lut or ("editorial_teal_amber" if depth else "doc_clean_neutral")
            media.assets["photo.png"] = grade_photo(photo, photo_lut)
            media.payload.update(photo="/_media/photo.png", photoCredit=media_credit(photo))
            media.luts = (photo_lut,)
            if depth:
                subject = remove_background(photo)
                media.assets["subject.png"] = grade_photo(subject, photo_lut)
                media.payload["subject"] = "/_media/subject.png"
                media.models = (matting_model(subject),)
    if media.video_lut and media.video_lut not in media.luts:
        media.luts += (media.video_lut,)
    return media


def render_scene(spec, scene, timing, media):
    if timing.fps != 30 or spec.aspect_ratio not in {"9:16", "16:9"}:
        raise ValueError("motion_editorial uses 30 fps and 9:16 or 16:9")
    options = Options.model_validate(scene.options)
    resolved = prepare_media(options)
    payload = composition_payload(scene.id, options, timing, resolved.payload)
    if resolved.video:
        payload["bg_mode"] = "broll_video"
    elif options.bg_mode in {"broll_video", "broll_dark"}:
        payload["bg_mode"] = "photo_duotone" if resolved.payload.get("photo") else "grid_glow"
    frame_format = "png" if resolved.video else "jpeg"
    frames = render_frames(TEMPLATE, width=spec.width, height=spec.height, timing=timing, payload=payload,
                           locale=spec.tts_locale or "pt-BR", assets=resolved.assets, frame_format=frame_format)
    return SceneRender(frames, effects(options.foley, options.module or options.visual_type, timing, options.overlay_fx), web=True,
                       frame_format=frame_format, background=resolved.video, background_lut=resolved.video_lut,
                       matting_models=resolved.models, color_luts=resolved.luts)
