"""Tests for Judge evaluator — P3."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from tree_walker.agent.judge import (
    JudgeEvaluator,
    JudgementResult,
    _JUDGE_SYSTEM_PROMPT,
    _JUDGE_TOOL_SCHEMA,
)
from tree_walker.agent.views import ActionResult, AgentHistory, AgentHistoryList
from tree_walker.config import JudgeSettings, TruncationSettings


def _history_with(
    *,
    state_summary=None,
    model_output=None,
    result=None,
    step_number=1,
) -> AgentHistoryList:
    """Build a single-step history with optional state_summary / result overrides."""
    return AgentHistoryList(history=[AgentHistory(
        step_number=step_number,
        model_output=model_output or {
            "next_goal": "Go",
            "action": {"name": "navigate", "params": {}},
        },
        result=result or [ActionResult()],
        state_summary=state_summary,
    )])


class TestJudgementResult:
    def test_basic_creation(self):
        r = JudgementResult(reasoning="Looks good", verdict=True)
        assert r.verdict is True
        assert r.failure_reason is None
        assert r.impossible_task is False
        assert r.captcha is False

    def test_default_values(self):
        r = JudgementResult(verdict=False)
        assert r.reasoning is None
        assert r.failure_reason is None
        assert r.impossible_task is False
        assert r.captcha is False

    def test_failure_with_reason(self):
        r = JudgementResult(
            reasoning="Task incomplete",
            verdict=False,
            failure_reason="Missing required items",
        )
        assert r.failure_reason == "Missing required items"

    def test_captcha_flag(self):
        r = JudgementResult(reasoning="blocked by captcha", verdict=False, captcha=True)
        assert r.captcha is True


class TestJudgeSchema:
    def test_schema_has_captcha_property(self):
        props = _JUDGE_TOOL_SCHEMA["input_schema"]["properties"]
        assert "captcha" in props
        assert props["captcha"]["type"] == "boolean"

    def test_schema_required_fields(self):
        required = _JUDGE_TOOL_SCHEMA["input_schema"]["required"]
        assert "reasoning" in required
        assert "verdict" in required


class TestJudgeSystemPrompt:
    def test_prompt_says_missing_extract_is_not_hallucination(self):
        # 对标方案 §3.3:无显式 extract 动作不应单独作为幻觉判据
        assert "hallucination" in _JUDGE_SYSTEM_PROMPT.lower()
        assert "extract" in _JUDGE_SYSTEM_PROMPT.lower()

    def test_prompt_requires_page_excerpt_cross_check(self):
        assert "page excerpt" in _JUDGE_SYSTEM_PROMPT.lower()

    def test_prompt_keeps_high_standard(self):
        assert "high standard" in _JUDGE_SYSTEM_PROMPT.lower()


class TestJudgeEvaluatorSerializeHistory:
    def test_empty_history(self):
        history = AgentHistoryList()
        result = JudgeEvaluator(llm=None)._serialize_history(history)
        assert result == ""

    def test_single_step(self):
        h = AgentHistory(
            step_number=1,
            model_output={
                "next_goal": "Navigate to Google",
                "action": {"name": "navigate", "params": {"url": "https://google.com"}},
            },
            result=[ActionResult()],
        )
        history = AgentHistoryList(history=[h])
        result = JudgeEvaluator(llm=None)._serialize_history(history)
        assert "Step 1" in result
        assert "Navigate to Google" in result
        assert "navigate" in result

    def test_keeps_all_steps_no_middle_drop(self):
        # 对标方案 §3.3:删除"前3+后N丢中间步"逻辑,保留全部步
        steps = []
        for i in range(30):
            steps.append(AgentHistory(
                step_number=i + 1,
                model_output={
                    "next_goal": f"Goal {i + 1}",
                    "action": {"name": "click", "params": {"index": i}},
                },
                result=[ActionResult()],
            ))
        history = AgentHistoryList(history=steps)
        serialized = JudgeEvaluator(llm=None)._serialize_history(history)
        # Every step must appear — middle steps are no longer dropped.
        for i in range(1, 31):
            assert f"Step {i}" in serialized

    def test_includes_page_evidence(self):
        # 对标方案 §3.3:每步读 url/title/dom_excerpt
        history = _history_with(state_summary={
            "url": "https://www.google.com/search?q=test",
            "title": "test - Google Search",
            "dom_excerpt": "[0]<h3>Real Search Result Title</h3>",
        })
        serialized = JudgeEvaluator(llm=None)._serialize_history(history)
        assert "URL: https://www.google.com/search?q=test" in serialized
        assert "Title: test - Google Search" in serialized
        assert "Page excerpt: [0]<h3>Real Search Result Title</h3>" in serialized

    def test_uses_raw_extracted_content_not_display_truncated(self):
        # 对标方案 §3.3:result 改为直接读 extracted_content 原值,绕开 500 字截断
        long_text = "A" * 1500  # well over ActionResult.display_max_chars (500)
        history = _history_with(
            model_output={"next_goal": "Extract", "action": {"name": "done", "params": {}}},
            result=[ActionResult(is_done=True, success=True, extracted_content=long_text)],
        )
        serialized = JudgeEvaluator(llm=None)._serialize_history(history)
        assert long_text in serialized  # full content, not truncated to 500

    def test_result_includes_error(self):
        history = _history_with(result=[ActionResult(error="element not found")])
        serialized = JudgeEvaluator(llm=None)._serialize_history(history)
        assert "ERROR: element not found" in serialized

    def test_done_step_emits_page_excerpt_non_done_omits(self):
        # 对标修复方案 §3.3:dom_excerpt 非空(done 步)才输出 Page excerpt 行
        done_step = AgentHistory(
            step_number=1,
            model_output={"next_goal": "done", "action": {"name": "done", "params": {}}},
            result=[ActionResult(is_done=True, success=True, extracted_content="r")],
            state_summary={"url": "u", "title": "t", "dom_excerpt": "<real content>"},
        )
        mid_step = AgentHistory(
            step_number=2,
            model_output={"next_goal": "click", "action": {"name": "click", "params": {}}},
            result=[ActionResult()],
            state_summary={"url": "u2", "title": "t2"},  # no dom_excerpt key
        )
        history = AgentHistoryList(history=[done_step, mid_step])
        serialized = JudgeEvaluator(llm=None)._serialize_history(history)
        assert "Page excerpt: <real content>" in serialized  # done step has it
        assert serialized.count("Page excerpt:") == 1        # non-done step omits it


class TestJudgeEvaluatorBuildPrompt:
    def test_prompt_includes_task(self):
        history = _history_with()
        prompt = JudgeEvaluator(llm=None)._build_judge_prompt(
            task="Search for Python", history=history, final_result=None,
        )
        assert "Search for Python" in prompt
        assert "Execution Trace" in prompt

    def test_prompt_includes_final_result(self):
        history = _history_with(
            model_output={"next_goal": "Done", "action": {"name": "done", "params": {}}},
            result=[ActionResult(is_done=True, success=True, extracted_content="Found 5 items")],
        )
        prompt = JudgeEvaluator(llm=None)._build_judge_prompt(
            task="Find items", history=history, final_result="Found 5 items",
        )
        assert "Found 5 items" in prompt

    def test_empty_history_returns_none(self):
        history = AgentHistoryList()
        prompt = JudgeEvaluator(llm=None)._build_judge_prompt(
            task="Test", history=history, final_result=None,
        )
        assert prompt is None

    def test_long_trace_is_truncated_keep_tail(self):
        # 对标修复方案 §3.2:trace 超长时保尾(保 done/最近步),不保头
        big_excerpt = "X" * 500
        steps = []
        for i in range(40):
            steps.append(AgentHistory(
                step_number=i + 1,
                model_output={"next_goal": f"G{i}", "action": {"name": "click", "params": {}}},
                result=[ActionResult()],
                state_summary={"url": "https://x.com", "title": "t", "dom_excerpt": big_excerpt},
            ))
        history = AgentHistoryList(history=steps)
        judge = JudgeEvaluator(llm=None, settings=JudgeSettings(trace_max_chars=1000))
        prompt = judge._build_judge_prompt(task="T", history=history, final_result=None)
        assert prompt is not None
        assert "[trace truncated, kept most recent steps]" in prompt
        assert "Step 40" in prompt      # tail (done/most-recent) is kept
        assert "Step 1" not in prompt   # head is dropped


class TestJudgeEvaluatorParseResponse:
    def test_parse_valid_json(self):
        judge = JudgeEvaluator(llm=None)
        result = judge._parse_response(
            'Here is my evaluation:\n```json\n'
            '{"reasoning": "Task completed", "verdict": true, "failure_reason": null, "impossible_task": false}\n'
            '```'
        )
        assert result.verdict is True
        assert result.reasoning == "Task completed"

    def test_parse_json_with_false_verdict(self):
        judge = JudgeEvaluator(llm=None)
        result = judge._parse_response(
            '{"reasoning": "Incomplete", "verdict": false, "failure_reason": "Missing data", "impossible_task": false}'
        )
        assert result.verdict is False
        assert result.failure_reason == "Missing data"

    def test_parse_fallback_for_non_json(self):
        judge = JudgeEvaluator(llm=None)
        result = judge._parse_response("The agent completed the task successfully.")
        assert result.reasoning is not None

    def test_parse_captcha_field(self):
        judge = JudgeEvaluator(llm=None)
        result = judge._parse_response(
            '{"reasoning": "captcha", "verdict": false, "captcha": true}'
        )
        assert result.captcha is True


class _FakeToolUseBlock:
    def __init__(self, input_dict):
        self.type = "tool_use"
        self.input = input_dict


class _FakeResponse:
    def __init__(self, blocks):
        self.content = blocks


def _fake_llm(response):
    """LLM-like mock whose client.messages.create returns ``response``."""
    llm = MagicMock()
    llm.model = "m"
    llm.max_tokens = 10
    llm.client.messages.create.return_value = response
    return llm


class TestJudgeEvaluatorJudge:
    """Covers judge()'s LLM-call path: success, captcha, no-tool-use, error, empty."""

    def test_parses_tool_use_response(self):
        response = _FakeResponse([_FakeToolUseBlock({
            "reasoning": "ok", "verdict": True,
        })])
        result = asyncio.run(JudgeEvaluator(llm=_fake_llm(response)).judge(
            task="t", history=_history_with(),
        ))
        assert result is not None
        assert result.verdict is True
        assert result.captcha is False

    def test_parses_captcha_true(self):
        response = _FakeResponse([_FakeToolUseBlock({
            "reasoning": "blocked", "verdict": False, "captcha": True,
            "failure_reason": "captcha wall",
        })])
        result = asyncio.run(JudgeEvaluator(llm=_fake_llm(response)).judge(
            task="t", history=_history_with(),
        ))
        assert result.verdict is False
        assert result.captcha is True
        assert result.failure_reason == "captcha wall"

    def test_returns_none_when_no_tool_use_block(self):
        response = _FakeResponse([SimpleNamespace(type="text", text="nope")])
        result = asyncio.run(JudgeEvaluator(llm=_fake_llm(response)).judge(
            task="t", history=_history_with(),
        ))
        assert result is None

    def test_retries_once_on_empty_then_succeeds(self):
        """B3-3：空响应（无 tool_use 块）先 nudge 重试一次——批次二验收两跑均现
        "Judge returned no tool_use block"，仿 client.py R1 的做法。"""
        empty = _FakeResponse([SimpleNamespace(type="text", text="thinking aloud")])
        ok = _FakeResponse([_FakeToolUseBlock({"reasoning": "ok", "verdict": True})])
        llm = _fake_llm(None)
        llm.client.messages.create.side_effect = [empty, ok]

        result = asyncio.run(JudgeEvaluator(llm=llm).judge(
            task="t", history=_history_with(),
        ))

        assert result is not None
        assert result.verdict is True
        assert llm.client.messages.create.call_count == 2

    def test_returns_none_after_retry_exhausted(self):
        """连续两次空响应 → 放弃返回 None（有界，不无限重试）。"""
        empty = _FakeResponse([SimpleNamespace(type="text", text="still no tool")])
        llm = _fake_llm(None)
        llm.client.messages.create.side_effect = [empty, empty]

        result = asyncio.run(JudgeEvaluator(llm=llm).judge(
            task="t", history=_history_with(),
        ))

        assert result is None
        assert llm.client.messages.create.call_count == 2

    def test_returns_none_on_llm_exception(self):
        llm = _fake_llm(None)
        llm.client.messages.create.side_effect = RuntimeError("boom")
        result = asyncio.run(JudgeEvaluator(llm=llm).judge(
            task="t", history=_history_with(),
        ))
        assert result is None

    def test_returns_none_for_empty_history(self):
        result = asyncio.run(JudgeEvaluator(llm=_fake_llm(None)).judge(
            task="t", history=AgentHistoryList(),
        ))
        assert result is None


