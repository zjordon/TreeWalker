# replace_file 阶段二 follow-up 方案（count / regex / case_sensitive / expected_count / backup）

> 承接 [`replace_file.md`](./replace_file.md) 末尾「阶段二」一节。经核对代码，阶段二的「原子写」「写路径白名单」以及附带的 `encoding`/`newline` 参数**已随 `write_file` 阶段二 PR（#75）落地于 `replace_file`**（`actions.py:1668-1738` / `models.py:307-325`）——**本方案不再重复**。
> 本方案只覆盖阶段二**仍未实现**的 4 项：①`count`、②`regex`/`case_sensitive`、③`expected_count`、④`backup`。`FileSystem` 沙箱（阶段二的「沙箱」表述）属重大架构变更，明确**不在本方案范围**（与 [`write_file_follow_up.md`](./write_file_follow_up.md) 一致）。
> 范式镜像：`regex`/`case_sensitive` 参数族对齐 `search_page`（`models.py:420-421`）；`case_sensitive` 默认取 `True`（保留阶段一大小写敏感，**有意分叉** search_page 的 `False` 默认）。

---

## Context（为什么做这个）

阶段一 + write_file 阶段二已让 `replace_file` 具备原子写、编码/行尾控制、写路径白名单与零匹配软提示。但仍有 4 个可用性 / 安全性缺口：

1. **只能全量替换**：`str.replace` 一刀切替换所有匹配，无法「只改前 N 个 / 只改第一个」（最常见的小编辑诉求）。
2. **只能字面 + 大小写敏感**：无法处理正则（如 `\d+` → `N`、命名捕获重排），也无法做大小写不敏感替换（对齐 `search_page` 的 Ctrl+F 式检索能力）。
3. **`old` 写错无防护**：`old` 拼错 → 0 匹配（有软提示）或意外匹配一大片 → 大面积误改，缺一个「声明预期匹配数」的写前守卫。
4. **无备份**：replace 前不保留原始副本，误改后无 `.bak` 可回滚。

**预期结果**：四项均为**纯加法、默认零行为变更**——`count=None`=全部（阶段一）、`regex=False`+`case_sensitive=True`=字面大小写敏感（阶段一）、`expected_count=None`=不校验、`backup=False`=不备份。新增/扩展测试覆盖四项的正路径与边界，覆盖率维持 >85%。

---

## 工程约束（实施时遵守）

- Windows + PowerShell；包用 uv，跑测试 `uv run python -m pytest tests/ -x -v`。
- **缩进按文件（已实测）**：`src/tree_walker/tools/models.py`、`actions.py` = **4 空格**；`tests/test_replace_file.py` = **TAB**。编辑前实测行首缩进。
- 改完跑相关单测 + 全量回归；覆盖率目标 >85%。
- 不主动 `git commit` / `git push`。
- `re` 在 `actions.py:11` 已 import；**`shutil` 未 import，需在顶部加 `import shutil`**（按字母序，位于 `import re` 与 `import time` 之间）。`Field`/`ConfigDict` 在 `models.py` 已 import。

---

## 与 browser-use 的关键差异（沿用阶段一立场，不照搬）

1. **不引入 `FileSystem` 沙箱。** `backup` 用裸 `shutil.copy2(path, path+".bak")`，与 read/write/replace 全家裸 `path` 直写磁盘一致；不引入内存层。
2. **`case_sensitive` 默认 `True`（分叉 search_page）。** search_page 默认不敏感（Ctrl+F 检索语义）；replace_file 阶段一已是大小写敏感（有 `test_case_sensitive` 守护），改默认会破坏已发布语义。仅参数族（名字/语义）对齐，默认值在 description 注明分叉。
3. **`expected_count` 比对原始总数、写前守卫。** 不是比对 `count` 限制后的值——否则 `count=1`+`expected_count=1` 会掩盖「old 意外匹配 47 处」的 runaway。
4. **`backup` 失败致命。** 用户显式 `backup=True` 即依赖它，静默继续会违背预期；失败返回 error、不写。
5. **大小写不敏感但非 regex 时 `new` 保持字面。** 用 `pattern.sub(lambda m: new, content)` 把 `new` 当不透明字面量，不展开 `\1`/`\g<name>`；仅 `regex=True` 时 `new` 才作为 re.sub 模板支持反向引用。

