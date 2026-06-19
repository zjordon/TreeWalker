# scroll 工具完善方案

> 参照 browser-use 的 `scroll` 实现，完善本项目 `scroll` 工具。
>
> 本文为**提案文档**（不改源码），给出差距分析、分级改进方案与可落地的代码片段，供后续实施直接参照。范围：**核心改进（scroll 工具本身：成功回显 + 当轮到底提示 + 参数校验 + 异常软降级 + 测试）+ P1 进阶蓝图（元素内滚动 / 平滑滚动）**。

---

## 1. 背景与目标

参照对象：

- browser-use：`browser_use/tools/service.py` 的 `scroll` 动作 + `browser_use/dom/views.py` 的 `get_scroll_info`（边界信号归属 DOM 状态层，action 内不检测）。
- 本项目：`src/tree_walker/tools/actions.py:593-595` 的 `_action_scroll`、`src/tree_walker/browser/session.py:1218-1240` 的 `scroll`、`src/tree_walker/tools/models.py:27-30` 的 `ScrollParams`。
- 对齐基准：`close_tab`（`docs/tools-optimize/close.md`）/ `switch_tab`（`docs/tools-optimize/switch.md`）已建立的"标签页/导航类动作"新规范——成功回显、Pydantic `ge/le` 校验、try/except 软降级、补测试。

**既有优势（关键前提）**：TreeWalker 已有与 browser-use 同构的 DOM 滚动信息层——`src/tree_walker/browser/views.py:422-539` 的 `is_actually_scrollable` / `scroll_info` / `get_scroll_info_text`，由 `src/tree_walker/browser/serializer.py:845-849` 渲染成 `(scroll: 45%, 3.4 pages below, total: 8.5 pages)` 附在可滚动元素上喂给 LLM。**即"边界信号归 DOM 状态层"这一架构已与 browser-use 对齐**，scroll action 不必重建边界检测——这是本方案能"轻量化"的前提。

目标：

1. **补成功回显**：滚动后回显方向、量、当前位置（百分比），对齐 `navigate`/`click`/`go_back`/`switch_tab`/`close_tab`。
2. **收紧参数**：`amount` 加 `ge=1, le=10`，`direction` 补 description，保持 int / default=3 不破坏 tool schema。
3. **当轮到底提示**：滚动后做**一次** `Runtime.evaluate` 读 `{scrollY, scrollHeight, clientHeight}`，若实际未移动（已在边界）在回显追加 `(already at bottom)`——比 browser-use 早一轮告知 LLM，省一个空滚回合。
4. **异常软降级**：CDP 调用失败 → `ActionResult(error="Scroll failed: ...")`（scroll **非幂等**，失败应明确报错而非软成功，区别于 `close_tab`）。
5. **补齐测试**：新增 `tests/test_scroll.py`，对齐 `tests/test_close_tab.py` 的三层结构。

---

## 2. 现状对比

| 维度 | browser-use `scroll` | TreeWalker `scroll`（当前） | 差距 |
|---|---|---|---|
| 成功回显 | `extracted_content="🔍 Scrolled down 2.5 pages"`（无位置/边界） | 裸 `ActionResult()` → LLM 只见 `"OK"` | **无回显** |
| 参数校验 | `pages: float=1.0`、`down: bool=True`、`index: int\|None`（无 ge/le） | `amount: int=3`、`direction: Literal`（无 ge/le，direction 无 description） | **缺边界约束 + description** |
| 滚动机制（页面级） | `Input.synthesizeScrollGesture`（x/y 居中，`yDistance`，`speed=50000` 近乎瞬时） | `Input.dispatchMouseEvent(mouseWheel)`（`deltaY=amount×clientHeight`） | 机制不同但等效；mouseWheel 对 wheel 事件监听器更友好（更接近真实用户） |
| 边界检测归属 | **DOM 状态层**（`dom/views.py:get_scroll_info`，渲染成元素属性喂 LLM）；action 内**不检测** | **DOM 状态层已有**（`views.py:422-539`，渲染成 `(scroll: 45%, ... pages below)`） | 已对齐 browser-use；**action 层缺当轮到底提示** |
| 多页 / 小数滚动 | `pages≥1.0` 循环 dispatch（每页 `sleep 0.15`）+ 小数部分 | 单次 dispatch 整 `amount×clientHeight` | TreeWalker 更简单瞬时；无小数支持（int） |
| 平滑滚动 | 否（瞬时，`speed=50000`） | 否（瞬时） | 已对齐 |
| 滚动到元素 | `index` 参数（iframe→resolveNode+callFunctionOn；普通→getBoxModel+mouseWheel） | 无（但 `click_element` 的 `DOM.scrollIntoViewIfNeeded` 可复用） | **缺 index 参数（P1）** |
| 异常处理 | action 内 catch → 软降级意图（JS fallback） | 无 try/except，CDP 异常直接外抛 | **缺异常处理** |
| 等待机制 | 循环内 `sleep(0.15)`（分页滚动节奏） | `sleep(0.3)`（滚动后渲染窗口） | 语义不同；`_wait_for_page_settle` **不适用**（轮询 readyState，scroll 不改 readyState） |
| 视口高度 fallback | `cssVisualViewport.clientHeight` fallback **1000** | fallback **800** | 数值差异（对齐到 1000） |
| 测试 | 有 | **无**（`tests/` 下无 `test_scroll.py`） | 缺测试（覆盖率缺口） |

