"""Tests for click: index lookup, SELECT branch, coordinate-fail error mapping,
success echo, mouseMoved sequence, occlusion JS fallback, viewport clipping.

Covers:
- index lookup: cache hit path through _get_element_by_index
- success echo: click_element returning True yields 'Clicked [...]' in
  extracted_content + long_term_memory (mirrors navigate/go_back style)
- coordinate-fail error mapping: click_element returning False (no coordinates
  + JS fallback failed) yields an explicit error instead of silent success
- CDP exception mapping: highlight/click raising -> friendly 'Click failed: ...'
- SELECT branch: scoped option fetch via fetch_select_options(backend_id),
  NOT the old querySelectorAll('select option') page-wide scan
- mouseMoved: click_at now emits mouseMoved -> mousePressed -> mouseReleased
  (browser-use default_action_watchdog.py:902-955 alignment)
- occlusion fallback: _is_element_occluded=True -> skip click_at, call _js_click
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tree_walker.browser.session import BrowserSession
from tree_walker.browser.views import (
	BrowserStateSummary,
	DOMRect,
	EnhancedDOMTreeNode,
	NodeType,
	SerializedDOMState,
)
from tree_walker.tools.actions import Tools


# ── Shared helpers ────────────────────────────────────────────────────────────


def _make_entry(
	*,
	tag: str = "BUTTON",
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
	click_element_return: bool = True,
	click_element_side_effect=None,
	fetch_options_return=None,
	fetch_options_side_effect=None,
) -> MagicMock:
	"""Stub BrowserSession for action-layer tests (does NOT touch CDP)."""
	bs = MagicMock()
	bs.current_session_id = "sid"
	bs.current_target_id = "tid"
	if click_element_side_effect is not None:
		bs.click_element = AsyncMock(side_effect=click_element_side_effect)
	else:
		bs.click_element = AsyncMock(return_value=click_element_return)
	bs.highlight_element = AsyncMock()
	if fetch_options_side_effect is not None:
		bs.fetch_select_options = AsyncMock(side_effect=fetch_options_side_effect)
	else:
		bs.fetch_select_options = AsyncMock(
			return_value=fetch_options_return
			if fetch_options_return is not None
			else [{"value": "a", "text": "A", "selected": False}],
		)
	bs.get_state = AsyncMock(return_value=_make_state({}))
	bs.get_tabs = AsyncMock(return_value=[])  # _action_click snapshots tabs (G7)
	return bs


# ── Element lookup ────────────────────────────────────────────────────────────


class TestClickElementLookup:
	@pytest.mark.asyncio
	async def test_index_in_cache_calls_click(self):
		entry = _make_entry(backend_node_id=42)
		state = _make_state({5: entry})
		browser = _make_browser()

		result = await Tools().execute("click", {"index": 5}, browser, browser_state=state)

		assert result.error is None
		browser.highlight_element.assert_awaited_once_with(42)
		browser.click_element.assert_awaited_once_with(42)

	@pytest.mark.asyncio
	async def test_missing_index_returns_error_no_click(self):
		state = _make_state({})  # index 5 absent
		browser = _make_browser()

		result = await Tools().execute("click", {"index": 5}, browser, browser_state=state)

		assert result.error is not None
		assert "5" in result.error
		browser.highlight_element.assert_not_awaited()
		browser.click_element.assert_not_awaited()


# ── Success echo ──────────────────────────────────────────────────────────────


class TestClickSuccessEcho:
	@pytest.mark.asyncio
	async def test_echoes_tag_and_index_when_no_text(self):
		entry = _make_entry(tag="DIV", backend_node_id=7)
		state = _make_state({3: entry})
		browser = _make_browser()

		result = await Tools().execute("click", {"index": 3}, browser, browser_state=state)

		assert result.error is None
		assert result.extracted_content == "Clicked [DIV] at index 3"
		assert result.long_term_memory == "Clicked [DIV] at index 3"

	@pytest.mark.asyncio
	async def test_echoes_aria_label_when_available(self):
		entry = _make_entry(
			tag="BUTTON", backend_node_id=7, attributes={"aria-label": "Submit form"},
		)
		state = _make_state({3: entry})
		browser = _make_browser()

		result = await Tools().execute("click", {"index": 3}, browser, browser_state=state)

		assert result.error is None
		assert "Submit form" in result.extracted_content
		assert result.extracted_content.startswith("Clicked [BUTTON]")


# ── Coordinate-fail error mapping ─────────────────────────────────────────────


class TestClickCoordinateFail:
	@pytest.mark.asyncio
	async def test_click_element_false_yields_explicit_error(self):
		"""No silent success when coordinates can't be obtained and JS fails."""
		entry = _make_entry(backend_node_id=42)
		state = _make_state({1: entry})
		browser = _make_browser(click_element_return=False)

		result = await Tools().execute("click", {"index": 1}, browser, browser_state=state)

		assert result.error is not None
		assert "Could not click" in result.error
		assert result.extracted_content is None
		assert result.long_term_memory is None

	@pytest.mark.asyncio
	async def test_cdp_exception_maps_to_friendly_error(self):
		entry = _make_entry(backend_node_id=42)
		state = _make_state({1: entry})
		browser = _make_browser(click_element_side_effect=RuntimeError("target detached"))

		result = await Tools().execute("click", {"index": 1}, browser, browser_state=state)

		assert result.error == "Click failed: target detached"


