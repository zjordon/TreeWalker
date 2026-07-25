# 封面上传语义线索方案（upload semantic clue）—— issue #139 的根治方向

> 状态：**可行方案（待实施）**。本文把 `recorder-timing-solutions.md`（架构探讨、标"待决策"）里
> 构想过的 **upload 语义线索**落成可实施的具体方案，并基于 issue #139 的日志 / HTML / 代码实证补全。
>
> 承接关系：`semantic-clue-replay.md`（已实施，但**显式排除 upload**，见其 :152-154 forward-pointer）
> → 本文（补上 upload 这块）→ 后续 issue/PR 实施。
>
> 关联：issue #139（封面上传选错 file input）、`cover-upload-fix.md` / `cover-upload-fix-plan-v2.md`
> （早期 upload 探索，D1 标记法失败）、`docs/bug-evidence/douyin-cover-upload-timing/`（两份对照日志）。

---

## 一、背景与问题

`examples/replay_full_timing.py`（开 #123 等待机制全套）重放 `rerun-history/douyin_redesign14.json`，
第 8 步（横封面 `heng.png`）和第 10 步（竖封面 `shu.png`）**选错 file input**：封面图被上传到名为
`upload-btn` 的（主页面/错误）input，页面弹出一个看不见的上传窗口。对照组 `examples/replay.py`
（不开等待机制）重放**同一 history** 第 8/10 步正常。**唯一变量是等待机制开关 → 回归。**

证据（Step 8 `heng.png`）：

| | replay.py（正常） | replay_full_timing.py（异常） |
|---|---|---|
| 选中 input | `[INPUT]` 4925（未命名，正确 cover input） | `[INPUT] 'upload-btn'` 10304（错误） |
| 页面验证 | ✅ `background-image preview 14→15` | ⚠️ `<img> preview 21→22` + 隐藏窗口 |

完整日志见 `docs/bug-evidence/douyin-cover-upload-timing/{replay-no-timing,replay-full-timing}.txt`。
根因详见 issue #139；本文聚焦**修复方向**。

---

## 二、现状诊断：为什么 upload 步骤没有语义线索

### 2.1 upload 被硬排除出语义线索（5 处）

录制期 `upload_file` 的 `interacted_element` 恒为 `[None]`，且**从不走 `_store_semantic_clue`**：

- `src/tree_walker/recorder/recorder.py:195-198`：upload 分支 `locate = None`，跳过整个
  locate-and-store-clue 块（`_store_semantic_clue` 在 `:227` 仅 locate 重试耗尽时调用）。
- `recorder.py:241`：`action.interacted_element = [None] if action_name == "upload_file" else []`。
- `recorder.py:247` / `:259`：两个兜底 gate 都是 `action_name in ("click","input_text","select_dropdown")`，
  upload 不在其中。
- `recorder.py:279-310` `_store_semantic_clue`：docstring 明示 scope = click/input/select。
- `src/tree_walker/agent/rerun.py:614`：语义线索分支 gate `if hist_elem and hist_elem.get("_semantic_clue")`，
  upload 的 `hist_elem is None` → **不可达**；upload 一律走 `:644-657` 的 `_resolve_file_input_by_accept`。
- `rerun.py:1192`：`_collect_target_hists`（步前等元素）显式 `continue` 跳过 upload。

> 这是 `semantic-clue-replay.md` 当年的**有意决策**（:74「upload_file 保持 `[None]`+accept」、
> :121「仍 accept 兜底（本次不动）」、:152-154「后续若要覆盖，可给 upload_file 也存语义线索
> accept+rect+xpath」——本文即响应这个 forward-pointer）。

### 2.2 TS 端 onFileChange 丢弃了本可捕获的上下文

`recording_extension/capture/action-recorder.ts:230-250` `onFileChange`：

```ts
const target = findInteractiveAncestor(raw) ?? raw;   // input ∈ INTERACTIVE_SELECTOR → 立即返回，不向上走
const ref = buildElementRef(target);                  // 算出完整 ref {xpath, rect, text, classes, ...}
const accept = raw.getAttribute('accept') ?? '';
emit({ type:'upload_file', xpath: ref.xpath, ...refAttrs(target), params:{ path, accept } });
//                       ↑ 只用 xpath            ↑ 只 tag/id/name/ariaLabel/role
//   ref.rect / ref.text / ref.classes 全部丢弃；enclosing .semi-upload 从不读取
```

