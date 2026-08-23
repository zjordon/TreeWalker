# Task 1 短板优化方案（轨迹解剖 → 改进项）

> 来源：[`01-task1-trajectory-anatomy.md`](01-task1-trajectory-anatomy.md)——四条轨迹、三种死因（诚实放弃 / thinking-only 猝死 / 600s 超时）+ DB 地面真值验证 + max_tokens 修复后的验证二。
> 与 issue #167 的关系：#167 是**策略层**（批量提取优先、步数预算感知）；本方案是**工程层**——修策略层依赖的地基（结果可见性）、补防猝死纵深、降 wall-clock。交叉依赖在文中标注。
> 所有 file:line 均已对照源码核实（2026-08-17）。

---

## 0. 已完成项（背景，不重复实施）

| 项 | 改动 | 验证 |
|---|---|---|
| thinking 耗尽输出上限（猝死主因） | `max_tokens` 4096 → 16384：`config.py`（主 LLM dataclass + env 默认、fallback dataclass + env 默认）、`llm/client.py:47`、`web/server.py` 设置面注册表、**`.env`** | 验证二：22 步零猝死，92.58s 超长思考步存活（4096 时代死亡墙 ~77-78s） |
| **批次一（R1+R2+R3a，2026-08-17 实施并验收）** | R1：`client.py` `get_action` 加 `_no_action_retry_used` 防递归标志，空响应先 nudge 重试一次再 fallback done（三处递归透传标志）；R2：空响应 WARNING 带 `stop_reason/output_tokens/blocks`；R3a：`display_max_chars` 500→4000（`config.py` dataclass + env 默认 + `.env`；`views.py` ClassVar 按设计保持 500） | 单测新增 2 例（重试成功/重试耗尽+R2 日志断言），全量 **2323 passed**；验收重跑见 §4a |

**⚠️ 运维教训（适用于本方案所有默认值改动）**：仓库 `.env` 会击败代码默认值（`config.py:29` import 时 `load_dotenv`）。改默认值必须同步核对 `.env`，否则改动静默失效（max_tokens 修复曾因此差点验证作废）。

### §4a 批次一验收记录（2026-08-17，Chrome 9223，`examples/p7_rerun_webarena_task.py`）

- **R3a 直接生效证据**：task 1 的 Step 1 **一次 `find_elements` 即看到全部 27 条菜单链接**（Eval 原文 "enumerated 27 links … Bestsellers report URL"），Step 2 即导航正确 URL——对照轨迹一的 4 次翻页 8 步。验收标准「菜单发现 ≤2 步」达标（枚举+导航各 1 步）。
- **零猝死**：两跑均无 "no parseable response"（R1 未触发——16384 额度下本就罕见；R2 就位待命）。task 502 出现 119.83s 超长思考步，存活。
- **成功标准未达**：两任务均死于 **600s 超时**（task 1 于 Step 23、task 502 于 Step 24，各剩 7/6 步预算）。归因（非批次一改动所致）：
  1. **提交环节状态性失败**（终版结论见 R7 v4；早前「fresh 会话 typed 提交陷阱/datepicker 清值」表述作废）：8/17 运行 Show Report 点击**未触发表单提交**（zcode 回显判别：字段值始终保持原始格式 `01/01/2022`，未被服务端重格式化为 `1/1/22`）；8/16 则是值在提交瞬间为空（invalid）。输入层经 zcode 系统实验证实无问题。环境本身未坏：DB 聚合表 248 行、Q1 有 40 个 complete 订单；147 个 canceled 为 8 月上旬原始评测残留。
  2. **wall-clock 是当前硬约束**：600s 内 text-retry、裸动作参数错误（见下）、大网格页快照（catalog 7000+ 条）三者叠加。R4/R6 的必要性被再次确认。
  3. **P8（裸动作）优先级应上调**：无参数 `click` 错误 task 1 ×3、task 502 ×4（task 502 死前最后一步正是它——Update attributes 的裸 click 失败烧掉了最后机会）。每跑 3-4 次、每次烧 1 整步 + 一整轮 LLM 调用，建议并入批次二实施（提示词或 schema 层修，如 tool schema 对 index 加 "required" 强调）。
