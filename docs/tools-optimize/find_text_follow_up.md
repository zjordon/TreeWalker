# find_text P1 进阶能力落地方案

> 本文是 [`find_text.md`](./find_text.md) 「P1 增强 —— 进阶能力（本次仅画蓝图）」一节的**实施细化**。P0（核心改进）已 shipped（`session.py` 的 `find_text`/`_find_text_js_fallback`/`_highlight_search_node`/`_xpath_string_literal`、`actions.py:904-929` 的 `_action_find_text`、`models.py:111-113` 的 `FindTextParams`、`tests/test_find_text.py`），但蓝图里的 G8–G11 **全部未实现**。本文给出四个能力的**可落地设计**——把蓝图悬而未决的问题逐个定调：G8 的 searchId 生命周期、G9 的成本与判据、G10「丑但标准」的 translate、G11 的可选性与 best-effort 语义。
>
> 本文为**提案文档（不改源码）**，给出统一签名、分级改进方案（G8–G11）与可直接参照的代码片段 / 测试矩阵，供后续实施直接照做。

---

## 1. 背景与目标

参照对象：

- **P0 已落地代码**（本方案的基底）：
  - `src/tree_walker/browser/session.py:2143-2208` —— `find_text(text)`：三段 XPath（`xpath-text` / `xpath-content` / `xpath-attr`）经 `DOM.performSearch` + JS TreeWalker 兜底，硬编码 `fromIndex:0, toIndex:1` + `node_ids[0]`，`contains()` 大小写敏感，命中后 `_highlight_search_node` 走 CDP 元素框高亮。
  - `session.py:38-53` —— `_xpath_string_literal`（XPath 1.0 引号安全字面量）。
  - `session.py:2210-2238` —— `_find_text_js_fallback`（TreeWalker 兜底，`json.dumps` 注入）。
  - `session.py:2240-2256` —— `_highlight_search_node`（nodeId → backendNodeId → `highlight_element`）。
  - `src/tree_walker/tools/actions.py:904-929` —— `_action_find_text`（薄包装：成功回显 + 软「未找到」+ 异常 error）。
  - `src/tree_walker/tools/models.py:111-113` —— `FindTextParams`（仅 `text`，`extra="forbid"`）。
- **对齐基准 / 可复用模板**：
  - `search_page`（`session.py:2258-2292` + `models.py:213-229` `SearchPageParams`）—— 已有 `case_sensitive: bool = False` 管道 + `{matches, total, has_more}` 计数模式，是 G10 / G8 的**直接模板**。
  - `scroll`（`actions.py:681-699` + `session.py:1958-2027`）—— `{at_edge}` 服务端算 → action 回显，是 G9 `visible` 标志的范式。
  - CDP 调用范式（全代码库一致）：`await self.client.send.DOM.X({...}, session_id=sid)`。

**P0 留下的四个缺口（即 G8–G11）**：

1. **G8 多命中导航**：始终取首个 match，无法定位第 N 个；LLM 想「找下一个」无能为力。
2. **G9 可见性优先**：盲取 `node_ids[0]`，命中隐藏元素（`display:none`、折叠区、离屏）会让 LLM 困惑——以为滚到了却看不到。
3. **G10 大小写不敏感**：XPath `contains()` + JS `String.includes()` 均大小写敏感，与「Ctrl+F 默认不敏感」直觉相悖，与 `search_page`（默认不敏感）不一致。
4. **G11 原生选区高亮**：仅 CDP 元素框（粗），对「大容器命中」过粗；无精确蓝底文本选区。

**目标**：在 P0 基底上，用最小改动面、最大复用度补齐四能力，且**单命中默认场景零行为差异**（既有 P0 用例不回归）。

---

## 2. 现状对比（P0 → P1）

