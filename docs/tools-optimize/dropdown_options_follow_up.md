# dropdown_options P1 进阶蓝图落地规格（follow-up）

> 本文是 [`dropdown_options.md`](./dropdown_options.md) 末尾「P1 进阶蓝图」（`:223-266`）的**落地实现规格**——把草图升级为可直接照搬实施的「完整代码 + 测试骨架」提案文档。
>
> **本文为提案文档（不改源码）**，与 [`dropdown_options.md`](./dropdown_options.md) / [`select_dropdown.md`](./select_dropdown.md) 同属「提案文档」风格。
>
> 范围：**G7 读侧**（ARIA menu/listbox / custom class / combobox 读 / 子树搜索 / 懒加载等待）+ **G9**（click 误点 select 降级）+ **G5 进阶**（空选项诊断）。

---

## 背景（为什么写这份 follow-up）

[`dropdown_options.md`](./dropdown_options.md) 的 P1 蓝图只画了草图（每项给 session 新方法名 + 大致 JS 思路，明确「不在本次落地」）。本 follow-up 把它落到**可行的完整实现规格**。

### ⚠️ 现状纠偏（必须先认清，否则会重复造轮子）

探查确认：**P0 与 P1 蓝图第 7 项（G8 `select_dropdown`）都已落地**。本 follow-up 据此收缩范围。

| 蓝图项 | 状态 | 证据（行号） |
|---|---|---|
| P0 `dropdown_options` 核心 | ✅ **已完成** | `_action_dropdown_options` `actions.py:1020-1070`（scope via `fetch_select_options(backend_node_id)` + SELECT-only 守卫 + `json.dumps` + `enumerate` + `_describe_dropdown` 短/长回显）；`fetch_select_options` `session.py:2497-2533`；`_describe_dropdown` `actions.py:551-573`；`import json` `actions.py:6`；`DropdownOptionsParams{index}` `models.py:167-170`；测试 `tests/test_dropdown_options.py` |
| 7. `select_dropdown` 同 bug（G8） | ✅ **已完成** | `_action_select_dropdown` `actions.py:1072-1129`；`set_select_option` `session.py:2535-2588`；JS 常量 `_SELECT_OPTION_JS`/`_SELECT_OPTION_CLICK_FALLBACK_JS` `session.py:583-650`；测试 `tests/test_select_dropdown.py`；姊妹文档 [`select_dropdown.md`](./select_dropdown.md) |

> **本文档剔除蓝图第 7 项（G8），仅交叉引用** [`select_dropdown.md`](./select_dropdown.md)。注意：`select_dropdown.md` 自身也有 P1 蓝图（ARIA/custom/combobox 的**写侧**选择），那是平行工作，**本文档不展开写侧**，只覆盖 `dropdown_options` 的**读侧** + G9。

**真正未实现的 follow-up 范围**（蓝图第 1/2/3/4/5/6/8 项）：
- **G7 读侧**：ARIA menu/listbox、custom class、combobox（读：展开→读→收起）、子树搜索、懒加载等待。
- **G9**：click 误点 `<select>` 降级（顺带统一 `_action_click` SELECT 分支 `str(options)` 与 `dropdown_options` 的输出格式不一致问题，见 `actions.py:408-414`）。
- **G5 进阶**：空选项诊断提示。

### browser-use 源码未 vendored

仓库内**无** `default_action_watchdog.py` / `service.py` / `vendor/`（字符串 `searchChildrenForDropdowns`/`checkDropdownElement`/`_handle_aria_combobox_options` 仅出现在 markdown 文档）。**P1 特性须从设计描述 + ARIA 通用知识移植**，不能照搬 vendored 代码。且 browser-use 自身的 ARIA/custom/combobox 测试**全部 skip**——参照路径未充分验证，这是贯穿本文档的风险信号（尤其 combobox）。

---

## 差距再分析（再对齐表）

| 维度 | browser-use（`default_action_watchdog.py`） | TreeWalker 当前 | 差距（本 follow-up 覆盖） |
|---|---|---|---|
| native `<select>` 读 | 精确绑定 | ✅ 已对齐 | — |
| **ARIA menu/listbox 读** | `querySelectorAll('[role=menuitem"],[role="option"]')` + `aria-selected`/classList 判选 | ❌ 非 select 走 tag 守卫 error | **G7（P1a）** |
| **custom class 读**（Semantic UI 等） | `classList.contains('dropdown')\|\|'ui'` → `.item,.option,[data-value]` | ❌ 同上 | **G7（P1b）** |
| **combobox 读**（aria-controls 独立 listbox） | 展开→等懒加载→`getElementById(aria-controls)`→读→收起 | ❌ 同上 | **G7（P1c，实验性）** |
| **子树搜索** | `searchChildrenForDropdowns(start, 4)` 递归向下 | ❌ 无 | **G7（P1d）** |
| **懒加载等待** | combobox 展开后 sleep + 轮询 | ❌ 无 | **G7（P1c 内）** |
| **空选项诊断** | 空 option 返回带 error 的 dict 不抛错 | ⚠️ P0 仅回 hint 行，无类型诊断 | **G5 进阶（并入 P1a）** |
| **click 误点 select 降级** | `_action_click` 误点 select 自动读选项（`service.py:711-723`） | ⚠️ SELECT 分支调 `fetch_select_options` 但返回 `str(options)`（Python repr，非 json 编码，与 `dropdown_options` 格式不一致） | **G9（P1e）** |

---

## 已确认的可复用资产（实现须基于这些，不臆造）

