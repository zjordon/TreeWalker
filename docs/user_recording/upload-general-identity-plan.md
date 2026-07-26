# upload file input 通用重定位方案（站点无关）

> 状态：**设计文档（通用化方向）**。issue #139 的修复（`_match_file_upload_by_clue` + `area_text`）**能跑通，
> 但硬编码了抖音 / Semi-UI 的站点特征**：`.semi-upload`、`[class*="semi-upload-drag-area"]`、
> `[class*="step-active"]`、`[class*="modal"]`、文案"点击上传文件或拖拽文件到这里"。TreeWalker 是通用浏览器
> agent，功能不能针对单一站点。本文给出**站点无关的通用方案**（Layer 1+2，可落地），并如实记录**做不到完全
> 通用的技术天花板**（Layer 3）。
>
> 承接：[`upload-semantic-clue-plan.md`](upload-semantic-clue-plan.md)（方向 A 方案）、
> [`upload-semantic-clue-retrospective.md`](upload-semantic-clue-retrospective.md)（实施复盘 + 四个坑）、
> [`semantic-clue-replay.md`](semantic-clue-replay.md)。关联 issue：#139。

---

## 一、背景与动机

录制时用户在某个 `<input type=file>` 上选了文件；重放时要在一个**可能含多个同 `accept` 的 file input** 的页面里
找回"同一个语义"的 input。issue #139 的根因是：upload 步骤无指纹（`interacted_element=[None]`），重放只能走
`_resolve_file_input_by_accept`，其 xpath 精确消歧脆弱（modal `div[N]` 漂移）、兜底 `candidates[0]`（DOM 顺序
第一个）受 get_state 时序影响。

已落地的修复（`_match_file_upload_by_clue`）用 `area_text`（drag-area 文案）精筛解决了抖音封面 case，**但
`area_text` 的获取写死了 Semi-UI 选择器**：

```python
# src/tree_walker/agent/rerun.py:_upload_widget_contexts（当前，站点特化）
"const w=inp.closest('.semi-upload');"                              # ← Semi-UI
"const d=w&&w.querySelector('[class*=\"semi-upload-drag-area\"]');" # ← Semi-UI
"in_modal:!!inp.closest('[class*=\"modal\"]')"                      # ← 通用性存疑
# src/tree_walker/recorder/.../action-recorder.ts:captureUploadCtx 同款
```

换个站点（Ant Design `ant-upload`、Element `el-upload`、自研 dropzone）这些选择器全失效。本文目标：**用站点无关
的信号替代这些写死选择器**。

---

## 二、问题本质（通用表述）

file input 按前端惯例是**视觉隐藏**的（opacity:0 / 1×1 / display:none），由一个独立的**可见 affordance**
（button / dropzone / `<label>`）驱动。"是哪一个上传"的稳定身份活在**可见 affordance + 语义**里，不在隐藏 input 上。

affordance ↔ input 的链接有两种形态，决定了通用方案的边界：

| 形态 | 例子 | 静态 DOM 能否抓到 |
|---|---|---|
| **DOM 静态**（标准关联） | `<label for=input.id>`、`aria-labelledby`、祖先/后代包裹 | ✅ 能 |
| **JS 闭包**（框架常见） | `<button onclick={() => inputRef.current.click()}>` | ❌ 无 DOM 级链接，只有框架运行时状态知道 |

**静态 DOM 检视只能抓前者；后者必须运行时观察**（拦截 `input.click()` 或 `fileChooser` 事件）。这是整套通用方案
的核心约束，也是 Layer 3 天花板的根。

---

## 三、通用信号清单（按通用性排序，全部站点无关）

