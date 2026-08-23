# Task 1 轨迹解剖报告

## 一句话失败原因（先读这个）

> **Agent 全程没走错路**：第 8 步就正确到达 Bestsellers 报表页（菜单翻链接慢是因为工具结果被 500 字符截断，不是找错）；日期筛选第 11 步成功提交、报表出数 33 条；死因在最后的抄数阶段——**第 16 步模型返回了「只含 thinking 块」的响应（思考写满 max_tokens=4096 输出上限，没有剩余额度产出动作），TreeWalker 代码对这种响应零重试，直接自动 `done(success=False)` 终结任务，30 步预算还剩 13 步没用**。它不是「步数耗尽」，也不是「找错菜单/填不进表单」。菜单翻页（4 步）、表单被拒（2 步）、JS 转义报错（2 步）只是拖慢它，均非死因。该根因已在 2026-08-16 重跑中复现并抓到实锤（附三）。详见 §三。

---

> 单任务失败轨迹解剖（P7 短板反哺 · 第 1 篇）。
> **配套方案**：本文发现的问题已整理为 [`02-optimization-plan.md`](02-optimization-plan.md)（P0×3 / P1×2 / P2 及评测工作空间建议，含实施顺序与验收实验）。
> 输入：`evals/webarena/results/logs/steps_exhausted/1.log`（完整轨迹）+ `1.json`（判分），
> 代码事实均已对照本仓库源码核实（引用处标 `file:line`）。
> 结论范围：**仅本条轨迹**。agent 是随机过程，重跑可能完全不同；共性结论需多条轨迹交叉。

## 一、任务卡

| 项 | 值 | 来源 |
|---|---|---|
| intent | What is the top-1 best-selling brand in Quarter 1 2022 | `1.json` / log 行 5 |
| 参考答案 | **`Sprite`**（string_match / exact_match） | `webarena_repo/config_files/1.json:22` |
| agent 收到的描述 | intent 原文 + `起始页: http://localhost:7780/admin`（无任何品牌含义提示） | log 行 9-12 |
| 最终判分 | score **0.0**，is_done=True，is_successful=False；eval_answer `N/A`（final_result 是合成字符串，无答案可提取） | `1.json` |
| 步数 / 耗时 | **17 步（Step 0-16，上限 30，剩 13 步未用）** / 523.1s（≈30.8s/步，LLM 延迟主导） | log 行 225 |
| 原分类 | steps_exhausted | 归因报告目录名 |
| **裁决后分类** | **推翻。真实失败模式：LLM 空响应 → 零重试的 fallback done 强制提前终止**（决策基础设施故障，9 类中应归「其他」，非任务能力短板）。理由：n_steps=17/30，全程无 BUDGET WARNING（日志 grep 计数 0；17/30=57% < 75% 阈值，`step.py:486`），任务死于 Step 16 的合成 done 而非预算耗尽 | 见 §三 |

## 二、轨迹时间线

| 阶段 | 步骤区间 | 发生了什么 | 有效/浪费 |
|---|---|---|---|
| **导航期** | 0-7（8 步） | S0 猜 URL 直达报表 → 404（`"the direct URL ... returned a 404 page"`，行 31）；S1 click Reports 菜单+wait 2s（菜单未视觉展开，但 js_listeners 8→27 说明子菜单链接已在 DOM）；S2/3/4/6 `find_elements` offset 0/5/10/14 翻页读链接——**每次 LLM 只消化约 5 条**（S3 Eval: `"found 27 links ... but the output was truncated, only seeing 'Products in Cart', 'Search Terms', 'Abandoned Carts' so far"`，行 55）；S5 改用 evaluate 一次拿全 → `SyntaxError: missing ) after argument list`（行 83-84）；S7 offset 18 终于看到尾部 | S0 是合理赌注（1 步成本）；**S2-7 共 6 步只完成「找到报表入口」一件事** |
| **核心操作期** | 8-11（4 步） | S8 navigate bestsellers ✓（Eval: `"found the Bestsellers link ... note it's under report_sales, not report_product — that's why my guessed URL 404'd"`，行 114——自我修正准确）；S9 `input_text` 填 From/To + Show Report → **invalid=true** `"This is a required field."`（Magento 校验拒绝 CDP 输入，行 143）；S10 evaluate JS+input/change 事件设值 ✓（途中 1 次 params 校验重试，行 141）；S11 再点 Show Report ✓ | S9 是表单校验摩擦（+2 步），但 agent 有正确兜底手段 |
| **提取期** | 12-15（4 步） | S12 `dropdown_options` ✓ + evaluate 提取表格行 → **正则里裸换行** `SyntaxError: Invalid regular expression: missing /`（行 171-175）；S13 screenshot 备份（防御性，0 前进）；S14 用 `String.fromCharCode(10)` 自救 ✓——**结果又被 500 字符截断**，只见行 0-9（`"the result text was truncated after the 10th row ('Cass...')"`，行 202）；S15 evaluate 行 10+ ✓ | S12 失败-S13 绕路-S14 截断-S15 补提 = 转义故障 + 显示上限的双重税 |
| **猝死** | 16 | **LLM 返回无可解析响应**（该步 77.71s 全程最长）→ `"[WARNING] LLM returned no parseable response, using fallback done"`（行 211）→ `done: text=No response from LLM, success=False`（行 214）→ 任务终止，**13 步余量作废** | 非任务能力死亡，是决策层故障 |

