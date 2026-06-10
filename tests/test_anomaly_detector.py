"""Tests for anomaly detector."""
from __future__ import annotations

from tree_walker.observability.anomaly_detector import AnomalyDetector
from tree_walker.observability.events import (
    ModelResultEvent,
    StepEndEvent,
    ToolResultEvent,
)


class _FakeBus:
    """Captures emitted anomaly events."""
    def __init__(self):
        self.emitted = []

    def emit(self, event):
        self.emitted.append(event)


def test_consecutive_tool_error_triggers():
    bus = _FakeBus()
    det = AnomalyDetector(bus=bus, max_steps=100)

    for i in range(3):
        det.handle(ToolResultEvent(
            step=i, session_id="s", tool_call_id=f"t{i}",
            success=False, error="timeout", duration_seconds=1.0,
        ))

    assert len(bus.emitted) == 1
    assert bus.emitted[0].rule == "consecutive_tool_error"
    assert bus.emitted[0].severity == "high"


def test_tool_error_resets_on_success():
    bus = _FakeBus()
    det = AnomalyDetector(bus=bus, max_steps=100)

    det.handle(ToolResultEvent(step=1, session_id="s", tool_call_id="t1", success=False, error="err", duration_seconds=1.0))
    det.handle(ToolResultEvent(step=2, session_id="s", tool_call_id="t2", success=True, duration_seconds=0.5))
    det.handle(ToolResultEvent(step=3, session_id="s", tool_call_id="t3", success=False, error="err", duration_seconds=1.0))
    det.handle(ToolResultEvent(step=4, session_id="s", tool_call_id="t4", success=False, error="err", duration_seconds=1.0))

    assert len(bus.emitted) == 0  # reset interrupted the streak


def test_near_iteration_limit_triggers():
    bus = _FakeBus()
    det = AnomalyDetector(bus=bus, max_steps=10)

    det.handle(StepEndEvent(
        step=9, session_id="s", duration_seconds=2.0,
        is_done=False, consecutive_failures=0,
    ))

    assert len(bus.emitted) == 1
    assert bus.emitted[0].rule == "near_iteration_limit"
    assert bus.emitted[0].severity == "medium"


def test_near_iteration_limit_does_not_trigger_when_far():
    bus = _FakeBus()
    det = AnomalyDetector(bus=bus, max_steps=100)

    det.handle(StepEndEvent(
        step=5, session_id="s", duration_seconds=2.0,
        is_done=False, consecutive_failures=0,
    ))

    assert len(bus.emitted) == 0


def test_empty_response_loop_triggers():
    bus = _FakeBus()
    det = AnomalyDetector(bus=bus, max_steps=100)

    det.handle(ModelResultEvent(
        step=1, session_id="s", model_call_id="m1",
        action_name="done", next_goal="Ending task",
    ))
    det.handle(ModelResultEvent(
        step=2, session_id="s", model_call_id="m2",
        action_name="done", next_goal="Ending task",
    ))

    assert len(bus.emitted) == 1
    assert bus.emitted[0].rule == "empty_response_loop"
    assert bus.emitted[0].severity == "high"


def test_empty_response_resets_on_normal():
    bus = _FakeBus()
    det = AnomalyDetector(bus=bus, max_steps=100)

    det.handle(ModelResultEvent(
        step=1, session_id="s", model_call_id="m1",
        action_name="done", next_goal="Ending task",
    ))
    det.handle(ModelResultEvent(
        step=2, session_id="s", model_call_id="m2",
        action_name="click", next_goal="click button",
    ))
    det.handle(ModelResultEvent(
        step=3, session_id="s", model_call_id="m3",
        action_name="done", next_goal="Ending task",
    ))

    assert len(bus.emitted) == 0
