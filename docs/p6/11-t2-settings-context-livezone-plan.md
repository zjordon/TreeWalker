# P6 后续实施计划：T2 中期（便利 / 统一）——设置面 C + 右 Context 面板 G + 进行中 zone H + I4/I5/I6

> 状态：**M1–M8 已实施**（2026-08-15；后端 `test_web_server.py` 75 过 + 全量 2312 过（M3 时点）、前端 vitest 103 过 / tsc / build 全绿）。真机 e2e runbook 见 [`12-t2-e2e-runbook.md`](12-t2-e2e-runbook.md)（场景 L–R：状态提升 / 进行中 zone / 设置面 / 任务历史 / 模型选择 / 右 Context / ⌘K）——**待用户真机执行**。
> 范围：按 [`05`](05-followup-plan.md) §2（T2 三项：**C 设置面 / G 右 Context 面板 / H 侧栏「进行中」zone**）+ §1.I 明确后置到 T2 的 **I4 ⌘K 命令面板 / I5 模型选择器 / I6 任务历史**。T2 定位「便利 / 统一，无阻塞」。
> 前置：建立在 P6 首期（`02`/`04`）+ T1 已交付项（`06` B/I1/I2/I3、`08` A 直播视口）之上。**不改 shell 注册表结构**（设置面只是把 `enabled:false` 改 `true`）、**不改 EventBus 事件 schema**、**不改 SSE 桥接骨架**。
> 关联：issue #162 / [`05`](05-followup-plan.md)。

---

## 0. 范围回顾与前置事实核实 ★

调研代码后核实以下事实，直接影响方案落点（仿 `06` §1 的勘误体例）：

| 项 | 05 的描述 | 代码实际（已核实） | 对方案的影响 |
|---|---|---|---|
| **C** 配置来源 | 「配置全靠 `.env`」 | 属实：`load_settings()`（`config.py:315`）**每次调用都从 `os.environ` 现读**；`_build_agent`（`web/server.py:271`）**每个任务构造时才调 `load_settings()`** | **不需要写 `.env` 就能改配置**——web 进程内 `os.environ[...] = ...` 即可，下一个任务自动生效。`.env` 写回降级为可选项 |
| **C** 占位 | 「disabled 占位」 | `AppShell.tsx:24` `{id:"settings", enabled:false, Component:SettingsPlaceholder}` | 注册表机制现成，改 `enabled:true` + 换组件即可（`01` §4.2 机制 1 兑现） |
| **H** live 状态归属 | 「sidebar 加进行中区」 | **live 任务 state 在 RunView 组件内部**（`RunView.tsx:10` 的 `useReducer(liveReducer)`）；而 sidebar 在 FlowWorkspace 内（`FlowWorkspace.tsx:128`）；**切模式即卸载组件、state 全丢**（`AppShell.tsx:58` 的 `<ActiveComponent/>` 条件渲染） | H 的**真正核心是 live 状态提升**（RunView → AppShell 级 context），sidebar 列表本身反而是薄壳 |
| **H** 后端可枚举性 | 未提 | `_LIVE_TASKS`（`server.py:84`）是模块级 dict，含 running/done handle + `final_event`；但**无 `/task/list` 端点**，前端无法枚举活跃任务 | 需新增 `GET /task/list`（只读，零风险） |
| **G** 右栏现状 | 「各模式局部 context；无统一右栏」 | RunView 时间线**无选中概念**（`RunView.tsx:150-158` 平铺事件）；编辑 tab 右栏 = ActionEditor+VariablePanel（`FlowWorkspace.tsx:169-172`）；详情 = DetailView master-detail（`DetailView.tsx:29-51`） | RunView 缺「选中→详情」的第一步；编辑/详情已有局部右栏，**迁移成本高、收益低**（见 §8 决策 3：本期只接 RunView） |
| **I5** 模型 | 「需后端按任务指定 model」 | `_build_agent(task)` 不收 model；`settings.llm.model` 默认 `glm-5.1`（`config.py:464`，env `LLM_MODEL`）；前端 `startTask`（`api.ts:92`）body 已有扩展位 | 改动面小：`/task/start` 增 `model` 参数 → `_build_agent` override `settings.llm.model` |
| **I6** 历史 | 「同 TUI 的 `~/.treewalker/history.json`」 | TUI 读写 `HISTORY_FILE = Path.home()/".treewalker"/"history.json"`（`tui/app.py:24`，`list[str]`，max 100，连续去重） | 后端加 `/task/history` GET/POST **共享同一文件**——TUI 与 web 历史互通（决策见 §8.7） |
| **I4** ⌘K | 「快速搜索/跳转」 | 现无全局键盘监听；数据源（流程列表 `listFiles`、技能 `listSkills`、模式）api 现成 | 纯前端，无后端改动 |

