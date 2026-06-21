# 抖音封面上传 50% 失败修复方案

> 本文是 `master` 版本「抖音发视频约 50% 失败」的设计文档，对应失败日志 `D:\temp\tree-walker-master.log`。
>
> 上游 issue：`docs/tools-optimize/upload_file_fix.md`（issue #34）已确认抖音封面**无 `<label>`**、不可经 file-chooser 自动上传，且 v0.3.0..master **无回归**（已 bisect，不再重查）。本文不重查回归，只针对日志暴露的两个具体失败原因做修复。

## Context（为什么做）

当前 `master` 在抖音创作者中心发视频时约 50% 失败。日志显示失败集中在**封面编辑器**这一步——视频本体、标题、简介、合集、自主声明都已成功，卡在封面上传。已验证（合法 PNG + 传输无损 + 报错出现在 live input 上）存在两个**相互独立**的根因：

- **原因 B（本次日志失败的直接原因）**：封面文件名是中文（`横封面.png` / `竖封面.png`），被抖音前端的 ASCII-only 文件名正则误判为「不支持的图片格式」。
- **原因 A（让 50% 显得「固有」的那个）**：抖音封面编辑器有 ~6 个 `<input type=file>`，Agent 常把文件 set 到没有 handler 的隐藏「诱饵」input 上——`setFileInputFiles` 在 CDP 层永远「成功」返回，但页面毫无变化（日志里 77724/78476/78514/78525 都是「假成功」）。这正是 issue #34 记录的「抖音封面无 `<label>` → 打不开 chooser → 多 input 易打错」的机制抖动。

## 关键发现（实证）

### 1. 封面文件本身是合法 PNG（排除「文件损坏」）

`横封面.png`（1.5MB）、`竖封面.png`（1.6MB）魔数 `89 50 4E 47 0D 0A 1A 0A` + IHDR，是合法 PNG。所以「不支持的图片格式」不是文件坏了。

### 2. 上传传输链无损（排除「传输把中文搞坏」）

`upload_file → set_file_input → DOM.setFileInputFiles({files:[路径]})`（`session.py:1975`）传的是**文件路径**，由 Chrome 自己读盘。底层 `cdp_use`（`.venv/.../cdp_use/client.py:386`）用 `json.dumps`（默认 `ensure_ascii=True`）把 CJK 无损转义为 `\uXXXX` 再被 Chrome 还原——页面拿到的 `File.name = 横封面.png`、`File.type = image/png`（按内容嗅探），字节没被破坏。

### 3. 抖音前端照样拒（坐实「文件名校验是 ASCII-only」）

日志 Step 19 上传到 input **77725**（`semi-upload` 那个**接了 handler 的** live 封面 input）后，弹出「不支持的图片格式，只支持jpg, png, jpeg」。一个合法 PNG + `image/png` 却被判格式不支持，**唯一异常点就是文件名里的中文**——抖音封面前端的文件名校验大概率是 `[\w.\-]+\.(png|jpg|jpeg)` 之类，`横封面` 不匹配 `\w`，用误导性错误把它拒掉。Agent 自己也猜到（Step 20 想改名 `heng_cover.png`）。

### 4. 诱饵 input 的「假成功」无法靠 file-chooser 解决

抖音封面无 `<label>`，`discover_file_input_via_click` 走不通；6 个 file input 里只有 `semi-upload` 父级那个（日志 77725）是 live 的。Agent 当前只能盲猜 index + 重试。**目前 LLM 拿到的 file-input 元数据是零**（prompt 里只有 `element_tree_text`）。

---

## 方案

### Fix B（主修复，高置信度）：上传时透明转 ASCII 文件名

**改动**：`src/tree_walker/browser/session.py` 的 `set_file_input`（约 1936-1978）。在 `DOM.setFileInputFiles` 调用前，若 `os.path.basename(file_path)` 含非 ASCII，复制成 ASCII 临时名（保留扩展名）再上传，`finally` 清理。纯 ASCII 路径直接透传，**对现有上传零回归**。

放在 session 层的理由：通往 CDP 的唯一咽喉点，所有调用方受益；`actions.py` 的白名单校验和 echo 都跑在**原始路径**上（白名单照常过，echo 仍显示真实文件名 `横封面.png`），只有真正发往浏览器的路径换成 ASCII 临时副本。