| 资产 | 位置 | 复用方式 |
|---|---|---|
| CDP resolveNode→callFunctionOn 内联模式（**无 wrapper**） | `fetch_select_options` `session.py:2509-2531`；`_js_click` `session.py:1519-1532` | 新 session 方法照抄此 4 步 |
| `callFunctionOn` 用 `arguments:[{value}]` 传参 | `set_select_option` `session.py:2558-2566` | 需带参的 JS 用 |
| 模块级 JS 常量风格 | `_SELECT_OPTION_JS` `session.py:583-625` | 新 JS 常量紧邻其后（`session.py:650` 之后） |
| 真实点击（展开 combobox） | `click_element(backend_node_id)` `session.py:1408`（已含 scrollIntoView+occlusion+JS 回退） | **不手搓 `dispatchEvent('click')`** |
| 真实按键（Escape 收起） | `send_keys("Escape")` `session.py:1881`（`Input.dispatchKeyEvent` keyDown/keyUp） | combobox 收起用 |
| 轮询等待模板 | `_wait_for_page_settle` `session.py:1194-1225`（`time.monotonic()`+`asyncio.sleep`） | P1c-2 轮询沿用；P1c 先固定 `asyncio.sleep(0.5)` |
| combobox/autocomplete 探测 | `_is_autocomplete_field(entry) -> (is_combo, needs_js_wait)` `actions.py:593-615` | action 层预分类直接复用（已含 `needs_js_wait`） |
| `entry.attributes` 携带 `role`/`aria-controls` | `views.py:31,108`；serializer 标 listbox/menu/combobox 为交互 `serializer.py:707` | action 层读 attribute 判类型 |
| `ActionResult`（`extracted_content` 被 `__str__` 截到 500；`long_term_memory` 不受 `__str__` 限；**无 `include_extracted_content_only_once`**） | `views.py:8-37` | 大菜单靠 long_term_memory 摘要兜底 |
| 测试模式 | `tests/test_dropdown_options.py`（action 层 `_make_browser`）；`tests/test_select_dropdown.py:206-228`（session 层 `BrowserSession.__new__`+CDP mock） | 新测试照抄；`@pytest.mark.asyncio` 逐方法必加（asyncio_mode=strict，无 pyproject 配置） |

---

## 关键技术决策

### D1｜混合调度（action 廉价预分类 + session 轻量 dispatcher）

三种架构选择：

- **(A) 纯 per-type session 方法 + action 层智能调度**：action 层靠 `entry` 的 HTML attribute 猜类型（SELECT/role=combobox/div.dropdown…）再调对应方法。**否**——attribute 猜测易错（`div.dropdown` 是真 Semantic-UI 控件还是纯样式 div？），猜错就空返回无诊断。
- **(B) 单一大方法 `fetch_dropdown_options` 在 JS 内判全部类型**：**否**——combobox 展开需真实 `click_element`/`send_keys("Escape")`（Python 侧 `await`，无法在单次 `callFunctionOn` 内完成），combobox **必须是 Python flow**；且单一大方法不可测（session 测试要 mock 分支 JS 返回，action 层失去「哪个类型跑了」的可见性）。
- **(C) 混合（推荐）**：
  - **action 层**做廉价 attribute 预分类（复用 `_is_autocomplete_field`）：`SELECT`→native（**沿用 P0 路径零改动**）；`is_combo + aria-controls`→combobox；其余→委托 session dispatcher。
  - **session 层**轻量 dispatcher `fetch_dropdown_options(backend_node_id) -> {"options", "source"}`，**顺序试** `_fetch_aria_options` → `_fetch_custom_class_options` → `search_children_for_dropdowns`，首个命中即返回（带 `source` 标记）。
  - combobox 因需真实 click/Escape，独立为 `expand_and_fetch_combobox_options`（Python flow）。

> 延迟判真到浏览器侧（JS 探测 `[role=option]` 是否存在、`aria-controls` 是否指向真 listbox），同时保持每个 helper 小而可测（各一次 resolveNode+callFunctionOn）。

### D2｜输出格式统一（抽取 `_format_options_result`）

把 `_action_dropdown_options` `actions.py:1052-1070` 的格式化段（`enumerate`+`json.dumps`+`selected`+hint+短/长回显）抽成 `Tools._format_options_result(raw_options, entry, index, source: str)`。**native/aria/custom/combobox/subtree/click-select 共用同一格式**。

- `source` 折进 `long_term_memory`：`Got N options from [SELECT] ... at index N`（native，无后缀，**与 P0 字节一致**）vs `... via [ARIA]` / `via [COMBOBOX]` / `via child-depth-2`（其余类型）。`source` 是子树搜索（P1d）的诊断通道。
- native 传 `source="native"` → 渲染无 `via` 后缀 → **P0 现有测试零回归**。

### D3｜combobox 流程（真实手势 + finally 强制收起）

`expand_and_fetch_combobox_options(backend_node_id)` 是 **Python flow**：

1. `await self.click_element(backend_node_id)`（展开，复用 `session.py:1408`，**不手搓 `dispatchEvent`**）。
2. `await asyncio.sleep(0.5)`（固定等懒加载，见 D4）。
3. 一次 `callFunctionOn` 跑 `_COMBOBOX_OPTIONS_JS`：`getElementById(aria-controls)` → `[role="option"]`/降级 `li`，返回 `{options, listboxFound, error}`。**用 `getElementById` 而非 `querySelectorAll`**——React Portal 把 listbox 渲染到 `document.body` 外，子树查询找不到但 id 查得到。
4. **`finally` 强制收起**：`await self.send_keys("Escape")` + 一次 JS `element.blur()`。**即便第 3 步抛错也收起**——残留展开的遮罩会拦截后续 click，是 load-bearing 而非装饰。

> `listboxFound=False` → action 层返回友好 error（**不静默返回空**）。

### D4｜懒加载（P1c 固定 sleep + 200 上限；轮询推迟）

- **P1c 先固定 `asyncio.sleep(0.5)`**（确定、保守）。
- **轮询 `_wait_for_options_stable`（仿 `_wait_for_page_settle`）推迟到 P1c-2**，按真实 flake 反馈再开。
- **选项数 hard cap 200**：JS `nodes.slice(0, 200)`；若 `nodes.length > 200`，追加一条合成项 `{text: "... (showing 200 of <total>, use scroll/search_page for more)", value: "", selected: false}`。靠 `ActionResult.__str__` 的 500 截断，**永不等待不会显示的选项**，规避 browser-use 的 15s 超时焦虑。
- **不用 MutationObserver**（全仓零用例，破例引入不值）。

