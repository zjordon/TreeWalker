# 任务级 skill 加载技术方案 v2（评审修订定稿）

> 状态：设计定稿 v2（2026-09-05 v1 评审后修订，待实施）。
> 前版：`treeforge/docs/task-skill-loading-design.md`（v1，存档保留；评审意见已全部吸收进本版，对照见附录 C）。
> 关联：ROADMAP P7 改进路线三（任务级 skill 优化，仅产品口径）、TreeForge P4 双产物
>（`treeforge/docs/p4/p4-implement-plan.md` S6 读取契约）、`src/tree_walker/skills/loader.py`（站点级注入现状）。
> 职责划分：**TreeForge 定义数据契约与检索锚点，TreeWalker 按本文 S0-S5 实现**；本文是两端的对接基准。
> v2 修订动因：v1 评审发现 3 个 P1（host key 契约断裂 / 匹配时机未定义 / 评测口径 env 卫生）
> 与 5 个 P2，全部落进本版设计；所有「v1 是 X，v2 改为 Y」的裁决都附代码证据（file:line）。

## 一、背景与定位

站点级 skill 注入已在 WebArena 实测：任务成功率有效提升（ROADMAP P7：主口径 52.2% →
站点 skill 变体 65.2%），但失败率仍高——符合预期：站点卡（功能地图 + 通用知识）只解决
「路盲」和跨任务共性，不解决「这个具体任务的流程怎么走」。任务级 skill 补这一层：
**检索命中即走已验证流程，未命中回落自主探索**（蓝图路线三原话）。

**定位铁律（蓝图原文，不可违背）**：任务级 skill 是**产品口径**能力（企业重复流程的
RPA 化——命中高可靠、未命中测真实下限），**不是评测手段**。对自主探索评测口径，注入
任务级 skill 等价于泄露参考轨迹，属**作弊红线**：其 SR 禁止与任何评测口径（主口径 /
with-site-knowledge 口径 / 外部 leaderboard）混合或对比。评测纪律见 §八。

## 二、数据契约（v2 修订：host key 统一裁决）

### 2.1 目录布局与 host key（P1-1 修订，契约级）

```
domain-skills/<host_key>/
├── _sop.md / selectors.md / quirks.md     # 站点级（常驻注入，现状）
└── tasks/<slug>/                          # 任务级（命中才注入，本方案）
    ├── _sop.md                            # 该任务的连贯流程叙事（step 1..N）
    ├── selectors.md / quirks.md           # 该任务的元素指纹 / 坑
    └── _task.json                         # 检索元数据（锚点）
```

**`<host_key>` 的定义（本版钉死，两端共用同一语义）**：URL 的 hostname；URL 显式带
端口时为 `host_port`（`_` 连接，因 Windows 目录名不能含 `:`）。即 TreeWalker
`src/tree_walker/browser/url_utils.py:41-62` `extract_host_with_port` 的语义——
`http://localhost:7780/admin` → `localhost_7780`；`https://member.bilibili.com` →
`member.bilibili.com`。

**v1 的契约断裂（评审实测证据）**：

- TreeForge 侧按 `urlparse(url).hostname` 索引（`treeforge/adapters/treewalker_adapter.py:20`
  docstring），44 张卡实际产在 `domain-skills/localhost/tasks/`（裸 hostname）；
- TreeWalker 侧站点级注入的读取 key 是端口限定的（`agent.py:448-450` 用
  `extract_host_with_port(page_url)`），live 目录为 `domain-skills/localhost_7780/`
  （只含三件套——现状站点卡就是**手工改名搬运**的产物，这个暗步骤 v1 未记录）；
- 后果：S1 按 `<host_key>=localhost_7780` 扫 `tasks/` 会**静默零命中**——本方案的降级
  语义是「无卡 = 不注入 = 现状」，不报任何错，口径 C 会静默退化成口径 B，评测跑完
  数字一样，没人知道检索层根本没生效。非 localhost 站点两端 key 恰好重合（无端口），
  唯带端口 host 分叉——而评测站恰是 localhost:7780。

