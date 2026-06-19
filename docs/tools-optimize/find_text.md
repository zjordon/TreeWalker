# find_text 工具完善方案

> 参照 browser-use 的 `find_text` 实现完善本项目 `find_text` 工具。
>
> 本文为**提案文档**（不改源码），给出差距分析、分级改进方案与可落地的代码片段，供后续实施直接参照。范围：**核心改进（find_text 工具本身：CDP 文本搜索替换 window.find + 成功回显 + 软「未找到」回显 + 兑现 highlight + 修复 browser-use 4 个 bug + 测试）+ P1 进阶蓝图（多命中导航 / 可见性优先 / 大小写不敏感 / 原生选区高亮）**。

---

## 0. 重要前提：browser-use 文档与实际代码不符

用户指出的 browser-use 文档（`docs/Tools技术细节/05-动作详解-浏览器交互.md` §11）描述的 CDP 链是 `DOM.enable → DOM.getDocument(pierce) → DOM.performSearch → DOM.getSearchResults → DOM.scrollIntoViewIfNeeded → DOM.discardSearchResults → Runtime.evaluate(可见性检查)`。

但**实际代码**（`browser_use/browser/watchdogs/default_action_watchdog.py:2682-2774` 的 `on_ScrollToTextEvent`）与文档**多处不符**：

- 不是单一查询，而是**三段 XPath 查询链**（顺序 fallback）；
- `Runtime.evaluate` **不是**「可见性检查」，而是三段 XPath 全部 miss 时的 **JS TreeWalker 兜底**；
- `DOM.getDocument` 算了 `root_node_id` 但**从未使用**（无用调用）；
- `discardSearchResults` 放在 `break` **之后**，命中即跳过 → **searchId 泄漏**（bug）；
- XPath 用 f-string 直接把 `event.text` 插进 `"..."`，**未转义**（text 含 `"` 即语法错误）；
- 未传 `includeUserAgentShadowDOM`（Shadow DOM 内文本搜不到）。

**本方案以 browser-use 实际代码为蓝本，并修复上述 4 个 bug。** 下文「browser-use」一律指实际代码。

---

## 1. 背景与目标

参照对象：

- **browser-use 实际代码**：`default_action_watchdog.py:2682-2774`（`on_ScrollToTextEvent`，CDP 三段 XPath + JS TreeWalker 兜底）+ `browser_use/tools/service.py:1457-1475`（`find_text` 工具包装：catch 异常 → 软回显）。
- **本项目当前**：`src/tree_walker/tools/actions.py:787-797`（`_action_find_text`，用 `window.find()`）、`src/tree_walker/browser/session.py:1467-1481`（`execute_js`）、`src/tree_walker/tools/models.py:99-101`（`FindTextParams`）、`models.py:202`（注册：`"find_text": (FindTextParams, "Scroll to and highlight text on the page", False)`）。
- **对齐基准**：`scroll`（`docs/tools-optimize/scroll.md`）/ `send_keys`（`send_keys.md`）已建立的「成功回显 + try/except 软降级 + Pydantic 校验 + 三层测试」新规范；以及本项目最相近的文本搜索动作 `search_page`（`actions.py:460-475`，无匹配走**软回显** `extracted_content`，非 error）。

**当前实现的三个问题**：

1. **不可靠**：`window.find()` 是非标准 API（Ctrl+F 等价），跨元素分裂文本、属性文本、Shadow DOM 文本都覆盖不到，行为随浏览器版本漂移。
2. **无成功回显**：命中时返回裸 `ActionResult()`，LLM 只看到 `"OK"`，无法确认滚到了什么——与 `scroll`/`click`/`send_keys`（都有 `extracted_content` + `long_term_memory`）不一致。
3. **承诺未兑现 + 语义偏差**：`models.py:202` 的 description 是 `"Scroll to and highlight text on the page"`，但当前实现既不稳定滚动也不可靠高亮；且「未找到」用硬 `error`，与本项目最相近的 `search_page`（软回显）不一致。

目标：

