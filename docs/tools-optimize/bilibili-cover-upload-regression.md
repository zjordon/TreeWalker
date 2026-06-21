# Bilibili 封面上传「假成功」回归修复方案

> 本文是 `fix/douyin-cover-upload-#36` 分支引入 **Fix B**（非 ASCII 文件名先复制成 ASCII 临时名再上传）后，`examples/upload_file_bilibili.py` 封面上传变成「假成功」的设计文档，对应失败日志 `D:\temp\tree-walker-b.log`。
>
> 上游：`docs/tools-optimize/douyin-cover-upload-50pct.md`（issue #36）—— Fix B 是为绕过抖音前端的 ASCII-only 文件名正则而加的「复制成 ASCII 临时名」逻辑。本文修的是 Fix B 实现里的一个缺陷（临时副本被过早删除），**不推翻 Fix B 的意图**（仍发 ASCII 名给浏览器），只是把清理时机从「传完即删」改成「延迟到 session 结束」。Fix A（`file_inputs_meta` 元数据）经逐一核实**无辜**，不动。

## Context（为什么做）

`fix/douyin-cover-upload-#36` 分支跑 `examples/upload_file_bilibili.py`，封面上传动作完成、CDP 报 `OK=1`，但封面**实际没传上去**（上传区仍是占位符「拖拽图片 或点击上传」），试 2 次都失败；切回 `master` 正常。所以是本次未提交改动（Fix B / Fix A）之一引入的回归。

## 关键发现（实证）

### 1. Fix B 确实触发，且发的是 ASCII 临时名（不是 Fix B 没生效）

日志（`D:\temp\tree-walker-b.log`）：
- `upload non-ASCII filename '横封面.png' -> ASCII temp 'tw_upload_8b93f8248014.png'` —— Fix B 的临时复制分支**跑了**。
- `DOM.setFileInputFiles: backendNodeId=17407, file=...\横封面.png` —— 这行日志打的是**原始** `file_path`（一个日志 bug，实际发给 CDP 的是 `upload_path` 即临时副本，见 `session.py:2000`），一度让人以为 Fix B 没生效，其实生效了。
- CDP 返回 `✅ Step 14: ... [OK=1]` —— 命令层成功。

### 2. 真正的病因：临时副本被过早删除（`session.py:2003-2008`）

`DOM.setFileInputFiles` 的 **path 形式**（`files:[路径]`）创建的是**路径背书的 `File` 对象**——页面在 JS 真正消费这个 `File` 时（`onchange` → 预览 → 点确认，常在几秒后）才**惰性**读盘。Fix B 的 `finally` 在 CDP `await` 一返回就 `os.remove(tmp_path)`，等页面来读时文件已不存在 → 读取静默失败 → `setFileInputFiles` 早已返回 success → **假成功**。

日志佐证「页面没读到」：Step 16 的 Eval 明确写到上传区仍是「拖拽图片 或点击上传」，且**没有任何格式错误 toast**（若是文件名被拒，通常会弹错误；这里是纯静默没传上）。

### 3. 为什么只有封面挂、视频正常

- 视频 `2026-04-29-20-41-59.mp4` 是**纯 ASCII** → Fix B 的 `not base_name.isascii()` 为 False → **跳过** → 用原始（持久存在的）路径 → 正常。
- 封面 `横封面.png` 是 **CJK** → Fix B 触发 → 临时副本被过早删除 → 失败。
- `master` 无 Fix B → 一直用原始 CJK 路径（文件持久存在，页面随时能读）→ 正常。

> 与抖音的对照（说明 Fix B 的「ASCII 名」本身是对的）：抖音前端对 `File.name` 跑 ASCII 正则**同步**校验、直接弹「不支持的图片格式」——病因是**文件名**，Fix B 的 ASCII 名正是为此。Bilibili 不弹任何错误、只是静默没传上——病因是**文件被删**。两者不同。

### 4. Fix A 无辜（逐一核实）

- `actions.py:980` `backend_id = entry.backend_node_id`：file-input 目标**直传**解析到的元素，**不**重新索引 `file_input_backend_ids`。
- `set_file_input` 被以 `file_input_backend_ids=None` 调用（`actions.py:1065`，与日志 `file_input_backend_ids=None` 吻合）——说明目标用的是 agent 指定的 `17407` 本身，没被元数据改写。
- soft-warning（`actions.py:992-1013`）只拼一段 Note 字符串、**从不改 `backend_id`**；`file_input_backend_ids` 内容/顺序未变。
- → Fix A 无法造成本次假成功，不动它。

## 方案

核心：封面临时副本**存活到 session 结束**（覆盖页面惰性读盘的时间窗，行为对齐 `master`），同时**仍发 ASCII 临时名给 CDP**（保留 Fix B 对抖音的意图）。session 结束时统一清理。

### 改动 1 — `session.py` `__init__`（341-382 末尾）

