# find_elements 阶段二 follow-up 方案（分波次，可实施）

> 锚定 `docs/tools-optimize/find_elements.md` **阶段一**（commit `a34a1f9`，已合并）；对齐 `search_page` **阶段二**（commit `3a95202` / issue #44 / PR #69）与 `find_text` 的 `DOM.performSearch` 链。
> 本文档把原方案 `find_elements.md`「阶段二（可选，独立，对齐 / 超越 browser-use 完整能力）」一节的 5 条占位项展开成**可逐行实施**的方案，按**风险分三波**：P2-A（低风险，镜像 search_page）→ P2-B（中风险，重写 JS body）→ P2-C（大改，独立 PR）。

---

## Context（为什么做这个改动）

阶段一把 `find_elements` 升到了 browser-use 同级的「CSS 选择器元素查询」（`querySelectorAll` + 可选 attributes + `src`/`href` 绝对 URL + children_count + 总数 + 分页 + 封装 + 分级错误 + 测试），并**诚实标注限制**：仅查顶层文档 light-DOM、不穿透 shadow DOM、不进 iframe、不回显可用于点击的稳定 id（`session.py:2907-2908`）。

阶段二就是要**逐条兑现或超越**这些限制。原 `find_elements.md` 阶段二一节列了 5 项：

1. **穿透 shadow DOM / iframe**（`>>>` 深度组合器 + 递归 `shadowRoot`，或逐 iframe evaluate）
2. **回显 `backend_node_id` / 接入 `selector_map`**（让结果可直接喂 `click` / `input_text`）
3. **回显几何 `DOMRect`** + 一并恢复更稳的 `visible` 检测
4. **`unique` / 首匹配模式**（`first_only`）
5. **大结果落盘**（命中数千节点时写文件、`extracted_content` 只回摘要）

**关键洞察：阶段二的多数能力已有现成模板，不必从零设计。**

| 阶段二项 | 直接镜像/复用源 | 状态 |
|---|---|---|
| ① 穿透 shadow / 同源 iframe + offset 分页 | `search_page` 阶段二的 `_collectText` 递归（`session.py:130-154`）+ `_origin` 标记（`:120-129`）+ offset 窗口（`:177-200`） | **search_page 已落地**（原方案写「需 search_page 阶段二同步推进」——现已就绪） |
| ⑤ 大结果落盘 | `_action_search_page` 落盘块（`actions.py:1605-1628`）+ `TruncationSettings.search_page_*`（`config.py:55-56`） | **search_page 已落地** |
| ② backend_node_id / click-by-id | `find_text` 的 `DOM.performSearch → getSearchResults → describeNode` 链（`session.py:2615-2633`、`:2729-2733`）+ `click_element(backend_node_id)`（`:1794`）+ `_visible_node_ids`（`:2632`） | **find_text 已趟通**，CSS 选择器可直接作为 `performSearch` 的 query |
| ③ 几何 + visible | `get_element_coordinates`（`session.py:1662`）、`DOMRect`（`views.py:202`）、`_is_element_occluded`（`session.py:1848`）；阶段一 §5/§6 已论证 `offsetParent` 浅检测不可信 | 复用 + JS 内补可信检测 |
| ④ first_only | browser-use 无此参数；本项是 find_elements 专属小特性 | 新增，极小 |

**决策**（已与 stakeholder 确认）：
- **覆盖全部 5 项**，按风险分三波（P2-A / P2-B / P2-C），便于分 PR 落地、逐步回归。
- **item ② 走 `DOM.performSearch` 拿真实 `backend_node_id` 路线**（非 CSS 选择器桥接），复用 find_text 已验证的 CDP 链，与现有 `selector_map`（index==backend_id，`serializer.py:724`）同构；改动面大，**独立成期（P2-C，单独 PR）**。

**预期结果**：阶段二全部落地后，`find_elements` 从「仅顶层 light-DOM、结果只能给 LLM 看」升级为「穿透 shadow / 同源 iframe、带 offset 分页与落盘、带可信几何/可见性、可回稳定 id 直连点击」——对齐并局部超越 browser-use。

---

## 工程约束（实施时务必遵守）

- Windows + PowerShell；包用 `uv`，跑脚本 / 测试用 `uv run python ...`。测试命令 `uv run python -m pytest tests/test_find_elements.py -x -v`（单文件）/ `uv run python -m pytest tests/ -x -v`（全量回归）。
- **缩进按文件**（已复核，与阶段一一致）：`src/tree_walker/**` = **4 空格**；`tests/test_find_elements.py` = **TAB**。下文 before/after 代码片段均按目标文件缩进给出；JS body 在 `r"""..."""` 内沿用 4 空格。
- `json` 在 `session.py` 已是模块级 import；`logger`（`logging`）已存在；`Field` / `ConfigDict` 已在 `models.py` import；`os` / `time` 已在 `actions.py` import（`_action_search_page:1610-1611` 已用）。新增代码无需新 import。
- 每波改完跑 `tests/test_find_elements.py -x -v` + 全量回归；覆盖率目标 >85%；**不主动 `git commit` / `git push`**。
- 阶段二对 `_FIND_ELEMENTS_JS_BODY` 的重写会改变现有「顶层文档」行为，需同步更新现有假设该限制的用例（见各波测试计划）。

