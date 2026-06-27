# search_page 阶段二 follow-up 方案（offset 分页 + 同源 iframe / 开放 shadow DOM 遍历 + 属性检索 + 大结果落盘）

> 前置：阶段一已落地（commit `e8e2aed`：grep 式全文检索 + `BrowserSession.search_page` 封装 + 错误分级 + 单测）。本文针对 `search_page.md`「阶段二」未实现项（`:398-407`）给出可实施方案，结构与粒度对齐阶段一章节。
> 决策（已确认）：iframe / shadow DOM 取「**同源 iframe + 开放 shadow DOM，纯 JS，仍单次 `Runtime.evaluate`**」，跨源 iframe 列阶段三不做；**不引入 `scroll_to_first`**（维持纯只读，滚动交给 `find_text`）。
> 故本文覆盖 4 项，分三个独立 PR：**A** = offset 分页 + 大结果落盘（低风险，先做）；**B** = 同源 iframe / 开放 shadow DOM 遍历（中等）；**C** = 属性检索（可选，价值最低）。
> 同族先例：`extract` 阶段二（commit `586cd79`，落盘 + 分页）；`find_text` 的 `DOM.performSearch(includeUserAgentShadowDOM=True)` 与 `xpath-attr`（`session.py:84-86`、`:2550`）。

---

## 适用场景（阶段二补什么）

阶段一交付「确定性 grep 式检索 + 上下文 + 元素路径 + 总数」。阶段二补四类缺口：

1. **翻页**：阶段一靠 `max_results` 截断 + `has_more` 提示，第 26 条以后不可达。`offset` 让 LLM 翻到任意区间。
2. **大结果不撑上下文**：`max_results=200` 且每条 150 字符上下文可达 3 万字符。落盘镜像 `extract`。
3. **进 iframe / shadow DOM**：阶段一仅顶层 `document.body` 文本节点；很多现代组件（Web Components / 同源 iframe 内嵌文档）的文本搜不到。开放 shadow + 同源 iframe 全在 JS 内递归完成，仍单次 evaluate。
4. **搜属性值**：`<a href>` / `<input value>` / `data-*` 这类不在文本节点里的值，阶段一搜不到；`search_attributes` 补一类可选模式。

---

## 工程约束（实施时务必遵守）

- Windows + PowerShell；包用 `uv`，跑脚本 / 测试用 `uv run python ...`。测试命令 `uv run python -m pytest tests/ -x -v`。
- **缩进按文件**（已复核）：`actions.py` / `models.py` / `browser/session.py` / `config.py` = **4 空格**；`tests/test_search_page.py` = **TAB**（对齐阶段一）。下文代码片段按目标文件缩进给出。
- **新增 import 无需**：`actions.py` 已 import `os`/`time`/`TruncationSettings`（`:10-11,16`）；`session.py` 已 import `json`/`logger`/`Any`；`models.py` 已 import `Field`/`ConfigDict`；`config.py` 已 import `dataclass`/`os`。
- **向后兼容**：所有新 param 有默认值；JS 返回 dict 仅**新增可选 key**（`offset` / `attribute_matches` / `attribute_total`），`matches` / `total` / `has_more` 语义在 `offset=0`、`search_attributes=False` 时与阶段一**完全等价**（见「与 browser-use 差异 §1」论证）→ 阶段一既有测试不破坏。
- 改完跑 `tests/test_search_page.py` + 全量回归；覆盖率 >85%。
- 不主动 `git commit` / `git push`。

---

## 与 browser-use 的关键差异（阶段二）