1. **换机制**：用标准 CDP `DOM.performSearch` 三段 XPath 链 + JS TreeWalker 兜底，替换非标准 `window.find()`——覆盖直接文本 / 跨元素分裂文本 / 属性文本 / Shadow DOM。
2. **补成功回显**：命中回显文本与命中方式，对齐 `scroll`/`click`/`send_keys`。
3. **软「未找到」回显**：未找到返回 `extracted_content`（非 error），对齐 browser-use + 本项目 `search_page`（已与用户确认）。
4. **兑现 highlight**：命中后用现有 `Overlay.highlightNode` 高亮元素框（复用 `highlight_element`），兑现 description 承诺。
5. **修复 browser-use 4 个 bug**：引号转义 / searchId 清理 / Shadow DOM 穿透 / 删无用调用。
6. **补齐测试**：新增 `tests/test_find_text.py`，对齐 `tests/test_scroll.py` 三层结构。

---

## 2. 现状对比

| 维度 | browser-use（实际代码 `default_action_watchdog.py:2682-2774`） | TreeWalker（当前 `actions.py:787-797`） | 差距 |
|---|---|---|---|
| 定位机制 | CDP `DOM.performSearch` 三段 XPath + JS TreeWalker 兜底 | 非标准 `window.find()`（经 `execute_js`） | TreeWalker 用非标准 API，不可靠 |
| 跨元素分裂文本 | `//*[contains(., T)]`（第 2 段）覆盖 | 依赖 `window.find` 浏览器实现 | TreeWalker 可能漏 |
| 属性文本 | `//*[@*[contains(., T)]]`（第 3 段） | 无 | TreeWalker 不查 |
| Shadow DOM | 未传 `includeUserAgentShadowDOM`（bug） | `window.find` 不穿透 | 均缺；本方案修为 `True` |
| 引号转义 | 无（f-string 直插 `"`，text 含 `"` 即崩） | `repr(text)` 包 JS 字面量（勉强安全） | browser-use 有 bug；本方案 `_xpath_string_literal` |
| 滚动 | `DOM.scrollIntoViewIfNeeded({nodeId})`（标准 CDP） | `window.find` 视觉滚动（非标准） | — |
| 清理 | `discardSearchResults` 放在 `break` 之后 → 命中即跳过（bug，searchId 泄漏） | 无搜索会话 | 本方案 `finally` 修复 |
| 多命中处理 | 始终取 `nodeIds[0]`（`fromIndex:0, toIndex:1`） | `window.find` 命中首个 | 均无导航；记为 P1（G8） |
| 成功回显 | `extracted_content="Scrolled to text: {text}"` | 裸 `ActionResult()` → LLM 只见 `"OK"` | **TreeWalker 无回显** |
| 未找到语义 | 软回显（`extracted_content` 非 error） | `error=...` 硬错误 | 本方案改软回显 |
| 高亮 | 无（仅滚动） | `window.find` 原生选区（不可靠） | description 承诺但未稳定兑现；本方案 Overlay 高亮 |
| 测试 | 有 | **无**（`tests/` 下无 `test_find_text.py`） | 缺测试（覆盖率缺口） |

---

## 3. 改进方案（分级）

### P0 核心 —— find_text 工具本身

#### G1｜新增 `BrowserSession.find_text(text)`（port browser-use 三段 XPath 链 + 修 bug）

新增 session 方法封装 CDP 文本搜索 + 滚动 + 高亮，action 层只做薄包装（对齐 `scroll`/`send_keys` 的「session 出逻辑、action 出回显」分层）。返回 `dict` 让 action 层据回显：

