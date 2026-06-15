# TreeWalker 引入 multi_act（一次执行多个浏览器动作）实现方案

> 制定时间：2026-06-15
> 参考实现：`D:\dev\git\z_jordon\browser-use\browser_use\agent\service.py:2727-2847`（`multi_act`）
> 参考分析：`D:\dev\git\z_jordon\browser-use\docs\multi_act执行技术分析.md`
> 目标版本：TreeWalker v0.2.x
> 判据：**工业级可用 + 与现有架构最小侵入 + 4 阶段可独立验证**

---

## 一、Context（背景与动机）

### 1.1 当前瓶颈

TreeWalker 当前 agent loop 严格执行「一步一动作」（step.py:490-556 的 `_execute_actions` 只调用一次 `tools.execute()`）。这导致以下问题：

1. **LLM 调用浪费**：填表场景（3 个 input + 1 个 click）需要 4 步、4 次 LLM 调用，每次都要重新看完整 DOM 摘要 + 历史记录。LLM 调用是当前 agent 的最大成本与最大延迟来源。
2. **简单流程被切碎**：`scroll + scroll`、`find_text + find_text`、连续 `click` 等"DOM 状态不变"的简单操作被迫拆成多步，破坏 LLM 的"目标连贯性"。
3. **与 browser-use 的能力差距**：browser-use 通过 `multi_act` 把上述场景压缩到 1 步，单步 token 消耗和延迟可降低 30-60%（实测见 `browser-use/examples/features/initial_actions.py`）。

### 1.2 已预留的关键基础设施（好消息）

| 基础设施 | 位置 | 当前状态 |
|---|---|---|
| `AgentSettings.max_actions_per_step: int = 1` | `src/tree_walker/config.py:63`，注释 `# reserved for future multi-action` | 字段已存在，下游从不读取 |
| `RegisteredAction.terminates_sequence: bool = False` | `src/tree_walker/tools/registry.py:22` | 字段已存在，**仅用于 prompt 文本**（registry.py:186 输出 `[terminates sequence]`），执行链路不读取 |
| 5 个动作已标 `terminates=True` | `src/tree_walker/tools/models.py:138-199`：navigate / search / switch_tab / go_back / evaluate | 元数据齐备 |
| `AgentHistory.result: list[ActionResult]` | `src/tree_walker/agent/views.py:73` | 已是 list 类型，多动作天然兼容 |
| `Tools.execute()` 单动作入口 | `src/tree_walker/tools/actions.py:126-148` | 完全可复用，循环调用即可 |
| `BrowserSession.current_target_id` | `src/tree_walker/browser/session.py:133` | 现成的 tab 切换检测信号 |

**结论**：基础设施已 70% 就位，主要工作量在「执行链路接线」和「LLM I/O schema 适配」。改动量约 6 个文件、~250 行核心代码 + ~200 行测试。

---

## 二、browser-use multi_act 设计要点回顾

详细分析见 `D:\dev\git\z_jordon\browser-use\docs\multi_act执行技术分析.md`，此处仅列示移植到 TreeWalker 时必须保留的核心设计点。

### 2.1 严格串行执行

`for i, action in enumerate(actions):` + 每个 `await self.tools.act(...)` 串行 await。**不并发**。原因：DOM 状态在动作间可能变化，并发会导致 index 失效、CDP 响应顺序不可控、错误因果链断裂。

### 2.2 五道中断门（核心安全机制）

按检查顺序：

| 门 | 触发条件 | 动作 | 代码位置（browser-use） |
|---|---|---|---|
| #1 | 序列中段出现 `done`（`i > 0 and action_data.get('done') is not None`） | 静默 break | service.py:2759-2764 |
| #2 | `result.is_done` 为 True | break | service.py:2808 |
| #3 | `result.error` 非空 | break | service.py:2808 |
| #4 | 静态标志 `registered_action.terminates_sequence == True` | break | service.py:2813-2819 |
| #5 | 运行时 `post_url != pre_url` 或 `post_focus != pre_focus` | break | service.py:2821-2827 |

**核心价值**：保证队列里剩余动作对应的 DOM index 不会失效——这是浏览器自动化与纯文本 LLM 工具调用的本质区别。

### 2.3 异常三分流

| 异常类型 | 处理 | 原因 |
|---|---|---|
| `InterruptedError`（用户 Ctrl+C） | **重抛**，让 `step()` 上层捕获响应停止信号 | 用户中止必须立即生效 |
| `_is_connection_like_error`（浏览器崩溃/CDP 断连） | **重抛**，触发 `_handle_step_error` 重连或停止 | 这类错误不是动作本身的错，需要浏览器层恢复 |
| 其他异常（handler bug、元素不存在、参数错误） | **吞掉** + 追加 `ActionResult(error=...)` + **return**（不是 break） | 语义：绝对不再尝试剩余动作；下一步 LLM 看到 failure 自行 recovery |

