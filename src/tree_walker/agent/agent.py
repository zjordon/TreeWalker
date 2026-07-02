"""Main agent orchestrator implementing the Sense-Think-Act loop."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import threading
import uuid
from typing import Any

from tree_walker.agent.loop_detector import ActionLoopDetector
from tree_walker.agent.message_compactor import MessageCompactor
from tree_walker.agent.plan_manager import PlanManager
from tree_walker.agent.rerun import RerunMixin
from tree_walker.agent.step import StepPipeline
from tree_walker.agent.views import AgentHistoryList, ActionResult, AgentState
from tree_walker.browser.session import BrowserSession
from tree_walker.config import AgentSettings, LLMSettings
from tree_walker.llm.client import LLMClient
from tree_walker.prompts.system_prompt import build_system_prompt
from tree_walker.tools.actions import Tools

from tree_walker.agent.judge import JudgeEvaluator
from tree_walker.config import JudgeSettings

logger = logging.getLogger(__name__)


class Agent(StepPipeline, RerunMixin):
    """Browser automation agent.

    Usage::

        agent = Agent(
            task="Search for Python tutorials",
            llm=LLMClient(settings=LLMSettings(api_key="...")),
            browser=BrowserSession(settings=BrowserSettings(ws_url="...")),
        )
        history = await agent.run()
        print(history.final_result())
    """

    def __init__(
        self,
        task: str,
        llm: LLMClient,
        browser: BrowserSession,
        tools: Tools | None = None,
        settings: AgentSettings | None = None,
        sensitive_data: dict[str, str] | None = None,
        output_model: type[BaseModel] | None = None,
    ) -> None:
        self.task = task
        self.llm = llm
        self.browser = browser
        _settings = settings or AgentSettings()
        self.tools = tools or Tools(truncation=_settings.truncation, allowed_upload_paths=_settings.allowed_upload_paths, allowed_write_paths=_settings.allowed_write_paths, allowed_read_paths=_settings.allowed_read_paths, display_files_in_done_text=_settings.display_files_in_done_text, output_model=output_model)
        if _settings.action_page_filters:
            self.tools.apply_page_filters(_settings.action_page_filters)

        # extract 工具接线（对齐 browser-use：page_extraction_llm 默认=主 llm；extraction_schema 注入）
        if _settings.extract_llm is not None:
            self.tools._extract_llm = LLMClient(_settings.extract_llm)
        else:
            self.tools._extract_llm = self.llm
        self.tools._extraction_schema = _settings.extraction_schema
        ActionResult.display_max_chars = _settings.truncation.display_max_chars
        self._truncation = _settings.truncation
        self.max_steps = _settings.max_steps
        self.max_failures = _settings.max_failures
        self.llm_timeout = _settings.llm_timeout
        self.action_timeout = _settings.action_timeout
        self.reconnect_timeout = _settings.reconnect_timeout
        self.max_actions_per_step = _settings.max_actions_per_step
        self.wait_between_actions = browser._settings.wait_between_actions
        self.rerun_history_dir = _settings.rerun_history_dir

        self.state = AgentState()
        self.history = AgentHistoryList()
        self.loop_detector = ActionLoopDetector()
        self._compactor: MessageCompactor | None = None
        if _settings.message_compaction and _settings.message_compaction.enabled:
            self._compactor = MessageCompactor(_settings.message_compaction, self.llm)
        self.messages: list[dict[str, Any]] = []

        # Sensitive data filtering
        _sd = sensitive_data or _settings.sensitive_data
        if _sd:
            self._sensitive_map = {v: k for k, v in _sd.items()}
            self._safe_task = self.task
            for real_val, placeholder in self._sensitive_map.items():
                self._safe_task = self._safe_task.replace(real_val, placeholder)
            self.llm._sensitive_map = self._sensitive_map
        else:
            self._sensitive_map = None
            self._safe_task = self.task

        # Pause/resume control
        self._resume_event = asyncio.Event()
        self._resume_event.set()
        self._ctrl_c_count = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._prev_sigint = None
        self._stdin_thread: threading.Thread | None = None

        # Download tracking
        self._track_downloads = _settings.track_downloads

        # Planning system
        self._enable_planning = _settings.enable_planning
        self._enable_decision_attribution = _settings.enable_decision_attribution
        self._exploration_threshold = _settings.exploration_threshold
        self._replan_failure_threshold = _settings.replan_failure_threshold
        self.plan_manager: PlanManager | None = None
        if self._enable_planning:
            self.plan_manager = PlanManager()

        # Judge
        self._judge: JudgeEvaluator | None = None
        if _settings.judge and _settings.judge.enabled:
            self._judge = JudgeEvaluator(llm=self.llm, settings=_settings.judge)

        # Observability
        self._obs_bus = None
        self._obs_metrics = None
        self._obs_session_id = ""
        self._output_mode = getattr(llm, 'output_mode', 'standard')
        self._obs_metrics = None
        self._obs_session_id = ""
        if _settings.enable_observability:
            from tree_walker.observability import (
                AnomalyDetector,
                EventBus,
                JsonlRecorder,
                MetricsAggregator,
            )
            self._obs_session_id = uuid.uuid4().hex[:8]
            self._obs_bus = EventBus()
            self._obs_metrics = MetricsAggregator()
            recorder = JsonlRecorder(
                session_id=self._obs_session_id,
                log_dir=_settings.observability_log_dir,
            )
            anomaly_detector = AnomalyDetector(
                bus=self._obs_bus,
                max_steps=self.max_steps,
            )
            self._obs_bus.subscribe("*", self._obs_metrics.handle)
            self._obs_bus.subscribe("*", recorder.handle)
            self._obs_bus.subscribe("tool_result", anomaly_detector.handle)
            self._obs_bus.subscribe("model_result", anomaly_detector.handle)
            self._obs_bus.subscribe("step_end", anomaly_detector.handle)
            recorder.register(self._obs_bus.on_close)

        self._system_prompt = build_system_prompt(
            action_descriptions=self.tools.registry.get_action_descriptions_text(),
            task=self._safe_task,
            enable_decision_attribution=self._enable_decision_attribution,
            max_actions=self.max_actions_per_step,
        )
        self._tool_schema = self.tools.registry.get_tool_schema(
            enable_planning=self._enable_planning,
            output_mode=self._output_mode,
            max_actions=self.max_actions_per_step,
        )

    # ── Public API ─────────────────────────────────────────────────────

    def _finalize_session(self) -> None:
        """Emit SessionEndEvent and close the observability bus."""
        if not self._obs_bus:
            return
        from tree_walker.observability import SessionEvaluator
        from tree_walker.observability.events import SessionEndEvent

        metrics_summary = self._obs_metrics.get_summary() if self._obs_metrics else {}
        has_done = any(r.is_done for h in self.history.history for r in h.result)
        total_duration = sum(
            (h.state_summary or {}).get("duration", 0)
            for h in self.history.history
        )

        evaluator = SessionEvaluator()
        evaluation = evaluator.evaluate(has_done=has_done, metrics_summary=metrics_summary)

        self._obs_bus.emit(SessionEndEvent(
            step=self.state.n_steps,
            session_id=self._obs_session_id,
            total_steps=self.state.n_steps,
            total_duration_seconds=total_duration,
            summary=evaluation["summary"],
            evaluation=evaluation,
        ))
        self._obs_bus.close()

    async def run(self, keep_alive: bool = False) -> AgentHistoryList:
        """Execute the agent loop until done, max_steps, or max_failures."""
        await self.browser.start(track_downloads=self._track_downloads)

        initial_url = self._extract_url(self.task)
        if initial_url:
            try:
                await self.browser.navigate(initial_url)
            except Exception as e:
                logger.warning("Failed to navigate to initial URL: %s", e)

        self._setup_signal_handler()
        try:
            while self.state.n_steps <= self.max_steps:
                if self.state.stopped:
                    logger.info("Agent stopped by user")
                    break

                if self.state.consecutive_failures >= self.max_failures:
                    logger.warning(
                        "Max consecutive failures (%d) reached",
                        self.state.consecutive_failures,
                    )
                    break

                # Wait for resume if paused, with stdin listener
                if self.state.paused:
                    self._start_stdin_listener()
                await self._resume_event.wait()
                if self.state.stopped:
                    break

                try:
                    done = await self._step()
                    if done:
                        if self._judge:
                            await self._run_judge()
                        break
                except KeyboardInterrupt:
                    logger.info("Interrupted by user")
                    break
        finally:
            self._finalize_session()
            self._restore_signal_handler()
            if not keep_alive:
                await self.browser.stop()

        return self.history

    def stop(self) -> None:
        self.state.stopped = True
        self._resume_event.set()

    def pause(self) -> None:
        """Pause the agent after the current step completes."""
        self.state.paused = True
        self._resume_event.clear()
        logger.info("Agent paused (Ctrl+C again to stop, Enter to resume)")

    def resume(self) -> None:
        """Resume the agent from paused state."""
        self.state.paused = False
        self._ctrl_c_count = 0
        self._resume_event.set()
        logger.info("Agent resumed")

    def _sigint_handler(self, signum: int, frame) -> None:
        """Signal handler for Ctrl+C: first pause, second stop."""
        self._ctrl_c_count += 1
        if self._ctrl_c_count == 1:
            if self._loop:
                self._loop.call_soon_threadsafe(self.pause)
        else:
            if self._loop:
                self._loop.call_soon_threadsafe(self.stop)

    def _setup_signal_handler(self) -> None:
        """Register SIGINT handler for pause/stop."""
        self._loop = asyncio.get_running_loop()
        try:
            self._prev_sigint = signal.signal(signal.SIGINT, self._sigint_handler)
        except (ValueError, OSError):
            logger.warning("Cannot register SIGINT handler on this platform/thread")

    def _restore_signal_handler(self) -> None:
        """Restore previous SIGINT handler."""
        if self._prev_sigint is not None:
            signal.signal(signal.SIGINT, self._prev_sigint)
            self._prev_sigint = None

    def _start_stdin_listener(self) -> None:
        """Start a daemon thread that resumes the agent on Enter key."""
        if not sys.stdin.isatty():
            logger.info("stdin is not a TTY, stdin-based resume disabled")
            return
        if self._stdin_thread is not None and self._stdin_thread.is_alive():
            return

        def _listen():
            try:
                sys.stdin.readline()
                if self._loop and self.state.paused and not self.state.stopped:
                    self._loop.call_soon_threadsafe(self.resume)
            except Exception:
                pass

        self._stdin_thread = threading.Thread(target=_listen, daemon=True)
        self._stdin_thread.start()

    # ── Helpers ────────────────────────────────────────────────────────

    async def _run_judge(self) -> None:
        """Run independent Judge evaluation on the completed trace."""
        if not self._judge or not self.history.is_done():
            return

        final_result = self.history.final_result()
        judgement = await self._judge.judge(
            task=self._safe_task,
            history=self.history,
            final_result=final_result,
        )

        if judgement and self.history.history:
            last_step = self.history.history[-1]
            for r in last_step.result:
                if r.is_done:
                    r.judgement = judgement

            if judgement.verdict:
                logger.info("Judge verdict: SUCCESS")
            else:
                logger.warning("Judge verdict: FAILED — %s", judgement.failure_reason)

    def _last(self, field: str) -> str | None:
        if self.state.last_model_output:
            return self.state.last_model_output.get(field)
        return None

    def _trim_messages(self, max_messages: int = 20) -> list[dict[str, Any]]:
        """Keep recent messages to avoid exceeding context window."""
        if self._compactor:
            # Compactor manages trimming, but enforce a hard safety limit
            if len(self.messages) > max_messages * 3:
                logger.warning(
                    "Messages (%d) exceed safety limit (%d), trimming tail",
                    len(self.messages),
                    max_messages * 3,
                )
                return list(self.messages[-(max_messages * 3):])
            return list(self.messages)
        if len(self.messages) <= max_messages:
            return list(self.messages)
        return list(self.messages[-max_messages:])

    @staticmethod
    def _extract_url(task: str) -> str | None:
        """Extract a URL from the task description for initial navigation."""
        import re
        match = re.search(r'https?://[^\s<>"\']+', task)
        return match.group(0) if match else None
