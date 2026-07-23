# 封面上传选错 file input：根因订正与正解方案（v2，待 review）

> 🛑 **2026-07-23 订正（本文核心结论已被证伪，issue #136）**：本文把"封面上传选错 file input"当作
> 封面流程失败的根因、并围绕"给 file input 算指纹"（D1 标记 → area_text）展开——**方向错了**。实测证明
> 封面流程失败的**真正主因是"切换竖封面"的 click 回放点错**（指纹撞车，点到设置横封面），与 file input
> 重建无关；已在 issue #136 用"扩展文字 + 重放端 TEXT 匹配级"修复（详见
> `recording-reliability-fixes-retrospective.md`）。§八的 file-input-重建现象本身真实，但不是卡点；
> area_text 方案从未实施，upload 至今走 accept+xpath 兜底。**下方原文保留作"走过的弯路"存档。**

> ⚠️ **2026-07-16 更新：D1（标记算指纹）已实施但实测失败**——抖音 Semi-UI 上传后**重建 file input**，
> 扩展打的 `data-tw-recmark` 标记随旧 input 销毁，后端 get_state 时新 input 已无标记。拟转向
> **area_text 方案**（drag-area 文本是 input 的兄弟元素、不随重建消失，且能区分封面/参考图）。
> 下方保留 D1 原方案描述（已尝试），末尾「D1 实测失败与根因订正」节给出失败分析 + area_text 方案。

> 本文是对 `cover-upload-fix.md`（已订正）后续的**正解方案**，待 review 后实施。经 Explore agent
> 核实源码 + 本地验证，订正了前几版（cover_index 等）的错误方向，定位到精确根因，并给出回到
> "忠实记录指纹"的正解。

---

## 一、难点反思：为什么改了这么多版都不对

你问得对——**封面上传的业务逻辑很简单**（切 step「设置横/竖封面」+ upload 到同一个 input），
agent 忠实记录指纹就重放成功了。难点不在业务，在我录制架构的偏差 + 反复在错误框架里打转：

1. **B 方案一刀切切错了对象**：为治"视频上传的导航竞态"，我给**所有** upload_file 去掉了指纹
   （`interacted_element=[None]`）。但**封面上传根本不导航**，本该像 agent 那样保留指纹。这刀把
   封面 input 的指纹也丢了。

2. **丢了指纹，重放只能用 accept+xpath 事后猜**：accept 对多个 image input 无区分力（封面/参考图
   都是 `image`）；xpath 又不可靠（见下根因）→ fallback `candidates[0]` → 选到参考图。

3. **我前几版一直在"重放端换姿势猜 input"打转**（accept → xpath → cover_index），每版换个猜法，
   却没回到根本——**录制时就没握住正确的 input 身份**。cover_index 还踩了错误前提（误判两组
   `semi-upload-hidden-input` 是横/竖，其实是参考图+封面）。

**本质**：agent 录制时 LLM 实时选对 input、当场算出指纹；扩展录制没有 LLM，change 事件虽握住了
`e.target`（就是正确 input），但传递它的手段（xpath）不可靠 → 后端 `get_state` 后找不到对应 node →
算不出指纹 → 只能退回 accept 猜 → 猜错。**你的直觉"忠实记录就好"完全正确，正解就是让扩展像
agent 一样握住正确 input 算出指纹。**

---

## 二、根因（经源码核实，精确订正）

### agent 重放成功的精确机制

- **录制**：`step.py:_project_interacted_elements` 用 `selector_map[LLM给的index]` 算完整指纹
  （`element_hash` + DOM walker `xpath` + bounds）。
- **重放**：`rerun.py:_match_element_index` 用 `element_hash` 圈定"所有同款 `semi-upload-hidden-input`"
  （封面+参考图哈希**相同**，因 `views.py:_get_parent_branch_path` 只取 tag 不取下标），再用**录制时的
  DOM walker xpath**（`div[2]/input[1]` 横 vs `div[3]/input[1]` 竖）在同款里**唯一命中**。
  **哈希圈同款，xpath 定位横/竖**。两层叠加才选对。

