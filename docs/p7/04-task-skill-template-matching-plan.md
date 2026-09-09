# 任务级 skill 模板语义匹配升级方案（issue #182）

> 状态：**S1-S4 已实施，S3 离线门槛 PASS**（2026-09-09；执行记录见 §十）。
> S5 真机验收（评测仓口径 C 全量重跑）待做。
> 动因：口径 C 全量实测（184 任务，2026-09-09）泛化命中率仅 42/140 = 30%，
> 13/37 个模板变体零命中——匹配器按「任务本尊」而非「模板语义」工作。
> issue：[#182](https://github.com/zjordon/TreeWalker/issues/182)；
> 评测报告：treewalker-webarena `docs/task-skill-c-caliber-report-2026-09-09.md`。
> 前版：`docs/p7/03-task-skill-loading-design.md`（v2，已实施——其 §4.4 保守判据是本方案
> 的修订对象；03 同步小修见 §七 S4，不整体重写）。
> 范围：TreeWalker 侧匹配器 / 注入头 / 日志 / 测试 + 评测仓指标口径配套。
> TreeForge 侧重蒸（issue 点 2a）为独立配套项，**不阻塞本方案**（见 §5.3）。

## 一、问题定性与证据链

机制链路本身健康（回放漏命中 0/44、降档 0 次、命中-未命中 SR 差 +19.6pp），
问题出在**匹配判据**：`src/tree_walker/skills/task_matcher.py:25-41` 的 prompt
把「实体差异」与「模板差异」混为一根轴，全部推给拒绝侧。

三处措辞的直接后果（拒绝理由 reason 原文 → prompt 根因）：

| reason 原文（match 日志） | prompt 根因 |
|---|---|
| "The closest card adds a color option to a **different** product" | `"same goal on the same kind of target object"`——LLM 把商品实体解读为 target object identity 的一部分 |
| "The closest card (reduce-product-price) **differs**…"（同模板 6 变体全拒） | 正例 `"matches a card describing **exactly** that"` 强化字面匹配 |
| "The closest cards ask for the top-1 best-selling… but none for [本变体的年份/名次]" | 负例清单只有模板级差异（dimension/object/output），**无一句「实体不同不算差异」**；叠加 "When in doubt, return null" 全倒向拒绝 |

两个关键佐证（均来自报告 §2/§3）：

- **降档 0 次**：所有 miss 都是模型 high/medium 置信度下的主动拒绝——模型不是
  拿不准，是**自信地执行错误判据**。prompt 问题，非模型能力问题；
- **两极分化**（add-simple-product 4/4 vs reduce-product-price 0/6，结构同构）：
  判据欠定下 LLM 时而自行抽象、时而字面比较的典型症状。

v2 方案（docs/p7/03 §4.4/§8.2）自身的概念缺口：其反例（「按 SKU 查」vs「按名称查」、
「数全部」vs「数待审」）全是**模板级**差异，但文档从未把「实体差异」单列成轴——
两根轴被笼统塞进「近邻变体不命中」。录制清单（`shopping_admin-recording-plan-2026-08-30`
的设计：每模板 1 代表 + 换实体变体抽查泛化）的产品语义明确假设实体级变体命中。

## 二、判据语义钉死：两轴模型

匹配单元从「任务」升级为「操作模板」（operation template）：

| 轴 | 内容 | 例子 | 判定 |
|---|---|---|---|
| **实体**（参数，忽略） | 任务的数据槽位值 | 商品名 / 客户名 / 日期 / 年份 / 名次 / 数值 / 状态值 | **永不构成拒绝理由** |
| **模板**（意图，判据） | 动作动词 + 对象类型 + 参数维度 + 输出形态 | add vs remove；orders vs products；by quantity vs by price；count vs list | **任一不同 = 不命中** |

**最易混淆的边界形态必须进 prompt 当显式例子**——「维度 vs 参数值」：

- `"quantity = 0"` vs `"quantity = 3"`：同维度不同值 → **同模板**（命中）；
- `filter by quantity` vs `filter by price`：不同维度 → **不同模板**（拒绝）；
- `top-1 in 2023` vs `top-5 in 2024`：名次/年份是实体值 → **同模板**（命中）。

## 三、方案总览（三层，全部 TreeWalker 侧可控）

1. **prompt 判据升级**（核心，§4.1）：匹配目标 = 操作模板；实体互换给正例，
   模板差异给负例，边界形态显式钉死；
2. **匹配分级输出 + 分档注入头**（issue 点 2b 的落地形态，§4.2/§4.3）：schema 加
   `match_kind` / `task_kind` 两字段（同一次调用零增量成本），注入头按
   「本尊 / 模板命中 / 模板命中且读型」三档分级——**否决**「机械剥离卡内答案快照」
   的字面实现（见 §6）；
3. **观测与口径配套**（§4.4/§4.5）：日志加两字段；评测仓 `skill_metrics.py`
   误命中定义改为**模板等价类**。

设计不变量：host_key 候选域、`confidence` 白名单降档（low → null）、null 一等答案、
全异常降级不注入、每任务一次匹配、per-step 注入——**全部保留不动**。

## 四、详细设计

### 4.1 prompt 重写（`task_matcher.py` `_MATCH_PROMPT_TEMPLATE`）

最终版（v3，经 §十三轮离线迭代定稿；与实现逐字一致）：

```
You are a task-matching judge. Given a user task and a catalog of recorded task skills,
decide which recorded task follows the SAME OPERATION TEMPLATE as the user task.

Judge on two axes:
- ENTITY values are parameters, never matching criteria. Product names, customer names,
  dates, years, ranks, numbers, and statuses differ between instances of the same
  template. They NEVER justify rejection: "disable product X" matches a card that
  disables product Y.
- TEMPLATE identity is the criterion — all three must match:
  (a) operation family: count vs list vs read-one-field vs edit vs create vs delete vs
      generate-report vs notify;
  (b) object family: orders vs products vs customers vs reviews vs reports;
  (c) filter/sort dimension when one is applied: by status vs by keyword vs by date
      range vs by quantity — or no filter at all.
  Any of these differing = different template = null.
- SLOT values inside the same pattern are parameters too, NOT template identity:
  which field to read (customer name vs order ID vs date), which report type to
  generate (orders vs coupons vs shipping), which option type to add (size vs color),
  which direction an edit goes (increase vs reduce), which reviews a delete targets
  (all pending negative reviews of product X vs all reviews from reviewer Y), which
  quantifier picks the row (most, second-most, exactly 2), which extreme to pick (most
  recent vs oldest, top-1 vs top-5), amounts, dates, keywords. Substituting any slot
  value keeps the same template: "create a coupons report for May" matches a card that
  creates an orders report for another date range; "get the order ID of the newest
  pending order" matches a card that gets the customer name of the newest cancelled
  order.
- Dimension vs value: "quantity = 0" vs "quantity = 3" is the same template;
  "filter by quantity" vs "filter by price" is not. "top-1 in 2023" vs "top-5 in 2024"
  is the same template. But "count ALL reviews" vs "count reviews that MENTION a term"
  is NOT the same template — an added filter dimension (none vs keyword) changes the
  template.
- A composite task chaining two lookups that no single card performs is null — do not
  match a card covering only half of it.

Also classify the match: same_task = same template AND same entity values;
same_template = same template with different entity or slot values. And classify the
user task: read = asks to look up / compute / report a fact; operate = asks to change
site state.

Rules:
- Surface wording may differ (synonyms, language).
- When in doubt, return null — a wrong match is worse than no match; the agent will
  explore fine on its own.

User task:
{task}

Catalog (same site):
{catalog}
```

`_MATCH_SYSTEM_PROMPT` 同步（"...Entity and slot values (names, dates, amounts,
fields, types, direction) are irrelevant; operation family, object family, or filter
dimension differences reject. Be conservative..."）。`catalog_line()` 不改——matcher
需要看到实体才能做「实体剥离后比较」。