```python
async def find_text(self, text: str) -> dict:
	"""Find text on the page and scroll the first match into view.

	Mirrors browser-use ``on_ScrollToTextEvent`` (default_action_watchdog.py
	2682-2774), with four bug fixes: (1) XPath-safe string literal via
	``_xpath_string_literal`` (browser-use f-string-injects and breaks on
	``"``), (2) ``discardSearchResults`` in a ``finally`` (browser-use skips
	it on the winning query → searchId leak), (3) ``includeUserAgentShadowDOM``
	pierce (browser-use omits it), (4) no unused ``DOM.getDocument``
	(``performSearch`` already does a full-document search).

	Returns ``{found, method, tag}`` where ``method`` is one of
	``xpath-text`` / ``xpath-content`` / ``xpath-attr`` / ``js-treewalker``
	/ ``none``. A clean miss returns ``found=False`` (never raises); the
	action layer builds the soft echo. Unexpected CDP errors propagate to
	the action layer's try/except.
	"""
	sid = self.current_session_id
	lit = _xpath_string_literal(text)
	queries = [
		("xpath-text", f"//*[contains(text(), {lit})]"),     # 直接文本子节点
		("xpath-content", f"//*[contains(., {lit})]"),       # 全文本内容（覆盖分裂文本）
		("xpath-attr", f"//*[@*[contains(., {lit})]]"),      # 属性值
	]
	for method, query in queries:
		search_id = None
		try:
			search = await self.client.send.DOM.performSearch(
				{"query": query, "includeUserAgentShadowDOM": True},
				session_id=sid,
			)
			search_id = search.get("searchId")
			if search.get("resultCount", 0) <= 0:
				continue
			res = await self.client.send.DOM.getSearchResults(
				{"searchId": search_id, "fromIndex": 0, "toIndex": 1},
				session_id=sid,
			)
			node_ids = res.get("nodeIds", [])
			if not node_ids:
				continue
			node_id = node_ids[0]
			await self.client.send.DOM.scrollIntoViewIfNeeded(
				{"nodeId": node_id}, session_id=sid,
			)
			tag = await self._highlight_search_node(node_id)  # G5 兑现 highlight（best-effort）
			return {"found": True, "method": method, "tag": tag}
		except Exception as e:
			logger.debug("find_text query %s failed: %s", query, e)
			continue
		finally:
			# 修复 bug 2：命中（上面 return）也会进 finally，确保 searchId 释放
			if search_id is not None:
				try:
					await self.client.send.DOM.discardSearchResults(
						{"searchId": search_id}, session_id=sid,
					)
				except Exception:
					pass
	# 兜底：JS TreeWalker（G3）
	if await self._find_text_js_fallback(text):
		return {"found": True, "method": "js-treewalker", "tag": None}
	return {"found": False, "method": "none", "tag": None}
```

> **为什么不调 `DOM.getDocument`/`DOM.enable`**：`performSearch` 自带全文检索（含 shadow，传 `includeUserAgentShadowDOM` 即可），browser-use 调了 `getDocument` 但 `root_node_id` 算完即弃，纯浪费一次 CDP；`DOM.enable` 在 `session.py:300` 的 `_connect` 里已对会话开启（`click`/`upload_file` 均依赖它），无需重复。

#### G2｜XPath 引号安全转义 `_xpath_string_literal`（修复 bug 1）

browser-use 用 `f'//*[contains(text(), "{event.text}")]'` 直接把 text 插进 `"..."`，text 含 `"` 即 XPath 语法错误。新增模块级 helper 生成 XPath 1.0 安全字面量（双引号字面量、或单引号字面量、或 `concat()`）：

```python
def _xpath_string_literal(text: str) -> str:
	"""Build an XPath 1.0 string literal safe for contains().

	XPath 1.0 string literals can't escape a quote inside their own
	delimiter, so: no double-quote → wrap in "..."; no single-quote → wrap
	in '...'; both present → splice with concat(..., '"', ...).
	browser-use f-string-injects into "..." and breaks when text contains ".
	"""
	if '"' not in text:
		return f'"{text}"'
	if "'" not in text:
		return f"'{text}'"
	parts = text.split('"')
	return "concat(" + ", '\"', ".join(f'"{p}"' for p in parts) + ")"
```

示例：

| 输入 text | 输出字面量 |
|---|---|
| `Hello` | `"Hello"` |
| `He said "hi"` | `'He said "hi"'`（单引号定界，内含双引号合法） |
| `It's "fine"` | `concat("It's ", '\"', "fine", '\"', "")` |

#### G3｜JS TreeWalker 兜底（port browser-use，修转义）

三段 XPath 全空时走 `execute_js` 跑 TreeWalker，text 经 `json.dumps` 注入（JS 安全，browser-use 用 f-string 直插同样有引号 bug）。仅作 XPath 全 miss 的兜底（实际很少触发）：