---

## 关键设计（四项如何叠加，避免相互打架）

四项正交、可一次性合并。核心是引入一个**分支选择器** `use_re = bool(regex) or not case_sensitive`：

- 默认（`regex=False`、`case_sensitive=True`）→ `use_re=False` → 走阶段一 `str.count`/`str.replace` 路径，**字节级零变更**。
- 任一开关打开 → `use_re=True` → 走 `re.compile` + `subn` 路径（`regex` 决定 `old` 是否转义、`new` 是否支持反向引用；`case_sensitive=False` 叠加 `re.IGNORECASE`）。
- `count`：字面路径用 `str.replace(old,new[,count])`；re 路径用 `pattern.subn(repl, content, count=count)`。**注意 `count=0` 语义分叉**——`str.replace(...,0)` 替换 0 个、`re.subn(...,count=0)` 替换全部，故一律按 `count is None` 分支、永不传 `0`，并由运行时守卫拒绝 `count<1`。
- `expected_count`：在 `raw_total`（原始匹配总数）算出后、软失败与 backup 之前比对；不匹配则文件不动、返回 error。
- `backup`：在 `expected_count` 校验通过、软失败排除后，写 tmp 之前 `shutil.copy2`；`.bak` 与 `path` 同目录，白名单前缀校验对 `path` 放行即隐含对 `.bak`/`.tmp` 放行（`(path+".bak").startswith(allowed)` 成立）。

---

## ① `count: int | None` —— 限制替换次数

### 决策表

| 决策点 | 结论 | 理由 |
|---|---|---|
| 字段 | `count: int \| None = Field(default=None, ge=1)` | None=全部（阶段一）；正整数=前 N 个；`ge=1` 在 schema 层拒 0/负 |
| 运行时守卫 | `count is None or (int 且非 bool 且 ≥1)`，否则 `error="replace_file 'count' must be a positive integer (got X)"` | registry 不校验 execute 路径；`bool` 是 `int` 子类，须显式拒（`True` 否则被当 1） |
| 字面路径取值 | `count is None` → `content.replace(old,new)`；否则 `content.replace(old,new,count)` | `str.replace` 的 count 是「替换前 N 个」；不传=全部 |
| re 路径取值 | `count is None` → `pattern.subn(repl, content)`；否则 `pattern.subn(repl, content, count=count)` | `subn` 返回 `(新串, 实际替换数)`，天然拿到 `replaced` |
| 实际替换数 | `replaced = raw_total if count is None else min(count, raw_total)`（字面路径）；re 路径取 `subn` 第二返回值 | 文件匹配数 < N 时只换到匹配上限 |
| 回显 | `count is not None and replaced < raw_total` → `"Replaced {replaced} of {raw_total} occurrence(s)..."`；否则 `"Replaced {replaced} occurrence(s)..."` | 有限替换未耗尽全部时注明分母；不夸大 |

### 字段定义（`models.py` `ReplaceFileParams` 内，4 空格）

```python
count: int | None = Field(
    default=None,
    ge=1,
    description="Maximum number of occurrences to replace, from the top of the file. "
    "None (default) replaces all; a positive integer replaces only the first N "
    "(or fewer if the file has fewer matches).",
)
```

---

## ② `regex: bool` / `case_sensitive: bool` —— 正则 / 大小写不敏感

### 决策表

| 决策点 | 结论 | 理由 |
|---|---|---|
| `regex` 默认 | `False`（字面） | 对齐 search_page 默认；默认走阶段一字面路径 |
| `case_sensitive` 默认 | **`True`**（大小写敏感） | 保留阶段一已发布行为与 `test_case_sensitive`；**有意分叉** search_page 的 `False` |
| 分支选择器 | `use_re = bool(regex) or not case_sensitive` | 默认 False → 阶段一 `str` 路径零回归 |
| `old` 处理 | regex=True：`re.compile(old, flags)`（不转义）；否则 `re.compile(re.escape(old), flags)` | regex 模式 old 是正则；字面模式 old 转义后精确匹配 |
| flags | `0 if case_sensitive else re.IGNORECASE` | 大小写不敏感叠加 IGNORECASE |
| `new` 处理 | regex=True：`pattern.subn(new, ...)`（支持 `\1`/`\g<name>`）；字面不敏感：`pattern.subn(lambda m: new, ...)`（new 字面、不展开反向引用） | regex 用户期望反向引用；字面用户期望 `\1` 当字面量 |
| 非法正则 | `re.error` 在 compile 与 subn 两处都 catch → `ActionResult(error="Invalid regex pattern {old!r}: {e}")`（subn 处文案 `"Regex substitution failed for {old!r}: {e}"`） | compile 抓坏 pattern；subn 抓坏替换模板（如非法 `\g<...>`） |

