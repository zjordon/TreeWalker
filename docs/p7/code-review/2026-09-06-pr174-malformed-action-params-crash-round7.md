# Code Review（第七轮）：PR #174（fix/173-malformed-action-params-crash）

- **日期**：2026-09-06
- **目标**：修复提交 `737a0f9`（针对[第六轮 review](2026-09-06-pr174-malformed-action-params-crash-round6.md) 的修复；前几轮见 [round1](2026-09-06-pr174-malformed-action-params-crash.md) / [round2](2026-09-06-pr174-malformed-action-params-crash-round2.md) / [round3](2026-09-06-pr174-malformed-action-params-crash-round3.md) / [round4](2026-09-06-pr174-malformed-action-params-crash-round4.md) / [round5](2026-09-06-pr174-malformed-action-params-crash-round5.md)）
- **范围**：全分支 diff `master...HEAD`（相对第六轮评审时的增量修复）
- **方法**：8 个 finder 角度并行 → 4 个定向验证 agent；多条实证复现
- **验证结论**：9 项 CONFIRMED + 1 项 PLAUSIBLE（#7）；1 项候选被证伪；PR 相关 112 个测试全过

## 总评

第六轮的主线修复（`normalize_model_output` 入口收口、位置感知畸形策略、honest-done 校验放行、降级累计制、validator 拷贝归一化）落地，但按验证归类仍有三层问题：

1. **2 条新回归（#1/#2）**：index-0 honest-done 无列表长度检查；history validator 套用实况策略造成重放分叉。
2. **5 条不变量只应用了一半（#3-#7）**：validator 与 normalize_model_output 分叉、`_honest_failure` 标记入载荷、标记只覆盖三个合成 done 之一、truncate 早于归一化、dict 形态 `name: null` 仍造合成名。
3. **#8 master 既有但在重写块内；#9/#10 本 PR"统一"目标自身的残留重复**。

**七轮模式教训**：策略定义在 `action_shape`，却在 live 入口、历史加载、truncate、client else 分支等 N 处各自应用不同子集——每轮修 3 处漏 1 处，下一轮那 1 处就是新 finding。**收口方向**：一个 `normalize_model_output(mo, context="live"|"history")` 承载全部"形状 × 位置 × 上下文"策略表，所有路径统一走它；`_honest_failure` 改带外机制。

被证伪候选（留档）：master 时代 done-`{}` 录制的重放 verdict 翻转——rerun 批量 verdict 从不读重执行 done 的 success，逐步翻转正是第四轮 #1 的预期修复。

## Findings（按严重度排序）

### 1. CONFIRMED — `normalize_actions_list` 的 index-0 honest-done 策略没有列表长度检查：多动作列表的畸形标量头直接硬终止整个 run，丢弃全部有效的尾部动作——相对 master 可恢复澄清重试的回归（第六轮 review 文档自己点名过该后果，落地的修复只改了 index>0 分支）

- **位置**：`src/tree_walker/action_shape.py:94`
- **类别**：correctness

**失败场景**：`max_actions>1` 且弱模型输出 `action=[null, {"name":"click","params":{"index":5}}]`：`client.py:382` 把 honest-done 设为 action 镜像，`_is_valid_action` 通过（dict、name "done"），`_validate_action_params` 经 `_honest_failure` 标记跳过，`_execute_actions` 在 i=0 执行 done（guard #1 只对 i>0 触发）→ is_done → `_step` 返回 True → `run()` 以 success=False break；有效的 click 从未被尝试，也没有任何纠正反馈发给模型。master 上镜像是 None → `_is_valid_action` 为 False → 澄清重试，模型可重新输出正确列表、任务继续。验证 agent 已实证。

### 2. CONFIRMED — `AgentHistory` model_validator 把实况管线的位置感知强转策略套到历史数据上：重放 master 时代的录制会执行原 run 从未执行过的动作（master 的 rerun 跳过非 dict 条目）

- **位置**：`src/tree_walker/agent/views.py:153`
- **类别**：correctness

