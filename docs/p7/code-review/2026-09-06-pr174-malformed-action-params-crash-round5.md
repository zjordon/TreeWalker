# Code Review（第五轮）：PR #174（fix/173-malformed-action-params-crash）

- **日期**：2026-09-06
- **目标**：修复提交 `6987f8f`（针对[第四轮 review](2026-09-06-pr174-malformed-action-params-crash-round4.md) 的修复；前几轮见 [round1](2026-09-06-pr174-malformed-action-params-crash.md) / [round2](2026-09-06-pr174-malformed-action-params-crash-round2.md) / [round3](2026-09-06-pr174-malformed-action-params-crash-round3.md)）
- **范围**：全分支 diff `master...HEAD`，17 个文件（源码：新增 `action_shape.py`、`agent/agent.py`、`agent/rerun.py`、`agent/step.py`、`agent/views.py`、`llm/client.py`、`observability/event_bus.py`、`tools/actions.py`，4 个测试文件）
- **方法**：8 个 finder 角度并行 → 去重 → 10 候选 → 每候选 1 个验证 agent
- **验证结论**：9 项 CONFIRMED + 1 项 PLAUSIBLE；新测试文件中 6 个失败由本报告 finding #7 复现解释，全套其余失败（PIL 缺失、Windows 路径测试等）为本分支无关的既有环境问题

## 总评

**趋势**：第四轮修复大方向全部落地（`action_shape` 共享模块、按订阅键控熔断、`_action_done` success 默认值），但**每个新 helper/策略都留了一个残余口**，其中 #1/#2 是相对 master 的行为回归。五轮下来崩溃类早已清零，剩余问题是伪造语义边缘 + 卫生项，且 **#1/#2/#4/#8/#10 同一病根：归一化分散在三层（client choke point、load_from_dict 预校验、逐点访问器），每层都有绕过**。建议下一轮以 #10 的 `model_validator` 为主线收口，作为收尾轮。

被排名规则截断未报的小项（留档）：`agent.py` 魔法数字 `3` 未命名常量、review 轮次引用注释噪音、测试文件死段落标记 "已迁出"、`name_of` 未使用的 `default` 参数与 strip 语义重复；两个较弱设计批评——EventBus 永久 disable 无恢复路径、`step.py:883` 与 `actions.py:638` 的 "Unknown action" 消息格式重复。

## Findings（按严重度排序）

### 1. CONFIRMED — `name_of()` 把畸形 name（null/""/非 str）映射到默认 "done"，伪造终止动作：guard #1 随之静默截断多动作序列剩余部分（master 此处会产生可见错误），旁路形态的单动作带 text 会执行一次成功的 done

- **位置**：`src/tree_walker/agent/step.py:966`
- **类别**：correctness

**失败场景**：LLM 输出 `{"actions": [{"name": "click", "params": {"index": 1}}, {"name": null}, ...]}`：client 归一化从不修复 name，校验只检查 `actions[0]` 镜像，因此 i=1 处 `name_of` → "done" → guard #1（`step.py:972-977`）只以一条 debug 日志 break——剩余动作被静默丢弃，无错误结果、无失败计数、无 LLM 反馈（master 的裸 `.get` 会把 None 传给 `Tools.execute` → history 中出现可见的 "Unknown action: None" 错误）。内存/旁路的单动作 `{"name": "", "params": {"text": "done!"}}` 会执行真实 done 且 success 默认 True——静默伪造的任务终止。

### 2. CONFIRMED — `normalize_actions_list` 对 actions 列表内的标量条目（null/123/true）经 `coerce_named_action` 强转成伪造名（"None"/"123"），绕过 client.py 只应用于顶层标量的诚实 done 策略（第四轮 #2）——列表包裹标量相对 master 是回归

- **位置**：`src/tree_walker/action_shape.py:58`
- **类别**：correctness

**失败场景**：LLM 输出 `{"action": [null]}`：`client.py:335-336` 走列表分支（行 344-349 的诚实 done 守卫只对非列表触发），归一化强转为 `{"name": "None", "params": {}}` → 通过 `_is_valid_action`（truthy str）→ "Unknown action 'None'" → `_validate_params_or_retry` 为模型从未输出的名字烧掉 2 次全上下文重试 → "proceeding anyway" → `Tools.execute` 报错 → `consecutive_failures++`。master 中 `[null]` 原样保留、`result["action"]=None` 过不了 `_is_valid_action`，诚实的 `_FALLBACK_DONE_OUTPUT(success=False)` 一次调用结束 run——正是 client 注释声称要防止的"合成名烧重试"结局。

### 3. CONFIRMED — finalize 降级升级（>= 3）放在 `if done: break` 之后：降级步恰是 done 步时永远不触发——正是其注释声称覆盖的"done 步不进 history"最坏情形——且降级从不暴露给调用方

- **位置**：`src/tree_walker/agent/agent.py:314`
- **类别**：correctness