---

## 与 search_page 阶段二 / find_text 的对齐关系

1. **item①⑤逐行镜像 `search_page`**：`search_page` 阶段二（`3a95202`）与本工具同属「读侧确定性 CDP 查询」，落盘块、offset 窗口、shadow/iframe 递归三段可**几乎原样搬运**——仅把「文字检索」语义换成「结构选择器」语义。
2. **item②复用 `find_text` 的 performSearch 链**：`find_text` 已证明 `DOM.performSearch(query)` 接受 XPath；CDP 规范中该接口**同时接受 CSS 选择器**，故 find_elements 的 `selector` 可直接作为 query，无需转换。`getSearchResults → describeNode → backendNodeId` 与 `_visible_node_ids` 全部现成。
3. **`selector_map` 不改、不依赖**：现有 `selector_map`（`views.py:622`，index==backend_id，**仅交互元素**）保持不变；item②通过给 `click`/`input_text` 增 `element_id` 参数**直连** `click_element(backend_id)`，**绕过** selector_map（因为 performSearch 命中的非交互元素本就不在 map 里）。这是「接入 selector_map 体系」而非「写入 selector_map」。
4. **有意不照搬 browser-use 的两处**：browser-use 的 find_elements 同样不穿透 shadow/iframe（`service.py:257` 注释）——本方案 item①是**超越**；browser-use 无 first_only / 无几何回显——本方案 item③④是**补强**。

---

## Wave P2-A：低风险，逐行镜像 search_page（item⑤ 落盘 + item④ first_only）

> 风险最低，几乎零新逻辑，可直接镜像 `search_page` 阶段二已验证代码。建议作为**第一个 PR**。

### A.1 大结果落盘（item⑤）

镜像 `_action_search_page:1605-1628` + `TruncationSettings.search_page_*`（`config.py:55-56`）。

**(a) `config.py` `TruncationSettings` 增两字段**（紧邻 `search_page_*:55-56`，4 空格）：

before（`config.py:55-56`）：
```python
    search_page_save_threshold: int = 10000  # search_page result >= this → write to file (mirrors extract)
    search_page_output_dir: str = "search_page_output"  # dir for oversized match lists (env-config)
```

after：
```python
    search_page_save_threshold: int = 10000  # search_page result >= this → write to file (mirrors extract)
    search_page_output_dir: str = "search_page_output"  # dir for oversized match lists (env-config)
    find_elements_save_threshold: int = 10000  # find_elements result >= this → write to file (mirrors search_page)
    find_elements_output_dir: str = "find_elements_output"  # dir for oversized element lists (env-config)
```

**(b) `actions.py` `_action_find_elements`（`:1019-1049`）在 `formatted = _format_find_results(...)` 后插镜像块**，4 空格：

before（`:1046-1049`）：
```python
        formatted = _format_find_results(data, selector)
        memory = f'Found {total} element{"s" if total != 1 else ""} matching "{selector}".'
        logger.info(memory)
        return ActionResult(extracted_content=formatted, long_term_memory=memory)
```

after：
```python
        formatted = _format_find_results(data, selector)
        # 大结果分级落盘（镜像 _action_search_page / _action_extract；OSError 不失败只 warning）
        tr = self._truncation
        saved_to = None
        if len(formatted) >= tr.find_elements_save_threshold:
            try:
                os.makedirs(tr.find_elements_output_dir, exist_ok=True)
                fpath = os.path.join(tr.find_elements_output_dir, f"find_elements_{int(time.time() * 1000)}.txt")
                with open(fpath, "w", encoding="utf-8", newline="") as f:
                    f.write(formatted)
                saved_to = fpath
            except OSError as e:
                logger.warning("find_elements: save to file failed: %s", e)
        if saved_to:
            visible = (f"Find results ({len(formatted)} chars) saved to {saved_to}. "
                       f"Preview: {formatted[:200]}...").strip()
        else:
            visible = formatted
        memory = f'Found {total} element{"s" if total != 1 else ""} matching "{selector}".'
        if saved_to:
            memory += f" Results saved: {saved_to}"
        logger.info(memory)
        return ActionResult(extracted_content=visible, long_term_memory=memory)
```

> 与 `_action_search_page:1605-1628` 逐行对齐：阈值判定 → `os.makedirs(exist_ok=True)` → 时间戳文件名 → `OSError` 仅 warning → `visible` = 预览 + 路径、`memory += " Results saved: ..."`。