### D5｜子树搜索（session 层 JS 递归）

Python 侧只有可能被剪枝的序列化树，故在**浏览器侧 JS 递归**：BFS depth ≤ 4，short-circuit 首个命中；命中即在同一 JS 内返回该后代的 options（省去二次 resolveNode）；未命中返回 `{type: null}`。`source = child-depth-N`。

### D6｜不改 `models.py` / 不引入 `include_extracted_content_only_once`

类型自动探测，`DropdownOptionsParams{index}`（`models.py:167-170`）不变。`ActionResult`（`views.py:8-37`）无 `include_extracted_content_only_once`，引入需改模型 + 所有回显点，改动面与风险不成比例。

### D7｜G9 click-降级（复用 formatter，不重路由）

`_action_click` 误点 `<select>` 时**不重定向**到 `_action_dropdown_options`（click 已「点过」，重路由会混淆 LLM 的工具选择信号）；改为 SELECT 分支（`actions.py:408-414`）**直接复用 `_format_options_result(..., source="click-select")`**，输出与 `dropdown_options` 字节级同格式（修掉当前的 `str(options)` repr 不一致）。

---

## 架构与调度流程

### action 层 `_action_dropdown_options`（P1a 后重构为 dispatcher）

```text
entry, error = _get_element_by_index(index)
if error: return error
tag = entry.tag_name.upper(); backend_id = entry.backend_node_id
is_combo, _ = _is_autocomplete_field(entry)        # 复用 actions.py:593-615
attrs = entry.attributes or {}

try:
  if tag == "SELECT":                              # native：P0 路径零改动
      raw = await browser.fetch_select_options(backend_id)
      return _format_options_result(raw, entry, index, "native")
  if is_combo and (attrs.get("aria-controls") or attrs.get("aria-owns")):   # combobox
      raw = await browser.expand_and_fetch_combobox_options(backend_id)
      return _format_options_result(raw, entry, index, "combobox")
  res = await browser.fetch_dropdown_options(backend_id)                   # 其余：session dispatcher
  if res["source"] is None:                                                # 真阴性
      return ActionResult(error=f"Index {index} is a [{tag}], not a recognized dropdown ...")
  return _format_options_result(res["options"], entry, index, res["source"])
except Exception as e:
  return ActionResult(error=f"Failed to read dropdown options: {e}")
```

### session 层 dispatcher `fetch_dropdown_options`（顺序试）

```text
aria  = await _fetch_aria_options(bid)           # None=非 aria；[]/list=是 aria
if aria is not None: return {"options": aria,  "source": "aria"}
cust  = await _fetch_custom_class_options(bid)   # None=非 custom；[]/list=是 custom
if cust is not None: return {"options": cust,  "source": "custom"}
found = await search_children_for_dropdowns(bid) # 子树递归
if found["options"]:    return {"options": found["options"], "source": found["source"]}
return {"options": [], "source": None}           # 真阴性
```

> 约定：`_fetch_*` 返回 `None` 表示「不是此类型」，返回 `list[dict]`（可能为空）表示「是此类型」。这让 dispatcher 能区分「是 listbox 但空」（→ G5 诊断「未展开/懒加载」）与「根本不是 listbox」（→ 续探 custom/子树）。

---

## 分阶段改动（完整代码 + 测试骨架）

> 每阶段独立可交付、各自带测试、低风险优先。依赖：P1a 无依赖 → P1b 依赖 dispatcher → P1c/P1d 依赖 P1a → P1e 依赖 `_format_options_result` 抽取。

---

### P1a — ARIA menu / listbox（G7）+ G5 进阶空选项诊断

#### session 层：JS 常量 + 方法

JS 常量定位：紧邻 `_SELECT_OPTION_CLICK_FALLBACK_JS`（`session.py:650`）之后。

```python
# ARIA menu/listbox 读脚本（移植 browser-use default_action_watchdog.py type='aria'）。
# 非 aria-shaped 返回 null（让 dispatcher 续探 custom/子树）；是 aria 但无 option 子节点返回 []。
# 选项数 hard cap 200（D4）；超出追加合成截断项。
_ARIA_OPTIONS_JS = """
function() {
	const root = this;
	const role = root.getAttribute('role');
	const isAriaContainer = ['listbox', 'menu', 'menubar', 'tree', 'grid'].indexOf(role) !== -1;
	const hasAriaOptions = !!root.querySelector('[role="option"],[role="menuitem"]');
	if (!isAriaContainer && !hasAriaOptions) return null;
	const all = Array.from(root.querySelectorAll('[role="menuitem"],[role="option"]'));
	const capped = all.slice(0, 200);
	const mapped = capped.map(function(n) {
		return {
			text: (n.textContent || '').trim(),
			value: n.getAttribute('data-value') || n.getAttribute('value') || (n.textContent || '').trim(),
			selected: n.getAttribute('aria-selected') === 'true' || n.classList.contains('selected') || n.classList.contains('active'),
		};
	});
	if (all.length > 200) {
		mapped.push({text: '... (showing 200 of ' + all.length + ', use scroll/search_page for more)', value: '', selected: false});
	}
	return mapped;
}
"""
```

session 方法定位：紧邻 `fetch_select_options`（`session.py:2533`）之后。镜像其 resolveNode+callFunctionOn 结构，**仅 JS 不同**。

```python
async def _fetch_aria_options(self, backend_node_id: int) -> list[dict] | None:
	"""Read options of an ARIA menu/listbox scoped to backendNodeId.

	Returns None when the element is not aria-shaped (no listbox/menu role and
	no [role=option]/[role=menuitem] descendants) — lets the dispatcher try the
	next type. Returns a list (possibly empty) when it IS aria-shaped. Capped to
	200 options (D4). Raises on CDP/JS error (caller wraps).
	"""
	resolve = await self.client.send.DOM.resolveNode(
		{"backendNodeId": backend_node_id},
		session_id=self.current_session_id,
	)
	object_id = resolve["object"]["objectId"]
	result = await self.client.send.Runtime.callFunctionOn(
		{"objectId": object_id, "functionDeclaration": _ARIA_OPTIONS_JS, "returnByValue": True},
		session_id=self.current_session_id,
	)
	value = result.get("result", {}).get("value")
	return value  # None / list[dict]
```

