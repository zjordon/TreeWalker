"""Anomaly detection — monitors events and emits anomaly events when rules trigger."""
from __future__ import annotations

from typing import TYPE_CHECKING

from tree_walker.observability.events import (
    AnomalyEvent,
    BaseEvent,
    ModelResultEvent,
    StepEndEvent,
    ToolResultEvent,
)

if TYPE_CHECKING:
    from tree_walker.observability.event_bus import EventBus


class AnomalyDetector:
    """Stateful rule engine that emits anomaly events via the bus."""

    def __init__(self, bus: EventBus, max_steps: int) -> None:
        self._bus = bus
        self._max_steps = max_steps
        self._consecutive_tool_errors: int = 0
        self._consecutive_empty_responses: int = 0

    def handle(self, event: BaseEvent) -> None:
        handler = getattr(self, f"_on_{event.event_type}", None)
        if handler:
            handler(event)

    def _on_tool_result(self, event: ToolResultEvent) -> None:
        if event.error:
            self._consecutive_tool_errors += 1
        else:
            self._consecutive_tool_errors = 0
            return

        if self._consecutive_tool_errors >= 3:
            self._bus.emit(AnomalyEvent(
                step=event.step,
                session_id=event.session_id,
                rule="consecutive_tool_error",
                severity="high",
                description=f"Tool failed {self._consecutive_tool_errors} consecutive times",
            ))
            self._consecutive_tool_errors = 0

    def _on_step_end(self, event: StepEndEvent) -> None:
        if event.step >= self._max_steps * 0.9:
            self._bus.emit(AnomalyEvent(
                step=event.step,
                session_id=event.session_id,
                rule="near_iteration_limit",
                severity="medium",
                description=f"Step {event.step} is near max_steps ({self._max_steps})",
            ))

    def _on_model_result(self, event: ModelResultEvent) -> None:
        if event.action_name == "done" and "ending task" in event.next_goal.lower():
            self._consecutive_empty_responses += 1
        else:
            self._consecutive_empty_responses = 0
            return

        if self._consecutive_empty_responses >= 2:
            self._bus.emit(AnomalyEvent(
                step=event.step,
                session_id=event.session_id,
                rule="empty_response_loop",
                severity="high",
                description="LLM returned fallback done 2 consecutive times",
            ))
            self._consecutive_empty_responses = 0