**转折点**：
- **A（Step 2）**：菜单点击未视觉展开 → 转入 DOM 查询，随即掉进「500 字符显示上限」翻页陷阱（6 步才找到入口）。
- **B（Step 9）**：日期校验拒绝——已知短板 3（Magento 表单对 CDP 输入不友好）模式，agent 两步内自救成功。
- **C（Step 16）**：真正的死因，与任务本身无关。

## 三、失败定位

**失败第一因：Step 16 LLM 返回空响应，`client.py` 的 no-parseable-response 路径零重试、直接合成 `done(success=False)` 终止任务。**

### 关键证据

**日志侧**（行 211-219）：

```
[WARNING] tree_walker.llm.client: LLM returned no parseable response, using fallback done
  ❔ Eval: No response from LLM
  🎯 Next goal: Ending task due to empty response
  ▶️ done: text: No response from LLM, success: False
```

**代码侧——重试机制的不对称**（`src/tree_walker/llm/client.py`）：

| 响应形态 | 处理 | 位置 |
|---|---|---|
| 有文本但不可解析为 JSON | **递归重试**（补 user 消息 "You must respond using the agent_response tool..."） | `client.py:240-245`（本轨迹 Step 2/4 即走此路成功，行 42/65） |
| 无 tool_use 且**无文本** | **直接死刑**：合成 `done(success=False)` 返回，无任何重试 | `client.py:247-262` |
| step 层「空 action」 | **重试一次** clarification，仍败才 fallback done | `step.py:741-762` |

且 client 级 fallback done 是**合法 action dict**（`{"name": "done", "params": {...}}`），`_is_valid_action()` 判真 → **短路了 `step.py:738` 起步的两级重试梯子**——空响应是唯一一种「零重试即终止」的失败形态。

**空响应的直接诱因**：无法从日志确定（debug 级响应块日志 `client.py:219` 未开启）。**推测**与大上下文（state 消息 3106 snapshot 条目 + 多条长 evaluate 结果 + 每步 700+ 字符 Memory）和 77.71s 最长延迟下的输出截断/超时有关——标注推测。

### 连锁后果（死前的摩擦账）

猝死前的轨迹是「健康带摩擦」，摩擦本身不是死因但持续放血：

1. **500 字符显示上限**（`config.py:54` `display_max_chars=500`，`views.py:43` `extracted_content[:500]`）：27 条菜单链接每次只见 ~5 条 → S2/3/4/6 四步翻页；S14 的全量行提取也被截到 10 行。注：万字符落盘路径（`actions.py:1318`）本次**未触发**（`find_elements_output/` 无 2026-08-16 文件）。
2. **evaluate 转义故障 ×2**（S5 `missing )`；S12 回显的"修复后"代码里正则 `/\/g` 中嵌着**真实换行符**——LLM 在 JSON 里少写一层反斜杠，`\n` 被 JSON 解析成真换行，`_validate_and_fix_javascript`（`session.py:437-469`）只修引号/双反斜杠，不修正则字面量内的裸控制字符）→ 逼出 S13 screenshot 绕路 + S14 fromCharCode 自救。
3. **Magento 日期校验拒绝**（S9）→ +2 步（S10 JS 设值 + S11 重提交）。

