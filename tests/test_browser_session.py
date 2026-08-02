"""Tests for BrowserSession reconnect functionality."""

from __future__ import annotations

import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tree_walker.browser.session import BrowserSession


def _make_mock_cdp_client(
    target_id: str = "test-target-1",
    session_id: str = "test-session-1",
) -> MagicMock:
    """Create a mock CDPClient with proper send.Target / send.Page / send.DOM attrs."""
    client = MagicMock()
    client.start = AsyncMock()
    client.stop = AsyncMock()

    # client.send.Target.getTargets -> returns target list
    client.send.Target.getTargets = AsyncMock(return_value={
        "targetInfos": [
            {"type": "page", "targetId": target_id, "url": "about:blank", "title": ""}
        ]
    })
    # client.send.Target.attachToTarget -> returns sessionId
    client.send.Target.attachToTarget = AsyncMock(return_value={
        "sessionId": session_id
    })
    # client.send.Page.enable -> returns {}
    client.send.Page.enable = AsyncMock(return_value={})
    # client.send.DOM.enable -> returns {}
    client.send.DOM.enable = AsyncMock(return_value={})

    return client


@pytest.fixture
def mock_cdp_client():
    """Create a default mock CDPClient."""
    return _make_mock_cdp_client()


class TestReconnect:
    """Tests for BrowserSession.reconnect."""

    @pytest.mark.asyncio
    async def test_reconnect_success(self, mock_cdp_client):
        """reconnect() returns True when connection succeeds."""
        with patch("tree_walker.browser.session.CDPClient", return_value=mock_cdp_client):
            session = BrowserSession(ws_url="ws://localhost:9222")
            await session.start()
            assert session.is_connected

            # Simulate disconnect
            session.client = None
            assert not session.is_connected

            # Create a new mock for the reconnect attempt
            reconnect_mock = _make_mock_cdp_client(
                target_id="test-target-2",
                session_id="test-session-2",
            )

            with patch("tree_walker.browser.session.CDPClient", return_value=reconnect_mock):
                result = await session.reconnect()
                assert result is True
                assert session.is_connected
                assert session.current_session_id == "test-session-2"

    @pytest.mark.asyncio
    async def test_reconnect_failure(self, mock_cdp_client):
        """reconnect() returns False when connection fails."""
        with patch("tree_walker.browser.session.CDPClient", return_value=mock_cdp_client):
            session = BrowserSession(ws_url="ws://localhost:9222")
            await session.start()

            # Make reconnect fail by having the new client's start() raise
            fail_mock = MagicMock()
            fail_mock.start = AsyncMock(side_effect=ConnectionError("Connection refused"))
            fail_mock.stop = AsyncMock()

            session.client = None

            with patch("tree_walker.browser.session.CDPClient", return_value=fail_mock):
                result = await session.reconnect()
                assert result is False
                assert not session.is_connected

    @pytest.mark.asyncio
    async def test_is_connected_property(self, mock_cdp_client):
        """is_connected reflects client state."""
        with patch("tree_walker.browser.session.CDPClient", return_value=mock_cdp_client):
            session = BrowserSession(ws_url="ws://localhost:9222")
            assert not session.is_connected

            await session.start()
            assert session.is_connected

            await session.stop()
            assert not session.is_connected


def _make_intercept_mock_client(
    target_id: str = "test-target-1",
    session_id: str = "test-session-1",
) -> MagicMock:
    """Mock CDPClient including the file-chooser intercept command + registration."""
    client = _make_mock_cdp_client(target_id, session_id)
    client.send.Page.setInterceptFileChooserDialog = AsyncMock(return_value={})
    client.send.Target.setAutoAttach = AsyncMock(return_value={})
    client.send.Target.activateTarget = AsyncMock(return_value={})
    client.register.Page.fileChooserOpened = MagicMock()
    return client


