# Skill 文件注入 Agent 上下文 —— 设计方案

> 状态：方案设计，待实施决策。本文是 TreeWalker ROADMAP P1（skill 注入机制）的工程实施依据。
> 与 TreeForge ROADMAP P1（手写 skill + A/B 验证）是同一件事的两端。
>
> 范围：仅设计方案，不含代码实现。
>
> **2026-07-27 修订**：增加默认关闭开关 `enable_skill_injection`（默认 `False`，未显式开启时零行为变更），详见第三节改动 3。

---

## 一、目标与定位

### 目标

agent 探索时按当前域名读取 `domain-skills/<host>/` 的 skill 文件，注入 agent 上下文，提升探索准确率。

> **默认关闭（opt-in）**：本机制默认不加载、不注入。需通过 `enable_skill_injection=True`（构造传参）或 `AGENT_ENABLE_SKILL_INJECTION=true`（环境变量）显式开启；未开启时 agent 行为与无此机制完全一致（零行为变更）。理由：skill 注入是 P1 实验性功能（A/B 验证未完成），多数部署无 skill 文件，且涉及文件 IO。

### skill 定位

**给 LLM 看的上下文提示**，不是给 CDP 直接执行的结构化 selector 库。这一个定位决定了整个方案的精度要求和注入方式：
- 精度要求：「LLM 能看懂」即可，比 record-replay 的「五级匹配」低一档
- 注入方式：进 user message（state message 的可选段），不进 system prompt 常量

### 与战略转折的关系

这是 [[manual-vs-agent-recording]] 战略转折后的核心方向——不改 agent 逻辑，给 agent 喂知识。TreeForge 蒸馏产出 skill 文件，TreeWalker 注入消费。详见 TreeWalker ROADMAP P1 / TreeForge ROADMAP P1。

---

## 二、注入点设计决策（核心）

### 三个候选注入点的取舍

经代码核实，TreeWalker 的 agent 循环有三个潜在的注入位置。逐一分析：

| 候选 | 结论 | 理由 |
|---|---|---|
| ❌ `build_system_prompt` 扩展（仿 `FILE_UPLOAD_RULES`） | 不选 | system_prompt 每步重建成本高；skill 是「上下文」非「角色定义」，语义不符；且 skill 随域名变化，塞 system_prompt 要每步重拼 |
| ❌ `_add_context_message`（TYPE_CONTEXT 槽位） | 不选 | TYPE_CONTEXT 是给**瞬时 nudge**（budget warning / force done）用的，每步 `_clear_context_messages` 清空重灌。skill 是**持久上下文**，用这个槽位语义错配 |
| ✅ `build_state_message` 加可选 kwarg（仿 `sensitive_description`） | **选定** | `sensitive_description` 已经是「按 URL 过滤、注入 state message 可选段」——和 skill 注入需求完全同构 |

### 核心依据：`_build_sensitive_description` 是现成范式

代码现状（`agent.py` 的 `_build_sensitive_description`）：
- 输入：当前页 URL
- 处理：按 URL 用 fnmatch 过滤，构造可选字符串
- 输出：返回字符串或 None
- 消费：传给 `build_state_message` 的 `sensitive_description` kwarg，`if x:` 控制渲染
- 生命周期：state message 每步替换（`_set_state_message`），跟着每步自然更新

**skill 注入是这个范式的「第二兄弟」**——把「按 URL fnmatch 过滤」换成「按 host 读文件」，其余完全复用。零新生命周期管理，零新消息分类。

### 为什么不进 system_prompt

system_prompt 已经每步重建（`_update_action_models_for_page` 按 URL 过滤 action 列表），但：
- system_prompt 是「角色定义 + 规则」（如 FILE_UPLOAD_RULES 是「永远不要点上传按钮」这类硬规则）
- skill 是「领域知识」（这个站点的 selector / 怪癖 / 流程），属于上下文而非规则
- 把可能很长的 skill 文本塞 system_prompt 会抬高每步 token 成本（Anthropic 计费上 system 每步重发）

---

## 三、模块设计

### 改动 1：host 解析工具函数（新模块）

**位置**：`src/tree_walker/browser/url_utils.py`

**职责**：从 URL 提取 hostname（`www.bilibili.com`），供 skill 加载器和其他按域名分发的能力共用。

