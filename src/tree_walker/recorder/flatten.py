"""Flatten：``Recording`` 内部模型 → ``AgentHistoryList``（重放端格式，零改动消费）。

录制停止时调用。纯 reshape——``ActionRecord`` 的 ``params``（含实时 resolve 的 index/path/tab_id）
与 ``interacted_element``（实时指纹投影）在 ``handle_event`` 事件到达时就已填好，这里只是按位
转成 ``AgentHistory``，**不再二次定位**（modal 在 stop 前已关闭，stop 时 locate 会失败）。

signal 在 flatten 时基本被消费掉了：``NAVIGATION`` signal 已让副作用 navigate 在规则里被丢弃，
``MODAL_OPENED`` signal 已让编辑器触发器 click 在 ``rule_file_upload`` 里被保留。重放端不读
signal，故 flatten 不序列化 signal（如需保留可写入 ``state_summary``，重放端忽略）。
"""

from __future__ import annotations

from typing import Any

from tree_walker.agent.views import AgentHistory, AgentHistoryList, StepMetadata
from tree_walker.recorder.models import ActionRecord, Recording


def flatten(recording: Recording) -> AgentHistoryList:
	"""``Recording`` → ``AgentHistoryList``（step_number 重排 0..N-1）。"""
	steps = [
		_action_to_history(
			action,
			step_number=i,
			prev_ts=recording.actions[i - 1].timestamp if i > 0 else None,
		)
		for i, action in enumerate(recording.actions)
	]
	return AgentHistoryList(history=steps)


def _action_to_history(
	action: ActionRecord, step_number: int, prev_ts: float | None
) -> AgentHistory:
	# interacted_element 复刻旧 Recorder.handle_event 的取值：
	#   [proj]（定位成功）/ [None]（定位失败）/ []（无 target 动作）→ [] 归一为 None。
	interacted = action.interacted_element
	state_summary: dict[str, Any] = {
		"url": action.page_url,
		"title": action.page_title,
		"duration": 0.0,
	}
	# 诊断：locate 失败的事件线索带进 state_summary（重放端忽略未知键），便于事后分析
	if action.locate_miss:
		state_summary["_locate_miss"] = action.locate_miss
	return AgentHistory(
		step_number=step_number,
		model_output={
			"actions": [{"name": action.action_name, "params": dict(action.params)}],
		},
		result=[],
		state_summary=state_summary,
		interacted_element=interacted if interacted else None,
		metadata=StepMetadata(
			step_start_time=action.timestamp,
			step_end_time=action.timestamp,
			step_number=step_number,
			# 录制回放专用：user_pause_seconds = 相邻 action 的 timestamp 差（人类操作间隔近似）。
			# 不能用 prev.metadata.duration_seconds——flatten 里 start==end==timestamp，恒 0。
			# timestamp 已是秒（translation._ts_seconds /1000），减法直接得秒。
			# 阶段4 / 缺口7：与 agent 自录路径 step_interval（上一步耗时含 LLM）语义分离；
			# 重放端 _compute_step_delay 优先读本字段（不封顶，忠实还原用户停顿）。
			user_pause_seconds=action.timestamp - prev_ts if prev_ts is not None else None,
		),
	)


__all__ = ["flatten"]