**(c) 测试**（`tests/test_find_elements.py` `TestFindElementsAction`，TAB 缩进，镜像 `tests/test_search_page.py`）：
- `test_oversized_result_saved_to_file`：构造 `total/showing` 使 `_format_find_results` 输出 ≥ 阈值；patch `tools._truncation.find_elements_save_threshold` 为小值（如 10）+ monkeypatch `os.makedirs`/`open` 或用 `tmp_path` 改 `find_elements_output_dir`；断言 `extracted_content` 含 `"saved to"`、`long_term_memory` 含 `"Results saved:"`。
- `test_small_result_not_saved`：默认阈值（10000）下小结果不落盘，`extracted_content == formatted` 全量。
- `test_save_oserror_falls_back_to_inline`：patch `os.makedirs` 抛 `OSError` → 不失败、回退 inline、`logger.warning` 命中。

### A.2 first_only 首匹配模式（item④）

**(a) `models.py` `FindElementsParams`（`:130-144`）增字段**，4 空格：

after（在 `include_text` 后追加）：
```python
    include_text: bool = Field(default=True, description="Include text content of each element")
    first_only: bool = Field(
        default=False,
        description="Return only the first matching element; total still reports the full count so you know there are more.",
    )
```

**(b) `_FIND_ELEMENTS_JS_BODY`（`session.py:281-324`）改 limit 行 + builder 注入**：

before（`:290`）：
```python
        var total = elements.length;
        var limit = Math.min(total, MAX_RESULTS);
```

after：
```python
        var total = elements.length;
        var limit = FIRST_ONLY ? Math.min(total, 1) : Math.min(total, MAX_RESULTS);
```

> `total` 仍回全量命中数（保留「还有更多」语义，footer 的 `Showing 1 of N` 会提示）；`first_only` 只是封顶到 1。

`_build_find_elements_js`（`:327-347`）builder 增 var + 形参：

after：
```python
def _build_find_elements_js(
    selector: str,
    attributes: list[str] | None,
    max_results: int,
    include_text: bool,
    first_only: bool,
) -> str:
    ...
    params_js = (
        f"var SELECTOR = {json.dumps(selector)};\n"
        f"var ATTRIBUTES = {json.dumps(attributes)};\n"
        f"var MAX_RESULTS = {json.dumps(max_results)};\n"
        f"var INCLUDE_TEXT = {json.dumps(include_text)};\n"
        f"var FIRST_ONLY = {json.dumps(first_only)};\n"
    )
    return "(function() {\n" + params_js + _FIND_ELEMENTS_JS_BODY + "\n})()"
```

**(c) session / action 转发**：

`BrowserSession.find_elements`（`:2885-2916`）签名增 `first_only: bool = False`，透传给 builder。
`_action_find_elements`（`:1019-1049`）增 `first_only = params.get("first_only", False)` 并作为 kwarg 传入。

**(d) 测试**：
- `TestFindElementsAction.test_first_only_caps_at_one`：`total=5`，传 `first_only=True` → mock session 返回 `{total:5, showing:1}` → 命中 `Found 5 elements` + footer；并断言 `browser.find_elements(..., first_only=True)` 被调用。
- `TestFindElementsAction.test_first_only_zero_is_soft_miss`：`first_only=True` 且无命中 → 软回显 `No elements found ...`。
- `TestFindElementsParams.test_first_only_default_false` / `test_accepts_first_only_true`。
- `TestBuildFindElementsJs.test_first_only_injected_as_var`（镜像现有 `test_builder_injects_via_json_dumps`）。

### P2-A 文件清单

| 文件 | 改动 | 锚点 |
|---|---|---|
| `src/tree_walker/config.py` | `TruncationSettings` +`find_elements_save_threshold` / `+find_elements_output_dir` | 紧邻 `:55-56` |
| `src/tree_walker/tools/models.py` | `FindElementsParams` +`first_only` | `:130-144` |
| `src/tree_walker/browser/session.py` | `_FIND_ELEMENTS_JS_BODY` limit 行；`_build_find_elements_js` +`first_only`；`find_elements` +`first_only` | `:290` / `:327-347` / `:2885-2916` |
| `src/tree_walker/tools/actions.py` | `_action_find_elements` 落盘块 + `first_only` 转发 | `:1019-1049` |
| `tests/test_find_elements.py` | +落盘 3 例 + first_only 5 例（TAB） | 对齐 `tests/test_search_page.py` |

---

## Wave P2-B：中风险（item① 穿透 shadow/iframe + offset + item③ 几何 DOMRect/visible）

> 重写 `_FIND_ELEMENTS_JS_BODY`，行为从「顶层文档」变为「穿透开放 shadow / 同源 iframe」。镜像 `search_page` 阶段二已验证的 JS 结构。建议作为**第二个 PR**（P2-A 之后）。

### B.1 穿透 shadow DOM / 同源 iframe + offset 分页（item①）

**(a) `models.py` `FindElementsParams` 增 `offset`**（对齐 `SearchPageParams.offset:285-288`），4 空格：