1. **仍单次 `Runtime.evaluate`，同源 iframe + 开放 shadow DOM 全在 JS 内递归完成，不动 CDP。** browser-use 的 `search_page` 两者都不做（其 `default_action_watchdog.py:2685` 有 cross-origin iframe TODO）→ 本方案**超越** browser-use。递归用 `el.shadowRoot`（开放根可达，closed 不可达，与 `DOM.performSearch` 一致）+ `iframe.contentDocument`（同源可读，跨源抛 `SecurityError` 被 `catch` 跳过）。
2. **`offset` 翻页用「累计全部 total + 只存窗口区间」而非「收集全部再切片」。** 保持阶段一 early-bail 的性能特性：exec 循环仍 O(命中数)，只是 `matches.push` 的窗口从 `[0, MAX_RESULTS)` 变成 `[OFFSET, OFFSET+MAX_RESULTS)`；`has_more` 语义改为「当前页之后还有更多」——`OFFSET=0` 时与阶段一 `total > MAX_RESULTS` 完全等价（论证见 §A.2）。
3. **`offset` 回显进结果 dict，formatter 从 dict 读，不改 helper 签名。** JS 返回 `offset: OFFSET`，`_format_search_results(data, query)` 用 `data.get("offset", 0)`，零侵入。
4. **大结果落盘镜像 `extract`，不引入 `include_extracted_content_only_once`。** 项目 `ActionResult` 无此字段（`dropdown_options.md:168` / `select_dropdown.md:300` 已论证不引入）；沿用 `extract` 的「`len >= threshold` → 写文件 → 返回 preview + 路径」+ `ActionResult.__str__` 500 字截断兜底。
5. **不引入 `scroll_to_first`。** 维持阶段一立的「search_page 纯只读、滚动 + 高亮是 `find_text` 职责」原则。
6. **属性检索为 search_page 新增可选模式（browser-use search_page 无）。** 输出独立 `attribute_matches` 列表，不混入文本 `matches`（避免污染 `offset` 语义）；用非全局 `RegExp` 副本做 `.test`，规避全局正则 `lastIndex` 漂移。
7. **诚实标注限制：closed shadow root 不可达、跨源 iframe 不穿透（阶段三）。** 不回归也不夸大。

---

## 阶段二.A：`offset` 分页游标 + 大结果分级落盘（低风险，建议先做，可独立 PR）

### A.1 `SearchPageParams` 加 `offset`（`models.py:268-284`，4 空格）

在 `max_results` 字段后追加：
```python
    offset: int = Field(
        default=0, ge=0,
        description="0-based index of the first match to return (for paginating large result sets; total is always the full count).",
    )
```
> `ge=0` 编译期校验；默认 0 = 阶段一行为。`Field` 已 import。

### A.2 `_SEARCH_PAGE_JS_BODY` 支持 offset 窗口 + 回传 offset（`session.py:100-176`，4 空格；JS 缩进保持现状 4 空格）

把匹配循环的 `if (matches.length < MAX_RESULTS)` 改成 offset 窗口，并在返回 dict 回显 `offset`、重定义 `has_more`：

before（`:146-172`）：
```javascript
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
            if (match[0].length === 0) re.lastIndex++;
        }
        return {matches: matches, total: total, has_more: total > MAX_RESULTS};
```
after：
```javascript
        var matches = [];
        var total = 0;
        var match;
        while ((match = re.exec(fullText)) !== null) {
            total++;
            // offset 窗口：累计全部 total，只存 [OFFSET, OFFSET+MAX_RESULTS) 区间（保持 early-bail 性能）
            if (total - 1 >= OFFSET && matches.length < MAX_RESULTS) {
                var start = Math.max(0, match.index - CONTEXT_CHARS);
                var end = Math.min(fullText.length, match.index + match[0].length + CONTEXT_CHARS);
                var context = fullText.slice(start, end);
                var elementPath = '';
                for (var i = 0; i < nodeOffsets.length; i++) {
                    var no = nodeOffsets[i];
                    if (no.offset <= match.index && no.offset + no.length > match.index) {
                        elementPath = _getPath(no.node.parentElement) + _origin(no.node);
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
            if (match[0].length === 0) re.lastIndex++;
        }
        return {
            matches: matches,
            total: total,
            offset: OFFSET,
            has_more: (OFFSET + matches.length) < total
        };
```
> **`OFFSET=0` 等价性论证**：`total ≤ MAX_RESULTS` 时 `matches.length=total` → `has_more=(0+total)<total=false`，旧 `total>MAX_RESULTS=false` ✓；`total > MAX_RESULTS` 时 `matches.length=MAX_RESULTS` → `has_more=MAX_RESULTS<total=true`，旧 `=true` ✓。故阶段一既有 session/formatter 测试（基于 mock dict，不跑 JS）不受影响，真实 JS 行为在 `offset=0` 时不变。
> `_origin(no.node)` 见 §B.2（B 阶段加入；A 阶段若先合并，可先不加 `_origin`，仅改 offset 窗口 + 回传 offset + 新 has_more）。