```python
async def _find_text_js_fallback(self, text: str) -> bool:
	"""TreeWalker over text nodes under document.body; scrollIntoView the
	first match's parentElement. Returns True if a match was scrolled into
	view. text is injected via json.dumps (JS-safe; browser-use f-string-
	injects and breaks on quotes)."""
	js = (
		"(() => {"
		f"  const needle = {json.dumps(text)};"
		"  const walker = document.createTreeWalker("
		"    document.body, NodeFilter.SHOW_TEXT, null, false);"
		"  let node;"
		"  while ((node = walker.nextNode())) {"
		"    const t = node.nodeValue || '';"
		"    if (t.includes(needle) && t.trim()) {"
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

#### G4｜`_action_find_text` 重写为薄包装（成功回显 + 软「未找到」+ 异常 error）

替换 `actions.py:787-797`，对齐 `scroll`/`send_keys` 范式：

```python
async def _action_find_text(self, params: dict, browser: BrowserSession) -> ActionResult:
	text = params["text"]
	try:
		info = await browser.find_text(text)
	except Exception as e:
		# CDP 层异常（连接断 / DOM 域报错）= 工具执行失败，明确报 error
		logger.warning("find_text(%r) failed: %s", text, e)
		return ActionResult(error=f"Find text failed: {e}")
	if not info.get("found"):
		# 软回显：「文本不在页面上」是可操作信息而非工具失败，
		# 对齐 browser-use + 本项目 search_page（error is None）
		msg = f"Text '{text}' not found on page"
		logger.info(msg)
		return ActionResult(extracted_content=msg, long_term_memory=msg)
	# 命中：回显文本 + 命中方式（+ 命中元素 tag，若 G5 拿到）
	method = info.get("method")
	tag = info.get("tag")
	memory = f"Scrolled to text '{text}' into view (via {method})"
	if tag:
		memory = f"Scrolled to text '{text}' into view (found in <{tag}>, via {method})"
	logger.info(memory)
	return ActionResult(extracted_content=memory, long_term_memory=memory)
```

> 不设 `success=True`（`views.py:18-25` validator 禁止非 done 动作设 success=True）。

#### G5｜兑现 highlight（`Overlay.highlightNode`，best-effort）

`performSearch` 返回前端 `nodeId`，`scrollIntoViewIfNeeded` 直接吃 `nodeId`；但 `highlight_element`（`session.py:575-577`）吃 **`backendNodeId`**（经 `Overlay.highlightNode`），需经 `DOM.describeNode({nodeId})` 取 `node.backendNodeId` 转换。高亮本身 best-effort（`highlight_element` 内部已 auto-hide + 吞错），失败不影响滚动回显：

```python
async def _highlight_search_node(self, node_id: int) -> str | None:
	"""Convert a performSearch nodeId to backendNodeId and highlight the
	element box (visual feedback, best-effort). Returns the lowercased tag
	name for the echo, or None if describe/highlight failed."""
	try:
		desc = await self.client.send.DOM.describeNode(
			{"nodeId": node_id}, session_id=self.current_session_id,
		)
		node = desc.get("node", {})
		backend_id = node.get("backendNodeId")
		tag = (node.get("nodeName") or "").lower() or None
		if backend_id:
			await self.highlight_element(backend_id)  # 复用 session.py:575 → Overlay.highlightNode
		return tag
	except Exception:
		return None
```

> **局限**：匹配落在「元素框」层级——若命中第 2 段 `contains(., T)` 的是一个大容器 `<div>`，高亮的是整个 div 框而非精确文本。精确文本高亮（原生选区）留 P1（G11）。

#### G6｜`FindTextParams.text` 收紧（可选，`models.py:99-101`）

`text` 加 `min_length=1`：拒绝空串（空串会让 `contains(text(), "")` 命中**全部**元素，无意义且浪费 CDP）。不破坏 tool schema（LLM 总会传非空 text）：

```python
class FindTextParams(BaseModel):
	model_config = ConfigDict(extra="forbid")
	text: str = Field(min_length=1, description="Text to search for on the page")