---

## 3. 改进方案（分级）

### P0 核心 —— scroll 工具本身

#### G1｜成功回显（对齐 navigate/click/close_tab）

滚动成功后回显 `Scrolled {direction} {amount} viewport-heights`，写入 `extracted_content` + `long_term_memory`，并 `logger.info`。

文案要点：

- 用 **"viewport-heights"** 而非 "pages"——避免与 DOM 层 `pages_below`/`total_pages` 口径混淆（amount 是"滚了几屏"，`pages_below` 是"还剩几屏内容"，二者不同）。
- 不截断：回显文案本身短（<100 字符），`ActionResult.display_max_chars=500`（`agent/views.py:9`）无压力。

#### G2｜当轮到底检测（并入 G1 回显，1 次 `Runtime.evaluate`）

滚动后做**一次** `Runtime.evaluate` 读 `{scrollY, scrollHeight, clientHeight}`，计算：

- `vertical_percentage = round(scrollY / max(scrollHeight - clientHeight, 1) * 100, 1)`（`0%`=顶部、`100%`=底部；内容短于一屏时直接 `100%`）
- `at_edge`：direction=`down` 时 `scrollY + clientHeight >= scrollHeight - 1`；direction=`up` 时 `scrollY <= 1`

若 `at_edge` 为真，回显追加 `(already at {direction}, no further content)`——当轮告诉 LLM 已到边界，省掉下一轮空滚。回显同时带百分比 `(XX.X% down)`，让 LLM 知道当前在文档的什么位置。

**为什么不与 DOM 层重复**：DOM 层（`get_scroll_info_text`）会在**下一轮** `get_state` 渲染 `pages_below=0`，LLM 那时才知道到底。G2 把这个信号提前到**当轮**，只回显**布尔边界信号 + 百分比**，**不**回显 `pages_below`/`total_pages`（那是 DOM 层的职责），避免双源信息冗余让 LLM 困惑。

> 成本：+1 次 `Runtime.evaluate`（<5ms）。收益：省下一个"空滚回合"（1 次 LLM 调用 + 1 次 `get_state`，秒级），性价比碾压，故从可选 P1 升到 P0。

#### G3｜`ScrollParams` 校验收紧（`models.py:27-30`）

`amount` 加 `ge=1, le=10`；`direction` 补 description。保持 int / default=3 不变（不破坏 tool schema，不改 float）。

```python
class ScrollParams(BaseModel):
	model_config = ConfigDict(extra="forbid")
	amount: int = Field(
		default=3, ge=1, le=10,
		description="Number of viewport-heights to scroll (1-10). "
		            "Check the scroll info on scrollable elements in the DOM tree "
		            "(e.g. '3.4 pages below') to judge how much remains before scrolling.",
	)
	direction: Literal["up", "down"] = Field(
		default="down",
		description="Scroll direction: 'down' (default) or 'up'.",
	)
```

> `ge=1` 拒绝 `amount=0`/负数；`le=10` 拒绝过大值（browser-use 描述里 `pages=10` 到底是合理上限，`le=20` 易过冲且单次巨量 `deltaY` 在某些站点触发异常动画/卡顿）。保持 int 的副作用：`amount=2.5` 被 Pydantic 拒——有意为之（对齐 browser-use 的 `pages: float` 会引入"0.3 页"无意义调用），LLM 需精细控制时看 DOM 层 `pages_below` 小数值。