after（在 `max_results` 后、`include_text` 前追加；或按字段语义排在 `max_results` 旁）：
```python
    offset: int = Field(
        default=0, ge=0,
        description="0-based index of the first element to return (for paginating large result sets; total is always the full count across all roots).",
    )
```

**(b) 重写 `_FIND_ELEMENTS_JS_BODY`（`session.py:281-324`）**——搬运 search_page 的 `_origin` + 新增 `_collectAll` 递归收集器 + offset 窗口 + `matches()` 命中（4 空格，body 引用 `SELECTOR/ATTRIBUTES/MAX_RESULTS/OFFSET/INCLUDE_TEXT/FIRST_ONLY` 六个 var）：

after（整体替换 `_FIND_ELEMENTS_JS_BODY`）：
```python
_FIND_ELEMENTS_JS_BODY = r"""
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
    function _collectAll(root, out) {
        var we = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
        var el;
        while ((el = we.nextNode())) {
            out.push(el);
            // 穿透：开放 shadow root + 同源 iframe contentDocument（TreeWalker 不跨 shadow / 文档边界，需手动递归）
            if (el.shadowRoot) {
                try { _collectAll(el.shadowRoot, out); } catch (_) {}      // closed shadow: shadowRoot=null，自然跳过
            }
            if (el.tagName === 'IFRAME') {
                try {
                    var cd = el.contentDocument;                           // 同源可读；跨源抛 SecurityError → catch 跳过（阶段三）
                    if (cd && cd.body) _collectAll(cd.body, out);
                } catch (_) {}
            }
        }
    }
    try {
        var elements;
        try {
            // 选择器合法性先校验一次（invalid selector 会让下面的 matches() 抛 SyntaxError）
            document.querySelector(SELECTOR);
        } catch (e) {
            return {error: 'Invalid CSS selector: ' + (e && e.message ? e.message : e), elements: [], total: 0};
        }
        // 收集顶层文档 + 所有开放 shadow / 同源 iframe 内的元素
        var all = [];
        _collectAll(document.documentElement, all);
        var total = 0;
        var matched = [];
        for (var k = 0; k < all.length; k++) {
            if (all[k].matches(SELECTOR)) {
                if (total - OFFSET >= 0 && matched.length < (FIRST_ONLY ? 1 : MAX_RESULTS)) {
                    matched.push(all[k]);
                }
                total++;
            }
        }
        var results = [];
        for (var i = 0; i < matched.length; i++) {
            var el = matched[i];
            var item = {index: OFFSET + i, tag: el.tagName.toLowerCase(), origin: _origin(el)};
            if (INCLUDE_TEXT) {
                var text = (el.textContent || '').trim();
                item.text = text.length > 300 ? text.slice(0, 300) + '...' : text;
            }
            if (ATTRIBUTES && ATTRIBUTES.length > 0) {
                item.attrs = {};
                for (var j = 0; j < ATTRIBUTES.length; j++) {
                    var attrName = ATTRIBUTES[j];
                    var val;
                    if ((attrName === 'src' || attrName === 'href')
                        && typeof el[attrName] === 'string' && el[attrName] !== '') {
                        val = el[attrName];        // 绝对 URL
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
        return {
            elements: results,
            total: total,
            showing: results.length,
            offset: OFFSET,
            has_more: (OFFSET + results.length) < total
        };
    } catch (e) {
        return {error: 'find_elements error: ' + (e && e.message ? e.message : e), elements: [], total: 0};
    }
"""
```

> 关键点：
> - **`_origin` / `_collectAll` 与 search_page `_collectText:130-154` 同构**，仅把「收集文本」换成「收集元素」。
> - **选择器先 `querySelector` 校验**（沿用阶段一现状：invalid selector → `{error}` → session 翻译 `RuntimeError` → action 硬错误），避免 `matches()` 逐元素抛。
> - **offset 窗口**：累计全量 `total`，只存 `[OFFSET, OFFSET+limit)`（镜像 `_SEARCH_PAGE_JS_BODY:177-200`）；`index` 字段改成**全局序** `OFFSET+i`（便于跨页拼接），不再是 0-based。
> - **`element.matches(SELECTOR)`** 用元素自身 scope，对 shadow DOM 内元素有效；shadow root 内元素的 `getRootNode()` 返回 ShadowRoot → `_origin` 标 `(in shadow DOM)`。
> - **closed shadow / 跨源 iframe** 自然跳过（`shadowRoot=null` / `contentDocument` 抛 SecurityError 被 catch），文档与 search_page 一致标注「阶段三再做跨源」。

**(c) builder / session / action 串 offset**：

