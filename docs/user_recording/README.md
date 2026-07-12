# 用户操作录制 → 历史重放（技术方案设计）

> **状态：设计阶段，尚未实施。** 本文档记录经多轮技术可行性讨论后定稿的方案架构与实施路线，作为后续开发的依据。
>
> 与 [历史重放（rerun_history）](../rerun_history/README.md) 的关系：rerun_history 的录制来源是 **agent 自动探索**；本方案把录制来源换成 **录制用户真实操作**，录制产物仍是 `AgentHistory`，重放侧完全复用现有 `rerun_history`，**不改重放器核心**。

---

## 1. 背景与目标

现有 `rerun_history` 功能的录制来源是 agent 自动探索——每步由 LLM 决策驱动。实际使用暴露两个问题：

1. **路径不稳定**：同一个任务每次探索的操作路径都不同（LLM 决策随机）。
2. **可能失败**：LLM 推理本身有失败率，录制阶段就跑不通。

**目标**：把录制来源从"agent 自动探索"换成"录制用户真实操作"。路径完全由人的操作决定，**消除决策环节**，从而稳定可靠。

**结论：技术可行，且能直接解决路径不稳定这个核心痛点。** 录制把"LLM 决策随机"这个不稳定根源整个移除——这是质变，不是优化。

---

## 2. 关键技术判断（可行性依据）

TreeWalker 重放消费的是 `AgentHistory`（`model_output.actions` + `interacted_element` 元素投影），重放器在当前页用**五级匹配**把"录制时的元素"重新定位出来再真实执行：

```
EXACT(element_hash) → STABLE(stable_hash) → XPATH → AX_NAME → ATTRIBUTE
```

录制方案要让 Chrome 扩展产出这份格式。可行性 hinges on 三个判断：

### 2.1 五级匹配是双端比较

录制侧存的指纹 vs 重放时 TreeWalker 实时算的指纹，必须相等才命中。`element_hash`/`stable_hash`（`src/tree_walker/browser/views.py:547/566`）的输入 = 父级标签路径 + `STATIC_ATTRIBUTES` 过滤属性 + **`ax_name`**，再 sha256。其中 `ax_name` 来自 CDP 的 `Accessibility.getFullAXTree`（`src/tree_walker/browser/dom.py:294`）。

### 2.2 录制侧若用原生 DOM，EXACT/STABLE 大概率对不上

扩展用原生 DOM 只能**近似**算 accessible name，与 CDP 不完全一致 → hash 对不上 → 前两级失效，只能降级到 XPATH/AX_NAME/ATTRIBUTE。强动态 SPA（随机 class、节点飘移）上鲁棒性弱于 agent 自录。

### 2.3 化解：指纹计算放回 Python 后端（本方案采用）

让录制侧的指纹也由 TreeWalker Python 后端算——**同一份 `dom.py` + `compute_stable_hash` 代码**，与重放侧 100% 一致，零移植、零同步负担，EXACT/STABLE 天然有效。前端只负责轻量事件采集。这是"全对齐"路线的核心。

> 备选路线（已排除）：
> - **扩展独立移植 hash 算法到 JS**：需移植 1000+ 行 `_build_enhanced_dom_tree`，且要跟上游同步，脆且贵。
> - **扩展用 `chrome.debugger` 拉三源**：页面会有"正在被调试"黄条，且仍需移植算法。本方案用 `--remote-debugging-port` 绕开黄条。

---

## 3. 定稿架构

混合架构：**扩展（轻量，无 debugger 权限）+ Python 后端（remote-debugging-port CDP 直连）**。

```
用户浏览器 (chrome --remote-debugging-port=9222)        本机 Python 后端
┌───────────────────────────────────┐      ┌──────────────────────────────────────┐
│ 扩展 (无 debugger 权限)            │      │ BrowserSession(ws_url=ws://9222)      │
│  · popup: 开始/停止/标记完成        │ http │  · 收事件 → browser.get_state()       │
│  · background: POST 事件 / 状态    │───→  │    (复用 _build_enhanced_dom_tree)    │
│  · content script: 监听 DOM 事件   │      │  · 按 xpath 在 selector_map 定位节点  │
│    发: 操作类型 + 目标 xpath 线索  │      │  · DOMInteractedElement 投影 + 指纹   │
└───────────────────────────────────┘      │  · 拼 AgentHistory → 落盘 history.json│
  (零指纹逻辑, 零 CDP)                     └────────────┬─────────────────────────┘
                                                         │ 现有 load_and_rerun
                                                         ▼
                                                  重放 (指纹天然对齐)
```

