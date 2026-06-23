"""Step pipeline: 5-stage decomposition of the agent step."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from tree_walker.agent.log_formatter import log_response, log_step_completion
from tree_walker.agent.views import ActionResult, AgentHistory
from tree_walker.browser.views import BrowserStateSummary
from tree_walker.prompts.system_prompt import build_state_message, build_system_prompt
from tree_walker.tools.models import ACTION_DEFINITIONS

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

_FALLBACK_DONE_OUTPUT: dict[str, Any] = {
    "evaluation_previous_goal": "No action returned",
    "memory": "",
    "next_goal": "Ending task",
    "action": {"name": "done", "params": {"text": "No action returned by LLM", "success": False}},
}


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
            self._finalize(browser_state, model_output, results)

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
        )
        self.messages.append({"role": "user", "content": state_msg})

        # 5. Inject budget warning (>=75% steps used)
        self._inject_budget_warning()

        # 6. Force done on last step
        self._force_done_on_last_step()

        # 7. Force done after consecutive failures
        self._force_done_after_failure()

        return browser_state, state_msg

    # ── Context injection helpers ─────────────────────────────────────

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
            self.messages.append({"role": "user", "content": msg})
            logger.info("Budget warning injected: %d/%d steps used", steps_used, self.max_steps)

    def _force_done_on_last_step(self) -> None:
        """Force LLM to call done on the last step."""
        if self.state.n_steps >= self.max_steps - 1:
            msg = (
                "LAST STEP: You have reached max_steps - this is your final step. "
                'You must call the "done" action now. '
                "Summarize what you have accomplished so far."
            )
            self.messages.append({"role": "user", "content": msg})
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
            self.messages.append({"role": "user", "content": msg})
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
    ) -> dict[str, Any]:
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
        self.messages.append({
            "role": "assistant",
            "content": (
                f"[{response.get('evaluation_previous_goal', '')}] "
                f"Goal: {response.get('next_goal', '')} | "
                f"Action: {response.get('action', {}).get('name', 'unknown')}"
            ),
        })

        # Log action decision (structured four-line block)
        action = response.get("action", {})
        action_name = action.get("name", "done")
        action_params = action.get("params", {})
        log_response(
            evaluation=response.get("evaluation_previous_goal", ""),
            memory=response.get("memory", ""),
            next_goal=response.get("next_goal", ""),
            action_name=action_name,
            action_params=action_params,
            step=self.state.n_steps,
            logger=logger,
        )

        self._current_model_call_id = model_call_id

        return response

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

    @staticmethod
    def _validate_action_params(response: dict[str, Any]) -> str | None:
        """Validate action params against the registered Pydantic model.

        Returns None if valid, or an error detail string if invalid.
        """
        action = response.get("action", {})
        name = action.get("name", "")
        params = action.get("params", {})

        definition = ACTION_DEFINITIONS.get(name)
        if definition is None:
            return None

        param_model = definition[0]
        flat_params = _flatten_params_for_validation(params, name)
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

        # Update plan state from model output (if planning enabled)
        if self._enable_planning and self.plan_manager:
            self.plan_manager.update_from_model_output(self.state, model_output)

        # Record each action to loop detector with exemption filtering.
        # Multi-action steps record each action individually so the detector
        # sees the full sequence; Phase 4 will refine failure semantics.
        actions = model_output.get("actions") or [model_output.get("action", {})]
        for action in actions:
            action_name = action.get("name", "done")
            action_params = action.get("params", {})
            if action_name not in _LOOP_EXEMPT_ACTIONS:
                self.loop_detector.record_action(action_name, action_params)

        # Phase 4 failure semantics:
        #   - all-error step (single-action OR multi-action all failed) → count
        #   - multi-action step with partial failure → do NOT count
        #     (loop_detector + replan handle recovery)
        #   - any success → reset counter
        if results and all(r.error for r in results):
            self.state.consecutive_failures += 1
            logger.debug("Consecutive failures: %d", self.state.consecutive_failures)
            return
        if results and any(r.error for r in results) and not all(r.error for r in results):
            logger.info(
                "Multi-action step had partial failure (%d/%d actions failed) "
                "— not incrementing consecutive_failures",
                sum(1 for r in results if r.error), len(results),
            )

        # Success → reset failure counter
        if self.state.consecutive_failures > 0:
            self.state.consecutive_failures = 0

        # Completion result logging
        if results and results[-1].is_done:
            result = results[-1]
            if result.success:
                logger.info(
                    "\n\033[32m Task SUCCESS\033[0m\n%s\n",
                    result.extracted_content or "",
                )
            else:
                logger.info(
                    "\n\033[31m Task FAILED\033[0m\n%s\n",
                    result.extracted_content or "",
                )

    # ── Stage 5: Finalize ─────────────────────────────────────────────

    def _finalize(
        self,
        browser_state: BrowserStateSummary | None,
        model_output: dict[str, Any] | None,
        results: list[ActionResult],
    ) -> None:
        """Record history, log summary, and advance step counter.

        Called from finally block — runs regardless of success or exception.
        n_steps is incremented last so all consumers see the current value.
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


def _flatten_params_for_validation(params: dict, action_name: str) -> dict:
    """Flatten nested params before Pydantic validation (mirrors Tools._flatten_params)."""
    if not params:
        return params
    if action_name in params and isinstance(params[action_name], dict):
        return params[action_name]
    dict_vals = {k: v for k, v in params.items() if isinstance(v, dict)}
    if len(dict_vals) == 1 and len(params) == 1:
        return list(dict_vals.values())[0]
    return params
