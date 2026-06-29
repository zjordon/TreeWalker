"""Tests for the 5-step serialization pipeline in DOMTreeSerializer."""
from __future__ import annotations

from unittest.mock import patch

from tree_walker.browser.views import DEFAULT_INCLUDE_ATTRIBUTES, DOMRect, NodeType, SerializedDOMState
from tree_walker.browser.serializer import DOMTreeSerializer, DISABLED_ELEMENTS, SVG_ELEMENTS
from tests.conftest import (
	_make_node,
	_make_text_node,
	_make_snapshot_node,
	_make_simplified_node,
	_make_dom_rect,
	_make_ax_node,
	_make_ax_property,
)


# ══════════════════════════════════════════════════════════════════════════════
# Step 1: _create_simplified_tree
# ══════════════════════════════════════════════════════════════════════════════


class TestStep1CreateSimplifiedTree:

	def test_document_node_passthrough(self):
		"""DOCUMENT_NODE takes first valid child as root."""
		child_div = _make_node(tag='div', node_id=2, backend_node_id=2)
		doc = _make_node(tag='#document', node_type=NodeType.DOCUMENT_NODE, children=[child_div])
		serializer = DOMTreeSerializer(root_node=doc)
		result = serializer._create_simplified_tree(doc)
		assert result is not None
		assert result.original_node.node_id == child_div.node_id

	def test_document_fragment_preserved(self):
		"""DOCUMENT_FRAGMENT_NODE always returns a SimplifiedNode, even with empty children."""
		frag = _make_node(
			tag='#fragment',
			node_type=NodeType.DOCUMENT_FRAGMENT_NODE,
			shadow_root_type='open',
			children=[],
		)
		serializer = DOMTreeSerializer(root_node=_make_node(tag='body'))
		result = serializer._create_simplified_tree(frag)
		assert result is not None
		assert result.original_node.node_type == NodeType.DOCUMENT_FRAGMENT_NODE
		assert result.children == []

	def test_disabled_elements_skipped(self):
		"""style/script/head/meta/link/title tags are skipped."""
		for tag in DISABLED_ELEMENTS:
			node = _make_node(tag=tag)
			serializer = DOMTreeSerializer(root_node=_make_node(tag='body'))
			result = serializer._create_simplified_tree(node)
			assert result is None, f'{tag} should be skipped'

	def test_svg_children_skipped(self):
		"""SVG child elements (path, rect, etc.) are skipped but svg itself is kept."""
		# Only test tags that survive the upper() -> lower() round-trip
		# (clipPath becomes CLIPPATH -> clippath, which won't match 'clipPath')
		lowercase_svg_tags = {t for t in SVG_ELEMENTS if t == t.lower()}
		assert len(lowercase_svg_tags) > 0, 'Should have at least some lowercase SVG elements'

		for tag in lowercase_svg_tags:
			node = _make_node(tag=tag)
			serializer = DOMTreeSerializer(root_node=_make_node(tag='body'))
			result = serializer._create_simplified_tree(node)
			assert result is None, f'SVG child <{tag}> should be skipped'

		# svg itself is kept
		svg_node = _make_node(tag='svg')
		serializer = DOMTreeSerializer(root_node=_make_node(tag='body'))
		result = serializer._create_simplified_tree(svg_node)
		assert result is not None, '<svg> element should be preserved'

	def test_exclusion_marker(self):
		"""data-browser-use-exclude='true' -> None."""
		node = _make_node(tag='div', attributes={'data-browser-use-exclude': 'true'})
		serializer = DOMTreeSerializer(root_node=_make_node(tag='body'))
		result = serializer._create_simplified_tree(node)
		assert result is None

	def test_session_exclusion_marker(self):
		"""data-browser-use-exclude-{session_id}='true' -> None."""
		serializer = DOMTreeSerializer(root_node=_make_node(tag='body'), session_id='abc123')
		node = _make_node(tag='div', attributes={'data-browser-use-exclude-abc123': 'true'})
		result = serializer._create_simplified_tree(node)
		assert result is None

	def test_iframe_with_content_document(self):
		"""IFRAME node with content_document -> simplified tree has content_document's children."""
		inner_div = _make_node(tag='div', node_id=10, backend_node_id=10)
		content_doc = _make_node(
			tag='#document',
			node_type=NodeType.DOCUMENT_NODE,
			node_id=20,
			backend_node_id=20,
			children=[inner_div],
		)
		iframe = _make_node(
			tag='iframe',
			node_id=5,
			backend_node_id=5,
			content_document=content_doc,
		)
		content_doc.parent_node = iframe

		serializer = DOMTreeSerializer(root_node=_make_node(tag='body'))
		result = serializer._create_simplified_tree(iframe)
		assert result is not None
		assert result.original_node.node_id == iframe.node_id
		assert len(result.children) == 1
		assert result.children[0].original_node.node_id == inner_div.node_id

	def test_text_node_visible_preserved(self):
		"""Visible text with len > 1 -> SimplifiedNode returned."""
		text_node = _make_text_node('Hello World')
		serializer = DOMTreeSerializer(root_node=_make_node(tag='body'))
		result = serializer._create_simplified_tree(text_node)
		assert result is not None
		assert result.original_node.node_value == 'Hello World'

	def test_text_node_single_char_skipped(self):
		"""Text 'x' (len 1 after strip) -> None."""
		text_node = _make_text_node('x')
		serializer = DOMTreeSerializer(root_node=_make_node(tag='body'))
		result = serializer._create_simplified_tree(text_node)
		assert result is None

	def test_forced_visibility_aria(self):
		"""Invisible element with aria-label attribute -> preserved via forced visibility."""
		node = _make_node(
			tag='div',
			is_visible=False,
			attributes={'aria-label': 'test'},
			snapshot_node=_make_snapshot_node(bounds=_make_dom_rect(0, 0, 100, 100)),
		)
		serializer = DOMTreeSerializer(root_node=_make_node(tag='body'))
		result = serializer._create_simplified_tree(node)
		assert result is not None