**设计要点**：
- 用标准库 `urllib.parse`，零依赖
- 先按完整 hostname 起步，不强制 eTLD+1 聚合（后续若要 `bilibili.com` 聚合 `www.bilibili.com` 再引入 tldextract）
- 无效 URL 返回 None

**为什么独立模块而非 BrowserSession 方法**：BrowserSession 是 CDP 适配层，职责单一；host 解析是纯函数，可被 agent、registry、skill loader 共用。

**公开接口**：
```
extract_host(url: str) -> str | None
```

### 改动 2：skill 加载器（新模块）

**位置**：`src/tree_walker/skills/loader.py`（新建 `skills/` 包）

**职责**：按 host 加载 `domain-skills/<host>/` 下的 skill 文件，拼成可注入的文本。

**目录约定**（与 TreeForge `adapters/treewalker_adapter.py` 输出对齐）：
```
<skills_dir>/<host>/{_sop.md, selectors.md, quirks.md}
```

**三文件含义**（`api.md` 已弃用——URL 信息在 DOM `[Current URL]` 可见，或并入 quirks 的"提交后跳转"；保留 `_sop`/`selectors`/`quirks` 三件套）：
| 文件 | 性质 | 内容 |
|---|---|---|
| `_sop.md` | 骨架 | 这个站点常见任务流程（量少） |
| `selectors.md` | 血肉 | 多候选元素的选择指导（量大、可操作） |
| `quirks.md` | 怪癖 | 隐藏等待、SPA 导航、框架行为、提交后跳转预期 |

**设计要点**：
- 固定读取顺序：SOP → SELECTORS → QUIRKS（骨架在前，血肉次之）
- 按 host 缓存拼好的文本，避免每步 IO（agent 每步都调 `load_for_host`，但同 host 只首次读盘）
- 部分文件缺失只拼存在的；host 目录不存在返回空串（静默，skill 是可选增强）
- 提供 `invalidate(host)` 清缓存（skill 文件更新后调用）

**公开接口**：
```
class SkillLoader:
    def __init__(self, skills_dir: str | Path) -> None
    def load_for_host(self, host: str | None) -> str   # 无 skill 返回空串
    def invalidate(self, host: str | None = None) -> None
```

**渲染格式**：每个文件渲染成 `[SOP]`/`[SELECTORS]`/`[QUIRKS]` 段，多文件用空行分隔。

### 改动 3：配置项（双范式：开关仿 `enable_observability`，目录仿 `rerun_history_dir`）

**位置**：`src/tree_walker/config.py`

本改动涉及两个字段，分别照搬两个现成范式：

#### (a) 开关字段（仿 `enable_observability`，默认关闭）

skill 注入是 opt-in 功能，默认不开启：
- `AgentSettings` 加字段：`enable_skill_injection: bool = False`
- `load_settings()` 加 env 加载：`enable_skill_injection=os.environ.get("AGENT_ENABLE_SKILL_INJECTION", "").lower() == "true",`

**参考范式**：`enable_observability`（`config.py:93` 字段 `= False` + `config.py:362` env 默认空串 `.lower()=="true"` + `agent.py:163` 消费三件套）。默认关闭用空串 `""` 是项目主流惯例（`enable_observability`/`enable_decision_attribution`/`display_files_in_done_text` 等均如此）。

#### (b) 目录字段（仿 `rerun_history_dir`，保留原方案）

- `AgentSettings` 加字段：`skills_dir: str = "domain-skills"`
- `load_settings()` 加 env 加载：`skills_dir=os.environ.get("AGENT_SKILLS_DIR", "domain-skills"),`

**参考范式**：`rerun_history_dir`（`config.py:113` 字段 + `config.py:373` env + `agent.py:78` `Agent.__init__` 读取三件套）。

#### 两字段独立 + env 解析坑

开关关时 `skills_dir` 仍可配置（只是不被读取）。即「目录字段永远可配，开关关时不被消费」——不要误以为「开关关 = `skills_dir` 必须为空」。

> **env 解析坑**：`.lower()=="true"` 意味着仅 `true`/`True`/`TRUE` 开启；`1`/`yes`/`on`/`False`/空串均关闭。

### 改动 4：Agent 构建 loader + 注入方法

**位置**：`src/tree_walker/agent/agent.py`

**两处改动**：

**(a) `__init__` 缓存开关 + 无条件构建 loader**：
- 缓存开关（仿 `agent.py:109` `self._enable_sensitive_description = _settings.enable_sensitive_description`）：
  - `self._enable_skill_injection = _settings.enable_skill_injection`
