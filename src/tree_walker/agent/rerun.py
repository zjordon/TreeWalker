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

    # ── 公共 API ───────────────────────────────────────────────────────

    def save_history(self, file_path: str | Path | None = None) -> None:
        """把本次运行的历史落盘（含敏感数据脱敏 + 注册表版本号）。"""
        if not file_path:
            file_path = "AgentHistory.json"
        # _sensitive_map 是 {real_val: placeholder}；脱敏需要 {placeholder: real_val}
        sensitive = None
        if self._sensitive_map:
            sensitive = {p: r for r, p in self._sensitive_map.items()}
        self.history.save_to_file(
            file_path,
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
        """读历史文件 → 可选变量替换 → 重放。"""
        history = AgentHistoryList.load_from_file(history_file)
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

        try:
            for item in history.history:
                if self.state.stopped:
                    break
                step_delay = self._compute_step_delay(
                    item, delay_between_actions, max_step_interval
                )
                if self._should_skip_step(
                    item, previous_item, previous_succeeded, skip_failures
                ):
                    previous_item, previous_succeeded = item, False
                    continue

                self.state.n_steps += 1
                step_results = await self._rerun_step_with_retries(
                    item, step_delay, max_retries, previous_item, ai_step_llm, wait_for_elements
                )
                results.extend(step_results)
                previous_succeeded = bool(step_results) and not any(
                    r.error for r in step_results
                )
                previous_item = item

                if any(r.is_done for r in step_results):
                    break
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
                # 菜单重打开：找不到元素 + 上一步是菜单 opener → 重打开后重试，不消耗 attempt
                if (
                    menu_reopens < 3
                    and "Could not find matching element" in err_str
                    and self._is_menu_opener_step(previous_item)
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

    def _should_skip_step(
        self,
        item: AgentHistory,
        previous_item: AgentHistory | None,
        previous_succeeded: bool,
        skip_failures: bool,
    ) -> bool:
        actions = item.model_output.get("actions") or [item.model_output.get("action", {})]
        # 无动作跳过
        if not actions or all(not (a and a.get("name")) for a in actions if isinstance(a, dict)):
            logger.warning("步骤 %d 无动作，跳过", item.step_number)
            return True
        # 原始出错跳过
        if skip_failures and item.result and all(r.error for r in item.result):
            logger.info("步骤 %d 原始运行即出错（skip_failures），跳过", item.step_number)
            return True
        # 冗余重试跳过
        if self._is_redundant_retry_step(item, previous_item, previous_succeeded):
            logger.info("步骤 %d 是冗余重试（同元素同动作且上步已成功），跳过", item.step_number)
            return True
        return False

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
                result = await self._exec_one("extract", dict(action.get("params", {})), state)
            else:
                hist_elem = interacted[i] if i < len(interacted) else None
                params = action.get("params") if isinstance(action.get("params"), dict) else {}
                has_index = params.get("index") is not None or params.get("element_id") is not None
                if hist_elem and has_index:
                    updated = self._update_action_indices(hist_elem, action, selector_map)
                    if updated is None:
                        raise ValueError(
                            self._format_match_failure(hist_elem, i, selector_map)
                        )
                    result = await self._exec_one(
                        updated.get("name", name), dict(updated.get("params", {})), state
                    )
                else:
                    result = await self._exec_one(name, dict(action.get("params", {})), state)

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
        """在当前 selector_map 里找回同款元素。返回 (index, level) 或 None。"""
        # Level 1: EXACT（TreeWalker 确定性 sha256，跨会话稳定）
        h_exact = hist.get("element_hash")
        if h_exact is not None:
            for idx, elem in selector_map.items():
                if elem.element_hash == h_exact:
                    return idx, MatchLevel.EXACT
        # Level 2: STABLE（重放首选）
        h_stable = hist.get("stable_hash")
        if h_stable is not None:
            for idx, elem in selector_map.items():
                if elem.compute_stable_hash() == h_stable:
                    return idx, MatchLevel.STABLE
        # Level 3: XPATH
        h_xpath = hist.get("x_path")
        if h_xpath:
            for idx, elem in selector_map.items():
                if elem.xpath == h_xpath:
                    return idx, MatchLevel.XPATH
        # Level 4: AX_NAME
        h_ax = hist.get("ax_name")
        h_name = (hist.get("node_name") or "").lower()
        if h_ax:
            for idx, elem in selector_map.items():
                elem_ax = (
                    elem.ax_node.name
                    if elem.ax_node and getattr(elem.ax_node, "name", None)
                    else None
                )
                if elem.node_name.lower() == h_name and elem_ax == h_ax:
                    return idx, MatchLevel.AX_NAME
        # Level 5: ATTRIBUTE
        h_attrs = hist.get("attributes") or {}
        if h_attrs:
            for attr_key in ("name", "id", "aria-label"):
                target = h_attrs.get(attr_key)
                if target:
                    for idx, elem in selector_map.items():
                        if (
                            elem.node_name.lower() == h_name
                            and (elem.attributes or {}).get(attr_key) == target
                        ):
                            return idx, MatchLevel.ATTRIBUTE
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
        return (
            f"Could not find matching element for action {action_index} in current page. "
            f"Looking for: <{name}> {attr_str} hash={hist_elem.get('element_hash')} "
            f"xpath={hist_elem.get('x_path')}. "
            f"Page has {len(selector_map)} interactive elements. "
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
        if not item or not item.interacted_element:
            return False
        elem = item.interacted_element[0]
        if not elem:
            return False
        attrs = elem.get("attributes") or {}
        if attrs.get("aria-haspopup") in ("true", "menu", "listbox"):
            return True
        if "expand-button" in (attrs.get("class") or ""):
            return True
        if attrs.get("role") == "button" and attrs.get("aria-expanded") in ("false", "true"):
            return True
        return False

    async def _reexecute_menu_opener(
        self, opener_item: AgentHistory, ai_step_llm: LLMClient | None
    ) -> bool:
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
