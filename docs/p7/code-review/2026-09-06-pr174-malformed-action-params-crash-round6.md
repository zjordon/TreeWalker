# Code Review（第六轮）：PR #174（fix/173-malformed-action-params-crash）

- **日期**：2026-09-06
- **目标**：修复提交 `0102b0a`（针对[第五轮 review](2026-09-06-pr174-malformed-action-params-crash-round5.md) 的修复；前几轮见 [round1](2026-09-06-pr174-malformed-action-params-crash.md) / [round2](2026-09-06-pr174-malformed-action-params-crash-round2.md) / [round3](2026-09-06-pr174-malformed-action-params-crash-round3.md) / [round4](2026-09-06-pr174-malformed-action-params-crash-round4.md)）
- **范围**：全分支 diff `master...HEAD`（相对第五轮评审时的增量修复）
- **方法**：8 个 finder 角度并行 → 去重（25 原始候选 → 10 findings）→ 8 个独立验证 agent，多条带实证复现
- **验证结论**：10 项全部 CONFIRMED（其中 #1/#7/#8 为本 diff 引入的回归）
- **测试状态**：全套 38 个失败均为本机环境问题（socks 代理破坏测试中急切构造的 Anthropic SDK、缺 PIL、Windows 路径用例），先于本提交存在；三个改动的测试文件 101/101 全过。**第五轮 #7（测试 env 无关性）尚未处理。**

## 总评

第五轮的主线修复（`AgentHistory` model_validator、停止伪造 done、降级升级修正）都落了地，但：

1. **三条回归由本轮修复自身引入**：`name_of` 透传契约破坏 observability emit（#1）、validator 就地改写调用方 dict（#8，已复现）、升级检查前移跳过 `_run_judge`（#7）。
2. **结构性根因仍未动**：`_is_valid_action`/`_validate_action_params` 只门控 `response["action"]`（= `actions[0]` 镜像），**从不校验整个 actions 列表**——#1/#2/#6 全部源于此；逐点守卫继续增殖（#9：全仓库 12+ 份分叉的三分支 dispatch 拷贝）。

下一轮若仍逐点修补，预计还会出现同类残余。**建议以"管线入口一次性归一化整个 model_output"为收口**（见修复建议第 1 项）。

## Findings（按严重度排序）

### 1. CONFIRMED — `name_of` 新的裸透传契约把 None/非 str 动作名喂进 `ToolCallEvent.action_name`（pydantic str 字段），ValidationError 杀死整步——本 diff 引入的回归（旧 `name_of` 总是返回 str）

- **位置**：`src/tree_walker/agent/step.py:1025`
- **类别**：correctness

**失败场景**：真实 client + observability 开启即可达：LLM 输出 `actions=[{"name":"click","params":{}},{"name":null,"params":{}}]`——`normalize_actions_list` 只修非 dict 条目和非 dict params，`_is_valid_action`/`_validate_action_params` 只门控 `response["action"]`（= `actions[0]`），null-name 条目到达 `_execute_actions` i=1：`name_of` 返回 None，`ToolCallEvent(action_name=None)` 在 per-action try（行 1069 只包 `tools.execute`）之外抛 "Input should be a valid string" → `_handle_step_error` 分支 3：`consecutive_failures++`，已执行的 click 结果被丢弃（results 保持 []），LLM 看到的是 pydantic 错误串而非设计中的可见 "Unknown action" 反馈。`{"name":["click"]}`（unhashable）同样崩溃，或在 `step.py:1051` 的 frozenset 成员判断处 TypeError。已用项目自身的 ToolCallEvent 复现；无测试覆盖该 emit 路径（违反 CLAUDE.md"新增功能或修改功能时必须同步增加测试用例"）。

### 2. CONFIRMED — `honest_done_action` 替换列表中段的畸形标量后，guard #1 静默截断剩余动作——无错误结果、无 LLM 反馈、`consecutive_failures` 反而被重置——且重放时会执行这个从未执行过的 done 造成分叉

- **位置**：`src/tree_walker/agent/step.py:978`
- **类别**：correctness

