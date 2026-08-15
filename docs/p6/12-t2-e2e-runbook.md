# P6 Web 真机 e2e 操作手册（T2 中期：设置面 / 进行中 zone / 右 Context / I4-I6）

> 用途：[`11-t2-settings-context-livezone-plan.md`](11-t2-settings-context-livezone-plan.md) 的**真机验收门**——M1–M8 已全部实施（单测全绿），本手册逐卡验证各 milestone 的「真机」验收项。
> 本手册**承接** [`03`](03-web-e2e-runbook.md) / [`07`](07-skills-and-run-enhancements-e2e-runbook.md) / [`09`](09-livestream-viewport-e2e-runbook.md)：起服务步骤完全相同（Chrome 9223 + `tw-web` + `npm run dev`），场景编号续接（L 起）。
> 前置：03 场景 A/B/C（live 控制台基础流）已绿——本手册在其之上验证 T2 各项，不重复基础流。
> 关联：issue #162 / [`05`](05-followup-plan.md) §2 / [`11`](11-t2-settings-context-livezone-plan.md)。

---

## 0. 前置（一次性确认）

- 仓库 `D:\dev\git\z_jordon\TreeWalker`，依赖已装：`uv sync --extra vision --extra docs`；`web_ui/node_modules` 存在。
- `.env` 的 LLM key / model 配好。
- **前端是本轮构建**（T2 改了 AppShell / RunView / FlowWorkspace / liveReducer / api / 新组件 4 个）：prod 须先 `.\scripts\build_editor.ps1`；dev `npm run dev` 自动热更（⚠️ `vite.config.ts` 本轮加了 `/settings` 代理——dev 模式若提示「非 JSON 响应」见 §9 首行）。
- **后端是本轮 `tw-web`**（含 `/settings/*` + `/task/list` + `/task/history` + `/task/start` 的 model 参数）：`uv run tw-web`，默认连 Chrome **9223**。
- 测试目标建议 `bing.com`（快速无登录）；场景 P（模型）跑一个短任务即可。
- 场景 O（历史互通）需要能跑一次 `uv run tw-tui`（连 **9222**，与 web 的 9223 互不干扰；若 9222 无 Chrome 可跳过 TUI 侧，只验 web 自身闭环）。

---

## 1. 起服务（同 03/07/09 步骤 1–3）

三个 PowerShell：① 起 Chrome 调试实例（`--remote-debugging-port=9223` + 独立 `--user-data-dir`）；② 仓库根 `uv run tw-web`（`http://127.0.0.1:8766`）；③ `web_ui` 下 `npm run dev`（`http://127.0.0.1:5173`）。
> 细节与排障见 03 §1–§3、§6。确认 03 场景 A 基础流能跑通后再做下面场景。

---

## 2. 场景 L — live 状态提升 + TopBar 状态点（M1）

用日常浏览器开 **http://127.0.0.1:5173/**，停在**探索**模式。

- [x] 发任务 `打开 https://www.bing.com 搜索"天气"`（📷 截图模式即可）
- [x] 运行中切到**流程库** → 再切回**探索**：任务输入框恢复为运行态（禁用）、**暂停/停止按钮还在**、时间线事件完整、截图还在——**不是**回到空白输入页（这是 M1 的核心验收）
- [x] F12 → Network：切回探索后 `events`（eventstream）**重开一条**（RunView 重挂载按同一 taskId 重订），任务继续收事件
- [x] 任务运行中切到**流程库**：TopBar 右侧出现 **`● 任务运行中`** 绿色状态点；点击它 → 直接回到运行视图
- [x] 点 **⏸ 暂停** 后切到流程库：状态点变 **`● 任务已暂停`**
- [x] 任务结束（done）后：状态点消失

---

## 3. 场景 M — 侧栏「进行中」zone（M2）

- [x] 任务运行中切到**流程库**：侧栏顶部出现 **「进行中」** 区，列出该任务（绿色 ● + 任务文本截断到 24 字，完整文本在 hover title）
- [x] 点击该条目 → 回到运行视图且状态保留（同场景 L 的行为）
- [x] 开**录制轨迹**再发一个任务并等它跑完：zone 里该任务显示 **✓ + （已存 yyyyMMddHHmm.json）**，且该文件**同时出现在下方「流程库」列表**（落库链路）
- [x] 无任何 live/最近任务时（`tw-web` 刚重启后）：「进行中」区**整区隐藏**，侧栏只剩「流程库」
- [x] F12 → Network：停在流程库模式时有周期性的 `list`（`/task/list`）请求（30s 轮询），无 SSE

---

## 4. 场景 N — 设置面读写 + 生效（M3/M4）