新增实例属性记录待清理的临时上传文件：

```python
self._upload_temp_paths: list[str] = []
```

### 改动 2 — `session.py` `set_file_input`（1978-2008）

把「复制后立即删」改成「登记、延迟到 `stop()` 清理」，并修掉误导诊断的日志行：

- 复制临时副本后，**先**把 `tmp_path` 登记进 `self._upload_temp_paths`（放在 CDP `await` **之前**，保证即使 CDP 抛异常也已登记、`stop()` 时仍会清理）。
- **删掉** `finally: os.remove(tmp_path)` 整块（2003-2008）——回归根因。
- 日志行（1997）的 `file=%s` 从 `file_path` 改成 `upload_path`（真实发给浏览器的路径）。

改动后该段（4 空格缩进，对齐 session.py）：

```python
upload_path = file_path
tmp_path: str | None = None
base_name = os.path.basename(file_path)
if not base_name.isascii():
    _stem, ext = os.path.splitext(base_name)
    tmp_path = os.path.join(
        tempfile.gettempdir(), f"tw_upload_{secrets.token_hex(6)}{ext}",
    )
    shutil.copy2(file_path, tmp_path)
    upload_path = tmp_path
    self._upload_temp_paths.append(tmp_path)  # 延迟清理：浏览器按路径惰性读盘
    logger.info(
        "upload non-ASCII filename %r -> ASCII temp %r",
        base_name, os.path.basename(tmp_path),
    )

logger.info("DOM.setFileInputFiles: backendNodeId=%d, file=%s", target_id, upload_path)
await self.client.send.DOM.setFileInputFiles(
    {"backendNodeId": target_id, "files": [upload_path]},
    session_id=self.current_session_id,
)
```

### 改动 3 — `session.py` `stop()`（598-605）

断开连接前 best-effort 清理本 session 创建的所有临时上传文件：

```python
async def stop(self) -> None:
    """Disconnect from the browser."""
    self._cached_selector_map = None
    self._previous_cached_selector_map = None
    for p in getattr(self, "_upload_temp_paths", []):
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            logger.warning("failed to remove temp upload file %r", p)
    if self.client:
        await self.client.stop()
        self.client = None
        logger.info("Browser disconnected")
```

## 测试更新 — `tests/test_browser_session.py` `TestSetFileInputAsciiSafe`（246-309，TAB 缩进）

生命周期从「传完即删」改成「延迟到 `stop()`」，两个断言「已清理」的用例翻转语义（mock client 已有 `client.stop = AsyncMock()`，可直接 `await session.stop()`）：

- **`test_non_ascii_filename_uploads_ascii_temp_copy_and_cleans_up`** → 改名 `test_non_ascii_defers_temp_cleanup_to_stop`：
  - `set_file_input` 返回后断言临时副本**仍存在**、`session._upload_temp_paths == [sent_path]`；
  - `await session.stop()` 后断言已删、`list(tmp_path.glob("tw_upload_*")) == []`、`session._upload_temp_paths == []`。
  - 保留断言：发给浏览器的名是 ASCII、保留 `.png`、`backendNodeId==123`、原文件不动。
- **`test_ascii_filename_passthrough_no_temp_copy`**：不变（ASCII 不建临时副本，`_upload_temp_paths` 为空）。
- **`test_temp_cleaned_up_when_cdp_raises`** → 改名 `test_temp_persists_after_cdp_failure_then_cleaned_at_stop`：
  - `pytest.raises(RuntimeError)` 包住 `set_file_input`（CDP mock 抛错）；
  - 断言异常后临时副本**仍存在**且已登记进 `_upload_temp_paths`；
  - `await session.stop()` 后断言已清理。

## 验证

1. **单元测试（uv run）**：
   - `uv run python -m pytest tests/test_browser_session.py tests/test_upload_file.py -x -v`
   - 全量：`uv run python -m pytest tests/ -x -v`（应仍 900+ 通过）。
2. **回归验证（用户侧，需浏览器/登录态）**：本分支重跑 `examples/upload_file_bilibili.py`，确认封面**真正传上去**（占位符被封面图替换），而非「文件已 set」的假成功。
3. **抖音复核（可选，用户登录态）**：确认 Fix B 意图仍在——中文封面不再被前端误判格式不支持。

## 风险与回归

- 临时副本改为存活到 `stop()`，覆盖浏览器惰性读盘的时间窗——这是修复关键，且行为对齐 `master`（master 本就用持久存在的原文件）。
- 仍发 ASCII 临时名给 CDP，Fix B 对抖音的意图不变；Fix A 全程不动。
- 即使 `stop()` 未被调用（示例异常退出），回归也已修复（临时副本活过整个 session，`%TEMP%` 由 OS 兜底清理）；`stop()` 清理只是卫生。
- 不触碰 git；改完跑测试即止（按 CLAUDE.md，不主动提交）。
