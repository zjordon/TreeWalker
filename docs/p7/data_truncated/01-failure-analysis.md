# data_truncated 类失败分析报告

> **分析对象**：新基线（antithrottle，SR 52.2%，96/184）88 个失败任务中 GLM 自述归入 data_truncated 的 17 个任务。
> **数据源**：`evals\webarena\results\logs\data_truncated\{task}.log/.json`（2026-08-20/21 重跑采集，与新基线同条件：反节流 flags、30 步/600s 上限）+ `webarena_repo\config_files\*.json`（官方判分配置）+ `shopping_admin_antithrottle.json`（基线判分）。
> **方法**：轨迹逐条深读（4 路并行提取 + 交叉验证）× 判分配置对照 × DB 转储旁证（214）。
> **重要时间线**：重跑（8/20-21）**早于** form_interaction 批次修复（8/24 起：网格 kick、type_text change、domain-skills Magento 手册、loader CWD 回退均未生效）。本报告所有"可救性"结论均以这一点为前提。
> 旧先验分析（`failure-analysis-2026-08-14.md`）的"数据提取不完整"桶当时仅 2 个任务，其假设（"agent 缺少数据完整性自检"）在本批轨迹中得到复验——**成立且仍是主因之一**，但已不是唯一主因。

---

## 〇、判分机制速写（后文反复引用）

| 判分类型 | 机制 | 本类任务 | 特性 |
|---|---|---|---|
| `must_include` | 规则子串匹配，作用于 **GLM 从 final_result 精简提取的 eval_answer**（`runner.py:380-386`） | 62/64/107/116/128 | 提取步骤有信息丢失风险 |
| `fuzzy_match` | **LLM judge（GLM 替换 OpenAI，`cdp_evaluator.py:257-340`）**，逐条参考串全部通过才得分 | 119/121/213/214/215/186 | 措辞敏感、非确定性 |
| `program_html` | 最后页面（`url:"last"`）或指定 URL 上 JS locator 读值比对（`evaluators.py:244-333`） | 679/705/707/709/780/782 | 对表单字段终值的字符串格式做 exact_match |

---

## 一、类别校验结论

**有效任务 6 个 / 归类存疑 11 个。** "data_truncated"作为自述分类只有 ~1/3 名实相符；其余实为表述层、格式层、判分器/参考答案缺陷三类。逐任务判定：

| task | 重跑 | 判定 | 实际失败模式（基线失败的原因） |
|---|---|---|---|
| 62 | ✅ 1.00 | **有效** | 真截断：订单网格 KO 行渲染冻结，基线 30 步读不到行（基线 final："grid rows are rendered asynchronously and all DOM/XHR scraping failed"）。重跑第 10 步靠一张 screenshot 踢活网格后 3 步完成 |
| 64 | ❌ | **有效** | 真截断：只拿到第 2 页缓存 108/308 行，第 1 页 200 行三条路（UI 翻页/mui XHR/uiRegistry paging）全断，答案缺 4 个正确客户 |
| 107 | ❌ | **有效** | 真截断：页 1 后半 KO 冻结，44/67 条 Complete 订单未读到，Jul–Nov 被当作 0 提交 |
| 128 | ❌ | **有效** | 真截断（局部）+ 推断替代：网格靠截图 kick 解锁，但订单 299 的 qty 没实读，用"小计=单价×1"推断 → 答 8（参考 9） |
| 186 | ❌ | **有效** | 真截断（最彻底）：KO 行全程空文本 + mui/uiRegistry/scroll 四通道全断，29 步颗粒无收；差一步（枚举 uiRegistry 组件名） |
| 116 | ✅ 1.00 | **有效**（基线为截断） | 基线只找到 1 个不满客户（漏读其余 tank 产品评论）；重跑靠 AJAX 通道补齐 27 条后通过 |
| 119 | ❌ | **存疑** | → 答案表述层：3 条评论识别全、339 全文已读，但答案缺 "color and style" 原词（fuzzy judge 判 0）；次要：337/338 未打开读全文（338 里的 "very stylish" 从未进入视野） |
| 121 | ✅ 1.00 | **存疑** | → 答案表述层：基线数据完整（2/2 评论全文），fuzzy judge 判 0；重跑同路径同数据，措辞贴参考（"true to size" 逐字）通过 |
| 213 | ❌ | **存疑** | → 答案表述层（最冤）：3 条评论全文全读、语义全中（"not good for high-impact activities" vs 参考 "Not suitable for high-impact workouts"），fuzzy judge 仍判 0 |
| 215 | ✅ 1.00 | **存疑** | → 答案表述层：与 121 同款，重跑用词直贴参考（quality/fit/warmth/color 逐点）通过 |
| 679 | ❌ | **存疑** | → 判分器用词缺陷：Status=Complete 过滤正确（308→153）且全部行提取成功；判分器要求最后页面过滤器条 outerText 含 **"Completed"**，而 Magento 状态 label 只有 "Complete"——UI 上不可满足（推断，缺页面文本直接观测，见发现 9） |
| 705 | ✅ 1.00 | **存疑** | → 路线层：基线用了 Sales>Orders 网格 + 日期过滤（判分器读的是报表页 `sales_report_from/to` 字段，locator 不存在）；重跑走报表页通过 |
| 707 | ❌ | **存疑** | → 格式失配：输入 `01/01/2022`（前导零），判分器要 `1/1/2022`；终态又被页面规范化为 `01/01/22`。数据（2022 全年报表）完整 |
| 709 | ❌ | **存疑** | → 格式失配：`05/01/2021`/`03/31/2022` vs 要求 `5/1/2021`/`3/31/2022`。数据完整（2021 无订单是数据集事实） |
| 780 | ❌ | **存疑** | → 环境价格漂移：操作链全对（$84→$101，保存+回读验证），判分器要 `64.00`（=47+17）；两次运行所见原价均为 $84，全日志无任何 47/64 线索 |
| 782 | ❌ | **存疑** | → 参考答案数学错误：判分期望 22.33/21.56 = 29×0.77 / 28×0.77——把"涨 23%"算成了**乘 0.77（降 23%）**；agent 的 28×1.23=34.44 按任务字面语义正确 |
| 214 | ❌ | **存疑** | → 参考答案挂错产品：参考要点 "It is hard to find the right size. Won't last long" 对应的是 **Ana Running Short（WSH10）** 的评论 307/309（DB 全量转储实锤），Zing Jump Rope 全库仅 3 条正面评论；agent 的"无差评"结论事实正确 |

