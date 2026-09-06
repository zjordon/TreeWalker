# Code Review（第三轮）：PR #174（fix/173-malformed-action-params-crash）

- **日期**：2026-09-06
- **目标**：修复提交 `6e10ec9`（针对[第二轮 review](2026-09-06-pr174-malformed-action-params-crash-round2.md) 的修复；第一轮见[此处](2026-09-06-pr174-malformed-action-params-crash.md)）
- **范围**：对 `origin/master` 全量 +797/−28，9 个文件
- **方法**：high effort，8 个并行 finder 角度（逐行、被移除行为、跨文件、复用、简化、效率、层级、CLAUDE.md 规范）+ 逐条验证；~24 个原始候选去重后 10 条存活
- **验证结论**：9 项 CONFIRMED、1 项 PLAUSIBLE（测试工厂拷贝，低严重度）；规范角度无违规（新测试文件为 tab 缩进；空格缩进的新增位于既有空格缩进文件内，符合"与现有代码一致"条款）

## 总评

**本轮三项主修复验证为可靠**：choke point 归一化、emit 隔离、livelock 防护（当前不存在 `n_steps` 重复计数——递增恰为 `_finalize` 最后一条语句）。

但**两个新建立的不变量都只建了一半**：

- **emit 隔离**：只覆盖 `emit()`，`close()` 回调仍无防护；且隔离是全吞无升级，死订阅者会静默截断观测数据并刷屏 ERROR。
- **历史归一化**：`load_from_dict` 漏掉旧格式 dict-action 形态（issue #173 的原始 replay 崩溃仍可构造）；`rerun_history()` 是公开入口，接受内存构造的 `AgentHistoryList`，完全绕过该 choke point。

**三轮模式**：每轮修复都是"点修补"，相邻入口总是留缺口。下一轮应按不变量层级收口，而非继续逐点堵。

## Findings（按严重度排序）

### 1. CONFIRMED — `load_from_dict` 归一化存在形态缺口：旧格式 dict action 带字符串 params 原样放行

- **位置**：`src/tree_walker/agent/views.py:252`
- **类别**：correctness

**失败场景**：存量单动作 JSONL 步骤 `model_output = {"action": {"name": "click", "params": "561857"}}`（views.py 自己文档化的旧格式，`rerun.py:612/665` 通过 `or [model_output.get("action", {})]` 显式支持）：新代码块的 elif 只处理 `mo['action']` 为 str 的情况，该形态被跳过 → `_skip_reason` 算出 `fp = "561857"`（truthy 字符串击穿 `or {}`），`rerun.py:632` 的 `fp.get("index")` → AttributeError 穿过 rerun_history 的 try/finally（无 except），在前面步骤的副作用已重放后中止整个 replay；对不在白名单的名字，`_execute_history_step` 在 `rerun.py:677/720` 撞上 `dict("561857")` → ValueError，烧掉 3 次退避重试。该 hunk 的注释声称"覆盖全部 rerun 消费者"，但此形态未覆盖。

### 2. CONFIRMED — per-handler 隔离只加在 `emit()`，`close()` 回调仍无防护：session 收尾失败会丢弃已完成任务的 history

- **位置**：`src/tree_walker/observability/event_bus.py:55`
- **类别**：correctness

**失败场景**：`JsonlRecorder.close()`（`jsonl_recorder.py:26-29`）无防护地 flush+close，经 `bus.on_close` 注册；session 结束时 `EventBus.close()`（`event_bus.py:53-58`）无 try/except 调用它。磁盘满或文件已被外部关闭 → OSError 穿出 `run()` finally（`agent.py:314-315`）中的 `_finalize_session`（`agent.py:257`），替换掉 `return self.history`（`agent.py:320`）——调用方拿到异常而非已完成任务的结果，正是本 PR 自己 docstring 声称绝不允许发生的 778/782 式死亡，就在被修复方法的隔壁。

### 3. CONFIRMED — 裸字符串 elif 改变了畸形响应语义：裸 "done" 可在重试耗尽后以 success=True 终止任务；未注册名字字符串完全无重试；非字符串畸形值仍零重试硬终止

- **位置**：`src/tree_walker/llm/client.py:338`
- **类别**：correctness

**失败场景**：模型输出 `{"action": "done"}` → 归一化为 `{"name":"done","params":{}}` → `_is_valid_action` 通过 → `DoneParams.text` 必填（`models.py:563`）触发重试梯子，但 2 次重试失败后 `_validate_params_or_retry` 记录 "proceeding anyway"（`step.py:839-843`）并执行 → `_action_done` 默认 `success = params.get("success", True)`（`actions.py:2745`）→ 任务以 is_done=True/success=True + "(no summary provided)" 结束，而旧 else 分支会报告诚实的失败。反过来，散文式字符串如 "click the login button" 归一化为未注册名字，`_validate_action_params` 对 registry 未命中返回 None（`step.py:862-865`），完全得不到重试梯子反馈（与分支注释的声称相反）；且 `{"action": null}`/`{"action": 123}`/`{}` 仍落入 done(success=False) 零重试 else——第二轮 #5 标记的不对称只对字符串修复了。

