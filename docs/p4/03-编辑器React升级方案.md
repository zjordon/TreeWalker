# P4 编辑器 React+Vite 升级方案（issue #149 子任务 1 强化）

> 状态：方案（待实现；docs/p4/01 D1「本地 HTTP+SPA」的 React 完整形态）
> 范围：把历史编辑器从单文件 HTML MVP（docs/p4/01 阶段③）升级到 React+Vite SPA——**步骤拖拽重排、试跑按钮、组件化架构、变量编辑面板强化**。
> 承接：业界 React 实现调研（2026-08-02，拖拽库/状态/构建/测试实证，含来源 URL）；docs/p4/02 形态调研（已选本地 HTTP+SPA）；现有后端 `/history/*` + mutation API（阶段①②）。
> 关联：docs/p4/01（方案 D1/阶段③）、docs/p4/02（形态调研）；`src/tree_walker/history_editor/`（后端 + MVP）。

## Context（为什么做）

阶段③交付的单文件 HTML MVP（`history_editor/static/index.html`）走通了编辑链路（列表/加载/改 text/删步/标注变量/detect/保存），但交互弱：

- **无拖拽重排**——步骤顺序固定，删步后想调整只能改 JSON
- **无试跑**——编辑完只能另开 `tw-tui --rerun` 验证，割裂
- **变量只读列表**——detect 结果只展示，不能在面板里改名/删除/导出 CSV 模板
- **JS 散在单文件**——250 行 vanilla JS 难扩展（加拖拽/试跑会膨胀失控）

本方案升级到 React+Vite SPA，对齐 docs/p4/01 D1 完整形态，强化上述四点。**后端 `/history/*` 端点 + mutation API（阶段①②）全部复用**，新增仅 `/history/rerun`（试跑）+ 一个等长断言。

## 业界实现调研锚点（2026-08-02 实证）

| 维度 | 推荐 | 理由（实证） | 备选 |
|---|---|---|---|
| 拖拽库 | **`@dnd-kit/core`+`@dnd-kit/sortable`**（经典 v6.x） | 2026 业界默认，~6KB，内置 Keyboard/PointerSensor（可访问性最好）；rbd 弃用后的事实标准 | `@hello-pangea/dnd`（rbd 维护 fork）；`@atlaskit/pragmatic-drag-and-drop`（~3.5KB，框架无关） |
| 拖拽库（禁用） | ❌ `react-beautiful-dnd` | Atlassian 2022 起弃用、无安全更新、不保证 React 19 兼容 | — |
| 状态管理 | **`useReducer`+Context**（MVP）→ Zustand（变复杂时） | 本地工具单一编辑器 state，一个 reducer 足够、零依赖 | ❌ Redux（过重） |
| 组件库 | **CSS Modules**（沿用 MVP 风格）+ **Radix 无样式原语**（Dialog/Popover 按需） | Python 仓库要轻、零侵入；shadcn/ui 拉 Tailwind 全套对次要前端偏重 | shadcn/ui（若不介意 Tailwind）；MUI（重，不推荐） |
| 测试 | **Vitest + @testing-library/react + MSW** | Vitest 是 Vite 原生测试运行器（零配置）；MSW 是 Vitest 官方 HTTP mock 首选 | 手写 fetch mock（端点少可省 MSW） |
| HTTP（dev） | **Vite `server.proxy` 代理 `/history`→aiohttp 8766** | 同源免 CORS，后端零改动 | 后端开 CORS（多此一举） |
| HTTP（prod） | **`vite build`→aiohttp `add_static`+catch-all 回退 index.html** | 单进程托管，无需 nginx | 反向代理 nginx |

**关键事实**：`@dnd-kit` 有两套 API——经典 `@dnd-kit/core`（2021 稳定、教程最多、对低维护工具最稳）vs 新 `@dnd-kit/react`（2025-04 beta、<1 年、API 在演进）。**TreeWalker 前端是次要、要少维护 → 选经典 `@dnd-kit/core`**。

## 现状（已就绪，复用）

| 已有 | 位置 | 升级时角色 |
|---|---|---|
| 后端端点 `/history/{list,load,save,detect}` + `/health` + `/` | `history_editor/server.py` | React SPA 直接 fetch（零改动） |
| mutation API（remove_step/update_action_params/merge_steps/add_manual_variable/remove_manual_variable） | `views.py` | 前端 reducer 镜像其语义（不调后端方法，前端 splice state + save） |
| `merge_variable_sources`（detect ∪ manual） | `variable_detector.py` | `/history/detect` 已返回合并结果，变量面板数据源 |
| `Agent.load_and_rerun(history_file, variables=None)` | `rerun.py:273` | 试跑端点薄封装它 |
| 单文件 HTML MVP | `history_editor/static/index.html` | 迁移映射到 React 组件（迁完由构建产物覆盖） |