```python
import os, shutil, tempfile, secrets
upload_path = file_path
tmp_path = None
base = os.path.basename(file_path)
if not base.isascii():
    _stem, ext = os.path.splitext(base)
    tmp_path = os.path.join(tempfile.gettempdir(), f"tw_upload_{secrets.token_hex(6)}{ext}")
    shutil.copy2(file_path, tmp_path)
    upload_path = tmp_path
    logger.info("upload non-ASCII filename %r -> ASCII temp %r", base, os.path.basename(tmp_path))
try:
    await self.client.send.DOM.setFileInputFiles(
        {"backendNodeId": target_id, "files": [upload_path]},
        session_id=self.current_session_id,
    )
finally:
    if tmp_path and os.path.exists(tmp_path):
        try: os.remove(tmp_path)
        except OSError: pass
```

### Fix A（best-effort）：暴露 file-input 元数据，帮 Agent 锁定 live input

**诚实声明**：抖音的 `<input type=file>` **全都 `display:none`**，且其中几个共用 upload-父级 class，因此静态元数据**无法完美区分** live / 诱饵 input。本修复的价值是「给 LLM 结构化信息去推理」而非「机械判定」——目前 LLM 拿到的元数据是零，任何结构化信息都比盲猜强。若上线后仍不够，下一步再考虑「按候选逐个 set + 反应检测」（本文不含）。

- `dom.py` `_collect_file_inputs`（469-485）：递归同时收集每个 input 的 `{accept, visible, upload_ancestor}`。`accept` 在 473 行已解析但被丢弃（免费）；`visible` 复用 `snapshot_lookup[bid].computed_styles`（display/visibility/opacity，等同 `_is_element_visible_according_to_all_parents` 前 6 行 dom.py:685-693，不要复用整个闭包/视口交集）；`upload_ancestor` 累积父级 class 含 `upload`/`semi-upload`。返回 `list[FileInputInfo]`，在 660 行派生 `file_input_backend_ids = [fi.backendNodeId ...]`（**字段语义不变**）。
- `views.py` `SerializedDOMState`（677-689）：新增 `FileInputInfo` dataclass + `file_inputs_meta: list[FileInputInfo]`（**不动** `file_input_backend_ids`，降低回归面）；同步 `EMPTY_DOM_STATE`。
- `prompts/system_prompt.py`（~152-169）：`len(file_inputs_meta) > 1` 时追加 `[File Inputs]` 段（index/accept/visible/upload-ancestor + 选型提示）。**价值最高的一个改动，纯增量**。
- `tools/actions.py` soft-warning 分支（988-999）：读 `file_inputs_meta`，把软警告升级为「可见 + upload 容器内的候选是 index X/Y，你选的是 Z；若 Z 无反应请改试 X」。**保持信任 agent 指定 index 的契约不变**。

---

## 需要改动的文件

- `src/tree_walker/browser/session.py` — `set_file_input` 加 ASCII 临时副本（Fix B）
- `src/tree_walker/browser/dom.py` — `_collect_file_inputs` 收集元数据 + 派生 ids（Fix A）
- `src/tree_walker/browser/views.py` — `FileInputInfo` + `SerializedDOMState.file_inputs_meta`（Fix A）
- `src/tree_walker/prompts/system_prompt.py` — `[File Inputs]` 段（Fix A）
- `src/tree_walker/tools/actions.py` — soft-warning 升级（Fix A）
- 测试：`tests/test_browser_session.py`、`tests/test_dom_building.py`、`tests/test_dom_views.py`、`tests/test_system_prompt.py`、`tests/test_upload_file.py`

## 验证

1. 单元测试（按 CLAUDE.md 用 uv run）：
   - `uv run python -m pytest tests/test_browser_session.py tests/test_upload_file.py tests/test_dom_building.py tests/test_dom_views.py tests/test_system_prompt.py -x -v`
   - 全量：`uv run python -m pytest tests/ -x -v`
   - 覆盖率（目标 >85%）：`uv run python -m pytest tests/ --cov=tree_walker --cov-report=term-missing`
2. 本地机制验证（Fix B，不需抖音登录）：含 `<input type=file>` + `onchange` 打印 `File.name`/`File.type` 的本地 HTML；`upload_file` 传 `横封面.png`；断言页面收到 ASCII 名 + `image/png`。
3. 抖音真实验证（需用户登录态）：用 `横封面.png` / `竖封面.png` 发草稿；确认封面被接受（Fix B）、误选诱饵 input 的步数明显减少（Fix A）。

## 风险与回归

- Fix B：纯 ASCII 路径透传不变，对正常上传零影响；临时文件 `finally` 清理；不改任何签名。
- Fix A：`file_input_backend_ids` 字段语义不变（仅新增 `file_inputs_meta`），现有 session 回退 / step 日志 / 既有 upload 测试不受影响；prompt 与 soft-warning 均为纯增量。
- 不触碰 git；改完跑测试即止（按 CLAUDE.md，不主动提交）。
