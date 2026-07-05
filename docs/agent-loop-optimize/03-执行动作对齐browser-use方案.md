# 执行动作（Act）对齐 browser-use 方案

> 阶段：agent step 5 阶段流程的**第三阶段「执行动作」**（`_execute_actions`）
> 对照源：`D:\dev\git\learn_agent\browse-use\docs\agent-core\step内部流程\3-执行动作.md` + `3-执行动作内部逻辑\{1-初始化准备,2-遍历执行动作,3-页面变化保护机制}.md`；`D:\dev\git\z_jordon\browser-use\browser_use\agent\service.py`（`_execute_actions` L1199-L1205、`multi_act` L2710-L2828）
> 本文档只交付**方案**，不含代码落地；落地为后续独立任务。
>
> **实施范围（2026-07-05 确定）**：本期落地 **P0-1（循环内 per-action stop 检查）+ P1-2（per-action 执行日志 + 秘密脱敏，含 `log_response` 顺手修复）+ P3-3（执行上下文 7 参数架构说明，仅文档）**；**`file_system` / `available_file_paths` 剔除**（架构差异、当前无 handler 实际需要）；**抽独立 `_check_stop_or_pause()` / `_log_action()` 方法剔除**（inline 即可，与 02 风格一致）。其余章节保留作为对齐全景与未来参考。

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

**本文档范围**：仅第三阶段 `_execute_actions()`（`step.py:714-865`），即 browser-use 的 `_execute_actions`（`service.py:1199-1205`）+ `multi_act`（`service.py:2710-2828`）。**不覆盖** Sense/Think/Post/Final（阶段 1/2 已在 01/02 文档展开，阶段 4/5 后续文档展开）。

经逐行核对，TreeWalker 在此阶段**绝大多数子步骤已对齐甚至更优**（5 道中断门、runtime drift 检测、per-action `action_timeout` 均领先 browser-use），但仍存在 **1 个 P0 安全 gap（循环内 stop 检查缺失）+ 1 个 P1 调试/安全 gap（per-action 日志 + 日志秘密泄露）+ 1 组 P3 架构说明项（`tools.act` 7 参数）**。下文逐项给出代码级方案。

---

## 2. 现状精确锚点（已核对真实代码）

| 维度 | TreeWalker 现状 | 文件:行 |
|---|---|---|
| 入口 stop 检查 | `if self.state.stopped or self.state.paused: return [ActionResult(error=...)]` | `step.py:747-748` |
| **循环内 stop 检查** | **缺失**。`for i, action in enumerate(actions)` 循环体（L754-865）再无 stop/pause 检查 | — |
| actions 归一化 | `actions = model_output.get("actions") or [model_output.get("action", {})]` | `step.py:750` |
| `action_name` try 前提取 | `action_name = action.get("name", "done")`，在 inner try（L799）之前 | `step.py:755` |
| Done 单动作守卫（#1） | `if i > 0 and action_name == "done": break` | `step.py:761-766` |
| 动作间延迟 | `if i > 0 and self.wait_between_actions > 0: await asyncio.sleep(...)` | `step.py:770-771` |
| observability 上报 | `ToolCallEvent`（执行前 L778）/ `ToolResultEvent`（执行后 L822），仅 `self._obs_bus` 存在时 emit | `step.py:775-784`、`820-828` |
| pre-action 状态采样 | `pre_action_url`（i==0 复用 browser_state.url，否则 `browser.get_current_url()`）+ `pre_target_id = browser.current_target_id` | `step.py:786-797` |
| **per-action 超时** | `asyncio.wait_for(self.tools.execute(...), timeout=self.action_timeout)`（**TreeWalker 独有，browser-use 无**） | `step.py:800-803` |
| 异常三分诊 | `TimeoutError`→error result；`InterruptedError`→raise；其他→`_is_connection_error` 判定 raise 或 error result | `step.py:804-815` |
| 终止门 #2/#3 | `if result.is_done or result.error or i == total - 1: break` | `step.py:832` |
| 终止门 #4（静态） | `registered.terminates_sequence` → break（navigate/search/go_back/switch_tab/evaluate） | `step.py:838-844` |
| 终止门 #5（runtime drift） | URL 或 `current_target_id` 变化 → break（**领先 browser-use**：覆盖 `<a>` 跳转/JS 重定向/新标签） | `step.py:846-863` |
| **per-action 执行日志** | **缺失**。循环内仅 obs 事件 + error/timeout 警告，无 `[i/total] name: params` 人类可读行 | — |
| 整步决策日志 | `log_response(evaluation, memory, next_goal, action_name, action_params, ...)`，在 `_get_next_action` 末尾打印一次 | `step.py:509`、`log_formatter.py:44` |
| **日志秘密脱敏** | **缺失**。`log_response` 经 `format_action_params` 原样打印 params；params 在 LLM 输出解析时已被 `_restore_sensitive_in_output` 还原为真值 → 终端日志泄露真密码 | `log_formatter.py:80`、`client.py:145,244,297` |
| history 秘密脱敏 | `_redact_history_data`：按 `_SENSITIVE_ACTION_FIELDS` 用 `redact_sensitive_string` 过滤 input_text/search/extract 的 text/query | `views.py:117-162` |
| `tools.execute` 参数 | `(action_name, params, browser, browser_state)` 4 参数；handler 拿 `(params, browser)` | `actions.py:420-442` |
| `extract` 工具接线 | `self.tools._extract_llm`（默认=主 llm）/ `self.tools._extraction_schema`，`_action_extract` 内消费 | `agent.py:63-68`、`actions.py:1051,1068` |