- 读目录（仿 `agent.py:78`，字符串字段不加下划线前缀）：
  - `self.skills_dir = _settings.skills_dir`
- 构建 loader（**无条件**）：
  - `self._skill_loader = SkillLoader(self.skills_dir)`
  - loader 内部对目录不存在静默处理，`__init__` 不报错

> **为什么 loader 无条件构建**（不写成 `SkillLoader(...) if _settings.enable_skill_injection else None`）：
> 1. 与亲兄弟 `enable_sensitive_description` 镜像——数据源常驻、门控在调用点（`agent.py:109` 缓存开关 + `agent.py:115 self._sensitive_data_raw` 永远初始化）
> 2. 构造零副作用——`SkillLoader.__init__` 只存路径不读盘，真正 IO 在 `load_for_host`
> 3. 避免 `None` 分支扩散到 `_build_skill_description`/`invalidate`/调试路径

**(b) 新增 `_build_skill_description` 方法**（紧邻 `_build_sensitive_description`）：
- 输入：当前页 URL
- 处理：`extract_host` → `_skill_loader.load_for_host`
- 输出：skill 文本或 None（无 skill 时 None，`build_state_message` 不渲染）
- 完全仿 `_build_sensitive_description` 的「URL 驱动 → 可选字符串」模式
- **入口不加 `if not self._enable_skill_injection: return None` 内守卫**——严格镜像亲兄弟（它也没有内守卫），门控统一放在调用点（见改动 5），避免「两道门控到底谁负责」的歧义

### 改动 5：接入 state message 构造

**位置**：`src/tree_walker/agent/step.py` 的 `_prepare_context` + `src/tree_walker/prompts/system_prompt.py`

**两处改动**：

**(a) `_prepare_context` 调用点三元门控**（紧邻 `sensitive_desc`，仿 `step.py:239-243`）：

step.py 类属性声明区（紧邻 `_enable_sensitive_description: bool`）加：
- `_enable_skill_injection: bool`

`_prepare_context` 内（紧邻现有 `sensitive_desc` 三元）：
```
skill_desc = (
    self._build_skill_description(browser_state.url)
    if self._enable_skill_injection
    else None
)
```
传入 `build_state_message(..., skill_description=skill_desc)`。

> **门控只在调用点**：开关关时直接置 `skill_desc=None`，不调 `extract_host`、不触 loader、零 IO。`_build_skill_description` 方法本体不感知开关（见改动 4）。

**(b) `build_state_message` 加 kwarg + 渲染段**（开关不影响渲染层）：
- 加 kwarg `skill_description: str | None = None`
- 在 `[Task]` 之后、`[Available Secrets]` 之前插入渲染段（skill 是领域知识，应靠前让 LLM 尽早看到）：
  ```
  if skill_description:
      parts.append("[Domain Skill]")
      parts.append(skill_description)
      parts.append("")
  ```

**为什么 `[Domain Skill]` 放在 `[Task]` 之后**：LLM 读消息是从上到下，Task 是目标、Skill 是「怎么达成目标的知识」，紧跟着让 LLM 在看页面状态前先吸收领域知识。渲染层的 `if skill_description:` 已天然处理 None——开关只控制「要不要算 skill」，不控制「要不要渲染」。

---

## 四、数据流

完整链路（每步触发）：

```
agent._step
  └─ _prepare_context
       ├─ browser.get_state()                          # 拿当前页 url
       ├─ _update_action_models_for_page(url)          # 现有：按 URL 过滤 action
       ├─ _build_sensitive_description(url)            # 现有：默认开，按 URL 列 secret
       ├─ [开关] if self._enable_skill_injection:      # 新增门控（默认关）
       │    └─ _build_skill_description(url)           #   开：按 host 读 skill
       │         ├─ extract_host(url)                  #   www.bilibili.com
       │         └─ _skill_loader.load_for_host(host)  #   缓存命中或读盘
       │              └─ 读 <skills_dir>/<host>/{_sop,selectors,quirks}.md
       │                   → 拼成 "[SOP]...\n[SELECTORS]..." 文本
       ├─ else: skill_desc = None                      # 关：跳过，零 IO（默认）
       └─ build_state_message(..., skill_description=skill_desc)
            └─ 渲染 [Domain Skill] 段进 state message
                 └─ _set_state_message（每步替换）
```

