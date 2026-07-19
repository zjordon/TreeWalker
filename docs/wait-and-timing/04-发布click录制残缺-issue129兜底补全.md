# 04：发布 click 录制残缺 —— issue #129 兜底补全 + 常驻 server 重启陷阱

> **关联**：#123（等待机制总览）｜ #129（录制异常兜底）｜ #126（阶段3 落地后实测发现）｜ 实测案例 `rerun-history/douyin_redesign10.json`
> **状态**：已修复（`recorder.py::handle_event` 三重兜底）+ 单测覆盖 + 全量回归通过
> **体裁**：故障分析 + 修复记录（postmortem）
> **对照源**：基于本会话修复后代码（`recorder.py` / `rerun.py` / `server.py`）

---

## 背景

阶段 3（#126）落地后实测 `rerun-history/douyin_redesign10.json`（抖音创作者发布流程）：重放到**流程最后一步 click（「发布」按钮）时没有实际执行**，日志显示：

```
⏭️  跳过 Step 21 (21/23) [...]: 无 index 且无 interacted_element（录制定位失败的噪声步）
```

最后一步是整个录制的关键动作（点发布），跳过 = 录制回放链路在此断掉。排查后定位到**录制端的一个兜底缺口**（issue #129 漏网场景），并在第二轮验证时踩了**常驻 server 不热重载**的运维陷阱。本文记录全过程。

---

## 现象

残缺步（第一次 `douyin_redesign10.json` 的 step 20 / 重新录制后的 step 21）的字段：

```json
{
  "model_output": {"actions": [{"name": "click", "params": {}}]},
  "interacted_element": null,
  "state_summary": {"url": "", "title": "", "duration": 0.0},
  "result": []
}
```

对比同文件正常 click 步（如 step 19）：

```json
{
  "model_output": {"actions": [{"name": "click", "params": {"index": 5480}}]},
  "interacted_element": [{"node_id": ..., "x_path": "...", "element_hash": ..., ...}],
  "state_summary": {"url": "https://creator.douyin.com/creator-micro/...", ...}
}
```

三个残缺字段拼出完整图景：

| 字段 | 残缺步 | 正常步 | 含义 |
|---|---|---|---|
| `params` | `{}`（无 `index`） | `{"index": N}` | 录制时没回填 index |
| `interacted_element` | `null` | `[{x_path, element_hash, ...}]` | 没拿到指纹，**也没存语义线索** |
| `state_summary.url` | `""` | `"https://..."` | 录制时 `state=None`（`get_state` 失败） |

---

## 直接原因（重放为何跳过）

`rerun.py::_skip_reason`（L506-517）把"需 index 但无 index 且无 interacted_element"的 click/input_text/select_dropdown 判定为录制定位失败的噪声步，跳过：

```python
# rerun.py::_skip_reason
if first and first.get("name") in ("click", "input_text", "select_dropdown"):
    fp = first.get("params") or {}
    if fp.get("index") is None and fp.get("element_id") is None:
        ie = item.interacted_element or []
        if not ie or ie[0] is None:
            return "无 index 且无 interacted_element（录制定位失败的噪声步）"
```

残缺步 `params={}`（无 index）+ `interacted_element=null`（`ie=[]`）→ 三条件全中 → **跳过**。这是正确的（无 index 无线索的 click 重放必报错，跳过比硬执行更安全），问题在**录制端不该让 target 动作以这种形态落盘**。

---

## 根本原因（录制为何残缺）

step 20/21 是流程**最后一步 click**（「发布」按钮）。点击发布 → 抖音**立即 submit + 页面跳转/卸载** → 录制端 `handle_event` 在 click 后处理时，CDP target 已卸载：

- `get_state` 失败 → `state=None` → `state_summary.url=""`。
- 定位拿不到目标 → 本应走兜底 `_store_semantic_clue` 存语义线索（`interacted=[{_semantic_clue, xpath, ...}]`，重放端可重新定位）。
- 但实际 `interacted_element=null`，说明**兜底根本没触发**。

### issue #129 兜底的缺口

`recorder.py::handle_event` **有**兜底机制（issue #129 / PR #130），专门处理"submit 跳转 target 卸载"。修复前的结构：

```python
# 修复前（简化）
action = translate_event(event, self.recording)   # ← try 之外！
if action is None: return None
...switch_tab / upload_file...
try:                                              # ← 定位块 try
    await self._ensure_target(...)
    state = await get_state(...)                  # 内层 try/except Exception 容错
    ...locate（命中设指纹 / 未命中 _store_semantic_clue）...
except BaseException as e:                        # ← 定位块 BaseException 兜底
    pending_exc = e
    if action.action_name in ("click", ...):
        self._store_semantic_clue(action, event, True, 0)   # 存语义线索
```

