"""Centralized configuration with environment variable loading."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

logging.getLogger("httpx").setLevel(logging.WARNING)


def _load_dotenv() -> None:
    """Load .env from project root (repo root or cwd)."""
    from pathlib import Path

    for candidate in (Path(__file__).resolve().parent.parent.parent, Path.cwd()):
        env_file = candidate / ".env"
        if env_file.is_file():
            from dotenv import load_dotenv

            load_dotenv(env_file, override=False)
            return


_load_dotenv()


@dataclass
class MessageCompactionSettings:
    enabled: bool = False
    llm: LLMSettings | None = None
    compact_every_n_steps: int = 10
    trigger_char_count: int = 40000
    keep_last_items: int = 4
    summary_max_chars: int | None = None


@dataclass
class TruncationSettings:
    """Configurable thresholds for all text truncation in the agent pipeline."""

    extract_page_max_chars: int = 8000       # page text fed to LLM for extraction
    extract_fallback_max_chars: int = 2000   # page text returned without LLM
    extract_chunk_max_chars: int = 8000      # per-chunk budget for structured pagination (Phase 2)
    extract_save_threshold: int = 10000      # result >= this → write to file (browser-use parity)
    extract_output_dir: str = "extract_output"  # dir for oversized results (env-config, not agent-controlled)
    extract_call_timeout: float = 0.0        # inner LLM-call timeout seconds (0 = disabled)
    read_file_max_chars: int = 5000          # file read tool result
    eval_result_max_chars: int = 2000        # JavaScript eval result
    display_max_chars: int = 4000            # ActionResult 渲染上限（__str__ → LLM 每步可见一次；500 时代 evaluate/find_elements 全量结果只见 ~10 行——P7 task1 R3a，docs/p7/02）
    dom_excerpt_max_chars: int = 2000        # DOM excerpt persisted per step for Judge review
    search_page_save_threshold: int = 10000  # search_page result >= this → write to file (mirrors extract)
    search_page_output_dir: str = "search_page_output"  # dir for oversized match lists (env-config)
    find_elements_save_threshold: int = 10000  # find_elements result >= this → write to file (mirrors search_page)
    find_elements_output_dir: str = "find_elements_output"  # dir for oversized element lists (env-config)
    eval_save_threshold: int = 10000      # evaluate result >= this → write to file (mirrors extract/search_page/find_elements)
    eval_output_dir: str = "evaluate_output"  # dir for oversized JS results (env-config)
    done_attachment_max_chars: int = 2000   # 二.D：单个附件内联上限（display_files_in_done_text 开启时）


@dataclass
class JudgeSettings:
    enabled: bool = True
    model: str = ""
    max_history_steps: int = 20
    trace_max_chars: int = 40000


@dataclass
class AgentSettings:
    max_steps: int = 100
    max_failures: int = 5
    max_actions_per_step: int = 5  # multi-action per step (browser-use parity)
    llm_timeout: int = 120  # seconds; wraps the entire retry sequence
    action_timeout: int = 30  # seconds; per-action execution timeout
    reconnect_timeout: int = 30  # seconds; wait for browser reconnection
    sensitive_data: dict[str, Any] | None = None
    track_downloads: bool = False
    message_compaction: MessageCompactionSettings | None = None
    enable_message_typing: bool = True  # P0：消息分类管理（state 替换/context 清理），关则回退原始 append
    enable_page_stats: bool = True  # P1a：state 消息渲染 [Page Stats]（links/交互/iframe/骨架屏），关则不渲染
    enable_sensitive_description: bool = True  # P1d：state 消息渲染 [Available Secrets]（按 URL 过滤告知可用占位符）
    enable_skill_injection: bool = False  # P1：按 host 读 domain-skills/<host>/ 注入 [Domain Skill]（默认关 = 零行为变更）
    max_history_items: int = 10  # P1c：<agent_history> 滑动窗口大小（compactor 启用时自动降到 5）
    enable_recent_events: bool = False  # P1b：state 消息渲染 [Recent Events]（首期仅 dialog；CDP 回调风险，默认关）
    truncation: TruncationSettings = field(default_factory=TruncationSettings)
    enable_planning: bool = False
    exploration_threshold: int = 5
    replan_failure_threshold: int = 3
    enable_observability: bool = False
    enable_decision_attribution: bool = False
    observability_log_dir: str = "logs"
    action_page_filters: dict[str, list[str]] | None = None
    allowed_upload_paths: list[str] | None = None
    allowed_write_paths: list[str] | None = None
    allowed_read_paths: list[str] | None = None
    # P1 三次修订：upload_file 上传后页面级验证（canvas/img/bg-image 预览探测）。默认开；
    # 关则回显无 ✅/ℹ️ 验证段（等价旧行为）。四次修订：wait_s 是 polling 总预算（默认 1.5s，
    # 早退于首个 delta；抖音 canvas 慢渲染需要 > 原 0.6s），interval_s 是 poll 间隔。
    upload_verify_enabled: bool = True
    upload_verify_wait_s: float = 1.5
    upload_verify_interval_s: float = 0.25
    display_files_in_done_text: bool = False  # 二.D：done 是否把附件内容内联进 extracted_content
    judge: JudgeSettings = field(default_factory=JudgeSettings)
    # extract 工具：专用 LLM（None=复用主 llm）+ 结构化抽取的 JSON Schema（None=free-text）
    extract_llm: LLMSettings | None = None
    extraction_schema: dict | None = None
    # 历史重放文件根目录：save_history/load_and_rerun 的相对路径都相对此根解析。
    # 相对路径=相对 CWD(项目根)；可用绝对路径覆盖。默认 "rerun-history"。
    rerun_history_dir: str = "rerun-history"
    # P1：domain-skills 根目录。enable_skill_injection 开启时按 host 读
    # <skills_dir>/<host>/{_sop,selectors,quirks}.md 注入 [Domain Skill]。
    # 相对路径=相对 CWD(项目根)；可用绝对路径覆盖。默认 "domain-skills"。
    # 与 TreeForge adapters/treewalker_adapter.py 输出对齐。开关关时此字段不被读取。
    skills_dir: str = "domain-skills"
    # 用户操作录制：upload_file 的约定目录。空→运行时解析为 <rerun_history_dir>/uploads。
    # 扩展只能拿到文件名（浏览器安全限制），录制产物存文件名，重放前用户须把文件放进此目录。
    record_upload_dir: str = ""
    # P1-3：每步对话 dump（input messages + model output 的人类可读文本快照），用于离线审计
    # LLM 决策。空=禁用。与 rerun_history_dir（机器重放）/ observability JsonlRecorder（事件流）
    # 定位不同，可并存。browser-use parity（service.py save_conversation_path）。
    save_conversation_path: str = ""
    # ── 重放时序（阶段 1）：默认值对齐 CLI/TUI 现状硬编码，避免改变现有行为 ──
    # 步间兜底延迟（秒）；对齐原 CLI/TUI 的 1（非 rerun_history 自身默认 2.0）
    rerun_delay_between_actions: float = 1.0
    # step_interval 封顶（秒）；对齐原 CLI/TUI 的 5（非 rerun_history 自身默认 45.0）
    rerun_max_step_interval: float = 5.0
    # 等元素数量（既有粗粒度等待）；对齐 rerun_history 默认 False
    rerun_wait_for_elements: bool = False
    # get_state 前等 readyState（缺口 1）；默认关 = 零行为变更
    rerun_wait_for_page_settle: bool = False
    # ── actionability 阶段一（阶段 2）：visible+enabled 检查，默认关 = 零行为变更 ──
    # 超时降级（不抛错），永不引入新失败。详见 docs/wait-and-timing/02-阶段2-...md
    rerun_actionability_check: bool = False
    # 单 action 等元素 actionable 的超时（秒）；元素已定位只等 visible，2s 够
    rerun_actionability_timeout: float = 2.0
    # actionability 轮询间隔（秒）；每次 poll 一次 get_state，0.3s 折中
    rerun_actionability_poll: float = 0.3
    # ── actionability 阶段二/三（阶段 4）：receives-events + stable ──
    # 总开关 rerun_actionability_check 关时整体不生效 → 默认值仍零行为变更。详见
    # docs/wait-and-timing/05-阶段4-actionability完善与step_interval语义清理.md
    # receives-events L1（paint_order 静态遮挡）+ L2（pointer-events:none），零开销静态判定
    rerun_actionability_receives_events: bool = True
    # receives-events L3（elementFromPoint 运行时遮挡），有 CDP 开销，默认关
    rerun_actionability_runtime_occlusion: bool = False
    # stable 检查（阶段三）：两次取 rect 比，动画/重排中元素。可选/优先级最低，默认关
    rerun_actionability_stable: bool = False
    # stable 两次取 rect 间隔（秒），对齐 Playwright ~100ms
    rerun_actionability_stable_interval: float = 0.1
    # stable rect 变化容差（像素）
    rerun_actionability_stable_tolerance: float = 1.0
    # ── P0 探索 actionability（默认开）：探索端点击/输入前等元素 actionable ──
    # 与重放端 rerun_actionability_* 同源（共享 actionability 模块），但探索侧默认开——
    # 探索可靠性即 P2 目的。降级原则：超时/拿不到 node → 照常执行，不引入新失败。
    # 详见 docs/p3/01-探索可靠性提升方案.md
    exploration_actionability_check: bool = True
    exploration_actionability_timeout: float = 1.5
    exploration_actionability_poll: float = 0.3
    exploration_actionability_receives_events: bool = True
    exploration_actionability_runtime_occlusion: bool = False
    exploration_actionability_stable: bool = False
    exploration_actionability_stable_interval: float = 0.1
    exploration_actionability_stable_tolerance: float = 1.0
    # B3-1（P7 02 批次三）：探索端页面级 settle——导航后等页面 JS 就绪（requirejs
    # 模块数连续 N 次 poll 不变）再放行动作。元素级 actionability 从 t≈0.1s 就绿灯
    # （部件初始化不改变元素几何/可见性），但 requirejs 页面的部件武装要 ~6-14s，
    # 窗口内输入的值会被迟到部件清掉（P12 输入侧失败，batch2_task1.log Step 6-8 实锤）。
    # 无 requirejs 的页面即刻就绪（零开销）；超时降级放行（不引入新失败）。
    exploration_page_settle: bool = True
    exploration_page_settle_timeout: float = 10.0
    exploration_page_settle_poll: float = 0.5
    exploration_page_settle_stable_polls: int = 4
    # ── 等待机制 阶段 3：networkidle（默认关）+ 重放端 upload 等待 ──
    # get_state 前等 networkidle（缺口 2）；默认关 = 零行为变更。
    # 开启条件：页面变化由 AJAX 驱动（readyState 常年 complete 的 SPA）。超时降级不抛错。
    rerun_wait_for_networkidle: bool = False
    # 重放端 upload_file 后等待（秒）；替代原录制端硬编码注入（缺口 6）。
    # 默认 = 原 _UPLOAD_WAIT_SECONDS（video 5 / image 3），旧录制（无注入）与新录制零差异。
    rerun_upload_wait_video: float = 5.0
    rerun_upload_wait_image: float = 3.0


@dataclass
class FallbackLLMSettings:
    model: str = ""
    api_key: str | None = None
    base_url: str = "https://open.bigmodel.cn/api/anthropic"
    # 与 LLMSettings.max_tokens 同理：fallback 也跑同一套 agent 决策（含 thinking）
    max_tokens: int = 16384


@dataclass
class LLMSettings:
    model: str = "glm-5.1"
    api_key: str | None = None
    base_url: str = "https://open.bigmodel.cn/api/anthropic"
    # 单次响应输出上限（thinking 计入）。4096 时难推理步的思考即可写满额度 →
    # 返回体只剩 thinking 块、无动作 → client.py 零重试 fallback done 猝死
    # （P7 task 1 两度复现，见 docs/p7/01-task1-trajectory-anatomy.md 附三）。
    # GLM 上下文窗口 ≥128K，16384 给 thinking+动作留足余量。
    max_tokens: int = 16384
    fallback: FallbackLLMSettings | None = None
    output_mode: str = "standard"  # "standard" | "flash" | "thinking"


@dataclass
class HighlightSettings:
	"""Visual highlight configuration for browser interaction feedback."""

	enabled: bool = True
	interaction_enabled: bool = True
	interaction_duration: float = 0.5
	interaction_color: dict | None = None
	click_feedback_enabled: bool = True
	click_feedback_duration: float = 0.3
	debug_mode: bool = False
	debug_highlight_color: str = '#4a90e2'

	def __post_init__(self):
		if self.interaction_color is None:
			self.interaction_color = {'r': 255, 'g': 165, 'b': 0, 'a': 0.8}


@dataclass
class BrowserSettings:
    ws_url: str | None = None
    cdp_host: str = "localhost"
    cdp_port: int = 9222
    cdp_first_timeout: float = 10.0
    cdp_retry_timeout: float = 2.0
    max_iframes: int = 100
    heavy_page_element_threshold: int = 10000
    circuit_breaker_threshold: int = 3
    circuit_breaker_recovery_s: float = 30.0
    highlight: HighlightSettings = field(default_factory=HighlightSettings)
    page_settle_timeout: float = 2.0
    page_settle_poll_interval: float = 0.1
    # 等待机制 阶段 3：networkidle 追踪调参（tracker always-on；wait 由 get_state 显式触发）。
    # 不暴露 env（仅 dataclass 默认），与 page_settle_* 一致。
    network_idle_timeout: float = 5.0  # 单步 networkidle 等待上限（秒）；AJAX 常 <2s，慢网/大文件 5s 兜底
    network_idle_stability_window: float = 0.5  # "无新请求 N 秒"判 idle（秒）；§6 推荐；Playwright 0.5
    network_idle_poll_interval: float = 0.1  # wait_until_idle 轮询间隔（秒）；对齐 page_settle_poll
    wait_between_actions: float = 0.0


@dataclass
class TUISettings:
	"""TUI interface settings."""

	theme: str = "textual-dark"
	log_level: str = "INFO"


@dataclass
class Settings:
    agent: AgentSettings = field(default_factory=AgentSettings)
    llm: LLMSettings = field(default_factory=LLMSettings)
    browser: BrowserSettings = field(default_factory=BrowserSettings)
    tui: TUISettings = field(default_factory=TUISettings)


def _load_sensitive_data() -> dict[str, Any] | None:
    """Load sensitive data mapping from SENSITIVE_DATA env var (JSON).

    P1d：兼容两种格式——
      旧（全局，无 URL 过滤）：``{"password": "real123"}``
      新（按 URL pattern 过滤）：``{"password": {"value": "real123", "urls": ["*login*"]}}``
    两种都原样返回，归一化在 Agent 侧完成（``_sensitive_data_raw``）。
    """
    raw = os.environ.get("SENSITIVE_DATA")
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and data:
            return data
        return None
    except (json.JSONDecodeError, TypeError):
        logger.warning("SENSITIVE_DATA env var is not valid JSON")
        return None


def _load_action_page_filters() -> dict[str, list[str]] | None:
    """Load action page filters from AGENT_ACTION_PAGE_FILTERS env var (JSON)."""
    raw = os.environ.get("AGENT_ACTION_PAGE_FILTERS")
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and data:
            return data
        return None
    except (json.JSONDecodeError, TypeError):
        logger.warning("AGENT_ACTION_PAGE_FILTERS env var is not valid JSON")
        return None


def _load_allowed_upload_paths() -> list[str] | None:
    """Load allowed upload paths from AGENT_ALLOWED_UPLOAD_PATHS env var (comma-separated)."""
    raw = os.environ.get("AGENT_ALLOWED_UPLOAD_PATHS")
    if not raw:
        return None
    paths = [p.strip() for p in raw.split(",") if p.strip()]
    return paths or None


def _load_allowed_write_paths() -> list[str] | None:
    """Load allowed write paths from AGENT_ALLOWED_WRITE_PATHS env var (comma-separated)."""
    raw = os.environ.get("AGENT_ALLOWED_WRITE_PATHS")
    if not raw:
        return None
    paths = [p.strip() for p in raw.split(",") if p.strip()]
    return paths or None


def _load_allowed_read_paths() -> list[str] | None:
    """Load allowed read paths from AGENT_ALLOWED_READ_PATHS env var (comma-separated)."""
    raw = os.environ.get("AGENT_ALLOWED_READ_PATHS")
    if not raw:
        return None
    paths = [p.strip() for p in raw.split(",") if p.strip()]
    return paths or None


def load_settings() -> Settings:
    """Load settings from environment variables, fetching ws_url if needed."""
    api_key = os.environ.get("ZHIPU_API_KEY")
    if not api_key:
        logger.warning("ZHIPU_API_KEY environment variable not set")

    # 消息压缩配置
    message_compaction: MessageCompactionSettings | None = None
    if os.environ.get("MESSAGE_COMPACTION_ENABLED", "").lower() == "true":
        compaction_llm: LLMSettings | None = None
        compaction_model = os.environ.get("MESSAGE_COMPACTION_MODEL")
        if compaction_model:
            compaction_llm = LLMSettings(
                model=compaction_model,
                api_key=os.environ.get("MESSAGE_COMPACTION_API_KEY", api_key),
                base_url=os.environ.get(
                    "MESSAGE_COMPACTION_BASE_URL",
                    "https://open.bigmodel.cn/api/anthropic",
                ),
            )
        message_compaction = MessageCompactionSettings(
            enabled=True,
            llm=compaction_llm,
            compact_every_n_steps=int(os.environ.get("MESSAGE_COMPACTION_EVERY_N_STEPS", "10")),
            trigger_char_count=int(os.environ.get("MESSAGE_COMPACTION_TRIGGER_CHARS", "40000")),
            keep_last_items=int(os.environ.get("MESSAGE_COMPACTION_KEEP_LAST", "4")),
            summary_max_chars=(
                int(v) if (v := os.environ.get("MESSAGE_COMPACTION_SUMMARY_MAX_CHARS")) else None
            ),
        )

    agent = AgentSettings(
        max_steps=int(os.environ.get("AGENT_MAX_STEPS", "100")),
        max_failures=int(os.environ.get("AGENT_MAX_FAILURES", "5")),
        llm_timeout=int(os.environ.get("AGENT_LLM_TIMEOUT", "120")),
        action_timeout=int(os.environ.get("AGENT_ACTION_TIMEOUT", "30")),
        reconnect_timeout=int(os.environ.get("RECONNECT_TIMEOUT", "30")),
        sensitive_data=_load_sensitive_data(),
        track_downloads=os.environ.get("TRACK_DOWNLOADS", "").lower() == "true",
        message_compaction=message_compaction,
        enable_message_typing=os.environ.get("AGENT_ENABLE_MESSAGE_TYPING", "true").lower() == "true",
        enable_page_stats=os.environ.get("AGENT_ENABLE_PAGE_STATS", "true").lower() == "true",
        enable_sensitive_description=os.environ.get("AGENT_ENABLE_SENSITIVE_DESCRIPTION", "true").lower() == "true",
        enable_skill_injection=os.environ.get("AGENT_ENABLE_SKILL_INJECTION", "").lower() == "true",
        max_history_items=int(os.environ.get("AGENT_MAX_HISTORY_ITEMS", "10")),
        enable_recent_events=os.environ.get("AGENT_ENABLE_RECENT_EVENTS", "false").lower() == "true",
        truncation=TruncationSettings(
            extract_page_max_chars=int(os.environ.get("AGENT_TRUNCATE_EXTRACT_PAGE", "8000")),
            extract_fallback_max_chars=int(os.environ.get("AGENT_TRUNCATE_EXTRACT_FALLBACK", "2000")),
            extract_chunk_max_chars=int(os.environ.get("AGENT_TRUNCATE_EXTRACT_CHUNK", "8000")),
            extract_save_threshold=int(os.environ.get("AGENT_EXTRACT_SAVE_THRESHOLD", "10000")),
            extract_output_dir=os.environ.get("AGENT_EXTRACT_OUTPUT_DIR", "extract_output"),
            extract_call_timeout=float(os.environ.get("AGENT_EXTRACT_CALL_TIMEOUT", "0")),
            read_file_max_chars=int(os.environ.get("AGENT_TRUNCATE_READ_FILE", "5000")),
            eval_result_max_chars=int(os.environ.get("AGENT_TRUNCATE_EVAL_RESULT", "2000")),
            display_max_chars=int(os.environ.get("AGENT_TRUNCATE_DISPLAY", "4000")),
            dom_excerpt_max_chars=int(os.environ.get("AGENT_TRUNCATE_DOM_EXCERPT", "2000")),
            search_page_save_threshold=int(os.environ.get("AGENT_SEARCH_PAGE_SAVE_THRESHOLD", "10000")),
            search_page_output_dir=os.environ.get("AGENT_SEARCH_PAGE_OUTPUT_DIR", "search_page_output"),
            eval_save_threshold=int(os.environ.get("AGENT_EVAL_SAVE_THRESHOLD", "10000")),
            eval_output_dir=os.environ.get("AGENT_EVAL_OUTPUT_DIR", "evaluate_output"),
            done_attachment_max_chars=int(os.environ.get("AGENT_DONE_ATTACHMENT_MAX_CHARS", "2000")),
        ),
        enable_planning=os.environ.get("AGENT_ENABLE_PLANNING", "true").lower() == "true",
        exploration_threshold=int(os.environ.get("AGENT_EXPLORATION_THRESHOLD", "5")),
        replan_failure_threshold=int(os.environ.get("AGENT_REPLAN_FAILURE_THRESHOLD", "3")),
        enable_observability=os.environ.get("AGENT_ENABLE_OBSERVABILITY", "").lower() == "true",
        enable_decision_attribution=os.environ.get("AGENT_ENABLE_DECISION_ATTRIBUTION", "").lower() == "true",
        observability_log_dir=os.environ.get("AGENT_OBSERVABILITY_LOG_DIR", "logs"),
        action_page_filters=_load_action_page_filters(),
        allowed_upload_paths=_load_allowed_upload_paths(),
        allowed_write_paths=_load_allowed_write_paths(),
        allowed_read_paths=_load_allowed_read_paths(),
        display_files_in_done_text=os.environ.get("AGENT_DISPLAY_FILES_IN_DONE_TEXT", "").lower() == "true",
        upload_verify_enabled=os.environ.get("AGENT_UPLOAD_VERIFY_ENABLED", "true").lower() == "true",
        upload_verify_wait_s=float(os.environ.get("AGENT_UPLOAD_VERIFY_WAIT_S", "1.5")),
        upload_verify_interval_s=float(os.environ.get("AGENT_UPLOAD_VERIFY_INTERVAL_S", "0.25")),
        rerun_history_dir=os.environ.get("AGENT_RERUN_HISTORY_DIR", "rerun-history"),
        skills_dir=os.environ.get("AGENT_SKILLS_DIR", "domain-skills"),
        record_upload_dir=os.environ.get("AGENT_RECORD_UPLOAD_DIR", ""),
        save_conversation_path=os.environ.get("AGENT_SAVE_CONVERSATION_PATH", ""),
        # 重放时序（阶段 1）
        rerun_delay_between_actions=float(os.environ.get("AGENT_RERUN_DELAY_BETWEEN_ACTIONS", "1.0")),
        rerun_max_step_interval=float(os.environ.get("AGENT_RERUN_MAX_STEP_INTERVAL", "5.0")),
        rerun_wait_for_elements=os.environ.get("AGENT_RERUN_WAIT_FOR_ELEMENTS", "").lower() == "true",
        rerun_wait_for_page_settle=os.environ.get("AGENT_RERUN_WAIT_FOR_PAGE_SETTLE", "").lower() == "true",
        # actionability 阶段一（阶段 2）
        rerun_actionability_check=os.environ.get("AGENT_RERUN_ACTIONABILITY_CHECK", "").lower() == "true",
        rerun_actionability_timeout=float(os.environ.get("AGENT_RERUN_ACTIONABILITY_TIMEOUT", "2.0")),
        rerun_actionability_poll=float(os.environ.get("AGENT_RERUN_ACTIONABILITY_POLL", "0.3")),
        # actionability 阶段二/三（阶段 4）：receives-events（L1+L2 默认开，零开销）+ stable（默认关）
        rerun_actionability_receives_events=os.environ.get("AGENT_RERUN_ACTIONABILITY_RECEIVES_EVENTS", "true").lower() == "true",
        rerun_actionability_runtime_occlusion=os.environ.get("AGENT_RERUN_ACTIONABILITY_RUNTIME_OCCLUSION", "").lower() == "true",
        rerun_actionability_stable=os.environ.get("AGENT_RERUN_ACTIONABILITY_STABLE", "").lower() == "true",
        rerun_actionability_stable_interval=float(os.environ.get("AGENT_RERUN_ACTIONABILITY_STABLE_INTERVAL", "0.1")),
        rerun_actionability_stable_tolerance=float(os.environ.get("AGENT_RERUN_ACTIONABILITY_STABLE_TOLERANCE", "1.0")),
        # P0 探索 actionability（默认开）：探索端点击/输入前等元素 actionable
        exploration_actionability_check=os.environ.get("AGENT_EXPLORATION_ACTIONABILITY_CHECK", "true").lower() == "true",
        exploration_actionability_timeout=float(os.environ.get("AGENT_EXPLORATION_ACTIONABILITY_TIMEOUT", "1.5")),
        exploration_actionability_poll=float(os.environ.get("AGENT_EXPLORATION_ACTIONABILITY_POLL", "0.3")),
        exploration_actionability_receives_events=os.environ.get("AGENT_EXPLORATION_ACTIONABILITY_RECEIVES_EVENTS", "true").lower() == "true",
        exploration_actionability_runtime_occlusion=os.environ.get("AGENT_EXPLORATION_ACTIONABILITY_RUNTIME_OCCLUSION", "").lower() == "true",
        exploration_actionability_stable=os.environ.get("AGENT_EXPLORATION_ACTIONABILITY_STABLE", "").lower() == "true",
        exploration_actionability_stable_interval=float(os.environ.get("AGENT_EXPLORATION_ACTIONABILITY_STABLE_INTERVAL", "0.1")),
        exploration_actionability_stable_tolerance=float(os.environ.get("AGENT_EXPLORATION_ACTIONABILITY_STABLE_TOLERANCE", "1.0")),
        # B3-1：探索端页面级 settle（默认开）——导航后等 requirejs 模块数稳定
        exploration_page_settle=os.environ.get("AGENT_PAGE_SETTLE", "true").lower() == "true",
        exploration_page_settle_timeout=float(os.environ.get("AGENT_PAGE_SETTLE_TIMEOUT", "10.0")),
        exploration_page_settle_poll=float(os.environ.get("AGENT_PAGE_SETTLE_POLL", "0.5")),
        exploration_page_settle_stable_polls=int(os.environ.get("AGENT_PAGE_SETTLE_STABLE_POLLS", "4")),
        # 等待机制 阶段 3：networkidle 开关 + 重放端 upload 等待（默认值对齐现状 = 零行为变更）
        rerun_wait_for_networkidle=os.environ.get("AGENT_RERUN_WAIT_FOR_NETWORKIDLE", "").lower() == "true",
        rerun_upload_wait_video=float(os.environ.get("AGENT_RERUN_UPLOAD_WAIT_VIDEO", "5.0")),
        rerun_upload_wait_image=float(os.environ.get("AGENT_RERUN_UPLOAD_WAIT_IMAGE", "3.0")),
        judge=JudgeSettings(
            enabled=os.environ.get("AGENT_JUDGE_ENABLED", "1") == "1",
            model=os.environ.get("AGENT_JUDGE_MODEL", ""),
            max_history_steps=int(os.environ.get("AGENT_JUDGE_MAX_HISTORY_STEPS", "20")),
            trace_max_chars=int(os.environ.get("AGENT_JUDGE_TRACE_MAX_CHARS", "40000")),
        ),
    )

    # extract 专用 LLM（镜像 FALLBACK_LLM_* 模式；None=复用主 llm，保阶段一行为）
    extract_model = os.environ.get("AGENT_EXTRACT_MODEL", "")
    if extract_model:
        agent.extract_llm = LLMSettings(
            model=extract_model,
            api_key=os.environ.get("AGENT_EXTRACT_API_KEY") or api_key,
            base_url=os.environ.get(
                "AGENT_EXTRACT_BASE_URL",
                "https://open.bigmodel.cn/api/anthropic",
            ),
            max_tokens=int(os.environ.get("AGENT_EXTRACT_MAX_TOKENS", "4096")),
        )

    # Fallback LLM configuration
    fallback_settings: FallbackLLMSettings | None = None
    fallback_model = os.environ.get("FALLBACK_LLM_MODEL", "")
    if fallback_model:
        fallback_settings = FallbackLLMSettings(
            model=fallback_model,
            api_key=os.environ.get("FALLBACK_LLM_API_KEY") or api_key,
            base_url=os.environ.get(
                "FALLBACK_LLM_BASE_URL",
                "https://open.bigmodel.cn/api/anthropic",
            ),
            max_tokens=int(os.environ.get("FALLBACK_LLM_MAX_TOKENS", "16384")),
        )
    output_mode = os.environ.get("LLM_OUTPUT_MODE", "standard")
    if output_mode not in ("standard", "flash", "thinking"):
        logger.warning("Invalid LLM_OUTPUT_MODE '%s', falling back to 'standard'", output_mode)
        output_mode = "standard"

    llm = LLMSettings(
        model=os.environ.get("LLM_MODEL", "glm-5.1"),
        api_key=api_key,
        base_url=os.environ.get("LLM_BASE_URL", "https://open.bigmodel.cn/api/anthropic"),
        max_tokens=int(os.environ.get("LLM_MAX_TOKENS", "16384")),
        fallback=fallback_settings,
        output_mode=output_mode,
    )

    cdp_host = os.environ.get("CDP_HOST", "localhost")
    cdp_port = int(os.environ.get("CDP_PORT", "9222"))

    ws_url = os.environ.get("CDP_WS_URL")
    if not ws_url:
        ws_url = _fetch_ws_url(cdp_host, cdp_port)

    browser = BrowserSettings(
        ws_url=ws_url,
        cdp_host=cdp_host,
        cdp_port=cdp_port,
        cdp_first_timeout=float(os.environ.get("DOM_CDP_FIRST_TIMEOUT", "10.0")),
        cdp_retry_timeout=float(os.environ.get("DOM_CDP_RETRY_TIMEOUT", "2.0")),
        max_iframes=int(os.environ.get("DOM_MAX_IFRAMES", "100")),
        heavy_page_element_threshold=int(os.environ.get("DOM_HEAVY_PAGE_THRESHOLD", "10000")),
        circuit_breaker_threshold=int(os.environ.get("DOM_CIRCUIT_BREAKER_THRESHOLD", "3")),
        circuit_breaker_recovery_s=float(os.environ.get("DOM_CIRCUIT_BREAKER_RECOVERY_S", "30.0")),
        highlight=HighlightSettings(
            enabled=os.environ.get("BROWSER_HIGHLIGHT_ENABLED", "true").lower() != "false",
            interaction_enabled=os.environ.get("BROWSER_HIGHLIGHT_INTERACTION", "true").lower() != "false",
            interaction_duration=float(os.environ.get("BROWSER_HIGHLIGHT_INTERACTION_DURATION", "0.5")),
            click_feedback_enabled=os.environ.get("BROWSER_HIGHLIGHT_CLICK_FEEDBACK", "true").lower() != "false",
            click_feedback_duration=float(os.environ.get("BROWSER_HIGHLIGHT_CLICK_DURATION", "0.3")),
            debug_mode=os.environ.get("BROWSER_HIGHLIGHT_DEBUG_MODE", "").lower() == "true",
        ),
    )

    return Settings(agent=agent, llm=llm, browser=browser)


def _fetch_ws_url(host: str, port: int) -> str | None:
    """Fetch WebSocket URL from Chrome DevTools Protocol endpoint."""
    try:
        import httpx
        resp = httpx.get(f"http://{host}:{port}/json/version", timeout=5)
        resp.raise_for_status()
        return resp.json()["webSocketDebuggerUrl"]
    except Exception as e:
        logger.warning("Failed to fetch ws_url from %s:%d: %s", host, port, e)
        return None
