# read_file 工具优化方案（分阶段）

> 参照 browser-use（`browser_use/tools/service.py:1721-1757` `read_file` 动作体、`browser_use/filesystem/file_system.py:506-721` `read_file_structured`、参数模型自动生成）完善本项目 read_file 工具。
> 相关现状文档：`docs/Tools技术细节/04_动作清单与CDP映射.md` 的 4.12 节（注：该节所引 `actions.py:431-438` 行号已 stale，实际为 `:1277-1284`，实现阶段一并修订）；参考标杆：`browser-use/docs/Tools技术细节/06-动作详解-数据处理与文件.md` 的 22. read_file 节。
> 同族先例：`docs/tools-optimize/write_file.md`（commit `4322a77` / issue #50 / PR #51，**最直接对标**——同为本地文件三件套、同为纯 IO 无 CDP）、`docs/tools-optimize/replace_file.md`（读取侧 `newline=""` + `UnicodeDecodeError` 分支的来源）、`docs/tools-optimize/search_page.md`（`c5db7db`，**soft-miss 模式的来源**）、`docs/tools-optimize/save_as_pdf.md`、`docs/tools-optimize/evaluate.md`（`9a60e9d`）、`docs/tools-optimize/find_elements.md`（`a34a1f9`）。本方案在结构、错误分级、回显规范上全面对齐 write_file / replace_file 阶段一；read_file 是 read_file / write_file / replace_file 三件套里的"读"那一环——write_file.md 阶段二已承诺"对齐 read_file 错误处理与回显"，本方案即兑现该承诺。

---

## 适用场景（什么时候会用到 read_file）

**定位**：把**本地磁盘上的 UTF-8 文本文件**读进 agent 上下文。是三件套里"读"的那一环，与浏览器零交互。write_file / replace_file 的描述都已埋了正向引导（先 read 再 write / replace），本工具是那条工作流的前置。

| 工具 | 职责 | 与 read_file 的区别 |
|---|---|---|
| `read_file` | 读本地文本文件进上下文 | 唯一的"读本地磁盘"出口 |
| `write_file` | 文本写入（覆盖/追加） | read 的反向：read 看现状，write 改整文件 |
| `replace_file` | 文件内字符串替换（全局、字面量） | read 的下游：先 read 定位 `old` 片段，再 replace |
| `extract` | 抽取当前页面文本 | 作用域是浏览器，不是本地 fs |
| `save_as_pdf` | 把当前页面存成 PDF | 写浏览器产物到磁盘，不读 |
| `evaluate` | 页面内执行 JS | 作用域是浏览器，不是本地 fs |

**典型场景**：

1. 读配置/笔记/代码文件，确认内容后再 `write_file`（重写）或 `replace_file`（小改）。
2. 查看 agent 自己刚导出的文件（`save_as_pdf` 的产物、`download` 的文件、`write_file` 的落盘结果）。
3. 读取脚本/数据文件，把内容喂给后续推理或 `evaluate`。
4. 在大文件里定位某段文本（读取后自行查找；阶段二会补 `offset`/`limit` 分页）。

**什么时候不需要它**：

- 要看的是**当前网页**内容 → 用 `extract` / `search_page` / `find_elements`，`read_file` 只读本地磁盘。
- 要读的是**图片 / PDF / DOCX** 等富文档 → 阶段一只支持 UTF-8 文本；富文档解析留阶段二（对齐 browser-use 才有的能力）。
- 只想知道文件**是否存在 / 多大**，不需要内容 → 目前没有独立 `stat` 动作，仍得 `read_file`（空文件会得到明确软提示）。

**可用性提示**：阶段一覆盖 UTF-8 文本读取、Windows 换行字节保真、分级错误（`FileNotFoundError` / 解码失败 / 目录·权限·锁定）、截断回显（字符数 + 字节数 + 截断 footer）、空文件软提示、全量单测；阶段二再补 `offset`/`limit` 分页、`encoding` 参数、二进制嗅探、富文档解析、读路径白名单等（见末尾）。

---

## Context（为什么做这个改动）

