"""Tests for data models in views.py."""

from __future__ import annotations

import pytest

from tree_walker.browser.views import (
	BrowserStateSummary,
	DOMCollectionConfig,
	DOMDegradationLevel,
	DOMInteractedElement,
	DOMRect,
	EnhancedDOMTreeNode,
	EnhancedSnapshotNode,
	MatchLevel,
	NodeType,
	SerializedDOMState,
	SimplifiedNode,
	TabInfo,
	filter_dynamic_classes,
)
from tests.conftest import (
	_make_ax_node,
	_make_ax_property,
	_make_dom_rect,
	_make_node,
	_make_simplified_node,
	_make_snapshot_node,
	_make_text_node,
)


# ── TestDOMRect ─────────────────────────────────────────────────────────────


class TestDOMRect:
	def test_construction(self):
		r = _make_dom_rect(x=10, y=20, w=200, h=50)
		assert r.x == 10
		assert r.y == 20
		assert r.width == 200
		assert r.height == 50

	def test_to_dict(self):
		r = _make_dom_rect(x=5, y=15, w=80, h=40)
		d = r.to_dict()
		assert d == {'x': 5.0, 'y': 15.0, 'width': 80.0, 'height': 40.0}

	def test_json(self):
		r = _make_dom_rect(x=1, y=2, w=3, h=4)
		assert r.__json__() == r.to_dict()


# ── TestNodeType ────────────────────────────────────────────────────────────


class TestNodeType:
	def test_all_twelve_values_exist_and_correct_int(self):
		expected = {
			'ELEMENT_NODE': 1,
			'ATTRIBUTE_NODE': 2,
			'TEXT_NODE': 3,
			'CDATA_SECTION_NODE': 4,
			'ENTITY_REFERENCE_NODE': 5,
			'ENTITY_NODE': 6,
			'PROCESSING_INSTRUCTION_NODE': 7,
			'COMMENT_NODE': 8,
			'DOCUMENT_NODE': 9,
			'DOCUMENT_TYPE_NODE': 10,
			'DOCUMENT_FRAGMENT_NODE': 11,
			'NOTATION_NODE': 12,
		}
		for name, val in expected.items():
			assert NodeType[name].value == val

	def test_int_comparison_works(self):
		assert NodeType.ELEMENT_NODE == 1
		assert NodeType.TEXT_NODE == 3
		assert int(NodeType.DOCUMENT_FRAGMENT_NODE) == 11

	def test_iteration_yields_twelve(self):
		assert len(list(NodeType)) == 12


# ── TestEnhancedDOMTreeNodeConstruction ──────────────────────────────────────


class TestEnhancedDOMTreeNodeConstruction:
	def test_default_values(self):
		node = _make_node()
		assert node.is_scrollable is None
		assert node.is_visible is True
		assert len(node.uuid) == 32  # hex uuid

	def test_parent_property_returns_parent_node(self):
		parent = _make_node(tag='div', node_id=1)
		child = _make_node(tag='span', node_id=2, parent=parent)
		assert child.parent is parent

	def test_children_property_returns_empty_list_when_none(self):
		node = _make_node(children=None)
		assert node.children == []

	def test_children_and_shadow_roots_combines_both(self):
		shadow = _make_node(tag='shadow-child', node_id=99)
		child = _make_node(tag='span', node_id=2)
		node = _make_node(
			tag='div',
			node_id=1,
			children=[child],
			shadow_roots=[shadow],
		)
		combined = node.children_and_shadow_roots
		assert len(combined) == 2
		assert child in combined
		assert shadow in combined

	def test_tag_name_is_node_name_lower(self):
		node = _make_node(tag='DIV')
		assert node.tag_name == 'div'


# ── TestCoordinateProperties ────────────────────────────────────────────────


