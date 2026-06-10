"""Tests for Agent pause/resume mechanism."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from tree_walker.agent.agent import Agent
from tree_walker.config import AgentSettings


def _make_agent():
    """Create an Agent with mocked dependencies."""
    llm = MagicMock()
    browser = MagicMock()
    browser.start = AsyncMock()
    browser.stop = AsyncMock()
    browser.navigate = AsyncMock()
    return Agent(
        task="test task",
        llm=llm,
        browser=browser,
        settings=AgentSettings(max_steps=3),
    )


class TestPauseResume:
    """Tests for pause/resume control flow."""

    def test_pause_sets_state(self):
        """pause() sets state.paused=True and clears resume event."""
        agent = _make_agent()
        assert not agent.state.paused
        agent.pause()
        assert agent.state.paused

    def test_resume_clears_paused_state(self):
        """resume() sets state.paused=False and sets resume event."""
        agent = _make_agent()
        agent.pause()
        assert agent.state.paused
        agent.resume()
        assert not agent.state.paused

    def test_double_ctrl_c_then_stop(self):
        """Two Ctrl+C calls via signal handler: first pauses, second stops."""
        agent = _make_agent()
        agent._loop = asyncio.new_event_loop()

        assert agent._ctrl_c_count == 0

        # First Ctrl+C: pause
        agent._sigint_handler(2, None)
        # call_soon_threadsafe schedules pause on the loop, run it
        agent._loop.run_until_complete(asyncio.sleep(0))
        assert agent.state.paused
        assert not agent.state.stopped
        assert agent._ctrl_c_count == 1

        # Second Ctrl+C: stop
        agent._sigint_handler(2, None)
        agent._loop.run_until_complete(asyncio.sleep(0))
        assert agent.state.stopped
        agent._loop.close()

    def test_pause_without_signal_handler(self):
        """pause() can be called directly as a public API."""
        agent = _make_agent()
        agent.pause()
        assert agent.state.paused
        # _ctrl_c_count is NOT incremented by pause() directly
        assert agent._ctrl_c_count == 0

    def test_resume_event_is_set_by_default(self):
        """_resume_event starts set (non-paused state)."""
        agent = _make_agent()
        assert agent._resume_event.is_set()

    def test_pause_clears_resume_event(self):
        """pause() clears _resume_event so await will block."""
        agent = _make_agent()
        agent.pause()
        assert not agent._resume_event.is_set()

    def test_resume_sets_resume_event(self):
        """resume() sets _resume_event so await will unblock."""
        agent = _make_agent()
        agent.pause()
        assert not agent._resume_event.is_set()
        agent.resume()
        assert agent._resume_event.is_set()

    def test_stop_always_sets_resume_event(self):
        """stop() also sets _resume_event to unblock any waiting."""
        agent = _make_agent()
        agent.pause()
        assert not agent._resume_event.is_set()
        agent.stop()
        assert agent._resume_event.is_set()