**三个关键优势（均已代码验证）**：

| 优势 | 说明 |
|---|---|
| 零移植 | 后端直连 CDP，直接 `browser.get_state()` 间接复用 `_build_enhanced_dom_tree` + `compute_stable_hash`，树构建逻辑一行不搬 |
| 全对齐 | 录制侧指纹与重放侧用同一份 Python 代码算出，`EXACT`/`STABLE` 匹配天然有效 |
| 无黄条 | 用 `--remote-debugging-port` 直连，绕开 `chrome.debugger` 的"正在被调试"提示 |

### 数据流

1. 用户用 `--remote-debugging-port=9222 --user-data-dir=<录制专用 profile>` 启动自己的 Chrome（profile 内提前登录目标站点）。
2. 启动 Python 后端 → 经 `_fetch_ws_url` 从 `http://localhost:9222/json/version` 发现完整 `webSocketDebuggerUrl`（裸 `ws://localhost:9222` 会 404）→ `BrowserSession(ws_url=...)` 连上浏览器（CDP），同时开本地 HTTP 服务（如 `http://localhost:8765`）接收扩展事件。
3. 用户点扩展 popup 的"开始录制"→ content script 注入 → 监听 DOM 事件。
4. 每次用户操作，content script 采集「操作类型 + 目标元素线索（tag/id/selector/xpath/...）+ 操作参数 + frame 路径」→ `chrome.runtime.sendMessage` → background → POST 后端。
5. 后端收到事件：`get_state()` 拉当前页 → 在 `selector_map` 里按 xpath 定位节点 → `DOMInteractedElement.load_from_enhanced_dom_tree(node).to_dict()` 取指纹投影 → 构造一条 `AgentHistory` 追加。
6. 用户点"停止录制"→ 后端可选补一条 `done` → `AgentHistoryList.save_to_file()` 落盘到 rerun 目录。
7. 重放：`agent.load_and_rerun("xxx.json", variables=...)`，走现有重放器，五级匹配生效。

### 3.1 扩展技术栈：WXT