`_build_find_elements_js` 增形参 `offset: int` + `f"var OFFSET = {json.dumps(offset)};\n"`（与 A.2 的 `first_only` 一并加，签名最终为 `(selector, attributes, max_results, include_text, first_only, offset)`）。
`BrowserSession.find_elements`（`:2885-2916`）增 `offset: int = 0`；docstring 删掉「does not pierce shadow DOM or traverse into iframes」限制句、改为「pierces open shadow roots + same-origin iframes (closed shadow / cross-origin iframe skipped)」。
`_action_find_elements` 增 `offset = params.get("offset", 0)` 转发。

**(d) `_format_find_results`（`actions.py:115-150`）加 origin + offset-aware 尾注**（4 空格）：

before（`:127-149` 元素循环 + 尾注）：
```python
    for el in elements:
        idx = el.get("index", 0)
        tag = el.get("tag", "?")
        text = el.get("text", "")
        attrs = el.get("attrs", {})
        children = el.get("children_count", 0)

        parts = [f"[{idx}] <{tag}>"]
        ...
        parts.append(f"({children} children)")
        lines.append(" ".join(parts))

    if showing < total:
        lines.append(
            f"\nShowing {showing} of {total} total elements. Increase max_results to see more."
        )
    return "\n".join(lines)
```

after（元素行尾追加 `origin`；尾注改 offset-aware，镜像 `_format_search_results:95-100`）：
```python
    offset = data.get("offset", 0)
    has_more = data.get("has_more", False)
    for el in elements:
        idx = el.get("index", 0)
        tag = el.get("tag", "?")
        text = el.get("text", "")
        attrs = el.get("attrs", {})
        children = el.get("children_count", 0)
        origin = el.get("origin", "")

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
        if origin:
            parts.append(origin.strip())          # ' (in shadow DOM)' → 'in shadow DOM'
        lines.append(" ".join(parts))

    if has_more:
        next_offset = offset + len(elements)
        lines.append(
            f"\n... showing {offset + 1}–{offset + len(elements)} of {total} total elements. "
            f"Call again with offset={next_offset} for the next batch (or raise max_results)."
        )
    return "\n".join(lines)
```

> `has_more` 由 session 返回（替代原 `showing < total` 本地推断），与 search_page 尾注措辞统一。`showing` 字段保留但尾注不再单独引用它（仍可用于其它断言）。

**(e) 测试**（`tests/test_find_elements.py`，TAB，镜像 `tests/test_search_page.py` 的 `TestSearchPageJsBody` / `TestFormatSearchResults`）：
- 更新现有假设「顶层文档」/ `index` 0-based 的用例（如 `test_builder_injects_via_json_dumps` 增 `offset`/`first_only` var 断言；formatter 用例改用 `has_more`）。
- `TestFindElementsJsBody`（新增 class）：`test_recurses_open_shadow_root`、`test_recurses_same_origin_iframe`、`test_skips_cross_origin_iframe`（contentDocument 抛 SecurityError 不中断）、`test_origin_tagging_helper`、`test_collect_all_helper`、`test_offset_window`、`test_selector_validated_before_match`（invalid selector → `{error}`）。
- `TestFormatFindResults`：`test_offset_footer_with_offset`、`test_origin_appended_when_present`、`test_origin_omitted_when_empty`。
- `TestFindElementsParams`：`test_offset_negative_rejected`、`test_offset_default_zero`。
- `TestFindElementsSession`：`test_offset_forwarded_into_expression`。

### B.2 几何 DOMRect + 恢复可信 visible（item③）

**(a) `models.py` `FindElementsParams` 增 `include_geometry`**（4 空格）：
```python
    include_geometry: bool = Field(
        default=False,
        description="Add per-element getBoundingClientRect() {x,y,w,h} and a stable visibility flag "
        "(checks ancestor display/visibility/opacity + non-zero size). Default off to avoid overhead.",
    )
```

**(b) `_FIND_ELEMENTS_JS_BODY` 增 `_isVisible` helper + 取值块**（在 `_collectAll` 之后、主 `try` 之前加 helper；元素提取循环内加块；body 新增引用 `INCLUDE_GEOMETRY` var）：
```javascript
    function _isVisible(el) {
        // 可信可见性：祖先链 display/visibility/opacity + 自身非零尺寸（修复阶段一 offsetParent 浅检测）
        var node = el;
        while (node && node.nodeType === 1) {
            var cs = document.defaultView.getComputedStyle(node);
            if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') {
                return false;
            }
            node = node.parentElement;
        }
        var r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    }
```

元素提取循环内（`item.children_count = ...` 后）追加：
```javascript
            if (INCLUDE_GEOMETRY) {
                var rect = el.getBoundingClientRect();
                item.rect = {x: rect.left, y: rect.top, w: rect.width, h: rect.height};
                item.visible = _isVisible(el);
            }
```

> `include_geometry=True` 时**恢复**阶段一被删的 `visible` 字段，但用可信实现（祖先链 + 非零尺寸），正面回应阶段一 §5/§6 对 `offsetParent !== null` 的批评。默认 False 避免给「只数数」的常见场景加开销。

