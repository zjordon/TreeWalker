# read_file 工具阶段二 Follow-up 方案（分批落地）

> 兑现 [`read_file.md`](read_file.md) 末尾「阶段二（可选，独立，对齐 / 超越 browser-use 完整能力）」一节里**尚未实现**的部分。
> 同族先例（最直接对标）：[`write_file.md`](write_file.md) 阶段二（commit `1cb0484` / PR #75）、[`replace_file.md`](replace_file.md) 阶段二（commit `fc7910f` / PR #76）——同为本地文件三件套、同在 `actions.py` 内编排。本方案在结构、错误分级、回显规范、决策表写法上全面对齐 write_file / replace_file 阶段二。
> 参照标杆：browser-use `browser_use/filesystem/file_system.py:506-721` `read_file_structured`（扩展名分派：PDF/DOCX/图片，IDF 加权分页）。

---

## Context（为什么做这个 follow-up）

`read_file.md` 的「阶段二」节列了 6 项，但代码现状（2026-06-27 复测）显示 **2 项已随 write_file/replace_file 阶段二一并落地**：

| 阶段二原列项 | 现状 | 出处 |
|---|---|---|
| `encoding` 参数 | ✅ 已实现 | `_action_read_file`（`actions.py:1614-1667`）、`ReadFileParams`（`models.py:291-305`）、`TestReadFileEncoding` |
| （未列）`newline` 翻译控制 | ✅ 已实现 | 同上、`TestReadFileNewlineMode` |
| `offset` / `limit` / `line_range` 分页 | ❌ 未实现 | 本方案 二.A |
| 二进制嗅探 | ❌ 未实现 | 本方案 二.B |
| `allowed_read_paths` 白名单 | ❌ 未实现 | 本方案 二.C |
| 富文档解析（PDF/DOCX/图片） | ❌ 未实现 | 本方案 二.D |
| `FileSystem` 沙箱 | ⏸️ 显式延后 | 重大架构变更，见差异 #6 |

所以本 follow-up 聚焦 **4 个未实现项（二.A–二.D）**，并把沙箱继续明确延后。落地形态对齐 write_file 阶段二（多个子项 二.A–二.D）；但因风险差异大，**建议拆两个 PR**：

- **PR 1（小、互相关联的「读安全 + 分页 + 嗅探」）**：二.A（offset/limit）+ 二.B（二进制嗅探）+ 二.C（读白名单）。
- **PR 2（引可选依赖、面更大）**：二.D（PDF/DOCX/图片）。

> 二.B 与 二.D 共用一个文件分类器（`_sniff_file_kind`），所以 PR 1 须把分类器骨架先放进去（命中富文档时临时按「binary 拒」或「未实现提示」），PR 2 再接 `_read_rich_document`。

---

## 工程约束（实施时务必遵守）

- Windows + PowerShell；包用 uv，跑脚本/测试用 `uv run python ...`。测试命令 `uv run python -m pytest tests/ -x -v`。覆盖率目标 >85%。
- **缩进按文件实测**（已复核，与 `read_file.md` 原文的 TAB 断言**不同**——以本节为准）：
  - `src/tree_walker/tools/models.py`、`src/tree_walker/tools/actions.py`、`src/tree_walker/config.py`、`src/tree_walker/agent/agent.py` = **4 空格**。
  - `tests/test_read_file.py` 现存文件实测 = **TAB**（与 `read_file.md` 原文一致；与 src 的 4 空格不同）。新增测试类须用 TAB 与之对齐，否则 Edit 因缩进不匹配失败。
- 不主动 `git commit` / `git push`。
- **已确认的接入锚点**：
  - `config.py:91`（`AgentSettings.allowed_write_paths` 字段）、`:206-212`（`_load_allowed_write_paths` 模板）、`:279`（wiring）、`:262`（`read_file_max_chars` 读 env `AGENT_TRUNCATE_READ_FILE`）。
  - `Tools.__init__`（`actions.py:322`）、`self._allowed_write_paths`（`actions.py:328`，用法见 `:1561` 写、`:1696` replace）。
  - `agent.py:57` 的 `Tools(truncation=..., allowed_upload_paths=..., allowed_write_paths=...)` 构造调用。
  - offset/limit 惯例：`Field(default=0, ge=0)` + `params.get("offset", 0)`（见 `search_page` `models.py:467-470`、`find_elements` `models.py:162-166`）。
  - `ActionResult.metadata`（`views.py:18`，「阶段二（二.F）」通用副字段；evaluate 已用 `metadata["images"]`，`__str__` 不 dump 它，保持显示有界）。
  - `mimetypes` 已在 `actions.py` import（`_file_matches_accept:42-74` 已用）。

