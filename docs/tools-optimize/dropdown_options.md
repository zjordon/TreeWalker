# dropdown_options 工具完善方案

> 参照 browser-use 的 `dropdown_options` 实现完善本项目 `dropdown_options` 工具。
>
> 本文为**提案文档**（不改源码），给出差距分析、分级改进方案与可落地的代码片段，供后续实施直接参照。范围：**P0 核心改进（dropdown_options 本身：范围 bug 修复 + tag 校验 + 成功回显 + 输出格式优化 + 异常软降级 + 测试）+ P1 进阶蓝图（ARIA menu/listbox / custom class / combobox / 子树搜索 / 懒加载等待 / 空选项软回显 / click 误点 select 降级 / select_dropdown 同 bug 修复）**。

---

## 背景（为什么改）

参照对象：

- **browser-use 实际代码**：`browser_use/browser/watchdogs/default_action_watchdog.py:2776-3036`（`on_GetDropdownOptionsEvent`，CDP `DOM.resolveNode` + `Runtime.callFunctionOn` 精确绑定到目标 select，4 种下拉类型判定 + 子树递归 + combobox 展开/收起）+ `browser_use/tools/service.py:1607-1633`（`dropdown_options` 工具包装：瘦壳，事件分发 + 3s 超时 + `include_extracted_content_only_once`）。**关键**：browser-use 的这个工具本身就基于 CDP（非 Playwright），核心逻辑可直接迁移。
- **本项目当前**：`src/tree_walker/tools/actions.py:835-844`（`_action_dropdown_options`，全局 `document.querySelectorAll('select option')`）、`src/tree_walker/browser/session.py:1616-1652`（`fetch_select_options`，**已存在的正确实现**，click SELECT 分支 `actions.py:344-350` 已在用）、`src/tree_walker/tools/models.py:114-116`（`DropdownOptionsParams`）、`src/tree_walker/agent/views.py:8-37`（`ActionResult`）。
- **对齐基准**：本项目已建立的「成功回显 + try/except 软降级 + 三层测试」新规范（`scroll`/`send_keys`/`find_text`/`upload_file`），回显 helper `_describe_click`（`actions.py:378-399`）/`_describe_input`（`actions.py:429-455`）/`_describe_upload`（`actions.py:457-479`）。

**当前实现的核心问题**：JS 用 `document.querySelectorAll('select option')` **全页扫描所有 select 的 option**，与传入的 `index` **完全无关**——取到的 `entry` 形同虚设。多 select 页面返回混杂数据，LLM 无法定位到「目标那一个 select」。`session.py:1620-1623` 注释明确点名此 bug「to be fixed separately」。

### 现状对比（差距表）

| 维度 | browser-use（`default_action_watchdog.py:2776-3036`） | TreeWalker（`actions.py:835-844`） | 差距 |
|---|---|---|---|
| **范围绑定** | `DOM.resolveNode({backendNodeId})` → `Runtime.callFunctionOn({objectId, functionDeclaration: function(){return Array.from(this.options).map(...)}}`，精确到**目标 select** | `document.querySelectorAll('select option')` 全页扫描，与 `index` 无关 | **G1（范围 bug，致命）** |
| **tag 校验** | `checkDropdownElement` 判定 tagName/role/class，非可识别类型抛 `BrowserError`（含 tag/role/classes 诊断信息） | 无校验；非 select 元素的 index 也返回全页 select 数据 | **G2** |
| **成功回显** | `extracted_content`（紧凑选项列表）+ `long_term_memory`（short_term_memory）+ `include_extracted_content_only_once=True` | 裸 `ActionResult(extracted_content=str(options))`，无 long_term_memory | **G3** |
| **输出格式** | 每行 `N: text=<json.dumps>, value=<json.dumps> (selected)`，json 编码保证 LLM 精确复制；末尾追加用法提示 | `str(options)`（Python repr，`{'value': ...}` 格式，含单引号，LLM 难精确复制） | **G4** |
| **异常处理** | 元素 index 失效返回提示而非抛错；空选项返回带 error 的 dict 不抛错；3s 工具超时 / 15s 事件超时 | `execute_js` 异常裸抛，无 try/except | **G5** |
| **测试** | `tests/ci/test_tools.py::test_get_dropdown_options`（native select 启用；ARIA/custom/combobox 全部 skip） | **无测试**（`tests/` 下无 `test_dropdown_options.py`） | **G6** |
| **ARIA menu/listbox** | `role==='menu'||'listbox'` → `querySelectorAll('[role="menuitem"],[role="option"]')`，aria-selected/classList 判选中 | 不支持（native 之外的都返回不了） | **G7（P1）** |
| **custom class** | `classList.contains('dropdown')||'ui'` → `querySelectorAll('.item,.option,[data-value]')` | 不支持 | **G7（P1）** |
| **combobox** | `role==='combobox' && aria-controls` → `_handle_aria_combobox_options`（展开 → 等懒加载 → 读 → 收起，无副作用） | 不支持 | **G7（P1）** |
| **子树搜索** | `searchChildrenForDropdowns(startElement, 4)` 递归向下搜 4 层 | 无 | **G7（P1）** |
| **select_dropdown 同 bug** | — | `_action_select_dropdown`（`actions.py:846-855`）用 `document.querySelectorAll('select')[0]`，只取页面上第一个 select，与 index 无关 | **G8（P1，强相关）** |
| **click 误点 select 降级** | `click` 误点 `<select>` 时自动调 `dropdown_options`（`service.py:711-723`） | `_action_click` SELECT 分支直接读选项不降级 | **G9（P1）** |

