# input_text 工具完善方案：成功回显 + 聚焦失败纠错 + 值校验反馈 + 日期时间直写 + autocomplete 延迟

> 参照 browser-use（`browser_use/tools/service.py:749-833` 的 `input` 动作 + `browser_use/browser/watchdogs/default_action_watchdog.py:451-511` 的 `on_TypeTextEvent` / `:1728-2052` 的 `_input_text_element_node_impl` / `:1589-1639` 的 `_requires_direct_value_assignment` / `:404-417` 的 `_is_autocomplete_field`）完善本项目的 `input_text` 动作。
> 相关文档：`docs/Tools技术细节/04_动作清单与CDP映射.md` 的 4.10 input_text 节（本项目现状，注意其"主要逻辑"行号 `actions.py:219-227` 已过期，实际为 `actions.py:363-371`）、`browser-use/docs/Tools技术细节/05-动作详解-浏览器交互.md` 的 5. input 节（参考标杆）。

## 背景（为什么改）

当前 `input_text` 是本项目最薄的 action 之一（`actions.py:363-371`，共 6 行有效逻辑），几乎所有"真正"工作都在 session 层 `type_text`。对照刚被完善的 `click`/`navigate`/`go_back`（已统一"回显 + 错误映射 + bool 信号上浮"惯例）与 browser-use 的 `input`，存在以下缺口：

| # | 缺口 | browser-use | TreeWalker 现状 | 影响 | 风险 |
|---|------|-------------|-----------------|------|------|
| G1 | **成功无回显** | 回显 `Typed '{text}'`（敏感时 `Typed <key>`） | 返回空 `ActionResult()` | LLM 无法从 result 确认输入了什么、输入到哪个字段，与 navigate/go_back/click 回显惯例脱节 | 无 |
| G2 | **`click_element` bool 被丢弃** | 聚焦失败走兜底/报错 | `await browser.click_element(...)` 不接收返回值 | 元素 detached/hidden/跨源 iframe 时聚焦失败被静默吞掉，LLM 收到"成功"但实际没聚焦，后续输入落到错误元素 | 无 |
| G3 | **CDP 调用无 try/except** | 全程包裹 | 无 | CDP 异常（连接断、target 消失）打到 `Tools.execute` 通用兜底（`actions.py:171-173`）成裸 `str(e)` | 无 |
| G4 | **无值校验反馈** | 读回字段值，不匹配时追加 `⚠️ Note: the field's actual value '...' differs...` | `type_text` 内部有读回，但**只用于拼接自愈，结果不上浮**； maxlength 截断 / 框架回写 / 输入掩码 / 站点拒收 等不匹配 LLM 全程不知情 | LLM 误以为输入成功，继续下一步 | 低 |
| G5 | **不处理 date/time/特殊输入** | 检测 7 种 type 与 jQuery/Bootstrap 日期选择器，走"原生 setter 直写" | 逐字符 `_type_char` | `<input type="date">` 等逐字符输入只生效部分甚至被拒收（`2026/` 之类残值），预订/表单类站点高频踩雷 | 中 |
| G6 | **不处理 autocomplete/combobox** | 检测后给 LLM 提示，并对 JS 驱动型加 0.4s 等待 | 无 | 输入后 JS 下拉（Select2/jQuery UI/MUI Autocomplete）未填充完，下一步点建议项时选项还不存在 | 低 |
| G7 | **无页面级兜底** | 元素级输入失败 → 点击聚焦 → 向"当前焦点元素"重输 | 无 | 极少数"节点解析失败但元素仍在焦点"场景失败率略高 | 中（延后） |
| G8 | **元素找不到是硬错误** | 返回 `extracted_content` 软提示"页面可能已变化，请刷新状态" | 硬 `error="Element {N} not found in DOM state"` | LLM 盲目重试而非重新观察 | 无（次要） |

**好消息——session 层大部分机制已就绪（本次复用，不重写）：**

