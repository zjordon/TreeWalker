# upload file input 通用重定位：实施计划（Layer 1 + Layer 2）

> 状态：**已落地 + 抖音 e2e 通过**（2026-07-26，分支 `feat/upload-general-identity`，未提交）。本文是
> [`upload-general-identity-plan.md`](upload-general-identity-plan.md)（通用化**设计**文档）的**落地实施计划**
> ——把写死的抖音/Semi-UI 选择器替换为站点无关信号的具体改法、测试与验证。设计理由、信号清单、技术天花板
> 见设计文档；本文只讲"怎么改"。实施结果与 e2e 命中路径见 [`upload-general-identity-e2e-analysis.md`](upload-general-identity-e2e-analysis.md)。
>
> **范围**：Layer 1（含 `in_modal`→ARIA-dialog 通用化）+ Layer 2（触发点击 affordance）。**不含** agent 端
> `FileInputInfo`/`_find_upload_label_near` 去框架、**不含** Layer 3 运行时 hook（均见设计文档 §四/§五）。
>
> 承接：[`upload-general-identity-plan.md`](upload-general-identity-plan.md)（设计）、
> [`upload-semantic-clue-retrospective.md`](upload-semantic-clue-retrospective.md)（#139 修复复盘 + 四个坑）、
> [`semantic-clue-replay.md`](semantic-clue-replay.md)。关联 issue：#139。

---

## 一、Context（为什么做）

issue #139 的修复（`_match_file_upload_by_clue`）能跑通抖音封面上传，但**写死了 4 个 Semi-UI/抖音选择器**：
`.semi-upload`、`[class*="semi-upload-drag-area"]`、`[class*="step-active"]`、`[class*="modal"]`（集中在
`rerun.py:_upload_widget_contexts` 的内嵌 JS + 扩展 `action-recorder.ts:captureUploadCtx`）。TreeWalker 是
通用浏览器 agent，换站即失效。本计划按设计文档把这些选择器换成**站点无关的标准信号**，并补 Layer 2。

**验证方式**：单测为主（全量 pytest 必绿）；抖音真站 e2e 由用户后续重录 `douyin_redesign17` +
`replay_full_timing`（实施方无法连真站点）。

**安全性核心论点**：现有降级链尾部是 visibility + rect 就近——这正是当前解决抖音横/竖封面的实际手段
（area_text 在那里撞车）。只要保留这条尾巴 + accept 对齐契约（坑③，JS 必须返回 `accept` 让 Python 同款
kind 过滤后 DOM 序下标一一对齐），通用化只**增加**消歧能力，不会让抖音比现在差。老录制（带
`area_text`/`in_modal` 的 fix/139 history）走 legacy 别名仍能匹配。

---

## 二、设计总览：共享「通用信号规约」

录制端（扩展 TS）与重放端（`rerun.py` 内嵌 JS）**各自从活 DOM 独立计算同一组字段**，匹配靠字段相等。
两边用同一份 capture 逻辑（一个是 TS、一个是嵌入式 JS），实现时保持一致。每个 file input 产出：

| 字段 | 来源（标准、站点无关） | 层 |
|---|---|---|
| `accept` | `inp.getAttribute('accept')` | 对齐契约（坑③，必留） |
| `label_text` | `Array.from(inp.labels\|\|[]).map(l=>l.textContent).join(' ')` 归一化 | L1 |
| `aria_text` | `aria-labelledby` 的 IDREF→`getElementById`→textContent 拼接 | L1 |
| `region_text` | 向上 ≤5 层找首个 textContent 非空且 <200 字的祖先（**泛化 area_text**——Semi widget 祖先的 textContent 含 drag-area 文案） | L1 |
| `in_dialog` | `!!inp.closest('[role="dialog"],[aria-modal="true"]')`（**泛化 in_modal**） | L1 |
| `affordance_text` / `affordance_role` | 向上 ≤6 层找首个可点祖先（`button`/`[role=button]`/`a`/`label`/`cursor:pointer`）的 textContent/role | L2 重放侧推断 |
| `trigger_affordance` | `{text,role,tag,rect}`——**录制侧独有**：`change` 前最近一次可见 click 的真实身份（比推断精确） | L2 |

> **别在 JS 里重算 accname**（设计文档 §五④）：浏览器 accname 算法太特化。直接 `input.labels` /
> `getElementById(id).textContent` 取原始文本。