> dispatcher `fetch_dropdown_options`（见下「session 层 dispatcher」总装）首个调用即 `_fetch_aria_options`。

#### action 层：dispatcher 重构 + `_format_options_result` 抽取

抽取 `_format_options_result`（`@staticmethod` 之外的实例方法，因用 `self._describe_dropdown`）。定位：紧邻 `_describe_dropdown`（`actions.py:573`）之后。

```python
def _format_options_result(
	self, raw_options: list[dict], entry: Any, index: int, source: str,
) -> ActionResult:
	"""Shared dropdown echo (short/long split) for native/aria/custom/combobox/
	subtree/click-select. ``source`` folds into long_term_memory as the subtree
	diagnostic channel; "native" renders no suffix (byte-identical to P0)."""
	lines: list[str] = []
	for i, opt in enumerate(raw_options):
		text = json.dumps(opt.get("text", ""))
		value = json.dumps(opt.get("value", ""))
		status = " (selected)" if opt.get("selected") else ""
		lines.append(f"{i}: text={text}, value={value}{status}")
	hint = f"Use the value in select_dropdown(index={index}, value=...)"
	# G5 进阶：空选项按 source 给诊断（native 沿用 P0 仅 hint，避免回归）
	if not lines:
		diag = _EMPTY_OPTIONS_DIAGNOSTIC.get(source)
		extracted = (diag + "\n" + hint) if diag else hint
	else:
		extracted = "\n".join(lines) + "\n" + hint
	desc = self._describe_dropdown(entry, index)
	if source == "native":
		via = ""                               # P0 字节一致（无 via 后缀）
	elif source.startswith("child-depth-"):
		via = f" via {source}"                 # 子树诊断：via child-depth-2
	else:
		via = f" via [{source.upper()}]"       # via [ARIA] / [CUSTOM] / [COMBOBOX] / [CLICK-SELECT]
	memory = f"Got {len(raw_options)} options from {desc}{via}"
	return ActionResult(extracted_content=extracted, long_term_memory=memory)
```

> `_EMPTY_OPTIONS_DIAGNOSTIC` 为模块级常量（P1a 引入）：
> ```python
> _EMPTY_OPTIONS_DIAGNOSTIC = {
> 	"aria": "Listbox/menu found but no [role=option] children (may need expanding).",
> 	"custom": "Custom dropdown found but no .item/.option/[data-value] children (may need expanding).",
> 	"combobox": "Combobox listbox found but empty (options may load on demand).",
> }
> ```
> native 空选项**不进此表** → 沿用 P0（仅 hint 行），`tests/test_dropdown_options.py::test_empty_options_soft_echo_just_hint` **零回归**。

`_action_dropdown_options`（`actions.py:1020-1070`）按「架构与调度流程」伪代码整段重写为 dispatcher（native 分支调 `_format_options_result(raw, entry, index, "native")`）。

#### 测试（完整骨架）

**action 层**（扩 `tests/test_dropdown_options.py`，`_make_browser` 新增 `fetch_dropdown_options` mock）：

```python
def _make_browser(*, options=None, raises=None, dispatch=None) -> MagicMock:
	bs = MagicMock()
	if raises is not None:
		bs.fetch_select_options = AsyncMock(side_effect=raises)
	else:
		bs.fetch_select_options = AsyncMock(return_value=options if options is not None else [])
	bs.fetch_dropdown_options = AsyncMock(
		return_value=dispatch if dispatch is not None else {"options": [], "source": None}
	)
	bs.expand_and_fetch_combobox_options = AsyncMock(return_value=[])
	bs.get_state = AsyncMock(return_value=_make_state({}))
	return bs


class TestDropdownOptionsDispatcher:
	@pytest.mark.asyncio
	async def test_aria_listbox_routes_through_session_dispatcher(self):
		entry = _make_entry(tag="UL", backend_node_id=7, attributes={"role": "listbox"})
		state = _make_state({3: entry})
		browser = _make_browser(dispatch={"options": [
			{"value": "a", "text": "Alpha", "selected": True},
		], "source": "aria"})
		result = await Tools().execute("dropdown_options", {"index": 3}, browser, browser_state=state)
		# 范围绑定：dispatcher 用 backend_node_id
		browser.fetch_dropdown_options.assert_awaited_once_with(7)
		assert result.error is None
		# source 折进 long_term_memory（诊断通道）
		assert "Got 1 options" in result.long_term_memory
		assert "via [ARIA]" in result.long_term_memory
		# native fetch 未被调用（非 SELECT）
		browser.fetch_select_options.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_native_select_still_uses_fetch_select_options_no_regression(self):
		entry = _make_entry(tag="SELECT", backend_node_id=7, attributes={"aria-label": "Country"})
		state = _make_state({3: entry})
		browser = _make_browser(options=[{"value": "us", "text": "US", "selected": True}])
		result = await Tools().execute("dropdown_options", {"index": 3}, browser, browser_state=state)
		# native 路径零回归：仍调 fetch_select_options，source=native 无 "via" 后缀
		browser.fetch_select_options.assert_awaited_once_with(7)
		assert "Got 1 options" in result.long_term_memory
		assert "via" not in result.long_term_memory
		browser.fetch_dropdown_options.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_unknown_type_returns_friendly_error(self):
		entry = _make_entry(tag="DIV", backend_node_id=7)  # 无 role，非下拉
		state = _make_state({3: entry})
		browser = _make_browser(dispatch={"options": [], "source": None})
		result = await Tools().execute("dropdown_options", {"index": 3}, browser, browser_state=state)
		assert result.error is not None
		assert "[DIV]" in result.error
		# 不泄漏全页 select 数据（native fetch 未调）
		browser.fetch_select_options.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_empty_aria_options_emits_diagnostic_and_hint(self):
		entry = _make_entry(tag="UL", backend_node_id=7, attributes={"role": "listbox"})
		state = _make_state({3: entry})
		browser = _make_browser(dispatch={"options": [], "source": "aria"})
		result = await Tools().execute("dropdown_options", {"index": 3}, browser, browser_state=state)
		assert result.error is None
		assert "Got 0 options" in result.long_term_memory
		assert "[role=option]" in result.extracted_content  # G5 诊断
		assert "select_dropdown(index=3, value=...)" in result.extracted_content
```