# ── SELECT branch ─────────────────────────────────────────────────────────────


class TestClickSelectBranch:
	@pytest.mark.asyncio
	async def test_select_uses_scoped_fetch_not_global_query(self):
		"""SELECT branch must call fetch_select_options(backend_id), NOT
		execute_js with querySelectorAll('select option') (page-wide bug)."""
		entry = _make_entry(tag="SELECT", backend_node_id=99)
		state = _make_state({2: entry})
		options = [{"value": "x", "text": "X", "selected": True}]
		browser = _make_browser(fetch_options_return=options)

		result = await Tools().execute("click", {"index": 2}, browser, browser_state=state)

		assert result.error is None
		assert "x" in result.extracted_content
		browser.fetch_select_options.assert_awaited_once_with(99)
		browser.click_element.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_select_fetch_error_is_friendly(self):
		entry = _make_entry(tag="SELECT", backend_node_id=99)
		state = _make_state({2: entry})
		browser = _make_browser(fetch_options_side_effect=RuntimeError("CDP down"))

		result = await Tools().execute("click", {"index": 2}, browser, browser_state=state)

		assert result.error == "Failed to read select options: CDP down"


# ── Session-layer: mouseMoved sequence ────────────────────────────────────────


class TestClickAtMouseSequence:
	def _make_session(self) -> tuple[BrowserSession, MagicMock]:
		s = BrowserSession.__new__(BrowserSession)
		s._highlight_settings = MagicMock(enabled=False, click_feedback_enabled=False)
		s.current_session_id = "sid"
		client = MagicMock()
		client.send.Input.dispatchMouseEvent = AsyncMock(return_value={})
		s.client = client
		return s, client

	@pytest.mark.asyncio
	async def test_emits_three_events_in_order(self, monkeypatch):
		async def _no_sleep(_):
			pass
		monkeypatch.setattr("tree_walker.browser.session.asyncio.sleep", _no_sleep)

		s, client = self._make_session()
		await s.click_at(100.0, 200.0)

		assert client.send.Input.dispatchMouseEvent.await_count == 3
		types = [
			c.args[0]["type"]
			for c in client.send.Input.dispatchMouseEvent.await_args_list
		]
		assert types == ["mouseMoved", "mousePressed", "mouseReleased"]


# ── Session-layer: click_element occlusion + viewport clip ────────────────────


class TestClickElementFallback:
	def _make_session(
		self, *, coords: DOMRect | None, occluded: bool, js_click_ok: bool,
		viewport: tuple[int, int] | None = None,
	) -> tuple[BrowserSession, MagicMock]:
		s = BrowserSession.__new__(BrowserSession)
		s._highlight_settings = MagicMock(enabled=False, click_feedback_enabled=False)
		s.current_session_id = "sid"
		client = MagicMock()
		client.send.DOM.scrollIntoViewIfNeeded = AsyncMock(return_value={})
		s.client = client
		s._get_viewport_size = AsyncMock(return_value=viewport)
		s.get_element_coordinates = AsyncMock(return_value=coords)
		s._is_element_occluded = AsyncMock(return_value=occluded)
		s._js_click = AsyncMock(return_value=js_click_ok)
		s.click_at = AsyncMock()
		return s, client

	@pytest.mark.asyncio
	async def test_normal_click_skips_js_fallback(self):
		s, _ = self._make_session(
			coords=DOMRect(x=10, y=20, width=100, height=50), occluded=False, js_click_ok=True,
		)
		ok = await s.click_element(42)
		assert ok is True
		s.click_at.assert_awaited_once_with(60, 45)  # 中心 (60,45)
		s._js_click.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_occluded_triggers_js_fallback(self):
		s, _ = self._make_session(
			coords=DOMRect(x=10, y=20, width=100, height=50), occluded=True, js_click_ok=True,
		)
		ok = await s.click_element(42)
		assert ok is True
		s.click_at.assert_not_awaited()
		s._js_click.assert_awaited_once_with(42)

	@pytest.mark.asyncio
	async def test_no_coords_triggers_js_fallback(self):
		s, _ = self._make_session(coords=None, occluded=False, js_click_ok=True)
		ok = await s.click_element(42)
		assert ok is True
		s.click_at.assert_not_awaited()
		s.get_element_coordinates.assert_awaited_once_with(42, viewport=None)
		s._js_click.assert_awaited_once_with(42)

	@pytest.mark.asyncio
	async def test_total_failure_returns_false(self):
		s, _ = self._make_session(coords=None, occluded=False, js_click_ok=False)
		assert await s.click_element(42) is False
		s.click_at.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_center_clipped_to_viewport(self):
		# rect 中心 x=950 超出 vw=800 -> 裁剪到 799
		s, _ = self._make_session(
			coords=DOMRect(x=900, y=10, width=100, height=20),
			occluded=False, js_click_ok=True, viewport=(800, 600),
		)
		await s.click_element(42)
		s.click_at.assert_awaited_once_with(799, 20)  # x 裁剪，y 不变