**失败场景**：master 上，畸形列表条目在 per-action try 之外崩掉 `_execute_actions`（`step.py:933` 对字符串 `action.get`），但 `_step` 的 finally 仍把该步 `model_output` 持久化进 history；master 的 rerun.py 以 `if not isinstance(action, dict): continue` 跳过非 dict 条目。本 PR 之后，加载时把录制的中段 `"scroll"` 转成 `{"name":"scroll","params":{}}`，重放以默认参数执行它——改变原 run 从未改变的视口状态，后续 fingerprint 重定位随之分叉；录制的标量变成 `"None"` 动作并错误 break 该步（`rerun.py:813`）。PR 自己的注释（`action_shape.py:77-80`，第六轮 #2）正是以"重放执行未执行动作"为由不在 index>0 合成 done——却在历史加载处应用了同样的合成。验证：CONFIRMED（重试退避子主张被证伪，核心分叉成立）。

### 3. CONFIRMED — validator 手写三分支 elif dispatch，与 `action_shape.normalize_model_output` 分叉而非委托给它：非列表 truthy 的 `actions` 值完全未被归一化，`actions_of` 随后把它按字符拆分

- **位置**：`src/tree_walker/agent/views.py:150`
- **类别**：correctness

**失败场景**：手工编辑/损坏的历史文件 `model_output={"actions": "click", "action": {...}}`（issue #173 针对的畸形历史类别）：validator 第一分支不命中（非 list），elif 只归一化 action 镜像、留下 `"actions": "click"`；所有 `actions_of` 消费者拿到 `list("click") == ['c','l','i','c','k']`——web 编辑器 DetailView.tsx/ActionList.tsx 对字符串 `.map()` 抛 TypeError 崩掉步骤详情视图，`web/server.py:184` `_assert_pairing` 以长度不匹配（5 vs 1）400 拒绝该文件的一切编辑，rerun 把该步当"无动作"跳过，`_apply_manual_variable_at_location` 对 5 个字符做 action_index 边界检查——三个消费者对同一文件静默不一致。`normalize_model_output` 的行为是替换畸形 actions 为 `[action]`。已实证。

### 4. CONFIRMED — `_honest_failure` 控制流标记混在动作数据载荷里：被持久化为 saved history JSON 中的未文档化私有 key，且任何携带它的 action dict——包括 LLM 自己输出的——跳过全部参数校验

- **位置**：`src/tree_walker/action_shape.py:36`
- **类别**：correctness

**失败场景**：(a) `normalize_actions_list` 只动 name/params key，LLM 输出 `{"name":"click","params":{...},"_honest_failure":true}`（工具 schema 不禁止额外 key）穿过 client choke point，`_validate_action_params`（`step.py:885`）在 registry 查找**之前**检查标记并返回 None——任意动作绕过整个校验/重试梯。(b) 每次 honest-done 终止都把 `"_honest_failure": true` 写进持久化 history 文件（AgentHistory validator 拷贝 action dict 时不剥离），下划线前缀的模块常量成为交换格式的永久部分，web 编辑器 load/save 往返保留它、严格的外部校验器拒绝它。两点均已实证。

### 5. CONFIRMED — 标记机制只应用于三个合成 done 中的一个：client 的空响应 fallback done（`client.py:320-327`）与 `_FALLBACK_DONE_OUTPUT`（`step.py:63`）都没有 `_honest_failure`，variant B 下它们过不了 StructuredDoneParams 校验并烧 2 次全上下文重试——正是第六轮 #3 引入标记要消除的消耗

- **位置**：`src/tree_walker/llm/client.py:320`
- **类别**：correctness

**失败场景**：variant B agent（设置 output_model）：LLM 两次无可解析响应 → client R1 fallback done（params `{"text":..., "success":False}`）→ `_is_valid_action` 通过 → `_validate_action_params` 对 StructuredDoneParams 校验（data 必填、extra="forbid"）→ "data: Field required" → `_validate_params_or_retry` 做 `_PARAM_VALIDATION_MAX_RETRIES=2` 次额外全上下文调用后才 "proceeding anyway" 终止。step 级 fallback 同理。master 上已存在，但 skip 机制就建在同两个文件里却没覆盖这两个兄弟。

