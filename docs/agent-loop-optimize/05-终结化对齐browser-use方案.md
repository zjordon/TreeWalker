# 终结化（Final）对齐 browser-use 方案

> 阶段：agent step 5 阶段流程的**第五阶段「终结化」**（`_finalize`）
> 对照源：`D:\dev\git\learn_agent\browse-use\docs\agent-core\step内部流程\5-终结化.md` + `5-终结化内部逻辑\{1-前置检查与时间记录,2-创建历史记录项,3-记录步骤完成摘要,4-保存文件系统状态,5-步骤事件分发,6-递增步骤计数器}.md`；`D:\dev\git\z_jordon\browser-use\browser_use\agent\service.py`（`_finalize` L1344-L1403、`_make_history_item` L1725-L1767、`_log_step_completion_summary` L2142-L2165、`save_file_system_state` L737-L743、`_demo_mode_log` L2109）
> 本文档只交付**方案**，不含代码落地；落地为后续独立任务。
>
> **结论先行（2026-07-06 核对）**：TreeWalker 的 `_finalize()` **结构上已高度对齐** browser-use 的 6 子步骤——`finally` 块运行、`step_interval`（上一步耗时）、`StepMetadata`、interacted_elements 投影、`AgentHistory` 追加、步骤完成摘要日志、`n_steps` 最后递增（异常安全）**逐项命中**。**唯一实质数据缺口是「截图入历史」（P1）**，但被**截图视觉通道阶段二**阻塞（`include_screenshot=False`，`AgentHistory` 按「无截图」设计），本期只能记录集成点。其余为 **P2 可选增强（建议暂缓）** 与 **P3 架构差异 / 不适用（仅文档说明）**。**本期落地代码项 = 无**，与 phase 03「绝大多数已对齐甚至更优」同构。

---

## 1. 背景与范围

TreeWalker 的目标是「所有逻辑都对齐 browser-use」。Agent step 被拆为 5 阶段流水线（`src/tree_walker/agent/step.py`）：

| 阶段 | 方法 | 职责 |
|---|---|---|
| 1. Sense | `_prepare_context()` | 调 LLM 前组装全部输入（系统提示词、历史、工具、页面状态、注入提示） |
| 2. Think | `_get_next_action()` | 调 LLM 拿决策 |
| 3. Act | `_execute_actions()` | 执行动作 |
| 4. Post | `_post_process()` | 动作执行后的状态更新（下载/计划/循环检测/失败计数/结果日志） |
| 5. Final | `_finalize()` | 历史/摘要日志/步数递增 |

**本文档范围**：仅第五阶段 `_finalize()`（`step.py:1006-1055`），即 browser-use 的 `_finalize()`（`service.py:1344-1403`）及其调用的 `_make_history_item`（`service.py:1725-1767`）、`_log_step_completion_summary`（`service.py:2142-2165`）、`save_file_system_state`（`service.py:737-743`）、步骤事件分发（`service.py:1383-1400`）。**不覆盖** Sense/Think/Act/Post（阶段 1-4 已在 01-04 文档展开）；**不覆盖**异常路径 `_handle_step_error`（与 browser-use 一致、本期不动）与主循环控制（`agent.py`）。

经逐行核对，TreeWalker 在此阶段**绝大多数子步骤已对齐**（finally 语义、计时元数据、interacted_elements 投影、步数递增时序全部命中），仅存在 **1 个 P1 数据缺口（截图入历史，被外部依赖阻塞）+ 3 个 P2 可选增强（早退守卫语义 / tabs / state_message）+ 3 个 P3 架构差异（file_system / 富步骤事件 / demo_mode 通道）**。下文逐项给出代码级对照与方案。

---

## 2. 现状精确锚点（已核对真实代码）

### 2.1 TreeWalker `_finalize` 全文（`step.py:1006-1055`）