### A.3 `_build_search_page_js` 注入 `OFFSET`（`session.py:179-204`，4 空格）

before（签名 + `params_js`）：
```python
def _build_search_page_js(
    pattern: str,
    regex: bool,
    case_sensitive: bool,
    context_chars: int,
    css_scope: str | None,
    max_results: int,
) -> str:
    ...
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
after：签名加 `offset: int`，`params_js` 追加 `var OFFSET`：
```python
def _build_search_page_js(
    pattern: str,
    regex: bool,
    case_sensitive: bool,
    context_chars: int,
    css_scope: str | None,
    max_results: int,
    offset: int,
) -> str:
    ...
    params_js = (
        f"var PATTERN = {json.dumps(pattern)};\n"
        f"var IS_REGEX = {json.dumps(regex)};\n"
        f"var CASE_SENSITIVE = {json.dumps(case_sensitive)};\n"
        f"var CONTEXT_CHARS = {json.dumps(context_chars)};\n"
        f"var CSS_SCOPE = {json.dumps(css_scope)};\n"
        f"var MAX_RESULTS = {json.dumps(max_results)};\n"
        f"var OFFSET = {json.dumps(offset)};\n"
    )
    return "(function() {\n" + params_js + _SEARCH_PAGE_JS_BODY + "\n})()"
```

### A.4 `BrowserSession.search_page` 透传 `offset`（`session.py:2773-2807`，4 空格）

签名加 `offset: int = 0` 关键字参数，传入 builder。docstring 的 Limitations 段不动（A 阶段仍是顶层文档）：
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
        offset: int = 0,
    ) -> dict:
        ...
        js = _build_search_page_js(
            pattern, regex, case_sensitive, context_chars, css_scope, max_results, offset,
        )
        ...
```

### A.5 `_action_search_page` 透传 `offset` + 大结果落盘（`actions.py:1560-1589`，4 空格）

镜像 `_action_extract` 落盘块（`actions.py:843-854` + `:856-870`）。`offset=` 透传在 PR A；`search_attributes=` 透传在 PR C（此处先不写）。

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
                offset=params.get("offset", 0),
            )
        except Exception as e:
            logger.warning("search_page(%r) failed: %s", query, e)
            return ActionResult(error=f"Search page failed: {e}")
        total = data.get("total", 0)
        if total == 0:
            msg = f"No matches for '{query}'"
            logger.info(msg)
            return ActionResult(extracted_content=msg, long_term_memory=msg)
        formatted = _format_search_results(data, query)
        # 大结果分级落盘（镜像 _action_extract actions.py:843-854；OSError 不失败只 warning）
        tr = self._truncation
        saved_to = None
        if len(formatted) >= tr.search_page_save_threshold:
            try:
                os.makedirs(tr.search_page_output_dir, exist_ok=True)
                fpath = os.path.join(tr.search_page_output_dir, f"search_page_{int(time.time() * 1000)}.txt")
                with open(fpath, "w", encoding="utf-8", newline="") as f:
                    f.write(formatted)
                saved_to = fpath
            except OSError as e:
                logger.warning("search_page: save to file failed: %s", e)
        if saved_to:
            visible = (f"Search results ({len(formatted)} chars) saved to {saved_to}. "
                       f"Preview: {formatted[:200]}...").strip()
        else:
            visible = formatted
        memory = f'Searched page for "{query}": {total} match{"es" if total != 1 else ""} found.'
        if saved_to:
            memory += f" Results saved: {saved_to}"
        logger.info(memory)
        return ActionResult(extracted_content=visible, long_term_memory=memory)