**结论**：T2 六项均无外部阻塞。工作量最大的意外点是 **H 的状态提升**（05 估「中」是准的，但难点在 React 架构而非列表本身）；**C 后端比 05 暗示的简单**（env override 即可，`.env` 写回是可选增强）。

---

## 1. 架构总览

```
┌────────────────────────────────────────────────────────────────────┐
│ 前端 SPA（web_ui）                                                  │
│  AppShell（改造点 ①②）：                                             │
│   ① live 状态提升：useReducer(liveReducer) 上移到 AppShell，         │
│      经 LiveTaskContext 下传 → RunView 变消费者（切模式不丢状态）      │
│   ② TopBar 增量：[模型 ▾]（I5）+ [⌘K]（I4）+ 运行状态点（H 配套）    │
│   ③ MODES：settings → enabled:true + <SettingsShell/>（C）          │
│  FlowWorkspace sidebar（改造点 ④）：顶部「进行中」zone（H）           │
│   · 活跃/最近 live task（/task/list）· 状态点 · 点击回 RunView        │
│  RunView（改造点 ⑤⑥）：                                              │
│   ⑤ 时间线项可选中 → 右侧 <ContextPanel/>（G 阶段一）                 │
│   ⑥ 任务输入 up/down 历史（I6，/task/history）                        │
└───────────────┬────────────────────────────────────────────────────┘
                │ REST + SSE（同源 / Vite proxy）
┌───────────────▼────────────────────────────────────────────────────┐
│ 后端 aiohttp（web/server.py）                              │
│  新增：GET /settings/get · POST /settings/set（env override，C）     │
│  新增：GET /task/list（活跃/最近 live task 枚举，H）                 │
│  新增：GET/POST /task/history（共享 ~/.treewalker/history.json，I6） │
│  改动：/task/start 增 model 参数 → _build_agent override（I5）       │
│  EventBus→SSE 桥接零改动（"*" 订阅 + model_dump 自动转发）           │
└────────────────────────────────────────────────────────────────────┘
```

**复用要点**（同 `06` §2）：所有新端点都注册在 SPA catch-all **之前**（`make_app` :98-118 的既定模式）；路径校验/错误风格仿 `_handle_skills_*`；前端 api 封装仿 `SKILLS` 块（`api.ts:147-176`）。

---

## 2. C 设置面

### 2.1 后端：`/settings/*` 端点

**注册表驱动的可编辑项**（server.py 模块级常量，唯一需要维护的清单）：

```python
@dataclass(frozen=True)
class SettingField:
	key: str          # 展示名（亦作前端 id）
	env: str          # 对应环境变量
	type: str         # "str" | "int" | "float" | "bool" | "enum"
	default: str      # 字符串形式默认值（与 load_settings 的 fallback 一致）
	section: str      # llm | agent | browser | advanced
	choices: tuple = ()   # enum 的候选
	sensitive: bool = False  # GET 时脱敏

_SETTINGS_FIELDS = (
	SettingField("模型", "LLM_MODEL", "str", "glm-5.1", "llm"),
	SettingField("Base URL", "LLM_BASE_URL", "str", "https://open.bigmodel.cn/api/anthropic", "llm"),
	SettingField("Max Tokens", "LLM_MAX_TOKENS", "int", "4096", "llm"),
	SettingField("输出模式", "LLM_OUTPUT_MODE", "enum", "standard", "llm",
	             choices=("standard", "flash", "thinking")),
	SettingField("API Key", "ZHIPU_API_KEY", "str", "", "llm", sensitive=True),
	# fallback LLM（可选段）
	SettingField("Fallback 模型", "FALLBACK_LLM_MODEL", "str", "", "llm"),
	# agent 段
	SettingField("最大步数", "AGENT_MAX_STEPS", "int", "100", "agent"),
	SettingField("最大失败数", "AGENT_MAX_FAILURES", "int", "5", "agent"),
	SettingField("LLM 超时(秒)", "AGENT_LLM_TIMEOUT", "int", "120", "agent"),
	SettingField("Action 超时(秒)", "AGENT_ACTION_TIMEOUT", "int", "30", "agent"),
	SettingField("计划模式", "AGENT_ENABLE_PLANNING", "bool", "true", "agent"),
	SettingField("Skill 注入", "AGENT_ENABLE_SKILL_INJECTION", "bool", "true", "agent"),
	# browser 段（cdp_port 由 tw-web CLI 决定，不在此暴露）
	SettingField("高亮反馈", "BROWSER_HIGHLIGHT_INTERACTION", "bool", "true", "browser"),
	# advanced 段（少量代表，勿全量搬）
	SettingField("重放步间延迟(秒)", "AGENT_RERUN_DELAY_BETWEEN_ACTIONS", "float", "1.0", "advanced"),
	...
)
```

