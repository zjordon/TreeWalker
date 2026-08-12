# P6 后续功能实施计划（01 提到但未实现项）

> 范围：`01-ui-framework-proposal.md` 提到、但首期（M1-M5 + M6 前半）**未实现 / 已后置**的功能，整理成可跟进的 backlog。
> 当前状态：web live 控制台 + 流程库（编辑/重放/详情）已交付并真机 e2e 通过（见 `04-implementation-retrospective.md`）；TUI 并存保留。
> 本文不含已实现项；每项标 01 来源、现状、阻塞、工作量与分期。

---

## 0. 总览与分期

| # | 功能 | 01 来源 | 现状 | 阻塞 | 工作量 | 分期 |
|---|---|---|---|---|---|---|
| A | 直播视口（livestream） | §4.2 机制2 / §5 | 仅截图流；livestream 占位 | 无 | 中 | **T1** |
| B | 技能面（Skills） | §2 / §4.2 机制3 | 探索模式无活动技能标示；技能面 disabled 占位 | 无（SkillLoader 就绪） | 中 | **T1** |
| I | Run 视图增强（活动技能标示 + token/耗时环 + 元素高亮标注 + ⌘K + 模型选择 + 任务历史） | §3.1/§3.2① | 多数缺；标注层仅占位文本 | 无 | 小～中 | **T1** |
| C | 设置面（Settings） | §2 / §4.2 / §5 | disabled 占位；配置靠 .env | 无 | 中 | T2 |
| G | 右 Context 面板（shell 级，可插拔） | §3.1 / §4.2 | 各模式局部 context；无统一右栏 | 无 | 中 | T2 |
| H | 侧栏「进行中」zone（live tasks） | §3.1 | sidebar 仅流程库；live run 在探索模式 | 无 | 中 | T2 |
| D | DOM 快照回放（详情每步） | §2 / §3.2④ / §4.2 | Detail 用 state_summary 顶上 | ⚠️ element_tree_text 未持久化 | 大 | **T3**（阻塞） |
| E | 编排 / Workflow tab | §4.2 / §5 | 无 | 编排语义待定 | 大 | T3 |
| F | 模板 / 定时（sidebar 新区） | §3.1 / §4.2 / §5 | 无 | 定时需调度器 | 中 | T3 |
| J | Flow 扩展能力（多 agent / 下载管理 / 结果导出） | §4.2 | 无 | 视项 | 大 | T3 |

**T1 = 近期（高价值/用户明确想要/小而快，无阻塞）**；**T2 = 中期（便利/统一，无阻塞）**；**T3 = 远期/阻塞（需先决策或解阻塞）**。

---

## 1. T1 — 近期

### A. 直播视口（`<BrowserView mode='livestream'>`）
- **现状**：运行视图用截图流（每步一帧）；`BrowserView` 的 `mode` 已留 livestream 占位。
- **方案**：**走 agent 侧** CDP `Page.startScreencast`（按帧率推流，~1-4 fps 可配），避开 web 层异步采集与浏览器生命周期的 race（`04` 复盘的截图坑教训）。后端新增 screencast 推帧端点（或复用 `/task/events` 加 `screenshot` 高频帧）；前端 `BrowserView mode` 切换，标注层复用。
- **依赖**：无（CDP 原生）。
- **开放问题**：帧率/带宽控制（降采样 + 仅最新帧 + 前端不可见不推）；与截图流共存还是替代。

### B. 技能面（Skills）
- **现状**：探索模式无「活动技能」标示；顶部「技能」disabled 占位。
- **方案**：分栏 shell（左 host 列表 + 右 skill 编辑：`domain-skills/<host>/` 的 `_sop`/`selectors`/`quirks` 三文件查看/编辑/保存）。后端加 `/skills/*` 端点（list/get/put）。RunView「活动技能」标示从无 → 只读显示当前 host 的 skill → 可点击进技能面。
- **依赖**：skill 注入机制（`SkillLoader`）已就绪；三文件格式已定。
- **价值**：skill 是 agent 探索成功率的关键杠杆（A/B：精简版 100% vs 无 80%）；可视化编辑降低 skill 维护门槛。
- **开放问题**：编辑后是否热更新 SkillLoader 缓存（invalidate）。

### I. Run 视图增强（一批小项）
- **I1 活动技能标示**：RunView 顶部/侧显示当前任务 URL 对应的 host skill（只读，先不做点击进技能面）。
- **I2 token / 耗时上下文环**：累计 input/output tokens + 运行时长，环形进度（hermes 式）。需后端 `ModelResultEvent` 已带 `input_tokens`/`output_tokens`，累加即可。
- **I3 元素高亮标注层**：`BrowserView` 上叠一层，高亮当前 `tool_call` 的目标元素（边框/序号）。需后端在 `tool_call` 事件里带元素定位信息（如 `interacted_element` 的 bbox 或 index）。
- **I4 命令面板 ⌘K**：快速搜索/跳转（流程、任务模板、动作）。中等工作量，可后置到 T2。
- **I5 模型选择器**：TopBar 切换 LLM 模型（影响新任务）。需后端按任务指定 model。
- **I6 任务历史导航**：RunView 输入框 up/down 历史 + 落盘（同 TUI 的 `~/.treewalker/history.json`）。
- **依赖**：I1/I2 无；I3 需事件带元素 bbox；I5 需后端 per-task model。

