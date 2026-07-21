# 半主动 get_state：让 upload 录到 agent 同款指纹（issue #134）

> ⚠️ **已放弃（2026-07-21，issue #134 已关闭 `NOT_PLANNED`）。本文档仅作存档保留，方案不会实现。**
>
> 关闭理由：① 额外监听事件 / `get_state` 的无谓开销；② 抢读窗口极短、靠"抢"大概率抢不到（前奏到 `change` 重建之间，受框架/导航/对话框时序影响，固有脆弱，调参消不掉）；③ 业界无成功先例（Playwright codegen / Selenium IDE / Chrome Recorder 对 upload+modal 普遍绕开），仅理论可行。设计过程中触发方式虽从 pointerdown+子树迭代到 click-on-file-input，但 ②③ 是方向性的、迭代解决不了。
>
> **现状不变**：upload 仍走 accept+xpath（`recorder.py:178` 存签名 / `rerun.py:879 _resolve_file_input_by_accept` 解析），"动作改变元素自身"的兜底由已落地的语义线索回放（PR #122）承担。

---

> **历史存档** ↓ 以下为放弃前的设计内容，仅供回顾。

> 本文原为 issue [#134](https://github.com/zjordon/TreeWalker/issues/134) 的设计文档，承接 [`recorder-timing-solutions.md`](recorder-timing-solutions.md) §三（长期方向）。
>
> **2026-07-21 讨论后更新**：触发方式从"pointerdown + DOM 子树启发式"改为"**监听 file input 自身被 click**"；并澄清"重放端零改动"对 upload **不成立**（需小改）。范围**仅 file upload**。
>
> 原状态：~~设计 + 待 spike 验证~~ → **已放弃**（见顶部）。

---

## 一、问题与一个关键 reframing

录制器是**被动观察者**：用户先做了，录制器才知道；等它 `get_state` 时页面已变。这个矛盾在"动作改变元素自身"的场景上爆发，**file upload 是典型**：change 后框架（Semi-UI 等）重建 file input，post-change 的 `get_state` 抓到重建/跳转后的页面 → 录制端算不出指纹。

### 关键修正：upload 走 accept+xpath 是"被迫的兜底"，不是"指纹对 upload 没用"

一度（包括本设计早期）误判过："重放现在走 accept+xpath（`rerun.py:879 _resolve_file_input_by_accept`）→ 说明指纹对 upload 没用 → 半主动对 upload 零增益"。这是**循环论证**：

- upload 走 accept+xpath，是因为**录制端拿不到指纹**（时序劣势），是症状不是原因。
- **file input 其实在 selector_map 里**：`_resolve_file_input_by_accept`（`rerun.py:900`）就是在遍历 selector_map 找 `input[type=file]`；`is_interactive` 规则 9（`input`→交互）也收录它。（别被 `browser/dom.py` 的 `_collect_file_inputs` 单独扫描误导——那条只服务 `backendNodeId`/`setFileInputFiles` 机械路径 + 可见性元数据，不代表 file input 不在 selector_map。）
- 所以预读 `locate_by_ref` **能**在 selector_map 找到 file input、算出指纹。半主动要治的正是这个根因：在 input 被 change 重建前抢读指纹，让 upload 不必退到 accept+xpath。

### 关键 reframing：录制器不执行用户动作

录制阶段，录制器**从不执行用户的动作**——动作是用户做的。"get_state → 执行动作"是 **agent / 重放端**的模型（`agent/step.py:180` `_prepare_context` 在执行前 `get_state`）。半主动的"半"就体现在：录制端不主动执行，只在**前奏信号那一刻抢先 get_state（预读）→ 把指纹存起来 → 等用户随后做出真实动作时认领回去**。

---

## 二、Q1：怎么识别"这次操作要半主动"

### 核心原则：误判永不破坏录制（语义线索是安全网）

语义线索回放（PR #122）已是"录制端算不出指纹"的兜底。所以前奏识别的误判永远不破坏录制：漏判 → 回退语义线索；误判 → 多一次 `get_state`。"准确识别"是尽力而为，不是硬约束。

### 触发方式的演进（重要）

**初版**：扩展监听 `pointerdown`，落在上传区时用 `isUploadTrigger(target)` 判断（交互祖先的**子树**含 `<input type=file>` 则命中）。

**`rerun-history/conver-hen.html`（抖音封面，Semi-UI）**：子树谓词命中——两个同 `accept` 的隐藏 input（`semi-upload-hidden-input` + `-replace`）都在 `.semi-upload` 子树里；且两者 class 不同 → 指纹天然区分，比 xpath（重建后漂）更稳。

**`rerun-history/upload_mp4.html`（抖音视频）暴露漏洞**——"上传视频"按钮和 file input 是**兄弟节点**：

```
container-drag-VAfIfu
├── container-drag-info-efu4jl
│   └── container-drag-upload-tL99XD
│       └── <button>上传视频</button>      ← 用户点的按钮（line 11-16）
└── <input type="file">                    ← 和 info 区是【兄弟】（line 20-22），不在按钮子树里
```

按钮和 file input 不在同一子树 → `isUploadTrigger(button)` 的"子树含 file input"**直接落空**。子树启发式对"按钮与 input 分离"的结构失效。

### 最终触发：监听 file input 自身被 click

用户点按钮 → 框架 handler 调 `fileInput.click()` 开对话框 → 这次 programmatic click 的 target **就是 input 本身**。扩展**现在已收到并 skip 这个 click**（`action-recorder.ts:157`，注释明说"file input 的 click 几乎都是 JS 触发的 input.click()"）。把它从"skip"改成"**skip + 发 `pre_action`**"即可：

- target 就是那个 input → 精确知道是谁，**完全不依赖按钮/input 的 DOM 位置关系**（兄弟、嵌套、隔多远都行）；
- 这一刻 input 完好（对话框刚开、`change` 未到）→ 后端能算指纹；
- 两个 HTML 都命中（封面的 `hidden-input`、视频的兄弟 input）；
- ~零误报——click 落在 `input[type=file]` 上几乎必是上传意图。
- **前提**：框架用 `input.click()` 开对话框（Semi-UI/drag-area 都是）。`<label for>` 原生触发会漏 → 回退现状（两个 Douyin 例子不踩）。

---

## 三、Q2：怎么处理"动作前 get_state → 执行动作"

### 流程（录制端预读 → 存 → 认领；重放端小改）

```
file-input click（框架 input.click() 开对话框）
  → 扩展发 pre_action（input ref + accept）            ← 复用 onClick 现有 file-input 分支，从 skip 改成 skip+发
  → 后端 get_state + locate_by_ref + 算指纹 → 存 _pending_prestate（input 完好）
  → 用户选文件 → change → 扩展照常发 upload_file
  → 后端认领预读指纹，填进 interacted_element
  → 重放：有指纹走 _match_element_index（EXACT/STABLE），无指纹回退 _resolve_file_input_by_accept
```

**关键**：预读 `get_state` 发生在 input 被 change 重建**之前**（click-input ≪ 用户在对话框浏览 ≪ change），抢到的是原 input 的指纹，和 agent 同源。click-input 与 change 之间是**人时间尺度**（秒级），后端预读 `get_state` 早就完成。

### 时序 / 竞态处理

| 细节 | 处理 |
|---|---|
| **顺序保证** | `file-input click` 严格早于 `change`。后端单事件循环，pre_action 的 get_state 在 change 到达前完成。即便 aiohttp 并发交错，认领**容忍** prestate 缺失（回退 `[None]`）。无需加锁。 |
| **用户取消**（点开对话框又没选） | 无 change → prestate 挂着。靠 `_PRESTATE_TTL_S=30` + 每次 `handle_event` 开头 / stop 清扫陈旧项。 |
| **多 input 错配**（封面 hidden-input vs -replace） | 按 `expected` 键 + 认领时校验 `accept`/指纹匹配；不匹配则丢弃、回退。 |
| **`_ensure_target` 切 target** | 预读分支包在外层 try（对齐现有 `recorder.py:163`）。 |
| **`server.py` ok 标志** | `_handle_event` 特判 pre_action → `{ok:true, step:None}`（避免扩展误报 /event 失败）。 |
| **副作用观察窗** | pre_action 走 `emit` 会顺带 `onAction` 开 1s 窗。对 upload 无害（文件对话框是 OS 级，不产 DOM modal）。 |

### 重放端需小改（"重放零改动"对 upload 不成立）

要让重放消费预读指纹，upload 重放路径要改一小处：**`rerun.py` 的 upload 分支——`interacted_element[0]` 是真指纹 dict（非 `_semantic_clue`/`None`）时先走 `_match_element_index`（EXACT/STABLE），否则回退 `_resolve_file_input_by_accept`（accept+xpath）**。这正是 [`recorder-timing-solutions.md`](recorder-timing-solutions.md) §二 早就点明的"打破重放端零改动"。

---

## 四、实施草图（最小改动）

### 扩展端（`recording_extension/`）

| 文件 | 改动 |
|---|---|
| `shared/types.ts` | `RecorderEvent.type` 联合加 `'pre_action'`；`params` 加 `expected?: 'upload_file'` |
| `capture/action-recorder.ts` | **`onClick` 现有 file-input skip 分支（:157）**改成"skip + 发 `pre_action`"（`buildElementRef(raw)` + `emit({type:'pre_action', url, is_top_frame, xpath:ref.xpath, rect:ref.rect, ...refAttrs(raw), params:{expected:'upload_file', accept}})`）。**不新增 pointerdown 监听**。 |
| `shared/backend.ts` / `entrypoints/background.ts` / `entrypoints/content.ts` / `wxt.config.ts` | **零改动**——`postEvent` / `case 'event'` / `sendEvent` / `host_permissions` 都通用，pre_action 原样过管 |

### 后端（`src/tree_walker/recorder/` + `agent/`）

| 文件 | 改动 |
|---|---|
| `recorder.py` | ①`__init__`+`start` 加 `self._pending_prestate: dict[str,dict] = {}`（**放实例上，不放 `RecordingState`**——否则泄进 flatten）；②`handle_event` 顶部（`translate_event` **之前**）加 `if event['type']=='pre_action': return await self._capture_prestate(event)`；③`_capture_prestate`：外层 try 里 `_ensure_target`+`get_state(include_screenshot=False)`+`locate_by_ref`+`load_from_enhanced_dom_tree().to_dict()` → park（带 ts/accept）→ return None；④upload_file 分支(~178)：在现有 `[None]` 兜底前 pop+TTL+accept 校验，命中则 `interacted_element=[fingerprint]`；⑤常量 `_PRESTATE_TTL_S=30`（挨着 `_SIGNAL_WINDOW_S`）；⑥`handle_event` 开头 + stop 清扫陈旧 prestate |
| `server.py` | `_handle_event` 特判 pre_action → `{ok:true, step:None}` |
| `event_mapper.py` | `map_event`/`translate_event` 对 `pre_action` 早返 None（兜底防御） |
| **`agent/rerun.py`** | **upload 分支小改**：`interacted_element[0]` 是真指纹时走 `_match_element_index`，否则回退 `_resolve_file_input_by_accept`（"重放非零改动"的落点） |
| `models.py` / `flatten.py` | **零改动**——`interacted_element` 已收 dict 指纹、flatten 纯透传 |

**复用的现成件**：`onClick` 的 file-input 分支、`buildElementRef`/`refAttrs`、`emit`/`postEvent` 管线、`_ensure_target`、`locate_by_ref`（`locator.py:104`）、`DOMInteractedElement.load_from_enhanced_dom_tree`、`attach_signal` 的 TTL 模式（`_SIGNAL_WINDOW_S`）。

---

## 五、范围（仅 file upload）与三层降级链

首期只做 **file upload**：它正是"拿不到指纹 → 被迫走兜底"的典型，半主动直接治根因。modal/submit 暂由语义线索兜底（前奏更脆弱：难预测 click 开 modal；submit 与跳转竞态）。

三层降级链（半主动产出最优指纹，与语义线索互补）：

1. **抢到前奏** → 半主动 get_state → agent 同款指纹 → 重放 `_match_element_index`（**最优**）
2. **抢不到/漏判**（无前奏、`<label>` 触发）→ 语义线索 → 重放重新定位（**次优**，已落地）
3. **都不行** → accept+xpath 兜底（`_resolve_file_input_by_accept`，**最后防线**，即现状）

为后续推广留口子：`_pending_prestate` 按 `expected` 键、`_capture_prestate` 按 `expected` 分发；验证 upload 闭环稳定后再议 modal。

---

## 六、关键未知 + spike（先验证再实现）

**待验证**：预读的指纹在**重放时**能否匹配上？重放走到 upload 步时还没上传，`get_state` 重建的是"上传前"的原页面，和录制 click-input 那一刻理论上都是"原 input、未选择"的同一状态，ax_name/属性/祖先链应一致。但这是**经验问题**，不能拍脑袋。

**最小 spike**（只读 + 临时脚本，不动正式代码）：

1. 在 file-input click 时机（或直接用两个 HTML 的 input）算一次 input 的 `element_hash`/`stable_hash`；
2. 模拟重放 upload 步（同一 input 在"上传前"页面状态）再算一次；
3. 比两者是否相等。

覆盖两个结构：`conver-hen.html`（双 input 同 accept，验证指纹能区分 hidden-input vs -replace）、`upload_mp4.html`（兄弟结构，验证 click-on-input 触发 + 指纹）。

**spike 通过 → 再按 §四 实现；spike 证伪 → 方向需重估（可能语义线索已够）。**

---

## 相关文档

- [`recorder-timing-solutions.md`](recorder-timing-solutions.md) —— 蓝本（§三 长期方向）
- [`semantic-clue-replay.md`](semantic-clue-replay.md) —— 中期方向（已落地，PR #122）；半主动是最优层、语义线索是次优层
- [`fingerprint-realtime.md`](fingerprint-realtime.md) —— 指纹为何必须实时算（半主动"抢在元素改变前算"的原理依据）
- [`modal-trigger-capture.md`](modal-trigger-capture.md) —— modal 触发器（后续推广方向）
- issue [#134](https://github.com/zjordon/TreeWalker/issues/134) —— 跟踪 issue（讨论整理见其评论）
