# 封面上传语义线索：实施复盘（issue #139 修复全程）

> 本文复盘 issue #139（开 #123 等待机制后，抖音封面上传选错 file input）的修复过程：总体思路、
> 最终代码改动、以及**踩过的四个坑**（每个含「现象 → 误判 → 真实根因 → 修复」）。供日后排查类似
> 「录制端捕获 + 重放端精筛」问题时参考，避免重蹈。
>
> 承接：[`upload-semantic-clue-plan.md`](upload-semantic-clue-plan.md)（方案文档）、
> [`semantic-clue-replay.md`](semantic-clue-replay.md)（click/input/select 语义线索，已实施）、
> [`upload-accept-fix-retrospective.md`](upload-accept-fix-retrospective.md)（B 方案 accept 兜底）。
> 关联 issue：#139、#123。

---

## 一、背景（一句话）

开 #123 重放等待机制（networkidle / page-settle / actionability）后，重放 `douyin_redesign1x.json`
的封面上传步（heng.png / shu.png）会把图片传到错误的 `upload-btn` input、弹出看不见的上传窗口；
不开等待机制的 `replay.py` 正常。根因详见 issue #139：**upload 步骤被显式排除在语义线索之外**
（`interacted_element=[None]`），重放只能走 `_resolve_file_input_by_accept`，其 xpath 精确消歧脆弱
（`/body/div[12]` 随页面异步挂载漂移）、兜底 `candidates[0]`（DOM 顺序第一个）受 get_state 时序影响。

修复方向（方案 A，详见 plan 文档）：**录制端多存语义线索**（封装组件的 drag-area 文案 `area_text`
等兄弟元素特征，不随 Semi-UI 重建 input 消失）+ **重放端 `_match_file_upload_by_clue` 精筛**（accept
粗筛 → area_text 精筛 → …），替 `candidates[0]`。

---

## 二、总体思路：诊断驱动的迭代 loop

整个修复走了多轮，每轮遵循同一个 loop（这是能高效定位的关键）：

```
加诊断日志（INFO 级，打中间状态）→ 用户重放一次 → 读日志里的「真相」→ 精准改一处 → 再验证
```

不靠脑补猜根因。`_match_file_upload_by_clue` 里那几行 `logger.info("upload 线索精筛：...")`（候选、
widget 上下文、area_text 命中、可见候选、最终 pick）是定位四个坑的核心——每跑一次都直接暴露「数据
在哪一层丢的」。**先让失败可见，再修**，比反复盲改高效得多。

---

## 三、最终代码改动

### 3.1 录制端（捕获线索）

**`recording_extension/shared/types.ts`**：`RecorderEvent` 新增 `upload_ctx` 字段：

```ts
upload_ctx?: {
  area_text: string;            // 封装 semi-upload widget 的 drag-area 文案（兄弟元素，跨重建稳定）
  nearby_text: string;          // 活动 step tab 文案（"设置横/竖封面"）
  upload_ancestor_class: string; // 封装 widget 的 class（粗筛辅助）
};
```

**`recording_extension/capture/action-recorder.ts`**：新增 `captureUploadCtx(input)`，`onFileChange`
emit 时带上 `rect`（原本被丢弃）+ `upload_ctx`：

```ts
function captureUploadCtx(input: Element) {
  const norm = (s: string | null): string => (s ?? '').replace(/\s+/g, ' ').trim();
  const widget = input.closest('.semi-upload');          // ← 精确 class token（见坑①）
  const dragArea = widget?.querySelector('[class*="semi-upload-drag-area"]');
  const activeStep = document.querySelector('[class*="step-active"]');
  return {
    area_text: norm(dragArea?.textContent ?? null),
    nearby_text: norm(activeStep?.textContent ?? null),
    upload_ancestor_class: widget?.className ?? '',
  };
}
```

**`src/tree_walker/recorder/recorder.py`**：新增 `_store_upload_clue`，把 upload 的 `interacted_element`
从 `[None]` 改存语义线索（与 click/input/select 的 `_semantic_clue` 同形，多 `kind="file_upload"`）：

```python
action.interacted_element = [{
    "_semantic_clue": True, "kind": "file_upload",
    "xpath": ..., "tag": ..., "rect": ..., "accept": ...,
    "area_text": ..., "nearby_text": ..., "upload_ancestor_class": ...,
}]
```

（`handle_event` 的 upload 分支调它；accept+xpath 仍同时落 `params`，老重放路径兼容。）

### 3.2 重放端（用线索精筛）

**`src/tree_walker/agent/rerun.py`**：

- 抽出 `_file_input_candidates(selector_map, *, accept_hint, path)`——`_resolve_file_input_by_accept`
  与新 matcher 共用候选收集（零行为变更）。
