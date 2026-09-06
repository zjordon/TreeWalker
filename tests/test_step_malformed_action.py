"""Tests for malformed-action handling (issue #173, PR #174 四轮 review 修订版).

LLM 偶发输出畸形动作（params 为字符串 / 动作为裸字符串），65.2% 轮曾把 778/782
从「可恢复的单步失败」放大成「整任务崩」。按三轮 code review 的裁决，防线按
不变量层级收敛（docs/p7/code-review/2026-09-06-pr174-malformed-action-params-crash*.md）：

  1. choke point：client._coerce_named_action + _normalize_actions_list 在
     get_action 构造 result 前原地归一化（含顶层裸值/null/数字，round3 #3/#9），
     一处修复执行 / 参数校验 / _post_process / loop_detector / 历史持久化
  2. 历史双入口（round2 #3 + round3 #1/#5）：load_from_dict 归一化存量 JSONL
     （actions 列表 / 老格式 dict action / 老格式裸字符串）；rerun._skip_reason
     保留一行本地防御兜内存构造的 AgentHistoryList
  3. EventBus 全生命周期隔离（round2 #1/#2 + round3 #2/#6）：emit 与 close 均
     per-handler try/except，失败计数 + disable-after-N + close 汇总
  4. _step 死亡模式收口（round3 #4/#8）：_handle_step_error 自防护；n_steps
     递增单一所有者（_step finally，_finalize 尾部副本已删）
  5. 语义统一（round3 #3）：未注册名进澄清-重试梯子；裸 "done" 缺 text 重试
     耗尽后诚实失败（_action_done success 默认值按 text 有无）
  6. 投影逐条降级（round3 #7）：params 守卫维护 actions/interacted_element
     等长配对，catch-all 只兜真 DOM bug

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

from tree_walker.action_shape import coerce_named_action, name_of, normalize_actions_list
from tree_walker.agent.loop_detector import ActionLoopDetector
from tree_walker.agent.rerun import RerunMixin
from tree_walker.agent.step import StepPipeline
from tree_walker.agent.views import ActionResult, AgentHistory, AgentHistoryList, AgentState
from tree_walker.browser.views import BrowserStateSummary, SerializedDOMState
from tree_walker.config import LLMSettings, TruncationSettings
from tree_walker.llm.client import LLMClient
from tree_walker.tools.actions import Tools
from tree_walker.tools.registry import ActionRegistry, RegisteredAction


@pytest.fixture(autouse=True)
def _mock_anthropic_sdk(monkeypatch):
	# review5 #7：LLMClient() 在 setup 阶段急切构造真实 Anthropic SDK client——
	# 设了 socks:// 代理变量的机器上（httpx 只认 http/https/socks5[h]）直接
	# ValueError。测试反正立刻 MagicMock 覆盖 client，这里提前 mock 掉符号，
	# 使整套测试环境无关。
	monkeypatch.setattr("tree_walker.llm.client.Anthropic", MagicMock())


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
		normalize_actions_list(actions)
		assert actions == [{"name": "click", "params": {}}]

	def test_bare_string_action_coerced_to_named_action(self):
		actions = ["wait"]
		normalize_actions_list(actions)
		assert actions == [{"name": "wait", "params": {}}]

	def test_none_params_filled_without_warning(self, caplog):
		# params 缺失/None = setdefault 既有语义，不是畸形——不应告警
		import logging

		actions = [{"name": "click"}]
		with caplog.at_level(logging.WARNING):
			normalize_actions_list(actions)
		assert actions == [{"name": "click", "params": {}}]
		assert caplog.records == []

	def test_clean_actions_untouched(self):
		actions = [{"name": "click", "params": {"index": 5}}]
		normalize_actions_list(actions)
		assert actions == [{"name": "click", "params": {"index": 5}}]

	def test_in_place_mutation_covers_full_list(self):
		# get_action 随后取 actions_list[0] 作镜像——归一化必须发生在镜像之前且
		# 覆盖每个条目（review #1：下游逐条目重读原始数据）
		actions = [{"name": "input_text", "params": "x"}, "click"]
		normalize_actions_list(actions)
		assert all(isinstance(a, dict) and isinstance(a["params"], dict) for a in actions)
		assert actions[1]["name"] == "click"

	def test_warning_logs_type_not_value(self, caplog):
		# review #4 脱敏不变量：client 在归一化前已把占位符还原为真值，畸形
		# params 字符串可能含密钥——warning 只许记类型，不许记值
		import logging

		actions = [{"name": "input_text", "params": "<SECRET-TOKEN-1234>"}]
		with caplog.at_level(logging.WARNING, logger="tree_walker.action_shape"):
			normalize_actions_list(actions)
		assert any("params malformed (str)" in r.getMessage() for r in caplog.records)
		assert not any("<SECRET-TOKEN-1234>" in r.getMessage() for r in caplog.records)

	def test_list_scalar_position_aware_policy(self):
		# review6 #1/#2：位置感知——index 0 标量 = 镜像、进校验 → 诚实 done
		# （带 _honest_failure 标记，校验放行一次调用终止）；index > 0 标量 →
		# str() 强转命名动作（执行时可见 Unknown action 错误，而非触发 guard #1
		# 的静默截断/重放分叉）
		actions = [None, "click", 123]
		normalize_actions_list(actions)
		assert actions[0]["name"] == "done"
		assert actions[0]["params"]["success"] is False
		assert actions[0].get("_honest_failure") is True  # 校验放行标记
		assert actions[1] == {"name": "click", "params": {}}
		assert actions[2] == {"name": "123", "params": {}}

	def test_dict_with_invalid_name_gets_visible_string_name(self):
		# review6 #1：null/非字符串 name → str() 强转（可见错误），绝不伪造 done
		actions = [{"name": None, "params": {"index": 1}}, {"name": ["click"], "params": {}}]
		normalize_actions_list(actions)
		assert actions[0]["name"] == "None"
		assert actions[0]["params"] == {"index": 1}  # params 保留
		assert actions[1]["name"] == "['click']"
		# name 键缺失 → 不动（name_of 的旧单动作 "done" 缺省语义）
		actions2 = [{"params": {}}]
		normalize_actions_list(actions2)
		assert "name" not in actions2[0]


class TestNameOfSemantics:
	"""review5 #1：name_of 绝不把畸形 name 伪造为 done。"""

	def test_missing_name_key_defaults_done(self):
		# 旧单动作形态的既有缺省（master 的 .get("name", "done") 语义）
		assert name_of({"params": {}}) == "done"

	def test_null_name_passthrough_visible_error(self):
		# 显式 null/空串/非字符串 name 原样返回——下游 registry 未命中产生
		# 可见的 Unknown action 错误（master 行为），绝不静默伪造终止动作
		assert name_of({"name": None}) is None
		assert name_of({"name": ""}) == ""
		assert name_of({"name": 123}) == 123

	def test_bare_string_action_name(self):
		assert name_of("click") == "click"
		assert name_of("  click  ") == "click"
		assert name_of(None) is None


