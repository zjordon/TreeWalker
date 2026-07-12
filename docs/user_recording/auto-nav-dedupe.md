# 排除自动跳转的 navigate 步骤方案

> 问题：录制时「上一步操作后页面自动跳转」被录成 navigate 步骤，回放会重复定向。
> 目标：把这种副作用 navigate 从录制产物里排除，避免回放时整页重载、丢失页面状态。

---

## 1. 背景：step 2 / step 27 是自动跳转，回放会重复定向

实测录制抖音上传（`recorded.json`）里有两步 `navigate` 是**上一步的副作用**：

| step | action | 来源 | 与触发动作间隔 |
|---|---|---|---|
| 2 | `navigate → post/video?enter_from=publish_page` | step 1 `upload_file` 上传视频 → 抖音 JS 自动跳发布页 | 0.92s |
| 27 | `navigate → upload?enter_from=publish` | step 26 `click`（提交/确定）→ 自动跳回 upload 页 | 0.04s |

回放时：回放 step 1 / step 26 会**再次触发同样的自动跳转**（页面已跳到目标 URL），紧接着回放 step 2 / step 27 的 `navigate`——而 CDP `Page.navigate` 到当前已在的 URL 会**整页重载**，导致发布页刚填的标题/描述/封面全丢。这两步（以及任何同类自动跳转）必须排除。

对比 step 0 `navigate`（首步，录制起点的初始 URL）是用户/录制开始的主动导航，**应保留**。

---

## 2. 根因（两层）

### 2.1 扩展无法区分"主动导航" vs "自动跳转"

`navigation-recorder.ts` + `injected.ts`：
- MAIN-world 注入脚本 wrap `history.pushState/replaceState`，调用后派发 `tw:nav` CustomEvent；
- content script 监听 `tw:nav` + `popstate` + `hashchange`，统一发 `navigate(url)`。

无论 pushState 是用户点菜单触发、还是上传完成回调触发，扩展看到的都是同一个 `navigate(url)` 事件，**无法区分**。

### 2.2 回放侧 navigate 不幂等

- `_action_navigate`（`src/tree_walker/tools/actions.py:493`）→ `browser.navigate(url, new_tab)`；
- `session.navigate`（`src/tree_walker/browser/session.py:1720`）直接 `client.send.Page.navigate({"url": url, "transitionType": "address_bar"})`。

**没有**"当前 URL == 目标 URL 就跳过"的判断。CDP `Page.navigate` 到当前 URL 仍触发完整导航（重载、JS 重新执行、表单状态丢）。

---

## 3. 关键洞察：启发式为何安全

录到的 `navigate` 只有两种来源：

1. **首步**——录制起点的初始 URL（step 0）；
2. **前置动作的副作用**——
   - 用户点菜单/链接：`click` 已单独录；
   - 上传完成回调：`upload_file` 已录；
   - 提交后跳转：`click`（确定/保存）已录。

「地址栏输入整页 URL 导航」**根本录不到**——整页重载会让 content script 重新注入、pushState hook 丢失，且 `popstate` 只对 same-document history navigation 触发。所以：

> **录到的 navigate 若不是首步，就一定是前置动作的副作用**；回放那个前置动作会再次触发跳转，navigate 步骤冗余 → 可安全丢弃。

证据（`recorded.json` 时间戳）：step1 upload_file@1783819271.53 → step2 navigate@1783819272.45（0.92s）；step26 click@1783819335.15 → step27 navigate@1783819335.19（0.04s）。都紧贴触发动作。

---

## 4. 方案：后端 `denoise_steps` 加 navigate-dedupe pass

在 `src/tree_walker/recorder/event_mapper.py` 的 `denoise_steps` 里、`dedupe_uploads` 之后、现有 input/click/scroll 合并之前，加一个 pass（独立函数 `dedupe_auto_navigates` 或并入 denoise_steps）：

**规则**（对每个 `navigate` 步骤）：
- `new_tab=True` → **直接保留**（主动开新 tab，非副作用）；
- **首步**（前面无保留步骤）→ 保留；
- 否则看紧邻前一步（保留序列的 `out[-1]`）：
  - 前一步**非 navigate** 且时间间隔 ≤ `gap_navigate`（默认 **3s**）→ 判为副作用跳转，**丢弃**；
  - 前一步是 navigate（连续导航），或间隔超 `gap_navigate` → **保留**（避免连锁误丢 + 给地址栏式主动导航留口）。

`gap_navigate=3s` 覆盖大文件上传处理完才跳的慢场景；可配。

### `recorded.json` 效果

- step 2（前 upload_file，0.92s ≤ 3s）→ **丢**；
- step 27（前 click，0.04s ≤ 3s）→ **丢**；
- step 0（首步）→ **留**；
- new_tab 类不受影响。

合并后回放不再重复定向。

---

## 5. 可选增强：回放侧 URL 幂等（双保险）

`_action_navigate`（`actions.py:493`）或 `session.navigate`（`session.py:1720`）开头加：

```
当前 URL == 目标 URL（规范化比较：去 hash、统一尾斜杠、query 参数排序）
  → 直接返回 no-op，不调 Page.navigate
```

对「录侧万一没排除干净的自动跳转」兜底，也顺带优化其它重复导航。本轮**不强制做**，列为后续可选项。

---

## 6. 边界与风险

- **gap 误判**：用户主动 SPA 导航紧跟操作（<3s）会被丢。但按 §3 洞察，SPA 主动导航由 `click` 触发、`click` 已录、回放 `click` 会重新触发跳转——navigate 冗余，丢掉反而正确。
- **首步 navigate**：始终保留（录制起点）。
- **连续 navigate**：前一步也是 navigate 时保留当前（避免连锁误丢）。
- **new_tab=True**：始终保留（主动开新 tab）。
- **done 步骤**：在 `denoise_steps` 之后由 `Recorder.stop()` 追加，不受此 pass 影响。

---

## 7. 验证方法

### 单元测试（后端）

`tests/test_recorder_event_mapper.py` 给 `denoise_steps` 加用例：

- `[navigate(A) 首步, upload_file, navigate(B) @0.9s]` → navigate(B) 丢，剩 `[navigate(A), upload_file]`；
- `[click, navigate(B) @0.04s]` → navigate 丢；
- `[click @t0, navigate @t5]`（>3s）→ navigate 留（模拟地址栏式主动）；
- `[navigate(A), navigate(B)]` → 都留（连续 navigate 不连锁丢）；
- `[navigate(new_tab=True)]` → 留；
- 同时验证 `step_number` 重排正确。

### 扩展手测 + 端到端

录抖音上传流程 → 查产物 json：不应有「紧跟 upload_file / click 的 navigate」 → `load_and_rerun` 回放，确认发布页表单状态不丢、无重复定向。

---

## 8. 实现落点清单

| 文件 | 位置 | 改动 |
|---|---|---|
| `src/tree_walker/recorder/event_mapper.py` | `denoise_steps`（`dedupe_uploads` 之后） | 新增 navigate-dedupe pass：副作用 navigate 丢弃 |
| `tests/test_recorder_event_mapper.py` | —— | 加 navigate-dedupe 用例 |
| `src/tree_walker/tools/actions.py` / `browser/session.py` | `_action_navigate` / `navigate` | （可选）URL 幂等 |

后端改动跑 `uv run python -m pytest tests/test_recorder_event_mapper.py tests/test_recorder.py -v`。
