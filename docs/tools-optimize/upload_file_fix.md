# upload_file 回归修复方案 v2（实证推翻首版前提后的重做）

> 本文是 [issue #34](https://github.com/zjordon/TreeWalker/issues/34) 的**第二版**设计文档。
>
> **v1（已作废）** 的前提是"browser-use 用 file chooser 拦截 → 我也照做拦截+discover"。经实证（通读 browser-use 源码）**该前提错误**：browser-use 根本不拦截、不 discover。v1 的改动（拦截 + `discover_file_input_via_click` + 非 file-input 分支诚实回退 + 删除 `_pick_nearest_file_input`）**已实施未提交**，实测让抖音封面**更糟**（agent 卡死循环）。本文更正根因并重做。
>
> **状态（2026-06-20 最终）**：根因已坐实并经多轮实证（含 trusted CDP click）。**结论：抖音封面不可经 CDP 自动上传**（Semi-UI 上传组件无 `<label>`、drag-area 的 JS `input.click()` 即便 trusted 点击也不触发原生选择器）。已实施修复 = **破除死循环 + 防收藏弹窗 + label-click chooser-capture（对有 label 的上传有效）+ 诚实回退**；B站/普通站点/有 label 的上传均工作；抖音封面诚实失败（需手动）。894 测试通过。

## Context（为什么重做）

`c91b78f`（2026-06-19，给 `upload_file` 加"成功回显 + 目标替换提示 + accept 软校验"）之后，真实站点两个回归：

1. **B 站上传文件频繁弹出 OS 原生文件选择框**。
2. **抖音竖封面.png 反复失败"不支持的图片格式"，竖封面被上传多次**。

v1 修复后用户实测反馈：**抖音封面反而更糟**——日志显示 agent 进入封面编辑弹窗后，对隐藏 input `[7478/8232/8250/8261/7479/7480]` 反复 `setFileInputFiles`，全部"未能触发编辑器上传处理器"，弹窗不断重建 file input，agent 卡死循环 6 次后放弃。

## 关键发现（本轮静态分析 + 对 browser-use 源码实证）

### 1. browser-use 根本不用 file chooser 拦截（v1 前提错误）

Explore agent 通读 `z_jordon/browser-use` 与 `ai-browser/browser-use` 两棵树，对 `setInterceptFileChooserDialog` / `fileChooserOpened` **零命中**。browser-use 的上传是**单一路径**：

- 找一个现成 `<input type=file>` → `DOM.setFileInputFiles` → 结束。
- **不 dispatch 额外事件、不 click 触发、不拦截 chooser**。
- 且**硬禁止 click file input**（`default_action_watchdog.py:715-723` 直接返回 validation_error）。

→ v1"参照 browser-use 做拦截+discover"是臆想，不是 browser-use 的模型。

### 2. 隐藏 file input 被序列化器强制可见并分配 index

`src/tree_walker/browser/serializer.py:216-223`：

```python
# 强制可见：隐藏的 file input (Bootstrap opacity:0 模式)
is_file_input = (tag_lower == 'input' and node.attributes
                 and node.attributes.get('type') == 'file')
if not is_visible and is_file_input:
    is_visible = True
```

→ 抖音弹窗内的隐藏 file input 直接进 `selector_map`，agent 能按 index 直接 `upload_file` 命中它们 → 走 `is_file_input=True` 分支 → `set_file_input(entry.backend_node_id)` 直传。**这是日志里 agent 直接打 `[7478…]` 的根因。**

### 3. 抖音封面 input 在交互间被 React 重建

日志 backendNodeId 从 `7478→8232→8250→8261` 单调攀升 = React 按需 unmount/remount。agent 看到的持久 input 要么无监听器、要么在 setFileInputFiles 落地前已被替换 → change 事件打在失活节点 → 编辑器忽略。

### 4. upload_file 无效也报成功（死循环放大器）

`src/tree_walker/tools/actions.py:1015` 无条件 `extracted_content=memory`。agent 无法判断"没生效"，只能换 index 重试 → **放大成日志里的 6 次死循环**。

### 5. 三阶段失败模式（解释"参照前没问题、我的改动更糟"）

| 阶段 | setFileInputFiles 触发抖音处理器？ | 结果 |
|---|---|---|
| **模式 B**（c91b78f 后，原 Bug2）：`_pick_nearest_file_input` 恒取 `file_input_ids[0]` | ✅ 触发了（"不支持的图片格式"=文件到达抖音处理逻辑） | 槽位错 → 反传 |
| **模式 C**（v1 改动后，当前日志）：discover+诚实回退把 agent 逼进弹窗，弹窗内隐藏 input 被强制可见可直传 | ❌ 全不触发（瞬态/失活 input） | agent 卡死循环 |

**结论：问题不是 setFileInputFiles"不能用"，而是 v1 改动让 agent 找不到那个能用的 input。** 模式 B 证明存在能触发处理器的 input；Phase 0 要找出是哪一个。

## Phase Pre — 设计文档先行（已完成）

本文即设计交付物。改任何源码前先落本文（沿用 `docs/tools-optimize/` 每项工具改动一份文档的惯例）。

## Phase 0 — 实证诊断（先于改码）

**唯一目的：找出"抖音封面页哪个 input 的 setFileInputFiles 能触发处理器"。** 已在 v1 假设上栽过一次，这次绝不猜。

新增 `examples/debug_cover_input_live.py`（用户保持抖音停在"封面选择/编辑"状态，我跑；只读为主，最多触发一次本地上传、**不点完成/发布**）：

1. 连已登录 Chrome（9222，拦截已 ON）。枚举全部 file input：`backendNodeId / accept / bounds / is_visible`。
2. 对每个 input：`setFileInputFiles(竖封面.png)` → 2s 内探测抖音是否反应（预览/缩略图出现、特定 class 变化、`input.files.length`、或"完成"按钮可点击态变化）。逐个记 yes/no。
3. 找弹窗内"上传图片/本地上传"按钮 → click（拦截 ON）→ 捕获 `fileChooserOpened.backendNodeId` → 立即 setFileInputFiles → 同样探测反应。
4. 输出结论表。

> **实证结论（2026-06-20，抖音封面编辑器实跑 4 个探针）：**
>
> 1. **`setFileInputFiles` 对横封面 input 有效** —— `bid=8798`（`accept=image/png,image/jpeg,image/jpg`，3 类）直传后 **+2 张 blob 预览图**，横封面槽出现封面。**机制本身对横封面可用。**
> 2. **竖封面不可达** —— 枚举到的「竖」input（`9612/9613`，`accept` 6 类）直传**全无反应**，竖封面槽始终空（"点击上传"）。竖封面的真实上传 input 不在静态枚举内 / 需前置交互，且其触发器难以稳定定位（探针 4 在 selector_map 中连"上传"文本元素都搜不到，说明它非交互或被过滤）。
> 3. **chooser-capture 在抖音彻底不可行** —— 5 个可点击上传触发器（2 个 `semi-upload-drag-area` + 3 个 `semi-button-primary`）+ 顶层 dropzone（v1 探针）**全部不触发 `fileChooserOpened`**。原因：Semi-UI Upload 的程序化点击丢失了"打开原生选择器所需的 user-gesture"，native picker 根本不开 → 无事件可捕获（不是被拦截）。
> 4. **input 瞬态** —— 用后即被 React 重建，backendNodeId 不稳定。
>
> **→ v1 的"chooser-capture + 诚实回退"前提被实证推翻**：chooser 永不触发 → 永远落到诚实回退 → 把 agent 逼进弹窗死循环（即用户反馈的"更糟"）。修复必须走「识别正确 input 直传 + 无效检测防死循环」，**竖封面列为已知需手动**。

## Phase 1 — 修复（依 Phase 0 结果定形）

### 主分支：若 Phase 0 证明"click→fileChooserOpened→setFileInputFiles 能触发"

（拦截的正确用法——browser-use 缺、我们补的能力）

- `actions.py _action_upload_file`：目标是**隐藏 file input 且页面有多个 file input**（抖音签名）时，**不**直传该 input；改为定位最近可点击上传触发器（按钮/label）→ click → 取 `fileChooserOpened.backendNodeId`（拦截已 ON）→ `set_file_input` 该瞬态 input。
- 保留拦截（B 站已受益）。诚实回退仅在"click 既不开 chooser 又不开有用弹窗"时触发。

### 兜底分支：若 Phase 0 证明"setFileInputFiles 对抖音封面 input 一概不触发"（React 事件缺口）

- (a) setFileInputFiles 后补发合成事件（移植 browser-use `_trigger_framework_events`/`_set_value_directly` 的 React 感知逻辑到 file 路径：dispatch `input`/`change` `bubbles:true`，必要时 force `isTrusted`）；或
- (b) 承认该 uploader 不可自动化：不直传隐藏 input，诚实回退"此封面编辑器不支持自动上传，请手动"——**绝不在无效时回报成功**。

### 必做安全网（无论哪个分支）

- **修"无效也报成功"**：`_action_upload_file` 的 `is_file_input=True` 且多隐藏 input 场景，不再无条件直传 + 报成功；改为走触发器→discover，未命中/无效时回可操作提示（**回归守卫：禁止 agent 死循环换 index**）。
- **重评"强制可见隐藏 file input"**（`serializer.py:216-223`）：考虑仅在"页面唯一 file input"时强制可见、多 input 时不给可直传 index（逼 agent 走触发器→discover）；需实测防回归 Bootstrap opacity:0 站点。

## Phase 2 — 测试

- 复用 `test_upload_file.py` / `test_browser_session.py`。
- 新增回归守卫：多隐藏 input 场景，`upload_file` 走触发器→discover（mock `fileChooserOpened`）命中正确瞬态 input；未命中/无效时**不报成功**且回可操作提示、不退回盲选首个。
- `uv run python -m pytest tests/ -x -v` 全过，覆盖率 >85%。

## Phase 3 — 文档同步

- 本文已落（Phase Pre）。Phase 0 后回填实证结论与最终代码细节。
- 同步 `docs/Tools技术细节/04_动作清单与CDP映射.md`。

## 验证

1. 单测全过。
2. 用户在抖音封面页实证：竖封面一次成功、agent 不再死循环换 index。
3. B 站不回归（仍不弹原生框）。

## 关键文件

- `src/tree_walker/browser/session.py`（`set_file_input`、`discover_file_input_via_click`、`_on_file_chooser_opened`、`_enable_file_chooser_intercept`）
- `src/tree_walker/tools/actions.py`（`_action_upload_file` 各分支 + "无效也报成功"修复，当前 `actions.py:899-1015`）
- `src/tree_walker/browser/serializer.py:216-223`（隐藏 file input 强制可见——依 Phase 0 重评）
- `examples/debug_cover_input_live.py`（Phase 0 新增诊断）
- `tests/test_upload_file.py`、`tests/test_browser_session.py`

---

## 最终结论与已实施修复（2026-06-20）

### 抖音封面为何不可 CDP 自动上传（多轮实证坐实）

| 路径 | 结果 | 证据 |
|---|---|---|
| `DOM.setFileInputFiles` 打静态 input | ❌ 命中"收藏封面"input → 弹收藏框 | `15202`(3 类 accept)直传 → 7 个 `semi-modal-*` 新增 |
| 点击 drag-area / 按钮（Semi-UI 的 JS `input.click()`） | ❌ 不触发 `fileChooserOpened` | 5 个触发器全不触发 |
| 点击 `<label for=input>` | ✅ 触发 chooser | **但封面组件无 `<label>`**（只有 video 有） |
| **trusted** CDP `click_at` 打封面 drag-area + 已选"上传新封面" | ❌ 仍不触发 chooser | `click_at(960,657)` → `_last_file_chooser=None` |

**根因**：抖音封面用 Semi-UI Upload，组件结构 = 隐藏 input + drag-area，**无 `<label>`**。drag-area 的点击由 JS 调 `input.click()`，该调用在 CDP 下（即便 trusted `Input.dispatchMouseEvent`）不满足 Chrome 打开原生选择器所需的 user-activation；而 `<label>` 的点击是浏览器原生行为，能保留 activation（故 video 那个有 label 的上传可触发 chooser）。**这是 抖音/Semi-UI/Chrome 的限制，非 TreeWalker bug。** file input 的 `accept` 在重渲染间还不稳定（3 类↔6 类变化），无法用作区分信号。

### 已实施修复（`actions.py` + `session.py`）

1. **`_find_upload_label_near`**（`actions.py` 新增）：从目标上溯找最近的 `<label class*="upload">`（选择文件/上传）。`<label>` 原生点击能触发 `fileChooserOpened`（drag-area/div/button 的 JS 点击不行）——issue #34 的机制关键。
2. **`_action_upload_file` 分支重写**：
   - **is_file_input 且页面有多个 file input** → **拒绝直传**（诚实 error，引导改点上传按钮）。避免直传命中抖音"收藏封面"等无关 input → 不再弹收藏框、不再死循环。
   - **非 file input + 多 input** → `_find_upload_label_near` 找 label → `discover_file_input_via_click(label)` → chooser 命中则传到页面打开的 input；无 label 或 chooser 未开 → 诚实 error。
   - **唯一 input** → 直传（普通站点）。
3. **保留**：`setInterceptFileChooserDialog` 拦截（B站 Bug1 修复 + chooser-capture 前提）、`discover_file_input_via_click`、accept 软校验中性文案。

### 各场景效果

- ✅ **B站**：拦截兜底，不弹原生框（Bug1 修复保留）。
- ✅ **普通站点（单 file input）**：直传，行为不变。
- ✅ **有 `<label>` 的上传（含抖音 video 上传）**：label-click chooser-capture 命中正确 input。
- ⚠️ **抖音封面（横/竖）**：诚实失败（清晰 error，**不再死循环、不再弹收藏框**），需手动上传——这是平台限制，工具不假装成功。

### 测试

`uv run python -m pytest tests/ -q` → **894 passed**。新增：`TestFindUploadLabelNear`（label 定位单元测试）、is_file_input+多 input 拒绝直传、label-near→discover 用 label bid、单 input 直传不回归。
