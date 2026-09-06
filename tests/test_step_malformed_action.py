"""Tests for malformed-action handling (issue #173, PR #174 review 修订版).

LLM 偶发输出畸形动作（params 为字符串 / 动作为裸字符串），65.2% 轮曾把 778/782
从「可恢复的单步失败」放大成「整任务崩」。按 PR #174 code review 的裁决，防线
收敛为（docs/p7/code-review/2026-09-06-pr174-malformed-action-params-crash.md）：

  1. choke point：client._normalize_actions_list 在 get_action 构造 result 前
     原地归一化——一处修复执行 / 参数校验 / _post_process / loop_detector /
     历史投影与持久化全部下游（review #1/#2/#6）
  2. rerun._skip_reason 的存量数据守卫（review #3——归一化只保新历史，旧 JSONL
     可能已有畸形数据）
  3. _step 的 finally 对 _finalize 整体兜底（review #5——EventBus 订阅者无隔离）
     + _safe_project_interacted_elements 元数据降级

harness 复刻自 test_multi_act.py（registry/tools/agent）与 test_step_finalize.py
（_FakeAgent 形态）。
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from tree_walker.agent.loop_detector import ActionLoopDetector
from tree_walker.agent.rerun import RerunMixin
from tree_walker.agent.step import StepPipeline
from tree_walker.agent.views import ActionResult, AgentHistoryList, AgentState
from tree_walker.browser.views import BrowserStateSummary, SerializedDOMState
from tree_walker.config import TruncationSettings
from tree_walker.llm.client import _normalize_actions_list
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
	def __init__(self) -> None:
		self.calls: list[tuple[str, dict]] = []
		self.registry = _make_registry("click", "input_text", "wait", "done")

	async def execute(self, name: str, params: dict, browser: Any, browser_state: Any) -> ActionResult:
		self.calls.append((name, dict(params)))
		return ActionResult()  # 非 done 步 success 必须为 None（ActionResult 模型约束）


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
	# _post_process 需要的真实部件（其余 falsy 属性走跳过分支）
	agent.loop_detector = ActionLoopDetector()
	agent._track_downloads = False
	agent._enable_planning = False
	agent.plan_manager = None
	agent._compactor = None  # MagicMock 不可 await——_step 里 `await .maybe_compact`
	agent._handle_step_error = AsyncMock()
	return agent


def _browser_state(selector_map: dict | None = None) -> BrowserStateSummary:
	return BrowserStateSummary(
		url="https://example.com",
		title="Example",
		dom_state=SerializedDOMState(
			_root=None, selector_map=selector_map or {}, element_tree_text="dom",
		),
	)


def _malformed_model_output() -> dict:
	"""issue #173 / 778 实锤形态：params 为字符串 + 裸字符串动作。"""
	return {
		"actions": [
			{"name": "input_text", "params": {"index": 1, "text": "x"}},
			{"name": "click", "params": "561857"},  # 畸形 params
			"wait",  # 裸字符串动作
		]
	}


# ── 1. choke point：client._normalize_actions_list ─────────────────────


class TestNormalizeActionsList:
	def test_string_params_coerced_to_empty_dict(self):
		actions = [{"name": "click", "params": "561857"}]
		_normalize_actions_list(actions)
		assert actions == [{"name": "click", "params": {}}]

	def test_bare_string_action_coerced_to_named_action(self):
		actions = ["wait"]
		_normalize_actions_list(actions)
		assert actions == [{"name": "wait", "params": {}}]

	def test_none_params_filled_without_warning(self, caplog):
		# params 缺失/None = setdefault 既有语义，不是畸形——不应告警
		import logging

		actions = [{"name": "click"}]
		with caplog.at_level(logging.WARNING):
			_normalize_actions_list(actions)
		assert actions == [{"name": "click", "params": {}}]
		assert caplog.records == []

	def test_clean_actions_untouched(self):
		actions = [{"name": "click", "params": {"index": 5}}]
		_normalize_actions_list(actions)
		assert actions == [{"name": "click", "params": {"index": 5}}]

	def test_in_place_mutation_covers_full_list(self):
		# get_action 随后取 actions_list[0] 作镜像——归一化必须发生在镜像之前且
		# 覆盖每个条目（review #1：下游逐条目重读原始数据）
		actions = [{"name": "input_text", "params": "x"}, "click"]
		_normalize_actions_list(actions)
		assert all(isinstance(a, dict) and isinstance(a["params"], dict) for a in actions)
		assert actions[1]["name"] == "click"

	def test_warning_logs_type_not_value(self, caplog):
		# review #4 脱敏不变量：client 在归一化前已把占位符还原为真值，畸形
		# params 字符串可能含密钥——warning 只许记类型，不许记值
		import logging

		actions = [{"name": "input_text", "params": "<SECRET-TOKEN-1234>"}]
		with caplog.at_level(logging.WARNING, logger="tree_walker.llm.client"):
			_normalize_actions_list(actions)
		assert any("params malformed (str)" in r.getMessage() for r in caplog.records)
		assert not any("<SECRET-TOKEN-1234>" in r.getMessage() for r in caplog.records)


# ── 2. 流水线端到端：归一化后走完 execute + post_process ────────────────


