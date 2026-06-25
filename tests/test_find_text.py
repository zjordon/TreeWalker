"""Tests for find_text: CDP text search, success echo, soft not-found, highlight.

Covers P0 (core) + P1 (advanced — see docs/tools-optimize/find_text_follow_up.md):

- action layer: hit echoes ``Scrolled to text '...' into view (found in <tag>,
  via <method>)`` with ``extracted_content == long_term_memory``; a hit with
  no tag omits the ``found in <...>`` clause; not-found is a SOFT echo
  (``error is None``, aligns with search_page / browser-use, unlike the old
  hard error); a raised ``find_text`` returns ``error="Find text failed: ..."``;
  the action does NOT call ``get_state`` and passes the text through verbatim;
  forwards nth/case_sensitive/highlight as kwargs (G8/G10/G11); multi-match
  echo reports counts (G8); nth-exceeds is a soft echo (G8); selection
  highlight is echoed (G11)
- param model: ``FindTextParams.text`` accepts non-empty, rejects empty
  (``min_length=1``); ``nth`` defaults to 1 with ge=1; ``case_sensitive``
  defaults False; ``highlight`` defaults "box" over a Literal enum; extra
  fields forbidden
- xpath builder: ``_text_queries`` case-sensitive uses plain contains();
  case-insensitive wraps haystack + needle in translate(); quote-safety is
  orthogonal to case-folding (G10)
- session layer: ``BrowserSession.find_text`` runs the 3-query XPath chain via
  ``DOM.performSearch`` (``includeUserAgentShadowDOM=True``) and scrolls the
  nth visible match with ``DOM.scrollIntoViewIfNeeded``; ``discardSearchResults``
  runs in a ``finally`` (so the WINNING query also cleans up — the browser-use
  leak fix); case-insensitive queries carry translate() (G10); nth picks the
  nth visible match (G8); the visibility probe (resolveNode + one
  callFunctionOn) is only triggered for >1 match (G9); all-hidden degrades to
  the first match; highlight modes box/selection/none (G11); nth-exceeds
  returns reason="nth_exceeds"; the batch is capped at _FIND_TEXT_CAP
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from tree_walker.browser.session import BrowserSession, _text_queries, _xpath_string_literal
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
		# Single match (no total) -> P0-style echo, no count noise.
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
		browser.find_text.assert_awaited_once_with(
			"Sign in", nth=1, case_sensitive=False, highlight="box",
		)

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

		browser.find_text.assert_awaited_once_with(
			'He said "hi"', nth=1, case_sensitive=False, highlight="box",
		)

	@pytest.mark.asyncio
	async def test_forwards_all_params_as_kwargs(self):
		# G8/G10/G11: nth/case_sensitive/highlight forwarded verbatim.
		browser = _make_browser()

		await Tools().execute(
			"find_text",
			{"text": "x", "nth": 2, "case_sensitive": True, "highlight": "selection"},
			browser,
		)

		browser.find_text.assert_awaited_once_with(
			"x", nth=2, case_sensitive=True, highlight="selection",
		)

	@pytest.mark.asyncio
	async def test_multi_match_echo_includes_counts(self):
		# G8: multi-match (total>1) echo reports which match out of how many.
		browser = _make_browser(
			find_return={
				"found": True, "method": "xpath-text", "tag": "p",
				"match_index": 3, "visible_total": 5, "total": 8, "highlight": "box",
			},
		)

		result = await Tools().execute("find_text", {"text": "foo"}, browser)

		assert result.error is None
		assert "match 3 of 5 visible, 8 total" in result.extracted_content
		assert "found in <p>" in result.extracted_content
		assert "via xpath-text" in result.extracted_content

	@pytest.mark.asyncio
	async def test_nth_exceeds_is_soft_echo(self):
		# G8: text present but nth > visible count -> actionable soft echo, not error.
		browser = _make_browser(
			find_return={
				"found": False, "reason": "nth_exceeds", "method": "xpath-text",
				"requested_nth": 5, "visible_total": 2, "total": 2,
			},
		)

		result = await Tools().execute("find_text", {"text": "foo", "nth": 5}, browser)

		assert result.error is None
		assert "only 2 visible" in result.extracted_content
		assert "match 5" in result.extracted_content
		assert "try a smaller nth" in result.extracted_content
		assert result.extracted_content == result.long_term_memory

	@pytest.mark.asyncio
	async def test_selection_highlight_echo(self):
		# G11: non-box highlight mode is surfaced in the echo.
		browser = _make_browser(
			find_return={
				"found": True, "method": "xpath-text", "tag": "p",
				"match_index": 1, "visible_total": 1, "total": 1, "highlight": "selection",
			},
		)

		result = await Tools().execute("find_text", {"text": "foo", "highlight": "selection"}, browser)

		assert result.error is None
		assert "selection highlight" in result.extracted_content


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
			FindTextParams(text="hello", bogus=2)

	def test_nth_defaults_and_bounds(self):
		# G8: nth defaults to 1, ge=1.
		assert FindTextParams(text="x").nth == 1
		assert FindTextParams(text="x", nth=5).nth == 5
		with pytest.raises(ValidationError):
			FindTextParams(text="x", nth=0)
		with pytest.raises(ValidationError):
			FindTextParams(text="x", nth=-1)

	def test_case_sensitive_default_false(self):
		# G10: defaults to case-insensitive (aligns with search_page / Ctrl+F).
		assert FindTextParams(text="x").case_sensitive is False
		assert FindTextParams(text="x", case_sensitive=True).case_sensitive is True

	def test_highlight_default_and_values(self):
		# G11: Literal["box","selection","none"], default "box".
		assert FindTextParams(text="x").highlight == "box"
		assert FindTextParams(text="x", highlight="selection").highlight == "selection"
		assert FindTextParams(text="x", highlight="none").highlight == "none"
		with pytest.raises(ValidationError):
			FindTextParams(text="x", highlight="foo")


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


# ── xpath query builder (G10) ───────────────────────────────────────────────


class TestBuildTextQueries:
	def test_case_sensitive_no_translate(self):
		queries = dict(_text_queries("Foo", True))
		# sensitive: plain contains(text(), "Foo"), no translate()
		assert 'contains(text(), "Foo")' in queries["xpath-text"]
		for q in queries.values():
			assert "translate(" not in q

	def test_case_insensitive_uses_translate(self):
		queries = dict(_text_queries("Foo", False))
		# insensitive: both haystack and needle upper-cased via translate()
		assert "translate(text()" in queries["xpath-text"]
		assert 'translate("Foo"' in queries["xpath-text"]

	def test_insensitive_with_quotes_still_safe(self):
		# quote-safety is orthogonal to case-folding: a needle containing a
		# double-quote still uses single-quote delimiting inside translate().
		queries = dict(_text_queries('He said "hi"', False))
		assert '\'He said "hi"\'' in queries["xpath-text"]
		assert "translate(" in queries["xpath-text"]


# ── session layer ────────────────────────────────────────────────────────────


class TestFindTextSession:
	def _make_session(
		self, *,
		result_counts=(1, 0, 0), node_ids=(42,), js_value: bool = False,
		perform_raises_at: int | None = None, eval_raises: Exception | None = None,
		visible_flags: list[bool] | None = None,
	) -> tuple[BrowserSession, MagicMock]:
		"""Stub a BrowserSession with a MagicMock CDP client.

		visible_flags wires the G9 callFunctionOn probe return (only exercised
		when len(node_ids) > 1). None -> callFunctionOn raises if called
		(single-match path must not probe).
		"""
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
		# G9 visibility probe (only called for >1 match)
		client.send.DOM.resolveNode = AsyncMock(
			side_effect=[{"object": {"objectId": f"obj-{nid}"}} for nid in node_ids],
		)
		if visible_flags is None:
			client.send.Runtime.callFunctionOn = AsyncMock(side_effect=RuntimeError("probe not expected"))
		else:
			client.send.Runtime.callFunctionOn = AsyncMock(
				return_value={"result": {"value": list(visible_flags)}},
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

		assert info == {
			"found": True, "method": "xpath-text", "tag": "p",
			"match_index": 1, "visible_total": 1, "total": 1, "highlight": "box",
		}
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
		# default case_sensitive=False -> lowercase comparison
		assert "toLowerCase" in expr
		# needle injected as a JS string literal (json.dumps at the call site)
		assert '"hello"' in expr

	@pytest.mark.asyncio
	async def test_js_fallback_case_sensitive(self):
		# G10: case_sensitive=True -> plain includes(), no toLowerCase.
		s, client = self._make_session(result_counts=(0, 0, 0), js_value=True)

		await s.find_text("hello", case_sensitive=True)

		expr = client.send.Runtime.evaluate.await_args.args[0]["expression"]
		assert "t.includes(needle)" in expr
		assert "toLowerCase" not in expr

	@pytest.mark.asyncio
	async def test_all_miss_returns_not_found(self):
		s, client = self._make_session(result_counts=(0, 0, 0), js_value=False)

		info = await s.find_text("hello")

		assert info == {"found": False, "method": "none", "tag": None}

	@pytest.mark.asyncio
	async def test_case_insensitive_query_uses_translate(self):
		# G10: default (case_sensitive=False) -> performSearch query has translate().
		s, client = self._make_session()

		await s.find_text("hello")

		q = client.send.DOM.performSearch.await_args.args[0]["query"]
		assert "translate(" in q

	@pytest.mark.asyncio
	async def test_case_sensitive_query_has_no_translate(self):
		# G10: case_sensitive=True -> plain contains(), no translate().
		s, client = self._make_session()

		await s.find_text("hello", case_sensitive=True)

		q = client.send.DOM.performSearch.await_args.args[0]["query"]
		assert "translate(" not in q
		assert 'contains(text(), "hello")' in q

	@pytest.mark.asyncio
	async def test_double_quote_escaped_as_single_quote_literal(self):
		s, client = self._make_session()

		await s.find_text('He said "hi"')

		q = client.send.DOM.performSearch.await_args.args[0]["query"]
		# contains " but not ' -> single-quote delimited, never concat()
		assert "concat(" not in q
		assert '\'He said "hi"\'' in q

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

		assert info == {
			"found": True, "method": "xpath-text", "tag": None,
			"match_index": 1, "visible_total": 1, "total": 1, "highlight": "box",
		}
		s.highlight_element.assert_not_awaited()  # no backendNodeId -> no box highlight

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

	@pytest.mark.asyncio
	async def test_nth_picks_second_visible_match(self):
		# G8: nth=2 selects the 2nd match out of a multi-match batch.
		s, client = self._make_session(
			result_counts=(3, 0, 0), node_ids=(42, 43, 44),
			visible_flags=[True, True, True],
		)

		info = await s.find_text("hello", nth=2)

		assert info["found"] is True
		assert info["match_index"] == 2
		assert client.send.DOM.scrollIntoViewIfNeeded.await_args.args[0] == {"nodeId": 43}

	@pytest.mark.asyncio
	async def test_visibility_filters_hidden_match(self):
		# G9: a hidden middle match is skipped; nth=2 lands on the 2nd VISIBLE.
		s, client = self._make_session(
			result_counts=(3, 0, 0), node_ids=(42, 43, 44),
			visible_flags=[True, False, True],
		)

		info = await s.find_text("hello", nth=2)

		assert info["found"] is True
		assert info["match_index"] == 2
		# visible = [42, 44]; 2nd visible is 44
		assert client.send.DOM.scrollIntoViewIfNeeded.await_args.args[0] == {"nodeId": 44}
		assert info["visible_total"] == 2

	@pytest.mark.asyncio
	async def test_all_hidden_degrades_to_first(self):
		# G9: all hidden -> degrade to first nodeId rather than fail.
		s, client = self._make_session(
			result_counts=(3, 0, 0), node_ids=(42, 43, 44),
			visible_flags=[False, False, False],
		)

		info = await s.find_text("hello")

		assert info["found"] is True
		assert client.send.DOM.scrollIntoViewIfNeeded.await_args.args[0] == {"nodeId": 42}

	@pytest.mark.asyncio
	async def test_visibility_probe_skipped_for_single_match(self):
		# G9: single match -> no resolveNode/callFunctionOn round-trips.
		s, client = self._make_session(result_counts=(1, 0, 0), node_ids=(42,))

		await s.find_text("hello")

		client.send.DOM.resolveNode.assert_not_awaited()
		client.send.Runtime.callFunctionOn.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_highlight_none_skips_box_highlight(self):
		# G11: highlight="none" -> no Overlay box, but describe still runs (tag).
		s, client = self._make_session(result_counts=(1, 0, 0))

		info = await s.find_text("hello", highlight="none")

		assert info["highlight"] == "none"
		assert info["tag"] == "p"
		s.highlight_element.assert_not_awaited()
		client.send.DOM.describeNode.assert_awaited_once()  # tag still resolved

	@pytest.mark.asyncio
	async def test_highlight_selection_uses_window_find(self):
		# G11: highlight="selection" -> window.find via execute_js; no box overlay.
		s, client = self._make_session(result_counts=(1, 0, 0))

		await s.find_text("hello", highlight="selection")

		s.highlight_element.assert_not_awaited()  # selection, not box
		client.send.Runtime.evaluate.assert_awaited_once()
		expr = client.send.Runtime.evaluate.await_args.args[0]["expression"]
		assert "window.find" in expr

	@pytest.mark.asyncio
	async def test_nth_exceeds_returns_reason(self):
		# G8: nth beyond the visible count -> found=False, reason="nth_exceeds".
		s, client = self._make_session(
			result_counts=(2, 0, 0), node_ids=(42, 43),
			visible_flags=[True, True],
		)

		info = await s.find_text("hello", nth=5)

		assert info == {
			"found": False, "reason": "nth_exceeds", "method": "xpath-text",
			"requested_nth": 5, "visible_total": 2, "total": 2,
		}
		client.send.DOM.scrollIntoViewIfNeeded.assert_not_awaited()
		# nth_exceeds returns inside the try -> finally still discards
		client.send.DOM.discardSearchResults.assert_awaited()

	@pytest.mark.asyncio
	async def test_batch_capped_at_find_text_cap(self):
		# G8/G9: large result set is fetched in a capped batch (toIndex=CAP).
		from tree_walker.browser.session import _FIND_TEXT_CAP

		capped_ids = tuple(range(_FIND_TEXT_CAP))
		s, client = self._make_session(
			result_counts=(_FIND_TEXT_CAP * 2, 0, 0),  # total > CAP
			node_ids=capped_ids,
			visible_flags=[True] * _FIND_TEXT_CAP,
		)

		info = await s.find_text("hello")

		to_index = client.send.DOM.getSearchResults.await_args.args[0]["toIndex"]
		assert to_index == _FIND_TEXT_CAP
		assert info["total"] == _FIND_TEXT_CAP * 2  # true total still reported