**失败场景**：模型输出 done 的那一步存在确定性 `_finalize` bug：`_step` 的 finally 吞掉异常（`finalize_degraded_steps` 到 3）但仍返回 True → `run()` 在 done 分支（`agent.py:307-310`）break，先于行 314 的检查 → 返回的 history 缺 done 步；`views.is_done()`/`is_successful()` 读 `history[-1]` → False；`web/server.py:951-954` 报 `{"type":"done","success":false}` 且无 error 字段——与诚实失败不可区分，且 `_run_judge`（`agent.py:308-309`）在缺失其验证所依据 DOM 摘要的那一步的 history 上运行。

### 4. CONFIRMED — `_validate_action_params` 仍读取原始 `action.get("params", {})` 喂给 `_flatten_params`，后者对 truthy 字符串调 `params.items()`——本 PR 唯一没换成 `params_of` 的畸形 params 读取点，注入/自定义 LLM 绕过 client choke point 时校验层 AttributeError 崩溃

- **位置**：`src/tree_walker/agent/step.py:874`
- **类别**：correctness

**失败场景**：以非 tree_walker 的 LLM 对象构造 Agent（`llm` 参数只是未强制的注解；PR 自己的测试就注入 MagicMock），其 `get_action` 返回 `{"action": {"name": "click", "params": "561857"}}`：通过 `_is_valid_action`（dict + str name）→ `step.py:886` `_flatten_params("561857", "click")` → `actions.py:2848` 的 `str.items()` AttributeError → 以分支 3 步骤失败逃逸并 `consecutive_failures++`——issue #173 针对的"崩溃变失败"类在所有其他消费方都已转换后残存于校验层。

### 5. CONFIRMED — `finalize_degraded_steps` 被空跑 finalize 清零：步中 pause/stop 提前返回仍会执行 finally，`_finalize(None, ...)` 平凡成功、计数器清零——交错的 Ctrl+C 让确定性 `_finalize` bug 永远低于 >= 3 升级线，且降级无程序可见性（无 exc_info、返回的 history 无字段）

- **位置**：`src/tree_walker/agent/step.py:199`
- **类别**：correctness

**失败场景**：持续 `_finalize` 失败（磁盘满 recorder）降级第 N、N+1 步（计数 2）；用户在第 N+2 步 LLM 调用期间 Ctrl+C → `_step` 在 `step.py:170-173` 提前返回 → finally 以 `model_output=None` 运行 `_finalize` → history 块被跳过（`step.py:1234`）→ 平凡成功 → 计数器清零 → 恢复并重复：每个真实步都被降级、`agent.py:314` 的升级永不达到，`run()` 返回形状正常但无任何 error/降级字段的 `AgentHistoryList`（`step.py:201` 的逐行日志也省略 traceback）。

### 6. CONFIRMED — 无测试断言新的 `_action_done` success 默认（text/data 缺失 → False），违反 CLAUDE.md"新增功能或修改功能时必须同步增加测试用例"——回退第四轮 #1 修复整套测试依然全绿

- **位置**：`src/tree_walker/tools/actions.py:2751`
- **类别**：test-coverage

**失败场景**：新默认 `params.get("success", text is not None or data is not None)` 是本 PR 的头条假成功修复，但 `test_done.py:134`（`_run({})`）只断言 extracted_content/is_done，`test_done_success_default_true` 提供 text（新旧都过），`test_step_molded_action` 对 `_action_done` 只在注释里提及——删掉行 2751-2752 恢复 `params.get("success", True)` 重新引入静默 success=True + "(no summary provided)" 终止而零测试失败（缺：`_run({})` 断言 success is False，及 variant B data-present 为 True 的用例）。

### 7. CONFIRMED — 新测试在替换为 MagicMock 之前急切构造真实 Anthropic SDK client（`LLMClient(...)`，行 390 与 542），任何 socks:// 代理环境变量的机器上 6 个新测试失败——本 checkout 已复现（3 FAILED + 3 ERROR）

- **位置**：`tests/test_step_malformed_action.py:390`
- **类别**：test-coverage

**失败场景**：设置 `ALL_PROXY/HTTP_PROXY=socks://...` 时（httpx 只接受 http/https/socks5/socks5h），`LLMClient.__init__`（`client.py:43`，`Anthropic(...)`）在 setup 阶段抛 `ValueError("Unknown scheme for proxy URL")`：`TestGetActionChokePoint` 的 3 个测试在 setup_method 报错、`TestMalformedActionSemantics` 的 3 个在 `self._client()` 失败——PR 核心（第四轮 #2 的 null/数字诚实 done、裸 done 重试梯子）在此类机器上未被验证；mock 掉 Anthropic 符号或清理代理 env 即可环境无关（`client.client` 反正立刻被 MagicMock 覆盖）。

### 8. CONFIRMED — 四份手写"params 非 dict → {}"幸存（`rerun.py:1178`、`rerun.py:1343`、`rerun.py:721` 经 `updated.get`、`agent/variable_detector.py:39`），尽管 `action_shape.params_of` 自称唯一实现且 rerun.py 已 import 它——重建了该模块为消除副本漂移而生的局面

- **位置**：`src/tree_walker/agent/rerun.py:1178`
- **类别**：simplification

