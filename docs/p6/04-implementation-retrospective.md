# P6 实施复盘（2026-08-12）

> 本轮落地了 M1-M5 + M6 前半（`tw-web` CLI）+ 前端目录改名 + 截图 race 修复，真机 e2e（场景 A/B/C）全绿。
> 本文档对照 [`02-implementation-plan.md`](02-implementation-plan.md)，记录**实际实施与计划的偏差**与**遇到的问题**，供后续（直播视口、技能/设置面、DOM 快照回放等）参考。

---

## 一、计划 vs 实际的变化

### 1. 决策#2 修订：TUI 保留并存（非下线）★
- **计划**（02 §8 + 01 决策表 #2）：迁移完**下掉 TUI**，终态单一 web 前端。
- **实际**（用户 2026-08-12 决定）：**TUI 不下**，作为另一个入口与 `tw-web` 并存。
- **影响**：M6「TUI 下线」**取消**；`tw-tui` / `tui/` 保留；§8.3「默认进 web」不再适用（两个 CLI 并存）。后续无需再考虑 TUI 移除的迁移成本。决策修订记入本复盘 §一·1 + 项目记忆 `p6-tui-to-web`；issue #162 的决策表待同步（或留评论说明）。

### 2. 截图机制：web 层 capture + race 修复（vs 计划的 agent 侧 emit）
- **计划**（02 §2.6）：「给 `_obs_bus` 加 `ScreenshotEvent`（或在 `StepEndEvent` 后由 step 流程 emit）」——倾向 **agent 侧 emit**。
- **实际**：选了**零 agent 改动**的 web 层方案——订阅 `step_end` → `asyncio.create_task(_capture_screenshot)` → `browser.take_screenshot()`。
- **偏差代价**：web 层异步采集与 `agent.run` 的浏览器生命周期有时序竞争 → **末步截图 race**（见问题 1）。计划 §7 风险只列了带宽/虚拟滚动，**没预见到这个 race**。
- **教训**：浏览器生命周期相关的采集，agent 侧 emit（采集发生在 step 内、浏览器必活）比 web 层 fire-and-forget 更稳。后续直播视口/密帧应回到 agent 侧。

### 3. 前端目录改名（计划外）
- **计划**：沿用 `history_editor_ui`。
- **实际**：P6 后该目录承载的是完整 web 前端（不止 history editor），名不副实 → 改名 `history_editor_ui`→`web_ui`（npm 包名 `history-editor-ui`→`web-ui`），同步改 build 脚本 / `server.py` / `cli.py` / `.gitignore` / `README` / `ROADMAP` / p6 文档。历史文档（CHANGELOG / docs/p4 / p5 / rerun_history）保留原名为历史档案。

### 4. M6 范围收窄：只做 CLI 一等化
- **计划** M6 = CLI 一等化 + TUI 下线。
- **实际**：只做 CLI 一等化（`tw-web` console script + `cli.py` 的 `web` 命令，启动 web 服务）。TUI 下线因决策#2 修订而**取消**。

### 5. Flow Workspace 四视图的分布（vs 纯四 tab）
- **计划**（01 §3.2）：Flow Workspace 一个容器内 运行/编辑/重放/详情 四 tab。
- **实际**：拆成两个顶部模式——**探索**=运行（RunView live 控制台），**流程库**=FlowWorkspace（编辑/重放/详情 tab）。运行（新探索，不绑特定 Flow）与编辑/重放（操作已存 Flow）语义不同，分模式比硬塞一个 tab 更顺。**四视图都在，只是分家**。

### 6. Detail 视图：DOM 快照未持久化 → 用 state_summary 顶上
- **计划** M5：详情含「每步 DOM 快照回放」。
- **实际**：`AgentHistory` 不持久化 `element_tree_text`（同 `screenshot_path` 被视觉通道阶段二阻塞），Detail 只能用 `state_summary`（url/title/tabs/时长）作为每步上下文。真 DOM 快照回放需后端先存盘（单独一条）。

