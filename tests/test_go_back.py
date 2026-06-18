"""Tests for the go_back action: cache clearing, no-history error, health check, URL echo.

Covers:
- error mapping: ``browser.go_back`` raising never enters the health check
  (``get_state`` not called) and yields a friendly ``Failed to go back: ...``
- no history: ``browser.go_back`` returning ``None`` (currentIndex <= 0) yields an
  explicit ``No previous page in history to go back to`` error instead of a silent
  success (browser-use's known defect)
- success echo: a returned target URL is echoed in ``extracted_content`` and
  ``long_term_memory`` as ``Navigated back to {url}``
- health check (lightweight, user-chosen: no reload, never hard-fails): non-empty
  page -> single get_state; empty-then-recovered -> wait + recheck; persistently
  empty -> still success (warning only); non-http target (e.g. chrome://) skips
- session layer: ``BrowserSession.go_back`` returns the previous entry URL,
  returns ``None`` when there is no history, and clears the selector_map caches
  (aligning with ``navigate`` / ``switch_tab``)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tree_walker.agent.views import ActionResult  # noqa: F401  (asserts shape only)
from tree_walker.browser.session import BrowserSession
from tree_walker.browser.views import (
	BrowserStateSummary,
	EnhancedDOMTreeNode,
	NodeType,
	SerializedDOMState,
	SimplifiedNode,
)
from tree_walker.tools.actions import Tools


# ── Shared helpers ────────────────────────────────────────────────────────────


# A minimal non-empty root so SerializedDOMState._root is not None. The health
# check only inspects ``_root is None`` and ``element_tree_text.strip()``, so a
# bare SimplifiedNode is enough (mirrors test_navigate.py).
_NON_EMPTY_ROOT = SimplifiedNode(
	original_node=EnhancedDOMTreeNode(
		node_id=1,
		backend_node_id=1,
		node_type=NodeType.ELEMENT_NODE,
		node_name="body",
		node_value="",
		attributes={},
	),
	children=[],
)


def _make_state(*, empty: bool = False, url: str = "https://example.com") -> BrowserStateSummary:
	"""Build a BrowserStateSummary; ``empty`` yields _root=None + blank text."""
	return BrowserStateSummary(
		url=url,
		title="",
		dom_state=SerializedDOMState(
			_root=None if empty else _NON_EMPTY_ROOT,
			selector_map={},
			element_tree_text="" if empty else "<body>some rendered content</body>",
		),
	)


def _make_browser(*, go_back_return="https://a.com", go_back_side_effect=None, states=None) -> MagicMock:
	"""Stub BrowserSession.

	- ``go_back`` is an AsyncMock; set ``go_back_side_effect`` to make it raise
	  (simulating a CDP failure), or ``go_back_return`` for the normal / no-history
	  (None) cases.
	- ``get_state`` is an AsyncMock returning queued states in order, then a
	  default non-empty state. This lets health-check tests script the DOM state
	  across stages without touching CDP primitives.
	"""
	bs = MagicMock()
	if go_back_side_effect is not None:
		bs.go_back = AsyncMock(side_effect=go_back_side_effect)
	else:
		bs.go_back = AsyncMock(return_value=go_back_return)
	default_state = _make_state(empty=False)
	queue = list(states) if states is not None else [default_state]

	def _next_state(*args, **kwargs):
		return queue.pop(0) if queue else default_state

	bs.get_state = AsyncMock(side_effect=_next_state)
	return bs


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch):
	"""Collapse the health-check retry wait to zero so tests don't sleep 3s.

	go_back uses a lightweight check (no reload), so only the retry wait matters.
	"""
	monkeypatch.setattr("tree_walker.tools.actions._NAVIGATE_EMPTY_RETRY_WAIT", 0.0)


# ── error mapping ─────────────────────────────────────────────────────────────


class TestGoBackErrorMapping:
	@pytest.mark.asyncio
	async def test_exception_maps_to_friendly_error(self):
		browser = _make_browser(go_back_side_effect=RuntimeError("boom"))
		result = await Tools().execute("go_back", {}, browser)

		assert result.error == "Failed to go back: boom"
		# A failed go_back must not enter the health check.
		browser.get_state.assert_not_called()


# ── no history ────────────────────────────────────────────────────────────────


class TestGoBackNoHistory:
	@pytest.mark.asyncio
	async def test_none_return_is_explicit_error(self):
		browser = _make_browser(go_back_return=None)
		result = await Tools().execute("go_back", {}, browser)

		assert result.error == "No previous page in history to go back to"
		# Nothing to go back to -> no health check.
		browser.get_state.assert_not_called()


# ── success echo ──────────────────────────────────────────────────────────────


class TestGoBackSuccess:
	@pytest.mark.asyncio
	async def test_echoes_back_target_url(self):
		browser = _make_browser(go_back_return="https://a.com", states=[_make_state(empty=False)])
		result = await Tools().execute("go_back", {}, browser)

		assert result.error is None
		assert result.extracted_content == "Navigated back to https://a.com"
		assert result.long_term_memory == "Navigated back to https://a.com"


# ── health check (lightweight: no reload, never hard-fails) ───────────────────


class TestGoBackHealthCheck:
	@pytest.mark.asyncio
	async def test_non_empty_page_single_get_state(self):
		browser = _make_browser(go_back_return="https://a.com", states=[_make_state(empty=False)])
		result = await Tools().execute("go_back", {}, browser)

		assert result.error is None
		assert browser.get_state.await_count == 1  # no retry

	@pytest.mark.asyncio
	async def test_empty_then_recovered_after_wait(self):
		# First read empty, second read (after the retry wait) non-empty -> success.
		browser = _make_browser(
			go_back_return="https://a.com",
			states=[_make_state(empty=True), _make_state(empty=False)],
		)
		result = await Tools().execute("go_back", {}, browser)

		assert result.error is None
		assert browser.get_state.await_count == 2
		assert result.extracted_content == "Navigated back to https://a.com"

	@pytest.mark.asyncio
	async def test_persistent_empty_does_not_fail_hard(self):
		# Lightweight policy: persistently empty only warns, still reports success.
		browser = _make_browser(
			go_back_return="https://a.com",
			states=[_make_state(empty=True), _make_state(empty=True)],
		)
		result = await Tools().execute("go_back", {}, browser)

		assert result.error is None  # NOT a hard failure
		assert browser.get_state.await_count == 2  # initial + one recheck
		assert result.extracted_content == "Navigated back to https://a.com"

	@pytest.mark.asyncio
	async def test_non_http_target_skips_check(self):
		"""A non-http target (e.g. chrome://) skips the health check even if empty."""
		browser = _make_browser(
			go_back_return="chrome://settings",
			states=[_make_state(empty=True, url="chrome://settings")],
		)
		result = await Tools().execute("go_back", {}, browser)

		assert result.error is None
		assert browser.get_state.await_count == 1  # no retry


# ── session layer ─────────────────────────────────────────────────────────────


class TestGoBackSession:
	"""Exercise BrowserSession.go_back directly with a half-initialized session.

	Pattern mirrors TestWaitForPageSettle in test_multi_act.py: bypass __init__
	(no ws_url needed) and stub the CDP client.
	"""

	def _make_session(self, history: dict) -> tuple[BrowserSession, MagicMock]:
		s = BrowserSession.__new__(BrowserSession)
		s._settings = MagicMock(page_settle_timeout=0.0, page_settle_poll_interval=0.0)
		s.current_session_id = "sid"
		client = MagicMock()
		client.send.Page.getNavigationHistory = AsyncMock(return_value=history)
		client.send.Page.navigateToHistoryEntry = AsyncMock(return_value={})
		client.send.Runtime.evaluate = AsyncMock(
			return_value={"result": {"value": "complete"}},  # consumed by _wait_for_page_settle
		)
		s.client = client
		s._cached_selector_map = {"old": 1}
		s._previous_cached_selector_map = {"old": 1}
		return s, client

	@pytest.mark.asyncio
	async def test_returns_previous_url_and_navigates(self):
		history = {
			"currentIndex": 1,
			"entries": [
				{"id": 11, "url": "https://prev.com"},
				{"id": 22, "url": "https://cur.com"},
			],
		}
		s, client = self._make_session(history)

		url = await s.go_back()

		assert url == "https://prev.com"
		client.send.Page.navigateToHistoryEntry.assert_awaited_once_with(
			{"entryId": 11}, session_id="sid",
		)

	@pytest.mark.asyncio
	async def test_no_history_returns_none(self):
		history = {"currentIndex": 0, "entries": [{"id": 1, "url": "u"}]}
		s, client = self._make_session(history)

		assert await s.go_back() is None
		client.send.Page.navigateToHistoryEntry.assert_not_called()

	@pytest.mark.asyncio
	async def test_clears_selector_map_caches(self):
		history = {
			"currentIndex": 1,
			"entries": [{"id": 1, "url": "u"}, {"id": 2, "url": "v"}],
		}
		s, _ = self._make_session(history)
		assert s._cached_selector_map is not None  # precondition

		await s.go_back()

		assert s._cached_selector_map is None
		assert s._previous_cached_selector_map is None
