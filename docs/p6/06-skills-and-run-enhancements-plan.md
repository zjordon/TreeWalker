# P6 后续实施计划：技能面 + Run 视图增强（B / I1 / I2 / I3）

> 状态：**已实施**（M1–M5，2026-08-13；2274 Python + 57 前端测试过，覆盖率 88%）。真机 e2e 验收见 [`07`](07-skills-and-run-enhancements-e2e-runbook.md)。
> 范围：按 [`05`](05-followup-plan.md) §5 建议切入点，先做 **B 技能面** → **I1 活动技能标示 / I2 token·耗时环 / I3 元素高亮标注层**（I4 ⌘K / I5 模型选择 / I6 任务历史**不在本期**，仍后置 T2）。
> 前置：本期建立在 P6 首期（`02`/`04`）已交付的 web live 控制台 + 流程库之上，**不改 shell 注册表结构**、**不改 SSE 桥接骨架**。
> 关联：issue #162 / [`05`](05-followup-plan.md)。

---

## 0. 范围回顾与前置事实

- **B 技能面**：skill 注入机制已上线（v0.12.0，`SkillLoader` + `domain-skills/<host>/`），但**无后端端点、无 CLI、前端仅 `disabled` 占位**。本期补 `/skills/*` 端点 + 分栏编辑器。
- **I1/I2/I3**：RunView 已是 live 控制台（`02`/`04`），但三项增强的数据源**当前缺失或为空**（见 §1 勘误）。

---

## 1. 关键事实勘误（05 的现状描述 vs 代码实际）★

调研后发现 `05` 对 I1/I2/I3 的「现状」描述与代码实际有出入，**直接影响工作量估计**，先校正：

| 项 | 05 的描述 | 代码实际（已核实） | 对工作量的影响 |
|---|---|---|---|
| **I2** token | 「`ModelResultEvent` 已带 `input_tokens`/`output_tokens`，累加即可」 | 字段**在 schema 上**（`events.py:35-36`），但 emit 时**从不赋值**（`step.py:574-579` 省略）；根因是 `LLMClient.get_action()`（`llm/client.py:166-299`）拿到了 SDK 的 `response.usage` 却**丢弃**了 | **不是「累加即可」**——需先从 `LLMClient` 把 usage 透传到 `ModelResultEvent`（见 §3.3）。`MetricsAggregator`（`metrics.py:34-38`）早已订阅累加，但永远是 0 |
| **I3** 元素 | 「需后端在 `tool_call` 事件里带元素定位信息」 | 方向对，但**好消息**：元素查找原语已存在（`step.py:949-959` 的 `selector_map.get(idx)` → node），完整投影 `DOMInteractedElement.load_from_enhanced_dom_tree(node)`（`browser/views.py:54-103`，含 `bounds`/`x_path`）也是现成 | **无需新 DOM 接线**——只需在 emit 点（`step.py:924`）复用现成查找，把 bbox/xpath 塞进 `ToolCallEvent` |
| **I1** 活动 skill | 「显示当前 URL 对应的 host skill」 | ① 任何 live 事件都**不带 URL**（`StepStart/StepEnd` 无 url 字段；`LiveState` 无 url；`/task/start` 不收 url）；② `enable_skill_injection` 默认 **False**（`config.py:87`），`_build_agent` **不强制开启**（不像 observability 在 `server.py:196` 强制） | 需要：① 在 `_build_agent` 为 live 强制开 skill 注入；② 在 `step.py:252-256` 算完 `skill_desc` 处**emit 一个新事件**（§3.2） |

**结论**：I2 工作量比 05 估的大（要碰 `LLMClient`）；I3 比 05 估的小（DOM 接线现成）；I1 需新增一个事件 + 强制开关。

---

## 2. 架构总览

