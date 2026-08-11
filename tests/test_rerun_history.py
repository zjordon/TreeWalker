"""历史重放（rerun_history）单元测试。

覆盖：录制投影、五级匹配、序列化往返（含脱敏/老格式）、变量检测与精确整串替换、
步间延迟、跳过/重试辅助、三层兜底 AI 摘要、端到端 rerun_history（mock browser/tools）。
设计见 docs/rerun_history/08-测试与落地清单.md。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tree_walker.agent.rerun import MatchLevel, RerunMixin, _substitute_in_dict
from tree_walker.agent.step import StepPipeline
from tree_walker.agent.variable_detector import detect_variables_in_history, merge_variable_sources
from tree_walker.agent.views import (
    AgentHistory,
    AgentHistoryList,
    ActionResult,
    DetectedVariable,
    ManualVariableBinding,
    StepMetadata,
    redact_sensitive_string,
)
from tree_walker.browser.views import DOMInteractedElement


def _state(selector_map: dict) -> SimpleNamespace:
    """轻量 BrowserStateSummary 替身（duck-typed：仅用 .dom_state.selector_map）。"""
    return SimpleNamespace(dom_state=SimpleNamespace(selector_map=selector_map), url="http://x")


# ── 录制投影 ────────────────────────────────────────────────────────────


def test_project_interacted_element(make_node):
    sp = StepPipeline()
    node = make_node(tag="input", node_id=5, backend_node_id=5,
                     attributes={"type": "email", "name": "email"})
    model_output = {"action": {"name": "input_text", "params": {"index": 5, "text": "a@b.com"}}}
    projected = sp._project_interacted_elements(model_output, _state({5: node}))
    assert projected is not None
    assert projected[0]["node_name"] == "INPUT"
    assert projected[0]["attributes"]["name"] == "email"
    assert projected[0]["stable_hash"] is not None


def test_project_no_index_action_is_none(make_node):
    sp = StepPipeline()
    node = make_node(tag="button", node_id=1, backend_node_id=1)
    model_output = {"action": {"name": "done", "params": {}}}
    assert sp._project_interacted_elements(model_output, _state({1: node})) == [None]


def test_project_multi_action_positional(make_node):
    sp = StepPipeline()
    a = make_node(tag="input", node_id=2, backend_node_id=2, attributes={"name": "x"})
    b = make_node(tag="button", node_id=3, backend_node_id=3)
    mo = {"actions": [
        {"name": "input_text", "params": {"index": 2, "text": "hi"}},
        {"name": "done", "params": {}},
    ]}
    projected = sp._project_interacted_elements(mo, _state({2: a, 3: b}))
    assert projected[0]["node_name"] == "INPUT"
    assert projected[1] is None


def test_project_none_when_no_dom_state():
    sp = StepPipeline()
    assert sp._project_interacted_elements({"action": {}}, None) is None
    assert sp._project_interacted_elements(
        {"action": {}}, SimpleNamespace(dom_state=None)
    ) is None


def test_build_step_metadata_sets_previous_interval():
    from tree_walker.agent.views import AgentState
    sp = StepPipeline()
    sp.state = AgentState()
    sp.history = AgentHistoryList(history=[
        AgentHistory(step_number=0, model_output={"action": {}}, result=[],
                     metadata=StepMetadata(step_start_time=10.0, step_end_time=13.0, step_number=0))])
    sp._step_start_time = 14.0
    md = sp._build_step_metadata(15.0)
    assert md.step_interval == 3.0      # 上一步耗时
    assert md.step_start_time == 14.0
    assert md.step_number == 0          # n_steps 尚未自增


def test_build_step_metadata_first_step_none():
    from tree_walker.agent.views import AgentState
    sp = StepPipeline()
    sp.state = AgentState()
    sp.history = AgentHistoryList()
    sp._step_start_time = 1.0
    assert sp._build_step_metadata(2.0).step_interval is None


# ── 五级元素匹配 ────────────────────────────────────────────────────────


def test_match_exact(make_node):
    rm = RerunMixin()
    live = make_node(tag="input", attributes={"name": "email"})
    hist = DOMInteractedElement.load_from_enhanced_dom_tree(live).to_dict()
    assert rm._match_element_index(hist, {7: live}) == (7, MatchLevel.EXACT)


def test_match_stable(make_node):
    rm = RerunMixin()
    live = make_node(tag="input", attributes={"name": "email"})
    hist = DOMInteractedElement.load_from_enhanced_dom_tree(live).to_dict()
    hist["element_hash"] = 999999   # 破坏 EXACT
    assert rm._match_element_index(hist, {4: live}) == (4, MatchLevel.STABLE)


def test_match_xpath(make_node):
    rm = RerunMixin()
    live = make_node(tag="input", attributes={"name": "email"})
    hist = DOMInteractedElement.load_from_enhanced_dom_tree(live).to_dict()
    hist["element_hash"] = 999999
    hist["stable_hash"] = 999999    # 破坏 STABLE，x_path 保留
    assert rm._match_element_index(hist, {4: live}) == (4, MatchLevel.XPATH)


def test_match_ax_name(make_node, make_ax_node):
    rm = RerunMixin()
    live = make_node(tag="button", ax_node=make_ax_node(name="Submit"))
    hist = {"node_name": "BUTTON", "ax_name": "Submit"}   # 无 attributes → ATTRIBUTE 跳过
    assert rm._match_element_index(hist, {3: live}) == (3, MatchLevel.AX_NAME)


# ── Level 0 TEXT 匹配（issue #136：cover step tab 仅靠可见文字区分）────────


def test_match_text_overrides_colliding_exact(make_node):
    """守住 bug 修复：录制设置竖封面（点后 step-active），element_hash 撞到重放时激活的
    设置横封面——TEXT 级（优先于 EXACT）按文字命中设置竖封面，避免点错。"""
    from tree_walker.browser.views import NodeType
    rm = RerunMixin()
    h_tab = make_node(
        tag="div", attributes={"class": "step-dXVbPX step-active-AWDV7U"},
        children=[make_node(tag="#text", node_type=NodeType.TEXT_NODE, node_value="设置横封面")],
    )
    v_tab = make_node(
        tag="div", attributes={"class": "step-dXVbPX"},
        children=[make_node(tag="#text", node_type=NodeType.TEXT_NODE, node_value="设置竖封面")],
    )
    sm = {1: h_tab, 2: v_tab}
    # hist：录制设置竖封面，但 element_hash 故意取 h_tab（重放时 step-active 在横封面那边，
    # EXACT 本会命中 idx=1 设置横封面——错的）
    hist = {
        "node_name": "DIV",
        "text": "设置竖封面",
        "element_hash": h_tab.element_hash,
        "x_path": "",
        "bounds": None,
    }
    idx, level = rm._match_element_index(hist, sm)
    assert idx == 2                       # 命中设置竖封面，而非 h_tab
    assert level == MatchLevel.TEXT


def test_match_text_skipped_when_absent(make_node):
    """无 text（旧录制/agent 自录）→ 跳过 TEXT，走原指纹路径（向后兼容）。"""
    rm = RerunMixin()
    live = make_node(tag="input", attributes={"name": "email"})
    hist = DOMInteractedElement.load_from_enhanced_dom_tree(live).to_dict()
    assert "text" not in hist
    assert rm._match_element_index(hist, {7: live}) == (7, MatchLevel.EXACT)


def test_match_text_handles_per_char_spans(make_node):
    """抖音封面 tab 把每个字拆成独立 span——live ``get_all_children_text`` 带 \\n/空格，
    须 strip 全部空白后才能与录制的 textContent（无分隔符）匹配（issue #136 真实场景）。"""
    from tree_walker.browser.views import NodeType
    rm = RerunMixin()
    v_tab = make_node(
        tag="div", attributes={"class": "step-dXVbPX"},
        children=[make_node(tag="span", children=[
            make_node(tag="#text", node_type=NodeType.TEXT_NODE, node_value=ch)
        ]) for ch in "设置竖封面"],
    )
    sm = {1: v_tab}
    hist = {"node_name": "DIV", "text": "设置竖封面", "element_hash": 0, "x_path": "", "bounds": None}
    idx, level = rm._match_element_index(hist, sm)
    assert idx == 1
    assert level == MatchLevel.TEXT


def test_match_attribute_fallback(make_node):
    rm = RerunMixin()
    live = make_node(tag="input", attributes={"name": "email", "id": "x"})
    hist = {"node_name": "INPUT", "attributes": {"name": "email"}}   # 无 hash/xpath/ax_name
    assert rm._match_element_index(hist, {9: live}) == (9, MatchLevel.ATTRIBUTE)


def test_match_none(make_node):
    rm = RerunMixin()
    live = make_node(tag="input", attributes={"name": "other"})
    assert rm._match_element_index(
        {"node_name": "INPUT", "attributes": {"name": "email"}}, {1: live}
    ) is None


def test_match_class_fallback(make_node):
    # SPA 按钮只有 CSS 类、无 name/id/aria-label → 前 5 级全失败，CLASS 兜底（抖音「确定」场景）
    rm = RerunMixin()
    live = make_node(tag="button", attributes={"class": "semi-button semi-button-primary btn-xtdEbg"})
    hist = {"node_name": "BUTTON", "attributes": {"class": "semi-button semi-button-primary btn-xtdEbg"}}
    assert rm._match_element_index(hist, {9: live}) == (9, MatchLevel.CLASS)


def test_match_class_superset_tolerates_extra_state_class(make_node):
    # 候选 class 含录制全部 token（外加 focused-xxx 状态类）→ 仍匹配
    rm = RerunMixin()
    live = make_node(tag="button", attributes={"class": "semi-button semi-button-primary btn-xtdEbg focused-abc"})
    hist = {"node_name": "BUTTON", "attributes": {"class": "semi-button semi-button-primary btn-xtdEbg"}}
    assert rm._match_element_index(hist, {5: live}) == (5, MatchLevel.CLASS)


def test_match_class_rejects_missing_token(make_node):
    # 候选缺一个 token（primary 换成 tertiary）→ 不匹配（不会误选「取消」按钮当「确定」）
    rm = RerunMixin()
    live = make_node(tag="button", attributes={"class": "semi-button semi-button-tertiary btn-xtdEbg"})
    hist = {"node_name": "BUTTON", "attributes": {"class": "semi-button semi-button-primary btn-xtdEbg"}}
    assert rm._match_element_index(hist, {5: live}) is None


def test_match_class_skipped_when_name_id_present(make_node):
    # 有 name 时 ATTRIBUTE 命中，不走 CLASS（CLASS 只是无 name/id/aria-label 的兜底）
    rm = RerunMixin()
    live = make_node(tag="input", attributes={"name": "email", "class": "inp"})
    hist = {"node_name": "INPUT", "attributes": {"name": "email", "class": "inp"}}
    assert rm._match_element_index(hist, {3: live}) == (3, MatchLevel.ATTRIBUTE)


def test_match_tiebreak_by_position(make_node, make_snapshot_node, make_dom_rect):
    # 哈希碰撞：两个同 attrs、同 parent(无) 的 div → element_hash 相同。
    # 同级多个候选时，按「录制 bounds 中心就近」选——不能取迭代顺序里靠前的那个。
    rm = RerunMixin()
    near = make_node(tag="div", attributes={"class": "x"},
                     snapshot_node=make_snapshot_node(bounds=make_dom_rect(x=700, y=660, w=200, h=30)))
    far = make_node(tag="div", attributes={"class": "x"},
                    snapshot_node=make_snapshot_node(bounds=make_dom_rect(x=400, y=500, w=200, h=30)))
    assert near.element_hash == far.element_hash   # 确认确实碰撞
    hist = DOMInteractedElement.load_from_enhanced_dom_tree(near).to_dict()
    hist["bounds"] = {"x": 614.7, "y": 657.8, "width": 390.4, "height": 30.4}  # center≈(810,673)
    # far 放在 selector_map 前面，确保不是「取第一个」
    match = rm._match_element_index(hist, {1: far, 2: near})
    assert match == (2, MatchLevel.EXACT)          # 选离录制位置最近的 near(idx 2)


