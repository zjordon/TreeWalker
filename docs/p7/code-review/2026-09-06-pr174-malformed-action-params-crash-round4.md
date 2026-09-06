# Code Review（第四轮）：PR #174（fix/173-malformed-action-params-crash）

- **日期**：2026-09-06
- **目标**：修复提交 `e58e66b`（针对[第三轮 review](2026-09-06-pr174-malformed-action-params-crash-round3.md) 的修复；第一、二轮见 [round1](2026-09-06-pr174-malformed-action-params-crash.md) / [round2](2026-09-06-pr174-malformed-action-params-crash-round2.md)）
- **范围**：对 `origin/master` 全量 +1204/−39，12 个文件（畸形动作归一化 choke point、EventBus 隔离/熔断、`_step` finally 守卫 + `n_steps` 单一所有者、历史加载归一化、670 行测试）
- **方法**：8 个 finder 角度（逐行、被移除行为、跨文件、复用、简化、效率、层级、规范）→ 去重 → 逐候选验证（6 个验证 agent + 直接代码引用核对）
- **验证结论**：6 项 correctness CONFIRMED + 4 项清理/规范类（直接引用核实）；3 项候选被带代码引用证伪

## 总评

**趋势向好**：前三轮的崩溃类问题（生产 AttributeError/ValueError、livelock、run 被杀）已基本清零。本轮剩余问题集中在**归一化引入的新语义**与残留窄门——其中 #1 是一条比修复前更差的**新假成功路径**。

被证伪的 3 项候选（留档）：

- `_handle_step_error` 吞异常会丢 `stopped=True`——不复现：`BrowserSession.reconnect()`（`session.py:1658-1661`）内部捕获所有异常并返回 `False`，分支 2 生产上不会抛。
- `load_from_dict` 就地修改调用方 dict——无受影响调用方。
- EventBus dict 无界增长——每 agent 有界。

**环境提示**：review 中测试首跑失败（`TestGetActionChokePoint` errors、`TestMalformedActionSemantics` failures）系本机 `ALL_PROXY=socks://…` 环境变量破坏 httpx 代理解析所致；unset 代理变量后三个涉及文件共 89 个测试全过。**本地跑测试前先清代理变量。**

## Findings（按严重度排序）

### 1. CONFIRMED — `_normalize_actions_list` 把畸形 done 的 params 擦成 `{}` 却无诚实失败守卫：dict 形态的畸形 done 可以 success=True 终止任务

- **位置**：`src/tree_walker/llm/client.py:592`
- **类别**：correctness

正是 `_coerce_named_action` docstring 自己命名的"假成功终止"危险，只对裸字符串形态修了。

**失败场景**：LLM 输出 `{"name": "done", "params": "561857"}`（或 `params: null`）并在 2 次 "text: Field required" 重试中持续输出无 text 的 done：params 被强转为 `{}` → 重试梯子耗尽 → "proceeding anyway" → `_action_done`（`actions.py:2745`）执行 `success = params.get("success", True)` → True、替换 "(no summary provided)" → `ActionResult(is_done=True, success=True)` → run 以 `history.is_successful() == True` 结束在占位摘要上。修复前字符串 params 会在 `_flatten_params` 的 `params.items()` 处 AttributeError → 计为失败、不终止——**这是擦除引入的新假成功路径**。**在正确深度修**：`_action_done` 在 text 缺失/为空时默认 `success=False`（覆盖所有形态），而非逐形态特判。

### 2. CONFIRMED — null/数字/布尔动作被强转成机器合成名（'None'/'123'/'True'）进入澄清重试梯子：每步多烧 2 次全上下文 LLM 调用，最终经 max_failures 终止且 history 无 done

- **位置**：`src/tree_walker/llm/client.py:343`
- **类别**：correctness

**失败场景**：小模型每步输出 `{"action": null}`（issue #173 同群）：被强转为 `{"name": "None"}` → `_validate_action_params` 返回 "Unknown action 'None'" → 2 次重试带着 "Your action parameters are invalid: Unknown action 'None'"（模型从未输出过这个名字，无从修起）重发全部消息历史 → `Tools.execute` → `ActionResult(error)` → `consecutive_failures++` → 最多 15 次 LLM 调用后在 `max_failures=5` 中止，`final_result() == None`。旧代码：一次调用 + done(success=False, "Invalid action shape") 诚实终止。**合成名不应进入重试梯子**（或应映射回诚实 done）。

### 3. CONFIRMED — 新增的内存历史防御只护住 `_skip_reason` 的 click/input_text/select_dropdown 无 index 路径；`_execute_history_step` 仍有两处未防护的 `dict(action.get("params", {}))`（行 681 extract 分支、行 724 else 分支）