`findInteractiveAncestor`（`:55-74`）对 `<input>` **立即返回**（input 在 `INTERACTIVE_SELECTOR` 里），
**不向上遍历**，所以封装的 `semi-upload` widget 从未被访问。扩展在 change 瞬间**有完整 DOM 访问权**
（`raw.closest(...)`、widget 文案、周边 heading 都能读到），只是没读。

### 2.3 录制结果：只有 accept + xpath，且 accept 无法区分

`rerun-history/douyin_redesign14.json` step 8（`:360-390`）/ step 10（`:444-466`）：

```json
{"name":"upload_file","params":{"path":"...heng.png",
  "accept":"image/png,image/jpeg,image/jpg,image/bmp,image/webp,image/tif",
  "xpath":"/html/body/div[12]/.../div[2]/input[1]"}}, "interacted_element":[null]
```

- 两步 `accept` **完全相同** → accept 无法区分。
- 唯一区分项是 `xpath` 的 `div[2]/input[1]`（heng）vs `div[3]/input[1]`（shu）；但 `div[12]` 是弹窗
  作为 `<body>` 第 12 子节点的序号，**随 toast/popover 异步挂载而漂**；`normalize_xpath`
  （`locator.py:22-29`）只 strip 前导 `/`，**整串精确比** → 一漂就 0 命中。
- 失配后 `_resolve_file_input_by_accept`（`rerun.py:893-931`）落到 `candidates[0]`（DOM 顺序第一个
  image input），而这个"第一个"受 get_state 时序影响——#123 等待机制把快照推到另一时刻 → 选错。

### 2.4 locate_by_ref 四道防线对 file input 全失效

`src/tree_walker/recorder/locator.py:122-171`（TEXT→XPATH→ATTRIBUTE→RECT）对隐藏 file input：

| 防线 | 失效原因 |
|---|---|
| TEXT（:141-152） | file input 无子文本 |
| XPATH（:154→:79-119） | `div[N]` 不稳 + cover 主/替换 input 同 `…/div/input` 本地路径 |
| ATTRIBUTE（:158-169） | 只读 `name`/`id`/`ariaLabel`；cover input 只有共享 class，无 name/id/aria |
| RECT（:171→:221-268） | 隐藏 1×1，rect 塌缩到容器；且为 click 目标设计，非 upload |

**结论**：现有四防线**无法**挑出正确的 cover input——因为唯一稳定区分信号（drag-area 文案）是 input 的
**兄弟元素**，而 `locate_by_ref` 只读节点自身属性。需要**新增一道 upload 专用精筛**。

---

## 三、可用的稳定区分信号（HTML 实证）

基于本地抓取的封面弹窗快照 `rerun-history/conver-dialog.html`（未入库，关键结构如下）：

| 信号 | 取值（实证） | 区分力 | 位置 |
|---|---|---|---|
| **`area_text`**（widget drag-area 文案） | 上传封面区 = "点击上传文件或拖拽文件到这里"；AI封面区 = **无文案**（仅 icon） | 强（区分上传封面 vs AI封面 vs 主页 input） | `:1378` vs `:183-192` |
| **`nearby_text`**（活动 step tab） | "设置横封面" / "设置竖封面" | 强（区分横/竖方向） | `:12`（active）/ `:13` |
| **`container_label`**（区标题） | "上传封面" / "AI封面" | 中（补充 area_text） | `:1361` / `:32` |
| **`upload_ancestor_class`** | `semi-upload`（稳）/ `upload-BvM5FF`（hash，不稳） | 弱（粗筛辅助） | `:1363` |
| input 自身 `rect` | 隐藏 1×1，塌缩到容器 | 兜底就近 | — |

**关键**：`area_text` 是 **drag-area 的文案**，属于 input 的**兄弟元素**——Semi-UI 在 change 后
**重建的是 `<input>` 本身**（这正是 `cover-upload-fix-plan-v2.md` D1「`data-tw-recmark` 标记法」
失败的原因：标记随旧 input 销毁），**但 drag-area 文案作为兄弟节点不被重建**，录制瞬间与重放瞬间都在、
且不变。这是 `area_text` 相对 D1 的根本优势（`cover-upload-fix.md:90-96` 已识别此信号为"唯一稳定
区分信号"，但当时未实现）。

