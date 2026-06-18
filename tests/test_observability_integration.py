"""Integration test: observability wired into StepPipeline with mocked LLM/browser."""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from tree_walker.agent.agent import Agent
from tree_walker.agent.views import ActionResult
from tree_walker.browser.views import BrowserStateSummary, SerializedDOMState
from tree_walker.config import AgentSettings
from tree_walker.llm.client import LLMClient
from tree_walker.tools.actions import Tools


@pytest.fixture
def mock_components(tmp_path):
    llm = MagicMock(spec=LLMClient)
    llm.get_action = AsyncMock(return_value={
        "evaluation_previous_goal": "done",
        "memory": "",
        "next_goal": "click button",
        "action": {"name": "click", "params": {"index": 0}},
    })
    llm._sensitive_map = None

    browser = MagicMock()
    browser.get_state = AsyncMock(return_value=BrowserStateSummary(
        url="https://example.com",
        title="Example",
        tabs=[],
        dom_state=SerializedDOMState(
            _root=None,
            element_tree_text="",
            selector_map={},
        ),
        screenshot=None,
    ))
    browser.get_current_url = AsyncMock(return_value="https://example.com")
    browser.start = AsyncMock()
    browser.stop = AsyncMock()
    browser.reconnect = AsyncMock(return_value=False)
    browser.current_target_id = "target1"
    browser.consume_completed_downloads = MagicMock(return_value=[])

    tools = MagicMock(spec=Tools)
    tools.execute = AsyncMock(return_value=ActionResult())
    tools.registry = MagicMock()
    tools.registry.get_action_descriptions_text = MagicMock(return_value="click: Click element")
    tools.registry.get_tool_schema = MagicMock(return_value={
        "name": "agent_response",
        "description": "Respond",
        "input_schema": {},
    })

    settings = AgentSettings(
        max_steps=3,
        max_failures=3,
        enable_observability=True,
        observability_log_dir=str(tmp_path / "logs"),
    )

    return llm, browser, tools, settings, tmp_path


@pytest.mark.asyncio
async def test_observability_creates_jsonl(mock_components):
    llm, browser, tools, settings, tmp_path = mock_components

    agent = Agent(
        task="Test task",
        llm=llm,
        browser=browser,
        tools=tools,
        settings=settings,
    )

    await agent.browser.start()
    done = await agent._step()
    agent._finalize_session()
    await agent.browser.stop()

    log_dir = str(tmp_path / "logs")
    files = [f for f in os.listdir(log_dir) if f.endswith(".jsonl")]
    assert len(files) == 1

    log_path = os.path.join(log_dir, files[0])
    with open(log_path) as f:
        lines = f.readlines()

    events = [json.loads(line) for line in lines]
    event_types = [e["event_type"] for e in events]

    assert "step_start" in event_types
    assert "model_call" in event_types
    assert "model_result" in event_types
    assert "tool_call" in event_types
    assert "tool_result" in event_types
    assert "step_end" in event_types
    assert "session_end" in event_types


@pytest.mark.asyncio
async def test_observability_off_no_jsonl(mock_components):
    llm, browser, tools, _, tmp_path = mock_components
    settings = AgentSettings(
        max_steps=3,
        max_failures=3,
        enable_observability=False,
        observability_log_dir=str(tmp_path / "logs"),
    )

    agent = Agent(
        task="Test task",
        llm=llm,
        browser=browser,
        tools=tools,
        settings=settings,
    )

    await agent.browser.start()
    await agent._step()
    agent._finalize_session()
    await agent.browser.stop()

    log_dir = str(tmp_path / "logs")
    assert not os.path.exists(log_dir)


@pytest.mark.asyncio
async def test_model_call_id_links_events(mock_components):
    llm, browser, tools, settings, tmp_path = mock_components

    agent = Agent(
        task="Test task",
        llm=llm,
        browser=browser,
        tools=tools,
        settings=settings,
    )

    await agent.browser.start()
    await agent._step()
    agent._finalize_session()
    await agent.browser.stop()

    log_dir = str(tmp_path / "logs")
    files = [f for f in os.listdir(log_dir) if f.endswith(".jsonl")]
    log_path = os.path.join(log_dir, files[0])
    with open(log_path) as f:
        events = [json.loads(line) for line in f]

    model_call_events = [e for e in events if e["event_type"] == "model_call"]
    model_result_events = [e for e in events if e["event_type"] == "model_result"]
    tool_call_events = [e for e in events if e["event_type"] == "tool_call"]

    assert len(model_call_events) == 1
    mc_id = model_call_events[0]["model_call_id"]

    assert len(model_result_events) == 1
    assert model_result_events[0]["model_call_id"] == mc_id

    assert len(tool_call_events) == 1
    assert tool_call_events[0]["model_call_id"] == mc_id
