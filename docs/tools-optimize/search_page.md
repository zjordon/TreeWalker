# search_page 工具优化方案（分阶段）

> 参照 browser-use（`browser_use/tools/service.py:1260-1295` search_page 动作体、`:181-255` `_SEARCH_PAGE_JS_BODY`、`:301-318` `_build_search_page_js`、`:337-360` `_format_search_results`、`browser_use/tools/views.py:30-36` `SearchPageAction`）完善本项目 search_page 工具。
> 相关现状文档：`docs/Tools技术细节/04_动作清单与CDP映射.md` 的 4.18 节；参考标杆：`browser-use/docs/Tools技术细节/06-动作详解-数据处理与文件.md` 的 17. search_page 节。
> 同族先例：`docs/tools-optimize/screenshot.md`（screenshot 阶段一已落地，commit `b1fc97e` / issue #38 / PR #39）、`docs/tools-optimize/save_as_pdf.md`（commit `21153d8` / issue #40 / PR #41）、`docs/tools-optimize/extract.md`（commit `a8148a4` / issue #42 / PR #43）——本方案在结构、错误处理、封装思路上全面对齐三者阶段一。

---

## 适用场景（什么时候会用到 search_page）

`search_page` 在 agent 工具集里的定位是 **「grep 式页面文本搜索：一次 `Runtime.evaluate`、零 LLM 成本、瞬时返回所有匹配及其上下文」**。它和其它「读 / 找」类工具分工明确：

| 工具 | 职责 | 与 search_page 的区别 |
|---|---|---|
| `find_text` | 定位某段文字并 **滚动 + 高亮** 首条匹配到视口 | 只找「第一条」并驱动视口；不返回匹配清单 / 总数 / 上下文 |
| `get_state`（每步自动） | 给 agent 看 DOM 快照，决定点哪 | 面向「看 + 操作」，不为全文检索优化 |
| `evaluate` | 跑任意 JS 拿原始返回值 | 通用逃生口，无检索语义、无上下文裁剪 |
| `extract` | 语义提炼页面内容 → 摘要或结构化 JSON | 走 LLM 理解；search_page 是确定性字符串检索，无理解 |
| **`search_page`** | **确定性全文检索 → 返回带上下文 / 元素路径 / 总数的匹配清单** | **唯一一个「读完 + 检索 + 带定位信息」的确定性工具** |

典型场景：

1. **核对内容是否存在 / 计数**：「这页有没有『Out of stock』？有几处？」。`search_page` 一次返回 `total` + 逐条上下文，比 `find_text`（只滚动首条）或 `extract`（跑 LLM）都快且便宜。
2. **批量定位数据再操作**：搜出所有价格 / 订单号 / 链接的上下文与元素路径，配合后续 `click` / `extract`。正则模式（`regex=True`）尤其适合抓「`\$\d+\.\d{2}`」这类结构化片段。
3. **限定范围检索**：用 `css_scope='div#main'` 只在主内容区搜，避开导航 / 页脚噪音。
4. **验证页面状态**：提交表单后搜「Thank you」「Error」判断成败，比看整页 DOM 快。

**什么时候不需要它**：只要「滚动到某段文字并看见它」就够时用 `find_text`（带视觉高亮）；需要「理解语义 / 结构化抽取」时用 `extract`。`search_page` 是为「确定性、全量、带定位、零 LLM 成本」的检索存在的。

**可用性提示**：阶段一落地后上述场景 1–4 全部可用；iframe / shadow DOM 内的文本检索属阶段二（browser-use 同样不支持，见下文「与 browser-use 的关键差异」）。

---

## Context（为什么做这个改动）

当前 TreeWalker 的 `search_page` 是一个**极简实现**，明显落后于刚做完阶段一优化的 `screenshot` / `save_as_pdf` / `extract`，也落后于参照标杆 browser-use：

