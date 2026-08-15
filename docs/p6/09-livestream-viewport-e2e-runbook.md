# P6 Web 真机 e2e 操作手册（直播视口 A）

> 用途：[`08-livestream-viewport-plan.md`](08-livestream-viewport-plan.md) 的 **M4 验收门**——真机跑通 **A 直播视口**（CDP `Page.startScreencast` 连续推流）。
> 本手册**承接** [`03-web-e2e-runbook.md`](03-web-e2e-runbook.md) 与 [`07-…-e2e-runbook.md`](07-skills-and-run-enhancements-e2e-runbook.md)：起服务步骤完全相同（Chrome 9223 + `tw-web` + `npm run dev`），这里只聚焦**直播视口的验收场景**。
> 前置：03 场景 A/B/C（live 控制台基础流）+ 07 场景 G（I3 高亮框，截图模式）已绿——本手册在其之上验证直播模式 + 截图模式零回归。
> 关联：issue #162 / [`05`](05-followup-plan.md) §1.A。

---

## 0. 前置（一次性确认）

- 仓库 `D:\dev\git\z_jordon\TreeWalker`，依赖已装：`uv sync --extra vision --extra docs`。
- 前端依赖已装：`web_ui/node_modules` 存在。
- `.env` 的 LLM key / model 配好。
- **前端已重新构建/起 dev**：M3 改了前端（BrowserView 双 mode / RunView toggle / api / reducer），prod 模式须先 `.\scripts\build_editor.ps1` 重建 `web/static/`；dev 模式 `npm run dev` 自动热更。
- **后端是本轮 `tw-web`**（含 M1 screencast 原语 + M2 `/task/screencast` SSE）：`uv run tw-web`，默认连 Chrome **9223**。
- ⚠️**直播模式要求 9223 Chrome 窗口「可见」**：`Page.startScreencast` 只采集**非最小化、非完全遮挡**的 tab（`captureScreenshot` 无此限制，故截图模式即使窗口隐藏也正常）。验收直播时**别把 agent Chrome 最小化**；若必须放后台，启动 Chrome 加 `--disable-features=CalculateNativeWinOcclusion --disable-backgrounding-occluded-windows`。后端 6s 无帧会打 warning 自检（见 §7）。
- 一个无登录负担的测试目标即可验核心（建议 `bing.com` 搜索：有输入框+按钮，快速）；B站/抖音作可选压测。

---

## 1. 起服务（同 03/07 步骤 1–3）

三个 PowerShell：① 起 Chrome 调试实例（`--remote-debugging-port=9223` + 独立 `--user-data-dir`）；② 仓库根 `uv run tw-web`（见 `http://127.0.0.1:8766`）；③ `web_ui` 下 `npm run dev`（见 `http://127.0.0.1:5173`）。
> 细节与排障见 03 §1–§3、§6。确认 03 场景 A 基础流（截图模式）能跑通后再做下面场景。

---

## 2. 场景 H — 直播视口连续帧（livestream 核心）

用日常浏览器开 **http://127.0.0.1:5173/**，停在**探索**模式 RunView。

- [ ] 输入栏新增一个 mode 下拉（`📷 截图` / `📡 直播`），默认 `📷 截图`
- [ ] 切到 **📡 直播**，发任务 `打开 https://www.bing.com 搜索"天气"`
- [ ] 浏览器视图右上角出现 **`● 直播`** 橙色徽标
- [ ] **画面连续刷新**——agent 操作期间（滚动 / 点搜索框 / 输入 / 回车）能看到页面**实时变化**，而不是「每步末尾才换一帧」（这是直播 vs 截图流的关键体感差异）
- [ ] 帧率约 **2–3 fps**（`everyNthFrame=4` 源头限速；非视频级流畅，但能看清动作过程）
- [ ] 后端 `tw-web` 终端**无** `screencast 启动失败` / `stopScreencast failed` 报错

> 区分直播与截图：截图模式（📷）只在每个 `step_end` 换一帧，步间画面静止；直播模式（📡）在 agent 动作期间持续推帧。若直播模式仍像截图（步间不动），见 §7。

---

## 3. 场景 I — 直播模式下的 I3 高亮框（复用）

