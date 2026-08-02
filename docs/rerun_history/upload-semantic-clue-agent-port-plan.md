# 方案：把「上传封面语义线索」采集移植到 agent 路径

> 背景命令：`uv run python examples/csv_rerun.py douyin_upload_skill.json <csv>`
> 关联文档：`docs/user_recording/upload-semantic-clue-plan.md`、`upload-semantic-clue-retrospective.md`、`upload-general-identity-impl-plan.md`、`upload-general-identity-e2e-analysis.md`；参考回放产物 `rerun-history/douyin_redesign17.json`（手工录制，带线索）。

## 背景（为什么做这件事）

`csv_rerun.py` 重放抖音发视频流程时，**上传封面的两步（第 11 步横版 heng.png、第 13 步竖版 shu.png）不稳定**——有时传到错误的封面槽位。

根因（已逐层确认）：

- `csv_rerun.py` 是**纯重放**（`batch_rerun` → `rerun_history`），全程不调决策 LLM，只在结尾调一次总结 LLM。所以不稳定来自**重放时重新定位错了 file input**，不是 agent 实时挑错。
- `douyin_upload_skill.json` 是 **agent 录制**的历史（含 `evaluation_previous_goal`/`memory`/`next_goal` 等 agent-loop 字段），其上传步的 `interacted_element` 只是原始 DOM 节点序列化（`xpath` / `element_hash` / `stable_hash` / `bounds:null` / `attributes.accept`），**没有 `_semantic_clue`**。
- 因此重放跳过稳健的 `_match_file_upload_by_clue`（`rerun.py:1032`），落到 `_match_element_index`（横/竖 input 的 `element_hash` 撞车）→ `_resolve_file_input_by_accept` 直接返回 `candidates[0]`（DOM 顺序第一个）。唯一能区分横/竖的是 xpath 的 `div[2]/input[1]` vs `div[3]/input[1]`，但抖音弹窗外层 `div[12]` 下标跨运行漂移 → xpath 失配 → 选错。
- **手工录制**早已解决：`recorder.py:_store_upload_clue`（`recorder.py:317-353`）在上传时存 `_semantic_clue{kind:file_upload, accept, label_text, aria_text, region_text, in_dialog, trigger_affordance?}`，重放端 `_match_file_upload_by_clue` 按线索精筛——**匹配器与路径无关，谁录的只要带线索就能用**。缺口纯粹在**采集**：agent 路径不采集线索。

**一个比预期更深的坑（关键）**：`_match_file_upload_by_clue` 尾部的 rect 就近（`_nearest_idx`，`rerun.py:128-160`）对隐藏 input **完全失效**——`_bounds_center` 在 `w<=0 and h<=0` 时返回 `None`（`:117-118`），导致直接返回 `candidates[0][0]`；而且即便线索带真实 rect，**每个候选**的几何来自 `snapshot_node.bounds`（`_elem_bounds`，`:122-125`），隐藏 input 同样是 `{0,0,0,0}` → `_bounds_center` 返回 `None` → 循环体 `continue` → 还是落到 `candidates[0][0]`。当前能蒙对全靠 `_nearest_idx` 的「xpath 精确命中」首条臂（`:144-148`），xpath 一漂移就崩。所以本方案必须让**候选侧也带真实几何**，rect 就近才有意义。

**目标**：把语义线索采集移植到 agent 上传执行路径，让 agent 录制的历史也带 `_semantic_clue`，重放复用已有匹配器。确认范围 = **采集 + 重放**（不动实时 LLM 挑选、不给无线索旧历史加重放兜底），现有 skill 文件**重录一次**。

---

## 方案总览

1. 新建共享模块 `upload_identity.py`（JS 探针 + 线索构建/采集 + rect 选择器），采集端（`actions.py`）与重放端（`rerun.py`）共用单一真相源。
2. `rerun.py`：两个方法改为薄委托（纯重构，行为不变）+ 匹配器尾部改为 **`container_rect` 就近**（候选侧真实几何）。
3. `actions.py`：`_action_upload_file` 解析出 `backend_id` 后 best-effort 采集线索，塞进 `ActionResult.metadata["upload_clue"]`。
4. `step.py`：`_project_interacted_elements` 收 `results`，对 `upload_file` 动作用线索覆盖原始节点投影，写入 `{"_semantic_clue": True, "kind": "file_upload", **clue}`（对齐 `recorder.py:353`）。
5. 测试：采集产物含非零 `container_rect`；xpath 漂移下靠 `container_rect` 区分横/竖；录制端线索零回归；单 input 页不回归。

> 缩进：项目用 **tab**，所有改动须与周边代码一致（CLAUDE.md）。包管理用 `uv`，跑测试用 `uv run python -m pytest tests/ -x -v`，覆盖率目标 >85%。

