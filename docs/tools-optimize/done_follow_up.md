# done 工具优化方案 · 阶段二（对齐 / 超越 browser-use 完整能力）

> 承接 `docs/tools-optimize/done.md`。阶段一（`long_term_memory` 双写 + `logger.info` + 空 text 运行时守卫 + `min_length=1` + anti-hallucination 富 description + 全量单测）已 100% 落地（`_action_done` `actions.py:2089-2104`、`DoneParams` `models.py:487-509`、registry 项 `models.py:632-638`）；本文件把 done.md「阶段二（可选，独立，对齐 / 超越 browser-use 完整能力）」一节（`:281-287`）列出的 5 项能力展开为可实施蓝图。
> 同族阶段二先例：`write_file`（`1cb0484`：原子写 + `encoding` + `allowed_write_paths` 白名单 + newline 翻译，**done 的 `files_to_display` 白名单直接对标**）、`read_file`（`f8a3894`：`offset/limit` + `allowed_read_paths` + 富文档）、`evaluate`（`3da6e40`：per-call 运行时守卫 + 大结果落盘 + `ActionResult.metadata` 扩字段，**通用副字段的先例**）。本方案在「`allowed_read_paths` 白名单复用」「`ActionResult` 通用副字段扩展」「registry 不校验 execute 路径故 handler 自加守卫」三处全面对齐同族阶段二。
> 下载自动附加（二.C）直接复用已上线的下载基建：`AgentState.downloaded_files: list[DownloadInfo]`（`views.py:67`，每步由 `step.py:163-172` 消费 `browser.consume_completed_downloads()` 填充）、`DownloadInfo(filename, url, path)`、CDP 下载事件（`browser/session.py:1349-1373`）、`tests/test_download_tracking.py`。

## 工程约束（实施时务必遵守）

- Windows + PowerShell；包用 `uv`，跑脚本 / 测试用 `uv run python ...`。测试命令 `uv run python -m pytest tests/ -x -v`。
- **缩进按文件**（已复核）：`actions.py` / `models.py` / `agent/views.py` / `agent/step.py` / `agent/agent.py` / `tools/registry.py` / `config.py` = **4 空格**；`tests/test_done.py` = **TAB**（对齐 `tests/test_replace_file.py`）。下文 src 代码片段按 4 空格给出，测试用例以 prose 描述（避免 TAB 在 markdown 里失真）。
- **registry 不校验 execute 路径**（params 是 raw dict）——见 memory `action-params-no-runtime-validation`。`DoneParams` 的 Pydantic 约束只对 schema(LLM) + 直接构造生效，**handler 必须自己加运行时守卫**。
- done **必须终止**（`is_done=True` 才退出循环，`step.py:105`）。因此阶段二新增的附件解析 / 结构化校验**任何失败都 warn + 跳过 / 兜底，绝不返回 `error`、绝不 `is_done=False`**（与阶段一空 text 兜底思路一致）；结构化校验失败时退化成 `success=False` 的兜底输出但仍 `is_done=True`。
- `actions.py` 现有 import（`os` `time` `json` `Any`）够 二.A–二.D 用；**二.E 需新增 `from pydantic import ValidationError`**（确认 `actions.py` 顶部；`ValidationError` 当前未直接 import）。`models.py` 已 import `Field` / `ConfigDict` / `create_model` 待补。
- 改完跑 `test_done.py` + 下载 / registry / ActionResult 相关回归 + 全量；覆盖率目标 >85%。不主动 commit/push。

## 阶段二分期总览与依赖

| 子期 | 能力（对应 done.md 阶段二项） | 风险 | 依赖 |
|---|---|---|---|
| **二.A** | ` - N more characters` 后缀（item 4） | 极低 | 独立 |
| **二.B** | `ActionResult.attachments` + `DoneParams.files_to_display` + `allowed_read_paths` 白名单解析（item 2，附件地基） | 低 | 独立 |
| **二.C** | 自动附加 downloads（item 3，复用已上线基建） | 低 | 二.B（写 `attachments`） |
| **二.D** | `display_files_in_done_text` 内联开关（item 5） | 低 | 二.B（读 attachments） |
| **二.E** | 结构化输出 `output_model` / `data`——**泛型重做 registry**（item 1，旗舰） | 中高 | 二.B（共享附件解析） |

**推荐落地顺序**：A → B → C → D → E，每个独立成 PR、独立可回滚、独立通过全量回归。二.E 改动面最大（触及 registry 核心），务必独立 PR + 全量回归。

