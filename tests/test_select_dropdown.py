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


def _make_browser(*, returns=None, raises=None, select=None, combo=None, dropdown=None, custom=None) -> MagicMock:
	"""Stub BrowserSession for action-layer tests (does NOT touch CDP).

	P0 native path calls set_select_option (``returns``/``raises``, or ``select`` for
	explicit clarity); P1 combobox path calls set_combobox_option (``combo``); the multi-type
	write dispatcher path calls set_dropdown_option (``dropdown``). ``returns`` aliases
	``select`` for backward compat with the P0 action-layer tests.
	"""
	bs = MagicMock()
	if raises is not None:
		bs.set_select_option = AsyncMock(side_effect=raises)
	else:
		bs.set_select_option = AsyncMock(
			return_value=(select if select is not None else (returns if returns is not None else {}))
		)
	bs.set_combobox_option = AsyncMock(return_value=combo or {})
	bs.set_dropdown_option = AsyncMock(return_value=dropdown or {})
	bs.set_custom_dropdown_option = AsyncMock(return_value=custom or {})
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
	async def test_non_select_element_routes_through_dispatcher(self):
		# P1：非 SELECT 不再 hard-reject，走写 dispatcher；真阴性（source=None）→ issue #160
		# 兜底 set_custom_dropdown_option（开态 discover+select），其 listbox-not-found bare
		# error 回显为 action error。验证非 native 路由 + 兜底串联。
		entry = _make_entry(tag="DIV", backend_node_id=7)
		state = _make_state({3: entry})
		browser = _make_browser(
			dropdown={"success": False, "source": None, "error": "not a recognized dropdown"},
			custom={"success": False, "error": "custom dropdown listbox not found after opening",
			        "availableOptions": []},
		)

		result = await Tools().execute(
			"select_dropdown", {"index": 3, "value": "x"}, browser, browser_state=state,
		)

		assert result.error is not None
		assert "listbox not found" in result.error
		# 先写 dispatcher（source=None），再兜底 custom flow；不碰 native 写链
		browser.set_dropdown_option.assert_awaited_once_with(7, "x")
		browser.set_custom_dropdown_option.assert_awaited_once_with(7, "x")
		browser.set_select_option.assert_not_awaited()
		browser.set_combobox_option.assert_not_awaited()

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


# ── P1a: set_select_option lazy-load retry (session layer) ────────────────────


class TestSetSelectOptionLazyLoadRetry:
	"""G11 懒加载重试：select 有 option 但全空（text/value 都空白）→ focus()+sleep 1.0s+
	重跑一次。重试块只在「全空」谓词为真时介入，常规 miss/success/reverted 不触发。"""
	def _make_session(self, first_value, second_value=None):
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid"
		client = MagicMock()
		client.send.DOM.resolveNode = AsyncMock(return_value={"object": {"objectId": "obj-1"}})
		# 首次 _SELECT_OPTION_JS → this.focus() → （可选）重跑 _SELECT_OPTION_JS
		values = [
			{"result": {"value": first_value}},
			{"result": {"value": {}}},
		]
		if second_value is not None:
			values.append({"result": {"value": second_value}})
		client.send.Runtime.callFunctionOn = AsyncMock(side_effect=values)
		s.client = client
		return s, client

	@pytest.mark.asyncio
	async def test_all_empty_triggers_focus_sleep_retry(self, monkeypatch):
		sleeps = []
		async def fake_sleep(t): sleeps.append(t)
		monkeypatch.setattr("tree_walker.browser.session.asyncio.sleep", fake_sleep)
		s, client = self._make_session(
			first_value={"success": False, "availableOptions": [{"text": "", "value": ""}]},
			second_value={"success": True, "message": "Selected option: X (value: x)", "value": "x"},
		)
		out = await s.set_select_option(7, "x")
		assert out["success"] is True
		assert 1.0 in sleeps                                    # 重试前 sleep 1.0s
		assert client.send.Runtime.callFunctionOn.await_count == 3  # select + focus + retry

	@pytest.mark.asyncio
	async def test_no_retry_when_options_populated(self):
		# miss 但有真实 availableOptions → 仅 1 次 callFunctionOn，不重试
		s, client = self._make_session(
			first_value={"success": False, "availableOptions": [{"text": "US", "value": "us"}]},
		)
		out = await s.set_select_option(7, "zz")
		assert client.send.Runtime.callFunctionOn.await_count == 1
		assert out["success"] is False

	@pytest.mark.asyncio
	async def test_no_retry_on_success(self):
		s, client = self._make_session(first_value={"success": True, "message": "ok", "value": "x"})
		await s.set_select_option(7, "x")
		assert client.send.Runtime.callFunctionOn.await_count == 1

	@pytest.mark.asyncio
	async def test_no_retry_when_available_empty_list(self):
		# availableOptions 为空列表（无 option 占位）→ 不满足「全空且非空列表」谓词，不重试
		s, client = self._make_session(first_value={"success": False, "availableOptions": []})
		await s.set_select_option(7, "x")
		assert client.send.Runtime.callFunctionOn.await_count == 1

	@pytest.mark.asyncio
	async def test_retry_once_only(self, monkeypatch):
		async def fake_sleep(t): pass
		monkeypatch.setattr("tree_walker.browser.session.asyncio.sleep", fake_sleep)
		# 重跑仍全空 —— 不第三次重试
		s, client = self._make_session(
			first_value={"success": False, "availableOptions": [{"text": "", "value": ""}]},
			second_value={"success": False, "availableOptions": [{"text": "", "value": ""}]},
		)
		await s.set_select_option(7, "x")
		assert client.send.Runtime.callFunctionOn.await_count == 3   # select + focus + 1 retry


