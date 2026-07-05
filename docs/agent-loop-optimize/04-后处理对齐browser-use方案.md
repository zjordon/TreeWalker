# 后处理（Post）对齐 browser-use 方案

> 阶段：agent step 5 阶段流程的**第四阶段「后处理」**（`_post_process`）
> 对照源：`D:\dev\git\learn_agent\browse-use\docs\agent-core\step内部流程\4-后处理.md` + `4-后处理内部逻辑\{1-检查下载状态,2-更新计划状态,3-循环检测器更新,4-失败计数管理,5-完成结果日志}.md`；`D:\dev\git\z_jordon\browser-use\browser_use\agent\service.py`（`_post_process` L1207-L1244、`_check_and_update_downloads` L650-L685、`_update_plan_from_model_output` L1405-L1438、`_update_loop_detector_actions` L1496-L1513）
> 本文档只交付**方案**，不含代码落地；落地为后续独立任务。
>
> **实施范围（2026-07-06 确定）**：本期落地 **P1-1（失败计数语义对齐 `len==1`）+ P1-2（完成结果日志补 attachments 输出 + 文案对齐 `📄 Final Result:`）**；**`available_file_paths` / post_process 第二次下载刷新剔除**（架构差异、功能已等价，phase 03 已剔除同类项）；**loop_detector 模块差异（归一化 / 页面停滞指纹 / 窗口）暂缓**（落在 phase 01 + 模块本身，另开文档）。其余章节保留作为对齐全景与未来参考。

---

## 1. 背景与范围

TreeWalker 的目标是「所有逻辑都对齐 browser-use」。Agent step 被拆为 5 阶段流水线（`src/tree_walker/agent/step.py`）：

| 阶段 | 方法 | 职责 |
|---|---|---|
| 1. Sense | `_prepare_context()` | 调 LLM 前组装全部输入（系统提示词、历史、工具、页面状态、注入提示） |
| 2. Think | `_get_next_action()` | 调 LLM 拿决策 |
| 3. Act | `_execute_actions()` | 执行动作 |
| 4. Post | `_post_process()` | 动作执行后的状态更新（下载/计划/循环检测/失败计数/结果日志） |
| 5. Final | `_finalize()` | 历史/日志/步数 |

**本文档范围**：仅第四阶段 `_post_process()`（`step.py:922-991`），即 browser-use 的 `_post_process()`（`service.py:1207-1244`）。**不覆盖** Sense/Think/Act/Final（阶段 1/2/3 已在 01/02/03 文档展开，阶段 5 后续文档展开）；**不覆盖**异常路径 `_handle_step_error`（与 browser-use 一致、本期不动）与主循环阈值检查（`agent.py`）。

经逐行核对，TreeWalker 在此阶段**绝大多数子步骤已对齐**（计划更新完全对齐、循环检测器调用点对齐、ANSI 颜色对齐），但仍存在 **2 个 P1 行为分歧（失败计数语义 + 完成日志缺 attachments）+ 1 组 P3 架构差异（下载检测架构 + loop_detector 模块）**。下文逐项给出代码级方案。

---

## 2. 现状精确锚点（已核对真实代码）

### 2.1 TreeWalker `_post_process` 全文（`step.py:922-991`）

```python
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
    # sees the full sequence; Phase 4 will refine failure semantics.
    actions = model_output.get("actions") or [model_output.get("action", {})]
    for action in actions:
        action_name = action.get("name", "done")
        action_params = action.get("params", {})
        if action_name not in _LOOP_EXEMPT_ACTIONS:
            self.loop_detector.record_action(action_name, action_params)

    # Phase 4 failure semantics:
    #   - all-error step (single-action OR multi-action all failed) → count
    #   - multi-action step with partial failure → do NOT count
    #     (loop_detector + replan handle recovery)
    #   - any success → reset counter
    if results and all(r.error for r in results):
        self.state.consecutive_failures += 1
        logger.debug("Consecutive failures: %d", self.state.consecutive_failures)
        return
    if results and any(r.error for r in results) and not all(r.error for r in results):
        logger.info(
            "Multi-action step had partial failure (%d/%d actions failed) "
            "— not incrementing consecutive_failures",
            sum(1 for r in results if r.error), len(results),
        )

    # Success → reset failure counter
    if self.state.consecutive_failures > 0:
        self.state.consecutive_failures = 0

    # Completion result logging
    if results and results[-1].is_done:
        result = results[-1]
        if result.success:
            logger.info(
                "\n\033[32m Task SUCCESS\033[0m\n%s\n",
                result.extracted_content or "",
            )
        else:
            logger.info(
                "\n\033[31m Task FAILED\033[0m\n%s\n",
                result.extracted_content or "",
            )
```