```
> 落盘仅命中路径（`formatted` 非空）；软 miss / 硬错误路径不受影响。`preview` 取前 200 字符防 `ActionResult.__str__` 500 字截断丢路径。

### A.6 `_format_search_results` 分页页脚（`actions.py:75-97`，4 空格）

从 `data` 读 `offset`（JS 回显），把 `has_more` 页脚改成带页码区间 + 下一页 `offset` 提示：

before（`:82-97`）：
```python
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
after：
```python
    matches = data.get("matches", [])
    total = data.get("total", 0)
    has_more = data.get("has_more", False)
    offset = data.get("offset", 0)

    lines = [f'Found {total} match{"es" if total != 1 else ""} for "{query}" on page:', ""]
    for i, m in enumerate(matches):
        context = m.get("context", "")
        path = m.get("element_path", "")
        loc = f" (in {path})" if path else ""
        lines.append(f"[{i + 1}] {context}{loc}")
    if has_more:
        next_offset = offset + len(matches)
        lines.append(
            f"\n... showing {offset + 1}–{offset + len(matches)} of {total} total matches. "
            f"Call again with offset={next_offset} for the next batch (or raise max_results)."
        )
    return "\n".join(lines)
```
> **注意**：页脚措辞变化 → 阶段一 formatter 的 `has_more` 页脚断言需同步更新（见 §A.9）。

### A.7 `TruncationSettings` 新增阈值 + output_dir + env（`config.py:42-54` / `:239-250`，4 空格）

`config.py:42-54` dataclass 末尾追加两字段：
```python
    search_page_save_threshold: int = 10000   # search_page result >= this → write to file (mirrors extract)
    search_page_output_dir: str = "search_page_output"  # dir for oversized match lists (env-config)
```
`config.py:239-250` `load_settings` 的 `TruncationSettings(...)` 内追加：
```python
            search_page_save_threshold=int(os.environ.get("AGENT_SEARCH_PAGE_SAVE_THRESHOLD", "10000")),
            search_page_output_dir=os.environ.get("AGENT_SEARCH_PAGE_OUTPUT_DIR", "search_page_output"),
```

### A.8 `ACTION_DEFINITIONS["search_page"]` 描述更新（`models.py`，4 空格）

描述串改为点明 offset 分页 + shadow/iframe 覆盖（B 落地后）+ 属性检索（C 落地后）；A 阶段先加 offset 一句：
```python
(
    "Search page text for a pattern (like grep). Zero LLM cost, instant. "
    "Returns matches with surrounding context, element path, and a total count. "
    "Paginate large result sets with offset. "
    "Set regex=True for regex patterns; use css_scope to search within a section. "
    "Read-only — does not scroll or highlight (use find_text for that)."
)
```
> 仅改描述串第二项；`terminates_sequence=False` 不变。B/C 落地时再补「traverses same-origin iframes / open shadow roots」「search_attributes=True to match href/value」两句。

### A.9 测试增量（`tests/test_search_page.py`，TAB 缩进）

