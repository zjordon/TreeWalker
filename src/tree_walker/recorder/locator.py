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
	"""多级定位：xpath → ATTRIBUTE → RECT（位置）。用于 click / input_text / select_dropdown。

	- Level 1 XPATH：按 xpath 找（多候选 rect 就近）。
	- Level 2 ATTRIBUTE：tag + name/id/aria-label（对应 rerun 五级匹配第 5 级）。
	- Level 3 RECT（位置）：前两级都失配时，按扩展 rect 的中心点在 ``selector_map`` 里找
	  **bounds 包含该点的节点**（取 IoU 最高者），就近兜底。解决 SPA ``cursor:pointer`` div
	  触发器无 name/id/aria-label、且扩展 xpath 与 CDP 树瞬时漂移（如点击触发 modal 重排）
	  导致 XPATH/ATTRIBUTE 双双失配的场景——位置比 xpath 稳定。

	``upload_file`` 不走本函数（file input 在 selector_map，但扩展 xpath 常与 CDP xpath 不一致
	→ 由 ``Recorder._locate_upload_file`` 按 accept 定位）。

	返回 ``(index, node)`` 或 ``None``。
	"""
	# Level 1: XPATH
	r = locate_by_xpath(ref.get("xpath"), selector_map, ref.get("rect"))
	if r:
		return r
	# Level 2: ATTRIBUTE（tag + name/id/aria-label）
	tag = (ref.get("tag") or "").lower()
	if tag:
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
	# Level 3: RECT（位置兜底）
	return _locate_by_rect(ref, selector_map)


def _node_bounds(node: Node) -> tuple[float, float, float, float] | None:
	"""``EnhancedDOMTreeNode.snapshot_node.bounds`` → ``(x, y, w, h)``；不可用返 None。"""
	snap = getattr(node, "snapshot_node", None)
	bounds = getattr(snap, "bounds", None) if snap is not None else None
	if not bounds:
		return None
	try:
		if isinstance(bounds, Mapping):
			x, y = float(bounds.get("x", 0)), float(bounds.get("y", 0))
			w, h = float(bounds.get("width", 0)), float(bounds.get("height", 0))
		else:
			x, y = float(getattr(bounds, "x", 0)), float(getattr(bounds, "y", 0))
			w, h = float(getattr(bounds, "width", 0)), float(getattr(bounds, "height", 0))
	except (TypeError, ValueError):
		return None
	if w <= 0 or h <= 0:
		return None
	return (x, y, w, h)


def _rect_tuple(bounds: Any) -> tuple[float, float, float, float] | None:
	"""扩展 rect（``{x,y,width,height}`` dict 或同属性对象）→ ``(x, y, w, h)``；不可用返 None。"""
	if not bounds:
		return None

	def _get(key: str) -> Any:
		if isinstance(bounds, Mapping):
			return bounds.get(key)
		return getattr(bounds, key, None)

	try:
		x, y = float(_get("x")), float(_get("y"))
		w, h = float(_get("width")), float(_get("height"))
	except (TypeError, ValueError):
		return None
	if w <= 0 or h <= 0:
		return None
	return (x, y, w, h)


# RECT 兜底的就近距离阈值（px）——无高 IoU 容器时，最近候选在此范围内才认。
_RECT_NEAR_PX = 150
# 容器与点击 rect 的最低 IoU：低于则不认。过滤「点击落在大容器里的小图标」（IoU 极低）
# 和「整页 root 兜底」（点击 rect 占 root 面积比例极低）两类误匹配。
_RECT_MIN_IOU = 0.1


def _locate_by_rect(
	ref: Mapping[str, Any],
	selector_map: Mapping[int, Node],
) -> tuple[int, Node] | None:
	"""Level 3：按扩展 rect 定位 ``selector_map`` 里的可交互节点。

	选择策略：在 **bounds 包含点击中心** 的节点里，取与 **点击 rect 的 IoU（交并比）最高** 者
	（IoU 相同取面积小）。用 IoU 而非「最小容器」：用户点 ``cover-Jg3T4p``(160×120) 触发器时，
	点击中心常落在其内的 18×18 svg 图标上——「最小容器」会误选 svg（无稳定属性，重放匹配不上）；
	IoU 则选 ``cover-Jg3T4p``（bounds ≈ 点击 rect，IoU≈1）。IoU < ``_RECT_MIN_IOU`` 的容器不认
	（过滤整页 root、无关小图标）。无合格容器则 **就近**（``_RECT_NEAR_PX`` 内）。

	ref 无可用 rect 返回 None。
	"""
	ref_b = _rect_tuple(ref.get("rect"))
	if ref_b is None:
		return None
	rx, ry, rw, rh = ref_b
	cx, cy = rx + rw / 2.0, ry + rh / 2.0
	ref_area = rw * rh
	best: tuple[tuple[float, float], int, Node] | None = None  # ((iou, -area), idx, node)
	near: list[tuple[float, int, Node]] = []
	for idx, node in selector_map.items():
		b = _node_bounds(node)
		if b is None:
			continue
		x, y, w, h = b
		# IoU with ref rect
		ix = max(0.0, min(x + w, rx + rw) - max(x, rx))
		iy = max(0.0, min(y + h, ry + rh) - max(y, ry))
		inter = ix * iy
		union = w * h + ref_area - inter
		iou = inter / union if union > 0 else 0.0
		if x <= cx <= x + w and y <= cy <= y + h and iou >= _RECT_MIN_IOU:
			key = (iou, -(w * h))  # IoU 大优先；并列时面积小优先（更具体的匹配）
			if best is None or key > best[0]:
				best = (key, idx, node)
		else:
			cx2, cy2 = x + w / 2.0, y + h / 2.0
			near.append(((cx2 - cx) ** 2 + (cy2 - cy) ** 2, idx, node))
	if best is not None:
		return (best[1], best[2])
	if near:
		near.sort(key=lambda t: t[0])
		d, idx, node = near[0]
		if d <= _RECT_NEAR_PX * _RECT_NEAR_PX:
			return (idx, node)
	return None
