# 录制文件上传去重方案

> 问题：录制「上传文件」时，单次上传被录成 4 步，回放只有最后 1 步有效。
> 目标：把一次上传操作合并成单步 `upload_file`（index 指向 `<input type=file>`），与 agent 手工产出的 `douyin_upload_history.json` 同构，回放走 CDP `DOM.setFileInputFiles` 直传。

---

## 1. 背景：录成了 4 步，回放只要 1 步

实测录制抖音「上传视频」（`recorded.json` step 2–5）：

| step | action | 命中元素 | 回放会发生什么 |
|---|---|---|---|
| 2 | `click` idx=1283 | "上传视频" `BUTTON.container-drag-btn` | 回放时 `file-chooser intercept` 已开，click 触发 `Page.fileChooserOpened` 被 intercept 吞，页面无反应 |
| 3 | `click` idx=1293 | 隐藏的 1×1 `<input type=file>`（按钮 JS 调 `input.click()` 冒泡上来） | 元素 `width:1px;height:1px;clip:rect(0,0,0,0)` 视觉隐藏，回放 `click_element` 大概率判不可点/失败 |
| 4 | `input_text` = `C:\fakepath\2026-04-29-20-41-59.mp4` | （误录） | file input 只读，回放 `input_text` 无效 |
| 5 | `upload_file` path=`…mp4` | index **缺失**（`interacted_element=[null]`） | ✅ 唯一真正干活的那步，但因没 index 无法回放 |

期望产物（`douyin_upload_history.json` step 1）只有 1 步：

```json
{
  "name": "upload_file",
  "params": { "index": 4490, "path": "D:\\…\\2026-04-29-20-41-59.mp4" }
}
```

回放时 `_action_upload_file`（`actions.py:1600`）见 index 指向的元素 `is_file_input`（tag=INPUT&type=file）→ 直接 `set_file_input` → `DOM.setFileInputFiles` 注入文件，**不弹原生文件框**。所以前 3 步对回放要么无效要么报错，必须合并掉。

---

## 2. 根因（三层）

### 2.1 扩展 `onInput` 没排除 file input → 误录 `input_text`（step 4）

`recording_extension/capture/action-recorder.ts:125`：

```ts
const onInput = (e: Event) => {
  const raw = (e.composedPath()[0] as Element) || (e.target as Element | null);
  if (!raw || raw.nodeType !== Node.ELEMENT_NODE) return;
  const target = findInteractiveAncestor(raw) ?? raw;
  setPending(sendEvent, target);   // ← file input 的 value 变化也走这里
};
```

`<input type=file>` 在用户选完文件后，`value` 会被浏览器设成 `C:\fakepath\<文件名>`（安全伪路径，全平台一致）并触发 `input` 事件。`onInput` 没识别这种情况，把它当文本输入 `setPending` → `flushInput` 发出 `input_text` 事件 → step 4。

### 2.2 扩展 `onClick` 没排除 file input 的编程 click → 误录 click（step 2/3）

`action-recorder.ts:110`：

```ts
const onClick = (e: Event) => {
  flushInput(sendEvent);
  const raw = e.composedPath()[0] as Element || (e.target as Element | null);
  if (!raw || raw.nodeType !== Node.ELEMENT_NODE) return;
  const target = findInteractiveAncestor(raw) ?? raw;
  sendEvent({ type: 'click', xpath: ref.xpath, ... });
};
```

抖音上传按钮（`BUTTON.container-drag-btn`）的点击处理器里 JS 调 `hiddenInput.click()` 打开文件框。这个编程 click：
- `raw` = file input 本身（`INPUT` 匹配 `INTERACTIVE_SELECTOR`，`findInteractiveAncestor` 返回它自己）→ 录成 step 3（click idx=1293）。
- 用户实际点的按钮 `raw` = BUTTON → 录成 step 2（click idx=1283）。

两个 click 来自同一次用户操作，且 `upload_file`（`change` 事件）会覆盖它们，属冗余。

### 2.3 `upload_file` 事件到达后端时定位失败 → index 缺失