### 2.2 维度锚点

| 维度 | TreeWalker 现状 | 文件:行 |
|---|---|---|
| 入口签名 | `_post_process(self, results, model_output)`（同步，results/model_output 由 `_step` 传入） | `step.py:922-926` |
| 存储结果 | `state.last_result = results` / `state.last_model_output = model_output`（显式回写） | `step.py:936-937` |
| 下载处理 | `_attach_downloads_to_done_results(results, state.downloaded_files)`（变体 B，attach 到 done 结果的 attachments）；下载**检测**在 sense 阶段 `_prepare_context` 的 `consume_completed_downloads` | `step.py:942-943`、`210-219`；helper `step.py:57-73` |
| 计划更新 | `plan_manager.update_from_model_output(state, model_output)`（双路径 + clamp + 区间 done + 互斥 return） | `step.py:946-947`；`plan_manager.py:26-61` |
| 循环检测器记录 | 遍历 `actions`，非豁免动作调 `loop_detector.record_action(name, params)` | `step.py:952-957` |
| 豁免集合 | `_LOOP_EXEMPT_ACTIONS = frozenset({"wait", "done", "go_back"})` | `step.py:1210` |
| 失败计数（现状） | `all(r.error)` → `consecutive_failures += 1` + early return；partial → info 日志不计数；success → reset | `step.py:964-977` |
| 完成日志 | `results[-1].is_done` → 绿 `Task SUCCESS` / 红 `Task FAILED` + `extracted_content`；**不输出 attachments** | `step.py:980-991` |
| 异常路径递增 | `_handle_step_error` 第三分支 `consecutive_failures += 1`（与 browser-use 一致） | `step.py:1137-1148` |
| `_step` 调用点 | `self._post_process(results, model_output)`（`_step` L146，try 块内，`_execute_actions` 之后） | `step.py:146` |

**四个决定性结论：**

1. **失败计数语义与 browser-use 分歧，且是已知 TODO**。`step.py:964` 用 `all(r.error for r in results)`，单动作**或多动作全失败都计数**；browser-use `service.py:1223` 用 `len(last_result)==1 and last_result[-1].error`，**仅单动作失败计数**。`step.py:951` 注释自标「Phase 4 will refine failure semantics」。
2. **完成日志缺 attachments 输出**。`step.py:980-991` 只打印文案 + `extracted_content`；browser-use `service.py:1241-1244` 额外遍历 `attachments` 打印 `👉 Attachment {N}: {path}`。TreeWalker 已通过变体 B 把下载挂到 done 结果的 `attachments`（`step.py:942-943`），数据已就位，只差展示。
3. **计划更新完全对齐**。`plan_manager.update_from_model_output`（`plan_manager.py:26-61`）的双路径（plan_update 整体替换 / current_plan_item 推进指针）、clamp 到 `[0, len-1]`、`[old_idx, new_idx)` 区间标 done、`plan_update` 末尾 `return` 互斥——与 browser-use `_update_plan_from_model_output`（`service.py:1405-1438`）逐行一致。
4. **下载检测是架构差异而非缺失**。browser-use 在 post_process 调 `_check_and_update_downloads` 刷新独立的 `available_file_paths`（sense/post 各一次）；TreeWalker 在 sense 的 `_prepare_context` consume 下载并构建 `download_notice` 喂 LLM，post_process 把下载 attach 到 done 结果。功能等价，概念不同（详见 P3-1）。

---

## 3. browser-use 5 子步骤 vs TreeWalker 全景对照

### 子步骤 1：检查下载状态（browser-use L1212）

