"""Tests for step error handling with reconnect."""

from __future__ import annotations

import asyncio
from typing import Any
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


class TestPostLlmStopCheck:
    """P0-1: post-LLM double stop/pause check in _get_next_action (browser-use service.py:1191/1197)."""

    _RESPONSE = {
        "evaluation_previous_goal": "ok",
        "memory": "",
        "next_goal": "click",
        "action": {"name": "click", "params": {"index": 1}},
        "actions": [{"name": "click", "params": {"index": 1}}],
    }

    def _make_agent(self, response: dict[str, Any]) -> Any:
        """Minimal fake agent exposing what _get_next_action touches."""
        agent = MagicMock()
        agent.state = AgentState()
        agent.state.n_steps = 3
        agent.messages: list[dict[str, Any]] = []
        agent._obs_bus = None
        agent._obs_session_id = "test"
        agent._current_model_call_id = ""
        agent.max_actions_per_step = 5
        agent.llm_timeout = 120
        agent._enable_message_typing = False
        agent._save_conversation_path = ""
        agent._trim_messages = MagicMock(return_value=[{"role": "user", "content": "hi"}])
        agent._get_action_with_retry = AsyncMock(return_value=response)
        agent._truncate_actions = MagicMock(return_value=response)
        agent._save_conversation = MagicMock()
        return agent

    @pytest.mark.asyncio
    async def test_stopped_during_llm_returns_none(self):
        """Check #1: stopped set during the LLM call → output discarded, returns None."""
        from tree_walker.agent.step import StepPipeline

        agent = self._make_agent(self._RESPONSE)
        agent.state.stopped = True
        result = await StepPipeline._get_next_action(agent, MagicMock(), "state msg")
        assert result is None

    @pytest.mark.asyncio
    async def test_paused_during_llm_returns_none(self):
        """Check #1: paused set during the LLM call → output discarded, returns None."""
        from tree_walker.agent.step import StepPipeline

        agent = self._make_agent(self._RESPONSE)
        agent.state.paused = True
        result = await StepPipeline._get_next_action(agent, MagicMock(), "state msg")
        assert result is None

    @pytest.mark.asyncio
    async def test_stopped_does_not_pollute_history(self):
        """When stopped, no assistant message is appended to self.messages."""
        from tree_walker.agent.step import StepPipeline

        agent = self._make_agent(self._RESPONSE)
        agent.state.stopped = True
        await StepPipeline._get_next_action(agent, MagicMock(), "state msg")
        assert agent.messages == []

    @pytest.mark.asyncio
    async def test_normal_returns_response_and_records_history(self):
        """No stop signal → response returned and assistant message appended once."""
        from tree_walker.agent.step import StepPipeline

        agent = self._make_agent(self._RESPONSE)
        result = await StepPipeline._get_next_action(agent, MagicMock(), "state msg")
        assert result is not None
        assert result["action"]["name"] == "click"
        assert len(agent.messages) == 1

    @pytest.mark.asyncio
    async def test_stopped_before_append_returns_response_no_history(self):
        """Check #2: stopped in the window between side effects and history commit.

        _truncate_actions runs after check #1; flipping stopped there simulates the
        user stopping between the two checks. Check #2 must return the response
        (side effects already ran) but NOT append to history.
        """
        from tree_walker.agent.step import StepPipeline

        agent = self._make_agent(self._RESPONSE)

        def truncate_and_stop(response):
            agent.state.stopped = True
            return response

        agent._truncate_actions = MagicMock(side_effect=truncate_and_stop)
        result = await StepPipeline._get_next_action(agent, MagicMock(), "state msg")
        assert result is not None
        assert result["action"]["name"] == "click"
        assert agent.messages == []