**校正后的真实分布**（17 个基线失败）：真数据截断 6 ｜ 答案表述层 4（119/121/213/215）｜ 表单格式失配 2（707/709）｜ 判分器/参考答案缺陷 4（214/679/780/782）｜ 路线层 1（705）。

**重跑通过 5 个**（62/116/121/215/705）——印证提示语"单次轨迹是随机样本"：KO 冻结本身是概率性事件（取决于合成器是否产帧），表述层失败受 LLM judge 非确定性影响。

---

## 二、真实失败机制

确属截断的 6 个任务，失败是**四条链叠加**的结果：

**链 A：KO 网格行渲染冻结——"N records found" 可见、行文本全空。**
同一形态在 5 个任务复现：107 S4-7（"Rows appear as empty `<tr>` clones"）、64 S11（"rows found (114 tr) but text/cells empty"）、128 S5-7（"rows exist (200 in data table) but innerText is empty"）、186 S18（"table rows exist (200 rows) but textContent empty"）、62 S8（"tbody rows are empty knockout templates"）。与 form_interaction 报告发现 1 同机制（7 正例）；本批新增两个细节：**冻结可发生在会话中段**（64 的行文本在 S9 可读、S11 自发消失，期间无任何 agent 动作），**重导航不救**（186 两次整页刷新后仍空）。

**链 B：解锁窗口错失 + "没读到 = 不存在"。**
唯一实证有效的解锁是截图 kick（128 S17、62 S10，快照条目数 5078→24686/4618→15218 两次跳变）。但：107 全程只计划过一次 screenshot（S11）且被序列终止规则跳过，翻到页 1 后再无 kick，44 行永不进聚合；64 的第 1 页 200 行在初载后可读 ~10 秒，被 S8 误输入（'300' 进页码框跳到第 2 页）冲掉。此后两者都不做"网格声明 total vs 实读行数"的对账：107 S26 自评 "suspicious; likely incomplete"，S27/S28 却反转为 "Verified…Data complete" 把缺失月份填 0；64 明知 108/308 仍提交 8 人名单。**内部 done-judge 全部自评 SUCCESS**——自检通道完全失真。

**链 C：交互通道失效烧步数 → 预算压力 → 早断。**
CDP click 对 Magento KO/legacy 按钮系统性无效（"no visible effect"）：Filters×3+Apply（186）、Export×4（186/64/107/128/707）、Prev/Next Page×3（64/107/116）、评论网格 Search×6（116/119/121/213，还误点过 New Review）；`mui/index/render` XHR 全线失败（Invalid Form Key / 脚手架 HTML）×15+ 次跨 5 任务。结果：读取尝试占 107 的 19/29 步、186 的 17/29 步、64 的 16/30 步。步数预算见底（≥75% 时注入 budget warning）直接促成链 B 的"证据缺失→填 0/推断"。