| 维度 | P0（当前） | P1（本方案） | 复用点 |
|---|---|---|---|
| 多命中 | `fromIndex:0, toIndex:1` + `node_ids[0]` | `nth` 参数 + `getSearchResults(fromIndex:0, toIndex:min(total,CAP))` 批量取回后按 nth 选 | `getSearchResults` 已支持 `fromIndex/toIndex`（`cdp_use/.../dom/commands.py:234-245`） |
| 计数回显 | 无 | 回显 `match N of V visible, T total` | `search_page` 的 `{total}` 计数范式 |
| 可见性 | 盲取首个 | 默认按可见性筛首个**可见** match（`total>1` 时 +1 次 `Runtime.evaluate`） | `scroll` 的 `{at_edge}` 服务端算→回显 |
| 大小写 | `contains()` 敏感 | `case_sensitive: bool = False`；不敏感走 XPath `translate(., LOWER, UPPER)` | `SearchPageParams.case_sensitive`（`models.py:220`）管道照搬 |
| 高亮 | 仅 CDP 元素框 | `highlight: Literal["box","selection","none"]`；`selection` 用 `window.find` 造原生选区 | `_highlight_search_node` 沿用为 `box` |
| 签名 | `find_text(text)` | `find_text(text, *, nth=1, case_sensitive=False, highlight="box")` | — |
| 返回 | `{found, method, tag}` | `{found, method, tag, match_index, visible_total, total, highlight}`（补字段，向后兼容） | — |

---

## 3. 改进方案（G8–G11，统一签名）

### 统一新签名

四个能力合并到同一签名，避免方法爆炸。session 出逻辑、action 出回显（对齐 `scroll`/`search_page`）。

```python
# session.py
async def find_text(
	self, text: str, *,
	nth: int = 1,
	case_sensitive: bool = False,
	highlight: Literal["box", "selection", "none"] = "box",
) -> dict:
	"""Find text on the page, scroll the nth visible match into view, highlight it.

	Extends P0 ``find_text(text)`` with: (G8) ``nth`` selects which match
	(stateless — re-searches each call, no cross-call session state); (G9)
	visibility-priority filtering (default on, only probes when >1 match);
	(G10) case-insensitive by default via XPath ``translate()``; (G11)
	``highlight`` mode (box/selection/none). Returns a dict; never raises on
	a clean miss or nth-out-of-range — the action layer builds the echo.
	"""
```

```python
# models.py
class FindTextParams(BaseModel):
	model_config = ConfigDict(extra="forbid")
	text: str = Field(min_length=1, description="Text to search for on the page")
	nth: int = Field(
		default=1, ge=1,
		description="Which match to scroll to, 1-based (default: 1st). The echo reports total/visible counts so the caller can increment.",
	)
	case_sensitive: bool = Field(
		default=False,
		description="Case-sensitive match (default: case-insensitive, like Ctrl+F). Aligns with search_page.",
	)
	highlight: Literal["box", "selection", "none"] = Field(
		default="box",
		description="Highlight style: box=element outline (default), selection=native blue text selection (best-effort, Chromium-only), none=off.",
	)
```

> `extra="forbid"` 要求三个新字段**必须**显式声明在 model 上（否则 LLM 传 `nth` 会被拒）。注册行 `models.py:296` 的 description 可顺带刷新为 `"Scroll to and highlight the nth visible match of text on the page"`。

---

### G10｜大小写不敏感（translate 双向小写，保留 XPath 快路径）

XPath 1.0 无原生大小写不敏感 `contains`，标准做法是 `translate(., LOWER, UPPER)` 双向转大（或小）写后比较。这样**保留 CDP XPath 快路径**（JS 仅作兜底），不必为不敏感整体降级到 JS。

新增模块常量 + 谓词构造器（needle 仍**先**经 `_xpath_string_literal` 转义，**再**包 `translate`，所以引号安全与大小写正交）：

```python
_XPATH_LOWER = "'abcdefghijklmnopqrstuvwxyz'"
_XPATH_UPPER = "'ABCDEFGHIJKLMNOPQRSTUVWXYZ'"

def _text_queries(text: str, case_sensitive: bool) -> list[tuple[str, str]]:
	"""Build the 3-query XPath chain. case_sensitive=False wraps both the
	haystack and the needle in translate(., LOWER, UPPER) so contains() is
	case-insensitive. The needle is still run through _xpath_string_literal
	first (quote-safety and case-folding are orthogonal)."""
	lit = _xpath_string_literal(text)
	needle = lit if case_sensitive else f"translate({lit}, {_XPATH_LOWER}, {_XPATH_UPPER})"
	wrap = (lambda e: e) if case_sensitive else (
		lambda e: f"translate({e}, {_XPATH_LOWER}, {_XPATH_UPPER})"
	)
	return [
		("xpath-text",    f"//*[contains({wrap('text()')}, {needle})]"),  # 直接文本子节点
		("xpath-content", f"//*[contains({wrap('.')}, {needle})]"),       # 全文本内容（覆盖分裂文本）
		("xpath-attr",    f"//*[@*[contains({wrap('.')}, {needle})]]"),   # 属性值
	]
```

