"""Runtime metrics aggregator — subscribes to events and maintains counters."""
from __future__ import annotations

from tree_walker.observability.events import (
    AnomalyEvent,
    BaseEvent,
    ModelResultEvent,
    StepEndEvent,
    ToolResultEvent,
)


class MetricsAggregator:
    """Collects runtime metrics from observability events."""

    def __init__(self) -> None:
        self.tool_calls: int = 0
        self.tool_errors: int = 0
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.step_durations: list[float] = []
        self.anomaly_count_by_severity: dict[str, int] = {}

    def handle(self, event: BaseEvent) -> None:
        handler = getattr(self, f"_on_{event.event_type}", None)
        if handler:
            handler(event)

    def _on_tool_result(self, event: ToolResultEvent) -> None:
        self.tool_calls += 1
        if event.error:
            self.tool_errors += 1

    def _on_model_result(self, event: ModelResultEvent) -> None:
        if event.input_tokens is not None:
            self.total_input_tokens += event.input_tokens
        if event.output_tokens is not None:
            self.total_output_tokens += event.output_tokens

    def _on_step_end(self, event: StepEndEvent) -> None:
        self.step_durations.append(event.duration_seconds)

    def _on_anomaly(self, event: AnomalyEvent) -> None:
        self.anomaly_count_by_severity[event.severity] = (
            self.anomaly_count_by_severity.get(event.severity, 0) + 1
        )

    def get_summary(self) -> dict:
        return {
            "tool_calls": self.tool_calls,
            "tool_errors": self.tool_errors,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "step_durations": list(self.step_durations),
            "anomaly_count_by_severity": dict(self.anomaly_count_by_severity),
        }