当前实现（[`src/tree_walker/tools/actions.py:1277-1284`](../../src/tree_walker/tools/actions.py)）：

```python
async def _action_read_file(self, params: dict, browser: BrowserSession) -> ActionResult:
    path = params["path"]
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        return ActionResult(extracted_content=content[:self._truncation.read_file_max_chars])
    except FileNotFoundError:
        return ActionResult(error=f"File not found: {path}")
```

参数模型（[`src/tree_walker/tools/models.py:186-188`](../../src/tree_walker/tools/models.py)）：

```python
class ReadFileParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(description="File path to read")
```

注册项（[`src/tree_walker/tools/models.py:317`](../../src/tree_walker/tools/models.py)）：`"read_file": (ReadFileParams, "Read content from a local file", False)`

**主要问题**：

1. **只 catch `FileNotFoundError`** —— `PermissionError` / `IsADirectoryError`（`path` 指向目录：Windows 是 `PermissionError`、POSIX 是 `IsADirectoryError`，都是 `OSError` 子类）和 `UnicodeDecodeError`（非 UTF-8 文件）全部冒泡到 `Tools.execute` 通用 catch（[`actions.py:260-262`](../../src/tree_walker/tools/actions.py)），变成无 `read_file` 语义的裸 `ActionResult(error=str(e))` + 一整条 `logger.exception` 堆栈（比同族用的 `logger.warning` 更吵、更没语义）。
2. **缺 `newline=""`** —— `read_file` 是 universal-newline 翻译侧（和 `replace_file` 同病，[`actions.py:1298`](../../src/tree_walker/tools/actions.py) 已修）：读时 `\r\n` 被压成 `\n`，CRLF 内容字节级丢失；若随后被 `replace_file` 用含 `\r\n` 的 `old` 匹配会失配。
3. **无字节数回显、无截断提示** —— 内容被 `[:read_file_max_chars]`（默认 5000，[`config.py:47`](../../src/tree_walker/config.py)）静默截断，LLM 不知道后面还有内容，会把截断处误当成文件结尾——这是 read_file 独有的"截断不可见"缺陷（write/replace 的回显是操作确认，不涉及内容截断）。
4. **不设 `long_term_memory`** —— agent 无法回忆"我已经读过这个文件"，同族 `write_file` / `replace_file` 都双写 `extracted_content` + `long_term_memory`。
5. **空文件语义模糊** —— 空文件返回 `extracted_content=""`，`ActionResult.__str__`（[`views.py:31-36`](../../src/tree_walker/agent/views.py)）对 falsy `extracted_content` 兜底成 `"OK"`，LLM 无法区分"文件存在但为空"与"工具什么都没做"。
6. **零测试** —— `tests/` 下无 `test_read_file.py`，全目录 grep `read_file` 无命中（`write_file.md` 已将此列为覆盖率黑洞）。
7. **description 过简** —— `"Read content from a local file"` 缺编码/类型提示（相对 `write_file` 的 `"Text content to write (UTF-8)."`），LLM 可能拿它去读二进制/富文档。

**参照标杆 browser-use 的做法**：把读取逻辑放进 `FileSystem.read_file_structured()`（`file_system.py:506-721`），动作体（`service.py:1721-1757`）只做编排——**关键思想是 `extracted_content`（全文，`include_extracted_content_only_once=True` 只注入下一步）与 `long_term_memory`（截断摘要，`MAX_MEMORY_SIZE=1000` 逐行截断并附 `"{N} more lines..."`）分离**；错误一律降级成 `message` 字符串（`Error: File '...' not found.` / `Permission denied` / `Could not read ... {e}`）不抛异常。

**预期结果**：read_file 与 write_file / replace_file 阶段一**完全同构**——`newline=""` 字节保真、`FileNotFoundError → UnicodeDecodeError → OSError` 三级错误 + `logger.warning`、截断/字节数双写回显、空文件软提示、全量单测；并补上 read_file 独有的"截断显式告知"。

---

## 工程约束（实施时务必遵守）