#### G4｜异常软降级（scroll 非幂等 → error 而非软成功）

区别于 `close_tab`（幂等，失败软成功），scroll **不是幂等**——CDP 调用失败意味着滚动没发生，LLM 必须知道。所以用 `ActionResult(error=f"Scroll failed: {e}")` 而非软成功。

**改写后的 `_action_scroll`（替换 `actions.py:593-595`）：**

```python
async def _action_scroll(self, params: dict, browser: BrowserSession) -> ActionResult:
	direction = params.get("direction", "down")
	amount = int(params.get("amount", 3))
	try:
		# G5: scroll 现在返回 {vertical_percentage, at_edge}（G2 位置读取）
		position = await browser.scroll(direction, amount)
	except Exception as e:
		# G4: scroll 非幂等，CDP 失败=没滚，必须报 error（区别于 close_tab 的软成功）
		logger.warning("scroll(%s, %d) failed: %s", direction, amount, e)
		return ActionResult(error=f"Scroll failed: {e}")
	# G1 + G2: 回显方向/量 + 当前位置；已到边界则当轮提示
	memory = f"Scrolled {direction} {amount} viewport-heights"
	pct = position.get("vertical_percentage")
	if pct is not None:
		memory += f" ({pct}% down)"
	if position.get("at_edge"):
		memory += f" (already at {direction}, no further content)"
	logger.info(memory)
	return ActionResult(extracted_content=memory, long_term_memory=memory)
```

#### G5｜`BrowserSession.scroll` 返回位置信息（`session.py:1218-1240`）

`scroll()` 改为返回 `dict`，包含 `vertical_percentage` 和 `at_edge`（由 G2 的 `Runtime.evaluate` 填充）。**保留固定 `sleep`（0.3→0.2s），不加 `_wait_for_page_settle`**（语义不匹配，见决策备注）。视口 fallback 800→1000 对齐 browser-use。

```python
async def scroll(
	self, direction: str = "down", amount: int = 3,
) -> dict:
	"""Scroll the page by a number of viewport heights.

	Returns ``{vertical_percentage, at_edge}`` from a post-scroll
	``Runtime.evaluate``，让 action 层在当轮回显当前位置并在已到边界时提示，
	避免 LLM 在页面底部反复空滚。位置读取失败时退化为
	``{vertical_percentage: None, at_edge: False}``，不影响滚动本身。
	"""
	sid = self.current_session_id
	metrics = await self.client.send.Page.getLayoutMetrics({}, session_id=sid)
	viewport = metrics.get("cssVisualViewport", {})
	viewport_height = viewport.get("clientHeight", 1000)  # fallback 对齐 browser-use
	viewport_width = viewport.get("clientWidth", 1280)
	delta = amount * viewport_height
	if direction == "up":
		delta = -delta

	await self.client.send.Input.dispatchMouseEvent(
		{
			"type": "mouseWheel",
			"x": viewport_width / 2,
			"y": viewport_height / 2,
			"deltaX": 0,
			"deltaY": delta,
		},
		session_id=sid,
	)
	# 滚动动画 + 懒加载触发的渲染窗口；不用 _wait_for_page_settle——
	# 它轮询 readyState，而 scroll 不改 readyState，会立即返回等于无等待。
	await asyncio.sleep(0.2)

	# G2: 一次 Runtime.evaluate 读当前位置 + 判边界
	# （documentElement/body 取 max，兼容 quirks 模式滚动挂在 body 的页面）
	position = {"vertical_percentage": None, "at_edge": False}
	try:
		result = await self.client.send.Runtime.evaluate(
			{
				"expression": (
					"(() => {"
					"  const d = document.documentElement, b = document.body;"
					"  const sy = Math.max(d.scrollTop || 0, b ? b.scrollTop || 0 : 0);"
					"  const sh = Math.max(d.scrollHeight || 0, b ? b.scrollHeight || 0 : 0);"
					"  const ch = d.clientHeight || window.innerHeight || 0;"
					"  const max = sh - ch;"
					"  const pct = max > 0 ? (sy / max) * 100 : 100;"
					"  return JSON.stringify({ sy, sh, ch, pct });"
					"})()"
				),
				"returnByValue": True,
			},
			session_id=sid,
		)
		import json
		val = json.loads(result.get("result", {}).get("value") or "{}")
		sy, sh, ch = val.get("sy", 0), val.get("sh", 0), val.get("ch", 0)
		max_top = sh - ch
		position["vertical_percentage"] = (
			round((sy / max_top) * 100, 1) if max_top > 0 else 100.0
		)
		if direction == "down":
			position["at_edge"] = sy + ch >= sh - 1
		else:
			position["at_edge"] = sy <= 1
	except Exception:
		# 位置读取失败不影响滚动本身——回显退化为基础文案
		pass
	return position
```

