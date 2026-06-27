"""Centralized configuration with environment variable loading."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field

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
    display_max_chars: int = 500             # ActionResult display string
    dom_excerpt_max_chars: int = 2000        # DOM excerpt persisted per step for Judge review
    search_page_save_threshold: int = 10000  # search_page result >= this → write to file (mirrors extract)
    search_page_output_dir: str = "search_page_output"  # dir for oversized match lists (env-config)
    find_elements_save_threshold: int = 10000  # find_elements result >= this → write to file (mirrors search_page)
    find_elements_output_dir: str = "find_elements_output"  # dir for oversized element lists (env-config)


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
    sensitive_data: dict[str, str] | None = None
    track_downloads: bool = False
    message_compaction: MessageCompactionSettings | None = None
    truncation: TruncationSettings = field(default_factory=TruncationSettings)
    enable_planning: bool = False
    exploration_threshold: int = 5
    replan_failure_threshold: int = 3
    enable_observability: bool = False
    enable_decision_attribution: bool = False
    observability_log_dir: str = "logs"
    action_page_filters: dict[str, list[str]] | None = None
    allowed_upload_paths: list[str] | None = None
    judge: JudgeSettings = field(default_factory=JudgeSettings)
    # extract 工具：专用 LLM（None=复用主 llm）+ 结构化抽取的 JSON Schema（None=free-text）
    extract_llm: LLMSettings | None = None
    extraction_schema: dict | None = None


@dataclass
class FallbackLLMSettings:
    model: str = ""
    api_key: str | None = None
    base_url: str = "https://open.bigmodel.cn/api/anthropic"
    max_tokens: int = 4096


@dataclass
class LLMSettings:
    model: str = "glm-5.1"
    api_key: str | None = None
    base_url: str = "https://open.bigmodel.cn/api/anthropic"
    max_tokens: int = 4096
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


def _load_sensitive_data() -> dict[str, str] | None:
    """Load sensitive data mapping from SENSITIVE_DATA env var (JSON)."""
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
        truncation=TruncationSettings(
            extract_page_max_chars=int(os.environ.get("AGENT_TRUNCATE_EXTRACT_PAGE", "8000")),
            extract_fallback_max_chars=int(os.environ.get("AGENT_TRUNCATE_EXTRACT_FALLBACK", "2000")),
            extract_chunk_max_chars=int(os.environ.get("AGENT_TRUNCATE_EXTRACT_CHUNK", "8000")),
            extract_save_threshold=int(os.environ.get("AGENT_EXTRACT_SAVE_THRESHOLD", "10000")),
            extract_output_dir=os.environ.get("AGENT_EXTRACT_OUTPUT_DIR", "extract_output"),
            extract_call_timeout=float(os.environ.get("AGENT_EXTRACT_CALL_TIMEOUT", "0")),
            read_file_max_chars=int(os.environ.get("AGENT_TRUNCATE_READ_FILE", "5000")),
            eval_result_max_chars=int(os.environ.get("AGENT_TRUNCATE_EVAL_RESULT", "2000")),
            display_max_chars=int(os.environ.get("AGENT_TRUNCATE_DISPLAY", "500")),
            dom_excerpt_max_chars=int(os.environ.get("AGENT_TRUNCATE_DOM_EXCERPT", "2000")),
            search_page_save_threshold=int(os.environ.get("AGENT_SEARCH_PAGE_SAVE_THRESHOLD", "10000")),
            search_page_output_dir=os.environ.get("AGENT_SEARCH_PAGE_OUTPUT_DIR", "search_page_output"),
        ),
        enable_planning=os.environ.get("AGENT_ENABLE_PLANNING", "").lower() == "true",
        exploration_threshold=int(os.environ.get("AGENT_EXPLORATION_THRESHOLD", "5")),
        replan_failure_threshold=int(os.environ.get("AGENT_REPLAN_FAILURE_THRESHOLD", "3")),
        enable_observability=os.environ.get("AGENT_ENABLE_OBSERVABILITY", "").lower() == "true",
        enable_decision_attribution=os.environ.get("AGENT_ENABLE_DECISION_ATTRIBUTION", "").lower() == "true",
        observability_log_dir=os.environ.get("AGENT_OBSERVABILITY_LOG_DIR", "logs"),
        action_page_filters=_load_action_page_filters(),
        allowed_upload_paths=_load_allowed_upload_paths(),
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
            max_tokens=int(os.environ.get("FALLBACK_LLM_MAX_TOKENS", "4096")),
        )
    output_mode = os.environ.get("LLM_OUTPUT_MODE", "standard")
    if output_mode not in ("standard", "flash", "thinking"):
        logger.warning("Invalid LLM_OUTPUT_MODE '%s', falling back to 'standard'", output_mode)
        output_mode = "standard"

    llm = LLMSettings(
        model=os.environ.get("LLM_MODEL", "glm-5.1"),
        api_key=api_key,
        base_url=os.environ.get("LLM_BASE_URL", "https://open.bigmodel.cn/api/anthropic"),
        max_tokens=int(os.environ.get("LLM_MAX_TOKENS", "4096")),
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
