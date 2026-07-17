"""recorder.flatten 单元测试：Recording → AgentHistoryList 纯 reshape。"""

from tree_walker.recorder.flatten import flatten
from tree_walker.recorder.models import ActionRecord, Recording


def _action(name, params=None, interacted=None, ts=1.0, url="https://x.com", title="X"):
    return ActionRecord(
        action_name=name,
        params=dict(params or {}),
        element_ref=None,
        timestamp=ts,
        signals=[],
        interacted_element=interacted,
        page_url=url,
        page_title=title,
    )


def test_flatten_empty():
    out = flatten(Recording())
    assert out.history == []


def test_flatten_renumbers_step_numbers():
    rec = Recording(actions=[
        _action("navigate", {"url": "https://x.com", "new_tab": False}, interacted=[], ts=1.0),
        _action("click", {"index": 5}, interacted=[{"element_hash": 123}], ts=2.0),
        _action("input_text", {"text": "ab", "clear": True, "index": 9}, interacted=[{"element_hash": 999}], ts=3.0),
    ])
    out = flatten(rec)
    assert [s.step_number for s in out.history] == [0, 1, 2]
    assert all(s.metadata.step_number == s.step_number for s in out.history)


def test_flatten_interacted_proj_passthrough():
    rec = Recording(actions=[_action("click", {"index": 5}, interacted=[{"element_hash": 123, "stable_hash": 456}])])
    out = flatten(rec)
    assert out.history[0].interacted_element == [{"element_hash": 123, "stable_hash": 456}]


def test_flatten_locate_failure_keeps_none_interacted():
    # 定位失败 → interacted=[None]，flatten 透传（[None] 是 truthy）
    rec = Recording(actions=[_action("click", {}, interacted=[None])])
    out = flatten(rec)
    assert out.history[0].interacted_element == [None]


def test_flatten_non_target_interacted_becomes_none():
    # 无 target 动作（navigate/scroll）→ interacted=[] → flatten 归一为 None
    rec = Recording(actions=[_action("navigate", {"url": "https://x.com", "new_tab": False}, interacted=[])])
    out = flatten(rec)
    assert out.history[0].interacted_element is None


def test_flatten_model_output_params():
    rec = Recording(actions=[_action("click", {"index": 5}, interacted=[{"element_hash": 1}])])
    out = flatten(rec)
    action = out.history[0].model_output["actions"][0]
    assert action["name"] == "click"
    assert action["params"] == {"index": 5}


def test_flatten_state_summary_and_metadata():
    rec = Recording(actions=[_action("click", {"index": 5}, ts=42.5, url="https://y.com", title="Y")])
    out = flatten(rec)
    s = out.history[0]
    assert s.state_summary["url"] == "https://y.com"
    assert s.state_summary["title"] == "Y"
    assert s.state_summary["duration"] == 0.0
    assert s.metadata.step_start_time == 42.5
    assert s.metadata.step_end_time == 42.5
