# issue #194 实施方案：限流/网络基建失败与能力失败分罪——client 层退避 + step 层 infra 预算

- 日期：2026-09-19；分支 `fix/194-rate-limit-infra-wait`（自 master `ab1f56f`）
- 依据：`docs/bug-fix/194-rate-limit-infra-wait-analysis.md`（六环因果链/计步口径/三层选型）
- 范围：L2 client 层退避重试 + L3 step/run 层 infra 分类与预算；runner 任务超时的 infra 豁免是 evals 仓另案（§7）
- 验收（issue 原文）：注入限流场景回放，有效步数消耗与无限流时一致（§4 D4 + §5 真机回放）

## 0. 现象 → 改动映射

| 问题（分析文档 §2） | 改动 | 模块 |
|---|---|---|
| ② client 层限流直接 re-raise，无退避 | **A** `_create_with_backoff`：限流/网络错误指数退避 + retry-after，总预算封顶 | llm/client.py |
| ③ Branch 3 把基建失败当能力失败，且"Waiting"是谎言 | **B** Branch 2.5 infra 分类：不进连败、真退避、truthful 回显 | agent/step.py |
| ④ 每个限流失败步烧 1 步 max_steps | **B** `_skip_step_increment`：infra 步不递增 n_steps（livelock 防护移交 infra 预算） | agent/step.py |
| ⑤ 限流 5 连发触发能力止损死刑 | **B** infra 预算独立计数（`infra_failures`/`max_infra_failures`），run() 顶部独立检查 | step.py + agent.py + views.py + config.py |
| 验收与回归 | **D** client/step/run/config 四层测试 + 真机注入回放探针 | tests + examples |

## 1. A：client 层退避重试（L2）

### A1 常量与谓词（`client.py`，与 `_TEXT_RETRY_MAX = 2` 同位）

```python
from anthropic import Anthropic, APIConnectionError, APIError, RateLimitError

# issue #194：限流/网络传输类基建错误的 client 层退避（L2）。
_RATE_LIMIT_RETRY_MAX = 5        # 退避重试次数上限（6 次请求）
_RATE_LIMIT_BACKOFF_BASE = 2.0   # 首次退避秒数（2,4,8,16,30）
_RATE_LIMIT_BACKOFF_CAP = 30.0   # 指数退避单次上限
_RETRY_AFTER_CAP = 60.0          # retry-after 头的单次上限（防 proxy 报超大值）
_RATE_LIMIT_BUDGET_MAX = 90.0    # 退避总预算——保证终点异常类型是 RateLimitError
                                  # 而非外层 llm_timeout(120s) 的 TimeoutError（L3 分类的
                                  # 类型精确性依赖这一点）


def is_llm_infra_error(error: Exception) -> bool:
    """LLM 侧基建错误谓词（限流 429 / 网络传输）——L2 重试与 L3 分罪共用。

    只认 anthropic 类型，不含 builtin ConnectionError（那是浏览器侧，走
    _handle_step_error Branch 2 的 reconnect）。AuthenticationError/401/402/5xx
    不在其列：重试无益，维持既有 fallback-切换-否则-raise 语义。
    """
    return isinstance(error, (RateLimitError, APIConnectionError))
```

`is_llm_infra_error` 定义在 client.py（client.py 不依赖 agent 包，`from tree_walker.llm.client import is_llm_infra_error` 无环），step.py 模块级引用——单一事实源，测试锁定两层对称。

### A2 `_create_with_backoff`（`client.py`，get_action 的 create 调用收口）