摩擦合计 ~7 步（占已执行 16 步的近半），但 13 步余量说明**预算不是本轨迹的约束条件**。

## 四、自述 vs 实际

1. **Step 2 Eval 与事实不符**：`"the submenu items did not appear in the visible DOM tree"`（行 44）——27 条链接当时**就在 DOM 里**（S2 的 find_elements 立刻全数匹配到；点击后 js_listeners 8→27 也证明子菜单已挂载），只是菜单未视觉展开、元素不可见。误判无害：它引出的绕路（直接查链接）恰好正确。
2. **Step 12 Eval 诚实且准确**：`"filter submitted successfully: report shows 33 records"`（行 164）与 DOM 变化一致（snapshot 2178→2933、js_listeners 79→11），后续提取出真实行数据佐证。
3. **Step 15 Eval 是本轨迹最规范的自评**：明确报告截断（`"truncated after the 10th row"`）和分页缺口（`"33 found vs ~24 shown ... likely on page 2"`，行 202）。**本轨迹没有犯归因报告的短板 1（分页数据不完整就下结论）**——agent 清楚知道自己没读全，并计划翻页。
4. **最关键：「自述」根本不存在**。`final_result="No response from LLM"` 不是 agent 的汇报，是 `client.py:249-253` 合成的兜底字符串。分类管线把它当 agent 最终汇报消费，产出了 steps_exhausted 标签——**分类的输入本身是合成物**。这是单任务分析能提供的最直接警示：凡 final_result 为合成兜底文案的任务，其归因分类不可信。
5. **离答案一步之遥——但「一步」比初判更远（已验证，见附二）**：参考答案 `Sprite` 确实是 Q1 2022 按品牌聚合的 top-1（DB 地面真值：Sprite 9 件/6 产品 > Helios 5 > Cassia 4，断层第一）。但**仅凭它已见的 page-1 数据推不出这个结论**：可见 24 行里 Sprite 只出现 2 次（55cm/75cm 球），而 Dash、Quest 各有 3 件的产品在未见页——「可见数据启发式」会给出不安全的答案。正确推导需要全量行提取（每店 124 行/111 产品）后聚合，它死在这一步之前。行 qty 全为 1 已验证（Q1 `qty_ordered` 唯一取值 = 1）。

## 五、救回假设

**最小改动：给 `client.py` 的空响应分支加重试。** 两种等价实现：

- **实现 a（局部）**：`client.py:247` 的 `if not tool_input:` 分支内，首次空响应时走与 240-245 相同的递归重试——追加
  `{"role": "user", "content": "Your previous response was empty. Respond now with the agent_response tool, including your evaluation, memory, next goal, and action."}`
  后再调一次 `get_action`；仅重试 1 次（防递归无界）。
- **实现 b（结构性，更推荐）**：**删除 client 级的空响应 fallback done 合成**（`client.py:247-262`），让该情况返回无效响应，交给 `step.py:741-762` 既有的「empty action → clarification 重试一次 → 仍败才 `_FALLBACK_DONE_OUTPUT`」梯子——一处删除把空响应纳入已有重试路径，不新增机制。

**预期效果**：Step 16 的空响应（大概率瞬时故障）触发重试后，轨迹以 13 步余量继续：行 10+ 数据已在手（S15 成功），剩翻页取 9 条（1-2 步）+ 品牌映射（agent 原计划走 Catalog>Products 约 3-6 步；若采用「产品名首词=品牌」启发式则 1 步）+ 聚合汇报。

**置信度**：
- 「防猝死」（任务不再死于单次空响应）：**高**——机制层面确定性修复，不依赖任务内容。
- 「救到成功」（30 步内答出 Sprite）：**中**——品牌映射环节未被本轨迹验证，且单次重跑本身是随机采样。
  （2026-08-16 验证补充：品牌 = 产品名首词的聚合口径已被 DB 证实（附二），**无需** Catalog>Products 映射；剩余工作收敛为「补齐提取 + 翻页 + 聚合」，13 步余量下可行性上调，但单轨迹随机性结论不变。）

