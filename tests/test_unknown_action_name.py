"""Tests for unknown-action-name collateral fix (issue #176).

死亡链（2026-09-07 视觉验收会话第二轮，PR #175/177 采样）：
  multi_act 批 ['plan_update', 'click', 'wait'] 头部未注册名（agent_response
  的响应字段被误发为动作名）→ 镜像 = actions[0] 使 _validate_action_params
  对整步报 Unknown action 进重试梯（兄弟动作陪葬）→ 重试响应退化成无名字
  动作 → 内梯一次 fallback done 死刑 → 任务提前终结。

三层修复（docs/bug-fix/176-unknown-action-name-collateral.md）：
  P0-A  action_shape：known_names 注入——live 批次丢「shape 合法但未注册」
        名 + 镜像刷新；响应字段名（plan_update/current_plan_item/thinking）
        降 INFO；丢光保留原列表（落内梯「Unknown action」反馈重试，不合成不空批）
  P0-B  step：参数校验内梯对无效动作与外梯对称——澄清重试（共用
        _PARAM_VALIDATION_MAX_RETRIES 预算），仍无效才 fallback done
  P1-C  registry/plan_manager：响应字段不是动作的澄清文案

harness 复刻自 test_step_malformed_action.py（_RecordingTools/_FakeBrowser）
与 _ProjectionAgent 形态（真实 StepPipeline 方法 + 最小属性集）。
"""

from __future__ import annotations

import copy
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from tree_walker.action_shape import normalize_actions_list, normalize_model_output
from tree_walker.agent.plan_manager import PlanManager
from tree_walker.agent.step import StepPipeline, _PARAM_VALIDATION_MAX_RETRIES
from tree_walker.agent.views import ActionResult, AgentState, PlanItem
from tree_walker.browser.views import BrowserStateSummary, SerializedDOMState
from tree_walker.config import LLMSettings
from tree_walker.llm.client import LLMClient
from tree_walker.tools.registry import ActionRegistry, RegisteredAction


@pytest.fixture(autouse=True)
def _mock_anthropic_sdk(monkeypatch):
	# test_step_malformed_action.py 同款：LLMClient() 急切构造真实 SDK——
	# 代理变量机器上直接 ValueError，提前 mock 掉符号使测试环境无关。
	monkeypatch.setattr("tree_walker.llm.client.Anthropic", MagicMock())


# ── helpers（复刻 test_step_malformed_action.py）────────────────────────


class _DummyParams(BaseModel):
	pass


class _ClickParams(BaseModel):
	index: int


_KNOWN_NAMES = frozenset({"click", "wait", "done"})


def _make_registry() -> ActionRegistry:
	"""click 需 index（缺参可造 Invalid-params 进内梯）；wait/done 无字段。"""
	registry = ActionRegistry()
	for name in _KNOWN_NAMES:
		registry.actions[name] = RegisteredAction(
			name=name,
			description=f"{name} action",
			param_model=_ClickParams if name == "click" else _DummyParams,
			handler=MagicMock(),
			terminates_sequence=False,
		)
	return registry


class _RecordingTools:
	def __init__(self) -> None:
		self.calls: list[tuple[str, dict]] = []
		self.registry = _make_registry()

	def _flatten_params(self, params: dict, action_name: str) -> dict:
		return dict(params)  # 测试 params 均为扁平形

	async def execute(self, name: str, params: dict, browser: Any, browser_state: Any) -> ActionResult:
		self.calls.append((name, dict(params)))
		return ActionResult()  # 非 done 步 success 必须为 None（模型约束）


class _FakeBrowser:
	def __init__(self, url: str = "https://example.com", target_id: str = "tab-1") -> None:
		self._url = url
		self.current_target_id = target_id

	async def get_current_url(self) -> str:
		return self._url


