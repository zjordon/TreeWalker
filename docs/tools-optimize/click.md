# click 工具完善方案：坐标失败纠错 + 遮挡回退 + 视口对齐 + 结果回显

> 参照 browser-use（`browser_use/tools/service.py:635-740` 的 `_click_by_coordinate` / `_click_by_index` + `browser_use/browser/watchdogs/default_action_watchdog.py:702-1140` 的 `_click_element_node_impl` / `_click_on_coordinate` / `_check_element_occlusion`）完善本项目的 `click` 动作。
> 相关文档：`docs/Tools技术细节/04_动作清单与CDP映射.md` 的 4.1 click 节（本项目现状）、`browser-use/docs/Tools技术细节/05-动作详解-浏览器交互.md` 的 4. click 节（参考标杆）。

## 背景（为什么改）

当前 TreeWalker 的 `click`（`src/tree_walker/tools/actions.py:300-314` 的 `_action_click` + `src/tree_walker/browser/session.py:476-595` 的 `click_at` / `get_element_coordinates` / `click_element`）是最薄的 action 之一，几乎所有逻辑都在 session 层。对照 browser-use 的成熟实现与刚被完善的 `navigate`/`go_back`（已统一回显、错误映射惯例），存在三类正确性 bug 与两类鲁棒性短板：

1. **坐标失败被静默吞掉（正确性 bug）**：`click_element`（`session.py:591-595`）在三层坐标 fallback 全返回 `None` 时仅 `logger.warning` 并 `return`，但 `_action_click`（`actions.py:312-314`）不感知，仍返回空成功 `ActionResult()`。LLM 收到 `OK` 却实际没点 → loop detector 误判、task 永不收敛。browser-use 此处会走 JS 回退（`_click_element_node_impl:875,957-992`），最终失败则明确 `ActionResult(error=...)`。
2. **SELECT 分支查全页（正确性 bug）**：`_action_click` 的 SELECT 分支（`actions.py:305-311`）执行 `document.querySelectorAll('select option')`，扫描页面上**所有** select 的 option，与 `index` 指定的那个无关。多 select 页面会返回错误的 option 列表（`dropdown_options` / `select_dropdown` 有同源 bug，但分属不同动作，本次不修）。
3. **成功无回显（语义缺陷）**：普通点击成功返回空 `ActionResult()`（`actions.py:314`），显示 `OK`。LLM 无法从 result 确认点了什么，需额外 `get_state` 才知道——与已统一的 navigate/go_back/search 回显惯例脱节。
4. **缺 `mouseMoved`（鲁棒性）**：`click_at`（`session.py:476-492`）只派发 `mousePressed` + `mouseReleased`，没有先 `mouseMoved`。hover 菜单、`mousemove`/`mouseenter` 监听器、反爬检测依赖 `mouseMoved` 触发状态，会失效。browser-use 序列为 `mouseMoved → mousePressed → mouseReleased`（`default_action_watchdog.py:902-955`）。
5. **缺遮挡检查 + JS click 回退（鲁棒性）**：元素被固定 header / footer / 弹窗遮挡时几何点击点不到，应回退 `this.click()`。browser-use 用 `document.elementFromPoint` 判遮挡（`_check_element_occlusion:573-700`）并在遮挡 / 无 quad / dispatch 失败 / checkbox 状态未变时回退 JS（`_click_element_node_impl:957-992`）。
6. **坐标取第一个 quad、不裁剪视口（鲁棒性）**：`get_element_coordinates`（`session.py:494-572`）的 Method 1 取 `quads[0]`，不与视口求交；`click_element` 取几何中心后不裁剪到视口。browser-use 取"与视口交集最大的 quad"（`_click_element_node_impl:829-864`）并把中心裁剪到 `[0, viewport-1]`（`:866-872`）。

附带问题：`docs/Tools技术细节/04_动作清单与CDP映射.md` 的 4.1 节"主要逻辑"代码块（行 66-81）仍引用旧版行号 `actions.py:203-217`（现 300-314），CDP 清单 `Input.dispatchMouseEvent × 2` 未含新增的 `mouseMoved`，文档与实现已脱节。

**已确认的决策（用户已选）：**
- **范围 = 全面层级**：在"修正确性 + 鲁棒性"基础上，额外对齐 browser-use 的坐标选取——`get_element_coordinates` 改为"取与视口交集最大的 quad"，`click_element` 把中心裁剪到视口内，并新增 `_get_viewport_size()`（`Page.getLayoutMetrics`）。
- 坐标失败 → **明确 error**（不静默成功），并加 JS click 回退兜底。
- 成功 → **回显** `Clicked [TAG] {text} at index N`（对齐 navigate/go_back）。
- SELECT 分支用 `fetch_select_options(backend_id)` **精确查指定 index 的那个 select**。
- iframe 会话路由、post-click 等待、coordinate_x/y 双模式、dropdown_options/select_dropdown 的全页 bug **留作未来**（理由见"已知限制"）。

预期结果：click 在元素被遮挡、坐标异常、视口边缘等场景下仍能可靠点击；失败时明确报错；成功时回显；鼠标序列对齐 browser-use；并有测试覆盖。

---

## 改动文件（共 4 个）

### 1. `src/tree_walker/tools/models.py` —— 不改

`ClickParams`（models.py:15-17）保持只有 `index: int`。不引入 `force` / `js_fallback` / `wait_for_navigation` 等开关——遮挡回退与视口裁剪是 session 层的鲁棒性细节，不应暴露给 LLM（LLM 不该决定是否回退 JS click）；click 也不是页面级动作，无需等待开关。`ACTION_DEFINITIONS["click"]` 的 description 与 `terminates_sequence=False` 维持不变。

---

### 2. `src/tree_walker/browser/session.py`（session 层保持纯粹）

**(a) 重写 `click_at`（第 476-492 行）—— 新增 `mouseMoved`**，序列对齐 browser-use `default_action_watchdog.py:902-955`：