| # | 信号 | 通用性 | 现状 |
|---|---|---|---|
| 1 | input 自身 `accept`/`name`/`id`/`multiple` | 通用，但**会撞**（多 input 同 accept/name） | `accept` 已用（`_file_input_candidates`）；`name`/`id` 部分用 |
| 2 | **`<label for>` → `input.labels`**（W3C 标准关联） | **最通用的静态信号** | ❌ **生产代码未实现**（仅 `examples/debug_upload_*.py`） |
| 3 | **`aria-labelledby`/`aria-describedby` 解析**（IDREF→目标节点文本） | 通用（无障碍标准） | ❌ IDREF 已收进 `STATIC_ATTRIBUTES`（`views.py:82-131`）但**从不解析到目标** |
| 4 | 表单上下文（外层 `<form>`/`<fieldset>` + legend） | 通用但粗 | ❌ 未用 |
| 5 | **触发 picker 的可见点击（affordance）**——用户点的那个可见元素的身份（text/role/rect） | 通用，可再识别 | ❌ click 与 change **无关联**（`onClick` 跳过 input 点击，`onFileChange` 不记来源 click） |
| 6 | 可见 affordance 的 `rect`（非 1×1 input） | 通用（几何），但跨会话会漂 | 部分（`buildElementRef.rect`） |
| 7 | 就近可见标题/标签文本（邻近启发式） | 通用意图但模糊 | ❌ 无通用 helper（`_find_upload_label_near` 写死 `上传`/`选择文件`） |
| 8 | 运行时 `HTMLInputElement.prototype.click` hook | **完全通用**（覆盖 JS 闭包） | ❌ 仅 `examples/debug_upload_btn.py:121-133` |
| 9 | `Page.fileChooserOpened`（CDP 拦截） | 通用 CDP 原语，**但对 JS 触发的上传失效**（见 §5） | `discover_file_input_via_click`（`session.py:1349-1399`）已实现，录制期被关掉 |

> **3 个最该补的通用原语（生产缺失）**：`<label for>` 解析、`aria-labelledby` 解析、捕获触发 picker 的可见点击。
> 它们是标准 / 行业正解，且代码里已有原始材料（IDREF attr 已收、click 已录、`parent_node` 可遍历），只差"关联与解析"。

---

## 四、分层通用方案

### Layer 1：通用静态身份束（泛化当前 `area_text` 写死）

录制端用**标准信号**替代 `.semi-upload`/drag-area：

```ts
// captureUploadCtx 改造方向（站点无关）——读 input 的标准 affordance 关联，不读框架 class
function captureUploadCtx(input: HTMLInputElement) {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  // 1. 原生 label（W3C）：input.labels 或 label[for=input.id]
  const labelText = norm(Array.from(input.labels ?? [])
    .map(l => l.textContent).join(' '));
  // 2. aria-labelledby / aria-describedby → 目标元素文本（DOM.querySelector('[id=…]')）
  const ariaText = norm(resolveAriaRefs(input));   // 解析 IDREF 到目标节点 textContent
  // 3. 外层 form/fieldset legend
  const formLabel = norm(input.form?.querySelector('legend')?.textContent
    ?? input.closest('fieldset')?.querySelector('legend')?.textContent);
  // 4. 就近可见标题（通用邻近启发式：向上 N 层找最近有可见文本的 heading/label，封顶深度，不写 class）
  const nearbyHeading = norm(findNearestVisibleHeading(input, { maxUp: 4 }));
  return { label_text: labelText, aria_text: ariaText, form_label: formLabel,
           nearby_heading: nearbyHeading };
}
```

重放端把 `_match_file_upload_by_clue` 的 `area_text` 精筛换成**上述通用文本字段的加权匹配**（任一相等即命中，
多命中再 rect 就近）。`accept` 粗筛 / 可见性 / rect 就近链路**保留不动**（它们本就通用）。

> **label/aria 文本解析别在 JS 里重算 accname**——浏览器 accname 算法太特化（`fingerprint-realtime.md:177`）；
> 直接 `input.labels` / `document.getElementById(id).textContent` 取原始文本即可。

- **覆盖**：用原生 `<label for>` / 有 aria 标签 / 有就近可见标题的上传区（绝大多数表单、Ant/Element/原生上传）。
- **不覆盖**：JS 闭包触发且无任何可见标签的 dropzone（→ Layer 2/3）。

### Layer 2：捕获触发 picker 的可见点击 affordance

录制端把"用户点开 picker 的那个可见元素"的身份存进线索（**`drag-area 文案` 的通用化**）：

- `action-recorder.ts`：`onClick` 不再无条件跳过——保留"最近一次可见 click（非 input 点击）+ ts"；`onFileChange`
  发 `upload_file` 时，若距上次可见 click ≤ N ms（如 2s），把它作为 `trigger_affordance`（text/role/rect/label）
  附进 `upload_ctx`。
- 重放端：在 accept 粗筛后的候选里，优先 `trigger_affordance` 文本/role 匹配的那个。