`find_text` 把 P0 里硬编码的 `queries = [...]` 换成 `queries = _text_queries(text, case_sensitive)`，其余流程不动。

JS 兜底同步加 `case_sensitive` 形参，不敏感时用 `toLowerCase`（`json.dumps` 安全注入不变）：

```python
async def _find_text_js_fallback(self, text: str, case_sensitive: bool = False) -> bool:
	needle_js = json.dumps(text)
	if case_sensitive:
		cond = "t.includes(needle)"
	else:
		cond = "t.toLowerCase().includes(needle.toLowerCase())"
	js = (
		"(() => {"
		f"  const needle = {needle_js};"
		"  const walker = document.createTreeWalker("
		"    document.body, NodeFilter.SHOW_TEXT, null, false);"
		"  let node;"
		"  while ((node = walker.nextNode())) {"
		"    const t = node.nodeValue || '';"
		f"    if ({cond} && t.trim()) {{"
		"      if (node.parentElement) {"
		"        node.parentElement.scrollIntoView({ behavior: 'smooth', block: 'center' });"
		"      }"
		"      return true;"
		"    }"
		"  }"
		"  return false;"
		"})()"
	)
	try:
		return bool(await self.execute_js(js))
	except Exception as e:
		logger.debug("find_text JS fallback failed: %s", e)
		return False
```

> 默认不敏感对齐 `search_page`（`models.py:220` 同样 `default=False`）与 Ctrl+F 直觉。

---

### G8｜多命中导航（无状态 nth）

**设计抉择（已定）**：**无状态** `nth`——每次调用重新 `performSearch`，按 `fromIndex/toIndex` 取第 nth 个。searchId 不跨调用（P0 的 `finally` 已 `discardSearchResults`），无需会话状态、无需导航失效处理。LLM 据回显里的 `total`/`visible_total` 自行递增 `nth`。

**胜出查询语义不变**：优先级 `xpath-text` > `xpath-content` > `xpath-attr`，**首个 `resultCount≥1` 即胜出**；`nth` 与可见性**仅在胜出查询内**生效（不跨查询合并 match，否则 `method`/`total` 语义混乱、难以报告）。

胜出查询内的取数（与 G9 共用一条「批量 + 筛 + 取 nth」路径）：

```python
total = search.get("resultCount", 0)            # 该查询的总命中数
batch_end = min(total, _FIND_TEXT_CAP)          # CAP=50，防大结果集爆 CDP 返回
results = await self.client.send.DOM.getSearchResults(
	{"searchId": search_id, "fromIndex": 0, "toIndex": batch_end}, session_id=sid,
)
node_ids = results.get("nodeIds", [])           # 批量取回（≤CAP 个）
```

> **为何不直接 `fromIndex:nth-1, toIndex:nth`**：那样更省一次返回，但与 G9 可见性筛选冲突——可见性要求先把候选批量取回、逐个判可见、**再**按 nth 选。统一走批量路径，代码 / 测试只有一条分支。`CAP=50` 兜底：超出部分在回显注明（见 §action 回显）。

`nth` 选择规则（在 G9 可见性筛选之后）：

- `nth ≤ len(visible_ids)` → 命中 `visible_ids[nth-1]`，`match_index = nth`。
- `nth > len(visible_ids)` → 文本**在页但不够**：返回 `{found: False, reason: "nth_exceeds", ...}`，action 软回显，**不**回退下一查询（保持可预测；LLM 据回显调小 nth）。

---

### G9｜可见性优先（默认开启）

**默认开**：严格优于 P0（绝不滚到 `display:none` / 折叠区 / 离屏元素），且**仅 `total>1` 时**触发 +1 次 `Runtime.evaluate`（对齐 `find_text.md` G9 成本备注）。`total==1` 跳过探针 → **P0 单命中行为与既有测试零改动**。

判据用 `getBoundingClientRect + computedStyle`，**不用**蓝图提的 `offsetParent !== null`（后者对 `position:fixed` 误判为隐藏，而 fixed 元素其实是可见的）：

