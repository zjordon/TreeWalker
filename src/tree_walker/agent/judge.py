"""Independent LLM judge — post-execution trace review."""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from tree_walker.agent.views import AgentHistoryList

logger = logging.getLogger(__name__)


class JudgementResult(BaseModel):
    reasoning: str | None = None
    verdict: bool = Field(description="Whether the trace was successful or not")
    failure_reason: str | None = None
    impossible_task: bool = False


_JUDGE_SYSTEM_PROMPT = """\
You are an independent task reviewer. Your job is to evaluate whether a browser \
automation agent truly completed the user's task by reviewing its execution trace.

## Evaluation Criteria (by priority)

1. **Task satisfaction** (most important) — did the agent accomplish every \
specific requirement the user asked for?
2. **Output quality** — is the result formatted correctly and complete?
3. **Action effectiveness** — did the browser interactions actually achieve \
their intended effects?

## Key Instructions

- Do NOT blindly trust the agent's self-reported success.
- Verify each critical step was actually executed (not just attempted).
- Apply a very high standard for task completion.
- If the task was impossible to complete (e.g., page down, login blocked), \
set impossible_task=true and verdict=false.
"""


_JUDGE_TOOL_SCHEMA = {
    "name": "agent_response",
    "description": "Submit your evaluation of whether the agent completed the task.",
    "input_schema": {
        "type": "object",
        "required": ["reasoning", "verdict"],
        "properties": {
            "reasoning": {
                "type": "string",
                "description": "Step-by-step reasoning about whether the task was truly completed.",
            },
            "verdict": {
                "type": "boolean",
                "description": "True if the agent successfully completed the task, False otherwise.",
            },
            "failure_reason": {
                "type": "string",
                "description": "If verdict is false, explain what went wrong or what was missing.",
            },
            "impossible_task": {
                "type": "boolean",
                "description": "True if the task was inherently impossible to complete.",
            },
        },
    },
}


class JudgeEvaluator:
    """Use an independent LLM to review an agent's execution trace."""

    def __init__(self, llm) -> None:
        self._llm = llm

    async def judge(
        self,
        task: str,
        history: AgentHistoryList,
        final_result: str | None = None,
        max_history_steps: int = 20,
    ) -> JudgementResult | None:
        prompt = self._build_judge_prompt(task, history, final_result, max_history_steps)
        if not prompt:
            return None

        try:
            response = self._llm.client.messages.create(
                model=self._llm.model,
                max_tokens=self._llm.max_tokens,
                system=_JUDGE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                tools=[_JUDGE_TOOL_SCHEMA],
                tool_choice={"type": "tool", "name": "agent_response"},
            )

            for block in response.content:
                if getattr(block, "type", None) == "tool_use":
                    data = block.input
                    return JudgementResult(
                        reasoning=data.get("reasoning"),
                        verdict=bool(data.get("verdict", False)),
                        failure_reason=data.get("failure_reason"),
                        impossible_task=bool(data.get("impossible_task", False)),
                    )

            logger.warning("Judge returned no tool_use block")
            return None
        except Exception:
            logger.exception("Judge evaluation failed")
            return None

    def _build_judge_prompt(
        self,
        task: str,
        history: AgentHistoryList,
        final_result: str | None,
        max_history_steps: int,
    ) -> str | None:
        trace = self._serialize_history(history, max_history_steps)
        if not trace:
            return None

        parts = [
            f"## User Task\n{task}\n",
            f"## Execution Trace\n{trace}\n",
        ]
        if final_result:
            parts.append(f"## Agent's Final Result\n{final_result}\n")

        parts.append(
            "## Your Evaluation\n"
            "Based on the trace above, evaluate whether the agent truly completed the task. "
            "Respond in JSON format:\n"
            "```json\n"
            '{"reasoning": "...", "verdict": true/false, "failure_reason": "... or null", "impossible_task": false}\n'
            "```\n"
        )
        return "\n".join(parts)

    def _serialize_history(self, history: AgentHistoryList, max_steps: int) -> str:
        all_steps = history.history
        if not all_steps:
            return ""

        if len(all_steps) <= max_steps:
            selected = all_steps
        else:
            selected = all_steps[:3] + all_steps[-(max_steps - 3):]

        lines: list[str] = []
        for h in selected:
            step = h.step_number
            model_out = h.model_output or {}
            goal = model_out.get("next_goal", "")
            action = model_out.get("action", {})
            action_name = action.get("name", "")
            action_params = action.get("params", {})
            result_strs = [str(r) for r in h.result]
            lines.append(
                f"Step {step}:\n"
                f"  Goal: {goal}\n"
                f"  Action: {action_name}({json.dumps(action_params, default=str)})\n"
                f"  Result: {'; '.join(result_strs)}"
            )
        return "\n\n".join(lines)

    def _parse_response(self, content: str) -> JudgementResult:
        # Try to extract JSON from the response
        import re
        json_match = re.search(r'\{[^{}]*\}', content, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group())
                return JudgementResult(
                    reasoning=data.get("reasoning"),
                    verdict=bool(data.get("verdict", False)),
                    failure_reason=data.get("failure_reason"),
                    impossible_task=bool(data.get("impossible_task", False)),
                )
            except (json.JSONDecodeError, TypeError):
                pass

        # Fallback: guess verdict from content
        verdict = "success" in content.lower() or "complete" in content.lower()
        return JudgementResult(
            reasoning=content[:500],
            verdict=verdict,
            failure_reason=None if verdict else content[:500],
        )