**(c) `_format_find_results` 几何渲染**（4 空格，在 origin 之前插）：
```python
        rect = el.get("rect")
        if rect:
            vis = "visible" if el.get("visible") else "hidden"
            parts.append(f"({vis}, {int(rect['w'])}x{int(rect['h'])}@{int(rect['x'])},{int(rect['y'])})")
```

**(d) 测试**：
- `test_geometry_rect_shape`（`include_geometry=True` → 元素含 `rect`/`visible`）。
- `TestFindElementsJsBody.test_visible_ancestor_display_none_is_false` / `test_hidden_when_zero_size` / `test_visible_when_shown`。
- `TestFormatFindResults.test_geometry_rendered` / `test_no_geometry_omits_segments`（默认不渲染）。
- `TestFindElementsParams.test_include_geometry_default_false`。
- session：`test_include_geometry_forwarded_into_expression`。

### P2-B 文件清单

| 文件 | 改动 | 锚点 |
|---|---|---|
| `src/tree_walker/tools/models.py` | `FindElementsParams` +`offset` +`include_geometry` | `:130-144` |
| `src/tree_walker/browser/session.py` | 重写 `_FIND_ELEMENTS_JS_BODY`（+`_origin`/`_collectAll`/`_isVisible`）；builder +`offset`/`include_geometry`；`find_elements` +两参 + docstring 去限制 | `:281-324` / `:327-347` / `:2885-2916` |
| `src/tree_walker/tools/actions.py` | `_format_find_results` +origin/offset 尾注/几何；`_action_find_elements` 转发 offset/geometry | `:115-150` / `:1019-1049` |
| `tests/test_find_elements.py` | 更新「顶层文档」旧用例 + 新增 shadow/iframe/offset/geometry ~18 例（TAB） | 对齐 `tests/test_search_page.py` |

---

## Wave P2-C：大改，独立成期（item② backend_node_id / click-by-id，performSearch 路线）

> **风险最高、改动面跨工具**（动 click / input_text action），原方案标注「独立成期」。建议作为**第三个 PR**（P2-A/P2-B 之后），单独评审 + 重点回归。

> **实现备注（落地时对本节的修正）**：原设计写「element_id **绕过** selector_map」，但深读 `_action_click`/`_action_input_text` 后发现它们全程依赖 `entry`（file-input 守卫、下拉降级、autocomplete 检测、值赋值方式），绕过会丢失这些守卫。因本系统 `index === backend_id`（`serializer.py:724`），实际落地的更优方案是：`click`/`input_text` 增 `element_id`（index 的别名，二者互斥），**经同一条 selector_map 解析路径**（保留全部守卫），仅是命名上的自文档化。`find_elements_node_ids` 回的 backend_id 对交互元素天然在 selector_map 内，可被解析。详见 `docs/Tools技术细节/04_动作清单与CDP映射.md` §4.7。

### 目标

让 `find_elements` 命中元素能**直接喂** `click` / `input_text`（按稳定 id 操作），不再依赖 `get_state` 快照里**只含交互元素**的 `selector_map`（`views.py:622`、`serializer.py:724`）。

### 技术路线：DOM.performSearch 拿真实 backend_node_id（复用 find_text 链）

**(a) 新 session 方法 `find_elements_node_ids`**（紧邻 `find_elements:2885` / `find_text:2594`，4 空格）：

```python
    async def find_elements_node_ids(
        self,
        selector: str,
        *,
        max_results: int = 50,
        offset: int = 0,
        include_user_agent_shadow: bool = True,
        visible_only: bool = False,
    ) -> dict:
        """Resolve CSS-selected elements to stable backendNodeIds via DOM.performSearch.

        Reuses the find_text chain (DOM.performSearch → getSearchResults →
        describeNode → backendNodeId; session.py:2615-2633, 2729-2733). CDP's
        performSearch accepts a CSS selector directly as `query` (same as
        XPath), so `selector` needs no conversion. Returns
        ``{node_ids, total, showing, offset, has_more}`` where each node_id is
        a backendNodeId usable directly as click/input_text ``element_id``.

        Heavier than find_elements (one describeNode round-trip per element);
        use only when stable ids are needed for subsequent interaction.
        """
        sid = self.current_session_id
        search = await self.client.send.DOM.performSearch(
            {"query": selector, "includeUserAgentShadowDOM": include_user_agent_shadow},
            session_id=sid,
        )
        search_id = search.get("searchId")
        total = search.get("resultCount", 0)
        try:
            if total <= 0:
                return {"node_ids": [], "total": 0, "showing": 0, "offset": offset, "has_more": False}
            to_index = min(total, offset + max_results)
            results = await self.client.send.DOM.getSearchResults(
                {"searchId": search_id, "fromIndex": offset, "toIndex": to_index},
                session_id=sid,
            )
            node_ids = results.get("nodeIds", [])
            if visible_only:
                node_ids = await self._visible_node_ids(node_ids, sid)   # 复用 find_text 的可见性过滤
            backend_ids: list[int] = []
            for nid in node_ids:
                desc = await self.client.send.DOM.describeNode({"nodeId": nid}, session_id=sid)
                bid = desc.get("node", {}).get("backendNodeId")
                if bid is not None:
                    backend_ids.append(bid)
            return {
                "node_ids": backend_ids,
                "total": total,
                "showing": len(backend_ids),
                "offset": offset,
                "has_more": (offset + len(backend_ids)) < total,
            }
        finally:
            try:
                await self.client.send.DOM.discardSearchResults({"searchId": search_id}, session_id=sid)
            except Exception as e:  # 清理失败不影响结果
                logger.debug("find_elements_node_ids: discardSearchResults failed: %s", e)
```