```python
def _finalize(
    self,
    browser_state: BrowserStateSummary | None,
    model_output: dict[str, Any] | None,
    results: list[ActionResult],
) -> None:
    """Record history, log summary, and advance step counter.

    Called from finally block — runs regardless of success or exception.
    n_steps is incremented last so all consumers see the current value.
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
        self.history.history.append(AgentHistory(
            step_number=self.state.n_steps,
            model_output=model_output,
            result=results,
            state_summary=state_summary,
            interacted_element=self._project_interacted_elements(model_output, browser_state),
            metadata=self._build_step_metadata(time.time()),
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
    self.state.n_steps += 1
```

### 2.2 辅助方法锚点

| 方法 | 位置 | 职责 |
|---|---|---|
| `_project_interacted_elements` | `step.py:1057-1086` | 把每个动作当年交互的元素投影成 `DOMInteractedElement.to_dict()`，与 `model_output.actions` 等长按位对应；用**该步开始时**的 `browser_state.selector_map` |
| `_build_step_metadata` | `step.py:1088-1100` | 构造 `StepMetadata`，`step_interval` 取 `history[-1].metadata.duration_seconds`（上一步耗时，首步 None） |
| `_log_step_completion_summary` | `step.py:1102-1115` | 委托 `log_step_completion(step, duration, ok_count, err_count, logger)` 输出步号/耗时/成功失败计数 |
| `_step` finally 调用点 | `step.py:155-156` | `finally: self._finalize(browser_state, model_output, results)`——无论 try 块成功或异常都运行 |
| `StepMetadata` / `AgentHistory` | `views.py:84-113` | 计时模型（start/end/number/interval + `duration_seconds` 属性）/ 历史项模型（step_number/model_output/result/state_summary/interacted_element/metadata，**无 screenshot_path**） |
| `StepEndEvent` | `observability/events.py:59-63` | 字段：`step / session_id / duration_seconds / is_done / consecutive_failures` |
| 截图开关 | `step.py:179` | `browser_state = await self.browser.get_state(include_screenshot=False)`（视觉通道未打通，见 `docs/tools-optimize/screenshot.md` 阶段二） |

**六个决定性结论：**

1. **`finally` 块语义对齐**。`_finalize` 在 `step.py:155-156` 的 `finally` 块内调用，与 browser-use `step()`（`service.py:1023`）的 `finally: await self._finalize(...)` 一致——try 块成功或异常都运行。
2. **`step_interval` 计算对齐**。`_build_step_metadata`（`step.py:1093-1094`）取 `history[-1].metadata.duration_seconds` 作为上一步耗时，与 browser-use `_finalize`（`service.py:1351-1358`，`max(0, prev_end - prev_start)`）语义一致；首步为 None。
3. **interacted_elements 投影对齐**。`_project_interacted_elements`（`step.py:1057-1086`）按动作 index 从 `selector_map` 还原 DOMElement 并 `to_dict()`，与 browser-use `AgentHistory.get_interacted_element`（`views.py:500`）在 `_make_history_item`（`service.py:1735-1738`）的调用等价。
4. **`n_steps` 递增时序对齐**。`step.py:1055` `self.state.n_steps += 1` 是 `_finalize` 最后一行，注释明确「all consumers see the current value」——与 browser-use `service.py:1403`（最后操作、异常安全）一致。
5. **截图入历史是唯一实质缺口（P1，被阻塞）**。browser-use `_make_history_item`（`service.py:1740-1757`）经 `screenshot_service.store_screenshot` 异步存盘并把路径写入 `BrowserStateHistory.screenshot_path`；TreeWalker `include_screenshot=False`（`step.py:179`），`AgentHistory`（`views.py:101-113`）按「无截图」设计、无该字段。
6. **三处架构差异为 N/A 或已等价覆盖**。`save_file_system_state`（无 file_system）/ `CreateAgentStepEvent` 富事件（无云同步架构，细粒度事件已覆盖）/ `_demo_mode_log` 通道（无 demo 模式）——详见 §6。

---

## 3. browser-use 6 子步骤 vs TreeWalker 全景对照

### 子步骤 1：前置检查与时间记录（browser-use L1346-L1348）

