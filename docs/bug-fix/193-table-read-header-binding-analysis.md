# issue #193 分析：表格数值读取缺列头绑定与合计校验——读错列不自知、校验是假的

- 日期：2026-09-19；issue：#193（OPEN，agent 类）
- 证据：evals 仓 C 轮（2026-09-16 全量，代码 @ `9ffe21c`，已含 #184/#185/#186 各修复）轨迹
  `results/self_overestimate/C_20260916/task_{107,111,204,6}.log`＋V 轮
  `V_20260918/task_6.log`＋B 轮 `B_20260915/task_111.log`＋分析文档
  `evals/webarena/docs/evaluation/c-hit-fail-27-analysis-2026-09-17.md`（issue 的证据源）；
  本仓侧核验 `tools/actions.py`（read_grid）、`prompts/system_prompt.py`、
  `skills/task_matcher.py`、dom-snapshot serializer
- 关联：#184（单字符单元格快照修复——修复后仍混列，证明不是可见性问题）、
  #185（evaluate 传输截断——V 轮 task 6 侧证）、#186（done 完整性门禁——只扫
  不确定标记，自信的错误值不在其射程，by design）、#182（任务卡语义匹配与读型加严段）

## 0. 结论速览

issue 的两个形态在 C 轮一手轨迹里**一个成立、一个半成立**，且成立的那半机制比
issue 叙述的更具体：

| 形态 | issue 叙述 | 一手轨迹核验 | 判定 |
|---|---|---|---|
| 107/111 混列 + 假合计校验 | 读值混列；声称 total 67 而实加 130≠67，"合计校验是假的" | 107：答案与卡烘焙值一字不差（含自相矛盾的 67），全程**零取数工具调用**；111：卡外月份自读，2/3 月对、4 月取了 Sales Items 值 | **成立**，机制见 §2 |
| 204/6 "列全"自觉缺失 | 204 C 轮只报 2 件中的 1 件 | 204 C 轮最终文本**两件都报了**（`1) Ida…$38.40; 2) Proteus…$45.00`），判分产物同；败因是坏任务（参考答案来自 processing 订单 #125） | **不成立（证据勘误）**，见 §1.2 |

三层根因（均在 TreeWalker 侧，卡缺陷/坏任务属 evals 仓另案）：

1. **快照无表格结构语义**：dom-snapshot 把表格序列化为扁平缩进文本流——
   `tr`/`td` 非交互不渲染标签（`serializer.py:952-1058`），单元格文本成裸行，
   **行界与列界都不可见**。列对齐只能靠模型数格子推断，相邻数值列
   （Orders 与 Sales Items）错位一格即混列。
2. **结构化取数通道存在但不可见**：`read_grid` 的 DOM 表格通道
   （`actions.py:531` `_DOM_TABLE_READ_JS`）**本来就做表头绑定取格**
   （`row[表头文本]=单元格值`），且 #189 已加固。但 C 轮 4 任务 0 调用、
   V 轮 task 6 宁可手写 evaluate（还撞上 #185 传输截断）——系统提示词没有
   任何"表格取值走 read_grid"的路由规则，动作描述用的"data grid"词汇也不
   cue 报表场景。
3. **验证纪律有文无武**：读型加严段（`task_matcher.py:134`）已注入且明说
   "不许报卡内值"，107 仍照抄——因为卡里那串混列值**每一个都能在活表格里
   找到**（分布在两列中），"位置检查式"的假验证防不住；而合计校验靠 LLM
   心算，107 直接断言"total 67 matching"没做算术，111 真算了 sum=175 却没
   和 Total 行比对（真值 94）。**LLM 侧的算术与"声称已验证"都不可信，
   必须由代码给出可读的两个数**。

修复映射（详见 §5）：issue 三方向全部落 TreeWalker 侧可做——
方向 1（列头绑定）= 提示词路由规则 + read_grid 教法修订；
方向 2（Total 校验真算术）= read_grid DOM 通道补 tfoot/合计行捕获 +
Python 侧数值列加和 + 一致性自动标注（把"校验"从心算变成读代码算好的两个数）；
方向 3（列全自查）= 提示词一行 + 已有的 rows/total 元信息（证据薄，按低成本
防复发件处理）。

