"""Tests for scroll: success echo, same-turn edge detection, param guards, session delta.

Covers:
- action layer: default + explicit params echo ``Scrolled {dir} {amount}
  viewport-heights`` with ``extracted_content == long_term_memory``; position
  percentage is echoed; already-at-edge surfaces an inline hint (down/up);
  a degraded position read (``vertical_percentage=None``) yields a bare echo;
  a failed ``scroll`` returns an error (scroll is NOT idempotent, unlike
  close_tab's soft-success); the action does NOT call ``get_state``
- param model: ``ScrollParams`` defaults amount=3/direction="down"; accepts
  bounds 1 and 10; rejects 0/11/negative; rejects non-literal direction;
  forbids extra fields; ``amount`` stays int (2.5 rejected)
- session layer: ``BrowserSession.scroll`` returns ``{vertical_percentage,
  at_edge}``; dispatches ``mouseWheel`` with ``deltaY = ±amount*clientHeight``
  (positive down, negative up); reads scroll position via one
  ``Runtime.evaluate``; detects at-bottom / not-at-bottom / at-top; a failed
  position read degrades without raising; a failed ``mouseWheel`` propagates;
  viewport height falls back to 1000 when ``cssVisualViewport`` is absent
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from tree_walker.browser.session import BrowserSession
from tree_walker.tools.actions import Tools
from tree_walker.tools.models import ScrollParams


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_browser(
	*, scroll_return: dict | None = None, scroll_raises: Exception | None = None,
) -> MagicMock:
	"""Stub BrowserSession: scroll returns a position dict (or raises).

	get_state is an AsyncMock purely so we can assert it is NOT awaited
	(scroll must not trigger a full DOM fetch).
	"""
	bs = MagicMock()
	if scroll_raises:
		bs.scroll = AsyncMock(side_effect=scroll_raises)
	else:
		bs.scroll = AsyncMock(
			return_value=scroll_return or {"vertical_percentage": 45.0, "at_edge": False},
		)
	bs.get_state = AsyncMock()
	return bs


# ── action layer ─────────────────────────────────────────────────────────────


class TestScrollAction:
	@pytest.mark.asyncio
	async def test_default_params_echo_and_call_scroll(self):
		browser = _make_browser(scroll_return={"vertical_percentage": 45.0, "at_edge": False})

		result = await Tools().execute("scroll", {}, browser)

		assert result.error is None
		assert "Scrolled down 3 viewport-heights" in result.extracted_content
		assert "(45.0% down)" in result.extracted_content
		assert "already at" not in result.extracted_content
		assert result.extracted_content == result.long_term_memory
		browser.scroll.assert_awaited_once_with("down", 3)

	@pytest.mark.asyncio
	async def test_explicit_direction_and_amount_pass_through(self):
		browser = _make_browser(scroll_return={"vertical_percentage": 10.0, "at_edge": False})

		result = await Tools().execute("scroll", {"direction": "up", "amount": 5}, browser)

		assert result.error is None
		assert "Scrolled up 5 viewport-heights" in result.extracted_content
		browser.scroll.assert_awaited_once_with("up", 5)

	@pytest.mark.asyncio
	async def test_at_bottom_echoes_edge_hint(self):
		browser = _make_browser(scroll_return={"vertical_percentage": 100.0, "at_edge": True})

		result = await Tools().execute("scroll", {"direction": "down", "amount": 10}, browser)

		assert "(100.0% down)" in result.extracted_content
		assert "(already at down, no further content)" in result.extracted_content
		browser.scroll.assert_awaited_once_with("down", 10)

	@pytest.mark.asyncio
	async def test_at_top_echoes_edge_hint(self):
		browser = _make_browser(scroll_return={"vertical_percentage": 0.0, "at_edge": True})

		result = await Tools().execute("scroll", {"direction": "up"}, browser)

		assert "(already at up, no further content)" in result.extracted_content
		browser.scroll.assert_awaited_once_with("up", 3)

	@pytest.mark.asyncio
	async def test_degraded_position_omits_percentage(self):
		# vertical_percentage=None (Runtime.evaluate failed) -> bare echo, no % / edge hint
		browser = _make_browser(scroll_return={"vertical_percentage": None, "at_edge": False})

		result = await Tools().execute("scroll", {}, browser)

		assert result.error is None
		assert result.extracted_content == "Scrolled down 3 viewport-heights"
		assert "%" not in result.extracted_content
		assert "already at" not in result.extracted_content

	@pytest.mark.asyncio
	async def test_cdp_failure_returns_error_not_soft_success(self):
		# scroll raises -> error (scroll is NOT idempotent, unlike close_tab's soft-success)
		browser = _make_browser(scroll_raises=RuntimeError("cdp timeout"))

		result = await Tools().execute("scroll", {"amount": 3}, browser)

		assert result.error == "Scroll failed: cdp timeout"
		assert result.extracted_content is None

	@pytest.mark.asyncio
	async def test_does_not_call_get_state(self):
		browser = _make_browser()

		await Tools().execute("scroll", {}, browser)

		browser.get_state.assert_not_awaited()  # scroll must not fetch full state


# ── param model ──────────────────────────────────────────────────────────────


class TestScrollParams:
	def test_defaults(self):
		p = ScrollParams()
		assert p.amount == 3
		assert p.direction == "down"

	def test_boundaries_accepted(self):
		assert ScrollParams(amount=1).amount == 1
		assert ScrollParams(amount=10).amount == 10

	def test_zero_rejected(self):
		with pytest.raises(ValidationError):
			ScrollParams(amount=0)

	def test_above_max_rejected(self):
		with pytest.raises(ValidationError):
			ScrollParams(amount=11)

	def test_negative_rejected(self):
		with pytest.raises(ValidationError):
			ScrollParams(amount=-2)

	def test_non_literal_direction_rejected(self):
		with pytest.raises(ValidationError):
			ScrollParams(direction="sideways")

	def test_extra_field_forbidden(self):
		with pytest.raises(ValidationError):
			ScrollParams(amount=3, pages=2)

	def test_amount_must_be_int(self):
		# amount stays int (G3 decision): a fractional float is rejected
		with pytest.raises(ValidationError):
			ScrollParams(amount=2.5)


# ── session layer ────────────────────────────────────────────────────────────


class TestScrollSession:
	def _make_session(
		self, *,
		client_height: int = 800, client_width: int = 1280,
		scroll_pos: dict | None = None, eval_raises: Exception | None = None,
		dispatch_raises: Exception | None = None,
	) -> tuple[BrowserSession, MagicMock]:
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid-1"
		scroll_pos = scroll_pos or {"sy": 3000, "sh": 10000, "ch": 1000}
		client = MagicMock()
		client.send.Page.getLayoutMetrics = AsyncMock(return_value={
			"cssVisualViewport": {"clientHeight": client_height, "clientWidth": client_width},
		})
		if dispatch_raises:
			client.send.Input.dispatchMouseEvent = AsyncMock(side_effect=dispatch_raises)
		else:
			client.send.Input.dispatchMouseEvent = AsyncMock(return_value={})
		if eval_raises:
			client.send.Runtime.evaluate = AsyncMock(side_effect=eval_raises)
		else:
			client.send.Runtime.evaluate = AsyncMock(return_value={
				"result": {"value": json.dumps(scroll_pos)},
			})
		s.client = client
		return s, client

	@pytest.mark.asyncio
	async def test_scroll_down_dispatches_positive_delta(self):
		s, client = self._make_session(client_height=800)

		await s.scroll("down", 3)

		client.send.Page.getLayoutMetrics.assert_awaited_once()
		params = client.send.Input.dispatchMouseEvent.await_args.args[0]
		assert params["type"] == "mouseWheel"
		assert params["deltaX"] == 0
		assert params["deltaY"] == 3 * 800  # positive = down
		assert params["x"] == 1280 / 2
		assert params["y"] == 800 / 2
		assert client.send.Input.dispatchMouseEvent.await_args.kwargs["session_id"] == "sid-1"

	@pytest.mark.asyncio
	async def test_scroll_up_dispatches_negative_delta(self):
		s, client = self._make_session(client_height=800)

		await s.scroll("up", 3)

		params = client.send.Input.dispatchMouseEvent.await_args.args[0]
		assert params["deltaY"] == -(3 * 800)  # negative = up

	@pytest.mark.asyncio
	async def test_reads_position_via_runtime_evaluate(self):
		s, client = self._make_session()

		await s.scroll("down", 3)

		client.send.Runtime.evaluate.assert_awaited_once()
		expr = client.send.Runtime.evaluate.await_args.args[0]["expression"]
		assert "scrollTop" in expr
		assert "scrollHeight" in expr

	@pytest.mark.asyncio
	async def test_at_bottom_detected(self):
		# sy+ch = 9000+1000 = 10000 >= sh-1 = 9999 -> at_edge True, pct 100.0
		s, client = self._make_session(scroll_pos={"sy": 9000, "sh": 10000, "ch": 1000})

		position = await s.scroll("down", 3)

		assert position["at_edge"] is True
		assert position["vertical_percentage"] == 100.0

	@pytest.mark.asyncio
	async def test_not_at_bottom(self):
		# max_top = 9000; pct = 3000/9000*100 = 33.333 -> 33.3
		s, client = self._make_session(scroll_pos={"sy": 3000, "sh": 10000, "ch": 1000})

		position = await s.scroll("down", 3)

		assert position["at_edge"] is False
		assert position["vertical_percentage"] == 33.3

	@pytest.mark.asyncio
	async def test_at_top_detected(self):
		s, client = self._make_session(scroll_pos={"sy": 0, "sh": 10000, "ch": 1000})

		position = await s.scroll("up", 3)

		assert position["at_edge"] is True  # sy <= 1
		assert position["vertical_percentage"] == 0.0

	@pytest.mark.asyncio
	async def test_position_read_failure_degrades(self):
		# Runtime.evaluate fails -> degraded dict; the scroll itself still happened
		s, client = self._make_session(eval_raises=RuntimeError("eval failed"))

		position = await s.scroll("down", 3)

		assert position == {"vertical_percentage": None, "at_edge": False}
		client.send.Input.dispatchMouseEvent.assert_awaited_once()

	@pytest.mark.asyncio
	async def test_dispatch_failure_propagates(self):
		# mouseWheel itself fails -> scroll() raises (caught by action layer's try/except)
		s, client = self._make_session(dispatch_raises=RuntimeError("wheel failed"))

		with pytest.raises(RuntimeError, match="wheel failed"):
			await s.scroll("down", 3)

	@pytest.mark.asyncio
	async def test_fallback_viewport_height(self):
		# getLayoutMetrics returns no cssVisualViewport -> fallback 1000
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid-1"
		client = MagicMock()
		client.send.Page.getLayoutMetrics = AsyncMock(return_value={})
		client.send.Input.dispatchMouseEvent = AsyncMock(return_value={})
		client.send.Runtime.evaluate = AsyncMock(return_value={
			"result": {"value": json.dumps({"sy": 0, "sh": 1000, "ch": 1000})},
		})
		s.client = client

		await s.scroll("down", 2)

		params = client.send.Input.dispatchMouseEvent.await_args.args[0]
		assert params["deltaY"] == 2 * 1000  # fallback viewport height
