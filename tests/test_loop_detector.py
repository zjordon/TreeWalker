"""Tests for ActionLoopDetector — action normalization, page stagnation, dual-dim nudges."""

from tree_walker.agent.loop_detector import (
    ActionLoopDetector,
    FailureStreakTracker,
    PageFingerprint,
    compute_action_hash,
)


class TestComputeActionHash:
    """P1-1: per-action-type semantic normalization."""

    # --- search: order / case / punctuation agnostic ---
    def test_search_word_order_agnostic(self):
        assert compute_action_hash("search", {"query": "hello world"}) == compute_action_hash(
            "search", {"query": "world hello"}
        )

    def test_search_case_and_punctuation_agnostic(self):
        assert compute_action_hash("search", {"query": "Hello, World!"}) == compute_action_hash(
            "search", {"query": "world hello"}
        )

    def test_search_dedupes_repeated_tokens(self):
        assert compute_action_hash("search", {"query": "a a b"}) == compute_action_hash("search", {"query": "a b"})

    def test_search_different_query_different_hash(self):
        assert compute_action_hash("search", {"query": "a"}) != compute_action_hash("search", {"query": "b"})

    def test_search_default_engine_is_baidu(self):
        # TreeWalker default engine is 'baidu' (not browser-use's 'google')
        assert compute_action_hash("search", {"query": "x"}) == compute_action_hash(
            "search", {"query": "x", "engine": "baidu"}
        )

    # --- click: by element identity ---
    def test_click_same_index_same_hash(self):
        assert compute_action_hash("click", {"index": 1}) == compute_action_hash("click", {"index": 1})

    def test_click_different_index_different_hash(self):
        assert compute_action_hash("click", {"index": 1}) != compute_action_hash("click", {"index": 2})

    def test_click_index_takes_precedence_over_element_id(self):
        h_idx = compute_action_hash("click", {"index": 1})
        h_both = compute_action_hash("click", {"index": 1, "element_id": 99})
        assert h_idx == h_both

    def test_click_falls_back_to_element_id(self):
        # no index → element_id used as identity (same numeric value → same hash)
        assert compute_action_hash("click", {"element_id": 7}) == compute_action_hash("click", {"index": 7})

    # --- input_text: keep text (REGRESSION: old code stripped text → collision) ---
    def test_input_text_different_text_different_hash(self):
        assert compute_action_hash("input_text", {"index": 1, "text": "hello"}) != compute_action_hash(
            "input_text", {"index": 1, "text": "world"}
        )

    def test_input_text_whitespace_case_normalized(self):
        assert compute_action_hash("input_text", {"index": 1, "text": "  Hello  "}) == compute_action_hash(
            "input_text", {"index": 1, "text": "hello"}
        )

    def test_input_text_different_index_different_hash(self):
        assert compute_action_hash("input_text", {"index": 1, "text": "x"}) != compute_action_hash(
            "input_text", {"index": 2, "text": "x"}
        )

    # --- navigate: url only ---
    def test_navigate_ignores_new_tab(self):
        assert compute_action_hash("navigate", {"url": "http://x.com", "new_tab": False}) == compute_action_hash(
            "navigate", {"url": "http://x.com", "new_tab": True}
        )

    def test_navigate_different_url_different_hash(self):
        assert compute_action_hash("navigate", {"url": "http://x.com"}) != compute_action_hash(
            "navigate", {"url": "http://y.com"}
        )

    # --- scroll: direction only (REGRESSION: old code included amount → over-split) ---
    def test_scroll_direction_only_ignores_amount(self):
        assert compute_action_hash("scroll", {"direction": "down", "amount": 3}) == compute_action_hash(
            "scroll", {"direction": "down", "amount": 5}
        )

    def test_scroll_different_direction_different_hash(self):
        assert compute_action_hash("scroll", {"direction": "down"}) != compute_action_hash(
            "scroll", {"direction": "up"}
        )

    # --- default branch ---
    def test_default_branch_excludes_none_params(self):
        assert compute_action_hash("extract", {"query": "x", "extract_links": None}) == compute_action_hash(
            "extract", {"query": "x"}
        )

    def test_default_branch_param_order_agnostic(self):
        assert compute_action_hash("extract", {"query": "x", "extract_links": True}) == compute_action_hash(
            "extract", {"extract_links": True, "query": "x"}
        )

    def test_hash_is_12_chars(self):
        assert len(compute_action_hash("click", {"index": 1})) == 12


