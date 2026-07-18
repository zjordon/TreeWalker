# 用户操作录制 — 问题排查记录

> 本文记录 `feat/user-recording` 分支端到端联调过程中遇到的实际问题、根因分析与修复，
> 以及调试方法论教训。供后续维护类似"录制 / 重放 / 浏览器自动化"问题时参考。
> 设计文档见同目录 [README.md](README.md)。

---

## 问题 1：后端连浏览器 HTTP 404

**现象**：点「开始录制」，后端报 `websockets.exceptions.InvalidStatus: server rejected WebSocket connection: HTTP 404`。

**根因**：`examples/record_user_actions.py` 默认用裸 `ws://localhost:9222`。但 Chrome 的 `--remote-debugging-port` 暴露的是 HTTP 端点，根路径不接受 WebSocket 握手；必须先 `GET http://localhost:9222/json/version` 取 `webSocketDebuggerUrl`（含 `/devtools/browser/<uuid>`）再连。

**修复**：examples 复用 `config._fetch_ws_url(host, port)`（`config.py:408`）自动发现完整 ws_url，和 TUI / agent 走同一条路径。

---

## 问题 2：url/title 全是扩展 popup，interacted_element 全 null

**现象**：recorded.json 每步 `state_summary.url = chrome-extension://.../popup.html`、`interacted_element = [null]`、click `params = {}`。

**根因**：`BrowserSession._connect`（`session.py:1199`）attach `Target.getTargets` 返回的**第一个 page target**，而点「开始录制」时扩展 popup 刚弹出排在前头 → 后端 `get_state` 一直读 popup 的 DOM，用户页面的 xpath 在 popup 的 selector_map 里找不到。

**修复**：Recorder 加 `select_http_target` + `_ensure_target`（`recorder.py`）——扩展事件带 `url`（content script 的 `location.href`），后端按它在 CDP targets 里找匹配的 **http page**（跳过 `chrome-extension://` / `chrome://` / `devtools://`），必要时 `switch_tab`。`switch_tab` 切 `current_session_id`，`get_state` 自动拿新页 DOM。

---

## 问题 3：click 定位整批失败（叶子元素 vs 可交互祖先）

**现象**：定位失败日志显示 click xpath 全指向 `button[2]/span`、`/img`、`/span[1]/span`、`/div[2]` 等叶子节点。

**根因 A**：浏览器 click 事件的 `e.target` 是最内层元素（button 内的 span、img），而 TreeWalker `selector_map` 只含**可交互祖先**（button / a / label / [role=button]）。xpath 指向叶子、map 里只有祖先 → 整批失配。

**修复 A**：扩展 `action-recorder` 的 click/input 用 `findInteractiveAncestor` 向上找最近的可交互祖先（`action-recorder.ts`），xpath 指向 button 等可交互元素。

**根因 B**：input 的 xpath 路径极深（`/html/body/div[13]/.../input`，`div[13]` 是 SPA 动态弹出的 modal），扩展原生 DOM 树和 TreeWalker CDP 树在这种深嵌套结构上路径不一致。

**修复 B**：扩展额外发元素的 `tag/name/id/aria-label` 原始属性；后端 `locator.locate_by_ref`（`locator.py`）在 xpath 失败后按 `tag + name/id/aria-label` 做 ATTRIBUTE 级兜底（对应 rerun 五级匹配的第 5 级）。

---

## 问题 4：点上传弹不出文件选择框

**现象**：加载扩展 + 开始录制后，点上传按钮不弹原生文件选择框；不加载扩展（= 没开始录制）正常。

**根因**：不是扩展的问题，是后端 `BrowserSession` 启用了 CDP `Page.setInterceptFileChooserDialog`（`session.py:1238`）——它**抑制原生 picker**（设计给 agent 上传用，避免卡在 OS 对话框）。后端一连上，picker 就被吞。"不加载扩展正常"其实是因为不加载 = 没开始录制 = 后端没连。

**修复**：Recorder 在 `start()` 后、以及每次 `switch_tab` 后（因为它会 re-enable）调 `Page.setInterceptFileChooserDialog({"enabled": False})` 关掉 intercept（`recorder.py: _disable_file_chooser_intercept`）。**不动 BrowserSession 本身**，agent 上传行为完全不变。

---

## 问题 5：navigate 第 0 步的设计反思（忠实录制）

**过程**：初看 agent 录制的第 0 步是 navigate，我一度在 Recorder `start()` 时自动插入一个 navigate 步（取当前页 url）对齐结构。

**纠正**：用户指出录制应**忠实记录用户实际操作**——用户开始录制时已在页面，没有导航动作，不该凭空插入 navigate。起始 URL 由每步 `state_summary.url` 承载，重放时 `rerun` 用第 0 步的 `state_summary.url` 做初始导航（`rerun.py:309`），不需要编造的 navigate 步。