```python
async def click_at(self, x: float, y: float) -> None:
	"""Click at viewport coordinates.

	Mouse sequence mirrors browser-use default_action_watchdog.py:902-955:
	mouseMoved -> mousePressed -> mouseReleased. The leading mouseMoved is
	required by hover menus, mousemove listeners, and anti-bot heuristics
	that only fire on an explicit move event (not press/release alone).
	"""
	sid = self.current_session_id
	# 1) mouseMoved — 触发 hover 状态 / mousemove 监听器 / 反爬检测
	await self.client.send.Input.dispatchMouseEvent(
		{"type": "mouseMoved", "x": x, "y": y},
		session_id=sid,
	)
	await asyncio.sleep(0.05)  # 对齐 browser-use moved->pressed 间隔
	# 2) mousePressed
	await self.client.send.Input.dispatchMouseEvent(
		{
			"type": "mousePressed",
			"x": x,
			"y": y,
			"button": "left",
			"clickCount": 1,
		},
		session_id=sid,
	)
	await asyncio.sleep(0.08)  # 对齐 browser-use pressed->released 间隔
	# 3) mouseReleased
	await self.client.send.Input.dispatchMouseEvent(
		{
			"type": "mouseReleased",
			"x": x,
			"y": y,
			"button": "left",
			"clickCount": 1,
		},
		session_id=sid,
	)
	await asyncio.sleep(0.3)  # 保留原有等待，让点击反馈动画 / SPA 局部更新有机会
	if self._highlight_settings.enabled and self._highlight_settings.click_feedback_enabled:
		await self._highlight.highlight_click_point(x, y)
```

- **新增 `mouseMoved`**：CDP 规范的 move 事件无 `button`/`clickCount` 字段。
- **sleep 拆分**：原单次 `0.3` → `0.05`(moved→pressed) + `0.08`(pressed→released) + `0.3`(released→返回)，总时延 0.3s → ≈0.43s，对齐 browser-use 时序。
- **不改签名**：仍 `click_at(x, y) -> None`。唯一另一调用方 `_action_input_text`（`actions.py:321`，间接经 `click_element` → `click_at`）零适配，且同样受益于更完整的鼠标序列（聚焦更可靠）。

**(b) 重写 `get_element_coordinates`（第 494-572 行）—— 取最大交集 quad + 接受 viewport 形参**：

```python
async def get_element_coordinates(
	self, backend_node_id: int, viewport: tuple[int, int] | None = None,
) -> DOMRect | None:
	"""Get real-time viewport coordinates for an element via CDP.

	Three-tier fallback chain (same as browser-use):
	1. DOM.getContentQuads — best for inline/complex layouts; picks the
	   quad with the largest intersection with the viewport (mirrors
	   browser-use _click_element_node_impl:829-864).
	2. DOM.getBoxModel — fallback using box model content.
	3. JS getBoundingClientRect() via DOM.resolveNode + Runtime.callFunctionOn.

	``viewport`` is an optional (width, height); callers that already fetched
	it (e.g. click_element) pass it in to avoid a duplicate Page.getLayoutMetrics
	round-trip. If omitted it is fetched here.
	"""
	sid = self.current_session_id
	if viewport is None:
		viewport = await self._get_viewport_size()

	# Method 1: DOM.getContentQuads — 取与视口交集最大的 quad 的外接矩形
	try:
		result = await self.client.send.DOM.getContentQuads(
			{"backendNodeId": backend_node_id},
			session_id=sid,
		)
		best = self._best_quad_rect(result.get("quads", []), viewport)
		if best:
			return best
	except Exception:
		pass

	# Method 2: DOM.getBoxModel
	try:
		result = await self.client.send.DOM.getBoxModel(
			{"backendNodeId": backend_node_id},
			session_id=sid,
		)
		model = result.get("model", {})
		content = model.get("content", [])
		if len(content) >= 8:
			xs = [content[i] for i in range(0, 8, 2)]
			ys = [content[i] for i in range(1, 8, 2)]
			return DOMRect(
				x=min(xs), y=min(ys),
				width=max(xs) - min(xs),
				height=max(ys) - min(ys),
			)
	except Exception:
		pass

	# Method 3: JS getBoundingClientRect()
	try:
		resolve = await self.client.send.DOM.resolveNode(
			{"backendNodeId": backend_node_id},
			session_id=sid,
		)
		object_id = resolve["object"]["objectId"]
		js_result = await self.client.send.Runtime.callFunctionOn(
			{
				"objectId": object_id,
				"functionDeclaration": """
				function() {
					const rect = this.getBoundingClientRect();
					return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
				}
				""",
				"returnByValue": True,
			},
			session_id=sid,
		)
		rect = js_result.get("result", {}).get("value")
		if rect and rect.get("width", 0) > 0 and rect.get("height", 0) > 0:
			return DOMRect(
				x=rect["x"], y=rect["y"],
				width=rect["width"], height=rect["height"],
			)
	except Exception:
		pass

	return None
```

- **新增 `viewport` 形参**：`click_element` 已为"中心裁剪"取过一次视口，传入避免重复 `Page.getLayoutMetrics`。默认 `None` 时自行取（保持向后兼容）。
- **Method 1 改为"最大交集 quad"**：原 `quads[0]` 对多 quad 元素（如跨行 inline、CSS transform 拆分）可能选到不可见的那块；现在用 `_best_quad_rect` 选与视口交集最大者。Method 2/3 维持不变。

**(c) 新增 `_best_quad_rect` 静态 helper（紧随 `get_element_coordinates`）**：

```python
@staticmethod
def _best_quad_rect(
	quads: list, viewport: tuple[int, int] | None,
) -> DOMRect | None:
	"""From a list of quads (each 8 floats: x1,y1,...,x4,y4), return the
	bounding DOMRect of the quad with the largest intersection with the
	viewport. Falls back to the first quad when viewport is unknown or
	empty. Mirrors browser-use _click_element_node_impl:829-864 (simplified
	to bounding-box intersection instead of exact polygon clipping).
	"""
	rects: list[DOMRect] = []
	for quad in quads:
		if len(quad) < 8:
			continue
		xs = [quad[i] for i in range(0, 8, 2)]
		ys = [quad[i] for i in range(1, 8, 2)]
		rects.append(DOMRect(
			x=min(xs), y=min(ys),
			width=max(xs) - min(xs),
			height=max(ys) - min(ys),
		))
	if not rects:
		return None
	if not viewport or viewport[0] <= 0 or viewport[1] <= 0:
		return rects[0]
	vw, vh = viewport

	def _area(r: DOMRect) -> float:
		iw = max(0.0, min(r.x + r.width, vw) - max(r.x, 0.0))
		ih = max(0.0, min(r.y + r.height, vh) - max(r.y, 0.0))
		return iw * ih

	return max(rects, key=_area)
```