- 产物：`evals/webarena/results/logs/batch1_task{1,502}.log`。

---

## 1. 问题 → 改进项总表

| # | 问题（证据：轨迹 + 代码） | 优先级 | 改进项 |
|---|---|---|---|
| P1 | 空响应/thinking-only 零重试直接终止（`client.py:247-262`；两跑分别死于 Step 16/11，各剩 13/18 步） | **P0** | R1 重试 |
| P2 | no-parseable 路径不记 `stop_reason`/`usage`（根因定位花了两轮重跑 + 推断） | **P0** | R2 观测 |
| P3 | 动作结果渲染 500 字符上限（`views.py:43` ← `AGENT_TRUNCATE_DISPLAY`；菜单链接每次只见 ~5 条 → 翻页烧 4 步；evaluate 全量提取只见 10 行） | **P0** | R3 结果可见性 |
| P4 | text-not-tool_use 递归重试**无上限**（`client.py:240-245`；验证二 4 次重试多烧 4 轮完整调用；评测 harness 靠 600s 总闸防它挂死） | P1 | R4 重试治理 |
| P5 | evaluate 转义故障（`_validate_and_fix_javascript` `session.py:437-469` 不修正则字面量内裸换行；两跑各烧 2-4 步 + screenshot 绕路） | P1 | R5 转义防护 |
| P6 | 大网格页快照膨胀（catalog 页 snapshot 7575 条 / ax_nodes 2813 → 每步 20-90s，600s 超时主因之一） | P1-P2 | R6 快照瘦身 |
| P7 | 报表提交链路状态性失败：8/17 点击未触发表单提交（回显格式铁证）；8/16 值在提交瞬间为空——**输入层无问题**（zcode 系统证实，R7 v4） | **P1** | R7 v4：提交生效反馈（首选轻量）+ 验证状态可见性；submit_form 降为备选 |
| P8 | 模型偶发无参数动作（`click` 裸发：8/17 两跑合计 7 次，每次烧一整轮 LLM 调用；task 502 死前最后一步正是它） | **P1**（批次一验收后上调） | tool schema / 提示层让 `index` 必填；并入批次二 |
| P9 | 环境残留过滤器（"Name: Sahara"，前序任务留下的 admin session 状态，烧 2 步） | — | 评测工作空间建议（§3） |
| P10 | 评测结果无轨迹存档（`shopping_admin_full.json` 只有元数据，归因只能靠手跑复现） | — | 评测工作空间建议（§3） |
| P11 | 快照质量（zcode 发现）：headless 下 `#filter_form_submit` 不进交互快照；日历触发按钮 ax name 为字符串 `"undefined"`，无名元素易被 LLM 误选 | P2 | 快照层：无名交互元素语义化占位或过滤（dom-snapshot 侧） |
| P12 | 「提交环节失败」的触发条件未钉死：生产 `_action_click` 在 8/17 真实运行未触发表单提交，zcode 干净环境同路径成功（其实验统一 JS click 提交，恰好控制掉了该变量） | 分析项 | 下一个专项排查（见 R7 v4 §4） |

---

## 2. 改进项详设

### R1（P0）：thinking-only / 空响应重试——防猝死纵深