> **不追求全量**：`load_settings` 有 80+ 个 env，全暴露反而不可维护。首期按「用户会想调的」精选 ~15-20 个；后续加项 = 注册表加一行。

| 方法 | 路径 | 行为 |
|---|---|---|
| GET | `/settings/get` | 遍历注册表 → `{"fields":[{key,env,section,type,choices,value,masked}]}`；`value = os.environ.get(env, default)`；**sensitive 且非空 → value 换成 `"****" + 尾4位`、`masked:true`** |
| POST | `/settings/set` | body `{env: value}` 逐项校验（**必须在注册表内**——防任意 env 注入；type 解析失败 400）→ **`os.environ[env] = value`**（进程内存 override）→ `{"ok":true, "applies":"new_tasks"}` |

**关键决策——为什么默认不写 `.env`**（§8.1）：
- `load_settings()` 每任务现读 env → 内存 override 对「下一个 live task / 试跑 / 批量」即时生效，**零持久化风险**；
- 重启 `tw-web` 后 override 失效、回落 `.env`——对「试参数」场景反而合适；
- `.env` 含 API key，web 写它有并发/转义/误覆盖三重风险，05 的开放问题「写回 .env 的安全/并发」最稳的答案是**首期不写**。显式「保存到 .env」按钮列为后续增强（原子写：临时文件 + `os.replace`，按 `KEY=` 整行替换、保留注释与顺序）。

**生效范围（明示给前端）**：只影响**之后构造**的 agent（`_build_agent` 每任务调 `load_settings`）；运行中任务不受影响（其 settings 已固化）。`cdp_port` 不进设置面——它在 CLI 层（`cli.py:129`）固定写 env，属进程参数非运行配置。

### 2.2 前端：`<SettingsShell/>` 分栏（仿 SkillsShell）

```
┌──────────────┬──────────────────────────────────────────┐
│ LLM    ●     │  模型            [glm-5.1        ]       │
│ Agent        │  Base URL        [https://open.big…]     │
│ Browser      │  API Key         [****abcd     ]（脱敏）  │
│ 高级         │  输出模式        [standard ▾]             │
│              │  ── 修改仅对新任务生效 ──                  │
│              │  [应用]  状态: 已应用 ✓                    │
└──────────────┴──────────────────────────────────────────┘
```

- 左栏 = section 列表（注册表 section 字段分组）；右栏 = 该区字段表单（type→控件：bool→checkbox、enum→select、int/float→number input、str→text）。
- `api.ts` 加 `getSettings()` / `setSettings(values)`；`AppShell` MODES `settings` 改 `enabled:true, Component: SettingsShell`（删 `SettingsPlaceholder`）。
- **敏感字段交互**：`masked:true` 时输入框 placeholder 显示掩码值；用户不动它就不提交该 key（**空/全掩码值 → POST 跳过**，防把掩码串写进 env）。
- 应用后顶部提示「已应用，新任务生效」。dirty 未保存切换 section 时确认丢弃（同 SkillsShell 的轻量防丢）。

---

## 3. H 侧栏「进行中」zone

### 3.1 前端核心：live 状态提升（本项最大改动）

现状问题：`AppShell.tsx:58` 条件渲染 `<ActiveComponent/>`——切到「流程库」再切回「探索」，RunView 卸载重建，live reducer state（taskId/事件流/截图/token）全丢，SSE 也断（重连只补 `final_event`，中途事件不可恢复）。

**改造**：
1. 新建 `web_ui/src/liveContext.ts`（仿 `appNav.ts` 的轻量 context）：
   ```ts
   export interface LiveTaskCtx {
       state: LiveState;
       dispatch: React.Dispatch<LiveAction>;
   }
   export const LiveTaskContext = createContext<LiveTaskCtx | null>(null);
   export const useLiveTask = (): LiveTaskCtx | null => useContext(LiveTaskContext);
   ```
2. `AppShell`：`useReducer(liveReducer, initialLiveState)` 上移至此，`<LiveTaskContext.Provider>` 包住 shell 主体。
3. `RunView`：删本地 `useReducer`，改 `const live = useLiveTask()` 消费（解构 `state`/`dispatch`，其余渲染/订阅逻辑零改动——`useEffect` 依赖仍是 `state.taskId`）。
4. **副作用收益**：切模式回来，RunView 状态完整、SSE 订阅重建（`useEffect` 重跑）后从队列剩余事件续播——「进行中点进去还在」就自然成立了。