**(d) 新增 `_get_viewport_size`（紧随 `_best_quad_rect`）**：

```python
async def _get_viewport_size(self) -> tuple[int, int] | None:
	"""Viewport (clientWidth, clientHeight) via Page.getLayoutMetrics.

	Used for quad selection (pick the most-visible quad) and center clipping.
	Returns None on any CDP error so callers degrade gracefully (first quad
	/ no clip).
	"""
	try:
		result = await self.client.send.Page.getLayoutMetrics(
			{}, session_id=self.current_session_id,
		)
		lv = result.get("layoutViewport", {})
		w, h = int(lv.get("clientWidth", 0)), int(lv.get("clientHeight", 0))
		return (w, h) if w > 0 and h > 0 else None
	except Exception:
		return None
```

**(e) 重写 `click_element`（第 574-595 行）—— 返回 `bool` + 视口裁剪 + 遮挡回退**：

```python
async def click_element(self, backend_node_id: int) -> bool:
	"""Click an element using real-time CDP coordinates.

	Returns True if a mouse-event sequence was dispatched (either at the
	element's center or via JS fallback). Returns False if coordinates
	could not be obtained AND JS fallback also failed — the caller (action
	layer) should treat False as "click did not happen" and surface a
	friendly error to the LLM.

	Robustness chain (mirrors browser-use _click_element_node_impl):
	1. scrollIntoViewIfNeeded (best-effort, failures swallowed)
	2. get_element_coordinates -> center, clipped to viewport
	3. click_at unless _is_element_occluded reports the click point is
	   covered by another element
	4. JS fallback this.click() when step 2/3 are skipped (no coords or
	   occluded)
	"""
	# 1. Scroll into view first (best-effort)
	try:
		await self.client.send.DOM.scrollIntoViewIfNeeded(
			{"backendNodeId": backend_node_id},
			session_id=self.current_session_id,
		)
		await asyncio.sleep(0.05)
	except Exception:
		pass

	viewport = await self._get_viewport_size()
	rect = await self.get_element_coordinates(backend_node_id, viewport=viewport)
	if rect:
		x = int(rect.x + rect.width / 2)
		y = int(rect.y + rect.height / 2)
		# 裁剪到视口（对齐 browser-use _click_element_node_impl:866-872）
		if viewport:
			x = max(0, min(viewport[0] - 1, x))
			y = max(0, min(viewport[1] - 1, y))
		if not await self._is_element_occluded(backend_node_id, x, y):
			await self.click_at(x, y)
			return True
		logger.info(
			"Element backendNodeId=%d is occluded at (%d,%d), using JS click fallback",
			backend_node_id, x, y,
		)

	# 2. 坐标拿不到 OR 被遮挡 -> JS click 回退
	if await self._js_click(backend_node_id):
		return True

	logger.warning(
		"Could not click backendNodeId=%d (no coordinates and JS fallback failed)",
		backend_node_id,
	)
	return False
```

- **返回类型 `None` → `bool`**：成功（几何点击 or JS 回退）→ `True`；坐标拿不到 + JS 回退也失败 → `False`。与 `go_back` 返回 `str | None`、`navigate` 返回 `target_id | None` 的"边界情况用特殊返回值"风格一致。
- **视口裁剪**：取中心后 `max(0, min(vw-1, x))`，避免元素部分超出视口时点击坐标落到视口外被 CDP 截断。
- **遮挡检查 + JS 回退**：被遮挡时不几何点击，直接 `_js_click`；坐标 `None` 也走 `_js_click`。只有 JS 也失败才 `False`。**不再静默成功**。
- **唯一调用方零破坏**：`_action_input_text`（`actions.py:321`）`await browser.click_element(...)` 不接收返回值，`None` → `bool` 向后兼容。

**(f) 新增 `_is_element_occluded`（紧随 `click_element`）**：

```python
async def _is_element_occluded(
	self, backend_node_id: int, x: int, y: int,
) -> bool:
	"""Check if the element is occluded at (x, y) by another element.

	Uses document.elementFromPoint to find the topmost element at the click
	point, then walks up its ancestor chain looking for the target (covers
	<label> wrapping <input>, aria-labelledby pairs, and other "visible
	target is an ancestor of the hit element" patterns that browser-use
	also handles in _check_element_occlusion:573-700).

	Best-effort: any JS/CDP error returns False (treat as not occluded so
	the geometric click proceeds — JS fallback would also fail if the page
	were truly broken). x/y are passed as arguments, not string
	interpolation, to avoid injection.
	"""
	try:
		resolve = await self.client.send.DOM.resolveNode(
			{"backendNodeId": backend_node_id},
			session_id=self.current_session_id,
		)
		object_id = resolve["object"]["objectId"]
		result = await self.client.send.Runtime.callFunctionOn(
			{
				"objectId": object_id,
				"functionDeclaration": """
				function(x, y) {
					var hit = document.elementFromPoint(x, y);
					if (!hit) return true;  // 视口外或被遮挡
					var cur = hit;
					while (cur) {
						if (cur === this) return false;  // 命中目标或其祖先 -> 未遮挡
						cur = cur.parentElement;
					}
					return true;  // elementFromPoint 命中的是无关元素 -> 被遮挡
				}
				""",
				"arguments": [{"value": x}, {"value": y}],
				"returnByValue": True,
			},
			session_id=self.current_session_id,
		)
		return bool(result.get("result", {}).get("value"))
	except Exception as e:
		logger.debug("_is_element_occluded failed (treating as not occluded): %s", e)
		return False
```

**(g) 新增 `_js_click`（紧随 `_is_element_occluded`）**：

