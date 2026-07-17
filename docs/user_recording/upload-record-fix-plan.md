# 修复用户录制：upload_file 的正确捕获（修订版——不动 dom.py）

> 本文是针对手工录制 upload_file 问题的**实施方案（修订版，待 review）**。
> 对照样本：agent 自动录制 `douyin_upload_history.json`（正确）vs 手工录制 `douyin_final_1.json`（有问题）。
>
> **修订说明**：上一版方案曾提议改 `dom.py`（给 `FileInputInfo` 加 xpath）来解决「横/竖封面指纹相同」——经核查这是**误判**，已撤销。本文为修订后版本。

---

## Context（为什么做 + 上一版的修正）

手工录制对比 agent 自动录制暴露 3 个疑点。**经代码核查修正了认知**：

- **关键事实1**：`<input type=file>`（含隐藏 1×1）**本来就在 `selector_map` 里**（`src/tree_walker/browser/serializer.py:766-773`，`is_file_input` 覆盖可见性判定）。之前以为不在，错了。
- **关键事实2**：agent 录制的两个封面上传 **hash 完全相同**（都 `8015197448111531807`），却能正确重放——靠的是 `_nearest_idx` 的 **xpath tie-break**（`src/tree_walker/agent/rerun.py:115`，EXACT hash 碰撞时按录制 xpath 唯一命中区分 `div[2]` vs `div[3]`）。**所以 hash 碰撞不是问题，已被现有机制解决。**
- **结论**：上一版「给 `FileInputInfo` 加 xpath / 改 `dom.py`」是**解决不存在的问题**，且会动到 agent 赖以工作的 file-input 收集逻辑，有回归风险。**撤销该改动。**

真正要修的只有 2 个录制端问题（都不动 `dom.py`、不动重放匹配）：

1. **upload_file 后无 wait**：agent 每个 upload 后有 `wait`（视频 5s / 封面 3s），手工录制一个都没有 → 上传后页面没处理完就进下一步（封面编辑器打不开的时序根因）。
2. **视频上传录到 image input**：视频 input 在 selector_map 里，但扩展发的 xpath（content script 活 DOM）与 CDP selector_map 的 xpath **不一致**（深层动态节点常见）→ `locate_by_xpath` 失败 → file-input 兜底取 selector_map 迭代序第一个 file input = 封面 image input（accept=image/*）→ 录错。**正解：按 accept（文件类型）定位**——这正是 agent 解析 file input 的方式，且 accept 稳定、不受 xpath 漂移影响。

**问题3（横/竖封面 hash 相同）撤销**：非问题，xpath tie-break 已解决，agent 同样如此。

---

## 设计原则

**upload_file 录制时按 accept（文件类型）在 `selector_map` 里选 file input**（file input 本就在 selector_map），不再依赖会漂移的 xpath、也不再用「第一个 file input」兜底。这是 agent `_action_upload_file` 的同款思路，**仅改录制端，零重放改动、零 dom.py 改动**。

---

## 改动（全部在录制端）

### 1. `src/tree_walker/recorder/recorder.py`：upload_file 按 accept 定位 + 追加 wait
为 `upload_file` 走专用解析（**不走** `locate_by_ref` 的 xpath 路径、不走「第一个 file input」兜底）：
- 从 `action.params["path"]` 算文件类型（mp4→video，png→image；复用现有 `_file_kind`）。
- 在 `selector_map` 里筛 `<input type=file>`，按 **accept 含文件类型** 选（video→accept 含 `video`，image→accept 含 `image`）——修问题2（视频 input，非 image）。
- 同 accept 多个（横/竖封面）时：用扩展事件的 xpath（normalize 后）在候选里唯一命中区分（xpath 在封面这对是稳的——agent 重放就靠它）；仍无法区分则取 `visible` + `upload_ancestor` 优先。
- 用命中节点的真实指纹（`DOMInteractedElement.load_from_enhanced_dom_tree`）落 `interacted_element`——accept / xpath / hash 都正确。
- **追加 wait**：upload_file 处理完后，紧接着 append 一条 `wait` ActionRecord（视频 5s / 图片 3s，对齐 agent）。`map_event` 不认 wait，直接构造 `ActionRecord(action_name="wait", params={"seconds": N})`（`wait` action 后端已存在：`tools/models.py:571` + `tools/actions.py:1226`）。

### 2. 清理上一轮的 upload 兜底（现在多余）
- `recorder.py`：删 `_locate_with_upload_fallback` 里的「selector_map 第一个 file input」兜底（被 accept 定位取代）；upload_file 不再走 `locate_by_ref`、不再走定位重试（重试只留给 click / input_text）。
- `recorder/locator.py`：`locate_by_ref` 的 `use_rect_fallback` 参数及 upload_file 特判可简化（upload_file 不再调用 `locate_by_ref`）；RECT 兜底保留给普通 click。
- `agent/rerun.py`：删上一轮加的 `_find_file_input_index` + 两个 upload_file selector_map 兜底分支（录制正确后，file input 在 selector_map 里、指纹正确，正常 EXACT / XPATH 匹配即可，无需兜底）。**保留** `_nearest_idx` 的 xpath tie-break（解决封面 hash 碰撞，agent 也靠它）。
- 保留：RECT 兜底（无属性 click 触发器如 `cover-Jg3T4p`）、locate 重试（click / input）、`_nearest_idx` xpath tie-break。

---

## 不改 / 复用
- **`dom.py` / `FileInputInfo` 不动**（撤销上一版方案）——不碰 agent 的 file-input 收集，零回归风险。
- **重放端匹配不动**：file input 在 selector_map、录制指纹正确 → 正常匹配 + xpath tie-break 处理碰撞。`_action_upload_file` 的多 input 纠正（Fix C）保留。
- **扩展端不动**（onFileChange 已发 xpath）。
- `wait` action 后端已就绪，录制端产 `wait` 步即可。

---

## 验证
1. **单测**（`tests/test_recorder*.py`）：
   - upload_file 按 accept 定位：mock selector_map 含 video input(accept=video/*) + image input(accept=image/*) + 两个 image cover input(div[2]/div[3])，传 mp4 → 命中 video input、interacted_element.accept 含 video；传 png + xpath=div[3] → 命中 div[3] 那个。
   - upload_file 后自动追加 wait（视频 5s / 图片 3s）。
   - 删除/更新被删兜底函数的旧测试（`_find_file_input_index` 等）。
2. **全量回归** `uv run python -m pytest tests/ -q` 全绿（重点：rerun_history 的 upload 测试改用「录制正确→正常匹配」路径）。
3. **真机**（用户重录，扩展无需 rebuild）：检查新录制文件：①每个 upload_file 后有 wait；②视频 upload 的 interacted_element 是 accept=video/* 的 input（而非 image）；③横/竖封面两个 upload 的 x_path 不同（div[2] vs div[3]，hash 可同——靠 xpath tie-break 区分）。重放：视频传成功→（wait 给足处理时间）封面编辑器打开→横/竖封面分别传对。

---

## 实施顺序（review 通过后）
1. `recorder.py`：upload_file 专用 accept 定位 + 追加 wait。
2. 清理 `_locate_with_upload_fallback` / `locate_by_ref` 的 upload 特判、`rerun.py` 的 `_find_file_input_index` + upload 兜底分支。
3. 单测 + 全量回归。