---

## 二.A ` - N more characters` 后缀（item 4）—— 极低风险，最先做

对齐 browser-use 变体 A：`text` 超 100 字时在 `long_term_memory` 末尾追加 ` - {N} more characters`。阶段一已截到 100 字，本子期只补后缀。

### A.1 `_action_done` memory 行（`actions.py:2097`，4 空格）

before：
```python
        memory = f"Task completed: {success} - {text[:100]}"
```
after：
```python
        truncated = text[:100]
        memory = f"Task completed: {success} - {truncated}"
        if len(text) > 100:
            memory += f" - {len(text) - 100} more characters"
```
> 仅影响变体 A 自由文本路径（变体 B 见二.E，不走该行）。`extracted_content` 仍带全文，memory 仍是一行摘要。

### A.2 测试（`tests/test_done.py` `TestDoneEcho` 追加，TAB）

- `test_long_term_memory_appends_more_chars`：250 字符 `text` → `long_term_memory` 以 ` - 150 more characters` 结尾；`extracted_content` 仍为 250 字全文。
- `test_short_text_no_suffix`（回归）：≤100 字 → memory **不含** `more characters`。

---

## 二.B `ActionResult.attachments` + `DoneParams.files_to_display` + 白名单解析（item 2）—— 附件地基

让 done 能携带文件附件（绝对路径）。本项目 `ActionResult` 当前无 `attachments` 字段（`views.py:8-40`）——本子期给共享类型加该字段（非 done 专用，便于后续工具复用，对齐 evaluate 阶段二加 `metadata` 的先例）。

### B.1 `ActionResult` 加 `attachments`（`agent/views.py:19` 之后、紧邻 `metadata`，4 空格）

```python
    # 阶段二（二.B）：done 附件（绝对路径列表），默认 None；__str__ 不渲染它，
    # 保持 display_max_chars=500 的有界显示（附件清单改走 extracted_content）。
    attachments: list[str] | None = None
```
> `__str__` **不改**（不渲染 attachments，保 Judge 的 500 有界；附件可见性改走下方 `extracted_content` 清单）。`list` 已是 builtin。该字段为通用容器，二.C（downloads）也写它。

### B.2 `DoneParams` 加 `files_to_display`（`models.py:507` `success` 之后，4 空格）

```python
    files_to_display: list[str] = Field(
        default_factory=list,
        description=(
            "Absolute file paths to attach to the final result (downloads, saved "
            "reports, screenshots). Each must exist and be under an allowed read "
            "path; invalid paths are skipped with a warning. Shown as a short "
            "manifest in the summary."
        ),
    )
```
> `extra="forbid"` 保留（与全文件一致）。变体 B（二.E）的参数模型也带该字段（但被 registry 从 LLM schema 隐藏）。

### B.3 `_action_done` 共享附件解析（`actions.py:2089-2104`，4 空格）

在方法开头（取 `success` 之后、变体 A 取 `text` 之前）插入**共享**解析段（变体 A/B 都走它）：
```python
        # 共享：解析 files_to_display → attachments（白名单 + 存在性；done 必须终止，
        # 故任何失败只 warn + 跳过，绝不 error / 绝不 is_done=False）
        attachments: list[str] = []
        allowed = self._allowed_read_paths
        for raw in params.get("files_to_display") or []:
            p = os.path.abspath(raw)
            if allowed and not any(p.startswith(pre) for pre in allowed):  # 镜像 write_file:1595
                logger.warning("done: skip attachment outside allowed_read_paths: %s", p)
                continue
            if not os.path.isfile(p):
                logger.warning("done: skip missing attachment: %s", p)
                continue
            attachments.append(p)
```
变体 A 的 `return ActionResult(...)` 补 `attachments=attachments or None`，并在 `visible = text` 之后、非空时追加一行清单（让 `final_result()` 可见；`__str__` 仍被 500 截断，Judge 不爆）：
```python
        visible = text
        if attachments:
            visible += "\n\nAttachments: " + ", ".join(os.path.basename(a) for a in attachments)
```
> `os` 已在 `actions.py` import（evaluate 落盘用到）。`self._allowed_read_paths` 由 `Tools.__init__` 注入（`actions.py:362`）。白名单语义对标 `write_file` 的 `allowed_write_paths`（display = 读语义，复用 `allowed_read_paths`，**不新增** `allowed_done_paths`）。

### B.4 测试（`tests/test_done.py` 新增 `TestDoneAttachments`，TAB）