### 扩展录制失败的精确根因

1. B 方案刻意不给指纹（`recorder.py:170-173,218-220`），重放走 `_resolve_file_input_by_accept`。
2. `accept` 对多个 image input **无区分力**；唯一区分靠 xpath 唯一命中，但**扩展存的 xpath 失配**
   → fallback `candidates[0]` → 选到参考图。
3. **xpath 失配的根因不是"格式差"**（已核实：扩展 `selector.ts:77 xpathFor` 与 DOM walker
   `views.py:370/375-388` 都是"单子不带、多兄弟带 [n]"的**相同**条件性规则），而是
   **content-script 算的 xpath（`document` 起点、`parentElement/children`）与 CDP DOM 树算的 xpath
   （含 shadow/iframe/注释节点的父链）对同一节点算出不同结果**——靠 xpath 比较定位本质上不可靠。

**一句话**：agent 靠"完整指纹"选对；扩展丢了指纹、靠事后 xpath 猜，而 xpath 不可靠 → 选错。

---

## 三、正解：让扩展录制产生 agent 同款指纹（D1 方案）

**核心**：扩展 `onFileChange` 时 `e.target` 就是用户实际用的那个 input（正确目标）。给它打一个
**临时唯一标记**，让后端 `get_state` 后在 selector_map **可靠定位**到 e.target 对应 node（绕过 xpath
不确定性），算出**与 agent 完全一样格式**的指纹（`DOMInteractedElement.load_from_enhanced_dom_tree`）。
重放直接走 agent 已验证的指纹路径。

- **封面上传**（不导航）：get_state 抓正确页 → 凭标记找到 node → 算出指纹 → 重放指纹匹配精准选对。✓
- **视频上传**（导航竞态）：get_state 抓到跳转后页面，旧页 input（带标记）已消失 → 找不到 →
  无指纹 + accept 兜底（B 方案保留）。✓
- 即"**能算指纹就算（封面）、算不出就 accept（视频）**"，一刀切变分情况，最贴近"忠实记录"。

---

## 四、改动清单

### 1. 扩展 `recording_extension/capture/action-recorder.ts` `onFileChange`
给 `raw`（e.target）打临时唯一标记 + 发标记值：
```typescript
const mark = `tw-${Date.now()}-${Math.random().toString(36).slice(2,8)}`;
raw.setAttribute('data-tw-recmark', mark);
emit({ type: 'upload_file', xpath: ref.xpath, ...refAttrs(target),
       params: { path: file.name, accept, recmark: mark } });
```
（标记一次性、随机，残留无害；后端算完会清。）

### 2. `src/tree_walker/recorder/event_mapper.py` `map_event`
upload_file 透传 `recmark`：`"recmark": str(ep.get("recmark", ""))`。

### 3. `src/tree_walker/recorder/recorder.py` `handle_event` upload_file 分支
改为"先试算指纹，算不出退 accept"：
```python
if action.action_name == "upload_file":
    recmark = action.params.get("recmark") or raw_params.get("recmark") or ""
    located_node, located_idx = None, None
    if recmark:
        # get_state 后凭标记在 selector_map 找 e.target 的 node
        for idx, node in selector_map.items():
            attrs = getattr(node, "attributes", {}) or {}
            if attrs.get("data-tw-recmark") == recmark:
                located_node, located_idx = node, idx
                break
    if located_node is not None:
        # 算 agent 同款指纹
        action.interacted_element = [DOMInteractedElement.load_from_enhanced_dom_tree(located_node).to_dict()]
        action.params["index"] = located_idx
        await self._clear_recmark(recmark)   # JS evaluate 清标记
    else:
        # 找不到（视频导航 / 无标记）→ B 方案兜底
        action.interacted_element = [None]
        action.params["accept"] = action.params.get("accept") or ""
        action.params["xpath"] = event.get("xpath") or ""
    locate = None  # 仍不走 locate_by_ref
```
新增 `_clear_recmark`：`Runtime.evaluate` 跑
`document.querySelectorAll('[data-tw-recmark="..."]').forEach(e=>e.removeAttribute('data-tw-recmark'))`。

