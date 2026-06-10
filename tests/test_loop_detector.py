"""Tests for ActionLoopDetector — 12/8/5 threshold nudges."""

from tree_walker.agent.loop_detector import ActionLoopDetector


class TestLoopDetectorNudge:
    def _repeat(self, detector: ActionLoopDetector, name: str, params: dict, count: int) -> None:
        for _ in range(count):
            detector.record_action(name, params)

    def test_no_nudge_below_5(self):
        d = ActionLoopDetector()
        self._repeat(d, "click", {"index": 1}, 4)
        assert d.get_nudge_message() is None

    def test_nudge_at_5(self):
        d = ActionLoopDetector()
        self._repeat(d, "click", {"index": 1}, 5)
        msg = d.get_nudge_message()
        assert msg is not None
        assert "5+" in msg

    def test_nudge_at_8(self):
        d = ActionLoopDetector()
        self._repeat(d, "click", {"index": 1}, 8)
        msg = d.get_nudge_message()
        assert msg is not None
        assert "8+" in msg

    def test_nudge_at_12_is_critical(self):
        d = ActionLoopDetector()
        self._repeat(d, "click", {"index": 1}, 12)
        msg = d.get_nudge_message()
        assert msg is not None
        assert "CRITICAL" in msg
        assert "12+" in msg

    def test_12_takes_priority_over_8(self):
        d = ActionLoopDetector()
        self._repeat(d, "click", {"index": 1}, 15)
        msg = d.get_nudge_message()
        assert "CRITICAL" in msg
        assert "12+" in msg

    def test_no_nudge_with_fewer_than_3_actions(self):
        d = ActionLoopDetector()
        d.record_action("click", {"index": 1})
        d.record_action("click", {"index": 1})
        assert d.get_nudge_message() is None
