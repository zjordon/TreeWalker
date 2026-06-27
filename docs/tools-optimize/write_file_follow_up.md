# write_file 阶段二 follow-up 方案（原子写 / encoding / 写路径白名单 / newline）

> 承接 [`write_file.md`](./write_file.md) 末尾「阶段二」一节。阶段一（append/换行/分级错误/回显/测试）已全量落地于 `write_file` / `read_file` / `replace_file` 三件套，阶段二第 2 项「对齐 read_file/replace_file 错误处理与回显」已在阶段一顺带完成——三者的 try/except/echo 形状已一致，**本方案不再重复**。
> 本方案只覆盖阶段二**仍未实现**的 4 项：①原子写、②`encoding` 参数、③`allowed_write_paths` 白名单、④`newline` 翻译控制参数。`FileSystem` 沙箱（阶段二第 6 项）属重大架构变更，明确**不在本方案范围**。
> 范式镜像：原子写——`src/` 现无先例，采用 Python 惯用 `tmp + os.replace`；白名单——端到端镜像 `allowed_upload_paths`（`actions.py:1410-1416` + `config.py:90/196-202/268` + `agent.py:57`）。

---

## Context（为什么做这个）

阶段一让三个本地文件工具具备了基本健壮性，但仍有四个可用性/可靠性缺口：

1. **非原子写**：`write_file`（overwrite）与 `replace_file` 直接 `open(path,"w")` 写盘，进程在写盘中途崩溃（OOM / 超时 kill / 断电）会留下**半个文件**，且 replace_file 的现状文档 4.13 已注明「非原子操作」。`src/` 全树无任何 `os.replace`/`tmp` 先例。
2. **写死 UTF-8**：三者均硬编码 `encoding="utf-8"`，遇到 latin-1 / cp936（GBK）等遗留文件直接 `UnicodeDecodeError`（读）或写出乱码（写）。LLM 无法在不改代码的前提下处理遗留编码。
3. **写路径无 jail**：`upload_file` 有 `allowed_upload_paths` 前缀白名单（宿主可约束上传目录），但写侧（`write_file` / `replace_file`）可写任意路径——宿主无法把 agent 的写操作关进笼子。
4. **行尾翻译不可控**：三者硬编码 `newline=""`（无翻译、LF/CRLF 原样）。这对跨平台一致性是最优默认，但 LLM 无法在需要时显式产出 CRLF（如某些 Windows 严格要求 `\r\n` 的工具链）。

**预期结果**：四项均为**纯加法、默认零行为变更**——原子写只在 overwrite/replace 生效（append 保持 O(1) 直接写）、`encoding`/`newline` 默认值复现阶段一行为、白名单默认 `None`（= 不启用、全放行）。新增/扩展测试覆盖四项的正路径与边界，覆盖率维持 >85%。

---

## 工程约束（实施时遵守）

- Windows + PowerShell；包用 uv，跑测试 `uv run python -m pytest tests/ -x -v`。
- **缩进按文件（已实测）**：`src/tree_walker/tools/models.py`、`actions.py`、`config.py` = **4 空格**；`tests/test_*.py` = **TAB**。编辑前实测行首缩进。
- 改完跑相关单测 + 全量回归；覆盖率目标 >85%。
- 不主动 `git commit` / `git push`。
- `os` / `logger` 在 `actions.py` 已是模块级 import；`Field` / `ConfigDict` 在 `models.py` 已 import。新增代码无需新 import。

---

## 与 browser-use 的关键差异（沿用阶段一立场，不照搬）

1. **不引入 `FileSystem` 沙箱。** browser-use 的原子性来自其 `FileSystem` 内存层（`sync_to_disk` 整体落盘）。TreeWalker 用裸 `path` 直写磁盘（read/replace/write 全家一致），原子性改用 Python 惯用 `tmp + os.replace`，**不引入内存层、不改 `path` 参数语义**。
2. **append 刻意不原子。** browser-use 无独立 append。本方案 append 保持 `open(path,"a")` 直接追加（O(1)、不读全文、不需读权限）；文档明确告知 append 非崩溃安全。若需崩溃安全，用 overwrite（先读后整体原子写）。
3. **白名单沿用前缀匹配、不做 realpath 规范化。** 与 `allowed_upload_paths`（`actions.py:1413` `any(path.startswith(p) ...)`）**逐字一致**——一致性优先于完美；`..` traversal 硬化留作后续（与 upload 同步硬化，避免二者行为分叉）。
4. **`newline` 暴露为 passthrough。** 默认 `""`（阶段一行为）；非 `""` 时直传 Python `open(newline=...)`。不引入自定义枚举，`trailing_newline`/`leading_newline`（内容层补 `\n`）与 `newline`（I/O 层翻译）职责分明，由 description 区分。

