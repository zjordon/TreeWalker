"""Tests for LLMClient URL shortening/restoration and fallback switching."""

from __future__ import annotations

from typing import Any

import asyncio
import os
import time
from unittest.mock import MagicMock, patch

import pytest

from anthropic import APIError, AuthenticationError, RateLimitError

from tree_walker.config import FallbackLLMSettings, LLMSettings
from tree_walker.llm.client import LLMClient


# ── URL shortening tests ──────────────────────────────────────────────


class TestShortenUrls:
    """Tests for LLMClient._shorten_urls_in_messages."""

    def setup_method(self):
        self.client = LLMClient(LLMSettings(api_key="test-key"))

    def test_short_message_unchanged(self):
        """Short URLs (<100 chars) should not be replaced."""
        messages = [{"role": "user", "content": "Visit https://example.com/page for info"}]
        url_map = self.client._shorten_urls_in_messages(messages)
        assert url_map == {}
        assert messages[0]["content"] == "Visit https://example.com/page for info"

    def test_long_url_replaced(self):
        """URLs >=100 chars should be replaced with [u0] marker."""
        long_url = "https://example.com/very/long/path/" + "a" * 80
        messages = [{"role": "user", "content": f"Go to {long_url}"}]
        url_map = self.client._shorten_urls_in_messages(messages)
        assert len(url_map) == 1
        assert "[u0]" in messages[0]["content"]
        assert long_url not in messages[0]["content"]
        assert url_map["[u0]"] == long_url

    def test_multiple_long_urls(self):
        """Multiple long URLs get sequential markers [u0], [u1], etc."""
        url1 = "https://example.com/path1/" + "b" * 80
        url2 = "https://example.com/path2/" + "c" * 80
        messages = [{"role": "user", "content": f"A: {url1} B: {url2}"}]
        url_map = self.client._shorten_urls_in_messages(messages)
        assert len(url_map) == 2
        assert "[u0]" in url_map
        assert "[u1]" in url_map
        assert url_map["[u0]"] == url1
        assert url_map["[u1]"] == url2

    def test_mixed_long_and_short_urls(self):
        """Only long URLs are replaced; short URLs remain untouched."""
        short_url = "https://example.com/short"
        long_url = "https://example.com/long/" + "d" * 80
        messages = [{"role": "user", "content": f"Short: {short_url} Long: {long_url}"}]
        url_map = self.client._shorten_urls_in_messages(messages)
        assert len(url_map) == 1
        assert short_url in messages[0]["content"]
        assert long_url not in messages[0]["content"]
        assert "[u0]" in messages[0]["content"]

    def test_empty_messages(self):
        """Empty message list returns empty map."""
        messages: list[dict[str, Any]] = []
        url_map = self.client._shorten_urls_in_messages(messages)
        assert url_map == {}

    def test_no_urls(self):
        """Messages without URLs are unchanged."""
        messages = [{"role": "user", "content": "No URLs here"}]
        url_map = self.client._shorten_urls_in_messages(messages)
        assert url_map == {}
        assert messages[0]["content"] == "No URLs here"

    def test_multiple_messages(self):
        """URLs across multiple messages are shortened."""
        long_url = "https://example.com/page/" + "e" * 80
        messages = [
            {"role": "user", "content": f"Step 1: {long_url}"},
            {"role": "assistant", "content": "OK"},
            {"role": "user", "content": f"Step 2: {long_url} again"},
        ]
        url_map = self.client._shorten_urls_in_messages(messages)
        assert "[u0]" in messages[0]["content"]
        assert "[u0]" in messages[2]["content"]


# ── URL restoration tests ─────────────────────────────────────────────