| # | 子步骤 | browser-use | TreeWalker 现状 | 状态 |
|---|---|---|---|---|
| 1a | 记录 `step_end_time` | L1346 `step_end_time = time.time()` | 内联 `time.time()`（`_build_step_metadata(time.time())` `step.py:1041` + `state_summary["duration"]` `step.py:1023`） | ✅ 对齐（取数方式略异） |
| 1b | 早退守卫 | L1347-L1348 `if not self.state.last_result: return`（跳过历史/日志/事件/**n_steps 递增**） | 守卫为 `if model_output is not None:`（仅门控历史写入）；`n_steps` **总递增** | ⚠️ **P2-1**（语义分歧，见 §5.1） |

### 子步骤 2：创建历史记录项（browser-use L1350-L1373 → `_make_history_item` L1725-L1767）

| # | 子步骤 | browser-use | TreeWalker 现状 | 状态 |
|---|---|---|---|---|
| 2a | `step_interval`（上一步耗时） | L1351-L1358 `max(0, prev_end - prev_start)` | ✅ `_build_step_metadata` 取 `history[-1].metadata.duration_seconds` | ✅ 对齐 |
| 2b | `StepMetadata`(start/end/number/interval) | L1359-L1364 | ✅ `views.py:84-98`（+ `duration_seconds` 属性） | ✅ 对齐 |
| 2c | interacted_elements 投影 | L1735-L1738 `get_interacted_element(model_output, selector_map)` | ✅ `_project_interacted_elements`（等长按位，`step.py:1057-1086`） | ✅ 对齐 |
| 2d | **截图存盘 + `screenshot_path`** | L1740-L1749 `screenshot_service.store_screenshot`（异步存盘）→ `BrowserStateHistory.screenshot_path` | ❌ `include_screenshot=False`（`step.py:179`）；`AgentHistory` 无 screenshot_path 字段（`views.py:110` 自注「无截图」） | ❌ **P1**（依赖截图视觉通道阶段二，见 §4） |
| 2e | `BrowserStateHistory` 字段 | L1751-L1757 url/title/**tabs**/interacted_element/screenshot_path | `state_summary` dict：url/title/duration/dom_excerpt（done 步）— **缺 tabs** | ⚠️ **P2-2**（tabs 可选，见 §5.2） |
| 2f | **`state_message` 入历史** | L1367-L1373 `state_message=self._message_manager.last_state_message_text` → `AgentHistory.state_message`（`views.py:495`） | ❌ 不存 state_message（用滑动窗口 `<agent_history>` 注入替代） | ⚠️ **P2-3**（重放完整性 vs 体积，见 §5.3） |
| 2g | `AgentHistory` + `add_item` | L1759-L1767 `self.history.add_item(history_item)` | ✅ `self.history.history.append(AgentHistory(...))`（`step.py:1035-1042`） | ✅ 对齐（append vs add_item 等价） |

### 子步骤 3：记录步骤完成摘要（browser-use L1376-L1378 + `_log_step_completion_summary` L2142-L2165）

| # | 子步骤 | browser-use | TreeWalker 现状 | 状态 |
|---|---|---|---|---|
| 3a | 空结果守卫 | L2144-L2145 `if not result: return None` | ✅ `if not results: return`（`step.py:1104-1105`） | ✅ 对齐 |
| 3b | 统计动作数/耗时/成败 | L2147-L2152 `step_duration` / `action_count` / `success_count` / `failure_count` | ✅ `log_step_completion(step, duration, ok_count, err_count, ...)`（`step.py:1106-1114`） | ✅ 对齐（统计维度一致） |
| 3c | 文案格式 | L2160-L2163 `📍 Step N: Ran X action(s) in Ys: ✅ A \| ❌ B` | 委托 `log_step_completion`（具体文案在工具函数内，结构等价） | ✅ 对齐（措辞小异） |
| 3d | 双通道输出 | L1376-L1378 `logger.debug(message)` + `_demo_mode_log(message, 'info', ...)` | 单通道（logger） | ⚠️ **P3-3**（demo_mode 通道 N/A，见 §6.3） |

### 子步骤 4：保存文件系统状态（browser-use L1381 → `save_file_system_state` L737-L743）

