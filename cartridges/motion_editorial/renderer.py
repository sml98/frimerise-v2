"""Adapt the core speech timeline to the editorial web composition."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.audio_aligner import caption_blocks
from core.html_adapter import render_frames
from core.schema import CaptionBlock, SceneRender

from .foley import Cue, effects, transition_duration

TEMPLATE = Path(__file__).with_name("template.html")


class Bar(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=42)
    value: int = Field(gt=0)
    display: str = Field(min_length=1, max_length=14)


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kicker: str = Field(min_length=1, max_length=40)
    headline: str = Field(min_length=1, max_length=72)
    visual_type: Literal["barcode_to_qr", "go_board_markers", "bar_comparison", "open_patent_network"]
    metric_badge: str = Field(default="", max_length=42)
    kpi: str = Field(default="", max_length=18)
    subtitle: str = Field(default="", max_length=110)
    bars: list[Bar] = Field(default_factory=list)
    foley: list[Cue] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_bars(self):
        if self.visual_type == "bar_comparison" and len(self.bars) != 2:
            raise ValueError("bar_comparison requires two bars")
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


def composition_payload(scene_id, options, timing):
    blocks = editorial_captions(timing.caption_blocks or caption_blocks(timing.audio.words))
    return {
        **options.model_dump(exclude={"foley"}),
        "id": scene_id,
        "durationSec": timing.duration_sec,
        "transitionSec": transition_duration(timing),
        "captions": [{
            "start": block.start_sec,
            "end": block.end_sec,
            "words": [{"text": word.text, "start": word.start_sec, "end": word.end_sec} for word in block.words],
        } for block in blocks],
    }


def render_scene(spec, scene, timing, media):
    if timing.fps != 30 or spec.aspect_ratio not in {"9:16", "16:9"}:
        raise ValueError("motion_editorial uses 30 fps and 9:16 or 16:9")
    options = Options.model_validate(scene.options)
    payload = composition_payload(scene.id, options, timing)
    frames = render_frames(TEMPLATE, width=spec.width, height=spec.height, timing=timing, payload=payload,
                           locale=spec.tts_locale or "pt-BR")
    return SceneRender(frames, effects(options.foley, options.visual_type, timing))