```js
// 单次 Runtime.evaluate（returnByValue），对一批元素判可见，返回 [{nodeId, visible}, ...]
const vis = (el) => {
	if (!el) return false;
	const r = el.getBoundingClientRect();
	const s = getComputedStyle(el);
	return r.width > 0 && r.height > 0
		&& s.visibility !== "hidden"
		&& s.display !== "none";
};
```

**实现路径（G9 唯一新增复杂点）**：`performSearch` 给的是前端 `nodeId`，而 `Runtime.evaluate` 的 JS 拿不到 `nodeId`。两条可行路径，文档推荐 **路径 A**：

- **路径 A（推荐）**：批量 `DOM.resolveNode({nodeId})` → 拿一组 `objectId`，再**单次** `Runtime.callFunctionOn`（或 `evaluate` 注入一个接收 objectId 数组的函数）逐个判可见，一次往返返回 `{nodeId, visible}[]`。复用项目既有的 `Runtime.callFunctionOn` 调用范式（`session.py:1299/1450/1491/2344/2388`）。
- **路径 B（备选）**：每个 nodeId 单独 `DOM.resolveNode` + `callFunctionOn` 判可见——简单但 N 次往返，`total` 大时慢。仅作后备。

> 路径 A 把「批量判可见」压成 1 次额外 CDP 往返（`resolveNode` 批量 + 1 次 `callFunctionOn`），与蓝图「+1 次 `Runtime.evaluate`」的成本量级一致。

筛选规则：

```python
if total > 1:
	vis_map = await self._probe_visibility(node_ids)   # {nodeId: bool}，路径 A
	visible_ids = [nid for nid in node_ids if vis_map.get(nid)]
	if not visible_ids:
		visible_ids = node_ids[:1]   # 全隐藏→降级取首个（回显注明「未明显可见」），不报错
else:
	visible_ids = list(node_ids)     # 单命中：跳过探针，P0 行为
```

nth 在 `visible_ids` 上取（见 G8）。`_probe_visibility` best-effort：探针异常 → `logger.debug` + 退化为「全部视为可见」（`visible_ids = node_ids`），不阻断主流程。

---

### G11｜原生选区高亮（highlight 三态）

XPath 定位 + 滚动 + 计数照常（提供 nth/可见性/total），**仅高亮**按 `highlight` 分流：

- **`box`（默认，现状）**：沿用 `_highlight_search_node`（`describeNode` → `backendNodeId` → `highlight_element` 走 Overlay 元素框）。返回 tag。
- **`selection`**：元素已滚入视口后，跑 `window.find(text, caseSensitive, false)` **循环 nth 次**，把原生选区推进到**第 nth 个**出现位置（蓝底精确文本，与 nth 对齐）。`case_sensitive` 直传 `window.find` 第二参。tag 仍由 `describeNode` 提供（回显一致）。

  ```js
  // selection 模式（json.dumps 注入 text；NTH/caseSensitive 由 Python 注入整数/布尔）
  (() => {
	  let ok = false;
	  for (let i = 0; i < NTH; i++) {
		  ok = window.find(TEXT, CASESENSITIVE, false);
		  if (!ok) break;
	  }
	  return ok;
  })()
  ```

  best-effort：`window.find` 非标准（Chromium-only，随版本漂移），失败不影响已完成的滚动 / 计数（仅无蓝底）。**注意**：选区位置与「XPath nth」在大容器 / 跨查询场景可能不精确对齐——单查询内的常见场景对齐良好；文档标注此为已知局限。
- **`none`**：跳过高亮；仍 `describeNode` 取 tag 供回显。

> G11 **重新引入** P0 刚移除的 `window.find`，但**仅作可视化选区**（非定位机制）——定位仍走可靠的三段 XPath + JS 兜底。这是 `find_text.md` G11 蓝图明确认可的「精确但非标准」的可选补充。

---

### 返回 dict（新形状，向后兼容补字段）

```python
# 命中
{"found": True, "method": "xpath-text", "tag": "p",
 "match_index": 3, "visible_total": 5, "total": 8, "highlight": "box"}
# 文本在页但 nth 超出可见数（软回显，非 error）
{"found": False, "reason": "nth_exceeds", "method": "xpath-text",
 "requested_nth": 5, "visible_total": 2, "total": 2}
# 文本不在页（P0 不变）
{"found": False, "method": "none", "tag": None}
```