class _LadderAgent(StepPipeline):
	"""真实 StepPipeline 方法 + 最小属性集——可驱动 _get_next_action →
	_get_action_with_retry（外梯）→ _validate_params_or_retry（内梯）→
	_execute_actions 全链。responses 依次作为 get_action 的返回值。"""

	def __init__(self, responses: list[dict[str, Any]] | None = None) -> None:
		self.state = AgentState()
		self.llm = SimpleNamespace(get_action=AsyncMock(side_effect=list(responses or [])))
		self.tools = _RecordingTools()
		self.browser = _FakeBrowser()
		self.action_timeout = 30
		self.exploration_actionability_check = False
		self.wait_between_actions = 0.0
		self.llm_timeout = 30
		self.messages: list[dict[str, Any]] = []
		self._system_prompt = ""
		self._tool_schema = {"name": "agent_response", "input_schema": {"type": "object"}}
		self._obs_bus = None
		self._obs_session_id = "test"
		self._current_model_call_id = ""
		self._sensitive_map = {}
		self._enable_message_typing = False
		self.max_actions_per_step = 5
		self._save_conversation_path = None
		# issue #186：_execute_actions 的连败记录点与 done 门禁所需属性
		from tree_walker.agent.loop_detector import FailureStreakTracker, ZeroResultStreakTracker
		self.failure_streak = FailureStreakTracker()
		self._enable_done_gate = False  # 门禁另测（test_done_uncertainty_gate.py）
		# issue #186-c2：零结果降级跟踪所需属性
		self.zero_result_streak = ZeroResultStreakTracker()
		self._pending_zero_result_nudge = None

	def _trim_messages(self) -> list[dict[str, Any]]:
		return list(self.messages)


def _browser_state(selector_map: dict | None = None) -> BrowserStateSummary:
	return BrowserStateSummary(
		url="https://example.com",
		title="Example",
		dom_state=SerializedDOMState(
			_root=None, selector_map=selector_map or {}, element_tree_text="dom",
		),
	)


def _death_chain_response() -> dict[str, Any]:
	"""issue #176 死亡链首环：镜像 = actions[0] = 未注册的响应字段名。"""
	return {
		"evaluation_previous_goal": "", "memory": "", "next_goal": "g",
		"action": {"name": "plan_update", "params": {"steps": ["a"]}},
		"actions": [
			{"name": "plan_update", "params": {"steps": ["a"]}},
			{"name": "click", "params": {"index": 5}},
			{"name": "wait", "params": {}},
		],
	}


# ── 1. normalize 层：known_names 注入（P0-A）────────────────────────────


