"""Tests for ClickableElementDetector.is_interactive — 14-rule decision waterfall."""

from tests.conftest import (
	_make_node,
	_make_snapshot_node,
	_make_ax_node,
	_make_ax_property,
	_make_dom_rect,
)
from tree_walker.browser.views import NodeType
from tree_walker.browser.dom import ClickableElementDetector


# ── Rule 1: Non-ELEMENT_NODE ─────────────────────────────────────────────


class TestRule1NonElementNode:
	def test_text_node_returns_false(self):
		node = _make_node(tag='#text', node_type=NodeType.TEXT_NODE)
		assert ClickableElementDetector.is_interactive(node) is False

	def test_document_node_returns_false(self):
		node = _make_node(tag='#document', node_type=NodeType.DOCUMENT_NODE)
		assert ClickableElementDetector.is_interactive(node) is False


# ── Rule 2: html/body tags ───────────────────────────────────────────────


class TestRule2HtmlBody:
	def test_html_tag_returns_false(self):
		node = _make_node(tag='html')
		assert ClickableElementDetector.is_interactive(node) is False

	def test_body_tag_returns_false(self):
		node = _make_node(tag='body')
		assert ClickableElementDetector.is_interactive(node) is False


# ── Rule 3: JS click listener ────────────────────────────────────────────


class TestRule3JSClickListener:
	def test_has_js_click_listener_returns_true(self):
		node = _make_node(tag='div', has_js_click_listener=True)
		assert ClickableElementDetector.is_interactive(node) is True

	def test_short_circuit_div_with_js_listener(self):
		"""A plain div with no other interactive signals is interactive via JS listener."""
		node = _make_node(tag='div', has_js_click_listener=True)
		assert ClickableElementDetector.is_interactive(node) is True


# ── Rule 4: IFRAME/FRAME ────────────────────────────────────────────────


class TestRule4Iframe:
	def test_large_iframe_returns_true(self):
		bounds = _make_dom_rect(w=200, h=200)
		node = _make_node(
			tag='iframe',
			snapshot_node=_make_snapshot_node(bounds=bounds),
		)
		assert ClickableElementDetector.is_interactive(node) is True

	def test_small_iframe_does_not_trigger_rule4(self):
		"""Small iframe (50x50) does NOT match rule 4; falls through to later rules."""
		bounds = _make_dom_rect(w=50, h=50)
		node = _make_node(
			tag='iframe',
			snapshot_node=_make_snapshot_node(bounds=bounds),
		)
		# Rule 4 requires > 100x100, so this should NOT be caught by rule 4.
		# No other rules match a plain small iframe, so final result is False.
		assert ClickableElementDetector.is_interactive(node) is False


# ── Rule 5: Label ────────────────────────────────────────────────────────


class TestRule5Label:
	def test_label_with_for_attr_returns_false(self):
		node = _make_node(tag='label', attributes={'for': 'username'})
		assert ClickableElementDetector.is_interactive(node) is False

	def test_label_wrapping_input_returns_true(self):
		child = _make_node(tag='input', node_id=2, backend_node_id=2)
		node = _make_node(tag='label', children=[child])
		assert ClickableElementDetector.is_interactive(node) is True

	def test_label_without_for_no_control_falls_through(self):
		"""A label with no 'for' and no wrapped control falls through to later rules."""
		node = _make_node(tag='label')
		# No other rules match a plain label, so final result is False.
		assert ClickableElementDetector.is_interactive(node) is False


# ── Rule 6: Span wrapper ─────────────────────────────────────────────────


class TestRule6Span:
	def test_span_wrapping_input_returns_true(self):
		child = _make_node(tag='input', node_id=2, backend_node_id=2)
		node = _make_node(tag='span', children=[child])
		assert ClickableElementDetector.is_interactive(node) is True

	def test_plain_span_falls_through(self):
		"""A plain span with no form control falls through to later rules."""
		node = _make_node(tag='span')
		# No other rules match, so final result is False.
		assert ClickableElementDetector.is_interactive(node) is False