class TestRestoreUrls:
    """Tests for LLMClient._restore_urls_in_output."""

    def setup_method(self):
        self.client = LLMClient(LLMSettings(api_key="test-key"))

    def test_empty_map_returns_unchanged(self):
        output = {"action": {"name": "click", "params": {"index": 5}}}
        result = self.client._restore_urls_in_output(output, {})
        assert result == output

    def test_restores_url_in_string(self):
        long_url = "https://example.com/page/" + "a" * 80
        output = {"action": {"name": "navigate", "params": {"url": "[u0]"}}}
        result = self.client._restore_urls_in_output(output, {"[u0]": long_url})
        assert result["action"]["params"]["url"] == long_url

    def test_restores_url_in_nested_structure(self):
        long_url = "https://example.com/page/" + "b" * 80
        output = {"current_state": {"memory": "Visited [u0] earlier"}}
        result = self.client._restore_urls_in_output(output, {"[u0]": long_url})
        assert result["current_state"]["memory"] == f"Visited {long_url} earlier"

    def test_restores_multiple_urls(self):
        url1 = "https://example.com/a/" + "a" * 80
        url2 = "https://example.com/b/" + "b" * 80
        output = {"text": "Go from [u0] to [u1]"}
        result = self.client._restore_urls_in_output(output, {"[u0]": url1, "[u1]": url2})
        assert result["text"] == f"Go from {url1} to {url2}"

    def test_preserves_non_url_content(self):
        output = {"action": {"name": "done", "params": {"success": True, "text": "Task complete"}}}
        result = self.client._restore_urls_in_output(output, {"[u0]": "https://example.com"})
        assert result == output

    def test_restores_in_list_values(self):
        long_url = "https://example.com/page/" + "c" * 80
        output = {"tabs": ["[u0]", "https://short.com"]}
        result = self.client._restore_urls_in_output(output, {"[u0]": long_url})
        assert result["tabs"][0] == long_url
        assert result["tabs"][1] == "https://short.com"


# ── Fallback config tests ─────────────────────────────────────────────


class TestFallbackConfig:
    """Tests for FallbackLLMSettings and LLMSettings.fallback."""

    def test_default_no_fallback(self):
        settings = LLMSettings(api_key="key")
        assert settings.fallback is None

    def test_fallback_settings(self):
        fb = FallbackLLMSettings(model="backup-model", api_key="backup-key")
        settings = LLMSettings(api_key="key", fallback=fb)
        assert settings.fallback is not None
        assert settings.fallback.model == "backup-model"

    def test_load_settings_without_fallback(self):
        with patch.dict(os.environ, {"ZHIPU_API_KEY": "test"}, clear=False):
            # Remove all FALLBACK_ env vars
            for k in ["FALLBACK_LLM_MODEL", "FALLBACK_LLM_API_KEY", "FALLBACK_LLM_BASE_URL", "FALLBACK_LLM_MAX_TOKENS"]:
                os.environ.pop(k, None)
            from tree_walker.config import load_settings
            settings = load_settings()
            assert settings.llm.fallback is None

    def test_load_settings_with_fallback(self):
        env = {
            "ZHIPU_API_KEY": "main-key",
            "FALLBACK_LLM_MODEL": "backup-model",
            "FALLBACK_LLM_API_KEY": "backup-key",
        }
        with patch.dict(os.environ, env, clear=False):
            from tree_walker.config import load_settings
            settings = load_settings()
            assert settings.llm.fallback is not None
            assert settings.llm.fallback.model == "backup-model"
            assert settings.llm.fallback.api_key == "backup-key"

    def test_load_settings_fallback_defaults(self):
        env = {
            "ZHIPU_API_KEY": "main-key",
            "FALLBACK_LLM_MODEL": "backup-model",
        }
        with patch.dict(os.environ, env, clear=False):
            from tree_walker.config import load_settings
            settings = load_settings()
            assert settings.llm.fallback is not None
            # Falls back to main key when FALLBACK_LLM_API_KEY not set
            assert settings.llm.fallback.api_key == "main-key"


# ── Fallback switching tests ──────────────────────────────────────────


class TestFallbackSwitch:
    """Tests for LLMClient fallback switching."""

    def _make_client_with_fallback(self):
        main_settings = LLMSettings(
            model="main-model",
            api_key="main-key",
            fallback=FallbackLLMSettings(
                model="fallback-model",
                api_key="fallback-key",
            ),
        )
        return LLMClient(main_settings)

    def test_no_fallback_configured(self):
        """Client without fallback config does not switch."""
        client = LLMClient(LLMSettings(api_key="key"))
        assert client._fallback_client is None
        assert client._using_fallback is False

    def test_fallback_precreated(self):
        """Fallback client is created during __init__."""
        client = self._make_client_with_fallback()
        assert client._fallback_client is not None
        assert client._using_fallback is False
        assert client.model == "main-model"

    def test_try_switch_success(self):
        """_try_switch_to_fallback switches client and model."""
        client = self._make_client_with_fallback()
        error = RateLimitError(
            message="rate limited",
            response=MagicMock(status_code=429),
            body=None,
        )
        result = client._try_switch_to_fallback(error)
        assert result is True
        assert client._using_fallback is True
        assert client.model == "fallback-model"
        assert client.client is client._fallback_client

    def test_try_switch_idempotent(self):
        """Second call to _try_switch_to_fallback returns False."""
        client = self._make_client_with_fallback()
        error = RateLimitError(
            message="rate limited",
            response=MagicMock(status_code=429),
            body=None,
        )
        assert client._try_switch_to_fallback(error) is True
        assert client._try_switch_to_fallback(error) is False

    def test_try_switch_without_fallback_config(self):
        """Client without fallback config returns False."""
        client = LLMClient(LLMSettings(api_key="key"))
        error = RateLimitError(
            message="rate limited",
            response=MagicMock(status_code=429),
            body=None,
        )
        assert client._try_switch_to_fallback(error) is False


