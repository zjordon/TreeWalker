"""Tests for tree_walker.tui.event_bridge."""

from __future__ import annotations

from unittest.mock import MagicMock

from tree_walker.observability.events import (
	AnomalyEvent,
	ModelCallEvent,
	ModelResultEvent,
	SessionEndEvent,
	StepEndEvent,
	StepStartEvent,
	ToolCallEvent,
	ToolResultEvent,
)
from tree_walker.tui.event_bridge import EventBridge, _fmt_params


def _make_event(etype: str, **kwargs):
	"""Build a minimal event dict with defaults."""
	base = {
		"step": 1,
		"session_id": "abcd1234",
	}
	base.update(kwargs)
	return base


class TestFormatEvent:
	def setup_method(self):
		self.bridge = EventBridge(MagicMock())

	def test_step_start(self):
		event = StepStartEvent(**_make_event("step_start", step=3))
		result = self.bridge._format_event(event)
		assert result == "Step 3 开始"

	def test_model_call(self):
		event = ModelCallEvent(
			**_make_event("model_call", model_call_id="m1", message_count=12),
		)
		result = self.bridge._format_event(event)
		assert "Step 1" in result
		assert "LLM 调用" in result
		assert "12" in result

	def test_model_result(self):
		event = ModelResultEvent(
			**_make_event("model_result", model_call_id="m1", action_name="click"),
		)
		result = self.bridge._format_event(event)
		assert "← LLM: click" in result

	def test_tool_call_with_params(self):
		event = ToolCallEvent(
			**_make_event(
				"tool_call",
				model_call_id="m1",
				tool_call_id="t1",
				action_name="input_text",
				params={"index": 5, "text": "hello"},
			),
		)
		result = self.bridge._format_event(event)
		assert "执行: input_text(" in result

	def test_tool_result_success(self):
		event = ToolResultEvent(
			**_make_event("tool_result", tool_call_id="t1", success=True, duration_seconds=1.23),
		)
		result = self.bridge._format_event(event)
		assert "✓" in result
		assert "1.23s" in result

	def test_tool_result_failure(self):
		event = ToolResultEvent(
			**_make_event(
				"tool_result", tool_call_id="t1", success=False, duration_seconds=0.5,
			),
		)
		result = self.bridge._format_event(event)
		assert "✗" in result

	def test_step_end_not_done(self):
		event = StepEndEvent(
			**_make_event("step_end", duration_seconds=2.5, is_done=False, consecutive_failures=0),
		)
		result = self.bridge._format_event(event)
		assert "结束" in result
		assert "完成" not in result
		assert "2.50s" in result

	def test_step_end_done(self):
		event = StepEndEvent(
			**_make_event("step_end", duration_seconds=3.0, is_done=True, consecutive_failures=0),
		)
		result = self.bridge._format_event(event)
		assert "完成" in result

	def test_anomaly(self):
		event = AnomalyEvent(
			**_make_event("anomaly", rule="consecutive_tool_error", severity="high", description="Tool failed 3 times"),
		)
		result = self.bridge._format_event(event)
		assert "⚠" in result
		assert "[high]" in result
		assert "Tool failed 3 times" in result

	def test_session_end(self):
		event = SessionEndEvent(
			**_make_event(
				"session_end",
				total_steps=10,
				total_duration_seconds=30.0,
				summary="任务成功完成",
			),
		)
		result = self.bridge._format_event(event)
		assert "会话结束" in result
		assert "任务成功完成" in result


class TestHandle:
	def test_handle_writes_to_richlog(self):
		mock_log = MagicMock()
		bridge = EventBridge(mock_log)
		event = StepStartEvent(**_make_event("step_start", step=1))
		bridge.handle(event)

		mock_log.write.assert_called_once()
		written = mock_log.write.call_args[0][0]
		assert "[cyan]" in written
		assert "Step 1 开始" in written

	def test_handle_includes_timestamp(self):
		mock_log = MagicMock()
		bridge = EventBridge(mock_log)
		event = StepStartEvent(
			step=1, session_id="x", timestamp="2026-06-09T10:30:01.123456+00:00",
		)
		bridge.handle(event)

		written = mock_log.write.call_args[0][0]
		assert "10:30:01" in written


class TestFmtParams:
	def test_short_params(self):
		result = _fmt_params({"index": 5})
		assert "index" in result
		assert "5" in result

	def test_long_params_truncated(self):
		long_val = "x" * 100
		result = _fmt_params({"data": long_val}, max_len=40)
		assert result.endswith("...")
		assert len(result) == 43  # 40 + "..."