def test_match_tiebreak_falls_back_to_first_without_bounds(make_node):
    # 录制元素无 bounds 时，无法按位置 tie-break → 退回第一个（保持旧行为，不报错）
    rm = RerunMixin()
    a = make_node(tag="div", attributes={"class": "x"})
    b = make_node(tag="div", attributes={"class": "x"})
    hist = {"node_name": "DIV", "attributes": {"class": "x"},
            "element_hash": a.element_hash, "bounds": None}
    match = rm._match_element_index(hist, {1: a, 2: b})
    assert match == (1, MatchLevel.EXACT)          # 退回第一个


def test_match_tiebreak_prefers_unique_xpath_match():
    # 指纹碰撞（同 element_hash）+ xpath 不同 → 优先「恰好一个 xpath 命中」的候选
    # 抖音封面编辑器横/竖版上传区场景：同 hash，xpath div[2] vs div[3]
    from tree_walker.agent.rerun import _nearest_idx
    horiz = SimpleNamespace(xpath="html/body/div[2]/icon", snapshot_node=None)
    vert = SimpleNamespace(xpath="html/body/div[3]/icon", snapshot_node=None)
    hist = {"x_path": "html/body/div[3]/icon", "bounds": None}  # 录制的是竖版
    assert _nearest_idx(hist, [(1, horiz), (2, vert)]) == 2


def test_match_tiebreak_xpath_all_match_falls_back_to_position(make_node):
    # 所有候选 xpath 都相同（真·无区分碰撞）→ xpath tie-break 不触发，退回 bounds 就近
    rm = RerunMixin()
    # 两个同 attrs 的 div（element_hash 碰撞、xpath 同为 "div"）
    a = make_node(tag="div", attributes={"class": "x"})
    b = make_node(tag="div", attributes={"class": "x"})
    assert a.element_hash == b.element_hash
    hist = {"node_name": "DIV", "attributes": {"class": "x"}, "element_hash": a.element_hash,
            "x_path": "div", "bounds": None}  # xpath "div" 对 a/b 都匹配 → 不是唯一 → 退回第一个
    assert rm._match_element_index(hist, {1: a, 2: b}) == (1, MatchLevel.EXACT)


def test_update_action_indices_relocates_and_preserves_params(make_node):
    rm = RerunMixin()
    live = make_node(tag="input", attributes={"name": "email"})
    hist = DOMInteractedElement.load_from_enhanced_dom_tree(live).to_dict()
    action = {"name": "input_text", "params": {"index": 2, "text": "a@b.com"}}
    updated = rm._update_action_indices(hist, action, {7: live})
    assert updated["params"]["index"] == 7
    assert updated["params"]["text"] == "a@b.com"      # 其他参数保留
    assert "element_id" not in updated["params"]
    assert action["params"]["index"] == 2              # 原始未污染（深拷贝）


def test_update_action_indices_failure_returns_none(make_node):
    rm = RerunMixin()
    live = make_node(tag="input", attributes={"name": "other"})
    hist = {"node_name": "INPUT", "attributes": {"name": "email"}}
    assert rm._update_action_indices(
        hist, {"name": "click", "params": {"index": 2}}, {1: live}
    ) is None


def test_format_match_failure_mentions_levels(make_node):
    rm = RerunMixin()
    msg = rm._format_match_failure(
        {"node_name": "BUTTON", "attributes": {"name": "submit"},
         "element_hash": 123, "x_path": "html/body/button"}, 0, {1: make_node()}
    )
    assert "Could not find matching element" in msg
    assert "EXACT -> STABLE -> XPATH -> AX_NAME -> ATTRIBUTE" in msg


# ── 序列化往返 ──────────────────────────────────────────────────────────


def _sample_history() -> AgentHistoryList:
    return AgentHistoryList(history=[
        AgentHistory(
            step_number=0,
            model_output={"action": {"name": "input_text", "params": {"index": 2, "text": "a@b.com"}}},
            result=[ActionResult(extracted_content="ok")],
            state_summary={"url": "http://x"},
            interacted_element=[{"node_name": "INPUT", "attributes": {"name": "email"},
                                 "x_path": "html/body/input", "element_hash": 1, "stable_hash": 2}],
            metadata=StepMetadata(step_start_time=1.0, step_end_time=2.0, step_number=0, step_interval=None),
        )
    ], action_registry_version="v1-test")


def test_save_load_roundtrip(tmp_path):
    path = tmp_path / "h.json"
    _sample_history().save_to_file(path, action_registry_version="v1-test")
    loaded = AgentHistoryList.load_from_file(path)
    item = loaded.history[0]
    assert item.step_number == 0
    assert item.interacted_element[0]["attributes"]["name"] == "email"
    assert item.metadata.step_end_time == 2.0
    assert loaded.action_registry_version == "v1-test"


def test_save_redacts_only_input_action_params(tmp_path):
    path = tmp_path / "h.json"
    history = AgentHistoryList(history=[
        AgentHistory(
            step_number=0,
            model_output={"action": {"name": "input_text",
                                     "params": {"index": 1, "text": "my-secret-password"}}},
            result=[ActionResult(extracted_content="my-secret-password")],
            metadata=StepMetadata(step_start_time=1.0, step_end_time=2.0, step_number=0),
        )])
    history.save_to_file(path, sensitive_data={"password": "my-secret-password"})
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["history"][0]["model_output"]["action"]["params"]["text"] == "<secret>password</secret>"
    # result 不脱敏
    assert raw["history"][0]["result"][0]["extracted_content"] == "my-secret-password"


def test_load_legacy_without_interacted_element(tmp_path):
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({
        "history": [{"step_number": 0,
                     "model_output": {"action": {"name": "done", "params": {}}},
                     "result": []}]
    }), encoding="utf-8")
    loaded = AgentHistoryList.load_from_file(path)
    assert loaded.history[0].interacted_element is None


def test_redact_sensitive_string_longest_first():
    out = redact_sensitive_string(
        "pass=password123 extra=password",
        {"password": "password", "pw123": "password123"},
    )
    assert "<secret>pw123</secret>" in out     # 长的先匹配
    assert "password123" not in out


# ── 变量检测与替换 ──────────────────────────────────────────────────────


def test_detect_email_by_element_type():
    history = AgentHistoryList(history=[
        AgentHistory(step_number=0,
                     model_output={"action": {"name": "input_text", "params": {"text": "a@b.com"}}},
                     result=[],
                     interacted_element=[{"node_name": "INPUT", "attributes": {"type": "email"}}])])
    dv = detect_variables_in_history(history)
    assert dv["email"].original_value == "a@b.com"
    assert dv["email"].format == "email"


def test_detect_email_by_value_pattern():
    history = AgentHistoryList(history=[
        AgentHistory(step_number=0,
                     model_output={"action": {"name": "input_text", "params": {"text": "a@b.com"}}},
                     result=[])])
    assert "email" in detect_variables_in_history(history)


def test_detect_ignores_non_text_fields():
    history = AgentHistoryList(history=[
        AgentHistory(step_number=0,
                     model_output={"action": {"name": "click", "params": {"index": 5}}},
                     result=[])])
    assert detect_variables_in_history(history) == {}


def test_substitute_in_dict_exact_only():
    data = {"text": "John", "nested": {"q": "John"}, "list": ["John", "John Doe"]}
    n = _substitute_in_dict(data, {"John": "Jane"})
    assert data["text"] == "Jane"
    assert data["nested"]["q"] == "Jane"
    assert data["list"][0] == "Jane"
    assert data["list"][1] == "John Doe"     # 子串不动
    assert n == 3


def test_substitute_variables_replaces_and_does_not_mutate_original():
    rm = RerunMixin()
    history = AgentHistoryList(history=[
        AgentHistory(step_number=0,
                     model_output={"action": {"name": "input_text", "params": {"index": 1, "text": "john@x.com"}}},
                     result=[],
                     interacted_element=[{"node_name": "INPUT", "attributes": {"type": "email"}}])])
    new = rm._substitute_variables_in_history(history, {"email": "jane@x.com"})
    assert new.history[0].model_output["action"]["params"]["text"] == "jane@x.com"
    assert history.history[0].model_output["action"]["params"]["text"] == "john@x.com"   # 原始未污染
    assert new.history[0].interacted_element[0]["attributes"]["type"] == "email"         # element 不动


def test_substitute_unknown_variable_skipped():
    rm = RerunMixin()
    history = AgentHistoryList(history=[
        AgentHistory(step_number=0,
                     model_output={"action": {"name": "input_text", "params": {"text": "john@x.com"}}},
                     result=[])])
    new = rm._substitute_variables_in_history(history, {"nonexistent": "x"})
    assert new.history[0].model_output["action"]["params"]["text"] == "john@x.com"


def test_detect_does_not_autodetect_select_dropdown():
    """select_dropdown.value 不在 _FIELDS(text/query) → 不自动检测（P5 agent 路径走手工标注）。
    守住「select 不进自动检测」决策：若误把 value 加进 _FIELDS，此断言失败即提醒。"""
    history = AgentHistoryList(history=[
        AgentHistory(
            step_number=0,
            model_output={"action": {"name": "select_dropdown", "params": {"index": 4, "value": "Technical Support"}}},
            result=[],
            interacted_element=[{"node_name": "SELECT", "attributes": {"id": "department"}}],
        )
    ])
    assert detect_variables_in_history(history) == {}


def test_manual_select_dropdown_variable_substitutes():
    """P5 agent 路径核心：手工标注 select_dropdown.value → 并入合并变量集 → 精确整串替换 params.value。
    agent 的 value 本就是可见文本（如 'Technical Support'，非 value 属性码），无需 label；
    重放时 set_select_option 按 text 匹配（live 验证见 examples/p5_select_e2e_live.py）。"""
    rm = RerunMixin()
    history = AgentHistoryList(history=[
        AgentHistory(
            step_number=0,
            model_output={"action": {"name": "select_dropdown", "params": {"index": 4, "value": "Technical Support"}}},
            result=[],
            interacted_element=[{"node_name": "SELECT", "attributes": {"id": "department"}}],
        )
    ])
    history.manual_variables = [ManualVariableBinding(
        name="department", step_number=0, action_index=0, field="value",
        original_value="Technical Support")]
    # 手工变量并入合并集（CSV 列头 = detect ∪ manual）；select 不被自动检测 → department 仅来自 manual
    merged = merge_variable_sources(detect_variables_in_history(history), history.manual_variables)
    assert "department" in merged
    assert merged["department"].original_value == "Technical Support"
    # 替换：Technical Support → Sales，精确命中 params.value；index 不动；原始 history 未污染
    new = rm._substitute_variables_in_history(history, {"department": "Sales"})
    assert new.history[0].model_output["action"]["params"]["value"] == "Sales"
    assert new.history[0].model_output["action"]["params"]["index"] == 4
    assert history.history[0].model_output["action"]["params"]["value"] == "Technical Support"


def test_manual_variables_same_original_different_location():
    """两个手工变量 original_value 相同但位置不同（录制时标题与简介同文本），按 (step/action/field)
    位置替换，各自得各自的值——不撞 key（value-based 会把两处都换成同一个）。"""
    rm = RerunMixin()
    history = AgentHistoryList(history=[
        AgentHistory(step_number=7,
                     model_output={"actions": [{"name": "input_text",
                                                "params": {"index": 1, "text": "X"}}]},
                     result=[]),
        AgentHistory(step_number=9,
                     model_output={"actions": [
                         {"name": "select_dropdown", "params": {"index": 2, "value": "Y"}},
                         {"name": "input_text", "params": {"index": 3, "text": "浏览器Agent"}},
                         {"name": "send_keys", "params": {"keys": "Enter"}},
                         {"name": "input_text", "params": {"index": 4, "text": "X"}},
                     ]},
                     result=[]),
    ])
    history.manual_variables = [
        ManualVariableBinding(name="title-1", step_number=7, action_index=0, field="text", original_value="X"),
        ManualVariableBinding(name="title-2", step_number=9, action_index=3, field="text", original_value="X"),
    ]
    new = rm._substitute_variables_in_history(history, {"title-1": "标题A", "title-2": "简介B"})
    # title-1 → step7/action0/text；title-2 → step9/action3/text，各自独立
    assert new.history[0].model_output["actions"][0]["params"]["text"] == "标题A"
    assert new.history[1].model_output["actions"][3]["params"]["text"] == "简介B"
    # 其他 action 不动
    assert new.history[1].model_output["actions"][1]["params"]["text"] == "浏览器Agent"
    # 原始 history 未污染
    assert history.history[0].model_output["actions"][0]["params"]["text"] == "X"


