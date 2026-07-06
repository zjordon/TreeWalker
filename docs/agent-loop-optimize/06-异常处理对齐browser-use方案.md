# 异常处理（Error Handling）对齐 browser-use 方案

> 阶段：agent step 内部流程的**异常处理子流程**（`_handle_step_error`）—— `_step` 的 `try/except/finally` 中 `except` 分支
> 对照源：`D:\dev\git\learn_agent\browse-use\docs\agent-core\step内部流程\异常处理.md` + `异常处理内部逻辑\{1-处理中断错误,2-处理连接或浏览器关闭错误,3-处理其他所有异常}.md`；`D:\dev\git\z_jordon\browser-use\browser_use\agent\service.py`（`_handle_step_error` L1246-L1302、`_is_connection_like_error` L1304-L1318、`_is_browser_closed_error` L1320-L1342、`_execute_step` 超时 L2435-L2496、`run` 异常 L2498-L2726）+ `views.py`（`AgentError` L949-L985、`AgentSettings` L66/L86/L87）
> 本文档只交付**方案**，不含代码落地；落地为后续独立任务。
>
> **结论先行（2026-07-06 核对）**：TreeWalker 的 `_handle_step_error()`（`step.py:1119-1159`）**三分支结构与 browser-use 已对齐**——InterruptedError（不计失败）/ 连接错误重连（失败写 `last_result`）/ 其他错误计数（递增 `consecutive_failures` + 写 `last_result`）——**无 P0 级结构缺失**。缺口集中在 **Branch 3 的错误格式化与可观测性细节**：4 项 **P1**（错误格式化器 / 条件 traceback / 解析错误模型名日志 / 中断消息拼接），4 项 **P2 暂缓**（`final_response_after_failure` / 连接两级检测 / whole-step 超时 / run-loop `except Exception` 兜底），3 项 **P3 N/A**（`_demo_mode_log` / CAPTCHA / 初始动作）—— 另有历史重放（`rerun_history`）经复核 **✅ 已对齐甚至更优**（独立重放流程，不在 `_handle_step_error` 范围，见 §6.2）。**本期落地 P1-1/2/3/4，P2/P3 暂缓。**

---

## 1. 背景与范围

TreeWalker 的目标是「所有逻辑都对齐 browser-use」。Agent step 被拆为 5 阶段流水线（`src/tree_walker/agent/step.py`），由 `_step()`（`step.py:115-158`）的 `try/except/finally` 编排：

| 区段 | 位置 | 职责 | 覆盖文档 |
|---|---|---|---|
| `try` | `step.py:127-151` | 5 阶段流水线（Sense → Think → Act → Post） | 01-04 |
| `except` | `step.py:152-154` | `await self._handle_step_error(e); return False` | **本文档** |
| `finally` | `step.py:155-156` | `self._finalize(...)`（历史/摘要/步数递增） | 05 |

**本文档范围**：仅 `except` 分支调用的 `_handle_step_error()`（`step.py:1119-1159`）三分支，及其依赖的 `_is_connection_error()`（`step.py:1201-1217`）、相关配置（`config.py:76/80`）、与 run-loop 兜底（`agent.py:225-258`）。对应 browser-use 的 `_handle_step_error`（`service.py:1246-1302`）、`_is_connection_like_error`（`service.py:1304-1318`）、`_is_browser_closed_error`（`service.py:1320-1342`）、`AgentError.format_error`（`views.py:949-985`）。

**不覆盖**：5 阶段流水线本身（01-04）、终结化（05）、LLM 层内部的 fallback/重试（`client.py`，已由 `_try_switch_to_fallback` 等覆盖，本文档只关心**冒泡到 step 级**的异常）。

经逐行核对，三分支主体已对齐，缺口集中在 Branch 3 的错误格式化（裸 `str(error)`）与可观测性（无 traceback / 无模型名标记）。下文逐项给出代码级对照与方案。

---

## 2. 现状精确锚点（已核对真实代码）

### 2.1 TreeWalker `_handle_step_error` 全文（`step.py:1119-1159`）

```python
async def _handle_step_error(self, error: Exception) -> None:
    """Classify and handle exceptions during step execution.

    Three-branch classification (following browser-use):
      1. InterruptedError → user interrupt, no failure count
      2. Connection errors → attempt reconnect, stop on timeout
      3. All other errors → increment failures, create error result
    """
    # Branch 1: User interrupt — not a failure
    if isinstance(error, InterruptedError):
        logger.warning("Agent interrupted mid-step")
        return

    # Branch 2: Connection errors — attempt reconnect, stop on timeout
    if _is_connection_error(error):
        logger.warning("Connection error, attempting reconnect: %s", error)
        for _ in range(self.reconnect_timeout):
            if await self.browser.reconnect():
                logger.info("Reconnection succeeded, continuing")
                self.state.last_result = [
                    ActionResult(error=f"Connection lost and recovered: {error}")
                ]
                return
            await asyncio.sleep(1)
        logger.error("Reconnection failed after %ds, stopping agent", self.reconnect_timeout)
        self.state.stopped = True
        return

    # Branch 3: All other errors — count and log
    self.state.consecutive_failures += 1
    is_final = self.state.consecutive_failures >= self.max_failures
    log_level = logging.ERROR if is_final else logging.WARNING
    logger.log(
        log_level,
        "Step %d failed (%d/%d): %s",
        self.state.n_steps,
        self.state.consecutive_failures,
        self.max_failures,
        error,
    )
    self.state.last_result = [ActionResult(error=str(error))]
```

### 2.2 browser-use `_handle_step_error` 全文（`service.py:1246-1302`）