- **现状**：`client.py` 的三种失败形态处理不对称——非空文本不可解析 → 递归重试（`client.py:240-245`）；无 tool_use 且无 text → **直接合成 `done(success=False)`**（`client.py:247-262`），且该 done 是合法动作，短路 `step.py:738` 起步的两级重试梯子。max_tokens 修复消除了主因（思考写满额度），但纯空响应 / 其他截断形态仍会走这条死刑路径。
- **设计**：与 240-245 的 text-retry 对齐——`if not tool_input:` 分支先重试一次（追加 user 消息 "Your previous response contained no action. Respond now with the agent_response tool, including evaluation, memory, next goal, and action."），仍失败才 fallback done。重试计数与 R4 共用同一机制（实例级计数器，防递归无界）。
- **单测**：mock 首次响应只有 thinking 块、第二次正常 → 断言走了重试且任务未终止；mock 连续两次空 → 断言 fallback done。
- **验收**：`examples/p7_rerun_webarena_task.py` 重跑 task 1 与 task 502（184 全量中唯一的 "No response" 猝死任务），全程无 "No response from LLM" 终止。

### R2（P0）：no-parseable 路径补记 stop_reason / usage

- **现状**：`client.py:248` 只 warn 一句话；空响应的真身（这次是 `['thinking']`）只能靠预先开 DEBUG 才能看到，`stop_reason`（预期 `max_tokens`）与 `usage.output_tokens`（预期 ≈ 上限）从不落日志——根因定位花了两轮重跑 + 推断。
- **设计**：`client.py:248` 附近加一行：
  `logger.warning("no parseable response: stop_reason=%s output_tokens=%s blocks=%s", getattr(response, "stop_reason", "?"), ..., block_types)`
  （`block_types` 在 `client.py:217-219` 已拼好，顺手提到 WARNING。）
- **成本**：一行日志；无需专项单测（随 R1 的测试附带断言）。

### R3（P0）：结果可见性——500 字符渲染上限是「批量提取」的前置依赖

**机制（已逐层核实）**：各动作有自己的预算——evaluate 2000 字符（`actions.py:2229`）+ 万字符落盘只回 200 预览（`actions.py:2230-2232`，阈值 `config.py:60`）、find_elements 万字符落盘（`actions.py:1318`）、extract 8000（`config.py:46`）——**但最终渲染全部经过 `ActionResult.__str__` 的 500 字符截断**（`views.py:43` ← `display_max_chars` ← `.env AGENT_TRUNCATE_DISPLAY=500`），经 `system_prompt.py:180-182` 的 `[Previous Action Results]` 注入，且**只在下一步的状态消息里可见一次**（`step.py:303` 状态消息每步替换；`agent.py:521` 注释）。即：模型对任何动作结果的可见量 = 一次 × 500 字符，per-action 的 2000/8000 预算实际全部作废。

- **证据代价**：轨迹一菜单链接每次只见 ~5 条 → find_elements 翻页 4 步；轨迹一 Step 14 全量行提取只见 10 行；验证二 Step 11/13 提取同样被截断（"truncated mid-way"）。
- **设计（分两步）**：
  - **R3a（一行级）**：`display_max_chars` 500 → 4000，四处同步——`config.py:54` 默认值、`config.py:370` env 默认、`.env` 的 `AGENT_TRUNCATE_DISPLAY`、（`views.py:14` ClassVar 默认与 `agent.py:71` 注入保持不动）。风险低：结果只一步可见不累积，单步上下文增量有界（一个状态消息内 ≤ 动作数 × 4000）。
  - **R3b（约定，配合 #167）**：批量提取要真正可用还需**分页返回约定**——evaluate 结果超预算时返回带 `has_more`/`total` 的截断标记并提示用 offset/slice 分段（工具层增强），或至少在提示层约定「大结果让 JS 端先聚合再输出」。v1 先做 R3a + 提示层一句话，工具层分页留到 #167 实施时定。
- **验收**：重跑 task 1，菜单发现 ≤2 步、报表行提取 ≤2 步（对照轨迹一的 6 步 / 4 步）。

### R4（P1）：text-retry 上限——重试开销治理

