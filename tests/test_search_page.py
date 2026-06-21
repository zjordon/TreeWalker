"""Tests for search_page: grep-style page text search via a single Runtime.evaluate.

Covers:
- action layer: a hit echoes ``Found N match(es) for "..." on page:`` with a
  compact ``long_term_memory`` summary (singular/plural); total==0 is a SOFT
  echo (``error is None``, aligns with find_text / browser-use); a raised
  ``search_page`` returns ``error="Search page failed: ..."``; the action does
  NOT call ``get_state`` and forwards regex/case_sensitive/context_chars/
  css_scope/max_results through as kwargs
- param model: ``SearchPageParams.query`` accepts non-empty, rejects empty
  (``min_length=1``), forbids extra fields; ``max_results`` bounded
  ``ge=1, le=200``; ``context_chars`` bounded ``ge=0``; booleans + optional
  ``css_scope`` accepted
- session layer: ``BrowserSession.search_page`` runs one ``Runtime.evaluate``
  with ``returnByValue=True`` and returns the ``{matches, total, has_more}``
  dict; a JS-layer ``{error: ...}`` (invalid regex / css_scope not found)
  raises ``RuntimeError("search_page: ...")``; ``exceptionDetails`` propagates
  as ``RuntimeError("JS error: ...")``; a null return raises
  ``RuntimeError("search_page returned no result")``
- builder: ``_build_search_page_js`` injects every user value via ``json.dumps``
  into a ``var`` declaration (no f-string interpolation of user text — a
  pattern containing a double-quote is escaped, not spliced raw), wraps the
  body in an IIFE, and serializes ``css_scope=None`` as JS ``null``
- formatter: ``_format_search_results`` renders ``Found N match(es) ...`` with
  per-match ``[i] context (in path)`` and a ``showing K of N total`` footer
  when ``has_more``
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from tree_walker.browser.session import BrowserSession, _build_search_page_js
from tree_walker.tools.actions import Tools, _format_search_results
from tree_walker.tools.models import SearchPageParams


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_browser(
	*, search_return: dict | None = None, search_raises: Exception | None = None,
) -> MagicMock:
	"""Stub BrowserSession: search_page returns a result dict (or raises).

	get_state is an AsyncMock purely so we can assert it is NOT awaited
	(search_page must not trigger a full DOM fetch).
	"""
	bs = MagicMock()
	if search_raises:
		bs.search_page = AsyncMock(side_effect=search_raises)
	else:
		bs.search_page = AsyncMock(
			return_value=search_return or {
				"matches": [{"match_text": "foo", "context": "...foo...", "element_path": "p#x"}],
				"total": 1,
				"has_more": False,
			},
		)
	bs.get_state = AsyncMock()
	return bs


# ── action layer ─────────────────────────────────────────────────────────────


class TestSearchPageAction:
	@pytest.mark.asyncio
	async def test_hit_echoes_formatted_results_singular(self):
		browser = _make_browser(
			search_return={
				"matches": [
					{"match_text": "foo", "context": "...foo...", "element_path": "p#x"},
				],
				"total": 1,
				"has_more": False,
			},
		)

		result = await Tools().execute("search_page", {"query": "foo"}, browser)

		assert result.error is None
		assert result.is_done is False
		assert 'Found 1 match for "foo" on page:' in result.extracted_content
		assert "[1] ...foo... (in p#x)" in result.extracted_content
		# compact summary memory (singular), NOT the full match list
		assert result.long_term_memory == 'Searched page for "foo": 1 match found.'
		assert result.long_term_memory != result.extracted_content
		browser.search_page.assert_awaited_once_with(
			"foo", regex=False, case_sensitive=False, context_chars=150, css_scope=None, max_results=25,
		)

	@pytest.mark.asyncio
	async def test_hit_plural_matches_memory(self):
		browser = _make_browser(
			search_return={
				"matches": [
					{"match_text": "bar", "context": "bar", "element_path": ""},
					{"match_text": "bar", "context": "bar", "element_path": "div.a"},
				],
				"total": 4,
				"has_more": True,
			},
		)

		result = await Tools().execute("search_page", {"query": "bar"}, browser)

		assert result.error is None
		assert 'Found 4 matches for "bar" on page:' in result.extracted_content
		assert "showing 2 of 4 total matches" in result.extracted_content
		assert result.long_term_memory == 'Searched page for "bar": 4 matches found.'

	@pytest.mark.asyncio
	async def test_no_matches_is_soft_echo_not_error(self):
		# Soft echo (aligns with browser-use + find_text): "no matches" is
		# actionable info, NOT a tool failure. error stays None.
		browser = _make_browser(search_return={"matches": [], "total": 0, "has_more": False})

		result = await Tools().execute("search_page", {"query": "ghost"}, browser)

		assert result.error is None
		assert result.extracted_content == "No matches for 'ghost'"
		assert result.extracted_content == result.long_term_memory

	@pytest.mark.asyncio
	async def test_hard_error_on_raise(self):
		# search_page raises (invalid regex / css_scope not found / CDP error) ->
		# search_page-specific error, NOT the generic Tools.execute catch.
		browser = _make_browser(search_raises=RuntimeError("search_page: Invalid regex pattern: *"))

		result = await Tools().execute("search_page", {"query": "*"}, browser)

		assert result.error == "Search page failed: search_page: Invalid regex pattern: *"
		assert result.extracted_content is None

	@pytest.mark.asyncio
	async def test_does_not_call_get_state(self):
		browser = _make_browser()

		await Tools().execute("search_page", {"query": "x"}, browser)

		browser.get_state.assert_not_awaited()  # search_page must not fetch full state

	@pytest.mark.asyncio
	async def test_forwards_all_params_as_kwargs(self):
		browser = _make_browser()

		await Tools().execute(
			"search_page",
			{
				"query": "price",
				"regex": True,
				"case_sensitive": True,
				"context_chars": 80,
				"css_scope": "div#main",
				"max_results": 5,
			},
			browser,
		)

		browser.search_page.assert_awaited_once_with(
			"price", regex=True, case_sensitive=True, context_chars=80, css_scope="div#main", max_results=5,
		)


# ── param model ──────────────────────────────────────────────────────────────


class TestSearchPageParams:
	def test_accepts_query_with_defaults(self):
		p = SearchPageParams(query="hello")
		assert p.query == "hello"
		assert p.regex is False
		assert p.case_sensitive is False
		assert p.context_chars == 150
		assert p.css_scope is None
		assert p.max_results == 25

	def test_accepts_all_fields(self):
		p = SearchPageParams(
			query="\\d+", regex=True, case_sensitive=True, context_chars=0, css_scope="main", max_results=200,
		)
		assert p.regex is True
		assert p.context_chars == 0
		assert p.max_results == 200

	def test_rejects_empty_query(self):
		# min_length=1: empty query would match every line
		with pytest.raises(ValidationError):
			SearchPageParams(query="")

	def test_extra_field_forbidden(self):
		with pytest.raises(ValidationError):
			SearchPageParams(query="hello", nth=2)

	def test_max_results_lower_bound(self):
		with pytest.raises(ValidationError):
			SearchPageParams(query="hello", max_results=0)

	def test_max_results_upper_bound(self):
		with pytest.raises(ValidationError):
			SearchPageParams(query="hello", max_results=201)

	def test_context_chars_negative_rejected(self):
		with pytest.raises(ValidationError):
			SearchPageParams(query="hello", context_chars=-1)


# ── builder (safe injection) ─────────────────────────────────────────────────


class TestBuildSearchPageJs:
	def test_user_values_injected_as_vars(self):
		js = _build_search_page_js("foo", True, True, 80, "div#main", 5)

		assert js.startswith("(function() {")
		assert js.rstrip().endswith("})()")
		# every value declared as a var (json.dumps -> JS literal)
		assert 'var PATTERN = "foo";' in js
		assert "var IS_REGEX = true;" in js
		assert "var CASE_SENSITIVE = true;" in js
		assert "var CONTEXT_CHARS = 80;" in js
		assert 'var CSS_SCOPE = "div#main";' in js
		assert "var MAX_RESULTS = 5;" in js

	def test_double_quote_in_pattern_escaped_not_spliced(self):
		# A query containing a double-quote must be json-encoded (\"), never
		# spliced raw into the expression (would break / inject JS).
		js = _build_search_page_js('a"b', False, False, 150, None, 25)

		assert 'var PATTERN = "a\\"b";' in js
		# the body references the var, not the raw pattern
		assert "new RegExp(PATTERN" in js

	def test_none_css_scope_serializes_to_null(self):
		js = _build_search_page_js("x", False, False, 150, None, 25)

		assert "var CSS_SCOPE = null;" in js


# ── session layer ────────────────────────────────────────────────────────────


class TestSearchPageSession:
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
			"matches": [{"match_text": "foo", "context": "...foo...", "element_path": "p#x"}],
			"total": 1,
			"has_more": False,
		}
		s, client = self._make_session(eval_return={"result": {"value": payload}})

		data = await s.search_page("foo")

		assert data == payload
		# single Runtime.evaluate, returnByValue + awaitPromise, correct session
		client.send.Runtime.evaluate.assert_awaited_once()
		params = client.send.Runtime.evaluate.await_args.args[0]
		assert params["returnByValue"] is True
		assert params["awaitPromise"] is True
		assert client.send.Runtime.evaluate.await_args.kwargs == {"session_id": "sid-1"}
		# pattern injected as a var (json.dumps), not f-string-spliced
		assert 'var PATTERN = "foo";' in params["expression"]

	@pytest.mark.asyncio
	async def test_raises_on_js_layer_error(self):
		# JS returns {error: ...} (invalid regex / css_scope not found)
		s, client = self._make_session(
			eval_return={"result": {"value": {"error": "Invalid regex pattern: boom"}}},
		)

		with pytest.raises(RuntimeError, match=r"^search_page: Invalid regex pattern: boom$"):
			await s.search_page("*")

	@pytest.mark.asyncio
	async def test_raises_on_exception_details(self):
		# execute_js surfaces exceptionDetails as RuntimeError("JS error: ...");
		# search_page does not swallow it.
		s, client = self._make_session(eval_return={"exceptionDetails": {"text": "SyntaxError"}})

		with pytest.raises(RuntimeError, match="JS error"):
			await s.search_page("x")

	@pytest.mark.asyncio
	async def test_raises_on_null_return(self):
		s, client = self._make_session(eval_return={"result": {"value": None}})

		with pytest.raises(RuntimeError, match="search_page returned no result"):
			await s.search_page("x")

	@pytest.mark.asyncio
	async def test_css_scope_not_found_is_error(self):
		s, client = self._make_session(
			eval_return={
				"result": {"value": {"error": "CSS scope selector not found: .nope"}},
			},
		)

		with pytest.raises(RuntimeError, match="CSS scope selector not found"):
			await s.search_page("x", css_scope=".nope")


# ── formatter ────────────────────────────────────────────────────────────────


class TestFormatSearchResults:
	def test_zero_total(self):
		out = _format_search_results({"matches": [], "total": 0, "has_more": False}, "q")

		assert out == 'Found 0 matches for "q" on page:\n'

	def test_single_match_with_path(self):
		out = _format_search_results(
			{
				"matches": [{"context": "ctx", "element_path": "p#x"}],
				"total": 1,
				"has_more": False,
			},
			"q",
		)

		assert 'Found 1 match for "q" on page:' in out  # singular
		assert "[1] ctx (in p#x)" in out
		assert "showing" not in out

	def test_multiple_matches_without_path(self):
		out = _format_search_results(
			{
				"matches": [
					{"context": "a", "element_path": ""},
					{"context": "b", "element_path": ""},
				],
				"total": 2,
				"has_more": False,
			},
			"q",
		)

		assert 'Found 2 matches for "q" on page:' in out  # plural
		assert "[1] a" in out
		assert "[2] b" in out
		assert "(in " not in out

	def test_has_more_footer(self):
		out = _format_search_results(
			{
				"matches": [{"context": "a", "element_path": ""}],
				"total": 5,
				"has_more": True,
			},
			"q",
		)

		assert "showing 1 of 5 total matches" in out
		assert "Increase max_results to see more." in out