```python
async def _handle_step_error(self, error: Exception) -> None:
    """Handle all types of errors that can occur during a step"""

    # Handle InterruptedError specially
    if isinstance(error, InterruptedError):
        error_msg = 'The agent was interrupted mid-step' + (f' - {str(error)}' if str(error) else '')
        # NOTE: This is not an error, it's a normal part of the execution when the user interrupts the agent
        self.logger.warning(f'{error_msg}')
        return

    # Handle browser closed/disconnected errors
    if self._is_connection_like_error(error):
        # If reconnection is in progress, wait for it instead of stopping
        if self.browser_session.is_reconnecting:
            wait_timeout = self.browser_session.RECONNECT_WAIT_TIMEOUT
            self.logger.warning(
                f'🔄 Connection error during reconnection, waiting up to {wait_timeout}s for reconnect: {error}'
            )
            try:
                await asyncio.wait_for(self.browser_session._reconnect_event.wait(), timeout=wait_timeout)
            except TimeoutError:
                pass

            # Check if reconnection succeeded
            if self.browser_session.is_cdp_connected:
                self.logger.info('🔄 Reconnection succeeded, retrying step...')
                self.state.last_result = [ActionResult(error=f'Connection lost and recovered: {error}')]
                return

        # Not reconnecting or reconnection failed — check if truly terminal
        if self._is_browser_closed_error(error):
            self.logger.warning(f'🛑 Browser closed or disconnected: {error}')
            self.state.stopped = True
            self._external_pause_event.set()
            return

    # Handle all other exceptions
    include_trace = self.logger.isEnabledFor(logging.DEBUG)
    error_msg = AgentError.format_error(error, include_trace=include_trace)
    max_total_failures = self.settings.max_failures + int(self.settings.final_response_after_failure)
    prefix = f'❌ Result failed {self.state.consecutive_failures + 1}/{max_total_failures} times: '
    self.state.consecutive_failures += 1

    # Use WARNING for partial failures, ERROR only when max failures reached
    is_final_failure = self.state.consecutive_failures >= max_total_failures
    log_level = logging.ERROR if is_final_failure else logging.WARNING

    if 'Could not parse response' in error_msg or 'tool_use_failed' in error_msg:
        # give model a hint how output should look like
        self.logger.log(log_level, f'Model: {self.llm.model} failed')
        self.logger.log(log_level, f'{prefix}{error_msg}')
    else:
        self.logger.log(log_level, f'{prefix}{error_msg}')

    await self._demo_mode_log(f'Step error: {error_msg}', 'error', {'step': self.state.n_steps})
    self.state.last_result = [ActionResult(error=error_msg)]
    return None
```

### 2.3 辅助方法 / 配置锚点

| 方法 / 配置 | 位置 | 职责 |
|---|---|---|
| `_is_connection_error` | `step.py:1201-1206` | 一级检测：`isinstance(ConnectionError)` OR 字符串模式匹配 |
| `_CONNECTION_ERROR_PATTERNS` | `step.py:1209-1217` | 7 个连接错误签名（websocket closed / connection closed/reset/refused / browser closed / no browser） |
| `_step` except 调用点 | `step.py:152-154` | `except Exception as e: await self._handle_step_error(e); return False` |
| `llm: LLMClient` | `step.py:82` | LLM 客户端实例属性；模型名走 `self.llm.model`（`client.py:40`） |
| `max_failures` | `config.py:76`（默认 `5`）/ env `AGENT_MAX_FAILURES`（`config.py:285`） | 连续失败上限，触发 loop 退出 |
| `reconnect_timeout` | `config.py:80`（默认 `30`）/ env `RECONNECT_TIMEOUT`（`config.py:288`） | 连接重连轮询秒数 |
| `_try_switch_to_fallback` | `client.py:58-70` | LLM 层对 `RateLimitError`/`APIError` 的 fallback 切换（单向） |
| LLM 解析失败兜底 | `step.py` `_get_action_with_retry` 的 `_FALLBACK_DONE_OUTPUT` | 解析失败多数在此兜底，**不冒泡到 `_handle_step_error`** |
| run-loop 失败检查 | `agent.py:231-236` | `consecutive_failures >= max_failures` → break（**无** `+ final_response_after_failure`） |
| run-loop 键盘中断 | `agent.py:251-253` | `except KeyboardInterrupt: logger.info(...); break`（**无** 通用 `except Exception`） |
| browser-use `AgentError.format_error` | `views.py:957-985` | 按 `ValidationError`/`RateLimitError`/LLM 格式错误/通用 四类返回引导文案 |
| browser-use `_is_browser_closed_error` | `service.py:1320-1342` | 二级精确判断：错误签名 AND 非重连中 AND `_cdp_client_root is None` |
| browser-use `final_response_after_failure` | `views.py:87`（默认 `True`） | 失败上限 = `max_failures + int(...)`，给 agent 一次「临终遗言」 |
| browser-use `step_timeout` | `views.py:86`（默认 `180`） | whole-step 超时，在 `_execute_step`（`service.py:2435-2496`）包整个 step |

**七个决定性结论：**

1. **三分支结构对齐**。TreeWalker `InterruptedError` / 连接重连 / 其他计数与 browser-use 一一对应，控制流（不计失败 / 重连成功写 `last_result` / 计数 + 写 `last_result`）语义一致。
2. **连接重连语义对齐，实现不同**。browser-use 事件驱动（`_reconnect_event.wait()` + `RECONNECT_WAIT_TIMEOUT`，等待 `BrowserSession` 后台自动重连）；TreeWalker 主动轮询（`for _ in range(reconnect_timeout): await self.browser.reconnect()`）。两者「重连成功不计失败、写 `last_result`」的语义一致。
3. **Branch 3 用裸 `str(error)` 是核心缺口（P1-1）**。browser-use 经 `AgentError.format_error` 按 4 类返回引导文案；TreeWalker 把 `RateLimitError` 等异常的原始文案直接喂给 LLM，削弱 LLM 下一轮自我修复。
4. **无条件 traceback（P1-2）**。browser-use `include_trace = logger.isEnabledFor(DEBUG)`，DEBUG 时附 `traceback.format_exc()`；TreeWalker 无此机制，DEBUG 也不带堆栈。
5. **失败计数上限语义分歧（P2-1）**。browser-use `max_total_failures = max_failures + int(final_response_after_failure)`；TreeWalker 直接用 `max_failures`，无「临终遗言」。
6. **LLM 层已部分前置兜底**。`anthropic.RateLimitError`/`APIError` 在 `client.py:189/379/404` 被 `_try_switch_to_fallback` 处理；解析失败被 `_FALLBACK_DONE_OUTPUT` 兜底。真正冒泡到 Branch 3 的是 **fallback 也失败 / `_prepare_context` 异常 / 未预期异常**——格式化器主要服务于这些，文档需如实说明触发概率。
7. **LLM 栈差异决定不能照搬字符串**。browser-use 用 OpenAI SDK（`openai.RateLimitError`、解析错误文案 `'Expected format: AgentOutput'`）；TreeWalker 用 **Anthropic SDK**（`anthropic.RateLimitError`、解析错误文案 `"LLM returned no parseable response"`）→ P1 格式化器须按 TreeWalker 实际类型/文案适配。