1. **算法过于简陋**（`actions.py:1184-1199`，4 空格）：只对 `document.body.innerText` 按 `'\n'` 切行 + 小写 `includes` 子串过滤，取前 20 行拼接返回。不支持正则、不支持大小写敏感切换、不区分范围。
2. **参数几乎为零**：`SearchPageParams`（`models.py:176-178`）只有 `query: str`，无 `min_length`、无 `regex`、无 `case_sensitive`、无 `max_results`、无 `css_scope`。
3. **无匹配总数 / 无分页**：写死 `slice(0, 20)`，LLM 拿到 20 行却不知道总共多少条；第 21 条以后永远不可见、不可达。
4. **无逐条定位信息**：返回的是整行裸文本，没有上下文裁剪、没有元素路径、没有字符位置——LLM 无法把一条匹配连到可点击的 DOM 节点。
5. **注入方式不安全且不一致**：`f"...includes({repr(query.lower())})..."`（`actions.py:1189`）用 Python `repr` 生成字面量，而兄弟 `_find_text_js_fallback` 用 `json.dumps`（`session.py:1899`）——`repr` 不是项目约定，对含特殊字符的 query 不够稳。
6. **错误不分级**：JS 异常直接 `error=str(e)`（`actions.py:1198-1199`），丢失「miss = 软回显 / CDP 失败 = 硬错误」的兄弟约定（`_action_find_text` `actions.py:808-833`）；空 query 会让 `includes("")` 命中所有行返回 20 行噪音（无 `min_length` 校验）。
7. **从不设 `long_term_memory`**：与 `find_text` / `navigate` / `click` 的回显约定不一致（`find_text` 测试甚至断言 `extracted_content == long_term_memory`）。
8. **无截断保护**：20 行全长拼接，遇压缩 / 超长行会撑爆上下文；兄弟工具（`evaluate` / `extract`）都用 `self._truncation.*` 封顶。
9. **描述对 LLM 无指引**：`ACTION_DEFINITIONS["search_page"]` 描述仅为 `"Search for text within the current page content"`，LLM 不知道它与 `find_text` 的分工。
10. **零测试**：`tests/` 下无 `test_search_page.py`。
11. **现状文档过时**：`04_动作清单与CDP映射.md` §4.18 写 `Runtime.evaluate` 在 `session.py:785`（实际 `execute_js` 在 `:1809`）；且注释仍称 `find_text` 用 `window.find()`（实际已是 `DOM.performSearch` 链路）。

**参照标杆 browser-use 的做法**（`browser_use/tools/service.py:1260-1295` 动作体、`:181-255` JS body、`:301-318` builder、`:337-360` 格式化、`views.py:30-36` 参数）：单次 `Runtime.evaluate`（`returnByValue=True, awaitPromise=True`）执行一个 TreeWalker-TextNodes IIFE——把范围内所有文本节点拼成一条带 `{offset, length, node}` 偏移索引的大字符串，用 `g`-flag `RegExp` 跑 `exec` 循环（含零宽匹配保护 `if (match[0].length === 0) re.lastIndex++`），每条匹配回填 `{match_text, context, element_path, char_position}`，返回 `{matches, total, has_more}`；非正则模式先转义元字符；`css_scope` 经 `querySelector` 解析，未命中直接报错；用户值用 `json.dumps` 注入成 `var` 声明（body 引用 var，绝不 f-string 拼用户串）；miss 不是 error（返回 `extracted_content`）；不滚动 / 不高亮（那是 `find_text` 的职责）。

**预期结果**：把 `search_page` 升级到 browser-use 同级的「grep 式页面文本搜索」（TreeWalker + 正则 + 逐条上下文 + 元素路径 + 总数 + `has_more`），同时按本项目约定**封装进 `BrowserSession.search_page`**、**错误分级捕获**、**补齐单测覆盖率 ≥85%**，并同步修正现状文档。

---

## 工程约束（实施时务必遵守）

- Windows + PowerShell；包用 `uv`，跑脚本 / 测试用 `uv run python ...`。测试命令 `uv run python -m pytest tests/ -x -v`。
- **缩进按文件**（已复核）：`actions.py` / `models.py` / `browser/session.py` = **4 空格**；`tests/test_search_page.py` = **TAB**（对齐 `tests/test_find_text.py` / `tests/test_screenshot.py` / `tests/test_save_as_pdf.py`）。下文代码片段均按目标文件缩进给出。
- 改完跑相关单测 + 全量回归；覆盖率目标 >85%。
- 不主动 `git commit` / `git push`。
- `json` / `logging` / `Any` 在 `session.py` 已是模块级 import（`_find_text_js_fallback:1899` 已用 `json.dumps`）；`logger` 已存在；`Field` / `ConfigDict` / `Literal` 已在 `models.py` import（`ScreenshotParams:113` / `SaveAsPdfParams:125` 已用）。新增代码无需新 import。

---

## 与 browser-use 的关键差异（有意为之，不照搬）