### 4. CONFIRMED — `_handle_step_error` 自身抛出的异常仍会逃出 `_step` 杀死 run

- **位置**：`src/tree_walker/agent/step.py:181`
- **类别**：correctness

**失败场景**：CDP 中途掉线 → `_execute_actions` 重抛 → except 块执行 `await self._handle_step_error(e)` → 分支 2 的 `await self.browser.reconnect()`（`step.py:1365`）在半死浏览器上自身抛出非连接错误（CDP teardown 的 TypeError/TimeoutError）→ 新异常穿出 except 块；finally 的内层 try/except 只保护 `_finalize`，因此 `_finalize` 执行后异常重新抛出，穿过 `run()` 的 `except KeyboardInterrupt`（`agent.py:311`）终止整个任务。属既有问题，但位于被改动的函数内、紧邻那条注释声称此类死亡"必须兜住"的守卫。

### 5. CONFIRMED — 移除本地畸形 params 守卫使 `_skip_reason` 单一依赖 `load_from_dict`，但 `rerun_history()` 是公开入口，接受从未经过它的内存构造 `AgentHistoryList`

- **位置**：`src/tree_walker/agent/rerun.py:631`
- **类别**：correctness

**失败场景**：任何重放程序化构造或经 `model_validate`/`model_validate_json` 加载的 `AgentHistoryList` 的消费方（仓库内先例：`examples/p5_select_manual_var_verify.py:27`）完全绕过 `load_from_dict` choke point；truthy 字符串 params 条目随后到达行 632 的 `fp.get("index")` → AttributeError 杀死整个 replay，`_execute_history_step` 行 677/720 的 `dict(action.get("params", {}))` 抛 ValueError。旧的 `or {}` 加一行 isinstance 覆盖所有来源；保留它（并修正那条声称"无本地守卫"的注释）只花一行。

### 6. CONFIRMED — 隔离是全吞无升级（无失败计数/熔断/disable-after-N）：死订阅者静默截断整段观测数据，同时每事件刷一条 ERROR

- **位置**：`src/tree_walker/observability/event_bus.py:43`
- **类别**：robustness

**失败场景**：100 步任务的第 3 步磁盘写满（observability 在两个主运行时强制开启：`cli.py:41` / `web/server.py:426`）：`JsonlRecorder.handle`（wildcard 订阅者，`agent.py:212`，裸 write+flush）在后续每个事件都抛错——每步 ~7 次 emit（StepStart/SkillActive/ModelCall/ModelResult/ToolCall/ToolResult/StepEnd，多动作时更多）→ ~600-700 条 ERROR 掩盖真实错误并在已满的磁盘上继续增长日志，而 session JSONL 从第 3 步起静默截断、无聚合信号；下游 P7 eval 管线在不知情的情况下基于残缺 session 计算统计。**合适深度的修法**：per-handler 失败计数 + disable-after-N（或 log-once）+ close 时汇总。

### 7. CONFIRMED — `_project_interacted_elements` 仍有最后一处未防护访问（对可能是字符串的 params 调 params.get——检查了 action 是 dict，没检查 params 是 dict），且新安全 wrapper 把整步投影降级为 None 而非逐条降级

- **位置**：`src/tree_walker/agent/step.py:1284`
- **类别**：correctness

**失败场景**：任何让畸形动作到达投影的绕过路径（内存/validate 加载的历史、未来调用方——测试已在直接调用 `_project_interacted_elements`）在 `params.get("index")` 处 AttributeError；经 wrapper 转化为整步 `interacted_element=None`，连格式良好的动作也失去文档化的 actions/interacted_element 等长配对（`views.py:257`），只有一条 per-step WARNING。录制"成功"结束、`_assert_pairing` 跳过 None，损失只在重放时暴露——点击没有元素锚点。在行 1284 对 params 加守卫（对齐 `rerun.py:680` 已有做法），catch-all 留给真正的 DOM bug。

### 8. CONFIRMED — `n_steps` 递增现在存在于两处（`_finalize` 尾部行 1250 + 新的 except 路径副本），仅靠一条跨 ~1050 行断言语句顺序的注释维持一致

- **位置**：`src/tree_walker/agent/step.py:196`
- **类别**：simplification

**失败场景**：except 路径的递增只在 `self.state.n_steps += 1` 保持为 `_finalize` 最后一条语句时正确。第一次在行 1250 之后追加任何可能抛错的语句的重构（尾部 obs emit 是最自然的加法，也正是本 PR 修复的失败类别）会使每次失败的 `_finalize` 双重计数：预算每次失败烧 2 步、history step_number 出现空洞——新测试抓不到（它把 `_finalize` 整体 AsyncMock，真实 body 的顺序从未被测过）。**单一所有者修法**：把递增提升到 `_step` finally 中被守卫调用之后，删掉尾部递增。

