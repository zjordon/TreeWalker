"""历史重放（rerun_history）—— 把录制的动作序列在新浏览器里重跑。

设计详见 ``docs/rerun_history/``。本模块是 ``RerunMixin``，由 ``Agent`` 继承，提供：
- ``save_history`` / ``detect_variables`` / ``load_and_rerun`` / ``rerun_history`` 公共 API；
- 五级元素匹配 ``_update_action_indices``；
- 单步重放 ``_execute_history_step``（``extract`` 直接 re-execute，其余动作重定位后执行）；
- 5 种跳过/重试策略、步间延迟、SPA 等待；
- 三层兜底 AI 摘要（无截图）。

关键复用：``DOMInteractedElement`` / ``compute_stable_hash`` / ``element_hash`` / ``Tools.execute``。
"""

from __future__ import annotations

import asyncio
import copy
import logging
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tree_walker.agent.views import (
    AgentHistory,
    AgentHistoryList,
    ActionResult,
    DetectedVariable,
    RerunSummaryAction,
)
from tree_walker.prompts.rerun_summary import get_rerun_summary_prompt
from tree_walker.recorder.locator import locate_by_ref, normalize_xpath

if TYPE_CHECKING:
    from tree_walker.browser.session import BrowserSession
    from tree_walker.llm.client import LLMClient
    from tree_walker.tools.actions import Tools

logger = logging.getLogger(__name__)


class MatchLevel(Enum):
    """元素匹配严格级别（命中即停，逐级降级）。"""

    EXACT = 1      # element_hash 完全相等（TreeWalker 确定性 sha256，跨会话稳定）
    STABLE = 2     # stable_hash 相等（过滤动态 CSS 类，重放首选）
    XPATH = 3      # x_path 字符串相等
    AX_NAME = 4    # node_name + ax_name 相等（动态菜单/SPA 救星）
    ATTRIBUTE = 5  # node_name + name/id/aria-label 相等（兜底）
    CLASS = 6      # node_name + class token 超集（最末兜底：无 name/id/aria-label 的 SPA 按钮）


# ── actionability 阶段一（阶段 2）：visible + enabled 检查的纯函数辅助 ──
# 详见 docs/wait-and-timing/02-阶段2-等目标元素与actionability阶段一.md
_ACTIONABILITY_ACTIONS: frozenset[str] = frozenset({"click", "input_text", "select_dropdown"})


def _is_file_input(node: Any) -> bool:
    """防御短路：INPUT[type=file] 永远跳过 actionability（仿 serializer.py:264-271）。

    upload_file 的 file input 因 opacity:0/display:none/1×1 → node.is_visible=False（serializer
    仅对 LLM 简化树强制可见，不改 node.is_visible）。白名单已排除 upload_file，本函数是第二层
    防御——避免未来白名单逻辑变更或 LLM 误把 file input 当 click 目标时被 visible 检查误杀。
    """
    return (
        (getattr(node, "node_name", "") or "").upper() == "INPUT"
        and (getattr(node, "attributes", None) or {}).get("type", "").lower() == "file"
    )


def _is_actionable(node: Any, *, check_receives_events: bool = False) -> bool:
    """visible + enabled（+ receives-events）判定。None 字段保守放过（不引入新失败）。

    - visible：``is_visible`` 为 ``bool|None``。``False``=明确不可见→阻断；``None``=未知→放过。
    - receives-events（阶段二，``check_receives_events=True`` 时启用）：L2 ``pointer-events:none`` →
      不接收指针事件→阻断；L1 paint_order 判定被完全覆盖（``ignored_by_paint_order``）→阻断。
      两者都读快照静态数据，零额外 CDP 开销；``snapshot_node`` 缺失时保守放过。
    - enabled：AX ``ax_node.properties`` 里 ``name=='disabled'`` 且真值，HTML ``attributes`` 含
      ``disabled``，或 ``aria-disabled="true"``（阶段4 遗留收编）。

    ``check_receives_events`` 默认 ``False`` → 行为与阶段一一字不差（零回归基线）。
    运行时遮挡（L3，``elementFromPoint``）是 async，不在此同步函数——见 ``_wait_for_actionability``。
    """
    if getattr(node, "is_visible", None) is False:
        return False
    # ── 阶段二 receives-events（L2 pointer-events + L1 paint_order 静态遮挡）──
    if check_receives_events:
        snap = getattr(node, "snapshot_node", None)
        if snap is not None:
            pe = (getattr(snap, "computed_styles", None) or {}).get("pointer-events", "")
            if str(pe).lower() == "none":
                return False
        if getattr(node, "ignored_by_paint_order", False):
            return False
    # ── enabled（阶段一 + 阶段4 遗留 aria-disabled）──
    ax = getattr(node, "ax_node", None)
    if ax is not None:
        for prop in (ax.properties or []):
            if getattr(prop, "name", "") == "disabled" and prop.value:
                return False
    attrs = getattr(node, "attributes", None) or {}
    if "disabled" in attrs:
        return False
    if str(attrs.get("aria-disabled", "")).lower() == "true":
        return False
    return True


def _substitute_in_dict(data: dict[str, Any], replacements: dict[str, str]) -> int:
    """递归替换——【仅精确整串匹配】，不做子串替换。返回替换次数。"""
    count = 0
    for key, value in data.items():
        if isinstance(value, str):
            if value in replacements:
                data[key] = replacements[value]
                count += 1
        elif isinstance(value, dict):
            count += _substitute_in_dict(value, replacements)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, str) and item in replacements:
                    value[i] = replacements[item]
                    count += 1
    return count


def _truncate(text: str, limit: int = 80) -> str:
    """截断长文本用于日志展示（剥首尾空白、折叠换行）。"""
    text = (text or "").strip().replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _format_action_params(params: dict[str, Any], limit: int = 120) -> str:
    """把动作参数渲染成 ``key: value, key: value`` 形式（日志展示用）。"""
    return _truncate(", ".join(f"{k}: {v}" for k, v in params.items()), limit)


def _first_interacted_element(item: AgentHistory | None) -> dict[str, Any] | None:
    """安全取某步首个被交互元素的投影 dict；无则 None。"""
    if not item or not item.interacted_element:
        return None
    elem = item.interacted_element[0]
    return elem or None


def _bounds_center(bounds: Any) -> tuple[float, float] | None:
    """``DOMRect`` 对象或 dict 形式的 bounds → 中心坐标 (cx, cy)；无则 None。

    dict 兼容 ``DOMInteractedElement.to_dict()`` 的 ``{x,y,width,height}`` 与历史里
    可能出现的 ``{x,y,w,h}`` 两种写法。
    """
    if bounds is None:
        return None
    if isinstance(bounds, dict):
        x = float(bounds.get("x", 0) or 0)
        y = float(bounds.get("y", 0) or 0)
        w = float(bounds.get("width", bounds.get("w", 0)) or 0)
        h = float(bounds.get("height", bounds.get("h", 0)) or 0)
    else:
        x, y, w, h = float(bounds.x), float(bounds.y), float(bounds.width), float(bounds.height)
    if w <= 0 and h <= 0:
        return None
    return (x + w / 2.0, y + h / 2.0)


def _elem_bounds(elem: Any) -> Any:
    """EnhancedDOMTreeNode → snapshot bounds（与 ``DOMInteractedElement`` 投影一致）。"""
    sn = getattr(elem, "snapshot_node", None)
    return getattr(sn, "bounds", None) if sn else None