- 新增 `_upload_widget_contexts(candidates, kind)`——**单次 `execute_js`** 扫所有 `input[type=file]`，
  返回每个的 `{accept, area_text, nearby_text, in_modal}`，Python 侧按 `kind` 过滤后与候选按 DOM 序
  下标对齐。
- 新增 `_match_file_upload_by_clue`——降级链：**accept 粗筛 → area_text 精筛 → in_modal 撞车兜底 →
  可见性 → rect 就近**，替 `_resolve_file_input_by_accept` 的 `candidates[0]`。
- `_execute_history_step` 语义线索分支按 `kind == "file_upload"` 分发到它（其余仍 `locate_by_ref`）。

精筛链核心：

```python
candidates = self._file_input_candidates(selector_map, accept_hint=clue["accept"], path="")
if len(candidates) == 1: return candidates[0][0]
ctx = await self._upload_widget_contexts(candidates, kind)
hits = [idx for idx in cand_ids if ctx[idx]["area_text"] == want_area]   # area_text 精筛
if len(hits) == 1: return hits[0]
if len(hits) > 1:                                                        # area_text 撞车
    in_modal = [idx for idx in hits if ctx[idx]["in_modal"]]            # → 优先 modal 内
    if 0 < len(in_modal) < len(hits): hits = in_modal
... → 可见性 → rect 就近
```

### 3.3 测试

- `tests/test_recorder.py`：upload 存线索断言（替原 `[None]`）+ 新 `test_upload_file_stores_semantic_clue_with_ctx`。
- `tests/test_rerun_history.py`：`_match_file_upload_by_clue`（area_text 区分 / 失配降级 / 单候选与空 /
  **area_text 撞车优先 modal**）+ `_upload_widget_contexts`（kind 过滤对齐 / 降级）。全量 2059+ 测试绿。

### 3.4 向后兼容

老 history（`interacted_element=[None]`）仍走 `_resolve_file_input_by_accept`，零回归；只有新录制（带
`_semantic_clue`）的 upload 走精筛。

---

## 四、走过的弯路（四个坑）

### 坑①：CSS 子串选择器误伤元素自身 —— 录出的 area_text 恒为空

- **现象**：用新扩展重录（`douyin_redesign15.json`），封面上传步的 `area_text` 是空串。
- **误判**：一度以为是扩展没 reload / 没 rebuild。
- **真相**：`input.closest('[class*="semi-upload"]')` —— file input **自己的 class** 是
  `semi-upload-hidden-input`，含子串 `"semi-upload"`，`closest`（含自身）把 input 当成了 widget →
  input 无子节点 → `dragArea=null` → `area_text=""`。**`upload_ancestor_class` 字段暴露了真相**：值是
  `semi-upload-hidden-input`（input 的 class），而不是 widget 的 `semi-upload upload-BvM5FF`。
- **修复**：`.semi-upload`（**精确 class token**）。widget 有独立 token `semi-upload`，input 没有
 （`semi-upload-hidden-input` 是另一个 token）→ `closest` 跳过 input、正确向上找 widget。
- **教训**：`[class*=x]` 子串匹配极易误伤 `x-foo` / `foo-x`；优先用 `.x` 精确 token。诊断字段
  （`upload_ancestor_class`）意外的「副作用」是定位利器——别省。

### 坑②：`evaluate(elements=...)` 静默失败 —— area_text 全 None，却无报错

- **现象**：录制修好了（area_text 正确落盘），但重放 `_match_file_upload_by_clue` 的日志显示
  `widget 上下文={…: None}`（即 `ctx={}`），area_text 命中 `[]`，落到 rect 兜底又选错。
- **误判**：反复核对我那段 JS 的转义、怀疑 `_validate_and_fix_javascript`（`evaluate` 会先跑它"修 LLM
  引号错误"）改坏了代码——逐条验证它并没动我的单引号 selector / `/\s+/g`。
- **真相**：`evaluate(elements=backend_ids)` 走 **per-element `DOM.resolveNode` + `callFunctionOn`**，
  某个 element 句柄解析/调用抛异常，被我的 `try/except ... logger.debug(...)` 吞掉（debug 级，INFO
  不可见）→ 返回 `{}`。**静默失败**。
- **修复**：换成**单次 `execute_js`**（普通 `Runtime.evaluate`，不解析 element 句柄）：JS 自己
  `querySelectorAll('input[type=file]')` 扫全部，一次返回。`execute_js` 走 `returnByValue` 直接返回
  解析后的 list，还省了 JSON 编解码。
- **教训**：`try/except` + `debug` 日志 = 隐形故障温床；调试期至少 `warning` 并把异常信息打全。
  `evaluate(elements=...)` 的 per-element 句柄路径比想象脆——能用单次 `querySelectorAll` 就别用。

### 坑③：DOM 序计数不匹配 —— 我自己的"防御守卫"误杀了正确数据

- **现象**：换成 `execute_js` 后，日志报 `页面 file input 数 6 ≠ 候选数 5 → 放弃 area_text`，
  又退回 rect 兜底选错。
