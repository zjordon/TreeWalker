"""Tests for find_elements: CSS selector element query via a single Runtime.evaluate.

Phase 1 (commit a34a1f9) covered the core querySelectorAll path. These tests
extend coverage to **Phase 2** (find_elements_follow_up.md):

- action layer: a hit echoes ``Found N element(s) matching "..."`` with a
  compact ``long_term_memory`` summary (singular/plural); an offset-aware
  ``... showing A–B of N total elements`` footer when ``has_more``; ``total==0``
  is a SOFT echo (``error is None``); a raised ``find_elements`` /
  ``find_elements_node_ids`` returns ``error="Find elements failed: ..."``; the
  action does NOT call ``get_state`` and forwards all flags through as kwargs;
  **first_only** caps at one; **oversized results spillover to a file**;
  **return_node_ids** resolves backend ids via DOM.performSearch.
- param model: ``selector`` required; ``attributes`` optional; ``max_results``
  bounded ``ge=1, le=200`` (default 50); ``offset`` ``ge=0`` (default 0);
  ``include_text``/``first_only``/``include_geometry``/``return_node_ids`` bools;
  extra fields forbidden.
- builder: ``_build_find_elements_js`` injects every user value via
  ``json.dumps`` into a ``var`` declaration (a selector containing a double-quote
  is escaped, not spliced raw), wraps the body in an IIFE, and serializes
  ``attributes=None`` as JS ``null``.
- session layer: ``find_elements`` runs one ``Runtime.evaluate`` and returns the
  ``{elements, total, showing, offset, has_more}`` dict; JS-layer ``{error}`` /
  ``exceptionDetails`` / null return raise ``RuntimeError``. ``find_elements_node_ids``
  runs the ``DOM.performSearch`` → ``getSearchResults`` → ``describeNode`` chain
  and discards the search id in a finally.
- formatter: ``_format_find_results`` renders per-element
  ``[i] <tag> "text" {k="v"} (N children)`` + optional origin / geometry + an
  offset-aware footer; ``_format_node_id_results`` renders ``[backend_id] <tag>``.
- JS body (string assertions, mirrors TestSearchPageJsBody): open-shadow-root +
  same-origin-iframe recursion, origin tagging, offset window, geometry/visible,
  selector validation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from tree_walker.browser.session import (
	BrowserSession,
	_FIND_ELEMENTS_JS_BODY,
	_build_find_elements_js,
)
from tree_walker.config import TruncationSettings
from tree_walker.tools.actions import (
	Tools,
	_format_find_results,
	_format_node_id_results,
)
from tree_walker.tools.models import FindElementsParams


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_browser(
	*,
	find_return: dict | None = None,
	find_raises: Exception | None = None,
	node_ids_return: dict | None = None,
	node_ids_raises: Exception | None = None,
) -> MagicMock:
	"""Stub BrowserSession.

	find_elements / find_elements_node_ids return a result dict (or raise).
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
					{"index": 0, "tag": "a", "text": "hi", "attrs": {"href": "/x"},
					 "children_count": 2, "origin": ""},
				],
				"total": 1,
				"showing": 1,
				"offset": 0,
				"has_more": False,
			},
		)
	if node_ids_raises:
		bs.find_elements_node_ids = AsyncMock(side_effect=node_ids_raises)
	else:
		bs.find_elements_node_ids = AsyncMock(
			return_value=node_ids_return or {
				"node_ids": [{"backend_id": 123, "tag": "button"}],
				"total": 1,
				"showing": 1,
				"offset": 0,
				"has_more": False,
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
					{"index": 0, "tag": "a", "text": "hi", "attrs": {"href": "/x"},
					 "children_count": 2, "origin": ""},
				],
				"total": 1,
				"showing": 1,
				"offset": 0,
				"has_more": False,
			},
		)

		result = await Tools().execute("find_elements", {"selector": "a"}, browser)

		assert result.error is None
		assert result.is_done is False
		assert 'Found 1 element matching "a":' in result.extracted_content  # singular
		assert '[0] <a> "hi" {href="/x"} (2 children)' in result.extracted_content
		assert "showing" not in result.extracted_content  # no footer when has_more False
		assert result.long_term_memory == 'Found 1 element matching "a".'
		assert result.long_term_memory != result.extracted_content
		browser.find_elements.assert_awaited_once_with(
			"a", attributes=None, max_results=50, offset=0,
			include_text=True, first_only=False, include_geometry=False,
		)
		# node_ids path NOT taken when return_node_ids is default False
		browser.find_elements_node_ids.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_hit_plural_with_offset_footer(self):
		browser = _make_browser(
			find_return={
				"elements": [
					{"index": 0, "tag": "div", "text": "", "attrs": {},
					 "children_count": 0, "origin": ""},
					{"index": 1, "tag": "div", "text": "", "attrs": {},
					 "children_count": 0, "origin": ""},
				],
				"total": 4,
				"showing": 2,
				"offset": 0,
				"has_more": True,
			},
		)

		result = await Tools().execute("find_elements", {"selector": "div"}, browser)

		assert result.error is None
		assert 'Found 4 elements matching "div":' in result.extracted_content  # plural
		assert "of 4 total elements" in result.extracted_content
		assert "offset=2" in result.extracted_content  # next-page hint
		assert result.long_term_memory == 'Found 4 elements matching "div".'

	@pytest.mark.asyncio
	async def test_attributes_rendered(self):
		browser = _make_browser(
			find_return={
				"elements": [
					{"index": 0, "tag": "a", "text": "link",
					 "attrs": {"href": "https://x/a", "class": "btn"},
					 "children_count": 0, "origin": ""},
				],
				"total": 1, "showing": 1, "offset": 0, "has_more": False,
			},
		)

		result = await Tools().execute("find_elements", {"selector": "a"}, browser)

		assert '{href="https://x/a", class="btn"}' in result.extracted_content

	@pytest.mark.asyncio
	async def test_text_collapsed_and_truncated(self):
		long_text = "x" * 200
		browser = _make_browser(
			find_return={
				"elements": [
					{"index": 0, "tag": "p", "text": long_text, "attrs": {},
					 "children_count": 0, "origin": ""},
				],
				"total": 1, "showing": 1, "offset": 0, "has_more": False,
			},
		)

		result = await Tools().execute("find_elements", {"selector": "p"}, browser)

		assert long_text not in result.extracted_content
		assert "..." in result.extracted_content

	@pytest.mark.asyncio
	async def test_no_matches_is_soft_echo_not_error(self):
		browser = _make_browser(
			find_return={"elements": [], "total": 0, "showing": 0, "offset": 0, "has_more": False},
		)

		result = await Tools().execute("find_elements", {"selector": "ghost"}, browser)

		assert result.error is None
		assert result.extracted_content == 'No elements found matching "ghost"'
		assert result.extracted_content == result.long_term_memory  # equal-value echo on miss

	@pytest.mark.asyncio
	async def test_hard_error_on_raise(self):
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

		browser.get_state.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_forwards_attributes_and_flags_as_kwargs(self):
		browser = _make_browser()

		await Tools().execute(
			"find_elements",
			{"selector": "img", "attributes": ["src"], "max_results": 10, "include_text": False},
			browser,
		)

		browser.find_elements.assert_awaited_once_with(
			"img", attributes=["src"], max_results=10, offset=0,
			include_text=False, first_only=False, include_geometry=False,
		)

	@pytest.mark.asyncio
	async def test_first_only_forwarded(self):
		browser = _make_browser(
			find_return={
				"elements": [
					{"index": 0, "tag": "a", "text": "first", "attrs": {},
					 "children_count": 0, "origin": ""},
				],
				"total": 5, "showing": 1, "offset": 0, "has_more": True,
			},
		)

		result = await Tools().execute(
			"find_elements", {"selector": "a", "first_only": True}, browser,
		)

		browser.find_elements.assert_awaited_once_with(
			"a", attributes=None, max_results=50, offset=0,
			include_text=True, first_only=True, include_geometry=False,
		)
		# first_only still reports the full total + footer so the LLM knows there are more
		assert 'Found 5 elements matching "a":' in result.extracted_content
		assert "of 5 total elements" in result.extracted_content

	@pytest.mark.asyncio
	async def test_offset_and_geometry_forwarded(self):
		browser = _make_browser()

		await Tools().execute(
			"find_elements",
			{"selector": "div", "offset": 20, "include_geometry": True},
			browser,
		)

		browser.find_elements.assert_awaited_once_with(
			"div", attributes=None, max_results=50, offset=20,
			include_text=True, first_only=False, include_geometry=True,
		)

	@pytest.mark.asyncio
	async def test_oversized_result_saved_to_file(self, tmp_path):
		# formatted >= threshold → write file, return preview + path (mirrors search_page).
		long_text = "y" * 500
		browser = _make_browser(
			find_return={
				"elements": [
					{"index": 0, "tag": "p", "text": long_text, "attrs": {},
					 "children_count": 0, "origin": ""},
				],
				"total": 1, "showing": 1, "offset": 0, "has_more": False,
			},
		)
		tools = Tools()
		tools._truncation = TruncationSettings(
			find_elements_save_threshold=10, find_elements_output_dir=str(tmp_path),
		)

		result = await tools.execute("find_elements", {"selector": "p"}, browser)

		assert result.error is None
		saved = list(tmp_path.glob("find_elements_*.txt"))
		assert len(saved) == 1
		assert result.extracted_content.startswith("Find results")
		assert "saved to" in result.extracted_content
		assert str(saved[0]) in result.extracted_content
		assert "Results saved:" in result.long_term_memory

	@pytest.mark.asyncio
	async def test_small_result_not_saved(self, tmp_path):
		browser = _make_browser()
		tools = Tools()
		tools._truncation = TruncationSettings(
			find_elements_save_threshold=100000, find_elements_output_dir=str(tmp_path),
		)

		result = await tools.execute("find_elements", {"selector": "a"}, browser)

		assert list(tmp_path.glob("find_elements_*.txt")) == []
		assert "saved to" not in (result.extracted_content or "")

	@pytest.mark.asyncio
	async def test_save_oserror_falls_back_to_inline(self, tmp_path, monkeypatch):
		# OSError during save must NOT fail the tool — fall back to inline result + warning.
		long_text = "y" * 500
		browser = _make_browser(
			find_return={
				"elements": [
					{"index": 0, "tag": "p", "text": long_text, "attrs": {},
					 "children_count": 0, "origin": ""},
				],
				"total": 1, "showing": 1, "offset": 0, "has_more": False,
			},
		)
		tools = Tools()
		tools._truncation = TruncationSettings(
			find_elements_save_threshold=10, find_elements_output_dir=str(tmp_path),
		)
		monkeypatch.setattr("os.makedirs", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))

		result = await tools.execute("find_elements", {"selector": "p"}, browser)

		assert result.error is None  # degraded gracefully, not a hard failure
		assert "saved to" not in (result.extracted_content or "")
		assert "Found 1 element" in result.extracted_content  # inline fallback
		assert list(tmp_path.glob("find_elements_*.txt")) == []

	@pytest.mark.asyncio
	async def test_return_node_ids_echoes_backend_ids(self):
		browser = _make_browser(
			node_ids_return={
				"node_ids": [
					{"backend_id": 111, "tag": "button"},
					{"backend_id": 222, "tag": "a"},
				],
				"total": 2, "showing": 2, "offset": 0, "has_more": False,
			},
		)

		result = await Tools().execute(
			"find_elements", {"selector": "button", "return_node_ids": True}, browser,
		)

		assert result.error is None
		browser.find_elements_node_ids.assert_awaited_once_with(
			"button", max_results=50, offset=0,
		)
		browser.find_elements.assert_not_awaited()  # JS path skipped
		assert "[111] <button>" in result.extracted_content
		assert "[222] <a>" in result.extracted_content
		assert "index= or element_id=" in result.extracted_content
		assert "(node ids)" in result.long_term_memory

	@pytest.mark.asyncio
	async def test_return_node_ids_zero_is_soft_echo(self):
		browser = _make_browser(
			node_ids_return={"node_ids": [], "total": 0, "showing": 0, "offset": 0, "has_more": False},
		)

		result = await Tools().execute(
			"find_elements", {"selector": "x", "return_node_ids": True}, browser,
		)

		assert result.error is None
		assert result.extracted_content == 'No elements found matching "x"'

	@pytest.mark.asyncio
	async def test_return_node_ids_hard_error_on_raise(self):
		browser = _make_browser(
			node_ids_raises=RuntimeError("DOM.performSearch failed: boom"),
		)

		result = await Tools().execute(
			"find_elements", {"selector": "x", "return_node_ids": True}, browser,
		)

		assert result.error == "Find elements failed: DOM.performSearch failed: boom"
		assert result.extracted_content is None


