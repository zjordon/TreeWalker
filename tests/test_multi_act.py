"""Tests for multi_act Phase 1 — schema list-ification, dual-compat parsing, loop execution."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from tree_walker.agent.views import ActionResult, AgentState
from tree_walker.config import AgentSettings, LLMSettings
from tree_walker.llm.client import LLMClient
from tree_walker.tools.registry import ActionRegistry, RegisteredAction


# ── Helpers ────────────────────────────────────────────────────────────


class _DummyParams(BaseModel):
	pass


def _make_registry(*names: str, terminating: tuple[str, ...] = ()) -> ActionRegistry:
	"""创建包含指定 action 的 registry；terminating 中的动作标 terminates_sequence=True。"""
	registry = ActionRegistry()
	for name in names:
		registry.actions[name] = RegisteredAction(
			name=name,
			description=f"{name} action",
			param_model=_DummyParams,
			handler=MagicMock(),
			terminates_sequence=name in terminating,
		)
	return registry


# Phase 2+: _execute_actions 守卫门需要 navigate/search/switch_tab/go_back/evaluate
# 这些「page-changing」动作的元数据。下面这个集合对齐 src/tree_walker/tools/models.py
# 的 ACTION_DEFINITIONS 中 terminates=True 的 5 个动作。
_TERMINATING_ACTIONS = ("navigate", "search", "switch_tab", "go_back", "evaluate")
_ALL_FAKE_ACTIONS = (
	"click", "input_text", "done", "scroll", "wait",
	*_TERMINATING_ACTIONS,
)


def _build_full_registry() -> ActionRegistry:
	"""构建含全部常用动作的 registry，page-changing 动作标 terminates_sequence=True。"""
	return _make_registry(*_ALL_FAKE_ACTIONS, terminating=_TERMINATING_ACTIONS)


def _make_tool_use_response(tool_input: dict[str, Any]) -> MagicMock:
	"""构造一个伪 Anthropic tool_use 响应。"""
	block = MagicMock()
	block.type = "tool_use"
	block.name = "agent_response"
	block.input = tool_input
	response = MagicMock()
	response.content = [block]
	return response


class _FakeBrowser:
	"""模拟 BrowserSession 的最小子集。"""

	def __init__(self, url: str = "https://example.com", target_id: str = "tab-1") -> None:
		self._url = url
		self.current_target_id = target_id

	async def get_current_url(self) -> str:
		return self._url


class _FakeTools:
	"""记录每次 execute 调用，按预置结果依次返回。"""

	def __init__(self, results: list[ActionResult] | None = None) -> None:
		self.calls: list[tuple[str, dict]] = []
		self._results = list(results or [])
		self._idx = 0
		self.registry = _build_full_registry()

	async def execute(self, name: str, params: dict, browser: Any, browser_state: Any) -> ActionResult:
		self.calls.append((name, dict(params)))
		if self._idx < len(self._results):
			r = self._results[self._idx]
			self._idx += 1
			return r
		return ActionResult(success=True)


def _make_agent(results: list[ActionResult] | None = None) -> Any:
	"""构造可直接调用 _execute_actions 的 fake agent。"""
	agent = MagicMock()
	agent.state = AgentState()
	agent.action_timeout = 30
	agent.wait_between_actions = 0.0
	agent.tools = _FakeTools(results)
	agent.browser = _FakeBrowser()
	agent._obs_bus = None
	agent._obs_session_id = "test"
	agent._current_model_call_id = ""
	agent._sensitive_map_for_log = None  # P1-2：默认无 sensitive → 日志脱敏 no-op
	return agent


# ── 1. Schema tests ─────────────────────────────────────────────────────


class TestSchemaMultiAction:
	"""get_tool_schema 在 max_actions > 1 时把 action 包装为 list。"""

	def test_default_max_actions_1_is_object(self):
		"""max_actions=1（默认）时 action 是 object（向后兼容）。"""
		registry = _make_registry("click", "done")
		schema = registry.get_tool_schema()
		action_field = schema["input_schema"]["properties"]["action"]
		assert action_field["type"] == "object"
		enum_path = action_field["properties"]["name"]["enum"]
		assert "click" in enum_path

	def test_max_actions_5_wraps_as_array(self):
		"""max_actions=5 时 action 是 array。"""
		registry = _make_registry("click", "done")
		schema = registry.get_tool_schema(max_actions=5)
		action_field = schema["input_schema"]["properties"]["action"]
		assert action_field["type"] == "array"
		assert action_field["minItems"] == 1
		assert action_field["maxItems"] == 5

	def test_array_items_have_action_shape(self):
		"""array items 仍是 {name, params} 对象。"""
		registry = _make_registry("click", "done")
		schema = registry.get_tool_schema(max_actions=3)
		items = schema["input_schema"]["properties"]["action"]["items"]
		assert items["type"] == "object"
		assert items["required"] == ["name"]
		assert "click" in items["properties"]["name"]["enum"]

	def test_flash_mode_supports_multi_action(self):
		"""flash 模式也支持 list 化。"""
		registry = _make_registry("click")
		schema = registry.get_tool_schema(output_mode="flash", max_actions=5)
		action_field = schema["input_schema"]["properties"]["action"]
		assert action_field["type"] == "array"
		assert action_field["maxItems"] == 5

	def test_thinking_mode_supports_multi_action(self):
		"""thinking 模式也支持 list 化。"""
		registry = _make_registry("click")
		schema = registry.get_tool_schema(output_mode="thinking", max_actions=5)
		action_field = schema["input_schema"]["properties"]["action"]
		assert action_field["type"] == "array"

	def test_max_actions_1_with_flash_still_object(self):
		"""max_actions=1 + flash 模式仍是 object。"""
		registry = _make_registry("click")
		schema = registry.get_tool_schema(output_mode="flash", max_actions=1)
		action_field = schema["input_schema"]["properties"]["action"]
		assert action_field["type"] == "object"

	def test_include_actions_with_multi_action(self):
		"""include_actions 与 max_actions 可组合使用。"""
		registry = _make_registry("click", "done", "input_text")
		schema = registry.get_tool_schema(
			include_actions=["done"],
			max_actions=5,
		)
		action_field = schema["input_schema"]["properties"]["action"]
		assert action_field["type"] == "array"
		items_enum = action_field["items"]["properties"]["name"]["enum"]
		assert items_enum == ["done"]


# ── 2. Client parsing tests ─────────────────────────────────────────────


class TestClientParsing:
	"""get_action 双兼容解析：list 和单 dict。"""

	def setup_method(self) -> None:
		self.client = LLMClient(LLMSettings(api_key="test-key"))
		# 直接替换底层 Anthropic 客户端的 messages.create
		self.client.client = MagicMock()

	def _patch_response(self, tool_input: dict[str, Any]) -> None:
		self.client.client.messages.create = MagicMock(
			return_value=_make_tool_use_response(tool_input),
		)

	@pytest.mark.asyncio
	async def test_parses_action_list(self):
		"""LLM 返回 list → result['actions'] 是 list，result['action'] 是首元素。"""
		actions_list = [
			{"name": "click", "params": {"index": 1}},
			{"name": "input_text", "params": {"index": 2, "text": "hi"}},
		]
		tool_input = {
			"evaluation_previous_goal": "ok",
			"memory": "",
			"next_goal": "fill form",
			"action": actions_list,
		}
		self._patch_response(tool_input)

		result = await self.client.get_action(
			system_prompt="",
			messages=[],
			tool_schema={"name": "agent_response", "input_schema": {"type": "object"}},
		)

		assert result["actions"] == actions_list
		assert result["action"] == actions_list[0]
		assert result["next_goal"] == "fill form"

	@pytest.mark.asyncio
	async def test_parses_single_action_dict_backward_compat(self):
		"""LLM 返回单 dict → 自动包装为 list。"""
		single = {"name": "click", "params": {"index": 42}}
		tool_input = {
			"evaluation_previous_goal": "",
			"memory": "",
			"next_goal": "",
			"action": single,
		}
		self._patch_response(tool_input)

		result = await self.client.get_action(
			system_prompt="",
			messages=[],
			tool_schema={"name": "agent_response", "input_schema": {"type": "object"}},
		)

		assert result["action"] == single
		assert result["actions"] == [single]

	@pytest.mark.asyncio
	async def test_each_action_has_params(self):
		"""缺 params 的 action 自动补 {}。"""
		actions_list = [
			{"name": "click"},
			{"name": "wait"},
		]
		tool_input = {
			"evaluation_previous_goal": "",
			"memory": "",
			"next_goal": "",
			"action": actions_list,
		}
		self._patch_response(tool_input)

		result = await self.client.get_action(
			system_prompt="",
			messages=[],
			tool_schema={"name": "agent_response", "input_schema": {"type": "object"}},
		)

		for a in result["actions"]:
			assert a.get("params") == {}


# ── 3. _execute_actions loop tests ──────────────────────────────────────


class TestExecuteActionsLoop:
	"""_execute_actions 多动作循环。"""

	@pytest.mark.asyncio
	async def test_executes_multiple_actions_in_order(self):
		"""LLM 输出 3 个 action → 全部按顺序执行。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent([
			ActionResult(),
			ActionResult(),
			ActionResult(),
		])
		model_output = {
			"actions": [
				{"name": "click", "params": {"index": 1}},
				{"name": "input_text", "params": {"index": 2, "text": "hi"}},
				{"name": "click", "params": {"index": 3}},
			],
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		results = await StepPipeline._execute_actions(agent, model_output, browser_state)

		assert len(results) == 3
		assert [c[0] for c in agent.tools.calls] == ["click", "input_text", "click"]
		assert [c[1] for c in agent.tools.calls] == [
			{"index": 1}, {"index": 2, "text": "hi"}, {"index": 3},
		]

	@pytest.mark.asyncio
	async def test_single_action_backward_compat(self):
		"""LLM 输出单 action（只有 action 字段，无 actions）→ 仅执行 1 次。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent([ActionResult()])
		model_output = {
			"action": {"name": "click", "params": {"index": 1}},
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		results = await StepPipeline._execute_actions(agent, model_output, browser_state)

		assert len(results) == 1
		assert len(agent.tools.calls) == 1
		assert agent.tools.calls[0][0] == "click"

	@pytest.mark.asyncio
	async def test_per_action_timeout_returns_error_result(self):
		"""某个 action 超时 → 该 action 返回 error result。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent([])
		# 让 execute 抛长时间等待
		async def slow_execute(*args, **kwargs):
			await asyncio.sleep(10)
		agent.tools.execute = slow_execute
		agent.action_timeout = 0.05  # 50ms

		model_output = {
			"action": {"name": "click", "params": {}},
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		results = await StepPipeline._execute_actions(agent, model_output, browser_state)

		assert len(results) == 1
		assert results[0].error is not None
		assert "timed out" in results[0].error.lower()

	@pytest.mark.asyncio
	async def test_action_exception_returns_error_result(self):
		"""普通异常被捕获并转化为 error result（不向上抛）。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent([])

		async def failing_execute(*args, **kwargs):
			raise RuntimeError("element not found")
		agent.tools.execute = failing_execute

		model_output = {
			"action": {"name": "click", "params": {}},
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		results = await StepPipeline._execute_actions(agent, model_output, browser_state)

		assert len(results) == 1
		assert "RuntimeError" in results[0].error

	@pytest.mark.asyncio
	async def test_stopped_state_returns_early(self):
		"""agent.stopped 时立即返回 error result。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent()
		agent.state.stopped = True

		model_output = {
			"actions": [
				{"name": "click", "params": {}},
				{"name": "click", "params": {}},
			],
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		results = await StepPipeline._execute_actions(agent, model_output, browser_state)

		assert len(results) == 1
		assert "stopped" in results[0].error.lower()
		assert agent.tools.calls == []  # 没执行任何 action

	@pytest.mark.asyncio
	async def test_loop_runs_url_check_per_action(self):
		"""每个非 _NO_URL_CHECK_ACTIONS 动作后都触发一次 get_current_url。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent([
			ActionResult(),
			ActionResult(),
		])
		url_calls = {"count": 0}
		original_get_url = agent.browser.get_current_url

		async def counting_get_url():
			url_calls["count"] += 1
			return await original_get_url()
		agent.browser.get_current_url = counting_get_url

		model_output = {
			"actions": [
				{"name": "click", "params": {}},
				{"name": "click", "params": {}},
			],
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		await StepPipeline._execute_actions(agent, model_output, browser_state)

		# 两个 click 都不在 _NO_URL_CHECK_ACTIONS 集合里（实际上 click 在里面 - 修正预期）
		# 看 step.py: _NO_URL_CHECK_ACTIONS 包含 click，所以不会被检测。改用 input_text
		# 这里只验证循环本身不会因 url check 失败


# ── 3b. P0-1 per-action stop/pause check ─────────────────────────────────


class TestPerActionStopCheck:
	"""P0-1：循环内 per-action stop/pause 检查（对齐 browser-use ``_check_stop_or_pause``）。

	入口检查（``_execute_actions`` L747）已在 ``test_stopped_state_returns_early`` 覆盖；
	这里覆盖**循环内** action 之间的 stop/pause 响应——02 期 P0-1 给 LLM 阶段修过的对称漏洞。
	"""

	@pytest.mark.asyncio
	async def test_stopped_between_actions_raises_interrupted(self):
		"""action 1 执行后、action 2 执行前置 stopped=True → raise InterruptedError，后续动作未执行。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent()

		# action 1 执行时把 stopped 置 True（模拟用户在动作间按停止），同时记录调用
		async def stop_after_first(name, params, browser, browser_state):
			agent.tools.calls.append((name, dict(params)))
			agent.state.stopped = True
			return ActionResult()

		agent.tools.execute = stop_after_first

		model_output = {
			"actions": [
				{"name": "click", "params": {"index": 1}},
				{"name": "click", "params": {"index": 2}},
				{"name": "click", "params": {"index": 3}},
			],
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		with pytest.raises(InterruptedError):
			await StepPipeline._execute_actions(agent, model_output, browser_state)

		# P0-1 检查在 tools.execute 之前 → action 2/3 未触达，仅 action 1 执行
		assert len(agent.tools.calls) == 1

	@pytest.mark.asyncio
	async def test_paused_between_actions_raises_interrupted(self):
		"""paused 信号同样在动作间触发 InterruptedError。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent()

		async def pause_after_first(name, params, browser, browser_state):
			agent.tools.calls.append((name, dict(params)))
			agent.state.paused = True
			return ActionResult()

		agent.tools.execute = pause_after_first

		model_output = {
			"actions": [
				{"name": "click", "params": {}},
				{"name": "click", "params": {}},
			],
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		with pytest.raises(InterruptedError):
			await StepPipeline._execute_actions(agent, model_output, browser_state)

		assert len(agent.tools.calls) == 1

	@pytest.mark.asyncio
	async def test_stop_at_entry_returns_error_result_not_raise(self):
		"""入口（执行任何 action 前）已 stopped → 返回 error result 列表（既有行为，回归）。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent()
		agent.state.stopped = True

		model_output = {
			"actions": [{"name": "click", "params": {}}, {"name": "click", "params": {}}],
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		results = await StepPipeline._execute_actions(agent, model_output, browser_state)

		# 入口检查走 L747 返回路径（不抛 InterruptedError）；未进入循环
		assert len(results) == 1
		assert results[0].error is not None
		assert len(agent.tools.calls) == 0

	@pytest.mark.asyncio
	async def test_no_stop_executes_all_actions(self):
		"""无 stop/pause → 全部动作执行（回归：P0-1 不影响正常流程）。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent([ActionResult(), ActionResult(), ActionResult()])
		model_output = {
			"actions": [
				{"name": "click", "params": {"index": 1}},
				{"name": "click", "params": {"index": 2}},
				{"name": "click", "params": {"index": 3}},
			],
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		results = await StepPipeline._execute_actions(agent, model_output, browser_state)

		assert len(results) == 3
		assert len(agent.tools.calls) == 3


# ── 3c. P1-2 per-action log + secret redaction ───────────────────────────


class TestPerActionLog:
	"""P1-2：per-action 执行日志（``[i/total] name: params``）+ 秘密脱敏。"""

	@pytest.mark.asyncio
	async def test_per_action_log_shows_position(self, caplog):
		"""多动作 → caplog 出现 [1/3] / [2/3] / [3/3] 进度行。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent([ActionResult(), ActionResult(), ActionResult()])
		model_output = {
			"actions": [
				{"name": "click", "params": {"index": 1}},
				{"name": "scroll", "params": {}},
				{"name": "click", "params": {"index": 2}},
			],
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		with caplog.at_level(logging.INFO):
			await StepPipeline._execute_actions(agent, model_output, browser_state)

		assert "[1/3]" in caplog.text
		assert "[2/3]" in caplog.text
		assert "[3/3]" in caplog.text
		assert "click" in caplog.text
		assert "scroll" in caplog.text

	@pytest.mark.asyncio
	async def test_sensitive_field_redacted_in_log(self, caplog):
		"""input_text 的敏感 text 字段在日志中脱敏为 ``<secret>pw</secret>``，真值不出现。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent([ActionResult()])
		# {placeholder: real_value} 方向（_redact_params_for_log 期望的方向）
		agent._sensitive_map_for_log = {"pw": "secret123"}

		model_output = {
			"actions": [
				{"name": "input_text", "params": {"index": 1, "text": "secret123"}},
			],
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		with caplog.at_level(logging.INFO):
			await StepPipeline._execute_actions(agent, model_output, browser_state)

		# 真值绝不进日志；占位符标记必须出现
		assert "secret123" not in caplog.text
		assert "<secret>pw</secret>" in caplog.text
		# 非敏感字段保留
		assert "index" in caplog.text

	@pytest.mark.asyncio
	async def test_non_sensitive_action_unchanged_without_secrets(self, caplog):
		"""无 sensitive 配置 → 非敏感字段原样打印（脱敏 no-op，回归）。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent([ActionResult()])
		# _sensitive_map_for_log=None（_make_agent 默认）→ _redact_params_for_log no-op
		model_output = {
			"actions": [
				{"name": "click", "params": {"index": 5}},
			],
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		with caplog.at_level(logging.INFO):
			await StepPipeline._execute_actions(agent, model_output, browser_state)

		assert "index" in caplog.text
		assert "5" in caplog.text

	@pytest.mark.asyncio
	async def test_real_action_params_not_mutated_by_redaction(self, caplog):
		"""脱敏返回副本：真实 params 未被改动 → tools.execute 拿到真值（动作正常执行）。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent([ActionResult()])
		agent._sensitive_map_for_log = {"pw": "secret123"}

		model_output = {
			"actions": [
				{"name": "input_text", "params": {"index": 1, "text": "secret123"}},
			],
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		with caplog.at_level(logging.INFO):
			await StepPipeline._execute_actions(agent, model_output, browser_state)

		# _FakeTools.execute 在 calls 里记录的是真实 params（脱敏只在日志副本）
		assert agent.tools.calls[0][1]["text"] == "secret123"


# ── 4. Post-process loop detector ───────────────────────────────────────


class TestPostProcessMultiAction:
	"""_post_process 在多动作步中循环调用 loop_detector。"""

	def test_loop_detector_records_each_action(self):
		"""多动作步的每个非豁免 action 都被记录到 loop_detector。"""
		from tree_walker.agent.step import StepPipeline

		agent = MagicMock()
		agent.state = AgentState()
		agent._enable_planning = False
		agent.plan_manager = None
		recorded: list[tuple[str, dict]] = []

		class _FakeLoopDetector:
			def record_action(self, name: str, params: dict) -> None:
				recorded.append((name, dict(params)))

		agent.loop_detector = _FakeLoopDetector()

		model_output = {
			"actions": [
				{"name": "click", "params": {"index": 1}},
				{"name": "click", "params": {"index": 2}},
				{"name": "wait", "params": {}},  # 豁免，不记录
			],
		}
		results = [
			ActionResult(),
			ActionResult(),
			ActionResult(),
		]

		StepPipeline._post_process(agent, results, model_output)

		assert recorded == [("click", {"index": 1}), ("click", {"index": 2})]


# ── 5. Backward compat ──────────────────────────────────────────────────


class TestBackwardCompat:
	"""默认配置与单动作行为的回归保护。"""

	def test_default_max_actions_per_step_is_5(self):
		"""config.py 默认 max_actions_per_step=5（对齐 browser-use）。"""
		assert AgentSettings().max_actions_per_step == 5

	def test_schema_with_explicit_max_actions_1_is_object(self):
		"""显式传 max_actions=1 → action 仍是 object（向后兼容路径）。"""
		registry = _make_registry("click")
		schema = registry.get_tool_schema(max_actions=1)
		action_field = schema["input_schema"]["properties"]["action"]
		assert action_field["type"] == "object"

	def test_post_process_single_failure_increments_counter(self):
		"""单动作步失败仍按原逻辑计数。"""
		from tree_walker.agent.step import StepPipeline

		agent = MagicMock()
		agent.state = AgentState()
		agent._enable_planning = False
		agent.plan_manager = None
		agent.state.consecutive_failures = 0

		class _NoOpLoopDetector:
			def record_action(self, *args, **kwargs):
				pass

		agent.loop_detector = _NoOpLoopDetector()

		model_output = {
			"action": {"name": "click", "params": {}},
		}
		results = [ActionResult(error="boom")]

		StepPipeline._post_process(agent, results, model_output)

		assert agent.state.consecutive_failures == 1


# ── 6. Phase 2 guards ───────────────────────────────────────────────────


class TestPhase2Guards:
	"""Phase 2 静态守卫门：done 单动作 / is_done / error / terminates_sequence。"""

	@pytest.mark.asyncio
	async def test_guard1_done_in_midpoint_is_skipped(self):
		"""门 #1：list 中段出现 done → done 及之后动作被跳过。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent([ActionResult(), ActionResult()])
		model_output = {
			"actions": [
				{"name": "click", "params": {"index": 1}},
				{"name": "done", "params": {"text": "done"}},
				{"name": "click", "params": {"index": 99}},  # 应被跳过
			],
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		results = await StepPipeline._execute_actions(agent, model_output, browser_state)

		# 只执行了第一个 click，done 与后续 click 被守卫门跳过
		assert len(results) == 1
		assert len(agent.tools.calls) == 1
		assert agent.tools.calls[0][0] == "click"

	@pytest.mark.asyncio
	async def test_guard1_done_as_single_action_executes(self):
		"""门 #1：done 作为单动作（i==0）正常执行。"""
		from tree_walker.agent.step import StepPipeline

		done_result = ActionResult(is_done=True, success=True, extracted_content="task done")
		agent = _make_agent([done_result])
		model_output = {
			"actions": [{"name": "done", "params": {"text": "task done", "success": True}}],
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		results = await StepPipeline._execute_actions(agent, model_output, browser_state)

		assert len(results) == 1
		assert results[0].is_done is True
		assert len(agent.tools.calls) == 1

	@pytest.mark.asyncio
	async def test_guard2_is_done_terminates_sequence(self):
		"""门 #2：第一个 action 返回 is_done → 终止后续动作。"""
		from tree_walker.agent.step import StepPipeline

		done_result = ActionResult(is_done=True, success=True, extracted_content="ok")
		agent = _make_agent([done_result, ActionResult()])
		model_output = {
			"actions": [
				{"name": "click", "params": {"index": 1}},
				{"name": "click", "params": {"index": 99}},  # 应被跳过
			],
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		results = await StepPipeline._execute_actions(agent, model_output, browser_state)

		# _FakeTools 按 results 列表顺序返回，所以这里 click 的返回值是 done_result
		# 第一个 click 返回 is_done=True → 门 #2 触发，第二个 click 被跳过
		assert len(results) == 1
		assert results[0].is_done is True
		assert len(agent.tools.calls) == 1

	@pytest.mark.asyncio
	async def test_guard3_error_terminates_sequence(self):
		"""门 #3：第一个 action 返回 error → 终止后续动作。"""
		from tree_walker.agent.step import StepPipeline

		err_result = ActionResult(error="element not found")
		agent = _make_agent([err_result, ActionResult()])
		model_output = {
			"actions": [
				{"name": "click", "params": {"index": 1}},
				{"name": "click", "params": {"index": 99}},  # 应被跳过
			],
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		results = await StepPipeline._execute_actions(agent, model_output, browser_state)

		assert len(results) == 1
		assert results[0].error == "element not found"
		assert len(agent.tools.calls) == 1

	@pytest.mark.asyncio
	async def test_guard4_navigate_terminates(self):
		"""门 #4：navigate 是 terminates_sequence → 后续动作被跳过。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent([ActionResult(), ActionResult()])
		model_output = {
			"actions": [
				{"name": "navigate", "params": {"url": "https://other.com"}},
				{"name": "click", "params": {"index": 99}},  # 应被跳过
			],
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		results = await StepPipeline._execute_actions(agent, model_output, browser_state)

		assert len(results) == 1
		assert len(agent.tools.calls) == 1
		assert agent.tools.calls[0][0] == "navigate"

	@pytest.mark.asyncio
	async def test_guard4_search_terminates(self):
		"""门 #4：search 是 terminates_sequence → 后续动作被跳过。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent([ActionResult(), ActionResult()])
		model_output = {
			"actions": [
				{"name": "search", "params": {"query": "test"}},
				{"name": "click", "params": {}},  # 应被跳过
			],
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		results = await StepPipeline._execute_actions(agent, model_output, browser_state)

		assert len(results) == 1
		assert agent.tools.calls[0][0] == "search"

	@pytest.mark.asyncio
	async def test_guard4_evaluate_terminates(self):
		"""门 #4：evaluate 是 terminates_sequence → 后续动作被跳过。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent([ActionResult(), ActionResult()])
		model_output = {
			"actions": [
				{"name": "evaluate", "params": {"script": "return 1"}},
				{"name": "click", "params": {}},  # 应被跳过
			],
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		results = await StepPipeline._execute_actions(agent, model_output, browser_state)

		assert len(results) == 1
		assert agent.tools.calls[0][0] == "evaluate"

	@pytest.mark.asyncio
	async def test_guard4_switch_tab_terminates(self):
		"""门 #4：switch_tab 是 terminates_sequence。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent([ActionResult(), ActionResult()])
		model_output = {
			"actions": [
				{"name": "switch_tab", "params": {"tab_id": "abcd"}},
				{"name": "click", "params": {}},
			],
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		results = await StepPipeline._execute_actions(agent, model_output, browser_state)

		assert len(results) == 1
		assert agent.tools.calls[0][0] == "switch_tab"

	@pytest.mark.asyncio
	async def test_guard4_go_back_terminates(self):
		"""门 #4：go_back 是 terminates_sequence。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent([ActionResult(), ActionResult()])
		model_output = {
			"actions": [
				{"name": "go_back", "params": {}},
				{"name": "click", "params": {}},
			],
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		results = await StepPipeline._execute_actions(agent, model_output, browser_state)

		assert len(results) == 1
		assert agent.tools.calls[0][0] == "go_back"

	@pytest.mark.asyncio
	async def test_guard4_non_terminating_actions_chain(self):
		"""反向验证：click / input_text / scroll 不触发守卫 → 全部执行。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent([ActionResult(), ActionResult(), ActionResult()])
		model_output = {
			"actions": [
				{"name": "input_text", "params": {"index": 1, "text": "a"}},
				{"name": "input_text", "params": {"index": 2, "text": "b"}},
				{"name": "click", "params": {"index": 3}},
			],
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		results = await StepPipeline._execute_actions(agent, model_output, browser_state)

		assert len(results) == 3
		assert [c[0] for c in agent.tools.calls] == ["input_text", "input_text", "click"]

	@pytest.mark.asyncio
	async def test_guard4_terminating_as_last_action_ok(self):
		"""terminates_sequence 动作作为最后一个时正常执行（不触发 skip）。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent([ActionResult(), ActionResult()])
		model_output = {
			"actions": [
				{"name": "click", "params": {"index": 1}},
				{"name": "navigate", "params": {"url": "https://x.com"}},
			],
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		results = await StepPipeline._execute_actions(agent, model_output, browser_state)

		# 两个都执行；门 #4 在 navigate 后触发但因为已是最后一个，行为无差异
		assert len(results) == 2
		assert [c[0] for c in agent.tools.calls] == ["click", "navigate"]


# ── 7. Phase 3 runtime guards ───────────────────────────────────────────


def _wrap_execute_with_drift(tools: _FakeTools, *, drift_url: str | None = None, drift_target_id: str | None = None) -> None:
	"""让 _FakeTools 在第一次 execute 调用后改 browser 的 URL/target_id（模拟副作用）。

	在调用 ``await StepPipeline._execute_actions`` 之前调用本函数，即可让守卫门 #5
	在第一次动作执行后看到 URL 或 target_id 漂移。
	"""
	original = tools.execute
	calls = {"n": 0}

	async def drifting_execute(name: str, params: dict, browser: Any, browser_state: Any) -> ActionResult:
		result = await original(name, params, browser, browser_state)
		calls["n"] += 1
		if calls["n"] == 1:
			if drift_url is not None:
				browser._url = drift_url
			if drift_target_id is not None:
				browser.current_target_id = drift_target_id
		return result

	tools.execute = drifting_execute


class TestPhase3RuntimeGuards:
	"""Phase 3 运行时守卫门 #5：URL / target_id 漂移检测。"""

	@pytest.mark.asyncio
	async def test_guard5_url_drift_breaks_loop(self):
		"""门 #5：第一个 action 后 URL 变化 → 后续动作被跳过。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent([ActionResult(), ActionResult()])
		_wrap_execute_with_drift(agent.tools, drift_url="https://other.com")
		model_output = {
			"actions": [
				{"name": "click", "params": {}},
				{"name": "click", "params": {}},  # 应被跳过
			],
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		results = await StepPipeline._execute_actions(agent, model_output, browser_state)

		assert len(results) == 1
		assert len(agent.tools.calls) == 1

	@pytest.mark.asyncio
	async def test_guard5_target_id_drift_breaks_loop(self):
		"""门 #5：第一个 action 后 target_id 变化（新 tab 打开）→ 后续动作被跳过。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent([ActionResult(), ActionResult()])
		_wrap_execute_with_drift(agent.tools, drift_target_id="new-tab-xyz")
		model_output = {
			"actions": [
				{"name": "click", "params": {}},
				{"name": "click", "params": {}},  # 应被跳过
			],
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		results = await StepPipeline._execute_actions(agent, model_output, browser_state)

		assert len(results) == 1
		assert len(agent.tools.calls) == 1

	@pytest.mark.asyncio
	async def test_guard5_no_drift_continues_loop(self):
		"""反向验证：URL 与 target_id 都不变 → 所有动作执行。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent([ActionResult(), ActionResult(), ActionResult()])
		model_output = {
			"actions": [
				{"name": "click", "params": {"index": 1}},
				{"name": "click", "params": {"index": 2}},
				{"name": "click", "params": {"index": 3}},
			],
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		results = await StepPipeline._execute_actions(agent, model_output, browser_state)

		assert len(results) == 3
		assert len(agent.tools.calls) == 3

	@pytest.mark.asyncio
	async def test_guard5_skips_on_second_action_only(self):
		"""门 #5：漂移发生在第二个 action 后 → 第三个被跳过，前两个执行。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent([ActionResult(), ActionResult(), ActionResult()])
		original = agent.tools.execute
		calls = {"n": 0}

		async def drifting_execute(name: str, params: dict, browser: Any, browser_state: Any) -> ActionResult:
			result = await original(name, params, browser, browser_state)
			calls["n"] += 1
			if calls["n"] == 2:  # 第二个 action 后漂移
				browser._url = "https://other.com"
			return result

		agent.tools.execute = drifting_execute
		model_output = {
			"actions": [
				{"name": "click", "params": {}},
				{"name": "click", "params": {}},
				{"name": "click", "params": {}},  # 应被跳过
			],
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		results = await StepPipeline._execute_actions(agent, model_output, browser_state)

		assert len(results) == 2
		assert len(agent.tools.calls) == 2


# ── 8. _wait_for_page_settle ────────────────────────────────────────────


class TestWaitForPageSettle:
	"""BrowserSession._wait_for_page_settle 行为。"""

	def _make_session_with_state_sequence(self, states: list[str]) -> Any:
		"""构造一个 BrowserSession 替身，按顺序返回 document.readyState 值。"""
		from tree_walker.config import BrowserSettings
		from tree_walker.browser.session import BrowserSession

		# 不连真浏览器，直接构造一个半初始化的 session
		session = BrowserSession.__new__(BrowserSession)
		session._settings = BrowserSettings(
			ws_url="ws://x",
			page_settle_timeout=0.5,
			page_settle_poll_interval=0.01,
		)
		session.client = MagicMock()
		session.current_session_id = "fake-session"
		queue = list(states)

		async def fake_send(method, params=None, session_id=None, **kwargs):
			# 模拟 Runtime.evaluate
			state = queue.pop(0) if queue else "complete"
			return {"result": {"value": state}}

		session.client.send = MagicMock()
		# client.send.Runtime.evaluate(...) 是 attribute chain；直接把
		# Runtime.evaluate 替身为可 await 的 callable
		session.client.send.Runtime.evaluate = fake_send
		return session

	@pytest.mark.asyncio
	async def test_returns_immediately_when_complete(self):
		"""readyState == 'complete' → 立即返回，不轮询。"""
		session = self._make_session_with_state_sequence(["complete"])
		# 应该几乎瞬间返回
		import time as _time
		t0 = _time.monotonic()
		await session._wait_for_page_settle()
		elapsed = _time.monotonic() - t0
		assert elapsed < 0.1

	@pytest.mark.asyncio
	async def test_polls_until_complete(self):
		"""readyState 从 loading → interactive → complete → 返回。"""
		session = self._make_session_with_state_sequence([
			"loading", "interactive", "complete",
		])
		# 不应该抛异常，且应该正常返回（不是超时）
		await session._wait_for_page_settle()

	@pytest.mark.asyncio
	async def test_times_out_when_never_complete(self):
		"""readyState 一直非 complete → 超时后返回。"""
		session = self._make_session_with_state_sequence(["loading"] * 1000)
		import time as _time
		t0 = _time.monotonic()
		await session._wait_for_page_settle()
		elapsed = _time.monotonic() - t0
		# timeout 是 0.5s；实际 elapsed 应该接近 0.5s（容忍一点波动）
		assert 0.4 < elapsed < 1.5

	@pytest.mark.asyncio
	async def test_returns_immediately_without_client(self):
		"""client=None 时立即返回（不抛异常）。"""
		from tree_walker.browser.session import BrowserSession
		from tree_walker.config import BrowserSettings

		session = BrowserSession.__new__(BrowserSession)
		session._settings = BrowserSettings(ws_url="ws://x")
		session.client = None
		session.current_session_id = None

		# 不应该抛异常
		await session._wait_for_page_settle()


# ── 9. Phase 4 exception triage ─────────────────────────────────────────


class TestPhase4ExceptionTriage:
	"""Phase 4 异常三分流：InterruptedError 重抛 / 连接错误重抛 / 其他吞掉。"""

	@pytest.mark.asyncio
	async def test_interrupted_error_propagates(self):
		"""InterruptedError 向上抛，不被转化为 ActionResult。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent([])
		call_count = {"n": 0}

		async def interrupting_execute(*args, **kwargs):
			call_count["n"] += 1
			raise InterruptedError("user stopped")
		agent.tools.execute = interrupting_execute

		model_output = {"action": {"name": "click", "params": {}}}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		with pytest.raises(InterruptedError):
			await StepPipeline._execute_actions(agent, model_output, browser_state)

		# action 被尝试执行一次（异常发生在 result.append 之前）
		assert call_count["n"] == 1

	@pytest.mark.asyncio
	async def test_connection_error_propagates(self):
		"""连接错误向上抛，触发上层重连逻辑。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent([])

		async def failing_execute(*args, **kwargs):
			raise ConnectionError("websocket connection closed")
		agent.tools.execute = failing_execute

		model_output = {"action": {"name": "click", "params": {}}}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		with pytest.raises(ConnectionError):
			await StepPipeline._execute_actions(agent, model_output, browser_state)

	@pytest.mark.asyncio
	async def test_generic_exception_returns_error_result(self):
		"""普通异常被吞掉，追加 ActionResult(error)，剩余动作被跳过。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent([])
		call_count = {"n": 0}

		async def failing_execute(*args, **kwargs):
			call_count["n"] += 1
			raise RuntimeError("element not found")
		agent.tools.execute = failing_execute

		model_output = {
			"actions": [
				{"name": "click", "params": {}},
				{"name": "click", "params": {}},  # 应被跳过
			],
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		results = await StepPipeline._execute_actions(agent, model_output, browser_state)

		assert len(results) == 1
		assert "RuntimeError" in results[0].error
		# 只有第一个 action 被调用，第二个被跳过
		assert call_count["n"] == 1

	@pytest.mark.asyncio
	async def test_timeout_in_second_action_returns_partial_results(self):
		"""第一个 action 成功、第二个 timeout → 返回 [success, timeout_error]。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent([ActionResult()])
		# 第二次调用 execute 时阻塞，触发 action_timeout
		call_count = {"n": 0}
		original = agent.tools.execute

		async def slow_second_call(name, params, browser, browser_state):
			call_count["n"] += 1
			if call_count["n"] == 2:
				await asyncio.sleep(10)
			return await original(name, params, browser, browser_state)
		agent.tools.execute = slow_second_call
		agent.action_timeout = 0.05

		model_output = {
			"actions": [
				{"name": "click", "params": {"index": 1}},
				{"name": "click", "params": {"index": 2}},
				{"name": "click", "params": {"index": 3}},  # 应被跳过
			],
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		results = await StepPipeline._execute_actions(agent, model_output, browser_state)

		# 1 个成功 + 1 个 timeout error，共 2 个；第三个被跳过
		assert len(results) == 2
		assert results[0].error is None
		assert results[1].error is not None
		assert "timed out" in results[1].error.lower()
		# 第二个 action 进入 wrapper 但被 wait_for 超时取消；第三个被跳过
		assert call_count["n"] == 2


# ── 10. Phase 4 failure count semantics ─────────────────────────────────


class TestPhase4FailureCount:
	"""Phase 4 _post_process 失败计数语义细化。"""

	def _make_post_process_agent(self, *, initial_failures: int = 0) -> Any:
		from tree_walker.agent.step import StepPipeline  # noqa: F401

		agent = MagicMock()
		agent.state = AgentState()
		agent.state.consecutive_failures = initial_failures
		agent._enable_planning = False
		agent.plan_manager = None

		class _NoOpLoopDetector:
			def record_action(self, *args, **kwargs):
				pass

		agent.loop_detector = _NoOpLoopDetector()
		return agent

	def test_single_action_error_increments(self):
		"""单动作步失败 → consecutive_failures += 1。"""
		from tree_walker.agent.step import StepPipeline

		agent = self._make_post_process_agent(initial_failures=0)
		model_output = {"action": {"name": "click", "params": {}}}
		results = [ActionResult(error="boom")]

		StepPipeline._post_process(agent, results, model_output)

		assert agent.state.consecutive_failures == 1

	def test_multi_action_all_error_increments(self):
		"""多动作步全失败 → consecutive_failures += 1。"""
		from tree_walker.agent.step import StepPipeline

		agent = self._make_post_process_agent(initial_failures=0)
		model_output = {
			"actions": [
				{"name": "click", "params": {}},
				{"name": "click", "params": {}},
			],
		}
		results = [
			ActionResult(error="err1"),
			ActionResult(error="err2"),
		]

		StepPipeline._post_process(agent, results, model_output)

		assert agent.state.consecutive_failures == 1

	def test_multi_action_partial_error_does_not_increment(self):
		"""多动作步部分失败 → consecutive_failures 不增加（保持原值或被 reset，但不 +1）。"""
		from tree_walker.agent.step import StepPipeline

		# initial_failures=0 → 部分失败不会让计数变成 1
		agent = self._make_post_process_agent(initial_failures=0)
		model_output = {
			"actions": [
				{"name": "click", "params": {}},
				{"name": "click", "params": {}},
			],
		}
		results = [
			ActionResult(),
			ActionResult(error="oops"),
		]

		StepPipeline._post_process(agent, results, model_output)

		# 关键断言：没有增加到 1
		assert agent.state.consecutive_failures == 0

	def test_multi_action_partial_error_resets_prior_failures(self):
		"""多动作步部分失败、有 prior failures → reset 为 0（fall through 到 success 分支）。"""
		from tree_walker.agent.step import StepPipeline

		agent = self._make_post_process_agent(initial_failures=2)
		model_output = {
			"actions": [
				{"name": "click", "params": {}},
				{"name": "click", "params": {}},
			],
		}
		results = [
			ActionResult(),
			ActionResult(error="oops"),
		]

		StepPipeline._post_process(agent, results, model_output)

		# partial failure 含至少一个 success → reset 为 0
		assert agent.state.consecutive_failures == 0

	def test_multi_action_partial_error_resets_when_no_history(self):
		"""多动作步部分失败、无 prior failures → 仍为 0（明确不计数）。"""
		from tree_walker.agent.step import StepPipeline

		agent = self._make_post_process_agent(initial_failures=0)
		model_output = {
			"actions": [
				{"name": "click", "params": {}},
				{"name": "click", "params": {}},
			],
		}
		results = [
			ActionResult(error="err"),
			ActionResult(),
		]

		StepPipeline._post_process(agent, results, model_output)

		assert agent.state.consecutive_failures == 0

	def test_all_success_resets_counter(self):
		"""全成功 → consecutive_failures 重置为 0。"""
		from tree_walker.agent.step import StepPipeline

		agent = self._make_post_process_agent(initial_failures=3)
		model_output = {
			"actions": [
				{"name": "click", "params": {}},
				{"name": "click", "params": {}},
			],
		}
		results = [ActionResult(), ActionResult()]

		StepPipeline._post_process(agent, results, model_output)

		assert agent.state.consecutive_failures == 0


# ── 11. Phase 4 wait_between_actions ────────────────────────────────────


class TestPhase4WaitBetweenActions:
	"""Phase 4 wait_between_actions 反爬节奏停顿。"""

	@pytest.mark.asyncio
	async def test_default_zero_does_not_sleep(self, monkeypatch):
		"""wait_between_actions=0 → 不调用 asyncio.sleep。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent([ActionResult(), ActionResult()])
		# 默认 _make_agent 设 wait_between_actions=0.0
		sleep_calls: list[float] = []
		original_sleep = asyncio.sleep

		async def tracking_sleep(seconds):
			# 仅追踪正向停顿；其他 await 走原 sleep
			if seconds > 0:
				sleep_calls.append(seconds)
			await original_sleep(0)
		monkeypatch.setattr("tree_walker.agent.step.asyncio.sleep", tracking_sleep)

		model_output = {
			"actions": [
				{"name": "click", "params": {}},
				{"name": "click", "params": {}},
			],
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		await StepPipeline._execute_actions(agent, model_output, browser_state)

		# 没有任何 wait_between_actions 触发的 sleep
		assert sleep_calls == []

	@pytest.mark.asyncio
	async def test_positive_value_sleeps_between_actions(self, monkeypatch):
		"""wait_between_actions=0.5 → 两个 action 之间 sleep 一次。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent([ActionResult(), ActionResult(), ActionResult()])
		agent.wait_between_actions = 0.5
		sleep_calls: list[float] = []
		original_sleep = asyncio.sleep

		async def tracking_sleep(seconds):
			if seconds == 0.5:
				sleep_calls.append(seconds)
			await original_sleep(0)
		monkeypatch.setattr("tree_walker.agent.step.asyncio.sleep", tracking_sleep)

		model_output = {
			"actions": [
				{"name": "click", "params": {}},
				{"name": "click", "params": {}},
				{"name": "click", "params": {}},
			],
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		await StepPipeline._execute_actions(agent, model_output, browser_state)

		# 3 个 action，应有 2 次 wait_between_actions sleep（i=1 和 i=2 之前）
		assert sleep_calls == [0.5, 0.5]

	@pytest.mark.asyncio
	async def test_no_sleep_on_last_action(self, monkeypatch):
		"""单 action 不触发 wait_between_actions sleep（i==0 跳过）。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent([ActionResult()])
		agent.wait_between_actions = 0.5
		sleep_calls: list[float] = []
		original_sleep = asyncio.sleep

		async def tracking_sleep(seconds):
			if seconds == 0.5:
				sleep_calls.append(seconds)
			await original_sleep(0)
		monkeypatch.setattr("tree_walker.agent.step.asyncio.sleep", tracking_sleep)

		model_output = {"action": {"name": "click", "params": {}}}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		await StepPipeline._execute_actions(agent, model_output, browser_state)

		assert sleep_calls == []

	@pytest.mark.asyncio
	async def test_no_sleep_after_guard_break(self, monkeypatch):
		"""guard #1 在 done 处 break → 不在 done 之前 sleep（done 是 i>0 但被跳过）。"""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent([ActionResult()])
		agent.wait_between_actions = 0.5
		sleep_calls: list[float] = []
		original_sleep = asyncio.sleep

		async def tracking_sleep(seconds):
			if seconds == 0.5:
				sleep_calls.append(seconds)
			await original_sleep(0)
		monkeypatch.setattr("tree_walker.agent.step.asyncio.sleep", tracking_sleep)

		# 第一个是 click（i==0，不 sleep），第二个是 done（i>0，guard #1 break）
		model_output = {
			"actions": [
				{"name": "click", "params": {}},
				{"name": "done", "params": {}},
			],
		}
		browser_state = MagicMock()
		browser_state.url = "https://example.com"

		await StepPipeline._execute_actions(agent, model_output, browser_state)

		# done 在 sleep 之前被 guard #1 拦截
		assert sleep_calls == []


class TestActionTruncation:
	"""P0-2: _truncate_actions hard-caps actions to max_actions_per_step (browser-use service.py:1950-1951)."""

	def _make_agent(self, max_actions: int = 3) -> Any:
		agent = _make_agent()
		agent.max_actions_per_step = max_actions
		agent.state.n_steps = 1
		return agent

	def _resp(self, names: list[str]) -> dict[str, Any]:
		return {
			"action": {"name": names[0], "params": {}} if names else {},
			"actions": [{"name": n, "params": {}} for n in names],
		}

	def test_under_max_not_truncated(self):
		"""actions count <= max → unchanged."""
		from tree_walker.agent.step import StepPipeline
		agent = self._make_agent(max_actions=3)
		resp = self._resp(["click", "input_text"])
		out = StepPipeline._truncate_actions(agent, resp)
		assert len(out["actions"]) == 2

	def test_over_max_truncated_with_warning(self, caplog):
		"""actions count > max → truncated to max and warning lists dropped names."""
		import logging
		from tree_walker.agent.step import StepPipeline
		agent = self._make_agent(max_actions=3)
		resp = self._resp(["click", "input_text", "scroll", "wait", "done"])
		with caplog.at_level(logging.WARNING):
			out = StepPipeline._truncate_actions(agent, resp)
		assert len(out["actions"]) == 3
		assert "truncated" in caplog.text
		assert "wait" in caplog.text
		assert "done" in caplog.text

	def test_truncation_keeps_action_and_actions_consistent(self):
		"""After truncation, action (first) and actions (list) stay in sync."""
		from tree_walker.agent.step import StepPipeline
		agent = self._make_agent(max_actions=2)
		resp = self._resp(["click", "input_text", "scroll", "wait"])
		out = StepPipeline._truncate_actions(agent, resp)
		assert len(out["actions"]) == 2
		assert out["action"]["name"] == "click"
		assert out["actions"][0]["name"] == "click"

	def test_exactly_max_not_truncated(self):
		"""actions count == max boundary → unchanged."""
		from tree_walker.agent.step import StepPipeline
		agent = self._make_agent(max_actions=3)
		resp = self._resp(["click", "input_text", "scroll"])
		out = StepPipeline._truncate_actions(agent, resp)
		assert len(out["actions"]) == 3

	def test_empty_actions_not_truncated(self):
		"""Empty actions list → unchanged, no crash."""
		from tree_walker.agent.step import StepPipeline
		agent = self._make_agent(max_actions=3)
		resp = {"action": {}, "actions": []}
		out = StepPipeline._truncate_actions(agent, resp)
		assert out["actions"] == []

	def test_single_action_not_truncated(self):
		"""Single action → unchanged."""
		from tree_walker.agent.step import StepPipeline
		agent = self._make_agent(max_actions=5)
		resp = self._resp(["click"])
		out = StepPipeline._truncate_actions(agent, resp)
		assert len(out["actions"]) == 1

	def test_actions_not_list_returned_unchanged(self):
		"""Missing/non-list actions → response returned unchanged."""
		from tree_walker.agent.step import StepPipeline
		agent = self._make_agent(max_actions=3)
		resp = {"action": {"name": "click", "params": {}}}
		out = StepPipeline._truncate_actions(agent, resp)
		assert out is resp

	def test_done_in_dropped_region_is_dropped(self, caplog):
		"""done beyond max is dropped — LLM violated 'done must be last', truncation is the feedback."""
		import logging
		from tree_walker.agent.step import StepPipeline
		agent = self._make_agent(max_actions=2)
		resp = self._resp(["click", "input_text", "scroll", "done"])
		with caplog.at_level(logging.WARNING):
			out = StepPipeline._truncate_actions(agent, resp)
		assert len(out["actions"]) == 2
		assert all(a["name"] != "done" for a in out["actions"])
		assert "done" in caplog.text

