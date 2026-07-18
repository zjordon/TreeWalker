# 对齐 Browser-BC 录制方案（聚焦回放价值）

> 目标：参考 `D:\dev\git\ai\Browser-BC\extension\src\capture\action-recorder.ts` 的优雅实现，完善 TreeWalker 录制扩展。
> 范围：**选择性对齐**——只搬对回放有增益的事件/字段 + 代码结构优雅化，不照搬分析型事件。

---

## 1. 背景：两项目目标不同，决定了「对齐」要筛选

| | Browser-BC | TreeWalker |
|---|---|---|
| 目标 | 忠实录制所有 DOM 事件供后台**分析** | 录制 → 拼成**可回放的 action** |
| 事件 | click/dblclick/drag/drop/input/change/paste/composition/submit/keydown/scroll/focus/blur/contextmenu/copy/cut/selection/file_select | click/input_text/select_dropdown/scroll/navigate/send_keys/upload_file/switch_tab/close_tab |
| 后端 | 存档/分析事件流 | 实时拼 `AgentHistory` → 回放 |

Browser-BC 的多数事件（paste/focus/blur/selection/contextmenu/copy/cut/submit/dblclick/drag/drop）在 TreeWalker **没有对应回放 action**——照搬录下来回放不了，只增臃肿。所以本方案聚焦「对回放有增益」的对齐项。

---

## 2. 核心改进（功能修复，对回放直接增益）

### 2.1 IME composition 处理（彻底修中文输入中间拼音值）

**现状**：中文输入法 composing 期间，`input` 事件和 contenteditable 的 `MutationObserver` 会带中间拼音值触发 `setPending`。`setPending` 虽累积覆盖取最后值，但**长 composing（选字停顿 >400ms）**会让 400ms timer 在 composing 中触发 `flushInput`，发出中间拼音值（`recorded.json` step 4 `"Ai浏览器第王"` 的 `"王"` 即此类误值）。

> 注：编辑键（Backspace 等）打断合并的主因，上一轮 `EDIT_KEYS` 修复已解决。这里处理的是残留的「长 composing 中间 flush」。

**方案**（仿 Browser-BC `compositionstart`/`compositionend` 监听 + `isComposing` flag）：

```ts
let isComposing = false;
const onCompositionStart = () => { isComposing = true; };
const onCompositionEnd = () => { isComposing = false; };
// onInput 与 MutationObserver 回调里：
if (isComposing) return;  // composing 中不 setPending，等 compositionend 后的最终值
```

- `compositionstart` → `isComposing = true`；
- `compositionend` → `isComposing = false`（浏览器随后发的 `input` 事件带最终值，此时录）；
- `onInput`（line 138）与 contenteditable `MutationObserver` 回调（line 251）里 `if (isComposing) return`，composing 中不 `setPending`。

**落点**：`recording_extension/capture/action-recorder.ts` —— 新增 composition 监听 + `isComposing` 抑制。

### 2.2 contenteditable 用 innerText（去噪）

**现状**：`readValue`（`action-recorder.ts:95`）对 contenteditable 用 `textContent`，带入 Slate 等编辑器的零宽字符（`recorded.json` step 12 文本末尾的 `​` U+200B）等噪声。

**方案**：对齐 Browser-BC `valueFor`（`action-recorder.ts:381`）—— contenteditable 用 `innerText`（更接近用户可见文本）：

```ts
function readValue(target: Element): string {
  if ((target as HTMLElement).isContentEditable) return (target as HTMLElement).innerText;
  if ('value' in target) return String((target as HTMLInputElement).value);
  return '';
}
```

**落点**：`recording_extension/capture/action-recorder.ts:95`。

---

## 3. 代码结构优雅化（重构，低风险，行为须不变）

### 3.1 `on()` 工厂 + cleanup 收集器

**现状**：`installActionRecorder` 末尾 7 处手动 `addEventListener` + return 里 7 处手动 `removeEventListener`（line 238-244 / 277-283），冗余且易漏。

**方案**（仿 Browser-BC `on()` line 333-342）：

