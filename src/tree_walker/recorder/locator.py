"""元素线索 → selector_map 节点定位。

扩展侧（content script）只发 xpath 线索（+ 可选 rect），后端在当前页 ``selector_map`` 里
定位用户实际操作的节点。xpath 仅作「录制瞬间定位线索」用——跨会话稳定性交给后端算的
指纹（``element_hash``/``stable_hash``，重放时由 ``rerun._match_element_index`` 比对），所以
这里只要录制瞬间能唯一定位即可。

xpath 格式归一化：Browser-BC ``xpathFor`` 产出 ``/html/body/...``（前导 ``/``），TreeWalker
``EnhancedDOMTreeNode.xpath`` 是 ``html/body/...``（无前导）。统一 strip 前导 ``/``。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# EnhancedDOMTreeNode 的结构子集（仅用到的字段），用 Any 避免硬依赖
Node = Any


def normalize_xpath(xpath: str | None) -> str:
	"""归一化 xpath：strip 首尾空白 + 去前导 ``/``。

	``/html/body/form/input`` → ``html/body/form/input``，与 TreeWalker ``xpath`` 对齐。
	"""
	if not xpath:
		return ""
	return xpath.strip().lstrip("/")


def _bounds_center(bounds: Any) -> tuple[float, float] | None:
	"""从 DOMRect-like 对象取中心点；不可用返回 None。

	支持两种形态：``{x,y,width,height}`` dict（扩展发的 rect）或带这些属性的对象
	（``EnhancedDOMTreeNode.snapshot_node.bounds``）。
	"""
	if not bounds:
		return None

	def _get(key: str) -> Any:
		if isinstance(bounds, Mapping):
			return bounds.get(key)
		return getattr(bounds, key, None)

	try:
		x = float(_get("x"))
		y = float(_get("y"))
		w = float(_get("width"))
		h = float(_get("height"))
	except (TypeError, ValueError):
		return None
	return (x + w / 2.0, y + h / 2.0)


def _node_bounds_center(node: Node) -> tuple[float, float] | None:
	"""``EnhancedDOMTreeNode`` 的 bounds 在 ``snapshot_node.bounds``。"""
	snap = getattr(node, "snapshot_node", None)
	return _bounds_center(getattr(snap, "bounds", None) if snap is not None else None)


def locate_by_xpath(
	xpath: str | None,
	selector_map: Mapping[int, Node],
	rect: Mapping[str, float] | None = None,
) -> tuple[int, Node] | None:
	"""在 ``selector_map`` 里按 xpath 定位节点。

	:param xpath: 扩展发的 xpath 线索（可为 ``/html/...`` 或 ``html/...``）
	:param selector_map: 当前页 ``{index: EnhancedDOMTreeNode}``（来自 ``browser.get_state()``）
	:param rect: 扩展发的元素 rect（``{x,y,width,height}``）；多候选时按中心就近 tie-break
	:return: ``(index, node)`` 或 ``None``（未命中）

	多候选 tie-break 参考 ``rerun._nearest_idx`` 的「录制 bounds 中心就近」思路，避免取到
	迭代顺序里靠前的错误元素。
	"""
	target = normalize_xpath(xpath)
	if not target:
		return None
	matches = [
		(idx, node)
		for idx, node in selector_map.items()
		if normalize_xpath(getattr(node, "xpath", "")) == target
	]
	if not matches:
		return None
	if len(matches) == 1:
		return matches[0]
	# 多候选：按 rect 中心就近
	if rect is not None:
		hint = _bounds_center(rect)
		if hint is not None:
			best_idx, best_node, best_d = matches[0][0], matches[0][1], float("inf")
			for idx, node in matches:
				center = _node_bounds_center(node)
				if center is None:
					continue
				d = (center[0] - hint[0]) ** 2 + (center[1] - hint[1]) ** 2
				if d < best_d:
					best_idx, best_node, best_d = idx, node, d
			return (best_idx, best_node)
	return matches[0]


def locate_by_ref(
	ref: Mapping[str, Any],
	selector_map: Mapping[int, Node],
) -> tuple[int, Node] | None:
	"""多级定位：先 xpath（含 rect 就近），失败则 ATTRIBUTE 级（tag + name/id/aria-label）。

	ATTRIBUTE 级对应 rerun 五级匹配的第 5 级——xpath 因 DOM 树差异对不上时（典型：SPA 动态
	modal 深嵌套，扩展原生 DOM 与 TreeWalker CDP 树路径不一致），用元素的原始属性兜底。
	"""
	# Level 1: XPATH
	r = locate_by_xpath(ref.get("xpath"), selector_map, ref.get("rect"))
	if r:
		return r
	# Level 2: ATTRIBUTE（tag + name/id/aria-label）
	tag = (ref.get("tag") or "").lower()
	if not tag:
		return None
	# 扩展发的 ariaLabel 对应 HTML aria-label 属性
	for source_key, attr_key in (("name", "name"), ("id", "id"), ("ariaLabel", "aria-label")):
		val = ref.get(source_key)
		if not val:
			continue
		for idx, node in selector_map.items():
			if (getattr(node, "node_name", "") or "").lower() != tag:
				continue
			attrs = getattr(node, "attributes", None) or {}
			if attrs.get(attr_key) == val:
				return (idx, node)
	return None
