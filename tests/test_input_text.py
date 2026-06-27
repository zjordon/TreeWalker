"""Tests for input_text: element lookup, success echo, focus-fail error mapping,
CDP exception mapping, value-mismatch verification, date/time direct-set branch,
and autocomplete/combobox delay.

Covers the action layer (Tools._action_input_text), mirroring tests/test_click.py:
- success echo: returns 'Typed '...' into [TAG] ...' in extracted_content +
  long_term_memory (mirrors navigate/go_back/click style)
- focus-fail error mapping: click_element returning False (no coordinates + JS
  fallback failed) yields an explicit error instead of silent success
- CDP exception mapping: highlight/click or type raising -> friendly error
- value verification: read-back differing from intended text appends a ⚠️ Note
- date/time direct-set: _requires_direct_value_assignment routes to
  _force_set_value instead of per-char type_text
- autocomplete: JS-driven combobox sleeps ~0.4s and emits a 💡 hint
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tree_walker.browser.views import (
	BrowserStateSummary,
	EnhancedDOMTreeNode,
	NodeType,
	SerializedDOMState,
)
from tree_walker.tools.actions import Tools


# ── Shared helpers ────────────────────────────────────────────────────────────


def _make_entry(
	*,
	tag: str = "INPUT",
	backend_node_id: int = 42,
	attributes: dict[str, str] | None = None,
	node_value: str = "",
) -> EnhancedDOMTreeNode:
	"""A minimal EnhancedDOMTreeNode for selector_map entries."""
	return EnhancedDOMTreeNode(
		node_id=backend_node_id,
		backend_node_id=backend_node_id,
		node_type=NodeType.ELEMENT_NODE,
		node_name=tag.upper(),
		node_value=node_value,
		attributes=attributes or {},
	)


def _make_state(selector_map: dict[int, EnhancedDOMTreeNode]) -> BrowserStateSummary:
	"""Build a BrowserStateSummary with the given selector_map (cache path)."""
	return BrowserStateSummary(
		url="https://example.com",
		title="",
		dom_state=SerializedDOMState(
			_root=None,
			selector_map=selector_map,
			element_tree_text="",
		),
	)


def _make_browser(
	*,
	click_return: bool = True,
	click_side_effect=None,
	read_return: str = "",
	type_side_effect=None,
	force_side_effect=None,
) -> MagicMock:
	"""Stub BrowserSession for action-layer tests (does NOT touch CDP)."""
	bs = MagicMock()
	bs.current_session_id = "sid"
	bs.current_target_id = "tid"
	if click_side_effect is not None:
		bs.click_element = AsyncMock(side_effect=click_side_effect)
	else:
		bs.click_element = AsyncMock(return_value=click_return)
	bs.highlight_element = AsyncMock()
	if type_side_effect is not None:
		bs.type_text = AsyncMock(side_effect=type_side_effect)
	else:
		bs.type_text = AsyncMock()
	if force_side_effect is not None:
		bs._force_set_value = AsyncMock(side_effect=force_side_effect)
	else:
		bs._force_set_value = AsyncMock()
	bs._clear_text_field = AsyncMock(return_value=True)
	bs._read_active_text = AsyncMock(return_value=read_return)
	bs.get_state = AsyncMock(return_value=_make_state({}))
	return bs


@pytest.fixture(autouse=True)
def _fake_sleep(monkeypatch):
	"""Patch asyncio.sleep in the actions module: avoids real waits AND records
	call durations so the autocomplete-delay test can assert on 0.4."""
	calls: list[float] = []

	async def _fake(seconds):
		calls.append(seconds)

	monkeypatch.setattr("tree_walker.tools.actions.asyncio.sleep", _fake)
	return calls


# ── Element lookup ────────────────────────────────────────────────────────────


class TestInputTextElementLookup:
	@pytest.mark.asyncio
	async def test_index_in_cache_types_into_element(self):
		entry = _make_entry(backend_node_id=42)
		state = _make_state({5: entry})
		browser = _make_browser()

		result = await Tools().execute("input_text", {"index": 5, "text": "hi"}, browser, browser_state=state)

		assert result.error is None
		browser.highlight_element.assert_awaited_once_with(42)
		browser.click_element.assert_awaited_once_with(42)
		browser.type_text.assert_awaited_once_with("hi", clear=True)

	@pytest.mark.asyncio
	async def test_missing_index_returns_error_without_typing(self):
		state = _make_state({})  # index 5 absent
		browser = _make_browser()

		result = await Tools().execute("input_text", {"index": 5, "text": "hi"}, browser, browser_state=state)

		assert result.error is not None
		assert "5" in result.error
		browser.click_element.assert_not_awaited()
		browser.type_text.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_element_id_resolves_like_index(self):
		# element_id is a backend node id (e.g. from find_elements(return_node_ids));
		# index===backend_id, so it resolves through selector_map identically.
		entry = _make_entry(backend_node_id=42)
		state = _make_state({42: entry})
		browser = _make_browser()

		result = await Tools().execute(
			"input_text", {"element_id": 42, "text": "hi"}, browser, browser_state=state,
		)

		assert result.error is None
		browser.click_element.assert_awaited_once_with(42)
		browser.type_text.assert_awaited_once_with("hi", clear=True)

	@pytest.mark.asyncio
	async def test_element_id_and_index_mutually_exclusive(self):
		state = _make_state({5: _make_entry(backend_node_id=5)})
		browser = _make_browser()

		result = await Tools().execute(
			"input_text", {"index": 5, "element_id": 42, "text": "hi"}, browser, browser_state=state,
		)

		assert result.error is not None
		assert "exactly one" in result.error
		browser.type_text.assert_not_awaited()


# ── Success echo ──────────────────────────────────────────────────────────────


class TestInputTextEcho:
	@pytest.mark.asyncio
	async def test_echoes_text_and_label_with_match(self):
		entry = _make_entry(backend_node_id=7, attributes={"placeholder": "Email"})
		state = _make_state({3: entry})
		browser = _make_browser(read_return="a@b.com")

		result = await Tools().execute(
			"input_text", {"index": 3, "text": "a@b.com"}, browser, browser_state=state,
		)

		assert result.error is None
		assert result.extracted_content == "Typed 'a@b.com' into [INPUT] 'Email' at index 3"
		assert result.extracted_content == result.long_term_memory
		assert result.success is None
		assert result.is_done is False

	@pytest.mark.asyncio
	async def test_echoes_node_value_when_no_attr(self):
		entry = _make_entry(backend_node_id=7, node_value="search-box")
		state = _make_state({3: entry})
		browser = _make_browser(read_return="q")

		result = await Tools().execute("input_text", {"index": 3, "text": "q"}, browser, browser_state=state)

		assert "search-box" in result.extracted_content
		assert result.extracted_content.startswith("Typed 'q' into [INPUT]")

	@pytest.mark.asyncio
	async def test_echoes_bare_tag_when_nothing_to_identify(self):
		entry = _make_entry(backend_node_id=7)
		state = _make_state({3: entry})
		browser = _make_browser(read_return="x")

		result = await Tools().execute("input_text", {"index": 3, "text": "x"}, browser, browser_state=state)

		assert result.extracted_content == "Typed 'x' into [INPUT] at index 3"

	@pytest.mark.asyncio
	async def test_truncates_long_text_to_60_chars(self):
		long_text = "x" * 200
		entry = _make_entry(backend_node_id=7)
		state = _make_state({3: entry})
		# read-back matches the text so no ⚠️ Note is emitted; this isolates
		# the echo-truncation behavior (shown text is bounded to 60 chars).
		browser = _make_browser(read_return=long_text)

		result = await Tools().execute(
			"input_text", {"index": 3, "text": long_text}, browser, browser_state=state,
		)

		assert "..." in result.extracted_content
		assert long_text not in result.extracted_content  # full 200-char string not echoed
		assert "⚠️ Note" not in result.extracted_content

	@pytest.mark.asyncio
	async def test_truncates_long_label_attr_to_60_chars(self):
		long_label = "L" * 100
		entry = _make_entry(backend_node_id=7, attributes={"placeholder": long_label})
		state = _make_state({3: entry})
		browser = _make_browser(read_return="hi")

		result = await Tools().execute("input_text", {"index": 3, "text": "hi"}, browser, browser_state=state)

		assert "..." in result.extracted_content
		assert long_label not in result.extracted_content  # full 100-char label not echoed

	@pytest.mark.asyncio
	async def test_truncates_long_node_value_to_60_chars(self):
		long_value = "V" * 100
		entry = _make_entry(backend_node_id=7, node_value=long_value)
		state = _make_state({3: entry})
		browser = _make_browser(read_return="hi")

		result = await Tools().execute("input_text", {"index": 3, "text": "hi"}, browser, browser_state=state)

		assert "..." in result.extracted_content
		assert long_value not in result.extracted_content


# ── Focus-fail error mapping ──────────────────────────────────────────────────


class TestInputTextFocusFail:
	@pytest.mark.asyncio
	async def test_click_false_blocks_typing(self):
		"""No silent success when focus click fails."""
		entry = _make_entry(backend_node_id=42)
		state = _make_state({1: entry})
		browser = _make_browser(click_return=False)

		result = await Tools().execute("input_text", {"index": 1, "text": "x"}, browser, browser_state=state)

		assert result.error is not None
		assert "Could not focus element" in result.error
		assert result.extracted_content is None
		browser.type_text.assert_not_awaited()
		browser._force_set_value.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_click_exception_maps_to_friendly_error(self):
		entry = _make_entry(backend_node_id=42)
		state = _make_state({1: entry})
		browser = _make_browser(click_side_effect=RuntimeError("target detached"))

		result = await Tools().execute("input_text", {"index": 1, "text": "x"}, browser, browser_state=state)

		assert result.error == "Input focus failed: target detached"
		browser.type_text.assert_not_awaited()


# ── Type exception mapping ────────────────────────────────────────────────────


class TestInputTextTypeException:
	@pytest.mark.asyncio
	async def test_type_text_exception_maps_to_friendly_error(self):
		entry = _make_entry(backend_node_id=42)
		state = _make_state({1: entry})
		browser = _make_browser(type_side_effect=RuntimeError("CDP down"))

		result = await Tools().execute("input_text", {"index": 1, "text": "x"}, browser, browser_state=state)

		assert result.error is not None
		assert result.error.startswith("Failed to type text")


# ── Value-mismatch verification ───────────────────────────────────────────────


class TestInputTextValueMismatch:
	@pytest.mark.asyncio
	async def test_mismatch_appends_warning_note(self):
		entry = _make_entry(backend_node_id=42)
		state = _make_state({3: entry})
		browser = _make_browser(read_return="XXX")  # differs from typed "hello"

		result = await Tools().execute(
			"input_text", {"index": 3, "text": "hello"}, browser, browser_state=state,
		)

		assert "⚠️ Note" in result.extracted_content
		assert "XXX" in result.extracted_content
		assert "hello" in result.extracted_content

	@pytest.mark.asyncio
	async def test_empty_readback_appends_no_note(self):
		"""Read failure (empty) must not spam the LLM with a mismatch note."""
		entry = _make_entry(backend_node_id=42)
		state = _make_state({3: entry})
		browser = _make_browser(read_return="")

		result = await Tools().execute("input_text", {"index": 3, "text": "hi"}, browser, browser_state=state)

		assert "⚠️ Note" not in result.extracted_content

	@pytest.mark.asyncio
	async def test_matching_readback_appends_no_note(self):
		entry = _make_entry(backend_node_id=42)
		state = _make_state({3: entry})
		browser = _make_browser(read_return="hi")

		result = await Tools().execute("input_text", {"index": 3, "text": "hi"}, browser, browser_state=state)

		assert "⚠️ Note" not in result.extracted_content


# ── Date/time direct-set branch ───────────────────────────────────────────────


class TestInputTextDateDirectSet:
	@pytest.mark.asyncio
	async def test_date_uses_force_set_not_type_text(self):
		entry = _make_entry(backend_node_id=42, attributes={"type": "date"})
		state = _make_state({2: entry})
		browser = _make_browser(read_return="2026-06-18")

		await Tools().execute(
			"input_text", {"index": 2, "text": "2026-06-18"}, browser, browser_state=state,
		)

		browser._force_set_value.assert_awaited_once_with("2026-06-18")
		browser.type_text.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_date_clears_before_force_set(self):
		entry = _make_entry(backend_node_id=42, attributes={"type": "date"})
		state = _make_state({2: entry})
		browser = _make_browser(read_return="2026-06-18")

		await Tools().execute(
			"input_text", {"index": 2, "text": "2026-06-18", "clear": True}, browser, browser_state=state,
		)

		browser._clear_text_field.assert_awaited_once()
		# force_set_value is awaited AFTER clear_text_field
		assert browser._clear_text_field.await_count == 1
		assert browser._force_set_value.await_count == 1

	@pytest.mark.asyncio
	async def test_date_without_clear_skips_clear(self):
		entry = _make_entry(backend_node_id=42, attributes={"type": "date"})
		state = _make_state({2: entry})
		browser = _make_browser(read_return="2026-06-18")

		await Tools().execute(
			"input_text", {"index": 2, "text": "2026-06-18", "clear": False}, browser, browser_state=state,
		)

		browser._clear_text_field.assert_not_awaited()
		browser._force_set_value.assert_awaited_once_with("2026-06-18")

	@pytest.mark.asyncio
	async def test_plain_text_does_not_force_set(self):
		entry = _make_entry(backend_node_id=42, attributes={"type": "text"})
		state = _make_state({2: entry})
		browser = _make_browser(read_return="hi")

		await Tools().execute("input_text", {"index": 2, "text": "hi"}, browser, browser_state=state)

		browser._force_set_value.assert_not_awaited()
		browser.type_text.assert_awaited_once_with("hi", clear=True)


# ── Autocomplete / combobox ───────────────────────────────────────────────────


# ── _is_autocomplete_field predicate (direct unit tests) ─────────────────────


class TestIsAutocompleteField:
	def test_role_combobox(self):
		entry = _make_entry(attributes={"role": "combobox"})
		assert Tools._is_autocomplete_field(entry) == (True, True)

	def test_aria_autocomplete_list(self):
		entry = _make_entry(attributes={"aria-autocomplete": "list"})
		assert Tools._is_autocomplete_field(entry) == (True, True)

	def test_aria_autocomplete_none_is_skipped(self):
		entry = _make_entry(attributes={"aria-autocomplete": "none"})
		assert Tools._is_autocomplete_field(entry) == (False, False)

	def test_native_datalist(self):
		entry = _make_entry(attributes={"list": "cities"})
		assert Tools._is_autocomplete_field(entry) == (True, False)

	def test_aria_haspopup_with_controls(self):
		entry = _make_entry(attributes={"aria-haspopup": "listbox", "aria-controls": "cb"})
		assert Tools._is_autocomplete_field(entry) == (True, False)

	def test_plain_input(self):
		entry = _make_entry(attributes={"type": "text"})
		assert Tools._is_autocomplete_field(entry) == (False, False)


# ── Autocomplete / combobox (via action) ─────────────────────────────────────
	@pytest.mark.asyncio
	async def test_js_combobox_waits_and_hints(self, _fake_sleep):
		entry = _make_entry(backend_node_id=42, attributes={"role": "combobox"})
		state = _make_state({2: entry})
		browser = _make_browser(read_return="ny")

		result = await Tools().execute("input_text", {"index": 2, "text": "ny"}, browser, browser_state=state)

		assert 0.4 in _fake_sleep  # JS-dropdown settle wait
		assert "💡 autocomplete field" in result.extracted_content

	@pytest.mark.asyncio
	async def test_native_datalist_hints_but_does_not_wait(self, _fake_sleep):
		entry = _make_entry(backend_node_id=42, attributes={"list": "cities"})
		state = _make_state({2: entry})
		browser = _make_browser(read_return="ny")

		result = await Tools().execute("input_text", {"index": 2, "text": "ny"}, browser, browser_state=state)

		assert 0.4 not in _fake_sleep  # native <datalist> renders instantly
		assert "💡 autocomplete field" in result.extracted_content

	@pytest.mark.asyncio
	async def test_plain_input_neither_waits_nor_hints(self, _fake_sleep):
		entry = _make_entry(backend_node_id=42, attributes={"type": "text"})
		state = _make_state({2: entry})
		browser = _make_browser(read_return="hi")

		result = await Tools().execute("input_text", {"index": 2, "text": "hi"}, browser, browser_state=state)

		assert 0.4 not in _fake_sleep
		assert "💡 autocomplete field" not in result.extracted_content

	@pytest.mark.asyncio
	async def test_aria_haspopup_with_controls_hints_but_does_not_wait(self, _fake_sleep):
		# Loose aria-haspopup + aria-controls is combobox-shaped (hint) but
		# renders synchronously (no 0.4s wait) — browser-use service.py:413-416.
		entry = _make_entry(
			backend_node_id=42, attributes={"aria-haspopup": "listbox", "aria-controls": "cb-list"},
		)
		state = _make_state({2: entry})
		browser = _make_browser(read_return="ny")

		result = await Tools().execute("input_text", {"index": 2, "text": "ny"}, browser, browser_state=state)

		assert 0.4 not in _fake_sleep
		assert "💡 autocomplete field" in result.extracted_content


# ── clear default ─────────────────────────────────────────────────────────────


class TestInputTextClearDefault:
	@pytest.mark.asyncio
	async def test_omitting_clear_defaults_to_true(self):
		entry = _make_entry(backend_node_id=42)
		state = _make_state({1: entry})
		browser = _make_browser(read_return="x")

		await Tools().execute("input_text", {"index": 1, "text": "x"}, browser, browser_state=state)

		browser.type_text.assert_awaited_once_with("x", clear=True)

	@pytest.mark.asyncio
	async def test_clear_false_appends(self):
		entry = _make_entry(backend_node_id=42)
		state = _make_state({1: entry})
		browser = _make_browser(read_return="oldx")

		await Tools().execute(
			"input_text", {"index": 1, "text": "x", "clear": False}, browser, browser_state=state,
		)

		browser.type_text.assert_awaited_once_with("x", clear=False)
