"""Simple synchronous event bus."""
from __future__ import annotations

import logging
from typing import Any, Callable

from tree_walker.observability.events import BaseEvent

logger = logging.getLogger(__name__)


class _Subscription:
    """一次 subscribe 调用的投递状态（review4 #6）。

    失败计数/熔断按「订阅」键控而非按 handler 对象：同一 handler 订阅具名事件
    与 ``*`` 是两条独立投递路径，各自的失败各自计数（共享计数会 2 个事件即触
    发 disable-after-3）；bound-method 多路订阅（如 AnomalyDetector.handle ×3）
    的 close 汇总按名字去重，不虚报倍数。
    """

    __slots__ = ("handler", "name", "failures", "disabled")

    def __init__(self, handler: Callable[[BaseEvent], None]) -> None:
        self.handler = handler
        self.name = getattr(handler, "__qualname__", repr(handler))
        self.failures = 0
        self.disabled = False


class EventBus:
    """Lightweight in-process event bus."""

    # per-subscription 连续失败达到该次数后熔断该投递路径（review3 #6：死订阅者
    # 不再每事件刷 ERROR，也不再静默截断观测数据——熔断 + close 汇总显形）。
    _MAX_SUBSCRIPTION_FAILURES = 3

    def __init__(self) -> None:
        self._subscribers: dict[str, list[_Subscription]] = {}
        self._close_callbacks: list[Callable[[], None]] = []

    def subscribe(self, event_type: str, handler: Callable[[BaseEvent], None]) -> None:
        """Subscribe *handler* to events matching *event_type*.

        Use ``"*"`` as a wildcard to receive all events.
        """
        self._subscribers.setdefault(event_type, []).append(_Subscription(handler))

    def emit(self, event: BaseEvent) -> None:
        """Publish *event* to all matching subscribers.

        Per-subscription 隔离（issue #173，PR #174 review2 #1/#2）：订阅者异常不得
        穿透 emit——emit 调用点遍布 step 流程（含 try 外的 StepStartEvent 与
        finally 内的 StepEndEvent），穿透即杀死整个 run（778/782 同型死法）或被
        误计为分支 3 步骤失败。坏订阅者只打 error 不影响其他订阅者与 agent 主
        流程；连续失败达上限后熔断（review3 #6），close 时汇总显形。
        """
        for sub in self._subscribers.get(event.event_type, []):
            self._call(sub, event)
        for sub in self._subscribers.get("*", []):
            self._call(sub, event)

    def _call(self, sub: _Subscription, event: BaseEvent) -> None:
        if sub.disabled:
            return
        try:
            sub.handler(event)
            sub.failures = 0  # 恢复则清零连续计数
        except Exception as e:  # noqa: BLE001 — 观测层故障不得反噬 agent 主流程
            sub.failures += 1
            if sub.failures >= self._MAX_SUBSCRIPTION_FAILURES:
                sub.disabled = True
                logger.error(
                    "event subscriber %s failed on %s (%d/%d) — DISABLED for the rest "
                    "of this session (obs data from it is truncated from here)",
                    sub.name, type(event).__name__, sub.failures,
                    self._MAX_SUBSCRIPTION_FAILURES,
                )
            else:
                logger.error(
                    "event subscriber %s failed on %s (%d/%d): %s",
                    sub.name, type(event).__name__, sub.failures,
                    self._MAX_SUBSCRIPTION_FAILURES, e,
                )

    def on_close(self, callback: Callable[[], None]) -> None:
        """Register a callback to run when the bus closes."""
        self._close_callbacks.append(callback)

    def close(self) -> None:
        """Invoke all registered close callbacks and clear subscribers.

        close 回调与 emit 同样 per-callback 隔离（review3 #2）：JsonlRecorder 收尾
        的 flush/close 失败（磁盘满/文件已关）不得穿出 ``run()`` finally 的
        ``_finalize_session`` 替换掉 ``return self.history``——调用方必须拿到
        已完成任务的结果。熔断/失败订阅者在此汇总显形（review3 #6 / review4 #6
        去重：按订阅计数、按名字去重）。
        """
        for cb in self._close_callbacks:
            try:
                cb()
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "event bus close callback %s failed: %s",
                    getattr(cb, "__qualname__", repr(cb)), e,
                )
        all_subs = [s for subs in self._subscribers.values() for s in subs]
        disabled = [s for s in all_subs if s.disabled]
        failing = {s.name for s in all_subs if s.failures > 0}
        if disabled or failing:
            names = sorted({s.name for s in disabled})
            logger.warning(
                "event bus close: %d subscription(s) disabled [%s], %d handler(s) with "
                "recent failures — session observation data may be truncated/incomplete",
                len(disabled), ", ".join(names), len(failing),
            )
        self._close_callbacks.clear()
        self._subscribers.clear()
