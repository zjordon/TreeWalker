"""Tests for JSONL event recorder."""
from __future__ import annotations

import json
import os

from tree_walker.observability.event_bus import EventBus
from tree_walker.observability.events import StepStartEvent, StepEndEvent
from tree_walker.observability.jsonl_recorder import JsonlRecorder


def test_recorder_creates_file_and_writes_events(tmp_path):
    log_dir = str(tmp_path / "logs")
    recorder = JsonlRecorder(session_id="s1", log_dir=log_dir)
    recorder.register(lambda e: None)  # dummy subscribe callback

    event = StepStartEvent(step=1, session_id="s1")
    recorder.handle(event)
    recorder.close()

    log_file = os.path.join(log_dir, "agent_s1.jsonl")
    assert os.path.exists(log_file)

    with open(log_file) as f:
        lines = f.readlines()
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["event_type"] == "step_start"
    assert data["step"] == 1


def test_recorder_writes_multiple_events(tmp_path):
    log_dir = str(tmp_path / "logs")
    recorder = JsonlRecorder(session_id="s1", log_dir=log_dir)
    recorder.register(lambda e: None)

    recorder.handle(StepStartEvent(step=1, session_id="s1"))
    recorder.handle(StepEndEvent(
        step=1, session_id="s1", duration_seconds=1.0,
        is_done=False, consecutive_failures=0,
    ))
    recorder.close()

    log_file = os.path.join(log_dir, "agent_s1.jsonl")
    with open(log_file) as f:
        lines = f.readlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event_type"] == "step_start"
    assert json.loads(lines[1])["event_type"] == "step_end"


def test_recorder_with_event_bus(tmp_path):
    log_dir = str(tmp_path / "logs")
    bus = EventBus()
    recorder = JsonlRecorder(session_id="s1", log_dir=log_dir)
    bus.subscribe("*", recorder.handle)
    recorder.register(bus.on_close)

    bus.emit(StepStartEvent(step=1, session_id="s1"))
    bus.close()

    log_file = os.path.join(log_dir, "agent_s1.jsonl")
    with open(log_file) as f:
        lines = f.readlines()
    assert len(lines) == 1