def test_manual_variable_location_miss_skips_no_fallback():
    """位置未命中（step/action/field 对不上）→ 警告跳过，不回退到 value-based（避免重撞 key）。"""
    rm = RerunMixin()
    history = AgentHistoryList(history=[
        AgentHistory(step_number=7,
                     model_output={"actions": [{"name": "input_text",
                                                "params": {"index": 1, "text": "X"}}]},
                     result=[])])
    history.manual_variables = [ManualVariableBinding(
        name="v", step_number=99, action_index=0, field="text", original_value="X")]
    new = rm._substitute_variables_in_history(history, {"v": "Y"})
    # step 99 不存在 → 未命中 → 不替换（不回退值替换）
    assert new.history[0].model_output["actions"][0]["params"]["text"] == "X"


# ── 步间延迟 / 跳过重试辅助 ─────────────────────────────────────────────


def _item(step_interval=None, step_number=1, user_pause_seconds=None):
    return AgentHistory(
        step_number=step_number, model_output={"action": {}}, result=[],
        metadata=StepMetadata(step_start_time=0.0, step_end_time=1.0, step_number=step_number,
                              step_interval=step_interval, user_pause_seconds=user_pause_seconds),
    )


def test_compute_step_delay_caps_interval():
    rm = RerunMixin()
    assert rm._compute_step_delay(_item(step_interval=30.0), 2.0, 5.0) == 5.0    # 封顶
    assert rm._compute_step_delay(_item(step_interval=3.0), 2.0, 5.0) == 3.0     # 未达上限
    assert rm._compute_step_delay(_item(step_interval=None), 2.0, 5.0) == 2.0    # 兜底


def test_compute_step_delay_user_pause_wins():
    """阶段4 / 缺口7：user_pause_seconds（recorder 路径）优先于 step_interval，且不封顶。"""
    rm = RerunMixin()
    # 同时存在 → user_pause_seconds 胜出，不封顶（30 > max 5）
    assert rm._compute_step_delay(
        _item(user_pause_seconds=30.0, step_interval=3.0), 2.0, 5.0) == 30.0
    # 仅 user_pause_seconds
    assert rm._compute_step_delay(_item(user_pause_seconds=7.0), 2.0, 5.0) == 7.0
    # user_pause_seconds=None → 回落 step_interval 封顶（agent 自录路径，与上测试一致）
    assert rm._compute_step_delay(
        _item(user_pause_seconds=None, step_interval=30.0), 2.0, 5.0) == 5.0


def test_count_expected_elements():
    rm = RerunMixin()
    item = AgentHistory(step_number=0, model_output={"actions": [
        {"name": "click", "params": {"index": 5}},
        {"name": "click", "params": {"index": 2}},
    ]}, result=[])
    assert rm._count_expected_elements(item) == 6     # max index 5 + 1
    assert rm._count_expected_elements(
        AgentHistory(step_number=0, model_output={"action": {"name": "done", "params": {}}}, result=[])
    ) == 0


def test_is_redundant_retry():
    rm = RerunMixin()
    elem = {"element_hash": 1, "stable_hash": 2, "x_path": "x"}
    curr = AgentHistory(step_number=1,
                        model_output={"action": {"name": "click", "params": {"index": 1}}},
                        result=[], interacted_element=[elem])
    prev = AgentHistory(step_number=0,
                        model_output={"action": {"name": "click", "params": {"index": 1}}},
                        result=[], interacted_element=[elem])
    assert rm._is_redundant_retry_step(curr, prev, True) is True
    assert rm._is_redundant_retry_step(curr, prev, False) is False     # 上步未成功
    diff = AgentHistory(step_number=1,
                        model_output={"action": {"name": "click", "params": {"index": 1}}},
                        result=[], interacted_element=[{"element_hash": 99}])
    assert rm._is_redundant_retry_step(diff, prev, True) is False      # 不同元素


def test_is_menu_opener():
    rm = RerunMixin()
    opener = AgentHistory(step_number=0, model_output={"action": {}}, result=[],
                          interacted_element=[{"attributes": {"aria-haspopup": "menu"}}])
    assert rm._is_menu_opener_step(opener) is True
    plain = AgentHistory(step_number=0, model_output={"action": {}}, result=[],
                         interacted_element=[{"attributes": {"class": "btn"}}])
    assert rm._is_menu_opener_step(plain) is False
    assert rm._is_menu_opener_step(None) is False


def test_build_rerun_evidence():
    out = RerunMixin._build_rerun_evidence(
        [ActionResult(extracted_content="hello"), ActionResult(error="boom")]
    )
    assert "Step 0: OK" in out and "hello" in out
    assert "Step 1: ERROR" in out


# ── AI 摘要（三层兜底）────────────────────────────────────────────────


class _StructuredLLM:
    async def extract(self, *, prompt, content, output_schema=None, **kw):
        assert output_schema is not None
        return '{"summary":"all good","success":true,"completion_status":"complete"}'


@pytest.mark.asyncio
async def test_summary_layer1_structured():
    rm = RerunMixin()
    results = [ActionResult(), ActionResult()]
    summary = await rm._generate_rerun_summary("do x", results, summary_llm=_StructuredLLM())
    assert summary.is_done is True
    assert summary.success is True
    assert summary.extracted_content == "all good"


@pytest.mark.asyncio
async def test_summary_layer3_pure_count_when_llm_fails():
    rm = RerunMixin()
    rm.llm = None     # extract 必抛错 → 一路降到 Layer 3
    results = [ActionResult(), ActionResult(error="boom")]
    summary = await rm._generate_rerun_summary("do x", results, summary_llm=None)
    assert summary.is_done is True
    assert summary.success is False                  # 有 error
    assert "1/2" in summary.extracted_content


# ── 端到端 rerun_history（mock browser/tools）─────────────────────────


@pytest.mark.asyncio
async def test_rerun_history_relocates_and_runs(make_node):
    from tree_walker.agent.agent import Agent
    from tree_walker.config import AgentSettings, JudgeSettings

    rec_node = make_node(tag="input", node_id=2, backend_node_id=2,
                         attributes={"name": "email", "type": "email"})
    hist_elem = DOMInteractedElement.load_from_enhanced_dom_tree(rec_node).to_dict()
    history = AgentHistoryList(history=[
        AgentHistory(step_number=0,
                     model_output={"action": {"name": "input_text", "params": {"index": 2, "text": "a@b.com"}}},
                     result=[ActionResult()],
                     state_summary={"url": "http://example.com"},
                     interacted_element=[hist_elem]),
    ])

    # 重放时同元素但 index=7
    live_node = make_node(tag="input", node_id=7, backend_node_id=7,
                          attributes={"name": "email", "type": "email"})
    state = SimpleNamespace(url="http://example.com",
                            dom_state=SimpleNamespace(selector_map={7: live_node}))

    browser = MagicMock()
    browser._settings = SimpleNamespace(wait_between_actions=0)
    browser.start = AsyncMock()
    browser.stop = AsyncMock()
    browser.navigate = AsyncMock()
    browser.get_state = AsyncMock(return_value=state)
    browser.get_current_url = AsyncMock(return_value="http://example.com")

    agent = Agent(task="do x", llm=MagicMock(), browser=browser,
                  settings=AgentSettings(judge=JudgeSettings(enabled=False)))

    captured: dict = {}

    async def fake_execute(name, params, br, st):
        captured["name"] = name
        captured["params"] = dict(params)
        return ActionResult()

    agent.tools.execute = fake_execute
    agent.llm = _StructuredLLM()

    results = await agent.rerun_history(
        history, max_step_interval=0, delay_between_actions=0, summary_llm=_StructuredLLM()
    )

    assert captured["params"]["index"] == 7          # 重定位成功
    assert results[-1].is_done
    assert results[-1].success is True


# ── upload_file 回放兜底（录制无定位 → 从 selector_map 找 file input）──


def test_skip_reason_keeps_upload_file_without_index():
    """upload_file 无 index/interacted 不跳过——回放时可从 selector_map 兜底找 file input。"""
    rm = RerunMixin()
    item = AgentHistory(
        step_number=0,
        model_output={"action": {"name": "upload_file", "params": {"path": "v.mp4"}}},
        result=[ActionResult()],
        state_summary={"url": "http://x"},
        interacted_element=[None],
    )
    assert rm._skip_reason(item, None, False, False) is None


def test_skip_reason_skips_click_without_index():
    """click 无 index/interacted 仍跳过（噪声步，回放无法兜底）。"""
    rm = RerunMixin()
    item = AgentHistory(
        step_number=0,
        model_output={"action": {"name": "click", "params": {}}},
        result=[ActionResult()],
        state_summary={"url": "http://x"},
        interacted_element=[None],
    )
    assert rm._skip_reason(item, None, False, False) is not None


@pytest.mark.asyncio
async def test_rerun_upload_file_with_correct_fingerprint_matches(make_node):
    """录制正确的 upload_file（accept 定位出的 file input 真实指纹）→ 重放正常 EXACT 匹配
    命中，无需任何兜底。file input 本就在 selector_map。"""
    from tree_walker.agent.agent import Agent
    from tree_walker.config import AgentSettings, JudgeSettings

    file_input = make_node(tag="input", node_id=4490, backend_node_id=4490,
                           attributes={"type": "file", "accept": "video/*"})
    # 录制指纹 = 该 file input 的真实投影（accept 定位产出）
    hist_elem = DOMInteractedElement.load_from_enhanced_dom_tree(file_input).to_dict()
    history = AgentHistoryList(history=[
        AgentHistory(step_number=0,
                     model_output={"action": {"name": "upload_file",
                                              "params": {"path": "v.mp4", "index": 4490}}},
                     result=[ActionResult()],
                     state_summary={"url": "http://x"},
                     interacted_element=[hist_elem]),
    ])
    state = SimpleNamespace(
        url="http://x",
        dom_state=SimpleNamespace(selector_map={4490: file_input}),
    )

    browser = MagicMock()
    browser._settings = SimpleNamespace(wait_between_actions=0)
    browser.start = AsyncMock()
    browser.stop = AsyncMock()
    browser.navigate = AsyncMock()
    browser.get_state = AsyncMock(return_value=state)
    browser.get_current_url = AsyncMock(return_value="http://x")

    agent = Agent(task="upload", llm=MagicMock(), browser=browser,
                  settings=AgentSettings(judge=JudgeSettings(enabled=False)))
    captured: dict = {}

    async def fake_execute(name, params, br, st):
        captured["name"] = name
        captured["params"] = dict(params)
        return ActionResult()

    agent.tools.execute = fake_execute
    agent.llm = _StructuredLLM()

    await agent.rerun_history(
        history, max_step_interval=0, delay_between_actions=0, summary_llm=_StructuredLLM()
    )

    assert captured["name"] == "upload_file"
    assert captured["params"]["index"] == 4490   # EXACT 匹配命中（无兜底）
    assert captured["params"]["path"] == "v.mp4"