# ── param model ──────────────────────────────────────────────────────────────


class TestFindElementsParams:
	def test_accepts_selector_with_defaults(self):
		p = FindElementsParams(selector="a")
		assert p.selector == "a"
		assert p.attributes is None
		assert p.max_results == 50
		assert p.offset == 0
		assert p.include_text is True
		assert p.first_only is False
		assert p.include_geometry is False
		assert p.return_node_ids is False

	def test_accepts_all_fields(self):
		p = FindElementsParams(
			selector="img", attributes=["src", "alt"], max_results=200, offset=5,
			include_text=False, first_only=True, include_geometry=True, return_node_ids=True,
		)
		assert p.attributes == ["src", "alt"]
		assert p.max_results == 200
		assert p.offset == 5
		assert p.include_text is False
		assert p.first_only is True
		assert p.include_geometry is True
		assert p.return_node_ids is True

	def test_selector_required(self):
		with pytest.raises(ValidationError):
			FindElementsParams()

	def test_max_results_lower_bound(self):
		with pytest.raises(ValidationError):
			FindElementsParams(selector="a", max_results=0)

	def test_max_results_upper_bound(self):
		with pytest.raises(ValidationError):
			FindElementsParams(selector="a", max_results=201)

	def test_offset_negative_rejected(self):
		with pytest.raises(ValidationError):
			FindElementsParams(selector="a", offset=-1)

	def test_extra_field_forbidden(self):
		with pytest.raises(ValidationError):
			FindElementsParams(selector="a", nth=2)