### 6. CONFIRMED — `_truncate_actions` 能在 `normalize_model_output`（`step.py:184`）运行之前用裸列表元素（字符串/标量）覆盖 `response["action"]`，行 669/681 的 emit/log 随即在未包裹形态上崩溃——管线入口归一化对它要防御的 bypass-LLM 形态而言来得太晚

- **位置**：`src/tree_walker/agent/step.py:661`
- **类别**：correctness

**失败场景**：注入/自定义 LLM（PR 自己声明的威胁模型——`test_custom_llm_str_params_survives_validation_layer` 正是在这一层注入）返回 `{"action": {"name":"click","params":{"index":1}}, "actions": ["scroll_down"]*6}`：`_is_valid_action` 在镜像上通过，`_truncate_actions` 见 6 > max_actions_per_step 便设 `response["action"] = kept[0] = "scroll_down"`；随后行 669 的 `name_of(...)` 对字符串还好，但 `actions[0]=null` 的同一交换产出 `action_name=None` → `ModelResultEvent(action_name=None)` ValidationError（obs 开启），行 681 的 `response.get('action', {}).get('name','unknown')` 对字符串抛 AttributeError——该步死亡而非被归一化。已内联复现。行 184 的归一化只在 `_get_next_action` 返回之后运行，即晚于校验、truncate、emit 与 assistant 消息构造。

### 7. PLAUSIBLE — `name=null` 的 dict 动作被强转为合成名 `"None"`，通过 `_is_valid_action` 并进入 Unknown-action 澄清重试梯，反馈一个模型从未输出的名字——`honest_done_action` docstring 承诺避免的结局，套在了语义相同的 dict 形态上

- **位置**：`src/tree_walker/action_shape.py:110`
- **类别**：correctness

**失败场景**：LLM 输出 `{"name": null, "params": {"index": 5}}`（issue #173 的核心 dict 变体）：choke point 把 name 改写为 `"None"`；`_is_valid_action` 通过；`_validate_action_params` 返回 "Unknown action 'None'" → 2 次全上下文重试、反馈模型无法映射回其 null（第五轮 #2 谴责过 `[null]` 形态的同一合成名烧法），然后 "proceeding anyway" → 执行错误 + `consecutive_failures++`。标量孪生（`action: null`）得到设计好的一次调用诚实终止——同一模型错误的两种形态得到相反对待。验证：PLAUSIBLE（机制确认；master 也烧 1 次澄清调用，最坏增量是 +1 次调用加困惑反馈）。

### 8. CONFIRMED — LLM 显式输出 `"next_goal": null`（key 存在）经 `client.py:381` 的 `tool_input.get("next_goal", "")` 流入 `ModelResultEvent(next_goal=None)`——pydantic str 字段——抛 ValidationError 丢弃一个本有效的步骤（master 既有，位于本 PR 重写的 emit 块内）

- **位置**：`src/tree_walker/agent/step.py:670`
- **类别**：correctness

**失败场景**：obs 开启的 run，模型输出 tool_use input `{"action": {"name":"click","params":{"index":1}}, "next_goal": null}`：`_is_valid_action`/`_validate_action_params` 只检查 action，response 通过；`response.get("next_goal", "")` → None → `ModelResultEvent` 在 `_get_next_action` 内抛 ValidationError → `_handle_step_error` 分支 3 计 `consecutive_failures`，有效的 click 步骤丢失（模型持续输出 null 则重复至 max_failures）。已验证 `ModelResultEvent(next_goal=None)` 抛错。

### 9. CONFIRMED — 裸字符串/标量 else 分支（`client.py:343-351`）逐字节重实现了 `normalize_actions_list` 的 index-0 策略（对 `""`、`" "`、`"click"`、null、7 验证输出完全一致），紧随其后行 376 的 `normalize_actions_list` 调用本已应用同一逻辑——纯漂移风险

- **位置**：`src/tree_walker/llm/client.py:343`
- **类别**：simplification

**失败场景**：index-0 畸形标量策略在本 PR 内已被重写三次（第四轮 #2、第五轮 #2、第六轮 #1）；现在每次重写都要求 client.py 与 action_shape.py 锁步修改、两份拷贝可漂移——正是 action_shape.py 为终结"12+ 散落拼法"而生的问题。更简形式：`actions_list = [raw_action]`，让 `normalize_actions_list` 应用位置感知策略；client.py 的 `coerce_named_action`/`honest_done_action` import 随之消失。