- `type_text`（`session.py:781-823`）：逐字符输入 + 框架事件 + **末尾拼接自愈**（`clear=True` 时读回，发现 OLD+NEW 拼接就用 `_force_set_value` 强写）。⇒ browser-use 的 Step 6 auto-retry **已实现**。
- `_read_active_text`（`session.py:825-844`）：读 `document.activeElement.value`（input/textarea）或 `textContent`（contenteditable），异常吞掉返回 `""`。
- `_force_set_value`（`session.py:846-894`）：React/Vue 原生 setter trick（`HTMLInputElement.prototype.value` / `HTMLTextAreaElement.prototype.value` 描述符）+ `input`/`change` 事件。**正是 browser-use 给 date 输入用的 `_set_value_directly` 等价物**。
- `_clear_text_field`（`session.py:896-1020`）：三层清空（JS `select()+value=''` / 三连击+Delete / Ctrl+A+Backspace），返回 `bool`。
- `click_element`（`session.py:651-703`）：`scrollIntoView` + 三层坐标 + 遮挡检查 + JS 回退，返回 `bool`。
- `_type_char`（`session.py:1022-1061`）：keyDown/char/keyUp，CJK 仅发 char。

预期结果：input_text 成功时回显 `Typed '...' into [TAG] ...`、聚焦失败/CDP 异常明确报错、输入值与预期不符时给出 `⚠️ Note` 提示、date/time 等特殊输入走直写保证生效、combobox 输入后等待 JS 下拉并提示 LLM"从下拉选"；全部复用已有 session 工具，分层与 click/navigate 一致。

---

## 已确认的决策（方案采用）

- **范围 = 全面分档**：P0（action 层回显 + 聚焦纠错 + 值校验）+ P1（日期时间直写 + autocomplete 延迟）本次纳入；P2（页面级兜底 / 敏感数据掩码）延后。
- **值校验放 action 层**：`type_text` 签名保持 `-> None` 不变，action 层在输入后自行调 `_read_active_text()` 读回比对（D1）。
- **date/combobox 判定分开放**：`_requires_direct_value_assignment` 放 `session.py`（紧贴它门控的 `_force_set_value`/`_clear_text_field`）；`_is_autocomplete_field` 放 `actions.py` 作 `@staticmethod`（只影响 echo 文案与延迟，是 action 层关切）（D3）。
- **分支在 action 层做**：`type_text` 只收字符串、不感知 entry；entry 感知的分支由持有 entry 的 `_action_input_text` 决策（D4）。
- **0.4s 延迟仅在 JS 驱动型生效**：`role=combobox` 或非 `none` 的 `aria-autocomplete` 才等待；原生 `<datalist>`（`list` 属性）与松散 `aria-haspopup` 即时渲染不等待（D5，对齐 browser-use）。
- **不改 `models.py`**：`InputTextParams`（`index/text/clear`）保持不变；date 直写、延迟都是内部鲁棒性细节，不暴露给 LLM（LLM 不该决定是否走直写）。

---

## 改动文件（共 3 个源文件 + 2 个测试文件，另同步 1 个文档）

### 1. `src/tree_walker/tools/models.py` —— 不改

`InputTextParams`（`models.py:20-24`）与 `ACTION_DEFINITIONS["input_text"]`（`models.py:153-157`）维持 `index: int` / `text: str` / `clear: bool=True` / `terminates_sequence=False`。不引入 `force` / `direct_set` / `wait` 等开关——直写、延迟、值校验都是 session/action 内部细节。

---

### 2. `src/tree_walker/browser/session.py`（新增 1 个判定函数；其余复用）

**(a) 新增 `_requires_direct_value_assignment(entry)`（紧随 `_force_set_value`，`:894` 之后）**

纯 Python 谓词，照搬 browser-use `default_action_watchdog.py:1589-1639` 规则——这些输入**拒收逐字符 key 事件**，必须直写值：