---

## 与 browser-use 的关键差异（有意为之，不照搬，延续 read_file.md 的差异节）

1. **不做 spill-to-disk（大结果落盘）。** `search_page` / `evaluate` / `find_elements` 阶段二都加了「`*_save_threshold` + `*_output_dir`」落盘，因为它们**生成**超大结果无处安放。read_file 的源**本身就是磁盘文件**，分页（offset/limit）才是天然机制：LLM 读首页 → 看 footer 的 `use offset=N to continue` → 再调一次。这是有意偏离同族落盘模式，**不新增** `read_file_save_threshold` / `read_file_output_dir`。
2. **二进制嗅探用 magic bytes 为主、扩展名为辅**（browser-use 用扩展名白名单 + 沙箱）。本项目无沙箱，magic bytes 更可靠（不依赖文件名），扩展名 `mimetypes.guess_type` 兜底。
3. **富文档依赖做成 optional extra（`[docs]`），import 失败给安装提示而非崩溃。** browser-use 把 pypdf/python-docx 当硬依赖；本项目保持核心零重依赖，富文档 opt-in。
4. **office 格式（docx=xlsx=pptx 都是 zip，magic 同为 `PK\x03\x04`）靠扩展名区分**，不能纯靠 magic bytes——这是 zip 容器的固有局限，文档须点明。
5. **`allowed_read_paths` 默认关（None=全放行）**，保持现状「read anywhere」（`examples/file_system/file_system.py:83` 明示）；仅宿主显式配置 env 才收敛。读是低风险操作，opt-in 对齐而非默认收敛。
6. **`FileSystem` 沙箱继续不移植。** 与 write_file.md / replace_file.md 一致，属重大架构变更，留待 TreeWalker 采纳沙箱执行模型时三件套统一设计。

---

## 二.A：`offset` / `limit` 字符级分页（`models.py` + `actions.py`）

**动机**：阶段一的截断 footer 只告诉 LLM「还有更多」，没告诉它**怎么续读**。补 `offset`/`limit` 后，footer 直接给出 `use offset=N to continue`，LLM 可翻读超大文件。

### ReadFileParams 新增字段（`models.py:291-305`，4 空格）

`before:`（现状，已有 `path` / `encoding` / `newline`）

`after:` 追加两字段：

```python
offset: int = Field(
    default=0, ge=0,
    description="0-based character offset to start reading at (for paginating files larger than "
    "read_file_max_chars; pair with the truncation footer's 'use offset=N to continue' hint).",
)
limit: int | None = Field(
    default=None, ge=1,
    description="Max characters to return from this read (default: read_file_max_chars). "
    "Use with offset to page through very large files.",
)
```

### `_action_read_file` 窗口化（替换 `actions.py:1651-1674` 的 `total_chars`…`return` 段）

在现有 `total_chars` / `total_bytes` 计算后、空文件软提示**之后**，插窗口逻辑；footer 统一加「from offset + 续读提示」。

> **关键向后兼容**：默认 `offset=0` 的截断 footer 仍含阶段一断言子串 `"[...truncated: showing {max} of {total} chars"`（阶段一测试用 `in` 子串断言），所以既有测试不破。

`after:`

```python
offset = params.get("offset", 0)
limit = params.get("limit")
max_chars = self._truncation.read_file_max_chars
window = limit if (limit is not None and limit < max_chars) else max_chars

# offset 越过文件尾：软提示（对齐 empty soft-miss），非 error。
if total_chars > 0 and offset >= total_chars:
    msg = f"offset {offset} is at or past end of {path} ({total_chars} chars); nothing to read"
    logger.info(msg)
    return ActionResult(extracted_content=msg, long_term_memory=msg)

content_window = content[offset:offset + window]
shown = len(content_window)
remaining = total_chars - offset - shown
if remaining > 0:
    end = offset + shown
    extracted = (
        content_window
        + f"\n[...truncated: showing {shown} of {total_chars} chars from offset {offset} "
        f"({total_bytes} bytes total); use offset={end} to continue]"
    )
    memory = (f"Read {path} ({shown} of {total_chars} chars from offset {offset}, "
              f"{total_bytes} bytes; truncated)")
else:
    extracted = content_window
    if offset > 0:
        memory = (f"Read {path} chars {offset}-{offset + shown} of {total_chars} "
                  f"({total_bytes} bytes; final page)")
    else:
        memory = f"Read {path} ({total_chars} chars, {total_bytes} bytes)"  # 阶段一原样
logger.info(memory)
return ActionResult(extracted_content=extracted, long_term_memory=memory)
```