- **现状**：`client.py:240-245` 对「有文本但非 tool_use」无限递归重试；评测 harness 明说是靠 600s 总闸防它挂死（`runner.py` docstring）。验证二里 4 次该重试共多烧 ~4 轮完整推理（每轮带全上下文），是 600s 超时的主要贡献者之一。
- **设计**：实例级重试计数器（R1 共用），上限 2 次；超限不再递归，返回无效响应交 `step.py:741-762` 既有的「clarification 重试一次 → fallback done」梯子。同时把重试消息从解释型改为指令型（"Do not explain. Call the agent_response tool now."），压重试轮的输出长度。
- **单测**：mock 连续 3 次文本响应 → 断言终止而非挂死；mock 第 2 次正常 → 断言成功。
- **验收**：重跑 task 1 总 wall-clock 下降（目标 ≤ 500s）；无挂死。

### R5（P1）：evaluate 转义防护

- **现状**：LLM 在 JSON 里少写一层反斜杠时，`\n` 被 JSON 解析成真实换行进入正则字面量 → `SyntaxError: Invalid regular expression`（轨迹一 Step 5/12 各一次，逼出 screenshot 绕路和 String.fromCharCode 自救）；`_validate_and_fix_javascript`（`session.py:437-469`）只修引号/双反斜杠，不修正则内的裸控制字符。
- **设计（双层）**：
  - 管线：`_validate_and_fix_javascript` 加一步——把**正则字面量内**的裸 `\n`/`\t`/`\r` 控制字符转义回字面量形式（扫描 `/.../` 区间内做替换；保守起见只处理正则内，避免误伤字符串字面量）。
  - 提示：evaluate 工具描述加一句「代码中避免反斜杠转义；正则不要跨行，换行符用 `String.fromCharCode(10)`」——agent 在两条轨迹里都自发发现了该技巧，说明模型可被提示引导。
- **单测**：构造含裸换行正则的 code → 修复后可执行且语义不变。
- **收益**：每跑省 2-4 步 + 少一轮挫败上下文。

### R6（P1-P2）：大网格快照瘦身

- **证据**：catalog 产品页（2044 条记录）snapshot_entries 7575 / ax_nodes 2813，每步推理 20-90s；验证二死于此（600s 内只跑到 Step 22）。
- **两条路线**：
  - **R6a（决策层，先做，属 #167 延伸）**：系统提示/预算提示加「大列表页优先一次 evaluate 提取所需数据并尽快离开，不要在大网格页面逐步操作」——注意状态消息是自动采集的，页内每停留一步都要付快照成本，所以只能减少停留步数。并入 #167 的「批量提取优先」提示一起实施，不单独立项。
  - **R6b（dom-snapshot 层，暂缓）**：可交互元素数上限 / 虚拟化区域跳过。dom-snapshot 是跨仓公共库，改动面大——待 #167 策略层 + R3a 落地后重测，若快照成本仍是主要瓶颈再立项。
- **验收（R6a）**：重跑 task 1，catalog 页停留步数 ≤ 3（对照验证二的 8 步）。

### R7（P1，v4 终版 2026-08-18）：报表提交链路——输入无问题，死因在提交环节的状态性失败

**证据链归档（三方收敛：本仓库探针 + 用户界面观察 + zcode 独立实验，报告在评测工作空间 `D:\dev\git\z_jordon\evals\webarena\docs\magento-date-input-test-2026-08-18.md`，配套脚本 `test_date_input.py` / `test_treewalker_date.py` / `debug_snapshot.py`）**。本节 v2/v3 的「坐标点击哑火」「控件不认 CDP 输入」结论均被推翻，以本版为准：