---

## 关键设计（四项如何叠加，避免相互打架）

四项都改同一批 `open()` 调用点，但**正交、可一次性合并**：合并后 `open(..., encoding=enc, newline=newline)` 同时承载 2.B（encoding）与 2.D（newline）；2.A（原子写）把 overwrite/replace 的「直接写 target」改成「写 `path+".tmp"` 再 `os.replace(tmp, path)`」；2.C（白名单）在方法最前面加一道前缀匹配守卫。三者互不干扰：

- 白名单校验 `path` 即足够——`tmp = path + ".tmp"` 与 target 同目录，`path` 放行则 tmp 隐含放行。
- 原子写要求 **tmp 与 target 同卷**（同目录即满足），`os.replace` 在 Windows 同卷下原子（`MoveFileEx` + `MOVEFILE_REPLACE_EXISTING`）。
- `newline` 默认 `""`、`encoding` 默认 `"utf-8"` → 合并后默认行为与阶段一**逐字节一致**（现有测试零回归）。

---

## 2.A 原子写（overwrite + replace_file；append 除外）

### 决策表

| 决策点 | 结论 | 理由 |
|---|---|---|
| 适用范围 | overwrite + replace_file 走原子；append 保持直接写 | 用户确认：append 保 O(1) 直接追加、非崩溃安全（文档注明） |
| tmp 位置 | `path + ".tmp"`（与 target **同目录**） | `os.replace` 仅同卷原子；同目录即同卷 |
| 替换原语 | `os.replace(tmp, path)` | 同卷原子、覆盖现有 target、跨平台（Win 用 `MoveFileEx`） |
| 失败清理 | `except` 内 `if os.path.exists(tmp): os.remove(tmp)`（吞二次 OSError） | 写盘/replace 失败时清残留 tmp，避免脏文件 |
| tmp 命名 | `path + ".tmp"`（非随机后缀） | 单 agent 串行写；concurrent 同 path 写本就 race；前次崩溃残留 tmp 会被下次 `open(tmp,"w")` 覆盖，无 stale 问题 |
| crash 语义 | 写 tmp 中途崩溃 → target 完好（仅留 tmp 残骸，下次覆盖） | 这是原子写的核心收益；replace 失败→target 不变 |

### 2.A.1 `_action_write_file` overwrite 分支改写（`actions.py:1561-1568`，4 空格）

before（阶段一，直接写 target）：

```python
mode = "a" if append else "w"
try:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, mode, encoding="utf-8", newline="") as f:
        f.write(content)
except OSError as e:
    logger.warning("write_file(%r) failed: %s", path, e)
    return ActionResult(error=f"Failed to write file {path}: {e}")
```

after（append 直接写；overwrite 走 tmp + os.replace；失败清残留 tmp）：

```python
mode = "a" if append else "w"
tmp = path + ".tmp"  # 原子写临时文件（仅 overwrite 用；同目录 → os.replace 同卷原子）
try:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if append:
        # append 不走原子写：保 O(1) 增量追加（见 2.A 决策表，非崩溃安全）。
        with open(path, mode, encoding=enc, newline=newline) as f:
            f.write(content)
    else:
        # overwrite 原子写：tmp + os.replace，崩溃不留半个文件。
        with open(tmp, "w", encoding=enc, newline=newline) as f:
            f.write(content)
        os.replace(tmp, path)
except OSError as e:
    # 仅 overwrite 路径会留下 tmp 残骸；清理之（吞二次 OSError）。
    if not append and os.path.exists(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass
    logger.warning("write_file(%r) failed: %s", path, e)
    return ActionResult(error=f"Failed to write file {path}: {e}")
```

> `enc` / `newline` 来自 2.B / 2.D（见下），默认 `"utf-8"` / `""` → 与阶段一等价。

### 2.A.2 `_action_replace_file` 写段改写（`actions.py:1651-1653`，4 空格）

before（直接写 target）：

```python
content = content.replace(old, new)
with open(path, "w", encoding="utf-8", newline="") as f:
    f.write(content)
```