> **重构建议**：抽 helper `_window_and_echo(content, path, offset, limit, total_bytes)` 供文本路径与 二.D 的 PDF/DOCX 路径复用（动作体只编排，对齐 browser-use「service.py 只编排、逻辑在 FileSystem」的思想）。

### 二.A 决策表

| 决策点 | 结论 | 理由 |
|---|---|---|
| offset/limit 单位 | **字符**（非字节） | 与 `read_file_max_chars`、阶段一 footer「showing X of Y chars」一致；`offset=read_file_max_chars` 即续读起点 |
| `limit` 默认 | `None` → 用 `read_file_max_chars` | 默认行为=阶段一；显式 limit 才收窄 |
| offset 越界 | 软提示（非 error） | 对齐 empty soft-miss；让 LLM 知道读完了 |
| 默认截断 footer 是否改 | 加 `from offset 0; use offset=N to continue` | 必须告诉 LLM 续读 offset（阶段二动机）；子串断言兼容，阶段一测试不破 |
| `success` | `None` | 分页不终止序列（`views.py` 校验器禁非 done 设 `success=True`） |

---

## 二.B：二进制嗅探（`actions.py`，新增 helper + 守卫）

**动机**：阶段一靠 `UnicodeDecodeError` 兜底二进制，但错误信息是「Failed to decode … as UTF-8」——对 LLM 不可操作（不知道这是什么文件、该换什么工具）。补 magic-byte 嗅探后，能给出「这是 PNG 图片，read_file 读不了，请用 …」这样的指引；同时为 二.D 富文档分派提供分类器。

### 新增 `_sniff_file_kind(path) -> str`（模块级，靠近 `_file_matches_accept:42-74`）

```python
_MAGIC = [  # (signature, kind)
    (b"\x89PNG\r\n\x1a\n", "image"),        # PNG（8 字节签名，最特异）
    (b"\xff\xd8\xff", "image"),             # JPEG
    (b"GIF87a", "image"), (b"GIF89a", "image"),
    (b"RIFF", "image"),                      # WebP/AVI；扩展名二次确认
    (b"%PDF-", "pdf"),
    (b"PK\x03\x04", "office"),              # zip 容器：docx/xlsx/pptx/普通 zip，靠扩展名细分
    (b"\x7fELF", "binary"),                  # Linux 可执行
    (b"MZ", "binary"),                       # Windows PE/exe
    (b"\x1f\x8b", "binary"),                 # gzip
    (b"BZh", "binary"),                      # bzip2
    (b"Rar!", "binary"),
    (b"7z\xbc\xaf\x27\x1c", "binary"),
]

def _sniff_file_kind(path: str) -> str:
    """Peek magic bytes; return 'text' | 'pdf' | 'docx' | 'image' | 'binary'.
    'office' (zip) is refined to 'docx' by .docx extension, else 'binary'."""
    with open(path, "rb") as f:
        head = f.read(8)
    for sig, kind in _MAGIC:
        if head.startswith(sig):
            if kind == "office":
                return "docx" if path.lower().endswith(".docx") else "binary"
            if kind == "image" and not path.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
                return "binary"   # RIFF 但非图片扩展名（如 .avi）当二进制拒
            return kind
    return "text"
```

### `_action_read_file` 守卫（置于 二.C 的 allowed_read_paths 检查之后、文本 `open` 之前）

```python
try:
    kind = _sniff_file_kind(path)
except FileNotFoundError:
    return ActionResult(error=f"File not found: {path}")
except OSError as e:
    logger.warning("read_file(%r) sniff failed: %s", path, e)
    return ActionResult(error=f"Failed to read file {path}: {e}")

if kind == "binary":
    logger.warning("read_file(%r) rejected binary", path)
    return ActionResult(error=f"{path} looks like a binary file; read_file reads UTF-8 text, "
                               "PDF, DOCX, or images (PNG/JPEG/GIF/WebP).")
if kind in ("pdf", "docx", "image"):
    return await self._read_rich_document(path, kind, params)   # → 二.D（PR 2 接）
# kind == "text" → 走现有文本解码 + 二.A 窗口
```