```python
async def _js_click(self, backend_node_id: int) -> bool:
	"""JS fallback click via DOM.resolveNode + Runtime.callFunctionOn.

	Used when geometric click is impossible (no coordinates) or when the
	element is occluded. Calls this.click() directly, bypassing the mouse
	event pipeline. Mirrors browser-use _click_element_node_impl:957-992.

	Returns True if the JS click dispatched without error, False on any
	failure (DOM.resolveNode miss, JS exception, transport glitch).
	"""
	try:
		resolve = await self.client.send.DOM.resolveNode(
			{"backendNodeId": backend_node_id},
			session_id=self.current_session_id,
		)
		object_id = resolve["object"]["objectId"]
		await self.client.send.Runtime.callFunctionOn(
			{
				"objectId": object_id,
				"functionDeclaration": "function() { this.click(); }",
				"returnByValue": True,
			},
			session_id=self.current_session_id,
		)
		return True
	except Exception as e:
		logger.debug("_js_click failed: %s", e)
		return False
```

**(h) 新增 `fetch_select_options`（放在 `execute_js` 附近，属"高级 JS 封装"分组）**：

```python
async def fetch_select_options(self, backend_node_id: int) -> list[dict]:
	"""Read all options of the <select> identified by backendNodeId.

	Uses DOM.resolveNode + Runtime.callFunctionOn to scope the query to the
	specific select element (NOT document.querySelectorAll('select option'),
	which scans every select on the page — the bug fixed in the click SELECT
	branch; dropdown_options / select_dropdown still have it, to be fixed
	separately).

	Returns a list of {value, text, selected} dicts. Raises on CDP/JS error
	(caller wraps with a friendly message).
	"""
	resolve = await self.client.send.DOM.resolveNode(
		{"backendNodeId": backend_node_id},
		session_id=self.current_session_id,
	)
	object_id = resolve["object"]["objectId"]
	result = await self.client.send.Runtime.callFunctionOn(
		{
			"objectId": object_id,
			"functionDeclaration": """
			function() {
				return Array.from(this.options).map(function(o) {
					return {
						value: o.value,
						text: (o.textContent || '').trim(),
						selected: o.selected,
					};
				});
			}
			""",
			"returnByValue": True,
		},
		session_id=self.current_session_id,
	)
	value = result.get("result", {}).get("value")
	return value if isinstance(value, list) else []
```

> **为什么遮挡检查 / JS 回退 / fetch_select_options 都放 session 层**：三者都是纯 CDP 编排（`DOM.resolveNode` + `Runtime.callFunctionOn`），与 LLM 无关，action 层只看到一个 `bool` / `list`，不需要知道是几何点击还是 JS 点击、是哪个 select。符合项目"session 层保持纯粹、action 层做策略"的分层哲学。

---

### 3. `src/tree_walker/tools/actions.py`（action 层做策略）

**(a) 重写 `_action_click`（第 300-314 行）**：

```python
async def _action_click(self, params: dict, browser: BrowserSession) -> ActionResult:
	# 1. 元素查找（保持原逻辑）
	entry, error = await self._get_element_by_index(params["index"], browser)
	if error:
		return error

	backend_id = entry.backend_node_id

	# 2. SELECT 分支：精确查"指定 index 的那个 select"，不再全页 querySelectorAll
	if entry.tag_name.upper() == "SELECT":
		try:
			options = await browser.fetch_select_options(backend_id)
		except Exception as e:
			return ActionResult(error=f"Failed to read select options: {e}")
		return ActionResult(extracted_content=str(options))

	# 3. 普通点击：highlight -> click_element，映射 bool 信号
	try:
		await browser.highlight_element(backend_id)
		clicked = await browser.click_element(backend_id)
	except Exception as e:
		# CDP 异常（连接断开、target 消失等）——友好映射，不让 LLM 看裸堆栈
		return ActionResult(error=f"Click failed: {e}")

	if not clicked:
		# 坐标拿不到 + JS 回退也失败 —— 明确告知 LLM，不再静默成功
		return ActionResult(
			error=(
				f"Could not click element {params['index']} "
				f"(no coordinates and JS click fallback failed; "
				f"the element may be detached, hidden, or in a cross-origin iframe)"
			),
		)

	# 4. 成功回显（对齐 navigate/go_back 风格）
	memory = self._describe_click(entry, params["index"])
	logger.info(memory)
	return ActionResult(extracted_content=memory, long_term_memory=memory)
```

**(b) 新增 `_describe_click` helper（紧随 `_action_click`）**：

```python
@staticmethod
def _describe_click(entry: Any, index: int) -> str:
	"""Build a human-readable click echo, mirroring navigate/go_back style.

	Prefers an identifying attribute the LLM can also see in the DOM tree
	(aria-label/placeholder/title/alt/value), then node_value, then just the
	tag. Bounded to ~60 chars per field so the echo fits the LLM context.
	"""
	tag = entry.tag_name.upper()
	attrs = getattr(entry, "attributes", {}) or {}
	for key in ("aria-label", "placeholder", "title", "alt", "value"):
		v = attrs.get(key)
		if v:
			v = v.strip()
			if len(v) > 60:
				v = v[:60] + "..."
			return f"Clicked [{tag}] {v!r} at index {index}"
	node_value = (getattr(entry, "node_value", "") or "").strip()
	if node_value:
		if len(node_value) > 60:
			node_value = node_value[:60] + "..."
		return f"Clicked [{tag}] {node_value!r} at index {index}"
	return f"Clicked [{tag}] at index {index}"
```

- **bool 信号映射**：`clicked=False` → 明确 `ActionResult(error=...)`，描述可能原因（detached / hidden / cross-origin iframe），引导 LLM 重试或换元素。不再静默返回空 OK。
- **异常友好映射**：highlight / click_element 抛异常 → `Click failed: {e}`，不让 `Tools.execute` 通用兜底（`actions.py:171-173`）打成裸 `str(e)`。参照 navigate 的 try/except（`actions.py:229-240`）风格。
- **成功回显**：调 `_describe_click`，不传 `success=True`（`ActionResult.validate_success_requires_done` 校验器 `views.py:18-25` 对非 done 动作拒绝）。
- **回显字段选择**：`EnhancedDOMTreeNode`（`views.py:259-299`）没有 `.text` 字段（DOM 文本在子节点），但 `.attributes` 含 aria-label/placeholder 等、`.node_value` 对文本节点有值。优先级链覆盖常见 case，最坏回退到 `[TAG] at index N`，对 LLM 仍有定位价值。
- **SELECT 精确化**：`fetch_select_options(backend_id)` 经 `DOM.resolveNode` 精确定位元素，绕过"页面上有多个 select"的歧义，与 `get_element_coordinates` 第三层 fallback（`session.py:544-548`）使用同一 API，模式一致。回显多一个 `selected` 字段（grep 确认无测试断言旧格式，纯增益）。