class TestAgentHistoryConstructionNormalization:
	"""review5 #10 主线：AgentHistory model_validator 构造收口——内存构造 /
	model_validate 不再绕过归一化（update_action_params 等消费方无需自带守卫）。"""

	def _mk(self, model_output: dict) -> AgentHistory:
		return AgentHistory(step_number=1, model_output=model_output, result=[])

	def test_in_memory_construction_normalizes(self):
		h = self._mk({"actions": [{"name": "click", "params": "561257"}, "wait"]})
		assert h.model_output["actions"] == [
			{"name": "click", "params": {}},
			{"name": "wait", "params": {}},
		]
		assert h.model_output["action"] == h.model_output["actions"][0]

	def test_in_memory_old_format_dict_action_normalized(self):
		h = self._mk({"action": {"name": "click", "params": "561257"}})
		assert h.model_output["action"] == {"name": "click", "params": {}}

	def test_update_action_params_on_malformed_history_no_crash(self):
		# review5 #10 的崩溃实例：此前 update_action_params 对 str params
		# setdefault 后赋值 → TypeError；构造收口后 model_output 已是干净形态
		hl = AgentHistoryList(history=[AgentHistory(
			step_number=1,
			model_output={"actions": [{"name": "click", "params": "561257"}]},
			result=[],
		)])
		hl.update_action_params(step_number=1, action_index=0, field="text", value="v")
		assert hl.history[0].model_output["actions"][0]["params"]["text"] == "v"

	def test_failed_construction_does_not_mutate_caller_dict(self):
		# review6 #8：mode='before' validator 曾就地改写调用方 dict——构造随后
		# 字段校验失败（result 非法）时腐蚀了失败构造器从未拥有的输入。
		# 拷贝归一化后：无论构造成败，调用方 dict 逐字节不变。
		import copy

		mo = {"actions": [{"name": "click", "params": "561257"}]}
		snapshot = copy.deepcopy(mo)
		with pytest.raises(Exception):
			AgentHistory(step_number=1, model_output=mo, result="not-a-list")
		assert mo == snapshot  # 失败构造不改写调用方输入

	def test_normalize_model_output_materializes_and_fixes(self):
		# review6 #9 主线：入口一次性归一化——单动作物化为列表、name 修复、镜像刷新
		from tree_walker.action_shape import normalize_model_output

		mo = {"action": {"name": None, "params": "561257"}}
		normalize_model_output(mo)
		assert mo["actions"] == [{"name": "None", "params": {}}]
		assert mo["action"] == mo["actions"][0]


