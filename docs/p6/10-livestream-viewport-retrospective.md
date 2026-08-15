# P6 直播视口实施复盘（2026-08-14）

> 本轮按 [`08-livestream-viewport-plan.md`](08-livestream-viewport-plan.md) 交付 **A 直播视口**：M1 browser 原语 → M2 server 接线 → M3 前端 → M4 真机 e2e（runbook [`09`](09-livestream-viewport-e2e-runbook.md) 场景 H/I/J/K **全绿**，用户 2026-08-14 确认）。
> 本文档对照 08 记录**实施与计划的偏差**与 **M4 真机暴露的三个问题**——尤其「卡顿」问题的两轮迭代（v1 合并 → v2 节流窗），供后续实时流类功能参考。
> 验证口径：全量 Python 测试 **2306 过**；前端 vitest 62 + tsc + vite build 绿。

---

## 一、计划 vs 实际的变化

### 1. M1–M3 与计划高度一致
- **M1**：`BrowserSession.start/stop/configure_screencast` + `start()` 会话就绪自动起 / `stop()` 收尾 + `_LatestFrameSlot` + 帧回调工厂。08 的「M1 先 de-risk CDP/线程」策略生效——WS 读线程移交、`sessionId` 双义、cdp_use 单回调覆盖式这些坑在纯 mock 单测里锁死，M2/M3 零重蹈。
- **M2**：`viewport_mode` 分叉 + 独立 `GET /task/screencast` SSE（`/task/events` 零改动）+ 截图采集门控 + 收尾 wake。
- **M3**：`BrowserView` 双 mode 共渲染 + `RunView` toggle/双 EventSource（done 即关防重连）+ reducer `screencast` 复用 `screenshot`。
- 偏差少的原因：08 调研时把 cdp_use 的 API **核实到了行号**（library.py:766 等），实现期没有「以为有其实没有」的意外。

### 2. 计划外：后端包改名 `history_editor` → `web`（用户要求）
- 顺势完成：git mv 保历史 + 代码/构建/文档全引用更新；CHANGELOG/docs·p4/p5/rerun_history 作历史档案保留原名。
- pyproject **零改动**（`tw-web=cli:web` 稳定入口 + `packages=["src/tree_walker"]` 自动发现）——印证 04 复盘「前端已改 `web_ui`、后端迟早要对齐」的遗留项。

### 3. M4 真机暴露 3 个问题——08 §7 风险表只预见了部分
- **预见到的**：带宽（源头限速 + 最新帧槽）、target 切换画面停（记 future）、宽高比（只设 `maxWidth`）。
- **没预见的**：Chrome 可见性要求（问题 1）、投递节奏与事件循环争用（问题 2/3）——都是**真机才暴露的运行时行为**，mock 单测无法覆盖。

---

## 二、遇到的问题

### 问题 1：直播零帧（「等待直播帧」不动）★ 可见性
- **现象**：📡 模式跑任务画面一直占位；📷 截图模式完全正常。两次表现还不一致（一次部分帧、一次零帧）。
- **诊断**：加自检日志（`startScreencast 成功` / `首帧已收到` / 6s 无帧看门狗）后定位——看门狗触发 = Chrome 收了命令但一帧不推。
- **根因**：**`Page.startScreencast` 只采集「可见」tab**。9223 agent Chrome 窗口被最小化/遮挡时 Chrome 不产帧；而 `Page.captureScreenshot` **无此限制**——截图模式正常正是判别线索。用户把窗口拉到前台后帧即正常。
- **修复**：不改逻辑（Chrome 固有行为），改为**自检化 + 文档**——6s 无帧 warning 直接说原因与对策（含 `--disable-features=CalculateNativeWinOcclusion` 建议）、起流/首帧两行 info、runbook §0 前置 + §7 排障头行。
- **教训**：①「A 模式正常 B 模式不正常」是最快的定位线索，背后往往是同链路不同原语的**能力差异**，要当硬约束前置查证；②静默失败（占位文案不动）必须 watchdog 化，不能让用户猜。

### 问题 2：画面滞后 + ack ERROR ★ 积压
- **现象**：浏览器实际跑完，画面隔好久才慢慢追上；终端 `screencastFrameAck ... ConnectionError('Client is stopping')` + `Task exception was never retrieved`。
- **根因（滞后）**：每帧各自排一个 `call_soon_threadsafe(slot.set)` 回调。事件循环被 agent 的 DOM/LLM 工作穿插占用时，回调**积压**；循环空闲（往往=任务结束）后逐个 drain——每帧一次 SSE 写 + React 重渲，形成慢速「追帧回放」。
- **根因（ERROR）**：ack 是 fire-and-forget 的 `ensure_future` task，收尾 `browser.stop()` 时 client 正 stopping，ack 失败且异常无人取。
- **修复**：ack coro 内 `try/except (ConnectionError, RuntimeError)` + `add_done_callback` 取走异常 → ERROR 消失；滞后见问题 3。