@pytest.mark.asyncio
async def test_rerun_upload_file_no_fingerprint_resolves_by_accept(make_node):
    """upload_file 无指纹（file input 录制时瞬态不在 selector_map）→ 重放按 accept(文件类型)
    从当前页 selector_map 解析 file input。对应「视频上传时 video input 未渲染」场景。"""
    from tree_walker.agent.agent import Agent
    from tree_walker.config import AgentSettings, JudgeSettings

    history = AgentHistoryList(history=[
        AgentHistory(step_number=0,
                     model_output={"action": {"name": "upload_file", "params": {"path": "v.mp4"}}},
                     result=[ActionResult()],
                     state_summary={"url": "http://x"},
                     interacted_element=[None]),  # 录制无指纹（file input 瞬态）
    ])
    video = make_node(tag="input", node_id=4490, backend_node_id=4490,
                      attributes={"type": "file", "accept": "video/x-flv,video/mp4,video/*"})
    state = SimpleNamespace(
        url="http://x",
        dom_state=SimpleNamespace(selector_map={4490: video}),
    )

    browser = MagicMock()
    browser._settings = SimpleNamespace(wait_between_actions=0)
    browser.start = AsyncMock()
    browser.stop = AsyncMock()
    browser.navigate = AsyncMock()
    browser.get_state = AsyncMock(return_value=state)
    browser.get_current_url = AsyncMock(return_value="http://x")

    agent = Agent(task="upload", llm=MagicMock(), browser=browser,
                  settings=AgentSettings(judge=JudgeSettings(enabled=False)))
    captured: dict = {}

    async def fake_execute(name, params, br, st):
        captured["name"] = name
        captured["params"] = dict(params)
        return ActionResult()

    agent.tools.execute = fake_execute
    agent.llm = _StructuredLLM()

    await agent.rerun_history(
        history, max_step_interval=0, delay_between_actions=0, summary_llm=_StructuredLLM()
    )

    assert captured["name"] == "upload_file"
    assert captured["params"]["index"] == 4490   # 按 accept=video 解析命中
    assert captured["params"]["path"] == "v.mp4"


# ── _resolve_file_input_by_accept：accept + xpath 签名解析（B 方案）──────


def test_resolve_file_input_by_accept_xpath_disambiguates_covers():
    """同 accept（横/竖封面都 image）多个 → xpath_hint normalize 后唯一命中区分。"""
    rm = RerunMixin()
    heng = SimpleNamespace(node_name="INPUT",
                           attributes={"type": "file", "accept": "image/png,image/jpeg"},
                           xpath="html/body/div[12]/div[2]/input")
    shu = SimpleNamespace(node_name="INPUT",
                          attributes={"type": "file", "accept": "image/png,image/jpeg"},
                          xpath="html/body/div[12]/div[3]/input")
    state = _state({10: heng, 11: shu})
    # 竖封面 xpath（前导 / 经 normalize_xpath strip）→ idx 11
    assert rm._resolve_file_input_by_accept(
        state, "shu.png", "/html/body/div[12]/div[3]/input", "image/png,image/jpeg") == 11
    # 横封面 xpath → idx 10
    assert rm._resolve_file_input_by_accept(
        state, "heng.png", "html/body/div[12]/div[2]/input", "image/png,image/jpeg") == 10


def test_resolve_file_input_by_accept_accept_hint_overrides_path():
    """accept_hint 优先定 kind：path 是 .mp4 但 accept_hint=image → 取 image input；
    无 accept_hint 时退回 path 扩展名（.mp4→video，无 video input → None）。"""
    rm = RerunMixin()
    img = SimpleNamespace(node_name="INPUT",
                          attributes={"type": "file", "accept": "image/png,image/jpeg"}, xpath="x")
    state = _state({7: img})
    assert rm._resolve_file_input_by_accept(state, "weird.mp4", "", "image/png,image/jpeg") == 7
    assert rm._resolve_file_input_by_accept(state, "weird.mp4", "", "") is None


def test_resolve_file_input_by_accept_single_video_input():
    """唯一 video 候选 → 无需 xpath_hint（视频上传只有 1 个 video input）。"""
    rm = RerunMixin()
    vid = SimpleNamespace(node_name="INPUT",
                          attributes={"type": "file", "accept": "video/*"}, xpath="x")
    state = _state({9: vid})
    assert rm._resolve_file_input_by_accept(state, "v.mp4", "", "") == 9


# ── _match_file_upload_by_clue：issue #139 upload 语义线索精筛（站点无关，通用化）──


@pytest.mark.asyncio
async def test_match_file_upload_by_clue_region_text_disambiguates():
    """issue #139 通用化：多个同 accept 的 file input → region_text（就近可见文本祖先，泛化旧
    area_text）精筛命中正确的封面 input。替代 candidates[0]（DOM 顺序第一个）。"""
    rm = RerunMixin()
    cover = SimpleNamespace(node_name="INPUT",
                            attributes={"type": "file", "accept": "image/png,image/jpeg"},
                            xpath="x", is_visible=True, snapshot_node=None)
    ai_cover = SimpleNamespace(node_name="INPUT",
                               attributes={"type": "file", "accept": "image/png,image/jpeg"},
                               xpath="y", is_visible=True, snapshot_node=None)
    main_btn = SimpleNamespace(node_name="INPUT",
                               attributes={"type": "file", "accept": "image/png,image/jpeg"},
                               xpath="z", is_visible=True, snapshot_node=None)
    sm = {10: cover, 11: ai_cover, 12: main_btn}
    # 只有封面区就近祖先文本是"点击上传文件或拖拽文件到这里"
    rm._upload_input_contexts = AsyncMock(return_value={
        10: {"region_text": "点击上传文件或拖拽文件到这里"},
        11: {"region_text": ""},
        12: {"region_text": "点击上传新的视频封面"},
    })
    clue = {"accept": "image/png,image/jpeg",
            "region_text": "点击上传文件或拖拽文件到这里", "rect": None}
    assert await rm._match_file_upload_by_clue(clue, sm) == 10


@pytest.mark.asyncio
async def test_match_file_upload_by_clue_label_text_disambiguates():
    """通用化：原生 <label for> 的 label_text（W3C 标准信号）精筛——覆盖非抖音站点。"""
    rm = RerunMixin()
    cover = SimpleNamespace(node_name="INPUT", attributes={"type": "file", "accept": "image/png"},
                            xpath="a", is_visible=True, snapshot_node=None)
    avatar = SimpleNamespace(node_name="INPUT", attributes={"type": "file", "accept": "image/png"},
                             xpath="b", is_visible=True, snapshot_node=None)
    sm = {10: cover, 11: avatar}
    rm._upload_input_contexts = AsyncMock(return_value={
        10: {"label_text": "封面图", "region_text": ""},
        11: {"label_text": "头像", "region_text": ""},
    })
    clue = {"accept": "image/png", "label_text": "头像", "rect": None}
    assert await rm._match_file_upload_by_clue(clue, sm) == 11


@pytest.mark.asyncio
async def test_match_file_upload_by_clue_aria_text_disambiguates():
    """通用化：aria-labelledby 解析出的 aria_text 精筛。"""
    rm = RerunMixin()
    a = SimpleNamespace(node_name="INPUT", attributes={"type": "file", "accept": "image/png"},
                        xpath="a", is_visible=True, snapshot_node=None)
    b = SimpleNamespace(node_name="INPUT", attributes={"type": "file", "accept": "image/png"},
                        xpath="b", is_visible=True, snapshot_node=None)
    sm = {10: a, 11: b}
    rm._upload_input_contexts = AsyncMock(return_value={
        10: {"aria_text": "上传横版封面", "region_text": ""},
        11: {"aria_text": "上传竖版封面", "region_text": ""},
    })
    clue = {"accept": "image/png", "aria_text": "上传竖版封面", "rect": None}
    assert await rm._match_file_upload_by_clue(clue, sm) == 11


@pytest.mark.asyncio
async def test_match_file_upload_by_clue_trigger_affordance_disambiguates():
    """Layer 2：trigger_affordance.text（用户实点元素文案）== 候选可点祖先 affordance_text 精筛
    （最精确——优先于文本束；即便 region_text 撞车也能区分）。"""
    rm = RerunMixin()
    a = SimpleNamespace(node_name="INPUT", attributes={"type": "file", "accept": "image/png"},
                        xpath="a", is_visible=True, snapshot_node=None)
    b = SimpleNamespace(node_name="INPUT", attributes={"type": "file", "accept": "image/png"},
                        xpath="b", is_visible=True, snapshot_node=None)
    sm = {10: a, 11: b}
    rm._upload_input_contexts = AsyncMock(return_value={
        10: {"affordance_text": "更换封面", "region_text": "点击上传"},
        11: {"affordance_text": "更换头像", "region_text": "点击上传"},
    })
    clue = {"accept": "image/png",
            "trigger_affordance": {"text": "更换头像"}, "region_text": "点击上传", "rect": None}
    # 两者 region_text 撞车，但 trigger_affordance 命中 11
    assert await rm._match_file_upload_by_clue(clue, sm) == 11


@pytest.mark.asyncio
async def test_match_file_upload_by_clue_region_text_collision_prefers_in_dialog():
    """region_text 撞车（封面区与主上传区同文案"点击上传文件..."）→ in_dialog 收窄（封面在
    [role=dialog] 内、主上传区在外）——泛化旧 in_modal 撞车 tiebreak。"""
    rm = RerunMixin()
    cover = SimpleNamespace(node_name="INPUT", attributes={"type": "file", "accept": "image/png"},
                            xpath="c", is_visible=True, snapshot_node=None)
    main_btn = SimpleNamespace(node_name="INPUT", attributes={"type": "file", "accept": "image/png"},
                               xpath="m", is_visible=True, snapshot_node=None)
    sm = {10: cover, 11: main_btn}
    rm._upload_input_contexts = AsyncMock(return_value={
        10: {"region_text": "点击上传文件或拖拽文件到这里", "in_dialog": True},
        11: {"region_text": "点击上传文件或拖拽文件到这里", "in_dialog": False},
    })
    clue = {"accept": "image/png",
            "region_text": "点击上传文件或拖拽文件到这里", "in_dialog": True, "rect": None}
    assert await rm._match_file_upload_by_clue(clue, sm) == 10


@pytest.mark.asyncio
async def test_match_file_upload_by_clue_legacy_area_text_alias():
    """向后兼容：老 history（fix/139）clue 带 area_text/in_modal、无 region_text/in_dialog，
    重放端新 ctx 用 region_text/in_dialog——want_region 走 area_text 别名、want_in_dialog 走
    in_modal 别名，仍命中。"""
    rm = RerunMixin()
    cover = SimpleNamespace(node_name="INPUT", attributes={"type": "file", "accept": "image/png"},
                            xpath="c", is_visible=True, snapshot_node=None)
    main_btn = SimpleNamespace(node_name="INPUT", attributes={"type": "file", "accept": "image/png"},
                               xpath="m", is_visible=True, snapshot_node=None)
    sm = {10: cover, 11: main_btn}
    rm._upload_input_contexts = AsyncMock(return_value={
        10: {"region_text": "点击上传文件或拖拽文件到这里", "in_dialog": True},
        11: {"region_text": "别的", "in_dialog": False},
    })
    # 老 clue：只有 area_text（无 region_text）+ in_modal（无 in_dialog）
    clue = {"accept": "image/png", "area_text": "点击上传文件或拖拽文件到这里",
            "in_modal": True, "rect": None}
    assert await rm._match_file_upload_by_clue(clue, sm) == 10


@pytest.mark.asyncio
async def test_match_file_upload_by_clue_text_miss_degrades():
    """文本束全失配（页面改版/文案漂）→ 不抛错，降级到可见性优先 + rect 就近。"""
    rm = RerunMixin()
    a = SimpleNamespace(node_name="INPUT", attributes={"type": "file", "accept": "image/png"},
                        xpath="a", is_visible=True, snapshot_node=None)
    b = SimpleNamespace(node_name="INPUT", attributes={"type": "file", "accept": "image/png"},
                        xpath="b", is_visible=False, snapshot_node=None)
    sm = {10: a, 11: b}
    rm._upload_input_contexts = AsyncMock(return_value={
        10: {"region_text": "别的"}, 11: {"region_text": ""},
    })
    clue = {"accept": "image/png", "region_text": "点击上传文件或拖拽文件到这里", "rect": None}
    # region_text 都不匹配 → 降级；可见性优先选 a(10)
    assert await rm._match_file_upload_by_clue(clue, sm) == 10