> `import json` 在 `session.py` 已是模块级依赖（`execute_js` 等多处用到），实际落地时无需内联 import，这里仅为片段自包含。

#### G6｜新增 `tests/test_scroll.py`（对齐 `tests/test_close_tab.py` 三层结构）

> CLAUDE.md 要求：改功能必须同步加测试、覆盖率 >85%。参照 `tests/test_close_tab.py` / `tests/test_switch_tab.py` 的既有模式：action 层用 `_make_browser` 桩（`MagicMock` + `AsyncMock`，经 `Tools().execute` 调用）；param model 层校验 `ValidationError`；session 层用 `BrowserSession.__new__` 绕过 `__init__` + `MagicMock` client。所有 async 测试显式标 `@pytest.mark.asyncio`（项目无 `asyncio_mode=auto`）。

| 测试类 | 用例 |
|---|---|
| `TestScrollAction` | ① 默认参数（down, amount=3）成功回显含 `Scrolled down 3 viewport-heights`、`extracted_content == long_term_memory`、`browser.scroll.assert_awaited_once_with("down", 3)`；② direction=up + amount=5 透传 + 回显含 `Scrolled up 5`；③ 位置回显（scroll 返回 `{vertical_percentage:45.0, at_edge:False}`）→ 含 `(45.0% down)`、**不含** `(already at`；④ 到底（down, at_edge=True, pct=100.0）→ 同时含 `(100.0% down)` 和 `(already at down, no further content)`；⑤ 到顶（up, at_edge=True）→ 含 `(already at up`；⑥ 位置读取退化（`vertical_percentage=None`）→ 回显仅 `Scrolled down 3 viewport-heights`，无百分比括号；⑦ CDP 异常（`browser.scroll` side_effect）→ `result.error == "Scroll failed: ..."`、`extracted_content is None`（**非软成功**，区别 close_tab）；⑧ `browser.get_state.assert_not_awaited()`（动作不应触发全量 DOM 抓取） |
| `TestScrollParams` | 默认值 amount=3/direction="down"；边界 1、10 被接受；amount=0 被拒（`ge=1`）；amount=11 被拒（`le=10`）；负数被拒；direction 非 up/down 被拒（Literal）；`extra="forbid"` 拒绝未知字段；amount 必须 int（`amount=2.5` 被拒） |
| `TestScrollSession` | 向下滚动 → mouseWheel `deltaY == 3*clientHeight`（正）、`deltaX==0`、x/y 在视口中心；向上 → `deltaY == -(3*clientHeight)`；scroll 后 `Runtime.evaluate` 被 await 一次且 expression 含 `scrollTop`/`scrollHeight`；到底判定（`{sy:9000,sh:10000,ch:1000}` → at_edge=True、pct=100.0）；未到底（`{sy:3000,...}` → at_edge=False、pct=33.3）；到顶（up, `{sy:0}` → at_edge=True）；位置读取异常（`Runtime.evaluate` side_effect）→ `scroll()` 不抛、返回 `{None, False}` 退化；mouseWheel 本身失败 → `scroll()` 抛 RuntimeError（由 action G4 catch）；fallback 视口（`getLayoutMetrics` 无 `cssVisualViewport`）→ deltaY 用 1000 |

action 层桩（复用 `test_close_tab.py` 的 `_make_browser` 模式）：

```python
def _make_browser(
	*, scroll_return: dict | None = None, scroll_raises: Exception | None = None,
) -> MagicMock:
	bs = MagicMock()
	if scroll_raises:
		bs.scroll = AsyncMock(side_effect=scroll_raises)
	else:
		bs.scroll = AsyncMock(
			return_value=scroll_return or {"vertical_percentage": 45.0, "at_edge": False},
		)
	bs.get_state = AsyncMock()  # 仅断言未被调用
	return bs
```

