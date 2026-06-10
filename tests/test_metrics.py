"""Tests for metrics aggregator."""
from __future__ import annotations

from tree_walker.observability.events import (
    AnomalyEvent,
    ModelResultEvent,
    StepEndEvent,
    ToolResultEvent,
)
from tree_walker.observability.metrics import MetricsAggregator


def test_counts_tool_calls_and_errors():
    m = MetricsAggregator()
    m.handle(ToolResultEvent(step=1, session_id="s", tool_call_id="t1", success=True, duration_seconds=0.5))
    m.handle(ToolResultEvent(step=2, session_id="s", tool_call_id="t2", success=False, error="timeout", duration_seconds=30.0))
    m.handle(ToolResultEvent(step=3, session_id="s", tool_call_id="t3", success=True, duration_seconds=0.3))

    summary = m.get_summary()
    assert summary["tool_calls"] == 3
    assert summary["tool_errors"] == 1


def test_tracks_step_durations():
    m = MetricsAggregator()
    m.handle(StepEndEvent(step=1, session_id="s", duration_seconds=2.0, is_done=False, consecutive_failures=0))
    m.handle(StepEndEvent(step=2, session_id="s", duration_seconds=3.0, is_done=False, consecutive_failures=0))

    summary = m.get_summary()
    assert summary["step_durations"] == [2.0, 3.0]


def test_counts_tokens():
    m = MetricsAggregator()
    m.handle(ModelResultEvent(
        step=1, session_id="s", model_call_id="m1",
        action_name="click", next_goal="click", input_tokens=500, output_tokens=100,
    ))

    summary = m.get_summary()
    assert summary["total_input_tokens"] == 500
    assert summary["total_output_tokens"] == 100


def test_handles_missing_tokens():
    m = MetricsAggregator()
    m.handle(ModelResultEvent(
        step=1, session_id="s", model_call_id="m1",
        action_name="click", next_goal="click",
    ))

    summary = m.get_summary()
    assert summary["total_input_tokens"] == 0
    assert summary["total_output_tokens"] == 0


def test_counts_anomalies():
    m = MetricsAggregator()
    m.handle(AnomalyEvent(step=3, session_id="s", rule="consecutive_tool_error", severity="high", description="err"))
    m.handle(AnomalyEvent(step=5, session_id="s", rule="near_iteration_limit", severity="medium", description="warn"))

    summary = m.get_summary()
    assert summary["anomaly_count_by_severity"] == {"high": 1, "medium": 1}


def test_empty_summary_defaults():
    m = MetricsAggregator()
    summary = m.get_summary()
    assert summary["tool_calls"] == 0
    assert summary["tool_errors"] == 0
    assert summary["step_durations"] == []
    assert summary["total_input_tokens"] == 0
    assert summary["total_output_tokens"] == 0
    assert summary["anomaly_count_by_severity"] == {}