@pytest.mark.asyncio
async def test_match_file_upload_by_clue_single_and_empty():
    """单候选直接返回（不查上下文）；无候选返回 None。"""
    rm = RerunMixin()
    only = SimpleNamespace(node_name="INPUT",
                           attributes={"type": "file", "accept": "image/png"},
                           xpath="x", is_visible=True, snapshot_node=None)
    rm._upload_input_contexts = AsyncMock()
    assert await rm._match_file_upload_by_clue(
        {"accept": "image/png", "region_text": "x"}, {10: only}) == 10
    rm._upload_input_contexts.assert_not_called()
    assert await rm._match_file_upload_by_clue({"accept": "image/png"}, {}) is None


@pytest.mark.asyncio
async def test_match_file_upload_by_clue_container_rect_beats_xpath_drift():
    """#151：隐藏 input 横/竖封面——region_text/in_dialog 全撞车、xpath 漂移失配时，靠 container_rect
    （最近非零祖先真实几何）中心就近区分。这是 agent 端采集移植后抖音封面的实际解法。"""
    rm = RerunMixin()
    heng = SimpleNamespace(node_name="INPUT", attributes={"type": "file", "accept": "image/png"},
                           xpath="/html/body/div[99]/div[2]/input[1]", is_visible=True, snapshot_node=None)
    shu = SimpleNamespace(node_name="INPUT", attributes={"type": "file", "accept": "image/png"},
                          xpath="/html/body/div[99]/div[3]/input[1]", is_visible=True, snapshot_node=None)
    sm = {10: heng, 11: shu}
    rm._upload_input_contexts = AsyncMock(return_value={
        10: {"region_text": "点击上传文件或拖拽文件到这里", "in_dialog": True,
             "container_rect": {"x": 10, "y": 100, "width": 200, "height": 120}},
        11: {"region_text": "点击上传文件或拖拽文件到这里", "in_dialog": True,
             "container_rect": {"x": 500, "y": 100, "width": 120, "height": 200}},
    })
    # 线索：input 自身 rect={0,0,0,0}（隐藏）→ effective_clue_rect 取 container_rect；xpath 已漂移
    clue = {"accept": "image/png",
            "region_text": "点击上传文件或拖拽文件到这里", "in_dialog": True,
            "rect": {"x": 0, "y": 0, "width": 0, "height": 0},
            "container_rect": {"x": 500, "y": 100, "width": 120, "height": 200},
            "xpath": "/html/body/div[12]/div[3]/input[1]"}
    assert await rm._match_file_upload_by_clue(clue, sm) == 11


@pytest.mark.asyncio
async def test_match_file_upload_by_clue_accept_only_computes_ctx():
    """#151：accept-only 线索（无 region/affordance/in_dialog）+ 多候选也强制算 ctx——尾部 container_rect
    就近依赖它；此前 need_ctx=False 会跳过 ctx，rect 全零退回 candidates[0] 选错。"""
    rm = RerunMixin()
    a = SimpleNamespace(node_name="INPUT", attributes={"type": "file", "accept": "image/png"},
                        xpath="a", is_visible=True, snapshot_node=None)
    b = SimpleNamespace(node_name="INPUT", attributes={"type": "file", "accept": "image/png"},
                        xpath="b", is_visible=True, snapshot_node=None)
    sm = {10: a, 11: b}
    rm._upload_input_contexts = AsyncMock(return_value={
        10: {"container_rect": {"x": 10, "y": 0, "width": 10, "height": 10}},
        11: {"container_rect": {"x": 500, "y": 0, "width": 10, "height": 10}},
    })
    clue = {"accept": "image/png", "rect": None,
            "container_rect": {"x": 500, "y": 0, "width": 10, "height": 10}}
    assert await rm._match_file_upload_by_clue(clue, sm) == 11
    rm._upload_input_contexts.assert_awaited()


@pytest.mark.asyncio
async def test_match_file_upload_by_clue_all_zero_rect_falls_back_to_nearest():
    """#151：线索 rect/container_rect/affordance 全零/缺失、ctx 也无 container_rect → effective_clue_rect
    返回零 rect → 中心 None → 跳过 container_rect 就近，退回 legacy _nearest_idx（不崩，DOM 序首个）。"""
    rm = RerunMixin()
    a = SimpleNamespace(node_name="INPUT", attributes={"type": "file", "accept": "image/png"},
                        xpath="a", is_visible=True, snapshot_node=None)
    b = SimpleNamespace(node_name="INPUT", attributes={"type": "file", "accept": "image/png"},
                        xpath="b", is_visible=True, snapshot_node=None)
    sm = {10: a, 11: b}
    rm._upload_input_contexts = AsyncMock(return_value={
        10: {"region_text": ""}, 11: {"region_text": ""},  # 无 container_rect
    })
    clue = {"accept": "image/png", "rect": {"x": 0, "y": 0, "width": 0, "height": 0}}
    # 文本失配 → 降级；rect 全零、无 container_rect → _nearest_idx 退回 candidates[0][0]=10
    assert await rm._match_file_upload_by_clue(clue, sm) == 10


@pytest.mark.asyncio
async def test_upload_input_contexts_aligns_by_dom_order():
    """_upload_input_contexts：单次 execute_js 扫所有 input[type=file]（DOM 序）→ Python 侧按
    kind 过滤 → 与候选同序对齐 → {bid: ctx}；空入参/异常/非 list/计数不等 → 降级返 {}（不抛）。"""
    rm = RerunMixin()
    rm.browser = MagicMock()
    rm.browser.execute_js = AsyncMock(return_value=[
        {"accept": "image/png", "region_text": "点击上传文件或拖拽文件到这里", "in_dialog": True},
        {"accept": "image/png", "region_text": "", "in_dialog": False},
    ])
    ctx = await rm._upload_input_contexts([(10, None), (11, None)], kind="image")
    assert ctx[10]["region_text"] == "点击上传文件或拖拽文件到这里"
    assert ctx[11]["in_dialog"] is False
    # kind 过滤：JS 返回 3 个（1 个 video 被 kind=image 滤掉）→ 过滤后 2 个 == 2 候选
    rm.browser.execute_js = AsyncMock(return_value=[
        {"accept": "image/png", "region_text": "A"},
        {"accept": "video/mp4", "region_text": "V"},
        {"accept": "image/png", "region_text": "B"},
    ])
    ctx = await rm._upload_input_contexts([(10, None), (11, None)], kind="image")
    assert ctx[10]["region_text"] == "A" and ctx[11]["region_text"] == "B"
    # 空入参 → 不调 execute_js
    rm.browser.execute_js = AsyncMock()
    assert await rm._upload_input_contexts([], kind="image") == {}
    rm.browser.execute_js.assert_not_called()
    # execute_js 抛异常 → 返回 {}（降级）
    rm.browser.execute_js = AsyncMock(side_effect=RuntimeError("cdp down"))
    assert await rm._upload_input_contexts([(10, None)], kind="image") == {}
    # 非 list 返回 → {}
    rm.browser.execute_js = AsyncMock(return_value="not list")
    assert await rm._upload_input_contexts([(10, None)], kind="image") == {}
    # 计数不等（过滤后 ≠ 候选数）→ {}（DOM 序对应不可靠）
    rm.browser.execute_js = AsyncMock(return_value=[{"accept": "image/png", "region_text": "x"}])
    assert await rm._upload_input_contexts([(10, None), (11, None)], kind="image") == {}


# ── 语义线索重定位（_semantic_clue → locate_by_ref）─────────────────


@pytest.mark.asyncio
async def test_rerun_semantic_clue_relocates_via_attribute(make_node):
    """录制失败步存语义线索，重放在稳定页面用 locate_by_ref 重新定位（ATTRIBUTE 级匹配）。

    模拟 submit 场景：录制时 get_state 抓跳转后页、button 消失 → 存语义线索；重放到 click 步页面
    稳定、button 在，xpath 虽漂移但 name 命中 → 重定位成功。详见 semantic-clue-replay.md。
    """
    from tree_walker.agent.agent import Agent
    from tree_walker.config import AgentSettings, JudgeSettings

    history = AgentHistoryList(history=[
        AgentHistory(step_number=0,
                     model_output={"action": {"name": "click", "params": {}}},
                     result=[ActionResult()],
                     state_summary={"url": "http://x"},
                     interacted_element=[{
                         "_semantic_clue": True,
                         "xpath": "/drifted/xpath",          # Level 1 xpath 漂移失配
                         "tag": "button", "name": "submit",   # Level 2 ATTRIBUTE 命中
                         "id": "", "ariaLabel": "", "role": "", "rect": None,
                     }]),
    ])
    btn = make_node(tag="button", node_id=7069, backend_node_id=7069,
                    attributes={"name": "submit"})
    state = SimpleNamespace(url="http://x", dom_state=SimpleNamespace(selector_map={7069: btn}))

    browser = MagicMock()
    browser._settings = SimpleNamespace(wait_between_actions=0)
    browser.start = AsyncMock()
    browser.stop = AsyncMock()
    browser.navigate = AsyncMock()
    browser.get_state = AsyncMock(return_value=state)
    browser.get_current_url = AsyncMock(return_value="http://x")

    agent = Agent(task="x", llm=MagicMock(), browser=browser,
                  settings=AgentSettings(judge=JudgeSettings(enabled=False)))
    captured: dict = {}

    async def fake_execute(name, params, br, st):
        captured["name"] = name
        captured["params"] = dict(params)
        return ActionResult()

    agent.tools.execute = fake_execute
    agent.llm = _StructuredLLM()

    await agent.rerun_history(
        history, max_step_interval=0, delay_between_actions=0, summary_llm=_StructuredLLM()
    )

    assert captured["name"] == "click"
    assert captured["params"]["index"] == 7069   # 语义线索重定位命中 button


def test_rerun_semantic_clue_failure_format():
    """语义线索重定位失败 → _format_semantic_clue_failure 给诊断信息（不静默 skip）。"""
    rm = RerunMixin()
    clue = {"_semantic_clue": True, "tag": "button", "xpath": "/x",
            "name": "submit", "id": "", "ariaLabel": "", "role": "", "rect": None}
    msg = rm._format_semantic_clue_failure(clue, {})
    assert "button" in msg.lower()
    assert "submit" in msg
    assert "locate_by_ref" in msg


# ── 重放文件根目录 + 相对路径校验 ─────────────────────────────────────


def test_resolve_rerun_path_relative():
	from tree_walker.agent.rerun import resolve_rerun_path
	assert resolve_rerun_path("rerun-history", "a/b.json") == Path("rerun-history") / "a" / "b.json"


def test_resolve_rerun_path_absolute_root(tmp_path):
	from tree_walker.agent.rerun import resolve_rerun_path
	# 根目录本身允许绝对；相对路径落在其下
	assert resolve_rerun_path(str(tmp_path), "x.json") == tmp_path / "x.json"


def test_resolve_rerun_path_rejects_absolute():
	from tree_walker.agent.rerun import resolve_rerun_path
	with pytest.raises(ValueError):
		resolve_rerun_path("rerun-history", "/abs/x.json")
	with pytest.raises(ValueError):
		resolve_rerun_path("rerun-history", "C:/abs/x.json")


def test_resolve_rerun_path_rejects_traversal():
	from tree_walker.agent.rerun import resolve_rerun_path
	with pytest.raises(ValueError):
		resolve_rerun_path("rerun-history", "../escape.json")
	with pytest.raises(ValueError):
		resolve_rerun_path("rerun-history", "a/../../escape.json")


def test_agent_rerun_path_default_and_rejection():
	from tree_walker.agent.rerun import RerunMixin
	rm = RerunMixin()
	rm.rerun_history_dir = "rerun-history"
	assert rm.rerun_path("x.json") == Path("rerun-history/x.json")
	with pytest.raises(ValueError):
		rm.rerun_path("/abs/x.json")
	with pytest.raises(ValueError):
		rm.rerun_path("../escape.json")


