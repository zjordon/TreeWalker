"""Tests for paint order filtering algorithm."""
from __future__ import annotations

from unittest.mock import patch

from tree_walker.browser.paint_order import PaintOrderRemover, Rect, RectUnionPure
from tests.conftest import (
	_make_dom_rect,
	_make_node,
	_make_snapshot_node,
	_make_simplified_node,
)


# ── TestRect ─────────────────────────────────────────────────────────────────


class TestRect:
	def test_area(self):
		r = Rect(0, 0, 10, 20)
		assert r.area() == 200

	def test_intersects_overlapping(self):
		a = Rect(0, 0, 10, 10)
		b = Rect(5, 5, 15, 15)
		assert a.intersects(b) is True

	def test_intersects_touching_edges(self):
		# Edge-touching: a.x2 == b.x1 -> not intersecting (closed boundary)
		a = Rect(0, 0, 10, 10)
		b = Rect(10, 0, 20, 10)
		assert a.intersects(b) is False

	def test_intersects_fully_separate(self):
		a = Rect(0, 0, 10, 10)
		b = Rect(100, 100, 200, 200)
		assert a.intersects(b) is False

	def test_contains(self):
		outer = Rect(0, 0, 20, 20)
		inner = Rect(5, 5, 15, 15)
		partial = Rect(5, 5, 25, 15)

		# Full containment
		assert outer.contains(inner) is True
		# Same rect
		assert outer.contains(outer) is True
		# Partial overlap -> not contained
		assert outer.contains(partial) is False


# ── TestRectSplitDiff ────────────────────────────────────────────────────────


class TestRectSplitDiff:
	"""Tests for RectUnionPure._split_diff."""

	def _make_union(self) -> RectUnionPure:
		return RectUnionPure()

	def test_bottom_slice(self):
		# b is above a (a.y1 < b.y1) -> bottom part of a remains
		a = Rect(0, 0, 10, 20)
		b = Rect(0, 5, 10, 20)
		parts = self._make_union()._split_diff(a, b)
		assert len(parts) == 1
		assert parts[0] == Rect(0, 0, 10, 5)

	def test_top_slice(self):
		# b is below a (b.y2 < a.y2) -> top part of a remains
		a = Rect(0, 0, 10, 20)
		b = Rect(0, 0, 10, 15)
		parts = self._make_union()._split_diff(a, b)
		assert len(parts) == 1
		assert parts[0] == Rect(0, 15, 10, 20)

	def test_left_slice(self):
		# Y-overlap, b is to the right -> left slice remains
		a = Rect(0, 0, 20, 10)
		b = Rect(5, 0, 20, 10)
		parts = self._make_union()._split_diff(a, b)
		assert len(parts) == 1
		assert parts[0] == Rect(0, 0, 5, 10)

	def test_right_slice(self):
		# Y-overlap, b is to the left -> right slice remains
		a = Rect(0, 0, 20, 10)
		b = Rect(0, 0, 15, 10)
		parts = self._make_union()._split_diff(a, b)
		assert len(parts) == 1
		assert parts[0] == Rect(15, 0, 20, 10)

	def test_all_four_slices(self):
		# b in center of a -> 4 sub-rects
		a = Rect(0, 0, 20, 20)
		b = Rect(5, 5, 15, 15)
		parts = self._make_union()._split_diff(a, b)
		assert len(parts) == 4
		# Bottom slice
		assert Rect(0, 0, 20, 5) in parts
		# Top slice
		assert Rect(0, 15, 20, 20) in parts
		# Left slice
		assert Rect(0, 5, 5, 15) in parts
		# Right slice
		assert Rect(15, 5, 20, 15) in parts


# ── TestRectUnionPure ────────────────────────────────────────────────────────