**失败场景**：LLM 输出 `[{"name":"click","params":{"index":5}}, null, {"name":"input_text",...}]` → client 把 null 归一化为 done(success=False) → 实况：click 执行，guard #1 在 i=1 以一条 `logger.debug` break；`_post_process` 看不到错误 → 把 `consecutive_failures` 重置为 0，模型永远不知道自己的列表是畸形的（第四轮代码会产生可见的 Unknown-action 错误）。null 在 index 0 时 done 直接执行并硬终止 run，有效的兄弟动作不被尝试。重放分叉：持久化的 history 保留了这个原 run 跳过的 done；`rerun._execute_history_step`（`rerun.py:672-675`）没有 guard #1，经 `_exec_one`（793）执行它并在 is_done（810-813）break，产生录制 run 从未创建过的 "Task completed: False" 结果。

### 3. CONFIRMED — `honest_done_action()` 携带的 `text` 参数被 variant B 的 `StructuredDoneParams`（extra="forbid"，仅 data/success/files_to_display 字段）拒绝，"一次调用诚实终止"在 variant B 下确定性烧掉至多 2 次全上下文重试——恰是该 helper 要消除的消耗（列表 index-0 路径为本 diff 新增；顶层路径是从第四轮相同的内联 dict 提取的）

- **位置**：`src/tree_walker/action_shape.py:37`
- **类别**：correctness

**失败场景**：设置了 output_model 的 Agent（done 的 param_model 在 `tools/actions.py:677-678` 换成 StructuredDoneParams）：LLM 在列表 index 0（或顶层）输出畸形标量 → client 返回 `honest_done_action()` → `_is_valid_action` 通过 → `_validate_action_params` 以 "text: Extra inputs are not permitted; data: Field required" 失败（复现核实；`_flatten_params` 不丢弃 text）→ `_validate_params_or_retry` 为模型从未输出的 done 做 2 次额外全上下文调用并反馈自相矛盾的错误，然后 "proceeding anyway"——docstring 规定 1 次调用，实际 3 次。

### 4. CONFIRMED — `AgentHistoryList.finalize_degraded_steps` 是自复位连续计数器的快照：中途回复的降级报 0——该字段自称的契约（"0 = 无降级"、调用方可"区分正常完成与 history 残缺的完成"）对该情形事实上不成立

- **位置**：`src/tree_walker/agent/views.py:234`
- **类别**：correctness

**失败场景**：`step.py:203-204` 在下一次真实 finalize 成功时把计数器清零，`agent.py:327` 只拷贝最终值：`_finalize` 在 history append 之前抛错（`step.py:1241-1265`，如行 1256 的一次性 element_tree_text 异常）的 run 永久丢失 1-2 行 history，随后回复并正常结束 → `finalize_degraded_steps=0`，与干净 run 不可区分；以 `==0` 为门槛的评测 harness 会信任并为缺步的 history 打分。只有从未回复（>=3 升级）的情形可见——diff 自己的新测试恰好只覆盖该情形（`tests/test_step_malformed_action.py:750-787`）。**更稳的修法**：逐步降级标记，或从 `n_steps` 与 `len(history)` 之差推导。

### 5. CONFIRMED — 新复位守卫判 `model_output is not None`（做了决策），但注释契约是"真正做了 LLM 决策并执行"（决策且执行）——多条交错停止路径会跑一次成功但无执行的完整 finalize，仍把降级计数洗掉

- **位置**：`src/tree_walker/agent/step.py:203`
- **类别**：correctness

**失败场景**：暂停落在 `wait_between_actions` 睡眠的数秒窗口，或 LLM 后停止检查 #2（`step.py:685-690`，返回 response）与 `_execute_actions` 入口守卫（961-962，不执行直接返回 error）之间：`_finalize` append 该步并成功 → `model_output` 非 None → `finalize_degraded_steps=0`——与 Ctrl+C 交错的确定性 `_finalize` bug 永远达不到 3 连续升级线，正是第五轮 #5 想关闭而不完全的同一洗法（经行 996-1001 的 per-action InterruptedError 路径与连接恢复分支同理）。

