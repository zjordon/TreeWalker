# save_as_pdf 工具优化方案（分阶段）

> 参照 browser-use（`browser_use/tools/service.py:1517-1603` save_as_pdf、`browser_use/tools/views.py:161-172` SaveAsPdfAction）完善本项目 save_as_pdf 工具。
> 相关现状文档：`docs/Tools技术细节/04_动作清单与CDP映射.md` 的 4.14 节；参考标杆：`browser-use/docs/Tools技术细节/05-动作详解-浏览器交互.md` 的 15. save_as_pdf 节。
> 同族先例：`docs/tools-optimize/screenshot.md`（screenshot 阶段一已落地，commit `b1fc97e` / PR #38）——本方案在结构、错误处理、封装思路上全面对齐 screenshot 阶段一。

---

## Context（为什么做这个改动）

当前 TreeWalker 的 `save_as_pdf` 是一个最小实现，明显落后于刚做完阶段一优化的 `screenshot`，也落后于参照标杆 browser-use：

1. **无封装**：`_action_save_as_pdf`（`actions.py:854-864`，4 空格）**直穿** `browser.client.send.Page.printToPDF`，没有 `BrowserSession` 方法。项目自己的文档 `04_动作清单与CDP映射.md` 4.14 节标注「PDF 没有专门封装」是 smell——其它页面输出动作（screenshot→`take_screenshot`、navigate→`navigate`）都走 session 封装。
2. **零参数化**：`SaveAsPdfParams`（`models.py:125-127`，4 空格）只有一个必填 `path: str`，`extra="forbid"`；CDP 调用写死 `{"printBackground": True}`。纸张/方向/缩放全用 CDP 默认（纵向 US-Letter 8.5×11、约 1cm 边距、scale 1.0、无页眉页脚）。
3. **错误冒泡**：handler 内无 try/except，CDP 失败或写盘 `OSError` 全部冒泡到 `Tools.execute` 通用 catch（`actions.py:181-183`）。兄弟 `_action_screenshot`（`actions.py:823-852`）是分级捕获（CDP 异常 / `OSError` 分别给明确 `ActionResult(error=...)`），save_as_pdf 没有这层保护。
4. **回显不足**：成功只返回 `ActionResult(extracted_content=f"PDF saved to {path}")`，不回显字节数、不带上纸张/方向等 meta（screenshot 会回显 `({len} bytes)` + format/full_page/clip）。写盘前不 `makedirs` 父目录（对比 `write_file` `actions.py:1120` 会 `os.makedirs(..., exist_ok=True)`）——LLM 给的路径若父目录不存在会直接 `FileNotFoundError`。
5. **零测试**：`tests/` 下无 `test_save_as_pdf.py`，CDP 组装 / action 成功失败 / 文件 IO 一条覆盖都没有。

**参照标杆 browser-use 的做法**（`browser_use/tools/service.py:1517-1603`、参数模型 `views.py:161-172`）：暴露 `file_name / print_background / landscape / scale / paper_format` 五参；纸张尺寸英寸查表（letter/legal/a4/a3/tabloid），未知格式静默回退 letter；硬编码 `preferCSSPageSize=True`；base64 解码写文件；CDP 调用包 30s `asyncio.wait_for`；返回带 `attachments=[path]` 的 `ActionResult`。

**预期结果**：让 `save_as_pdf` 参数化（对齐 browser-use 暴露面）、把 CDP 调用收敛进 `BrowserSession.print_to_pdf`、错误分级捕获 + 回显字节数 + 自动建父目录、补齐单测覆盖率 ≥85%。

---

## 工程约束（实施时务必遵守）