**三个决定性结论：**

1. **循环内 stop 检查缺失是 P0-1（02 期 LLM 阶段）的对称漏洞**。02 期已为 `_get_next_action` 加了 post-LLM 双重 stop 检查（`step.py:456,497`），但 Act 阶段的多动作循环里 action 1→2→… 之间无任何检查。叠加 `wait_between_actions` sleep + per-action `action_timeout`（可达数十秒），用户停止后最坏要等剩余序列跑完才在下一步边界停下。
2. **`action_params` 在进入 `_execute_actions` 时已是真值**（client 层 `_restore_sensitive_in_output` 已还原），故任何执行期日志/异常 echo 若原样打印 params 都会泄密。`log_response`（决策期）当前就有此问题，是 pre-existing 隐患；新增 per-action 执行日志必须同步治理。
3. **`tools.act` 7 参数中 4 项已通过别的机制满足**（`page_extraction_llm`/`extraction_schema` 走 Tools 实例属性、`sensitive_data` 走 client 还原 + history 脱敏、`file_system` 走 `allowed_*_paths` 白名单），**不构成真 gap**，仅架构与 browser-use 不同。

---

## 3. browser-use 子步骤 vs TreeWalker 全景对照

### 3.1 初始化准备（`multi_act` 入口，service.py L2719-L2733）

| # | 子步骤 | browser-use | TreeWalker 现状 | 状态 |
|---|---|---|---|---|
| 0a | 入口 None 守卫 | L1199 `if last_model_output is None: raise ValueError` | 上游 `_step` L134 `if model_output is None: return False` 拦截 | ✅ 对齐（位置上移） |
| 0b | `results = []` / `total = len(actions)` | L2719-2720 | `step.py:751-752` | ✅ 对齐 |
| 0c | `cached_selector_map` 三层容错 | L2722-2733 `dict(selector_map)` + try/except 降级 `{}` | 经 `browser_state` 整体传入；handler 通过 `_get_element_by_index`（`actions.py:446`）读 `selector_map` | ✅ 对齐（更干净：传整 state 而非拆出 map） |

### 3.2 遍历执行动作（循环体，service.py L2735-L2828）