# ── 2. 流水线端到端：归一化后走完 execute + post_process ────────────────


class TestPipelineStagesEndToEnd:
	"""review #1 的补口：原测试只孤立驱动 _execute_actions（CI 绿但生产崩——
	_post_process 重读原始 model_output）。这里按真实时序：client 归一化 →
	执行 → 后处理，全程不抛。"""

	@pytest.mark.asyncio
	async def test_malformed_output_flows_through_execute_and_post_process(self):
		agent = _loop_agent()
		model_output = _malformed_model_output()
		normalize_actions_list(model_output["actions"])  # get_action 的 choke point
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
	async def test_raw_bypass_form_handled_by_shared_accessors(self):
		# review4 #8：契约从「step 层崩（证明归一化归 client）」翻转为「共享
		# 访问器兜住旁路形态」——未经 choke point 的裸字符串动作以
		# name_of/params_of 解释（click + 空 params），不再 AttributeError。
		agent = _loop_agent()
		model_output = {"actions": ["click"]}
		results = await StepPipeline._execute_actions(agent, model_output, _browser_state())
		assert agent.tools.calls == [("click", {})]
		assert len(results) == 1

	@pytest.mark.asyncio
	async def test_mid_list_malformed_executes_visibly_not_silent_done(self):
		# review6 #2：中段畸形标量强转为 'None' 名、作为动作执行（真实 Tools
		# 会得到可见 Unknown action 错误并 guard #3 截断）——不再替换成
		# honest-done 触发 guard #1 的静默截断（无反馈、重放分叉）
		agent = _loop_agent()
		model_output = {"actions": [
			{"name": "click", "params": {}},
			None,
			{"name": "input_text", "params": {"index": 1, "text": "x"}},
		]}
		normalize_actions_list(model_output["actions"])
		await StepPipeline._execute_actions(agent, model_output, _browser_state())
		assert agent.tools.calls[0] == ("click", {})
		assert agent.tools.calls[1] == ("None", {})  # 可见执行，非静默 done 截断


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
		# review4 #4：降级可观测——计数入 state（run() 连续达阈值升级终止）
		assert agent.state.finalize_degraded_steps == 1

	@pytest.mark.asyncio
	async def test_finalize_degradation_counter_is_cumulative(self):
		# review6 #4/#5：累计制（只增不清零）——中途回复的降级不再被成功/空跑
		# 洗成 0（缺步的 run 与干净 run 不可区分）；快照语义 = history 缺失步数
		agent = _loop_agent()
		agent._prepare_context = AsyncMock(return_value=(_browser_state(), "state msg"))
		agent._get_next_action = AsyncMock(return_value={"actions": [{"name": "click", "params": {}}]})
		agent._execute_actions = AsyncMock(return_value=[ActionResult()])
		agent._finalize = AsyncMock(side_effect=[RuntimeError("boom"), None])
		await StepPipeline._step(agent)
		assert agent.state.finalize_degraded_steps == 1
		await StepPipeline._step(agent)  # 成功一步——累计值保持（不被洗掉）
		assert agent.state.finalize_degraded_steps == 1

	@pytest.mark.asyncio
	async def test_empty_finalize_run_does_not_reset_counter(self):
		# review5 #5 / review6 #5：pause/stop 提前返回的「空跑」finalize 不动计数
		agent = _loop_agent()
		agent._prepare_context = AsyncMock(return_value=(_browser_state(), "state msg"))
		agent._get_next_action = AsyncMock(return_value={"actions": [{"name": "click", "params": {}}]})
		agent._execute_actions = AsyncMock(return_value=[ActionResult()])
		agent._finalize = AsyncMock(side_effect=RuntimeError("bug"))
		await StepPipeline._step(agent)
		await StepPipeline._step(agent)
		assert agent.state.finalize_degraded_steps == 2
		agent._finalize = AsyncMock()  # 第三步 stopped 空跑——计数保持
		agent.state.stopped = True
		await StepPipeline._step(agent)
		assert agent.state.finalize_degraded_steps == 2

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
	async def test_finalize_string_params_degrades_per_entry(self):
		# review3 #7：畸形 params 直达投影（旁路场景）→ 逐条降级（本条 None），
		# 维护 actions/interacted_element 等长配对——catch-all 只兜真 DOM bug
		agent = _ProjectionAgent()
		model_output = {"actions": [{"name": "click", "params": "561857"}]}
		await StepPipeline._finalize(
			agent, _browser_state(selector_map={1: object()}), model_output, [ActionResult()]
		)
		assert len(agent.history.history) == 1
		assert agent.history.history[-1].interacted_element == [None]


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