| # | 子步骤 | browser-use | TreeWalker 现状 | 状态 |
|---|---|---|---|---|
| 1a | post_process 触发 | L1212 `_check_and_update_downloads('after executing actions')` | 无对应调用（post_process 只做变体 B attach） | ⚠️ **P3-1** |
| 1b | 增量检测 | `_last_known_downloads` 快照对比 + set 差集 | sense 阶段 `consume_completed_downloads`（清空式 consume） | ⚠️ P3-1（机制不同） |
| 1c | 可用文件列表 | 刷新 `available_file_paths`（独立列表，供 `file_upload` 引用） | `state.downloaded_files` + done `attachments` + `download_notice` 喂 LLM | ⚠️ P3-1（概念替代） |
| 1d | 容错 | try/except → debug 日志，不中断 | consume 在 sense 阶段，异常由上层处理 | ✅ 等价 |

### 子步骤 2：更新计划状态（browser-use L1215-L1216）

| # | 子步骤 | browser-use | TreeWalker 现状 | 状态 |
|---|---|---|---|---|
| 2a | 入口守卫 | `if last_model_output is not None:` | `if self._enable_planning and self.plan_manager:`（`_post_process` 已确保 model_output 非 None） | ✅ 对齐 |
| 2b | 分支 A：plan_update 整体替换 | L1411-L1420，重置 index=0、记 `plan_generation_step`、首项='current'、return | `plan_manager.py:34-46` 逐行一致 | ✅ 对齐 |
| 2c | 分支 B：current_plan_item 推进 | L1423-L1438，clamp `[0,len-1]`、`[old,new)` 区间标 done、`plan[new]='current'` | `plan_manager.py:49-61` 逐行一致 | ✅ 对齐 |
| 2d | 互斥 | `plan_update` 末尾 `return` | `plan_manager.py:46` `return` | ✅ 对齐 |

### 子步骤 3：循环检测器更新（browser-use L1219）

| # | 子步骤 | browser-use | TreeWalker 现状 | 状态 |
|---|---|---|---|---|
| 3a | 入口守卫 | `loop_detection_enabled` 关闭 / `last_model_output is None` → 跳过 | 无 `loop_detection_enabled` 开关（始终记录）；model_output 已确保非 None | ✅ 调用点对齐（TreeWalker 无开关，可接受） |
| 3b | 遍历动作 | `last_model_output.action` | `model_output.get("actions") or [model_output.get("action", {})]` | ✅ 对齐（结构差异：list vs 单 action 字段） |
| 3c | 豁免集合 | `{'wait', 'done', 'go_back'}` | `frozenset({"wait", "done", "go_back"})` | ✅ 对齐 |
| 3d | 记录 | `record_action(action_name, params)` | `loop_detector.record_action(action_name, action_params)` | ✅ 调用点对齐 |
| 3e | （模块内）哈希归一化 | 按动作类型语义归一化（search/click/type/scroll/navigate） | `name:json(params 去 text/clear)` | ⚠️ **P3-2**（模块差异，超本期范围） |

### 子步骤 4：失败计数管理（browser-use L1221-L1231）

| # | 子步骤 | browser-use | TreeWalker 现状 | 状态 |
|---|---|---|---|---|
| 4a | 计数触发条件 | `len(last_result)==1 and last_result[-1].error`（**仅单动作**） | `all(r.error for r in results)`（**单动作或多动作全失败**） | ❌ **P1-1** |
| 4b | 计数动作 | `consecutive_failures += 1` + debug 日志 + `return` | `consecutive_failures += 1` + debug 日志 + `return` | ✅ 对齐（仅判定条件不同） |
| 4c | partial 失败处理 | 无（直接 fall through 到 reset） | 显式 info 日志「Multi-action step had partial failure … not incrementing」 | ✅ TreeWalker 增强（保留） |
| 4d | 成功重置 | `if consecutive_failures > 0: = 0` + debug 日志 | `if consecutive_failures > 0: = 0`（无 debug 日志） | ✅ 对齐（日志粒度小差异） |

### 子步骤 5：完成结果日志（browser-use L1232-L1244）