### 字段定义（`models.py`，4 空格）

```python
regex: bool = Field(
    default=False,
    description="When True, treat 'old' as a Python regular expression (re.sub semantics, "
    "including backreference expansion \\1 / \\g<name> in 'new'; escape backslashes for "
    "literal paths). When False (default), 'old' is a literal substring.",
)
case_sensitive: bool = Field(
    default=True,
    description="When True (default), match case-sensitively. When False, match "
    "case-insensitively regardless of regex mode. Note: defaults to True (unlike "
    "search_page's False) to preserve replace_file's historical case-sensitive behavior.",
)
```

---

## ③ `expected_count: int | None` —— 写前匹配数守卫

### 决策表

| 决策点 | 结论 | 理由 |
|---|---|---|
| 字段 | `expected_count: int \| None = Field(default=None, ge=0)` | None=不校验；非负整数；`ge=0` 允许「声明 0 匹配」 |
| 运行时守卫 | `None or (int 且非 bool 且 ≥0)`，否则 `error="... must be a non-negative integer"` | 同 count，拒 bool |
| 比对基准 | **原始匹配总数 `raw_total`**（全部匹配，非 `count` 限制后） | typo-guard：检测「old 写错→0」或「old 意外匹配一大片」；比 post-count 会掩盖 runaway |
| 触发时机 | `raw_total` 算出后、**软失败与 backup 之前** | 不匹配则文件不动、不产生 `.bak` |
| 不匹配文案 | `error="replace_file expected {N} match(es) for {old!r} in {path}, found {raw_total}; file unchanged"` | 明确预期/实际/未改动 |
| `expected_count=0 & raw_total=0` | 校验通过（0==0）→ 落到 `raw_total==0` 软失败分支（成功语义、不写） | 声明「确为 0 匹配」与默认零匹配同走软提示，文件不动 |

### 字段定义（`models.py`，4 空格）

```python
expected_count: int | None = Field(
    default=None,
    ge=0,
    description="If set, the file must contain exactly this many matches for the operation "
    "to proceed; on mismatch the file is left UNCHANGED and an error is returned (typo-guard "
    "against 0 or unexpectedly-many replacements). Compared against the TOTAL match count, "
    "before the 'count' limit is applied.",
)
```

---

## ④ `backup: bool` —— replace 前保留 `.bak`

### 决策表

| 决策点 | 结论 | 理由 |
|---|---|---|
| 字段 | `backup: bool = Field(default=False)` | 默认不备份（阶段一） |
| 备份原语 | `shutil.copy2(path, path + ".bak")`（保留 mtime 等元数据） | 标准库、同目录；copy2 保元数据 |
| 时机 | 读成功 + `expected_count` 校验通过 + 软失败排除**之后**，写 tmp **之前** | `.bak` = 改动前原始；校验失败/零匹配不产生 `.bak` |
| 失败语义 | 致命 → `ActionResult(error="Failed to create backup {bak}: {e}")`，不写 | 用户依赖 backup，静默继续违背预期 |
| 已存在 `.bak` | 覆盖 | 可预测；多数编辑器行为一致 |
| 白名单 | 不单独校验 `.bak` | `.bak` 与 `path` 同前缀，`path` 放行即隐含 `.bak` 放行 |
| import | 顶部加 `import shutil` | `shutil` 当前未 import |
| 后续阶段失败留 `.bak` | 保留（如 subn 抛 re.error 时 `.bak` 已写） | `.bak` = 原始 = 正是用户所要，非脏文件；不清理 |

### 字段定义（`models.py`，4 空格）

