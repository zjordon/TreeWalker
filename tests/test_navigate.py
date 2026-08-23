"""Tests for the navigate action: errorText mapping, new_tab, health check.

Covers:
- errorText / network-error mapping: ``browser.navigate`` raising a CDP
  ``errorText`` (``net::ERR_NAME_NOT_RESOLVED`` etc.) is turned into a friendly
  ``ActionResult(error="...site unavailable...")``; non-network errors keep the
  raw message; failures never enter the health check (``get_state`` not called)
- new_tab: default ``False``; ``True`` opens a new tab and skips the health
  check; result message distinguishes "Opened new tab" vs "Navigated to"
- URL completion: missing scheme gets ``https://`` prepended; existing scheme
  left intact
- health check (mirrors browser-use three-stage logic): non-empty page -> no
  retry; empty-then-recovered -> wait only; empty-reload-success -> one reload;
  persistently empty -> friendly "empty content" error; non-http state URL
  (e.g. about:blank) skips the check
- Pydantic validation: ``new_tab`` defaults to False, ``url`` required,
  ``extra="forbid"``, JSON schema exposes the default
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

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
from tree_walker.tools.models import NavigateParams


# ── Shared helpers ────────────────────────────────────────────────────────────


# A minimal non-empty root so SerializedDOMState._root is not None. The health
# check only inspects ``_root is None`` and ``element_tree_text.strip()``, so a
# bare SimplifiedNode is enough.
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


def _make_browser(*, navigate_side_effect=None, states=None) -> MagicMock:
	"""Stub BrowserSession.

	- ``navigate`` is an AsyncMock; set ``navigate_side_effect`` to make it raise
	  (simulating a CDP errorText soft-failure).
	- ``get_state`` is an AsyncMock returning queued states in order, then a
	  default non-empty state. This lets health-check tests script the DOM state
	  across the three stages without touching CDP primitives.
	"""
	bs = MagicMock()
	if navigate_side_effect is not None:
		bs.navigate = AsyncMock(side_effect=navigate_side_effect)
	else:
		bs.navigate = AsyncMock()
	default_state = _make_state(empty=False)
	queue = list(states) if states is not None else [default_state]

	def _next_state(*args, **kwargs):
		return queue.pop(0) if queue else default_state

	bs.get_state = AsyncMock(side_effect=_next_state)
	return bs


@pytest.fixture(autouse=True)
def _fast_health_check(monkeypatch):
	"""Collapse the health-check waits to zero so tests don't sleep 3s+5s."""
	monkeypatch.setattr("tree_walker.tools.actions._NAVIGATE_EMPTY_RETRY_WAIT", 0.0)
	monkeypatch.setattr("tree_walker.tools.actions._NAVIGATE_EMPTY_RELOAD_WAIT", 0.0)


# ── errorText / network-error mapping ─────────────────────────────────────────


class TestNavigateErrorMapping:
	@pytest.mark.asyncio
	async def test_dns_error_maps_to_site_unavailable(self):
		browser = _make_browser(
			navigate_side_effect=RuntimeError("Navigation failed: net::ERR_NAME_NOT_RESOLVED"),
		)
		result = await Tools().execute("navigate", {"url": "nope.invalid"}, browser)

		assert result.error is not None
		assert "site unavailable" in result.error
		# A failed navigation must not enter the health check.
		browser.get_state.assert_not_called()

	@pytest.mark.asyncio
	@pytest.mark.parametrize(
		"marker",
		[
			"net::ERR_CONNECTION_REFUSED",
			"net::ERR_TIMED_OUT",
			"net::ERR_TUNNEL_CONNECTION_FAILED",
			"net::ERR_INTERNET_DISCONNECTED",
		],
	)
	async def test_network_markers_map_to_site_unavailable(self, marker):
		browser = _make_browser(navigate_side_effect=RuntimeError(f"Navigation failed: {marker}"))
		result = await Tools().execute("navigate", {"url": "https://x.com"}, browser)

		assert "site unavailable" in result.error

	@pytest.mark.asyncio
	async def test_bare_net_prefix_also_maps(self):
		"""The ``net::`` catch-all covers error codes not enumerated explicitly."""
		browser = _make_browser(navigate_side_effect=RuntimeError("Navigation failed: net::ERR_SOMETHING_NEW"))
		result = await Tools().execute("navigate", {"url": "https://x.com"}, browser)

		assert "site unavailable" in result.error

	@pytest.mark.asyncio
	async def test_non_network_error_keeps_raw_message(self):
		browser = _make_browser(navigate_side_effect=RuntimeError("boom"))
		result = await Tools().execute("navigate", {"url": "https://x.com"}, browser)

		assert result.error == "Navigation failed: boom"
		assert "site unavailable" not in result.error


# ── new_tab ───────────────────────────────────────────────────────────────────


