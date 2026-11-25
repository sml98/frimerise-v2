"""Episode JSON and the values exchanged between plugins."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Iterable, Literal

from pydantic import ConfigDict, Field, StrictBool, StrictInt, StringConstraints, TypeAdapter
from pydantic import model_validator
from pydantic.dataclasses import dataclass as validated_dataclass

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Seconds = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]
Dimension = Annotated[StrictInt, Field(ge=2, multiple_of=2)]
SAMPLE_RATE = 48_000


@validated_dataclass(config=ConfigDict(extra="forbid"))
class SceneSpec:
    id: Text
    plugin: Text
    narration: Text
    pause_after_sec: Seconds = 0.35
    cut_music_for_punchline: StrictBool = False
    options: dict[str, Any] = field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def collect_plugin_options(cls, data: Any) -> Any:
        if isinstance(data, dict):
            known = {key: value for key, value in data.items() if key in cls.__dataclass_fields__}
            extra = data.keys() - known.keys()
            if extra:
                given = known.get("options", {})
                if not isinstance(given, dict) or extra & given.keys():
                    raise ValueError("scene options must be an object with unique keys")
                known["options"] = {**given, **{key: data[key] for key in extra}}
            return known
        return data


@validated_dataclass(config=ConfigDict(extra="forbid"))
class EpisodeSpec:
    title: Text
    niche_skill: Text
    scenes: Annotated[list[SceneSpec], Field(min_length=1)]
    aspect_ratio: Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]*:[1-9][0-9]*$")] = "16:9"
    width: Dimension = 1920
    height: Dimension = 1080
    fps: Annotated[StrictInt, Field(gt=0)] = 24
    tts_plugin: Text = "gemini_tts"
    tts_locale: Text | None = None
    voice_id: Text | None = None
    tts_fallback_plugin: Text | None = "edge_kokoro_tts"
    fallback_voice_id: Text | None = None
    speech_style: Text | None = None
    music_track: Text | None = None
    media_plugin: Text | None = None
    audio_plugin: Text = "master_dsp"
    qc_plugin: Text = "ffprobe_audit"
    transcriber_plugin: Text = "whisper"
    export_shorts_clips: StrictBool = False
    audio_mode: Literal["scene", "continuous"] = "scene"

    @model_validator(mode="after")
    def validate_episode(self) -> "EpisodeSpec":
        across, down = map(int, self.aspect_ratio.split(":"))
        if self.width * down != self.height * across:
            raise ValueError("width and height must match aspect_ratio")
        if len({scene.id for scene in self.scenes}) != len(self.scenes):
            raise ValueError("scene IDs must be unique")
        return self

    @classmethod
    def from_json(cls, content: str | bytes) -> "EpisodeSpec":
        return TypeAdapter(cls).validate_json(content)


@dataclass(frozen=True, slots=True)
class WordTiming:
    text: str
    start_sec: float
    end_sec: float


@dataclass(frozen=True, slots=True)
class CaptionBlock:
    """Verbatim words and endpoints in scene-local seconds."""

    words: tuple[WordTiming, ...]

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)

    @property
    def start_sec(self) -> float:
        return self.words[0].start_sec

    @property
    def end_sec(self) -> float:
        return self.words[-1].end_sec


@dataclass(frozen=True, slots=True)
class Speech:
    path: Path
    words: tuple[WordTiming, ...] = ()
    locale: str | None = field(default=None, kw_only=True)
    spoken_text: str | None = field(default=None, kw_only=True)


@dataclass(frozen=True, slots=True)
class AudioTrackSpec(Speech):
    samples: int = field(kw_only=True)
    start_sample: int = field(default=0, kw_only=True)

    @property
    def duration_sec(self) -> float:
        return self.samples / SAMPLE_RATE


@dataclass(frozen=True, slots=True)
class SceneTiming:
    audio: AudioTrackSpec
    start_frame: int
    frame_count: int
    fps: int
    caption_blocks: tuple[CaptionBlock, ...] = ()
    continuous: bool = False
    beats_sec: tuple[float, ...] = field(default=(), kw_only=True)

    @property
    def start_sec(self) -> float:
        return self.start_frame / self.fps

    @property
    def end_sec(self) -> float:
        return (self.start_frame + self.frame_count) / self.fps

    @property
    def duration_sec(self) -> float:
        return self.frame_count / self.fps

    def progress(self, local_sec: float) -> float:
        return max(0.0, min(1.0, local_sec / self.duration_sec))

    def authored_time(self, seconds: float, *, reference: float) -> float:
        """Scale a legacy authored cue in continuous mode, keeping legacy episodes fixed."""
        return seconds * self.duration_sec / reference if self.continuous else seconds


@dataclass(frozen=True, slots=True)
class SoundEffect:
    path: Path
    at_sec: float
    gain_db: float = 0.0


@dataclass(frozen=True, slots=True)
class SceneRender:
    frames: Iterable[bytes]
    effects: tuple[SoundEffect, ...] = ()
    punchline_at_sec: float | None = None
    web: bool = field(default=False, kw_only=True)
    frame_format: Literal["rgb24", "jpeg", "png"] = field(default="rgb24", kw_only=True)
    background: Path | None = field(default=None, kw_only=True)
    background_lut: str | None = field(default=None, kw_only=True)
    matting_models: tuple[str, ...] = field(default=(), kw_only=True)
    color_luts: tuple[str, ...] = field(default=(), kw_only=True)


@dataclass(frozen=True, slots=True)
class ExportClip:
    suffix: str
    first_scene: int
    end_scene: int
    aspect_ratio: str
    width: int
    height: int
