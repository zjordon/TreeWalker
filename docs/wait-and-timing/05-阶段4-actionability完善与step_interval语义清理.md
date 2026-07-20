# 05-阶段4：actionability 完善（receives-events + stable）+ step_interval 语义清理

> **关联**：#127（本期实现 issue）｜ #123（等待机制完善总览）｜ 前置 #124（阶段1）/ #125（阶段2，已落地）/ #126（阶段3，已落地）｜ **收尾阶段**
> **状态**：待实施
> **体裁**：带代码的实施计划（基于现状代码，含"现状 → 改后"代码块、逐文件改动清单、测试与验证）
> **对照源**：已逐行核验真实代码（2026-07-19，master @ `ab8c37c`）
> **范围**：actionability 阶段二（receives-events：`pointer-events:none` + 被覆盖）+ actionability 阶段三（stable，可选/默认关/优先级最低）+ 缺口 7（`step_interval` 语义陷阱，改法 ② 新增 `user_pause_seconds`）+ 阶段 2 遗留收编（aria-disabled + `locator.py:141` 死代码）。四块正交、可独立开关、可分拆合并。
> **总原则**：复用阶段 1/2 已就绪的 `_wait_until` 轮询骨架 + `_locate_target` 只读谓词 + `_is_element_occluded`/`get_element_coordinates` 既有 CDP 原语；新检查项默认关 → 零行为变更；超时永远降级，永不引入新失败；缺口 7 走"新增字段"路线 → agent 自录路径零回归、旧 JSON 向后兼容。

---

## Context（为什么做）

阶段 2 让重放获得了"等目标元素 + visible/enabled actionability（阶段一）"，阶段 3 补齐了 networkidle + 清理了 upload 硬编码 wait。本期是 #123 的**收尾阶段**，收掉三类残留：

1. **actionability 阶段二 — receives-events 检查缺失**：阶段一的 `_is_actionable`（`agent/rerun.py:71-86`）只查 `is_visible` + `disabled`，**不查元素是否真正能接收指针事件**。两类漏网：
   - `pointer-events: none`（CSS 显式禁用命中）：元素可见、enabled，但点击穿透。`dom.py:48` 早已把 `pointer-events` 列入 `REQUIRED_COMPUTED_STYLES` 抓取，主 visibility 函数（`dom.py:737`）读的也是同一个 `computed_styles` dict，但 actionability 路径**没用上**。
   - 被覆盖（paint order / 运行时遮挡）：目标元素的命中点被更高 z-index 的元素盖住。`paint_order.py` 已算出 `ignored_by_paint_order` 标志（静态几何），`session.py:2079-2124` `_is_element_occluded` 已实现 `elementFromPoint` 运行时判定（服务于 `click_element`，**未接入 actionability**）。

2. **actionability 阶段三 — stable 检查缺失（可选，优先级最低）**：动画中 / 正在重排的元素，两次取 rect 位置漂移，点击会落空。Playwright actionability 五级里有 stable 一级。但本项目 modal 多为瞬时弹出（阶段 3 文档已述），stable 收益小，**本期写完整方案但默认关**。

3. **缺口 7 — `step_interval` 语义陷阱**：`StepMetadata.step_interval`（`agent/views.py:84-98`）在两条填充路径下语义截然不同——agent 自录路径（`agent/step.py:1103-1115` `_build_step_metadata`）存的是"上一步耗时（**含 LLM 决策时间**）"，recorder 路径（`recorder/flatten.py:55-63`）存的是"相邻 action 的 timestamp 差（**纯人类停顿**）"。下游 `_compute_step_delay`（`agent/rerun.py:421-427`）一视同仁地 `min(step_interval, max_step_interval)`——封顶是为 agent 自录路径防 LLM 空等设计的，对 recorder 路径反而**截断真实节奏**。阶段 1 文档"边界与风险"已明示此迁移留给阶段 4。

4. **阶段 2 遗留收编**：阶段 2 文档"边界与风险"明确遗留两项——`_is_actionable` 漏判 `aria-disabled="true"`；`recorder/locator.py:140-141` 有 unreachable 死代码。本期一并清掉。

参考 `knowledge-garden/ai/agent/browser-wait-and-timing.md`（**注：位于 sibling 仓库 `D:\dev\git\z_jordon\knowledge-garden\`，非 TreeWalker 内**）：§1 Playwright actionability 五级 = visible / stable / enabled / receives events / precise location，命中即停逐级判定——阶段一已覆盖 visible+enabled，本期补 receives events（阶段二）+ stable（阶段三）。§7 stable / modal：本项目 modal 多瞬时弹出，stable 优先级低，按需开。

> ⚠️ 参考文档**未提** `pointer-events` computed-style 读法、`document.elementFromPoint` 祖先链遍历、`DOM.getBoxModel` 两次取 rect 比对的具体实现——本期引用范围以 §1（五级语义）/ §7（stable 定位）为准，不外延。具体 CDP 原语以 TreeWalker 既有实现（`_is_element_occluded` / `get_element_coordinates` / `paint_order.py`）为准。

---

## 现状精确锚点（已核验真实代码 @ `ab8c37c`）

### 锚点 A：actionability 阶段一框架（已就绪，阶段二/三在其上扩展）

**A-1 `_is_actionable` 纯函数**（`agent/rerun.py:71-86`）——阶段二/遗留的**主改动点**：

```python
def _is_actionable(node: Any) -> bool:
    """visible + enabled 判定。None 字段保守放过（不引入新失败）。"""
    if getattr(node, "is_visible", None) is False:
        return False
    ax = getattr(node, "ax_node", None)
    if ax is not None:
        for prop in (ax.properties or []):
            if getattr(prop, "name", "") == "disabled" and prop.value:
                return False
    if "disabled" in (getattr(node, "attributes", None) or {}):
        return False
    return True