class TestPageFingerprint:
    """P1-2: 3-dim page fingerprint equality."""

    def test_equal_when_all_three_dims_same(self):
        assert PageFingerprint.from_state("http://x", "dom", 5) == PageFingerprint.from_state("http://x", "dom", 5)

    def test_diff_when_dom_text_changes(self):
        assert PageFingerprint.from_state("http://x", "dom1", 5) != PageFingerprint.from_state("http://x", "dom2", 5)

    def test_diff_when_url_changes(self):
        assert PageFingerprint.from_state("http://x", "dom", 5) != PageFingerprint.from_state("http://y", "dom", 5)

    def test_diff_when_element_count_changes(self):
        assert PageFingerprint.from_state("http://x", "dom", 5) != PageFingerprint.from_state("http://x", "dom", 6)

    def test_empty_dom_text_does_not_crash(self):
        fp = PageFingerprint.from_state("http://x", "", 0)
        assert fp.text_hash and len(fp.text_hash) == 16


class TestRecordPageState:
    """P1-2: stagnation counter."""

    def test_first_page_no_stagnation(self):
        d = ActionLoopDetector()
        d.record_page_state("http://x", "dom", 5)
        assert d.consecutive_stagnant_pages == 0

    def test_same_page_increments_stagnation(self):
        d = ActionLoopDetector()
        d.record_page_state("http://x", "dom", 5)
        d.record_page_state("http://x", "dom", 5)
        assert d.consecutive_stagnant_pages == 1
        d.record_page_state("http://x", "dom", 5)
        assert d.consecutive_stagnant_pages == 2

    def test_dom_change_resets_stagnation(self):
        d = ActionLoopDetector()
        d.record_page_state("http://x", "dom1", 5)
        d.record_page_state("http://x", "dom1", 5)
        assert d.consecutive_stagnant_pages == 1
        d.record_page_state("http://x", "dom2", 5)
        assert d.consecutive_stagnant_pages == 0

    def test_url_change_resets_stagnation(self):
        d = ActionLoopDetector()
        d.record_page_state("http://x", "dom", 5)
        d.record_page_state("http://x", "dom", 5)
        d.record_page_state("http://y", "dom", 5)
        assert d.consecutive_stagnant_pages == 0

    def test_element_count_change_resets_stagnation(self):
        d = ActionLoopDetector()
        d.record_page_state("http://x", "dom", 5)
        d.record_page_state("http://x", "dom", 5)
        d.record_page_state("http://x", "dom", 6)
        assert d.consecutive_stagnant_pages == 0

    def test_fingerprint_queue_capped_at_5(self):
        d = ActionLoopDetector()
        for i in range(10):
            d.record_page_state(f"http://{i}", f"dom{i}", i)
        assert len(d.recent_page_fingerprints) == 5


