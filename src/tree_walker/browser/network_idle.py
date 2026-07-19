"""CDP Network-domain inflight-request tracker for optional networkidle wait.

Mirrors ``_wait_for_page_settle``'s §8 pattern (deadline + poll + 乐观早退 + 悲观降级):
the caller polls ``is_idle()`` inside its own deadline loop; this class only
maintains the inflight set + last-activity timestamp, fed by CDP callbacks.

长连接（WebSocket / EventSource）永不收 loadingFinished → pending 永不归零。靠
``responseReceived.type`` 把它们从 idle 判定里剔除（type 在 requestWillBeSent 是
``NotRequired``，必须等 responseReceived 才能分类）。详见
``docs/wait-and-timing/03-阶段3-networkidle开关与清理upload硬编码wait.md``。

线程安全：cdp_use 回调分发线程模型未明示；``threading.Lock`` 无论单线程(asyncio)
还是独立 ws 读线程都安全（临界区极短：set.add/discard + monotonic，不阻塞 loop）。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)

# 长连接 ResourceType（CDP 字符串枚举，取自 ResponseReceived.type —— 必填）。
# 这俩会让 pending 永不归零：WebSocket 握手后不收 loadingFinished；EventSource 长流不结束。
# 不含 Fetch/XHR long-poll：type 不可区分，靠严格 deadline 兜底
# （对齐 Playwright networkidle 已知限制）。
_LONG_CONNECTION_TYPES = frozenset({"WebSocket", "EventSource"})


class NetworkIdleTracker:
    """Tracks inflight network requests via CDP Network-domain callbacks.

    Lifecycle: created per BrowserSession; ``reset()`` on reconnect/switch_tab;
    ``register(client, session_id)`` wires the 4 callbacks (idempotent under
    cdp_use's single-handler-overwrite registry).
    """

    def __init__(
        self,
        timeout: float = 5.0,
        stability_window: float = 0.5,
        poll_interval: float = 0.1,
        now_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.timeout = timeout
        self.stability_window = stability_window
        self.poll_interval = poll_interval
        self._now = now_fn
        self._lock = threading.Lock()
        self._inflight: set[str] = set()            # requestId set (all types)
        self._long_conn_ids: set[str] = set()       # subset classified long-connection
        self._last_activity: float = self._now()    # any add/remove/recv touches this
        self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def register(self, client, session_id: str | None) -> None:
        """Wire the 4 CDP callbacks. Idempotent (cdp_use overwrites). Safe to
        call on every ``_connect`` / switch_tab / reconnect."""
        try:
            client.register.Network.requestWillBeSent(self._on_request_will_be_sent)
            client.register.Network.responseReceived(self._on_response_received)  # type 分类依据
            client.register.Network.loadingFinished(self._on_loading_finished)
            client.register.Network.loadingFailed(self._on_loading_failed)
            self._enabled = True
        except Exception as e:
            logger.warning("NetworkIdleTracker register failed (degrading to off): %s", e)
            self._enabled = False

    def reset(self) -> None:
        """Clear state on reconnect/switch_tab (inflight set is per-session)."""
        with self._lock:
            self._inflight.clear()
            self._long_conn_ids.clear()
            self._last_activity = self._now()

    # ── CDP callbacks (possibly websocket reader thread) ────────────
    def _on_request_will_be_sent(self, event: dict, session_id: str | None = None) -> None:
        rid = event.get("requestId")
        if not rid:
            return
        # event['type'] is NotRequired here — defer classification to responseReceived.
        with self._lock:
            self._inflight.add(rid)                 # set.add idempotent: redirect reuses requestId, no double-count
            self._last_activity = self._now()

    def _on_response_received(self, event: dict, session_id: str | None = None) -> None:
        rid = event.get("requestId")
        rtype = event.get("type") or ""
        if not rid:
            return
        with self._lock:
            if rtype in _LONG_CONNECTION_TYPES:
                self._long_conn_ids.add(rid)        # mark long-conn; excluded from idle pending
            self._last_activity = self._now()

    def _on_loading_finished(self, event: dict, session_id: str | None = None) -> None:
        self._retire(event.get("requestId"))

    def _on_loading_failed(self, event: dict, session_id: str | None = None) -> None:
        self._retire(event.get("requestId"))        # failure also retires (release pending)

    def _retire(self, rid: str | None) -> None:
        if not rid:
            return
        with self._lock:
            self._inflight.discard(rid)             # discard idempotent: clamps at 0, never negative
            self._long_conn_ids.discard(rid)
            self._last_activity = self._now()

    # ── Idle predicate + wait (asyncio thread) ──────────────────────
    def is_idle(self) -> bool:
        """True iff (inflight minus long-connections) is empty AND no activity
        for ``stability_window``. Strict (mirrors Playwright networkidle)."""
        return self._idle_locked(self.stability_window)

    def _idle_locked(self, sw: float) -> bool:
        with self._lock:
            if not self._enabled:
                return True                         # degraded = instant idle
            if self._inflight - self._long_conn_ids:
                return False
            return (self._now() - self._last_activity) >= sw

    async def wait_until_idle(
        self,
        timeout: float | None = None,
        stability_window: float | None = None,
        poll_interval: float | None = None,
    ) -> bool:
        """Poll ``is_idle()`` until true or deadline. Returns True if reached
        idle, False on timeout (degrade — caller proceeds regardless). Mirrors
        ``_wait_for_page_settle``'s loop structure exactly."""
        if not self._enabled:
            return True
        timeout = self.timeout if timeout is None else timeout
        sw = self.stability_window if stability_window is None else stability_window
        poll = self.poll_interval if poll_interval is None else poll_interval
        deadline = self._now() + timeout
        while self._now() < deadline:
            if self._idle_locked(sw):               # 乐观早退 (most pages already quiet)
                return True
            await asyncio.sleep(poll)
        return self._idle_locked(sw)                # final check
