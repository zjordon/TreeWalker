"""End-to-end integration tests for the DOMTreeSerializer pipeline.

Builds complete EnhancedDOMTreeNode trees and runs the full serialization
pipeline (create simplified tree -> paint order -> optimize -> bbox filter
-> assign indices -> text output), verifying the final output.
"""

from tests.conftest import (
	_make_dom_rect,
	_make_node,
	_make_text_node,
	_make_snapshot_node,
	_make_ax_node,
	_make_ax_property,
	_make_simplified_node,
)
from tree_walker.browser.views import NodeType, SerializedDOMState, SimplifiedNode
from dom_snapshot.serializer import DOMTreeSerializer


# ── Helpers ─────────────────────────────────────────────────────────────


def _run_pipeline(doc_node, **kwargs):
	"""Run the full serialize_accessible_elements pipeline and return (state, timing)."""
	serializer = DOMTreeSerializer(root_node=doc_node, **kwargs)
	return serializer.serialize_accessible_elements()


# ── TestSimplePage ──────────────────────────────────────────────────────


class TestSimplePage:
	"""Basic page with a button and text node."""

	def _build_tree(self):
		button = _make_node(tag='button', node_id=10, backend_node_id=42, attributes={'title': 'Click me'})
		text = _make_text_node('Hello World', node_id=11, backend_node_id=11)
		body = _make_node(tag='body', node_id=2, backend_node_id=2, children=[button, text])
		doc = _make_node(tag='#document', node_type=NodeType.DOCUMENT_NODE, node_id=1, backend_node_id=1, children=[body])
		return doc, button

	def test_button_and_text_output(self):
		doc, _ = self._build_tree()
		state, timing = _run_pipeline(doc)

		assert state.element_tree_text
		assert '<button' in state.element_tree_text
		assert 'Hello World' in state.element_tree_text
		assert 'create_simplified_tree' in timing
		assert 'serialize_accessible_elements_total' in timing

	def test_selector_map_entries(self):
		doc, button = self._build_tree()
		state, _ = _run_pipeline(doc)

		assert 42 in state.selector_map
		assert state.selector_map[42].tag_name == 'button'


# ── TestNestedElements ─────────────────────────────────────────────────


class TestNestedElements:
	"""Tests for nested element structures."""

	def test_link_with_children(self):
		span = _make_node(tag='span', node_id=12, backend_node_id=43, snapshot_node=_make_snapshot_node(bounds=_make_dom_rect(5, 5, 90, 90)))
		text = _make_text_node('Click here', node_id=11, backend_node_id=11)
		link = _make_node(tag='a', node_id=10, backend_node_id=42, attributes={'href': '#'}, children=[text, span], snapshot_node=_make_snapshot_node(bounds=_make_dom_rect(0, 0, 100, 100)))
		body = _make_node(tag='body', node_id=2, backend_node_id=2, children=[link])
		doc = _make_node(tag='#document', node_type=NodeType.DOCUMENT_NODE, node_id=1, backend_node_id=1, children=[body])

		state, _ = _run_pipeline(doc)

		assert '<a' in state.element_tree_text
		assert 'Click here' in state.element_tree_text
		# <a> is a propagating element; the <span> bounds are contained
		# within <a>'s bounds so it gets excluded_by_parent=True and is
		# suppressed from output. This is correct bbox-filtering behavior.
		assert 42 in state.selector_map

	def test_deeply_nested_divs(self):
		# Plain divs without interactive/scrollable attributes don't produce
		# <div> tags in the output; they are transparent containers. So we put
		# an interactive button at the deepest level to verify nesting depth is
		# preserved in the output indentation.
		btn = _make_node(tag='button', node_id=13, backend_node_id=43, attributes={'title': 'Deep'})
		inner_div = _make_node(tag='div', node_id=10, backend_node_id=40, children=[btn])
		mid_div = _make_node(tag='div', node_id=11, backend_node_id=41, children=[inner_div])
		outer_div = _make_node(tag='div', node_id=12, backend_node_id=42, children=[mid_div])
		body = _make_node(tag='body', node_id=2, backend_node_id=2, children=[outer_div])
		doc = _make_node(tag='#document', node_type=NodeType.DOCUMENT_NODE, node_id=1, backend_node_id=1, children=[body])

		state, _ = _run_pipeline(doc)

		text = state.element_tree_text
		# The deeply nested button should appear and be interactive
		assert '<button' in text
		assert 'Deep' in text
		assert 43 in state.selector_map


