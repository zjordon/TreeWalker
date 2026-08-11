"""Tests for custom (non-native, non-ARIA-classified) dropdown support (issue #160).

Covers:
- session layer: BrowserSession.expand_and_fetch_custom_options / set_custom_dropdown_option
  / _effective_click_bid / _scroll_listbox / _find_option_object_id — open→discover→
  find-option→**real click_element**→readback ordering, finally-guaranteed collapse,
  listbox/option-not-found, virtualized scroll-until-found.
- action layer: Tools._action_dropdown_options / _action_select_dropdown custom fallback
  routing (闭态 dispatcher miss → expand_and_fetch_custom_options / set_custom_dropdown_option).

Mirrors tests/test_combobox_options.py + tests/test_select_dropdown.py::TestSetComboboxOption.
Mocks the CDP boundary (DOM.resolveNode / describeNode + Runtime.callFunctionOn) + click_element,
NOT real browser behavior. Real-framework correctness (B 站/Douyin) is local-fixture / manual e2e.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from tree_walker.browser.session import BrowserSession
from tree_walker.browser.views import (
	BrowserStateSummary,
	EnhancedDOMTreeNode,
	NodeType,
	SerializedDOMState,
)
from tree_walker.tools.actions import Tools


# ── Shared helpers ────────────────────────────────────────────────────────────


def _make_entry(
	*, tag: str = "DIV", backend_node_id: int = 42,
	attributes: dict[str, str] | None = None, node_value: str = "",
) -> EnhancedDOMTreeNode:
	return EnhancedDOMTreeNode(
		node_id=backend_node_id,
		backend_node_id=backend_node_id,
		node_type=NodeType.ELEMENT_NODE,
		node_name=tag.upper(),
		node_value=node_value,
		attributes=attributes or {},
	)


def _make_state(selector_map: dict[int, EnhancedDOMTreeNode]) -> BrowserStateSummary:
	return BrowserStateSummary(
		url="https://example.com",
		title="",
		dom_state=SerializedDOMState(
			_root=None,
			selector_map=selector_map,
			element_tree_text="",
			file_input_backend_ids=[],
		),
	)


def _make_session(
	call_results, *, resolve_id: str = "trg", describe_results=None,
	click_side=None, send_keys_raises=None, still_open=False,
) -> tuple[BrowserSession, MagicMock]:
	"""Stub BrowserSession for session-layer tests.
	call_results = 顺序 callFunctionOn 返回值/异常；describe_results = 顺序 describeNode 返回值
	（控制 _effective_click_bid / _backend_id_of_object 解析的 bid；None → 默认 99）；
	click_side = click_element side_effect（list 模拟多次 click，或异常）。"""
	s = BrowserSession.__new__(BrowserSession)
	s.current_session_id = "sid"
	s.click_element = AsyncMock(side_effect=click_side) if click_side else AsyncMock()
	s.send_keys = AsyncMock(side_effect=send_keys_raises) if send_keys_raises else AsyncMock()
	client = MagicMock()
	client.send.DOM.resolveNode = AsyncMock(return_value={"object": {"objectId": resolve_id}})
	if describe_results is not None:
		client.send.DOM.describeNode = AsyncMock(side_effect=describe_results)
	else:
		client.send.DOM.describeNode = AsyncMock(return_value={"node": {"backendNodeId": 99}})
	client.send.Runtime.callFunctionOn = AsyncMock(side_effect=call_results)
	# _custom_dropdown_still_open 走 Runtime.evaluate（与 callFunctionOn 分开 mock）
	client.send.Runtime.evaluate = AsyncMock(return_value={"result": {"value": bool(still_open)}})
	s.client = client
	return s, client


def _make_action_browser(
	*, dispatch=None, expand_custom=None, dropdown=None, custom=None,
) -> MagicMock:
	"""Stub BrowserSession for action-layer routing tests."""
	bs = MagicMock()
	bs.fetch_select_options = AsyncMock(return_value=[])
	bs.fetch_dropdown_options = AsyncMock(return_value=dispatch or {"options": [], "source": None})
	bs.expand_and_fetch_combobox_options = AsyncMock(return_value=[])
	bs.expand_and_fetch_custom_options = AsyncMock(return_value=expand_custom or [])
	bs.set_select_option = AsyncMock(return_value={})
	bs.set_combobox_option = AsyncMock(return_value={})
	bs.set_dropdown_option = AsyncMock(return_value=dropdown or {})
	bs.set_custom_dropdown_option = AsyncMock(return_value=custom or {})
	bs.get_state = AsyncMock(return_value=_make_state({}))
	return bs


# ── session layer: expand_and_fetch_custom_options ────────────────────────────


class TestExpandAndFetchCustomOptions:
	@pytest.mark.asyncio
	async def test_open_discover_read_collapse_order(self, monkeypatch):
		monkeypatch.setattr(asyncio, "sleep", AsyncMock())
		opts = [{"text": "a", "value": "a", "selected": False}]
		calls = [
			{"result": {"objectId": "wrap"}},   # _effective_click_bid (F)
			{"result": {"objectId": "lb"}},     # discover listbox (F)
			{"result": {"value": opts}},        # read options (T)
		]
		s, client = _make_session(calls, describe_results=[{"node": {"backendNodeId": 99}}])
		out = await s.expand_and_fetch_custom_options(7)
		assert out == opts
		s.click_element.assert_awaited_once_with(99)        # 点组件根 bid
		a = client.send.Runtime.callFunctionOn.await_args_list
		assert len(a) == 3
		assert a[0].args[0]["returnByValue"] is False       # effective (F)
		assert a[1].args[0]["returnByValue"] is False       # discover (F)
		assert a[2].args[0]["objectId"] == "lb"             # read on listbox
		s.send_keys.assert_awaited_once()
		assert s.send_keys.await_args.args[0].lower().startswith("esc")

	@pytest.mark.asyncio
	async def test_collapse_toggles_trigger_when_still_open(self, monkeypatch):
		# Semi 不认 Escape → _custom_dropdown_still_open=True → finally 再 click 触发器 toggle 收起
		monkeypatch.setattr(asyncio, "sleep", AsyncMock())
		opts = [{"text": "a", "value": "a", "selected": False}]
		calls = [
			{"result": {"objectId": "wrap"}},   # effective (F)
			{"result": {"objectId": "lb"}},     # discover (F)
			{"result": {"value": opts}},        # read (T)
		]
		s, _ = _make_session(calls, describe_results=[{"node": {"backendNodeId": 99}}], still_open=True)
		out = await s.expand_and_fetch_custom_options(7)
		assert out == opts
		# click_element 两次：开(99) + toggle 收起(7)
		assert s.click_element.await_count == 2
		assert s.click_element.await_args_list[0].args[0] == 99
		assert s.click_element.await_args_list[1].args[0] == 7
		s.send_keys.assert_awaited_once()  # Escape

	@pytest.mark.asyncio
	async def test_listbox_not_found_raises_and_collapses(self, monkeypatch):
		monkeypatch.setattr(asyncio, "sleep", AsyncMock())
		calls = [
			{"result": {"objectId": "wrap"}},   # effective
			{"result": {}},                     # discover → 无 objectId
		]
		# effective_bid==触发器 → 单次尝试
		s, client = _make_session(calls, describe_results=[{"node": {"backendNodeId": 7}}])
		with pytest.raises(RuntimeError, match="listbox not found"):
			await s.expand_and_fetch_custom_options(7)
		s.click_element.assert_awaited_once_with(7)
		assert client.send.Runtime.callFunctionOn.await_count == 2
		s.send_keys.assert_awaited_once()

	@pytest.mark.asyncio
	async def test_read_raises_still_collapses(self, monkeypatch):
		monkeypatch.setattr(asyncio, "sleep", AsyncMock())
		calls = [
			{"result": {"objectId": "wrap"}},
			{"result": {"objectId": "lb"}},
			RuntimeError("detached"),          # read 抛错
		]
		s, _ = _make_session(calls, describe_results=[{"node": {"backendNodeId": 99}}])
		with pytest.raises(RuntimeError, match="detached"):
			await s.expand_and_fetch_custom_options(7)
		s.click_element.assert_awaited_once_with(99)
		s.send_keys.assert_awaited_once()

	@pytest.mark.asyncio
	async def test_collapse_failure_swallowed(self, monkeypatch):
		monkeypatch.setattr(asyncio, "sleep", AsyncMock())
		opts = [{"text": "a", "value": "a", "selected": True}]
		calls = [
			{"result": {"objectId": "wrap"}},
			{"result": {"objectId": "lb"}},
			{"result": {"value": opts}},
			# _collapse_custom_dropdown 的 Escape 抛错被吞（best-effort），选项照常返回
		]
		s, _ = _make_session(calls, describe_results=[{"node": {"backendNodeId": 99}}],
		                     send_keys_raises=RuntimeError("key blocked"))
		out = await s.expand_and_fetch_custom_options(7)
		assert out == opts


# ── session layer: set_custom_dropdown_option (real click) ────────────────────


class TestSetCustomDropdownOption:
	@pytest.mark.asyncio
	async def test_open_find_realclick_success(self, monkeypatch):
		monkeypatch.setattr(asyncio, "sleep", AsyncMock())
		calls = [
			{"result": {"objectId": "wrap"}},   # effective (F)
			{"result": {"objectId": "lb"}},     # discover (F)
			{"result": {"objectId": "opt"}},    # find option (F)
		]
		s, client = _make_session(calls, describe_results=[
			{"node": {"backendNodeId": 99}}, {"node": {"backendNodeId": 88}}])
		out = await s.set_custom_dropdown_option(7, "a")
		assert out["success"] is True
		# click_element 两次：开(99) + 选(88，真实 click option)
		assert s.click_element.await_count == 2
		assert s.click_element.await_args_list[0].args[0] == 99
		assert s.click_element.await_args_list[1].args[0] == 88
		a = client.send.Runtime.callFunctionOn.await_args_list
		assert len(a) == 3
		# find 传了 value
		find_calls = [c for c in a if c.args[0].get("returnByValue") is False
		              and c.args[0].get("arguments") == [{"value": "a"}]]
		assert len(find_calls) == 1
		s.send_keys.assert_awaited_once()

	@pytest.mark.asyncio
	async def test_listbox_not_found_returns_error_still_collapses(self, monkeypatch):
		monkeypatch.setattr(asyncio, "sleep", AsyncMock())
		calls = [
			{"result": {"objectId": "wrap"}},
			{"result": {}},                     # discover miss
		]
		s, _ = _make_session(calls, describe_results=[{"node": {"backendNodeId": 7}}])
		out = await s.set_custom_dropdown_option(7, "a")
		assert out["success"] is False
		assert "listbox not found" in out["error"]
		assert out["availableOptions"] == []
		s.send_keys.assert_awaited_once()

	@pytest.mark.asyncio
	async def test_option_not_found_returns_miss_with_available(self, monkeypatch):
		monkeypatch.setattr(asyncio, "sleep", AsyncMock())
		avail = [{"text": "b", "value": "b"}]
		calls = [
			{"result": {"objectId": "wrap"}},   # effective
			{"result": {"objectId": "lb"}},     # discover
			{"result": {}},                     # find → 无 objectId（miss）
			{"result": {"value": False}},       # scroll 不可滚 → 放弃
			{"result": {"value": avail}},       # read availableOptions（回显）
		]
		s, _ = _make_session(calls, describe_results=[{"node": {"backendNodeId": 99}}])
		out = await s.set_custom_dropdown_option(7, "a")
		assert out["success"] is False
		assert "not found" in out["error"]
		assert out["availableOptions"] == avail

	@pytest.mark.asyncio
	async def test_click_raises_still_collapses(self, monkeypatch):
		monkeypatch.setattr(asyncio, "sleep", AsyncMock())
		calls = [
			{"result": {"objectId": "wrap"}},   # effective
			{"result": {"objectId": "lb"}},     # discover
			{"result": {"objectId": "opt"}},    # find
		]
		# click_element：第 1 次（开 99）成功，第 2 次（选 88）抛错
		s, _ = _make_session(calls, describe_results=[
			{"node": {"backendNodeId": 99}}, {"node": {"backendNodeId": 88}}],
			click_side=[None, RuntimeError("click boom")])
		with pytest.raises(RuntimeError, match="click boom"):
			await s.set_custom_dropdown_option(7, "a")
		s.send_keys.assert_awaited_once()

	@pytest.mark.asyncio
	async def test_virtualized_scroll_retries_until_found(self, monkeypatch):
		monkeypatch.setattr(asyncio, "sleep", AsyncMock())
		calls = [
			{"result": {"objectId": "wrap"}},   # effective
			{"result": {"objectId": "lb"}},     # discover
			{"result": {}},                     # find #1 miss
			{"result": {"value": True}},        # scroll（仍可滚）
			{"result": {"objectId": "opt"}},    # find #2 命中
		]
		s, client = _make_session(calls, describe_results=[
			{"node": {"backendNodeId": 99}}, {"node": {"backendNodeId": 88}}])
		out = await s.set_custom_dropdown_option(7, "a")
		assert out["success"] is True
		# find（returnByValue=False + arguments value=a）跑了 2 次
		find_calls = [c for c in client.send.Runtime.callFunctionOn.await_args_list
		              if c.args[0].get("returnByValue") is False
		              and c.args[0].get("arguments") == [{"value": "a"}]]
		assert len(find_calls) == 2


# ── session layer: _effective_click_bid + _scroll_listbox ──────────────────────


class TestEffectiveClickBid:
	@pytest.mark.asyncio
	async def test_resolved_to_component_root_bid(self):
		s, client = _make_session([{"result": {"objectId": "wrap"}}],
		                          describe_results=[{"node": {"backendNodeId": 99}}])
		assert await s._effective_click_bid("trg", 7) == 99
		client.send.DOM.describeNode.assert_awaited_once()

	@pytest.mark.asyncio
	async def test_resolve_failure_falls_back(self):
		s, _ = _make_session([{"result": {}}], describe_results=[{"node": {"backendNodeId": 99}}])
		assert await s._effective_click_bid("trg", 7) == 7

	@pytest.mark.asyncio
	async def test_describe_failure_falls_back(self):
		s, _ = _make_session([{"result": {"objectId": "wrap"}}], describe_results=[{"node": {}}])
		assert await s._effective_click_bid("trg", 7) == 7


class TestScrollListbox:
	@pytest.mark.asyncio
	async def test_returns_true_when_scrolled(self):
		s, _ = _make_session([{"result": {"value": True}}])
		assert await s._scroll_listbox("lb") is True

	@pytest.mark.asyncio
	async def test_returns_false_when_at_bottom(self):
		s, _ = _make_session([{"result": {"value": False}}])
		assert await s._scroll_listbox("lb") is False

	@pytest.mark.asyncio
	async def test_returns_false_on_exception(self):
		s, client = _make_session([])
		client.send.Runtime.callFunctionOn = AsyncMock(side_effect=RuntimeError("boom"))
		assert await s._scroll_listbox("lb") is False


# ── action layer: custom fallback routing ─────────────────────────────────────


class TestDropdownOptionsCustomRouting:
	@pytest.mark.asyncio
	async def test_custom_fallback_routes_to_expand_flow(self):
		entry = _make_entry(tag="DIV", backend_node_id=7, attributes={"class": "fq-trigger"})
		state = _make_state({3: entry})
		browser = _make_action_browser(
			dispatch={"options": [], "source": None},
			expand_custom=[{"text": "影视", "value": "影视", "selected": False}],
		)
		result = await Tools().execute(
			"dropdown_options", {"index": 3}, browser, browser_state=state,
		)
		assert result.error is None
		browser.fetch_dropdown_options.assert_awaited_once_with(7)
		browser.expand_and_fetch_custom_options.assert_awaited_once_with(7)
		browser.fetch_select_options.assert_not_awaited()
		assert "via [CUSTOM-OPEN]" in result.long_term_memory

	@pytest.mark.asyncio
	async def test_existing_path_wins_no_fallback(self):
		entry = _make_entry(tag="UL", backend_node_id=7, attributes={"role": "listbox"})
		state = _make_state({3: entry})
		browser = _make_action_browser(
			dispatch={"options": [{"text": "a", "value": "a", "selected": True}], "source": "aria"},
		)
		await Tools().execute("dropdown_options", {"index": 3}, browser, browser_state=state)
		browser.expand_and_fetch_custom_options.assert_not_awaited()


class TestSelectDropdownCustomRouting:
	@pytest.mark.asyncio
	async def test_custom_fallback_routes_to_set_custom(self):
		entry = _make_entry(tag="DIV", backend_node_id=7, attributes={"class": "fq-trigger"})
		state = _make_state({3: entry})
		browser = _make_action_browser(
			dropdown={"success": False, "source": None, "error": "x"},
			custom={"success": True, "message": "Selected option: 娱乐 (value: 娱乐)", "value": "娱乐"},
		)
		result = await Tools().execute(
			"select_dropdown", {"index": 3, "value": "娱乐"}, browser, browser_state=state,
		)
		assert result.error is None
		browser.set_dropdown_option.assert_awaited_once_with(7, "娱乐")
		browser.set_custom_dropdown_option.assert_awaited_once_with(7, "娱乐")
		browser.set_select_option.assert_not_awaited()
		browser.set_combobox_option.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_existing_path_wins_no_fallback(self):
		entry = _make_entry(tag="UL", backend_node_id=7, attributes={"role": "listbox"})
		state = _make_state({3: entry})
		browser = _make_action_browser(
			dropdown={"success": True, "source": "aria", "message": "ok"},
		)
		await Tools().execute(
			"select_dropdown", {"index": 3, "value": "a"}, browser, browser_state=state,
		)
		browser.set_custom_dropdown_option.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_custom_miss_soft_echoes_available_options(self):
		entry = _make_entry(tag="DIV", backend_node_id=7)
		state = _make_state({3: entry})
		browser = _make_action_browser(
			dropdown={"success": False, "source": None, "error": "x"},
			custom={"success": False, "availableOptions": [{"text": "影视", "value": "影视"}]},
		)
		result = await Tools().execute(
			"select_dropdown", {"index": 3, "value": "zzz"}, browser, browser_state=state,
		)
		assert result.error is None
		assert "select_dropdown(index=3" in result.extracted_content
		assert "Couldn't select" in result.long_term_memory

	@pytest.mark.asyncio
	async def test_custom_bare_error_maps_to_action_error(self):
		entry = _make_entry(tag="DIV", backend_node_id=7)
		state = _make_state({3: entry})
		browser = _make_action_browser(
			dropdown={"success": False, "source": None, "error": "x"},
			custom={"success": False, "error": "custom dropdown listbox not found after opening",
			        "availableOptions": []},
		)
		result = await Tools().execute(
			"select_dropdown", {"index": 3, "value": "a"}, browser, browser_state=state,
		)
		assert result.error is not None
		assert "listbox not found" in result.error
