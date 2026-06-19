"""Tests for switch_tab: success echo, suffix-collision guard, get_tabs plumbing.

Covers:
- action layer: success echoes ``Switched to tab [{id}] title (url)`` into
  ``extracted_content == long_term_memory``; a full target_id also matches;
  not-found returns an error listing open tabs; two tabs sharing a suffix
  returns a conflict error; the action calls ``browser.get_tabs()`` (NOT
  ``get_state``)
- param model: ``SwitchTabParams`` rejects empty ``tab_id`` (min_length=1) and
  forbids extra fields
- session layer: ``BrowserSession.switch_tab`` emits ``activateTarget`` then
  ``attachToTarget(flatten=True)``, updates ``current_target_id`` /
  ``current_session_id``, and clears both selector_map caches;
  ``BrowserSession.get_tabs`` returns only ``type == "page"`` targets
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from tree_walker.agent.views import ActionResult  # noqa: F401  (asserts shape only)
from tree_walker.browser.session import BrowserSession
from tree_walker.browser.views import TabInfo
from tree_walker.tools.actions import Tools
from tree_walker.tools.models import SwitchTabParams


# ── helpers ──────────────────────────────────────────────────────────────────


async def _no_sleep(_seconds: float = 0) -> None:
	"""asyncio.sleep stand-in for tests (no real waiting)."""
	return None


def _tab(target_id: str, *, title: str = "Page", url: str = "https://x.test") -> TabInfo:
	return TabInfo(target_id=target_id, title=title, url=url)


def _make_browser(tabs: list[TabInfo]) -> MagicMock:
	"""Stub BrowserSession: get_tabs returns the given list; switch_tab is AsyncMock.

	get_state is an AsyncMock too, purely so we can assert it is NOT awaited
	(switch_tab must use the lightweight get_tabs path, not full get_state).
	"""
	bs = MagicMock()
	bs.get_tabs = AsyncMock(return_value=tabs)
	bs.switch_tab = AsyncMock()
	bs.get_state = AsyncMock()
	return bs


# ── action layer ─────────────────────────────────────────────────────────────


class TestSwitchTabAction:
	@pytest.mark.asyncio
	async def test_success_echoes_target_and_calls_switch_tab(self):
		tabs = [_tab("ABCDEF1234", title="Inbox", url="https://mail.test/inbox")]
		browser = _make_browser(tabs)

		result = await Tools().execute("switch_tab", {"tab_id": "1234"}, browser)

		assert result.error is None
		assert "Switched to tab [1234]" in result.extracted_content
		assert "Inbox" in result.extracted_content
		assert "https://mail.test/inbox" in result.extracted_content
		assert result.extracted_content == result.long_term_memory
		browser.switch_tab.assert_awaited_once_with("ABCDEF1234")

	@pytest.mark.asyncio
	async def test_full_target_id_also_matches(self):
		# min_length=1 keeps the full target_id usable, not only the 4-char suffix
		tabs = [_tab("ABCDEF1234")]
		browser = _make_browser(tabs)

		result = await Tools().execute("switch_tab", {"tab_id": "ABCDEF1234"}, browser)

		assert result.error is None
		browser.switch_tab.assert_awaited_once_with("ABCDEF1234")

	@pytest.mark.asyncio
	async def test_not_found_lists_open_tabs_and_uses_get_tabs(self):
		tabs = [_tab("ABCDEF1234", title="Inbox"), _tab("BBBBBB9999", title="Docs")]
		browser = _make_browser(tabs)

		result = await Tools().execute("switch_tab", {"tab_id": "0000"}, browser)

		assert result.error is not None
		assert "No tab ending with '0000'" in result.error
		# error surfaces the open tabs so the LLM can pick a real one
		assert "1234" in result.error and "9999" in result.error
		browser.switch_tab.assert_not_awaited()
		browser.get_tabs.assert_awaited()
		browser.get_state.assert_not_awaited()  # G2: must not fetch full state

	@pytest.mark.asyncio
	async def test_suffix_collision_returns_error(self):
		# two tabs share suffix "1234" -> refuse rather than silently switch the first
		tabs = [_tab("AAA1111234", title="One"), _tab("BBB2221234", title="Two")]
		browser = _make_browser(tabs)

		result = await Tools().execute("switch_tab", {"tab_id": "1234"}, browser)

		assert result.error is not None
		assert "Multiple tabs match '1234'" in result.error
		assert "2" in result.error
		browser.switch_tab.assert_not_awaited()


# ── param model ──────────────────────────────────────────────────────────────


class TestSwitchTabParams:
	def test_empty_tab_id_rejected(self):
		with pytest.raises(ValidationError):
			SwitchTabParams(tab_id="")

	def test_non_empty_accepted(self):
		p = SwitchTabParams(tab_id="1234")
		assert p.tab_id == "1234"

	def test_extra_forbidden(self):
		with pytest.raises(ValidationError):
			SwitchTabParams(tab_id="1234", target_id="nope")


# ── session layer ────────────────────────────────────────────────────────────


class TestSwitchTabSession:
	def _make_session(self) -> tuple[BrowserSession, MagicMock]:
		s = BrowserSession.__new__(BrowserSession)
		s._cached_selector_map = {"stale": 1}
		s._previous_cached_selector_map = {"stale": 2}
		s.current_target_id = "OLD"
		s.current_session_id = "old-sid"
		s._wait_for_page_settle = AsyncMock()
		client = MagicMock()
		client.send.Target.activateTarget = AsyncMock(return_value={})
		client.send.Target.attachToTarget = AsyncMock(
			return_value={"sessionId": "new-sid"},
		)
		s.client = client
		return s, client

	@pytest.mark.asyncio
	async def test_switch_tab_activates_then_attaches_and_updates_state(self):
		s, client = self._make_session()

		await s.switch_tab("ABCDEF1234")

		client.send.Target.activateTarget.assert_awaited_once_with(
			{"targetId": "ABCDEF1234"},
		)
		client.send.Target.attachToTarget.assert_awaited_once_with(
			{"targetId": "ABCDEF1234", "flatten": True},
		)
		assert s.current_target_id == "ABCDEF1234"
		assert s.current_session_id == "new-sid"
		assert s._cached_selector_map is None
		assert s._previous_cached_selector_map is None

	@pytest.mark.asyncio
	async def test_get_tabs_returns_only_page_targets(self):
		s = BrowserSession.__new__(BrowserSession)
		client = MagicMock()
		client.send.Target.getTargets = AsyncMock(
			return_value={
				"targetInfos": [
					{"type": "page", "targetId": "PAGE1", "url": "https://a.test", "title": "A"},
					{"type": "iframe", "targetId": "IFRAME", "url": "https://b.test", "title": "B"},
					{"type": "worker", "targetId": "WORKER", "url": "", "title": ""},
					{"type": "page", "targetId": "PAGE2", "url": "https://c.test", "title": "C"},
				],
			},
		)
		s.client = client

		tabs = await s.get_tabs()

		assert [t.target_id for t in tabs] == ["PAGE1", "PAGE2"]
		assert tabs[0].url == "https://a.test"


# ── new-tab detection (G7) ───────────────────────────────────────────────────


class TestDetectNewTab:
	"""G7: _action_click reuses _detect_new_tab_opened; cover its three paths."""

	@pytest.mark.asyncio
	async def test_no_new_tab_returns_empty(self, monkeypatch):
		monkeypatch.setattr(asyncio, "sleep", _no_sleep)
		browser = MagicMock()
		browser.get_tabs = AsyncMock(return_value=[_tab("AAA1111234")])
		browser.switch_tab = AsyncMock()

		# the only tab was already there before -> no diff
		note = await Tools()._detect_new_tab_opened(browser, ("AAA1111234",))

		assert note == ""
		browser.switch_tab.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_new_tab_auto_switches_and_echoes_id(self, monkeypatch):
		monkeypatch.setattr(asyncio, "sleep", _no_sleep)
		new_tab = _tab("NEW1234567", title="Opened", url="https://new.test")
		browser = MagicMock()
		browser.get_tabs = AsyncMock(return_value=[new_tab])
		browser.switch_tab = AsyncMock()

		note = await Tools()._detect_new_tab_opened(browser, ())  # nothing before

		assert "auto-switched" in note
		assert "[4567]" in note  # last 4 of NEW1234567
		browser.switch_tab.assert_awaited_once_with("NEW1234567")

	@pytest.mark.asyncio
	async def test_switch_failure_soft_degrades(self, monkeypatch):
		monkeypatch.setattr(asyncio, "sleep", _no_sleep)
		browser = MagicMock()
		browser.get_tabs = AsyncMock(return_value=[_tab("NEW1234567")])
		browser.switch_tab = AsyncMock(side_effect=RuntimeError("boom"))

		note = await Tools()._detect_new_tab_opened(browser, ())

		assert "use switch_tab" in note