- [x] TopBar 点**设置**（此前是 disabled 占位）：进入分栏——左 LLM/Agent/Browser/高级，右字段表单
- [x] **LLM 区**：模型 / Base URL / Max Tokens / 输出模式（下拉）/ API Key；**API Key 显示掩码**（`****` + 尾 4 位或「未设置」placeholder），明文 key 不出现在页面
- [x] 切到 **Agent 区**：最大步数是 number 输入、计划模式是 checkbox
- [x] 把 **最大步数** 改成 `3` → 点**应用** → 状态显示「已应用 ✓（新任务生效）」、按钮 dirty 星号消失
- [x] 回**探索**发一个任务：agent **3 步即停**（达到 max_steps）——设置真实生效
- [x] 恢复：把最大步数改回 `100` 应用（或重启 tw-web 回落 .env，重启后回设置面确认值回落 = 内存 override 不落盘的设计）
- [x] 负向：应用一个非法值（如最大步数输 `abc`）→ 后端 400，状态显示「应用失败」——非法值不落
- [x] 敏感不动：不动 API Key 直接应用其他字段 → F12 看请求体**不含** `ZHIPU_API_KEY`（掩码串不回传）

---

## 5. 场景 O — 任务历史（M5，TUI↔web 互通）

- [x] web 探索模式：任务输入框**空、光标在首行**时按 **↑** → 回填最近一次启动成功的任务文本；继续 ↑ 向旧翻、↓ 向新翻、翻到底回到空输入
- [x] 多行编辑不被劫持：输入两行文本、光标停在**第二行**按 ↑ → 光标正常上移（不被历史回填覆盖）
- [x] 发一个任务（如 bing 搜索"新闻"）→ 启动成功后立即按 ↑ → 该任务已在历史最顶
- [x] 启动一个**注定失败**的任务（如后端无 Chrome 时发任务，或断开 9223）→ 输入框显示启动失败后按 ↑ → **失败任务不在历史里**（§8.8）（实测用 409 单槽冲突等价路径）
- [ ] **互通**（可选，需 9222 Chrome + `uv run tw-tui`）：TUI 里发一个任务 → web 输入框 ↑ 能翻到它；反向 web 发的任务 TUI ↑ 也能翻到（共享 `~/.treewalker/history.json`）

---

## 6. 场景 P — 模型选择器（M6）

- [x] TopBar 右侧出现**模型输入框**（placeholder「模型（默认）」，datalist 候选 glm-5.1 / glm-4-flash，可任意输入自定义名）
- [x] 输入一个模型名（如 `glm-4-flash`）→ **刷新页面** → 输入框仍是 `glm-4-flash`（localStorage 记忆）
- [x] 带该模型发任务 → `tw-web` 后端终端 / 任务日志显示**该模型在跑**（模型名出现在 LLM 调用日志）（实测：flash 任务成功 + 假模型名报错透传 `modelCode 不存在`，证明 TopBar 值真实到达 LLM 端点）
- [x] 清空模型框（回到「默认」）→ 新任务用 `.env` / 设置面的 `LLM_MODEL` 默认模型
- [x] **两层并存**：设置面把模型默认改成 X、TopBar 填 Y → 新任务用 Y（本次 override 优先）；TopBar 清空 → 用 X（实测用假模型名 X + 真模型 Y 成/败判别法验证）
- [x] 输入一个**不存在的模型名**发任务 → 启动/首步报错文案透传到状态栏（后端不校验模型名，LLM 端点报错回显）

---

## 7. 场景 Q — RunView 右 Context 面板（M7）

- [x] 任务运行中/结束后：时间线条目**可点击**（hover 有提示），点击后右侧出现第三栏 **Context 面板**
- [x] 点一个 **tool_call**（click/input 类）条目：右栏显示**动作名 / 参数 JSON / 元素定位（index + xpath，有则 bbox）**——xpath 应能对应画面上的目标元素
- [x] 点一个 **model_result** 条目：右栏显示**本步目标（next_goal）+ 本步 token**
- [x] 点 **skill_active** 条目（跑 B站/抖音等有 skill 的 host 时）：右栏显示 host + 字数 + 「查看/编辑技能 →」按钮，点击**跳到技能面**并预选该 host（2026-08-15 二次验证通过，#165 修复后：时间线出现 `skill_active step N` 条目，面板显示 `member.bilibili.com · 3261 字` + 跳转预选 ✓）
- [x] 面板头部 **⟩ 折叠** → 收成细条、浏览器视图/时间线变宽；**⟨ 展开** 恢复；**✕** 关闭面板（回到两栏）
- [x] 选中的时间线条目有**蓝色高亮**；发新任务后选中态清空

---

## 8. 场景 R — ⌘K 命令面板（M8）

