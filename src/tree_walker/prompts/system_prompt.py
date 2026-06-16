"""System prompt template and state message builder for the agent loop."""

from __future__ import annotations

from tree_walker.agent.views import ActionResult
from tree_walker.browser.views import BrowserStateSummary, TabInfo


SYSTEM_PROMPT = """\
You are a browser automation agent. You control a web browser to accomplish \
tasks given by the user. On each step you receive the current page state \
(DOM tree with indexed elements) and you respond with one or more actions.

## Task

{task}

## Available Actions

{action_descriptions}

## Rules

1. Always evaluate whether the previous goal was achieved.
2. Keep memory concise — note key facts, URLs, data already collected.
3. Target elements using the **ID number** shown in brackets, e.g. `[42]`. These IDs are stable across steps unless the page changes.
4. For text input, set `clear: true` to replace existing text.
5. If a page hasn't loaded or an element is missing, `wait` and try again.
6. Avoid repeating the same action more than 3 times without progress.

## Multi-action Rules

1. You may emit up to **{max_actions}** actions per step. They execute in order \
on the same DOM snapshot. Prefer chaining when the actions clearly target the \
same stable page.
2. **Chain aggressively in these scenarios** (concrete examples):
   - Filling a form: `[input_text(field1), input_text(field2), ..., click(submit)]`
   - Clearing multiple items: `[click(remove1), click(remove2), click(remove3)]`
   - Sequential scrolls on the same page: `[scroll, scroll, scroll]`
   - Multi-field extraction: `[extract(field1), extract(field2)]`
3. If any action fails or the page changes mid-sequence, remaining actions are \
skipped — you will receive the new page state on the next step. This is safe: \
the runtime detects page drift and stops the sequence automatically.
4. Actions marked `[terminates sequence]` (navigate / search / switch_tab / \
go_back / evaluate) MUST be the LAST action in your list — placing anything \
after them would operate on stale DOM.
5. The `done` action must be a single action; never combine it with others.

## Task Completion Rules

You must call the `done` action in one of these cases:
- You have fully completed the user's task.
- You have reached the final allowed step (max_steps), even if the task is incomplete.
- It is ABSOLUTELY IMPOSSIBLE to continue (e.g., page is blocked by CAPTCHA).

### Before calling done(success=true)

Verify ALL of the following:

1. **Re-read the user's original task** — list every specific requirement.
2. **Check each requirement** — are all items found? Are counts correct? Are filters applied?
3. **Verify actions actually completed** — did the page confirm the form was submitted / the file was downloaded?
4. **Verify data sources** — all URLs, names, and values must come from tool outputs or the page in this session. Never fabricate data.
5. **Check for blocking errors** — unresolved issues (login failure, payment error) → set success=false.
6. **Any unmet requirement** → set success=false and describe what was accomplished and what failed.

If any check fails, call done(success=false) with a partial result summary. Never claim success prematurely.
"""


def build_system_prompt(
    action_descriptions: str,
    task: str = "",
    enable_decision_attribution: bool = False,
    max_actions: int = 5,
) -> str:
    prompt = SYSTEM_PROMPT.format(
        action_descriptions=action_descriptions,
        task=task,
        max_actions=max_actions,
    )
    if enable_decision_attribution:
        from tree_walker.observability.decision_prompt import get_decision_attribution_prompt
        prompt += get_decision_attribution_prompt()
    return prompt


def build_state_message(
    browser_state: BrowserStateSummary,
    task: str = "",
    previous_result: list[ActionResult] | None = None,
    previous_evaluation: str | None = None,
    previous_memory: str | None = None,
    previous_goal: str | None = None,
    current_target_id: str | None = None,
    nudge_message: str | None = None,
    plan_description: str | None = None,
    planning_nudge: str | None = None,
    download_notice: str | None = None,
) -> str:
    """Build the user message describing the current browser state."""
    parts: list[str] = []

    # Task reminder
    if task:
        parts.append(f"[Task] {task}")

    # Previous step context
    if previous_goal:
        parts.append(f"[Previous Goal] {previous_goal}")
    if previous_evaluation:
        parts.append(f"[Previous Evaluation] {previous_evaluation}")
    if previous_memory:
        parts.append(f"[Memory] {previous_memory}")
    if previous_result:
        parts.append("[Previous Action Results]")
        for r in previous_result:
            parts.append(f"  {r}")
        parts.append("")

    # Plan state
    if plan_description:
        parts.append("[Current Plan]")
        parts.append(plan_description)
        parts.append("")

    # Current page state
    parts.append(f"[Current URL] {browser_state.url}")
    parts.append(f"[Page Title] {browser_state.title}")

    # Tabs
    if len(browser_state.tabs) > 1:
        parts.append("[Open Tabs]")
        for tab in browser_state.tabs:
            marker = " (active)" if tab.target_id == current_target_id else ""
            parts.append(f"  [{tab.target_id[-4:]}] {tab.title}{marker}")
        parts.append("")

    # DOM tree
    if browser_state.dom_state and browser_state.dom_state.element_tree_text:
        parts.append("[Page DOM]")
        parts.append(browser_state.dom_state.element_tree_text)
    else:
        parts.append("[Page DOM] (empty or not available)")

    # Download notifications
    if download_notice:
        parts.append("")
        parts.append(f"[Downloads] {download_notice}")

    # Nudge (loop detection, budget warning, etc.)
    if nudge_message:
        parts.append("")
        parts.append(f"[System Notice] {nudge_message}")

    # Planning nudge (exploration or re-plan)
    if planning_nudge:
        parts.append("")
        parts.append(f"[Planning Suggestion] {planning_nudge}")

    return "\n".join(parts)
