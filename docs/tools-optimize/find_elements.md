# find_elements 工具优化方案（分阶段）

> 参照 browser-use（`browser_use/tools/service.py:1297-1330` find_elements 动作体、`:257-298` `_FIND_ELEMENTS_JS_BODY`、`:321-334` `_build_find_elements_js`、`:363-401` `_format_find_results`、`browser_use/tools/views.py:39-46` `FindElementsAction`）完善本项目 find_elements 工具。
> 相关现状文档：`docs/Tools技术细节/04_动作清单与CDP映射.md` 的 4.7 节；参考标杆：`browser-use/docs/Tools技术细节/06-动作详解-数据处理与文件.md` 的 18. find_elements 节。
> 同族先例：`docs/tools-optimize/search_page.md`（commit `e8e2aed` / issue #44 / PR #45）、`docs/tools-optimize/extract.md`（commit `a8148a4` / issue #42 / PR #43）、`docs/tools-optimize/screenshot.md`（commit `b1fc97e` / issue #38 / PR #39）、`docs/tools-optimize/save_as_pdf.md`（commit `21153d8` / issue #40 / PR #41）——本方案在结构、错误分级、封装思路上全面对齐四者阶段一。

---

## 适用场景（什么时候会用到 find_elements）

`find_elements` 在 agent 工具集里的定位是 **「CSS 选择器元素查询：一次 `Runtime.evaluate`、零 LLM 成本、瞬时返回所有匹配元素的 tag / text / attributes / children_count」**。它和其它「读 / 找」类工具分工明确：

| 工具 | 职责 | 与 find_elements 的区别 |
|---|---|---|
| `find_text` | 定位某段文字并 **滚动 + 高亮** 首条匹配到视口 | 基于文字内容找一条并驱动视口；不接受 CSS 选择器、不返回元素清单 |
| `search_page` | grep 式全文检索 → 带上下文 / 元素路径 / 总数的匹配清单 | 基于「文字」检索；find_elements 基于「CSS 选择器」检索结构 |
| `get_state`（每步自动） | 给 agent 看 DOM 快照，决定点哪 | 面向「看 + 操作」，按 index 点；find_elements 是按 CSS 选择器探查结构 |
| `evaluate` | 跑任意 JS 拿原始返回值 | 通用逃生口，无选择器语义、无属性裁剪、无总数回显 |
| `extract` | 语义提炼页面内容 → 摘要或结构化 JSON | 走 LLM 理解；find_elements 是确定性结构查询，无理解、零 LLM 成本 |
| **`find_elements`** | **按 CSS 选择器查询元素 → 返回 tag / text / 指定 attributes / children_count + 总数** | **唯一一个「按选择器探查结构 + 批量取属性 + 带总数」的确定性工具」** |

典型场景：

1. **探查页面结构 / 计数**：「这页有几张 `<img>`？有几个 `.product`？」——一次返回 `total` + 逐条 tag / children_count，比 `get_state` 看 DOM 快照更聚焦。
2. **批量取链接 / 图片地址**：`selector='a.link'`, `attributes=['href']` 或 `selector='img'`, `attributes=['src']`，`src`/`href` 走 DOM 属性解析为**绝对 URL**（不是 `getAttribute` 的原始相对值）。
3. **抓结构化字段**：`selector='tr.product-row'`, `attributes=['data-id','class']`，配合后续 `click` / `extract` / `search_page` 做精确操作。
4. **核对元素是否存在**：提交后查 `.error-message` 是否出现，看 `total` 即知成败，比看整页 DOM 快。

**什么时候不需要它**：只要「滚动到某段文字并看见它」就够时用 `find_text`（带视觉高亮）；需要「按文字检索全文」时用 `search_page`；需要「理解语义 / 结构化抽取」时用 `extract`。`find_elements` 是为「按 CSS 选择器、确定性、批量取属性、带总数、零 LLM 成本」的结构查询存在的。

**可用性提示**：阶段一落地后上述场景 1–4 全部可用；iframe / shadow DOM 内的元素查询属阶段二（browser-use 同样不支持，见下文「与 browser-use 的关键差异」）。

---

## Context（为什么做这个改动）

当前 TreeWalker 的 `find_elements` 是一个**极简实现**（`actions.py:818-831`，4 空格），明显落后于刚做完阶段一优化的 `search_page` / `extract` / `screenshot` / `save_as_pdf`，也落后于参照标杆 browser-use：