# ── P1b: set_aria_option (session layer) ──────────────────────────────────────


class TestSetAriaOption:
	"""_fetch_aria_options 的写侧对应。mock CDP 边界（resolveNode + callFunctionOn），
	经 _call_setter_on_node helper 跑 _SET_ARIA_JS。"""
	def _make_session(self, value):
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid"
		client = MagicMock()
		client.send.DOM.resolveNode = AsyncMock(return_value={"object": {"objectId": "obj-1"}})
		client.send.Runtime.callFunctionOn = AsyncMock(return_value={"result": {"value": value}})
		s.client = client
		return s, client

	@pytest.mark.asyncio
	async def test_success_passes_through_scoped_to_backend_id(self):
		s, client = self._make_session({"success": True, "message": "Selected option: A (value: a)", "value": "a"})
		out = await s.set_aria_option(99, "a")
		assert out["success"] is True
		client.send.DOM.resolveNode.assert_awaited_once_with({"backendNodeId": 99}, session_id="sid")
		args = client.send.Runtime.callFunctionOn.await_args.args[0]
		assert args["objectId"] == "obj-1"
		assert args["arguments"] == [{"value": "a"}]

	@pytest.mark.asyncio
	async def test_miss_returns_available_options(self):
		s, _ = self._make_session({"success": False, "availableOptions": [{"text": "A", "value": "a"}], "error": "not found"})
		out = await s.set_aria_option(7, "zz")
		assert out["success"] is False
		assert out["availableOptions"] == [{"text": "A", "value": "a"}]


# ── P1c: set_custom_option (session layer) ────────────────────────────────────


class TestSetCustomOption:
	def _make_session(self, value):
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid"
		client = MagicMock()
		client.send.DOM.resolveNode = AsyncMock(return_value={"object": {"objectId": "obj-1"}})
		client.send.Runtime.callFunctionOn = AsyncMock(return_value={"result": {"value": value}})
		s.client = client
		return s, client

	@pytest.mark.asyncio
	async def test_success_passes_through_scoped_to_backend_id(self):
		s, client = self._make_session({"success": True, "message": "Selected option: US (value: us)", "value": "us"})
		out = await s.set_custom_option(99, "us")
		assert out["success"] is True
		client.send.DOM.resolveNode.assert_awaited_once_with({"backendNodeId": 99}, session_id="sid")
		args = client.send.Runtime.callFunctionOn.await_args.args[0]
		assert args["arguments"] == [{"value": "us"}]

	@pytest.mark.asyncio
	async def test_miss_returns_available_options(self):
		s, _ = self._make_session({"success": False, "availableOptions": [{"text": "US", "value": "us"}], "error": "not found"})
		out = await s.set_custom_option(7, "zz")
		assert out["success"] is False
		assert out["availableOptions"] == [{"text": "US", "value": "us"}]


# ── P1d: set_combobox_option (session layer, experimental; CDP-shape focus) ───


