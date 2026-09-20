"""issue #194：infra 步不烧步数预算 + infra 预算接管 livelock 防护的测试。

背景（docs/bug-fix/194-rate-limit-infra-wait-analysis.md）：B 轮 task_550 被
API 限流 5 连发触发 max_failures 能力止损死刑——11 个限流失败步各烧 1 步
（30 步预算烧掉 11），20 秒速死零交接。修复 = Branch 2.5 infra 分罪
（不进 consecutive_failures、豁免 n_steps 递增、真退避）+ run() 顶部
infra_failures 预算检查（infra 步不递增步数后的防 livelock 界）。
本文件把 issue 验收原文（"注入限流场景回放，有效步数消耗与无限流时一致"）
编码为可回归断言。
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anthropic import RateLimitError

from tree_walker.agent.agent import Agent
from tree_walker.agent.loop_detector import ActionLoopDetector
from tree_walker.agent.step import StepPipeline
from tree_walker.agent.views import (
	ActionResult,
	AgentHistoryList,
	AgentState,
)
from tree_walker.browser.views import BrowserStateSummary, SerializedDOMState
from tree_walker.config import AgentSettings


def _rl() -> RateLimitError:
	"""RateLimitError 桩——真实协议形状（response MagicMock / body None）。"""
	return RateLimitError(
		message="rate limited",
		response=MagicMock(status_code=429),
		body=None,
	)


def _browser_state() -> BrowserStateSummary:
	return BrowserStateSummary(
		url="https://example.com",
		title="Example",
		dom_state=SerializedDOMState(
			_root=None, selector_map={}, element_tree_text="dom",
		),
	)


def _loop_agent() -> Any:
	"""驱动 StepPipeline._step 的最小桩（属性集对齐 test_step_malformed_action
	的 _loop_agent——MagicMock 自动属性恒 truthy 的坑同款防御）。"""
	agent = MagicMock()
	agent.state = AgentState()
	agent.action_timeout = 30
	agent.exploration_actionability_check = False
	agent.wait_between_actions = 0.0
	agent.browser = MagicMock()
	agent._obs_bus = None
	agent._obs_session_id = "test"
	agent._current_model_call_id = ""
	agent._sensitive_map_for_log = None
	agent.loop_detector = ActionLoopDetector()
	agent._track_downloads = False
	agent._enable_planning = False
	agent.plan_manager = None
	agent._compactor = None  # MagicMock 不可 await——_step 里 `await .maybe_compact`
	agent.history = AgentHistoryList()
	# _step 尾部 `consecutive_failures >= self.max_failures` 与 Branch 3 都比较
	# 此值——MagicMock 自动属性会让 int>=Mock 抛 TypeError（被 finally 兜住
	# 后静默吞掉，断言看不到根因）
	agent.max_failures = 5
	agent.max_infra_failures = 8
	# issue #194：豁免标记显式置 False（MagicMock 自动属性恒 truthy）
	agent._skip_step_increment = False
	# 真错误处理器 + 真 _finalize + 真 _post_process + 真元素投影——infra 分罪、
	# history 追加、成功清零都要跑真逻辑（auto-attr 都是 no-op Mock：_finalize
	# 还是普通 Mock，await 直接炸；_post_process 静默吞 reset；投影返回的 Mock
	# 过不了 AgentHistory.interacted_element 的 pydantic 校验）
	agent._handle_step_error = StepPipeline._handle_step_error.__get__(agent)
	agent._finalize = StepPipeline._finalize.__get__(agent)
	agent._post_process = StepPipeline._post_process.__get__(agent)
	agent._safe_project_interacted_elements = (
		StepPipeline._safe_project_interacted_elements.__get__(agent)
	)
	agent._build_step_metadata = StepPipeline._build_step_metadata.__get__(agent)
	return agent


def _wire_success_and_failures(agent: Any, side_effects: list[Any]) -> None:
	"""接 _prepare_context/_get_next_action/_execute_actions：side_effects 依次
	成为每步的 _get_next_action 结果（异常项则当步抛出）；动作恒成功。"""
	agent._prepare_context = AsyncMock(return_value=(_browser_state(), "state msg"))
	agent._get_next_action = AsyncMock(side_effect=list(side_effects))
	agent._execute_actions = AsyncMock(return_value=[ActionResult()])


def _ok_output() -> dict[str, Any]:
	return {"actions": [{"name": "click", "params": {}}]}


class TestInfraStepBudget:
	@pytest.mark.asyncio
	async def test_infra_step_not_counted_then_success_resets(self):
		"""infra 步不烧步数/不进 history；成功步后 infra 计数清零。"""
		agent = _loop_agent()
		_wire_success_and_failures(agent, [_rl(), _ok_output()])

		with patch("tree_walker.agent.step.asyncio.sleep", new_callable=AsyncMock):
			assert await StepPipeline._step(agent) is False  # infra 失败步
		assert agent.state.n_steps == 0
		assert agent.state.infra_failures == 1
		assert len(agent.history.history) == 0  # runner 报表口径（len(history)）免疫

		assert await StepPipeline._step(agent) is False  # 成功步
		assert agent.state.n_steps == 1
		assert agent.state.infra_failures == 0  # B6 成功清零
		assert len(agent.history.history) == 1

	@pytest.mark.asyncio
	async def test_skip_flag_does_not_leak_to_next_step(self):
		"""豁免标记每步复位：infra 步后的通用失败步照常计步（不白送步数）。"""
		agent = _loop_agent()
		_wire_success_and_failures(
			agent, [_rl(), ValueError("boom"), _ok_output()],
		)

		with patch("tree_walker.agent.step.asyncio.sleep", new_callable=AsyncMock):
			await StepPipeline._step(agent)  # infra → n_steps 0
			await StepPipeline._step(agent)  # 通用失败（Branch 3）→ n_steps 1
		assert agent.state.n_steps == 1
		assert agent.state.consecutive_failures == 1
		await StepPipeline._step(agent)  # 成功 → n_steps 2
		assert agent.state.n_steps == 2

	@pytest.mark.asyncio
	async def test_capability_error_step_resets_infra_streak(self):
		"""review2：到达 _post_process 即证明 LLM 可达——单动作能力失败步
		（early return 路径）同样清零 infra：被能力失败步隔开的两个限流窗口
		不叠加判死（「连续基建失败才累积」语义）。"""
		agent = _loop_agent()
		agent._prepare_context = AsyncMock(return_value=(_browser_state(), "state msg"))
		agent._get_next_action = AsyncMock(side_effect=[
			_rl(), _rl(),                        # 限流窗口 1（infra 2 连）
			_ok_output(),                        # 能力失败步（单动作 error → early return）
			_rl(),                              # 限流窗口 2 首枪
		])
		agent._execute_actions = AsyncMock(side_effect=[
			[ActionResult(error="boom")],        # 能力失败步
			[ActionResult()],                   # 窗口 2 不会执行到动作（infra 在 LLM 层失败）
		])

		with patch("tree_walker.agent.step.asyncio.sleep", new_callable=AsyncMock):
			await StepPipeline._step(agent)  # infra
			await StepPipeline._step(agent)  # infra
		assert agent.state.infra_failures == 2

		await StepPipeline._step(agent)  # 单动作能力失败 → early return 前清零
		assert agent.state.consecutive_failures == 1
		assert agent.state.infra_failures == 0  # ← review2 回归锁

		with patch("tree_walker.agent.step.asyncio.sleep", new_callable=AsyncMock):
			await StepPipeline._step(agent)  # 窗口 2 首枪——从 1 重新计
		assert agent.state.infra_failures == 1

	@pytest.mark.asyncio
	async def test_step_consumption_identical_with_injected_rate_limit(self):
		"""issue 验收原文编码：注入 RateLimit×2 的 run 与干净 run 终态的
		有效步数消耗完全一致（n_steps / history / consecutive_failures）。"""
		clean = _loop_agent()
		_wire_success_and_failures(clean, [_ok_output(), _ok_output(), _ok_output()])

		injected = _loop_agent()
		_wire_success_and_failures(
			injected, [_rl(), _rl(), _ok_output(), _ok_output(), _ok_output()],
		)

		for _ in range(3):
			await StepPipeline._step(clean)
		with patch("tree_walker.agent.step.asyncio.sleep", new_callable=AsyncMock):
			for _ in range(5):
				await StepPipeline._step(injected)

		# 有效步数消耗与无限流时一致（issue #194 验收）
		assert injected.state.n_steps == clean.state.n_steps == 3
		assert len(injected.history.history) == len(clean.history.history) == 3
		assert injected.state.consecutive_failures == clean.state.consecutive_failures == 0
		# 限流窗口已被成功步洗掉（连续语义，非累计）
		assert injected.state.infra_failures == 0


class TestRunInfraBudget:
	@pytest.mark.asyncio
	async def test_run_breaks_on_infra_budget_not_max_steps(self, caplog):
		"""run() 级：连续 infra 失败达 max_infra_failures 独立终止（基建死法），
		不烧步数、不等 max_steps——livelock 防护从步数递增移交至此。"""
		import logging

		browser = MagicMock()
		browser.start = AsyncMock()
		browser.stop = AsyncMock()
		browser._settings = MagicMock(wait_between_actions=0.0)
		agent = Agent(
			task="t", llm=MagicMock(), browser=browser,
			settings=AgentSettings(
				max_steps=10, max_infra_failures=3, enable_planning=False,
			),
		)

		calls = {"n": 0}

		async def infra_only_step():
			# 模拟 Branch 2.5 效果：infra+1、n_steps 不动（豁免路径的真实形状）
			calls["n"] += 1
			agent.state.infra_failures += 1
			return False

		agent._step = infra_only_step
		with caplog.at_level(logging.ERROR):
			await agent.run()

		assert agent.state.infra_failures == 3  # 在 infra 预算处终止
		assert calls["n"] == 3
		assert agent.state.n_steps == 0  # 从未烧步数（绝非 max_steps 耗尽）
		assert any("API unreachable" in r.getMessage() for r in caplog.records)

	@pytest.mark.asyncio
	async def test_run_infra_check_before_step(self):
		"""顺序：预算检查在步前——达限后不再多跑一步（B 轮死法不再叠加空转）。"""
		browser = MagicMock()
		browser.start = AsyncMock()
		browser.stop = AsyncMock()
		browser._settings = MagicMock(wait_between_actions=0.0)
		agent = Agent(
			task="t", llm=MagicMock(), browser=browser,
			settings=AgentSettings(
				max_steps=10, max_infra_failures=2, enable_planning=False,
			),
		)
		agent.state.infra_failures = 2  # 已达限（模拟前两步 infra）

		calls = {"n": 0}

		async def never_called_step():
			calls["n"] += 1
			return False

		agent._step = never_called_step
		await agent.run()
		assert calls["n"] == 0  # 预算检查先于 _step，零空转

class TestInfraSettings:
	def test_max_infra_failures_env_override(self):
		"""AGENT_MAX_INFRA_FAILURES 覆盖默认 8（评测/调试可调）。"""
		from tree_walker.config import load_settings

		env = {"ZHIPU_API_KEY": "test", "AGENT_MAX_INFRA_FAILURES": "3"}
		with patch.dict(os.environ, env, clear=False):
			assert load_settings().agent.max_infra_failures == 3

	def test_max_infra_failures_default(self):
		"""缺省回落 8（连续基建失败的终止预算）。"""
		from tree_walker.config import load_settings

		env = {"ZHIPU_API_KEY": "test"}
		with patch.dict(os.environ, env, clear=False):
			os.environ.pop("AGENT_MAX_INFRA_FAILURES", None)
			assert load_settings().agent.max_infra_failures == 8
