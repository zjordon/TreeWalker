# select_dropdown 工具完善方案

> 参照 browser-use 的 `select_dropdown` 实现完善本项目 `select_dropdown` 工具。
>
> 本文为**提案文档**（不改源码），给出差距分析、P0 改进方案与可落地的代码片段，供后续实施直接参照。范围：**P0 核心改进（native `<select>` 完整选择链：范围 bug 修复 + tag 校验 + 框架兼容设值 + 读回验证 + 框架回退时点击回退 + 选项未命中软回显 + 成功回显 + 测试）+ P1 进阶蓝图（懒加载重试 / ARIA menu·listbox / custom class / combobox / 子树搜索 / click 误点 select 降级）**。

---

## 背景（为什么改）

参照对象：

- **browser-use 实际代码**：`browser_use/browser/watchdogs/default_action_watchdog.py:3241-3695`（`on_SelectDropdownOptionEvent`，CDP `DOM.resolveNode` + `Runtime.callFunctionOn` 跑 `selection_script` 精确绑定到目标 select；native 分支 `:3273-3344` 做 focus → 三种方式设值（`element.value` / `option.selected` / `element.selectedIndex`）→ dispatch `input`+`change`+`blur` → **读回 `element.value` 验证是否被框架回退**；回退时跑 `click_fallback_script` `:3556-3607`（mousedown/click-on-option/mouseup/change）兜底；选项未命中时返回 `availableOptions` 供 LLM 自纠）+ `browser_use/tools/service.py:1635-1678`（`select_dropdown` 工具包装：瘦壳，事件分发 + 结果解析）。**关键**：browser-use 的这个工具本身就基于 CDP（非 Playwright），核心逻辑可直接迁移。
- **本项目当前**：`src/tree_walker/tools/actions.py:912-921`（`_action_select_dropdown`，`document.querySelectorAll('select')[0]` —— **只操作页面上第一个 select，与传入的 `index` 完全无关**）。
- **可复用的既有资产**：`session.fetch_select_options`（`session.py:1616-1652`，已是正确的 `DOM.resolveNode`+`callFunctionOn` 实现，`dropdown_options` P0 已在用）；`_describe_dropdown`（`actions.py:489-510`，已存在，直接复用）；`SelectDropdownParams`/`ACTION_DEFINITIONS["select_dropdown"]`（`models.py:119-122`/`214-218`，已正确，**不改**）。
- **对齐基准**：本项目已建立的「成功回显 + try/except 软降级 + 三层测试」新规范（`scroll`/`send_keys`/`find_text`/`upload_file`/`dropdown_options`），回显 helper `_describe_dropdown`（`actions.py:489`）。

**当前实现的核心问题**：JS 用 `document.querySelectorAll('select')[0]` **只写页面上第一个 select**，与传入的 `index` **完全无关**——取到的 `entry` 形同虚设。多 select 页面会改错元素。`dropdown_options.md` 已把此 bug 列为强相关 P1（§P1.7），并明确下一块蓝图就是「新增 `session.set_select_option`」。

### 现状对比（差距表）

| 维度 | browser-use（`default_action_watchdog.py:3241-3695`） | TreeWalker（`actions.py:912-921`） | 差距 |
|---|---|---|---|
| **范围绑定** | `DOM.resolveNode({backendNodeId})` → `Runtime.callFunctionOn({objectId, ...})`，精确到**目标 select** | `document.querySelectorAll('select')[0]` 全页第一个 select，与 index 无关 | **G1（范围 bug，致命）** |
| **tag 校验** | JS 内 `tagName.toLowerCase()==='select'` 判定 | 无校验，非 select 也照跑 | **G2** |
| **设值 + 框架事件** | focus → `value`/`selected`/`selectedIndex` 三方式 → `input`+`change`+`blur` | 仅 `select.value=...` + 单个 `change` | **G3** |
| **读回验证** | 设值后 `element.value !== expectedValue` 检测框架回退 | 无 | **G4** |
| **点击回退** | 回退时 `mousedown/click-on-option/mouseup/change` 兜底 | 无 | **G5** |
| **选项未命中** | 返回 `availableOptions`（bulleted 列表）+ long_term_memory 提示，不抛错 | 无 —— value 不匹配时静默写空或报错 | **G6** |
| **成功回显** | `extracted_content`(message) + `long_term_memory` | 裸 `ActionResult()` → `"OK"` | **G7** |
| **异常处理** | resolveNode 失败 / JS 异常 → 结构化错误 | `execute_js` 异常裸抛，仅被 `Tools.execute` 外层兜成 `ActionResult(error=...)` | **G8** |
| **测试** | `tests/ci/test_tools.py`（native 启用；ARIA/custom/combobox 全部 skip） | **无测试**（`tests/` 下无 `test_select_dropdown.py`） | **G9** |
| **匹配策略** | 按 `option.text` **或** `option.value`，大小写不敏感，精确匹配 | 仅按 `value` 精确赋值 | **G10** |
| **懒加载重试** | 全空 option 时 `focus()`+`sleep 1s`+重试一次 | 无 | **G11（P1）** |
| **ARIA/custom/combobox** | 4 种类型判定 + 子树搜索 depth 4 | 不支持 | **G12（P1）** |