class TestSetComboboxOption:
	"""Python flow（展开→定位 listbox→写→收起）。重点：调用顺序不变量 + returnByValue=False
	的 RemoteObject shape + finally 强制收起。combobox 真实框架差异手测兜底，单测验管线。"""
	def _make_session(self, *, listbox_found=True, setter_value=None, setter_raises=None):
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid"
		s.click_element = AsyncMock()
		s.send_keys = AsyncMock()
		client = MagicMock()
		client.send.DOM.resolveNode = AsyncMock(return_value={"object": {"objectId": "combo-1"}})
		# callFunctionOn 链：1. locate listbox（returnByValue=False）→ 2. set on listbox
		# → 3. finally blur（_collapse_combobox 收起；combo_object_id 已解析故必跑）
		lb_ret = {"objectId": "listbox-1"} if listbox_found else {"value": None}
		call_results = [{"result": lb_ret}]
		if listbox_found:
			if setter_raises is not None:
				call_results.append(setter_raises)
			elif setter_value is not None:
				call_results.append({"result": {"value": setter_value}})
		call_results.append({"result": {"value": None}})   # finally blur
		client.send.Runtime.callFunctionOn = AsyncMock(side_effect=call_results)
		s.client = client
		return s, client

	@pytest.mark.asyncio
	async def test_expand_locate_set_collapse_order(self, monkeypatch):
		async def fake_sleep(t): pass
		monkeypatch.setattr("tree_walker.browser.session.asyncio.sleep", fake_sleep)
		s, client = self._make_session(setter_value={"success": True, "message": "Selected option: A (value: a)", "value": "a"})
		out = await s.set_combobox_option(7, "a")
		assert out["success"] is True
		s.click_element.assert_awaited_once_with(7)             # 1. 展开
		# callFunctionOn 链：locate listbox（returnByValue=False）→ set on listbox → blur（收起）
		assert client.send.Runtime.callFunctionOn.await_count == 3
		first = client.send.Runtime.callFunctionOn.await_args_list[0].args[0]
		assert first["returnByValue"] is False                  # locate 用 returnByValue=False
		second = client.send.Runtime.callFunctionOn.await_args_list[1].args[0]
		assert second["objectId"] == "listbox-1"               # setter 跑在 listbox 上
		assert second["arguments"] == [{"value": "a"}]
		s.send_keys.assert_awaited_once()                        # finally 收起（Escape）

	@pytest.mark.asyncio
	async def test_listbox_not_found_returns_error_still_collapses(self):
		s, _ = self._make_session(listbox_found=False)
		out = await s.set_combobox_option(7, "a")
		assert out["success"] is False
		assert "listbox not found" in out["error"]
		s.send_keys.assert_awaited_once()                        # D4 不变量：仍收起

	@pytest.mark.asyncio
	async def test_setter_raises_still_collapses(self, monkeypatch):
		async def fake_sleep(t): pass
		monkeypatch.setattr("tree_walker.browser.session.asyncio.sleep", fake_sleep)
		s, _ = self._make_session(setter_raises=RuntimeError("detached"))
		with pytest.raises(RuntimeError):
			await s.set_combobox_option(7, "a")
		s.send_keys.assert_awaited_once()                        # finally load-bearing


# ── P1e: _set_subtree_option (session layer; CDP-shape two-shape compat) ──────