```python
backup: bool = Field(
    default=False,
    description="When True, copy the original (pre-edit) file to <path>.bak before replacing "
    "(shutil.copy2 metadata retained). Default False; .bak is overwritten if it exists.",
)
```

---

## 组合后完整方法（四项叠加的终态，供实现参照）

`src/tree_walker/tools/actions.py`，替换 `_action_replace_file`（现 `1668-1738`，4 空格）；顶部加 `import shutil`。原子写 / encoding / newline / 白名单 / 软失败 / 分级错误**全部保留**。

```python
async def _action_replace_file(self, params: dict, browser: BrowserSession) -> ActionResult:
    path = params["path"]
    old = params["old"]
    new = params["new"]
    if not old:
        # min_length=1 覆盖 schema + 直接构造；registry 不校验 execute 路径，
        # 此守卫兜底，避免 str.replace("", x) 在每字符间插入而膨胀文件。
        return ActionResult(error="replace_file 'old' must be a non-empty string")

    # 阶段二参数（registry 不校验 execute 路径，params 是 raw dict，运行时守卫）。
    enc = params.get("encoding") or "utf-8"
    newline = params.get("newline", "")
    regex = params.get("regex", False)
    case_sensitive = params.get("case_sensitive", True)  # 默认 True（分叉 search_page）
    count = params.get("count", None)
    expected_count = params.get("expected_count", None)
    backup = params.get("backup", False)

    # 运行时守卫：count 必须为 None 或正整数；expected_count 必须为 None 或非负整数。
    # bool 是 int 子类——True/False 须显式拒，否则被当 1/0。
    if count is not None and (not isinstance(count, int) or isinstance(count, bool) or count < 1):
        return ActionResult(error=f"replace_file 'count' must be a positive integer (got {count!r})")
    if expected_count is not None and (not isinstance(expected_count, int) or isinstance(expected_count, bool) or expected_count < 0):
        return ActionResult(error=f"replace_file 'expected_count' must be a non-negative integer (got {expected_count!r})")

    # 阶段二（二.C）：写路径白名单（replace_file 也是写）。.bak/.tmp 与 path 同目录同前缀，
    # path 放行即隐含 bak/tmp 放行，无需单独检查。
    allowed = self._allowed_write_paths
    if allowed and not any(path.startswith(p) for p in allowed):
        return ActionResult(error=f"File path not in allowed write paths: {path}")

    # 仅在 regex 或大小写不敏感时走 re；默认（literal+case_sensitive）保留阶段一
    # str.count/str.replace 路径，字节级不变、零回归。
    use_re = bool(regex) or not case_sensitive

    def _literal_replacer(literal: str):
        # 大小写不敏感但非 regex：把 new 当不透明字面量，不展开 \1 / \g<name>。
        return lambda m: literal

    tmp = path + ".tmp"
    bak = path + ".bak"
    try:
        # newline="" 关闭 universal-newline 翻译：读时保留原始 \r\n，写时 \n 不被译成 \r\n。
        with open(path, "r", encoding=enc, newline=newline) as f:
            content = f.read()

        # 计算原始匹配总数（expected_count 基准，也是软失败判定）。
        if use_re:
            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                pattern = re.compile(old, flags)
            except re.error as e:
                logger.warning("replace_file(%r) invalid regex %r: %s", path, old, e)
                return ActionResult(error=f"Invalid regex pattern {old!r}: {e}")
            raw_total = len(pattern.findall(content))
        else:
            raw_total = content.count(old)

        # expected_count：写前守卫，比对原始总数；不匹配则文件不动（typo-guard）。
        if expected_count is not None and raw_total != expected_count:
            msg = (f"replace_file expected {expected_count} match(es) for {old!r} in {path}, "
                   f"found {raw_total}; file unchanged")
            logger.info(msg)
            return ActionResult(error=msg)

        # 软失败：raw_total==0（含 expected_count=0 & actual=0）→ 不写、成功语义。
        if raw_total == 0:
            msg = f"No occurrences of {old!r} found in {path}; file unchanged"
            logger.info(msg)
            return ActionResult(extracted_content=msg, long_term_memory=msg)

        # 备份：读成功、校验通过后再复制原始内容到 .bak。失败致命——不写。
        if backup:
            try:
                shutil.copy2(path, bak)
            except OSError as e:
                logger.warning("replace_file(%r) backup failed: %s", path, e)
                return ActionResult(error=f"Failed to create backup {bak}: {e}")

        # 执行替换。
        if use_re:
            repl = new if regex else _literal_replacer(new)
            try:
                if count is None:
                    new_content, replaced = pattern.subn(repl, content)
                else:
                    new_content, replaced = pattern.subn(repl, content, count=count)
            except re.error as e:
                # 替换模板（new 含非法 \g<...>）也会抛 re.error。
                logger.warning("replace_file(%r) substitution failed: %s", path, e)
                return ActionResult(error=f"Regex substitution failed for {old!r}: {e}")
        else:
            if count is None:
                new_content = content.replace(old, new)
                replaced = raw_total
            else:
                new_content = content.replace(old, new, count)
                replaced = min(count, raw_total)

        # 原子写（tmp + os.replace）。写段失败清 tmp；bak 由用户显式要求，保留。
        with open(tmp, "w", encoding=enc, newline=newline) as f:
            f.write(new_content)
        os.replace(tmp, path)
    except FileNotFoundError:
        return ActionResult(error=f"File not found: {path}")
    except UnicodeDecodeError as e:
        logger.warning("replace_file(%r) decode failed: %s", path, e)
        return ActionResult(error=f"Failed to decode {path} as {enc}: {e}")
    except LookupError as e:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        logger.warning("replace_file(%r) unknown encoding %r: %s", path, enc, e)
        return ActionResult(error=f"Unknown encoding {enc!r}: {e}")
    except OSError as e:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        logger.warning("replace_file(%r) failed: %s", path, e)
        return ActionResult(error=f"Failed to replace text in {path}: {e}")

    # 回显：有限替换且未耗尽全部时注明 "of {raw_total}"；否则沿用原文案。
    # final_bytes 用替换后的 new_content（不是替换前的 content）。
    final_bytes = len(new_content.encode(enc))
    if count is not None and replaced < raw_total:
        match_clause = f"{replaced} of {raw_total} occurrence{'s' if raw_total != 1 else ''}"
    else:
        match_clause = f"{replaced} occurrence{'s' if replaced != 1 else ''}"
    memory = (
        f"Replaced {match_clause} of {old!r} with {new!r} "
        f"in {path} ({final_bytes} bytes)"
    )
    logger.info(memory)
    return ActionResult(extracted_content=memory, long_term_memory=memory)
```

