# replace_file 工具优化方案（分阶段）

> 参照 browser-use（`browser_use/tools/service.py:1713-1719` `replace_file` 动作体、`browser_use/filesystem/file_system.py:776-802` `replace_file_str`、参数模型自动生成）完善本项目 replace_file 工具。
> 相关现状文档：`docs/Tools技术细节/04_动作清单与CDP映射.md` 的 4.13 节；参考标杆：`browser-use/docs/Tools技术细节/06-动作详解-数据处理与文件.md` 的 21. replace_file 节。
> 同族先例：`docs/tools-optimize/write_file.md`（commit `4322a77` / issue #50 / PR #51，**最直接对标**——同为本地文件三件套、同为纯 IO 无 CDP）、`docs/tools-optimize/evaluate.md`（`9a60e9d`）、`docs/tools-optimize/find_elements.md`（`a34a1f9`）、`docs/tools-optimize/search_page.md`（`c5db7db`，**soft-miss 模式的来源**）。本方案在结构、错误分级、回显规范上全面对齐 write_file 阶段一；replace_file 是 read_file / write_file / replace_file 三件套里的"就地编辑"那一环。

---

## 适用场景（什么时候会用到 replace_file）

**定位**：对**已存在的本地文件**做**小范围就地字符串替换**，不重写整文件。是三件套里"改"的那一环，与浏览器零交互。write_file 的描述已埋了反向引导（`"Prefer replace_file for in-place edits to a small region of a large file you have already read."`，`models.py:309`），本工具补齐这条承诺。

| 工具 | 职责 | 与 replace_file 的区别 |
|---|---|---|
| `replace_file` | 文件内字符串替换（全局、字面量） | 唯一的"就地编辑"出口 |
| `write_file` | 文本写入（覆盖/追加） | 整文件写；改大文件的小区域用 replace_file 更安全 |
| `read_file` | 读本地文件 | replace_file 的前置：先 read 确认 old 片段，再 replace |
| `evaluate` | 页面内执行 JS | 作用域是浏览器，不是本地 fs |

**典型场景**：

1. 改配置/笔记里的某一处值（`old="debug=false"` → `new="debug=true"`），不动文件其余部分。
2. 批量替换文档里的某个词（全局替换所有匹配）。
3. 勾选/反勾选 todo 项（对齐 browser-use description 里的 "updating todo checkboxes"）。
4. 修代码里的某一行（先 read_file 定位，再 replace_file 改）。

**什么时候不需要它**：

- 要改的幅度很大、或要重排大部分内容 → 直接 `write_file(overwrite)` 重写整文件。
- 要改的是当前网页内容 → replace_file 只动本地磁盘，不碰浏览器。

**可用性提示**：阶段一覆盖全局字面量替换、UTF-8 读写、Windows 换行保持、匹配计数回显、未命中的软提示、空 `old` 拦截、分级错误、全量单测；阶段二再补原子写、`count`（限制替换次数）、`regex`/`case_sensitive`、写路径白名单等（见末尾）。

---

## Context（为什么做这个改动）

当前实现（[`src/tree_walker/tools/actions.py:1286-1296`](../../src/tree_walker/tools/actions.py)）：

```python
async def _action_replace_file(self, params: dict, browser: BrowserSession) -> ActionResult:
    path = params["path"]
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        content = content.replace(params["old"], params["new"])
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return ActionResult()
    except FileNotFoundError:
        return ActionResult(error=f"File not found: {path}")
```

参数模型（[`src/tree_walker/tools/models.py:191-195`](../../src/tree_walker/tools/models.py)）：

```python
class ReplaceFileParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(description="File path")
    old: str = Field(description="Text to find and replace")
    new: str = Field(description="Replacement text")
```

注册项（[`src/tree_walker/tools/models.py:314`](../../src/tree_walker/tools/models.py)）：`"replace_file": (ReplaceFileParams, "Replace text within a local file", False)`。

**主要问题**：