# ── Session-layer: _best_quad_rect ────────────────────────────────────────────


class TestBestQuadRect:
	def test_picks_largest_viewport_intersection(self):
		# quad A: (0,0,10,10) 在视口内；quad B: (5000,5000,10,10) 在视口外
		quads = [
			[0, 0, 10, 0, 10, 10, 0, 10],
			[5000, 5000, 5010, 5000, 5010, 5010, 5000, 5010],
		]
		rect = BrowserSession._best_quad_rect(quads, (800, 600))
		assert rect == DOMRect(x=0, y=0, width=10, height=10)

	def test_falls_back_to_first_quad_without_viewport(self):
		quads = [[0, 0, 10, 0, 10, 10, 0, 10]]
		rect = BrowserSession._best_quad_rect(quads, None)
		assert rect is not None

	def test_returns_none_for_empty(self):
		assert BrowserSession._best_quad_rect([], (800, 600)) is None


# ── Session-layer: _get_viewport_size ─────────────────────────────────────────


class TestGetViewportSize:
	def _make_session(self, layout_viewport, *, raise_exc=None) -> tuple[BrowserSession, MagicMock]:
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid"
		client = MagicMock()
		if raise_exc is not None:
			client.send.Page.getLayoutMetrics = AsyncMock(side_effect=raise_exc)
		else:
			client.send.Page.getLayoutMetrics = AsyncMock(
				return_value={"layoutViewport": layout_viewport},
			)
		s.client = client
		return s, client

	@pytest.mark.asyncio
	async def test_returns_client_width_and_height(self):
		s, _ = self._make_session({"clientWidth": 1280, "clientHeight": 800})
		assert await s._get_viewport_size() == (1280, 800)

	@pytest.mark.asyncio
	async def test_returns_none_on_cdp_error(self):
		s, _ = self._make_session({}, raise_exc=RuntimeError("cdp down"))
		assert await s._get_viewport_size() is None

	@pytest.mark.asyncio
	async def test_returns_none_on_zero_dimensions(self):
		s, _ = self._make_session({"clientWidth": 0, "clientHeight": 800})
		assert await s._get_viewport_size() is None


# ── Session-layer: fetch_select_options ───────────────────────────────────────


class TestFetchSelectOptions:
	def _make_session(self, options_value) -> tuple[BrowserSession, MagicMock]:
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid"
		client = MagicMock()
		client.send.DOM.resolveNode = AsyncMock(
			return_value={"object": {"objectId": "obj-1"}},
		)
		client.send.Runtime.callFunctionOn = AsyncMock(
			return_value={"result": {"value": options_value}},
		)
		s.client = client
		return s, client

	@pytest.mark.asyncio
	async def test_scoped_to_specific_backend_id(self):
		s, client = self._make_session([{"value": "a", "text": "A", "selected": True}])
		options = await s.fetch_select_options(99)
		assert options == [{"value": "a", "text": "A", "selected": True}]
		client.send.DOM.resolveNode.assert_awaited_once_with(
			{"backendNodeId": 99}, session_id="sid",
		)

	@pytest.mark.asyncio
	async def test_returns_empty_list_on_non_list_value(self):
		s, _ = self._make_session(None)
		assert await s.fetch_select_options(1) == []
