# Code Review（第二轮）：PR #174（fix/173-malformed-action-params-crash）

- **日期**：2026-09-06
- **目标**：修复提交 `7b3b9f0`（针对[第一轮 review](2026-09-06-pr174-malformed-action-params-crash.md) 的修复）
- **范围**：6 个文件——`llm/client.py` choke point 归一化、`agent/rerun.py` 存量守卫、`agent/step.py` finally 守卫、安全投影 wrapper、15 个新测试、文档
- **验证结论**：6 项 CONFIRMED；1 项候选（测试 fixture 复制）复审判为"有意跳过的旧结论"（即第一轮 #8，已明确标记可跳过），未重复报告；纯风格类候选（冗余 `or {}`、内联 import、注释残留）不满足"可观察效果"门槛，已丢弃（冗余备注并入 rerun finding 的修法）

## 总评

**choke point 归一化本身是稳的**：所有 `StepPipeline` 输入都流经 `get_action` 或格式良好的 fallback 常量，`action`/`actions` 镜像在归一化后保持一致。第一轮 8 条中的 #1/#2/#6（归一化根因）修复到位。

遗留问题集中在**三个非 choke point 的改动都是点修补，而根因在再往下一层**：两个 step.py finding 的根因是 `EventBus.emit` 不隔离订阅者异常；rerun finding 的根因是历史加载入口没有归一化；以及整个修复所依赖的生产接线没有测试覆盖。

## Findings（按严重度排序）

### 1. CONFIRMED — 吞掉 `_finalize` 异常会连 `n_steps += 1` 一起跳过，冻结步数计数器，造成无限循环

- **位置**：`src/tree_walker/agent/step.py:188`
- **类别**：correctness

新的整体吞异常使得 `step.py:1238` 的 StepEndEvent emit 一旦抛出，`_finalize` 最后一条语句 `state.n_steps += 1`（`step.py:1245`）被跳过，`run()` 的 `while n_steps <= max_steps` 循环退化为无界 livelock。

**失败场景**：一个只在 step_end 失败的订阅者（anomaly_detector，在 `agent.py:215` 订阅 `"step_end"`——且它在 step >= max_steps*0.9 时会再发 AnomalyEvent，恰是计数器卡死所处的区间；或 `web/server.py:933` 的 on_step_end）持续抛错 → 每一步：动作成功、`consecutive_failures` 保持 0、history 行照常 append（`step.py:1225`）、emit 抛出被吞、`n_steps` 永不前进 → `run()` 无限重新调用 LLM 并重复执行带副作用的动作，无墙钟保护，history 中无界追加重复 step_number 的行。即使单次瞬时失败也会留下一条重复 step_number 记录并超出预算一步。**在正确层级修**：`EventBus.emit` 内 per-handler try/except，且/或保证 `n_steps` 递增（或重新抛出）使吞异常不能跨越计数器边界。

### 2. CONFIRMED — StepStartEvent emit 位于 try 之外，wildcard 订阅者失败仍会杀死整个 run

- **位置**：`src/tree_walker/agent/step.py:148`
- **类别**：correctness

`_step` 顶部的 StepStartEvent emit 在 try（行 155）之前，而 `EventBus.emit`（`event_bus.py:23-28`）调用 handler 无隔离——wildcard 订阅者失败仍会在下一步杀掉整个 run，即本 PR 动机所述的死亡模式只修了一半。

**失败场景**：observability 开启（在两个主运行时中被强制开启：`cli.py:41`、`web/server.py:426`），`JsonlRecorder.handle` 无防护的 write+flush（`jsonl_recorder.py:23-24`，在 `agent.py:212` 订阅 `"*"`）遇到磁盘满或文件已关闭 → 下一次 `_step()` 在 try 之外的 StepStartEvent emit 处抛 OSError → 穿透只捕获 KeyboardInterrupt 的 `run()`（`agent.py:311`）→ 任务以 `step.py:184-187` 新注释声称要防止的 778/782 式死亡告终。try 内中段的 emit（ModelCallEvent 597、ModelResultEvent 637、ToolCallEvent 995、ToolResultEvent 1062）还会被错误归因为分支 3 步骤失败（`consecutive_failures++`）。**通用修法**——在 `EventBus.emit` 内部隔离订阅者异常——覆盖当前与未来的全部调用点。

### 3. CONFIRMED — `_skip_reason` 新守卫覆盖面过窄，漏网畸形行仍在 `dict(params)` 处 ValueError

- **位置**：`src/tree_walker/agent/rerun.py:722`
- **类别**：correctness

新守卫（行 628-637）只覆盖"首个动作名为 click/input_text/select_dropdown 且无 interacted_element"的形态；通过守卫的存量畸形行仍会在 `_execute_history_step` 未防护的 `dict(action.get("params", {}))`（行 722 与 679，truthy 字符串触发 ValueError）处崩溃——与行 682 已防护的 raw_params 形成对照。