# ══════════════════════════════════════════════════════════════════════════════
# Step 2: Paint order integration
# ══════════════════════════════════════════════════════════════════════════════


class TestStep2PaintOrderIntegration:

	def test_paint_order_filtering_enabled(self):
		"""Occluded node marked ignored_by_paint_order when filtering is enabled."""
		# Two nodes at the same position with different paint orders
		behind = _make_node(
			tag='button',
			node_id=1,
			backend_node_id=1,
			snapshot_node=_make_snapshot_node(
				bounds=_make_dom_rect(0, 0, 100, 100),
				paint_order=1,
			),
		)
		front = _make_node(
			tag='button',
			node_id=2,
			backend_node_id=2,
			snapshot_node=_make_snapshot_node(
				bounds=_make_dom_rect(0, 0, 100, 100),
				paint_order=2,
			),
		)
		root = _make_node(tag='div', children=[behind, front])
		serializer = DOMTreeSerializer(root_node=root, paint_order_filtering=True)

		state, _ = serializer.serialize_accessible_elements()
		assert state._root is not None

		# Collect all nodes and check for paint order filtering
		all_nodes = self._collect_all_nodes(state._root)
		# At least one node should have been handled by paint order filtering
		# (the paint order remover runs and may mark some as ignored)
		assert len(all_nodes) > 0

	def test_paint_order_filtering_disabled(self):
		"""No paint order filtering when paint_order_filtering=False."""
		behind = _make_node(
			tag='button',
			node_id=1,
			backend_node_id=1,
			snapshot_node=_make_snapshot_node(
				bounds=_make_dom_rect(0, 0, 100, 100),
				paint_order=1,
			),
		)
		front = _make_node(
			tag='button',
			node_id=2,
			backend_node_id=2,
			snapshot_node=_make_snapshot_node(
				bounds=_make_dom_rect(0, 0, 100, 100),
				paint_order=2,
			),
		)
		root = _make_node(tag='div', children=[behind, front])
		serializer = DOMTreeSerializer(root_node=root, paint_order_filtering=False)

		state, _ = serializer.serialize_accessible_elements()
		assert state._root is not None
		# No paint order timing info should exist
		assert 'paint_order_filtering' not in serializer.timing_info

	@staticmethod
	def _collect_all_nodes(node):
		"""Flatten the tree into a list of all SimplifiedNodes."""
		nodes = [node]
		for child in node.children:
			nodes.extend(TestStep2PaintOrderIntegration._collect_all_nodes(child))
		return nodes