1. **封装进 `BrowserSession.search_page`，而非在 action 里直连 CDP。** browser-use 在 `search_page` action 内联 `cdp_client.send.Runtime.evaluate`。TreeWalker 按 `print_to_pdf:766-820` / `find_text:1825` 的先例，把 CDP 调用 + JS 组装收进 session 方法，action 只做薄编排 + 回显——保持「session 拥有 CDP，action 拥有语义」的分层。
2. **保留 `query` 字段名，不重命名为 browser-use 的 `pattern`。** 向后兼容已有 schema / description / 文档——对齐 `extract.md` 保留 `goal` 而非 browser-use `query` 的决策。`query` ≡ `pattern`，docstring 注明即可。
3. **`query` 加 `min_length=1`。** 对齐 `FindTextParams.text` 的 `min_length=1`；browser-use 无此校验，空串会让 `includes("")` 命中所有行返回噪音。
4. **hit 用紧凑摘要 `long_term_memory`，miss 用等值回显。** browser-use 的 hit memory 是 `Searched page for "X": N matches found.` 紧凑摘要（本项目采纳）。但兄弟 `find_text` 的约定是 `extracted_content == long_term_memory`（等值回显）——对多匹配工具把 25 条上下文灌进跨会话 memory 太费 token，故 hit 取摘要、miss 取等值回显（与 `find_text` 的 miss 行为一致）。
5. **只读：不滚动、不高亮。** browser-use 同；滚动 + 高亮是 `find_text` 的职责（`session.py:1870` `scrollIntoViewIfNeeded` + `:1935` `highlight_element`）。保持本项目已有的 `search_page`（返回匹配）vs `find_text`（滚动 + 高亮）分工。
6. **错误分级用 `RuntimeError` + action try/except。** browser-use 在 action 内联查 `result.exceptionDetails` / `data.get("error")`。TreeWalker 复用 `execute_js`（`session.py:1820-1822`）已有的 `RuntimeError("JS error: ...")`，并在 session 方法里把 JS 层 `{error:...}`（非法 regex / scope 未命中 / 空返回）翻译成 `RuntimeError` 上抛；action 层做「硬错误 → `ActionResult(error=...)` / 软 miss → `extracted_content`」分流，对齐 `_action_find_text:808-833`。
7. **诚实标注同 browser-use 的限制：仅顶层文档文本节点、不进 iframe、不穿 shadow DOM。** TreeWalker 从 `document.body`（或 `css_scope` 元素）出发遍历 `NodeFilter.SHOW_TEXT`，shadow host 的 `textContent` 只返回 light-DOM 文本。这与 browser-use 完全一致——不回归也不夸大；穿透 iframe / shadow DOM 属阶段二（browser-use 自己也没做，相关 TODO 见 `default_action_watchdog.py:2685`）。

---

## 阶段一：grep 式全文检索（TreeWalker + 正则 + 上下文 + 元素路径 + 总数）+ 封装 + 分级错误 + 测试（优先做，风险低）

### 1.1 `SearchPageParams` 扩展（`models.py:176-178`，4 空格）

before：
```python
class SearchPageParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(description="Text to search for within the current page")
```
after：
```python
class SearchPageParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(
        min_length=1,
        description="Text or regex pattern to search for within the current page",
    )
    regex: bool = Field(default=False, description="Treat query as a regex (default: literal text match).")
    case_sensitive: bool = Field(default=False, description="Case-sensitive match (default: case-insensitive).")
    context_chars: int = Field(default=150, ge=0, description="Characters of surrounding context per match.")
    css_scope: str | None = Field(
        default=None,
        description="CSS selector to limit search scope (e.g. 'div#main'). Selector not matching anything is an error.",
    )
    max_results: int = Field(
        default=25, ge=1, le=200,
        description="Maximum matches to return (total count is always reported even when truncated).",
    )
```
> 保留 `query` 名（见差异 §2）；加 `min_length=1`（差异 §3）；`max_results` 用 `ge=1, le=200` 做编译期校验，避免 LLM 传 0 或天文数字。`Field` / `ConfigDict` 已 import（`ScreenshotParams:113-114` 已用同款）。

### 1.2 `BrowserSession.search_page(...)` 封装（`session.py`，紧邻 `execute_js:1809` / `find_text:1825`，4 空格）

新增三段：模块级 JS body 常量 + JS builder + session 方法。JS body 直接移植 browser-use `service.py:181-255`（TreeWalker + 偏移索引 + `RegExp.exec` + 零宽保护 + 内联 `_getPath`）。