> **PR 1 占位**：二.D 未落地前，`if kind in ("pdf", "docx", "image")` 分支可临时返回一个明确的「rich-doc 尚未实现，请装 `[docs]` 后待 二.D」提示，或直接归并到 binary 拒。二.D 落地时替换为 `_read_rich_document` 调用。

### 二.B 决策表

| 决策点 | 结论 | 理由 |
|---|---|---|
| 识别手段 | magic bytes 主、扩展名辅 | 不依赖文件名更可靠；zip 容器 / RIFF 需扩展名细分 |
| 命中「不支持二进制」 | 可操作 error（非异常、非 decode error） | 早拒，给 LLM 明确指引（读不了 exe/zip） |
| 命中「富文档」 | 转 `_read_rich_document`（二.D） | 二.B 与 二.D 共用分类器，自然衔接 |
| 普通文本是否误判 | 不会 | magic 签名足够特异；既有 GBK 测试文件无 magic 头，仍走文本 → decode error（回归通过） |
| 双重 open | 接受（peek 8 字节 + 文本 open） | 小文件成本可忽略；逻辑清晰优先 |

---

## 二.C：`allowed_read_paths` 白名单（默认关，opt-in）—— 完整镜像 `allowed_write_paths`

**动机**：三件套（upload/write/read）路径安全应对称。读虽低风险，但宿主若需 jail（如只允许 agent 读 workspace），现无出口。补 `allowed_read_paths`（默认关），与 write/replace 对称、opt-in，保持现状「read anywhere」不破。

7 处对称改动：

1. **`config.py:91`**：`AgentSettings` 加 `allowed_read_paths: list[str] | None = None`。
2. **`config.py:206-212`**：加 `_load_allowed_read_paths()`，读 env `AGENT_ALLOWED_READ_PATHS`（逗号分隔），照抄 `_load_allowed_write_paths` 形状。
3. **`config.py:279`**：wiring `allowed_read_paths=_load_allowed_read_paths()`。
4. **`actions.py:322`**：`Tools.__init__` 加形参 `allowed_read_paths: list[str] | None = None`。
5. **`actions.py:328`**：存 `self._allowed_read_paths = allowed_read_paths`。
6. **`agent.py:57`**：`Tools(...)` 调用补 `allowed_read_paths=_settings.allowed_read_paths`。
7. **`_action_read_file` 顶部**（最先，fail fast，在 sniff/open 之前）：

   ```python
   allowed = self._allowed_read_paths
   if allowed and not any(path.startswith(p) for p in allowed):
       return ActionResult(error=f"File path not in allowed read paths: {path}")
   ```

> 默认 `None` = 全放行，保留现状「read anywhere」（`examples/file_system/file_system.py:83`）。仅宿主设 `AGENT_ALLOWED_READ_PATHS` 才收敛。

---

## 二.D：富文档解析（PDF/DOCX/图片，optional extra `[docs]`）

**动机**：阶段一只读 UTF-8 文本，富文档（PDF/DOCX/图片）一律 `UnicodeDecodeError`。补扩展名 + magic 分派，对齐 browser-use `read_file_structured` 的多格式能力（差异 #3：依赖 opt-in）。

### 依赖（`pyproject.toml:16-17`）

```toml
[project.optional-dependencies]
vision = ["pillow>=10.0.0"]
docs = ["pypdf>=4.0.0", "python-docx>=1.1.0"]   # 新增
```

安装：`uv pip install -e ".[docs]"` / `uv sync --extra docs`。

### `_read_rich_document(self, path, kind, params) -> ActionResult`（dispatch by kind）

**图片**（kind == "image"）—— **只返回可操作提示，不堆 base64**：

```python
mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
try:
    nbytes = os.path.getsize(path)
except OSError:
    nbytes = 0
msg = (f"{path} is an image ({mime}, {nbytes} bytes). read_file cannot inline images "
       "yet (vision channel not wired); re-save as text/PDF, or use a vision-capable flow.")
return ActionResult(extracted_content=msg, long_term_memory=msg)
```

> **为什么不走 `metadata["images"]`（base64）**：实测 agent loop 从不读 `metadata`——evaluate 写了 `metadata["images"]`，但 `step.py` 只取 `extracted_content` 喂 LLM，即该通道当前是**死代码**。往里堆 base64 既撑上下文又无收益。故图片暂只给可操作提示；待 agent loop 统一接线 images 通道（届时也激活 evaluate）后再改为 base64。