# ── builder (safe injection) ─────────────────────────────────────────────────


class TestBuildFindElementsJs:
	def test_user_values_injected_as_vars(self):
		js = _build_find_elements_js("a.link", ["href", "src"], 5, True, False, 0, False)

		assert js.startswith("(function() {")
		assert js.rstrip().endswith("})()")
		assert 'var SELECTOR = "a.link";' in js
		assert 'var ATTRIBUTES = ["href", "src"];' in js
		assert "var MAX_RESULTS = 5;" in js
		assert "var OFFSET = 0;" in js
		assert "var INCLUDE_TEXT = true;" in js
		assert "var FIRST_ONLY = false;" in js
		assert "var INCLUDE_GEOMETRY = false;" in js

	def test_offset_and_geometry_injected(self):
		js = _build_find_elements_js("div", None, 50, True, False, 30, True)

		assert "var OFFSET = 30;" in js
		assert "var INCLUDE_GEOMETRY = true;" in js

	def test_double_quote_in_selector_escaped_not_spliced(self):
		js = _build_find_elements_js('a[href="x"]', None, 50, True, False, 0, False)

		assert 'var SELECTOR = "a[href=\\"x\\"]";' in js
		# body validates + matches via the var, never splices the raw selector
		assert "document.querySelector(SELECTOR)" in js
		assert "matches(SELECTOR)" in js

	def test_attributes_none_serializes_to_null(self):
		js = _build_find_elements_js("a", None, 50, True, False, 0, False)

		assert "var ATTRIBUTES = null;" in js


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
				{"index": 0, "tag": "a", "text": "hi", "attrs": {"href": "/x"},
				 "children_count": 2, "origin": ""},
			],
			"total": 1, "showing": 1, "offset": 0, "has_more": False,
		}
		s, client = self._make_session(eval_return={"result": {"value": payload}})

		data = await s.find_elements("a")

		assert data == payload
		client.send.Runtime.evaluate.assert_awaited_once()
		params = client.send.Runtime.evaluate.await_args.args[0]
		assert params["returnByValue"] is True
		assert params["awaitPromise"] is True
		assert client.send.Runtime.evaluate.await_args.kwargs == {"session_id": "sid-1"}
		assert 'var SELECTOR = "a";' in params["expression"]

	@pytest.mark.asyncio
	async def test_raises_on_js_layer_error(self):
		s, client = self._make_session(
			eval_return={"result": {"value": {"error": "Invalid CSS selector: boom"}}},
		)

		with pytest.raises(RuntimeError, match=r"^find_elements: Invalid CSS selector: boom$"):
			await s.find_elements("[[[invalid")

	@pytest.mark.asyncio
	async def test_raises_on_exception_details(self):
		s, client = self._make_session(eval_return={"exceptionDetails": {"text": "SyntaxError"}})

		with pytest.raises(RuntimeError, match="JS error"):
			await s.find_elements("a")

	@pytest.mark.asyncio
	async def test_raises_on_null_return(self):
		s, client = self._make_session(eval_return={"result": {"value": None}})

		with pytest.raises(RuntimeError, match="find_elements returned no result"):
			await s.find_elements("a")

	@pytest.mark.asyncio
	async def test_forwards_attributes_offset_and_geometry(self):
		s, client = self._make_session(
			eval_return={"result": {"value": {"elements": [], "total": 0, "showing": 0}}},
		)

		await s.find_elements(
			"img", attributes=["src"], max_results=10, offset=7,
			include_text=False, first_only=True, include_geometry=True,
		)

		params = client.send.Runtime.evaluate.await_args.args[0]
		assert 'var ATTRIBUTES = ["src"];' in params["expression"]
		assert "var MAX_RESULTS = 10;" in params["expression"]
		assert "var OFFSET = 7;" in params["expression"]
		assert "var INCLUDE_TEXT = false;" in params["expression"]
		assert "var FIRST_ONLY = true;" in params["expression"]
		assert "var INCLUDE_GEOMETRY = true;" in params["expression"]