```python
async def _action_find_elements(self, params: dict, browser: BrowserSession) -> ActionResult:
    selector = params["selector"]
    js_code = (
        f"Array.from(document.querySelectorAll({repr(selector)}))"
        ".map((e, i) => ({"
        "index: i, tag: e.tagName, text: (e.textContent || '').substring(0, 100).trim(),"
        "href: e.href || '', visible: e.offsetParent !== null"
        "}))"
    )
    try:
        result = await browser.execute_js(js_code)
        return ActionResult(extracted_content=str(result))
    except Exception as e:
        return ActionResult(error=str(e))
```

主要问题：

1. **参数几乎为零**：`FindElementsParams`（`models.py:94-96`）只有 `selector: str`，无 `attributes` / `max_results` / `include_text`——既不能取指定属性，也不能限流。
2. **返回值是 `str(list)` 裸 dump**：`ActionResult(extracted_content=str(result))`，把 Python 列表的 `repr` 直接灌进 `extracted_content`，对 LLM 极不友好（单引号 / `True`/`False` 等 Python 字面量混入）。
3. **无匹配总数 / 无分页**：`querySelectorAll` 命中几千个节点时，全量 dump 会撑爆上下文；没有 `max_results` 封顶，也没有 `total` 回显「真实命中数」。
4. **`href` 写死、不能取其它属性**：硬编码取 `href`，`src` / `class` / `data-*` 等一律取不到；且非链接元素的 `href` 是 `undefined` → 被写成 `''`，语义混乱。
5. **可见性检测过浅**：`e.offsetParent !== null` 把 `position: fixed` 元素误判为不可见，且忽略祖先链上的 `display:none` / `visibility:hidden` / `opacity:0`——结论不可信，不如不报（browser-use 就不报 `visible`，改报 `children_count`）。
6. **注入方式不安全且不一致**：`repr(selector)`（Python repr）拼成 JS 字面量，而兄弟 `search_page` 已统一用 `json.dumps` 注入（`session.py:161-168` `_build_search_page_js`）——`repr` 不是项目约定，对含特殊字符的选择器不够稳。
7. **错误不分级**：JS 异常 / 非法选择器直接 `error=str(e)`（`actions.py:830-831`），丢失「miss = 软回显 / CDP 失败 = 硬错误」的兄弟约定（`_action_find_text:833-858`）；零命中返回字面量 `"[]"` 而不是「No elements found」软回显。
8. **从不设 `long_term_memory`**：与 `find_text` / `search_page` 的回显约定不一致（跨会话拿不到「找到 N 个元素」的摘要）。
9. **无 session 层封装**：在 action 里内联 JS 字符串直连 `execute_js`，而兄弟工具已统一收进 `BrowserSession.<method>`（`find_text` / `search_page` / `take_screenshot` / `print_to_pdf`）。
10. **无格式化 helper**：不像 `_format_search_results`（`actions.py:64-86`）把结果渲染成 LLM 友好的多行文本。
11. **零测试**：`tests/` 下无 `test_find_elements.py`（兄弟工具都有：`test_find_text.py` / `test_search_page.py` / `test_screenshot.py` 等）。
12. **描述对 LLM 无指引**：`ACTION_DEFINITIONS["find_elements"]` 描述仅为 `"Find elements on the page using a CSS selector"`，LLM 不知道它能取属性、能限流、会回显总数。

**参照标杆 browser-use 的做法**（`service.py:1297-1330` 动作体、`:257-298` JS body、`:321-334` builder、`:363-401` 格式化、`views.py:39-46` 参数）：单次 `Runtime.evaluate`（`returnByValue=True, awaitPromise=True`）执行一个 `querySelectorAll` IIFE——两层 try/catch（内层捕非法选择器 `DOMException`、外层兜底），逐元素取 `{index, tag, text?, attrs?, children_count}`；`src`/`href` 走 DOM 属性（`el.href`）拿**绝对 URL**、其余走 `getAttribute`，null 属性跳过；text 截 300、attr 值截 500；`max_results` 封顶但 `total` 始终回真实命中数 + `showing`；用户值用 `json.dumps` 注入成 `var` 声明（body 引用 var，绝不 f-string 拼用户串）；miss 不是 error（返回 `extracted_content`）；不滚动 / 不取坐标（那是 `find_text` / 点击类工具的职责）。

**预期结果**：把 `find_elements` 升级到 browser-use 同级的「CSS 选择器元素查询」（`querySelectorAll` + 可选 attributes + `src`/`href` 绝对 URL + children_count + 总数 + 分页），同时按本项目约定**封装进 `BrowserSession.find_elements`**、**错误分级捕获**、**补齐单测覆盖率 ≥85%**，并同步修正现状文档。

