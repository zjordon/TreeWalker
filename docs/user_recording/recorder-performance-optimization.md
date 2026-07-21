# 录制器性能与 get_state 复杂度优化方案（快慢路径折中）

> 状态：方案设计，待实施决策。本文承接架构反思讨论，把"录制架构太复杂"的三点观察
> （每事件实时 get_state 性能差 / get_state 给 agent 用太复杂 / 是否能只存 xpath+attribute）
> 转化为可落地的工程方案。
>
> 决策结果：聚焦第 1+2 点（性能 + get_state 复杂度），**保留五级匹配和 stable_hash**
> （放弃第 3 点的"只存 xpath+attribute"——会重蹈 cover-upload-fix-plan-v2 §二的 xpath 不可靠坑）。

---

## 一、范围与不动的能力

### 解决的问题

- **第 1 点**：每个 click/input/select_dropdown 事件触发 1-3 次 get_state（`recorder.py:165, 192-203` 重试），录制性能差
- **第 2 点**：get_state 是给 agent 用的，record-replay 只用 `selector_map`，但每次都顺带生成 LLM 文本（`element_tree_text`）和 `page_stats`

### 不动的能力（按"保留已踩坑能力"决策）

- **五级匹配完全保留**（含 STABLE/AX name/element_hash）
- **semantic_clue 兜底**（issue #129 三重兜底机制）
- **upload_file 特殊处理**（accept + xpath，重放端 `_resolve_file_input_by_accept`）
- **信号机制**（modal_opened/dropdown_opened）
- **重放端（rerun.py）零改动**

### 不做第 3 点（只存 xpath+attribute）的理由

1. 直接砍掉五级匹配 L1/L2/L4，只剩 xpath(L3)+attribute(L5)+class(L6)
2. L3 xpath 在列表增减/SPA 重排时本身就不可靠（`_get_element_position` 下标漂）
3. **扩展 xpath 与 CDP xpath 对同一节点会算出不同结果**（`cover-upload-fix-plan-v2.md` §二已证实）——CDP 树含注释节点/shadow，live DOM 的 `parentElement` 不看这些，iframe 边界处理也不同
4. STABLE 是之前为解决 xpath 不可靠问题专门设计的，是资产不是负担

详见知识库 `browser-selectors-and-location.md` §4.1"深度展开：扩展算的 xpath 和 CDP 算的对同一节点为什么会不同"。

---

## 二、方案：三层优化，按改动量从小到大

### 优化 1：剥离 LLM 文本（最小改动，立即收益）

**问题**：record-replay 的 `handle_event` 只用 `state.dom_state.selector_map`，但 `serialize_accessible_elements`（`serializer.py:80-130`）每次都顺带生成 `element_tree_text`（LLM 文本）和 `page_stats`——这两项 record-replay 完全不用。

**关键事实**（`serializer.py:80-130` 核实）：五步管线里，前 5 步（创建简化树/绘制顺序/优化/bbox/index 分配）是 selector_map 必需的，只有最后的 `serialize_tree`（生成 element_tree_text）和 `_collect_page_stats` 是纯 LLM 文本成本。这两步可以干净地跳过，不影响 selector_map 构建。

**改法**：`build_dom_state` 加 `skip_llm_text: bool = False` 参数：
- `serialize_accessible_elements` 收到 `skip_llm_text=True` 时跳过最后两步（`serialize_tree` + `_collect_page_stats`），保留前五步
- `BrowserSession.get_state` 加同名参数透传
- `recorder.py:165` 调用改 `get_state(include_screenshot=False, skip_llm_text=True)`

**收益**：每次 get_state 省掉文本序列化和 page_stats，减少 GC 压力。但 **CDP 三源采集（`_build_enhanced_dom_tree`）仍是主成本**，所以这只是**边际收益**——真正解药在优化 2。

**风险**：低。改动集中在 serializer.py 加分支，selector_map 构建逻辑不动。

### 优化 2:快慢路径折中（核心架构优化，主性能解药）

**问题**：每个 click/input/select_dropdown 都触发 1-3 次 get_state，即使绝大多数是普通动作（modal/file upload 之外）。但 get_state 重在 CDP 三源采集，不是定位本身。

**核心洞察**：D1 死结证明"动作改变元素自身"的场景必须实时算指纹，但**绝大多数动作不改变页面结构**（普通 click 按钮、input 文本框），这些动作的指纹可以延后算——只要录制停止时页面还有它们，或用最近一次 get_state 的 selector_map 批量补。

**改法**：把 `handle_event` 的定位路径分两条：