class TestFindElementsNodeIdsSession:
	"""DOM.performSearch → getSearchResults → describeNode → backendNodeId chain."""

	def _make_session(self, client: MagicMock) -> BrowserSession:
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid-1"
		s.client = client
		return s

	@pytest.mark.asyncio
	async def test_returns_backend_ids(self):
		client = MagicMock()
		client.send.DOM.performSearch = AsyncMock(return_value={"searchId": "s1", "resultCount": 2})
		client.send.DOM.getSearchResults = AsyncMock(return_value={"nodeIds": [10, 20]})
		client.send.DOM.describeNode = AsyncMock(side_effect=[
			{"node": {"backendNodeId": 111, "nodeName": "BUTTON"}},
			{"node": {"backendNodeId": 222, "nodeName": "A"}},
		])
		client.send.DOM.discardSearchResults = AsyncMock(return_value={})
		s = self._make_session(client)

		data = await s.find_elements_node_ids("button")

		assert data == {
			"node_ids": [
				{"backend_id": 111, "tag": "button"},
				{"backend_id": 222, "tag": "a"},
			],
			"total": 2, "showing": 2, "offset": 0, "has_more": False,
		}
		# performSearch gets the CSS selector verbatim as the query
		ps = client.send.DOM.performSearch.await_args.args[0]
		assert ps["query"] == "button"
		assert ps["includeUserAgentShadowDOM"] is True
		# search id discarded exactly once (cleanup)
		client.send.DOM.discardSearchResults.assert_awaited_once()
		discard = client.send.DOM.discardSearchResults.await_args.args[0]
		assert discard["searchId"] == "s1"

	@pytest.mark.asyncio
	async def test_zero_results_short_circuits(self):
		client = MagicMock()
		client.send.DOM.performSearch = AsyncMock(return_value={"searchId": "s1", "resultCount": 0})
		client.send.DOM.getSearchResults = AsyncMock(return_value={"nodeIds": []})
		client.send.DOM.discardSearchResults = AsyncMock(return_value={})
		s = self._make_session(client)

		data = await s.find_elements_node_ids(".nope")

		assert data == {"node_ids": [], "total": 0, "showing": 0, "offset": 0, "has_more": False}
		client.send.DOM.getSearchResults.assert_not_awaited()  # short-circuit before fetch
		client.send.DOM.discardSearchResults.assert_awaited_once()  # still cleaned up

	@pytest.mark.asyncio
	async def test_offset_window_and_has_more(self):
		client = MagicMock()
		client.send.DOM.performSearch = AsyncMock(return_value={"searchId": "s1", "resultCount": 5})
		client.send.DOM.getSearchResults = AsyncMock(return_value={"nodeIds": [30, 40]})
		client.send.DOM.describeNode = AsyncMock(side_effect=[
			{"node": {"backendNodeId": 333, "nodeName": "A"}},
			{"node": {"backendNodeId": 444, "nodeName": "A"}},
		])
		client.send.DOM.discardSearchResults = AsyncMock(return_value={})
		s = self._make_session(client)

		data = await s.find_elements_node_ids("a", max_results=2, offset=2)

		gs = client.send.DOM.getSearchResults.await_args.args[0]
		assert gs["fromIndex"] == 2
		assert gs["toIndex"] == 4  # min(5, 2+2)
		assert data["offset"] == 2
		assert data["has_more"] is True  # 2 + 2 < 5

	@pytest.mark.asyncio
	async def test_discard_failure_swallowed(self):
		# discardSearchResults raising must not poison the returned result.
		client = MagicMock()
		client.send.DOM.performSearch = AsyncMock(return_value={"searchId": "s1", "resultCount": 1})
		client.send.DOM.getSearchResults = AsyncMock(return_value={"nodeIds": [10]})
		client.send.DOM.describeNode = AsyncMock(
			return_value={"node": {"backendNodeId": 999, "nodeName": "DIV"}},
		)
		client.send.DOM.discardSearchResults = AsyncMock(side_effect=RuntimeError("boom"))
		s = self._make_session(client)

		data = await s.find_elements_node_ids("div")

		assert data["node_ids"] == [{"backend_id": 999, "tag": "div"}]