## 1. 证据核验与勘误

### 1.1 成立的部分（107/111）

**C 轮 107**（`C_20260916/task_107.log`，5 步 73.9s，score 0）：

- 任务卡命中 `monthly-orders-may-dec-2022`（same_task / high / **task_kind=read**，
  读型加严段已注入），SOP 第 4 步烘焙值为
  `05:25, 06:13, 07:28, 08:18, 09:10, 10:11, 11:15, 12:10（总计 67）`。
- Step 0–3：导航 + 填筛选 + 提交 Show Report（整页导航，quirks 已知）。
- Step 4：**直接 done**，全程没有 read_grid / evaluate / extract 任何取数调用。
  Memory："Orders Report results: 05:25, …, total 67. **All verified from live
  table**"——答案与卡烘焙值一字不差，含那自相矛盾的总计 67
  （25+13+28+18+10+11+15+10=130）。
- 假验证的机制：出厂真值两列并排为 Orders = 8/13/9/8/10/4/5/10（5–12 月，
  加和 67）、Sales Items 含 25(5月)/28(7月)/18(8月)/11(10月)/15(11月)——
  **卡里每个错值都能在活表格中"找到"**（只是多数落在隔壁列）。模型做了
  存在性比对就宣称 verified；只有列头绑定或加和-vs-Total 能戳穿。

**C 轮 111**（`task_111.log`，4 步 73.8s，score 0）：

- 命中同一张卡但 same_template（卡只覆盖 5–12 月），2–4 月须自读。
- Step 3 直接 done：02:16 ✓、03:14 ✓（读对了 Orders 列）、**04:25 ✗（4 月
  Sales Items 值，真值 7）**；5–11 月与卡值一致（照抄）。真值 Orders 列
  2–11 月加和 = 94，agent 心算 175 且未与 Total 行比对。
- 结论：**自读部分混列**——同一遍目测里对了两月错一月，与"扁平文本流数
  格子错位"的机制吻合（不是整列错位）。

**对照：B 轮 111 通过，且路径完全不同**（`B_20260915/task_111.log`）：
根本不进报表页——`read_grid namespace=sales_order_grid filters={status:
complete, created_at: 2022-02-01..2022-11-30}` 读原始订单行（153 行落盘后
read_file 分页 tally），逐月全对。**结构化数据通道存在且够用，缺的是把
agent 引到这条路上的规则**。

### 1.2 勘误：204 的"只报 1 件"不成立

issue（及其证据源分析文档 §204"次要观察"）称 C 轮 204 只报了 #230 两件中的
一件。一手证据相反：

- `C_20260916/task_204.log` Step 4 最终文本：`1. Ida Workout Parachute Pant —
  $38.40 … 2. Proteus Fitness Jackshirt — $45.00`——**两件都在**；
- 判分产物 `shopping_admin_antithrottle_skillC.corrected.json` 的
  `eval_answer` 同样两件齐全。
- 推测误读来源：日志里 done 文本预览截断为 `1. Ida Workou…`（"- 344 more
  characters"），分析文档作者按预览行了结论。204 判 0 的真因是**坏任务**
  （参考答案三件商品全部来自 processing 订单 #125，意图要求 completed）——
  按 204 自身，agent 挑单、读件、排序全对。

**影响**：issue 方向 3（列全自查）的 C 轮证据只剩 task 6（坏任务：参考答案
来自年度聚合表，agent 读的 Bestsellers UI 视图连候选集都不一致；B 轮把并列
池 12+ 项全列了，败在字符串形态）。方向 3 降级为低成本防复发件，不再作为
主修复项。

（此勘误建议回帖 evals 仓分析文档；204 无需任何 agent 侧动作。）

## 2. 机制链：为什么错得如此安静

三层叠加，每层单看都"情有可原"，合起来就是"自信地错"：