### 2.4 动作间停顿

`if i > 0: await asyncio.sleep(self.browser_profile.wait_between_actions)` —— 反爬节奏，模拟人类操作。

### 2.5 prompt 与代码守卫双向同步

prompt 里列出的 "page-changing actions"（`navigate / search / go_back / switch / evaluate`）必须与代码里 `terminates_sequence=True` 的动作**完全一致**。任何修改其中一方的 PR 必须同步另一方。

---

## 三、TreeWalker 当前实现盘点（差距分析表）

| ID | 维度 | 当前现状（文件:行号） | 目标状态 | 改动量 |
|---|---|---|---|---|
| **A** | LLM 输出 schema | `tools/registry.py:80-98`：`action` 是单个对象，含 `name` enum + `params` | 改为 `list`，`maxItems=max_actions`（默认 5），`minItems=1` | ~30 行 |
| **B** | LLM 返回解析 | `llm/client.py:241-250`：`action` 是单个 dict，`result["action"] = action` | 双兼容：接受 list 或单 dict，返回结构同时含 `action`（首元素，向后兼容）和 `actions`（list） | ~25 行 |
| **C** | 执行入口签名 | `agent/step.py:490-556` `_execute_actions` 只调 1 次 `tools.execute`，返回 `[result]` 长度恒为 1 | 改为 `for action in actions:` 循环，应用 5 道中断门 + 异常分流 | ~80 行 |
| **D** | 静态保护层 | `terminates_sequence` 字段存在但执行链路从不读取 | 循环内读 `self.tools.registry.actions[name].terminates_sequence`，True 则 break | 已含在 C |
| **E** | 运行时保护层 | `step.py:548-554` 仅整步结束后对比 URL（仅 debug log，不阻断） | 循环内每个 action 前后对比 `URL` + `current_target_id`，变化则 break | 已含在 C |
| **F** | done 单动作约束 | 无（当前只有 1 个动作，不需要） | 循环开头 `if i > 0 and action_name == 'done': break` | 已含在 C |
| **G** | 动作间停顿 | 无 | 新增 `BrowserSettings.wait_between_actions: float = 0.0`，循环内 `if i > 0: await asyncio.sleep(...)` | ~10 行 |
| **H** | 失败处理 | 单动作失败 → 整个 step 进 `_handle_step_error` | 单动作异常三分流；多动作步中单失败不计 `consecutive_failures` | 已含在 C + step.py:589 修改 |
| **I** | 页面稳定机制 | 全靠 `asyncio.sleep(0.3~0.5)` 硬编码（session.py:380-401） | 新增 `_wait_for_page_settle(timeout=2.0)`（轮询 `document.readyState`），navigate/go_back/switch_tab 后调用 | ~25 行 |
| **J** | system prompt 文案 | `prompts/system_prompt.py:11-12`：`respond with an action`（单数） | 改为「one or more actions」+ 多动作规则（page-changing 必须放最后 / done 单动作 / safe-to-chain 清单） | ~15 行 |
| **K** | observability 事件 | 每步一个 `ToolCallEvent/ToolResultEvent`（step.py:513-546） | 循环内每个 action 独立事件，加 `action_index/total_actions` 字段 | ~15 行 |

**改动量总计**：~250 行核心代码 + ~200 行测试。

---

## 四、整体架构与数据流

### 4.1 数据流变更

```
当前：
  LLM → Anthropic tool_use → action: {name, params}（单 dict）
       → _execute_actions → tools.execute(action_name, params, ...)
       → [single_result]
       → _post_process → _finalize → AgentHistory(result=[single_result])

目标：
  LLM → Anthropic tool_use → action: [{name, params}, {name, params}, ...]（list）
       → _execute_actions → for action in actions:
                            └─ tools.execute(action.name, action.params, ...)
                            └─ 应用 5 道中断门 + 异常分流
       → [result_1, result_2, ..., result_N]（N ≤ max_actions_per_step，可能更短）
       → _post_process → _finalize → AgentHistory(result=[result_1, ..., result_N])
```

### 4.2 历史记录兼容性

**完全兼容，无需改 `AgentHistory` 数据结构**：

- `AgentHistory.result: list[ActionResult]`（views.py:73）已是 list，长度可变（1 到 max_actions_per_step）
- `AgentHistoryList.is_done()`（views.py:96-98）已用 `any(r.is_done for r in self.history[-1].result)` 检查，多结果天然兼容
- `build_state_message`（system_prompt.py:65-138）只需在循环里多打印几个 result，无需结构改动