---

## 已确认的决策（P0 = native 完整路径）

> P0 范围 = **native `<select>` 完整选择链**（含读回验证 + 点击回退）。懒加载重试 / ARIA / custom / combobox / 子树搜索 → P1 蓝图。论证见 [§关键技术决策说明](#关键技术决策说明)。

1. **新增 session 方法 `set_select_option(backend_node_id, value)`**：`DOM.resolveNode`+`callFunctionOn`，移植 browser-use native 选择链（focus→三方式设值→`input`+`change`+`blur`→读回验证→回退时点击兜底）。覆盖 G1/G3/G4/G5。**session 层不碰 `fetch_select_options`**（已稳定，click SELECT 分支 + dropdown_options 都依赖它）。
2. **action 层重写 `_action_select_dropdown`**：瘦壳 —— tag 校验（G2）→ 调 `set_select_option` → try/except 软降级（G8）→ 成功回显 / 选项未命中软回显可用选项（G6/G7）。
3. **匹配策略：text OR value，大小写不敏感精确匹配**（G10）：参数名仍是 `value`（`models.py:121` 不改），但 JS 同时比对 `option.text` 和 `option.value`，任一相等即选中。dropdown_options 同时返回 text 与 value，LLM 传哪个都能命中。
4. **点击回退进 P0**：读回验证检测到框架回退（`selectionReverted`）时，session 层自动跑 `click_fallback_script`。回退是 native select 在受框架保护站点上的真实失败模式，读回验证若不配套回退则只是「报清晰错误」而非「真正选上」。
5. **懒加载重试 / ARIA / custom / combobox 进 P1**：对齐 dropdown_options P0 的克制纪律，native 先做对。
6. **回显 helper 复用 `_describe_dropdown`**（`actions.py:489`，已存在，不再新增）。
7. **不引入 `include_extracted_content_only_once`**：本项目 `ActionResult` 无此字段（`views.py:8-37`），改动面大；选项未命中回显的可用选项列表对 LLM 多步决策有价值。
8. **不改 `models.py` / `views.py` / 注册机制**：`SelectDropdownParams`、`ACTION_DEFINITIONS["select_dropdown"]`、`_register_all`（`actions.py:235-246` 自动按名绑定 `_action_<name>`）均已就绪。

---

## 改动文件

### 1. `src/tree_walker/browser/session.py` —— 新增 `set_select_option`（P0）

定位：紧邻 `fetch_select_options`（`session.py:1616-1652`）之后、`# ── File operations ──` 注释（`:1654`）之前。新增**两个模块级 JS 常量** + **一个 async 方法**。

**JS 常量**（移植自 browser-use `default_action_watchdog.py:3266-3495` 的 native 分支 + `:3556-3607` 的 click_fallback；仅保留 native，剥除 ARIA/custom/子树搜索 —— 留 P1）：

```python
# Native <select> 选择脚本（移植 browser-use default_action_watchdog.py:3273-3344）。
# 匹配 option.text 或 option.value（大小写不敏感，精确）；命中后 focus → 三方式设值
# → dispatch input+change+blur → 读回 element.value 验证是否被框架回退。
# 未命中或被回退时返回 availableOptions，供 action 层软回显给 LLM 自纠。
_SELECT_OPTION_JS = """
function(targetText) {
	const element = this;
	if (!element || element.tagName.toLowerCase() !== 'select') {
		return { success: false, error: 'Element is not a <select>' };
	}
	const targetLower = (targetText || '').toLowerCase();
	const options = Array.from(element.options);
	for (const option of options) {
		const textLower = (option.text || '').trim().toLowerCase();
		const valueLower = (option.value || '').toLowerCase();
		if (textLower === targetLower || valueLower === targetLower) {
			const expectedValue = option.value;
			element.focus();
			element.value = expectedValue;
			option.selected = true;
			element.selectedIndex = option.index;
			element.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
			element.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
			element.blur();
			if (element.value !== expectedValue) {
				return {
					success: false,
					error: 'Selection was set but reverted by page framework. The dropdown may require clicking.',
					selectionReverted: true,
					targetOption: { text: (option.text || '').trim(), value: expectedValue, index: option.index },
					availableOptions: options.map(o => ({ text: (o.text || '').trim(), value: o.value })),
				};
			}
			return {
				success: true,
				message: 'Selected option: ' + (option.text || '').trim() + ' (value: ' + option.value + ')',
				value: option.value,
			};
		}
	}
	return {
		success: false,
		error: 'Option with text or value \\'' + targetText + '\\' not found in select element',
		availableOptions: options.map(o => ({ text: (o.text || '').trim(), value: o.value })),
	};
}
"""

# 点击回退脚本（移植 browser-use default_action_watchdog.py:3556-3607）。
# 仅当 selectionReverted=true 时触发：模拟完整手势 mousedown/click-on-option/mouseup/change。
_SELECT_OPTION_CLICK_FALLBACK_JS = """
function(optionIndex) {
	const select = this;
	if (!select || select.tagName.toLowerCase() !== 'select') return { success: false, error: 'Not a select element' };
	const option = select.options[optionIndex];
	if (!option) return { success: false, error: 'Option not found at index ' + optionIndex };
	select.focus();
	select.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
	select.selectedIndex = optionIndex;
	option.selected = true;
	option.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
	select.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
	select.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
	select.blur();
	if (select.value === option.value || select.selectedIndex === optionIndex) {
		return { success: true, message: 'Selected via click fallback: ' + (option.text || '').trim(), value: option.value };
	}
	return { success: false, error: 'Click fallback also failed - framework may block all programmatic selection', finalValue: select.value, expectedValue: option.value };
}
"""
```

**方法**（镜像 `fetch_select_options` 的 resolveNode+callFunctionOn 结构；`arguments` 传值方式对齐既有 `session.py:863` `_is_element_occluded` 的 `arguments: [{"value": x}, {"value": y}]` 写法）：

```python
async def set_select_option(self, backend_node_id: int, value: str) -> dict:
	"""Set the option of the <select> identified by backendNodeId.

	Mirrors browser-use on_SelectDropdownOptionEvent (default_action_watchdog.py
	3241-3695): DOM.resolveNode + Runtime.callFunctionOn running the native-<select>
	selection script — focus → set value three ways (element.value /
	option.selected / element.selectedIndex) → dispatch input+change+blur →
	read back element.value to detect framework reversion. On reversion, runs
	a click fallback (mousedown/click-on-option/mouseup/change). Matches an
	option by text OR value, case-insensitively (exact).

	Scoped to the target select via backendNodeId (NOT querySelectorAll('select')[0],
	which writes the first select on the page regardless of index — the bug fixed
	here). Returns a dict shaped like browser-use's:
	  success: True|False, message?, value?, selectionReverted?, availableOptions?, error?
	Raises on CDP/JS error (caller wraps with a friendly message).
	"""
	resolve = await self.client.send.DOM.resolveNode(
		{"backendNodeId": backend_node_id},
		session_id=self.current_session_id,
	)
	object_id = resolve["object"]["objectId"]

	result = await self.client.send.Runtime.callFunctionOn(
		{
			"objectId": object_id,
			"functionDeclaration": _SELECT_OPTION_JS,
			"arguments": [{"value": value}],
			"returnByValue": True,
		},
		session_id=self.current_session_id,
	)
	selection = result.get("result", {}).get("value", {}) or {}

	# 框架回退 → 点击回退（对齐 browser-use default_action_watchdog.py:3550-3617）
	if selection.get("selectionReverted"):
		option_index = selection.get("targetOption", {}).get("index", 0)
		fallback = await self.client.send.Runtime.callFunctionOn(
			{
				"objectId": object_id,
				"functionDeclaration": _SELECT_OPTION_CLICK_FALLBACK_JS,
				"arguments": [{"value": option_index}],
				"returnByValue": True,
			},
			session_id=self.current_session_id,
		)
		fb = fallback.get("result", {}).get("value", {}) or {}
		if fb.get("success"):
			return {"success": True, "message": fb.get("message"), "value": fb.get("value", value)}
		# 回退也失败 —— 返回原始结构化错误（带 availableOptions，供 action 层软回显）
	return selection
```

### 2. `src/tree_walker/tools/actions.py` —— 重写 `_action_select_dropdown`（P0）

定位：替换 `actions.py:912-921` 整段。瘦壳风格对齐 `_action_dropdown_options`（`:860-910`）—— tag 校验 → session 方法 → try/except 软降级 → 成功回显 / 选项未命中软回显。

```python
async def _action_select_dropdown(self, params: dict, browser: BrowserSession) -> ActionResult:
	"""在指定 index 的 <select> 中选择选项。

	复用 session.set_select_option（DOM.resolveNode + Runtime.callFunctionOn），
	精确绑定到目标 select，修掉全局 querySelectorAll('select')[0] 范围 bug。
	移植 browser-use native 选择链：focus→三方式设值→input/change/blur→读回验证，
	框架回退时由 session 层走点击回退。选项未命中软错误回显可用选项（对齐
	dropdown_options / upload_file 的成功回显 + try/except 软降级规范）。
	"""
	index = params["index"]
	entry, error = await self._get_element_by_index(index, browser)
	if error:
		return error

	# G2: tag 校验 —— 非 <select> 直接报错（对齐 dropdown_options 早退，不碰 CDP）
	tag = (getattr(entry, "tag_name", "") or "").upper()
	if tag != "SELECT":
		return ActionResult(
			error=(
				f"Index {index} is a [{tag}] element, not a <select>. "
				f"select_dropdown only supports native <select>. "
				f"For ARIA menu/listbox or custom dropdowns, use click to expand and select manually."
			),
		)

	# G1: 复用 set_select_option，精确绑定到目标 select 的 backend_node_id
	backend_id = getattr(entry, "backend_node_id", None)
	value = params["value"]
	try:
		result = await browser.set_select_option(backend_id, value)
	except Exception as e:
		return ActionResult(error=f"Failed to select option: {e}")

	# G7: 成功回显（short/long 分离，对齐 _describe_dropdown + json.dumps 规范）
	desc = self._describe_dropdown(entry, index)
	if result.get("success"):
		message = result.get("message", f"Selected option: {value}")
		memory = f"Selected {json.dumps(value)} in {desc}"
		return ActionResult(extracted_content=message, long_term_memory=memory)

	# G6: 选项未命中 / 框架拦截且回退也失败 —— 回显可用选项供 LLM 自纠
	# （对齐 browser-use short/long_term_memory 思路；格式与 dropdown_options 一致）
	available = result.get("availableOptions") or []
	if available:
		lines = [
			f"{i}: text={json.dumps(o.get('text', ''))}, value={json.dumps(o.get('value', ''))}"
			for i, o in enumerate(available)
		]
		extracted = "\n".join(lines) + "\n" + f"Use the value in select_dropdown(index={index}, value=...)"
		memory = f"Couldn't select {json.dumps(value)} in {desc} (not an available option)"
		return ActionResult(extracted_content=extracted, long_term_memory=memory)

	# 兜底：无可用选项的失败（如 resolveNode 异常已在上文 try 兜住，此处为 JS 返回的纯错误）
	err = result.get("error", f"Failed to select option: {value}")
	return ActionResult(error=err)
```

> **依赖**：`json` 已在 `actions.py:6` 导入（dropdown_options P0 已加），`_describe_dropdown` 已在 `actions.py:489` 存在，**无新增 import / 无新增 helper**。

### 3. `tests/test_select_dropdown.py` —— 新增（P0）

参照 `tests/test_dropdown_options.py` 模式（`unittest.mock.AsyncMock` + `MagicMock`，构造 `EnhancedDOMTreeNode` 进 `SerializedDOMState.selector_map`，经 `Tools().execute("select_dropdown", {...}, browser, browser_state=state)` 调用，mock 边界为 **`set_select_option`** 而非 CDP 原语，显式 `@pytest.mark.asyncio`）。完整骨架见 [§测试用例清单](#测试用例清单)。

### 4. 附带文档同步（建议本次实现一并做）

更新 `docs/Tools技术细节/04_动作清单与CDP映射.md` 的 §4.19（详见 [§文档同步](#文档同步)）。

---

## 关键技术决策说明

### D1｜匹配策略：text OR value，大小写不敏感（G10）

browser-use 对 `option.text` 和 `option.value` 都做大小写不敏感的**精确**比较，任一命中即选。本项目参数名是 `value`（`models.py:121` 不改），但 dropdown_options 回显同时给 text 和 value，LLM 传哪个都应命中。若改成仅按 `value` 精确赋值，LLM 误传可见文本时直接失败 —— 失去「完善」的意义。**采纳 browser-use 行为**。

> 边界：若两个 option 仅大小写不同，取第一个匹配项（与 browser-use 一致）。可接受。

### D2｜点击回退进 P0（G5）

读回验证（`element.value !== expectedValue`）若不配套回退，则受框架保护的 select（拦截程序化 `value` 赋值的站点）只会得到「选上又被改回去」的清晰错误，**真正问题未解决**。点击回退（`mousedown`+`click-on-option`+`mouseup`+`change`）是 browser-use 对此的标准解法，且与 native 选择链同属一个内聚单元。读回验证 + 点击回退一起构成「native 完整路径」。**回退进 P0**。

> 回退仅触发于 `selectionReverted=true`（实际受框架保护的少数站点），不增加常规路径开销。

### D3｜读回验证进 P0（G4）

设值后立刻 `element.value !== expectedValue` 比较，零额外 CDP 往返（在原 callFunctionOn 内完成），是触发 D2 回退的前提，也是向 LLM 报告「选上又被改回」的唯一手段。**进 P0**。

### D4｜`callFunctionOn` 用 `arguments` 传值（而非字符串拼接）

既有先例：`session.py:863`（`_is_element_occluded`）已用 `arguments: [{"value": x}, {"value": y}]` + `function(x, y){...}`。故 `targetText` / `optionIndex` 通过 `arguments` 传入，**不做 `repr()`/`json.dumps()` 字符串拼接**（当前 `actions.py:918` 的 `repr(value)` 拼接是对的，但有转义风险）。与 browser-use 一致，无转义风险。

> 兜底：若实现期发现 CDP client 对 `arguments` 兼容性问题（极低概率，既有 `_is_element_occluded` 已验证可行），回退为 `json.dumps(value)` 拼接。

### D5｜懒加载重试 / ARIA / custom / combobox / 子树搜索 → P1

1. native 是绝大多数表单场景，先做对（G1 致命 bug 先修）。
2. ARIA/custom/combobox 需要大量新工程（4 种类型判定 JS、combobox 展开-收起无副作用逻辑、子树递归、新 session 方法），且 browser-use 自身的这些路径测试**全部 skip**（`tests/ci/interactions/test_dropdown_aria_menus.py`），照搬风险高。
3. 懒加载重试（`focus()`+`sleep 1s`+重试）针对「选项异步填充」的 select，native 同步渲染的 select 无此问题，P1 再加。
4. 对齐 dropdown_options P0 的克制纪律（它也把 ARIA/custom/combobox 全部放 P1）。

### D6｜不改 `fetch_select_options`

`fetch_select_options`（`session.py:1616-1652`）返回 `[{value,text,selected}]`，已稳定，click SELECT 分支 + dropdown_options 都依赖它。select_dropdown 是「写」，新增独立的 `set_select_option`，**不动读路径**，零回归风险。

### D7｜成功回显用 `_describe_dropdown`，不引入 `include_extracted_content_only_once`

- `_describe_dropdown`（`actions.py:489`）已存在，select_dropdown 与 dropdown_options 共用，无需新增。
- 选项未命中时回显的可用选项列表（`availableOptions`）对 LLM 多步决策有价值；本项目 `ActionResult` 无 `include_extracted_content_only_once` 字段（`views.py:8-37`），引入需改模型 + 所有回显点，改动面与风险不成比例。**不引入**。
- short/long 分离（对齐 dropdown_options D1）：`extracted_content`=成功 message 或紧凑选项列表（靠 `ActionResult.__str__` 的 500 字符截断自然兜底）；`long_term_memory`=简短摘要（`Selected X in [SELECT] ... at index N` / `Couldn't select X in ... (not an available option)`）。

---

## CDP 调用清单（变更后）

| 路径 | CDP 链 | 次数 | 行号 |
|---|---|---|---|
| **before（G1 bug）** | `Runtime.evaluate({expression: "querySelectorAll('select')[0].value=...; dispatchEvent('change')"})` | 1 | `session.py:1485`（`execute_js`） |
| **after（P0，常规命中）** | `DOM.resolveNode({backendNodeId})` → `Runtime.callFunctionOn({objectId, functionDeclaration: _SELECT_OPTION_JS, arguments:[{value}], returnByValue:True})` | 2 | 新 `set_select_option` |
| **after（P0，框架回退→点击回退）** | 上述 + `Runtime.callFunctionOn({objectId, functionDeclaration: _SELECT_OPTION_CLICK_FALLBACK_JS, arguments:[{value:optionIndex}], returnByValue:True})` | 3 | 新 `set_select_option` |

CDP 调用次数：常规 **2 次**（vs 原 1 次 evaluate），换取**正确的范围绑定 + 框架兼容**（修 bug 的必要代价）。回退路径 3 次，仅在 `selectionReverted` 时触发。

---

## 涉及文件清单

| 文件 | 改动类型 | 说明 |
|---|---|---|
| `src/tree_walker/browser/session.py` | **改**（P0） | 新增 `_SELECT_OPTION_JS` / `_SELECT_OPTION_CLICK_FALLBACK_JS` 模块常量 + `set_select_option` 方法（紧邻 `fetch_select_options` `:1616-1652` 之后） |
| `src/tree_walker/tools/actions.py` | **改**（P0） | 重写 `_action_select_dropdown`（`:912-921`）；复用既有 `import json`（`:6`）与 `_describe_dropdown`（`:489`） |
| `tests/test_select_dropdown.py` | **新增**（P0） | 对齐 `test_dropdown_options.py` 模式的三层测试 |
| `src/tree_walker/tools/models.py` | 不改 | `SelectDropdownParams{index:int, value:str}` 不变（`:119-122`） |
| `src/tree_walker/agent/views.py` | 不改 | `ActionResult` 不引入 `include_extracted_content_only_once` |
| `docs/Tools技术细节/04_动作清单与CDP映射.md` | **改**（P0 文档同步） | §4.19 行号纠错 + 代码示例 + CDP 清单更新（见 [§文档同步](#文档同步)） |

---

## 测试用例清单

骨架（对齐 `tests/test_dropdown_options.py`，mock 边界为 `set_select_option`）：

```python
"""Tests for select_dropdown: scope bug fix, tag validation, native selection
success echo, option-not-found soft echo, error mapping, and output format.

Covers the action layer (Tools._action_select_dropdown), mirroring
tests/test_dropdown_options.py. The session layer (set_select_option, incl.
readback-verify + click fallback) is mocked — these tests assert the action
shell, not the CDP/JS internals.
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


def _make_browser(*, returns=None, raises=None) -> MagicMock:
	bs = MagicMock()
	bs.set_select_option = AsyncMock(
		side_effect=raises if raises else (returns if returns is not None else {})
	)
	bs.get_state = AsyncMock(return_value=_make_state({}))
	return bs


class TestSelectDropdownAction:
	@pytest.mark.asyncio
	async def test_native_select_success_echo(self):
		entry = _make_entry(backend_node_id=7, attributes={"aria-label": "Country"})
		state = _make_state({3: entry})
		browser = _make_browser(returns={
			"success": True,
			"message": "Selected option: Canada (value: ca)",
			"value": "ca",
		})
		result = await Tools().execute(
			"select_dropdown", {"index": 3, "value": "ca"}, browser, browser_state=state,
		)
		# G1: 用目标 select 的 backend_node_id + value 调用（范围绑定）
		browser.set_select_option.assert_awaited_once_with(7, "ca")
		assert result.error is None
		# G7: 成功 message 进 extracted_content
		assert "Canada" in result.extracted_content
		# G7: long_term_memory 带 json 编码 value + [SELECT] + index
		assert "Selected \"ca\"" in result.long_term_memory
		assert "[SELECT]" in result.long_term_memory
		assert "index 3" in result.long_term_memory

	@pytest.mark.asyncio
	async def test_non_select_element_returns_error_without_select(self):
		entry = _make_entry(tag="DIV", backend_node_id=7)
		state = _make_state({3: entry})
		browser = _make_browser()
		result = await Tools().execute(
			"select_dropdown", {"index": 3, "value": "x"}, browser, browser_state=state,
		)
		assert result.error is not None
		assert "[DIV]" in result.error
		assert "not a <select>" in result.error
		# G2: 早退，不碰 CDP
		browser.set_select_option.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_missing_index_returns_error_without_select(self):
		state = _make_state({})  # index 3 absent
		browser = _make_browser()
		result = await Tools().execute(
			"select_dropdown", {"index": 3, "value": "x"}, browser, browser_state=state,
		)
		assert result.error is not None
		browser.set_select_option.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_select_raises_maps_to_friendly_error(self):
		entry = _make_entry(backend_node_id=7)
		state = _make_state({3: entry})
		browser = _make_browser(raises=RuntimeError("CDP target detached"))
		result = await Tools().execute(
			"select_dropdown", {"index": 3, "value": "ca"}, browser, browser_state=state,
		)
		assert result.error is not None
		assert "Failed to select option" in result.error
		assert "CDP target detached" in result.error

	@pytest.mark.asyncio
	async def test_option_not_found_soft_echoes_available_options(self):
		entry = _make_entry(backend_node_id=7, attributes={"aria-label": "Country"})
		state = _make_state({3: entry})
		# session 层返回未命中 + 可用选项（也覆盖 selectionReverted 回退失败后的形态）
		browser = _make_browser(returns={
			"success": False,
			"error": "Option with text or value 'zz' not found in select element",
			"availableOptions": [
				{"text": "United States", "value": "us"},
				{"text": 'Canada "North"', "value": "ca"},
			],
		})
		result = await Tools().execute(
			"select_dropdown", {"index": 3, "value": "zz"}, browser, browser_state=state,
		)
		# G6: 不抛 error，软回显可用选项
		assert result.error is None
		# G6: extracted 列出选项（json 编码保留引号）+ 用法提示（D2：参数名 value）
		assert '"Canada \\"North\\""' in result.extracted_content
		assert result.extracted_content.rstrip().endswith(
			"Use the value in select_dropdown(index=3, value=...)"
		)
		# G6: long_term_memory 摘要「选不中」
		assert "Couldn't select" in result.long_term_memory

	@pytest.mark.asyncio
	async def test_output_format_uses_value_param_name(self):
		entry = _make_entry(backend_node_id=7)
		state = _make_state({3: entry})
		browser = _make_browser(returns={
			"success": False,
			"availableOptions": [{"text": "A", "value": "a"}],
		})
		result = await Tools().execute(
			"select_dropdown", {"index": 3, "value": "zz"}, browser, browser_state=state,
		)
		# D2: 提示语用本项目参数名 value（不是 browser-use 的 text），避免 ValidationError
		assert "select_dropdown(index=3, value=...)" in result.extracted_content
```

| 用例 | 覆盖缺口 | 断言要点 |
|---|---|---|
| `test_native_select_success_echo` | G1/G7 | `set_select_option(7, "ca")`（范围绑定 + 传 value）；extracted 含 message；long_term_memory 含 `Selected "ca"`+`[SELECT]`+`index 3` |
| `test_non_select_element_returns_error_without_select` | G2 | `[DIV] not a <select>`；`set_select_option` 未调用（早退） |
| `test_missing_index_returns_error_without_select` | G2/索引 | index 不在 selector_map → error，不碰 CDP |
| `test_select_raises_maps_to_friendly_error` | G8 | `RuntimeError` → `Failed to select option: CDP target detached` |
| `test_option_not_found_soft_echoes_available_options` | G6 | 未命中 → 不报 error，回显 json 编码选项 + 用法提示；long_term_memory「Couldn't select」 |
| `test_output_format_uses_value_param_name` | D2 | 提示语正好是 `select_dropdown(index=3, value=...)` |

---

## 已知限制（本次不处理）

1. **ARIA menu/listbox / custom class / combobox 不支持**（G12，P1）：P0 仅 native `<select>`；非 select 下拉走 tag 校验 error 分支，提示用 click 手动展开选择。理由见 [§D5](#d5懒加载重试--aria--custom--combobox--子树搜索--p1)。
2. **懒加载重试不支持**（G11，P1）：选项异步填充的 native select（全空 option）当前走「未命中」软回显；P1 加 `focus()`+`sleep 1s`+重试。
3. **500 字符截断**：选项很多时 `extracted_content` 被 `ActionResult.__str__` 截到 500，LLM 看不全；靠 `long_term_memory` 摘要 + 工具可重调缓解。不引入 `include_extracted_content_only_once`（理由见 [§D7](#d7成功回显用-_describe_dropdown不引入-include_extracted_content_only_once)）。
4. **点击回退也失败的极端站点**：session 层会返回带 `availableOptions` 的结构化错误，action 层软回显可用选项（LLM 可换 click 工具手动选）。无进一步兜底。
5. **match 策略为精确匹配**：不支持模糊匹配 / 部分匹配（与 browser-use 一致）。LLM 传错 value 时靠软回显的可用选项自纠。
6. **不支持多选 / 取消选中**（`<select multiple>`）：browser-use 的 `select_dropdown` 也仅单选。P1 若需多选再扩参数。

---

## P1 进阶蓝图

> P1 仅画蓝图，给出每项所需的 session 增量，不在本次落地。

### 1. 懒加载重试（G11）

- 在 `set_select_option` 内：若首次 `selection.success=false` 且 `availableOptions` 全为空（text 与 value 都空），`callFunctionOn('function(){ this.focus(); }')` + `await asyncio.sleep(1.0)` + 重跑 `_SELECT_OPTION_JS` 一次。对齐 browser-use `default_action_watchdog.py:3509-3547`。

### 2. ARIA menu / listbox（G12）

- 在 `_SELECT_OPTION_JS` 内追加分支：`role==='menu'||'listbox'` → `querySelectorAll('[role="menuitem"],[role="option"]')`，命中后 `aria-selected=true`+`classList.add('selected')`+`item.click()`+`MouseEvent`。对齐 `default_action_watchdog.py:3347-3393`。
- action 层 tag 校验放宽：允许 `role` 为上述值（需 `EnhancedDOMTreeNode.attributes` 含 role）。

### 3. custom class（Semantic UI 等）（G12）

- `_SELECT_OPTION_JS` 追加分支：`classList.contains('dropdown')||'ui'` → `querySelectorAll('.item,.option,[data-value]')`，命中后 toggle `selected`/`active` + 更新 `.text` + dispatch `click`+`change`。对齐 `:3395-3449`。

### 4. combobox（aria-controls 独立 listbox）（G12）

- 新 session 方法 `set_combobox_option(backend_node_id, value)`：`role==='combobox' && aria-controls` 时展开（focus/focusin/click/mousedown）→ 读 `getElementById(aria-controls)` 的 `[role=option]` → 命中后 click option → **读后 blur+Escape 收起**保证无副作用。对齐 `_handle_aria_combobox_options:3038-3239`（getter 版，setter 版 browser-use 当前缺失，是已知不一致）。

### 5. 子树搜索（G12）

- 目标元素自身非任何已知类型时，`searchChildrenForSelection(startElement, 4)` 递归向下搜 4 层。对齐 `:3455-3488`。

### 6. click 误点 select 降级（强相关，dropdown_options G9）

- 对齐 browser-use `service.py:711-723`：`_action_click` 误点 `<select>` 时，自动降级调 `_action_dropdown_options` 把选项读出来给 LLM（而非直接报错）。

---

## 文档同步

`docs/Tools技术细节/04_动作清单与CDP映射.md` §4.19 需更新（行号纠错 + 代码示例 + CDP 清单）：

1. **行号纠错**：§4.19 标的 `[actions.py:348-357]` **错误**（该范围实为 `_action_click` SELECT 分支）。真值 **`actions.py:912-921`**（P0 后变为新实现的行号）。
2. **代码示例更新**：§4.19 的 `_action_select_dropdown` 代码块替换为本方案 [§改动文件 2](#2-srctree_walkertoolsactionspy--重写-_action_select_dropdownp0) 的新实现；删除「JS 实际操作页面上第一个 select……已知限制」注释。
3. **CDP 调用清单更新**：

   | CDP 命令 | 主要参数 | 行号 |
   |---|---|---|
   | ~~`Runtime.evaluate`~~ → `DOM.resolveNode` | `{backendNodeId}` | `session.py` 新 `set_select_option` |
   | `Runtime.callFunctionOn` | `{objectId, functionDeclaration: _SELECT_OPTION_JS, arguments:[{value}], returnByValue:True}` | 同上 |
   | `Runtime.callFunctionOn`（回退） | `{objectId, functionDeclaration: _SELECT_OPTION_CLICK_FALLBACK_JS, arguments:[{value:optionIndex}], returnByValue:True}`（仅 `selectionReverted` 时） | 同上 |

4. **注意事项更新**：删除「JS 实际查询的是页面上第一个 select……已知限制」「必须手动 dispatch change」，改为「复用 `set_select_option` 精确绑定到目标 select；移植 browser-use native 选择链（focus→三方式设值→input/change/blur→读回验证→框架回退时点击回退）；选项未命中软回显可用选项」。

---

## 验证步骤

### 自动化测试

```powershell
# 仅跑新增测试
uv run python -m pytest tests/test_select_dropdown.py -v

# 连同读写闭环回归（dropdown_options + click SELECT 分支共享 fetch_select_options，确保未回归）
uv run python -m pytest tests/test_select_dropdown.py tests/test_dropdown_options.py tests/test_click.py -x -v

# 全量回归 + 覆盖率（CLAUDE.md 目标 >85%）
uv run python -m pytest tests/ -x -v
uv run python -m pytest tests/ --cov=tree_walker.tools --cov=tree_walker.browser --cov-report=term-missing
```

预期：6 个用例全绿；`test_dropdown_options.py`/`test_click.py` 不回归（session 层 `fetch_select_options` 未改）。

### 手测步骤

1. **native select 正常选**：
   - 打开含 `<select>` 的页面（如 `https://www.w3schools.com/tags/tryit.asp?filename=tryhtml_select`）。
   - 先 `dropdown_options(index=N)` 读出选项，再 `select_dropdown(index=N, value=<某 option 的 value>)`。
   - 预期：extracted=`Selected option: ... (value: ...)`；long_term_memory=`Selected "<value>" in [SELECT] ... at index N`；页面选中项实际变化。
2. **多 select 页面范围正确**（核心 bug 验证）：
   - 打开含**两个以上 select** 的页面。
   - 对第二个 select 调 `select_dropdown(index=<第二个 select 的 index>, value=...)`。
   - 预期：改的是**第二个 select**（修复前会改第一个）。
3. **传 text 也能命中**（G10 匹配策略）：
   - `select_dropdown(index=N, value=<某 option 的可见文本>)`。
   - 预期：同样选中（text OR value 大小写不敏感匹配）。
4. **选项未命中软回显**：
   - `select_dropdown(index=N, value="不存在的值")`。
   - 预期：不报 error；extracted 列出可用选项 + `Use the value in select_dropdown(index=N, value=...)`；LLM 可据此重试。
5. **非 select 元素报错**：
   - 对一个 `<div>` 的 index 调 `select_dropdown`。
   - 预期：`error="Index N is a [DIV] element, not a <select>..."`。
6. **框架回退 → 点击回退**（可选，需受框架保护的 select 站点）：
   - 在拦截程序化 `value` 赋值的 select 上调 `select_dropdown`。
   - 预期：session 层读回验证发现回退 → 跑点击回退 → 选中成功；若无此类站点可跳过，由单测覆盖该分支逻辑。
7. **CDP 异常软降级**：
   - select 元素 index 失效场景（导航后旧 index）调 `select_dropdown`。
   - 预期：`error="Failed to select option: ..."`，不裸抛中断 agent loop。