**(a) 模块级常量 `_SEARCH_PAGE_JS_BODY`**（ES5 风格、`var`、最大兼容；body 引用 `PATTERN/IS_REGEX/CASE_SENSITIVE/CONTEXT_CHARS/CSS_SCOPE/MAX_RESULTS` 六个 var，由 builder 注入）：
```javascript
try {
    var scope = CSS_SCOPE ? document.querySelector(CSS_SCOPE) : document.body;
    if (!scope) {
        return {error: 'CSS scope selector not found: ' + CSS_SCOPE, matches: [], total: 0};
    }
    var walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
    var fullText = '';
    var nodeOffsets = [];
    while (walker.nextNode()) {
        var node = walker.currentNode;
        var text = node.textContent;
        if (text && text.trim()) {
            nodeOffsets.push({offset: fullText.length, length: text.length, node: node});
            fullText += text;
        }
    }
    var flags = CASE_SENSITIVE ? 'g' : 'gi';
    var re;
    try {
        if (IS_REGEX) {
            re = new RegExp(PATTERN, flags);
        } else {
            // 非正则：转义元字符，按字面量匹配（对齐 browser-use service.py:206-210）
            re = new RegExp(PATTERN.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), flags);
        }
    } catch (e) {
        return {error: 'Invalid regex pattern: ' + (e && e.message ? e.message : e), matches: [], total: 0};
    }
    var matches = [];
    var total = 0;
    var match;
    while ((match = re.exec(fullText)) !== null) {
        total++;
        if (matches.length < MAX_RESULTS) {
            var start = Math.max(0, match.index - CONTEXT_CHARS);
            var end = Math.min(fullText.length, match.index + match[0].length + CONTEXT_CHARS);
            var context = fullText.slice(start, end);
            var elementPath = '';
            for (var i = 0; i < nodeOffsets.length; i++) {
                var no = nodeOffsets[i];
                if (no.offset <= match.index && no.offset + no.length > match.index) {
                    elementPath = _getPath(no.node.parentElement);
                    break;
                }
            }
            matches.push({
                match_text: match[0],
                context: (start > 0 ? '...' : '') + context + (end < fullText.length ? '...' : ''),
                element_path: elementPath,
                char_position: match.index
            });
        }
        if (match[0].length === 0) re.lastIndex++;   // 零宽匹配保护，防 .* 死循环
    }
    return {matches: matches, total: total, has_more: total > MAX_RESULTS};
} catch (e) {
    return {error: String((e && e.message) || e), matches: [], total: 0};
}

function _getPath(el) {
    if (!el || el === document.body) return '';
    var parts = [];
    while (el && el !== document.body) {
        var tag = (el.tagName || '').toLowerCase();
        if (!tag) break;
        var part = tag;
        if (el.id) {
            part += '#' + el.id;
        } else if (el.className && typeof el.className === 'string') {
            var cls = el.className.trim().split(/\s+/).slice(0, 2).join('.');
            if (cls) part += '.' + cls;
        }
        parts.unshift(part);
        el = el.parentElement;
    }
    return parts.join(' > ');
}
```
> 移植自 browser-use `service.py:181-255`；`_getPath` 走父链到 `document.body` 为止，输出 `footer#footer > p` 形式。`total` 始终是真实命中数，`matches` 仅存前 `MAX_RESULTS` 条，`has_more` 标记截断。

**(b) `_build_search_page_js(...)` builder**（`json.dumps` 注入成 var，绝不 f-string 拼用户串，对齐 `_find_text_js_fallback:1899`）：
```python
def _build_search_page_js(
    pattern: str,
    regex: bool,
    case_sensitive: bool,
    context_chars: int,
    css_scope: str | None,
    max_results: int,
) -> str:
    params_js = (
        f"var PATTERN = {json.dumps(pattern)};\n"
        f"var IS_REGEX = {json.dumps(regex)};\n"
        f"var CASE_SENSITIVE = {json.dumps(case_sensitive)};\n"
        f"var CONTEXT_CHARS = {json.dumps(context_chars)};\n"
        f"var CSS_SCOPE = {json.dumps(css_scope)};\n"
        f"var MAX_RESULTS = {json.dumps(max_results)};\n"
    )
    return "(function() {\n" + params_js + _SEARCH_PAGE_JS_BODY + "\n})()"
```
> 关键安全点：用户值（`pattern` / `css_scope`）经 `json.dumps` 序列化成 JS 字面量赋给 var，body 只引用 var 名。body 本体是常量字符串，**绝不用 f-string 插值用户文本**——消除引号 / 元字符注入类 bug。