1. **输入层无问题（zcode 系统证实）**：playwright 三种输入方式（fill / click+逐字打字+Tab / JS 设值+事件）+ TreeWalker 生产路径（`get_state` 索引 + `_action_input_text` 直赋/打字双路径）× Period Day/Month/Year × 有头/无头——**全部成功出数**（110/15/5 行）。`_requires_direct_value_assignment` 对 `_has-datepicker` 字段返回 True 走直赋路径，实测够用。**无需新输入工具**（zcode #5：`set_date` 不做）。
2. **8/17 真实运行的死因在提交环节**（zcode 回显判别口诀）：提交成功后服务端会把日期重格式化为 locale 短格式（`01/01/2022` → `1/1/22`）；8/17 运行里 agent 看到的始终是原始格式 → **Show Report 点击从未触发表单提交**（值在、提交没发生，agent 误读为"查无数据"一路误诊到 600s）。8/16 则相反：字段被标 invalid "This is a required field" → 值在提交瞬间为空。两种表现，均为**状态性/时序性**。
3. **本仓库探针的补充证据（与 zcode 互补而非矛盾）**：raw 坐标点击（无遮挡回退的 `trusted_click`）在特定状态下打不响提交，同页 `el.click()` 可提交；值清除现象（交互后字段变空）真实存在但条件性（zcode 场景 2 特意加提交前 blur 亦全通）。注意 zcode 实验把提交环节变量控制掉了（其脚本统一 JS click 提交，自述「点击目标问题另行分析」），故未复现提交失败。
4. **P12 待钉**：生产 `_action_click`（带遮挡回退）在 8/17 真实运行状态下为何未触发表单提交，而 zcode 干净环境同路径成功——触发条件未定，是本课题的下一个专项。

**修复（按优先级重排，吸收 zcode 五条建议）**：

1. **提交生效反馈（zcode #3，首选轻量）**：提交类动作后检查「字段值是否被服务端重格式化 / URL 是否变化」——原样不动 = 提交未发生 → 当步重试或提示 LLM。判别口诀：`1/1/22` 短格式 = 查询跑了；仍是 `01/01/2022` = 没提交。
2. **验证状态可见性（zcode #2）**：`input_text` 回读时顺带采集 `aria-invalid` / `.mage-error` 标记进 ⚠️ 提示——8/16 型失败 LLM 可当步自愈（现回读只比值，检测不到「值在但被验证器拒绝」）。
3. **click 裸参数 bug（= P8，zcode #1）**：批次二实施。
4. **快照质量（zcode #4，= P11）**：headless 下 `#filter_form_submit` 不进交互快照（zcode 实验靠 JS click 兜底才发现）；日历触发按钮 ax name 为字符串 `"undefined"`——无名交互元素语义化占位或过滤。
5. **submit_form 原子动作（原 R7，降级为备选）**：同一次 evaluate「设值 + 调页面提交函数」仍是硬兜底（本仓库多轮实证），但输入既然无问题，优先做 1/2 的轻量反馈修复；注意不可用 `form.submit()`（该表单 action 指向 sales 报表 = 错误目标）。

**排查方法教训（记入 memory）**：①判元素绑定别用 `getAttribute('onclick')`（DOM 属性赋值查不到）；②探针必须复刻 agent 的聚焦/提交方式（JS focus ≠ CDP 点击；JS click ≠ 坐标点击——变量不同结论不同）；③本页部件初始化有竞态，同名实验要跑多次，单次结果会误导；④独立复现（zcode 用全新 Chrome 9224 + 生产代码）比在同实例上反复试更能隔离状态污染。

---

## 3. 评测工作空间建议（非本仓库改动，记录供决策）

1. **runner 保存逐动作轨迹**（P10）：现在只有元数据 + final_result，本次归因的每一步分析都靠手跑复现。建议 smoke/runner 把 `history.history`（每步 model_output + results 摘要）落盘。
2. **分类管线过滤合成 final_result**（P1 关联）：`final_result == "No response from LLM"` 是 client 合成的兜底字符串，不是 agent 自述，不应参与失败归因分类（task 1 因此被误标 steps_exhausted）。
3. **task_timeout 维持 600s**：与 34.8% 基线可比是硬约束；优化目标定义为「600s 内跑完」，不是放大闸门。
4. **环境残留状态**（P9）：前序任务的过滤器残留（Name: Sahara）污染后续轨迹；对 shopping_admin 建议批量跑任务间重置 admin session 过滤状态（或按 require_reset 语义处理），属评测公平性问题。

