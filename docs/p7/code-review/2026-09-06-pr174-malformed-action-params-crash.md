# Code Review：PR #174（fix/173-malformed-action-params-crash）

- **日期**：2026-09-06
- **目标**：分支 `fix/173-malformed-action-params-crash`（issue #173：778/782 任务因 LLM 畸形动作崩溃）
- **范围**：3 个文件，+264 / −5（`src/tree_walker/agent/step.py`、`tests/test_step_malformed_action.py`、`docs/p7/03-task-skill-loading-design.md`）
- **验证结论**：7 项 CONFIRMED、1 项 PLAUSIBLE、1 项候选被证伪排除

## 总评

PR 的核心动作——在 `_execute_actions` 内将畸形 LLM 动作强制转换为 dict 形态、并给历史投影加安全兜底——孤立看是正确的。但**归一化只修改了局部变量，从未回写 `model_output`**，导致 issue #173 的原始崩溃形态（`params` 为字符串）在三个下游位置依然会崩：

1. `_post_process`（`step.py:1144/1147`）
2. `_validate_action_params` → `Tools._flatten_params`（`step.py:856` → `actions.py:2840`）
3. rerun 的 `_skip_reason`（`rerun.py:629-630`——修复前畸形步骤根本不会进历史，现在会带着原始畸形数据被持久化，重放时再崩掉整个 replay）

此外新增的未脱敏 `%r` warning 日志违反了同文件自己声明的日志脱敏不变量。

**被证伪的候选**：裸字符串 `"done"` 会作为成功终止执行——不复现。`_is_valid_action` 与 fallback-done 路径会先拦截该形态（新归一化之前），且 guard #1 阻止 i>0 处的 done。

## Findings（按严重度排序）

### 1. CONFIRMED — `_post_process` 重读原始畸形 actions，晚一阶段仍然崩溃

- **位置**：`src/tree_walker/agent/step.py:1144`
- **类别**：correctness

`_execute_actions` 只归一化局部变量，`_post_process` 无防护地重读原始 `model_output["actions"]` 并抛 AttributeError。

**失败场景**：LLM 输出 `{"actions": [{"name":"input_text","params":{...}}, {"name":"click","params":"561857"}]}`（正是 issue-173/778 形态；`client.py:362-370` 只对 dict 条目 setdefault params，并把 `actions[0]` 镜像进 `"action"`，校验永远看不到坏条目）。`_execute_actions` 现在能正常执行，随后 `_post_process` 执行 `action.get("name")`/`action.get("params")`（`step.py:1144-1145`）→ 裸字符串条目 AttributeError；或把 `"561857"` 传给 `loop_detector.record_action`（`step.py:1147`）→ `_normalize_action_for_hash` 的 `params.get("index")`（`loop_detector.py:38`）→ AttributeError。异常进入 `_handle_step_error` 分支 3：`consecutive_failures += 1`（绕过 `step.py:1158` 的多动作"不计数"规则），`state.last_result` 被原始 AttributeError 文本覆盖（LLM 永远看不到承诺的优雅缺参反馈），`step.py:176` 的 `any(r.is_done)` 检查被跳过（done 已执行但任务继续跑），重复发生直至 `max_failures` → 任务提前结束。新测试只孤立驱动 `_execute_actions`，因此 CI 保持绿色而生产仍崩。

### 2. CONFIRMED — 已注册动作名的字符串 params 在到达归一化之前就崩

- **位置**：`src/tree_walker/agent/step.py:856`
- **类别**：correctness

对已注册动作名，字符串 params 的崩溃发生在 `_validate_action_params` → `Tools._flatten_params`，在 `_execute_actions` 之前，新归一化在单动作 / 位置 0 路径上是死代码。

**失败场景**：LLM 输出 `{"action": {"name": "click", "params": "561857"}}`（或 `actions[0]` 同形态，被 `client.py:370` 镜像进 `"action"`）。`_is_valid_action`（`step.py:870-874`）只检查 dict+name，随后 `_validate_action_params`（`step.py:848-856`）调用 `self.tools._flatten_params("561857", "click")`——其唯一防护是 `if not params`（`actions.py:2835`），truthy 字符串通过，然后 `actions.py:2840` 的 `params.items()` → AttributeError。它逃出了专门用来告知 LLM params 错误的 `_validate_params_or_retry` 重试机制（`step.py:800`），落地为分支 3 步骤失败（`consecutive_failures++`），而非 PR 承诺的优雅 `ActionResult.error`。只有未注册名字（`step.py:851-853` 提前返回）才能到达新归一化。