```python
async def _create_with_backoff(
    self,
    messages: list[dict[str, Any]],
    **create_kwargs: Any,
) -> Any:
    """messages.create + 基建错误退避重试（issue #194 L2）。

    - RateLimitError/APIConnectionError：先试 fallback 切换（不占退避预算），
      无 fallback/已切换 → 指数退避重试，_RATE_LIMIT_RETRY_MAX 次为限；
    - retry-after 头可解析 → min(retry-after, _RETRY_AFTER_CAP) 覆盖指数值；
    - 退避累计超过 _RATE_LIMIT_BUDGET_MAX → 立即 raise（保证终点类型）；
    - 退避用 await asyncio.sleep（async 侧，create 仍在 asyncio.to_thread 里，
      issue #163 的事件循环可服务性不变）；CancelledError 穿透不吞（#186 教训）。
    """
    waited = 0.0
    for attempt in range(_RATE_LIMIT_RETRY_MAX + 1):
        try:
            return await asyncio.to_thread(self.client.messages.create, **create_kwargs)
        except (RateLimitError, APIConnectionError) as e:
            if self._try_switch_to_fallback(e):
                # 阶段二（§2.5 边界 2）：fallback 无视觉滤 image block（原 get_action 行为搬入）
                if not model_supports_vision(self.model):
                    _strip_image_blocks(messages)
                continue  # 切换即重试，不占退避预算（_try_switch 单向，至多一次）
            if attempt >= _RATE_LIMIT_RETRY_MAX:
                raise
            delay = _infra_backoff_delay(attempt, e)
            if waited + delay > _RATE_LIMIT_BUDGET_MAX:
                logger.warning(
                    "LLM infra backoff budget (%.0fs) exhausted after %d retry(ies) — raising",
                    _RATE_LIMIT_BUDGET_MAX, attempt,
                )
                raise
            waited += delay
            logger.warning(
                "LLM %s (retry %d/%d) — backing off %.1fs",
                type(e).__name__, attempt + 1, _RATE_LIMIT_RETRY_MAX, delay,
            )
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")  # for-else 语义守卫，静态可读
```

`_infra_backoff_delay(attempt, error)` 模块级辅助：指数 `min(_RATE_LIMIT_BACKOFF_CAP, _RATE_LIMIT_BACKOFF_BASE * 2 ** attempt)`；`getattr(error, "response", None)` 的 `headers.get("retry-after")` 可解析为数字时取 `min(float(v), _RETRY_AFTER_CAP)` 覆盖。解析全程 try/except——response 可能是 MagicMock/缺 headers/非数字（GLM proxy 行为未知，容错优先）。

### A3 `get_action` 接线（`client.py:249-276` 改造）

- create 调用点换为 `response = await self._create_with_backoff(messages, model=self.model, max_tokens=..., system=..., messages=messages, tools=..., tool_choice=...)`；
- **既有外层 `except (RateLimitError, APIError)` 保留不动**，职责收窄为：APIError 家族非 infra 成员（401/402/5xx）的 fallback 切换 + recursion（`test_authentication_error_triggers_fallback` 行为零变化）；infra 成员到达此处 = A2 预算耗尽，`_try_switch_to_fallback` 必返 False → raise（终点类型保持 RateLimitError/APIConnectionError，L3 可精确分类）；
- A2 内已处理 RateLimit/Connection 的 fallback 切换（含滤图），外层 except 里这两类的旧 fallback 分支自然只剩"已试过→False→raise"一条路——行为等价，无重复切换风险（`_using_fallback` 单向锁）。

### A4 不动的调用点（明确出界）

`extract`/`structured_call` 走 `_extract_call`（`client.py:509/537/580`）：它们在动作执行期调用，异常被 step.py:1419 的 per-action try 兜成 ActionResult error——限流在那里的最坏形态是"动作错误回显 + 单动作步计 1 次连败"，不产生 run 级死亡，v1 不改（§7 后置）。

## 2. B：step/run 层 infra 分类（L3）

### B1 `AgentState.infra_failures`（`views.py:101` 后）

```python
    # issue #194：LLM 基建失败（限流/网络）的连续计数——与 consecutive_failures
    # 分罪：不烧 n_steps、不进能力止损；成功步清零；达 max_infra_failures 由
    # run() 顶部检查独立终止（livelock 防护从 n_steps 递增移交至此）。
    infra_failures: int = 0
```

### B2 config（`config.py`）

`Settings.max_infra_failures: int = 8`（`:125` max_failures 旁）+ `int(os.environ.get("AGENT_MAX_INFRA_FAILURES", "8"))`（`:457`）。默认 8 × 退避 5,10,20,40,60,60,60 ≈ 5min——评测由 runner 600s 任务超时做最外层护栏，agent 侧只需"不死于 20 秒级窗口"。