**配套**：TopBar 加运行状态点（`state.phase === "running" ? "●" : ""`，点击 `setMode("explore")`）——让用户在任何模式都能一眼看到有任务在跑。

### 3.2 后端：`GET /task/list`

```python
async def _handle_task_list(request):
	items = []
	for tid, h in _LIVE_TASKS.items():
		items.append({
			"task_id": tid,
			"task": <任务文本>,        # LiveTaskHandle 需新增 task_text 字段（start 时存）
			"phase": "done" if h.final_event else "running",
			"success": (h.final_event or {}).get("success"),
			"saved": (h.final_event or {}).get("saved"),   # 录制落库的文件名
			"viewport_mode": h.vp_mode,
		})
	return web.json_response({"tasks": items})
```

- `LiveTaskHandle` 增 `task_text: str = ""`（`_handle_task_start` 存入；I6 也要用）。
- 顺序：新→旧（dict 插入序，start 时已清已结束 handle，列表天然很短）。
- **paused 状态**：`agent` 有 `pause()/resume()`，phase 可从 `getattr(h.agent.state, "paused", False)` 补一个 `"paused"`（拿不到就并进 running，非关键）。
- **不持久化**（§8.5）：刷新后 `/task/list` 仍能列出后端内存里的活跃任务（后端进程没重启就行），前端挂载时拉一次即可「恢复入口」；但 RunView 的已消费事件/截图不可恢复——**已知局限**，标注在 zone 的 UI 上（「恢复视图将丢失历史步骤」不必显式提示，时间线从恢复点续记即可接受）。

### 3.3 前端：sidebar「进行中」区

`FlowWorkspace.tsx` sidebar 顶部（「流程库」标题上方）：

```
▼ 进行中
  ● 帮我把视频上传到B站…（running）   ← 点击 setMode("explore")
  ✓ 抖音封面设置（已存 20260815….json）
（无任务时整区隐藏）
```

- 新建 `web_ui/src/components/LiveZone.tsx`：props `{items, activeId, onOpen}`；挂载时 + 30s 轮询 `api.listTasks()`（SSE 不合适——zone 在流程库模式，没有对应事件流；轮询只读端点、列表极短，开销可忽略）。
- 点击行为：`setMode("explore")`。若该 task 非「当前 RunView 的 task」（`live.state.taskId !== item.task_id`）→ dispatch 一个新的 `ADOPT` action（`{type:"ADOPT", taskId}`：按 §3.1 的恢复语义重置展示字段并让 useEffect 重订 SSE）——**单槽并发下同屏至多一个 running**，多数情况点击只是切模式。
- 「把 live task 也视作一种 Flow 统一进 sidebar」（05 的备选方案）**不做**——live task 无 history 文件（除非录制落库），硬塞进流程列表语义混乱；落库后自然出现在「流程库」区，链路已经连续（`01` §3.1 的原设计）。

---

## 4. G 右 Context 面板（阶段一：RunView 接线）

### 4.1 与 05 方案的偏差（先说清）

05 设想「shell 级可折叠面板，跨模式跟随选中项」。核实后：编辑 tab 的 ActionEditor/VariablePanel 与 reducer 深度耦合（selected 步/动作索引驱动表单），详情的 master-detail 工作良好——**把这两处迁进 shell 级右栏是纯重构，收益低**。本期做「组件级统一 + RunView 接线」（§8.3）：

- 抽一个通用 `ContextPanel`（右栏容器：标题 + 折叠 + children 按选中类型换渲染器——对应 `01` §4.2「右 Context 可插拔：不同选中类型渲染不同详情」的预留缝）；
- **RunView 接线**（当前完全无右栏的模式，净增能力）；
- 编辑/详情维持现状，迁移列为 D（DOM 快照回放）落地时的配套项——G 的「为 D 铺路」价值主要就在 RunView 步骤详情这条线上。

### 4.2 RunView 设计

- `LiveState` 增 `selectedEvent: number | null`（events 数组索引；`STARTING`/`ADOPT` 重置）。
- `liveReducer` 增 `{type:"SELECT_EVENT", index: number | null}`。
- 时间线 `<li>` 变可点击（选中高亮 `selected`），点击 dispatch SELECT_EVENT。
- `.run-body` 右侧新增 `<ContextPanel>`，按选中事件 type 渲染：
  - `tool_call`：动作名、params（JSON 格式化）、`element_index`/`element_xpath`（I3 已带）、目标元素 bbox；
  - `model_result`：`next_goal`、本步 token（I2 已带）；
  - `skill_active`：host、字数、点击进技能面（复用 I1 chip 的 `nav.openSkills`）；
  - `screenshot`/`log`：不进右栏（时间线/日志流已有）——时间线渲染时可过滤这两类不出现在可选列表（或标注跳过）。