class TestCoordinateProperties:
	def test_xy_with_snapshot_bounds_preferred(self):
		snap = _make_snapshot_node(
			bounds=_make_dom_rect(x=0, y=0, w=200, h=200),
			client_rects=_make_dom_rect(x=50, y=50, w=100, h=100),
		)
		node = _make_node(snapshot_node=snap)
		# bounds center: 0 + 200/2 = 100, 0 + 200/2 = 100
		assert node.x == 100
		assert node.y == 100

	def test_xy_without_snapshot_returns_zero(self):
		node = _make_node(snapshot_node=None)
		assert node.x == 0
		assert node.y == 0

	def test_width_height_from_bounds(self):
		snap = _make_snapshot_node(bounds=_make_dom_rect(x=0, y=0, w=250, h=80))
		node = _make_node(snapshot_node=snap)
		assert node.width == 250
		assert node.height == 80

	def test_width_height_without_snapshot_returns_zero(self):
		node = _make_node(snapshot_node=None)
		assert node.width == 0
		assert node.height == 0

	def test_bounds_preferred_over_client_rects_for_xy(self):
		snap = _make_snapshot_node(
			bounds=_make_dom_rect(x=0, y=0, w=500, h=500),
			client_rects=_make_dom_rect(x=10, y=10, w=20, h=20),
		)
		node = _make_node(snapshot_node=snap)
		# Should use bounds, not clientRects
		assert node.x == 250  # 0 + 500/2
		assert node.y == 250  # 0 + 500/2

	def test_xy_uses_bounds_when_no_client_rects(self):
		snap = _make_snapshot_node(
			bounds=_make_dom_rect(x=100, y=200, w=50, h=60),
			client_rects=None,
		)
		node = _make_node(snapshot_node=snap)
		assert node.x == 125  # 100 + 50/2
		assert node.y == 230  # 200 + 60/2


# ── TestXPath ───────────────────────────────────────────────────────────────


class TestXPath:
	def test_simple_single_div(self):
		div = _make_node(tag='div', node_id=1)
		assert div.xpath == 'div'

	def test_same_tag_siblings_get_index(self):
		parent = _make_node(tag='ul', node_id=1)
		li1 = _make_node(tag='li', node_id=2, parent=parent)
		li2 = _make_node(tag='li', node_id=3, parent=parent)
		li3 = _make_node(tag='li', node_id=4, parent=parent)
		parent.children_nodes = [li1, li2, li3]
		for c in parent.children_nodes:
			c.parent_node = parent
		assert li1.xpath.endswith('li[1]')
		assert li2.xpath.endswith('li[2]')
		assert li3.xpath.endswith('li[3]')

	def test_stops_at_iframe_parent(self):
		iframe = _make_node(tag='iframe', node_id=1)
		div = _make_node(tag='div', node_id=2, parent=iframe)
		# xpath stops at iframe boundary — does not include iframe in the path
		assert 'iframe' not in div.xpath
		# The loop breaks before adding the div itself since its parent is iframe
		assert div.xpath == ''

	def test_stops_at_document_fragment(self):
		frag = _make_node(
			tag='#document-fragment',
			node_type=NodeType.DOCUMENT_FRAGMENT_NODE,
			node_id=1,
			snapshot_node=None,
		)
		div = _make_node(tag='div', node_id=2, parent=frag)
		frag.children_nodes = [div]
		div.parent_node = frag
		# Should skip the fragment and just be 'div'
		assert div.xpath == 'div'

	def test_mixed_tags_get_no_index(self):
		parent = _make_node(tag='div', node_id=1)
		span = _make_node(tag='span', node_id=2, parent=parent)
		p = _make_node(tag='p', node_id=3, parent=parent)
		parent.children_nodes = [span, p]
		for c in parent.children_nodes:
			c.parent_node = parent
		assert '[' not in span.xpath.split('/')[-1]
		assert '[' not in p.xpath.split('/')[-1]


# ── TestIsActuallyScrollable ────────────────────────────────────────────────


