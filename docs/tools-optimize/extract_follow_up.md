# extract 工具阶段二 follow-up 方案（对齐 browser-use 完整能力）

> 本文承接 [`extract.md`](./extract.md) 的「阶段二（可选，独立，对齐 browser-use 完整能力）」。阶段一已落地（接通 LLM + 结构化输出 + 分级错误 + 测试），但那一节只给了 bullet 级提示，不可直接施工。本文把其中 7 项**阶段一未实现**的能力展开成**可直接实施**的设计：含文件锚点、before/after 代码骨架、配置/env 总表、测试点、风险表、验收 checklist，风格与 `extract.md` 一致。
> 参照标杆：browser-use 的 `extract_clean_markdown`（DOMSnapshot→HTMLSerializer→`markdownify`→清洗）、`chunk_markdown_by_structure` / `MarkdownChunk` / `start_from_char` / `already_collected` / `include_extracted_content_only_once`。
> 同族先例：[`screenshot.md`](./screenshot.md)、[`save_as_pdf.md`](./save_as_pdf.md)（阶段一均落地），以及本族 [`extract.md`](./extract.md) 阶段一。

---

## Context（为什么做这个改动）

`extract.md` 阶段一交付了「接通 LLM + 结构化输出 + 分级错误 + 测试」，但抽取的**输入源仍是 `document.body.innerText`**——纯文本、无结构、无链接/图片/表格语义。这让 extract 在以下 browser-use 主战场仍打不满：

- **长页面 / 表格列表页的结构化抓取**：innerText 把表格拍平成散乱文本，markdown 提取才能保住「表头—行」关系。
- **跨页 / 分页批量采集**（`extract.md` 场景 4）：依赖 markdown 分块 + `start_from_char` 分页 + `already_collected` 去重，阶段一全无。
- **大结果搬运**：innerText 截断后信息丢失；阶段二落盘 + 分页才能稳。

本文把这 7 项做成可施工方案。**两个关键决策**（已确认）：
1. **markdown HTML 来源用 CDP 序列化器**（`DOM.getDocument` 树重建 HTML），而非 `execute_js outerHTML`——为了穿透 shadow DOM + 同源 iframe；`execute_js outerHTML` 仅作降级后备。
2. **7 项全部做成可实施**，含 `goal→query` 重命名（破坏式）与 inner timeout（阶段一刻意不加，阶段二补）。

**预期结果**：extract 支持 markdown 结构化源 + 分页分块 + 跨页去重 + 大结果落盘 + 专用小模型 env 默认化 + 字段对齐 browser-use + 可选内层超时；覆盖率 >85%。

---

## 阶段一基线（阶段二的地基，已落地，行号对齐 master）

| 锚点 | 现状 |
|---|---|
| `src/tree_walker/tools/actions.py:774-800` `_action_extract` | 分级错误（`execute_js` 失败 `logger.warning`、LLM 异常 `ActionResult(error=...)`）、传 `output_schema=getattr(self,"_extraction_schema",None)`、读 `getattr(self,"_extract_llm",None)`；**源 = `browser.execute_js("document.body.innerText")`**（阶段二要换） |
| `src/tree_walker/llm/client.py:295-372` `LLMClient.extract` | 签名 `extract(prompt, content, *, max_content_chars=8000, output_schema=None) -> str`；结构化路径用 `extract_result` 工具 + `tool_choice={"type":"tool","name":"extract_result"}` + 解析 `tool_use.input`→`json.dumps`；非法 schema 降级 free-text；`_try_switch_to_fallback` 重试。`json`/`Any`/`RateLimitError`/`APIError` 已 import，**`asyncio` 未 import** |
| `src/tree_walker/tools/models.py:54-56` `ExtractParams` | 仅 `goal: str`，`model_config = ConfigDict(extra="forbid")`；`"extract"` 动作接线在 `models.py:295` |
| `src/tree_walker/config.py` | `AgentSettings` 已有 `extract_llm`/`extraction_schema`；`TruncationSettings` 有 `extract_page_max_chars=8000`/`extract_fallback_max_chars=2000`/`display_max_chars=500` 等 |
| `src/tree_walker/agent/agent.py:61-66` | 已接线 `_extract_llm`（专用或复用主 `self.llm`）+ `_extraction_schema` |
| `src/tree_walker/agent/views.py:8-37` `ActionResult` | 字段 `is_done/success/error/extracted_content/long_term_memory/judgement`；`__str__` 把 `extracted_content` 截断到 `display_max_chars=500`，`long_term_memory` **不截断但不进 `__str__`**（→ LLM 经 `previous_result→str(r)` **只看到 extracted_content 前 500 字**） |
| `load_settings()` `config.py:195-313` | `MESSAGE_COMPACTION_MODEL/_API_KEY/_BASE_URL/_MAX_TOKENS` → `LLMSettings` 是 `extract_llm` env 接线模板；默认 key=`ZHIPU_API_KEY`、默认 base_url=`https://open.bigmodel.cn/api/anthropic` |
| `src/tree_walker/tools/actions.py:1346-1378` `_action_write_file` | 文件输出姿势：全路径直写、**无沙箱**、`os.makedirs(os.path.dirname(path) or ".", exist_ok=True)`、`OSError→ActionResult(error=...)`、文本写 `encoding="utf-8", newline=""`（`save_as_pdf`/`screenshot` 同姿势） |
| `src/tree_walker/browser/dom.py:600` | 已调 `DOM.getDocument({'depth':-1,'pierce':True})`；`_collect_file_inputs`（dom.py:526-532）递归 `children`/`shadowRoots`/`contentDocument` → **该 CDP 树天然含 shadow DOM + 同源 iframe**；`_parse_attrs`（dom.py:66-72）是交错数组解析器 |
| `src/tree_walker/browser/session.py` | `self.client: CDPClient`（:983）+ `current_session_id`（:985）；`execute_js`（:2445）走 `Runtime.evaluate`，`session_id=self.current_session_id` |
| `pyproject.toml` | v0.5.0、python>=3.12、deps = cdp-use/anthropic/pydantic/python-dotenv/textual/click（+ optional vision=pillow）；**无 markdownify/bs4** |