---

## 2. T2 — 中期（便利 / 统一）

### C. 设置面（Settings）
- **现状**：disabled 占位；配置全靠 `.env`。
- **方案**：分栏 shell，分区 LLM / Browser / Agent / 高级（注册表驱动）；后端 `/settings/get` + `/settings/set`（写回 `.env` 或内存 override）。敏感字段（API key）脱敏显示。
- **开放问题**：写回 `.env` 的安全/并发；运行中改配置的生效范围。

### G. 右 Context 面板（shell 级）
- **现状**：编辑 tab 右侧有 ActionEditor/VariablePanel（局部）；详情有 master-detail（局部）；Run 视图选中步骤无右栏。
- **方案**：把右 Context 提升为 shell 级可折叠面板，跨模式跟随选中项（RunView 步骤 → 该步详情/DOM；编辑动作 → ActionEditor；批量行 → 行结果）。
- **价值**：统一选中→详情交互；为 DOM 快照回放（D）铺路。
- **开放问题**：与各模式局部右栏合并的迁移成本。

### H. 侧栏「进行中」zone
- **现状**：FlowWorkspace sidebar 仅「流程库」；live run 在探索模式（独立顶部 mode），不在 sidebar。
- **方案**：sidebar 加「进行中」区（活跃/最近 live task 列表，带状态点）；点进 = 回到该 RunView。或把 live task 也视作一种 Flow，统一进 sidebar。
- **开放问题**：live task 列表是否持久化（刷新后恢复）、与单槽并发的关系。

---

## 3. T3 — 远期 / 阻塞

### D. DOM 快照回放（详情每步）
- **现状**：`AgentHistory` 不持久化 `element_tree_text`（视觉通道阶段二阻塞）；Detail 用 `state_summary` 顶上。
- **方案**：先后端打通每步 `element_tree_text` 持久化（history schema 扩展 + 存储决策：全量存 vs 按需存 vs 单独 store），再在 Detail 渲染每步 DOM 快照（可滚动/可搜索/可 diff）。
- **阻塞**：⚠️ 依赖 dom-snapshot 持久化方案（与视觉通道阶段二、history schema 协调）。**先记录集成点，等后端决策。**

### E. 编排 / Workflow tab
- **现状**：无；Flow Workspace 仅 运行/编辑/重放/详情。
- **方案**：Skyvern 式无代码 workflow builder（Browser Task / 数据提取 / For 循环 / HTTP / 文件 等块串联）；或先做轻量「任务序列」（多个 Flow 顺序/并行）。注册表加 `编排` tab。
- **开放问题**：编排语义（与单 Flow 重放/CSV 批量的边界）；执行引擎（独立于 agent.run？）。

### F. 模板 / 定时
- **模板 Templates**：常用任务文本/Flow 模板，sidebar 新区，一键新建探索/批量。
- **定时 Schedules**：cron 触发 Flow 重放/批量；需后端调度器（长期运行进程或外部 cron + webhook）。
- **开放问题**：调度器部署形态（tw-web 常驻？独立 worker？）。

### J. Flow 扩展能力
- **多 agent**：一个任务多浏览器并发（对齐 `examples/parallel_agents.py`）；需多 CDP 端口 + 多 BrowserSession + UI 多视图。
- **下载管理**：agent 下载文件的可视化列表/打开。
- **结果导出**：`extract` 结果导出 CSV/JSON/剪贴板。

---

## 4. 与 ROADMAP 主线的关系

- **B 技能面 / I1 活动技能** 与 skill 注入机制（已落地）正向循环，可独立推进。
- **D DOM 快照回放** 受阻于视觉通道阶段二 / dom-snapshot 持久化——非 P6 能单独解。
- **E 编排 / J 多 agent** 属战略级，建议等 **P2（探索可靠性）** 与 **P7（WebArena 基准）** 出结论后，按短板反哺再定优先级（编排/多 agent 的 ROI 取决于单 agent 成功率）。
- **A 直播视口 / C 设置面 / G 右 Context / H 进行中 zone / I 增强** 均无外部阻塞，按用户需求排期即可。

---

## 5. 建议切入点

若继续推进 P6 后续，建议顺序：**B 技能面 → I（Run 增强，尤其 I1/I2/I3）→ A 直播视口**（T1，高价值或用户明确想要，无阻塞），再视需要进 T2。T3 待解阻塞或主线出结论。