---

### 4. `tests/test_click.py`（新建）

参照 `tests/test_go_back.py` 的 mock 模式（端到端 `Tools().execute("click", {...}, browser, browser_state=state)`，mock 边界是 `_get_element_by_index` + `browser.highlight_element/click_element/fetch_select_options`，**不碰 CDP 原语**；session 层用例用 `BrowserSession.__new__` + stub）。当前 click 无行为级单元测试（`tests/test_highlight.py:421-436` 仅断言 highlight 被 await，`tests/test_multi_act.py` 用 fake click 测守卫不跑真 handler）。

**测试用例清单**：

| 组 | 用例 | 断言要点 |
|---|---|---|
| 元素查找 | index 在 selector_map | 调 `highlight_element(42)` + `click_element(42)`；`error is None`；回显 |
| 元素查找 | index 不在 selector_map | 返回 `Element {N} not found in DOM state`；不调 highlight/click_element |
| 成功回显 | entry 无可识别文本 | `extracted_content` == `Clicked [DIV] at index 3`；`long_term_memory` 同 |
| 成功回显 | entry 有 aria-label | 回显含 aria-label 文本，以 `Clicked [BUTTON]` 开头 |
| 坐标失败 | `click_element` 返回 `False` | `error` 含 `Could not click element`；无回显（`extracted_content is None`） |
| CDP 异常 | `click_element` 抛 RuntimeError | `error` == `Click failed: target detached` |
| SELECT | entry 是 SELECT | `fetch_select_options(99)` 被调（精确 backend_id）；**不**调 click_element；回显含 option |
| SELECT 异常 | `fetch_select_options` 抛异常 | `error` == `Failed to read select options: CDP down` |
| session-鼠标序列 | `click_at(100,200)` | `dispatchMouseEvent` 被调 3 次，类型依次 `mouseMoved/mousePressed/mouseReleased`；pressed 含 button=left/clickCount=1 |
| session-正常点击 | coords 有 + 不遮挡 | `click_at(中心)` 被调；`_js_click` 不调；返回 True |
| session-遮挡回退 | coords 有 + 遮挡 | `click_at` 不调；`_js_click` 被调；返回 True |
| session-无坐标回退 | coords=None | `click_at` 不调；`get_element_coordinates` 被调；`_js_click` 被调；返回 True |
| session-全失败 | coords=None + JS 失败 | 返回 False；`click_at` 不调 |
| session-视口裁剪 | rect 中心超出视口 | `click_at` 收到裁剪后的坐标（`min(vw-1, ...)`） |
| session-最大 quad | 两个 quad，一个在视口内一个外 | `_best_quad_rect` 选视口内那个 |
| session-_best_quad_rect | viewport=None | 回退到第一个 quad |
| session-_get_viewport_size | getLayoutMetrics 返回 clientWidth/Height | 返回 `(w, h)`；异常返回 None |
| session-fetch_select_options | callFunctionOn 返回 list | 返回结构正确；`resolveNode` 传精确 backend_id；非 list 返回 [] |

代表性骨架：