> 注意：阶段一代码用 `content = content.replace(...)` 就地覆写再算 `final_bytes = len(content.encode(enc))`；本终态改用独立变量 `new_content`，回显字节数取 `len(new_content.encode(enc))`，语义一致。现有 `test_*_echo` 的字节数断言仍成立。

---

## `ReplaceFileParams` 新字段汇总（`models.py:307-325`，4 空格）

在 `newline` 字段之后追加 `regex` / `case_sensitive` / `count` / `expected_count` / `backup` 五字段（`old`/`new` 的 description 顺带补 regex 模式说明）。`extra="forbid"` 不变。

## 注册 description 更新（`models.py` `ACTION_DEFINITIONS["replace_file"]`，约 `550-558`）

```python
"replace_file": (
    ReplaceFileParams,
    "Replace occurrences of text inside an existing local file, in place. By default "
    "performs a case-sensitive literal substring replace of every non-overlapping "
    "occurrence (phase-1 behavior preserved). Set regex=True to treat 'old' as a Python "
    "regex (then 'new' supports backreferences), or case_sensitive=False for "
    "case-insensitive matching. count limits replacements to the first N (default: all). "
    "expected_count guards against typos: if the file does not contain exactly that many "
    "matches it is left unchanged and an error is returned. backup=True first copies the "
    "original to <path>.bak. old must be non-empty; zero matches returns 'no occurrences' "
    "rather than silently succeeding. Prefer this over write_file for small edits to a "
    "large file you have already read.",
    False,
),
```

---

## 文件清单