---

## 3. browser-use 3 子步骤 vs TreeWalker 全景对照

### 子步骤 1：处理中断错误（browser-use L1250-L1254）

| # | 子步骤 | browser-use | TreeWalker 现状 | 状态 |
|---|---|---|---|---|
| 1a | 不计失败 / 不写 `last_result` / 直接 return | ✅ L1250-1254 | ✅ `step.py:1128-1130` | ✅ 对齐 |
| 1b | 日志级别 WARNING（非 ERROR） | ✅ L1253 | ✅ `logger.warning`（`step.py:1129`） | ✅ 对齐 |
| 1c | **消息条件拼接** `- {error}` | L1251 `'... mid-step' + (f' - {str(error)}' if str(error) else '')` | ❌ 固定 `"Agent interrupted mid-step"`（`step.py:1129`），无详情拼接 | ❌ **P1-4** |

### 子步骤 2：处理连接或浏览器关闭错误（browser-use L1257-L1280 + L1304-L1342）

| # | 子步骤 | browser-use | TreeWalker 现状 | 状态 |
|---|---|---|---|---|
| 2a | 连接错误检测 | 两级漏斗：`_is_connection_like_error`（仅签名，宽松）+ `_is_browser_closed_error`（签名+非重连中+`_cdp_client_root is None`，严格） | 一级 `_is_connection_error`（isinstance + 7 签名） | ⚠️ **P2-2**（架构差异） |
| 2b | 区分「正在重连」vs「未重连」 | L1259 `is_reconnecting` 分支，重连中走等待路径 | ❌ 无此区分，统一进轮询重连 | ⚠️ P2-2 |
| 2c | 重连等待机制 | 事件驱动 `asyncio.wait_for(_reconnect_event.wait(), timeout=RECONNECT_WAIT_TIMEOUT)`，超时静默 `pass` | 轮询 `for _ in range(reconnect_timeout): reconnect() / sleep(1)` | ✅ 语义对齐（实现不同） |
| 2d | 重连成功：不计失败，写 `last_result` | L1270-1273 `last_result=[ActionResult(error=f'Connection lost and recovered: {error}')]` | ✅ `step.py:1137-1141` 同文案 | ✅ 对齐 |
| 2e | 浏览器关闭终态：精确判断后 `stopped=True` | L1276-1280 `_is_browser_closed_error`（CDP 客户端为 None）→ `state.stopped=True` + `_external_pause_event.set()` | ⚠️ 重连失败**直接** `state.stopped=True`（`step.py:1143-1144`），无 CDP 客户端终态精确判断 | ⚠️ **P2-2** |
| 2f | 连接错误不计入 `consecutive_failures` | ✅（重连成功/失败均不递增） | ✅（`step.py:1132-1145` 不触碰 `consecutive_failures`） | ✅ 对齐 |

### 子步骤 3：处理所有其他异常（browser-use L1283-L1301 + `AgentError.format_error` L957-L985）

| # | 子步骤 | browser-use | TreeWalker 现状 | 状态 |
|---|---|---|---|---|
| 3a | **错误格式化器** | L1284 `AgentError.format_error(error, include_trace)` 四类引导文案 | ❌ 裸 `str(error)`（`step.py:1159`） | ❌ **P1-1** |
| 3b | **条件 traceback**（DEBUG） | L1283 `include_trace = logger.isEnabledFor(DEBUG)`；通用分支 L983-984 附 `traceback.format_exc()` | ❌ 无（DEBUG 也不带堆栈） | ❌ **P1-2** |
| 3c | 失败计数上限（含「临终遗言」） | L1285 `max_total_failures = max_failures + int(final_response_after_failure)` | ❌ 直接 `max_failures`（`step.py:1149/1156`） | ⚠️ **P2-1** |
| 3d | 动态日志级别（WARNING→ERROR） | L1290-1291 `is_final_failure` 决定 | ✅ `step.py:1149-1150` `is_final` 决定 | ✅ 对齐 |
| 3e | **解析错误特殊日志（模型名）** | L1293-1298 检测 `'Could not parse response'`/`'tool_use_failed'` → 额外 `Model: {llm.model} failed` | ❌ 无模型名标记 | ❌ **P1-3** |
| 3f | 写 `last_result`（错误入历史） | L1301 `last_result=[ActionResult(error=error_msg)]` | ✅ `step.py:1159`（但用 `str(error)` 而非格式化文案） | ⚠️ 随 P1-1 修正 |
| 3g | `_demo_mode_log` 通道 | L1300 `await self._demo_mode_log(...)` | ❌ 无 demo 模式 | 📄 **P3-1**（N/A） |

### 子步骤 4：run-loop 兜底（browser-use `run` L2498-L2726 / `_execute_step` L2435-L2496）

| # | 子步骤 | browser-use | TreeWalker 现状 | 状态 |
|---|---|---|---|---|
| 4a | run 级 `except Exception`（re-raise + 记 telemetry） | L2670-2673 `except Exception as e: logger.error(...); agent_run_error=str(e); raise e` | ❌ 仅 `KeyboardInterrupt`（`agent.py:251-253`），无 `except Exception` | ⚠️ **P2-4** |
| 4b | run 级 `KeyboardInterrupt` | L2661-2668 记 `agent_run_error` + 返回 history | ✅ `agent.py:251-253` break（语义略简） | ✅ 对齐（措辞小异） |
| 4c | run 级 `finally`（telemetry/eventbus/close） | L2675-2726 | ✅ `agent.py:254-258` `_finalize_session` + `browser.stop()`（无 telemetry/eventbus） | ✅ 对齐（架构差异） |
| 4d | **whole-step 超时**（`step_timeout`） | `_execute_step` L2460-2476 `asyncio.wait_for(step(), step_timeout)`，超时单独计数 + 推进 `n_steps` | ❌ 只有 LLM 超时（`llm_timeout`）+ action 超时（`action_timeout`），无 whole-step | ⚠️ **P2-3** |
| 4e | 初始动作异常处理 | L2578-2594 `_execute_initial_actions` 独立 try/except | N/A（TreeWalker 无 initial_actions 概念，初始 URL 导航在 `agent.py:217-222` 已 try/except） | 📄 P3（N/A） |