---

## 工程约束（实施时务必遵守）

- Windows + PowerShell；包用 `uv`，跑脚本 / 测试用 `uv run python ...`。测试命令 `uv run python -m pytest tests/ -x -v`。
- **缩进按文件**（已复核）：`actions.py` / `models.py` / `browser/session.py` = **4 空格**；`tests/test_find_elements.py` = **TAB**（对齐 `tests/test_find_text.py` / `tests/test_search_page.py`）。下文代码片段均按目标文件缩进给出。
- 改完跑相关单测 + 全量回归；覆盖率目标 >85%。
- 不主动 `git commit` / `git push`。
- `json` 在 `session.py` 已是模块级 import（`_build_search_page_js:162` 已用 `json.dumps`）；`logger`（`logging`）已存在；`Field` / `ConfigDict` 已在 `models.py` import（`ScreenshotParams:113-114` / `SearchPageParams:176-177` 已用同款）。新增代码无需新 import。

---

## 与 browser-use 的关键差异（有意为之，不照搬）

1. **封装进 `BrowserSession.find_elements`，而非在 action 里直连 CDP。** browser-use 在 `find_elements` action 内联 `cdp_client.send.Runtime.evaluate`。TreeWalker 按 `find_text:1942` / `search_page:2057` / `print_to_pdf` 的先例，把 CDP 调用 + JS 组装收进 session 方法，action 只做薄编排 + 回显——保持「session 拥有 CDP，action 拥有语义」的分层。
2. **保留 `selector` 字段名（不重命名为 browser-use 的同名 `selector`——本就一致）；但 `attributes` / `max_results` / `include_text` 三参直接采用 browser-use 命名**，最大化与标杆及 LLM 常识对齐（无需兼容旧字段，find_elements 此前无这些参数）。
3. **`selector` 故意**不**加 `min_length=1`。** 与 `FindTextParams.text` / `SearchPageParams.query` 不同：CSS 选择器可以合法地是单字符（`a`、`*`），且空串 `""` 会让 `querySelectorAll` 抛 `DOMException`，被 JS 内层 try/catch 捕获 → 返回 `{error:'Invalid CSS selector'}` → session 翻译成 `RuntimeError` → action 作为硬错误回显。校验由 JS 层兜底，无需在 Pydantic 层重复。
4. **`max_results` 用 `ge=1, le=200` 做编译期校验**（默认 50，对齐 browser-use 默认值）。browser-use 无上界校验；本项目对齐 `SearchPageParams.max_results`（`models.py:189-192`）的 `le=200`，防 LLM 传天文数字撑爆上下文。
5. **hit 用紧凑摘要 `long_term_memory`，miss 用等值回显。** browser-use hit memory 是 `Found N elements matching "X".`（本项目采纳）。但兄弟 `find_text` 约定 miss 时 `extracted_content == long_term_memory`（等值回显）——对多元素工具把 N 条清单灌进跨会话 memory 太费 token，故 hit 取摘要、miss 取等值回显（与 `find_text` / `search_page` 的 miss 行为一致）。
6. **丢弃当前的 `visible` / 写死 `href` 字段，改报 `children_count` + 可选 `attributes`。** `offsetParent !== null` 的可见性检测过浅（误判 `position:fixed`、忽略祖先链隐藏），结论不可信——不如不报，对齐 browser-use。`href` 不再写死，改为按需 `attributes=['href']` 取，且 `src`/`href` 走 DOM 属性拿绝对 URL（这是 browser-use 的关键正确性细节）。
7. **错误分级用 `RuntimeError` + action try/except。** browser-use 在 action 内联查 `result.exceptionDetails` / `data.get("error")`。TreeWalker 复用 `execute_js`（`session.py:1937-1939`）已有的 `RuntimeError("JS error: ...")`，并在 session 方法里把 JS 层 `{error:...}`（非法选择器 / 空返回）翻译成 `RuntimeError` 上抛；action 层做「硬错误 → `ActionResult(error=...)` + `logger.warning` / 软 miss → `extracted_content`」分流，对齐 `_action_find_text:833-858`。
8. **诚实标注同 browser-use 的限制：仅顶层文档、不进 iframe、不穿 shadow DOM。** `document.querySelectorAll` 只查顶层文档 light-DOM。这与 browser-use 完全一致——不回归也不夸大；穿透 iframe / shadow DOM 属阶段二。

