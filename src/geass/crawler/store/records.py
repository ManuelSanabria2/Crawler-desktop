"""Registros del almacenamiento del crawler."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from geass.crawler.uia.schema import Rect

SessionMode = Literal["passive", "active", "manual"]
TransitionSource = Literal["passive", "active"]
ActionType = Literal[
    "invoke",
    "toggle",
    "expand_collapse",
    "select",
    "set_value",
    "coordinate_click",
    "unknown",
]


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True)


class AppRecord(_Record):
    id: int
    executable: str
    display_name: str
    app_user_model_id: str | None


class SessionRecord(_Record):
    id: int
    started_at: datetime
    ended_at: datetime | None
    mode: SessionMode
    notes: str


class StateRecord(_Record):
    id: int
    app_id: int
    structural_signature: str
    detailed_signature: str
    title_pattern: str
    first_seen: datetime
    last_seen: datetime
    visits: int
    screenshot_path: str | None


class ElementInput(_Record):
    """Elemento a guardar. El rectángulo es relativo a la ventana."""

    path: str
    control_type: str
    name: str
    automation_id: str
    rect: Rect | None
    patterns: list[str]
    enabled: bool


class ElementRecord(ElementInput):
    id: int
    state_id: int


class TransitionRecord(_Record):
    id: int
    source_state_id: int
    target_state_id: int
    element_id: int | None
    action_type: ActionType
    source: TransitionSource
    count: int
    mean_latency_s: float | None
    latency_samples: int
    last_seen: datetime


class ScreenshotRecord(_Record):
    id: int
    file_name: str
    phash: str
    width: int
    height: int
    size_bytes: int
    created_at: datetime