1. **Windows 换行被破坏（真实 bug，write_file 已修但 replace_file 漏修）**：裸 `open(path, "r")` 读时启用 universal-newline，`\r\n`→`\n`；裸 `open(path, "w")` 写时又把 `\n`→`\r\n`。后果：① 原 LF 文件被改成 CRLF（污染 git diff）；② 若 `old`/`new` 含 `\r\n` 字面量，读时被压缩成 `\n`，匹配失败或内容损坏。write_file 阶段一已用 `newline=""` 解决（[`actions.py:1263`](../../src/tree_walker/tools/actions.py)），replace_file 必须同步。
2. **成功无回显**：`return ActionResult()` 是空的，不设 `extracted_content` / `long_term_memory`，LLM 无法得知"替换了几处、最终文件多大、到底改没改"。同族 write_file（字节数）、evaluate、find_elements、search_page 全都有回显。
3. **静默未匹配（browser-use 遗留缺陷，本项目需修正）**：`str.replace(old, new)` 在 `old` 不存在于文件时原样写回，仍走"成功"分支。LLM 以为改了其实没改——这是 browser-use 也有的坑（它仍返回 `"Successfully replaced all occurrences"`）。同族 search_page 已用 soft-miss 模式修正（[`actions.py:1332-1340`](../../src/tree_walker/tools/actions.py)），replace_file 应对齐：`count==0` 时回一个"未命中、文件未改动"的提示，而非假装成功。
4. **`old` 允许空串（边界 bug）**：`ReplaceFileParams.old` 无 `min_length=1` 约束，`str.replace("", new)` 会在原串每个字符间插入 `new`（`"ab".replace("", "x") == "xaxbx"`），静默膨胀文件。browser-use 显式 `if not old_str: return 'Error: Cannot replace empty string'`。
5. **错误覆盖窄**：只 catch `FileNotFoundError`；`PermissionError` / `IsADirectoryError`（path 指向目录）/ `UnicodeDecodeError`（文件非 UTF-8，**replace_file 读取侧特有，write_file 不会有**）/ 磁盘满 全部冒泡到 `Tools.execute` 通用 catch（[`actions.py:260-262`](../../src/tree_walker/tools/actions.py)），变成无上下文的 `error=str(e)`，LLM 拿不到"哪个文件、什么错"。
6. **无 `logger.warning`**：失败无日志（同族 save_as_pdf / find_elements / evaluate / write_file 均有）。
7. **描述过简**：`"Replace text within a local file"` 一句话，没说全局替换、大小写敏感、纯字面量（非正则）、`old` 不能为空、与 write_file 的分工。
8. **零测试**：`tests/` 下无 `test_replace_file.py`（59 个测试文件零命中 replace_file），覆盖率黑洞——24 个 action 里的明显盲区。
9. **非原子写**：写到一半进程挂掉会留下半个文件（阶段二再修，同 write_file）。

**参照标杆 browser-use 的做法**（`browser_use/filesystem/file_system.py:776-802`）：`replace_file_str` 做 `read → content.replace(old_str, new_str) → write`，显式拦截空 `old_str`，错误全部以字符串形式返回（不抛异常），回显 `"Successfully replaced all occurrences of ..."`。browser-use 额外有一套 `FileSystem` 沙箱（`_resolve_filename` basename 防 traversal、扩展名白名单、`sanitize_filename`、内存+磁盘双层），**TreeWalker 无此基础设施且不打算引入**（见"关键差异"，与 write_file 差异 #1 完全一致）。

**预期结果**：阶段一为 replace_file 给 `old` 加 `min_length=1`、读写改用 `newline=""` 修复 Windows 换行、补齐 `FileNotFoundError`/`UnicodeDecodeError`/`OSError` 三级捕获 + `logger.warning`、对未命中走 soft-miss 软提示（修正 browser-use 静默缺陷）、命中时回显"替换次数 + 最终字节数"并同步 `long_term_memory`、精修 description、新增 `tests/test_replace_file.py` 覆盖率 ≥85%，并同步修正现状文档 4.13 节（含过时行号 `actions.py:440-450` → 实际行号）。

---

## 工程约束（实施时务必遵守）

- Windows + PowerShell；包用 uv，跑脚本 / 测试用 `uv run python ...`。测试命令 `uv run python -m pytest tests/ -x -v`。
- **缩进按文件**（已复核）：`src/tree_walker/tools/models.py`、`src/tree_walker/tools/actions.py` = **4 空格**；`tests/test_replace_file.py` = **TAB**（对齐 `tests/test_write_file.py`）。
- 改完跑相关单测 + 全量回归；覆盖率目标 >85%。
- 不主动 `git commit` / `git push`。
- `os` / `logger` 在 `actions.py` 已是模块级 import；`Field` / `ConfigDict` / `min_length` 已在 `models.py` 可用（Pydantic v2 `Field(min_length=1)`）。新增代码无需新 import。