---

## 工程约束（实施时遵守）

- Windows + PowerShell；包用 `uv`，跑脚本/测试用 `uv run python ...`。测试 `uv run python -m pytest tests/ -x -v`。
- **缩进按文件**：`src/**/*.py` = **4 空格**；`tests/test_extract*.py`/`tests/test_html_source.py` = **TAB**（对齐 `tests/test_screenshot.py`/`tests/test_save_as_pdf.py`）。下文代码片段按目标文件缩进给出。
- 改完跑相关单测 + 全量回归；覆盖率 >85%。不主动 `git commit`/`push`。
- `markdownify` 会**传递依赖**装上 `beautifulsoup4`（markdownify 把 bs4 列为硬依赖），无需显式声明 bs4；`uv sync` 一并解决。

---

## 与 browser-use 的关键差异（阶段二延续阶段一取舍）

1. **markdown 来源 = CDP `DOM.getDocument` 树重建 HTML**（穿透 shadow DOM + 同源 iframe；**跨源 iframe `contentDocument=None` 不可达**，显式标注 out-of-scope，与 browser-use 同立场），**不用** `execute_js outerHTML`；`execute_js outerHTML` 仅作降级后备。
2. **结构化输出仍用 Anthropic tool-use**（阶段一已立），不移植 provider-agnostic `output_format`。
3. **分页 = 单次单块 + agent 驱动 `start_from_char`**：一次 extract 只抽一个结构化分块，把「下一块偏移」作为提示回传，agent 决定是否继续。
4. **大结果落盘复用 `write_file` 全路径直写姿势**（无沙箱，对齐兄弟工具）。
5. **inner timeout 默认关闭**（`extract_call_timeout=0`），env 开启；开启时必须 `< action_timeout`（否则外层 `action_timeout` 先取消，inner 无意义）。
6. **`goal→query` 重命名是破坏式**，但因 `ExtractParams` 本就要扩展，重命名搭车一并做，放最后一步、独立 diff。
7. **分页提示必须落在 `extracted_content` 且前置**（LLM 经 `__str__` 只看前 500 字，后置提示会被截断掉）；`long_term_memory` 仅作日志/状态镜像，不依赖它传给 LLM。

---

## 实施分阶段（依赖图）

- **Stage 1（纯函数，独立、可单测，无 I/O）**：`pyproject.toml` 加 `markdownify` → 新建 `tools/extract_markdown.py` + `browser/html_source.py` + 对应 `tests/`。
- **Stage 2（config + client，独立于 Stage 1，可与 Stage 1 并行）**：`config.py` 加 `TruncationSettings` 新字段 + `load_settings` env 接线；`client.py` `extract` 加 `already_collected`/`call_timeout`/`import asyncio`/`prompt→query`。
- **Stage 3（集成，依赖 Stage 1+2）**：`session.py` 加 `get_page_html`；`models.py` 扩 `ExtractParams`；`actions.py` 重写 `_action_extract`；迁移 `tests/test_extract.py`。
- **Stage 4（破坏式重命名，最后，独立 diff）**：`goal→query`。

> inner timeout 可在 Stage 2 落地（默认关闭，零行为变更）；Stage 1 与 Stage 2 可并行。

---

## F1 — markdown 提取（CDP HTML 序列化器）

### F1.1 新模块 `src/tree_walker/browser/html_source.py`（4 空格）