**裁决：两端统一用 `extract_host_with_port` 语义，TreeForge 侧对齐**（修订 v1 的
「TreeForge 已交付、只读」表述——契约本身有 bug，必须生产端修）：

1. TreeForge adapter 复制该 key 函数（纯 stdlib urlparse，零依赖），蒸馏产物写
   `domain-skills/<host_key>/`。站点级三件套同样受益（消灭手工改名步骤）。
2. 存量迁移（**可手工，立即可做**——S0a）：把 treeforge
   `data/skills/domain-skills/localhost/` 复制进本仓库 `domain-skills/localhost_7780/`
   （关键是 `tasks/` 子树；三件套与现存版本逐字节一致，已核实——2026-09-05 早晨的
   一次手工复制正是本仓库现三件套的来源）。`tasks/` 子目录不污染站点级注入——
   loader 按固定文件名直读、不递归（P4 现状盘点已论证）。
3. 否决的备选：TreeWalker 侧双 key 回退（`localhost_7780` → `localhost`）。端口限定
   key 是 P7 form_interaction 补丁特意引入的（本机 7780 Magento / 5173 tw-web 各挂
   各的 skill，互不误注入）；回退裸 host 会让 TreeForge 未来给多个本地服务蒸的卡
   混进同一目录，重新引入跨服务误注入。
4. 防再犯：匹配时大声日志 `task-skill catalog: N cards (host_key=...)`（S4）；N=0
   且 `tasks/` 目录存在 → warning。把这类静默失效变成一眼可见。

### 2.2 `_task.json` schema（不变，实测已核对一致）

| 字段 | 类型 | 用途 |
|---|---|---|
| `slug` | str | 任务标识（kebab-case，稳定——同任务重录覆盖同 slug） |
| `task_description` | str | 用户录制作业时的任务描述**原话**（检索主锚点） |
| `task_keywords` | str[] | 蒸馏时 LLM 提炼的关键词（≤5，站点语言，辅助锚点） |
| `source_traces` | str[] | 历次录制来源（可追溯，不参与检索） |
| `distilled_at` | ISO 时间 | 蒸馏时间（时效参考，v1 不做自动失效） |

### 2.3 实测规模（localhost_7780，44 张卡）

| 指标 | 值 | 含义 |
|---|---|---|
| 单卡三件套均值 / 最大 | 2,052 / 2,661 chars | 命中注入的上下文成本极低（≤3k） |
| catalog 全量（slug+desc+keywords） | ~6.0k chars / 44 任务 | **整个 catalog 一次喂给匹配器毫无压力**——LLM-as-ranker 不需要任何预筛 |
| 卡片分布 | 全部在单一 host_key 下 | 候选域 = 当前 host_key 的 tasks/，天然第一道精度过滤 |

### 2.4 双卡分工（注入语义，不变）

| | 站点级（host 卡） | 任务级（task 卡） |
|---|---|---|
| 内容 | 功能地图 + 跨任务共性 | 该任务的具体流程 + 该任务的坑 |
| 注入时机 | 进入该 host 即常驻 | **检索命中才注入** |
| 解决什么 | 路盲 / 共性控件 | 流程编排 / 步骤顺序 |

### 2.5 部署形态：蒸馏直装，消灭手工拷贝（S0b 的终局）

「每次蒸馏后手工拷到 TreeWalker」不是架构必然，是「两仓各存一份 + key 不一致」的
现状偶然。TreeWalker 的 skill 目录本就可配（`AGENT_SKILLS_DIR`，`config.py:419`，
loader 对绝对路径直用）；TreeForge 蒸馏产物根也可配（`distill --output`，产出
`<output>/domain-skills/<host_key>/`）。S0b 对齐 key 后，任选一端指向共享位置，
拷贝步骤即从工作流消失——「蒸馏完落盘 → 下一个 run 即见」成立（loader 缓存挂在
Agent 实例上，每任务/每 run 重建）：