---

## 与 browser-use 的关键差异（有意为之，不照搬）

1. **不移植 `FileSystem` 沙箱。** browser-use 用 `file_name`（纯文件名）+ `FileSystem`（workspace 目录、`_resolve_filename` basename 防 traversal、`_is_valid_filename` 扩展名白名单、`sanitize_filename`、内存 `self.files` + `sync_to_disk` 双层）。TreeWalker 用裸 `path`（绝对/相对路径）直写磁盘，read_file / write_file 全家一致；移植沙箱是重大架构变更，**阶段二再议**（与 read_file/write_file 一起）。保留 `path` 参数名。
2. **不移植 `file_name` sanitize / `auto-corrected` 提示。** browser-use 的 `_resolve_filename` 会把 `my$notes.txt` 清洗成 `mynotes.txt` 并回显 `(auto-corrected from ...)`。TreeWalker 走真实路径，不做文件名改写——传什么路径就是什么路径，错了直接报错。
3. **不移植扩展名白名单 / `_build_filename_error_message`。** 同 write_file 差异 #2：`content` 天然是文本，无二进制路径。
4. **不移植 `file_system` 特殊参数注入。** browser-use 经 registry 把 `FileSystem` 实例注入 action。TreeWalker 无 FileSystem，`Tools` 直接 `open()`。
5. **`old` 空串在 Pydantic schema 层拦截（非运行时）。** browser-use 在 `replace_file_str` 里 `if not old_str: return 'Error: ...'`。本方案改为 `Field(min_length=1)`，在 Params 校验阶段直接 `ValidationError`——更早、LLM 立刻看到字段约束，不占用一次工具往返。
6. **修正 browser-use 的"静默未匹配"缺陷（有意超越）。** browser-use 在 `old_str` 未命中时仍返回 `"Successfully replaced all occurrences"`，误导 LLM。本方案对齐同族 search_page 的 soft-miss：`count == 0` 时返回 `extracted_content == long_term_memory == "No occurrences of {old!r} found in {path}; file unchanged"`，明确告知"没匹配到、文件没动"，让 LLM 决定是否调整 `old` 或改用 write_file。
7. **回显"替换次数 + 字节数"，而非 browser-use 的笼统成功串。** 对齐 write_file 的字节数回显（[`actions.py:1271-1273`](../../src/tree_walker/tools/actions.py)）：`f"Replaced {count} occurrence{'s' if count != 1 else ''} of {old!r} with {new!r} in {path} ({final_bytes} bytes)"`。`old`/`new` 用 `!r`（repr）防多行/特殊字符把消息搞乱，也防注入歧义。
8. **读写用 `newline=""`（Windows 必需，browser-use 在 Linux 无此问题）。** 同 write_file（[`actions.py:1263`](../../src/tree_walker/tools/actions.py)）：读时保留原始 `\r\n` 不翻译，写时 `\n` 不被译成 `\r\n`。原 LF 保持 LF、原 CRLF 保持 CRLF，替换前后行尾字节级一致。
9. **错误用 `ActionResult(error=...)` 而非字符串返回。** browser-use 把错误塞进返回字符串（`return "Error: ..."`）。TreeWalker 用 `ActionResult` 的 `error` 字段，符合本项目的 agent loop 语义（`error` 触发失败分支，字符串 `extracted_content` 不触发）。

---

## 阶段一：换行修复 + 分级错误 + 软提示 + 计数回显 + 测试（优先做，风险低）

> replace_file 是纯本地文件操作，**不涉及 CDP，无需 session 封装**——保留 action 内联，仅加固读写、错误处理与回显（与 write_file 阶段一同构，区别仅在 replace_file 多一个读取侧 `UnicodeDecodeError` 和"未命中"语义）。

### 1.1 `ReplaceFileParams` 扩展（`models.py:191-195`，4 空格）

