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
    """同一匹配级有多个候选时，按「录制 bounds 中心就近」选一个；无 bounds 退回第一个。

    消除哈希碰撞（如多个相似下拉触发器同 ``element_hash``）时「按迭代顺序取到错误元素」
    的问题——多个同款元素里，离录制时位置最近的那个最可能是正确目标。
    """
    if len(candidates) == 1:
        return candidates[0][0]
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
        delay_between_actions: float = 2.0,
        max_step_interval: float = 45.0,
        wait_for_elements: bool = False,
        summary_llm: LLMClient | None = None,
        ai_step_llm: LLMClient | None = None,
    ) -> list[ActionResult]:
        """重放历史。返回 ``list[ActionResult]``，最后一项是 AI 摘要（``is_done=True``）。

        重放不调决策 LLM（``get_action`` 次数为 0）；``extract`` 动作走 ``tools._extract_llm``
        在当前页重算；结尾 1 次 LLM 调用生成摘要。
        """
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
                delay_src = (
                    f"saved step_interval={item.metadata.step_interval:.1f}s"
                    if item.metadata and item.metadata.step_interval is not None
                    else f"default delay={delay_between_actions}s"
                )
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
                    item, step_delay, max_retries, previous_item, ai_step_llm, wait_for_elements
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
        # step_interval 存的是【上一步】耗时（含 LLM 时间），必须封顶
        if item.metadata and item.metadata.step_interval is not None:
            return min(item.metadata.step_interval, max_step_interval)
        return delay_between_actions

    async def _rerun_step_with_retries(
        self,
        item: AgentHistory,
        delay: float,
        max_retries: int,
        previous_item: AgentHistory | None,
        ai_step_llm: LLMClient | None,
        wait_for_elements: bool,
    ) -> list[ActionResult]:
        attempt = 0
        menu_reopens = 0
        cur_delay = delay
        while True:
            try:
                return await self._execute_history_step(
                    item, cur_delay, ai_step_llm, wait_for_elements
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
                    if await self._reexecute_menu_opener(previous_item, ai_step_llm):
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
        return None

    async def _execute_history_step(
        self,
        item: AgentHistory,
        delay: float,
        ai_step_llm: LLMClient | None,
        wait_for_elements: bool,
    ) -> list[ActionResult]:
        """执行单步：取当前 selector_map → 逐动作重定位/重算并执行，带 guard。"""
        await asyncio.sleep(delay)
        state = await self.browser.get_state(include_screenshot=False)

        if wait_for_elements:
            min_elements = self._count_expected_elements(item)
            if min_elements > 0:
                state = await self._wait_for_minimum_elements(state, min_elements, timeout=15.0)

        selector_map = state.dom_state.selector_map if state and state.dom_state else {}
        actions = item.model_output.get("actions") or [item.model_output.get("action", {})]
        interacted = item.interacted_element or []

        results: list[ActionResult] = []
        for i, action in enumerate(actions):
            if not isinstance(action, dict):
                continue
            name = action.get("name", "done")

            if name == "extract":
                # extract 自带 LLM 且读当前页（tools/_action_extract）→ 直接 re-execute 即在当前页重算
                params = dict(action.get("params", {}))
            else:
                hist_elem = interacted[i] if i < len(interacted) else None
                raw_params = action.get("params") if isinstance(action.get("params"), dict) else {}
                has_index = raw_params.get("index") is not None or raw_params.get("element_id") is not None
                if hist_elem and has_index:
                    updated = self._update_action_indices(hist_elem, action, selector_map)
                    if updated is None:
                        raise ValueError(self._format_match_failure(hist_elem, i, selector_map))
                    name = updated.get("name", name)
                    params = dict(updated.get("params", {}))
                else:
                    params = dict(action.get("params", {}))

            logger.info("▶️  %s: %s", name, _format_action_params(params))
            result = await self._exec_one(name, params, state)
            results.append(result)

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
        return None

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
            f"Tried: EXACT -> STABLE -> XPATH -> AX_NAME -> ATTRIBUTE"
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
        self, opener_item: AgentHistory, ai_step_llm: LLMClient | None
    ) -> bool:
        logger.info("🔁 菜单重打开：重执行上一步（opener）以重新展开下拉，随后立即重试")
        try:
            await self._execute_history_step(
                opener_item, delay=0.5, ai_step_llm=ai_step_llm, wait_for_elements=False
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

    async def _wait_for_minimum_elements(
        self, state: Any, min_elements: int, timeout: float = 15.0, poll: float = 1.0
    ) -> Any:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if state and state.dom_state and len(state.dom_state.selector_map) >= min_elements:
                return state
            await asyncio.sleep(poll)
            try:
                state = await self.browser.get_state(include_screenshot=False)
            except Exception:
                pass
        return state

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