`onFileChange`（`action-recorder.ts:171`）发出的 `upload_file` 事件 `xpath` 指向 file input 本身。但事件到达后端时（`recorder.py` `handle_event` 走 `locate_by_xpath`），文件已选、页面 DOM 正在重绘（视频开始上传），selector_map 短暂失配 → 定位失败 → step 5 `params` 无 `index`、`interacted_element=[null]`。

而紧前的 step 3 click 定位**成功**（index=1293，`interacted_element` 完整），说明这个隐藏 file input **是被 selector_map 收录的**——step 5 失败纯属时序。合并时把 step 3 的 index 借给 step 5 即可。

---

## 3. 解法：源头为主（扩展）+ 后端兜底（denoise）

> 推荐**双端都改**：A 把歧义消解在源头，B 兜底旧扩展 + 补 index。若要最小改动，**只做 B 也能解决**（fakepath 模式识别 + index 补全）。

### A. 扩展端（`action-recorder.ts`）—— 主力

**A1：`onInput` 排除 file input**（125 行函数体开头）：

```ts
const onInput = (e: Event) => {
  const raw = (e.composedPath()[0] as Element) || (e.target as Element | null);
  if (!raw || raw.nodeType !== Node.ELEMENT_NODE) return;
  // file input 选文件后 value 变为 C:\fakepath\<名>，不是用户文本输入 ——
  // 交给 onFileChange 录 upload_file，这里跳过避免误录 input_text。
  if (raw.tagName === 'INPUT' && (raw.getAttribute('type') || '').toLowerCase() === 'file') return;
  const target = findInteractiveAncestor(raw) ?? raw;
  setPending(sendEvent, target);
};
```

消灭 step 4 的 fakepath `input_text`。

**A2：`onClick` 排除 file input 的 click**（110 行函数体开头，`flushInput` 之后、取 `raw` 之后）：

```ts
const onClick = (e: Event) => {
  flushInput(sendEvent);
  const raw = e.composedPath()[0] as Element || (e.target as Element | null);
  if (!raw || raw.nodeType !== Node.ELEMENT_NODE) return;
  // file input 的 click 几乎都是上传按钮 JS 触发的 input.click()（非用户直接点），
  // 后续 change 会录 upload_file；跳过避免冗余 click（回放 upload_file 不需要先点）。
  if (raw.tagName === 'INPUT' && (raw.getAttribute('type') || '').toLowerCase() === 'file') return;
  const target = findInteractiveAncestor(raw) ?? raw;
  ...
};
```

消灭 step 3。

> **零副作用**：file input 的完整交互语义已由 `onFileChange`（`change` → `upload_file`）覆盖；`input`/`click` 事件跳过不丢任何信息。罕见场景「用户直接点可见 file input」也被 `change` 覆盖。

A1/A2 后事件流收敛为：`click` 上传按钮（step 2）+ `upload_file`。残留的 step 2 click 由后端 B 吸收。

### B. 后端（`event_mapper.py: denoise_steps`）—— 兜底 + index 补全

在 `denoise_steps`（109 行，`Recorder.stop()` 落盘前调用）现有 input/click/scroll 合并**之前**，加一个 upload-dedupe pass。

**B1：吸收 upload_file 前的冗余步骤**。对每个 `upload_file` 步骤 U，向前看连续的「上传相关步骤」并删除：

- `input_text` 且 `text` 匹配 fakepath（含 `fakepath` 子串，或 basename 与 U 的 `path` 一致）——兜底旧扩展未上 A1 的情况；
- `click`（上传按钮 / file input / 拖拽区）。

停止条件（任一）：遇到非 `{click, input_text}` 的步骤（navigate / send_keys / scroll 等）；或时间戳早于 U 超过窗口（建议 **~10s**，覆盖用户在原生文件框里挑文件的时间——实测 step 3→step 4 间隔约 7.4s 就是选文件耗时）。