保留 `path` / `old` / `new`，给 `old` 加 `min_length=1`（拦截空串），`extra="forbid"` 保留。`path`/`new` 描述精修，`old` 描述补"不能为空、全局替换所有匹配、大小写敏感、纯字面量非正则"。**不新增** `count`/`regex`/`case_sensitive`（留阶段二，阶段一保持与 browser-use 一致的"全局字面量替换"语义，风险最低）。

before：见 Context 节引用的 5 行定义。

after：

```python
class ReplaceFileParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(description="Path to an existing local file to edit in place.")
    old: str = Field(
        min_length=1,
        description="Exact text to find (literal substring, NOT a regex). All non-overlapping "
        "occurrences are replaced. Case-sensitive. Must be non-empty.",
    )
    new: str = Field(description="Replacement text (literal; may be empty to delete matches).")
```

> `new` 允许空串（删除匹配是合法用途，如 `old="DEBUG: "` → `new=""`）；只拦 `old`。`min_length=1` 是 Pydantic v2 `Field` 参数，无需额外 import。

### 1.2 `_action_replace_file` 重写（`actions.py:1286-1296`，4 空格）

before：见 Context 节引用的 11 行裸实现。

after：

```python
async def _action_replace_file(self, params: dict, browser: BrowserSession) -> ActionResult:
    path = params["path"]
    old = params["old"]
    new = params["new"]
    try:
        # newline="" 关闭 universal-newline 翻译（对齐 write_file:1263）：
        # 读时保留原始 \r\n，写时 \n 不被译成 \r\n。原 LF 保持 LF、原 CRLF 保持
        # CRLF，行尾字节级不变；含 \r\n 字面量的 old/new 也不会被压缩成 \n。
        with open(path, "r", encoding="utf-8", newline="") as f:
            content = f.read()
        count = content.count(old)
        if count == 0:
            # Soft miss（对齐 search_page:1332-1340）：old 不在文件里是可操作信息
            # （LLM 可调 old、或改用 write_file），不是工具失败。修正 browser-use
            # 的静默"Successfully replaced"缺陷——绝不假装改了。
            msg = f"No occurrences of {old!r} found in {path}; file unchanged"
            logger.info(msg)
            return ActionResult(extracted_content=msg, long_term_memory=msg)
        content = content.replace(old, new)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
    except FileNotFoundError:
        return ActionResult(error=f"File not found: {path}")
    except UnicodeDecodeError as e:
        # 读取侧特有（write_file 是写入不会有）：文件不是合法 UTF-8。
        logger.warning("replace_file(%r) decode failed: %s", path, e)
        return ActionResult(error=f"Failed to decode {path} as UTF-8: {e}")
    except OSError as e:
        # 分级错误：权限/目录(path 指向目录)/磁盘满/只读 → 明确 error + warning，
        # 不冒泡到 Tools.execute 通用 catch（actions.py:260-262）。
        logger.warning("replace_file(%r) failed: %s", path, e)
        return ActionResult(error=f"Failed to replace text in {path}: {e}")

    final_bytes = len(content.encode("utf-8"))
    memory = (
        f"Replaced {count} occurrence{'s' if count != 1 else ''} of {old!r} with {new!r} "
        f"in {path} ({final_bytes} bytes)"
    )
    logger.info(memory)
    return ActionResult(extracted_content=memory, long_term_memory=memory)
```

**阶段一关键决策（压测确认）**：

| 决策点 | 结论 | 理由 |
|---|---|---|
| `count` 用 `content.count(old)`（replace 前） | ✅ | `str.count` 与 `str.replace` 的非重叠匹配定义一致，次数准确 |
| `count == 0` 走 soft-miss（`extracted_content`，非 `error`） | ✅ | 对齐 search_page：未命中不是工具失败，是可操作信息；修正 browser-use 静默缺陷 |
| 读写都用 `newline=""` | ✅ | 修复现状 Windows 换行 bug；对齐 write_file:1263 |
| 不加 `os.makedirs` | ✅ | replace 改**已存在**文件，父目录应已在；缺失则 `FileNotFoundError`（合理，避免意外建目录）。与 read_file 一致，区别于 write_file（write 要能建新文件） |
| `except` 顺序：FileNotFoundError → UnicodeDecodeError → OSError | ✅ | FileNotFoundError 是 OSError 子类须在前；UnicodeDecodeError 非 OSError 须独立；OSError 兜底其余 IO 错 |
| 回显里 `old`/`new` 用 `!r` | ✅ | repr 防多行/特殊字符把消息搞乱，防注入歧义 |
| 不返回替换前的备份内容 | ✅ | 控制 context 体积；备份留阶段二 |

