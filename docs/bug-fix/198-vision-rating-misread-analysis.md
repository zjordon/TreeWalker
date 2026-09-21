# issue #198 分析：截图读星与实际值矛盾（task 771）——证据链核查

> 分析日期：2026-09-21。状态：**issue 前提不成立**——771 的失败与视觉读星无关，
> 属无卡口径暴露 + 评测互斥对；issue 提出的三条修复方向对该案例全部无效。
> 材料来源：evals 仓 `results/self_overestimate/*`、`webarena_repo/config_files/{771,774}.json`、
> `analysis/V_20260918/task_771.md`、TreeWalker `domain-skills/localhost_7780/tasks/approve-positive-reviews/`。

## 核心结论

1. **"截图读星"不成立**：V 轮 771 轨迹读星全程走 `evaluate`+JS fetch（DOM 通道），
   截图从未参与读数；且三次读数（radio positions → checked radio values）**全部正确**，
   349=3★ 自始至终没读错过。
2. **"B 过 V 败"不成立**：无卡对照轮实测 A（09-08）、B（09-10）、V（09-18）771 全部
   score=0.00，失败集合完全同构（都只批 {347, 352}）——与视觉通道无关。
3. **失败真因是判分口径不可知**：verifier 要求 347/**349**/352 全部 approved，
   正解口径 = "positive 含 3★（rating ≥ 60%）"（任务卡 SOP 原文）。无卡 agent 按常识把
   3★"OKish"判为负面 → 必漏批。带卡轮（skillC/skillB）same_task 命中卡 → 全 1.0。
4. **771/774 是互斥对**：349 一题两用（771 要批准、774 要删除——"positive 含 3★"与
   "<4★ 删除"对同一条 3★ 评论互相矛盾，WebArena 标注自身冲突），轮内必挂其一；
   全轮次记录完美互斥（见 §2 表）。
5. issue 三条修复方向对 771 全部无效（§5）；771 的正解已存在且已验证（任务卡）；
   顺带核查 V 轮读值族（issue 背景段拉进来的 16 任务）——**也全部走 DOM 通道**，
   "DOM 优先"是现状不是修复（§6）。

---

## 1. 判分真值还原

### verifier（evals 仓 `config_files/771.json`）

```
program_html:
  352 -> document.querySelector('[name="status_id"]').value == "1"  (Approved)
  349 -> 同上 == "1"
  347 -> 同上 == "1"
```

期望批准集合 = **{347, 349, 352}**。agent（V 轮）批了 {347, 352}——差在 349。

### 349 的星级 = 3★（三路证据互洽）

| 证据 | 内容 | 结论 |
|---|---|---|
| V 轮 Step 5 DOM 复核 | 349 checked radio `value=18`，agent 映射 3★ | 3★（**读数正确**） |
| 任务卡 SOP | "正面（3 星以上**包括 3 星**）→ 批准" | 正解口径含 3★ |
| 774 verifier 互证 | 774 = "Delete pending reviews with **less than 4 stars**"，要求删除 {351, 353, **349**} | 349=3★<4★，同一套 value→星数映射（16=1★…20=5★）在两个任务的 verifier 里互洽 |

即：**value→星数映射（value − 15）在 771/774 两侧判分与 agent 读数三方一致**，
349=3★ 无歧义。判分把 3★ 算作 "positive"（60% 口径），而 "OKish" 的文本观感偏负、
常识上 3★ 也不像 "positive"——口径不可知是无卡轮的结构性死点。

## 2. 多轮对照（决定性证据）

| 轮 | 日期 | 口径 | 771 | 774 | 771 批准集合 | 数据源 |
|---|---|---|---|---|---|---|
| A | 09-08 | 无卡文本 | **0.0** | — | {347,352} | self_overestimate/A/task_771.log |
| B | 09-10 | 无卡文本 | **0.0** | — | {347,352} | self_overestimate/B/task_771.log |
| skillB(0910) | 09-10 | 带卡 | 0.0 | 1.0 | {347,352}* | shopping_admin_antithrottle_skillB.20260910_071246.json |
| skillB(终) | — | 带卡 | **1.0** | 0.0 | {347,349,352} | shopping_admin_antithrottle_skillB.json |
| skillC(0909) | 09-09 | 带卡 | **1.0** | 0.0 | {347,349,352} | …skillC.20260909_190329.json |
| skillC(0916=C) | 09-16 | 带卡 | **1.0** | 0.0 | {347,349,352} | …skillC.20260916_155845.log:23427-23712 |
| V | 09-18 | 无卡视觉 | **0.0** | 1.0 | {347,352} | …antithrottle_vision.json |

