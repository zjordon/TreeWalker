"""Tests for BrowserSession reconnect functionality."""

from __future__ import annotations

import logging
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