| # | 子步骤 | browser-use | TreeWalker 现状 | 状态 |
|---|---|---|---|---|
| a | `action_name` try 前提取 | L2737-2738 `model_dump(exclude_unset=True)` | `step.py:755` `action.get("name","done")` | ✅ 对齐 |
| b | Done 单动作守卫 | L2740-2745 `if i>0 and done: break` | `step.py:761-766` | ✅ 对齐 |
| c | 动作间延迟 | L2748-2750 `asyncio.sleep(wait_between_actions)` | `step.py:770-771` | ✅ 对齐 |
| d | **停止/暂停检查** | L2753 `await _check_stop_or_pause()` | **缺失**（仅入口 L747 检查一次） | ❌ **P0-1** |
| e | **动作日志** | L2756 `_log_action(action, name, i+1, total)` | **缺失**（仅 obs 事件） | ❌ **P1-2** |
| f | pre-action 状态采样 | L2759-2760 `pre_url` + `pre_focus=agent_focus_target_id` | `step.py:786-797` `pre_action_url` + `pre_target_id=current_target_id` | ✅ 对齐（focus 字段名不同但语义等价，guard #5 已生效） |
| g | 执行动作 | L2762-2770 `tools.act` **7 参数** | `step.py:800-803` `tools.execute` 4 参数 + `asyncio.wait_for` 超时 | ⚠️ **P3-3**（参数架构差异；超时是 TreeWalker 独有优势） |
| h | Demo 模式日志 | L2772-2785 `_demo_mode_log`（error/done） | EventBus `ToolResultEvent`（`step.py:822`） | ✅ 对齐（机制不同，等价） |
| i | 追加结果 | L2787 `results.append(result)` | `step.py:817` | ✅ 对齐 |
| j | 终止门（done/error/last） | L2789-2790 | `step.py:832` | ✅ 对齐 |
| k | Layer 1 静态 `terminates_sequence` | L2794-2800 | `step.py:838-844` | ✅ 对齐 |
| l | Layer 2 运行时页面变化 | L2803-2808 URL/focus 对比 | `step.py:846-863` URL/`current_target_id` 对比 | ✅ 对齐 |
| m | 异常三分诊 | L2810-2826 Interrupted/connection/other | `step.py:804-815` Timeout/Interrupted/connection/other | ✅ 对齐（TreeWalker 多显式 `TimeoutError` 分支） |

> **覆盖率**：16 个子步骤中 **13 项 ✅ 对齐**、**2 项 ❌ 真 gap（P0-1 / P1-2）**、**1 项 ⚠️ 架构差异（P3-3，非 gap）**。TreeWalker 在 f/l（runtime drift 双层保护，browser-use Layer 2 同款）与 g（per-action 超时）上**领先** browser-use。

---

## 4. P0-1：循环内 per-action stop/pause 检查

### 4.1 现状
`_execute_actions`（`step.py:714-865`）只在**入口**检查一次 stop/pause（L747-748，返回一个 error result）。`for i, action in enumerate(actions)` 循环体（L754-865）内**再无任何 stop/pause 检查**——既不在 `wait_between_actions` sleep 之后，也不在 `tools.execute` 之前。

### 4.2 影响
多动作步骤（如 `[click, input_text, click]`）中，action 1 跑完、action 2 即将开始时用户按停止：
- `_execute_actions` 不会中途停下，继续执行 action 2、action 3…
- 叠加 `wait_between_actions` sleep（L770-771）+ 每 action `action_timeout`（L802，可达数十秒），最坏要等整个剩余序列跑完，才在**下一步边界**（`_step` L124）停下
- 这是 02 期 P0-1 给 `_get_next_action` 修过的**对称漏洞**——LLM 阶段已可 ms 级响应停止，Act 阶段仍敞开到下一步

browser-use 在每个 action 执行**前**（sleep 之后、`_log_action` 之前）调 `await self._check_stop_or_pause()`（service.py L2753）。

### 4.3 browser-use 做法
```python
# service.py L2748-L2753
if i > 0:                                                              # L2748
    await asyncio.sleep(self.browser_profile.wait_between_actions)     # L2750
try:
    await self._check_stop_or_pause()                                  # L2753 ★
    await self._log_action(action, action_name, i + 1, total_actions)  # L2756
    ...
```
`_check_stop_or_pause()`（service.py L1001-L1019）满足条件时 **raise InterruptedError**，传播到 `step()` 的 `_handle_step_error`，不计 failure、优雅退出。

### 4.4 方案
在 `_execute_actions` 循环体内、`wait_between_actions` sleep（L771）**之后**、observability 事件（L773）**之前**插入一次 stop 检查，**raise InterruptedError**（与 browser-use 语义一致）：

```python
# step.py L768-L773 现有
        # Phase 4: anti-detection cadence between chained actions.
        if i > 0 and self.wait_between_actions > 0:
            await asyncio.sleep(self.wait_between_actions)

        # ★ P0-1：per-action stop/pause 检查（对齐 browser-use service.py:2753）。
        # 放在 inner try（L799）之前，使 raise 直达 _step 外层 try（L120）→
        # _handle_step_error 分支 1（L1064，不计 failure）。partial results 随
        # raise 丢弃——与 browser-use 一致（用户主动停 = 不要剩余结果）。
        if self.state.stopped or self.state.paused:
            logger.debug(
                "Step %d: stopped/paused before action %d/%d — aborting sequence",
                self.state.n_steps, i + 1, total,
            )
            raise InterruptedError

        tool_call_id = ""                               # L773 现有
        ...
```