# ── Integration tests ─────────────────────────────────────────────────


class TestGetActionIntegration:
    """Integration tests for get_action with URL shortening and fallback."""

    def _mock_tool_use_response(self, tool_input: dict[str, Any]) -> MagicMock:
        """Create a mock Anthropic response with a tool_use block."""
        block = MagicMock()
        block.type = "tool_use"
        block.name = "agent_response"
        block.input = tool_input
        response = MagicMock()
        response.content = [block]
        return response

    def test_url_shortened_and_restored(self):
        """Long URLs in input are shortened, then restored in output."""
        long_url = "https://example.com/very/deep/path/" + "x" * 80
        settings = LLMSettings(api_key="test-key")
        client = LLMClient(settings)

        mock_response = self._mock_tool_use_response({
            "evaluation_previous_goal": "navigated",
            "memory": f"Visited [u0]",
            "next_goal": "click",
            "action": {"name": "click", "params": {"index": 5}},
        })

        messages = [{"role": "user", "content": f"Go to {long_url}"}]
        with patch.object(client.client.messages, "create", return_value=mock_response):
            result = asyncio.run(
                client.get_action("sys", messages, {"name": "tool"}),
            )

        # URL was restored in the memory field
        assert long_url in result["memory"]
        # Messages were shortened during the call
        assert "[u0]" in messages[0]["content"]

    def test_fallback_on_rate_limit(self):
        """get_action switches to fallback on RateLimitError and retries."""
        main_settings = LLMSettings(
            model="main-model",
            api_key="main-key",
            fallback=FallbackLLMSettings(model="fallback-model", api_key="fallback-key"),
        )
        client = LLMClient(main_settings)

        mock_response = self._mock_tool_use_response({
            "evaluation_previous_goal": "ok",
            "memory": "",
            "next_goal": "done",
            "action": {"name": "done", "params": {"text": "complete", "success": True}},
        })

        # First call raises RateLimitError, second call succeeds
        call_count = 0
        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RateLimitError(
                    message="rate limited",
                    response=MagicMock(status_code=429),
                    body=None,
                )
            return mock_response

        with patch.object(client.client.messages, "create", side_effect=side_effect), \
             patch.object(client._fallback_client.messages, "create", side_effect=side_effect):
            result = asyncio.run(
                client.get_action("sys", [], {"name": "tool"}),
            )

        assert result["action"]["name"] == "done"
        assert client._using_fallback is True
        assert call_count == 2

    def test_authentication_error_triggers_fallback(self):
        """401 (AuthenticationError) is an APIError subclass — fallback covers it.

        Aligns TreeWalker with browser-use's retryable status-code set
        (service.py:1989-1995: 401/402/429/5xx). The SDK exception hierarchy
        routes 401 through ``except (RateLimitError, APIError)``, so
        AuthenticationError triggers the fallback path without explicit
        status-code checking.
        """
        main_settings = LLMSettings(
            model="main-model",
            api_key="main-key",
            fallback=FallbackLLMSettings(model="fallback-model", api_key="fallback-key"),
        )
        client = LLMClient(main_settings)

        mock_response = self._mock_tool_use_response({
            "evaluation_previous_goal": "ok",
            "memory": "",
            "next_goal": "done",
            "action": {"name": "done", "params": {"text": "complete", "success": True}},
        })

        call_count = 0
        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise AuthenticationError(
                    message="invalid api key",
                    response=MagicMock(status_code=401),
                    body=None,
                )
            return mock_response

        with patch.object(client.client.messages, "create", side_effect=side_effect), \
             patch.object(client._fallback_client.messages, "create", side_effect=side_effect):
            result = asyncio.run(
                client.get_action("sys", [], {"name": "tool"}),
            )

        assert result["action"]["name"] == "done"
        assert client._using_fallback is True
        assert call_count == 2

    def test_no_fallback_raises(self):
        """get_action raises RateLimitError when no fallback configured."""
        client = LLMClient(LLMSettings(api_key="test-key"))

        with patch.object(client.client.messages, "create", side_effect=RateLimitError(
            message="rate limited",
            response=MagicMock(status_code=429),
            body=None,
        )):
            with pytest.raises(RateLimitError):
                asyncio.run(
                    client.get_action("sys", [], {"name": "tool"}),
                )


