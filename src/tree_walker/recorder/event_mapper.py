"""扩展事件 → TreeWalker action 映射 + 去噪。

扩展 content script 监听 DOM 事件，归一化为统一事件结构发给后端；本模块把这些事件映射
成 TreeWalker 的 action（``{"name": ..., "params": ...}``），并做基本去噪。

只处理「人类浏览器操作」对应的那批 action；``extract``/``find_elements`` 等 LLM 语义动作
无对应人类操作，不在此映射（重放用户流程通常也用不到）。

参考 Browser-BC ``capture/action-recorder.ts`` 的事件归一化思路。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
	from tree_walker.agent.views import AgentHistory

# 需要 target 元素（带 xpath，recorder 用 locator 定位后填 index）的 action 集合
_INDEX_ACTIONS = frozenset({
	"click",
	"input_text",
	"select_dropdown",
	"upload_file",
})


def needs_target(action_name: str) -> bool:
	"""该 action 是否需要定位目标元素（带 index）。"""
	return action_name in _INDEX_ACTIONS


def map_event(event: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
	"""把一条扩展事件映射成 ``(action_name, params_excluding_index)``；不可映射返回 None。

	``event`` 结构：``{"type": "click"|"input_text"|..., "params": {...}, "xpath": ..., ...}``
	需 index 的 action（click/input_text/select_dropdown/upload_file）的 index 由 recorder 用
	``locator.locate_by_xpath`` 定位后填入；本函数只产除 index 外的 params。
	"""
	t = event.get("type")
	ep = event.get("params") or {}
	if t == "click":
		return ("click", {})
	if t == "input_text":
		return ("input_text", {"text": str(ep.get("text", "")), "clear": bool(ep.get("clear", True))})
	if t == "select_dropdown":
		return ("select_dropdown", {"value": str(ep.get("value", ""))})
	if t == "scroll":
		amount = int(ep.get("amount", 3))
		direction = ep.get("direction", "down")
		if direction not in ("up", "down"):
			direction = "down"
		return ("scroll", {
			# 对齐 ScrollParams ge=1/le=10：扩展估算的 amount 可能越界，clamp 进合法区间
			"amount": max(1, min(10, amount)),
			"direction": direction,
		})
	if t == "navigate":
		return ("navigate", {"url": str(ep.get("url", "")), "new_tab": bool(ep.get("new_tab", False))})
	if t == "go_back":
		return ("go_back", {})
	if t == "switch_tab":
		return ("switch_tab", {"tab_id": str(ep.get("tab_id", ""))})
	if t == "close_tab":
		return ("close_tab", {"tab_id": str(ep.get("tab_id", ""))})
	if t == "send_keys":
		return ("send_keys", {"keys": str(ep.get("keys", ""))})
	if t == "upload_file":
		return ("upload_file", {"path": str(ep.get("path", ""))})
	return None


def coalesce_inputs(events: list[dict[str, Any]], gap_ms: float = 1500) -> list[dict[str, Any]]:
	"""去噪：同一 xpath 的连续 input_text 事件合并为最后一条（取最终值）。

	人类在输入框连续敲键会触发多条 input 事件；重放只需要最终值。``gap_ms`` 内、同一 xpath
	且中间未被其它事件隔开的连续 input 视为一次输入，取最后一条。
	"""
	out: list[dict[str, Any]] = []
	for ev in events:
		if ev.get("type") != "input_text":
			out.append(ev)
			continue
		last = out[-1] if out else None
		if (
			last
			and last.get("type") == "input_text"
			and last.get("xpath") == ev.get("xpath")
			and _within_gap(last, ev, gap_ms)
		):
			out[-1] = {**last, "params": ev.get("params"), "ts": ev.get("ts", last.get("ts"))}
		else:
			out.append(ev)
	return out


def _within_gap(a: dict[str, Any], b: dict[str, Any], gap_ms: float) -> bool:
	"""两条事件的时间戳是否在 gap 内（无时间戳则保守判为可合并）。"""
	ta = a.get("ts")
	tb = b.get("ts")
	if ta is None or tb is None:
		return True
	try:
		return abs(tb - ta) <= gap_ms
	except TypeError:
		return True


def denoise_steps(
	steps: "list[AgentHistory]",
	click_gap_s: float = 0.5,
) -> "list[AgentHistory]":
	"""对最终 steps 列表做去噪后处理（``Recorder.stop()`` 落盘前调用）。

	实时管线里每条事件立刻建一条 ``AgentHistory``，难免有冗余；这里在落盘前统一收口：

	- 合并**相邻同 index 的 ``input_text``** → 取最后一条（``clear=True`` 本就覆盖）。
- 折叠**相邻同 index 的 ``click``**（``click_gap_s`` 内，按 ``step_start_time``）→ 留一条。
	- 合并**相邻同方向 ``scroll``** → amount 求和 clamp 1-10。

	跨非可合并步骤不合并。最后**重排 ``step_number``**（0..N-1，同步 ``metadata.step_number``）。

	与 ``coalesce_inputs`` 的区别：后者作用于原始事件流（批处理，仅合并 input_text），
	本函数作用于已拼接的 ``AgentHistory`` steps（含 click 折叠 / scroll 合并），是实时管线
	落盘前的安全网。``AgentHistory`` 非冻结、``model_output`` 为普通 dict，原地改写安全。
	"""
	def _action(step: "AgentHistory") -> dict[str, Any] | None:
		acts = (step.model_output or {}).get("actions") or []
		return acts[0] if acts else None

	def _name(step: "AgentHistory") -> str | None:
		a = _action(step)
		return a.get("name") if a else None

	def _params(step: "AgentHistory") -> dict[str, Any]:
		a = _action(step)
		return a.get("params") if a else {}

	def _t(step: "AgentHistory") -> float | None:
		m = step.metadata
		return getattr(m, "step_start_time", None) if m else None

	out: "list[AgentHistory]" = []
	for step in steps:
		name = _name(step)
		last = out[-1] if out else None
		merged = False
		if last is not None and _name(last) == name and name is not None:
			if name == "input_text" and _params(last).get("index") == _params(step).get("index"):
				out[-1] = step  # 取最终值
				merged = True
			elif name == "click" and _params(last).get("index") == _params(step).get("index") and _params(step).get("index") is not None:
				ta, tb = _t(last), _t(step)
				if ta is None or tb is None or abs(tb - ta) <= click_gap_s:
					out[-1] = step  # 短时重复点击归一，留最后一条
					merged = True
			elif name == "scroll" and _params(last).get("direction") == _params(step).get("direction"):
				total = max(1, min(10, int(_params(last).get("amount", 1)) + int(_params(step).get("amount", 1))))
				_action(last)["params"]["amount"] = total  # 同方向滚动求和
				merged = True
		if not merged:
			out.append(step)

	# 重排 step_number（合并后序号有空洞）
	for i, s in enumerate(out):
		s.step_number = i
		if s.metadata is not None:
			s.metadata.step_number = i
	return out