| # | 子步骤 | browser-use | TreeWalker 现状 | 状态 |
|---|---|---|---|---|
| 5a | 触发条件 | `last_result and len>0 and last_result[-1].is_done` | `results and results[-1].is_done` | ✅ 对齐 |
| 5b | 文案 | 统一 `📄 Final Result:`（绿 `\033[32m` / 红 `\033[31m`） | `Task SUCCESS` / `Task FAILED`（标签随结果变） | ❌ **P3-3**（文案） |
| 5c | 内容 | `extracted_content` | `extracted_content or ""` | ✅ 对齐（TreeWalker 多了 `or ""` 防 None） |
| 5d | attachments 输出 | 遍历打印 `👉 Attachment {i+1 if total>1 else ""}: {path}` | **缺失** | ❌ **P1-2** |

---

## 4. P1-1：失败计数语义对齐 browser-use（`len==1`） — 本期落地

### 4.1 现状

`step.py:964-967`：

```python
if results and all(r.error for r in results):
    self.state.consecutive_failures += 1
    logger.debug("Consecutive failures: %d", self.state.consecutive_failures)
    return
```

判定条件是 `all(r.error for r in results)`——**单动作失败**（`len==1` 且 error）和**多动作全失败**（`len>1` 且全部 error）都会递增 `consecutive_failures`。`step.py:951` 注释自标 TODO「Phase 4 will refine failure semantics」。

### 4.2 影响

多动作全失败时也递增 `consecutive_failures`，会比 browser-use **更早**触发 `max_failures` 终止（主循环 `agent.py` 检查 `consecutive_failures >= max_failures`）。一个 3 动作全失败的步骤在 TreeWalker 里计 1 次失败，连续若干次后会触发 force-done / 终止；browser-use 把这类多动作失败**交给循环检测 + replan nudge** 处理，不进失败计数。

### 4.3 browser-use 做法（`service.py:1221-1231`）

```python
# check for action errors - only count single-action steps toward consecutive failures;
# multi-action steps with errors are handled by loop detection and replan nudges instead
if self.state.last_result and len(self.state.last_result) == 1 and self.state.last_result[-1].error:
    self.state.consecutive_failures += 1
    self.logger.debug(f'🔄 Step {self.state.n_steps}: Consecutive failures: {self.state.consecutive_failures}')
    return

if self.state.consecutive_failures > 0:
    self.state.consecutive_failures = 0
    self.logger.debug(f'🔄 Step {self.state.n_steps}: Consecutive failures reset to: {self.state.consecutive_failures}')
```

**设计原理**（browser-use 设计文档 `4-失败计数管理.md`）：
- 单动作失败 → 通常是能力不足或元素定位问题，需累积计数触发终止。
- 多动作失败（含部分/全部）→ 通常是页面状态变化导致后续动作失效，由循环检测和重新规划机制处理更合适。
- 成功步骤 → 立即重置计数器，保证连续性语义。

### 4.4 方案

把计数判定从 `all(r.error)` 收窄为 `len(results)==1 and results[-1].error`；保留并调整 partial-failure 日志分支；保留 success reset 与 early-return。建议改后片段：

```python
# Phase 4 failure semantics (aligned to browser-use service.py:1221-1231):
#   - single-action step with error → count + early return
#   - multi-action step (any failure, partial or all) → do NOT count;
#     loop_detector + replan nudges handle recovery instead
#   - any success → reset counter
if results and len(results) == 1 and results[-1].error:
    self.state.consecutive_failures += 1
    logger.debug("Consecutive failures: %d", self.state.consecutive_failures)
    return
if results and len(results) > 1 and any(r.error for r in results):
    # TreeWalker 增强：显式记录多动作失败（含全失败）以便观测，
    # 但按 browser-use 语义不计入 consecutive_failures。
    logger.info(
        "Multi-action step had %d/%d actions failed — not incrementing "
        "consecutive_failures (deferred to loop detection)",
        sum(1 for r in results if r.error), len(results),
    )

# Success → reset failure counter
if self.state.consecutive_failures > 0:
    self.state.consecutive_failures = 0
```

同时更新 `step.py:951` 注释，去掉「Phase 4 will refine failure semantics」TODO，改为指向本方案。