---

## 改动点

### 1. 新建 `src/tree_walker/agent/upload_identity.py`（单一真相源）

把 `RerunMixin` 的私有能力抽成模块级函数（`self.browser` → 显式 `browser` 形参），供采集端与重放端共用。`actionability.py` 仍只管 visible/enabled/stable（其既有职责），上传身份是独立关注点，故单开文件。

包含：

- `UPLOAD_INPUT_CONTEXTS_JS`：**扩展** `rerun.py:980-1008` 的 JS，每个 `input[type=file]` 条目额外返回 `container_rect`——最近（≤6 层）非零 `getBoundingClientRect()` 的祖先 rect。抖音的 Semi-UI 封面槽就是它，横/竖槽位几何不同，而隐藏 input 自身 rect 是 `{0,0,0,0}`。不变量不变：仍按 DOM 文档序一条对应一个 input（`len(entries)==len(candidates)` 对齐，`rerun.py:1023` 不受影响）。
- `file_input_candidates(selector_map, *, accept_hint, path)`：从 `rerun.py:907-930` 原样搬入。
- `upload_input_contexts(browser, candidates, *, kind)`：从 `rerun.py:963-1030` 搬入（`self.browser` → `browser`；JS 用上面扩展版，返回值多一个 `container_rect`）。
- `nonzero_rect(r)` / `effective_clue_rect(clue)`：选 rect 的优先级 `clue["rect"]` → `clue["container_rect"]` → `clue["trigger_affordance"]["rect"]`，首个非零者胜出；全无则返回原 `clue.get("rect")`（保留 legacy 行为）。
- `build_upload_clue(node, ctx_entry)`：镜像 `recorder.py:331-353` 形状产出线索（`xpath/tag/rect(input 快照)/accept/label_text/aria_text/region_text/in_dialog/container_rect`，`affordance_text` 非空时附 `trigger_affordance`）。
- `async capture_upload_clue(browser, selector_map, backend_id) -> dict | None`：采集端入口。按 `backend_node_id` 在 `selector_map` 找 node → 算 accept/kind → `file_input_candidates` → `upload_input_contexts` → 取该 input 的 `ctx_entry` → `build_upload_clue`。**全程 try/except，失败返回 None，绝不阻塞上传**。

### 2. `src/tree_walker/agent/rerun.py`（纯重构 + 匹配器尾部增强）

- `_file_input_candidates`（`:907`）、`_upload_input_contexts`（`:963`）改为委托共享模块（行为完全不变；现有 mock 这两个方法的测试零改动）。
- **匹配器 `_match_file_upload_by_clue`（`:1032-1129`）两处协调改动**：
  - **(a) 多候选时强制算 `ctx`**（`:1063-1066`）：`need_ctx = True`（多候选才到这里，单候选已在 `:1052` 提前返回），始终 `await self._upload_input_contexts(...)`。尾部 rect 就近依赖 `ctx` 的 `container_rect`。
  - **(b) 尾部 rect 就近改为 `container_rect` 感知**（替换 `:1126-1128`）：用 `effective_clue_rect(clue)` 得目标中心，遍历候选用 `ctx[idx]["container_rect"]` 的中心比距离，选最近；`ctx` 无 `container_rect` 时退回原 `_nearest_idx`（visible input / 旧路径行为不变）。
  - **向后兼容**：录制端线索（rect 可能为零、`trigger_affordance.rect` 非零）现在会用上 affordance rect（改进）；全零且无 affordance 的旧线索退回 legacy `_nearest_idx`（含 xpath 首条臂），行为不变。

### 3. `src/tree_walker/tools/actions.py`（`_action_upload_file`，`:1577-1751`）

- **采集时机**：`backend_id` 解析并 log 后（`:1704` 之后）、`before_signals` 探针（`:1708`）之前。此刻封面弹窗 DOM 仍活着（封面上传不跳转），`selector_map`/`file_inputs_meta` 已就绪。
- **对所有 `upload_file` 采集**（非仅多 input 分支）：单 input 页重放时匹配器对唯一候选提前返回，线索无害；统一走稳健重放路径，省分支。best-effort、try/except、debug 日志、失败回退无线索。
- **写回**：成功时 `return ActionResult(..., metadata={"upload_clue": upload_clue})`（`:1751`）；失败分支（`:1584/1589/1594/1664/1690/1722`）保持无线索。`metadata` 不进 `__str__`（`views.py:38-48`），LLM 回显不变。

### 4. `src/tree_walker/agent/step.py`（`_project_interacted_elements`，`:1106-1135`）