### 3. CONFIRMED — 任务崩溃被转换为重放崩溃：畸形 model_output 原样持久化进 AgentHistory

- **位置**：`src/tree_walker/agent/rerun.py:629`
- **类别**：correctness

`_skip_reason` 无防护地执行 `fp = first.get("params") or {}` 然后 `fp.get("index")`，杀掉整个 replay。

**失败场景**：修复前，`{"name":"click","params":"561857"}` 的步骤在 history append 之前就死了；现在 `_safe_project_interacted_elements` 让 `_finalize`（`step.py:1228-1235`）常规地持久化原始畸形 `model_output`（归一化仅局部；`views.py` 的 `load_from_dict` 不修复它）。重放时，`_skip_reason` 在 `rerun.py:468` 于主循环 try/finally 内被调用且无 except：truthy 字符串 `"561857"` 击败 `or {}` 兜底，行 630 的 `fp.get("index")` 抛 AttributeError，终止整个 rerun。`"click"` 在行 628 白名单里，因此必然到达此代码；`rerun.py:678/1338` 自身的 `isinstance(params, dict)` 防护表明此处漏掉了既定模式。

### 4. CONFIRMED — 新 warning 用 %r 打印原始 params，绕过脱敏不变量（可构造的密钥泄漏）

- **位置**：`src/tree_walker/agent/step.py:942`
- **类别**：security

同一循环内其他所有 params 日志点都经过 `_redact_params_for_log` 脱敏，唯独这条新 warning 用 `%r` 打印原始值。

**失败场景**：`client.py:157-166` 的 `_restore_sensitive_in_output` 把占位符替换为真实密钥值（作用于解析输出的每个字符串，包括畸形的字符串 params），且 LLM 被指示使用占位符 token（`agent.py:153-155`）。多动作输出中位置 ≥1 的 `{"name":"input_text","params":"<placeholder>"}` 到达此 warning，`params=%r` 原样打印——而相邻的逐动作日志（`step.py:976-987`）带有明确不变量"action_params 已被 client 还原为真值，必须先脱敏再打印"。WARNING 会透传到控制台/文件（`cli.py:82/127`）和 web UI SSE 流（`server.py:936-939`）。**修法**：对字符串形式脱敏（`redact_sensitive_string(str(action_params), ...)`）；注意直接复用 `_redact_params_for_log` 对非 dict 返回 `{}`（`step.py:1478`），会丢掉诊断价值。

### 5. CONFIRMED — finally 抛异常杀任务的失效模式只为投影调用关了一半

- **位置**：`src/tree_walker/agent/step.py:1241`
- **类别**：correctness

`_finalize` 的 `self._obs_bus.emit(StepEndEvent(...))` 裸调用订阅者，仍会从 finally 终止整个 run。

**失败场景**：`EventBus.emit`（`event_bus.py:25-28`）无 try/except 地调用 `handler(event)`，`agent.py:212` 接入了 wildcard `JsonlRecorder`，其 `handle()` 无防护地写/flush（`jsonl_recorder.py:23-24`）——StepEndEvent emit 期间的磁盘满或文件已关闭错误会从 `_step` 的 finally（`step.py:183-184`）抛出，而 run 循环只捕获 KeyboardInterrupt（`agent.py:311-313`），终止 run 并跳过 `n_steps += 1`（`step.py:1248`）——正是 wrapper docstring 描述的 778/838 式死亡。需要 `enable_observability`（默认关、env 门控），但正确边界是在 finally 中对 `await self._finalize(...)` 整体 try/except（或在 emit 内部），而非只包一个内部调用。

### 6. CONFIRMED — 层级错误：畸形动作强制转换在 step.py 内手写两份且语义分叉，应在 llm/client.py 既有 choke point 归一化一次

- **位置**：`src/tree_walker/agent/step.py:937`
- **类别**：simplification

