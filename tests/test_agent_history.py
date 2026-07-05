"""Tests for P1c agent_history_description —— <agent_history> 滑动窗口 + 每步替换注入。

对齐方案：``docs/agent-loop-optimize/01-准备上下文对齐browser-use方案.md`` §5.3。
对齐 browser-use ``agent_history_description``：首条 + [... N omitted ...] + 最近 N 条。
"""

from tree_walker.agent.agent import Agent
from tree_walker.agent.step import _MSG_TYPE, TYPE_USER
from tree_walker.agent.views import ActionResult, AgentHistory, AgentHistoryList


class _FakeAgent(Agent):
	"""继承 Agent 拿到 _build_agent_history_description / _set_history_message，
	跳过 __init__，只设测试所需属性。"""

	def __init__(self, history=None, max_history_items=10, compactor=None,
	             enable_message_typing=True):
		self.history = history if history is not None else AgentHistoryList()
		self._max_history_items = max_history_items
		self._compactor = compactor
		self.messages = []
		self._enable_message_typing = enable_message_typing


def _hist(step, goal="", eval_="", memory="", action_name="click", params=None,
          result=None, actions=None) -> AgentHistory:
	mo = {
		"next_goal": goal,
		"evaluation_previous_goal": eval_,
		"memory": memory,
	}
	if actions is not None:
		mo["actions"] = actions
	else:
		mo["action"] = {"name": action_name, "params": params or {}}
	return AgentHistory(
		step_number=step,
		model_output=mo,
		result=[result] if result else [],
	)