**已确认可行**：`dom.py:_parse_attrs`（66-72）无属性白名单，保留全部 HTML 属性（含自定义
`data-tw-recmark`，仅值截断到 200 字符；recmark 值远短于此）。故 selector_map node.attributes 能
读到标记、凭它定位 node。

### 4. 重放 `src/tree_walker/agent/rerun.py`：无需改
有指纹 → `_match_element_index`（element_hash 圈同款 + xpath 定位，agent 已验证路径）；
无指纹 → `_resolve_file_input_by_accept`（B 方案，已存在）。

### 5. 测试
- `test_recorder.py`：upload_file 有 recmark + 命中标记 node → 产生指纹（interacted_element 非 None、
  params.index 设）；recmark 找不到 → `[None]` + accept 兜底。
- `test_recorder_event_mapper.py`：`map_event` 透传 recmark。
- 重放路径已有 agent 测试覆盖（`_match_element_index`），无需新增。

---

## 五、验证（端到端）

1. `cd recording_extension && npm run build`；Chrome 重载扩展。
2. 重录抖音上传：封面步骤的 upload_file 事件带 `recmark`，录制端算出指纹，`interacted_element` 非 None。
3. 重放，日志封面步骤应走 `_match_element_index`（`元素 index 更新 ... 匹配级别 EXACT`）而非
   `_resolve_file_input_by_accept`，且选对封面 input（竖封面传对）。
4. 视频步骤仍走 accept 兜底（导航，无指纹），行为不变。
5. 全量 `uv run python -m pytest tests/ -x`；recorder 包覆盖率 ≥ 85%。

---

## 六、备选（更轻量，不推荐）

**D0**：标记定位后只存 DOM walker `node.xpath`（替代扩展 xpath），不算 `element_hash`，重放仍走
accept 路径但 xpath 能匹配。改动略小，但**无 element_hash 预筛**，DOM 漂移时仍可能失配——不如 D1 鲁棒。
D1 产生 agent 同款指纹、走已验证路径，是正解。

---

## 七、关键文件路径（便于实施时精读）

- `recording_extension/capture/action-recorder.ts`（onFileChange）、`capture/selector.ts`（xpathFor:64-82）
- `src/tree_walker/recorder/recorder.py`（handle_event:131-234，upload_file 分支 153-173/217-220）
- `src/tree_walker/recorder/event_mapper.py`（map_event upload_file）
- `src/tree_walker/browser/dom.py`（`_parse_attrs:66-72`、`_collect_file_inputs:502-535`）
- `src/tree_walker/browser/views.py`（`xpath:355-373`、`_get_element_position:375-388`、
  `_get_parent_branch_path:578-586`、`DOMInteractedElement.load_from_enhanced_dom_tree:747-762`）
- `src/tree_walker/agent/step.py`（`_project_interacted_elements:1072-1101`，agent 指纹产生）
- `src/tree_walker/agent/rerun.py`（`_match_element_index`、`_nearest_idx:116-146`、
  `_resolve_file_input_by_accept`、`_execute_history_step:429-516`）
- `rerun-history/douyin_upload_history.json`（agent 成功重放对照：step 10/13 指纹相同）

---

## 八、D1 实测失败与根因订正（2026-07-16）

### 实测结果

D1（标记算指纹）实施后，重录 `douyin_redesign6.json`，upload_file 步骤 `interacted_element` 仍为空。
加诊断日志（`recorder.handle_event` upload_file 分支打印 selector_map 里 file input 的标记）后重录：