## 组件架构 + 三栏布局

**布局选型**：Selenium IDE 式主从布局（业界三范式中对「动作列表+选中编辑+变量面板」适配度最高 ★★★★；UiPath 画布+Properties 过重，Chrome Recorder 单列卡片不适合多步表格）：

```
┌─ Toolbar（文件下拉/加载/检测变量/保存/试跑）─────────────────┐
├──────────────────────────┬───────────────────────────────┤
│ ActionList（左，主）       │ 右栏（从）                      │
│  ⠿ 1 click "搜索"         │ ┌─ ActionEditor（选中步）────┐ │
│  ⠿ 2 input_text "iPhone"  │ │ step/action_index          │ │
│  ⠿ 3 click "发布"         │ │ params[field] 编辑器        │ │
│  （⠿=拖拽手柄，整行选中）  │ │ 删步/合并步                 │ │
│                          │ └────────────────────────────┘ │
│                          │ ┌─ VariablePanel ─────────────┐ │
│                          │ │ Name|original_value|format  │ │
│                          │ │ CRUD + 导出 CSV 模板        │ │
│                          │ └────────────────────────────┘ │
└──────────────────────────┴───────────────────────────────┘
RunPanel（试跑结果：每步 ✓/✗ + 失败步 error + 截图缩略图）
```

**组件拆分**：`<Toolbar>` / `<ActionList>`（含 `<SortableStepRow>`）/ `<ActionEditor>` / `<VariablePanel>` / `<RunPanel>`。

**数据流（单一 reducer）**：
```
state = { history, loadedName, dirty, selected:{stepIdx,actionIdx}, variables, runResult }
actions: LOAD / SELECT / UPDATE_PARAM / MOVE_STEP / DELETE_STEP / MERGE_STEPS
         / SET_DIRTY / SAVE_DONE / DETECT_DONE / RUN_DONE
```
Context 暴露 `{state, dispatch}`，所有 mutation 走 reducer → 自动重渲三栏；`dirty` 标未保存、`selected` 驱动 `<ActionEditor>`。

## 三个关键交互

### 1. 拖拽重排（含 actions↔interacted_element 不变量守护）

**UX**：用**专用拖拽手柄**（不整行拖）——行内有选中点击/编辑框，整行拖会冲突、触屏误触；手柄配 `cursor: grab`、给键盘用户保留 up/down 按钮作非拖拽替代（WCAG 1.4.11）。

**实现（经典 @dnd-kit/core）**：
```tsx
// SortableStepRow.tsx — 仅手柄绑 listeners
const {attributes, listeners, setNodeRef, transform, isDragging} = useSortable({id: step.step_number});
<tr ref={setNodeRef} style={transformStyle} onClick={() => dispatch({type:'SELECT', step})}>
  <td className="grip" {...attributes} {...listeners}>⠿</td>
  <td>{step.step_number}</td><td>{action.name}</td><td>{elemDesc}</td>
</tr>

// ActionList.tsx
<DndContext sensors={[PointerSensor, KeyboardSensor]} collisionDetection={closestCenter}
  onDragEnd={e => { const {active, over} = e;
    if (over && active.id !== over.id) dispatch({type:'MOVE_STEP', from: idxOf(active.id), to: idxOf(over.id)}); }}>
  <SortableContext items={steps.map(s=>s.step_number)} strategy={verticalListSortingStrategy}>
    <tbody>{steps.map(s => <SortableStepRow key={s.step_number} step={s}/>)}</tbody>
  </SortableContext>
</DndContext>
```

**不变量守护（核心）**：TreeWalker 不变量是「每步 `model_output.actions` 与 `interacted_element` 等长按位对应」（views.py 注释）。
- **MVP 只做整步拖拽**——整步作为单元搬运，`actions[]`+`interacted_element[]` 同步移动，不变量天然保持（与 `merge_steps` 同理）。`reducer` 的 `MOVE_STEP` 把整步对象 `splice(from→to)`。
- **不做步内 action 拖拽**——那需在两个数组做同下标原子移动，复杂；留后续。
- **防御性兜底**：`_handle_save` 加 `assert len(actions)==len(interacted_element)`（当前 `load_from_dict` 老格式补 None 但不校验等长），作为前端 bug 的最后防线。