```
报表页（相邻数值列 Orders | Sales Items）
   │
   ├─ 快照层：tr/td 不渲染 → 单元格文本成裸缩进行（行界/列界不可见）
   │    └─ 模型按"月名后第 N 个数"数格子取值 → 错位一格 = 混列（111 的 4 月）
   │       （#184 修复后数值都在场——混列发生在"看得见"的前提下）
   │
   ├─ 卡层：same_task 全注入 + SOP 烘焙值本身混列（加和 130 却写总计 67）
   │    └─ 模型照抄卡值，用"每个值都在表格里"的存在性比对宣称 verified（107）
   │       （读型加严段"不许报卡内值"已注入——纪律有文，无执法）
   │
   └─ 验证层：合计校验靠心算
        ├─ 107：根本没算，断言"total 67 matching sum of counts"
        └─ 111：算了 175，但没和页面 Total 行（94）比对
   ↓
 done(success=true) —— #186 门禁只扫不确定标记，自信的错误一路绿灯
```

旁证（V 轮 task 6，2026-09-17）：agent 想读表格数值时选择**手写 evaluate**
（`querySelectorAll('table tbody tr')` 逐格取），两次连续 SyntaxError
（#185 传输截断家族），止损 nudge 触发后降级截图目测。全程没有想到
read_grid——尽管 DOM 通道就是为此写的。**工具的"可发现性缺口"是横跨
C/V 两轮的稳定形态**。

## 3. TreeWalker 侧资产盘点

| 资产 | 现状 | 缺口 |
|---|---|---|
| `read_grid` DOM 表格通道（`actions.py:531`） | **表头绑定取格**：`row[thead th 文本]=td innerText`；选 `tbody tr` 最多的表；headers 一并返回 | ① 只读 `tbody tr`——Total 行若在 `tfoot` 则丢失；② 无数值列加和——校验仍靠 LLM 心算；③ 多表页按最大表选取，无显式指定 |
| `read_grid` 其他 | uiRegistry/legacy 通道、group_count 确定性聚合（#189）、大结果落盘、参数守卫 | 描述用"data grid"词汇，报表场景无 cue |
| 系统提示词 | 规则 7 只覆盖"大列表页"→evaluate 聚合；done 规则 2/7 泛化要求"counts correct / data complete" | 无"表格取值走结构化工具、绑列头"的路由规则；无"Total 行交叉校验、和必须来自工具输出"的验证规则 |
| 读型加严段（`task_matcher.py:134`） | 已注入且被 107 违反 | 无执法手段（合理——值级执法需页面访问，见 §5.4 不做项） |
| done 门禁（#186） | 只扫不确定标记 | 自信错误值不在射程（by design，不扩） |
| dom-snapshot | v0.1.1 单字符修复后数值可见（#184 真机复验） | 表格无结构语义——**不动**（改快照格式影响面全站，且 read_grid 通道已覆盖需求） |

## 4. 根因归纳（一句话版）

**表格数值的"读"与"验"都留给了 LLM 目测与心算：快照层没有结构、提示词层
没有路由、工具层有结构化通道却不为人知、验证层没有代码化的算术。**

## 5. 修复方向对比（映射 issue 三方向）

### 方向 1（主体）：表格取值路由到 read_grid——列头绑定由代码做

- **1a 提示词路由规则**（系统提示词 Rules 追加一条，与规则 7 并列）：
  "从页面表格取**数值/名称**作答时，用 `read_grid`（返回按表头绑定的行），
  不要在 DOM 快照里按单元格位置目测取值；相邻数值列在扁平快照里无法区分。"
- **1b read_grid 动作描述修订**：点明覆盖"报表结果表"（reports）场景，
  并把"表头绑定取格，天然防相邻列混淆"写进卖点。
- 取舍：不改 dom-snapshot 加表格结构（全站影响面大，read_grid 已够）；
  不为表格新开动作（复用 read_grid，#189 的守卫/落盘/分页白得）。

### 方向 2（主体）：合计校验由代码真做算术

- **2a read_grid DOM 通道增强**（`_DOM_TABLE_READ_JS` + Python 侧）：
  - 捕获合计行：`tfoot tr` **及** tbody 内首格匹配 `total|合计` 的行，作为
    `footer` 字段返回（两种 DOM 形态都接住；Magento 报表合计行具体落点
    真机确认）；
  - Python 侧对可解析为数值的列计算 `column_sums`（镜像 group_count 的
    确定性聚合哲学：计数不交给上下文 tally，加和同理不交给心算）；
  - 自动标注：某列加和 == footer 对应格 → 附 note "column X sums to N
    (matches Total row)"；不等 → "column X sums to N ≠ footer Total M —
    检查是否读错列/漏行"。