- **位置**：`src/tree_walker/agent/rerun.py:681`
- **类别**：correctness

**失败场景**：`rerun_history()` 接受 `model_validate`/内存构造的 `AgentHistoryList`（`rerun.py:629-632` 新注释明确声称防御的入口）。步骤 `{"actions": [{"name": "extract", "params": "561857"}]}`，或 `interacted_element[0]` 非 None 的字符串 params click（skip 被抑制；行 684 的 isinstance 守卫清空 raw_params 使 `has_index=False` → 落入行 724 的 else）→ 撞上 `dict("561857")` → ValueError → 被 `_rerun_step_with_retries` 捕获 → 3 次重试 5/10/20s 退避加每次完整 `browser.get_state()`（浪费 ~35s+）后才记录该步失败。

### 4. CONFIRMED — finally 守卫吞掉所有 `_finalize` 异常却只有一行日志：无状态标记、tombstone 或连续失败升级——确定性 `_finalize` bug 会让 run 报成功但 history 残缺

- **位置**：`src/tree_walker/agent/step.py:196`
- **类别**：correctness

**失败场景**：未来 AgentHistory/StepMetadata 字段变更或 `state_summary`/`_build_step_metadata` bug 每步都抛：每次异常被记录、循环继续；`run()` 从不检查 `self.history` 并将其作为"完成"的 run 返回。最坏是 done 步——`_step` 在失败的 finally 之前已从 try 体返回 True，run 报任务成功而 done 动作不在 history 中；之后重放该 session 会静默丢失受影响动作。`AgentState` 没有任何能暴露该降级的字段（已核实），`final_result()` 只返回 None。

### 5. CONFIRMED — 裸 done 诚实失败特判只覆盖 variant A：variant B（output_model）下强转出的 `{"text", "success"}` params 必然校验失败 StructuredDoneParams（extra="forbid"、无 text 字段、data 必填），与 docstring/测试声称的"text 占位使校验通过、立即终止"矛盾

- **位置**：`src/tree_walker/llm/client.py:561`
- **类别**：correctness

**失败场景**：variant B agent（`Agent(..., output_model=...)`，如 `examples/features/structured_output.py`）的模型输出裸 "done"：校验返回 "data: Field required; text: Extra inputs are not permitted" → 2 次浪费的重试往返、反馈自相矛盾 → "proceeding anyway" → `_action_done` variant B 捕获 `params["data"]` KeyError 后诚实终止。净效果：承诺的立即终止变成 3 次 LLM 调用且重试反馈误导模型；新测试只锚定了 variant A 的普通 DoneParams。

### 6. CONFIRMED — 熔断计数按 `id(handler)` 键控，混淆 handler 身份与订阅调用点：同一对象同时订阅具名事件与 `*` 时 2 个事件即触发 `_MAX_HANDLER_FAILURES`（每次 emit 重复计数），多路订阅的 handler（如 `anomaly_detector.handle`，`agent.py:213-215`）在 close 汇总中被重复追加至 3 次

- **位置**：`src/tree_walker/observability/event_bus.py:47`
- **类别**：correctness

**失败场景**：`bus.subscribe("step_end", h); bus.subscribe("*", h)`：每个 StepEndEvent 调用 h 两次、两次失败都递增同一计数器——宣称的 disable-after-3 实际 2 个事件即触发，且同一 emit 内第 3 次调用被中途跳过。今天就存在：`AnomalyDetector.handle` 失败时，其 3 个不同的 bound-method 订阅各自独立 disable 并追加 'AnomalyDetector.handle'，close 汇总（行 97 `len(self._disabled_handlers)`）对一个逻辑订阅者虚报 3 倍——而该汇总正是熔断要变得可信的东西。

### 7. CONFIRMED — agent 数据模型层现在 import `tree_walker.llm.client` 的两个下划线私有函数，把 LLM client（及其模块级 anthropic SDK import）拉进一切 import views 的模块的依赖图——包括 `tools/registry.py`

- **位置**：`src/tree_walker/agent/views.py:13`
- **类别**：architecture

**失败场景**：`tools/registry.py:12` import `agent.views`，因此 tools 层传递依赖整个 LLM client；那段 4 行的"无循环依赖"注释是承重的顺序假设——未来 client.py 只要 import 任何 view 类型（比如给 `get_action` 的返回值做 typing）就变成真循环，且下划线 helper 的任何重命名/搬移会在远处静默弄坏 views.py。**更便宜**：把两个 helper 移到无依赖的叶子模块（或转为公开），client.py 与 views.py 都从那里 import。