# ── formatter ────────────────────────────────────────────────────────────────


class TestFormatFindResults:
	def test_single_element_with_text_and_attrs(self):
		out = _format_find_results(
			{
				"elements": [
					{"index": 0, "tag": "a", "text": "hi", "attrs": {"href": "/x"},
					 "children_count": 2, "origin": ""},
				],
				"total": 1, "showing": 1, "offset": 0, "has_more": False,
			},
			"a",
		)

		assert 'Found 1 element matching "a":' in out
		assert '[0] <a> "hi" {href="/x"} (2 children)' in out
		assert "showing" not in out

	def test_origin_appended_when_present(self):
		out = _format_find_results(
			{
				"elements": [
					{"index": 0, "tag": "button", "text": "ok", "attrs": {},
					 "children_count": 0, "origin": " (in shadow DOM)"},
				],
				"total": 1, "showing": 1, "offset": 0, "has_more": False,
			},
			"button",
		)

		assert "in shadow DOM" in out
		assert "[0] <button>" in out

	def test_geometry_rendered(self):
		out = _format_find_results(
			{
				"elements": [
					{"index": 0, "tag": "div", "text": "", "attrs": {}, "children_count": 0,
					 "origin": "", "visible": True, "rect": {"x": 10, "y": 20, "w": 100, "h": 50}},
				],
				"total": 1, "showing": 1, "offset": 0, "has_more": False,
			},
			"div",
		)

		assert "(visible, 100x50@10,20)" in out

	def test_offset_aware_footer(self):
		out = _format_find_results(
			{
				"elements": [
					{"index": 2, "tag": "div", "text": "", "attrs": {},
					 "children_count": 0, "origin": ""},
				],
				"total": 4, "showing": 1, "offset": 2, "has_more": True,
			},
			"div",
		)

		assert "of 4 total elements" in out
		assert "offset=3" in out  # next batch hint (offset 2 + 1 element)

	def test_no_footer_when_has_more_false(self):
		out = _format_find_results(
			{
				"elements": [
					{"index": 0, "tag": "div", "text": "", "attrs": {},
					 "children_count": 0, "origin": ""},
				],
				"total": 1, "showing": 1, "offset": 0, "has_more": False,
			},
			"div",
		)

		assert "Call again with offset" not in out


