"""Tests for select_dropdown: scope bug fix, tag validation, native selection
success echo, option-not-found soft echo, error mapping, and output format.

Covers the action layer (Tools._action_select_dropdown), mirroring
tests/test_dropdown_options.py. The session layer (set_select_option, incl.
readback-verify + click fallback) is mocked — these tests assert the action
shell, not the CDP/JS internals.
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
	*,
	tag: str = "SELECT",
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
	"""Build a BrowserStateSummary with the given selector_map."""
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


def _make_browser(*, returns=None, raises=None) -> MagicMock:
	"""Stub BrowserSession for action-layer tests (does NOT touch CDP).

	set_select_option is the only session method select_dropdown calls.
	"""
	bs = MagicMock()
	if raises is not None:
		bs.set_select_option = AsyncMock(side_effect=raises)
	else:
		bs.set_select_option = AsyncMock(
			return_value=returns if returns is not None else {}
		)
	bs.get_state = AsyncMock(return_value=_make_state({}))
	return bs


# ── select_dropdown ───────────────────────────────────────────────────────────


class TestSelectDropdownAction:
	@pytest.mark.asyncio
	async def test_native_select_success_echo(self):
		entry = _make_entry(backend_node_id=7, attributes={"aria-label": "Country"})
		state = _make_state({3: entry})
		browser = _make_browser(returns={
			"success": True,
			"message": "Selected option: Canada (value: ca)",
			"value": "ca",
		})

		result = await Tools().execute(
			"select_dropdown", {"index": 3, "value": "ca"}, browser, browser_state=state,
		)

		# G1: 用目标 select 的 backend_node_id + value 调用（范围绑定，修复全页 [0] bug）
		browser.set_select_option.assert_awaited_once_with(7, "ca")
		assert result.error is None
		# G7: 成功 message 进 extracted_content
		assert "Canada" in result.extracted_content
		# G7: long_term_memory 带 json 编码 value + [SELECT] + index
		assert 'Selected "ca"' in result.long_term_memory
		assert "[SELECT]" in result.long_term_memory
		assert "'Country'" in result.long_term_memory
		assert "index 3" in result.long_term_memory

	@pytest.mark.asyncio
	async def test_non_select_element_returns_error_without_select(self):
		entry = _make_entry(tag="DIV", backend_node_id=7)
		state = _make_state({3: entry})
		browser = _make_browser()

		result = await Tools().execute(
			"select_dropdown", {"index": 3, "value": "x"}, browser, browser_state=state,
		)

		assert result.error is not None
		assert "[DIV]" in result.error
		assert "not a <select>" in result.error
		# G2: tag 校验早退，不碰 CDP
		browser.set_select_option.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_missing_index_returns_error_without_select(self):
		state = _make_state({})  # index 3 absent
		browser = _make_browser()

		result = await Tools().execute(
			"select_dropdown", {"index": 3, "value": "x"}, browser, browser_state=state,
		)

		assert result.error is not None
		browser.set_select_option.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_select_raises_maps_to_friendly_error(self):
		entry = _make_entry(backend_node_id=7)
		state = _make_state({3: entry})
		browser = _make_browser(raises=RuntimeError("CDP target detached"))

		result = await Tools().execute(
			"select_dropdown", {"index": 3, "value": "ca"}, browser, browser_state=state,
		)

		assert result.error is not None
		assert "Failed to select option" in result.error
		assert "CDP target detached" in result.error

	@pytest.mark.asyncio
	async def test_option_not_found_soft_echoes_available_options(self):
		entry = _make_entry(backend_node_id=7, attributes={"aria-label": "Country"})
		state = _make_state({3: entry})
		# session 层返回未命中 + 可用选项（也覆盖 selectionReverted 回退失败后的形态）
		browser = _make_browser(returns={
			"success": False,
			"error": "Option with text or value 'zz' not found in select element",
			"availableOptions": [
				{"text": "United States", "value": "us"},
				{"text": 'Canada "North"', "value": "ca"},
			],
		})

		result = await Tools().execute(
			"select_dropdown", {"index": 3, "value": "zz"}, browser, browser_state=state,
		)

		# G6: 不抛 error，软回显可用选项供 LLM 自纠
		assert result.error is None
		# G6: json 编码保留双引号（json.dumps('Canada "North"') == '"Canada \\"North\\""')
		assert '"Canada \\"North\\""' in result.extracted_content
		# D2: 提示语用本项目参数名 value（不是 browser-use 的 text）
		assert result.extracted_content.rstrip().endswith(
			"Use the value in select_dropdown(index=3, value=...)"
		)
		# G6: long_term_memory 摘要「选不中」
		assert "Couldn't select" in result.long_term_memory
		assert "index 3" in result.long_term_memory

	@pytest.mark.asyncio
	async def test_output_format_uses_value_param_name(self):
		entry = _make_entry(backend_node_id=7)
		state = _make_state({3: entry})
		browser = _make_browser(returns={
			"success": False,
			"availableOptions": [{"text": "A", "value": "a"}],
		})

		result = await Tools().execute(
			"select_dropdown", {"index": 3, "value": "zz"}, browser, browser_state=state,
		)

		# D2: 提示语用本项目参数名 value，避免 LLM 传 text 触发 ValidationError
		assert "select_dropdown(index=3, value=...)" in result.extracted_content


# ── set_select_option (session layer) ─────────────────────────────────────────


class TestSetSelectOption:
	"""Session-level coverage for BrowserSession.set_select_option.

	Mirrors test_click.py::TestFetchSelectOptions: mocks the CDP boundary
	(client.send.DOM.resolveNode + Runtime.callFunctionOn) and asserts the
	readback-verify / click-fallback branching, NOT real browser behavior.
	"""

	def _make_session(self, call_results) -> tuple[BrowserSession, MagicMock]:
		"""Build a BrowserSession whose CDP calls return call_results.

		call_results is either a single dict (constant return) or a list of
		dicts (sequential returns — used to exercise the click fallback, which
		makes a second callFunctionOn).
		"""
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid"
		client = MagicMock()
		client.send.DOM.resolveNode = AsyncMock(
			return_value={"object": {"objectId": "obj-1"}},
		)
		if isinstance(call_results, list):
			client.send.Runtime.callFunctionOn = AsyncMock(
				side_effect=[{"result": {"value": r}} for r in call_results]
			)
		else:
			client.send.Runtime.callFunctionOn = AsyncMock(
				return_value={"result": {"value": call_results}},
			)
		s.client = client
		return s, client

	@pytest.mark.asyncio
	async def test_success_returns_selection_scoped_to_backend_id(self):
		s, client = self._make_session({
			"success": True,
			"message": "Selected option: Canada (value: ca)",
			"value": "ca",
		})
		result = await s.set_select_option(99, "ca")
		assert result == {
			"success": True,
			"message": "Selected option: Canada (value: ca)",
			"value": "ca",
		}
		# 范围绑定：resolveNode 用目标 backendNodeId
		client.send.DOM.resolveNode.assert_awaited_once_with(
			{"backendNodeId": 99}, session_id="sid",
		)
		# 成功路径只调一次 callFunctionOn，targetText 经 arguments 传入（非字符串拼接）
		assert client.send.Runtime.callFunctionOn.await_count == 1
		sent = client.send.Runtime.callFunctionOn.await_args.args[0]
		assert sent["objectId"] == "obj-1"
		assert sent["arguments"] == [{"value": "ca"}]

	@pytest.mark.asyncio
	async def test_option_not_found_returns_available_no_fallback(self):
		s, client = self._make_session({
			"success": False,
			"error": "Option with text or value 'zz' not found in select element",
			"availableOptions": [{"text": "A", "value": "a"}],
		})
		result = await s.set_select_option(1, "zz")
		# 未命中（无 selectionReverted）→ 原样返回，不触发点击回退
		assert result["success"] is False
		assert result["availableOptions"] == [{"text": "A", "value": "a"}]
		assert client.send.Runtime.callFunctionOn.await_count == 1

	@pytest.mark.asyncio
	async def test_selection_reverted_click_fallback_succeeds(self):
		reverted = {
			"success": False,
			"selectionReverted": True,
			"targetOption": {"text": "A", "value": "a", "index": 2},
			"availableOptions": [{"text": "A", "value": "a"}],
		}
		fallback_ok = {
			"success": True,
			"message": "Selected via click fallback: A",
			"value": "a",
		}
		s, client = self._make_session([reverted, fallback_ok])
		result = await s.set_select_option(1, "a")
		# 回退成功 → 返回 success
		assert result["success"] is True
		assert "click fallback" in result["message"]
		# 两次 callFunctionOn：第二次（回退）用 targetOption.index 作为 arguments
		assert client.send.Runtime.callFunctionOn.await_count == 2
		fallback_sent = client.send.Runtime.callFunctionOn.await_args_list[1].args[0]
		assert fallback_sent["arguments"] == [{"value": 2}]

	@pytest.mark.asyncio
	async def test_selection_reverted_click_fallback_fails_returns_original(self):
		reverted = {
			"success": False,
			"selectionReverted": True,
			"targetOption": {"text": "A", "value": "a", "index": 2},
			"availableOptions": [{"text": "A", "value": "a"}],
		}
		fallback_fail = {
			"success": False,
			"error": "Click fallback also failed - framework may block all programmatic selection",
		}
		s, client = self._make_session([reverted, fallback_fail])
		result = await s.set_select_option(1, "a")
		# 回退也失败 → 返回原始结构化错误（带 availableOptions，供 action 层软回显）
		assert result["success"] is False
		assert result["selectionReverted"] is True
		assert result["availableOptions"] == [{"text": "A", "value": "a"}]
		assert client.send.Runtime.callFunctionOn.await_count == 2