> **覆盖率**：3 子步骤 + run-loop 兜底共 22 项，其中 **11 项 ✅ 对齐**（三分支主体、计数语义、`last_result` 写入、run 的 KeyboardInterrupt/finally）；**4 项 ❌ 真 gap（P1）**（3a/3b/3e/1c）；**5 项 ⚠️ 差异/暂缓（P2）**（2a/2b/2e/3c/4a/4d，含一项 whole-step）；**2 项 📄 N/A（P3）**（3g/4e）。

---

## 4. P1 方案（本期落地）

### 4.1 P1-1：错误格式化器（`AgentError.format_error` 等价物）

#### 4.1.1 现状

Branch 3（`step.py:1147-1159`）直接用 `str(error)` 构造 `ActionResult` 与日志：

```python
self.state.last_result = [ActionResult(error=str(error))]
```

`anthropic.RateLimitError` 等异常的 `str()` 是 SDK 风格的长文案（含 status/body），直接喂给 LLM 缺乏「该如何修正」的引导，削弱 LLM 下一轮自我修复。

#### 4.1.2 影响

- `RateLimitError`（fallback 也失败后冒泡）：LLM 看到 SDK 原始错误体，不知道这是限流、该等还是该改输出。
- 通用未预期异常：LLM 看到原始 traceback 片段，难以提取可行动信息。
- 调试：线上日志无引导性，排障需反复翻 SDK 文档。

#### 4.1.3 browser-use 做法

`AgentError.format_error`（`views.py:957-985`）按错误类型分四类返回引导文案：

```python
@staticmethod
def format_error(error: Exception, include_trace: bool = False) -> str:
    message = ''
    if isinstance(error, ValidationError):
        return f'{AgentError.VALIDATION_ERROR}\nDetails: {str(error)}'
    from openai import RateLimitError
    if isinstance(error, RateLimitError):
        return AgentError.RATE_LIMIT_ERROR
    error_str = str(error)
    if 'LLM response missing required fields' in error_str or 'Expected format: AgentOutput' in error_str:
        lines = error_str.split('\n')
        main_error = lines[0] if lines else error_str
        helpful_msg = f'{main_error}\n\nThe previous response had an invalid output structure. Please stick to the required output format. \n\n'
        if include_trace:
            helpful_msg += f'\n\nFull stacktrace:\n{traceback.format_exc()}'
        return helpful_msg
    if include_trace:
        return f'{str(error)}\nStacktrace:\n{traceback.format_exc()}'
    return f'{str(error)}'
```

#### 4.1.4 方案（适配 TreeWalker 实际类型，**不照搬 browser-use 字符串**）

新增 `format_step_error(error, include_trace)` 函数（建议放 `step.py` Helpers 区，紧邻 `_is_connection_error`；或新建 `src/tree_walker/agent/errors.py`）。**关键适配点**：

| 错误类 | browser-use | TreeWalker 适配 |
|---|---|---|
| 验证错误 | `pydantic.ValidationError` → 引导 + Details | 同（TreeWalker 参数校验走 `_validate_action_params` 返回字符串、不抛 `ValidationError`，但保留 isinstance 分支兜底未来 Pydantic 解析） |
| 限流 | `openai.RateLimitError` → 固定文案 | **`anthropic.RateLimitError`**（`client.py:11` 已 import）→ 同文案 `"Rate limit reached. Waiting before retry."` |
| LLM 响应格式错误 | 检测 `'Expected format: AgentOutput'` 等 | 检测 **TreeWalker 自己的文案**：`"no parseable response"`（`client.py:232`）、`"Could not parse"`、`"tool_use_failed"` → 首行 + 引导 `"The previous response had an invalid output structure. Please stick to the required output format."` |
| 通用 | `str(error)` + 可选 stacktrace | 同 |

`anthropic` 类型用 **lazy import**（仿 browser-use lazy import openai，避免在 `step.py` 模块级引入 anthropic SDK）：

```python
def format_step_error(error: Exception, include_trace: bool = False) -> str:
    """Format a step-level error with guidance for the LLM (mirrors browser-use AgentError.format_error)."""
    # Pydantic validation error (future-proof; current params validation returns str, doesn't raise)
    try:
        from pydantic import ValidationError
    except ImportError:
        ValidationError = ()  # type: ignore
    if ValidationError and isinstance(error, ValidationError):
        return f"Invalid model output format. Please follow the correct schema.\nDetails: {error}"

    # Anthropic rate limit (lazy import — anthropic is the LLM SDK, see client.py:11)
    try:
        from anthropic import RateLimitError as _AnthropicRateLimit
        if isinstance(error, _AnthropicRateLimit):
            return "Rate limit reached. Waiting before retry."
    except ImportError:
        pass

    error_str = str(error)
    # TreeWalker's own parse-failure wording (client.py:232 etc.) — NOT browser-use's 'Expected format: AgentOutput'
    if any(marker in error_str for marker in _LLM_PARSE_ERROR_MARKERS):
        main = error_str.split('\n', 1)[0]
        msg = f"{main}\n\nThe previous response had an invalid output structure. Please stick to the required output format."
        if include_trace:
            msg += f"\n\nFull stacktrace:\n{traceback.format_exc()}"
        return msg

    if include_trace:
        return f"{error_str}\nStacktrace:\n{traceback.format_exc()}"
    return error_str


_LLM_PARSE_ERROR_MARKERS = (
    "no parseable response",
    "Could not parse",
    "tool_use_failed",
    "invalid output structure",
)
```

**Branch 3 改造点**（`step.py:1147-1159`）：