```python
# _match_file_upload_by_clue 增加一道（在 area_text 之后、可见性之前）：
if clue.get("trigger_affordance_text"):
    trig = [idx for idx in cand_ids
            if affordance_text_of(selector_map[idx]) == clue["trigger_affordance_text"]]
    if len(trig) == 1: return trig[0]
    if trig: candidates = [(idx, selector_map[idx]) for idx in trig]
```

- **覆盖**：所有"点可见按钮/dropzone → 弹 picker"模式（含 JS 闭包触发的——因为用户**点的那个可见元素**有可再识别
  的文本/role，重放时按它找 affordance，再落到其关联 input）。**这是 Playwright codegen 的正解**（见 §6）。
- **代价**：click→change 的时间窗关联是启发式（可能误关联到无关 click）；需结合"click 目标是 input 的祖先/兄弟/
  label 关联"做校验降噪。

### Layer 3：运行时拦截（完全通用，重，记为长期/可选）

针对 Layer 1+2 都不覆盖的硬场景（JS 闭包 affordance + 无可见标签 + click 误关联）：

- **`HTMLInputElement.prototype.click` shim**（`examples/debug_upload_btn.py:121-133` 已有原型）：录制期注入 hook，
  页面调用 `input.click()` 时记录"哪个 input + 调用方元素/call stack"。**不需 user-gesture**（JS 级 hook，不是 CDP
  fileChooser），故能覆盖 Semi-UI 式 JS 触发。
- 代价：hook 有侵入性（改原型方法，需在 page load 早期注入，SPA 路由切换后重注）；跨 iframe/shadow 要分别注入。
- **别走 `Page.setInterceptFileChooserDialog`**：见 §5 天花板①，它对 JS 触发的上传直接失效。

---

## 五、技术难点（如实写：为什么没有"一行搞定"的通用方案）

### ① fileChooser 拦截对 JS 触发的上传失效（CDP 原语的天花板）

`Page.setInterceptFileChooserDialog` + `Page.fileChooserOpened{backendNodeId}` 本是通用 CDP 原语，
`discover_file_input_via_click`（`session.py:1349-1399`）已实现。但实测（`docs/tools-optimize/upload_file_fix.md:138-146`）：
**Semi-UI 等 drag-area/button 触发器走 JS 调 `input.click()`，丢失 user-gesture → 浏览器不发 `fileChooserOpened`**。
故该原语**只对原生 `<label for>` 点击有效**，覆盖不了 JS 触发的上传。叠加：录制期必须**关掉**拦截
（`_disable_file_chooser_intercept` `recorder.py:390-405`），否则用户看不到原生 picker → 无 change → 录不到上传。
所以"开 fileChooser 拦截来记录 input 身份"这条路在录制侧是堵死的。

### ② JS 闭包 affordance 无 DOM 链接 = 静态方案的天花板

`<button onclick={() => inputRef.click()}>` 在 DOM 上与 input **毫无关联**（关联在框架运行时状态/闭包里）。
静态 DOM 检视（Layer 1 的 label/aria/就近文本）**结构上抓不到**这条链接。这是"完全静态、完全通用"方案不存在的
根因。能救的只有运行时（Layer 3 的 click hook）。

### ③ `backendNodeId` 跨会话无效

CDP backendNodeId 是单次 page load 的句柄，**不能跨录制/重放会话复用**。所以"记录 backendNodeId、重放直接用"
不成立——必须每次重放重新解析（这正是当前所有麻烦的来源）。

### ④ accname 算法太浏览器特化

完整的 accessible name 计算（label-for、aria-labelledby 多 ID、alt、递归 title…）浏览器实现差异大，**难在 JS/Python
里忠实重实现**（`fingerprint-realtime.md:177` 明确记）。所以 Layer 1 的 label/aria 解析应走**原始 attr / `input.labels`
/ `getElementById` 取文本**，不要自己算 accname——否则跨浏览器不稳。

### ⑤ 多 input 共享 label/rect/accept 的退化地板

即使 Layer 1+2 都到位，仍可能遇到：多个 input 共享同一 label 文案、同一就近标题、相近 rect（如同一 widget 内
`hidden-input` + `hidden-input-replace`，或横/竖封面同结构）。这时只能靠 rect 就近 + 可见性兜底，**可能仍选错**。
这是通用方案的固有退化地板，无法 100% 消除——只能叠加更多正交信号降低概率。

---