- 形参加 `results: list[ActionResult] | None = None`（默认 None = 调用方零行为变化）；调用点 `:1089` 传入 `results`（已在作用域，`:1087`）。
- 循环体：对第 `i` 个动作，若是 `upload_file` 且 `results[i].metadata["upload_clue"]` 存在，则投影为 `{"_semantic_clue": True, "kind": "file_upload", **clue}`（对齐 `recorder.py:353`），跳过原始节点序列化；否则维持原逻辑。`i < len(results)` 防御截断（done 单动作，upload 不会被前置截断）。
- 位置对齐已确认：`step.py:805-916` 在同一 `enumerate(actions)` 循环里按位 append `ActionResult`。

### 5. 测试

- **新建 `tests/test_upload_identity.py`**：`effective_clue_rect` 优先级 / `nonzero_rect` / `build_upload_clue` 形状（含/不含 trigger_affordance）/ `file_input_candidates` kind 过滤。
- **`tests/test_upload_file.py`**：隐藏 input + 多 input 页 → `result.metadata["upload_clue"]["container_rect"]` 非零；采集异常不阻塞上传（仿 `test_verify_exception_does_not_block`，`:873`）；单 input 页仍产出线索（无害）。复用 `_make_state`/`_make_browser(execute_js_side_effect=...)`（`:58-101`，`execute_js` 既有种子，`:97-100`）。
- **`tests/test_rerun_history.py`**：**核心回归**——两候选 xpath **故意不匹配**线索 xpath（漂移）、`region_text` 相同、`in_dialog` 同 True、但 `container_rect` 不同（横 `{x:10}` / 竖 `{x:500}`），线索带 `container_rect:{x:500}` → 断言 `_match_file_upload_by_clue` 返回竖版 idx（证明 rect 就近击败 xpath 漂移 + region 撞车）；录制端线索（`trigger_affordance.rect` 非零）回归；`accept`-only 线索 + 2 候选 → 断言 `_upload_input_contexts` **被调用**（原会跳过）；全零 rect 退回 legacy 不崩。复用 `SimpleNamespace` 节点 + `rm._upload_input_contexts = AsyncMock(...)` 模式（`:853-866`）。
- 既有 `test_match_file_upload_by_clue_single_and_empty`（断言不调 ctx）走的是单候选/空候选提前返回路径，仍绿；既有 `_upload_input_contexts` 对齐测试（`:941-973`）只需给 mock 返回多加 `container_rect: None`。

---

## 关键文件

| 动作 | 路径 |
|---|---|
| 新建 | `src/tree_walker/agent/upload_identity.py`（JS 探针 + 线索构建/采集 + `effective_clue_rect`） |
| 改 | `src/tree_walker/agent/rerun.py`（`:907/:963` 委托；`:1063-1066` 强制 ctx；`:1126-1128` container_rect 就近） |
| 改 | `src/tree_walker/tools/actions.py`（`_action_upload_file` `:1704` 采集 + `:1751` metadata） |
| 改 | `src/tree_walker/agent/step.py`（`_project_interacted_elements` 形参/调用/合并 `:1089/:1106-1135`） |
| 改/新建 | `tests/test_rerun_history.py`、`tests/test_upload_file.py`、`tests/test_upload_identity.py` |

复用既有：`recorder.py:_store_upload_clue`（线索形状范本）、`rerun.py:_nearest_idx`/`_bounds_center`（尾部兜底）、`actions.py:_probe_upload_signals`（execute_js + try/except 范本）、`_make_browser`/`_make_state` 测试夹具。

---

## 验证

1. **单元/重放测试**：`uv run python -m pytest tests/test_upload_identity.py tests/test_upload_file.py tests/test_rerun_history.py -x -v`，再跑全量 `uv run python -m pytest tests/ -x -v`，确认覆盖率 >85%。
2. **重录 skill 文件**（需 Chrome `--remote-debugging-port=9222` + `ZHIPU_API_KEY` + 已登录抖音创作者中心）：
   `uv run python examples/skill/upload_douyin_with_skill.py`
   → `agent.run()`（agent 探索，`_project_interacted_elements` 现在会为每个 upload_file 步写入 `_semantic_clue`）→ `agent.save_history("douyin_upload_skill.json")`。检查新文件第 11/13 步 `interacted_element[0]` 含 `_semantic_clue:true` + 非零 `container_rect`。
3. **重放回归**：`uv run python examples/csv_rerun.py douyin_upload_skill.json <csv>`，多行重放，确认第 11 步稳定传横版、第 13 步稳定传竖版（即便弹窗 `div[12]` 下标漂移），不再偶发选错槽位。
4. **回归底线**：旧的**无线索** agent 历史（如重录前的 `douyin_upload_skill.json`）与录制端线索重放行为与今天完全一致（`interacted_element` 无 `_semantic_clue` → 走原 `_resolve_file_input_by_accept` / xpath 路径）。