**(c) `search_page` session 方法**（复用 `execute_js` 的 `returnByValue=True`；JS 层 `{error:...}` 翻译成 `RuntimeError`）：
```python
async def search_page(
    self,
    pattern: str,
    *,
    regex: bool = False,
    case_sensitive: bool = False,
    context_chars: int = 150,
    css_scope: str | None = None,
    max_results: int = 25,
) -> dict:
    """Grep-style page text search via a single Runtime.evaluate.

    Mirrors browser-use ``search_page`` (``service.py:1260-1295`` + JS body
    ``:181-255``): a TreeWalker over text nodes builds an offset-indexed text
    buffer, a ``g``-flag RegExp exec loop collects matches with context +
    element path, and ``{matches, total, has_more}`` is returned.

    Raises ``RuntimeError`` on JS exception (via ``execute_js``), on a null
    return, or when the JS layer reports ``{error: ...}`` (invalid regex /
    css_scope not found) — the action layer maps these to a hard
    ``ActionResult(error=...)``. A clean miss returns ``total=0`` and never
    raises; the action layer builds the soft echo.
    """
    js = _build_search_page_js(pattern, regex, case_sensitive, context_chars, css_scope, max_results)
    data = await self.execute_js(js)  # returnByValue=True → dict; RuntimeError on exceptionDetails
    if data is None:
        raise RuntimeError("search_page returned no result")
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(f"search_page: {data['error']}")
    return data
```
> `execute_js`（`session.py:1809-1823`）已带 `returnByValue=True, awaitPromise=True` 并在 `exceptionDetails` 抛 `RuntimeError("JS error: ...")`，故本方法无需重复 CDP 模板。返回的 dict 形如 `{matches:[{match_text, context, element_path, char_position}], total:int, has_more:bool}`。

### 1.3 `_action_search_page` 重写（`actions.py:1184-1199`，4 空格）

薄编排 + 分级错误（硬错误 / 软 miss 二分），对齐 `_action_find_text:808-833`：

before：
```python
async def _action_search_page(self, params: dict, browser: BrowserSession) -> ActionResult:
    query = params["query"]
    js_code = (
        "(function() {"
        "  const body = document.body.innerText;"
        f"  const lines = body.split('\\n').filter(l => l.toLowerCase().includes({repr(query.lower())}));"
        "  return lines.slice(0, 20).join('\\n');"
        "})()"
    )
    try:
        result = await browser.execute_js(js_code)
        if result:
            return ActionResult(extracted_content=str(result))
        return ActionResult(extracted_content=f"No matches for '{query}'")
    except Exception as e:
        return ActionResult(error=str(e))
```
after：
```python
async def _action_search_page(self, params: dict, browser: BrowserSession) -> ActionResult:
    query = params["query"]
    try:
        data = await browser.search_page(
            query,
            regex=params.get("regex", False),
            case_sensitive=params.get("case_sensitive", False),
            context_chars=params.get("context_chars", 150),
            css_scope=params.get("css_scope"),
            max_results=params.get("max_results", 25),
        )
    except Exception as e:
        # 硬错误：CDP 故障 / 非法 regex / css_scope 未命中 —— 工具执行失败，
        # 抛 find_text 式具体错误而非冒泡通用 catch（actions.py:181-183）。
        logger.warning("search_page(%r) failed: %s", query, e)
        return ActionResult(error=f"Search page failed: {e}")
    total = data.get("total", 0)
    if total == 0:
        # 软 miss：「页面没有这段文字」是可操作信息（LLM 可滚动 / 换 tab / 接受其不存在），
        # 不是工具失败 —— 对齐 browser-use 与兄弟 _action_find_text（均返回 extracted_content 而非 error）。
        msg = f"No matches for '{query}'"
        logger.info(msg)
        return ActionResult(extracted_content=msg, long_term_memory=msg)
    formatted = _format_search_results(data, query)
    memory = f'Searched page for "{query}": {total} match{"es" if total != 1 else ""} found.'
    logger.info(memory)
    return ActionResult(extracted_content=formatted, long_term_memory=memory)
```
> 硬 / 软分流与 `_action_find_text` 完全同构。hit 的 `long_term_memory` 用紧凑摘要（差异 §4），miss 用等值回显（对齐 `find_text` miss 行为）。

### 1.4 `_format_search_results` helper（`actions.py` 模块级，4 空格）

移植 browser-use `service.py:337-360`：
```python
def _format_search_results(data: dict, query: str) -> str:
    """Format search_page {matches,total,has_more} as LLM-readable text.

    Mirrors browser-use ``_format_search_results`` (service.py:337-360).
    Caller guarantees total > 0 (the total==0 soft-miss path is handled in
    _action_search_page).
    """
    matches = data.get("matches", [])
    total = data.get("total", 0)
    has_more = data.get("has_more", False)

    lines = [f'Found {total} match{"es" if total != 1 else ""} for "{query}" on page:', ""]
    for i, m in enumerate(matches):
        context = m.get("context", "")
        path = m.get("element_path", "")
        loc = f" (in {path})" if path else ""
        lines.append(f"[{i + 1}] {context}{loc}")
    if has_more:
        lines.append(
            f"\n... showing {len(matches)} of {total} total matches. "
            f"Increase max_results to see more."
        )
    return "\n".join(lines)
```
> 输出形如：`Found 4 matches for "$29.99" on page:` 后跟 `[1] ...<td>$29.99</td>... (in tr.product > td)` 逐条。`has_more` 时末尾提示加大 `max_results`。