class TestFileChooserIntercept:
    """issue #34, Bug 1: Page.setInterceptFileChooserDialog is enabled on every
    session so clicking a file input / upload button never pops the OS native
    picker. Real uploads use DOM.setFileInputFiles (set_file_input), independent
    of this and unaffected.
    """

    @pytest.mark.asyncio
    async def test_connect_enables_intercept_and_registers_handler(self):
        client = _make_intercept_mock_client()
        with patch("tree_walker.browser.session.CDPClient", return_value=client):
            session = BrowserSession(ws_url="ws://localhost:9222")
            await session.start()

        client.send.Page.setInterceptFileChooserDialog.assert_awaited_once()
        call = client.send.Page.setInterceptFileChooserDialog.call_args
        assert call.args[0] == {"enabled": True}
        assert call.kwargs.get("session_id") == "test-session-1"
        client.register.Page.fileChooserOpened.assert_called_once()

    @pytest.mark.asyncio
    async def test_switch_tab_re_enables_intercept_on_new_session(self):
        client = _make_intercept_mock_client(session_id="s1")
        with patch("tree_walker.browser.session.CDPClient", return_value=client):
            session = BrowserSession(ws_url="ws://localhost:9222")
            await session.start()
        # switching tabs attaches a fresh session
        client.send.Target.attachToTarget = AsyncMock(return_value={"sessionId": "s2"})
        session._wait_for_page_settle = AsyncMock()

        await session.switch_tab("test-target-1")

        # intercept re-enabled against the NEW session id (regression guard)
        last = client.send.Page.setInterceptFileChooserDialog.call_args
        assert last.kwargs.get("session_id") == "s2"

    @pytest.mark.asyncio
    async def test_connect_tolerates_unsupported_command(self):
        """Older Chrome lacking the command: best-effort, no crash, still connects."""
        client = _make_mock_cdp_client()
        client.send.Page.setInterceptFileChooserDialog = AsyncMock(
            side_effect=RuntimeError("command not found"),
        )
        with patch("tree_walker.browser.session.CDPClient", return_value=client):
            session = BrowserSession(ws_url="ws://localhost:9222")
            await session.start()  # must not raise

        assert session.is_connected

    def test_file_chooser_callback_logs_without_raising(self, caplog):
        client = _make_intercept_mock_client()
        with patch("tree_walker.browser.session.CDPClient", return_value=client):
            session = BrowserSession(ws_url="ws://localhost:9222")

        with caplog.at_level(logging.INFO, logger="tree_walker.browser.session"):
            session._on_file_chooser_opened(
                {"mode": "selectOpen", "backendNodeId": 99, "frameId": "F1"}, "s1",
            )
        assert "intercepted" in caplog.text.lower()


def _make_network_mock_client() -> MagicMock:
    """Mock CDPClient including Network.enable + the 4 networkidle callbacks."""
    client = _make_intercept_mock_client()
    client.send.Network.enable = AsyncMock(return_value={})
    client.register.Network.requestWillBeSent = MagicMock()
    client.register.Network.responseReceived = MagicMock()
    client.register.Network.loadingFinished = MagicMock()
    client.register.Network.loadingFailed = MagicMock()
    return client


class TestNetworkIdleTracking:
    """阶段3：_connect 启用 Network 域 + 注册 4 回调（always-on）；失败降级。
    tracker 本身见 tests/test_network_idle_tracker.py；get_state 透传见
    tests/test_rerun_history.py::test_networkidle_on_passes_true_to_get_state。"""

    @pytest.mark.asyncio
    async def test_connect_enables_network_and_registers_callbacks(self):
        client = _make_network_mock_client()
        with patch("tree_walker.browser.session.CDPClient", return_value=client):
            session = BrowserSession(ws_url="ws://localhost:9222")
            await session.start()
        client.send.Network.enable.assert_awaited_once()
        assert client.send.Network.enable.call_args.kwargs.get("session_id") == "test-session-1"
        client.register.Network.requestWillBeSent.assert_called_once()
        client.register.Network.responseReceived.assert_called_once()
        client.register.Network.loadingFinished.assert_called_once()
        client.register.Network.loadingFailed.assert_called_once()
        assert session._network_idle_tracker.enabled is True

    @pytest.mark.asyncio
    async def test_connect_tolerates_network_enable_failure(self):
        """Network.enable 抛错 → start() 不崩，tracker 降级 disabled（对齐 recent_events）。"""
        client = _make_network_mock_client()
        client.send.Network.enable = AsyncMock(side_effect=RuntimeError("Network domain unavailable"))
        with patch("tree_walker.browser.session.CDPClient", return_value=client):
            session = BrowserSession(ws_url="ws://localhost:9222")
            await session.start()  # 必须不抛
        assert session.is_connected
        assert session._network_idle_tracker.enabled is False