## 六、与现有方案 / 业界对照

- **Playwright codegen**：录 `page.setInputFiles(page.getByLabel("Upload cover"), file)`——**可见 affordance 就是
  locator**，`setInputFiles` 内部从 locator 解析到隐藏 input。即 Playwright 把"哪个上传"的身份放在**可见 affordance**
  （label/role/text），不放在隐藏 input——**印证 Layer 1（label/aria）+ Layer 2（触发点击 affordance）是行业正解**。
- **Selenium IDE**：多 locator（id/name/css/xlink）+ `sendKeys`；录制时存多种 locator，回放逐个试。
- **browser-use**：`set_input_files(index)` 靠 LLM 在 selector_map 里选 index——把"哪个 input"丢给 LLM 语义判断
  （通用但非确定性、依赖 LLM）。
- **当前 TreeWalker**：`_resolve_file_input_by_accept`（accept+xpath，通用但脆弱）+ `_match_file_upload_by_clue`
  （`area_text` 写死，**= Layer 1 的退化、站点特化版本**）。本文 = 把它升级到通用 Layer 1+2。

---

## 七、落地建议（把写死选择器替换为通用信号）

按"性价比 + 通用性"排序：

1. **先做 Layer 1（通用静态身份束）**——去掉 `.semi-upload`/`[class*="semi-upload-drag-area"]`/`[class*="step-active"]`
   写死，改读 `input.labels` + `aria-labelledby`/`aria-describedby` 解析 + `<form>`/`<fieldset legend>` + 就近可见标题。
   - 改动点：`captureUploadCtx`（`action-recorder.ts`）+ `_upload_widget_contexts` 的 JS（`rerun.py`）+
     `_match_file_upload_by_clue` 的精筛字段（`area_text` → 多通用文本字段加权）。
   - 补 3 个 MISSING 原语：`input.labels` 读取、IDREF 解析、通用就近可见标题 helper（封顶深度，不写 class）。
   - **抖音封面 case 回归**：上传封面区有"上传封面"标题/可见文本，Layer 1 的就近标题即可命中，不再依赖 `semi-upload`。
2. **再做 Layer 2（触发点击 affordance）**——补 click→change 时间窗关联（`action-recorder.ts` `onClick`/`onFileChange`），
   把触发元素身份存进 `upload_ctx.trigger_affordance`；重放端加一道 affordance 匹配。
   - 覆盖 JS 闭包触发的上传（用户点的可见按钮有可再识别文本）。
3. **Layer 3（click hook）记为长期/可选**——仅当 Layer 1+2 仍漏的硬场景值得做时；先在 `examples/debug_upload_btn.py`
   原型上评估侵入性与 SPA/iframe 注入成本。
4. **`in_modal`（`[class*="modal"]`）通用化**：改用 ARIA——`closest('[role="dialog"]')` 或祖先 `aria-modal="true"`
   （无障碍标准，比 class 子串稳）。
5. **`FileInputInfo`（`views.py:680-694`）去框架化**：`upload_ancestor` 现按 `'upload' in cls or 'semi-upload' in cls`
   （`dom.py:471-474`，写死）——改为通用（如"祖先含 `<label>` 关联此 input" 或纯数据 `class_name` 交由线索层解读）。

**验证**：Layer 1 改完后，抖音封面 case 仍应选对（用就近标题/label，不靠 `semi-upload`）；另找 1-2 个非抖音上传页
（如 Ant `ant-upload`、原生 `<input type=file><label>`）验证通用性。单测覆盖 label/aria 解析 + 就近标题 helper。

---

## 八、相关

- [`upload-semantic-clue-plan.md`](upload-semantic-clue-plan.md)——方向 A 方案（本文是其通用化升级）。
- [`upload-semantic-clue-retrospective.md`](upload-semantic-clue-retrospective.md)——实施复盘 + 四个坑。
- [`semantic-clue-replay.md`](semantic-clue-replay.md)——click/input/select 语义线索（upload 当时被排除）。
- [`upload-record-replay-research.md`](upload-record-replay-research.md)——Playwright/Selenium 对照。
- `docs/tools-optimize/upload_file_fix.md:138-146`——fileChooser 对 JS 触发失效的实测。
- `examples/debug_upload_{probe,btn}.py`——label-for 解析 + `HTMLInputElement.prototype.click` hook 的现成原型。
- issue #139。