- Windows + PowerShell；包用 `uv`，跑脚本/测试用 `uv run python ...`。测试命令 `uv run python -m pytest tests/ -x -v`。
- **缩进按文件**（已复核）：`session.py`（`take_screenshot` 所在 body 段）/ `actions.py` / `models.py` = **4 空格**；`tests/test_save_as_pdf.py` = **TAB**（对齐 `tests/test_screenshot.py`）。下文代码片段均按目标文件缩进给出。
- 改完跑相关单测 + 全量回归；覆盖率目标 >85%。
- 不主动 `git commit` / `git push`。
- `base64` 已是 `session.py` 模块级 import（`take_screenshot` 在 `session.py:764` 直接用 `base64.b64decode`），新方法无需再 import；`os` 已是 `actions.py` 模块级 import（`write_file` 在 `actions.py:1120` 已用 `os.makedirs`）；`Literal` 已在 `models.py` 顶部导入（`ScreenshotParams:115` 已用）。

---

## 与 browser-use 的关键差异（有意为之，不照搬）

1. **文件名模型 = 保留 `path`（必填），不引入沙箱/标题自动命名/重名自增。** browser-use 用 `file_name` + 沙箱 `FileSystem.get_dir()` + 页面标题自动命名 + 重名自增 `(1)`。TreeWalker **没有 FileSystem 沙箱**——`screenshot.save_path` / `write_file.path` 都是 LLM 给全路径。保留 `path` 以与项目一致；强行移植 browser-use 的沙箱模型要新建一整套文件系统抽象，不在本次范围。
2. **`paper_format` 用 `Literal` 而非 browser-use 的自由 `str`。** browser-use 用 `str` + 运行时小写查表、未知值**静默回退** letter（LLM 拼错不报错却出意外尺寸）。改用 `Literal["letter","legal","a4","a3","tabloid"]`，在 Pydantic 校验阶段就拒绝非法值——与项目 `StepPipeline._validate_action_params`（`step.py:459-483`）的 ValidationError 自动重试机制契合。
3. **不加 `asyncio.wait_for` 超时。** browser-use 包了 30s。项目 `take_screenshot` 不加（`screenshot.md` 的「1.7 CDP 超时 —— 不新增专用条目」明确不加）；`cdp_timeout.py` 是 DOM 批量取数专用（`run_cdp_batch` 两阶段超时+重试），不适用单次 `printToPDF`。**对齐 screenshot，不加超时**。
4. **不加 `attachments`。** browser-use 返回 `ActionResult(attachments=[path])`；TreeWalker 的 `ActionResult` 无此字段（`agent/views.py:8-37`），且 `success=True` 仅 `is_done` 时允许。对齐 screenshot：成功只设 `extracted_content`。

---

## 阶段一：参数化 + 封装 + 错误处理 + 测试（优先做，风险低，对齐 browser-use 暴露面）

### 1.1 新增 `BrowserSession.print_to_pdf`（`session.py`，4 空格，紧邻 `take_screenshot` `:764` 之后）

镜像 `take_screenshot`（`session.py:712-764`）的形状：纸张英寸查表 → 组装 CDP params → `self.client.send.Page.printToPDF` → `RuntimeError` on no data → `logger.warning` + re-raise on CDP 异常。`preferCSSPageSize=True` 硬编码（与 browser-use 一致）。

```python
async def print_to_pdf(
    self,
    paper_format: str = "letter",
    landscape: bool = False,
    print_background: bool = True,
    scale: float = 1.0,
    wait_settle: bool = False,
) -> bytes:
    """Render the current page to PDF bytes via CDP Page.printToPDF.

    Args:
        paper_format: 'letter' | 'legal' | 'a4' | 'a3' | 'tabloid'.
        landscape: landscape orientation.
        print_background: include background graphics/colors.
        scale: render scale (0.1-2.0).
        wait_settle: poll document.readyState to 'complete' before printing.

    Raises:
        RuntimeError: if CDP returns no 'data' field.
    """
    paper_sizes = {  # 英寸 (width, height)
        "letter": (8.5, 11.0),
        "legal": (8.5, 14.0),
        "a4": (8.27, 11.69),
        "a3": (11.69, 16.54),
        "tabloid": (11.0, 17.0),
    }
    paper_width, paper_height = paper_sizes.get(paper_format.lower(), (8.5, 11.0))

    if wait_settle:
        try:
            await self._wait_for_page_settle()
        except Exception as e:
            logger.warning("Pre-pdf wait_settle failed: %s", e)

    params: dict = {
        "printBackground": print_background,
        "landscape": landscape,
        "scale": scale,
        "paperWidth": paper_width,
        "paperHeight": paper_height,
        "preferCSSPageSize": True,
    }
    try:
        result = await self.client.send.Page.printToPDF(
            params,
            session_id=self.current_session_id,
        )
    except Exception as e:
        logger.warning("Page.printToPDF failed: %s", e)
        raise

    if not isinstance(result, dict) or "data" not in result:
        raise RuntimeError("printToPDF failed - no data returned")
    return base64.b64decode(result["data"])
```

