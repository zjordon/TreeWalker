# write_file 工具优化方案（分阶段）

> 参照 browser-use（`browser_use/tools/service.py:1682-1711` write_file 动作体、`browser_use/filesystem/file_system.py:723-802` `write_file`/`append_file`、参数模型自动生成）完善本项目 write_file 工具。
> 相关现状文档：`docs/Tools技术细节/04_动作清单与CDP映射.md` 的 4.24 节；参考标杆：`browser-use/docs/Tools技术细节/06-动作详解-数据处理与文件.md` 的 20. write_file 节。
> 同族先例：`docs/tools-optimize/evaluate.md`（commit `9a60e9d` / issue #48 / PR #49）、`docs/tools-optimize/find_elements.md`（commit `a34a1f9` / issue #46 / PR #47）、`docs/tools-optimize/search_page.md`（commit `c5db7db` / issue #44 / PR #45）、`docs/tools-optimize/save_as_pdf.md`——本方案在结构、错误分级、回显规范上全面对齐四者阶段一；write_file 是纯本地文件操作（无 CDP），IO 错误模式与回显风格直接镜像 `save_as_pdf`。

---

## 适用场景（什么时候会用到 write_file）

**定位**：把 LLM 在会话中产出/加工的文本（抽取结果、爬到的数据、生成的代码/配置）**持久化到本地磁盘**的通用出口。是 read_file / replace_file 三件套里的"写"那一半，与浏览器零交互。

| 工具 | 职责 | 与 write_file 的区别 |
|---|---|---|
| `write_file` | 文本写入本地文件（覆盖/追加） | 唯一的"整文件写"出口 |
| `read_file` | 读本地文件 | 反向操作；写入后用来回读校验 |
| `replace_file` | 文件内字符串替换 | 小范围就地编辑，不重写整文件（适合改大文件的某一行） |
| `save_as_pdf` | 当前页面打印为 PDF | 产物来自浏览器（CDP `Page.printToPDF`），不是 LLM 文本 |
| `screenshot` | 截图当前视口 | 二进制图片，不是文本 |
| `extract` | LLM 从页面抽取信息 | 抽取结果常作为 write_file 的 `content` 输入 |

**典型场景**：

1. 把 `extract` / `find_elements` 抓到的结构化数据落盘成 `.json` / `.csv` / `.md`。
2. 把生成的脚本/配置/笔记写到工作目录（父目录自动创建）。
3. 追加日志/流水（`append=True`，避免每次重写整个文件）。
4. 覆盖式更新一个已有文件（默认行为，先 read_file 再整体改写）。

**什么时候不需要它**：

- 只想改文件里的一小段 → 用 `replace_file(path, old, new)`，别整体重写（大文件重写既慢又易丢未读部分）。
- 想保存的是当前网页 → 用 `save_as_pdf` / `screenshot`，write_file 不接收二进制。

**可用性提示**：阶段一覆盖覆盖写、追加写、尾/首换行控制、UTF-8 编码、分级错误、字节数回显、全量单测；阶段二再补原子写、`encoding` 参数、写路径白名单等（见末尾）。

---

## Context（为什么做这个改动）

当前实现（[`src/tree_walker/tools/actions.py:1243-1249`](../../src/tree_walker/tools/actions.py)）：

```python
async def _action_write_file(self, params: dict, browser: BrowserSession) -> ActionResult:
    path = params["path"]
    content = params["content"]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return ActionResult(extracted_content=f"Written to {path}")
```

参数模型（[`src/tree_walker/tools/models.py:165-168`](../../src/tree_walker/tools/models.py)）：

```python
class WriteFileParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(description="File path to write to")
    content: str = Field(description="Content to write")
```

注册项（[`src/tree_walker/tools/models.py:285`](../../src/tree_walker/tools/models.py)）：`"write_file": (WriteFileParams, "Write content to a local file", False)`。

**主要问题**：