**PDF**（kind == "pdf"，pypdf，缺依赖给安装提示）：

```python
try:
    from pypdf import PdfReader
except ImportError:
    return ActionResult(error=f"{path} is a PDF; install the 'docs' extra: uv pip install -e .[docs]")
pages = PdfReader(path).pages
parts = [f"--- page {i+1}/{len(pages)} ---\n{(p.extract_text() or '')}"
         for i, p in enumerate(pages)]
text = "\n\n".join(parts)
return self._window_and_echo(text, path, params, len(text.encode("utf-8")))   # 复用 二.A 窗口
```

**DOCX**（kind == "docx"，python-docx，缺依赖给安装提示）：

```python
try:
    from docx import Document
except ImportError:
    return ActionResult(error=f"{path} is a DOCX; install the 'docs' extra: uv pip install -e .[docs]")
doc = Document(path)
text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
return self._window_and_echo(text, path, params, len(text.encode("utf-8")))
```

> 无新增 config（图片不 base64，故不需要 `read_file_image_max_bytes`）。

### 二.D 决策表

| 决策点 | 结论 | 理由 |
|---|---|---|
| 依赖形式 | optional extra `[docs]`，import 失败 → 可操作 error | 核心零重依赖；opt-in |
| 图片通道 | **不接线**，只返回可操作提示 | `metadata["images"]` 是死代码（agent loop 不读）；待统一接线后再启用，避免堆无用 base64 |
| PDF 分页 | 顺序拼各页文本 + 复用 二.A offset/limit 窗口 | MVP 简单；browser-use 的 IDF 加权分页列为后续增强（注释点明） |
| docx 区分 | 靠 `.docx` 扩展名（zip 容器 magic 无法区分） | 差异 #4；`.xlsx/.pptx` 暂不支持 → 当 binary 拒（或后续扩） |
| 提取后字节统计 | `len(text.encode("utf-8"))`（已解码文本） | 富文档提取出的是 str，按 utf-8 计字节 |
| 解析异常 | `except Exception` 降级为 `ActionResult(error=...)` | 第三方库异常类型不可枚举；统一降级不冒泡通用 catch |

---

## 新增测试（追加到 `tests/test_read_file.py`，**TAB**，对齐现存文件）

入口仍走 `Tools().execute("read_file", params, MagicMock())`，`tmp_path` 隔离（对齐 `TestWriteFileWhitelist`）。

- **TestReadFileOffsetLimit**
    - `test_offset_reads_from_offset` —— `offset>0` 返回 `content[offset:]` 起的内容。
    - `test_limit_caps_window` —— 显式 `limit` 收窄返回字符数。
    - `test_offset_past_end_soft_miss` —— `offset >= total_chars` → 软提示、`error is None`。
    - `test_default_offset_zero_is_phase1_behavior` —— 不传 offset/limit → 与阶段一回显完全一致。
    - `test_truncated_footer_has_continue_hint` —— 截断 footer 含 `use offset=N to continue`。
    - `test_offset_preserves_crlf` —— offset 窗口下 CRLF 仍字节保真。
- **TestReadFileBinarySniff**
    - `test_exe_mz_rejected` / `test_elf_rejected` / `test_gzip_rejected` —— 可执行/归档拒，`error` 含「binary」。
    - `test_plain_zip_non_docx_rejected` —— `PK\x03\x04` 且非 `.docx` → binary 拒。
    - `test_utf8_text_not_false_positive` —— 普通 UTF-8 文本不被误判（仍走文本路径）。
    - `test_gbk_text_still_decode_error` —— 既有 GBK 测试文件无 magic 头 → 仍 `UnicodeDecodeError`（回归不破）。
- **TestReadFileReadWhitelist**（镜像 `TestWriteFileWhitelist`）
    - `test_blocked_outside_whitelist` —— `Tools(allowed_read_paths=[inside])`，读 `outside` → `error` 含「allowed read paths」。
    - `test_allowed_inside_whitelist` —— 白名单内放行。
    - `test_none_whitelist_allows_anywhere` —— 默认 `Tools()` 全放行（保持现状）。
- **TestReadFileRichDocs**（PDF/DOCX 用 `pytest.importorskip("pypdf")` / `("docx")` 守卫）
    - `test_pdf_text_extraction` —— 抽出页文本（含 `--- page 1/N ---`）。
    - `test_docx_text_extraction` —— 抽出段落文本。
    - `test_image_returns_actionable_prompt` —— 图片 → `extracted_content` 含「image」+「vision」、`metadata is None`（不堆 base64）。
    - `test_pdf_without_dep_returns_install_hint` / `test_docx_without_dep_returns_install_hint` —— monkeypatch 模拟 ImportError → `error` 含安装命令。