| # | 子步骤 | browser-use | TreeWalker 现状 | 状态 |
|---|---|---|---|---|
| 4a | 持久化 file_system 快照 | L737-L743 `state.file_system_state = file_system.get_state()`（未初始化则 raise） | ❌ 无 file_system 概念（下载走 `state.downloaded_files` + 变体 B done `attachments`） | ⚠️ **P3-1**（架构差异 / N/A，见 §6.1） |

### 子步骤 5：步骤事件分发（browser-use L1383-L1400 → `CreateAgentStepEvent.from_agent_step`）

| # | 子步骤 | browser-use | TreeWalker 现状 | 状态 |
|---|---|---|---|---|
| 5a | 守卫 | L1384 `if browser_state_summary and self.state.last_model_output:` | `_finalize` 内 `if self._obs_bus:`（`step.py:1044`） | ✅ 守卫语义对齐（触发条件不同） |
| 5b | actions 序列化 | L1386-L1390 `action.model_dump()` 逐个 | （无——动作执行已由 `ToolCallEvent` 在更细粒度覆盖） | ⚠️ **P3-2**（见 §6.2） |
| 5c | 截图 base64 data URL | `cloud_events.py` `screenshot_url=f'data:image/png;base64,{screenshot}'` | ❌（无截图；无云同步） | ⚠️ P3-2（同 2d 依赖） |
| 5d | 事件字段 | step/eval/memory/next_goal/actions/url/screenshot_url | `StepEndEvent`：step/session_id/duration/is_done/consecutive_failures（`events.py:59-63`） | ⚠️ **P3-2**（精简设计，功能已等价覆盖，见 §6.2） |

### 子步骤 6：递增步骤计数器（browser-use L1403）

| # | 子步骤 | browser-use | TreeWalker 现状 | 状态 |
|---|---|---|---|---|
| 6a | `n_steps += 1` 最后操作 | L1403（`_finalize` 最后一行） | ✅ `step.py:1055`（最后一行） | ✅ 对齐 |
| 6b | 异常安全（前序异常则不递增） | 设计文档「Exception Safety：前序抛异常则 n_steps 不递增，下一步用同号重试」 | ✅ 注释「n_steps is incremented last so all consumers see the current value」（`step.py:1015`） | ✅ 对齐 |

---

## 4. P1：截图入历史 — 被外部依赖阻塞，本期仅记录集成点

### 4.1 现状

browser-use `_make_history_item`（`service.py:1740-1757`）：

```python
# Store screenshot and get path
screenshot_path = None
if browser_state_summary.screenshot:
    screenshot_path = await self.screenshot_service.store_screenshot(
        browser_state_summary.screenshot, self.state.n_steps)
state_history = BrowserStateHistory(
    url=browser_state_summary.url,
    title=browser_state_summary.title,
    tabs=browser_state_summary.tabs,
    interacted_element=interacted_elements,
    screenshot_path=screenshot_path,
)
```

TreeWalker：`_prepare_context` 中 `include_screenshot=False`（`step.py:179`，注释「LLM 视觉通道尚未打通，见 `docs/tools-optimize/screenshot.md` 阶段二」）；`AgentHistory`（`views.py:101-113`）无 `screenshot_path` 字段，`views.py:110` 自注「本方案「无截图」，故不含 screenshot_path 字段」。

### 4.2 影响

历史项缺失每步页面截图，重放/调试时无法还原当年视觉现场；`AgentHistoryList.screenshot_paths()`（browser-use `views.py:771-784`）类能力无对应。**这是 Phase 5 唯一的实质数据缺口**，但根因在更上游的「截图视觉通道」工作流（phase 二），而非 `_finalize` 本身的设计疏漏。

### 4.3 方案（记录集成点，不在本期落地）

截图视觉通道打通后，在 `_finalize` 内补 screenshot 存盘与字段写入。**落地时需同步处理的两点**：