1. **无错误处理**：`OSError` / `PermissionError` / `IsADirectoryError`（path 指向目录）/ 父目录不可写 等全部冒泡到 `Tools.execute` 的通用 catch（[`actions.py:260-262`](../../src/tree_walker/tools/actions.py)），变成无上下文的 `error=str(e)`，LLM 拿不到 "写哪个文件失败" 的信息。
2. **无 `logger.warning`**：失败时无日志，排障困难（同族 `save_as_pdf` / `find_elements` / `evaluate` 均有）。
3. **不设 `long_term_memory`**：与 navigate / click / find_elements / evaluate / upload_file 主流回显约定不一致，agent 跨步无法回忆"我已经写过这个文件"。
4. **回显信息量低**：仅 `f"Written to {path}"`，无字节数、无"覆盖/追加"语义、无编码提示。
5. **永远覆盖**：没有 append 模式，写日志/流水必须每次重写整文件，低效且易丢历史。
6. **无换行控制**：LLM 产出的文本常常缺尾换行，写出的文件末尾没有 `\n`，不符合 POSIX 文本文件惯例；追加时也无法在已有内容和新内容之间插空行。
7. **描述过于简短**：`"Write content to a local file"` 没告诉 LLM 默认是覆盖、父目录会自动建、何时该改用 `replace_file`。
8. **零测试**：`tests/` 下无 `test_write_file.py`（read_file / replace_file 同样无测试），覆盖率黑洞。
9. **非原子写**：写到一半进程挂掉会留下半个文件（阶段二再修，同 `replace_file`）。

**参照标杆 browser-use 的做法**（`browser_use/tools/service.py:1682-1711`）：write_file 在 action 层做换行簿记（`trailing_newline` 默认补 `\n`、`leading_newline` 前置 `\n`），按 `append` 分流到 `file_system.write_file()` / `append_file()`，回显同时写 `extracted_content` + `long_term_memory`。browser-use 额外有一套 `FileSystem` 沙箱（workspace 目录、文件名 sanitize、扩展名白名单、内存+磁盘双层），**TreeWalker 无此基础设施且不打算引入**（见"关键差异"）。

**预期结果**：阶段一为 write_file 增加 `append` / `trailing_newline` / `leading_newline` 三个参数、补齐 `OSError` 分级捕获 + `logger.warning`、回显字节数与写法（覆盖/追加）并同步 `long_term_memory`、精修 description、新增 `tests/test_write_file.py` 覆盖率 ≥85%，并同步修正现状文档 4.24 节。

---

## 工程约束（实施时务必遵守）

- Windows + PowerShell；包用 uv，跑脚本 / 测试用 `uv run python ...`。测试命令 `uv run python -m pytest tests/ -x -v`。
- **缩进按文件**（已复核）：`src/tree_walker/tools/models.py`、`src/tree_walker/tools/actions.py` = **4 空格**；`tests/test_write_file.py` = **TAB**（对齐 `tests/test_save_as_pdf.py`）。
- 改完跑相关单测 + 全量回归；覆盖率目标 >85%。
- 不主动 `git commit` / `git push`。
- `os` / `logger` 在 `actions.py` 已是模块级 import；`Field` / `ConfigDict` 已在 `models.py` import。新增代码无需新 import。

---

## 与 browser-use 的关键差异（有意为之，不照搬）