镜像阶段一三层，新增：
- **参数层**：`offset` 接受 `0`+；`offset=-1` 被拒（`ge=0`，`ValidationError`）。
- **builder 层**：`_build_search_page_js(..., offset=10)` 产出的 expression 含 `var OFFSET = 10;`（验证 `json.dumps` 注入）；默认 `offset=0` 含 `var OFFSET = 0;`。
- **会话层**：`search_page(..., offset=25)` 调用 → 断言 `Runtime.evaluate` 入参 `expression` 含 `var OFFSET = 25;`。
- **动作层**：`offset` 作为 kwarg 透传给 `browser.search_page`（断言 call kwargs）；落盘——设 `tools._truncation = TruncationSettings(search_page_save_threshold=10, search_page_output_dir=str(tmp_path))`（对齐 `tests/test_extract.py:234` 姿势），mock `search_page` 返回足够多匹配使 `formatted ≥ 10` 字符 → 断言文件落盘（`tmp_path` 下出现 `search_page_*.txt`）、`extracted_content` 以 `Search results ... saved to` 开头且含路径、`long_term_memory` 含 `Results saved:`；阈值调高（如 `10_000_000`）→ 内联返回、无落盘。
- **formatter 层**：构造 `{matches:[2 项], total:30, offset:0, has_more:True}` → 页脚含 `showing 1–2 of 30` + `offset=2`；`{...,offset:25,has_more:False}` → 无页脚；**更新**阶段一既有 `has_more` 页脚断言为新措辞。

---

## 阶段二.B：同源 iframe + 开放 shadow DOM 遍历（中等，独立 PR）

### B.1 `_SEARCH_PAGE_JS_BODY` 递归采集（`session.py:100-176`）

把阶段一的单 `TreeWalker(SHOW_TEXT)` 块（`:124-134`）替换为递归采集器：每个根（document / shadowRoot / iframe contentDocument）内先采文本节点，再遍历其元素，对 `el.shadowRoot`（开放根）与同源 `el.contentDocument` 递归。函数声明紧邻 `_getPath`（IIFE 内 function 声明 hoist；`nodeOffsets`/`fullText` 为函数级 `var`，调用前已赋值）。

新增 `_origin` + `_collectText` 两个函数声明，并替换采集块：
```javascript
    function _origin(node) {
        // 标记非顶层文档来源：ShadowRoot(nodeType=11) → shadow DOM；其它(getRootNode≠document) → iframe
        try {
            var r = node.getRootNode ? node.getRootNode() : null;
            if (r && r !== document) {
                return r.nodeType === 11 ? ' (in shadow DOM)' : ' (in iframe)';
            }
        } catch (_) {}
        return '';
    }
    function _collectText(root) {
        var wt = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        var n;
        while ((n = wt.nextNode())) {
            var t = n.textContent;
            if (t && t.trim()) {
                nodeOffsets.push({offset: fullText.length, length: t.length, node: n});
                fullText += t;
            }
        }
        // 穿透：开放 shadow root + 同源 iframe contentDocument（TreeWalker 不跨 shadow / 文档边界，需手动递归）
        var we = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
        var el;
        while ((el = we.nextNode())) {
            if (el.shadowRoot) {
                try { _collectText(el.shadowRoot); } catch (_) {}      // closed shadow: shadowRoot=null，自然跳过
            }
            if (el.tagName === 'IFRAME') {
                try {
                    var cd = el.contentDocument;                       // 同源可读；跨源抛 SecurityError → catch 跳过（阶段三）
                    if (cd && cd.body) _collectText(cd.body);
                } catch (_) {}
            }
        }
    }
```
并把 `try` 块内原采集段：
```javascript
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
```
替换为：
```javascript
        var fullText = '';
        var nodeOffsets = [];
        _collectText(scope);
```
> 无 shadow / iframe 时 `_collectText(scope)` 行为与阶段一逐字等价（先采文本节点，元素遍历无 shadowRoot/iframe 命中），仅多一次元素遍历的开销（可忽略）。

### B.2 `_origin` 标记接入 `element_path`（`session.py` 匹配循环）

匹配循环里 `elementPath = _getPath(no.node.parentElement) + _origin(no.node);`（A.2 已含；若 A 先合并未带 `_origin`，B 阶段补上）。属性检索段同样 `_getPath(el) + _origin(el)`（见 §C.2）。

### B.3 文档 + 描述更新