class TestLoopDetectorNudge:
    """P1-3: dual-dim nudge thresholds + actual-count text."""

    def _repeat(self, detector: ActionLoopDetector, name: str, params: dict, count: int) -> None:
        for _ in range(count):
            detector.record_action(name, params)

    # --- action repetition thresholds (5 / 8 / 12) ---
    def test_no_nudge_below_5(self):
        d = ActionLoopDetector()
        self._repeat(d, "click", {"index": 1}, 4)
        assert d.get_nudge_message() is None

    def test_nudge_at_5(self):
        d = ActionLoopDetector()
        self._repeat(d, "click", {"index": 1}, 5)
        msg = d.get_nudge_message()
        assert msg is not None
        assert "Heads up" in msg
        assert "similar action" in msg
        assert "5 times" in msg
        assert "in the last 5 actions" in msg

    def test_nudge_at_8(self):
        d = ActionLoopDetector()
        self._repeat(d, "click", {"index": 1}, 8)
        msg = d.get_nudge_message()
        assert msg is not None
        assert "8 times" in msg
        assert "Are you still making progress" in msg

    def test_nudge_at_12_top_tier(self):
        d = ActionLoopDetector()
        self._repeat(d, "click", {"index": 1}, 12)
        msg = d.get_nudge_message()
        assert msg is not None
        assert "12 times" in msg
        assert "a different approach might get you there faster" in msg

    def test_high_repetition_uses_actual_count(self):
        d = ActionLoopDetector()
        self._repeat(d, "click", {"index": 1}, 15)
        msg = d.get_nudge_message()
        assert "15 times" in msg  # actual count, not a "12+" bucket

    def test_no_nudge_with_fewer_than_3_actions(self):
        d = ActionLoopDetector()
        d.record_action("click", {"index": 1})
        d.record_action("click", {"index": 1})
        assert d.get_nudge_message() is None

    # --- page stagnation dimension (>=5) ---
    def test_stagnation_nudge_at_5(self):
        d = ActionLoopDetector()
        for _ in range(6):  # 1 baseline + 5 same → stagnation 5
            d.record_page_state("http://x", "dom", 5)
        assert d.consecutive_stagnant_pages == 5
        msg = d.get_nudge_message()
        assert msg is not None
        assert "page content has not changed" in msg
        assert "5 consecutive actions" in msg

    def test_no_stagnation_nudge_below_5(self):
        d = ActionLoopDetector()
        for _ in range(5):  # 1 baseline + 4 same → stagnation 4
            d.record_page_state("http://x", "dom", 5)
        assert d.consecutive_stagnant_pages == 4
        assert d.get_nudge_message() is None

    # --- dual-dim interaction ---
    def test_both_dimensions_nudge_joined(self):
        d = ActionLoopDetector()
        self._repeat(d, "click", {"index": 1}, 5)  # action dim
        for _ in range(6):  # stagnation 5
            d.record_page_state("http://x", "dom", 5)
        msg = d.get_nudge_message()
        assert msg is not None
        assert "similar action" in msg
        assert "page content has not changed" in msg
        assert "\n\n" in msg  # two messages joined

    def test_stagnation_only_when_actions_below_min3_guard(self):
        # <3 actions but stagnation >=5 → stagnation nudge fires, action nudge does not
        d = ActionLoopDetector()
        d.record_action("click", {"index": 1})
        d.record_action("click", {"index": 2})
        for _ in range(6):
            d.record_page_state("http://x", "dom", 5)
        msg = d.get_nudge_message()
        assert msg is not None
        assert "page content has not changed" in msg
        assert "similar action" not in msg

    def test_search_variations_detected_as_repetition(self):
        # word-order/case/punctuation variants normalize to one hash → counted as repetition
        d = ActionLoopDetector()
        d.record_action("search", {"query": "hello world"})
        d.record_action("search", {"query": "World Hello"})
        d.record_action("search", {"query": "hello, world"})
        d.record_action("search", {"query": "world hello"})
        d.record_action("search", {"query": "HELLO WORLD"})
        msg = d.get_nudge_message()
        assert msg is not None
        assert "5 times" in msg


class TestLoopDetectorWindow:
    """P1-4: window size + sliding."""

    def test_default_window_is_20(self):
        d = ActionLoopDetector()
        assert d.recent_actions.maxlen == 20

    def test_custom_window(self):
        d = ActionLoopDetector(window_size=10)
        assert d.recent_actions.maxlen == 10

    def test_old_action_slides_out(self):
        d = ActionLoopDetector(window_size=5)
        for _ in range(4):
            d.record_action("click", {"index": 1})  # A
        for _ in range(5):
            d.record_action("click", {"index": 2})  # B
        # window=5 keeps only the 5 B's; A has slid out
        assert d.max_repetition_count == 5
        assert d.get_nudge_message() is not None

    def test_repetition_clears_when_actions_slide_out(self):
        d = ActionLoopDetector(window_size=5)
        for _ in range(5):
            d.record_action("click", {"index": 1})
        assert d.get_nudge_message() is not None
        # 5 distinct actions push A out → no longer repeating
        for i in range(5):
            d.record_action("click", {"index": 100 + i})
        assert d.max_repetition_count == 1
        assert d.get_nudge_message() is None


# ── issue #186 现象①：FailureStreakTracker（失败感知连败止损） ────────────────


