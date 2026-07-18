# 阶段 2 实施方案 — 补全动作覆盖 + 去噪 + iframe + 文件上传

> 本文档是用户操作录制功能（[README.md](README.md)）**阶段 2** 的执行级实施方案。
> 阶段 1（MVP）已完成并端到端跑通，踩坑记录见 [troubleshooting.md](troubleshooting.md)。
> 创建于 2026-07-11，分支 `feat/user-recording`。

---

## 1. 背景与目标

阶段 1 已证明核心管线可行：扩展采集 `click` + `input_text`（含 contenteditable / Slate，经 MutationObserver），后端实时 `get_state` → `locator` 定位 → 算指纹 → 拼 `AgentHistory` → 落盘 → `load_and_rerun` 重放。

阶段 2 把录制覆盖面从 2 类动作扩到 [README §6.1](README.md#61-事件--action-映射表阶段-2-补全后) 的全部 10 类，并补去噪 / iframe / 文件上传。

**目标**：录制产物对真实业务流（如抖音上传：导航 → 点击 → 输入 → 选下拉 → 上传文件 → 滚动 → 快捷键）完整且可重放。

### 关键架构约束（不可违背）

指纹必须在事件发生瞬间由后端 CDP 算（[README §3.2](README.md#为何实时处理不是-browser-bc-那样批量上传)）→ `AgentHistory` 是**逐事件实时构建**的（不能把 `get_state` 推迟到 `stop()`）。因此：

> **去噪只能在 `stop()` 对最终 steps 列表做一遍后处理**，不能对原始事件流做批处理（实时流没有"批"）。

### 已确认的三个范围决策

| 决策点 | 选定方案 |
|---|---|
| **upload_file 取文件** | 仅采集文件名 → 后端解析为 `<rerun_history_dir>/uploads/<文件名>`。重放前用户须把文件放进该目录。（不抓字节，避免视频等大文件 base64 经 HTTP 传输。） |
| **navigate（SPA）** | 本阶段实现——新增 MAIN-world 注入脚本入口 hook `history.pushState/replaceState` + `popstate`。 |
| **switch_tab / close_tab** | 本阶段实现——background 监听 `chrome.tabs` 事件发携带目标 tab URL 的事件；后端用已有 `get_tabs()` 解析 URL → CDP `targetId`，存 `target_id[-4:]`（重放侧期望格式）。 |

---

## 2. 重放侧执行语义（决定扩展采集什么）

来自 `tools/actions.py` + `browser/session.py` 的核实结果：

| 动作 | 执行语义（重放侧） | 扩展采集要点 |
|---|---|---|
| `select_dropdown` (`session.py:3265`) | `value` 匹配 option 的 **value 属性或可见文本**（大小写不敏感精确） | 采 `select.value`（option 的 value 属性）✓ |
| `scroll` (`session.py:2532`) | `delta = amount * viewportHeight`，方向取符号，mouseWheel 派发 | 累计 wheel `deltaY`，`amount = clamp(1,10, round(|Δy| / innerHeight))`，方向按符号；amount=0 丢弃 |
| `send_keys` (`session.py:2421`) | 含 `+` → 组合键；命名键(`Enter`/`ArrowUp`/`F5`) → special；纯文本逐字符 | 只采**组合键**(Ctrl/Alt/Meta) + **命名非打印键**；可打印字符走 input_text（不录） |
| `switch_tab` / `close_tab` (`actions.py:1154`) | `tab_id` = CDP `targetId` 后 4 位；后端 `get_tabs()` 按 `endswith` 解析；close_tab 空 = 关当前 | 扩展只发目标 tab **URL**；后端解析成 `target_id[-4:]` |
| `navigate` (`actions.py:493`) | `Page.navigate(url)`；`new_tab=True` 新标签 | 采 url；SPA / 地址栏 new_tab=False |
| `go_back` | `Page.goBack` | **不单独录**：popstate 折叠成 `navigate(url)`（见 §5） |

---

## 3. 改动清单（按文件）

### 3.1 后端（Python，`src/tree_walker/recorder/` + `config.py`）

#### `event_mapper.py` — 完善映射 + 新增 `denoise_steps`
- `map_event`：10 类映射**已存在**，补两处：
  - `scroll`：`amount` 加 clamp `max(1, min(10, …))`（对齐 `ScrollParams` ge/le）。
  - `navigate`：透传 `new_tab`（默认 `False`）。
- **新增 `denoise_steps(steps: list[AgentHistory]) -> list[AgentHistory]`**（生产去噪路径，在 `stop()` 调用）：
  - 合并**相邻同 index 的 `input_text`** → 取最后一条（`clear=True` 本就覆盖）。
  - 折叠**相邻同 index 的 `click`**（gap 内，按 `metadata.step_start_time`）→ 留一条。
  - 合并**相邻同方向 `scroll`** → amount 求和 clamp 1-10。
  - 跨非可合并步骤不合并；最后**重排 `step_number`**（0..N-1，同步 `metadata.step_number`）。
- 保留现有 `coalesce_inputs`（原始事件级，已有测试，作工具函数）。

#### `locator.py` — 无改动
- iframe 定位**复用现有 rect 就近**：`dom.py:758-767` 已把 iframe 内容节点 bounds 加上 iframe 偏移（视口坐标），扩展 `getBoundingClientRect` 同为视口坐标 → xpath 多候选（跨 frame 同名 `html/body/...`）时 rect 就近能正确选到目标 frame 内的节点。不引入复杂的 frame_path 解析（诚实记录此为 best-effort）。

#### `recorder.py` — tab_id 解析 + upload 路径解析 + stop 去噪
- `__init__` 新增 `upload_dir: str = ""`；空则解析为 `os.path.join(rerun_history_dir, "uploads")`。
- `handle_event` 新增分支（在 `needs_target` 分支之前）：
  - `switch_tab` / `close_tab`：用 `self.browser.get_tabs()` 找 url 匹配的 page（`endswith` 或归一化相等），置 `params["tab_id"] = target_id[-4:]`；解析失败置空（close_tab 空 = 关当前，合法）。
  - `upload_file`：`params["path"] = os.path.join(self.upload_dir, basename)`（扩展只发文件名）。
- `stop()`：先 `self.history.history = denoise_steps(self.history.history)`，再追加 `done`（如有），再 `save_to_file`。

#### `config.py` — 新增 `record_upload_dir`
- `AgentSettings`（`:113` 附近）加字段 `record_upload_dir: str = ""`（镜像 `rerun_history_dir`）。
- `load_settings()`（`:328` 附近）加 `record_upload_dir=os.environ.get("AGENT_RECORD_UPLOAD_DIR", "")`。
- 运行时解析：空 → `os.path.join(rerun_history_dir, "uploads")`。

#### `server.py` — 无改动
filename-only 方案不需要 `/upload` 端点；`upload_dir` 经 `Recorder.__init__` 注入。

#### `examples/record_user_actions.py`
- `Recorder(browser, rerun_history_dir=…, upload_dir=…)`：新增 `--upload-dir` CLI 参数（可选，默认走 settings 解析）。

### 3.2 扩展（TypeScript，`recording_extension/`）

#### `capture/action-recorder.ts` — 补 4 类采集
在现有 click / input / contenteditable 之外：
- **select_dropdown**：`change`（capture）on `<select>` → `{type:'select_dropdown', params:{value: select.value}}` + refAttrs（先 flushInput）。
- **scroll**：`wheel`（capture, passive）→ 累计 `deltaY`；方向反转或空闲 ~500ms 或累计达 1 视口时 flush：`amount=clamp(1,10,round(|Δy|/innerHeight))`，`amount=0` 丢弃。
- **send_keys**：`keydown`（capture）→ 仅当 `(ctrlKey||altKey||metaKey)` 或 key ∈ {Enter,Tab,Escape,Arrow*,F1-F12,Backspace,Delete,Home,End,PageUp/Down} 时发；`e.repeat` 跳过；组合键拼 `"Control+a"` 风格（Shift 仅在伴其它修饰符时计入；Shift+字母 = 大写输入，归 input_text，不发）。
- **upload_file**：`change` on `input[type=file]` → `files[0]?.name` → `{type:'upload_file', params:{path: filename}}`（refAttrs 用 findInteractiveAncestor）。
- **frame 信号**：`sendEvent` 附带 `is_top_frame: window.top === window.self`（`frame_url` 即已有 `url`）。

#### `capture/navigation-recorder.ts`（新）+ `entrypoints/injected.ts`（新，MAIN-world）
- `injected.ts`：`defineUnlistedScript`，在 **MAIN world** 包装 `history.pushState/replaceState`，调用后 `window.dispatchEvent(new CustomEvent('tw:nav', {detail:{url: location.href}}))`；并监听 `popstate` 同样派发。
- `navigation-recorder.ts`（content 侧 helper）：注入 `injected.ts`（WXT `injectScript` 或 `<script src>` 元素注入），监听 `tw:nav` CustomEvent（跨 world 共享 window，CustomEvent 可穿透）→ 发 `{type:'navigate', params:{url}}`（仅录制中）。

#### `entrypoints/background.ts` — tab 事件
- 维护 `tabUrlCache: Map<number, string>`（onActivated / onUpdated 更新）。
- `chrome.tabs.onActivated` → `chrome.tabs.get(tabId)` 取 url → 录制中发 `{type:'switch_tab', params:{url}}`。
- `chrome.tabs.onRemoved` → 从 cache 取被关 tab url → 录制中发 `{type:'close_tab', params:{url}}`。
- 复用现有 `postEvent` 直发后端；与 content 事件同走 `/event`。

#### `shared/types.ts`
- `RecorderEvent` 加可选 `is_top_frame?: boolean`；`params` 注释补 select / scroll / send_keys / upload / navigate(new_tab) / switch_tab(url) / close_tab(url)。

#### `entrypoints/content.ts` — 装配 navigation-recorder
- `installActionRecorder` 旁加 `installNavigationRecorder({sendEvent})`；卸载时一并 disconnect。

---

## 4. 测试（`tests/`，覆盖率 >85%）

- `test_recorder_event_mapper.py`：补 select_dropdown / scroll（amount clamp 越界）/ send_keys / navigate(new_tab) / switch_tab / close_tab 映射；**新增 `denoise_steps` 用例**：input 合并、click 折叠、scroll 合并、跨步骤不合并、step_number 重排。
- `test_recorder.py`：
  - `FakeBrowser` 加 `get_tabs()` 返回假 page 列表（带 `target_id`）。
  - switch_tab 事件 → 断言 `params.tab_id == target_id[-4:]`。
  - upload_file 事件 → 断言 `params.path == <upload_dir>/<filename>`。
  - `stop()` → 断言相邻 input_text 被合并（步数减少）。
- `test_recorder_locator.py`：无改动（rect 就近已覆盖）。
- 全量回归：`uv run python -m pytest tests/ -x -v` 确认不破坏现有用例。

---

## 5. 关键设计取舍说明

- **go_back 折叠进 navigate**：`popstate` 无法可靠区分"后退按钮"vs"pushState 触发的回退"，统一记 `navigate(url)`。重放落点同为某 URL，且 `navigate(url)` 比 `go_back`（依赖历史栈）更稳。如后续需真正 `Page.goBack` 重放，加启发式（popstate 前无 pushState tick）。
- **iframe 走 rect 就近（best-effort）**：跨 frame 同名 xpath 靠 rect 就近（已视口坐标对齐）消歧；深嵌套跨源 iframe 仍可能误选，阶段 3 滚动 `selector_map` 缓存可进一步缓解。
- **close_tab 的 tabId 对齐**：扩展只有 Chrome tabId，靠 url 在后端解析成 CDP `targetId[-4:]`；被关 tab 的 url 靠 background `tabUrlCache` 记住。
- **MAIN-world 注入兜底**：默认 WXT unlisted 脚本 + content 注入 + CustomEvent 跨 world 桥；若站点 CSP 阻断内联 `<script>`，改用 `chrome.scripting.executeScript({world:'MAIN'})`（background 已有 `scripting` 权限）。
- **去噪分层**：扩展侧做实时去噪（input 400ms 合并已存在 / scroll 节流 / send_keys 闸门）；后端 `stop()` 的 `denoise_steps` 作安全网（最终 steps 再过一遍），两层都单元可测。

---

## 6. 验证（端到端）

1. 后端单测：`uv run python -m pytest tests/test_recorder*.py -x -v` → 全过、覆盖率 >85%。
2. 全量回归：`uv run python -m pytest tests/ -x -v`。
3. 扩展构建：`cd recording_extension; npm run build`（产物 `.output/chrome-mv3/`）。
4. 手动联调（用户驱动）：Chrome(9222) + 后端 + 重载扩展 → 录一个含「SPA 导航 / 下拉选择 / 滚动 / 快捷键 / 文件上传 / 切标签」的流程 → 停止 → 查 json 每步 `actions` + `interacted_element` → 放文件到 `rerun-history/uploads/` → `load_and_rerun` 重放成功。
5. 比对：阶段 1 的 click / input 用例不回归。

---

## 7. 落地顺序（建议）

1. 后端：`config.py` → `event_mapper.py`（map 完善 + `denoise_steps`）→ `recorder.py`（tab/upload 解析 + stop 去噪）→ `examples/record_user_actions.py`。
2. 后端测试先行（`denoise_steps` / tab 解析 / upload 解析），跑通再动扩展。
3. 扩展：`action-recorder.ts`（select/scroll/send_keys/upload）→ `injected.ts` + `navigation-recorder.ts`（navigate）→ `background.ts`（tab 事件）→ `content.ts` 装配 → `types.ts`。
4. `npm run build` → 手动联调。
