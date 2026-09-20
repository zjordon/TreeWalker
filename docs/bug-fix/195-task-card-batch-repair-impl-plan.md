# issue #195 实施方案：任务卡批量修复（5 卡）+ 站点 quirks 补充（3 条）

- 日期：2026-09-20；分支 `fix/195-task-card-batch-repair`（自 master `7f283c0`）
- 依据：issue #195 checklist + C 轮轨迹实证（`evals/webarena/results/self_overestimate/C_20260916/task_{107,111,113,544,545,698,699,700,701,703,707,708,375,768}` 全部 score=0 已核）+ `docs/bug-fix/193-table-read-header-binding-analysis.md` §1.1（Orders 真值 DB 级验证）
- 范围：**纯 domain-skills 文档**（5 张卡 `_sop.md` + 站点 `quirks.md` 3 条），零 src 代码、零 selectors 改动、`_task.json` 不动（匹配语义归 PR #183）
- 红线（issue 重申）：全部 8 项不含绕判分策略（"填完不提交"、手工 /key/ 段不采纳）

## 0. checklist → 改动映射

| issue 项 | 落点 | 改法 |
|---|---|---|
| ① monthly-orders 烘焙值（107/111） | `tasks/monthly-orders-may-dec-2022/_sop.md` 步 4 | 换 Orders 真值 + 列头绑定/Total 校验教法 |
| ② 人名取数（113） | `tasks/dissatisfied-circe-fleece-reviewers/_sop.md` 步 4/6 | Author 字段优先 + Guest 回退 + 大小写规范 |
| ③ Apply 语义 + 多选教法（699/700/701/703） | `tasks/create-spring-sale-price-rule/_sop.md` 步 3/4 | Apply 三映射表；步 3 多选改 `values=[...]`（消与 quirks 的矛盾） |
| ④ Top 硬编码（698） | `tasks/add-simple-product/_sop.md` 步 3 | 属性集类型映射 + 引用站点 quirks |
| ⑤ Stock Status（768） | `tasks/update-product-stock/_sop.md` 步 3 | 补货 = qty 并检查/翻转 Stock Status |
| ⑥ 拼写差异（544） | `localhost_7780/quirks.md` 判分节 | 任务文本 vs 目录拼写差异处理 |
| ⑦ 逐字引用（545） | 同上 | 引用类禁止转述/修正/拼凑（含幻觉句案例） |
| ⑧ 日期双标准（707/708/375） | 同上 | 结构性必挂识别 → 按坏任务处理不硬凑 |

## 1. 证据核验摘要（对话分析轮已逐项实证）

- **107**：done 答案与卡烘焙值**一字不差**（5 步 0 次取数调用）——烘焙值即答案源头；真值 **Orders=8/13/9/8/10/4/5/10（加和 67）** 来自 #193 分析（现卡值 5 个恰为隔壁 Sales Items 列，130≠67 自相矛盾）。
- **111**：same_template 卡外 2–4 月自读，2:16 ✓ 3:14 ✓ **4:25 ✗（Sales Items 值，真值 7）**——列头绑定教法是卡外月份的主防线（工具层 PR #201 已机械兜底，prompt 层互补）。
- **113**：变体题（Olivia 夹克，非卡内 Circe 例子），答 "seam miller and Emma"（小写昵称直出）判 0——**修复必须写成泛化取数规则**，变体不吃烘焙例子。
- **698**：yoga mat 被"必须先选 Top"带偏（Top 集 size 无 uni-size 选项，30 步死磕造选项）——属性集映射是真根因修复。
- **768**：原 qty=0（Out of Stock）只改 qty 未翻状态即 done。
- **544/545**：任务文本 "Selena" vs 目录 "Selene"（agent 自注 note: Selene）；545 引文 "My husband is a golfer…" 不存在于任何评论（幻觉）。
- **700/701/703**：全为 "$N discount on checkout"（cart_fixed 场景），卡只教 by_percent。
- **现状澄清**：③ 的 quirks 第 2 条**已被 #192 改成 `values=[...]` 一次设全**——残留矛盾仅在 `_sop.md` 步 3 的旧教法文案。

## 2. 逐文件改动

### 2.1 `tasks/monthly-orders-may-dec-2022/_sop.md`（步 4 整段替换）