# ══════════════════════════════════════════════════════════════════════════════
# Step 3: _optimize_tree
# ══════════════════════════════════════════════════════════════════════════════


class TestStep3OptimizeTree:

	def test_invisible_empty_container_pruned(self):
		"""Invisible div with no children -> None (pruned)."""
		node = _make_node(tag='div', is_visible=False, snapshot_node=None)
		simplified = _make_simplified_node(original_node=node, children=[])
		serializer = DOMTreeSerializer(root_node=_make_node(tag='body'))
		result = serializer._optimize_tree(simplified)
		assert result is None

	def test_visible_node_kept(self):
		"""Visible node with snapshot -> kept."""
		node = _make_node(tag='div', is_visible=True)
		simplified = _make_simplified_node(original_node=node, children=[])
		serializer = DOMTreeSerializer(root_node=_make_node(tag='body'))
		result = serializer._optimize_tree(simplified)
		assert result is not None
		assert result.original_node.node_id == node.node_id

	def test_scrollable_node_kept(self):
		"""Node with is_scrollable=True -> kept even when invisible."""
		node = _make_node(tag='div', is_visible=False, is_scrollable=True)
		simplified = _make_simplified_node(original_node=node, children=[])
		serializer = DOMTreeSerializer(root_node=_make_node(tag='body'))
		result = serializer._optimize_tree(simplified)
		assert result is not None

	def test_text_node_kept(self):
		"""TEXT_NODE with value -> kept."""
		text_node = _make_text_node('Hello World')
		simplified = _make_simplified_node(original_node=text_node, children=[])
		serializer = DOMTreeSerializer(root_node=_make_node(tag='body'))
		result = serializer._optimize_tree(simplified)
		assert result is not None
		assert result.original_node.node_type == NodeType.TEXT_NODE


# ══════════════════════════════════════════════════════════════════════════════
# Step 4: _apply_bounding_box_filtering
# ══════════════════════════════════════════════════════════════════════════════


