# upload_file accept 定位问题：根因分析与修复总结

> 本文记录用户录制抖音上传流程中，视频 upload_file 反复录到错误 file input（image input）
> 的根因分析过程及最终修复方案。前序方案见 `upload-record-fix-plan.md`。

---

## 问题现象

用户多次重录，step 3（视频上传）的 `interacted_element` 始终是 `accept='image/png,image/jpeg'`
的 image input（`upload-btn-input-UY_qeY`），而非 `accept='video/*'` 的 video input。
重放时该 image input 指纹在视频上传页匹配不上 → 视频没传 → 页面不跳转 → 后续步骤全废。

---

## 根因（经代码核查 + 真机验证确认）

### 1. file input 在录制 `get_state` 瞬间是**瞬态**的

通过真机 CDP 检查确认（`/content/upload` 页稳定态）：
- selector_map 有 **1 个 file input**：`accept='video/x-flv,video/mp4,...,video/*'`（video input）。
- file_inputs_meta 同样只有这 1 个。

但在**录制瞬间**（用户刚选完文件、扩展发 upload_file 事件到后端、后端 `get_state`），video input
**还没渲染进 selector_map**——此时 selector_map 里只有 `accept='image/...'` 的 image input
（可能是页面另一个用途的 file input，或过渡态残留）。

### 2. 旧 `_locate_upload_file` 的 accept 过滤 + fallback 逻辑导致录错

```
file_inputs = [所有 selector_map 里的 file input]  # 此时只有 image input
→ accept 过滤 video → 空（没有 accept 含 video 的 input）
→ fallback: 取 file_inputs[0]  ← image input（错的！）
```

accept 过滤正确地发现没有 video input，但 fallback 取了第一个（image input），录到了错指纹。

### 3. 重试（0.6s + 1.5s）也救不回

video input 在 2.1s 内未渲染进 selector_map（渲染时机不可控），重试全部失败。

### 4. 关键认知：file input 录制 vs 重放的不对称

| | 录制时 | 重放时 |
|---|---|---|
| 页面状态 | 过渡态（刚选完文件，video input 未渲染） | 稳定态（video input 已在 selector_map） |
| file input | 只有 image input（accept=image） | 只有 video input（accept=video） |

**录制端无法保证捕获正确的 file input**（瞬态），但**重放端页面稳定时可以按 accept 解析**。

---

## 修复方案

### 录制端（`recorder.py` `_locate_upload_file`）

**accept 不匹配时不再 fallback 取第一个**，返回 None（诚实——不录错指纹）：

```python
# 旧逻辑（有 bug）：
if typed:                          # accept 匹配的候选
    file_inputs = typed
return file_inputs[0]              # ← 匹配为空时取了 ALL file_inputs 的第一个 = image input（错！）

# 新逻辑（修复）：
if not typed:
    return None                    # ← accept 不匹配 → 不录错，留给重放端按 accept 解析
file_inputs = typed
```

效果：video input 瞬态不在 selector_map → accept 过滤空 → 返回 None → 录制无指纹（诚实）。
重试（0.6s + 1.5s）仍会尝试，但如果 video input 始终不在，最终无指纹。

### 重放端（`rerun.py` `_resolve_file_input_by_accept`）

新增方法：upload_file **无指纹或匹配失败时**，按文件类型（mp4→video、png→image）从当前页
selector_map 按 accept 解析 file input 的 index。

```python
def _resolve_file_input_by_accept(self, state, path):
    """按 accept(文件类型) 从当前页 selector_map 解析 file input。"""
    ext = Path(path).suffix  # mp4 → video, png → image
    kind = "video" if ext in _UPLOAD_VIDEO_EXTS else "image" if ext in _UPLOAD_IMAGE_EXTS else None
    for idx, node in selector_map.items():
        if node is INPUT type=file and (kind is None or kind in node.accept):
            return idx
    return None
```

在 `_execute_history_step` 的两条路径中接入：
1. **有指纹但匹配失败**：`_update_action_indices` 返回 None → `_resolve_file_input_by_accept` 兜底。
2. **无指纹**（interacted_element=null）：直接 `_resolve_file_input_by_accept`。