运行时挂载（StepPipeline 是纯 Mixin——`step.py:116` 起"attributes provided by Agent"注解块，无构造函数）：注解块 `max_failures: int`（`step.py:126`）旁加 `max_infra_failures: int`；`Agent.__init__` 的 `self.max_failures = _settings.max_failures`（`agent.py:82`）旁加 `self.max_infra_failures = _settings.max_infra_failures`。

### B3 `_handle_step_error` Branch 2.5（`step.py:1799` Branch 2 之前插入）

```python
        # Branch 2.5: LLM 基建失败（限流/网络传输，issue #194）——先于浏览器
        # reconnect 分支：anthropic 类型精确，builtin ConnectionError 不在此列
        # （浏览器侧仍走 Branch 2）。与能力失败分罪：不进 consecutive_failures、
        # 不烧步数（_skip_step_increment）、真退避；防 livelock 由 infra 预算接管。
        if is_llm_infra_error(error):
            self.state.infra_failures += 1
            is_final = self.state.infra_failures >= self.max_infra_failures
            if is_final:
                logger.error(
                    "Infra failure budget (%d/%d) exhausted — run will stop",
                    self.state.infra_failures, self.max_infra_failures,
                )
            else:
                delay = min(_INFRA_BACKOFF_CAP, _INFRA_BACKOFF_BASE * 2 ** (self.state.infra_failures - 1))
                logger.warning(
                    "Infra failure (%d/%d): %s — backing off %.0fs (step budget not consumed)",
                    self.state.infra_failures, self.max_infra_failures,
                    type(error).__name__, delay,
                )
                await asyncio.sleep(delay)
            self._skip_step_increment = True
            self.state.last_result = [ActionResult(error=(
                f"LLM API {type(error).__name__} (infrastructure, not an action result); "
                f"backoff applied, no action executed this step"
            ))]
            return
```

模块级常量：`_INFRA_BACKOFF_BASE = 5.0`、`_INFRA_BACKOFF_CAP = 60.0`（`step.py` 顶部）。

设计要点：

- **终局步不再退避**（都死了，别白等 60s）；`_skip_step_increment` 仍置位（n_steps 语义一致性）；
- **last_result 说真话**："not an action result / no action executed"——下一步成功的模型读到它不会误以为自己做错过动作（替换掉那句谎言文案；`format_step_error` 的 rate-limit 分支保留但从此只在异常路径可达，既有测试不动）；
- **顺序**：Branch 1（中断）→ **2.5（LLM infra）** → 2（浏览器连接）→ 3（通用）。anthropic `APIConnectionError` 继承自 APIError 而非 builtin ConnectionError，且其 str（"Connection error."）不匹配 `_CONNECTION_ERROR_PATTERNS`——双保险不误入 Branch 2，但谓词在前使分类确定性不依赖字符串巧合。

### B4 `_step` finally 的步数递增（`step.py:259`）

```python
            # 计数器边界（review2 #1 / review3 #8 单一所有者）：n_steps 递增只在此
            # 处——吞异常不得跳过它（否则 run() 的 while 循环退化为无界 livelock）。
            # issue #194 唯一豁免：Branch 2.5 的 infra 失败步不烧步数预算，其
            # livelock 防护由 run() 顶部的 infra_failures 检查接管（预算独立、
            # 有界）。getattr 守卫：FakeAgent/MagicMock 测试桩无此属性时按未置位。
            if not getattr(self, "_skip_step_increment", False):
                self.state.n_steps += 1
            self._skip_step_increment = False
```

`_skip_step_increment: bool` 加入 StepPipeline 注解块（Mixin 约定）+ `Agent.__init__` 显式置 False；finally 的 getattr 守卫为双保险（FakeAgent/MagicMock 测试桩直接调 `StepPipeline._step` 时不经 Agent.__init__）。

### B5 run() 顶部预算检查（`agent.py:313` 连败检查之后）

```python
                if self.state.infra_failures >= self.max_infra_failures:
                    logger.error(
                        "Max infra failures (%d) reached — API unreachable, stopping",
                        self.state.infra_failures,
                    )
                    break
```