**L2 关联原理（关键）**：原生文件选择器是 OS 级模态——打开 picker 的 click 与 `change` 之间**没有 DOM
click 事件**。且 `onClick`（`action-recorder.ts:183`）已丢弃程序化 `input.click()`（`composedPath[0]` 是
input 的）。所以"最近一次非 input click"可靠地 = 触发上传的可见 affordance，**不需要紧时间窗**（加 60s
陈旧兜底即可）。这比设计文档 §四 Layer 2 原建议的"≤N ms"更稳。

### 重放降级链（`_match_file_upload_by_clue`，保留可观测性）

```
accept 粗筛（_file_input_candidates，不动）
 → 唯一候选? return
 → trigger_affordance.text == affordance_text？（L2，最精确：用户实点元素）命中1→return；多→收窄
 → 文本束任一相等：label_text / aria_text / region_text(或 legacy area_text)（L1）命中1→return；多→收窄
 → in_dialog(或 legacy in_modal) tiebreak 收窄
 → visibility（is_visible 非 False）收窄
 → rect 就近（_nearest_idx，复用）兜底
```
每级独立、有 INFO 日志、失败不崩（retrospective 教训 #7）。每级只能收窄，仅 `len==1` 提前返回。

---

## 三、文件改动（按依赖序）

### 1. `recording_extension/shared/types.ts`（2-space）
扩展 `upload_ctx`（现 L53-60）为通用字段 + L2：
```ts
upload_ctx?: {
  label_text: string;
  aria_text: string;
  region_text: string;
  in_dialog: boolean;
  trigger_affordance?: { text: string; role: string; tag: string; rect: { x: number; y: number; width: number; height: number } };
  // legacy（老 history 兼容，录制端不再 emit，但类型保留可选以便读旧数据）
  area_text?: string; nearby_text?: string; in_modal?: boolean; upload_ancestor_class?: string;
};
```

### 2. `recording_extension/capture/action-recorder.ts`（2-space）
- **重写 `captureUploadCtx(input)`（现 L95-111）**：去掉 `.semi-upload`/`drag-area`/`step-active` 3 个写死
  选择器，改按上表规约计算 `label_text`/`aria_text`/`region_text`/`in_dialog`（用 `input.labels`、
  `getElementById` 解析 IDREF、有界祖先走查）。复用本文件已有的 `norm`（L96）；`getComputedStyle` 查
  `cursor:pointer` 仅在 affordance 走查里用（L1 的 region 不需要）。
- **L2：模块级 `let lastVisibleClick: {text,role,tag,rect,ts}|null = null`**。`onClick`（L177-196）算出
  `target`/`ref` 后 stash（`performance.now()` 记 ts）。`onFileChange`（L257-278）emit 时若
  `lastVisibleClick && now-ts <= 60_000`，附 `trigger_affordance` 进 `upload_ctx` 并清空 `lastVisibleClick`。
  click 事件照常 emit（不改事件流，仅内存 stash）。

### 3. `src/tree_walker/recorder/recorder.py`（**TAB 缩进**）
- **`_store_upload_clue`（现 L317-341）**：`base` 改读通用字段——`label_text`/`aria_text`/`region_text`/
  `in_dialog` 从 `event["upload_ctx"]` 取；`trigger_affordance` 同理透传。保留 `accept`/`xpath`/`tag`/`rect`。
  产出的 clue：`{_semantic_clue, kind:file_upload, xpath, tag, rect, accept, label_text, aria_text,
  region_text, in_dialog, trigger_affordance}`。
- `handle_event` upload 分支（L195-198, L243-244）不动（仍调 `_store_upload_clue`）。

### 4. `src/tree_walker/agent/rerun.py`（**4-space 缩进**）
- **`_upload_widget_contexts`（现 L956-1008）→ 重命名为 `_upload_input_contexts`**（不再 Semi-widget 特化）。
  替换 `code=(...)`（L971-986）内嵌 JS：`querySelectorAll('input[type=file]')` 每个产出
  `{accept, label_text, aria_text, region_text, in_dialog, affordance_text, affordance_role}`（按规约）。
  **保留**：返回 `accept`、Python 侧 `kind` 过滤、DOM 序下标对齐、计数守卫（坑③，L996-1007 不动逻辑）。
  返回类型注解更新为 `dict[int, dict[str, Any]]`。
- **`_match_file_upload_by_clue`（现 L1010-1071）**：按降级链重写匹配段。`want` 从 clue 取（label/aria/region
  + legacy `area_text` 别名 + `trigger_affordance.text`）；候选 `have` 从 ctx 取。逐级收窄 + INFO 日志。
  保留 `_file_input_candidates`（L900）/`_nearest_idx`（L173）/ visibility 段（L1058-1065）不动。
- 调用点更新：`_match_file_upload_by_clue` 内 `self._upload_widget_contexts(...)` → `_upload_input_contexts`。