> 与 `find_text:2615-2633` / `:2729-2733` 同构：`performSearch`（query=CSS 选择器）→ `getSearchResults`（offset 窗口）→ 逐 `nodeId` `describeNode` 取 `backendNodeId`；`finally` 里 `discardSearchResults`（镜像 find_text 的 finally 清理）。`_visible_node_ids`（`:2632`）按需复用。

**(b) `FindElementsParams` 增 `return_node_ids`**（4 空格）：
```python
    return_node_ids: bool = Field(
        default=False,
        description="Return stable backend node ids usable directly as click/input_text `element_id` "
        "(uses DOM.performSearch — heavier, one CDP round-trip per element). "
        "Offsets apply to the document-order match list.",
    )
```

**(c) `_action_find_elements` 分流**（`:1019-1049`）：`return_node_ids=True` 时改调 `browser.find_elements_node_ids(...)`，回显每个 `backend_node_id`（如 `[0] backendNodeId=12345 <tag> "text"`）+ total/has_more；格式化函数新增 `_format_node_id_results` 或在 `_format_find_results` 内分支。

**(d) `click` / `input_text` 增 `element_id`（index 的替代，绕过 selector_map）**：

- `ClickParams` / `InputTextParams`（`models.py`）增 `element_id: int | None = Field(default=None, description="Stable backend node id from find_elements(return_node_ids=True). Takes precedence over index; bypasses the interactive-only selector_map.")`。
- `_get_element_by_index`（`actions.py:294-309`）/ `_action_click`（`:415-454`）/ `_action_input_text` 增分支：
  - 校验 `element_id` 与 `index` 互斥（两者都给或都不给 → error）。
  - `element_id` 给定时**直连** `browser.click_element(element_id)`（`session.py:1794`）/ input 的 backend setter（`:3302` / `:3320`），**不查 selector_map**（performSearch 命中的非交互元素本就不在 map 里）。

**(e) 测试**：
- session：`test_performsearch_returns_backend_ids`（mock `DOM.performSearch/getSearchResults/describeNode` → backend 列表）、`test_describe_node_yields_backend_id`、`test_offset_window`、`test_discards_search_id_in_finally`、`test_zero_results_short_circuits`、`test_visible_only_filters`。
- action：`test_return_node_ids_echoes_backend_ids`、`test_click_by_element_id_bypasses_selector_map`、`test_click_element_id_and_index_mutually_exclusive`、`test_input_text_by_element_id`。
- `TestFindElementsParams.test_return_node_ids_default_false`。

### P2-C 文件清单

| 文件 | 改动 | 锚点 |
|---|---|---|
| `src/tree_walker/browser/session.py` | 新增 `find_elements_node_ids` | 紧邻 `find_elements:2885` |
| `src/tree_walker/tools/models.py` | `FindElementsParams` +`return_node_ids`；`ClickParams`/`InputTextParams` +`element_id` | `:130-144` 等 |
| `src/tree_walker/tools/actions.py` | `_action_find_elements` node_ids 分流；`_get_element_by_index`/`_action_click`/`_action_input_text` +`element_id` 分支 + 互斥校验 | `:1019-1049` / `:294-309` / `:415-454` |
| `tests/test_find_elements.py` + `tests/test_click.py`（如存在）/ input 相关 | node_ids + click-by-id 用例（TAB） | — |

---

## 风险与回归点