### 9. CONFIRMED — 裸字符串→命名动作强转现存三处、语义分叉（client.py:342 带 strip、`_normalize_actions_list` 行 560 用 str(a) 不带 strip、views.py:253 带 strip），另有 views.py 的循环内 import 与 rerun.py:631 与自己注释矛盾的死 `or {}`

- **位置**：`src/tree_walker/llm/client.py:342`
- **类别**：simplification

**失败场景**：同一 LLM 错误 `' click '` 在顶层归一化为 `{"name":"click"}`、在列表内归一化为 `{"name":" click "}`——按位置不同结果不同（已注册 vs 未知动作）。强转规则下次变更时必须跨两个模块找三处；只更新"共享"helper 会静默留下 history 加载路径与实时路径产出不同 action dict。提取一个 coercion helper 三处共用；把 `views.py:249` 的循环内 import 提升到模块级（无循环依赖——client.py 只 import config；anthropic 经 `tree_walker/__init__` 本已加载）；删掉第二轮 review 明确要求移除的 `or {}`，或改写那条声称无本地守卫的注释。

### 10. PLAUSIBLE — 测试装置是本地 registry builder 的第 4 份拷贝且丢了 `test_multi_act.py` 的 `terminating` 旋钮；`_DummyParams` 第 3 份、`_make_tool_use_response` 逐字第 2 份、`_ProjectionAgent` 近拷贝 `test_step_finalize.py` 的 `_FakeAgent`——conftest.py 没有可扩展的此类工厂

- **位置**：`tests/test_step_malformed_action.py:52`
- **类别**：test-coverage（低严重度）

**失败场景**：本文件的 registry 无法表达 `terminates_sequence`（guard #4）场景，fake agent 无法改变截断行为——拷贝在落地时就已能力分叉。下一次 `_finalize`/`_execute_actions` 契约变更（正是本 PR 修复的 bug 类别）必须在 4+ 个文件间手工复制；维护者更新真实 registry fixture 后会发现这些拷贝仍在断言旧形态。把 `make_registry(*names, terminating=...)`/fake-tools/fake-agent 工厂上提到 `tests/conftest.py`（已为 9 个测试文件提供 DOM 工厂），供 `test_multi_act.py`、`test_step_malformed_action.py`、`test_step_finalize.py` 共用。文件头已声明拷贝行为、符合仓库惯例，属缓解因素。

## 修复建议（按不变量层级分组，非逐点堵）

1. **EventBus 全生命周期隔离 + 升级机制（解决 #2/#6）**：`close()` 回调与 `emit()` 同样 per-handler try/except；增加 per-handler 失败计数与 disable-after-N（或 log-once），close 时输出汇总——死订阅者不再刷屏也不再静默截断观测数据。
2. **归一化覆盖所有动作入口（解决 #1/#5/#9）**：提取单一 coercion helper（统一 strip 语义），`client.py` 顶层、`_normalize_actions_list`、`views.py load_from_dict` 三处共用；views.py 补旧格式 dict-action 形态；`rerun._skip_reason` 保留一行本地 isinstance 防御覆盖内存构造的 `AgentHistoryList` 并修正注释；删除死 `or {}`；views.py 循环内 import 提升到模块级。
3. **畸形 action 语义统一（解决 #3）**：裸 "done" 缺 text 重试耗尽后不应以默认 success=True 终止任务（诚实失败或继续重试）；未注册名字也应进入重试反馈；非字符串畸形值（null/123/{}）与字符串同等对待而非零重试硬终止。
4. **`_step` 死亡模式彻底收口（解决 #4/#8）**：`_handle_step_error` 自身包 try/except（其失败降级为日志，不杀 run）；`n_steps` 递增提升到 `_step` finally 单一所有者，删除 `_finalize` 尾部递增。
5. **投影兜底粒度（解决 #7）**：`_project_interacted_elements` 行 1284 对 params 加 isinstance 守卫（对齐 `rerun.py:680`），catch-all 留给 DOM bug，避免整步投影降级 None 破坏等长配对。
6. **测试工厂上提 conftest.py（解决 #10，低优先）**。

**补测试清单**：

- `close()` 回调抛错不影响 `run()` 返回 history
- 旧格式 dict-action（字符串 params）经 rerun 不崩
- 内存构造（`model_validate`）的 `AgentHistoryList` 含畸形动作经 rerun 不崩
- 裸 "done" / 未注册字符串 / `{"action": null}` 的终止与重试语义
- 不 AsyncMock 整体 `_finalize` 的真实 body 时序测试（递增恰在最后 / 单一所有者后无需此测试）
- 死订阅者 disable-after-N 行为

改完运行 `uv run python -m pytest tests/ -x -v`。改动继续落在 `fix/173-malformed-action-params-crash` 分支，PR #174 自动更新。