> 说明：`wait_settle` 内部参数（对齐 `take_screenshot` 的 `wait_settle`），阶段一 action 层**不暴露给 LLM**、默认不等待（与 browser-use 一致）。保留参数位是为了与 screenshot 形状对称、供将来按需开启。

### 1.2 `SaveAsPdfParams` 扩展（`models.py:125-127`，4 空格）

before：
```python
class SaveAsPdfParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(description="File path to save the PDF")
```
after：
```python
class SaveAsPdfParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(description="File path to save the PDF (parent dirs auto-created).")
    paper_format: Literal["letter", "legal", "a4", "a3", "tabloid"] = Field(
        default="letter", description="Paper size."
    )
    landscape: bool = Field(default=False, description="Landscape orientation.")
    print_background: bool = Field(default=True, description="Include background graphics/colors.")
    scale: float = Field(default=1.0, ge=0.1, le=2.0, description="Render scale (0.1-2.0).")
```

### 1.3 `_action_save_as_pdf` 重写（`actions.py:854-864`，4 空格）

镜像 `_action_screenshot`（`actions.py:823-852`）：`.get()` 解包 → 委托 `browser.print_to_pdf(...)` → try/except `logger.warning` + `ActionResult(error=...)` → 单独 `OSError` 捕获写盘 → `makedirs` 父目录 → 回显字节数 + meta。

before：
```python
async def _action_save_as_pdf(self, params: dict, browser: BrowserSession) -> ActionResult:
    import base64
    result = await browser.client.send.Page.printToPDF(
        {"printBackground": True},
        session_id=browser.current_session_id,
    )
    pdf_data = base64.b64decode(result["data"])
    path = params["path"]
    with open(path, "wb") as f:
        f.write(pdf_data)
    return ActionResult(extracted_content=f"PDF saved to {path}")
```
after：
```python
async def _action_save_as_pdf(self, params: dict, browser: BrowserSession) -> ActionResult:
    path: str = params["path"]
    paper_format: str = params.get("paper_format", "letter")
    landscape: bool = params.get("landscape", False)
    print_background: bool = params.get("print_background", True)
    scale: float = params.get("scale", 1.0)

    try:
        pdf_bytes = await browser.print_to_pdf(
            paper_format=paper_format,
            landscape=landscape,
            print_background=print_background,
            scale=scale,
        )
    except Exception as e:
        logger.warning("save_as_pdf action failed: %s", e)
        return ActionResult(error=f"Failed to generate PDF: {e}")

    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            f.write(pdf_bytes)
    except OSError as e:
        return ActionResult(error=f"Failed to save PDF to {path}: {e}")

    meta = f"paper={paper_format}, {len(pdf_bytes)} bytes"
    if landscape:
        meta += ", landscape"
    return ActionResult(extracted_content=f"PDF saved to {path} ({meta})")
```

> `path` 仍是必填、用 `params["path"]`（缺失会 KeyError，由 `Tools.execute` 通用 catch 兜底——与现状一致；screenshot 的 `save_path` 是可选才用 `.get`）。可选 PDF 选项一律 `.get(..., default)`。

### 1.4 更新 `ACTION_DEFINITIONS` description（`models.py:225`，4 空格）

before：
```python
"save_as_pdf": (SaveAsPdfParams, "Save the current page as a PDF file", False),
```
after：
```python
"save_as_pdf": (
    SaveAsPdfParams,
    "Save the current page as a PDF. Supports paper_format (letter/legal/a4/a3/tabloid), "
    "landscape, scale (0.1-2.0), print_background.",
    False,
),
```