def test_agentsettings_default_rerun_history_dir():
	from tree_walker.config import AgentSettings
	assert AgentSettings().rerun_history_dir == "rerun-history"


def test_agentsettings_default_rerun_timing():
	from tree_walker.config import AgentSettings
	s = AgentSettings()
	# 阶段 1：默认值对齐 CLI/TUI 现状硬编码（1/5），避免改变现有重放行为
	assert s.rerun_delay_between_actions == 1.0
	assert s.rerun_max_step_interval == 5.0
	assert s.rerun_wait_for_elements is False
	assert s.rerun_wait_for_page_settle is False


def test_agent_wires_rerun_timing_from_settings():
	# Agent.__init__ 把 AgentSettings 的时序字段拷到 self（与 rerun_history_dir 同范式），
	# 供 rerun_history 的 None 哨兵默认值回落使用。
	from tree_walker.agent import Agent
	from tree_walker.browser.session import BrowserSettings
	from tree_walker.config import AgentSettings, JudgeSettings
	browser = MagicMock()
	browser._settings = BrowserSettings()
	agent = Agent(
		task="x", llm=MagicMock(), browser=browser,
		settings=AgentSettings(
			judge=JudgeSettings(enabled=False),
			rerun_delay_between_actions=2.5,
			rerun_max_step_interval=30.0,
			rerun_wait_for_elements=True,
			rerun_wait_for_page_settle=True,
		),
	)
	assert agent.rerun_delay_between_actions == 2.5
	assert agent.rerun_max_step_interval == 30.0
	assert agent.rerun_wait_for_elements is True
	assert agent.rerun_wait_for_page_settle is True


@pytest.mark.asyncio
async def test_rerun_history_timing_defaults_to_settings_when_kwargs_omitted(make_node):
	# 不传时序 kwargs → rerun_history 用 self.rerun_xxx（AgentSettings）归一化，
	# 再传给 _compute_step_delay（覆盖 None 哨兵的 4 个分支）。
	from tree_walker.agent.agent import Agent
	from tree_walker.config import AgentSettings, JudgeSettings

	rec_node = make_node(tag="input", node_id=2, backend_node_id=2,
	                     attributes={"name": "email", "type": "email"})
	hist_elem = DOMInteractedElement.load_from_enhanced_dom_tree(rec_node).to_dict()
	history = AgentHistoryList(history=[
		AgentHistory(step_number=0,
		             model_output={"action": {"name": "input_text", "params": {"index": 2, "text": "a@b.com"}}},
		             result=[ActionResult()],
		             state_summary={"url": "http://example.com"},
		             interacted_element=[hist_elem]),
	])
	live_node = make_node(tag="input", node_id=7, backend_node_id=7,
	                      attributes={"name": "email", "type": "email"})
	state = SimpleNamespace(url="http://example.com",
	                        dom_state=SimpleNamespace(selector_map={7: live_node}))

	browser = MagicMock()
	browser._settings = SimpleNamespace(wait_between_actions=0)
	browser.start = AsyncMock()
	browser.stop = AsyncMock()
	browser.navigate = AsyncMock()
	browser.get_state = AsyncMock(return_value=state)
	browser.get_current_url = AsyncMock(return_value="http://example.com")

	agent = Agent(task="do x", llm=MagicMock(), browser=browser,
	              settings=AgentSettings(judge=JudgeSettings(enabled=False),
	                                    rerun_delay_between_actions=7.0,
	                                    rerun_max_step_interval=9.0))

	async def fake_execute(name, params, br, st):
		return ActionResult()
	agent.tools.execute = fake_execute
	agent.llm = _StructuredLLM()

	captured: dict = {}
	def fake_compute(item, dba, msi):
		captured["dba"] = dba
		captured["msi"] = msi
		return 0.0
	agent._compute_step_delay = fake_compute

	# 注意：不传任何时序 kwargs → 走 self.rerun_xxx 归一化
	await agent.rerun_history(history, summary_llm=_StructuredLLM())

	assert captured["dba"] == 7.0    # delay_between_actions 来自 AgentSettings
	assert captured["msi"] == 9.0    # max_step_interval 来自 AgentSettings


# ── 阶段 2：_wait_until 通用轮询原语 ────────────────────────────────────


@pytest.mark.asyncio
async def test_wait_until_predicate_hits_returns_early():
	"""谓词首次命中即返回，不 sleep / 不 get_state（乐观短路）。"""
	rm = RerunMixin()
	rm.browser = MagicMock()
	rm.browser.get_state = AsyncMock(return_value=_state({}))
	s1 = _state({5: "node"})
	out = await rm._wait_until(s1, lambda s: bool(s.dom_state.selector_map), timeout=5.0, poll=0.01)
	assert out is s1
	rm.browser.get_state.assert_not_called()       # 命中即返，未刷新


@pytest.mark.asyncio
async def test_wait_until_timeout_degrades_to_state():
	"""谓词永不命中 → 超时降级返回 state（不抛错）。"""
	rm = RerunMixin()
	rm.browser = MagicMock()
	rm.browser.get_state = AsyncMock(return_value=_state({}))
	out = await rm._wait_until(_state({}), lambda s: False, timeout=0.05, poll=0.01)
	assert out is not None                          # 降级返回，非 None / 非 raise


# ── 阶段 2：actionability 纯函数（_is_actionable / _is_file_input）───────


def test_is_actionable_visible_enabled_passes():
	from tree_walker.agent.rerun import _is_actionable
	assert _is_actionable(SimpleNamespace(is_visible=True, ax_node=None, attributes={})) is True


def test_is_actionable_visible_false_blocks():
	from tree_walker.agent.rerun import _is_actionable
	assert _is_actionable(SimpleNamespace(is_visible=False, ax_node=None, attributes={})) is False


def test_is_actionable_none_is_visible_passes():
	"""is_visible=None（未知）保守放过——永不引入新失败。"""
	from tree_walker.agent.rerun import _is_actionable
	assert _is_actionable(SimpleNamespace(is_visible=None, ax_node=None, attributes={})) is True


def test_is_actionable_ax_disabled_blocks():
	from tree_walker.agent.rerun import _is_actionable
	prop = SimpleNamespace(name="disabled", value=True)
	ax = SimpleNamespace(properties=[prop])
	assert _is_actionable(SimpleNamespace(is_visible=True, ax_node=ax, attributes={})) is False
	# AX disabled value=False 不阻断
	prop_f = SimpleNamespace(name="disabled", value=False)
	assert _is_actionable(SimpleNamespace(is_visible=True,
		ax_node=SimpleNamespace(properties=[prop_f]), attributes={})) is True


def test_is_actionable_html_disabled_blocks():
	from tree_walker.agent.rerun import _is_actionable
	assert _is_actionable(SimpleNamespace(is_visible=True, ax_node=None, attributes={"disabled": ""})) is False


# ── 阶段 4：receives-events（L1 paint_order / L2 pointer-events）+ aria-disabled ──


def test_is_actionable_aria_disabled_blocks():
	"""阶段4 遗留收编：aria-disabled="true" 阻断（无需 check_receives_events）。"""
	from tree_walker.agent.rerun import _is_actionable
	assert _is_actionable(SimpleNamespace(
		is_visible=True, ax_node=None, attributes={"aria-disabled": "true"})) is False
	# aria-disabled="false" 不阻断
	assert _is_actionable(SimpleNamespace(
		is_visible=True, ax_node=None, attributes={"aria-disabled": "false"})) is True


def test_is_actionable_pointer_events_none_blocks_when_checking():
	"""阶段二 L2：check_receives_events=True 时 pointer-events:none 阻断。"""
	from tree_walker.agent.rerun import _is_actionable
	snap = SimpleNamespace(computed_styles={"pointer-events": "none"})
	assert _is_actionable(SimpleNamespace(
		is_visible=True, ax_node=None, attributes={}, snapshot_node=snap),
		check_receives_events=True) is False


def test_is_actionable_pointer_events_none_passes_when_not_checking():
	"""零回归基线：check_receives_events=False（默认）→ pointer-events:none 不阻断。"""
	from tree_walker.agent.rerun import _is_actionable
	snap = SimpleNamespace(computed_styles={"pointer-events": "none"})
	assert _is_actionable(SimpleNamespace(
		is_visible=True, ax_node=None, attributes={}, snapshot_node=snap)) is True


def test_is_actionable_paint_order_blocks_when_checking():
	"""阶段二 L1：check_receives_events=True 时 ignored_by_paint_order 阻断。"""
	from tree_walker.agent.rerun import _is_actionable
	assert _is_actionable(SimpleNamespace(
		is_visible=True, ax_node=None, attributes={}, ignored_by_paint_order=True),
		check_receives_events=True) is False


def test_is_actionable_snapshot_none_passes():
	"""snapshot_node 缺失 → 保守放过（不引入新失败）。"""
	from tree_walker.agent.rerun import _is_actionable
	assert _is_actionable(SimpleNamespace(
		is_visible=True, ax_node=None, attributes={}, snapshot_node=None),
		check_receives_events=True) is True


def test_is_file_input_detection():
	from tree_walker.agent.rerun import _is_file_input
	assert _is_file_input(SimpleNamespace(node_name="INPUT", attributes={"type": "file"})) is True
	assert _is_file_input(SimpleNamespace(node_name="INPUT", attributes={"type": "text"})) is False
	assert _is_file_input(SimpleNamespace(node_name="BUTTON", attributes={"type": "file"})) is False


# ── 阶段 2：_locate_target 统一只读谓词 ──────────────────────────────────


def test_locate_target_none_hist_returns_none():
	rm = RerunMixin()
	assert rm._locate_target(None, {5: "x"}) is None


def test_locate_target_semantic_clue_path(make_node):
	rm = RerunMixin()
	node = make_node(tag="button", node_id=5, backend_node_id=5, attributes={"aria-label": "保存"})
	hist = {"_semantic_clue": True, "tag": "button", "ariaLabel": "保存"}
	out = rm._locate_target(hist, {5: node})
	assert out is not None and out[0] == 5 and out[1] is node


def test_locate_target_fingerprint_path(make_node):
	rm = RerunMixin()
	node = make_node(tag="input", node_id=7, backend_node_id=7, attributes={"name": "email"})
	hist = DOMInteractedElement.load_from_enhanced_dom_tree(node).to_dict()
	out = rm._locate_target(hist, {7: node})
	assert out is not None and out[0] == 7


def test_locate_target_fingerprint_miss_returns_none(make_node):
	rm = RerunMixin()
	# 不同 tag → 六级匹配都要求 node_name 相同 → 全失配
	# （同 tag 无 name/class 会被 CLASS 级「空 class 超集」兜底命中，不能用来测 miss）
	node = make_node(tag="button", node_id=7, backend_node_id=7, attributes={"aria-label": "x"})
	hist = DOMInteractedElement.load_from_enhanced_dom_tree(
		make_node(tag="input", node_id=9, backend_node_id=9, attributes={"name": "email"})
	).to_dict()
	assert rm._locate_target(hist, {7: node}) is None


# ── 阶段 2 缺口 5：等目标元素 ────────────────────────────────────────────


def test_collect_target_hists_skips_upload_and_no_fingerprint():
	rm = RerunMixin()
	item = AgentHistory(step_number=0, model_output={"actions": [
		{"name": "click", "params": {"index": 5}},
		{"name": "upload_file", "params": {"path": "x.mp4"}},
		{"name": "extract", "params": {}},
	]}, result=[], interacted_element=[
		{"element_hash": 1, "x_path": "a"},   # click 有指纹 → 收
		None,                                   # upload_file → 剔除
		None,                                   # extract 无指纹 → 不收
	])
	out = rm._collect_target_hists(item)
	assert len(out) == 1 and out[0]["element_hash"] == 1