- [x] 任意模式按 **Ctrl+K**（Mac ⌘K）→ 呼出浮层面板，输入框自动聚焦
- [x] 列表分组：**跳转**（四模式）/ **流程**（流程库文件名）/ **技能**（host 列表）/ **最近任务**（历史，最新在前）/ **模型**（跟随默认 + 预设）
- [x] 输入 `设置` → 过滤到跳转-设置一项 → **Enter** → 面板关闭且切到设置面
- [x] 输入某流程名片段 → Enter → 面板关闭、切到**流程库**且**该流程已加载**（状态栏「已加载 xxx.json」）
- [x] 选一条**重发**（最近任务）→ 切到探索且任务文本**已回填**但**未自动启动**（须手动点发送）
- [x] **↑↓** 移动高亮、Enter 执行、**Esc** 关闭、点**背景**关闭
- [x] 再按 Ctrl+K：已开 → 关闭（toggle）
- [x] 输入乱码 → 显示「（无匹配命令）」
- [x] 停掉后端（Ctrl+C tw-web）再 Ctrl+K：面板仍可用，只剩跳转/模型命令（数据源静默降级，不白屏不报错弹窗）

---

## 9. 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| 设置面报 `后端返回了非 JSON 响应（content-type: text/html）…` | `readJson` 守卫的可诊断报错：① dev 模式下 vite 代理缺该前缀（本轮已在 `vite.config.ts` 加 `/settings`——确认 dev server 重启过，终端应见 `server restarted`）；② 后端是旧 `tw-web`（无 `/settings/*` 路由 → SPA fallback 回 index.html）→ 重启 `tw-web` |
| 重启 tw-web 后「进行中」zone 空 / 运行视图丢 | **设计行为**（§8.5）：live 任务在进程内存，重启即失；「进行中」不持久化 |
| 刷新页面（F5）后运行视图回到空白输入 | **已知局限**：M1 解决的是**切模式**不丢（SPA 内路由）；整页刷新重挂 AppShell，live state 归零。刷新后 zone/状态点也不会显示旧任务（后端重启前 `/task/list` 仍能列出，但 UI 入口依赖轮询拉起——刷新后 zone 会重新出现该任务，点击走 ADOPT 恢复 SSE，**已消费事件/截图不可恢复**，时间线从恢复点续记） |
| 设置改了但新任务没生效 | ① 改的是运行中任务之前构造的（只对新任务生效，UI 底部有明示）；② dev 模式改了后端代码但没重启 `tw-web`；③ 确认应用时状态栏出现「已应用 ✓」 |
| 历史互通没生效 | 确认两端文件一致：`~/.treewalker/history.json`（TUI 与 web 同一路径）；TUI 侧须是本轮代码（老 TUI 逻辑相同，一般无碍）；文件损坏会被容错为 `[]`（两边都翻不到） |
| ⌘K 里没有流程/技能/最近任务 | 对应数据源拉取失败（后端停了/路由 404）——静默降级设计；恢复后端后**重新打开面板**（每次打开重拉） |
| 模型选了但任务仍用默认模型 | ① 该任务是选择前启动的（仅对新任务生效）；② dev 未重启（`/task/start` 的 model 参数是后端新代码）；③ TopBar 框里是空格（trim 后为空 = 默认） |
| 右栏点 tool_call 没有 xpath | 该步动作无 `index`（done / 全局类动作）或元素几何未取到——字段缺省正常，params 仍应显示 |
| Ctrl+K 被浏览器抢走 | Chrome 无默认冲突；若与其他扩展冲突，Esc 后用鼠标操作即可（快捷键非唯一入口） |

---

## 10. 验收结论

- 场景 **L**（切模式往返状态保留 + SSE 重订 + 状态点）✓ ⇒ M1 状态提升可用
- 场景 **M**（zone 列表/状态点/点击回视图/落库联动）✓ ⇒ M2 进行中 zone 可用
- 场景 **N**（设置读写 + max_steps 生效 + 脱敏 + 非法拒绝）✓ ⇒ M3/M4 设置面可用
- 场景 **O**（↑↓ 翻历史 + 成功才落 + TUI 互通）✓ ⇒ M5 任务历史可用
- 场景 **P**（模型 override 生效 + localStorage 记忆 + 两层并存）✓ ⇒ M6 模型选择可用
- 场景 **Q**（tool_call 详情 params/xpath + 折叠 + 跳技能面）✓ ⇒ M7 右 Context 面板可用
- 场景 **R**（⌘K 呼出/过滤/键盘/执行 + 数据源降级）✓ ⇒ M8 命令面板可用

七项全绿 ⇒ [`11`](11-t2-settings-context-livezone-plan.md) 的 T2 真机验收通过，M1–M8 交付完成。
任一失败 ⇒ 把 **`tw-web` 后端终端**报错 + 浏览器 F12 Network 对应请求（`/settings/*`、`/task/list`、`/task/history`、`/task/start` 请求体）与 Console 报错贴出来定位。