# ── 7.（已迁出）EventBus 隔离/熔断/close 测试见 tests/test_event_bus.py（review4 #9）──


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

	def test_custom_llm_str_params_survives_validation_layer(self):
		# review5 #4：注入/自定义 LLM 绕过 client choke point 时（llm 参数只是
		# 未强制的注解），str params 曾在 _flatten_params 的 params.items() 崩
		# ——校验层换 params_of 后优雅给缺参反馈。
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
		response = {"action": {"name": "click", "params": "561257"}}  # 未经归一化
		err = StepPipeline._validate_action_params(agent, response)
		assert err is not None
		assert "index" in err


# ── 9. round3 补口：旧格式形态 / 内存构造历史 / 错误处理器自防护 / 语义统一 ──


class TestRound3EdgeShapes:
	def test_old_format_dict_action_string_params_normalized(self):
		# review3 #1：老格式单动作 {"action": {"name": "click", "params": "561857"}}
		# 此前被 elif 链跳过（只处理了 str 形态）——issue #173 的原始 replay 崩溃
		# 仍可构造
		data = {"history": [{
			"step_number": 1,
			"model_output": {"next_goal": "g", "action": {"name": "click", "params": "561857"}},
			"result": [], "state_summary": {}, "interacted_element": None,
		}]}
		hl = AgentHistoryList.load_from_dict(data)
		assert hl.history[0].model_output["action"] == {"name": "click", "params": {}}

	def test_old_format_dict_action_flows_through_skip_reason(self):
		data = {"history": [{
			"step_number": 1,
			"model_output": {"next_goal": "g", "action": {"name": "click", "params": "561857"}},
			"result": [], "state_summary": {}, "interacted_element": None,
		}]}
		hl = AgentHistoryList.load_from_dict(data)
		fake = RerunMixin()
		fake._is_redundant_retry_step = lambda *a, **k: False
		reason = fake._skip_reason(
			hl.history[0], previous_item=None, previous_succeeded=True, skip_failures=False,
		)
		assert reason is not None and "无 index" in reason

	def test_in_memory_history_guarded_locally(self):
		# review3 #5：rerun_history() 也接受内存/model_validate 构造的
		# AgentHistoryList（未经 load_from_dict choke point）——本地一行防御兜住
		item = SimpleNamespace(
			model_output={"actions": [{"name": "click", "params": "561857"}]},
			result=[ActionResult()], interacted_element=None, step_number=1,
		)
		fake = RerunMixin()
		fake._is_redundant_retry_step = lambda *a, **k: False
		reason = fake._skip_reason(
			item, previous_item=None, previous_succeeded=True, skip_failures=False,
		)  # 不抛；按无 index 处理
		assert reason is not None and "无 index" in reason

	@pytest.mark.asyncio
	async def test_step_error_handler_failure_does_not_kill_step(self):
		# review3 #4：_handle_step_error 自身抛错（如半死浏览器上 reconnect 再抛）
		# 降级为日志——_step 返回 False 而非穿出杀死 run
		agent = _loop_agent()
		agent._prepare_context = AsyncMock(return_value=(_browser_state(), "state msg"))
		agent._get_next_action = AsyncMock(return_value={"actions": [{"name": "click", "params": {}}]})
		agent._execute_actions = AsyncMock(side_effect=ConnectionError("CDP lost"))
		agent._handle_step_error = AsyncMock(side_effect=TypeError("half-dead browser teardown"))
		agent._finalize = AsyncMock()
		assert await StepPipeline._step(agent) is False  # 不抛


