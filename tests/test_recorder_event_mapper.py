"""event_mapper 单元测试：事件 → action 映射 + 输入合并去噪。"""

from tree_walker.recorder.event_mapper import coalesce_inputs, map_event, needs_target


def test_map_click():
	assert map_event({"type": "click"}) == ("click", {})


def test_map_input_text_defaults_clear_true():
	name, params = map_event({"type": "input_text", "params": {"text": "abc"}})
	assert name == "input_text"
	assert params == {"text": "abc", "clear": True}


def test_map_input_text_clear_false():
	name, params = map_event({"type": "input_text", "params": {"text": "x", "clear": False}})
	assert params == {"text": "x", "clear": False}


def test_map_select_dropdown():
	assert map_event({"type": "select_dropdown", "params": {"value": "opt1"}}) == (
		"select_dropdown", {"value": "opt1"},
	)


def test_map_navigate():
	assert map_event({"type": "navigate", "params": {"url": "https://x.com"}}) == (
		"navigate", {"url": "https://x.com"},
	)


def test_map_go_back():
	assert map_event({"type": "go_back"}) == ("go_back", {})


def test_map_scroll_defaults():
	name, params = map_event({"type": "scroll"})
	assert name == "scroll"
	assert params["amount"] == 3
	assert params["direction"] == "down"


def test_map_unknown_event_returns_none():
	assert map_event({"type": "hover"}) is None
	assert map_event({}) is None


def test_needs_target():
	assert needs_target("click") is True
	assert needs_target("input_text") is True
	assert needs_target("select_dropdown") is True
	assert needs_target("upload_file") is True
	assert needs_target("navigate") is False
	assert needs_target("scroll") is False
	assert needs_target("go_back") is False


def test_coalesce_inputs_merges_consecutive_same_xpath():
	events = [
		{"type": "input_text", "xpath": "html/input[1]", "params": {"text": "a"}, "ts": 1000},
		{"type": "input_text", "xpath": "html/input[1]", "params": {"text": "ab"}, "ts": 1100},
		{"type": "input_text", "xpath": "html/input[1]", "params": {"text": "abc"}, "ts": 1200},
	]
	out = coalesce_inputs(events)
	assert len(out) == 1
	assert out[0]["params"]["text"] == "abc"  # 取最后值


def test_coalesce_inputs_keeps_different_xpath_separate():
	events = [
		{"type": "input_text", "xpath": "html/input[1]", "params": {"text": "a"}, "ts": 1000},
		{"type": "input_text", "xpath": "html/input[2]", "params": {"text": "b"}, "ts": 1100},
	]
	out = coalesce_inputs(events)
	assert len(out) == 2


def test_coalesce_inputs_does_not_merge_across_other_events():
	events = [
		{"type": "input_text", "xpath": "html/input[1]", "params": {"text": "a"}, "ts": 1000},
		{"type": "click", "xpath": "html/button", "ts": 1100},
		{"type": "input_text", "xpath": "html/input[1]", "params": {"text": "b"}, "ts": 1200},
	]
	out = coalesce_inputs(events)
	assert len(out) == 3  # click 隔开，两个 input 不合并


def test_coalesce_inputs_gap_breaks_merge():
	events = [
		{"type": "input_text", "xpath": "html/input[1]", "params": {"text": "a"}, "ts": 1000},
		{"type": "input_text", "xpath": "html/input[1]", "params": {"text": "b"}, "ts": 5000},
	]
	out = coalesce_inputs(events, gap_ms=1500)
	assert len(out) == 2  # 间隔 4s > 1.5s，不合并


def test_map_switch_tab():
	assert map_event({"type": "switch_tab", "params": {"tab_id": "A1B2"}}) == (
		"switch_tab", {"tab_id": "A1B2"},
	)


def test_map_close_tab():
	assert map_event({"type": "close_tab", "params": {"tab_id": ""}}) == (
		"close_tab", {"tab_id": ""},
	)


def test_map_send_keys():
	assert map_event({"type": "send_keys", "params": {"keys": "Ctrl+C"}}) == (
		"send_keys", {"keys": "Ctrl+C"},
	)


def test_map_upload_file():
	assert map_event({"type": "upload_file", "params": {"path": "/tmp/f.png"}}) == (
		"upload_file", {"path": "/tmp/f.png"},
	)


def test_needs_target_upload_file():
	assert needs_target("upload_file") is True
