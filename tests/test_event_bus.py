"""Tests for EventBus publish-subscribe."""
from __future__ import annotations

from tree_walker.observability.event_bus import EventBus
from tree_walker.observability.events import StepStartEvent, StepEndEvent


def test_emit_calls_matching_handler():
    bus = EventBus()
    received = []
    bus.subscribe("step_start", lambda e: received.append(e))

    event = StepStartEvent(step=1, session_id="s1")
    bus.emit(event)

    assert len(received) == 1
    assert received[0].step == 1


def test_emit_ignores_non_matching_handlers():
    bus = EventBus()
    received = []
    bus.subscribe("step_end", lambda e: received.append(e))

    bus.emit(StepStartEvent(step=1, session_id="s1"))

    assert len(received) == 0


def test_multiple_handlers_for_same_type():
    bus = EventBus()
    a, b = [], []
    bus.subscribe("step_start", lambda e: a.append(e))
    bus.subscribe("step_start", lambda e: b.append(e))

    bus.emit(StepStartEvent(step=1, session_id="s1"))

    assert len(a) == 1
    assert len(b) == 1


def test_subscribe_all_catches_every_event():
    bus = EventBus()
    received = []
    bus.subscribe("*", lambda e: received.append(e))

    bus.emit(StepStartEvent(step=1, session_id="s1"))
    bus.emit(StepEndEvent(step=1, session_id="s1", duration_seconds=1.0, is_done=False, consecutive_failures=0))

    assert len(received) == 2


def test_close_calls_cleanup_handlers():
    bus = EventBus()
    cleaned = []
    bus.on_close(lambda: cleaned.append(True))
    bus.close()
    assert len(cleaned) == 1