# ── Rule 7: Search indicators ────────────────────────────────────────────


class TestRule7Search:
	def test_class_search_btn(self):
		node = _make_node(tag='div', attributes={'class': 'search-btn'})
		assert ClickableElementDetector.is_interactive(node) is True

	def test_id_searchbox(self):
		node = _make_node(tag='div', attributes={'id': 'searchbox'})
		assert ClickableElementDetector.is_interactive(node) is True

	def test_data_action_magnify(self):
		node = _make_node(tag='div', attributes={'data-action': 'magnify'})
		assert ClickableElementDetector.is_interactive(node) is True


# ── Rule 8: AX properties ────────────────────────────────────────────────


class TestRule8AXProperties:
	def test_disabled_returns_false(self):
		ax = _make_ax_node(properties=[_make_ax_property('disabled', True)])
		node = _make_node(tag='button', ax_node=ax)
		assert ClickableElementDetector.is_interactive(node) is False

	def test_hidden_returns_false(self):
		ax = _make_ax_node(properties=[_make_ax_property('hidden', True)])
		node = _make_node(tag='button', ax_node=ax)
		assert ClickableElementDetector.is_interactive(node) is False

	def test_focusable_returns_true(self):
		ax = _make_ax_node(properties=[_make_ax_property('focusable', True)])
		node = _make_node(tag='div', ax_node=ax)
		assert ClickableElementDetector.is_interactive(node) is True

	def test_editable_returns_true(self):
		ax = _make_ax_node(properties=[_make_ax_property('editable', True)])
		node = _make_node(tag='div', ax_node=ax)
		assert ClickableElementDetector.is_interactive(node) is True

	def test_checked_returns_true(self):
		ax = _make_ax_node(properties=[_make_ax_property('checked', True)])
		node = _make_node(tag='div', ax_node=ax)
		assert ClickableElementDetector.is_interactive(node) is True

	def test_keyshortcuts_returns_true(self):
		ax = _make_ax_node(properties=[_make_ax_property('keyshortcuts', 'Enter')])
		node = _make_node(tag='div', ax_node=ax)
		assert ClickableElementDetector.is_interactive(node) is True


# ── Rule 9: Interactive tags ─────────────────────────────────────────────


class TestRule9InteractiveTags:
	def test_button_returns_true(self):
		node = _make_node(tag='button')
		assert ClickableElementDetector.is_interactive(node) is True

	def test_all_nine_interactive_tags(self):
		"""Verify all 9 interactive tags are detected."""
		tags = ['button', 'input', 'select', 'textarea', 'a', 'details', 'summary', 'option', 'optgroup']
		for tag in tags:
			node = _make_node(tag=tag)
			assert ClickableElementDetector.is_interactive(node) is True, f'{tag} should be interactive'


# ── Rule 10: Interactive HTML attributes ─────────────────────────────────


class TestRule10InteractiveAttributes:
	def test_onclick_returns_true(self):
		node = _make_node(tag='div', attributes={'onclick': 'doSomething()'})
		assert ClickableElementDetector.is_interactive(node) is True

	def test_tabindex_returns_true(self):
		node = _make_node(tag='div', attributes={'tabindex': '0'})
		assert ClickableElementDetector.is_interactive(node) is True


# ── Rule 11: ARIA role (HTML attribute) ──────────────────────────────────


class TestRule11AriaRole:
	def test_role_button_returns_true(self):
		node = _make_node(tag='div', attributes={'role': 'button'})
		assert ClickableElementDetector.is_interactive(node) is True

	def test_role_tab_returns_true(self):
		node = _make_node(tag='div', attributes={'role': 'tab'})
		assert ClickableElementDetector.is_interactive(node) is True


# ── Rule 12: AX tree role ────────────────────────────────────────────────