---

## 阶段一：CSS 选择器元素查询（querySelectorAll + 可选 attributes + src/href 绝对 URL + children_count + 总数 + 分页）+ 封装 + 分级错误 + 测试（优先做，风险低）

### 1.1 `FindElementsParams` 扩展（`models.py:94-96`，4 空格）

before：
```python
class FindElementsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selector: str = Field(description="CSS selector to find elements on the page")
```

after：
```python
class FindElementsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selector: str = Field(
        description='CSS selector to query elements (e.g. "table tr", "a.link", "div.product")'
    )
    attributes: list[str] | None = Field(
        default=None,
        description='Specific attributes to extract (e.g. ["href", "src", "class"]). '
        'If not set, returns tag and text only. src/href are resolved to absolute URLs.',
    )
    max_results: int = Field(
        default=50, ge=1, le=200,
        description="Maximum elements to return (total count is always reported even when truncated).",
    )
    include_text: bool = Field(default=True, description="Include text content of each element")
```

> `selector` 不加 `min_length`（差异 §3）；`max_results` 用 `ge=1, le=200` 对齐 `SearchPageParams`（差异 §4）；`list[str] | None` 是 Pydantic v2 写法，项目已用 `int | None` / `str | None`（`quality:119` / `css_scope:185`）。`Field` / `ConfigDict` 已 import。

### 1.2 `BrowserSession.find_elements(...)` 封装（`session.py`，紧邻 `_build_search_page_js:144` / `search_page:2057`，4 空格）

新增三段：模块级 JS body 常量 + JS builder + session 方法。JS body 直接移植 browser-use `service.py:257-298`（`querySelectorAll` + 两层 try/catch + 逐元素提取 + `src`/`href` 走 DOM 属性 + 截断 300/500 + `children_count`）。

**(a) 模块级常量 `_FIND_ELEMENTS_JS_BODY`**（ES5 风格、`var`、最大兼容；body 引用 `SELECTOR/ATTRIBUTES/MAX_RESULTS/INCLUDE_TEXT` 四个 var，由 builder 注入）：
```python
_FIND_ELEMENTS_JS_BODY = r"""
    try {
        var elements;
        try {
            elements = document.querySelectorAll(SELECTOR);
        } catch (e) {
            return {error: 'Invalid CSS selector: ' + (e && e.message ? e.message : e), elements: [], total: 0};
        }
        var total = elements.length;
        var limit = Math.min(total, MAX_RESULTS);
        var results = [];
        for (var i = 0; i < limit; i++) {
            var el = elements[i];
            var item = {index: i, tag: el.tagName.toLowerCase()};
            if (INCLUDE_TEXT) {
                var text = (el.textContent || '').trim();
                item.text = text.length > 300 ? text.slice(0, 300) + '...' : text;
            }
            if (ATTRIBUTES && ATTRIBUTES.length > 0) {
                item.attrs = {};
                for (var j = 0; j < ATTRIBUTES.length; j++) {
                    var attrName = ATTRIBUTES[j];
                    var val;
                    // src/href: use the resolved DOM property (absolute URL),
                    // not getAttribute (raw authored value, often relative).
                    if ((attrName === 'src' || attrName === 'href')
                        && typeof el[attrName] === 'string' && el[attrName] !== '') {
                        val = el[attrName];
                    } else {
                        val = el.getAttribute(attrName);
                    }
                    if (val !== null) {
                        item.attrs[attrName] = val.length > 500 ? val.slice(0, 500) + '...' : val;
                    }
                }
            }
            item.children_count = el.children.length;
            results.push(item);
        }
        return {elements: results, total: total, showing: limit};
    } catch (e) {
        return {error: 'find_elements error: ' + (e && e.message ? e.message : e), elements: [], total: 0};
    }
"""
```
> 移植自 browser-use `service.py:257-298`，唯一加固：`e.message` 用 `(e && e.message ? e.message : e)` 防某些环境下 `e` 无 `message`（对齐本项目 `_SEARCH_PAGE_JS_BODY:109/139` 的防御写法）。raw 字符串保证 JS 反斜杠原样存活。

