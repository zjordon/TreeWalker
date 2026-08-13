# P6 Web 真机 e2e 操作手册（技能面 + Run 增强）

> 用途：[`06-skills-and-run-enhancements-plan.md`](06-skills-and-run-enhancements-plan.md) 的验收门——真机跑通 **B 技能面 / I1 活动 skill 标示 / I2 token·耗时环 / I3 元素高亮框**。
> 本手册**承接** [`03-web-e2e-runbook.md`](03-web-e2e-runbook.md)：起服务步骤完全相同（Chrome + `tw-web` + `npm run dev`），这里只聚焦**新增功能的验收场景**。
> 前置：03 的场景 A/B/C（live 控制台基础流）已绿——本手册在其之上验证增强项。

---

## 0. 前置（一次性确认）

- 仓库 `D:\dev\git\z_jordon\TreeWalker`，依赖已装：`uv sync --extra vision --extra docs`（vision 含 Pillow，供截图）。
- 前端依赖已装：`web_ui/node_modules` 存在。
- `.env` 的 LLM key / model 配好。
- **前端已重新构建/起 dev**：本次改了前端，prod 模式需先 `.\scripts\build_editor.ps1` 重建 `static/`；dev 模式 `npm run dev` 会自动热更。
- `domain-skills/` 下已有 `member.bilibili.com`、`creator.douyin.com` 两个 host（仓库自带，供场景 D 用）。

---

## 1. 起服务（同 03 步骤 1–3）

三个 PowerShell：① 起 Chrome 调试实例（`--remote-debugging-port=9223` + 独立 `--user-data-dir`）；② 仓库根 `uv run tw-web`（见 `http://127.0.0.1:8766`）；③ `web_ui` 下 `npm run dev`（见 `http://127.0.0.1:5173`）。
> 细节与排障见 03 §1–§3、§6。确认 03 场景 A 基础流能跑通后再做下面场景。

---

## 2. 场景 D — 技能面查看 / 编辑（B）

用你日常浏览器开 **http://127.0.0.1:5173/**，顶部点 **技能** 模式：

- [ ] 左侧 host 列表至少出现 `member.bilibili.com`、`creator.douyin.com`
- [ ] 点 `member.bilibili.com` → 右侧出现 **SOP / Selectors / Quirks** 三 tab，**SOP** 的 textarea 有内容（步骤式文案）
- [ ] 点 **Selectors** → textarea 切成表格内容；点 **Quirks** → 切成编号注意事项
- [ ] 在 Selectors 里改一处（如末尾加一行 `<!-- e2e test -->`）→ 「保存」按钮变成 **保存 \***（dirty 标记）
- [ ] 点 **保存 \*** → 状态显示 `已保存 member.bilibili.com/selectors.md ✓`，按钮回到 `保存`
- [ ] **磁盘核验**（仓库根 PowerShell）：`Get-Content domain-skills\member.bilibili.com\selectors.md -Raw` 末尾能看到刚加的内容

---

## 3. 场景 E — 活动 skill chip（I1）

I1 的 chip 在「探索」模式 RunView 的输入栏。分两步：先看「无技能」，再造一个 skill 看「已加载」。

### E1 无技能（host 未配 skill）

- [ ] 顶部回 **探索** 模式，发任务 `打开 https://www.bing.com 搜索"天气"`，运行中输入栏出现 chip：`🔧 无技能（www.bing.com）`（灰色）
- [ ] 任务正常跑完（基础流不受影响）

### E2 已加载（造一个 www.bing.com 的 skill）

**仓库根 PowerShell** 先建空目录 + 空文件（让该 host 进列表，内容用 UI 填，免 BOM）：

```powershell
$d = "D:\dev\git\z_jordon\TreeWalker\domain-skills\www.bing.com"
New-Item -ItemType Directory -Force $d | Out-Null
New-Item -ItemType File -Force "$d\_sop.md" | Out-Null
```

- [ ] 顶部 **技能** 模式 → 刷新页面（或重选模式）→ 左侧多出 `www.bing.com`
- [ ] 点 `www.bing.com` → SOP tab 的 textarea 空 → 输入 `[SOP]` 换行 `点搜索框 → 输入关键词 → 回车` → **保存 \***
- [ ] 回 **探索** 模式，重发 `打开 https://www.bing.com 搜索"天气"`
- [ ] 运行中 chip 变成 **`🔧 www.bing.com（N字）`**（蓝色可点，N = 刚存 SOP 的字数）
- [ ] **点 chip** → 自动跳回 **技能** 模式，且右侧已选中 `www.bing.com`、SOP 内容是刚存的

> **清理**（验收后删掉测试 skill，别污染仓库）：`Remove-Item -Recurse -Force domain-skills\www.bing.com`

---

## 4. 场景 F — token·耗时环（I2）

任一任务运行中（可复用 E 的 bing 任务）：

- [ ] 「步骤时间线」面板标题下方有一行 `⏱ X.Xs · 🪙 ↑in ↓out`
- [ ] 耗时 `⏱` 随步数**累加**（每步 +该步 duration）
- [ ] token `↑in`（输入）/ `↓out`（输出）随步数**递增**（每步 model_result 累加；首步就有非 0 值）
- [ ] 多步后数字明显大于首步