- `test_files_to_display_attached_as_absolute`：`files_to_display=[tmp_path/"a.txt"]`（先建文件）→ `result.attachments == [str(tmp_path/"a.txt")]`、`extracted_content` 含 `Attachments: a.txt`。
- `test_relative_path_resolved_to_absolute`：相对路径 → `attachments` 为绝对路径。
- `test_attachment_outside_allowlist_skipped`：`Tools(allowed_read_paths=[safe_dir])`、附件在白名单外 → 跳过、`attachments` 不含它、WARNING 命中、`is_done is True`。
- `test_missing_attachment_skipped`：不存在的路径 → 跳过、WARNING、`is_done is True`。
- `test_no_files_to_display_empty`：缺省 → `attachments in (None, [])`、`extracted_content` 无 `Attachments:`。
- `test_attach_path_model_field`（同步）：`DoneParams(text="ok", files_to_display=["/x"])` 接受；`files_to_display` 缺省为 `[]`。

---

## 二.C 自动附加 downloads（item 3）—— 低风险，复用已上线基建

把会话下载（`AgentState.downloaded_files`）在 done 时自动并入 `attachments`，对齐 browser-use 变体 B 的 `browser_session.downloaded_files`。**下载基建已全线上线**，本子期只接一根线。

### 关键事实（核验自代码库）

- `state.downloaded_files: list[DownloadInfo]` 跨整个会话累计（`step.py:163-172` 每步消费 `browser.consume_completed_downloads()` 追加）；`DownloadInfo.path: str | None` 即磁盘路径——正是附件要的。
- **handler 拿不到 `state`**（签名 `(self, params, browser)`，`registry.py` 派发只传两参）。改 handler 签名会波及所有动作 → **不采用**。改在 `step.py:_post_process`（持有 `results` 与 `self.state`，`step.py:103` 调用）里附加，零签名改动、零 dispatch 影响。

### C.1 `_post_process` 附加下载（`step.py:667-668` 之后，4 空格）

在 `self.state.last_result = results` / `self.state.last_model_output = model_output` **之后**、loop detector / failure 计数分支（`step.py:689-693`）**之前**插入（不改变 `consecutive_failures` 语义）：
```python
        # 二.C：把会话下载自动并入 done 结果的 attachments（对齐 browser-use 变体 B；
        # 仅 track_downloads 开启且存在带路径的下载时；去重）
        if self._track_downloads and self.state.downloaded_files:
            dl_paths = [d.path for d in self.state.downloaded_files if d.path]
            for r in results:
                if not r.is_done:
                    continue
                existing = set(r.attachments or [])
                merged = list(r.attachments or []) + [p for p in dl_paths if p not in existing]
                if merged:
                    r.attachments = merged
```
> `self._track_downloads` 既有（`AgentSettings.track_downloads`，env `TRACK_DOWNLOADS`，`step.py:165` 已用）。`r.attachments` 由二.B 初始化（无附件时为 `None` → `list(r.attachments or [])` 兜底）。去重避免 LLM 显式传同一文件 + 下载重复。

### C.2 测试（`tests/test_done.py` 新增 `TestDoneDownloads`，TAB；或并入 `tests/test_download_tracking.py`）

- `test_done_auto_attaches_downloads`：构造 `state.downloaded_files=[DownloadInfo(filename="a.csv", url="u", path=str(tmp_path/"a.csv"))]`、`track_downloads=True`；跑含 done 的一步 → done 结果 `attachments` 含 `a.csv` 路径。
- `test_downloads_dedup_with_files_to_display`：`files_to_display` 含同一路径 + 同文件被下载 → `attachments` 仅一份。
- `test_no_track_downloads_no_attach`（回归）：`track_downloads=False` → 不附加。
- `test_download_without_path_skipped`：`DownloadInfo(path=None)` → 不并入。

---

## 二.D `display_files_in_done_text` 内联开关（item 5）—— 低风险

控制是否把附件文件内容内联进 `extracted_content` 的 `Attachments:` 段（对齐 browser-use）。默认 off（opt-in）。**仅变体 A 生效**——变体 B 的 `extracted_content` 是结构化 JSON，内联会破坏 JSON（见二.E 注）。

### D.1 `config.py` 开关 + cap（`AgentSettings` `:92` `allowed_read_paths` 之后；`TruncationSettings` `:60` `eval_output_dir` 之后，4 空格）