1. **不移植 `FileSystem` 沙箱。** browser-use 用 `file_name`（纯文件名）+ `FileSystem`（workspace 目录 `base_dir/browseruse_agent_data/`、`_resolve_filename` basename 防 traversal、`_is_valid_filename` 扩展名正则白名单、`sanitize_filename` 自动修正、内存 `self.files` + `sync_to_disk` 双层、构造时 `shutil.rmtree` 清空，`file_system.py:353-385/407-470/723-802`）。TreeWalker 用裸 `path`（绝对/相对路径）直写磁盘，read_file / replace_file 全家一致；移植沙箱是重大架构变更，**阶段二再议**。保留 `path` 参数名。
2. **不移植扩展名白名单 / 二进制拒绝。** browser-use 显式拒 `.png/.jpg/.mp4` 等（`_build_filename_error_message`）。TreeWalker 的 `content: str` 天然只写文本，无二进制路径，不需要。
3. **不移植 `file_system` 特殊参数注入。** browser-use 经 registry 把 `FileSystem` 实例注入 action（`registry/service.py:366-394`）。TreeWalker 无 FileSystem，`Tools` 直接 `open()` 写盘。
4. **不移植内存文件存储 + 按扩展名序列化（csv/pdf/docx）。** browser-use 的 `CsvFile` 每次写重解析 csv 规范引号、`PdfFile`/`DocxFile` 经 reportlab/python-docx 渲染二进制（`file_system.py:168-326`）。超出本工具范围。
5. **append 缺失文件行为相反（有意）。** browser-use `append_file` 要求文件已在 `self.files` 中，否则返回 `"File not found"`（内存存储的副产物）。TreeWalker 沿用 Python `open(path, "a")` 的**自动创建**语义——append 到不存在的文件直接创建它（建日志场景更顺手，且不让 append 比 overwrite 弱）。见阶段一决策表。
6. **`trailing_newline` 用守卫式而非无条件追加。** browser-use 无条件 `content += '\n'`（对已带尾换行的内容会产生双换行）。本方案改为 `if trailing_newline and not content.endswith("\n")`，幂等、不双换行、且天然不破坏 CRLF（`"foo\r\n".endswith("\n")` 为 True）。

---

## 阶段一：追加/换行控制 + 分级错误 + 回显 + 测试（优先做，风险低）

> write_file 是纯本地文件操作，**不涉及 CDP，无需 session 封装**——保留 action 内联，仅加固错误处理与回显（与 `save_as_pdf` 写盘段同构，区别仅在于 write_file 的"产物"是 LLM 给的 `content` 而非 CDP 返回的 bytes）。

### 1.1 `WriteFileParams` 扩展（`models.py:165-168`，4 空格）

保留 `path` + `content`，新增 `append` / `trailing_newline` / `leading_newline` 三个 bool（对齐 browser-use 签名），`extra="forbid"` 保留。

before：

```python
class WriteFileParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(description="File path to write to")
    content: str = Field(description="Content to write")
```

after：

```python
class WriteFileParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(description="File path to write to (parent directories are auto-created).")
    content: str = Field(description="Text content to write (UTF-8).")
    append: bool = Field(
        default=False,
        description="If True, append to the end of an existing file instead of overwriting it. "
        "Default False overwrites the entire file.",
    )
    trailing_newline: bool = Field(
        default=True,
        description="If True (default), ensure the written content ends with exactly one newline "
        "(no-op if it already does).",
    )
    leading_newline: bool = Field(
        default=False,
        description="If True, prepend a newline before the content (useful when appending to a "
        "file that lacks a trailing newline).",
    )
```

> 不加 `min_length`/`max_length`：空写（截断/建空文件）是合法用法；写入体积的截断属于 read_file 职责（`read_file_max_chars`），write 不截断。三个 bool 各自语义独立、默认即复现当前行为，LLM 零成本省略。

### 1.2 `_action_write_file` 重写（`actions.py:1243-1249`，4 空格）

before：见 Context 节引用的 7 行裸实现。

after：