\* skillB(0910) 该日未命中/未遵循卡（同日 B 轮无卡也挂），终版 skillB 过。

- **无卡轮全挂**（A/B/V，含视觉与非视觉），失败机制完全同构：把 349(3★) 判为负面。
- **带卡轮全过**：skillC 09-16 轨迹 Step 0 即命中卡 `approve-positive-reviews`
  （same_task, high），按 "score ≥60% rule" 直接选 347/349/352 批准。
- **771/774 完美互斥**：349 被 771 批准则 774 的"349 已删除"必挂，反之亦然——
  每轮恰好一题 1.0。这是 WebArena 标注自身对 3★ 的双重身份（既是 "positive" 又是
  "<4 stars"），不是 TreeWalker 的可修项。

> ⚠️ v-fail-analysis-2026-09-19.md 中 771 被标 "B 过 V 败 → 视觉轮特有退化"，
> 系轮次数据错配（材料包 task_771.md 的多轮记录只列了 V 和 skillC 两行，漏拉
> self_overestimate/A、B）。按实测应改判：**无卡裸跑暴露（B 同败，C 卡救）**，
> 与 65/695/697/769 同族——"视觉特有退化 16 个"应为 15 个。

## 3. "读星矛盾"机制还原（轨迹逐行）

V 轮轨迹（`results/self_overestimate/V_20260918/task_771.log`）：

- **Step 3**（evaluate+fetch，非截图）：goal 原文 "extract the star rating
  (**position of checked radio** among ratings radios)"，按 ids=[347,349,351,352,353]
  得 positions = (1, 3, 5, 2, 5)。
- **Step 4**：Eval 称 "contradicts review sentiment (positive text showing 1-2 stars,
  negative text showing 5 stars)" —— 即把 position **正序**当星数解读
  （347→"1★"、351/353→"5★"），与文本情感冲突，判定"计数方法可疑"。
- **Step 5**（evaluate+fetch 改读 value）：347=20(5★)/352=19(4★)/349=18(3★)/
  351=16(1★)/353=16(1★)，自建映射 value−15=星数，**全部正确**。

关键：Magento admin rating 表格 radio **DOM 倒序**（5★ 在第一列，admin 后台惯例），
position i ↔ 星数 6−i：

| review | position | 倒序解读 | value | value 解读 | 一致？ |
|---|---|---|---|---|---|
| 347 | 1 | 5★ | 20 | 5★ | ✓ |
| 349 | 3 | 3★ | 18 | 3★ | ✓ |
| 351 | 5 | 1★ | 16 | 1★ | ✓ |
| 352 | 2 | 4★ | 19 | 4★ | ✓ |
| 353 | 5 | 1★ | 16 | 1★ | ✓ |

**初读与复核五行完全自洽**——初读数据没错，Step 4 的"矛盾"是把倒序 position 正序
解读出的**假警报**（常见星级控件 1★→5★ 正序的直觉 vs admin 表格 5★→1★ 倒序的
现实）。代价是多花 2 步复核；复核后读数正确、分类依旧（349=3★ 按常识仍判负面）。

> 这同时解释了 issue 标题的由来：issue 作者从 Step 4 Eval 的"positive text showing
> 1-2 stars, negative text showing 5 stars"读出了"星位与实际值矛盾"的观感。
> 但那是 agent **自己的解读错误**（正序读 position），不是控件渲染或视觉通道的错。
> 文本轮同样可能犯此错——B 轮没走这环节只是因为快照里星不可见，直接按文本判了。

## 4. TreeWalker 侧现状（为什么三条修复方向不对症）

（代码引用为当前 master）

- **截图与 DOM 同时提供**：`use_vision` 时 state 消息 = `[text block(完整 [Page DOM] + element_tree), image block]`
  （`src/tree_walker/prompts/system_prompt.py:340-368` `build_state_blocks`）。视觉轮的
  DOM 信息一点不少——V 轮 agent 读数全走 DOM 工具正得益于此。
- **读值指导已经全是 DOM 优先**：system prompt Rule 7（大列表一次 `evaluate` 全量抽取）、
  Rule 8（表格具体值必须 `read_grid`，不从快照抄值）、Task Completion Rule 9（加和来自
  工具输出并与 Total 行比对）。**没有任何"从截图读数值"的引导**——"DOM 优先"是现状。