I3 高亮层与 mode 无关（08 §4.1），直播帧上应同样工作。复用 H 的 bing 任务：

- [ ] agent 执行 **click / input_text** 类步骤时，直播画面上出现 **橙色高亮框**框住目标元素
- [ ] 框左上角有 **action_index 角标**（`0`/`1`…）
- [ ] **框对齐目标元素**（百分比定位：bbox/视口同宽高比 → 免采样比/DPR 换算，与截图模式同一策略）
- [ ] 进入下一步（新 `step_start`）→ 旧框清、出新框

> **已知局限（正常现象，非 bug）**：直播帧连续刷新，而高亮框在「本步 tool_call → 下个 step_start」之间**停留**（框看似钉住，直到下一步刷新）——这是 MVP 预期。若本步动作触发了**页面跳转**（如点搜索后结果页刷新），框会短暂错位，带角标可辨识。
> 若全程无框：多为该步动作无 `index`（`done`/`send_keys` 全局类），或视口尺寸未取到（后端 warn）——同 07 场景 G 排障。

---

## 4. 场景 J — 暂停 / 停止干净停推流（生命周期）

验证 livestream 的起停边界 + EventSource 不重连（08 §3.5 / §4.2 的防重连设计）。

### J1 暂停 / 恢复
- [ ] 运行中点 **⏸ 暂停** → 任务暂停；agent 不再操作 → 页面静止 → 直播帧**不再有实质变化**（CDP screencast 仍在推，但页面没动 → 帧内容相同）
- [ ] 点 **▶ 恢复** → agent 继续 → 画面重新动起来
- [ ] 暂停/恢复期间 F12 的 `/task/screencast` 连接**保持不断、不重开**（pause 不重建帧流）

### J2 停止 → EventSource 关闭、防重连
- [ ] 运行中点 **⏹ 停止** → 任务结束
- [ ] F12 → Network → `screencast`（eventstream）连接状态变 **closed/取消**
- [ ] **关键**：停止后 `/task/screencast` **不自动重连**（无周期性 reconnect 风暴）——RunView 在 done/error 时 `es.close()`
- [ ] 后端终端：任务结束后**无持续推帧日志**、无 `screencast` 相关报错

> 若停止后 `screencast` 连接反复重连，多半是前端没在 done 时关流（前端未重建）——见 §7。

---

## 5. 场景 K — 截图模式零回归

确认 livestream 改动没破坏原有截图流（mode 互斥，默认 screenshots）。

- [ ] mode 切回 **📷 截图**，发一个新任务（如 `打开 https://www.bing.com 搜索"新闻"`）
- [ ] **无 `● 直播` 徽标**；占位文案是「等待截图…」
- [ ] 画面在**每个 step_end 换一帧**（步间静止，与 03 场景 A 一致）
- [ ] F12 → Network：**只有一个 `events` eventstream，没有 `screencast` 连接**
- [ ] 复跑 03 场景 A（实时步骤/截图/日志）+ B（暂停/停止）+ C（录制存盘）全绿——截图模式行为完全不变

---

## 6. 看事件流（可选 / 排障）

日常浏览器 F12 → Network。livestream 模式下应有**两个** eventstream：

- `events`（`/task/events`）：步骤/日志/控制（同 03/07）。
- `screencast`（`/task/screencast`）：**仅 livestream 任务有**。点开 EventStream 面板：
  - 帧类型 `screencast`，payload `{"data":"data:image/jpeg;base64,...","width":..,"height":..,"scale":..}`
  - 帧持续到来（~2–3fps）；前端只保留最新一帧（`_LatestFrameSlot` 覆盖式，旧帧丢弃）
  - 停止任务后该流关闭、不重连

> `data` 是 base64 data URL，直接喂 `<img src>`；`width/height/scale` 来自 CDP `ScreencastFrameMetadata`（deviceWidth/deviceHeight/pageScaleFactor），供后续若做精确坐标换算用（当前高亮走百分比，不依赖它们）。

---