### 4.5 边界与风险

- **异常路径不动**：`_handle_step_error`（`step.py:1137-1148`）在未捕获异常时仍 `consecutive_failures += 1`，与 browser-use `_handle_step_error`（`service.py:1246-1302`）一致，不在本期范围。
- **阈值检查不动**：`agent.py` 主循环的 `consecutive_failures >= max_failures` 判定与 force-done 注入（`step.py:393-410`）保持原样。
- **行为变化须复核既有测试**：现有「连续失败触发终止」类用例若依赖「多动作全失败也计数」，对齐后会不再触发——这正是预期，但需更新用例断言（见 §8）。
- **循环检测兜底能力**：多动作失败改由循环检测处理后，需确认 phase 01 的 nudge 注入（`step.py:208` `get_nudge_message`）能在重复失败时及时介入；当前 `loop_detector` 模块的页面停滞维度缺失（见 P3-2）是已知短板，建议 loop_detector 模块对齐时一并补强（超本期范围）。

---

## 5. P1-2：完成结果日志补 attachments 输出 + 文案对齐 — 本期落地

### 5.1 现状

`step.py:980-991`：

```python
if results and results[-1].is_done:
    result = results[-1]
    if result.success:
        logger.info(
            "\n\033[32m Task SUCCESS\033[0m\n%s\n",
            result.extracted_content or "",
        )
    else:
        logger.info(
            "\n\033[31m Task FAILED\033[0m\n%s\n",
            result.extracted_content or "",
        )
```

两个问题：(1) 文案 `Task SUCCESS` / `Task FAILED` 与 browser-use 的统一标签 `📄 Final Result:` 不一致；(2) **不输出 attachments**——用户在任务完成时看不到产出了哪些文件。

### 5.2 影响

TreeWalker 已通过变体 B（`_attach_downloads_to_done_results`，`step.py:942-943`）把会话下载挂到 done 结果的 `attachments`，数据已就位，但完成日志不展示 → 可观测性缺失，下载类任务的产出文件对用户不可见。

### 5.3 browser-use 做法（`service.py:1232-1244`）

```python
if self.state.last_result and len(self.state.last_result) > 0 and self.state.last_result[-1].is_done:
    success = self.state.last_result[-1].success
    if success:
        self.logger.info(f'\n📄 \033[32m Final Result:\033[0m \n{self.state.last_result[-1].extracted_content}\n\n')
    else:
        self.logger.info(f'\n📄 \033[31m Final Result:\033[0m \n{self.state.last_result[-1].extracted_content}\n\n')
    if self.state.last_result[-1].attachments:
        total_attachments = len(self.state.last_result[-1].attachments)
        for i, file_path in enumerate(self.state.last_result[-1].attachments):
            self.logger.info(f'👉 Attachment {i + 1 if total_attachments > 1 else ""}: {file_path}')
```

设计要点：统一标签靠 ANSI 颜色区分成功/失败；attachments 多个带序号、单个不带（`i + 1 if total_attachments > 1 else ""`）。

### 5.4 方案

文案改为 `📄 Final Result:`（绿/红），并在 `extracted_content` 输出后追加 attachments 遍历。建议改后片段：

```python
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
            logger.info("👉 Attachment %s: %s", i + 1 if total > 1 else "", file_path)
```

### 5.5 边界与风险

- 纯日志输出，无状态变更，低风险。
- `result.attachments` 来自变体 B 的 `_attach_downloads_to_done_results`（去重合并，`step.py:57-73`），不会重复打印同一文件。
- 日志格式变化可能影响下游解析日志的脚本（若有）；本仓库未见此类消费者，可接受。

---

## 6. P3-1：下载检测架构差异（`available_file_paths` 概念） — 仅文档说明，不大改

### 6.1 差异