class TestDiscoverFileInputViaClick:
    """discover_file_input_via_click (issue #34 Bug 2): click a dropzone and
    capture the file input the page opens via Page.fileChooserOpened. Refuses to
    click when interception is off (Bug-1 guard — clicking would pop the native
    dialog). Returns None on no-chooser (custom dialog) so upload_file surfaces
    an honest error instead of guessing among indistinguishable inputs."""

    @pytest.mark.asyncio
    async def test_returns_backend_node_id_when_chooser_fires(self):
        client = _make_intercept_mock_client()
        with patch("tree_walker.browser.session.CDPClient", return_value=client):
            session = BrowserSession(ws_url="ws://localhost:9222")
            await session.start()
        assert session._file_chooser_intercept_enabled is True

        # the click causes the page to open input backendNodeId=42
        async def _click_then_open(bid):
            session._last_file_chooser = {"backendNodeId": 42, "ts": 0.0}
            return True

        session.click_element = AsyncMock(side_effect=_click_then_open)

        got = await session.discover_file_input_via_click(7, timeout=1.0)
        assert got == 42
        session.click_element.assert_awaited_once_with(7)

    @pytest.mark.asyncio
    async def test_returns_none_when_intercept_disabled_and_does_not_click(self):
        client = _make_intercept_mock_client()
        with patch("tree_walker.browser.session.CDPClient", return_value=client):
            session = BrowserSession(ws_url="ws://localhost:9222")
            await session.start()
        session._file_chooser_intercept_enabled = False  # simulate unsupported Chrome
        session.click_element = AsyncMock(return_value=True)

        got = await session.discover_file_input_via_click(7, timeout=0.2)
        assert got is None
        # Bug-1 guard: must NOT click without interception (would pop native dialog)
        session.click_element.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_none_when_no_chooser_within_timeout(self):
        client = _make_intercept_mock_client()
        with patch("tree_walker.browser.session.CDPClient", return_value=client):
            session = BrowserSession(ws_url="ws://localhost:9222")
            await session.start()
        session.click_element = AsyncMock(return_value=True)  # click opens no chooser

        got = await session.discover_file_input_via_click(7, timeout=0.2)
        assert got is None
        session.click_element.assert_awaited_once_with(7)

    def test_callback_records_last_file_chooser(self):
        client = _make_intercept_mock_client()
        with patch("tree_walker.browser.session.CDPClient", return_value=client):
            session = BrowserSession(ws_url="ws://localhost:9222")
        assert session._last_file_chooser is None

        session._on_file_chooser_opened(
            {"mode": "selectOpen", "backendNodeId": 55, "frameId": "F1"}, "s1",
        )
        assert session._last_file_chooser is not None
        assert session._last_file_chooser["backendNodeId"] == 55


