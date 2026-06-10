"""Bridge EventBus events to the Textual RichLog widget."""

from __future__ import annotations

from textual.widgets import RichLog

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

EVENT_COLORS: dict[str, str] = {
	"step_start": "cyan",
	"model_call": "blue",
	"model_result": "green",
	"tool_call": "magenta",
	"tool_result": "yellow",
	"step_end": "cyan",
	"anomaly": "red bold",
	"session_end": "green bold",
}


class EventBridge:
	"""Subscribe to EventBus and write formatted events to a RichLog."""

	def __init__(self, events_log: RichLog) -> None:
		self._log = events_log

	def handle(self, event: BaseEvent) -> None:
		color = EVENT_COLORS.get(event.event_type, "white")
		ts = event.timestamp[11:19]
		text = self._format_event(event)
		self._log.write(f"[{color}][{ts}] {text}[/]")

	def _format_event(self, event: BaseEvent) -> str:
		name = event.event_type
		step = event.step

		if name == "step_start":
			return f"Step {step} 开始"
		if name == "model_call":
			return f"Step {step} → LLM 调用 (消息: {event.message_count})"
		if name == "model_result":
			return f"Step {step} ← LLM: {event.action_name}"
		if name == "tool_call":
			params_str = _fmt_params(event.params)
			return f"Step {step} 执行: {event.action_name}({params_str})"
		if name == "tool_result":
			mark = "✓" if event.success else "✗"
			return f"Step {step} 结果: {mark} ({event.duration_seconds:.2f}s)"
		if name == "anomaly":
			return f"⚠ [{event.severity}] {event.description}"
		if name == "step_end":
			done = " (完成)" if event.is_done else ""
			return f"Step {step} 结束{done} ({event.duration_seconds:.2f}s)"
		if name == "session_end":
			return f"会话结束: {event.summary}"
		return name


def _fmt_params(params: dict, max_len: int = 40) -> str:
	s = str(params)
	return s[:max_len] + "..." if len(s) > max_len else s