**为什么 raise 而非 break+return**：
- browser-use 的 `_check_stop_or_pause()` 就是 raise InterruptedError（service.py L1001-1019），`multi_act` 的 `except` 捕获后 re-raise（L2812），partial results 同样丢弃。TreeWalker 对齐此语义。
- raise 走 `_handle_step_error` 分支 1（`step.py:1064-1066`，仅 `logger.warning("Agent interrupted mid-step")` + return，**不计 consecutive_failures**），符合「用户主动停止非失败」。
- break+return partial results 会让 `_post_process` 处理半截 results（其中一个 action 的副作用已发生但无结果记录），语义混乱。

**复用现有处理链，无需新增 `_check_stop_or_pause()` 方法**：raise 后被 `_step` 外层 `try/except`（L120/L145-147）捕获 → `_handle_step_error` → `finally: _finalize`（results=`[]`，`step.py:118` 初值，对空 results 安全）。

### 4.5 边界与风险
- **raise 位置必须在内层 `try`（L799）之前**：若错放在 `try` 内，会被 `except Exception as e`（L811）当成普通异常吞成 `ActionResult(error)`，反而执行了「部分 actions + 计失败」的错误语义。插入点固定在 L771 与 L773 之间，**测试覆盖此路径**。
- **partial results 丢弃**：action 1 的副作用（如已输入文本）已发生但无 result 记录。用户主动停止通常意味着终止，丢弃合理；若需保留 partial results 供下次续跑，是另一独立特性（browser-use 也不保留），不在本期。
- **不与 guard #2/#3 冲突**：guard #2/#3（L832）处理的是「action 执行后」的 done/error；P0-1 处理的是「action 执行前」的用户停止，时序不同。
- **`paused` 同样 raise**：暂停语义下，raise InterruptedError 后 `_handle_step_error` 分支 1 直接 return，下一轮 `_step` 入口（L124）会再次检查 paused 决定是否等待恢复（`agent.py:239`），行为正确。

### 4.6 测试要点
- 多动作（3 个）+ 在 action 1 完成后、action 2 执行前置 `state.stopped=True` → `_execute_actions` raise `InterruptedError`，且 action 2/3 **未执行**（mock `tools.execute` 断言只被调用 1 次）。
- 同上置 `state.paused=True` → 同样 raise。
- raise 后 `_post_process` **未被调用**（`_step` except 跳过 L139），`consecutive_failures` 不增加。
- 单动作 + 不停止 → 正常执行，无 InterruptedError（回归）。

---

## 5. P1-2：per-action 执行日志 + 秘密脱敏（含 `log_response` 顺手修复）

### 5.1 现状（双问题）
1. **缺 per-action 执行日志**：`_execute_actions` 循环内只有 observability 事件（`ToolCallEvent`/`ToolResultEvent`，结构化、面向 UI/指标）和 error/timeout 警告，**没有人类可读的「正在执行第 i/N 个动作」终端行**。多动作步骤调试时看不出当前跑到第几个、卡在哪。browser-use 的 `_log_action`（service.py L2756/L2849-2877）每个 action 执行前打印 `[1/3] click: index=5, text="hello"`。
2. **`log_response` 日志泄露真密码**（pre-existing）：`action_params` 在 LLM 输出解析时已被 `_restore_sensitive_in_output`（`client.py:145`，调用点 L244/L297）还原为真值，进入 `_execute_actions` 时 `params["text"]` 就是真密码。`log_response`（`log_formatter.py:44`，调用点 `step.py:509`）经 `format_action_params`（`log_formatter.py:80`）**原样打印 params，未脱敏** → 终端日志泄露。新增 per-action 日志若也原样打印会加剧同一问题。

### 5.2 影响
- 多动作步骤可观测性差：终端日志只在每步开头打印一次整步决策（`log_response`），执行期无逐动作进度，难以定位「3 个动作里哪个卡住/失败」。
- 秘密泄露：`input_text(text="<真密码>")` / `search(query="<敏感词>")` 的真值直接进终端日志（及任何捕获 stdout 的日志收集器）。

### 5.3 browser-use 做法
```python
# service.py L2756
await self._log_action(action, action_name, i + 1, total_actions)
```
browser-use 的 `sensitive_data` 在 `tools.act` 内部按 placeholder→真值替换（service.py L2762-2770 传 `sensitive_data=self.sensitive_data`），**model_output 里只有占位符**，故 `_log_action` 打印 params 不泄密。TreeWalker 走「client 还原 + history 脱敏」的不同架构（见 §6），params 已是真值，必须额外脱敏。