```ts
const cleanup: Array<() => void> = [];
function on<K extends keyof WindowEventMap>(
  type: K, listener: (e: WindowEventMap[K]) => void,
) {
  window.addEventListener(type, listener, { capture: true, passive: true });
  cleanup.push(() => window.removeEventListener(type, listener, { capture: true } as EventListenerOptions));
}
// 调用：on('click', onClick); on('input', onInput); ...
// 卸载：cleanup.splice(0).forEach((d) => d());
```

return 时 `cleanup.splice(0).forEach(d => d())`，一行替代 7 行 remove。

**落点**：`action-recorder.ts` install 末尾 add/removeEventListener 块。

### 3.2 `emit()` 统一发送（填 ts）

**现状**：每个 handler 手动 `ts: Date.now()`（8 处）。

**方案**（仿 Browser-BC `emit` line 39-54）：

```ts
const emit = (partial: Omit<RecorderEvent, 'ts'>) => {
  sendEvent({ ...partial, ts: Date.now() });
};
// 调用：emit({ type: 'click', xpath, ... });
```

**落点**：`action-recorder.ts` 各 handler 的 `sendEvent` 调用。

> **handler 工厂不照搬**：Browser-BC 的 `mouseHandler`/`inputHandler`/`focusHandler` 适合它（handler 逻辑简单且相似）。TreeWalker 的 `onClick`/`onInput` 含特殊逻辑（file input 排除、`findInteractiveAncestor`、`flushInput` 时序、`EDIT_KEYS` 判断），抽象成工厂反而损失清晰度。

---

## 4. 可选（评估回放侧后定）

### 4.1 click 带 modifiers

**现状**：`onClick`（line 120）不录修饰键。

**方案**：仿 Browser-BC `modifiersFor`（line 349），click 事件带 `modifiers: {ctrl, shift, alt, meta}`。

**前提**：回放侧 `_action_click`（`src/tree_walker/tools/actions.py`）/ `click_element`（`session.py`）要支持按 modifiers 修饰点击（如 Ctrl+click 新标签打开）。**如不支持，录了仅存档无回放价值**——需先确认回放侧，再决定做不做。

**落点**：`action-recorder.ts:120` onClick + `shared/types.ts` `RecorderEvent` 加可选 `modifiers`。

---

## 5. 明确不做（TreeWalker 目标 = 录可回放的 action）

| Browser-BC 有 | 不做的原因 |
|---|---|
| paste / submit / focus / blur / selection / contextmenu / copy / cut / dblclick / drag / drop | 无对应回放 action，仅分析价值 |
| checkbox/radio → `String(checked)` | TreeWalker 录 checkbox 是 click（切换），不走 input value 路径，`readValue` 不被调用——现状 OK |
| `nav_type` / `from_url` / `to_url`（navigation） | 回放 navigate 只需 to_url |
| value 脱敏结构（`RedactedValue`） | 本地工具，不脱敏 |
| selector 加 `inputType` / rect 改 `{w,h}` | TreeWalker selector 已够用（指纹在后端算），改名是 churn |
| SHORTCUT_KEYS 含 Backspace/Delete（独立 send_keys） | TreeWalker 已用 `EDIT_KEYS` 把它们归 input 最终值（更优，不打断合并） |

---

## 6. 验证

- **IME**：录中文输入（拼音选字，含长停顿 >400ms），产物 `input_text` 是最终中文，无中间拼音值/单字碎片。
- **innerText**：contenteditable 输入产物无零宽字符（U+200B 等）。
- **重构**：`on`/`emit` 重构后，现有录制行为不变（click/input_text/upload_file/scroll/select_dropdown/send_keys 都正常）——手测一个完整流程 + 后端 `test_recorder*` 不回归。
- action-recorder 无 TS 单测，靠手测 + 后端测试不回归兜底。

---

## 7. 实现落点清单

| 文件 | 改动 |
|---|---|
| `recording_extension/capture/action-recorder.ts` | composition 监听 + `isComposing` 抑制；`readValue` contenteditable 用 innerText；`on()` 工厂 + cleanup；`emit()` 统一；（可选）click modifiers |
| `recording_extension/shared/types.ts` | （可选）`RecorderEvent` 加 `modifiers?` |
| `src/tree_walker/tools/actions.py` / `browser/session.py` | （可选）`_action_click` / `click_element` 支持 modifiers |

扩展改动需 `npm run build`；后端改动跑 `uv run python -m pytest tests/test_recorder*.py -v`。
