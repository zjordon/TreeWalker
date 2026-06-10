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


class ToolResultEvent(BaseEvent):
    event_type: str = "tool_result"
    tool_call_id: str
    success: bool | None
    error: str | None = None
    duration_seconds: float = 0.0


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
