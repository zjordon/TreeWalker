"""Tests for combobox dropdown options (P1c, experimental).

Covers:
- session layer: BrowserSession.expand_and_fetch_combobox_options — call order
  (click -> sleep -> read -> Escape), finally-guaranteed collapse even on read
  failure, listbox-not-found raising, swallowed collapse failure.
- action layer: Tools._action_dropdown_options combobox branch routing +
  error mapping.

Combobox correctness against real frameworks (React Portals, Material, Semantic)
is manual-only — these tests prove the plumbing (call ordering, finally
invariants, error mapping), NOT the JS selectors matching real DOM.
"""

from __future__ import annotations

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


# ── session layer ────────────────────────────────────────────────────────────


class TestExpandAndFetchComboboxOptions:
	"""Session-level coverage. Mocks click_element/send_keys (instance methods)
	+ the CDP boundary (DOM.resolveNode + Runtime.callFunctionOn). Asserts the
	call-ordering invariants and the finally-guaranteed collapse, NOT real
	browser behavior.
	"""

	def _make_session(self, value, *, read_raises=None) -> tuple[BrowserSession, MagicMock]:
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid"
		s.click_element = AsyncMock()
		s.send_keys = AsyncMock()
		client = MagicMock()
		client.send.DOM.resolveNode = AsyncMock(return_value={"object": {"objectId": "obj-1"}})
		if read_raises is not None:
			client.send.Runtime.callFunctionOn = AsyncMock(side_effect=read_raises)
		else:
			client.send.Runtime.callFunctionOn = AsyncMock(
				return_value={"result": {"value": value}},
			)
		s.client = client
		return s, client

	@pytest.mark.asyncio
	async def test_expand_read_collapse_order(self):
		s, client = self._make_session({
			"options": [{"value": "a", "text": "Alpha", "selected": False}],
			"listboxFound": True,
		})
		out = await s.expand_and_fetch_combobox_options(7)
		assert out == [{"value": "a", "text": "Alpha", "selected": False}]
		# 展开：click_element
		s.click_element.assert_awaited_once_with(7)
		# finally 强制收起：send_keys(Escape)
		s.send_keys.assert_awaited_once()
		assert s.send_keys.await_args.args[0].lower().startswith("esc")

	@pytest.mark.asyncio
	async def test_collapse_runs_even_when_read_raises(self):
		# D3 不变量：读抛错也要收起
		s, client = self._make_session(None, read_raises=RuntimeError("detached"))
		with pytest.raises(RuntimeError):
			await s.expand_and_fetch_combobox_options(7)
		s.click_element.assert_awaited_once_with(7)
		s.send_keys.assert_awaited_once()

	@pytest.mark.asyncio
	async def test_listbox_not_found_raises(self):
		s, client = self._make_session({
			"options": [], "listboxFound": False, "error": "no aria-controls",
		})
		with pytest.raises(RuntimeError, match="listbox not found"):
			await s.expand_and_fetch_combobox_options(7)
		# 仍收起（finally）
		s.send_keys.assert_awaited_once()

	@pytest.mark.asyncio
	async def test_collapse_failure_is_swallowed(self):
		# 读成功，但 Escape 抛错 —— finally 吞错，已读选项照常返回
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid"
		s.click_element = AsyncMock()
		s.send_keys = AsyncMock(side_effect=RuntimeError("key blocked"))
		client = MagicMock()
		client.send.DOM.resolveNode = AsyncMock(return_value={"object": {"objectId": "obj-1"}})
		client.send.Runtime.callFunctionOn = AsyncMock(return_value={"result": {"value": {
			"options": [{"value": "a", "text": "Alpha", "selected": True}],
			"listboxFound": True,
		}}})
		s.client = client
		out = await s.expand_and_fetch_combobox_options(7)
		assert out == [{"value": "a", "text": "Alpha", "selected": True}]


# ── action layer ─────────────────────────────────────────────────────────────


class TestDropdownOptionsComboboxRouting:
	def _make_browser(self, *, returns=None, raises=None) -> MagicMock:
		bs = MagicMock()
		if raises is not None:
			bs.expand_and_fetch_combobox_options = AsyncMock(side_effect=raises)
		else:
			bs.expand_and_fetch_combobox_options = AsyncMock(
				return_value=returns if returns is not None else []
			)
		bs.fetch_select_options = AsyncMock(return_value=[])
		bs.fetch_dropdown_options = AsyncMock(return_value={"options": [], "source": None})
		bs.get_state = AsyncMock(return_value=_make_state({}))
		return bs

	@pytest.mark.asyncio
	async def test_combobox_routes_to_expand_flow(self):
		entry = _make_entry(backend_node_id=7, attributes={
			"role": "combobox", "aria-controls": "list1",
		})
		state = _make_state({3: entry})
		browser = self._make_browser(returns=[
			{"value": "a", "text": "Alpha", "selected": True},
		])

		result = await Tools().execute(
			"dropdown_options", {"index": 3}, browser, browser_state=state,
		)

		# combobox 分支：调 expand_and_fetch_combobox_options（非 dispatcher / native）
		browser.expand_and_fetch_combobox_options.assert_awaited_once_with(7)
		assert result.error is None
		assert "Got 1 options" in result.long_term_memory
		assert "via [COMBOBOX]" in result.long_term_memory
		browser.fetch_select_options.assert_not_awaited()
		browser.fetch_dropdown_options.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_combobox_expand_raises_maps_to_friendly_error(self):
		entry = _make_entry(backend_node_id=7, attributes={
			"role": "combobox", "aria-controls": "list1",
		})
		state = _make_state({3: entry})
		browser = self._make_browser(raises=RuntimeError("combobox listbox not found: nope"))

		result = await Tools().execute(
			"dropdown_options", {"index": 3}, browser, browser_state=state,
		)

		assert result.error is not None
		assert "Failed to read dropdown options" in result.error
		assert "listbox not found" in result.error
