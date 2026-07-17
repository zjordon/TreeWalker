"""扩展事件 → TreeWalker action 纯映射（Stage 1）。

扩展 content script 监听 DOM 事件，归一化为统一事件结构发给后端；本模块把这些事件**机械**
映射成 TreeWalker 的 action（``{"name": ..., "params": ...}``）——只做 1:1 类型映射，**不做**
意图判断、**不做**去噪。意图推断（连续 input 聚合）在 ``translation.translate_event``（状态机
辅助），signal 感知去噪在 ``rules.apply_rules``。

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
	``locator.locate_by_ref`` 定位后填入；本函数只产除 index 外的 params。
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
		# accept 来自扩展 change 瞬间（真实 file input），供重放端按 accept 解析；
		# 录制端不再 get_state 定位（导航竞态 + input 重建，算指纹结构性不可达——见
		# docs/user_recording/recorder-timing-solutions.md），故 accept 是 upload 身份签名。
		return ("upload_file", {"path": str(ep.get("path", "")), "accept": str(ep.get("accept", ""))})
	return None


__all__ = ["needs_target", "map_event"]