---

## 4. 实施顺序与验收实验

| 批次 | 内容 | 验收 |
|---|---|---|
| 一（P0）✅ 已交付（2026-08-17） | R1 + R2 + R3a | 全量 2323 测试过；R3a 实证生效（一次 find_elements 见全 27 条链接）；验收记录见 §4a |
| 二（P1）✅ 已交付（2026-08-18） | R4 + R5 + P8（click 裸参数）+ R7 修复 1/2（提交生效反馈 + 验证状态可见性） | 全量 **2336 测试过**；验收重跑 **task 1 史上首次成功**（12 步/~133s/答案 Sprite 正确，六条轨迹首次）；task 107 诚实失败（28/30 步，死因=短板 4 Knockout 未渲染 + 短板 2 逐月拆分，归批次三）。详见 §4b |
| 三（并入 #167 / 后续） | R3b + R6a + P11（快照质量）+ submit_form 备选 + P12 专项排查 | 按 #167 的验收（代表任务 30 步内完成；步数耗尽类失败数下降）；P12 = 钉死生产 `_action_click` 未触发表单提交的状态条件 |

### §4b 批次二验收记录（2026-08-18，Chrome 9223）

**task 1（top-1 best-selling brand Q1 2022）——六条轨迹里首次成功**：

| 轨迹 | 步数 | 时长 | 结局 |
|---|---|---|---|
| 原始评测 08-14 | 29/30 | — | 诚实放弃 |
| 1.log 08-16 | 17 | 523s | thinking 猝死 |
| batch1 两跑 08-17 | 22 / 24 | 600s 超时 ×2 | 误诊到超时 |
| **batch2（本次）** | **12** | **~133s** | **✅ done success=True，答案 Sprite 正确**（5/19 件 + 品牌前缀分组推导，与 DB 地面真值一致） |

- **R3a 实战兑现**：Step 10 一次 evaluate 拿全 19 行含 qty（旧 500 字符时代只见 10 行）→ 一步聚合出 Sprite=5 领先 → done。
- **P8 实战两次**：Step 9 步内重试后模型修正（省 1 步）；Step 2 模型三连裸发、重试 2 次未修（烧 1 步——旧版必烧，新版给 2 次机会）。
- R7-1/R7-2 未触发（本次页面状态好、提交一次成功、无验证拒绝）——反馈机制就位待命。
- 无 budget warning（12 步远未触顶）、无 text-retry（wall-clock 压力消失：每步 5-24s）。

**task 107（逐月订单统计）——诚实失败（28/30 步）**：找对地方（Orders 网格 Complete+2022 = 308 单，与 DB 一致），但逐月拆分被三堵墙挡住：网格行是未渲染的 Knockout 模板（**短板 4**）、MUI 端点 403、Reports 404——正是批次三/#167 策略层（批量提取提示 + 异步渲染等待）的范围。budget warning 从 23/30 起每步生效，最终诚实汇报部分结果而非误诊。

**新观察（小待办）**：两跑结尾均出现 `judge: Judge returned no tool_use block`——judge 侧空响应（不影响 agent 结果记录），judge 可考虑复用 R1 的重试模式。