**链 D：旁路通道存在但发现靠运气。**
uiRegistry data-source storage 是网格数据的可靠旁路（64 S21 枚举组件名→S23 读出 108 行；62 S2 半命中）；评论网格另有 `reviewGridJsObject.setPage(n)` + AJAX `grid?isAjax=true&limit=100`（116 S13/S18）。但 186 两次猜错组件名（`product_listing`、`product_listing.product_listing`），唯一能定位正确名字的"枚举组件"动作在 S26 被 **evaluate 终止序列规则跳过**（"Action 'evaluate' terminates sequence — skipping 1/2 remaining"）——与 107 S11 计划中的 screenshot 被同一规则吞掉，两任务各差这一步。

**步数花在哪（分类统计，据轨迹逐歸类）**：

| task | 读取尝试（有产出/无产出） | 无效重复（JS 语法错/超时/无效点击） | 导航试错 | 有效推进 |
|---|---|---|---|---|
| 107（29 步） | 19（8/11） | 7 | 0 | 3 |
| 186（29 步头） | 17（0/17） | 5 | 2 | 4 |
| 64（30 步） | 16（3/13） | 4 | 4（404×3+菜单死路） | 5 |
| 128（24 步） | 10（1/9） | 5 | 2（404×2） | 5 |
| 62 重跑（14 步） | 10（2/8） | 1 | 0 | 3 |

**"一步能做完却用十步"的实证**：
- 107：先点 Status=Complete 过滤（679 实证 308→153 且单页可读，参考全集仅 67 条）→ 一次 evaluate 聚合，约 3 步；实际 29 步且答案错。
- 186：`require('uiRegistry').get('*')` 枚举 + 读 storage，2 步；实际 29 步未成。
- 64：AJAX 全量拉取（116 的方法）或初载 10 秒窗口内全量 dump，1-4 步；实际 30 步只得半页。
- 128：screenshot kick + 顶行两单 + 两次 evaluate 读 qty，约 5 步；实际 24 步，最后一步还放弃已验证的读取路径改用推断。

---

## 三、类内分化

**近成功组（差一步/一词/一格式，8 个）**：
- 128：S21 已验证 evaluate 能读 qty（同构嵌套表），S22 已把 299 页重新加载好，S23 却直接 done——差**执行一次已验证的读取**。
- 186：差**一次组件名枚举**（被序列规则跳过）。
- 107：翻页后差**一次 kick**（页 1 前段渲染证明机制有效）。
- 64：差**第 1 页**（URL `?p=1` / 早点全量 dump / AJAX 通道任一）。
- 119/213：数据全，差**判分措辞**（213 语义完全命中仍 0 分）。
- 707/709：差**日期字符串格式**（且判分器自身格式与 705 矛盾，见发现 8）。

**远失败组（agent 无论怎么做都过不了，4 个）**：214（参考挂错产品）、782（×0.77）、780（原价漂移 47≠84）、679（"Completed" 措辞）。

**分界线有三条**：
1. **判分器形态**：program_html 型任务中参考答案有缺陷的（4 个）无解；string_match 规则可预测；fuzzy 型看措辞运气（121/215 vs 213 同路径不同命）。
2. **页面类型**：KO 数据网格任务（订单/产品列表）全部陷入读取泥潭；编辑页/报表页任务（780/782/705/707/709）执行零失误——摩擦几乎全部集中在网格层。
3. **策略选择**：同为订单网格，679（任务语义要求过滤）单页完成，107/64 选择"全量翻页+客户端聚合"则坠入冻结+翻页双重陷阱。**过滤优先能把 308 行的问题变成 153 行单页的问题。**

**翻转对比（62/116/121/215/705 vs 同批失败）**：62 重跑比基线多做对的一件事就是截了一张图；116 多做对的是 AJAX 通道；121/215 靠措辞；705 靠走对页面。没有一个翻转来自"数据源变了"——全部是**执行路径上的单点差异**，这正是"随机样本"论断的实证。

---

## 四、发现清单

### 发现 1：KO 网格行渲染冻结是 data_truncated 的第一主因，且有两个被低估的子形态
- **证据**：107 S7 "Rows appear as empty `<tr>` clones; direct selectors fail"；64 S11 "rows found (114 tr) but text/cells empty"（S9 尚可读，S11 自发消失，间隔仅 agent 思考时间）；128 S5 "rows exist (200 in data table) but innerText is empty"；186 S18 "table rows exist (200 rows) but textContent empty"（两次整页刷新后依旧）；62 S8 "tbody rows are empty knockout templates (fastForEach not rendered)"。
- **机制**：窗口不可见/合成器不产帧 → KO fastForEach 模板不实例化 → 行在 DOM、innerText 空。子形态①：冻结可**中途自发出现**（64）；子形态②：**重导航不解锁**（186）——只有强制产帧（截图）有效（128 S17、62 S10 两次快照条目数量级跳变）。
- **置信度**：高（5 任务 8+ 处；与 form_interaction 发现 1 跨类别一致）。

