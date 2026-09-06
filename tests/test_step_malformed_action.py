"""Tests for malformed-action handling (issue #173, 778/782 崩溃回归).

LLM 偶发输出畸形动作（params 为字符串 / 动作为裸字符串），65.2% 轮曾把 778/782
从「可恢复的单步失败」放大成「整任务崩」（_step 的 finally 里 _finalize 再抛）。
三层防护各自的回归锚点：
  1. _execute_actions 循环头归一化——畸形动作以「缺必填参数」优雅失败
  2. _project_interacted_elements 的 params dict 守卫——投影不抛
  3. _safe_project_interacted_elements——投影自身 bug 只降级元数据，不杀任务

harness 复刻自 test_multi_act.py（_make_registry/_FakeTools/_make_agent）与
test_step_finalize.py（_FakeAgent），保持两个来源的形态一致。
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from tree_walker.agent.step import StepPipeline
from tree_walker.agent.views import ActionResult, AgentHistoryList, AgentState
from tree_walker.browser.views import BrowserStateSummary, SerializedDOMState
from tree_walker.config import TruncationSettings
from tree_walker.tools.registry import ActionRegistry, RegisteredAction


# ── helpers（复刻 test_multi_act.py）────────────────────────────────────


class _DummyParams(BaseModel):
	pass


def _make_registry(*names: str) -> ActionRegistry:
	registry = ActionRegistry()
	for name in names:
		registry.actions[name] = RegisteredAction(
			name=name,
			description=f"{name} action",
			param_model=_DummyParams,
			handler=MagicMock(),
			terminates_sequence=False,
		)
	return registry


class _FakeBrowser:
	def __init__(self, url: str = "https://example.com", target_id: str = "tab-1") -> None:
		self._url = url
		self.current_target_id = target_id

	async def get_current_url(self) -> str:
		return self._url


class _RecordingTools:
	"""记录每次 execute 调用（畸形归一化后 params 应为 {}）。"""

	def __init__(self) -> None:
		self.calls: list[tuple[str, dict]] = []
		self.registry = _make_registry("click", "input_text", "wait", "done")

	async def execute(self, name: str, params: dict, browser: Any, browser_state: Any) -> ActionResult:
		self.calls.append((name, dict(params)))
		# 非 done 步 success 必须为 None（ActionResult 模型约束），空结果 = 无错成功
		return ActionResult()


def _loop_agent() -> Any:
	agent = MagicMock()
	agent.state = AgentState()
	agent.action_timeout = 30
	agent.exploration_actionability_check = False
	agent.wait_between_actions = 0.0
	agent.tools = _RecordingTools()
	agent.browser = _FakeBrowser()
	agent._obs_bus = None
	agent._obs_session_id = "test"
	agent._current_model_call_id = ""
	agent._sensitive_map_for_log = None
	return agent


def _loop_state() -> BrowserStateSummary:
	return BrowserStateSummary(
		url="https://example.com",
		title="Example",
		dom_state=SerializedDOMState(_root=None, selector_map={}, element_tree_text="dom"),
	)


# ── 1. _execute_actions 归一化 ──────────────────────────────────────────


class TestExecuteActionsNormalization:
	@pytest.mark.asyncio
	async def test_string_params_does_not_crash(self):
		# 65.2% 轮 778 实锤形态：params 是字符串。修复前在 action.get / params.get
		# 抛 AttributeError；修复后归一化为 {}，动作以缺参形态执行（优雅失败）。
		agent = _loop_agent()
		model_output = {
			"actions": [
				{"name": "input_text", "params": {"index": 1, "text": "x"}},
				{"name": "click", "params": "561857"},  # 畸形
			]
		}
		results = await StepPipeline._execute_actions(agent, model_output, _loop_state())
		assert len(results) == 2
		assert agent.tools.calls[0] == ("input_text", {"index": 1, "text": "x"})
		assert agent.tools.calls[1] == ("click", {})  # 归一化后的执行参数

	@pytest.mark.asyncio
	async def test_bare_string_action_does_not_crash(self):
		# 裸字符串动作（"wait"）→ 归一化 {'name': 'wait', 'params': {}}
		agent = _loop_agent()
		model_output = {"actions": [{"name": "click", "params": {}}, "wait"]}
		results = await StepPipeline._execute_actions(agent, model_output, _loop_state())
		assert len(results) == 2
		assert agent.tools.calls[1] == ("wait", {})

	@pytest.mark.asyncio
	async def test_empty_string_params_coerced(self):
		agent = _loop_agent()
		model_output = {"actions": [{"name": "click", "params": ""}]}
		results = await StepPipeline._execute_actions(agent, model_output, _loop_state())
		assert len(results) == 1
		assert agent.tools.calls[0] == ("click", {})

	@pytest.mark.asyncio
	async def test_normal_actions_unaffected(self):
		agent = _loop_agent()
		model_output = {"actions": [{"name": "click", "params": {"index": 5}}]}
		await StepPipeline._execute_actions(agent, model_output, _loop_state())
		assert agent.tools.calls == [("click", {"index": 5})]


# ── 2. _project_interacted_elements 守卫 ────────────────────────────────


class _ProjectionAgent(StepPipeline):
	"""test_step_finalize.py _FakeAgent 同款：仅暴露投影所需属性。"""

	def __init__(self):
		self.state = AgentState()
		self.history = AgentHistoryList()
		self._step_start_time = time.time()
		self._truncation = TruncationSettings()
		self._obs_bus = None

	def _log_step_completion_summary(self, results):
		pass


def _projection_state(selector_map: dict) -> BrowserStateSummary:
	return BrowserStateSummary(
		url="https://example.com",
		title="Example",
		dom_state=SerializedDOMState(_root=None, selector_map=selector_map, element_tree_text="dom"),
	)


class TestProjectionGuard:
	def test_string_params_returns_none_entry(self):
		# selector_map 非空（否则走早退分支测不到守卫）；畸形 params 无 index
		# 可投影 → None 条目，不抛
		agent = _ProjectionAgent()
		model_output = {"actions": [
			{"name": "click", "params": "561857"},
			{"name": "input_text", "params": {"index": 999, "text": "x"}},  # index 不在 map
		]}
		out = agent._project_interacted_elements(model_output, _projection_state({1: object()}), None)
		assert out == [None, None]

	def test_bare_string_action_returns_none_entry(self):
		agent = _ProjectionAgent()
		model_output = {"actions": ["click"]}
		out = agent._project_interacted_elements(model_output, _projection_state({1: object()}), None)
		assert out == [None]


# ── 3. _safe_project_interacted_elements 兜底 ──────────────────────────


class TestSafeProjection:
	def test_degrades_to_none_on_projection_bug(self, monkeypatch):
		# 投影自身有 bug（人为注入）→ 降级 None + 不抛
		agent = _ProjectionAgent()
		monkeypatch.setattr(
			agent, "_project_interacted_elements",
			lambda *a, **k: (_ for _ in ()).throw(RuntimeError("metadata bug")),
		)
		assert agent._safe_project_interacted_elements({"actions": []}, None, None) is None

	@pytest.mark.asyncio
	async def test_finalize_appends_history_when_projection_raises(self, monkeypatch):
		# 端到端：_finalize（_step 的 finally 调它）在投影抛异常时仍追加历史，
		# 任务不死——778/782「finally 异常杀任务」的回归锚点。
		agent = _ProjectionAgent()
		monkeypatch.setattr(
			StepPipeline, "_project_interacted_elements",
			lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError("metadata bug")),
		)
		model_output = {"next_goal": "g", "action": {"name": "click", "params": {}}}
		await StepPipeline._finalize(agent, _projection_state({}), model_output, [ActionResult()])
		assert len(agent.history.history) == 1
		assert agent.history.history[-1].interacted_element is None

	@pytest.mark.asyncio
	async def test_finalize_string_params_end_to_end(self):
		# 端到端：畸形 params 的 model_output 走完整 _finalize（含安全投影）不抛
		agent = _ProjectionAgent()
		model_output = {"actions": [{"name": "click", "params": "561857"}]}
		await StepPipeline._finalize(
			agent, _projection_state({1: object()}), model_output, [ActionResult()]
		)
		assert len(agent.history.history) == 1
		assert agent.history.history[-1].interacted_element == [None]