### B6 成功清零（`step.py:1573-1575` _post_process）

非计数步（成功/多动作失败）重置处并排加 `self.state.infra_failures = 0`——语义与 consecutive_failures 对称：**连续**基建失败才累积，两个窗口各中两枪不应叠加判死。

## 3. 与既有机制的交互核对（已核，不改）

| 机制 | 交互 | 结论 |
|---|---|---|
| `llm_timeout=120s` wait_for（`step.py:783`） | A2 退避总预算 90s + 请求时间 < 120s——限流的终点异常类型恒为 RateLimitError，不会变形为 TimeoutError 掉进 Branch 3 | 预算取值已对齐 |
| streak nudge peek/ack（#186 review2 #5） | infra 步 LLM 未送达输出 → 不 ack，下步首报重发 | 天然正确 |
| loop_detector | infra 步 `_prepare_context` 重跑 → 同页重复记录（无害 cosmetic）；L2 命中时（主路径）无重跑 | 记入 §6 风险 4 |
| `_finalize` | infra 步 model_output=None → 不进 history（runner 的 `len(history)` 口径天然免疫，分析 §3） | 零改动 |
| `finalize_degraded_steps` 升级终止 | infra 步的 _finalize 照常执行、无异常 | 零交互 |
| rerun.py 重放循环 | 无 LLM 调用 | 不适用 |
| obs StepEndEvent | infra 步照常 emit（is_done=False） | 零改动 |

## 4. D：测试

桩值纪律：RateLimitError 构造沿用既有形状 `RateLimitError(message=..., response=MagicMock(status_code=429), body=None)`（`test_llm_client.py:225`）；sleep 一律 `patch("<module>.asyncio.sleep", new_callable=AsyncMock)` 断言 await 序列，不让真实退避拖慢测试。

### D1 client 层（`tests/test_llm_client.py` 新类 `TestCreateWithBackoff`）

| # | 用例 | 断言 |
|---|---|---|
| 1 | 429×2 后成功 | 返回结果；create.call_count==3；sleep await [2.0, 4.0] |
| 2 | 永远 429 | raises RateLimitError；sleep await [2,4,8,16,30]；create.call_count==6 |
| 3 | retry-after="7" | sleep(7.0) 覆盖指数值 |
| 4 | retry-after="300" | 封顶 sleep(60.0) |
| 5 | retry-after="soon"（不可解析） | 回落指数 2.0 |
| 6 | retry-after 恒 60 | 预算耗尽：sleep [60.0] 后 raise（60+60>90），create.call_count==2 |
| 7 | fallback 切换不占预算 | 主 429 一次→fallback 成功；sleep 未被 await；`_using_fallback` True（扩既有 `test_fallback_on_rate_limit`） |
| 8 | sleep 抛 CancelledError | 向上穿透（不吞、不再重试）；create.call_count==1 |
| 9 | AuthenticationError(401) | 不退避（sleep 未被 await）；无 fallback → 立即 raise——既有 401→fallback 用例零变化 |
| 10 | APIConnectionError 同待遇 | 同 #1 |

### D2 step 层（`tests/test_step_error_handling.py`）

| # | 用例 | 断言 |
|---|---|---|
| 11 | RateLimitError 分类 | infra_failures==1；consecutive_failures==0；`_skip_step_increment` True；sleep(5.0)；last_result 含 "no action executed" |
| 12 | 预算耗尽步 | infra_failures 预置=max：不 sleep；ERROR 级日志（caplog）；仍置 skip 标记 |
| 13 | builtin ConnectionError 不属 infra | 走 Branch 2 reconnect（既有用例不动 + infra_failures==0 断言） |
| 14 | 通用 ValueError 走 Branch 3 | 既有 `test_non_connection_error_increments_failures` 不动 + infra_failures==0 |

### D3 编排层（`tests/test_step_malformed_action.py` `_loop_agent` harness 或新文件）

