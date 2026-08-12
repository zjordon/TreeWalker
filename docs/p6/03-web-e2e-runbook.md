# P6 Web 真机 e2e 操作手册

> 用途：P6 M3 的验收门——真机跑通 web 前端的 **live agent 控制台**（实时步骤/截图/日志 + 暂停/停止/录制存盘）。
> 通过本 e2e 后，方可推进 M6 的「TUI 下线」（见 `02-implementation-plan.md`）。
> 后端（M1/M2：`/task/*` SSE + 事件化日志 + 截图推帧）+ 前端（M3：AppShell + RunView）需联调验证。

---

## 0. 前置（一次性确认）

- 仓库在 `D:\dev\git\z_jordon\TreeWalker`，依赖已装：`uv sync --extra vision --extra docs`（**vision 含 Pillow，供截图降采样**）。
- 前端依赖已装：`web_ui/node_modules` 存在（否则 `npm install --prefix web_ui`）。
- `.env` 的 LLM key / model 配好（agent 运行要调 LLM）。

---

## 1. 起 Chrome 调试实例（agent 的「手」）

**新开一个 PowerShell**，跑（**关键：单独 `--user-data-dir`，否则调试端口不生效**）：

```powershell
& "$env:ProgramFiles\Google\Chrome\Application\chrome.exe" `
    --remote-debugging-port=9222 `
    --user-data-dir="$env:TEMP\tw-chrome" `
    --no-first-run --no-default-browser-check
```

> Chrome 路径不在 `Program Files` 就换实际路径（如 `Program Files (x86)` 或 `%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe`）。
> 这个 Chrome 窗口是 **agent 操作的浏览器**，开着不用管。`tw-web` 默认 `--cdp-port=9222` 连它（`config.py` 经 `CDP_PORT` → `_fetch_ws_url`）。

---

## 2. 起后端（tw-web，端口 8766）

**第二个 PowerShell**（仓库根）：

```powershell
cd D:\dev\git\z_jordon\TreeWalker
uv run tw-web
```

看到这行就成了：

```
TreeWalker web: http://127.0.0.1:8766/  (Chrome CDP 端口=9222)
======== Running on http://127.0.0.1:8766 ========
```

---

## 3. 起前端 dev 服务（端口 5173）

**第三个 PowerShell**：

```powershell
cd D:\dev\git\z_jordon\TreeWalker\web_ui
npm run dev
```

看到 `Local: http://127.0.0.1:5173/` 即可。（`vite.config.ts` 已代理 `/task` `/history` `/health` → 8766。）

> **prod 模式替代**（不想开三个终端）：在仓库根跑 `.\scripts\build_editor.ps1`（`npm run build` + 拷 `dist`→`src/tree_walker/history_editor/static`），然后只跑步骤 1+2，浏览器开 **http://127.0.0.1:8766/**，省掉步骤 3。

---

## 4. 跑任务 + 验收清单

**用你日常的浏览器**（不是步骤 1 那个调试 Chrome）开 **http://127.0.0.1:5173/**，顶部停在「**探索**」模式。

### 场景 A — 基础 live 流

输入框填一个简单任务（先**不开**录制）：

```
打开 https://www.bing.com 搜索"天气"，告诉我第一个结果标题
```

点 **▶ 发送**，逐项确认：

- [ ] 顶部状态变 `运行中…`
- [ ] **步骤时间线**逐条冒出并循环：`step_start` → `model_call` → `model_result`（含 `action_name`）→ `tool_call` → `tool_result`（✓/✗）→ `step_end`，多步重复
- [ ] **实时浏览器视图**出现截图帧，每步更新（带 Bing 页面）
- [ ] **日志流**冒出 `tree_walker.*` 的 INFO 日志
- [ ] 任务结束 → 状态 `完成`，出现 **新任务** 按钮

### 场景 B — 运行控制

再发一个任务，中途：

- [ ] 点 **⏸ 暂停** → 状态 `已暂停`、时间线停住
- [ ] 点 **▶ 恢复** → 状态回 `运行中…`、继续冒步骤
- [ ] 点 **⏹ 停止** → 状态结束、出 **新任务** 按钮

### 场景 C — 录制 + 流程库

勾上 **录制轨迹**，再发一个能完成的任务（如 `打开 https://example.com 并告诉我 H1 标题文字`），等它完成：

- [ ] 状态显示 `完成，已存 yyyyMMddHHmm.json`
- [ ] 点顶部 **流程库** → 左侧 sidebar 出现刚存的 json，点开它
- [ ] **编辑** tab 能看到动作列表；**重放** tab 有「试跑」按钮；**详情** tab 有元信息 + 步骤 master-detail，点步骤能看右侧详情

---

## 5. 看事件流（可选 / 排障）

你日常浏览器里 F12 → Network → 找 `events` 那条（类型 `eventstream`）→ 应是持续 SSE；EventStream 面板能看到 `step_start` / `screenshot` / `log` / `done` 等帧。

---

## 6. 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| 发送即报 `Chrome 未以 --remote-debugging-port 启动` | 步骤 1 的 Chrome 没起、端口不是 9222、或被别的 Chrome 抢了 user-data-dir → 确认是**新窗口**且 `--user-data-dir` 是独立目录 |
| 步骤/日志出，但**无截图** | 多半 `take_screenshot` 失败 → 看**步骤 2 后端终端**的日志（`screenshot capture failed`）；Pillow 缺失只影响降采样，不影响有帧 |
| 完全无事件 / 一直转 | 步骤 2 后端没起、或 5173 没代理上 → F12 Network 看 `/task/events` 是否 200 eventstream；后端终端有无报错 |
| `npm run dev` 起不来 | 端口 5173 被占 → `npm run dev -- --port 5174`，浏览器相应换端口 |
| LLM 报错 / agent 不动 | `.env` 的 key/model 没配好 → 看后端终端的 LLM 异常 |

---

## 7. 验收结论

场景 A/B/C 全绿 ⇒ web live 控制台可用，P6 M3 验收通过 ⇒ 可推进 M6「TUI 下线」。
任一场景失败 ⇒ 把**步骤 2 后端终端**的报错 + 浏览器 F12 Network 的 `/task/events` 状态贴出来定位。