class TestPipelineStagesEndToEnd:
	"""review #1 的补口：原测试只孤立驱动 _execute_actions（CI 绿但生产崩——
	_post_process 重读原始 model_output）。这里按真实时序：client 归一化 →
	执行 → 后处理，全程不抛。"""

	@pytest.mark.asyncio
	async def test_malformed_output_flows_through_execute_and_post_process(self):
		agent = _loop_agent()
		model_output = _malformed_model_output()
		_normalize_actions_list(model_output["actions"])  # get_action 的 choke point
		results = await StepPipeline._execute_actions(agent, model_output, _browser_state())
		assert len(results) == 3
		assert agent.tools.calls == [
			("input_text", {"index": 1, "text": "x"}),
			("click", {}),   # 缺参执行 → 优雅失败（非 AttributeError）
			("wait", {}),
		]
		StepPipeline._post_process(agent, results, model_output)  # 不抛即过
		assert agent.state.consecutive_failures == 0  # 多动作失败不计数（既有语义）

	@pytest.mark.asyncio
	async def test_raw_malformed_output_never_reaches_stages(self):
		# 契约锚点：未经 client 归一化的裸字符串动作直灌 step 层会在循环头
		# `action.get` 崩——这正是归一化必须在 choke point 完成的原因
		# （step 层不再持有本地副本）。用纯裸字符串形态保证确定性（str params
		# 形态会被 guard #3 提前截断，走不到该断言）。
		agent = _loop_agent()
		model_output = {"actions": ["click"]}
		with pytest.raises(AttributeError):
			await StepPipeline._execute_actions(agent, model_output, _browser_state())


# ── 3. rerun 存量数据守卫 ───────────────────────────────────────────────


class TestRerunSkipReasonGuard:
	def _item(self, actions: list) -> SimpleNamespace:
		return SimpleNamespace(
			model_output={"actions": actions},
			result=[ActionResult()],
			interacted_element=None,
			step_number=1,
		)

	def _mixin(self) -> RerunMixin:
		fake = RerunMixin()
		fake._is_redundant_retry_step = lambda *a, **k: False  # 与本测试无关的分支
		return fake

	def test_string_params_history_does_not_crash(self):
		# review #3：truthy 字符串 "561857" 会击穿 `or {}` 兜底 → fp.get 崩掉
		# 整个 replay；守卫后按「无 index」处理返回跳过原因
		reason = self._mixin()._skip_reason(
			self._item([{"name": "click", "params": "561857"}]),
			previous_item=None, previous_succeeded=True, skip_failures=False,
		)
		assert reason is not None
		assert "无 index" in reason

	def test_valid_index_history_not_skipped(self):
		reason = self._mixin()._skip_reason(
			self._item([{"name": "click", "params": {"index": 5}}]),
			previous_item=None, previous_succeeded=True, skip_failures=False,
		)
		assert reason is None


# ── 4. _step 的 finally 兜底（review #5）───────────────────────────────


class TestStepFinallyGuard:
	@pytest.mark.asyncio
	async def test_finalize_exception_does_not_kill_step(self):
		# EventBus 订阅者无异常隔离（JsonlRecorder 磁盘满/文件关闭）——_finalize
		# 从 finally 抛出曾与 778/782 同型地杀死整个 run；兜底后 _step 正常返回。
		agent = _loop_agent()
		agent._prepare_context = AsyncMock(return_value=(_browser_state(), "state msg"))
		agent._get_next_action = AsyncMock(return_value={"actions": [{"name": "click", "params": {}}]})
		agent._execute_actions = AsyncMock(return_value=[ActionResult()])
		agent._finalize = AsyncMock(side_effect=RuntimeError("disk full in JsonlRecorder"))
		done = await StepPipeline._step(agent)
		assert done is False  # 不抛、正常返回（历史/obs 降级由 error 日志记录）

	@pytest.mark.asyncio
	async def test_finalize_still_runs_normally(self):
		agent = _loop_agent()
		agent._prepare_context = AsyncMock(return_value=(_browser_state(), "state msg"))
		agent._get_next_action = AsyncMock(return_value={"actions": [{"name": "click", "params": {}}]})
		agent._execute_actions = AsyncMock(return_value=[ActionResult()])
		agent._finalize = AsyncMock()
		assert await StepPipeline._step(agent) is False
		agent._finalize.assert_awaited_once()


# ── 5. 投影降级（保留：元数据不杀任务的两层之一）───────────────────────


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


class TestSafeProjection:
	def test_degrades_to_none_on_projection_bug(self, monkeypatch):
		agent = _ProjectionAgent()
		monkeypatch.setattr(
			agent, "_project_interacted_elements",
			lambda *a, **k: (_ for _ in ()).throw(RuntimeError("metadata bug")),
		)
		assert agent._safe_project_interacted_elements({"actions": []}, None, None) is None

	@pytest.mark.asyncio
	async def test_finalize_appends_history_when_projection_raises(self, monkeypatch):
		agent = _ProjectionAgent()
		monkeypatch.setattr(
			StepPipeline, "_project_interacted_elements",
			lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError("metadata bug")),
		)
		model_output = {"next_goal": "g", "action": {"name": "click", "params": {}}}
		await StepPipeline._finalize(agent, _browser_state(), model_output, [ActionResult()])
		assert len(agent.history.history) == 1
		assert agent.history.history[-1].interacted_element is None

	@pytest.mark.asyncio
	async def test_finalize_string_params_degrades_metadata(self):
		# 畸形 params 直达投影（未经 choke point 的旁路场景）→ 投影自身抛、
		# safe 包装降级 None——历史照常追加
		agent = _ProjectionAgent()
		model_output = {"actions": [{"name": "click", "params": "561857"}]}
		await StepPipeline._finalize(
			agent, _browser_state(selector_map={1: object()}), model_output, [ActionResult()]
		)
		assert len(agent.history.history) == 1
		assert agent.history.history[-1].interacted_element is None