class TestFailureStreakTracker:
    """跨步 per-action 连败计数：多动作步失败也计、成功清零、done 豁免、nudge 去抖分档。"""

    def test_below_threshold_no_nudge(self):
        t = FailureStreakTracker()
        t.record("screenshot", failed=True)
        assert t.nudge() is None  # streak=1 < 2

    def test_two_consecutive_failures_nudge_level1(self):
        t = FailureStreakTracker()
        t.record("screenshot", failed=True)
        t.record("screenshot", failed=True)
        msg = t.nudge()
        assert msg is not None
        assert "failed 'screenshot' 2 times" in msg
        assert "Re-read the original task" in msg  # 止损+回归目标文案

    def test_third_failure_same_tier_no_renotify(self):
        t = FailureStreakTracker()
        for _ in range(3):
            t.record("screenshot", failed=True)
        assert t.nudge() is not None  # streak=2 首报
        assert t.nudge() is None  # streak=3 同档不重报

    def test_fourth_failure_escalates_once(self):
        t = FailureStreakTracker()
        for _ in range(4):
            t.record("evaluate", failed=True)
        msg = t.nudge()
        assert msg is not None
        assert "4 times" in msg
        assert "honest partial result" in msg  # 升级措辞：诚实部分结果出路
        assert t.nudge() is None  # 5+ 不重报
        for _ in range(3):
            t.record("evaluate", failed=True)
            assert t.nudge() is None  # streak 冻结在高档位不再注入

    def test_success_clears_streak_and_notification_state(self):
        t = FailureStreakTracker()
        t.record("screenshot", failed=True)
        t.record("screenshot", failed=True)
        assert t.nudge() is not None
        t.record("screenshot", failed=False)  # 成功清零（含通知状态）
        assert t.nudge() is None
        t.record("screenshot", failed=True)
        t.record("screenshot", failed=True)  # 重新连败 2 → 再报
        assert t.nudge() is not None

    def test_done_exempt(self):
        t = FailureStreakTracker()
        for _ in range(5):
            t.record("done", failed=True)
        assert t.nudge() is None

    def test_per_action_name_independent(self):
        t = FailureStreakTracker()
        t.record("screenshot", failed=True)
        t.record("evaluate", failed=True)  # 不同动作互不清零
        t.record("screenshot", failed=True)  # screenshot streak=2
        msg = t.nudge()
        assert msg is not None and "'screenshot'" in msg
        t.record("screenshot", failed=False)  # 清 screenshot
        t.record("evaluate", failed=True)  # evaluate streak=2
        msg = t.nudge()
        assert msg is not None and "'evaluate'" in msg

    def test_task374_shape_multi_action_step_failures_counted(self):
        """task_374 形态：[close_tab OK, screenshot 失败] 步 + 单独 screenshot 失败步
        ——consecutive_failures 对这种不计且被重置；这里必须第 2 次失败即触发。"""
        t = FailureStreakTracker()
        t.record("close_tab", failed=False)  # 同步成功动作
        t.record("screenshot", failed=True)  # 同步失败动作（step 7）
        assert t.nudge() is None
        t.record("screenshot", failed=True)  # 下一单独失败步（step 8）
        assert t.nudge() is not None

    def test_new_failing_tool_not_starved_by_suppressed_streak(self):
        """review 修正：抑制档动作不得饿死新达阈值动作的首报——screenshot 冻结
        在 3 档（已报过）期间，evaluate workaround 连败 2 次必须拿到自己的提示。"""
        t = FailureStreakTracker()
        for _ in range(3):
            t.record("screenshot", failed=True)
        assert t.nudge() is not None  # screenshot 首报（streak=2 档）
        assert t.nudge() is None  # streak=3 抑制档
        t.record("evaluate", failed=True)
        t.record("evaluate", failed=True)  # evaluate 新达 2
        msg = t.nudge()
        assert msg is not None
        assert "'evaluate' 2 times" in msg

    def test_higher_tier_takes_priority_over_new_tier_two(self):
        """同时可报时高 streak 优先（4 档升级 > 新动作 2 档首报）。"""
        t = FailureStreakTracker()
        t.record("screenshot", failed=True)
        t.record("screenshot", failed=True)
        t.nudge()  # screenshot 首报，notified=2
        t.record("screenshot", failed=True)
        t.record("screenshot", failed=True)  # streak=4 → 升级档
        t.record("evaluate", failed=True)
        t.record("evaluate", failed=True)  # evaluate 2 档
        msg = t.nudge()
        assert msg is not None
        assert "'screenshot' 4 times" in msg
        msg2 = t.nudge()  # screenshot 已升级报过 → evaluate 的首报
        assert msg2 is not None and "'evaluate' 2 times" in msg2

    def test_peek_does_not_consume_until_acked(self):
        """review2 #5：查询/提交解耦——peek 只读；LLM 调用失败（未 ack）时下步
        peek 返回同一候选，首报不丢；ack 后才抑制。"""
        t = FailureStreakTracker()
        t.record("screenshot", failed=True)
        t.record("screenshot", failed=True)
        c1 = t.peek_nudge()
        assert c1 is not None and c1[0] == "screenshot" and c1[1] == 2
        c2 = t.peek_nudge()  # 未 ack → 同一候选可重发
        assert c2 == c1
        t.ack_nudge(c1[0], c1[1])
        assert t.peek_nudge() is None  # 提交后抑制