**次选改动（不作为单点，记录备查）**：evaluate 转义——在 evaluate 工具描述或 system prompt 加一句「代码里避免反斜杠转义与正则换行符，换行用 `String.fromCharCode(10)`」（agent 在 S13 已自行发现该技巧，说明可被提示引导）；或给 `_validate_and_fix_javascript` 补「正则字面量内裸换行 → `\n`」的修复。本轨迹可省 ~3 步，但阻止不了 Step 16 猝死。

---

## 附：数据局限与关联

- **单次随机采样**：以上全部结论只描述这条轨迹。GLM 空响应可能是一次性故障；「500 字符上限 + evaluate 转义」的摩擦税是否在同类任务中普遍存在，需其他轨迹交叉验证。
- **与 issue #167 的两点互馈**：
  1. **审计分类桶**：24 个「步数耗尽」任务中应先过滤 final_result 含 `"No response from LLM"` 的成员（关键词即可），本任务（n_steps=17，还拉低了该桶 29.5 的均值）说明桶里可能混有同类误标，步数预算类改动的真实盘子需先校准。
  2. **500 字符上限是「批量提取优先」的前置依赖**：#167 建议聚合任务「优先一次 evaluate 拿全量」，但本轨迹 S14 显示 **evaluate 结果同样被 `display_max_chars=500` 截断**（10 行即到顶）——光改决策提示不够，需配套调大该上限（或给 evaluate/find_elements 结果设计分段返回/摘要策略），否则批量提取拿回来仍是 500 字符。
- 证据文件：`evals/webarena/results/logs/steps_exhausted/{1.log,1.json}`、`webarena_repo/config_files/1.json`（均在评测工作空间 `D:\dev\git\z_jordon\evals\webarena`）。

## 附二：假设验证记录（2026-08-16，DB 地面真值）

用 [`examples/p7_verify_bestsellers_brand.py`](../examples/p7_verify_bestsellers_brand.py) 直查 WebArena Docker（`shopping_admin` 容器）里 Magento 的 `sales_bestsellers_aggregated_daily` 物化表——确定性验证，无 LLM、无浏览器：

| 假设 | 结论 | 数据 |
|---|---|---|
| H2「33 records」 | ⚠️ 部分吻合 | Q1 有销售天数 = 33（与 agent 读数吻合，每日 top-1 行数也是 33）；但 (day,product) 全量行是**每店 124 行 / 111 个产品**——「33」的确切 UI 语义未定，agent 也确实没读全 |
| H4 行 qty 全为 1 | ✅ PASS | Q1 `qty_ordered` 唯一取值 = 1.0000 |
| H5 top-1 品牌 = Sprite | ✅ PASS | **Sprite 9 件（6 产品）> Helios 5 > Cassia 4**，断层第一；rating_pos=1（每日 top-1）口径下四品牌并列 2 天——参考答案对应**全产品聚合**口径 |
| H3 分页 ~24+9 | 未验证 | DB 无法验证 UI 分页，维持日志观察 |

**附带发现（环境谜团，非 agent 能力）**：bestsellers 页的 Show Report 按钮在**全新浏览器会话**里没有任何监听（jQuery 事件空、无 onclick、零 AJAX/导航反应）；JS 设值 + input/change/keyup/blur 事件 + trusted click（`Input.dispatchMouseEvent`）+ URL 直参数（store_ids × 日期格式枚举）全部无法让网格出数据。agent 当时的会话（9222）显然能触发 AJAX 重载，差异原因未定。排查工具（表单/事件 dump、XHR/fetch/导航 hook、trusted click、URL 参数枚举）合并为 [`examples/p7_probe_bestsellers_ui.py`](../examples/p7_probe_bestsellers_ui.py)（Chrome 9223 专用实例）。

**方法学结论**：P7 假设验证优先走环境 DB（docker exec + mysql 物化表），不要复刻 UI 操作——UI 会话状态依赖让复跑不可靠，DB 是确定性的。另：DB 无独立 brand 属性列，品牌编码在产品名首词（验证时确认）。

## 附三：agent 重跑验证（2026-08-16 21:00，Chrome 9223）

用临时 wrapper（smoke_test 薄包装 + `tree_walker.llm.client` DEBUG 日志 + `CDP_PORT=9223`，事后已移除）原样重跑 task 1：**同一份 agent 代码**（本分支未动 src/，评测 venv editable 安装）、同 GLM-5.2、同 30 步上限，唯一变量是 Chrome 实例（全新 profile）。后续重跑一律用 [`examples/p7_rerun_webarena_task.py`](../examples/p7_rerun_webarena_task.py)（本仓库自带的重跑示例：直接以 TreeWalker Agent 驱动，只读评测工作空间的任务配置与 cookie，llm client DEBUG 默认开启）。