```
┌──────────────────────────────────────────────────────────────────┐
│ 前端 SPA（web_ui）                                                │
│  AppShell MODES：技能 enabled:true → <SkillsShell/>（分栏）       │
│   ┌ 左：host 列表（/skills/list）  ┐                              │
│   └ 右：_sop/selectors/quirks 三 tab 编辑 + 保存（/skills/put） ┘ │
│  RunView 增强：                                                   │
│   · .bar 加 [活动技能 chip]（I1，点进技能面）                      │
│   · .run-body 加 <TokenRing/>（I2，累计 token + 耗时）            │
│   · <BrowserView annotation={bbox...}/>（I3，叠元素高亮框）        │
└───────────────┬──────────────────────────────────────────────────┘
                │ REST + SSE（同源 / Vite proxy）
┌───────────────▼──────────────────────────────────────────────────┐
│ 后端 aiohttp（history_editor/server.py）                          │
│  新增 /skills/{list,get,put}（复用 resolve_rerun_path 路径校验）   │
│  EventBus→SSE 不改（"*" 订阅 + model_dump 自动转发新事件/新字段） │
└───────────────┬──────────────────────────────────────────────────┘
                │ 新事件 + 字段在 emit 处填充
┌───────────────▼──────────────────────────────────────────────────┐
│ Agent / step.py / LLMClient（最小侵入）                           │
│  · step.py:252 emit SkillActiveEvent（I1）                        │
│  · LLMClient.get_action 透传 response.usage → step.py:574 填 token │
│    （I2）                                                         │
│  · step.py:924 查 selector_map → 填 ToolCallEvent 的 bbox/xpath   │
│    （I3）                                                         │
│  · _build_agent 强制 enable_skill_injection=True（I1 前提）       │
└──────────────────────────────────────────────────────────────────┘
```

**复用要点**：新事件/新字段**自动流到 SSE**（`server.py:518-519` 的 `"*"` 订阅 + `model_dump(mode="json")`），**server.py 不需为 I1/I2/I3 改桥接**——只 B 的 `/skills/*` 要加端点。

---

## 3. 后端设计

### 3.1 B 后端：`/skills/*` 端点（server.py `make_app`）

在 `make_app`（`server.py:80-106`）的 `/task/*` 块之后、`/health`（:99）之前注册三条路由（**必须在 :105 SPA catch-all 之前**）：

| 方法 | 路径 | 作用 | 模板 |
|---|---|---|---|
| GET | `/skills/list` | 列 `skills_dir` 下所有 host 目录 → `{"hosts":["member.bilibili.com","creator.douyin.com"]}` | 仿 `_handle_list`（:113-116）glob 目录 |
| GET | `/skills/get?host=<host>` | 读 `<host>/{_sop,selectors,quirks}.md` → `{"host","files":{"_sop":..., "selectors":..., "quirks":...}}` | 仿 `_handle_load`（:119-128）+ **`resolve_rerun_path` 路径校验** |
| POST | `/skills/put` | body `{host, file, content}` → 写回 `.md` → **`invalidate`** + `{"ok":true}` | 仿 `_handle_save`（:131-146）+ 路径校验 |

**关键约束**：
- **路径穿越防护**：`host`/`file` 拼接后必须过 `resolve_rerun_path`（server.py:16 注释明确「路径校验复用 `rerun.resolve_rerun_path`」），拒绝 `..`/绝对路径；`file` 白名单 `{_sop, selectors, quirks}`。
- **skills_dir 解析**：`load_settings().agent.skills_dir`（默认 `"domain-skills"`，相对 CWD）——同 `history_dir` 取法（server.py:675-681）。
- **热更新（put 后失效缓存）**：`/skills/put` 写盘后，若 `_LIVE_TASKS`（server.py:77）里有正在跑的 agent，调该 agent 的 `agent._skill_loader.invalidate(host)`。单并发槽 → 至多一个 live task，遍历 `_LIVE_TASKS.values()` 即可。

### 3.2 I1 后端：`SkillActiveEvent` + 强制开启 skill 注入

**事件定义**（`observability/events.py`，新增）：
```python
class SkillActiveEvent(BaseEvent):
	host: str | None = None            # extract_host(browser_state.url)
	skill_loaded: bool = False         # bool(skill_desc)
	char_count: int = 0                # len(skill_desc)（轻量，不传全文）
```

**emit 点**（`step.py`，紧挨 252-256 之后）：
```python
# I1：把「本步活动 skill」事件化（web 前端标示用）
if self._obs_bus:
	from tree_walker.observability.events import SkillActiveEvent
	_host = extract_host(browser_state.url)
	self._obs_bus.emit(SkillActiveEvent(
		step=self.state.n_steps, session_id=self._obs_session_id,
		host=_host, skill_loaded=bool(skill_desc),
		char_count=len(skill_desc or ""),
	))
```
> `extract_host` 已在 `step.py` 间接可用（经 `_build_skill_description`→`agent.py:439`）；如未直接 import 则补一行 `from tree_walker.browser.url_utils import extract_host`。

**强制开启**（`server.py:_build_agent`，仿 :196 observability）：
```python
settings.agent.enable_observability = True
settings.agent.enable_skill_injection = True   # I1 前提：live 控制台默认带 skill
```
> loader 对无 skill 的 host 返回 `""` → 无副作用；env `AGENT_ENABLE_SKILL_INJECTION` 仍可覆盖关闭。