### 2. 变量编辑面板（仿 UiPath Variables 面板）

| UiPath 列 | TreeWalker 列 | 来源 |
|---|---|---|
| Name | `name`（可改名 → `manual_variables`） | 用户输入 |
| Type | `format`（string/number/email/...） | `/history/detect` |
| Default | `original_value`（detect 原值，只读） | `/history/detect` |

- 数据源：`GET /history/detect` 已返回 `detect ∪ manual`。
- 改名/新增 → 镜像 `add_manual_variable`（同名替换否则追加）；删除 → `remove_manual_variable`。前端走 reducer 标 `dirty`，保存随 history `POST /history/save`。
- **CSV 模板导出**：取 `variables` 的 name 列做表头，生成空行模板（对齐 `batch_rerun`「列头=变量名」）。
- 内联编辑用受控 `<input>` + onBlur 提交，避免每键重渲。

### 3. 试跑 / 运行（新增 `/history/rerun` + `<RunPanel>`）

**后端新端点**（薄封装 `load_and_rerun`）：
```python
# POST /history/rerun?name=<file>  body: {"variables": {...}}
async def _handle_rerun(request):
    name = request.query.get("name")
    if not name: return web.json_response({"error":"missing name"}, status=400)
    body = await request.json()
    agent = _build_agent()  # 复用 examples/csv_rerun.py 的 Agent 构造（LLMClient/BrowserSession）
    results = await agent.load_and_rerun(name, variables=body.get("variables"))
    return web.json_response({"results": [r.model_dump(mode="json") for r in results]})
```

**前端**：`<Toolbar>` 试跑 → `POST /history/rerun`（按钮 loading）→ `RUN_DONE(results)`；`<RunPanel>` 按 `step_number` 对齐 `<ActionList>` 行渲染 ✓/✗，失败步展开 `error`+`extracted_content` 并标红对应行。MVP 阻塞 `await`（`load_and_rerun` 串行返回全量）；步骤多/慢再升级 SSE 流式（`web.StreamResponse` 逐 step 推）。**不做断点/单步**（Chrome Recorder 那套成本高，重放端无单步原语，留 P2）。

## 构建集成

**Dev（Vite proxy，同源免 CORS）**——`history_editor_ui/vite.config.ts`：
```ts
server: { port: 5173, proxy: {
  '/history': { target: 'http://localhost:8766', changeOrigin: true },
  '/health':  { target: 'http://localhost:8766', changeOrigin: true },
}}
```
前端 `fetch('/history/list')` 相对路径，dev 经代理、prod 同源直连，**代码零分支**。工作流：终端1 起 aiohttp（8766）；终端2 `npm run dev`（5173，浏览器开这个）。

**Prod（vite build → aiohttp 托管 + SPA fallback）**——`server.py` 增：
```python
_DIST = Path(__file__).parent / "static"
app.router.add_static("/assets", _DIST / "assets")     # vite 构建的 JS/CSS 在 /assets
app.router.add_get("/", _serve_index)
app.router.add_get("/{tail:.*}", _serve_index)         # SPA fallback：未知路径回 index.html
```
catch-all 必须最后注册；`/history/*` 已先注册故优先。aiohttp 不自动认 `index.html`，fallback 显式 `web.FileResponse`（issue #1220）。

**工程位置**：新建 `history_editor_ui/`（仓库根，与 `recording_extension/` 同级、独立 npm 工程）。`.gitignore` 增 `history_editor_ui/node_modules/`、`history_editor_ui/dist/`。

**构建产物策略（推荐 B：dist 不进 git）**：发版/CI 跑 `npm run build` 把 `dist/` 拷进 `history_editor/static/`（或打包进 wheel）。理由：Python 仓库不追踪编译产物；现 `static/index.html` 是手写 MVP，迁 React 后由构建覆盖。开发期跑 dev server（proxy），不需 build。备选 A（dist 进 git）：仅当要「pip 装完即点开」的分发包时才值。

## 文件改动（按依赖序）

**后端（小）**
- `history_editor/server.py`：加 `POST /history/rerun`（薄封装 `load_and_rerun`，复用 examples 的 Agent 构造）；`_handle_save` 加等长断言；prod 静态托管加 catch-all fallback。
- `tests/test_history_editor_server.py`：加 rerun 端点测试（mock `load_and_rerun`）+ 等长断言测试。