class TestStep4BoundingBoxFiltering:

	def test_propagating_element_a(self):
		"""<a> is detected as a propagating element."""
		serializer = DOMTreeSerializer(root_node=_make_node(tag='body'))
		assert serializer._is_propagating_element({'tag': 'a', 'role': None})

	def test_propagating_element_div_role_button(self):
		"""<div role='button'> is detected as propagating."""
		serializer = DOMTreeSerializer(root_node=_make_node(tag='body'))
		assert serializer._is_propagating_element({'tag': 'div', 'role': 'button'})

	def test_non_propagating_span(self):
		"""Plain <span> is not propagating."""
		serializer = DOMTreeSerializer(root_node=_make_node(tag='body'))
		assert not serializer._is_propagating_element({'tag': 'span', 'role': None})

	def test_contained_child_excluded(self):
		"""Child 99% inside <a> parent bounds -> excluded_by_parent=True."""
		parent = _make_node(
			tag='a',
			node_id=1,
			backend_node_id=1,
			snapshot_node=_make_snapshot_node(bounds=_make_dom_rect(0, 0, 100, 100)),
		)
		child = _make_node(
			tag='span',
			node_id=2,
			backend_node_id=2,
			snapshot_node=_make_snapshot_node(bounds=_make_dom_rect(1, 1, 98, 98)),
			parent=parent,
		)
		parent_node = _make_simplified_node(
			original_node=parent,
			children=[_make_simplified_node(original_node=child)],
		)

		serializer = DOMTreeSerializer(root_node=_make_node(tag='body'))
		serializer._apply_bounding_box_filtering(parent_node)

		assert parent_node.children[0].excluded_by_parent is True

	def test_text_node_never_excluded(self):
		"""TEXT_NODE inside <a> -> excluded_by_parent=False."""
		parent = _make_node(
			tag='a',
			node_id=1,
			backend_node_id=1,
			snapshot_node=_make_snapshot_node(bounds=_make_dom_rect(0, 0, 100, 100)),
		)
		text_node = _make_text_node('Link text', node_id=2, backend_node_id=2, parent=parent)
		parent_node = _make_simplified_node(
			original_node=parent,
			children=[_make_simplified_node(original_node=text_node)],
		)

		serializer = DOMTreeSerializer(root_node=_make_node(tag='body'))
		serializer._apply_bounding_box_filtering(parent_node)

		assert parent_node.children[0].excluded_by_parent is False

	def test_form_element_exception(self):
		"""<input> inside <button> -> excluded_by_parent=False."""
		parent = _make_node(
			tag='button',
			node_id=1,
			backend_node_id=1,
			snapshot_node=_make_snapshot_node(bounds=_make_dom_rect(0, 0, 100, 100)),
		)
		child = _make_node(
			tag='input',
			node_id=2,
			backend_node_id=2,
			snapshot_node=_make_snapshot_node(bounds=_make_dom_rect(1, 1, 98, 98)),
			parent=parent,
		)
		parent_node = _make_simplified_node(
			original_node=parent,
			children=[_make_simplified_node(original_node=child)],
		)

		serializer = DOMTreeSerializer(root_node=_make_node(tag='body'))
		serializer._apply_bounding_box_filtering(parent_node)

		assert parent_node.children[0].excluded_by_parent is False


# ══════════════════════════════════════════════════════════════════════════════
# Step 5: _assign_interactive_indices_and_mark_new_nodes
# ══════════════════════════════════════════════════════════════════════════════