纯函数，**按已取到的 CDP 树重建 HTML，不再发第二次 CDP**（`DOM.getDocument(depth=-1,pierce=True)` 的结果已含 shadow DOM + 同源 iframe）。

```python
"""CDP DOM.getDocument 树 → 干净 HTML 重建（供 extract 工具走 markdown 路径）。

纯函数：调用方把已取到的 CDP 节点 dict 传进来，本模块不再发 CDP。
"""
from __future__ import annotations
import html as _html

# 整棵子树丢弃的标签（噪声 / 非内容；iframe 由 contentDocument 递归带出，故元素本身也丢）
_SKIP_TAGS = frozenset({
    "script", "style", "template", "noscript", "svg", "canvas",
    "link", "meta", "head", "iframe",
})
# 自闭合标签（不发闭合标签）
_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})


def _parse_attrs(raw: list | None) -> dict[str, str]:
    """CDP attributes 交错数组 [name,val,...] → dict。复制自 dom.py:66-72，
    保持本模块零依赖（避免 import dom 的循环风险）。"""
    attrs: dict[str, str] = {}
    if not raw:
        return attrs
    for i in range(0, len(raw) - 1, 2):
        attrs[raw[i]] = raw[i + 1][:200]
    return attrs


def node_to_html(node: dict, *, extract_links: bool = True, extract_images: bool = True) -> str:
    """递归 children/shadowRoots/contentDocument 重建干净 HTML。

    nodeType: 3=文本(转义) / 1=元素(过滤 _SKIP_TAGS、门控 a.href/img.src) / 其余丢弃。
    extract_links=False → <a> 去掉 href；extract_images=False → <img> 去掉 src。
    返回 '' 表示空/被跳过的子树。
    """
    ...


def document_body_to_html(
    root: dict | None, *, extract_links: bool = True, extract_images: bool = True
) -> str:
    """从 DOM.getDocument 根定位 <body>（缺失则从 <html>）返回其 HTML；root 为空返回 ''。"""
    ...
```

`node_to_html` 核心递归：文本节点 `html.escape(nodeValue)`（空白也保留被 markdownify 处理）；元素节点按 `_SKIP_TAGS` 过滤、按 `_VOID_TAGS` 决定是否闭合；递归 `children` → `shadowRoots` → `contentDocument`（三者顺序与 dom.py:526-532 一致）。

### F1.2 `BrowserSession.get_page_html`（`session.py` `execute_js` 附近，约 :2459 后，4 空格）

```python
async def get_page_html(self, *, extract_links: bool = True, extract_images: bool = True) -> str:
    """单次 CDP DOM.getDocument(depth=-1, pierce=True) 取当前页 body 的干净 HTML。

    失败返回 ''（调用方降级到 execute_js outerHTML）。返回 '' 即等价空页。
    """
    try:
        doc = await self.client.send.DOM.getDocument(
            {"depth": -1, "pierce": True}, session_id=self.current_session_id,
        )
        from tree_walker.browser.html_source import document_body_to_html
        return document_body_to_html(
            (doc or {}).get("root", {}),
            extract_links=extract_links, extract_images=extract_images,
        )
    except Exception as e:
        logger.warning("get_page_html: DOM.getDocument failed: %s", e)
        return ""
```

### F1.3 `extract_clean_markdown`（`src/tree_walker/tools/extract_markdown.py`，4 空格）

```python
import re
from markdownify import markdownify as _md

_NOISE = ["script", "style", "nav", "footer", "header", "noscript", "template", "form"]
_WS_RE = re.compile(r"\n{3,}")


def extract_clean_markdown(html: str, *, extract_links: bool = True, extract_images: bool = True) -> str:
    """HTML → 干净 markdown：先剥噪声，再 markdownify(ATX 标题)，再折叠多余空行。

    link/image 门控在 markdownify **之前**用正则去标签（比清洗 markdown 更便宜、更稳）。
    """
    if not html or not html.strip():
        return ""
    if not extract_links:
        html = re.sub(r"<a\b[^>]*>(.*?)</a>", r"\1", html, flags=re.I | re.S)
        html = re.sub(r"<a\b[^>]*/?>", "", html, flags=re.I)
    if not extract_images:
        html = re.sub(r"<img\b[^>]*>", "", html, flags=re.I)
    md = _md(html, heading_style="ATX", strip=_NOISE)
    return _WS_RE.sub("\n\n", md).strip()
```

> F1 的输出取代阶段一的 `document.body.innerText`，作为 `_action_extract` 的抽取源。`extract_links`/`extract_images` 由 `ExtractParams` 透传（见 F3 同模块的 ExtractParams 扩展）。

---

## F2 — 结构化分块 + 分页（`chunk_markdown_by_structure` + `MarkdownChunk` + `start_from_char`）