```python
# Branch 3: All other errors — count and log
include_trace = logger.isEnabledFor(logging.DEBUG)
error_msg = format_step_error(error, include_trace=include_trace)
self.state.consecutive_failures += 1
is_final = self.state.consecutive_failures >= self.max_failures
log_level = logging.ERROR if is_final else logging.WARNING
# (P1-3 解析错误模型名日志插入点 — 见 4.3)
logger.log(
    log_level,
    "Step %d failed (%d/%d): %s",
    self.state.n_steps,
    self.state.consecutive_failures,
    self.max_failures,
    error_msg,
)
self.state.last_result = [ActionResult(error=error_msg)]
```

#### 4.1.5 关键决策

- **不照搬 browser-use 的检测字符串**（`'Expected format: AgentOutput'`、`'LLM response missing required fields'`）——那是 browser-use 自家 LLM 解析器的错误文案，对 TreeWalker 永远匹配不到，照搬等于死代码。改用 TreeWalker 的 `"no parseable response"` 等。
- **lazy import anthropic**：`step.py` 当前不 import anthropic（只有 `client.py` import），模块级引入会让 `step.py` 强依赖 anthropic SDK；lazy import 把代价推迟到异常路径（冷路径），与 browser-use lazy import openai 一致。
- **保留 ValidationError 分支**：TreeWalker 当前参数校验不抛 `ValidationError`，但 LLM 响应若未来改用 Pydantic 解析会抛，保留分支零成本兜底。

#### 4.1.6 边界与风险

- **触发概率如实说明**：TreeWalker 的解析错误多数被 `_FALLBACK_DONE_OUTPUT`（`step.py` `_get_action_with_retry`）兜底，**不冒泡到 Branch 3**；`RateLimitError`/`APIError` 多数被 `_try_switch_to_fallback`（`client.py:58-70`）处理。真正冒泡的是 fallback 也失败 / `_prepare_context` 异常 / 未预期异常。格式化器主要服务于这些，文档与代码注释须如实说明，避免给人「覆盖所有 LLM 错误」的错觉。
- **`anthropic.APIError`（非 RateLimit）**：browser-use 只对 RateLimit 给固定文案，其他 APIError 归入通用分支。TreeWalker 保持一致——不额外给 APIError 专用文案，避免过度设计。
- **测试隔离**：`format_step_error` 是纯函数，单测无需起 LLM/浏览器，构造各类异常入参断言文案即可。

---

### 4.2 P1-2：条件 traceback（DEBUG 模式）

#### 4.2.1 现状 / 影响

Branch 3 无论日志级别都只输出 `str(error)`，DEBUG 模式排查时无堆栈，定位困难。

#### 4.2.2 方案

与 P1-1 合并实现：Branch 3 入口取 `include_trace = logger.isEnabledFor(logging.DEBUG)`，传入 `format_step_error`。通用分支与解析错误分支在 `include_trace=True` 时附 `traceback.format_exc()`（见 4.1.4 代码）。非 DEBUG 保持单行消息，不污染默认日志。

#### 4.2.3 关键决策

- 用 `logger.isEnabledFor(DEBUG)` 而非新增配置项——与 browser-use 完全一致，零配置成本。
- traceback 只附在「给 LLM 的 `error_msg`」与「日志」上，**不**改 `ActionResult` 的结构（仍是 `error=str`）。

---

### 4.3 P1-3：解析错误特殊日志（模型名）

#### 4.3.1 现状 / 影响

解析类错误不单独标记模型名，日志里难以快速定位「是哪个 LLM 模型/提供方输出格式出问题」，多模型/fallback 场景排障成本高。

#### 4.3.2 browser-use 做法

`service.py:1293-1298`：检测 `error_msg` 含 `'Could not parse response'` 或 `'tool_use_failed'`，额外 `logger.log(log_level, f'Model: {self.llm.model} failed')`，给模型输出格式问题一个明确归因信号。

#### 4.3.3 方案

Branch 3 计数后，若 `error_msg` 命中解析错误标志（复用 P1-1 的 `_LLM_PARSE_ERROR_MARKERS`），额外记一条模型名日志：

```python
if any(marker in error_msg for marker in _LLM_PARSE_ERROR_MARKERS):
    logger.log(log_level, "Model %s failed to produce valid output", self.llm.model)
```

#### 4.3.4 核对点

- step.py 中 LLM 客户端属性名为 `self.llm`（`step.py:82` `llm: LLMClient`），模型名为 `self.llm.model`（`client.py:40` `self.model = s.model`，fallback 切换时 `client.py:63` 同步更新）——已确认，无需额外适配。
- 标志集与 P1-1 的 `_LLM_PARSE_ERROR_MARKERS` 共用，单一来源。

#### 4.3.5 边界

TreeWalker 解析错误冒泡到 Branch 3 概率低（见 4.1.6），此日志主要在「未来 LLM 响应改 Pydantic 解析 / 解析异常逃逸 fallback」时发挥作用；当前作为对齐 browser-use 的可观测性兜底，成本低、不引入噪音（仅解析错误才触发）。

---

### 4.4 P1-4：中断消息条件拼接

#### 4.4.1 现状

`step.py:1128-1130`：

```python
if isinstance(error, InterruptedError):
    logger.warning("Agent interrupted mid-step")
    return
```

固定文案，丢弃 `InterruptedError` 自身可能携带的详情（如自定义中断原因）。

#### 4.4.2 方案

对齐 browser-use L1251 的条件拼接（仅当 `str(error)` 非空时附加，避免尾部多余分隔符）：

```python
if isinstance(error, InterruptedError):
    msg = "Agent interrupted mid-step"
    if str(error):
        msg = f"{msg} - {error}"
    logger.warning(msg)
    return
```

#### 4.4.3 边界

TreeWalker 当前抛 `InterruptedError` 的位置（`step.py:131` stopped/paused 检查、`step.py:806-812` per-action stop 检查）均为裸 `raise InterruptedError`（无消息），故 `str(error)` 为空、消息不变。但保留拼接可为未来「携带原因的中断」（如外部回调注入原因）零成本预留，与 browser-use 行为一致。

---

## 5. P2：差异 / 暂缓（记录取舍，本期不落地）

### 5.1 P2-1：`final_response_after_failure`「临终遗言」— 暂缓