> **需同步更新既有用例**：`test_non_select_element_returns_error_without_fetch` 改为断言走 `fetch_dropdown_options`（source None）返回友好 error；`test_native_select_echoes_options_and_memory` 等用 native 路径，断言不变（source=native 无 via）。

**session 层**（`tests/test_dropdown_options.py` 加 `TestFetchAriaOptions`，mock 边界为 CDP，仿 `tests/test_select_dropdown.py:206-228`）：

```python
class TestFetchAriaOptions:
	def _make_session(self, value):
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid"
		client = MagicMock()
		client.send.DOM.resolveNode = AsyncMock(return_value={"object": {"objectId": "obj-1"}})
		client.send.Runtime.callFunctionOn = AsyncMock(return_value={"result": {"value": value}})
		s.client = client
		return s, client

	@pytest.mark.asyncio
	async def test_aria_returns_options_scoped_to_backend_id(self):
		s, client = self._make_session([{"value": "a", "text": "Alpha", "selected": True}])
		out = await s._fetch_aria_options(99)
		assert out == [{"value": "a", "text": "Alpha", "selected": True}]
		client.send.DOM.resolveNode.assert_awaited_once_with({"backendNodeId": 99}, session_id="sid")
		client.send.Runtime.callFunctionOn.await_count == 1

	@pytest.mark.asyncio
	async def test_not_aria_returns_none(self):
		s, _ = self._make_session(None)
		assert await s._fetch_aria_options(7) is None
```

---

### P1b — custom class（Semantic UI 等）（G7）

#### session 层：JS 常量 + 方法

```python
# custom-class 下拉读脚本（移植 browser-use type='custom'：Semantic UI / Foundation 等）。
# 非 custom-shaped（无 dropdown/ui class）返回 null；是 custom 但无候选项返回 []。
_CUSTOM_CLASS_OPTIONS_JS = """
function() {
	const root = this;
	if (!(root.classList.contains('dropdown') || root.classList.contains('ui'))) {
		return null;
	}
	const all = Array.from(root.querySelectorAll('.item, .option, [data-value]'));
	const capped = all.slice(0, 200);
	const mapped = capped.map(function(n) {
		return {
			text: (n.textContent || '').trim(),
			value: n.getAttribute('data-value') || n.getAttribute('value') || (n.textContent || '').trim(),
			selected: n.classList.contains('selected') || n.classList.contains('active'),
		};
	});
	if (all.length > 200) {
		mapped.push({text: '... (showing 200 of ' + all.length + ', use scroll/search_page for more)', value: '', selected: false});
	}
	return mapped;
}
"""

async def _fetch_custom_class_options(self, backend_node_id: int) -> list[dict] | None:
	"""Read options of a custom-class dropdown (Semantic UI etc.) scoped to
	backendNodeId. None = not custom-shaped; list = is custom (possibly empty).
	Capped to 200 (D4). Raises on CDP/JS error (caller wraps)."""
	resolve = await self.client.send.DOM.resolveNode(
		{"backendNodeId": backend_node_id}, session_id=self.current_session_id,
	)
	object_id = resolve["object"]["objectId"]
	result = await self.client.send.Runtime.callFunctionOn(
		{"objectId": object_id, "functionDeclaration": _CUSTOM_CLASS_OPTIONS_JS, "returnByValue": True},
		session_id=self.current_session_id,
	)
	return result.get("result", {}).get("value")  # None / list[dict]
```

#### session 层 dispatcher 总装（P1a + P1b 合并落地）

```python
async def fetch_dropdown_options(self, backend_node_id: int) -> dict:
	"""Dispatcher: try aria → custom → subtree, return first hit.

	Returns {"options": list[dict], "source": str|None}. source None = element
	is not a recognized non-native dropdown (true negative). Raises on CDP/JS
	error (caller wraps with a friendly message).
	"""
	aria = await self._fetch_aria_options(backend_node_id)
	if aria is not None:
		return {"options": aria, "source": "aria"}
	custom = await self._fetch_custom_class_options(backend_node_id)
	if custom is not None:
		return {"options": custom, "source": "custom"}
	found = await self.search_children_for_dropdowns(backend_node_id)  # P1d
	if found["options"]:
		return {"options": found["options"], "source": found["source"]}
	return {"options": [], "source": None}
```

#### 测试（要点）

- session 层 `TestFetchCustomClassOptions`：`classList` 有 `dropdown` → 返回 `.item` 选项；无 `dropdown`/`ui` class → 返回 `None`。
- action 层：dispatcher 回退链——`_fetch_aria_options` 返回 `None`、`_fetch_custom_class_options` 返回 list → source=`custom`，long_term_memory 带 `via [CUSTOM]`。

---

### P1c — combobox + 懒加载（G7，**实验性**）

> ⚠️ **实验性阶段**：browser-use 自身跳过了全部 combobox/ARIA 测试，参照路径未充分验证。保守实现 = 固定 sleep + finally 强制收起 + 200 上限 + 手测验收门槛。轮询推迟到 P1c-2。

#### session 层：JS 常量 + Python flow 方法

