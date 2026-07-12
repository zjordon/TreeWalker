"""event_mapper 单元测试：事件 → action 映射 + 输入合并去噪。"""

from tree_walker.agent.views import AgentHistory, StepMetadata
from tree_walker.recorder.event_mapper import coalesce_inputs, denoise_steps, map_event, needs_target


def _step(name, params, step_number=0, t=0.0):
	"""构造一条 AgentHistory（denoise_steps 测试用）。"""
	return AgentHistory(
		step_number=step_number,
		model_output={"actions": [{"name": name, "params": params}]},
		result=[],
		state_summary={"url": "https://x.com"},
		metadata=StepMetadata(step_start_time=t, step_end_time=t, step_number=step_number),
	)


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


# ── denoise_steps：落盘前去噪（合并 input / 折叠 click / 合并 scroll / 重排序号）──


def test_denoise_merges_consecutive_input_text_same_index():
	steps = [
		_step("input_text", {"index": 5, "text": "a", "clear": True}, 0, 1000.0),
		_step("input_text", {"index": 5, "text": "abc", "clear": True}, 1, 1001.0),
	]
	out = denoise_steps(steps)
	assert len(out) == 1
	assert out[0].model_output["actions"][0]["params"]["text"] == "abc"  # 取最终值
	assert out[0].step_number == 0  # 重排


def test_denoise_keeps_input_text_different_index():
	steps = [
		_step("input_text", {"index": 5, "text": "a", "clear": True}, 0),
		_step("input_text", {"index": 6, "text": "b", "clear": True}, 1),
	]
	out = denoise_steps(steps)
	assert len(out) == 2


def test_denoise_collapses_rapid_clicks_same_index():
	# 0.2s 内同 index 两次 click → 合一条
	steps = [
		_step("click", {"index": 3}, 0, 1000.0),
		_step("click", {"index": 3}, 1, 1000.2),
	]
	out = denoise_steps(steps)
	assert len(out) == 1


def test_denoise_keeps_clicks_apart_in_time():
	# 超过 gap（默认 0.5s）→ 不合并
	steps = [
		_step("click", {"index": 3}, 0, 1000.0),
		_step("click", {"index": 3}, 1, 1001.0),
	]
	out = denoise_steps(steps)
	assert len(out) == 2


def test_denoise_merges_same_direction_scroll():
	steps = [
		_step("scroll", {"amount": 2, "direction": "down"}, 0),
		_step("scroll", {"amount": 3, "direction": "down"}, 1),
	]
	out = denoise_steps(steps)
	assert len(out) == 1
	assert out[0].model_output["actions"][0]["params"]["amount"] == 5


def test_denoise_scroll_amount_clamps_at_10():
	steps = [
		_step("scroll", {"amount": 7, "direction": "down"}, 0),
		_step("scroll", {"amount": 8, "direction": "down"}, 1),
	]
	out = denoise_steps(steps)
	assert len(out) == 1
	assert out[0].model_output["actions"][0]["params"]["amount"] == 10


def test_denoise_keeps_opposite_direction_scroll():
	steps = [
		_step("scroll", {"amount": 2, "direction": "down"}, 0),
		_step("scroll", {"amount": 2, "direction": "up"}, 1),
	]
	out = denoise_steps(steps)
	assert len(out) == 2


def test_denoise_does_not_merge_across_other_action():
	# click 隔开两个同 index input_text → 都保留
	steps = [
		_step("input_text", {"index": 5, "text": "a", "clear": True}, 0),
		_step("click", {"index": 9}, 1),
		_step("input_text", {"index": 5, "text": "b", "clear": True}, 2),
	]
	out = denoise_steps(steps)
	assert len(out) == 3


def test_denoise_renumbers_step_numbers_after_merge():
	steps = [
		_step("input_text", {"index": 5, "text": "a", "clear": True}, 0, 1000.0),
		_step("input_text", {"index": 5, "text": "b", "clear": True}, 1, 1000.1),
		_step("click", {"index": 3}, 2),
		_step("click", {"index": 3}, 3, 1000.0),  # 与上 click 时间差大（>gap），不合并
	]
	out = denoise_steps(steps)
	# input 合并为 1 + 两个不相邻 click = 3 条
	assert [s.step_number for s in out] == [0, 1, 2]
	assert all(s.metadata.step_number == s.step_number for s in out)