**不改动**：`dom.py`、`actions.py`、`views.py`、`session.py`（agent 端去框架未选）。

---

## 四、向后兼容

- 老 history（fix/139 录制，clue 带 `area_text`/`in_modal` 无新字段）：matcher 把 `area_text` 当
  `region_text` 别名、`in_modal` 当 `in_dialog` 别名匹配 → 零回归。
- 更老的 `[None]` history：仍走 `_resolve_file_input_by_accept`（`rerun.py:632-664`）未动。
- 新录制（带通用字段）：走新降级链；抖音靠 `region_text`（=旧 area_text 文案）+ `in_dialog` + visibility
  + rect，与今天等价或更强。

---

## 五、测试（单测为主，覆盖 >85%）

### `tests/test_rerun_history.py`（4-space）
- 改现有用例（`_area_text_disambiguates`/`_area_text_miss_degrades`/`_single_and_empty`/
  `_collision_prefers_modal`/`_contexts_aligns_by_dom_order`）到新字段名（`region_text`/`in_dialog`），
  并验证 legacy 别名（`area_text`/`in_modal`）仍匹配。
- 新增：`label_text` 区分、`aria_text` 区分、`trigger_affordance` 区分（mock `execute_js` 返回带
  `affordance_text` 的 payload）、`in_dialog` tiebreak、`_upload_input_contexts` 新字段 kind 过滤对齐。
  （`execute_js` mock 返回固定 `list[dict]`，只测 Python 过滤/对齐/匹配逻辑；JS 正确性靠 e2e。）

### `tests/test_recorder.py`（TAB）
- 改 `test_upload_file_stores_semantic_clue_with_ctx`：断言新通用字段 + `trigger_affordance` 透传。
- 新增：`upload_ctx` 含 `trigger_affordance` 时透传进 clue；缺省时 `trigger_affordance` 为 None/缺省。

### 扩展端
- 查 `recording_extension/package.json` 有无 test 脚本；无则靠 `npm run build`（tsc 类型检查）+ 手动重录验证。
  `captureUploadCtx` 是纯函数，若引入 vitest 可单测 label/aria/region 计算（可选增强）。

---

## 六、验证

1. `uv run python -m pytest tests/ -x -v` 全绿（目标 >85% 覆盖，重点 `rerun.py`/`recorder.py`）。
2. `cd recording_extension && npm run build` 过（tsc 无类型错）。
3. ✅ **抖音 e2e 已通过**（2026-07-26）：重录 `rerun-history/douyin_redesign17.json`（新扩展）→
   `uv run python examples/replay_full_timing.py rerun-history/douyin_redesign17.json`，step 8/10 封面选对
   input。日志 `upload 线索精筛` 实际命中路径：`region_text` 收窄 5→2、`rect` 就近决横/竖（L2 affordance
   因"affordance 是兄弟非祖先"未命中，靠 region_text+rect 兜底）。完整分析见
   [`upload-general-identity-e2e-analysis.md`](upload-general-identity-e2e-analysis.md)。另找 1 个非抖音
   上传页（原生 `<label for>` 或 Ant `ant-upload`）验证通用性——**仍待做**。

---

## 七、风险与回退

- **JS capture 两边不一致**（TS vs 内嵌 JS 算同一字段方式不同）→ 匹配失效。缓解：规约写在本章 + 代码注释，
  review 对照；`region_text` 的祖先走查深度/长度上限两边一致。
- **region_text 撞车**（多 input 共祖先）→ 退化到 in_dialog/visibility/rect（=今天行为，不更差）。
- **L2 误关联**（drag-drop 无 click / 中间有无关 click）→ `trigger_affordance` 缺省或错；matcher 跳过 L2
  走 L1+尾巴，安全降级。
- **e2e 不能自验**：单测覆盖匹配逻辑；真站回归靠用户。若 e2e 暴露问题，按 #139 的诊断驱动 loop
  （INFO 日志已逐级保留）定位。

---

## 八、相关

- [`upload-general-identity-plan.md`](upload-general-identity-plan.md)——通用化**设计**文档（本文是其落地实施计划）。
- [`upload-semantic-clue-retrospective.md`](upload-semantic-clue-retrospective.md)——#139 修复复盘 + 四个坑（本文继承其降级链与可观测性约定）。
- [`upload-semantic-clue-plan.md`](upload-semantic-clue-plan.md)——方向 A 方案（#139 站点特化修复）。
- [`semantic-clue-replay.md`](semantic-clue-replay.md)——click/input/select 语义线索。
- issue #139。
