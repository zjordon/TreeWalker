"""Tests for the three-layer clear strategy and concatenation guard.

Covers the B站 title input fix (issue #6):
- _clear_text_field: 3-layer fallback (JS select+value='' → triple-click → Ctrl+A)
- _read_active_text / _force_set_value helpers
- type_text concatenation detection + native-setter fallback
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


# ── Shared fixture ────────────────────────────────────────────────────────────


@pytest.fixture()
def browser():
	from tree_walker.browser.session import BrowserSession
	from tree_walker.config import BrowserSettings

	bs = BrowserSession.__new__(BrowserSession)
	bs.client = MagicMock()
	bs.current_session_id = "sid1"
	bs.current_target_id = "t1"
	bs.settings = BrowserSettings()
	bs.client.send = MagicMock()
	bs.client.send.Input = MagicMock()
	bs.client.send.Input.dispatchKeyEvent = AsyncMock()
	bs.client.send.Input.dispatchMouseEvent = AsyncMock()
	bs.client.send.Input.insertText = AsyncMock()
	bs.client.send.Runtime = MagicMock()
	bs.client.send.Runtime.evaluate = AsyncMock(
		return_value={"result": {"value": True}},
	)
	return bs


# ── _read_active_text ─────────────────────────────────────────────────────────


class TestReadActiveText:
	@pytest.mark.asyncio
	async def test_returns_value_when_input(self, browser):
		browser.client.send.Runtime.evaluate = AsyncMock(
			return_value={"result": {"value": "hello"}},
		)
		assert await browser._read_active_text() == "hello"

	@pytest.mark.asyncio
	async def test_returns_empty_string_when_no_active(self, browser):
		browser.client.send.Runtime.evaluate = AsyncMock(
			return_value={"result": {"value": ""}},
		)
		assert await browser._read_active_text() == ""

	@pytest.mark.asyncio
	async def test_returns_empty_string_on_exception(self, browser):
		browser.client.send.Runtime.evaluate = AsyncMock(side_effect=RuntimeError("oops"))
		# Non-critical: should not raise, returns ""
		assert await browser._read_active_text() == ""

	@pytest.mark.asyncio
	async def test_returns_empty_when_value_is_none(self, browser):
		browser.client.send.Runtime.evaluate = AsyncMock(
			return_value={"result": {"value": None}},
		)
		assert await browser._read_active_text() == ""


# ── _force_set_value ──────────────────────────────────────────────────────────


class TestForceSetValue:
	@pytest.mark.asyncio
	async def test_expression_uses_native_setter_and_dispatches_events(self, browser):
		await browser._force_set_value("WORLD")

		browser.client.send.Runtime.evaluate.assert_called_once()
		expr = browser.client.send.Runtime.evaluate.call_args[0][0]["expression"]

		# Native setter bypass (React/Vue)
		assert "HTMLInputElement.prototype" in expr
		assert "HTMLTextAreaElement.prototype" in expr
		assert "Object.getOwnPropertyDescriptor" in expr
		# Both input and change dispatched
		assert "dispatchEvent(new Event('input'" in expr
		assert "dispatchEvent(new Event('change'" in expr
		# Text was JSON-escaped and embedded
		assert "\"WORLD\"" in expr

	@pytest.mark.asyncio
	async def test_handles_non_ascii_text(self, browser):
		await browser._force_set_value("你好")

		expr = browser.client.send.Runtime.evaluate.call_args[0][0]["expression"]
		# json.dumps escapes CJK as \uXXXX by default (ensure_ascii=True)
		assert "\\u4f60\\u597d" in expr

	@pytest.mark.asyncio
	async def test_non_critical_on_exception(self, browser):
		browser.client.send.Runtime.evaluate = AsyncMock(side_effect=RuntimeError("boom"))
		# Should not raise
		await browser._force_set_value("X")


# ── _clear_text_field three-layer strategy ────────────────────────────────────


class TestClearTextFieldStrategies:
	@pytest.mark.asyncio
	async def test_strategy1_js_success_short_circuits(self, browser):
		"""If JS strategy 1 returns cleared=True with empty final, return True immediately
		without falling through to triple-click or keyboard."""
		browser.client.send.Runtime.evaluate = AsyncMock(
			return_value={"result": {"value": {"cleared": True, "method": "value", "final": ""}}},
		)

		result = await browser._clear_text_field()

		assert result is True
		# Only strategy 1 should have called Runtime.evaluate (once)
		assert browser.client.send.Runtime.evaluate.call_count == 1
		# Strategies 2 & 3 must not have fired
		browser.client.send.Input.dispatchMouseEvent.assert_not_called()
		browser.client.send.Input.dispatchKeyEvent.assert_not_called()

	@pytest.mark.asyncio
	async def test_strategy1_contenteditable_branch(self, browser):
		"""The JS expression must contain the isContentEditable branch."""
		browser.client.send.Runtime.evaluate = AsyncMock(
			return_value={"result": {"value": {"cleared": True, "method": "contenteditable", "final": ""}}},
		)

		await browser._clear_text_field()

		expr = browser.client.send.Runtime.evaluate.call_args[0][0]["expression"]
		assert "isContentEditable" in expr
		assert "selectNodeContents" in expr

	@pytest.mark.asyncio
	async def test_strategy1_residual_falls_through_to_strategy2(self, browser):
		"""If JS reports cleared=True but final still has text, treat as failure → try strategy 2."""
		# call 1: strategy 1 reports residual text
		# call 2: strategy 2 fetches coordinates
		# call 3: _read_active_text after triple-click returns '' (success)
		browser.client.send.Runtime.evaluate = AsyncMock(side_effect=[
			{"result": {"value": {"cleared": True, "method": "value", "final": "stale"}}},
			{"result": {"value": '{"x": 100, "y": 50}'}},
			{"result": {"value": ""}},
		])

		result = await browser._clear_text_field()

		assert result is True
		# Strategy 2: triple-click + Delete
		mouse_calls = browser.client.send.Input.dispatchMouseEvent.call_args_list
		assert len(mouse_calls) == 2  # pressed + released
		assert mouse_calls[0][0][0]["clickCount"] == 3
		assert mouse_calls[0][0][0]["x"] == 100
		# Strategy 2 keyboard: Delete keyDown + keyUp
		key_calls = browser.client.send.Input.dispatchKeyEvent.call_args_list
		assert len(key_calls) == 2
		assert key_calls[0][0][0]["key"] == "Delete"

	@pytest.mark.asyncio
	async def test_strategy2_no_coords_falls_through_to_strategy3(self, browser):
		"""If strategy 2 can't get coordinates (null return), fall through to strategy 3."""
		# call 1: strategy 1 cleared=False (unsupported)
		# call 2: strategy 2 fetches coordinates → null
		# call 3: _read_active_text after strategy 3 returns '' (success)
		browser.client.send.Runtime.evaluate = AsyncMock(side_effect=[
			{"result": {"value": {"cleared": False, "error": "unsupported"}}},
			{"result": {"value": None}},
			{"result": {"value": ""}},
		])

		result = await browser._clear_text_field()

		assert result is True
		# Strategy 3: Ctrl+A keyDown/keyUp + Backspace keyDown/keyUp = 4 events
		key_calls = browser.client.send.Input.dispatchKeyEvent.call_args_list
		assert len(key_calls) == 4
		assert key_calls[0][0][0]["key"] == "a"
		assert key_calls[0][0][0]["modifiers"] == 2  # Ctrl
		assert key_calls[2][0][0]["key"] == "Backspace"
		# Strategy 2 mouse never fired (no coords)
		browser.client.send.Input.dispatchMouseEvent.assert_not_called()

	@pytest.mark.asyncio
	async def test_strategy3_failure_returns_false(self, browser):
		"""If all three strategies fail to actually empty the field, return False."""
		# call 1: strategy 1 cleared=False
		# call 2: strategy 2 fetches coordinates → null
		# call 3: _read_active_text after strategy 3 still has text
		browser.client.send.Runtime.evaluate = AsyncMock(side_effect=[
			{"result": {"value": {"cleared": False, "error": "unsupported"}}},
			{"result": {"value": None}},
			{"result": {"value": "still here"}},
		])

		result = await browser._clear_text_field()

		assert result is False

	@pytest.mark.asyncio
	async def test_strategy1_exception_falls_through(self, browser):
		"""If strategy 1 throws, strategy 2 should be attempted."""
		# call 1: strategy 1 raises
		# call 2: strategy 2 coordinates
		# call 3: _read_active_text after triple-click returns '' (success)
		browser.client.send.Runtime.evaluate = AsyncMock(side_effect=[
			RuntimeError("JS error"),
			{"result": {"value": '{"x": 1, "y": 1}'}},
			{"result": {"value": ""}},
		])

		result = await browser._clear_text_field()

		assert result is True

	@pytest.mark.asyncio
	async def test_strategy2_invalid_json_falls_through_to_strategy3(self, browser):
		"""If strategy 2 gets coordinates but they aren't valid JSON
		(e.g. page returned something unexpected), fall through to strategy 3."""
		# call 1: strategy 1 cleared=False
		# call 2: strategy 2 returns malformed coordinate string
		# call 3: _read_active_text after strategy 3
		browser.client.send.Runtime.evaluate = AsyncMock(side_effect=[
			{"result": {"value": {"cleared": False, "error": "unsupported"}}},
			{"result": {"value": "not-json{"}},  # malformed → json.loads raises
			{"result": {"value": ""}},  # strategy 3 readback
		])

		result = await browser._clear_text_field()

		# Strategy 3 succeeds (readback empty)
		assert result is True
		# Strategy 3 fired Ctrl+A + Backspace
		key_calls = browser.client.send.Input.dispatchKeyEvent.call_args_list
		assert any(c[0][0]["key"] == "a" and c[0][0]["modifiers"] == 2 for c in key_calls)

	@pytest.mark.asyncio
	async def test_strategy3_dispatch_failure_returns_false(self, browser):
		"""If strategy 3's CDP dispatchKeyEvent throws, return False (not raise)."""
		# Strategy 1 fails, strategy 2 can't get coords, strategy 3 throws on dispatch
		browser.client.send.Runtime.evaluate = AsyncMock(side_effect=[
			{"result": {"value": {"cleared": False, "error": "unsupported"}}},  # s1
			{"result": {"value": None}},  # s2 no coords
		])
		browser.client.send.Input.dispatchKeyEvent = AsyncMock(side_effect=RuntimeError("CDP dead"))

		result = await browser._clear_text_field()

		assert result is False