| # | 用例 | 断言 |
|---|---|---|
| 15 | infra 步不烧步数 | `_get_next_action` side_effect=RateLimitError → `_step` 后 n_steps==0、infra_failures==1、history 长度 0；接一步成功 → n_steps==1、infra_failures==0（B6 清零） |
| 16 | skip 标记不泄漏 | infra 步后紧跟正常失败步（ValueError）→ 该步 n_steps 照常 +1（标记每步复位） |
| 17 | **验收回放（issue 原文编码）** | 变体 A：3 个成功步；变体 B：RateLimit×2 + 同 3 个成功步 → 两变体终态 `n_steps` 相等、`len(history)` 相等、`consecutive_failures` 相等（==0） |
| 18 | run() 级预算终止 | `Agent(task, llm=MagicMock(), browser=mock, settings=AgentSettings(...))` + `agent._step` 假步注入 infra_failures 至 max（`test_step_malformed_action.py:836` 同型 harness）→ run() break 于 max_infra_failures 而非 max_steps；caplog 含 "API unreachable" |

### D4 config（`tests/test_config.py`）

| # | 用例 | 断言 |
|---|---|---|
| 19 | env 覆盖 | `AGENT_MAX_INFRA_FAILURES=3` → Settings.max_infra_failures==3；缺省 8 |

## 5. 验证步骤

1. `uv run python -m pytest tests/test_llm_client.py tests/test_step_error_handling.py tests/test_step_malformed_action.py tests/test_config.py -x -v`（模块级）；
2. `uv run python -m pytest tests/ -x -v` 全量（基线 2571+，覆盖率 ≥ 现状 89%）；
3. 真机注入回放（issue 验收原文）：新探针 `examples/debug_194_rate_limit_inject.py`——
   - 本地 aiohttp 反向代理：前 N 个 `/v1/messages` 请求回 `429 + retry-after: 2`（复刻 B 轮"几十秒间歇窗口"的压缩版），其余原样转发真实 base_url；
   - 同一真实任务（Magento admin，Chrome **9223**）跑两遍：clean vs injected（N≈6，跨 2-3 步）；
   - 断言：两遍 `state.n_steps` 终值一致、`len(history)` 一致、`consecutive_failures`==0、日志出现 `backing off` 而非 `Step N failed (k/5)`；
   - 桩值纪律（#185 durable 教训）：探针的 429 响应须带真实协议形状（JSON body + retry-after 头），先单测代理行为再上真机。

## 6. 风险

1. **退避等待吃任务墙钟**（600s）：L2 预算 90s/次 + L3 累计 ~5min，最坏叠加可被 runner 超时杀——这是设计内（runner 是最外层护栏；评测里"耗尽预算死亡"与"超时死亡"都属基建死法，本修复保证的是**不再 20 秒速死 + 不烧有效步数**）。runner 侧豁免另案（§7）。
2. **retry-after 语义**：GLM proxy 若恒报小 retry-after，会以 6 次快速请求收场——每请求仍含 SDK 内部 2 重试，总量有界（≤18 请求），可接受；探针真机验证实际头部行为。
3. **`_skip_step_increment` 与 finally 顺序**：标记检查必须紧贴 `n_steps += 1` 原位（`_finalize` 之后），且每步复位——D3#16 锁死"不泄漏到下一正常步"。
4. **loop_detector 重复记录**（仅 L3 路径，L2 命中时无）：同页无动作重复记录会推高 repetition 计数——最坏注入一条 loop nudge（提示性，不终止）；真机回放观察，若误导明显再后置处理。
5. **`format_step_error` 的 rate-limit 文案从此主路径不可达**：保留（异常路径兜底 + 既有测试锚点），不改——避免无谓的测试 churn。

## 7. 后置不做

- runner（evals 仓）任务超时的 infra 豁免与 err 字段 infra 分类——跨仓另案（分析 §6）；
- `extract`/`structured_call` 的限流退避（A4：不产生 run 级死亡，动作错误回显已可见）；
- `_force_done_after_failure` 不可达死代码的清理（与 #194 无因果，独立小改动另案）；
- infra 步免重跑 `_prepare_context`（省 1.7-2.5s/步的快照成本）——L2 主路径已天然免除，L3 长窗口场景收益小复杂度高；
- obs/`AgentHistoryList` 增加 stop reason 字段（P2 final_response 暂缓期间无消费方）。