```python
    # AgentSettings
    display_files_in_done_text: bool = False
```
```python
    # TruncationSettings
    done_attachment_max_chars: int = 2000   # 单个附件内联上限（display_files_in_done_text 开启时）
```
`load_settings` 补两条 env（对齐既有布尔 / int 开关写法，`:263` `track_downloads` 与 `:279` `eval_output_dir` 附近）：
```python
        display_files_in_done_text=os.environ.get("AGENT_DISPLAY_FILES_IN_DONE_TEXT", "").lower() == "true",
        # truncation= 内：
            done_attachment_max_chars=int(os.environ.get("AGENT_DONE_ATTACHMENT_MAX_CHARS", "2000")),
```

### D.2 `Tools.__init__` 透传（`actions.py:355`，4 空格）

签名加 `display_files_in_done_text: bool = False`，存 `self._display_files_in_done_text = display_files_in_done_text`。

### D.3 `agent.py:57` 透传

`Tools(..., display_files_in_done_text=_settings.agent.display_files_in_done_text)`。

### D.4 `_action_done` 内联段（变体 A 的 `visible` 拼装里，二.B 清单之后，4 空格）

```python
        if self._display_files_in_done_text and attachments:
            cap = self._truncation.done_attachment_max_chars
            inline_parts = ["", "Attachments:"]
            for a in attachments:
                try:
                    with open(a, "r", encoding="utf-8", errors="replace") as f:
                        body = f.read(cap)
                except OSError as e:
                    logger.warning("done: skip inline read %s: %s", a, e)
                    continue
                inline_parts.append(f"--- {a} ---\n{body}")
            if len(inline_parts) > 1:
                visible += "\n" + "\n".join(inline_parts)
```
> 附件已在二.B 过白名单 + 存在性校验，此处直接读。`errors="replace"` 防二进制炸解码（对齐 read_file 思路）。开 on 时 `extracted_content` 变长，但 `__str__` 仍 500 截断（Judge 安全），`final_result()` 得全文（用户可读附件）。**变体 B 不执行此段**（JSON 纯净）。

### D.5 测试（`tests/test_done.py` 新增 `TestDoneInlineAttachments`，TAB）

- `test_inline_off_by_default`：缺省 → `extracted_content` 无 `--- ... ---`。
- `test_inline_on_embeds_content`：`Tools(display_files_in_done_text=True)`、附件含 `"hello"` → `extracted_content` 含 `--- <path> ---` 与 `hello`。
- `test_inline_caps_large_file`：附件 > cap → 内联正文长度 ≤ cap。
- `test_inline_read_failure_skipped`：附件读抛 `OSError`（monkeypatch `open`）→ warn + 跳过该文件、`is_done is True`。
- `test_inline_only_variant_a`：变体 B（`output_model` 设置，二.E）下即使开关 on 也不内联（`extracted_content` 仍是纯 JSON）。

---

## 二.E 结构化输出（item 1）—— 泛型重做 registry，旗舰，中高风险

目标：对齐 browser-use `Tools(output_model=T)` → 变体 B（`StructuredOutputAction[T]`）；`output_model=None` 时仍走变体 A（自由文本 `DoneParams`，零回归）。**用户已选定泛型重做 registry 路线**（最大对齐 browser-use，改动面最大）。

### 设计决策（与 browser-use / 本项目约定的关系）

- **变体 B 参数模型用 `pydantic.create_model` 动态生成**（`data: T` 必填 + `success`/`files_to_display` 隐藏），而非把 `ACTION_DEFINITIONS` 改成 `Generic` 类型——`RegisteredAction.param_model` 仍是 `type[BaseModel]`（不动类型签名），只是注册时传入**按 `output_model` 解析出的具体类型**。这是「registry 级支持结构化输出」的最小完整形态：`ActionRegistry` 持有 `output_model`、`get_tool_schema` 据此对 done 做 schema 隐藏。
- **schema 隐藏**：变体 B 模型含 `success`/`files_to_display`，但用 `_hide_fields_from_schema` 把二者从 LLM 可见 schema 删掉（镜像 browser-use `_hide_internal_fields_from_schema`）→ LLM 只见 `data`（用户模型形状）。
- **附件解析共享**：变体 B 也解析 `files_to_display` → `attachments`（二.B 那段在变体分支之前），但 `extracted_content` 保持**纯 JSON**（不追加清单 / 不内联，避免破坏结构化输出）。
- **`output_model` 是运行期 Pydantic 类**，不能来自 env → 经 `Agent.__init__(output_model=...)` 程序化传入（对标 `extraction_schema` 也是程序化注入的先例，`agent.py:62-66`）。
- **兜底安全**：`_FALLBACK_DONE_OUTPUT`（`step.py:33-38`）直接 `return ActionResult(...)` 绕过校验，构造的是变体 A 形态（有 `text`）——变体 B 下若 LLM 回空触发兜底，兜底仍产出合法 `ActionResult`（不经 `DoneParams` 校验），循环照常终止。文档风险表点明。

