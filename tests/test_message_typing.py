"""Tests for P0 message typing — state replacement / context clearing / _type stripping.

对齐方案：``docs/agent-loop-optimize/01-准备上下文对齐browser-use方案.md`` 第 4 章。
对齐 browser-use ``MessageManager``：``_set_message_with_type('state')`` 每步替换
唯一 state 消息；``prepare_step_state()`` 开头 ``context_messages.clear()`` 每步清理
注入提示。TreeWalker 用轻量 ``_type`` 元数据等价表达，``_trim_messages`` 边界剥除。
"""

from tree_walker.agent.step import (
	StepPipeline,
	_MSG_TYPE,
	TYPE_STATE,
	TYPE_CONTEXT,
	TYPE_USER,
	TYPE_ASSISTANT,
)


class _FakeAgent(StepPipeline):
	"""Minimal agent-like object exposing only what the message-typing helpers
	touch. Subclasses ``StepPipeline`` to inherit ``_strip_type`` /
	``_set_state_message`` / ``_clear_context_messages`` / ``_add_context_message``.
	"""

	def __init__(self, enable_message_typing=True, messages=None):
		self.messages = messages if messages is not None else []
		self._enable_message_typing = enable_message_typing


class TestSetStateMessage:
	def test_keeps_last_two_states(self):
		"""连续 _set_state_message 后 messages 里 TYPE_STATE 恒为 2 条（previous + current），
		让 LLM 能 before/after 对比（修复 P0 封面上传回归：纯替换让模型看不到画布新出现的
		img 节点而被残留占位文误导）。更老的 state 被删。"""
		agent = _FakeAgent()
		for i in range(5):
			agent._set_state_message(f"dom-step-{i}")
		state_msgs = [m for m in agent.messages if m.get(_MSG_TYPE) == TYPE_STATE]
		assert len(state_msgs) == 2
		assert state_msgs[-1]["content"] == "dom-step-4"  # 当前
		assert state_msgs[-2]["content"] == "dom-step-3"  # 上一步
		assert len(agent.messages) == 2  # 仅 2 份 state，更老的已删

	def test_first_step_no_error(self):
		"""首步（无旧 state）不报错。"""
		agent = _FakeAgent()
		agent._set_state_message("first-dom")
		assert len(agent.messages) == 1
		assert agent.messages[0][_MSG_TYPE] == TYPE_STATE
		assert agent.messages[0]["content"] == "first-dom"

	def test_disabled_appends_without_type(self):
		"""enable_message_typing=False：回退原始 append（无 _type 标记）。"""
		agent = _FakeAgent(enable_message_typing=False)
		agent._set_state_message("a")
		assert _MSG_TYPE not in agent.messages[0]
		assert agent.messages[0]["content"] == "a"

	def test_disabled_accumulates(self):
		"""enable_message_typing=False：不替换，每步累积（旧行为，向后兼容）。"""
		agent = _FakeAgent(enable_message_typing=False)
		agent._set_state_message("a")
		agent._set_state_message("b")
		assert len(agent.messages) == 2


class TestClearContextMessages:
	def test_clears_only_context_messages(self):
		"""_clear_context_messages 只删 context，保留 state/assistant/user。"""
		agent = _FakeAgent()
		agent.messages = [
			{"role": "user", "content": "task", _MSG_TYPE: TYPE_USER},
			{"role": "user", "content": "state", _MSG_TYPE: TYPE_STATE},
			{"role": "user", "content": "budget", _MSG_TYPE: TYPE_CONTEXT},
			{"role": "assistant", "content": "resp", _MSG_TYPE: TYPE_ASSISTANT},
			{"role": "user", "content": "last-step", _MSG_TYPE: TYPE_CONTEXT},
		]
		agent._clear_context_messages()
		types = [m.get(_MSG_TYPE) for m in agent.messages]
		assert TYPE_CONTEXT not in types
		assert types.count(TYPE_STATE) == 1
		assert TYPE_ASSISTANT in types
		assert TYPE_USER in types
		assert len(agent.messages) == 3

	def test_disabled_is_noop(self):
		"""enable_message_typing=False：no-op（保持原始累积行为）。"""
		agent = _FakeAgent(enable_message_typing=False)
		agent.messages = [{"role": "user", "content": "x", _MSG_TYPE: TYPE_CONTEXT}]
		agent._clear_context_messages()
		assert len(agent.messages) == 1

	def test_no_context_messages_is_noop(self):
		agent = _FakeAgent()
		agent.messages = [{"role": "user", "content": "s", _MSG_TYPE: TYPE_STATE}]
		agent._clear_context_messages()
		assert len(agent.messages) == 1


class TestAddContextMessage:
	def test_marks_context_type(self):
		agent = _FakeAgent()
		agent._add_context_message("BUDGET WARNING")
		assert agent.messages[0][_MSG_TYPE] == TYPE_CONTEXT
		assert agent.messages[0]["content"] == "BUDGET WARNING"
		assert agent.messages[0]["role"] == "user"

	def test_disabled_no_type(self):
		agent = _FakeAgent(enable_message_typing=False)
		agent._add_context_message("BUDGET WARNING")
		assert _MSG_TYPE not in agent.messages[0]