**v1（首版两轴）→ v3 的两处演进**（离线回归驱动，详见 §十）：
- v2 加**槽位置换原则**：v1 的「action verb / output shape 属模板身份」被 WebArena
  模板字符串证伪——`{{action}} the price of {{config}} by {{amount}}`（247/742）、
  `{{type}}` report（271）、`{{attribute}}`（366/234）、`{{option}}`（252）、
  `{{quantifier}}`（276）里这些全是**槽位**；模板身份收敛为三元组
  「操作家族 × 对象家族 × 过滤维度」；
- v3 补**无过滤 vs 有关键词过滤**显式负例（杀 v2 引入的 1 例真误命中：[288]
  "count reviews mentioning decent" 错配 count-total-reviews）+ 槽位例句扩
  delete-target / quantifier（赢回 246 ×2、276 ×1）。

### 4.2 schema 扩展与解析侧守卫（`task_matcher.py`）

```json
{
  "match": {"type": ["string", "null"]},
  "match_kind": {"type": ["string", "null"], "enum": ["same_task", "same_template", null],
                  "description": "same_task = same template and same entities; same_template = same template, different entities."},
  "task_kind": {"type": ["string", "null"], "enum": ["read", "operate", null],
                  "description": "Whether the user task reads a fact or changes site state."},
  "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
  "reason": {"type": "string"}
}
```