> heng/shu 方向：录制 xpath 的 `div[2]` vs `div[3]` 提示二者可能命中不同 input；也可能共用同一 input、
> 方向由 #136 的 step-tab click 决定（`cover-upload-fix.md` 记录的另一种可能）。两种情况下
> `nearby_text`（活动 step tab）都能提供方向信息，不依赖 input 是否共用。

---

## 四、方案设计（方向 A：录制端多存线索，改 TS 扩展）

对齐 `recorder-timing-solutions.md:60-74,107-125` 已有构想，分两层。

### 4.1 录制端：捕获并存储 upload 语义线索

**TS（`recording_extension/capture/action-recorder.ts` + `selector.ts` + `shared/types.ts`）**

`onFileChange` 在现有 `ref = buildElementRef(target)` 基础上，多读封装 widget 的上下文：

```ts
// action-recorder.ts onFileChange 内，emit 前
const widget = raw.closest('[class*="semi-upload"]') as HTMLElement | null;
const dragArea = widget?.querySelector('[class*="semi-upload-drag-area"]') as HTMLElement | null;
const areaText = (dragArea?.innerText ?? '').trim();                 // "点击上传文件或拖拽文件到这里" / ""
// nearby：活动 step tab（封面弹窗）或最近可见 heading
const stepTab = document.querySelector('[class*="step-active"]')?.textContent?.trim() ?? "";
emit({
  type: 'upload_file',
  xpath: ref.xpath,
  rect: ref.rect,                      // 现被丢弃 → 现在带上
  ...refAttrs(target),
  params: { path: file.name, accept },
  upload_ctx: { area_text: areaText, nearby_text: stepTab,
                upload_ancestor_class: widget?.className ?? "" },   // 新增结构化字段
});
```

- `ElementRef`（`shared/types.ts:4-16`）/ `RecorderEvent`（`:18-52`）加 `rect` 与可选 `upload_ctx`。
- `buildElementRef`（`selector.ts:10-30`）已算 `rect`，只是 upload 路径没用——现在透传。
- 不破坏现有 click/input 路径（它们不带 `upload_ctx`）。

**Python（`src/tree_walker/recorder/recorder.py` + `models.py` + `event_mapper.py`）**

upload 不再存 `[None]`，改存语义线索（与 click/input/select 的 `_semantic_clue` 同形，多 `kind` 与
upload 专有字段）：

```python
# recorder.py：替 recorder.py:195-198 + :241 的 upload 排除
def _store_upload_clue(self, action, event):
	action.interacted_element = [{
		"_semantic_clue": True,
		"kind": "file_upload",
		"accept": event.get("params", {}).get("accept") or "",
		"xpath": event.get("xpath") or "",
		"rect": event.get("rect"),                       # 现从 event 取（TS 新带上）
		"area_text": (event.get("upload_ctx") or {}).get("area_text", ""),
		"nearby_text": (event.get("upload_ctx") or {}).get("nearby_text", ""),
		"upload_ancestor_class": (event.get("upload_ctx") or {}).get("upload_ancestor_class", ""),
	}]
```

- 放宽 `:195-198`（upload 仍 `locate=None`，但改为调用 `_store_upload_clue` 而非 `[None]`）。
- `_skip_reason`（`rerun.py:538-568`）已对 upload 放行（`:562` 只对 click/input/select 判无 index 跳过），
  且 `interacted[0]` 非 None → 不会被当噪声步跳过。
- `flatten.py:33-54` 透传 `interacted_element`，无需改。

### 4.2 重放端：用线索精筛，替代 candidates[0]

**新增 `_match_file_upload_by_clue`**（放 `rerun.py`，与 `_resolve_file_input_by_accept` 同模块；或并入
`locator.py` 作为 upload 专用定位）。accept 粗筛 → area_text 精筛 → nearby_text 精筛 → rect 就近：