class TestMalformedActionSemantics:
	"""review3 #3 + review4 #1/#2/#5：畸形 action 的终止/重试语义。

	分流规则：裸字符串（像样的动作名）→ 命名动作进重试梯子；null/数字/布尔
	（无可修的名字）→ 诚实失败 done 直接终止（一次调用）；裸 "done" → 命名动作
	进重试梯子（要 text），重试耗尽后 _action_done 的 success 默认值兜底诚实失败。
	"""

	def _client(self) -> LLMClient:
		client = LLMClient(LLMSettings(api_key="test-key"))
		client.client = MagicMock()
		return client

	async def _get_with_action(self, client, raw_action) -> dict:
		client.client.messages.create = MagicMock(return_value=_make_tool_use_response({
			"evaluation_previous_goal": "", "memory": "", "next_goal": "g",
			"action": raw_action,
		}))
		return await client.get_action(
			system_prompt="", messages=[],
			tool_schema={"name": "agent_response", "input_schema": {"type": "object"}},
		)

	@pytest.mark.asyncio
	async def test_null_action_honest_done_termination(self):
		# review4 #2 / review6 #3：无可修的名字——诚实失败 done 一次调用终止
		# （_honest_failure 标记使校验放行，variant B 也不烧重试）
		result = await self._get_with_action(self._client(), None)
		[action] = result["actions"]
		assert action["name"] == "done"
		assert action["params"]["success"] is False
		assert action["_honest_failure"] is True

	@pytest.mark.asyncio
	async def test_numeric_action_honest_done_termination(self):
		result = await self._get_with_action(self._client(), 123)
		[action] = result["actions"]
		assert action["name"] == "done"
		assert action["params"]["success"] is False

	@pytest.mark.asyncio
	async def test_bare_done_goes_through_retry_ladder(self):
		# review4 #1/#5：裸 "done"（真动作名）进重试梯子要 text——模型可修；
		# 耗尽后 _action_done 的 success 默认值（text/data 任一存在才 True）兜底
		result = await self._get_with_action(self._client(), "done")
		assert result["actions"] == [{"name": "done", "params": {}}]

	def test_unregistered_name_gets_retry_feedback(self):
		# 未注册名进澄清-重试梯子（此前 validation 返 None → 执行失败计 failure）
		agent = MagicMock()
		agent.tools = MagicMock()
		agent.tools.registry = _make_registry("click")  # 没有 "click the login button"
		err = StepPipeline._validate_action_params(
			agent, {"action": {"name": "click the login button", "params": {}}},
		)
		assert err is not None
		assert "Unknown action" in err

	def test_bare_done_missing_text_gets_retry_feedback(self):
		# 裸 "done" → {"params":{}} → 真实 DoneParams 校验给出「text 缺字段」
		# 反馈（重试梯子输入；模型补 text 后正常终止）
		from tree_walker.tools.models import DoneParams

		agent = MagicMock()
		agent.tools = MagicMock()
		agent.tools._flatten_params = lambda params, name: Tools._flatten_params(
			MagicMock(), params, name,
		)
		agent.tools.registry = ActionRegistry()
		agent.tools.registry.actions["done"] = RegisteredAction(
			name="done", description="done", param_model=DoneParams,
			handler=MagicMock(), terminates_sequence=False,
		)
		err = StepPipeline._validate_action_params(
			agent, {"action": {"name": "done", "params": {}}},
		)
		assert err is not None
		assert "text" in err