### 4.3 与现有功能的兼容性

| 现有功能 | 兼容性 | 说明 |
|---|---|---|
| `force_done_on_last_step` / `force_done_after_failure`（step.py:197-211） | ✅ 兼容 | 这两个路径用 `include_actions=["done"]` 限制 enum，list 包装逻辑要覆盖此分支 |
| `output_mode="flash"`（registry.py:101-112） | ✅ 兼容 | flash schema 也需要 `action` 改 list；flash 模式不输出 memory/next_goal，但允许多动作 |
| `output_mode="thinking"`（registry.py:139-144） | ✅ 兼容 | 同上 |
| `loop_detector.record_action`（step.py:586） | ✅ 兼容 | 循环内每个 action 单独调用一次，行为不变 |
| `plan_manager.update_from_model_output`（step.py:579） | ✅ 兼容 | 读 `model_output["action"]`，我们保留该字段指向首元素 |
| `enable_planning` 的 `plan_update` / `current_plan_item`（registry.py:146-155） | ✅ 兼容 | plan 字段与 action list 并列，互不影响 |
| `Tools.execute()` 单动作入口（actions.py:126-148） | ✅ 完全复用 | 循环内直接调用，签名不动 |
| `_LOOP_EXEMPT_ACTIONS = {"wait", "done", "go_back"}`（step.py） | ✅ 兼容 | 循环内按 action name 单独判断豁免 |
| `_NO_URL_CHECK_ACTIONS`（step.py） | ⚠️ 合并 | 该集合的语义会被门 #5（运行时 URL 对比）替代，可在 Phase 3 删除 |

---

## 五、详细实施计划（4 个 Phase）

### Phase 1：核心循环（~1 天）

**目标**：让 LLM 能输出多动作、执行链路能按顺序执行，但不带任何守卫。`max_actions_per_step=1` 时所有现有测试不变。

**改动清单**：

1. **`src/tree_walker/tools/registry.py`**

   `get_tool_schema()` 新增 `max_actions: int = 5` 参数；把 `action_property` 包装为 list：

   ```python
   def get_tool_schema(
       self,
       enable_planning: bool = False,
       page_url: str | None = None,
       output_mode: str = "standard",
       include_actions: list[str] | None = None,
       max_actions: int = 5,   # 新增
   ) -> dict[str, Any]:
       # ... 现有逻辑生成 action_property 不变 ...

       action_list_property = {
           "type": "array",
           "minItems": 1,
           "maxItems": max_actions,
           "description": (
               "One or more actions to execute in order. "
               "Most steps should contain exactly 1 action. "
               "Only chain multiple actions when they operate on the same stable DOM "
               "(e.g. multiple input_text fills before a single click submit)."
           ),
           "items": action_property,
       }

       # 在 properties dict 里把 "action" 的值改为 action_list_property
       # flash / standard / thinking 三个分支同步修改
   ```

2. **`src/tree_walker/llm/client.py`**

   `get_action()` 解析逻辑双兼容（line 241-250）：

   ```python
   raw_action = tool_input.get("action", {})

   # 双兼容：list 或单 dict
   if isinstance(raw_action, list):
       actions_list = raw_action
   elif isinstance(raw_action, dict):
       actions_list = [raw_action]
   else:
       actions_list = [{"name": "done", "params": {"text": "Invalid action type", "success": False}}]

   # 确保每个 action 的 params 字段存在
   for a in actions_list:
       if isinstance(a, dict):
           a.setdefault("params", {})

   result = {
       "evaluation_previous_goal": tool_input.get("evaluation_previous_goal", ""),
       "memory": tool_input.get("memory", ""),
       "next_goal": tool_input.get("next_goal", ""),
       "action": actions_list[0] if actions_list else {},  # 向后兼容（首元素）
       "actions": actions_list,                              # 新字段（完整 list）
   }
   ```

   注：保留 `action` 字段是为了让 `plan_manager`、`loop_detector` 等下游消费者**零改动**继续工作；同时新增 `actions` 字段供 `_execute_actions` 循环使用。

