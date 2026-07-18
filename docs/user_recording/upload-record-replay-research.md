# 文件上传 record-replay 调研：Playwright / Selenium IDE 怎么做

> 本文记录对成熟 record-replay 工具（本地 `D:\dev\git\auto-browser\` 下的 Playwright、Selenium IDE）
> 如何处理「文件上传录制与重放」的源码调研，用于校验我们的实现方向、避免造轮子。
> 所有引用均经亲自核实源码（非转述）。前序：`upload-accept-fix-retrospective.md`。

---

## 我们的问题

抖音上传流程录制-重放反复出错，根因（实测确认，见 `upload-accept-fix-retrospective.md`）：

1. **隐藏 file input**：「上传视频」按钮触发隐藏的 `<input type=file accept=video/*>`（1×1 clipped）。
   按钮 = `videoInput.click()`，打在同容器兄弟节点上的静态 input（CDP hook 实测：`.click()` 命中
   静态 input 2 次，MutationObserver 0 个新建）。
2. **导航竞态**：选完文件后抖音立即 `/content/upload` → `/content/post/video` 跳转，后端 `get_state`
   定位时已在发布页，原 video input 没了 → 录到错误 input。
3. **同型多 input**：横/竖封面都是 `accept=image`，需区分。

核心矛盾：录制时要捕获正确的 file input 身份，但页面会跳、元素会瞬态。

---

## Playwright 怎么做（核实：`packages/injected/src/recorder/recorder.ts:414-427`）

录制器注入页面，监听原生 DOM 事件。**file upload 在 `onInput`（input/change 事件）里识别**：

```typescript
// recorder.ts:414-427
onInput(event: Event) {
  const target = this._recorder.deepEventTarget(event);
  if (target.nodeName === 'INPUT' && (target as HTMLInputElement).type.toLowerCase() === 'file') {
    // When the file input is hidden and triggered by another element (e.g. a button with
    // onclick="input.click()"), the hover model points to the trigger, not the input.
    // Derive the selector from the actual target element in that case.
    const selector = target === this._hoveredElement
      ? this._hoveredModel!.selector
      : this._recorder.injectedScript.generateSelector(target, {...}).selector;  // ← 对 file input 本身生成 selector
    this._recordAction({ name: 'setInputFiles', selector, signals: [], files: [...files].map(f=>f.name) });
    return;
  }
  ...
}
```

**关键点：**
- 注释（415-417）**直接点名我们的场景**——「hidden file input triggered by a button」。
- 录制时对**实际 target（file input 本身）**生成 selector（`generateSelector`），不是对触发按钮。
- 录的是 `{name: setInputFiles, selector, files: [文件名]}`。

**生成的代码（`packages/isomorphic/codegen/javascript.ts:121`）：**
```javascript
await page.locator(selector).setInputFiles(file);  // ← replay 时通过 locator 重新解析
```

**replay 解析（`packages/playwright-core/src/server/frames.ts` + `dom.ts`）：** locator 每次
`setInputFiles` 都**重新查询 selector**，带 `_retryWithProgressIfNotConnected`——元素未找到或从 DOM
分离（detached）就带 backoff 重试，导航前等页面稳定（`performActionPreChecks`）。

### Playwright 的模式
> **change 事件瞬间（页面还没跳）对 file input 生成 resilient selector；replay 时用 locator 重新
> 解析（auto-wait/retry）。不锁定元素引用。**

selector 优先级：testId > role+name > label > placeholder > text > css。基于 DOM 结构而非可见性，
对隐藏元素仍有效。

---

## Selenium IDE 怎么做（核实：`packages/selenium-ide/.../preload/record-handlers.ts:30-52`）

录制器监听 file input 的 `change` 事件，录成 **`type` 命令**（不是专门的 upload 命令）：

```typescript
// record-handlers.ts:30-52
handlers.push(['type', 'change', function (event) {
  const target = event.target as HTMLInputElement;
  ...
  if ('input' == tagName && this.inputTypes.indexOf(type) >= 0) {  // inputTypes 含 'file'
    if (target.value.length > 0) {
      this.record(event, 'type',
        locatorBuilders.buildAll(target),   // ← 多个备选 locator（id/name/css/xpath 链）
        target.value);                        // ← 文件路径作 value（file input 是 C:\fakepath\<名>）
    }
  }
}, true]);
```

**locator 构建（`locator-builders.ts`）：** `buildAll` 返回**主 locator + 备选链**：
```
id > linkText > name > css(data-attr) > css(finder) > xpath:link > xpath:attributes ...
```
每个 command 存 `target`（主）+ `targets`（备选列表）。隐藏 file input：`buildAll` 会向上找可见
祖先，但重放时直接对解析出的元素 `sendKeys`。

**replay（`packages/side-runtime/src/webdriver.ts`）：**
```typescript
async doType(locator, value, ...) {
  const element = await this.waitForElement(locator, ...fallbacks);  // 5s 隐式等待轮询
  await element.clear();
  if (value) await element.sendKeys(value);  // ← 直接 sendKeys 文件路径到 file input
}
```

### Selenium IDE 的模式
> **change 事件瞬间存 file input 的多 locator + 文件路径；replay 时按 locator 解析元素（5s 隐式等待
> 轮询），`sendKeys` 喂文件路径。不点上传按钮。**

---

## 两者的共同模式（关键）

| 维度 | Playwright | Selenium IDE |
|---|---|---|
| 捕获时机 | change/input 瞬间（页面未跳） | change 瞬间（页面未跳） |
| 存的身份 | 生成的 selector（file input 本身） | 多 locator 备选链（file input） |
| replay 解析 | locator 重新查询 + auto-wait/retry | locator 解析 + 5s 隐式等待轮询 |
| **点上传按钮？** | **否**（setInputFiles 直注） | **否**（sendKeys 直注） |

**两点核心结论：**

1. **「change 时存身份，replay 时解析」是标准架构**——两者都不在录制时锁定元素引用，而是存一个
   resilient 定位信息，replay 时重新解析（带等待/重试应对页面变化）。
2. **两者都不 replay 上传触发按钮的 click**——直接对 file input 操作（setInputFiles / sendKeys），
   从根本上避开原生 picker 和导航竞态。

---

## 对比我们的 B 方案

B 方案（已实现，见 `upload-accept-fix-retrospective.md`）：

| | B 方案 | Playwright | Selenium IDE |
|---|---|---|---|
| 捕获时机 | change 瞬间（扩展 `onFileChange`，页面未跳）✓ | ✓ | ✓ |
| 存的身份 | `accept` + `xpath` | 生成 selector | 多 locator |
| replay 解析 | 按 accept+xpath 重解析 ✓ | locator 重解析 ✓ | locator 重解析 ✓ |
| auto-wait | 靠前置 5s wait（无显式重试） | retryWithProgress | 5s 隐式等待 |

**结论：B 方案架构与成熟工具一致**（change 时存身份、replay 时解析）——独立收敛到业界标准模式，
非白费。身份形式不同：我们用 `accept`（file input 强语义信号，且与 agent `_action_upload_file`
按 `file_inputs_meta`/accept 选同源），对 DOM 重构甚至比脆 xpath 更鲁棒。

---

## 增强 1：对齐成熟工具——不 replay 上传触发 click（本文档驱动）

### 发现

成熟工具 replay 时**不点上传按钮**（直注文件到 file input）。而我们：
- `_action_upload_file` 已是直接 `setFileInputFiles`（对齐 ✓）；
- 但**录制还额外录了上传触发按钮的 click**（扩展 `onClick` 跳过了 file input 自己的 click，但没跳过
  触发它的按钮 click）。

replay 时点「上传视频」按钮 → `input.click()` → 弹原生 picker → 可能阻塞/与 `upload_file` 冲突。
且保留它使手工录制比 agent 录制（upload_file 无前置 click）多一步冗余。

### 设计

在 `rules.py` `rule_file_upload` 中，对每个 `upload_file`，吸收其**紧邻前置 click**，**但仅当该 click
无 `MODAL_OPENED` signal**：

- 无 modal 信号 → 原生 picker 触发器（上传按钮）→ **吸收**（replay 直注文件，不需它）；
- 有 `MODAL_OPENED` → 弹窗/编辑器开启器（如「选择封面」打开封面编辑器）→ **保留**（replay 需先开编辑器）。

这精准对齐成熟工具「不录/不 replay 触发 click」，同时不误杀编辑器开启器（这正是旧规则注释担心的
场景——旧规则一律保留前置 click，现在用 modal signal 区分）。

### 边界正确性（手工录制流程推演）

```
视频上传：  [click(上传视频), upload_file]
            → 吸收 click → [upload_file]  ✓（与 agent 录制一致）

封面上传：  [click(选择封面,modal), ..., click(上传封面), upload_file]
            → upload 紧邻前置 = click(上传封面,无modal) 吸收
            → click(选择封面,modal) 保留  ✓
```

### 与旧规则的差异

旧 `rule_file_upload` 注释：「前置 click 一律保留……上传按钮 click 保留无害（file-chooser intercept
吞）」。本增强推翻该判断——直注式 upload_file 不需要前置 click，且丢弃更干净（对齐 Playwright/
Selenium），避免 replay 弹 picker。

---

## 可选增强 2（未实施）：replay auto-wait

Playwright/Selenium replay 解析元素时带 auto-wait/retry（应对元素渲染慢/瞬态）。我们的
`_resolve_file_input_by_accept` 是一次性解析（靠 upload_file 前置的 5s wait 兜底）。若实测偶发
「file input 渲染慢导致解析落空」，可加等待循环。当前未实施，留作观测后决定。

---

## 参考文件路径（便于复看）

**Playwright（`D:\dev\git\auto-browser\playwright`）：**
- 录制 file upload：`packages/injected/src/recorder/recorder.ts:414-427`
- 生成代码：`packages/isomorphic/codegen/javascript.ts:120-121`
- replay 解析 + 重试：`packages/playwright-core/src/server/frames.ts`（`_retryWithProgressIfNotConnected`）

**Selenium IDE（`D:\dev\git\auto-browser\selenium-ide`）：**
- 录制 type 命令：`packages/selenium-ide/src/browser/windows/PlaybackWindow/preload/record-handlers.ts:30-52`
- locator 构建：同目录 `locator-builders.ts`（`buildAll`，多 locator 备选链）
- replay doType + waitForElement：`packages/side-runtime/src/webdriver.ts`（`implicitWait=5000ms`）
