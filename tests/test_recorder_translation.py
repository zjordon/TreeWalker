"""recorder.translation 单元测试：Stage1 映射 + 聚合 + Stage4 状态机。"""

from tree_walker.recorder.models import ActionRecord, ElementRef, Recording, Signal, SignalKind
from tree_walker.recorder.translation import aggregates_input, translate_event, update_state


def _input(xpath, text, ts):
    return {"type": "input_text", "xpath": xpath, "params": {"text": text, "clear": True}, "ts": ts}


# ── translate_event：事件 → ActionRecord（追加进 recording）──


def test_translate_click_appends_and_returns():
    rec = Recording()
    action = translate_event({"type": "click", "xpath": "html/btn", "ts": 1000}, rec)
    assert action is not None
    assert action.action_name == "click"
    assert action.element_ref.xpath == "html/btn"
    assert action.timestamp == 1.0  # ms → s
    assert rec.actions == [action]  # 已追加


def test_translate_input_text_params():
    rec = Recording()
    action = translate_event(_input("html/inp", "abc", 1000), rec)
    assert action.params == {"text": "abc", "clear": True}


def test_translate_navigate_no_xpath_needed():
    rec = Recording()
    action = translate_event({"type": "navigate", "params": {"url": "https://x.com"}, "ts": 1000}, rec)
    assert action.action_name == "navigate"
    assert action.params["url"] == "https://x.com"


def test_translate_unmappable_returns_none_no_append():
    rec = Recording()
    assert translate_event({"type": "hover", "xpath": "html/x"}, rec) is None
    assert rec.actions == []


# ── Stage1 聚合：连续同 xpath input_text 取最终值 ──


def test_aggregates_consecutive_same_xpath_input():
    rec = Recording()
    a1 = translate_event(_input("html/inp", "a", 1000), rec)
    a2 = translate_event(_input("html/inp", "abc", 1100), rec)
    assert a1 is not None
    assert a2 is None  # 聚合进 a1，不新增
    assert len(rec.actions) == 1
    assert rec.actions[0].params["text"] == "abc"  # 取最终值


def test_aggregates_keeps_different_xpath_separate():
    rec = Recording()
    translate_event(_input("html/inp[1]", "a", 1000), rec)
    translate_event(_input("html/inp[2]", "b", 1100), rec)
    assert len(rec.actions) == 2


def test_aggregates_gap_breaks_merge():
    rec = Recording()
    translate_event(_input("html/inp", "a", 1000), rec)
    # 间隔 2s（> 1.5s gap）→ 不聚合
    a2 = translate_event(_input("html/inp", "b", 3000), rec)
    assert a2 is not None
    assert len(rec.actions) == 2


def test_aggregates_does_not_merge_across_other_action():
    rec = Recording()
    translate_event(_input("html/inp", "a", 1000), rec)
    translate_event({"type": "click", "xpath": "html/btn", "ts": 1100}, rec)
    a3 = translate_event(_input("html/inp", "b", 1200), rec)
    assert a3 is not None  # click 隔开，不聚合
    assert len(rec.actions) == 3


def test_aggregates_input_helper_direct():
    a = ActionRecord("input_text", {"text": "a"}, ElementRef(xpath="html/i"), 1.0)
    b = ActionRecord("input_text", {"text": "b"}, ElementRef(xpath="html/i"), 1.1)
    c = ActionRecord("input_text", {"text": "c"}, ElementRef(xpath="html/j"), 1.2)
    d = ActionRecord("click", {}, ElementRef(xpath="html/i"), 1.3)
    assert aggregates_input(b, a) is True
    assert aggregates_input(c, a) is False  # 不同 xpath
    assert aggregates_input(d, a) is False  # 非 input_text


# ── Stage4 update_state：focus / pending_modal ──


def test_update_state_input_sets_focus():
    st = Recording().state
    update_state(st, ActionRecord("input_text", {"text": "hi"}, ElementRef(xpath="html/inp"), 1.0))
    assert st.focus_target_xpath == "html/inp"
    assert st.focus_value == "hi"
    assert st.last_action_ts == 1.0


def test_update_state_click_other_element_clears_focus():
    st = Recording().state
    update_state(st, ActionRecord("input_text", {"text": "x"}, ElementRef(xpath="html/inp"), 1.0))
    update_state(st, ActionRecord("click", {}, ElementRef(xpath="html/btn"), 2.0))
    assert st.focus_target_xpath is None  # 点的不是 focus 元素 → 失焦
    assert st.focus_value is None


def test_update_state_click_focus_element_keeps_focus():
    st = Recording().state
    update_state(st, ActionRecord("input_text", {"text": "x"}, ElementRef(xpath="html/inp"), 1.0))
    update_state(st, ActionRecord("click", {}, ElementRef(xpath="html/inp"), 2.0))  # 点 focus 本身
    assert st.focus_target_xpath == "html/inp"


def test_update_state_modal_signal_sets_pending_modal():
    st = Recording().state
    action = ActionRecord("click", {}, ElementRef(xpath="html/cover"), 1.0)
    action.signals.append(Signal(SignalKind.MODAL_OPENED, 1.1, {"selector": ".editor"}))
    update_state(st, action)
    assert st.pending_modal == ".editor"