- **形态 A（推荐）——蒸馏直装**：`treeforge distill --output` 指 TreeWalker 仓库根。
  蒸馏完成 = 安装完成；本仓库 `domain-skills/` 继续当唯一真源（douyin/bilibili
  存量卡不动），且卡进 git——每个评测基线注入的内容有 commit 级快照，可复现可追溯
  （65.2% 基线的卡片现状即被 git 跟踪）。注脚：`registry/` 同落 output 下，
  .gitignore 掉即可。
- **形态 B——直读**：`AGENT_SKILLS_DIR` 指 treeforge `data/skills/domain-skills`。
  一行 env 零侵入，但 douyin/bilibili 卡需先并入、产物不进 git，评测可复现性弱一截。

S0b 已交付（2026-09-06，treeforge issue #9 / commit `33bb9d2`）——key 分叉消除，
**形态 A 蒸馏直装自此可用**：`distill --output <本仓库根>` 即蒸馏完成 = 安装完成，
此后新蒸馏产物不再需要手工拷贝；存量 44 卡两侧已核实逐字节一致（此前「S0b 落地前
localhost 直读不到」的警告随之作废）。

## 三、总体流程与匹配时机（P1-2 修订：hook 点钉死）

```
Agent.run() 初始导航之后、步循环之前（一次性）：
  取当前页 URL → extract_host_with_port → host_key
  → 读 domain-skills/<host_key>/tasks/ → 无卡？→ 仅站点级（现状，零改动路径）
  → 组 catalog（slug + description + keywords）
  → LLM-as-ranker 一次匹配调用（见 §四）
      → match=<slug>：读该卡三件套 → 存 agent 实例字段（每步注入，见 §五）
      → match=null / 调用失败 / 无任务文本 / 无 host：不注入（安全降级 = 现状）
  → agent 正常跑（命中卡是指引，不是脚本）
```

**hook 点 = `Agent.run()` 里 `_extract_url` 导航（`agent.py:252-257`）之后**，取当前页
URL 的 host_key——与站点级注入（`step.py:273-274`）同一 keying。v1 流程图「任务开始
（拿到 user task 文本）→ 读当前 host」语焉不详，host 何时可知没有定义；真实链路是：

- **评测**：runner 把起始页拼进任务文本（`evals/webarena/runner.py:349`
  `task_text = f"{intent}\n\n起始页: {start_url}"`），TreeWalker 在 `run()` 里
  `_extract_url(task)` → navigate——**导航之前**当前页是 about:blank 或上一任务的
  残页，host 不可知也不可信（若在导航前取 host，串行评测会拿上一个任务的页面做
  匹配——错 host 错卡）；
- **产品（web 控制台）**：任务文本通常不含 URL，`_extract_url` 返回 None、导航为
  no-op，host = 用户当前 tab。web 入口与 CLI/TUI 同走 `Agent.run()`
  （`web/server.py:947` `await agent.run(keep_alive=True)`），hook 一处覆盖三端。

**边界情形**：

- 初始导航抛异常（`agent.py:256-257` 已捕获）：当前页为残页，按残页 host_key 匹配
  ——接受降级，日志记录实际使用的 host_key；
- 当前页无 host（about:blank）：不注入；
- rerun 重放不走 `Agent.run()` / `build_state_message`（已核实 `agent/rerun.py` 无
  引用）——与本机制零交集，符合「skill 注入与 rerun 重放是两个机制」的边界。

**每任务一次成立性**：CLI/TUI 每 run 一个 Agent；web 控制台每 task 新建 Agent
（`web/server.py:432`）——task 在 Agent 生命周期内不可变，重匹配只浪费。

## 四、检索设计（核心：精度优先）

### 4.1 候选域

仅当前 host_key 的任务卡。跨 host 任务不匹配（产品场景也是「同站重复流程」）。

### 4.2 匹配方法与调用通道（P2 修订：通道措辞对齐现状）

LLM-as-ranker（Browser-BC 哲学，「不做」清单：no embeddings）——catalog 全量 + 用户
任务文本 → 一次轻量调用，返回 `{match, confidence, reason}`。44 任务 catalog 仅 6k
chars，几百任务内都不需要分片或预筛；真到了千级再加关键词预筛（远期）。