### 3.3 I2 后端：token 透传（LLMClient → ModelResultEvent）

这是本期**唯一碰 LLM 抽象**的点，分两步：

**① `llm/client.py` `get_action`**：`messages.create` 的返回 `response`（Anthropic `Message`）自带 `response.usage.input_tokens` / `output_tokens`，目前丢弃。在 `:181` 拿到 `response` 后捕获，最终塞进返回 dict（**向后兼容**，消费者按 key 读，不读 `usage`）：
```python
response = self.client.messages.create(...)
_usage = getattr(getattr(response, "usage", None), "model_dump", lambda: {})()
# ... 末尾
result = { ...existing keys..., "usage": _usage or None }
```
> 注意三条早返回路径（`:196` fallback 递归、`:229` 文本重试递归、`:233-245` 无解析兜底）——兜底分支也要带上 `usage`（其 `response` 同样有 usage）。递归分支由深层返回携带，无需重复。

**② `step.py:574-579` ModelResultEvent emit**：从 `response`（即 `get_action` 返回的 dict）读 usage 填字段：
```python
_usage = response.get("usage") or {}
self._obs_bus.emit(ModelResultEvent(
	step=self.state.n_steps, session_id=self._obs_session_id,
	model_call_id=model_call_id,
	action_name=action.get("name", "done"),
	next_goal=response.get("next_goal", ""),
	input_tokens=_usage.get("input_tokens"),
	output_tokens=_usage.get("output_tokens"),
))
```
> `MetricsAggregator`（`metrics.py:34-38`）自动开始累加真实值（此前一直 0）；`SessionEndEvent` 等其它消费方零改动。

### 3.4 I3 后端：`ToolCallEvent` 带 bbox/xpath

**事件扩字段**（`events.py:39-46`）：
```python
class ToolCallEvent(BaseEvent):
	...  # 现有字段
	element_index: int | None = None
	element_bbox: dict | None = None      # {x,y,width,height}，CSS 视口像素
	viewport: dict | None = None          # {width,height}，CSS 视口像素
	element_xpath: str | None = None
```

**emit 点**（`step.py:924`，复用 :949-959 的查找原语）：
```python
# I3：把目标元素几何挂上（仅 index 类动作：click/input_text/select_dropdown…）
_e_idx = action_params.get("index") if isinstance(action_params.get("index"), int) else None
_e_bbox = _e_vp = _e_xpath = None
if _e_idx is not None and browser_state and browser_state.dom_state:
	_sm = browser_state.dom_state.selector_map
	_node = _sm.get(_e_idx) if _sm else None
	if _node is not None:
		_diel = DOMInteractedElement.load_from_enhanced_dom_tree(_node)
		if _diel is not None and _diel.bounds is not None:
			_e_bbox = _diel.bounds.to_dict()
			_e_xpath = _diel.x_path or None
		_e_vp = _viewport_size(browser_state)   # 见下
self._obs_bus.emit(ToolCallEvent(
	step=self.state.n_steps, session_id=self._obs_session_id,
	model_call_id=getattr(self, "_current_model_call_id", ""),
	tool_call_id=tool_call_id, action_name=action_name, params=action_params,
	action_index=i, total_actions=total,
	element_index=_e_idx, element_bbox=_e_bbox, viewport=_e_vp, element_xpath=_e_xpath,
))
```
> `_viewport_size` 取 CSS 视口宽高（来源待实现期确认：`browser_state.dom_state` 的 viewport meta 或 `browser` 的视口尺寸方法）。**降级原则不变**：拿不到 node/视口 → 对应字段 `None`，照常执行，不影响动作。

---

## 4. 前端设计

> web_ui 源码用 **TAB 缩进**，编辑时务必对齐（`indentation-tabs-vs-spaces` 记忆）。

### 4.1 B 前端：技能面（`<SkillsShell/>` 分栏）

**启用模式**（`AppShell.tsx:22-27`）：`skills` 项 `enabled:true` + `Component: SkillsShell`（替换 `SkillsPlaceholder`）+ import。shell 的动态渲染（:30-32/51）零改动。

