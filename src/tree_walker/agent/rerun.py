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
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from tree_walker.action_shape import actions_of, params_of
from tree_walker.agent.actionability import (
    ACTIONABILITY_ACTIONS as _ACTIONABILITY_ACTIONS,
    is_actionable as _is_actionable,
    is_file_input as _is_file_input,
    is_rect_stable,
)
from tree_walker.agent.upload_identity import (
    _UPLOAD_IMAGE_EXTS,
    _UPLOAD_VIDEO_EXTS,
    effective_clue_rect,
    file_input_candidates,
    upload_input_contexts,
)
from tree_walker.agent.views import (
    AgentHistory,
    AgentHistoryList,
    ActionResult,
    DetectedVariable,
    RerunSummaryAction,
)
from tree_walker.prompts.rerun_summary import get_rerun_summary_prompt
from tree_walker.recorder.locator import locate_by_ref, normalize_text, normalize_xpath

if TYPE_CHECKING:
    from tree_walker.browser.session import BrowserSession
    from tree_walker.llm.client import LLMClient
    from tree_walker.tools.actions import Tools

logger = logging.getLogger(__name__)


class MatchLevel(Enum):
    """元素匹配严格级别（命中即停，逐级降级）。"""

    TEXT = 0       # node_name + 可见文字相等（扩展捕获的点击瞬间 ground truth，优先于指纹——issue #136）
    EXACT = 1      # element_hash 完全相等（TreeWalker 确定性 sha256，跨会话稳定）
    STABLE = 2     # stable_hash 相等（过滤动态 CSS 类，重放首选）
    XPATH = 3      # x_path 字符串相等
    AX_NAME = 4    # node_name + ax_name 相等（动态菜单/SPA 救星）
    ATTRIBUTE = 5  # node_name + name/id/aria-label 相等（兜底）
    CLASS = 6      # node_name + class token 超集（最末兜底：无 name/id/aria-label 的 SPA 按钮）


# actionability 纯函数（白名单 / is_file_input / is_actionable）已抽到共享模块
# ``tree_walker.agent.actionability``（探索端 + 重放端共用）；顶部 import 处保私有别名供本模块调用。
# 详见 docs/wait-and-timing/02-阶段2、05-阶段4 与 docs/p3/01-探索可靠性提升方案.md


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


def _apply_manual_variable_at_location(
    history_items: list, step_number: int, action_index: int, field: str, new_value: str,
) -> bool:
    """把手工变量按 (step_number, action_index, field) 精确写入对应 action 的 params，返回是否命中。

    用于区分「同 original_value、不同位置」的多个手工变量——value-based 替换（_substitute_in_dict）
    按 original_value 建 dict 会撞 key（后写覆盖先写），位置绑定直接定位则不会。典型场景：录制时
    标题与简介填了同一文本，title-1(标题) 与 title-2(简介) 的 original_value 相同。"""
    for item in history_items:
        if getattr(item, "step_number", None) != step_number:
            continue
        model_output = item.model_output or {}
        actions = model_output.get("actions")
        if not actions:
            single = model_output.get("action")
            actions = [single] if single else []
        if not (0 <= action_index < len(actions)):
            return False
        action = actions[action_index]
        if not isinstance(action, dict):
            return False
        # review6 #9 残留守卫：params_of 统一「非 dict → {}」
        params = params_of(action)
        if field in params:
            params[field] = new_value
            return True
        return False
    return False


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


