"""event_mapper 单元测试：事件 → action 纯映射（Stage 1）。

去噪测试已迁移到 ``test_recorder_rules.py``（signal 感知规则）+ ``test_recorder_translation.py``
（连续 input 聚合）。本文件只测 ``map_event`` 的机械映射与 ``needs_target``。
"""

from tree_walker.recorder.event_mapper import map_event, needs_target


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
		"navigate", {"url": "https://x.com", "new_tab": False},
	)


def test_map_navigate_new_tab():
	assert map_event({"type": "navigate", "params": {"url": "https://x.com", "new_tab": True}}) == (
		"navigate", {"url": "https://x.com", "new_tab": True},
	)


def test_map_scroll_clamps_amount():
	# 越界 amount 被 clamp 进 [1, 10]（对齐 ScrollParams ge/le）
	_, p = map_event({"type": "scroll", "params": {"amount": 99, "direction": "down"}})
	assert p["amount"] == 10
	_, p = map_event({"type": "scroll", "params": {"amount": 0, "direction": "up"}})
	assert p["amount"] == 1
	assert p["direction"] == "up"


def test_map_scroll_normalizes_direction():
	_, p = map_event({"type": "scroll", "params": {"amount": 3, "direction": "sideways"}})
	assert p["direction"] == "down"  # 非法方向回退 down


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
	# accept 透传（扩展 change 瞬间捕获），缺省空串
	assert map_event({"type": "upload_file", "params": {"path": "/tmp/f.png"}}) == (
		"upload_file", {"path": "/tmp/f.png", "accept": ""},
	)
	assert map_event({"type": "upload_file",
	                  "params": {"path": "/tmp/v.mp4", "accept": "video/*"}}) == (
		"upload_file", {"path": "/tmp/v.mp4", "accept": "video/*"},
	)


def test_needs_target_upload_file():
	assert needs_target("upload_file") is True
