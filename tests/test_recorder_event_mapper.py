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


# ── dedupe_uploads：合并 upload_file 操作（吸收冗余 click/fakepath + 补 index）──


def _step_elem(name, params, elem, step_number=0, t=0.0):
	"""带 interacted_element 的 AgentHistory（upload dedupe 测试用）。"""
	s = _step(name, params, step_number, t)
	s.interacted_element = [elem]
	return s


def _file_input_elem(backend_id=1293):
	return {
		"backend_node_id": backend_id,
		"node_name": "INPUT",
		"attributes": {"type": "file", "accept": "video/*"},
	}


def _button_elem(backend_id=1283):
	return {"backend_node_id": backend_id, "node_name": "BUTTON", "attributes": {}}


def test_dedupe_upload_absorbs_clicks_fakepath_and_borrows_index():
	"""仿 recorded.json：上传按钮click + file input click + fakepath + upload → 单步 upload。

	upload 借 file input 的 index；更早的无关 click（clicks 达 max=2 后）不被误吸收。
	"""
	steps = [
		_step("navigate", {"url": "https://x.com", "new_tab": False}, 0, 0.0),
		_step("click", {}, 1, 1.0),  # 更早无效 click（interacted null），不应被吸收
		_step_elem("click", {"index": 1283}, _button_elem(), 2, 2.0),
		_step_elem("click", {"index": 1293}, _file_input_elem(), 3, 3.0),
		_step("input_text", {"text": "C:\\fakepath\\v.mp4", "clear": True}, 4, 4.0),
		_step("upload_file", {"path": "uploads/v.mp4"}, 5, 5.0),  # 无 index
	]
	out = denoise_steps(steps)
	# navigate + 更早无效 click 保留；中间 click×2 + fakepath 吸收；upload 借 index
	assert [s.model_output["actions"][0]["name"] for s in out] == ["navigate", "click", "upload_file"]
	upload = out[-1]
	assert upload.model_output["actions"][0]["params"]["index"] == 1293
	assert upload.model_output["actions"][0]["params"]["path"] == "uploads/v.mp4"
	assert upload.interacted_element == [_file_input_elem()]


def test_dedupe_upload_keeps_non_fakepath_input_text():
	"""upload 前的普通 input_text（非 fakepath、basename 不匹配）不吸收。"""
	steps = [
		_step("input_text", {"text": "正常输入", "clear": True}, 0, 0.0),
		_step("upload_file", {"path": "uploads/v.mp4"}, 1, 1.0),
	]
	out = denoise_steps(steps)
	assert len(out) == 2


def test_dedupe_upload_keeps_existing_index():
	"""upload 自带 index 时不向被吸收的 file input click 借。"""
	steps = [
		_step_elem("click", {"index": 1293}, _file_input_elem(), 0, 0.0),
		_step("upload_file", {"path": "uploads/v.mp4", "index": 5000}, 1, 1.0),
	]
	out = denoise_steps(steps)
	assert len(out) == 1
	assert out[0].model_output["actions"][0]["params"]["index"] == 5000


def test_dedupe_upload_time_window_breaks_absorption():
	"""超 gap_s（默认 10s）的前置 click 不吸收；upload 无候选 → 不借 index。"""
	steps = [
		_step_elem("click", {"index": 1293}, _file_input_elem(), 0, 0.0),
		_step("upload_file", {"path": "uploads/v.mp4"}, 1, 20.0),
	]
	out = denoise_steps(steps)
	assert len(out) == 2
	assert out[1].model_output["actions"][0]["params"].get("index") is None


def test_dedupe_two_uploads_merge_independently():
	"""两个 upload 各自吸收前置 file input click、各自借 index（多 file input 不串扰）。"""
	steps = [
		_step_elem("click", {"index": 100}, _file_input_elem(100), 0, 0.0),
		_step("upload_file", {"path": "uploads/a.mp4"}, 1, 1.0),
		_step_elem("click", {"index": 200}, _file_input_elem(200), 2, 2.0),
		_step("upload_file", {"path": "uploads/b.mp4"}, 3, 3.0),
	]
	out = denoise_steps(steps)
	assert len(out) == 2
	assert out[0].model_output["actions"][0]["params"]["index"] == 100
	assert out[1].model_output["actions"][0]["params"]["index"] == 200


# ── dedupe_auto_navigates：丢弃自动跳转的 navigate（上一步副作用的 SPA 跳转）──


def test_denoise_drops_navigate_after_upload_within_gap():
	"""上传后自动跳转的 navigate（紧邻 upload_file ≤gap）丢弃。"""
	steps = [
		_step("navigate", {"url": "https://x.com/upload", "new_tab": False}, 0, 0.0),
		_step("upload_file", {"path": "uploads/v.mp4"}, 1, 1.0),
		_step("navigate", {"url": "https://x.com/post", "new_tab": False}, 2, 1.9),  # 0.9s 后
	]
	out = denoise_steps(steps)
	assert [s.model_output["actions"][0]["name"] for s in out] == ["navigate", "upload_file"]


def test_denoise_drops_navigate_after_click_within_gap():
	"""提交 click 后自动跳转的 navigate（0.04s）丢弃。"""
	steps = [
		_step("click", {"index": 5}, 0, 1000.0),
		_step("navigate", {"url": "https://x.com/after", "new_tab": False}, 1, 1000.04),
	]
	out = denoise_steps(steps)
	assert [s.model_output["actions"][0]["name"] for s in out] == ["click"]


def test_denoise_keeps_navigate_after_long_gap():
	"""前一步间隔超 gap（默认 3s）→ 视为地址栏式主动导航，保留。"""
	steps = [
		_step("click", {"index": 5}, 0, 0.0),
		_step("navigate", {"url": "https://x.com/manual", "new_tab": False}, 1, 5.0),
	]
	out = denoise_steps(steps)
	assert len(out) == 2


def test_denoise_keeps_consecutive_navigates():
	"""连续 navigate 不连锁丢弃（前一步也是 navigate → 保留当前）。"""
	steps = [
		_step("navigate", {"url": "https://x.com/a", "new_tab": False}, 0, 0.0),
		_step("navigate", {"url": "https://x.com/b", "new_tab": False}, 1, 1.0),
	]
	out = denoise_steps(steps)
	assert len(out) == 2


def test_denoise_keeps_new_tab_navigate():
	"""new_tab=True 是主动开新 tab，始终保留（即使紧邻 click）。"""
	steps = [
		_step("click", {"index": 5}, 0, 0.0),
		_step("navigate", {"url": "https://x.com/tab", "new_tab": True}, 1, 0.5),
	]
	out = denoise_steps(steps)
	assert len(out) == 2
	assert out[1].model_output["actions"][0]["params"]["new_tab"] is True


def test_denoise_keeps_first_step_navigate():
	"""首步 navigate（前面无保留步骤）保留。"""
	steps = [
		_step("navigate", {"url": "https://x.com/start", "new_tab": False}, 0, 0.0),
	]
	out = denoise_steps(steps)
	assert len(out) == 1