---

### Action 层富回显（自适应，单命中无噪音）

`_action_find_text` 改为转发三个新参数 + 据 dict 富回显。**单命中（`total==1`）回显与 P0 完全一致**（无计数噪音），仅在多命中 / nth_exceeds / 非 box 高亮时追加信息：

```python
async def _action_find_text(self, params: dict, browser: BrowserSession) -> ActionResult:
	text = params["text"]
	nth = params.get("nth", 1)
	case_sensitive = params.get("case_sensitive", False)
	highlight = params.get("highlight", "box")
	try:
		info = await browser.find_text(
			text, nth=nth, case_sensitive=case_sensitive, highlight=highlight,
		)
	except Exception as e:
		logger.warning("find_text(%r, nth=%d) failed: %s", text, nth, e)
		return ActionResult(error=f"Find text failed: {e}")
	if not info.get("found"):
		if info.get("reason") == "nth_exceeds":
			# 文本在页但 nth 超出可见数：可操作信息（调小 nth），非工具失败
			msg = (
				f"Text '{text}' found but only {info.get('visible_total')} visible "
				f"matches ({info.get('total')} total via {info.get('method')}) "
				f"— asked for match {info.get('requested_nth')}, try a smaller nth"
			)
			logger.info(msg)
			return ActionResult(extracted_content=msg, long_term_memory=msg)
		# 文本不在页（P0 软回显不变）
		msg = f"Text '{text}' not found on page"
		logger.info(msg)
		return ActionResult(extracted_content=msg, long_term_memory=msg)
	# 命中：据 total 自适应回显
	method = info.get("method")
	tag = info.get("tag")
	total = info.get("total")
	visible_total = info.get("visible_total")
	match_index = info.get("match_index")
	if total == 1:
		# 单命中：与 P0 回显一致，无计数噪音
		memory = f"Scrolled to text '{text}' into view (via {method})"
	else:
		counts = f"match {match_index} of {visible_total} visible, {total} total"
		memory = f"Scrolled to text '{text}' into view ({counts}, via {method})"
	if tag:
		memory = memory.replace(f" via {method}", f", found in <{tag}> via {method}")
	if highlight != "box":
		memory += f" ({highlight} highlight)"
	logger.info(memory)
	return ActionResult(extracted_content=memory, long_term_memory=memory)
```

回显示例：

| 场景 | extracted_content |
|---|---|
| 单命中（默认） | `Scrolled to text 'foo' into view (found in <p>, via xpath-text)`（**同 P0**） |
| 多命中 nth=3 | `Scrolled to text 'foo' into view (match 3 of 5 visible, 8 total, found in <p>, via xpath-text)` |
| nth 超出 | `Text 'foo' found but only 2 visible matches (2 total via xpath-text) — asked for match 5, try a smaller nth`（软回显，`error is None`） |
| 未找到 | `Text 'foo' not found on page`（**同 P0**，软回显） |
| selection 高亮 | 多命中回显末尾追加 ` (selection highlight)` |

> 全程 `success=None`（`views.py:18-25` validator 禁止非 done 动作设 `success=True`）。多命中 / nth_exceeds 时 `extracted_content`（详）与 `long_term_memory`（简）可分流，对齐 `search_page`。

---

## 4. 测试计划（扩展 `tests/test_find_text.py` 四层）

> CLAUDE.md：改功能必须同步加测试、覆盖率 >85%。参照 `test_search_page.py::test_forwards_all_params_as_kwargs`（kwargs 转发）、`test_scroll.py::TestScrollParams`（参数边界）。`_make_browser` / `_make_session` 既有桩模式沿用。