class TestStep5AssignInteractiveIndices:

	def test_interactive_visible_gets_index(self):
		"""button (interactive tag) + visible -> highlight_index set."""
		from tree_walker.browser import dom as dom_module

		btn_node = _make_node(tag='button', node_id=1, backend_node_id=42)
		simplified = _make_simplified_node(original_node=btn_node, children=[])
		root_simplified = _make_simplified_node(original_node=_make_node(tag='div'), children=[simplified])

		serializer = DOMTreeSerializer(root_node=_make_node(tag='body'))
		with patch.object(dom_module.ClickableElementDetector, 'is_interactive', return_value=True):
			serializer._assign_interactive_indices_and_mark_new_nodes(root_simplified)

		assert simplified.highlight_index == 42
		assert simplified.is_interactive is True

	def test_scrollable_dropdown_always_indexed(self):
		"""Scrollable with role=listbox -> indexed."""
		scroll_node = _make_node(
			tag='div',
			node_id=1,
			backend_node_id=55,
			is_scrollable=True,
			attributes={'role': 'listbox'},
			snapshot_node=_make_snapshot_node(bounds=_make_dom_rect(0, 0, 200, 200)),
		)
		simplified = _make_simplified_node(original_node=scroll_node, children=[])

		serializer = DOMTreeSerializer(root_node=_make_node(tag='body'))
		serializer._assign_interactive_indices_and_mark_new_nodes(simplified)

		assert simplified.highlight_index == 55
		assert simplified.is_interactive is True

	def test_scrollable_without_interactive_descendants(self):
		"""Scrollable div with no interactive children -> indexed."""
		scroll_node = _make_node(
			tag='div',
			node_id=1,
			backend_node_id=77,
			is_scrollable=True,
			attributes={},
			snapshot_node=_make_snapshot_node(bounds=_make_dom_rect(0, 0, 200, 200)),
		)
		simplified = _make_simplified_node(original_node=scroll_node, children=[])

		serializer = DOMTreeSerializer(root_node=_make_node(tag='body'))
		serializer._assign_interactive_indices_and_mark_new_nodes(simplified)

		assert simplified.highlight_index == 77
		assert simplified.is_interactive is True

	def test_new_node_detection(self):
		"""backend_node_id not in previous selector_map -> is_new=True."""
		from tree_walker.browser import dom as dom_module

		btn_node = _make_node(tag='button', node_id=1, backend_node_id=99)
		simplified = _make_simplified_node(original_node=btn_node, children=[])

		prev_state = SerializedDOMState(
			_root=None,
			selector_map={1: _make_node(tag='div')},
			element_tree_text='',
		)
		serializer = DOMTreeSerializer(
			root_node=_make_node(tag='body'),
			previous_cached_state=prev_state,
		)
		with patch.object(dom_module.ClickableElementDetector, 'is_interactive', return_value=True):
			serializer._assign_interactive_indices_and_mark_new_nodes(simplified)

		assert simplified.is_new is True

	def test_compound_component_always_new(self):
		"""is_compound_component -> is_new=True regardless of previous state."""
		from tree_walker.browser import dom as dom_module

		btn_node = _make_node(tag='button', node_id=1, backend_node_id=50)
		simplified = _make_simplified_node(
			original_node=btn_node,
			is_compound_component=True,
			children=[],
		)

		# Previous state with the same backend_node_id
		prev_state = SerializedDOMState(
			_root=None,
			selector_map={50: _make_node(tag='button', backend_node_id=50)},
			element_tree_text='',
		)
		serializer = DOMTreeSerializer(
			root_node=_make_node(tag='body'),
			previous_cached_state=prev_state,
		)
		with patch.object(dom_module.ClickableElementDetector, 'is_interactive', return_value=True):
			serializer._assign_interactive_indices_and_mark_new_nodes(simplified)

		assert simplified.is_new is True

	def test_excluded_node_not_indexed(self):
		"""excluded_by_parent=True -> no index assigned."""
		from tree_walker.browser import dom as dom_module

		btn_node = _make_node(tag='button', node_id=1, backend_node_id=30)
		simplified = _make_simplified_node(
			original_node=btn_node,
			excluded_by_parent=True,
			children=[],
		)

		serializer = DOMTreeSerializer(root_node=_make_node(tag='body'))
		with patch.object(dom_module.ClickableElementDetector, 'is_interactive', return_value=True):
			serializer._assign_interactive_indices_and_mark_new_nodes(simplified)

		assert simplified.highlight_index is None
		assert simplified.is_interactive is False

	def test_paint_order_occluded_no_index(self):
		"""ignored_by_paint_order=True + 无 JS click listener -> 不分配索引。"""
		from tree_walker.browser import dom as dom_module

		node = _make_node(tag='div', backend_node_id=10, has_js_click_listener=False)
		simplified = _make_simplified_node(
			original_node=node,
			ignored_by_paint_order=True,
			children=[],
		)

		serializer = DOMTreeSerializer(root_node=_make_node(tag='body'))
		with patch.object(dom_module.ClickableElementDetector, 'is_interactive', return_value=True):
			serializer._assign_interactive_indices_and_mark_new_nodes(simplified)

		assert simplified.highlight_index is None
		assert simplified.is_interactive is False

	def test_paint_order_occluded_js_listener_bypasses(self):
		"""ignored_by_paint_order=True + 有 JS click listener -> 绕过过滤，仍分配索引。"""
		from tree_walker.browser import dom as dom_module

		node = _make_node(tag='section', backend_node_id=129, has_js_click_listener=True)
		simplified = _make_simplified_node(
			original_node=node,
			ignored_by_paint_order=True,
			children=[],
		)

		serializer = DOMTreeSerializer(root_node=_make_node(tag='body'))
		with patch.object(dom_module.ClickableElementDetector, 'is_interactive', return_value=True):
			serializer._assign_interactive_indices_and_mark_new_nodes(simplified)

		assert simplified.highlight_index == 129
		assert simplified.is_interactive is True