```

当前只查三类：`is_visible!=False` + AX `disabled` property + HTML `disabled` attr。**漏**：`pointer-events:none`、被覆盖（paint order / 运行时）、`aria-disabled="true"`。

**A-2 编排注入点**（`agent/rerun.py:606-631`）——三层门 + 乐观短路：

```python
if (
    rerun_actionability_check                          # 总开关（默认关）
    and name in _ACTIONABILITY_ACTIONS                 # 白名单 click/input_text/select_dropdown
    and hist_elem
):
    node_now = selector_map.get(params.get("index"))
    if (
        node_now is not None
        and not _is_file_input(node_now)               # 第二层防御
        and not _is_actionable(node_now)               # 乐观短路：首帧不通过才等
    ):
        state, fresh_idx, _ = await self._wait_for_actionability(
            state, hist_elem, params["index"],
            timeout=self.rerun_actionability_timeout,
            poll=self.rerun_actionability_poll,
        )
        if fresh_idx is not None and fresh_idx != params.get("index"):
            params["index"] = fresh_idx                # poll 期间 index 漂移写回
```

阶段二/三**不动编排层结构**，只让 `_is_actionable`（L1/L2）和 `_wait_for_actionability`（L3/stable）的语义更严格。

**A-3 `_wait_until` 通用骨架**（`agent/rerun.py:1035-1055`）——**predicate 是同步函数**，这是阶段二 L3 / 阶段三 stable 的关键约束：

```python
async def _wait_until(self, state, predicate, timeout, poll=1.0, refresh=True):
    deadline = time.time() + timeout
    while True:
        if predicate(state):           # ← 同步调用，不能 await
            return state
        if time.time() >= deadline:
            return state               # ← 超时降级，不抛错
        await asyncio.sleep(poll)
        if refresh:
            try:
                state = await self.browser.get_state(include_screenshot=False)
            except Exception:
                pass
```

→ L3（`_is_element_occluded` 是 async）和 stable（`get_element_coordinates` 是 async）**不能直接塞进 `predicate`**，需在 `_wait_for_actionability` / `_wait_for_stable` 自定义循环里内联 `await`。

**A-4 `_wait_for_actionability`**（`agent/rerun.py:1122-1151`）——poll 期间用 `hist_elem` 重解析漂移 index：

```python
async def _wait_for_actionability(self, state, hist_elem, initial_idx, timeout, poll):
    fresh_idx = initial_idx
    fresh_node = state.dom_state.selector_map.get(initial_idx) if state and state.dom_state else None

    def _actionable_now(s):                          # ← 同步闭包，写 nonlocal
        nonlocal fresh_idx, fresh_node
        sm = s.dom_state.selector_map if s and s.dom_state else {}
        located = self._locate_target(hist_elem, sm) # 重解析漂移 index
        if located is None:
            return False
        fresh_idx, fresh_node = located
        return _is_actionable(fresh_node)

    state = await self._wait_until(state, _actionable_now, timeout, poll)
    return state, fresh_idx, fresh_node
```

L3 运行时遮挡需在此函数内**绕开 `_wait_until`**，自己写 deadline 循环（见方案 B-3）。

### 锚点 B：receives-events 三层判定可复用的底层原语

**B-1 L2 pointer-events（零开销，数据已抓取）**：

`dom.py:43-54` `REQUIRED_COMPUTED_STYLES` 已含 `'pointer-events'`：

```python
REQUIRED_COMPUTED_STYLES = [
    'display', 'visibility', 'opacity', 'cursor',
    'pointer-events',          # ← 阶段二需要的字段早在抓取清单
    'overflow', 'overflow-x', 'overflow-y', 'position', 'background-color',
]
```

存于 `EnhancedSnapshotNode.computed_styles`（`browser/views.py:251`，`dict[str,str]|None`）。主 visibility 函数 `dom.py:737` 的读法就是范本：

```python
styles = enode.snapshot_node.computed_styles or {}     # dom.py:737
```

→ 阶段二 L2 读法：`node.snapshot_node.computed_styles.get('pointer-events','').lower() == 'none'`，与主 visibility 同源，零额外 CDP 开销。

**B-2 L1 静态遮挡（paint order，零开销但需字段映射）**：

`paint_order.py:178-179` 设置标志：

```python
if rect_union.contains(rect):
    node.ignored_by_paint_order = True      # ← node 是 SimplifiedNode
```

**关键约束**：`ignored_by_paint_order` 字段定义在 `SimplifiedNode`（`browser/views.py:637`），**不在 `EnhancedDOMTreeNode` 上**。而 selector_map 的 value 是 `EnhancedDOMTreeNode`——`serializer.py:773` 存的是 `node.original_node`：

```python
# serializer.py:770-773
if should_make_interactive:
    node.is_interactive = True
    node.highlight_index = node.original_node.backend_node_id
    self._selector_map[node.highlight_index] = node.original_node   # ← EnhancedDOMTreeNode