after（tmp + os.replace；`except OSError` 内补 tmp 清理——见「组合后完整方法」）：

```python
content = content.replace(old, new)
# 原子写：tmp + os.replace，崩溃不留半个文件。
with open(tmp, "w", encoding=enc, newline=newline) as f:
    f.write(content)
os.replace(tmp, path)
```

> `tmp = path + ".tmp"` 在方法头声明；`except OSError` 分支补 `if os.path.exists(tmp): os.remove(tmp)`（读阶段失败时 tmp 不存在，`exists` 守卫保证安全）。

---

## 2.B `encoding` 参数（三件套）

### 决策表

| 决策点 | 结论 | 理由 |
|---|---|---|
| 字段 | `encoding: str | None = None`（三者均加） | 默认 None → action 内 `or "utf-8"` → 复现阶段一 |
| 字节数 | `len(content.encode(enc))`（write/replace） | 与 `os.path.getsize` 一致；CJK/latin-1 各自准确 |
| 回显 | 仅 `enc != "utf-8"` 时追加 `(encoding: {enc})` | 默认回显零变更，现有 `test_*_echo` 不回归 |
| decode 错误文案 | `Failed to decode {path} as {enc}: {e}`（read/replace） | 不再写死 "UTF-8"，反映真实编码 |
| description | 点明默认 UTF-8 + 遗留编码示例 | 引导 LLM 仅在确实需要时传 |

### 2.B.1 三个 Params 各加一字段（`models.py`，4 空格）

`WriteFileParams`（`models.py:258-276`）追加：

```python
encoding: str | None = Field(
    default=None,
    description="Text encoding to write with (default UTF-8). Set e.g. 'latin-1' or "
    "'cp936' for legacy files; the byte-count echo reflects this encoding.",
)
```

`ReadFileParams`（`models.py:279-281`）/ `ReplaceFileParams`（`models.py:284-292`）追加同名字段（description 微调为「read/decode with」）。

### 2.B.2 action 内取值（三者统一）

```python
enc = params.get("encoding") or "utf-8"
```

替换所有 `encoding="utf-8"` 为 `encoding=enc`；字节数 `len(content.encode(enc))`；read/replace 的 `UnicodeDecodeError` 文案与 write 的回显按上表调整。

> 运行时守卫说明（见 memory `action-params-no-runtime-validation`）：registry 不校验 execute 路径 params，`encoding` 的非法值（如 `"no-such"`）会在 `open()` 抛 `LookupError`。`LookupError` **不是** `OSError` 子类，会冒泡到 `Tools.execute` 通用 catch。**建议**把 `except OSError` 前补一个 `except LookupError as e:` → `ActionResult(error=f"Unknown encoding {enc!r}: {e}")`，与分级错误风格一致（三者均加）。

---

## 2.C `allowed_write_paths` 写路径白名单（write_file + replace_file）

端到端镜像 `allowed_upload_paths`，**逐层对齐**：

| 层 | upload 现状（镜像源） | write 新增 |
|---|---|---|
| `Tools.__init__` 形参 | `allowed_upload_paths: list[str]\|None=None`（`actions.py:321`） | 加 `allowed_write_paths: list[str]\|None=None` |
| 存储 | `self._allowed_upload_paths=...`（`:326`） | 加 `self._allowed_write_paths=allowed_write_paths` |
| `AgentSettings` 字段 | `allowed_upload_paths`（`config.py:90`） | 加 `allowed_write_paths: list[str]\|None=None`（紧随其后） |
| env 加载 | `_load_allowed_upload_paths()` 读 `AGENT_ALLOWED_UPLOAD_PATHS`（`config.py:196-202`） | 加 `_load_allowed_write_paths()` 读 `AGENT_ALLOWED_WRITE_PATHS`（逐字镜像） |
| `load_settings()` 注入 | `allowed_upload_paths=_load_allowed_upload_paths()`（`config.py:268`） | 加 `allowed_write_paths=_load_allowed_write_paths()` |
| `agent.py` 透传 | `Tools(..., allowed_upload_paths=_settings.allowed_upload_paths)`（`agent.py:57`） | 加 `allowed_write_paths=_settings.allowed_write_paths` |
| action 内校验 | `if allowed and not any(file_path.startswith(p) for p in allowed): return ActionResult(error=...)`（`actions.py:1411-1416`） | write_file + replace_file **方法头**同形校验 |