```python
async def _action_write_file(self, params: dict, browser: BrowserSession) -> ActionResult:
    path = params["path"]
    content = params["content"]
    append = params.get("append", False)
    trailing_newline = params.get("trailing_newline", True)
    leading_newline = params.get("leading_newline", False)

    # 换行簿记在 action 层（对齐 browser-use service.py:1691-1694），
    # 但 trailing 采用守卫式（幂等、不双换行、不破坏 CRLF），见"关键差异"第 6 条。
    if leading_newline:
        content = "\n" + content
    if trailing_newline and not content.endswith("\n"):
        content = content + "\n"

    mode = "a" if append else "w"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # newline="" 关闭 Windows 文本模式 \n→\r\n 翻译：否则写出字节 ≠ 字节数回显、
        # 且显式 \r\n 内容被破坏。LF 行尾跨平台一致，对齐 save_as_pdf 二进制写。
        with open(path, mode, encoding="utf-8", newline="") as f:
            f.write(content)
    except OSError as e:
        # 分级错误：磁盘/权限/路径异常 → 明确 error + warning（对齐 save_as_pdf:980-985），
        # 不冒泡到 Tools.execute 的通用 catch（actions.py:260-262）。
        logger.warning("write_file(%r) failed: %s", path, e)
        return ActionResult(error=f"Failed to write file {path}: {e}")

    written = len(content.encode("utf-8"))
    action_word = "Appended" if append else "Wrote"
    memory = f"{action_word} {written} bytes to {path}"
    logger.info(memory)
    return ActionResult(extracted_content=memory, long_term_memory=memory)
```

**阶段一关键决策（压测确认）**：

| 决策点 | 结论 | 理由 |
|---|---|---|
| append 缺失文件 | 自动创建（Python `'a'` 原生） | 无沙箱；append 比 overwrite 弱会混淆 LLM；建日志场景需要 |
| `trailing_newline` | 守卫式 `not endswith("\n")` | 避免双换行；CRLF（`"foo\r\n"`）天然不被破坏 |
| `leading_newline` + append | 不自动修复既有文件尾 | 保持 O(1) append；由 description 警告 LLM 仅在已知缺尾换行时用 |
| 空内容 | 全部组合合法（`""`+`trailing=True`→写 `"\n"`；`""`+`trailing=False`→截断为空） | 截断/建空文件是合理用法 |
| 字节数 | `len(content.encode("utf-8"))`，换行簿记**之后**计算 | CJK 准确（"你好"=6 字节），与 `os.path.getsize` 一致 |
| 行尾翻译 | `open(..., newline="")` 关闭 Windows `\n→\r\n` | 默认文本模式会把 `\n` 译成 `\r\n`，导致写出字节 ≠ 字节数回显、显式 `\r\n` 被破坏成 `\r\r\n`；LF 跨平台一致 |
| `long_term_memory` | 设置（与 `extracted_content` 相等） | 对齐 navigate/click/find_elements/evaluate/upload_file 主流约定 |
| `success` | 保持 `None` | `ActionResult` 校验器（`views.py:18-25`）拒绝非 done 的 `success=True` |
| `_LOOP_EXEMPT_ACTIONS` | 不加 write_file | `loop_detector`（`loop_detector.py:21-22`）仅剥离 `text`/`clear`，`content` 不剥离 → 不同内容不同 hash 不误判；同 path+同 content 的字面重复本就该被循环检测揪出 |

### 1.3 `ACTION_DEFINITIONS["write_file"]` 描述更新（`models.py:285`，4 空格）

before：

```python
"write_file": (WriteFileParams, "Write content to a local file", False),
```

after：

```python
"write_file": (
    WriteFileParams,
    "Write UTF-8 text to a local file (parent directories are "
    "auto-created). Default is overwrite: the file's previous content is "
    "fully replaced. Set append=True to add to the end of an existing "
    "file instead (it is created if missing). trailing_newline (default "
    "True) ensures the content ends with exactly one newline — no-op if "
    "it already does; set leading_newline=True only when appending to a "
    "file you know lacks a trailing newline, to separate the new content. "
    "Prefer replace_file for in-place edits to a small region of a large "
    "file you have already read.",
    False,
),
```

> 多句描述（对齐 `EvaluateParams`/`FindElementsParams` 风格）：点明默认覆盖、append 自动建文件、`leading_newline` 的使用前提（避免双空行坑），并引导 LLM 小范围改动改用 `replace_file`。`terminates_sequence=False`（无浏览器交互，与 read_file/replace_file/upload_file 一致）。

### 1.4 新增 `tests/test_write_file.py`（TAB 缩进，对齐 `tests/test_save_as_pdf.py`）

