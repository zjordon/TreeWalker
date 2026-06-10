"""JSONL file recorder — subscribes to all events and writes one JSON per line."""
from __future__ import annotations

import os
from typing import Callable

from tree_walker.observability.events import BaseEvent


class JsonlRecorder:
    """Writes every event as a JSON line to ``log_dir/agent_{session_id}.jsonl``."""

    def __init__(self, session_id: str, log_dir: str = "logs") -> None:
        self._path = os.path.join(log_dir, f"agent_{session_id}.jsonl")
        os.makedirs(log_dir, exist_ok=True)
        self._file = open(self._path, "a", encoding="utf-8")

    def register(self, subscribe_fn: Callable[[Callable], None]) -> None:
        """Convenience: ``recorder.register(bus.on_close)`` registers cleanup."""
        subscribe_fn(self.close)

    def handle(self, event: BaseEvent) -> None:
        self._file.write(event.model_dump_json() + "\n")
        self._file.flush()

    def close(self) -> None:
        if not self._file.closed:
            self._file.flush()
            self._file.close()