```

→ `_is_actionable(node_now)` 里的 `node_now` 是 `EnhancedDOMTreeNode`，**取不到 `ignored_by_paint_order`**。阶段二必须先把该标志映射到 `EnhancedDOMTreeNode`（见方案 A）。

**B-3 L3 运行时遮挡（elementFromPoint，async + CDP 开销）**：

`session.py:2079-2124` `_is_element_occluded(backend_node_id, x, y)` 已实现（best-effort，JS/CDP 异常返 `False`=不遮挡）：

```python
async def _is_element_occluded(self, backend_node_id, x, y):
    try:
        resolve = await self.client.send.DOM.resolveNode(
            {"backendNodeId": backend_node_id}, session_id=self.current_session_id)
        object_id = resolve["object"]["objectId"]
        result = await self.client.send.Runtime.callFunctionOn({
            "objectId": object_id,
            "functionDeclaration": """
            function(x, y) {
                var hit = document.elementFromPoint(x, y);
                if (!hit) return true;
                var cur = hit;
                while (cur) {
                    if (cur === this) return false;   // 命中目标或其祖先 → 未遮挡
                    cur = cur.parentElement;
                }
                return true;                          // 命中无关元素 → 被遮挡
            }""",
            "arguments": [{"value": x}, {"value": y}],
            "returnByValue": True,
        }, session_id=self.current_session_id)
        return bool(result.get("result", {}).get("value"))
    except Exception as e:
        logger.debug("_is_element_occluded failed (treating as not occluded): %s", e)
        return False
```

中心坐标取自 `EnhancedDOMTreeNode.x` / `.y` 属性（`browser/views.py:322-338`，来自 `snapshot_node.bounds`），`backend_node_id` 取自 `browser/views.py:265`。→ L3 调用形态：`await self.browser._is_element_occluded(node.backend_node_id, node.x, node.y)`，原语现成，只需编排接入。

### 锚点 C：stable 检查（阶段三）可复用原语

`session.py:1892-1972` `get_element_coordinates(backend_node_id)` 三级 fallback（`getContentQuads` → `getBoxModel` → JS `getBoundingClientRect`），返回 `DOMRect`（`browser/views.py:202`，`x/y/width/height`）：

```python
async def get_element_coordinates(self, backend_node_id, viewport=None) -> DOMRect | None:
    # Method 1: DOM.getContentQuads（取与视口交集最大的 quad 外接矩形）
    # Method 2: DOM.getBoxModel（content 8 坐标 → 外接矩形）
    # Method 3: DOM.resolveNode + Runtime.callFunctionOn(getBoundingClientRect)
    ...
```

→ stable 检查**零新 CDP 封装**：两次调 `get_element_coordinates` + `asyncio.sleep(interval)` + rect 差 ≤ tolerance。

### 锚点 D：`step_interval` 现状（两条路径语义不一致，缺口 7）

**D-1 定义**（`agent/views.py:84-98`）——docstring 明写"含 LLM 时间"：

```python
class StepMetadata(BaseModel):
    """单步计时信息（重放步间延迟用）。

    ⚠️ ``step_interval`` 存的是【上一步】的耗时（含 LLM 时间），不是当前步耗时。
    重放时必须用 ``max_step_interval`` 封顶，否则会把首次运行的 LLM 等待时间再空等一遍。
    """
    step_start_time: float
    step_end_time: float
    step_number: int
    step_interval: float | None = None

    @property
    def duration_seconds(self) -> float:
        return self.step_end_time - self.step_start_time
```

**D-2 agent 自录填充**（`agent/step.py:1103-1115`）——`step_interval = prev.duration_seconds`，**含 LLM 决策时间**：

```python
def _build_step_metadata(self, step_end_time: float) -> StepMetadata:
    prev = self.history.history[-1] if self.history.history else None
    step_interval = prev.metadata.duration_seconds if prev and prev.metadata else None
    return StepMetadata(
        step_start_time=self._step_start_time,
        step_end_time=step_end_time,
        step_number=self.state.n_steps,
        step_interval=step_interval,        # ← 上一步耗时（含 LLM）
    )
```

**D-3 recorder 填充**（`recorder/flatten.py:55-63`）——`step_interval = timestamp 差`，**纯人类停顿**，注释自承语义分叉：

```python
metadata=StepMetadata(
    step_start_time=action.timestamp,
    step_end_time=action.timestamp,
    step_number=step_number,
    # 录制回放专用：step_interval = 相邻 action 的 timestamp 差（人类操作间隔近似）。
    # 不能用 prev.metadata.duration_seconds——flatten 里 start==end==timestamp，恒 0。
    step_interval=action.timestamp - prev_ts if prev_ts is not None else None,
),
```

**D-4 唯一下游消费**（`agent/rerun.py:421-427` + 日志 `:370-374`）——两条路径合流，一视同仁封顶：

```python
def _compute_step_delay(self, item, delay_between_actions, max_step_interval):
    # step_interval 存的是【上一步】耗时（含 LLM 时间），必须封顶
    if item.metadata and item.metadata.step_interval is not None:
        return min(item.metadata.step_interval, max_step_interval)
    return delay_between_actions