**通道事实修正**：TreeWalker 没有 v1 所说的统一「fast 模型通道」；现状是三个互不相干
的先例——`AGENT_EXTRACT_MODEL`（`config.py:463-474`，None=复用主 LLM）、
`AGENT_JUDGE_MODEL`（`config.py:457`）、`MESSAGE_COMPACTION_MODEL`（`config.py:350`）。
本方案**镜像 extract 模式**新增：

- `AGENT_TASK_SKILL_MODEL` / `AGENT_TASK_SKILL_API_KEY` / `AGENT_TASK_SKILL_BASE_URL`
  （缺省回退主 LLM——glm-5.1 跑一次 ~2-3k token 输入 + ~100 token 输出的调用成本
  也可忽略；配 flash 级模型是优化不是前提）。

### 4.3 输出加固（P2 修订：tool-enforced structured output）

不用 v1 的「STRICT JSON only + 解析失败重试一次」自由文本约定，直接走**工具强制结构化
输出**——复用 `LLMClient.extract` 的 `output_schema` 路径（`llm/client.py:438-459`：
Anthropic tool + `tool_choice` 强制 + 模型未用工具时 text 兜底 + `_try_parse_json`
`client.py:497` 兜底自由文本），现成代码。schema：

```json
{"match": "<slug> | null", "confidence": "high | medium | low", "reason": "<一句话依据>"}
```

- API 级失败的 fallback 切换已内建（`_try_switch_to_fallback`，`client.py:62`）；
  「一次重试后放弃」仅保留给 API 失败，解析重试逻辑基本可删。
- 单次调用加 `asyncio.wait_for` 短超时（15s，对齐 `_extract_call` 的 `call_timeout`
  模式 `client.py:385-397`）；超时 = 降级 null。
- **confidence 降档规则（v2 新增）**：解析侧 `confidence == "low"` → 一律视为未命中
  （null），日志照记（含 downgraded 标记）——把「拿不准返回 null」从 prompt 自觉
  变成解析器强制。

### 4.4 误命中防护（误命中比未命中更糟——蓝图原话）

1. **保守匹配 prompt**（草案全文见附录 B）：只有「本质同一操作」才命中；近邻变体
   （如「数全部评论」vs「数待审评论」、「按 SKU 查」vs「按名称查」）默认**不命中**；
   拿不准返回 null。
2. **null 是一等答案**：未命中的代价只是回落探索（现状能力），误命中会带偏流程。
3. **匹配日志**：每次匹配（命中/未命中/降档都）记结构化日志（S4），评测后复盘误
   命中率——这是调 prompt 的唯一依据。

### 4.5 降级路径（全部等价于「不注入」）

无任务文本 / 无 host_key / 目录无卡 / 匹配调用失败（含超时，重试一次后放弃）。
**任何异常不得阻断 agent 启动**——匹配调用整体包 try/except。

## 五、注入设计

- **新块 `[Task Skill]`**，位置：`[Task]` 之后、`[Domain Skill]` **之前**
  （`prompts/system_prompt.py:160-170` 之间插入）——任务流程比站点共性更贴当前目标，
  放前面让 LLM 优先按流程走。
- **实现形态（v2 钉死 per-step 语义）**：`build_state_message` 新增 kwarg
  `task_skill_description: str | None = None`（默认 None，向后兼容，既有测试零破）；
  命中文本在 `run()` 匹配后存 agent 实例字段，`step.py` `_prepare_context` 每步透传
  ——**与 `[Domain Skill]` 完全同构，每步的 state message 重建时都带**，不是「命中时
  注入一次」。v1 §五读起来像一次性注入，此处明确。
- **成本核算（实测）**：站点卡 12.3k chars/步（localhost_7780 三件套实测）已在口径 B
  跑；任务卡均值 2.1k → +17%，远小于 DOM 预算。
- **与 message compaction 交互**：无需处理——每步 state message 是新建 user message，
  压缩历史不丢当前步上下文。