同模块 `src/tree_walker/tools/extract_markdown.py`（4 空格）：

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class MarkdownChunk:
    content: str      # 块内容（可能含合成的表头，故未必等于 end_index - start_index）
    start_index: int  # 在原始 markdown 中的偏移
    end_index: int    # exclusive


def chunk_markdown_by_structure(md: str, *, max_chars: int = 8000) -> list[MarkdownChunk]:
    """按结构分块，每块 ≤ max_chars：
    1) 按 markdown 标题（# 行）切 section；section 过大则按 \\n\\n 段落硬切；
       段落仍过大则按 max_chars 硬切。
    2) 贪心把相邻小 section 装进同一块，直到再加下一块会超 max_chars。
    3) 表头延续：块跨越表格边界时，下一块顶部重复上一块最近的 `| header |\\n|---|---|`
       （合成的表头不计入 start_index，故 content 可能略长于 end_index-start_index）。
    返回 start_index/end_index 单调递增、连续、覆盖全 md；md ≤ max_chars 时单块。
    """
    ...
```

**分页接线**（在 `_action_extract` 里）：
- `start_from_char` 由 agent 传入（默认 0）。
- `chunk_markdown_by_structure(md, max_chars=tr.extract_chunk_max_chars)` 得到全部块；找到 `[start_index, end_index)` 含 `start_from_char` 的块；块内若 `start_from_char` 落在块中间，再做子切片 `chunk.content[local_offset:]`。
- **一次 extract 只抽这一块**，喂给 `llm.extract`。
- 分页进度提示见 F4（前置进 `extracted_content`）。

---

## F3 — `already_collected` 去重

`ExtractParams` 加字段（见「ExtractParams 扩展」）。透传链：`_action_extract` → `llm.extract(..., already_collected=...)`；client 把它拼进 user message 末尾：

```python
collected_block = ""
if already_collected:
    joined = "\n".join(f"- {c}" for c in already_collected[:200])  # 上限 200 防 prompt 膨胀
    collected_block = (
        "\n\nItems already collected (DO NOT re-extract these, skip exact duplicates):\n"
        + joined
    )
user_msg = f"{query}{collected_block}\n\n---\n{content}"
```

`_try_switch_to_fallback` 重试时 `already_collected` 原样回传。

---

## F4 — 结果分级落盘 + 分页进度可见性 + `include_extracted_content_only_once` 讨论

### F4.1 落盘（复用 `_action_write_file` 直写姿势）

`result ≥ extract_save_threshold`（默认 10000）→ 写文件（**按大小触发，与分页解耦**——避免每个分页块各写一个文件、并让 free-text 提示前置的代码路径保持 live）：

```python
saved_to: str | None = None
if len(result) >= tr.extract_save_threshold or next_offset is not None:
    try:
        os.makedirs(tr.extract_output_dir, exist_ok=True)
        ext = "json" if schema else "md"
        fpath = os.path.join(tr.extract_output_dir, f"extract_{int(time.time()*1000)}.{ext}")
        with open(fpath, "w", encoding="utf-8", newline="") as f:  # 对齐 write_file:1366
            f.write(result)
        saved_to = fpath
    except OSError as e:
        logger.warning("extract: save to file failed: %s", e)
        # 不崩：全文留在 extracted_content（受 __str__ 500 字截断）
```

### F4.2 分页进度可见性（关键约束：LLM 只看 `extracted_content` 前 500 字）

```python
remaining = sum(c.end_index - c.start_index for c in chunks[target_idx + 1:])
next_offset = chunks[target_idx + 1].start_index if target_idx + 1 < len(chunks) else None
hint = (
    f"[chunk {target_idx+1}/{len(chunks)}; ~{remaining} chars remain; "
    f"call extract again with start_from_char={next_offset} to continue]"
    if next_offset is not None else ""
)

# hint 必须在 500 字窗口内可见
if schema is not None:
    # 结构化：JSON 原样进文件/extracted_content，hint 分离
    if saved_to:
        visible = f"Extraction ({len(result)} chars) saved to {saved_to}. Preview: {result[:200]}...\n{hint}".strip()
    else:
        visible = result  # 纯 JSON，hint 走 long_term_memory
else:
    # free-text：hint 前置（result 可能 >500，后置会被截掉）
    visible = (hint + "\n" + result) if hint else result