| 维度 | browser-use | TreeWalker |
|---|---|---|
| 检测时机 | sense + post 各一次（`_check_and_update_downloads`，动作前后各一次） | 仅 sense 一次（`_prepare_context` 的 `consume_completed_downloads`） |
| 检测机制 | `_last_known_downloads` 快照对比 + set 差集（增量） | `consume_completed_downloads` 清空式 consume（session 缓冲区 `_completed_downloads`） |
| 落地目标 | 刷新独立 `available_file_paths`（供 `file_upload` 等动作引用） | `state.downloaded_files` + done `attachments`（变体 B）+ `download_notice` 喂 LLM |
| 容错 | try/except → debug 日志 | sense 阶段异常由上层处理 |

### 6.2 结论

**功能等价**：TreeWalker 仍能让 LLM 获知新文件（`download_notice` 注入上下文），done 结果仍携带产出文件（变体 B）。`available_file_paths` 作为独立「可用文件列表」概念，被 `state.downloaded_files`（持久列表）+ done `attachments`（结果绑定）替代。

**不建议本期引入 post_process 第二次下载刷新**：
- 收益不匹配复杂度（TreeWalker 的 sense 阶段 consume 已在下步 LLM 决策前捕获下载，时序上不丢）。
- phase 03 已剔除 `file_system` / `available_file_paths`（见 03 文档「实施范围」），本期保持一致。
- 若未来发现「同一步内动作触发下载、需当步即用」的场景，再评估引入。

文档记录差异与理由即可，不动代码。

---

## 7. P3-2：loop_detector 模块差异 — 交叉引用，超出本期范围

### 7.1 差异（已核 `loop_detector.py` vs browser-use `views.py` / `service.py:1496-1528`）

| 维度 | browser-use | TreeWalker | 影响 |
|---|---|---|---|
| 哈希归一化 | 按动作类型语义归一化（search→sorted token 集合 / click→`index` / type→`index\|text` / scroll→`direction\|index` / navigate→`url`） | `name:json(params 去 text/clear)` | 同义动作可能哈希不同（如 click 同一 index 不同次），影响重复检测灵敏度 |
| 页面状态指纹 | `record_page_state(url, dom_text, element_count)`（三维） | `record_page(url)`（仅 URL） | 页面停滞检测粒度粗 |
| nudge 维度 | `get_nudge_message` 同时看 `max_repetition_count` 与 `consecutive_stagnant_pages` | `get_nudge_message` **只看动作重复**（`recent_urls` 记了但 `get_nudge_message` 从不读） | 页面停滞（URL 不变但 DOM 变/不变）无法触发 nudge |
| 窗口大小 | 20 | 15 | 略短 |
| nudge 阈值 | （browser-use 内部） | 5/8/12 三档递进 | TreeWalker 自有设计 |

### 7.2 范围界定

post_process 的**调用点**（`record_action` + 豁免集合）已对齐（见 §3 子步骤 3 的 3a-3d）。上述差异落在：
- **phase 01**：`record_page` 调用（`step.py:188`）+ nudge 注入（`step.py:208` `get_nudge_message`）。
- **loop_detector 模块本身**（`loop_detector.py`）。

本文档仅交叉引用，**建议另开独立文档或纳入 phase 01 复盘**，不在本期展开。P1-1 把多动作失败交给循环检测后，loop_detector 的兜底能力更显重要——这是 P3-2 后续优先处理的依据。

---

## 8. 测试策略

| gap | 文件 | 操作 | 用例 |
|---|---|---|---|
| **P1-1** | `tests/test_step_error_handling.py`（及既有 post_process / 失败计数测试） | 扩展 | (1) 单动作 error → `consecutive_failures` +1 且 early-return（不打印完成日志）；(2) **多动作全失败 → 不再 +1**（关键回归点）；(3) 多动作 partial → 不 +1、打 info 日志；(4) 成功 → reset 0；(5) 多动作全失败连续 N 步 → 不触达 `max_failures` 终止（改由循环检测） |
| **P1-2** | 同上 / log 相关测试 | 新增 | (1) done+success → 绿色 `📄 Final Result:`；(2) done+fail → 红色 `📄 Final Result:`；(3) done 携带 1 个 attachment → `👉 Attachment: path`（无序号）；(4) 携带 ≥2 个 → `👉 Attachment 1: ...` / `👉 Attachment 2: ...` |