> 这一行数据来自 `ModelResultEvent.input_tokens/output_tokens`（本轮刚从 `LLMClient` 透传打通，此前恒为 0）+ `StepEndEvent.duration_seconds` 累加。若 token 一直是 0，见 §8 排障。

---

## 5. 场景 G — 元素高亮框（I3）

跑一个有「点 + 输入」的任务（bing 搜索最典型，搜索框/按钮都带 index）：

- [ ] 运行中，**截图区**在 agent 执行 click/input 类动作的步骤出现 **橙色高亮框**，框住目标元素
- [ ] 框左上角有 **action_index 角标**（`0`、`1`、`2`…），对应本步第几个动作
- [ ] 多动作步（如 multi_act）会同时出现多个框
- [ ] 进入下一步（新 `step_start`）→ 旧框清掉、出新框

> **已知局限（正常现象）**：截图在 `step_end` 采，若本步动作触发了**页面跳转**（如点搜索后结果页刷新），截图已是新页面，该步的框可能不再贴合原元素——框仍会画（带角标），属 MVP 预期，不算 bug。
> 框坐标是后端按「元素 bbox / 视口尺寸」归一化的百分比，免采样比/DPR 换算；若全步都无框，多为该步动作无 `index`（如 `done`/`send_keys` 全局）或视口尺寸未取到，见 §8。

---

## 6. 热更新验证（B put → live loader 失效，进阶 / 可选）

验证「运行中改 skill，下一步生效」（单槽，至多一个 live task）：

- [ ] 起一个**较长**的 member.bilibili.com 任务（运行中）
- [ ] 另开一个浏览器 tab 在 **技能** 模式改 `member.bilibili.com/quirks.md` 并保存
- [ ] 后端 `tw-web` 终端**无报错**；agent 后续步骤的 LLM state 里应含新 quirks（看后端日志 `skill loaded: host=member.bilibili.com` 是否在保存后重新出现 / 字数变化）

> `/skills/put` 写盘后会遍历 `_LIVE_TASKS` 调 `agent._skill_loader.invalidate(host)`；下一 步 `_build_skill_description` 重新读盘。无需重启 task。

---

## 7. 看事件流（可选 / 排障）

日常浏览器 F12 → Network → `events`（eventstream）→ EventStream 面板，确认新事件/字段到位：

- `skill_active` 帧：`{"host":"www.bing.com","skill_loaded":true,"char_count":N}`（I1）
- `model_result` 帧：含 `"input_tokens":..`、`"output_tokens":..`（I2，非 null）
- `tool_call` 帧：含 `"element_bbox":{"left":..,"top":..,"width":..,"height":..}`、`"element_index":..`（I3，仅 index 类动作）

---

## 8. 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| **技能** 模式点不亮 / 404 | 前端没重建：dev 模式确认 `npm run dev` 在跑；prod 模式重跑 `.\scripts\build_editor.ps1` |
| `/skills/list` 返回空 | `skills_dir` 指向不对（默认 `domain-skills`，相对 CWD）；`tw-web` 必须在**仓库根**启动 |
| 保存报 `非法 file` / `非法 host` | 白名单仅 `_sop.md`/`selectors.md`/`quirks.md`；host 不能含 `/`、`..`——这是路径校验，按提示改 |
| chip 一直「无技能」 | 该 host 在 `domain-skills/` 下无同名目录；或 `_build_agent` 没强制开 skill（确认用的是本轮 `tw-web`） |
| I2 token 恒 0 | LLM provider 没返回 usage，或用的是非 Anthropic 兼容端点；F12 看 `model_result` 帧的 `input_tokens` 是否 null |
| I3 全程无高亮框 | 该步动作无 `index`（done/send_keys 全局类）；或 `browser._get_viewport_size` 取视口失败（后端终端 warn）→ bbox 降级 None |
| 点 chip 没跳技能面 | `appNav` context 未生效（前端没重建）；或该 host 目录已删（E2 清理后）→ 仍会跳，但右侧加载空 |
| 截图里框位置偏 | 该步触发了跳转（已知局限）；或页面有固定定位 header 导致视口基准偏移——MVP 接受 |

---

## 9. 验收结论

- 场景 **D**（技能面查看/编辑/存盘 + 磁盘核验）✓ ⇒ B 技能面可用
- 场景 **E**（chip 无技能 / 已加载 / 点进技能面）✓ ⇒ I1 活动标示可用
- 场景 **F**（token/耗时随步增长）✓ ⇒ I2 token·耗时环可用
- 场景 **G**（橙色高亮框 + 角标 + 步间刷新）✓ ⇒ I3 元素高亮可用

四项全绿 ⇒ [`06`](06-skills-and-run-enhancements-plan.md) 的 B/I1/I2/I3 验收通过。
任一失败 ⇒ 把 **`tw-web` 后端终端**报错 + 浏览器 F12 Network 的对应帧（`skill_active`/`model_result`/`tool_call`）贴出来定位。