# 上传扩展名集 + file-input 身份逻辑已移至 ``upload_identity``（单一真相源，#151）。
# 此处经顶部 import 复用 _UPLOAD_VIDEO_EXTS / _UPLOAD_IMAGE_EXTS（``_upload_file_kind`` 用）。


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
        *,
        on_step: Callable[[int, int, list[ActionResult]], Awaitable[None]] | None = None,
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
        return await self.rerun_history(history, on_step=on_step, **kwargs)

    async def batch_rerun(
        self,
        history_file: str | Path,
        csv_path: str | Path,
        *,
        variables: dict[str, str] | None = None,
        on_row: Callable[[BatchRowResult], Awaitable[None]] | None = None,
        on_step: Callable[[int, int, list[ActionResult]], Awaitable[None]] | None = None,
        **kwargs: Any,
    ) -> list[BatchRowResult]:
        """CSV 批量重放：每行变量跑一次 ``load_and_rerun``（串行，P4 子任务 2）。

        CSV 列头 = 变量名（detect + manual 的并集，见 ``merge_variable_sources``）。
        缺列 = 该变量用 history 原值（宽容，不报错）；空单元格（""）跳过不注入。
        每行叠加全局 ``variables``（行值优先）。逐行串行（共享一个 BrowserSession）；
        单行异常不中断批量，记入该行 ``error``。

        ``on_row``：每行完成（无论成功/失败）后异步回调，Web SSE 行级进度推送用；
        CLI 不传则行为不变。行循环顶部与每行结束后检查 ``self.state.stopped``，
        协作式取消（``Agent.stop()``）在行边界生效（issue #155）。
        """
        import csv

        from tree_walker.agent.views import BatchRowResult

        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            logger.warning("CSV %s 无数据行", csv_path)

        base = variables or {}
        results: list[BatchRowResult] = []
        for i, row in enumerate(rows):
            if self.state.stopped:  # 协作式取消：行前检查（issue #155）
                break
            merged = {**base, **{k: v for k, v in row.items() if v not in (None, "")}}
            try:
                row_results = await self.load_and_rerun(
                    history_file, variables=merged or None, on_step=on_step, **kwargs
                )
                last = row_results[-1] if row_results else None
                result = BatchRowResult(
                    row_index=i,
                    variables=merged,
                    success=bool(last and last.is_done and last.success),
                    n_steps=len(row_results),
                    extracted_content=last.extracted_content if last else None,
                    error=last.error if last and last.error else None,
                )
            except Exception as e:  # 单行失败不中断批量（不捕获 CancelledError）
                logger.exception("CSV 第 %d 行重放失败", i)
                result = BatchRowResult(
                    row_index=i, variables=merged, success=False, error=str(e)
                )
            results.append(result)
            if on_row is not None:  # 行级进度回调（issue #155，Web SSE 推送用）
                await on_row(result)
            if self.state.stopped:  # 协作式取消：行后检查（覆盖取消在行执行中到达）
                break
        return results

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
        on_step: Callable[[int, int, list[ActionResult]], Awaitable[None]] | None = None,
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
                if on_step is not None:  # 步级进度回调（issue #155，每步推送）
                    await on_step(i, total, step_results)
                previous_succeeded = bool(step_results) and not any(
                    r.error for r in step_results
                )
                previous_item = item
                # 注意：不在外层循环因 is_done 而 break——done 可能出现在录制中途
                #（例如 agent 先 done、再因错误重试一步）。重放须忠实回放每一步，
                # done 只终止「该步内的动作链」（_execute_history_step 的 guard），
                # 不应截断后续步骤。对齐 browser-use rerun_history。
        finally:
            if not self.state.stopped:  # 正常路径：生成 AI 摘要
                summary = await self._generate_rerun_summary(self.task, results, summary_llm)
                results.append(summary)
            else:  # 取消快速路径：跳过 summary（否则卡 ~120s LLM），直接关浏览器（issue #155）
                results.append(
                    ActionResult(
                        is_done=True,
                        success=False,
                        extracted_content="Rerun cancelled by user.",
                    )
                )
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
        actions = actions_of(item.model_output)
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
            # 归一化主责在历史加载入口（load_from_dict，review2 #3）；但
            # rerun_history() 也接受内存构造 / model_validate 加载的
            # AgentHistoryList（未经该入口，review3 #5）——params_of 共享访问器
            # 兜住（review4 #8：不再手写本地拼法）。
            fp = params_of(first)
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
        actions = actions_of(item.model_output)
        interacted = item.interacted_element or []

        results: list[ActionResult] = []
        for i, action in enumerate(actions):
            if not isinstance(action, dict):
                continue
            name = action.get("name", "done")
            hist_elem = None  # extract 分支不设；下方 actionability 块据此安全引用

            if name == "extract":
                # extract 自带 LLM 且读当前页（tools/_action_extract）→ 直接 re-execute 即在当前页重算
                params = dict(params_of(action))  # review4 #3：内存历史旁路形态不崩
            else:
                hist_elem = interacted[i] if i < len(interacted) else None
                raw_params = params_of(action)
                has_index = raw_params.get("index") is not None or raw_params.get("element_id") is not None
                if hist_elem and hist_elem.get("_semantic_clue"):
                    # 语义线索路径：录制时 locate 失败（get_state 抓变化后页），存了 e.target 的
                    # xpath/tag/attr/rect 线索。重放有主动时序优势（到这步页面稳定、元素完好），
                    # 复用 locate_by_ref 重新定位。详见 semantic-clue-replay.md。
                    # upload_file（kind=file_upload）走专用 _match_file_upload_by_clue：accept 粗筛
                    # → area_text（封装组件 drag-area 文案）精筛——替 locate_by_ref（file input 隐藏
                    # 无属性，四防线全失效）+ 替 candidates[0] 时序漂移（issue #139）。
                    if hist_elem.get("kind") == "file_upload":
                        matched_idx = await self._match_file_upload_by_clue(hist_elem, selector_map)
                    else:
                        matched = locate_by_ref(hist_elem, selector_map)
                        matched_idx = matched[0] if matched is not None else None
                    if matched_idx is not None:
                        params = dict(raw_params)
                        params["index"] = matched_idx
                        logger.info("语义线索重定位 idx=%s（action=%s）", matched_idx, name)
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
                    params = dict(params_of(action))  # review4 #3：内存历史旁路形态不崩
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
                    # review6 #6：历史含 null/非 str name（旁路构造未经归一化）时
                    # pydantic str 字段在事件构造处 ValidationError、烧掉整个
                    # 重试梯——str() 包裹，emit 移入 try 由下方执行错误优雅呈现
                    action_name=str(action_name or ""),
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

        # Level 0: TEXT（扩展捕获的点击瞬间文字，ground truth）——优先于指纹/ax_name：
        # get_state 在动作后跑，状态依赖元素（cover step tab 的 step-active 类、toggle 按钮的翻转
        # 文字）的指纹/名称可能已是动作后状态，反而匹配到错误元素（issue #136）。仅当扩展提供了
        # text 才启用（旧录制/agent 自录无 text → 跳过，走原指纹路径，向后兼容）。
        h_text = normalize_text(hist.get("text"))
        if h_text:
            matches = [
                (idx, e) for idx, e in selector_map.items()
                if e.node_name.lower() == h_name
                and normalize_text(e.get_all_children_text()) == h_text
            ]
            if matches:
                return _nearest_idx(hist, matches), MatchLevel.TEXT
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

    def _file_input_candidates(
        self, selector_map: dict[int, Any], *, accept_hint: str = "", path: str = "",
    ) -> list[tuple[int, Any]]:
        """收集 accept(文件类型 kind) 匹配的 file input 候选（selector_map 迭代顺序）。

        kind 优先取自 ``accept_hint``（扩展 change 瞬间捕获的真实 accept），否则按 path 扩展名
        （mp4→video、png→image）推断。供 ``_resolve_file_input_by_accept``（老 accept 兜底）与
        ``_match_file_upload_by_clue``（issue #139 语义线索精筛）共用，避免重复。
        """
        # #151：实现移至 ``upload_identity.file_input_candidates``（采集端与重放端共用同一份）。
        return file_input_candidates(selector_map, accept_hint=accept_hint, path=path)

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

        候选收集抽出 ``_file_input_candidates``（与 ``_match_file_upload_by_clue`` 共用）。这是
        **老 history（``interacted_element=[None]``）**走的路径；新录制带 ``_semantic_clue`` 的
        upload 走 ``_match_file_upload_by_clue``（见 ``_execute_history_step`` 语义线索分支）。
        """
        sm = state.dom_state.selector_map if state and state.dom_state else {}
        candidates = self._file_input_candidates(sm, accept_hint=accept_hint, path=path)
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

    async def _upload_input_contexts(
        self, candidates: list[tuple[int, Any]], kind: str = "",
    ) -> dict[int, dict[str, Any]]:
        """单次 ``execute_js`` 扫页面上所有 ``input[type=file]``（DOM 文档序），返回每个 input 的
        **站点无关**通用身份（issue #139 通用化）：``accept`` + 原生 label 文案 + aria-labelledby 解析
        + 就近可见文本祖先 + 是否在 ARIA dialog 内 + 可点 affordance 文案/role/rect + ``container_rect``（#151，隐藏 input 位置信号）。Python 侧按 ``kind``
        过滤后，与 ``candidates``（``_file_input_candidates`` 同款过滤）按 **DOM 序下标** 对齐 →
        ``{backend_id: ctx}``（``_match_file_upload_by_clue`` 精筛用）。

        替代旧 ``_upload_widget_contexts`` 写死的 Semi-UI 选择器（``.semi-upload``/drag-area/step-active/
        ``[class*="modal"]``）——改读标准信号，跨框架/站点通用（Ant/Element/原生都覆盖）。关键不变量保留：
        JS 必须返回 accept、Python 用与 ``_file_input_candidates`` 完全相同的 kind 过滤再对齐（坑③：否则
        页面 6 个 input 含 1 video ≠ 5 image 候选，下标对不上 → 计数不等放弃精筛，matcher 降级到可见性/rect，
        不崩）。详见 ``docs/user_recording/upload-general-identity-impl-plan.md``。
        """
        # #151：实现移至 ``upload_identity.upload_input_contexts``——JS 探针扩展了 affordance_rect /
        # container_rect（隐藏 input 的位置信号），采集端（actions.py）与重放端共用同一份。
        return await upload_input_contexts(self.browser, candidates, kind=kind)

    async def _match_file_upload_by_clue(
        self, clue: dict[str, Any], selector_map: dict[int, Any],
    ) -> int | None:
        """upload_file 语义线索精筛（issue #139 通用化）：accept 粗筛 → trigger_affordance（L2）→
        文本束 label/aria/region（L1，或 legacy ``area_text`` 别名）→ in_dialog（或 legacy ``in_modal``）
        → 可见性 → rect 就近。

        站点无关：线索字段（label_text/aria_text/region_text/in_dialog/trigger_affordance）由扩展
        ``captureUploadCtx`` 与本类 ``_upload_input_contexts`` 用**同一份标准信号逻辑**各自从活 DOM 算出，
        字段相等即匹配。每级独立收窄、有日志、失败不崩——尾部 visibility + rect 就近即当前抖音横/竖封面
        的实际解法（通用化只增不减）。老 history（fix/139 的 area_text/in_modal）走 legacy 别名零回归。
        逐级降级、不抛错（让 ``_exec_one`` 照常执行 → ``_rerun_step_with_retries`` 兜底）。
        """
        candidates = self._file_input_candidates(
            selector_map, accept_hint=clue.get("accept", ""), path="",
        )
        cand_ids = [idx for idx, _ in candidates]
        if not candidates:
            logger.info("upload 线索精筛：无 accept 候选")
            return None
        if len(candidates) == 1:
            logger.info("upload 线索精筛：唯一候选 idx=%s", cand_ids[0])
            return cand_ids[0]

        want_aff = ((clue.get("trigger_affordance") or {}).get("text") or "").strip()
        want_label = (clue.get("label_text") or "").strip()
        want_aria = (clue.get("aria_text") or "").strip()
        want_region = (clue.get("region_text") or clue.get("area_text") or "").strip()  # legacy 别名
        want_in_dialog = clue.get("in_dialog")
        if want_in_dialog is None:
            want_in_dialog = clue.get("in_modal")  # legacy 别名
        # #151：多候选（单候选已在上面提前返回）必算 ctx——尾部 container_rect 就近依赖它；
        # 即便线索只有 accept（want_* 全空）也要算，否则隐藏 input 区分不了横/竖。
        need_ctx = True
        _acc = (clue.get("accept") or "").lower()
        _kind = "video" if "video" in _acc else ("image" if "image" in _acc else "")
        ctx = await self._upload_input_contexts(candidates, _kind) if need_ctx else {}
        logger.info(
            "upload 线索精筛：%d 候选 %s；want aff=%r label=%r aria=%r region=%r in_dialog=%r；上下文=%s",
            len(cand_ids), cand_ids, want_aff, want_label, want_aria, want_region, want_in_dialog,
            {i: ctx.get(i) for i in cand_ids},
        )

        def _try_narrow(hits: list[int]) -> bool:
            """命中非空且真正收窄时把 candidates 收到 hits；返回收窄后是否唯一。

            全命中（``len(hits)==len(cand_ids)``，未起区分作用）或空命中都不动，留给下一级。
            """
            nonlocal candidates, cand_ids
            if not hits or len(hits) >= len(cand_ids):
                return False
            candidates = [(idx, selector_map[idx]) for idx in hits if idx in selector_map]
            cand_ids = [idx for idx, _ in candidates]
            return len(cand_ids) == 1

        if ctx and want_aff:
            # Layer 2：用户实点 affordance 文本 == 候选可点祖先文案（最精确——用户点的那个可见元素）
            hits = [idx for idx in cand_ids if ctx.get(idx, {}).get("affordance_text", "") == want_aff]
            logger.info("upload 线索精筛：trigger_affordance=%r 命中 %s", want_aff, hits)
            if _try_narrow(hits):
                return cand_ids[0]

        if ctx and (want_label or want_aria or want_region):
            # Layer 1：文本束任一相等（label/aria/region——标准静态身份信号）
            hits = [
                idx for idx in cand_ids
                if (want_label and ctx.get(idx, {}).get("label_text", "") == want_label)
                or (want_aria and ctx.get(idx, {}).get("aria_text", "") == want_aria)
                or (want_region and ctx.get(idx, {}).get("region_text", "") == want_region)
            ]
            logger.info("upload 线索精筛：文本束(label/aria/region) 命中 %s", hits)
            if _try_narrow(hits):
                return cand_ids[0]

        if ctx and want_in_dialog is not None:
            # ARIA dialog 收窄（泛化旧 in_modal 撞车 tiebreak；现作独立级，无文本命中时也能用）
            hits = [
                idx for idx in cand_ids
                if bool(ctx.get(idx, {}).get("in_dialog")) == bool(want_in_dialog)
            ]
            logger.info("upload 线索精筛：in_dialog=%r 命中 %s", want_in_dialog, hits)
            if _try_narrow(hits):
                return cand_ids[0]

        # 可见性优先（隐藏 input 排后；活动面板的 widget 才可见——横/竖面板同屏时区分）
        visible = [(idx, n) for idx, n in candidates
                   if getattr(n, "is_visible", None) is not False]
        logger.info(
            "upload 线索精筛：可见候选（is_visible 非 False）= %s",
            [idx for idx, _ in visible],
        )
        if visible:
            candidates = visible
            cand_ids = [idx for idx, _ in candidates]
        if not candidates:
            return None
        # rect 就近兜底：隐藏 input 自身 rect={0,0,0,0}、候选 snapshot bounds 同样为零时 _nearest_idx
        # 退回 candidates[0]。改用 effective_clue_rect（优先 container_rect/affordance 真实几何）的中心
        # 对候选 ctx 的 container_rect 比距离；ctx 无 container_rect 时退回 _nearest_idx（legacy 行为）。
        want_rect = effective_clue_rect(clue)
        rc = _bounds_center(want_rect)
        chosen: int | None = None
        if rc is not None and ctx:
            best_d = float("inf")
            for idx in cand_ids:
                cc = _bounds_center(ctx.get(idx, {}).get("container_rect"))
                if cc is None:
                    continue
                d = (cc[0] - rc[0]) ** 2 + (cc[1] - rc[1]) ** 2
                if d < best_d:
                    best_d, chosen = d, idx
            if chosen is not None:
                logger.info("upload 线索精筛：container_rect 就近 → idx=%s", chosen)
                return chosen
        chosen = _nearest_idx({"bounds": want_rect}, candidates)
        logger.info("upload 线索精筛：rect 就近(snapshot) → idx=%s", chosen)
        return chosen

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
        params = params_of(new_action)
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
        actions = actions_of(item.model_output)
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
        actions = actions_of(item.model_output)
        max_index = -1
        for action in actions:
            if not isinstance(action, dict):
                continue
            params = params_of(action)
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
        actions = actions_of(item.model_output)
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
        """委托共享 ``actionability.is_rect_stable``（单一事实源，探索/重放共用）。"""
        return await is_rect_stable(self.browser, backend_node_id, interval, tolerance)

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
        """变量名→新值；只改动作参数，不碰 interacted_element/result。两路替换：

        - 手工变量（history.manual_variables，带 step/action/field 位置）：按位置精确写入
          （_apply_manual_variable_at_location）——能区分「同 original_value、不同位置」的变量
          （如录制时标题与简介同文本，title-1 与 title-2 的 original_value 相同；按值替换会撞 key）。
        - 自动检测变量（无位置）：按 original_value 精确整串替换（_substitute_in_dict）。
        变量源 = 自动检测（detect_variables_in_history）∪ 人工标注，见 merge_variable_sources。
        """
        from tree_walker.agent.variable_detector import (
            detect_variables_in_history,
            merge_variable_sources,
        )

        detected = merge_variable_sources(
            detect_variables_in_history(history), history.manual_variables
        )
        manual_by_name = {mv.name: mv for mv in (history.manual_variables or [])}

        modified = copy.deepcopy(history)
        n_applied = 0
        value_replacements: dict[str, str] = {}
        for var_name, new_value in variables.items():
            mv = manual_by_name.get(var_name)
            if mv is not None:
                # 手工变量：按 (step, action, field) 精确写入，不靠 original_value（避免同值撞 key）
                if _apply_manual_variable_at_location(
                    modified.history, mv.step_number, mv.action_index, mv.field, new_value,
                ):
                    n_applied += 1
                else:
                    logger.warning(
                        "变量 %r 的位置 (step=%s action=%s field=%s) 未命中，跳过",
                        var_name, mv.step_number, mv.action_index, mv.field,
                    )
            elif var_name in detected:
                # 自动检测变量：按 original_value 精确整串替换
                value_replacements[detected[var_name].original_value] = new_value
            else:
                logger.warning("变量 %r 在历史中未检测到，跳过", var_name)

        if value_replacements:
            for item in modified.history:
                actions = actions_of(item.model_output)
                for action in actions:
                    if isinstance(action, dict):
                        _substitute_in_dict(params_of(action), value_replacements)
            n_applied += len(value_replacements)
        if n_applied:
            logger.info("🔁 已替换 %d 个变量值", n_applied)
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