### 8. CONFIRMED — PR 宣称归一化 choke point 以"避免散落各处的 isinstance 副本漂移"，却又新增两份手写 params 规则副本（`rerun.py:633-635`、`step.py:1297-1302`），叠加既有 4+ 种拼法（`rerun.py:684/1179/1344`、`variable_detector.py:39`、`step.py:1496`）——且 `_execute_actions`/`_post_process` 仍无防护，PR 自己的测试把 AttributeError 固化为契约

- **位置**：`src/tree_walker/agent/rerun.py:634`
- **类别**：simplification

**失败场景**：现在 6 处以 5 种拼法编码"params 非 dict 则 {}"；下一次规则变更（强转 list params、记录强转日志、认 element_id）只改 `_normalize_actions_list` 就会漏掉绕过副本——正是 PR 声称要消除的漂移，且已经发生（finding #3 的行 681/724 崩溃即证明）。**一个共享的 `_action_params(action)` 访问器**（或在 get_action/load_from_dict 边界校验的 typed Action model）可消除整类问题。

### 9. CONFIRMED — 新增的 close() 汇总 warning 块是本 diff 唯一零测试覆盖的代码路径，违反 CLAUDE.md"新增功能或修改功能时必须同步增加测试用例"

- **位置**：`src/tree_walker/observability/event_bus.py:92`
- **类别**：test-coverage

**失败场景**：`test_disable_after_n_failures` 从不在订阅者失败后调用 close()，`test_close_callback_failure_does_not_propagate` close 的是 `_handler_failures` 为空的 bus——`if self._disabled_handlers or nonzero:` 分支没有任何测试执行过，其中的 set 推导与两个可对调的 `%d` 计数会静默回归。EventBus 测试还住在畸形动作测试文件里而非 `tests/test_event_bus.py`，bus 变更找不到它们。

### 10. CONFIRMED — `_finalize` docstring 仍写"Record history, log summary, and advance step counter"与"n_steps is incremented last…"——本 diff 把递增移到 `_step` 的 finally 后两句均为假

- **位置**：`src/tree_walker/agent/step.py:1216`
- **类别**：docs

**失败场景**：PR 的中心主题是让 `n_steps` 顺序契约显式化（单一所有者，`step.py:200-204` 注释），但函数 docstring 仍在教旧契约（"递增在此、在最后"）。下一个依赖 docstring 增加 `_finalize` 调用方或搬移代码的维护者会重新引入刚被移除的跨千行顺序危险；陈旧文本还与改名的测试 `test_finalize_no_longer_advances_step_counter` 矛盾。

## 修复建议（按根因分组）

1. **done 诚实失败语义（解决 #1/#5）**：`_action_done` 在 text 缺失/为空时默认 `success=False`——一处覆盖所有形态（dict 畸形、裸字符串、variant A/B），替代逐形态特判；随后可删除裸 done 的 variant-A 特判。
2. **合成名不进重试梯子（解决 #2）**：null/数字/布尔等非字符串畸形值映射回诚实 done 终止（一次调用），而非强转成 `'None'` 走 per-step 重试。
3. **共享 `_action_params(action)` 访问器（解决 #3/#8）**：一处实现"params 非 dict → {}"，替换全部 6 处拼法（含 `rerun.py:681/724` 两处未防护点与 `_execute_actions`/`_post_process`）；或在 get_action/load_from_dict 边界引入 typed Action model。
4. **finally 降级可观测（解决 #4）**：`AgentState` 增加 finalize 降级计数/标记（或 history tombstone 行），`run()` 收尾检查并反映到结果；连续 N 次失败升级为终止。
5. **熔断键修正（解决 #6）**：失败计数与 disable 列表按 `(handler, event)` 或订阅对象键控，close 汇总去重。
6. **import 解耦（解决 #7）**：两个下划线 helper 移到无依赖叶子模块，client/views 共同 import。
7. **测试与文档（解决 #9/#10）**：close 汇总分支补测并把 EventBus 测试迁到 `tests/test_event_bus.py`；更新 `_finalize` docstring。

**补测试清单**：

- dict 畸形 done / 裸 done（variant A + B）/ `{"action": null}` 的终止语义（成功标志、LLM 调用次数上限）
- 内存历史：extract 带字符串 params、带 interacted_element 的 click 字符串 params，重放不崩不烧重试
- 同一 handler 多路订阅的熔断触发阈值与 close 汇总计数
- `_finalize` 持续失败时降级在结果中可见
- close() 汇总分支（disabled 非空 / 仅失败计数非零两种路径）

改完运行 `uv run python -m pytest tests/ -x -v`（先 unset `ALL_PROXY` 等代理变量）。改动继续落在 `fix/173-malformed-action-params-crash` 分支，PR #174 自动更新。
