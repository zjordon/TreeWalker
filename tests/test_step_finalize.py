"""Tests for StepPipeline._finalize — DOM excerpt persistence (Judge 改造 §3.2)."""

import time

import pytest

from tree_walker.agent.step import StepPipeline
from tree_walker.agent.views import ActionResult, AgentHistoryList, AgentState
from tree_walker.browser.views import BrowserStateSummary, SerializedDOMState
from tree_walker.config import TruncationSettings


class _FakeAgent(StepPipeline):
    """Minimal agent-like object exposing only what _finalize touches.

    Subclasses StepPipeline to inherit the recording helpers
    (``_project_interacted_elements`` / ``_build_step_metadata``) added for
    history replay, while keeping a hand-rolled ``__init__`` and a stubbed
    ``_log_step_completion_summary``. Called as ``StepPipeline._finalize(fake, ...)``.
    """

    def __init__(self, truncation=None):
        self.state = AgentState()
        self.history = AgentHistoryList()
        self._step_start_time = time.time()
        self._truncation = truncation or TruncationSettings()
        self._obs_bus = None

    def _log_step_completion_summary(self, results):
        pass  # stubbed out — not under test


def _browser_state(tree_text):
    """Build a BrowserStateSummary; tree_text=None yields dom_state=None."""
    dom_state = None
    if tree_text is not None:
        dom_state = SerializedDOMState(
            _root=None, selector_map={}, element_tree_text=tree_text,
        )
    return BrowserStateSummary(
        url="https://example.com", title="Example", dom_state=dom_state,
    )


class TestFinalizeDomExcerpt:
    async def _run(self, agent, tree_text, *, done=False):
        model_output = {
            "next_goal": "g",
            "action": {"name": "done" if done else "click", "params": {}},
        }
        results = [ActionResult(is_done=True)] if done else [ActionResult()]
        await StepPipeline._finalize(agent, _browser_state(tree_text), model_output, results)
        return agent.history.history[-1]

    @pytest.mark.asyncio
    async def test_done_step_persists_dom_excerpt(self):
        last = await self._run(_FakeAgent(), "<body>real content here</body>", done=True)
        assert last.state_summary["dom_excerpt"] == "<body>real content here</body>"

    @pytest.mark.asyncio
    async def test_non_done_step_has_no_dom_excerpt(self):
        # 对标修复方案 §3.1:仅 done 步存 dom_excerpt,其余步只 url/title
        last = await self._run(_FakeAgent(), "<body>real content here</body>", done=False)
        assert "dom_excerpt" not in last.state_summary
        # url/title 仍然记录
        assert last.state_summary["url"] == "https://example.com"

    @pytest.mark.asyncio
    async def test_done_step_truncates_to_dom_excerpt_max_chars(self):
        agent = _FakeAgent(truncation=TruncationSettings(dom_excerpt_max_chars=10))
        last = await self._run(agent, "X" * 100, done=True)
        assert len(last.state_summary["dom_excerpt"]) == 10

    @pytest.mark.asyncio
    async def test_done_step_empty_string_when_dom_state_is_none(self):
        # 断路器开启 (EMPTY_DOM_STATE) 或 dom_state 缺失 → 兜底空串
        last = await self._run(_FakeAgent(), None, done=True)
        assert last.state_summary["dom_excerpt"] == ""

    @pytest.mark.asyncio
    async def test_state_summary_none_when_browser_state_is_none(self):
        agent = _FakeAgent()
        model_output = {"next_goal": "g", "action": {"name": "click", "params": {}}}
        await StepPipeline._finalize(agent, None, model_output, [ActionResult()])
        assert agent.history.history[-1].state_summary is None

    @pytest.mark.asyncio
    async def test_also_records_url_and_title(self):
        last = await self._run(_FakeAgent(), "content", done=False)
        assert last.state_summary["url"] == "https://example.com"
        assert last.state_summary["title"] == "Example"

    @pytest.mark.asyncio
    async def test_finalize_no_longer_advances_step_counter(self):
        # review3 #8（PR #174）：n_steps 递增的唯一所有者是 _step 的 finally
        # （紧跟被守卫的 _finalize 调用之后）——_finalize 自身不再递增。
        # 计数器边界行为（_finalize 抛异常仍递增）由
        # test_step_malformed_action.py::TestStepFinallyGuard 锚定。
        agent = _FakeAgent()
        await self._run(agent, "content", done=True)
        assert agent.state.n_steps == 0