**前端（新工程 `history_editor_ui/`）**
- 脚手架：`package.json`（react/react-dom/@dnd-kit/core/@dnd-kit/sortable/vite/@vitejs/plugin-react/typescript）、`vite.config.ts`（含 proxy）、`tsconfig.json`、`index.html`、`src/main.tsx`。
- 组件：`src/App.tsx`（reducer + Context）+ `Toolbar/ActionList/SortableStepRow/ActionEditor/VariablePanel/RunPanel.tsx` + `api.ts`（fetch 封装）+ `types.ts`（AgentHistoryList 等类型）。
- 测试：`src/__tests__/`（Vitest + Testing Library + MSW，覆盖 reducer 逻辑 + 关键组件交互）。
- 构建脚本：`scripts/build_editor.ps1`（`npm ci && npm run build && copy dist→static`）。

**文档**：README「历史可视化编辑」节补一句「完整 React SPA 见 docs/p4/03」。

## 工作量与分阶段（单人估）

| 阶段 | 范围 | 估时 |
|---|---|---|
| **MVP 升级** | Vite+React+TS 脚手架；迁 load/save/detect/edit-text/删步/变量列表；`useReducer` 状态；Vite proxy dev；aiohttp 静态托管+fallback；**步骤拖拽重排**（整步，@dnd-kit 经典）；手动测试跑通 | **3–5 人日** |
| **完整** | + 变量 CRUD 面板 + CSV 模板导出；+ `/history/rerun` + `<RunPanel>` 每步结果/失败高亮；+ Vitest+Testing Library+MSW 组件测试（对齐项目 85% 覆盖率）；+ 构建/产物脚本 | **+5–7 人日（共 8–12 人日）** |

**风险**：aiohttp SPA fallback 与现有 `/` 路由注册顺序需测；`/history/rerun` 涉及真实 BrowserSession（试跑起浏览器），CI 测试需 mock。

## 验证

1. 前端单测：`cd history_editor_ui && npm test`（Vitest，reducer + 组件 + MSW mock 端点）
2. 后端：`uv run python -m pytest tests/test_history_editor_server.py -v`（含新 rerun + 等长断言）
3. 全量回归：`uv run python -m pytest tests/ -q`（确认 server.py 改动不破）
4. dev 冒烟：终端1 `uv run python examples/serve_history_editor.py`；终端2 `cd history_editor_ui && npm run dev`；浏览器开 `http://localhost:5173/` → 加载历史 → 拖拽重排 → 改 text → 标注变量 → 试跑 → 保存
5. prod 冒烟：`npm run build` + 拷 dist→static；`uv run python examples/serve_history_editor.py`；浏览器开 `http://127.0.0.1:8766/` 验证构建产物托管

## 不在本期范围

- 断点 / 单步执行 / "从此处重放"（Chrome Recorder 级，重放端无单步原语）→ P2
- 步内 action 拖拽（需双数组同下标原子移动）→ 后续
- SSE 流式试跑（步骤多/慢时再升级）
- shadcn/ui / Tailwind（MVP 用 CSS Modules 保持轻量；需精致 UI 再评估）
- `/history/rerun` 的截图嵌入（`screenshot_path` 当前恒 None，待截图通道打通）

## 来源（调研实证，2026-08-02）

- 拖拽：[pkgpulse dnd-kit vs rbd vs Pragmatic 2026](https://www.pkgpulse.com/guides/dnd-kit-vs-react-beautiful-dnd-vs-pragmatic-drag-drop-2026)、[@hello-pangea/dnd](https://www.npmjs.com/package/@hello-pangea/dnd)、[dnd-kit changelog](https://dndkit.com/changelog)
- 构建：[Vite server.proxy](https://vite.dev/config/server-options)、[aiohttp quickstart](http://docs.aiohttp.org/en/stable/web_quickstart.html)、[aiohttp #1220](https://github.com/aio-libs/aiohttp/issues/1220)
- 业界 UI：[Selenium IDE](https://www.selenium.dev/documentation/legacy/selenium_ide/)、[Chrome Recorder reference](https://developer.chrome.com/docs/devtools/recorder/reference)、[UiPath Variables panel](https://docs.uipath.com/studio/standalone/latest/user-guide/the-variables-panel)、[Playwright trace viewer](https://playwright.dev/docs/trace-viewer)
- 拖拽 a11y：[NN/g drag-drop](https://www.nngroup.com/articles/drag-drop/)、[Primer drag-drop a11y](https://primer.style/accessibility/patterns/drag-and-drop/)
- 测试：[Vitest mocking (MSW)](https://vitest.dev/guide/mocking/requests)、[MSW](https://mswjs.io/docs/quick-start/)