入口走 `Tools().execute("write_file", {...}, browser)`（经 registry + `_normalize`，非直调 `_action_*`）；`browser` 用 `MagicMock()`（write_file 不碰浏览器）；`tmp_path` 做文件系统隔离；每个异步测试标 `@pytest.mark.asyncio`（项目无全局 `asyncio_mode`）。

```python
"""Tests for write_file: overwrite, append, newline bookkeeping,
OSError mapping, success echo, UTF-8 round-trip, param validation.

Mirrors tests/test_save_as_pdf.py: Tools().execute(...) entry point,
tmp_path for FS isolation, TAB indentation per CLAUDE.md.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from tree_walker.tools.actions import Tools
from tree_walker.tools.models import WriteFileParams
```

测试类与方法（约 30 个）：

- **TestWriteFileOverwrite**
	- `test_default_overwrites_existing_file`：既有文件 + 默认 → 内容被整体替换
	- `test_creates_new_file`：path 不存在 → 自动创建
	- `test_creates_parent_directories`：`tmp_path/sub/deep/out.txt` → 父目录自动建
	- `test_overwrite_replaces_partial_content`：先写 `"hello world"` 再写 `"bye"` → 文件为 `"bye\n"`
- **TestWriteFileAppend**
	- `test_append_to_existing_file`：既有 `"a\n"`，append `"b"` → `"a\nb\n"`
	- `test_append_to_nonexistent_file_creates_it`：append=True 对不存在 path → 文件被创建（Python `'a'` 语义，见决策表）
	- `test_append_does_not_overwrite`：既有 `"keep me"`，append `"more"` → 原内容保留
- **TestTrailingNewline**
	- `test_trailing_newline_default_appends_one`：`content="hi"`（无 `\n`）默认 → `"hi\n"`
	- `test_trailing_newline_idempotent_on_existing`：`content="hi\n"` → 不变（守卫式，不双换行）
	- `test_trailing_newline_false_no_append`：`trailing_newline=False`, `content="hi"` → `"hi"`（无 `\n`）
	- `test_trailing_newline_preserves_crlf`：`content="hi\r\n"` → `"hi\r\n"`（`endswith("\n")` 对 CRLF 为 True）
- **TestLeadingNewline**
	- `test_leading_newline_prepends`：`content="x"`, `leading_newline=True` → `"\nx\n"`
	- `test_leading_newline_false_default`：默认 → 不前置换行
	- `test_leading_plus_trailing`：`leading=True, trailing=True, content="x"` → `"\nx\n"`
- **TestEmptyContent**
	- `test_empty_content_overwrite_truncates`：既有 `"data"`，写 `""` + `trailing_newline=False` → `""`
	- `test_empty_content_with_trailing_newline`：写 `""` 默认 `trailing_newline=True` → `"\n"`
	- `test_empty_content_append`：既有 `"x\n"`，append `""` + `trailing_newline=False` → `"x\n"`
- **TestWriteFileErrorMapping**
	- `test_write_to_directory_returns_error`：`path=tmp_path`（目录） → `ActionResult(error=…)`
	- `test_error_message_includes_path`：error 文本含 `"Failed to write file {path}"`
	- `test_error_does_not_set_extracted_content`：error 时 `extracted_content is None`（对齐 `save_as_pdf`）
- **TestWriteFileEcho**
	- `test_extracted_content_includes_byte_count`：`"Wrote N bytes"`，N == `len(content.encode())`
	- `test_extracted_content_includes_path`：path 子串出现
	- `test_append_uses_Appended_word`：`append=True` → `"Appended N bytes"` 而非 `"Wrote"`
	- `test_long_term_memory_equals_extracted_content`：两者都设且相等（主流约定）
	- `test_byte_count_after_newline_bookkeeping`：`content="hi"` 默认 → 3 字节（含补的 `\n`）
	- `test_success_is_none`：`success is None`, `is_done is False`（`ActionResult` 校验器）
	- `test_cjk_byte_count_accurate`：`"你好"` → 回显 6 字节，与 `os.path.getsize` 一致