## 7. 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| 直播模式画面不动 / 一直「等待直播帧」（零帧） | **头号原因：9223 Chrome 窗口被最小化或完全遮挡**——`Page.startScreencast` 只采集**可见** tab（`captureScreenshot` 无此限制，故截图模式正常）。恢复显示该窗口后重试。后端启动 6s 无帧会打 warning 提示（并建议 `--disable-features=CalculateNativeWinOcclusion --disable-backgrounding-occluded-windows`）。**排查顺序（看 `tw-web` 终端）**：①有无 `screencast: startScreencast 成功`（无 → screencast 没起 / 看有无 `启动失败` warn）；②有无 `livestream: 首帧 screencast 已收到`（**有「成功」无「首帧」= Chrome 没推帧 → 多半窗口隐藏**）；③F12 看有无 `screencast` 连接（404 = 该任务非 livestream，mode 没选对） |
| 直播模式仍像截图（步间不动） | 帧没推到前端：F12 `screencast` 连接是否有持续 `screencast` 帧；后端终端看 `Page.startScreencast` 是否成功、有无 `stopScreencast failed`；确认 Chrome 是 9223 那个调试实例 |
| 停止任务后 `screencast` 疯狂重连 | 前端没在 done 时关 EventSource（前端未重建）；或 done 事件没到（主 `events` 流断）→ phase 没转 done → 帧流没关。重建前端 / 查 `events` 流的 done 帧 |
| 直播帧画质糊 / 带宽高 | jpeg `quality=60` + `maxWidth=1280` 源头压缩（带宽权衡）。要清晰：调 `server.py:_handle_task_start` 里 `configure_screencast(..., quality=, max_width=, every_nth_frame=)`；降帧率减带宽 |
| 画面滞后 / 卡顿（投递节奏） | **已修（v2 定时节流）**：三种方案的取舍——①每帧各排一个回调：loop 忙于 agent DOM/LLM 时积压、末尾慢慢 drain → **滞后**；②严格合并 N 帧→1 次：忙窗内到达的帧全被并掉，出帧率跌破推帧率 → **卡顿**（v1 教训）；③现行：**节流窗**——出帧后开 ~120ms 窗（`_SC_DELIVERY_MIN_INTERVAL`），窗到期有新帧则续投最新帧 → 节奏平稳（≈8fps 上限）、lag 上界 ≈ 120ms、无积压。另 `every_nth_frame` 4→2 提高源头推帧率。残余：重步骤期间的 ack 流控固有迟滞，MVP 接受 |
| 截图模式（📷）也出现直播徽标 / 有 screencast 连接 | mode 没切对，或前端没重建（旧前端默认无 toggle，新前端默认 screenshots 不连 screencast） |
| 高亮框在直播帧上一直钉住 | 已知局限：框停留至下个 `step_start`；跳转后可能错位——MVP 预期（08 §7） |
| agent 切 tab / 新开 target 后直播画面停 | screencast 绑当前 target，切 target 后画面可能停——MVP 已知局限（08 §7）；回 📷 模式或重启任务 |
| 后端终端 `screencastFrameAck ... Client is stopping` 报错 | 已修：收尾 `browser.stop()` 时 client 正 stopping，在飞的 ack 失败现**吞在 ack coro 内**（debug 级）并取走 task 异常，不再 `Task exception was never retrieved`。若仍见 ERROR，确认跑的是本轮 `tw-web` |

---

## 8. 验收结论

- 场景 **H**（直播徽标 + 连续帧刷新 + 后端无报错）✓ ⇒ livestream 推流可用
- 场景 **I**（直播帧上高亮框 + 角标 + 对齐 + 步间刷新）✓ ⇒ I3 高亮层跨 mode 复用可用
- 场景 **J**（暂停/恢复不断流 + 停止关流且不重连）✓ ⇒ 生命周期 + 防重连可用
- 场景 **K**（截图模式无 screencast 连接 + 03 A/B/C 零回归）✓ ⇒ mode 互斥、截图流无回归

四项全绿 ⇒ [`08`](08-livestream-viewport-plan.md) 的 **M4 真机验收通过**，A 直播视口交付完成。
任一失败 ⇒ 把 **`tw-web` 后端终端**报错 + 浏览器 F12 Network 的 `screencast`（与 `events`）连接状态/帧贴出来定位。
