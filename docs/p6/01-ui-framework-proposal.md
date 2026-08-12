# P6 界面框架提案（UI Framework Proposal）

> 状态：**已实施**（M1-M5 + M6 前半，2026-08-12 真机 e2e 通过）。本文为 IA 框架定调；实施见 [`02`](02-implementation-plan.md)、复盘见 [`04`](04-implementation-retrospective.md)、后续 backlog 见 [`05`](05-followup-plan.md)。注：TUI 去留（issue #162 决策 Q2，原「下掉」）已修订为**并存保留**（见 §0 / `04` §一·1）。
> 关联：ROADMAP P6 / issue #162。
> 目的：在写代码前先把「整个 web 前端的功能布局」讨论清楚，使后续功能扩展有处可放。

---

## 0. 背景

P6 已决策（2026-08-11，见 issue #162）：把 TUI（`tui/`）的能力迁到浏览器端 web，统一交互入口。**TUI 并存保留**（`tw-tui` + `tw-web` 共存，web 为主）——原计划「迁移完下掉 TUI」，2026-08-12 修订为并存（见 `04` §一·1）。范围 = live agent 控制台 + 统一现有 `history_editor` 入口，**不含手工录制**（Chrome 扩展，迁 TreeForge）。

本提案调研 OpenClaw / hermes-agent / browser-use webui / Skyvern 等成熟 agent 交付界面，结合 TreeWalker 的定位与实际能力，给出 web 前端的**信息架构与界面框架**，供讨论定调。

---

## 1. 调研提炼

| 参考 | 布局要点 | 对 TreeWalker 的启发 |
|---|---|---|
| **OpenClaw** Control UI | 左侧栏（agent 身份 + 页面 + 会话分区 Threads/Groups/Coding + 底栏）；中心聊天 + 工作区 rail + 会话 rail（实时摘要/计划进度/PR）+ 后台任务 rail + **浏览器面板**（渲染 agent 驱动的浏览器，带标注）；⌘K 命令面板；WebSocket | 「会话 rail 显示计划进度/实时摘要」「浏览器面板带标注」「后台任务 rail」——透明度做得好 |
| **hermes-agent** WebUI | 三栏：左=会话（项目/标签/置顶/归档/搜索）；中=流式聊天 + **工具调用卡 + 子 agent 卡 + thinking 卡 + 审批卡** + composer 底栏（模型/profile/workspace + 上下文环 + Stop）；右=工作区文件树；顶部 tabs：Chat/Tasks/Skills/Memory/Profiles/Todos | 「工具调用卡/thinking 卡/审批卡」可视化 agent 每步；「上下文环」；tab 化的多功能面 |
| **browser-use webui** | Gradio + **VNC（`/vnc.html`）看实时浏览器**；持久化浏览器会话 | 同源项目——**实时浏览器视图是必备**；但它 Gradio 不适合复杂交付（TreeWalker 已选 React） |
| **Skyvern** | localhost:8080；**Task（URL+prompt+data schema）+ 无代码 Workflow Builder（11+ block）+ 直播视口 + 结果展示 + 保存/周期工作流** | 「Task/Workflow 作为一等资产」「直播视口调试」——最贴近 TreeWalker 的「流程资产 + 批量/重放」 |

**共性模式**：左导航/会话 + 中心工作区 + 右上下文；agent 每步要可视化（卡片/时间线）；实时浏览器视图是浏览器自动化的标配；多功能用顶部 tab 分。

---

## 2. TreeWalker 能力 → 界面映射

TreeWalker 要承载的能力（实际代码位置）：

| 能力（代码位置） | 界面落点 |
|---|---|
| 跑新 agent 任务（`Agent.run`） | **运行视图**：任务输入 + 实时步骤 + 浏览器视图 |
| 实时日志/事件（`EventBus`/`_obs_bus`） | 运行视图：步骤时间线 + 日志流 |
| 运行控制（`pause/resume/stop`） | 运行视图顶栏按钮 |
| agent 轨迹录制（`record-switch` → `save_history`） | 运行视图：录制开关，结束存为 Flow |
| 历史编辑（`history_editor`：action list/editor + 变量面板） | **编辑视图**（现有 React 资产，折叠进来） |
| 单次试跑 / CSV 批量（`batch_rerun` + SSE） | **重放视图**：试跑 + 批量进度 |
| 历史管理（`rerun_history_dir`） | **侧栏流程库** |
| skill 注入（`domain-skills/<host>/`） | 运行视图「活动技能」标示 + **技能面**（查看/编辑） |
| DOM 快照（`element_tree_text`） | 右侧上下文：每步 DOM 快照（透明度） |
| 配置（`AgentSettings`/LLM/CDP） | **设置面** |