```

#### G7｜新增 `tests/test_find_text.py`（对齐 `tests/test_scroll.py` 三层结构）

> CLAUDE.md 要求：改功能必须同步加测试、覆盖率 >85%。参照 `tests/test_scroll.py` 既有模式：action 层用 `_make_browser` 桩（`MagicMock` + `AsyncMock`，经 `Tools().execute` 调用）；param model 层校验 `ValidationError`；session 层用 `BrowserSession.__new__` 绕过 `__init__` + `MagicMock` client。所有 async 测试显式标 `@pytest.mark.asyncio`（项目无 `asyncio_mode=auto`）。

| 测试类 | 用例 |
|---|---|
| `TestFindTextAction` | ① 命中（`browser.find_text` 返回 `{found:True, method:'xpath-text', tag:'p'}`）→ 回显含 `Scrolled to text '...' into view`、`found in <p>`、`extracted_content == long_term_memory`、`error is None`、`success is None`；② 命中但 tag=None（G5 失败）→ 回显含 `(via xpath-content)`、**不含** `found in <`；③ 未找到（`{found:False, method:'none'}`）→ 软回显 `Text '...' not found on page`、**`error is None`**（区别当前硬 error）、`extracted_content == long_term_memory`；④ `browser.find_text` 抛 `RuntimeError("cdp timeout")` → `result.error == "Find text failed: cdp timeout"`、`extracted_content is None`；⑤ `browser.get_state.assert_not_awaited()`（动作不应触发全量 DOM 抓取）；⑥ `browser.find_text.assert_awaited_once_with(text)`（透传原样 text） |
| `TestFindTextParams` | `text` 接受非空串；`min_length=1` 拒绝空串（G6）；`extra="forbid"` 拒绝未知字段 |
| `TestFindTextSession` | ① 命中第 1 段：`performSearch` 返回 `resultCount:1` → `getSearchResults` 返回 `nodeIds:[42]` → `scrollIntoViewIfNeeded({nodeId:42})` 被 await、返回 `{found:True, method:'xpath-text'}`；**`discardSearchResults` 在命中路径也被 await**（验证 bug 2 修复，用 `assert_awaited`）；② 第 1 段空（`resultCount:0`）走第 2 段命中 → `method:'xpath-content'`、`performSearch` 被 await 2 次；③ 三段全空 → 走 `_find_text_js_fallback`（`execute_js`→`Runtime.evaluate` 被 await，expression 含 `createTreeWalker`），返回 `method:'js-treewalker'`；④ JS 兜底也 false → `{found:False, method:'none'}`；⑤ 引号转义：text=`He said "hi"` → `performSearch` 收到的 query 含 `'He said "hi"'`（单引号定界），text=`It's "fine"` → query 含 `concat(`（验证 G2）；⑥ `performSearch` 抛异常 → 该段 catch、continue 下一段（验证 per-query try/except）；⑦ G5：命中后 `describeNode({nodeId})` 被 await，返回的 `backendNodeId` 传给 `highlight_element` |

action 层桩（复用 `test_scroll.py` 的 `_make_browser` 模式）：

```python
def _make_browser(
	*, find_return: dict | None = None, find_raises: Exception | None = None,
) -> MagicMock:
	bs = MagicMock()
	if find_raises:
		bs.find_text = AsyncMock(side_effect=find_raises)
	else:
		bs.find_text = AsyncMock(
			return_value=find_return or {"found": True, "method": "xpath-text", "tag": "p"},
		)
	bs.get_state = AsyncMock()  # 仅断言未被调用
	return bs
```

session 层桩要点（`performSearch` 返回 `{searchId, resultCount}`、`getSearchResults` 返回 `{nodeIds:[...]}`、`describeNode` 返回 `{node:{backendNodeId, nodeName}}`）：

