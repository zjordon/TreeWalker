"""Tests for dropdown_options: scope bug fix, tag validation, success echo,
json-encoded output, and error mapping.

Covers the action layer (Tools._action_dropdown_options), mirroring
tests/test_upload_file.py:
- native select: echoes 'Got N options from [SELECT] ...' in long_term_memory,
  extracted_content lists each option json-encoded with a select_dropdown hint
- non-select element: friendly error (no full-page select leak)
- index absent from selector_map: returns error without touching CDP
- fetch_select_options raising -> friendly 'Failed to read dropdown options:'
- empty options: soft echo (just the hint line, no exception)
- output format: text/value json-encoded (quotes preserved) + hint uses 'value'
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tree_walker.browser.views import (
	BrowserStateSummary,
	EnhancedDOMTreeNode,
	NodeType,
	SerializedDOMState,
)
from tree_walker.browser.session import BrowserSession
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


def _make_browser(*, options=None, raises=None, dispatch=None) -> MagicMock:
	"""Stub BrowserSession for action-layer tests (does NOT touch CDP).

	native <select> path calls fetch_select_options (options/raises); the
	non-native dispatcher path calls fetch_dropdown_options (dispatch, default
	true-negative {"options":[], "source": None}); the combobox path calls
	expand_and_fetch_combobox_options.
	"""
	bs = MagicMock()
	if raises is not None:
		bs.fetch_select_options = AsyncMock(side_effect=raises)
	else:
		bs.fetch_select_options = AsyncMock(
			return_value=options if options is not None else []
		)
	bs.fetch_dropdown_options = AsyncMock(
		return_value=dispatch if dispatch is not None else {"options": [], "source": None}
	)
	bs.expand_and_fetch_combobox_options = AsyncMock(return_value=[])
	bs.get_state = AsyncMock(return_value=_make_state({}))
	return bs


# ── dropdown_options ──────────────────────────────────────────────────────────


class TestDropdownOptionsAction:
	@pytest.mark.asyncio
	async def test_native_select_echoes_options_and_memory(self):
		entry = _make_entry(backend_node_id=7, attributes={"aria-label": "Country"})
		state = _make_state({3: entry})
		browser = _make_browser(options=[
			{"value": "us", "text": "United States", "selected": True},
			{"value": "ca", "text": 'Canada "North"', "selected": False},
		])

		result = await Tools().execute(
			"dropdown_options", {"index": 3}, browser, browser_state=state,
		)

		# G1: 用目标 select 的 backend_node_id 调用（范围绑定，修复全页扫描 bug）
		browser.fetch_select_options.assert_awaited_once_with(7)
		assert result.error is None
		# G3: long_term_memory 带选项数 + label
		assert "Got 2 options" in result.long_term_memory
		assert "[SELECT]" in result.long_term_memory
		assert "'Country'" in result.long_term_memory
		assert "index 3" in result.long_term_memory
		# G4: json 编码保留双引号（json.dumps('Canada "North"') == '"Canada \\"North\\""')
		assert '"Canada \\"North\\""' in result.extracted_content
		# 选中标记
		assert " (selected)" in result.extracted_content
		# D2: 提示语用本项目参数名 value（不是 browser-use 的 text）
		assert "select_dropdown(index=3, value=...)" in result.extracted_content

	@pytest.mark.asyncio
	async def test_non_select_element_returns_error_without_fetch(self):
		entry = _make_entry(tag="DIV", backend_node_id=7)
		state = _make_state({3: entry})
		browser = _make_browser()

		result = await Tools().execute(
			"dropdown_options", {"index": 3}, browser, browser_state=state,
		)

		assert result.error is not None
		assert "[DIV]" in result.error
		assert "not a recognized dropdown" in result.error
		# 非 select 委托 session dispatcher（真阴性），不碰 native fetch
		browser.fetch_select_options.assert_not_awaited()
		browser.fetch_dropdown_options.assert_awaited_once_with(7)

	@pytest.mark.asyncio
	async def test_missing_index_returns_error_without_fetch(self):
		state = _make_state({})  # index 3 absent
		browser = _make_browser()

		result = await Tools().execute(
			"dropdown_options", {"index": 3}, browser, browser_state=state,
		)

		assert result.error is not None
		browser.fetch_select_options.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_fetch_raises_maps_to_friendly_error(self):
		entry = _make_entry(backend_node_id=7)
		state = _make_state({3: entry})
		browser = _make_browser(raises=RuntimeError("CDP target detached"))

		result = await Tools().execute(
			"dropdown_options", {"index": 3}, browser, browser_state=state,
		)

		assert result.error is not None
		assert "Failed to read dropdown options" in result.error
		assert "CDP target detached" in result.error

	@pytest.mark.asyncio
	async def test_empty_options_soft_echo_just_hint(self):
		entry = _make_entry(backend_node_id=7)
		state = _make_state({3: entry})
		browser = _make_browser(options=[])

		result = await Tools().execute(
			"dropdown_options", {"index": 3}, browser, browser_state=state,
		)

		assert result.error is None
		assert "Got 0 options" in result.long_term_memory
		# 空选项：extracted 仅剩提示行
		assert "select_dropdown(index=3, value=...)" in result.extracted_content

	@pytest.mark.asyncio
	async def test_output_format_json_encoded_with_hint(self):
		entry = _make_entry(backend_node_id=7)
		state = _make_state({3: entry})
		# 含引号 / 换行的文本，验证 json 编码可精确复制
		browser = _make_browser(options=[
			{"value": 'a"b', "text": "x\ny", "selected": False},
		])

		result = await Tools().execute(
			"dropdown_options", {"index": 3}, browser, browser_state=state,
		)

		assert "0: text=" in result.extracted_content
		assert "value=" in result.extracted_content
		assert result.extracted_content.rstrip().endswith(
			"Use the value in select_dropdown(index=3, value=...)"
		)


# ── P1: dispatcher routing (ARIA / custom / combobox / subtree) ───────────────


class TestDropdownOptionsDispatcher:
	@pytest.mark.asyncio
	async def test_aria_listbox_routes_through_session_dispatcher(self):
		entry = _make_entry(tag="UL", backend_node_id=7, attributes={"role": "listbox"})
		state = _make_state({3: entry})
		browser = _make_browser(dispatch={"options": [
			{"value": "a", "text": "Alpha", "selected": True},
		], "source": "aria"})

		result = await Tools().execute(
			"dropdown_options", {"index": 3}, browser, browser_state=state,
		)

		# 范围绑定：dispatcher 用目标 backend_node_id
		browser.fetch_dropdown_options.assert_awaited_once_with(7)
		assert result.error is None
		# source 折进 long_term_memory（诊断通道）
		assert "Got 1 options" in result.long_term_memory
		assert "via [ARIA]" in result.long_term_memory
		# native fetch 未被调用（非 SELECT）
		browser.fetch_select_options.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_native_select_no_via_suffix_no_regression(self):
		entry = _make_entry(tag="SELECT", backend_node_id=7, attributes={"aria-label": "Country"})
		state = _make_state({3: entry})
		browser = _make_browser(options=[{"value": "us", "text": "US", "selected": True}])

		result = await Tools().execute(
			"dropdown_options", {"index": 3}, browser, browser_state=state,
		)

		# native 路径零回归：仍调 fetch_select_options，source=native 无 "via" 后缀
		browser.fetch_select_options.assert_awaited_once_with(7)
		assert "Got 1 options" in result.long_term_memory
		assert "via" not in result.long_term_memory
		browser.fetch_dropdown_options.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_empty_aria_options_emits_diagnostic_and_hint(self):
		entry = _make_entry(tag="UL", backend_node_id=7, attributes={"role": "listbox"})
		state = _make_state({3: entry})
		browser = _make_browser(dispatch={"options": [], "source": "aria"})

		result = await Tools().execute(
			"dropdown_options", {"index": 3}, browser, browser_state=state,
		)

		assert result.error is None
		assert "Got 0 options" in result.long_term_memory
		assert "via [ARIA]" in result.long_term_memory
		# G5 进阶：空选项诊断
		assert "[role=option]" in result.extracted_content
		assert "select_dropdown(index=3, value=...)" in result.extracted_content

	@pytest.mark.asyncio
	async def test_dispatcher_raises_maps_to_friendly_error(self):
		entry = _make_entry(tag="UL", backend_node_id=7, attributes={"role": "listbox"})
		state = _make_state({3: entry})
		browser = _make_browser()
		browser.fetch_dropdown_options = AsyncMock(side_effect=RuntimeError("CDP detached"))

		result = await Tools().execute(
			"dropdown_options", {"index": 3}, browser, browser_state=state,
		)

		assert result.error is not None
		assert "Failed to read dropdown options" in result.error
		assert "CDP detached" in result.error

	@pytest.mark.asyncio
	async def test_custom_dropdown_routes_with_custom_source(self):
		entry = _make_entry(tag="DIV", backend_node_id=7, attributes={"class": "ui dropdown"})
		state = _make_state({3: entry})
		browser = _make_browser(dispatch={"options": [
			{"value": "us", "text": "United States", "selected": True},
		], "source": "custom"})

		result = await Tools().execute(
			"dropdown_options", {"index": 3}, browser, browser_state=state,
		)

		browser.fetch_dropdown_options.assert_awaited_once_with(7)
		assert result.error is None
		assert "Got 1 options" in result.long_term_memory
		assert "via [CUSTOM]" in result.long_term_memory
		browser.fetch_select_options.assert_not_awaited()


# ── P1a: session layer (_fetch_aria_options + fetch_dropdown_options) ─────────


class TestFetchAriaOptions:
	"""Session-level coverage for BrowserSession._fetch_aria_options and the
	fetch_dropdown_options dispatcher. Mirrors test_select_dropdown.py:
	TestSetSelectOption — mocks the CDP boundary (client.send.DOM.resolveNode +
	Runtime.callFunctionOn), NOT real browser behavior.
	"""

	def _make_session(self, value) -> tuple[BrowserSession, MagicMock]:
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid"
		client = MagicMock()
		client.send.DOM.resolveNode = AsyncMock(return_value={"object": {"objectId": "obj-1"}})
		client.send.Runtime.callFunctionOn = AsyncMock(
			return_value={"result": {"value": value}},
		)
		s.client = client
		return s, client

	@pytest.mark.asyncio
	async def test_aria_returns_options_scoped_to_backend_id(self):
		s, client = self._make_session([{"value": "a", "text": "Alpha", "selected": True}])
		out = await s._fetch_aria_options(99)
		assert out == [{"value": "a", "text": "Alpha", "selected": True}]
		# 范围绑定：resolveNode 用目标 backendNodeId
		client.send.DOM.resolveNode.assert_awaited_once_with(
			{"backendNodeId": 99}, session_id="sid",
		)
		assert client.send.Runtime.callFunctionOn.await_count == 1
		sent = client.send.Runtime.callFunctionOn.await_args.args[0]
		assert sent["objectId"] == "obj-1"
		assert sent["functionDeclaration"] is not None

	@pytest.mark.asyncio
	async def test_not_aria_returns_none(self):
		s, _ = self._make_session(None)
		assert await s._fetch_aria_options(7) is None

	@pytest.mark.asyncio
	async def test_dispatcher_returns_aria_source(self):
		s, _ = self._make_session([{"value": "a", "text": "Alpha", "selected": False}])
		out = await s.fetch_dropdown_options(99)
		assert out == {"options": [{"value": "a", "text": "Alpha", "selected": False}], "source": "aria"}

	@pytest.mark.asyncio
	async def test_dispatcher_returns_none_source_when_not_aria(self):
		s, _ = self._make_session(None)
		out = await s.fetch_dropdown_options(99)
		assert out == {"options": [], "source": None}


# ── P1b: session layer (_fetch_custom_class_options + dispatcher fallback) ────


class TestFetchCustomClassOptions:
	def _make_session(self, value) -> tuple[BrowserSession, MagicMock]:
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid"
		client = MagicMock()
		client.send.DOM.resolveNode = AsyncMock(return_value={"object": {"objectId": "obj-1"}})
		client.send.Runtime.callFunctionOn = AsyncMock(
			return_value={"result": {"value": value}},
		)
		s.client = client
		return s, client

	@pytest.mark.asyncio
	async def test_custom_returns_options_scoped_to_backend_id(self):
		s, client = self._make_session([{"value": "us", "text": "United States", "selected": True}])
		out = await s._fetch_custom_class_options(99)
		assert out == [{"value": "us", "text": "United States", "selected": True}]
		client.send.DOM.resolveNode.assert_awaited_once_with(
			{"backendNodeId": 99}, session_id="sid",
		)

	@pytest.mark.asyncio
	async def test_not_custom_returns_none(self):
		s, _ = self._make_session(None)
		assert await s._fetch_custom_class_options(7) is None

	@pytest.mark.asyncio
	async def test_dispatcher_falls_back_to_custom_when_aria_misses(self):
		# aria 返回 None（callFunctionOn 第 1 次），custom 命中（第 2 次）。
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid"
		client = MagicMock()
		client.send.DOM.resolveNode = AsyncMock(return_value={"object": {"objectId": "obj-1"}})
		client.send.Runtime.callFunctionOn = AsyncMock(side_effect=[
			{"result": {"value": None}},  # aria miss
			{"result": {"value": [{"value": "a", "text": "Alpha", "selected": False}]}},  # custom hit
		])
		s.client = client
		out = await s.fetch_dropdown_options(99)
		assert out == {"options": [{"value": "a", "text": "Alpha", "selected": False}], "source": "custom"}
		assert client.send.Runtime.callFunctionOn.await_count == 2


# ── P1d: session layer (search_children_for_dropdowns + dispatcher fallback) ──


class TestSearchChildrenForDropdownOptions:
	def _make_session(self, value) -> tuple[BrowserSession, MagicMock]:
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid"
		client = MagicMock()
		client.send.DOM.resolveNode = AsyncMock(return_value={"object": {"objectId": "obj-1"}})
		client.send.Runtime.callFunctionOn = AsyncMock(
			return_value={"result": {"value": value}},
		)
		s.client = client
		return s, client

	@pytest.mark.asyncio
	async def test_subtree_hit_returns_options_and_depth_source(self):
		s, client = self._make_session({
			"options": [{"value": "a", "text": "Alpha", "selected": False}],
			"source": "child-depth-2",
		})
		out = await s.search_children_for_dropdowns(99)
		assert out == {"options": [{"value": "a", "text": "Alpha", "selected": False}], "source": "child-depth-2"}
		# max_depth 经 arguments 传入（默认 4）
		sent = client.send.Runtime.callFunctionOn.await_args.args[0]
		assert sent["arguments"] == [{"value": 4}]

	@pytest.mark.asyncio
	async def test_subtree_miss_returns_none_source(self):
		s, _ = self._make_session({"options": [], "source": None})
		out = await s.search_children_for_dropdowns(99)
		assert out == {"options": [], "source": None}

	@pytest.mark.asyncio
	async def test_subtree_value_none_defaults_to_empty(self):
		# JS 返回 null/undefined -> 默认 {"options":[], "source": None}
		s, _ = self._make_session(None)
		out = await s.search_children_for_dropdowns(99)
		assert out == {"options": [], "source": None}

	@pytest.mark.asyncio
	async def test_dispatcher_falls_back_to_subtree_when_aria_custom_miss(self):
		# aria None（call 1）-> custom None（call 2）-> subtree 命中（call 3）
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid"
		client = MagicMock()
		client.send.DOM.resolveNode = AsyncMock(return_value={"object": {"objectId": "obj-1"}})
		client.send.Runtime.callFunctionOn = AsyncMock(side_effect=[
			{"result": {"value": None}},  # aria miss
			{"result": {"value": None}},  # custom miss
			{"result": {"value": {"options": [{"value": "a", "text": "A", "selected": False}], "source": "child-depth-1"}}},
		])
		s.client = client
		out = await s.fetch_dropdown_options(99)
		assert out == {"options": [{"value": "a", "text": "A", "selected": False}], "source": "child-depth-1"}
		assert client.send.Runtime.callFunctionOn.await_count == 3