# ── 10. review4：run() 对 _finalize 持续降级的升级终止（#4）──────────────


class TestRunEscalationOnFinalizeDegradation:
	@pytest.mark.asyncio
	async def test_run_aborts_after_three_consecutive_degraded_steps(self):
		# 确定性 _finalize bug 会让 run 报成功但 history 残缺（最坏是 done 步
		# 不进 history）——连续 3 步降级即中止并显形，而非静默跑完
		from tree_walker.agent.agent import Agent
		from tree_walker.config import AgentSettings

		browser = MagicMock()
		browser.start = AsyncMock()
		browser.stop = AsyncMock()
		browser._settings = MagicMock(wait_between_actions=0.0)
		agent = Agent(
			task="t", llm=MagicMock(), browser=browser,
			settings=AgentSettings(max_steps=10, enable_planning=False),
		)

		steps = {"n": 0}

		async def degraded_step():
			steps["n"] += 1
			agent.state.n_steps += 1  # 模拟 finally 的递增
			agent.state.finalize_degraded_steps += 1
			return False

		agent._step = degraded_step
		await agent.run()
		assert steps["n"] == 3  # 第 3 次降级后升级终止，而非跑满 max_steps

	@pytest.mark.asyncio
	async def test_done_step_degradation_still_escalates_and_is_visible(self):
		# review5 #3：升级检查在 done-break **之前**——降级步恰是 done 步时
		# （其 history 正是残缺的那个）同样触发；降级计数落到返回的 history
		# 上（AgentHistoryList.finalize_degraded_steps），调用方可区分。
		from tree_walker.agent.agent import Agent
		from tree_walker.config import AgentSettings

		browser = MagicMock()
		browser.start = AsyncMock()
		browser.stop = AsyncMock()
		browser._settings = MagicMock(wait_between_actions=0.0)
		agent = Agent(
			task="t", llm=MagicMock(), browser=browser,
			settings=AgentSettings(max_steps=10, enable_planning=False),
		)

		steps = {"n": 0}

		async def degraded_done_step():
			steps["n"] += 1
			agent.state.n_steps += 1
			agent.state.finalize_degraded_steps += 1
			# 第 3 步是 done——恰在升级线上的「降级 done 步」：若升级检查在
			# done-break 之后（review5 #3 的旧顺序），此处直接 break、永不触发
			return steps["n"] >= 3

		agent._step = degraded_done_step
		history = await agent.run()
		assert steps["n"] == 3
		assert history.finalize_degraded_steps == 3