- Windows + PowerShell；包用 uv，跑脚本 / 测试用 `uv run python ...`。测试命令 `uv run python -m pytest tests/ -x -v`。
- **缩进按文件**（已复核）：`src/tree_walker/tools/models.py`、`src/tree_walker/tools/actions.py` = **4 空格**；`tests/test_read_file.py` = **TAB**（对齐 `tests/test_replace_file.py`）。
- 改完跑相关单测 + 全量回归；覆盖率目标 >85%。
- 不主动 `git commit` / `git push`。
- `os` / `logger` 在 `actions.py` 已是模块级 import（`os` 在 [`actions.py:10`](../../src/tree_walker/tools/actions.py)、`logger` 在 `:19`）；`Field` / `ConfigDict` 已在 `models.py` import。重写 `_action_read_file` 无需新 import。

---

## 与 browser-use 的关键差异（有意为之，不照搬）

1. **不移植 `FileSystem` 沙箱 / `available_file_paths` 白名单 / 内外部文件双路径。** browser-use 把文件分成内部沙箱（内存 dict `self.files`）和外部 OS 文件（须在用户白名单 `available_file_paths` 里），两套完全不同的读路径（`file_system.py:506-721`）。本项目同 write_file / replace_file，**保留裸 `path` 直读本地磁盘**，无沙箱、无白名单——路径由 LLM 给，agent 责任制（与差异同族 #1 一致）。
2. **不移植 PDF(`pypdf`) / DOCX(`python-docx`) / 图片(base64) 解析。** browser-use 按扩展名分派：PDF 还有 IDF 加权分页截断（`MAX_CHARS=60000`，`file_system.py:551`）、DOCX 抽段落、图片转 base64 走 `images` 通道。阶段一**只读 UTF-8 文本**；富文档 / 二进制留阶段二。
3. **不移植扩展名白名单与文件名 sanitize。** browser-use 用 `_build_filename_error_message` + `UNSUPPORTED_BINARY_EXTENSIONS`（`file_system.py:15-37,40-73`）在动作里拦截。本项目不做扩展名校验，交给 `UnicodeDecodeError` / `OSError` 兜底（二进制嗅探留阶段二）。
4. **`newline=""` 关闭 universal-newline（Windows 必需，平台修正）。** browser-use 在 Linux 跑（LF 环境）无此问题；Windows 上 Python 文本读默认把 `\r\n` 译成 `\n`，会破坏 CRLF 内容字节——`read_file` 是翻译侧，与 `replace_file`（[`actions.py:1298`](../../src/tree_walker/tools/actions.py)）同病同治。
5. **截断须显式告知（有意超越 browser-use）。** browser-use 对文本文件**不限长**（全文进 `extracted_content`，仅 `long_term_memory` 截到 1000 字符），靠 `include_extracted_content_only_once=True` 只注入一次，所以无需告知截断。本项目受 LLM 上下文约束**必须截断内容本身**（`read_file_max_chars` 默认 5000），因此**必须**在 `extracted_content` 末尾追加 `[...truncated]` footer，否则 LLM 会把截断处误当文件结尾——这是 browser-use 没有、TreeWalker 特有的回显需求。
6. **错误用 `ActionResult(error=...)` 而非字符串返回。** browser-use 把错误塞进 `result['message']` 当内容返回给 LLM；本项目统一降级成 `ActionResult(error=...)`（`extracted_content is None`）+ `logger.warning`，对齐 save_as_pdf / find_elements / evaluate / write_file / replace_file，不冒泡到 `Tools.execute` 通用 catch。

---

## 阶段一：换行修复 + 分级错误 + 截断回显 + 空文件软提示 + 测试（优先做，风险低）

### 1.1 `ReadFileParams` 富化（`models.py:186-188`，4 空格）

`before:`

```python
class ReadFileParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(description="File path to read")
```

`after:`

```python
class ReadFileParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(description="Path to a local UTF-8 text file to read.")
```