**SkillsShell 布局**（新建 `web_ui/src/components/SkillsShell.tsx`）：
```
┌──────────────┬─────────────────────────────────────┐
│ host 列表     │  host: member.bilibili.com          │
│ · bilibili   │  [SOP] [Selectors] [Quirks] ←tab     │
│ · douyin  ●  │ ┌─────────────────────────────────┐ │
│              │ │ <textarea>（当前文件内容）        │ │
│              │ │                                  │ │
│              │ └─────────────────────────────────┘ │
│              │  [保存]   状态: 已保存 / 保存失败     │
└──────────────┴─────────────────────────────────────┘
```
- `api.ts` 加 `listSkills()→{hosts}` / `getSkill(host)→{host,files}` / `putSkill(host,file,content)→{ok}`（仿 `listFiles/loadHistory/saveHistory`）。
- 右侧三 tab 对应 `_sop`/`selectors`/`quirks`；切换 host/tab → `getSkill`；编辑 → 本地 state；保存 → `putSkill`。
- **未保存切换 host/tab 时**确认丢弃（轻量防丢）。

### 4.2 I1 前端：RunView 活动技能 chip

- `LiveState`（`types.ts:103-114`）加 `activeSkill: {host, loaded, charCount} | null`。
- `liveReducer` `EVENT` 分支：`e.type==="skill_active"` → 设 `activeSkill`（结构与 §3.2 字段对齐）。
- `RunView` `.bar`（:70-98，status span 旁）渲染 chip：`loaded` 时 `🔧 {host}（{charCount}字）`，否则 `🔧 无技能`。
- **D6 点击进技能面**：chip `onClick` → 切 AppShell `mode="skills"` 并预选该 host（AppShell 需把 `mode`/`setMode` + 选中 host 经 props/context 下传；I1 收尾做，详见 §5 M3）。

### 4.3 I2 前端：token·耗时环

- `LiveState` 加 `tokens:{in,out}`（累计）、`elapsedMs:number`、`startedAt:number|null`。
- `liveReducer`：
  - `model_result` 分支：`tokens.in += e.input_tokens ?? 0`、`tokens.out += e.output_tokens ?? 0`（镜像 `MetricsAggregator._on_model_result`）。
  - `step_end` 分支：`elapsedMs += e.duration_seconds*1000`（或用 `startedAt` + 前端定时器，二者择一；建议累加 `step_end.duration_seconds`，离屏不跑定时器更省）。
  - `RESET`（:69-71）清零。
- `RunView` `.run-body` 加 `<TokenRing in out elapsedMs/>`（新建小组件，环形或条形皆可，先文本+进度条 MVP）。

### 4.4 I3 前端：BrowserView 元素高亮标注层

**坐标空间策略（关键）**：后端给 `element_bbox`（CSS 视口 px）+ `viewport`（CSS 视口 px）。截图经 `resize_screenshot_bytes` 等比降采样，**与视口同宽高比** → 前端用**百分比定位**即可对齐，无需知道采样比/DPR：
```
left%   = bbox.x / viewport.width  × 100
top%    = bbox.y / viewport.height × 100
width%  = bbox.width / viewport.width × 100
height% = bbox.height / viewport.height × 100
```
> 前提：`take_screenshot()` 是**视口截图**（非全页）；实现期确认，若是全页则改用截图实际像素分母。

**BrowserView 改造**（`components/BrowserView.tsx`，现 27 行）：
- `Props.annotation`（现文本占位）升级为结构化：`{bbox, viewport, index, label} | null`（或新增 `highlights: Highlight[]` 支持多框）。
- `.browser-frame` 加 `position:relative`；`<img>` 上叠一个绝对定位 `<div className="hl" style={{left,top,width,height（%）}}>`，带 `index` 角标。
- 多动作步：当前步所有 `tool_call` 的 bbox 都画（带 `action_index` 角标），与 step 截图同框。
- **已知局限（写进组件注释）**：截图在 `step_end` 采，若某动作触发了导航，目标元素可能已不在画面 → 该框可能错位/无意义。MVP 接受，后续可按「框 vs 截图 URL 一致性」过滤。

**数据流**：`liveReducer` 维护 `currentStepToolCalls: {index,bbox,viewport}[]`（`tool_call` 累加，`step_end` 清）；`RunView` 传给 `<BrowserView highlights=.../>`。

---

## 5. 分阶段交付