### 发现 2："没读到 = 不存在"：缺失数据被静默填 0/推断，自检通道全失真
- **证据**：107 S26 "only Dec=9 — suspicious; likely incomplete" → S28 "Verified: page 1's only 2022 rows are Dec…Data complete"（同一证据上反转，Jul–Nov 填 0，实际缺 36 条）；64 S29 "I could only count the 108 rows…The remaining 200 records (page 1) could not be retrieved" 仍提交名单；128 S23 "qty cells…same as order 65"（错误等价：65 的 qty 恰恰是 S21 实读出来的），用小计×单价推断 299 → 答 8（参考 9）。三个任务内部 judge 均自评 SUCCESS。
- **机制**：预算压力（≥75% warning 已注入）+ 无"total records vs 实读行数"对账 → 把读取失败折叠成数据为空。
- **置信度**：高。

### 发现 3：CDP click 对 Magento KO/legacy 按钮系统性无效，JS click 一发即中但未被固化
- **证据**："no visible effect" 清单——186 S1 Filters（S2 同按钮 JS click 立即打开）、186 S6 Apply、64 S19 Export、64 S27 Previous Page、107 S10 Export、116 S11 Next page（S13 `reviewGridJsObject.setPage(2)` 立即生效）、评论网格 Search 6 次（116 S8 evaluate 设值+JS click 生效）。
- **机制**：KO 按钮的 handler 绑定在 viewModel/部件层，CDP 合成事件绕过（或早于部件武装）；`el.click()` 走 DOM 桥接能触发。TreeWalker 的 click 已有 occluded→JS fallback，但这些按钮不遮挡，警告发出后没有自动升级路径，agent 也未把"JS click"固化为策略。
- **置信度**：高（跨 5 任务 15+ 次；2 个正例对照）。

### 发现 4：uiRegistry data-source storage 是网格数据的可靠旁路，但其发现依赖"枚举组件名"这一步
- **证据**：64 S21 枚举 → 命中 `sales_order_grid.sales_order_grid_data_source_storage` → S23 读出 108 行（全场唯一大批量数据产出）；186 S19/S24 两次猜错组件名（`product_listing` / `product_listing.product_listing`）返回 no grid，S26 计划的枚举动作被序列规则跳过（"terminates sequence — skipping 1/2 remaining"），此后再未尝试——**186 的颗粒无收直接系于这一步被吞**。
- **机制**：storage 组件名 = `<namespace>.<namespace>_data_source_storage`，猜不可靠、枚举一步即中；但"枚举"几乎总是作为第二个 evaluate 出现，而 evaluate 终止序列规则会跳过它。
- **置信度**：高。

### 发现 5：evaluate 序列终止规则 + 长代码截断 + 裸 return，三种工具层故障在关键时刻改变任务命运
- **证据**：序列吞动作——186 S26（枚举，致命）、107 S11（计划中的 screenshot 被跳过，页 1 永未解锁）、128 S9/S11；裸 `return` SyntaxError——121 S6、215 S5、64 S5、780 S7、107 S13/S22（每次烧 1 步）；长代码截断——107 S23 / 186 S27 / 128 S8 均 "Unexpected end of input"（"Validated code" 显示代码在字符串中间被切断）；30s 超时——128 S11、186 S20、64 S26、116 S16（scroll/长 await）。
- **机制**：① evaluate 被标记为序列终止者，排在后面的动作（常是另一个只读 evaluate 或 screenshot）静默丢弃；② 代码参数超长被截断后无检测；③ IIFE 约定只在工具描述里，运行时不自愈。
- **置信度**：高。

### 发现 6：跨任务书签/网格状态污染是重跑装置与基线共有的系统性偏置
- **证据**：107 S6 "on page 2 of 2"（落地即第 2 页、200/页，非 Magento 默认 20/页，且 S1-S13 快照恒定证明非 agent 造成）；119 S1 "filter currently 'tank'"（继承 116）；215 S2 "the leftover 'Zing Jump Rope' name filter also applied"（继承 214，白丢一步 0 记录）；782 S1 "active keyword filter was 'Ingrid Running'"（继承 780）；64 S8 的 '300' 落入页码框把第 1 页挤出缓存（该框存在本身即是污染态）。
- **机制**：同一持久浏览器会话顺序跑任务，Magento 把过滤器/分页/每页条数存进服务端 bookmark 与客户端状态。对基线（184 任务连跑）同样成立——**部分基线失败可能是前序任务的幽灵**。
- **置信度**：高（5 任务直接引文）。

### 发现 7：fuzzy_match 的通过/失败分界是"参考原词是否出现在答案里"，语义等价不够
- **证据**：213 数据全、答案明写 "not good for high-impact activities"（参考 "Not suitable for high-impact workouts"）→ 0 分；对照 121（"true to size" 逐字）→ 1 分、215（quality/fit/warmth/color 逐点直贴）→ 1 分、705（判分串恰等于页面规范化输出）→ 1 分；119 覆盖 color（写成 "colorful look"）但 style 一词缺席 → 0 分。
- **机制**：LLM judge 对长答案中的同义改写判罚，对原词短评放行；要点埋在编号列表第 2 条（213）比放在首句更危险。
- **置信度**：高（4 组正反对照；judge 非确定性是残余噪声）。