**分歧**：browser-use `max_total_failures = max_failures + int(final_response_after_failure)`（`service.py:1285`、`views.py:87` 默认 `True`），即失败到 `max_failures` 次时再多给 1 次「最终响应」机会；TreeWalker 直接用 `max_failures`（`step.py:1149/1156`、`agent.py:231`、`config.py:76` 默认 `5`）。

**取舍 / 暂缓理由**：
- 引入会改变 loop 终止语义，需联动改 `agent.py:231`（run-loop 失败检查）、`step.py:150/395`（step 内失败检查）、`config.py`（新增配置项 + env）、以及「最终响应」的 prompt 设计（browser-use 在最后一次给特殊提示让 agent 总结）。
- 当前 `max_failures=5` 已较宽容（browser-use 默认也是 5），常规任务足够；「临终遗言」的边际价值低。
- **下一步**：若未来出现「agent 在失败上限前本可总结出有用结论却硬终止」的明确需求，再独立评估此机制（含 prompt 设计与配置项），不与本期的格式化器混做。

### 5.2 P2-2：连接重连两级检测 / 浏览器关闭终态精确判断 — 暂缓

**分歧**：browser-use 两级漏斗——`_is_connection_like_error`（仅签名，宽松初筛）+ `_is_browser_closed_error`（签名 AND 非重连中 AND `_cdp_client_root is None`，严格确认真终态）（`service.py:1304-1342`）；并区分「正在重连」（`is_reconnecting`，走事件等待）vs「未重连」（直接查终态）。TreeWalker 一级 `_is_connection_error`（`step.py:1201-1217`），重连失败直接 `state.stopped=True`（`step.py:1143-1144`），无 CDP 客户端终态精确判断。

**取舍 / 暂缓理由**：
- browser-use 的两级检测依赖 `BrowserSession` 暴露 `is_reconnecting` / `is_cdp_connected` / `_cdp_client_root` / `_reconnect_event` / `RECONNECT_WAIT_TIMEOUT` 等状态与事件，其重连是 **`BrowserSession` 后台自动重连**，agent 只等事件。
- TreeWalker 是 **agent 主动轮询** `await self.browser.reconnect()`（`step.py:1136`），架构不同。是否暴露上述状态、能否做「终态精确判断」取决于 `BrowserSession.reconnect()` 的实现。
- **下一步**：先摸清 TreeWalker `BrowserSession`（`browser/` 目录）的 `reconnect()` 实现与可观察状态，再定方案（可能的方向：重连失败时探活一次 CDP 连接，确认真的断了才 `stopped=True`，避免短暂网络抖动 > `reconnect_timeout` 时误杀 agent）。

### 5.3 P2-3：whole-step 超时（`step_timeout`）— 暂缓

**分歧**：browser-use `_execute_step`（`service.py:2435-2496`）用 `asyncio.wait_for(step(), step_timeout)`（默认 `180`s，`views.py:86`）包整个 step，超时单独处理（`consecutive_failures += 1` + 写 `last_result` + 推进 `n_steps` 防死循环）。TreeWalker 只有 **LLM 超时**（`llm_timeout`，`step.py:447-456` 抛 `TimeoutError` 进 Branch 3）与 **action 超时**（`action_timeout`，`step.py:852-856` 内部 catch 成 `ActionResult`），无 whole-step 超时。

**取舍 / 暂缓理由**：
- LLM 超时 + action 超时已覆盖大多数卡死场景；剩余卡死点（如 `_prepare_context` 的 DOM 采集挂起）概率低。
- 引入 whole-step 超时需评估与 `_handle_step_error` 的交互（whole-step 超时抛的 `TimeoutError` 会进 Branch 3，需确认不与 LLM 超时混淆）、以及 `n_steps` 推进语义（browser-use 在超时分支显式 `n_steps += 1`，TreeWalker `_finalize` 已在 finally 总递增，是否重复需厘清）。
- **下一步**：若线上出现「step 整体挂起既非 LLM 也非 action 超时」的案例，再引入 `step_timeout`（含配置项 + 与 `_finalize` 步数递增的交互测试）。

### 5.4 P2-4：run-loop `except Exception` 兜底 — 暂缓

**分歧**：browser-use `run()`（`service.py:2670-2673`）有 `except Exception as e: logger.error(...); agent_run_error=str(e); raise e`，主要服务于 finally 中的 telemetry（`agent_run_error` 字段）。TreeWalker `agent.py:225-258` 仅 `except KeyboardInterrupt`（L251-253），无 `except Exception`。

**取舍 / 暂缓理由**：
- `_step` 内部已 `try/except` catch all 到 `_handle_step_error`（`step.py:152-154`），异常冒泡到 run loop 的概率极低（仅 `_handle_step_error` 自身抛异常、或 `_step` 在 `try` 外的代码抛异常）。
- TreeWalker 无 telemetry/eventbus，`agent_run_error` 无消费者，加 `except Exception` 仅多一条日志、无功能收益。
- **下一步**：若引入遥测/外部 run 级消费者（与 05 的 P3-2 富事件同理），再补 `except Exception` 记 `agent_run_error` 供消费；当前保持简洁。

---

## 6. P3：架构差异 / 不适用 / 已对齐核查（仅文档说明，不动代码）

### 6.1 P3-1：`_demo_mode_log` 双通道 — 无 demo 模式，N/A

browser-use Branch 3 末尾 `await self._demo_mode_log(f'Step error: {error_msg}', 'error', {'step': self.state.n_steps})`（`service.py:1300`），向浏览器 demo 模式面板推送错误。TreeWalker **无 demo 模式**（与 05 §6.3 结论一致），错误日志走单通道 `logger` 足够，不引入 `_demo_mode_log`。

### 6.2 P3-2：历史重放（`rerun_history`）的指数退避重试 — ✅ 已对齐甚至更优（独立流程，不在 `_handle_step_error` 范围）

> **勘误（2026-07-06 复核）**：本文档初版误判「TreeWalker 无历史重放功能」。经核对，TreeWalker **有完整的 `rerun_history`**（`src/tree_walker/agent/rerun.py`，含独立重试 + 指数退避 + 菜单重开，并有 `docs/rerun_history/` 设计目录与 `tests/test_rerun_history.py`/`test_rerun_history_integration.py` 共 80+ 测试）。本节据实重写。