```python
# combobox 读脚本：用 getElementById(aria-controls) 定位独立 listbox（React Portal 友好）。
# 返回 {options, listboxFound, error}。options 200 上限（D4）。
_COMBOBOX_OPTIONS_JS = """
function() {
	const combo = this;
	const controlsId = combo.getAttribute('aria-controls') || combo.getAttribute('aria-owns');
	if (!controlsId) return {options: [], listboxFound: false, error: 'no aria-controls/aria-owns'};
	const listbox = document.getElementById(controlsId);
	if (!listbox) return {options: [], listboxFound: false, error: 'listbox not found'};
	const all = Array.from(listbox.querySelectorAll('[role="option"], li'));
	const capped = all.slice(0, 200);
	const mapped = capped.map(function(n) {
		return {
			text: (n.textContent || '').trim(),
			value: n.getAttribute('data-value') || n.getAttribute('value') || (n.textContent || '').trim(),
			selected: n.getAttribute('aria-selected') === 'true' || n.classList.contains('selected'),
		};
	});
	if (all.length > 200) {
		mapped.push({text: '... (showing 200 of ' + all.length + ', use scroll/search_page for more)', value: '', selected: false});
	}
	return {options: mapped, listboxFound: true};
}
"""

async def expand_and_fetch_combobox_options(self, backend_node_id: int) -> list[dict]:
	"""Expand a combobox (real click), read its aria-controls listbox, then
	ALWAYS collapse (finally). Python flow — combobox needs real click/Escape,
	not a single callFunctionOn. Capped to 200 (D4). Raises on CDP/JS error;
	collapse still runs in finally (D3).

	Note: returns the option list; raises RuntimeError if the listbox isn't
	found (caller maps to a friendly error)."""
	# 1. 展开（真实 click，复用 click_element：scrollIntoView+occlusion+JS 回退）
	await self.click_element(backend_node_id)
	# 2. 固定等懒加载（D4；轮询推迟到 P1c-2）
	await asyncio.sleep(0.5)
	# 3. 读（一次 callFunctionOn，getElementById 定位 listbox）
	try:
		resolve = await self.client.send.DOM.resolveNode(
			{"backendNodeId": backend_node_id}, session_id=self.current_session_id,
		)
		object_id = resolve["object"]["objectId"]
		result = await self.client.send.Runtime.callFunctionOn(
			{"objectId": object_id, "functionDeclaration": _COMBOBOX_OPTIONS_JS, "returnByValue": True},
			session_id=self.current_session_id,
		)
		payload = result.get("result", {}).get("value") or {}
		if not payload.get("listboxFound"):
			raise RuntimeError("combobox listbox not found: " + str(payload.get("error", "")))
		return payload.get("options", [])
	finally:
		# 4. 强制收起（即便第 3 步抛错也收起，防止遮罩拦截后续点击 —— D3 load-bearing）
		try:
			await self.send_keys("Escape")
			await self.client.send.Runtime.callFunctionOn(
				{"objectId": object_id, "functionDeclaration": "function(){ try{ this.blur(); }catch(e){} }", "returnByValue": True},
				session_id=self.current_session_id,
			)
		except Exception as e:
			logger.debug("combobox collapse failed: %s", e)
```

> 注：`object_id` 在 `finally` 引用——若第 3 步 `resolveNode` 前抛错则未定义；实现时把 `object_id` 预初始化为 `None` 并在 finally 内 `if object_id:` 守卫。文档实现者据此细化。

#### action 层

dispatcher 的 combobox 分支（见「架构与调度流程」）：`is_combo and (aria-controls or aria-owns)` → `expand_and_fetch_combobox_options` → `_format_options_result(raw, entry, index, "combobox")`。`listboxFound=False`（session 抛 `RuntimeError`）被 `except` 捕获 → `ActionResult(error="Failed to read dropdown options: combobox listbox not found: ...")`。

#### 测试（完整骨架，**调用顺序不变量**是重点）

session 层需额外 mock `click_element`/`send_keys`（实例方法）：

```python
class TestExpandAndFetchComboboxOptions:
	def _make_session(self, value, *, read_raises=None):
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid"
		s.click_element = AsyncMock()
		s.send_keys = AsyncMock()
		client = MagicMock()
		client.send.DOM.resolveNode = AsyncMock(return_value={"object": {"objectId": "obj-1"}})
		if read_raises is not None:
			client.send.Runtime.callFunctionOn = AsyncMock(side_effect=read_raises)
		else:
			client.send.Runtime.callFunctionOn = AsyncMock(return_value={"result": {"value": value}})
		s.client = client
		return s, client

	@pytest.mark.asyncio
	async def test_expand_read_collapse_order(self):
		s, client = self._make_session({"options": [{"value": "a", "text": "A", "selected": False}], "listboxFound": True})
		out = await s.expand_and_fetch_combobox_options(7)
		assert out == [{"value": "a", "text": "A", "selected": False}]
		# 顺序：click → (sleep) → read → Escape
		s.click_element.assert_awaited_once_with(7)
		s.send_keys.assert_awaited_once()  # Escape
		assert s.send_keys.await_args.args[0].lower().startswith("esc")

	@pytest.mark.asyncio
	async def test_collapse_runs_even_when_read_raises(self):
		s, client = self._make_session(None, read_raises=RuntimeError("detached"))
		with pytest.raises(RuntimeError):
			await s.expand_and_fetch_combobox_options(7)
		# D3 不变量：读失败也要收起
		s.send_keys.assert_awaited_once()

	@pytest.mark.asyncio
	async def test_listbox_not_found_raises(self):
		s, client = self._make_session({"options": [], "listboxFound": False, "error": "no aria-controls"})
		with pytest.raises(RuntimeError, match="listbox not found"):
			await s.expand_and_fetch_combobox_options(7)
		s.send_keys.assert_awaited_once()  # 仍收起
```

action 层：`is_combo + aria-controls` → 调 `expand_and_fetch_combobox_options`，source=`combobox`；mock 其 `side_effect=RuntimeError` → 友好 error（不裸抛中断 agent loop）。

#### 手测门槛（合并前必过，**不靠单测兜底框架差异**）