session 层桩要点（`Runtime.evaluate` 的返回值要用 `JSON.stringify(...)` 包一层，因为 JS 用了 `return JSON.stringify({...})` + `returnByValue=True`，mock 时把 `result.value` 设成 JSON 字符串）：

```python
def _make_session(
	self, *, client_height: int = 800, client_width: int = 1280,
	scroll_pos: dict | None = None, eval_raises: Exception | None = None,
) -> tuple[BrowserSession, MagicMock]:
	s = BrowserSession.__new__(BrowserSession)
	s.current_session_id = "sid-1"
	scroll_pos = scroll_pos or {"sy": 3000, "sh": 10000, "ch": 1000}
	client = MagicMock()
	client.send.Page.getLayoutMetrics = AsyncMock(return_value={
		"cssVisualViewport": {"clientHeight": client_height, "clientWidth": client_width},
	})
	client.send.Input.dispatchMouseEvent = AsyncMock(return_value={})
	if eval_raises:
		client.send.Runtime.evaluate = AsyncMock(side_effect=eval_raises)
	else:
		client.send.Runtime.evaluate = AsyncMock(return_value={
			"result": {"value": json.dumps(scroll_pos)},
		})
	s.client = client
	return s, client
```

### P1 增强 —— 对齐 browser-use 的进阶能力（本次仅画蓝图）

#### G7｜`index` 参数支持元素内滚动

`ScrollParams` 加 `index: int | None = None`。`index` 为空 → 页面级滚动（当前路径）；`index` 非空 → 元素内滚动。元素内路径复用 `click_element` 已有的坐标获取链（`DOM.scrollIntoViewIfNeeded` 见 `session.py:710-713` + `get_element_coordinates` 取中心），再在该坐标 dispatch `mouseWheel`——比 browser-use 的 iframe 分支（`DOM.resolveNode`+`Runtime.callFunctionOn`）更通用（不区分 iframe/普通元素）。代价：对 iframe 内滚动可能不生效，需后续验证。

#### G8｜平滑滚动选项

`ScrollParams` 加 `smooth: bool = False`。`True` 时把单次大 `deltaY` 拆成 N 次小 `deltaY` 循环 dispatch（每次 `sleep ~0.05s`），模拟 browser-use 的分页循环。代价是延迟，默认关闭（LLM 通常不需要平滑视觉）。

### 决策备注

- **`amount` 保持 int / default=3**：不破坏 tool schema；DOM 层 `pages_below` 已提供小数精度，LLM 需精细控制时看 DOM 状态。不改 float。
- **`le=10` 而非 20**：browser-use 描述里 `pages=10` 到底是合理上限；`le=20` 易过冲且单次巨量 `deltaY` 在某些站点触发异常动画/卡顿。
- **`_wait_for_page_settle` 不适用 scroll**：它轮询 `document.readyState == "complete"`（`session.py:482-508`，通过 `Runtime.evaluate("document.readyState")`），而 scroll **不改变 readyState**（页面滚动前后都是 `complete`），所以它会**立即返回**等于无等待。scroll 的 `sleep(0.2)` 是给"滚动动画 + 懒加载触发"的渲染窗口，语义不同。懒加载的内容在下一轮 `get_state` 自然出现，不需要 scroll 内长等待。保留固定 sleep + 注释。
- **G2 与 DOM 层不重复**：G2 只回显**布尔边界 + 百分比**，**不**回显 `pages_below`/`total_pages`（那是 DOM 层 `get_scroll_info_text` 的职责，下一轮渲染）。两源维度不同（当轮 vs 下一轮、布尔 vs 数值），不冗余。
- **scroll 异常用 error 而非软成功**（区别于 close_tab）：scroll 非幂等，CDP 失败=滚动没发生，LLM 必须知道；close_tab 幂等，目标不存在=已达成。
- **mouseWheel 保留不改 synthesizeScrollGesture**：两者等效；mouseWheel 更接近真实用户、触发 wheel 事件监听器，某些站点用 wheel 事件做无限滚动加载时更可靠。
- **视口 fallback 800 → 1000**：对齐 browser-use；仅影响 `getLayoutMetrics` 不返回 `cssVisualViewport` 的极端情况（几乎不发生），无破坏性。
- **G2 在 SPA / 虚拟滚动 / iframe 的局限**：虚拟滚动列表（React Virtualized、社交 timeline）的 `scrollHeight` 随滚动动态变化，G2 读到的是瞬间快照，`at_edge` 可能瞬真瞬假；SPA 路由切换后读到的可能是新页面；主滚动在 iframe 内时读顶层 `documentElement` 拿不到 iframe 内的 scrollY。三者均由 `at_edge` 仅作"提示非权威"消化——DOM 层 `pages_below` 下一轮给更准信号；iframe 主滚动留待 P1 G7。文档与代码注释均注明此局限。
- **`terminates_sequence`**：scroll 当前在 `ACTION_DEFINITIONS`（`models.py:161`）为 `False`，保持不变（对齐 browser-use `scroll` 的 `terminates_sequence=False`）。