**结果：12 步死亡（481s），`final_result="No response from LLM"`——同一种死法复现，且抓到了根因实锤。**

### H1 从「机制确认」升级为「根因确认」

DEBUG 日志（`client.py:219`，本次重跑专门打开）在死亡步记录了响应块构成：

```
21:07:13,744 [DEBUG] tree_walker.llm.client: LLM response blocks: ['thinking']
21:07:13,745 [WARNING] tree_walker.llm.client: LLM returned no parseable response, using fallback done
```

**空响应的真身 = 只含 thinking 块的响应**（无 tool_use、无 text）。完整因果链：

1. GLM-5.2 thinking 模式 + `max_tokens=4096`（`config.py:183/467` 默认值，评测未覆盖）——在最难的推理步（本例：产品→品牌聚合决策），**思考写满 4096 输出上限，没有剩余额度产出动作块**；
2. `client.py:222-226` 找不到 tool_use → `client.py:229-234` 也找不到 text（thinking 块无 `.text` 属性）→ 跳过唯一的重试分支；
3. `client.py:247-262` 零重试合成 `done(success=False)` → 任务终结（本次剩 **18** 步没用）。

**佐证**：两次死亡的步耗时几乎一致（原始 77.71s / 重跑 77.85s，各自全场最长）——与「以稳定速度生成 ~4096 个 thinking token 后被上限截断」吻合。此为强推断；一锤定音需在 no-parseable 路径补记 `response.stop_reason`（预期 `max_tokens`）与 `usage.output_tokens`（预期 ≈4096）。

**救回假设相应修订（§五 的实现 b 仍有效但不再最优）**：

- **新首选：调大 `LLM_MAX_TOKENS`**（4096 → 16384，env 或默认值，一行改动）。若根因确是 thinking 耗尽上限，单纯重试很可能在原地再撞一次同样的墙（重试的上下文更长了）；加额度才是治本。**✅ 已实施（2026-08-16）**：默认值 4096→16384（`config.py` 主 LLM + fallback、`llm/client.py` 预置值、`web/server.py` 设置面注册表同步），相关 157 测试通过。
- retry（实现 a/b）保留为纵深防御：即使额度充足，也该对 thinking-only 响应重试而非直接终止。
- 两者都做最稳；只改一处则先改 max_tokens。

### 重跑的其余发现

- **fresh profile 的报表筛选故障也砸中了 agent**（印证附二的 UI 谜团不是探针脚本的问题）：Step 4 typed+真实 CDP click Show Report → 网格纹丝不动「We couldn't find any records」（与探针所见一致）；Step 5 试 URL 直参数 → 直接触发 Magento 500 错误页（"An error has happened during application run"）。烧了 6 步（3-8）后，**Step 9 靠 evaluate 内 JS 重新提交拿到 19 条数据**——即这条故障是可绕过的，JS 提交路径有效。原轨迹（长寿命 profile 的 9222）没遇到，故两轮对比支持「浏览器会话状态相关」，根因仍开放。
- **菜单翻页摩擦未复现**：本次菜单点击正常展开，3 步就到报表页（原轨迹 8 步）——500 字符截断的税是真实存在的，但这次没交，属随机性。
- **讽刺的相近结局**：死亡前 agent 的 Memory 已拿到月度数据（19 条中 Sprite 出现 3 次：Jan 的 Stasis Ball 55cm、Feb 的 Yoga Strap 8ft/6ft），比原轨迹离答案更近；死在切换 period=year 聚合的思考步上。
- **判分链路**：TreeWalker 自带 judge 正常运行并给出合理 FAILED 判词；WebArena evaluator 因 wrapper 未设站点 URL env 崩溃（`run_task.ps1:83-89` 有完整清单，已在 `rerun_task.py` 补齐）——本任务成败不受影响（done 即失败），但后续重跑需带上。
- 产物：`evals/webarena/results/rerun_9223_task1.json` + `results/logs/rerun_9223_task1.log`。

