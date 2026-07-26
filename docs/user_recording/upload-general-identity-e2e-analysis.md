# upload 通用重定位：redesign17 e2e 分析（nearby_text 删了为何仍匹配）

> 状态：**e2e 验证记录 + 诊断分析**。通用化（Layer 1+2）落地后首次真站重录（`douyin_redesign17.json`，
> 新扩展）+ `replay_full_timing` 回放，封面上传（step 8/10）**选对 input、回放无误**。本文记录对比
> 新旧回放文件发现的现象（step 8/10 少了 `nearby_text` 等字段）并解释为何不影响匹配——用后台日志
> 实锤实际命中路径。日志：`D:\temp\douyin_redesign17.log`。
>
> 承接：[`upload-general-identity-impl-plan.md`](upload-general-identity-impl-plan.md)（实施计划，本文是其 e2e 验证）、
> [`upload-semantic-clue-retrospective.md`](upload-semantic-clue-retrospective.md)（#139 复盘 + 四个坑）、
> [`upload-general-identity-plan.md`](upload-general-identity-plan.md)（设计）。关联 issue：#139。

---

## 一、现象

对比新旧回放文件 step 8/10（两条封面上传 `upload_file`）的语义线索字段：

- 旧 `rerun-history/douyin_redesign16.json`（fix/139 扩展录制）：step 8/10 的 clue 带 `area_text` /
  `nearby_text`（"设置横/竖封面"）/ `upload_ancestor_class`。
- 新 `rerun-history/douyin_redesign17.json`（通用化扩展录制）：step 8/10 的 clue **没有** `nearby_text`、
  `area_text`、`upload_ancestor_class`，改为 `label_text`/`aria_text`/`region_text`/`in_dialog`/`trigger_affordance`。

但 redesign17 回放**仍正确匹配**到封面 input。问：少了 `nearby_text` 为什么还能匹配？

---

## 二、字段对比表（step 8/10 封面上传线索）

| 字段 | redesign16（旧扩展） | redesign17（新扩展） | 去向 |
|---|---|---|---|
| `area_text` | `"点击上传文件或拖拽文件到这里"` | ❌ 删 | → `region_text`（**同值**） |
| `nearby_text` | `"设置横封面"` / `"设置竖封面"` | ❌ 删 | 来自写死的 `[class*="step-active"]`，通用化时移除 |
| `upload_ancestor_class` | `"semi-upload upload-BvM5FF"` | ❌ 删 | 含 CSS-module hash，本就不稳 |
| `region_text` | — | `"点击上传文件或拖拽文件到这里"` | 新：≤5 层就近可见文本祖先（泛化 `area_text`） |
| `in_dialog` | —（旧是 `in_modal`） | `true` | 新：`[role=dialog]`/`[aria-modal]`（泛化 `in_modal`） |
| `label_text` / `aria_text` | — | `""` | 抖音 input 无原生 label/aria，空 |
| `trigger_affordance` | — | `{text:"点击上传文件或拖拽文件到这里", …}` | 新：Layer 2 |

`nearby_text` 等三个字段是被**刻意删掉**的（设计预期，不是漏录）——它们正是通用化要消除的 Semi-UI
写死选择器产物。

---

## 三、为什么删了 `nearby_text` 还能匹配

两层原因：

### 3.1 `nearby_text` 从来没参与过匹配

关键事实：旧 `_match_file_upload_by_clue` 里，`nearby_text` **只出现在日志格式串**（`want area_text=%r
nearby=%r`），从不进任何匹配条件。真正干活的信号一直是 `area_text`（→ 现在 `region_text`）和
`in_modal`（→ 现在 `in_dialog`）。

`nearby_text`（"设置横/竖封面"）语义上确实能区分横版/竖版，但旧代码**没把它接进匹配**——是个"看着有用、
实际没接线"的诊断字段。所以删它对匹配零影响。

### 3.2 `region_text` 接替了 `area_text`（同值），rect 决横/竖

后台日志 `D:\temp\douyin_redesign17.log` 里 step 8 的完整降级链（step 10 同理）：