```
[D1诊断] step3(视频)   recmark=tw-... | selector_map file inputs(idx,mark,accept): []
[D1诊断] step10(横封面) recmark=tw-... | [(2486,None,image),(3145,None,video),(3565,None,image),(3566,None,image),(4538,None,image),(3794,None,image)]
[D1诊断] step13(竖封面) recmark=tw-... | [(2486,None,image),(3145,None,video),(4582,None,image),(4583,None,image),(4965,None,image),(3794,None,image)]
```

### 根因订正：标记被 Semi-UI 重建的 file input 带走

- **视频(step3)**：`file inputs: []` —— 导航竞态，get_state 抓跳转后页面，无 file input（预期，退 accept）。
- **封面(step10/13)**：6 个 file input，**mark 全是 `None`** —— 扩展打的 `data-tw-recmark` 在**旧 input** 上，
  但后端 get_state 时抖音 Semi-UI 已**重建 file input**（agent 核实：上传后 backend_id 7069→7817→8248），
  新 input 无标记 → 凭标记找不到。

**D1 致命伤**：标记打在一个**会被销毁的元素**（file input）上。扩展是被动观察 change，等它打标记 → 发事件 →
后端 get_state 时，抖音 JS 已在 change 处理里重建 input，标记必然丢失。

**agent 为何不受影响**：agent 在**动作前** get_state 算指纹（input 完好）；扩展 change 后才 get_state
（input 已重建）——"被动观察 change" 相对 "主动执行" 的固有劣势。

### 正解：换一把"不会被重建带走"的钥匙——drag-area 文本

封面上传区结构（`conver-dialog.html` 1363-1385）：
```
<div class="semi-upload upload-BvM5FF">     ← 容器
  <input class="semi-upload-hidden-input">  ← 上传后被重建（D1 标记丢失）
  <input class="semi-upload-hidden-input-replace">
  <div class="semi-upload-drag-area">
    <div class="semi-upload-drag-area-main-text">点击上传文件或拖拽文件到这里</div>  ← 兄弟元素，不重建 ✓
  </div>
</div>
```

**drag-area 文本是 input 的兄弟元素，不随 input 重建**，且（`debug_cover_area.py` 实测）能区分：
- 封面区：「点击上传文件或拖拽文件到这里」
- 参考图区（list-Ldrppp）：空
- 视频封面区：「点击上传新的视频封面…」

### area_text 方案（拟转向）

1. **扩展 `onFileChange`**：读 `e.target` 所在 semi-upload 的 `drag-area-main-text` 文本，发 `area_text`（替代 D1 的 recmark）。
2. **`dom.py` `_collect_file_inputs`**：给 `FileInputInfo` 加 `area_text`——遍历到 file input 时取其 semi-upload 祖先的 drag-area-main-text。
3. **`views.py` `FileInputInfo`**：加 `area_text: str = ""`。
4. **`recorder.handle_event` upload_file**：凭 **accept + area_text** 在 `file_inputs_meta` 找匹配 input（重建后的新 input，drag-area 文本还在）→ `backend_node_id` → selector_map 找 node → `DOMInteractedElement` 算指纹。
5. **`rerun.py`**：无需改（有指纹走 `_match_element_index`，无指纹走 accept 兜底）。

**为何这次能成**：area_text 是 input 的**兄弟元素文本**，不随 input 重建消失；后端 get_state 时新 input 的 drag-area 文本还在，凭它找到新 input。新 input 指纹 == 旧 input 指纹（agent 核实：属性链一致 → element_hash 同），算出的指纹正确，重放走 agent 已验证的指纹路径。

**取舍**：area_text 依赖抖音文案「点击上传文件或拖拽文件到这里」不改。比 xpath（漂移）、标记（被重建）可靠；比单用 element_hash（封面/参考图同款）多了区分力。

### 待回滚的 D1 代码（转向 area_text 前）

- `action-recorder.ts onFileChange`：去掉 `data-tw-recmark` setAttribute + recmark，改读 area_text。
- `event_mapper.py`：recmark → area_text。
- `recorder.py`：去掉凭标记找 node + `_clear_recmark` + 诊断日志，改为凭 area_text 在 file_inputs_meta 找。
- 测试：D1 的 recmark 测试改为 area_text。

