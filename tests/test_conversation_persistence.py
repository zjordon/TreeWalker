"""Tests for P1-3 conversation persistence (StepPipeline._save_conversation).

Aligns with browser-use service.py:1713-1723 (save_conversation_path).
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

from tree_walker.agent.views import AgentState


def _make_agent(save_path: str = "", session_id: str = "abc123") -> Any:
	"""Minimal fake agent exposing what _save_conversation touches."""
	agent = MagicMock()
	agent.state = AgentState()
	agent.state.n_steps = 7
	agent._save_conversation_path = save_path
	agent._obs_session_id = session_id
	agent.llm = MagicMock()
	agent.llm.model = "test-model"
	return agent


class TestSaveConversation:
	"""P1-3: _save_conversation dumps input messages + model_output."""

	def test_empty_path_does_not_write(self, tmp_path):
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent(save_path="")
		StepPipeline._save_conversation(agent, [{"role": "user", "content": "hi"}], {"action": {}})
		# tmp_path is untouched — the method short-circuited on empty path
		assert list(tmp_path.iterdir()) == []

	def test_normal_writes_file_with_content(self, tmp_path):
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent(save_path=str(tmp_path), session_id="sess1")
		messages = [
			{"role": "user", "content": "do task"},
			{"role": "assistant", "content": "ok"},
		]
		model_output = {"action": {"name": "click", "params": {"index": 1}}, "next_goal": "click"}
		StepPipeline._save_conversation(agent, messages, model_output)

		files = list(tmp_path.glob("conversation_sess1_7.txt"))
		assert len(files) == 1
		text = files[0].read_text(encoding="utf-8")
		assert "do task" in text
		assert "ok" in text
		assert "--- model_output ---" in text
		# model_output rendered as JSON
		assert '"name": "click"' in text

	def test_io_error_swallowed_and_warned(self, tmp_path, caplog):
		"""Disk failure must not propagate; best-effort warning only."""
		from tree_walker.agent.step import StepPipeline

		# save_path points at an existing FILE → mkdir(exist_ok=True) raises FileExistsError
		file_path = tmp_path / "afile"
		file_path.write_text("x", encoding="utf-8")
		agent = _make_agent(save_path=str(file_path), session_id="s")

		with caplog.at_level(logging.WARNING):
			# must not raise
			StepPipeline._save_conversation(agent, [{"role": "user", "content": "hi"}], {"action": {}})
		assert "Failed to save conversation" in caplog.text

	def test_falls_back_to_object_id_when_no_session(self, tmp_path):
		"""When _obs_session_id is empty (observability off), filename uses id(self)."""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent(save_path=str(tmp_path), session_id="")
		StepPipeline._save_conversation(agent, [{"role": "user", "content": "hi"}], {"action": {}})

		files = list(tmp_path.glob("conversation_*.txt"))
		assert len(files) == 1
		# conversation_<hex_id>_7.txt — middle segment is non-empty hex
		parts = files[0].name.replace(".txt", "").split("_")
		assert parts[0] == "conversation"
		assert parts[1] != ""
		assert parts[2] == "7"

	def test_multiple_steps_do_not_overwrite(self, tmp_path):
		"""Each step writes its own file (step number in name)."""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent(save_path=str(tmp_path), session_id="sess")
		for step in (1, 2, 3):
			agent.state.n_steps = step
			StepPipeline._save_conversation(
				agent, [{"role": "user", "content": f"step{step}"}], {"action": {}}
			)

		files = sorted(tmp_path.glob("conversation_sess_*.txt"))
		assert len(files) == 3
		assert {f.name for f in files} == {
			"conversation_sess_1.txt",
			"conversation_sess_2.txt",
			"conversation_sess_3.txt",
		}

	def test_dump_faithful_to_input_and_no_type_leak(self, tmp_path):
		"""Dump reflects the input messages verbatim; _save_conversation adds no _type keys."""
		from tree_walker.agent.step import StepPipeline

		agent = _make_agent(save_path=str(tmp_path), session_id="s")
		messages = [{"role": "user", "content": "literal content"}]
		StepPipeline._save_conversation(agent, messages, {"action": {}})

		text = (tmp_path / "conversation_s_7.txt").read_text(encoding="utf-8")
		assert "literal content" in text
		# the method itself does not inject internal _type metadata
		assert "_type" not in text