```
5 候选 [9060, 10554, 10555, 10782, 10783]
  trigger_affordance='点击上传文件或拖拽文件到这里' 命中 []      ← L2 未命中（见 §四）
  文本束(label/aria/region) 命中 [10782, 10783]                 ← region_text 把 5→2
  in_dialog=True 命中 [10782, 10783]                            ← 都在弹窗内，未收窄
  可见候选（is_visible 非 False）= []                            ← file input 隐藏，is_visible 都是 False
  rect 就近 → idx=10782                                         ← 最终靠 rect 决横/竖
```

即：
- **`region_text`**（值 = 旧 `area_text` 同一串文案）负责把 5 候选收到 2 个封面 input（排掉视频 input
  与其它 image input）；
- **`rect` 就近**在横/竖两个封面里挑对的（录制 input rect 离哪个候选最近）。

这印证通用化核心论点：**尾部 `visibility + rect` 一直是抖音横/竖封面的实际解法**——旧代码里 `area_text`
同样撞车、`nearby_text` 没用上，heng/shu 也是靠 rect 决断。**redesign17 没有变脆弱**，新旧两版的
heng/shu 决断机制一致。

---

## 四、附带发现：Layer 2 trigger_affordance 本次未命中

日志 `trigger_affordance 命中 []`——L2 在抖音封面 case 没生效。原因是个**结构限制**：

- 抖音 drag-area（用户点的可见 affordance）是隐藏 input 的**兄弟**（同属 `semi-upload` widget 的子节点），
  不是祖先；
- 重放端 `affordance_text` 走查是**向上找可点祖先**（button/`[role=button]`/a/label/`cursor:pointer`），
  找不到兄弟，故 `affordance_text` 对不上录制的 `trigger_affordance.text`。

能兜住的是 `region_text`——它读**祖先子树的 textContent**，把兄弟 drag-area 的文案也包含进来，所以命中。

> 结论：对 Semi-UI 这种"affordance 是兄弟"的结构，**`region_text` 是主力，L2 affordance 不生效**。
> L2 的价值在 affordance 是祖先的场景（如原生 `<label>` 包裹 `<input>`，`<label>` 既是可点祖先又含文案）。
> 这是安全降级在工作——L2 miss → L1 `region_text` + rect 接住，不崩。

---

## 五、结论与潜在缺口

1. **`nearby_text` 删除不影响匹配**——它从来只是日志字段；真正匹配靠 `region_text`（= 旧 `area_text`
   同源）收窄 + `rect` 决横/竖，新旧一致。redesign17 e2e 通过，通用化在抖音封面 case 成立。
2. **潜在缺口**：万一哪天 `rect` 在横/竖间打平（两个 input rect 极近），`nearby_text`（"设置横/竖封面"）
   本来能救命——但它无法用通用方式采集（"活动 step"得靠写死的 `[class*="step-active"]`，正是要消除的
   站点特化）。目前 `rect` 兜着够用。
3. **可选增强**（非回归修复）：若想补回"活动区域文案"做兜底区分，可找**通用**信号——如就近的
   `[role="tab"][aria-selected="true"]` 文案、或祖先里的 `<legend>`/heading。但这是增强项，当前 e2e
   已通过，无紧迫性。

---

## 六、相关

- [`upload-general-identity-impl-plan.md`](upload-general-identity-impl-plan.md)——通用化实施计划（本文是其 e2e 验证）。
- [`upload-general-identity-plan.md`](upload-general-identity-plan.md)——通用化设计（信号清单 / Layer 1-3 / 技术天花板）。
- [`upload-semantic-clue-retrospective.md`](upload-semantic-clue-retrospective.md)——#139 修复复盘（降级链 + 可观测性约定，本文继承）。
- 回放文件：`rerun-history/douyin_redesign16.json`（旧）/ `rerun-history/douyin_redesign17.json`（新）。
- 后台日志：`D:\temp\douyin_redesign17.log`（`upload 线索精筛：...` 逐级命中）。
- issue #139。