### E.1 `ActionRegistry` 持有 `output_model` + schema 隐藏助手（`tools/registry.py`，4 空格）

`__init__`（`:27`）加参：
```python
    def __init__(self, output_model: type[BaseModel] | None = None) -> None:
        self.actions: dict[str, RegisteredAction] = {}
        self.output_model = output_model
```
模块级（`RegisteredAction` 之后）加隐藏助手：
```python
def _hide_fields_from_schema(schema: dict, fields: Iterable[str]) -> dict:
    """Return a copy of a Pydantic JSON schema with ``fields`` removed from
    ``properties`` and ``required`` (mirrors browser-use
    ``_hide_internal_fields_from_schema``). Used so variant-B done exposes only
    ``data`` to the LLM while the handler still accepts success/files_to_display.
    """
    import copy
    out = copy.deepcopy(schema)
    props = out.get("properties", {})
    for f in fields:
        props.pop(f, None)
    req = out.get("required")
    if isinstance(req, list):
        out["required"] = [r for r in req if r not in fields]
    return out
```
> `Iterable` 需在 `registry.py:7` 的 `typing` import 里（当前只有 `Any`，补 `Iterable`；或就地用 `tuple`/`list` 类型注解避免新 import）。

`get_tool_schema`（`:82-85` 循环之后）对 done 做隐藏：
```python
        for name in action_names:
            act = self.actions[name]
            action_descriptions[name] = act.description
            schema = act.param_model.model_json_schema()
            if name == "done" and self.output_model is not None:
                schema = _hide_fields_from_schema(schema, {"success", "files_to_display"})
            params_by_action[name] = schema
```

### E.2 变体 B 参数模型工厂（`tools/models.py`，`DoneParams` 之后，4 空格）

顶部补 import：`from pydantic import BaseModel, ConfigDict, Field, create_model`（`create_model` 新增）。
```python
def make_structured_done_params(output_model: type[BaseModel]) -> type[BaseModel]:
    """Build the variant-B done param model: ``data: output_model`` (required) +
    hidden ``success``/``files_to_display``. Mirrors browser-use
    ``StructuredOutputAction[T]``. The registry hides success/files_to_display
    from the LLM schema (E.1); the handler still reads them with defaults.
    """
    return create_model(
        "StructuredDoneParams",
        data=(output_model, Field(..., description="Structured final output.")),
        success=(bool, Field(default=True)),
        files_to_display=(list[str], Field(default_factory=list)),
        __config__=ConfigDict(extra="forbid"),
    )
```
> `data` 必填、类型为用户模型；`model_json_schema()` 会把用户模型字段嵌进 `$defs`，LLM 可见其结构。

### E.3 `Tools.__init__` + `_register_all`（`actions.py:355 / 411`，4 空格）

`__init__` 签名加 `output_model: type[BaseModel] | None = None`，**并在 `self._register_all()` 之前**设 `self._output_model = output_model`、用 `ActionRegistry(output_model=output_model)`（顺序关键：`_register_all` 要读 `self._output_model`）：
```python
    def __init__(self, truncation=None, allowed_upload_paths=None, allowed_write_paths=None,
                 allowed_read_paths=None, display_files_in_done_text=False, output_model=None) -> None:
        self._output_model = output_model
        self.registry = ActionRegistry(output_model=output_model)
        self._register_all()
        self._cached_browser_state = None
        self._truncation = truncation or TruncationSettings()
        self._allowed_upload_paths = allowed_upload_paths
        self._allowed_write_paths = allowed_write_paths
        self._allowed_read_paths = allowed_read_paths
        self._display_files_in_done_text = display_files_in_done_text
```
`_register_all` 注册 done 时解析变体 A/B（其余动作照旧）：
```python
    def _register_all(self) -> None:
        for name, (param_model, description, terminates) in ACTION_DEFINITIONS.items():
            if name == "done" and self._output_model is not None:
                param_model = make_structured_done_params(self._output_model)  # 变体 B
            handler = getattr(self, f"_action_{name}", None)
            if handler is None:
                ...  # 原逻辑
```