mem = " | ".join(p for p in [f"extract result saved: {saved_to}" if saved_to else "", hint] if p)
return ActionResult(extracted_content=visible, long_term_memory=mem or None)
```

> **结构化结果不污染 schema**：`schema is not None` 时，JSON 原样进文件/`extracted_content`；未落盘时 hint 只进 `long_term_memory`（agent 不解析它）；落盘时 agent 读文件取纯 JSON，preview 只是给人看。free-text 时 hint 前置，确保在 500 字窗口内可见。

### F4.3 `include_extracted_content_only_once` 讨论

TreeWalker `ActionResult.__str__` 已把 `extracted_content` 截到 500 字，且落盘后回显只是「saved to path」短串 → 「只回显一次 / 不胀上下文」**大体已由「落盘 + 500 字截断」达成**。browser-use 式「N 步后从历史彻底移除」需改 `agent/step.py` 的 `previous_result` 注入逻辑，**本阶段不做**（风险大、收益边际），标注为后续可选项。

---

## F5 — 专用小模型 env 默认化（`load_settings`）

在 `load_settings()` 内 `AgentSettings(...)` 构造之后（镜像 `MESSAGE_COMPACTION_*`，约 `config.py:257`，4 空格）：

```python
extract_model = os.environ.get("AGENT_EXTRACT_MODEL")
if extract_model:
    agent.extract_llm = LLMSettings(
        model=extract_model,
        api_key=os.environ.get("AGENT_EXTRACT_API_KEY", api_key),  # 默认 ZHIPU_API_KEY
        base_url=os.environ.get(
            "AGENT_EXTRACT_BASE_URL", "https://open.bigmodel.cn/api/anthropic",
        ),
        max_tokens=int(os.environ.get("AGENT_EXTRACT_MAX_TOKENS", "4096")),
    )
```

未设 env → `agent.extract_llm` 保持 `None` → `Agent.__init__`（agent.py:61-66）复用主 `llm`，**阶段一行为不变**。

---

## F6 — `goal → query` 重命名（破坏式，Stage 4 最后做）

| 位置 | 改动 |
|---|---|
| `tools/models.py:56` | `ExtractParams.goal` → `query` |
| `tools/actions.py` | `params["goal"]` → `params["query"]`；`llm.extract(goal, ...)` → `llm.extract(query, ...)` |
| `llm/client.py` | `extract` 形参 `prompt` → `query`（含 docstring） |
| `tools/models.py:295` | `"extract"` 动作描述若含 `goal` 同步改 |
| `tests/test_extract.py` | 所有 `{"goal": ...}` → `{"query": ...}` |

schema 经 `ExtractParams.model_json_schema()` 自动传播给 LLM，无需手改 LLM 工具定义。

**迁移说明**（写进 changelog）：纯 LLM-facing、**无持久化状态**要迁移（TreeWalker 不重放旧 transcript）；含 `goal` 的自定义 prompt / 示例改用 `query`；`extraction_schema` 正交、不受影响。

---

## F7 — inner timeout

`LLMClient.extract` 新增 `call_timeout: float | None = None` 形参，**仅包 `messages.create`**（不包解析）：

```python
try:
    if call_timeout:
        response = await asyncio.wait_for(
            self.client.messages.create(...),
            timeout=call_timeout,
        )
    else:
        response = await self.client.messages.create(...)
except asyncio.TimeoutError:
    raise  # 交给 _action_extract 映射成分级错误
except (RateLimitError, APIError) as e:
    if self._try_switch_to_fallback(e):
        return await self.extract(query, content, max_content_chars=...,
                                  output_schema=..., already_collected=..., call_timeout=call_timeout)
    raise
```

`client.py` 顶部加 `import asyncio`。`_action_extract` 调用传 `call_timeout=self._truncation.extract_call_timeout or None`（`0.0 → None` 即关闭，保阶段一行为），并新增 `except TimeoutError` 映射 `ActionResult(error=f"Extract timed out: {e}")`。

**理由**：阶段一刻意不加（对齐 screenshot/save_as_pdf 依赖 `action_timeout=30s`）；阶段二分块可能多块，单次调用虽仍应 <30s，但 120s（对齐 browser-use）给抽取类大调用更宽裕。**开启时必须同时调大 `AGENT_ACTION_TIMEOUT`**，文档给 env 示例：`AGENT_EXTRACT_CALL_TIMEOUT=120` 配 `AGENT_ACTION_TIMEOUT=150`。

---

## ExtractParams 扩展（F2/F3 汇总，`models.py:54-56`，4 空格）

```python
class ExtractParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        description="What information to extract from the current page. Be specific: name the fields/items and any filtering criteria."
    )
    extract_links: bool = Field(
        default=True,
        description="If True, preserve <a href> URLs in the source markdown. Set False for text-only extraction.",
    )
    extract_images: bool = Field(
        default=True,
        description="If True, preserve <img src> URLs in the source markdown.",
    )
    start_from_char: int = Field(
        default=0, ge=0,
        description="Character offset to resume extraction from (paginate large pages). Default 0. Use the offset reported by a previous truncated extract call.",
    )
    already_collected: list[str] | None = Field(
        default=None,
        description="Items already extracted (dedupe). Pass prior results; the model will skip exact duplicates. Optional.",
    )

    @field_validator("already_collected")
    @classmethod
    def _drop_empty_items(cls, v):
        if v is not None:
            v = [item for item in v if item and item.strip()]
        return v or None