### 10. CONFIRMED — 本 PR 触碰的文件里又幸存三份手写 actions 分发拼法——`rerun.py:103-106`（与同函数内 params_of 转换只隔两行）、`views.py:202`（`_redact_history_data`）、`agent.py:585`（每步喂给 LLM 的 `<agent_history>` 块）——与 action_shape.py 的"唯一实现"宣称矛盾，且 `agent.py:585` 已在渲染与执行器不一致的描述

- **位置**：`src/tree_walker/agent/rerun.py:103`
- **类别**：simplification

**失败场景**：`model_output` 为空或旧格式的步骤，`agent.py:585` 的变体在 prompt 窗口产出 `actions=[]`/'?'，而执行器（`name_of` 默认）与 rerun 把同一步描述为 done——模型看到的 `<agent_history>` 与实际执行矛盾，且因为两种渲染"只是字符串"而无测试失败；action_shape 的每次未来规则变更（本 PR 内已有三次语义变更）都留下这三份拷贝变陈。可调用现成 helper：`actions_of`/`name_of`/`params_of`，其中两个文件已 import。

## 修复建议（以"单一策略表 + 带外标记"收口）

1. **`normalize_model_output(mo, context="live"|"history")` 单一策略表（主线，解决 #1/#2/#3/#6/#9）**：一个函数承载"形状 × 位置 × 上下文"全部策略——live 与 history 分开（history 不合成可执行动作、不套用 honest-done，只做无害化：跳过/剥离畸形条目并保留原始形态供展示）；validator 的手写 dispatch 与 client else 分支（#9，改为 `actions_list=[raw_action]`）全部委托给它；live 路径把归一化提到校验/truncate/emit **之前**（#6）。index-0 策略加列表长度检查：多动作列表的标量头走澄清重试而非硬终止（#1）。
2. **`_honest_failure` 改带外（解决 #4/#5）**：标记不进 action dict——用模块级 id 集合/独立数据类属性/wrapper 对象承载；三个合成 done（choke point、client fallback、step fallback）统一挂标记；持久化时天然不出现。
3. **dict `name: null` 与标量同策略（解决 #7）**：`name` 非 str（含 null/数字/列表）的 dict 条目走与标量一致的诚实/澄清路径，不再造 `"None"` 合成名。
4. **emit 字段防御（解决 #8 及同类）**：所有进入 pydantic 事件的字段（`action_name`/`next_goal` 等）在 emit 前统一 `str(x or "")`；`next_goal` 读取改 `tool_input.get("next_goal") or ""`。
5. **分发拷贝清理（解决 #10）**：`rerun.py:103-106`、`views.py:202`、`agent.py:585` 换 `actions_of`/`name_of`/`params_of`。

**补测试清单**：

- `[null, {click}]`：不硬终止，click 被尝试或模型收到可映射的澄清反馈（断言 run 未终止 + 反馈内容）
- master 时代录制（含中段标量/字符串条目）重放：不执行原 run 未执行的动作（逐动作断言）
- `{"actions": "click"}` 历史文件：不逐字符拆分，三个消费者（rerun/web 编辑器/pairing）一致
- LLM 输出携带 `_honest_failure` key：校验不被绕过；honest-done 持久化 JSON 中无该 key
- variant B + 空响应双次：断言 1 次调用即终止（三个合成 done 全挂标记）
- 注入 LLM 返回 `{"actions": ["scroll_down"]*6}`：truncate 不产生裸镜像，emit 不崩
- `{"name": null}` dict 形态：与 `action: null` 同等待遇（不出现 `'None'` 反馈）
- `next_goal: null`：步骤不丢，emit 不崩
- agent_history 渲染与执行器描述一致（同一 model_output 快照断言两处输出）

改完运行 `uv run python -m pytest tests/ -x -v`（先 unset `ALL_PROXY` 等代理变量）。改动继续落在 `fix/173-malformed-action-params-crash` 分支，PR #174 自动更新。