- 折叠态：宽度收成 0（CSS transition），BrowserView/时间线自然占满——复用现有 `.flow-workspace` 三栏样式思路，`App.css` 增 `.run-body.with-context` 布局。

---

## 5. I5 模型选择器 / I6 任务历史 / I4 ⌘K

### 5.1 I5 模型选择器

- **后端**：`/task/start` body 增 `model?: string`；`_handle_task_start` 传入 `_build_agent(task_text, model=model or None)`；`_build_agent` 增参：
  ```python
  def _build_agent(task: str = "", model: str | None = None):
	  ...
	  if model:
		  settings.llm.model = model   # 仅本任务 override（settings 每次 load_settings 新建，无交叉污染）
  ```
- **前端**：AppShell TopBar 增 `<select>`（I4 的 ⌘K 面板里也给同名命令）；候选 = 静态列表（`glm-5.1` 等 + 「自定义…」走 prompt/input）+ `localStorage["tw-web.model"]` 记忆；经 `appNav` context 扩展字段 `model/setModel` 下传给 RunView → `api.startTask(..., model)`。
- **范围**：只影响**新 live 任务**；试跑/批量沿用 env 默认（`06` 同款边界，避免扩散）。
- **与 C 的关系**：设置面改 `LLM_MODEL` 是「改默认」；TopBar 选择器是「本次 override」——两层并存，选择器默认值取「未选 = 跟随设置」。

### 5.2 I6 任务历史

- **后端**：新增 `GET /task/history`（读 `~/.treewalker/history.json`，损坏/缺失 → `[]`）+ `POST /task/history` body `{task}`（append、连续去重、`[-100:]`、`mkdir(parents=True, exist_ok=True)` 后写回——**逐行对齐 TUI `_save_history`** `tui/app.py:362-374`，两端口共用同一文件与格式）。
- **前端**：`RunView` 挂载时拉历史存本地 `history: string[]` + `histIdx`；`onStart` 成功后 POST 追加；textarea `onKeyDown`：光标在首行且 ArrowUp → `histIdx-1` 回填、光标在末行且 ArrowDown → 前进/清空（textarea 原生多行行为需拦截 `preventDefault`；编辑中（textarea 非空且非历史回填态）不拦截）。
- **录入时机**：`startTask` 返回 task_id 后（TUI 是提交时存，web 等启动成功再存，避免失败任务污染历史——§8.8）。

### 5.3 I4 ⌘K 命令面板

- 新建 `web_ui/src/components/CommandPalette.tsx`：`AppShell` 挂全局 keydown（Ctrl/Cmd+K）开关；浮层 = input + 结果列表 + ↑↓/Enter/Esc。
- 命令源（懒加载 fetch，打开面板时拉一次）：
  1. **跳转**：切四个模式；
  2. **流程**：`listFiles()` → 「打开流程 xxx」→ `setMode("flows")` + 预选加载（FlowWorkspace 需经 appNav 扩展 `openFlow(name)`——仿 `openSkills` 的既有模式，AppShell 持 `flowsName` state 下传）；
  3. **技能**：`listSkills()` → `openSkills(host)`；
  4. **最近任务**：`GET /task/history` → 「重发：xxx」→ 切 explore + 回填 task 文本（不直接启动，让用户确认/补充）；
  5. **模型**：切换 TopBar 模型（与 I5 同一 action）。
- 过滤：子串不区分大小写匹配 + 关键字拼音/别名**不做**（MVP）。

---

## 6. 分阶段交付（goal 卡）

> 每张卡自含可直接作为实施会话的开场 prompt：**目标 / 改动面 / 硬约束 / 验收 / 不做什么**。设计细节不复制进卡片，一律引用章节（§2-§5）——改设计只改一处，卡片不漂移。**M1 是唯一的架构重构，用处方 + 硬约束的混合形态**（纯 goal 会让实施者自由发挥出另一种架构）；M2-M8 均为加法式改动，goal 化。

### M1 · H 前半：live 状态提升（处方式，先做 de-risk）