### E.4 `_action_done` 变体 B 分支（`actions.py:2089`，4 空格；顶部补 `from pydantic import ValidationError`）

在二.B 共享附件解析**之后**、变体 A 取 `text` **之前**插入变体 B 分支：
```python
        # 变体 B：结构化输出（二.E）
        if self._output_model is not None:
            try:
                data = self._output_model.model_validate(params["data"])
            except (ValidationError, KeyError, TypeError) as e:
                # done 必须终止：结构化校验失败 → success=False 兜底，仍 is_done=True
                logger.warning("done: structured data invalid: %s", e)
                return ActionResult(
                    is_done=True,
                    success=False,
                    extracted_content=f"(invalid structured output: {e})",
                    long_term_memory="Task completed: False - invalid structured output",
                    attachments=attachments or None,
                )
            payload = data.model_dump(mode="json")
            logger.info(f"Task completed (structured): {success}")
            return ActionResult(
                is_done=True,
                success=success,
                extracted_content=json.dumps(payload, ensure_ascii=False, indent=2),
                long_term_memory=f"Task completed (structured): {success}",
                metadata={"structured_output": payload},
                attachments=attachments or None,
            )
        # 变体 A：现有自由文本逻辑（阶段一 + 二.A 后缀 + 二.B 清单 + 二.D 内联）...
```
> `extracted_content` 为**纯 JSON**（`model_dump(mode="json")`），便于下游机读；原始结构化数据另存 `metadata["structured_output"]`（复用 evaluate 阶段二加的 `metadata` 通用字段）。变体 B **不追加附件清单 / 不内联**（保 JSON 纯净；附件仍由 `attachments` 字段 + 二.C downloads 携带）。`success` 默认 True（LLM schema 隐藏了它，不会回传；若回传则用回传值）。

### E.5 `agent.py` 透传 `output_model`（`agent/agent.py`，4 空格）

`Agent.__init__(..., output_model: type[BaseModel] | None = None)`，`:57` 改为
```python
        self.tools = tools or Tools(
            truncation=_settings.truncation,
            allowed_upload_paths=_settings.allowed_upload_paths,
            allowed_write_paths=_settings.allowed_write_paths,
            allowed_read_paths=_settings.allowed_read_paths,
            display_files_in_done_text=_settings.agent.display_files_in_done_text,
            output_model=output_model,
        )
```

### E.6 测试（`tests/test_done.py` 新增 `TestDoneStructuredOutput`，TAB）

- `test_structured_done_serializes_data`：`Tools(output_model=SomeModel)`、`execute("done", {"data": {...}}, ...)` → `extracted_content` 为该模型 JSON（`json.loads` 回来等于 `data`）、`metadata["structured_output"] == data`、`success is True`、`is_done is True`。
- `test_structured_invalid_data_falls_back`：非法 `data` → `success is False`、`is_done is True`、`extracted_content` 含 `invalid structured output`。
- `test_structured_schema_hides_internal_fields`：`Tools(output_model=SomeModel).registry.get_tool_schema(output_mode="standard")` → done 的 params schema **不含** `success`/`files_to_display`、`data` 含 SomeModel 字段。
- `test_structured_schema_exposes_data_fields`：done schema 的 `data` 含 SomeModel 各字段名。
- `test_no_output_model_is_variant_a`（回归）：`Tools()` → done schema 仍是 `DoneParams`（含 `text`/`success`/`files_to_display`，无 `data`）；`execute("done", {"text": "ok"})` 走变体 A。
- `test_structured_done_with_attachments`：变体 B + `files_to_display` → `attachments` 含路径、`extracted_content` 仍为纯 JSON（无 `Attachments:` 清单）。

---

## 阶段二文件清单（汇总）

