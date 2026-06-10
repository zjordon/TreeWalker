"""Tests for observability event models."""
from __future__ import annotations

import json

from tree_walker.observability.events import (
    AnomalyEvent,
    BaseEvent,
    ModelCallEvent,
    ModelResultEvent,
    SessionEndEvent,
    StepEndEvent,
    StepStartEvent,
    ToolCallEvent,
    ToolResultEvent,
)


def test_base_event_has_auto_timestamp():
    event = BaseEvent(event_type="test", step=1, session_id="abc")
    assert event.timestamp
    assert event.event_type == "test"


def test_step_start_event():
    event = StepStartEvent(step=0, session_id="abc")
    assert event.event_type == "step_start"
    data = json.loads(event.model_dump_json())
    assert data["event_type"] == "step_start"
    assert data["step"] == 0


def test_model_call_event():
    event = ModelCallEvent(step=1, session_id="abc", model_call_id="m1", message_count=5)
    assert event.model_call_id == "m1"
    assert event.message_count == 5


def test_model_result_event():
    event = ModelResultEvent(
        step=1, session_id="abc", model_call_id="m1",
        action_name="click", next_goal="click button",
    )
    assert event.thinking is None
    assert event.input_tokens is None
    assert event.output_tokens is None


def test_tool_call_event():
    event = ToolCallEvent(
        step=1, session_id="abc", model_call_id="m1",
        tool_call_id="t1", action_name="click", params={"index": 3},
    )
    assert event.model_call_id == "m1"
    assert event.tool_call_id == "t1"


def test_tool_result_event():
    event = ToolResultEvent(
        step=1, session_id="abc", tool_call_id="t1",
        success=True, error=None, duration_seconds=0.5,
    )
    assert event.success is True
    assert event.duration_seconds == 0.5


def test_step_end_event():
    event = StepEndEvent(
        step=1, session_id="abc", duration_seconds=2.5,
        is_done=False, consecutive_failures=0,
    )
    assert event.is_done is False


def test_anomaly_event():
    event = AnomalyEvent(
        step=5, session_id="abc", rule="consecutive_tool_error",
        severity="high", description="click failed 3 times in a row",
    )
    assert event.event_type == "anomaly"
    assert event.severity == "high"


def test_session_end_event():
    event = SessionEndEvent(
        step=10, session_id="abc",
        total_steps=10, total_duration_seconds=30.0,
        summary="Completed in 10 steps", evaluation=None,
    )
    assert event.event_type == "session_end"
    assert event.evaluation is None