class TestIsActuallyScrollable:
	def test_cdp_flag_is_scrollable_true(self):
		node = _make_node(is_scrollable=True)
		assert node.is_actually_scrollable is True

	def test_no_snapshot_returns_false(self):
		node = _make_node(snapshot_node=None)
		assert node.is_actually_scrollable is False

	def test_scroll_rects_larger_with_overflow_auto(self):
		snap = _make_snapshot_node(
			bounds=_make_dom_rect(x=0, y=0, w=100, h=100),
			client_rects=_make_dom_rect(x=0, y=0, w=100, h=100),
			scroll_rects=_make_dom_rect(x=0, y=0, w=100, h=500),
			computed_styles={'overflow': 'auto'},
		)
		node = _make_node(tag='div', snapshot_node=snap)
		assert node.is_actually_scrollable is True

	def test_overflow_visible_returns_false(self):
		snap = _make_snapshot_node(
			bounds=_make_dom_rect(x=0, y=0, w=100, h=100),
			client_rects=_make_dom_rect(x=0, y=0, w=100, h=100),
			scroll_rects=_make_dom_rect(x=0, y=0, w=100, h=500),
			computed_styles={'overflow': 'visible'},
		)
		node = _make_node(tag='div', snapshot_node=snap)
		assert node.is_actually_scrollable is False

	def test_no_computed_styles_tag_in_fallback_set(self):
		snap = _make_snapshot_node(
			bounds=_make_dom_rect(x=0, y=0, w=100, h=100),
			client_rects=_make_dom_rect(x=0, y=0, w=100, h=100),
			scroll_rects=_make_dom_rect(x=0, y=0, w=100, h=500),
			computed_styles=None,
		)
		node = _make_node(tag='div', snapshot_node=snap)
		assert node.is_actually_scrollable is True

	def test_equal_sizes_returns_false(self):
		snap = _make_snapshot_node(
			bounds=_make_dom_rect(x=0, y=0, w=100, h=100),
			client_rects=_make_dom_rect(x=0, y=0, w=100, h=100),
			scroll_rects=_make_dom_rect(x=0, y=0, w=100, h=100),
		)
		node = _make_node(tag='div', snapshot_node=snap)
		assert node.is_actually_scrollable is False


# ── TestScrollInfo ──────────────────────────────────────────────────────────


class TestScrollInfo:
	def _make_scrollable_node(self):
		snap = _make_snapshot_node(
			bounds=_make_dom_rect(x=0, y=0, w=100, h=100),
			client_rects=_make_dom_rect(x=0, y=0, w=100, h=100),
			scroll_rects=_make_dom_rect(x=0, y=50, w=100, h=500),
			computed_styles={'overflow': 'auto'},
		)
		return _make_node(tag='div', snapshot_node=snap)

	def test_full_metrics_calculation(self):
		node = self._make_scrollable_node()
		info = node.scroll_info
		assert info is not None
		assert info['scrollable_height'] == 500
		assert info['visible_height'] == 100
		assert info['content_above'] == 50  # scroll_top
		assert info['content_below'] == 350  # 500 - 100 - 50
		assert info['can_scroll_up'] is True
		assert info['can_scroll_down'] is True
		assert info['total_pages'] == 5.0  # 500/100

	def test_none_when_not_scrollable(self):
		node = _make_node(snapshot_node=None)
		assert node.scroll_info is None

	def test_should_show_scroll_info_true_when_can_scroll(self):
		node = self._make_scrollable_node()
		assert node.should_show_scroll_info is True

	def test_get_scroll_info_text_formatting(self):
		node = self._make_scrollable_node()
		text = node.get_scroll_info_text()
		assert text is not None
		assert 'scroll:' in text
		assert 'pages below' in text
		assert 'total:' in text


# ── TestTextCollection ──────────────────────────────────────────────────────


