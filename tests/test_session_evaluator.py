"""Tests for session evaluator."""
from __future__ import annotations

from tree_walker.observability.session_evaluator import SessionEvaluator


def test_all_checks_pass():
    ev = SessionEvaluator()
    result = ev.evaluate(
        has_done=True,
        metrics_summary={
            "tool_calls": 10,
            "tool_errors": 1,
            "step_durations": [2.0, 3.0],
            "anomaly_count_by_severity": {},
        },
    )
    assert result["passed"] is True
    assert all(c["passed"] for c in result["checks"])


def test_fails_when_no_done():
    ev = SessionEvaluator()
    result = ev.evaluate(
        has_done=False,
        metrics_summary={
            "tool_calls": 10,
            "tool_errors": 1,
            "step_durations": [2.0],
            "anomaly_count_by_severity": {},
        },
    )
    assert result["passed"] is False
    assert any(c["name"] == "has_final_response" and not c["passed"] for c in result["checks"])


def test_fails_when_high_anomaly():
    ev = SessionEvaluator()
    result = ev.evaluate(
        has_done=True,
        metrics_summary={
            "tool_calls": 10,
            "tool_errors": 1,
            "step_durations": [2.0],
            "anomaly_count_by_severity": {"high": 1},
        },
    )
    assert result["passed"] is False
    assert any(c["name"] == "no_high_severity_anomaly" and not c["passed"] for c in result["checks"])


def test_fails_when_high_tool_error_rate():
    ev = SessionEvaluator()
    result = ev.evaluate(
        has_done=True,
        metrics_summary={
            "tool_calls": 10,
            "tool_errors": 4,
            "step_durations": [2.0],
            "anomaly_count_by_severity": {},
        },
    )
    assert result["passed"] is False
    assert any(c["name"] == "tool_error_rate" and not c["passed"] for c in result["checks"])


def test_skips_tool_error_rate_when_no_calls():
    ev = SessionEvaluator()
    result = ev.evaluate(
        has_done=True,
        metrics_summary={
            "tool_calls": 0,
            "tool_errors": 0,
            "step_durations": [2.0],
            "anomaly_count_by_severity": {},
        },
    )
    assert result["passed"] is True


def test_summary_string():
    ev = SessionEvaluator()
    result = ev.evaluate(
        has_done=True,
        metrics_summary={
            "tool_calls": 5,
            "tool_errors": 0,
            "step_durations": [2.0, 3.0],
            "anomaly_count_by_severity": {},
        },
    )
    assert isinstance(result["summary"], str)
    assert len(result["summary"]) > 0