| 文件 | 改动 | 子期 | 缩进 |
|---|---|---|---|
| `src/tree_walker/agent/views.py` | `ActionResult` 加 `attachments: list[str] \| None = None` | B | 4 空格 |
| `src/tree_walker/tools/models.py` | `DoneParams` 加 `files_to_display`（B）；顶部 `create_model` import + `make_structured_done_params` 工厂（E） | B, E | 4 空格 |
| `src/tree_walker/tools/registry.py` | `ActionRegistry.__init__(output_model=)` + `_hide_fields_from_schema` + `get_tool_schema` done 隐藏；`Iterable` import | E | 4 空格 |
| `src/tree_walker/tools/actions.py` | `_action_done`：二.A 后缀 + 二.B 附件解析/清单 + 二.D 内联 + 二.E 变体 B 分支；`Tools.__init__` 加 `display_files_in_done_text`/`output_model`、`_register_all` 解析变体；顶部 `ValidationError` import | A–E | 4 空格 |
| `src/tree_walker/agent/step.py` | `_post_process` 附加 downloads | C | 4 空格 |
| `src/tree_walker/config.py` | `AgentSettings.display_files_in_done_text` + `TruncationSettings.done_attachment_max_chars` + 两条 env | D | 4 空格 |
| `src/tree_walker/agent/agent.py` | `Agent.__init__(output_model=)` + `:57` 透传 `display_files_in_done_text`/`output_model` | D, E | 4 空格 |
| `tests/test_done.py` | 追加 `TestDoneAttachments`/`TestDoneDownloads`/`TestDoneInlineAttachments`/`TestDoneStructuredOutput` + echo 后缀用例 | A–E | TAB |
| `docs/Tools技术细节/04_动作清单与CDP映射.md` | §4.3 同步阶段二新行为（附件 / 结构化 / 下载自动附加）+ 修订 stale 行号 | 全 | — |

## 测试计划

```powershell
# 单文件（含阶段二新增）
uv run python -m pytest tests/test_done.py -x -v
# 覆盖率
uv run python -m pytest tests/test_done.py --cov=tree_walker.tools.actions --cov-report=term-missing
# 下载 / registry / ActionResult / Judge 消费方回归
uv run python -m pytest tests/test_download_tracking.py tests/test_action_registry.py tests/test_action_result_semantics.py tests/test_judge.py tests/test_force_done_schema.py -x -v
# 全量回归
uv run python -m pytest tests/ -x -v
```
> 覆盖率目标 >85%；`test_done.py` 扩到 ≥7 个测试类（Basic/Echo/EmptyText/Attachments/Downloads/Inline/Structured）。二.C 失败语义回归：确认 `_post_process` 新增段不改变 `consecutive_failures` 计数（`step.py:689-693`）。

## 风险与回归点

| 风险 | 影响 | 缓解 |
|---|---|---|
| done 恒 `is_done=True` 被附件/结构化校验破坏 | 循环不终止（致命） | 附件失败只 warn+跳过；结构化失败兜底 `success=False` 仍 `is_done=True`；`TestDoneAttachments`/`TestDoneStructuredOutput` 固化 |
| `attachments` 进 `__str__` 撑爆 Judge 上下文 | Judge 输入膨胀 | `__str__` **不渲染** attachments；可见性走 `extracted_content` 清单（被 500 截断） |
| 二.C 改 `_post_process` 影响 failure 计数 | 误判失败 / 不终止 | 新增段置于 failure 计数分支**之前**且不改 `consecutive_failures`；`test_download_tracking` + 全量回归 |
| 二.E registry 改动是核心面 | schema 生成 / 注册 / dispatch 回归 | 变体 A 严格零回归（`output_model=None`）；`test_action_registry.py` + `TestDoneStructuredOutput` 覆盖 schema 隐藏；独立 PR |
| 变体 B 下 `_FALLBACK_DONE_OUTPUT` 构造变体 A 形态 | 兜底产出含 `text` 而非 `data` | 兜底直接 `return ActionResult(...)` 绕过 `DoneParams` 校验，仍是合法终止结果；风险表点明，不阻塞 |
| 变体 B `create_model` + `$defs` schema 形状 | LLM 看到的 `data` 结构异常 | `test_structured_schema_exposes_data_fields` 固化用户模型字段可见 |
| `files_to_display` 白名单误拒合法附件 | 附件丢失 | 复用 `allowed_read_paths`（display=读语义）；WARNING 可见；默认 `None` 不拦 |
| 变体 B `extracted_content` 非 JSON（被清单/内联污染） | 下游机读失败 | 变体 B 严格纯 JSON，清单/内联仅变体 A；`test_structured_done_with_attachments` 固化 |

## 验证方法