class TestNavigateNewTab:
	@pytest.mark.asyncio
	async def test_default_new_tab_is_false(self):
		browser = _make_browser(states=[_make_state(empty=False)])
		await Tools().execute("navigate", {"url": "https://x.com"}, browser)

		assert browser.navigate.call_args.kwargs["new_tab"] is False

	@pytest.mark.asyncio
	async def test_new_tab_true_skips_health_check(self):
		# Even with an empty DOM, new_tab=True must skip the health check.
		browser = _make_browser(states=[_make_state(empty=True)])
		result = await Tools().execute("navigate", {"url": "https://x.com", "new_tab": True}, browser)

		assert browser.navigate.call_args.kwargs["new_tab"] is True
		browser.get_state.assert_not_called()
		assert "Opened new tab" in result.extracted_content

	@pytest.mark.asyncio
	async def test_new_tab_false_message(self):
		browser = _make_browser(states=[_make_state(empty=False)])
		result = await Tools().execute("navigate", {"url": "https://x.com"}, browser)

		assert "Navigated to" in result.extracted_content
		assert "Opened new tab" not in result.extracted_content


# ── URL completion ────────────────────────────────────────────────────────────


class TestNavigateUrlCompletion:
	@pytest.mark.asyncio
	async def test_missing_scheme_gets_https_prepended(self):
		browser = _make_browser(states=[_make_state(empty=False)])
		await Tools().execute("navigate", {"url": "x.com"}, browser)

		assert browser.navigate.call_args.args[0] == "https://x.com"

	@pytest.mark.asyncio
	async def test_existing_https_left_intact(self):
		browser = _make_browser(states=[_make_state(empty=False)])
		await Tools().execute("navigate", {"url": "https://y.com"}, browser)

		assert browser.navigate.call_args.args[0] == "https://y.com"

	@pytest.mark.asyncio
	async def test_existing_http_left_intact(self):
		browser = _make_browser(states=[_make_state(empty=False)])
		await Tools().execute("navigate", {"url": "http://z.com"}, browser)

		assert browser.navigate.call_args.args[0] == "http://z.com"


# ── health check (mirrors browser-use three-stage logic) ──────────────────────


class TestNavigateHealthCheck:
	@pytest.mark.asyncio
	async def test_non_empty_page_no_retry_no_reload(self):
		browser = _make_browser(states=[_make_state(empty=False)])
		result = await Tools().execute("navigate", {"url": "https://x.com"}, browser)

		assert result.error is None
		assert browser.navigate.await_count == 1
		assert browser.get_state.await_count == 1

	@pytest.mark.asyncio
	async def test_empty_then_recovered_after_wait(self):
		# First read empty, second read (after the retry wait) non-empty -> done.
		browser = _make_browser(states=[_make_state(empty=True), _make_state(empty=False)])
		result = await Tools().execute("navigate", {"url": "https://x.com"}, browser)

		assert result.error is None
		assert browser.navigate.await_count == 1  # no reload
		assert browser.get_state.await_count == 2

	@pytest.mark.asyncio
	async def test_empty_reload_then_success(self):
		# Empty, still empty, then non-empty after a reload.
		browser = _make_browser(
			states=[_make_state(empty=True), _make_state(empty=True), _make_state(empty=False)],
		)
		result = await Tools().execute("navigate", {"url": "https://x.com"}, browser)

		assert result.error is None
		assert browser.navigate.await_count == 2  # initial + reload
		assert browser.get_state.await_count == 3

	@pytest.mark.asyncio
	async def test_persistent_empty_yields_friendly_error(self):
		browser = _make_browser(
			states=[_make_state(empty=True), _make_state(empty=True), _make_state(empty=True)],
		)
		result = await Tools().execute("navigate", {"url": "https://x.com"}, browser)

		assert result.error is not None
		assert "empty content" in result.error
		assert browser.navigate.await_count == 2  # initial + reload attempt
		assert browser.get_state.await_count == 3

	@pytest.mark.asyncio
	async def test_non_http_state_url_skips_health_check(self):
		"""If the browser lands on a non-http URL (e.g. about:blank after a
		redirect/block), the health check is skipped even when the DOM is empty."""
		browser = _make_browser(states=[_make_state(empty=True, url="about:blank")])
		result = await Tools().execute("navigate", {"url": "https://x.com"}, browser)

		assert result.error is None
		assert browser.navigate.await_count == 1  # no reload
		assert browser.get_state.await_count == 1

	@pytest.mark.asyncio
	async def test_result_echoes_url_on_success(self):
		browser = _make_browser(states=[_make_state(empty=False)])
		result = await Tools().execute("navigate", {"url": "https://echo.test"}, browser)

		assert result.extracted_content == "Navigated to https://echo.test"
		assert result.long_term_memory == "Navigated to https://echo.test"


# ── Pydantic validation ───────────────────────────────────────────────────────