```python
# 模块级常量（放在文件顶部已有的 _KEY_VK_MAP 附近）
_DIRECT_VALUE_INPUT_TYPES = frozenset(
	{"date", "time", "datetime-local", "month", "week", "color", "range"}
)
_DATEPICKER_CLASS_MARKERS = (
	"datepicker", "daterangepicker", "datetimepicker", "bootstrap-datepicker",
)
_DATEPICKER_DATA_ATTRS = ("data-datepicker", "data-date-format", "data-provide")


def _requires_direct_value_assignment(entry) -> bool:
	"""True if the element won't accept per-character key events and must be
	set via a direct value assignment (native setter).

	Mirrors browser-use default_action_watchdog.py:1589-1639:
	  - <input type> in {date, time, datetime-local, month, week, color, range}
	    (HTML5 compound inputs that require ISO/hex formatted values)
	  - <input type='text'|''> whose class contains a known datepicker marker
	  - <input type='text'|''> carrying a known datepicker data-* attribute

	Used by Tools._action_input_text to route date/special inputs to
	BrowserSession._force_set_value instead of per-char typing.
	"""
	tag = (getattr(entry, "tag_name", "") or "").lower()
	if tag != "input":
		return False
	attrs = getattr(entry, "attributes", {}) or {}
	itype = (attrs.get("type", "") or "").lower()
	if itype in _DIRECT_VALUE_INPUT_TYPES:
		return True
	if itype in ("", "text"):
		cls = (attrs.get("class", "") or "").lower()
		if any(m in cls for m in _DATEPICKER_CLASS_MARKERS):
			return True
		if any(attrs.get(a) for a in _DATEPICKER_DATA_ATTRS):
			return True
	return False
```

> **放 session 层而非 action 层的理由（D3）**：该谓词紧耦合它门控的 `_force_set_value` / `_clear_text_field`（都在本文件），未来重构 setter 可同步更新；且需要的 `tag_name`/`attributes` 字段语义稳定。
> `_force_set_value` 作用于 `document.activeElement`，故 date 直写前仍需 `click_element` 聚焦（action 层保证）。

其余 session 函数（`type_text` / `_read_active_text` / `_force_set_value` / `_clear_text_field` / `_type_char` / `_trigger_framework_events` / `click_element`）**全部原样复用**，不改。

---

### 3. `src/tree_walker/tools/actions.py`（action 层做策略）

**(a) 新增 `_is_autocomplete_field` 静态 helper（紧随 `_describe_click`，`:361` 之后）**

照搬 browser-use `service.py:404-417` 规则，返回 `(is_combo, needs_js_wait)`：前者决定是否给 LLM 加提示，后者决定是否睡 0.4s（仅 JS 驱动型）。

```python
@staticmethod
def _is_autocomplete_field(entry: Any) -> tuple[bool, bool]:
	"""Detect combobox/autocomplete fields. Returns (is_combo, needs_js_wait).

	Mirrors browser-use tools/service.py:404-417. ``is_combo`` is True for any
	combobox-shaped field (drives the LLM hint); ``needs_js_wait`` is True only
	for the JS-driven subset (role=combobox or non-none aria-autocomplete) whose
	dropdowns populate asynchronously and need a ~0.4s settle before the next
	click — native <datalist> (list attr) and loose aria-haspopup render
	synchronously and are excluded from the wait.
	"""
	attrs = getattr(entry, "attributes", {}) or {}
	if attrs.get("role") == "combobox":
		return True, True
	aria_ac = attrs.get("aria-autocomplete", "")
	if aria_ac and aria_ac != "none":
		return True, True
	if attrs.get("list"):
		return True, False  # native <datalist>: instant, no wait
	haspopup = attrs.get("aria-haspopup", "")
	if haspopup and haspopup != "false" and (attrs.get("aria-controls") or attrs.get("aria-owns")):
		return True, False
	return False, False
```

**(b) 新增 `_describe_input` 静态 helper（紧随 `_is_autocomplete_field`）**

镜像 `_describe_click`（`:340-361`）的属性优先级链与 60 字截断，生成 `Typed '...' into [TAG] {label} at index N`：

