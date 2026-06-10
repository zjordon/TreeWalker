"""Shared test fixtures and factory functions for DOM pipeline tests."""

from __future__ import annotations

import pytest

from tree_walker.browser.views import (
	DOMRect,
	DOMSelectorMap,
	EnhancedAXNode,
	EnhancedAXProperty,
	EnhancedDOMTreeNode,
	EnhancedSnapshotNode,
	NodeType,
	SerializedDOMState,
	SimplifiedNode,
)


# ── Geometry ──────────────────────────────────────────────────────────────


def _make_dom_rect(x: float = 0.0, y: float = 0.0, w: float = 100.0, h: float = 100.0) -> DOMRect:
	return DOMRect(x=x, y=y, width=w, height=h)


# ── Snapshot ──────────────────────────────────────────────────────────────


def _make_snapshot_node(
	*,
	bounds: DOMRect | None | str = 'auto',
	client_rects: DOMRect | None | str = 'auto',
	scroll_rects: DOMRect | None = None,
	is_clickable: bool | None = None,
	cursor_style: str | None = None,
	computed_styles: dict[str, str] | None = None,
	paint_order: int | None = None,
	stacking_contexts: int | None = None,
) -> EnhancedSnapshotNode:
	"""Create an EnhancedSnapshotNode.

	Pass 'auto' for bounds/client_rects to auto-generate a DOMRect(0,0,100,100).
	Pass None to leave the field as None.
	"""
	_auto = _make_dom_rect()
	return EnhancedSnapshotNode(
		is_clickable=is_clickable,
		cursor_style=cursor_style,
		bounds=_auto if bounds == 'auto' else bounds,
		clientRects=_auto if client_rects == 'auto' else client_rects,
		scrollRects=scroll_rects,
		computed_styles=computed_styles,
		paint_order=paint_order,
		stacking_contexts=stacking_contexts,
	)


# ── AX tree ──────────────────────────────────────────────────────────────


def _make_ax_property(name: str, value: str | bool | None = None) -> EnhancedAXProperty:
	return EnhancedAXProperty(name=name, value=value)


def _make_ax_node(
	*,
	role: str | None = None,
	name: str | None = None,
	properties: list[EnhancedAXProperty] | None = None,
	ignored: bool = False,
	ax_node_id: str = 'ax-1',
	child_ids: list[str] | None = None,
) -> EnhancedAXNode:
	return EnhancedAXNode(
		ax_node_id=ax_node_id,
		ignored=ignored,
		role=role,
		name=name,
		description=None,
		properties=properties,
		child_ids=child_ids,
	)


# ── DOM tree nodes ────────────────────────────────────────────────────────


def _make_node(
	*,
	tag: str = 'div',
	node_type: NodeType = NodeType.ELEMENT_NODE,
	node_id: int = 1,
	backend_node_id: int = 1,
	attributes: dict[str, str] | None = None,
	is_visible: bool = True,
	is_scrollable: bool | None = None,
	snapshot_node: EnhancedSnapshotNode | None | str = 'auto',
	ax_node: EnhancedAXNode | None = None,
	has_js_click_listener: bool = False,
	children: list[EnhancedDOMTreeNode] | None = None,
	parent: EnhancedDOMTreeNode | None = None,
	content_document: EnhancedDOMTreeNode | None = None,
	shadow_roots: list[EnhancedDOMTreeNode] | None = None,
	node_value: str = '',
	frame_id: str | None = None,
	shadow_root_type: str | None = None,
	session_id: str | None = None,
) -> EnhancedDOMTreeNode:
	"""Create an EnhancedDOMTreeNode with sensible defaults.

	- snapshot_node='auto' generates a visible snapshot with bounds
	- snapshot_node=None skips snapshot entirely (element has no layout data)
	- children/parent bidirectional links are wired automatically
	"""
	snap: EnhancedSnapshotNode | None = None
	if snapshot_node == 'auto':
		snap = _make_snapshot_node()
	elif snapshot_node is not None:
		snap = snapshot_node

	node = EnhancedDOMTreeNode(
		node_id=node_id,
		backend_node_id=backend_node_id,
		node_type=node_type,
		node_name=tag.upper() if node_type == NodeType.ELEMENT_NODE else tag,
		node_value=node_value,
		attributes=attributes if attributes is not None else {},
		is_scrollable=is_scrollable,
		is_visible=is_visible,
		snapshot_node=snap,
		ax_node=ax_node,
		has_js_click_listener=has_js_click_listener,
		parent_node=parent,
		children_nodes=list(children) if children else None,
		content_document=content_document,
		shadow_root_type=shadow_root_type,
		shadow_roots=list(shadow_roots) if shadow_roots else None,
		session_id=session_id,
		frame_id=frame_id,
	)

	# Wire parent links for children
	if node.children_nodes:
		for child in node.children_nodes:
			child.parent_node = node

	return node


def _make_text_node(
	text: str,
	node_id: int = 10,
	backend_node_id: int = 10,
	is_visible: bool = True,
	parent: EnhancedDOMTreeNode | None = None,
) -> EnhancedDOMTreeNode:
	"""Shortcut for creating a TEXT_NODE."""
	return _make_node(
		tag='#text',
		node_type=NodeType.TEXT_NODE,
		node_id=node_id,
		backend_node_id=backend_node_id,
		node_value=text,
		is_visible=is_visible,
		parent=parent,
		snapshot_node=_make_snapshot_node() if is_visible else None,
	)


# ── Simplified tree ───────────────────────────────────────────────────────


def _make_simplified_node(
	*,
	original_node: EnhancedDOMTreeNode | None = None,
	children: list[SimplifiedNode] | None = None,
	should_display: bool = True,
	is_interactive: bool = False,
	is_new: bool = False,
	ignored_by_paint_order: bool = False,
	excluded_by_parent: bool = False,
	is_shadow_host: bool = False,
	is_compound_component: bool = False,
	highlight_index: int | None = None,
) -> SimplifiedNode:
	if original_node is None:
		original_node = _make_node()
	return SimplifiedNode(
		original_node=original_node,
		children=children or [],
		should_display=should_display,
		is_interactive=is_interactive,
		is_new=is_new,
		ignored_by_paint_order=ignored_by_paint_order,
		excluded_by_parent=excluded_by_parent,
		is_shadow_host=is_shadow_host,
		is_compound_component=is_compound_component,
		highlight_index=highlight_index,
	)


# ── Pytest fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def make_dom_rect():
	return _make_dom_rect


@pytest.fixture
def make_snapshot_node():
	return _make_snapshot_node


@pytest.fixture
def make_ax_property():
	return _make_ax_property


@pytest.fixture
def make_ax_node():
	return _make_ax_node


@pytest.fixture
def make_node():
	return _make_node


@pytest.fixture
def make_text_node():
	return _make_text_node


@pytest.fixture
def make_simplified_node():
	return _make_simplified_node