1. **单测全绿 + 覆盖率 ≥85%**（`test_done.py` + `--cov`）。
2. **全量回归**：`pytest tests/ -x -v`，确认下载 / registry / ActionResult / Judge / force-done 消费方无 regression。
3. **附件冒烟**（二.B，PowerShell 单行走 `Tools().execute`）：`done({text:"ok", files_to_display:["<abs>"]})` → `attachments` 含绝对路径、`extracted_content` 含 `Attachments:`。
4. **下载冒烟**（二.C）：`track_downloads=True` + 造 `state.downloaded_files` → done 结果 `attachments` 含下载 path。
5. **内联冒烟**（二.D）：`Tools(display_files_in_done_text=True)` → `extracted_content` 含 `--- <path> ---`。
6. **结构化冒烟**（二.E）：`Tools(output_model=SomeModel)` → `registry.get_tool_schema()` 中 done 隐藏 success/files_to_display、`data` 为 SomeModel 形状；`execute("done", {"data":{...}})` → `extracted_content` 为 JSON、`metadata["structured_output"]` 命中。
7. **回归对照**：与 `write_file`（`1cb0484`）白名单、`evaluate`（`3da6e40`）`metadata` 副字段、下载基建（`session.py`/`test_download_tracking.py`）逐条比对一致。

## 验收 checklist（按子阶段，各独立可合并）

- **二.A**：[ ] memory 行加 ` - N more characters` 后缀 [ ] 2 用例
- **二.B**：[ ] `ActionResult.attachments` 字段（`__str__` 不动） [ ] `DoneParams.files_to_display` [ ] `_action_done` 共享附件解析 + 清单 [ ] ~6 用例
- **二.C**：[ ] `_post_process` 附加 downloads（`track_downloads` 门控、去重） [ ] failure 计数无回归 [ ] ~4 用例
- **二.D**：[ ] `display_files_in_done_text` 开关 + `done_attachment_max_chars` cap（config + env） [ ] `Tools.__init__`/`agent.py` 透传 [ ] `_action_done` 内联段（仅变体 A） [ ] ~5 用例
- **二.E**：[ ] `ActionRegistry(output_model=)` + `_hide_fields_from_schema` + `get_tool_schema` 隐藏 [ ] `make_structured_done_params` 工厂 [ ] `Tools.__init__`/`_register_all`/`_action_done` 变体 B 分支 [ ] `Agent` 透传 `output_model` [ ] ~6 用例
- **全期**：[ ] 全量 `pytest tests/ -x -v` 无 regression [ ] §4.3 同步 + stale 行号修订 [ ] 缩进按文件（src 4 空格 / tests TAB） [ ] 覆盖率 >85%

---

## 实施备忘（实现阶段发现的偏差，已落地于 `feat/done-stage2`）

> 以下三条是编码时暴露、方案原文未预见、已按下方方式实现并补单测的点。

1. **`_flatten_params` 会误吞变体 B 的 `data`**（`actions.py`）。原启发式「单个嵌套 dict 视为 LLM 包裹并展开」会把 `{"data": {...}}` 展开成内层 dict，导致 `params["data"]` 取不到。修复：`_flatten_params` 由 `@staticmethod` 改为实例方法，展开前先查 `self.registry.actions[action_name].param_model.model_fields`——若该单键是动作真字段（如 done 变体 B 的 `data`），则不展开。`test_structured_serializes_data` 固化。零回归（其它动作无 dict 值单字段参数）。
2. **schema 隐藏的有效位置是 `get_action_descriptions_text`，不是 `get_tool_schema`**（`registry.py`）。核验发现 `get_tool_schema` 构造的 `params_by_action` **从不进返回的 tool schema**（`action.params` 是通用 `{type: object}`，详情走 system prompt 的 `get_action_descriptions_text` 文本）。故 `_hide_fields_from_schema` 只在 `get_action_descriptions_text` 应用（LLM 实际参数面）；`get_tool_schema` 里的隐藏作为死代码已移除。`test_structured_descriptions_hide_internal_fields` / `test_variant_a_descriptions_show_text_success_files` 固化（断言 params 段而非整行，避开 done 动作描述里的 `success=False` 字样）。
3. **二.C 下载合并抽成纯函数 `_attach_downloads_to_done_results(results, downloaded_files)`**（`step.py` 模块级，`_post_process` 门控 `track_downloads` 后调用）。原因：`_post_process` 依赖大量 `self.*`，直接构造 StepPipeline 单测成本高；抽纯函数后 `TestDoneDownloads` 4 例直接覆盖（done/非 done/去重/无 path）。`_post_process` 调用点本身由既有 step 集成测试覆盖（`step.py` 该行不在 missing 列表）。

**测试结果**：`test_done.py` 42 例全过（含二.A–二.E 新增）；全量 `pytest tests/` **1510 passed**；改动模块覆盖率 actions.py 95% / models.py 98% / registry.py 100% / config.py 84%；项目整体 83%（被未触及的 cli.py 0% / tui 37–40% / dom.py 43% 拖低，属既有基线，本次新增代码本身高覆盖）。

