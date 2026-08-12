# P6 实施方案（Implementation Plan）

> 状态：**方案已定**（2026-08-11）。IA + 实施细节均已锁，可进入实施（M1 起）。
> 关联：issue #162 / ROADMAP P6。
> 前置：扩展现有 `web_ui`（React + reducer）+ `history_editor/server.py`（aiohttp，端口 8766），非重写。

---

## 0. 范围回顾（来自 01）

- IA = **Flow 为中心**；首期 = App Shell + Flow Workspace **全四视图**（运行/编辑/重放/详情）+ 流程库侧栏。
- 实时视图 = **截图流**（直播视口后置，留 `<BrowserView mode>` 缝）。
- 技能/设置 = 首期后置（注册表 `disabled`）。
- 复用现有：编辑/批量/试跑的 React 组件与 `/history/*` 端点已是现成资产。

---

## 1. 架构总览

```
┌──────────────────────────────────────────────────────────────┐
│ 前端 SPA（web_ui，React + reducer）                │
│  App Shell（注册表驱动 modes）→ Flow Workspace（注册表 flowTabs）│
│   ├ Run 视图：任务输入 + <BrowserView mode='screenshots'>     │
│   │           + 步骤时间线 + 日志流 + 控制按钮 + 录制开关       │
│   ├ Edit 视图：现 ActionList/ActionEditor/VariablePanel（折叠）│
│   ├ Replay 视图：现 RunPanel/BatchRunPanel（折叠）            │
│  └ Detail 视图：元信息 + 每步 DOM 快照                         │
└───────────────┬──────────────────────────────────────────────┘
                │ REST + SSE（同源 / Vite proxy）
┌───────────────▼──────────────────────────────────────────────┐
│ 后端 aiohttp（history_editor/server.py，扩 make_app）          │
│  现有 /history/*（list/load/save/detect/rerun/batch）不变     │
│  新增 /task/*（live agent 控制台）：                          │
│   POST /task/start · GET /task/events(SSE) ·                  │
│   POST /task/{pause,resume,stop} ·（截图经 events 流推帧）     │
│  LiveTaskHandle（仿 BatchTaskHandle：agent + Queue + 任务态）  │
└───────────────┬──────────────────────────────────────────────┘
                │ Agent.run（asyncio.create_task）+ 订阅 _obs_bus
┌───────────────▼──────────────────────────────────────────────┐
│ Agent / BrowserSession / EventBus（零改动复用）               │
│  agent.run/pause/resume/stop · _obs_bus（8 类事件）·           │
│  browser.take_screenshot（CDP Page.captureScreenshot）        │
└──────────────────────────────────────────────────────────────┘
```

**复用要点**：
- `BatchTaskHandle` + SSE + 协作中止（`_handle_batch_progress`/`_handle_batch_cancel`）是 live task 的直接模板。
- `EventBridge`（TUI 把 `_obs_bus` 事件格式化进 RichLog）是「EventBus → 前端」的模板——web 版把事件序列化进 Queue 而非 RichLog。
- `_build_agent()`（server.py 现有，构造带 BrowserSession 的 Agent）复用；live task 只是把空 task 换成真实任务、开 EventBus 订阅。

---

## 2. 后端设计

### 2.1 模块布局

在 `history_editor/server.py` 的 `make_app` 注册新路由组 `/task/*`（不新建服务、不新开端口，统一入口）。新增一个 `LiveTaskHandle`（与 `BatchTaskHandle` 并列，模块级 dict `_LIVE_TASKS`）。

> 单并发约束：与 batch 共享「同 Chrome 单 BrowserSession 抢 CDP target」的限制。**live task 与 batch 共享一个并发槽、互斥**（§8 决策 1）——同一时刻仅一个 agent 跑。

### 2.2 LiveTaskHandle（仿 BatchTaskHandle）

```python
@dataclass
class LiveTaskHandle:
	agent: Agent                  # 跑探索的 agent（cancel 调 agent.stop()）
	queue: asyncio.Queue          # 事件流（agent 侧 put / SSE handler get）
	task: asyncio.Task | None = None
	final_event: dict | None = None
	record: bool = False          # 录制开关 → 结束调 agent.save_history
```