# ── Non-blocking loop（issue #163）─────────────────────────────────────


class TestJudgeNonBlocking:
    """judge 的同步 ``messages.create`` 必须经 ``asyncio.to_thread``——真机曾观测 judge
    阶段 tw-web 全部端点无响应 4-5 分钟。判据同 test_llm_client：慢 create 期间
    ticker 协程持续推进。"""

    def test_judge_offloads_create_to_thread(self):
        import time

        response = _FakeResponse([_FakeToolUseBlock({"reasoning": "ok", "verdict": True})])

        def slow_create(*args, **kwargs):
            time.sleep(0.2)
            return response

        llm = _fake_llm(None)
        llm.client.messages.create.side_effect = slow_create
        ticks = 0

        async def ticker():
            nonlocal ticks
            for _ in range(30):
                await asyncio.sleep(0.01)
                ticks += 1

        async def main():
            task = asyncio.create_task(ticker())
            result = await JudgeEvaluator(llm=llm).judge(task="t", history=_history_with())
            await task
            return result

        result = asyncio.run(main())
        assert result is not None and result.verdict is True
        assert ticks >= 15  # 若 create 阻塞 loop，ticker 几乎不动


class TestConfigDefaults:
    """方案 §3.1:Judge 默认开启 + 新截断阈值默认值。"""

    def test_judge_enabled_by_default(self):
        assert JudgeSettings().enabled is True

    def test_judge_trace_max_chars_default(self):
        assert JudgeSettings().trace_max_chars == 40000

    def test_truncation_dom_excerpt_default(self):
        assert TruncationSettings().dom_excerpt_max_chars == 2000