```python
def _match_file_upload_by_clue(self, clue: dict, selector_map: dict) -> int | None:
	# 1. accept 粗筛（复用 _resolve_file_input_by_accept 的候选收集逻辑）
	candidates = [idx for idx, n in selector_map.items()
	              if _is_file_input(n) and self._accept_kind(clue["accept"]) in _accept_of(n)]
	if not candidates:
		return None
	if len(candidates) == 1:
		return candidates[0]
	# 2. area_text 精筛：读每个候选所在 semi-upload 的 drag-area 文案
	want_area = (clue.get("area_text") or "").strip()
	if want_area:
		hits = [idx for idx in candidates if self._area_text_of(selector_map[idx]) == want_area]
		if len(hits) == 1:
			return hits[0]
		if hits:
			candidates = hits
	# 3. nearby_text 精筛（活动 step tab "设置横/竖封面"）
	want_near = (clue.get("nearby_text") or "").strip()
	if want_near:
		hits = [idx for idx in candidates if want_near in self._nearby_text_of(selector_map[idx])]
		if len(hits) == 1:
			return hits[0]
		if hits:
			candidates = hits
	# 4. rect 就近兜底（复用 _nearest_idx 的 bounds 中心就近）
	return self._nearest_idx_by_rect(clue.get("rect"), [selector_map[i] for i in candidates])
```

- `_area_text_of(node)` / `_nearby_text_of(node)`：从候选 input 向上找 `semi-upload` widget 再读
  drag-area 文案 / 周边 heading。复用 `_nearest_idx`（`rerun.py:173-205`）的 bounds 就近思路。
- `rerun.py:614` 的语义线索分支**已存在**且对 upload 也适用（只要 `interacted[0]` 带 `_semantic_clue`）；
  但其 `locate_by_ref` 不读 upload 字段 → 在该分支内对 `kind=="file_upload"` 分发到
  `_match_file_upload_by_clue`（对齐 `recorder-timing-solutions.md:99-105` 的 `_match_by_semantic_clue`
  dispatch 构想）。
- **替代** `_resolve_file_input_by_accept` 的 `candidates[0]` 兜底（`:931`）——老 history 无线索时仍走它。

### 4.3 降级链（三层，互不干扰，零回归）

| 录制结果 | `interacted_element[0]` | 重放路径 |
|---|---|---|
| upload 新录制（带线索） | `{_semantic_clue, kind:file_upload, area_text, ...}` | **新增** `_match_file_upload_by_clue`（accept→area_text→nearby→rect） |
| upload 老历史（无线索） | `None` | 现有 `_resolve_file_input_by_accept`（保留，零回归） |
| 稳定元素 | `{element_hash,...}` | 现有指纹匹配（最优，不动） |

---

## 五、为何可行（基于既有架构，最小新造）

- **TS 端零新能力**：`buildElementRef` 已算 `rect`；`closest`/`querySelector` 是标准 DOM API；`onFileChange`
  已在 change 瞬间持有 `raw`——只是多读几行。不改 click/input 路径。
- **Python 端复用既有模式**：`_store_upload_clue` 仿 `_store_semantic_clue`；线索字段名与
  `recorder-timing-solutions.md:60-74` 构想一致；`_match_file_upload_by_clue` 复用
  `_resolve_file_input_by_accept` 的候选收集 + `_nearest_idx` 的 bounds 就近。
- **`area_text` 不随重建消失**（兄弟元素）——这是 D1 标记法失败、而本方案可行的根本对照
  （`cover-upload-fix-plan-v2.md:176-200`）。
- **重放有时序优势**：重放到 upload 步时，重放还没点 → 封面弹窗稳定、drag-area 文案在场、6 个 input
  都在 selector_map——精筛在真实 DOM 上做，不受录制端被动观察的时序劣势影响
  （`semantic-clue-replay.md:30-46` 的 reframing）。

---

## 六、实施顺序

1. **Phase 1（录制端捕获）**：TS `onFileChange` 带 `rect`+`upload_ctx`；Python `_store_upload_clue`；
   `ElementRef`/`RecorderEvent`/`event_mapper` 透传新字段。`npm run build` 扩展。
2. **Phase 2（重放端精筛）**：`_match_file_upload_by_clue` + `rerun.py:614` 分支内 `kind` 分发；
   `_area_text_of`/`_nearby_text_of` 辅助。
3. **兼容与回归**：老 history 无线索 → 仍走 `_resolve_file_input_by_accept`（零回归）；重录 douyin 封面
   → 用 `replay_full_timing.py` 回归 step 8/10。