`required` 仍只锁 `match / confidence / reason`——text 兜底路径不做 schema 校验，
新字段必须容缺。解析侧归一化（与 confidence 白名单同风格）：

- `match_kind`：非 `same_task` / `same_template`（含缺失、乱值）→ **默认
  `same_task`**——分级是增强不是门控，缺省时行为与现状完全一致（回放路径零扰动）；
- `task_kind`：非 `read` / `operate` → None（不触发读型加严句）；
- `TaskSkillMatch` 加字段 `match_kind: str = "same_task"`、`task_kind: str | None = None`
  （frozen dataclass 追加带默认值字段，既有构造零破）。

### 4.3 分档注入头（`task_matcher.py`，`build_task_skill_text`）

签名扩展（向后兼容默认）：`build_task_skill_text(slug, card_text, *,
match_kind="same_task", task_kind=None)`。三档：

| 档 | 条件 | 头 |
|---|---|---|
| 本尊 | `match_kind="same_task"` 且非 read | **现状头不动**（`_TASK_SKILL_HEADER_TEMPLATE` 原文，含易变值声明句） |
| 模板命中 | `match_kind="same_template"` | 新头：卡为**同一任务类型的另一实例**录制，卡内所有具体值（名称/日期/数值）对该实例成立、**对你的任务是错的**；按步骤序列与导航路径走，替换为本任务的实体；页面是事实之源 |
| 读型加严 | `task_kind="read"`（**无论 match_kind**，本尊读型同样加） | 追加一段：本任务是读取事实——卡给出的是**通往答案的路径**而非答案本身；禁止报告任何来自卡内的数值/事实，答案必须从当前页面现取 |

读型加严不区分本尊/变体的理由：答案固化（docs/p7/03 §六）对本尊回放同样存在
（parrot 风险恰在回放最大），加严句对两档都是纯增益且零成本。

### 4.4 日志与接线（`agent.py`）

- `_match_task_skill` 的 S4 日志 JSON 加 `"match_kind"` / `"task_kind"` 两键
  （`agent.py:523-539`），命中/未命中/降档都照记；
- `build_task_skill_text` 调用点透传两字段（`agent.py:546-548`）；
- `step.py` **零改动**——`_task_skill_text` 组装一次、每步透传的现状结构，
  分级信息在组装时已烧进文本。

### 4.5 评测仓指标口径配套（treewalker-webarena 侧，随验收做）

- **误命中重定义**：旧口径「命中非本尊任务」在模板判据下失效；新口径 =
  **命中卡的模板 ≠ 任务模板**（按 `intent_template_id` 等价类判定）。`skill_metrics.py`
  聚类逻辑同步改，否则放宽后误命中率虚高（把模板命中算成误命中）；
- **同模板多卡**：42 模板 44 卡（多 2 张），命中同模板另一张卡算正确命中，
  统计按模板等价类；
- 数据源不动：`task-skill-match` 日志 + `config/replay_map.json` +
  `intent_template_id`（报告 §4.3 已确认可复算）。

## 五、与 issue 优化要求的对照裁决

1. **匹配目标升级为模板语义** → §4.1/§4.2，同意，核心改动。
2. **读型调和（2a/2b 二选一或组合）** → **组合**：2b 以「分级输出 + 分档头」形态
   落地（§4.2/§4.3）；2a（存量 44 卡重蒸，S0b 易变值规则已交付）是 treeforge 侧
   独立动作，值得排但**不阻塞**——且去实体化规则只管答案值不写死，SOP 叙事里
   代表任务的实体引用仍在，变体命中的实体替换头声明（§4.3 第二档）在重蒸后
   **依然必需**。两者正交互补。
