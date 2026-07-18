# 解决 modal 触发器 click 录制问题方案

> 问题：重放 recorded.json 时，modal 内步骤（封面编辑器/合集下拉/自主声明/发布）全失败——modal 没打开。
> 目标：让录制扩展正确捕获「打开 modal 的触发器 click」，使回放能打开 modal。

---

## 1. 背景：modal 内步骤回放全失败

重放抖音上传流程（`recorded.json`），所有 xpath 含 `div[12]`（抖音 modal 容器）的步骤都找不到元素：

| 失败步骤 | 元素 | modal |
|---|---|---|
| step 8/9 | 封面 `<input type=file>` | 封面编辑器 |
| step 10 | 「完成」button | 封面编辑器 |
| step 13 | 合集选项 `<div role=option>` | 合集下拉 |
| step 15 | 自主声明 radio `<label>` | 自主声明弹窗 |
| step 17 | 「发布」button | 确认弹窗 |

回放时这些 modal 没打开，元素不在 DOM。20 步中 8 步成功、12 步失败。

**回放代码本身正常**——指纹 5 级匹配（EXACT/STABLE/XPATH/AX_NAME/ATTRIBUTE）、retry 3 次、菜单重打开 3 次、AI 摘要都工作。前 7 步（导航/上传视频/标题/描述）指纹重匹配完美（`6777→18516` 等）。

---

## 2. 关键洞察：后端 selector_map 已收录 div 触发器，问题在录制侧

后端 `dom.py` 的 `is_interactive`（line 81-213）判定元素是否进 `selector_map`，规则覆盖 div 触发器：

| 规则 | 信号 | 抖音场景 |
|---|---|---|
| 规则 3 | JS 点击监听器（`has_js_click_listener`，React onClick/Vue @click/addEventListener） | 「选择封面」「自主声明」入口 |
| 规则 10 | `onclick`/`onmousedown`/`tabindex` 属性 | 内联事件 |
| 规则 11/12 | ARIA role / AX role | `role=option` 合集选项 |
| **规则 14** | **`cursor: pointer`（最终兜底）** | **Semi select 触发器等 div** |

所以抖音「选择封面」、`semi-select-selection` 等 cursor:pointer div **已在 selector_map**，回放**能定位**（如果录制录了正确指纹）。

**问题在录制侧**：`findInteractiveAncestor`（`action-recorder.ts:42-53`）只用 `INTERACTIVE_SELECTOR`（CSS 选择器 `a/button/input/select/textarea/[role]` 等），**不含 cursor:pointer / onclick 检测**——比后端 `is_interactive` 窄。

---

## 3. 根因（两层）

### 3.1 录错元素（合集 select，step 12）

用户点 `semi-select-selection`（cursor:pointer div 触发器）打开下拉。`onClick` 的 `raw = e.composedPath()[0]` 是内部 span/div。`findInteractiveAncestor(raw)` 只用 CSS 选择器向上找——`semi-select-selection` 是 div 无 role，不匹配 `INTERACTIVE_SELECTOR` → 找不到可交互祖先，返回 `raw`（内部 div）→ **录内部 div 指纹**。

回放 `step 12` 用内部 div 指纹匹配 selector_map（内部 div），点击它——**不开下拉**（要点 `semi-select-selection` 触发器）。菜单重打开机制重执行 step 12（点同一个内部 div）也无效。

### 3.2 漏录（封面编辑器 step 7→8、自主声明 step 14→15）

打开这些 modal 的 click 在 `recorded.json` 里**完全缺失**（连 `click {}` 噪声都没有）→ `onClick`（window capture）**没收到**这些 click。根因待查：
- 抖音用 `mousedown` 触发（非 `click`）？
- 点击在 iframe？
- React 合成事件未派发原生 click 到 window？

> **注**：A2（`onClick` 排除 `raw=<input type=file>` 的 click）**不误伤**可见元素 click——可见「选择封面」click 的 `raw=div`，不匹配 file input，不被排除。所以 A2 不是漏录原因。

---

## 4. 方案

### A. `findInteractiveAncestor` 增强（对齐后端 `is_interactive` 规则 10/14）—— 解决「录错」

录制侧 `findInteractiveAncestor` 在现有 `INTERACTIVE_SELECTOR` 之外，加两条识别（对齐后端）：

```ts
function findInteractiveAncestor(el: Element | null): Element | null {
  let cur: Element | null = el;
  while (cur && cur !== document.body) {
    try {
      if (cur.matches(INTERACTIVE_SELECTOR)) return cur;
      // 对齐后端 is_interactive 规则 14：cursor:pointer
      if (window.getComputedStyle(cur).cursor === 'pointer') return cur;
      // 对齐后端规则 10：onclick/onmousedown 属性
      if ((cur as HTMLElement).onclick || cur.getAttribute('onclick') || cur.getAttribute('onmousedown')) return cur;
    } catch { /* 非元素节点 */ }
    cur = cur.parentElement;
  }
  return el;
}
```

