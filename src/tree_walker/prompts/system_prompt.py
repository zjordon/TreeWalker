"""System prompt template and state message builder for the agent loop."""

from __future__ import annotations

from typing import Any

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
7. On large list/data-grid pages, prefer **one `evaluate` call that extracts all rows \
and aggregates client-side** over paginating or reading rows step by step — leave such \
pages as soon as the data is captured.

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


# Appended to the prompt ONLY when upload_file is available for the current page
# (URL filters may drop the action — don't advertise guidance for an action the
# agent can't call). See build_system_prompt.
FILE_UPLOAD_RULES = """\

## File Upload Rules

1. To upload a file, call `upload_file(index=<file input's ID>, path=<absolute path>)` directly.
2. NEVER click an upload button or an `<input type='file'>` first — that opens the OS \
native file picker, which this runtime cannot drive. `upload_file` sets the file \
programmatically and needs no click. (If you click a file input by mistake, you'll \
get an error telling you to use `upload_file`.)
3. When the DOM shows a labeled upload area (e.g. "竖封面" / "横封面") instead of the \
raw `<input type='file'>`, the input nearest that area is chosen automatically. When \
several cover/upload slots exist, target the slot whose label matches your file \
(portrait/竖 → vertical slot; landscape/横 → horizontal slot).
4. A `ℹ️ Note` about `accept` mismatch is informational ONLY — the file was uploaded \
successfully. Do NOT retry on a different index just because of it.
"""