> **备选 B（已否决）**：不改扩展，纯重放端在 `_resolve_file_input_by_accept` 内按"候选 drag-area
> 文案/可见性/在 modal 内"启发式精筛替代 `candidates[0]`。能立即修老 history、零扩展改动，但**带硬编码
> 封面文案、不通用**，且重放端要为每个候选做 DOM 上下文查询（`locate_by_ref` 现只读节点自身属性）。
> 选 A：根治、可推广、对齐已有设计与"多存线索"方向。B 可作为 A 上线前的临时止血，但非本方案主线。

---

## 七、风险与边界

- **`area_text` 巧合**：多个 widget 的 drag-area 文案可能相同（如多个"点击上传文件或拖拽文件到这里"）。
  缓解：叠加 `nearby_text`（活动 step tab）+ `container_label`（"上传封面" vs "AI封面"）+ rect 就近；
  HTML 实证下上传封面区有文案、AI封面区无文案，已天然区分。
- **i18N / 文案改版**：drag-area 文案若改版则 `area_text` 失配 → 降级到 nearby/rect/老 accept 兜底
  （不崩，退回现状）。可接受（比 xpath 整串稳得多）。
- **heng/shu 方向**：若二者共用同一 input，方向由 #136 step-tab click 决定，upload 步无需区分；
  若是不同 input，`nearby_text`（活动 tab）区分。两种皆覆盖。
- **rect 隐藏 1×1**：仅作末级兜底，不单独依赖。
- **老历史兼容**：`[None]`→ 老路径，零回归（只有新录制受益）。
- **覆盖范围**：本方案只解 upload；其他"动作改变元素自身"场景（modal 内 click、动态列表）仍由
  现有 click/input/select 语义线索 + 未来扩展覆盖。

---

## 八、改动清单

- **TS**：`recording_extension/capture/action-recorder.ts`（`onFileChange` 捕获 `rect`+`upload_ctx`）、
  `capture/selector.ts`（透传 `rect`）、`shared/types.ts`（`ElementRef`/`RecorderEvent` 加字段）。
- **Python 录制**：`src/tree_walker/recorder/recorder.py`（`_store_upload_clue`，放宽 `:195-198/:241`）、
  `recorder/models.py`（`element_ref_from_event` / `to_ref_dict` 透传）、`recorder/event_mapper.py`
  （`upload_file` params 不变，新字段走顶层 event）。
- **Python 重放**：`src/tree_walker/agent/rerun.py`（`_match_file_upload_by_clue` + `:614` 分支 `kind`
  分发 + `_area_text_of`/`_nearby_text_of`）；可选 `recorder/locator.py` 并入。
- **测试**：`tests/test_recorder.py`（upload 存 `_semantic_clue{kind:file_upload,area_text,...}`）、
  `tests/test_rerun_history.py`（`_match_file_upload_by_clue` 多候选精筛命中/失配降级）。

---

## 九、验证

1. `uv run python -m pytest tests/ -x -v` 全绿；recorder 包覆盖率 ≥ 85%。
2. 重录抖音封面（heng+shu）→ step 8/10 的 `interacted_element[0]` 非 None、含
   `_semantic_clue`/`kind:file_upload`/`area_text`（"点击上传文件或拖拽文件到这里"）/`nearby_text`
   （"设置横/竖封面"）。
3. `uv run python examples/replay_full_timing.py douyin_redesign14.json`（重录后的新 history）→
   step 8/10 日志走 `_match_file_upload_by_clue`（area_text 精筛命中正确 cover input），不再命中
   `upload-btn`、不再弹隐藏窗口。
4. 回归：老 `douyin_redesign14.json`（`[None]`）仍走 `_resolve_file_input_by_accept`，行为不变；
   稳定元素仍走指纹路径（`匹配级别 EXACT`）。

---

## 十、与其它文档的关系

- 上承 `recorder-timing-solutions.md`（架构探讨，提出 `_match_file_upload_by_clue` + `area_text`/`nearby_text`
  构想但未实现）——本文是其**实施落地版**。
- 补 `semantic-clue-replay.md`（已实施 click/input/select，:152-154 forward-pointer 指向 upload）的缺口。
- 取代 `cover-upload-fix.md` A 方案 / `cover-upload-fix-plan-v2.md` §8 中"提议但未建"的 `area_text`
  （以及推翻 `:282` 当时"不需要 area_text"的判断——issue #139 证明 accept+xpath 兜底在开等待机制时不够）。
- 根因与现象见 issue #139 及其评论（基于 `conver-dialog.html` 的实证分析）。