### 问题 3：v1「合并」反而更卡 → v2 节流窗 ★★ 核心复盘
- **v1 修法**：N 帧合并成 **1** 次投递（只取最新帧）——以为既去积压又不影响显示。
- **用户反馈**：**更卡了**。
- **根因分析**：合并门（「已排投递」标志直到投递真正执行才复位）意味着「投递在飞」期间到达的帧**全部并成一次更新**。而循环恒有 agent 工作穿插 → 投递经常在排队 → 有效出帧率**跌破 Chrome 推帧率** → 更新稀疏 → 卡。改前那些帧虽迟到、但会在循环空闲时**成串补放**（快进回放）——客观是滞后，主观反而「顺」。
- **教训（本轮最重要）**：实时流有两个**独立维度**——「滞后」（显示落后现实多少）与「节奏」（单位时间更新几次）。原始方案牺牲滞后保节奏，v1 牺牲节奏保滞后，都只修了一半。用户说「卡顿」对应**节奏**、「等半天才出」对应**滞后**——修任何一个都不能动另一个。
- **v2 最终方案：定节奏节流（节流窗）**——投出一帧后开 ~120ms 窗（`_SC_DELIVERY_MIN_INTERVAL`），窗到期若有新帧则续投最新帧并再开窗；无帧则挂起、下帧重新触发。三态状态机（触发投递 / 窗到期续投 / 挂起）：

| 方案 | 滞后 | 节奏（卡顿） |
|---|---|---|
| ① 每帧各排回调（原始） | ❌ 积压追帧 | ✅ |
| ② 严格合并 N→1（v1） | ✅ | ❌ 忙窗内帧全被并掉，出帧率跌破推帧率 |
| ③ **节流窗（v2，现行）** | ✅ 上界 ≈120ms + 忙时延迟 | ✅ 节奏平稳（≈8fps 上限，不随循环忙闲抖动） |

  另把源头 `every_nth_frame` 4→2（Chrome 推帧率约翻倍，localhost 带宽无压力）。
- **实现要点**：三态迁移的「判定 + 置位」都在 `threading.Lock` 内做原子操作，防「刚挂起又有帧入槽但不再触发」的丢帧竞态；节流窗用 `loop.call_later`，循环已关时 `RuntimeError` 兜底挂起。状态机三态各有单测锁定（`test_delivery_paces_with_interval_window`）。

---

## 三、经验与后续建议

1. **agent/browser 侧生命周期绑定全程零 race**（`start()` 会话就绪自动起、`stop()` 收尾、`run_live` wake）——04 复盘「浏览器生命周期采集优先 agent 侧」教训的正面验证，值得沿用。
2. **静默失败必须自检化**：6s 无帧 watchdog 一次日志定位一类问题（可见性），比起流/首帧日志还能区分「没起流」与「起了没帧」。
3. **实时流双维度（滞后/节奏）评审法**：同类改动先问「动的是哪个维度、会不会伤另一个」——合并/去重类优化十有八九在牺牲节奏。
4. **真机行为 mock 不出来**（可见性、循环争用下的节奏）：e2e 场景要覆盖「异常表现」（零帧/滞后/卡顿）而不只正常路径；runbook §7 的排障表就是这轮的沉淀。
5. **调参旋钮集中文档化**：`_SC_DELIVERY_MIN_INTERVAL`（投递节奏，0.12s）、`every_nth_frame`（源头帧率，2）、`quality/max_width`（画质/带宽，60/1280）——都在 `web/server.py`，runbook §7 有指引。

---

## 四、当前状态与遗留

- ✅ **M4 场景 H/I/J/K 真机全绿**（2026-08-14 用户确认；运行前提：agent Chrome（9223）窗口可见，见 09 §0）。
- 测试：全量 **2306** 过（`test_browser_screencast.py` 13 + `test_livestream_frame.py` 14 + `test_web_server.py` 含 5 个 livestream 用例及其余）；前端 vitest 62 + tsc + build 绿。
- 遗留（future，均记 08 §7 / 09 §7）：mid-run 切 mode（不支持，须新任务）；agent 切 tab/新 target 后画面停（screencast 绑当前 target）；高亮框停留至下个 `step_start`（MVP 语义）；重步骤期间 ack 流控固有迟滞（CDP 特性，MVP 接受）。
- 至此 **A 直播视口交付完成**：方案（08）+ 实现（M1–M3）+ 验收（09 全绿）+ 复盘（本文）齐备。