- **目标**：切模式（探索↔流程库↔技能）往返后，RunView 的 live 任务状态（taskId/事件流/截图/token/技能 chip）完整保留，SSE 从队列剩余事件续播。
- **处方**（§3.1，按此实施，勿另选架构）：
	1. 新建 `web_ui/src/liveContext.ts`：`LiveTaskCtx { state: LiveState; dispatch: Dispatch<LiveAction> }` + `LiveTaskContext` + `useLiveTask()`（仿 `appNav.ts` 体例）；
	2. `AppShell.tsx`：`useReducer(liveReducer, initialLiveState)` 上移至此，`<LiveTaskContext.Provider>` 包住 shell 主体；
	3. `RunView.tsx`：删本地 `useReducer`（:10），改 `const live = useLiveTask()` 消费——**其余渲染/SSE 订阅逻辑零改动**（`useEffect` 依赖仍是 `state.taskId`）；
	4. TopBar 增运行状态点（`state.phase === "running"` 时 `●`，点击 `setMode("explore")`）。
- **硬约束**：不改 `liveReducer.ts` 的现有 action 语义；不改 `api.ts`；不改后端。
- **验收**：`npx vitest run` 全绿（现有 RunView/liveReducer 测试零回归）+ 新增 AppShell 级测试「切模式往返 state 不丢」；`npm run build` + `tsc` 过。
- **不做什么**：`ADOPT` action 与 `/task/list`（M2）；右栏（M7）；命令面板（M8）。

### M2 · H 后半：`/task/list` + 侧栏「进行中」zone

- **目标**：FlowWorkspace 侧栏顶部出现「进行中」区——活跃任务带 ● 状态点、最近完成任务带结果/落库名，点击回到 RunView 视图。
- **改动面**：`web/server.py`（`LiveTaskHandle` 增 `task_text` 字段 + `GET /task/list`，见 §3.2）+ 新建 `web_ui/src/components/LiveZone.tsx`（§3.3）+ `FlowWorkspace.tsx` sidebar 接线 + `api.ts` 增 `listTasks()` + `liveReducer.ts` 增 `ADOPT` action。
- **硬约束**：`/task/list` 只读、注册在 SPA catch-all 之前；zone 用 30s 轮询（不用 SSE）；单槽并发下 running 至多一个，「live task 即 Flow」不做（§8.6）。
- **验收**：`uv run python -m pytest tests/ -x -v` 全绿 + 新增 `/task/list` 单测（running/done 字段、task_text、saved 透出）；vitest 新增 zone 渲染/点击切模式；真机：跑任务时切流程库 → zone 显示 ●，点回视图还在续播。
- **不做什么**：刷新后恢复已消费事件（已知局限 §8.5）；paused 精确态（拿不到就并进 running）。

### M3 · C 后端：`/settings/get` + `/settings/set`

- **目标**：web 进程内可读写运行配置——精选 ~15-20 个字段的注册表（§2.1），GET 返回当前有效值（sensitive 脱敏），SET 内存 override 后对下一个任务生效。
- **改动面**：仅 `web/server.py`（`SettingField` + `_SETTINGS_FIELDS` + 两 handler）+ `tests/test_web_server.py`。
- **硬约束**：**不写 `.env`**（§8.1）；SET 的 env 必须在注册表白名单内（否则 400，防任意 env 注入）；type 解析失败 400；sensitive GET 掩码 `****` + 尾 4 位；`CDP_PORT` 等进程级 env 不进注册表。
- **验收**：pytest 全绿 + 新增单测：get 默认值/掩码、注册表外 env 拒 400、bool/int 非法值 400、set 后 monkeypatch `_build_agent` 断言 settings 生效。
- **不做什么**：SettingsShell 前端（M4）；「保存到 .env」按钮（后续增强）；全量 80+ env 暴露。

### M4 · C 前端：`<SettingsShell/>` 分栏

- **目标**：顶部「设置」从 disabled 占位变为可用——左 section 导航（LLM/Agent/Browser/高级）+ 右字段表单（type→控件映射），应用后提示「新任务生效」。
- **改动面**：新建 `web_ui/src/components/SettingsShell.tsx`（§2.2，仿 `SkillsShell`）+ `AppShell.tsx` MODES 启用（删 `SettingsPlaceholder`）+ `api.ts` 增 `getSettings()/setSettings()`。
- **硬约束**：sensitive 字段 masked 时不改动则**不提交该 key**（防掩码串写回 env）；dirty 切 section 确认丢弃；改动仅对新任务生效需在 UI 明示。
- **验收**：vitest 全绿 + 新增 SettingsShell 测试（分区切换、bool→checkbox/enum→select、敏感字段不动不提交）；真机：改 max_steps=3 → 新任务 3 步停。
- **不做什么**：「恢复默认」可做可不做（风险表有列，非本期验收项）；.env 写回。