**教训**：agent 录制第 0 步是 navigate 是因为 agent 真的导航了；录制用户操作时第 0 步是用户第一个动作，两者语义不同，**不强行对齐结构**。已回退自动插入。

---

## 问题 6：副标题 contenteditable 漏录（Slate 富文本编辑器）

这是最曲折的一个，记录完整排查链。

**现象**：录制产物缺副标题「browse-use体验及技术原理」的 input_text；主标题（标准 input）录到了。

**排查过程**：

1. `debug_recorder_inputs.py` 查元素：副标题是 `<div contenteditable>`（在 selector_map index 22170）。
2. 旧版 onInput 用 `'value' in target` 取值——div 无 value 属性 → 取空 → 漏录。改 `isContentEditable → textContent`。
3. 重录还是漏 → `debug_ce_input.py` 测事件：CDP `Input.insertText` 触发 input/beforeinput（各 4 个）。
4. 扩展加 console.log 重录：扩展 window 监听收到 4 个 `<INPUT>` 的 input，**完全没收到 contenteditable 副标题的事件**。
5. 用户揭示 DOM 结构：副标题不是普通 div，是 Slate 富文本——内部 `<span data-leaf="true"><span data-string="true">文本</span></span>`。

**根因**：Slate（`data-leaf` / `data-string` 是其标志）用 `beforeinput` + `preventDefault` 接管输入、自己改 span 结构，**标准 input 事件根本不派发**。所以扩展的 input/keyup 监听都收不到。CDP `insertText` 走"直接插入文本"路径会触发 input，和真实键盘打字不等价，导致诊断误判。

**修复**：扩展 `action-recorder` 加 `MutationObserver` 观察 contenteditable 的 `subtree + characterData + childList` 变化（`action-recorder.ts`）——Slate 每次输入改 span 结构必触发 mutation，回调读 `textContent` 发 input_text。**不依赖事件传播**，绕过 Slate 拦截。同时监听 document 动态新增的 contenteditable（SPA 弹出编辑器）。

---

## 调试方法论教训

1. **CDP 模拟输入 ≠ 真实键盘输入**：`Input.insertText` 走"直接插入文本"路径会触发标准 input 事件；但真实键盘打字在富文本框架（Slate / Quill / ProseMirror）上常走 `beforeinput` + `preventDefault` + 自己接管，标准 input 不派发。**测真实输入要让真人打字 + 抓事件，或同时看事件流和 DOM 结构两条线交叉验证**，别只用 CDP 模拟。

2. **debug 脚本打印元素要带 `outerHTML` 片段**：不只打印 tag / attributes / value——`outerHTML.slice(0, 200)` 能一眼看出富文本框架特征（`data-leaf` = Slate、`ql-editor` = Quill、`.ProseMirror` = ProseMirror）。本次正是因为没打印内部 HTML，绕了弯才由人眼看源码发现 Slate。（已给 `debug_recorder_inputs.py` 补 outerHTML。）

3. **content script 的 window 与事件隔离坑**：标准 `<input>` 的 input 事件能被 content script 的 window capture 监听收到；但富文本框架拦截 / 合成的事件未必。需要事件无关手段（`MutationObserver`）兜底。

4. **"录制应忠实"优于"结构对齐"**：不要为了产物看起来像 agent 录制而编造步骤。录制记录用户实际操作，重放需要的上下文（如起始 URL）由数据本身（`state_summary`）承载。

5. **改动要区分"录制侧"与"重放侧"**：本次问题大多在录制侧（采集 / 取值 / 定位）；重放侧（`input_text` 对 contenteditable 的执行）是独立的潜在问题——录到不等于能重放，需分别验证。

---

## 涉及的调试脚本

| 脚本 | 用途 |
|---|---|
| `examples/debug_recorder_inputs.py` | 查页面可输入元素（原生 DOM 含 outerHTML + selector_map index/xpath + contenteditable 命中） |
| `examples/debug_ce_input.py` | 测 contenteditable 输入触发什么事件（input / beforeinput / keydown） |

## 关键代码位置

| 问题 | 位置 |
|---|---|
| ws_url discovery | `src/tree_walker/config.py:408 _fetch_ws_url` / `examples/record_user_actions.py` |
| target 选择（避开 popup） | `src/tree_walker/recorder/recorder.py select_http_target` / `_ensure_target` |
| 可交互祖先（click 叶子→祖先） | `recording_extension/capture/action-recorder.ts findInteractiveAncestor` |
| ATTRIBUTE 兜底定位 | `src/tree_walker/recorder/locator.py locate_by_ref` |
| file-chooser intercept 禁用 | `src/tree_walker/recorder/recorder.py _disable_file_chooser_intercept` |
| contenteditable MutationObserver | `recording_extension/capture/action-recorder.ts installActionRecorder` |