**中心洞察**：这些能力都围绕一个对象——**「流程/Flow」**（一条 `AgentHistory`）：探索时**生成**它、编辑时**改**它、重放时**跑**它。所以 IA 用「Flow 为中心」，而不是照搬 OpenClaw/hermes 的「会话为中心」。

> ⚠️ 与 TUI 的本质体验差：TUI 把「跑」和「存历史」割裂（跑完手动 `/rerun` 或开开关）；web 用 Flow 把它们连成一条链——探索生成 Flow → 编辑 → 重放/批量，全程同一对象。

---

## 3. 信息架构提案

### 3.1 App Shell（三栏 + 顶部模式切换）

```
┌────────────────────────────────────────────────────────────────┐
│ TopBar  [TreeWalker]   探索·流程库·技能·设置   [模型▾] [⌘K]     │
├────────────┬───────────────────────────────────┬───────────────┤
│ 左 Sidebar │  中心：Flow Workspace             │ 右 Context    │
│            │                                   │ （可折叠）     │
│ ▼ 进行中    │  [运行][编辑][重放][详情] ←子视图  │  选中步骤/动作 │
│  ·封面任务●│ ───────────────────────────────  │  的详情：      │
│ ▼ 流程库    │                                   │  ·DOM 快照     │
│  ·抖音上传 │  (内容随子视图 tab 变)             │  ·提取内容     │
│  ·B站发视频│                                   │  ·元素定位线索  │
│  ·搜索×3   │                                   │  ·元数据       │
│  ...       │                                   │                │
│ [+ 新探索]  │                                   │                │
│ [模板]      │                                   │                │
├────────────┴───────────────────────────────────┴───────────────┤
│  （运行视图时）任务输入栏 + 文件路径 + [录制 ○] [发送]            │
└────────────────────────────────────────────────────────────────┘
```

- **左 Sidebar**：上半「进行中」（活跃 agent 运行，置顶、带状态点）+ 下半「流程库」（已存 `AgentHistory`）。新建探索 = 产生一个「进行中」Flow，录制保存后落入「流程库」。→ 统一「跑」和「存」。
- **顶部模式**：探索 / 流程库（默认，即 Flow 工作区）/ 技能 / 设置。非 Flow 的独立面（技能、设置）单独走顶部 tab，不挤进侧栏。
- **右 Context**：跟随中心选中项（某步/某动作）显示详情，可折叠给中心让位。

### 3.2 中心 Flow Workspace —— 四个子视图（tab）

核心扩展点。每个 Flow（无论进行中还是已存）都能切这四个视图（**四视图均为首期范围**，见 §5）：

#### ① 运行（Run）— live agent 控制台（P6 首期核心，TUI 独有能力 web 化）

```
┌─────────────────────────────────────────────┐
│ 任务：帮我把视频上传到B站创作中心            │
│ [▶ 运行中] [● 录制]   [⏸ 暂停] [⏹ 停止]      │
├────────────────────┬────────────────────────┤
│ 实时浏览器视图       │ Agent 步骤时间线        │
│ （截图流 / 直播视口）│ ▸ step1 [思考] + click  │
│  带元素高亮标注      │ ▸ step2 input "..."    │
│                    │ ▸ step3 select ▾ (…中)  │
│                    │ ──────────────────────  │
│                    │ 🔧 活动技能: B站 skill   │
│                    │ 📊 token/耗时（上下文环） │
├────────────────────┴────────────────────────┤
│ 日志流（可折叠/过滤/级别）                    │
└─────────────────────────────────────────────┘
```

#### ② 编辑（Edit）— 现有 `web_ui` 折叠进来

action list + action editor + 变量面板（#149/#153）。已存 Flow 默认进这里。**零重写**，作为子视图嵌入。

#### ③ 重放（Replay）— 单次试跑 + CSV 批量

单次试跑（RunPanel）+ CSV 批量（#155 的 SSE 步级进度 + 中止），结果可视化。

#### ④ 详情（Detail）— Flow 元信息与透明度

步数/成功率、用了哪个 skill、每步 DOM 快照回放（对齐 P7「看 agent 在干嘛」）。

---

## 4. 关键设计取舍 & 扩展性