class TestRectUnionPure:
	def test_empty_contains_returns_false(self):
		u = RectUnionPure()
		assert u.contains(Rect(0, 0, 10, 10)) is False

	def test_add_and_contains_basic(self):
		u = RectUnionPure()
		r = Rect(0, 0, 10, 10)
		assert u.add(r) is True
		assert u.contains(r) is True

	def test_contains_uncovered_area(self):
		u = RectUnionPure()
		u.add(Rect(0, 0, 10, 10))
		assert u.contains(Rect(20, 20, 30, 30)) is False

	def test_overlapping_rects(self):
		u = RectUnionPure()
		u.add(Rect(0, 0, 10, 10))
		u.add(Rect(5, 5, 15, 15))
		# The union covers both rects; the full span should be contained
		assert u.contains(Rect(0, 0, 10, 10)) is True
		assert u.contains(Rect(5, 5, 15, 15)) is True

	def test_multiple_overlapping(self):
		u = RectUnionPure()
		u.add(Rect(0, 0, 10, 10))
		u.add(Rect(5, 0, 15, 10))
		u.add(Rect(10, 0, 20, 10))
		# Full horizontal span should be covered
		assert u.contains(Rect(0, 0, 20, 10)) is True

	def test_max_rects_cap(self):
		# Verify the constant
		assert RectUnionPure._MAX_RECTS == 5000

		# Patch _MAX_RECTS to 3 to test cap behavior
		with patch.object(RectUnionPure, '_MAX_RECTS', 3):
			u = RectUnionPure()
			assert u.add(Rect(0, 0, 1, 1)) is True
			assert u.add(Rect(2, 0, 3, 1)) is True
			assert u.add(Rect(4, 0, 5, 1)) is True
			# Now at cap; next add returns False
			assert u.add(Rect(6, 0, 7, 1)) is False

	def test_partial_coverage_not_contained(self):
		u = RectUnionPure()
		u.add(Rect(0, 0, 10, 10))
		# Larger rect is only partially covered
		assert u.contains(Rect(0, 0, 20, 10)) is False

	def test_l_shape_corner(self):
		u = RectUnionPure()
		# Two rects forming an L shape
		u.add(Rect(0, 0, 10, 10))   # bottom leg
		u.add(Rect(0, 10, 20, 20))  # top arm
		# The corner area (10,0)-(20,10) is NOT covered
		assert u.contains(Rect(10, 0, 20, 10)) is False


# ── TestPaintOrderRemover ────────────────────────────────────────────────────