> `terminates_sequence` 保持 `False`——save_as_pdf 不导航/不切页，可留在 `multi_act` 序列里。

### 1.5 新增 `tests/test_save_as_pdf.py`（TAB 缩进，对齐 `tests/test_screenshot.py`）

镜像 `test_screenshot.py` 的 mock 工厂模式（`test_screenshot.py:19-57`）：

- `_make_mock_cdp_client(pdf_return=..., pdf_side_effect=...)`：可控 `client.send.Page.printToPDF`；其余 `Target.getTargets` / `Target.attachToTarget` / `Target.setAutoAttach` / `Page.enable` / `DOM.enable` / `Runtime.evaluate` 与 screenshot 工厂一致（`Runtime.evaluate` 返回 `{"result":{"value":...}}` 让 `start()` 通过）。
- `_start_session(client)`：`patch("tree_walker.browser.session.CDPClient", return_value=client)` + `await session.start()`，绑定真实 `BrowserSession`。
- `_captured_params(client)`：读 `client.send.Page.printToPDF.call_args` 的 `args[0]`。
- **`print_to_pdf` CDP param 组装测试**：
  - 默认参 → `{printBackground:True, landscape:False, scale:1.0, paperWidth:8.5, paperHeight:11.0, preferCSSPageSize:True}`；
  - `paper_format="a4"` → `paperWidth=8.27, paperHeight=11.69`；
  - `landscape=True`；
  - `scale=1.5`；
  - CDP 返回无 `data` → 抛 `RuntimeError`；
  - CDP 抛异常 → `logger.warning` 被调用且 re-raise。
- **`_action_save_as_pdf` 工具层测试**（用 `_make_mock_browser`，仅 `browser.print_to_pdf = AsyncMock(return_value=b"%PDF-...")`，不起真实 session）：
  - 成功路径：文件写入 + `extracted_content` 含字节数与 `paper=...`；
  - 写盘 `OSError`（如只读目录）→ `ActionResult(error=f"Failed to save PDF to ...")`；
  - CDP 失败（`print_to_pdf` 抛异常）→ `ActionResult(error=f"Failed to generate PDF: ...")`；
  - 默认参只传 `{"path": ...}` 仍合法；
  - 父目录自动创建：`path = tmp_path / "sub" / "out.pdf"`，执行后 `sub/` 存在且文件写入。
- 文件 IO 用 pytest `tmp_path`；异步测试逐个标 `@pytest.mark.asyncio`（项目用 `pytest-asyncio>=0.24.0`，无全局 `asyncio_mode`）。

### 1.6 阶段一文件清单

| 文件 | 改动 | 锚点 |
|---|---|---|
| `src/tree_walker/browser/session.py` | 新增 `print_to_pdf` 方法 | 紧邻 `take_screenshot` `:712-764` 之后，**4 空格** |
| `src/tree_walker/tools/models.py` | `SaveAsPdfParams` 扩展 + `ACTION_DEFINITIONS` description | `:125-127`、`:225`，**4 空格** |
| `src/tree_walker/tools/actions.py` | 重写 `_action_save_as_pdf` | `:854-864`，**4 空格** |
| `tests/test_save_as_pdf.py` | 新建 | **TAB** 缩进 |
| `docs/Tools技术细节/04_动作清单与CDP映射.md` | 更新 4.14 节（参数表 + 行号修正为 `actions.py:854`） | `:705-739` |

### 1.7 阶段一测试计划

```powershell
uv run python -m pytest tests/test_save_as_pdf.py -x -v
uv run python -m pytest tests/ -x -v
uv run python -m pytest tests/test_save_as_pdf.py --cov=tree_walker.tools.actions --cov=tree_walker.browser.session --cov-report=term-missing
```

---

## 阶段二（可选，独立，超出 browser-use 范围）

browser-use 自己也没暴露以下 CDP 能力（见 `cdp_use/cdp/page/commands.py:255-306` 的 `PrintToPDFParameters`），按需再开，不在阶段一交付：

