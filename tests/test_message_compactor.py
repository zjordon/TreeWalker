"""Tests for MessageCompactor — gates, trimming, incremental compression."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from tree_walker.agent.message_compactor import MessageCompactor
from tree_walker.config import LLMSettings, MessageCompactionSettings


def _make_compactor(
    *,
    enabled: bool = True,
    compact_every_n_steps: int = 5,
    trigger_char_count: int = 100,
    keep_last_items: int = 2,
    summary_max_chars: int | None = None,
) -> tuple[MessageCompactor, MagicMock]:
    """Create a compactor with a mock LLM that returns a fixed summary."""
    settings = MessageCompactionSettings(
        enabled=enabled,
        compact_every_n_steps=compact_every_n_steps,
        trigger_char_count=trigger_char_count,
        keep_last_items=keep_last_items,
        summary_max_chars=summary_max_chars,
    )
    # Mock fallback LLM
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="[Summary of conversation history]")]
    mock_llm.client.messages.create.return_value = mock_response
    mock_llm.model = "test-model"
    return MessageCompactor(settings, mock_llm), mock_llm


def _make_messages(count: int, content_size: int = 50) -> list[dict[str, Any]]:
    """Generate messages with predictable content."""
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "x" * content_size + f" msg{i}"}
        for i in range(count)
    ]


def run(coro: Any) -> Any:
    return asyncio.run(coro)


class TestGateOneStepInterval:
    """Gate 1: compaction skips when step interval not reached."""

    def test_below_threshold(self) -> None:
        compactor, mock_llm = _make_compactor(compact_every_n_steps=5)
        messages = _make_messages(10, content_size=50)
        run(compactor.maybe_compact(messages, step_number=3))
        # No LLM call should have been made
        mock_llm.client.messages.create.assert_not_called()
        assert len(messages) == 10

    def test_at_threshold(self) -> None:
        compactor, mock_llm = _make_compactor(compact_every_n_steps=5)
        messages = _make_messages(10, content_size=50)
        run(compactor.maybe_compact(messages, step_number=5))
        # Gate 1 passes, gate 2 should also pass (10 * 50 = 500 > 100)
        mock_llm.client.messages.create.assert_called_once()


class TestGateTwoCharCount:
    """Gate 2: compaction skips when total chars below threshold."""

    def test_below_threshold(self) -> None:
        compactor, mock_llm = _make_compactor(
            compact_every_n_steps=1,
            trigger_char_count=10000,
        )
        messages = _make_messages(5, content_size=50)
        run(compactor.maybe_compact(messages, step_number=10))
        mock_llm.client.messages.create.assert_not_called()


class TestTrimming:
    """Messages are trimmed to first + summary + last N."""

    def test_keeps_first_summary_and_tail(self) -> None:
        compactor, _ = _make_compactor(
            compact_every_n_steps=1,
            trigger_char_count=10,
            keep_last_items=2,
        )
        messages = _make_messages(10, content_size=20)
        first_content = messages[0]["content"]
        run(compactor.maybe_compact(messages, step_number=5))
        # first + summary + 2 tail = 4
        assert len(messages) == 4
        assert messages[0]["content"] == first_content
        assert "[Conversation Summary]" in messages[1]["content"]
        assert messages[2] is not None
        assert messages[3] is not None

    def test_no_trim_when_few_messages(self) -> None:
        compactor, mock_llm = _make_compactor(
            compact_every_n_steps=1,
            trigger_char_count=10,
            keep_last_items=2,
        )
        messages = _make_messages(3, content_size=20)
        run(compactor.maybe_compact(messages, step_number=5))
        # 3 messages <= keep_last(2) + 1, so LLM is called but no trimming occurs
        # Messages remain unchanged because len(messages) <= keep_last + 1
        assert len(messages) == 3
        mock_llm.client.messages.create.assert_called_once()


class TestIncrementalCompression:
    """Second compaction includes previous summary as previous_compacted_memory."""

    def test_includes_previous_summary(self) -> None:
        compactor, mock_llm = _make_compactor(
            compact_every_n_steps=1,
            trigger_char_count=10,
            keep_last_items=2,
        )
        messages = _make_messages(10, content_size=20)
        run(compactor.maybe_compact(messages, step_number=5))

        # Second compaction with new messages
        messages.extend(_make_messages(5, content_size=20))
        # Offset content to distinguish
        for i, m in enumerate(messages[-5:]):
            m["content"] = "new_" + m["content"]
        run(compactor.maybe_compact(messages, step_number=10))

        # LLM called twice
        assert mock_llm.client.messages.create.call_count == 2
        # Second call should include previous_compacted_memory
        second_call_args = mock_llm.client.messages.create.call_args_list[1]
        user_msg = second_call_args.kwargs["messages"][0]["content"]
        assert "previous_compacted_memory" in user_msg


class TestSummaryTruncation:
    """Summary is truncated when summary_max_chars is set."""

    def test_truncates_long_summary(self) -> None:
        compactor, mock_llm = _make_compactor(
            compact_every_n_steps=1,
            trigger_char_count=10,
            keep_last_items=2,
            summary_max_chars=50,
        )
        # Mock returns a long summary
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="x" * 200)]
        mock_llm.client.messages.create.return_value = mock_response

        messages = _make_messages(10, content_size=20)
        run(compactor.maybe_compact(messages, step_number=5))

        summary_msg = messages[1]["content"]
        # "[Conversation Summary]\n" = 24 chars + 50 chars summary
        assert len(summary_msg) <= 80


class TestDefaultOff:
    """Compaction is off by default — Agent works without it."""

    def test_agent_instantiation_without_compaction(self) -> None:
        from tree_walker.config import AgentSettings

        settings = AgentSettings()
        assert settings.message_compaction is None