**失败场景**：`action_shape` 未来规则变更（如同时强转 list params）静默漏掉这四处，全部位于畸形历史可达路径（内存/`model_validate` 历史绕过 `load_from_dict`，见 `rerun.py:630-632` 自身注释）；`rerun.py:721` 离成为唯一未防护读取只差一次清理——移除 `_update_action_indices` 内重复的 isinstance 守卫后，`dict(updated.get("params", {}))` 就是 issue #173 字符串 params 形态的崩溃点。

### 9. CONFIRMED — close() 汇总对熔断 handler 双重报告：`failing = {s.name for s in all_subs if s.failures > 0}` 包含已 disable 的订阅（failures 停在 3、从不重置），两个计数混用单位（订阅路径 vs 去重名）

- **位置**：`src/tree_walker/observability/event_bus.py:108`
- **类别**：correctness

**失败场景**：一个 handler 3 次失败熔断后日志为 "1 subscription(s) disabled [h], 1 handler(s) with recent failures"——两个数字描述同一 handler，读起来像两个独立问题；一个 handler 同时在具名事件与 `*` 上熔断时日志为 "2 subscription(s) disabled, 1 handler(s) with recent failures"（分母不可比），排障截断观测数据的运维者会多计独立问题数。修法：failing 集合排除已 disabled 订阅（且无测试钉住重叠情形）。

### 10. PLAUSIBLE — PR 在三层散点防御畸形 model_output（client choke point、load_from_dict 预校验、逐点访问器），而 `AgentHistory` 上的 model_validator——`views.py:34` 已在 ActionResult 上使用的模式——能归一化所有构造路径；散点防御可证明不完整（原始读取残留在 `step.py:654-692`、`rerun.py:675`，`update_action_params` 在 issue #173 形态上崩溃）

- **位置**：`src/tree_walker/agent/views.py:256`
- **类别**：simplification

**失败场景**：`model_output` 类型是普通 dict（`views.py:117`），因此 `model_validate`/构造器绕过 `load_from_dict` 的归一化：`update_action_params`（`views.py:296-297`，当前仅测试使用，经 `test_history_edit.py`）对 `{"name": "click", "params": "561857"}` 先 `setdefault("params", {})` 再 `params[field]=value` → `TypeError: 'str' object does not support item assignment`；每个新消费方都必须记得自带 `name_of`/`params_of` 守卫（`step.py:659/671/690-692` 与 `rerun.py:675` 仍是裸读取），否则崩溃类回归——把 `normalize_actions_list` 移入 `AgentHistory` 的 model_validator 关闭旁路，并允许退役散点守卫。

## 修复建议（以 model_validator 收口为主线）

1. **`AgentHistory` model_validator 收口（主线，解决 #10 并关闭 #1/#2/#4/#8 的旁路类）**：把 `normalize_actions_list` 移入 `AgentHistory` 的 model_validator（对齐 `views.py:34` ActionResult 的既有模式），使 `load_from_dict`、`model_validate`、内存构造全部路径统一归一化；随后退役散点守卫，残余裸读取（`step.py:654-692`、`rerun.py:675`）换 `params_of`。
2. **停止伪造（解决 #1/#2）**：`name_of` 移除 default="done" 语义——畸形 name 映射为可见错误动作或跳过并产生错误结果，绝不映射成 done；`normalize_actions_list` 对列表内标量应用与顶层一致的诚实 done 策略（合成名不进重试梯子）。
3. **降级可观测修正（解决 #3/#5）**：升级检查移到 done-break 之前；计数器只在真实 finalize（`model_output` 非 None）成功时清零；降级信息（计数/最后异常 traceback）落到返回的 history 或 `AgentState` 字段，调用方可区分。
4. **补测试（解决 #6/#7，含 #9 用例）**：`_run({})` 断言 success is False + variant B data-present True 用例；测试 mock Anthropic 符号或清理代理 env，消除真实 client 构造；close 汇总重叠（disabled ∩ failing）用例。
5. **副本清理（解决 #8）**：`rerun.py:1178/1343/721`、`variable_detector.py:39` 全部换 `params_of`。
6. **close 汇总修正（解决 #9）**：failing 集合排除已 disabled 订阅，统一两个计数的单位。

**补测试清单**：

- `name` 为 null/"" 的多动作序列：不静默截断，有可见错误/失败计数
- `{"action": [null]}`：一次调用诚实终止，不烧重试（断言 LLM 调用次数）
- done 步 `_finalize` 失败：run 结果可区分降级（新增字段断言）
- Ctrl+C 交错的持续 `_finalize` 失败：计数不被空跑清零、最终触发升级
- 自定义 LLM 返回 `{"action": {"name": "click", "params": "561857"}}`：validation 层不崩（`params_of` 生效）
- `_action_done` success 默认：`_run({})` → False；variant B data-present → True
- socks 代理环境下全套新测试可运行（mock Anthropic 符号）
- close 汇总：同一 handler 多路订阅熔断时输出不双计

改完运行 `uv run python -m pytest tests/ -x -v`（先 unset `ALL_PROXY` 等代理变量）。改动继续落在 `fix/173-malformed-action-params-crash` 分支，PR #174 自动更新。