### 2.3 端点

| 方法 | 路径 | 作用 | 对应现有 |
|---|---|---|---|
| POST | `/task/start` | body: `{task, file_paths?, record?}` → 建 Agent → `create_task(agent.run)` → 返 `{task_id}` | 仿 `_handle_batch_start` |
| GET | `/task/events` | SSE：`step_start/model_call/model_result/tool_call/tool_result/step_end/screenshot/log/done` | 仿 `_handle_batch_progress` |
| POST | `/task/pause` | `agent.pause()` | — |
| POST | `/task/resume` | `agent.resume()` | — |
| POST | `/task/stop` | `agent.stop()`（协作式，run 循环在边界退出） | 仿 `_handle_batch_cancel` |

`/task/events` 的 SSE 事件名直接复用 EventBus 的 `event_type`（前端按类型渲染卡片），额外加 `screenshot`/`log`/`done`。

### 2.4 EventBus → SSE 桥接（核心）

```python
# 启动 task 时
agent._setup_signal_handler = lambda: None   # 同 TUI：web 用端点控制，不走信号
def on_event(event: BaseEvent):
	agent_task.queue.put_nowait({
		"type": event.event_type,
		**event.model_dump(mode="json"),
	})
agent._obs_bus.subscribe("*", on_event)
```

SSE handler 从 queue 取事件 → `_sse_event(type, data)` → 写响应（仿 `_handle_batch_progress` 的 keepalive + 重连补发 `final_event`）。

> EventBus 8 类事件（`StepStart/ModelCall/ModelResult/ToolCall/ToolResult/StepEnd/Anomaly/SessionEnd`）字段已在 `observability/events.py` 定义，前端可直接结构化渲染（思考卡 / 工具调用卡 / 结果卡，对齐 hermes 的卡片模式）。

### 2.5 日志事件化（唯一需专门做的点）

现状：`logging` → `RichLogHandler`（TUI 专用）。web 抓不到。

方案：起 task 时给 `tree_walker` logger 挂一个自定义 `logging.Handler`，`emit()` 把 record 转成 `{type:"log", level, msg, logger}` `put_nowait` 进同一个 queue。任务结束摘除 handler。

```python
class _SseLogHandler(logging.Handler):
	def __init__(self, queue): super().__init__(); self._q = queue
	def emit(self, record):
		self._q.put_nowait({"type": "log", "level": record.levelname,
		                    "msg": self.format(record), "logger": record.name})
```

→ 前端日志流面板订阅 `log` 事件，可按 level/logger 过滤。**不抓 stdout，纯事件化**。

### 2.6 截图推帧（`<BrowserView mode='screenshots'>` 的后端）

现状：`browser.take_screenshot()`（CDP `Page.captureScreenshot`）每步 `get_state` 都抓，但**不写入 history**（`views.py:116` 视觉通道阶段二阻塞）——与持久化无关，正好用于实时推帧。

方案：给 `_obs_bus` 加一个 `ScreenshotEvent`（或在 `StepEndEvent` 后由 step 流程 emit），载荷 = 截图 bytes（或先存盘传 path，避免 SSE 单帧过大）。SSE 推 `{type:"screenshot", step, data}`。

- **带宽**：仅保留「最新帧」（新帧覆盖旧帧），或按需（前端可见才推）。截图降采样复用 `browser/image_utils.py:resize_screenshot_bytes`。
- **直播视口后置**：`<BrowserView mode>` 接口不变，后期 `mode='livestream'` 换成 `Page.startScreencast` 推流，前端组件零改动。

### 2.7 Flow 持久化（录制）

`record=True` 时，run 结束（或 stop）后调 `agent.save_history(name)`（TUI `_maybe_save_recording` 同款）→ 落 `rerun_history_dir` → 前端侧栏「流程库」刷新即见。

---

## 3. 前端设计