1. **WAI-ARIA Combobox with Listbox Popup** 示例 → 选项读出 + 调用后 combobox 收起。
2. **React-Select v5** demo（Portal 渲染 listbox 到 body）→ `getElementById(aria-controls)` 命中，选项读出。
3. **Semantic-UI dropdown**（若 `role=combobox`）→ combobox 路径读出；否则走 custom-class（P1b）。
4. 故意 mock Escape 失败 → 确认 `blur()` JS 回退兜底，不残留遮罩。

---

### P1d — 子树搜索（G7）

#### session 层：JS 常量 + 方法

```python
# 子树搜索：目标自身非任何已知类型时，BFS 向下搜 maxDepth 层，首个命中即在同一 JS
# 内返回该后代的 options（省去二次 resolveNode）。返回 {options, source}。
_SUBTREE_SEARCH_JS = """
function(maxDepth) {
	const start = this;
	function readAria(el) {
		const ns = Array.from(el.querySelectorAll('[role="menuitem"],[role="option"]'));
		return ns.slice(0, 200).map(function(n) {
			return {text: (n.textContent||'').trim(), value: n.getAttribute('data-value')||n.getAttribute('value')||(n.textContent||'').trim(), selected: n.getAttribute('aria-selected')==='true'||n.classList.contains('selected')};
		});
	}
	function readCustom(el) {
		const ns = Array.from(el.querySelectorAll('.item, .option, [data-value]'));
		return ns.slice(0, 200).map(function(n) {
			return {text: (n.textContent||'').trim(), value: n.getAttribute('data-value')||n.getAttribute('value')||(n.textContent||'').trim(), selected: n.classList.contains('selected')||n.classList.contains('active')};
		});
	}
	function classify(el) {
		if (el.querySelector('[role="option"],[role="menuitem"]')) return 'aria';
		if ((el.classList.contains('dropdown') || el.classList.contains('ui')) && el.querySelector('.item,.option,[data-value]')) return 'custom';
		return null;
	}
	var queue = [[start, 0]];
	while (queue.length) {
		var pair = queue.shift(); var el = pair[0]; var d = pair[1];
		if (d > 0) {
			var t = classify(el);
			if (t === 'aria') return {options: readAria(el), source: 'child-depth-' + d};
			if (t === 'custom') return {options: readCustom(el), source: 'child-depth-' + d};
		}
		if (d < maxDepth) {
			for (var i = 0; i < el.children.length; i++) queue.push([el.children[i], d + 1]);
		}
	}
	return {options: [], source: null};
}
"""

async def search_children_for_dropdowns(self, backend_node_id: int, max_depth: int = 4) -> dict:
	"""BFS the subtree (max_depth levels) for a dropdown-shaped descendant, read
	its options in-place. JS-side recursion (the Python tree may be pruned).
	Returns {"options": list[dict], "source": "child-depth-N"|None}. Raises on
	CDP/JS error (caller wraps)."""
	resolve = await self.client.send.DOM.resolveNode(
		{"backendNodeId": backend_node_id}, session_id=self.current_session_id,
	)
	object_id = resolve["object"]["objectId"]
	result = await self.client.send.Runtime.callFunctionOn(
		{"objectId": object_id, "functionDeclaration": _SUBTREE_SEARCH_JS,
		 "arguments": [{"value": max_depth}], "returnByValue": True},
		session_id=self.current_session_id,
	)
	return result.get("result", {}).get("value") or {"options": [], "source": None}
```

#### 测试（要点）

- session 层 `TestSearchChildrenForDropdowns`：mock 返回 `{options:[...], source:"child-depth-2"}` → 断言 `arguments=[{"value":4}]`；mock 返回 `{options:[], source:null}` → 未命中。
- action 层：dispatcher `unknown` → 子树命中 → `via child-depth-2` 进 long_term_memory；未命中 → 友好 error。

---

### P1e — G9 click 误点 select 降级 + 格式统一

#### action 层：click SELECT 分支复用 formatter

定位：`_action_click` SELECT 分支 `actions.py:408-414`。

```python
# 2. SELECT 分支：误点 <select> 时读出选项（G9 降级），统一为 dropdown_options 格式
if entry.tag_name.upper() == "SELECT":
	try:
		raw_options = await browser.fetch_select_options(backend_id)
	except Exception as e:
		return ActionResult(error=f"Failed to read select options: {e}")
	return self._format_options_result(raw_options, entry, params["index"], "click-select")
```

> **D7**：不重路由到 `_action_dropdown_options`（click 已「点过」，重路由混淆 LLM 工具选择信号）。`source="click-select"` → long_term_memory 带 `via [CLICK-SELECT]`，输出与 `dropdown_options` 字节同格式（修掉当前 `str(options)` repr 不一致）。

#### 测试（扩 `tests/test_click.py::TestClickSelectBranch`）

```python
@pytest.mark.asyncio
async def test_select_branch_uses_shared_format_not_str_repr(self):
	# click SELECT 分支输出应与 dropdown_options 同格式（json 编码 + 序号 + hint）
	entry = _make_entry(tag="SELECT", backend_node_id=7, attributes={"aria-label": "Country"})
	state = _make_state({3: entry})
	browser = _make_browser_for_click(fetch_options=[
		{"value": "us", "text": 'US "North"', "selected": True},
	])
	result = await Tools().execute("click", {"index": 3}, browser, browser_state=state)
	# 非 str(options) 的 Python repr，而是 json 编码（双引号保留）
	assert '"US \\"North\\""' in result.extracted_content
	assert "0: text=" in result.extracted_content
	assert " (selected)" in result.extracted_content
	assert result.extracted_content.rstrip().endswith("Use the value in select_dropdown(index=3, value=...)")
	assert "via [CLICK-SELECT]" in result.long_term_memory
```

> 既有 `test_select_uses_scoped_fetch_not_global_query` 仍绿（仍调 `fetch_select_options(backend_id)`），仅断言「输出格式」的新用例为新增。

---

## 涉及文件清单