```

`max_step_interval` 默认 `5.0`（`config.py:125`），**为 agent 自录路径防 LLM 空等设计**；recorder 路径本就是真实停顿，封顶反而截断。两条路径在重放端完全合流（都进 `rerun_history`），差别只在 `step_interval` 谁填、语义是什么。

### 锚点 E：遗留项现状

**E-1 aria-disabled 漏判**：`_is_actionable`（`agent/rerun.py:84-85`）只查 HTML `disabled` attr，**漏 `aria-disabled="true"`**（ARIA 规范的禁用态，很多 SPA 组件库用它而非原生 `disabled`）。

**E-2 locator 死代码**（`recorder/locator.py:139-141`）：

```python
	# Level 3: RECT（位置兜底）
	return _locate_by_rect(ref, selector_map)   # L140 — 实际返回点
	return None                                  # L141 — unreachable，永远执行不到
```

L140 已 `return`，L141 是纯死代码。阶段 2 文档"边界与风险"记 TODO 留给本期。

---

## 方案

### 共用基础设施：`ignored_by_paint_order` 字段映射到 `EnhancedDOMTreeNode`

让 selector_map 里的 `EnhancedDOMTreeNode` 能直接查静态遮挡（L1 的前提）。

**改 `browser/views.py:260`** —— `EnhancedDOMTreeNode` 加字段（默认 `False`，向后兼容）：

```python
@dataclass
class EnhancedDOMTreeNode:
    """Enhanced DOM tree node combining data from DOM, AX, and Snapshot trees."""
    # DOM node data
    node_id: int
    backend_node_id: int
    ...
    is_visible: bool | None = None
    absolute_position: DOMRect | None = None
    ignored_by_paint_order: bool = False        # ← 新增（阶段4）：paint_order 回填
    ...
```

**改 `paint_order.py:178-179`** —— 设置 SimplifiedNode 标志时同步回填 `original_node`：

```python
if rect_union.contains(rect):
    node.ignored_by_paint_order = True
    node.original_node.ignored_by_paint_order = True   # ← 新增：回填到 EnhancedDOMTreeNode
```

> **时机前提**：`PaintOrderRemover` 遍历的是序列化树（SimplifiedNode），其 `original_node` 就是后进入 selector_map 的同一个 `EnhancedDOMTreeNode` 引用（`serializer.py:773`）。paint_order 在 build_dom_state 之后、序列化期间运行，此时 `original_node` 引用已稳定，回填安全。

### actionability 阶段二：receives-events

**B-1 L2 pointer-events（加进 `_is_actionable`，零开销）**——在 `is_visible` 检查后加分支：

```python
def _is_actionable(node: Any, *, check_receives_events: bool = False) -> bool:
    if getattr(node, "is_visible", None) is False:
        return False
    # ── 阶段二 L2：pointer-events:none → 不接收指针事件 ──
    if check_receives_events:
        snap = getattr(node, "snapshot_node", None)
        if snap is not None:
            pe = (snap.computed_styles or {}).get("pointer-events", "")
            if str(pe).lower() == "none":
                return False
        # ── 阶段二 L1：paint order 判定被完全覆盖 ──
        if getattr(node, "ignored_by_paint_order", False):
            return False
    # ── enabled（阶段一 + 遗留 aria-disabled）──
    ax = getattr(node, "ax_node", None)
    if ax is not None:
        for prop in (ax.properties or []):
            if getattr(prop, "name", "") == "disabled" and prop.value:
                return False
    attrs = getattr(node, "attributes", None) or {}
    if "disabled" in attrs:
        return False
    if str(attrs.get("aria-disabled", "")).lower() == "true":   # ← 遗留收编
        return False
    return True
```

> `check_receives_events` 形参默认 `False` → **零行为变更**（不传则与阶段一一字不差）。编排层和 `_wait_for_actionability` 在 `rerun_actionability_receives_events` 开关开时传 `True`。

**B-2 编排层传参**（`agent/rerun.py:619` 和 `_wait_for_actionability` 内）——把开关透传到 `_is_actionable`：

```python
# rerun.py:615-620（注入点）
node_now = selector_map.get(params.get("index"))
if (
    node_now is not None
    and not _is_file_input(node_now)
    and not _is_actionable(node_now, check_receives_events=self.rerun_actionability_receives_events)
):
    ...
```

`_wait_for_actionability` 的 `_actionable_now` 闭包同理传 `check_receives_events`（通过外层捕获 `self.rerun_actionability_receives_events`）。

**B-3 L3 运行时遮挡（async，独立开关，默认关）**——因 `_wait_until` predicate 同步，L3 在 `_wait_for_actionability` 内**自定义 deadline 循环**（不破坏既有同步谓词路径）：

```python
async def _wait_for_actionability(self, state, hist_elem, initial_idx, timeout, poll,
                                  runtime_occlusion: bool = False):
    fresh_idx, fresh_node = initial_idx, (
        state.dom_state.selector_map.get(initial_idx) if state and state.dom_state else None)
    deadline = time.time() + timeout
    while True:
        sm = state.dom_state.selector_map if state and state.dom_state else {}
        located = self._locate_target(hist_elem, sm)
        if located is not None:
            fresh_idx, fresh_node = located
            actionable = _is_actionable(
                fresh_node, check_receives_events=self.rerun_actionability_receives_events)
            if actionable and runtime_occlusion and fresh_node is not None:
                # L3：运行时 elementFromPoint 遮挡判定（async，独立开关）
                occluded = await self.browser._is_element_occluded(
                    fresh_node.backend_node_id, fresh_node.x, fresh_node.y)
                actionable = not occluded
            if actionable:
                return state, fresh_idx, fresh_node
        if time.time() >= deadline:
            return state, fresh_idx, fresh_node          # 超时降级
        await asyncio.sleep(poll)
        try:
            state = await self.browser.get_state(include_screenshot=False)
        except Exception:
            pass