### 3.1 App Shell（注册表驱动）

```ts
// 注册表——技能/设置首期 enabled:false，后期改 true 即出现
const MODES: Mode[] = [
	{ id: "explore",  label: "探索",   icon: "compass",  component: FlowWorkspace, enabled: true },
	{ id: "flows",    label: "流程库", icon: "folder",   component: FlowLibrary,   enabled: true },
	{ id: "skills",   label: "技能",   icon: "wrench",   component: SkillsShell,   enabled: false }, // 后置
	{ id: "settings", label: "设置",   icon: "gear",     component: SettingsShell, enabled: false }, // 后置
];
```

- 左 Sidebar：`进行中`（活跃 task，状态点）+ `流程库`（已存 history，复用 `listFiles`）。`[+ 新探索]` → 进 Run 视图空态。
- 右 Context：跟随选中项（步/动作/批量行）渲染，可折叠。

### 3.2 Flow Workspace（注册表驱动 tab）

```ts
const FLOW_TABS: FlowTab[] = [
	{ id: "run",     label: "运行", component: RunView },     // 新建：live 控制台
	{ id: "edit",    label: "编辑", component: EditView },    // 现 ActionList/ActionEditor/VariablePanel
	{ id: "replay",  label: "重放", component: ReplayView },  // 现 RunPanel/BatchRunPanel
	{ id: "detail",  label: "详情", component: DetailView },  // 新建：元信息 + 每步 DOM 快照
];
// 未来 { id: "orchestrate", ... } 加一条即可
```

**折叠策略（零重写）**：`EditView`/`ReplayView` 直接包裹现有组件，state 从全局 shell 的「选中 Flow」取，替代现在 App.tsx 里的局部 state。

### 3.3 Run 视图（新建，P6 核心）

```
┌──────────────────────────────────────────────────┐
│ 任务输入 + 文件路径        [录制 ○] [▶ 发送]      │
│ [⏸ 暂停][⏹ 停止]  状态: 运行中 step 3             │
├──────────────────────┬───────────────────────────┤
│ <BrowserView mode=   │ 步骤时间线（事件卡）        │
│  'screenshots'>       │ ▸ step1 [思考]+click ✏️选中 │
│  + 标注层（高亮元素）  │ ▸ step2 input "..."        │
│  控件: 全屏/标注/切源  │ ▸ step3 select ▾ …         │
│                      │ 🔧 活动技能: B站            │
├──────────────────────┴───────────────────────────┤
│ 日志流（level 过滤，虚拟滚动）                     │
└──────────────────────────────────────────────────┘
```

- 订阅 `subscribeTaskEvents(taskId, handlers)`（EventSource `/task/events`），按 `type` 分发到时间线 / BrowserView / 日志。
- 控制按钮 → `pauseTask/resumeTask/stopTask`（POST）。

### 3.4 `<BrowserView mode>` + 标注层（直播视口留缝）

```ts
interface BrowserViewProps { mode: "screenshots" | "livestream"; taskId: string; }
// screenshots: 订阅 screenshot 事件，渲染最新帧
// livestream: 后期接推流端点（同 props，换数据源）
// 标注层 <AnnotationOverlay>: 独立于 mode，叠在 BrowserView 上，高亮当前 tool_call 的目标元素
```

**标注层独立于 mode 是关键**——先在截图流上做好「看 agent 点哪」，直播视口后期直接复用，不返工。

### 3.5 状态管理

- 新增 **shell 级 state**（selectedFlow、activeMode、activeTab、liveRuns[]），独立 reducer。
- 现 `reducer.ts`（EditorState）**降级为 Edit 视图的局部 state**，不动其逻辑（零回归）。

### 3.6 api.ts 扩展

```ts
startTask(task, filePaths?, record?) → { task_id }
subscribeTaskEvents(taskId, onEvent) → EventSource   // 复用 subscribeBatchProgress 模式
pauseTask/resumeTask/stopTask(taskId)
// 现有 listFiles/loadHistory/saveHistory/detectVariables/rerun/startBatch/... 不变
```

