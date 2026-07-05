# LLM 决策对齐 browser-use 方案

> 阶段：agent step 5 阶段流程的**第二阶段「LLM 决策」**（`_get_next_action`）
> 对照源：`D:\dev\git\learn_agent\browse-use\docs\agent-core\step内部流程\2-LLM决策.md` + `2-LLM决策内部逻辑\` 四份子文档；`D:\dev\git\z_jordon\browser-use\browser_use\agent\service.py`
> 本文档只交付**方案**，不含代码落地；落地为后续独立任务。
>
> **实施范围（2026-07-05 确定）**：本期落地 **P0-1 + P0-2 + P1-3 + P3**；**P1-4（步骤回调）暂缓**（与已有 EventBus 重复）；**P2-7（session_id 透传）剔除**（Anthropic 走 `cache_control` 不适用）；**P2-5（超时记录输入消息）视精力**。其余章节保留作为对齐全景与未来参考。

---

## 1. 背景与范围

TreeWalker 的目标是「所有逻辑都对齐 browser-use」。Agent step 被拆为 5 阶段流水线（`src/tree_walker/agent/step.py`）：

| 阶段 | 方法 | 职责 |
|---|---|---|
| 1. Sense | `_prepare_context()` | 调 LLM 前组装全部输入（系统提示词、历史、工具、页面状态、注入提示） |
| 2. Think | `_get_next_action()` | 调 LLM 拿决策 |
| 3. Act | `_execute_actions()` | 执行动作 |
| 4. Post | `_post_process()` | 更新状态 |
| 5. Final | `_finalize()` | 历史/日志/步数 |

**本文档范围**：仅第二阶段 `_get_next_action()`（`step.py:412-484`）及其调用链（`_get_action_with_retry` `step.py:486-531`、`LLMClient.get_action` `client.py:166-294`）。browser-use 对应 `_get_next_action`（`service.py:1164-1198`）+ `_get_model_output_with_retry`（`service.py:1657-1691`）+ `get_model_output`（`service.py:1932-1968`）+ `_handle_post_llm_processing`（`service.py:1693-1723`）。**不覆盖** Sense/Act/Post/Final 四阶段（阶段 1 已在 01 文档展开，阶段 3/4/5 后续文档展开）。

经逐行核对，TreeWalker 在此阶段**绝大多数子步骤已对齐甚至更优**，但仍存在 **2 个核心安全 gap（P0）+ 1 个调试体验 gap（P1）+ 1 个文档清晰度项（P3）**。下文逐项给出代码级方案。

---

## 2. 现状精确锚点（已核对真实代码）

| 维度 | TreeWalker 现状 | 文件:行 |
|---|---|---|
| 清除上一步状态 | `self.state.last_model_output = None; self.state.last_result = None`，在 `_prepare_context` 之后、LLM 调用之前 | `step.py:128-129` |
| 取输入消息 | `trimmed = self._trim_messages()`（含 `_strip_type` 剥除 `_type` 内部键的边界处理） | `step.py:418`，`step.py:254-259` |
| 入口 stop 检查 | `if self.state.stopped or self.state.paused: return False`（LLM 调用**之前**） | `step.py:122-123` |
| LLM 调用超时 | `asyncio.wait_for(self._get_action_with_retry(trimmed), timeout=self.llm_timeout)`；超时 `raise TimeoutError(...)` | `step.py:434-443` |
| 超时文案 | `f"LLM call timed out after {self.llm_timeout}s. Keep your output concise."` | `step.py:440-443` |
| observability 上报 | `ModelCallEvent`（调用前）/ `ModelResultEvent`（调用后），**仅 `self._obs_bus` 存在时** emit | `step.py:426-432`、`445-453` |
| 空动作重试 | 首次空 → 追加澄清消息重试一次 → 仍空 → `_FALLBACK_DONE_OUTPUT` 兜底 | `step.py:486-531` |
| 参数校验重试 | `_validate_params_or_retry`：用注册动作 Pydantic `param_model` 校验，最多重试 `_PARAM_VALIDATION_MAX_RETRIES` 次（**TreeWalker 独有，browser-use 无**） | `step.py:533-574` |
| assistant 消息写历史 | 每步 `self.messages.append({"role":"assistant", ...})`，`enable_message_typing` 时标 `TYPE_ASSISTANT` | `step.py:455-466` |
| 结构化日志 | `log_response(evaluation, memory, next_goal, action_name, action_params, step, logger)` | `step.py:468-480` |
| URL 缩短/还原 | `_shorten_urls_in_messages`（≥100 字符 → `[uN]`）/ `_restore_urls_in_output`（递归还原） | `client.py:72-123` |
| 敏感数据过滤/还原 | `_filter_sensitive_in_messages` / `_restore_sensitive_in_output` | `client.py:125-164` |
| LLM 结构化输出 | `tools=[tool_schema]` + `tool_choice={"type":"tool","name":"agent_response"}`，解析降级链（tool_use → 文本 JSON → 重试 → fallback done） | `client.py:180-240` |
| multi-action 归一化 | `raw_action` 是 list → `actions_list`；dict → `[raw_action]`；同时暴露 `action`（首位）与 `actions`（列表） | `client.py:245-284` |
| Fallback LLM | `_try_switch_to_fallback`：单向切换，靠调用方 `except (RateLimitError, APIError)` 决定是否切 | `client.py:58-70`、调用点 `189-192` |
| **动作硬截断** | **缺失**。`max_actions_per_step` 仅在 system prompt 文本告知 LLM；全局 grep `[:max_actions` 在 `src/` 下零命中 | `step.py:319`、`agent.py:172,177`（仅 prompt） |
| **Post-LLM stop 检查** | **缺失**。`_get_next_action` 内 LLM 返回后到 assistant 消息 append 之间无检查 | — |
| **对话持久化** | **缺失**。无每步 input messages + model output 文本 dump | — |

**三个决定性结论：**

1. **`_finalize` 天然兼容 `model_output=None`**（`step.py:860` `if model_output is not None:`）→ P0-1 让 `_get_next_action` 返回 `None` 不会让终结化崩溃，但 `_step` 主循环必须加 `if model_output is None: return False` 守卫，否则 `_execute_actions`（`step.py:657`）和 `_post_process`（`step.py:806`）会对 `None` 取 `.get` 崩溃。
2. **`max_actions_per_step` 全链路仅用于「告知 LLM」** → 无任何运行时硬上限；LLM 忽略 `maxItems` 时 `_execute_actions` 照单全收全部动作。这是 P0-2 的实证依据。
3. **client 层 fallback 已通过异常继承链覆盖 401/402/429/5xx** → P3 行为无 gap，仅文档/测试清晰度问题。

---

## 3. browser-use 12 子步骤 vs TreeWalker 全景对照

| # | 子步骤 | browser-use（service.py） | TreeWalker 现状 | 状态 |
|---|---|---|---|---|
| 0 | 清除上一步状态 | L1058-1059 `last_model_output=None; last_result=None` | `step.py:128-129` | ✅ 对齐 |
| 1 | 获取输入消息 | L1166 `_message_manager.get_messages()` | `step.py:418` `_trim_messages()` | ✅ 对齐 |
| 2 | 带超时 LLM 调用 | L1172-1186 `asyncio.wait_for(..., timeout=llm_timeout)` | `step.py:434-443` | ✅ 对齐 |
| 2a | URL 缩短 | L1935 `_process_messsages_and_replace_long_urls_shorter_ones` | `client.py:72-104` `[uN]` | ✅ 对齐 |
| 2b | LLM 推理（结构化输出） | L1942 `ainvoke(output_format=AgentOutput, session_id=...)` | `client.py:181` `messages.create(tools=[tool_schema], tool_choice=...)` | ✅ 对齐（机制不同：browser-use 走 `output_format`，TreeWalker 走 tool_use；语义等价） |
| 2c | URL 还原 | L1946-1947 `_recursive_process_all_strings_inside_pydantic_model` | `client.py:106-123` `_restore_urls_in_output` | ✅ 对齐 |
| 2d | **动作截断** | L1950-1951 `parsed.action = parsed.action[:max_actions_per_step]` | **缺失** | ❌ **P0-2** |
| 2e | Fallback LLM 切换 | L1962-1968 `_try_switch_to_fallback_llm`（检查 401/402/429/500/502/503/504） | `client.py:189-192` `except (RateLimitError, APIError)` | ⚠️ **P3**（行为等价，见 §6） |
| 3 | 模型输出重试 | L1657-1691 空动作→澄清→兜底 `setattr(done, {success:False,...})` | `step.py:486-531` `_FALLBACK_DONE_OUTPUT` | ✅ 对齐（TreeWalker 额外有参数校验重试，领先） |
| 4 | 存储模型输出 | L1188 `state.last_model_output = model_output` | 隐式（response 直接返回，`_post_process` L791 回写 `last_model_output`） | ✅ 对齐 |
| 5a | **Post-LLM stop 检查（×2）** | L1191（存储后/回调前）+ L1197（回调后/提交历史前） | **缺失**（仅 `_step` 入口 L122-123 检查） | ❌ **P0-1** |
| 5b | 步骤回调 + 对话持久化 | L1693-1723 `register_new_step_callback`（sync/async 自适应）+ `save_conversation_path` | EventBus `ModelResultEvent`（等价回调）；**无对话持久化** | ⚠️ **P1-3**（持久化缺失）/ P1-4（回调暂缓，见 §7） |

> **覆盖率**：12 个子步骤中 **8 项 ✅ 对齐**、**2 项 ❌ 真 gap（P0）**、**1 项 ⚠️ 调试 gap（P1-3）**、**1 项 ⚠️ 文档清晰度（P3）**。无遗漏。

---

## 4. P0-1：Post-LLM 双重 stop/pause 检查

### 4.1 现状
`_step` 仅在入口（`step.py:122-123`，LLM 调用**前**）检查 stop/pause；`_execute_actions` 入口（`step.py:654`）也有检查。但 `_get_next_action`（`step.py:412-484`）内部，从 `asyncio.wait_for` 返回（L443）到 `self.messages.append(assistant_msg)`（L466）之间**无任何 stop 检查**。

### 4.2 影响
用户在 LLM 调用期间按停止（Ctrl+C / 外部 stopped 信号）：
- `asyncio.wait_for` 因 stop 而被取消，或 LLM 调用刚好返回但用户已按停止
- TreeWalker 仍会执行 L445-466：emit `ModelResultEvent`、`self.messages.append(assistant_msg)`
- **过期 assistant 消息进入 `self.messages` 历史** → 下一步 LLM 看到污染历史（一个「已停止」任务的决策被当成正常历史）
- 即便 `_execute_actions` L654 入口检查会阻止执行，**历史污染已经发生**

browser-use 用**两次** `_check_stop_or_pause()` 守卫：L1191（存储输出后、回调前）+ L1197（回调后、提交历史前，注释明确 "check again if Ctrl+C was pressed before we commit the output to history"）。

### 4.3 browser-use 做法
```python
# service.py L1188-L1197
self.state.last_model_output = model_output          # L1188 存储输出
await self._check_stop_or_pause()                    # L1191 第 1 次：存储后/回调前
await self._handle_post_llm_processing(...)          # L1194 回调 + 持久化
await self._check_stop_or_pause()                    # L1197 第 2 次：提交历史前
```

### 4.4 方案
在 `_get_next_action`（`step.py:412-484`）内加两次检查，并在 `_step` 加 None 守卫：

**插入点 A**（`step.py:443` 之后，`asyncio.wait_for` 返回后、emit `ModelResultEvent` 前）：
```python
response = await asyncio.wait_for(...)               # L434-438 现有
# ★ 第 1 次 stop 检查：LLM 期间用户停止 → 不上报事件、不写历史
if self.state.stopped or self.state.paused:
    logger.debug("Step %d: stopped/paused during LLM call — discarding output", self.state.n_steps)
    return None
if self._obs_bus:                                    # L445 现有 emit ModelResultEvent
    ...
```

**插入点 B**（`step.py:466` 之前，`self.messages.append(assistant_msg)` 前）：
```python
# ★ 第 2 次 stop 检查：回调/日志期间用户停止 → 输出已拿到但不污染历史
if self.state.stopped or self.state.paused:
    logger.debug("Step %d: stopped/paused before committing assistant message", self.state.n_steps)
    return response
# Record assistant message for conversation history  # L455 现有
assistant_msg = {...}
self.messages.append(assistant_msg)                  # L466 现有
```

**`_step` 配合**（`step.py:131-133`）：
```python
model_output = await self._get_next_action(browser_state, state_message)
if model_output is None:                             # ★ P0-1：LLM 期间停止
    return False
results = await self._execute_actions(model_output, browser_state)
self._post_process(results, model_output)
```

### 4.5 关键决策
**返回 `None` 而非抛 `InterruptedError`**。理由：
- `InterruptedError` 在 TreeWalker 里是 **Phase 4 执行期**中断信号（`_execute_actions` L714、`_handle_step_error` 的三分流），语义是「动作执行被用户中断」。
- Post-LLM 停止是「决策刚拿到还没执行」，抛 `InterruptedError` 会与执行期语义混淆，且走 `_handle_step_error` 的 InterruptedError 分支（不计 failure）反而绕路。
- 返回 `None` + `_step` 早退是最小侵入，`_finalize`（L860 `if model_output is not None:`）天然跳过历史记录，行为正确。

### 4.6 边界与风险
- **`None` 语义扩散**：`_execute_actions`（L657 `model_output.get(...)`）、`_post_process`（L806 `model_output.get(...)`）会对 `None` 崩溃。**必须**靠 `_step` 的 `if model_output is None: return False` 守卫拦在前面。落地时先补全路径测试（见 §8）。
- **`finally` 仍执行**：`_step` 的 `finally: self._finalize(...)`（L142-143）仍会跑，但 `_finalize` L860 守卫保证 `model_output=None` 时不写 history、不发 `StepEndEvent` 之外的副作用。`results` 是 `[]`（L116 初值），`_finalize` 对空 results 安全。
- **不破坏 observability**：第 1 次检查在 emit `ModelResultEvent` **之前**，比 browser-use 更严（browser-use 是存储后才检查，事件可能已发）。TreeWalker 选择「停止时连事件都不发」，语义更干净。

---

## 5. P0-2：动作硬截断

### 5.1 现状
`max_actions_per_step`（默认 5，`config.py:77`）仅出现在：
- system prompt 模板的 `{max_actions}` 占位（`step.py:319`、`agent.py:172,177`）——**告知 LLM**
- tool schema 的 `maxItems`（由 registry 生成）

全局 grep `[:max_actions` / `action[:max` / `actions[:max` 在 `src/` 下**零命中**——**无任何运行时硬上限**。`_execute_actions`（`step.py:657`）`actions = model_output.get("actions") or [model_output.get("action", {})]` 后直接 `for i, action in enumerate(actions)` 遍历全部。

### 5.2 影响
LLM 忽略 `maxItems` 约束（小模型常见）返回 6+ 个动作时：
- `_execute_actions` 照单全收，执行过量动作
- 中间动作（如 `navigate`/`click <a>`）改变 DOM/URL，后续动作操作在 stale DOM 上 → guard #5（runtime drift，L753-770）会截断，但前 N 个动作已执行且页面状态已乱
- 浪费 token、动作预算

browser-use 在解析后立即硬截断：`service.py:1950-1951 parsed.action = parsed.action[:max_actions_per_step]`。

### 5.3 方案
**新增方法** `StepPipeline._truncate_actions`，放在 `step.py:485` 附近（`_get_next_action` 与 `_get_action_with_retry` 之间）：

```python
def _truncate_actions(self, response: dict[str, Any]) -> dict[str, Any]:
    """Hard-cap actions to max_actions_per_step (browser-use service.py:1950-1951).

    The system prompt only *tells* the LLM the limit; this is the runtime
    safety net for when small models ignore maxItems. Mutates response in
    place, keeping `action` (first) and `actions` (list) consistent.
    """
    actions = response.get("actions")
    if not isinstance(actions, list):
        return response
    if len(actions) <= self.max_actions_per_step:
        return response
    dropped = actions[self.max_actions_per_step:]
    kept = actions[:self.max_actions_per_step]
    response["actions"] = kept
    response["action"] = kept[0] if kept else response.get("action", {})
    dropped_names = [a.get("name", "?") for a in dropped if isinstance(a, dict)]
    logger.warning(
        "Step %d: LLM emitted %d actions (max %d) — truncated, dropped: %s",
        self.state.n_steps, len(actions), self.max_actions_per_step, dropped_names,
    )
    return response
```

**调用点**：`_get_next_action` 内，emit `ModelResultEvent`（L445-453）**之前**、第 1 次 stop 检查（P0-1 插入点 A）**之后**：
```python
# ★ P0-1 第 1 次 stop 检查
if self.state.stopped or self.state.paused:
    return None
# ★ P0-2 硬截断（在事件上报前，保证 ModelResultEvent 的 action_name 与实际执行一致）
response = self._truncate_actions(response)
if self._obs_bus:                                    # L445 现有 emit ModelResultEvent
    action = response.get("action", {})
    ...
```

### 5.4 边界与风险
- **不放 client.py**：截断是 agent 行为约束（依赖 `max_actions_per_step` 配置 + step number 日志），client 层只负责「忠实解析 LLM 输出」。放在 step 层与 `_execute_actions` 同文件，职责清晰。
- **截断丢 done**：若 LLM 把 `done` 放在超过 max 的位置（如第 6 个），截断会丢 done。但这本就是 LLM 违规（system prompt 明确「done 必须单动作且放最后」），截断 + warning 是合理反馈；下一步 LLM 看到 warning 会自我纠正。
- **与 multi-action guards 协作**：截断在 guards 之前，guards（done-in-midpoint/terminates/drift）仍正常工作，只是处理的动作数已被压到 max 内。
- **`action` 与 `actions` 一致性**：截断后必须同步更新 `action`（首位），否则 `log_response`（L472 读 `response["action"]`）和 `ModelResultEvent`（L447 读 `response["action"]`）会上报未截断的首位——但若首位本身就在 max 内则无影响。同步更新是防御性写法。

---

## 6. P1-3：对话持久化 `save_conversation_path`

### 6.1 现状
无每步对话文本 dump 机制。已有两类相关机制：
- **`rerun-history`**（`RerunMixin`，`config.py` `rerun_history_dir="rerun-history"`）：结构化重放数据（`AgentHistory` 序列化），面向**机器重放**。
- **observability `JsonlRecorder`**（`enable_observability=True` 时）：结构化事件流（StepStart/ModelCall/ModelResult/ToolCall/...），面向**指标分析**。

两者都不是「每步 input messages + model output 的人类可读文本 dump」。browser-use 的 `save_conversation_path`（`service.py:1713-1723`）写 `{path}/conversation_{id}_{step}.txt`，面向**离线人工审计 LLM 决策质量**。

### 6.2 影响
调试 LLM 决策问题时（如「为什么这步选了 click 而不是 input」），缺一份「这步发给 LLM 的完整消息 + LLM 返回的完整输出」的可读快照，难以离线复盘。

### 6.3 browser-use 做法
```python
# service.py L1713-L1723
if self.settings.save_conversation_path and self.state.last_model_output:
    conversation_dir = Path(self.settings.save_conversation_path)
    conversation_filename = f'conversation_{self.id}_{self.state.n_steps}.txt'
    target = conversation_dir / conversation_filename
    await save_conversation(input_messages, self.state.last_model_output, target, encoding)
```

### 6.4 方案
**配置**（`config.py` AgentSettings，与 `rerun_history_dir` 同区）：
```python
save_conversation_path: str = ""  # browser-use parity; per-step input+output text dump
```
env 加载：`config.py` `load_settings` 附近（读 env 的区，参照 `rerun_history_dir` 的读法）加 `AGENT_SAVE_CONVERSATION_PATH`。

**新增方法** `StepPipeline._save_conversation`，放在 `_truncate_actions` 附近：
```python
def _save_conversation(self, messages: list[dict[str, Any]], model_output: dict[str, Any]) -> None:
    """Dump this step's input messages + model output to a text file (browser-use service.py:1713-1723).

    Human-readable audit artifact — distinct from rerun-history (machine replay)
    and JsonlRecorder (event stream). Best-effort: IO errors are logged and swallowed.
    """
    if not self._save_conversation_path:
        return
    try:
        path = Path(self._save_conversation_path)
        path.mkdir(parents=True, exist_ok=True)
        target = path / f"conversation_{self._obs_session_id}_{self.state.n_steps}.txt"
        lines = [f"=== Step {self.state.n_steps} ==="]
        for m in messages:
            role = m.get("role", "?")
            content = m.get("content", "")
            lines.append(f"\n--- {role} ---\n{content}")
        lines.append(f"\n--- model_output ---\n{json.dumps(model_output, ensure_ascii=False, indent=2)}")
        target.write_text("\n".join(lines), encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to save conversation for step %d: %s", self.state.n_steps, e)
```

**调用点**：`_get_next_action` 末尾（`step.py:482` 之后，`log_response` 之后、`return response` 之前）：
```python
log_response(...)                                    # L468-480 现有
self._save_conversation(trimmed, response)           # ★ P1-3
self._current_model_call_id = model_call_id          # L482 现有
return response
```

### 6.5 边界与风险
- **用 `trimmed`（已 `_strip_type`）写盘**：`trimmed = self._trim_messages()`（L418）已经在边界剥除了 `_type` 内部键，直接用它可避免内部元数据泄漏到 dump。**不要**用 `self.messages`（含 `_type`）。
- **IO 阻塞**：每步几 KB 同步写入可接受；若 concern 可包 `asyncio.to_thread`，但 `_save_conversation` 是 fire-and-forget，同步写 + `try/except` 已足够。**绝不**因落盘失败影响主流程。
- **敏感数据**：`trimmed` 已经过 `_filter_sensitive_in_messages`（在 `client.py:get_action` 内）吗？**注意**：`_filter_sensitive_in_messages` 是在 `client.get_action` 内**就地修改传入的 messages**，而 `trimmed` 是 `_trim_messages` 的返回值。需确认 `trimmed` 进 `get_action` 后是否已被脱敏——落地时核对：若 `get_action` 脱敏了 `trimmed`，则 `_save_conversation(trimmed, ...)` 写的是脱敏后的（安全）；若没脱敏（因为 `trimmed` 是副本），则 dump 可能含真实敏感值。**落地核对项**：确保 dump 的是脱敏后的 messages，或在 `_save_conversation` 内补一次脱敏。
- **与 `enable_message_typing` 无关**：`trimmed` 已 `_strip_type`，无论开关如何 dump 都干净。
- **三者并存**：`save_conversation_path`（人读 dump）/ `rerun_history_dir`（机器重放）/ `JsonlRecorder`（事件流）用途不同，可同时开启。

---

## 7. P3：Fallback 状态码文档清晰度

### 7.1 现状
`_try_switch_to_fallback`（`client.py:58-70`）不看 HTTP 状态码，靠调用方 `except (RateLimitError, APIError) as e`（`client.py:189`）决定是否调用。browser-use 的 `_try_switch_to_fallback_llm`（`service.py:1970-2011`）显式检查 `retryable_status_codes = {401, 402, 429, 500, 502, 503, 504}`。

### 7.2 复核结论：行为无 gap
Anthropic SDK 的异常继承链：
- `RateLimitError`（429）→ 是 `APIStatusError` → 是 `APIError` 子类 ✅
- `AuthenticationError`（401）→ 是 `APIStatusError` → 是 `APIError` 子类 ✅
- `APIStatusError`（5xx、402 等）→ 是 `APIError` 子类 ✅

所以 `except (RateLimitError, APIError)` 已覆盖 browser-use 的全部 retryable 状态码。**行为等价**，只是 TreeWalker 不显式列状态码。

### 7.3 方案（仅文档 + 测试，不改逻辑）
- **注释**：`client.py:189` 上方补注释说明覆盖范围：
  ```python
  # Covers browser-use retryable status codes via the SDK exception hierarchy:
  #   RateLimitError (429), AuthenticationError (401), APIStatusError (5xx, 402, ...)
  #   are all subclasses of APIError.
  except (RateLimitError, APIError) as e:
  ```
- **测试**：`tests/test_llm_client.py` 加 `test_authentication_error_triggers_fallback`——mock client 抛 `AuthenticationError`，断言切换到 fallback 并递归重试。

---

## 8. 测试策略

沿用 `tests/test_multi_act.py::_make_agent` 的 fake-agent + 直接调用 `StepPipeline._method(agent, ...)` 模式，配 `caplog` / `tmp_path` / `monkeypatch`。

| gap | 文件 | 操作 | 用例 |
|---|---|---|---|
| **P0-1** | `tests/test_step_error_handling.py` | 扩展 `TestPostLlmStopCheck` | (1) LLM 调用期间置 `stopped=True` → `_get_next_action` 返回 `None`；(2) 置 `paused=True` → 返回 `None`；(3) 正常 → 返回 response dict；(4) 返回 `None` 时 `self.messages` 不增加（无 assistant 污染）；(5) `_step` 收到 `None` → 返回 `False` 且不调用 `_execute_actions`/`_post_process` |
| **P0-2** | `tests/test_multi_act.py` | 扩展 `TestActionTruncation` | (1) actions 数 ≤ max → 不截断、无 warning；(2) actions 数 > max → 截断到 max 且 warning 含被丢弃动作名；(3) 截断后 `action`=首位、`actions`=截断列表；(4) 正好 = max 边界 → 不截断；(5) 空 `actions` → 不崩；(6) 单 action → 不截断；(7) done 在截断区外 → 被丢弃（验证 warning） |
| **P1-3** | `tests/test_conversation_persistence.py`（**新建**） | 全新 | (1) `save_conversation_path=""` → 不写盘；(2) 正常 → 文件名 `conversation_{session}_{step}.txt`、内容含 input messages + model_output；(3) IO 异常（只读目录）→ 静默 warning、不抛；(4) dump 的 messages 不含 `_type` 键（`_strip_type` 验证）；(5) `enable_message_typing=False` 时仍正确 dump；(6) 多步不覆盖（step 号不同） |
| **P3** | `tests/test_llm_client.py` | 扩展 | (1) `test_authentication_error_triggers_fallback`：mock 抛 `AuthenticationError` → 切换 fallback + 递归重试 |

**回归守护**：P0-1 改动 `_get_next_action` 返回类型（`dict` → `dict | None`），必须重跑 `tests/test_multi_act.py` 全套（Phase 2 guards）+ `tests/test_message_typing.py`（五步循环模拟）确认无回归。

---

## 9. 暂缓 / 剔除项与理由

| 项 | 决定 | 理由 |
|---|---|---|
| **P1-4 步骤回调 `register_new_step_callback`** | **暂缓** | TreeWalker 已有 EventBus + 6 细粒度事件（`StepStartEvent`/`ModelCallEvent`/`ModelResultEvent`/`ToolCallEvent`/`ToolResultEvent`/`StepEndEvent`），其中 `ModelResultEvent` 的 payload（step / action_name / next_goal）与 browser-use 的 `register_new_step_callback(browser_state, model_output, n_steps)` 等价。再补一个简单回调会造成**双入口**、与 EventBus 的 sync handle 路径重复维护。需要「极简回调」场景（如快速 Gradio demo）再评估——届时可考虑加一个 `register_on_model_result(callback)` 薄封装走 EventBus，而非另起一套。 |
| **P2-5 超时记录输入消息** | **视精力** | browser-use 超时时把 `input_messages` 发到 Langner 可观测平台（`@observe` 装饰器副作用）。TreeWalker 可改为：超时时 `logger.warning` 仅记 message 数量与末条 content 长度（**不写 content 本身**，避免敏感数据进日志），observability 开时 emit 一个 `ModelTimeoutEvent`。用户需要完整输入快照时直接启用 P1-3 的 `save_conversation_path`。价值边际，视精力做。 |
| **P2-7 `session_id` 透传** | **剔除** | browser-use 给 `ainvoke` 传 `session_id=self.session_id` 用于多轮 KV 缓存连续性。但 Anthropic 的 prompt caching 走 `cache_control` block（在 system/state 消息上标记 ephemeral cache），**不走 `session_id`**。TreeWalker 走 anthropic-compatible API（`base_url=https://open.bigmodel.cn/api/anthropic`），`session_id` 参数无对应语义。API 风格差异，**不适用**。 |
| **`_broadcast_model_state` 无条件广播** | **非 gap** | browser-use 在 `get_model_output`（`service.py:1953-1957`）无条件 `_broadcast_model_state`。TreeWalker 条件 emit（`if self._obs_bus:`，`step.py:445`）比无条件广播**更优**——关闭 observability 时零开销，开启时通过 `ModelResultEvent` 提供更结构化的 payload。保留现状。 |
| **空动作三条件判断** | **非 gap** | browser-use 用 `not action or not isinstance(action, list) or all(a.model_dump() == {} for a in action)`（`service.py:1664-1668`）。TreeWalker `_is_valid_action`（`step.py:507` 调用）语义更严，已覆盖这三种空情形。保留现状。 |
| **参数校验重试** | **TreeWalker 优势** | `_validate_params_or_retry`（`step.py:533-574`）用注册动作的 Pydantic `param_model` 校验参数并把错误反馈给 LLM 重试，是 browser-use **没有的**机制。TreeWalker 在此**领先**，无需对齐。 |

---

## 10. 实施路线图与本期范围

参照 01 文档「本期仅落地 P0 + P1」的显式范围标注风格：

| 优先级 | 项 | 本期 | 复杂度 | 价值 | 依赖 |
|---|---|---|---|---|---|
| **P0-1** | Post-LLM 双重 stop 检查 | ✅ 落地 | 低（加 2 处 if + 1 处 None 守卫） | 高（历史污染防护） | 无 |
| **P0-2** | 动作硬截断 | ✅ 落地 | 低（1 个新方法 + 1 处调用） | 中高（运行时安全网） | 无 |
| **P1-3** | 对话持久化 | ✅ 落地 | 中（配置 + 方法 + 脱敏核对） | 中（调试体验） | 无 |
| **P3** | Fallback 状态码文档 | ✅ 落地 | 极低（注释 + 1 测试） | 低（可读性） | 无 |
| P2-5 | 超时记录输入消息 | 视精力 | 低 | 低 | 可复用 P1-3 |
| P1-4 | 步骤回调 | 暂缓 | 中 | 低（与 EventBus 重复） | — |
| P2-7 | session_id 透传 | 剔除 | — | — | 不适用 |

**三个 P0/P1 改动彼此独立**，可分别实现、分别回滚、分别测试。建议落地顺序：P0-2（最独立、零回归风险）→ P3（顺手）→ P1-3（独立）→ P0-1（需回归 `test_multi_act` + `test_message_typing`）。

**落地验收**：每项改动后跑 `uv run python -m pytest tests/ -x -v`，确保全绿且覆盖率 > 85%（项目规范）。

---

## 附：落地核对清单

- [ ] P0-2：`grep -r "[:max_actions" src/` 应在 `step.py` 出现一次（新增的 `_truncate_actions`）
- [ ] P0-1：`_get_next_action` 返回类型注解改为 `dict[str, Any] | None`；`_step` 加 `if model_output is None: return False`
- [ ] P0-1：重跑 `tests/test_multi_act.py` + `tests/test_message_typing.py` 全绿
- [ ] P1-3：核对 `trimmed` 进 `client.get_action` 后是否被 `_filter_sensitive_in_messages` 脱敏；若否，`_save_conversation` 内补脱敏
- [ ] P1-3：dump 文件抽查无 `_type` 内部键、无未脱敏敏感值
- [ ] P3：`tests/test_llm_client.py::test_authentication_error_triggers_fallback` 通过
