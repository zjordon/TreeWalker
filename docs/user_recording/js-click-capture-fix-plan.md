# JS 监听器元素点击捕获修复：实施计划（方案 A：MAIN-world addEventListener hook）

> 状态：**实施计划（已评审，待编码）**。录制端把只通过 `addEventListener` 注册点击、无任何 DOM 级交互
> 信号的可点击元素（如 B 站封面 slot tab）的 click 漏掉，导致 `bili-2.json` step 6 被重放"冗余重试"跳过。
> 本计划给扩展补上与后端同等的 `addEventListener` 检测力（MAIN-world hook + DOM 标记 + `findInteractiveAncestor`
> 认标记）。诊断见 [`upload-general-identity-e2e-analysis.md`](upload-general-identity-e2e-analysis.md) 的同源思路
> （扩展弱捕获 vs 后端强检测）；探针脚本 `examples/debug_cover_switch_click.py`。
>
> 承接：[`upload-general-identity-impl-plan.md`](upload-general-identity-impl-plan.md)（upload 通用化）、
> [`semantic-clue-replay.md`](semantic-clue-replay.md)（点击定位）。关联回放文件：`rerun-history/bili-2.json`。

---

## 一、Context（为什么做）

`bili-2.json` 第 6 步（竖版封面上传）重放时报"冗余重试（同元素同动作且上步已成功）"被跳过。根因（经
`examples/debug_cover_switch_click.py` 在真站确认）：用户在 step 5（横版上传）和 step 6（竖版上传）之间
**点了一下封面 slot 切换 tab**（`div.cover-editor-panel-canvas.active` → `.inactive`，"首页推荐封面（4:3）"
↔ "个人空间封面（16:9）"），但这条 click 没进录制文件。其祖先链**完全没有 DOM 级交互信号**：

```
<span class="text">首页推荐封面（4:3）'   cursor=default
  ↑ div.cover-editor-panel-canvas-titl   cur=auto role= onc=n mdown=n
  ↑ div.active                           cur=auto role= onc=n mdown=n   ← 实际的 tab
  ↑ div.cover-editor-panel-canvas        cur=auto role= onc=n mdown=n
```

`cursor=auto`（非 pointer）、无 `role`、无 `onclick`/`onmousedown` 属性，点击处理器**只通过 `addEventListener`
注册**。扩展 `findInteractiveAncestor` 的三个启发式（`INTERACTIVE_SELECTOR` / `cursor:pointer` div / `onclick`
属性）**全都不识别 addEventListener** → `onClick` 把切换 tab 的 click 直接 drop（不 emit）→ 录制缺这一步 →
重放 step 5/6 目标同一 input → 被判冗余跳过。

**关键不对称**：Python 后端 `dom.py:is_interactive` 规则 3 `has_js_click_listener`（CDP
`_detect_js_click_listeners`）**能**检测 addEventListener（Vue @click / React onClick / 原生），所以这些
tab **在 selector_map 里**；但 content script **无法**枚举监听器 → `findInteractiveAncestor` 看不到 → 丢
click。漏点正在"扩展弱捕获 vs 后端强检测"的缝里。

**目标**：给扩展补上和后端同等的 addEventListener 检测力——MAIN-world 里 wrap
`EventTarget.prototype.addEventListener`，给注册了 click 类监听器的元素打 DOM 标记，`findInteractiveAncestor`
认该标记。**通用解**（不写死任何站点 class），正好补盲区。

---

## 二、设计

### 2.1 跨 world 标记：必须用 DOM 属性
MAIN↔ISOLATED world 共享 DOM 节点，但**不共享** JS 堆（expando 属性、WeakSet 都跨 world 不可见）。故标记
必须是 DOM 属性：`el.setAttribute('data-tw-jsclick','1')`。ISOLATED world 的 `findInteractiveAncestor`
用 `cur.hasAttribute('data-tw-jsclick')` 可见。

### 2.2 标记哪些事件
`'click'` 为主；为一次到位（避免二次重录），同时标 `'mousedown'`、`'pointerdown'`（同为"点击类 affordance"
事件，常见于自定义按钮抢焦点）。仅在 `this instanceof Element` 时标（window/document 非 Element，跳过）。
removeEventListener 不清标记（轻微过标可接受——这些元素确曾可交互）。

### 2.3 时序：无条件尽早注入
现有 `injected.ts` 由 `navigation-recorder.ts` 在 `install()`（**录制门控**）里经 `<script src>` 异步注入。
补丁必须**先于元素注册监听器**就位。把注入挪到 `content.ts main()` 开头**无条件**执行（document_idle），
这样补丁在录制开始前、动态组件挂载前就装好。封面编辑器是用户操作时才动态挂载（远晚于 document_idle）→
其监听器注册时补丁已在 → 命中。

> **已知局限**：document_idle **之前**注册的监听器（初始 HTML 解析期的内联脚本、或 app bundle 同步首挂）
> 会被漏。对 SPA 动态组件（如 B 站封面编辑器）不影响。若 debug 探针证实某 tab 仍漏（属初始挂载），升级为
> `document_start` MAIN-world 注入（经 `chrome.scripting.executeScript({world:'MAIN',injectImmediately})` 或
> manifest `content_scripts[].world:'MAIN'`）——列为本计划的硬验证关卡。