- **内容**：命中卡三件套全文，头部加「指引非脚本」声明（v2 增补易变值句，见附录 B）。
- **读序**：`_sop → selectors → quirks`（与站点级 loader 一致）。
- **开关**：`AGENT_ENABLE_TASK_SKILL_INJECTION`（默认 off，加在 `config.py`，模式同
  `AGENT_ENABLE_SKILL_INJECTION` 那行；独立于站点级开关——评测时可只开一个，分口径
  对照，见 §八）。
- **缓存**：进程内 per-host dict，对齐 `loader.py` 既有模式（`loader.py:67-76`）；
  **不做 mtime / 磁盘缓存**（v1 两处措辞作废——每任务 / 每 run 新建 Agent，缓存生命
  周期已够；长会话重蒸馏本就预期重跑）。

## 六、执行语义

- **skill 是上下文提示，不是可执行脚本**：agent 仍用自己的动作循环自主执行；命中卡
  给的是步骤编排与坑位提示，元素定位、等待、重试仍是 agent 自己的能力。
- **命中卡失准不特殊处理**：prompt 已声明「页面是事实之源，步骤与现实不符时自主
  调整」——agent 自然回落探索，与未命中同路径。不做「命中即锁定步骤」的硬执行
  （那是 rerun 重放那条线的事，与 skill 注入是两个机制）。
- **⚠️ 读型任务的答案固化（v2 新增，评审实测发现）**：抽检
  `count-pending-reviews/_sop.md` 第 5 步写着「`N records found`（**本例为 5**）」——
  录制时的答案值固化进了卡。影响：
  - 产品：站点数据漂移后卡内数字过期（新待审评价进来说 5 就是错的）；
  - 评测：口径 C 同任务回放可能测成「阅读理解」——agent 直接 parrot 卡内答案，RPA
    sanity 数字失真（见 §八 caveat）。
  - v1 内缓解（TreeWalker 侧）：注入头声明加一句「卡中具体数值是录制时快照，一律以
    页面当前读数为准」（附录 B 已更新）。
  - 遗留（TreeForge 侧，不阻塞本方案）：蒸馏 prompt 加「易变结果值（计数/金额/日期
    结果）不写死、或显式标注为录制时示例」——列入 TreeForge 待办，两端文档同步。

## 七、实施步骤

- **S0a 存量迁移（手工，约五分钟，立即可做——口径 C 的唯一前提就是这个）**：把
  treeforge `data/skills/domain-skills/localhost/` 复制进本仓库
  `domain-skills/localhost_7780/`。关键是 `tasks/` 子树；**三件套保持与 65.2% 基线
  一致的版本**（当前两侧逐字节一致，整目录拷贝等价；但别在任务级实验里同时引入
  新版站点卡——一次只动一个变量）。S1 按目录位置扫描，不关心文件怎么来的。
- **S0b 契约修复（TreeForge 侧代码）——✅ 已交付 2026-09-06**（treeforge issue #9 /
  commit `33bb9d2`）：新建 `harness/hostkey.py` 共享 key 函数（与本仓库
  `extract_host_with_port` 逐字对齐）+ ADAPT 按事件 URL 对账升级存量裸 hostname
  trace（不改 captures 数据）+ 易变值 prompt 规则 + 直装 runbook；数据侧
  `localhost/`→`localhost_7780/`、registry 同步 rename，44 卡与本仓库逐字节一致
  （MD5 全量校验，无需重拷）。**§2.5 形态 A 蒸馏直装自此可用。**
  原计划（留档）：adapter 的
  host 目录名对齐 `extract_host_with_port` 语义；蒸馏 prompt 的易变值规则（§六遗留
  项，可同车）。不做 S0b 的后果**不是本次零命中**（S0a 已让 44 张卡可见），而是
  **复发**：adapter 仍写裸 `localhost/`，此后每次蒸馏（重录任务 / 站点改版后重蒸）
  都落在旧名字下，手工复制退化为常驻暗步骤——漏一次拷贝，轻则新卡静默缺失，重则
  **旧卡过期仍被注入**（treeforge 已更新、本仓库还在注旧版：比零命中更隐蔽，数字
  看着正常、内容是旧的）。S4 大声日志与 §8.1 sanity 断言正是给手工流程兜底的
  loud 检测：catalog N 不及预期、或 catalog 最新 `distilled_at` 落后 treeforge 侧
  产物时间，都应显形。