1. **`AgentHistory` 增 `screenshot_path: str | None = None` 字段**（`views.py:101-113`），删除 `views.py:110`「无截图」注释。
2. **`_finalize` 改 `async def`**：browser-use `_finalize` 是 `async def`（`service.py:1344`），因 `screenshot_service.store_screenshot` 是异步 I/O；TreeWalker 当前 `def _finalize`（`step.py:1006`，sync）。落地截图存盘时需把 `_finalize` 改为 `async def`，并同步更新 `step.py:156` 调用点为 `await self._finalize(...)`、`step.py:155` 的 `finally` 块内 await 语义。`_make_history_item` 是否单列为方法可选（browser-use 单列；TreeWalker 当前内联，二者等价）。

**前置依赖**：`docs/tools-optimize/screenshot.md` 阶段二（截图采集 + `screenshot_service` 存盘服务）。未打通前不动 `_finalize`。

### 4.4 边界与风险

- 改 `_finalize` 为 async 会牵连 `step.py:155-156` finally 调用点 + 所有 mock `_finalize` 的测试（须改 `AsyncMock` / `await`）。落地时需全量跑 `uv run python -m pytest tests/ -x -v`。
- 截图存盘是磁盘 I/O，须考虑失败容错（browser-use 仅 debug 日志，不中断）；TreeWalker 落地时建议 try/except 包裹，存盘失败仅记日志、`screenshot_path` 置 None，不影响历史项写入与步数递增。

---

## 5. P2：可选增强（建议暂缓，记录取舍）

### 5.1 P2-1：早退守卫语义分歧 — 建议保留 TreeWalker 现状，不对齐

**分歧**：browser-use `service.py:1347-1348` `if not self.state.last_result: return` 会**跳过历史写入、摘要日志、事件分发、`n_steps` 递增**全部终结化操作；TreeWalker `step.py:1017` 守卫为 `if model_output is not None:`，仅门控历史写入，`StepEndEvent` 与 `n_steps += 1`（`step.py:1044-1055`）**总执行**。

**取舍**：
- browser-use 的「无结果则不递增 n_steps」在边缘场景（异常未生成 ActionResult）下会让下一步复用同一步号，语义上允许「重试同一步」；但也会让 no-op 步不消费 `max_steps` 预算。
- TreeWalker 的「总递增」更稳健可预测：`model_output is None` 仅在「LLM 期间用户停止」时发生（`step.py:141-144`，输出已丢弃直接 `return False`），此时下一步循环顶 `step.py:131` 的 `stopped` 检查即退出循环，`n_steps` 是否递增实际无副作用。

**结论**：TreeWalker 行为更稳健，**建议保留、不对齐**；本文档仅记录分歧与理由。若未来出现「异常路径未生成 ActionResult 却需复用步号」的明确需求再评估。

### 5.2 P2-2：`state_summary` 补 tabs — 价值低，暂缓

browser-use `BrowserStateHistory`（`service.py:1754`）存 `tabs=browser_state_summary.tabs`；TreeWalker `state_summary`（`step.py:1020-1034`）仅 url/title/duration/dom_excerpt。重放/调试场景几乎不用 tabs 列表，且 `BrowserStateSummary` 已在 sense 阶段采集（`step.py:179`），补字段成本低但价值低 → **暂缓**。

### 5.3 P2-3：`state_message` 入历史 — 完整性 vs 体积，暂缓

browser-use 把 `self._message_manager.last_state_message_text`（喂给 LLM 的完整 state 文本）写入每个 `AgentHistory.state_message`（`service.py:1372`、`views.py:495`、`588`），供重放时还原「LLM 当年看到什么」。TreeWalker 用滑动窗口 `<agent_history>` 描述注入（`step.py:252` `_set_history_message`），不存原 state_message。

**取舍**：state_message 含完整 DOM 描述，每步存盘会显著膨胀历史体积（与 `dom_excerpt` 仅 done 步携带的策略冲突）。TreeWalker 当前重放需求未明确要求逐字还原 LLM 输入 → **暂缓**，除非未来重放/评测工作流明确依赖。

---

## 6. P3：架构差异 / 不适用（仅文档说明，不动代码）

### 6.1 P3-1：`save_file_system_state` — 架构差异，N/A