```python
"""Tests for click: index lookup, SELECT branch, coordinate-fail error mapping,
success echo, mouseMoved sequence, occlusion JS fallback, viewport clipping.

Covers:
- index lookup: cache hit path through _get_element_by_index
- success echo: click_element returning True yields 'Clicked [...]' in
  extracted_content + long_term_memory (mirrors navigate/go_back style)
- coordinate-fail error mapping: click_element returning False (no coordinates
  + JS fallback failed) yields an explicit error instead of silent success
- CDP exception mapping: highlight/click raising -> friendly 'Click failed: ...'
- SELECT branch: scoped option fetch via fetch_select_options(backend_id),
  NOT the old querySelectorAll('select option') page-wide scan
- mouseMoved: click_at now emits mouseMoved -> mousePressed -> mouseReleased
  (browser-use default_action_watchdog.py:902-955 alignment)
- occlusion fallback: _is_element_occluded=True -> skip click_at, call _js_click
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tree_walker.browser.session import BrowserSession
from tree_walker.browser.views import (
	BrowserStateSummary,
	DOMRect,
	EnhancedDOMTreeNode,
	NodeType,
	SerializedDOMState,
)
from tree_walker.tools.actions import Tools


# ── Shared helpers ────────────────────────────────────────────────────────────


def _make_entry(
	*,
	tag: str = "BUTTON",
	backend_node_id: int = 42,
	attributes: dict[str, str] | None = None,
	node_value: str = "",
) -> EnhancedDOMTreeNode:
	"""A minimal EnhancedDOMTreeNode for selector_map entries."""
	return EnhancedDOMTreeNode(
		node_id=backend_node_id,
		backend_node_id=backend_node_id,
		node_type=NodeType.ELEMENT_NODE,
		node_name=tag.upper(),
		node_value=node_value,
		attributes=attributes or {},
	)


def _make_state(selector_map: dict[int, EnhancedDOMTreeNode]) -> BrowserStateSummary:
	"""Build a BrowserStateSummary with the given selector_map (cache path)."""
	return BrowserStateSummary(
		url="https://example.com",
		title="",
		dom_state=SerializedDOMState(
			_root=None,
			selector_map=selector_map,
			element_tree_text="",
		),
	)


def _make_browser(
	*,
	click_element_return: bool = True,
	click_element_side_effect=None,
	fetch_options_return=None,
	fetch_options_side_effect=None,
) -> MagicMock:
	"""Stub BrowserSession for action-layer tests (does NOT touch CDP)."""
	bs = MagicMock()
	bs.current_session_id = "sid"
	bs.current_target_id = "tid"
	if click_element_side_effect is not None:
		bs.click_element = AsyncMock(side_effect=click_element_side_effect)
	else:
		bs.click_element = AsyncMock(return_value=click_element_return)
	bs.highlight_element = AsyncMock()
	if fetch_options_side_effect is not None:
		bs.fetch_select_options = AsyncMock(side_effect=fetch_options_side_effect)
	else:
		bs.fetch_select_options = AsyncMock(
			return_value=fetch_options_return
			if fetch_options_return is not None
			else [{"value": "a", "text": "A", "selected": False}],
		)
	bs.get_state = AsyncMock(return_value=_make_state({}))
	return bs


# ── Element lookup ────────────────────────────────────────────────────────────


class TestClickElementLookup:
	@pytest.mark.asyncio
	async def test_index_in_cache_calls_click(self):
		entry = _make_entry(backend_node_id=42)
		state = _make_state({5: entry})
		browser = _make_browser()

		result = await Tools().execute("click", {"index": 5}, browser, browser_state=state)

		assert result.error is None
		browser.highlight_element.assert_awaited_once_with(42)
		browser.click_element.assert_awaited_once_with(42)

	@pytest.mark.asyncio
	async def test_missing_index_returns_error_no_click(self):
		state = _make_state({})  # index 5 absent
		browser = _make_browser()

		result = await Tools().execute("click", {"index": 5}, browser, browser_state=state)

		assert result.error is not None
		assert "5" in result.error
		browser.highlight_element.assert_not_awaited()
		browser.click_element.assert_not_awaited()


# ── Success echo ──────────────────────────────────────────────────────────────


class TestClickSuccessEcho:
	@pytest.mark.asyncio
	async def test_echoes_tag_and_index_when_no_text(self):
		entry = _make_entry(tag="DIV", backend_node_id=7)
		state = _make_state({3: entry})
		browser = _make_browser()

		result = await Tools().execute("click", {"index": 3}, browser, browser_state=state)

		assert result.error is None
		assert result.extracted_content == "Clicked [DIV] at index 3"
		assert result.long_term_memory == "Clicked [DIV] at index 3"

	@pytest.mark.asyncio
	async def test_echoes_aria_label_when_available(self):
		entry = _make_entry(
			tag="BUTTON", backend_node_id=7, attributes={"aria-label": "Submit form"},
		)
		state = _make_state({3: entry})
		browser = _make_browser()

		result = await Tools().execute("click", {"index": 3}, browser, browser_state=state)

		assert result.error is None
		assert "Submit form" in result.extracted_content
		assert result.extracted_content.startswith("Clicked [BUTTON]")


# ── Coordinate-fail error mapping ─────────────────────────────────────────────


class TestClickCoordinateFail:
	@pytest.mark.asyncio
	async def test_click_element_false_yields_explicit_error(self):
		"""No silent success when coordinates can't be obtained and JS fails."""
		entry = _make_entry(backend_node_id=42)
		state = _make_state({1: entry})
		browser = _make_browser(click_element_return=False)

		result = await Tools().execute("click", {"index": 1}, browser, browser_state=state)

		assert result.error is not None
		assert "Could not click" in result.error
		assert result.extracted_content is None
		assert result.long_term_memory is None

	@pytest.mark.asyncio
	async def test_cdp_exception_maps_to_friendly_error(self):
		entry = _make_entry(backend_node_id=42)
		state = _make_state({1: entry})
		browser = _make_browser(click_element_side_effect=RuntimeError("target detached"))

		result = await Tools().execute("click", {"index": 1}, browser, browser_state=state)

		assert result.error == "Click failed: target detached"


# ── SELECT branch ─────────────────────────────────────────────────────────────


class TestClickSelectBranch:
	@pytest.mark.asyncio
	async def test_select_uses_scoped_fetch_not_global_query(self):
		"""SELECT branch must call fetch_select_options(backend_id), NOT
		execute_js with querySelectorAll('select option') (page-wide bug)."""
		entry = _make_entry(tag="SELECT", backend_node_id=99)
		state = _make_state({2: entry})
		options = [{"value": "x", "text": "X", "selected": True}]
		browser = _make_browser(fetch_options_return=options)

		result = await Tools().execute("click", {"index": 2}, browser, browser_state=state)

		assert result.error is None
		assert "x" in result.extracted_content
		browser.fetch_select_options.assert_awaited_once_with(99)
		browser.click_element.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_select_fetch_error_is_friendly(self):
		entry = _make_entry(tag="SELECT", backend_node_id=99)
		state = _make_state({2: entry})
		browser = _make_browser(fetch_options_side_effect=RuntimeError("CDP down"))

		result = await Tools().execute("click", {"index": 2}, browser, browser_state=state)

		assert result.error == "Failed to read select options: CDP down"


# ── Session-layer: mouseMoved sequence ────────────────────────────────────────


class TestClickAtMouseSequence:
	def _make_session(self) -> tuple[BrowserSession, MagicMock]:
		s = BrowserSession.__new__(BrowserSession)
		s._highlight_settings = MagicMock(enabled=False, click_feedback_enabled=False)
		s.current_session_id = "sid"
		client = MagicMock()
		client.send.Input.dispatchMouseEvent = AsyncMock(return_value={})
		s.client = client
		return s, client

	@pytest.mark.asyncio
	async def test_emits_three_events_in_order(self, monkeypatch):
		async def _no_sleep(_):
			pass
		monkeypatch.setattr("tree_walker.browser.session.asyncio.sleep", _no_sleep)

		s, client = self._make_session()
		await s.click_at(100.0, 200.0)

		assert client.send.Input.dispatchMouseEvent.await_count == 3
		types = [
			call.args[0]["type"]
			for call in client.send.Input.dispatchMouseEvent.await_args_list
		]
		assert types == ["mouseMoved", "mousePressed", "mouseReleased"]


# ── Session-layer: click_element occlusion + viewport clip ────────────────────


class TestClickElementFallback:
	def _make_session(
		self, *, coords: DOMRect | None, occluded: bool, js_click_ok: bool,
		viewport: tuple[int, int] | None = None,
	) -> tuple[BrowserSession, MagicMock]:
		s = BrowserSession.__new__(BrowserSession)
		s._highlight_settings = MagicMock(enabled=False, click_feedback_enabled=False)
		s.current_session_id = "sid"
		client = MagicMock()
		client.send.DOM.scrollIntoViewIfNeeded = AsyncMock(return_value={})
		s.client = client
		s._get_viewport_size = AsyncMock(return_value=viewport)
		s.get_element_coordinates = AsyncMock(return_value=coords)
		s._is_element_occluded = AsyncMock(return_value=occluded)
		s._js_click = AsyncMock(return_value=js_click_ok)
		s.click_at = AsyncMock()
		return s, client

	@pytest.mark.asyncio
	async def test_normal_click_skips_js_fallback(self):
		s, _ = self._make_session(
			coords=DOMRect(x=10, y=20, width=100, height=50), occluded=False, js_click_ok=True,
		)
		ok = await s.click_element(42)
		assert ok is True
		s.click_at.assert_awaited_once_with(60, 45)  # 中心 (60,45)
		s._js_click.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_occluded_triggers_js_fallback(self):
		s, _ = self._make_session(
			coords=DOMRect(x=10, y=20, width=100, height=50), occluded=True, js_click_ok=True,
		)
		ok = await s.click_element(42)
		assert ok is True
		s.click_at.assert_not_awaited()
		s._js_click.assert_awaited_once_with(42)

	@pytest.mark.asyncio
	async def test_no_coords_triggers_js_fallback(self):
		s, _ = self._make_session(coords=None, occluded=False, js_click_ok=True)
		ok = await s.click_element(42)
		assert ok is True
		s.click_at.assert_not_awaited()
		s.get_element_coordinates.assert_awaited_once_with(42, viewport=None)
		s._js_click.assert_awaited_once_with(42)

	@pytest.mark.asyncio
	async def test_total_failure_returns_false(self):
		s, _ = self._make_session(coords=None, occluded=False, js_click_ok=False)
		assert await s.click_element(42) is False
		s.click_at.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_center_clipped_to_viewport(self):
		# rect 中心 x=950 超出 vw=800 -> 裁剪到 799
		s, _ = self._make_session(
			coords=DOMRect(x=900, y=10, width=100, height=20),
			occluded=False, js_click_ok=True, viewport=(800, 600),
		)
		await s.click_element(42)
		s.click_at.assert_awaited_once_with(799, 20)  # x 裁剪，y 不变


# ── Session-layer: _best_quad_rect ────────────────────────────────────────────


class TestBestQuadRect:
	def test_picks_largest_viewport_intersection(self):
		# quad A: (0,0,10,10) 在视口内；quad B: (5000,5000,10,10) 在视口外
		quads = [
			[0, 0, 10, 0, 10, 10, 0, 10],
			[5000, 5000, 5010, 5000, 5010, 5010, 5000, 5010],
		]
		rect = BrowserSession._best_quad_rect(quads, (800, 600))
		assert rect == DOMRect(x=0, y=0, width=10, height=10)

	def test_falls_back_to_first_quad_without_viewport(self):
		quads = [[0, 0, 10, 0, 10, 10, 0, 10]]
		rect = BrowserSession._best_quad_rect(quads, None)
		assert rect is not None

	def test_returns_none_for_empty(self):
		assert BrowserSession._best_quad_rect([], (800, 600)) is None


# ── Session-layer: fetch_select_options ───────────────────────────────────────


class TestFetchSelectOptions:
	def _make_session(self, options_value) -> tuple[BrowserSession, MagicMock]:
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid"
		client = MagicMock()
		client.send.DOM.resolveNode = AsyncMock(
			return_value={"object": {"objectId": "obj-1"}},
		)
		client.send.Runtime.callFunctionOn = AsyncMock(
			return_value={"result": {"value": options_value}},
		)
		s.client = client
		return s, client

	@pytest.mark.asyncio
	async def test_scoped_to_specific_backend_id(self):
		s, client = self._make_session([{"value": "a", "text": "A", "selected": True}])
		options = await s.fetch_select_options(99)
		assert options == [{"value": "a", "text": "A", "selected": True}]
		client.send.DOM.resolveNode.assert_awaited_once_with(
			{"backendNodeId": 99}, session_id="sid",
		)

	@pytest.mark.asyncio
	async def test_returns_empty_list_on_non_list_value(self):
		s, _ = self._make_session(None)
		assert await s.fetch_select_options(1) == []
```