# ── type_text concatenation guard ─────────────────────────────────────────────


class TestTypeTextConcatenationGuard:
	@pytest.fixture()
	def browser_with_clear_ok(self, browser):
		"""Stub _clear_text_field to succeed so we can isolate the concatenation guard."""
		browser._clear_text_field = AsyncMock(return_value=True)
		# _trigger_framework_events will call Runtime.evaluate once
		# _read_active_text will call Runtime.evaluate once more
		return browser

	@pytest.mark.asyncio
	async def test_concatenation_triggers_force_set(self, browser_with_clear_ok):
		"""clear=True, readback='OLDNEW', text='NEW' → _force_set_value fires."""
		browser = browser_with_clear_ok
		# _trigger_framework_events call returns True; _read_active_text returns OLDNEW
		# Then _force_set_value triggers another Runtime.evaluate
		browser.client.send.Runtime.evaluate = AsyncMock(side_effect=[
			{"result": {"value": True}},  # _trigger_framework_events
			{"result": {"value": "OLDNEW"}},  # _read_active_text
			{"result": {"value": True}},  # _force_set_value
		])
		browser._force_set_value = AsyncMock()

		await browser.type_text("NEW", clear=True)

		browser._clear_text_field.assert_awaited_once()
		browser._force_set_value.assert_awaited_once_with("NEW")

	@pytest.mark.asyncio
	async def test_no_concatenation_when_value_matches(self, browser_with_clear_ok):
		"""clear=True, readback='NEW' (matches typed text) → _force_set_value NOT called."""
		browser = browser_with_clear_ok
		browser.client.send.Runtime.evaluate = AsyncMock(side_effect=[
			{"result": {"value": True}},  # _trigger_framework_events
			{"result": {"value": "NEW"}},  # _read_active_text (exact match)
		])
		browser._force_set_value = AsyncMock()

		await browser.type_text("NEW", clear=True)

		browser._force_set_value.assert_not_called()

	@pytest.mark.asyncio
	async def test_no_concatenation_when_readback_unrelated(self, browser_with_clear_ok):
		"""If readback is something totally different (not endswith/startswith text),
		don't trigger force-set — the field may have been formatted/autocompleted."""
		browser = browser_with_clear_ok
		browser.client.send.Runtime.evaluate = AsyncMock(side_effect=[
			{"result": {"value": True}},  # _trigger_framework_events
			{"result": {"value": "totally different"}},  # _read_active_text
		])
		browser._force_set_value = AsyncMock()

		await browser.type_text("NEW", clear=True)

		browser._force_set_value.assert_not_called()

	@pytest.mark.asyncio
	async def test_no_guard_when_clear_false(self, browser):
		"""clear=False must skip the concatenation check entirely."""
		# Spy on _read_active_text to ensure it isn't called
		browser._read_active_text = AsyncMock(return_value="OLDNEW")
		browser._force_set_value = AsyncMock()
		browser._clear_text_field = AsyncMock()

		await browser.type_text("NEW", clear=False)

		browser._clear_text_field.assert_not_called()
		browser._read_active_text.assert_not_called()
		browser._force_set_value.assert_not_called()

	@pytest.mark.asyncio
	async def test_guard_skipped_when_read_active_text_swallows_exception(self, browser_with_clear_ok):
		"""_read_active_text is non-critical and returns '' on internal failure;
		type_text should see empty string (not concatenation) and skip the guard."""
		browser = browser_with_clear_ok
		# _trigger_framework_events ok; _read_active_text's Runtime.evaluate raises
		# but _read_active_text catches it internally and returns ""
		browser.client.send.Runtime.evaluate = AsyncMock(side_effect=[
			{"result": {"value": True}},  # _trigger_framework_events
			RuntimeError("readback exploded"),  # _read_active_text's evaluate
		])
		browser._force_set_value = AsyncMock()

		# Must not raise; force_set must NOT be called (readback is "" — no concat detected)
		await browser.type_text("NEW", clear=True)

		browser._force_set_value.assert_not_called()

	@pytest.mark.asyncio
	async def test_starts_with_pattern_triggers_force_set(self, browser_with_clear_ok):
		"""Concatenation can also be detected when actual starts with text (text appended before old)."""
		browser = browser_with_clear_ok
		browser.client.send.Runtime.evaluate = AsyncMock(side_effect=[
			{"result": {"value": True}},
			{"result": {"value": "NEWOLD"}},  # startsWith('NEW') and longer
			{"result": {"value": True}},
		])
		browser._force_set_value = AsyncMock()

		await browser.type_text("NEW", clear=True)

		browser._force_set_value.assert_awaited_once_with("NEW")