- `BrowserSession.search_page` docstring 的 Limitations 段（`session.py:2796-2797`）改为：「traverses same-origin iframes and open shadow roots; closed shadow roots and cross-origin iframes are not pierced (cross-origin → Phase 3).」
- `ACTION_DEFINITIONS["search_page"]` 描述补一句：「Traverses same-origin iframes and open shadow roots.」（§A.8 预留位）
- `docs/Tools技术细节/04_动作清单与CDP映射.md` §4.18：删「仅顶层文档」措辞，注明覆盖同源 iframe + 开放 shadow DOM、closed/跨源限制。

### B.4 测试增量（`tests/test_search_page.py`，TAB）

- **builder/JS 内容断言**（无需浏览器）：`_SEARCH_PAGE_JS_BODY` / `_build_search_page_js` 产出含 `el.shadowRoot`、`contentDocument`、`_collectText`、`_origin`（grep 字符串）。
- **浏览器冒烟**（见「验证方法」，真实 ws）：含开放 shadow root + 同源 iframe 的测试页 → 命中两者内文本、`total` 含之、`element_path` 带 `(in shadow DOM)` / `(in iframe)`。
- 诚实标注：纯 JS 执行无法单测（无浏览器），靠 JS 字符串断言 + 冒烟覆盖。

---

## 阶段二.C：属性值检索（可选，价值最低，独立 PR）

### C.1 `SearchPageParams` 加 `search_attributes`（`models.py`，4 空格）
```python
    search_attributes: bool = Field(
        default=False,
        description="Also search element attribute values (href / value / data-* etc). Returns a separate attribute_matches list; offset applies to text matches only.",
    )
```

### C.2 `_SEARCH_PAGE_JS_BODY` 属性采集（`session.py`，匹配循环后、return 前）

用**非全局** `RegExp` 副本（`re.source` 复用已转义/解析的源）做 `.test`，规避全局正则 `lastIndex` 漂移；输出独立 `attribute_matches` / `attribute_total`：
```javascript
        var attribute_matches = [];
        var attribute_total = 0;
        if (SEARCH_ATTRIBUTES) {
            var reAttr = new RegExp(re.source, CASE_SENSITIVE ? '' : 'i');
            var we = document.createTreeWalker(scope, NodeFilter.SHOW_ELEMENT);
            var el;
            while ((el = we.nextNode())) {
                var attrs = el.attributes;
                if (!attrs) continue;
                for (var a = 0; a < attrs.length; a++) {
                    var av = attrs[a].value;
                    if (av && reAttr.test(av)) {
                        attribute_total++;
                        if (attribute_matches.length < MAX_RESULTS) {
                            attribute_matches.push({
                                attribute: attrs[a].name,
                                value: av,
                                element_path: _getPath(el) + _origin(el)
                            });
                        }
                    }
                }
            }
        }
        return {
            matches: matches,
            total: total,
            offset: OFFSET,
            has_more: (OFFSET + matches.length) < total,
            attribute_matches: attribute_matches,
            attribute_total: attribute_total
        };
```
> `SEARCH_ATTRIBUTES=false` 时 `attribute_matches=[]`、`attribute_total=0` → 阶段一 mock 测试不读这俩 key，不受影响。
> `re` 此时必已定义（非法 regex 在 `:143-145` 提前 return error）。

### C.3 `_build_search_page_js` 注入 `SEARCH_ATTRIBUTES`（`session.py`）

签名加 `search_attributes: bool`，`params_js` 追加 `f"var SEARCH_ATTRIBUTES = {json.dumps(search_attributes)};\n"`（对齐 §A.3 OFFSET 注入姿势）。

### C.4 `BrowserSession.search_page` 透传（`session.py:2773-2807`）

签名加 `search_attributes: bool = False`，传入 builder。

### C.5 `_action_search_page` 透传 + 属性感知的软 miss（`actions.py:1560-1589`）

