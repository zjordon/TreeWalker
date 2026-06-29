# 抖音封面上传「不支持的图片格式」修复方案（issue #96）

> 本文是 [issue #96](https://github.com/zjordon/TreeWalker/issues/96) 的设计文档。
>
> 上游已落地：[`upload_file_fix.md`](./upload_file_fix.md)（#34：抖音封面无 `<label>`，click dropzone 不触发原生 chooser → 诚实回退 + 防死循环）、[`douyin-cover-upload-50pct.md`](./douyin-cover-upload-50pct.md)（#36：Fix B 中文→ASCII 临时名 + Fix A `file_inputs_meta` 元数据）。本文针对**它们的修复之后仍残留**的根因——与文件名无关、传完后才报错。
>
> 诊断脚本：`examples/debug_cover_input_container.py`（容器结构 + `setFileInputFiles` 反应）、`examples/debug_model_page_view.py`（模型视角的页面结构 `element_tree_text`）。

## Context（为什么做）

抖音创作者中心上传封面时**约 50% 概率**出现「不支持的图片格式」，导致封面上传失败。用户确认两个关键事实，**推翻了 #36 的归因**：

1. **与文件名无关**：中文名、英文名文件**都会报错** → 排除 #36 的 ASCII 文件名正则（Fix B 已在 `session.py:3659` 把中文转 ASCII 临时名）。
2. **传完后才提示**：「不支持的图片格式」是页面**提示信息**（非弹窗），且在**文件传完后**才出现 → 文件确实到达了抖音处理逻辑、但被某个校验拒；区别于 #36 原因 A「set 到失活诱饵 input 的无反应假成功」。

既已修复却仍高频失败，说明是**新根因**。

## 关键发现（实证）

### 1. 封面区有 6 个 file input，其中 4 个 accept 完全相同

`debug_cover_input_container.py` 阶段 1 枚举（某次快照）：

| # | bid | class（模型看不到） | accept | 备注 |
|---|---|---|---|---|
| 0 | 2082 | `upload-btn-input-UY_qeY` | 图片 3 类 | 上传按钮 input |
| 1 | 1766 | `upload-btn-input-UY_qeY` | **视频** | 视频上传 |
| 2 | … | `semi-upload-hidden-input` | 图片 6 类 | 封面组A·初次上传 |
| 3 | … | `semi-upload-hidden-input-replace` | 图片 6 类 | 封面组A·替换 |
| 4 | … | `semi-upload-hidden-input` | 图片 6 类 | 封面组B·初次上传 |
| 5 | … | `semi-upload-hidden-input-replace` | 图片 6 类 | 封面组B·替换 |

→ **4 个封面 input（#2–#5）的 `accept` 完全相同**（`image/png,image/jpeg,image/jpg,image/bmp,image/webp,image/tif`），是 2 组 Semi-UI Upload（横/竖各一对 `hidden-input` + `hidden-input-replace`）。

### 2. 唯一能区分的 class 在序列化时被丢弃（根因）

原始 HTML 里 4 个 input 靠 `class` 区分（`hidden-input`=初次上传 / `-replace`=替换；两组分属横/竖容器）。但发给模型的 `element_tree_text` 里，它们**一字不差**：

```
上传封面
*[2653]<input type=file accept=image/png,...,image/tif autocomplete=off compound_components=(...)/>
*[2654]<input type=file accept=image/png,...,image/tif autocomplete=off compound_components=(...)/>
```

`class` 被 `DEFAULT_INCLUDE_ATTRIBUTES`（`views.py:25`，**不含 class**）白名单过滤掉了 → 模型看到 **4 个一模一样的 input**，无从区分「上传 vs 替换」「横 vs 竖」。

> 对照反例（证明模型有理解力、不是瞎猜）：同页"作品描述"下的 `[1974]<div/>`、`[1978]<div/>` 能被模型识别为可输入框——靠的是 `[index]` 标记（可交互）+ 元素间差异（子内容"30" vs "#添加话题"）+ 语义推理。封面 input 的问题不是模型"看不懂"，而是**序列化把这几个 input 之间的差异抹掉了**，模型"看不到差异"。

### 3. bid 因 React 重建瞬态

同一个"上传封面"input，不同快照的 `backend_node_id`：`2653 → 2118 → 5629 → 6064 → …`（每次枚举都变）。→ 用 bid 做选择本身就不可靠，常 set 到 React 重建后的失活节点。

### 4. LCA 证实"并列 div"结构

阶段 1 的 LCA 表：上传触发器 ↔ 封面 input 的最近公共祖先全是 `<body>`（`✗ 并列/外层汇合`）——印证用户"触发器和 input 在并列的两个 div"的判断，也说明"就近查找"在此页失效。

### 5. 直接 setFileInputFiles 大多假成功

阶段 2 对 4 个封面 bid 逐个 `setFileInputFiles`：**既没出现「不支持格式」，也没出现预览图**（检测到的 `semi-select select-collection` 是页面**常驻**的"从收藏选择封面"区，检测脚本未对比 baseline 造成误判，已作废）。真实情况：枚举到的 bid 多是 React 重建后的失活节点，set 上去抖音不处理。这恰恰说明：**问题不在"打到了某个会弹框的 input"，而在"模型根本分不清该选哪个 + bid 瞬态"**。

### 6. [File Inputs] 段也无 class（#36 Fix A 的盲点）

`FileInputInfo`（`views.py:678`）只有 `backend_node_id / accept / visible / upload_ancestor`，**没 class**。`system_prompt.py:179-183` 的 [File Inputs] 段也就无法告知模型"哪个是上传、哪个是替换"。→ 即便 #36 加了这段引导，模型面对 4 个相同 accept 的 input **依然无法区分**。

> 注：`highlight_index = backend_node_id`（`serializer.py:724`），两者同值 → [File Inputs] 段的 bid 与 `element_tree_text` 的 [index] 一致，**不存在 key 串号**（曾怀疑，已排除）。

## 方案（三层，互补）

### Fix A（核心·根因层）：序列化时给 file input 保留 class

让模型在 `element_tree_text` 里**看见** `hidden-input` vs `-replace` 的差异——直接消除"4 个一模一样"的盲选。

- **文件**：`src/tree_walker/browser/serializer.py`（**空格缩进**）· 函数 `_build_attributes_string`（line 934）
- **关键约束**：Step 8 格式化循环（line 1101+）**只遍历 `include_attributes` 白名单**，class 不在白名单 → 不能只塞 `attrs_to_include`，必须把 class 加进**本次调用的 `include_attributes` 局部副本**。
- **改法**：在 Step 2（line 962 `if node.tag_name == 'input' and node.attributes:` 块内）的 input-type 分支加 `elif input_type == 'file':`：

  ```python
  elif input_type == 'file':
      cls = node.attributes.get('class', '').strip()
      if cls:
          if 'class' not in include_attributes:
              include_attributes = [*include_attributes, 'class']  # 局部副本，不污染调用方
          attrs_to_include['class'] = cls
  ```

  与既有 date/tel/text 分支同模式；`include_attributes = [...]` 是局部重新赋值，不影响调用方 `DEFAULT_INCLUDE_ATTRIBUTES`。
- **不动** `DEFAULT_INCLUDE_ATTRIBUTES`（避免全局给所有元素加 class 涨 token；file input 全站很少，token 影响可忽略）。

### Fix B（增强·结构化）：FileInputInfo 加 class_name + [File Inputs] 段输出

让模型在 [File Inputs] 段（`element_tree_text` 之外的第二处信号）也看到每个 input 的 class。

- `src/tree_walker/browser/views.py`（**tab 缩进**）：`FileInputInfo`（line 678）加字段 `class_name: str = ""`（放 `upload_ancestor` 后，带默认值字段居尾）。
- `src/tree_walker/browser/dom.py`（**tab 缩进**）：`_collect_file_inputs`（line 518-523）构造 FileInputInfo 时加 `class_name=node_attrs.get('class', '')`（`_parse_attrs` line 513 已解析全部属性含 class）。
- `src/tree_walker/prompts/system_prompt.py`（**空格缩进**）：[File Inputs] 段（line 179-183）每行追加 `cls = f" class={fi.class_name}" if fi.class_name else ""` 并拼进输出。

### Fix C（行为兜底）：actions 层优先 hidden-input

不靠模型猜，replace 误选时软纠正到初次上传 input。

- **文件**：`src/tree_walker/tools/actions.py`（**空格缩进**）· `_action_upload_file` 分支 A（line 1496-1517）
- **三条件护栏**（全部满足才纠正，防误伤其他多 input 站点）：
  1. agent 选的 input `class` 含 `replace`；
  2. 存在 `class` 含 `hidden-input`（非 replace）的 primary 候选；
  3. **≥2 个 file input 共享 upload 祖先**（`sum(fi.upload_ancestor) >= 2`，复用 `dom.py:525` 已算）——关键护栏：抖音 4 input 全在 semi-upload 容器满足；普通站点"替换头像"通常单 input 不满足，不误触发。
- 满足 → 改选 primary hidden-input + note 提示（**不硬拒绝**，避免 #36 的 0% 回归）；不满足 → 保持原软警告逻辑。
- 用 `_find_node_by_backend_id`（`actions.py:780`）从**同一快照** selector_map 读候选 class（同快照无失活）；候选找不到（None）→ 不纠正、回退软警告。
- **横/竖槽位**：Fix C 只保证"上传 vs 替换"正确；横/竖交给 Fix A/B（模型读 class + 兄弟文本"横向/竖向封面"判断位置）。可选 best-effort `_pick_slot_match`（DOM 邻近度选最近 primary），但 note 永远提示"槽位可能错，请按上传区位置重试"，**不在 actions.py 硬编码 DOM 启发式**。

## 需要改动的文件

| 文件 | Fix | 缩进 | 改动点 |
|---|---|---|---|
| `src/tree_walker/browser/serializer.py` | A | 空格 | `_build_attributes_string` Step 2 加 `file` 分支保留 class |
| `src/tree_walker/browser/views.py` | B | tab | `FileInputInfo` 加 `class_name` 字段 |
| `src/tree_walker/browser/dom.py` | B | tab | `_collect_file_inputs` 收集 `class_name` |
| `src/tree_walker/prompts/system_prompt.py` | B | 空格 | [File Inputs] 段输出 class |
| `src/tree_walker/tools/actions.py` | C | 空格 | `_action_upload_file` 分支 A 三条件护栏软纠正 |

## 测试（参照现有模式，不新造 helper）

- **Fix A** → `tests/test_serializer.py::TestSerializeTreeTextOutput`（tab）：file input 带 class 输出；两个 input class 值**不同**（#96 核心断言）；无 class 不输出；`<input type=text>` 不泄漏 class；`include_attributes` 调用前后不变。
- **Fix B** → `tests/test_dom_building.py::TestCollectFileInputs`（tab，`class_name` 收集）+ `tests/test_system_prompt.py::TestFileInputsSection`（空格，段输出 class）。
- **Fix C** → `tests/test_upload_file.py`（tab，`_make_entry`/`_make_state`/`file_inputs_meta`，参照 `test_file_input_target_among_many_names_live_candidates`）：replace→primary 纠正；无 primary 候选 / 单 upload_ancestor 不纠正（护栏）；primary 不纠正；既有 `test_file_input_target_among_many_*` 回归保护。

## 验证

1. 单测（每改一文件跑相关子集）：
   - `uv run python -m pytest tests/test_serializer.py tests/test_dom_building.py tests/test_system_prompt.py tests/test_upload_file.py -x -v`
2. 全量 + 覆盖率（目标 >85%）：
   - `uv run python -m pytest tests/ -x --cov=tree_walker --cov-report=term-missing`
3. 诊断（Chrome `--remote-debugging-port=9222` 停抖音封面区）：
   - `uv run python examples/debug_model_page_view.py` → 核对 [3] `element_tree_text` 的 `<input type=file>` 行**带 class**（Fix A 前不带，#96 盲选根因反证）；[4] [File Inputs] 段每行带 class（Fix B）。
4. 真实抖音：跑上传封面任务，确认模型读 class 区分 primary/replace、Fix C 软纠正成功（不再「不支持格式」）、横/竖正确或模型按 note 重试。

## 风险与回归

- **Fix C 改"信任 agent index"行为** → 三条件护栏 + 软纠正（不硬拒绝）+ `node is None` 防御；条件 3（≥2 upload 祖先）是防误伤其他站点的关键。
- **bid 瞬态**：同一快照内 entry 与候选一致；`set_file_input` 失活会抛错被 `actions.py:1571` 捕获，建议 note 提示"候选可能失效，请 get_state 重试"。
- **横/竖**最终靠模型读 Fix A/B 暴露的 class + 位置；不硬编码 DOM 启发式（脆且不可维护）。
- **Fix A token**：仅 file input 带 class（全站很少），单元素 ~28 token，可忽略；不动 `DEFAULT_INCLUDE_ATTRIBUTES`，对非 file input 零影响。
- **docstring 同步**：`serializer.py:939-950`（Step 2 加 file）、`dom.py:507-511`（加 class_name）。
- 不触碰 git；改完跑测试即止（按 CLAUDE.md，不主动 commit）。