**范围说明**：`rerun_history` 是**独立的重放流程**（在新浏览器里重跑录制的动作序列），有自己的 try/except 重试循环，**不在 `_handle_step_error` 范围内**（browser-use 的异常处理文档也只覆盖 `_handle_step_error`）。本节列出是为核查 browser-use `service.py` 中所有异常处理点、确认无遗漏——核查结论：**✅ 已对齐甚至更优，不是缺口**。

TreeWalker `src/tree_walker/agent/rerun.py:234-376`（`rerun_history` + `_rerun_step_with_retries`）与 browser-use `service.py` `rerun_history`（约 L3102-3282）逐项对齐：

| 维度 | browser-use | TreeWalker | 状态 |
|---|---|---|---|
| `max_retries` 默认 | 3 | 3（`rerun.py:238`） | ✅ |
| 指数退避序列 | `min(base·2^(n-1), max)` → 5/10/20 | `min(5·2^attempt, 30)` → 5/10/20（`rerun.py:369`） | ✅ 序列一致 |
| 退避 base / max | 5 / 30 | 5 / 30（硬编码） | ✅ |
| 菜单重开 | `_is_menu_opener_step` + `_reexecute_menu_opener` | 同（`rerun.py:676/717`） | ✅ |
| 菜单重开不消耗重试 | `retry_count -= 1` | 独立 `menu_reopens` 计数（`rerun.py:337`） | ✅ 等价（更清晰） |
| 菜单重开上限 | 1 次（`menu_reopened` 布尔） | 3 次（`rerun.py:350`） | ⚠️ TreeWalker 更宽松 |
| 触发信号 | 仅 `_is_menu_opener_step` | + `_is_option_element` 框架无关强信号（`rerun.py:701`） | ✅ TreeWalker 更鲁棒 |
| `skip_failures` / done 不截断后续步 / 重放不调决策 LLM | ✓ | ✓（`rerun.py:239/295-298`） | ✅ |
| 达到上限错误格式 | `{step} failed after {max} attempts: {err}` | `Step {n} failed after {max} retries: {err}`（`rerun.py:365-366`） | ✅ 措辞小异 |
| 无截图适配 | 靠截图视觉判定重放结果 | `prompts/rerun_summary.py` AI 摘要（无截图适配） | ⚠️ TreeWalker 独有（`include_screenshot=False`） |

**结论**：此项**不是缺口，从 P3 N/A 清单移除**。TreeWalker 的 rerun 异常处理与 browser-use 对齐，并在菜单重开信号识别（`_is_option_element`：option 匹配失败即触发、不必认得出触发器）、无截图 AI 摘要适配上**领先** browser-use。设计详见 `docs/rerun_history/`。

### 6.3 P3-3：CAPTCHA 等待处理 — 架构差异，N/A

browser-use `step()` Phase 0（`service.py:1032-1049`）有 `wait_if_captcha_solving()` 注入 CAPTCHA 结果。TreeWalker 无 CAPTCHA 自动处理架构，不适用。

### 6.4 P3-4：初始动作（`_execute_initial_actions`）异常处理 — 概念不存在，N/A

browser-use `run()`（`service.py:2578-2594`）有独立的初始动作执行与超时/异常处理。TreeWalker 无 initial_actions 概念，初始 URL 导航已在 `agent.py:217-222` 用 try/except 兜底（`logger.warning("Failed to navigate to initial URL: %s", e)`），不适用。

---

## 7. 测试策略

本期**纯文档，无代码改动 → 无新增测试**。未来落地 P1 时需补（现有 `tests/test_step_error_handling.py` 已覆盖三分支主体）：

| P1 项 | 文件 | 操作 | 用例 |
|---|---|---|---|
| P1-1 | `tests/test_step_error_handling.py`（或新建 `test_format_step_error.py`） | 新增 `format_step_error` 纯函数测试 | (1) `pydantic.ValidationError` → 引导文案 + Details；(2) `anthropic.RateLimitError` → 固定 `"Rate limit reached..."`；(3) 含 `"no parseable response"` → 首行 + 引导文案；(4) 通用异常 → `str(error)` |
| P1-2 | 同上 | 新增 `include_trace` 参数测试 | `include_trace=True` 附 `traceback.format_exc()`；`False` 仅消息；用 monkeypatch `logger.isEnabledFor` 验证 Branch 3 取值 |
| P1-3 | `tests/test_step_error_handling.py` | 用 `caplog` 扩展现有 Branch 3 测试 | 解析错误（`"no parseable response"`）触发额外 `Model {model} failed to produce valid output` 日志；非解析错误不触发 |
| P1-4 | `tests/test_step_error_handling.py` | 扩展现有 InterruptedError 测试 | 裸 `InterruptedError()` → `"Agent interrupted mid-step"`；`InterruptedError("reason")` → `"... - reason"` |
| 回归 | 全量 | `uv run python -m pytest tests/ -x -v` | 全过，覆盖率 >85%（CLAUDE.md 要求） |

**与既有失败计数测试的关系**（避免与 04 后处理冲突）：异常处理的 `consecutive_failures`（`_handle_step_error` Branch 3）与后处理的 `consecutive_failures`（`_post_process`，04 方案 P1-1 的 `len==1` 语义）是**两个通道**——前者管「步骤级异常」，后者管「动作级错误结果」。两者操作同一计数器但触发条件不同，测试需分别覆盖，不可混用。

---

## 8. 暂缓 / 剔除项与理由