```

> **注意**：此重构把 `_wait_for_actionability` 从"借 `_wait_until` + 同步闭包"改为自带 deadline 循环——因为 L3 必须 `await`。L1/L2（同步）仍走 `_is_actionable`，L3（async）在其后追加。重构后阶段一既有行为（visible+enabled 等待 + index 漂移重解析 + 超时降级）完全保留，单测应全过。

### actionability 阶段三：stable（可选 / 默认关 / 优先级最低）

**C-1 新增 `_is_rect_stable`**（async，两次取 rect 比）：

```python
async def _is_rect_stable(self, backend_node_id: int,
                          interval: float = 0.1, tolerance: float = 1.0) -> bool:
    """两次取 rect 比（~interval 间隔），位置/尺寸变化 ≤ tolerance 视为稳定。

    复用 ``session.get_element_coordinates`` 三级 fallback，零新 CDP 封装。
    拿不到坐标（None）→ 视为不稳定（保守）。
    """
    r1 = await self.browser.get_element_coordinates(backend_node_id)
    if r1 is None:
        return False
    await asyncio.sleep(interval)
    r2 = await self.browser.get_element_coordinates(backend_node_id)
    if r2 is None:
        return False
    return (abs(r1.x - r2.x) <= tolerance and abs(r1.y - r2.y) <= tolerance
            and abs(r1.width - r2.width) <= tolerance
            and abs(r1.height - r2.height) <= tolerance)
```

**C-2 新增 `_wait_for_stable`**（仿 `_wait_for_actionability` 的 deadline 循环）：

```python
async def _wait_for_stable(self, state, hist_elem, initial_idx, timeout, poll,
                           interval, tolerance):
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
            return state, fresh_idx                       # 超时降级
        await asyncio.sleep(poll)
        try:
            state = await self.browser.get_state(include_screenshot=False)
        except Exception:
            pass
