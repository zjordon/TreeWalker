"""用户操作录制 → 历史重放。

Chrome 扩展采集用户真实操作（事件 + 元素 xpath 线索 + modal/dropdown 副作用信号），本后端
通过 remote-debugging-port 直连浏览器 CDP，复用 ``_build_enhanced_dom_tree`` +
``compute_stable_hash`` 算指纹，经翻译管线（``Recording`` 内部模型）落盘到 ``rerun-history/``，
供现有 ``load_and_rerun`` 重放。

架构：事件 → ``translation.translate_event``（Stage1 映射+聚合）→ 实时 locate/指纹 →
``Recording.actions`` → ``rules.apply_rules``（Stage3 signal 感知去噪）→ ``flatten`` →
``AgentHistoryList``。详见 ``docs/user_recording/README.md`` / ``redesign-impl-plan.md``。
"""

from tree_walker.recorder.event_mapper import map_event, needs_target
from tree_walker.recorder.flatten import flatten
from tree_walker.recorder.locator import locate_by_ref, locate_by_xpath, normalize_xpath
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
from tree_walker.recorder.rules import apply_rules
from tree_walker.recorder.translation import aggregates_input, translate_event, update_state

__all__ = [
	"ActionRecord",
	"ElementRef",
	"Recording",
	"RecordingState",
	"Signal",
	"SignalKind",
	"aggregates_input",
	"apply_rules",
	"element_ref_from_event",
	"flatten",
	"locate_by_ref",
	"locate_by_xpath",
	"map_event",
	"needs_target",
	"normalize_xpath",
	"signal_from_payload",
	"translate_event",
	"update_state",
]
