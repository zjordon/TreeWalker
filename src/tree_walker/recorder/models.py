"""录制内部模型：ActionRecord / Signal / Recording。

录制器收到扩展事件后，不再立即拼 ``AgentHistory``，而是先映射成 ``ActionRecord`` 累积进
``Recording``（承载 signal + 状态机字段），落盘时再 ``flatten`` 成 ``AgentHistoryList``。
这样去噪在「带完整上下文（signal）的 ActionRecord 列表」上做，而非「上下文已丢失的成品
steps」上事后猜。设计见 ``docs/user_recording/redesign.md`` / ``redesign-impl-plan.md``。

定位 + 指纹投影仍在 ``Recorder.handle_event`` 事件到达时**实时做**（modal 打开时 DOM 是活的，
stop 时再 locate 会因 modal 已关而失败），结果存进 ``ActionRecord.interacted_element`` 与
``params['index']``；``flatten`` 退化为纯 reshape，不需要 browser。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SignalKind(str, Enum):
	"""动作的副作用信号种类（附加到 ``ActionRecord.signals``，不独立成动作）。

	str Enum：扩展端发来的信号 payload 里 ``type`` 是字符串（``'modal_opened'`` 等），
	str Enum 可直接与字符串比较 / 由字符串构造，省一层转换。
	"""

	NAVIGATION = "navigation"
	MODAL_OPENED = "modal_opened"
	DROPDOWN_OPENED = "dropdown_opened"
	DIALOG = "dialog"
	DOWNLOAD = "download"


@dataclass
class Signal:
	"""动作触发的副作用信号（事后检测，附加到触发动作）。

	对应 Playwright 的 Signal 概念：导航/弹窗/下载等副作用不独立成动作，而是附加到最近
	触发动作。``rule_file_upload`` 据 ``MODAL_OPENED`` signal 判「前置 click 打开了编辑器，
	绝非上传按钮」→ 不吸收。
	"""

	kind: SignalKind
	timestamp: float
	detail: dict[str, Any] = field(default_factory=dict)
	# 关联到的触发动作时间戳（回溯填充；导航信号由 rule 附加到前一步时填前一步 ts）
	source_action_ts: float | None = None


@dataclass
class ElementRef:
	"""录制瞬间的元素定位线索。

	扩展 content script 发的原始属性（xpath/tag/name/id/aria-label/rect）。后端 ``locator``
	用其在当前页 ``selector_map`` 定位节点（XPATH 级 + ATTRIBUTE 级兜底）。定位完成后此对象
	不再使用——跨会话稳定性交给后端算的指纹（``element_hash``/``stable_hash``）。
	"""

	xpath: str | None = None
	tag: str | None = None
	id: str | None = None
	name: str | None = None
	aria_label: str | None = None
	role: str | None = None
	rect: dict[str, Any] | None = None

	def to_ref_dict(self) -> dict[str, Any]:
		"""转成 ``locator.locate_by_ref`` 期望的 dict（``aria_label`` → ``ariaLabel``）。"""
		return {
			"xpath": self.xpath,
			"rect": self.rect,
			"tag": self.tag,
			"id": self.id,
			"name": self.name,
			"ariaLabel": self.aria_label,
			"role": self.role,
		}


@dataclass
class ActionRecord:
	"""一个语义动作（映射自一条扩展事件，含实时定位结果与 signal）。

	``params`` 含**实时 resolve 的 index / upload path / tab_id**（``handle_event`` 事件到达
	时定位填入）；``interacted_element`` 是**实时指纹投影**（``DOMInteractedElement.to_dict()``
	产物，modal 打开时 DOM 活着才能算准）。``flatten`` 直接透传这两者，不再二次定位。
	"""

	action_name: str
	params: dict[str, Any]
	element_ref: ElementRef | None
	timestamp: float
	signals: list[Signal] = field(default_factory=list)
	# 实时指纹投影（与 model_output 的 actions 等长、按位对应；无 index 动作为 None/空）
	interacted_element: list[dict[str, Any] | None] | None = None
	page_url: str = ""
	page_title: str = ""
	# 诊断：该步实时 locate 失败（三级都未命中）时，记录事件线索 + selector_map 规模，
	# flatten 时写入 state_summary['_locate_miss'] 便于事后分析「为何录制无指纹」（重放端忽略）。
	locate_miss: dict[str, Any] | None = None


@dataclass
class RecordingState:
	"""跨事件保持的状态机字段（Selenium IDE 式，供翻译规则判断意图）。

	- ``focus_target_xpath`` / ``focus_value``：最近输入框及其值，用于「连续同框输入聚合」。
	- ``pending_modal``：最近打开的 modal 选择器（有 modal_opened signal 时设）。
	"""

	focus_target_xpath: str | None = None
	focus_value: str | None = None
	pending_modal: str | None = None
	last_action_ts: float | None = None


@dataclass
class Recording:
	"""整次录制的内部表示（落盘前）。"""

	actions: list[ActionRecord] = field(default_factory=list)
	state: RecordingState = field(default_factory=RecordingState)


def element_ref_from_event(event: dict[str, Any]) -> ElementRef:
	"""从扩展事件 dict 抽 ``ElementRef``（字段名映射 ``ariaLabel`` → ``aria_label``）。"""
	return ElementRef(
		xpath=event.get("xpath"),
		tag=event.get("tag"),
		id=event.get("id"),
		name=event.get("name"),
		aria_label=event.get("ariaLabel"),
		role=event.get("role"),
		rect=event.get("rect"),
	)


def signal_from_payload(payload: dict[str, Any]) -> Signal | None:
	"""从扩展 SideEffectObserver 的信号 payload 构造 ``Signal``。

	payload 形如 ``{"type": "modal_opened", "selector": "...", "ts": 1234}``。未知类型返 None。
	"""
	kind_str = payload.get("type")
	try:
		kind = SignalKind(kind_str)
	except ValueError:
		return None
	detail: dict[str, Any] = {}
	if payload.get("selector"):
		detail["selector"] = payload["selector"]
	if payload.get("to_url"):
		detail["to_url"] = payload["to_url"]
	# 扩展 ts 是 Date.now()（毫秒）；统一秒口径，与 ActionRecord.timestamp 可比。
	raw_ts = payload.get("ts")
	try:
		ts = float(raw_ts) / 1000.0 if raw_ts else 0.0
	except (TypeError, ValueError):
		ts = 0.0
	return Signal(
		kind=kind,
		timestamp=ts,
		detail=detail,
		source_action_ts=payload.get("source_action_ts"),
	)