- 透传：`search_attributes=params.get("search_attributes", False)` 加入 `browser.search_page(...)` 调用。
- 软 miss 条件从 `total == 0` 改为 `total == 0 and not data.get("attribute_total")`（有属性命中不算 miss）。

### C.6 `_format_search_results` 属性段（`actions.py:75-97`）

在文本 matches 循环后、`has_more` 页脚前插入：
```python
    attr_matches = data.get("attribute_matches") or []
    attr_total = data.get("attribute_total", 0)
    if attr_total:
        lines.append("")
        lines.append(f'Attribute matches for "{query}" ({attr_total}):')
        for i, m in enumerate(attr_matches):
            path = m.get("element_path", "")
            loc = f" (in {path})" if path else ""
            lines.append(f"[{i + 1}] @{m.get('attribute', '')}={m.get('value', '')}{loc}")
        if attr_total > len(attr_matches):
            lines.append(f"... showing {len(attr_matches)} of {attr_total} attribute matches.")
```

### C.7 测试增量（`tests/test_search_page.py`，TAB）

- 参数层：`search_attributes` 布尔接受。
- builder 层：`var SEARCH_ATTRIBUTES = true;` 注入断言。
- 动作层：`search_attributes=` kwarg 透传；软 miss：`total=0` 但 `attribute_total=2` → 非 miss（`extracted_content` 含属性段）。
- formatter 层：dict 含 `attribute_matches:[{attribute:'href',value:'...x...',element_path:'a'}], attribute_total:1` → 输出含 `Attribute matches for "x" (1):` + `@href=...`。

---

## 阶段三（暂不做，仅记录）

- **跨源 iframe 文本检索**：需复用 `dom.py:537-570` 的 `_build_frame_target_map`（`Target.getTargets` 建 frameId→targetId / url→targetId）+ `_attach_to_iframe_target`（`Target.attachToTarget{flatten:true}` 取 sessionId），对每个跨源子 frame 单独 `Runtime.evaluate` 跑同一段 search JS，再在 Python 侧合并 `{matches,total,...}`、`detachFromTarget`。这会**打破阶段一「单次 evaluate」不变量**，`BrowserSession.search_page` 需改成「主文档 evaluate + 逐 frame evaluate + 合并」多步流程，工程量与回归面显著大于 A/B/C。browser-use 亦未做（`default_action_watchdog.py:2865` TODO）。仅在确有跨源 iframe 检索诉求时再开。

---

## 风险与回归点

| 风险 | 影响 | 缓解 |
|---|---|---|
| `offset` 改变 `has_more` 语义 | `OFFSET=0` 时与阶段一 `total>MAX_RESULTS` 等价（§A.2 已论证）；`OFFSET>0` 为新语义 | mock 测试不跑 JS 不受影响；formatter 页脚措辞变化 → 同步更新阶段一 formatter `has_more` 断言 |
| 递归采集穿透 shadow / iframe | 开放 shadow root 可达；closed root `shadowRoot=null` 自然跳过；跨源 iframe 抛 `SecurityError` 被 `catch` 跳过 | 与 `DOM.performSearch` 一致，不回归；`_origin` 标记 `(in shadow DOM)` / `(in iframe)` 补可读性；closed/跨源诚实标注 |
| 全局 `RegExp` 复用于属性 `.test` | `g` flag 使 `lastIndex` 漂移 → 漏匹配 | 属性段用非全局副本 `new RegExp(re.source, ...)`（§C.2） |
| 大结果落盘 | 极大 `formatted` 写文件开销 / 磁盘 | `search_page_save_threshold` 默认 10000 + `max_results ≤ 200` 双封顶；`OSError` 不失败只 warning；preview 200 字防 `__str__` 500 字截断丢路径 |
| `_getPath` 对 shadow / iframe 内节点 partial | 父链到 shadow root / iframe document 边界为止 | `_origin` 标记补足来源信息；路径本就是 best-effort |
| `json.dumps` 注入新 var（`OFFSET`/`SEARCH_ATTRIBUTES`） | 均为 int/bool，安全 | 已是项目约定（`_find_text_js_fallback`）；builder 测试断言注入字面量 |
| 结果 dict 新增可选 key | 旧消费方只读 `matches/total/has_more` | 全用 `.get(...)` 默认；阶段一 mock 测试不读新 key |