- **2b 提示词验证规则**（done 规则补一条）："页面有 Total/合计行时，逐行
  值必须与其加和一致，加和用 read_grid 返回的 column_sums——**禁止断言
  未由工具输出支撑的算术或验证**（'verified' 必须能指到本次会话的工具
  输出，不允许凭目测宣称）。"
- 效果推演（107 场景，即使卡仍带错值）：agent 走 read_grid → 列头绑定直接
  取对 Orders 列；即便先取错列，Sales Items 加和 130 ≠ footer 67 → note
  点破 → 换列重读。"错列时自动重读"的验收由 2a 的 note + 1a 的路由共同
  支撑。

### 方向 3（防复发件，低成本）：列举型答案列全自查

- 提示词一行：答案为表格行枚举（清单/top-N）时，核对已列项数与工具返回的
  `rows_returned`/`total_records`，并列（tie）边界要说明。
- 证据已勘误为薄（§1.2），不单独成 PR 粒度，并入 1a/2b 的提示词改动。

### 5.4 明确不做

- **done 门禁扩值级校验**：门禁无页面访问，重读表格做值比对等于在门禁里
  跑半个 agent（成本/复杂度失控）；#186 门禁的射界就是"不确定标记"，
  自信错误值交给方向 1/2 的工具化路径消解。
- **快照表格结构化**：影响全站 token 形态与既有 quirks；read_grid 通道
  是更窄的手术。
- **卡侧修复**（烘焙值、蒸馏自洽校验）：evals 仓 B4 清单已列，跨仓依赖，
  本仓不等它——方向 1+2 落地后，坏卡值会被 sum≠Total 当场暴露。

## 6. 实施清单（建议 PR 粒度）

1. **PR-1 工具层**（方向 2a）：
   - `_DOM_TABLE_READ_JS`：tfoot/合计行捕获（JS 侧）；
   - `_action_read_grid`：`column_sums` 计算 + footer 一致性 note（Python 侧，
     通道无关——uiRegistry/legacy 通道有数值列时同样受益）；
   - 单测：相邻数值列 fixture（Orders|Sales Items+Total 行）——正确列
     sum==footer、错列 sum≠footer 的 note 都要测；colspan/非数值列的边界；
     合计行在 tfoot 与在 tbody 两种形态。
2. **PR-2 提示词层**（方向 1a+1b+2b+3）：
   - 系统提示词路由规则 + done 验证规则 + read_grid 描述修订；
   - 既有 prompt 快照测试同步。
3. **真机验收**（Chrome 9223，eval 环境）：
   - 107 场景回放：逐月值全对且加和 == Total 行；注入"取 Sales Items 列"
     的错列路径 → note 触发 → 自动换列重读；
   - B 轮 111 的 read_grid 原始订单路径回归不受影响。

## 7. 风险与边界

- **表头绑定仍是位置配对**（`thead th[i]` ↔ `td[i]`），colspan/不规则表会
  错位——DOM 通道按现状已如此，本修复不恶化；note 的 sum≠Total 反而是
  这类错位的信号。
- 多表页最大表选取可能错靶（B 轮 111 step0 就踩过 dashboard 部件网格）——
  agent 有自纠先例（下一步换 namespace）；必要时后续补 table 指定参数，
  首期不做。
- Magento 报表页非 uiRegistry 网格，走 DOM 通道——`tfoot` vs tbody 合计行
  落点需真机确认（实现按两者都接住写，风险已对冲）。
- 提示词新增 2 条规则（1 路由 + 1 验证）+ 描述修订，长度可控；规则语义与
  规则 7、done 规则 2/7 无冲突（一个管"怎么读"，一个管"怎么验"）。

## 8. 验收（对 issue 原验收的具体化)

- 107 场景回放：逐月值与 Total 行加和一致 ✓（由列头绑定 + column_sums
  保障）；
- 错列时自动重读 ✓（由 sum≠footer note + 路由规则保障）；
- 单测全绿 + 覆盖率 ≥85% + #189 既有 read_grid 测试零回归。