### 2.C.1 校验代码（`_action_write_file` / `_action_replace_file` 方法头，4 空格）

放在取参之后、任何 `makedirs`/`open` 之前（**fail fast**，避免在 jail 外创建目录/临时文件）：

```python
# 写路径白名单（镜像 allowed_upload_paths 前缀匹配）；None = 不启用、全放行。
allowed = self._allowed_write_paths
if allowed and not any(path.startswith(p) for p in allowed):
    return ActionResult(error=f"File path not in allowed write paths: {path}")
```

> read_file **不加**（用户确认：白名单只 gate 两个写工具）。`tmp = path + ".tmp"` 与 target 同目录，`path` 放行即隐含 tmp 放行，无需额外校验 tmp。

### 2.C.2 `config.py` 新增（4 空格，逐字镜像 upload）

```python
def _load_allowed_write_paths() -> list[str] | None:
    """Load allowed write paths from AGENT_ALLOWED_WRITE_PATHS env var (comma-separated)."""
    raw = os.environ.get("AGENT_ALLOWED_WRITE_PATHS")
    if not raw:
        return None
    paths = [p.strip() for p in raw.split(",") if p.strip()]
    return paths or None
```

`AgentSettings`（`config.py:90` 后）加 `allowed_write_paths: list[str] | None = None`；`load_settings()`（`config.py:268` 后）加 `allowed_write_paths=_load_allowed_write_paths(),`。

---

## 2.D `newline` 翻译控制参数（三件套）

### 决策表

| 决策点 | 结论 | 理由 |
|---|---|---|
| 字段 | `newline: str = ""`（三者均加） | 默认 `""` = 无翻译 = 阶段一行为；passthrough 给 `open(newline=...)` |
| 取值 | 直传 Python `open` 语义（`""`/`"\n"`/`"\r"`/`"\r\n"`） | 不自造枚举；`"\r\n"` 强制 CRLF 输出 |
| 与 trailing/leading_newline 关系 | **正交**：后者在内容层补 `\n`；`newline` 在 I/O 层翻译 | description 显式区分，避免 LLM 混淆 |
| 回显 | 不改（行尾模式不影响字节数语义的默认回显） | 默认 `""` 零变更 |

### 2.D.1 三个 Params 各加一字段（`models.py`，4 空格）

`WriteFileParams` 追加：

```python
newline: str = Field(
    default="",
    description="Python open() newline translation mode (default '' = no translation; "
    "\\n/\\r\\n stay as-is). Set '\\r\\n' to force CRLF output, '\\n' for LF. This is the "
    "I/O-layer translation — distinct from trailing_newline/leading_newline, which only "
    "add/remove a \\n in the content string.",
)
```

`ReadFileParams` / `ReplaceFileParams` 追加同名字段（read 侧 description 注明 `""` 保留 CRLF、`None`/`"\n"` 为 universal-newline 压成 `\n`）。

### 2.D.2 action 内取值（三者统一）

```python
newline = params.get("newline", "")
```

替换所有 `newline=""` 为 `newline=newline`。默认 `""` → 与阶段一逐字节等价。

---

## 组合后完整方法（四项叠加的终态，供实现参照）

### `_action_write_file`（`actions.py:1547`，4 空格）