# ── Usage passthrough tests（P6 后续 I2）──────────────────────────────


class TestUsagePassthrough:
    """get_action 把 SDK 返回的 token usage 透传到结果 dict（供 step → ModelResultEvent）。"""

    @staticmethod
    def _response(tool_input: dict[str, Any], *, usage) -> MagicMock:
        block = MagicMock()
        block.type = "tool_use"
        block.name = "agent_response"
        block.input = tool_input
        resp = MagicMock()
        resp.content = [block]
        resp.usage = usage
        return resp

    def test_usage_in_result(self):
        client = LLMClient(LLMSettings(api_key="test-key"))
        usage = MagicMock()
        usage.input_tokens = 123
        usage.output_tokens = 45
        resp = self._response(
            {"evaluation_previous_goal": "", "memory": "", "next_goal": "g",
             "action": {"name": "done", "params": {"text": "x", "success": True}}},
            usage=usage,
        )
        with patch.object(client.client.messages, "create", return_value=resp):
            result = asyncio.run(client.get_action("sys", [], {"name": "tool"}))
        assert result["usage"] == {"input_tokens": 123, "output_tokens": 45}

    def test_usage_none_when_missing(self):
        # provider 无 usage 字段 → result["usage"] 为 None，不崩
        client = LLMClient(LLMSettings(api_key="test-key"))
        resp = self._response(
            {"action": {"name": "done", "params": {"text": "x", "success": True}}},
            usage=None,
        )
        with patch.object(client.client.messages, "create", return_value=resp):
            result = asyncio.run(client.get_action("sys", [], {"name": "tool"}))
        assert result["usage"] is None


# ── Non-blocking loop tests（issue #163）──────────────────────────────


class TestNonBlockingLoop:
    """同步 ``messages.create`` 必须经 ``asyncio.to_thread``——LLM 往返期间事件循环保持
    可调度。否则 tw-web 全部 HTTP 端点随 agent 的 LLM 调用一起卡死（0 CPU 等同步 socket，
    真机观测数十秒到数分钟）。判据：慢 create 期间并发的 ticker 协程持续推进。"""

    @pytest.mark.asyncio
    async def test_get_action_offloads_create_to_thread(self):
        client = LLMClient(LLMSettings(api_key="test-key"))

        def slow_create(**kwargs):
            time.sleep(0.2)  # 模拟一次慢 LLM 往返
            return TestUsagePassthrough._response(
                {"action": {"name": "done", "params": {"text": "x", "success": True}}},
                usage=None,
            )

        client.client.messages.create = slow_create
        ticks = 0

        async def ticker():
            nonlocal ticks
            for _ in range(30):
                await asyncio.sleep(0.01)
                ticks += 1

        task = asyncio.create_task(ticker())
        result = await client.get_action("sys", [{"role": "user", "content": "hi"}], {"name": "t"})
        await task
        assert result["action"]["name"] == "done"
        assert ticks >= 15  # 若 create 阻塞 loop，0.2s 内 ticker 几乎无法推进


# ── Empty/unparseable response retry（P7 task1 附三 R1/R2）────────────