browser-use `save_file_system_state`（`service.py:737-743`）持久化虚拟 `file_system` 快照到 `state.file_system_state`，供跨步文件操作（step N 下载、step N+1 读取）与中断恢复。TreeWalker **无 file_system 概念**：下载由 `state.downloaded_files`（持久列表）+ 变体 B（done 结果 `attachments`）+ sense 阶段 `download_notice` 喂 LLM 替代。**phase 03（`file_system` 剔除）+ phase 04（`available_file_paths` 剔除）已处理同类项**，本期保持一致，不引入。

### 6.2 P3-2：步骤事件分发（`CreateAgentStepEvent`）— 无云同步架构，功能已等价覆盖

browser-use 在 `_finalize`（`service.py:1383-1400`）分发富事件 `CreateAgentStepEvent`：actions 序列化、截图 base64 data URL、url、`evaluation_previous_goal` / `memory` / `next_goal`——供云同步 / UI 实时更新 / 遥测等外部消费者。TreeWalker **无云同步架构**，`StepEndEvent`（`events.py:59-63`）故意为精简设计（step/session_id/duration/is_done/consecutive_failures）。

**功能等价**：browser-use 富事件携带的 LLM reasoning（evaluation/memory/next_goal）已由 TreeWalker 的 `ModelResultEvent`（`events.py:29-36`）在更细粒度覆盖；动作执行已由 `ToolCallEvent` / `ToolResultEvent`（`events.py:39-56`）覆盖。`StepEndEvent` 只承担「步级聚合摘要」职责，概念分层更清晰。**不建议本期富化 `StepEndEvent`**：会与既有细粒度事件重复，且无外部消费者驱动。

### 6.3 P3-3：`_demo_mode_log` 双通道 — 无 demo 模式，N/A

browser-use `_log_step_completion_summary`（`service.py:2142-2165`）返回 message 字符串后，`_finalize`（`service.py:1376-1378`）走双通道：`logger.debug(message)` + `_demo_mode_log(message, 'info', ...)`（`service.py:2109`，向浏览器 demo 模式面板推送）。TreeWalker **无 demo 模式**，`_log_step_completion_summary`（`step.py:1102-1115`）单通道委托 `log_step_completion` 足够，不引入 `_demo_mode_log`。

---

## 7. 测试策略

本期**纯文档，无代码改动 → 无新增测试**。未来落地 P1（截图入历史）时需补：

| 落地点 | 文件 | 用例 |
|---|---|---|
| 截图存盘路径写入历史 | `tests/test_step_*` 或历史相关测试 | (1) `include_screenshot=True` 时 `AgentHistory.screenshot_path` 为存盘路径；(2) 截图为空时 `screenshot_path=None`、不崩；(3) 存盘 I/O 异常时仅记日志、历史项仍写入 |
| `_finalize` 改 async | 现有 mock `_finalize` 的测试 | 全部改 `AsyncMock` + `await`；`finally` 块内 await 语义正确 |
| 回归 | 全量 | `uv run python -m pytest tests/ -x -v` 全过，覆盖率 >85%（CLAUDE.md 要求） |

---

## 8. 暂缓 / 剔除项与理由

| 项 | 决定 | 理由 |
|---|---|---|
| 截图入历史（`screenshot_path` + 存盘） | **P1-blocked**（记录集成点，本期不落地） | 依赖截图视觉通道阶段二（`docs/tools-optimize/screenshot.md`）；落地需 `_finalize` 改 async + 调用点 + 测试全改 |
| 早退守卫对齐 browser-use（`if not last_result: return`） | **不对齐**（保留 TreeWalker 现状） | TreeWalker「总递增 n_steps」更稳健可预测；分歧仅在用户中途停止边缘场景、实际无副作用 |
| `state_summary` 补 tabs | **暂缓** | 重放/调试几乎不用，价值低 |
| `state_message` 入历史 | **暂缓** | 完整性 vs 体积取舍；与 `dom_excerpt` 仅 done 步策略冲突；重放需求未明确 |
| `save_file_system_state` | **剔除（N/A）** | 无 file_system 概念；phase 03/04 已剔除同类 |
| 富化 `StepEndEvent`（对齐 `CreateAgentStepEvent`） | **剔除（N/A）** | 无云同步架构；功能已由 ModelResultEvent/ToolCallEvent/ToolResultEvent 等价覆盖 |
| `_demo_mode_log` 双通道 | **剔除（N/A）** | 无 demo 模式 |