# Appended to the prompt ONLY when a dropdown action is available for the current
# page (URL filters may drop actions — don't advertise guidance the agent can't
# act on). Radix/shadcn dropdowns render as role=combobox, and the bare name
# "dropdown" doesn't cue the model to these tools — make it explicit. See
# build_system_prompt.
DROPDOWN_RULES = """\

## Dropdown Rules

1. For any dropdown — native `<select>`, `role=combobox`, `role=listbox`, or a \
custom dropdown — use `dropdown_options` to read its options and `select_dropdown` \
to pick a value. These tools operate on the dropdown directly.
2. Do NOT first `click` the dropdown trigger (the combobox button / `<select>`). \
These tools expand and read/select options themselves; clicking first wastes a step \
and may leave an open menu behind.
3. For a Radix/shadcn custom dropdown, the trigger is a `role=combobox` button — \
pass that button's index to `select_dropdown`.
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
    # Upload guidance only when upload_file is available for this page (URL
    # filters may drop it — don't advertise an action the agent can't call).
    if "upload_file" in action_descriptions:
        prompt += FILE_UPLOAD_RULES
    if "dropdown_options" in action_descriptions or "select_dropdown" in action_descriptions:
        prompt += DROPDOWN_RULES
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
    page_stats: dict[str, Any] | None = None,
    grid_meta: dict[str, Any] | None = None,
    sensitive_description: str | None = None,
    skill_description: str | None = None,
) -> str:
    """Build the user message describing the current browser state."""
    parts: list[str] = []

    # Task reminder
    if task:
        parts.append(f"[Task] {task}")

    # P1：domain-skill 注入（按 host 读 domain-skills/<host>/，给 LLM 的领域知识）。
    # 多行渲染（skill 常是大段文本）；放在 [Task] 后、[Available Secrets] 前，
    # 让 LLM 在看页面状态前先吸收领域知识。
    if skill_description:
        parts.append("[Domain Skill]")
        parts.append(skill_description)
        parts.append("")

    # P1d：告知 LLM 当前页可用的 <secret> 占位符（只列 key，不含真实值）
    if sensitive_description:
        parts.append(f"[Available Secrets] {sensitive_description}")

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

    # P1a：页面统计（links/交互元素/iframe/骨架屏）。page_stats 为空（如空页面、
    # EMPTY_DOM_STATE、或 enable_page_stats=False）时不渲染。
    if page_stats:
        stats = (
            f"[Page Stats] links={page_stats.get('links', 0)}, "
            f"interactive={page_stats.get('interactive', 0)}, "
            f"iframes={page_stats.get('iframes', 0)}"
        )
        if page_stats.get('skeleton'):
            stats += " SKELETON/LOADING (page may not be fully rendered)"
        parts.append(stats)

    # P7 tool_layer B2：UI 网格元信息——排序状态 + 总数 + 活动过滤残留。
    # 防 128 型「脑补已按日期排序」（8/26 轨迹 S1 自述 sorted by Purchase Date
    # desc，实为任意序）与残留书签过滤误读（跨会话存活）。
    if grid_meta:
        ns = grid_meta.get('namespace') or 'grid'
        total = grid_meta.get('total_records')
        loaded = grid_meta.get('rows_loaded')
        page = grid_meta.get('page')
        page_size = grid_meta.get('page_size')
        parts.append(f"[Grid] {ns} | rows {loaded} of {total}"
                     + (f" (page {page}, {page_size}/page)" if page else ""))
        s = grid_meta.get('sorting')
        if isinstance(s, dict) and s.get('field'):
            line = f"  sorted: {s.get('field')} {s.get('direction', 'asc')}"
            fv = grid_meta.get('first_sorted_value')
            if fv is not None:
                line += f" (first row: {fv})"
            parts.append(line)
        else:
            parts.append("  sorted: (none — do NOT assume any row order; pass sorting to read_grid)")
        leftover = []
        if grid_meta.get('active_filters'):
            leftover.append(f"filters={grid_meta['active_filters']}")
        if grid_meta.get('active_search'):
            leftover.append(f"search={grid_meta['active_search']!r}")
        if leftover:
            parts.append("  ⚠️ active " + " ".join(leftover)
                         + " — leftover from a previous session (server-side bookmark), "
                           "not from your actions; totals above are already filtered")
        parts.append("")

    # Tabs
    if len(browser_state.tabs) > 1:
        parts.append("[Open Tabs]")
        for tab in browser_state.tabs:
            marker = " (active)" if tab.target_id == current_target_id else ""
            parts.append(f"  [{tab.target_id[-4:]}] {tab.title} - {tab.url}{marker}")
        parts.append("")

    # P1b：最近浏览器事件（首期仅 dialog）。enable_recent_events=False 或无事件时不渲染。
    # 最多 5 条、倒序（最新在前）。直接读 browser_state.recent_events，无需额外参数。
    if browser_state.recent_events:
        recent = browser_state.recent_events[-5:][::-1]  # 最近 5 条，倒序
        parts.append("[Recent Events]")
        for ev in recent:
            parts.append(f"  {ev.type}: {ev.message}")
        parts.append("")

    # DOM tree
    if browser_state.dom_state and browser_state.dom_state.element_tree_text:
        parts.append("[Page DOM]")
        parts.append(browser_state.dom_state.element_tree_text)
    else:
        parts.append("[Page DOM] (empty or not available)")

    # File inputs (help pick the right one when several exist, e.g. 抖音 cover editor)
    if browser_state.dom_state and len(browser_state.dom_state.file_inputs_meta) > 1:
        parts.append("[File Inputs]")
        parts.append(
            "Multiple file inputs on this page. Prefer one that is visible and inside an "
            "upload container (upload-ancestor=yes); hidden inputs are often decoys with no "
            "handler (upload reports success but the page does not change)."
        )
        for fi in browser_state.dom_state.file_inputs_meta:
            vis = "visible" if fi.visible else "hidden"
            up = "yes" if fi.upload_ancestor else "no"
            acc = f" accept={fi.accept}" if fi.accept else ""
            cls = f" class={fi.class_name}" if fi.class_name else ""
            parts.append(f"  [{fi.backend_node_id}] {vis}, upload-ancestor={up}{acc}{cls}")
        parts.append("")

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