**(b) builder `_build_find_elements_js`**（紧邻 `_build_search_page_js:144`，4 空格）：
```python
def _build_find_elements_js(
    selector: str,
    attributes: list[str] | None,
    max_results: int,
    include_text: bool,
) -> str:
    """Build the find_elements IIFE expression.

    Each user value is serialized via ``json.dumps`` into a ``var``
    declaration and the body references those vars by name — the body is a
    constant string with NO f-string interpolation of user text. This is the
    safe-injection pattern (mirrors browser-use service.py:321-334) and
    matches the project's ``_build_search_page_js`` (session.py:144-169).
    """
    params_js = (
        f"var SELECTOR = {json.dumps(selector)};\n"
        f"var ATTRIBUTES = {json.dumps(attributes)};\n"
        f"var MAX_RESULTS = {json.dumps(max_results)};\n"
        f"var INCLUDE_TEXT = {json.dumps(include_text)};\n"
    )
    return "(function() {\n" + params_js + _FIND_ELEMENTS_JS_BODY + "\n})()"
```
> `attributes=None` → `json.dumps(None)` → JS `null` → body 里 `ATTRIBUTES && ATTRIBUTES.length > 0` 为 false，不取属性（等价「只返回 tag + text + children_count」）。`json.dumps` 对含 `"` / `\` / 中文的选择器安全转义。

**(c) session 方法 `find_elements`**（紧邻 `search_page:2057`，4 空格）：
```python
async def find_elements(
    self,
    selector: str,
    *,
    attributes: list[str] | None = None,
    max_results: int = 50,
    include_text: bool = True,
) -> dict:
    """Query DOM elements by CSS selector via a single Runtime.evaluate.

    Mirrors browser-use ``find_elements`` (``service.py:1297-1330`` + JS body
    ``:257-298``): ``document.querySelectorAll`` + per-element extraction
    (tag, text, attributes, children_count), returning
    ``{elements, total, showing}``. ``src``/``href`` resolve to absolute URLs.

    Raises ``RuntimeError`` on a JS exception (via ``execute_js``), on a null
    return, or when the JS layer reports ``{error: ...}`` (invalid CSS
    selector) — the action layer maps these to a hard ``ActionResult(error=...)``.
    A clean miss returns ``total=0`` and never raises; the action layer builds
    the soft echo.

    Limitations (same as browser-use): top-document elements only; does not
    pierce shadow DOM or traverse into iframes.
    """
    js = _build_find_elements_js(selector, attributes, max_results, include_text)
    data = await self.execute_js(js)  # returnByValue=True -> dict; RuntimeError on exceptionDetails
    if data is None:
        raise RuntimeError("find_elements returned no result")
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(f"find_elements: {data['error']}")
    return data
```

### 1.3 `_format_find_results` helper（`actions.py` 模块级，紧邻 `_format_search_results:64`，4 空格）

移植 browser-use `service.py:363-401`：
```python
def _format_find_results(data: dict, selector: str) -> str:
    """Format find_elements {elements, total, showing} as LLM-readable text.

    Mirrors browser-use ``_format_find_results`` (``service.py:363-401``).
    Caller guarantees total > 0 (the total==0 soft-miss path is handled in
    _action_find_elements).
    """
    elements = data.get("elements", [])
    total = data.get("total", 0)
    showing = data.get("showing", 0)

    lines = [f'Found {total} element{"s" if total != 1 else ""} matching "{selector}":', ""]
    for el in elements:
        idx = el.get("index", 0)
        tag = el.get("tag", "?")
        text = el.get("text", "")
        attrs = el.get("attrs", {})
        children = el.get("children_count", 0)

        parts = [f"[{idx}] <{tag}>"]
        if text:
            display_text = " ".join(text.split())
            if len(display_text) > 120:
                display_text = display_text[:120] + "..."
            parts.append(f'"{display_text}"')
        if attrs:
            attr_strs = [f'{k}="{v}"' for k, v in attrs.items()]
            parts.append("{" + ", ".join(attr_strs) + "}")
        parts.append(f"({children} children)")
        lines.append(" ".join(parts))

    if showing < total:
        lines.append(
            f"\nShowing {showing} of {total} total elements. Increase max_results to see more."
        )
    return "\n".join(lines)
