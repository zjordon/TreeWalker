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
from tree_walker.agent.variable_detector import detect_variables_in_history
from tree_walker.agent.views import (
    AgentHistory,
    AgentHistoryList,
    ActionResult,
    DetectedVariable,
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


# ── 步间延迟 / 跳过重试辅助 ─────────────────────────────────────────────


def _item(step_interval=None, step_number=1):
    return AgentHistory(
        step_number=step_number, model_output={"action": {}}, result=[],
        metadata=StepMetadata(step_start_time=0.0, step_end_time=1.0, step_number=step_number,
                              step_interval=step_interval),
    )


def test_compute_step_delay_caps_interval():
    rm = RerunMixin()
    assert rm._compute_step_delay(_item(step_interval=30.0), 2.0, 5.0) == 5.0    # 封顶
    assert rm._compute_step_delay(_item(step_interval=3.0), 2.0, 5.0) == 3.0     # 未达上限
    assert rm._compute_step_delay(_item(step_interval=None), 2.0, 5.0) == 2.0    # 兜底


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