3. **`src/tree_walker/agent/step.py`**

   `_execute_actions` 重写为循环骨架（先不接守卫）：

   ```python
   async def _execute_actions(
       self,
       model_output: dict[str, Any],
       browser_state: BrowserStateSummary,
   ) -> list[ActionResult]:
       if self.state.stopped or self.state.paused:
           return [ActionResult(error="Agent stopped or paused")]

       actions = model_output.get("actions") or [model_output.get("action", {})]
       results: list[ActionResult] = []

       for i, action in enumerate(actions):
           action_name = action.get("name", "done")
           action_params = action.get("params", {})

           # observability 事件移到循环内
           tool_call_id = ""
           tool_start = time.time()
           if self._obs_bus:
               from tree_walker.observability.events import ToolCallEvent
               tool_call_id = uuid.uuid4().hex[:8]
               self._obs_bus.emit(ToolCallEvent(
                   step=self.state.n_steps, session_id=self._obs_session_id,
                   model_call_id=getattr(self, "_current_model_call_id", ""),
                   tool_call_id=tool_call_id, action_name=action_name,
                   params=action_params,
                   action_index=i, total_actions=len(actions),   # 新字段
               ))

           try:
               result = await asyncio.wait_for(
                   self.tools.execute(action_name, action_params, self.browser, browser_state),
                   timeout=self.action_timeout,
               )
           except asyncio.TimeoutError:
               result = ActionResult(error=f"Action timed out after {self.action_timeout}s")
           except Exception as e:
               if _is_connection_error(e):
                   raise
               result = ActionResult(error=f"{type(e).__name__}: {e}")

           results.append(result)

           # ToolResultEvent 发射（每个 action 一个）
           # ...

       return results
   ```

   `_post_process`（step.py:582-586）的 `loop_detector.record_action` 改为循环：

   ```python
   actions = model_output.get("actions") or [model_output.get("action", {})]
   for action in actions:
       action_name = action.get("name", "done")
       action_params = action.get("params", {})
       if action_name not in _LOOP_EXEMPT_ACTIONS:
           self.loop_detector.record_action(action_name, action_params)
   ```

   **失败计数逻辑保持不变**（step.py:589）——Phase 1 阶段仍按"整步 1 个失败"语义；Phase 4 再细化。

4. **`src/tree_walker/prompts/system_prompt.py`**

   SYSTEM_PROMPT 文案修改（line 11-12）：

   ```diff
   - On each step you receive the current page state \
   - (DOM tree with indexed elements) and you respond with an action.
   + On each step you receive the current page state \
   + (DOM tree with indexed elements) and you respond with one or more actions.
   ```

   新增"多动作规则"小节（Phase 1 先放最简版，Phase 2-3 逐步加约束）：

   ```
   ## Multi-action Rules

   1. Most steps should output exactly 1 action.
   2. You may chain up to {max_actions} actions when they operate on the same stable DOM
      (e.g. multiple input_text fills before a click submit, multiple scrolls).
   3. If any action in the list fails or changes the page, remaining actions are skipped —
      you will receive the new page state on the next step.
   ```

5. **`src/tree_walker/config.py`**

   ```diff
   - max_actions_per_step: int = 1  # reserved for future multi-action
   + max_actions_per_step: int = 5  # multi-action per step (browser-use parity)
   ```

6. **`src/tree_walker/observability/events.py`**

   `ToolCallEvent` / `ToolResultEvent` 新增可选字段：

   ```python
   @dataclass
   class ToolCallEvent(...):
       # ... 现有字段 ...
       action_index: int = 0
       total_actions: int = 1
   ```

7. **新增 `tests/test_multi_act.py`**：
   - `test_get_tool_schema_action_is_list`：验证 schema 中 `action` 是 array 类型
   - `test_get_tool_schema_max_items`：验证 `maxItems` 等于传入的 `max_actions`
   - `test_client_parses_action_list`：mock LLM 返回 list，验证 `result["actions"]` 长度正确
   - `test_client_parses_single_action_dict_backward_compat`：mock LLM 返回单 dict，验证 `result["action"]` 和 `result["actions"]` 同时正确
   - `test_execute_actions_loop`：mock `tools.execute` 返回不同 result，验证循环执行
   - `test_max_actions_per_step_1_backward_compat`：`max_actions=1` 时所有现有测试不变

### Phase 2：静态守卫层（~0.5 天）

**目标**：在 Phase 1 的循环上接入"5 道中断门"中的 4 道（done 单动作 / is_done / error / 静态 terminates_sequence）。

**改动清单**：

在 `_execute_actions` 循环开头和结尾插入守卫：