class TestStripType:
	def test_removes_type_key(self):
		out = StepPipeline._strip_type({"role": "user", "content": "x", _MSG_TYPE: TYPE_STATE})
		assert _MSG_TYPE not in out
		assert out == {"role": "user", "content": "x"}

	def test_no_type_key_returns_equivalent(self):
		msg = {"role": "user", "content": "x"}
		out = StepPipeline._strip_type(msg)
		assert out == {"role": "user", "content": "x"}

	def test_preserves_other_keys(self):
		out = StepPipeline._strip_type(
			{"role": "assistant", "content": "x", "extra": 1, _MSG_TYPE: TYPE_ASSISTANT}
		)
		assert out == {"role": "assistant", "content": "x", "extra": 1}


class TestSimulatedStepCycle:
	"""模拟 _prepare_context 多步循环：clear → set_state → add_context。"""

	def test_five_steps_keep_two_states_no_context_accumulation(self):
		"""连续 5 步后 state 恒 2 条（最近两步：dom-3 + dom-4）、context 不累积（每步清后灌）。"""
		agent = _FakeAgent()
		for i in range(5):
			agent._clear_context_messages()
			agent._set_state_message(f"dom-{i}")
			agent._add_context_message(f"budget-{i}")
		state_msgs = [m for m in agent.messages if m.get(_MSG_TYPE) == TYPE_STATE]
		context_msgs = [m for m in agent.messages if m.get(_MSG_TYPE) == TYPE_CONTEXT]
		assert len(state_msgs) == 2
		assert state_msgs[-1]["content"] == "dom-4"  # 当前步
		assert state_msgs[-2]["content"] == "dom-3"  # 上一步
		assert len(context_msgs) == 1
		assert context_msgs[0]["content"] == "budget-4"

	def test_previous_step_context_cleared_next_step(self):
		"""第 N 步注入 budget，第 N+1 步（无注入）无 context 残留。"""
		agent = _FakeAgent()
		# step N（有注入）
		agent._clear_context_messages()
		agent._set_state_message("dom-N")
		agent._add_context_message("BUDGET WARNING")
		assert any(m.get("content") == "BUDGET WARNING" for m in agent.messages)
		# step N+1（无注入）
		agent._clear_context_messages()
		agent._set_state_message("dom-N+1")
		assert not any(m.get("content") == "BUDGET WARNING" for m in agent.messages)
		assert len([m for m in agent.messages if m.get(_MSG_TYPE) == TYPE_CONTEXT]) == 0

	def test_force_done_context_cleared_next_step(self):
		"""最后一步 force_done 注入 context，下一步入口清理后无残留。"""
		agent = _FakeAgent()
		agent._clear_context_messages()
		agent._set_state_message("dom")
		agent._add_context_message("LAST STEP: you must call done")
		assert any(m.get("content") == "LAST STEP: you must call done" for m in agent.messages)
		# 下一步入口清理
		agent._clear_context_messages()
		assert not any(m.get("content") == "LAST STEP: you must call done" for m in agent.messages)


class TestTrimMessagesStripsType:
	"""``_trim_messages`` 在 ``Agent`` 类（agent.py），返回前剥 ``_type``。
	用继承 ``Agent`` 但跳过 ``__init__`` 的轻量 fake。
	"""

	def _agent(self, messages=None, compactor=None):
		from tree_walker.agent.agent import Agent

		class _AgentWithTrim(Agent):
			def __init__(self, messages=None, compactor=None):
				self.messages = messages if messages is not None else []
				self._compactor = compactor

		return _AgentWithTrim(messages=messages, compactor=compactor)

	def test_strips_type_from_all_messages(self):
		"""_trim_messages 返回的每个 dict 不含 _type（送 SDK 不报错）。"""
		agent = self._agent(messages=[
			{"role": "user", "content": "s", _MSG_TYPE: TYPE_STATE},
			{"role": "assistant", "content": "a", _MSG_TYPE: TYPE_ASSISTANT},
			{"role": "user", "content": "c", _MSG_TYPE: TYPE_CONTEXT},
		])
		trimmed = agent._trim_messages()
		assert all(_MSG_TYPE not in m for m in trimmed)
		assert len(trimmed) == 3
		assert trimmed[0] == {"role": "user", "content": "s"}
		assert trimmed[1] == {"role": "assistant", "content": "a"}

	def test_under_limit_preserves_all(self):
		agent = self._agent(messages=[{"role": "user", "content": str(i)} for i in range(5)])
		trimmed = agent._trim_messages(max_messages=20)
		assert len(trimmed) == 5

	def test_over_limit_trims_tail(self):
		agent = self._agent(messages=[{"role": "user", "content": str(i)} for i in range(10)])
		trimmed = agent._trim_messages(max_messages=3)
		assert len(trimmed) == 3
		assert trimmed[-1]["content"] == "9"

	def test_compactor_mode_strips_type_without_trim(self):
		"""compactor 启用时按全量返回（除非超 max*3），但仍剥 _type。"""
		agent = self._agent(
			messages=[{"role": "user", "content": "s", _MSG_TYPE: TYPE_STATE}],
			compactor=object(),  # 非 None 触发 compactor 分支
		)
		trimmed = agent._trim_messages(max_messages=20)
		assert len(trimmed) == 1
		assert _MSG_TYPE not in trimmed[0]

	def test_compactor_mode_safety_limit_trims_and_strips(self):
		"""compactor 模式下超 max*3 安全上限时裁尾 + 剥 _type。"""
		msgs = [{"role": "user", "content": f"m{i}", _MSG_TYPE: TYPE_STATE} for i in range(10)]
		agent = self._agent(messages=msgs, compactor=object())
		trimmed = agent._trim_messages(max_messages=2)  # safety = 2*3 = 6
		assert len(trimmed) == 6
		assert all(_MSG_TYPE not in m for m in trimmed)
		assert trimmed[-1]["content"] == "m9"
