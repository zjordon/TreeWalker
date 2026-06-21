"""Tests for find_elements: CSS selector element query via a single Runtime.evaluate.

Covers:
- action layer: a hit echoes ``Found N element(s) matching "..."`` with a
  compact ``long_term_memory`` summary (singular/plural); ``Showing K of N``
  footer when truncated; ``total==0`` is a SOFT echo (``error is None``,
  aligns with find_text / search_page / browser-use); a raised
  ``find_elements`` returns ``error="Find elements failed: ..."``; the action
  does NOT call ``get_state`` and forwards attributes/max_results/include_text
  through as kwargs
- param model: ``FindElementsParams.selector`` is required; ``attributes`` is
  an optional list; ``max_results`` bounded ``ge=1, le=200`` (default 50);
  ``include_text`` bool (default True); extra fields forbidden
- builder: ``_build_find_elements_js`` injects every user value via
  ``json.dumps`` into a ``var`` declaration (no f-string interpolation of user
  text — a selector containing a double-quote is escaped, not spliced raw),
  wraps the body in an IIFE, and serializes ``attributes=None`` as JS ``null``
- session layer: ``BrowserSession.find_elements`` runs one ``Runtime.evaluate``
  with ``returnByValue=True`` and returns the ``{elements, total, showing}``
  dict; a JS-layer ``{error: ...}`` (invalid CSS selector) raises
  ``RuntimeError("find_elements: ...")``; ``exceptionDetails`` propagates as
  ``RuntimeError("JS error: ...")``; a null return raises
  ``RuntimeError("find_elements returned no result")``
- formatter: ``_format_find_results`` renders ``Found N element(s) ...`` with
  per-element ``[i] <tag> "text" {k="v"} (N children)`` and a
  ``Showing K of N total elements`` footer when truncated
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from tree_walker.browser.session import BrowserSession, _build_find_elements_js
from tree_walker.tools.actions import Tools, _format_find_results
from tree_walker.tools.models import FindElementsParams


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_browser(
	*, find_return: dict | None = None, find_raises: Exception | None = None,
) -> MagicMock:
	"""Stub BrowserSession: find_elements returns a result dict (or raises).

	get_state is an AsyncMock purely so we can assert it is NOT awaited
	(find_elements must not trigger a full DOM fetch).
	"""
	bs = MagicMock()
	if find_raises:
		bs.find_elements = AsyncMock(side_effect=find_raises)
	else:
		bs.find_elements = AsyncMock(
			return_value=find_return or {
				"elements": [
					{"index": 0, "tag": "a", "text": "hi", "attrs": {"href": "/x"}, "children_count": 2},
				],
				"total": 1,
				"showing": 1,
			},
		)
	bs.get_state = AsyncMock()
	return bs


# ── action layer ─────────────────────────────────────────────────────────────


class TestFindElementsAction:
	@pytest.mark.asyncio
	async def test_hit_singular_echoes_formatted_results(self):
		browser = _make_browser(
			find_return={
				"elements": [
					{"index": 0, "tag": "a", "text": "hi", "attrs": {"href": "/x"}, "children_count": 2},
				],
				"total": 1,
				"showing": 1,
			},
		)

		result = await Tools().execute("find_elements", {"selector": "a"}, browser)

		assert result.error is None
		assert result.is_done is False
		assert 'Found 1 element matching "a":' in result.extracted_content  # singular
		assert '[0] <a> "hi" {href="/x"} (2 children)' in result.extracted_content
		assert "Showing" not in result.extracted_content  # no footer when not truncated
		# compact summary memory (singular), NOT the full element list
		assert result.long_term_memory == 'Found 1 element matching "a".'
		assert result.long_term_memory != result.extracted_content
		browser.find_elements.assert_awaited_once_with(
			"a", attributes=None, max_results=50, include_text=True,
		)

	@pytest.mark.asyncio
	async def test_hit_plural_with_truncation_footer(self):
		browser = _make_browser(
			find_return={
				"elements": [
					{"index": 0, "tag": "div", "text": "", "attrs": {}, "children_count": 0},
					{"index": 1, "tag": "div", "text": "", "attrs": {}, "children_count": 0},
				],
				"total": 4,
				"showing": 2,
			},
		)

		result = await Tools().execute("find_elements", {"selector": "div"}, browser)

		assert result.error is None
		assert 'Found 4 elements matching "div":' in result.extracted_content  # plural
		assert "Showing 2 of 4 total elements" in result.extracted_content
		assert result.long_term_memory == 'Found 4 elements matching "div".'

	@pytest.mark.asyncio
	async def test_attributes_rendered(self):
		browser = _make_browser(
			find_return={
				"elements": [
					{
						"index": 0,
						"tag": "a",
						"text": "link",
						"attrs": {"href": "https://x/a", "class": "btn"},
						"children_count": 0,
					},
				],
				"total": 1,
				"showing": 1,
			},
		)

		result = await Tools().execute("find_elements", {"selector": "a"}, browser)

		assert '{href="https://x/a", class="btn"}' in result.extracted_content

	@pytest.mark.asyncio
	async def test_text_collapsed_and_truncated(self):
		# 200-char text with no whitespace -> collapsed unchanged, then truncated
		# to 120 chars + "..." in the formatter display layer.
		long_text = "x" * 200
		browser = _make_browser(
			find_return={
				"elements": [
					{"index": 0, "tag": "p", "text": long_text, "attrs": {}, "children_count": 0},
				],
				"total": 1,
				"showing": 1,
			},
		)

		result = await Tools().execute("find_elements", {"selector": "p"}, browser)

		# the full 200-char text must NOT appear; the truncated "..."" marker must
		assert long_text not in result.extracted_content
		assert "..." in result.extracted_content

	@pytest.mark.asyncio
	async def test_no_matches_is_soft_echo_not_error(self):
		# Soft echo (aligns with find_text / search_page / browser-use): "no
		# elements" is actionable info, NOT a tool failure. error stays None.
		browser = _make_browser(find_return={"elements": [], "total": 0, "showing": 0})

		result = await Tools().execute("find_elements", {"selector": "ghost"}, browser)

		assert result.error is None
		assert result.extracted_content == 'No elements found matching "ghost"'
		assert result.extracted_content == result.long_term_memory  # equal-value echo on miss

	@pytest.mark.asyncio
	async def test_hard_error_on_raise(self):
		# find_elements raises (invalid selector surfaced as {error} -> RuntimeError /
		# CDP drop) -> find_elements-specific error, NOT the generic Tools.execute catch.
		browser = _make_browser(
			find_raises=RuntimeError("find_elements: Invalid CSS selector: '[[[invalid'"),
		)

		result = await Tools().execute("find_elements", {"selector": "[[[invalid"}, browser)

		assert result.error == "Find elements failed: find_elements: Invalid CSS selector: '[[[invalid'"
		assert result.extracted_content is None
		assert result.is_done is False

	@pytest.mark.asyncio
	async def test_does_not_call_get_state(self):
		browser = _make_browser()

		await Tools().execute("find_elements", {"selector": "a"}, browser)

		browser.get_state.assert_not_awaited()  # find_elements must not fetch full state

	@pytest.mark.asyncio
	async def test_forwards_attributes_and_flags_as_kwargs(self):
		browser = _make_browser()

		await Tools().execute(
			"find_elements",
			{"selector": "img", "attributes": ["src"], "max_results": 10, "include_text": False},
			browser,
		)

		browser.find_elements.assert_awaited_once_with(
			"img", attributes=["src"], max_results=10, include_text=False,
		)


# ── param model ──────────────────────────────────────────────────────────────


class TestFindElementsParams:
	def test_accepts_selector_with_defaults(self):
		p = FindElementsParams(selector="a")
		assert p.selector == "a"
		assert p.attributes is None
		assert p.max_results == 50
		assert p.include_text is True

	def test_accepts_all_fields(self):
		p = FindElementsParams(
			selector="img", attributes=["src", "alt"], max_results=200, include_text=False,
		)
		assert p.attributes == ["src", "alt"]
		assert p.max_results == 200
		assert p.include_text is False

	def test_selector_required(self):
		with pytest.raises(ValidationError):
			FindElementsParams()

	def test_max_results_lower_bound(self):
		with pytest.raises(ValidationError):
			FindElementsParams(selector="a", max_results=0)

	def test_max_results_upper_bound(self):
		with pytest.raises(ValidationError):
			FindElementsParams(selector="a", max_results=201)

	def test_max_results_negative_rejected(self):
		with pytest.raises(ValidationError):
			FindElementsParams(selector="a", max_results=-1)

	def test_extra_field_forbidden(self):
		with pytest.raises(ValidationError):
			FindElementsParams(selector="a", nth=2)


# ── builder (safe injection) ─────────────────────────────────────────────────


class TestBuildFindElementsJs:
	def test_user_values_injected_as_vars(self):
		js = _build_find_elements_js("a.link", ["href", "src"], 5, True)

		assert js.startswith("(function() {")
		assert js.rstrip().endswith("})()")
		# every value declared as a var (json.dumps -> JS literal)
		assert 'var SELECTOR = "a.link";' in js
		assert 'var ATTRIBUTES = ["href", "src"];' in js
		assert "var MAX_RESULTS = 5;" in js
		assert "var INCLUDE_TEXT = true;" in js

	def test_double_quote_in_selector_escaped_not_spliced(self):
		# A selector containing a double-quote must be json-encoded (\"), never
		# spliced raw into the expression (would break / inject JS).
		js = _build_find_elements_js('a[href="x"]', None, 50, True)

		assert 'var SELECTOR = "a[href=\\"x\\"]";' in js
		# the body references the var, not the raw selector
		assert "document.querySelectorAll(SELECTOR)" in js

	def test_attributes_none_serializes_to_null(self):
		js = _build_find_elements_js("a", None, 50, True)

		assert "var ATTRIBUTES = null;" in js

	def test_include_text_false_serializes_to_false(self):
		js = _build_find_elements_js("a", None, 50, False)

		assert "var INCLUDE_TEXT = false;" in js


# ── session layer ────────────────────────────────────────────────────────────


class TestFindElementsSession:
	def _make_session(self, *, eval_return: dict | None = None) -> tuple[BrowserSession, MagicMock]:
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid-1"
		client = MagicMock()
		client.send.Runtime.evaluate = AsyncMock(
			return_value=eval_return if eval_return is not None else {"result": {"value": None}},
		)
		s.client = client
		return s, client

	@pytest.mark.asyncio
	async def test_returns_dict_on_success(self):
		payload = {
			"elements": [
				{"index": 0, "tag": "a", "text": "hi", "attrs": {"href": "/x"}, "children_count": 2},
			],
			"total": 1,
			"showing": 1,
		}
		s, client = self._make_session(eval_return={"result": {"value": payload}})

		data = await s.find_elements("a")

		assert data == payload
		# single Runtime.evaluate, returnByValue + awaitPromise, correct session
		client.send.Runtime.evaluate.assert_awaited_once()
		params = client.send.Runtime.evaluate.await_args.args[0]
		assert params["returnByValue"] is True
		assert params["awaitPromise"] is True
		assert client.send.Runtime.evaluate.await_args.kwargs == {"session_id": "sid-1"}
		# selector injected as a var (json.dumps), not f-string-spliced
		assert 'var SELECTOR = "a";' in params["expression"]

	@pytest.mark.asyncio
	async def test_raises_on_js_layer_error(self):
		# JS returns {error: ...} (invalid CSS selector)
		s, client = self._make_session(
			eval_return={"result": {"value": {"error": "Invalid CSS selector: boom"}}},
		)

		with pytest.raises(RuntimeError, match=r"^find_elements: Invalid CSS selector: boom$"):
			await s.find_elements("[[[invalid")

	@pytest.mark.asyncio
	async def test_raises_on_exception_details(self):
		# execute_js surfaces exceptionDetails as RuntimeError("JS error: ...");
		# find_elements does not swallow it.
		s, client = self._make_session(eval_return={"exceptionDetails": {"text": "SyntaxError"}})

		with pytest.raises(RuntimeError, match="JS error"):
			await s.find_elements("a")

	@pytest.mark.asyncio
	async def test_raises_on_null_return(self):
		s, client = self._make_session(eval_return={"result": {"value": None}})

		with pytest.raises(RuntimeError, match="find_elements returned no result"):
			await s.find_elements("a")

	@pytest.mark.asyncio
	async def test_forwards_attributes_and_flags(self):
		s, client = self._make_session(
			eval_return={"result": {"value": {"elements": [], "total": 0, "showing": 0}}},
		)

		await s.find_elements("img", attributes=["src"], max_results=10, include_text=False)

		params = client.send.Runtime.evaluate.await_args.args[0]
		assert 'var ATTRIBUTES = ["src"];' in params["expression"]
		assert "var MAX_RESULTS = 10;" in params["expression"]
		assert "var INCLUDE_TEXT = false;" in params["expression"]


# ── formatter ────────────────────────────────────────────────────────────────


class TestFormatFindResults:
	def test_single_element_with_text_and_attrs(self):
		out = _format_find_results(
			{
				"elements": [
					{"index": 0, "tag": "a", "text": "hi", "attrs": {"href": "/x"}, "children_count": 2},
				],
				"total": 1,
				"showing": 1,
			},
			"a",
		)

		assert 'Found 1 element matching "a":' in out  # singular
		assert '[0] <a> "hi" {href="/x"} (2 children)' in out
		assert "Showing" not in out  # no footer when not truncated

	def test_plural_elements_without_text_or_attrs(self):
		out = _format_find_results(
			{
				"elements": [
					{"index": 0, "tag": "div", "text": "", "attrs": {}, "children_count": 0},
					{"index": 1, "tag": "div", "text": "", "attrs": {}, "children_count": 0},
				],
				"total": 2,
				"showing": 2,
			},
			"div",
		)

		assert 'Found 2 elements matching "div":' in out  # plural
		assert "[0] <div> (0 children)" in out  # text="" / attrs={} omitted
		assert "[1] <div> (0 children)" in out
		assert "Showing" not in out

	def test_truncation_footer(self):
		out = _format_find_results(
			{
				"elements": [
					{"index": 0, "tag": "div", "text": "", "attrs": {}, "children_count": 0},
				],
				"total": 4,
				"showing": 1,
			},
			"div",
		)

		assert "Showing 1 of 4 total elements" in out
		assert "Increase max_results to see more." in out