class TestSetSubtreeOption:
	"""两阶段编排（D5）：_SUBTREE_LOCATE_JS（returnByValue=False）取子代 RemoteObject →
	按类型跑 setter。CDP-shape 兼容顶层 objectId 与嵌套 node.objectId 两种。"""
	def _make_session(self, locate_payload, setter_value):
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid"
		client = MagicMock()
		client.send.DOM.resolveNode = AsyncMock(return_value={"object": {"objectId": "parent-1"}})
		client.send.Runtime.callFunctionOn = AsyncMock(side_effect=[
			{"result": locate_payload},          # _SUBTREE_LOCATE_JS（returnByValue=False）
			{"result": {"value": setter_value}}, # setter on child object
		])
		s.client = client
		return s, client

	@pytest.mark.asyncio
	async def test_aria_child_top_level_object_id(self):
		# CDP-shape A：objectId 在 result 顶层
		s, client = self._make_session(
			{"found": True, "type": "aria", "objectId": "child-1", "depth": 2},
			{"success": True, "message": "Selected option: A (value: a)", "value": "a"},
		)
		out = await s._set_subtree_option(99, "a")
		assert out["success"] is True
		locate_sent = client.send.Runtime.callFunctionOn.await_args_list[0].args[0]
		assert locate_sent["returnByValue"] is False            # 需子代 RemoteObject
		assert locate_sent["arguments"] == [{"value": 4}]        # maxDepth=4

	@pytest.mark.asyncio
	async def test_custom_child_nested_node_object_id(self):
		# CDP-shape B：objectId 嵌在 node.objectId
		s, client = self._make_session(
			{"found": True, "type": "custom", "node": {"objectId": "child-2"}, "depth": 1},
			{"success": True, "message": "Selected option: US (value: us)", "value": "us"},
		)
		out = await s._set_subtree_option(99, "us")
		assert out["success"] is True
		setter_sent = client.send.Runtime.callFunctionOn.await_args_list[1].args[0]
		assert setter_sent["objectId"] == "child-2"             # 嵌套解析出的 objectId
		assert setter_sent["arguments"] == [{"value": "us"}]

	@pytest.mark.asyncio
	async def test_not_found_returns_error_without_setter(self):
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid"
		client = MagicMock()
		client.send.DOM.resolveNode = AsyncMock(return_value={"object": {"objectId": "parent-1"}})
		client.send.Runtime.callFunctionOn = AsyncMock(return_value={"result": {"found": False, "type": None, "node": None}})
		s.client = client
		out = await s._set_subtree_option(99, "a")
		assert out["success"] is False
		assert "vanished" in out["error"]
		assert client.send.Runtime.callFunctionOn.await_count == 1   # 仅 locate，不调 setter

	@pytest.mark.asyncio
	async def test_found_but_no_object_id_returns_error_without_setter(self):
		# CDP-shape 防御：found=True 但既无顶层 objectId 也无 node.objectId（序列化异常）→ error
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid"
		client = MagicMock()
		client.send.DOM.resolveNode = AsyncMock(return_value={"object": {"objectId": "parent-1"}})
		client.send.Runtime.callFunctionOn = AsyncMock(return_value={"result": {"found": True, "type": "aria", "depth": 1}})
		s.client = client
		out = await s._set_subtree_option(99, "a")
		assert out["success"] is False
		assert "could not resolve subtree child objectId" in out["error"]
		assert client.send.Runtime.callFunctionOn.await_count == 1   # 仅 locate，不调 setter


# ── P1b/c/e: set_dropdown_option dispatcher routing (session layer) ───────────


class TestSetDropdownOptionDispatcher:
	"""写侧 dispatcher 复用读侧 fetch_dropdown_options 分类（D1），按 source 路由到
	对应 setter，并补 'source' 字段。source=None → 真阴性 error。"""
	@pytest.mark.asyncio
	async def test_aria_source_routes_to_set_aria_option(self):
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid"
		s.fetch_dropdown_options = AsyncMock(return_value={"options": [{"value": "a", "text": "A"}], "source": "aria"})
		s.set_aria_option = AsyncMock(return_value={"success": True, "message": "ok", "value": "a"})
		out = await s.set_dropdown_option(7, "a")
		s.fetch_dropdown_options.assert_awaited_once_with(7)
		s.set_aria_option.assert_awaited_once_with(7, "a")
		assert out["success"] is True
		assert out["source"] == "aria"   # dispatcher 补 source（读写零漂移回显用）

	@pytest.mark.asyncio
	async def test_custom_source_routes_to_set_custom_option(self):
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid"
		s.fetch_dropdown_options = AsyncMock(return_value={"options": [], "source": "custom"})
		s.set_custom_option = AsyncMock(return_value={"success": True, "message": "ok", "value": "us"})
		out = await s.set_dropdown_option(7, "us")
		s.set_custom_option.assert_awaited_once_with(7, "us")
		assert out["source"] == "custom"

	@pytest.mark.asyncio
	async def test_subtree_source_routes_to_set_subtree_option(self):
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid"
		s.fetch_dropdown_options = AsyncMock(return_value={"options": [], "source": "child-depth-2"})
		s._set_subtree_option = AsyncMock(return_value={"success": True, "message": "ok", "value": "a"})
		out = await s.set_dropdown_option(7, "a")
		s._set_subtree_option.assert_awaited_once_with(7, "a")
		assert out["source"] == "child-depth-2"

	@pytest.mark.asyncio
	async def test_source_none_returns_not_recognized(self):
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid"
		s.fetch_dropdown_options = AsyncMock(return_value={"options": [], "source": None})
		out = await s.set_dropdown_option(7, "a")
		assert out["success"] is False
		assert out["source"] is None
		assert "not a recognized dropdown" in out["error"]


# ── P1: select_dropdown action-layer multi-type dispatch ──────────────────────


