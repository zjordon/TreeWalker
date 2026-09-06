"""Simple synchronous event bus."""
from __future__ import annotations

import logging
from typing import Any, Callable

from tree_walker.observability.events import BaseEvent

logger = logging.getLogger(__name__)


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
        """Publish *event* to all matching subscribers.

        Per-handler 隔离（issue #173，PR #174 review2 #1/#2）：订阅者异常不得穿透
        emit——emit 调用点遍布 step 流程（含 try 外的 StepStartEvent 与 finally 内
        的 StepEndEvent），穿透即杀死整个 run（778/782 同型死法）或被误计为分支 3
        步骤失败。坏订阅者只打 error 不影响其他订阅者与 agent 主流程。
        """
        for handler in self._subscribers.get(event.event_type, []):
            self._call(handler, event)
        for handler in self._subscribers.get("*", []):
            self._call(handler, event)

    @staticmethod
    def _call(handler: Callable[[BaseEvent], None], event: BaseEvent) -> None:
        try:
            handler(event)
        except Exception as e:  # noqa: BLE001 — 观测层故障不得反噬 agent 主流程
            logger.error(
                "event subscriber %r failed on %s: %s",
                getattr(handler, "__qualname__", handler), type(event).__name__, e,
            )

    def on_close(self, callback: Callable[[], None]) -> None:
        """Register a callback to run when the bus closes."""
        self._close_callbacks.append(callback)

    def close(self) -> None:
        """Invoke all registered close callbacks and clear subscribers."""
        for cb in self._close_callbacks:
            cb()
        self._close_callbacks.clear()
        self._subscribers.clear()
