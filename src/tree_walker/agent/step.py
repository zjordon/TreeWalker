"""Step pipeline: 5-stage decomposition of the agent step."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
import traceback
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from tree_walker.action_shape import (
    actions_of,
    honest_done_action,
    is_honest_failure_action,
    name_of,
    normalize_model_output,
    params_of,
)
# normalize_model_output 在 _normalize_llm_response 内使用（#176 P0-A 起
# 传 known_names；_get_next_action 经该 helper 间接调用）
from tree_walker.agent.actionability import (
    ACTIONABILITY_ACTIONS,
    is_file_input,
    wait_for_actionability,
)
from tree_walker.agent.log_formatter import BLUE, RESET, format_action_params, log_response, log_step_completion
from tree_walker.agent.views import (
    ActionResult,
    AgentHistory,
    DownloadInfo,
    StepMetadata,
    _SENSITIVE_ACTION_FIELDS,
    redact_sensitive_string,
)
from tree_walker.browser.image_utils import resize_screenshot_bytes
from tree_walker.browser.views import BrowserStateSummary, DOMInteractedElement
from tree_walker.browser.url_utils import extract_host_with_port
from tree_walker.config import _DEFAULT_LLM_SCREENSHOT_SIZE, model_supports_vision
# issue #194：限流/网络基建错误谓词（client.py 不依赖 agent 包，无环）——
# L3 分罪与 L2 client 层退避共用同一分类
from tree_walker.llm.client import is_llm_infra_error
from tree_walker.prompts.system_prompt import build_state_blocks, build_state_message, build_system_prompt

if TYPE_CHECKING:
    from tree_walker.config import TruncationSettings
    from tree_walker.agent.loop_detector import (
        ActionLoopDetector,
        FailureStreakTracker,
        ZeroResultStreakTracker,
    )
    from tree_walker.agent.message_compactor import MessageCompactor
    from tree_walker.agent.plan_manager import PlanManager
    from tree_walker.browser.session import BrowserSession
    from tree_walker.llm.client import LLMClient
    from tree_walker.observability.event_bus import EventBus
    from tree_walker.tools.actions import Tools

logger = logging.getLogger(__name__)

_PARAM_VALIDATION_MAX_RETRIES = 2

# 无效动作（无名字/空 action）的澄清消息——外梯（_get_action_with_retry）与
# 参数校验内梯共用同一措辞（issue #176 P0-B：内梯对无效动作与外梯对称，先
# 澄清重试而非一次 fallback 死刑）。
_INVALID_ACTION_CLARIFICATION = (
    "You forgot to return an action. Please respond with a valid "
    "action using the agent_response tool, including your evaluation, "
    "memory, next goal, and action."
)

# P0 消息分类管理：内部 _type 键标记消息类别（不送 SDK，_trim_messages 边界剥除）。
# 对齐 browser-use MessageManager 的 state/context/agent_history 分类。
_MSG_TYPE = "_type"
TYPE_STATE = "state"        # 当前页状态消息：每步替换，全局唯一
TYPE_CONTEXT = "context"    # 注入提示（budget/last/failure/loop）：每步清理后重灌
TYPE_USER = "user"          # 持久 user 消息（任务说明、conversation summary）
TYPE_ASSISTANT = "assistant"

def _fallback_done_output() -> dict[str, Any]:
    """review7 #5：合成 done 统一挂 _HonestDone 带外标记（校验放行；variant B
    不烧重试）。每次调用返回新实例，text 沿用既有文案。"""
    action = honest_done_action()
    action["params"]["text"] = "No action returned by LLM"
    return {
        "evaluation_previous_goal": "No action returned",
        "memory": "",
        "next_goal": "Ending task",
        "action": action,
        "actions": [action],
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
    # issue #194：LLM 基建失败（限流/网络）连续上限 + infra 步豁免步数递增标记
    max_infra_failures: int
    _skip_step_increment: bool
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
    _enable_grid_meta: bool  # P7 tool_layer B2：[Grid] 元信息渲染开关（session 侧照常采集）
    _enable_sensitive_description: bool
    _enable_skill_injection: bool
    _enable_task_skill_injection: bool  # P7 路线三：[Task Skill] 注入开关（默认关）
    _task_skill_text: str | None  # run() 匹配一次后的命中文本（每步注入）
    _task_skill_slug: str | None  # 命中 slug（obs 事件用）
    _max_history_items: int
    # LLM 视觉通道（阶段二，issue #175）：配置门 + 降采样目标尺寸
    _use_vision: bool
    _llm_screenshot_size: tuple[int, int] | None
    _system_prompt: str
    _tool_schema: dict[str, Any]
    loop_detector: ActionLoopDetector
    # issue #186 现象①：失败感知连败跟踪（agent.py 实例化，见 FailureStreakTracker）
    failure_streak: FailureStreakTracker
    # issue #186-c2 形态②：同类查询连续零结果跟踪（见 ZeroResultStreakTracker）
    zero_result_streak: ZeroResultStreakTracker
    _pending_zero_result_nudge: tuple[str, str] | None
    # issue #186 现象②：done(success=True) 不确定标记门禁开关（AGENT_DONE_GATE）
    _enable_done_gate: bool
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

    async def _cached_viewport(self) -> tuple[int, int] | None:
        """视口 (clientWidth, clientHeight)，懒加载缓存（视口罕变，整 session 复用）。

        供 ToolCallEvent 元素几何归一化用。browser 无该方法 / CDP 失败 → None（bbox 降级 None）。
        """
        vp = getattr(self, "_viewport_cache", _VIEWPORT_UNSET)
        if vp is not _VIEWPORT_UNSET:
            return vp  # type: ignore[return-value]
        getter = getattr(self.browser, "_get_viewport_size", None)
        try:
            vp = await getter() if getter is not None else None
        except Exception:
            vp = None
        self._viewport_cache = vp  # type: ignore[assignment]
        return vp

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
            # review2 #5（issue #186）：止损 nudge 的档位此刻才提交——_prepare_context
            # 里 peek 只暂存；LLM 调用失败/超时（输出未送达）时首报在下步重发，
            # 不会"查询即消费"地静默丢失。
            self._ack_pending_streak_nudge()
            # 归一化在 _get_next_action 内完成（校验/truncate/emit 之前，
            # review7 #6）——其每条返回路径（含 fallback done）都已归一化，
            # 此处不再重复调用（review8 #8：三重归一化 + 同一 WARNING ×3/步）
            results = await self._execute_actions(model_output, browser_state)
            self._post_process(results, model_output)

            if any(r.is_done for r in results):
                return True
            if self.state.consecutive_failures >= self.max_failures:
                return True
        except Exception as e:
            try:
                await self._handle_step_error(e)
            except Exception as he:
                # review3 #4：错误处理器自身故障（如半死浏览器上 reconnect 再抛）
                # 降级为日志——不得从 except 逃出杀死 run（finally 只兜 _finalize）。
                logger.error(
                    "step error handler itself failed: %s (original error: %s)", he, e,
                )
            return False
        finally:
            # finally 边界（issue #173，PR #174 review #5）：finalize 含历史追加与
            # obs emit。finally 里抛异常会越过上方 except 杀死整个 run——778/782
            # 同类死法，必须整体兜住：历史/obs 降级也强过任务死亡。
            # （订阅者异常的根因已在 EventBus.emit 做 per-handler 隔离；这里兜
            # _finalize 其余部分——历史追加/元数据等。）
            try:
                await self._finalize(browser_state, model_output, results)
            except Exception as e:
                logger.error("_finalize failed — history/obs degraded for this step: %s", e)
                # 降级累计计数（review4 #4 / review6 #4/#5）：**只增不清零**——
                # 任何「成功即清零」的连续计数器都会被交错的 pause/stop 空跑或
                # 中途回复洗掉（缺步的 run 报 0，与干净 run 不可区分）；累计值
                # 语义即「history 里缺失的步数」，run() 达阈值升级终止。
                self.state.finalize_degraded_steps += 1
            # 计数器边界（review2 #1 / review3 #8 单一所有者）：n_steps 递增只在此
            # 处（_finalize 尾部副本已删）——吞异常不得跳过它（否则 run() 的
            # while 循环退化为无界 livelock），正确性也不再依赖「递增恰是
            # _finalize 最后一条语句」这条跨千行的顺序约定。
            # issue #194 唯一豁免：Branch 2.5 的 infra 失败步不烧步数预算，
            # 其 livelock 防护由 run() 顶部的 infra_failures 预算检查接管
            # （独立、有界）。getattr 守卫：FakeAgent/MagicMock 测试桩不经
            # Agent.__init__ 时按未置位处理。
            if not getattr(self, "_skip_step_increment", False):
                self.state.n_steps += 1
            self._skip_step_increment = False

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

    def _current_task_skill_text(self) -> str | None:
        """每步取任务级 skill 文本（P7 路线三，docs/p7/03 §五）。

        开关门控集中在此（而非调用点三元）：门控是评测红线的执行点，独立成方法
        才能被测试直接驱动——调用点三元会退化成与测试各写一份的同义反复。
        """
        if self._enable_task_skill_injection and self._task_skill_text:
            return self._task_skill_text
        return None

    async def _prepare_context(self) -> tuple[BrowserStateSummary, str]:
        """Gather browser state and build the state message for the LLM."""
        # 0. P0：每步入口清理上一步的注入提示（budget/last/failure/loop），
        # 避免 context 消息累积污染。enable_message_typing=False 时 no-op。
        self._clear_context_messages()

        # 1. Get browser state
        # LLM 视觉通道（screenshot.md 阶段二，issue #175）：视觉门开时恢复每步截图
        # 采集；默认 use_vision=False → include_screenshot=False，与阶段一断路止血
        # 行为完全一致。采集编排维持 get_state 现状串行（§2.2.1 决策：gather 化
        # 留作实测驱动的后续优化，勿在此并发）。
        browser_state = await self.browser.get_state(
            include_screenshot=self._vision_gate_open(),
        )
        # 临时调试：env AGENT_DEBUG_DUMP_DIR 设定时，每步 dump element_tree_text（issue #157）
        await self._maybe_dump_step_dom(browser_state)

        # 2. Log step context
        self._log_step_context(browser_state)

        # 2b. Update action models based on current page URL
        self._update_action_models_for_page(browser_state.url)

        # 3. Record page state for loop detection (3-dim: url + element_count + dom text hash)
        dom = browser_state.dom_state
        self.loop_detector.record_page_state(
            browser_state.url,
            dom.element_tree_text if dom else "",
            len(dom.selector_map) if dom else 0,
        )

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
        if nudge:
            logger.debug(
                "Loop detection nudge injected (repetition=%d, stagnation=%d)",
                self.loop_detector.max_repetition_count,
                self.loop_detector.consecutive_stagnant_pages,
            )
        # issue #186 现象①：连败止损 nudge（失败感知，阈值 2/4 分级带去抖）——与
        # loop nudge 同位并入；state message 每步重建，天然自清。review2 #5：peek
        # 只暂存不落档——LLM 调用失败/超时（输出未送达）时，_step 不调 ack，下步
        # 重建状态消息时首报重发，不会"查询即消费"地静默丢失。
        streak_candidate = self.failure_streak.peek_nudge()
        self._pending_streak_nudge = streak_candidate
        streak_nudge = streak_candidate[2] if streak_candidate else None
        if streak_nudge:
            logger.info("Failure-streak nudge staged: %s", streak_nudge[:100])
            nudge = "\n\n".join(x for x in (nudge, streak_nudge) if x)
        # issue #186-c2 形态②：零结果降级 nudge——同 peek/ack 语义（查询不
        # 消费；C 轮 544：精确名过滤 0 结果 ×N 不换策略）
        zero_candidate = self.zero_result_streak.peek_nudge()
        self._pending_zero_result_nudge = zero_candidate
        if zero_candidate:
            logger.info("Zero-result nudge staged: %s", zero_candidate[1][:100])
            nudge = "\n\n".join(x for x in (nudge, zero_candidate[1]) if x)

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
        # P7 tool_layer B2：网格元信息（total/sorting/活动过滤残留）——防 128 型
        # 「脑补已排序」与残留过滤误读；非网格页 grid_meta 为 None 不渲染。
        grid_meta = browser_state.grid_meta if self._enable_grid_meta else None
        sensitive_desc = (
            self._build_sensitive_description(browser_state.url)
            if self._enable_sensitive_description
            else None
        )
        skill_desc = (
            self._build_skill_description(browser_state.url)
            if self._enable_skill_injection
            else None
        )
        # P7 路线三（docs/p7/03 §五）：任务级 skill——run() 匹配一次的文本每步注入，
        # 与 [Domain Skill] 同构（state message 每步重建，compaction 无需特殊处理）。
        task_skill_desc = self._current_task_skill_text()

        # P6 后续 I1：把「本步活动 skill」事件化（web 前端 RunView chip 用）。
        # 仅 host/命中/字数，不传全文；emit 同步、与 agent 同线程，安全。
        # P7 路线三：任务级分开标（task_slug/task_skill_chars）——站点级 off 或
        # 无站点卡时 chip 只看 skill_loaded 会误报「未注入」。
        if self._obs_bus:
            from tree_walker.observability.events import SkillActiveEvent
            _skill_host = extract_host_with_port(browser_state.url)
            self._obs_bus.emit(SkillActiveEvent(
                step=self.state.n_steps, session_id=self._obs_session_id,
                host=_skill_host,
                skill_loaded=bool(skill_desc),
                char_count=len(skill_desc or ""),
                task_slug=self._task_skill_slug or "",
                task_skill_chars=len(task_skill_desc or ""),
            ))

        state_kwargs = dict(
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
            skill_description=skill_desc,
            task_skill_description=task_skill_desc,
            grid_meta=grid_meta,
        )
        # 阶段二：截图就位时 state 消息升级为 [text, image] blocks（Anthropic
        # 格式）；否则保持纯文本 str——视觉关（默认）与视觉开但无图（step 0
        # 新标签页/截图失败）都走 str，与既有行为零差异。
        screenshot_b64 = self._prepare_state_screenshot_b64(browser_state)
        if screenshot_b64 is not None:
            state_msg: str | list[dict[str, Any]] = build_state_blocks(
                browser_state, screenshot_b64, **state_kwargs,
            )
        else:
            state_msg = build_state_message(browser_state, **state_kwargs)
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

    # ── LLM 视觉通道（screenshot.md 阶段二，issue #175）─────────────────

    def _vision_gate_open(self) -> bool:
        """视觉门：配置门（use_vision）+ 模型门（已知视觉名单）。

        **逐步评估**而非 run 开始锁存——client 侧 fallback 切换会改 ``llm.model``，
        切到文本模型后本门自动关（后续步不再采图/带图，配合 client 滤图双保险）。
        模型判定见 ``config.model_supports_vision``（P0 发现：端点对文本模型+
        图不报错只静默致盲，判定只能客户端做）。
        """
        return self._use_vision and model_supports_vision(getattr(self.llm, "model", None))

    def _prepare_state_screenshot_b64(self, browser_state: BrowserStateSummary) -> str | None:
        """当步截图 → 降采样 → Anthropic image block 的 b64 payload。

        §2.5 边界（返回 None = 只发文本，绝不因图挂步）：
          - 视觉门关（use_vision off / 模型非视觉）；
          - 新标签页 step 0（空白页无信息量）；
          - 截图为 None（get_state 内截图失败已 warning 降级）；
          - b64 编码异常（极端防御）。
        降采样失败 / 无 Pillow：``resize_screenshot_bytes`` 原样返回原图
        （可能很大但可用，warning 已在 helper 内记录）。
        """
        if not self._vision_gate_open():
            return None
        if self._is_new_tab_step_zero(browser_state):
            return None
        shot = browser_state.screenshot
        if not shot:
            return None
        # size 兜底（PR #177 review round2 CONFIRMED 修复）：AgentSettings 的
        # size 是 load 时按 LLM_MODEL env 快照的（文本主模型 → None=不预置），
        # 而本门每步按**活模型**判定——fallback 切到视觉模型后门开但 size 仍
        # None 会回流全分辨率原图。门开即模型是视觉的，未显式配置就走默认。
        target = self._llm_screenshot_size or _DEFAULT_LLM_SCREENSHOT_SIZE
        resized = resize_screenshot_bytes(shot, target)
        try:
            return base64.b64encode(resized).decode("ascii")
        except Exception as e:  # noqa: BLE001
            logger.warning("screenshot b64 encode failed, sending text-only: %s", e)
            return None

    def _is_new_tab_step_zero(self, browser_state: BrowserStateSummary) -> bool:
        """新标签页 step 0 判定（§2.5 边界 1）：url 空/about:blank 且 DOM 空。"""
        if self.state.n_steps != 0:
            return False
        url = (browser_state.url or "").strip().lower()
        dom = browser_state.dom_state
        dom_empty = not dom or not (dom.element_tree_text or "").strip()
        return url in ("", "about:blank") and dom_empty

    async def _maybe_dump_step_dom(self, browser_state: BrowserStateSummary) -> None:
        """临时调试：env AGENT_DEBUG_DUMP_DIR 设定时，每步把 element_tree_text + JS 直查实际 DOM 落盘。

        排查 dom_snapshot 是否漏抓已存在的表单子树（issue #157）：
        - JS probe 查实际 DOM 的表单控件数 + name input/form 的 display/rect（页面真实可见性）；
        - element_tree_text 是 dom_snapshot 生成给模型的视图。
        对照两者即可判断「表单真 hidden」还是「dom_snapshot 漏抓」。
        排查完取消设置即关；不设时本方法 no-op，对探索零影响。
        """
        import os
        dump_dir = os.environ.get("AGENT_DEBUG_DUMP_DIR")
        if not dump_dir:
            return
        dom = browser_state.dom_state
        tree = getattr(dom, "element_tree_text", "") if dom else ""
        js_probe = await self._dump_js_probe()  # 实际 DOM 可见性（对照 dom_snapshot）
        try:
            os.makedirs(dump_dir, exist_ok=True)
            from datetime import datetime, timezone
            ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
            step = self.state.n_steps
            path = os.path.join(dump_dir, f"step_{step:02d}.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"=== step {step} | url={browser_state.url} | {ts} ===\n\n")
                f.write("--- JS probe (actual DOM) ---\n")
                f.write(js_probe)
                f.write("\n\n--- element_tree_text (dom_snapshot) ---\n")
                f.write(tree)
        except OSError as e:
            logger.warning("step dom dump failed: %s", e)

    async def _dump_js_probe(self) -> str:
        """直查实际 DOM 的表单结构 + 所有控件布局信息（issue #157：定位 dom_snapshot 是否漏抓）。

        返回 JSON 摘要（formCount / nameInputCount / 每个 input·select·textarea 的 rect +
        offsetWidth + computed width + offsetParent）+ form 与 body 的 outerHTML 片段。
        对照 dom_snapshot 的 element_tree_text 即可判断「表单有布局却被剪」还是「真无布局」：
        offsetParent=null / offsetWidth=0 → 真无布局；offsetWidth≠0 但 rect=0 → 采集问题。
        """
        js = r"""(function(){
  var form = document.querySelector('form');
  var controls = Array.from(document.querySelectorAll('input,select,textarea')).map(function(el){
    var r = el.getBoundingClientRect(); var cs = getComputedStyle(el);
    return {tag:el.tagName, name:el.name, type:el.type, id:el.id,
      rect:[Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)],
      offsetW:el.offsetWidth, offsetH:el.offsetHeight, offsetParent:!!el.offsetParent,
      csW:cs.width, csH:cs.height};
  });
  var summary = JSON.stringify({
    formCount: document.querySelectorAll('form').length,
    nameInputCount: document.querySelectorAll('input[name="name"]').length,
    controls: controls
  });
  var formHTML = form ? form.outerHTML.slice(0,3000) : "<no form>";
  var bodyHTML = document.body.outerHTML.slice(0,2000);
  return summary + "\n\n--- form outerHTML (first 3000 chars) ---\n" + formHTML
    + "\n\n--- body outerHTML (first 2000 chars) ---\n" + bodyHTML;
})()"""
        try:
            result = await self.browser.client.send.Runtime.evaluate(
                {"expression": js, "returnByValue": True},
                session_id=self.browser.current_session_id,
            )
            return result.get("result", {}).get("value", "<no value>")
        except Exception as e:  # noqa: BLE001
            return f"<js probe failed: {e!r}>"

    # ── Context injection helpers ─────────────────────────────────────

    @staticmethod
    def _strip_type(msg: dict[str, Any]) -> dict[str, Any]:
        """P0：剥除内部 ``_type`` 键（送 SDK 前的边界）。无该键时原样返回。"""
        if _MSG_TYPE not in msg:
            return msg
        return {k: v for k, v in msg.items() if k != _MSG_TYPE}

    def _ack_pending_streak_nudge(self) -> None:
        """review2 #5（issue #186）：止损 nudge 档位的提交侧。

        _prepare_context 里 peek 只暂存到 ``_pending_streak_nudge``；本方法在
        LLM 响应确实取得后由 _step 调用——若本步 LLM 调用失败/超时/用户停止
        （输出未送达模型），ack 不发生，下步重建状态消息时同一档位重发，首报
        不会被去抖永久吞掉。
        """
        pending = getattr(self, "_pending_streak_nudge", None)
        if pending is not None:
            self.failure_streak.ack_nudge(pending[0], pending[1])
            self._pending_streak_nudge = None
        # issue #186-c2 形态②：零结果降级 nudge 同 ack 语义
        pending_zr = getattr(self, "_pending_zero_result_nudge", None)
        if pending_zr is not None:
            self.zero_result_streak.ack_nudge(pending_zr[0])
            self._pending_zero_result_nudge = None

    def _set_state_message(self, content: str | list[dict[str, Any]]) -> None:
        """设置当前步状态消息，并保留上一份 state 供 LLM 前后对比。

        ``enable_message_typing=True`` 时保留**最近 2 份** state（previous + current），
        而非 P0 原版的"仅留 1 份"：纯替换会让模型丧失 before/after DOM 对比，无法确认
        动作是否生效——抖音封面上传回归：上传后画布新增的 ``<img>`` 节点必须与上一步的
        空画布对比才能确认成功；只剩当前 state 时，模型被其他空槽位残留的"点击上传"
        占位文 + 变化的 input 索引误导，误判"上传没生效"而反复重试。保留 2 份既恢复对比
        能力，又有界（远小于 P0 前无界累积的 token 成本）。False 时回退原始 append。

        阶段二（issue #175）：``content`` 可为 Anthropic block list（视觉开时
        ``[text, image]``）。保留的旧 state **丢图留文**——before/after 对比的价值
        在文本 DOM（索引/结构），旧截图只是徒增 token；恒定单图在飞让图片 token
        成本可预算（与消息压缩"丢图留文"同思路，落在 state 槽位上）。
        """
        if not self._enable_message_typing:
            self.messages.append({"role": "user", "content": content})
            return
        # 保留最近 1 份旧 state（让 LLM 能 before/after 对比），删更老的。
        # state_idxs[:-1] = 除最近一份外的全部旧 state 索引。
        state_idxs = [i for i, m in enumerate(self.messages) if m.get(_MSG_TYPE) == TYPE_STATE]
        drop = set(state_idxs[:-1])
        self.messages = [m for i, m in enumerate(self.messages) if i not in drop]
        if isinstance(content, list):
            for m in self.messages:
                if m.get(_MSG_TYPE) == TYPE_STATE and isinstance(m.get("content"), list):
                    m["content"] = [
                        b for b in m["content"]
                        if not (isinstance(b, dict) and b.get("type") == "image")
                    ]
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
        state_message: str | list[dict[str, Any]],
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
            # issue #194 review3 #1/#2：wait_for 起点向 LLM client 登记步级退避
            # 窗口——梯子内（澄清重试/R4 递归/done-gate）所有 L2 退避共享同一
            # deadline、单次预算按 llm_timeout 派生，防终点异常变形为
            # TimeoutError 掉回 Branch 3 能力失败。getattr 守卫兼容测试桩。
            set_window = getattr(self.llm, "set_llm_window", None)
            if set_window is not None:
                set_window(self.llm_timeout)
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

        # 管线入口归一化（review6 #9 / review7 #6）：必须在校验 / truncate /
        # emit / assistant 消息构造**之前**——truncate 会把裸列表元素提升为
        # 镜像，未归一化形态先到 emit/log 就崩（pydantic str 字段 / str.get）。
        # client choke point 之后再兜一层（幂等），覆盖注入/自定义 LLM；
        # #176 P0-A 起带 known_names（梯子内已归一化，此处二次幂等 no-op）。
        self._normalize_llm_response(response)

        # Hard-cap actions to max_actions_per_step (browser-use service.py:1950-1951).
        # The system prompt and schema maxItems only *tell* the LLM the limit;
        # this is the runtime safety net for when small models ignore them and
        # emit too many actions (which would run on stale DOM as earlier actions
        # mutate the page). Before the ModelResultEvent emit so the reported
        # action_name matches what will actually execute.
        response = self._truncate_actions(response)

        if self._obs_bus:
            from tree_walker.observability.events import ModelResultEvent
            _usage = response.get("usage") or {}  # P6 后续 I2：LLMClient 透传的 token usage
            self._obs_bus.emit(ModelResultEvent(
                step=self.state.n_steps, session_id=self._obs_session_id,
                model_call_id=model_call_id,
                # review7 #8：emit 字段统一 str(x or "")——LLM 显式输出
                # next_goal: null（key 存在）曾把 None 塞进 pydantic str 字段、
                # ValidationError 丢弃整个有效步骤
                action_name=str(name_of(response.get("action")) or ""),
                next_goal=str(response.get("next_goal") or ""),
                input_tokens=_usage.get("input_tokens"),
                output_tokens=_usage.get("output_tokens"),
            ))

        # Record assistant message for conversation history
        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": (
                f"[{response.get('evaluation_previous_goal') or ''}] "
                f"Goal: {response.get('next_goal') or ''} | "
                # review7 #6/#8：归一化后镜像可能仍是标量/无名字典（多元素
                # 畸形头走澄清重试前）——name_of 兜底，不再裸 .get 崩
                f"Action: {name_of(response.get('action')) or 'unknown'}"
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
        action_name = str(name_of(response.get("action")) or "unknown")
        action_params = params_of(response.get("action"))
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
                # 阶段二：content 可为 block list——text block 取全文，image block
                # 摘要为大小标记（dump 人类可读审计件，不落几 MB 的 b64）。
                if isinstance(content, list):
                    parts = []
                    for b in content:
                        if not isinstance(b, dict):
                            continue
                        if b.get("type") == "text":
                            parts.append(b.get("text", ""))
                        elif b.get("type") == "image":
                            kb = len((b.get("source") or {}).get("data", "")) // 1024
                            parts.append(f"[screenshot: {kb} KiB base64 omitted]")
                    content = "\n".join(p for p in parts if p)
                lines.append(f"\n--- {role} ---\n{content}")
            lines.append(f"\n--- model_output ---\n{json.dumps(model_output, ensure_ascii=False, indent=2)}")
            target.write_text("\n".join(lines), encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to save conversation for step %d: %s", self.state.n_steps, e)

    def _normalize_llm_response(self, response: dict[str, Any]) -> dict[str, Any]:
        """issue #176 P0-A：live 管线对每个 LLM 响应的 registry 感知归一化。

        在 client choke point 归一化（无 registry 依赖，照旧不传 known_names）
        之上补一层：shape 合法但名字未注册的动作（模型把 agent_response 的
        响应字段 ``plan_update``/``current_plan_item`` 误发为动作名等）从批次
        移除并刷新镜像——头部未知名不再把整批拖进「Unknown action」重试梯
        （兄弟动作陪葬，issue #176 死亡链第 2 环）。``registry.actions`` 恒为
        全量注册表：page 过滤只影响 schema 暴露、不影响按名查找
        （``_validate_action_params`` 同源语义，后者保留作背带——注入/旁路
        LLM 绕过此处时兜底）。非 dict 响应原样透传（交 ``_is_valid_action``
        判假进澄清梯）。
        """
        if not isinstance(response, dict):
            return response
        return normalize_model_output(
            response,
            known_names=frozenset(self.tools.registry.actions),
        )

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

        # #176 P0-A：校验前归一化（含 known_names 丢弃 + 镜像刷新）——否则
        # 头部未注册名的连坐在进 _is_valid_action 之前就已注定
        response = self._normalize_llm_response(await self.llm.get_action(
            system_prompt=self._system_prompt,
            messages=messages,
            tool_schema=self._tool_schema,
        ))

        if self._is_valid_action(response):
            # issue #186 现象②：形状校验通过的响应过一次 done 完整性门禁
            #（只对 done+success=True 生效，其余原样穿透）
            return await self._gate_uncertain_success_done(
                await self._validate_params_or_retry(response, messages), messages)

        # Retry: append clarification message
        logger.warning("LLM returned empty action, retrying with clarification...")
        retry_messages = list(messages) + [{
            "role": "user",
            "content": _INVALID_ACTION_CLARIFICATION,
        }]
        response = self._normalize_llm_response(await self.llm.get_action(
            system_prompt=self._system_prompt,
            messages=retry_messages,
            tool_schema=self._tool_schema,
        ))

        if self._is_valid_action(response):
            return await self._gate_uncertain_success_done(
                await self._validate_params_or_retry(response, messages), messages)

        # Fallback: insert safe done action
        logger.warning("LLM still returned empty action after retry, using fallback done")
        return _fallback_done_output()

    async def _gate_uncertain_success_done(
        self,
        response: dict[str, Any],
        messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """issue #186 现象②：done(success=True) 携带未消解不确定标记时的步内门禁。

        task_64 形态：step16 memory 明写 ``Emma Davis=1?`` + 60 行未读缺口，
        问号无新证据蒸发后仍 done(success=True) 收题（DB 真值该名字恰好是被
        漏计的那个）。门禁只把 agent 自己写下的疑虑当真：

        - 只拦 ``success`` 为 True 的 done（缺省=True 一并拦）；``success=False``
          的诚实收题必须畅通；honest-done（带外标记）跳过；
        - 每 run 封顶 ``_DONE_GATE_MAX_PER_RUN`` 次（``AgentState.done_gate_uses``
          计数），单次调用只重试一次——重试响应仍带标记则放行（agent 坚持）；
        - 重试响应过一次 ``_validate_action_params``（纯形状校验，不再进 LLM
          梯）；无效或无动作则放行原响应。
        """
        action = response.get("action") or {}
        if str(name_of(action) or "") != "done":
            return response
        if is_honest_failure_action(action):
            return response
        params = params_of(action)
        # review2 #2：镜像执行侧/pydantic lax 的强转语义再判定——"success": "true"
        # 字符串经 lax 校验通过但不回写原 dict，`is not True` 的精确布尔判定会把
        # 它当 success=False 放行、跳过门禁。
        raw_success = params.get("success", True)
        if isinstance(raw_success, str):
            raw_success = raw_success.strip().lower() in ("true", "t", "yes", "y", "on", "1")
        if not bool(raw_success):
            return response
        if not getattr(self, "_enable_done_gate", True):
            return response
        if self.state.done_gate_uses >= _DONE_GATE_MAX_PER_RUN:
            return response
        # review4 #3：text 是对外交付物——agent 常复述任务问句（"Q: … $50?"），
        # 词尾 ? 对 text 无引语豁免，会误伤完整正确的答案；text 维度只扫不确定
        # 关键词，词尾 ? 仅对自评字段（evaluation/memory）生效。
        hits = scan_uncertainty_markers(
            str(response.get("evaluation_previous_goal") or ""),
            str(response.get("memory") or ""),
        ) or _scan_uncertainty_keywords(str(params.get("text") or ""))
        if not hits:
            return response
        self.state.done_gate_uses += 1
        logger.warning(
            "done(success=True) with unresolved uncertainty markers %s — "
            "verification retry (%d/%d)",
            hits, self.state.done_gate_uses, _DONE_GATE_MAX_PER_RUN,
        )
        feedback = (
            "Your own evaluation/memory contains unresolved uncertainty "
            f"(matched: {', '.join(repr(h) for h in hits)}). You are about to "
            "call done(success=true) on incomplete data. Either (a) verify the "
            "missing pieces with tools first, or (b) call done(success=false) "
            "describing exactly what was accomplished and what remains "
            "unverified. Do NOT restate done(success=true) while the same "
            "markers remain unresolved."
        )
        retry_messages = list(messages) + [{"role": "user", "content": feedback}]
        # review 修正：软干预不得把已握有合法 done 响应的步变成失败步——重试调用
        # 抛 API/网络异常时放行原响应（否则异常一路上抛 _handle_step_error 计
        # consecutive_failures，最坏把 run 推向 max_failures 终止）。用户停止信号
        # （InterruptedError）照常放行传播。review2 #1：内层小超时让预算耗尽的
        # 取消先以 TimeoutError（Exception 形态）落在本 except 内放行原响应。
        # review4 #1：取消本身必须原样 re-raise——3.12+ 外层 wait_for 基于
        # asyncio.timeout，协程吞掉取消后 __aexit__ 在 EXPIRING 态仍抛
        # TimeoutError（"放行"不成立）；外部强制 task.cancel()（server 关停）
        # 被吞后也不会在后续 await 自动再触发。不设 CancelledError 子句，保留
        # 取消语义交由外层转换/传播。
        try:
            # review5 #1：外层 llm_timeout 从 Think 阶段起点（_step_start_time）计时，
            # 内层必须按**剩余额度**取小——绝对值 min(llm_timeout, 60) 在首调+参数梯
            # 已耗 >llm_timeout-60s 时会让外层先到期：取消以 CancelledError 穿透本
            # except（按 review4 #1 不捕获），被外层转成 TimeoutError → 合法 done 步
            # 变失败步。剩余额度耗尽时 retry_timeout 归 0 → 内层立即 TimeoutError
            # （Exception 形态）落在兜底里放行原响应并回滚预算。
            elapsed = time.time() - getattr(self, "_step_start_time", time.time())
            retry_timeout = max(0.0, min(
                _DONE_GATE_RETRY_TIMEOUT, self.llm_timeout - elapsed))
            retried = self._normalize_llm_response(await asyncio.wait_for(
                self.llm.get_action(
                    system_prompt=self._system_prompt,
                    messages=retry_messages,
                    tool_schema=self._tool_schema,
                ),
                timeout=retry_timeout,
            ))
        except InterruptedError:
            # review5 #2：与 except Exception 同语义回滚——pause（非 stop）后 run
            # 会继续，重试未产出却烧掉本档预算，两次即静默失效；stop 场景 run
            # 结束，回滚无副作用。
            self.state.done_gate_uses -= 1
            raise
        except Exception as e:
            # review4 #2：重试未产出（异常放行）不消耗每 run 仅 2 次的门禁预算
            # ——两次基础设施抖动即让门禁对本 run 静默失效。
            self.state.done_gate_uses -= 1
            logger.warning(
                "done-gate verification retry failed (%s: %s) — passing "
                "through original response (budget rolled back)",
                type(e).__name__, e,
            )
            return response
        if not self._is_valid_action(retried):
            return response
        if self._validate_action_params(retried) is not None:
            return response
        return retried

    async def _validate_params_or_retry(
        self,
        response: dict[str, Any],
        original_messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Validate action params against Pydantic model; retry with error details.

        issue #176 P0-B：重试响应本身无效（无名字动作——死亡链第 3 环）不再
        一次 fallback 死刑，与外梯对称地附澄清消息再试；澄清与参数重试共用
        ``_PARAM_VALIDATION_MAX_RETRIES`` 预算（一次 Invalid-params + 一次
        Invalid-action 封顶），总 LLM 调用次数有界（外梯 2 + 内梯 2）。
        """
        param_error = self._validate_action_params(response)
        if param_error is None:
            return response

        for attempt in range(_PARAM_VALIDATION_MAX_RETRIES):
            if self._is_valid_action(response):
                logger.warning(
                    "Invalid params for '%s': %s — retrying (%d/%d)",
                    name_of(response.get("action")), param_error,
                    attempt + 1, _PARAM_VALIDATION_MAX_RETRIES,
                )
                feedback: str = (
                    f"Your action parameters are invalid: {param_error}. "
                    "Please fix the parameters and respond again with a valid action."
                )
            else:
                # P0-B：重试退化成无名字动作——外梯同款澄清（共用 attempt
                # 预算），而非立即 fallback done
                logger.warning(
                    "LLM returned invalid action during param validation retry "
                    "— clarifying (%d/%d)",
                    attempt + 1, _PARAM_VALIDATION_MAX_RETRIES,
                )
                feedback = _INVALID_ACTION_CLARIFICATION
            retry_messages = list(original_messages) + [{
                "role": "user",
                "content": feedback,
            }]
            response = self._normalize_llm_response(await self.llm.get_action(
                system_prompt=self._system_prompt,
                messages=retry_messages,
                tool_schema=self._tool_schema,
            ))

            if self._is_valid_action(response):
                param_error = self._validate_action_params(response)
                if param_error is None:
                    return response

        if not self._is_valid_action(response):
            # 预算耗尽时最后响应仍是无效动作——此前在循环内一次死刑的位置，
            # 现在只在澄清重试也失败后才 fallback
            logger.warning(
                "LLM still returned invalid action after %d param-validation "
                "retries — fallback done",
                _PARAM_VALIDATION_MAX_RETRIES,
            )
            return _fallback_done_output()

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
        action = response.get("action") or {}
        # review6 #3：诚实失败 done（畸形标量的归一化去向）跳过校验立即执行——
        # variant B 的 StructuredDoneParams 会拒绝其 text 参数、烧 2 次全上下文
        # 重试，违背「一次调用诚实终止」的设计。
        if is_honest_failure_action(action):
            return None
        # review5 #4 / review6 #9：name/params 一律经共享访问器——注入 LLM 绕过
        # client choke point 的畸形形态（str params / null name）不再崩校验层。
        name = str(name_of(action) or "")
        params = params_of(action)

        registered = self.tools.registry.actions.get(name)
        if registered is None:
            # review3 #3：未注册名也进澄清-重试梯子——畸形强转出来的名字（裸
            # 字符串 action / null / 数字）由此得到「Unknown action」反馈重发，
            # 而非落到执行失败计 failure。page 过滤只影响 schema 暴露、不影响
            # 此处按名查找（apply_page_filters 只写 page_patterns），未注册 =
            # 真未知名。
            return f"Unknown action '{name}'"

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
        # 非 dict（注入/旁路 LLM 的契约违反）判假进澄清梯——兑现
        # _normalize_llm_response docstring 的透传承诺，而非 AttributeError
        if not isinstance(response, dict):
            return False
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

        actions = actions_of(model_output)
        total = len(actions)
        results: list[ActionResult] = []

        for i, action in enumerate(actions):
            # 形态访问器统一自 action_shape（review4 #8：共享实现替换散落的
            # isinstance 拼法）。正常路径的 actions 已在 client choke point 归一化，
            # 此处访问器兜旁路形态（内存构造数据/测试直调）。
            action_name = name_of(action)
            action_params = params_of(action)

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
                # P6 后续 I3：目标元素几何（归一化百分比，供 BrowserView 高亮框）
                _vp = await self._cached_viewport()
                _eidx, _ebbox, _expath = _action_element_geometry(
                    action_params, browser_state.dom_state, _vp)
                self._obs_bus.emit(ToolCallEvent(
                    step=self.state.n_steps, session_id=self._obs_session_id,
                    model_call_id=getattr(self, "_current_model_call_id", ""),
                    tool_call_id=tool_call_id,
                    # review6 #1：pydantic str 字段不接受 None——str() 包裹防旁路
                    # 形态在事件构造处 ValidationError 杀死整步
                    action_name=str(action_name or ""),
                    params=action_params,
                    action_index=i, total_actions=total,
                    element_index=_eidx, element_bbox=_ebbox, element_xpath=_expath,
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

            # P0 探索 actionability（默认开）：白名单动作 click/input_text/select_dropdown
            # 点击/输入前等元素 actionable（visible+enabled+receives-events，可选 stable/L3）。
            # 降级原则：拿不到 node / 超时 / index 漂移 → 照常执行，不引入新失败。
            # 与重放端共用 actionability 模块；探索侧用 live index 重定位（无录制 hist_elem）。
            if getattr(self, "exploration_actionability_check", False) and action_name in ACTIONABILITY_ACTIONS:
                idx = action_params.get("index")
                sm = browser_state.dom_state.selector_map if browser_state.dom_state else None
                node = sm.get(idx) if (sm and isinstance(idx, int)) else None
                if node is not None and not is_file_input(node):
                    browser_state, _ = await wait_for_actionability(
                        self.browser,
                        browser_state,
                        idx,
                        timeout=self.exploration_actionability_timeout,
                        poll=self.exploration_actionability_poll,
                        receives_events=self.exploration_actionability_receives_events,
                        runtime_occlusion=self.exploration_actionability_runtime_occlusion,
                        stable=self.exploration_actionability_stable,
                        stable_interval=self.exploration_actionability_stable_interval,
                        stable_tolerance=self.exploration_actionability_stable_tolerance,
                    )

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

            # issue #186 现象①：连败跟踪——多动作步内的失败也计（task_374 形态：
            # [close_tab OK, screenshot 失败] 步，consecutive_failures 对这种不计
            # 且会被后续成功步重置）；该动作成功即清零；done 豁免。跳过的动作
            # （序列截断）不执行不记录。
            self.failure_streak.record(action_name, bool(result.error))
            # issue #186-c2 形态②：零结果降级跟踪——查询类动作经 metadata
            # query_total 旁路（零结果是 soft-miss，与工具失败两通道）。
            # review7 #7：record 前过同一 _flatten_params——LLM 嵌套包裹形态
            #（{"read_grid": {...}}）不归一则顶层取不到 selector/query/filters，
            # 包裹发射下跟踪整体静默失效或跨查询串染（与 _validate_action_params
            # 同款，保证 schema/校验/执行/跟踪四方一致）。getattr 容缺：测试
            # 的鸭子类型桩（_RecordingTools 等）不带该方法，跳过归一直透原始
            # params（生产 Tools 恒有——真实路径不降级）。
            _flatten = getattr(self.tools, "_flatten_params", None)
            # review8 #3：展平前先查注册表（与 Tools.execute /
            # _validate_action_params 两个既有调用点同款守卫）——
            # _flatten_params 单 dict 值分支裸下标 registry.actions[name]，
            # 未知名（校验梯耗尽 "proceeding anyway" / 旁路 LLM）+ 单 dict 值
            # params（read_grid 拼写错名 + 正常 {"filters": {...}}）会 KeyError；
            # record 在 per-action try 之外，会把 execute 已优雅返回的
            # Unknown action error 降级成整步崩溃。未知名跳过展平直透原始
            # params（record 对未知名无 query_total 信号本就早退，零语义损失）。
            _known = action_name in getattr(
                getattr(self.tools, "registry", None), "actions", ())
            self.zero_result_streak.record(
                action_name,
                _flatten(action_params, action_name) if _flatten and _known else action_params,
                result,
            )

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
        actions = actions_of(model_output)
        for action in actions:
            action_name = name_of(action)
            action_params = params_of(action)
            if action_name not in _LOOP_EXEMPT_ACTIONS:
                self.loop_detector.record_action(action_name, action_params)

        # Phase 4 failure semantics (aligned to browser-use service.py:1221-1231):
        #   - single-action step with error → count + early return
        #   - multi-action step (any failure, partial or all) → do NOT count;
        #     loop_detector + replan nudges handle recovery instead
        #   - any non-counted step → reset counter if previously > 0
        # issue #194 review2：infra 清零须在单动作失败 early return **之前**——
        # 到达 _post_process 即证明 LLM 可达（model_output 非空、动作已执行），
        # 能力失败步同样解除基建嫌疑；否则被能力失败步隔开的两个限流窗口
        # 会叠加判死，违背「连续基建失败才累积」的语义。
        if self.state.infra_failures > 0:
            self.state.infra_failures = 0
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
        """Record history and log the step summary.

        Called from the ``_step`` finally block — runs regardless of success or
        exception（自身异常由 finally 的守卫兜住并计数 ``finalize_degraded_steps``）。
        **n_steps 递增不在此处**（PR #174 review3 #8 单一所有者）：由 ``_step``
        的 finally 在本方法之后统一执行——请勿在本方法尾部追加可能抛异常的
        语句后假设步数语义不变，递增的正确性已与语句顺序解耦。

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
            # 阶段二（issue #175）：当步截图落盘兑现 screenshot_path 占位字段
            # （agent-loop-optimize/05 的 P1 截图入历史缺口）。存**原图**（LLM
            # 收的是降采样版，历史要档案级保真）；视觉关时 screenshot 恒 None
            # （_prepare_context 门控），此处自然零写入。
            screenshot_path = (
                self._save_step_screenshot(browser_state.screenshot)
                if browser_state is not None and browser_state.screenshot
                else None
            )
            self.history.history.append(AgentHistory(
                step_number=self.state.n_steps,
                model_output=model_output,
                result=results,
                state_summary=state_summary,
                interacted_element=self._safe_project_interacted_elements(model_output, browser_state, results),
                metadata=self._build_step_metadata(time.time()),
                screenshot_path=screenshot_path,
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
        # n_steps 递增已上移至 _step 的 finally（review3 #8 单一所有者）——
        # 此处不再持有副本。

    def _save_step_screenshot(self, png: bytes) -> str | None:
        """当步截图落盘（阶段二 P1 截图入历史），返回写入路径。

        目录 ``<rerun_history_dir>/screenshots/``、文件名 ``step_NNN.png``
        （与 history JSON 同根，重放/审计一起找）。**失败只 warning 返回
        None**——截图是增强产物，任何 IO 问题不得挂 _finalize（PR #174 的
        finalize 降级红线：历史写入比截图存档重要）。
        """
        try:
            base = Path(self.rerun_history_dir) / "screenshots"
            base.mkdir(parents=True, exist_ok=True)
            target = base / f"step_{self.state.n_steps:03d}.png"
            target.write_bytes(png)
            return str(target)
        except OSError as e:
            logger.warning("step screenshot save failed: %s", e)
            return None

    def _project_interacted_elements(
        self,
        model_output: dict[str, Any],
        browser_state: BrowserStateSummary | None,
        results: list[ActionResult] | None = None,
    ) -> list[dict[str, Any] | None] | None:
        """把每个动作当年交互的元素投影成 ``DOMInteractedElement.to_dict()``。

        与 ``model_output`` 的 actions 列表【等长、按位对应】；无 index 的动作为 None。
        使用【该步开始时】的 ``browser_state``（LLM 看到、index 所指的那份 selector_map），
        这样重放时才能正确还原「当年点的元素」。

        upload_file（#151）：若 ``results[i]`` 带 ``metadata["upload_clue"]``（agent 执行时采集，与
        手工录制 ``_store_upload_clue`` 同形），用它覆盖原始节点投影 → 历史带 ``_semantic_clue``，重放
        走 ``_match_file_upload_by_clue`` 稳健路径。``results`` 默认 None = 调用方零行为变化。
        """
        if not browser_state or not browser_state.dom_state:
            return None
        selector_map = browser_state.dom_state.selector_map
        if not selector_map:
            return None

        actions = actions_of(model_output)
        projected: list[dict[str, Any] | None] = []
        for i, action in enumerate(actions):
            # upload_file：agent 采集的语义线索优先于原始 DOM 节点投影（#151）
            if results is not None and i < len(results) and isinstance(action, dict) \
                    and action.get("name") == "upload_file":
                clue = (results[i].metadata or {}).get("upload_clue")
                if isinstance(clue, dict) and clue:
                    projected.append({"_semantic_clue": True, "kind": "file_upload", **clue})
                    continue
            # 逐条降级（review3 #7 / review4 #8）：params_of 统一实现「非 dict →
            # {}」，绕过 choke point 的畸形只丢本条投影，维护
            # actions/interacted_element 等长配对——catch-all 留给 DOM bug。
            params = params_of(action)
            index = params.get("index")
            if index is None:
                index = params.get("element_id")  # element_id 是 index 的别名
            node = selector_map.get(index) if index is not None else None
            if node is not None:
                projected.append(DOMInteractedElement.load_from_enhanced_dom_tree(node).to_dict())
            else:
                projected.append(None)
        return projected

    def _safe_project_interacted_elements(
        self,
        model_output: dict[str, Any],
        browser_state: BrowserStateSummary | None,
        results: list[ActionResult] | None = None,
    ) -> list[dict[str, Any] | None] | None:
        """``_finalize`` 兜底（issue #173）：投影只是历史元数据（重放用），失败降级
        None + warning——绝不让元数据 bug 杀死任务。778/782 教训：``_step`` 的
        ``finally`` 里抛异常会越过单步错误处理（except 已执行完），直接终结整任务。
        """
        try:
            return self._project_interacted_elements(model_output, browser_state, results)
        except Exception as e:
            logger.warning(
                "interacted-element projection failed — history metadata degraded to None: %s", e,
            )
            return None

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
            msg = "Agent interrupted mid-step"
            if str(error):
                msg = f"{msg} - {error}"
            logger.warning(msg)
            return

        # Branch 2.5: LLM 基建失败（限流/网络传输，issue #194）——先于浏览器
        # reconnect 分支：anthropic 类型精确（is_llm_infra_error 不认 builtin
        # ConnectionError，浏览器侧仍走 Branch 2）。与能力失败分罪：不进
        # consecutive_failures、不烧步数（_skip_step_increment）、真退避；
        # infra 步不递增 n_steps 后，防 livelock 的界由 run() 顶部的
        # infra_failures 预算检查接管。B 轮 task_550 的死法（20 秒 5 连发
        # 触发能力止损死刑）由此消除。
        if is_llm_infra_error(error):
            self.state.infra_failures += 1
            is_final = self.state.infra_failures >= self.max_infra_failures
            if is_final:
                # 预算耗尽：不再退避（run() 即将终止，白等无益），仍豁免步数
                logger.error(
                    "Infra failure budget (%d/%d) exhausted — run will stop",
                    self.state.infra_failures, self.max_infra_failures,
                )
            else:
                delay = min(
                    _INFRA_BACKOFF_CAP,
                    _INFRA_BACKOFF_BASE * 2 ** (self.state.infra_failures - 1),
                )
                logger.warning(
                    "Infra failure (%d/%d): %s — backing off %.0fs "
                    "(step budget not consumed)",
                    self.state.infra_failures, self.max_infra_failures,
                    type(error).__name__, delay,
                )
                await asyncio.sleep(delay)
            self._skip_step_increment = True
            # truthful 回显（替换旧文案"Rate limit reached. Waiting before
            # retry."的谎言——Branch 3 从不等待）：下一步成功的模型读到它，
            # 不会误以为自己做错过动作
            self.state.last_result = [ActionResult(error=(
                f"LLM API {type(error).__name__} "
                "(infrastructure, not an action result); "
                "backoff applied, no action executed this step"
            ))]
            return

        # issue #194 review4 #2：走到这里 = 非 infra 失败（浏览器连接/能力失败
        # 等）——同样解除基建嫌疑。Branch 2/3 结束的步不走 _post_process 的
        # 清零路径，不清零则被这类步隔开的限流窗口叠加、提前按基建死法终止
        # （B 轮正是 429+浏览器抖动并发的混合故障环境）。浏览器错误发生在 LLM
        # 调用前时 LLM 可达性未证明，此重置偏宽——混合交替形态由 max_steps
        # （Branch 2 步照常计步）/max_failures（Branch 3 步计连败）兜底有界。
        if self.state.infra_failures > 0:
            self.state.infra_failures = 0

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
        include_trace = logger.isEnabledFor(logging.DEBUG)
        error_msg = format_step_error(error, include_trace=include_trace)
        self.state.consecutive_failures += 1
        is_final = self.state.consecutive_failures >= self.max_failures
        log_level = logging.ERROR if is_final else logging.WARNING
        # Parse-class errors get an extra model-name line for diagnosis
        if any(marker in error_msg for marker in _LLM_PARSE_ERROR_MARKERS):
            logger.log(
                log_level, "Model %s failed to produce valid output", self.llm.model
            )
        logger.log(
            log_level,
            "Step %d failed (%d/%d): %s",
            self.state.n_steps,
            self.state.consecutive_failures,
            self.max_failures,
            error_msg,
        )
        self.state.last_result = [ActionResult(error=error_msg)]


# ── Helpers ────────────────────────────────────────────────────────────


# _cached_viewport 的未初始化哨兵（区分「未取」与「取到 None」）
_VIEWPORT_UNSET = object()


def _normalize_bbox(bounds: dict[str, Any] | None, viewport: tuple[int, int] | None) -> dict[str, float] | None:
	"""DOMRect.to_dict() + viewport(w,h) → 归一化 {left,top,width,height} ∈ [0,1]，或 None。

	无 bounds / 无 viewport / 零除 → None（前端降级为不画框）。
	"""
	if not bounds or not viewport:
		return None
	w, h = viewport
	if w <= 0 or h <= 0:
		return None
	x = float(bounds.get("x", 0) or 0)
	y = float(bounds.get("y", 0) or 0)
	bw = float(bounds.get("width", 0) or 0)
	bh = float(bounds.get("height", 0) or 0)
	return {
		"left": max(0.0, min(1.0, x / w)),
		"top": max(0.0, min(1.0, y / h)),
		"width": max(0.0, min(1.0, bw / w)),
		"height": max(0.0, min(1.0, bh / h)),
	}


def _action_element_geometry(
	action_params: dict[str, Any],
	dom_state: Any,
	viewport: tuple[int, int] | None,
) -> tuple[int | None, dict[str, float] | None, str | None]:
	"""ToolCallEvent 元素高亮的几何提取（P6 后续 I3）。

	返回 ``(element_index, element_bbox, element_xpath)``。无 index / 拿不到 node /
	DOM 投影失败 → 对应字段 None；全程降级不抛。bbox 已归一化（相对视口）。
	"""
	idx = action_params.get("index")
	if not isinstance(idx, int):
		return None, None, None
	sm = getattr(dom_state, "selector_map", None) if dom_state is not None else None
	node = sm.get(idx) if sm else None
	if node is None:
		return idx, None, None
	try:
		diel = DOMInteractedElement.load_from_enhanced_dom_tree(node)
	except Exception:
		return idx, None, None
	bbox: dict[str, float] | None = None
	if diel.bounds is not None:
		try:
			bbox = _normalize_bbox(diel.bounds.to_dict(), viewport)
		except Exception:
			bbox = None
	return idx, bbox, (diel.x_path or None)


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


_LLM_PARSE_ERROR_MARKERS = (
    "no parseable response",
    "Could not parse",
    "tool_use_failed",
    "invalid output structure",
)


def format_step_error(error: Exception, include_trace: bool = False) -> str:
    """Format a step-level error with guidance for the LLM.

    Mirrors browser-use ``AgentError.format_error`` (``service.py`` L1284 call
    site) but adapted to TreeWalker's Anthropic SDK + its own parse-failure
    wording (``client.py:232`` "LLM returned no parseable response"), NOT
    browser-use's ``'Expected format: AgentOutput'`` string (would never match
    here, since TreeWalker's LLM parser uses different wording).

    Trigger notes: most LLM parse errors are absorbed upstream by
    ``_FALLBACK_DONE_OUTPUT`` and ``RateLimitError``/``APIError`` by
    ``_try_switch_to_fallback`` (``client.py:58-70``); what reaches Branch 3 is
    mainly fallback-also-failed / ``_prepare_context`` exceptions / unexpected
    errors. This formatter serves those.
    """
    # Pydantic validation error (already imported at step.py:13).
    if isinstance(error, ValidationError):
        return (
            "Invalid model output format. Please follow the correct schema.\n"
            f"Details: {error}"
        )

    # Anthropic rate limit — lazy import (step.py doesn't import anthropic at
    # module level; only client.py does). browser-use uses openai's; we use anthropic's.
    try:
        from anthropic import RateLimitError as _AnthropicRateLimit
    except ImportError:
        _AnthropicRateLimit = ()  # type: ignore[assignment]
    if isinstance(error, _AnthropicRateLimit):
        return "Rate limit reached. Waiting before retry."

    error_str = str(error)
    if any(marker in error_str for marker in _LLM_PARSE_ERROR_MARKERS):
        main = error_str.split("\n", 1)[0]
        msg = (
            f"{main}\n\nThe previous response had an invalid output structure. "
            "Please stick to the required output format."
        )
        if include_trace:
            msg += f"\n\nFull stacktrace:\n{traceback.format_exc()}"
        return msg

    if include_trace:
        return f"{error_str}\nStacktrace:\n{traceback.format_exc()}"
    return error_str


def _is_connection_error(error: Exception) -> bool:
    """Detect connection/browser errors via isinstance and string patterns."""
    if isinstance(error, ConnectionError):
        return True
    msg = str(error).lower()
    return any(p in msg for p in _CONNECTION_ERROR_PATTERNS)


# issue #194 Branch 2.5：LLM 基建失败（限流/网络）的 step 层退避——L2 client
# 层预算耗尽后漏到这里的持续窗口才有此形态。5,10,20,40,60,60… 封顶 60；
# 默认 max_infra_failures=8 时累计 ~5min（评测由 runner 600s 任务超时做最外
# 层护栏；本层只须保证"不死于 20 秒级窗口 + 不烧有效步数"）。
_INFRA_BACKOFF_BASE = 5.0
_INFRA_BACKOFF_CAP = 60.0


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


# ── issue #186 现象②：done(success=True) 不确定标记门禁 ─────────────────────

# 词尾 ?：贴数字/标识符的悬而未决值（task_64 step16 的 "Emma Davis=1?" "=2?"
# "Davis?"）。token 不含空格 → 整句疑问（"Is this right?"）也会命中——自评里的
# 疑问句本身就是未消解状态，可接受；(?<![\w?]) 排除 "???" 连问与 token 中段。
# review2 #3：前瞻用排除式 (?![\w?])——尾前瞻 (?=\s|$) 会漏掉括号/引号/句读
# 包裹的形态（"(Emma Davis=1?)"、"3?."、'"1?",'），漏检即门禁静默失效。
_TOKEN_Q_RE = re.compile(r"(?<![\w?])[\w.\-=]{1,64}\?(?![\w?])")
_URL_RE = re.compile(r"https?://\S+")
_UNCERTAIN_KEYWORDS = (
    "unknown", "unverified", "unread", "gap", "missing", "pending",
    "partial", "uncertain", "unclear", "not sure", "not verified",
    "not confirmed", "needs verification", "to verify", "to check",
)
# review2 #4：否定语境——"nothing missing"/"no gap remains"/"no unread rows" 是
# agent 断言完整性的自信措辞，裸关键词会误报（白耗每 run 仅 2 次的门禁预算，
# 反馈文案还可能把本已完整的 run 诱导成诚实失败收题）。命中词前的短窗口内
# 出现否定词则不计。review3 #1：缩写否定单列一枝不带前置 \b——前置边界要求
# n 前是词边界，而 isn't/doesn't/wasn't 中 n 前是字母，带 \b 的 n't 永不匹配。
_NEGATION_RE = re.compile(r"\b(?:no|nothing|not|none|without)\b|n't\b")
_NEGATION_WINDOW = 25


def _scan_uncertainty_keywords(text: str) -> list[str]:
    """关键词-only 扫描（一个文本）——否定窗口语义同主扫描。

    done.text 等**对外交付物**专用（review4 #3）：agent 常在答案文本复述任务
    问句（"Q: … $50?"），词尾 ``?`` 对 text 无引语豁免，会把完整正确的答案
    误判为疑虑——故 text 只扫不确定关键词、不扫词尾 ``?``。
    """
    if not text:
        return []
    hits: list[str] = []
    low = _URL_RE.sub("", str(text)).lower()
    for kw in _UNCERTAIN_KEYWORDS:
        for m in re.finditer(rf"\b{re.escape(kw)}\b", low):
            neg = _NEGATION_RE.search(
                low, max(0, m.start() - _NEGATION_WINDOW), m.start(),
            )
            # review3 #2："not sure/not verified/not confirmed" 自带否定词，
            # 不受窗口抑制——前一从句的否定词（"No gap found, but not sure…"）
            # 会跨从句误杀疑虑本身，让门禁零命中静默失效
            if neg is None or kw.startswith("not "):
                if kw not in hits:
                    hits.append(kw)
                break
            # 首个出现被否定，仍继续找后续未否定的出现
    return hits


def scan_uncertainty_markers(*texts: str) -> list[str]:
    """扫 evaluation/memory 等自评文本中未消解的不确定标记（issue #186 现象②）。

    先剥 URL（query string 的 ``?`` 不是疑虑）；词尾 ``?`` 取 token 原文、关键词
    按整词（``\\b``）小写匹配且命中位置前的短窗口内无否定词。返回命中样本
    （去重、保序、封顶 3 个）供门禁的反馈消息引用——把 agent 自己写下的疑虑
    原样递回给它。
    """
    hits: list[str] = []
    seen: set[str] = set()

    def _add(sample: str) -> None:
        if len(hits) >= 3 or sample in seen:
            return
        seen.add(sample)
        hits.append(sample)

    for t in texts:
        if not t:
            continue
        stripped = _URL_RE.sub("", str(t))
        for m in _TOKEN_Q_RE.findall(stripped):
            _add(m)
        for kw in _scan_uncertainty_keywords(stripped):
            _add(kw)
    return hits


# 门禁每 run 触发封顶（防「每步重发带标记的 done」循环：打回→agent 换工具→
# 再 done 又带新标记→再打回……每次循环都强制了一轮验证推进，但必须有界）。
_DONE_GATE_MAX_PER_RUN = 2
# review2 #1：门禁重试的内层小超时——先于外层 llm_timeout 窗口到期，让预算
# 耗尽的取消以 TimeoutError（Exception 形态）落在门禁自己的兜底里放行原响应，
# 而非外层 wait_for 处变成失败步、丢掉已握有的合法 done。
_DONE_GATE_RETRY_TIMEOUT = 60.0