```
慢路径（实时算指纹，保留现状）：
  触发条件：动作带 signal（modal_opened/dropdown_opened），
            或 action_name == upload_file，
            或 _ensure_target 切了 tab（页面可能已变）
  行为：get_state + locate_by_ref + DOMInteractedElement 算指纹（完全同现状）
  原因：这些场景元素会被改变，必须 modal/file input 还活着时算

快路径（只存轻量线索，延后算指纹）：
  触发条件：其余的 click/input_text/select_dropdown（绝大多数）
  行为：只存 element_ref（xpath+attr+rect+timestamp）到 action.interacted_element=[{"_deferred": True, ...}]
        不调 get_state，不调 locate_by_ref
  原因：这些动作不改页面结构，指纹可以延后算

停止时（Recorder.stop）：
  对所有 _deferred 动作批量算指纹：
  1. 一次 get_state 拿当前 selector_map
  2. 遍历所有 _deferred 动作，用存的 xpath+attr+rect 在 selector_map 批量定位
  3. 定位命中 → DOMInteractedElement.load_from_enhanced_dom_tree 算指纹（和现状同源）
  4. 定位失败 → 保留 _deferred 线索或降级为 _semantic_clue（重放端重新定位）
```

**收益**：
- 录制期间 get_state 调用次数从"每动作 1-3 次"降到"仅关键动作（modal/file upload/tab 切换）调用"
- 假设一次录制 50 步，其中 5 步是关键动作、45 步是普通动作：现状 50-150 次 get_state → 优化后 5-15 次 + 停止时 1 次批量 = **10-20 倍减少**
- 保留所有已踩坑能力（关键动作仍走完整实时路径）

**风险与边界**（必须诚实面对）：

1. **批量补指纹的时序劣势**：停止时页面可能已不是某个早期动作发生时的页面（用户后续操作改了 DOM）。但快路径只针对"不改变页面结构的普通动作"——这些动作的目标元素（button/input）通常不会被后续普通操作销毁。若被销毁（极少见，如早期打开的 modal 后续关了），降级为 _semantic_clue，重放端重新定位——和现状的兜底一致。

2. **判断"哪些动作走快路径"的启发式**：初始版本保守——只有明确无 signal 且非 upload_file 且非 tab 切换的动作走快路径。宁可多走慢路径也不要误判。

3. **批量定位失败的回退**：停止时批量定位失败的动作降级为 _semantic_clue，和现有兜底机制无缝衔接。

**为什么快路径不会重蹈 D1 死结**：D1 死结是"动作改变元素自身"（file input 重建、modal 重排），这类动作强制走慢路径（带 signal/upload_file/tab 切换）。快路径只处理"不改变页面结构的普通动作"，这些动作的目标元素不会被后续操作销毁——延后算指纹安全。如果误判了（极少见），降级为 _semantic_clue，和现有兜底一致。

### 优化 3：合并相邻同页 get_state（可选增强）

**问题**：连续多个动作（如点击同一页的多个按钮）即使走慢路径，如果启发式判断它们需要实时算指纹，仍会重复 get_state 同一个页面。

**改法**：Recorder 维护一个**短期 selector_map 缓存**（key 是 url+timestamp，TTL 比如 1s）。慢路径触发时先查缓存命中则不调 get_state。

**收益**：进一步减少慢路径的 get_state 调用。但这是优化 2 之上的增量优化，优先级低。

---

## 三、实施分阶段（建议）

### 阶段 1：优化 1（剥离 LLM 文本）—— 最低风险，立即收益。1-2 天

- 改 `build_dom_state` / `serialize_accessible_elements` / `get_state` 加 `skip_llm_text` 参数
- `recorder.py:165` 调用处改传 True
- 测试：record-replay 全量测试通过；selector_map 结构不变

**为什么先做阶段 1**：风险最低、改 3-5 个文件加参数、立即收益。可以独立验证"剥离 LLM 文本"对性能的实际提升——如果提升明显，优化 2 的紧迫性降低；如果提升有限，说明 CDP 三源才是瓶颈，确认要推优化 2。这是用最小代价获取决策依据的渐进路径。

### 阶段 2：优化 2（快慢路径）—— 核心改动。3-5 天

- 新增 `_deferred` 动作标记（`recorder/models.py` `ActionRecord`）
- `handle_event` 分流（快/慢路径判断启发式）
- `Recorder.stop` 新增批量补指纹逻辑
- 测试：重点测批量定位失败时的降级路径（_semantic_clue）
- 端到端验证：抖音上传流程仍能跑通（关键动作走慢路径，普通动作走快路径）

### 阶段 3（可选）：优化 3（selector_map 缓存）—— 增量优化。1-2 天

---

## 四、关键文件改动清单