class TestTextCollection:
	def test_get_all_children_text_recursive(self):
		inner = _make_text_node('world', node_id=3)
		outer = _make_node(tag='span', node_id=2, children=[inner])
		root = _make_node(tag='div', node_id=1, children=[outer])
		text = root.get_all_children_text()
		assert 'world' in text

	def test_max_depth_limit(self):
		deep_text = _make_text_node('deep', node_id=4)
		mid = _make_node(tag='span', node_id=3, children=[deep_text])
		outer = _make_node(tag='span', node_id=2, children=[mid])
		root = _make_node(tag='div', node_id=1, children=[outer])
		# max_depth=1 should not reach the deeply nested text
		text = root.get_all_children_text(max_depth=1)
		assert 'deep' not in text

	def test_get_meaningful_text_for_llm_prefers_attributes(self):
		node = _make_node(
			tag='input',
			attributes={'value': 'hello', 'placeholder': 'type here'},
		)
		text = node.get_meaningful_text_for_llm()
		assert text == 'hello'  # 'value' is checked first

	def test_llm_representation_format(self):
		child = _make_text_node('hi', node_id=2)
		node = _make_node(tag='div', node_id=1, children=[child])
		rep = node.llm_representation()
		assert rep.startswith('<div>')
		assert 'hi' in rep


# ── TestHashing ─────────────────────────────────────────────────────────────


class TestHashing:
	def test_deterministic(self):
		node = _make_node(tag='div', node_id=1, attributes={'id': 'test'})
		h1 = node.compute_stable_hash()
		h2 = node.compute_stable_hash()
		assert h1 == h2

	def test_different_parent_paths_different_hash(self):
		parent = _make_node(tag='section', node_id=1)
		child = _make_node(tag='div', node_id=2, parent=parent, attributes={'id': 'x'})
		orphan = _make_node(tag='div', node_id=3, attributes={'id': 'x'})
		assert hash(child) != hash(orphan)

	def test_dynamic_class_filtering_in_compute_stable_hash(self):
		node1 = _make_node(tag='div', node_id=1, attributes={'class': 'btn focus active'})
		node2 = _make_node(tag='div', node_id=2, attributes={'class': 'btn'})
		assert node1.compute_stable_hash() == node2.compute_stable_hash()

	def test_ax_name_contributes(self):
		ax = _make_ax_node(name='Submit')
		node_with_ax = _make_node(tag='button', node_id=1, ax_node=ax)
		node_without_ax = _make_node(tag='button', node_id=2)
		assert hash(node_with_ax) != hash(node_without_ax)

	def test_only_static_attributes_used(self):
		# 'data-custom' is not in STATIC_ATTRIBUTES, so it should not affect hash
		node1 = _make_node(tag='div', node_id=1, attributes={'data-custom': 'abc'})
		node2 = _make_node(tag='div', node_id=2, attributes={})
		assert hash(node1) == hash(node2)


# ── TestFilterDynamicClasses ────────────────────────────────────────────────


class TestFilterDynamicClasses:
	def test_removes_focus_hover_active(self):
		result = filter_dynamic_classes('btn focus hover active primary')
		assert 'focus' not in result
		assert 'hover' not in result
		assert 'active' not in result
		assert 'btn' in result
		assert 'primary' in result

	def test_empty_string(self):
		assert filter_dynamic_classes('') == ''

	def test_none(self):
		assert filter_dynamic_classes(None) == ''

	def test_preserves_semantic_classes(self):
		result = filter_dynamic_classes('container header nav-item')
		assert result == 'container header nav-item'


# ── TestSerialization ───────────────────────────────────────────────────────


class TestSerialization:
	def test_json_returns_dict(self):
		node = _make_node(tag='div', node_id=1)
		result = node.__json__()
		assert isinstance(result, dict)
		assert result['node_id'] == 1
		assert result['node_name'] == 'DIV'

	def test_repr_format(self):
		node = _make_node(tag='div', node_id=1, attributes={'id': 'main'})
		r = repr(node)
		assert r.startswith('<div')
		assert 'id=main' in r
		assert 'num_children=' in r

	def test_str_format(self):
		node = _make_node(tag='div', node_id=1, backend_node_id=42, frame_id='ABCD1234')
		s = str(node)
		assert '<div>' in s
		assert '1234' in s  # last 4 chars of frame_id
		assert '42' in s


# ── TestSimplifiedNode ──────────────────────────────────────────────────────