### 5.4 方案

**5.4.1 抽共享脱敏辅助 `_redact_params_for_log`**（放 `step.py` 顶部 helpers 区，与 `_is_connection_error` 同级）：

```python
def _redact_params_for_log(
    action_name: str,
    params: dict[str, Any],
    sensitive_map: dict[str, str] | None,
) -> dict[str, Any]:
    """返回 params 的副本，仅对 _SENSITIVE_ACTION_FIELDS 列出的敏感字段脱敏。

    镜像 _redact_history_data（views.py:140）的单 action 版本，但**非原地**（返回
    副本，不影响真实 params）。用于终端日志：action_params 在 client.py 已被
    _restore_sensitive_in_output 还原为真值，直接打印会泄密。无 sensitive_map
    时 no-op。非敏感字段（index/url/...）原样保留，保证日志可读。
    """
    if not isinstance(params, dict):
        return {}
    if not sensitive_map:
        return dict(params)
    fields = _SENSITIVE_ACTION_FIELDS.get(action_name)
    if not fields:
        return dict(params)
    redacted = dict(params)
    for f in fields:
        if isinstance(redacted.get(f), str):
            redacted[f] = redact_sensitive_string(redacted[f], sensitive_map)
    return redacted
```

复用：`_SENSITIVE_ACTION_FIELDS`（`views.py:117`，`{"input_text": ("text",), "search": ("query",), "extract": ("query",)}`）+ `redact_sensitive_string`（`views.py:124`，输出 `<secret>key</secret>` 占位）。需在 `step.py` 顶部 import 这两个符号。

**5.4.2 展平 sensitive map**：`redact_sensitive_string` 期望 `{key: secret}`（即 `{placeholder: real_value}`）方向。Agent 侧 `self._sensitive_data_raw` 是 `{placeholder: {value, urls}}`（`agent.py:96`），需展平为 `{placeholder: value}`。
- **落地核对项**：检查 Agent 是否已有展平后的 map 属性（history 脱敏路径 `save_to_file(sensitive_data=...)` 已传过 `{placeholder: value}` 方向的 map）。若已有则复用；若无则在 `StepPipeline` 上加一个 `@property _sensitive_map_for_log` 展平缓存。

**5.4.3 per-action 执行日志**（`_execute_actions` 循环内，P0-1 stop 检查之后、obs 事件 L773 之前）：

```python
        # ★ P0-1 stop 检查（见 §4.4）
        if self.state.stopped or self.state.paused:
            raise InterruptedError

        # ★ P1-2：per-action 执行日志（对齐 browser-use _log_action service.py:2756）。
        # params 已是真值（client.py 还原），必须先脱敏再打印。
        safe_params = _redact_params_for_log(
            action_name, action_params, self._sensitive_map_for_log,
        )
        logger.info(
            "  [%d/%d] %s%s: %s",
            i + 1, total,
            BLUE, action_name, RESET,
            format_action_params(safe_params),
        )

        tool_call_id = ""                               # L773 现有
        ...
```

复用 `format_action_params`（`log_formatter.py:19`）+ ANSI 常量（`BLUE`/`RESET`，`log_formatter.py:9,12`）。

**5.4.4 顺手修复 `log_response` 泄露**（`step.py:509` 调用点）：

```python
# step.py L506-L517 现有 log_response 调用
action = response.get("action", {})
action_name = action.get("name", "done")
action_params = action.get("params", {})
# ★ P1-2：决策日志同样脱敏（修复 pre-existing 泄露）
safe_params = _redact_params_for_log(
    action_name, action_params, self._sensitive_map_for_log,
)
log_response(
    evaluation=response.get("evaluation_previous_goal", ""),
    memory=response.get("memory", ""),
    next_goal=response.get("next_goal", ""),
    action_name=action_name,
    action_params=safe_params,          # ★ 改传脱敏后副本
    step=self.state.n_steps,
    logger=logger,
)
```

`log_response` 本身（`log_formatter.py:44`）**不改签名**——它仍是纯格式化函数；脱敏在调用 site 完成，per-action 日志与决策日志共用同一辅助。