# ── TestShadowDOM ──────────────────────────────────────────────────────


class TestShadowDOM:
	"""Tests for Shadow DOM (open and closed) handling."""

	def test_open_shadow_root(self):
		shadow_btn = _make_node(tag='button', node_id=60, backend_node_id=43, attributes={'title': 'Shadow Button'})
		shadow_root = _make_node(
			tag='#fragment',
			node_type=NodeType.DOCUMENT_FRAGMENT_NODE,
			shadow_root_type='open',
			node_id=50,
			backend_node_id=50,
			children=[shadow_btn],
		)
		host = _make_node(tag='div', node_id=10, backend_node_id=42, children=[], shadow_roots=[shadow_root])
		shadow_root.parent_node = host

		body = _make_node(tag='body', node_id=2, backend_node_id=2, children=[host])
		doc = _make_node(tag='#document', node_type=NodeType.DOCUMENT_NODE, node_id=1, backend_node_id=1, children=[body])

		state, _ = _run_pipeline(doc)

		text = state.element_tree_text
		assert 'Open Shadow' in text
		assert 'Shadow End' in text
		assert '<button' in text
		assert 'Shadow Button' in text
		# Shadow button should be interactive
		assert 43 in state.selector_map

	def test_closed_shadow_root(self):
		shadow_btn = _make_node(tag='button', node_id=60, backend_node_id=43)
		shadow_root = _make_node(
			tag='#fragment',
			node_type=NodeType.DOCUMENT_FRAGMENT_NODE,
			shadow_root_type='closed',
			node_id=50,
			backend_node_id=50,
			children=[shadow_btn],
		)
		host = _make_node(tag='div', node_id=10, backend_node_id=42, children=[], shadow_roots=[shadow_root])
		shadow_root.parent_node = host

		body = _make_node(tag='body', node_id=2, backend_node_id=2, children=[host])
		doc = _make_node(tag='#document', node_type=NodeType.DOCUMENT_NODE, node_id=1, backend_node_id=1, children=[body])

		state, _ = _run_pipeline(doc)

		text = state.element_tree_text
		assert 'Closed Shadow' in text
		assert 'Shadow End' in text


# ── TestIframe ─────────────────────────────────────────────────────────


class TestIframe:
	"""Tests for iframe handling."""

	def test_iframe_with_content(self):
		inner_text = _make_text_node('Inside iframe', node_id=21, backend_node_id=21)
		inner_body = _make_node(tag='body', node_id=22, backend_node_id=22, children=[inner_text])
		inner_doc = _make_node(tag='#document', node_type=NodeType.DOCUMENT_NODE, node_id=23, backend_node_id=23, children=[inner_body])
		iframe = _make_node(
			tag='iframe',
			node_id=10,
			backend_node_id=42,
			content_document=inner_doc,
			snapshot_node=_make_snapshot_node(bounds=_make_dom_rect(0, 0, 300, 200)),
		)

		body = _make_node(tag='body', node_id=2, backend_node_id=2, children=[iframe])
		doc = _make_node(tag='#document', node_type=NodeType.DOCUMENT_NODE, node_id=1, backend_node_id=1, children=[body])

		state, _ = _run_pipeline(doc)

		text = state.element_tree_text
		# Large iframe (300x200) is interactive -> gets [42]<iframe /> format
		assert '<iframe' in text
		assert '[42]' in text
		assert 'Inside iframe' in text
		# iframe with large dimensions should be interactive
		assert 42 in state.selector_map

	def test_iframe_without_content(self):
		iframe = _make_node(
			tag='iframe',
			node_id=10,
			backend_node_id=42,
			content_document=None,
			snapshot_node=_make_snapshot_node(bounds=_make_dom_rect(0, 0, 300, 200)),
		)
		body = _make_node(tag='body', node_id=2, backend_node_id=2, children=[iframe])
		doc = _make_node(tag='#document', node_type=NodeType.DOCUMENT_NODE, node_id=1, backend_node_id=1, children=[body])

		state, _ = _run_pipeline(doc)

		# No content_document means _process_iframe returns None
		assert '<iframe' not in state.element_tree_text