```

> `from pydantic import ..., field_validator` 补 import。`extra="forbid"` 保留——agent 不能塞未知键（含「落盘目录」等敏感参数，见风险表）。

---

## `_action_extract` 重写总览（`actions.py:774-800`，4 空格，整合 F1–F4/F7）

```python
async def _action_extract(self, params: dict, browser: BrowserSession) -> ActionResult:
    query = params["query"]
    extract_links = params.get("extract_links", True)
    extract_images = params.get("extract_images", True)
    start_from_char = params.get("start_from_char", 0)
    already_collected = params.get("already_collected")
    tr = self._truncation
    schema = getattr(self, "_extraction_schema", None)

    # 1) 源：CDP HTML → markdown（取代 document.body.innerText）
    html_text = await browser.get_page_html(extract_links=extract_links, extract_images=extract_images)
    if not html_text:  # 降级到 execute_js outerHTML
        try:
            html_text = await browser.execute_js("document.documentElement.outerHTML") or ""
        except Exception as e:
            logger.warning("extract: HTML source failed: %s", e)
            html_text = ""
    if not html_text:
        return ActionResult(extracted_content="(empty page)")

    md = extract_clean_markdown(html_text, extract_links=extract_links, extract_images=extract_images)
    if not md.strip():
        return ActionResult(extracted_content="(empty page)")

    llm = getattr(self, "_extract_llm", None)
    if llm is None:
        snippet = md[start_from_char:start_from_char + tr.extract_fallback_max_chars]
        return ActionResult(extracted_content=snippet or "(no content at offset)")

    # 2) 分页：取 start_from_char 所在的单块
    chunks = chunk_markdown_by_structure(md, max_chars=tr.extract_chunk_max_chars)
    target_idx = 0
    for i, c in enumerate(chunks):
        if c.start_index <= start_from_char <= max(c.end_index - 1, c.start_index):
            target_idx = i
            break
    local_offset = max(0, start_from_char - chunks[target_idx].start_index)
    chunk_content = chunks[target_idx].content[local_offset:]

    # 3) 抽取（含去重 + 内层超时）
    try:
        result = await llm.extract(
            query, chunk_content,
            max_content_chars=tr.extract_chunk_max_chars,
            output_schema=schema,
            already_collected=already_collected,
            call_timeout=tr.extract_call_timeout or None,
        )
    except TimeoutError as e:
        logger.warning("extract: LLM call timed out: %s", e)
        return ActionResult(error=f"Extract timed out: {e}")
    except Exception as e:
        logger.warning("extract: LLM call failed: %s", e)
        return ActionResult(error=f"Extract failed: {e}")

    # 4) 分页进度 + 分级落盘（见 F4.2 / F4.1）
    next_offset = chunks[target_idx + 1].start_index if target_idx + 1 < len(chunks) else None
    remaining = sum(c.end_index - c.start_index for c in chunks[target_idx + 1:])
    hint = (f"[chunk {target_idx+1}/{len(chunks)}; ~{remaining} chars remain; "
            f"call extract again with start_from_char={next_offset} to continue]"
            if next_offset is not None else "")
    saved_to = None
    if len(result) >= tr.extract_save_threshold or next_offset is not None:
        try:
            os.makedirs(tr.extract_output_dir, exist_ok=True)
            ext = "json" if schema else "md"
            fpath = os.path.join(tr.extract_output_dir, f"extract_{int(time.time()*1000)}.{ext}")
            with open(fpath, "w", encoding="utf-8", newline="") as f:
                f.write(result)
            saved_to = fpath
        except OSError as e:
            logger.warning("extract: save to file failed: %s", e)

    if schema is not None:
        visible = (f"Extraction ({len(result)} chars) saved to {saved_to}. Preview: {result[:200]}...\n{hint}".strip()
                   if saved_to else result)
    else:
        visible = (hint + "\n" + result) if hint else result
    mem = " | ".join(p for p in [f"extract result saved: {saved_to}" if saved_to else "", hint] if p)
    return ActionResult(extracted_content=visible, long_term_memory=mem or None)
