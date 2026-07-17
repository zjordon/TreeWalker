"""recorder.rules 单元测试：Stage3 signal 感知翻译规则。

迁移自旧 ``event_mapper.denoise_steps`` / ``dedupe_uploads`` / ``dedupe_auto_navigates`` 的场景，
行为等价。时间戳单位为秒（对齐 ``ActionRecord.timestamp``）。
"""

from tree_walker.recorder.models import ActionRecord, ElementRef, Signal, SignalKind
from tree_walker.recorder.rules import (
    apply_rules,
    rule_file_upload,
    rule_merge_inputs,
    rule_merge_scrolls,
    rule_navigation_signal,
    rule_redundant_click,
)


def _a(name, params=None, xpath=None, ts=0.0, signals=None, index="__none__"):
    """构造 ActionRecord。index 默认不设；传 int 则填 params['index']。"""
    p = dict(params or {})
    if index != "__none__":
        p["index"] = index
    ref = ElementRef(xpath=xpath) if xpath is not None else None
    return ActionRecord(name, p, ref, ts, list(signals or []))


def _names(actions):
    return [a.action_name for a in actions]


# ── rule_navigation_signal：丢弃自动跳转的 navigate（前置动作副作用）──


def test_nav_signal_drops_navigate_after_upload_within_gap():
    actions = [
        _a("navigate", {"url": "https://x.com/upload", "new_tab": False}, ts=0.0),
        _a("upload_file", {"path": "uploads/v.mp4"}, ts=1.0),
        _a("navigate", {"url": "https://x.com/post", "new_tab": False}, ts=1.9),  # 上传后 0.9s
    ]
    assert _names(rule_navigation_signal(actions)) == ["navigate", "upload_file"]


def test_nav_signal_drops_navigate_after_click_within_gap():
    actions = [
        _a("click", index=5, ts=1000.0),
        _a("navigate", {"url": "https://x.com/after", "new_tab": False}, ts=1000.04),
    ]
    assert _names(rule_navigation_signal(actions)) == ["click"]


def test_nav_signal_keeps_navigate_after_long_gap():
    actions = [
        _a("click", index=5, ts=0.0),
        _a("navigate", {"url": "https://x.com/manual", "new_tab": False}, ts=5.0),  # 间隔 ≥5s
    ]
    assert len(rule_navigation_signal(actions)) == 2


def test_nav_signal_keeps_consecutive_navigates():
    actions = [
        _a("navigate", {"url": "https://x.com/a", "new_tab": False}, ts=0.0),
        _a("navigate", {"url": "https://x.com/b", "new_tab": False}, ts=1.0),
    ]
    assert len(rule_navigation_signal(actions)) == 2


def test_nav_signal_keeps_new_tab_navigate():
    actions = [
        _a("click", index=5, ts=0.0),
        _a("navigate", {"url": "https://x.com/tab", "new_tab": True}, ts=0.5),
    ]
    out = rule_navigation_signal(actions)
    assert len(out) == 2
    assert out[1].params["new_tab"] is True


def test_nav_signal_keeps_first_step_navigate():
    actions = [_a("navigate", {"url": "https://x.com/start", "new_tab": False}, ts=0.0)]
    assert len(rule_navigation_signal(actions)) == 1


def test_nav_signal_attaches_navigation_signal_to_prev():
    actions = [
        _a("click", index=5, ts=1.0),
        _a("navigate", {"url": "https://x.com/after", "new_tab": False}, ts=1.5),
    ]
    out = rule_navigation_signal(actions)
    assert len(out) == 1
    sigs = [s for s in out[0].signals if s.kind == SignalKind.NAVIGATION]
    assert len(sigs) == 1
    assert sigs[0].detail["to_url"] == "https://x.com/after"


# ── rule_file_upload：吸收 fakepath input_text，前置 click 保留 ──


def test_upload_keeps_modal_click_and_absorbs_fakepath():
    """前置 click 带 MODAL_OPENED（编辑器开启器）→ 保留；紧邻 fakepath input_text 吸收。"""
    modal = [Signal(SignalKind.MODAL_OPENED, 0.5, {"selector": ".editor"})]
    actions = [
        _a("navigate", {"url": "https://x.com", "new_tab": False}, ts=0.0),
        _a("click", index=200, ts=1.0, signals=modal),  # 打开编辑器（modal）→ 保留
        _a("input_text", {"text": "C:\\fakepath\\v.mp4", "clear": True}, ts=2.0),  # fakepath 吸收
        _a("upload_file", {"path": "uploads/v.mp4"}, ts=3.0),
    ]
    out = rule_file_upload(actions)
    assert _names(out) == ["navigate", "click", "upload_file"]


def test_upload_keeps_non_fakepath_input_text():
    actions = [
        _a("input_text", {"text": "正常输入", "clear": True}, ts=0.0),
        _a("upload_file", {"path": "uploads/v.mp4"}, ts=1.0),
    ]
    assert len(rule_file_upload(actions)) == 2


def test_upload_drops_trigger_click_keeps_index():
    """前置 click 无 modal（上传触发按钮）→ 吸收（对齐 Playwright/Selenium 不 replay 触发 click）；
    upload 自带 index 保留。"""
    actions = [
        _a("click", index=1293, ts=0.0),
        _a("upload_file", {"path": "uploads/v.mp4", "index": 5000}, ts=1.0),
    ]
    out = rule_file_upload(actions)
    assert _names(out) == ["upload_file"]
    assert out[0].params["index"] == 5000  # upload 自带 index 保留