### 6. CONFIRMED — 新"构造 choke point" validator 不处理 dict 条目的 null/非 str name，rerun 的 `_exec_one` 在任何 try 之外 emit `ToolCallEvent(action_name=name)` 到 pydantic str 字段——observability 开启时 ValidationError 烧掉重试梯（4 次尝试 + ~35s）并硬失败该步，而非优雅的 Unknown-action 结果

- **位置**：`src/tree_walker/agent/rerun.py:844`
- **类别**：correctness

**失败场景**：历史（旧文件或内存构造）含混合步 `[{"name":"click",...},{"name":null,...}]`（纯 null 单步被 `_skip_reason`（`rerun.py:615`）拦截，但混合步和 truthy 的 `{"name":123}` 通过）正常加载——`normalize_actions_list` 从不动 dict 的 name——随后 `_execute_history_step:675` 拿到 None，`_exec_one` 在行 850 的 try 之前于行 844 emit `ToolCallEvent(action_name=None)` → ValidationError 逃逸到 `_rerun_step_with_retries`（566），5/10/20s 退避重试后 "Step N failed after 3 retries"。依赖 `enable_observability`（默认关）；obs 关闭时 `tools.execute` 返回优雅的 "Unknown action" 结果——即 `name_of` docstring 假设却从未在该路径运行的缓解。

### 7. CONFIRMED — 降级升级检查移到 done-break 之前后静默跳过 `_run_judge`：第 3 次连续降级来自 append 后 `_finalize` 失败（history 完整、done 步已记录）的 done run 完全丢失 Judge verdict——diff 前的顺序会产生 verdict

- **位置**：`src/tree_walker/agent/agent.py:310`
- **类别**：correctness

**失败场景**：`enable_judge` + `_finalize` 尾部（StepEndEvent emit 或 `_log_step_completion_summary`，`step.py:1267-1277`——都在行 1258 的 history append 之后）的确定性 bug：done 步上新顺序经升级分支（`agent.py:310-316`）break，先于 `if done: await self._run_judge()`（317-319），judgement 保持 None、恰好最需要独立验证的 run 没有 verdict 日志；diff 自己的新测试构造了该场景但禁用了 judge，因此无法察觉。

### 8. CONFIRMED — `mode='before'` model_validator 就地改写调用方 dict——包括构造随后字段校验失败的情形——腐蚀一个失败的构造器从未拥有的输入（已实证复现）

- **位置**：`src/tree_walker/agent/views.py:152`
- **类别**：correctness

**失败场景**：`mo = {"actions":[{"name":"click","params":"561257"}]}; AgentHistory(step_number=1, model_output=mo, result="not-a-list")` → validator 改写 mo（params→{}、追加 `mo["action"]`），然后 pydantic 才对 result 抛错——调用方的 dict 被一个报告失败的构造永久改写。实况影响：`_finalize`（`step.py:1258`）用与 `state.last_model_output`（1154 设置）同一个 dict 对象构造 AgentHistory，因此 history 构造现在把归一化静默写回 agent state（主线无感，旁路形态改写 state）；`web/server.py:169` 的 `load_from_dict` 同样改写原始请求体。**修法**：validator 内对浅拷贝做归一化。

### 9. CONFIRMED — 清理/层级：第五轮 #4 的修复加了第 4 个逐点访问器补丁，而非在管线入口一次性归一化 response 的 actions 列表；守卫自身还在重复包裹已处理非 dict 的 helper——全仓库 ~13 份 `get("actions") or [get("action", {})]` 分发变体并存且已互相矛盾

- **位置**：`src/tree_walker/agent/step.py:876`
- **类别**：simplification

**失败场景**：`name_of(action) if isinstance(action, dict) else ""` / `params_of(action) if isinstance(action, dict) else {}` 重复了 helper 自带的非 dict 分支（`params_of` 对一切非 dict 已返回 {}），且 name 守卫与兄弟点分叉：裸字符串动作在此处产出 "Unknown action ''"，而 `name_of` 在 663/694 产出 "click"——重试反馈与日志/obs 对模型输出了什么的说法不一致。同时 `_is_valid_action`/`_validate_action_params` 只门控 `response["action"]`、从不门控 actions 列表（#1/#2/#6 的根因）；views.py validator 里手写的三分支 dispatch 在全仓库有 12+ 份分叉拷贝（step.py:964/1169/1303，rerun.py:613/668/1244/1338/1406/1574，variable_detector.py:35，agent.py:583，views.py:197——falsy action 有的产出 `[{}]`、有的产出 `[]`），另有 rerun.py:112 与 views.py:202 两处未转换的残留守卫。**入口归一化**（或在 action_shape 提供 `actions_of`/`normalize_model_output` 共享 helper）可全部收拢。

