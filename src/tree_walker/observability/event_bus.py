"""Simple synchronous event bus."""
from __future__ import annotations

import logging
from typing import Any, Callable

from tree_walker.observability.events import BaseEvent

logger = logging.getLogger(__name__)


class EventBus:
    """Lightweight in-process event bus."""

    # per-handler 连续失败达到该次数后熔断该订阅者（review3 #6：死订阅者不再
    # 每事件刷 ERROR，也不再静默截断观测数据——熔断 + close 汇总显形）。
    _MAX_HANDLER_FAILURES = 3

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable[[BaseEvent], None]]] = {}
        self._close_callbacks: list[Callable[[], None]] = []
        self._handler_failures: dict[int, int] = {}  # id(handler) → 连续失败次数
        self._disabled_handlers: list[str] = []  # 熔断的订阅者（close 汇总用）

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
        步骤失败。坏订阅者只打 error 不影响其他订阅者与 agent 主流程；连续失败
        达 ``_MAX_HANDLER_FAILURES`` 次后熔断（review3 #6），close 时汇总显形。
        """
        for handler in self._subscribers.get(event.event_type, []):
            self._call(handler, event)
        for handler in self._subscribers.get("*", []):
            self._call(handler, event)

    def _call(self, handler: Callable[[BaseEvent], None], event: BaseEvent) -> None:
        key = id(handler)
        if self._handler_failures.get(key, 0) >= self._MAX_HANDLER_FAILURES:
            return  # 已熔断
        try:
            handler(event)
            if key in self._handler_failures:
                self._handler_failures[key] = 0  # 恢复则清零连续计数
        except Exception as e:  # noqa: BLE001 — 观测层故障不得反噬 agent 主流程
            name = getattr(handler, "__qualname__", repr(handler))
            n = self._handler_failures.get(key, 0) + 1
            self._handler_failures[key] = n
            if n >= self._MAX_HANDLER_FAILURES:
                self._disabled_handlers.append(name)
                logger.error(
                    "event subscriber %s failed on %s (%d/%d) — DISABLED for the rest "
                    "of this session (obs data from it is truncated from here)",
                    name, type(event).__name__, n, self._MAX_HANDLER_FAILURES,
                )
            else:
                logger.error(
                    "event subscriber %s failed on %s (%d/%d): %s",
                    name, type(event).__name__, n, self._MAX_HANDLER_FAILURES, e,
                )

    def on_close(self, callback: Callable[[], None]) -> None:
        """Register a callback to run when the bus closes."""
        self._close_callbacks.append(callback)

    def close(self) -> None:
        """Invoke all registered close callbacks and clear subscribers.

        close 回调与 emit 同样 per-handler 隔离（review3 #2）：JsonlRecorder 收尾的
        flush/close 失败（磁盘满/文件已关）不得穿出 ``run()`` finally 的
        ``_finalize_session`` 替换掉 ``return self.history``——调用方必须拿到
        已完成任务的结果。熔断/失败订阅者在此汇总显形（review3 #6）。
        """
        for cb in self._close_callbacks:
            try:
                cb()
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "event bus close callback %s failed: %s",
                    getattr(cb, "__qualname__", repr(cb)), e,
                )
        # 汇总：有熔断或有失败计数非零的订阅者 → 一条 warning 提醒观测数据可能残缺
        nonzero = {k for k, v in self._handler_failures.items() if v > 0}
        if self._disabled_handlers or nonzero:
            logger.warning(
                "event bus close: %d subscriber(s) disabled, %d with recent failures "
                "— session observation data may be truncated/incomplete",
                len(self._disabled_handlers), len(nonzero),
            )
        self._close_callbacks.clear()
        self._subscribers.clear()
        self._handler_failures.clear()