```

> `actions.py` 顶部补 `import time`（若未 import）。`os`/`logger` 已在用。

---

## `LLMClient.extract` 改动总览（`client.py:295-372`，4 空格）

签名变为：

```python
async def extract(
    self,
    query: str,                       # 原 prompt
    content: str,
    *,
    max_content_chars: int = 8000,
    output_schema: dict[str, Any] | None = None,
    already_collected: list[str] | None = None,   # F3：拼进 user message
    call_timeout: float | None = None,            # F7：仅包 messages.create
) -> str:
```

- schema 校验降级（不变）、`_try_switch_to_fallback` 重试（**回传 `already_collected` + `call_timeout`**）。
- 结构化路径与 free-text 路径都加 `call_timeout` 的 `asyncio.wait_for` 包裹；`asyncio.TimeoutError` 原样上抛。
- `import asyncio` 顶部新增。

---

## `config.py` 改动总览（4 空格）

### `TruncationSettings`（config.py:41-50）加 4 字段

```python
@dataclass
class TruncationSettings:
    extract_page_max_chars: int = 8000
    extract_fallback_max_chars: int = 2000
    extract_chunk_max_chars: int = 8000         # 新：单块预算（默认=extract_page_max_chars，单块行为不变）
    extract_save_threshold: int = 10000         # 新：结果 ≥ 此值落盘（对齐 browser-use）
    extract_output_dir: str = "extract_output"  # 新：落盘目录（启动期 env 配置，非 agent 可控）
    extract_call_timeout: float = 0.0           # 新：inner LLM 超时秒（0=关）
    read_file_max_chars: int = 5000
    eval_result_max_chars: int = 2000
    display_max_chars: int = 500
    dom_excerpt_max_chars: int = 2000