class TestRule12AXRole:
	def test_ax_role_button_returns_true(self):
		ax = _make_ax_node(role='button')
		node = _make_node(tag='div', ax_node=ax)
		assert ClickableElementDetector.is_interactive(node) is True

	def test_ax_role_listbox_returns_true(self):
		"""listbox is in the AX tree role set but not in the HTML ARIA role set."""
		ax = _make_ax_node(role='listbox')
		node = _make_node(tag='div', ax_node=ax)
		assert ClickableElementDetector.is_interactive(node) is True


# ── Rule 13: Icon-size elements ──────────────────────────────────────────


class TestRule13IconSize:
	def test_icon_with_role_attr_returns_true(self):
		bounds = _make_dom_rect(w=30, h=30)
		node = _make_node(
			tag='span',
			attributes={'role': 'img'},
			snapshot_node=_make_snapshot_node(bounds=bounds),
		)
		assert ClickableElementDetector.is_interactive(node) is True

	def test_too_small_icon_returns_false(self):
		bounds = _make_dom_rect(w=5, h=5)
		node = _make_node(
			tag='span',
			attributes={'role': 'img'},
			snapshot_node=_make_snapshot_node(bounds=bounds),
		)
		assert ClickableElementDetector.is_interactive(node) is False


# ── Rule 14: cursor: pointer ─────────────────────────────────────────────


class TestRule14CursorPointer:
	def test_cursor_pointer_returns_true(self):
		node = _make_node(
			tag='div',
			snapshot_node=_make_snapshot_node(cursor_style='pointer'),
		)
		assert ClickableElementDetector.is_interactive(node) is True

	def test_cursor_default_returns_false(self):
		node = _make_node(
			tag='div',
			snapshot_node=_make_snapshot_node(cursor_style='default'),
		)
		assert ClickableElementDetector.is_interactive(node) is False


# ── Rule ordering ────────────────────────────────────────────────────────


class TestRuleOrdering:
	def test_js_listener_beats_tag_check(self):
		"""Rule 3 (JS listener) fires before Rule 9 (interactive tags)."""
		node = _make_node(tag='div', has_js_click_listener=True)
		# div is not in interactive_tags, but JS listener makes it interactive
		assert ClickableElementDetector.is_interactive(node) is True

	def test_ax_disabled_beats_interactive_tag(self):
		"""Rule 8 (AX disabled=True -> False) fires before Rule 9 (button tag -> True)."""
		ax = _make_ax_node(properties=[_make_ax_property('disabled', True)])
		node = _make_node(tag='button', ax_node=ax)
		assert ClickableElementDetector.is_interactive(node) is False


# ── Edge cases ───────────────────────────────────────────────────────────


class TestEdgeCases:
	def test_plain_div_no_signals_returns_false(self):
		"""A div with no attributes, no snapshot, no AX node is not interactive."""
		node = _make_node(tag='div', snapshot_node=None, ax_node=None, attributes={})
		assert ClickableElementDetector.is_interactive(node) is False

	def test_form_control_at_depth_2_is_found(self):
		"""has_form_control_descendant with max_depth=2 finds input at label -> span -> input."""
		grandchild = _make_node(tag='input', node_id=3, backend_node_id=3)
		child = _make_node(tag='span', node_id=2, backend_node_id=2, children=[grandchild])
		node = _make_node(tag='label', children=[child])
		assert ClickableElementDetector.is_interactive(node) is True

	def test_form_control_at_depth_3_not_found(self):
		"""has_form_control_descendant only checks max_depth=2, so depth 3 is not found."""
		deep_input = _make_node(tag='input', node_id=6, backend_node_id=6)
		mid2 = _make_node(tag='span', node_id=5, backend_node_id=5, children=[deep_input])
		mid1 = _make_node(tag='span', node_id=4, backend_node_id=4, children=[mid2])
		outer_label = _make_node(tag='label', children=[mid1])
		assert ClickableElementDetector.is_interactive(outer_label) is False
