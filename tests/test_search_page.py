"""Tests for search_page: grep-style page text search via a single Runtime.evaluate.

Covers:
- action layer: a hit echoes ``Found N match(es) for "..." on page:`` with a
  compact ``long_term_memory`` summary (singular/plural); total==0 is a SOFT
  echo (``error is None``, aligns with find_text / browser-use); a raised
  ``search_page`` returns ``error="Search page failed: ..."``; the action does
  NOT call ``get_state`` and forwards regex/case_sensitive/context_chars/
  css_scope/max_results/offset/search_attributes through as kwargs; oversized
  results are tiered-saved to a file (mirrors extract); attribute-only hits are
  NOT a soft miss
- param model: ``SearchPageParams.query`` accepts non-empty, rejects empty
  (``min_length=1``), forbids extra fields; ``max_results`` bounded
  ``ge=1, le=200``; ``context_chars`` bounded ``ge=0``; ``offset`` bounded
  ``ge=0``; booleans + optional ``css_scope`` accepted
- session layer: ``BrowserSession.search_page`` runs one ``Runtime.evaluate``
  with ``returnByValue=True`` and returns the ``{matches, total, has_more}``
  dict; a JS-layer ``{error: ...}`` (invalid regex / css_scope not found)
  raises ``RuntimeError("search_page: ...")``; ``exceptionDetails`` propagates
  as ``RuntimeError("JS error: ...")``; a null return raises
  ``RuntimeError("search_page returned no result")``; offset / search_attributes
  are forwarded into the expression as ``var`` declarations
- builder: ``_build_search_page_js`` injects every user value via ``json.dumps``
  into a ``var`` declaration (no f-string interpolation of user text — a
  pattern containing a double-quote is escaped, not spliced raw), wraps the
  body in an IIFE, and serializes ``css_scope=None`` as JS ``null``
- formatter: ``_format_search_results`` renders ``Found N match(es) ...`` with
  per-match ``[i] context (in path)``, an offset-aware ``showing A–B of N ...
  offset=...`` footer when ``has_more``, and a separate ``Attribute matches``
  section when ``attribute_total > 0``
- JS body content: the raw ``_SEARCH_PAGE_JS_BODY`` recurses into open shadow
  roots (``el.shadowRoot``) and same-origin iframes (``contentDocument``), tags
  origins via ``_origin``, applies the ``OFFSET`` window, and scans attributes
  with a non-global RegExp when ``SEARCH_ATTRIBUTES``
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from tree_walker.browser.session import (
	BrowserSession,
	_SEARCH_PAGE_JS_BODY,
	_build_search_page_js,
)
from tree_walker.config import TruncationSettings
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
			"foo", regex=False, case_sensitive=False, context_chars=150, css_scope=None,
			max_results=25, offset=0, search_attributes=False,
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
		assert "showing 1–2 of 4 total matches" in result.extracted_content
		assert "offset=2 for the next batch" in result.extracted_content
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
			"price", regex=True, case_sensitive=True, context_chars=80, css_scope="div#main",
			max_results=5, offset=0, search_attributes=False,
		)

	@pytest.mark.asyncio
	async def test_forwards_offset_and_search_attributes(self):
		browser = _make_browser()

		await Tools().execute(
			"search_page",
			{"query": "x", "offset": 40, "search_attributes": True},
			browser,
		)

		browser.search_page.assert_awaited_once_with(
			"x", regex=False, case_sensitive=False, context_chars=150, css_scope=None,
			max_results=25, offset=40, search_attributes=True,
		)

	@pytest.mark.asyncio
	async def test_attribute_only_hit_is_not_soft_miss(self, tmp_path):
		# total=0 but attribute_total>0 → NOT a soft miss; attribute section rendered.
		browser = _make_browser(
			search_return={
				"matches": [],
				"total": 0,
				"has_more": False,
				"attribute_matches": [
					{"attribute": "href", "value": "https://x.example.com", "element_path": "a"},
				],
				"attribute_total": 1,
			},
		)

		result = await Tools().execute("search_page", {"query": "example.com"}, browser)

		assert result.error is None
		assert 'Attribute matches for "example.com" (1):' in result.extracted_content
		assert "@href=https://x.example.com (in a)" in result.extracted_content
		assert result.long_term_memory == (
			'Searched page for "example.com": 0 matches found. (+1 attribute match)'
		)

	@pytest.mark.asyncio
	async def test_oversized_result_saved_to_file(self, tmp_path):
		# formatted >= threshold → write file, return preview + path (mirrors extract).
		browser = _make_browser(
			search_return={
				"matches": [{"match_text": "foo", "context": "...foo...", "element_path": "p#x"}],
				"total": 1,
				"has_more": False,
			},
		)
		tools = Tools()
		tools._truncation = TruncationSettings(
			search_page_save_threshold=10, search_page_output_dir=str(tmp_path),
		)

		result = await tools.execute("search_page", {"query": "foo"}, browser)

		assert result.error is None
		saved = list(tmp_path.glob("search_page_*.txt"))
		assert len(saved) == 1
		assert result.extracted_content.startswith("Search results")
		assert "saved to" in result.extracted_content
		assert str(saved[0]) in result.extracted_content
		assert "Results saved:" in result.long_term_memory

	@pytest.mark.asyncio
	async def test_small_result_not_saved(self, tmp_path):
		# formatted < threshold → inline, no file.
		browser = _make_browser(
			search_return={
				"matches": [{"match_text": "foo", "context": "...foo...", "element_path": "p#x"}],
				"total": 1,
				"has_more": False,
			},
		)
		tools = Tools()
		tools._truncation = TruncationSettings(
			search_page_save_threshold=10_000_000, search_page_output_dir=str(tmp_path),
		)

		result = await tools.execute("search_page", {"query": "foo"}, browser)

		assert result.error is None
		assert list(tmp_path.glob("search_page_*.txt")) == []
		assert 'Found 1 match for "foo"' in result.extracted_content
		assert "Results saved:" not in (result.long_term_memory or "")

	@pytest.mark.asyncio
	async def test_save_oserror_falls_back_to_inline(self, tmp_path, monkeypatch):
		# OSError during makedirs/write is a soft warning, NOT a hard error:
		# the formatted result is returned inline instead.
		browser = _make_browser(
			search_return={
				"matches": [{"match_text": "foo", "context": "...foo...", "element_path": "p#x"}],
				"total": 1,
				"has_more": False,
			},
		)
		tools = Tools()
		tools._truncation = TruncationSettings(
			search_page_save_threshold=10, search_page_output_dir=str(tmp_path),
		)

		def boom(*args, **kwargs):
			raise OSError("disk full")

		monkeypatch.setattr("os.makedirs", boom)

		result = await tools.execute("search_page", {"query": "foo"}, browser)

		assert result.error is None
		assert 'Found 1 match for "foo"' in result.extracted_content
		assert "Results saved:" not in (result.long_term_memory or "")


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
		assert p.offset == 0
		assert p.search_attributes is False

	def test_accepts_all_fields(self):
		p = SearchPageParams(
			query="\\d+", regex=True, case_sensitive=True, context_chars=0, css_scope="main",
			max_results=200, offset=50, search_attributes=True,
		)
		assert p.regex is True
		assert p.context_chars == 0
		assert p.max_results == 200
		assert p.offset == 50
		assert p.search_attributes is True

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

	def test_offset_negative_rejected(self):
		# ge=0: negative offset is invalid
		with pytest.raises(ValidationError):
			SearchPageParams(query="hello", offset=-1)


# ── builder (safe injection) ─────────────────────────────────────────────────


class TestBuildSearchPageJs:
	def test_user_values_injected_as_vars(self):
		js = _build_search_page_js("foo", True, True, 80, "div#main", 5, 10, True)

		assert js.startswith("(function() {")
		assert js.rstrip().endswith("})()")
		# every value declared as a var (json.dumps -> JS literal)
		assert 'var PATTERN = "foo";' in js
		assert "var IS_REGEX = true;" in js
		assert "var CASE_SENSITIVE = true;" in js
		assert "var CONTEXT_CHARS = 80;" in js
		assert 'var CSS_SCOPE = "div#main";' in js
		assert "var MAX_RESULTS = 5;" in js
		assert "var OFFSET = 10;" in js
		assert "var SEARCH_ATTRIBUTES = true;" in js

	def test_defaults_offset_and_search_attributes(self):
		js = _build_search_page_js("x", False, False, 150, None, 25, 0, False)

		assert "var OFFSET = 0;" in js
		assert "var SEARCH_ATTRIBUTES = false;" in js

	def test_double_quote_in_pattern_escaped_not_spliced(self):
		# A query containing a double-quote must be json-encoded (\"), never
		# spliced raw into the expression (would break / inject JS).
		js = _build_search_page_js('a"b', False, False, 150, None, 25, 0, False)

		assert 'var PATTERN = "a\\"b";' in js
		# the body references the var, not the raw pattern
		assert "new RegExp(PATTERN" in js

	def test_none_css_scope_serializes_to_null(self):
		js = _build_search_page_js("x", False, False, 150, None, 25, 0, False)

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
	async def test_offset_forwarded_into_expression(self):
		s, client = self._make_session(eval_return={"result": {"value": {"matches": [], "total": 0}}})

		await s.search_page("x", offset=25)

		params = client.send.Runtime.evaluate.await_args.args[0]
		assert "var OFFSET = 25;" in params["expression"]

	@pytest.mark.asyncio
	async def test_search_attributes_forwarded_into_expression(self):
		s, client = self._make_session(eval_return={"result": {"value": {"matches": [], "total": 0}}})

		await s.search_page("x", search_attributes=True)

		params = client.send.Runtime.evaluate.await_args.args[0]
		assert "var SEARCH_ATTRIBUTES = true;" in params["expression"]

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

	def test_has_more_footer_no_offset(self):
		out = _format_search_results(
			{
				"matches": [{"context": "a", "element_path": ""}],
				"total": 5,
				"has_more": True,
			},
			"q",
		)

		assert "showing 1–1 of 5 total matches" in out
		assert "offset=1 for the next batch" in out
		assert "Increase max_results" not in out  # old wording removed

	def test_has_more_footer_with_offset(self):
		out = _format_search_results(
			{
				"matches": [{"context": "a", "element_path": ""}, {"context": "b", "element_path": ""}],
				"total": 10,
				"offset": 2,
				"has_more": True,
			},
			"q",
		)

		# offset=2, 2 matches → showing 3–4 of 10; next batch at offset=4
		assert "showing 3–4 of 10 total matches" in out
		assert "offset=4 for the next batch" in out

	def test_attribute_section(self):
		out = _format_search_results(
			{
				"matches": [{"context": "a", "element_path": ""}],
				"total": 1,
				"has_more": False,
				"attribute_matches": [
					{"attribute": "href", "value": "https://x.example.com", "element_path": "a"},
				],
				"attribute_total": 1,
			},
			"q",
		)

		assert 'Attribute matches for "q" (1):' in out
		assert "@href=https://x.example.com (in a)" in out

	def test_attribute_section_truncated_footer(self):
		out = _format_search_results(
			{
				"matches": [],
				"total": 0,
				"has_more": False,
				"attribute_matches": [{"attribute": "value", "value": "q", "element_path": "input"}],
				"attribute_total": 9,
			},
			"q",
		)

		assert "showing 1 of 9 attribute matches" in out


# ── JS body content (shadow / iframe / offset / attribute machinery) ─────────


class TestSearchPageJsBody:
	"""The raw JS body cannot be executed without a browser, so we assert on its
	string content — the shadow-DOM / iframe recursion, origin tagging, offset
	window, and attribute scan must all be present."""

	def test_recurses_shadow_dom(self):
		assert "el.shadowRoot" in _SEARCH_PAGE_JS_BODY

	def test_recurses_same_origin_iframe(self):
		assert "el.contentDocument" in _SEARCH_PAGE_JS_BODY
		assert "IFRAME" in _SEARCH_PAGE_JS_BODY

	def test_origin_tagging_helper(self):
		assert "function _origin(node)" in _SEARCH_PAGE_JS_BODY
		assert "(in shadow DOM)" in _SEARCH_PAGE_JS_BODY
		assert "(in iframe)" in _SEARCH_PAGE_JS_BODY

	def test_collect_text_helper(self):
		assert "function _collectText(root)" in _SEARCH_PAGE_JS_BODY

	def test_offset_window(self):
		# the match-loop store condition keys on OFFSET
		assert "total - 1 >= OFFSET" in _SEARCH_PAGE_JS_BODY
		assert "var OFFSET" not in _SEARCH_PAGE_JS_BODY  # var is injected by builder, not in body

	def test_attribute_scan_uses_non_global_regexp(self):
		assert "var reAttr" in _SEARCH_PAGE_JS_BODY
		# non-global flags ('' or 'i') — NOT the 'g' used for the text exec loop
		assert "new RegExp(re.source" in _SEARCH_PAGE_JS_BODY

	def test_return_shape_includes_phase2_keys(self):
		assert "offset: OFFSET" in _SEARCH_PAGE_JS_BODY
		assert "attribute_matches: attribute_matches" in _SEARCH_PAGE_JS_BODY
		assert "attribute_total: attribute_total" in _SEARCH_PAGE_JS_BODY