**失败场景**：存量 issue-#173 JSONL（本 PR 自身前提——`rerun.py:631` 注释）中，多动作步骤的首个动作格式良好而后续动作 params 为字符串（修复前被持久化，因为 `_project_interacted_elements` 在 selector_map 为空时提前返回 None），或任何名字不在白名单内的步骤（wait/navigate/extract/scroll 带字符串 params，或字符串 params 但 `interacted_element[0]` 非 None）→ `_skip_reason` 返回 None → replay 到达 `dict("561857")` → `ValueError: dictionary update sequence element #0 has length 1` → `_rerun_step_with_retries` 烧掉 3 次重试与 5/10/20s 退避（每次重试重复执行可重放前缀的副作用）后才记录整步失败。**更深的修法**：在历史入口处归一化一次（`load_and_rerun`，`rerun.py:322` / `AgentHistoryList.load_from_file`），而不是第 7 份局部 isinstance 拷贝（并顺手删掉行 629 现已冗余的 `or {}`）。

### 4. CONFIRMED — 没有测试真正驱动 choke point：管线测试手工调用 `_normalize_actions_list`，绕过 `LLMClient.get_action` 生产接线

- **位置**：`tests/test_step_malformed_action.py:317`
- **类别**：test-coverage

**失败场景**：有人在 `get_action` 中加了一个绕过行 362 的提前返回分支 → `test_step_malformed_action.py` 仍然全过（它手工归一化），而 `test_llm_client.py` 只喂格式良好的动作形态 → 畸形动作再次直达 `_execute_actions`/`_post_process`——正是该测试文件自己 docstring 警告的"CI 绿但生产崩"模式。归档的第一轮 review 文档（`docs/p7/code-review/2026-09-06-pr174-malformed-action-params-crash.md` 补测试清单第 1 项："client 层：畸形形态 → 统一 dict 形态单测"）要求了该测试但未交付。次级问题：fake registry 的无字段 `_DummyParams` 使"click 缺参执行 → 优雅失败"断言空洞——从未用到真实的 `ClickParams`（index 必填）。

### 5. CONFIRMED — 顶层裸字符串 action（issue #173 记录的畸形类别）仍被强转为 done(success=False) 零重试终止整个任务

- **位置**：`src/tree_walker/llm/client.py:339`
- **类别**：correctness

**失败场景**：模型输出 JSON 文本 `{"action": "click", ...}` 而非 tool_use → `_try_parse_json` 原样返回（`client.py:250-259`，对 `"action"` 无形态检查）→ `raw_action` 是 str → 行 339 的 else 分支合成 done(success=False) → `_is_valid_action` 通过（name 为 "done"）、DoneParams 接受 params、done 执行且 `_step` 返回 True（`step.py:176-177`）→ run 以失败结束，完全没用到澄清-重试梯子——而 `{"action": ["click"]}` 却会被归一化为 `{"name":"click","params":{}}` 并带着错误反馈重试。属既有分支，但它正好位于本 PR 重写的归一化路径上。

### 6. CONFIRMED — 文档一致性清理仍留矛盾：头部状态行"待实施" vs 正文已交付；TreeForge 易变值规则既列已交付又列待办

- **位置**：`docs/p7/03-task-skill-loading-design.md:3`
- **类别**：docs

**失败场景**：头部仍写"状态：设计定稿 v2（…待实施）"，而正文记录 S0a/S0b ✅ 完成、S1-S4 已执行（行 252、258、295-296）；§六/§九仍把 TreeForge 易变值蒸馏 prompt 规则列为待办（行 247"列入 TreeForge 待办"、行 346"TreeForge 蒸馏 prompt 待办"、§8.2 行 329），而 §七 S0b 条目（行 258-263）把"易变值 prompt 规则"列入已交付。扫状态行的读者会把整个方案当作未实施（重估或重开代码已存在的工作），读 §六/§九的读者会认为答案冻结缓解未上线而重开或再度推迟已交付的 TreeForge 工作——同一文档对同一规则既说已交付又说待办，正是上一轮 #7 要求消除的内部矛盾类别。"原计划（留档）"归档标记只保护 §七内部文本，上述位置均不在其覆盖内。

## 修复建议（按根因分组）

6 条 findings 对应 5 个工作项：

1. **`EventBus.emit` 内做 per-handler 隔离（一次解决 #1/#2）**：订阅者异常不再穿透 emit。同时保证吞异常不能跨越计数器边界——`n_steps += 1` 移出可吞区域（或 emit 抛出时重新抛出/仍递增），杜绝 livelock。顺带消除 try 内中段 emit 被误计为分支 3 步骤失败的问题。
2. **rerun 在历史加载入口归一化一次（解决 #3）**：在 `load_and_rerun` / `AgentHistoryList.load_from_file` 处统一修复畸形动作，替换 `_skip_reason` 的窄白名单守卫，删掉冗余 `or {}`。
3. **补 choke point 单测（解决 #4）**：经 `LLMClient.get_action` 驱动——畸形形态入、统一 dict 形态出，覆盖 text-JSON 路径与 `result["action"]` 镜像；缺参测试改用真实 `ClickParams`。
4. **顶层裸字符串 action 归一化（解决 #5）**：`client.py:339` 的 else 分支改为走归一化（与 actions 列表内同形态同等对待），不再合成 done(success=False) 终止任务。
5. **文档状态行与 TreeForge 待办表述清理（解决 #6）**。

改完运行 `uv run python -m pytest tests/ -x -v`。改动继续落在 `fix/173-malformed-action-params-crash` 分支，PR #174 自动更新。