```python
for i, action in enumerate(actions):
    action_name = action.get("name", "done")
    action_params = action.get("params", {})

    # 门 #1：done 仅可作为单动作（i > 0 时遇到 done 直接 break）
    if i > 0 and action_name == "done":
        logger.debug(
            "Done is only allowed as a single action — skipping %d remaining",
            len(actions) - i,
        )
        break

    # ...（执行 + result.append）...

    # 门 #2 / #3：is_done 或 error
    if result.is_done or result.error or i == len(actions) - 1:
        break

    # 门 #4：静态 terminates_sequence
    registered = self.tools.registry.actions.get(action_name)
    if registered and registered.terminates_sequence:
        logger.info(
            "Action '%s' terminates sequence — skipping %d remaining",
            action_name, len(actions) - i - 1,
        )
        break
```

**测试用例**：

- `test_done_only_as_single_action`：list = `[click, done]`，验证只执行 click，done 被跳过
- `test_terminates_sequence_breaks_loop`：list = `[navigate, click]`，验证只执行 navigate，click 被跳过
- `test_error_breaks_loop`：list = `[click, click]`，第一个 click 返回 error，验证只执行第一个

### Phase 3：运行时守卫层（~0.5 天）

**目标**：加入门 #5（运行时 URL/target_id 漂移检测），并替换硬编码 sleep。

**改动清单**：

1. **`src/tree_walker/browser/session.py` 新增 `_wait_for_page_settle`**：

   ```python
   async def _wait_for_page_settle(self, timeout: float = 2.0, poll_interval: float = 0.1) -> None:
       """Poll document.readyState until complete or timeout."""
       deadline = time.time() + timeout
       while time.time() < deadline:
           try:
               result = await self.client.send.Runtime.evaluate(
                   {"expression": "document.readyState", "returnByValue": True},
                   session_id=self.current_session_id,
               )
               state = result.get("result", {}).get("value", "")
               if state == "complete":
                   return
           except Exception:
               pass
           await asyncio.sleep(poll_interval)
   ```

   在 `navigate`（session.py:380-388）/ `go_back`（session.py:389-401）/ `switch_tab`（session.py:745-755）末尾把 `await asyncio.sleep(0.3~0.5)` 替换为 `await self._wait_for_page_settle()`。

2. **`_execute_actions` 循环内加入门 #5**：

   ```python
   for i, action in enumerate(actions):
       # ...（门 #1，前置采样，执行，门 #2/#3/#4）...

       # 门 #5：运行时 URL / target_id 漂移
       pre_url = await self.browser.get_current_url()
       pre_target_id = self.browser.current_target_id

       # ...（执行 action）...

       if not result.error and action_name not in _NO_URL_CHECK_ACTIONS:
           post_url = await self.browser.get_current_url()
           post_target_id = self.browser.current_target_id
           if post_url != pre_url or post_target_id != pre_target_id:
               logger.info(
                   "Page changed after '%s' (url: %s→%s, tab: %s→%s) — skipping %d remaining",
                   action_name, pre_url, post_url, pre_target_id, post_target_id,
                   len(actions) - i - 1,
               )
               break
   ```

3. **`src/tree_walker/config.py`**：

   ```python
   @dataclass
   class BrowserSettings:
       # ... 现有字段 ...
       wait_between_actions: float = 0.0   # 新增；0 = 不停顿，对齐现有节奏
       page_settle_timeout: float = 2.0    # 新增
   ```

**测试用例**：

- `test_url_change_breaks_loop`：mock 第一个 click 后 `get_current_url` 返回不同值，验证后续动作被跳过
- `test_target_id_change_breaks_loop`：mock `current_target_id` 变化（模拟新 tab 打开），验证后续被跳过
- `test_wait_for_page_settle_completes`：mock `Runtime.evaluate` 返回 `complete`，验证立即返回
- `test_wait_for_page_settle_timeout`：mock 一直返回 `loading`，验证 `timeout` 后返回

### Phase 4：异常分流与失败语义（~0.5 天）

**目标**：把"异常三分流"和"多动作步的失败计数"做对。

**改动清单**：

1. **`_execute_actions` 循环异常分流**：

   ```python
   try:
       # 单动作执行
       result = await asyncio.wait_for(
           self.tools.execute(action_name, action_params, self.browser, browser_state),
           timeout=self.action_timeout,
       )
       results.append(result)

       # 门 #2 / #3
       if result.is_done or result.error or i == len(actions) - 1:
           break

       # 门 #4 / #5（见 Phase 2/3）

   except asyncio.TimeoutError:
       results.append(ActionResult(error=f"Action timed out after {self.action_timeout}s"))
       return results   # 注意：return 而非 break，对齐 browser-use
   except InterruptedError:
       raise              # 用户中止，向上抛
   except Exception as e:
       if _is_connection_error(e):
           raise          # 浏览器崩溃，向上抛触发重连
       results.append(ActionResult(error=f"{type(e).__name__}: {e}"))
       return results     # 其他异常，return
   ```

   注：Phase 1 写的 try/except 块在 Phase 4 改为上面的形态——主要差异是把"返回单 result"改成"append 错误 result 后 return 整个 results"。