---

## 4. CLI 一等化 & TUI 下线

- 现 editor 后端是 `examples/serve_history_editor.py`（非一等入口）。新增 CLI 子命令（如 `treewalker web [--host] [--port] [--cdp-port]`），`cli.py` 加 `serve` 子命令调用 `run_server`。
- TUI（`treewalker` 默认进 TUI）在 live 控制台验收通过后 deprecated → 移除 `tui/` + `cli.py` 的 TUI 分支（P6 收尾）。

---

## 5. 分阶段交付

| 里程碑 | 内容 | 验收 |
|---|---|---|
| **M1** 后端 live task | `LiveTaskHandle` + `/task/start` + `/task/events`(SSE，EventBus→Queue) + `/task/{pause,resume,stop}` | 单测：mock agent，事件入队 + SSE 格式；端点单测仿 `test_history_editor_server.py` |
| **M2** 日志事件化 + 截图推帧 | `_SseLogHandler` + `ScreenshotEvent` → 同一 SSE 流 | 单测：log handler emit 入队；截图降采样 |
| **M3** 前端 App Shell + Run 视图 | 注册表 shell + sidebar + Run 视图（输入/BrowserView 截图/时间线/日志/控制/录制）接 M1/M2 | 真机跑通一个真任务（看实时步骤+截图+日志，能暂停/停止/录制存盘） |
| **M4** 折叠 Edit/Replay | 现组件包成 Edit/Replay tab，从 sidebar 选 Flow 加载 | 现编辑/批量 e2e 零回归 |
| **M5** Detail 视图 + 右 Context + 技能/设置预留位 | Detail（元信息+每步 DOM 快照）+ Context 面板 + skills/settings `enabled:false` 注册 | DOM 快照回放可见；技能/设置入口禁用可见 |
| **M6** CLI 一等化 + TUI 下线 | `treewalker web` 子命令；TUI deprecated/移除 | `treewalker web` 起服务；TUI 移除后全量测试过 |

---

## 6. 测试策略

- **后端**：`LiveTaskHandle` 单测（mock agent.run + `_obs_bus`，断言事件→queue→SSE）；端点单测（start/events/pause/resume/stop，仿 `test_history_editor_server.py` 的 monkeypatch 套路）；日志 handler 单测。
- **前端**：shell 注册表 reducer 单测；Run 视图组件测（仿 `BatchRunPanel.test.tsx`，mock EventSource）；折叠后编辑/批量现有测试零回归。
- **覆盖率** >85%（项目目标）。
- **真机 e2e**：M3 在抖音/B站真跑一个任务验收 live 控制台。

---

## 7. 风险与对策

| 风险 | 对策 |
|---|---|
| 实时日志量大，web 卡顿 | SSE 背压 + 前端虚拟滚动 + level 过滤；仅保留可见范围 |
| 截图推帧占带宽 | 仅最新帧 + `resize_screenshot_bytes` 降采样；前端不可见不推 |
| 单 Chrome 单 BrowserSession | live task 与 batch 共享并发槽（互斥），复用 `_MAX_CONCURRENT` 思路 |
| 长任务 + 浏览器断连 | SSE 重连补发 `final_event`（仿 batch）；task 与 SSE 解耦，断连续续看 |
| agent.run 异常 | try/except 兜底发 `done{error}`，仿 batch 的 run_batch finally |
| 折叠破坏现有编辑/批量 | Edit/Replay 视图零改 reducer 逻辑，仅包裹；现测试守回归 |

---

## 8. 已定决策（2026-08-11）

1. **live task 与 batch 的并发**：**共享单槽（互斥）**——与现状（`_MAX_CONCURRENT_BATCH=1`）一致，不引入多 Chrome。
2. **截图推帧载荷**：**SSE 内联 base64**——单帧简洁；超大时走 `resize_screenshot_bytes` 降采样。
3. **TUI 下线后入口**：**`treewalker` 默认进 web**——M6 收尾时默认启动 web 服务，TUI 移除。
