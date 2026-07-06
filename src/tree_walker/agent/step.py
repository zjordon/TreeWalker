"""Step pipeline: 5-stage decomposition of the agent step."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from tree_walker.agent.log_formatter import BLUE, RESET, format_action_params, log_response, log_step_completion
from tree_walker.agent.views import (
    ActionResult,
    AgentHistory,
    DownloadInfo,
    StepMetadata,
    _SENSITIVE_ACTION_FIELDS,
    redact_sensitive_string,
)
from tree_walker.browser.views import BrowserStateSummary, DOMInteractedElement
from tree_walker.prompts.system_prompt import build_state_message, build_system_prompt

if TYPE_CHECKING:
    from tree_walker.config import TruncationSettings
    from tree_walker.agent.loop_detector import ActionLoopDetector
    from tree_walker.agent.message_compactor import MessageCompactor
    from tree_walker.agent.plan_manager import PlanManager
    from tree_walker.browser.session import BrowserSession
    from tree_walker.llm.client import LLMClient
    from tree_walker.observability.event_bus import EventBus
    from tree_walker.tools.actions import Tools

logger = logging.getLogger(__name__)

_PARAM_VALIDATION_MAX_RETRIES = 2

# P0 消息分类管理：内部 _type 键标记消息类别（不送 SDK，_trim_messages 边界剥除）。
# 对齐 browser-use MessageManager 的 state/context/agent_history 分类。
_MSG_TYPE = "_type"
TYPE_STATE = "state"        # 当前页状态消息：每步替换，全局唯一
TYPE_CONTEXT = "context"    # 注入提示（budget/last/failure/loop）：每步清理后重灌
TYPE_USER = "user"          # 持久 user 消息（任务说明、conversation summary）
TYPE_ASSISTANT = "assistant"

_FALLBACK_DONE_OUTPUT: dict[str, Any] = {
    "evaluation_previous_goal": "No action returned",
    "memory": "",
    "next_goal": "Ending task",
    "action": {"name": "done", "params": {"text": "No action returned by LLM", "success": False}},
}


def _attach_downloads_to_done_results(
    results: list[ActionResult], downloaded_files: list[DownloadInfo],
) -> None:
    """二.C：把会话下载自动并入 done 结果的 attachments（对齐 browser-use 变体 B 的
    browser_session.downloaded_files）。原地修改 results；去重；跳过无 path 的下载。
    纯函数，便于单测（_post_process 门控 track_downloads 后调用）。
    """
    dl_paths = [d.path for d in downloaded_files if d.path]
    if not dl_paths:
        return
    for r in results:
        if not r.is_done:
            continue
        existing = set(r.attachments or [])
        merged = list(r.attachments or []) + [p for p in dl_paths if p not in existing]
        if merged:
            r.attachments = merged


class StepPipeline:
    """Mixin providing the 5-stage step pipeline for Agent."""

    # Type hints for attributes provided by Agent — filled at runtime.
    task: str
    _safe_task: str
    llm: LLMClient
    browser: BrowserSession
    tools: Tools
    max_steps: int
    max_failures: int
    llm_timeout: int
    action_timeout: int
    reconnect_timeout: int
    max_actions_per_step: int
    wait_between_actions: float
    _track_downloads: bool
    _step_start_time: float
    messages: list[dict[str, Any]]
    _enable_message_typing: bool
    _enable_page_stats: bool
    _enable_sensitive_description: bool
    _max_history_items: int
    _system_prompt: str
    _tool_schema: dict[str, Any]
    loop_detector: ActionLoopDetector
    _compactor: MessageCompactor | None
    plan_manager: PlanManager | None
    _enable_planning: bool
    _output_mode: str
    _exploration_threshold: int
    _replan_failure_threshold: int
    _obs_bus: EventBus | None
    _obs_session_id: str
    _truncation: TruncationSettings
    _save_conversation_path: str

    # ── Orchestrator ──────────────────────────────────────────────────

    async def _step(self) -> bool:
        """Execute one Sense-Think-Act cycle. Returns True if done."""
        self._step_start_time = time.time()
        if self._obs_bus:
            from tree_walker.observability.events import StepStartEvent
            self._obs_bus.emit(StepStartEvent(
                step=self.state.n_steps, session_id=self._obs_session_id,
            ))
        browser_state: BrowserStateSummary | None = None
        model_output: dict[str, Any] | None = None
        results: list[ActionResult] = []

        try:
            browser_state, state_message = await self._prepare_context()
            if self._compactor:
                await self._compactor.maybe_compact(self.messages, self.state.n_steps)
            if self.state.stopped or self.state.paused:
                return False

            # Clear previous step state — must happen after prepare_context
            # reads old values but before the LLM call, so timeouts/exceptions
            # never leave stale data in state.
            self.state.last_model_output = None
            self.state.last_result = None

            model_output = await self._get_next_action(browser_state, state_message)
            if model_output is None:
                # P0-1：LLM 期间用户停止 → 输出已丢弃。不执行动作、不进 post_process，
                # _finalize 的 `if model_output is not None` 守卫会跳过历史写入。
                return False
            results = await self._execute_actions(model_output, browser_state)
            self._post_process(results, model_output)

            if any(r.is_done for r in results):
                return True
            if self.state.consecutive_failures >= self.max_failures:
                return True
        except Exception as e:
            await self._handle_step_error(e)
            return False
        finally:
            await self._finalize(browser_state, model_output, results)

        return False

    # ── Stage 1: Sense — Prepare context ──────────────────────────────
    #
    # Pipeline order (following browser-use _prepare_context):
    #   1. Get browser state (DOM + screenshot + URL/title/tabs)
    #   2. Log step context (URL, interactive element count)
    #   3. Record page for loop detection
    #   4. Build state message (includes previous context + nudge)
    #   5. Inject budget warning (≥75% steps used)
    #   6. Force done on last step
    #   7. Force done after consecutive failures

    async def _prepare_context(self) -> tuple[BrowserStateSummary, str]:
        """Gather browser state and build the state message for the LLM."""
        # 0. P0：每步入口清理上一步的注入提示（budget/last/failure/loop），
        # 避免 context 消息累积污染。enable_message_typing=False 时 no-op。
        self._clear_context_messages()

        # 1. Get browser state
        # 断路止血：每步截图暂不取（LLM 视觉通道尚未打通，见 docs/tools-optimize/screenshot.md 阶段二）
        browser_state = await self.browser.get_state(include_screenshot=False)

        # 2. Log step context
        self._log_step_context(browser_state)

        # 2b. Update action models based on current page URL
        self._update_action_models_for_page(browser_state.url)

        # 3. Record page for loop detection
        self.loop_detector.record_page(browser_state.url)

        # 3b. Build plan description and planning nudge (if planning enabled)
        plan_description: str | None = None
        planning_nudge: str | None = None
        if self._enable_planning and self.plan_manager:
            plan_description = self.plan_manager.render_plan_description(
                self.state.plan,
            )
            planning_nudge = self.plan_manager.build_replan_nudge(
                self.state.consecutive_failures,
                self._replan_failure_threshold,
                self.state.plan,
            ) or self.plan_manager.build_exploration_nudge(
                self.state.n_steps,
                self._exploration_threshold,
                self.state.plan,
            )

        # 4. Build state message (includes loop detection nudge inline)
        nudge = self.loop_detector.get_nudge_message()

        # 4b. Check for new downloads
        download_notice: str | None = None
        if self._track_downloads:
            new_downloads = self.browser.consume_completed_downloads()
            if new_downloads:
                from tree_walker.agent.views import DownloadInfo
                for d in new_downloads:
                    self.state.downloaded_files.append(DownloadInfo(**d))
                file_list = ", ".join(d["filename"] for d in new_downloads)
                download_notice = f"New files available: {file_list}"

        # P1a/P1d：页面统计 + 当前页可用 secret（均受 flag 控制；None 时 build_state_message 不渲染）
        page_stats = (
            browser_state.dom_state.page_stats
            if (self._enable_page_stats and browser_state.dom_state)
            else None
        )
        sensitive_desc = (
            self._build_sensitive_description(browser_state.url)
            if self._enable_sensitive_description
            else None
        )

        state_msg = build_state_message(
            browser_state=browser_state,
            task=self._safe_task,
            previous_result=self.state.last_result,
            previous_evaluation=self._last("evaluation_previous_goal"),
            previous_memory=self._last("memory"),
            previous_goal=self._last("next_goal"),
            current_target_id=self.browser.current_target_id,
            nudge_message=nudge,
            plan_description=plan_description,
            planning_nudge=planning_nudge,
            download_notice=download_notice,
            page_stats=page_stats,
            sensitive_description=sensitive_desc,
        )
        self._set_state_message(state_msg)  # P0：替换唯一 state 消息（避免完整 DOM 随步数累积）

        # P1c：注入 <agent_history>（滑动窗口，每步替换 TYPE_USER 消息）。
        # 首步无历史 → _build_... 返回 None → _set_history_message 仅清残留（无操作）。
        self._set_history_message(self._build_agent_history_description())

        # 5. Inject budget warning (>=75% steps used)
        self._inject_budget_warning()

        # 6. Force done on last step
        self._force_done_on_last_step()

        # 7. Force done after consecutive failures
        self._force_done_after_failure()

        return browser_state, state_msg

    # ── Context injection helpers ─────────────────────────────────────

    @staticmethod
    def _strip_type(msg: dict[str, Any]) -> dict[str, Any]:
        """P0：剥除内部 ``_type`` 键（送 SDK 前的边界）。无该键时原样返回。"""
        if _MSG_TYPE not in msg:
            return msg
        return {k: v for k, v in msg.items() if k != _MSG_TYPE}

    def _set_state_message(self, content: str) -> None:
        """设置当前步状态消息，并保留上一份 state 供 LLM 前后对比。

        ``enable_message_typing=True`` 时保留**最近 2 份** state（previous + current），
        而非 P0 原版的"仅留 1 份"：纯替换会让模型丧失 before/after DOM 对比，无法确认
        动作是否生效——抖音封面上传回归：上传后画布新增的 ``<img>`` 节点必须与上一步的
        空画布对比才能确认成功；只剩当前 state 时，模型被其他空槽位残留的"点击上传"
        占位文 + 变化的 input 索引误导，误判"上传没生效"而反复重试。保留 2 份既恢复对比
        能力，又有界（远小于 P0 前无界累积的 token 成本）。False 时回退原始 append。
        """
        if not self._enable_message_typing:
            self.messages.append({"role": "user", "content": content})
            return
        # 保留最近 1 份旧 state（让 LLM 能 before/after 对比），删更老的。
        # state_idxs[:-1] = 除最近一份外的全部旧 state 索引。
        state_idxs = [i for i, m in enumerate(self.messages) if m.get(_MSG_TYPE) == TYPE_STATE]
        drop = set(state_idxs[:-1])
        self.messages = [m for i, m in enumerate(self.messages) if i not in drop]
        self.messages.append({"role": "user", "content": content, _MSG_TYPE: TYPE_STATE})

    def _clear_context_messages(self) -> None:
        """每步入口清理上一步的注入提示（对齐 ``prepare_step_state`` 的
        ``context_messages.clear()``）。``enable_message_typing=False`` 时 no-op
        （保持原始累积行为，向后兼容）。
        """
        if not self._enable_message_typing:
            return
        self.messages = [m for m in self.messages if m.get(_MSG_TYPE) != TYPE_CONTEXT]

    def _set_history_message(self, content: str | None) -> None:
        """P1c：设置/替换唯一的 ``<agent_history>`` 消息（TYPE_USER，每步替换）。

        对齐 browser-use ``agent_history_items``：保留首条 + 省略提示 + 最近 N 步，
        让 LLM 看到早期 memory/目标，避免重复探索。``content=None``（首步无历史）
        时移除上一步残留的 history 消息。``enable_message_typing=False`` 时 no-op
        （不单独维护历史消息，回退到 messages 里既有的简化 assistant 文本）。
        TYPE_USER 当前为 history 专属槽位（compactor summary 无 ``_type``，不碰撞）。
        """
        if not self._enable_message_typing:
            return
        self.messages = [m for m in self.messages if m.get(_MSG_TYPE) != TYPE_USER]
        if content:
            self.messages.append({"role": "user", "content": content, _MSG_TYPE: TYPE_USER})

    def _add_context_message(self, content: str) -> None:
        """追加注入提示（budget/last/failure/loop）。每步先 ``_clear_context_messages``
        后灌，不累积。``enable_message_typing=False`` 时回退原始 append。
        """
        if not self._enable_message_typing:
            self.messages.append({"role": "user", "content": content})
            return
        self.messages.append({"role": "user", "content": content, _MSG_TYPE: TYPE_CONTEXT})

    def _update_action_models_for_page(self, page_url: str) -> None:
        self._tool_schema = self.tools.registry.get_tool_schema(
            page_url=page_url,
            enable_planning=self._enable_planning,
            output_mode=self._output_mode,
            max_actions=self.max_actions_per_step,
        )
        self._system_prompt = build_system_prompt(
            action_descriptions=self.tools.registry.get_action_descriptions_text(page_url=page_url),
            task=self._safe_task,
            enable_decision_attribution=self._enable_decision_attribution,
        )

    def _log_step_context(self, browser_state: BrowserStateSummary) -> None:
        """Log step number, URL, and interactive element count."""
        url = browser_state.url or ""
        url_short = (url[:50] + "...") if len(url) > 50 else url
        element_count = 0
        if browser_state.dom_state and browser_state.dom_state.selector_map:
            element_count = len(browser_state.dom_state.selector_map)
        logger.info("\n📍 Step %d:", self.state.n_steps)
        logger.debug(
            "Evaluating page with %d interactive elements on: %s",
            element_count,
            url_short,
        )
        # Log file input backend IDs
        if browser_state.dom_state and browser_state.dom_state.file_input_backend_ids:
            logger.debug(
                "File input backend IDs: %s",
                browser_state.dom_state.file_input_backend_ids,
            )

    def _inject_budget_warning(self) -> None:
        """Inject a budget warning when ≥75% of steps are used."""
        steps_used = self.state.n_steps + 1
        budget_ratio = steps_used / self.max_steps
        if budget_ratio >= 0.75 and self.state.n_steps < self.max_steps:
            steps_remaining = self.max_steps - steps_used
            pct = int(budget_ratio * 100)
            msg = (
                f"BUDGET WARNING: You have used {steps_used}/{self.max_steps} steps "
                f"({pct}%). {steps_remaining} steps remaining. "
                f"If the task cannot be completed in the remaining steps, "
                f"prioritize consolidating your results and call done. "
                f"Partial results are far more valuable than exhausting all steps with nothing saved."
            )
            self._add_context_message(msg)  # P0：注入提示（每步清理后重灌，不累积）
            logger.info("Budget warning injected: %d/%d steps used", steps_used, self.max_steps)

    def _force_done_on_last_step(self) -> None:
        """Force LLM to call done on the last step."""
        if self.state.n_steps >= self.max_steps - 1:
            msg = (
                "LAST STEP: You have reached max_steps - this is your final step. "
                'You must call the "done" action now. '
                "Summarize what you have accomplished so far."
            )
            self._add_context_message(msg)  # P0：注入提示（每步清理后重灌）
            self._tool_schema = self.tools.registry.get_tool_schema(
                include_actions=["done"],
                output_mode=self._output_mode,
                max_actions=1,
            )
            logger.info("Force-done injected: last step reached (done-only schema)")

    def _force_done_after_failure(self) -> None:
        """Force LLM to call done after consecutive failures reach max."""
        if self.state.consecutive_failures >= self.max_failures:
            msg = (
                f"FAILURE LIMIT: You have failed {self.state.consecutive_failures} consecutive times. "
                f"The agent will terminate after this step. "
                'You must call the "done" action now with whatever results you have.'
            )
            self._add_context_message(msg)  # P0：注入提示（每步清理后重灌）
            self._tool_schema = self.tools.registry.get_tool_schema(
                include_actions=["done"],
                output_mode=self._output_mode,
                max_actions=1,
            )
            logger.info(
                "Force-done injected: %d consecutive failures (done-only schema)",
                self.state.consecutive_failures,
            )

    # ── Stage 2: Think ────────────────────────────────────────────────
    #
    # Pipeline order (following browser-use _get_next_action):
    #   0. [in orchestrator] Clear previous state
    #   1. Get trimmed input messages
    #   2. Call LLM with timeout via asyncio.wait_for
    #      └─ _get_action_with_retry:
    #           a. First call to get_action()
    #           b. If empty action → append clarification → retry
    #           c. If still empty → fallback done(success=False)
    #   3. Record assistant message
    #   4. Log action decision

    async def _get_next_action(
        self,
        browser_state: BrowserStateSummary,
        state_message: str,
    ) -> dict[str, Any] | None:
        """Call the LLM with timeout and retry, return parsed output."""
        trimmed = self._trim_messages()
        logger.debug(
            "Step %d: Calling LLM with %d messages",
            self.state.n_steps,
            len(trimmed),
        )

        model_call_id = ""
        if self._obs_bus:
            from tree_walker.observability.events import ModelCallEvent
            model_call_id = uuid.uuid4().hex[:8]
            self._obs_bus.emit(ModelCallEvent(
                step=self.state.n_steps, session_id=self._obs_session_id,
                model_call_id=model_call_id, message_count=len(trimmed),
            ))

        try:
            response = await asyncio.wait_for(
                self._get_action_with_retry(trimmed),
                timeout=self.llm_timeout,
            )
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"LLM call timed out after {self.llm_timeout}s. "
                "Keep your output concise."
            )

        # P0-1 post-LLM stop check #1 (browser-use service.py:1191). If the user
        # stopped/paused during the LLM call, discard the output before any side
        # effect (no event emit, no history append) and return None so _step
        # short-circuits. Returning None (not raising InterruptedError) keeps this
        # distinct from Phase 4 in-execution interruption semantics.
        if self.state.stopped or self.state.paused:
            logger.debug(
                "Step %d: stopped/paused during LLM call — discarding output",
                self.state.n_steps,
            )
            return None

        # Hard-cap actions to max_actions_per_step (browser-use service.py:1950-1951).
        # The system prompt and schema maxItems only *tell* the LLM the limit;
        # this is the runtime safety net for when small models ignore them and
        # emit too many actions (which would run on stale DOM as earlier actions
        # mutate the page). Before the ModelResultEvent emit so the reported
        # action_name matches what will actually execute.
        response = self._truncate_actions(response)

        if self._obs_bus:
            from tree_walker.observability.events import ModelResultEvent
            action = response.get("action", {})
            self._obs_bus.emit(ModelResultEvent(
                step=self.state.n_steps, session_id=self._obs_session_id,
                model_call_id=model_call_id,
                action_name=action.get("name", "done"),
                next_goal=response.get("next_goal", ""),
            ))

        # Record assistant message for conversation history
        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": (
                f"[{response.get('evaluation_previous_goal', '')}] "
                f"Goal: {response.get('next_goal', '')} | "
                f"Action: {response.get('action', {}).get('name', 'unknown')}"
            ),
        }
        if self._enable_message_typing:
            assistant_msg[_MSG_TYPE] = TYPE_ASSISTANT  # P0：标记类型（_trim_messages 边界剥除）
        # P0-1 post-LLM stop check #2 (browser-use service.py:1197 — "check again
        # before we commit the output to history"). Side effects above (event emit,
        # log) already ran, but if the user stopped in that window we must not commit
        # the stale assistant message to self.messages. Return the response so the
        # existing _execute_actions stop guard (L654) handles non-execution.
        if self.state.stopped or self.state.paused:
            logger.debug(
                "Step %d: stopped/paused before committing assistant message",
                self.state.n_steps,
            )
            return response
        self.messages.append(assistant_msg)

        # Log action decision (structured four-line block)
        action = response.get("action", {})
        action_name = action.get("name", "done")
        action_params = action.get("params", {})
        # P1-2：决策日志脱敏（修复 pre-existing 泄露——params 已被 client
        # ``_restore_sensitive_in_output`` 还原为真值，直接打印会泄密；与
        # ``_execute_actions`` 的 per-action 日志共用同一辅助）。
        safe_params = _redact_params_for_log(
            action_name, action_params, self._sensitive_map_for_log,
        )
        log_response(
            evaluation=response.get("evaluation_previous_goal", ""),
            memory=response.get("memory", ""),
            next_goal=response.get("next_goal", ""),
            action_name=action_name,
            action_params=safe_params,
            step=self.state.n_steps,
            logger=logger,
        )

        # P1-3：每步对话 dump（browser-use save_conversation_path）。在 log_response 之后、
        # return 之前。trimmed 已脱敏/缩短/剥 _type，dump 安全且忠实于 LLM 所见。
        self._save_conversation(trimmed, response)

        self._current_model_call_id = model_call_id

        return response

    def _truncate_actions(self, response: dict[str, Any]) -> dict[str, Any]:
        """Hard-cap actions to ``max_actions_per_step`` (browser-use service.py:1950-1951).

        The system prompt and schema ``maxItems`` only *tell* the LLM the limit;
        this is the runtime safety net for when small models ignore them and emit
        too many actions (which would execute on stale DOM as earlier actions
        mutate the page). Mutates ``response`` in place and returns it, keeping
        ``action`` (first) and ``actions`` (list) consistent.
        """
        actions = response.get("actions")
        if not isinstance(actions, list):
            return response
        if len(actions) <= self.max_actions_per_step:
            return response
        kept = actions[: self.max_actions_per_step]
        dropped = actions[self.max_actions_per_step:]
        response["actions"] = kept
        response["action"] = kept[0] if kept else response.get("action", {})
        dropped_names = [a.get("name", "?") for a in dropped if isinstance(a, dict)]
        logger.warning(
            "Step %d: LLM emitted %d actions (max %d) — truncated, dropped: %s",
            self.state.n_steps, len(actions), self.max_actions_per_step, dropped_names,
        )
        return response

    def _save_conversation(self, messages: list[dict[str, Any]], model_output: dict[str, Any]) -> None:
        """Dump this step's input messages + model output to a text file (browser-use service.py:1713-1723).

        Human-readable audit artifact — distinct from rerun-history (machine replay) and
        observability JsonlRecorder (event stream). ``messages`` is the post-processed
        ``trimmed`` list (URLs shortened to ``[uN]``, sensitive values masked, ``_type``
        stripped) — i.e. exactly what the LLM saw, so the dump is safe (no real secret
        values) and faithful. Best-effort: IO errors are logged and swallowed so the agent
        loop is never blocked by disk failure.
        """
        if not self._save_conversation_path:
            return
        try:
            path = Path(self._save_conversation_path)
            path.mkdir(parents=True, exist_ok=True)
            conv_id = self._obs_session_id or format(id(self), "x")
            target = path / f"conversation_{conv_id}_{self.state.n_steps}.txt"
            lines = [f"=== Step {self.state.n_steps} (model={getattr(self.llm, 'model', '?')}) ==="]
            for m in messages:
                role = m.get("role", "?")
                content = m.get("content", "")
                lines.append(f"\n--- {role} ---\n{content}")
            lines.append(f"\n--- model_output ---\n{json.dumps(model_output, ensure_ascii=False, indent=2)}")
            target.write_text("\n".join(lines), encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to save conversation for step %d: %s", self.state.n_steps, e)

    async def _get_action_with_retry(
        self,
        messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Call LLM; retry once on empty action; fallback to done."""
        # Log available actions and tool schema for debugging
        action_enum = (
            self._tool_schema.get("input_schema", {})
            .get("properties", {}).get("action", {})
            .get("properties", {}).get("name", {})
            .get("enum", [])
        )
        logger.debug("Available actions for this step: %s", action_enum)
        logger.debug("Tool schema: %s", json.dumps(self._tool_schema, ensure_ascii=False, indent=2))

        response = await self.llm.get_action(
            system_prompt=self._system_prompt,
            messages=messages,
            tool_schema=self._tool_schema,
        )

        if self._is_valid_action(response):
            return await self._validate_params_or_retry(response, messages)

        # Retry: append clarification message
        logger.warning("LLM returned empty action, retrying with clarification...")
        retry_messages = list(messages) + [{
            "role": "user",
            "content": (
                "You forgot to return an action. Please respond with a valid "
                "action using the agent_response tool, including your evaluation, "
                "memory, next goal, and action."
            ),
        }]
        response = await self.llm.get_action(
            system_prompt=self._system_prompt,
            messages=retry_messages,
            tool_schema=self._tool_schema,
        )

        if self._is_valid_action(response):
            return await self._validate_params_or_retry(response, messages)

        # Fallback: insert safe done action
        logger.warning("LLM still returned empty action after retry, using fallback done")
        return dict(_FALLBACK_DONE_OUTPUT)

    async def _validate_params_or_retry(
        self,
        response: dict[str, Any],
        original_messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Validate action params against Pydantic model; retry with error details."""
        param_error = self._validate_action_params(response)
        if param_error is None:
            return response

        for attempt in range(_PARAM_VALIDATION_MAX_RETRIES):
            logger.warning(
                "Invalid params for '%s': %s — retrying (%d/%d)",
                response["action"]["name"], param_error,
                attempt + 1, _PARAM_VALIDATION_MAX_RETRIES,
            )
            retry_messages = list(original_messages) + [{
                "role": "user",
                "content": (
                    f"Your action parameters are invalid: {param_error}. "
                    "Please fix the parameters and respond again with a valid action."
                ),
            }]
            response = await self.llm.get_action(
                system_prompt=self._system_prompt,
                messages=retry_messages,
                tool_schema=self._tool_schema,
            )

            if not self._is_valid_action(response):
                logger.warning("LLM returned empty action during param validation retry")
                return dict(_FALLBACK_DONE_OUTPUT)

            param_error = self._validate_action_params(response)
            if param_error is None:
                return response

        logger.warning(
            "Params still invalid after %d retries: %s — proceeding anyway",
            _PARAM_VALIDATION_MAX_RETRIES, param_error,
        )
        return response

    def _validate_action_params(self, response: dict[str, Any]) -> str | None:
        """Validate action params against the registered Pydantic model.

        Uses the registry's param_model — variant-B aware (``done`` switches to
        ``StructuredDoneParams`` when ``output_model`` is set) — and the same
        flattening as the execution path (``Tools._flatten_params``), so the
        schema the LLM sees, this validation, and execution all agree. The prior
        static version used the ``ACTION_DEFINITIONS`` model, so variant-B
        ``done(data=...)`` was validated against the standard ``DoneParams``
        (text required, data forbidden) and always failed.

        Returns None if valid, or an error detail string if invalid.
        """
        action = response.get("action", {})
        name = action.get("name", "")
        params = action.get("params", {})

        registered = self.tools.registry.actions.get(name)
        if registered is None:
            # Unknown action — let execution surface it; nothing to validate.
            return None

        param_model = registered.param_model
        flat_params = self.tools._flatten_params(params, name)
        try:
            param_model.model_validate(flat_params)
            return None
        except ValidationError as e:
            parts = []
            for err in e.errors():
                field = ".".join(str(loc) for loc in err["loc"])
                parts.append(f"{field}: {err['msg']}")
            return "; ".join(parts)

    @staticmethod
    def _is_valid_action(response: dict[str, Any]) -> bool:
        """Check whether the LLM response contains a usable action."""
        action = response.get("action")
        if not action or not isinstance(action, dict):
            return False
        name = action.get("name")
        return bool(name and isinstance(name, str))

    # ── Stage 3: Act ──────────────────────────────────────────────────

    @property
    def _sensitive_map_for_log(self) -> dict[str, str] | None:
        """``{placeholder: real_value}`` 方向的敏感映射，供终端日志脱敏使用。

        ``redact_sensitive_string`` 要求 ``{placeholder: real_value}`` 方向，而
        Agent 维护的 ``_sensitive_map`` 是反向 ``{real_value: placeholder}``
        （client 脱敏用）。这里惰性反转，与 ``rerun.py:197`` 的 history 脱敏路径
        同款。无 sensitive 配置时返回 None（``_redact_params_for_log`` 据此 no-op）。
        """
        raw = getattr(self, "_sensitive_map", None)
        if not raw:
            return None
        return {placeholder: real for real, placeholder in raw.items()}

    async def _execute_actions(
        self,
        model_output: dict[str, Any],
        browser_state: BrowserStateSummary,
    ) -> list[ActionResult]:
        """Execute the action(s) decided by the LLM.

        Multi-action loop with five guards (Phase 2 + 3) and exception
        triage (Phase 4):
          - Reads ``model_output["actions"]`` (list) when present, falls back to
            single-action ``model_output["action"]`` for backward compatibility.
          - Executes actions strictly sequentially — never concurrently.
          - Each action has its own per-action timeout and observability event.
          - Pauses ``wait_between_actions`` seconds before each non-first
            action (anti-detection cadence, browser-use parity).

        Guards stop the sequence:
          #1 done as single action — list midpoint ``done`` short-circuits
          #2 result.is_done       — task completion signal
          #3 result.error         — execution failure
          #4 terminates_sequence  — navigate/search/switch_tab/go_back/evaluate
                                    and any other action flagged at registration
          #5 runtime drift        — URL or current_target_id changed between
                                    pre/post-action sampling. Covers implicit
                                    side effects (e.g. click on <a> navigating,
                                    opening a new tab).

        Exception triage (Phase 4):
          - ``InterruptedError``        — re-raise (user stop/pause signal)
          - connection-like errors      — re-raise (browser crashed / CDP lost)
          - other errors / TimeoutError — append ``ActionResult(error)`` and
                                            stop the sequence (return results)
        """
        if self.state.stopped or self.state.paused:
            return [ActionResult(error="Agent stopped or paused")]

        actions = model_output.get("actions") or [model_output.get("action", {})]
        total = len(actions)
        results: list[ActionResult] = []

        for i, action in enumerate(actions):
            action_name = action.get("name", "done")
            action_params = action.get("params", {})

            # Guard #1: done is only allowed as a single action. Encountering
            # it after position 0 means the LLM mis-chained; we stop here so
            # subsequent (meaningless) actions are skipped silently.
            if i > 0 and action_name == "done":
                logger.debug(
                    "done is only allowed as a single action — skipping %d/%d remaining",
                    total - i, total,
                )
                break

            # Phase 4: anti-detection cadence between chained actions.
            # Skipped on the first action and whenever guard #1 already broke.
            if i > 0 and self.wait_between_actions > 0:
                await asyncio.sleep(self.wait_between_actions)

            # P0-1：per-action stop/pause 检查（对齐 browser-use service.py:2753
            # ``_check_stop_or_pause``）。放在下方 inner try **之前**，使 raise
            # 直达 ``_step`` 外层 try → ``_handle_step_error`` 分支 1（不计
            # failure）。partial results 随 raise 丢弃——与 browser-use 一致
            # （用户主动停 = 不要剩余结果）。02 期 P0-1 已为 LLM 阶段修过对称漏洞，
            # 此处补齐 Act 阶段。
            if self.state.stopped or self.state.paused:
                logger.debug(
                    "Step %d: stopped/paused before action %d/%d — aborting sequence",
                    self.state.n_steps, i + 1, total,
                )
                raise InterruptedError

            # P1-2：per-action 执行日志（对齐 browser-use ``_log_action``
            # service.py:2756，格式 ``[i/total] name: params``）。``action_params``
            # 已被 client 还原为真值，必须先脱敏再打印（复用 ``_redact_params_for_log``）。
            safe_params = _redact_params_for_log(
                action_name, action_params, self._sensitive_map_for_log,
            )
            logger.info(
                "  [%d/%d] %s%s%s: %s",
                i + 1, total,
                BLUE, action_name, RESET,
                format_action_params(safe_params),
            )

            tool_call_id = ""
            tool_start = time.time()
            if self._obs_bus:
                from tree_walker.observability.events import ToolCallEvent
                tool_call_id = uuid.uuid4().hex[:8]
                self._obs_bus.emit(ToolCallEvent(
                    step=self.state.n_steps, session_id=self._obs_session_id,
                    model_call_id=getattr(self, "_current_model_call_id", ""),
                    tool_call_id=tool_call_id, action_name=action_name,
                    params=action_params,
                    action_index=i, total_actions=total,
                ))

            # Sample pre-action state for runtime drift detection (guard #5).
            # First iteration reuses the step-start URL from browser_state to
            # avoid an extra CDP call; later iterations read fresh values
            # because earlier actions may have changed them.
            if i == 0:
                pre_action_url = browser_state.url
            else:
                try:
                    pre_action_url = await self.browser.get_current_url()
                except Exception:
                    pre_action_url = browser_state.url
            pre_target_id = self.browser.current_target_id

            try:
                result = await asyncio.wait_for(
                    self.tools.execute(action_name, action_params, self.browser, browser_state),
                    timeout=self.action_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning("Action '%s' timed out after %ds", action_name, self.action_timeout)
                result = ActionResult(error=f"Action timed out after {self.action_timeout}s")
            except InterruptedError:
                # Phase 4: user stop/pause signal must propagate to
                # _handle_step_error without being wrapped in ActionResult.
                raise
            except Exception as e:
                if _is_connection_error(e):
                    raise
                logger.error("Action '%s' raised %s: %s", action_name, type(e).__name__, e)
                result = ActionResult(error=f"{type(e).__name__}: {e}")

            results.append(result)

            duration = time.time() - tool_start
            if self._obs_bus and tool_call_id:
                from tree_walker.observability.events import ToolResultEvent
                self._obs_bus.emit(ToolResultEvent(
                    step=self.state.n_steps, session_id=self._obs_session_id,
                    tool_call_id=tool_call_id,
                    success=result.success, error=result.error,
                    duration_seconds=duration,
                    action_index=i, total_actions=total,
                ))

            # Guard #2/#3: is_done or error terminates the sequence — the LLM
            # will see the failure / completion in the next step's state.
            if result.is_done or result.error or i == total - 1:
                break

            # Guard #4: static terminates_sequence flag. Covers page-changing
            # actions (navigate / search / switch_tab / go_back / evaluate)
            # and any custom action that opts in via registration metadata.
            registered = self.tools.registry.actions.get(action_name)
            if registered is not None and registered.terminates_sequence:
                logger.info(
                    "Action '%s' terminates sequence — skipping %d/%d remaining",
                    action_name, total - i - 1, total,
                )
                break

            # Guard #5: runtime drift. Catches implicit side effects not flagged
            # by terminates_sequence — e.g. click on <a> navigating, JS opening
            # a new tab, form submit redirecting. Compare URL + target_id; if
            # either changed, the remaining queued actions operate on stale DOM.
            try:
                post_url = await self.browser.get_current_url()
            except Exception:
                post_url = pre_action_url
            post_target_id = self.browser.current_target_id
            if post_url != pre_action_url or post_target_id != pre_target_id:
                logger.info(
                    "Page drifted after '%s' (url: %s→%s, tab: %s→%s) — "
                    "skipping %d/%d remaining",
                    action_name, pre_action_url, post_url,
                    pre_target_id, post_target_id,
                    total - i - 1, total,
                )
                break

        return results

    # ── Stage 4: Post-process ─────────────────────────────────────────

    def _post_process(
        self,
        results: list[ActionResult],
        model_output: dict[str, Any],
    ) -> None:
        """Update agent state after action execution.

        Pipeline order (following browser-use _post_process):
          1. Store results in state
          2. Record action to loop detector (with exemption filtering)
          3. Failure count management (single-action error → increment + early return)
          4. Success → reset failure counter
          5. Completion result logging (color-coded)
        """
        self.state.last_result = results
        self.state.last_model_output = model_output

        # 二.C：把会话下载自动并入 done 结果的 attachments（对齐 browser-use 变体 B 的
        # browser_session.downloaded_files）。置于 failure 计数分支之前，不改变
        # consecutive_failures 语义。
        if self._track_downloads and self.state.downloaded_files:
            _attach_downloads_to_done_results(results, self.state.downloaded_files)

        # Update plan state from model output (if planning enabled)
        if self._enable_planning and self.plan_manager:
            self.plan_manager.update_from_model_output(self.state, model_output)

        # Record each action to loop detector with exemption filtering.
        # Multi-action steps record each action individually so the detector
        # sees the full sequence.
        actions = model_output.get("actions") or [model_output.get("action", {})]
        for action in actions:
            action_name = action.get("name", "done")
            action_params = action.get("params", {})
            if action_name not in _LOOP_EXEMPT_ACTIONS:
                self.loop_detector.record_action(action_name, action_params)

        # Phase 4 failure semantics (aligned to browser-use service.py:1221-1231):
        #   - single-action step with error → count + early return
        #   - multi-action step (any failure, partial or all) → do NOT count;
        #     loop_detector + replan nudges handle recovery instead
        #   - any non-counted step → reset counter if previously > 0
        if results and len(results) == 1 and results[-1].error:
            self.state.consecutive_failures += 1
            logger.debug("Consecutive failures: %d", self.state.consecutive_failures)
            return
        if results and len(results) > 1 and any(r.error for r in results):
            # TreeWalker 增强：显式记录多动作失败（含全失败）以便观测，但按
            # browser-use 语义不计入 consecutive_failures（交循环检测 + replan）。
            logger.info(
                "Multi-action step had %d/%d actions failed — not incrementing "
                "consecutive_failures (deferred to loop detection)",
                sum(1 for r in results if r.error), len(results),
            )

        # Non-counted step (success or multi-action failure) → reset counter
        if self.state.consecutive_failures > 0:
            self.state.consecutive_failures = 0

        # Completion result logging (aligned to browser-use service.py:1232-1244):
        # 统一标签 "📄 Final Result:"，绿/红靠 ANSI 颜色区分；随后输出 attachments。
        if results and results[-1].is_done:
            result = results[-1]
            if result.success:
                logger.info(
                    "\n📄 \033[32m Final Result:\033[0m\n%s\n",
                    result.extracted_content or "",
                )
            else:
                logger.info(
                    "\n📄 \033[31m Final Result:\033[0m\n%s\n",
                    result.extracted_content or "",
                )
            if result.attachments:
                total = len(result.attachments)
                for i, file_path in enumerate(result.attachments):
                    logger.info(
                        "👉 Attachment %s: %s",
                        i + 1 if total > 1 else "",
                        file_path,
                    )

    # ── Stage 5: Finalize ─────────────────────────────────────────────

    async def _finalize(
        self,
        browser_state: BrowserStateSummary | None,
        model_output: dict[str, Any] | None,
        results: list[ActionResult],
    ) -> None:
        """Record history, log summary, and advance step counter.

        Called from finally block — runs regardless of success or exception.
        n_steps is incremented last so all consumers see the current value.

        Async in preparation for screenshot persistence (phase 5 P1); no await
        sites yet — screenshot storage lands with screenshot.md stage 2.
        """
        if model_output is not None:
            state_summary: dict[str, Any] | None = None
            if browser_state:
                state_summary = {
                    "url": browser_state.url,
                    "title": browser_state.title,
                    "duration": time.time() - self._step_start_time,
                }
                # Only the done step carries a DOM excerpt — it's the
                # independent page evidence the Judge uses to verify the final
                # result is real (not hallucinated). Other steps stay light
                # (url/title only) so a long session can't blow the token
                # budget and force a truncation that drops the done step.
                if any(r.is_done for r in results):
                    dom_state = browser_state.dom_state
                    state_summary["dom_excerpt"] = (
                        dom_state.element_tree_text if dom_state else ""
                    )[: self._truncation.dom_excerpt_max_chars]
            self.history.history.append(AgentHistory(
                step_number=self.state.n_steps,
                model_output=model_output,
                result=results,
                state_summary=state_summary,
                interacted_element=self._project_interacted_elements(model_output, browser_state),
                metadata=self._build_step_metadata(time.time()),
            ))

        if self._obs_bus:
            from tree_walker.observability.events import StepEndEvent
            duration = time.time() - self._step_start_time
            is_done = any(r.is_done for r in results) if results else False
            self._obs_bus.emit(StepEndEvent(
                step=self.state.n_steps, session_id=self._obs_session_id,
                duration_seconds=duration, is_done=is_done,
                consecutive_failures=self.state.consecutive_failures,
            ))

        self._log_step_completion_summary(results)
        self.state.n_steps += 1

    def _project_interacted_elements(
        self,
        model_output: dict[str, Any],
        browser_state: BrowserStateSummary | None,
    ) -> list[dict[str, Any] | None] | None:
        """把每个动作当年交互的元素投影成 ``DOMInteractedElement.to_dict()``。

        与 ``model_output`` 的 actions 列表【等长、按位对应】；无 index 的动作为 None。
        使用【该步开始时】的 ``browser_state``（LLM 看到、index 所指的那份 selector_map），
        这样重放时才能正确还原「当年点的元素」。
        """
        if not browser_state or not browser_state.dom_state:
            return None
        selector_map = browser_state.dom_state.selector_map
        if not selector_map:
            return None

        actions = model_output.get("actions") or [model_output.get("action", {})]
        projected: list[dict[str, Any] | None] = []
        for action in actions:
            params = action.get("params", {}) if isinstance(action, dict) else {}
            index = params.get("index")
            if index is None:
                index = params.get("element_id")  # element_id 是 index 的别名
            node = selector_map.get(index) if index is not None else None
            if node is not None:
                projected.append(DOMInteractedElement.load_from_enhanced_dom_tree(node).to_dict())
            else:
                projected.append(None)
        return projected

    def _build_step_metadata(self, step_end_time: float) -> StepMetadata:
        """构造单步计时。``step_interval`` = 上一步的耗时（首步为 None）。

        在当前步 AgentHistory 追加【之前】调用，故 ``history[-1]`` 即上一步。
        """
        prev = self.history.history[-1] if self.history.history else None
        step_interval = prev.metadata.duration_seconds if prev and prev.metadata else None
        return StepMetadata(
            step_start_time=self._step_start_time,
            step_end_time=step_end_time,
            step_number=self.state.n_steps,
            step_interval=step_interval,
        )

    def _log_step_completion_summary(self, results: list[ActionResult]) -> None:
        """Log step duration and success/failure counts."""
        if not results:
            return
        duration = time.time() - self._step_start_time
        ok = sum(1 for r in results if not r.error)
        errs = len(results) - ok
        log_step_completion(
            step=self.state.n_steps,
            duration=duration,
            ok_count=ok,
            err_count=errs,
            logger=logger,
        )

    # ── Error handling ────────────────────────────────────────────────

    async def _handle_step_error(self, error: Exception) -> None:
        """Classify and handle exceptions during step execution.

        Three-branch classification (following browser-use):
          1. InterruptedError → user interrupt, no failure count
          2. Connection errors → attempt reconnect, stop on timeout
          3. All other errors → increment failures, create error result
        """
        # Branch 1: User interrupt — not a failure
        if isinstance(error, InterruptedError):
            logger.warning("Agent interrupted mid-step")
            return

        # Branch 2: Connection errors — attempt reconnect, stop on timeout
        if _is_connection_error(error):
            logger.warning("Connection error, attempting reconnect: %s", error)
            for _ in range(self.reconnect_timeout):
                if await self.browser.reconnect():
                    logger.info("Reconnection succeeded, continuing")
                    self.state.last_result = [
                        ActionResult(error=f"Connection lost and recovered: {error}")
                    ]
                    return
                await asyncio.sleep(1)
            logger.error("Reconnection failed after %ds, stopping agent", self.reconnect_timeout)
            self.state.stopped = True
            return

        # Branch 3: All other errors — count and log
        self.state.consecutive_failures += 1
        is_final = self.state.consecutive_failures >= self.max_failures
        log_level = logging.ERROR if is_final else logging.WARNING
        logger.log(
            log_level,
            "Step %d failed (%d/%d): %s",
            self.state.n_steps,
            self.state.consecutive_failures,
            self.max_failures,
            error,
        )
        self.state.last_result = [ActionResult(error=str(error))]


# ── Helpers ────────────────────────────────────────────────────────────


def _redact_params_for_log(
    action_name: str,
    params: dict[str, Any],
    sensitive_map: dict[str, str] | None,
) -> dict[str, Any]:
    """Return a copy of ``params`` with sensitive fields redacted for logging.

    Mirrors ``_redact_history_data`` (views.py) but for a single action and
    **non-mutating** (returns a copy so the real ``action_params`` is untouched
    and the action still executes with real values). ``action_params`` reaches
    ``_execute_actions`` already restored to real values by client-side
    ``_restore_sensitive_in_output``; logging it raw would leak secrets.

    Only fields listed in ``_SENSITIVE_ACTION_FIELDS[action_name]`` are redacted
    (input_text.text / search.query / extract.query); other params (index, url,
    ...) are returned unchanged so the log stays readable. No-op when
    ``sensitive_map`` is None/empty.

    ``sensitive_map`` must be ``{placeholder: real_value}`` (the orientation
    ``redact_sensitive_string`` expects); obtained by inverting Agent's
    ``_sensitive_map`` — see ``StepPipeline._sensitive_map_for_log``.
    """
    if not isinstance(params, dict):
        return {}
    if not sensitive_map:
        return dict(params)
    fields = _SENSITIVE_ACTION_FIELDS.get(action_name)
    if not fields:
        return dict(params)
    redacted = dict(params)
    for f in fields:
        if isinstance(redacted.get(f), str):
            redacted[f] = redact_sensitive_string(redacted[f], sensitive_map)
    return redacted


def _is_connection_error(error: Exception) -> bool:
    """Detect connection/browser errors via isinstance and string patterns."""
    if isinstance(error, ConnectionError):
        return True
    msg = str(error).lower()
    return any(p in msg for p in _CONNECTION_ERROR_PATTERNS)


_CONNECTION_ERROR_PATTERNS = (
    "websocket connection closed",
    "connection closed",
    "connection reset",
    "connection refused",
    "browser has been closed",
    "browser closed",
    "no browser",
)


# Actions excluded from loop detection — always hash the same or are terminal.
_LOOP_EXEMPT_ACTIONS = frozenset({"wait", "done", "go_back"})