- **TestWriteFileUtf8RoundTrip**
	- `test_cjk_content_round_trips`：写 `"你好"` → 内置 `open` 读回 `"你好\n"`
	- `test_emoji_content_round_trips`：写 `"hi 🎉"` → 读回一致
	- `test_mixed_ascii_cjk`：`"abc你好"` → 9 字节，内容保持
- **TestWriteFileParamsValidation**
	- `test_defaults_append_false_trailing_true_leading_false`：仅传 path+content → 默认值正确
	- `test_extra_field_forbidden`：`WriteFileParams(path="x", content="y", oops=1)` → `ValidationError`
	- `test_path_required`：缺 path → `ValidationError`
	- `test_content_required`：缺 content → `ValidationError`
	- `test_explicit_append_true`：`append=True` 被接受
	- `test_bool_coercion_not_applied`：`append="true"`（字符串）→ `ValidationError`（严格 bool）

> 对齐 `tests/test_save_as_pdf.py` 的 `Tools().execute(...)` 入口 + `tmp_path` + `MagicMock()` browser 风格；异步测试逐个标 `@pytest.mark.asyncio`。

### 1.5 阶段一文件清单

| 文件 | 改动 | 锚点 |
|---|---|---|
| `src/tree_walker/tools/models.py` | `WriteFileParams` 加 3 个 bool；`ACTION_DEFINITIONS["write_file"]` 描述改多句 | 165-168 / 285 |
| `src/tree_walker/tools/actions.py` | `_action_write_file` 重写（换行簿记 + 分级错误 + 字节回显 + long_term_memory） | 1243-1249 |
| `tests/test_write_file.py` | 新增（TAB 缩进，约 30 测试，9 个类） | 新文件 |
| `docs/Tools技术细节/04_动作清单与CDP映射.md` | 同步 4.24 节：参数表、主要逻辑、注意事项 | 4.24 |

### 1.6 阶段一测试计划

```powershell
# 单文件
uv run python -m pytest tests/test_write_file.py -x -v
# 覆盖率
uv run python -m pytest tests/test_write_file.py --cov=tree_walker.tools.actions --cov-report=term-missing
# 全量回归
uv run python -m pytest tests/ -x -v
```

---

## 阶段二（可选，独立，对齐 / 超越 browser-use 完整能力）

- **原子写**：写 `path + ".tmp"` 再 `os.replace(tmp, path)`，进程中途崩溃不留半个文件；同步改 `replace_file`（现状文档 4.13 已注明"非原子"）。
- **对齐 read_file / replace_file 错误处理与回显**：现仅 catch `FileNotFoundError`（`actions.py:1251-1270`），`PermissionError`/`UnicodeDecodeError`/`IsADirectoryError` 冒泡到通用 catch；`replace_file` 成功还返回裸 `ActionResult()`（无回显）。三者应统一成 write_file 阶段一的 try/except/echo 形状。
- **`encoding` 参数**：默认 UTF-8，可选 `encoding: str | None = None` 以处理 latin-1/cp936 等遗留文件。
- **`allowed_write_paths` 白名单**：镜像 `Tools(allowed_upload_paths=...)`（`actions.py` `__init__`），让宿主 jail 写路径。
- **`newline` 翻译控制**：`open(..., newline=...)` 控制 Windows `\r\n`；当前依赖平台默认，几乎不需要。
- **FileSystem 沙箱**：仅当 TreeWalker 采纳沙箱执行模型时再引入（workspace 目录 + 文件名 sanitize + 扩展名白名单），属重大架构变更。

---

## 风险与回归点

