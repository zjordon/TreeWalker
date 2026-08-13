"""Tests for observability event models."""
from __future__ import annotations

import json

from tree_walker.observability.events import (
    AnomalyEvent,
    BaseEvent,
    ModelCallEvent,
    ModelResultEvent,
    SessionEndEvent,
    SkillActiveEvent,
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


def test_tool_call_event_element_geometry_defaults_none():
    # P6 后续 I3：元素几何字段默认 None（无 index / 无节点时）
    event = ToolCallEvent(
        step=1, session_id="abc", model_call_id="m1",
        tool_call_id="t1", action_name="send_keys", params={"keys": "Enter"},
    )
    assert event.element_index is None
    assert event.element_bbox is None
    assert event.element_xpath is None


def test_tool_call_event_element_geometry_populated():
    event = ToolCallEvent(
        step=1, session_id="abc", model_call_id="m1",
        tool_call_id="t1", action_name="click", params={"index": 3},
        element_index=3,
        element_bbox={"left": 0.1, "top": 0.2, "width": 0.3, "height": 0.4},
        element_xpath="/html/body/div",
    )
    data = json.loads(event.model_dump_json())
    assert data["element_index"] == 3
    assert data["element_bbox"]["left"] == 0.1
    assert data["element_xpath"] == "/html/body/div"


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


def test_skill_active_event():
    # P6 后续 I1：活动 skill 事件（host/命中/字数）
    event = SkillActiveEvent(
        step=1, session_id="abc",
        host="member.bilibili.com", skill_loaded=True, char_count=120,
    )
    assert event.event_type == "skill_active"
    data = json.loads(event.model_dump_json())
    assert data["event_type"] == "skill_active"
    assert data["host"] == "member.bilibili.com"
    assert data["skill_loaded"] is True
    assert data["char_count"] == 120


def test_skill_active_event_defaults():
    # 无 host / 未命中 → 默认值（None / False / 0）
    event = SkillActiveEvent(step=0, session_id="abc")
    assert event.host is None
    assert event.skill_loaded is False
    assert event.char_count == 0
