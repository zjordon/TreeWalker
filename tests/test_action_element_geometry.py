"""Tests for ToolCallEvent element-highlight geometry (P6 后续 I3).

Covers the module-level helpers in ``agent.step``:
- ``_normalize_bbox``: DOMRect dict + viewport → 归一化 [0,1]
- ``_action_element_geometry``: action params + dom_state + viewport → (index, bbox, xpath)

The bbox lookup primitive (``selector_map.get(idx)`` → ``DOMInteractedElement``) is reused
from the actionability path; here we test the extraction/normalization with fakes + monkeypatch.
"""
from __future__ import annotations

from types import SimpleNamespace

from tree_walker.agent.step import _action_element_geometry, _normalize_bbox


def test_normalize_bbox_basic():
    bbox = _normalize_bbox({"x": 100, "y": 50, "width": 200, "height": 100}, (1000, 500))
    assert bbox == {"left": 0.1, "top": 0.1, "width": 0.2, "height": 0.2}


def test_normalize_bbox_none_when_no_viewport():
    assert _normalize_bbox({"x": 1, "y": 1, "width": 1, "height": 1}, None) is None
    assert _normalize_bbox(None, (1000, 500)) is None


def test_normalize_bbox_clamps_to_unit():
    # 负坐标 / 超出视口 → clamp 到 [0,1]
    bbox = _normalize_bbox({"x": -50, "y": 0, "width": 2000, "height": 10}, (100, 100))
    assert bbox["left"] == 0.0
    assert bbox["width"] == 1.0


def test_action_geometry_no_index_action():
    # 非 index 类动作（如 send_keys 全局）→ 全 None
    idx, bbox, xp = _action_element_geometry({"keys": "Enter"}, None, (1000, 500))
    assert (idx, bbox, xp) == (None, None, None)


def test_action_geometry_missing_node():
    # index 有但 selector_map 无该 index → 返回 index、无 bbox/xpath
    dom_state = SimpleNamespace(selector_map={})
    idx, bbox, xp = _action_element_geometry({"index": 5}, dom_state, (1000, 500))
    assert idx == 5 and bbox is None and xp is None


def test_action_geometry_with_node(monkeypatch):
    from tree_walker.agent import step as step_mod

    fake_diel = SimpleNamespace(
        bounds=SimpleNamespace(to_dict=lambda: {"x": 100, "y": 50, "width": 200, "height": 100}),
        x_path="/html/body/div",
    )
    monkeypatch.setattr(
        step_mod.DOMInteractedElement, "load_from_enhanced_dom_tree", lambda node: fake_diel)
    dom_state = SimpleNamespace(selector_map={3: "node"})
    idx, bbox, xp = _action_element_geometry({"index": 3}, dom_state, (1000, 500))
    assert idx == 3
    assert bbox == {"left": 0.1, "top": 0.1, "width": 0.2, "height": 0.2}
    assert xp == "/html/body/div"


def test_action_geometry_no_viewport_suppresses_bbox(monkeypatch):
    # 有 node 但 viewport=None（CDP 失败）→ bbox None；index/xpath 仍给
    from tree_walker.agent import step as step_mod

    fake_diel = SimpleNamespace(
        bounds=SimpleNamespace(to_dict=lambda: {"x": 1, "y": 1, "width": 1, "height": 1}),
        x_path="/x",
    )
    monkeypatch.setattr(
        step_mod.DOMInteractedElement, "load_from_enhanced_dom_tree", lambda node: fake_diel)
    dom_state = SimpleNamespace(selector_map={3: "n"})
    idx, bbox, xp = _action_element_geometry({"index": 3}, dom_state, None)
    assert idx == 3 and bbox is None and xp == "/x"


def test_action_geometry_none_dom_state():
    # dom_state=None → 返回 index、无 bbox/xpath
    idx, bbox, xp = _action_element_geometry({"index": 1}, None, (1000, 500))
    assert idx == 1 and bbox is None and xp is None