---

## 三、文件改动（纯扩展 TS，无 Python）

### 1. `recording_extension/entrypoints/injected.ts`（MAIN-world）
在现有 `__twNavHooked` 守卫旁加 `__twAELHooked` 守卫 + patch：

```ts
const a = window as unknown as { __twAELHooked?: boolean };
if (!a.__twAELHooked) {
  a.__twAELHooked = true;
  const orig = EventTarget.prototype.addEventListener;
  EventTarget.prototype.addEventListener = function (this: EventTarget, type: string, listener: any, options?: any) {
    if ((type === 'click' || type === 'mousedown' || type === 'pointerdown') && this instanceof Element) {
      try { this.setAttribute('data-tw-jsclick', '1'); } catch { /* SVG 等无 setAttribute 容错 */ }
    }
    return orig.call(this, type, listener, options);
  };
}
```

### 2. `recording_extension/entrypoints/content.ts`
在 `main()` 开头**无条件**注入 `injected.ts`（与录制状态无关），封装一个小 `injectMainWorld()`（复用现
navigation-recorder 的 `<script src>` + `onload→remove` 模式）。

### 3. `recording_extension/capture/navigation-recorder.ts`
移除 `installNavigationRecorder` 内的 `<script src>` 注入（已挪到 content.ts 无条件注入）；只保留
`tw:nav`/`popstate`/`hashchange` 监听（仍是录制门控）。`injected.ts` 的 history hook 现由 content.ts
无条件注入确保就位。

### 4. `recording_extension/capture/action-recorder.ts`
`findInteractiveAncestor`（L55-74）的 try 块里加一道（与现有三启发式并列）：

```ts
if (cur.hasAttribute('data-tw-jsclick')) return cur; // MAIN-world addEventListener hook 标记（补盲区）
```

（注释说明：补 content script 看不到 addEventListener 的盲区，对齐后端 `has_js_click_listener`。）

### 不改动
- Python 全不动 → pytest 不受影响（降低风险）。
- 后端 `is_interactive`（已能检测）不动。
- `INTERACTIVE_SELECTOR` / `cursor:pointer` / `onclick` 三启发式保留（仍是优先信号）。

---

## 四、验证

1. **关键关卡：重跑 debug 探针**。`uv run python examples/debug_cover_switch_click.py 5`（静态部分即可），
   看 "首页推荐封面（4:3）"/"个人空间封面（16:9）" 两个 tab 从 `✗ 丢` 变 `✓ 录`。
   - ✓ 录 → 补丁命中，继续下一步。
   - 仍 ✗ 丢 → 说明 tab 监听器在 document_idle 前注册（初始挂载）→ 按上面"升级"路径改 document_start
     MAIN-world 注入。
2. `cd recording_extension && npm run build` 过（tsc 无错）。
3. **e2e（用户重录）**：重录 `rerun-history/bili-3.json`（新扩展），确认 step 5（heng）与 step 6（shu）
   **之间多出一条 click**（切 tab）；重放 `replay_full_timing` step 6 不再被"冗余重试"跳过、封面切到 16:9 后
   上传 shu。
4. 全量 pytest 仍 2065 绿（确认无 Python 回归——本应不变）。

---

## 五、风险与回退

- **perf/兼容**：wrap `addEventListener` 对每次调用多一次 `instanceof` +（仅 click 类 + Element 时）
  `setAttribute`。addEventListener 调用频繁，setAttribute 较廉；Element 过滤排除 window/document。对重页面
  的影响待 e2e 观察；若明显，缩窄到只标 `'click'`。wrap 透传原返回值，不改语义，页面无感（仅多一个 `data-*`
  属性，几乎无页面会枚举未知名属性）。
- **框架事件委托**：React 16-18 把 click 监听器注册在 root container（非元素本身）→ tab 元素不被标。
  B 站是 Vue（监听器在元素上）→ 不受影响。委托场景记为已知局限（后端 `is_interactive` 仍能兜底 selector_map）。
- **重复注入**：content.ts 无条件注入 + 残留 navigation-recorder 注入会双注入——靠 `__twAELHooked`/
  `__twNavHooked` 守卫幂等。本计划移除 navigation-recorder 注入以消歧。
- **回退**：纯扩展改动；若 e2e 出问题，revert 4 个 TS 文件即可，Python/录制历史不受影响。

---

## 六、相关

- [`upload-general-identity-e2e-analysis.md`](upload-general-identity-e2e-analysis.md)——同源"扩展弱捕获 vs 后端强检测"思路（upload 侧）。
- [`upload-general-identity-impl-plan.md`](upload-general-identity-impl-plan.md)——upload 通用化（同分支 `feat/upload-general-identity`）。
- [`semantic-clue-replay.md`](semantic-clue-replay.md)——点击定位语义线索。
- 诊断探针：`examples/debug_cover_switch_click.py`（复刻 `findInteractiveAncestor` 判 onClick 捕获/丢弃）。
- 回放文件：`rerun-history/bili-2.json`（step 6 被跳过）。