class TestPaintOrderRemover:
	def test_single_element_no_occlusion(self):
		snap = _make_snapshot_node(
			bounds=_make_dom_rect(0, 0, 100, 100),
			paint_order=1,
			computed_styles={'background-color': 'blue', 'opacity': '1'},
		)
		node = _make_node(tag='div', snapshot_node=snap)
		root = _make_simplified_node(
			original_node=_make_node(tag='body'),
			children=[_make_simplified_node(original_node=node)],
		)
		PaintOrderRemover(root).calculate_paint_order()
		assert root.children[0].ignored_by_paint_order is False

	def test_higher_paint_order_occludes_lower(self):
		bg_snap = _make_snapshot_node(
			bounds=_make_dom_rect(0, 0, 100, 100),
			paint_order=1,
			computed_styles={'background-color': 'blue', 'opacity': '1'},
		)
		bg_node = _make_node(tag='div', node_id=1, backend_node_id=1, snapshot_node=bg_snap)

		fg_snap = _make_snapshot_node(
			bounds=_make_dom_rect(0, 0, 100, 100),
			paint_order=2,
			computed_styles={'background-color': 'white', 'opacity': '1'},
		)
		fg_node = _make_node(tag='div', node_id=2, backend_node_id=2, snapshot_node=fg_snap)

		bg_simplified = _make_simplified_node(original_node=bg_node)
		fg_simplified = _make_simplified_node(original_node=fg_node)
		root = _make_simplified_node(
			original_node=_make_node(tag='body'),
			children=[bg_simplified, fg_simplified],
		)

		PaintOrderRemover(root).calculate_paint_order()
		assert bg_simplified.ignored_by_paint_order is True
		assert fg_simplified.ignored_by_paint_order is False
		# 阶段4：标志回填到 original_node（EnhancedDOMTreeNode）——引用同一性验证。
		# selector_map 存的是 original_node，rerun 侧 _is_actionable(check_receives_events=True)
		# 必须能读到这个回填值，paint_order.py:182 的回填才能生效。
		assert bg_node.ignored_by_paint_order is True
		assert fg_node.ignored_by_paint_order is False

	def test_transparent_background_no_occlusion(self):
		bg_snap = _make_snapshot_node(
			bounds=_make_dom_rect(0, 0, 100, 100),
			paint_order=1,
			computed_styles={'background-color': 'blue', 'opacity': '1'},
		)
		bg_node = _make_node(tag='div', node_id=1, backend_node_id=1, snapshot_node=bg_snap)

		# Foreground has transparent background -> does not occlude
		fg_snap = _make_snapshot_node(
			bounds=_make_dom_rect(0, 0, 100, 100),
			paint_order=2,
			computed_styles={'background-color': 'rgba(0, 0, 0, 0)', 'opacity': '1'},
		)
		fg_node = _make_node(tag='div', node_id=2, backend_node_id=2, snapshot_node=fg_snap)

		bg_simplified = _make_simplified_node(original_node=bg_node)
		fg_simplified = _make_simplified_node(original_node=fg_node)
		root = _make_simplified_node(
			original_node=_make_node(tag='body'),
			children=[bg_simplified, fg_simplified],
		)

		PaintOrderRemover(root).calculate_paint_order()
		assert bg_simplified.ignored_by_paint_order is False

	def test_low_opacity_no_occlusion(self):
		bg_snap = _make_snapshot_node(
			bounds=_make_dom_rect(0, 0, 100, 100),
			paint_order=1,
			computed_styles={'background-color': 'blue', 'opacity': '1'},
		)
		bg_node = _make_node(tag='div', node_id=1, backend_node_id=1, snapshot_node=bg_snap)

		# Foreground has opacity 0.5 -> below 0.8 threshold -> does not occlude
		fg_snap = _make_snapshot_node(
			bounds=_make_dom_rect(0, 0, 100, 100),
			paint_order=2,
			computed_styles={'background-color': 'white', 'opacity': '0.5'},
		)
		fg_node = _make_node(tag='div', node_id=2, backend_node_id=2, snapshot_node=fg_snap)

		bg_simplified = _make_simplified_node(original_node=bg_node)
		fg_simplified = _make_simplified_node(original_node=fg_node)
		root = _make_simplified_node(
			original_node=_make_node(tag='body'),
			children=[bg_simplified, fg_simplified],
		)

		PaintOrderRemover(root).calculate_paint_order()
		assert bg_simplified.ignored_by_paint_order is False

	def test_partial_overlap_not_ignored(self):
		bg_snap = _make_snapshot_node(
			bounds=_make_dom_rect(0, 0, 100, 100),
			paint_order=1,
			computed_styles={'background-color': 'blue', 'opacity': '1'},
		)
		bg_node = _make_node(tag='div', node_id=1, backend_node_id=1, snapshot_node=bg_snap)

		# Foreground covers only 50% of background (right half)
		fg_snap = _make_snapshot_node(
			bounds=_make_dom_rect(50, 0, 50, 100),
			paint_order=2,
			computed_styles={'background-color': 'white', 'opacity': '1'},
		)
		fg_node = _make_node(tag='div', node_id=2, backend_node_id=2, snapshot_node=fg_snap)

		bg_simplified = _make_simplified_node(original_node=bg_node)
		fg_simplified = _make_simplified_node(original_node=fg_node)
		root = _make_simplified_node(
			original_node=_make_node(tag='body'),
			children=[bg_simplified, fg_simplified],
		)

		PaintOrderRemover(root).calculate_paint_order()
		assert bg_simplified.ignored_by_paint_order is False

	def test_same_paint_order_batch(self):
		# Two nodes at same paint_order are processed together (batch)
		# Neither should occlude the other within the same batch
		snap_a = _make_snapshot_node(
			bounds=_make_dom_rect(0, 0, 100, 100),
			paint_order=2,
			computed_styles={'background-color': 'red', 'opacity': '1'},
		)
		node_a = _make_node(tag='div', node_id=1, backend_node_id=1, snapshot_node=snap_a)

		snap_b = _make_snapshot_node(
			bounds=_make_dom_rect(0, 0, 100, 100),
			paint_order=2,
			computed_styles={'background-color': 'green', 'opacity': '1'},
		)
		node_b = _make_node(tag='div', node_id=2, backend_node_id=2, snapshot_node=snap_b)

		simplified_a = _make_simplified_node(original_node=node_a)
		simplified_b = _make_simplified_node(original_node=node_b)
		root = _make_simplified_node(
			original_node=_make_node(tag='body'),
			children=[simplified_a, simplified_b],
		)

		PaintOrderRemover(root).calculate_paint_order()
		# Both at same paint_order; neither is marked because they are
		# processed before rects_to_add is flushed
		assert simplified_a.ignored_by_paint_order is False
		assert simplified_b.ignored_by_paint_order is False

	def test_empty_tree(self):
		# Root with no children should not crash
		root = _make_simplified_node(
			original_node=_make_node(tag='body', snapshot_node=None),
		)
		PaintOrderRemover(root).calculate_paint_order()
		assert root.ignored_by_paint_order is False