### 1.5 `ACTION_DEFINITIONS["search_page"]` 描述更新（`models.py:256-260` 附近，4 空格）

把泛泛描述换成点明分工的版本，让 LLM 知道何时选 `search_page` vs `find_text`：

before（描述串）：
```python
"Search for text within the current page content"
```
after：
```python
(
    "Search page text for a pattern (like grep). Zero LLM cost, instant. "
    "Returns matches with surrounding context, element path, and a total count. "
    "Set regex=True for regex patterns; use css_scope to search within a section. "
    "Read-only — does not scroll or highlight (use find_text for that)."
)
```
> 仅改描述串（`ACTION_DEFINITIONS["search_page"] = (SearchPageParams, <描述>, False)` 的第二项），不动注册机制。`terminates_sequence` 保持 `False`。

### 1.6 新增 `tests/test_search_page.py`（TAB 缩进，对齐 `tests/test_find_text.py`）

镜像 `test_find_text.py` 的 mock 工厂 + `Tools().execute("search_page", {...}, browser)` 模式，分三层：

- **动作层（`_action_search_page`）**——`_make_browser()` 设 `browser.search_page = AsyncMock(...)`、`browser.get_state = AsyncMock()`（后者用于断言「不拉全量状态」）：
  - hit：`search_page` 返回 `{matches:[...], total:4, has_more:False}` → `extracted_content` 含 `Found 4 matches` 与 `[1] ...`；`long_term_memory == 'Searched page for "X": 4 matches found.'`；
  - 单数：`total=1` → `long_term_memory` 用 `1 match`（无 `es`）；
  - 软 miss：`search_page` 返回 `{matches:[], total:0}` → `extracted_content == long_term_memory == "No matches for 'X'"`，且 `error is None`；
  - 硬错误：`search_page` 抛 `RuntimeError("search_page: Invalid regex pattern: ...")` → `ActionResult(error="Search page failed: ...")`，`extracted_content is None`；
  - 不调 `get_state`（确定性检索不应触发整页 DOM 拉取）；
  - 参数透传：传 `regex=True, case_sensitive=True, css_scope="div#main", max_results=5, context_chars=80` → 断言 `search_page` 以这些 kwarg 被调用（默认值正确性）。
- **参数层（`SearchPageParams`）**：接受 `query` + 全默认；拒绝空 `query`（`min_length=1`，`ValidationError`）；拒绝 extra 字段；`max_results=0` 与 `max_results=201` 被拒（`ge=1, le=200`）；`context_chars=-1` 被拒（`ge=0`）；`regex` / `case_sensitive` 接受布尔；`css_scope=None` 接受。
- **会话层（`BrowserSession.search_page`）**——`BrowserSession.__new__` 构造，`client.send.Runtime.evaluate = AsyncMock(...)`：
  - 正常：返回 `{result:{value:{matches:[...], total:2, has_more:False}}}` → `search_page` 返回该 dict；断言 `Runtime.evaluate` 入参 `returnByValue=True, awaitPromise=True`，且 `expression` 含 `var PATTERN = "X";`（验证 `json.dumps` 注入，而非裸 f-string 拼接）；
  - JS 层 error：返回 `{result:{value:{error:"Invalid regex pattern: ..."}}}` → `search_page` 抛 `RuntimeError` 且消息含 `search_page:`；
  - `exceptionDetails`：返回 `{exceptionDetails:{text:"SyntaxError"}}` → `search_page` 抛 `RuntimeError("JS error: ...")`（走 `execute_js`）；
  - null 返回：`{result:{value:null}}` → 抛 `RuntimeError("search_page returned no result")`；
  - `css_scope=None` → expression 含 `var CSS_SCOPE = null;`（验证 None 序列化）。
- **helper 层（`_format_search_results`）**：`total=0` 分支（理论上 action 层已拦截，但 helper 仍输出 `Found 0 matches`）；多匹配 + `has_more=True` → 末尾含 `showing K of N total matches`；`element_path` 非空 → 含 `(in ...)`；`element_path` 空 → 无 `(in ...)`。
- 异步测试逐个标 `@pytest.mark.asyncio`（项目无全局 `asyncio_mode`，对齐 `test_find_text.py`）。

