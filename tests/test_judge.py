"""Tests for Judge evaluator — P3."""

import pytest

from tree_walker.agent.judge import JudgeEvaluator, JudgementResult
from tree_walker.agent.views import ActionResult, AgentHistory, AgentHistoryList


class TestJudgementResult:
    def test_basic_creation(self):
        r = JudgementResult(reasoning="Looks good", verdict=True)
        assert r.verdict is True
        assert r.failure_reason is None
        assert r.impossible_task is False

    def test_default_values(self):
        r = JudgementResult(verdict=False)
        assert r.reasoning is None
        assert r.failure_reason is None
        assert r.impossible_task is False

    def test_failure_with_reason(self):
        r = JudgementResult(
            reasoning="Task incomplete",
            verdict=False,
            failure_reason="Missing required items",
        )
        assert r.failure_reason == "Missing required items"


class TestJudgeEvaluatorSerializeHistory:
    def test_empty_history(self):
        history = AgentHistoryList()
        result = JudgeEvaluator(llm=None)._serialize_history(history, 20)
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
        result = JudgeEvaluator(llm=None)._serialize_history(history, 20)
        assert "Step 1" in result
        assert "Navigate to Google" in result
        assert "navigate" in result

    def test_truncation_with_many_steps(self):
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
        serialized = JudgeEvaluator(llm=None)._serialize_history(history, 10)
        # Should include first 3 and last 7
        assert "Step 1" in serialized
        assert "Step 2" in serialized
        assert "Step 3" in serialized
        assert "Step 30" in serialized
        assert "Step 5" not in serialized  # middle steps should be excluded


class TestJudgeEvaluatorBuildPrompt:
    def test_prompt_includes_task(self):
        history = AgentHistoryList(history=[AgentHistory(
            step_number=1,
            model_output={"next_goal": "Go", "action": {"name": "navigate", "params": {}}},
            result=[ActionResult()],
        )])
        prompt = JudgeEvaluator(llm=None)._build_judge_prompt(
            task="Search for Python", history=history, final_result=None, max_history_steps=20,
        )
        assert "Search for Python" in prompt
        assert "Execution Trace" in prompt

    def test_prompt_includes_final_result(self):
        history = AgentHistoryList(history=[AgentHistory(
            step_number=1,
            model_output={"next_goal": "Done", "action": {"name": "done", "params": {}}},
            result=[ActionResult(is_done=True, success=True, extracted_content="Found 5 items")],
        )])
        prompt = JudgeEvaluator(llm=None)._build_judge_prompt(
            task="Find items", history=history, final_result="Found 5 items", max_history_steps=20,
        )
        assert "Found 5 items" in prompt

    def test_empty_history_returns_none(self):
        history = AgentHistoryList()
        prompt = JudgeEvaluator(llm=None)._build_judge_prompt(
            task="Test", history=history, final_result=None, max_history_steps=20,
        )
        assert prompt is None


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