效果：重放时页面稳定，video input（accept=video/*）在 selector_map → mp4 → kind=video → 命中。

### 为什么这不是「兜底」而是「正确机制」

file input 在录制时是瞬态的（渲染时机不可控），录制端**物理上无法保证**捕获正确 input。
重放端页面稳定时按 accept 解析是**唯一可靠**的定位方式。这与 agent 的 `_action_upload_file`
用 `file_inputs_meta` 在 action 执行时解析 file input 是同款思路——file input 天生需要
在**使用时**（而非录制时）定位。

---

## 其他改动（本轮一并完成）

### upload_file 后自动追加 wait

`recorder.py` handle_event：每个 upload_file 处理完后追加 `wait` ActionRecord（视频 5s / 图片 3s），
对齐 agent 录制经验值。解决上传后页面没处理完就进下一步（如封面编辑器打不开）的时序问题。
`wait` action 后端已存在（`tools/models.py` WaitParams + `tools/actions.py` _action_wait）。

### 定位重试改为 2 档（0.6s + 1.5s）

`_LOCATE_RETRY_DELAYS = (0.6, 1.5)`：统一 upload_file 和 click/input 的重试逻辑，给页面更多
渲染/动画收尾时间。覆盖「file input 瞬态」和「modal 后渲染按钮（如暂存离开）」两类场景。

---

## 代码改动清单

| 文件 | 改动 |
|---|---|
| `src/tree_walker/recorder/recorder.py` | `_locate_upload_file`：accept 不匹配返 None（不 fallback）；upload_file 后追加 wait；统一 2 档重试 |
| `src/tree_walker/agent/rerun.py` | 新增 `_resolve_file_input_by_accept` 方法 + `_UPLOAD_VIDEO_EXTS`/`_UPLOAD_IMAGE_EXTS`；两条 upload_file 解析路径（匹配失败 + 无指纹） |
| `tests/test_recorder.py` | 更新 upload_file 测试（accept 定位/无 file input 返 None/wait 追加/重试 2 档） |
| `tests/test_rerun_history.py` | 新增 `test_rerun_upload_file_no_fingerprint_resolves_by_accept`（无指纹→accept 解析） |

---

## 验证

- 全量 1958 passed，recorder 包 92% 覆盖。
- **现有 `douyin_redesign3.json` 可直接重放**（重放侧已修）：step 3 的 image input 指纹匹配失败
  → 按 accept=video 解析 → 命中 video input → 视频传成功。
- 重录也 OK：step 3 会录到无指纹（不再录错），重放仍按 accept 解析。

---

## 教训

1. **不要假设 file input 在录制时就在 selector_map**——它是瞬态的，渲染时机不可控。
2. **accept 过滤后不应 fallback 取第一个**——那会把错误类型的 input 当正确的录。
3. **file input 的正确定位时机是「使用时」（重放/执行），不是「录制时」**——这与 agent 的
   `_action_upload_file` 用 `file_inputs_meta` 在执行时解析一致。
4. **录制端诚实（无指纹）比录错指纹更好**——重放端有可靠机制（accept 解析）兜住。

---

## 更正：之前「按钮动态创建 file input」的理论是错的（2026-07-15 用 CDP 实测推翻）

> 上文（2026-07-13 初版）曾推断「上传视频」按钮动态创建临时 `accept=image` 的 file input，
> 不用 HTML 静态那个。**这个推断是错的**——是在没有抖音 JS 源码时的猜测。下面用真机 CDP
> 注入 hook 实测的结论取代它。

### 实测：按钮就是用静态 video input（accept=video/*）

用 debug 模式 Chrome + CDP `Runtime.evaluate` 注入三套 hook，程序化点「上传视频」按钮
（`examples/debug_upload_btn.py` / `debug_upload_click.py`）：

| hook | 结果 |
|---|---|
| `HTMLInputElement.prototype.click` | **命中 2 次**，全是静态 video input（`accept=video/*`，class 空，1×1） |
| `MutationObserver`（新建 input） | **0 个**——按钮没新建任何 input |
| `change` | 0（程序化点击无真实文件） |

`.click()` 打中的 input xpath `.../div[2]/input` 正是按钮 `.../div[4]/button` 的**兄弟节点**
（同一个 `div[2]` = `container-drag-VAfIfu` 拖拽区）。**抖音按钮就是 `videoInput.click()`，没有动态创建。**

`upload-btn-input-UY_qeY`（accept=image）这个 class 命名跟视频页的 `container-drag-*` **完全不是一套组件**，
它来自**发布编辑页 `/content/post/video`**，不是视频上传按钮动态建的。

### 真正的根因：导航竞态（navigation race）

dump 两个录制文件每步的 URL（`examples/debug_dump_flow.py`）：

```
手工录制 douyin_redesign3.json：
  step 0  navigate   /content/upload        ← 起始在视频上传页
  step 1  click      /content/upload
  step 2  click      /content/upload        ← 点「上传视频」按钮 → 开文件选择器
  step 3  upload_file /content/post/video   ← ⚠️ 已经跳到发布页了！录到 image input
  step 4+ ...         /content/post/video

agent 录制 douyin_upload_history.json：
  step 0  navigate   /content/upload
  step 1  upload_file /content/upload       ← ✅ 还在上传页定位，录到 video input
```

**手工录制的 step 3，`state_summary.url` 已经是 `/content/post/video`**——说明后端 `handle_event`
→ `get_state` 定位时，抖音已经从 `/content/upload` 跳转到 `/content/post/video` 了。
原页面的静态 video input（accept=video）**已经不存在**，后端在发布页上找到的是
`upload-btn-input-UY_qeY`（accept=image）。

时序：
```
用户点按钮 → 静态 video input.click() → 文件选择器 → 选 .mp4
  → change 触发（此时页面还在 /upload，扩展 e.target = 静态 video input ✓）
  → 抖音 JS 收到文件 → 立即导航 /upload → /post/video
  → 扩展发 upload_file 事件 → 后端 get_state   ← 此时页面已在 /post/video
  → selector_map 里没有 video input → 旧代码 fallback 取第一个 = image input ✗
```

**为什么 agent 不受影响**：agent 用 CDP `DOM.setFileInputFiles` 直接往静态 video input 塞文件，
**绕过按钮 JS，不触发导航**，定位时还在 `/content/upload`。

**扩展 onFileChange 没错**：`e.target` 在 change 瞬间就是静态 video input（页面还没跳），
发的是 video input 的 xpath。错在后端 `get_state` 晚于导航。

### 修复状态

- **B 方案已实施（2026-07-15）——彻底消除竞态**：
  - **扩展**（`action-recorder.ts` `onFileChange`）：change 瞬间读 `raw.getAttribute('accept')`，
    随 upload_file 事件发 `params.accept`（此刻页面还没跳，accept 是真实 file input 的）。
  - **录制端**（`recorder.py` `handle_event`）：upload_file **不再 get_state 定位**——直接存
    `params.accept` + `params.xpath` 签名、`interacted_element=[None]`、无 index。删掉死代码
    `_locate_upload_file`。get_state 仍调（仅拿 url/title，不用 selector_map 定位 input）。
  - **重放端**（`rerun.py` `_resolve_file_input_by_accept(state, path, xpath_hint, accept_hint)`）：
    kind 优先取自 `accept_hint`（扩展真实 accept），否则按 path 扩展名；同 accept 多个（横/竖封面）
    用 `xpath_hint`（normalize_xpath）唯一命中区分。两条 upload_file 路径（匹配失败 / 无指纹）都传。
  - 效果：录制不再受 `/upload`→`/post/video` 导航竞态影响——签名在 change 瞬间就定死，重放时页面
    稳定按签名解析。全量 1960 passed，recorder 包 92% 覆盖，扩展 npm build 过。
- **旧的「录制端 accept 不匹配返 None + 重放端按 accept 解析」方案已被 B 方案取代**（录制端不再
  定位，自然不存在「定位到错 input」的问题）。

### 教训

1. **没有页面 JS 源码时不要猜运行时行为**——用 CDP 注入 hook（prototype.click / MutationObserver）
   实测，2 分钟见分晓。当初「动态新建」理论就是纯猜测，被实测推翻。
2. **录制的 `state_summary.url` 是金矿**——它暴露了 get_state 时页面在哪，直接揭示了导航竞态。
3. **录制端 get_state 与页面跳转是竞态关系**——任何会触发导航/重渲染的动作（上传、提交），
   get_state 都可能抓到「动作之后」的页面，导致定位错位。file input 尤其敏感（上传即跳转）。