| 文件 | 改动 | 阶段 |
|---|---|---|
| `src/tree_walker/browser/dom.py` `build_dom_state` | 加 `skip_llm_text` 参数 | 1 |
| `src/tree_walker/browser/serializer.py` `serialize_accessible_elements` | `skip_llm_text=True` 时跳过 serialize_tree + page_stats | 1 |
| `src/tree_walker/browser/session.py` `get_state` | 透传 `skip_llm_text` | 1 |
| `src/tree_walker/recorder/recorder.py:165` | 调用改传 `skip_llm_text=True` | 1 |
| `src/tree_walker/recorder/models.py` `ActionRecord` | `_deferred` 标记字段 | 2 |
| `src/tree_walker/recorder/recorder.py` `handle_event` | 快慢路径分流 | 2 |
| `src/tree_walker/recorder/recorder.py` `stop` | 批量补指纹逻辑 | 2 |
| `tests/test_recorder*.py` | 快慢路径分支测试 + 批量补指纹测试 | 2 |

---

## 五、预期效果

| 指标 | 现状 | 优化后 |
|---|---|---|
| 录制期间 get_state 调用（50 步录制） | 50-150 次 | 5-15 次 + 1 次批量 |
| 单次 get_state 成本 | CDP 三源 + LLM 文本序列化 | CDP 三源（剥离 LLM 文本） |
| 五级匹配能力 | 完整 | 完整（保留 STABLE/AX name） |
| semantic_clue 兜底 | 有 | 有（批量定位失败时无缝降级） |
| upload_file 特殊处理 | 有 | 有（走慢路径） |
| 信号机制 | 有 | 有（走慢路径） |

---

## 六、关键判断要点

### 为什么优化 2 是核心而不是优化 1

调研时核实到，`build_dom_state` 的主成本是 CDP 三源采集（`_build_enhanced_dom_tree`），不是 LLM 文本序列化。剥离 LLM 文本是边际收益（优化 1），真正减少 get_state 调用次数才是解药（优化 2）。性能差主要来自次数多，不是单次慢。

### 为什么快路径不会重蹈 D1 死结

D1 死结是"动作改变元素自身"（file input 重建、modal 重排），这类动作强制走慢路径（带 signal/upload_file/tab 切换）。快路径只处理"不改变页面结构的普通动作"，这些动作的目标元素不会被后续操作销毁——延后算指纹安全。如果误判了（极少见），降级为 _semantic_clue，和现有兜底一致。

### 为什么是"停止时批量补"而不是"定期补"

定期补要额外维护 get_state 的节奏判断，复杂度高；停止时一次批量补最简单，且快路径动作的目标元素在整段录制期间通常都存在（用户不会在录制中把所有 button 都销毁）。

### 为什么不砍 stable_hash（对应第 3 点）

`cover-upload-fix-plan-v2.md` §二已证实"扩展 xpath 与 CDP xpath 对同一节点算出不同结果"——CDP 树含注释节点/shadow，扩展 live DOM 的 `parentElement` 跳过非 Element，iframe 边界处理也相反。砍掉 stable_hash 只留 xpath，等于重走那条已证明走不通的路。STABLE 是 xpath 不稳定的工程补丁，是资产。详见知识库 `browser-selectors-and-location.md` §4.1。

---

## 七、与现有文档的关系

- **`cover-upload-fix-plan-v2.md`**（D1/area_text）：本文的"不动 stable_hash"决策的直接依据——D1 失败证明"录制端算指纹"对部分场景不可达，但 D1 失败不等于"指纹该砍"，只等于"部分场景要走 semantic_clue 兜底"。
- **`recorder-timing-solutions.md`**（语义线索回放 + 半主动 get_state）：本文的快慢路径和那篇的中期方向一致——普通动作延后处理、关键动作实时处理。区别是那篇谈"指纹算不出怎么办"（重放端重新定位），本文谈"指纹何时算"（录制端时序优化）。两个方向互补。
- **`redesign.md`**（signal 模型 + 翻译层）：本文的"慢路径触发条件含 signal"直接复用那篇的 signal 机制——signal 不只是去噪信号，也是"该走慢路径"的标志。
- **`modal-trigger-capture.md`**（modal 连锁失败）：本文的慢路径覆盖 modal 场景（带 modal_opened signal 的动作走慢路径），保留 modal 实时算指纹的能力。

---

## 八、决策点

实施前建议确认：

1. **是否同意"先做阶段 1 验证收益再决定阶段 2"的渐进路径？** 这是用最小代价获取决策依据——如果阶段 1 的剥离 LLM 文本收益就够，阶段 2 可以缓做。

2. **阶段 2 的快路径启发式，初始版本是否同意"只有明确无 signal 且非 upload_file 且非 tab 切换才走快路径"？** 这是保守起点，宁可多走慢路径。后续按实测数据调整。

3. **批量补指纹失败时的降级**：是降级为 `_semantic_clue`（重放端重新定位），还是保留 `_deferred` 标记让重放端识别？前者复用现有兜底，后者要改重放端（破坏"零改动"约束）。建议前者。