class TestSetFileInputAsciiSafe:
	"""set_file_input 透明转 ASCII 文件名（抖音中文封面被前端误判「不支持的图片格式」）。"""

	@pytest.mark.asyncio
	async def test_non_ascii_defers_temp_cleanup_to_stop(self, tmp_path):
		src = tmp_path / "横封面.png"
		src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
		client = _make_mock_cdp_client()
		client.send.DOM.setFileInputFiles = AsyncMock(return_value={})
		session = BrowserSession(ws_url="ws://localhost:9222")
		session.client = client
		session.current_session_id = "sid"

		# 临时副本落在 tmp_path（隔离、便于断言清理）
		with patch(
			"tree_walker.browser.session.tempfile.gettempdir", return_value=str(tmp_path)
		):
			await session.set_file_input(backend_node_id=123, file_path=str(src))

		sent = client.send.DOM.setFileInputFiles.call_args.args[0]
		sent_path = sent["files"][0]
		assert os.path.basename(sent_path).isascii()  # 传给浏览器的名是 ASCII
		assert sent_path.endswith(".png")  # 保留扩展名
		assert sent["backendNodeId"] == 123
		assert src.exists()  # 原文件不动
		# 关键：CDP 返回后临时副本仍存在（浏览器按路径惰性读盘），并已登记待清理
		assert os.path.exists(sent_path)
		assert session._upload_temp_paths == [sent_path]
		# stop() 时才统一清理
		await session.stop()
		assert not os.path.exists(sent_path)
		assert list(tmp_path.glob("tw_upload_*")) == []
		assert session._upload_temp_paths == []

	@pytest.mark.asyncio
	async def test_ascii_filename_passthrough_no_temp_copy(self, tmp_path):
		src = tmp_path / "cover.png"
		src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
		client = _make_mock_cdp_client()
		client.send.DOM.setFileInputFiles = AsyncMock(return_value={})
		session = BrowserSession(ws_url="ws://localhost:9222")
		session.client = client
		session.current_session_id = "sid"

		await session.set_file_input(backend_node_id=7, file_path=str(src))

		sent = client.send.DOM.setFileInputFiles.call_args.args[0]
		assert sent["files"][0] == str(src)  # 原路径透传，无临时副本
		assert sent["backendNodeId"] == 7
		assert src.exists()
		assert list(tmp_path.glob("tw_upload_*")) == []
		assert session._upload_temp_paths == []  # ASCII 不复制临时副本

	@pytest.mark.asyncio
	async def test_temp_persists_after_cdp_failure_then_cleaned_at_stop(self, tmp_path):
		src = tmp_path / "横封面.png"
		src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
		client = _make_mock_cdp_client()
		client.send.DOM.setFileInputFiles = AsyncMock(side_effect=RuntimeError("CDP down"))
		session = BrowserSession(ws_url="ws://localhost:9222")
		session.client = client
		session.current_session_id = "sid"

		with patch(
			"tree_walker.browser.session.tempfile.gettempdir", return_value=str(tmp_path)
		):
			with pytest.raises(RuntimeError):
				await session.set_file_input(backend_node_id=123, file_path=str(src))

		assert src.exists()  # 原文件不动
		# 即使 CDP 抛错，临时副本也已登记（登记在 await 之前），仍存活、由 stop() 清理
		assert len(session._upload_temp_paths) == 1
		pending = session._upload_temp_paths[0]
		assert os.path.exists(pending)
		await session.stop()
		assert not os.path.exists(pending)
		assert list(tmp_path.glob("tw_upload_*")) == []