### 5.5 边界与风险
- **无 sensitive 配置时 no-op**：`_sensitive_map_for_log` 为空 → `_redact_params_for_log` 直接返回 `dict(params)`（浅拷贝），零开销、零误伤。`enable_sensitive_description=False` 或未配 sensitive_data 时走此路径。
- **仅按白名单脱敏**：只动 `_SENSITIVE_ACTION_FIELDS` 列出的字段（input_text.text / search.query / extract.query），`index`/`url`/`new_tab` 等不变，保证日志可读性。与 `_redact_history_data`（views.py:156-162）完全一致的口径。
- **`result.extracted_content` 不脱敏**：与 `_redact_history_data` 的有意权衡一致（views.py:143-146 注释：过滤 extracted_content 会损害可读性）。若日志/历史会外发，需另行评估——不在本期。
- **`_redact_params_for_log` 非原地**：返回副本，不影响真实 `action_params`（保证 `tools.execute(action_name, action_params, ...)` 拿到的仍是真值，动作正常执行）。
- **脱敏 map 方向**：`redact_sensitive_string` 是 `{placeholder: real_value}`，`_filter_sensitive_in_messages`（client.py:138）是 `{real_value: placeholder}`——两者方向相反。`_sensitive_map_for_log` 必须是前者方向。**落地核对项**：用单测验证 `input_text(text="真密码")` 日志输出含 `<secret>...</secret>` 而非真值。

### 5.6 测试要点
- **per-action 日志**：多动作步骤 + `caplog` 断言出现 `[1/3] click: ...`、`[2/3] input_text: ...`、`[3/3] ...` 三行；动作顺序与执行顺序一致。
- **脱敏（per-action）**：配 `sensitive_data={"pw": {"value": "secret123"}}` + `input_text(text="secret123")` → 日志中 `text` 显示 `<secret>pw</secret>`，**不出现** `secret123`。
- **脱敏（`log_response`）**：同上敏感配置 → 决策日志的 Action 行同样显示占位符（修复验证）。
- **非敏感字段不变**：`click(index=5)` 的 `index` 日志显示 `5`，未被误伤。
- **无 sensitive 配置**：`_sensitive_map_for_log={}` → 日志原样打印（与现状一致，回归）。
- **真实 params 未被改**：脱敏后断言 `action_params["text"] == "secret123"`（副本机制验证，动作执行不受影响）。

---

## 6. P3-3：`tools.act` 7 参数执行上下文（架构说明，不落地）

browser-use `tools.act(action, browser_session, file_system, page_extraction_llm, sensitive_data, available_file_paths, extraction_schema)`（service.py L2762-2770）—— 7 参数显式传递。TreeWalker `tools.execute(action_name, params, browser, browser_state)`（`actions.py:420`）→ handler `(params, browser)`（`actions.py:436`）。逐项核对：

| browser-use 参数 | TreeWalker 现状 | 结论 |
|---|---|---|
| `page_extraction_llm` | `self.tools._extract_llm`（`agent.py:65-67`，默认=主 llm；`extract_llm` 配置为空时复用 `self.llm`）→ `_action_extract` 消费（`actions.py:1068`） | **非 gap**——已通过 Tools 实例属性满足 |
| `extraction_schema` | `self.tools._extraction_schema`（`agent.py:68`）→ `_action_extract` 消费（`actions.py:1051`） | **非 gap**——同上 |
| `sensitive_data` | client 层 `_restore_sensitive_in_output` 还原占位符（`client.py:145`）+ history 写入时 `_redact_history_data` 脱敏（`views.py:140`） | **非安全漏洞**——架构差异：browser-use 在 handler 内 swap、model_output 保持占位符；TreeWalker 在 client 还原、history 脱敏。两者都保证「真密码不进 LLM、不进持久化 history」。TreeWalker 残留风险仅在「执行期日志/异常 echo params」（P1-2 治理） |
| `file_system` | TreeWalker 用 `allowed_read_paths`/`allowed_write_paths`/`allowed_upload_paths`（Tools 实例属性，`agent.py:59`）做路径白名单，无独立 file_system 对象 | **架构差异**——P3 文档说明，不落地 |
| `available_file_paths` | TUI 形参（`tui/app.py:100`）未透传到 Tools；TreeWalker 走 `allowed_upload_paths` 白名单 + `_action_upload_file` 内部枚举 | **P3 跟踪**——低价值，当前无 handler 实际需要 |