2. **`_post_process` 失败计数细化**（step.py:588-592）：

   ```python
   # 旧逻辑：
   # if results and len(results) == 1 and results[-1].error:
   #     self.state.consecutive_failures += 1
   #     return

   # 新逻辑：区分单动作步与多动作步
   if results and all(r.error for r in results):
       # 整步全部失败（含单动作步）→ 计数
       self.state.consecutive_failures += 1
       logger.debug("Consecutive failures: %d", self.state.consecutive_failures)
       return
   if results and any(r.error for r in results) and not all(r.error for r in results):
       # 多动作步部分失败 → 不计数，由 loop_detector + replan 处理
       logger.info(
           "Multi-action step had partial failure (%d/%d actions failed) — not incrementing consecutive_failures",
           sum(1 for r in results if r.error), len(results),
       )
   ```

3. **加入 `wait_between_actions`** 到循环：

   ```python
   for i, action in enumerate(actions):
       if i > 0 and self._settings.browser.wait_between_actions > 0:
           await asyncio.sleep(self._settings.browser.wait_between_actions)
       # ...（其他守卫与执行）
   ```

**测试用例**：

- `test_timeout_returns_partial_results`：mock 第一个 action timeout，验证 `results = [first_success_or_not_yet, timeout_error]`，无第三个 action
- `test_interrupted_error_propagates`：mock action 抛 `InterruptedError`，验证向上抛
- `test_connection_error_propagates`：mock action 抛 connection-like error，验证向上抛
- `test_partial_failure_not_counted`：list = `[success, error]`，验证 `consecutive_failures` 不变
- `test_all_failure_counted`：list = `[error, error]`，验证 `consecutive_failures += 1`
- `test_wait_between_actions_sleeps`：`wait_between_actions=0.5`，验证 action 间有 sleep

---

## 六、Prompt 改造对照

### 6.1 当前 system_prompt.py（节选）

```
You are a browser automation agent. ... On each step you receive the current
page state (DOM tree with indexed elements) and you respond with an action.
```

### 6.2 目标 system_prompt.py（节选）

```
You are a browser automation agent. ... On each step you receive the current
page state (DOM tree with indexed elements) and you respond with one or more actions.

## Multi-action Rules

1. **Most steps should output exactly 1 action.** Multi-action is an optimization,
   not a default — use it only when the chained actions clearly operate on the same
   stable DOM.

2. **Chainable combinations** (page state does not change between them):
   - `input_text + input_text + ... + click` → fill multiple form fields, then submit
   - `scroll + scroll + ...` → scroll further down the same page
   - `find_text + find_text + ...` → search for multiple terms on the same page
   - `extract + extract + ...` → extract multiple pieces from the same page

3. **Page-changing actions must be the LAST action in your list.** If you place them
   earlier, subsequent actions will be skipped (the DOM changes):
   - `navigate`, `search`, `switch_tab`, `go_back`, `evaluate`
   - These are marked `[terminates sequence]` in the action list above.

4. **`done` must be a single action.** Never combine `done` with other actions — it
   signals task completion and the rest would be meaningless.

5. **`click` is potentially page-changing** (it can navigate or open a new tab). The
   runtime detects this and stops the sequence. Do not place critical actions after a
   `click` unless you are certain the click stays on the same page.

6. **One clear goal per step.** Do not try multiple different paths in one step.
```

### 6.3 Flash / Thinking 模式

- **Flash**：保留多动作能力，但 prompt 不展开规则集（依赖 LLM 训练先验）。仅声明 `max_actions` 上限。
- **Thinking**：完整规则集，与 standard 模式一致。

### 6.4 代码守卫与 prompt 守卫的同步矩阵

| 守卫来源 | prompt 提及 | 代码位置 | 同步要求 |
|---|---|---|---|
| navigate/search/switch_tab/go_back/evaluate terminates | §3「Page-changing actions must be LAST」 | `models.py:138-199` 标注 + `step.py` 门 #4 | 新增动作必须同步两边 |
| done 单动作 | §4「done must be a single action」 | `step.py` 门 #1 | 不可解耦 |
| click 潜在换页 | §5「click is potentially page-changing」 | `step.py` 门 #5（URL/target_id 对比） | 不可解耦 |

---

## 七、测试策略

### 7.1 单元测试矩阵

