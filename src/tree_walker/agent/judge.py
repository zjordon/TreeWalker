"""Independent LLM judge — post-execution trace review."""

from __future__ import annotations

import asyncio
import json
import logging

from pydantic import BaseModel, Field

from tree_walker.agent.views import AgentHistoryList
from tree_walker.config import JudgeSettings

logger = logging.getLogger(__name__)


class JudgementResult(BaseModel):
    reasoning: str | None = None
    verdict: bool = Field(description="Whether the trace was successful or not")
    failure_reason: str | None = None
    impossible_task: bool = False
    captcha: bool = False


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
- Verify each critical step was actually executed (not just attempted), using \
the per-step "URL" and "Page excerpt" evidence recorded in the trace.
- Apply a very high standard for task completion.
- The agent reads page content directly from the DOM / visible text in its \
state, so an explicit extract action is NOT required to have read content. A \
missing explicit extract step is NOT by itself evidence of hallucination: if a \
step's "Page excerpt" shows the reported content is genuinely present on the \
page, treat it as real.
- Conversely, if the agent reports a step as done but the "Page excerpt" or \
"URL" evidence shows it was NOT actually completed (content absent from the \
page, wrong page, unchanged state), set verdict=false.
- For every key step, cross-check whether the action truly happened against \
the per-step URL / Page excerpt, not just the agent's goal statement.
- If the task was impossible to complete (e.g., page down, login blocked), \
set impossible_task=true and verdict=false. If the agent was blocked by a \
CAPTCHA it could not solve, set captcha=true and verdict=false.
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
            "captcha": {
                "type": "boolean",
                "description": "True if the agent was blocked by a CAPTCHA it could not solve.",
            },
        },
    },
}


class JudgeEvaluator:
    """Use an independent LLM to review an agent's execution trace."""

    def __init__(self, llm, settings: JudgeSettings | None = None) -> None:
        self._llm = llm
        # settings is optional so tests can construct JudgeEvaluator(llm=None);
        # defaults then match JudgeSettings.
        self._settings = settings if settings is not None else JudgeSettings()

    async def judge(
        self,
        task: str,
        history: AgentHistoryList,
        final_result: str | None = None,
    ) -> JudgementResult | None:
        prompt = self._build_judge_prompt(task, history, final_result)
        if not prompt:
            return None

        try:
            # B3-3（P7 02 批次三）：空响应（无 tool_use 块）先 nudge 重试一次再放弃
            # （批次二验收两跑均现 "Judge returned no tool_use block"——judge 侧同款
            # 空 LLM 响应，仿 client.py R1 的做法）。
            messages: list[dict] = [{"role": "user", "content": prompt}]
            for attempt in (1, 2):
                # issue #163：judge 的同步 ``messages.create`` 直接 await 会卡死事件循环
                # （tw-web 真机观测：judge 阶段全部端点无响应 4-5 分钟）。经 to_thread 丢线程池。
                response = await asyncio.to_thread(
                    self._llm.client.messages.create,
                    model=self._llm.model,
                    max_tokens=self._llm.max_tokens,
                    system=_JUDGE_SYSTEM_PROMPT,
                    messages=messages,
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
                            captcha=bool(data.get("captcha", False)),
                        )

                logger.warning("Judge returned no tool_use block (attempt %d/2)", attempt)
                messages = messages + [{
                    "role": "user",
                    "content": "Respond now using the agent_response tool with your JSON verdict.",
                }]
            return None
        except Exception:
            logger.exception("Judge evaluation failed")
            return None

    def _build_judge_prompt(
        self,
        task: str,
        history: AgentHistoryList,
        final_result: str | None,
    ) -> str | None:
        trace = self._serialize_history(history)
        if not trace:
            return None

        # Cap the total trace length. Keep the TAIL — the done step and most
        # recent steps are the key evidence (early steps are just navigation
        # context). Head-truncation (trace[:n]) would drop exactly the done
        # step, defeating the whole point of feeding page evidence to Judge.
        max_chars = self._settings.trace_max_chars
        if len(trace) > max_chars:
            trace = trace[-max_chars:]
            # Align forward to the next "Step N:" boundary so we don't start
            # mid-step after truncation.
            boundary = trace.find("\nStep ")
            if boundary != -1:
                trace = trace[boundary + 1:]
            trace = trace + "\n[trace truncated, kept most recent steps]"

        parts = [
            f"## User Task\n{task}\n",
            f"## Execution Trace\n{trace}\n",
        ]
        if final_result:
            parts.append(f"## Agent's Final Result\n{final_result}\n")

        parts.append(
            "## Your Evaluation\n"
            "Based on the trace above, evaluate whether the agent truly completed "
            "the task. Cross-check the per-step URL and Page excerpt against the "
            "agent's reported results. Respond in JSON format:\n"
            "```json\n"
            '{"reasoning": "...", "verdict": true/false, "failure_reason": "... or null", '
            '"impossible_task": false, "captcha": false}\n'
            "```\n"
        )
        return "\n".join(parts)

    def _serialize_history(self, history: AgentHistoryList) -> str:
        all_steps = history.history
        if not all_steps:
            return ""

        # Keep ALL steps — no middle-step dropping. Total length is bounded by
        # trace_max_chars in _build_judge_prompt.
        lines: list[str] = []
        for h in all_steps:
            step = h.step_number
            model_out = h.model_output or {}
            goal = model_out.get("next_goal", "")
            action = model_out.get("action", {})
            action_name = action.get("name", "")
            action_params = action.get("params", {})

            # Per-step page evidence (url/title/dom_excerpt) plus the RAW tool
            # results — not str(r), which is display-truncated and would drop
            # extracted_content. This closes the information asymmetry between
            # the agent (which sees the full DOM) and the Judge.
            summary = h.state_summary or {}
            url = summary.get("url", "") or ""
            title = summary.get("title", "") or ""
            dom_excerpt = summary.get("dom_excerpt", "") or ""

            result_parts: list[str] = []
            for r in h.result:
                if r.error:
                    result_parts.append(f"ERROR: {r.error}")
                elif r.extracted_content:
                    result_parts.append(r.extracted_content)
                else:
                    # Neither error nor extracted content — fall back to the
                    # concise display form for plain OK actions.
                    result_parts.append(str(r))

            block = [
                f"Step {step}:",
                f"  URL: {url}",
                f"  Title: {title}",
                f"  Goal: {goal}",
                f"  Action: {action_name}({json.dumps(action_params, default=str)})",
            ]
            # Only emit the Page excerpt line when there is one (the done step).
            # Non-done steps carry no dom_excerpt, so their block stays light.
            if dom_excerpt:
                block.append(f"  Page excerpt: {dom_excerpt}")
            block.append(f"  Result: {'; '.join(result_parts)}")
            lines.append("\n".join(block))
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
                    captcha=bool(data.get("captcha", False)),
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
