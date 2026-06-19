"""Tests for close_tab: success echo, suffix-collision guard, soft-degrade, session fallback.

Covers:
- action layer: success echoes ``Closed tab [{id}] title (url)`` into
  ``extracted_content == long_term_memory``; a full target_id also matches;
  empty ``tab_id`` closes the current tab; not-found returns an error listing
  open tabs; two tabs sharing a suffix returns a conflict error; a failed
  ``close_tab`` (already-closed/invalid target) soft-degrades to a non-error
  echo; the action calls ``browser.get_tabs()`` (NOT ``get_state``)
- param model: ``CloseTabParams`` allows empty ``tab_id`` (default=""), accepts
  non-empty, and forbids extra fields (distinct from ``SwitchTabParams``)
- session layer: ``BrowserSession.close_tab`` emits ``closeTarget``; closing the
  current tab switches to another remaining page (via ``switch_tab``); closing a
  non-current tab does not switch; closing the last page creates about:blank
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from tree_walker.browser.session import BrowserSession
from tree_walker.browser.views import TabInfo
from tree_walker.tools.actions import Tools
from tree_walker.tools.models import CloseTabParams


# ── helpers ──────────────────────────────────────────────────────────────────


def _tab(target_id: str, *, title: str = "Page", url: str = "https://x.test") -> TabInfo:
	return TabInfo(target_id=target_id, title=title, url=url)


def _make_browser(
	tabs: list[TabInfo], *, current_target_id: str | None = "AAA1111234",
) -> MagicMock:
	"""Stub BrowserSession: get_tabs returns the given list; close_tab is AsyncMock.

	get_state is an AsyncMock too, purely so we can assert it is NOT awaited
	(close_tab must use the lightweight get_tabs path, not full get_state).
	"""
	bs = MagicMock()
	bs.get_tabs = AsyncMock(return_value=tabs)
	bs.close_tab = AsyncMock()
	bs.get_state = AsyncMock()
	bs.current_target_id = current_target_id
	return bs


# ── action layer ─────────────────────────────────────────────────────────────


class TestCloseTabAction:
	@pytest.mark.asyncio
	async def test_success_echoes_closed_tab_and_calls_close_tab(self):
		tabs = [_tab("ABCDEF1234", title="Inbox", url="https://mail.test/inbox")]
		browser = _make_browser(tabs)

		result = await Tools().execute("close_tab", {"tab_id": "1234"}, browser)

		assert result.error is None
		assert "Closed tab [1234]" in result.extracted_content
		assert "Inbox" in result.extracted_content
		assert "https://mail.test/inbox" in result.extracted_content
		assert result.extracted_content == result.long_term_memory
		browser.close_tab.assert_awaited_once_with("ABCDEF1234")

	@pytest.mark.asyncio
	async def test_full_target_id_also_matches(self):
		tabs = [_tab("ABCDEF1234")]
		browser = _make_browser(tabs)

		result = await Tools().execute("close_tab", {"tab_id": "ABCDEF1234"}, browser)

		assert result.error is None
		browser.close_tab.assert_awaited_once_with("ABCDEF1234")

	@pytest.mark.asyncio
	async def test_empty_tab_id_closes_current_tab(self):
		# tab_id="" closes the current tab; echo still names it
		tabs = [_tab("AAA1111234", title="Current", url="https://cur.test")]
		browser = _make_browser(tabs, current_target_id="AAA1111234")

		result = await Tools().execute("close_tab", {"tab_id": ""}, browser)

		assert result.error is None
		assert "Closed tab" in result.extracted_content
		assert "[1234]" in result.extracted_content  # last 4 of current target_id
		browser.close_tab.assert_awaited_once_with("AAA1111234")

	@pytest.mark.asyncio
	async def test_empty_tab_id_with_no_current_tab_returns_error(self):
		# tab_id="" but no current tab -> explicit error (defensive guard)
		browser = _make_browser([], current_target_id=None)

		result = await Tools().execute("close_tab", {"tab_id": ""}, browser)

		assert result.error == "No current tab to close"
		browser.close_tab.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_not_found_lists_open_tabs_and_uses_get_tabs(self):
		tabs = [_tab("ABCDEF1234", title="Inbox"), _tab("BBBBBB9999", title="Docs")]
		browser = _make_browser(tabs)

		result = await Tools().execute("close_tab", {"tab_id": "0000"}, browser)

		assert result.error is not None
		assert "No tab ending with '0000'" in result.error
		# error surfaces the open tabs so the LLM can pick a real one
		assert "1234" in result.error and "9999" in result.error
		browser.close_tab.assert_not_awaited()
		browser.get_tabs.assert_awaited()
		browser.get_state.assert_not_awaited()  # G2: must not fetch full state

	@pytest.mark.asyncio
	async def test_suffix_collision_returns_error(self):
		# two tabs share suffix "1234" -> refuse rather than silently close the first
		tabs = [_tab("AAA1111234", title="One"), _tab("BBB2221234", title="Two")]
		browser = _make_browser(tabs)

		result = await Tools().execute("close_tab", {"tab_id": "1234"}, browser)

		assert result.error is not None
		assert "Multiple tabs match '1234'" in result.error
		assert "2" in result.error
		browser.close_tab.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_stale_target_soft_degrades(self):
		# close_tab raises (target already closed/invalid) -> soft-success, not error
		tabs = [_tab("ABCDEF1234", title="Inbox")]
		browser = _make_browser(tabs)
		browser.close_tab = AsyncMock(side_effect=RuntimeError("no such target"))

		result = await Tools().execute("close_tab", {"tab_id": "1234"}, browser)

		assert result.error is None
		assert "was already closed or invalid" in result.extracted_content
		assert result.extracted_content == result.long_term_memory


# ── param model ──────────────────────────────────────────────────────────────


class TestCloseTabParams:
	def test_empty_tab_id_allowed(self):
		# distinct from SwitchTabParams (min_length=1) — empty means "close current"
		assert CloseTabParams(tab_id="").tab_id == ""

	def test_default_is_empty(self):
		assert CloseTabParams().tab_id == ""

	def test_non_empty_accepted(self):
		assert CloseTabParams(tab_id="1234").tab_id == "1234"

	def test_extra_forbidden(self):
		with pytest.raises(ValidationError):
			CloseTabParams(tab_id="1234", target_id="nope")


# ── session layer ────────────────────────────────────────────────────────────


class TestCloseTabSession:
	def _make_session(
		self, *, targets_after: dict, current: str = "CURRENT",
	) -> tuple[BrowserSession, MagicMock]:
		s = BrowserSession.__new__(BrowserSession)
		s.current_target_id = current
		s.current_session_id = "cur-sid"
		s._wait_for_page_settle = AsyncMock()
		s.create_tab = AsyncMock(return_value="BLANK")
		client = MagicMock()
		client.send.Target.closeTarget = AsyncMock(return_value={})
		client.send.Target.getTargets = AsyncMock(return_value=targets_after)
		client.send.Target.activateTarget = AsyncMock(return_value={})
		client.send.Target.attachToTarget = AsyncMock(
			return_value={"sessionId": "new-sid"},
		)
		s.client = client
		return s, client

	@pytest.mark.asyncio
	async def test_close_current_tab_switches_to_another(self):
		# closing the current tab finds another page and switches (real switch_tab)
		after = {"targetInfos": [
			{"type": "page", "targetId": "OTHER", "url": "https://o.test", "title": "O"},
		]}
		s, client = self._make_session(targets_after=after, current="CURRENT")

		await s.close_tab("CURRENT")

		client.send.Target.closeTarget.assert_awaited_once_with({"targetId": "CURRENT"})
		client.send.Target.getTargets.assert_awaited()
		client.send.Target.activateTarget.assert_awaited_once_with({"targetId": "OTHER"})
		client.send.Target.attachToTarget.assert_awaited_once_with(
			{"targetId": "OTHER", "flatten": True},
		)
		assert s.current_target_id == "OTHER"  # switch_tab updated it
		s.create_tab.assert_not_awaited()  # another page existed -> no blank

	@pytest.mark.asyncio
	async def test_close_non_current_tab_does_not_switch(self):
		after = {"targetInfos": [
			{"type": "page", "targetId": "CURRENT", "url": "", "title": ""},
		]}
		s, client = self._make_session(targets_after=after, current="CURRENT")

		await s.close_tab("OTHER")  # not the current tab

		client.send.Target.closeTarget.assert_awaited_once_with({"targetId": "OTHER"})
		client.send.Target.getTargets.assert_not_awaited()  # only queried when was_current
		client.send.Target.activateTarget.assert_not_awaited()
		assert s.current_target_id == "CURRENT"  # unchanged

	@pytest.mark.asyncio
	async def test_close_last_tab_creates_blank(self):
		# G9: closing the current tab with no other page -> create about:blank
		after = {"targetInfos": []}
		s, client = self._make_session(targets_after=after, current="CURRENT")

		await s.close_tab("CURRENT")

		client.send.Target.closeTarget.assert_awaited_once_with({"targetId": "CURRENT"})
		client.send.Target.getTargets.assert_awaited()
		client.send.Target.activateTarget.assert_not_awaited()  # no page to switch to
		s.create_tab.assert_awaited_once_with("about:blank")