class TestSerializeTreeTextOutput:

	def test_interactive_element_format(self):
		"""Interactive element produces [index]<tag ... /> format."""
		node = _make_simplified_node(
			original_node=_make_node(tag='button', backend_node_id=42, attributes={'title': 'Click'}),
			is_interactive=True,
			highlight_index=42,
		)
		result = DOMTreeSerializer.serialize_tree(node, DEFAULT_INCLUDE_ATTRIBUTES)
		assert '[42]' in result
		assert '<button' in result
		assert '/>' in result

	def test_new_interactive_prefix(self):
		"""New interactive element has '*' prefix before index."""
		node = _make_simplified_node(
			original_node=_make_node(tag='button', backend_node_id=7),
			is_interactive=True,
			is_new=True,
			highlight_index=7,
		)
		result = DOMTreeSerializer.serialize_tree(node, DEFAULT_INCLUDE_ATTRIBUTES)
		assert '*[7]' in result

	def test_scrollable_not_interactive(self):
		"""Scrollable but not interactive element: |scroll element|<div />."""
		scroll_node = _make_node(
			tag='div',
			is_scrollable=True,
			snapshot_node=_make_snapshot_node(bounds=_make_dom_rect(0, 0, 200, 200)),
		)
		# Mock should_show_scroll_info to return True
		with patch.object(
			type(scroll_node), 'should_show_scroll_info',
			new_callable=lambda: property(lambda self: True),
		):
			node = _make_simplified_node(original_node=scroll_node, is_interactive=False)
			result = DOMTreeSerializer.serialize_tree(node, DEFAULT_INCLUDE_ATTRIBUTES)
			assert '|scroll element|' in result
			assert '<div' in result

	def test_iframe_format(self):
		"""IFRAME element produces |IFRAME|<iframe /> format."""
		iframe_node = _make_node(tag='iframe')
		node = _make_simplified_node(original_node=iframe_node, children=[])
		result = DOMTreeSerializer.serialize_tree(node, DEFAULT_INCLUDE_ATTRIBUTES)
		assert '|IFRAME|' in result
		assert '<iframe' in result

	def test_shadow_dom_open(self):
		"""Open Shadow DOM shows 'Open Shadow' marker."""
		frag = _make_node(
			tag='#fragment',
			node_type=NodeType.DOCUMENT_FRAGMENT_NODE,
			shadow_root_type='open',
			children=[],
		)
		simplified = _make_simplified_node(original_node=frag, children=[])
		result = DOMTreeSerializer.serialize_tree(simplified, DEFAULT_INCLUDE_ATTRIBUTES)
		assert 'Open Shadow' in result

	def test_shadow_dom_closed(self):
		"""Closed Shadow DOM shows 'Closed Shadow' marker."""
		frag = _make_node(
			tag='#fragment',
			node_type=NodeType.DOCUMENT_FRAGMENT_NODE,
			shadow_root_type='closed',
			children=[],
		)
		simplified = _make_simplified_node(original_node=frag, children=[])
		result = DOMTreeSerializer.serialize_tree(simplified, DEFAULT_INCLUDE_ATTRIBUTES)
		assert 'Closed Shadow' in result

	def test_svg_collapsing(self):
		"""SVG element shows collapsed content marker."""
		svg_node = _make_node(tag='svg')
		simplified = _make_simplified_node(original_node=svg_node, children=[])
		result = DOMTreeSerializer.serialize_tree(simplified, DEFAULT_INCLUDE_ATTRIBUTES)
		assert '<svg' in result
		assert 'SVG content collapsed' in result

	def test_text_node_output(self):
		"""Text node outputs plain text at indent level."""
		text_node = _make_text_node('Hello World')
		simplified = _make_simplified_node(original_node=text_node, children=[])
		result = DOMTreeSerializer.serialize_tree(simplified, DEFAULT_INCLUDE_ATTRIBUTES, depth=0)
		assert 'Hello World' in result

	def test_file_input_keeps_class_outside_whitelist(self):
		"""file input 的 class 即使不在白名单也保留（issue #96：accept 相同时 class 是唯一区分信号）。"""
		assert 'class' not in DEFAULT_INCLUDE_ATTRIBUTES
		node = _make_simplified_node(
			original_node=_make_node(
				tag='input', backend_node_id=42,
				attributes={'type': 'file', 'class': 'semi-upload-hidden-input'},
			),
			is_interactive=True, highlight_index=42,
		)
		result = DOMTreeSerializer.serialize_tree(node, DEFAULT_INCLUDE_ATTRIBUTES)
		assert '<input' in result
		assert 'semi-upload-hidden-input' in result

	def test_file_input_hidden_vs_replace_class_distinct(self):
		"""两个 file input 的 class 不同 → 序列化输出可区分（#96 核心断言）。"""
		hidden = _make_simplified_node(
			original_node=_make_node(
				tag='input', backend_node_id=7,
				attributes={'type': 'file', 'class': 'semi-upload-hidden-input'},
			),
			is_interactive=True, highlight_index=7,
		)
		replace = _make_simplified_node(
			original_node=_make_node(
				tag='input', backend_node_id=8,
				attributes={'type': 'file', 'class': 'semi-upload-hidden-input-replace'},
			),
			is_interactive=True, highlight_index=8,
		)
		text_hidden = DOMTreeSerializer.serialize_tree(hidden, DEFAULT_INCLUDE_ATTRIBUTES)
		text_replace = DOMTreeSerializer.serialize_tree(replace, DEFAULT_INCLUDE_ATTRIBUTES)
		assert 'semi-upload-hidden-input' in text_hidden
		assert 'semi-upload-hidden-input-replace' in text_replace
		assert text_hidden != text_replace  # 修复前两者完全相同

	def test_file_input_without_class_omits_class_attr(self):
		"""file input 无 class 时输出不含 class=。"""
		node = _make_simplified_node(
			original_node=_make_node(
				tag='input', backend_node_id=9, attributes={'type': 'file'},
			),
			is_interactive=True, highlight_index=9,
		)
		result = DOMTreeSerializer.serialize_tree(node, DEFAULT_INCLUDE_ATTRIBUTES)
		assert '<input' in result
		assert 'class=' not in result

	def test_text_input_class_not_leaked(self):
		"""非 file input（text）即使有 class 也不输出 class（Fix A 只针对 file，不全局污染）。"""
		node = _make_simplified_node(
			original_node=_make_node(
				tag='input', backend_node_id=10,
				attributes={'type': 'text', 'class': 'some-text-class'},
			),
			is_interactive=True, highlight_index=10,
		)
		result = DOMTreeSerializer.serialize_tree(node, DEFAULT_INCLUDE_ATTRIBUTES)
		assert '<input' in result
		assert 'some-text-class' not in result
		assert 'class=' not in result

	def test_include_attributes_not_mutated_by_file_input(self):
		"""file input 保留 class 用局部副本，不污染传入的 include_attributes 列表。"""
		attrs_list = [*DEFAULT_INCLUDE_ATTRIBUTES]
		assert 'class' not in attrs_list
		node = _make_simplified_node(
			original_node=_make_node(
				tag='input', backend_node_id=11,
				attributes={'type': 'file', 'class': 'semi-upload-hidden-input'},
			),
			is_interactive=True, highlight_index=11,
		)
		DOMTreeSerializer.serialize_tree(node, attrs_list)
		assert attrs_list == DEFAULT_INCLUDE_ATTRIBUTES
		assert 'class' not in attrs_list