### 4.1 取舍

| 决策 | 选择 | 理由 |
|---|---|---|
| IA 中心 | **Flow（资产）为中心**，非会话为中心 | TreeWalker 的探索/编辑/重放都围绕 AgentHistory；会话模型（OpenClaw/hermes）不适合「录一次跑多次」 |
| 与现有 web 资产 | **扩展现有 `web_ui`**（React + reducer + aiohttp + SSE），不重写 | 已验证技术栈；编辑/批量视图已是现成子视图 |
| 实时浏览器视图 | **截图流为主、直播视口为可选增强** | TreeWalker 原生 CDP（非 Playwright/VNC），截图已在 AgentHistory；直播视口（Skyvern/browser-use 式）工作量大，可后置 |
| 顶部模式 vs 全侧栏 | 顶部 tab（探索/流程库/技能/设置）+ 侧栏只放 Flow 列表 | 避免侧栏臃肿；功能域分离清晰 |
| 进行中/已存 Flow 是否合并到一个列表 | **合并，分两区**（进行中 / 流程库） | 运行结束（录制开）自然落库，链路连续 |

### 4.2 扩展性插槽与落地机制（核心诉求：后面好扩展）

**三个让插槽「可落地」的机制（无论首期范围都做）：**

1. **注册表驱动的 shell**：顶部模式 `modes: [{id,label,icon,component,enabled}]`、Flow Workspace 的 tab `flowTabs: [...]` 都是列表。技能/设置首期是 `enabled:false` 两条，后期改 `true` 即出现，**不改 shell**；未来「编排/Workflow」tab 加一条即可。
2. **可换的 `<BrowserView mode>`**（给直播视口留的核心缝）：
   - 首期 `mode='screenshots'`：订阅 agent 每步截图（AgentHistory 已抓截图，SSE 推帧）
   - 后期 `mode='livestream'`：接直播视口端点（CDP `Page.startScreencast` 或独立推流）
   - **元素高亮标注层独立于 mode**（叠在 BrowserView 上）——两种 mode 都能用，先在截图流做好，直播视口直接复用，不返工
   - 控件簇（全屏/标注开关/切源）预留「切换直播」位
3. **技能/设置统一「分栏 shell」**（左子导航 + 右内容，子页注册表驱动）；运行视图「活动技能」标示先做只读 +「查看技能」占位（首期禁用，后期点了进技能面）

**预留插槽（后期填）：**
- Flow Workspace 新 tab：`编排/Workflow`（Skyvern 式工作流 builder、多任务串联）
- 侧栏新区：`模板/Templates`、`定时/Schedules`（hermes/OpenClaw 式 cron）
- 右 Context 可插拔：不同选中类型渲染不同详情（步/动作/元素/批量行）
- Flow 抽象本身是扩展点：新能力（多 agent、下载管理、结果导出）都挂在 Flow 上，不改 shell

---

## 5. 已定决策（2026-08-11）

| # | 决策点 | 结论 |
|---|---|---|
| 1 | IA 中心 | **Flow 为中心**（非会话为中心） |
| 2 | 实时浏览器视图 | **首期截图流，直播视口后置**——但 `<BrowserView mode>` 可换组件 + 标注层独立，直播视口后期接入不返工（见 §4.2） |
| 3 | 编辑/批量是否首期折叠 | **首期全折叠**——Flow Workspace 含 运行/编辑/重放/详情 全 tab，一步消除「两个 web 前端」并存期 |
| 4 | 技能/设置面 | **首期后置**——首期面 = 探索 + 流程库；技能/设置作为注册表里 `disabled` 的顶部模式，后期 `enabled` 即出现 |

**首期范围（锁定）**：App Shell + Flow Workspace 全四子视图（运行/编辑/重放/详情）+ 流程库侧栏；实时浏览器视图用截图流；技能/设置为预留位（`disabled`）。

**后置扩展项**：直播视口（`<BrowserView mode='livestream'>`）、编排/Workflow、模板/定时、多 agent 等。

---

## 6. 参考

- [OpenClaw](https://openclaw.ai/) · [OpenClaw Control UI 文档](https://docs.openclaw.ai/web/control-ui)
- [hermes WebUI（nesquena/hermes-webui）](https://github.com/nesquena/hermes-webui)
- [browser-use web-ui](https://github.com/browser-use/web-ui)
- [Skyvern（Skyvern-AI/skyvern）](https://github.com/Skyvern-AI/skyvern)
