"""recorder.models 单元测试：内部模型构造 + 事件/信号 payload 解析。"""

from tree_walker.recorder.models import (
    ActionRecord,
    ElementRef,
    Recording,
    RecordingState,
    Signal,
    SignalKind,
    element_ref_from_event,
    signal_from_payload,
)


def test_signal_kind_is_str_enum():
    # str Enum：可直接与扩展发来的字符串比较 / 构造
    assert SignalKind("modal_opened") is SignalKind.MODAL_OPENED
    assert SignalKind.MODAL_OPENED == "modal_opened"


def test_signal_construction_defaults():
    s = Signal(kind=SignalKind.NAVIGATION, timestamp=1.5)
    assert s.detail == {}
    assert s.source_action_ts is None


def test_element_ref_to_ref_dict_maps_aria_label():
    ref = ElementRef(xpath="html/x", tag="input", id="i", name="n", aria_label="lbl", role="textbox", rect={"x": 1})
    d = ref.to_ref_dict()
    # aria_label → ariaLabel（locator.locate_by_ref 期望的键名）
    assert d["ariaLabel"] == "lbl"
    assert d["xpath"] == "html/x"
    assert d["tag"] == "input"
    assert d["id"] == "i"
    assert d["name"] == "n"
    assert d["role"] == "textbox"
    assert d["rect"] == {"x": 1}


def test_element_ref_from_event_maps_fields():
    # 扩展事件用 ariaLabel（驼峰）；ElementRef 存 aria_label（下划线）
    ref = element_ref_from_event({
        "xpath": "html/body/a", "tag": "a", "id": "aid", "name": "aname",
        "ariaLabel": "链接", "role": "link", "rect": {"x": 0, "y": 0, "width": 1, "height": 1},
    })
    assert ref.tag == "a"
    assert ref.aria_label == "链接"
    assert ref.xpath == "html/body/a"


def test_element_ref_from_event_missing_fields():
    ref = element_ref_from_event({"type": "navigate"})
    assert ref.xpath is None
    assert ref.tag is None


def test_action_record_defaults():
    a = ActionRecord(action_name="click", params={}, element_ref=None, timestamp=1.0)
    assert a.signals == []
    assert a.interacted_element is None
    assert a.page_url == ""
    assert a.page_title == ""


def test_recording_defaults_empty():
    r = Recording()
    assert r.actions == []
    assert isinstance(r.state, RecordingState)
    assert r.state.focus_target_xpath is None
    assert r.state.pending_modal is None


def test_signal_from_payload_modal():
    sig = signal_from_payload({"type": "modal_opened", "selector": ".dialog", "ts": 2000})
    assert sig.kind is SignalKind.MODAL_OPENED
    assert sig.detail == {"selector": ".dialog"}
    assert sig.timestamp == 2.0  # ms → s


def test_signal_from_payload_dropdown():
    sig = signal_from_payload({"type": "dropdown_opened", "selector": ".list", "ts": 1500})
    assert sig.kind is SignalKind.DROPDOWN_OPENED
    assert sig.timestamp == 1.5


def test_signal_from_payload_unknown_type_returns_none():
    assert signal_from_payload({"type": "mystery", "ts": 100}) is None


def test_signal_from_payload_missing_ts():
    sig = signal_from_payload({"type": "modal_opened", "selector": ".d"})
    assert sig.timestamp == 0.0