| 类别 | 文件 | 覆盖点 |
|---|---|---|
| Schema 生成 | `tests/test_action_registry.py`（扩展） | `action` 是 array；`maxItems` 生效；flash/standard/thinking 三模式都正确 |
| LLM 解析 | `tests/test_multi_act.py`（新增） | 单 dict 向后兼容；list 正常解析；空 list 兜底 |
| 循环执行 | `tests/test_multi_act.py` | 多 action 顺序执行；observability 事件每个 action 一个 |
| 门 #1 done 单动作 | `tests/test_multi_act.py` | `[click, done]` 只执行 click |
| 门 #2 is_done | `tests/test_multi_act.py` | done 在 i==0 时正常返回 |
| 门 #3 error | `tests/test_multi_act.py` | 第一个 error 终止 |
| 门 #4 terminates_sequence | `tests/test_multi_act.py` | navigate 后续被跳过 |
| 门 #5 URL 漂移 | `tests/test_multi_act.py` | mock URL 变化触发 break |
| 门 #5 target_id 漂移 | `tests/test_multi_act.py` | mock tab 切换触发 break |
| 异常分流 | `tests/test_multi_act.py` | timeout / InterruptedError / connection_error / 普通异常 各自正确路径 |
| 失败计数 | `tests/test_multi_act.py` | 单失败计数；多失败计数；部分失败不计数 |
| 页面稳定 | `tests/test_browser_session.py`（扩展） | `_wait_for_page_settle` complete/timeout 两条路径 |
| 向后兼容 | 全部现有测试 | `max_actions_per_step=1` 时所有现有行为不变 |

### 7.2 集成测试

新建 `examples/multi_act_demo.py`，演示三种典型场景：

1. **多字段表单**：登录页（用户名 + 密码 + 登录按钮），对比启用前后 step 数
2. **连续滚动**：长列表页连续滚 3 次，对比 step 数
3. **多动作后终止**：`navigate + click`，验证 click 被正确跳过（navigate 是 terminates）

集成测试**不强制要求真实浏览器**——可用 mock + 已录制的 CDP 响应回放。

### 7.3 覆盖率目标

按 CLAUDE.md 要求 > 85%。重点覆盖：
- `_execute_actions` 的所有分支（5 道门 + 4 类异常）
- `get_tool_schema` 的 3 种 output_mode × list 包装组合
- `_wait_for_page_settle` 的 complete / timeout 路径

### 7.4 回归保护

`max_actions_per_step=1` 必须作为 CI 中的一个独立 job 跑全套 `tests/`，确保新代码不破坏现有用户的单动作模式。

---

## 八、风险与缓解

| 风险 | 严重度 | 缓解措施 |
|---|---|---|
| **LLM 输出格式变更导致已有对话缓存失效** | 中 | client.py 保留单 dict 兼容路径；老对话回放不受影响（model_output 的 `action` 字段始终存在） |
| **失败语义变化影响 consecutive_failures** | 高 | `_post_process` 三分支明确处理（all-error / partial-error / no-error）；用单元测试固化语义 |
| **Tab 切换检测精度低于 browser-use**（current_target_id 是 tab 级，不是 DOM 元素级） | 中 | 文档明确说明此限制；未来可在 `BrowserSession` 加 `last_interacted_index` 字段做 DOM 元素级检测（Phase 5 候选） |
| **LLM 误用多动作导致性能下降**（输出多动作但每个都触发换页守卫，导致大量丢弃） | 中 | system prompt §1 强调"多动作是优化不是默认"；observability 事件可统计"action 丢弃率"，超阈值时考虑回退到 max_actions=1 |
| **observability JSONL 格式变化破坏下游分析** | 低 | `action_index/total_actions` 是新增字段，老字段（`action_name` = 首 action）保留；下游解析 JSON 不受影响 |
| **`_wait_for_page_settle` 替换 sleep 后行为变化** | 中 | `page_settle_timeout` 默认 2.0s，比原 sleep(0.5) 长，可能让单步耗时上升；可通过 `BrowserSettings.page_settle_timeout=0.5` 回退到接近原行为 |
| **flash 模式 LLM 训练数据不含 TreeWalker 的多动作规则** | 中 | flash 模式 prompt 至少声明 `max_actions` 上限；初期建议 flash 模式默认 `max_actions=1`（仅 standard/thinking 启用多动作） |

---

## 九、实施时间表