| 风险 | 影响 | 缓解 |
|---|---|---|
| `trailing_newline` 默认 True 改变既有写行为（旧实现不补 `\n`） | 老调用方写出的文件末尾多一个 `\n` | 这是**有意修正**（对齐 POSIX 文本惯例 + browser-use 默认）；若需完全保留旧字节流，传 `trailing_newline=False`；测试 `test_trailing_newline_*` 固化 |
| `leading_newline=True` + 既有文件已带尾换行 → 产生空行 | 追加场景出现空白行 | 由 description 明确"仅在已知缺尾换行时用"；`TestLeadingNewline` 覆盖组合；不自动修既有文件尾以保 O(1) append |
| append 自动创建文件与 browser-use 相反 | 期望 append 报错的老用法不报错 | 决策已记录（"关键差异"第 5 条）；append 比 overwrite 强更直观，测试固化 |
| `OSError` 分级捕获遗漏某子类 | 个别异常仍冒泡到通用 catch | catch `OSError`（`PermissionError`/`FileNotFoundError`/`IsADirectoryError` 均为其子类）；`TestWriteFileErrorMapping` 覆盖目录路径 |
| 新增 3 个 bool 参数胀大 LLM schema | token 成本微增 | 3 个 bool 描述简短；对齐 browser-use；默认即旧行为，LLM 可省略 |
| 改 `WriteFileParams` 影响在跑的 agent 会话 | 旧 params dict 无新字段 | 新字段全有默认值，`params.get(...)` 容错；`extra="forbid"` 仅拒绝**多余**字段，不拒绝**缺失**字段 |
| 未同步现状文档 4.24 节 | 文档与代码漂移 | 文件清单已列入 4.24 节同步项 |

---

## 验证方法

1. `uv run python -m pytest tests/test_write_file.py -x -v` 全绿，覆盖率 ≥85%。
2. `uv run python -m pytest tests/ -x -v` 全量回归无回归（尤其不碰 evaluate/find_elements/search_page/upload_file）。
3. **动作层冒烟**（PowerShell，临时目录）：

   ```powershell
   uv run python -c @'
   import asyncio, os, tempfile
   from tree_walker.tools.actions import Tools
   from unittest.mock import MagicMock
   async def main():
       t = Tools()
       d = tempfile.mkdtemp()
       p = os.path.join(d, "sub", "out.txt")
       r1 = await t.execute("write_file", {"path": p, "content": "hello"}, MagicMock())
       r2 = await t.execute("write_file", {"path": p, "content": "world", "append": True}, MagicMock())
       print(r1); print(r2)
       print(open(p, encoding="utf-8").read())
   asyncio.run(main())
   '@
   ```
   预期：r1 `Wrote 6 bytes to ...\out.txt`、r2 `Appended 6 bytes to ...\out.txt`、文件内容 `hello\nworld\n`、`sub/` 父目录被自动创建。
4. **错误分级冒烟**：把 path 指向一个目录 → `ActionResult(error="Failed to write file ...")`，`extracted_content is None`，日志有 `WARNING ... write_file(...) failed`。
5. **回归对照**：错误分级（`ActionResult(error=…)` + `logger.warning`）、`extracted_content` + `long_term_memory` 双写，均与 `save_as_pdf` / `find_elements` / `evaluate` 同族规范一致。

---

## 验收 checklist（阶段一）

- [ ] `WriteFileParams` 加 `append` / `trailing_newline` / `leading_newline`，`extra="forbid"` 保留（`models.py:165-168`）
- [ ] `_action_write_file` 重写：换行簿记 + `OSError` 分级捕获 + `logger.warning` + 字节回显 + `long_term_memory`（`actions.py:1243-1249`）
- [ ] `ACTION_DEFINITIONS["write_file"]` 描述改多句，`terminates_sequence=False`（`models.py:285`）
- [ ] 新增 `tests/test_write_file.py`（TAB 缩进，9 类约 30 测试，覆盖覆盖/追加/换行/空内容/错误映射/回显/UTF-8/参数校验）
- [ ] `uv run python -m pytest tests/test_write_file.py -x -v` 全绿，覆盖率 ≥85%
- [ ] `uv run python -m pytest tests/ -x -v` 全量回归无回归
- [ ] 同步现状文档 `docs/Tools技术细节/04_动作清单与CDP映射.md` 4.24 节
- [ ] 缩进按文件：`src/` = 4 空格、`tests/test_write_file.py` = TAB