| 测试类 | 用例（新增 / 改动） |
|---|---|
| `TestFindTextParams`（**翻转**现有 `test_extra_field_forbidden`：`nth` 现为合法字段） | `nth` 默认 1；接受 ≥1；拒绝 0 / 负。`case_sensitive` 默认 False、接受 True。`highlight` 默认 "box"、接受 "selection"/"none"、拒绝非法串（如 `"foo"`）。未知字段（如 `bogus=1`）仍拒（`extra="forbid"`）。 |
| `TestFindTextAction` | 新增：转发 `nth`/`case_sensitive`/`highlight` 为 kwargs（`browser.find_text.assert_awaited_once_with(text, nth=.., case_sensitive=.., highlight=..)`）；单命中回显同 P0；多命中回显含 `match N of M visible, T total`；nth_exceeds 软回显且 `error is None`。**改动**：`test_passes_text_through_verbatim` 断言更新为含 kwargs。既有其余用例保留。 |
| `TestBuildTextQueries`（新增 / 扩充自 `TestXPathStringLiteral`） | `case_sensitive=True` → 三条 query 均无 `translate`、含 `contains(text(), "Foo")`；`False` → 含 `translate(..., ${_XPATH_LOWER}, ${_XPATH_UPPER})`；needle 含引号（`He said "hi"`）时 translate 内仍正确（单引号定界 / concat 包裹，引号安全与大小写正交）。 |
| `TestFindTextSession`（`_make_session` 扩展） | ① `nth=2`、`node_ids=(42,43,44)`、全可见 → 取 43、`getSearchResults` 收到 `toIndex=3`、回显 `match_index=2`；② `case_sensitive=False` → `performSearch` 收到的 query 含 `translate(`；③ `total>1` → 可见性探针（`DOM.resolveNode` + `Runtime.callFunctionOn`）被 await、首个隐藏时跳过取首个可见；④ 全隐藏 → 降级取 `node_ids[0]`、回显含「未明显可见」语义；⑤ `highlight="none"` → `highlight_element` **未**调用；⑥ `highlight="selection"` → `window.find` JS 被 await（expression 含 `window.find`）；⑦ `nth` 超出可见数 → 返回 `{found:False, reason:"nth_exceeds", requested_nth, visible_total, total}`；⑧ `total>CAP` → `getSearchResults` 的 `toIndex=CAP`（批被截断）。⑨ **既有用例（多为 `total==1`，`node_ids=(42,)`）零改动**——G9 探针不触发，断言不动。 |

`_make_session` 扩展要点（在现有基础上加多值 / 可见性 / window.find mock）：

```python
def _make_session(
	self, *,
	result_counts=(1, 0, 0), node_ids=(42,), js_value=False,
	visible_ids=None,            # G9：可视为可见的 nodeId 集合；None=不触发探针语义
	perform_raises_at=None, eval_raises=None,
) -> tuple[BrowserSession, MagicMock]:
	# ... 既有 performSearch/getSearchResults/scrollIntoViewIfNeeded/discard/describeNode mock ...
	# G9：DOM.resolveNode({nodeId}) → {object: {objectId}}；Runtime.callFunctionOn 返回 [{nodeId, visible}]
	# G11 selection：Runtime.evaluate（window.find）返回 {result:{value:True/False}}
```

> **行为变更影响面可控**：既有 `TestFindTextSession` 用例普遍 `total==1`（`node_ids=(42,)`），G9 探针不触发，断言不动；仅新增多命中 / 可见性 / selection 用例需要相应 mock。

---

## 5. CDP 调用清单（P1 变更后，相对 P0 增量）

| 路径 | CDP 命令（P1） | 相对 P0 变化 |
|---|---|---|
| `find_text` 命中，单 match（`total==1`） | `performSearch` + `getSearchResults(0,1)` + `scrollIntoViewIfNeeded` + `discardSearchResults`(finally) + `describeNode` + `Overlay.highlightNode`(box) | **无变化**（G9 探针跳过） |
| `find_text` 命中，多 match（`total>1`） | 上述 + `DOM.resolveNode`×N + `Runtime.callFunctionOn`(批量可见性) | **+G9 批量可见性探针**（1 次往返量级） |
| `find_text` 命中，`highlight="selection"` | 上述（box 改为）+ `Runtime.evaluate`(`window.find`×nth) | **+G11 window.find 选区** |
| `find_text`，`case_sensitive=False` | query 改为 `translate()` 包裹 | `performSearch` 参数变化，命令数不变 |
| `find_text`，XPath 全空 → JS 兜底 | `performSearch`×3(空) + `Runtime.evaluate`(TreeWalker，`toLowerCase`) | JS 兜底表达式变化 |

---

## 6. 涉及文件清单（实施时）