# ── TestScrollableContainers ───────────────────────────────────────────


class TestScrollableContainers:
	"""Tests for scrollable container detection and indexing."""

	def test_scrollable_dropdown_indexed(self):
		scrollable = _make_node(
			tag='div',
			node_id=10,
			backend_node_id=42,
			is_scrollable=True,
			attributes={'role': 'listbox'},
			snapshot_node=_make_snapshot_node(
				bounds=_make_dom_rect(0, 0, 200, 300),
				scroll_rects=_make_dom_rect(0, 0, 200, 600),
				computed_styles={
					'overflow': 'auto',
					'overflow-y': 'auto',
					'display': 'block',
					'visibility': 'visible',
					'opacity': '1',
				},
			),
		)
		body = _make_node(tag='body', node_id=2, backend_node_id=2, children=[scrollable])
		doc = _make_node(tag='#document', node_type=NodeType.DOCUMENT_NODE, node_id=1, backend_node_id=1, children=[body])

		state, _ = _run_pipeline(doc)

		# listbox role makes it a dropdown -> always gets an interactive index
		assert 42 in state.selector_map
		assert '<div' in state.element_tree_text

	def test_scrollable_with_interactive_child_not_indexed(self):
		"""A scrollable div that has an interactive button child should NOT be indexed
		(because _has_interactive_descendants returns True for non-dropdown scrollables)."""
		btn = _make_node(tag='button', node_id=11, backend_node_id=43, attributes={'title': 'Click'})
		scrollable = _make_node(
			tag='div',
			node_id=10,
			backend_node_id=42,
			is_scrollable=True,
			children=[btn],
			snapshot_node=_make_snapshot_node(
				bounds=_make_dom_rect(0, 0, 200, 300),
				scroll_rects=_make_dom_rect(0, 0, 200, 600),
				computed_styles={
					'overflow': 'auto',
					'overflow-y': 'auto',
					'display': 'block',
					'visibility': 'visible',
					'opacity': '1',
				},
			),
		)
		body = _make_node(tag='body', node_id=2, backend_node_id=2, children=[scrollable])
		doc = _make_node(tag='#document', node_type=NodeType.DOCUMENT_NODE, node_id=1, backend_node_id=1, children=[body])

		state, _ = _run_pipeline(doc)

		# The scrollable div itself should NOT be in selector_map (has interactive child)
		assert 42 not in state.selector_map
		# But the button inside should be
		assert 43 in state.selector_map


# ── TestCompoundComponentsIntegration ───────────────────────────────────