```python
async def _action_write_file(self, params: dict, browser: BrowserSession) -> ActionResult:
    path = params["path"]
    content = params["content"]
    append = params.get("append", False)
    trailing_newline = params.get("trailing_newline", True)
    leading_newline = params.get("leading_newline", False)
    enc = params.get("encoding") or "utf-8"      # 2.B
    newline = params.get("newline", "")          # 2.D

    # 2.C 写路径白名单（镜像 allowed_upload_paths 前缀匹配）。
    allowed = self._allowed_write_paths
    if allowed and not any(path.startswith(p) for p in allowed):
        return ActionResult(error=f"File path not in allowed write paths: {path}")

    # 换行簿记（内容层）；trailing 守卫式（幂等、不双换行、不破坏 CRLF）。
    if leading_newline:
        content = "\n" + content
    if trailing_newline and not content.endswith("\n"):
        content = content + "\n"

    tmp = path + ".tmp"  # 2.A 原子写临时文件（仅 overwrite 用；同目录 → os.replace 同卷原子）
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        if append:
            with open(path, "a", encoding=enc, newline=newline) as f:   # append 非原子（保 O(1)）
                f.write(content)
        else:
            with open(tmp, "w", encoding=enc, newline=newline) as f:    # 2.A overwrite 原子写
                f.write(content)
            os.replace(tmp, path)
    except LookupError as e:   # 2.B 非法 encoding 名（非 OSError 子类，单独兜底）
        logger.warning("write_file(%r) unknown encoding %r: %s", path, enc, e)
        return ActionResult(error=f"Unknown encoding {enc!r}: {e}")
    except OSError as e:
        if not append and os.path.exists(tmp):   # 清残留 tmp（仅 overwrite 路径）
            try:
                os.remove(tmp)
            except OSError:
                pass
        logger.warning("write_file(%r) failed: %s", path, e)
        return ActionResult(error=f"Failed to write file {path}: {e}")

    written = len(content.encode(enc))
    action_word = "Appended" if append else "Wrote"
    memory = f"{action_word} {written} bytes to {path}"
    if enc != "utf-8":
        memory += f" (encoding: {enc})"
    logger.info(memory)
    return ActionResult(extracted_content=memory, long_term_memory=memory)
```

### `_action_replace_file`（`actions.py:1629`，4 空格）

```python
async def _action_replace_file(self, params: dict, browser: BrowserSession) -> ActionResult:
    path = params["path"]
    old = params["old"]
    new = params["new"]
    if not old:
        return ActionResult(error="replace_file 'old' must be a non-empty string")
    enc = params.get("encoding") or "utf-8"      # 2.B
    newline = params.get("newline", "")          # 2.D

    # 2.C 写路径白名单（replace_file 也是写）。
    allowed = self._allowed_write_paths
    if allowed and not any(path.startswith(p) for p in allowed):
        return ActionResult(error=f"File path not in allowed write paths: {path}")

    tmp = path + ".tmp"  # 2.A 原子写临时文件
    try:
        with open(path, "r", encoding=enc, newline=newline) as f:
            content = f.read()
        count = content.count(old)
        if count == 0:
            msg = f"No occurrences of {old!r} found in {path}; file unchanged"
            logger.info(msg)
            return ActionResult(extracted_content=msg, long_term_memory=msg)
        content = content.replace(old, new)
        with open(tmp, "w", encoding=enc, newline=newline) as f:    # 2.A 原子写
            f.write(content)
        os.replace(tmp, path)
    except FileNotFoundError:
        return ActionResult(error=f"File not found: {path}")
    except UnicodeDecodeError as e:
        logger.warning("replace_file(%r) decode failed: %s", path, e)
        return ActionResult(error=f"Failed to decode {path} as {enc}: {e}")
    except LookupError as e:   # 2.B 非法 encoding 名
        logger.warning("replace_file(%r) unknown encoding %r: %s", path, enc, e)
        return ActionResult(error=f"Unknown encoding {enc!r}: {e}")
    except OSError as e:
        if os.path.exists(tmp):   # 读阶段失败时 tmp 不存在，exists 守卫保证安全
            try:
                os.remove(tmp)
            except OSError:
                pass
        logger.warning("replace_file(%r) failed: %s", path, e)
        return ActionResult(error=f"Failed to replace text in {path}: {e}")

    final_bytes = len(content.encode(enc))
    memory = (
        f"Replaced {count} occurrence{'s' if count != 1 else ''} of {old!r} with {new!r} "
        f"in {path} ({final_bytes} bytes)"
    )
    logger.info(memory)
    return ActionResult(extracted_content=memory, long_term_memory=memory)
```

### `_action_read_file`（`actions.py:1581`，4 空格，仅 2.B + 2.D，无原子/无白名单）

仅改取参与 `open()` 实参 + decode 文案：

```python
    enc = params.get("encoding") or "utf-8"      # 2.B
    newline = params.get("newline", "")          # 2.D
    try:
        with open(path, "r", encoding=enc, newline=newline) as f:
            content = f.read()
    except FileNotFoundError:
        return ActionResult(error=f"File not found: {path}")
    except UnicodeDecodeError as e:
        logger.warning("read_file(%r) decode failed: %s", path, e)
        return ActionResult(error=f"Failed to decode {path} as {enc}: {e}")
    except LookupError as e:   # 2.B 非法 encoding 名
        logger.warning("read_file(%r) unknown encoding %r: %s", path, enc, e)
        return ActionResult(error=f"Unknown encoding {enc!r}: {e}")
    except OSError as e:
        logger.warning("read_file(%r) failed: %s", path, e)
        return ActionResult(error=f"Failed to read file {path}: {e}")
    # ……（截断 + 回显逻辑不变）
```