| 文件 | 改动 | 锚点 |
|---|---|---|
| `src/tree_walker/tools/models.py` | `ReplaceFileParams` 追加 `regex`/`case_sensitive`/`count`/`expected_count`/`backup` 五字段（`count` 带 `ge=1`、`expected_count` 带 `ge=0`）；`old`/`new` description 补 regex 说明；注册 description 重写 | 307-325 / 550-558 |
| `src/tree_walker/tools/actions.py` | 顶部加 `import shutil`；`_action_replace_file` 重写为四项叠加终态（保留原子写/encoding/newline/白名单/软失败/分级错误） | 11（import）/ 1668-1738 |
| `tests/test_replace_file.py` | 追加 6 个测试类（count / regex / 大小写不敏感字面 / expected_count / backup / 组合），TAB 缩进，约 35-40 用例 | 末尾追加 |
| （可选）`docs/tools-optimize/replace_file.md` | 阶段二「原子写」「写路径白名单」标注「已随 write_file PR #75 实现」，其余四项标注「见 replace_file_follow_up.md」 | 末尾阶段二节 |

---

## 测试计划

```powershell
# 相关单测
uv run python -m pytest tests/test_replace_file.py -x -v
# 覆盖率
uv run python -m pytest tests/test_replace_file.py --cov=tree_walker.tools.actions --cov-report=term-missing
# 全量回归（确保不碰 write_file/read_file/evaluate/find_elements/search_page/upload_file/save_as_pdf）
uv run python -m pytest tests/ -x -v
```

新增测试要点（均 `Tools().execute("replace_file", {...}, MagicMock())` 入口 + `tmp_path` + `_seed`/`_read`（`newline=""` 字节精确）+ `@pytest.mark.asyncio`，**TAB 缩进**，按类分组）：

- **count**：`None`=全部、`N`=前 N 个（断言 `"2 of 4 occurrences"`）、`N>匹配数`=换到上限（文案不带 "of"）、`count=0`/负数/`True`(bool) 各被拒且文件不动。
- **regex**：基础（`\d+`→`N`）、反向引用 `\3/\2/\1`、命名 `\g<v>=\g<k>`、非法 pattern → `error` 含 "Invalid regex" 且文件不动、非法 `\g<nope>` 模板 → subn 抛 `re.error` → `error` 且文件不动、`regex+case_sensitive=False` 不敏感匹配。
- **大小写不敏感字面**：`case_sensitive=False` 匹配各大小写变体；**关键**：此时 `new` 保持字面（`new=r"\1\n"` → 原样写入 `\1\n`，不展开）；默认（不传）仍是大小写敏感（回归 `test_case_sensitive`）。
- **expected_count**：匹配则放行替换；不匹配 → `error` 含 "expected N" "found M" 且**文件不动**；typo（0 匹配）守卫；`expected_count=0 & actual=0` → 软提示（成功语义、不写）；负数/`True`(bool) 被拒；**比对原始总数**（`count=1, expected_count=1`，文件 5 匹配 → `found 5` 报错、不动）；通过校验后按 count 限制替换（`"2 of 5 occurrences"`）。
- **backup**：`backup=True` 生成 `.bak`=`改动前原始`；默认无 `.bak`；覆盖已存在 `.bak`；零匹配时不产生 `.bak`（软失败在 backup 之前）；`monkeypatch` `shutil.copy2` 抛 `OSError` → `error` 含 "backup"、文件不动、无 `.tmp`。
- **组合**：`regex+count`（`"2 of 4 occurrences"`）；`case_sensitive=False+expected_count`；四项全开（regex+count+expected_count+backup，断言替换结果 + `.bak` 原始 + `"2 of 5 occurrences"`）；`expected_count` 不匹配时**不产生 `.bak`**（校验在 backup 之前）。
- **schema 校验**：`ReplaceFileParams` 直接构造——`count=None`/正整数放行、`count=0` 拒（`ge=1`）；`expected_count=0` 放行、负数拒（`ge=0`）；`regex`/`backup` 默认 `False`、`case_sensitive` 默认 `True`。

---

## 风险与回归点