class TestNoParseableResponseRetry:
    """空响应（含 thinking-only）先重试一次，仍败才 fallback done。

    背景：docs/p7/01-task1-trajectory-anatomy.md 附三——thinking 耗尽 max_tokens
    时返回体只剩 thinking 块（无 tool_use/无 text），旧代码零重试直接合成
    done(success=False) 终结任务（两跑分别死于 Step 16/11）。
    """

    @staticmethod
    def _thinking_only_response() -> MagicMock:
        """只含 thinking 块的响应。block.text 显式置 ""：真实 ThinkingBlock 无
        .text；MagicMock 会自动造任意属性，不置空会让 client 文本回退拼接 Mock 而炸。
        """
        block = MagicMock()
        block.type = "thinking"
        block.text = ""
        resp = MagicMock()
        resp.content = [block]
        resp.stop_reason = "max_tokens"
        resp.usage = MagicMock(input_tokens=100, output_tokens=16384)
        return resp

    @staticmethod
    def _tool_use_response() -> MagicMock:
        block = MagicMock()
        block.type = "tool_use"
        block.name = "agent_response"
        block.input = {
            "evaluation_previous_goal": "ok",
            "memory": "m",
            "next_goal": "g",
            "action": {"name": "click", "params": {"index": 1}},
        }
        resp = MagicMock()
        resp.content = [block]
        resp.stop_reason = "end_turn"
        return resp

    def test_retry_once_then_success(self):
        """首次 thinking-only → nudge 重试 → 第二次拿到动作，任务不终止。"""
        client = LLMClient(LLMSettings(api_key="test-key"))
        with patch.object(
            client.client.messages, "create",
            side_effect=[self._thinking_only_response(), self._tool_use_response()],
        ) as mock_create:
            result = asyncio.run(
                client.get_action("sys", [{"role": "user", "content": "hi"}], {"name": "t"}),
            )
        assert mock_create.call_count == 2
        assert result["action"]["name"] == "click"
        # 重试请求带上了明确的 nudge 消息
        retry_msgs = mock_create.call_args_list[1].kwargs["messages"]
        assert any("contained no action" in str(m.get("content", "")) for m in retry_msgs)

    def test_fallback_done_after_retry_exhausted(self, caplog):
        """连续两次 thinking-only → 恰好重试一次后 fallback done，无无限递归。"""
        client = LLMClient(LLMSettings(api_key="test-key"))
        with patch.object(
            client.client.messages, "create",
            side_effect=[self._thinking_only_response(), self._thinking_only_response()],
        ) as mock_create:
            with caplog.at_level("WARNING", logger="tree_walker.llm.client"):
                result = asyncio.run(
                    client.get_action("sys", [{"role": "user", "content": "hi"}], {"name": "t"}),
                )
        assert mock_create.call_count == 2
        assert result["action"]["name"] == "done"
        assert result["action"]["params"]["success"] is False
        # R2：诊断证据（stop_reason / output_tokens / blocks）随 WARNING 落日志
        msgs = [r.getMessage() for r in caplog.records]
        assert any("stop_reason=max_tokens" in m for m in msgs)
        assert any("output_tokens=16384" in m for m in msgs)
        assert any("blocks=['thinking']" in m for m in msgs)
        assert any("still returned no parseable response after retry" in m for m in msgs)


# ── Text-not-tool_use retry cap（P7 02 方案 R4）──────────────────────


class TestTextRetryCap:
    """R4：text-not-tool_use 重试上限（旧实现无限递归，runner 靠 600s 兜底）。

    上限 2 次；超限返回空 dict 哨兵 → step 层既有梯子（clarification 重试 →
    fallback done）接管，总调用次数有界。
    """

    @staticmethod
    def _text_response(text: str = "Let me think about this step by step.") -> MagicMock:
        block = MagicMock()
        block.type = "text"
        block.text = text  # 非 JSON，走 text-retry 分支
        resp = MagicMock()
        resp.content = [block]
        resp.stop_reason = "end_turn"
        return resp

    @staticmethod
    def _tool_use_response() -> MagicMock:
        block = MagicMock()
        block.type = "tool_use"
        block.name = "agent_response"
        block.input = {
            "evaluation_previous_goal": "ok",
            "memory": "m",
            "next_goal": "g",
            "action": {"name": "click", "params": {"index": 1}},
        }
        resp = MagicMock()
        resp.content = [block]
        resp.stop_reason = "end_turn"
        return resp

    def test_cap_exhausted_returns_empty_sentinel(self):
        """连续 3 次文本响应 → 恰好 1+2 次调用后返回 {}，不再递归。"""
        client = LLMClient(LLMSettings(api_key="test-key"))
        with patch.object(
            client.client.messages, "create",
            side_effect=[self._text_response(), self._text_response(), self._text_response()],
        ) as mock_create:
            result = asyncio.run(
                client.get_action("sys", [{"role": "user", "content": "hi"}], {"name": "t"}),
            )
        assert mock_create.call_count == 3  # 1 次初始 + 2 次重试，无界递归被封顶
        assert result == {}  # 哨兵：step 层 _is_valid_action 判假 → 梯子接管

    def test_retry_then_success_with_directive_message(self):
        """第 3 次返回 tool_use → 成功；重试请求带指令型消息。"""
        client = LLMClient(LLMSettings(api_key="test-key"))
        with patch.object(
            client.client.messages, "create",
            side_effect=[self._text_response(), self._text_response(), self._tool_use_response()],
        ) as mock_create:
            result = asyncio.run(
                client.get_action("sys", [{"role": "user", "content": "hi"}], {"name": "t"}),
            )
        assert mock_create.call_count == 3
        assert result["action"]["name"] == "click"
        # R4：重试消息为指令型（压重试轮输出长度）
        retry_msgs = mock_create.call_args_list[1].kwargs["messages"]
        assert any("Do not explain" in str(m.get("content", "")) for m in retry_msgs)