**缓存行为**：`SkillLoader._cache` 按 host 缓存。agent 在同一站点连续操作时，每步都调 `load_for_host` 但只首次读盘，后续走缓存。导航到新域名时读新 host 的 skill。

**开关行为**：`_enable_skill_injection=False`（默认）时，`_prepare_context` 直接置 `skill_desc=None`，不调 `extract_host`、不触 loader、零文件 IO。`SkillLoader` 实例仍常驻（`__init__` 时已无条件构建），仅为后续可能的运行时翻转就位。

---

## 五、关键文件改动清单

| 文件 | 改动类型 | 内容 |
|---|---|---|
| `src/tree_walker/browser/url_utils.py` | 新增 | `extract_host` 函数 |
| `src/tree_walker/skills/__init__.py` | 新增 | 包标识 |
| `src/tree_walker/skills/loader.py` | 新增 | `SkillLoader` 类 |
| `src/tree_walker/config.py` | 改动 | `AgentSettings` 加 `enable_skill_injection: bool = False`（仿 `enable_observability`）+ `skills_dir: str = "domain-skills"`（仿 `rerun_history_dir`）；`load_settings()` 加对应两条 env 加载 |
| `src/tree_walker/agent/agent.py` | 改动 | `__init__` 缓存 `self._enable_skill_injection` + **无条件**建 `self._skill_loader` + `_build_skill_description` 方法（入口无内守卫） |
| `src/tree_walker/agent/step.py` | 改动 | 类属性声明 `_enable_skill_injection: bool`；`_prepare_context` 用三元门控调 `_build_skill_description`（仿 `sensitive_desc`） |
| `src/tree_walker/prompts/system_prompt.py` | 改动 | `build_state_message` 加 kwarg + 渲染段 |
| `tests/test_skills_loader.py` | 新增 | loader 单测 |
| `tests/test_agent_skill_injection.py` | 新增 | 注入单测 |
| `domain-skills/<host>/*.md` | 新增 | 手写 skill（A/B 验证用，非代码） |

---

## 六、设计要点与约束

1. **默认关闭 = 零行为变更**：`enable_skill_injection` 默认 `False`，未显式开启时 agent 行为与无此机制完全一致。开关字段仿 `enable_observability`（默认关三件套），消费结构镜像亲兄弟 `enable_sensitive_description`（默认开，调用点三元门控 + 数据源常驻）。两兄弟唯一差异是默认值：secrets 数据自包含零 IO → 默认开；skill 涉及文件 IO 且多数部署无 skill 文件 → 默认关。
2. **零运行时依赖**：`extract_host` 用标准库，不引入 tldextract
3. **双层静默**：(a) 目录不存在 → loader 返回空串；(b) 开关关闭 → `_prepare_context` 直接置 None，连 `extract_host` 都不调、零 IO。两层都保证「skill 缺席不影响 agent」。
4. **缓存避免每步 IO**：`SkillLoader._cache` 按 host 缓存，host 不变时复用
5. **与 TreeForge 对齐**：`domain-skills/<host>/{_sop,selectors,quirks}.md` 路径约定和 TreeForge `adapters/treewalker_adapter.py` 输出一致
6. **复用现成范式**：完全模仿 `_build_sensitive_description` 的「URL 驱动 → 可选字符串 → state message kwarg」模式，零新生命周期管理

---

## 七、明确不做

- **不引入 tldextract**：先按完整 hostname，后续按需聚合
- **不改 system_prompt**：skill 进 state message 的 `[Domain Skill]` 段，不进 system_prompt 常量
- **不用 TYPE_CONTEXT 槽位**：那是给瞬时 nudge 的，skill 是持久上下文
- **不做 skill 失效检测**：P1 先验证「有 skill 提升成功率」，失效检测（selector 过时标记）留 P2+
- **不做结构化 selector 库**：skill 给 LLM 看自然语言描述，不做「给 CDP 直接执行」的结构化 selector（与「给 LLM 看」定位冲突，且精度要求与 record-replay 同档）
- **不调整默认值为开**：在第九节 A/B 实测验证 skill 注入有正向收益、且 `domain-skills/` 目录在仓库中常态化之前，`enable_skill_injection` 默认值保持 `False`。翻转默认值需另起决策（涉及所有未显式配置的部署的行为变更）。

---

## 八、测试设计

### `tests/test_skills_loader.py`（loader 单测）