**回归守护**：
- 落地后跑 `uv run python -m pytest tests/ -x -v` 全过；覆盖率仍 >85%（CLAUDE.md 要求）。
- **P1-1 改变多动作全失败的终止行为**，需复核既有「连续失败触发终止 / force-done 注入」类用例（`step.py:393-410` 相关测试）是否仍符预期，必要时更新断言。

---

## 9. 暂缓 / 剔除项与理由

| 项 | 决定 | 理由 |
|---|---|---|
| `available_file_paths` 概念 + post_process 第二次下载刷新 | **剔除** | 架构差异、功能已等价（`state.downloaded_files` + 变体 B + `download_notice`）；phase 03 已剔除同类项 |
| loop_detector 模块归一化 / 页面停滞指纹 / nudge 双维度 / 窗口 20 | **暂缓** | 落在 phase 01（record_page 调用 + nudge 注入）+ 模块本身，另开文档；P1-1 落地后优先级提升 |
| 异常路径 `_handle_step_error` 的 `consecutive_failures` 递增 | **不动** | 与 browser-use 一致，非本期范围 |
| 主循环 `max_failures` 阈值检查 / force-done 注入 | **不动** | 在 `agent.py` / `step.py:393-410`，非 post_process 范围 |

---

## 10. 实施路线图与本期范围

| 优先级 | 项 | 本期 | 复杂度 | 价值 | 依赖 |
|---|---|---|---|---|---|
| **P1-1** | 失败计数语义对齐 `len==1` | ✅ 落地 | 中（改判定 + 调 partial 分支 + 更新注释 + 测试） | 高（修正终止行为分歧，消除自标 TODO） | 无；落地后凸显 P3-2 |
| **P1-2** | 完成日志补 attachments + 文案对齐 | ✅ 落地 | 低（纯日志） | 中（可观测性，下载任务产出可见） | 无 |
| **P3-1** | 下载检测架构差异 | 📄 仅文档 | — | — | — |
| **P3-2** | loop_detector 模块差异 | ⏸ 暂缓 | 中-高 | 中-高（P1-1 后兜底能力） | 另开文档 / phase 01 复盘 |
| **P3-3** | 完成日志文案 | ✅ 随 P1-2 | — | — | — |

**实施顺序**：先 P1-1（语义改动，影响面大，先改先测）→ 再 P1-2（纯日志，低风险）。

**落地验收**：
- `step.py:964` 判定改为 `len(results)==1 and results[-1].error`，partial 分支条件调整为 `len>1 and any(error)`，注释更新。
- `step.py:980-991` 文案改为 `📄 Final Result:`，追加 attachments 遍历输出。
- 新增/扩展测试覆盖 §8 用例，`uv run python -m pytest tests/ -x -v` 全过，覆盖率 >85%。

---

## 附：落地核对清单

- [ ] P1-1：`step.py:964` 判定从 `all(r.error)` 改为 `len(results)==1 and results[-1].error`
- [ ] P1-1：partial-failure 日志分支条件改为 `len(results) > 1 and any(r.error ...)`，文案补「deferred to loop detection」
- [ ] P1-1：删除 `step.py:951`「Phase 4 will refine failure semantics」TODO，改为指向本方案
- [ ] P1-2：`step.py:980-991` 文案改为 `📄 Final Result:`（绿/红）
- [ ] P1-2：追加 attachments 遍历 `👉 Attachment {i+1 if total>1 else ""}: {path}`
- [ ] 测试：扩展 `test_step_error_handling.py` 覆盖单动作 +1 / 多动作全失败不 +1 / partial 不 +1 / 成功 reset
- [ ] 测试：新增 done + attachments（1 个 / ≥2 个）日志输出用例
- [ ] 回归：`uv run python -m pytest tests/ -x -v` 全过，覆盖率 >85%
- [ ] 复核既有「失败终止 / force-done」用例是否仍符预期（P1-1 行为变化）

---

*本文档基于 TreeWalker 当前 master 分支（`_post_process` @ `step.py:922-991`）与 browser-use `_post_process`（`service.py:1207-1244`）的逐行对比，并核对 browser-use 设计文档 `4-后处理.md` 及 `4-后处理内部逻辑\*.md`。落地实施时须以最新代码为准复核行号。*