---

## 九、本文结论被证伪（2026-07-23，issue #136）

> 本节订正本文（含 §八）的核心结论。完整的修复总结见 `recording-reliability-fixes-retrospective.md`。

### 本文的误诊

本文的核心假设：封面流程失败 = **upload_file 选错了 file input**（选到参考图而非封面），根因是录制端
算不出 file input 的指纹（导航竞态 + input 重建），正解是"给 file input 找一把稳定的钥匙"（D1 标记 →
area_text）。§八在这个框架下把 D1 失败归因为"Semi-UI 重建 file input 带走标记"，并提出 area_text。

### 实测推翻

重录 `douyin_redesign11/12.json` 重放定位：封面流程失败的**真正主因是"设置竖封面"切换 click 回放点错**
——本该点设置竖封面，实际点到设置横封面。根因是指纹撞车：两个 step tab 都是 `ax_name=null` 的 `<div>`，
`element_hash` 含会翻转的 `step-active` 状态类 + 录制时序（点后状态），重放时 EXACT 匹配到当前激活的
设置横封面（`len(matches)==1` 直接返回，绕过 xpath tie-break）。切错 → 后续竖封面上传落到错的区 →
整个封面流程失败。**与 file input 重建无关。**

证据：

- 录到的设置竖封面 click 元素是**顶部 step tab**（`<div class="step-dXVbPX step-active-...">`），不是 file input。
- 失败发生在**上传之前**的切换步，不是 upload_file 步。
- **agent 录制点的是底部 `<button>`**（有 ax_name="设置竖封面"，指纹唯一）所以 agent 不受影响——反证根因
  在 click 指纹撞车，不在 file input。（agent 点 button、扩展点 div tab 的这个差异本身就是根因线索，当时被忽略了。）

### 正解（已在 #136 落地）

**用扩展捕获的元素可见文字做主身份**：扩展 `buildElementRef` 早就有 `ref.text`（`textContent`），之前
click emit 把它丢了；修复后 click/select 带上 text，重放端加 `MatchLevel.TEXT`（优先于 EXACT，按
`get_all_children_text` 匹配，`node_name` 约束避免误匹配底部按钮）。配合 `normalize_text` 移除全部空白
（抖音每字独立 span，`textContent` 无分隔符 vs `get_all_children_text` 用 `\n` 连接，不 strip 会失配）。

切换 click 修好后，整个封面流程（切竖封面 → 上传竖封面图 → 完成）打通。**upload_file 本身无需算指纹**
——至今仍走 accept+xpath 兜底（`_resolve_file_input_by_accept`），由语义线索回放（`semantic-clue-replay.md`）承担。

### 关于 §八的 file-input-重建现象

§八观察到的"Semi-UI 上传后重建 file input、D1 标记丢失"**现象本身真实**（不是假的），但：

- 它**不是封面流程失败的卡点**（卡点在切换 click，发生在上传之前）。
- area_text 方案**从未实施**（还没转向 area_text，issue #136 已用更通用的 text 方案解决了卡点）。
- 即便 file input 重建导致 upload 算不出指纹，accept+xpath 兜底 + 语义线索回放已足够，**不需要 area_text**。

### 教训

- **封面流程"失败"要先定位失败在哪一步**（切换 click？上传？确认？），别默认是 upload 选错 input。本次
  花了大量精力在 file input 指纹（D1/area_text）上，真正的卡点（切换 click）却在上游。
- **agent 能跑通、扩展跑不通的差异**，先查"agent 点的元素 vs 扩展点的元素"是否不同（agent 点 button、
  扩展点 div tab），差异本身就是根因线索。
- 文档结论要标日期与证据状态；本文从"v2 正解方案"到"§八根因订正"多次自称已定位根因，但都建立在
  "upload 选错 input"的未经验证前提上——前提错了，后面的推理再精巧也没用。