### M5 · I6：任务历史（与 TUI 互通）

- **目标**：RunView 任务输入框 ↑/↓ 翻历史；启动成功的任务落历史；TUI 与 web 共用 `~/.treewalker/history.json`（§5.2）。
- **改动面**：`web/server.py` 增 `GET/POST /task/history`（读写逻辑逐行对齐 `tui/app.py:353-374`）+ `RunView.tsx` 键盘导航 + `api.ts` 两方法。
- **硬约束**：与 TUI 同格式（`list[str]`）、同上限（`[-100:]`）、连续去重；**启动成功后才 append**（§8.8）；损坏/缺失文件容错返回 `[]`；光标不在首行/末行时不拦截方向键。
- **验收**：pytest 全绿 + 新增单测（空/损坏容错、去重、100 截断、与 TUI 同路径）；vitest RunView keydown；真机：TUI 发过的任务在 web ↑ 可翻。
- **不做什么**：历史搜索/管理 UI（删除、置顶）。

### M6 · I5：模型选择器

- **目标**：TopBar 可选 LLM 模型，新 live 任务按所选模型起 agent（§5.1）。
- **改动面**：`web/server.py`（`/task/start` 增 `model` + `_build_agent` 增参 override `settings.llm.model`）+ `AppShell.tsx` TopBar select + `appNav.ts` 扩展 `model/setModel` + `RunView.tsx` 传参 + `api.ts` `startTask` 增参。
- **硬约束**：override 只作用于**新 live 任务**；试跑/批量走 env 默认（§8.9）；选择器是「本次 override」、设置面 `LLM_MODEL` 是「改默认」，两层并存；后端不校验模型名（LLM 端点报错透传）。
- **验收**：pytest 全绿 + 单测（start 带 model → agent llm.model 变、缺省不受影响）；vitest 选择器 localStorage 记忆；真机：切模型跑任务，日志显示新模型。
- **不做什么**：模型列表端点（静态候选 + 自定义输入即可）；试跑/批量的模型选择。

### M7 · G：RunView 右 Context 面板

- **目标**：RunView 时间线项可选中，右侧 `<ContextPanel>` 按事件类型渲染详情（tool_call→params/xpath/bbox、model_result→next_goal/token、skill_active→host/进技能面），可折叠（§4）。
- **改动面**：`liveReducer.ts` 增 `selectedEvent` + `SELECT_EVENT` action + `types.ts` + `RunView.tsx` 时间线可点击 + 新建 `web_ui/src/components/ContextPanel.tsx` + `App.css` 布局。
- **硬约束**：`ContextPanel` 做成通用容器（标题+折叠+按选中类型换渲染器，`01` §4.2 预留缝）；**编辑/详情的局部右栏不迁移**（§8.3）；`STARTING`/`ADOPT` 重置 `selectedEvent`；screenshot/log 事件不进可选列表。
- **验收**：vitest 全绿 + 新增 reducer `SELECT_EVENT`、右栏随选中渲染；真机：点 tool_call 步骤右栏出 params/xpath。
- **不做什么**：shell 级跨模式右栏（D 落地时配套）；编辑/详情右栏改造。

### M8 · I4：⌘K 命令面板

- **目标**：Ctrl/Cmd+K 呼出全局命令面板，可跳模式、打开流程/技能、回填最近任务、切模型（§5.3）。
- **改动面**：新建 `web_ui/src/components/CommandPalette.tsx` + `AppShell.tsx` 挂全局 keydown + `appNav.ts` 扩展 `openFlow(name)`（AppShell 持 `flowsName` 下传，仿 `openSkills`）+ `FlowWorkspace.tsx` 接收预选加载。
- **硬约束**：数据源（流程/技能/历史）**打开面板时懒加载一次**，拉失败静默降级为仅「跳转」命令（§8.10）；不做拼音匹配；「重发」只回填不直接启动。
- **验收**：vitest 全绿 + 新增面板测试（过滤、↑↓/Enter/Esc、执行回调）；真机：⌘K 搜流程名直接打开。
- **不做什么**：命令注册表抽象（MVP 硬编码五类命令源）。

> **顺序理由**：M1 先做——它是唯一的 React 架构改动（其余都是加法），早做早 de-risk，且 M2/M8 依赖它；M3/M4（C）独立可并行；G（M7）放后因它纯增量、且 UI 打磨依赖前面各件到位后的最终布局。e2e runbook（仿 `07`/`09` 的体例，编 `12`）在 M8 后统一走一遍全场景。

---

## 7. 测试策略

