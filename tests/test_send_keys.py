"""Tests for send_keys: success echo, error handling, alias normalization,
complete special-key VK mapping, three-route dispatch (combo / special / text),
param guards, and session dispatch.

Covers:
- action layer: success echoes ``Sent keys '...'`` with
  ``extracted_content == long_term_memory`` for single keys, combos, and text;
  a failed ``send_keys`` returns ``error="Send keys failed: ..."`` (send_keys is
  NOT idempotent, unlike close_tab's soft-success); the action passes the raw
  keys string through unchanged and does NOT call ``get_state``; success stays
  None (echo rule)
- param model: ``SendKeysParams`` accepts named keys / combos / text / a single
  space; rejects the empty string (``min_length=1``); forbids extra fields
- session layer: alias normalization (ctrl/return/up/esc/space/del); single
  special keys dispatch keyDown/char/keyUp with correct VK (Enter/Tab/Arrow/F5/
  Home/End/PageUp/PageDown); combinations carry modifiers — and a single-char
  main key (Control+a) goes through the key path so Ctrl+A select-all keeps its
  modifier; unknown modifiers soft-degrade (warn + skip); plain text is typed
  char-by-char via ``_type_char``; CJK routes to char-only events; dispatch
  failures propagate; ``+`` text is treated as a combination (known ambiguity)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from tree_walker.browser.session import BrowserSession
from tree_walker.tools.actions import Tools
from tree_walker.tools.models import SendKeysParams


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_browser(*, send_raises: Exception | None = None) -> MagicMock:
	"""Stub BrowserSession: send_keys returns None (or raises)."""
	bs = MagicMock()
	if send_raises:
		bs.send_keys = AsyncMock(side_effect=send_raises)
	else:
		bs.send_keys = AsyncMock(return_value=None)
	bs.get_state = AsyncMock()  # only asserted as NOT awaited
	return bs


def _events(client: MagicMock) -> list[dict]:
	"""All Input.dispatchKeyEvent param dicts, in dispatch order."""
	return [c.args[0] for c in client.send.Input.dispatchKeyEvent.call_args_list]


# ── action layer ─────────────────────────────────────────────────────────────


class TestSendKeysAction:
	@pytest.mark.asyncio
	async def test_single_key_echo(self):
		browser = _make_browser()

		result = await Tools().execute("send_keys", {"keys": "Enter"}, browser)

		assert result.error is None
		assert result.extracted_content == "Sent keys 'Enter'"
		assert result.extracted_content == result.long_term_memory
		browser.send_keys.assert_awaited_once_with("Enter")

	@pytest.mark.asyncio
	async def test_combination_echo(self):
		browser = _make_browser()

		result = await Tools().execute("send_keys", {"keys": "Control+a"}, browser)

		assert result.extracted_content == "Sent keys 'Control+a'"
		browser.send_keys.assert_awaited_once_with("Control+a")

	@pytest.mark.asyncio
	async def test_text_echo(self):
		browser = _make_browser()

		result = await Tools().execute("send_keys", {"keys": "hello"}, browser)

		assert result.extracted_content == "Sent keys 'hello'"

	@pytest.mark.asyncio
	async def test_cdp_failure_returns_error(self):
		# send_keys is NOT idempotent (a key can submit a form / navigate), so a
		# CDP failure surfaces as error — mirrors scroll, unlike close_tab.
		browser = _make_browser(send_raises=RuntimeError("cdp timeout"))

		result = await Tools().execute("send_keys", {"keys": "Enter"}, browser)

		assert result.error == "Send keys failed: cdp timeout"
		assert result.extracted_content is None

	@pytest.mark.asyncio
	async def test_does_not_call_get_state(self):
		browser = _make_browser()

		await Tools().execute("send_keys", {"keys": "Tab"}, browser)

		browser.get_state.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_passes_raw_keys_unchanged(self):
		# Aliases are normalized in the session layer; the action passes the raw
		# string through so the echo reflects what the LLM actually sent.
		browser = _make_browser()

		await Tools().execute("send_keys", {"keys": "Return"}, browser)

		browser.send_keys.assert_awaited_once_with("Return")

	@pytest.mark.asyncio
	async def test_success_stays_none(self):
		# Echo rule (ActionResult.validate_success_requires_done): a non-done
		# action that succeeds leaves success as None, never True.
		browser = _make_browser()

		result = await Tools().execute("send_keys", {"keys": "Enter"}, browser)

		assert result.success is None
		assert result.is_done is False


# ── param model ──────────────────────────────────────────────────────────────


class TestSendKeysParams:
	def test_accepts_named_key(self):
		assert SendKeysParams(keys="Enter").keys == "Enter"

	def test_accepts_combination(self):
		assert SendKeysParams(keys="Control+a").keys == "Control+a"

	def test_accepts_text(self):
		assert SendKeysParams(keys="hello world").keys == "hello world"

	def test_accepts_single_space(self):
		# " " is the space key; min_length=1 must not strip it.
		assert SendKeysParams(keys=" ").keys == " "

	def test_rejects_empty_string(self):
		with pytest.raises(ValidationError):
			SendKeysParams(keys="")

	def test_extra_field_forbidden(self):
		with pytest.raises(ValidationError):
			SendKeysParams(keys="Enter", foo=1)


# ── session layer ────────────────────────────────────────────────────────────


class TestSendKeysSession:
	def _make_session(self) -> tuple[BrowserSession, MagicMock]:
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid-1"
		client = MagicMock()
		client.send.Input.dispatchKeyEvent = AsyncMock(return_value={})
		s.client = client
		return s, client

	# ── alias normalization ─────────────────────────────────────────────────

	@pytest.mark.asyncio
	async def test_control_and_ctrl_same_sequence(self):
		s1, c1 = self._make_session()
		s2, c2 = self._make_session()

		await s1.send_keys("Control+Enter")
		await s2.send_keys("Ctrl+Enter")

		# Both produce identical dispatches; both carry the Control bit (2).
		assert _events(c1) == _events(c2)
		assert _events(c1)[0]["modifiers"] & 2  # Control

	@pytest.mark.asyncio
	async def test_return_alias(self):
		s, client = self._make_session()

		await s.send_keys("return")

		events = _events(client)
		assert [e["type"] for e in events] == ["keyDown", "char", "keyUp"]
		assert events[0]["key"] == "Enter"
		assert events[1]["text"] == "\r"

	@pytest.mark.asyncio
	async def test_up_alias(self):
		s, client = self._make_session()

		await s.send_keys("up")

		keydown = _events(client)[0]
		assert keydown["key"] == "ArrowUp"
		assert keydown["windowsVirtualKeyCode"] == 38
		assert not [e for e in _events(client) if e["type"] == "char"]

	@pytest.mark.asyncio
	async def test_esc_alias(self):
		s, client = self._make_session()

		await s.send_keys("esc")

		assert _events(client)[0]["key"] == "Escape"

	@pytest.mark.asyncio
	async def test_space_alias_types_via_char_path(self):
		s, client = self._make_session()

		await s.send_keys("space")

		# " " normalizes to a single space and is typed via _type_char (matches
		# browser-use, which routes space through the char path, not special keys).
		events = _events(client)
		assert [e["type"] for e in events] == ["keyDown", "char", "keyUp"]
		assert events[0]["key"] == " "
		assert events[0]["code"] == "Space"
		assert events[0]["windowsVirtualKeyCode"] == 32
		assert events[1]["text"] == " "

	@pytest.mark.asyncio
	async def test_del_alias(self):
		s, client = self._make_session()

		await s.send_keys("del")

		keydown = _events(client)[0]
		assert keydown["key"] == "Delete"
		assert keydown["windowsVirtualKeyCode"] == 46

	# ── single special keys ─────────────────────────────────────────────────

	@pytest.mark.asyncio
	async def test_enter_dispatches_keydown_char_keyup(self):
		s, client = self._make_session()

		await s.send_keys("Enter")

		events = _events(client)
		assert [e["type"] for e in events] == ["keyDown", "char", "keyUp"]
		assert events[0]["key"] == "Enter"
		assert events[0]["windowsVirtualKeyCode"] == 13
		assert events[1]["text"] == "\r"

	@pytest.mark.asyncio
	async def test_tab_dispatches_char(self):
		s, client = self._make_session()

		await s.send_keys("Tab")

		events = _events(client)
		assert [e["type"] for e in events] == ["keyDown", "char", "keyUp"]
		assert events[1]["text"] == "\t"

	@pytest.mark.asyncio
	async def test_arrow_key_vk_no_char(self):
		s, client = self._make_session()

		await s.send_keys("ArrowDown")

		keydown = _events(client)[0]
		assert keydown["key"] == "ArrowDown"
		assert keydown["windowsVirtualKeyCode"] == 40
		assert not [e for e in _events(client) if e["type"] == "char"]

	@pytest.mark.asyncio
	async def test_f5_vk_and_code(self):
		s, client = self._make_session()

		await s.send_keys("F5")

		keydown = _events(client)[0]
		assert keydown["key"] == "F5"
		assert keydown["windowsVirtualKeyCode"] == 0x74
		assert keydown["code"] == "F5"

	@pytest.mark.parametrize("key,vk", [
		("Home", 36), ("End", 35), ("PageUp", 33), ("PageDown", 34),
	])
	@pytest.mark.asyncio
	async def test_nav_key_vk(self, key, vk):
		s, client = self._make_session()

		await s.send_keys(key)

		assert _events(client)[0]["windowsVirtualKeyCode"] == vk

	# ── combinations (single-char main-key fix) ─────────────────────────────

	@pytest.mark.asyncio
	async def test_control_a_keeps_modifier(self):
		# Core fix: the 'a' in Ctrl+a must go through the key path carrying the
		# Control modifier so Ctrl+A select-all works.
		s, client = self._make_session()

		await s.send_keys("Control+a")

		events = _events(client)
		assert [e["type"] for e in events] == ["keyDown", "char", "keyUp"]
		assert events[0]["key"] == "a"
		assert events[0]["modifiers"] == 2  # Control
		assert events[1]["text"] == "a"

	@pytest.mark.asyncio
	async def test_shift_t_carries_shift_modifier(self):
		s, client = self._make_session()

		await s.send_keys("Shift+T")

		events = _events(client)
		# Per-char path: keyDown uses the lowercase base key 't' with Shift set
		# (matches _type_char); the char event inserts 'T'.
		assert events[0]["key"] == "t"
		assert events[0]["modifiers"] & 8  # Shift
		assert events[1]["text"] == "T"

	@pytest.mark.asyncio
	async def test_alt_f4(self):
		s, client = self._make_session()

		await s.send_keys("Alt+F4")

		keydown = _events(client)[0]
		assert keydown["key"] == "F4"
		assert keydown["modifiers"] & 1  # Alt
		assert keydown["windowsVirtualKeyCode"] == 0x73

	@pytest.mark.asyncio
	async def test_multi_modifiers(self):
		s, client = self._make_session()

		await s.send_keys("Control+Shift+a")

		assert _events(client)[0]["modifiers"] == 2 | 8  # Control | Shift

	@pytest.mark.asyncio
	async def test_unknown_modifier_soft_degrades(self):
		# 'Foo' is not a modifier: warn + skip, still dispatch 'a' with no modifier.
		s, client = self._make_session()

		await s.send_keys("Foo+a")  # must not raise

		events = _events(client)
		assert [e["type"] for e in events] == ["keyDown", "char", "keyUp"]
		assert events[0]["modifiers"] == 0
		assert events[1]["text"] == "a"

	# ── text branch ─────────────────────────────────────────────────────────

	@pytest.mark.asyncio
	async def test_text_types_char_by_char(self):
		s, client = self._make_session()

		await s.send_keys("hi")

		# Each char dispatches keyDown/char/keyUp; char texts are h, i in order.
		char_texts = [e["text"] for e in _events(client) if e["type"] == "char"]
		assert char_texts == ["h", "i"]

	@pytest.mark.asyncio
	async def test_text_reuses_type_char(self):
		s, client = self._make_session()
		s._type_char = AsyncMock(return_value=None)

		await s.send_keys("hi")

		assert s._type_char.await_count == 2
		assert s._type_char.await_args_list[0].args[0] == "h"
		assert s._type_char.await_args_list[1].args[0] == "i"

	@pytest.mark.asyncio
	async def test_cjk_text_routes_to_char_only(self):
		s, client = self._make_session()

		await s.send_keys("你好")

		events = _events(client)
		# _type_char skips keyDown/keyUp for non-ASCII; only char events fire.
		assert all(e["type"] == "char" for e in events)
		assert [e["text"] for e in events] == ["你", "好"]

	# ── exceptions / boundaries ─────────────────────────────────────────────

	@pytest.mark.asyncio
	async def test_dispatch_failure_propagates(self):
		# A CDP failure bubbles out of send_keys (the action layer catches it).
		s, client = self._make_session()
		client.send.Input.dispatchKeyEvent = AsyncMock(side_effect=RuntimeError("boom"))

		with pytest.raises(RuntimeError, match="boom"):
			await s.send_keys("Enter")

	@pytest.mark.asyncio
	async def test_plus_text_treated_as_combination(self):
		# Known '+' ambiguity: 'a+b' is parsed as a combo (modifier 'a' ignored,
		# main key 'b'), not literal text. Pin this behavior.
		s, client = self._make_session()

		await s.send_keys("a+b")

		events = _events(client)
		assert [e["type"] for e in events] == ["keyDown", "char", "keyUp"]
		assert events[0]["key"] == "b"
		assert events[0]["modifiers"] == 0
		assert events[1]["text"] == "b"

	@pytest.mark.asyncio
	async def test_enter_sleeps_after_dispatch(self):
		s, client = self._make_session()
		with patch("tree_walker.browser.session.asyncio.sleep", new=AsyncMock()) as mock_sleep:
			await s.send_keys("Enter")

		assert mock_sleep.await_count == 1

	@pytest.mark.asyncio
	async def test_non_enter_special_key_does_not_sleep(self):
		s, client = self._make_session()
		with patch("tree_walker.browser.session.asyncio.sleep", new=AsyncMock()) as mock_sleep:
			await s.send_keys("Tab")

		assert mock_sleep.await_count == 0