### 1.7 阶段一文件清单

| 文件 | 改动 | 锚点 |
|---|---|---|
| `src/tree_walker/tools/models.py` | `SearchPageParams` 加 5 字段 + `min_length`；更新 `ACTION_DEFINITIONS` 描述串 | `:176-178` / `:256-260`，**4 空格** |
| `src/tree_walker/browser/session.py` | 新增 `_SEARCH_PAGE_JS_BODY` + `_build_search_page_js` + `BrowserSession.search_page` | 紧邻 `execute_js:1809` / `find_text:1825`，**4 空格** |
| `src/tree_walker/tools/actions.py` | 重写 `_action_search_page`（薄编排 + 分级错误）；新增模块级 `_format_search_results` | `:1184-1199`，**4 空格** |
| `tests/test_search_page.py` | 新建（动作层 / 参数层 / 会话层 / helper 层） | **TAB** 缩进 |
| `docs/Tools技术细节/04_动作清单与CDP映射.md` | 更新 4.18 节（算法改 TreeWalker+正则、参数表、行号修正 `session.py:1809`、删 `window.find()` 过时注释） | §4.18 |

### 1.8 阶段一测试计划

```powershell
uv run python -m pytest tests/test_search_page.py -x -v
uv run python -m pytest tests/ -x -v
uv run python -m pytest tests/test_search_page.py --cov=tree_walker.tools.actions --cov=tree_walker.browser.session --cov=tree_walker.tools.models --cov-report=term-missing
```

---

## 阶段二（可选，独立，对齐 / 超越 browser-use 完整能力）

阶段一交付「grep 式检索 + 封装 + 分级错误 + 测试」；以下按需再开，不在阶段一交付：

- **iframe / 同源 shadow DOM 遍历**：当前 TreeWalker 仅走顶层 `document.body` 文本节点。要覆盖 iframe 需 `Page.getFrameTree` 遍历子 frame、对各 frame 上下文分别 `Runtime.evaluate`；shadow DOM 需 `Element.shadowRoot` 递归穿透。browser-use 自己也没做（`default_action_watchdog.py:2685` 有 `TODO: handle looking for text inside cross-origin iframes`）。
- **`nth` / offset 分页游标**：browser-use 同样只靠 `max_results` 截断 + `has_more` 提示，无游标。若需「翻到第 26–50 条」，可加 `offset` 参数（`DOM.getSearchResults` 的 `fromIndex/toIndex` 思路，但本项目走 `Runtime.evaluate`，需在 JS 里 `matches.slice(offset, offset+max_results)`）。
- **可选「滚动到首条匹配」开关**：加 `scroll_to_first: bool=False`，命中后对首条匹配的 `parentElement` 调 `scrollIntoView({block:'center'})`。会与 `find_text` 职责重叠，倾向保持 `search_page` 纯只读、滚动交给 `find_text`；仅在确有「既要看匹配清单又要落到首条」的场景才开。
- **结果体积分级落盘**：超长匹配清单（如 `max_results=200` 且每条上下文 150 字符）可能撑上下文。可仿 `extract` 阶段二的 ≥10000 字符落盘 + `include_extracted_content_only_once`（需先建轻量文件输出约定）。
- **markdown / 属性值检索**：当前只检索文本节点；若需搜属性值（`<a href>`、`<input value>`）或跨 inline 元素的文本，可补一类「属性检索」模式（browser-use 的 `find_text` 有 `xpath-attr` 查询覆盖属性，`search_page` 没有）。

---

## 风险与回归点