> JS `addEventListener` 监听器（后端规则 3，最强信号）content script 难检测（`getEventListeners` 是 DevTools API），但 cursor:pointer + onclick 覆盖绝大多数 div 触发器。

**效果**：用户点 div 触发器时，`findInteractiveAncestor` 向上找到 cursor:pointer 触发器、录它的指纹 → 回放 `selector_map` 有该触发器（后端规则 14 收录）→ 指纹匹配 → 点击触发器 → modal 打开。

**解决**：合集 select（step 12 录 `semi-select-selection` 而非内部 div）+ 凡 `onClick` 收到的 div 触发器。

**落点**：`recording_extension/capture/action-recorder.ts` `findInteractiveAncestor`（42-53）。

### B. 漏录诊断（`onClick` 没收到的 click）—— 解决「漏录」

封面/自主声明 click 缺失，先诊断再定方案。`onClick` 加临时 DEBUG 日志：

```ts
const onClick = (e: Event) => {
  flushInput(sendEvent);
  const raw = e.composedPath()[0] as Element || (e.target as Element | null);
  // DEBUG：记录每个 capture click
  console.log('[TW Recorder] click capture raw=<%s> path=%s', raw?.tagName, raw?.getAttribute?.('class'));
  ...
};
```

用户重录「选择封面」「自主声明」，看控制台日志：
- **`onClick` 没触发** → 抖音用非 click 事件（mousedown？）或 iframe 或 React 合成事件未冒泡 → 对症（加 mousedown 监听 / 处理 iframe / 改 `event.target`）。
- **`onClick` 触发但 `raw=file input` 被 A2 排除** → 收紧 A2（只排除隐藏 file input）。
- **`onClick` 触发 + 录到了** → 查为何 `recorded.json` 没有（denoise 误丢？）。

**落点**：`action-recorder.ts` `onClick`（120）加临时 DEBUG 日志；诊断后按根因改。

### C. 不改

- **A2 file-input 排除**：不误伤可见 click，保留。
- **后端 `selector_map` / `is_interactive`**：已收录 cursor:pointer div（规则 14），不改。

---

## 5. 边界与风险

- **`getComputedStyle` 性能**：`findInteractiveAncestor` 每次向上遍历调 `getComputedStyle`。click 频率不高，可接受；必要时缓存或限层深。
- **cursor:pointer 误识别**：装饰性 div 也可能 cursor:pointer。但录到 cursor:pointer div 比录内部 span/div 更接近真正触发器，且后端 selector_map 用同样规则（一致性，回放能匹配）。
- **漏录 B 根因未定**：A 先上（确定收益），B 需用户重录诊断配合。
- **旧 `recorded.json` 不变**：A 增强后需**重新录制**（新 `recorded.json` 的触发器步骤录真正触发器指纹），回放才生效。

---

## 6. 验证

1. **findInteractiveAncestor 手测**：console 里对合集 select 内部元素调 `findInteractiveAncestor`，应返回 `semi-select-selection`（cursor:pointer）。
2. **合集 select**：重录 → 检查 step 12 录的是 `semi-select-selection` 而非内部 div → 回放 step 12 点击开下拉 → step 13 选项可定位。
3. **漏录诊断**：重录「选择封面」，看 `onClick` DEBUG 日志定根因。
4. **端到端**：重录完整流程 → 重放 → 封面编辑器/合集下拉/自主声明 modal 能打开，modal 内步骤成功。

---

## 7. 实现落点清单

| 文件 | 改动 |
|---|---|
| `recording_extension/capture/action-recorder.ts` | `findInteractiveAncestor` 加 cursor:pointer + onclick 检测（A）；`onClick` 加临时 DEBUG 日志（B） |
| （B 诊断后）`action-recorder.ts` | 按根因改：mousedown 监听 / `event.target` / iframe 处理 / A2 收紧 |

扩展改动需 `npm run build`；录制侧改完后需**重新录制** `recorded.json` 再重放验证。

---

## 8. CDP 实测诊断 + 根因修正（补充，撤销 §4-B 的猜测）

跑 `examples/debug_cover_auto.py`（CDP 点击 + capture 监听）+ `debug_cover_struct.py`（dump React handler）+ `debug_dedupe_upload.py`（验证吸收）后，修正前文 §3-4 的部分根因。

### 8.1 click 事件正常派发（撤销 §4-B 的 mousedown/iframe/React 猜测）