> 加速：`TestClickAtMouseSequence` 用 monkeypatch 把 `tree_walker.browser.session.asyncio.sleep` 替换为 no-op，避免每个用例睡 0.43s。
> 回归（已验证）：`tests/test_highlight.py:394` `_make_mock_browser_session` 的 `click_element=AsyncMock()` 返回 truthy MagicMock → `test_click_triggers_highlight`（`result.error is None`，421-436 行）仍过；`test_highlight_failure_does_not_block_action`（484-508 行，只断言 highlight 被 await）仍过。注意：若该文件存在 SELECT click 用例需补 `fetch_select_options` 桩（当前未见）。

---

## 附带文档同步（建议本次一并做）

更新 `docs/Tools技术细节/04_动作清单与CDP映射.md` 的 4.1 click 节：

- **主要逻辑代码块**（行 66-81）：替换为新 `_action_click`（SELECT 精确分支 `fetch_select_options(backend_id)`、try/except 包裹、bool 信号映射、`_describe_click` 回显），并把行号引用 `actions.py:203-217` 更正为 `actions.py:300-314`。
- **CDP 调用清单**（行 87-94）：`Input.dispatchMouseEvent` × 2 → **× 3**（新增 `mouseMoved`）；新增 `DOM.resolveNode` + `Runtime.callFunctionOn` 三处用途（`_is_element_occluded` / `_js_click` / `fetch_select_options`）；新增 `Page.getLayoutMetrics`（`_get_viewport_size`）。
- **注意事项**（行 96）补充：坐标失败 + JS 回退失败 → 明确 `ActionResult(error=...)`，不再静默成功；成功回显 `Clicked [TAG] {text} at index N`；鼠标序列为 mouseMoved → mousePressed → mouseReleased（对齐 browser-use）；SELECT 分支用 `fetch_select_options(backend_id)` 精确查指定 index 的 select（修正原文"已知限制"——dropdown_options / select_dropdown 仍有全页 bug，下次 PR 修）；遮挡检查覆盖 label 包 input、固定 header、弹窗；坐标取最大交集 quad + 视口裁剪。
- **4.3.1 节"鼠标点击坐标计算"**：保留三层 fallback，补注"坐标拿到后通过 `_is_element_occluded` 判遮挡，被遮挡或 None 走 `_js_click` 回退；Method 1 取最大交集 quad；中心裁剪到视口；最终 bool 信号上浮到 action 层"。
- **4.4 CDP 调用总览矩阵 / 调用频次**：`dispatchMouseEvent` 行更新、新增 `mouseMoved` 与 `getLayoutMetrics` 行；click 调用频次 "4-8" → "5-10"。

