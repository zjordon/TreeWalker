"""Tests for download tracking in BrowserSession."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tree_walker.browser.session import BrowserSession


def _make_mock_client():
    """Create a mock CDPClient with event registration support."""
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
    client.send.Browser.setDownloadBehavior = AsyncMock(return_value={})
    client.register = MagicMock()
    client.register.Browser = MagicMock()
    return client


class TestDownloadTracking:
    """Tests for download tracking."""

    @pytest.mark.asyncio
    async def test_consume_returns_empty_when_no_downloads(self):
        with patch("tree_walker.browser.session.CDPClient") as MockCDP:
            mock_client = _make_mock_client()
            MockCDP.return_value = mock_client
            session = BrowserSession(ws_url="ws://localhost:9222")
            await session.start()
            result = session.consume_completed_downloads()
            assert result == []

    @pytest.mark.asyncio
    async def test_consume_returns_completed_downloads(self):
        with patch("tree_walker.browser.session.CDPClient") as MockCDP:
            mock_client = _make_mock_client()
            MockCDP.return_value = mock_client
            session = BrowserSession(ws_url="ws://localhost:9222")
            await session.start(track_downloads=True)

            # Simulate a completed download
            session._completed_downloads.append({
                "filename": "report.pdf",
                "url": "https://example.com/report.pdf",
                "path": "/tmp/report.pdf",
            })
            result = session.consume_completed_downloads()
            assert len(result) == 1
            assert result[0]["filename"] == "report.pdf"
            assert session.consume_completed_downloads() == []

    @pytest.mark.asyncio
    async def test_consume_is_destructive(self):
        with patch("tree_walker.browser.session.CDPClient") as MockCDP:
            mock_client = _make_mock_client()
            MockCDP.return_value = mock_client
            session = BrowserSession(ws_url="ws://localhost:9222")
            await session.start(track_downloads=True)

            session._completed_downloads.append({"filename": "a.pdf", "url": "u", "path": None})
            session._completed_downloads.append({"filename": "b.pdf", "url": "v", "path": None})

            first = session.consume_completed_downloads()
            assert len(first) == 2
            second = session.consume_completed_downloads()
            assert len(second) == 0

    @pytest.mark.asyncio
    async def test_setup_registers_cdp_events(self):
        with patch("tree_walker.browser.session.CDPClient") as MockCDP:
            mock_client = _make_mock_client()
            MockCDP.return_value = mock_client
            session = BrowserSession(ws_url="ws://localhost:9222")
            await session.start(track_downloads=True)

            mock_client.send.Browser.setDownloadBehavior.assert_called_once()
            call_args = mock_client.send.Browser.setDownloadBehavior.call_args
            assert call_args[0][0]["eventsEnabled"] is True
            # behavior="allow" MUST carry a downloadPath or CDP rejects with
            # -32602 "downloadPath not provided" (the bug this guards against).
            assert call_args[0][0]["downloadPath"]

            mock_client.register.Browser.downloadWillBegin.assert_called_once()
            mock_client.register.Browser.downloadProgress.assert_called_once()

    @pytest.mark.asyncio
    async def test_setup_default_download_path_is_user_downloads(self, monkeypatch):
        # No explicit path and no env → resolve to the user's ~/Downloads.
        monkeypatch.delenv("DOWNLOADS_PATH", raising=False)
        with patch("tree_walker.browser.session.CDPClient") as MockCDP:
            mock_client = _make_mock_client()
            MockCDP.return_value = mock_client
            session = BrowserSession(ws_url="ws://localhost:9222")
            await session.start(track_downloads=True)

            call_args = mock_client.send.Browser.setDownloadBehavior.call_args
            expected = os.path.join(os.path.expanduser("~"), "Downloads")
            assert call_args[0][0]["downloadPath"] == expected

    @pytest.mark.asyncio
    async def test_setup_explicit_download_path_is_passed_through(self, tmp_path):
        target = tmp_path / "dl"
        with patch("tree_walker.browser.session.CDPClient") as MockCDP:
            mock_client = _make_mock_client()
            MockCDP.return_value = mock_client
            session = BrowserSession(ws_url="ws://localhost:9222")
            await session.start(track_downloads=True, downloads_path=str(target))

            call_args = mock_client.send.Browser.setDownloadBehavior.call_args
            assert call_args[0][0]["downloadPath"] == str(target)

    @pytest.mark.asyncio
    async def test_setup_env_overrides_default_download_path(self, monkeypatch, tmp_path):
        env_dir = tmp_path / "envdl"
        monkeypatch.setenv("DOWNLOADS_PATH", str(env_dir))
        with patch("tree_walker.browser.session.CDPClient") as MockCDP:
            mock_client = _make_mock_client()
            MockCDP.return_value = mock_client
            session = BrowserSession(ws_url="ws://localhost:9222")
            await session.start(track_downloads=True)

            call_args = mock_client.send.Browser.setDownloadBehavior.call_args
            assert call_args[0][0]["downloadPath"] == str(env_dir)