| 项 | 决定 | 理由 |
|---|---|---|
| P1-1 错误格式化器（`format_step_error`） | ✅ **本期落地** | Branch 3 核心缺口；提升 LLM 自我修复与可调试性 |
| P1-2 条件 traceback（DEBUG） | ✅ **本期落地** | 与 P1-1 合并实现；零配置 |
| P1-3 解析错误模型名日志 | ✅ **本期落地** | 可观测性增强；复用 P1-1 标志集 |
| P1-4 中断消息条件拼接 | ✅ **本期落地** | 细节对齐；成本极低 |
| P2-1 `final_response_after_failure` | ⏸ 暂缓 | 改变 loop 终止语义，需联动 prompt/配置；当前 `max_failures=5` 足够 |
| P2-2 连接两级检测 / 浏览器关闭终态 | ⏸ 暂缓 | 依赖 `BrowserSession` 架构；需先摸清 `reconnect()` 可观察状态 |
| P2-3 whole-step `step_timeout` | ⏸ 暂缓 | LLM/action 超时已覆盖；需评估与 `_finalize` 步数递增交互 |
| P2-4 run-loop `except Exception` | ⏸ 暂缓 | `_step` 已 catch all；无 telemetry 消费者 |
| P3-1 `_demo_mode_log` 双通道 | 📄 N/A | 无 demo 模式（同 05 §6.3） |
| P3-2 历史重放重试 | ✅ 已对齐 | `rerun.py:234-376` 与 browser-use 对齐（指数退避 5/10/20 cap 30 + 菜单重开 + `max_retries=3`）；`_is_option_element` 强信号 + 无截图 AI 摘要领先；属独立重放流程不在 `_handle_step_error` 范围（§6.2） |
| P3-3 CAPTCHA 等待 | 📄 N/A | 无 CAPTCHA 架构 |
| P3-4 初始动作异常处理 | 📄 N/A | 无 initial_actions 概念（初始导航已兜底） |

---

## 9. 实施路线图与本期范围

| 优先级 | 项 | 本期 | 复杂度 | 价值 | 依赖 |
|---|---|---|---|---|---|
| **P1-1** | 错误格式化器 `format_step_error` | ✅ 落地 | 低 | 高（LLM 自我修复 + 调试） | 无 |
| **P1-2** | 条件 traceback（DEBUG） | ✅ 落地 | 低 | 中（线上排查） | P1-1 |
| **P1-3** | 解析错误模型名日志 | ✅ 落地 | 低 | 中（多模型排障） | P1-1 |
| **P1-4** | 中断消息条件拼接 | ✅ 落地 | 极低 | 低（细节） | 无 |
| P2-1 | `final_response_after_failure` | ⏸ 暂缓 | 中 | 中 | 明确「临终遗言」需求 |
| P2-2 | 连接两级检测 / 终态判断 | ⏸ 暂缓 | 中-高 | 中（避免误杀） | 摸清 `BrowserSession.reconnect()` |
| P2-3 | whole-step `step_timeout` | ⏸ 暂缓 | 中 | 低-中 | 线上挂起案例 |
| P2-4 | run-loop `except Exception` | ⏸ 暂缓 | 低 | 低 | 引入 telemetry |
| P3-1/3/4 | demo_mode / CAPTCHA / 初始动作 | 📄 N/A | — | — | — |

> **本期落地 P1-1/2/3/4，P2/P3 暂缓。无 P0**（三分支结构已对齐）。本期为**纯方案文档**，代码落地为后续独立任务。

**后续触发条件**：
1. P1 落地任务启动 → 按 §4 方案 + §7 测试执行，全量回归 `uv run python -m pytest tests/ -x -v`。
2. 线上出现「网络抖动 > 30s 被 `stopped=True` 误杀」案例 → 触发 P2-2 评估（先摸清 `BrowserSession`）。
3. 线上出现「step 整体挂起」案例 → 触发 P2-3 评估。
4. 引入遥测 / 「临终遗言」需求 → 分别触发 P2-4 / P2-1。

---

## 附：落地核对清单（P1 触发时）

- [ ] 新增 `format_step_error(error, include_trace)`（`step.py` Helpers 区或 `agent/errors.py`），覆盖 ValidationError / `anthropic.RateLimitError` / 解析错误标志 / 通用 四类，**lazy import** anthropic
- [ ] 新增 `_LLM_PARSE_ERROR_MARKERS` 常量（`"no parseable response"` / `"Could not parse"` / `"tool_use_failed"` / `"invalid output structure"`），P1-1 与 P1-3 共用
- [ ] `step.py:1147-1159` Branch 3 改造：`include_trace = logger.isEnabledFor(DEBUG)` → `error_msg = format_step_error(...)` → 日志与 `last_result` 均用 `error_msg`
- [ ] Branch 3 计数后插入 P1-3：`if any(marker in error_msg ...): logger.log(log_level, "Model %s failed to produce valid output", self.llm.model)`
- [ ] `step.py:1128-1130` Branch 1 改造：条件拼接 `if str(error): msg = f"{msg} - {error}"`
- [ ] `tests/test_step_error_handling.py` 扩展 P1-1/2/3/4 用例（见 §7），`caplog` 验证模型名日志
- [ ] 回归：`uv run python -m pytest tests/ -x -v` 全过，覆盖率 >85%
- [ ] 代码注释如实说明「解析错误多被 `_FALLBACK_DONE_OUTPUT` 兜底、格式化器主要服务 fallback 失败 / prepare_context 异常 / 未预期异常」

---

*本文档基于 TreeWalker 当前 master 分支（`_handle_step_error` @ `step.py:1119-1159`、`_is_connection_error` @ `1201-1206`、`_CONNECTION_ERROR_PATTERNS` @ `1209-1217`、`_step` except/finally @ `152-156`、`llm: LLMClient` @ `82`、`max_failures` @ `config.py:76`、`reconnect_timeout` @ `config.py:80`、run-loop @ `agent.py:225-258`、LLM 解析文案 @ `client.py:232`、fallback @ `client.py:58-70`）与 browser-use `_handle_step_error`（`service.py:1246-1302`）、`_is_connection_like_error`（`1304-1318`）、`_is_browser_closed_error`（`1320-1342`）、`AgentError.format_error`（`views.py:949-985`）、`AgentSettings`（`views.py:66/86/87`）的逐行对比，并核对 browser-use 设计文档 `异常处理.md` 及 `异常处理内部逻辑\{1,2,3}.md`。其中 `_execute_step`（L2435-2496）/ `run`（L2498-2726）的 browser-use 行号为子代理核对结果，落地实施时须以最新代码为准复核。本文档与 04（后处理失败计数 len==1）/ 05（终结化）结论不冲突：异常处理的 `consecutive_failures` 是「步骤级异常」通道，后处理的是「动作级错误结果」通道，两者触发条件不同。*