```python
def _make_session(
	self, *, result_counts=(1, 0, 0), node_ids=(42,), hit_query_index=0,
) -> tuple[BrowserSession, MagicMock]:
	s = BrowserSession.__new__(BrowserSession)
	s.current_session_id = "sid-1"
	# _highlight 注入：可置 None 绕过 describeNode
	s._highlight = MagicMock()
	client = MagicMock()
	client.send.DOM.performSearch = AsyncMock(
		side_effect=[{"searchId": f"sid-{i}", "resultCount": c} for i, c in enumerate(result_counts)],
	)
	client.send.DOM.getSearchResults = AsyncMock(
		return_value={"nodeIds": list(node_ids)},
	)
	client.send.DOM.scrollIntoViewIfNeeded = AsyncMock(return_value={})
	client.send.DOM.discardSearchResults = AsyncMock(return_value={})
	client.send.DOM.describeNode = AsyncMock(
		return_value={"node": {"backendNodeId": 99, "nodeName": "P"}},
	)
	client.send.Runtime.evaluate = AsyncMock(return_value={"result": {"value": True}})
	s.client = client
	s.highlight_element = AsyncMock()
	return s, client
```

### P1 增强 —— 进阶能力（本次仅画蓝图）

#### G8｜多命中导航（find next）

`getSearchResults` 改取 `toIndex=min(count, N)`（N>1），`FindTextParams` 加 `nth: int = 1`（或 `occurrence`），命中第 nth 个 match。需配合「已到第 N 个 / 共 M 个」回显。代价：searchId 生命周期需跨调用或在 `find_text` 内一次性取回全部 nodeIds 后按 nth 选——前者要会话状态，后者 N 大时 CDP 返回大。

#### G9｜可见性优先选 match

三段 XPath 全取回后再按可见性（`offsetParent !== null` / 非 `display:none`）筛首个**可见** match，而非盲取 `nodeIds[0]`。browser-use 取首个 match 即滚，命中隐藏元素（如折叠区）会让 LLM 困惑。代价：+1 次 `Runtime.evaluate`（批量读 `getBoundingClientRect` + `offsetParent`）。

#### G10｜大小写不敏感选项

`FindTextParams` 加 `case_sensitive: bool = False`。XPath 用 `translate(., LOWER, LOWER)` + `translate($t, ...)` 双向转小写（需构造含全字母的 `LOWER` 串，丑但标准）；JS 兜底用 `t.toLowerCase().includes(needle.toLowerCase())`。默认不敏感更贴近「Ctrl+F」直觉。

#### G11｜原生选区高亮（精确文本高亮）

命中后用 `window.find(text)` 创建浏览器原生**选区**（蓝底高亮精确文本），作为 G5 元素框高亮的补充/替代——元素框高亮对「大容器命中」过粗，选区高亮精确但 `window.find` 非标准。可作可选 `highlight: 'box'|'selection'|'none'`。

### 决策备注

- **以 browser-use 实际代码为准**（非文档 §11 描述）：实际是三段 XPath + JS TreeWalker 兜底，文档写的 `getDocument(pierce)` / `Runtime.evaluate 可见性检查` 在实际代码里不存在 / 未用。
- **软回显（非 error）**：「文本不在页面上」是可操作信息（LLM 可改滚/换页/确认文本不存在），非工具执行失败；对齐 browser-use + 本项目 `search_page`（已与用户确认）。
- **不调 `DOM.getDocument`/`DOM.enable`**：`performSearch` 自带全文检索；`DOM.enable` 已在 `_connect`（`session.py:300`）开启。browser-use 调 `getDocument` 但 `root_node_id` 弃用，纯浪费。
- **`finally` 清理 `discardSearchResults`**：browser-use 放在 `break` 之后导致命中路径漏调（searchId 泄漏）；本方案用 `try/finally`，命中（return）也进 finally。
- **`includeUserAgentShadowDOM: True`**：browser-use 漏传，Shadow DOM 内文本搜不到；本方案补上（`window.find` 本就不穿透）。
- **`nodeId` vs `backendNodeId`**：`performSearch`/`getSearchResults` 返回前端 `nodeId`，`scrollIntoViewIfNeeded` 直接吃 `nodeId`（`session.py:773` 现用 `backendNodeId`，但 CDP 命令两者皆可）；`highlight_element` 需 `backendNodeId`，经 `DOM.describeNode({nodeId})` 取 `node.backendNodeId` 转换。
- **不保留 `window.find()`**：非标准、不可靠，CDP 三段 XPath + JS 兜底覆盖更全；原生选区高亮（G5 元素框高亮的精确版）留 P1（G11）。
- **不设 `success=True`**：`views.py:18-25` validator 禁止非 done 动作设 success=True。
- **`terminates_sequence` 保持 `False`**（`models.py:202`）：find_text 不切页面上下文（仅滚动，不改 URL/Tab/DOM 结构），对齐 browser-use（其 `find_text` `terminates_sequence=False`）。
- **G5 高亮局限**：匹配落在元素框层级，大容器命中时高亮过粗；精确文本高亮留 P1（G11）。