测试命令：

```powershell
uv run python -m pytest tests/test_read_file.py -x -v
uv run python -m pytest tests/test_read_file.py --cov=tree_walker.tools.actions --cov-report=term-missing
uv run python -m pytest tests/ -x -v
```

---

## 关联文档修订（实现阶段一并改）

1. **`docs/tools-optimize/read_file.md`** 末尾「阶段二」节：把 `encoding` / `newline` 标 ✅ 已实现；offset/limit / 二进制嗅探 / allowed_read_paths / 富文档 → 指向本文件 `read_file_follow_up.md`；沙箱保持延后。
2. **`examples/file_system/file_system.py:83`** 注释「read_file is not gated」→ 改为「read_file 默认不限路径；可用 `AGENT_ALLOWED_READ_PATHS` opt-in 收敛」。
3. **`docs/Tools技术细节/04_动作清单与CDP映射.md`** 4.12 节：补 offset/limit 参数、二进制嗅探、富文档分派、读白名单的行为描述（并保持阶段一已修的 stale 行号 `actions.py:1277-1284` → 实际 `:1614-1667`）。

---

## 风险与回归点

| 风险 | 影响 | 缓解 |
|---|---|---|
| footer 加「from offset / use offset=N」改截断回显 | 阶段一 `test_over_max_chars_gets_footer`（`in` 子串断言） | 保留子串 `"[...truncated: showing {shown} of {total} chars"`，仅追加；断言仍过 |
| magic 嗅探误判文本 | 合法文本被当二进制拒 | 签名足够特异；既有 GBK/CRLF 测试回归验证不误判；普通文本无 magic 头 |
| `[docs]` 未装时读 PDF/DOCX | ImportError 冒泡 | try/except ImportError → 可操作 error（含安装命令） |
| 图片读到死通道 | base64 进 `metadata["images"]` 但 agent loop 不读 → LLM 看不到又撑上下文 | **图片改为只返回可操作提示**（不 base64、不写 metadata）；待 loop 接线后再启用 |
| 双重 open（sniff + decode） | 小性能开销 | 可忽略；逻辑清晰优先 |
| `ReadFileParams` 新增 offset/limit | `extra="forbid"` + registry 不校验 execute 路径 | 字段有默认值，安全；schema（LLM）+ 直接构造均不受影响（见 memory [[action-params-no-runtime-validation]]） |

---

## 验证方法

1. **单测三连**（read_file 单文件、覆盖率、全量回归），覆盖率 >85%。
2. **动作层冒烟**（PowerShell here-string，走 `Tools().execute`）：
    - 文本 round-trip + offset 续读（首页 footer `use offset=5000 to continue` → 二次 `offset=5000` 读后续）。
    - 目录 / GBK / 二进制（`.exe`）拒，`error` 可操作。
    - PDF/DOCX（装 `[docs]` 后）抽文本；图片返回可操作提示（vision 通道未接线）。
    - `Tools(allowed_read_paths=[...])` 白名单内外行为。
3. **回归对照**：错误降级（`logger.warning`，不冒泡 `Tools.execute` 通用 catch `actions.py:260-262`）与 write_file / replace_file / save_as_pdf 一致；阶段一全部测试不破。

---

## 验收 checklist

- [ ] 二.A：`ReadFileParams` 加 `offset`/`limit`；`_action_read_file` 窗口化 + footer 续读提示；阶段一子串断言兼容
- [ ] 二.B：`_sniff_file_kind` magic-byte 分类器；命中 binary 拒、命中富文档转 二.D（PR 1 可占位）
- [ ] 二.C：7 处对称改动（config 字段 + env loader + wiring + `Tools.__init__` + `agent.py:57` + action 守卫），默认 None 全放行
- [ ] 二.D：`[docs]` extra + `_read_rich_document`（PDF/DOCX/图片）+ `read_file_image_max_bytes`；缺依赖给安装提示
- [ ] `tests/test_read_file.py` 追加 4 个测试类（4 空格），全过
- [ ] 覆盖率 >85%，`uv run python -m pytest tests/ -x -v` 全量回归无破
- [ ] 关联文档修订：`read_file.md` 阶段二节、`file_system.py:83` 注释、`04_动作清单与CDP映射.md` 4.12 节