| 风险 | 影响 | 缓解 |
|---|---|---|
| 用户值经 `json.dumps` 注入 | 安全（杜绝引号 / 元字符注入），但需确认 `json.dumps` 对含 `\n` / Unicode 的 query 产出合法 JS 字面量 | `json.dumps` 默认 `ensure_ascii=True` 产出 ASCII 安全串；JS 字符串接受 `\uXXXX`；会话层测试覆盖含特殊字符的 query |
| 依赖 `returnByValue=True` 序列化 dict | 极大 `matches` 数组序列化开销 / CDP 体积上限 | `max_results ≤ 200`（Pydantic `le=200`）封顶；`has_more` 提示加大上限而非无限返回 |
| 正则合法性只能运行时检测 | 非法 regex 在 JS `new RegExp` 抛 → JS 层 catch → `{error:...}` → session 抛 → action 硬错误 | 已设计为硬错误 `ActionResult(error="Search page failed: search_page: Invalid regex pattern: ...")`；会话层 + 动作层测试覆盖 |
| 零宽匹配（如 `.*`、`(?:)`） | 无保护会 `exec` 死循环 | JS body 含 `if (match[0].length === 0) re.lastIndex++;`（移植 browser-use） |
| 仅顶层文档、不进 iframe / shadow DOM | 跨 frame 文本 / shadow DOM 内文本搜不到 | 与 browser-use 一致，不回归；描述 + 文档诚实标注；穿透属阶段二 |
| `long_term_memory` 用摘要而非全量 | 与 `find_text` 的等值回显约定不完全一致 | hit 取摘要省 token、miss 取等值回显对齐 `find_text` miss；差异 §4 已论证；动作层测试分别断言 hit 摘要 / miss 等值 |
| 改 `SearchPageParams` 加字段 | 旧调用方只传 `query` | 所有新字段有默认值，向后兼容；`query` 名不变 |
| `css_scope` 未命中 | browser-use 设计为硬错误（`querySelector` 返回 null） | JS 层返回 `{error:'CSS scope selector not found: ...'}` → session 抛 → action 硬错误；会话层测试覆盖 |

---

## 验证方法

1. **单测全绿 + 覆盖率 ≥85%**（命令见 1.8）。
2. **会话层冒烟**（需真实浏览器 ws）：
   ```python
   # 假设 browser 已连到一个含商品价格的页面
   data = await browser.search_page(r"\$\d+\.\d{2}", regex=True)
   assert data["total"] >= 1
   assert all("match_text" in m and "context" in m for m in data["matches"])
   ```
   确认：`returnByValue=True` 下 dict 正确回传；正则模式命中；`element_path` 非空。
3. **动作层冒烟**（经 `Tools().execute`）：
   ```python
   res = await tools.execute("search_page", {"query": "Out of stock", "css_scope": "div#main"}, browser)
   # 命中 → extracted_content 含 "Found N matches"；未命中 → "No matches for '...'"（非 error）
   ```
   确认：软 miss 非 error、硬错误带具体前缀、`long_term_memory` 正确。
4. **注入安全冒烟**：`query = '"); alert(1); //'`（含引号 / 注入试探）→ 正常返回该字面量的搜索结果，不触发 alert（证明 `json.dumps` 注入生效）。
5. **回归对照**：`v0.4.0..master` 范围内 `search_page` 此前零测试、行为极简，阶段一建立基线；全量 `tests/` 回归无破坏；特别确认 `find_text` / `evaluate` 等共用 `execute_js` 的工具不受影响（`search_page` 新增方法不改 `execute_js`）。

---

## 验收 checklist（阶段一）

- [ ] `SearchPageParams` 含 `query(min_length=1)` + `regex/case_sensitive/context_chars(ge=0)/css_scope/max_results(ge=1,le=200)`，`extra="forbid"`
- [ ] `BrowserSession.search_page(pattern, *, regex, case_sensitive, context_chars, css_scope, max_results)` 封装：`_SEARCH_PAGE_JS_BODY`（TreeWalker + 偏移索引 + `RegExp.exec` + 零宽保护 + `_getPath`）+ `_build_search_page_js`（`json.dumps` 注入 var）+ 复用 `execute_js`；JS 层 `{error:...}` / null → `RuntimeError`
- [ ] `_action_search_page` 重写：硬错误 `ActionResult(error="Search page failed: ...")` + `logger.warning`；软 miss `extracted_content == long_term_memory == "No matches for '...'"`；hit `extracted_content = _format_search_results(...)` + 摘要 `long_term_memory`；不冒泡通用 catch
- [ ] `_format_search_results` helper：`Found N match(es) for "..." on page:` + 逐条 `[i] context (in path)` + `has_more` 尾注
- [ ] `ACTION_DEFINITIONS["search_page"]` 描述更新为 grep 式 + 只读说明；`terminates_sequence=False` 不变
- [ ] `tests/test_search_page.py` 覆盖动作层（hit / 单数 / 软 miss / 硬错误 / 不调 get_state / 参数透传）+ 参数层（空 query / extra / max_results 越界 / context_chars 负 / 布尔）+ 会话层（正常 dict / JS error / exceptionDetails / null / `returnByValue` / `json.dumps` 注入断言）+ helper 层，全绿
- [ ] 全量回归 `uv run python -m pytest tests/ -x -v` 通过，覆盖率 >85%
- [ ] `docs/Tools技术细节/04_动作清单与CDP映射.md` 4.18 节同步更新（算法 TreeWalker+正则、参数表、行号修正 `session.py:1809`、删 `window.find()` 过时注释）