定位块兜底覆盖了 `_ensure_target` / `get_state` 抛 BaseException（含 `CancelledError`，不被内层 `except Exception` 抓），已有测试 `test_locate_block_base_exception_stores_semantic_clue` / `test_ensure_target_exception_stores_semantic_clue` 验证。

**缺口**：`translate_event`（L125）在 `try`（L153）**之外**。若它抛异常（页面卸载瞬间的残缺事件、状态机更新异常等），异常直接逃逸 `handle_event`，**不进任何兜底**。此时 `translate_event` 内部已把残缺 action append 到 `recording.actions`（`interacted_element=null` 默认），server `_handle_event` 捕获异常返 500，但残缺 action 已落盘。

`_store_semantic_clue` 本身是无条件赋值（`interacted=[{_semantic_clue, ...}]`，全是 `.get` 不抛）—— 所以只要 click 走到任何赋值路径，`interacted` 都不该是 `null`。step 20/21 是 `null` = 兜底链路完全没触及它 = `translate_event` 阶段逃逸。

---

## 修复：handle_event 三重兜底

`recorder.py::handle_event`（L115-254）重构为：**translate_event 纳入外层 try** + **统一兜底用 `recording.actions[-1]` 兜住 translate_event 抛时 action 变量为 None 的场景**。

### 结构

```python
# recorder.py::handle_event（修复后，简化）
if not self._recording: return None
action: ActionRecord | None = None
state = None; retried = False; pending_exc = None
appended_at = len(self.recording.actions)          # ← translate_event 前；异常时定位它 append 的 action
try:
    action = translate_event(event, self.recording)  # ← 纳入 try（原在 try 外）
    if action is None: return None
    ...switch_tab / upload_file...
    await self._ensure_target(...)
    try: state = await get_state(...)
    except Exception: state = None                  # 内层容错
    ...locate（命中设指纹 / 未命中 _store_semantic_clue）...
except BaseException as e:                          # 外层兜底（translate_event / 定位块 / CancelledError）
    pending_exc = e
    if action is not None and action.action_name in ("click", "input_text", "select_dropdown"):
        self._store_semantic_clue(action, event, True, 0)

# 统一兜底（issue #129 补全）：本事件 append 的 target 动作若 interacted 仍空 → 强制存语义线索
this_action = action if action is not None else (
    self.recording.actions[-1] if len(self.recording.actions) > appended_at else None
)
if this_action is not None and this_action.action_name in ("click", "input_text", "select_dropdown"):
    ie = this_action.interacted_element
    if ie is None or (isinstance(ie, list) and not ie):
        self._store_semantic_clue(this_action, event, True, 0)   # ← 强制语义线索

if this_action is not None:
    this_action.page_url = state.url if state else ""
if pending_exc is not None: raise pending_exc       # 异常仍传播（不吞 CancelledError）
return this_action
```

三重保证 `click/input_text/select_dropdown` 永不以 `interacted=null/[]` 落盘：
1. **定位未命中 / 异常** → `_store_semantic_clue`（既有）。
2. **外层 `except BaseException`** → 捕获 translate_event / 定位块逃逸（含 `CancelledError`）→ 存语义线索。
3. **统一兜底** → 任何路径下 `interacted` 仍空则强制存（catch-all，防御性）。

### 关键技术点：translate_event 抛时 `action=None` 但 `recording.actions[-1]` 残缺

这是修复的核心难点，第一次重构容易漏：

`translate_event` 抛异常时，`action = translate_event(...)` 的**赋值不会发生**（异常在赋值前抛），所以 `action` 变量保持初始 `None`。但 `translate_event` 内部已经把残缺 action `append` 到 `recording.actions`。

如果统一兜底只检查 `action` 变量（None），**不会救 `recording.actions[-1]` 里的残缺 target** —— step 20/21 就是这种情况，修复无效。

解决：用 `appended_at`（translate_event 前的 `len(recording.actions)`）判断是否新增，定位到 `recording.actions[-1]`：

```python
this_action = action if action is not None else (
    self.recording.actions[-1] if len(self.recording.actions) > appended_at else None
)
```

- 正常路径：`translate_event` 返回非 None → `action` 变量有值 → `this_action = action`。
- `translate_event` 抛（已 append）：`action=None`，但 `len > appended_at` → `this_action = recording.actions[-1]`（残缺 target）→ 统一兜底救回。
- `translate_event` 抛（未 append，如早期异常）：`len == appended_at` → `this_action=None` → 不误救前一步。

---

## 测试

`tests/test_recorder.py`（新增 + 既有，全过）：