```

### `load_settings()` 补 env 接线

`TruncationSettings(...)` 构造处（config.py:235-242）补：

```python
extract_chunk_max_chars=int(os.environ.get("AGENT_TRUNCATE_EXTRACT_CHUNK", "8000")),
extract_save_threshold=int(os.environ.get("AGENT_EXTRACT_SAVE_THRESHOLD", "10000")),
extract_output_dir=os.environ.get("AGENT_EXTRACT_OUTPUT_DIR", "extract_output"),
extract_call_timeout=float(os.environ.get("AGENT_EXTRACT_CALL_TIMEOUT", "0")),
```

+ F5 的 `AGENT_EXTRACT_MODEL/_API_KEY/_BASE_URL/_MAX_TOKENS` 块（见 F5）。

---

## 配置总表（TruncationSettings 新字段 + 新 env）

| 字段 | 默认 | env | 说明 |
|---|---|---|---|
| `extract_chunk_max_chars` | 8000 | `AGENT_TRUNCATE_EXTRACT_CHUNK` | 单块预算（默认=`extract_page_max_chars`，单块行为不变） |
| `extract_save_threshold` | 10000 | `AGENT_EXTRACT_SAVE_THRESHOLD` | 结果 ≥ 此值落盘 |
| `extract_output_dir` | `"extract_output"` | `AGENT_EXTRACT_OUTPUT_DIR` | 落盘目录（启动期 env 配置，非 agent 可控） |
| `extract_call_timeout` | 0.0 | `AGENT_EXTRACT_CALL_TIMEOUT` | inner LLM 超时秒（0=关） |
| —（`extract_llm`） | None | `AGENT_EXTRACT_MODEL` + `_API_KEY`/`_BASE_URL`/`_MAX_TOKENS` | 专用小模型；未设则复用主 llm |

---

## 文件清单

| 文件 | 改动 | 缩进 |
|---|---|---|
| `pyproject.toml` | 加 `markdownify>=0.13.1`（传递依赖 bs4） | — |
| `src/tree_walker/tools/extract_markdown.py` | **新建** `extract_clean_markdown` / `chunk_markdown_by_structure` / `MarkdownChunk` | 4 空格 |
| `src/tree_walker/browser/html_source.py` | **新建** `node_to_html` / `document_body_to_html` / `_parse_attrs` | 4 空格 |
| `src/tree_walker/browser/session.py` | 加 `get_page_html`（`execute_js` 附近） | 4 空格 |
| `src/tree_walker/llm/client.py` | `extract` 加 `already_collected`/`call_timeout`/`import asyncio`/`prompt→query` | 4 空格 |
| `src/tree_walker/tools/models.py` | `ExtractParams` 加 5 字段 + `goal→query` | 4 空格 |
| `src/tree_walker/tools/actions.py` | 重写 `_action_extract` + `import time` | 4 空格 |
| `src/tree_walker/config.py` | `TruncationSettings` 4 字段 + `load_settings` env 接线 | 4 空格 |
| `tests/test_extract_markdown.py` | **新建** 纯函数单测 | TAB |
| `tests/test_html_source.py` | **新建** CDP 树→HTML 单测 | TAB |
| `tests/test_extract.py` | 迁移 mock 工厂（加 `get_page_html`）+ `goal→query` + 新场景 | TAB |
| `docs/Tools技术细节/04_动作清单与CDP映射.md` | 4.6 节同步（markdown 源 / 分页 / 落盘 / 新参数） | — |

---

## 测试计划

```powershell
uv run python -m pytest tests/test_extract.py tests/test_extract_markdown.py tests/test_html_source.py -x -v
uv run python -m pytest tests/ -x -v
uv run python -m pytest tests/test_extract.py tests/test_extract_markdown.py tests/test_html_source.py --cov=tree_walker.tools.actions --cov=tree_walker.tools.extract_markdown --cov=tree_walker.browser.html_source --cov=tree_walker.llm.client --cov-report=term-missing
```

**mock 工厂迁移**：`_make_mock_browser` 加 `browser.get_page_html = AsyncMock(return_value=<html>)`，`browser.execute_js` 仍作降级路径。

**关键用例**：
1. **markdown 源**：`get_page_html` 返 `<p>Hello <a href="x">link</a></p>` → `llm.extract` 的 content 含 markdown 而非 innerText。
2. **`extract_links/extract_images=False`**：去链接/图后 content 无 URL。
3. **分页**：大 md → `extracted_content` 前置含 `start_from_char=` 提示；结构化结果 JSON 纯净、hint 进 `long_term_memory`。
4. **`start_from_char` 续抽**：两次调用（第二次用首次回传的 offset）抽到不同块。
5. **`already_collected`**：进 client 的 user message（client 层断言）。
6. **落盘 + `OSError` 降级**：`extract_save_threshold` 调小触发；`tmp_path` monkeypatch `extract_output_dir`；patch `open` 抛 `OSError` 不崩。
7. **inner timeout**：`create` 慢（`asyncio.sleep(1)`）+ 小 `call_timeout` → `asyncio.TimeoutError` → `ActionResult(error=...)`。
8. **CDP 失败降级**：`get_page_html` 返 `''` 但 `execute_js` 返 HTML → 继续抽取。
9. **env 接线**：`AGENT_EXTRACT_MODEL=glm-flash` set → `settings.agent.extract_llm.model == "glm-flash"`；未设 → `is None`。
10. **纯函数**：表头延续、标题切分、偏移连续覆盖全 md；`node_to_html` 跳过 script/style、走 `shadowRoots`/`contentDocument`、文本转义。

---

## 风险与回归点

| 风险 | 影响 | 缓解 |
|---|---|---|
| markdownify 表格/嵌套保真度 | 抽取质量略降 | 先剥噪声再转换；单测覆盖表/列表/链接常见形；落盘与原 JSON 一致 |
| CDP 序列化多一次 `DOM.getDocument` round-trip | 性能（几十 ms） | 接受；后续可缓存到 session |
| `extract_call_timeout ≥ action_timeout`（30s） | inner 永不触发（外层先取消） | 默认 0 关；文档要求开启时 `< action_timeout` 并配套调大 |
| `query` 重命名打破含 `goal` 的 prompt/示例 | 一次性混淆 | schema 自动传播；改唯一示例；changelog 标注 |
| 落盘无沙箱（`extract_output_dir`） | env 指向敏感目录 | env 仅启动期配置、`extra="forbid"` 不让 agent 传；对齐 write_file/save_as_pdf 姿势 |
| 结构化结果 + 分页提示污染 JSON | agent JSON 解析失败 | JSON 原样进文件/extracted_content，hint 分行/前置分离；落盘后 agent 读文件取纯 JSON |
| `_parse_attrs` 复制 vs import dom | 循环 import 风险 | 在 html_source 内复制 5 行，保持模块零依赖 |
| 分页提示被 500 字截断 | agent 看不到「继续」指令 | hint 前置（free-text）或落盘后短回显（结构化），保证在窗口内 |

---

## 验收 checklist（阶段二）

- [ ] `extract_clean_markdown` / `chunk_markdown_by_structure` / `MarkdownChunk` + `node_to_html` / `document_body_to_html` 落地，纯函数单测全绿
- [ ] `BrowserSession.get_page_html` 走 CDP `DOM.getDocument`，失败降级 `execute_js outerHTML`
- [ ] `ExtractParams` 含 `query` / `extract_links` / `extract_images` / `start_from_char` / `already_collected`（`extra="forbid"`，校验起效）
- [ ] `_action_extract` 用 markdown 源、单块分页、去重透传、超时分级、大结果/分页结果落盘、分页进度前置可见
- [ ] `LLMClient.extract` 加 `already_collected` / `call_timeout`（`asyncio.wait_for`），`prompt→query`
- [ ] `TruncationSettings` 4 新字段 + `load_settings` 接线（含 `AGENT_EXTRACT_MODEL` 等）
- [ ] `pyproject.toml` 加 `markdownify`，`uv sync` 通过
- [ ] 全量回归 `uv run python -m pytest tests/ -x -v` 通过，覆盖率 >85%
- [ ] `docs/Tools技术细节/04_动作清单与CDP映射.md` 4.6 节同步