| 文件 | 改动 | 对应改进 |
|---|---|---|
| `src/tree_walker/browser/session.py` | `find_text` 加 3 kw-only 参数 + 批量取回 + nth/可见性选择；新增 `_text_queries` / `_probe_visibility` + 模块常量 `_XPATH_LOWER/_UPPER/_FIND_TEXT_CAP`；`_find_text_js_fallback` 加 `case_sensitive`；`_highlight_search_node` 按 `highlight` 分流（含 `window.find` 选区） | G8 / G9 / G10 / G11 |
| `src/tree_walker/tools/actions.py` | `_action_find_text` 转发三参数 + 据 dict 富回显（单命中无噪音） | G8 / G9 / G11 回显 |
| `src/tree_walker/tools/models.py` | `FindTextParams` 加 `nth` / `case_sensitive` / `highlight` 三字段；刷新注册 description | G8 / G10 / G11 schema |
| `tests/test_find_text.py` | 四层扩展（翻转 nth 用例 + 新增 kwargs 转发 / 多命中 / 可见性 / selection / nth_exceeds） | 全部 |
| `docs/Tools技术细节/04_动作清单与CDP映射.md` | find_text 条目同步（nth / 可见性 / case_sensitive / highlight 三态 + CDP 链增量） | 文档同步（实施后） |

---

## 7. 实施后的验证

- 相关测试：`uv run python -m pytest tests/test_find_text.py -x -v`
- 全量回归：`uv run python -m pytest tests/ -x -v`
- 覆盖率（CLAUDE.md 目标 >85%）：`uv run python -m pytest tests/ --cov=tree_walker.tools --cov=tree_walker.browser`
- 手测：
  1. 长页面（如维基长文）多命中：`find_text {"text":"<重复词>", "nth":2}` → 回显 `match 2 of N visible, M total`，截图确认滚到第 2 个；
  2. 大小写变体：`find_text {"text":"hello"}`（页面为 `Hello`）默认命中（不敏感）；`{"text":"hello","case_sensitive":true}` 不命中（软回显）；
  3. 折叠区 / `display:none`：页面含隐藏的同文本元素 + 可见元素 → 默认滚到可见项；
  4. `find_text {"text":"<文本>","highlight":"selection"}` → 蓝底原生选区（best-effort）；
  5. nth 超出：`find_text {"text":"<仅 2 处>","nth":5}` → 软回显 `only 2 visible matches ... try a smaller nth`，`error is None`。

---

## 8. 决策备注

- **G8 无状态 nth**：searchId 不跨调用（P0 `finally` 已 `discardSearchResults`），每次重搜按 nth 索引；LLM 据 `total`/`visible_total` 回显自递增。避开会话状态（如 `_last_find_*`）+ 导航失效复杂度——`_last_file_chooser`（`session.py:658`）虽是跨调用状态先例，但本方案不采用。
- **G9 默认开 + 仅 `total>1` 触发**：严格优于 P0，单命中零开销 / 零行为差异（既有测试不回归）；判据用 `getBoundingClientRect + computedStyle`，**不用** `offsetParent`（对 `position:fixed` 误判）。
- **G10 translate 而非整体走 JS**：保留 CDP XPath 快路径（JS 仅兜底）；`translate(., LOWER, UPPER)` 是 XPath 1.0 唯一标准大小写不敏感做法；引号安全（`_xpath_string_literal`）与大小写折叠正交。
- **G11 best-effort + 可选**：`window.find` 仅作可视化选区（非定位），循环 nth 次与 nth 对齐；非标准（Chromium-only）、失败不阻断滚动 / 计数；重新引入 `window.find` 是蓝图明确认可的「精确但非标准」补充。
- **nth / 可见性仅在胜出查询内**：保留 P0 优先级语义（`xpath-text` > `content` > `attr`），`method`/`total` 报告清晰，不跨查询合并。
- **CAP=50**：批量取回防 CDP 大返回；超出在回显 / 注释注明（`total` 仍报告真实值）。
- **不设 `success=True`**（`views.py:18-25` validator）；**`terminates_sequence` 保持 `False`**（`models.py:296`）——find_text 仅滚动不改页面上下文，同 P0。
- **路径 A vs B（G9 可见性探针）**：推荐路径 A（批量 `resolveNode` + 单次 `callFunctionOn`），1 次往返量级；路径 B（逐个）仅后备。
