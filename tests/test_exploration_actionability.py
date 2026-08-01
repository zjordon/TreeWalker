"""P0 探索 actionability 单测（共享模块 wait_for_actionability + step.py 插入门控）。

详见 docs/p3/01-探索可靠性提升方案.md。覆盖：
- 纯函数 is_actionable / is_file_input / 白名单（迁自 rerun，行为零变）；
- wait_for_actionability（探索 index-based：秒返 / 等到 / 超时降级 / index 缺失降级）；
- step.py _execute_actions 插入门控（启用+白名单才调 wait；关闭/非白名单/index 缺/file input 跳过）。
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from tree_walker.agent.actionability import (
	ACTIONABILITY_ACTIONS,
	is_actionable,
	is_file_input,
	wait_for_actionability,
)
from tree_walker.agent.step import StepPipeline
from tree_walker.agent.views import ActionResult
from tests.test_multi_act import _make_agent


class _Node:
	"""最小 EnhancedDOMTreeNode 替身（只填 is_actionable / is_file_input 读的字段）。"""

	def __init__(self, *, is_visible=True, ignored_by_paint_order=False,
			pointer_events=None, disabled=False, aria_disabled=False,
			tag="BUTTON", ftype=None, backend_node_id=1, x=10, y=20):
		self.is_visible = is_visible
		self.ignored_by_paint_order = ignored_by_paint_order
		self.node_name = tag
		self.attributes = {}
		if disabled:
			self.attributes["disabled"] = ""
		if aria_disabled:
			self.attributes["aria-disabled"] = "true"
		if ftype:
			self.attributes["type"] = ftype
		snap = MagicMock()
		snap.computed_styles = {"pointer-events": pointer_events} if pointer_events else {}
		self.snapshot_node = snap
		self.ax_node = None
		self.backend_node_id = backend_node_id
		self.x = x
		self.y = y


def _state(selector_map: dict) -> MagicMock:
	"""最小 BrowserStateSummary 替身：dom_state.selector_map 用真实 dict。"""
	s = MagicMock()
	s.url = "https://example.com"
	s.dom_state.selector_map = selector_map
	return s


# ── 纯函数 ──────────────────────────────────────────────────────────────


class TestIsActionable:
	def test_visible_enabled_passes(self):
		assert is_actionable(_Node()) is True

	def test_not_visible_blocks(self):
		assert is_actionable(_Node(is_visible=False)) is False

	def test_none_visible_passes(self):
		assert is_actionable(_Node(is_visible=None)) is True

	def test_pointer_events_none_blocks(self):
		assert is_actionable(_Node(pointer_events="none"), check_receives_events=True) is False

	def test_paint_order_blocks(self):
		assert is_actionable(_Node(ignored_by_paint_order=True), check_receives_events=True) is False

	def test_disabled_attr_blocks(self):
		assert is_actionable(_Node(disabled=True)) is False

	def test_aria_disabled_blocks(self):
		assert is_actionable(_Node(aria_disabled=True)) is False

	def test_receives_events_off_skips_occlusion(self):
		# check_receives_events=False → paint_order / pointer-events 不查
		assert is_actionable(_Node(ignored_by_paint_order=True, pointer_events="none")) is True


class TestIsFileInput:
	def test_file_input(self):
		assert is_file_input(_Node(tag="INPUT", ftype="file")) is True

	def test_non_file_input(self):
		assert is_file_input(_Node(tag="INPUT", ftype="text")) is False

	def test_non_input_tag(self):
		assert is_file_input(_Node(tag="BUTTON")) is False


def test_whitelist():
	assert ACTIONABILITY_ACTIONS == frozenset({"click", "input_text", "select_dropdown"})


# ── wait_for_actionability（探索 index-based）──────────────────────────


class TestWaitForActionability:
	@pytest.mark.asyncio
	async def test_immediately_actionable_returns_fast(self):
		node = _Node()
		state = _state({5: node})
		browser = MagicMock()
		browser.get_state = AsyncMock(return_value=state)
		t0 = time.monotonic()
		s, n = await wait_for_actionability(browser, state, 5, timeout=1.0, poll=0.05)
		assert n is node
		assert time.monotonic() - t0 < 0.2  # 秒返，不轮询

	@pytest.mark.asyncio
	async def test_waits_until_actionable(self):
		# 初始 state 的 node 不可见；poll 后 get_state 返回可见 state
		hidden = _Node(is_visible=False)
		visible = _Node(is_visible=True)
		s1 = _state({5: hidden})
		s2 = _state({5: visible})
		browser = MagicMock()
		browser.get_state = AsyncMock(return_value=s2)
		s, n = await wait_for_actionability(browser, s1, 5, timeout=1.0, poll=0.02)
		assert n is visible

	@pytest.mark.asyncio
	async def test_timeout_degrades_no_raise(self):
		# node 永远不可见，get_state 永远返回同一不可见 state
		hidden = _Node(is_visible=False)
		state = _state({5: hidden})
		browser = MagicMock()
		browser.get_state = AsyncMock(return_value=state)
		t0 = time.monotonic()
		s, n = await wait_for_actionability(browser, state, 5, timeout=0.2, poll=0.05)
		assert time.monotonic() - t0 >= 0.15  # 等到超时
		assert n is hidden  # 降级返回最新 node，不抛

	@pytest.mark.asyncio
	async def test_index_missing_degrades_to_none(self):
		state = _state({})
		browser = MagicMock()
		browser.get_state = AsyncMock(return_value=state)
		s, n = await wait_for_actionability(browser, state, 5, timeout=0.1, poll=0.05)
		assert n is None  # index 不在 map → 拿不到 node → 降级


# ── step.py _execute_actions 插入门控 ─────────────────────────────────


def _agent_with_check(results=None, enabled=True):
	"""_make_agent + 覆盖 exploration_actionability_* 为真实值（默认 fake agent 关闭）。"""
	agent = _make_agent(results)
	agent.exploration_actionability_check = enabled
	agent.exploration_actionability_timeout = 1.0
	agent.exploration_actionability_poll = 0.05
	agent.exploration_actionability_receives_events = True
	agent.exploration_actionability_runtime_occlusion = False
	agent.exploration_actionability_stable = False
	agent.exploration_actionability_stable_interval = 0.1
	agent.exploration_actionability_stable_tolerance = 1.0
	return agent


def _spy_returning_state(monkeypatch):
	"""patch step 模块的 wait_for_actionability，返回 (原 state, node) 不破坏后续流程。"""
	import tree_walker.agent.step as step_mod
	spy = AsyncMock(
		side_effect=lambda browser, state, index, **kw: (state, state.dom_state.selector_map.get(index))
	)
	monkeypatch.setattr(step_mod, "wait_for_actionability", spy)
	return spy


class TestStepActionabilityGate:
	@pytest.mark.asyncio
	async def test_enabled_whitelist_calls_wait(self, monkeypatch):
		spy = _spy_returning_state(monkeypatch)
		agent = _agent_with_check([ActionResult()], enabled=True)
		bs = _state({1: _Node()})
		await StepPipeline._execute_actions(
			agent, {"action": {"name": "click", "params": {"index": 1}}}, bs
		)
		spy.assert_awaited_once()

	@pytest.mark.asyncio
	async def test_disabled_skips_wait(self, monkeypatch):
		spy = _spy_returning_state(monkeypatch)
		agent = _agent_with_check([ActionResult()], enabled=False)
		bs = _state({1: _Node()})
		await StepPipeline._execute_actions(
			agent, {"action": {"name": "click", "params": {"index": 1}}}, bs
		)
		spy.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_non_whitelist_skips_wait(self, monkeypatch):
		spy = _spy_returning_state(monkeypatch)
		agent = _agent_with_check([ActionResult()], enabled=True)
		bs = _state({1: _Node()})
		# go_back 不在白名单
		await StepPipeline._execute_actions(
			agent, {"action": {"name": "go_back", "params": {}}}, bs
		)
		spy.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_index_missing_skips_wait(self, monkeypatch):
		spy = _spy_returning_state(monkeypatch)
		agent = _agent_with_check([ActionResult()], enabled=True)
		bs = _state({})  # index 1 不在 map → node None → 跳过
		await StepPipeline._execute_actions(
			agent, {"action": {"name": "click", "params": {"index": 1}}}, bs
		)
		spy.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_file_input_skips_wait(self, monkeypatch):
		spy = _spy_returning_state(monkeypatch)
		agent = _agent_with_check([ActionResult()], enabled=True)
		bs = _state({1: _Node(tag="INPUT", ftype="file")})  # file input → 防御短路
		await StepPipeline._execute_actions(
			agent, {"action": {"name": "click", "params": {"index": 1}}}, bs
		)
		spy.assert_not_awaited()