---

## 4. CDP 调用清单（变更后）

| Action | CDP 命令 | 变化 |
|---|---|---|
| `scroll`（页面级） | `Page.getLayoutMetrics` + `Input.dispatchMouseEvent(mouseWheel)` + **`Runtime.evaluate`（G2 新增，读 scrollY/scrollHeight/clientHeight）** | **+1 次 `Runtime.evaluate`**（读位置，失败不影响滚动）；视口 fallback 800→1000；`sleep` 0.3→0.2 |
| `scroll`（P1 元素内，`index` 非空） | `DOM.scrollIntoViewIfNeeded` + `get_element_coordinates`（取中心） + `Input.dispatchMouseEvent(mouseWheel)` + `Runtime.evaluate` | P1 新增，本次不实现 |

---

## 5. 涉及文件清单

| 文件 | 改动 | 对应改进 |
|---|---|---|
| `src/tree_walker/tools/actions.py` | `_action_scroll` 重写（G1 回显 + G2 位置回显 + G4 try/except） | G1 / G2 / G4 |
| `src/tree_walker/tools/models.py` | `ScrollParams.amount` 加 `ge=1, le=10`，`direction` 补 description（int/default=3 不变） | G3 |
| `src/tree_walker/browser/session.py` | `scroll()` 改返回 `dict`（含 `vertical_percentage`/`at_edge`），+1 次 `Runtime.evaluate`，fallback 800→1000，`sleep` 0.3→0.2 | G2 / G5 |
| `tests/test_scroll.py` | **新建**（三层：action / param / session） | G6 |
| `docs/Tools技术细节/04_动作清单与CDP映射.md` | 4.16 节同步更新回显 / 位置读取 / 异常处理描述并刷新过时行号（文档写 `actions.py:229-231` / `session.py:725-747`，实际 `actions.py:593-595` / `session.py:1218-1240`） | 文档同步（实施后） |

---

## 6. 实施后的验证

- 新增/相关测试：`uv run python -m pytest tests/test_scroll.py -x -v`
- 全量回归：`uv run python -m pytest tests/ -x -v`
- 覆盖率（CLAUDE.md 目标 >85%）：`uv run python -m pytest tests/ --cov=tree_walker.tools --cov=tree_walker.browser`
- 手测：
  1. 打开长页面（如 Wikipedia 长文，或本地 `<body style="height:8000px">`）；
  2. 调 `scroll {"direction": "down", "amount": 3}` → 回显 `Scrolled down 3 viewport-heights (XX.X% down)`，**不含** `already at`；并核对 DOM 树可滚动元素的 `(scroll: XX%, X.X pages below)` 与回显百分比一致（两源交叉验证）；
  3. 连续 `scroll {"amount": 10}` 到底 → 最后一次回显含 `(already at down, no further content)`（**当轮**就知道到底）；
  4. 底部 `scroll {"direction": "up", "amount": 10}` 到顶 → 回显含 `(already at up, no further content)`；
  5. 参数校验：`scroll {"amount": 0}` / `{"amount": 11}` / `{"amount": -1}` → Pydantic 校验失败、工具不执行；`scroll {"direction": "sideways"}` → Literal 校验失败；
  6. 懒加载：打开无限滚动页面 → `scroll {"amount": 5}` 后下一轮 `get_state` 确认新加载内容出现在 DOM 树（验证 G2 快照与 DOM 层 `pages_below` 协作，不互相打架）。