class TestNormalizeKnownNames:
	def test_head_unregistered_dropped_mirror_refreshed(self):
		# 丢名留批：头部 plan_update 被丢，镜像刷新为第一个幸存动作 click
		mo = _death_chain_response()
		normalize_model_output(mo, known_names=_KNOWN_NAMES)
		assert mo["actions"] == [
			{"name": "click", "params": {"index": 5}},
			{"name": "wait", "params": {}},
		]
		assert mo["action"]["name"] == "click"

	def test_response_field_name_logged_info_not_warning(self, caplog):
		# 良性混淆：plan_update/current_plan_item/thinking 语义在响应体字段里，
		# 丢弃时降为 INFO
		mo = {"actions": [{"name": "plan_update", "params": {}}, {"name": "click", "params": {}}]}
		with caplog.at_level(logging.INFO, logger="tree_walker.action_shape"):
			normalize_model_output(mo, known_names=_KNOWN_NAMES)
		drops = [r for r in caplog.records if "plan_update" in r.getMessage()]
		assert drops and all(r.levelno == logging.INFO for r in drops)

	def test_thinking_name_dropped_as_info(self, caplog):
		mo = {"actions": [{"name": "thinking", "params": {}}, {"name": "click", "params": {}}]}
		with caplog.at_level(logging.INFO, logger="tree_walker.action_shape"):
			normalize_model_output(mo, known_names=_KNOWN_NAMES)
		assert [a["name"] for a in mo["actions"]] == ["click"]
		assert not any(r.levelno >= logging.WARNING for r in caplog.records)

	def test_hallucinated_name_dropped_with_warning(self, caplog):
		mo = {"actions": [{"name": "scroll_to_moon", "params": {}}, {"name": "click", "params": {}}]}
		with caplog.at_level(logging.INFO, logger="tree_walker.action_shape"):
			normalize_model_output(mo, known_names=_KNOWN_NAMES)
		assert [a["name"] for a in mo["actions"]] == ["click"]
		assert any(
			r.levelno == logging.WARNING and "scroll_to_moon" in r.getMessage()
			for r in caplog.records
		)

	def test_mid_list_unregistered_dropped(self):
		# 连坐消除对任意位置成立：中段未知名原本要到执行期才得可见 Unknown
		# action 失败——现在提前丢弃
		actions = [
			{"name": "click", "params": {}},
			{"name": "scroll_to_moon", "params": {}},
			{"name": "wait", "params": {}},
		]
		normalize_actions_list(actions, known_names=_KNOWN_NAMES)
		assert [a["name"] for a in actions] == ["click", "wait"]

	def test_bare_string_hallucination_dropped(self):
		# 裸字符串先被 shape 层强转为命名动作（#173），再按名字丢弃
		actions = ["scroll_to_moon", "click"]
		normalize_actions_list(actions, known_names=_KNOWN_NAMES)
		assert actions == [{"name": "click", "params": {}}]

	def test_all_unknown_batch_restored_as_is(self, caplog):
		# 丢光兜底：全部未注册 → 列表原样保留（镜像照旧指向头部 → 内梯
		# 「Unknown action」参数反馈重试，模型可重发），不合成动作、不空批
		original = [
			{"name": "plan_update", "params": {}},
			{"name": "scroll_to_moon", "params": {}},
		]
		mo = {"action": original[0], "actions": list(original)}
		with caplog.at_level(logging.INFO, logger="tree_walker.action_shape"):
			normalize_model_output(mo, known_names=_KNOWN_NAMES)
		assert mo["actions"] == original
		assert mo["action"] is original[0]
		assert any("kept as-is" in r.getMessage() for r in caplog.records)

	def test_known_names_none_behavior_unchanged(self):
		# known_names=None（client choke point 等既有调用方）：与 #173 行为
		# 逐例一致——未注册名不丢（#173 只管 shape），params 修复照旧
		mo = {"actions": [{"name": "plan_update", "params": {}}, {"name": "click", "params": "x"}]}
		normalize_model_output(mo)
		assert [a["name"] for a in mo["actions"]] == ["plan_update", "click"]
		assert mo["actions"][1]["params"] == {}
		assert mo["action"]["name"] == "plan_update"

	def test_idempotent_second_pass_no_further_drop(self, caplog):
		mo = _death_chain_response()
		normalize_model_output(mo, known_names=_KNOWN_NAMES)
		snapshot = copy.deepcopy(mo)
		with caplog.at_level(logging.INFO, logger="tree_walker.action_shape"):
			normalize_model_output(mo, known_names=_KNOWN_NAMES)
		assert mo == snapshot
		assert caplog.records == []  # 二次归一化零日志（无丢无修）

	def test_shape_malformed_entries_not_dropped(self):
		# 处置范围只有「合法名字但未注册」：shape 畸形的多元素 live 头原样
		# 保留（#173 review7 #1 澄清重试路径），与 known_names 无关
		actions = [None, {"name": "click", "params": {}}]
		normalize_actions_list(actions, known_names=_KNOWN_NAMES)
		assert actions[0] is None

	def test_honest_done_exempt_from_drop(self):
		# 诚实失败 done 是归一化层自产的终止动作（#173），不参与未注册名
		# 丢弃——即使 registry 里没有 "done"
		single = [None]
		normalize_actions_list(single, known_names=frozenset())
		assert single[0]["name"] == "done"
		assert single[0]["params"]["success"] is False

	def test_history_context_ignores_known_names(self):
		# 方案「不做」节：重放侧未注册名由消费方跳过，无连坐问题——
		# history 上下文即使传入 known_names 也不丢
		actions = [{"name": "scroll_to_moon", "params": {}}, "wait"]
		normalize_actions_list(actions, context="history", known_names=_KNOWN_NAMES)
		assert actions[0]["name"] == "scroll_to_moon"
		assert actions[1] == "wait"


# ── 2. step 层：死亡链 / 外梯 / 内梯（P0-A 接线 + P0-B 对称化）──────────


class TestDeathChainBatch:
	@pytest.mark.asyncio
	async def test_batch_executes_without_entering_retry_ladder(self):
		# 验收 #1：['plan_update','click','wait'] 批 + 镜像 plan_update——
		# 归一化丢头刷新镜像后校验直接通过，LLM 恰好一次调用，本步执行
		# click+wait（兄弟动作不再陪葬，任务不提前终结）
		agent = _LadderAgent([_death_chain_response()])
		response = await agent._get_next_action(_browser_state(), "state msg")

		agent.llm.get_action.assert_awaited_once()
		assert [a["name"] for a in response["actions"]] == ["click", "wait"]
		assert response["action"]["name"] == "click"

		await StepPipeline._execute_actions(agent, response, _browser_state())
		assert agent.tools.calls == [("click", {"index": 5}), ("wait", {})]