| 风险 | 影响 | 波次 | 缓解 |
|---|---|---|---|
| 落盘阈值/目录配置 | 改默认值影响所有 find_elements 调用 | P2-A | 默认 10000（对齐 search_page/extract）；env-config 不受 LLM 控制；测试覆盖阈值边界 |
| `first_only` 改 limit 语义 | total 仍全量，footer 行为变化 | P2-A | footer 已有 `Showing 1 of N`；测试 `test_first_only_caps_at_one` 覆盖 |
| 重写 JS body 改「顶层文档」→「穿透」 | 现有 `index` 0-based 假设、旧测试断言失效 | P2-B | 同步更新旧用例；`index` 改全局序 `OFFSET+i` 文档化；新增 shadow/iframe 专项测试 |
| `matches(SELECTOR)` 在 shadow 内语义差异 | 个别复杂选择器命中集合与 `querySelectorAll` 顶层略有不同 | P2-B | 先 `querySelector` 校验合法性；测试覆盖 shadow/iframe 命中；文档标注 closed shadow / 跨源 iframe 跳过 |
| 跨源 iframe 抛 SecurityError | 若未 catch 会中断整次查询 | P2-B | `_collectAll` 内 `try/catch` 跳过（对齐 search_page）；测试 `test_skips_cross_origin_iframe` |
| `_isVisible` 祖先链遍历开销 | `include_geometry=True` 时每元素一次 computed style 链查 | P2-B | 默认 False；仅在需要几何/可见性时开启；文档注明 |
| performSearch 的 CSS 语义 vs querySelectorAll | 伪类 / `:scope` / 复杂组合可能命中不同 | P2-C | 文档标注差异；`return_node_ids` 默认 False（opt-in）；与 JS 路径并存，非替换 |
| describeNode 逐节点 N 次 CDP 往返 | max_results=50 时 50 次往返，延迟上升 | P2-C | opt-in 模式；文档注明可后续用 `DOM.resolveNode` 批量化优化 |
| click/input_text 加 `element_id` 改动跨工具 | 回归面波及所有点击/输入路径 | P2-C | 严格 `element_id`/`index` 互斥校验；`element_id` 缺省时走原 index 路径（零行为变化）；重点回归 `tests/test_click.py` 等 |
| performSearch 命中元素不在 selector_map | 直接查 map 会 KeyError | P2-C | `element_id` 路径**绕过** selector_map，直连 `click_element(backend_id)`；测试 `test_click_by_element_id_bypasses_selector_map` |

---

## 验证方法

1. **单测全绿 + 覆盖率 ≥85%**：`uv run python -m pytest tests/test_find_elements.py -x -v` + `--cov=tree_walker.tools.actions --cov=tree_walker.browser.session --cov-report=term-missing`。
2. **全量回归**：`uv run python -m pytest tests/ -x -v`——P2-B 重点确认 `test_search_page.py`（共享 `_origin`/`_collectAll` 思路）无 regression；P2-C 重点确认 `test_click.py` / input 相关无 regression。
3. **会话层冒烟**（mock CDP 或接真实浏览器）：
   ```python
   # uv run python -c "..."
   data = await browser.find_elements("a", attributes=["href"], offset=0, max_results=5)
   assert {"elements", "total", "showing", "offset", "has_more"} <= data.keys()
   ```
4. **穿透冒烟**（P2-B）：构造含 `<iframe srcdoc>` + `el.attachShadow({mode:'open'})` 的页面，断言 shadow / iframe 内元素被命中且 `origin` 标注正确。
5. **node_ids 冒烟**（P2-C）：`data = await browser.find_elements_node_ids("button")` → 用返回的 `backend_id` 直接 `await browser.click_element(backend_id)` 成功。
6. **落盘冒烟**（P2-A）：构造超阈值结果，确认 `find_elements_output/` 生成时间戳文件、`extracted_content` 为预览。

---

## 验收 checklist（按波次）

### P2-A（低风险）
- [ ] `config.py` `TruncationSettings` +`find_elements_save_threshold` / `+find_elements_output_dir`
- [ ] `_action_find_elements` 落盘块落地（镜像 search_page）
- [ ] `FindElementsParams` +`first_only`；JS limit 行 + builder/session/action 转发
- [ ] 落盘 3 例 + first_only 5 例测试全绿
- [ ] 全量 `pytest tests/ -x -v` 无 regression

### P2-B（中风险）
- [ ] `FindElementsParams` +`offset` / +`include_geometry`
- [ ] `_FIND_ELEMENTS_JS_BODY` 重写（`_origin` + `_collectAll` + `_isVisible` + offset 窗口）
- [ ] `find_elements` docstring 去掉「不穿透」限制句
- [ ] `_format_find_results` +origin / offset 尾注 / 几何渲染
- [ ] 更新旧「顶层文档」用例 + 新增 shadow/iframe/offset/geometry ~18 例全绿
- [ ] 全量回归无 regression

### P2-C（大改，独立 PR）
- [ ] 新 session 方法 `find_elements_node_ids`（复用 performSearch 链 + finally discard）
- [ ] `FindElementsParams` +`return_node_ids`
- [ ] `ClickParams` / `InputTextParams` +`element_id`（与 index 互斥）
- [ ] `_get_element_by_index` / `_action_click` / `_action_input_text` element_id 分支绕过 selector_map
- [ ] node_ids + click-by-id + 互斥校验测试全绿
- [ ] click / input 全量回归无 regression

### 通用
- [ ] 缩进按文件：src = 4 空格、tests = TAB
- [ ] 覆盖率 >85%
- [ ] `docs/Tools技术细节/04_动作清单与CDP映射.md` §4.7 同步（穿透 / offset / 落盘 / 几何 / node_ids / element_id）