**P12 最锐利一帧（batch2_task1.log Step 6-8，2026-08-18）**：task 1 成功跑的中间正好捕获了值丢失全过程——页面加载后 **t≈5.7s** 即开打（LLM 决策快），打字时回读通过（值在、无 invalid 标记），t≈33s 时字段已空（"date entry didn't persist"）→ **迟到的日历部件初始化后在交互中按内部空状态重写了字段**（部件模块 t≈6s 到位、t≈14s 全稳，输入撞进初始化窗口）。这统一了此前所有矛盾观察：Typed 输入动作本身有效（zcode 证实），但撞上部件初始化窗口时值不存活；zcode/复刻实验的输入时刻都在窗口之外。agent 本次 2 步自愈（Step 7 主动复查 + Step 8 换 JS 设值）——与 8/17 同症状误诊 20 步形成对照，反馈机制的价值实证。
**R7-1 盲区（批次三改进项）**：页面指纹用 `outerHTML.length`，而 `input.value` 是 property 非 attribute——**纯值清除不会改变指纹**，逃过「无效果检测」。改进：提交类点击后的复查应包含表单字段值快照（正是本次 agent Step 7 自己做的事）。

### §4d P12 结案（2026-08-18 晚，对照实验收口）：根因 = Chrome 后台节流

用户手动前台跑同一脚本两次全成功（`D:\temp\tree-walker\log\task-1-success.log`，打字 0.65s/字段、日历 mask 活着并处理输入 `2022-01-01→20220101`）——与我的无人值守后台跑（打字 5.7s/字段、值被清）形成对照。**决定性实验**：9223 Chrome 以反节流 flags 重启（`--disable-background-timer-throttling --disable-backgrounding-occluded-windows --disable-renderer-backgrounding`），在**与失败完全相同的后台/无人值守条件**下重跑 task 1（`batch3_antithrottle_task1.log`）：

| 条件 | 打字耗时/字段 | typed 值 |
|---|---|---|
| 后台、无 flags | 5.7s | 被清、按钮死 |
| 前台（用户） | 0.65s | 存活 |
| **后台 + 反节流 flags** | **0.65s** | **存活 → /filter/ 提交成功 → 33 records** |

→ **P12 关案**：Chrome 对后台/被遮挡标签页的定时器钳制 + 渲染挂起，使页面 JS（requirejs 加载潮、日历部件异步处理、按钮绑定）整体变形。前台/headless（zcode 全通过）无此问题。此前所有"死按钮""值被清""模块永不稳定"（含 settle 10s 永不 confirm）统一为节流伪影。

**影响与行动项**：
1. **所有自动化 Chrome 启动必须带反节流三 flags**——评测工作空间的 `run_task.ps1`/`run_category.ps1`（9222，用户决策，本仓库不代改）；本仓库示例的 Chrome 启动说明已记 memory。
2. **8 月上旬 184 全量基线（无人值守）可能低估 SR**——部分「表单交互失败/环境数据缺失」或是节流伪影；flags 修复后的全量重跑可量化（大实验，另行决策）。
3. B3-1 settle 的"模块永不稳定"也是节流伪影——flags 下其必要性下降，保留为无害的通用等待（timeout 可调小）。
4. 本跑 R7-1 再次实战（Export 按钮死点击当步检出）。

**§4d 补充（同晚，用户反节流 Chrome 上复跑 107/502）——节流伪影二次实锤**：

- **task 107：28/29 步两连败 → 6 步成功（~80s）**。答案 `05:8, 06:13, 07:9, 08:8, 09:10, 10:4, 11:5, 12:10` 与参考答案八个月全匹配（总和 67 与 Total 行交叉验证），judge SUCCESS。此前三堵墙（Knockout 空 `<tr>` / mui 403 / POST 404）在无节流下**全部消失或不再被走到**。日志：`antithrottle_task107.log`。
- **task 502：不再超时（batch1 死于 600s），30 步走完整个流程并提交批量操作；DB 实证 16 个 MS04-* 产品全部 `is_in_stock=0`（工作实际完成）**。败因转为「验证」：异步队列消息 + 网格行读取仍空 → agent 诚实上报 success=False → judge 按证据 FAILED（判词合理）。日志：`antithrottle_task502.log`。
- B3-3 judge 重试实战触发（attempt 1/2 后成功出判词）；R7-1 两次触发并被消费。
- 502 剩余短板（真问题，非伪影）：异步 mass-action 的完成确认流程 + 网格行提取的稳定性——记入后续。