class TestSelectDropdownDispatch:
	"""action 层多类型调度：native select / combobox / 其余走写 dispatcher。三段
	（成功/未命中/裸 error）处理对所有 setter 通用（D2 统一 dict 形状）。"""
	@pytest.mark.asyncio
	async def test_aria_listbox_routes_through_set_dropdown_option(self):
		entry = _make_entry(tag="UL", backend_node_id=7, attributes={"role": "listbox"})
		state = _make_state({3: entry})
		browser = _make_browser(dropdown={
			"success": True, "message": "Selected option: A (value: a)", "value": "a", "source": "aria",
		})
		result = await Tools().execute("select_dropdown", {"index": 3, "value": "a"}, browser, browser_state=state)
		browser.set_dropdown_option.assert_awaited_once_with(7, "a")
		assert result.error is None
		assert 'Selected "a"' in result.long_term_memory
		browser.set_select_option.assert_not_awaited()
		browser.set_combobox_option.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_custom_dropdown_routes_through_set_dropdown_option(self):
		entry = _make_entry(tag="DIV", backend_node_id=7, attributes={"class": "ui dropdown"})
		state = _make_state({3: entry})
		browser = _make_browser(dropdown={
			"success": True, "message": "Selected option: US (value: us)", "value": "us", "source": "custom",
		})
		result = await Tools().execute("select_dropdown", {"index": 3, "value": "us"}, browser, browser_state=state)
		browser.set_dropdown_option.assert_awaited_once_with(7, "us")
		assert result.error is None
		assert 'Selected "us"' in result.long_term_memory

	@pytest.mark.asyncio
	async def test_combobox_routes_to_set_combobox_option(self):
		entry = _make_entry(tag="INPUT", backend_node_id=7, attributes={"role": "combobox", "aria-controls": "lb"})
		state = _make_state({3: entry})
		browser = _make_browser(combo={"success": True, "message": "Selected option: A (value: a)", "value": "a"})
		result = await Tools().execute("select_dropdown", {"index": 3, "value": "a"}, browser, browser_state=state)
		browser.set_combobox_option.assert_awaited_once_with(7, "a")
		assert result.error is None
		assert 'Selected "a"' in result.long_term_memory
		browser.set_select_option.assert_not_awaited()
		browser.set_dropdown_option.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_combobox_miss_soft_echoes_available_options(self):
		entry = _make_entry(tag="INPUT", backend_node_id=7, attributes={"role": "combobox", "aria-controls": "lb"})
		state = _make_state({3: entry})
		browser = _make_browser(combo={
			"success": False, "error": "not found",
			"availableOptions": [{"text": "A", "value": "a"}],
		})
		result = await Tools().execute("select_dropdown", {"index": 3, "value": "zz"}, browser, browser_state=state)
		assert result.error is None
		assert "0: text=" in result.extracted_content
		assert "Couldn't select" in result.long_term_memory

	@pytest.mark.asyncio
	async def test_combobox_bare_error_maps_to_action_error(self):
		entry = _make_entry(tag="INPUT", backend_node_id=7, attributes={"role": "combobox", "aria-controls": "lb"})
		state = _make_state({3: entry})
		# listbox not found：availableOptions 为空 → 裸 error 分支（不软回显）
		browser = _make_browser(combo={
			"success": False,
			"error": "combobox listbox not found (no aria-controls/aria-owns target)",
			"availableOptions": [],
		})
		result = await Tools().execute("select_dropdown", {"index": 3, "value": "a"}, browser, browser_state=state)
		assert result.error is not None
		assert "listbox not found" in result.error
		assert result.extracted_content is None   # 无 availableOptions 泄漏

	@pytest.mark.asyncio
	async def test_native_select_still_uses_set_select_option_no_regression(self):
		entry = _make_entry(tag="SELECT", backend_node_id=7, attributes={"aria-label": "Country"})
		state = _make_state({3: entry})
		browser = _make_browser(select={"success": True, "message": "ok", "value": "ca"})
		await Tools().execute("select_dropdown", {"index": 3, "value": "ca"}, browser, browser_state=state)
		browser.set_select_option.assert_awaited_once_with(7, "ca")
		browser.set_dropdown_option.assert_not_awaited()
		browser.set_combobox_option.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_dispatcher_raises_maps_to_friendly_error(self):
		entry = _make_entry(tag="UL", backend_node_id=7, attributes={"role": "listbox"})
		state = _make_state({3: entry})
		browser = _make_browser()
		browser.set_dropdown_option = AsyncMock(side_effect=RuntimeError("CDP detached"))
		result = await Tools().execute("select_dropdown", {"index": 3, "value": "a"}, browser, browser_state=state)
		assert result.error is not None
		assert "Failed to select option" in result.error
		assert "CDP detached" in result.error