def _nearest_idx(hist: dict[str, Any], candidates: list[tuple[int, Any]]) -> int:
    """同一匹配级有多个候选时选一个。

    优先级：
    1. **xpath 精确匹配**——区分「指纹碰撞但 xpath 不同」的同款元素（典型：抖音封面编辑器
       横版/竖版上传区 ``semi-upload-drag-area-icon`` 同 ``element_hash``，但 xpath div[2] vs div[3]；
       横版/竖版步骤 tab 同 ``step-dXVbPX``）。仅靠 bounds 就近无法区分（切 tab 后两者同屏位），
       xpath 是录制时唯一定位线索。
    2. **录制 bounds 中心就近**——消除哈希碰撞时「按迭代顺序取到错误元素」；无 bounds 退回第一个。
    """
    if len(candidates) == 1:
        return candidates[0][0]
    # 优先：录制 xpath 唯一命中某个候选——区分「指纹碰撞但 xpath 不同」的同款元素
    # （典型：抖音封面编辑器横/竖版上传区 semi-upload-drag-area-icon 同 element_hash，
    # xpath div[2] vs div[3]；横/竖版步骤 tab 同 step-dXVbPX）。仅当**恰好一个**候选 xpath
    # 匹配时才采用——全部匹配（真·无区分碰撞）或都不匹配（xpath 漂移）则退回 bounds 就近。
    h_xpath = hist.get("x_path")
    if h_xpath:
        xpath_hits = [idx for idx, elem in candidates if getattr(elem, "xpath", None) == h_xpath]
        if len(xpath_hits) == 1:
            return xpath_hits[0]
    rc = _bounds_center(hist.get("bounds"))
    if rc is None:
        return candidates[0][0]
    best_idx, best_d = candidates[0][0], float("inf")
    for idx, elem in candidates:
        ec = _bounds_center(_elem_bounds(elem))
        if ec is None:
            continue
        d = (ec[0] - rc[0]) ** 2 + (ec[1] - rc[1]) ** 2
        if d < best_d:
            best_d, best_idx = d, idx
    return best_idx


_UPLOAD_VIDEO_EXTS = frozenset({"mp4", "mov", "avi", "mkv", "webm", "flv", "wmv", "m4v", "ts", "3gp", "mpeg", "mpg"})
_UPLOAD_IMAGE_EXTS = frozenset({"png", "jpg", "jpeg", "gif", "bmp", "webp", "tif", "tiff", "svg", "heic"})


def _upload_file_kind(path: str) -> str | None:
    """文件扩展名 → ``"video"`` / ``"image"`` / ``None``。

    重放端 upload_file 后等待按类型给秒数用（阶段3 缺口6）；复用既存扩展名集，零重复。
    """
    ext = Path(path or "").suffix.lower().lstrip(".")
    if ext in _UPLOAD_VIDEO_EXTS:
        return "video"
    if ext in _UPLOAD_IMAGE_EXTS:
        return "image"
    return None


def resolve_rerun_path(rerun_history_dir: str, file_path: str | Path) -> Path:
    """相对路径 → 相对根目录的路径；拒绝绝对路径与 ``..`` 越界。

    公共函数：供 ``Agent.save_history`` / ``load_and_rerun``、示例、CLI 复用，保证重放文件
    统一落在 ``rerun_history_dir`` 之下、且调用方只能给相对路径。
    """
    p = Path(file_path)
    if p.is_absolute():
        raise ValueError(
            f"重放文件路径必须是相对路径（相对 rerun_history_dir={rerun_history_dir!r}），"
            f"收到: {file_path}"
        )
    root = Path(rerun_history_dir)
    resolved = root / p
    try:  # 拒绝 `..` 越界：解析后必须仍在 root 之内
        resolved.resolve().relative_to(root.resolve())
    except ValueError:
        raise ValueError(
            f"重放文件路径越界（跳出根目录 {str(root)!r}）: {file_path}"
        )
    return resolved