class TestSimplifiedNode:
	def test_default_values(self):
		sn = _make_simplified_node()
		assert sn.should_display is True
		assert sn.is_interactive is False
		assert sn.is_new is False
		assert sn.ignored_by_paint_order is False
		assert sn.excluded_by_parent is False
		assert sn.is_shadow_host is False
		assert sn.is_compound_component is False
		assert sn.highlight_index is None
		assert sn.children == []

	def test_json_cleans_nested_output(self):
		child_node = _make_node(tag='span', node_id=2)
		child_sn = _make_simplified_node(original_node=child_node)
		root_node = _make_node(tag='div', node_id=1, children=[child_node])
		root_sn = _make_simplified_node(
			original_node=root_node,
			children=[child_sn],
			highlight_index=5,
		)
		result = root_sn.__json__()
		assert result['highlight_index'] == 5
		# children_nodes and shadow_roots should be removed
		assert 'children_nodes' not in result['original_node']
		assert 'shadow_roots' not in result['original_node']
		assert len(result['children']) == 1


# ── TestDOMInteractedElement ────────────────────────────────────────────────


class TestDOMInteractedElement:
	def test_to_dict(self):
		elem = DOMInteractedElement(
			node_id=1,
			backend_node_id=2,
			frame_id='frame-1',
			node_type=NodeType.ELEMENT_NODE,
			node_value='',
			node_name='DIV',
			attributes={'id': 'test'},
			bounds=_make_dom_rect(x=0, y=0, w=100, h=50),
			x_path='div',
			element_hash=12345,
			stable_hash=67890,
			ax_name='TestDiv',
		)
		d = elem.to_dict()
		assert d['node_id'] == 1
		assert d['node_type'] == 1  # enum value
		assert d['bounds'] == {'x': 0, 'y': 0, 'width': 100, 'height': 50}
		assert d['ax_name'] == 'TestDiv'

	def test_load_from_enhanced_dom_tree(self):
		ax = _make_ax_node(name='SubmitBtn')
		node = _make_node(
			tag='button',
			node_id=5,
			backend_node_id=10,
			frame_id='frame-abc',
			attributes={'id': 'btn'},
			ax_node=ax,
		)
		elem = DOMInteractedElement.load_from_enhanced_dom_tree(node)
		assert elem.node_id == 5
		assert elem.backend_node_id == 10
		assert elem.frame_id == 'frame-abc'
		assert elem.node_name == 'BUTTON'
		assert elem.attributes == {'id': 'btn'}
		assert elem.ax_name == 'SubmitBtn'
		assert elem.stable_hash is not None

	def test_no_ax_node_case(self):
		node = _make_node(tag='div', node_id=1, ax_node=None)
		elem = DOMInteractedElement.load_from_enhanced_dom_tree(node)
		assert elem.ax_name is None


# ── TestPydanticModels ──────────────────────────────────────────────────────


class TestPydanticModels:
	def test_browser_state_summary_construction(self):
		tab = TabInfo(target_id='t1', url='https://example.com', title='Example')
		state = BrowserStateSummary(
			url='https://example.com',
			title='Example',
			tabs=[tab],
		)
		assert state.url == 'https://example.com'
		assert len(state.tabs) == 1
		assert state.tabs[0].target_id == 't1'
		assert state.dom_state is None
		assert state.screenshot is None

	def test_tab_info_construction(self):
		tab = TabInfo(target_id='abc', url='https://test.com', title='Test')
		assert tab.target_id == 'abc'
		assert tab.url == 'https://test.com'
		assert tab.title == 'Test'


# ── TestDOMDegradationEnums ─────────────────────────────────────────────────


class TestDOMDegradationEnums:
	def test_dom_degradation_level_values(self):
		assert DOMDegradationLevel.FULL.value == 'full'
		assert DOMDegradationLevel.PARTIAL.value == 'partial'
		assert DOMDegradationLevel.MINIMAL.value == 'minimal'
		assert DOMDegradationLevel.FAILED.value == 'failed'

	def test_dom_collection_config_defaults(self):
		cfg = DOMCollectionConfig()
		assert cfg.cdp_first_timeout == 10.0
		assert cfg.cdp_retry_timeout == 2.0
		assert cfg.max_iframes == 100
		assert cfg.heavy_page_element_threshold == 10000