```

**C-3 编排接入**（`agent/rerun.py:606-631` 的 actionability 块**之后**追加 stable 块，受同款白名单 + 总开关 + stable 开关保护）：

```python
# actionability 块（既有，606-631）之后：
if (
    rerun_actionability_check
    and self.rerun_actionability_stable                 # stable 独立开关（默认关）
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
        params["index"] = fresh_idx
```

> stable 在 actionability 之后、`_exec_one` 之前。两段独立等待，各自超时降级——stable 不通过不阻断执行（让既有 5/10/20s 退避重试兜底）。**定点单元素** `get_element_coordinates` 两次，仅白名单动作 + 双开关都开时触发，不影响整体性能。

### 缺口 7：改法 ② 新增 `user_pause_seconds`（agent 自录零回归）

**D-1 `agent/views.py:84-98`** —— `StepMetadata` 加字段，保留 `step_interval` 语义不动：

```python
class StepMetadata(BaseModel):
    """单步计时信息（重放步间延迟用）。

    ``step_interval`` = 上一步耗时（含 LLM 时间），仅 agent 自录路径填充。
    ``user_pause_seconds`` = 相邻用户操作的真实停顿（recorder 路径填充）。
    重放端优先用 ``user_pause_seconds``（忠实还原节奏），回落 ``step_interval``（封顶防 LLM 空等）。
    """
    step_start_time: float
    step_end_time: float
    step_number: int
    step_interval: float | None = None
    user_pause_seconds: float | None = None        # ← 新增（阶段4 / 缺口7）
    ...
```

**D-2 `recorder/flatten.py:55-63`** —— recorder 路径改填新字段，`step_interval` 不再填（走 None）：

```python
metadata=StepMetadata(
    step_start_time=action.timestamp,
    step_end_time=action.timestamp,
    step_number=step_number,
    # 录制回放专用：user_pause_seconds = 相邻 action 的 timestamp 差（人类操作间隔近似）。
    # 与 agent 自录路径的 step_interval（上一步耗时含 LLM）语义分离，重放端各自消费。
    user_pause_seconds=action.timestamp - prev_ts if prev_ts is not None else None,
),
```

**D-3 `agent/rerun.py:421-427` `_compute_step_delay`** —— 优先读 `user_pause_seconds`（不封顶，忠实还原），回落 `step_interval`（保持封顶）：

```python
def _compute_step_delay(self, item, delay_between_actions, max_step_interval):
    md = item.metadata
    if md:
        # recorder 路径优先：真实用户停顿，忠实还原（不封顶）
        if md.user_pause_seconds is not None:
            return md.user_pause_seconds
        # agent 自录路径：上一步耗时含 LLM，封顶防空等
        if md.step_interval is not None:
            return min(md.step_interval, max_step_interval)
    return delay_between_actions
```

> **封顶策略决策**（见决策表 D-8）：recorder 路径的 `user_pause_seconds` 推荐**不封顶**（忠实还原用户发呆；用户停 30s 选下拉就该等 30s）。若担心极端值（录制时离开座位 10 分钟），可加独立 `max_user_pause` 封顶（默认很大如 60s）——本文档推荐"不封顶"，封顶项列为备选。

**D-4 `agent/rerun.py:370-374` 日志** —— 区分两种来源：

```python
md = item.metadata
if md and md.user_pause_seconds is not None:
    delay_src = f"user_pause={md.user_pause_seconds:.1f}s"
elif md and md.step_interval is not None:
    delay_src = f"saved step_interval={md.step_interval:.1f}s"
else:
    delay_src = f"default delay={delay_between_actions}s"
```

**D-5 `agent/step.py` 不动** —— 改法 ② 的核心优势：`_build_step_metadata` 一字不改，agent 自录路径**零回归**。

#### 未选方案差异（备查）：改法 ① 统一为"操作间隔"语义

若改走 ①（`step_interval` 统一改成"操作间隔"），影响面：
- `agent/step.py:1103-1115` 重写 `_build_step_metadata`：需在 `StepPipeline` 加 `_prev_step_end_time` 字段（当前只存 `_step_start_time`），算 `当前步 start - 上一步 end`。但 agent 自录相邻 step 几乎紧挨（`_step` 一结束立刻下次 `get_action`），扣完大概率 0 或负 → 语义几乎无意义。
- `agent/rerun.py:421-427` 移除/放宽 `max_step_interval` 封顶（语义已变，不再需防 LLM 空等）→ agent 自录回放会变得极快（步间几乎不睡），**行为变化需全量回归**。
- `tests/test_rerun_history.py:79-99` 既有 `step_interval == 3.0`（上一步耗时）断言必断。
- 多处 `docs/rerun_history/*` 文档同步。

→ ① 概念更干净但动 agent 自录路径、回归风险大；② 影响面集中（rerun.py + flatten.py + views.py）、向后兼容旧 JSON、agent 自录零回归。**本期选 ②**。

### 遗留收编

**E-1 aria-disabled**：已并入阶段二 `_is_actionable` 改后代码（见 B-1 末尾 `aria-disabled` 分支），无额外文件。

**E-2 `recorder/locator.py:141`** —— 删 unreachable `return None`：

```python
# Level 3: RECT（位置兜底）
return _locate_by_rect(ref, selector_map)
# （删除原 L141 unreachable 的 return None）
```

### 配置（新开关链路，仿阶段一 5 touchpoints 范式）

新配置项（`config.py` 定义 + env 注入 + `RerunMixin` 注解 + `rerun_history` None 哨兵 + 管线回传，**逐点克隆阶段一 `rerun_actionability_check` 的接线**）：

| 配置项 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `rerun_actionability_receives_events` | bool | `True` | L1+L2 静态 receives-events（pointer-events + paint_order）。零开销，总开关 `rerun_actionability_check` 关时整体不生效 → 默认开仍零行为变更 |
| `rerun_actionability_runtime_occlusion` | bool | `False` | L3 运行时 elementFromPoint 遮挡。有 CDP 开销，默认关 |
| `rerun_actionability_stable` | bool | `False` | 阶段三 stable 检查。可选/优先级最低，默认关 |
| `rerun_actionability_stable_interval` | float | `0.1` | stable 两次取 rect 间隔（秒），对齐 Playwright ~100ms |
| `rerun_actionability_stable_tolerance` | float | `1.0` | stable rect 变化容差（像素） |

> **`receives_events` 默认值的权衡**（决策表 D-7）：默认 `True` 的理由是 L1/L2 零开销、让 actionable 语义对齐 Playwright、且总开关默认关时整体零行为变更；潜在风险是开了总开关的用户会立刻得到更严格判定（极少数 paint_order 误判可能降级）。若实现时倾向更保守，可改默认 `False`——不影响本期方案结构。

---

## 关键决策

| # | 决策 | 理由 |
|---|---|---|
| D-1 | receives-events 拆三层（L1 静态遮挡 / L2 pointer-events / L3 运行时遮挡） | 三层互补：L1/L2 零开销静态、L3 运行时真值。分别给开关，默认 L1+L2 开、L3 关 |
| D-2 | `ignored_by_paint_order` 字段从 SimplifiedNode 映射到 EnhancedDOMTreeNode | selector_map value 是 EnhancedDOMTreeNode（取不到原字段）；回填 `original_node` 最内聚，rerun 侧零改读路径 |
| D-3 | L3 运行时遮挡不进 `_is_actionable`，而在 `_wait_for_actionability` 内联 await | `_wait_until` predicate 同步，`_is_element_occluded` 是 async；内联是唯一不破既有同步路径的方式 |
| D-4 | `_wait_for_actionability` 重构为自带 deadline 循环（不再借 `_wait_until`） | 为容纳 L3 的 await；L1/L2 同步判定 + index 漂移重解析 + 超时降级行为全保留 |
| D-5 | stable 用 `get_element_coordinates` 两次比 rect，不新封 CDP | 三级 fallback 已就绪，零新代码；定点单元素不影响性能 |
| D-6 | stable 在 actionability 之后、`_exec_one` 之前，独立超时降级 | 两段等待正交；stable 不通过不阻断（退避重试兜底） |
| D-7 | `receives_events` 默认 `True`（受总开关管辖） | L1/L2 零开销；总开关默认关 → 默认开仍零行为变更；让 actionable 对齐 Playwright |
| D-8 | 缺口 7 选改法 ②（新增 `user_pause_seconds`），`user_pause_seconds` 不封顶 | agent 自录零回归、旧 JSON 向后兼容；recorder 真实停顿忠实还原（不封顶）；影响面集中 |
| D-9 | `_compute_step_delay` 优先级：`user_pause_seconds` > `step_interval`（封顶）> `delay_between_actions` | recorder 路径优先（语义更准），agent 自录路径保持封顶行为 |
| D-10 | `step.py` 不动 | 改法 ② 的核心：agent 自录路径零回归 |
| D-11 | aria-disabled 并入 `_is_actionable`，不单开开关 | 与 enabled 同属"能否交互"语义，零开销，无需独立开关 |
| D-12 | 删 `locator.py:141` 死代码 | 纯 unreachable，零行为影响，收编阶段 2 遗留 TODO |

---

## 改动清单（逐文件）

| 文件 | 改动 |
|---|---|
| `src/tree_walker/browser/views.py` | ① `EnhancedDOMTreeNode`（:260）加 `ignored_by_paint_order: bool = False` 字段 |
| `src/tree_walker/browser/paint_order.py` | ① `:178-179` 设置标志时同步回填 `node.original_node.ignored_by_paint_order = True` |
| `src/tree_walker/agent/rerun.py` | ① `_is_actionable`（:71-86）加 `check_receives_events` 形参 + L1/L2 分支 + aria-disabled ② `_wait_for_actionability`（:1122-1151）重构为自带 deadline 循环 + 接 L3 ③ 新增 `_is_rect_stable` + `_wait_for_stable` ④ `_execute_history_step`（:631 后）追加 stable 块 ⑤ `_compute_step_delay`（:421-427）优先读 `user_pause_seconds` ⑥ 日志（:370-374）区分来源 ⑦ 新配置项 RerunMixin 注解（:253-255 附近）+ `rerun_history` None 哨兵管线 |
| `src/tree_walker/agent/views.py` | ① `StepMetadata`（:84-98）加 `user_pause_seconds: float \| None = None` + 改 docstring |
| `src/tree_walker/recorder/flatten.py` | ① `:62` 改填 `user_pause_seconds`，`step_interval` 不再填 |
| `src/tree_walker/config.py` | ① `:130-136` 附近加 5 个新配置项定义 ② `:368-371` 附近加 env 注入 |
| `src/tree_walker/recorder/locator.py` | ① 删 `:141` unreachable `return None` |
| **`src/tree_walker/agent/step.py`** | **不改**（改法 ② 核心：agent 自录零回归） |
| **`src/tree_walker/recorder/recorder.py`** | **不改**（navigate/done 补丁步 `step_interval` 本就走 None 默认） |
| **`src/tree_walker/browser/session.py`** | **不改**（`_is_element_occluded` / `get_element_coordinates` 既有原语直接复用） |
| **`src/tree_walker/browser/dom.py`** | **不改**（`pointer-events` 早已在 `REQUIRED_COMPUTED_STYLES` 抓取） |

**复用**：`_wait_until` 骨架 / `_locate_target` 只读谓词 / `_is_file_input` 防御短路 / `_ACTIONABILITY_ACTIONS` 白名单（阶段一）+ `_is_element_occluded` / `get_element_coordinates` / `DOMRect` / `backend_node_id` / `EnhancedDOMTreeNode.x,y` / `ignored_by_paint_order` / `REQUIRED_COMPUTED_STYLES['pointer-events']`（既有 CDP/数据原语）+ 阶段一 5-touchpoint 配置链路范式。

---

## 测试策略

### `tests/test_rerun_history.py`

- **`_is_actionable` 扩展**（阶段一既有 `:942-981` 区段追加用例）：
  - `pointer-events:none`（snapshot_node.computed_styles）→ 阻断（`check_receives_events=True`）
  - `ignored_by_paint_order=True` → 阻断
  - `aria-disabled="true"` → 阻断（遗留收编）
  - `snapshot_node=None` → 放过（保守，不引入新失败）
  - `check_receives_events=False` → 行为与阶段一一字不差（零回归基线）
- **`_wait_for_actionability` 重构后**（既有 `:1078-1125` 区段）：
  - 阶段一三个既有用例（等至 visible 命中 / 超时降级 / index 漂移 5→7）全过——验证重构无回归
  - L3 运行时遮挡：mock `_is_element_occluded` 返 True（遮挡）→ 继续等 / 返 False → 命中
- **stable**（新增区段）：
  - 两次 rect 相同 → 稳定
  - rect 漂移超 tolerance → 不稳定
  - `get_element_coordinates` 返 None → 不稳定（保守）
  - `interval` / `tolerance` 可配
- **`_compute_step_delay` 优先级**（既有 `:394-406` 区段扩展）：
  - `user_pause_seconds` 优先于 `step_interval`
  - `user_pause_seconds` 不封顶（30s 原样返回）
  - `step_interval` 仍封顶（agent 自录路径，既有用例 `min(30,5)=5` 保留）
  - 两者都 None → `delay_between_actions` 兜底
- **agent 自录路径既有断言全不动**（`:79-99` `step_interval==3.0`、首步 None）——改法 ② 零回归的硬保证
- **配置**（既有 `:1233-1257` 区段扩展）：5 个新项默认值 + `Agent.__init__` 拆字段 + kwargs 优先 + None 哨兵回落

### `tests/test_recorder_flatten.py`

- 既有 `:77-88` `test_flatten_sets_step_interval_from_prev_ts` 改断言：三 action 非均匀 ts（10/12/17）→ `user_pause_seconds == [None, 2.0, 5.0]`，`step_interval` 全 None
- 既有 `:64-74` `test_flatten_state_summary_and_metadata` 改断言：`user_pause_seconds is None`（首步）

### `_is_element_occluded` 既有原语补单测（建议）

当前 `session._is_element_occluded` 无单测，阶段二 L3 依赖它。建议补：命中目标 / 命中祖先（label 包 input）/ 命中无关元素 / JS 异常降级返 False 四态。

---

## 验证

1. **文档体裁**：与 01-阶段1 / 02-阶段2 / 03-阶段3 一致（关联块 / Context / 现状锚点 / 方案 / 决策表 / 改动清单 / 测试 / 边界与风险 / 落地核对清单）。
2. **行号锚点**：全部对照 `master @ ab8c37c` 核验（实施时若 master 前进需重核；特别注意 `_wait_for_actionability` / `_is_actionable` / `_compute_step_delay` 行号）。
3. **纯文档零代码**：本方案文档不改任何 `.py`；代码改动留给 #127 实现 PR。
4. **后续实现 PR 的验证**：`uv run python -m pytest tests/ -x -v` 全过、覆盖率 ≥85%、默认配置（所有新开关关 / `receives_events` 虽默认开但总开关关）重放零回归、**agent 自录回放 + 用户录制回放双路径均不回归**（改法 ② 的核心保证：`step.py` 不动 + 既有 `step_interval` 断言保留）。

---

## 边界与风险

| 项 | 说明 |
|---|---|
| **L3/stable 的 async 谓词** | `_wait_until` predicate 同步，L3 与 stable 必须 `await` → `_wait_for_actionability` 重构为自带 deadline 循环。重构须保证阶段一既有行为（visible+enabled 等待 / index 漂移重解析 / 超时降级）逐字保留，靠既有三用例守底 |
| **`ignored_by_paint_order` 回填时机** | paint_order 遍历 SimplifiedNode 时 `original_node` 引用须已稳定（build_dom_state 之后）。回填前确认 paint_order 运行时机；若 SimplifiedNode 与 selector_map 的 EnhancedDOMTreeNode 非同一引用则回填无效——实施时加断言验证 |
| **`receives_events` 默认开的严格性** | 开了总开关的用户立刻得到更严格判定；paint_order 极少数误判可能让原本能跑的 case 降级（靠超时降级 + 退避重试兜底，不引入硬失败）。若实测误判多，改默认关 |
| **缺口 7 向后兼容** | 旧 `AgentHistory.json` 无 `user_pause_seconds`，pydantic 反序列化走默认 `None` ✓。旧 recorder 录制（已落盘）重放时 `user_pause_seconds=None` → 回落 `step_interval`（但 recorder 旧路径 step_interval 也是 timestamp 差，行为不变） |
| **stable 性能** | 定点单元素 `get_element_coordinates` 两次 + sleep(interval)，仅白名单动作 + `rerun_actionability_stable` 开时触发，默认关 → 零开销 |
| **`_compute_step_delay` 不封顶 `user_pause_seconds`** | 极端长停顿（录制时离开座位）会原样等。可接受（忠实还原）；若需防御加独立 `max_user_pause`（备选，本期不做） |
| **L3 跨源 iframe** | `_is_element_occluded` / `get_element_coordinates` 读 `self.current_session_id`，跨源 iframe 元素可能需扩展 session_id 传参——本期白名单动作（click/input_text/select_dropdown）通常在主文档，标记为已知边界 |

---

## 落地核对清单（实现 #127 时逐项核对）

- [ ] **`browser/views.py`**：`EnhancedDOMTreeNode` 加 `ignored_by_paint_order` 字段（默认 False）
- [ ] **`browser/paint_order.py`**：设置 SimplifiedNode 标志时同步回填 `original_node`；验证引用同一性
- [ ] **`agent/rerun.py`**：
  - [ ] `_is_actionable` 加 `check_receives_events` 形参 + L1（paint_order）+ L2（pointer-events）分支
  - [ ] `_is_actionable` 加 `aria-disabled="true"` 判定（遗留收编）
  - [ ] `_wait_for_actionability` 重构为自带 deadline 循环，接 L3（`runtime_occlusion` 开关）
  - [ ] 新增 `_is_rect_stable` + `_wait_for_stable`
  - [ ] `_execute_history_step` actionability 块后追加 stable 块
  - [ ] `_compute_step_delay` 优先读 `user_pause_seconds`（不封顶）→ `step_interval`（封顶）→ 兜底
  - [ ] 日志区分 `user_pause` / `step_interval` / `default` 三种来源
  - [ ] 5 个新配置项 RerunMixin 注解 + `rerun_history` None 哨兵 + 管线回传（仿阶段一）
- [ ] **`agent/views.py`**：`StepMetadata` 加 `user_pause_seconds` + 改 docstring
- [ ] **`recorder/flatten.py`**：改填 `user_pause_seconds`，`step_interval` 不再填
- [ ] **`config.py`**：5 个新配置项定义 + env 注入
- [ ] **`recorder/locator.py`**：删 `:141` unreachable `return None`
- [ ] **`agent/step.py` 不改**（改法 ② 核心）
- [ ] **测试**：`test_rerun_history.py` 加 receives-events/stable/`user_pause_seconds` 用例 + 既有 step_interval 断言保留；`test_recorder_flatten.py` 改断言到 `user_pause_seconds`；建议补 `_is_element_occluded` 单测
- [ ] **回归**：`uv run python -m pytest tests/ -x -v` 全过、覆盖率 ≥85%；默认配置重放零回归；agent 自录回放 + 用户录制回放双路径不回归