@pytest.mark.asyncio
async def test_wait_for_target_elements_all_located_returns_early(make_node):
	rm = RerunMixin()
	rm.browser = MagicMock()
	node = make_node(tag="button", node_id=5, backend_node_id=5, attributes={"aria-label": "go"})
	hist = {"_semantic_clue": True, "tag": "button", "ariaLabel": "go"}
	item = AgentHistory(step_number=0,
		model_output={"actions": [{"name": "click", "params": {}}]},
		result=[], interacted_element=[hist])
	out = await rm._wait_for_target_elements(_state({5: node}), item, timeout=5.0, poll=0.01)
	assert out.dom_state.selector_map[5] is node
	rm.browser.get_state.assert_not_called()       # 首帧命中，未 poll


@pytest.mark.asyncio
async def test_wait_for_target_elements_timeout_degrades(make_node):
	rm = RerunMixin()
	rm.browser = MagicMock()
	rm.browser.get_state = AsyncMock(return_value=_state({}))   # 永远找不到
	hist = {"_semantic_clue": True, "tag": "button", "ariaLabel": "missing"}
	item = AgentHistory(step_number=0,
		model_output={"actions": [{"name": "click", "params": {}}]},
		result=[], interacted_element=[hist])
	out = await rm._wait_for_target_elements(_state({}), item, timeout=0.05, poll=0.01)
	assert out is not None                          # 降级，不抛错


@pytest.mark.asyncio
async def test_wait_for_target_elements_no_targets_no_poll():
	"""纯 extract（无目标）→ 立即返回，不 poll。"""
	rm = RerunMixin()
	rm.browser = MagicMock()
	item = AgentHistory(step_number=0,
		model_output={"actions": [{"name": "extract", "params": {}}]},
		result=[], interacted_element=[None])
	s0 = _state({})
	out = await rm._wait_for_target_elements(s0, item, timeout=5.0, poll=0.01)
	assert out is s0
	rm.browser.get_state.assert_not_called()


# ── 阶段 2：_wait_for_actionability（index 漂移重解析 + 降级）────────────


@pytest.mark.asyncio
async def test_wait_for_actionability_waits_until_visible(make_node):
	rm = RerunMixin()
	rm.browser = MagicMock()
	visible = make_node(tag="button", node_id=5, backend_node_id=5, is_visible=True,
		attributes={"aria-label": "go"})
	hidden = make_node(tag="button", node_id=5, backend_node_id=5, is_visible=False,
		attributes={"aria-label": "go"})
	rm.browser.get_state = AsyncMock(return_value=_state({5: visible}))
	hist = {"_semantic_clue": True, "tag": "button", "ariaLabel": "go"}
	out_state, idx, node = await rm._wait_for_actionability(
		_state({5: hidden}), hist, 5, timeout=2.0, poll=0.01)
	assert idx == 5
	from tree_walker.agent.rerun import _is_actionable
	assert _is_actionable(node) is True             # 等到了 visible


@pytest.mark.asyncio
async def test_wait_for_actionability_timeout_degrades(make_node):
	rm = RerunMixin()
	rm.browser = MagicMock()
	hidden = make_node(tag="button", node_id=5, backend_node_id=5, is_visible=False,
		attributes={"aria-label": "go"})
	rm.browser.get_state = AsyncMock(return_value=_state({5: hidden}))
	hist = {"_semantic_clue": True, "tag": "button", "ariaLabel": "go"}
	out_state, idx, node = await rm._wait_for_actionability(
		_state({5: hidden}), hist, 5, timeout=0.05, poll=0.01)
	assert idx == 5                                  # 降级返回最新（仍 hidden），不抛错


@pytest.mark.asyncio
async def test_wait_for_actionability_index_drift_relocates(make_node):
	"""poll 刷新 state 后目标从 idx 5 漂移到 7；用 hist 重解析命中 7。"""
	rm = RerunMixin()
	rm.browser = MagicMock()
	node5_hidden = make_node(tag="button", node_id=5, backend_node_id=5, is_visible=False,
		attributes={"aria-label": "go"})
	node7_visible = make_node(tag="button", node_id=7, backend_node_id=7, is_visible=True,
		attributes={"aria-label": "go"})
	rm.browser.get_state = AsyncMock(return_value=_state({7: node7_visible}))
	hist = {"_semantic_clue": True, "tag": "button", "ariaLabel": "go"}
	out_state, idx, node = await rm._wait_for_actionability(
		_state({5: node5_hidden}), hist, 5, timeout=2.0, poll=0.01)
	assert idx == 7                                  # 漂移后重解析到 7


@pytest.mark.asyncio
async def test_wait_for_actionability_runtime_occlusion(make_node):
	"""阶段二 L3：_is_actionable 通过但 _is_element_occluded=True → 继续等至超时降级。"""
	rm = RerunMixin()
	rm.browser = MagicMock()
	visible = make_node(tag="button", node_id=5, backend_node_id=5, is_visible=True,
		attributes={"aria-label": "go"})
	rm.browser.get_state = AsyncMock(return_value=_state({5: visible}))
	rm.browser._is_element_occluded = AsyncMock(return_value=True)   # 始终被遮挡
	hist = {"_semantic_clue": True, "tag": "button", "ariaLabel": "go"}
	out_state, idx, node = await rm._wait_for_actionability(
		_state({5: visible}), hist, 5, timeout=0.05, poll=0.01,
		receives_events=False, runtime_occlusion=True)
	assert idx == 5                                  # 降级返回，不抛错
	rm.browser._is_element_occluded.assert_called()  # L3 确实被调用


# ── 阶段 4：stable（_is_rect_stable / _wait_for_stable）─────────────────


@pytest.mark.asyncio
async def test_is_rect_stable_stable():
	rm = RerunMixin()
	rm.browser = MagicMock()
	rect = SimpleNamespace(x=1.0, y=2.0, width=10.0, height=20.0)
	rm.browser.get_element_coordinates = AsyncMock(return_value=rect)
	assert await rm._is_rect_stable(5, interval=0.0, tolerance=1.0) is True


@pytest.mark.asyncio
async def test_is_rect_stable_drift():
	rm = RerunMixin()
	rm.browser = MagicMock()
	r1 = SimpleNamespace(x=1.0, y=2.0, width=10.0, height=20.0)
	r2 = SimpleNamespace(x=50.0, y=2.0, width=10.0, height=20.0)   # x 漂移
	rm.browser.get_element_coordinates = AsyncMock(side_effect=[r1, r2])
	assert await rm._is_rect_stable(5, interval=0.0, tolerance=1.0) is False


@pytest.mark.asyncio
async def test_is_rect_stable_no_coords():
	rm = RerunMixin()
	rm.browser = MagicMock()
	rm.browser.get_element_coordinates = AsyncMock(return_value=None)
	assert await rm._is_rect_stable(5) is False     # 拿不到坐标 → 不稳定（保守）


@pytest.mark.asyncio
async def test_wait_for_stable_hits(make_node):
	rm = RerunMixin()
	rm.browser = MagicMock()
	node = make_node(tag="button", node_id=5, backend_node_id=5, is_visible=True,
		attributes={"aria-label": "go"})
	rect = SimpleNamespace(x=1.0, y=2.0, width=10.0, height=20.0)
	rm.browser.get_element_coordinates = AsyncMock(return_value=rect)
	rm.browser.get_state = AsyncMock(return_value=_state({5: node}))
	hist = {"_semantic_clue": True, "tag": "button", "ariaLabel": "go"}
	out_state, idx = await rm._wait_for_stable(
		_state({5: node}), hist, 5, timeout=2.0, poll=0.01,
		interval=0.0, tolerance=1.0)
	assert idx == 5


@pytest.mark.asyncio
async def test_wait_for_stable_timeout_degrades(make_node):
	"""rect 始终漂移 → 轮询至超时降级返回最新 (state, idx)，不抛错（覆盖循环本体）。"""
	rm = RerunMixin()
	rm.browser = MagicMock()
	node = make_node(tag="button", node_id=5, backend_node_id=5, is_visible=True,
		attributes={"aria-label": "go"})
	r1 = SimpleNamespace(x=1.0, y=2.0, width=10.0, height=20.0)
	r2 = SimpleNamespace(x=50.0, y=2.0, width=10.0, height=20.0)   # x 漂移
	rm.browser.get_element_coordinates = AsyncMock(side_effect=[r1, r2] * 10)
	rm.browser.get_state = AsyncMock(return_value=_state({5: node}))
	hist = {"_semantic_clue": True, "tag": "button", "ariaLabel": "go"}
	out_state, idx = await rm._wait_for_stable(
		_state({5: node}), hist, 5, timeout=0.05, poll=0.01,
		interval=0.0, tolerance=1.0)
	assert idx == 5                                  # 降级返回，不抛错


# ── 阶段 2：actionability 编排（默认关零行为变更 + upload_file 不误杀）──


def _build_agent_with_click_step(make_node, *, live_visible: bool, actionability: bool):
	"""构造 Agent + 单步 click 历史（不可见按钮），返回 (agent, executed)。"""
	from tree_walker.agent.agent import Agent
	from tree_walker.config import AgentSettings, JudgeSettings

	rec_node = make_node(tag="button", node_id=5, backend_node_id=5,
		attributes={"aria-label": "go"})
	hist_elem = DOMInteractedElement.load_from_enhanced_dom_tree(rec_node).to_dict()
	history = AgentHistoryList(history=[
		AgentHistory(step_number=0,
			model_output={"action": {"name": "click", "params": {"index": 5}}},
			result=[ActionResult()], state_summary={"url": "http://x"},
			interacted_element=[hist_elem]),
	])
	live_node = make_node(tag="button", node_id=5, backend_node_id=5,
		is_visible=live_visible, attributes={"aria-label": "go"})
	state = SimpleNamespace(url="http://x",
		dom_state=SimpleNamespace(selector_map={5: live_node}))
	browser = MagicMock()
	browser._settings = SimpleNamespace(wait_between_actions=0)
	browser.start = AsyncMock()
	browser.stop = AsyncMock()
	browser.navigate = AsyncMock()
	browser.get_state = AsyncMock(return_value=state)
	browser.get_current_url = AsyncMock(return_value="http://x")
	agent = Agent(task="x", llm=MagicMock(), browser=browser,
		settings=AgentSettings(judge=JudgeSettings(enabled=False),
			rerun_actionability_check=actionability))
	executed: list = []

	async def fake_execute(name, params, br, st):
		executed.append((name, dict(params)))
		return ActionResult()
	agent.tools.execute = fake_execute
	agent.llm = _StructuredLLM()
	return agent, history, executed


@pytest.mark.asyncio
async def test_actionability_default_off_executes_invisible(make_node):
	"""rerun_actionability_check=False（默认）→ 即使元素不可见也直接执行（零行为变更）。"""
	agent, history, executed = _build_agent_with_click_step(
		make_node, live_visible=False, actionability=False)
	await agent.rerun_history(history, summary_llm=_StructuredLLM())
	assert executed and executed[0][0] == "click"    # 不可见仍执行（与改造前一致）


@pytest.mark.asyncio
async def test_actionability_on_still_executes_on_timeout(make_node):
	"""开启 actionability + 元素持续不可见 → 超时降级，仍执行（永不引入新失败）。"""
	agent, history, executed = _build_agent_with_click_step(
		make_node, live_visible=False, actionability=True)
	# 缩短超时让降级快（直接改 self 上的值）
	agent.rerun_actionability_timeout = 0.05
	agent.rerun_actionability_poll = 0.01
	await agent.rerun_history(history, summary_llm=_StructuredLLM())
	assert executed and executed[0][0] == "click"    # 降级后照常执行