class TestCompoundComponentsIntegration:
	"""End-to-end tests for compound component detection."""

	def test_select_with_options_e2e(self):
		opt1 = _make_node(
			tag='option',
			node_id=101,
			backend_node_id=101,
			attributes={'value': '1'},
			children=[_make_text_node('Option A', node_id=201, backend_node_id=201)],
		)
		opt2 = _make_node(
			tag='option',
			node_id=102,
			backend_node_id=102,
			attributes={'value': '2'},
			children=[_make_text_node('Option B', node_id=202, backend_node_id=202)],
		)
		select = _make_node(
			tag='select',
			node_id=10,
			backend_node_id=42,
			attributes={'name': 'color'},
			children=[opt1, opt2],
			ax_node=_make_ax_node(role='select', child_ids=['ax-opt1', 'ax-opt2']),
		)
		body = _make_node(tag='body', node_id=2, backend_node_id=2, children=[select])
		doc = _make_node(tag='#document', node_type=NodeType.DOCUMENT_NODE, node_id=1, backend_node_id=1, children=[body])

		state, _ = _run_pipeline(doc)

		text = state.element_tree_text
		assert '<select' in text
		assert 'compound_components=' in text
		assert 'Dropdown Toggle' in text
		assert 'listbox' in text
		assert 'Option A' in text
		assert 'Option B' in text
		assert 42 in state.selector_map

	def test_file_input_e2e(self):
		file_input = _make_node(
			tag='input',
			node_id=10,
			backend_node_id=42,
			attributes={'type': 'file'},
			ax_node=_make_ax_node(
				role='button',
				properties=[_make_ax_property('valuetext', 'my_document.pdf')],
			),
		)
		body = _make_node(tag='body', node_id=2, backend_node_id=2, children=[file_input])
		doc = _make_node(tag='#document', node_type=NodeType.DOCUMENT_NODE, node_id=1, backend_node_id=1, children=[body])

		state, _ = _run_pipeline(doc)

		text = state.element_tree_text
		assert '<input' in text
		assert 'compound_components=' in text
		assert 'Browse Files' in text
		assert 'my_document.pdf' in text
		assert 42 in state.selector_map


# ── TestIsNewMarking ───────────────────────────────────────────────────


class TestIsNewMarking:
	"""Tests for is_new flag based on previous selector_map."""

	def _make_simple_doc(self):
		button = _make_node(tag='button', node_id=10, backend_node_id=42, attributes={'title': 'Click'})
		body = _make_node(tag='body', node_id=2, backend_node_id=2, children=[button])
		doc = _make_node(tag='#document', node_type=NodeType.DOCUMENT_NODE, node_id=1, backend_node_id=1, children=[body])
		return doc, button

	def test_new_element_marked(self):
		doc, button = self._make_simple_doc()

		# Previous state with a DIFFERENT element (backend_node_id=99)
		prev_node = _make_node(tag='div', backend_node_id=99)
		previous_state = SerializedDOMState(
			_root=None,
			selector_map={99: prev_node},
			element_tree_text='',
		)

		state, _ = _run_pipeline(doc, previous_cached_state=previous_state)

		# Button 42 was NOT in previous selector_map -> is_new=True -> '*' prefix in text
		text = state.element_tree_text
		assert '*[42]' in text

	def test_existing_element_not_new(self):
		doc, button = self._make_simple_doc()

		# Previous state already has the same button (backend_node_id=42)
		prev_node = _make_node(tag='button', backend_node_id=42)
		previous_state = SerializedDOMState(
			_root=None,
			selector_map={42: prev_node},
			element_tree_text='',
		)

		state, _ = _run_pipeline(doc, previous_cached_state=previous_state)

		# Button 42 IS in previous selector_map -> is_new=False -> no '*' prefix
		text = state.element_tree_text
		assert '*[42]' not in text
		assert '[42]' in text


# ── TestEmptyEdgeCases ─────────────────────────────────────────────────


class TestEmptyEdgeCases:
	"""Edge cases with empty or excluded content."""

	def test_empty_document(self):
		doc = _make_node(
			tag='#document',
			node_type=NodeType.DOCUMENT_NODE,
			node_id=1,
			backend_node_id=1,
			children=[],
		)

		state, _ = _run_pipeline(doc)

		# No children means no simplified tree
		assert state.element_tree_text == ''

	def test_all_excluded(self):
		excluded_div = _make_node(
			tag='div',
			node_id=10,
			backend_node_id=42,
			attributes={'data-browser-use-exclude': 'true'},
		)
		body = _make_node(tag='body', node_id=2, backend_node_id=2, children=[excluded_div])
		doc = _make_node(tag='#document', node_type=NodeType.DOCUMENT_NODE, node_id=1, backend_node_id=1, children=[body])

		state, _ = _run_pipeline(doc)

		# The excluded div should not appear in output
		text = state.element_tree_text
		assert '<div' not in text
		assert 42 not in state.selector_map