### 发现 8：报表日期字段的 exact_match 格式陷阱，且判分器自身格式互相矛盾
- **证据**：705 输入 `1/29/2023`（S2 原文 "Typed '1/29/2023'"）→ 终态被页面规范化为 `1/29/23` 恰等于判分串 → 通过；707 输入 `01/01/2022`（URL 漂移 base64 证实 `from=01/01/2022` 原样进提交）→ 判分要 `1/1/2022`，终态又成 `01/01/22` → 0 分；709 输入 `05/01/2021`/`03/31/2022` → 判分要 `5/1/2021`/`3/31/2022` → 0 分。
- **机制**：判分器对 `sales_report_from/to` 的 `.value` 做 exact_match。705 要的是**规范化后的两位年**格式，707/709 要的是**无前导零四位年**（非规范化形态）——同一表单两套期望，正常流程（填→Show Report→页面重渲染规范化）最多满足其一。只有"最后一步用 JS 直接设字段值、不触发重渲染"才能摆出 707/709 要的形态。
- **置信度**：高（705 通过本身即机制证明）；"可救"程度见建议 6（脆弱）。

### 发现 9：四个任务属判分器/参考答案/环境缺陷，agent 侧无解
- **证据**：
  - 782：期望 22.33=29×0.77、21.56=28×0.77（乘 0.77=降 23%）；agent 28×1.23=34.44 按题面正确，四连保存+32 行网格复核零失误（S16 Eval "exactly two $34.44 and two $35.67…all other prices unchanged"）。
  - 780：判分要 64.00（=47+17）；agent 所见原价 84（S2 "found white variants size L (id 1264) and XL (id 1267), both $84.00"，全日志无 47/64 线索），操作保存+回读 101.00 全部成功；基线与重跑两次独立运行所见一致，且 config_files 全库检索无其他任务触碰该产品——漂移来自评测套件之外（容器历史上被手工实验改过价的可能性最大）。
  - 214：参考要点对应的评论实属 Ana Running Short（WSH10）——`evaluate_output` 全量转储 L29/L40 行原文（"It was really hard to find the right siz…"、"Wore these for a year and they started f…"，产品列=WSH10）；Zing Jump Rope 全库 3 条评论全正面，与 Reports 页 avg 93%=(80+100+100)/3 双源互证。agent "无差评"结论事实正确。
  - 679：判分要过滤器条含 "Completed"，Magento 状态 label 为 "Complete"（S2 Eval 引文 "Status select visible at [83566] with 'Complete' option"），"Complete" 不含 "Completed" → 经 Status 过滤路径不可满足。
- **机制**：WebArena 标注错误（214/782）、数据集版本错位或容器污染（780）、措辞不可满足（679）。与 form_interaction 发现 10（546 定义错位）、发现 12（LLM API 连挂）同属"非 agent 责任"桶。
- **置信度**：782/214 高（算术与 DB 实锤）；780 高（双运行一致+无任务触碰）；679 中（芯片文本由 label 推断，无 DOM 直接观测）。

### 发现 10：步数分配——真截断任务 42%-66% 的步数花在无产出的读取尝试上
- **证据**：见第二节表格。107 无产出读取 11 步+无效重复 7 步=18/29；186 无产出 17 步+无效 5 步=22/29（~76% 步数零推进）；对照评论类任务（119/121/213/215 均 ≤11 步）与报表类（6-8 步）效率尚可——**步数黑洞是网格读取专属性质**，不是普遍低效。
- **机制**：链 C 的通道失效 × 无"此路不通"记忆（同一失败通道被反复重试：186 对 mui render 换 8 种姿势打了 9 步）。
- **置信度**：高。

### 发现 11：过滤器优先策略与全量聚合策略的成败分化（同网格同数据）
- **证据**：679 用 Status=Complete 过滤（308→153，单页）7 步完成全量提取；107 同一张订单网格选择"全量翻页+客户端聚合"，29 步只见到 23/67 条 Complete；64 同策略 30 步只得 108/308。62 重跑虽完成但前 10 步同样烧在通道上。
- **机制**：过滤把多页问题压成单页，同时缩小冻结暴露面（153 行一页 vs 308 行两页+翻页后再冻结）；聚合任务里"先过滤再数"是数量级级别的步数节省。
- **置信度**：高（同网格正反对照）。

### 发现 12：scroll 在冻结网格页上 30s 超时，是纯死时间
- **证据**：186 S20 "Action 'scroll' timed out after 30s"；116 S16 同；64 S26（evaluate 内长 await）同。
- **机制**：与既往 scroll 超时记忆一致（大 deltaY/渲染线程被冻死时 scroll 无进展）——在冻结网格上 scroll 既不能解锁也不能读数。
- **置信度**：高（3 任务复现）。