CDP 注入 window capture 监听 + 点击封面，捕获完整 `pointerdown→mousedown→pointerup→mouseup→click` 序列（target=`div.filter-k_CjvJ`）。**click 正常派发到 window**，扩展 `onClick`（同 window capture）能收到。所以「选择封面」漏录**不是「click 没派发」**——**撤销 §4-B** 的「抖音用 mousedown / iframe / React 合成事件不冒泡」猜测，也**不需要** onClick DEBUG 日志诊断。

### 8.2 封面区结构：`cover-Jg3T4p` 绑 React onClick = 真正触发器

```
cover-Jg3T4p (div, cursor:pointer, React onClick)        ← 打开编辑器触发器
├─ background-OpVteV filter-k_CjvJ (蒙层, cursor:pointer, 无 onClick)
│  └─ filter-k_CjvJ / svg / title-wA45Xd (子元素, cursor:pointer)
```

- `cover-Jg3T4p` 绑 React `onClick`（打开封面编辑器）——后端 `selector_map` 收录（`is_interactive` 规则 14 cursor:pointer）。
- CDP 点击命中的是上层 `filter-k_CjvJ`（蒙层，无 onClick），所以 CDP click 没打开编辑器；但用户真实点击事件冒泡到 `cover-Jg3T4p` 触发 React onClick，会打开编辑器。

### 8.3 封面漏录真根因：`dedupe_uploads` 误吸收（已验证）

`examples/debug_dedupe_upload.py` 验证：序列 `[input(简介), click(选择封面), upload_file(封面)]` 经 `denoise_steps` 后，**click 被吸收**，只剩 `input + upload_file`。

根因：`dedupe_uploads`（upload-dedupe 方案 B1）吸收 upload_file 前的 click（最多 2 个），把「选择封面」click（**打开编辑器**，在封面上传前一步）误当 upload_file 的前置「上传按钮」click 吸收。回放时编辑器没打开 → 封面 input 找不到 → step 8/9/10 失败。

### 8.4 修正后的根因与方案对照

| modal 失败 | 真根因（实测） | 方案 |
|---|---|---|
| 封面编辑器（step 8/9/10） | `dedupe_uploads` 误吸收「选择封面」click（已验证） | **新方案 C**：dedupe_uploads 收紧 |
| 合集 select（step 12/13） | `findInteractiveAncestor` 录内部 div（cursor:pointer 触发器未识别） | 方案 A |
| 自主声明（step 14/15） | 同上（step 14 `click {}` = 录内部 + locate 失败） | 方案 A |
| 发布（step 17） | 前置 modal 没开，连锁失败 | 解决 A/C 后自然好 |

### 8.5 方案修正

- **A 保留**（`findInteractiveAncestor` 加 cursor:pointer + onclick 检测）：解决合集 select + 自主声明（凡 `onClick` 收到的 div 触发器）。落点不变：`action-recorder.ts:42-53`。
- **B 撤销**（`onClick` 漏录诊断 / DEBUG 日志）：click 派发正常，`onClick` 收到了，不需诊断。
- **新 C**：`dedupe_uploads` 收紧——**只吸收 fakepath `input_text`**（file input change 误录，明确的 upload 噪声），**不吸收 click**（click 可能是「打开编辑器/弹窗」等关键操作，吸收会丢）。上传按钮 click 保留无害（回放点它，file-chooser intercept 吞，`upload_file` 仍用 `setFileInputFiles` 直注）。落点：`src/tree_walker/recorder/event_mapper.py` `dedupe_uploads`（移除 click 吸收分支，只留 fakepath input_text）。

### 8.6 验证（更新）

- **封面**：收紧 `dedupe_uploads`（C）后重录 → `recorded.json` 含「选择封面」click → 回放打开编辑器 → 封面 input 可定位。
- **合集/自主声明**：`findInteractiveAncestor` 增强（A）后重录 → click 录 cursor:pointer 触发器（`cover-Jg3T4p`/`semi-select-selection` 等）→ 回放打开 modal。
- 单测：`debug_dedupe_upload.py` 的序列收紧后应保留 click；`test_recorder_event_mapper` 的 upload dedupe 用例需相应更新（click 不再吸收）。

### 8.7 实现落点（更新）

| 文件 | 改动 |
|---|---|
| `recording_extension/capture/action-recorder.ts` | `findInteractiveAncestor` 加 cursor:pointer + onclick 检测（A） |
| `src/tree_walker/recorder/event_mapper.py` | `dedupe_uploads` 收紧：只吸收 fakepath input_text，不吸收 click（C） |
| `tests/test_recorder_event_mapper.py` | upload dedupe 用例更新（click 不再被吸收） |