```python
@staticmethod
def _describe_input(entry: Any, index: int, text: str) -> str:
	"""Build a human-readable input echo, mirroring _describe_click /
	navigate / go_back style.

	Prefers an identifying attribute the LLM can also see in the DOM tree
	(aria-label/placeholder/title), then node_value, then just the tag.
	Both the label and the typed text are bounded to ~60 chars so the echo
	fits the LLM context. Skips 'value'/'alt' (unlike _describe_click) since
	for an input the typed text itself is what matters.
	"""
	shown = text if len(text) <= 60 else text[:60] + "..."
	tag = entry.tag_name.upper()
	attrs = getattr(entry, "attributes", {}) or {}
	for key in ("aria-label", "placeholder", "title"):
		v = attrs.get(key)
		if v:
			v = v.strip()
			if len(v) > 60:
				v = v[:60] + "..."
			return f"Typed {shown!r} into [{tag}] {v!r} at index {index}"
	node_value = (getattr(entry, "node_value", "") or "").strip()
	if node_value:
		if len(node_value) > 60:
			node_value = node_value[:60] + "..."
		return f"Typed {shown!r} into [{tag}] {node_value!r} at index {index}"
	return f"Typed {shown!r} into [{tag}] at index {index}"
```

**(c) 重写 `_action_input_text`（`:363-371`）**

整合 P0（聚焦纠错 + 回显 + 值校验）与 P1（date 直写 + autocomplete 延迟）：

```python
async def _action_input_text(self, params: dict, browser: BrowserSession) -> ActionResult:
	# 1. 元素查找（保持原逻辑）
	entry, error = await self._get_element_by_index(params["index"], browser)
	if error:
		return error
	backend_id = entry.backend_node_id
	text = params["text"]
	clear = params.get("clear", True)

	# 2. 聚焦：highlight -> click_element，映射 bool（对齐 _action_click）
	try:
		await browser.highlight_element(backend_id)
		clicked = await browser.click_element(backend_id)
	except Exception as e:
		return ActionResult(error=f"Input focus failed: {e}")
	if not clicked:
		return ActionResult(
			error=(
				f"Could not focus element {params['index']} for input "
				f"(no coordinates and JS click fallback failed; "
				f"the element may be detached, hidden, or in a cross-origin iframe)"
			),
		)
	await asyncio.sleep(0.1)  # 保留原有聚焦等待

	# 3. 输入：date/time 等特殊输入走直写，否则逐字符（对齐 browser-use
	#    _requires_direct_value_assignment / _set_value_directly）
	try:
		if _requires_direct_value_assignment(entry):
			if clear:
				await browser._clear_text_field()
			await browser._force_set_value(text)
		else:
			await browser.type_text(text, clear=clear)
	except Exception as e:
		return ActionResult(error=f"Failed to type text into element {params['index']}: {e}")

	# 4. autocomplete/combobox：JS 驱动型睡 0.4s 等下拉（对齐 browser-use service.py:812-819）
	is_combo, needs_js_wait = self._is_autocomplete_field(entry)
	if needs_js_wait:
		await asyncio.sleep(0.4)

	# 5. 值校验：读回 activeElement 值，不匹配则追加 ⚠️ Note（对齐 browser-use
	#    service.py:804-810）。_read_active_text 异常吞掉返回 ""，安全。
	memory = self._describe_input(entry, params["index"], text)
	actual = await browser._read_active_text()
	if actual and actual != text:
		memory += (
			f"  ⚠️ Note: the field's actual value {actual!r} differs from "
			f"the intended {text!r}. The site may have reformatted, truncated, "
			f"or rejected the input — re-observe before continuing."
		)
	if is_combo:
		memory += (
			"  💡 autocomplete field — select from the JS-populated dropdown "
			"if applicable instead of typing the full value."
		)
	logger.info(memory)
	return ActionResult(extracted_content=memory, long_term_memory=memory)
```

