"""Tests for sensitive data filtering in LLMClient."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tree_walker.config import LLMSettings
from tree_walker.llm.client import LLMClient


def _make_client() -> LLMClient:
    with patch("tree_walker.llm.client.Anthropic"):
        return LLMClient(LLMSettings(api_key="test-key"))


class TestFilterSensitiveInMessages:
    """Tests for _filter_sensitive_in_messages."""

    def test_replaces_sensitive_value_with_placeholder(self):
        client = _make_client()
        sensitive_map = {"my_secret_password": "<PASSWORD>"}
        messages = [
            {"role": "user", "content": "Login with password my_secret_password now"},
        ]
        client._filter_sensitive_in_messages(messages, sensitive_map)
        assert "<PASSWORD>" in messages[0]["content"]
        assert "my_secret_password" not in messages[0]["content"]

    def test_multiple_sensitive_values(self):
        client = _make_client()
        sensitive_map = {
            "sk-abc123def": "<API_KEY>",
            "my_secret_password": "<PASSWORD>",
        }
        messages = [
            {"role": "user", "content": "Key: sk-abc123def, Pass: my_secret_password"},
        ]
        client._filter_sensitive_in_messages(messages, sensitive_map)
        assert "<API_KEY>" in messages[0]["content"]
        assert "<PASSWORD>" in messages[0]["content"]
        assert "sk-abc123def" not in messages[0]["content"]
        assert "my_secret_password" not in messages[0]["content"]

    def test_empty_map_does_nothing(self):
        client = _make_client()
        messages = [{"role": "user", "content": "no changes"}]
        client._filter_sensitive_in_messages(messages, {})
        assert messages[0]["content"] == "no changes"

    def test_none_map_does_nothing(self):
        client = _make_client()
        messages = [{"role": "user", "content": "no changes"}]
        client._filter_sensitive_in_messages(messages, None)
        assert messages[0]["content"] == "no changes"

    def test_non_string_content_skipped(self):
        client = _make_client()
        sensitive_map = {"secret": "<HIDDEN>"}
        messages = [{"role": "user", "content": [1, 2, 3]}]
        client._filter_sensitive_in_messages(messages, sensitive_map)
        assert messages[0]["content"] == [1, 2, 3]

    def test_returns_map_for_restoration(self):
        client = _make_client()
        sensitive_map = {"secret123": "<SECRET>"}
        messages = [{"role": "user", "content": "use secret123"}]
        result = client._filter_sensitive_in_messages(messages, sensitive_map)
        assert result == sensitive_map


class TestRestoreSensitiveInOutput:
    """Tests for _restore_sensitive_in_output."""

    def test_restores_placeholder_to_real_value(self):
        client = _make_client()
        sensitive_map = {"secret123": "<SECRET>"}
        output = {
            "action": {"name": "input_text", "params": {"text": "use <SECRET> here"}},
        }
        result = client._restore_sensitive_in_output(output, sensitive_map)
        assert result["action"]["params"]["text"] == "use secret123 here"

    def test_restores_nested_values(self):
        client = _make_client()
        sensitive_map = {"sk-key": "<API_KEY>"}
        output = {
            "action": {"name": "navigate", "params": {"url": "https://api.example.com?token=<API_KEY>"}},
        }
        result = client._restore_sensitive_in_output(output, sensitive_map)
        assert "sk-key" in result["action"]["params"]["url"]

    def test_empty_map_returns_unchanged(self):
        client = _make_client()
        output = {"action": {"name": "done"}}
        result = client._restore_sensitive_in_output(output, {})
        assert result == output

    def test_none_map_returns_unchanged(self):
        client = _make_client()
        output = {"action": {"name": "done"}}
        result = client._restore_sensitive_in_output(output, None)
        assert result == output


class TestSensitiveDataIntegration:
    """Tests for Agent-level sensitive data integration."""

    def test_agent_creates_safe_task(self):
        from tree_walker.agent.agent import Agent
        from tree_walker.config import AgentSettings

        llm = MagicMock()
        browser = MagicMock()
        agent = Agent(
            task="Login with password my_secret_pass",
            llm=llm,
            browser=browser,
            settings=AgentSettings(),
            sensitive_data={"<PASSWORD>": "my_secret_pass"},
        )
        assert "my_secret_pass" not in agent._safe_task
        assert "<PASSWORD>" in agent._safe_task
        assert agent._sensitive_map == {"my_secret_pass": "<PASSWORD>"}

    def test_agent_no_sensitive_data(self):
        from tree_walker.agent.agent import Agent
        from tree_walker.config import AgentSettings

        llm = MagicMock()
        browser = MagicMock()
        agent = Agent(
            task="Simple task",
            llm=llm,
            browser=browser,
            settings=AgentSettings(),
        )
        assert agent._safe_task == "Simple task"
        assert agent._sensitive_map is None

    def test_sensitive_map_passed_to_llm_client(self):
        from tree_walker.agent.agent import Agent
        from tree_walker.config import AgentSettings

        llm = MagicMock()
        browser = MagicMock()
        agent = Agent(
            task="Use key sk-abc123",
            llm=llm,
            browser=browser,
            settings=AgentSettings(),
            sensitive_data={"<API_KEY>": "sk-abc123"},
        )
        assert llm._sensitive_map == {"sk-abc123": "<API_KEY>"}
