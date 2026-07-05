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


# ── P1d：sensitive_data_description（按 URL 过滤告知可用占位符）──────────────
# 对齐方案：docs/agent-loop-optimize/01-准备上下文对齐browser-use方案.md §6.1


class TestNormalizeSensitiveData:
    """Agent._normalize_sensitive_data：旧 dict[str,str] / 新 {value,urls} 归一化。"""

    def test_old_format_global(self):
        from tree_walker.agent.agent import Agent

        raw = Agent._normalize_sensitive_data({"password": "real123"})
        assert raw == {"password": {"value": "real123", "urls": None}}

    def test_new_format_with_urls(self):
        from tree_walker.agent.agent import Agent

        raw = Agent._normalize_sensitive_data(
            {"password": {"value": "real123", "urls": ["*login*"]}}
        )
        assert raw == {"password": {"value": "real123", "urls": ["*login*"]}}

    def test_none_or_empty(self):
        from tree_walker.agent.agent import Agent

        assert Agent._normalize_sensitive_data(None) is None
        assert Agent._normalize_sensitive_data({}) is None

    def test_skips_empty_value(self):
        from tree_walker.agent.agent import Agent

        raw = Agent._normalize_sensitive_data({"a": "x", "b": "", "c": None})
        assert raw == {"a": {"value": "x", "urls": None}}

    def test_new_format_without_urls_is_global(self):
        from tree_walker.agent.agent import Agent

        raw = Agent._normalize_sensitive_data({"token": {"value": "abc"}})
        assert raw == {"token": {"value": "abc", "urls": None}}


class TestBuildSensitiveDescription:
    """Agent._build_sensitive_description：按 URL 过滤列出可用占位符（只列 key）。"""

    def _agent(self, sensitive_data, task="t"):
        from tree_walker.agent.agent import Agent
        from tree_walker.config import AgentSettings

        return Agent(
            task=task,
            llm=MagicMock(),
            browser=MagicMock(),
            settings=AgentSettings(),
            sensitive_data=sensitive_data,
        )

    def test_none_when_no_sensitive_data(self):
        agent = self._agent(None)
        assert agent._build_sensitive_description("https://example.com") is None

    def test_global_secret_listed_on_any_page(self):
        agent = self._agent({"password": "real123"})
        desc = agent._build_sensitive_description("https://anything.com/page")
        assert desc is not None
        assert "password" in desc
        assert "real123" not in desc  # 只列 key，绝不列真实值

    def test_url_filtered_secret_only_on_match(self):
        agent = self._agent({"password": {"value": "real123", "urls": ["*login*"]}})
        assert agent._build_sensitive_description("https://site.com/login") is not None
        assert agent._build_sensitive_description("https://site.com/home") is None

    def test_never_includes_real_value(self):
        agent = self._agent({"<PWD>": "super-secret-123"})
        desc = agent._build_sensitive_description("https://x.com")
        assert "super-secret-123" not in desc
        assert "<PWD>" in desc

    def test_mixed_global_and_filtered(self):
        agent = self._agent(
            {
                "token": "globalsecret",
                "password": {"value": "pw", "urls": ["*login*"]},
            }
        )
        # 非 login 页：只列全局 token
        desc_home = agent._build_sensitive_description("https://site.com/home")
        assert "token" in desc_home
        assert "password" not in desc_home
        # login 页：两者都列
        desc_login = agent._build_sensitive_description("https://site.com/login")
        assert "token" in desc_login
        assert "password" in desc_login

    def test_real_value_masked_in_safe_task_new_format(self):
        # 新格式下 _safe_task 仍正确脱敏 + _sensitive_map 形如 {real_val: placeholder}
        agent = self._agent(
            {"<PWD>": {"value": "real123", "urls": ["*login*"]}},
            task="Login with real123 now",
        )
        assert agent._sensitive_map == {"real123": "<PWD>"}
        assert "real123" not in agent._safe_task
        assert "<PWD>" in agent._safe_task
