"""NetworkIdleTracker 单元测试（阶段3 缺口2）。

纯单测：直接喂数据事件 dict 给 CDP 回调，断言 is_idle / wait_until_idle 行为。
不依赖 BrowserSession / 真实 CDP。设计见 docs/wait-and-timing/03-阶段3-...md。
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from tree_walker.browser.network_idle import NetworkIdleTracker, _LONG_CONNECTION_TYPES

# 小窗口 + 短轮询，让真实时间测试在 ~0.1s 量级完成
_SW = 0.1
_POLL = 0.02
_TIMEOUT = 0.3


def _tracker() -> NetworkIdleTracker:
    t = NetworkIdleTracker(stability_window=_SW, poll_interval=_POLL, timeout=_TIMEOUT)
    t.register(MagicMock(), "sid")  # 挂 4 回调（MagicMock 链式自动接受）→ _enabled=True
    assert t.enabled is True
    return t


# ── 基础计数 ─────────────────────────────────────────────────────────


def test_long_connection_types_constant():
    assert "WebSocket" in _LONG_CONNECTION_TYPES
    assert "EventSource" in _LONG_CONNECTION_TYPES
    # Fetch/XHR long-poll 不在过滤集（type 不可区分，靠 deadline 兜底）
    assert "XHR" not in _LONG_CONNECTION_TYPES
    assert "Fetch" not in _LONG_CONNECTION_TYPES


def test_request_in_flight_then_finish_becomes_idle():
    t = _tracker()
    t._on_request_will_be_sent({"requestId": "r1"})
    assert t.is_idle() is False                     # pending 非空
    t._on_loading_finished({"requestId": "r1"})
    assert t.is_idle() is False                     # pending 空但 lull < window
    time.sleep(_SW + 0.02)
    assert t.is_idle() is True                      # lull >= window


def test_loading_failed_also_retires():
    t = _tracker()
    t._on_request_will_be_sent({"requestId": "r1"})
    assert t.is_idle() is False
    t._on_loading_failed({"requestId": "r1"})       # 失败也释放 pending
    time.sleep(_SW + 0.02)
    assert t.is_idle() is True


def test_response_received_then_finish_idle():
    t = _tracker()
    t._on_request_will_be_sent({"requestId": "r1"})
    t._on_response_received({"requestId": "r1", "type": "XHR"})
    assert t.is_idle() is False                     # XHR 仍在 flight
    t._on_loading_finished({"requestId": "r1"})
    time.sleep(_SW + 0.02)
    assert t.is_idle() is True


# ── 长连接过滤（以 responseReceived.type 为准）──────────────────────


def test_websocket_filtered_keeps_idle():
    t = _tracker()
    t._on_request_will_be_sent({"requestId": "ws1"})
    t._on_response_received({"requestId": "ws1", "type": "WebSocket"})
    # 无 loadingFinished（WebSocket 不结束）——但被过滤，不计入 pending
    time.sleep(_SW + 0.02)
    assert t.is_idle() is True


def test_eventsource_filtered_keeps_idle():
    t = _tracker()
    t._on_request_will_be_sent({"requestId": "es1"})
    t._on_response_received({"requestId": "es1", "type": "EventSource"})
    time.sleep(_SW + 0.02)
    assert t.is_idle() is True


def test_xhr_long_poll_not_filtered():
    t = _tracker()
    t._on_request_will_be_sent({"requestId": "x1"})
    t._on_response_received({"requestId": "x1", "type": "XHR"})
    # XHR 不在过滤集 → pending 永不归零（靠 deadline 兜底）
    time.sleep(_SW + 0.02)
    assert t.is_idle() is False


# ── 稳定窗口 / 去重 / 钳零 ──────────────────────────────────────────


def test_zero_pending_but_recent_activity_not_idle():
    t = _tracker()
    t._on_request_will_be_sent({"requestId": "r1"})
    t._on_loading_finished({"requestId": "r1"})
    assert t.is_idle() is False                     # 刚 finish，lull < window


def test_new_request_resets_lull_window():
    t = _tracker()
    t._on_request_will_be_sent({"requestId": "r1"})
    t._on_loading_finished({"requestId": "r1"})
    time.sleep(_SW * 0.8)                           # 接近但未到 window
    t._on_request_will_be_sent({"requestId": "r2"})
    t._on_loading_finished({"requestId": "r2"})     # 重置 last_activity
    time.sleep(_SW * 0.6)
    assert t.is_idle() is False                     # 从 r2 finish 起算 < window
    time.sleep(_SW * 0.6)
    assert t.is_idle() is True


def test_redirect_reuses_request_id_no_inflate():
    """CDP redirect：同 requestId 多次 requestWillBeSent，set 语义不膨胀。"""
    t = _tracker()
    t._on_request_will_be_sent({"requestId": "r1"})
    t._on_request_will_be_sent({"requestId": "r1"})  # 重复 add
    t._on_loading_finished({"requestId": "r1"})      # 一次 finish 即空
    time.sleep(_SW + 0.02)
    assert t.is_idle() is True


def test_duplicate_retire_clamps_zero():
    """重复 loadingFinished / 不存在的 requestId 退出，discard 幂等，永不负。"""
    t = _tracker()
    t._on_request_will_be_sent({"requestId": "r1"})
    t._on_loading_finished({"requestId": "r1"})
    t._on_loading_finished({"requestId": "r1"})      # 重复
    t._on_loading_failed({"requestId": "nope"})      # 不存在
    time.sleep(_SW + 0.02)
    assert t.is_idle() is True


def test_event_without_request_id_ignored():
    t = _tracker()
    t._on_request_will_be_sent({})                   # 无 requestId
    t._on_response_received({"type": "XHR"})         # 无 requestId
    t._on_loading_finished({})                       # 无 requestId
    time.sleep(_SW + 0.02)
    assert t.is_idle() is True


def test_request_will_be_sent_missing_type_does_not_crash():
    """requestWillBeSent.type 是 NotRequired；缺失时不分类，不崩。"""
    t = _tracker()
    t._on_request_will_be_sent({"requestId": "r1"})  # 无 type 字段
    t._on_loading_finished({"requestId": "r1"})
    time.sleep(_SW + 0.02)
    assert t.is_idle() is True


# ── 降级（disabled / 超时）──────────────────────────────────────────


def test_disabled_is_idle_true():
    """未 register（_enabled=False）→ is_idle 直接 True（等同关闭）。"""
    t = NetworkIdleTracker(stability_window=_SW)
    assert t.enabled is False
    assert t.is_idle() is True


def test_register_failure_disables():
    """register 抛异常 → _enabled=False，降级为即时 idle。"""
    t = NetworkIdleTracker(stability_window=_SW)
    bad_client = MagicMock()
    bad_client.register.Network.requestWillBeSent.side_effect = AttributeError("no Network domain")
    t.register(bad_client, "sid")
    assert t.enabled is False
    assert t.is_idle() is True


@pytest.mark.asyncio
async def test_disabled_wait_until_idle_immediate_true():
    t = NetworkIdleTracker(stability_window=_SW)
    assert await t.wait_until_idle() is True        # 立即返，不进循环


@pytest.mark.asyncio
async def test_wait_until_idle_reaches_idle():
    t = _tracker()
    t._on_request_will_be_sent({"requestId": "r1"})
    t._on_loading_finished({"requestId": "r1"})
    ok = await t.wait_until_idle()                   # sw=0.1, poll=0.02, timeout=0.3
    assert ok is True                                # 约 0.1s 后命中（乐观早退 + 轮询）


@pytest.mark.asyncio
async def test_wait_until_idle_timeout_degrades_false():
    t = _tracker()
    t._on_request_will_be_sent({"requestId": "x1"})
    t._on_response_received({"requestId": "x1", "type": "XHR"})  # 不过滤，pending 永不空
    ok = await t.wait_until_idle(timeout=0.2, poll_interval=0.05)
    assert ok is False                               # 超时降级，不抛错


# ── reset ───────────────────────────────────────────────────────────


def test_reset_clears_inflight():
    t = _tracker()
    t._on_request_will_be_sent({"requestId": "r1"})
    assert t.is_idle() is False
    t.reset()                                        # 模拟 reconnect/switch_tab
    time.sleep(_SW + 0.02)
    assert t.is_idle() is True                       # inflight 已清
