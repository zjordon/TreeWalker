"""Actionability checks shared by exploration (step.py) and replay (rerun.py).

重放端（rerun.py）已落地的 Playwright 式 actionability 抽到这里做单一事实源（对齐 dom-snapshot
抽取原则）。两类内容：

1. **纯函数**（迁自 rerun.py，零 replay 耦合，可直接复用）：
   - ``ACTIONABILITY_ACTIONS``：要检查的动作白名单 {click, input_text, select_dropdown}
   - ``is_file_input``：INPUT[type=file] 防御短路（file input 永远 is_visible=False 但有效）
   - ``is_actionable``：visible + enabled + receives-events（L1 paint_order / L2 pointer-events）
2. **探索侧 index-based 等待**（新增）：``wait_for_actionability`` + ``is_rect_stable``。
   与 rerun 版的 ``_wait_for_actionability``/``_wait_for_stable`` 唯一差别：重定位用 live ``index``
   （``selector_map.get(index)``），不用录制 ``hist_elem`` + ``_locate_target``。

降级原则贯穿：超时 / 拿不到 node / index 漂移 → 照常返回，让调用方照常执行，永不引入新失败。
详见 docs/p3/01-探索可靠性提升方案.md、docs/wait-and-timing/02、05。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

# 仅这 3 个动作做 actionability：有明确 DOM 目标且依赖元素已就绪可交互。
# navigate/search/go_back/done/wait/evaluate 等无具体元素目标或自带等待，不走此路径。
ACTIONABILITY_ACTIONS: frozenset[str] = frozenset({"click", "input_text", "select_dropdown"})


def is_file_input(node: Any) -> bool:
    """防御短路：INPUT[type=file] 永远跳过 actionability（仿 serializer.py:264-271）。

    upload_file 的 file input 因 opacity:0/display:none/1×1 → node.is_visible=False（serializer
    仅对 LLM 简化树强制可见，不改 node.is_visible）。白名单已排除 upload_file，本函数是第二层
    防御——避免未来白名单逻辑变更或 LLM 误把 file input 当 click 目标时被 visible 检查误杀。
    """
    return (
        (getattr(node, "node_name", "") or "").upper() == "INPUT"
        and (getattr(node, "attributes", None) or {}).get("type", "").lower() == "file"
    )


def is_actionable(node: Any, *, check_receives_events: bool = False) -> bool:
    """visible + enabled（+ receives-events）判定。None 字段保守放过（不引入新失败）。

    - visible：``is_visible`` 为 ``bool|None``。``False``=明确不可见→阻断；``None``=未知→放过。
    - receives-events（``check_receives_events=True`` 时启用）：L2 ``pointer-events:none`` →
      不接收指针事件→阻断；L1 paint_order 判定被完全覆盖（``ignored_by_paint_order``）→阻断。
      两者都读快照静态数据，零额外 CDP 开销；``snapshot_node`` 缺失时保守放过。
    - enabled：AX ``ax_node.properties`` 里 ``name=='disabled'`` 且真值，HTML ``attributes`` 含
      ``disabled``，或 ``aria-disabled="true"``。

    ``check_receives_events`` 默认 ``False`` → 行为与阶段一一字不差（零回归基线）。
    运行时遮挡（L3，``elementFromPoint``）是 async，不在此同步函数——见 ``wait_for_actionability``。
    """
    if getattr(node, "is_visible", None) is False:
        return False
    # ── receives-events（L2 pointer-events + L1 paint_order 静态遮挡）──
    if check_receives_events:
        snap = getattr(node, "snapshot_node", None)
        if snap is not None:
            pe = (getattr(snap, "computed_styles", None) or {}).get("pointer-events", "")
            if str(pe).lower() == "none":
                return False
        if getattr(node, "ignored_by_paint_order", False):
            return False
    # ── enabled（AX disabled + HTML disabled + aria-disabled）──
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


async def is_rect_stable(
    browser: Any, backend_node_id: int, interval: float = 0.1, tolerance: float = 1.0,
) -> bool:
    """两次取 rect 比（~interval 间隔），位置/尺寸变化 ≤ tolerance 视为稳定。

    复用 ``browser.get_element_coordinates`` 三级 fallback（getContentQuads → getBoxModel →
    JS getBoundingClientRect），零新 CDP 封装。拿不到坐标（None）→ 视为不稳定（保守）。
    """
    r1 = await browser.get_element_coordinates(backend_node_id)
    if r1 is None:
        return False
    await asyncio.sleep(interval)
    r2 = await browser.get_element_coordinates(backend_node_id)
    if r2 is None:
        return False
    return (
        abs(r1.x - r2.x) <= tolerance and abs(r1.y - r2.y) <= tolerance
        and abs(r1.width - r2.width) <= tolerance
        and abs(r1.height - r2.height) <= tolerance
    )


async def wait_for_actionability(
    browser: Any,
    state: Any,
    index: int,
    *,
    timeout: float,
    poll: float,
    receives_events: bool = False,
    runtime_occlusion: bool = False,
    stable: bool = False,
    stable_interval: float = 0.1,
    stable_tolerance: float = 1.0,
) -> tuple[Any, Any | None]:
    """探索侧 index-based actionability 等待（deadline + poll + 降级，不抛错）。

    每轮用 ``state.dom_state.selector_map.get(index)`` 拿最新 node（poll 后 index 可能漂移
    → None → 降级），依次查：
      (1) ``is_actionable``：visible + enabled + L1/L2 receives-events（同步，零开销静态）；
      (2) L3 ``browser._is_element_occluded``：``elementFromPoint`` 运行时遮挡（async，
          ``runtime_occlusion=True`` 时才查）；
      (3) ``is_rect_stable``：动画/重排中元素（``stable=True`` 时才查）。
    命中即返；超时降级返最新 ``(state, node)``，让调用方（tools.execute）照常执行 → 不引入新失败。

    与 rerun ``_wait_for_actionability`` 的唯一差别：重定位用 live ``index``，不用录制 ``hist_elem``。
    """
    fresh_node = (
        state.dom_state.selector_map.get(index) if state and state.dom_state else None
    )
    deadline = time.time() + timeout
    while True:
        sm = state.dom_state.selector_map if state and state.dom_state else {}
        fresh_node = sm.get(index)
        if fresh_node is not None:
            actionable = is_actionable(fresh_node, check_receives_events=receives_events)
            if actionable and runtime_occlusion:
                # L3：运行时 elementFromPoint 遮挡判定（async，独立开关，默认关）
                occluded = await browser._is_element_occluded(
                    fresh_node.backend_node_id, fresh_node.x, fresh_node.y,
                )
                actionable = not occluded
            if actionable and stable:
                actionable = await is_rect_stable(
                    browser, fresh_node.backend_node_id, stable_interval, stable_tolerance,
                )
            if actionable:
                return state, fresh_node
        if time.time() >= deadline:
            return state, fresh_node  # 超时降级，不抛错
        await asyncio.sleep(poll)
        try:
            state = await browser.get_state(include_screenshot=False)
        except Exception:
            pass