---

## 五、优化建议

> 已落地不复议：`_kick_frozen_data_grid`（session.py:2703，settle 后自动 kick）、type_text 补 change、domain-skills Magento 手册、loader CWD 回退（skills/loader.py:42）——**这四项在 8/20-21 重跑时均未生效**，建议 1 是让它们真正参与下一次评测。

### 建议 1：先验证已落地修复在本类任务上生效，再谈新改动（零新代码）
- **改动层**：评测流程（`run_category.ps1`）。
- **具体改动**：用 `.\run_category.ps1 -Category data_truncated -Force` 重收 17 个任务前，确认三件事：① runner 以 eval workspace 为 CWD 时 domain-skills 仍能命中（loader CWD 回退的日志行 "skill loaded: host=localhost:7780"）；② 网格 kick 日志行 "data-grid render kick" 在订单/产品网格页出现；③ Chrome 前台可见（kick 的成因是窗口不产帧，反节流 flags 不覆盖这一点）。
- **预期影响**：链 A 的 6 个截断任务（62/64/107/128/186/116）里，基线+重跑两次均败的 64/107/128/186 有望直接改善 2-3 个（kick 消灭"行文本空"主因后，剩余失败点是对账与策略，见建议 2/3）。
- **验证方法**：重收后对比 n_steps（62 重跑 kick 后 14 步完成是效率参照）与各任务 grid_kick 日志出现率。
- **风险**：kick 只挂在 `wait_for_page_settle` 后——**网格内 AJAX 翻页不触发 settle，翻页后再冻结（107 页 1 形态）不会被 kick**。需把 kick 钩子扩到 click 动作后（见建议 5 附带项）。

### 建议 2：done 前数据完整性对账（"total records vs 实读行数"）+ 禁止推断替代已验证读取
- **改动层**：prompt 层（`src/tree_walker/prompts/system_prompt.py` 的 Task Completion Rules）+ agent 策略层（`src/tree_walker/agent/step.py` done 前置检查）。
- **具体改动**：在 "Before calling done(success=true)" 清单追加两条（纯 prompt 版）：
  ```
  7. **Data completeness audit** — for count/list tasks on data grids: compare the
     grid's declared total ("N records found", page count) against the rows you
     actually read. If they don't match, you have NOT seen the data — missing
     rows are unknown, NOT zero/empty. Report the gap instead of fabricating
     counts for unseen rows.
  8. **No inference over readable values** — if a value is displayed on the page
     (e.g. qty in a table), read it; never substitute arithmetic inference
     (subtotal ÷ price) when the page shows the field.
  ```
  step.py 侧（可选加固）：`_action_done` 处若 memory 中同时出现 "records"/"total" 与明显更小的实读计数，注入一次 nudge 要求对账（复用 `_append_context_message` 的 context 注入通道，step.py:441）。
- **预期影响**：107（填 0）、64（108/308 仍提交）、128（推断 qty）三个任务的直接死因；kick 生效后这三个任务剩余失败点正是这条——预计合计救回 2-3 个。
- **验证方法**：重收后检查 107 的 final_result 是否不再含 "07:0,08:0…" 型零填充；64 是否在答案中报告缺口而非硬答。
- **风险**：prompt 变长；对非统计类任务无效但无害。107 注意：对账通过的前提是 kick 让页 1 可读，两建议耦合。

### 建议 3：domain-skills 手册补 5 条 Magento 实证通道（零代码，本批轨迹直接产出）
- **改动层**：prompt 层（`domain-skills/localhost_7780/quirks.md` + `_sop.md`）。
- **具体改动**：
  1. quirks.md「列表页」追加：**组件名枚举一步法**（64 S21 实证）——
     ```js
     require(['uiRegistry'], function(r){ r.get(function(c){ return Object.keys(c).join('\n'); }) })
     ```
     找 `*._data_source_storage` 后 `r.get(name, function(ds){ return JSON.stringify(ds.data) })` 直读缓存行（不要猜组件名，186 两次猜错即死）。
  2. 追加：评论网格（legacy）专用——`reviewGridJsObject.setPage(n)` 翻页 + `fetch(location.pathname+'grid?isAjax=true&limit=100&...')` 全量拉取（116 S13/S18 实证）；Search 按钮 CDP 点击无效，用 evaluate 设 `#reviewGrid_filter_name` 的 value 后 JS click。
  3. 追加：**统计/聚合任务先过滤后数**（679 vs 107/64 对照）：Status/日期过滤把 308 行压成单页，一步聚合；不要全量翻页客户端聚合。
  4. 追加：报表页 From/To 用 **M/D/YYYY 无前导零**格式输入；且报表任务的判分常读这两个字段的终值——生成报表后不要再触发会重渲染表单的动作（发现 8）。
  5. 追加：网格页禁用 scroll 促渲染（3 任务 30s 超时实证无效），用截图或 storage 通道。