def test_upload_time_window_breaks_fakepath():
    actions = [
        _a("input_text", {"text": "C:\\fakepath\\v.mp4", "clear": True}, ts=1.0),
        _a("upload_file", {"path": "uploads/v.mp4"}, ts=12.0),  # 间隔 11s > 10s gap
    ]
    assert len(rule_file_upload(actions)) == 2


def test_two_uploads_drop_both_trigger_clicks():
    """两个 upload 各自的紧邻前置触发 click（无 modal）都吸收。"""
    actions = [
        _a("click", index=100, ts=0.0),
        _a("input_text", {"text": "C:\\fakepath\\a.mp4", "clear": True}, ts=0.5),
        _a("upload_file", {"path": "uploads/a.mp4"}, ts=1.0),
        _a("click", index=200, ts=2.0),
        _a("input_text", {"text": "C:\\fakepath\\b.mp4", "clear": True}, ts=2.5),
        _a("upload_file", {"path": "uploads/b.mp4"}, ts=3.0),
    ]
    assert _names(rule_file_upload(actions)) == ["upload_file", "upload_file"]


def test_upload_drops_only_immediate_trigger_click():
    """只吸收 upload 紧邻前置的触发 click；更早的非触发 click（如切 tab）保留。"""
    actions = [
        _a("click", index=10, ts=0.0),   # 切 tab（非紧邻 upload）→ 保留
        _a("click", index=20, ts=1.0),   # 上传触发按钮（紧邻 upload，无 modal）→ 吸收
        _a("upload_file", {"path": "uploads/v.mp4"}, ts=2.0),
    ]
    assert _names(rule_file_upload(actions)) == ["click", "upload_file"]


def test_upload_keeps_click_with_modal_signal():
    """前置 click 带 MODAL_OPENED signal → 明确是编辑器触发器，保留（且 upload 不吸收它）。"""
    modal = [Signal(SignalKind.MODAL_OPENED, 0.1, {"selector": ".editor"})]
    actions = [
        _a("click", index=200, ts=0.0, signals=modal),
        _a("upload_file", {"path": "uploads/cover.png"}, ts=1.0),
    ]
    out = rule_file_upload(actions)
    assert _names(out) == ["click", "upload_file"]
    assert any(s.kind == SignalKind.MODAL_OPENED for s in out[0].signals)


# ── rule_redundant_click：短时同 index click 折叠 ──


def test_click_collapses_rapid_same_index():
    actions = [
        _a("click", index=3, ts=1000.0),
        _a("click", index=3, ts=1000.2),  # 0.2s 内同 index
    ]
    assert len(rule_redundant_click(actions)) == 1


def test_click_keeps_apart_in_time():
    actions = [
        _a("click", index=3, ts=1000.0),
        _a("click", index=3, ts=1001.0),  # 1.0s > 0.5s gap
    ]
    assert len(rule_redundant_click(actions)) == 2


def test_click_keeps_different_index():
    actions = [
        _a("click", index=3, ts=0.0),
        _a("click", index=4, ts=0.1),
    ]
    assert len(rule_redundant_click(actions)) == 2


# ── rule_merge_inputs：相邻同 xpath input 取最终值 ──


def test_merge_inputs_same_xpath():
    actions = [
        _a("input_text", {"text": "a"}, xpath="html/i", ts=0.0),
        _a("input_text", {"text": "ab"}, xpath="html/i", ts=0.1),
    ]
    out = rule_merge_inputs(actions)
    assert len(out) == 1
    assert out[0].params["text"] == "ab"


def test_merge_inputs_different_xpath():
    actions = [
        _a("input_text", {"text": "a"}, xpath="html/i[1]", ts=0.0),
        _a("input_text", {"text": "b"}, xpath="html/i[2]", ts=0.1),
    ]
    assert len(rule_merge_inputs(actions)) == 2


# ── rule_merge_scrolls：同方向求和 clamp ──


def test_scroll_merges_same_direction():
    actions = [
        _a("scroll", {"amount": 2, "direction": "down"}, ts=0.0),
        _a("scroll", {"amount": 3, "direction": "down"}, ts=0.1),
    ]
    out = rule_merge_scrolls(actions)
    assert len(out) == 1
    assert out[0].params["amount"] == 5


def test_scroll_clamps_at_10():
    actions = [
        _a("scroll", {"amount": 7, "direction": "down"}, ts=0.0),
        _a("scroll", {"amount": 8, "direction": "down"}, ts=0.1),
    ]
    out = rule_merge_scrolls(actions)
    assert len(out) == 1
    assert out[0].params["amount"] == 10


def test_scroll_keeps_opposite_direction():
    actions = [
        _a("scroll", {"amount": 2, "direction": "down"}, ts=0.0),
        _a("scroll", {"amount": 2, "direction": "up"}, ts=0.1),
    ]
    assert len(rule_merge_scrolls(actions)) == 2


# ── apply_rules：完整管线（规则串接）──


def test_apply_rules_runs_all_rules():
    # navigate 副作用丢弃 + upload fakepath 吸收 + click 折叠
    actions = [
        _a("click", index=3, ts=0.0),
        _a("click", index=3, ts=0.2),  # 折叠
        _a("input_text", {"text": "C:\\fakepath\\v.mp4", "clear": True}, ts=1.0),
        _a("upload_file", {"path": "uploads/v.mp4"}, ts=2.0),
        _a("navigate", {"url": "https://x.com/post", "new_tab": False}, ts=2.5),  # upload 副作用，丢
    ]
    out = apply_rules(actions)
    assert _names(out) == ["click", "upload_file"]