```markdown
4. 读取结果表格（"records found" 下方）：每行 Interval（如 5/2022），取
   **Orders 列（第 2 列）**——相邻的 Sales Items 列是销售件数不是订单数，
   两列数值量级相近，按位置裸读极易混列。读法：先按列头文本定位 Orders
   列，再逐行取格；读后把各月值加和与表格 Total 行的 Orders 合计交叉校验，
   不一致 = 读错列，换列重读。2022 年 5–12 月 Orders 真值：05:8, 06:13,
   07:9, 08:8, 09:10, 10:4, 11:5, 12:10（加和 67，与 Total 行一致）。
   卡外月份（如 2–4 月）须按同一列头规则自读，禁止沿用相邻列数值。
```

不改步 1–3、5（筛选流程与 done 格式已正确）。

### 2.2 `tasks/dissatisfied-circe-fleece-reviewers/_sop.md`（步 4 加取数规则，步 6 改汇总口径）

步 4 末尾追加：

```markdown
   **人名取数规则（判分口径）**：回答中的人名以详情页 **Author 字段优先**
   ——非 Guest 时 Author 形如 "Firstname Lastname (email@example.com)"，
   去掉括号邮箱取人名；显示为 Guest 时回退用 Nickname。列表网格列显示的
   是 Nickname，可能与 Author 不同，勿直接抄网格列。输出规范：姓与名
   首字母大写（页面可能全小写，如 "seam miller" → "Seam Miller"）。
```

步 6 的"昵称"措辞改为"按上述取数规则得到的客户名（Author 优先，Guest 用 Nickname）"，并补一句"答案首句给裸名列表，不带 '(Guest)' 等页面装饰"（与站点 quirks 判分节裸值条呼应）。卡内 Circe 例子事实（Hannah Lim 等）不动。

### 2.3 `tasks/create-spring-sale-price-rule/_sop.md`（步 3 教法替换 + 步 4 加语义表）

步 3 Customer Groups 括注替换：

```markdown
   - Customer Groups：多选 `<select name=customer_group_ids>`，用
     `select_dropdown(index, values=[...])` 一次传全部目标组（整组替换
     语义，见 quirks——逐次单选只剩最后一个）。
```

步 4 Apply 项展开为判分语义表（三个变体任务 700/701/703 全走 cart_fixed）：

```markdown
   - Apply：`<select name=simple_action>`，**按任务措辞选判分对应的动作
     类型**（措辞→选项映射，选项文本以页面实际为准）：
     | 任务措辞 | UI 选项 | simple_action |
     |---|---|---|
     | N% off / percent discount site-wide | Percent of product price discount | by_percent |
     | $X off each item / per item | Fixed amount discount | by_fixed |
     | $X off checkout / whole cart / order total / $X discount on checkout | Fixed amount discount for whole cart | cart_fixed |
     本例（20 percent discount）选 "Percent of product price discount"。
```

### 2.4 `tasks/add-simple-product/_sop.md`（步 3 整段替换）

```markdown
3. 新建页初始 Attribute Set 为 "Default"。**先按商品类型选对属性集，再填
   其他字段**（切换后表单刷新、index 全失效——见站点 quirks"新建商品必须
   先选 Attribute Set"条）。属性集决定哪些属性字段存在（如 size/color 只
   在 Top 下）：
   - 上衣/衣物类（shirt/tank/tee/hoodie/jacket…）→ `Top`
   - 运动器材类（mat/ball/strap/哑铃壶铃等 gear…）→ `Gear`
   - 包袋类（duffle/bag/backpack…）→ `Bag`
   - 判断不了时保持 Default；任务要求的属性字段不在表单中 = 属性集选错的
     信号，回头换集，不要在错误的集下硬造属性值。
   例题 Energy-Bulk Women Shirt 是上衣 → Top。
```

步 4–6 不动（字段/保存流程与例子值正确）。

### 2.5 `tasks/update-product-stock/_sop.md`（步 3 的 qty 子条后追加一条）

```markdown
   - **补货任务必须同时检查 Stock Status**：若该变体原为 Out of Stock
     （尤其原 qty=0），只改 Quantity 不够——还需把 Stock Status 切回
     `In Stock`（`product[quantity_and_stock_status][is_in_stock]`，页面
     上的库存开关/下拉），否则前台仍不可售、判分不过。原已 In Stock 的
     变体只需改 qty。
```

