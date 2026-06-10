"""绘制顺序过滤算法：基于几何差集判定被前景元素完全遮挡的节点。

核心思路：维护一个"已被遮挡区域"的矩形合集，从最高 paintOrder（前景）到最低（背景）
依次判定每个元素是否被完全覆盖。

算法组成：
  Rect -- 轴对齐包围盒 (AABB)
  RectUnionPure -- 矩形并集，用几何差集维持不相交不变量
  PaintOrderRemover -- 按绘制顺序遍历简化树，标记被遮挡节点
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from tree_walker.browser.views import SimplifiedNode


@dataclass(frozen=True, slots=True)
class Rect:
	"""轴对齐矩形，(x1,y1) 左下角，(x2,y2) 右上角。"""

	x1: float
	y1: float
	x2: float
	y2: float

	def area(self) -> float:
		return (self.x2 - self.x1) * (self.y2 - self.y1)

	def intersects(self, other: Rect) -> bool:
		return not (
			self.x2 <= other.x1
			or other.x2 <= self.x1
			or self.y2 <= other.y1
			or other.y2 <= self.y1
		)

	def contains(self, other: Rect) -> bool:
		return (
			self.x1 <= other.x1
			and self.y1 <= other.y1
			and self.x2 >= other.x2
			and self.y2 >= other.y2
		)


class RectUnionPure:
	"""维护一组互不相交的矩形，表示已被遮挡的屏幕区域总和。

	安全上限 _MAX_RECTS 防止复杂页面中矩形碎片的指数爆炸。
	达到上限后 add() 保守返回 False（不再添加新矩形），
	contains() 可能漏判被遮挡元素，但不会错误移除可见元素。
	"""

	__slots__ = ('_rects',)

	_MAX_RECTS = 5000

	def __init__(self):
		self._rects: list[Rect] = []

	def _split_diff(self, a: Rect, b: Rect) -> list[Rect]:
		"""计算差集 a \\ b，返回最多 4 个子矩形。前提：a 与 b 相交。

		四切片法：
		  Bottom slice: a 在 b 下方的部分
		  Top slice:    a 在 b 上方的部分
		  Left slice:   Y 重叠区间内 a 在 b 左侧的部分
		  Right slice:  Y 重叠区间内 a 在 b 右侧的部分
		"""
		parts: list[Rect] = []

		if a.y1 < b.y1:
			parts.append(Rect(a.x1, a.y1, a.x2, b.y1))
		if b.y2 < a.y2:
			parts.append(Rect(a.x1, b.y2, a.x2, a.y2))

		y_lo = max(a.y1, b.y1)
		y_hi = min(a.y2, b.y2)

		if a.x1 < b.x1:
			parts.append(Rect(a.x1, y_lo, b.x1, y_hi))
		if b.x2 < a.x2:
			parts.append(Rect(b.x2, y_lo, a.x2, y_hi))

		return parts

	def contains(self, r: Rect) -> bool:
		"""判定矩形 r 是否被当前并集完全覆盖。栈消减法。"""
		if not self._rects:
			return False

		stack = [r]
		for s in self._rects:
			new_stack: list[Rect] = []
			for piece in stack:
				if s.contains(piece):
					continue
				if piece.intersects(s):
					new_stack.extend(self._split_diff(piece, s))
				else:
					new_stack.append(piece)
			if not new_stack:
				return True
			stack = new_stack
		return False

	def add(self, r: Rect) -> bool:
		"""将矩形 r 添加到并集（只添加未被覆盖的部分）。返回并集是否增长。"""
		if len(self._rects) >= self._MAX_RECTS:
			return False

		if self.contains(r):
			return False

		pending = [r]
		for s in self._rects:
			new_pending: list[Rect] = []
			for piece in pending:
				if piece.intersects(s):
					new_pending.extend(self._split_diff(piece, s))
				else:
					new_pending.append(piece)
			pending = new_pending

		self._rects.extend(pending)
		return True


class PaintOrderRemover:
	"""基于绘制顺序判定哪些节点被前景元素完全遮挡。"""

	def __init__(self, root: SimplifiedNode):
		self.root = root

	def calculate_paint_order(self) -> None:
		"""遍历简化树，按 paintOrder 从前景到背景判定遮挡关系。"""
		all_nodes_with_paint_order: list[SimplifiedNode] = []

		def collect(node: SimplifiedNode) -> None:
			snap = node.original_node.snapshot_node
			if snap and snap.paint_order is not None and snap.bounds is not None:
				all_nodes_with_paint_order.append(node)
			for child in node.children:
				collect(child)

		collect(self.root)

		if not all_nodes_with_paint_order:
			return

		grouped: dict[int, list[SimplifiedNode]] = defaultdict(list)
		for node in all_nodes_with_paint_order:
			snap = node.original_node.snapshot_node
			if snap and snap.paint_order is not None:
				grouped[snap.paint_order].append(node)

		rect_union = RectUnionPure()

		for _paint_order, nodes in sorted(grouped.items(), key=lambda x: -x[0]):
			rects_to_add: list[Rect] = []

			for node in nodes:
				snap = node.original_node.snapshot_node
				if not snap or not snap.bounds:
					continue

				b = snap.bounds
				rect = Rect(
					x1=b.x,
					y1=b.y,
					x2=b.x + b.width,
					y2=b.y + b.height,
				)

				if rect_union.contains(rect):
					node.ignored_by_paint_order = True

				# 透明或低不透明度的元素不会遮挡下方内容
				styles = snap.computed_styles
				if styles:
					bg = styles.get('background-color', 'rgba(0, 0, 0, 0)')
					if bg == 'rgba(0, 0, 0, 0)':
						continue
					try:
						opacity = float(styles.get('opacity', '1'))
					except (ValueError, TypeError):
						opacity = 1.0
					if opacity < 0.8:
						continue

				rects_to_add.append(rect)

			for rect in rects_to_add:
				rect_union.add(rect)