class TestRediscoverWsUrl:
	"""Chrome 重启后 ws_url 过期（HTTP 404）时 _connect 的自愈重试。"""

	@pytest.mark.asyncio
	async def test_rediscover_new_url_retries_and_connects(self):
		"""首次握手失败 + 重新发现到不同 ws_url → 重建 client 重试成功，ws_url 更新。"""
		fail_client = MagicMock()
		fail_client.start = AsyncMock(side_effect=RuntimeError("HTTP 404"))
		ok_client = _make_mock_cdp_client(target_id="t2", session_id="s2")
		old_url = "ws://localhost:9222/devtools/browser/old-uuid"
		new_url = "ws://localhost:9222/devtools/browser/new-uuid"

		with patch("tree_walker.browser.session.CDPClient", side_effect=[fail_client, ok_client]):
			session = BrowserSession(ws_url=old_url)
			session._rediscover_ws_url = MagicMock(return_value=new_url)
			await session.start()

		assert session.is_connected
		assert session.ws_url == new_url
		assert session.client is ok_client
		fail_client.start.assert_awaited_once()
		ok_client.start.assert_awaited_once()
		session._rediscover_ws_url.assert_called_once()

	@pytest.mark.asyncio
	async def test_rediscover_none_reraises_original(self):
		"""重新发现失败（Chrome 真没开 /json/version）→ 抛原连接异常，不重试，ws_url 不变。"""
		fail_client = MagicMock()
		fail_client.start = AsyncMock(side_effect=RuntimeError("HTTP 404"))

		with patch("tree_walker.browser.session.CDPClient", return_value=fail_client):
			session = BrowserSession(ws_url="ws://localhost:9222/devtools/browser/old")
			session._rediscover_ws_url = MagicMock(return_value=None)
			with pytest.raises(RuntimeError, match="HTTP 404"):
				await session.start()

		# 没进重试分支：ws_url 未改，握手只发生一次
		assert session.ws_url == "ws://localhost:9222/devtools/browser/old"
		fail_client.start.assert_awaited_once()
		session._rediscover_ws_url.assert_called_once()

	@pytest.mark.asyncio
	async def test_rediscover_same_url_reraises_original(self):
		"""重新发现到的 url 与旧相同 → 非 Chrome 重启，不重试，抛原异常。"""
		same_url = "ws://localhost:9222/devtools/browser/same-uuid"
		fail_client = MagicMock()
		fail_client.start = AsyncMock(side_effect=RuntimeError("HTTP 404"))

		with patch("tree_walker.browser.session.CDPClient", return_value=fail_client):
			session = BrowserSession(ws_url=same_url)
			session._rediscover_ws_url = MagicMock(return_value=same_url)
			with pytest.raises(RuntimeError, match="HTTP 404"):
				await session.start()

		assert session.ws_url == same_url
		fail_client.start.assert_awaited_once()
		session._rediscover_ws_url.assert_called_once()


@pytest.mark.asyncio
async def test_is_element_occluded_three_states():
    # Stage4 L3 primitive: elementFromPoint occlusion 3 states + exception degrade.
    # Covers session._is_element_occluded Python wrapper (resolveNode -> callFunctionOn
    # -> bool); JS ancestor walk is browser-side. 3 states: hit unrelated=occluded /
    # hit target-or-ancestor=not occluded / CDP exception=best-effort degrade to False.
    client = _make_mock_cdp_client()
    client.send.DOM.resolveNode = AsyncMock(return_value={"object": {"objectId": "obj-1"}})
    session = BrowserSession(ws_url="ws://localhost:9222")
    session.client = client
    session.current_session_id = "sid"
    client.send.Runtime.callFunctionOn = AsyncMock(return_value={"result": {"value": True}})
    assert await session._is_element_occluded(1, 10, 20) is True
    client.send.Runtime.callFunctionOn = AsyncMock(return_value={"result": {"value": False}})
    assert await session._is_element_occluded(1, 10, 20) is False
    client.send.DOM.resolveNode = AsyncMock(side_effect=RuntimeError("boom"))
    assert await session._is_element_occluded(1, 10, 20) is False


@pytest.mark.asyncio
async def test_eval_function_on_node_returns_value():
    # #151：resolveNode(backendNodeId) + callFunctionOn(this=node, returnByValue) → 返回 JS return 值。
    # 供 upload_identity.capture_upload_clue 在目标 file input 自身提取身份上下文，避开候选计数对齐。
    client = _make_mock_cdp_client()
    client.send.DOM.resolveNode = AsyncMock(return_value={"object": {"objectId": "obj-1"}})
    session = BrowserSession(ws_url="ws://localhost:9222")
    session.client = client
    session.current_session_id = "sid"
    client.send.Runtime.callFunctionOn = AsyncMock(return_value={"result": {"value": {
        "region_text": "点击上传", "in_dialog": True,
        "container_rect": {"x": 10, "y": 10, "width": 5, "height": 5},
    }}})
    out = await session.eval_function_on_node(42, "function(){return this;}")
    assert out["region_text"] == "点击上传"
    assert out["container_rect"]["x"] == 10
    # CDP 调用参数：按 backendNodeId 解析、returnByValue=True
    assert client.send.DOM.resolveNode.call_args.args[0] == {"backendNodeId": 42}
    fn_kwargs = client.send.Runtime.callFunctionOn.call_args.args[0]
    assert fn_kwargs["objectId"] == "obj-1"
    assert fn_kwargs["returnByValue"] is True