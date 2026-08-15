"""Structured event models for agent observability."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class BaseEvent(BaseModel):
    event_type: str
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
    step: int
    session_id: str


class StepStartEvent(BaseEvent):
    event_type: str = "step_start"


class ModelCallEvent(BaseEvent):
    event_type: str = "model_call"
    model_call_id: str
    message_count: int


class ModelResultEvent(BaseEvent):
    event_type: str = "model_result"
    model_call_id: str
    action_name: str
    thinking: str | None = None
    next_goal: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None


class ToolCallEvent(BaseEvent):
    event_type: str = "tool_call"
    model_call_id: str
    tool_call_id: str
    action_name: str
    params: dict[str, Any] = Field(default_factory=dict)
    action_index: int = 0
    total_actions: int = 1
    # P6 后续 I3：目标元素几何（归一化百分比 [0,1]，相对视口）。无 index / 拿不到节点 / 拿不到视口 → None。
    element_index: int | None = None
    element_bbox: dict[str, float] | None = None  # {"left","top","width","height"}，均 ∈ [0,1]
    element_xpath: str | None = None


class ToolResultEvent(BaseEvent):
    event_type: str = "tool_result"
    tool_call_id: str
    success: bool | None
    error: str | None = None
    duration_seconds: float = 0.0
    action_index: int = 0
    total_actions: int = 1


class StepEndEvent(BaseEvent):
    event_type: str = "step_end"
    duration_seconds: float
    is_done: bool
    consecutive_failures: int


class AnomalyEvent(BaseEvent):
    event_type: str = "anomaly"
    rule: str
    severity: str  # "low" | "medium" | "high"
    description: str


class SessionEndEvent(BaseEvent):
    event_type: str = "session_end"
    total_steps: int
    total_duration_seconds: float
    summary: str
    evaluation: dict[str, Any] | None = None


class SkillActiveEvent(BaseEvent):
    """本步活动的 domain skill（P6 后续 I1）：host + 是否命中 + 文本字数。

    每步在算完 ``skill_desc`` 后由 step 流程 emit（仅 host/命中/字数，不传全文）。
    web 前端据此在 RunView 标示「活动技能」chip。
    """

    event_type: str = "skill_active"
    host: str | None = None
    skill_loaded: bool = False
    char_count: int = 0
