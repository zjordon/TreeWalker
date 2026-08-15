"""Tests for CDP screencast（直播视口，P6 后续 A）in BrowserSession.

镜像 ``test_download_tracking.py`` 的 mock 套路（patch CDPClient + mock send/register）。
覆盖：start_screencast 传参、幂等 stop、configure→start() 自动起、stop() 收尾、
未连接拒绝、未配置不自动起。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tree_walker.browser.session import BrowserSession


def _make_mock_client():
	"""Mock CDPClient：覆盖 _connect() 必需 + screencast 全套（仿 test_download_tracking）。"""
	client = MagicMock()
	client.start = AsyncMock()
	client.stop = AsyncMock()
	client.send = MagicMock()
	client.send.Target.getTargets = AsyncMock(return_value={
		"targetInfos": [
			{"type": "page", "targetId": "t1", "url": "about:blank", "title": ""}
		]
	})
	client.send.Target.attachToTarget = AsyncMock(return_value={"sessionId": "s1"})
	client.send.Page.enable = AsyncMock(return_value={})
	client.send.DOM.enable = AsyncMock(return_value={})
	# Network.enable 不 mock → _connect 内 try/except 降级（同 test_download_tracking）
	# 直播视口 screencast 命令/ack
	client.send.Page.startScreencast = AsyncMock(return_value={})
	client.send.Page.stopScreencast = AsyncMock(return_value={})
	client.send.Page.screencastFrameAck = AsyncMock(return_value={})
	client.register = MagicMock()
	client.register.Page = MagicMock()
	return client


class TestScreencastStartStop:
	@pytest.mark.asyncio
	async def test_start_screencast_registers_and_starts(self):
		with patch("tree_walker.browser.session.CDPClient") as MockCDP:
			mock_client = _make_mock_client()
			MockCDP.return_value = mock_client
			session = BrowserSession(ws_url="ws://localhost:9222")
			await session.start()

			cb = MagicMock()
			await session.start_screencast(
				cb, format="jpeg", quality=50, max_width=1024, every_nth_frame=3)

			# 注册帧回调（单回调覆盖式）
			mock_client.register.Page.screencastFrame.assert_called_once_with(cb)
			# startScreencast 传参：只限宽保宽高比 + everyNthFrame 源头限速
			mock_client.send.Page.startScreencast.assert_called_once()
			args, kwargs = mock_client.send.Page.startScreencast.call_args
			params = args[0]
			assert params["format"] == "jpeg"
			assert params["quality"] == 50
			assert params["maxWidth"] == 1024
			assert params["everyNthFrame"] == 3
			assert "maxHeight" not in params  # 关键：只限宽，高亮层百分比依赖宽高比
			assert kwargs["session_id"] == "s1"  # target 会话 id（非 screencast sessionId）
			assert session._screencast_on is True

	@pytest.mark.asyncio
	async def test_start_defaults_when_no_optional_params(self):
		with patch("tree_walker.browser.session.CDPClient") as MockCDP:
			mock_client = _make_mock_client()
			MockCDP.return_value = mock_client
			session = BrowserSession(ws_url="ws://localhost:9222")
			await session.start()
			await session.start_screencast(lambda *a: None)  # 全用默认值

			params = mock_client.send.Page.startScreencast.call_args[0][0]
			assert params["format"] == "jpeg"
			assert params["quality"] == 60
			assert "maxWidth" not in params  # max_width=None → 不设
			assert params["everyNthFrame"] == 4

	@pytest.mark.asyncio
	async def test_start_requires_connect(self):
		# client is None（未 start）→ 拒绝，避免对 None 发 CDP
		session = BrowserSession(ws_url="ws://localhost:9222")
		with pytest.raises(RuntimeError):
			await session.start_screencast(lambda *a: None)

	@pytest.mark.asyncio
	async def test_start_is_idempotent_stops_existing_first(self):
		with patch("tree_walker.browser.session.CDPClient") as MockCDP:
			mock_client = _make_mock_client()
			MockCDP.return_value = mock_client
			session = BrowserSession(ws_url="ws://localhost:9222")
			await session.start()

			await session.start_screencast(lambda *a: None)
			assert mock_client.send.Page.stopScreencast.call_count == 0  # 首次无需 stop

			await session.start_screencast(lambda *a: None)  # 重复 → 先 stop 再 start
			assert mock_client.send.Page.stopScreencast.call_count == 1
			assert mock_client.send.Page.startScreencast.call_count == 2

	@pytest.mark.asyncio
	async def test_stop_screencast_is_noop_when_not_streaming(self):
		with patch("tree_walker.browser.session.CDPClient") as MockCDP:
			mock_client = _make_mock_client()
			MockCDP.return_value = mock_client
			session = BrowserSession(ws_url="ws://localhost:9222")
			await session.start()

			await session.stop_screencast()  # 未起推流 → no-op，不发 CDP
			assert mock_client.send.Page.stopScreencast.call_count == 0

	@pytest.mark.asyncio
	async def test_stop_screencast_after_start_then_idempotent(self):
		with patch("tree_walker.browser.session.CDPClient") as MockCDP:
			mock_client = _make_mock_client()
			MockCDP.return_value = mock_client
			session = BrowserSession(ws_url="ws://localhost:9222")
			await session.start()
			await session.start_screencast(lambda *a: None)

			await session.stop_screencast()
			assert mock_client.send.Page.stopScreencast.call_count == 1
			assert session._screencast_on is False

			await session.stop_screencast()  # 已停 → no-op
			assert mock_client.send.Page.stopScreencast.call_count == 1

	@pytest.mark.asyncio
	async def test_stop_screencast_safe_when_client_gone(self):
		"""_screencast_on=True 但 client 已被置空（异常态）→ 不抛、不发 CDP。"""
		with patch("tree_walker.browser.session.CDPClient") as MockCDP:
			mock_client = _make_mock_client()
			MockCDP.return_value = mock_client
			session = BrowserSession(ws_url="ws://localhost:9222")
			await session.start()
			await session.start_screencast(lambda *a: None)
			session.client = None  # 模拟 client 在推流中被置空
			await session.stop_screencast()  # 不抛
			assert session._screencast_on is False
			assert mock_client.send.Page.stopScreencast.call_count == 0  # client None → 不发

	@pytest.mark.asyncio
	async def test_stop_screencast_swallows_cdp_error(self):
		"""stopScreencast CDP 调用失败也不抛（收尾路径，吞异常防卡 stop）。"""
		with patch("tree_walker.browser.session.CDPClient") as MockCDP:
			mock_client = _make_mock_client()
			mock_client.send.Page.stopScreencast = AsyncMock(side_effect=RuntimeError("cdp gone"))
			MockCDP.return_value = mock_client
			session = BrowserSession(ws_url="ws://localhost:9222")
			await session.start()
			await session.start_screencast(lambda *a: None)
			await session.stop_screencast()  # 吞异常，不抛
			assert session._screencast_on is False


class TestScreencastLifecycle:
	@pytest.mark.asyncio
	async def test_configure_screencast_autostarts_on_start(self):
		"""run 前 configure → start() 会话就绪自动起（browser 侧、零 race）。"""
		with patch("tree_walker.browser.session.CDPClient") as MockCDP:
			mock_client = _make_mock_client()
			MockCDP.return_value = mock_client
			session = BrowserSession(ws_url="ws://localhost:9222")

			cb = MagicMock()
			session.configure_screencast(cb, max_width=800, every_nth_frame=5)
			assert session._screencast_sink == (cb, {"max_width": 800, "every_nth_frame": 5})
			assert session._screencast_on is False  # configure 不立即起

			await session.start()  # 会话就绪 → 自动 startScreencast
			mock_client.send.Page.startScreencast.assert_called_once()
			mock_client.register.Page.screencastFrame.assert_called_once_with(cb)
			params = mock_client.send.Page.startScreencast.call_args[0][0]
			assert params["maxWidth"] == 800
			assert params["everyNthFrame"] == 5
			assert session._screencast_on is True

	@pytest.mark.asyncio
	async def test_no_autostart_when_not_configured(self):
		with patch("tree_walker.browser.session.CDPClient") as MockCDP:
			mock_client = _make_mock_client()
			MockCDP.return_value = mock_client
			session = BrowserSession(ws_url="ws://localhost:9222")
			await session.start()
			mock_client.send.Page.startScreencast.assert_not_called()
			assert session._screencast_on is False

	@pytest.mark.asyncio
	async def test_configure_autostart_failure_degrades_silently(self):
		"""自动起失败不应拖垮 start()（降级为无直播）。"""
		with patch("tree_walker.browser.session.CDPClient") as MockCDP:
			mock_client = _make_mock_client()
			mock_client.send.Page.startScreencast = AsyncMock(side_effect=RuntimeError("boom"))
			MockCDP.return_value = mock_client
			session = BrowserSession(ws_url="ws://localhost:9222")
			session.configure_screencast(lambda *a: None)
			await session.start()  # 不抛
			assert session._screencast_on is False
			assert session.client is mock_client  # 连接仍成功

	@pytest.mark.asyncio
	async def test_stop_stops_screencast_before_disconnect(self):
		"""stop() 必须在 client.stop() 之前停推流（CDP 会话还活着才能干净 stop）。"""
		with patch("tree_walker.browser.session.CDPClient") as MockCDP:
			mock_client = _make_mock_client()
			MockCDP.return_value = mock_client
			call_order = []
			mock_client.send.Page.stopScreencast = AsyncMock(
				side_effect=lambda *a, **k: call_order.append("stopScreencast"))
			mock_client.stop = AsyncMock(side_effect=lambda: call_order.append("client.stop"))
			session = BrowserSession(ws_url="ws://localhost:9222")
			session.configure_screencast(lambda *a: None)
			await session.start()
			assert session._screencast_on is True

			await session.stop()
			# 顺序：stopScreencast 先于 client.stop
			assert call_order == ["stopScreencast", "client.stop"]
			assert session._screencast_on is False
			assert session.client is None

	@pytest.mark.asyncio
	async def test_stop_noop_when_screencast_never_started(self):
		"""未起推流时 stop() 不应发 stopScreencast。"""
		with patch("tree_walker.browser.session.CDPClient") as MockCDP:
			mock_client = _make_mock_client()
			MockCDP.return_value = mock_client
			session = BrowserSession(ws_url="ws://localhost:9222")
			await session.start()
			await session.stop()
			mock_client.send.Page.stopScreencast.assert_not_called()