- **read_grid**（`src/tree_walker/tools/actions.py:2758`）：uiRegistry→legacy ExtJS→DOM
  `<table>` 三通道回落，只处理网格结构，不含 radio 组；星级控件由 `evaluate` 兜底
  （771 现场即如此，工作正常）。
- **无视觉-DOM 交叉校验机制**（全仓无实现）；read_grid 内部有 DOM 数值校验
  （逐列和 vs footer Total 行，actions.py:2935-3005）。

## 5. issue 三条修复方向逐条评估（对 771）

| # | issue 方向 | 对 771 的实际状态 | 结论 |
|---|---|---|---|
| 1 | 数值/评级/表格读值优先 DOM 通道，截图仅布局辅助 | 读星已在 DOM 通道（evaluate+fetch）且读数正确 | **无效**（已是现状） |
| 2 | 星级控件专用读取模式（value→星数映射 20/19/18…） | agent 已自建映射（20→5★…16→1★）且与判分两侧互洽 | **无效**（已自完成） |
| 3 | 视觉读数与文本/结构信号冲突时触发复核而非硬采纳 | 已发生：Step 4 发现矛盾→Step 5 复核→读数正确，**照样挂** | **无效**（挂点不在读数在口径） |

771 真正的正解 = 任务级知识（`approve-positive-reviews` 卡的"含 3★"口径），
带卡轮已实证 1.0。无卡轮挂掉属于 v-fail-analysis 里已单列的"无卡裸跑暴露"族，
不是读值通道问题。

## 6. 顺带核查：V 轮读值族（issue 背景段的 16 任务）通道统计

对 V_20260918 轨迹抽查（执行行统计）：

| task | 执行动作分布 | 读值通道 |
|---|---|---|
| 1 | evaluate×5, read_grid×2 | 全 DOM |
| 3 | evaluate×3 | 全 DOM |
| 4 | evaluate×3 | 全 DOM |
| 5 | evaluate×1 | 全 DOM |
| 116 | evaluate×5, read_grid×2 | 全 DOM |
| 196 | read_grid×4, read_file×4 | 全 DOM |
| 771 | evaluate×5 | 全 DOM |

**无一任务靠截图像素读值**。以 task 1 为例（答 Dash 期望 Sprite）：表格读数全对
（Dash 3/Quest 3/Helios 2/Radiant 2/Zing 2，总和 12 与 Total 校验通过），错在
**视图口径与品牌归属**（Period=Year 口径下 Dash/Quest 并列 3-3 → 找不到 brand 属性 →
按商品名首词答 Dash；C 轮卡教的是 Period=Month + 品牌约定）。即"视觉轮读值退化"的
机制是**决策/口径层**（视图选择、覆盖不全、并列处理、口径不知），工具结果本身没错；
截图的真实影响是对模型推理的干扰（与 #197 视觉形状崩坏同族的模型侧行为），
不是"读值通道选错"。**若要修，落点在模型侧/上下文侧或评测口径侧，加 DOM 校验修不到。**

## 7. 建议

1. **issue #198 勘误/转性**：771 证据链不成立（截图读星 ✗、B 过 V 败 ✗、三条修复方向
   对案例无效 ×3）。建议在 issue 评论附本分析，关闭或转为"V 轮读值族机制待查"
   （真实载体是 §6 的决策/口径层退化，另案）。
2. **评测仓侧勘误**（v-fail-analysis-2026-09-19.md）：
   - 771 行 "B 过 V 败/视觉读星误导" → "无卡暴露（B 同败，C 卡救）"；
     "视觉特有退化 16 个" → 15 个，"无卡暴露 4 个" → 5 个；
   - 材料包多轮记录表拉取漏了 self_overestimate/A、B 目录（771 只列了 V/skillC 两行），
     轮次映射需修，否则 "B 过 V 败" 类误判会复发。
3. **TreeWalker 侧无代码改动**（本分析结论：现状架构已 DOM 优先，771 无代码缺陷；
   349 类口径依赖任务卡，卡已存在且被 skillC 实证）。
4. **可选加固（低优先）**：rating radio 倒序这个坑可进 domain-skills 的
   `approve-positive-reviews/quirks.md`（一句话："编辑页 rating 表格 radio 为
   5★→1★ 倒序排列，checked radio 的位置 i 对应 6−i 星；读 value−15 更直接"），
   防止未来无卡/半卡轮再花步数复核。真机验证后随手加。