- `load_for_host` 三文件齐全 → 拼接为 `[SOP]/[SELECTORS]/[QUIRKS]` 段
- 部分文件缺失 → 只拼存在的
- host 目录不存在 → 空串（静默）
- host 为 None/空 → 空串
- 第二次调同 host 走缓存（改文件后不刷新验证缓存生效）
- `invalidate(host)` 清单个 host 缓存
- `invalidate()` 清全部
- 空内容文件被跳过
- 多 host 各自缓存互不污染
- 渲染顺序固定为 SOP → SELECTORS → QUIRKS → API（不论文件系统顺序）

> loader 单测**不**测开关——开关是 agent 层职责（见 `test_agent_skill_injection.py` 第四组），loader 永远可独立工作。

### `tests/test_agent_skill_injection.py`（注入单测）

四组：
- **`extract_host`**：正常 URL / http / 空 / None / 无效字符串
- **`build_state_message` 的 `[Domain Skill]` 段**：有 skill 时渲染 / 无 skill 时不渲染 / 位置在 `[Task]` 后 `[Available Secrets]` 前
- **`_build_skill_description`**（mock SkillLoader）：匹配 host 返回 skill / 不匹配返回 None / loader 缺失返回 None / 不同 host 拿不同 skill
- **开关门控**（核心，验证默认关闭）：
  - `AgentSettings()` 默认实例断言 `enable_skill_injection is False`（保护默认值不被误改）
  - env 仅 `"true"`/`"True"`/`"TRUE"` 开启；`"1"`/`"yes"`/`"on"`/`"False"`/空串 → 均 `False`
  - **开关关 + skill 文件存在 + loader 可加载 → `_prepare_context` 传出的 `skill_description is None`**（核心断言：门控优先于数据可用性；可选附加：单独调 `_skill_loader.load_for_host(host)` 仍返回非空，证明「是开关拦截不是 loader 坏」）
  - 开关关 → spy `_skill_loader.load_for_host` 断言 **0 次调用**（门控在调用点短路，零 IO）
  - 开关开 + skill 文件存在 → `skill_description` 非空，state message 含 `[Domain Skill]` 段
  - 开关开 + skill 文件缺失 → 静默 `None`（与双层静默第 (a) 层对齐）
  - 构造传参 `AgentSettings(enable_skill_injection=True)` 与 env `AGENT_ENABLE_SKILL_INJECTION=true` 加载结果在该字段相等

---

## 九、验证路径（A/B 实测）

代码机制就绪后，核心判据是 ROADMAP P1 的「有 skill 的 agent 探索成功率显著高于无 skill」：

0. **开启开关**（A/B 前置，默认关闭必须显式开）：
   - 方式 a（推荐，隔离干净）：构造传参 `AgentSettings(enable_skill_injection=True, skills_dir="domain-skills")`
   - 方式 b（运行时）：`AGENT_ENABLE_SKILL_INJECTION=true`（及可选 `AGENT_SKILLS_DIR=...`）
   - 不开启则整个验证流无法触发注入（见第三节改动 3）

1. **手写 skill**（不依赖 TreeForge 代码）：基于 record-replay 积累的 B 站知识，手写 `domain-skills/www.bilibili.com/{selectors.md, quirks.md}`
2. **A/B 实测**：TreeWalker agent 跑 B 站上传各 N 次——
   - **baseline 组**：开关关（默认）——即使 `domain-skills/` 存在也不注入
   - **treatment 组**：开关开 + `domain-skills/www.bilibili.com/` 就位
3. **判据**：成功率提升 ≥ 20pp 或步数减少 ≥ 30%
4. **若达标**：固化方案，作为 TreeForge P2 采集层的验收标准
5. **若不达标**：分析原因（skill 格式 / 注入位置 / LLM 没用上），调整后再验

---

## 十、与现有文档的关系

- **TreeWalker ROADMAP P1**：本文是 P1 的工程实施依据
- **TreeForge ROADMAP P1**：和本文是同一件事的两端（TreeForge 产 skill，TreeWalker 注入）
- **[[manual-vs-agent-recording]]**：战略转折记录，本文是转折后的核心方向落地
- **`docs/user_recording/recorder-timing-solutions.md`**：record-replay 的时序劣势分析，skill 注入是绕开该问题的替代路线
- **TreeForge `init-plan.md` §五**：skill 输出形态的定义，本文消费侧取 `_sop`/`selectors`/`quirks` 三件套（`api.md` 已弃用——URL 信息并入 quirks 或靠 DOM `[Current URL]`）