要点：
- **bool 信号映射（G2）**：`clicked=False` → 明确 `error`，不再静默成功（对齐 `_action_click:324-332`）。
- **异常包裹（G3）**：聚焦与输入分别 try/except，映射为对 LLM 友好的 `error`，不让裸堆栈上浮。
- **成功回显（G1）**：`_describe_input` 生成 `Typed '...' into [TAG] ...`，写入 `extracted_content` + `long_term_memory`（**不**设 `success=True`——`ActionResult` 校验器 `views.py:18-25` 对非 done 动作拒绝 `success=True`）。
- **值校验（G4）**：`actual and actual != text` 才追加 `⚠️ Note`（读回为空说明读失败，静默不打扰）。
- **date 直写（G5）**：特殊 type 走 `_clear_text_field` + `_force_set_value`，跳过逐字符——复用现成 setter，零新 JS。
- **autocomplete（G6）**：`needs_js_wait` 才睡 0.4s；`is_combo` 才追加 💡 提示。
- **跨模块下划线访问**：`browser._read_active_text` / `browser._clear_text_field` / `browser._force_set_value` 虽以 `_` 开头，但 action 层复用它们是已知的、可接受的轻耦合（`type_text` 内部本就调它们）；若出现第三个 action 调用方再统一改名去下划线（D6）。

---

### 4. `tests/test_input_text.py`（新建，行为级测试，闭合覆盖缺口）

当前 input_text **无任何行为级测试**（`tests/test_input_text_clear.py` / `tests/test_input_text_framework.py` 都是 session 层）。参照 `tests/test_click.py` 的 helper 模式（`_make_entry` / `_make_state` / `_make_browser`，端到端 `Tools().execute("input_text", {...}, browser, browser_state=state)`，mock 边界为 `click_element` / `type_text` / `_read_active_text` / `_force_set_value` / `_clear_text_field`，**不碰 CDP 原语**）。

**用例清单：**

| 组 | 用例 | 断言要点 |
|---|------|---------|
| 元素查找 | index 在 selector_map | 调 `highlight+click_element`；`error is None`；回显 |
| 元素查找 | index 不在 selector_map | 返回 `Element {N} not found...`；不调 click/type |
| 回显 G1 | 普通输入成功 | `extracted_content` 以 `Typed '...' into [INPUT]` 开头；== `long_term_memory`；`success is None`；`is_done is False` |
| 回显 G1 | 有 aria-label/placeholder | 回显含该属性文本 |
| 回显 G1 | text 超 60 字 | 截断 + `...` |
| 回显 G1 | bare entry（无可识别属性） | `Typed '...' into [TAG] at index N` |
| 聚焦纠错 G2 | `click_element` 返回 `False` | `error` 含 `Could not focus element`；**不**调 `type_text` |
| 异常映射 G3 | `click_element` 抛异常 | `error` 以 `Input focus failed:` 开头 |
| 异常映射 G3 | `type_text` 抛异常 | `error` 以 `Failed to type text` 开头 |
| 值校验 G4 | `_read_active_text` 返回不同串 | 回显含 `⚠️ Note` 且两端引号都在 |
| 值校验 G4 | `_read_active_text` 返回 `""` | 无 `⚠️ Note`（读失败静默） |
| 值校验 G4 | `_read_active_text` 返回等于 text | 无 `⚠️ Note` |
| date 直写 G5 | `type="date"` entry | 调 `_force_set_value`，**不**调 `type_text`（断言调用计数） |
| date 直写 G5 | `type="date"` + `clear=True` | `_clear_text_field` 在 `_force_set_value` 前被调 |
| date 直写 G5 | `type="text"` 普通输入 | **不**调 `_force_set_value`；调 `type_text` |
| autocomplete G6 | `role="combobox"` | 回显含 `💡 autocomplete field`；`asyncio.sleep` 被以 0.4 调用一次 |
| autocomplete G6 | `list="cities"`（原生 datalist） | 回显含 `💡 autocomplete field`；**不**出现 0.4 延迟（0.1 聚焦延迟除外，按时长区分） |
| clear 默认值 | 省略 `clear` | 默认 `True`（回归保护） |