- **后端**（`uv run python -m pytest tests/ -x -v`，覆盖率 >85%）：
  - `/settings/*`：注册表外 env 拒绝、类型校验、脱敏（GET 不泄 key）、set 后 monkeypatch `_build_agent` 断言 settings 生效、未运行任务不受影响。
  - `/task/list`：running/done 字段、task_text、final_event 的 saved 透出。
  - `/task/history`：空/损坏文件容错、append 去重、100 截断、与 TUI `HISTORY_FILE` 同路径。
  - `/task/start` model：override 生效、缺省不受影响。
- **前端**（vitest）：
  - `liveReducer.test.ts`：`ADOPT`/`SELECT_EVENT` 分支。
  - `AppShell` 新测试：LiveTaskContext 提供、切模式 state 保持、TopBar 状态点。
  - `SettingsShell.test.tsx` / `LiveZone.test.tsx` / `CommandPalette.test.tsx`：仿 `SkillsShell.test.tsx` 的 mock fetch 模式。
  - `RunView.test.tsx`：历史导航 keydown、右栏渲染。
- **真机 e2e**（runbook 单独成文 `12-t2-e2e-runbook.md`）：H 切模式不丢视图 / C 改 max_steps 生效 / I5 换模型 / I6 TUI↔web 历史互通 / G 步骤详情 / ⌘K 全流程。

---

## 8. 已定决策（建议方案，2026-08-15）

1. **C 配置写入方式**：**进程内存 `os.environ` override，不写 `.env`**。理由：`load_settings` 每任务现读 → 即时生效于新任务；规避写盘的并发/转义/误覆盖与敏感信息落盘风险；「保存到 .env」列为后续可选增强（原子替换整行）。
2. **C 生效范围**：只影响之后构造的 agent；运行中任务不受影响。UI 明示「新任务生效」。
3. **G 范围**：**阶段一只做 RunView 右栏**（通用 `ContextPanel` 组件 + 四种事件渲染器）；编辑/详情的局部右栏**不迁移**——迁移是纯重构、收益低，留待 D（DOM 快照回放）配套。
4. **H 状态归属**：**live reducer 提升到 AppShell**（LiveTaskContext），RunView 变消费者。这是「切模式/刷新不丢视图」的唯一正解。
5. **H 列表持久化**：后端不新增持久化——`_LIVE_TASKS` 进程内存已够（tw-web 常驻）；刷新后经 `/task/list` 恢复入口，已消费事件不可恢复为**已知局限**（SSE 重连补 `final_event` + 队列剩余）。
6. **H 不做「live task 即 Flow」**：落库（录制开）后自然进流程库，避免无 history 文件的伪 Flow 项。
7. **I6 文件**：**与 TUI 共享 `~/.treewalker/history.json`**（同格式同上限），两端历史互通。
8. **I6 录入时机**：**启动成功后** append（TUI 是提交时）——避免启动失败的任务污染历史。
9. **I5 边界**：模型 override 只作用于**新 live 任务**；试跑/批量走 env 默认；TopBar 选择器是「本次 override」，设置面 `LLM_MODEL` 是「改默认」，两层并存。
10. **I4 数据源**：流程/技能/历史**打开面板时懒加载**一次，不做增量同步；不做拼音匹配。

> 以上均为建议方案；如某条想另选（如 C 要直接写 `.env`、或 G 一步到位做 shell 级），在实施前提出即可调整。

---

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| M1 状态提升动 RunView 既有订阅逻辑（SSE useEffect 依赖） | 改动仅是 state 来源换 context，effect 依赖仍 `state.taskId`；现有 RunView/liveReducer vitest 全过作为回归门 |
| `/settings/set` 任意 env 注入 | 白名单（注册表内）+ 类型校验 + 400；`CDP_PORT` 等进程级 env 不进注册表 |
| 敏感字段掩码被误写回 | masked 值/空值 POST 端跳过；单测覆盖 |
| 设置改坏导致任务起不来（如非法 base_url） | `/task/start` 失败会经 400 + error 文案回到 UI；设置面加「恢复默认」按钮（逐字段 default） |
| 刷新后恢复的 RunView 事件不全 | 已知局限（§8.5）；时间线从恢复点续记，截图等下一帧；不在 UI 上做过度承诺 |
| `/task/history` TUI 与 web 并发写 | 同 TUI 现状（整文件覆写、max100 极小）；两端都是单实例本地工具，实际无并发；损坏容错已设 |
| I5 model 字符串无校验 | 后端不校验模型名（LLM 端点自会报错并透传 error 文案）；前端 select 候选 + 自定义输入 |
| 命令面板 fetch 慢 | 打开面板才拉、拉失败静默降级为仅「跳转」命令 |