| 风险 | 影响 | 缓解 |
|---|---|---|
| 默认路径行为变更（regex/大小写重路由碰阶段一） | 高 | `use_re = regex or not case_sensitive`；默认值令 `use_re=False` → 精确 `str.count`/`str.replace`；现有测试守护 |
| `count=0` 传到 `str.replace(...,0)` / `re.subn(...,0)`（替换全部 bug） | 高 | 守卫拒 `count<1`；按 `count is None` 分支、永不传 0；`test_count_zero_rejected` |
| `bool` 被当 `count`/`expected_count`（True→1） | 中 | 守卫显式 `isinstance(x, bool)` 拒；两处 bool 用例 |
| `expected_count` 比对 post-count 值（逻辑 bug） | 高 | 显式比对 `raw_total`；`test_expected_count_uses_raw_total_not_post_count` |
| `expected_count=0 & actual=0` 返回硬错误而非软提示 | 中 | 排序：校验通过（0==0）→ `raw_total==0` 软失败分支；`test_expected_count_zero_actual_zero_soft_miss` |
| regex `new` 含字面反斜杠致 re.error/损坏（用户意外） | 中 | description 注明 regex 模式反斜杠语义；`test_regex_bad_backref_template_returns_error` |
| `shutil` 未 import → 运行时 `NameError` | 高 | 顶部加 `import shutil` |
| 后续阶段失败留 `.bak`（如 subn 抛 re.error） | 低 | `.bak`=原始=用户所要，非脏；文档注明不清理 |
| 原子写/encoding/newline/白名单回归 | 高 | 终态保留这些路径；现有 `TestReplaceFileAtomicWrite`/`Encoding`/`Newline`/`Whitelist` 类全过 |
| 回显字节数用替换前 `content` | 中 | 终态用 `len(new_content.encode(enc))`；现有 `test_*_echo` 字节数断言守护 |
| 默认值变更 | 无 | 四项默认全部复现阶段一行为，现有测试零回归 |

---

## 验证方法

1. `uv run python -m pytest tests/test_replace_file.py -x -v` 全绿，覆盖率 ≥85%。
2. `uv run python -m pytest tests/ -x -v` 全量回归无回归（尤其不碰 write_file/read_file/evaluate/find_elements/search_page/upload_file/save_as_pdf）。
3. **count 冒烟**：`{"old":"a","new":"b","count":2}` on `"a a a a"` → `"b b a a"`、回显含 "2 of 4 occurrences"。
4. **regex 冒烟**：`{"old":r"(\d{4})-(\d{2})-(\d{2})","new":r"\3/\2/\1","regex":True}` on `"2024-01-02"` → `"02/01/2024"`。
5. **大小写不敏感冒烟**：`{"old":"foo","new":"X","case_sensitive":False}` on `"Foo fOO foo"` → `"X X X"`、`new=r"\1"` 保持字面。
6. **expected_count 冒烟**：`{"old":"a","new":"b","expected_count":5}` on `"a a a"` → `error` 含 "expected 5" "found 3"、文件不动。
7. **backup 冒烟**：`{...,"backup":True}` → `.bak` = 改动前原始；默认无 `.bak`。
8. **对照**：分级错误（`ActionResult(error=…)` + `logger.warning`）、`extracted_content`+`long_term_memory` 双写，均与 write_file/read_file/search_page 同族规范一致。

---

## 验收 checklist

- [ ] `ReplaceFileParams` 追加 `regex`(默认 False) / `case_sensitive`(默认 True) / `count`(ge=1) / `expected_count`(ge=0) / `backup`(默认 False)；`old`/`new` description 补 regex 说明（`models.py`）
- [ ] 注册 description 重写（`models.py` `ACTION_DEFINITIONS["replace_file"]`）
- [ ] 顶部加 `import shutil`；`_action_replace_file` 重写为四项叠加终态（保留原子写/encoding/newline/白名单/软失败/分级错误）（`actions.py`）
- [ ] 运行时守卫：`count`(None/正整数/拒 bool) / `expected_count`(None/非负/拒 bool)；`re.error` 在 compile + subn 两处 catch
- [ ] `tests/test_replace_file.py` 追加 6 类（count/regex/大小写不敏感字面/expected_count/backup/组合），TAB 缩进，全绿、覆盖率 ≥85%
- [ ] `uv run python -m pytest tests/ -x -v` 全量回归无回归
- [ ] 缩进按文件：`src/` = 4 空格、`tests/` = TAB