---

## 技术决策说明（要点）

- **范围 = 全面层级（用户已选）**：在"修正确性 + 鲁棒性"基础上，额外对齐 browser-use 的坐标选取（最大交集 quad + 视口裁剪）。完整覆盖正确性 bug 与 browser-use 已验证的鲁棒性模式，iframe 路由 / post-click 等待 / coordinate 模式因改动大或破坏设计哲学而留作未来。
- **session 层返回 bool、action 层做策略**：`BrowserSession.click_element` 成功返回 `True` / 失败（坐标 + JS 都失败）返回 `False` / CDP 异常抛出，保持纯粹；`_action_click` 把三种情况映射为对 LLM 友好的 `ActionResult`（对齐 navigate/go_back 的分层）。与 `go_back` 返回 `str | None`、`navigate` 返回 `target_id | None` 风格一致。
- **mouseMoved 加在 `click_at` 而非 `click_element`**：`click_at` 是"在指定坐标发鼠标序列"的原子操作，mouseMoved 是该序列的一部分；`click_element` 是高层操作，不应关心序列细节。未来若有其他调用方用 `click_at`（如 coordinate 模式）也自动受益。
- **遮挡检查 / JS 回退 / fetch_select_options 放 session 层**：纯 CDP 编排，与 LLM 无关。action 层只看到 bool / list，无需知道是几何点击还是 JS 点击。
- **`_get_viewport_size` 只取一次并透传**：`click_element` 取视口后传给 `get_element_coordinates(viewport=...)` 复用于 quad 选择，并在本函数内复用于中心裁剪——每次点击只一次 `Page.getLayoutMetrics`。
- **错误映射不复用 `_map_navigation_error`**：click 失败语义不是"站点不可达"，直述 `Click failed: {e}` 更诚实；navigate 的 5 个网络错误码标记对元素点击不适用。
- **不引入新模块常量**：mouseMoved 的 sleep 值（0.05/0.08）直接写在 `click_at` 内（与现有 0.3 同风格）；如未来需独立调参可再加 `_CLICK_AT_MOUSE_MOVED_DELAY` 等。

## 已知限制（本次不处理，留作未来选项）

- **iframe 会话路由**：browser-use 的 `cdp_client_for_node`（`session.py:3840-3896`）按 node.session_id/frame_id/target_id 路由到子 CDP session，让 iframe 内元素的 backendNodeId/坐标/dispatch 全在子 session 内解析。本项目 DOM 管线对跨源 iframe 支持有限（`_connect` 仅 `setAutoAttach` 但不维护子 session 表），引入需先做 iframe target 发现与 session 路由表，属独立专题。本次的 JS 回退对部分 iframe 场景已能兜底（`this.click()` 在元素所在 frame 的 session 内执行），但 `get_element_coordinates` 仍用主 frame session，跨源 iframe 内元素坐标可能失败——届时走 JS 回退。
- **post-click 等待 / click 健康检查**：click 不是页面级动作，没有"空 DOM = 失败"的干净语义（navigate/go_back 才需要）。browser-use 自己在 click 后也不做通用等待（依赖 agent loop 下一轮 `get_state` 自行感知）。强加等待会拖慢无明显收益。
- **coordinate_x/y 双模式注册**：browser-use 有 index-only 与坐标两套 schema。本项目设计哲学是"index 驱动"（DOM 树展示 `[index]`，LLM 不输出裸坐标），新增 coordinate 模式会破坏 system prompt 一致性。如未来需要可在独立 PR 加。
- **dropdown_options / select_dropdown 的全页 select bug**：与 click 的 SELECT 分支同源，但分属不同动作。本次新增的 `fetch_select_options`（及未来 `set_select_value(backend_id, value)`）可在下个 PR 统一修复，避免本次范围蔓延。
- **JS `.click()` 不触发鼠标事件序列**：部分反爬站点可能识别"非人类点击"；某些 disabled button 的 `.click()` 在 JS 层被忽略。这是 browser-use 也有的已知限制（`_click_element_node_impl:992` 注释）。JS 回退是几何点击失败的最后兜底，失败时返回 False → action 层明确报错，LLM 可换元素重试，比旧实现（静默成功）严格更好。
- **`_best_quad_rect` 用外接矩形交集而非精确多边形裁剪**：browser-use 对 quad 做精确多边形求交。本实现简化为外接矩形交集——对绝大多数布局（矩形元素）等价，对斜切/旋转元素的 quad 选择可能略有偏差，但配合"取交集最大者 + 视口裁剪 + 遮挡检查"三重保障，实际影响可忽略。

---

## 验证步骤

1. **跑新增单元测试**（CLAUDE.md 要求改动后必须跑）：
   ```powershell
   uv run python -m pytest tests/test_click.py -x -v
   ```
2. **回归 highlight / multi_act / input_text 测试**（确认 `click_element` 签名 None→bool 不破坏既有调用方）：
   ```powershell
   uv run python -m pytest tests/test_highlight.py tests/test_multi_act.py -x -v
   uv run python -m pytest tests/test_input_text_clear.py tests/test_input_text_framework.py -x -v
   ```
3. **全量测试 + 覆盖率**（项目目标 >85%）：
   ```powershell
   uv run python -m pytest tests/ -x -v
   uv run python -m pytest tests/ --cov=tree_walker.tools --cov=tree_walker.browser --cov-report=term-missing
   ```
4. **手动验证坐标失败路径**（可选，连真实浏览器）：
   ```powershell
   uv run python -c "import asyncio; from tree_walker.browser.session import BrowserSession; s=BrowserSession(ws_url='http://localhost:9222'); asyncio.run(s.start()); print(asyncio.run(s.click_element(999999)))"
   ```
   预期：不存在的 backendNodeId → 返回 `False`（旧实现静默成功）；此前 action 层显示 `OK`，现改为 `error="Could not click element N ..."`。