| Phase | 内容 | 预估工时 | 产出 |
|---|---|---|---|
| Phase 1 | 核心循环（schema + 解析 + 循环骨架 + prompt 文案 + 配置） | 1 天 | `max_actions_per_step=5` 可用，无守卫 |
| Phase 2 | 静态守卫层（门 #1/#2/#3/#4） | 0.5 天 | done 单动作 / terminates_sequence / is_done / error 都正确终止 |
| Phase 3 | 运行时守卫层（门 #5 + `_wait_for_page_settle`） | 0.5 天 | URL/target_id 漂移检测；navigate/go_back/switch_tab 后等稳定 |
| Phase 4 | 异常分流 + 失败语义细化 | 0.5 天 | timeout/Interrupted/connection 各自路径；partial failure 不计数 |
| 测试 + 文档 + PR review | 单元测试 + 集成 demo + 本文档对齐实现 | 0.5 天 | 覆盖率 > 85%；CI 全绿 |
| **总计** | | **~3 个工作日** | |

---

## 十、验收清单

实施完成后，逐项确认：

- [ ] `uv run python -m pytest tests/test_multi_act.py -v` 全部通过
- [ ] `uv run python -m pytest tests/ -v` 全套测试通过（含 `max_actions_per_step=1` 回归）
- [ ] `uv run python -m pytest tests/ --cov=tree_walker --cov-report=term-missing` 覆盖率 > 85%
- [ ] `examples/multi_act_demo.py` 真实浏览器跑通三个场景，step 数显著低于启用前
- [ ] `tools/registry.py` 的 `get_tool_schema` 生成的 schema 经 Anthropic API 接受（无 schema 验证错误）
- [ ] `agent/step.py` 的 `_execute_actions` 循环对 5 道中断门、4 类异常都有单元测试覆盖
- [ ] `prompts/system_prompt.py` 的多动作规则集与代码守卫完全一致（navigate/search/switch_tab/go_back/evaluate 五个动作清单在 prompt 与 `models.py` 同步）
- [ ] `docs/multi_act实现方案.md`（本文档）与最终代码一致，行号引用准确

---

## 附录 A：browser-use multi_act 源码精简版（蓝本）

完整版见 `D:\dev\git\z_jordon\browser-use\browser_use\agent\service.py:2727-2847`。

```python
async def multi_act(self, actions: list[ActionModel]) -> list[ActionResult]:
    results: list[ActionResult] = []
    total_actions = len(actions)

    for i, action in enumerate(actions):
        action_data = action.model_dump(exclude_unset=True)
        action_name = next(iter(action_data.keys())) if action_data else 'unknown'

        # 门 #1
        if i > 0 and action_data.get('done') is not None:
            break

        if i > 0:
            await asyncio.sleep(self.browser_profile.wait_between_actions)

        try:
            await self._check_stop_or_pause()

            pre_url = await self.browser_session.get_current_page_url()
            pre_focus = self.browser_session.agent_focus_target_id

            result = await self.tools.act(action=action, browser_session=self.browser_session, ...)
            results.append(result)

            # 门 #2 / #3
            if results[-1].is_done or results[-1].error or i == total_actions - 1:
                break

            # 门 #4
            registered = self.tools.registry.registry.actions.get(action_name)
            if registered and registered.terminates_sequence:
                break

            # 门 #5
            post_url = await self.browser_session.get_current_page_url()
            post_focus = self.browser_session.agent_focus_target_id
            if post_url != pre_url or post_focus != pre_focus:
                break

        except Exception as e:
            if isinstance(e, InterruptedError):
                raise
            if self._is_connection_like_error(e):
                raise
            results.append(ActionResult(error=f'{type(e).__name__}: {e}'))
            return results

    return results
```

## 附录 B：TreeWalker 与 browser-use 实现差异说明

| 差异点 | browser-use | TreeWalker 本方案 | 原因 |
|---|---|---|---|
| action schema | Pydantic `list[ActionModel]`（Union of 24 个 params 模型） | Anthropic tool_use `array` of `{name, params}` 对象 | TreeWalker 已采用 Anthropic 原生 tool_use + enum，避免 Pydantic Union 复杂性 |
| 运行时漂移检测字段 | URL + `agent_focus_target_id`（DOM 元素级） | URL + `current_target_id`（tab 级） | TreeWalker 当前未维护 DOM 元素级 focus；tab 级已覆盖主要场景 |
| 异常归一化位置 | `tools.act()` 内部 | `_execute_actions` 的 try/except | TreeWalker 的 `Tools.execute()` 已有部分归一化（返回 `ActionResult`），剩余在编排层处理 |
| 动作间停顿配置 | `browser_profile.wait_between_actions` | `BrowserSettings.wait_between_actions` | TreeWalker 配置层级与 browser-use 不同 |
| 历史回放 | `AgentHistory.model_output: AgentOutput`（Pydantic 模型） | `AgentHistory.model_output: dict`（dict） | TreeWalker 保留 dict 更灵活，无需改动数据结构 |