3. **观测无需新增埋点** → 同意，仅扩两字段（§4.4）。
4. **验收** → 同意，但**加一道离线前置关卡**（§七 S3）：匹配器是纯 LLM 调用，
   不依赖浏览器——44 卡 catalog × 184 任务文本可离线批量跑 `match_task_skill` 层，
   prompt 迭代几轮都在离线完成，真机全量只跑最终版。直接用 184 任务完整 agent
   跑调 prompt，一轮就是数小时的代价。

## 六、否决的备选（附理由）

| 备选 | 否决理由 |
|---|---|
| 机械剥离卡内答案快照（issue 2b 字面形态） | TreeWalker 无法可靠定位 SOP 文本里哪句是录制时答案——脆弱且伤流程叙事；分档头声明效果近似、鲁棒性高 |
| `_task.json` 契约加 `task_kind` 字段 | 需 treeforge 配合改契约/重导出；matcher 从用户任务文本自判成本为零（模板天然分读/操作） |
| embedding / 向量预筛 | v2「不做」清单；44 卡 6k chars 单次调用无压力，瓶颈不在检索规模 |
| 双调用（先归一化 skeleton 再匹配） | 调用翻倍收益存疑；单调用 prompt 内显式给两轴规则 + 边界例子已足够（42 个已命中变体证明模型能稳定做模板抽象） |
| 放松 `confidence` 降档守卫 | 降档守卫（low → null）防的是「判据明确后仍拿不准」的情形，与判据本身正交，保留 |

## 七、实施步骤

- **S1 匹配器改造**（`task_matcher.py`）：prompt 重写（§4.1）+ schema 扩展与解析
  归一化（§4.2）+ 分档注入头（§4.3）。半天。
- **S2 测试**（`tests/test_task_skill.py`）：
  - prompt 锚点断言更新（`test_prompt_contains_task_and_catalog` 扩展：断言实体
    无关指令与模板判据关键短语在 prompt 内——用稳定短语，别锁全文）；
  - `match_kind` / `task_kind` 解析分支：合法值 / 缺失默认 / 乱值归一化；
  - `build_task_skill_text` 三档：本尊头不变、模板头含实体替换警示、read 追加
    现取句；
  - 降档路径带新字段仍正常；FakeMatchLLM 是 dict 注入，既有用例零破。半天。
- **S3 离线回归关卡**（新 `examples/p7_task_skill_match_regression.py`）——**✅ 已完成
  且门槛 PASS（2026-09-09，见 §十）**：
  - 输入 `--eval-root`（评测仓路径）：读 44 卡 catalog（`TaskSkillLoader` 直用）+
    184 任务文本（组装对齐 `evals/webarena/runner.py:349` 的
    `task_text = f"{intent}\n\n起始页: {start_url}"`——离线 fidelity 关键）+
    `intent_template_id`（模板等价类）；
  - 并发限流跑 `match_task_skill`（默认 6 并发，184 次调用 ~200s）；call-failed
    在 harness 层再试（基础设施故障≠匹配语义）；`--limit` 小样冒烟、`--gate` 门槛判定；
  - 输出四率：回放命中（44 本尊）/ 泛化命中（140 变体）/ 跨模板误命中 /
    降档数，外加零命中模板明细与全部 reason（JSON 明细含误命中/漏命中逐例复核）。
- **S4 文档同步**（`docs/p7/03`）：§4.4 判据改两轴表述（反例只留模板级差异，补
  实体级正例）；§8.2「不相交泛化」改为**跨模板**变体口径；§九风险表「近邻任务
  该不该命中」行重写；附录 B prompt 草案替换；文档头加指针指向本文。半天内。
- **S5 真机验收**（评测仓）：`reset_env.sh` → `./run_full.sh --skill-mode C --fresh`
  全量重跑，四率对比报告基线（§2/§3），并跑 A 同任务子集对照。S3 达标后只跑
  最终版一次。

## 八、风险与边界