```
> text 在 JS 层截 300、展示层再折叠空白截 120（对齐 browser-use 双阈值），保证单条不喧宾夺主；attrs 渲染成 `{href="..." class="..."}`。

### 1.4 `_action_find_elements` 重写（`actions.py:818-831`，4 空格）

before：见上文 Context 节引文。

after：
```python
async def _action_find_elements(self, params: dict, browser: BrowserSession) -> ActionResult:
    selector = params["selector"]
    attributes = params.get("attributes")
    max_results = params.get("max_results", 50)
    include_text = params.get("include_text", True)
    try:
        data = await browser.find_elements(
            selector,
            attributes=attributes,
            max_results=max_results,
            include_text=include_text,
        )
    except Exception as e:
        # CDP layer failure (connection drop / invalid selector surfaced as
        # {error} -> RuntimeError) = tool execution failure; surface a
        # find_elements-specific error rather than the generic Tools.execute
        # fallback. Aligns with _action_find_text / _action_search_page.
        logger.warning("find_elements(%r) failed: %s", selector, e)
        return ActionResult(error=f"Find elements failed: {e}")
    total = data.get("total", 0)
    if total == 0:
        # Soft echo: "selector matched nothing" is actionable info (LLM can
        # fix the selector / wait / accept absence), not a tool failure —
        # aligns with find_text / search_page / browser-use.
        msg = f'No elements found matching "{selector}"'
        logger.info(msg)
        return ActionResult(extracted_content=msg, long_term_memory=msg)
    formatted = _format_find_results(data, selector)
    memory = f'Found {total} element{"s" if total != 1 else ""} matching "{selector}".'
    logger.info(memory)
    return ActionResult(extracted_content=formatted, long_term_memory=memory)