扩展用 [WXT](https://wxt.dev)（基于 Vite 的现代 Web Extension 框架）开发，替代手写 `manifest.json` + 散文件。选型理由：

- **manifest 自动生成**：权限、entrypoint 选项在 `wxt.config.ts` 和各 entrypoint 文件内声明，WXT 构建时生成 `manifest.json`，少一处维护。
- **entrypoint 约定**：`entrypoints/background.ts` / `entrypoints/popup.html` / `entrypoints/content.ts` 按文件名识别类型，结构清晰。
- **TypeScript + auto-import**：`defineBackground` / `defineContentScript` 等 helper 自动导入，content script 里写 xpath 生成、事件去噪等逻辑有类型保障。
- **content script 配置直接对应本方案需求**：`allFrames: true`（iframe 穿透）、`world: 'ISOLATED' | 'MAIN'`（如需监听 SPA 路由可切 MAIN）、`runAt`。

关键配置（`wxt.config.ts`）：

```ts
export default defineConfig({
	manifest: {
		name: 'TreeWalker Recorder',
		permissions: ['activeTab', 'tabs', 'scripting', 'storage'],
		host_permissions: ['http://localhost:8765/*', 'http://*/*', 'https://*/*'],
		// 不含 'debugger' —— 全对齐由后端 CDP 负责
	},
});
```

entrypoint 与本方案职责映射：

| WXT entrypoint | 文件 | 本方案职责 |
|---|---|---|
| Background | `entrypoints/background.ts` | 录制状态、把 content script 事件 POST 到后端 `http://localhost:8765`、停止前 flush |
| Content Script | `entrypoints/content.ts` | `defineContentScript({ matches: ['<all_urls>'], allFrames: true, runAt: 'document_idle', main() {...} })`，监听 DOM 事件、采集 xpath 线索 |
| Popup | `entrypoints/popup/index.html` + `popup/main.ts` | 开始/停止/标记完成按钮、已录步数显示 |

> **MV3 友好**：扩展↔后端用 **HTTP**（content script 采集 → `chrome.runtime.sendMessage` → background → POST 后端），无状态、SW 按需唤醒，规避 WebSocket 长连接在 SW 休眠时断开的麻烦。后端↔浏览器的 CDP 仍用 ws（协议要求）。
>
> content script ↔ background 通信用 WXT messaging（或原生 `browser.runtime.sendMessage`）；background 是唯一与后端通信的入口。

### 3.2 参考实现：Browser-BC 扩展（可直接借鉴）

`D:\dev\git\ai\Browser-BC\extension` 已有一个**成熟的 WXT 采集层**（技术栈 TS + WXT(MV3) + React + Dexie，详见 `out/扩展采集层技术文档.md`），其三层结构（page context / content script / background SW）与本方案高度吻合，下列组件可直接借鉴/移植：

| Browser-BC 组件 | 文件 | TreeWalker 用途 |
|---|---|---|
| WXT 三层骨架 | `src/entrypoints/{background,content,injected,popup}` | 扩展整体结构（印证 §3.1 选型） |
| Action 采集器 | `src/capture/action-recorder.ts` | DOM 操作归一化（click/input/keydown/scroll）+ 去噪（keydown 只录快捷键、scroll 250ms 节流）|
| 元素引用 + 选择器 | `src/capture/selector.ts`（`buildElementRef`/`bestSelector`/`xpathFor`） | 生成元素线索（tag/id/classes/role/name/text/selector/xpath/rect），比单 xpath 更利于后端定位 |
| SPA 导航 hook | `src/capture/navigation-{events,injected,recorder}.ts` | 抓 `history.pushState/replaceState`（content script 抓不到），映射成 `navigate` |
| MV3 SW 崩溃恢复 | `src/recording/recorder.ts` + `src/storage/db.ts`（`recoverActiveTraceId`/`ensureRecovered`） | IndexedDB 缓冲事件 + 启动恢复 activeTraceId，录制不丢 |
| 停止前 flush | `src/entrypoints/background.ts`（`flushRecordingTabs`） | 停止录制前冲刷 in-flight 事件，保证最后几步入库 |

**TreeWalker 相对 Browser-BC 的裁剪与差异**（明确边界，避免照搬）：

1. **精简采集器**：不要 `network-*` / `mutation-summary` / `form-summary` / `download-events` / `dom-snapshot`（重放用不到，后端自己 `get_state()`）。只保留 **action-recorder + navigation**。
2. **实时拼装，非批量上传**：Browser-BC 是「采集 → IndexedDB → gzip 分块 HTTP 上传 → 服务端蒸馏」；TreeWalker 是「事件实时 POST 后端 → 后端 CDP 算指纹 → 拼 `AgentHistory` → 落盘 `history.json`」。**不需要上传协议（init/chunks/finalize/manifest）**；IndexedDB 仅在要 SW 崩溃恢复时借用（可选增强）。
3. **保留 input 值**：Browser-BC「采集宽、脱敏严」（password/email 等抹除）；TreeWalker 录制的是**要重放**的操作，**必须保留 `input_text` 的 `text`**（`detect_variables` 要扫），脱敏层大幅精简（仅 password 字段可选脱敏）。
4. **xpath 格式对齐（注意）**：Browser-BC `xpathFor` 产出 **`/html/body/...`**（前导 `/`）；TreeWalker `EnhancedDOMTreeNode.xpath` 是 **`html/body/...`**（无前导，且 iframe 边界停止）。后端匹配前归一化（strip 前导 `/`），扩展侧 xpath 仅作「帧内定位线索」，跨帧靠 `frame_path`。

**为何实时处理（不是 Browser-BC 那样批量上传）**：全对齐指纹有两条硬约束——① 指纹必须**后端 CDP 算**（扩展原生 DOM 算不出与 CDP 一致的 `ax_name` → hash 对不上）；② 指纹必须在**事件发生瞬间**的页面状态算（用户点的元素那一刻在 `selector_map` 里）。合起来 → 事件必须实时到后端、后端立即 `get_state` 算指纹。Browser-BC 能批量是因为它**不算指纹**（离线蒸馏用 LLM + 事件附带的 DOM 快照）。

| 维度 | Browser-BC | TreeWalker |
|---|---|---|
| 后端连浏览器 | 否 | **是（CDP）** |
| 算指纹 | 不算 | **后端 CDP 算（全对齐）** |
| 事件时效性 | 不要求（快照随事件存） | **强要求** |
| 传输 | 批量（IndexedDB + gzip 分块） | 实时（每事件 POST） |

若照搬批量：扩展缓冲事件、录制结束才上传 → 后端收到时页面早已被后续操作改掉 → `get_state` 拿到非事件瞬间的页面 → 目标元素在当前 `selector_map` 里可能已不存在 → **指纹算不出/算错**（事件与页面状态时序错位）。代价：每事件一次 `get_state`（重）+ 时序竞态（SPA 跳转后页面变）；MV3 SW 休眠已用无状态 HTTP 规避。**可借鉴** Browser-BC 的 IndexedDB 做**发送队列**（后端短暂不可达不丢事件），但不改变「指纹必须实时算」的核心——时序竞态的缓解见 §4 阶段 3 滚动 `selector_map` 缓存。

### 3.3 服务端参考：Browser-BC `server.py`（有限借鉴）

`D:\dev\git\ai\Browser-BC\server\server.py`（FastAPI 本地服务，详见 `out/server-py-逻辑梳理.md`）是一个 trace 摄取 + 蒸馏服务。**它和 TreeWalker 后端业务本质不同**，核心业务大多不适用——只借鉴通用 HTTP 服务工程实践。

**业务差异（划清边界）**：Browser-BC 服务端**不连浏览器**，只接收扩展上传的离线 trace → LLM 蒸馏成 Claude 技能（`SKILL.md`）→ 装到 `~/.claude/skills` → MCP 接入。TreeWalker 后端**必须连浏览器** CDP 算指纹——这是 TreeWalker 的独特价值（全对齐），也是两者最大的架构区别。

**可借鉴（通用工程实践）**：

| 实践 | Browser-BC 实现 | TreeWalker 后端用法 |
|---|---|---|
| HTTP 服务选型 | FastAPI + uvicorn（`127.0.0.1:8099`） | TreeWalker 无现成 web 框架（`pyproject.toml` 仅 cdp-use/anthropic/pydantic/textual/click/markdownify），需新增：首选 **aiohttp**（单依赖、纯 async、与 CDP `get_state` 共享 event loop）；或借鉴 BB 用 **FastAPI+uvicorn**（复用已有 pydantic 做事件 schema 校验） |
| 原子落盘 | `_atomic_write`（tmp + `os.replace`） | 写 `history.json` 走原子写，防半截 JSON |
| 零配置鉴权 | `_load_api_keys` 默认 key 种入，扩展零配置连 | 可选：默认 Bearer token 防 localhost 上其他进程乱发事件 |
| 长驻健壮性 | `_ResilientStream`（stdout 断管道不崩）+ `RotatingFileHandler` 日志双写 | 若后端做长驻服务/sidecar，借鉴日志轮转 + 输出容错 |

**明确不借鉴（业务不相关）**：

- **可恢复上传协议**（`init`/`chunks`/`finalize`/sha256 幂等断点续传）：TreeWalker 是**实时事件流**（每条事件到达立即 CDP 算指纹拼 `AgentHistory`），非批量上传（§3.2 差异 2）。
- **蒸馏管线 / 技能安装 / MCP 集成**（`harness` 的 LLM 蒸馏、`~/.claude/skills`、Claude Desktop/Code/Codex 配置写入）：与 TreeWalker 无关。
- **控制面板 SPA 托管 / `config.json` 白名单配置子系统**：TreeWalker 用扩展 popup 做 UI 即可，MVP 不需要。

> TreeWalker 后端的独特性在 **"连浏览器 + 实时算指纹"**，这部分 Browser-BC 服务端没有对应实现——靠复用 TreeWalker 自身的 `BrowserSession` + `_build_enhanced_dom_tree`（§5）。

---

## 4. 实施路线（分阶段）

### 阶段 1：MVP——验证核心管线

用最小代价证明"扩展发事件 → 后端算指纹 → 拼 history → rerun 重放"端到端跑通。只覆盖 `click` + `input_text` 两类操作，去噪从简。

**后端**（`src/tree_walker/recorder/`）
- `server.py`：本地 HTTP 服务。TreeWalker 无现成 web 框架，新增 **aiohttp**（首选，单依赖纯 async）或 **FastAPI+uvicorn**（借鉴 Browser-BC，复用 pydantic 校验事件 schema），见 §3.3。
- `recorder.py`：`Recorder` 类。构造时建 `BrowserSession(ws_url=...)`；收到事件 → `await browser.get_state(include_screenshot=False)` → 取 `state.dom_state.selector_map` → 调 `locator` 定位 → 拼 `AgentHistory` → 追加到 `AgentHistoryList`；停止时 `save_to_file`。
- `locator.py`：`locate_by_xpath(xpath, selector_map) -> EnhancedDOMTreeNode | None`。复用 `rerun.py:545-549` 的 XPATH 匹配思路（`e.xpath == xpath`），命中多个时按 bounds 就近（参考 `_nearest_idx`）。
- `event_mapper.py`：事件 → action 的最小映射（click / input_text 两个）。

**扩展**（WXT 项目 `recording_extension/`，骨架与采集组件借鉴 Browser-BC，见 §3.2）
- `wxt.config.ts`：声明 permissions / host_permissions（不含 `debugger`）。
- `entrypoints/background.ts`：`defineBackground` 内维护录制状态、把事件 POST 到后端、停止前 flush（借鉴 Browser-BC `background.ts`）。
- `entrypoints/content.ts`：`defineContentScript({ allFrames: true, runAt: 'document_idle', ... })` 装配采集器（借鉴 `action-recorder.ts`）；MVP 只监听 `click`（mousedown 去重）和 `input`（change 触发、连续输入合并），用 `buildElementRef`（借鉴 `selector.ts`）采集元素线索 + 参数，经 messaging 发 background。
- `entrypoints/popup/index.html` + `popup/main.tsx`：开始 / 停止录制按钮（借鉴 Browser-BC 的 React popup）。

**元素线索生成（content script 侧）**：直接借鉴 Browser-BC `selector.ts` 的 `buildElementRef`（tag/id/classes/role/name/text/bestSelector/xpath/rect）。xpath 只用作"录制瞬间定位线索"，跨会话稳定性交给后端算的指纹，JS 版无需和 Python `EnhancedDOMTreeNode.xpath`（`views.py:354-373`）逐字节一致，录制瞬间唯一标识节点即可。**格式注意**：后端匹配前对 xpath 归一化（strip 前导 `/`，见 §3.2 差异 4）。

**阶段 1 验收**：
- 启动 Chrome(9222) → 启动后端 → 装扩展 → 在一个简单表单页点击输入框、输入文字、点提交 → 停止。
- 检查 `history.json`：每步 `model_output.actions` 有 name+params.index；`interacted_element` 有 `element_hash`/`stable_hash`（非空，证明后端算出来了）。
- `load_and_rerun` 重放该 json，表单被重新填写提交，成功。

### 阶段 2：补全动作覆盖 + 去噪 + iframe 穿透

- 扩展补全事件映射（见 §6 全部动作）。
- `event_mapper.py` 加去噪规则：hover/focus 无后续操作的丢弃；同一输入框连续 keystroke 合并为一条 `input_text`（带 `clear`）；短时重复点击归一；纯滚动阈值过滤。
- iframe / shadow DOM：content script 在 `defineContentScript` 设 `allFrames: true`；事件带上 `frame_path`（从顶层到当前 frame 的定位链），后端定位时跨 frame 解析。
- 文件上传：拦截 `<input type=file>` 的 `change`，取文件名 → `upload_file`（扩展只能拿文件名不能拿完整路径，后端用约定目录解析）。

> **✅ 阶段 2 已实现（2026-07-11，执行方案见 [stage2_plan.md](stage2_plan.md)）**。实现笔记（与上文的差异/澄清）：
>
> - **去噪分层**：扩展侧实时去噪（input 400ms 合并 / scroll wheel 累计节流 / send_keys 仅录组合键+命名非打印键）；后端 `event_mapper.denoise_steps` 在 `Recorder.stop()` 落盘前再过一遍——合并相邻同 index `input_text`、折叠短时（0.5s）重复 `click`、合并同方向 `scroll`（amount 求和 clamp 1-10）、重排 `step_number`。`hover/focus` 不在映射表（`map_event` 返回 `None` 即丢弃）。
> - **upload 约定目录**：扩展只采 `files[0].name`（浏览器安全限制不给完整路径），后端 `_resolve_upload_path` 拼成 `<rerun_history_dir>/uploads/<文件名>`（`record_upload_dir` 配置 / `AGENT_RECORD_UPLOAD_DIR` 环境变量可覆盖）；重放前用户须把文件放进该目录。
> - **go_back 折叠进 navigate**：`popstate` 无法可靠区分「后退按钮」vs「SPA 回退」，统一记 `navigate(url)`（重放落点同为某 URL，且 `navigate(url)` 比 `go_back` 依赖历史栈更稳）。navigate 的 SPA 捕获经 **MAIN-world 注入脚本** hook `pushState/replaceState`（content script 在 ISOLATED world 抓不到）+ content 直听 `popstate/hashchange`，跨 world 用 `tw:nav` CustomEvent 桥接（共享 window）。
> - **iframe 走 rect 就近（best-effort）**：`allFrames:true` 采集 + 扩展发 `is_top_frame` 信号；后端不引入复杂 `frame_path` 解析，复用现有 rect 就近（`dom.py:758-767` 已把 iframe 内容节点 bounds 加 iframe 偏移成视口坐标，跨 frame 同名 `html/body/...` xpath 时能正确消歧）。深嵌套跨源 iframe 仍可能误选，阶段 3 滚动 `selector_map` 缓存可缓解。
> - **switch_tab/close_tab 的 tabId 对齐**：扩展 `chrome.tabs` 事件给的是 Chrome tabId，background 发目标 tab 的 **url**；后端 `_resolve_tab_id` 用 `get_tabs()` 按 url 匹配 CDP page 取 `target_id[-4:]`（重放侧期望格式）。close_tab 的被关 tab url 由 background `tabUrlCache` 提前记住（onRemoved 时 tab 已销毁）。

### 阶段 3：鲁棒性（时序对齐）+ 多标签 + UX

- **时序对齐**（关键风险）：后端维护**滚动 `selector_map` 缓存**——录制期间对每个活动 target 短间隔（如 300ms）刷新一次 `get_state()`；事件到达时用最近一次缓存（点击前的页面状态）定位，规避"SPA 跳转后页面已变、定位不到目标节点"的竞态。MVP 的"事件到达立即 get_state"作为 fallback。
- **多标签**：后端跟踪多个 target；扩展 background 聚合所有标签 content script 事件并带 `tab_id`；`switch_tab`/`close_tab` 映射。
- **UX**：popup 显示已录步数；支持"删除最后一步"（误操作回退）；"标记任务完成"按钮补 `done`（带 success）。
- **变量检测**：录制停止后调 `AgentHistoryList` 的 `detect_variables`（`variable_detector.py`，已扫 `input_text` 的 `text` 字段），自动产出可替换变量清单，重放时 `variables={...}` 替换。

### 阶段 4：测试 + 文档

- 单元测试（`tests/recorder/`，覆盖率 >85%）：
  - `locator`：xpath 命中 / 多候选就近 / 未命中。
  - `event_mapper`：每类事件→action 映射、去噪规则（合并、过滤）、边界。
  - `AgentHistory` 构造：model_output 必填字段齐全、interacted_element 投影正确、state_summary.url 存在。
  - 集成测试（mock CDP + mock HTTP）：模拟事件流 → 断言产出的 history 结构 → 喂 mock 重放器断言 index 重映射。
- 文档：补充本目录下分篇设计文档（结构对齐 `docs/rerun_history/`），含协议、启动方式、与 rerun 的衔接、局限。

---

## 5. 关键复用物（已存在，直接接线）

| 复用物 | 位置 | 用途 |
|---|---|---|
| `BrowserSession(ws_url=...)` + `browser.get_state()` | `src/tree_walker/browser/session.py:1117` | 连用户浏览器 + 拉 DOM（间接复用 `_build_enhanced_dom_tree`） |
| `EnhancedDOMTreeNode.compute_stable_hash` / `element_hash` / `xpath` | `src/tree_walker/browser/views.py:547/566/354` | 指纹计算（通过 get_state 间接复用，录制重放同源） |
| `DOMInteractedElement.load_from_enhanced_dom_tree().to_dict()` | `src/tree_walker/browser/views.py:747` | 节点 → interacted_element 投影 |
| `AgentHistory` / `AgentHistoryList.save_to_file` | `src/tree_walker/agent/views.py:101/168` | 历史结构与序列化 |
| `_match_element_index` 的 XPATH 分支 + `_nearest_idx` | `src/tree_walker/agent/rerun.py:545-571` | 后端用 xpath 线索在 selector_map 定位节点的参考实现 |
| `Agent.load_and_rerun` / `rerun_history` | `src/tree_walker/agent/rerun.py:210/234` | 重放入口（录制产物直接喂它） |
| `detect_variables` | `src/tree_walker/agent/variable_detector.py` | 录制后识别可替换变量 |
| `Tools.execute` / `_get_element_by_index` | `src/tree_walker/tools/actions.py:420/446` | 重放侧单步执行（录制侧不直接用） |
| **扩展侧参考**：action-recorder / selector / navigation / SW 恢复 / flush | `D:\dev\git\ai\Browser-BC\extension\src\` | 借鉴采集层组件，详见 §3.2 |
| **服务端参考**：HTTP 选型 / `_atomic_write` / Bearer / 健壮性 | `D:\dev\git\ai\Browser-BC\server\server.py` | 借鉴通用 HTTP 工程实践，业务不借鉴，详见 §3.3 |

---

## 6. 关键设计决策

### 6.1 事件 → action 映射表（阶段 2 补全后）

| content script 监听的事件 | TreeWalker action | params 来源 |
|---|---|---|
| `mousedown`/`click`（去重） | `click` | `index`=后端定位；xpath 发扩展侧 |
| `input`/`change` on input/textarea（合并） | `input_text` | `text`=最终值，`clear`=是否有全选替换 |
| `change` on `<select>` | `select_dropdown` | `value`=选中项 |
| `wheel`/scroll（节流） | `scroll` | `direction`/`amount` |
| 地址栏/pushState 导航 | `navigate` | `url` |
| 后退按钮 | `go_back` | — |
| 标签切换 | `switch_tab` | `tab_id`=targetId 后 4 位 |
| 关闭标签 | `close_tab` | `tab_id` |
| 组合键 | `send_keys` | `keys` |
| `<input type=file>` change | `upload_file` | `path`=约定目录+文件名 |

> `extract`/`find_elements`/`evaluate`/`search_page` 等 LLM 语义动作无对应人类操作，录不到——但"重放用户流程"场景通常用不到。

### 6.2 扩展发给后端的事件线索格式

```json
{
  "type": "click",
  "xpath": "html/body/form/div[3]/input[1]",
  "frame_path": [],
  "tab_id": "A1B2",
  "params": {},
  "ts": 1750000000000
}
```

- `xpath` 仅承担"录制瞬间事件→节点关联"；跨会话稳定性由后端算的指纹承担。
- 后端定位失败时：记录诊断（哪个 xpath、当前 selector_map 有哪些相近），不中断录制，标记该步为 `interacted_element=null`。

### 6.3 model_output 录制填充（重放只读 actions，其余占位）

```python
AgentHistory(
	step_number=n,
	model_output={
		"actions": [{"name": "click", "params": {"index": <后端定位的index>}}],
		"next_goal": "",                 # 占位，重放不读
		"evaluation_previous_goal": "",  # 占位，重放不读
	},
	result=[],
	state_summary={"url": ..., "title": ..., "duration": ...},  # url 重放初始导航必需
	interacted_element=[<DOMInteractedElement.to_dict()>],
	metadata={"step_start_time":..., "step_end_time":..., "step_number":n},
)
```

依据：`rerun.py:415` 重放只读 `actions`/`action` 的 name+params；`rerun.py:309` 读 `state_summary.url` 做初始导航。

### 6.4 落盘路径（复用 rerun 根目录）

录制产出的 `history.json` **复用现有 rerun_history 的根目录机制**，不新造路径约定：

- **根目录** = `AgentSettings.rerun_history_dir`，默认 `"rerun-history"`（相对 CWD/项目根）；可用环境变量 `AGENT_RERUN_HISTORY_DIR` 覆盖（`config.py:113/328`）。
- **路径解析**：落盘经 `resolve_rerun_path(root, name)`（`rerun.py:136`）→ `root / name`，root 取自 `load_settings().agent.rerun_history_dir`；或直接复用 `Agent.save_history(name)` / `AgentHistoryList.save_to_file(self.rerun_path(name))`（`rerun.py:182`）。
- **`--out` 是相对根目录的文件名**：如 `--out myflow.json` → `<项目根>/rerun-history/myflow.json`；`--out douyin/upload.json` → `rerun-history/douyin/upload.json`（`save_to_file` 自带 `mkdir(parents=True)` 自动建子目录）。绝对路径 / `..` 越界会被 `resolve_rerun_path` 拒绝（`ValueError`）。
- **与 agent 自录同址**：录制产物天然落在重放器查找的根目录下，`load_and_rerun("myflow.json")` 直接重放，无需拷贝/改路径（agent 自录也是落此目录，文件名 `yyyyMMddhhmm.json`，`tui/app.py:282`）。

> 注：`docs/rerun_history/09` 虽标"设计文档"，但其描述的根目录 + 路径校验**已落地编码**（`config.py` / `rerun.py:136` / `agent.py:78` 均已实现）。

---

## 7. 新增文件结构

```
src/tree_walker/recorder/
	__init__.py
	server.py          # 本地 HTTP 服务，接收扩展事件
	recorder.py        # Recorder 类：连 CDP、收事件、拼 AgentHistory、落盘
	locator.py         # xpath 线索 → selector_map 节点定位
	event_mapper.py    # 事件 → action 映射 + 去噪
recording_extension/             # WXT 项目（独立 npm 项目，骨架借鉴 Browser-BC §3.2）
	wxt.config.ts              # 权限 / host_permissions 声明（不含 debugger）
	package.json
	entrypoints/
		background.ts         # defineBackground：状态、POST 后端、flush
		content.ts            # defineContentScript(allFrames)：装配采集器
		popup/                # 录制 UI（目录式 entrypoint）
			index.html        # HTML 入口
			main.tsx          # 开始 / 停止 / 标记完成
	capture/                   # 采集器（借鉴 Browser-BC，精简到 action + navigation）
		action-recorder.ts    # DOM 操作归一化 + 去噪（借鉴 BB）
		selector.ts           # buildElementRef / bestSelector / xpathFor（借鉴 BB）
		navigation-recorder.ts # SPA pushState hook → navigate
	shared/
		types.ts              # 事件协议类型
		messaging.ts          # content ↔ background 消息协议
examples/record_user_actions.py   # 编程式入口示例
tests/recorder/                   # 单元 + 集成测试
docs/user_recording/README.md     # 本文档
```

> 编程入口也可加一个 `tree-walker record` CLI 子命令（沿用项目现有 click 模式），与 `examples/` 二选一或都做。

---

## 8. 端到端验证

1. **启动**：`chrome --remote-debugging-port=9222 --user-data-dir=<录制专用 profile>`（首次在该 profile 登录目标站点）。
2. **后端**：`uv run python examples/record_user_actions.py --out myflow.json`（开 HTTP 服务 :8765，等扩展连入）。
3. **扩展**：Chrome 加载 `recording_extension/` 未打包扩展 → 点"开始录制"→ 执行真实操作流 → 点"停止录制"（可选"标记完成"）。
4. **检查产物**：`myflow.json` 每步 `interacted_element[].element_hash`/`stable_hash` 非空（证明全对齐指纹已算出）；`state_summary.url` 存在。
5. **重放**：
   ```python
   await agent.load_and_rerun("myflow.json", variables={"email":"new@x.com"},
                              max_step_interval=3, delay_between_actions=1)
   ```
   观察重放按录制的操作路径执行成功；对比"agent 自动探索录制"的稳定性差异。
6. **单元测试**：`uv run python -m pytest tests/recorder/ -x -v`，覆盖率 >85%。
7. **回归**：`uv run python -m pytest tests/ -x -v` 确认未破坏现有 rerun_history 测试。

---

## 9. 风险与缓解

| 风险 | 缓解 |
|---|---|
| **时序竞态**（SPA 跳转后定位失败） | 阶段 3 的滚动 selector_map 缓存是主缓解；阶段 1 先用"事件即采"验证竞态实际发生率 |
| **xpath 不唯一** | 多候选时按 bounds 就近（复用 `_nearest_idx` 思路）；仍不唯一则记诊断、该步 interacted_element 置空（重放时降级，不阻塞） |
| **iframe/shadow 内操作漏采** | `all_frames:true` + frame_path 线索；阶段 2 处理 |
| **录制专用 profile 与日常 profile 冲突** | 不直接指向日常 profile（会被锁），用独立录制 profile，首次登录目标站点 |
| **`--remote-debugging-port` 本地端口暴露** | 仅监听 localhost，低风险；文档提醒 |
| **MV3 service worker 休眠** | 扩展↔后端用 HTTP 无状态通信已规避长连接断连；若引入 IndexedDB 缓冲（借鉴 Browser-BC `recoverActiveTraceId`）可进一步防丢事件 |
| **xpath 格式不对齐** | Browser-BC `xpathFor` 前导 `/`，TreeWalker xpath 无前导且 iframe 边界停止；后端匹配前 strip 前导 `/` 归一化（§3.2 差异 4） |

若阶段 1 端到端验证失败（指纹对齐不成立等），回退到重新讨论架构假设，不继续扩大实现。
