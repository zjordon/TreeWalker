"""Soft loop detection — nudges the LLM when repeated actions are detected.

Two detection dimensions (aligned to browser-use's ``ActionLoopDetector``):
  1. Action repetition — per-action-type semantic hash in a sliding window.
  2. Page stagnation   — 3-dim page fingerprint (url + element_count + dom text hash).

Soft only: never blocks actions. Returns a nudge string that gets injected into
the LLM context for the next step.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from dataclasses import dataclass


def _normalize_action_for_hash(name: str, params: dict) -> str:
    """Normalize action params for similarity hashing.

    Adapted to TreeWalker's action vocabulary (NOT a copy of browser-use's
    action names/params). Same logical action → same normalized string:
      - search     : keyword order / case / punctuation agnostic (sorted token set)
      - click      : by element identity (``index``, falling back to ``element_id``)
      - input_text : by element identity + normalized text (different text ⇒ different action)
      - navigate   : by ``url`` (``new_tab`` ignored — same URL still signals a loop)
      - scroll     : by ``direction`` (``amount`` ignored)
      - <default>  : action name + sorted non-None params

    Known limitations (documented in docs/loop-detector-optimize/01-...md §4.1.6):
      - click: ``index`` and ``element_id`` are treated interchangeably; if the LLM
        alternates between them for the same element, the hashes may differ.
    """

    def _element_id() -> str:
        idx = params.get("index")
        return str(idx if idx is not None else params.get("element_id"))

    if name == "search":
        query = str(params.get("query", ""))
        tokens = sorted(set(re.sub(r"[^\w\s]", " ", query.lower()).split()))
        engine = params.get("engine", "baidu")
        return f"search|{engine}|{'|'.join(tokens)}"
    if name == "click":
        return f"click|{_element_id()}"
    if name == "input_text":
        text = str(params.get("text", "")).strip().lower()
        return f"input_text|{_element_id()}|{text}"
    if name == "navigate":
        return f"navigate|{params.get('url', '')}"
    if name == "scroll":
        return f"scroll|{params.get('direction', 'down')}"
    # Default: action name + sorted non-None params
    filtered = {k: v for k, v in sorted(params.items()) if v is not None}
    return f"{name}|{json.dumps(filtered, sort_keys=True, default=str)}"


def compute_action_hash(name: str, params: dict) -> str:
    """Stable 12-char hash (sha256[:12], 48-bit) — mirrors browser-use ``compute_action_hash``."""
    normalized = _normalize_action_for_hash(name, params)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class PageFingerprint:
    """Lightweight fingerprint of a page state: ``url`` + ``element_count`` + dom text hash."""

    url: str
    element_count: int
    text_hash: str  # first 16 chars of sha256 of the DOM text representation

    @staticmethod
    def from_state(url: str, dom_text: str, element_count: int) -> PageFingerprint:
        text_hash = hashlib.sha256(dom_text.encode("utf-8", errors="replace")).hexdigest()[:16]
        return PageFingerprint(url=url, element_count=element_count, text_hash=text_hash)


class ActionLoopDetector:
    """Tracks action repetition and page stagnation to detect stuck loops.

    Does not block actions. Instead, returns nudge messages that get injected
    into the LLM context for the next step.
    """

    def __init__(self, window_size: int = 20) -> None:
        self.recent_actions: deque[str] = deque(maxlen=window_size)
        self.recent_page_fingerprints: deque[PageFingerprint] = deque(maxlen=5)
        self.max_repetition_count: int = 0
        self.most_repeated_hash: str | None = None
        self.consecutive_stagnant_pages: int = 0

    def record_action(self, name: str, params: dict) -> None:
        """Record an action (already filtered for exempt actions) and update repetition stats."""
        self.recent_actions.append(compute_action_hash(name, params))
        self._update_repetition_stats()

    def record_page_state(self, url: str, dom_text: str, element_count: int) -> None:
        """Record the current page fingerprint and update the stagnation counter."""
        fp = PageFingerprint.from_state(url, dom_text, element_count)
        if self.recent_page_fingerprints and self.recent_page_fingerprints[-1] == fp:
            self.consecutive_stagnant_pages += 1
        else:
            self.consecutive_stagnant_pages = 0
        self.recent_page_fingerprints.append(fp)

    def _update_repetition_stats(self) -> None:
        """Recompute ``max_repetition_count`` / ``most_repeated_hash`` from the current window."""
        if not self.recent_actions:
            self.max_repetition_count = 0
            self.most_repeated_hash = None
            return
        counts: dict[str, int] = {}
        for h in self.recent_actions:
            counts[h] = counts.get(h, 0) + 1
        self.most_repeated_hash = max(counts, key=lambda g: counts[g])
        self.max_repetition_count = counts[self.most_repeated_hash]

    def get_nudge_message(self) -> str | None:
        """Return an escalating nudge from action repetition and/or page stagnation, or None."""
        # min-3 guard (more conservative than browser-use; harmless since the >=5 threshold dominates)
        if len(self.recent_actions) < 3 and self.consecutive_stagnant_pages < 5:
            return None

        messages: list[str] = []
        n = len(self.recent_actions)
        if self.max_repetition_count >= 12:
            messages.append(
                f"Heads up: you have repeated a similar action {self.max_repetition_count} times "
                f"in the last {n} actions. "
                "If you are making progress with each repetition, keep going. "
                "If not, a different approach might get you there faster."
            )
        elif self.max_repetition_count >= 8:
            messages.append(
                f"Heads up: you have repeated a similar action {self.max_repetition_count} times "
                f"in the last {n} actions. "
                "Are you still making progress with each attempt? "
                "If so, carry on. Otherwise, it might be worth trying a different approach."
            )
        elif self.max_repetition_count >= 5:
            messages.append(
                f"Heads up: you have repeated a similar action {self.max_repetition_count} times "
                f"in the last {n} actions. "
                "If this is intentional and making progress, carry on. "
                "If not, it might be worth reconsidering your approach."
            )

        if self.consecutive_stagnant_pages >= 5:
            messages.append(
                f"The page content has not changed across {self.consecutive_stagnant_pages} consecutive actions. "
                "Your actions might not be having the intended effect. "
                "It could be worth trying a different element or approach."
            )

        if messages:
            return "\n\n".join(messages)
        return None