### 影响面与三次轨迹对照

- **影响面量化**：原始全量 184 任务中 `final_result="No response from LLM"` 的只有 **1 个（task 502）**——thinking 耗尽 max_tokens 是**真实但罕见**的死法（≈0.8%），修复（调大 `LLM_MAX_TOKENS`）值得做（一行、防灾难性猝死），但**不是**影响 ~15% 失败的杠杆，别高估。
- **task 1 现共有四条轨迹、三种死法**：
  | 轨迹 | 时间 | 步数 | 死法 |
  |---|---|---|---|
  | 原始评测（shopping_admin_full.json） | 08-08~14 | 29/30 | 诚实放弃："unable to determine … within the available steps"——接近真·步数耗尽 |
  | 本报告分析的 1.log | 08-16 16:46 | 17/30 | thinking-only 猝死（本文主题） |
  | 重跑（附三） | 08-16 21:00 | 12/30 | thinking-only 猝死（同根因，DEBUG 实锤） |
  | 修复验证跑（附三·验证二） | 08-17 07:24 | 22/30 | **600s 任务超时**（无猝死；离答案一步） |

  即「steps_exhausted」标签对**原始轨迹**大致成立，对后两条重跑轨迹不成立——单轨迹结论必须绑定轨迹本身（本文开头的数据局限声明即为此）。同一任务在不同采样下死法漂移，本身也是 P7 归因工作要注意的现象：**120 个失败分类基于的是 8 月上旬那批轨迹，拿今天的重跑轨迹对号入座会张冠李戴**。

### 验证二（2026-08-17 07:24，max_tokens=16384 全链路生效后的重跑）

**结论：猝死修复 ✅ 有效；该任务的真实瓶颈转为 wall-clock（600s 任务超时）。**

- **运维坑（差点让验证作废）**：仓库 `.env` 里有 `LLM_MAX_TOKENS=4096`——`config.py` import 时 `load_dotenv`（`override=False`）加载，**击败了代码默认值**。首轮验证跑（08-17 07:19）因此仍跑在 4096 上，且中途 Chrome 实例死掉（日志 Step 12 起 "DOM circuit breaker is open"），作废停止。修正 `.env` → 16384 后实测 `load_settings()` 返回 16384 才发车。**教训：改默认值必须同步核对 `.env`。**
- **猝死零复现**：22 步里没有一次 thinking-only 响应；多次 `['thinking','text']`（非 tool_use）都被既有 text-retry 路径正常救回；**Step 21 的 92.58s 超长思考步存活**（4096 时代的死亡墙在 ~77-78s，此步必死）。对照：4096 时代两跑分别死于第 16/11 步。
- **新死因：600s 任务超时**（评测 harness 同款上限，`task_timeout` 默认 600s）——Step 22 进行中被切断，步数预算还剩 8 步。慢的构成：4 次 "text (not tool_use)" 重试（每次多一轮完整 LLM 调用）+ catalog 页 DOM 快照爆炸（snapshot_entries 7575 / ax_nodes 2813，2044 个产品的网格）拉长每步推理 + Step 21 批量 AJAX 尝试本身 92.6s。
- **轨迹质量大幅改善**（这次几乎走通）：Step 5 就定位到正确报表 URL；Step 9 用「JS 原生 setter + change + form.submit()」绕过 datepicker 清值（比前几跑都快）；Step 14 拿全 15 产品 × qty（sum=19 与 Total 行核对 ✓）；Step 20 翻遍 37 列找到 **Manufacturer**（品牌属性）；死时其 Memory 里已有「Sprite=5 领先（Dash/Quest=2）」的首词聚合——**离正确答案 Sprite 只差最后一步映射**。
- **新观察（后续短板候选）**：① 模型偶发无参数动作（Step 17 `click` 裸发，报错重试，本跑出现 1 次/昨晚跑 3 次）；② catalog 类大网格页面快照膨胀拖慢推理（呼应 #167 的批量提取方向：一次 evaluate 拿数据优于在大网格页面逐步操作）；③ 环境残留过滤器（"Name: Sahara"，前序任务留下的 admin session 状态）烧了 2 步才清掉——评测环境的会话污染问题。
- 产物：`evals/webarena/results/logs/rerun_9223_task1_maxtokens16384.log`（复用文件名，内容为验证二）。