---

## 验证方法

1. **单测全绿 + 覆盖率 ≥85%**：
   ```powershell
   uv run python -m pytest tests/test_search_page.py -x -v
   uv run python -m pytest tests/ -x -v
   uv run python -m pytest tests/test_search_page.py --cov=tree_walker.tools.actions --cov=tree_walker.browser.session --cov=tree_walker.tools.models --cov-report=term-missing
   ```
2. **builder / JS 内容断言**（无需浏览器）：`_build_search_page_js` 产出含 `var OFFSET = N;` / `var SEARCH_ATTRIBUTES = ...;` / `el.shadowRoot` / `contentDocument` / `_collectText` / `_origin`。
3. **浏览器冒烟**（需真实 ws）：
   ```python
   # 含开放 shadow root + 同源 iframe 的测试页（两者内各埋一段目标文本）
   data = await browser.search_page("target", css_scope=None)
   assert data["total"] >= 2
   assert any("(in shadow DOM)" in m["element_path"] for m in data["matches"])
   assert any("(in iframe)" in m["element_path"] for m in data["matches"])
   # offset 分页
   p1 = await browser.search_page("x", max_results=5, offset=0)
   p2 = await browser.search_page("x", max_results=5, offset=5)
   assert p1["has_more"] and len(p1["matches"]) == 5
   # 属性检索
   da = await browser.search_page("example.com", search_attributes=True)
   assert da["attribute_total"] >= 1
   ```
4. **回归对照**：`v0.5.0..master` 阶段一已建 search_page 测试基线；全量回归不破坏；`find_text` / `evaluate` 共用 `execute_js` 不受影响（search_page 不改 `execute_js`）。

---

## 验收 checklist

**阶段二.A**
- [ ] `SearchPageParams` 含 `offset(ge=0)`；`TruncationSettings` 含 `search_page_save_threshold` / `search_page_output_dir` + env
- [ ] JS 匹配循环 offset 窗口 + 回传 `offset` + 新 `has_more`（`OFFSET=0` 等价性已论证）
- [ ] `_build_search_page_js` / `search_page` / `_action_search_page` 透传 `offset`
- [ ] `_action_search_page` 大结果落盘（镜像 extract：`len>=threshold` → 写 `search_page_<ts>.txt` → preview + 路径；OSError warning）
- [ ] `_format_search_results` 分页页脚（`showing A–B of N ... offset=B`）；阶段一 has_more 断言已更新
- [ ] 测试：参数 / builder / 会话 / 动作（含落盘）/ formatter，全绿

**阶段二.B**
- [ ] `_SEARCH_PAGE_JS_BODY` 递归 `_collectText`（开放 shadowRoot + 同源 contentDocument）+ `_origin` 标记
- [ ] docstring / 描述 / `04_动作清单与CDP映射.md` §4.18 更新覆盖范围与限制
- [ ] 测试：JS 内容断言 + 浏览器冒烟（shadow / iframe 命中 + origin 标记）

**阶段二.C**
- [ ] `SearchPageParams.search_attributes`；JS 属性采集（非全局 RegExp 副本）+ `attribute_matches` / `attribute_total`
- [ ] builder / session / action（含属性感知软 miss）/ formatter 透传与渲染
- [ ] 测试：参数 / builder / 动作 / formatter，全绿

**通用**
- [ ] 全量 `uv run python -m pytest tests/ -x -v` 通过，覆盖率 >85%
- [ ] 向后兼容：`offset=0` / `search_attributes=False` 时行为与阶段一逐字等价，阶段一测试不破坏