### 10. CONFIRMED — 陈旧 warning：`normalize_actions_list` 对非 dict 条目仍记录 "coerced to named action with empty params"，但新的 else 分支已把 null/数字/布尔替换为诚实失败 done——日志现在错误描述实际发生的事

- **位置**：`src/tree_walker/action_shape.py:63`
- **类别**：observability

**失败场景**：`normalize_actions_list([123])` 记录 "action[0] malformed (int) — coerced to named action with empty params"，而该条目实际变成 `{"name":"done","params":{"text":"Invalid action shape","success":False}}`；从日志排障 issue #173 类事故的运维者会得出"模型输出带着强转名进入了重试梯"的结论，而 run 实际以失败 done 终止（或在 guard #1 截断列表）——在本 PR 针对的失败类别上误导事故响应。

## 修复建议（以入口归一化收口）

1. **管线入口一次性归一化整个 model_output（主线，解决 #1/#2/#6/#9 的根因）**：在 action_shape 提供 `normalize_model_output(mo)`（含 `actions_of`），在 `_step` 拿到 response 后立即调用一次——整个 actions 列表（含 dict 条目的 name 修复或剔除策略）在此统一成型；`_is_valid_action`/`_validate_action_params` 改为门控归一化后的完整列表。随后退役 12+ 份分发拷贝与逐点守卫（含 rerun.py:112、views.py:202 残留）。
2. **emit 侧防御（解决 #1/#6 的崩溃面）**：所有 `ToolCallEvent(action_name=...)` 以 `str(name_of(action) or "")` 形式传入（或事件字段放宽为 `str | None`）；emit 移入 per-action try 内。
3. **honest done 的 variant B 兼容（解决 #3）**：`honest_done_action` 按 agent 变体产出 params（variant B 用 `{"data": ..., "success": False}`），或终止路径绕过 param 校验直接构造 ActionResult。
4. **降级语义改为派生值（解决 #4/#5）**：弃用自复位连续计数器快照——从 `n_steps - len(history)`（或逐步标记）推导 `finalize_degraded_steps`，天然免疫交错暂停的洗法；升级判断同样用派生值。
5. **validator 拷贝归一化（解决 #8）**：`mode='before'` validator 对 `model_output` 浅拷贝后再改写，绝不触碰调用方 dict。
6. **judge 顺序（解决 #7）**：升级 break 之前先跑 `if done: _run_judge()`（或升级分支内显式执行 judge）。
7. **日志与测试（解决 #10 + 补 #1/#2/#6/#7 用例）**：更新 action_shape warning 文案；补测试：混合列表 null name 的执行/emit 路径、中段畸形标量的截断反馈与重放一致性、variant B 诚实终止调用次数、构造失败不改写调用方 dict、升级时 judge 仍运行、测试 mock Anthropic（补第五轮 #7）。

**补测试清单**：

- `actions=[{click}, {name:null}]`：不崩溃、click 结果保留、LLM 收到可见错误（覆盖 emit 路径）
- `[click, null, input_text]`：截断有错误结果/计数反馈；重放不执行未执行的 done
- variant B + 畸形标量：断言 1 次 LLM 调用即终止
- 一次性 `_finalize` 失败后回复的 run：`finalize_degraded_steps` 非 0（派生值语义）
- 交错 Ctrl+C 下持续 `_finalize` 失败：升级可达
- 构造失败（result 非法）后调用方 dict 逐字节不变
- 升级分支的 done run：judgement 非 None
- 测试 env 无关性：mock Anthropic 符号（第五轮 #7 欠账）

改完运行 `uv run python -m pytest tests/ -x -v`（先 unset `ALL_PROXY` 等代理变量）。改动继续落在 `fix/173-malformed-action-params-crash` 分支，PR #174 自动更新。