- **误判**：以为是 DOM 序对应本身不可靠（shadow DOM / iframe 穿透差异）。
- **真相**：JS `querySelectorAll` 返回**全部 6** 个 file input（含 1 个 video），而
  `_file_input_candidates` 已按 accept 过滤成 **5** 个 image 候选 → 下标对不上。**是我加的"计数守卫"
  把本可用的数据丢掉了**——守卫的前提（两边同集合）不成立。
- **修复**：JS 返回每个 input 的 `accept`，**Python 侧用与 `_file_input_candidates` 完全相同的 `kind`
  过滤**后再对齐 → 5 对 5，下标一一对应。
- **教训**：两边的过滤口径必须一致；否则"防御性计数检查"会帮倒忙。统一过滤口径（或干脆把过滤收敛到
  一处）比加守卫更可靠。

### 坑④（预判，非踩坑）：area_text 可能撞车 → 加 in_modal 兜底

- **预判**：主上传区与封面区的 drag-area 文案**可能都是**"点击上传文件或拖拽文件到这里"，area_text
  命中多个 → 仍可能选错。
- **加兜底**：JS 额外返回 `in_modal`（input 是否在 `[class*="modal"]` 即封面编辑弹窗内）；area_text
  撞车时优先选 modal 内的（封面 input 在弹窗内、主上传区 `upload-btn` 在弹窗外）。
- **结果**：实测 area_text（必要时经 in_modal 收敛）正确定位到封面 input，问题解决。

---

## 五、经验教训

1. **诊断驱动，不脑补**：加 INFO 日志打中间状态 → 重放 → 读真相 → 精准改。四个坑全是日志暴露的，
   没一个是靠"想"出来的。`_match_file_upload_by_clue` 的逐级日志保留（降级链每一步都打），日后类似
   问题零成本复现。
2. **静默吞异常是隐形 bug 源**：`except + debug` 让坑②藏了好几轮。调试期 warning 起步，带异常信息。
3. **CSS 选择器：精确 token > 子串匹配**：`[class*=x]` 易误伤；`.x` 精确且语义清晰（坑①）。
4. **两边口径必须一致**：JS 与 Python 各自过滤会产生对不齐（坑③）；把过滤收敛到一处，或确保同款。
5. **`evaluate(elements=...)` 脆**：per-element 句柄解析易抛；能用单次 `querySelectorAll` 扫全量就别用。
6. **诊断字段别省**：`upload_ancestor_class` 本是"粗筛辅助"，却成了定位坑①的关键证据。
7. **降级链要逐级、可观测**：accept → area_text → in_modal → 可见性 → rect，每级独立、有日志、
   失败不崩（最终回退到老行为，零回归）。

---

## 六、改动文件清单

| 文件 | 改动 |
|---|---|
| `recording_extension/shared/types.ts` | `RecorderEvent` 加 `upload_ctx` 字段 |
| `recording_extension/capture/action-recorder.ts` | `captureUploadCtx`（`.semi-upload` 精确 token）+ `onFileChange` 带 `rect`/`upload_ctx` |
| `src/tree_walker/recorder/recorder.py` | `_store_upload_clue`（替 upload 的 `[None]`）+ 接进 `handle_event` |
| `src/tree_walker/agent/rerun.py` | `_file_input_candidates`（抽出共用）+ `_upload_widget_contexts`（单次 execute_js）+ `_match_file_upload_by_clue`（降级链）+ `:614` 按 `kind` 分发 |
| `tests/test_recorder.py` / `tests/test_rerun_history.py` | 新增/更新 upload 线索与精筛测试 |

---

## 七、遗留 / 后续

- **方案文档与代码已对齐**：`upload-semantic-clue-plan.md` 描述的方向 A 已落地并验证。
- **e2e 已验证**：`replay_full_timing.py douyin_redesign16.json` 封面上传步（heng/shu）选对 input。
- **待提交**：改动在分支 `fix/139-cover-upload-wrong-input`，未 commit（按 CLAUDE.md）。
- **泛化**：`_match_file_upload_by_clue` 的降级链（accept→area_text→in_modal→可见性→rect）可推广到其它
  "多个同 accept file input" 场景；`area_text`/`in_modal` 思路也可为 modal 内 click 等提供参考。
- **⚠️ 本方案站点特化，需通用化**：`area_text`/`in_modal` 的获取写死了抖音/Semi-UI 选择器（`.semi-upload`、
  `[class*="semi-upload-drag-area"]`、`[class*="modal"]`、文案"点击上传文件..."），换站即失效。站点无关的通用方案
  见 [`upload-general-identity-plan.md`](upload-general-identity-plan.md)（Layer 1 标准信号 label/aria + Layer 2
  捕获触发点击 affordance + Layer 3 `input.click` hook；并如实记录了 fileChooser 拦截对 JS 触发失效等天花板）。
