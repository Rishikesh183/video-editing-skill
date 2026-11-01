"""Job, plan and media models. Persisted as JSON under data/jobs/<id>/job.json."""

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

Ratio = Literal["9:16", "16:9"]
SceneMode = Literal["fullframe", "overlay"]
JobStatus = Literal["uploaded", "planning", "planned", "rendering", "reviewing", "rendered", "error"]

ORIENT = {"9:16": "portrait", "16:9": "landscape"}
CANVAS = {"9:16": (1080, 1920), "16:9": (1920, 1080)}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MediaInfo(BaseModel):
    file: str
    mime: str
    duration_us: int
    width: int = 0
    height: int = 0
    rotation: int = 0
    has_audio: bool = False
    fps: float = 30.0


class Cue(BaseModel):
    start: float
    end: float
    text: str


class Scene(BaseModel):
    id: str
    start: float
    end: float
    mode: SceneMode
    template: str
    data: dict[str, Any] = Field(default_factory=dict)
    needs_input: list[str] = Field(default_factory=list)
    reason: str = ""
    approved: bool = True

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 4)


class AssetSlot(BaseModel):
    slot: str
    description: str
    file: str | None = None  # path relative to the job dir once supplied


class Plan(BaseModel):
    story: str = ""  # the planner's editor notes (beats and pictures), written before the scenes
    theme: str = ""  # voice videos: colour theme picked to fit the script (voice.THEMES)
    scenes: list[Scene]
    assets_needed: list[AssetSlot] = Field(default_factory=list)


class RenderResult(BaseModel):
    scene_id: str
    ratio: Ratio
    file: str | None = None  # relative to job dir
    status: Literal["ok", "skipped", "error"]
    detail: str = ""


class Job(BaseModel):
    id: str
    created: str = Field(default_factory=now)
    status: JobStatus = "uploaded"
    error: str = ""
    ratios: list[Ratio] = Field(default_factory=lambda: ["9:16", "16:9"])
    video: MediaInfo
    captions: list[Cue] = Field(default_factory=list)
    script: str = ""
    plan: Plan | None = None
    renders: list[RenderResult] = Field(default_factory=list)
    exports: dict[str, str] = Field(default_factory=dict)  # ratio -> bundle zip, relative to job dir
    provider: str = "claude"  # planner used for this job: "claude" (Claude Code) or "codex" (ChatGPT)
    tighten: bool = False  # cut long pauses and retakes before planning (see tighten.py)
    tightened: str = ""  # what tightening removed, for the app (empty until it ran)
    review: dict | None = None  # last self-review: {summary, edit_issues, photo_issues, round}
    style: str = ""  # planner/styles/<style>.md to follow; empty = the house style
    mode: Literal["talking", "voice"] = "talking"  # voice = a voice recording only; the whole picture is generated