class TestNavigateParams:
	def test_new_tab_defaults_to_false(self):
		params = NavigateParams.model_validate({"url": "x"})
		assert params.new_tab is False

	def test_new_tab_can_be_true(self):
		params = NavigateParams.model_validate({"url": "x", "new_tab": True})
		assert params.new_tab is True

	def test_url_required(self):
		with pytest.raises(ValidationError):
			NavigateParams.model_validate({"new_tab": False})

	def test_extra_keys_rejected(self):
		with pytest.raises(ValidationError):
			NavigateParams.model_validate({"url": "x", "foo": 1})

	def test_schema_has_new_tab_default(self):
		schema = NavigateParams.model_json_schema()
		assert schema["properties"]["new_tab"]["default"] is False


# ── B3-1: 页面级 settle（P7 02 批次三）───────────────────────────────


class TestNavigatePageSettle:
	"""navigate 后调 wait_for_page_settle，就绪信息进回显；settle 失败不阻断导航。"""

	@pytest.mark.asyncio
	async def test_settle_ready_appends_note(self):
		browser = _make_browser()
		browser.wait_for_page_settle = AsyncMock(
			return_value={"ready": True, "stage": "requirejs", "n": 199, "waited": 2.5}
		)

		result = await Tools().execute(
			"navigate", {"url": "https://example.com"}, browser,
		)

		assert result.error is None
		assert "page settled: requirejs, 2.5s" in result.extracted_content
		browser.wait_for_page_settle.assert_awaited_once()

	@pytest.mark.asyncio
	async def test_settle_timeout_appends_warning_note(self):
		browser = _make_browser()
		browser.wait_for_page_settle = AsyncMock(
			return_value={"ready": False, "stage": "requirejs", "n": 61,
				"timeout": True, "waited": 10.0}
		)

		result = await Tools().execute(
			"navigate", {"url": "https://example.com"}, browser,
		)

		assert result.error is None
		assert "not confirmed" in result.extracted_content

	@pytest.mark.asyncio
	async def test_settle_failure_does_not_break_navigation(self):
		"""settle 抛异常（如 mock 不可 await）→ 静默降级，导航照常成功。"""
		browser = _make_browser()
		browser.wait_for_page_settle = MagicMock(return_value="not-awaitable")

		result = await Tools().execute(
			"navigate", {"url": "https://example.com"}, browser,
		)

		assert result.error is None
		assert "Navigated to https://example.com" in result.extracted_content

	@pytest.mark.asyncio
	async def test_new_tab_skips_settle(self):
		browser = _make_browser()
		browser.wait_for_page_settle = AsyncMock()

		result = await Tools().execute(
			"navigate", {"url": "https://example.com", "new_tab": True}, browser,
		)

		assert result.error is None
		browser.wait_for_page_settle.assert_not_awaited()


# ── B3-1: wait_for_page_settle 单元（session 层）─────────────────────


class TestWaitForPageSettle:
	"""requirejs 模块数连续 stable_polls 次不变 → 就绪；无 requirejs 即刻就绪；超时降级。"""

	def _session(self) -> BrowserSession:
		return BrowserSession(ws_url="ws://localhost:9223/test")

	def test_no_requirejs_ready_immediately(self):
		s = self._session()
		with patch.object(s, "evaluate", AsyncMock(
			return_value='{"ready": true, "stage": "no-requirejs", "n": 0}'
		)) as ev:
			st = asyncio.run(s.wait_for_page_settle(timeout=1.0, poll=0.0))
		assert st["ready"] is True
		assert st["stage"] == "no-requirejs"
		assert ev.await_count == 1

	def test_requirejs_stable_after_consecutive_polls(self):
		s = self._session()
		# stable_polls=4 语义 = 首次计数 + 后续 4 次相同（共 5 个相同采样）→ 第 6 次就绪
		seq = ['{"ready": false, "stage": "requirejs", "n": 61}'] + \
			['{"ready": false, "stage": "requirejs", "n": 140}'] * 5
		with patch.object(s, "evaluate", AsyncMock(side_effect=seq)) as ev:
			st = asyncio.run(s.wait_for_page_settle(timeout=5.0, poll=0.0, stable_polls=4))
		assert st["ready"] is True
		assert st["n"] == 140
		assert ev.await_count == 6

	def test_timeout_degrades_not_ready(self):
		s = self._session()
		# 计数一直变化（无限序列）→ 到超时仍未稳定 → ready=False（放行但标记）
		counter = {"i": 0}

		def _ever_changing(*a, **k):
			counter["i"] += 1
			return '{"ready": false, "stage": "requirejs", "n": %d}' % (counter["i"] * 10)

		with patch.object(s, "evaluate", AsyncMock(side_effect=_ever_changing)):
			st = asyncio.run(s.wait_for_page_settle(timeout=0.1, poll=0.0))
		assert st["ready"] is False
		assert st.get("timeout") is True

	def test_evaluate_exception_returns_error_dict(self):
		s = self._session()
		with patch.object(s, "evaluate", AsyncMock(side_effect=RuntimeError("cdp down"))):
			st = asyncio.run(s.wait_for_page_settle(timeout=1.0))
		assert st["ready"] is False
		assert "cdp down" in st.get("error", "")