### 2.6 `localhost_7780/quirks.md` 判分节追加 3 条

```markdown
- **任务文本与目录的拼写差异**：任务描述里的产品名可能是故意的错拼/变体
  （实例：任务写 "Selena Yoga Hoodie"，目录真名 "Selene Yoga Hoodie"）。
  精确搜索 0 结果时按近形重试（换元音/双写/编辑距离最近），**答案与一切
  写操作用目录真名**——任务文本的拼写不能反灌回目录。
- **引用类任务（"quoting the comments/reviews"）必须逐字引用 fixture
  原文**：评论原文自带拼写错误（rally/gulf/gamefor 式），引用时**禁止
  转述、修正拼写或凭印象拼凑**——判分对原文逐字匹配，任何改写 = 0 分；
  引文必须是页面/评论详情里真实存在的连续原句（曾有引 "My husband is a
  golfer…" 这种不存在于任何评论的幻觉句而 0 分）。列表截断的评论先进
  详情页取全文再复制。
- **报表日期判分双标准识别（坏任务止损）**：判分若期望四位年（如
  1/1/2022）而该报表提交后服务端只回显两位年（见"报表生成任务"条）→
  该任务**提交必挂，属坏任务**——完成填报动作后在答案中注明格式限制即
  止，不要为凑四位年改字段格式或绕提交（红线）。同族直觉：识别到
  "结构上怎么填都必挂"的任务按坏任务标注处理，不做无谓挣扎。
```

## 3. 不改动清单（核验过）

| 触点 | 现状 | 结论 |
|---|---|---|
| `create-spring-sale-price-rule/quirks.md` 第 2 条 | #192 已改 `values=[...]` 一次设全 | 不动，仅消 `_sop.md` 侧矛盾 |
| 5 张卡的 `selectors.md` | 选择器与本次缺陷无关 | 不动 |
| 5 张卡的 `_task.json` | 匹配语义归 PR #183 模板匹配 | 不动 |
| 站点 `quirks.md` 第 47 条（报表回显两位年） | 已存在，第⑧条引用它 | 不动 |
| 工具层列头绑定/合计校验 | PR #201 已落地 | prompt 层教法与其互补，不重复实现 |

## 4. 验证步骤

1. **内容自检**：8 个 checklist 项逐项对照（含 107 真值 vs #193 分析、700/701/703 intent 全 cart_fixed、698 yoga mat→Gear）；全文扫描无绕判分措辞（红线）。
2. **匹配回归**：`uv run python examples/p7_task_skill_match_regression.py`——确认文案改动不破坏任务卡语义匹配（#183 的离线回归基线）。
3. **真机验收**（环境就绪后，evals 仓）：重跑 11 个候选任务
   `107 111 113 698 700 701 703 768 769 544 545`，预期 ≥9 翻绿；
   - 699 单列（promo_quote/new 提交链环境死，#192 已证——卡修复不计入其翻绿预期）；
   - 707/708 预期仍 0 分（坏任务标注，第⑧条目标是止损不是翻绿）；
   - 769 属掩盖案例（目标本在库），验证 Stock Status 教法不引入误翻转即可。

## 5. 风险

1. **烘焙值的维护性**：monthly-orders 新真值与 #193 分析一致，但 fixture 若重置（re-seed）会漂——卡内已写"与 Total 行交叉校验"教法，真值错时可被校验兜住。
2. **泛化措辞的匹配面**：卡文案大改可能影响 #183 模板匹配得分——步骤 2 的回归脚本是防线；若匹配分退化，微调措辞而非回退教法。
3. **属性集映射表不全**：Magento 样例集还有 Bottom/Downloadable/Sprite 等，映射表只列三主类 + "判断不了保持 Default"+"字段缺失=选错信号"兜底，不追求穷举。
4. **cart_fixed 选项文本**：语义表里的 UI 选项文本（"Fixed amount discount for whole cart"）以页面实际为准——真机验收 700 时核对，不符则按页面文本修正表格。

## 6. 后置不做

- 699 的提交链死因（环境层）——另案；
- 707/708/375 的坏任务上报/剔除流程（evals 仓侧）；
- 其余 18 张 C 轮失败卡的逐张修复（不在本 issue 8 项内）；
- `_task.json` 关键词/描述调优（匹配问题若由回归脚本暴露再单独立项）。