- **S1 catalog 扫描**：`skills/task_loader.py`——扫
  `domain-skills/<host_key>/tasks/*/_task.json`，进程内缓存；**路径解析必须复用
  `SkillLoader._resolve_dir` 的 repo-root 回退**（`loader.py:42-65`——2026-08-24 踩坑：
  评测 runner 以 `evals/webarena` 为 CWD，相对 `domain-skills` 在那里不存在；最省事是
  task_loader 挂在 agent 已持有的 SkillLoader 实例上，不独立再写一份路径解析）。单张
  `_task.json` 解析失败跳过该卡（warning），不让一张坏卡废掉整个 catalog；无 `tasks/`
  目录返回空。
- **S2 匹配器**：`match_task_skill(task_text, catalog) -> slug | None`——§4.2/4.3 通道
  与 structured output；API 失败重试一次、超时 15s、异常全包后降级 null。
- **S3 注入**：`run()` 初始导航后触发匹配（§三 hook）；`system_prompt.py`
  `build_state_message` 加 `task_skill_description` kwarg + `[Task Skill]` 块；env 开关
  默认 off。
- **S4 匹配日志**：`logger.info` 固定前缀单行 JSON（`task-skill-match: {...}`，字段
  ts / host_key / catalog_size / catalog_newest_distilled_at / task(截断 200 chars) /
  match / confidence / reason / downgraded），命中、未命中、降档都记；**catalog 大声
  日志**（found: N cards，N=0 且目录存在 → warning）。`catalog_newest_distilled_at`
  是手工迁移的过期探针——S0b 落地前，它与 treeforge 侧产物时间差一眼可查（S0b 的
  落地判据之一）。不建新文件——评测后从既有 agent 日志 grep 统计。
- **S5 评测脚本分口径**：见 §八 env 矩阵；变体只显式设置注入 flag 与输出文件名，
  其余参数与 `run_full.ps1` 完全一致。

工作量重估：S0a 约五分钟（手工，可先做）；S0b 半天（TreeForge，可后补）；S1-S3
一天到一天半（改动面小、先例齐全）；S4-S5 半天。

## 八、评测口径（红线落地 + env 卫生）

### 8.1 口径与 env 矩阵（P1-3 修订）

| 口径 | `AGENT_ENABLE_SKILL_INJECTION` | `AGENT_ENABLE_TASK_SKILL_INJECTION` | 性质 |
|---|---|---|---|
| A 主口径 | **false（必须显式设）** | false | 现状基线，对外可比 |
| B with site knowledge | true | false | 变体分列报告（已实测 65.2%） |
| C with task knowledge | true | true | **产品口径**：禁止与 A/B 或外部 leaderboard 混合/对比 |

**现状警示（v2 新增，评审发现）**：`AGENT_ENABLE_SKILL_INJECTION` 默认值是 **true**
（`config.py:384`），而 `run_full.ps1` / `smoke_test.py` 均不设置它；加之
`domain-skills/localhost_7780/` 已落地 + loader 的 repo-root 回退（`loader.py:42-65`），
意味着**现在裸跑 `run_full.ps1` 实际是口径 B，不是口径 A**。主口径历史基线（52.2%）
是卡片落地前跑出的；此后任何「默认跑」都不再是 A。因此 S5 的变体脚本必须**显式设置
上表两个 flag**（PowerShell wrapper 里显式赋值，不依赖 shell 继承），并：

- **口径元数据入 results**：`smoke_test.py` 结果 JSON 头部记录
  `{skill_site, skill_task, tw_version, timestamp}`——防 shell env 残留污染 + 防事后
  口径混淆。事故形态预演：一次口径 C 跑完 shell 里残留 env，下一次「以为在跑 A」的
  全量被污染——这正是 §一红线最怕的事故，元数据让它在结果文件里自查可见。
- **sanity 断言（防静默退化）**：口径 C 同任务回放跑完若全程 0 次 match 且 catalog
  found=0 → 检索层 bug（host key / 路径错，即 §2.1 那类断裂），该批评测作废重跑，
  不出数字。