---

## 已确认的决策

本方案采用以下决策（论证见 [§关键技术决策说明](#关键技术决策说明)）：

1. **复用 `fetch_select_options`**：P0 直接调用 `browser.fetch_select_options(entry.backend_node_id)`，精确绑定到目标 select，修掉 G1。session 层**不改**（`fetch_select_options` 已稳定，且 click SELECT 分支已依赖它）。
2. **action 层 `enumerate` 补 index**：`fetch_select_options` 返回 `[{value,text,selected}]` 无序号；action 层 `enumerate` 补，不动 session 结构。
3. **加 tag 校验**：`entry.tag_name.upper() != 'SELECT'` 时返回友好 error（不抛 `BrowserError`，本项目无该类型），覆盖 G2。
4. **成功回显（short/long 分离）**：`extracted_content` = 紧凑选项列表（`N: text=..., value=... (selected)`）；`long_term_memory` = `Got N options from [SELECT] {label} at index N`（≤120 字，仿 `_describe_upload`）。
5. **输出格式**：每行 json 编码 text/value（`json.dumps` 保证含引号/特殊字符的文本可精确复制），末尾追加 `Use the value in select_dropdown(index=N, value=...)`（**本项目参数名是 value 不是 text**）。
6. **try/except 软降级**：`fetch_select_options` 抛异常 → `ActionResult(error=f"Failed to read select options: {e}")`，对齐 click SELECT 分支 `actions.py:344-350`。
7. **不引入 `include_extracted_content_only_once`**：本项目 `ActionResult` 无此字段（`views.py:8-37`），改动面大；dropdown_options 是只读幂等，每次回显选项内容反而有助于 LLM 决策。
8. **select_dropdown 同 bug 列为 P1**：不扩大 P0 范围（它是另一个工具，需新增 session 方法）。
9. **ARIA/custom/combobox 列为 P1**：本项目连 native 都没做对，P1 工程量大。

---

## 改动文件

### 1. `src/tree_walker/tools/actions.py` —— 重写 `_action_dropdown_options`（P0）

定位：`actions.py:835-844`（替换整段）。

```python
async def _action_dropdown_options(self, params: dict, browser: BrowserSession) -> ActionResult:
	"""读取指定 index 的 <select> 的全部 option。

	复用 session.fetch_select_options（DOM.resolveNode + Runtime.callFunctionOn），
	精确绑定到目标 select，修掉全局 querySelectorAll('select option') 范围 bug。
	对齐 _describe_click / _describe_upload 的成功回显 + try/except 软降级规范。
	"""
	index = params["index"]
	entry, error = await self._get_element_by_index(index, browser)
	if error:
		return error

	# G2: tag 校验 —— 非 <select> 直接报错，不返回全页 select 数据
	tag = (getattr(entry, "tag_name", "") or "").upper()
	if tag != "SELECT":
		return ActionResult(
			error=(
				f"Index {index} is a [{tag}] element, not a <select>. "
				f"dropdown_options only supports native <select>. "
				f"For ARIA menu/listbox or custom dropdowns, use click to expand and read options manually."
			)
		)

	# G1: 复用 fetch_select_options，精确绑定到目标 select（与 click SELECT 分支一致）
	backend_id = getattr(entry, "backend_node_id", None)
	try:
		raw_options = await browser.fetch_select_options(backend_id)
	except Exception as e:
		return ActionResult(error=f"Failed to read select options: {e}")

	# G4: 格式化输出 —— json 编码 text/value 保证含引号/特殊字符的文本可精确复制
	# action 层 enumerate 补 index（fetch_select_options 返回结构无序号，不动 session）
	lines: list[str] = []
	for i, opt in enumerate(raw_options):
		text = json.dumps(opt.get("text", ""))
		value = json.dumps(opt.get("value", ""))
		status = " (selected)" if opt.get("selected") else ""
		lines.append(f"{i}: text={text}, value={value}{status}")

	# 末尾追加用法提示（本项目 select_dropdown 参数名是 value 不是 text）
	hint = f"Use the value in select_dropdown(index={index}, value=...)"
	extracted = hint if not lines else "\n".join(lines) + "\n" + hint

	# G3: 成功回显（short/long 分离）
	# extracted_content = 紧凑选项列表（靠 ActionResult.__str__ 的 500 字符截断自然兜底）
	# long_term_memory  = 简短摘要（≤120 字，仿 _describe_upload）
	memory = (
		f"Got {len(raw_options)} options from "
		f"{self._describe_dropdown(entry, index)}"
	)
	return ActionResult(extracted_content=extracted, long_term_memory=memory)
```

> **依赖**：用到 `json.dumps`，需在 `actions.py` 顶部新增 `import json`（当前文件头只有 `import os`，已核对；`find_text`/`upload_file` 等已有实现暂未在 actions.py 内用 json，故需补这条 import）。

### 2. `src/tree_walker/tools/actions.py` —— 新增 `_describe_dropdown` helper（P0）

定位：紧邻 `_describe_upload`（`actions.py:457-479`）之后，与其同风格（staticmethod，属性优先级链 + 60 字截断）。

```python
@staticmethod
def _describe_dropdown(entry: Any, index: int) -> str:
	"""Build a human-readable dropdown echo, mirroring _describe_click /
	_describe_upload style.

	select 元素通常没有 placeholder/value（value 是当前选中值），故优先级链用
	aria-label/title/name/id，再退到 node_value，最后退到 tag。Bounded to ~60 chars.
	"""
	tag = (getattr(entry, "tag_name", "") or "").upper() or "SELECT"
	attrs = getattr(entry, "attributes", {}) or {}
	for key in ("aria-label", "title", "name", "id"):
		v = attrs.get(key)
		if v:
			v = v.strip()
			if len(v) > 60:
				v = v[:60] + "..."
			return f"[{tag}] {v!r} at index {index}"
	node_value = (getattr(entry, "node_value", "") or "").strip()
	if node_value:
		if len(node_value) > 60:
			node_value = node_value[:60] + "..."
		return f"[{tag}] {node_value!r} at index {index}"
	return f"[{tag}] at index {index}"
```

> 与 `_describe_click`/`_describe_upload` 一致：跳过 `value`/`placeholder`（select 的 value 是当前选中值、placeholder 不适用），优先 `aria-label/title/name/id`。

### 3. `src/tree_walker/browser/session.py` —— **不改**（P0）

`fetch_select_options`（`session.py:1616-1652`）已是正确实现，返回 `[{value,text,selected}]`。P0 复用即可，序号在 action 层 `enumerate` 补，避免改动 click SELECT 分支（`actions.py:344-350`）已依赖的返回结构。

P1 若需 ARIA/custom/combobox，才新增 session 方法（见 [§P1 进阶蓝图](#p1-进阶蓝图)）。

### 4. `tests/test_dropdown_options.py` —— 新增（P0）

参照 `tests/test_upload_file.py` 模式（`unittest.mock.AsyncMock` + `MagicMock`，构造 `EnhancedDOMTreeNode` 进 `SerializedDOMState.selector_map`，经 `Tools().execute("dropdown_options", {...}, browser, browser_state=state)` 调用，mock 边界为 `fetch_select_options`/`get_state`，**不碰 CDP 原语**，显式 `@pytest.mark.asyncio`）。完整骨架见 [§测试用例清单](#测试用例清单)。

### 5. 附带文档同步（建议本次实现一并做）

更新 `docs/Tools技术细节/04_动作清单与CDP映射.md` 的 §4.4（详见 [§文档同步](#文档同步)）。

---

## 关键技术决策说明

### D1｜500 字符截断 → short/long 分离（推荐唯一方案）

**痛点**：dropdown 选项常远超 500 字符（一个国家选择器上百项）；而 `ActionResult.__str__`（`views.py:27-37`）把 `extracted_content` 截断到 `display_max_chars=500`（ClassVar）。browser-use 用 `include_extracted_content_only_once=True`（`service.py:1633`）规避——但本项目 `ActionResult` **没有这个字段**（`views.py:8-37` 核对确认）。

**决策**：**分离 short/long**，对齐 browser-use 的 `short_term_memory` / `long_term_memory` 思路但不引入新字段：

- `extracted_content`（short）：紧凑选项列表 `N: text=..., value=... (selected)`，靠 `__str__` 的 500 字符截断**自然兜底**。截断发生时 LLM 看到的虽不完整，但 dropdown_options 是**只读幂等**——LLM 需要时再调一次即可，不产生副作用。注意：测试中直接访问 `result.extracted_content` 属性拿到的是**原始值（不截断）**，只有 `str(result)` 才触发 500 截断，故测试断言不受影响。
- `long_term_memory`（long，跨步记忆）：`Got N options from [SELECT] {label} at index N`，≤120 字，保证**不超 500 截断**、稳定进入长期记忆。

**为何不加 `include_extracted_content_only_once`**：
1. 本项目 `ActionResult` 无此字段，引入需改模型 + 所有回显点（`navigate`/`click`/`input_text`/`find_text`/`upload_file` 全部），改动面与风险不成比例。
2. dropdown_options 是只读查询，每次回显选项内容反而有助于 LLM 多步决策（不像 click 那种一次性动作，回显一次即可）。

**为何不在回显里加「选项较多已截断」提示**：long_term_memory 摘要已带选项数 `N`，LLM 看到 `Got 137 options` 自然知道量大；与其多占 token 提示「已截断」，不如让 short 列表尽量紧凑。若 LLM 确需完整列表，再调一次同一工具即可。

### D2｜回显提示语对齐（确认 value 不是 text）

**确认**：本项目 `SelectDropdownParams`（`models.py:119-122`）参数名是 **`value`**（不是 browser-use 的 `text`）。故提示语必须是：

```
Use the value in select_dropdown(index=N, value=...)
```

若照搬 browser-use 的 `text=...`，LLM 会传 `text=` 参数，`extra="forbid"` 的 Pydantic 模型（`models.py:120`）直接 `ValidationError`，工具调用失败。

### D3｜是否一并修 select_dropdown → 放 P1，不扩大 P0 范围

**理由**：

1. `select_dropdown`（`actions.py:846-855`）有完全相同的 `[0]` 全局 bug（`document.querySelectorAll('select')[0]`），且 dropdown_options 的价值最终依赖 select_dropdown 可用。
2. 但修它需要**新的 session 方法** `set_select_option(backend_node_id, value)`（`DOM.resolveNode` → `callFunctionOn` 找到匹配 option 设 `selected` + dispatch `change`/`input`），是独立的工程单元。
3. P0 先把「读」做对（dropdown_options），「写」在 P1 蓝图（见 [§P1.7](#7-select_dropdown-同-bug-修复g8强相关)）一并修，便于一次测试覆盖读写闭环。select_dropdown 有自己的注册项与文档章节（§4.19），属另一工具，遵循既有单工具聚焦模式。

### D4｜fetch_select_options 返回结构 → action 层 enumerate 补 index

`fetch_select_options`（`session.py:1616-1652`）返回 `[{value,text,selected}]`，无序号。browser-use 的 option 结构带 `index`（`default_action_watchdog.py` 的 `checkDropdownElement` 内部 enumerate）。两种补法：

| 方案 | 改动点 | 风险 |
|---|---|---|
| **A. session 层补 index**（改 `fetch_select_options` 返回 `[{value,text,selected,index}]`） | `session.py:1638-1644` JS + `actions.py:344-350` click SELECT 分支 + 其测试 | 改已稳定接口，波及 click 分支 |
| **B. action 层 enumerate**（推荐） | 仅 `_action_dropdown_options` 内 `enumerate` | 零外溢 |

选 **B**：`enumerate` 在 action 层补，session 层零改动，click SELECT 分支不受影响。

### D5｜ARIA/custom/combobox 放 P1

**理由**：
1. 本项目连 native `<select>` 都没做对（G1 致命），先稳 native 覆盖绝大多数表单场景。
2. P1 需要大量新工程：
   - 4 种类型判定（native select / ARIA menu·listbox / custom class / combobox）的 JS 注入；
   - combobox 的展开-收起逻辑（`aria-controls` + focus/click 套件展开 + `sleep 0.5s` 等懒加载 + 读后 `blur + Escape` 收起，**保证无副作用**）；
   - 子树递归 `searchChildrenForDropdowns(startElement, 4)`；
   - 新的 session 方法（如 `fetch_aria_options` / `expand_and_fetch_combobox_options`）。
3. browser-use 自身的 ARIA/custom/combobox 测试**全部 skip**（`tests/ci/interactions/test_dropdown_aria_menus.py`），说明连参照对象的这些路径都未充分验证，照搬风险高。先 native 稳，P1 再分类型逐步推进。

---

## P1 进阶蓝图

> P1 仅画蓝图，给出每项所需的 session 新方法和大致 JS 注入逻辑，不在本次落地。

### 1. ARIA menu / listbox（G7）

- **session 新方法**：`fetch_aria_options(backend_node_id)`。
- **JS 注入逻辑**（对齐 `default_action_watchdog.py` type='aria'）：`DOM.resolveNode` → `callFunctionOn`，目标元素 `querySelectorAll('[role="menuitem"],[role="option"]')`，`aria-selected==='true' || classList.contains('selected')` 判选中。

### 2. custom class（Semantic UI 等）（G7）

- **session 新方法**：`fetch_custom_class_options(backend_node_id)`。
- **JS 注入逻辑**（type='custom'）：目标元素 `classList.contains('dropdown')||'ui'` 时，`querySelectorAll('.item,.option,[data-value]')`，`classList.contains('selected')||'active'` 判选中；value 优先 `data-value`。

### 3. combobox（G7）

- **session 新方法**：`expand_and_fetch_combobox_options(backend_node_id)`。
- **JS 注入逻辑**（对齐 `_handle_aria_combobox_options:3038-3239`）：`role==='combobox' && aria-controls` 时：
  1. 若 `aria-expanded!=='true'`，派发完整事件套件（focus/focusin/click/mousedown）展开；
  2. `sleep 0.5s` 等懒加载；
  3. `getElementById(aria-controls)` 找 listbox → `[role=option]` 或降级 `li`；
  4. **读完后 `blur + Escape` 收起**，保证无副作用。
- 需超时治理（browser-use 用 15s 事件超时应对上千懒加载选项）。

### 4. 子树搜索（G7）

- 目标元素自身不匹配任何类型时，递归向下搜 4 层（`searchChildrenForDropdowns(startElement, 4)`），source 标记 `child-depth-N` 便于诊断。

### 5. 懒加载等待（G7）

- combobox 展开后 `sleep 0.5s`；若选项数持续增长，需轮询直到稳定（参考 MutationObserver 或轮询 + 超时）。

### 6. 空选项软回显（G5 进阶）

- P0 已覆盖空选项（`raw_options == []` 时 extracted 只剩 hint 行）；P1 可对 native select 空选项也返回带诊断的提示（如 `<select>` 无 `<option>` 子节点）。

### 7. select_dropdown 同 bug 修复（G8，强相关）

- **session 新方法**：`set_select_option(backend_node_id, value)`。
- **JS 注入逻辑**：`DOM.resolveNode` → `callFunctionOn` 找到 `value` 匹配的 option 设 `selected=true` + dispatch `change`/`input` 事件（覆盖 React 等框架），替换 `actions.py:846-855` 的全局 `[0]` bug。

### 8. click 误点 select 降级（G9）

- 对齐 `service.py:711-723`：`_action_click` 误点 `<select>` 时，自动降级调用 `_action_dropdown_options` 把选项读出来给 LLM。

---

## CDP 调用清单（变更后）

| 改动 | P0 变更后 CDP 链 | 行号 |
|---|---|---|
| **before（G1 bug）** | `Runtime.evaluate({expression: "Array.from(document.querySelectorAll('select option'))..."})` × 1（全局，与 index 无关） | `session.py:1485`（`execute_js`） |
| **after（P0 修复）** | `DOM.resolveNode({backendNodeId})` × 1 → `Runtime.callFunctionOn({objectId, functionDeclaration: function(){return Array.from(this.options).map(...)}, returnByValue:True})` × 1（精确绑定到目标 select） | `session.py:1628-1650`（`fetch_select_options`） |

CDP 调用次数：**仍为 2 次**（resolveNode + callFunctionOn），与原 1 次 evaluate 相比多 1 次但换取**正确的范围绑定**（这是修 bug 的必要代价）。复用已稳定的 `fetch_select_options`，无新 CDP 代码。

---

## 涉及文件清单

| 文件 | 改动类型 | 说明 |
|---|---|---|
| `src/tree_walker/tools/actions.py` | **改**（P0） | 新增 `import json`；重写 `_action_dropdown_options`（`:835-844`）；新增 `_describe_dropdown` staticmethod（紧邻 `_describe_upload` `:457-479`） |
| `tests/test_dropdown_options.py` | **新增**（P0） | 对齐 `test_upload_file.py` 模式的三层测试 |
| `src/tree_walker/browser/session.py` | 不改（P0） | 复用 `fetch_select_options`（`:1616-1652`）；P1 才新增方法 |
| `src/tree_walker/tools/models.py` | 不改 | `DropdownOptionsParams{index:int}` 不变（`:114-116`） |
| `src/tree_walker/agent/views.py` | 不改 | `ActionResult` 不引入 `include_extracted_content_only_once` |
| `docs/Tools技术细节/04_动作清单与CDP映射.md` | **改**（P0 文档同步） | §4.4 行号纠错 + CDP 改 `DOM.resolveNode`+`callFunctionOn`（见 [§文档同步](#文档同步)） |

### 测试用例清单

骨架（对齐 `tests/test_upload_file.py`）：

```python
"""Tests for dropdown_options: scope bug fix, tag validation, success echo,
json-encoded output, and error mapping.

Covers the action layer (Tools._action_dropdown_options), mirroring
tests/test_upload_file.py:
- native select: echoes 'Got N options from [SELECT] ...' in long_term_memory,
  extracted_content lists each option json-encoded with a select_dropdown hint
- non-select element: friendly error (no full-page select leak)
- index absent from selector_map: returns error without touching CDP
- fetch_select_options raising -> friendly 'Failed to read select options:'
- empty options: soft echo (just the hint line, no exception)
- output format: text/value json-encoded (quotes preserved) + hint uses 'value'
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tree_walker.browser.views import (
	BrowserStateSummary,
	EnhancedDOMTreeNode,
	NodeType,
	SerializedDOMState,
)
from tree_walker.tools.actions import Tools


def _make_entry(
	*,
	tag: str = "SELECT",
	backend_node_id: int = 42,
	attributes: dict[str, str] | None = None,
	node_value: str = "",
) -> EnhancedDOMTreeNode:
	return EnhancedDOMTreeNode(
		node_id=backend_node_id,
		backend_node_id=backend_node_id,
		node_type=NodeType.ELEMENT_NODE,
		node_name=tag.upper(),
		node_value=node_value,
		attributes=attributes or {},
	)


def _make_state(selector_map: dict[int, EnhancedDOMTreeNode]) -> BrowserStateSummary:
	return BrowserStateSummary(
		url="https://example.com",
		title="",
		dom_state=SerializedDOMState(
			_root=None,
			selector_map=selector_map,
			element_tree_text="",
			file_input_backend_ids=[],
		),
	)


def _make_browser(*, options=None, raises=None) -> MagicMock:
	bs = MagicMock()
	bs.fetch_select_options = AsyncMock(
		side_effect=raises if raises else (options if options is not None else [])
	)
	bs.get_state = AsyncMock(return_value=_make_state({}))
	return bs


class TestDropdownOptionsAction:
	@pytest.mark.asyncio
	async def test_native_select_echoes_options_and_memory(self):
		entry = _make_entry(backend_node_id=7, attributes={"aria-label": "Country"})
		state = _make_state({3: entry})
		browser = _make_browser(options=[
			{"value": "us", "text": "United States", "selected": True},
			{"value": "ca", "text": 'Canada "North"', "selected": False},
		])
		result = await Tools().execute(
			"dropdown_options", {"index": 3}, browser, browser_state=state,
		)
		# G1: 用目标 select 的 backend_node_id 调用（范围绑定）
		browser.fetch_select_options.assert_awaited_once_with(7)
		assert result.error is None
		# G3: long_term_memory 带选项数 + label
		assert "Got 2 options" in result.long_term_memory
		assert "[SELECT]" in result.long_term_memory
		assert "index 3" in result.long_term_memory
		# G4: json 编码保留双引号（json.dumps('Canada "North"') == '"Canada \\"North\\""')
		assert '"Canada \\"North\\""' in result.extracted_content
		# 选中标记
		assert " (selected)" in result.extracted_content
		# D2: 提示语用本项目参数名 value
		assert "select_dropdown(index=3, value=...)" in result.extracted_content

	@pytest.mark.asyncio
	async def test_non_select_element_returns_error_without_fetch(self):
		entry = _make_entry(tag="DIV", backend_node_id=7)
		state = _make_state({3: entry})
		browser = _make_browser()
		result = await Tools().execute(
			"dropdown_options", {"index": 3}, browser, browser_state=state,
		)
		assert result.error is not None
		assert "[DIV]" in result.error
		assert "not a <select>" in result.error
		# G2: 早退，不碰 CDP
		browser.fetch_select_options.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_missing_index_returns_error_without_fetch(self):
		state = _make_state({})  # index 3 absent
		browser = _make_browser()
		result = await Tools().execute(
			"dropdown_options", {"index": 3}, browser, browser_state=state,
		)
		assert result.error is not None
		browser.fetch_select_options.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_fetch_raises_maps_to_friendly_error(self):
		entry = _make_entry(backend_node_id=7)
		state = _make_state({3: entry})
		browser = _make_browser(raises=RuntimeError("CDP target detached"))
		result = await Tools().execute(
			"dropdown_options", {"index": 3}, browser, browser_state=state,
		)
		assert result.error is not None
		assert "Failed to read select options" in result.error
		assert "CDP target detached" in result.error

	@pytest.mark.asyncio
	async def test_empty_options_soft_echo_just_hint(self):
		entry = _make_entry(backend_node_id=7)
		state = _make_state({3: entry})
		browser = _make_browser(options=[])
		result = await Tools().execute(
			"dropdown_options", {"index": 3}, browser, browser_state=state,
		)
		assert result.error is None
		assert "Got 0 options" in result.long_term_memory
		# 空选项：extracted 仅剩提示行
		assert "select_dropdown(index=3, value=...)" in result.extracted_content

	@pytest.mark.asyncio
	async def test_output_format_json_encoded_with_hint(self):
		entry = _make_entry(backend_node_id=7)
		state = _make_state({3: entry})
		# 含引号 / 换行的文本，验证 json 编码可精确复制
		browser = _make_browser(options=[
			{"value": 'a"b', "text": "x\ny", "selected": False},
		])
		result = await Tools().execute(
			"dropdown_options", {"index": 3}, browser, browser_state=state,
		)
		assert "0: text=" in result.extracted_content
		assert "value=" in result.extracted_content
		assert result.extracted_content.rstrip().endswith(
			"Use the value in select_dropdown(index=3, value=...)"
		)
```

| 用例 | 覆盖缺口 | 断言要点 |
|---|---|---|
| `test_native_select_echoes_options_and_memory` | G1/G3/G4/D2 | `fetch_select_options` 用 backend_node_id=7 调用（范围绑定）；long_term_memory 带 `Got 2 options`+`[SELECT]`+`index 3`；extracted json 编码保留引号；含 `(selected)`；提示语用 `value` |
| `test_non_select_element_returns_error_without_fetch` | G2 | `[DIV] not a <select>`；`fetch_select_options` 未被调用（早退） |
| `test_missing_index_returns_error_without_fetch` | G2/索引 | index 不在 selector_map → error，不碰 CDP |
| `test_fetch_raises_maps_to_friendly_error` | G5 | `RuntimeError` → `Failed to read select options: CDP target detached` |
| `test_empty_options_soft_echo_just_hint` | G5/空选项 | `Got 0 options`；extracted 仅剩提示行，无 error |
| `test_output_format_json_encoded_with_hint` | D2/G4 | 末行正好是 `Use the value in select_dropdown(index=3, value=...)` |

---

## 已知限制（本次不处理）

1. **ARIA menu/listbox / custom class / combobox 不支持**（G7，P1）：P0 只支持 native `<select>`；非 select 下拉（Semantic UI、Material、role=combobox 等）会走 tag 校验 error 分支，提示用 click 手动展开。理由见 [§D5](#d5ariacustomcombobox-放-p1)。
2. **select_dropdown 同 bug 未修**（G8，P1）：`actions.py:846-855` 仍用 `querySelectorAll('select')[0]`，多 select 页面会改错元素。需 P1 新增 `session.set_select_option(backend_node_id, value)`。dropdown_options 读出的 value 现阶段只能靠 LLM 用 `evaluate` 或其他方式落选。
3. **500 字符截断**：选项很多时 extracted_content 被 `ActionResult.__str__` 截到 500，LLM 看不全；靠 long_term_memory 的 `Got N options` 摘要 + 工具只读幂等可重调缓解。不引入 `include_extracted_content_only_once`（理由见 [§D1](#d1500-字符截断--shortlong-分离推荐唯一方案)）。
4. **click 误点 select 不降级**（G9，P1）：`_action_click` SELECT 分支不自动转 dropdown_options。
5. **懒加载选项**（combobox 等展开后才加载的选项）：native select 无此问题；P1 combobox 路径才需 `sleep + 轮询`。
6. **`_describe_dropdown` 优先级链不含 `value`/`placeholder`**：select 的 `value` 是当前选中值、placeholder 不适用，故用 `aria-label/title/name/id`。若某些自定义 select 用其他属性标识，P1 再扩。

---

## 文档同步

`docs/Tools技术细节/04_动作清单与CDP映射.md` §4.4 需纠错：

1. **行号纠错**：§4.4 标的 `[actions.py:337-346]` **错误**（337-346 实际是 `_action_click` 的 SELECT 分支）。真值 **`actions.py:835-844`**。
2. **代码示例更新**：§4.4 的 `_action_dropdown_options` 代码块替换为本方案 [§改动文件 1](#1-src_tree_walkertoolsactionspy--重写-_action_dropdown_optionsp0) 的新实现。
3. **CDP 调用清单更新**：

   | CDP 命令 | 主要参数 | 行号 |
   |---|---|---|
   | ~~`Runtime.evaluate`~~ → `DOM.resolveNode` | `{backendNodeId}` | `session.py:1628` |
   | `Runtime.callFunctionOn` | `{objectId, functionDeclaration: function(){return Array.from(this.options).map(...)}, returnByValue:True}` | `session.py:1633` |

4. **注意事项更新**：删除「JS 实际查询的是页面上所有 select……已知限制」「`str(list)` LLM 需自行解析」，改为「复用 `fetch_select_options` 精确绑定到目标 select；输出 json 编码 + 末尾 select_dropdown 用法提示」。

> 同步核对 §4.19 `select_dropdown`：其行号同样存疑（P1 修 select_dropdown 时一并纠），本次不动。

---

## 验证步骤

### 自动化测试

```powershell
# 仅跑新增测试
uv run python -m pytest tests/test_dropdown_options.py -v

# 连同相关回归（click SELECT 分支共享 fetch_select_options，确保未回归）
uv run python -m pytest tests/test_dropdown_options.py tests/test_click.py -x -v

# 全量回归 + 覆盖率（CLAUDE.md 目标 >85%）
uv run python -m pytest tests/ -x -v
uv run python -m pytest tests/ --cov=tree_walker.tools --cov=tree_walker.browser --cov-report=term-missing
```

预期：6 个用例全绿；`test_click.py` 中 SELECT 分支用例不回归（session 层未改）。

### 手测步骤

1. **native select 正常读**：
   - 打开任意含 `<select>` 的页面（如 `https://www.w3schools.com/tags/tryit.asp?filename=tryhtml_select`）。
   - 调 `dropdown_options(index=N)`（N 为该 select 在 DOM 树的 index）。
   - 预期：extracted 列出全部 option，每行 `i: text="...", value="..."`，末行 `Use the value in select_dropdown(index=N, value=...)`；long_term_memory `Got M options from [SELECT] ...`。

2. **多 select 页面范围正确**（核心 bug 验证）：
   - 打开含**两个以上 select** 的页面。
   - 对第二个 select 调 `dropdown_options`。
   - 预期：只返回**第二个 select** 的 option（修复前会返回所有 select 的混杂 option）。

3. **非 select 元素报错**：
   - 对一个 `<div>` 或 `<button>` 的 index 调 `dropdown_options`。
   - 预期：`error="Index N is a [DIV] element, not a <select>..."`，不返回任何 option。

4. **含引号的选项文本**：
   - 构造 `<option value='a"b'>He said "hi"</option>`。
   - 预期：extracted 显示 `text="He said \"hi\""`, `value="a\"b"`（json 编码，引号可精确复制）。

5. **CDP 异常软降级**：
   - 在 select 元素 index 失效场景（如导航后旧 index）调 `dropdown_options`。
   - 预期：`error="Failed to read select options: ..."`，不裸抛异常中断 agent loop。
