"""Tests for dropdown_options: scope bug fix, tag validation, success echo,
json-encoded output, and error mapping.

Covers the action layer (Tools._action_dropdown_options), mirroring
tests/test_upload_file.py:
- native select: echoes 'Got N options from [SELECT] ...' in long_term_memory,
  extracted_content lists each option json-encoded with a select_dropdown hint
- non-select element: friendly error (no full-page select leak)
- index absent from selector_map: returns error without touching CDP
- fetch_select_options raising -> friendly 'Failed to read select options:'
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


def _make_browser(*, options=None, raises=None) -> MagicMock:
	"""Stub BrowserSession for action-layer tests (does NOT touch CDP).

	fetch_select_options is the only session method dropdown_options calls.
	"""
	bs = MagicMock()
	if raises is not None:
		bs.fetch_select_options = AsyncMock(side_effect=raises)
	else:
		bs.fetch_select_options = AsyncMock(
			return_value=options if options is not None else []
		)
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
		assert "not a <select>" in result.error
		# G2: tag 校验早退，不碰 CDP
		browser.fetch_select_options.assert_not_awaited()

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
		assert "Failed to read select options" in result.error
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