class TestFormatNodeIdResults:
	def test_renders_backend_ids(self):
		out = _format_node_id_results(
			{
				"node_ids": [
					{"backend_id": 111, "tag": "button"},
					{"backend_id": 222, "tag": "a"},
				],
				"total": 2, "showing": 2, "offset": 0, "has_more": False,
			},
			"button",
		)

		assert 'Found 2 elements matching "button" (node ids):' in out
		assert "[111] <button>" in out
		assert "[222] <a>" in out
		assert "index= or element_id=" in out

	def test_footer_when_has_more(self):
		out = _format_node_id_results(
			{
				"node_ids": [{"backend_id": 5, "tag": "a"}],
				"total": 3, "showing": 1, "offset": 1, "has_more": True,
			},
			"a",
		)

		assert "of 3 total elements" in out
		assert "offset=2" in out


# ── JS body (string assertions, mirrors TestSearchPageJsBody) ────────────────


class TestFindElementsJsBody:
	"""The raw JS body cannot be executed without a browser, so we assert on its
	string content — the shadow-DOM / iframe recursion, origin tagging, offset
	window, geometry gate, and selector validation must all be present."""

	def test_recurses_shadow_dom(self):
		assert "el.shadowRoot" in _FIND_ELEMENTS_JS_BODY

	def test_recurses_same_origin_iframe(self):
		assert "el.contentDocument" in _FIND_ELEMENTS_JS_BODY
		assert "IFRAME" in _FIND_ELEMENTS_JS_BODY

	def test_origin_tagging_helper(self):
		assert "function _origin(node)" in _FIND_ELEMENTS_JS_BODY
		assert "(in shadow DOM)" in _FIND_ELEMENTS_JS_BODY
		assert "(in iframe)" in _FIND_ELEMENTS_JS_BODY

	def test_collect_all_helper(self):
		assert "function _collectAll(root, out)" in _FIND_ELEMENTS_JS_BODY

	def test_is_visible_helper(self):
		assert "function _isVisible(el)" in _FIND_ELEMENTS_JS_BODY
		assert "getComputedStyle" in _FIND_ELEMENTS_JS_BODY
		# ancestor-chain checks: display / visibility / opacity
		assert "display" in _FIND_ELEMENTS_JS_BODY
		assert "visibility" in _FIND_ELEMENTS_JS_BODY
		assert "opacity" in _FIND_ELEMENTS_JS_BODY

	def test_offset_window(self):
		# the store condition keys on OFFSET + a limit cap
		assert "total >= OFFSET" in _FIND_ELEMENTS_JS_BODY
		assert "results.length < limit" in _FIND_ELEMENTS_JS_BODY

	def test_selector_validated_then_matched(self):
		# invalid selector is caught once up front, then matches() per element
		assert "document.querySelector(SELECTOR)" in _FIND_ELEMENTS_JS_BODY
		assert "matches(SELECTOR)" in _FIND_ELEMENTS_JS_BODY

	def test_geometry_gate(self):
		assert "INCLUDE_GEOMETRY" in _FIND_ELEMENTS_JS_BODY
		assert "getBoundingClientRect" in _FIND_ELEMENTS_JS_BODY

	def test_return_shape_includes_phase2_keys(self):
		assert "offset: OFFSET" in _FIND_ELEMENTS_JS_BODY
		assert "has_more:" in _FIND_ELEMENTS_JS_BODY