- **margins**：`marginTop / marginBottom / marginLeft / marginRight`（英寸）。business 文档要留装订/页边距时需要。
- **header/footer**：`displayHeaderFooter=True` + `headerTemplate` / `footerTemplate`（支持页码 `<span class="pageNumber"></span>` / 总页数 `<span class="totalPages"></span>` / 日期 URL 等模板变量）。要页码、running header 时需要。
- **page_ranges**：`pageRanges`（如 `"1-3, 5"`），只导出指定页。
- **流式输出**：`transferMode="ReturnAsStream"`，大 PDF 用 `IO.read` 分块读，避免 base64 内存 1.33× 膨胀（当前阶段一仍走 base64 一次性解码）。
- **无障碍/书签**：`generateDocumentOutline`（PDF 书签大纲）、`generateTaggedPDF`（无障碍标签 PDF）。

> 若开 margins/header-footer，`print_to_pdf` 签名与 `SaveAsPdfParams` 都要相应扩展；header/footer 模板属于字符串注入，需在 docstring 里写清可用模板变量，避免 LLM 乱填。

---

## 风险与回归点

| 风险 | 影响 | 缓解 |
|---|---|---|
| `preferCSSPageSize=True` 硬编码 | 带 `@page` CSS 的页面会覆盖所选纸张尺寸 | 与 browser-use 一致；阶段二再开成参数（默认仍 True） |
| `extra="forbid"` + 新增字段 | 旧调用方/旧测试只传 `path` | 新字段全部带 default，向后兼容；已列入测试用例 |
| 直穿 CDP 改走 session 封装 | 行为不变，调用栈多一层 | 单测同时覆盖 `print_to_pdf`（session 层）与 `_action_save_as_pdf`（tool 层） |
| 不加超时 | 超大页面 `printToPDF` 可能挂起 | 对齐 screenshot 现状；阶段二可加 `asyncio.wait_for` |
| `paper_format` 改 `Literal` | LLM 传 `"A4"` 大写或拼写错会被校验拒 | `print_to_pdf` 内部仍 `.lower()` 容错；Literal 仅约束 schema 层，`StepPipeline._validate_action_params` 的 ValidationError 会触发 LLM 重试 |

---

## 验证方法

1. **单测全绿 + 覆盖率 ≥85%**（命令见 1.7）。
2. **手动冒烟**（需真实浏览器 ws，可用 `examples/` 下任一连浏览器的脚本改造）：
   ```python
   result = await tools.execute(
       "save_as_pdf",
       {"path": "out/report.pdf", "paper_format": "a4", "landscape": True},
       browser, browser_state,
   )
   ```
   确认：生成 A4 横向 PDF；`out/` 父目录被自动创建；`result.extracted_content` 含 `paper=a4`、`landscape`、字节数。
3. **回归对照**：`v0.3.0..master` 范围内 save_as_pdf 无既有行为被破坏（本工具此前零测试，阶段一补测后即建立基线）。

---

## 验收 checklist（阶段一）

- [ ] `BrowserSession.print_to_pdf` 存在，参数化 + 错误处理（`RuntimeError` on no data、`logger.warning` + re-raise）对齐 `take_screenshot`
- [ ] `SaveAsPdfParams` 含 `path / paper_format / landscape / print_background / scale`，`paper_format` 为 `Literal`，`scale` 有 `ge=0.1, le=2.0`
- [ ] `_action_save_as_pdf` 分级捕获错误（CDP 异常 / `OSError` 分别给明确 `ActionResult(error=...)`）、写盘前 `makedirs`、成功回显字节数 + meta
- [ ] `tests/test_save_as_pdf.py` 覆盖 CDP 组装（默认/a4/landscape/scale）+ action（成功/CDP 失败/写盘失败/默认参/父目录自建），全绿
- [ ] 全量回归 `uv run python -m pytest tests/ -x -v` 通过，覆盖率 >85%
- [ ] `docs/Tools技术细节/04_动作清单与CDP映射.md` 4.14 节同步更新（参数表 + 行号修正 + CDP 调用清单加 `printToPDF` 的完整 params）
