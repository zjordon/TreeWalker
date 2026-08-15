"""Tests for ``_LatestFrameSlot`` + ``_make_screencast_frame_handler``（直播视口，P6 后续 A）。

这两者位于 ``web/server.py``，是 livestream 推流的纯逻辑核心：
- ``_LatestFrameSlot``：覆盖式最新帧 + asyncio.Event（慢消费者不堆积）。
- ``_make_screencast_frame_handler``：CDP WS 读线程回调 → ``call_soon_threadsafe`` 移交 loop
  设槽 + 安排 ``screencastFrameAck``。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from tree_walker.web.server import (
	_LatestFrameSlot,
	_make_screencast_frame_handler,
)


class TestLatestFrameSlot:
	@pytest.mark.asyncio
	async def test_take_none_when_empty(self):
		slot = _LatestFrameSlot()
		assert slot.take() is None
		assert slot.pending is False

	@pytest.mark.asyncio
	async def test_set_then_take_clears(self):
		slot = _LatestFrameSlot()
		slot.set({"data": "frame1"})
		assert slot.pending is True
		assert slot.take() == {"data": "frame1"}
		assert slot.take() is None  # 取后清空
		assert slot.pending is False

	@pytest.mark.asyncio
	async def test_overwrite_keeps_only_latest(self):
		"""连续 set 多帧 → take 只返最新（中间帧丢弃，杜绝慢消费者堆积）。"""
		slot = _LatestFrameSlot()
		slot.set({"i": 1})
		slot.set({"i": 2})
		slot.set({"i": 3})
		assert slot.take() == {"i": 3}
		assert slot.take() is None

	def test_ever_received_tracks_frame_arrival(self):
		"""ever_received 用于看门狗判断「Chrome 是否真的推过帧」。"""
		slot = _LatestFrameSlot()
		assert slot.ever_received is False
		slot.set({"data": "x"})
		assert slot.ever_received is True
		slot.take()
		assert slot.ever_received is True  # take 不复位（只增不减）

	@pytest.mark.asyncio
	async def test_wait_wakes_after_set(self):
		slot = _LatestFrameSlot()

		async def producer():
			await asyncio.sleep(0)  # 让消费者先 await wait()
			slot.set({"data": "x"})

		asyncio.ensure_future(producer())
		await asyncio.wait_for(slot.wait(), timeout=1.0)
		assert slot.take() == {"data": "x"}

	@pytest.mark.asyncio
	async def test_wait_timeout_raises(self):
		slot = _LatestFrameSlot()
		with pytest.raises(asyncio.TimeoutError):
			await slot.wait(timeout=0.01)

	@pytest.mark.asyncio
	async def test_take_resets_event_so_next_wait_blocks(self):
		slot = _LatestFrameSlot()
		slot.set({"a": 1})
		slot.take()
		# 取后 Event 复位 → 再 wait 应阻塞（这里用短 timeout 验证不立即就绪）
		with pytest.raises(asyncio.TimeoutError):
			await slot.wait(timeout=0.01)


def _make_handler_fixture():
	"""构造 handler + 可控 loop（call_soon_threadsafe 记录但不执行）+ mock browser。"""
	slot = _LatestFrameSlot()
	scheduled: list = []

	loop = MagicMock()

	def fake_call_soon(fn, *args):
		scheduled.append((fn, args))

	loop.call_soon_threadsafe = fake_call_soon

	browser = MagicMock()
	browser.current_session_id = "s1"
	browser.client = MagicMock()
	browser.client.send.Page.screencastFrameAck = AsyncMock(return_value={})

	handler = _make_screencast_frame_handler(loop, slot, browser)
	return handler, slot, scheduled, browser


def _run_named(scheduled, name):
	"""执行 scheduled 里第一个名为 name 的回调（模拟 loop 跑），返回它。"""
	fn = next((f for f, _ in scheduled if f.__name__ == name), None)
	assert fn is not None, f"未排到 {name} 回调"
	fn()
	return fn


class TestScreencastFrameHandler:
	def test_single_frame_delivered_via_loop(self):
		"""回调在 WS 线程触发 → 帧经 _deliver（call_soon_threadsafe）移交 loop，不直接碰 asyncio 对象。"""
		handler, slot, scheduled, browser = _make_handler_fixture()
		handler({
			"data": "AAA",
			"metadata": {"deviceWidth": 1280, "deviceHeight": 800, "pageScaleFactor": 2},
			"sessionId": 7,
		})
		# 1 投递 + 1 ack 排到 loop
		assert [f.__name__ for f, _ in scheduled] == ["_deliver", "_spawn_ack"]
		_run_named(scheduled, "_deliver")
		frame = slot.take()
		assert frame is not None
		assert frame["type"] == "screencast"
		assert frame["data"] == "data:image/jpeg;base64,AAA"
		assert frame["width"] == 1280
		assert frame["height"] == 800
		assert frame["scale"] == 2

	def test_burst_coalesces_to_single_delivery_latest_wins(self):
		"""关键：loop 忙时 burst N 帧 → 只排 1 次 _deliver（带最新帧），杜绝积压追帧（修画面滞后）。"""
		handler, slot, scheduled, browser = _make_handler_fixture()
		for i in range(3):
			handler({"data": f"F{i}", "sessionId": i})
		delivers = [f for f, _ in scheduled if f.__name__ == "_deliver"]
		acks = [f for f, _ in scheduled if f.__name__ == "_spawn_ack"]
		assert len(delivers) == 1   # 3 帧合并为 1 次投递
		assert len(acks) == 3       # 每帧仍各 ack（Chrome 流控需要 ack 才推下一帧）
		delivers[0]()
		assert slot.take()["data"] == "data:image/jpeg;base64,F2"  # 只剩最新帧

	def test_delivery_paces_with_interval_window(self):
		"""节流窗节奏（修卡顿）：首帧立即投 + 开 interval 窗；窗内新帧由窗到期 _pump 续投
		（不丢出帧节奏）；窗到期无帧则挂起，下一帧重新触发——三种状态机全验。"""
		slot = _LatestFrameSlot()
		scheduled: list = []
		loop = MagicMock()
		loop.call_soon_threadsafe = lambda fn, *a: scheduled.append((fn, a))
		browser = MagicMock()
		browser.current_session_id = "s1"
		browser.client = MagicMock()
		handler = _make_screencast_frame_handler(loop, slot, browser)

		# ① 第 1 帧：立即投递，并开节流窗
		handler({"data": "F1", "sessionId": 1})
		_run_named(scheduled, "_deliver")
		assert slot.take()["data"].endswith("F1")
		loop.call_later.assert_called_once()
		interval, pump = loop.call_later.call_args[0]
		assert 0 < interval <= 0.2

		# ② 窗内来了第 2 帧：不新排投递（节奏不乱），由窗到期 _pump 续投最新帧
		handler({"data": "F2", "sessionId": 2})
		assert [f.__name__ for f, _ in scheduled].count("_deliver") == 1
		pump()
		assert slot.take()["data"].endswith("F2")
		assert loop.call_later.call_count == 2  # 续投后再开新窗

		# ③ 新窗到期无新帧 → 管道挂起；下一帧重新走 call_soon_threadsafe 触发
		_pump2 = loop.call_later.call_args[0][1]
		_pump2()
		handler({"data": "F3", "sessionId": 3})
		assert [f.__name__ for f, _ in scheduled].count("_deliver") == 2

	def test_missing_data_yields_null_data(self):
		handler, slot, scheduled, browser = _make_handler_fixture()
		handler({"metadata": {}, "sessionId": 1})
		_run_named(scheduled, "_deliver")
		assert slot.take()["data"] is None

	@pytest.mark.asyncio
	async def test_acks_each_frame_with_screencast_session_id(self, monkeypatch):
		"""ack 用帧事件里的 sessionId（screencast 会话 id），非 target session_id；异常被取走。"""
		handler, slot, scheduled, browser = _make_handler_fixture()
		captured_coros = []
		# _spawn_ack 内 asyncio.ensure_future(...) → patch 之捕获协程，并返一个带 add_done_callback 的 mock
		monkeypatch.setattr(asyncio, "ensure_future",
		                    lambda coro: captured_coros.append(coro) or MagicMock())
		handler({"data": "AAA", "sessionId": 9})
		_run_named(scheduled, "_spawn_ack")  # 模拟 loop 跑 _spawn_ack → ensure_future(_ack())

		assert len(captured_coros) == 1
		await captured_coros[0]  # 跑 _ack 协程 → 调 screencastFrameAck
		browser.client.send.Page.screencastFrameAck.assert_awaited_once_with(
			{"sessionId": 9}, session_id="s1")

	def test_no_ack_without_client(self):
		"""browser.client 为 None（已断开）时不再 ack，避免属性错误。"""
		slot = _LatestFrameSlot()
		scheduled: list = []
		loop = MagicMock()
		loop.call_soon_threadsafe = lambda fn, *a: scheduled.append((fn, a))
		browser = MagicMock()
		browser.client = None  # 已断开

		handler = _make_screencast_frame_handler(loop, slot, browser)
		handler({"data": "AAA", "sessionId": 3})

		assert [f.__name__ for f, _ in scheduled] == ["_deliver"]  # 只投递，无 ack
		_run_named(scheduled, "_deliver")
		assert slot.take()["data"] == "data:image/jpeg;base64,AAA"

	def test_accepts_positional_cdp_session_id(self):
		"""cdp_use 回调签名是 (event, session_id)——session_id 位置传参不应报错。"""
		handler, slot, scheduled, browser = _make_handler_fixture()
		handler({"data": "BBB", "sessionId": 2}, "some-target-session")  # 第二位置参
		_run_named(scheduled, "_deliver")
		assert slot.take()["data"] == "data:image/jpeg;base64,BBB"