- **预期影响**：186（通道 1）、64/107（通道 3+1）、116 型（通道 2）、707/709（通道 4，脆弱）。与建议 1/2 叠加后，12 个仍败任务预计合计救回 3-5 个。
- **验证方法**：重收对比 186 是否在 ≤6 步内拿到产品行；107 是否走过滤路线。
- **风险**：benchmark 针对性知识（尤其通道 4 的日期格式）有 overfit 嫌疑——对本项目无碍（P7 目标就是 WebArena），但要在手册里标注来源任务，避免泛化误用。

### 建议 4：click "no visible effect" 自动 JS-click 升级
- **改动层**：工具层（`src/tree_walker/tools/actions.py` 的 `_action_click`，现警告点在 actions.py:775 附近）。
- **具体改动**：检测到 "click succeeded but page unchanged"（现有快照 diff 逻辑已能判定）时，不只发警告——自动对同元素补发一次 `el.click()`（`evaluate` + elements 句柄，`EvaluateParams.elements` 已支持）并把结果标注 "JS-click fallback applied"。KO 按钮上 CDP→JS 一次升级即中（186 S2、116 S8 双正例）。
- **预期影响**：每任务省 2-6 步死点击（186 4 次、64 2 次、107/128 Export、116 Next page）；不是独立救回任务，但把预算还给数据读取（链 C）。
- **验证方法**：重收统计 "no visible effect" 后跟 JS fallback 的命中率与步数分布变化。
- **风险**：JS click 绕过命中测试/防机器人场景的语义差异——按 host 或已知框架（RequireJS/KO 页面）限定启用即可；对通用站点默认关。

### 建议 5：evaluate 序列终止规则放宽为"DOM 未变则继续"
- **改动层**：工具层/agent 策略层（`src/tree_walker/agent/step.py` 多动作执行器 / `tools/registry.py` 的执行循环）。
- **具体改动**：evaluate 执行后做一次轻量 DOM 指纹（现有快照 hash 即可）：**DOM 与执行前一致 → 不终止序列，继续执行剩余动作**；DOM 变了才跳过。screenshot/find_elements 等只读动作同理不再被前置 evaluate 吞掉。附带项（配合建议 1）：把 `_kick_frozen_data_grid` 的检测逻辑同样挂在 click 动作之后（翻页是网格内 AJAX，不进 settle），冻结即 kick。
- **预期影响**：186 S26（枚举被吞=直接死因）、107 S11（screenshot 被吞=页 1 永锁）两处实证的关键动作不再丢失；翻页后冻结（107 页 1 形态）有解。
- **验证方法**：单测覆盖"evaluate 只读→剩余动作应执行"；重收看 186 是否完成枚举。
- **风险**：指纹比较的假阴性（evaluate 改了无关区域）——只读 evaluate 场景本就不该有 DOM 变化，误判面小；实现时保留 30ms 内双采样。

### 建议 6：evaluate 语法自愈 + 截断检测
- **改动层**：工具层（`src/tree_walker/tools/actions.py:2321` `_action_evaluate` 入口）。
- **具体改动**：① 代码以 `return` 开头/裸顶层 return 时自动包 IIFE（本批 5 任务 7 次实证同型错误）；② 编译前做括号/引号配平检查，不平衡时直接返回明确错误 "code appears truncated — resend shorter code"，替代神秘的 "Unexpected end of input"（107 S23/186 S27/128 S8 三次截断均表现为运行时才炸）。
- **预期影响**：每任务省 1-3 步（128 12 步里 3 步是语法错/截断）；不独立救任务，但 107 正是差这几步做的页 1 全量 dump。
- **验证方法**：`tests/` 新增 `_action_evaluate` 参数变换单测（裸 return→IIFE；截断代码→明确报错）。
- **风险**：自动包装改变代码语义的场景（依赖顶层 this/变量提升）——仅在编译失败且代码含顶层 return 时才包装，保守触发。

### 建议 7：fuzzy 判分任务的答案措辞策略
- **改动层**：prompt 层（system_prompt.py Task Completion Rules 或 `_sop.md` 通用节）。
- **具体改动**：追加一条完成规则：
  ```
  9. **Quote, don't paraphrase, for "reasons/aspects/why" questions** — answer
     with the page's own wording first (short verbatim phrases, one per line,
     most important first), then add interpretation below. Avoid burying key
     phrases mid-list or rewording them into synonyms.
  ```
- **预期影响**：119（color/style 缺词）、213（语义全中被判 0）两个直接目标；121/215 重跑已验证"贴词"有效。预计救回 1-2 个（judge 仍有随机性）。
- **验证方法**：重收 119/213/214 的 eval_answer 与参考串的原词重合度。
- **风险**：无功能风险；对 must_include 任务同样有利（eval_answer 精简提取时保留原词）。