> 不加 `offset` / `limit` / `line_range` / `encoding`（留阶段二）。阶段一保持单字段 `path`，对齐 browser-use 暴露面（read_file 无分页，分页归单独的 `extract` 动作）+ 同族"阶段一低风险"原则。仅把 description 从 `"File path to read"` 富化为 `"Path to a local UTF-8 text file to read."`，明示编码与文本类型，对齐 write_file 的 `"(UTF-8)"` 提示，避免 LLM 拿它去读二进制 / 富文档。

### 1.2 `_action_read_file` 重写（`actions.py:1277-1284`，4 空格）

`before:` 见 [Context](#context为什么做这个改动) 节。

`after:`

```python
async def _action_read_file(self, params: dict, browser: BrowserSession) -> ActionResult:
    path = params["path"]
    try:
        # newline="" 关闭 universal-newline 翻译（对齐 replace_file:1298 / write_file:1263）：
        # 读时保留原始 \r\n，避免 CRLF 被压成 \n；行尾字节级不变，便于后续 replace_file
        # 用含 \r\n 的 old 精确匹配。
        with open(path, "r", encoding="utf-8", newline="") as f:
            content = f.read()
    except FileNotFoundError:
        return ActionResult(error=f"File not found: {path}")
    except UnicodeDecodeError as e:
        # 读取侧特有（对齐 replace_file:1313-1316）：文件不是合法 UTF-8。
        logger.warning("read_file(%r) decode failed: %s", path, e)
        return ActionResult(error=f"Failed to decode {path} as UTF-8: {e}")
    except OSError as e:
        # 分级错误（对齐 replace_file:1317-1321）：path 指向目录(IsADirectoryError/
        # PermissionError)、磁盘/只读/锁定 → 明确 error + warning，不冒泡到
        # Tools.execute 通用 catch（actions.py:260-262）。
        logger.warning("read_file(%r) failed: %s", path, e)
        return ActionResult(error=f"Failed to read file {path}: {e}")

    total_chars = len(content)
    total_bytes = len(content.encode("utf-8"))
    max_chars = self._truncation.read_file_max_chars
    if not content:
        # 软提示（对齐 replace_file:1301-1307 / search_page）：空文件不是错误，
        # 但要让 LLM 知道"文件存在且为空"，而非 __str__ 兜底成 "OK"。
        msg = f"{path} is empty (0 bytes)"
        logger.info(msg)
        return ActionResult(extracted_content=msg, long_term_memory=msg)
    if total_chars > max_chars:
        extracted = (
            content[:max_chars]
            + f"\n[...truncated: showing {max_chars} of {total_chars} chars "
            f"({total_bytes} bytes total)]"
        )
        memory = (
            f"Read {path} ({total_chars} chars, {total_bytes} bytes; "
            f"truncated to first {max_chars} chars)"
        )
    else:
        extracted = content
        memory = f"Read {path} ({total_chars} chars, {total_bytes} bytes)"
    logger.info(memory)
    return ActionResult(extracted_content=extracted, long_term_memory=memory)
```

**阶段一关键决策（压测确认）**：

| 决策点 | 结论 | 理由 |
|---|---|---|
| 是否新增参数（offset/limit/line_range） | ❌ 不新增 | 对齐 browser-use（read_file 无分页）+ 同族"阶段一低风险"；分页留阶段二 |
| `open()` 加 `newline=""` | ✅ | `read_file` 是 universal-newline 翻译侧，与 `replace_file:1298` 同病；CRLF 字节级保真 |
| except 顺序 | `FileNotFoundError` → `UnicodeDecodeError` → `OSError` | `FileNotFoundError` 是 `OSError` 子类须在前；`UnicodeDecodeError` 非 `OSError` 须独立；`OSError` 兜底目录/权限/锁定（对齐 `replace_file:1311-1321`） |
| 错误是否冒泡通用 catch | ❌ 全部在 action 内降级为 `ActionResult(error=...)` + `logger.warning` | 对齐 save_as_pdf / find_elements / evaluate / write_file / replace_file |
| 内容截断是否告知 LLM | ✅ 末尾追加 `\n[...truncated: showing {max} of {total} chars ({bytes} bytes total)]` | TreeWalker 必须截内容（不同于 browser-use），LLM 必须知道还有更多——read_file 独有回显 |
| 字节数计算 | `len(content.encode("utf-8"))`（截断前算总量） | CJK 准确（"你好"=6 字节），与 `os.path.getsize` 一致（对齐 write_file / replace_file 字节回显） |
| 截断阈值来源 | `self._truncation.read_file_max_chars`（默认 5000，env `AGENT_TRUNCATE_READ_FILE` 可覆盖） | 复用现有 `TruncationSettings`（[`config.py:47`](../../src/tree_walker/config.py)），不引入新配置 |
| 空文件处理 | 软提示 `"{path} is empty (0 bytes)"`，双写 `extracted_content`+`long_term_memory`，非 error | 对齐 `replace_file` soft-miss（[`actions.py:1301-1307`](../../src/tree_walker/tools/actions.py)）；避免 `__str__` 兜底成 `"OK"` 的歧义 |
| `long_term_memory` | ✅ 设置（短回显，不截断） | 对齐 write/replace 双写；让 agent 回忆已读 |
| `success` | `None` | `ActionResult` 校验器（[`views.py:18-25`](../../src/tree_walker/agent/views.py)）禁止非 done 动作设 `success=True` |

### 1.3 `ACTION_DEFINITIONS["read_file"]` description 更新（`models.py:317`，4 空格）

`before:`

```python
    "read_file": (ReadFileParams, "Read content from a local file", False),
```

`after:`

```python
    "read_file": (ReadFileParams, "Read content from a local UTF-8 text file.", False),
```

> `terminates_sequence` 保持 `False`：读文件不终止 agent 序列。description 与 1.1 的 param description 对齐，明示 UTF-8 文本，避免 LLM 拿去读二进制 / 富文档。

### 1.4 新增 `tests/test_read_file.py`（TAB 缩进，对齐 `tests/test_replace_file.py`）

文件头：

```python
"""Tests for read_file: UTF-8 text read, newline byte-fidelity, truncation echo,
empty-file soft-miss, tiered error mapping (NotFound / decode / dir-perm), echo.
Mirrors tests/test_replace_file.py: Tools().execute(...) entry point, tmp_path for
FS isolation, newline="" byte-exact helpers, TAB indentation per CLAUDE.md."""

from __future__ import annotations
from unittest.mock import MagicMock
import pytest
from pydantic import ValidationError
from tree_walker.tools.actions import Tools
from tree_walker.tools.models import ReadFileParams
```

入口与字节级 helper（对齐 `tests/test_replace_file.py`）：

```python
async def _run(params: dict):
    """Drive read_file through the public Tools().execute entry point."""
    tools = Tools()
    return await tools.execute("read_file", params, MagicMock())


def _seed(path, text: str) -> None:
    """Write a file with newline="" so LF/CRLF bytes are byte-exact on disk."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def _read(path) -> str:
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()
```

**测试类**（每组一个 class，`@pytest.mark.asyncio async def`，`tmp_path` 隔离）：

- **TestReadFileBasic**
    - `test_reads_text_content` —— 读普通文本，`extracted_content == 文件内容`。
    - `test_success_is_none_is_done_false` —— `r.success is None`、`r.is_done is False`。
    - `test_cjk_round_trip` —— 读 `"你好\n"`，`extracted_content == "你好\n"`。
- **TestReadFileNewline**
    - `test_lf_file_preserved` —— LF 文件返回内容保持 `\n`。
    - `test_crlf_file_preserved` —— CRLF 文件返回内容含 `\r\n`（断言 `"\r\n" in r.extracted_content`）。
    - `test_crlf_literal_round_trip` —— 内容里含 `\r\n` 字面量，读回不变。
- **TestReadFileTruncation**
    - `test_over_max_chars_gets_footer` —— 写 >`read_file_max_chars` 字符文件，`extracted_content` 含 `"[...truncated: showing {max} of {total} chars"` 且前缀 == `content[:max]`。
    - `test_memory_mentions_truncated` —— 同上，`long_term_memory` 含 `"truncated"`。
    - `test_at_exactly_max_chars_no_footer` —— 恰好等于上限 → 无 footer、无 `"truncated"`。
- **TestReadFileEmpty**
    - `test_empty_file_is_soft_miss` —— 空文件 → `r.extracted_content == r.long_term_memory == "{path} is empty (0 bytes)"`，`r.error is None`。
    - `test_empty_file_not_ok` —— `str(r)` 含 `"EXTRACTED:"`，不是裸 `"OK"`。
- **TestReadFileErrorMapping**
    - `test_file_not_found` —— 不存在路径 → `r.error` 含 `"File not found"` + path。
    - `test_path_is_directory_returns_oserror` —— `path` 指向 `tmp_path`（目录）→ `r.error` 含 `"Failed to read file"`（跨平台：POSIX `IsADirectoryError` / Windows `PermissionError`，都是 `OSError` 子类）。
    - `test_non_utf8_file_returns_decode_error` —— GBK 写入 `"你好".encode("gbk")` → `r.error` 含 `"UTF-8"`。
    - `test_error_no_extracted_content` —— 任一错误分支 → `r.extracted_content is None`。
- **TestReadFileEcho**
    - `test_memory_has_char_and_byte_counts` —— 非空文件 `long_term_memory` 形如 `"Read {path} (N chars, M bytes)"`。
    - `test_byte_count_matches_disk` —— `long_term_memory` 里字节数 == `os.path.getsize(path)`。
    - `test_cjk_byte_count_accurate` —— `"你好\n"` → 7 字节（6 + 1）。
- **TestReadFileParamsValidation**（同步，无 asyncio mark）
    - `test_accepts_path` —— `ReadFileParams(path="x")` OK。
    - `test_extra_forbidden` —— `ReadFileParams(path="x", offset=0)` 抛 `ValidationError`。
    - `test_path_required` —— `ReadFileParams()` 抛 `ValidationError`。

### 1.5 阶段一文件清单

| 文件 | 改动 | 锚点 |
|---|---|---|
| `src/tree_walker/tools/models.py` | 富化 `ReadFileParams.path` description（含 UTF-8）；更新 `ACTION_DEFINITIONS["read_file"]` description | `:186-188`、`:317` |
| `src/tree_walker/tools/actions.py` | 重写 `_action_read_file`：`newline=""` + 三级错误 + 截断/字节回显 + 空文件软提示 | `:1277-1284` |
| `tests/test_read_file.py` | 新增（7 个测试类，TAB 缩进） | 新文件 |
| `docs/Tools技术细节/04_动作清单与CDP映射.md` | 修订 4.12 节 stale 行号 `actions.py:431-438` → `:1277-1284`（+同步新行为描述） | 4.12 节 |

### 1.6 阶段一测试计划

```powershell
uv run python -m pytest tests/test_read_file.py -x -v
uv run python -m pytest tests/test_read_file.py --cov=tree_walker.tools.actions --cov-report=term-missing
uv run python -m pytest tests/ -x -v
```

---

## 阶段二（可选，独立，对齐 / 超越 browser-use 完整能力）

- **`offset` / `limit` / `line_range` 分页** —— browser-use 把分页归单独的 `extract` 动作；本项目可在 `read_file` 内补，让 LLM 翻读超大文件（与阶段一的截断 footer 配合："已读前 5000 字符，用 offset=5000 读后续"）。
- **`encoding` 参数** —— 显式指定 `latin-1` / `cp936` 等兜底读非 UTF-8 文本（替代直接 `UnicodeDecodeError`）。
- **二进制嗅探** —— peek 文件头 magic bytes（或扩展名），拒读 `.png` / `.exe` / `.zip` 等，给可操作提示（对齐 browser-use `UNSUPPORTED_BINARY_EXTENSIONS` 思路，但不移植其沙箱）。
- **`allowed_read_paths` 白名单** —— 与 write_file / replace_file 一起做安全收敛（三件套统一）。
- **富文档解析** —— PDF(`pypdf`，可借鉴其 IDF 加权分页) / DOCX(`python-docx`) / 图片(base64 走 `images` 通道)，对齐 browser-use `read_file_structured` 的扩展名分派。
- **`FileSystem` 沙箱** —— 若决定引入内部/外部文件双路径，三件套一起设计（当前明确不移植，见差异 #1）。

---

## 风险与回归点

| 风险 | 影响 | 缓解 |
|---|---|---|
| 截断 footer 改变 `extracted_content` 形状 | LLM 若对读到的内容做精确后缀判断（如"文件应以 X 结尾"），footer 会干扰 | footer 用醒目 `[...truncated]` 前缀，LLM 易识别剔除；且仅 >`read_file_max_chars`（默认 5000）时才出现，常规小文件不受影响 |
| `newline=""` 使 CRLF 文件内容现带 `\r` | 依赖旧"被压成 `\n`"行为的调用方 / 测试可能受影响 | 这是**修正**（字节保真），非回归；与 `replace_file` / `write_file` 已统一，`test_crlf_file_preserved` 固化该行为 |
| 新增 `UnicodeDecodeError` 分支 | 原本能"读出乱码"的非 UTF-8 文件现在返回 error | 预期行为：乱码对 LLM 无价值，明确报错 + `encoding` 建议（阶段二）更可操作；对齐 `replace_file` |
| `long_term_memory` 新增字段 | 历史 agent 会话不期望该字段 | `long_term_memory` 本就是 `ActionResult` 既有可选字段（[`views.py:15`](../../src/tree_walker/agent/views.py)），新增值不影响序列化兼容 |

---

## 验证方法

1. **单测三连**（见 1.6）：单文件、覆盖率、全量回归。
2. **动作层冒烟**（PowerShell here-string，走 `Tools().execute`）：
    - 读文本：构造 tmp 文件 → `execute("read_file", {"path": p}, ...)` → `extracted_content` round-trip、`long_term_memory` 含 `"Read {p} (N chars, M bytes)"`。
    - 读超长文件：写 >5000 字符文件 → `extracted_content` 末尾有 `"[...truncated: showing 5000 of N chars"`、`long_term_memory` 含 `"truncated"`。
    - 读目录：`path = tmp_path` → `error` 含 `"Failed to read file"`、`extracted_content is None`。
    - 读非 UTF-8：写 GBK 字节 → `error` 含 `"UTF-8"`。
    - 读空文件：`extracted_content == long_term_memory == "{p} is empty (0 bytes)"`、`error is None`。
3. **回归对照**：分级错误 + 回显形态与 `save_as_pdf` / `find_elements` / `evaluate` / `replace_file` 一致——错误不冒泡到 `Tools.execute` 通用 catch（[`actions.py:260-262`](../../src/tree_walker/tools/actions.py)），日志是 `logger.warning` 而非 `logger.exception`。

---

## 验收 checklist（阶段一）

- [ ] `_action_read_file` 加 `newline=""`，CRLF 字节保真
- [ ] 分级错误：`FileNotFoundError` → `UnicodeDecodeError` → `OSError`，全 `logger.warning`，不冒泡通用 catch
- [ ] 截断回显：>`read_file_max_chars` 时 `extracted_content` 带 `[...truncated]` footer、`long_term_memory` 带 `"truncated"`
- [ ] 空文件软提示：双写 `"{path} is empty (0 bytes)"`，非 error，`str(r)` 非 `"OK"`
- [ ] 非空文件 `long_term_memory` 含字符数 + 字节数，字节数 == `os.path.getsize`
- [ ] `ReadFileParams.path` description 富化（含 UTF-8）；`ACTION_DEFINITIONS["read_file"]` description 同步
- [ ] `tests/test_read_file.py` 新增，7 个测试类全过（TAB 缩进）
- [ ] 覆盖率 >85%，`uv run python -m pytest tests/ -x -v` 全量回归无破
- [ ] 修订 `04_动作清单与CDP映射.md` 4.12 节 stale 行号