class TestBuildAgentHistoryDescription:
	def test_none_when_no_history(self):
		agent = _FakeAgent()
		assert agent._build_agent_history_description() is None

	def test_none_when_max_items_zero(self):
		agent = _FakeAgent(
			history=AgentHistoryList(history=[_hist(0, goal="g")]),
			max_history_items=0,
		)
		assert agent._build_agent_history_description() is None

	def test_three_steps_full_no_omission(self):
		"""3 步内（max=10）：含全部历史，无省略提示。"""
		hist = AgentHistoryList(history=[
			_hist(0, goal="open page", eval_="start"),
			_hist(1, goal="fill form", eval_="Success"),
			_hist(2, goal="submit", eval_="Success"),
		])
		agent = _FakeAgent(history=hist)
		desc = agent._build_agent_history_description()
		assert desc is not None
		assert "<agent_history>" in desc and "</agent_history>" in desc
		assert "omitted" not in desc
		assert "Step 0:" in desc and "Step 1:" in desc and "Step 2:" in desc
		assert "open page" in desc and "fill form" in desc and "submit" in desc

	def test_fifteen_steps_window_omits_middle(self):
		"""15 步（max=10）：首步 + [... 5 previous steps omitted ...] + 最近 9 步。

		保留 = 索引 0 + 最后 9 个（索引 6..14）；省略 = 索引 1..5（共 5 个）。
		"""
		hist = AgentHistoryList(history=[
			_hist(i, goal=f"goal-{i}") for i in range(15)
		])
		agent = _FakeAgent(history=hist, max_history_items=10)
		desc = agent._build_agent_history_description()
		assert "[... 5 previous steps omitted ...]" in desc
		assert "Step 0:" in desc  # 首步保留
		assert "goal-0 |" in desc
		# 最近 9 步（索引 6..14）保留（用 "goal-N |" 精确匹配，避免 goal-1 命中 goal-10）
		for i in range(6, 15):
			assert f"goal-{i} |" in desc
		# 中间被省略（索引 1..5）
		for i in range(1, 6):
			assert f"goal-{i} |" not in desc

	def test_memory_rendered(self):
		hist = AgentHistoryList(history=[
			_hist(0, goal="g", memory="login worked at step 0"),
		])
		agent = _FakeAgent(history=hist)
		desc = agent._build_agent_history_description()
		assert "Memory: login worked at step 0" in desc

	def test_no_memory_line_when_empty(self):
		hist = AgentHistoryList(history=[_hist(0, goal="g", memory="")])
		agent = _FakeAgent(history=hist)
		desc = agent._build_agent_history_description()
		assert "Memory:" not in desc

	def test_compactor_reduces_window_to_five(self):
		"""compactor 启用时 max_history_items 降为 5。"""
		hist = AgentHistoryList(history=[
			_hist(i, goal=f"g-{i}") for i in range(15)
		])
		agent = _FakeAgent(history=hist, max_history_items=10, compactor=object())
		desc = agent._build_agent_history_description()
		# window=5 → first + last 4 = 5 shown；omitted = 15-5 = 10
		assert "[... 10 previous steps omitted ...]" in desc
		assert "g-0" in desc  # 首步
		for i in range(11, 15):  # 最近 4 步
			assert f"g-{i}" in desc

	def test_compactor_does_not_increase_small_setting(self):
		"""setting=3 + compactor → min(3,5)=3（不被抬到 5）。"""
		hist = AgentHistoryList(history=[_hist(i) for i in range(10)])
		agent = _FakeAgent(history=hist, max_history_items=3, compactor=object())
		assert agent._effective_max_history_items() == 3

	def test_multi_action_rendered(self):
		hist = AgentHistoryList(history=[
			_hist(0, goal="fill form", actions=[
				{"name": "input_text", "params": {"index": 1, "text": "x"}},
				{"name": "click", "params": {"index": 2}},
			]),
		])
		agent = _FakeAgent(history=hist)
		desc = agent._build_agent_history_description()
		assert "input_text(" in desc and "click(" in desc

	def test_success_shows_checkmark_no_verbose_text(self):
		"""P1c 修订：成功步（含 verbose extracted_content）→ 历史里只显示 ✓，
		不泄漏情境性软警告（抖音封面上传回归根因）。"""
		result = ActionResult(extracted_content=(
			"Uploaded 'heng.png' to [INPUT] at index 32197  ⚠️ Page has 6 file inputs; "
			"uploaded to the one you specified (index 32197). Likely-live candidates "
			"(visible + upload container): [30676, 31315]. If the site reacted wrongly, "
			"retry upload_file on the correct visible upload area."
		))
		hist = AgentHistoryList(history=[_hist(0, goal="upload cover", result=result)])
		agent = _FakeAgent(history=hist)
		desc = agent._build_agent_history_description()
		line = [ln for ln in desc.splitlines() if "Step 0" in ln][0]
		assert line.endswith("-> ✓")
		# 不泄漏任何情境性文本
		assert "⚠️" not in desc
		assert "retry" not in desc
		assert "Likely-live" not in desc
		assert "Uploaded 'heng.png'" not in desc
		assert "32197" not in desc

	def test_error_shows_cross_with_message(self):
		err_result = ActionResult(error="ConnectionError: timeout")
		hist = AgentHistoryList(history=[_hist(0, goal="g", result=err_result)])
		agent = _FakeAgent(history=hist)
		desc = agent._build_agent_history_description()
		line = [ln for ln in desc.splitlines() if "Step 0" in ln][0]
		assert "-> ✗ ConnectionError: timeout" in line

	def test_error_message_truncated_to_80(self):
		long_err = ActionResult(error="E" * 200)
		hist = AgentHistoryList(history=[_hist(0, goal="g", result=long_err)])
		agent = _FakeAgent(history=hist)
		desc = agent._build_agent_history_description()
		line = [ln for ln in desc.splitlines() if "Step 0" in ln][0]
		result_part = line.split("-> ", 1)[1]
		assert result_part.startswith("✗ ")
		# "✗ " (2 chars) + 80 char error
		assert len(result_part) <= 82

	def test_done_shows_done_marker(self):
		done_result = ActionResult(is_done=True, success=True, extracted_content="任务完成总结")
		hist = AgentHistoryList(history=[_hist(0, goal="g", result=done_result)])
		agent = _FakeAgent(history=hist)
		desc = agent._build_agent_history_description()
		line = [ln for ln in desc.splitlines() if "Step 0" in ln][0]
		assert "-> ✓ done" in line
		# done 的 extracted_content 也不泄漏
		assert "任务完成总结" not in desc

	def test_empty_result_renders_empty(self):
		# h.result=[] → result_str=""（line 以 "-> " 结尾）
		hist = AgentHistoryList(history=[_hist(0, goal="g")])
		agent = _FakeAgent(history=hist)
		desc = agent._build_agent_history_description()
		line = [ln for ln in desc.splitlines() if "Step 0" in ln][0]
		assert line.endswith("-> ")


