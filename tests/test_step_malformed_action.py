"""Tests for malformed-action handling (issue #173, PR #174 两轮 review 修订版).

LLM 偶发输出畸形动作（params 为字符串 / 动作为裸字符串），65.2% 轮曾把 778/782
从「可恢复的单步失败」放大成「整任务崩」。按两轮 code review 的裁决，防线收敛为
（docs/p7/code-review/2026-09-06-pr174-malformed-action-params-crash*.md）：

  1. choke point：client._normalize_actions_list 在 get_action 构造 result 前
     原地归一化——一处修复执行 / 参数校验 / _post_process / loop_detector /
     历史投影与持久化全部下游（round1 #1/#2/#6）
  2. rerun 的历史加载入口归一化（round2 #3）：AgentHistoryList.load_from_dict
     复用同一函数修存量 JSONL，_skip_reason 不再持有局部守卫
  3. EventBus.emit per-handler 隔离（round2 #1/#2 根因）+ _step 的 finally 对
     _finalize 整体兜底且不跳过 n_steps 递增（计数器边界）+
     _safe_project_interacted_elements 元数据降级
  4. 顶层裸字符串 action 归一化为命名动作走重试梯子（round2 #5），不再合成
     done(success=False) 零重试终止任务

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
from tree_walker.config import LLMSettings, TruncationSettings
from tree_walker.llm.client import LLMClient, _normalize_actions_list
from tree_walker.observability.event_bus import EventBus
from tree_walker.observability.events import StepStartEvent
from tree_walker.tools.actions import Tools
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
			("click", {}),   # 缺参形态的「优雅失败」证据在 TestValidateParamsGracefulFailure
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


# ── 3. rerun：历史加载入口归一化（review2 #3 的根因修法）───────────────


class TestRerunHistoryLoadNormalization:
	def _history_data(self, actions) -> dict:
		return {"history": [{
			"step_number": 1,
			"model_output": {"next_goal": "g", "actions": actions},
			"result": [],
			"state_summary": {},
			"interacted_element": None,
		}]}

	def _mixin(self) -> RerunMixin:
		fake = RerunMixin()
		fake._is_redundant_retry_step = lambda *a, **k: False  # 与本测试无关的分支
		return fake

	def test_load_from_dict_normalizes_malformed_actions(self):
		hl = AgentHistoryList.load_from_dict(self._history_data(
			[{"name": "input_text", "params": {"index": 1, "text": "x"}},
			 {"name": "click", "params": "561857"}, "wait"],
		))
		actions = hl.history[0].model_output["actions"]
		assert all(isinstance(a, dict) and isinstance(a["params"], dict) for a in actions)
		assert actions[1] == {"name": "click", "params": {}}
		assert actions[2] == {"name": "wait", "params": {}}
		assert hl.history[0].model_output["action"] == actions[0]  # 镜像刷新

	def test_load_from_dict_normalizes_str_single_action(self):
		# 老格式 {"action": "click"}（无 actions 列表）→ 命名动作 dict
		data = {"history": [{
			"step_number": 1,
			"model_output": {"next_goal": "g", "action": "click"},
			"result": [], "state_summary": {}, "interacted_element": None,
		}]}
		hl = AgentHistoryList.load_from_dict(data)
		assert hl.history[0].model_output["action"] == {"name": "click", "params": {}}

	def test_normalized_history_flows_through_skip_reason(self):
		# 端到端：畸形历史经 load_from_dict 归一化后，_skip_reason 按「无 index」
		# 处理返回跳过原因（修复前 truthy 字符串击穿 or {} → fp.get 崩掉 replay）
		hl = AgentHistoryList.load_from_dict(self._history_data(
			[{"name": "click", "params": "561857"}],
		))
		reason = self._mixin()._skip_reason(
			hl.history[0], previous_item=None, previous_succeeded=True, skip_failures=False,
		)
		assert reason is not None
		assert "无 index" in reason

	def test_valid_index_history_not_skipped(self):
		hl = AgentHistoryList.load_from_dict(self._history_data(
			[{"name": "click", "params": {"index": 5}}],
		))
		assert self._mixin()._skip_reason(
			hl.history[0], previous_item=None, previous_succeeded=True, skip_failures=False,
		) is None


# ── 4. _step 的 finally 兜底（review #5）───────────────────────────────


class TestStepFinallyGuard:
	@pytest.mark.asyncio
	async def test_finalize_exception_does_not_kill_step(self):
		# _finalize 从 finally 抛出曾与 778/782 同型地杀死整个 run；兜底后
		# _step 正常返回。
		agent = _loop_agent()
		agent._prepare_context = AsyncMock(return_value=(_browser_state(), "state msg"))
		agent._get_next_action = AsyncMock(return_value={"actions": [{"name": "click", "params": {}}]})
		agent._execute_actions = AsyncMock(return_value=[ActionResult()])
		agent._finalize = AsyncMock(side_effect=RuntimeError("disk full in JsonlRecorder"))
		done = await StepPipeline._step(agent)
		assert done is False  # 不抛、正常返回（历史/obs 降级由 error 日志记录）
		# 计数器边界（review2 #1）：吞异常不得跳过 n_steps 递增——否则 run() 的
		# while 循环退化为无界 livelock（动作照常执行、步数永不前进）
		assert agent.state.n_steps == 1

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


# ── 6. get_action 生产接线（review2 #4：经真实 LLM 解析路径驱动 choke point）──


def _make_tool_use_response(tool_input: dict) -> MagicMock:
	"""伪 Anthropic tool_use 响应（复刻 test_multi_act.py）。"""
	block = MagicMock()
	block.type = "tool_use"
	block.name = "agent_response"
	block.input = tool_input
	response = MagicMock()
	response.content = [block]
	return response


class TestGetActionChokePoint:
	"""畸形形态经 get_action 入、统一 dict 形态出——管线测试手工调
	_normalize_actions_list 测不到的「生产接线」层（防有人加提前返回分支绕过
	归一化时 CI 仍绿）。"""

	def setup_method(self) -> None:
		self.client = LLMClient(LLMSettings(api_key="test-key"))
		self.client.client = MagicMock()

	def _patch(self, tool_input: dict) -> None:
		self.client.client.messages.create = MagicMock(
			return_value=_make_tool_use_response(tool_input),
		)

	async def _get(self) -> dict:
		return await self.client.get_action(
			system_prompt="",
			messages=[],
			tool_schema={"name": "agent_response", "input_schema": {"type": "object"}},
		)

	@pytest.mark.asyncio
	async def test_malformed_list_normalized_and_mirrored(self):
		self._patch({
			"evaluation_previous_goal": "", "memory": "", "next_goal": "g",
			"action": [{"name": "click", "params": "561857"}, "wait"],
		})
		result = await self._get()
		assert result["actions"] == [
			{"name": "click", "params": {}},
			{"name": "wait", "params": {}},
		]
		assert result["action"] == result["actions"][0]  # 镜像同为归一化后形态

	@pytest.mark.asyncio
	async def test_top_level_string_action_becomes_retryable_named_action(self):
		# review2 #5：顶层裸字符串 action 不再合成 done(success=False) 零重试
		# 终止任务——归一化为命名动作，走缺参校验的重试梯子
		self._patch({
			"evaluation_previous_goal": "", "memory": "", "next_goal": "g",
			"action": "click",
		})
		result = await self._get()
		assert result["actions"] == [{"name": "click", "params": {}}]
		assert result["action"] == {"name": "click", "params": {}}

	@pytest.mark.asyncio
	async def test_text_json_path_string_action_normalized(self):
		# review2 #5 的完整链路：模型输出 JSON 文本（非 tool_use）里的裸字符串
		# action 同样被归一化
		text_block = MagicMock()
		text_block.type = "text"
		text_block.text = '```json\n{"next_goal": "g", "action": "click"}\n```'
		response = MagicMock()
		response.content = [text_block]
		self.client.client.messages.create = MagicMock(return_value=response)
		result = await self._get()
		assert result["actions"] == [{"name": "click", "params": {}}]
		assert result["action"] == {"name": "click", "params": {}}


# ── 7. EventBus 订阅者隔离（review2 #1/#2 根因修法）─────────────────────


class TestEventBusSubscriberIsolation:
	def test_bad_subscriber_does_not_break_emit_or_others(self):
		bus = EventBus()
		received: list[str] = []

		def bad(event):
			raise RuntimeError("disk full")

		def good(event):
			received.append(type(event).__name__)

		bus.subscribe("*", bad)
		bus.subscribe("*", good)
		bus.emit(StepStartEvent(step=1, session_id="s"))  # 不抛
		assert received == ["StepStartEvent"]

	def test_typed_and_wildcard_both_reached(self):
		bus = EventBus()
		seen: list[str] = []

		bus.subscribe("step_start", lambda e: seen.append("typed"))
		bus.subscribe("*", lambda e: seen.append("wildcard"))
		bus.emit(StepStartEvent(step=1, session_id="s"))
		assert seen == ["typed", "wildcard"]


# ── 8. 缺参优雅失败的真实证据（review2 #4 次级：真实 ClickParams）───────


class TestValidateParamsGracefulFailure:
	def test_normalized_click_empty_params_gets_retry_feedback(self):
		# 「click 缺参 → 优雅失败」的真实证据链：归一化后的 {"params": {}} 进
		# 参数校验，得到可反馈给 LLM 的缺字段错误（而非 AttributeError）——
		# 这正是澄清-重试梯子的输入。
		class _ClickParams(BaseModel):
			index: int

		registry = ActionRegistry()
		registry.actions["click"] = RegisteredAction(
			name="click", description="click", param_model=_ClickParams,
			handler=MagicMock(), terminates_sequence=False,
		)
		agent = MagicMock()
		agent.tools = MagicMock()
		agent.tools.registry = registry
		agent.tools._flatten_params = lambda params, name: Tools._flatten_params(
			MagicMock(), params, name,
		)
		response = {"action": {"name": "click", "params": {}}}
		err = StepPipeline._validate_action_params(agent, response)
		assert err is not None
		assert "index" in err  # 缺必填字段的反馈文案
