"""扩展事件 → TreeWalker action 映射 + 去噪。

扩展 content script 监听 DOM 事件，归一化为统一事件结构发给后端；本模块把这些事件映射
成 TreeWalker 的 action（``{"name": ..., "params": ...}``），并做基本去噪。

只处理「人类浏览器操作」对应的那批 action；``extract``/``find_elements`` 等 LLM 语义动作
无对应人类操作，不在此映射（重放用户流程通常也用不到）。

参考 Browser-BC ``capture/action-recorder.ts`` 的事件归一化思路。
"""

from __future__ import annotations

from typing import Any

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
		return ("scroll", {
			"amount": int(ep.get("amount", 3)),
			"direction": ep.get("direction", "down"),
		})
	if t == "navigate":
		return ("navigate", {"url": str(ep.get("url", ""))})
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