| 文件 | 改动类型 | 说明 |
|---|---|---|
| `src/tree_walker/browser/session.py` | **改** | 新增模块级 JS 常量 `_ARIA_OPTIONS_JS`/`_CUSTOM_CLASS_OPTIONS_JS`/`_COMBOBOX_OPTIONS_JS`/`_SUBTREE_SEARCH_JS`（紧邻 `session.py:650`）；新增方法 `fetch_dropdown_options`（dispatcher）、`_fetch_aria_options`、`_fetch_custom_class_options`、`expand_and_fetch_combobox_options`、`search_children_for_dropdowns`（紧邻 `session.py:2533`） |
| `src/tree_walker/tools/actions.py` | **改** | 抽取 `_format_options_result` + 模块常量 `_EMPTY_OPTIONS_DIAGNOSTIC`（紧邻 `actions.py:573`）；`_action_dropdown_options`（`:1020-1070`）重写为 dispatcher；`_action_click` SELECT 分支（`:408-414`）复用 formatter |
| `tests/test_dropdown_options.py` | **改/扩** | `_make_browser` 加 `fetch_dropdown_options`/`expand_and_fetch_combobox_options` mock；新增 `TestDropdownOptionsDispatcher`、`TestFetchAriaOptions`、`TestFetchCustomClassOptions`、`TestSearchChildrenForDropdowns`；同步既有用例（非 select 现走 dispatcher） |
| `tests/test_combobox_options.py` | **新增** | `TestExpandAndFetchComboboxOptions`（调用顺序不变量 + finally 收起 + listbox 未命中）——独立文件因顺序不变量值得专属套件 |
| `tests/test_click.py` | **扩** | `TestClickSelectBranch` 加格式统一断言 |
| `src/tree_walker/tools/models.py` | 不改 | `DropdownOptionsParams{index}` 不变（类型自动探测） |
| `src/tree_walker/agent/views.py` | 不改 | `ActionResult` 不引入 `include_extracted_content_only_once` |
| `docs/Tools技术细节/04_动作清单与CDP映射.md` | **改**（文档同步） | §4.4 加 ARIA/custom/combobox 读的 CDP 行（DOM.resolveNode + Runtime.callFunctionOn） |

---

## 风险与已知限制

1. **combobox 框架多样性**（P1c 最高风险）：React Portal 把 listbox 渲染到 `body` 外，`getElementById(aria-controls)` 能找到但 `querySelectorAll` 不能；Material/Downshift/headless-ui 在 `aria-controls`/`aria-owns`/`data-*` 上各有差异。P1c 对 vanilla + WAI-ARIA 可靠，React/Material 为 best-effort，**靠手测门槛兜底**。
2. **懒加载时序 flake**（P1c）：固定 0.5s 是起点非保证；轮询推迟到 P1c-2 按真实 flake 反馈再开。
3. **browser-use 自身 skip 测试**：参照路径未充分验证，P1c 继承此风险 → 标实验性。
4. **500 字符 `__str__` 截断 × 大菜单**：200 选项 ARIA 菜单渲染为约 15 行；可接受（LLM 得 `long_term_memory`「Got 200 options via [ARIA]」+ 前 15 行）。`slice(0,200)` + 合成截断项兜底最坏情况，**不报为 bug**。
5. **子树搜索性能**（P1d）：BFS depth 4 + 首命中短路；深而宽的 DOM 仍可能慢，depth 上限是护栏。
6. **custom-class 假阳性**（P1b）：非下拉的 `div.ui` 可能返回杂散 `.item`；限于子树，可接受。
7. **combobox 残留展开**：若 `send_keys("Escape")` 静默失败（罕见），后续 click 命中遮罩；`finally` + `blur()` JS 回退兜底。
8. **不写 select_dropdown 的写侧**（ARIA/custom/combobox 选择）：那是 [`select_dropdown.md`](./select_dropdown.md) P1 蓝图的平行工作，本文档不展开。

---

## 验证步骤

### 自动化测试（按 CLAUDE.md 用 `uv run python -m pytest`）

```powershell
# 逐阶段
uv run python -m pytest tests/test_dropdown_options.py -v                       # P1a/b/d
uv run python -m pytest tests/test_combobox_options.py -v                        # P1c
uv run python -m pytest tests/test_click.py::TestClickSelectBranch -v            # P1e

# 读写闭环回归（dropdown_options / select_dropdown / click SELECT 共享 fetch_select_options + _format_options_result）
uv run python -m pytest tests/test_dropdown_options.py tests/test_combobox_options.py tests/test_select_dropdown.py tests/test_click.py -x -v

# 全量 + 覆盖率（CLAUDE.md 目标 >85%）
uv run python -m pytest tests/ -x -v
uv run python -m pytest tests/ --cov=tree_walker.tools.actions --cov=tree_walker.browser.session --cov-report=term-missing
```

预期：新用例全绿；既有 `test_dropdown_options.py`/`test_select_dropdown.py` 不回归（native 路径字节一致）；combobox 的 `finally` 收起分支覆盖率须显示已覆盖（用 `side_effect=Exception` mock）。

### P1c 手测门槛（合并前必过，见 P1c 节）

WAI-ARIA combobox / React-Select Portal / Semantic-UI / Escape 失败回退四项。

### 手测步骤（其余阶段）

1. **ARIA listbox**：WAI-ARIA Listbox Example → `dropdown_options(index=N)` → extracted 列出 `[role=option]`，long_term_memory 带 `via [ARIA]`。
2. **custom class**：Semantic-UI dropdown demo → `dropdown_options` → 读出 `.item`，`via [CUSTOM]`。
3. **多类型页面范围正确**：含 native select + ARIA listbox 的页面 → 各自 index 返回各自选项（不混杂）。
4. **click 误点 select 降级**：对 `<select>` 调 `click` → 输出与 `dropdown_options` 同格式（json 编码 + hint），非 `str(options)` repr。
5. **空选项诊断**：对未展开的 ARIA listbox 调 `dropdown_options` → `Got 0 options` + `[role=option]` 诊断 + hint。