| 风险 | 缓解 |
|---|---|
| 放宽后跨模板误命中上升（主要风险） | prompt 负例 + host_key 域 + low 降档三道既有防线；S3 离线误命中人审前置；S5 真机复核 |
| 回放漏命中偏离 0 | 判据是纯放宽方向（本尊命中 ⊆ 模板命中）；S3 门槛 44/44 先行验证 |
| LLM 判 `match_kind`/`task_kind` 不稳 | 解析侧白名单 + 缺省保守回退（默认 same_task = 现状行为，分级失效只退化为旧单档） |
| ≥80% 目标不可达 | 13 个零命中模板的拒绝理由全是实体级；42 个已命中变体证明判据明确后模型能稳定抽象；若离线迭代后仍 <80%，拿 reason 聚类再议（可能需 task_skeleton 输出字段引导先归一化——留作后备，不首发） |
| 口径纪律 | 红线不变：C 禁止与 A/B 或外部榜混合对比；命中面扩大后 C 的 SR 会涨，更须分列 |
| prompt 措辞漂移伤测试 | 锚点断言用稳定短语（entity/template 关键词），不锁全文 |

## 九、明确不做

- ❌ 机械剥离卡内答案快照（§六）
- ❌ `_task.json` 契约变更（task_kind 由 matcher 自判）
- ❌ embedding / 向量预筛、跨 host 匹配、命中即硬执行（沿 v2「不做」清单）
- ❌ 本方案内做 TreeForge 重蒸（2a 独立排期，不阻塞不耦合）
- ❌ 口径 C 与任何评测口径混合报告（作弊红线，重申）

## 十、S3 执行记录（2026-09-09，离线 184 任务全量）

三轮 prompt 迭代（v1 首版两轴 → v2 槽位置换 → v3 显式负例 + 槽位扩例），每轮
离线全量 184 任务：

| 指标 | 基线（issue 报告） | v1 | v2 | **v3（定稿）** |
|---|---|---|---|---|
| 回放正确命中（本尊 44） | 44/44（漏命中 0） | 44/44 | 44/44 | **44/44，exact slug 44** |
| 泛化命中（any-card / 140） | 42（30%） | 106（75.7%） | 119（85.0%） | **118（84.3%）** |
| 泛化正确模板（id 等价 / 140） | — | 102（72.9%） | 111（79.3%） | **112（80.0%）✅** |
| 语义正确（id + 槽位正确 id 错位） | — | — | — | **118（84.3%）** |
| 真误命中 | 未知 | 4 例 id 错位 | 1 例（[288] 关键词计数错配 count-total） | **0** |
| 零命中模板（有卡） | 13 | 1（tpl 271） | 0 | **0** |
| 降档 | 0 | 1 | 0 | **0** |
| match_kind 分级 | 无此字段 | 本尊 44 全 same_task | 同 | 同（分级行为正确） |

**离线门槛判定：PASS**（回放 44/44 + 泛化正确 ≥80%）。产物：
`out/task_skill_match_regression_v3.json`（v1/v2 中间产物同名 `_v2`/无后缀保留）。

迭代中钉死的三个数据事实（评测仓侧需要知道）：

1. **5 个模板无卡、19 个变体结构性不可命中**（tpl 288 关键词计数 ×5、280 notify ×5、
   247 "this product" 价格 ×6、255 show-all-customers ×1、42 地图路线 ×2）——录制计划
   声称「42 模板代表」，replay_map 实际覆盖 37 个模板（44 卡 / 37 模板）。变体天花板
   = 121/140（86.4%），当前 118 已触 97.5% 天花板。补卡是 treeforge 侧录制计划的事。
2. **247/742 是同一操作模板的两个 id**（`{{action}} the price of {this product|{{config}}}
   by {{amount}}`）——6 个 [247] 变体命中 reduce-product-price（本尊 tpl 742）语义正确
   但按 id 等价判「跨模板」。**评测仓 skill_metrics 的模板等价类建议把 247+742 并类**，
   否则这 6 例在误命中率里虚高；并类后 v3 的 id 口径正确率 = 118/140 = 84.3%。
3. **[234] 两个组合任务按组合守卫有意放弃**（WebArena 分类里它们是 `{{attribute}}`
   槽位值，但「top 客户最近取消订单的 SKU/总花费」是链式查询，半匹配卡有带偏风险）——
   换 2 个正确率不换安全性。

S5 真机验收前的状态：代码（S1/S2）+ 文档（S4，03 同步 v3 prompt）+ 离线门槛（S3）
就绪；测试 65 passed（test_task_skill），全量 2599 passed（S1 时点，此后仅 prompt
文本与 docs 变更）。
