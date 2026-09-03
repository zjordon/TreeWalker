"""Tests for character-by-character typing and framework event triggering."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call

import pytest

from tree_walker.browser.session import (
	_get_char_modifiers_and_vk,
	_get_key_code_for_char,
	_requires_direct_value_assignment,
)
from tree_walker.browser.views import EnhancedDOMTreeNode, NodeType


# ── Unit tests for _get_char_modifiers_and_vk ──


class TestGetCharModifiersAndVk:
	def test_lowercase(self):
		mod, vk, base = _get_char_modifiers_and_vk("a")
		assert mod == 0
		assert vk == ord("A")
		assert base == "a"

	def test_uppercase(self):
		mod, vk, base = _get_char_modifiers_and_vk("A")
		assert mod == 8  # Shift
		assert vk == ord("A")
		assert base == "a"

	def test_digit(self):
		mod, vk, base = _get_char_modifiers_and_vk("5")
		assert mod == 0
		assert vk == ord("5")
		assert base == "5"

	def test_space(self):
		mod, vk, base = _get_char_modifiers_and_vk(" ")
		assert mod == 0
		assert vk == 32
		assert base == " "

	def test_shift_symbols(self):
		# @ requires Shift + 2
		mod, vk, base = _get_char_modifiers_and_vk("@")
		assert mod == 8  # Shift
		assert vk == 50  # VK for '2'
		assert base == "2"

	def test_exclamation(self):
		mod, vk, base = _get_char_modifiers_and_vk("!")
		assert mod == 8
		assert vk == 49
		assert base == "1"

	def test_no_shift_symbols(self):
		mod, vk, base = _get_char_modifiers_and_vk("-")
		assert mod == 0
		assert base == "-"

	def test_chinese_fallback(self):
		# CJK character should use fallback
		mod, vk, base = _get_char_modifiers_and_vk("测")
		assert mod == 0
		assert base == "测"

	def test_newline_not_handled_here(self):
		# \n is not a printable char, but fallback should work
		mod, vk, base = _get_char_modifiers_and_vk("\n")
		assert isinstance(mod, int)


# ── Unit tests for _get_key_code_for_char ──


class TestGetKeyCodeForChar:
	def test_lowercase(self):
		assert _get_key_code_for_char("a") == "KeyA"

	def test_uppercase(self):
		assert _get_key_code_for_char("A") == "KeyA"

	def test_digit(self):
		assert _get_key_code_for_char("3") == "Digit3"

	def test_space(self):
		assert _get_key_code_for_char(" ") == "Space"

	def test_at_symbol(self):
		assert _get_key_code_for_char("@") == "Digit2"

	def test_period(self):
		assert _get_key_code_for_char(".") == "Period"

	def test_cjk_fallback(self):
		result = _get_key_code_for_char("中")
		assert isinstance(result, str)


# ── Integration tests for type_text character-by-character dispatch ──


class TestTypeTextCharByChar:
	@pytest.fixture()
	def browser(self):
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
		bs.client.send.Input.insertText = AsyncMock()
		bs.client.send.Runtime = MagicMock()
		bs.client.send.Runtime.evaluate = AsyncMock(
			return_value={"result": {"value": True}},
		)
		return bs

	@pytest.mark.asyncio
	async def test_type_text_sends_char_events(self, browser):
		"""Each ASCII character should produce keyDown + char + keyUp."""
		await browser.type_text("Hi", clear=False)

		dispatch = browser.client.send.Input.dispatchKeyEvent
		# 'H' (uppercase): keyDown, char, keyUp
		# 'i' (lowercase): keyDown, char, keyUp
		assert dispatch.call_count == 6  # 2 chars × 3 events

		# First call should be keyDown for 'h' (base of 'H')
		first_call = dispatch.call_args_list[0]
		assert first_call[0][0]["type"] == "keyDown"
		assert first_call[0][0]["key"] == "h"
		assert first_call[0][0]["modifiers"] == 8  # Shift

		# Second call should be char with original char as key
		second_call = dispatch.call_args_list[1]
		assert second_call[0][0]["type"] == "char"
		assert second_call[0][0]["text"] == "H"
		assert second_call[0][0]["key"] == "H"

	@pytest.mark.asyncio
	async def test_type_text_cjk_only_char_event(self, browser):
		"""CJK characters should only produce a char event (no keyDown/keyUp)."""
		await browser.type_text("测", clear=False)

		dispatch = browser.client.send.Input.dispatchKeyEvent
		assert dispatch.call_count == 1  # only char event

		call_args = dispatch.call_args_list[0]
		assert call_args[0][0]["type"] == "char"
		assert call_args[0][0]["text"] == "测"

	@pytest.mark.asyncio
	async def test_type_text_clear_first(self, browser):
		"""With clear=True, _clear_text_field is called and strategy 1 (JS) handles clearing;
		only character-typing key events should reach dispatchKeyEvent."""
		# Strategy 1 of _clear_text_field reports success
		browser.client.send.Runtime.evaluate = AsyncMock(
			side_effect=[
				{"result": {"value": {"cleared": True, "method": "value", "final": ""}}},  # _clear_text_field strategy 1
				{"result": {"value": True}},  # _trigger_framework_events
				{"result": {"value": "X"}},  # _read_active_text (concatenation guard, matches)
			],
		)

		await browser.type_text("X", clear=True)

		dispatch = browser.client.send.Input.dispatchKeyEvent
		# Strategy 1 (JS) handled clearing — no Ctrl+A/Backspace key events.
		# Only the 3 events for typing 'X' (keyDown + char + keyUp).
		assert dispatch.call_count == 3

		# All key events should be for 'x' (base of 'X'), NOT Ctrl+A
		for call in dispatch.call_args_list:
			assert call[0][0]["key"] != "a" or call[0][0].get("modifiers") != 2

	@pytest.mark.asyncio
	async def test_type_text_no_longer_uses_insert_text(self, browser):
		"""type_text should NOT use Input.insertText anymore."""
		await browser.type_text("test", clear=False)
		browser.client.send.Input.insertText.assert_not_called()

	@pytest.mark.asyncio
	async def test_type_text_triggers_framework_events(self, browser):
		"""After typing, _trigger_framework_events should be called."""
		await browser.type_text("abc", clear=False)

		# Runtime.evaluate should be called by _trigger_framework_events
		browser.client.send.Runtime.evaluate.assert_called_once()
		call_args = browser.client.send.Runtime.evaluate.call_args
		expr = call_args[0][0]["expression"]
		assert "InputEvent" in expr
		assert "__vue__" in expr
		# P7 form_interaction 建议2：'change' 必须派发——KO value 绑定默认听 change，
		# 漏发会让 KO 表单（Magento admin）Save 提交空值（695/700/702/542 实证）。
		assert "Event('change'" in expr
		# 'blur' 仍不派发（下拉收起、tag-input 清值等副作用）。
		assert "Event('blur'" not in expr

	@pytest.mark.asyncio
	async def test_type_text_framework_events_non_critical(self, browser):
		"""Framework event failure should not raise."""
		browser.client.send.Runtime.evaluate = AsyncMock(
			side_effect=RuntimeError("JS error"),
		)
		# Should not raise
		await browser.type_text("x", clear=False)


# ── Tests for _type_char ──


class TestTypeChar:
	@pytest.fixture()
	def browser(self):
		from tree_walker.browser.session import BrowserSession
		from tree_walker.config import BrowserSettings

		bs = BrowserSession.__new__(BrowserSession)
		bs.client = MagicMock()
		bs.current_session_id = "sid1"
		bs.client.send = MagicMock()
		bs.client.send.Input = MagicMock()
		bs.client.send.Input.dispatchKeyEvent = AsyncMock()
		return bs

	@pytest.mark.asyncio
	async def test_type_char_ascii_sends_three_events(self, browser):
		await browser._type_char("a")
		assert browser.client.send.Input.dispatchKeyEvent.call_count == 3

	@pytest.mark.asyncio
	async def test_type_char_cjk_sends_one_event(self, browser):
		await browser._type_char("中")
		assert browser.client.send.Input.dispatchKeyEvent.call_count == 1
		call_args = browser.client.send.Input.dispatchKeyEvent.call_args
		assert call_args[0][0]["type"] == "char"
		assert call_args[0][0]["text"] == "中"

	@pytest.mark.asyncio
	async def test_type_char_events_order(self, browser):
		await browser._type_char("x")
		calls = browser.client.send.Input.dispatchKeyEvent.call_args_list
		types = [c[0][0]["type"] for c in calls]
		assert types == ["keyDown", "char", "keyUp"]

	@pytest.mark.asyncio
	async def test_type_char_with_explicit_sid(self, browser):
		await browser._type_char("z", sid="custom-sid")
		calls = browser.client.send.Input.dispatchKeyEvent.call_args_list
		for c in calls:
			assert c[1]["session_id"] == "custom-sid"


# ── Tests for _requires_direct_value_assignment ──


def _dv_entry(tag: str = "input", **attrs) -> EnhancedDOMTreeNode:
	"""Build an EnhancedDOMTreeNode with a tag and attributes for the predicate."""
	return EnhancedDOMTreeNode(
		node_id=1,
		backend_node_id=1,
		node_type=NodeType.ELEMENT_NODE,
		node_name=tag.upper(),
		node_value="",
		attributes=dict(attrs),
	)


class TestRequiresDirectValueAssignment:
	def test_each_special_type_returns_true(self):
		for t in ("date", "time", "datetime-local", "month", "week", "color", "range"):
			assert _requires_direct_value_assignment(_dv_entry(type=t)) is True

	def test_datepicker_class_markers_return_true(self):
		for cls in ("my-datepicker", "foo daterangepicker bar", "x-datetimepicker", "bootstrap-datepicker"):
			assert _requires_direct_value_assignment(_dv_entry(type="text", **{"class": cls})) is True

	def test_datepicker_class_on_no_type_returns_true(self):
		assert _requires_direct_value_assignment(_dv_entry(**{"class": "datepicker"})) is True

	def test_datepicker_data_attrs_return_true(self):
		for attr in ("data-datepicker", "data-date-format", "data-provide"):
			assert _requires_direct_value_assignment(_dv_entry(type="text", **{attr: "1"})) is True

	@pytest.mark.parametrize("t", ["text", "email", "password", "search", "url", "number", "checkbox", ""])
	def test_plain_types_return_false(self, t):
		assert _requires_direct_value_assignment(_dv_entry(type=t)) is False

	def test_textarea_returns_false(self):
		assert _requires_direct_value_assignment(_dv_entry(tag="textarea", type="text")) is False

	def test_datepicker_class_on_non_input_returns_false(self):
		# tag guard: only <input> qualifies
		assert _requires_direct_value_assignment(_dv_entry(tag="div", **{"class": "datepicker"})) is False

	def test_none_entry_is_safe(self):
		# getattr defaults make the predicate robust to malformed entries
		assert _requires_direct_value_assignment(None) is False