---

## 9. 实施路线图与本期范围

| 优先级 | 项 | 本期 | 复杂度 | 价值 | 依赖 |
|---|---|---|---|---|---|
| **P1** | 截图入历史（`screenshot_path` + 存盘 + `_finalize` async 化） | ⏸ blocked（仅记录集成点） | 中（改 `_finalize` 签名 + 调用点 + AgentHistory 字段 + 测试） | 高（重放/调试视觉现场） | 截图视觉通道阶段二 |
| **P2-1** | 早退守卫语义 | 📄 仅文档（建议保留） | — | — | — |
| **P2-2** | `state_summary` 补 tabs | ⏸ 暂缓 | 低 | 低 | — |
| **P2-3** | `state_message` 入历史 | ⏸ 暂缓 | 低-中 | 中（重放完整性） | 明确重放需求 |
| **P3-1** | `save_file_system_state` | 📄 仅文档（N/A） | — | — | — |
| **P3-2** | 富步骤事件 / `StepEndEvent` 富化 | 📄 仅文档（N/A） | — | — | — |
| **P3-3** | `_demo_mode_log` 双通道 | 📄 仅文档（N/A） | — | — | — |

**本期落地代码项 = 无**（纯方案文档）。Phase 5 结构已对齐，实质缺口（截图）被外部依赖阻塞，待截图工作流落地后按 §4 集成点接入。

**后续触发条件**：
1. 截图视觉通道阶段二完成 → 触发 P1 落地（按 §4.3 集成点 + §7 测试）。
2. 出现明确的「逐字重放 LLM 输入」需求 → 触发 P2-3 评估。
3. 引入云同步 / 外部步级消费者 → 触发 P3-2 评估（届时优先复用既有细粒度事件，而非富化 StepEndEvent）。

---

## 附：落地核对清单（P1 截图入历史触发时）

- [ ] `views.py:101-113` `AgentHistory` 增 `screenshot_path: str | None = None` 字段，删除「无截图」注释
- [ ] `step.py:179` `include_screenshot` 切 `True`（或按截图工作流配置驱动）
- [ ] `step.py:1006` `def _finalize` 改 `async def _finalize`
- [ ] `step.py:155-156` finally 调用点改 `await self._finalize(...)`
- [ ] `_finalize` 内补 screenshot 存盘（try/except 容错：失败仅日志、`screenshot_path=None`）+ 写入 `AgentHistory.screenshot_path`
- [ ] 接入 `screenshot_service`（或等价存盘服务），存盘路径规则对齐 browser-use（按 `n_steps` 命名）
- [ ] 全量改 mock `_finalize` 的测试为 `AsyncMock` + `await`
- [ ] 新增截图存盘 / 空截图 / 存盘异常三类用例
- [ ] 回归：`uv run python -m pytest tests/ -x -v` 全过，覆盖率 >85%

---

*本文档基于 TreeWalker 当前 master 分支（`_finalize` @ `step.py:1006-1055`、`_project_interacted_elements` @ `1057-1086`、`_build_step_metadata` @ `1088-1100`、`_log_step_completion_summary` @ `1102-1115`、`_step` finally @ `155-156`、`include_screenshot=False` @ `179`、`StepMetadata/AgentHistory` @ `views.py:84-113`、`StepEndEvent` @ `observability/events.py:59-63`）与 browser-use `_finalize`（`service.py:1344-1403`）、`_make_history_item`（`1725-1767`）、`_log_step_completion_summary`（`2142-2165`）、`save_file_system_state`（`737-743`）的逐行对比，并核对 browser-use 设计文档 `5-终结化.md` 及 `5-终结化内部逻辑\*.md`。落地实施时须以最新代码为准复核行号。*