- **新增** `test_translate_event_exception_keeps_click_semantic_clue`：mock `translate_event` 先 append 残缺 click 再抛 `BaseException`，断言 `recording.actions[-1]` 被统一兜底救回（`interacted[0]["_semantic_clue"] is True`）—— 直接回归 step 20/21 场景。
- **既有**（修复后仍过，证明重构没破坏 issue #129 原有保护）：
  - `test_locate_get_state_failure_records_semantic_clue`（get_state 抛 Exception）
  - `test_locate_block_base_exception_stores_semantic_clue`（get_state 抛 BaseException）
  - `test_nav_click_base_exception_survives_to_flatten`（端到端：BaseException → 落盘带线索 → flatten 不归一为 None）
  - `test_ensure_target_exception_stores_semantic_clue`（_ensure_target 抛 BaseException）
- **全量**：2028 passed，零回归。

---

## 运维陷阱：recorder server 常驻、无热重载（第二轮踩坑）

修复代码后，重新录制 `douyin_redesign10.json`，**step 21 仍是 `interacted=null`**（和修复前 step 20 同形态）。一度怀疑修复无效，排查后发现是**部署陷阱**：

### 根因

recorder 是**常驻 aiohttp server**（`server.py::run_server` → `web.run_app`，阻塞运行，**无 reload**）：

```python
# src/tree_walker/recorder/server.py
def run_server(recorder, host="127.0.0.1", port=8765, default_out="recorded.json") -> None:
    web.run_app(make_app(recorder, default_out), host=host, port=port)   # 阻塞，改 .py 不自动生效
```

启动入口 `examples/record_user_actions.py:69`。改了 `recorder.py` 后，**正在运行的 server 进程不会加载新代码** —— 重新录制跑的还是旧代码，step 21 注定残缺。

### 证据

- step 21 wallclock = 录制当天 20:22（新录制，确认重录了）。
- 但 `interacted=null` 是**旧代码行为**（translate_event 在旧 try 外、抛则不兜底）；修复后该是 `[{_semantic_clue, ...}]`。
- 修复逻辑本身正确（单测验证）→ 排除代码 bug → 指向 server 没重启。

### 教训

> **改 recorder 代码后，必须重启 `record_user_actions.py` server 才能生效。**「重新录制」≠「用新代码录制」—— 常驻进程不热重载。

---

## 操作：重启 + 重录 + 验证

```bash
# 1. 停掉当前 recorder server（跑 record_user_actions.py 的终端 Ctrl+C）

# 2. 重启加载新代码
uv run python examples/record_user_actions.py

# 3. 重新录制发布流程（Chrome 扩展操作）

# 4. 验证最后一步 click 带语义线索（不再是 null）
uv run python -c "import json; h=json.load(open('rerun-history/douyin_redesign10.json',encoding='utf-8'))['history']; \
[print('last click step',s.get('step_number'),'clue=',(s.get('interacted_element') or [{}])[0].get('_semantic_clue')) \
 for s in reversed(h) if ((s.get('model_output') or {}).get('actions') or [{}])[0].get('name')=='click'][:1]"
# 预期：clue= True
```

带 `_semantic_clue` 后，重放端 `locate_by_ref` 用 xpath/tag/rect 在新页面重新定位「发布」按钮，`_skip_reason` 不再跳过，最后一步会实际执行。

---

## 改动清单

| 文件 | 改动 |
|---|---|
| `src/tree_walker/recorder/recorder.py` | `handle_event`（L115-254）重构：translate_event 纳入外层 try + `appended_at` + 统一兜底 `this_action = action or recording.actions[-1]`，target 动作三重保证带语义线索；docstring 说明三重兜底 |
| `tests/test_recorder.py` | 新增 `test_translate_event_exception_keeps_click_semantic_clue`（translate_event 抛 → 统一兜底救回残缺 click） |

**未改**（验证后无需动）：
- `rerun.py::_skip_reason` —— 跳过无 index 无线索的 click 是正确行为，问题在录制端不该产出这种步。
- `recorder.py::_store_semantic_clue` —— 无条件赋值，本身健壮。

---

## 落地核对清单

- [x] `handle_event`：translate_event 纳入外层 try（原在 try 外是缺口）
- [x] `appended_at` 记录 translate_event 前长度，统一兜底用 `recording.actions[-1]` 兜住抛时 action=None 的残缺 target
- [x] 三重兜底：定位未命中/异常 + 外层 except BaseException + 统一兜底
- [x] 测试：新增 translate_event 抛场景 + 既有 4 条 BaseException 兜底测试全过 + 全量 2028 passed
- [x] 运维：recorder server 改代码后须重启 `record_user_actions.py`（常驻无 reload）
- [ ] 实测验证：重启 server + 重录 + 确认最后一步 click 带 `_semantic_clue` 且重放执行（待用户操作）
