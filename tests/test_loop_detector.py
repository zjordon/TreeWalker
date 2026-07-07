"""Tests for ActionLoopDetector — action normalization, page stagnation, dual-dim nudges."""

from tree_walker.agent.loop_detector import ActionLoopDetector, PageFingerprint, compute_action_hash


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