代表性骨架（仿 `test_click.py`）：

```python
def _make_entry(*, tag="INPUT", backend_node_id=42, attributes=None, node_value=""):
	return EnhancedDOMTreeNode(
		node_id=backend_node_id, backend_node_id=backend_node_id,
		node_type=NodeType.ELEMENT_NODE, node_name=tag.upper(),
		node_value=node_value, attributes=attributes or {},
	)

def _make_browser(*, click_return=True, click_side_effect=None,
                  read_return="", force_set=None, clear_return=True):
	bs = MagicMock()
	bs.current_session_id = "sid"
	bs.current_target_id = "tid"
	if click_side_effect is not None:
		bs.click_element = AsyncMock(side_effect=click_side_effect)
	else:
		bs.click_element = AsyncMock(return_value=click_return)
	bs.highlight_element = AsyncMock()
	bs.type_text = AsyncMock()
	bs._read_active_text = AsyncMock(return_value=read_return)
	bs._force_set_value = AsyncMock(side_effect=force_set or (lambda *a, **k: None))
	bs._clear_text_field = AsyncMock(return_value=clear_return)
	bs.get_state = AsyncMock(return_value=_make_state({}))
	return bs

class TestInputTextEcho:
	@pytest.mark.asyncio
	async def test_echoes_typed_text_and_label(self):
		entry = _make_entry(attributes={"placeholder": "Email"})
		state = _make_state({5: entry})
		browser = _make_browser(read_return="a@b.com")
		result = await Tools().execute("input_text", {"index": 5, "text": "a@b.com"}, browser, browser_state=state)
		assert result.error is None
		assert result.extracted_content == "Typed 'a@b.com' into [INPUT] 'Email' at index 5"
		assert result.extracted_content == result.long_term_memory
		assert result.success is None and result.is_done is False

class TestInputTextFocusFail:
	@pytest.mark.asyncio
	async def test_click_false_blocks_typing(self):
		entry = _make_entry()
		state = _make_state({1: entry})
		browser = _make_browser(click_return=False)
		result = await Tools().execute("input_text", {"index": 1, "text": "x"}, browser, browser_state=state)
		assert result.error is not None and "Could not focus" in result.error
		browser.type_text.assert_not_awaited()

class TestInputTextDateDirectSet:
	@pytest.mark.asyncio
	async def test_date_uses_force_set_not_type_text(self):
		entry = _make_entry(attributes={"type": "date"})
		state = _make_state({2: entry})
		browser = _make_browser(read_return="2026-06-18")
		await Tools().execute("input_text", {"index": 2, "text": "2026-06-18"}, browser, browser_state=state)
		browser._force_set_value.assert_awaited_once_with("2026-06-18")
		browser.type_text.assert_not_awaited()

class TestInputTextValueMismatch:
	@pytest.mark.asyncio
	async def test_mismatch_appends_note(self):
		entry = _make_entry()
		state = _make_state({3: entry})
		browser = _make_browser(read_return="XXX")  # 与 text 不符
		result = await Tools().execute("input_text", {"index": 3, "text": "hello"}, browser, browser_state=state)
		assert "⚠️ Note" in result.extracted_content and "XXX" in result.extracted_content
```

---

### 5. session 层谓词测试（扩展 `tests/test_input_text_framework.py`）

| 用例 | 断言 |
|------|------|
| 7 种特殊 type 各一 | `_requires_direct_value_assignment` 返回 True |
| 4 种 datepicker class 子串各一 | True |
| 3 种 data 属性各一 | True |
| 普通 text/email/password/search/url/number/checkbox、无 type、`<textarea>` | False |
| datepicker class 在 `<div>` 上（tag 守卫） | False |

> `_force_set_value` 本身已有测试（`test_input_text_clear.py:76-106`），不改不重测。

---

### 6. 附带文档同步（建议本次一并做）