class TestSummarizeStepResult:
	"""直接测 Agent._summarize_step_result 的状态压缩逻辑。"""

	def test_empty(self):
		assert _FakeAgent()._summarize_step_result([]) == ""

	def test_success(self):
		assert _FakeAgent()._summarize_step_result(
			[ActionResult(extracted_content="ok")]
		) == "✓"

	def test_error_returns_cross_and_message(self):
		assert _FakeAgent()._summarize_step_result(
			[ActionResult(error="boom")]
		) == "✗ boom"

	def test_done(self):
		assert _FakeAgent()._summarize_step_result(
			[ActionResult(is_done=True, success=True)]
		) == "✓ done"

	def test_any_error_wins(self):
		# 多结果中任一 error → ✗（取第一个 error）
		assert _FakeAgent()._summarize_step_result([
			ActionResult(extracted_content="ok"),
			ActionResult(error="boom"),
		]) == "✗ boom"

	def test_error_takes_precedence_over_done(self):
		# 同时有 error 和 done → ✗（失败优先）
		assert _FakeAgent()._summarize_step_result([
			ActionResult(is_done=True, success=False, error="failed at done"),
		]) == "✗ failed at done"

	def test_max_items_one_keeps_only_first(self):
		"""max=1 的边界（防 items[-0:] 退化成全量 bug）。"""
		hist = AgentHistoryList(history=[_hist(i, goal=f"g-{i}") for i in range(5)])
		agent = _FakeAgent(history=hist, max_history_items=1)
		desc = agent._build_agent_history_description()
		assert "[... 4 previous steps omitted ...]" in desc
		assert "g-0" in desc
		for i in range(1, 5):
			assert f"g-{i}" not in desc


class TestSetHistoryMessage:
	def test_appends_typed_user_message(self):
		agent = _FakeAgent()
		agent._set_history_message("<agent_history>x</agent_history>")
		assert len(agent.messages) == 1
		assert agent.messages[0][_MSG_TYPE] == TYPE_USER
		assert agent.messages[0]["role"] == "user"

	def test_replaces_previous_history(self):
		"""连续两次 → TYPE_USER 恒 1 条（最新内容）。"""
		agent = _FakeAgent()
		agent._set_history_message("v1")
		agent._set_history_message("v2")
		user_msgs = [m for m in agent.messages if m.get(_MSG_TYPE) == TYPE_USER]
		assert len(user_msgs) == 1
		assert user_msgs[0]["content"] == "v2"

	def test_none_clears_history(self):
		agent = _FakeAgent()
		agent._set_history_message("v1")
		agent._set_history_message(None)
		assert not any(m.get(_MSG_TYPE) == TYPE_USER for m in agent.messages)

	def test_does_not_touch_state_or_context(self):
		from tree_walker.agent.step import TYPE_STATE, TYPE_CONTEXT
		agent = _FakeAgent()
		agent.messages = [
			{"role": "user", "content": "state", _MSG_TYPE: TYPE_STATE},
			{"role": "user", "content": "old-hist", _MSG_TYPE: TYPE_USER},
			{"role": "user", "content": "budget", _MSG_TYPE: TYPE_CONTEXT},
		]
		agent._set_history_message("new-hist")
		types = [m.get(_MSG_TYPE) for m in agent.messages]
		assert types.count(TYPE_USER) == 1
		assert types.count(TYPE_STATE) == 1
		assert types.count(TYPE_CONTEXT) == 1
		assert "new-hist" in [m["content"] for m in agent.messages]

	def test_disabled_is_noop(self):
		agent = _FakeAgent(enable_message_typing=False)
		agent._set_history_message("v1")
		assert agent.messages == []