| 里程碑 | 内容 | 验收 |
|---|---|---|
| **M1** B 后端 `/skills/*` | list/get/put 端点 + 路径校验 + put 后 invalidate live loader | 单测：仿 `test_history_editor_server.py`，断言 list/get/put + `..` 拒绝 + put 后 loader 缓存失效 |
| **M2** B 前端 技能面 | 启用 `skills` mode + `SkillsShell` 分栏 + api 三方法 | vitest：列表/切换/保存流程；真机：改 B站 selectors.md 保存→重跑生效 |
| **M3** I1 活动 skill | `_build_agent` 强制开 skill + `SkillActiveEvent`（step.py）+ reducer + RunView chip + 点进技能面 | 单测：emit 事件字段；vitest：reducer `skill_active`；真机：跑抖音任务 chip 显示 `creator.douyin.com` |
| **M4** I2 token·耗时环 | `LLMClient.get_action` 透传 usage + `ModelResultEvent` 填 token + reducer 累加 + `<TokenRing/>` | 单测：`get_action` 返回含 usage（mock SDK response）；vitest：reducer 累加；真机：环随步增长 |
| **M5** I3 元素高亮 | `ToolCallEvent` 扩 bbox/viewport/xpath + step.py 填充 + BrowserView 百分比框 | 单测：emit 带 bbox（mock selector_map）；vitest：百分比换算；真机：click 动作框对齐截图元素 |

> **并行建议**：M4（碰 `LLMClient`，唯一风险点）可与 M1/M2 并行先行，**早 de-risk**；M3 依赖 M2 的「点进技能面」（chip → mode 切换），但 emit/reducer/chip 部分可先做。

---

## 6. 测试策略

- **后端**（`uv run python -m pytest tests/ -x -v`，覆盖率 >85%）：
  - `/skills/*` 端点单测（路径穿越、白名单文件、put invalidate）。
  - `SkillActiveEvent` emit 单测（仿 `test_agent_skill_injection.py`，断言事件字段 + 强制开启生效）。
  - `LLMClient.get_action` usage 透传单测（monkeypatch `messages.create` 返回带 `usage` 的假 response）。
  - `ToolCallEvent` bbox 单测（mock `selector_map` + `DOMInteractedElement`，断言非 index 动作字段为 None）。
- **前端**（vitest）：
  - `liveReducer.test.ts` 加 `skill_active`/`model_result`（token 累加）/`tool_call`（bbox 收集）case。
  - `RunView.test.tsx` 加 chip + ring 渲染。
  - `BrowserView` 百分比换算单测（给定 bbox+viewport → 期望 style%）。
  - `SkillsShell` list/get/put 流程单测（mock fetch）。
- **真机 e2e**：M2 改 skill 生效、M3 chip 显示、M4 环增长、M5 click 框对齐（抖音/B站各一）。

---

## 7. 风险与对策

| 风险 | 对策 |
|---|---|
| I2 改 `LLMClient` 影响探索/重放/extract 多路径 | usage 走「返回 dict 新增 key」向后兼容；三条早返回路径都带；extract 路径本期**不动**（次要调用，token 不计） |
| `_build_agent` 强制开 skill 改变 web 探索行为 | loader 对无 skill host 返回 `""`（无副作用）；保留 env 覆盖关闭；记入决策 |
| I3 截图（step_end）与 bbox（tool_call emit）时序错位 | 接受导航后错位为 MVP 已知局限；框带 `action_index` 角标便于辨识；后续按 URL 一致性过滤 |
| I3 坐标空间（CSS px vs 截图像素 vs DPR） | 百分比定位（bbox/viewport）规避采样比与 DPR（宽高比一致即对齐）；实现期确认 `take_screenshot` 为视口截图 |
| skill 编辑后缓存不刷新 | put 后 invalidate `_LIVE_TASKS` 中 agent 的 loader（单槽，至多一个） |
| `/skills/put` 写入并发 | 单并发槽 + 文件写原子性（临时文件 rename）；多 tab 编辑冲突本期不处理（last-write-wins） |

---

## 8. 已定决策（建议方案，2026-08-13）

1. **live 控制台是否强制 skill 注入**：**是**——`_build_agent` 强制 `enable_skill_injection=True`（仿 observability）。理由：技能面 + 活动标示的前提；对无 skill host 无副作用。
2. **活动 skill 的信号载体**：**新 `SkillActiveEvent`**（非给 `StepStartEvent` 加字段）。理由：`StepStart` 保持极简；新事件经 `"*"` 订阅自动到 SSE，server.py 不改。
3. **token 透传方式**：**`get_action` 返回 dict 增 `usage` key**（向后兼容）→ `step.py` 填 `ModelResultEvent`。extract 路径不动。
4. **I3 坐标对齐**：**百分比定位**（bbox/viewport），规避采样比/DPR。
5. **技能编辑热更新**：**put 后 invalidate 在跑 agent 的 loader**（单槽）。
6. **I1 chip 点击**：**切到技能面并预选 host**（与 B 联动，M3 收尾）。

> 以上均为建议方案；如某条想另选（如不强制开 skill、或 I3 改走后端归一化坐标），在实施前提出即可调整。