### 建议 8：评测装置修复 + 缺陷任务标记（evals workspace 侧）
- **改动层**：评测流程（`run_category.ps1`/`runner.py`）。
- **具体改动**：① 每个任务开始前清 Magento 书签过滤器（导航到网格后先 evaluate 一键 `localStorage` bookmark 清空或点 Clear all——发现 6 的 5 处污染直接消除）；② 在结果 json 中把 214/679/780/782 标记为 `benchmark_defect`（证据链见发现 9），从 SR 分母中单列，避免把 4 个不可救任务持续算作优化欠账；③ 780 类价格任务重跑前用干净容器（docker rm + run 原镜像）。
- **预期影响**：不改 agent 行为；消除 5 个任务的系统性偏置（污染）与 4 个任务的伪欠账。**对报告口径**：SR 52.2% 的真实可优化空间据此校正（88 失败中约 8-12 个属装置/标注缺陷，跨各类别——本类 4/17 的比例若外推，需要一次性全量核查 config_files）。
- **验证方法**：清书签后重收 data_truncated，看开局带过滤器的任务数归零。
- **风险**：清书签动作本身要防误伤（只清 `admin__data-grid` 书签键）；标记缺陷任务需双人复核证据链再定案。

---

## 六、优先级（可救任务数 × 实现成本排序）

| 序 | 建议 | 层 | 成本 | 本类预期可救 | 依据 |
|---|---|---|---|---|---|
| 1 | 建议 1：验证已落地修复（kick/skill/回退）生效并重收 | 流程 | 零 | 2-3（62/107/128/186/64/116 中） | 链 A 是第一主因且修复已在代码里 |
| 2 | 建议 3：skill 手册补 5 条实证通道 | prompt | 零（写 md） | 与 #1 叠加后累计 3-5 | 通道知识直接决定 186/64/107 命运 |
| 3 | 建议 2：done 前数据对账 + 禁推断 | prompt(+策略) | 小 | 2-3（107/64/128 的剩余死因） | 发现 2 是三任务的直接提交错误 |
| 4 | 建议 7：fuzzy 措辞策略 | prompt | 小 | 1-2（119/213） | 发现 7 四组正反对照 |
| 5 | 建议 5：evaluate 序列规则放宽 + click 后 kick 钩子 | 工具/策略 | 中 | 0-1 直接（186），另省 2-6 步/任务 | 发现 4/5 两处"被吞的关键动作" |
| 6 | 建议 4：click 无效果自动 JS 升级 | 工具 | 中 | 间接（省步） | 发现 3 |
| 7 | 建议 6：evaluate IIFE 自愈 + 截断检测 | 工具 | 小-中 | 间接（省 1-3 步/任务） | 发现 5 |
| 8 | 建议 8：装置清污染 + 缺陷任务标记 | 流程 | 小 | 0（口径校正） | 发现 6/9 |

**总体克制估算**：12 个仍失败任务中，prompt/流程层（建议 1+2+3+7）预计救回 **4±2 个**；判分器缺陷 4 个（214/679/780/782）不可救；707/709 仅在"终态字段值摆位"技巧下勉强可救（0-2 个，不建议为其单独投入）。若加上 5 个已翻转任务，本类的天花板约 11-13/17——即 data_truncated 是**所有失败类别中可救密度最高的之一**，且一半杠杆已经落在代码里，只欠一次带修复的重收。

---

## 附：证据文件索引

- 轨迹：`evals\webarena\results\logs\data_truncated\{62,64,107,116,119,121,128,186,213,214,215,679,705,707,709,780,782}.log`
- 判分配置：`evals\webarena\webarena_repo\config_files\{id}.json`（eval.reference_answers / program_html）
- 基线判分：`evals\webarena\results\shopping_admin_antithrottle.json`（tasks[] 内同 id 条目）
- DB 旁证：`evals\webarena\evaluate_output\evaluate_1787312091828.txt`（全量评论转储，214 的 WSH10 证据）；`evaluate_1787267292718.txt`（679 的 153 行提取）
- 判分器实现：`evals\webarena\webarena_repo\evaluation_harness\evaluators.py:244-333`（program_html）；`evals\webarena\cdp_evaluator.py:257-340`（fuzzy→GLM）；`evals\webarena\runner.py:380-386`（eval_answer 提取）
- 代码锚点：`src\tree_walker\browser\session.py:2627-2735`（settle+kick）、`src\tree_walker\tools\actions.py:1094-1175`（input_text 回读）、`src\tree_walker\tools\models.py:395-481`（evaluate 参数）、`src\tree_walker\prompts\system_prompt.py:54-73`（完成规则）、`src\tree_walker\skills\loader.py:42-65`（CWD 回退）、`domain-skills\localhost_7780\{quirks,_sop}.md`