更新 `docs/Tools技术细节/04_动作清单与CDP映射.md` 的 4.10 input_text 节：

- **行号纠错**：`actions.py:219-227` → `actions.py:363-371`。
- **主要逻辑代码块**：替换为重写后的 `_action_input_text`（聚焦 bool 映射、try/except、`_requires_direct_value_assignment` 分支、`_is_autocomplete_field` 延迟、`_describe_input` 回显 + `⚠️ Note` 值校验）。
- **CDP 清单**：补充 `Runtime.evaluate`（`_read_active_text` 值读回）一项；date 直写路径标注复用 `_force_set_value`。
- **注意事项**：补"成功回显 `Typed '...' into [TAG] ...`"、"聚焦失败/CDP 异常明确报错"、"值不符追加 `⚠️ Note`"、"date/time 走直写"、"combobox 睡 0.4s"。

---

## 技术决策说明（要点）

- **值校验放 action 层、不改 `type_text` 签名（D1）**：`type_text` 是 session 公共 API（未来或有其他调用方），强令返回 `(ok, actual)` 会扩散改动；action 层调 `_read_active_text` 已足够（它异常吞掉返回 `""`，安全）。代价：每次输入多一次 `Runtime.evaluate`，相比逐字符成本可忽略。
- **`⚠️ Note` 追加到同一 `extracted_content`（D2）**：对齐 browser-use 单消息风格，不改 `ActionResult` 形状；`⚠️` 标记可被 LLM 解析。
- **date 谓词放 session、combobox 谓词放 action（D3）**：date 谓词紧耦合 setter/clear；combobox 谓词只影响 echo 与延迟（action 关切）。
- **分支在 action 层（D4）**：`type_text` 只收字符串；entry 感知分支由 `_action_input_text` 决策。
- **0.4s 延迟仅 JS 驱动型（D5）**：原生 datalist/松散 haspopup 即时渲染，无条件等待会无谓拖慢。
- **复用 `_force_set_value` 作 date 直写（D7）**：它已实现 React 原生 setter + input/change 事件，正是 browser-use `_set_value_directly` 等价物，零新 JS。

## 已知限制（本次不处理，留作未来）

- **G7 页面级兜底**：browser-use 元素级输入失败后点击聚焦再向"当前焦点元素"重输。medium 风险（可能引入焦点竞争），低观测频次，待遥测确认后再做。
- **敏感数据掩码**：browser-use 用 `<secret>key</secret>` 标签 + registry 替换 + 日志 `Typed <password>`。本项目 `Tools` 注入机制无此敏感数据管线，本次跳过；如未来 tool 参数携带 `sensitive: bool` 再加。
- **color/range 值格式校验**（`#rrggbb` / min/max/step）：browser-use 也不校验，LLM 自行保证格式。
- **`_read_active_text` / `_force_set_value` 改名去下划线**：出现第三个 action 调用方时再做。
- **`type_text` 签名返回值**：明确不改（D1）。
- **iframe 会话路由**：与 click 同源限制，本次 `_force_set_value` 作用于 `document.activeElement`，跨源 iframe 场景能力与现有一致。

---

## 验证步骤

1. **本方案文档自身核对**：交叉核对所有 `file:line` 引用（browser-use 的 service.py / default_action_watchdog.py、本项目的 actions.py / session.py / models.py / views.py）与现有代码一致；确认 session 层被"复用"的函数（`type_text`/`_read_active_text`/`_force_set_value`/`_clear_text_field`/`click_element`）确实存在且行为如述。
2. **（实现阶段，非本次）跑测试**：
   ```powershell
   uv run python -m pytest tests/test_input_text.py tests/test_input_text_framework.py tests/test_input_text_clear.py -x -v
   uv run python -m pytest tests/ -x -v
   ```
3. **（实现阶段）手动验证 date 输入**：在真实浏览器开一个含 `<input type="date">` 的页面，`input_text` 后确认值完整写入（旧实现残缺）。