### 1.3 `ACTION_DEFINITIONS["replace_file"]` 描述更新（`models.py:314`，4 空格）

before：

```python
    "replace_file": (ReplaceFileParams, "Replace text within a local file", False),
```

after：

```python
    "replace_file": (
        ReplaceFileParams,
        "Replace every occurrence of an exact substring (old) with new text inside an "
        "existing local file, in place. Literal match, NOT a regex; case-sensitive; "
        "all non-overlapping occurrences are replaced. old must be non-empty and must "
        "already exist in the file (zero matches returns 'no occurrences' rather than "
        "silently succeeding). Prefer this over write_file for small edits to a large "
        "file you have already read.",
        False,
    ),
    # 注：原来与 write_file 描述里的 "Prefer replace_file for in-place edits ..." 形成
    # 双向呼应；此处补齐 replace_file 侧的承诺。
```

> `terminates_sequence=False` 不变（同 write_file / read_file）。描述告诉 LLM：字面量非正则、全局、大小写敏感、old 非空、零匹配会如实回报、与 write_file 的分工。

### 1.4 新增 `tests/test_replace_file.py`（TAB 缩进，对齐 `tests/test_write_file.py`）

入口走 `Tools().execute("replace_file", {...}, MagicMock())`（经 registry + `_normalize`，覆盖 Params 校验）；`browser` 用 `MagicMock()`；`tmp_path` 做文件系统隔离；每个异步测试标 `@pytest.mark.asyncio`。文件头与 helper 对齐 `tests/test_write_file.py`（`_read` / `_seed` 都用 `newline=""` 保证字节级断言）。

```python
"""Tests for replace_file: global literal replace, Windows newline preservation,
soft-miss on zero matches, empty-old rejection, tiered error mapping, count echo.

Mirrors tests/test_write_file.py: Tools().execute(...) entry point, tmp_path for
FS isolation, TAB indentation per CLAUDE.md.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from tree_walker.tools.actions import Tools
from tree_walker.tools.models import ReplaceFileParams


async def _run(params: dict):
	"""Drive replace_file through the public Tools().execute entry point."""
	tools = Tools()
	return await tools.execute("replace_file", params, MagicMock())


def _read(path) -> str:
	# newline="" reads exact disk bytes (no \r\n -> \n munging), matching the action.
	with open(path, "r", encoding="utf-8", newline="") as f:
		return f.read()


def _seed(path, text: str) -> None:
	"""Write a setup file with exact bytes (newline="")."""
	with open(path, "w", encoding="utf-8", newline="") as f:
		f.write(text)
```

测试类与方法（约 6 类 ~22 个用例，覆盖正常路径 + 关键边界 + 错误分级 + Params 校验）：

- **TestReplaceFileBasic**
	- `test_single_match_replaced`：`old="foo"`→`new="bar"`，文件 `"foo baz"` → `"bar baz"`（单匹配）。
	- `test_all_occurrences_replaced`：`"a a a"` + old=`"a"`→new=`"b"` → `"b b b"`，验证**全局**替换。
	- `test_new_can_delete_match`：old=`"X"`→new=`""`，`"aXb"`→`"ab"`（删除是合法用途）。
	- `test_case_sensitive`：old=`"Foo"`，文件 `"Foo foo"` → 替换后 `"X foo"`（小写 `foo` 不被动）。
	- `test_non_overlapping`：old=`"aa"`，文件 `"aaaa"` → `new`=`"b"` 得 `"bb"`（2 个非重叠匹配，验证 `count` 与 `str.replace` 语义一致）。
	- `test_multiline_old`：old 含 `\n`（跨行片段）能命中并替换。
- **TestReplaceFileNewline**（核心：修复现状 bug）
	- `test_lf_file_preserved`：种子 LF `"a\nb"`，替换后行尾仍 `\n`（不被改成 `\r\n`）。
	- `test_crlf_file_preserved`：种子 CRLF `"a\r\nb"`，替换后行尾仍 `\r\n`（读不翻译、写不翻译）。
	- `test_crlf_literal_in_old_preserved`：old=`"x\r\n"`，文件含 `"x\r\ny"`，替换后结构正确（`\r\n` 不被压成 `\n`）。
