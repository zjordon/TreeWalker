"""Simple synchronous event bus."""
from __future__ import annotations

from typing import Any, Callable

from tree_walker.observability.events import BaseEvent


class EventBus:
    """Lightweight in-process event bus."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable[[BaseEvent], None]]] = {}
        self._close_callbacks: list[Callable[[], None]] = []

    def subscribe(self, event_type: str, handler: Callable[[BaseEvent], None]) -> None:
        """Subscribe *handler* to events matching *event_type*.

        Use ``"*"`` as a wildcard to receive all events.
        """
        self._subscribers.setdefault(event_type, []).append(handler)

    def emit(self, event: BaseEvent) -> None:
        """Publish *event* to all matching subscribers."""
        for handler in self._subscribers.get(event.event_type, []):
            handler(event)
        for handler in self._subscribers.get("*", []):
            handler(event)

    def on_close(self, callback: Callable[[], None]) -> None:
        """Register a callback to run when the bus closes."""
        self._close_callbacks.append(callback)

    def close(self) -> None:
        """Invoke all registered close callbacks and clear subscribers."""
        for cb in self._close_callbacks:
            cb()
        self._close_callbacks.clear()
        self._subscribers.clear()
