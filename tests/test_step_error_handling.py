"""Tests for step error handling with reconnect."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tree_walker.agent.views import AgentState


class FakeAgent:
    """Minimal agent-like object for testing _handle_step_error."""

    def __init__(self, reconnect_timeout=5):
        self.state = AgentState()
        self.max_failures = 5
        self.reconnect_timeout = reconnect_timeout
        self.browser = MagicMock()
        self.browser.reconnect = AsyncMock()


def _make_connection_error(msg="websocket connection closed"):
    return ConnectionError(msg)


class TestHandleStepErrorReconnect:
    """Tests for _handle_step_error reconnect behavior."""

    @pytest.mark.asyncio
    async def test_connection_error_triggers_reconnect_attempt(self):
        """Connection error should attempt reconnect before stopping."""
        from tree_walker.agent.step import StepPipeline

        agent = FakeAgent(reconnect_timeout=3)
        agent.browser.reconnect = AsyncMock(return_value=True)

        await StepPipeline._handle_step_error(agent, _make_connection_error())

        assert not agent.state.stopped
        assert agent.browser.reconnect.call_count == 1
        assert agent.state.last_result is not None
        assert "recovered" in str(agent.state.last_result[0].error).lower()

    @pytest.mark.asyncio
    async def test_connection_error_stops_after_timeout(self):
        """Agent stops when reconnect times out."""
        from tree_walker.agent.step import StepPipeline

        agent = FakeAgent(reconnect_timeout=2)
        agent.browser.reconnect = AsyncMock(return_value=False)

        await StepPipeline._handle_step_error(agent, _make_connection_error())

        assert agent.state.stopped
        assert agent.browser.reconnect.call_count == 2

    def test_non_connection_error_increments_failures(self):
        """Non-connection errors still increment failure count."""
        from tree_walker.agent.step import StepPipeline

        agent = FakeAgent()
        # For non-connection errors, the method is async but the branch doesn't await anything
        # so we can call it synchronously via asyncio.run
        asyncio.run(StepPipeline._handle_step_error(agent, ValueError("some error")))

        assert not agent.state.stopped
        assert agent.state.consecutive_failures == 1
        agent.browser.reconnect.assert_not_called()