- **TestReplaceFileSoftMiss**（核心：修正 browser-use 静默缺陷）
	- `test_zero_matches_returns_soft_miss_not_error`：old 不在文件里 → `r.error is None`、`r.extracted_content` 含 `"No occurrences"`。
	- `test_soft_miss_file_unchanged`：零匹配时文件字节不变（读回 == 种子）。
	- `test_soft_miss_double_writes_memory`：`r.extracted_content == r.long_term_memory`。
	- `test_soft_miss_old_repr_in_message`：消息含 `{old!r}` 形式（带引号）。
- **TestReplaceFileErrorMapping**
	- `test_file_not_found`：path 不存在 → `r.error` 含 `"File not found"` + path。
	- `test_path_is_directory`：path 指向目录 → `r.error` 含 `"Failed to replace text"`（走 OSError 分支，`IsADirectoryError` 是 OSError 子类）。
	- `test_non_utf8_file_returns_decode_error`：用 GBK 写一个中文文件 → `r.error` 含 `"UTF-8"`（走 UnicodeDecodeError 分支）。
	- `test_error_logs_warning`：（可选）`caplog` 断言 decode/IO 失败有 WARNING。
- **TestReplaceFileEcho**
	- `test_hit_echo_includes_count`：3 处匹配 → `extracted_content` 含 `"3 occurrences"`。
	- `test_single_match_singular`：1 处匹配 → 含 `"1 occurrence"`（单数，无 `s`）。
	- `test_hit_echo_includes_path_and_bytes`：消息含 path 与 `"bytes"`。
	- `test_hit_long_term_memory_equals_extracted_content`：双写一致。
	- `test_hit_success_is_none`：`r.success is None`、`r.is_done is False`（非 done 动作）。
- **TestReplaceFileParamsValidation**
	- `test_old_empty_rejected`：`ReplaceFileParams(path="x", old="", new="y")` → `ValidationError`（min_length=1）。
	- `test_new_empty_allowed`：`ReplaceFileParams(path="x", old="a", new="")` → 合法（删除用途）。
	- `test_extra_field_forbidden`：未知字段 → `ValidationError`。
	- `test_all_three_required`：缺任一字段 → `ValidationError`。
	- `test_empty_old_rejected_at_execute`：`_run({"path":..., "old":"", "new":"x"})` 经 execute → `r.error` 含校验信息（验证 schema 层在 execute 路径生效）。

> UTF-8 round-trip（CJK/emoji）合入 TestReplaceFileBasic 或单列 `TestReplaceFileUtf8`：种子 `"你好 🎉"`，替换中文片段，读回字节级正确。

### 1.5 阶段一文件清单

| 文件 | 改动 | 锚点 |
|---|---|---|
| `src/tree_walker/tools/models.py` | `ReplaceFileParams.old` 加 `min_length=1` + 描述；3 字段描述精修 | `:191-195` |
| `src/tree_walker/tools/models.py` | `ACTION_DEFINITIONS["replace_file"]` 描述改多句 | `:314` |
| `src/tree_walker/tools/actions.py` | `_action_replace_file` 重写：`newline=""` 读写 + count + soft-miss + 三级错误 + 计数回显 | `:1286-1296` |
| `tests/test_replace_file.py` | 新增（TAB 缩进，~6 类 ~22 测试） | 新文件 |
| `docs/Tools技术细节/04_动作清单与CDP映射.md` | 同步 4.13 节：行号 `440-450`→实际、补错误分级/软提示/计数回显/`newline=""`/`min_length=1` | `:715-747` |

### 1.6 阶段一测试计划

```powershell
# 单文件
uv run python -m pytest tests/test_replace_file.py -x -v
# 覆盖率（actions 是改动主体）
uv run python -m pytest tests/test_replace_file.py --cov=tree_walker.tools.actions --cov-report=term-missing
# 全量回归（确保不破坏 write_file / read_file / evaluate 等兄弟工具）
uv run python -m pytest tests/ -x -v
```

---

## 阶段二（可选，独立，对齐 / 超越 browser-use 完整能力）

