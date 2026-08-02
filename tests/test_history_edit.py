"""P4 可视化编辑：AgentHistoryList mutation API + 变量源合并单测。

覆盖：remove_step / update_action_params / merge_steps（actions↔interacted_element 等长不变量，
含 interacted_element=None 兜底）、manual_variables 增删、merge_variable_sources（detect ∪ manual，
manual 覆盖同名）、save/load 往返（含老 JSON 缺 manual_variables 兼容）、manual 标注在
_substitute_variables_in_history 生效（绕过"整串匹配漏子串"盲区）。
设计见 docs/p4/01-可视化编辑与CSV批量执行方案.md（D2/D3）。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tree_walker.agent.rerun import RerunMixin
from tree_walker.agent.variable_detector import merge_variable_sources
from tree_walker.agent.views import (
    AgentHistory,
    AgentHistoryList,
    DetectedVariable,
    ManualVariableBinding,
)


def _make_history() -> AgentHistoryList:
    """两步历史：step1 单 input(email)，step2 click + input(搜索词)。"""
    return AgentHistoryList(
        history=[
            AgentHistory(
                step_number=1,
                model_output={"actions": [
                    {"name": "input_text", "params": {"text": "alice@example.com", "index": 0}},
                ]},
                result=[],
                interacted_element=[{"node_name": "INPUT", "attributes": {"type": "email"}}],
            ),
            AgentHistory(
                step_number=2,
                model_output={"actions": [
                    {"name": "click", "params": {"index": 1}},
                    {"name": "input_text", "params": {"text": "iPhone", "index": 2}},
                ]},
                result=[],
                interacted_element=[
                    {"node_name": "BUTTON"},
                    {"node_name": "INPUT", "attributes": {"name": "q"}},
                ],
            ),
        ]
    )


# ── mutation API ────────────────────────────────────────────────────────


def test_remove_step():
    h = _make_history()
    h.remove_step(1)
    assert [s.step_number for s in h.history] == [2]


def test_remove_step_not_found():
    h = _make_history()
    with pytest.raises(KeyError):
        h.remove_step(99)


def test_update_action_params():
    h = _make_history()
    h.update_action_params(1, 0, "text", "bob@mail.com")
    assert h.history[0].model_output["actions"][0]["params"]["text"] == "bob@mail.com"


def test_update_action_params_out_of_range():
    h = _make_history()
    with pytest.raises(IndexError):
        h.update_action_params(1, 99, "text", "x")


def test_merge_steps_maintains_pairing():
    h = _make_history()
    h.merge_steps(1, 2)
    assert len(h.history) == 1
    actions = h.history[0].model_output["actions"]
    ie = h.history[0].interacted_element
    assert len(actions) == 3                       # 1 (step1) + 2 (step2)
    assert len(ie) == 3                            # 等长不变量
    assert ie[2]["attributes"]["name"] == "q"      # step2 第二个元素就位


def test_merge_steps_none_interacted_pads():
    """interacted_element=None 的步合并：补 [None]*len 后再拼接，维持等长。"""
    h = AgentHistoryList(
        history=[
            AgentHistory(step_number=1, model_output={"actions": [
                {"name": "click", "params": {"index": 0}},
            ]}, result=[], interacted_element=None),
            AgentHistory(step_number=2, model_output={"actions": [
                {"name": "click", "params": {"index": 1}},
            ]}, result=[], interacted_element=[{"node_name": "BUTTON"}]),
        ]
    )
    h.merge_steps(1, 2)
    ie = h.history[0].interacted_element
    actions = h.history[0].model_output["actions"]
    assert len(actions) == 2
    assert len(ie) == 2                            # None 补 [None] + [BUTTON]
    assert ie[0] is None
    assert ie[1]["node_name"] == "BUTTON"


def test_merge_steps_same_raises():
    h = _make_history()
    with pytest.raises(ValueError):
        h.merge_steps(1, 1)


def test_add_manual_variable_same_name_replaces():
    h = _make_history()
    h.add_manual_variable(ManualVariableBinding(name="product", step_number=2, action_index=1, original_value="iPhone"))
    h.add_manual_variable(ManualVariableBinding(name="product", step_number=2, action_index=1, original_value="MacBook"))
    assert len(h.manual_variables) == 1
    assert h.manual_variables[0].original_value == "MacBook"


def test_remove_manual_variable():
    h = _make_history()
    h.add_manual_variable(ManualVariableBinding(name="product", step_number=2, action_index=1, original_value="iPhone"))
    h.remove_manual_variable("product")
    assert h.manual_variables == []


# ── merge_variable_sources ──────────────────────────────────────────────


def test_merge_sources_detect_plus_manual():
    detected = {"email": DetectedVariable(name="email", original_value="a@b.com", format="email")}
    manual = [ManualVariableBinding(name="product", step_number=2, action_index=1, original_value="iPhone")]
    merged = merge_variable_sources(detected, manual)
    assert set(merged) == {"email", "product"}
    assert merged["product"].original_value == "iPhone"


def test_merge_sources_manual_overrides_detect():
    detected = {"x": DetectedVariable(name="x", original_value="old")}
    manual = [ManualVariableBinding(name="x", step_number=1, action_index=0, original_value="new")]
    merged = merge_variable_sources(detected, manual)
    assert merged["x"].original_value == "new"     # manual 覆盖同名 detect


# ── 序列化往返 ──────────────────────────────────────────────────────────


def test_save_load_roundtrip_manual(tmp_path):
    h = _make_history()
    h.add_manual_variable(ManualVariableBinding(name="product", step_number=2, action_index=1, original_value="iPhone"))
    p = tmp_path / "h.json"
    h.save_to_file(p)
    loaded = AgentHistoryList.load_from_file(p)
    assert len(loaded.manual_variables) == 1
    assert loaded.manual_variables[0].name == "product"
    assert loaded.manual_variables[0].original_value == "iPhone"


def test_load_old_json_no_manual_defaults_empty(tmp_path):
    p = tmp_path / "old.json"
    p.write_text(
        json.dumps(
            {"history": [{"step_number": 1, "model_output": {"actions": []}, "result": []}],
             "action_registry_version": "x"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    loaded = AgentHistoryList.load_from_file(p)
    assert loaded.manual_variables == []


# ── 集成：manual 标注在替换时生效 ────────────────────────────────────────


def test_substitute_uses_manual_variable():
    """iPhone 无规则特征、detect 检测不到；manual 标注 product=iPhone 后应能替换。

    _substitute_variables_in_history 不读 self 属性（logger 为模块级），SimpleNamespace 即可调用。
    """
    h = AgentHistoryList(
        history=[
            AgentHistory(
                step_number=1,
                model_output={"actions": [
                    {"name": "input_text", "params": {"text": "iPhone", "index": 0}},
                ]},
                result=[],
                interacted_element=[{"node_name": "INPUT", "attributes": {"name": "q"}}],
            ),
        ]
    )
    h.add_manual_variable(ManualVariableBinding(name="product", step_number=1, action_index=0, original_value="iPhone"))
    modified = RerunMixin._substitute_variables_in_history(SimpleNamespace(), h, {"product": "MacBook"})
    assert modified.history[0].model_output["actions"][0]["params"]["text"] == "MacBook"
    # 原对象未被改（deepcopy）
    assert h.history[0].model_output["actions"][0]["params"]["text"] == "iPhone"