### 8.2 口径 C 的两种诚实测法

1. **同任务回放**（RPA sanity）：蒸馏任务 = 测试任务。预期很高（本来就该高），只验证
   「录过的任务能稳跑」，数字不对外。**caveat（v2）**：受答案固化影响（§六），读型
   任务可能 parrot 卡内答案——此测法只做「录过能跑」的下限 sanity，数字不作为能力
   证明；彻底修掉要靠 TreeForge 蒸馏侧的易变值规则。
2. **不相交泛化**（有信息量的数字）：蒸馏任务集 A 与测试任务集 B **不相交**（同站的
   变体任务，如蒸馏「按数量筛商品/按状态筛订单」，测「按价格筛商品/按日期筛订单」）。
   衡量任务知识的近邻泛化——若这个数字也好，说明任务级 skill 不止 RPA。**注意**：
   近邻变体（§4.4 的「不命中」区）在此口径下应判未命中回落探索，即测的是「不误命中
   + 站点级兜底」，别和口径 C 的命中路径混淆。

配套指标：命中率 / 误命中率 / 漏命中率（从 S4 日志统计）——比 SR 更早暴露检索质量
问题。

## 九、风险与边界

| 风险 | 缓解 |
|---|---|
| 误命中带偏流程 | 保守 prompt + null 自由度 + low 降档 + host_key 域 + 日志复盘（§四） |
| host key 再分叉（两端命名漂移） | 统一 key 语义写进契约（§2.1）+ S0 迁移 + catalog 大声日志 + §8.1 sanity 断言 |
| 手工迁移漏拷 / 过期卡仍被注入（S0b 落地前常驻） | catalog N 大声日志（不及预期即显形）+ `catalog_newest_distilled_at` 过期探针（S4）+ S0b 落地根治 |
| 读型任务答案固化 | 注入头易变值声明（v2）+ TreeForge 蒸馏 prompt 待办；同任务回放只做下限 sanity |
| 匹配调用阻断 agent 启动 | 全异常捕获 + 15s 超时 + API 失败一次重试后降级 null（§4.3/4.5） |
| 上下文膨胀 | catalog 6k/44 任务一次调用；命中卡 ≤3k chars/步（vs 站点卡 12.3k 已在跑）；总量远小于 DOM 预算 |
| 卡片过期（站点改版） | v1 靠「指引非脚本」自适应 + 重新蒸馏；不做自动失效 |
| 近邻任务该不该命中 | v1 严格不命中（回落探索，站点级兜底）；「cousin 档匹配」留作后续实验，先拿日志说话 |
| 多 host 任务 | v1 仅当前 host_key；跨 host 匹配不做（产品场景不支持） |
| 匹配调用成本 | 每任务一次调用（~3k token 级），可忽略 |

## 十、明确不做

- ❌ embedding / 向量检索（LLM-as-ranker 足够，沿「不做」清单）
- ❌ 跨 host 任务泛化匹配
- ❌ 命中即硬执行 / 步骤锁定（与 rerun 重放是两个机制，不混）
- ❌ TreeForge serve 运行时依赖（文件注入零依赖原则；TreeWalker 只读磁盘）
- ❌ 口径 C 与任何评测口径混合报告（作弊红线）
- ❌ mtime / 磁盘缓存（进程内缓存已够，v2 收紧 v1 的松措辞）

## 附录 A：实测数据（localhost，2026-09-05）

44 张任务卡（Magento Admin 测试站，WebArena 评测任务录制蒸馏）：单卡三件套均值
2,052 chars（最大 2,661）；catalog 全量 5,977 chars；站点级三件套（localhost_7780）
实测 12,303 chars。`_task.json` 示例：

```json
{
  "slug": "count-pending-reviews",
  "task_description": "What is the total count of Pending reviews amongst all the reviews?",
  "task_keywords": ["Pending", "评价", "待审", "数量", "reviews"],
  "source_traces": ["data\\captures\\c2f9582c\\trace.json"],
  "distilled_at": "2026-08-31T20:25:29.864244+00:00"
}
```

