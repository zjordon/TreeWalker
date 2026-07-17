"""Stage 1 事件→动作映射 + Stage 4 状态机更新。

``translate_event`` 是录制管线的入口：扩展事件 → ``ActionRecord``。它做三件事：

1. **纯映射**（调 ``event_mapper.map_event``）：事件类型 → action 名 + params（不含 index）。
2. **聚合判定**：连续同 xpath 的 ``input_text`` 视为一次输入，取最终值并入前一个动作
   （返回 None 让 recorder 不追加）——替代旧 ``coalesce_inputs`` + ``denoise_steps`` 的 input 合并。
3. **状态机更新**（``update_state``）：维护 focus / pending_modal，供翻译规则判断意图。

**不做**定位/指纹——那在 ``Recorder.handle_event`` 实时做（modal DOM 活着时才能算准），
结果回填到返回的 ``ActionRecord``。

设计见 ``docs/user_recording/redesign.md`` §3.3 Stage 1/4。
"""

from __future__ import annotations

from typing import Any

from tree_walker.recorder.event_mapper import map_event
from tree_walker.recorder.models import (
	ActionRecord,
	ElementRef,
	Recording,
	RecordingState,
	SignalKind,
	element_ref_from_event,
)


def translate_event(event: dict[str, Any], recording: Recording) -> ActionRecord | None:
	"""Stage 1 + Stage 4：扩展事件 → ``ActionRecord``（含聚合判定与状态机更新）。

	成功映射的新动作会**追加进 ``recording.actions``** 并返回（recorder 据此实时 locate 填
	index/指纹——返回的就是 ``recording.actions[-1]``，原地改即生效）。``None`` 表示不可映射，
	或已聚合进 ``recording.actions[-1]``（已在此处把 last.params 取最终值，不新增）。

	聚合仅对 ``input_text``：同 xpath 连续输入取最终值（人类敲键的中间值重放不需要）。
	扩展端已 400ms 合并一次，这里是跨 burst 的安全网。
	"""
	mapped = map_event(event)
	if mapped is None:
		return None
	action_name, params = mapped
	# 扩展 ts 是 Date.now()（毫秒 epoch）；统一换算成秒，对齐规则阈值（秒）与旧
	# denoise_steps 用 time.time()（秒）的计时口径。缺失 ts → 0.0（规则保守处理）。
	ts = _ts_seconds(event.get("ts"))
	ref = element_ref_from_event(event)
	action = ActionRecord(
		action_name=action_name,
		params=dict(params),
		element_ref=ref,
		timestamp=ts,
	)

	# Stage 1 聚合：连续同 xpath input_text → 取最终值并入 last，不新增动作
	last = recording.actions[-1] if recording.actions else None
	if last is not None and _aggregates_input(action, last):
		last.params["text"] = action.params.get("text", last.params.get("text", ""))
		_update_state(recording.state, action)
		return None

	recording.actions.append(action)
	_update_state(recording.state, action)
	return action


def update_state(state: RecordingState, action: ActionRecord) -> None:
	"""Stage 4：根据动作更新状态机字段（focus / pending_modal / last_action_ts）。

	- ``input_text``：设 focus_target_xpath + focus_value。
	- ``click``：清 focus（除非点的是 focus 元素本身——如点输入框内不丢焦点）。
	- 有 ``MODAL_OPENED`` signal：设 pending_modal（异步 signal 多在 attach_signal 时另更新）。
	"""
	_update_state(state, action)


def _update_state(state: RecordingState, action: ActionRecord) -> None:
	state.last_action_ts = action.timestamp
	if action.action_name == "input_text" and action.element_ref is not None:
		state.focus_target_xpath = action.element_ref.xpath
		state.focus_value = action.params.get("text")
		return
	if action.action_name == "click":
		click_xpath = action.element_ref.xpath if action.element_ref is not None else None
		# 点的不是当前 focus 元素 → 失焦
		if state.focus_target_xpath != click_xpath:
			state.focus_target_xpath = None
			state.focus_value = None
	modal_signals = [s for s in action.signals if s.kind == SignalKind.MODAL_OPENED]
	if modal_signals:
		state.pending_modal = modal_signals[-1].detail.get("selector")


def aggregates_input(new: ActionRecord, last: ActionRecord, gap_s: float = 1.5) -> bool:
	"""``new`` 是否聚合进 ``last``：同为 ``input_text``、同 xpath、时间在 ``gap_s`` 内。

	时间戳缺失时保守判为可合并（对齐旧 ``_within_gap`` 的 None 行为）。
	"""
	return _aggregates_input(new, last, gap_s)


def _aggregates_input(new: ActionRecord, last: ActionRecord, gap_s: float = 1.5) -> bool:
	if new.action_name != "input_text" or last.action_name != "input_text":
		return False
	new_xpath = new.element_ref.xpath if new.element_ref is not None else None
	last_xpath = last.element_ref.xpath if last.element_ref is not None else None
	if not new_xpath or new_xpath != last_xpath:
		return False
	ta, tb = last.timestamp, new.timestamp
	if ta <= 0 or tb <= 0:
		return True  # 缺时间戳，保守可合并
	return abs(tb - ta) <= gap_s


def _ts_seconds(raw: Any) -> float:
	"""扩展事件 ts（Date.now()，毫秒 epoch）→ 秒；缺失/非法返 0.0。

	统一秒口径：``ActionRecord.timestamp`` 与所有规则阈值（``_NAV_GAP_S`` 等）都用秒，
	复刻旧 ``denoise_steps`` 用 ``time.time()``（秒）的计时口径。0.0 表示缺失，规则保守处理。
	"""
	try:
		ms = float(raw) if raw is not None else 0.0
	except (TypeError, ValueError):
		return 0.0
	return ms / 1000.0 if ms > 0 else 0.0


__all__ = [
	"ElementRef",
	"translate_event",
	"update_state",
	"aggregates_input",
]