---

## 4. CDP 调用清单（变更后）

| 路径 | CDP 命令 | 变化 |
|---|---|---|
| `find_text`（命中，XPath 命中段） | `DOM.performSearch`(`{query, includeUserAgentShadowDOM:True}`) + `DOM.getSearchResults`(`{searchId, fromIndex:0, toIndex:1}`) + `DOM.scrollIntoViewIfNeeded`(`{nodeId}`) + **`DOM.discardSearchResults`**(`{searchId}`, finally) + `DOM.describeNode`(`{nodeId}` → backendNodeId) + `Overlay.highlightNode`(经 `highlight_element`) | 从 **1 次 `Runtime.evaluate(window.find)`** → **多次 `DOM.*` + 1 次 `Overlay.*`**（首用 `performSearch`/`getSearchResults`/`discardSearchResults`） |
| `find_text`（XPath 全空 → JS 兜底命中） | 上述 `performSearch`×3(空) + `Runtime.evaluate`(TreeWalker，经 `execute_js`) | 兜底路径，实际很少触发 |

---

## 5. 涉及文件清单

| 文件 | 改动 | 对应改进 |
|---|---|---|
| `src/tree_walker/browser/session.py` | 新增 `find_text(text)` + `_find_text_js_fallback` + `_highlight_search_node`（session 层方法），+ 模块级 `_xpath_string_literal` helper | G1 / G2 / G3 / G5 |
| `src/tree_walker/tools/actions.py` | `_action_find_text` 重写为薄包装（调 `browser.find_text` + 成功回显 + 软「未找到」+ 异常 error） | G4 |
| `src/tree_walker/tools/models.py` | `FindTextParams.text` 加 `min_length=1`（可选） | G6 |
| `tests/test_find_text.py` | **新建**（三层：action / param / session） | G7 |
| `docs/Tools技术细节/04_动作清单与CDP映射.md` | 4.8 节同步更新（CDP 链 / 回显 / 软未找到 / 高亮描述，刷新过时行号 `actions.py:304-314` → `actions.py:787-797`） | 文档同步（实施后） |

---

## 6. 实施后的验证

- 新增/相关测试：`uv run python -m pytest tests/test_find_text.py -x -v`
- 全量回归：`uv run python -m pytest tests/ -x -v`
- 覆盖率（CLAUDE.md 目标 >85%）：`uv run python -m pytest tests/ --cov=tree_walker.tools --cov=tree_walker.browser`
- 手测：
  1. 打开长页面（如维基百科长文），调 `find_text {"text": "<页内可见文本>"}` → 回显 `Scrolled to text '...' into view (found in <p>, via xpath-text)`，截图确认元素框高亮 + 已滚入视口；
  2. 调 `find_text {"text": "<跨行/分裂文本>"}`（如 `<p>Hello <b>World</b></p>` 搜 `Hello World`）→ 命中第 2 段 `xpath-content`，回显含 `via xpath-content`；
  3. 调 `find_text {"text": "<不存在的文本>"}` → **软回显** `Text '...' not found on page`，`error is None`（区别当前硬 error），LLM 可据此判断文本确实不在页；
  4. 调 `find_text {"text": "He said \"hi\""}`（含双引号）→ 正常命中（验证 G2 转义，browser-use 此输入会崩）；
  5. 含 Shadow DOM 的页面（如 Lit/Web Components 组件）搜组件内文本 → 命中（验证 G1 `includeUserAgentShadowDOM`）；
  6. CDP 异常（断开连接模拟）→ `result.error == "Find text failed: ..."`、`extracted_content is None`（验证 G4 异常路径）；
  7. 空串 `find_text {"text": ""}` → Pydantic 校验失败、工具不执行（验证 G6）。