class TestInnerLadderClarification:
	@pytest.mark.asyncio
	async def test_invalid_action_gets_clarification_before_death(self):
		# 死因链第 3 环：Invalid-params 重试响应退化成无名字动作——不再
		# 一次 fallback 死刑，先澄清重试；澄清仍无效才 fallback done
		agent = _LadderAgent([
			{"action": {"name": "click", "params": {}}},   # 缺 index → Invalid-params
			{"action": {"params": {"index": 5}}},          # 无 name → 澄清
			{"action": {"params": {}}},                    # 仍无 name → 才死刑
		])
		result = await agent._get_action_with_retry([])

		# 预算有界：初调 1 + 内梯 2（= _PARAM_VALIDATION_MAX_RETRIES），
		# 一次 Invalid-params + 一次 Invalid-action 共用预算
		assert agent.llm.get_action.await_count == 1 + _PARAM_VALIDATION_MAX_RETRIES
		assert result["action"]["name"] == "done"
		assert result["action"]["params"]["success"] is False

		# 第二次重试（澄清）用的是外梯同款无效动作措辞，非参数措辞
		clarify_messages = agent.llm.get_action.await_args_list[2].kwargs["messages"]
		assert "forgot to return an action" in clarify_messages[-1]["content"]

	@pytest.mark.asyncio
	async def test_clarification_recovery_returns_valid_action(self):
		# 澄清后恢复合法 → 正常返回（不死刑）
		agent = _LadderAgent([
			{"action": {"name": "click", "params": {}}},
			{"action": {"params": {"index": 5}}},                     # 无 name → 澄清
			{"action": {"name": "click", "params": {"index": 7}}},   # 恢复合法
		])
		result = await agent._get_action_with_retry([])

		assert agent.llm.get_action.await_count == 3
		assert result["action"] == {"name": "click", "params": {"index": 7}}

	@pytest.mark.asyncio
	async def test_params_retry_feedback_wording_unchanged(self):
		# 既有 Invalid-params 反馈语义零变化（#173 系列）
		agent = _LadderAgent([
			{"action": {"name": "click", "params": {}}},
			{"action": {"name": "click", "params": {"index": 5}}},
		])
		result = await agent._get_action_with_retry([])

		assert result["action"] == {"name": "click", "params": {"index": 5}}
		first_retry = agent.llm.get_action.await_args_list[1].kwargs["messages"]
		assert "action parameters are invalid" in first_retry[-1]["content"]
		assert "index" in first_retry[-1]["content"]

	@pytest.mark.asyncio
	async def test_budget_bound_worst_case(self):
		# 最坏路径（每次内梯重试都无效）：内梯调用次数恰为预算值，不超
		# _PARAM_VALIDATION_MAX_RETRIES
		agent = _LadderAgent([
			{"action": {"name": "click", "params": {}}},
			{"action": {"params": {}}},
			{"action": {"params": {}}},
		])
		await agent._get_action_with_retry([])
		inner_calls = agent.llm.get_action.await_count - 1
		assert inner_calls <= _PARAM_VALIDATION_MAX_RETRIES


class TestOuterLadderRegression:
	@pytest.mark.asyncio
	async def test_invalid_then_clarified_then_valid(self):
		# 外梯既有语义：无效 → 澄清 → 合法 → 正常进校验
		agent = _LadderAgent([
			{"action": {}, "actions": [{}]},
			{"action": {"name": "click", "params": {"index": 1}}},
		])
		result = await agent._get_action_with_retry([])
		assert agent.llm.get_action.await_count == 2
		assert result["action"]["name"] == "click"

	@pytest.mark.asyncio
	async def test_invalid_after_clarification_falls_back(self):
		agent = _LadderAgent([{}, {}])
		result = await agent._get_action_with_retry([])
		assert agent.llm.get_action.await_count == 2
		assert result["action"]["name"] == "done"
		assert result["action"]["params"]["success"] is False

	@pytest.mark.asyncio
	async def test_nondict_response_clarified_not_crash(self):
		# _normalize_llm_response 对非 dict 原样透传 → _is_valid_action 判假
		# （isinstance 守卫）进外梯澄清——注入/旁路 LLM 的契约违反不再
		# AttributeError 崩断（兑现透传 docstring 承诺）
		agent = _LadderAgent([
			"not-a-dict",
			{"action": {"name": "click", "params": {"index": 1}}},
		])
		result = await agent._get_action_with_retry([])
		assert agent.llm.get_action.await_count == 2
		first_retry = agent.llm.get_action.await_args_list[1].kwargs["messages"]
		assert "forgot to return an action" in first_retry[-1]["content"]
		assert result["action"]["name"] == "click"

	@pytest.mark.asyncio
	async def test_all_unknown_batch_kept_for_clarification_retry(self):
		# 丢光兜底接线验证：全部未注册 → 原样保留 → 镜像过 _is_valid_action
		# → 「Unknown action」参数反馈重试（模型可重发），而非 fallback 死刑
		agent = _LadderAgent([
			{"action": {"name": "plan_update", "params": {}},
			 "actions": [{"name": "plan_update", "params": {}}]},
			{"action": {"name": "click", "params": {"index": 3}}},
		])
		result = await agent._get_action_with_retry([])

		assert agent.llm.get_action.await_count == 2
		assert result["action"]["name"] == "click"
		first_retry = agent.llm.get_action.await_args_list[1].kwargs["messages"]
		assert "Unknown action" in first_retry[-1]["content"]


