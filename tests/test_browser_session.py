"""Tests for BrowserSession reconnect functionality."""

from __future__ import annotations

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