- **原子写**：`write-to-temp + os.replace`（写到一半异常不损坏文件）；与 write_file 阶段二一起做。
- **`count: int | None`**：限制替换次数（`None`=全部，`N`=前 N 个），覆盖"只改第一个匹配"的常见需求。
- **`regex: bool` / `case_sensitive: bool`**：正则模式、大小写不敏感（对齐 search_page 的 regex/case_sensitive 参数族）。
- **`expected_count: int | None`**：声明预期匹配数，不符则报错（防 `old` 写错导致的意外大面积替换）。
- **写路径白名单 / 沙箱**：与 read_file / write_file 三件套统一引入（移植 browser-use FileSystem 的子集）。
- **备份**：replace 前可选保留 `.bak`（`backup=True`）。

---

## 风险与回归点

| 风险 | 影响 | 缓解 |
|---|---|---|
| `newline=""` 改变既有调用方的文件行尾 | 原本依赖"读时翻译成 `\n`"的隐式行为会失效 | 这正是要修的 bug；无既有测试覆盖 replace_file，无回归面；新测试显式覆盖 LF/CRLF 保持 |
| `old` 加 `min_length=1` 拒绝历史合法的空 `old` 调用 | 空串 replace 本就是 bug 行为（每字符插入），无合理用途 | schema 层拒绝 + 测试 `test_empty_old_rejected_at_execute` 验证 execute 路径 |
| soft-miss 改变"零匹配"的返回语义（原本空 ActionResult，现 extracted_content） | 下游若按 `result.error is None and result.extracted_content is None` 判断"成功"会误判 | 零匹配本就不是成功；新语义更诚实；全量回归 + 检查 agent loop 对 `extracted_content` 的处理（search_page 已是同模式，无问题） |
| `UnicodeDecodeError` 新分支改变了非 UTF-8 文件的行为 | 原本冒泡通用 catch 成 `error=str(e)`，现成专属消息 | 行为更友好，方向正确；测试 `test_non_utf8_file_returns_decode_error` 锁定 |
| 回显含 `{old!r}`/`{new!r}` 可能很长 | 多行 old 把回显撑大 | repr 是项目既有安全惯例；超长时可截断（阶段二，对齐 `_truncation`） |

---

## 验证方法

1. `uv run python -m pytest tests/test_replace_file.py -x -v` 全绿，覆盖率 ≥85%。
2. `uv run python -m pytest tests/ -x -v` 全量回归无回归。
3. **动作层冒烟**（PowerShell，临时目录）：seed 一个 LF 文件 → replace → 读回确认行尾仍 LF、回显含次数；再 seed 一个不含 old 的文件 → 确认 soft-miss 文件未变。
4. **错误分级冒烟**：指向目录 / 不存在路径 / GBK 文件，分别确认三条 `error` 分支文案。
5. **回归对照**：错误分级（`FileNotFoundError`/`UnicodeDecodeError`/`OSError`）、`extracted_content` + `long_term_memory` 双写、soft-miss 模式，均与 `write_file` / `search_page` / `save_as_pdf` 同族规范一致。

---

## 验收 checklist（阶段一）

- [ ] `ReplaceFileParams.old` 加 `min_length=1`，三字段描述精修（models.py:191-195）
- [ ] `ACTION_DEFINITIONS["replace_file"]` 描述改多句，`terminates_sequence=False`（models.py:314）
- [ ] `_action_replace_file` 重写：`newline=""` 读写 + `count` + count==0 soft-miss + `FileNotFoundError`/`UnicodeDecodeError`/`OSError` 三级错误 + `logger.warning`/`logger.info` + 计数回显双写（actions.py:1286-1296）
- [ ] 新增 `tests/test_replace_file.py`（TAB 缩进，~6 类 ~22 测试，含 LF/CRLF 保持、soft-miss、空 old、GBK 解码失败）
- [ ] 单测全绿，覆盖率 ≥85%
- [ ] 全量回归无回归
- [ ] 同步现状文档 `docs/Tools技术细节/04_动作清单与CDP映射.md` 4.13 节（行号 `440-450`→实际、补错误分级/软提示/计数回显/`newline=""`/`min_length=1`）
- [ ] 缩进按文件：src = 4 空格、tests = TAB