# ── 3. P0-A 设计边界：client choke point 照旧不传 known_names ────────────


def _make_tool_use_response(tool_input: dict) -> MagicMock:
	"""伪 Anthropic tool_use 响应（复刻 test_step_malformed_action.py）。"""
	block = MagicMock()
	block.type = "tool_use"
	block.name = "agent_response"
	block.input = tool_input
	response = MagicMock()
	response.content = [block]
	return response


class TestClientChokePointUnchanged:
	@pytest.mark.asyncio
	async def test_client_keeps_unregistered_name_as_is(self):
		# client.py 无 registry 依赖，照旧不传 known_names——未注册名在
		# client 输出中原样保留，丢弃只发生在 step 层归一化（幂等兜底处）
		client = LLMClient(LLMSettings(api_key="test-key"))
		client.client = MagicMock()
		client.client.messages.create = MagicMock(return_value=_make_tool_use_response({
			"evaluation_previous_goal": "", "memory": "", "next_goal": "g",
			"action": [{"name": "plan_update", "params": {}},
			           {"name": "click", "params": {"index": 5}}],
		}))
		result = await client.get_action(
			system_prompt="", messages=[],
			tool_schema={"name": "agent_response", "input_schema": {"type": "object"}},
		)
		assert [a["name"] for a in result["actions"]] == ["plan_update", "click"]
		assert result["action"]["name"] == "plan_update"


# ── 4. P1-C：schema / prompt 澄清文案 ───────────────────────────────────


class TestResponseFieldClarificationCopy:
	def test_schema_descriptions_warn_against_action_use(self):
		registry = _make_registry()
		schema = registry.get_tool_schema(enable_planning=True)
		props = schema["input_schema"]["properties"]
		for field in ("plan_update", "current_plan_item"):
			assert "RESPONSE FIELD" in props[field]["description"]
			assert "never use it as an action name" in props[field]["description"]

	def test_planning_fields_absent_without_enable_planning(self):
		# 既有行为零变化：enable_planning=False 不注入这两个字段
		registry = _make_registry()
		schema = registry.get_tool_schema(enable_planning=False)
		props = schema["input_schema"]["properties"]
		assert "plan_update" not in props
		assert "current_plan_item" not in props

	def test_nudges_name_plan_update_as_response_field(self):
		mgr = PlanManager()
		assert "response field, not an action" in mgr.build_replan_nudge(3, 3, [PlanItem(text="A")])
		assert "response field, not an action" in mgr.build_exploration_nudge(5, 5, None)


# ── 5. review8 #3（#186-c2）：record 前展平的未注册名 KeyError 防护 ──────


class TestUnknownNameRecordFlattenGuard:
	"""_execute_actions 里 zero_result_streak.record 前的展平调用必须先守卫
	注册表——真实 ``Tools._flatten_params`` 单 dict 值分支裸下标
	``registry.actions[name]``（actions.py:2976），未知名（校验梯耗尽
	"proceeding anyway" / 旁路 LLM）+ 单 dict 值 params（read_grid 拼写错 +
	正常 ``{"filters": {...}}``）命中该分支；record 在 per-action try 之外，
	KeyError 会把 execute 已优雅返回的 Unknown action error 降级成整步崩溃。
	"""

	@pytest.mark.asyncio
	async def test_unknown_name_with_dict_param_no_crash(self):
		from tree_walker.tools.actions import Tools as RealTools
		agent = _LadderAgent([])
		agent.tools = RealTools()  # 真实 registry：read_gird 未注册、展平会裸下标
		response = {
			"action": {"name": "read_gird", "params": {"filters": {"name": "X"}}},
			"actions": [{"name": "read_gird", "params": {"filters": {"name": "X"}}}],
		}
		results = await StepPipeline._execute_actions(agent, response, _browser_state())
		assert len(results) == 1
		assert "Unknown action" in (results[0].error or "")  # 优雅失败，不崩溃

	def test_flatten_params_unknown_name_dict_param_raises(self):
		"""动机锚点：未知名 + 单 dict 值 params 对 _flatten_params 裸调确实
		KeyError（调用方守卫约定的存在理由——两个既有调用点 execute/
		_validate_action_params 均先查注册表）。"""
		from tree_walker.tools.actions import Tools as RealTools
		with pytest.raises(KeyError):
			RealTools()._flatten_params({"filters": {"a": 1}}, "read_gird")