---

## 文件清单

| 文件 | 改动 | 锚点 |
|---|---|---|
| `src/tree_walker/tools/models.py` | `WriteFileParams`/`ReadFileParams`/`ReplaceFileParams` 各加 `encoding`+`newline` 两字段；description 同步（read_file/write_file 多句描述补 encoding/newline 说明） | 258-276 / 279-281 / 284-292 |
| `src/tree_walker/tools/actions.py` | `Tools.__init__` 加 `allowed_write_paths` 形参+存储；`_action_write_file`/`_action_replace_file` 加白名单守卫 + 原子写 + enc/newline + LookupError 兜底；`_action_read_file` 加 enc/newline + LookupError 兜底 | 321/326 / 1547 / 1581 / 1629 |
| `src/tree_walker/config.py` | `AgentSettings` 加 `allowed_write_paths`；新增 `_load_allowed_write_paths()`；`load_settings()` 注入 | 90 / 196-202 / 268 |
| `src/tree_walker/agent/agent.py` | `Tools(...)` 透传 `allowed_write_paths=_settings.allowed_write_paths` | 57 |
| `tests/test_write_file.py` | 扩展：原子写（overwrite tmp+replace、append 不走 tmp、replace 失败 target 不变且清 tmp）、encoding（latin-1/cp936/回显/非法名 LookupError）、newline（CRLF 输出/默认无翻译）、白名单（越界拒、界内放行、None 全放行） | TAB 缩进，新增约 15-20 测试 |
| `tests/test_read_file.py` | 扩展：encoding（latin-1/cp936 读取、decode 错误文案含编码、非法名）、newline（universal 模式 CRLF→\n、默认保留 CRLF 回归） | TAB 缩进，新增约 6-8 测试 |
| `tests/test_replace_file.py` | 扩展：原子写（replace 失败 target 不变且清 tmp）、encoding（latin-1 文件替换）、白名单（越界拒） | TAB 缩进，新增约 5-7 测试 |
| `docs/Tools技术细节/04_动作清单与CDP映射.md` | 同步 4.13（replace_file 改注「原子写」）、4.24（write_file 补原子写/encoding/newline/白名单）；read_file 节补 encoding/newline | 4.13 / 4.24 |
| （可选）`docs/tools-optimize/write_file.md` | 阶段二第 2 项标注「已在阶段一完成」、其余四项标注「见 write_file_follow_up.md」 | 末尾阶段二节 |

---

## 测试计划

```powershell
# 三个相关单测
uv run python -m pytest tests/test_write_file.py tests/test_read_file.py tests/test_replace_file.py -x -v
# 覆盖率
uv run python -m pytest tests/test_write_file.py tests/test_read_file.py tests/test_replace_file.py --cov=tree_walker.tools.actions --cov-report=term-missing
# 全量回归（确保不碰 evaluate/find_elements/search_page/upload_file/save_as_pdf）
uv run python -m pytest tests/ -x -v
```

新增测试要点（均 `Tools().execute(...)` 入口 + `tmp_path` + `MagicMock()` browser，TAB 缩进，逐个 `@pytest.mark.asyncio`）：

- **原子写**：`monkeypatch` `tree_walker.tools.actions.os.replace` 抛 `OSError` → 断言 target 内容**不变**、无残留 `*.tmp`（覆盖 write_file overwrite 与 replace_file 两条路径）；append 路径断言**不**产生 `*.tmp`。
- **encoding**：写 latin-1/cp936 → 以同编码读回一致；回显含 `(encoding: latin-1)`；字节数与 `os.path.getsize` 一致；非法编码名 → `ActionResult(error="Unknown encoding ...")`。
- **newline**：`newline="\r\n"` 写 → 字节含 `\r\n`；默认 `""` → `\n` 不被译成 `\r\n`（回归）；read 侧 `newline` universal 模式把 CRLF 压成 `\n`。
- **白名单**：`Tools(allowed_write_paths=[str(tmp_path)])` → 界内 path 放行、界外 path 返回 `ActionResult(error="... not in allowed write paths ...")`；`None` 全放行；write_file 与 replace_file 各覆盖；env 加载（若存在 `tests/test_config.py` 则镜像 upload 的用例补一条）。