**结论**：GAP-3 不构成真 gap。`page_extraction_llm`/`extraction_schema`/`sensitive_data`/`file_system` 均通过别的机制满足，仅架构与 browser-use 不同（实例属性 + client 还原 vs 显式 7 参数 + handler swap）。`available_file_paths` 是唯一未透传项，但当前无 handler 实际消费，归 P3 跟踪。**本期不落地任何代码改动**，仅在对照表（§3.2 g 行）与本文档说明差异。

---

## 7. 测试策略

沿用 `tests/test_multi_act.py::_make_agent` 的 fake-agent + 直接调用 `StepPipeline._method(agent, ...)` 模式，配 `caplog` / `monkeypatch`。

| gap | 文件 | 操作 | 用例 |
|---|---|---|---|
| **P0-1** | `tests/test_multi_act.py`（或 `tests/test_step_error_handling.py`） | 扩展 `TestPerActionStopCheck`（**新建**） | (1) 3 动作 + action 1 完成后置 `stopped=True` → raise `InterruptedError`，action 2/3 未执行（`tools.execute` 仅调 1 次）；(2) 置 `paused=True` → 同样 raise；(3) raise 后 `_post_process` 未调用、`consecutive_failures` 不增；(4) 单动作不停止 → 正常执行无 raise（回归）；(5) raise 位置在 inner try 之外（不被 `except Exception` 吞成 error result） |
| **P1-2（per-action 日志）** | `tests/test_multi_act.py` | 扩展 `TestPerActionLog`（**新建**） | (1) 3 动作 + `caplog` 断言 `[1/3]`/`[2/3]`/`[3/3]` 三行、动作名顺序正确；(2) `format_action_params` 被复用（参数名染色）；(3) 无 obs_bus 时仍打印（日志不依赖 observability） |
| **P1-2（脱敏）** | `tests/test_log_redaction.py`（**新建**） | 全新 | (1) `_redact_params_for_log("input_text", {"text":"secret123"}, {"pw":"secret123"})` → `{"text":"<secret>pw</secret>"}`；(2) 同输入但 `sensitive_map=None` → 原样返回副本；(3) `click` 不在 `_SENSITIVE_ACTION_FIELDS` → 原样返回；(4) 非原地：原 params 不变；(5) `log_response` 调用点传入脱敏副本 → `caplog` 不含真值（修复验证）；(6) history 脱敏（`_redact_history_data`）回归仍绿 |

**回归守护**：
- P0-1 改动 `_execute_actions` 控制流（新增 raise），必须重跑 `tests/test_multi_act.py` 全套（5 道中断门 + 异常三分诊）+ `tests/test_step_error_handling.py`（`_handle_step_error` 三分支）确认无回归。
- P1-2 改动 `log_response` 调用 site（params→脱敏副本），重跑 `tests/test_log_formatter.py`（若存在）+ 任何断言日志内容的测试。
- 覆盖率 > 85%（项目规范）。

---

## 8. 暂缓 / 剔除项与理由

| 项 | 决定 | 理由 |
|---|---|---|
| **`file_system` 参数** | **剔除** | browser-use 传独立 `FileSystem` 对象；TreeWalker 用 `allowed_read/write/upload_paths`（Tools 实例属性）做路径白名单，已覆盖文件读写的安全边界。架构差异，当前无 handler 需要独立 file_system 对象。 |
| **`available_file_paths` 参数** | **暂缓** | TUI 形参（`tui/app.py:100`）未透传到 Tools。当前 `_action_upload_file` 走 `allowed_upload_paths` 白名单内部枚举，无 handler 实际消费 `available_file_paths`。价值边际，待出现真实需求再评估。 |
| **抽独立 `_check_stop_or_pause()` 方法** | **剔除** | browser-use 把它抽成方法（service.py L1001-1019）是因为它还要检查 `register_should_stop_callback`（外部回调）。TreeWalker 的 stop/pause 信号源单一（`state.stopped`/`state.paused`），inline `if ... raise InterruptedError` 一行即可，与 02 期 P0-1（post-LLM 检查也 inline）风格一致。抽方法反而增加间接层。 |
| **抽独立 `_log_action()` 方法** | **剔除** | browser-use 的 `_log_action`（service.py L2849-2877）含复杂的多行彩色格式化。TreeWalker 复用已有 `format_action_params`（`log_formatter.py:19`）+ 一行 `logger.info("[%d/%d] %s: %s", ...)` 即可达到同等效果，无需新方法。 |
| **`demo_mode_log`** | **非 gap** | browser-use 的 `_demo_mode_log`（service.py L2772-2785）为 Gradio/Streamlit 前端发结构化事件。TreeWalker 已有 EventBus `ToolCallEvent`/`ToolResultEvent`（`step.py:778,822`），payload（step/action_name/params/success/error/duration）更结构化，覆盖更广。保留现状。 |
| **per-action `action_timeout`** | **TreeWalker 优势** | `asyncio.wait_for(self.tools.execute(...), timeout=self.action_timeout)`（`step.py:800-803`）给每个动作独立超时，browser-use 的 `tools.act` 无此层保护（依赖 handler 内部超时）。TreeWalker 在此**领先**，无需对齐。 |
| **partial results 保留** | **非 gap** | 用户主动停止时 partial results 随 raise 丢弃，与 browser-use 一致（`multi_act` 的 `except InterruptedError: raise` 同样不返回 results）。若未来要支持「停止后续动作但保留已执行结果」，是独立新特性，不在本期。 |