class RerunMixin:
    """历史重放能力，混入 ``Agent``。

    依赖 Agent 提供的属性：``browser`` / ``tools`` / ``llm`` / ``task`` / ``history``
    / ``state`` / ``action_timeout`` / ``_track_downloads`` / ``_sensitive_map``
    / ``_obs_bus`` / ``_obs_session_id`` / ``_extract_url``。
    """

    # 声明给类型检查器（运行时由 Agent 填充）
    browser: BrowserSession
    tools: Tools
    llm: LLMClient
    task: str
    history: AgentHistoryList
    action_timeout: int
    _track_downloads: bool
    _sensitive_map: dict[str, str] | None
    _obs_bus: Any
    _obs_session_id: str
    rerun_history_dir: str
    # 重放时序（阶段 1）：由 Agent.__init__ 从 AgentSettings 拷贝
    rerun_delay_between_actions: float
    rerun_max_step_interval: float
    rerun_wait_for_elements: bool
    rerun_wait_for_page_settle: bool
    # actionability 阶段一（阶段 2）：由 Agent.__init__ 从 AgentSettings 拷贝
    rerun_actionability_check: bool
    rerun_actionability_timeout: float
    rerun_actionability_poll: float
    # actionability 阶段二/三（阶段 4）：receives-events + stable（由 Agent.__init__ 从 AgentSettings 拷贝）
    rerun_actionability_receives_events: bool
    rerun_actionability_runtime_occlusion: bool
    rerun_actionability_stable: bool
    rerun_actionability_stable_interval: float
    rerun_actionability_stable_tolerance: float
    # 等待机制 阶段 3：networkidle 开关 + 重放端 upload 等待（由 Agent.__init__ 从 AgentSettings 拷贝）
    rerun_wait_for_networkidle: bool
    rerun_upload_wait_video: float
    rerun_upload_wait_image: float

    # ── 公共 API ───────────────────────────────────────────────────────

    def rerun_path(self, file_path: str | Path) -> Path:
        """把相对路径解析到 ``self.rerun_history_dir`` 之下（含绝对路径/``..`` 越界校验）。"""
        return resolve_rerun_path(self.rerun_history_dir, file_path)

    def save_history(self, file_path: str | Path | None = None) -> None:
        """把本次运行的历史落盘（含敏感数据脱敏 + 注册表版本号）。

        ``file_path`` 必须是相对路径（相对 ``rerun_history_dir``）；绝对路径或 ``..`` 越界会抛
        ``ValueError``。最终落到 ``rerun_history_dir / file_path``。
        """
        if not file_path:
            file_path = "AgentHistory.json"
        # _sensitive_map 是 {real_val: placeholder}；脱敏需要 {placeholder: real_val}
        sensitive = None
        if self._sensitive_map:
            sensitive = {p: r for r, p in self._sensitive_map.items()}
        self.history.save_to_file(
            self.rerun_path(file_path),
            sensitive_data=sensitive,
            action_registry_version=self.tools.registry.registry_version,
        )

    def detect_variables(self) -> dict[str, DetectedVariable]:
        """规则检测历史中可替换的变量（不调 LLM）。"""
        from tree_walker.agent.variable_detector import detect_variables_in_history

        return detect_variables_in_history(self.history)

    async def load_and_rerun(
        self,
        history_file: str | Path,
        variables: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> list[ActionResult]:
        """读历史文件 → 可选变量替换 → 重放。

        ``history_file`` 必须是相对路径（相对 ``rerun_history_dir``）；绝对路径或 ``..`` 越界
        会抛 ``ValueError``。
        """
        history = AgentHistoryList.load_from_file(self.rerun_path(history_file))
        current_ver = self.tools.registry.registry_version
        if history.action_registry_version and history.action_registry_version != current_ver:
            logger.warning(
                "历史注册表版本 %s 与当前 %s 不一致，重放可能遇到未知动作",
                history.action_registry_version, current_ver,
            )
        if variables:
            history = self._substitute_variables_in_history(history, variables)
        return await self.rerun_history(history, **kwargs)

    # ── 重放主流程 ─────────────────────────────────────────────────────

    async def rerun_history(
        self,
        history: AgentHistoryList,
        *,
        max_retries: int = 3,
        skip_failures: bool = False,
        delay_between_actions: float | None = None,
        max_step_interval: float | None = None,
        wait_for_elements: bool | None = None,
        wait_for_page_settle: bool | None = None,
        rerun_actionability_check: bool | None = None,
        rerun_actionability_receives_events: bool | None = None,
        rerun_actionability_runtime_occlusion: bool | None = None,
        rerun_actionability_stable: bool | None = None,
        wait_for_networkidle: bool | None = None,
        summary_llm: LLMClient | None = None,
        ai_step_llm: LLMClient | None = None,
    ) -> list[ActionResult]:
        """重放历史。返回 ``list[ActionResult]``，最后一项是 AI 摘要（``is_done=True``）。

        重放不调决策 LLM（``get_action`` 次数为 0）；``extract`` 动作走 ``tools._extract_llm``
        在当前页重算；结尾 1 次 LLM 调用生成摘要。

        时序参数（``delay_between_actions`` / ``max_step_interval`` / ``wait_for_elements`` /
        ``wait_for_page_settle``）默认 ``None`` → 回落到 ``AgentSettings`` 同名字段；显式传值
        （含测试的 ``=0``）仍优先。
        """
        if delay_between_actions is None:
            delay_between_actions = self.rerun_delay_between_actions
        if max_step_interval is None:
            max_step_interval = self.rerun_max_step_interval
        if wait_for_elements is None:
            wait_for_elements = self.rerun_wait_for_elements
        if wait_for_page_settle is None:
            wait_for_page_settle = self.rerun_wait_for_page_settle
        if rerun_actionability_check is None:
            rerun_actionability_check = self.rerun_actionability_check
        if rerun_actionability_receives_events is None:
            rerun_actionability_receives_events = self.rerun_actionability_receives_events
        if rerun_actionability_runtime_occlusion is None:
            rerun_actionability_runtime_occlusion = self.rerun_actionability_runtime_occlusion
        if rerun_actionability_stable is None:
            rerun_actionability_stable = self.rerun_actionability_stable
        if wait_for_networkidle is None:
            wait_for_networkidle = self.rerun_wait_for_networkidle
        await self.browser.start(track_downloads=self._track_downloads)
        await self._rerun_initial_navigation(history)
        self.state.n_steps = 0
        self.state.stopped = False

        results: list[ActionResult] = []
        previous_item: AgentHistory | None = None
        previous_succeeded = False

        total = len(history.history)
        logger.info("🔁 开始重放（共 %d 步）", total)
        try:
            for i, item in enumerate(history.history, 1):
                if self.state.stopped:
                    break
                step_delay = self._compute_step_delay(
                    item, delay_between_actions, max_step_interval
                )
                goal = _truncate(item.model_output.get("next_goal", ""))
                _md = item.metadata
                if _md and _md.user_pause_seconds is not None:
                    delay_src = f"user_pause={_md.user_pause_seconds:.1f}s"
                elif _md and _md.step_interval is not None:
                    delay_src = f"saved step_interval={_md.step_interval:.1f}s"
                else:
                    delay_src = f"default delay={delay_between_actions}s"
                skip_reason = self._skip_reason(
                    item, previous_item, previous_succeeded, skip_failures
                )
                if skip_reason:
                    logger.info("⏭️  跳过 Step %d (%d/%d) [%s]: %s",
                                item.step_number, i, total, delay_src, skip_reason)
                    previous_item, previous_succeeded = item, False
                    continue

                logger.info("🔁 回放 Step %d (%d/%d) [%s]: %s",
                            item.step_number, i, total, delay_src, goal)
                self.state.n_steps += 1
                step_results = await self._rerun_step_with_retries(
                    item, step_delay, max_retries, previous_item, ai_step_llm,
                    wait_for_elements, wait_for_page_settle, rerun_actionability_check,
                    rerun_actionability_receives_events, rerun_actionability_runtime_occlusion,
                    rerun_actionability_stable, wait_for_networkidle,
                )
                results.extend(step_results)
                previous_succeeded = bool(step_results) and not any(
                    r.error for r in step_results
                )
                previous_item = item
                # 注意：不在外层循环因 is_done 而 break——done 可能出现在录制中途
                #（例如 agent 先 done、再因错误重试一步）。重放须忠实回放每一步，
                # done 只终止「该步内的动作链」（_execute_history_step 的 guard），
                # 不应截断后续步骤。对齐 browser-use rerun_history。
        finally:
            summary = await self._generate_rerun_summary(self.task, results, summary_llm)
            results.append(summary)
            await self.browser.stop()

        return results

    async def _rerun_initial_navigation(self, history: AgentHistoryList) -> None:
        initial_url: str | None = None
        if history.history:
            initial_url = (history.history[0].state_summary or {}).get("url")
        if not initial_url:
            initial_url = self._extract_url(self.task)
        if initial_url:
            logger.info("🔗 重导航到 %s", initial_url)
            try:
                await self.browser.navigate(initial_url)
            except Exception as e:
                logger.warning("重放：重导航初始 URL 失败: %s", e)

    def _compute_step_delay(
        self, item: AgentHistory, delay_between_actions: float, max_step_interval: float
    ) -> float:
        # 阶段4 / 缺口7：优先级 user_pause_seconds（recorder 真实停顿，不封顶）>
        # step_interval（agent 自录上一步耗时含 LLM，封顶防空等）> delay_between_actions 兜底
        md = item.metadata
        if md:
            if md.user_pause_seconds is not None:
                return md.user_pause_seconds
            if md.step_interval is not None:
                return min(md.step_interval, max_step_interval)
        return delay_between_actions

    async def _rerun_step_with_retries(
        self,
        item: AgentHistory,
        delay: float,
        max_retries: int,
        previous_item: AgentHistory | None,
        ai_step_llm: LLMClient | None,
        wait_for_elements: bool,
        wait_for_page_settle: bool,
        rerun_actionability_check: bool,
        rerun_actionability_receives_events: bool,
        rerun_actionability_runtime_occlusion: bool,
        rerun_actionability_stable: bool,
        wait_for_networkidle: bool,
    ) -> list[ActionResult]:
        attempt = 0
        menu_reopens = 0
        cur_delay = delay
        while True:
            try:
                return await self._execute_history_step(
                    item, cur_delay, ai_step_llm,
                    wait_for_elements, wait_for_page_settle, rerun_actionability_check,
                    rerun_actionability_receives_events, rerun_actionability_runtime_occlusion,
                    rerun_actionability_stable, wait_for_networkidle,
                )
            except Exception as e:
                err_str = str(e)
                # 菜单重打开：找不到元素 + (上一步是下拉触发器 | 当前步要找的是下拉选项)
                # → 重打开后重试，不消耗 attempt。后者是框架无关的强信号——option
                #   匹配失败几乎必然意味着上一步打开的下拉已关闭。
                if (
                    menu_reopens < 3
                    and "Could not find matching element" in err_str
                    and previous_item is not None
                    and (
                        self._is_menu_opener_step(previous_item)
                        or self._is_option_element(item)
                    )
                ):
                    if await self._reexecute_menu_opener(
                        previous_item, ai_step_llm, wait_for_page_settle,
                        rerun_actionability_check, rerun_actionability_receives_events,
                        rerun_actionability_runtime_occlusion, rerun_actionability_stable,
                        wait_for_networkidle,
                    ):
                        menu_reopens += 1
                        cur_delay = 0.5
                        continue
                if attempt >= max_retries:
                    return [
                        ActionResult(
                            error=f"Step {item.step_number} failed after "
                            f"{max_retries} retries: {err_str}"
                        )
                    ]
                backoff = min(5 * (2 ** attempt), 30)  # 5 → 10 → 20, cap 30
                logger.warning(
                    "重放步骤 %d 失败，%ds 后重试(%d/%d): %s",
                    item.step_number, backoff, attempt + 1, max_retries, err_str,
                )
                await asyncio.sleep(backoff)
                attempt += 1
                cur_delay = 0.0

    def _skip_reason(
        self,
        item: AgentHistory,
        previous_item: AgentHistory | None,
        previous_succeeded: bool,
        skip_failures: bool,
    ) -> str | None:
        """返回跳过原因字符串；不跳过则返回 None（由调用方统一打日志）。"""
        actions = item.model_output.get("actions") or [item.model_output.get("action", {})]
        # 无动作
        if not actions or all(not (a and a.get("name")) for a in actions if isinstance(a, dict)):
            return "无动作"
        # 原始出错
        if skip_failures and item.result and all(r.error for r in item.result):
            return "原始运行即出错（skip_failures=True）"
        # 冗余重试
        if self._is_redundant_retry_step(item, previous_item, previous_succeeded):
            return "冗余重试（同元素同动作且上步已成功）"
        # 需 index 的 action 但无 index 且无 interacted_element（录制定位失败的噪声 click/input）
        # → 跳过：回放 _action_click 等无 index 必报错，是无意义噪声步（recorded.json 里
        # click {} interacted=null 这类）。upload_file 例外——file input 隐藏 1×1、xpath 常失配，
        # 录制侧 locate 易失败致 interacted 缺失，但回放时可从 selector_map 兜底找 file input
        # （见 _execute_history_step 的 upload_file 兜底），故不在此跳过。
        first = next((a for a in actions if isinstance(a, dict) and a.get("name")), None)
        if first and first.get("name") in ("click", "input_text", "select_dropdown"):
            fp = first.get("params") or {}
            if fp.get("index") is None and fp.get("element_id") is None:
                ie = item.interacted_element or []
                if not ie or ie[0] is None:
                    return "无 index 且无 interacted_element（录制定位失败的噪声步）"
        return None

    async def _execute_history_step(
        self,
        item: AgentHistory,
        delay: float,
        ai_step_llm: LLMClient | None,
        wait_for_elements: bool,
        wait_for_page_settle: bool,
        rerun_actionability_check: bool,
        rerun_actionability_receives_events: bool,
        rerun_actionability_runtime_occlusion: bool,
        rerun_actionability_stable: bool,
        wait_for_networkidle: bool,
    ) -> list[ActionResult]:
        """执行单步：取当前 selector_map → 逐动作重定位/重算并执行，带 guard。"""
        await asyncio.sleep(delay)
        state = await self.browser.get_state(
            include_screenshot=False, wait_settle=wait_for_page_settle,
            wait_networkidle=wait_for_networkidle,
        )

        if wait_for_elements:
            # 语义升级（阶段2）：从"数数量"改为"等目标元素匹配成功"。默认关=零行为变更。
            # _wait_for_target_elements 用 _match_element_index / locate_by_ref 轮询直到本步所有
            # 需定位 action 的目标定位成功，超时降级照原样执行。
            state = await self._wait_for_target_elements(state, item, timeout=15.0)

        selector_map = state.dom_state.selector_map if state and state.dom_state else {}
        actions = item.model_output.get("actions") or [item.model_output.get("action", {})]
        interacted = item.interacted_element or []

        results: list[ActionResult] = []
        for i, action in enumerate(actions):
            if not isinstance(action, dict):
                continue
            name = action.get("name", "done")
            hist_elem = None  # extract 分支不设；下方 actionability 块据此安全引用

            if name == "extract":
                # extract 自带 LLM 且读当前页（tools/_action_extract）→ 直接 re-execute 即在当前页重算
                params = dict(action.get("params", {}))
            else:
                hist_elem = interacted[i] if i < len(interacted) else None
                raw_params = action.get("params") if isinstance(action.get("params"), dict) else {}
                has_index = raw_params.get("index") is not None or raw_params.get("element_id") is not None
                if hist_elem and hist_elem.get("_semantic_clue"):
                    # 语义线索路径：录制时 locate 失败（get_state 抓变化后页），存了 e.target 的
                    # xpath/tag/attr/rect 线索。重放有主动时序优势（到这步页面稳定、元素完好），
                    # 复用 locate_by_ref 三道防线重新定位。详见 semantic-clue-replay.md。
                    matched = locate_by_ref(hist_elem, selector_map)
                    if matched is not None:
                        params = dict(raw_params)
                        params["index"] = matched[0]
                        logger.info("语义线索重定位 idx=%s（action=%s）", matched[0], name)
                    else:
                        raise ValueError(self._format_semantic_clue_failure(hist_elem, selector_map))
                elif hist_elem and has_index:
                    updated = self._update_action_indices(hist_elem, action, selector_map)
                    if updated is None:
                        if name == "upload_file":
                            fb = self._resolve_file_input_by_accept(
                                state, raw_params.get("path", ""),
                                raw_params.get("xpath", ""), raw_params.get("accept", ""),
                            )
                            if fb is not None:
                                params = dict(raw_params)
                                params["index"] = fb
                                logger.info("upload_file 匹配失败，按 accept 解析 file input index=%s", fb)
                            else:
                                raise ValueError(self._format_match_failure(hist_elem, i, selector_map))
                        else:
                            raise ValueError(self._format_match_failure(hist_elem, i, selector_map))
                    else:
                        name = updated.get("name", name)
                        params = dict(updated.get("params", {}))
                else:
                    params = dict(action.get("params", {}))
                    if (
                        name == "upload_file"
                        and params.get("index") is None
                        and params.get("element_id") is None
                    ):
                        fb = self._resolve_file_input_by_accept(
                            state, params.get("path", ""),
                            params.get("xpath", ""), params.get("accept", ""),
                        )
                        if fb is not None:
                            params["index"] = fb
                            logger.info("upload_file 无指纹，按 accept 解析 file input index=%s", fb)

            # actionability（阶段2 visible+enabled / 阶段4 receives-events + L3 运行时遮挡）：
            # 定位成功后、_exec_one 前查。默认关（rerun_actionability_check）= 零行为变更；
            # 超时降级照原样执行。白名单（click/input_text/select_dropdown）+ hist_elem（poll 期间
            # 重解析漂移 index）+ _is_file_input 防御短路（upload_file 的隐藏 file input）三层保护。
            if (
                rerun_actionability_check
                and name in _ACTIONABILITY_ACTIONS
                and hist_elem
            ):
                node_now = selector_map.get(params.get("index"))
                if (
                    node_now is not None
                    and not _is_file_input(node_now)
                    and not _is_actionable(
                        node_now,
                        check_receives_events=rerun_actionability_receives_events,
                    )
                ):
                    state, fresh_idx, _ = await self._wait_for_actionability(
                        state, hist_elem, params["index"],
                        timeout=self.rerun_actionability_timeout,
                        poll=self.rerun_actionability_poll,
                        receives_events=rerun_actionability_receives_events,
                        runtime_occlusion=rerun_actionability_runtime_occlusion,
                    )
                    if fresh_idx is not None and fresh_idx != params.get("index"):
                        logger.info(
                            "actionability 等待后 index 漂移 %s→%s（%s）",
                            params.get("index"), fresh_idx, name,
                        )
                        params["index"] = fresh_idx

            # actionability 阶段三（stable，可选/默认关/优先级最低）：两次取 rect 比，
            # 动画/重排中元素位置漂移时等稳定。定点单元素 get_element_coordinates，零性能影响。
            # 在 actionability 块之后——用 actionability 更新后的 params["index"]；独立超时降级。
            if (
                rerun_actionability_check
                and rerun_actionability_stable
                and name in _ACTIONABILITY_ACTIONS
                and hist_elem
            ):
                state, fresh_idx = await self._wait_for_stable(
                    state, hist_elem, params.get("index"),
                    timeout=self.rerun_actionability_timeout,
                    poll=self.rerun_actionability_poll,
                    interval=self.rerun_actionability_stable_interval,
                    tolerance=self.rerun_actionability_stable_tolerance,
                )
                if fresh_idx is not None and fresh_idx != params.get("index"):
                    logger.info(
                        "stable 等待后 index 漂移 %s→%s（%s）",
                        params.get("index"), fresh_idx, name,
                    )
                    params["index"] = fresh_idx

            logger.info("▶️  %s: %s", name, _format_action_params(params))
            result = await self._exec_one(name, params, state)
            results.append(result)

            # 阶段3 缺口6：upload_file 成功后可配置等待（替代原录制端硬编码注入）。
            # 仅成功时等（result.error 跳过）——比原"独立 wait 步无条件睡"更合理：失败时 step
            # 会重试/break，不等避免 retry×sleep 叠加浪费。语义覆盖"upload 与下一动作之间"。
            if name == "upload_file" and not result.error:
                kind = _upload_file_kind(params.get("path", ""))
                wait_s = (
                    self.rerun_upload_wait_video if kind == "video"
                    else self.rerun_upload_wait_image if kind == "image"
                    else 0.0
                )  # 未知类型不等（原 .get(kind, 3) 太武断）
                if wait_s > 0:
                    logger.info("upload 后等待 %.1fs（%s）", wait_s, kind or "unknown")
                    await asyncio.sleep(wait_s)

            # guard：done/error → 停
            last = results[-1]
            if last.is_done or last.error:
                break
            # terminates_sequence → 停
            registered = self.tools.registry.actions.get(name)
            if registered and registered.terminates_sequence:
                break
            # runtime drift → 停
            try:
                post_url = await self.browser.get_current_url()
            except Exception:
                post_url = state.url
            if post_url != state.url:
                logger.info("重放：页面漂移(%s → %s)，停止剩余动作", state.url, post_url)
                break
        return results

    async def _exec_one(
        self, action_name: str, params: dict[str, Any], state: Any
    ) -> ActionResult:
        """执行单个动作（带超时 + observability 事件）。"""
        tool_call_id = ""
        tool_start = time.time()
        if self._obs_bus:
            from tree_walker.observability.events import ToolCallEvent

            tool_call_id = uuid.uuid4().hex[:8]
            self._obs_bus.emit(
                ToolCallEvent(
                    step=self.state.n_steps,
                    session_id=self._obs_session_id,
                    model_call_id="",
                    tool_call_id=tool_call_id,
                    action_name=action_name,
                    params=params,
                    action_index=0,
                    total_actions=1,
                )
            )
        try:
            result = await asyncio.wait_for(
                self.tools.execute(action_name, params, self.browser, state),
                timeout=self.action_timeout,
            )
        except asyncio.TimeoutError:
            result = ActionResult(error=f"Action timed out after {self.action_timeout}s")
        except InterruptedError:
            raise
        except Exception as e:
            result = ActionResult(error=f"{type(e).__name__}: {e}")

        if self._obs_bus and tool_call_id:
            from tree_walker.observability.events import ToolResultEvent

            self._obs_bus.emit(
                ToolResultEvent(
                    step=self.state.n_steps,
                    session_id=self._obs_session_id,
                    tool_call_id=tool_call_id,
                    success=result.success,
                    error=result.error,
                    duration_seconds=time.time() - tool_start,
                    action_index=0,
                    total_actions=1,
                )
            )
        return result

    # ── 五级元素匹配 ───────────────────────────────────────────────────

    def _match_element_index(
        self, hist: dict[str, Any], selector_map: dict[int, Any]
    ) -> tuple[int, MatchLevel] | None:
        """在当前 selector_map 里找回同款元素。返回 (index, level) 或 None。

        每一级若有多个候选（哈希/属性碰撞——常见于多个相似下拉触发器），按「录制 bounds
        中心就近」tie-break，避免取到迭代顺序里靠前的错误元素。
        """
        h_name = (hist.get("node_name") or "").lower()

        def _ax(elem: Any) -> str | None:
            return (
                elem.ax_node.name
                if elem.ax_node and getattr(elem.ax_node, "name", None)
                else None
            )

        # Level 1: EXACT（TreeWalker 确定性 sha256，跨会话稳定）
        h_exact = hist.get("element_hash")
        if h_exact is not None:
            matches = [(idx, e) for idx, e in selector_map.items() if e.element_hash == h_exact]
            if matches:
                return _nearest_idx(hist, matches), MatchLevel.EXACT
        # Level 2: STABLE（重放首选）
        h_stable = hist.get("stable_hash")
        if h_stable is not None:
            matches = [(idx, e) for idx, e in selector_map.items() if e.compute_stable_hash() == h_stable]
            if matches:
                return _nearest_idx(hist, matches), MatchLevel.STABLE
        # Level 3: XPATH
        h_xpath = hist.get("x_path")
        if h_xpath:
            matches = [(idx, e) for idx, e in selector_map.items() if e.xpath == h_xpath]
            if matches:
                return _nearest_idx(hist, matches), MatchLevel.XPATH
        # Level 4: AX_NAME
        h_ax = hist.get("ax_name")
        if h_ax:
            matches = [
                (idx, e) for idx, e in selector_map.items()
                if e.node_name.lower() == h_name and _ax(e) == h_ax
            ]
            if matches:
                return _nearest_idx(hist, matches), MatchLevel.AX_NAME
        # Level 5: ATTRIBUTE
        h_attrs = hist.get("attributes") or {}
        if h_attrs:
            for attr_key in ("name", "id", "aria-label"):
                target = h_attrs.get(attr_key)
                if target:
                    matches = [
                        (idx, e) for idx, e in selector_map.items()
                        if e.node_name.lower() == h_name
                        and (e.attributes or {}).get(attr_key) == target
                    ]
                    if matches:
                        return _nearest_idx(hist, matches), MatchLevel.ATTRIBUTE
        # Level 6: CLASS（最末兜底）
        # 适用 SPA 按钮/触发器只有 CSS 类、无 name/id/aria-label 的场景（如抖音「确定」
        # class="semi-button semi-button-primary btn-xtdEbg"）。要求候选 class token ⊇ 录制
        # token（顺序无关、可多不可少，容忍候选额外加的状态类），多候选按 bounds 就近。
        # 仅作最末兜底（前 5 级全失败才到这），跨构建的 CSS-module 哈希后缀漂移会失效。
        h_class = (h_attrs.get("class") or "").strip() if h_attrs else ""
        if h_class:
            want_tokens = {t for t in h_class.split() if t}
            if want_tokens:
                matches = []
                for idx, e in selector_map.items():
                    if e.node_name.lower() != h_name:
                        continue
                    e_cls = (e.attributes or {}).get("class") or ""
                    if want_tokens.issubset({t for t in e_cls.split() if t}):
                        matches.append((idx, e))
                if matches:
                    return _nearest_idx(hist, matches), MatchLevel.CLASS
        return None

    def _resolve_file_input_by_accept(
        self, state: Any, path: str, xpath_hint: str = "", accept_hint: str = "",
    ) -> int | None:
        """按 accept(文件类型) + xpath 从当前页 selector_map 解析 file input 的 index。

        upload_file 在录制端不 get_state 定位（B 方案：选完文件抖音立即 /upload→/post/video
        跳转，get_state 会抓到跳转后页面致 file input 错位），改存 accept+xpath 签名（change
        瞬间扩展捕获）。重放时页面已稳定、file input 在 selector_map，按签名解析：
        - kind 优先取自 ``accept_hint``（扩展捕获的真实 accept），否则按 path 扩展名
          （mp4→video、png→image）推断；
        - 同 accept 多个（横/竖封面）→ ``xpath_hint`` normalize 后唯一命中区分。
        无匹配返回 None。
        """
        sm = state.dom_state.selector_map if state and state.dom_state else {}
        if accept_hint:
            ah = accept_hint.lower()
            kind = "video" if "video" in ah else ("image" if "image" in ah else None)
        else:
            ext = Path(path or "").suffix.lower().lstrip(".")
            kind = "video" if ext in _UPLOAD_VIDEO_EXTS else ("image" if ext in _UPLOAD_IMAGE_EXTS else None)
        candidates: list[tuple[int, Any]] = []
        for idx, node in sm.items():
            attrs = getattr(node, "attributes", None) or {}
            if (getattr(node, "node_name", "") or "").upper() != "INPUT" \
                    or attrs.get("type", "").lower() != "file":
                continue
            if kind is None or kind in (attrs.get("accept", "") or "").lower():
                candidates.append((idx, node))
        if not candidates:
            return None
        # 同 accept 多个 → xpath_hint 唯一命中区分
        if xpath_hint and len(candidates) > 1:
            want = normalize_xpath(xpath_hint)
            if want:
                hits = [idx for idx, node in candidates
                        if normalize_xpath(getattr(node, "xpath", "")) == want]
                if len(hits) == 1:
                    return hits[0]
        return candidates[0][0]

    def _update_action_indices(
        self,
        historical_elem: dict[str, Any] | None,
        action: dict[str, Any],
        selector_map: dict[int, Any],
    ) -> dict[str, Any] | None:
        """重定位后返回更新了 index 的 action（深拷贝）；匹配失败返回 None。"""
        if not historical_elem:
            return None
        node_name = historical_elem.get("node_name") or "?"
        logger.info("🔍 定位元素: <%s> hash=%s stable_hash=%s",
                    node_name, historical_elem.get("element_hash"),
                    historical_elem.get("stable_hash"))
        if logger.isEnabledFor(logging.DEBUG):
            same_tag = [
                (idx, e.node_name, (e.attributes or {}).get("name")
                 or (e.attributes or {}).get("aria-label"))
                for idx, e in selector_map.items()
                if e.node_name == historical_elem.get("node_name")
            ]
            logger.debug("🔍 Selector map 共 %d 元素，其中 %d 个 <%s>: %s",
                         len(selector_map), len(same_tag), node_name, same_tag[:10])
        match = self._match_element_index(historical_elem, selector_map)
        if match is None:
            return None
        new_index, level = match
        new_action = copy.deepcopy(action)
        params = new_action.get("params") if isinstance(new_action.get("params"), dict) else {}
        new_action["params"] = params
        old = params.get("index", params.get("element_id"))
        # 统一写回 index（element_id 是别名；tools._flatten_params 处理 index）
        params["index"] = new_index
        params.pop("element_id", None)
        if old != new_index:
            logger.info("元素 index 更新 %s → %s（匹配级别 %s）", old, new_index, level.name)
        return new_action

    def _format_match_failure(
        self, hist_elem: dict[str, Any], action_index: int, selector_map: dict[int, Any]
    ) -> str:
        name = hist_elem.get("node_name") or "?"
        attrs = hist_elem.get("attributes") or {}
        attr_str = " ".join(f'{k}="{v}"' for k, v in list(attrs.items())[:4])
        h_role = (attrs.get("role") or "").lower()
        # 列出同标签（同类 role）的候选元素，便于诊断：目标到底在不在页面上、当前 ax_name 是什么
        candidates: list[str] = []
        for idx, e in selector_map.items():
            if e.node_name != hist_elem.get("node_name"):
                continue
            if h_role and (e.attributes or {}).get("role", "").lower() != h_role:
                continue
            e_ax = e.ax_node.name if e.ax_node and getattr(e.ax_node, "name", None) else None
            e_cls = (e.attributes or {}).get("class", "")
            candidates.append(f"[{idx}] ax={e_ax!r} class={e_cls!r}")
            if len(candidates) >= 10:
                break
        cand_block = (
            "; ".join(candidates) if candidates
            else "(无同标签候选——元素不在 selector_map，可能下拉已关闭或被排除)"
        )
        return (
            f"Could not find matching element for action {action_index} in current page. "
            f"Looking for: <{name}> {attr_str} hash={hist_elem.get('element_hash')} "
            f"xpath={hist_elem.get('x_path')} ax_name={hist_elem.get('ax_name')!r}. "
            f"Page has {len(selector_map)} interactive elements. "
            f"Same-<{name}> candidates: {cand_block}. "
            f"Tried: EXACT -> STABLE -> XPATH -> AX_NAME -> ATTRIBUTE -> CLASS"
        )

    def _format_semantic_clue_failure(self, clue: dict[str, Any], selector_map: dict[int, Any]) -> str:
        """语义线索重定位失败的诊断信息（录制失败步在重放端重新定位也没找到）。"""
        tag = (clue.get("tag") or "?").upper()
        attr_str = " ".join(
            f'{k}="{clue[k]}"' for k in ("name", "id", "ariaLabel", "role") if clue.get(k)
        )
        candidates = [
            f"[{idx}] class={(e.attributes or {}).get('class', '')!r}"
            f" name={(e.attributes or {}).get('name', '')!r}"
            for idx, e in selector_map.items()
            if (getattr(e, "node_name", "") or "").upper() == tag
        ][:10]
        cand_block = "; ".join(candidates) if candidates else "(无同标签候选)"
        return (
            f"Semantic-clue relocate failed for <{tag}> {attr_str} "
            f"xpath={clue.get('xpath')!r}. Page has {len(selector_map)} interactive elements. "
            f"Same-<{tag}> candidates: {cand_block}. "
            f"Tried: XPATH -> ATTRIBUTE -> RECT (locate_by_ref)"
        )

    # ── 跳过/重试辅助 ──────────────────────────────────────────────────

    @staticmethod
    def _first_action_name(item: AgentHistory) -> str | None:
        actions = item.model_output.get("actions") or [item.model_output.get("action", {})]
        if actions and isinstance(actions[0], dict):
            return actions[0].get("name")
        return None

    def _is_redundant_retry_step(
        self,
        curr: AgentHistory,
        prev: AgentHistory | None,
        prev_succeeded: bool,
    ) -> bool:
        if not prev or not prev_succeeded:
            return False
        curr_elems = curr.interacted_element or []
        prev_elems = prev.interacted_element or []
        if not curr_elems or not prev_elems:
            return False
        c, p = curr_elems[0], prev_elems[0]
        if not c or not p:
            return False
        same = (
            c.get("element_hash") == p.get("element_hash")
            or c.get("stable_hash") == p.get("stable_hash")
            or c.get("x_path") == p.get("x_path")
        )
        if not same:
            return False
        return self._first_action_name(curr) == self._first_action_name(prev)

    def _is_menu_opener_step(self, item: AgentHistory | None) -> bool:
        """上一步是否是「下拉/菜单触发器」（重放失败时可据此重打开）。

        启发式覆盖 ARIA 语义 + 主流前端框架的触发器 class：
        - ``aria-haspopup``（任意值）/ ``aria-expanded``（任意元素，不限 role=button）
        - ``role`` ∈ {combobox, button, menuitem, menu, option, listbox}
        - class 含 select / dropdown / combobox / menu / picker / expand-button
          （涵盖 semi-select、ant-select、el-select、select-selection 等）
        """
        elem = _first_interacted_element(item)
        if not elem:
            return False
        attrs = elem.get("attributes") or {}
        cls = (attrs.get("class") or "").lower()
        role = (attrs.get("role") or "").lower()
        if attrs.get("aria-haspopup"):
            return True
        if attrs.get("aria-expanded") is not None:
            return True
        if role in ("combobox", "button", "menuitem", "menu", "option", "listbox"):
            return True
        if any(k in cls for k in ("select", "dropdown", "combobox", "menu", "picker", "expand-button")):
            return True
        return False

    def _is_option_element(self, item: AgentHistory | None) -> bool:
        """当前步要找的元素是否是「下拉选项」（框架无关的强信号）。

        ``role=option`` 或 class 含 option / select-item → 几乎必然是某个下拉的选项；
        匹配失败时，上一步几乎必然是打开它的触发器（无论触发器 class 是什么），
        故可据此触发「菜单重打开」，而不必依赖 ``_is_menu_opener_step`` 认得出触发器。
        """
        elem = _first_interacted_element(item)
        if not elem:
            return False
        attrs = elem.get("attributes") or {}
        if (attrs.get("role") or "").lower() == "option":
            return True
        cls = (attrs.get("class") or "").lower()
        return "option" in cls or "select-item" in cls

    async def _reexecute_menu_opener(
        self, opener_item: AgentHistory, ai_step_llm: LLMClient | None,
        wait_for_page_settle: bool, rerun_actionability_check: bool,
        rerun_actionability_receives_events: bool, rerun_actionability_runtime_occlusion: bool,
        rerun_actionability_stable: bool, wait_for_networkidle: bool,
    ) -> bool:
        logger.info("🔁 菜单重打开：重执行上一步（opener）以重新展开下拉，随后立即重试")
        try:
            await self._execute_history_step(
                opener_item, delay=0.5, ai_step_llm=ai_step_llm, wait_for_elements=False,
                wait_for_page_settle=wait_for_page_settle,
                rerun_actionability_check=rerun_actionability_check,
                rerun_actionability_receives_events=rerun_actionability_receives_events,
                rerun_actionability_runtime_occlusion=rerun_actionability_runtime_occlusion,
                rerun_actionability_stable=rerun_actionability_stable,
                wait_for_networkidle=wait_for_networkidle,
            )
            await asyncio.sleep(0.3)  # 等菜单渲染
            return True
        except Exception as e:
            logger.warning("菜单重打开失败: %s", e)
            return False

    def _count_expected_elements(self, item: AgentHistory) -> int:
        actions = item.model_output.get("actions") or [item.model_output.get("action", {})]
        max_index = -1
        for action in actions:
            if not isinstance(action, dict):
                continue
            params = action.get("params") if isinstance(action.get("params"), dict) else {}
            idx = params.get("index")
            if idx is None:
                idx = params.get("element_id")
            if isinstance(idx, int) and idx > max_index:
                max_index = idx
        return max_index + 1 if max_index >= 0 else 0

    async def _wait_until(
        self, state: Any, predicate: Any, timeout: float, poll: float = 1.0, refresh: bool = True
    ) -> Any:
        """通用轮询：谓词命中即返回；超时降级返回当前 state（不抛错）。

        复用原 ``_wait_for_minimum_elements`` 的 deadline+poll+吞异常+降级 骨架。
        ``predicate(state) -> bool``，每轮先判再 sleep；``refresh=True`` 时 poll 后调
        ``get_state`` 刷新。缺口 5（等目标元素）/ actionability / 既有数量等待共用。
        """
        deadline = time.time() + timeout
        while True:
            if predicate(state):
                return state
            if time.time() >= deadline:
                return state
            await asyncio.sleep(poll)
            if refresh:
                try:
                    state = await self.browser.get_state(include_screenshot=False)
                except Exception:
                    pass

    async def _wait_for_minimum_elements(
        self, state: Any, min_elements: int, timeout: float = 15.0, poll: float = 1.0
    ) -> Any:
        """等 selector_map 元素总数 ≥ min_elements（阶段2 前的粗粒度等待，保留为薄封装）。"""
        return await self._wait_until(
            state,
            lambda s: bool(s and s.dom_state and len(s.dom_state.selector_map) >= min_elements),
            timeout, poll,
        )

    def _locate_target(
        self, hist_elem: dict[str, Any] | None, selector_map: dict[int, Any]
    ) -> tuple[int, Any] | None:
        """只读判定：能否在当前 selector_map 定位目标，返回 (idx, node) | None。

        统一两条定位路径：语义线索→``locate_by_ref``（已返回 idx+node）；指纹→``_match_element_index``
        得 idx 再取 node。``hist_elem`` 为 None（extract/wait/navigate/无指纹 upload_file）→ None。
        仅供等待循环（缺口 5）与 actionability 重解析复用；动作循环的写入定位（``_update_action_indices``
        深拷贝写回）不调用本函数，避免回归。
        """
        if not hist_elem:
            return None
        if hist_elem.get("_semantic_clue"):
            matched = locate_by_ref(hist_elem, selector_map)
            return matched if matched else None
        m = self._match_element_index(hist_elem, selector_map)
        if m is None:
            return None
        node = selector_map.get(m[0])
        return (m[0], node) if node is not None else None

    def _collect_target_hists(self, item: AgentHistory) -> list[dict[str, Any]]:
        """枚举本步所有需定位 action 的 hist_elem（剔除 upload_file / 无指纹 action）。"""
        actions = item.model_output.get("actions") or [item.model_output.get("action", {})]
        interacted = item.interacted_element or []
        out: list[dict[str, Any]] = []
        for i, action in enumerate(actions):
            if not isinstance(action, dict) or action.get("name") == "upload_file":
                continue  # accept 路径兜底，无预判价值
            h = interacted[i] if i < len(interacted) else None
            if h and (
                h.get("_semantic_clue") or h.get("element_hash") or h.get("stable_hash")
                or h.get("x_path") or h.get("ax_name") or h.get("attributes")
            ):
                out.append(h)
        return out

    async def _wait_for_target_elements(
        self, state: Any, item: AgentHistory, timeout: float = 15.0, poll: float = 1.0
    ) -> Any:
        """缺口 5：等本步所有需定位 action 的目标在 selector_map 能定位（all-or-nothing）。

        超时降级返回最新 state（不抛错）；后续动作循环若仍定位不到，由既有 ValueError +
        ``_rerun_step_with_retries`` 处理。本步无目标（纯 extract/wait）→ 立即返回不 poll。
        """
        targets = self._collect_target_hists(item)
        if not targets:
            return state

        def _all_located(s: Any) -> bool:
            sm = s.dom_state.selector_map if s and s.dom_state else {}
            return all(self._locate_target(h, sm) is not None for h in targets)

        return await self._wait_until(state, _all_located, timeout, poll)

    async def _wait_for_actionability(
        self,
        state: Any,
        hist_elem: dict[str, Any],
        initial_idx: int,
        timeout: float,
        poll: float,
        receives_events: bool = False,
        runtime_occlusion: bool = False,
    ) -> tuple[Any, int | None, Any | None]:
        """actionability（阶段一 visible+enabled / 阶段二 receives-events / L3 运行时遮挡）。

        自带 deadline 循环（不再借 ``_wait_until``）——因为 L3 运行时遮挡需 ``await
        _is_element_occluded``，而 ``_wait_until`` 的 predicate 是同步的。每轮用 ``hist_elem``
        重 ``_locate_target`` 拿最新 ``(idx, node)``（poll 后 index 可能漂移），再依次查：

        (1) ``_is_actionable``：visible + enabled + L1/L2 receives-events（同步，零开销静态）；
        (2) L3 ``_is_element_occluded``：``elementFromPoint`` 运行时遮挡（async，
            ``runtime_occlusion=True`` 时才查）。

        命中即返回；超时降级返回最新 ``(state, idx, node)``，不抛错（让 ``_exec_one`` 照常执行 →
        ``_rerun_step_with_retries`` 兜底）。
        """
        fresh_idx = initial_idx
        fresh_node = (
            state.dom_state.selector_map.get(initial_idx) if state and state.dom_state else None
        )
        deadline = time.time() + timeout
        while True:
            sm = state.dom_state.selector_map if state and state.dom_state else {}
            located = self._locate_target(hist_elem, sm)
            if located is not None:
                fresh_idx, fresh_node = located
                actionable = _is_actionable(fresh_node, check_receives_events=receives_events)
                if actionable and runtime_occlusion and fresh_node is not None:
                    # L3：运行时 elementFromPoint 遮挡判定（async，独立开关，默认关）
                    occluded = await self.browser._is_element_occluded(
                        fresh_node.backend_node_id, fresh_node.x, fresh_node.y,
                    )
                    actionable = not occluded
                if actionable:
                    return state, fresh_idx, fresh_node
            if time.time() >= deadline:
                return state, fresh_idx, fresh_node  # 超时降级，不抛错
            await asyncio.sleep(poll)
            try:
                state = await self.browser.get_state(include_screenshot=False)
            except Exception:
                pass

    async def _is_rect_stable(
        self, backend_node_id: int, interval: float = 0.1, tolerance: float = 1.0,
    ) -> bool:
        """两次取 rect 比（~interval 间隔），位置/尺寸变化 ≤ tolerance 视为稳定。

        复用 ``session.get_element_coordinates`` 三级 fallback（getContentQuads → getBoxModel →
        JS getBoundingClientRect），零新 CDP 封装。拿不到坐标（None）→ 视为不稳定（保守）。
        """
        r1 = await self.browser.get_element_coordinates(backend_node_id)
        if r1 is None:
            return False
        await asyncio.sleep(interval)
        r2 = await self.browser.get_element_coordinates(backend_node_id)
        if r2 is None:
            return False
        return (
            abs(r1.x - r2.x) <= tolerance and abs(r1.y - r2.y) <= tolerance
            and abs(r1.width - r2.width) <= tolerance
            and abs(r1.height - r2.height) <= tolerance
        )

    async def _wait_for_stable(
        self,
        state: Any,
        hist_elem: dict[str, Any],
        initial_idx: int,
        timeout: float,
        poll: float,
        interval: float,
        tolerance: float,
    ) -> tuple[Any, int | None]:
        """stable（阶段三）：轮询直到目标 rect 稳定；超时降级返回最新 (state, idx)。

        仿 ``_wait_for_actionability`` 的 deadline 循环——每轮用 ``hist_elem`` 重 ``_locate_target``
        拿最新 ``(idx, node)``（poll 后 index 可能漂移），再查 ``_is_rect_stable``。降级不抛错，
        让 ``_exec_one`` 照常执行 → ``_rerun_step_with_retries`` 兜底。
        """
        fresh_idx = initial_idx
        deadline = time.time() + timeout
        while True:
            sm = state.dom_state.selector_map if state and state.dom_state else {}
            located = self._locate_target(hist_elem, sm)
            if located is not None:
                fresh_idx, node = located
                if await self._is_rect_stable(node.backend_node_id, interval, tolerance):
                    return state, fresh_idx
            if time.time() >= deadline:
                return state, fresh_idx  # 超时降级，不抛错
            await asyncio.sleep(poll)
            try:
                state = await self.browser.get_state(include_screenshot=False)
            except Exception:
                pass

    # ── 变量替换 ───────────────────────────────────────────────────────

    def _substitute_variables_in_history(
        self, history: AgentHistoryList, variables: dict[str, str]
    ) -> AgentHistoryList:
        """变量名→原始值→新值；精确整串替换；只改动作参数，不碰 interacted_element/result。"""
        from tree_walker.agent.variable_detector import detect_variables_in_history

        detected = detect_variables_in_history(history)
        value_replacements: dict[str, str] = {}
        for var_name, new_value in variables.items():
            if var_name in detected:
                value_replacements[detected[var_name].original_value] = new_value
            else:
                logger.warning("变量 %r 在历史中未检测到，跳过", var_name)
        if not value_replacements:
            return history

        logger.info("🔁 已替换 %d 个变量值", len(value_replacements))
        modified = copy.deepcopy(history)
        for item in modified.history:
            actions = item.model_output.get("actions") or [item.model_output.get("action", {})]
            for action in actions:
                if isinstance(action, dict):
                    params = action.get("params")
                    if isinstance(params, dict):
                        _substitute_in_dict(params, value_replacements)
        return modified

    # ── AI 摘要（无截图）──────────────────────────────────────────────

    async def _generate_rerun_summary(
        self,
        original_task: str,
        results: list[ActionResult],
        summary_llm: LLMClient | None,
    ) -> ActionResult:
        llm = summary_llm or self.llm
        logger.info("🤖 生成重放完成 AI 摘要...")
        error_count = sum(1 for r in results if r.error)
        success_count = len(results) - error_count
        completion = (
            "complete" if error_count == 0
            else ("partial" if success_count > 0 else "failed")
        )
        evidence = self._build_rerun_evidence(results)
        prompt = get_rerun_summary_prompt(
            original_task=original_task,
            total_steps=len(results),
            success_count=success_count,
            error_count=error_count,
            evidence=evidence,
        )

        # Layer 1：结构化（复用 LLMClient.extract 的 output_schema 机制）
        try:
            schema = RerunSummaryAction.model_json_schema()
            raw = await asyncio.wait_for(
                llm.extract(prompt=prompt, content=evidence, output_schema=schema),
                timeout=120.0,
            )
            summary = RerunSummaryAction.model_validate_json(raw)
        except Exception:
            # Layer 2：文本兜底
            try:
                text = await asyncio.wait_for(
                    llm.extract(prompt=prompt, content=evidence, output_schema=None),
                    timeout=120.0,
                )
                summary = RerunSummaryAction(
                    summary=text or "",
                    success=(error_count == 0),
                    completion_status=completion,
                )
            except Exception as e:
                # Layer 3：纯计数兜底（不调 LLM）
                logger.warning("AI 摘要 LLM 调用失败，降级为纯计数: %s", e)
                summary = RerunSummaryAction(
                    summary=f"{success_count}/{len(results)} steps succeeded",
                    success=(error_count == 0),
                    completion_status=completion,
                )

        logger.info("📊 重放摘要: %s", _truncate(summary.summary, 300))
        logger.info("📊 状态: %s (success=%s, %d/%d 步成功)",
                    summary.completion_status, summary.success,
                    success_count, len(results))

        return ActionResult(
            is_done=True,
            success=summary.success,
            extracted_content=summary.summary,
            long_term_memory=f"Rerun completed with status: {summary.completion_status}.",
        )

    @staticmethod
    def _build_rerun_evidence(results: list[ActionResult]) -> str:
        lines: list[str] = []
        for i, r in enumerate(results):
            status = "ERROR" if r.error else ("DONE" if r.is_done else "OK")
            excerpt = (r.extracted_content or "")[:200]
            line = f"Step {i}: {status}"
            if excerpt:
                line += f" — {excerpt}"
            if r.error:
                line += f" [err: {r.error}]"
            lines.append(line)
        return "\n".join(lines)