---

## 二、遇到的问题

### 问题 1：末步截图 race（`ConnectionError: Client is stopping`）★ 主要问题
- **现象**：e2e 场景 A 任务正常跑，但截图区只有 1 张（step0），step1/2 的截图全失败。
- **诊断**：把 `_capture_screenshot` 的 `logger.debug` 临时改 info/warning 后，tw-web 终端看到 `screenshot: capture failed step=N: ConnectionError('Client is stopping')`。
- **根因**：截图采集是 `step_end` 时 `create_task` 异步调度，要等事件循环让出才跑 `take_screenshot()`；而 `run_live` 原用 `agent.run(keep_alive=False)`，run 结束时**自己关浏览器**——末步（done）结束后 run 立刻 finally 关 browser，采集还没轮到执行 → CDP 客户端已 stopping → 全 fail。只有 step0 采集碰巧在 step1 漫长 LLM 调用期间跑完了。
- **修复**：`run_live` 改 `agent.run(keep_alive=True)`（不让 run 关浏览器），收尾**先 drain 完挂起的截图采集，再 `browser.stop()`**。单测同步补：假 `run` 镜像 `keep_alive` 参数。

### 问题 2：可观测功能的失败路径被静默吞掉
- **现象**：截图失败时 tw-web 终端无任何输出，无法定位。
- **根因**：M2 写的 `_capture_screenshot` except 用了 `logger.debug`，而 tw-web 默认 INFO 级 → debug 被过滤。
- **教训**：可观测/辅助路径（截图、日志事件化）的**失败应默认可见（warning）**，成功可 debug；否则一旦出问题就是黑箱。

### 问题 3：测试假对象未镜像真实签名
- **现象**：`keep_alive=True` 修复后 7 个 server 测试挂——假 `run` 都是 `async def X():`（无参），`agent.run(keep_alive=True)` 传参即 `TypeError`。
- **修复**：所有假 `run` 改 `async def X(keep_alive=False):`。
- **教训**：假对象（fake/mock）的签名要镜像真实对象，否则一旦被测代码开始传参就集体断（且部分测试因只断言副作用而「碰巧过」，掩盖问题——本轮 `test_task_record_save_failure` 才真正暴露，另两个用同名假 `quick` 的测试是漏网）。

### 问题 4：`uv sync` 默认剥掉可选依赖（环境）
- **现象**：为重生成 `tw-web` 入口跑了裸 `uv sync`，把 `vision`（Pillow，截图降采样）/`docs` 可选依赖卸了。
- **修复**：`uv sync --extra vision --extra docs` 恢复。
- **教训**：本项目跑 `uv sync` 要带 `--extra vision --extra docs`（已隐含在 e2e runbook 前置）。

---

## 三、经验与后续建议

1. **采集类功能优先 agent 侧 emit**（在 step 内、浏览器必活时采集），避免 web 层异步采集与浏览器生命周期的时序竞争。后续直播视口/密帧应走 agent 侧（CDP `Page.startScreencast`）。
2. **可观测/辅助路径失败默认 warning**，别用 debug 吞。
3. **假对象镜像真实签名**。
4. **`uv sync` 带 extras**——写进 dev runbook。

---

## 四、当前状态与遗留

- ✅ web live 控制台 + 流程库（编辑/重放/详情）真机 e2e 通过（场景 A 实时步骤/截图/日志 + B 暂停/停止 + C 录制存盘/流程库）。
- 🟡 **TUI 并存**（决策#2 修订）——`tw-tui` 与 `tw-web` 两个入口共存，web 为主、TUI 为辅。
- ⏳ 后置：直播视口（`<BrowserView mode='livestream'>`，建议走 agent 侧 `Page.startScreencast`）、技能/设置面（注册表 `disabled` 位已留）、DOM 快照回放（需后端持久化 `element_tree_text`）。