---

## 风险与回归点

| 风险 | 影响 | 缓解 |
|---|---|---|
| 原子写在网络挂载/特殊 FS 上 `os.replace` 非原子 | 极少数环境下崩溃仍可能留半个文件 | tmp 与 target 同目录（同卷）已是 `os.replace` 原子的前提；本工具面向本地工作目录，文档注明 |
| `tmp = path + ".tmp"` 与外部工具同名冲突 | 极小概率覆盖他人 `.tmp` | 单 agent 串行；可后续改 `mkstemp(dir=...)`，但偏离 doc 指定 `path+".tmp"`，暂不做 |
| append 非崩溃安全 | append 中途崩溃可能丢尾部/重复 | **有意**（用户确认）；description 显式告知，需崩溃安全用 overwrite |
| 白名单前缀匹配可被 `..` 绕过 | 安全性弱于 realpath 规范化 | **与 upload 一致**（避免二者分叉）；硬化留作 upload+write 同步后续 |
| `newline` 与 `trailing_newline`/`leading_newline` 名字相近致 LLM 混淆 | LLM 误用 | description 显式区分「I/O 翻译 vs 内容补 `\n`」；如混淆严重可改名 `line_ending`（备选，暂不动） |
| `encoding` 非法值抛 `LookupError` 冒泡 | 未兜底则到通用 catch 变无上下文 error | 三者均加 `except LookupError` → 明确 `ActionResult(error="Unknown encoding ...")` |
| 默认值变更 | 无 | `encoding=None→utf-8`、`newline=""`、`allowed_write_paths=None` 全部复现阶段一行为，现有测试零回归 |

---

## 验证方法

1. `uv run python -m pytest tests/test_write_file.py tests/test_read_file.py tests/test_replace_file.py -x -v` 全绿，覆盖率 ≥85%。
2. `uv run python -m pytest tests/ -x -v` 全量回归无回归（尤其不碰 evaluate/find_elements/search_page/upload_file/save_as_pdf）。
3. **原子写冒烟**（PowerShell）：写一文件 → 再次 overwrite 中途模拟 replace 失败（脚本内 monkeypatch）→ 原文件完好、无 `.tmp` 残留。
4. **encoding 冒烟**：`write_file({"path":..., "content":"你好", "encoding":"latin-1"})` → 回显含 `(encoding: latin-1)`、字节数与 `os.path.getsize` 一致。
5. **白名单冒烟**：`Tools(allowed_write_paths=[r"D:\safe"])` → 写 `D:\safe\x.txt` 放行、写 `D:\other\x.txt` 返回 `error`。
6. **newline 冒烟**：`write_file({...,"newline":"\r\n"})` → 文件字节含 `\r\n`；默认 → `\n`。
7. **对照**：分级错误（`ActionResult(error=…)` + `logger.warning`）、`extracted_content`+`long_term_memory` 双写，均与 `save_as_pdf`/`find_elements`/`evaluate`/upload 白名单同族规范一致。

---

## 验收 checklist

- [ ] `WriteFileParams`/`ReadFileParams`/`ReplaceFileParams` 各加 `encoding: str | None = None` + `newline: str = ""`（`models.py`）
- [ ] `_action_write_file`：白名单守卫 + overwrite 原子写（tmp+os.replace+失败清残）+ append 直接写 + enc/newline + LookupError 兜底（`actions.py:1547`）
- [ ] `_action_replace_file`：白名单守卫 + 原子写（tmp+os.replace+失败清残）+ enc/newline + LookupError 兜底（`actions.py:1629`）
- [ ] `_action_read_file`：enc/newline + LookupError 兜底（`actions.py:1581`）
- [ ] `Tools.__init__` 加 `allowed_write_paths` 形参+存储；`AgentSettings` 加字段；`_load_allowed_write_paths()` + `load_settings()` 注入；`agent.py:57` 透传
- [ ] 三件套测试扩展（原子写/encoding/newline/白名单），TAB 缩进，全绿、覆盖率 ≥85%
- [ ] `uv run python -m pytest tests/ -x -v` 全量回归无回归
- [ ] 同步现状文档 4.13（replace_file 改注「原子写」）、4.24（write_file 四项）；read_file 节补 encoding/newline
- [ ] 缩进按文件：`src/` = 4 空格、`tests/` = TAB