```
> 三层分流（对齐 `_action_find_text:833-858`）：硬错误（`RuntimeError` 上抛）→ `ActionResult(error="Find elements failed: ...")` + `logger.warning`；软 miss（`total==0`）→ `extracted_content == long_term_memory`（等值回显，差异 §5）；命中 → 完整格式化文本进 `extracted_content`、紧凑摘要进 `long_term_memory`。不再 `str(result)` 裸 dump。

### 1.5 `ACTION_DEFINITIONS["find_elements"]` 描述更新（`models.py:233-237`，4 空格）

before：
```python
"find_elements": (
    FindElementsParams,
    "Find elements on the page using a CSS selector",
    False,
),
```

after：
```python
"find_elements": (
    FindElementsParams,
    "Query DOM elements by CSS selector (zero LLM cost, instant). Returns "
    "matching elements with tag, text, and attributes. Use "
    "attributes=['href','src'] to extract specific attributes (src/href "
    "resolve to absolute URLs). max_results caps the returned list; the total "
    "count is always reported. Use to explore page structure, count items, "
    "get links/attributes.",
    False,
),
```
> 描述对齐 browser-use（`service.py:1298`），让 LLM 知道可取属性 / 会回显总数 / 是瞬时零成本工具，能与 `find_text` / `search_page` 分工。

### 1.6 新增 `tests/test_find_elements.py`（TAB 缩进，对齐 `tests/test_search_page.py`）

测试分四层（动作层 / 参数层 / 会话层 / builder + 格式化层），覆盖正常路径 + 关键边界：

**TestFindElementsAction**（mock `BrowserSession`，驱动 `Tools().execute("find_elements", {...}, browser)`，断言返回 `ActionResult`；只读工具断言 `browser.get_state.assert_not_awaited()`）：
- `test_hit_singular_echoes_formatted_results`：1 个元素 → `Found 1 element matching "..."`、单数 `long_term_memory`、无 `Showing ... of ...` 尾注；`find_elements` 以默认 kwargs（`attributes=None, max_results=50, include_text=True`）被调用一次。
- `test_hit_plural_with_truncation_footer`：`total=4, showing=2` → `Found 4 elements matching "..."` + 尾注 `Showing 2 of 4 total elements`、复数 memory。
- `test_attributes_rendered`：元素带 `attrs={href:"https://x/a", class:"btn"}` → 输出含 `{href="https://x/a" class="btn"}`。
- `test_text_collapsed_and_truncated`：长 text → 折叠空白 + 截 120 + `...`。
- `test_no_matches_is_soft_echo_not_error`：`total=0` → `extracted_content == 'No elements found matching "..."'`、`long_term_memory` 等值、`error is None`（对齐 find_text miss）。
- `test_invalid_selector_is_hard_error`：`browser.find_elements` 抛 `RuntimeError("find_elements: Invalid CSS selector: ...")` → `error="Find elements failed: ..."`、`is_done is False`、`logger.warning` 命中。
- `test_does_not_call_get_state`：只读工具，断言 `browser.get_state.assert_not_awaited()`。
- `test_forwards_attributes_and_flags_as_kwargs`：传 `attributes=['href'], max_results=10, include_text=False` → 以这些 kwargs 调 `browser.find_elements`。

**TestFindElementsParams**（Pydantic 校验）：
- `test_accepts_selector_only`：只给 `selector` → 其余取默认（`attributes=None, max_results=50, include_text=True`）。
- `test_accepts_attributes_list`：`attributes=["href","src"]`、`include_text=False` 通过。
- `test_max_results_bounds`：`ge=1, le=200`——0 / 201 / 负数被 `ValidationError` 拒。
- `test_forbids_extra_fields`：未知字段被 `extra="forbid"` 拒。
- `test_selector_required`：缺 `selector` → `ValidationError`。

**TestFindElementsSession**（直接驱动 `BrowserSession.find_elements`，mock `client.send.Runtime.evaluate`）：
- `test_runs_one_runtime_evaluate_return_by_value`：返回 `{elements:[...], total, showing}` 原样透传；`execute_js` 被调一次、入参含 `returnByValue=True`。
- `test_js_exception_propagates`：`exceptionDetails` → `execute_js` 抛 `RuntimeError("JS error: ...")` → 方法向上抛。
- `test_js_layer_error_raises`：JS 返回 `{error:"Invalid CSS selector: ..."}` → `RuntimeError("find_elements: Invalid CSS selector: ...")`。
- `test_null_return_raises`：`execute_js` 返回 `None` → `RuntimeError("find_elements returned no result")`。

**TestFindElementsBuilderAndFormatter**（纯函数，无需 mock）：
- `test_builder_injects_via_json_dumps`：含 `"` 的选择器（如 `a[href="x"]`）→ 生成的 JS 里 `var SELECTOR = "a[href=\"x\"]"`（转义，非裸拼），且整个表达式是合法 IIFE。
- `test_builder_serializes_attributes_none_as_null`：`attributes=None` → `var ATTRIBUTES = null;`；`attributes=["href"]` → `var ATTRIBUTES = ["href"];`。
- `test_formatter_renders_elements_and_footer`：`_format_find_results({elements:[{index:0,tag:"a",text:"hi",attrs:{href:"/x"},children_count:2}], total:1, showing:1}, "a")` → 含 `Found 1 element matching "a":`、`[0] <a> "hi" {href="/x"} (2 children)`、无尾注。
- `test_formatter_no_attrs_no_text_omits_segments`：`include_text=False` 且无 attributes → 仅 `[0] <div> (0 children)`。

> 对齐 `tests/test_search_page.py:1-64` 的 `_make_browser` mock 工厂与三层 class 结构；只读工具统一断言 `get_state` 未被 await（见 `test_search_page.py` 同款断言）。

### 1.7 阶段一文件清单

| 文件 | 改动 | 锚点 |
|---|---|---|
| `src/tree_walker/tools/models.py` | `FindElementsParams` 扩 3 字段；`ACTION_DEFINITIONS["find_elements"]` 描述更新 | `:94-96` / `:233-237` |
| `src/tree_walker/browser/session.py` | 新增 `_FIND_ELEMENTS_JS_BODY` + `_build_find_elements_js` + `BrowserSession.find_elements` | 紧邻 `_SEARCH_PAGE_JS_BODY:55` / `_build_search_page_js:144` / `search_page:2057` |
| `src/tree_walker/tools/actions.py` | 新增 `_format_find_results`；重写 `_action_find_elements` | 紧邻 `_format_search_results:64` / `_action_find_elements:818-831` |
| `tests/test_find_elements.py` | 新建（4 个 class，~20 用例） | 对齐 `tests/test_search_page.py` |
| `docs/Tools技术细节/04_动作清单与CDP映射.md` | §4.7 同步：新参数、`{elements,total,showing}` 返回、错误分级、session 封装锚点 | §4.7 |

### 1.8 阶段一测试计划

```powershell
# 单文件
uv run python -m pytest tests/test_find_elements.py -x -v
# 覆盖率
uv run python -m pytest tests/test_find_elements.py --cov=tree_walker.tools.actions --cov=tree_walker.browser.session --cov-report=term-missing
# 全量回归（确保 search_page / find_text 等兄弟工具无 regression）
uv run python -m pytest tests/ -x -v
```

---

## 阶段二（可选，独立，对齐 / 超越 browser-use 完整能力）

- **穿透 shadow DOM / iframe**：用 `>>>` 深度组合器 + 递归 `shadowRoot`，或对每个 iframe 单独 evaluate（browser-use 亦未做，见 `service.py:257` 注释）。需 `search_page` 阶段二同步推进。
- **回显 `backend_node_id` / 接入 `selector_map`**：让 find_elements 的结果可直接喂给 `click` / `input_text`（按 index 操作）——需用 `DOM.performSearch` 或 `DOM.pushNodesByBackendIdToFrontend`，改动面大，独立成期。
- **回显几何 `DOMRect`**：取 `getBoundingClientRect()`，配合「元素是否在视口」判断；届时一并恢复更稳的 `visible` 检测（检查祖先链 `display/visibility/opacity` + 视口相交）。
- **`unique` / 首匹配模式**：browser-use 无此参数；若需要「只取第一个」可在阶段二加 `first_only: bool`。
- **大结果落盘**：命中数千节点时把完整清单写文件、`extracted_content` 只回摘要（对齐 `TruncationSettings` 思路）。

---

## 风险与回归点

| 风险 | 影响 | 缓解 |
|---|---|---|
| 选择器注入破 JS 语法（含 `"` / `\`） | 表达式解析失败 → 硬错误 | builder 用 `json.dumps` 注入（差异 §1.2），测试 `test_builder_injects_via_json_dumps` 覆盖 |
| 非法选择器（如 `[[[invalid`） | `querySelectorAll` 抛 `DOMException` | JS 内层 try/catch → `{error}` → `RuntimeError` → 硬错误回显「Find elements failed: ...」；测试覆盖 |
| `src`/`href` 取值口径变化 | 旧实现写死 `href`、新实现按需取且走 DOM 属性（绝对 URL） | 属预期改进（差异 §6）；描述已说明；测试 `test_attributes_rendered` 覆盖绝对 URL |
| 命中超量元素撑爆上下文 | `extracted_content` 过长 | `max_results` 默认 50 封顶 + 尾注回显真实 `total`；`ActionResult.__str__` 还有 500 字符兜底 |
| `total` 口径变化 | 旧实现无总数，新实现始终回真实命中数 | 属预期改进；描述已说明 |
| 返回结构从 `str(list)` 变结构化文本 | 旧消费者（如有）解析 `str(list)` 会断 | `extracted_content` 本就是给 LLM 读的自由文本，无程序化消费者；描述同步 |
| shadow DOM / iframe 内元素查不到 | 静默漏报 | 阶段一诚实标注限制（差异 §8）；阶段二推进穿透 |
| 删 `visible` 字段 | 依赖该字段的下游断言 | 当前无下游（find_elements 无测试、无 selector_map 接入）；描述同步 |

---

## 验证方法

1. **单测全绿 + 覆盖率 ≥85%**：`uv run python -m pytest tests/test_find_elements.py -x -v` + `--cov` 查看 `actions` / `session` 覆盖率。
2. **全量回归**：`uv run python -m pytest tests/ -x -v`，确认 `test_find_text.py` / `test_search_page.py` / `test_screenshot.py` 等兄弟工具无 regression。
3. **会话层冒烟**（mock CDP 即可，或接真实浏览器）：
   ```python
   # uv run python -c "..."
   data = await browser.find_elements("a", attributes=["href"], max_results=5)
   assert {"elements","total","showing"} <= data.keys()
   ```
4. **动作层冒烟**：
   ```python
   r = await Tools().execute("find_elements", {"selector":"img","attributes":["src"]}, browser)
   assert r.error is None and "Found" in r.extracted_content
   ```
5. **注入安全冒烟**：用 `a[href="x"]`、含 `\` 与中文的选择器跑 `_build_find_elements_js`，确认生成的 IIFE 合法且无裸拼。
6. **回归对照**：与 `search_page`（commit `e8e2aed`）、`find_text` 的错误分级 / 软回显 / `long_term_memory` 约定逐条比对一致。

---

## 验收 checklist（阶段一）

- [ ] `FindElementsParams` 扩 `attributes` / `max_results` / `include_text`（`models.py`）
- [ ] `ACTION_DEFINITIONS["find_elements"]` 描述更新（`models.py`）
- [ ] `_FIND_ELEMENTS_JS_BODY` + `_build_find_elements_js` + `BrowserSession.find_elements` 落地（`session.py`）
- [ ] `_format_find_results` helper 落地（`actions.py`）
- [ ] `_action_find_elements` 重写为三层分流（硬错误 / 软 miss / 命中）（`actions.py`）
- [ ] `tests/test_find_elements.py` 新建，4 class ~20 用例全绿，覆盖率 ≥85%
- [ ] 全量 `pytest tests/ -x -v` 无 regression
- [ ] `docs/Tools技术细节/04_动作清单与CDP映射.md` §4.7 同步（参数、返回、错误分级、封装锚点）
- [ ] 缩进按文件：src = 4 空格、tests = TAB