@pytest.mark.asyncio
async def test_actionability_skips_upload_file_invisible_fileinput(make_node):
	"""upload_file 的隐藏 file input 不被 actionability 误杀（白名单 + _is_file_input 双保险）。"""
	from tree_walker.agent.agent import Agent
	from tree_walker.config import AgentSettings, JudgeSettings

	rec_node = make_node(tag="input", node_id=3, backend_node_id=3,
		attributes={"type": "file", "accept": "video/*"})
	hist_elem = DOMInteractedElement.load_from_enhanced_dom_tree(rec_node).to_dict()
	hist_elem["accept"] = "video/*"                  # upload_file 走 accept 兜底路径
	history = AgentHistoryList(history=[
		AgentHistory(step_number=0,
			model_output={"action": {"name": "upload_file",
				"params": {"path": "x.mp4", "accept": "video/*"}}},
			result=[ActionResult()], state_summary={"url": "http://x"},
			interacted_element=[hist_elem]),
	])
	# file input 隐藏（is_visible=False）
	file_node = make_node(tag="input", node_id=3, backend_node_id=3, is_visible=False,
		attributes={"type": "file", "accept": "video/*"})
	state = SimpleNamespace(url="http://x",
		dom_state=SimpleNamespace(selector_map={3: file_node}))
	browser = MagicMock()
	browser._settings = SimpleNamespace(wait_between_actions=0)
	browser.start = AsyncMock(); browser.stop = AsyncMock(); browser.navigate = AsyncMock()
	browser.get_state = AsyncMock(return_value=state)
	browser.get_current_url = AsyncMock(return_value="http://x")
	agent = Agent(task="x", llm=MagicMock(), browser=browser,
		settings=AgentSettings(judge=JudgeSettings(enabled=False),
			rerun_actionability_check=True))          # 开启 actionability
	agent.rerun_actionability_timeout = 0.05
	agent.rerun_actionability_poll = 0.01
	executed: list = []

	async def fake_execute(name, params, br, st):
		executed.append((name, dict(params)))
		return ActionResult()
	agent.tools.execute = fake_execute
	agent.llm = _StructuredLLM()
	await agent.rerun_history(history, summary_llm=_StructuredLLM())
	# upload_file 被执行（未被 visible 检查误杀）
	assert executed and executed[0][0] == "upload_file"


# ── 阶段 2：配置（actionability 3 字段）─────────────────────────────────


def test_agentsettings_default_actionability():
	from tree_walker.config import AgentSettings
	s = AgentSettings()
	assert s.rerun_actionability_check is False       # 默认关 = 零行为变更
	assert s.rerun_actionability_timeout == 2.0
	assert s.rerun_actionability_poll == 0.3


def test_agent_wires_actionability_from_settings():
	from tree_walker.agent import Agent
	from tree_walker.browser.session import BrowserSettings
	from tree_walker.config import AgentSettings, JudgeSettings
	browser = MagicMock()
	browser._settings = BrowserSettings()
	agent = Agent(task="x", llm=MagicMock(), browser=browser,
		settings=AgentSettings(judge=JudgeSettings(enabled=False),
			rerun_actionability_check=True,
			rerun_actionability_timeout=5.0,
			rerun_actionability_poll=0.5))
	assert agent.rerun_actionability_check is True
	assert agent.rerun_actionability_timeout == 5.0
	assert agent.rerun_actionability_poll == 0.5


# ── 阶段 4：actionability receives-events + stable 配置 ──────────────────


def test_agentsettings_default_actionability_stage4():
	from tree_walker.config import AgentSettings
	s = AgentSettings()
	assert s.rerun_actionability_receives_events is True     # L1+L2 默认开（零开销，总开关管辖）
	assert s.rerun_actionability_runtime_occlusion is False  # L3 默认关（CDP 开销）
	assert s.rerun_actionability_stable is False             # 阶段三默认关
	assert s.rerun_actionability_stable_interval == 0.1
	assert s.rerun_actionability_stable_tolerance == 1.0


def test_agent_wires_actionability_stage4_from_settings():
	from tree_walker.agent import Agent
	from tree_walker.browser.session import BrowserSettings
	from tree_walker.config import AgentSettings, JudgeSettings
	browser = MagicMock()
	browser._settings = BrowserSettings()
	agent = Agent(task="x", llm=MagicMock(), browser=browser,
		settings=AgentSettings(judge=JudgeSettings(enabled=False),
			rerun_actionability_receives_events=False,
			rerun_actionability_runtime_occlusion=True,
			rerun_actionability_stable=True,
			rerun_actionability_stable_interval=0.2,
			rerun_actionability_stable_tolerance=2.0))
	assert agent.rerun_actionability_receives_events is False
	assert agent.rerun_actionability_runtime_occlusion is True
	assert agent.rerun_actionability_stable is True
	assert agent.rerun_actionability_stable_interval == 0.2
	assert agent.rerun_actionability_stable_tolerance == 2.0


# ── 阶段 3：networkidle 开关 + 重放端 upload 等待 ──────────────────────────


def test_agentsettings_default_networkidle():
	from tree_walker.config import AgentSettings
	s = AgentSettings()
	assert s.rerun_wait_for_networkidle is False    # 默认关 = 零行为变更
	assert s.rerun_upload_wait_video == 5.0         # 默认 = 原录制端硬编码（零差异）
	assert s.rerun_upload_wait_image == 3.0


def test_browsersettings_default_network_idle_tuning():
	from tree_walker.config import BrowserSettings
	s = BrowserSettings()
	assert s.network_idle_timeout == 5.0
	assert s.network_idle_stability_window == 0.5
	assert s.network_idle_poll_interval == 0.1


def test_agent_wires_networkidle_from_settings():
	from tree_walker.agent import Agent
	from tree_walker.browser.session import BrowserSettings
	from tree_walker.config import AgentSettings, JudgeSettings
	browser = MagicMock()
	browser._settings = BrowserSettings()
	agent = Agent(task="x", llm=MagicMock(), browser=browser,
		settings=AgentSettings(judge=JudgeSettings(enabled=False),
			rerun_wait_for_networkidle=True,
			rerun_upload_wait_video=8.0,
			rerun_upload_wait_image=4.0))
	assert agent.rerun_wait_for_networkidle is True
	assert agent.rerun_upload_wait_video == 8.0
	assert agent.rerun_upload_wait_image == 4.0


def _build_agent_with_done_step():
	"""单步 done 历史（无定位动作），隔离测 get_state 的 wait_networkidle 透传。"""
	from tree_walker.agent.agent import Agent
	from tree_walker.config import AgentSettings, JudgeSettings
	history = AgentHistoryList(history=[
		AgentHistory(step_number=0,
			model_output={"action": {"name": "done", "params": {}}},
			result=[ActionResult()], state_summary={"url": "http://x"},
			interacted_element=[None]),
	])
	state = SimpleNamespace(url="http://x", dom_state=SimpleNamespace(selector_map={}))
	browser = MagicMock()
	browser._settings = SimpleNamespace(wait_between_actions=0)
	browser.start = AsyncMock(); browser.stop = AsyncMock(); browser.navigate = AsyncMock()
	browser.get_state = AsyncMock(return_value=state)
	browser.get_current_url = AsyncMock(return_value="http://x")
	agent = Agent(task="x", llm=MagicMock(), browser=browser,
		settings=AgentSettings(judge=JudgeSettings(enabled=False)))
	agent.llm = _StructuredLLM()
	return agent, history, browser


@pytest.mark.asyncio
async def test_networkidle_default_off_passes_false_to_get_state():
	"""rerun_wait_for_networkidle=False（默认）→ get_state 收到 wait_networkidle=False（不等待）。"""
	agent, history, browser = _build_agent_with_done_step()
	await agent.rerun_history(history, delay_between_actions=0, max_step_interval=0,
		summary_llm=_StructuredLLM())
	waits = [c.kwargs.get("wait_networkidle") for c in browser.get_state.call_args_list]
	assert waits                                    # get_state 至少被调一次
	assert all(w is False for w in waits)           # 默认关，全部不等待


@pytest.mark.asyncio
async def test_networkidle_on_passes_true_to_get_state():
	"""rerun_wait_for_networkidle=True → get_state 收到 wait_networkidle=True（透传）。"""
	agent, history, browser = _build_agent_with_done_step()
	agent.rerun_wait_for_networkidle = True
	await agent.rerun_history(history, delay_between_actions=0, max_step_interval=0,
		summary_llm=_StructuredLLM())
	waits = [c.kwargs.get("wait_networkidle") for c in browser.get_state.call_args_list]
	assert any(w is True for w in waits)


def _build_agent_with_upload_step(make_node, *, video_wait=5.0, path="v.mp4"):
	"""单步 upload_file 历史（accept 兜底路径），用于测重放端 upload 等待。"""
	from tree_walker.agent.agent import Agent
	from tree_walker.config import AgentSettings, JudgeSettings
	history = AgentHistoryList(history=[
		AgentHistory(step_number=0,
			model_output={"action": {"name": "upload_file",
				"params": {"path": path, "accept": "video/*"}}},
			result=[ActionResult()], state_summary={"url": "http://x"},
			interacted_element=[{"accept": "video/*"}]),
	])
	state = SimpleNamespace(url="http://x", dom_state=SimpleNamespace(selector_map={}))
	browser = MagicMock()
	browser._settings = SimpleNamespace(wait_between_actions=0)
	browser.start = AsyncMock(); browser.stop = AsyncMock(); browser.navigate = AsyncMock()
	browser.get_state = AsyncMock(return_value=state)
	browser.get_current_url = AsyncMock(return_value="http://x")
	agent = Agent(task="x", llm=MagicMock(), browser=browser,
		settings=AgentSettings(judge=JudgeSettings(enabled=False),
			rerun_upload_wait_video=video_wait))
	agent.llm = _StructuredLLM()
	return agent, history


@pytest.mark.asyncio
async def test_upload_wait_sleeps_after_successful_upload(make_node, monkeypatch):
	"""upload_file 成功 → 按 rerun_upload_wait_video 等待（video 默认 5.0）。"""
	agent, history = _build_agent_with_upload_step(make_node, video_wait=5.0)
	executed: list = []

	async def fake_execute(name, params, br, st):
		executed.append(name)
		return ActionResult()                        # 成功，无 error
	agent.tools.execute = fake_execute

	sleeps: list = []

	async def fake_sleep(s):
		sleeps.append(s)
	monkeypatch.setattr("tree_walker.agent.rerun.asyncio.sleep", fake_sleep)
	await agent.rerun_history(history, delay_between_actions=0, max_step_interval=0,
		summary_llm=_StructuredLLM())
	assert "upload_file" in executed
	assert 5.0 in sleeps                            # video upload 后等待 5s


@pytest.mark.asyncio
async def test_upload_wait_skipped_on_upload_error(make_node, monkeypatch):
	"""upload_file 失败（result.error）→ 跳过等待（比原"无条件睡"更合理）。"""
	agent, history = _build_agent_with_upload_step(make_node, video_wait=5.0)

	async def fake_execute(name, params, br, st):
		return ActionResult(error="upload failed")  # 失败
	agent.tools.execute = fake_execute

	sleeps: list = []

	async def fake_sleep(s):
		sleeps.append(s)
	monkeypatch.setattr("tree_walker.agent.rerun.asyncio.sleep", fake_sleep)
	await agent.rerun_history(history, delay_between_actions=0, max_step_interval=0,
		summary_llm=_StructuredLLM())
	assert 5.0 not in sleeps                        # 失败不 sleep


@pytest.mark.asyncio
async def test_upload_wait_zero_for_unknown_kind(make_node, monkeypatch):
	"""path 无可识别扩展名 → _upload_file_kind=None → wait_s=0（不睡）。"""
	agent, history = _build_agent_with_upload_step(make_node, video_wait=5.0, path="noext")

	async def fake_execute(name, params, br, st):
		return ActionResult()
	agent.tools.execute = fake_execute

	sleeps: list = []

	async def fake_sleep(s):
		sleeps.append(s)
	monkeypatch.setattr("tree_walker.agent.rerun.asyncio.sleep", fake_sleep)
	await agent.rerun_history(history, delay_between_actions=0, max_step_interval=0,
		summary_llm=_StructuredLLM())
	assert 5.0 not in sleeps                        # 未知类型不等（原 .get(kind,3) 太武断）