### §4c 批次三验收记录（2026-08-18，Chrome 9223，`batch3_task{1,107}.log`）

全量 **2347 测试过**（+11 新增）。验收重跑结果：

| 任务 | batch2 | batch3 | 说明 |
|---|---|---|---|
| task 1 | ✅ 12 步成功 | **✅ 16 步成功（连续第二次）** | 答案 Sprite 正确；**judge 判 SUCCESS**（此前两跑 judge 空响应无判词——B3-3 生效或时运） |
| task 107 | ❌ 28 步诚实失败 | ❌ 29 步诚实失败 | 同一堵墙：Knockout 空 `<tr>` + mui 403 + POST 404——超出 prompt 层，属 DOM 级提取/渲染等待问题 |

**生效实证：**

- **R7-1「无效果检测」实战首发**（task 107，13:03:21）：`Clicked 'Show Report' ⚠️ The click had no visible effect (page unchanged)`——agent 下一步 Eval 直接引用该信号调整策略。8/17 型「点了没反应→误诊 20 步」的链条在反馈层被斩断。
- **watch 探针钉死竞态两方向**：部件 t≈5.1s 武装；t≈5-6s 注入的值从第一拍就没存活（武装后赋值即被清）；与 batch2 日志（输入时回读过、33s 后空）互补。

**诚实负项：**

1. **B3-1 settle 未阻止 typed 清值**：task 1 Step 5 在 t≈19-30s（远过部件武装 ~5s）输入，值仍被清——**「武装竞态」假说被本跑证伪为不充分**，清值非确定性超越时序。本环境模块数 10s 内持续爬升（166→187）永不稳定 → settle 全部以 10s 超时降级 = 每次导航固定 +10s（本任务 ~+50s）而未达主要目的（未伤结果，agent 自愈路径兜住）。调优方向：timeout 降到 ~6s（仍覆盖武装窗口）或按站点禁用。
2. **B3-2 清值提示未触发**（task 1）：点击时指纹有变化（校验标记/网格更新）→ 走了「有效果」分支跳过表单值复查；且值在点击前已被清（fv_before==fv_after 均空）。检测次序需再设计（先比值再比指纹）。
3. task 107 的墙（Knockout 空行/端点 403/404）不是 prompt 层能解的——需要 DOM 级「等行渲染」或网格自有数据通道（mui 需要正确 form_key 姿势），记入后续。

**结论**：批次三交付反馈层增强（R7-1 实战验证、judge 判词恢复）；settle 与清值检测达成「观测就位但未命中目标场景」，typed 清值的非确定性本质（时序之外还有变量）留给 P12 后续——好消息是 agent 自愈路径连续两跑稳定兜底（发现→JS 方案→正确答案）。

**指标口径**：每次重跑记录 步数 / wall-clock / "No response" 次数 / text-retry 次数 / 菜单发现步数 / 提取步数——示例的 DEBUG 日志已能直接数出这些。

**成本**：每任务重跑 ~10 分钟 + GLM tokens；单批次验证 ≤3 个任务。

---

## 5. 与既有工作的映射

| 既有项 | 关系 |
|---|---|
| issue #167（步数耗尽类） | R3a 是其「批量提取优先」的**前置依赖**（否则一次 evaluate 拿回来的还是 500 字符）；R7 即其建议 3（补充了实证）；R6a 是其策略在 wall-clock 维度的延伸 |
| 归因报告五短板 | 短板 2（聚合超预算）→ R3/R6；短板 3（表单不友好）→ R7；短板 5（REST 探测浪费）→ #167 预算感知（本方案不涉） |
| ROADMAP P7「短板反哺改进」 | 本方案即该条目的 task-1 部分；与「数据完整性自检」「异步渲染等待」两条并行不冲突 |