## 附录 B：匹配 prompt 草案（v2 微调）与注入头声明

匹配走 tool-enforced structured output（§4.3），以下为 prompt 主体（双语任务均适用；
输出经 `task_match_result` 工具强制返回，无需在 prompt 里祈求 STRICT JSON）：

```
You are a task-matching judge. Given a user task and a catalog of recorded task skills,
decide which recorded task is ESSENTIALLY THE SAME operation as the user task.

Rules:
- Match ONLY if a recorded task has the same goal on the same kind of target object
  (e.g. "count products with 0 quantity" matches a card describing exactly that).
  Surface wording may differ (synonyms, language).
- "Similar but different" is NOT a match: different filter dimension (by SKU vs by name),
  different object (orders vs invoices), different output (count vs list vs detail).
- When in doubt, return null — a wrong match is worse than no match; the agent will
  explore fine on its own.

User task:
{task}

Catalog (same site):
- `slug` — {description} | keywords: {keywords}
...
```

tool schema：`{"match": string|null, "confidence": "high"|"medium"|"low", "reason": string}`；
解析侧 `confidence=="low"` 一律降档为 null（§4.3）。

命中卡注入头声明（置于 `[Task Skill]` 块首；末句为 v2 增补）：

```
A recorded task matching your current goal was found (slug: {slug}). It describes a
PROVEN flow for essentially this task — follow it as guidance. The live page is the
source of truth: if any step no longer matches reality, adapt and explore on your own.
Concrete values in this card (counts, amounts, dates, names) are snapshots from the
recording session — always re-read the current value from the page.
```

## 附录 C：v1 → v2 修订对照（评审发现 → 设计裁决）

| # | 级别 | v1 问题 | v2 裁决 | 落点 |
|---|---|---|---|---|
| 1 | P1 | host key 契约断裂：TreeForge 写裸 hostname（44 卡在 `localhost/`），TreeWalker 读端口限定 key（`localhost_7780`）→ 静默零命中 | 统一 `extract_host_with_port` 语义；TreeForge adapter 对齐 + 存量迁移；否决 TreeWalker 双 key 回退 | §2.1 / S0 |
| 2 | P1 | 匹配时机未定义（「任务开始读当前 host」——host 彼时不可知，导航前取的是上一任务残页） | 钉死 hook = `Agent.run()` 初始导航之后取当前页 URL；CLI/TUI/web 三端同入口；rerun 零交集 | §三 |
| 3 | P1 | 评测口径 env 卫生：站点开关默认 true + 脚本不设 env ⇒ 裸跑 run_full.ps1 实为口径 B | 变体脚本显式设双 flag；口径元数据入 results JSON；catalog=0 的 sanity 断言 | §8.1 / S5 |
| 4 | P2 | 任务卡内嵌易变答案（「本例为 5」） | 注入头加易变值声明；TreeForge 蒸馏 prompt 列待办；同任务回放只做下限 sanity | §六 / 附录 B |
| 5 | P2 | 「fast 模型通道」不存在（实为三个互不相干先例） | 镜像 `AGENT_EXTRACT_MODEL` 模式新增 `AGENT_TASK_SKILL_MODEL`，缺省回退主 LLM | §4.2 |
| 6 | P2 | 自由文本 STRICT JSON + 解析重试脆弱 | tool-enforced structured output（复用 `LLMClient.extract` 现成路径）+ low 降档 | §4.3 |
| 7 | P2 | S1「按 mtime 缓存，对齐 loader.py」两半各对一半（实为进程内 dict 缓存） | 收紧为进程内 per-host dict，明确不做 mtime / 磁盘缓存 | §五 |
| 8 | P2 | S1 独立写路径解析会重蹈 eval CWD 覆辙 | task_loader 复用 `SkillLoader._resolve_dir` repo-root 回退 | S1 |
| 9 | P2 | 注入语义读起来像「一次」，实为每步重建 | 钉死 per-step 语义（与 `[Domain Skill]` 同构）+ 成本核算 + compaction 交互说明 | §五 |