**B2：index 补全**。U 若 `params` 无 `index`，从被吸收的步骤里找定位成功（`interacted_element` 非 null）且 `backend_node_id` 指向 file input（`tag=INPUT` & `type=file`）的那条，把它的 `index`/`backend_node_id` 借给 U，并复制其 `interacted_element` 指纹。

> **可行性依据**：本仓 selector_map 的 `index` == `backend_node_id`（`recorded.json` step 2/3、`douyin_upload_history.json` step 1 均验证：1283=1283、1293=1293、4490=4490）；且抖音这个隐藏 file input 被 selector_map 收录（step 3 click 定位成功可证）。借用后回放走 `is_file_input` 直传路径（`actions.py:1697`），与 `douyin_upload_history.json` step 1 完全等价。

### 合并后效果

`recorded.json` step 2–5 → 单步：

```json
{
  "name": "upload_file",
  "params": { "index": 1293, "path": "rerun-history\\uploads\\2026-04-29-20-41-59.mp4" }
}
```

`index=1293` 来自被吸收的 step 3（file input），回放直传成功。

---

## 4. 边界与风险

- **用户直接点可见 file input**：A2 跳过其 click，但 `upload_file` 由 `change` 覆盖，不丢信息。
- **选文件后取消**（未产生 `upload_file`）：B1 不触发吸收，前置 click 保留（回放无害，最坏被 intercept 吞）。
- **多 file input 场景**（抖音封面，`recorded.json` step 15–18 有两个封面 input）：每个 `upload_file` 独立按各自的 index/xpath 吸收各自的前置 click/input_text，互不串扰。B2 按 `backend_node_id` 精确匹配对应 file input，不会把横封面的 index 错借给竖封面。
- **B1 误吸收风险**：纯「类型 + 时间窗」匹配有歧义（理论上若用户点上传按钮后又点页面别处再选文件，中间那个 click 可能被吸收）。靠 A1/A2 把歧义消解在源头（A 之后残留仅 1 个上传按钮 click），后端窗口判据收敛为「紧邻 U 的连续 `{click, fakepath input_text}` 段」，实际风险很低。
- **A2 对非上传场景**：跳过的是 `<input type=file>` 的 click，普通按钮/链接 click 不受影响。

---

## 5. 验证方法

### 单元测试（后端 B）

`tests/test_recorder_event_mapper.py` 给 `denoise_steps` 加用例，喂入模拟 `recorded.json` step 2–5 的 4 个 `AgentHistory`（click 按钮 / click file input / fakepath input_text / upload_file 无 index），断言：

- 合并后只剩 1 步 `upload_file`；
- 其 `params.index` == 被借用的 file input index（1293）；
- `path` 保持原值；
- `step_number` 已重排；
- 同时覆盖「无 fakepath input_text（A1 已修）」「upload_file 自带 index 不借」「多 file input 各自合并」「取消选择无 upload_file 不吸收」等分支。

### 扩展手测（A）

- 改完 A1/A2 → `npm run build` → 浏览器重载扩展 → 录制上传视频；
- 后端控制台 `[TW Recorder]` 日志应只见 `click`（按钮）+ `upload_file`，不再有 fakepath 的 `input_text` 和 click file input。

### 端到端

- 录制 → 落盘 `rerun-history/xxx.json`；
- 检查该 step 为单步 `upload_file` 且带 index；
- `load_and_rerun` 回放，确认文件成功上传（页面出现上传后的预览/跳转）。

---

## 6. 实现落点清单

| 文件 | 位置 | 改动 |
|---|---|---|
| `recording_extension/capture/action-recorder.ts` | `onClick` 110 行 / `onInput` 125 行 | 各加 file input 排除（A2 / A1） |
| `src/tree_walker/recorder/event_mapper.py` | `denoise_steps` 109 行 | 新增 upload-dedupe pass：B1 吸收 + B2 index 补全（置于现有合并之前） |
| `tests/test_recorder_event_mapper.py` | —— | 加 `denoise_steps` 合并用例 |

扩展端改动需 `npm run build` 重新加载；后端改动跑 `uv run python -m pytest tests/test_recorder_event_mapper.py tests/test_recorder.py -v`。