**失败场景**：`client.py` 的 `get_action` 已对 actions 列表运行原地归一化循环（`client.py:362-364`：`for a in actions_list: if isinstance(a, dict): a.setdefault("params", {})`）——扩展两条强制转换即可一次性修复执行、`_post_process`、`_validate_action_params`、投影与历史持久化。而 PR 加的是第 5/6 份 isinstance-params 惯用法（已有 `variable_detector.py:39`、`rerun.py:678/1173/1338`、`step.py:1478`），且两份新副本语义分叉：`_execute_actions:937` 把裸字符串动作映射为可执行的 `{"name": str(action), "params": {}}`，而 `_project_interacted_elements:1282` 把同一条目映射为匿名无 index dict（投影 None）——必然漂移，并在下一个崩溃的消费者处出现第 N 个防护。

### 7. CONFIRMED — 设计文档内部不一致：§七仍把 S0b 当作未来工作，S0a 手工迁移路径引用已被 S0b 改名的目录

- **位置**：`docs/p7/03-task-skill-loading-design.md:294`
- **类别**：docs

**失败场景**：行 258 声明"S0b 契约修复……✅ 已交付 2026-09-06"，但行 294 仍是"工作量重估：S0a 约五分钟（手工，可先做）；S0b 半天（TreeForge，可后补）"——同一文档既说已交付又说待办。行 253-254 指示从 treeforge `data/skills/domain-skills/localhost/` 拷贝（行 60 也是旧路径），而行 262 说该目录已改名 `localhost/`→`localhost_7780/`，文档化的手工迁移来源已不存在；行 289-290 仍以"S0b 落地前……S0b 的落地判据之一"为待办口吻，§九风险表（行 343）仍写"S0b 落地前常驻……S0b 落地根治"——与新的"§2.5 形态 A 蒸馏直装自此可用"状态矛盾。

### 8. PLAUSIBLE — 测试装置手工复制兄弟文件的 fixture（第 3 份签名漂移的 _make_registry、第 2 份逐字复制的 StepPipeline fake）

- **位置**：`tests/test_step_malformed_action.py:37`
- **类别**：test-coverage

**失败场景**：此处的 `_make_registry` 丢了 `test_multi_act.py:26` 具有的 `terminating` 参数，导致这些测试无法表达 guard #4（`terminates_sequence`）场景，三份 builder 将各自漂移；`_ProjectionAgent` 近乎逐字复制 `_FakeAgent`（`test_step_finalize.py:13-30`），`_loop_state()`（行 87）就是 `_projection_state({})`（行 157）。被仓库"每文件局部 fake"的主流惯例与文件头声明的有意复制削弱——低优先级清理，但把一个 `_FakeStepPipeline`/registry 工厂上提到 `conftest.py`（已为 9 个测试文件提供共享工厂）能让下一次 `_finalize` 契约变更（正是本 PR 修复的 bug 类别）从三文件排查变为一行 fixture 更新。

## 修复建议（按根因分组）

8 条 findings 实际对应 4 个工作项：

1. **核心修复——归一化上移 choke point（一次解决 #1/#2/#3/#6）**：在 `client.py:362` 既有归一化循环中扩展两条强制转换（裸字符串动作 → `{"name": <str>, "params": {}}`；dict 但 params 为字符串 → 置空 `{}` + warning），原地修复 `actions` 列表与镜像的 `action` 字段。随后删除 `step.py` 新增的两处本地归一化。`rerun.py:629` 仍需单独加 `isinstance(params, dict)` 防御——归一化只保护新写入的历史，旧 JSONL 中可能已有畸形数据。
2. **日志脱敏（#4）**：`step.py:942` 的 warning 改用 `redact_sensitive_string(str(action_params), ...)`；不能直接复用 `_redact_params_for_log`（对非 dict 返回 `{}` 会丢诊断信息）。
3. **finally 边界（#5，低优先但改动小）**：在 `_step` 的 finally 中对 `await self._finalize(...)` 整体 try/except，或让 `EventBus.emit` 隔离订阅者异常。
4. **文档一致性（#7）+ 测试工厂上提（#8，可跳过）**。

**补测试清单**：

- client 层：畸形形态 → 统一 dict 形态单测
- step 端到端：畸形动作走完 `_execute` + `_post_process` 不崩；多动作失败不误计 `consecutive_failures`（现有测试只孤立驱动 `_execute_actions`，正是 CI 绿但生产崩的原因）
- rerun：加载含畸形历史不崩
- 日志脱敏断言（无原始敏感值）

改完运行 `uv run python -m pytest tests/ -x -v`。所有改动落在 `fix/173-malformed-action-params-crash` 分支，PR #174 自动更新。
