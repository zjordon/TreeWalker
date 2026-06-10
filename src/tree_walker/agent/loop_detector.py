"""Soft loop detection — nudges the LLM when repeated actions are detected."""

from __future__ import annotations

import json
from collections import deque


class ActionLoopDetector:
    """Tracks recent actions and page states to detect stuck loops.

    Does not block actions. Instead, returns nudge messages that get injected
    into the LLM context for the next step.
    """

    def __init__(self, window_size: int = 15) -> None:
        self.recent_actions: deque[str] = deque(maxlen=window_size)
        self.recent_urls: deque[str] = deque(maxlen=window_size)

    def record_action(self, name: str, params: dict) -> None:
        key_params = {k: v for k, v in params.items() if k not in ("text", "clear")}
        action_hash = f"{name}:{json.dumps(key_params, sort_keys=True, default=str)}"
        self.recent_actions.append(action_hash)

    def record_page(self, url: str) -> None:
        self.recent_urls.append(url)

    def get_nudge_message(self) -> str | None:
        if len(self.recent_actions) < 3:
            return None

        counts: dict[str, int] = {}
        for h in self.recent_actions:
            counts[h] = counts.get(h, 0) + 1

        max_count = max(counts.values())
        if max_count >= 12:
            return (
                "CRITICAL: You have repeated the same action 12+ times. "
                "You must immediately try a completely different approach or call done. "
                "Continuing the same action will not succeed."
            )
        if max_count >= 8:
            return (
                "WARNING: You have repeated the same action 8+ times. "
                "You are likely stuck in a loop. Try a completely different approach."
            )
        if max_count >= 5:
            return (
                "WARNING: You have repeated the same action 5+ times. "
                "Consider whether you are making progress or need a different strategy."
            )
        return None
