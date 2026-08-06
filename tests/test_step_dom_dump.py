"""临时调试开关 _maybe_dump_step_dom 的单测（issue #157）。

env AGENT_DEBUG_DUMP_DIR 控制：未设 = no-op；设了 = 每步写 step_NN.txt，
含 JS probe（实际 DOM 表单可见性，对照 dom_snapshot 是否漏抓）+ element_tree_text。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tree_walker.agent.step import StepPipeline
from tests.test_multi_act import _make_agent


def _bs(tree: str = "TREE", url: str = "https://example.com", dom_state=None) -> MagicMock:
	bs = MagicMock()
	bs.url = url
	bs.dom_state = dom_state if dom_state is not None else MagicMock()
	bs.dom_state.element_tree_text = tree
	return bs


def _wire_browser(agent, probe_return: str = '{"formControls": 8}') -> None:
	"""给 fake agent 装上 browser + mock _dump_js_probe（_maybe_dump_step_dom 测试用）。"""
	agent.browser = MagicMock()
	agent.browser.current_session_id = "test"
	agent._dump_js_probe = AsyncMock(return_value=probe_return)


class TestMaybeDumpStepDom:
	@pytest.mark.asyncio
	async def test_env_unset_is_noop(self, tmp_path, monkeypatch):
		"""env 未设 → 不写任何文件（默认状态，对探索零影响）。"""
		monkeypatch.delenv("AGENT_DEBUG_DUMP_DIR", raising=False)
		agent = _make_agent()
		await StepPipeline._maybe_dump_step_dom(agent, _bs())
		assert list(tmp_path.iterdir()) == []

	@pytest.mark.asyncio
	async def test_env_set_writes_step_file_with_js_probe(self, tmp_path, monkeypatch):
		"""env 设 → 写 step_NN.txt，含 JS probe + element_tree_text + 元信息。"""
		monkeypatch.setenv("AGENT_DEBUG_DUMP_DIR", str(tmp_path))
		agent = _make_agent()
		_wire_browser(agent)
		agent.state.n_steps = 2
		await StepPipeline._maybe_dump_step_dom(
			agent, _bs(tree="[2]<input name=name />", url="https://pingkai.cn/contact"),
		)
		files = sorted(tmp_path.iterdir())
		assert [f.name for f in files] == ["step_02.txt"]
		content = files[0].read_text(encoding="utf-8")
		assert "step 2" in content
		assert "https://pingkai.cn/contact" in content
		assert "JS probe" in content
		assert '"formControls": 8' in content
		assert "[2]<input name=name />" in content

	@pytest.mark.asyncio
	async def test_none_dom_state_does_not_raise(self, tmp_path, monkeypatch):
		"""dom_state 为 None（DOM 采集熔断）→ 不抛、写元信息 + JS probe，tree 为空。"""
		monkeypatch.setenv("AGENT_DEBUG_DUMP_DIR", str(tmp_path))
		agent = _make_agent()
		_wire_browser(agent)
		await StepPipeline._maybe_dump_step_dom(agent, _bs(dom_state=None))
		out = (tmp_path / "step_00.txt").read_text(encoding="utf-8")
		assert "step 0" in out

	@pytest.mark.asyncio
	async def test_creates_nested_dump_dir(self, tmp_path, monkeypatch):
		"""dump_dir 不存在 → 自动创建。"""
		nested = tmp_path / "debug" / "dump"
		monkeypatch.setenv("AGENT_DEBUG_DUMP_DIR", str(nested))
		agent = _make_agent()
		_wire_browser(agent)
		await StepPipeline._maybe_dump_step_dom(agent, _bs())
		assert (nested / "step_00.txt").exists()

	@pytest.mark.asyncio
	async def test_js_probe_failure_still_writes_tree(self, tmp_path, monkeypatch):
		"""JS probe 失败（_dump_js_probe 返回错误串）→ dump 仍写入 element_tree_text。"""
		monkeypatch.setenv("AGENT_DEBUG_DUMP_DIR", str(tmp_path))
		agent = _make_agent()
		_wire_browser(agent, probe_return="<js probe failed: RuntimeError('cdp down')>")
		await StepPipeline._maybe_dump_step_dom(agent, _bs(tree="TREE"))
		out = (tmp_path / "step_00.txt").read_text(encoding="utf-8")
		assert "js probe failed" in out
		assert "TREE" in out


class TestDumpJsProbe:
	"""_dump_js_probe 真实逻辑（unbound 调用，mock browser.client）。"""

	def _agent_with_cdp(self, return_value=None, side_effect=None):
		agent = _make_agent()
		agent.browser = MagicMock()
		agent.browser.current_session_id = "s1"
		mock_eval = AsyncMock()
		if side_effect is not None:
			mock_eval.side_effect = side_effect
		else:
			mock_eval.return_value = return_value or {"result": {"value": '{"formControls": 8}'}}
		agent.browser.client.send.Runtime.evaluate = mock_eval
		return agent

	@pytest.mark.asyncio
	async def test_calls_runtime_evaluate_and_returns_value(self):
		agent = self._agent_with_cdp(return_value={"result": {"value": '{"formControls": 7}'}})
		out = await StepPipeline._dump_js_probe(agent)
		assert '"formControls": 7' in out
		agent.browser.client.send.Runtime.evaluate.assert_awaited_once()

	@pytest.mark.asyncio
	async def test_swallows_cdp_error_returns_marker(self):
		agent = self._agent_with_cdp(side_effect=RuntimeError("cdp down"))
		out = await StepPipeline._dump_js_probe(agent)
		assert "js probe failed" in out
