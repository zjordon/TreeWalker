"""Tests for find_text: CDP text search, success echo, soft not-found, highlight.

Covers:
- action layer: hit echoes ``Scrolled to text '...' into view (found in <tag>,
  via <method>)`` with ``extracted_content == long_term_memory``; a hit with
  no tag omits the ``found in <...>`` clause; not-found is a SOFT echo
  (``error is None``, aligns with search_page / browser-use, unlike the old
  hard error); a raised ``find_text`` returns ``error="Find text failed: ..."``;
  the action does NOT call ``get_state`` and passes the text through verbatim
- param model: ``FindTextParams.text`` accepts non-empty, rejects empty
  (``min_length=1``), forbids extra fields
- session layer: ``BrowserSession.find_text`` runs the 3-query XPath chain via
  ``DOM.performSearch`` (``includeUserAgentShadowDOM=True``) and scrolls the
  first match with ``DOM.scrollIntoViewIfNeeded``; ``discardSearchResults``
  runs in a ``finally`` (so the WINNING query also cleans up — the browser-use
  leak fix); a missed first query falls through to the second; all-miss runs
  the JS TreeWalker fallback via ``Runtime.evaluate``; XPath quote escaping
  uses single-quote / ``concat()``; a failed ``performSearch`` is caught and
  the next query is tried; on a hit, ``describeNode`` (nodeId -> backendNodeId)
  feeds ``highlight_element``
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from tree_walker.browser.session import BrowserSession, _xpath_string_literal
from tree_walker.tools.actions import Tools
from tree_walker.tools.models import FindTextParams


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_browser(
	*, find_return: dict | None = None, find_raises: Exception | None = None,
) -> MagicMock:
	"""Stub BrowserSession: find_text returns an info dict (or raises).

	get_state is an AsyncMock purely so we can assert it is NOT awaited
	(find_text must not trigger a full DOM fetch).
	"""
	bs = MagicMock()
	if find_raises:
		bs.find_text = AsyncMock(side_effect=find_raises)
	else:
		bs.find_text = AsyncMock(
			return_value=find_return or {"found": True, "method": "xpath-text", "tag": "p"},
		)
	bs.get_state = AsyncMock()
	return bs


# ── action layer ─────────────────────────────────────────────────────────────


class TestFindTextAction:
	@pytest.mark.asyncio
	async def test_hit_echoes_found_in_tag(self):
		browser = _make_browser(
			find_return={"found": True, "method": "xpath-text", "tag": "p"},
		)

		result = await Tools().execute("find_text", {"text": "Sign in"}, browser)

		assert result.error is None
		assert result.is_done is False
		assert result.success is None
		assert "Scrolled to text 'Sign in' into view" in result.extracted_content
		assert "found in <p>" in result.extracted_content
		assert "via xpath-text" in result.extracted_content
		assert result.extracted_content == result.long_term_memory
		browser.find_text.assert_awaited_once_with("Sign in")

	@pytest.mark.asyncio
	async def test_hit_without_tag_omits_found_in(self):
		# JS-fallback hit (or describe failure) -> tag None -> no "found in <...>"
		browser = _make_browser(
			find_return={"found": True, "method": "xpath-content", "tag": None},
		)

		result = await Tools().execute("find_text", {"text": "More"}, browser)

		assert result.error is None
		assert "via xpath-content" in result.extracted_content
		assert "found in <" not in result.extracted_content
		assert result.extracted_content == result.long_term_memory

	@pytest.mark.asyncio
	async def test_not_found_is_soft_echo_not_error(self):
		# Soft echo (aligns with browser-use + search_page): "text not on the page"
		# is actionable info, NOT a tool failure. error stays None.
		browser = _make_browser(find_return={"found": False, "method": "none", "tag": None})

		result = await Tools().execute("find_text", {"text": "ghost text"}, browser)

		assert result.error is None
		assert result.extracted_content == "Text 'ghost text' not found on page"
		assert result.extracted_content == result.long_term_memory

	@pytest.mark.asyncio
	async def test_cdp_failure_returns_error(self):
		# find_text raises (connection drop / DOM error) -> find_text-specific error
		browser = _make_browser(find_raises=RuntimeError("cdp timeout"))

		result = await Tools().execute("find_text", {"text": "x"}, browser)

		assert result.error == "Find text failed: cdp timeout"
		assert result.extracted_content is None

	@pytest.mark.asyncio
	async def test_does_not_call_get_state(self):
		browser = _make_browser()

		await Tools().execute("find_text", {"text": "x"}, browser)

		browser.get_state.assert_not_awaited()  # find_text must not fetch full state

	@pytest.mark.asyncio
	async def test_passes_text_through_verbatim(self):
		browser = _make_browser()

		await Tools().execute("find_text", {"text": "He said \"hi\""}, browser)

		browser.find_text.assert_awaited_once_with('He said "hi"')


# ── param model ──────────────────────────────────────────────────────────────


class TestFindTextParams:
	def test_accepts_nonempty(self):
		assert FindTextParams(text="hello").text == "hello"

	def test_rejects_empty(self):
		# min_length=1: empty string would make contains(text(), "") match everything
		with pytest.raises(ValidationError):
			FindTextParams(text="")

	def test_extra_field_forbidden(self):
		with pytest.raises(ValidationError):
			FindTextParams(text="hello", nth=2)


# ── xpath literal helper ────────────────────────────────────────────────────


class TestXPathStringLiteral:
	def test_plain_text_double_quoted(self):
		assert _xpath_string_literal("Hello") == '"Hello"'

	def test_double_quote_uses_single_quotes(self):
		# contains " but not ' -> single-quote delimited (valid XPath)
		assert _xpath_string_literal('He said "hi"') == '\'He said "hi"\''

	def test_both_quotes_use_concat(self):
		lit = _xpath_string_literal('It\'s "fine"')
		assert lit.startswith("concat(")
		assert '"It\'s "' in lit
		assert "'\"'" in lit  # the spliced literal double-quote


# ── session layer ────────────────────────────────────────────────────────────


class TestFindTextSession:
	def _make_session(
		self, *,
		result_counts=(1, 0, 0), node_ids=(42,), js_value: bool = False,
		perform_raises_at: int | None = None, eval_raises: Exception | None = None,
	) -> tuple[BrowserSession, MagicMock]:
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid-1"
		client = MagicMock()
		perf = []
		for i, c in enumerate(result_counts):
			if perform_raises_at == i:
				perf.append(RuntimeError("performSearch boom"))
			else:
				perf.append({"searchId": f"sid-{i}", "resultCount": c})
		client.send.DOM.performSearch = AsyncMock(side_effect=perf)
		client.send.DOM.getSearchResults = AsyncMock(return_value={"nodeIds": list(node_ids)})
		client.send.DOM.scrollIntoViewIfNeeded = AsyncMock(return_value={})
		client.send.DOM.discardSearchResults = AsyncMock(return_value={})
		client.send.DOM.describeNode = AsyncMock(
			return_value={"node": {"backendNodeId": 99, "nodeName": "P"}},
		)
		if eval_raises:
			client.send.Runtime.evaluate = AsyncMock(side_effect=eval_raises)
		else:
			client.send.Runtime.evaluate = AsyncMock(
				return_value={"result": {"value": js_value}},
			)
		s.client = client
		s.highlight_element = AsyncMock()
		return s, client

	@pytest.mark.asyncio
	async def test_hit_first_query_scrolls_and_cleans_up(self):
		s, client = self._make_session(result_counts=(1, 0, 0))

		info = await s.find_text("hello")

		assert info == {"found": True, "method": "xpath-text", "tag": "p"}
		# scrolled the first match's nodeId
		assert client.send.DOM.scrollIntoViewIfNeeded.await_args.args[0] == {"nodeId": 42}
		# winning query ALSO discards (finally fix — browser-use leaks here)
		client.send.DOM.discardSearchResults.assert_awaited()
		# only the winning query resolved nodes / described / highlighted
		assert client.send.DOM.getSearchResults.await_count == 1
		client.send.DOM.describeNode.assert_awaited_once_with({"nodeId": 42}, session_id="sid-1")
		s.highlight_element.assert_awaited_once_with(99)
		# query carried includeUserAgentShadowDOM (browser-use omits it)
		perf_params = client.send.DOM.performSearch.await_args.args[0]
		assert perf_params["includeUserAgentShadowDOM"] is True

	@pytest.mark.asyncio
	async def test_first_query_empty_falls_through_to_second(self):
		s, client = self._make_session(result_counts=(0, 1, 0))

		info = await s.find_text("hello")

		assert info["found"] is True
		assert info["method"] == "xpath-content"
		assert client.send.DOM.performSearch.await_count == 2
		assert client.send.DOM.getSearchResults.await_count == 1  # only query 2 resolved
		# both queries (empty + winning) discarded
		assert client.send.DOM.discardSearchResults.await_count == 2

	@pytest.mark.asyncio
	async def test_all_queries_miss_js_fallback_hits(self):
		s, client = self._make_session(result_counts=(0, 0, 0), js_value=True)

		info = await s.find_text("hello")

		assert info == {"found": True, "method": "js-treewalker", "tag": None}
		client.send.Runtime.evaluate.assert_awaited_once()
		expr = client.send.Runtime.evaluate.await_args.args[0]["expression"]
		assert "createTreeWalker" in expr
		# needle injected as a JS string literal (json.dumps at the call site)
		assert '"hello"' in expr

	@pytest.mark.asyncio
	async def test_all_miss_returns_not_found(self):
		s, client = self._make_session(result_counts=(0, 0, 0), js_value=False)

		info = await s.find_text("hello")

		assert info == {"found": False, "method": "none", "tag": None}

	@pytest.mark.asyncio
	async def test_double_quote_escaped_as_single_quote_literal(self):
		s, client = self._make_session()

		await s.find_text('He said "hi"')

		q = client.send.DOM.performSearch.await_args.args[0]["query"]
		# contains " but not ' -> single-quote delimited, never concat()
		assert "concat(" not in q
		assert "'He said \"hi\"'" in q

	@pytest.mark.asyncio
	async def test_both_quotes_escaped_via_concat(self):
		s, client = self._make_session()

		await s.find_text('It\'s "fine"')

		q = client.send.DOM.performSearch.await_args.args[0]["query"]
		assert "concat(" in q

	@pytest.mark.asyncio
	async def test_performsearch_failure_continues_to_next_query(self):
		# query 1 raises (caught, continue), query 2 hits
		s, client = self._make_session(result_counts=(0, 1, 0), perform_raises_at=0)

		info = await s.find_text("hello")

		assert info["found"] is True
		assert info["method"] == "xpath-content"
		assert client.send.DOM.performSearch.await_count == 2

	@pytest.mark.asyncio
	async def test_describe_failure_degrades_to_no_tag(self):
		# describeNode raises -> tag None, but the scroll + found result stand
		s, client = self._make_session(result_counts=(1, 0, 0))
		client.send.DOM.describeNode = AsyncMock(side_effect=RuntimeError("describe boom"))

		info = await s.find_text("hello")

		assert info == {"found": True, "method": "xpath-text", "tag": None}
		s.highlight_element.assert_not_awaited()  # no backendNodeId -> no highlight

	@pytest.mark.asyncio
	async def test_empty_nodeids_falls_through_to_js_fallback(self):
		# resultCount>0 but getSearchResults returns no nodeIds -> continue,
		# then the JS TreeWalker fallback resolves it.
		s, client = self._make_session(
			result_counts=(1, 0, 0), node_ids=(), js_value=True,
		)

		info = await s.find_text("hello")

		assert info == {"found": True, "method": "js-treewalker", "tag": None}
		client.send.DOM.scrollIntoViewIfNeeded.assert_not_awaited()  # never resolved

	@pytest.mark.asyncio
	async def test_js_fallback_execute_failure_returns_not_found(self):
		# XPath all miss AND execute_js raises -> fallback catches, returns False
		s, client = self._make_session(
			result_counts=(0, 0, 0), eval_raises=RuntimeError("eval boom"),
		)

		info = await s.find_text("hello")

		assert info == {"found": False, "method": "none", "tag": None}
