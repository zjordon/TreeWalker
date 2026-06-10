"""LLM-based message compaction for long agent conversations."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from tree_walker.config import MessageCompactionSettings
from tree_walker.llm.client import LLMClient

logger = logging.getLogger(__name__)

_COMPACTION_SYSTEM_PROMPT = (
    "You are summarizing a browser automation agent's conversation history for context compaction.\n"
    "Preserve: task requirements, key facts, URLs, file paths, data collected, "
    "decisions made, errors encountered, and partial progress.\n"
    "Be concise but complete. Omit redundant state descriptions."
)


class MessageCompactor:
    """Compresses old conversation messages into a summary using a separate LLM."""

    def __init__(
        self,
        settings: MessageCompactionSettings,
        fallback_llm: LLMClient,
    ) -> None:
        if settings.llm:
            self._llm = LLMClient(settings=settings.llm)
        else:
            self._llm = fallback_llm
        self._settings = settings
        self._compacted_memory: str | None = None
        self._last_compaction_step: int = 0

    async def maybe_compact(
        self,
        messages: list[dict[str, Any]],
        step_number: int,
    ) -> None:
        """Check dual gates and compress messages if both pass.

        Modifies *messages* in-place.
        """
        settings = self._settings

        # Gate 1: step interval
        steps_since = step_number - self._last_compaction_step
        if steps_since < settings.compact_every_n_steps:
            return

        # Gate 2: character count
        full_text = "\n".join(m.get("content", "") for m in messages)
        if len(full_text) < settings.trigger_char_count:
            return

        # Build compaction input
        sections: list[str] = []
        if self._compacted_memory:
            sections.append(
                f"<previous_compacted_memory>\n{self._compacted_memory}\n</previous_compacted_memory>"
            )
        sections.append(f"<conversation_history>\n{full_text}\n</conversation_history>")
        compaction_input = "\n\n".join(sections)

        # Call compaction LLM
        try:
            summary = await self._generate_summary(compaction_input)
        except Exception as e:
            logger.warning("Compaction LLM call failed, skipping: %s", e)
            return

        if not summary:
            logger.warning("Compaction LLM returned empty summary, skipping")
            return

        # Truncate summary if configured
        if settings.summary_max_chars and len(summary) > settings.summary_max_chars:
            summary = summary[: settings.summary_max_chars]

        # Trim messages: keep first + summary + last N
        keep_last = max(0, settings.keep_last_items)
        if len(messages) <= keep_last + 1:
            return

        first = messages[0]
        tail = messages[-keep_last:] if keep_last > 0 else []
        summary_msg: dict[str, Any] = {
            "role": "user",
            "content": f"[Conversation Summary]\n{summary}",
        }
        messages[:] = [first, summary_msg, *tail]

        # Update internal state
        self._compacted_memory = summary
        self._last_compaction_step = step_number

        logger.info(
            "Compacted messages at step %d: %d -> %d messages, summary %d chars",
            step_number,
            len(messages),
            2 + keep_last,
            len(summary),
        )

    async def _generate_summary(self, text: str) -> str:
        """Call the compaction LLM to generate a summary."""
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self._llm.client.messages.create(
                model=self._llm.model,
                max_tokens=2048,
                system=_COMPACTION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": text}],
            ),
        )
        text_parts = [b.text for b in response.content if hasattr(b, "text")]
        return "\n".join(text_parts).strip()