---

## 9. 实施路线图与本期范围

参照 01/02 文档「本期仅落地 P0 + P1 + P3 文档」的显式范围标注风格：

| 优先级 | 项 | 本期 | 复杂度 | 价值 | 依赖 |
|---|---|---|---|---|---|
| **P0-1** | 循环内 per-action stop 检查 | ✅ 落地 | 低（1 处 if + raise，复用现有 `_handle_step_error`） | 高（停止响应从「下一步边界」提升到「下一动作前」） | 无 |
| **P1-2** | per-action 执行日志 + 秘密脱敏（含 `log_response` 修复） | ✅ 落地 | 中（1 个共享辅助 + per-action 日志 + 1 处调用 site 改 + 展平 map 核对） | 中高（调试体验 + 安全） | 无 |
| **P3-3** | `tools.act` 7 参数架构说明 | ✅ 落地（仅文档） | 极低（本文档 §6 已交付） | 低（对照清晰度） | 无 |
| `file_system` 参数 | 剔除 | — | — | 架构差异，无 handler 需要 | — |
| `available_file_paths` 参数 | 暂缓 | 低 | 低 | 当前无 handler 消费 | — |
| 抽 `_check_stop_or_pause()` / `_log_action()` 方法 | 剔除 | — | — | inline 即可，与 02 风格一致 | — |

**两个改动彼此独立**，可分别实现、分别回滚、分别测试。建议落地顺序：**P1-2 先于 P0-1**——P1-2 先落地后，per-action 日志行已就位，P0-1 的 stop 检查插入点（日志行之前/之后）语义更清晰，且 P1-2 的 `_redact_params_for_log` 辅助与 P0-1 无耦合，diff 更小、回归面更窄。

**落地验收**：每项改动后跑 `uv run python -m pytest tests/ -x -v`，确保全绿且覆盖率 > 85%（项目规范）。

---

## 附：落地核对清单

- [ ] P0-1：`_execute_actions` 循环内、`wait_between_actions` sleep（L771）之后、obs 事件（L773）之前，新增 `if self.state.stopped or self.state.paused: raise InterruptedError`
- [ ] P0-1：raise 位置在 inner `try`（L799）**之前**——测试验证不被 `except Exception`（L811）吞成 error result
- [ ] P0-1：重跑 `tests/test_multi_act.py` + `tests/test_step_error_handling.py` 全绿
- [ ] P1-2：`step.py` 顶部 import `_SENSITIVE_ACTION_FIELDS` + `redact_sensitive_string`（from `views.py`）
- [ ] P1-2：新增 `_redact_params_for_log(action_name, params, sensitive_map)` 辅助（非原地，按白名单脱敏）
- [ ] P1-2：核对/新增 `_sensitive_map_for_log`（展平 `_sensitive_data_raw` 为 `{placeholder: value}` 方向，复用 history 脱敏路径已有的展平）
- [ ] P1-2：`_execute_actions` 循环内新增 per-action 日志（`[i/total] name: format_action_params(safe_params)`）
- [ ] P1-2：`step.py:509` `log_response` 调用 site 改传 `_redact_params_for_log(...)` 副本
- [ ] P1-2：抽查终端日志——配 sensitive 时 `input_text`/`search`/`extract` 的敏感字段显示 `<secret>...</secret>`，非敏感字段不变
- [ ] P3-3：本文档 §6 已交付，无需代码改动
