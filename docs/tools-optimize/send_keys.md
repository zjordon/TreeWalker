# send_keys 工具完善方案

> 参照 browser-use 的 `send_keys` 实现，完善本项目 `send_keys` 工具。
>
> 本文为**提案文档**（不改源码），给出差距分析、分级改进方案与可落地的代码片段，供后续实施直接参照。范围：**核心改进（send_keys 工具本身：成功回显 + 异常处理 + 别名归一化 + 完整特殊键映射 + 文本逐字符分支 + 参数校验 + 测试）+ P1 进阶蓝图（逐修饰键 keyDown/keyUp / 字面 `+` 文本支持）**。

---

## 1. 背景与目标

参照对象：

- browser-use：`browser_use/tools/service.py:1437-1454` 的 `send_keys` 门面（极薄）+ `browser_use/browser/watchdogs/default_action_watchdog.py:2439-2642` 的 `on_SendKeysEvent`（真正的按键解析与 CDP 派发）+ `browser_use/actor/utils.py:8-176` 的 `get_key_info`/`key_map`。
- 本项目：`src/tree_walker/tools/actions.py:640-642` 的 `_action_send_keys`、`src/tree_walker/browser/session.py:1164-1214` 的 `send_keys`、`src/tree_walker/tools/models.py:59-62` 的 `SendKeysParams`。
- 对齐基准：`scroll`（`docs/tools-optimize/scroll.md`）/ `close_tab`（`docs/tools-optimize/close.md`）已建立的"动作类工具新规范"——成功回显、Pydantic 校验、try/except 软降级、补测试。

**既有优势（关键前提）**：TreeWalker 的 session 层已有 `input_text` 逐字符输入链路——`src/tree_walker/browser/session.py:74-122` 的 `_get_char_modifiers_and_vk` / `_get_key_code_for_char`，以及 `session.py` 的 `_type_char`（逐字符 keyDown→char→keyUp，含 CJK 走 `Input.insertText` 的处理）。send_keys 的"文本逐字符分支"可直接复用这些，无需重建。

目标：

1. **补成功回显**：发送后回显 `Sent keys '...'`，对齐 navigate/click/scroll/close_tab。
2. **补异常处理**：send_keys **非幂等**（按键可能提交表单/触发导航），CDP 失败即报 error（对齐 scroll 范式，区别于 close_tab 软成功）。
3. **别名归一化**：支持 ctrl/control、return、esc、cmd/command、up/down/left/right、pageup/pagedown、home/end、del、space、F1-F12 等大小写不敏感别名。
4. **完整特殊键映射**：Arrow/Home/End/PageUp/PageDown/Delete/F1-F12 补全 `code` + `windowsVirtualKeyCode`（当前只有 enter/tab/escape/backspace，其余 vk=0，行为可疑）。
5. **文本逐字符分支**：`hello` 这类纯文本应逐字符输入（复用 `_type_char`），当前实现完全不支持（多字符文本走 fallback 只发一次无意义事件）。
6. **修死代码**：`keys.replace("+","+")` 是 no-op。
7. **参数校验**：`keys` 加 `min_length=1`，拒绝空串。
8. **补测试**：新增 `tests/test_send_keys.py`，对齐 `tests/test_scroll.py` 三层结构。

---

## 2. 现状对比

| 维度 | browser-use `send_keys` | TreeWalker `send_keys`（当前） | 差距 |
|---|---|---|---|
| 成功回显 | `extracted_content="Sent keys: {keys}"` | 裸 `ActionResult()` → LLM 只见 `"OK"` | **无回显** |
| 异常处理 | 门面层 try/except 全捕获 → `ActionResult(error="Failed to send keys: ...")` | 无 try/except，CDP 异常冒泡到 `Tools.execute` 通用兜底（裸 `str(e)`，无 send_keys 专属文案） | **缺专属异常处理** |
| 别名归一化 | 完整 `key_aliases`（ctrl/control、return、esc、cmd/command、up→ArrowUp、pageup/pagedown、home/end、del、space→' '、F1-F12，大小写不敏感） | 无别名表；仅 `key_map` 4 个命名键 + `modifier_map` 4 个修饰键 | **缺别名** |
| 特殊键覆盖 | `special_keys` 集合 + `get_key_info` 全量 VK（导航键/修饰键/F1-F24/小键盘/OEM 标点/媒体键） | `_KEY_VK_MAP` 仅 enter/tab/escape/backspace/arrow；Arrow/Home/End/PageUp/PageDown/Delete/F1-F12 走 fallback（vk=0） | **VK 映射残缺** |
| 三条路由分支 | 组合键（`+`）/ 单特殊键 / 文本逐字符（每字符 keyDown+char+keyUp，10ms 节流） | **只有组合键 + 单键两路**，无文本分支；多字符文本走 fallback 只发一次 | **缺文本分支** |
| 文本字符插入机制 | 每字符补 `type:'char', text:char`（仅 keyDown 不插入字符） | 单键 Enter/Tab 补 char（`_KEY_CHAR_TEXT`）；无文本分支 | 文本分支需复用 `_type_char` |
| 修饰键做法 | 逐个 keyDown 各修饰键 → 主键 down/up → 逆序 keyUp 各修饰键 | 只在主键事件里带 `modifiers` 位掩码（不单独 keyDown 修饰键） | 做法不同；位掩码对绝大多数场景够用（见决策备注） |
| 死代码 | 无 | `keys.replace("+","+")`（no-op） | 需删除 |
| 参数校验 | `keys: str`（无约束，靠 description 引导） | `keys: str`（无 min_length，空串可过） | 缺 `min_length=1` |
| Enter 后等待 | Enter/Return 后额外 `sleep(0.1)` 等导航 | 固定 `sleep(0.1)`（所有键都 sleep） | 等价但可收窄到 Enter |
| 测试 | 无针对 send_keys 的单测（browser-use 自身也有此缺口） | **无**（`tests/` 下无 `test_send_keys.py`） | 缺测试（覆盖率缺口） |

---

## 3. 改进方案（分级）

### P0 核心 —— send_keys 工具本身

#### G1｜成功回显（对齐 scroll/click/close_tab）

发送成功后回显 `Sent keys '{keys}'`（单引号包裹原始输入，例 `Sent keys 'Enter'` / `Sent keys 'Control+a'` / `Sent keys 'hello'`），写入 `extracted_content` + `long_term_memory`，并 `logger.info`。归一化发生在 session 层，action 层透传原始 `keys` 串（回显真实输入，便于 LLM 对账）。

- 不额外截断：`ActionResult.display_max_chars=500`（`agent/views.py:9`）兜底；与 scroll 风格一致。
- 满足回显铁律（`ActionResult.validate_success_requires_done`，`agent/views.py:18-25`）：非 done 动作绝不设 `success=True`，只用 `extracted_content`+`long_term_memory`。

#### G2｜异常处理（send_keys 非幂等 → error 而非软成功，对齐 scroll 范式）

区别于 `close_tab`（幂等，失败软成功），send_keys **非幂等**——按键会提交表单/触发导航，CDP 失败 LLM 必须知道。用 `ActionResult(error=f"Send keys failed: {e}")`。

**改写后的 `_action_send_keys`（替换 `actions.py:640-642`）：**

```python
async def _action_send_keys(self, params: dict, browser: BrowserSession) -> ActionResult:
	keys = params["keys"]
	try:
		await browser.send_keys(keys)
	except Exception as e:
		# send_keys 非幂等（按键可能提交表单/触发导航），CDP 失败即报 error
		# （对齐 scroll 范式，区别于 close_tab 的软降级）
		logger.warning("send_keys(%r) failed: %s", keys, e)
		return ActionResult(error=f"Send keys failed: {e}")
	memory = f"Sent keys '{keys}'"
	logger.info(memory)
	return ActionResult(extracted_content=memory, long_term_memory=memory)
```

#### G3｜别名归一化 + 特殊键集合（session 层，`session.py:62-71` 区块）

在 `_KEY_CHAR_TEXT` 之后新增模块级常量：

```python
# 别名归一化表（查表前 .lower()，大小写不敏感）
_KEY_ALIASES: dict[str, str] = {
	"ctrl": "Control", "control": "Control",
	"alt": "Alt", "option": "Alt",
	"meta": "Meta", "cmd": "Meta", "command": "Meta",
	"shift": "Shift",
	"enter": "Enter", "return": "Enter",
	"esc": "Escape", "escape": "Escape",
	"backspace": "Backspace",
	"delete": "Delete", "del": "Delete",
	"tab": "Tab",
	"space": " ",
	"up": "ArrowUp", "arrowup": "ArrowUp",
	"down": "ArrowDown", "arrowdown": "ArrowDown",
	"left": "ArrowLeft", "arrowleft": "ArrowLeft",
	"right": "ArrowRight", "arrowright": "ArrowRight",
	"pageup": "PageUp", "pgup": "PageUp",
	"pagedown": "PageDown", "pgdn": "PageDown",
	"home": "Home", "end": "End",
	**{f"f{i}": f"F{i}" for i in range(1, 13)},
}

# 决定路由：命中则走"按键"分支（keyDown/keyUp），否则走文本逐字符
_SPECIAL_KEYS: frozenset[str] = frozenset({
	"Enter", "Tab", "Delete", "Backspace", "Escape",
	"ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
	"PageUp", "PageDown", "Home", "End",
	"Control", "Alt", "Meta", "Shift",
}) | {f"F{i}" for i in range(1, 13)}
# 注：不能用 `**{...}` 展开——frozenset 字面量里 `**` 是语法错误，用集合并集 `|`。
# 不含空格 " "：space 经别名归一化为 " " 后走文本逐字符分支（见决策备注）。

# 修饰键位掩码（提为模块级，便于测试引用；值同现有 modifier_map）
_MODIFIER_VK: dict[str, int] = {"alt": 1, "control": 2, "meta": 4, "shift": 8}

# 多字符特殊键的 DOM code 字符串
_KEY_CODE_FOR_SPECIAL: dict[str, str] = {
	"enter": "Enter", "tab": "Tab", "escape": "Escape",
	"backspace": "Backspace", "delete": "Delete",
	"arrowup": "ArrowUp", "arrowdown": "ArrowDown",
	"arrowleft": "ArrowLeft", "arrowright": "ArrowRight",
	"pageup": "PageUp", "pagedown": "PageDown",
	"home": "Home", "end": "End",
	"control": "ControlLeft", "alt": "AltLeft",
	"shift": "ShiftLeft", "meta": "MetaLeft",
	**{f"f{i}": f"F{i}" for i in range(1, 13)},
}
```

**扩充 `_KEY_VK_MAP`（替换 `session.py:62-65`）——补全 Arrow/Home/End/PageUp/PageDown/Delete/F1-F12 的 windowsVirtualKeyCode：**

```python
_KEY_VK_MAP: dict[str, int] = {
	"enter": 13, "tab": 9, "escape": 27, "backspace": 8, "delete": 46,
	"arrowup": 38, "arrowdown": 40, "arrowleft": 37, "arrowright": 39,
	"pageup": 33, "pagedown": 34, "home": 36, "end": 35,
	**{f"f{i}": 0x70 + (i - 1) for i in range(1, 13)},  # F1=0x70 ... F12=0x7B
}
```

> `_KEY_CHAR_TEXT`（enter→`\r`、tab→`\t`）保持不变。Delete/Backspace/Escape 不补 char（browser-use 一致：这些键 char 文本浏览器行为不一致）。

**别名归一化辅助函数：**

```python
def _normalize_key(raw: str) -> str:
	"""归一化单个按键 token：别名 → 标准 DOM key 名；单字符原样返回。

	大小写不敏感（查表前 .lower()）。未命中别名时原样返回，
	让调用方按 _SPECIAL_KEYS 判定路由。
	"""
	return _KEY_ALIASES.get(raw.lower(), raw)
```

#### G4｜`BrowserSession.send_keys` 重写：三条路由分支（`session.py:1164-1214`）

删除死代码 `keys.replace("+","+")`，按"组合键 / 单特殊键 / 文本逐字符"三路派发。

```python
async def send_keys(self, keys: str) -> None:
	"""发送按键：组合键（'Control+a'）/ 单特殊键（'Enter'）/ 文本逐字符（'hello'）。"""
	sid = self.current_session_id
	if "+" in keys:
		await self._send_combination(keys, sid)
		return
	normalized = _normalize_key(keys)
	if normalized in _SPECIAL_KEYS:
		await self._send_single_special_key(normalized, sid)
	else:
		# 文本分支：逐字符复用 _type_char（已正确处理 keyDown→char→keyUp、CJK insertText）。
		# 遍历 normalized 而非原始 keys，使别名映射到可打印字符时（如 'space'→' '）正确输入；
		# 对普通文本 normalized == keys。字符间额外间隔对齐 browser-use 的 ~10ms/字符节奏。
		for ch in normalized:
			await self._type_char(ch, sid=sid)
			await asyncio.sleep(0.005)
```

**组合键分支 `_send_combination`（核心修正点：单字符主键走按键语义带 modifiers，不能 fallback 文本分支，否则 Ctrl+A 全选失效）：**

```python
async def _send_combination(self, keys: str, sid: str) -> None:
	parts = [p.strip() for p in keys.split("+")]
	modifiers = 0
	for part in parts[:-1]:
		norm = _normalize_key(part)
		vk = _MODIFIER_VK.get(norm.lower())
		if vk is None:
			# 未知修饰键 → 软降级（warning + 跳过）；组合键空间太大，不宜硬失败
			logger.warning("send_keys: ignoring unknown modifier '%s' in '%s'", part, keys)
			continue
		modifiers |= vk

	main = _normalize_key(parts[-1])
	if len(main) == 1 and main not in _SPECIAL_KEYS:
		# 单字符主键（Control+a）→ 按键语义带 modifiers（Ctrl+A 全选语义正确）
		await self._send_combo_char_key(main, sid, modifiers)
	else:
		await self._send_single_special_key(main, sid, modifiers=modifiers)
```

**组合键单字符主键 `_send_combo_char_key`（复用 `_get_char_modifiers_and_vk` / `_get_key_code_for_char`）：**

```python
async def _send_combo_char_key(self, char: str, sid: str, modifiers: int) -> None:
	char_mod, char_vk, base = _get_char_modifiers_and_vk(char)
	code = _get_key_code_for_char(base)
	total_mod = modifiers | char_mod
	await self.client.send.Input.dispatchKeyEvent(
		{"type": "keyDown", "key": base, "code": code,
		 "modifiers": total_mod, "windowsVirtualKeyCode": char_vk},
		session_id=sid,
	)
	await self.client.send.Input.dispatchKeyEvent(
		{"type": "char", "text": char, "key": char},
		session_id=sid,
	)
	await self.client.send.Input.dispatchKeyEvent(
		{"type": "keyUp", "key": base, "code": code,
		 "modifiers": total_mod, "windowsVirtualKeyCode": char_vk},
		session_id=sid,
	)
```

**单特殊键分支 `_send_single_special_key`（沿用现有 keyDown→char→keyUp 结构，补 code/VK 查表 + Enter 后 sleep）：**

```python
async def _send_single_special_key(self, key: str, sid: str, *, modifiers: int = 0) -> None:
	key_lower = key.lower()
	code = _KEY_CODE_FOR_SPECIAL.get(key_lower, key)
	vk = _KEY_VK_MAP.get(key_lower, 0)

	await self.client.send.Input.dispatchKeyEvent(
		{"type": "keyDown", "key": key, "code": code,
		 "modifiers": modifiers, "windowsVirtualKeyCode": vk},
		session_id=sid,
	)
	char_text = _KEY_CHAR_TEXT.get(key_lower)  # Enter → "\r", Tab → "\t"
	if char_text:
		await self.client.send.Input.dispatchKeyEvent(
			{"type": "char", "text": char_text, "key": key},
			session_id=sid,
		)
	await self.client.send.Input.dispatchKeyEvent(
		{"type": "keyUp", "key": key, "code": code,
		 "modifiers": modifiers, "windowsVirtualKeyCode": vk},
		session_id=sid,
	)
	if key_lower == "enter":
		await asyncio.sleep(0.1)  # Enter 后等可能导航（对齐 browser-use）
```

#### G5｜`SendKeysParams` 校验（`models.py:59-62`）

```python
class SendKeysParams(BaseModel):
	model_config = ConfigDict(extra="forbid")
	keys: str = Field(
		min_length=1,
		description=(
			"Key combination or text to send. "
			"Combos use '+': 'Control+a', 'Shift+T', 'Alt+F4'. "
			"Named keys: 'Enter', 'Tab', 'Escape', 'ArrowUp', 'F5', etc. "
			"Plain text (e.g. 'hello') is typed character by character."
		),
	)
```

- **加 `min_length=1`**：拒绝空串；**不**拒绝纯空白（`" "` 是合法的 space 键，Pydantic 不剥空白，正确放行）。
- **未知键名：软降级，不硬拒绝。** 组合键空间无限（`Control+` 后可接任意字符），`Literal` 枚举不可行；browser-use 也软处理。软降级体现：组合键未知修饰键 → warning + 跳过；未知单字符主键 → 走按键 char 派发；未知多字符主键 → fallback 文本逐字符（最坏逐字符输入，不崩）。

#### G6｜新增 `tests/test_send_keys.py`（对齐 `tests/test_scroll.py` 三层结构）

> CLAUDE.md 要求：改功能必须同步加测试、覆盖率 >85%。action 层用 `_make_browser` 桩（`MagicMock`+`AsyncMock`，经 `Tools().execute` 调用）；param model 层校验 `ValidationError`；session 层用 `BrowserSession.__new__` 绕过 `__init__` + `MagicMock` client。所有 async 测试显式标 `@pytest.mark.asyncio`。

| 测试类 | 用例 |
|---|---|
| `TestSendKeysAction` | ① 单键 `{"keys":"Enter"}` 回显 `Sent keys 'Enter'`、`extracted_content == long_term_memory`、`send_keys.assert_awaited_once_with("Enter")`；② 组合键 `Control+a` 回显 `Sent keys 'Control+a'`；③ 文本 `hello` 回显 `Sent keys 'hello'`；④ CDP 异常（`send_raises=RuntimeError("cdp timeout")`）→ `error == "Send keys failed: cdp timeout"`、`extracted_content is None`（非软成功）；⑤ `get_state.assert_not_awaited()`；⑥ 别名透传：传 `"Return"` 仍以原始 `"Return"` 调 `browser.send_keys`；⑦ 成功路径 `success is None`（回显铁律） |
| `TestSendKeysParams` | 接受命名键/组合键/文本/单空格（space 键）；拒绝空串（`min_length=1`）；`extra="forbid"` 拒绝未知字段 |
| `TestSendKeysSession` | **别名**：Control+Enter 与 Ctrl+Enter 同序列且 `modifiers&2`；`return`→keyDown `key=="Enter"`+char `"\r"`；`up`→`key=="ArrowUp"`、vk==38；`esc`→`key=="Escape"`；`space`→经 `_type_char` 走文本/char 分支（keyDown `key==" "`、code=="Space"、vk==32、char `text==" "`）；`del`→`key=="Delete"`、vk==46。**单特殊键**：Enter 三连 dispatch（keyDown/char `"\r"`/keyUp）、vk==13；Tab char `"\t"`；ArrowDown vk==40 无 char；F5 vk==0x74、code=="F5"；Home/End/PageUp/PageDown vk 36/35/33/34。**组合键（含单字符主键修正）**：`Control+a`→keyDown `key=="a"`、`modifiers==2`、char `text=="a"`（Ctrl+A 全选语义正确）；`Shift+T`→keyDown `key=="t"`（base 小写，与 `_type_char` 一致）、`modifiers&8`、char `text=="T"`；`Alt+F4`→`key=="F4"`、`modifiers&1`；`Control+Shift+a`→`modifiers==10`；`Foo+a`→Foo 忽略（warning）仍对 'a' 派发（`modifiers==0`）不抛。**文本**：`hi`→2 组 keyDown/char/keyUp、char text 依次 "h"/"i"；`你好`→CJK 走 insertText 仅 char 事件。**异常/边界**：dispatch 抛→send_keys 抛（action 层接住）；`a+b`→走组合键分支（修饰 a 忽略、主键 b 单字符 char "b"，固化 `+` 歧义行为）；Enter 后 sleep 一次、Tab 不 sleep |

action 层桩（复用 `test_scroll.py` 的 `_make_browser` 模式）：

```python
def _make_browser(*, send_raises=None) -> MagicMock:
	bs = MagicMock()
	bs.send_keys = AsyncMock(side_effect=send_raises) if send_raises else AsyncMock(return_value=None)
	bs.get_state = AsyncMock()  # 仅断言未被调用
	return bs
```

session 层桩：`BrowserSession.__new__(BrowserSession)` + `s.current_session_id` + `s.client`，stub `client.send.Input.dispatchKeyEvent` 为 `AsyncMock`，spy `_type_char`（用 `AsyncMock` 覆盖实例方法）验证文本分支复用。

### P1 增强 —— 对齐 browser-use 进阶能力（本次仅画蓝图）

#### G7｜逐修饰键 keyDown/keyUp

browser-use 在组合键里"依次 keyDown 各修饰键 → 主键 down/up → 逆序 keyUp 各修饰键"。TreeWalker 当前只在主键事件带 `modifiers` 位掩码。位掩码对绝大多数场景够用（CDP `modifiers` 字段就是为此设计，浏览器据此设 `ctrlKey/altKey/...`），唯一已知局限：监听修饰键**自身** keydown 的罕见页面（自定义快捷键录制器）不触发。若后续遇此类站点，再移植逐键 keyDown/keyUp。

#### G8｜字面 `+` 文本支持（解决 `+` 歧义）

当前 `+` 既是修饰键分隔符也是合法字符，文本 `a+b` 被当组合键。browser-use 同样未解决。可选方案：约定转义（如 `++` 表示字面 `+`）或无修饰键 fallback（split 后若所有非末段都不是合法修饰键，则整体当文本）。引入语法复杂度，与"send_keys 发原始串"语义冲突；真实场景 send_keys 主用于快捷键，长文本应用已存在的 `input_text`（无此问题）。本次照搬局限，文档注明引导改用 `input_text`。

### 决策备注

- **send_keys 异常用 error 而非软成功**（对齐 scroll，区别 close_tab）：send_keys 非幂等（按键提交表单/触发导航），CDP 失败 LLM 必须知道；close_tab 幂等（目标不存在=已达成）。
- **修饰键保留位掩码做法**：见 G7。CDP `modifiers` 字段设计如此；与 `_type_char`/`input_text` 风格一致。
- **未知键名软降级**：见 G5。组合键空间无限，硬拒绝误伤合法用法。
- **组合键单字符主键必须走按键语义**：见 G4 `_send_combo_char_key`。`Control+a` 的 `a` 不在 `_SPECIAL_KEYS`，若 fallback 文本分支会丢 modifiers（Ctrl+A 全选失效）。这是与 browser-use 行为对齐的核心修正点。
- **文本分支复用 `_type_char`**：已正确处理 ASCII（keyDown+char+keyUp+5ms）和 CJK（`Input.insertText`）；send_keys 文本分支只在外层再加 5ms 节奏控制。不重复造轮子。
- **文本分支遍历 `normalized` 而非 `keys`**：使别名映射到可打印字符时（`space`→`" "`）按归一化结果逐字符输入；普通文本 `normalized == keys`，行为不变。
- **space 走文本/char 分支而非特殊键**：`space` 别名归一化为 `" "` 后不在 `_SPECIAL_KEYS`（对齐 browser-use，其 special_keys 也不含空格），故经 `_type_char` 以 keyDown/char/keyUp 三连输入（`code="Space"`、vk=32、char `text=" "`）。若当特殊键处理，`key_lower=" "` 无法命中以 `"space"` 为键的 `_KEY_CODE_FOR_SPECIAL`/`_KEY_VK_MAP`，反而出错。
- **Enter 后 sleep 收窄**：当前所有键都 `sleep(0.1)`；改为仅 Enter 后 sleep（等可能导航），其余键不 sleep，减少无谓延迟。
- **`+` 文本歧义照搬**：见 G8。文档显式注明，引导改用 `input_text`。
- **回显不额外截断**：`ActionResult.display_max_chars=500` 兜底；与 scroll 风格一致。
- **`terminates_sequence`**：send_keys 在 `ACTION_DEFINITIONS`（`models.py:184`）为 `False`，保持不变（对齐 browser-use）。

---

## 4. CDP 调用清单（变更后）

| 路由 | CDP 命令 | 变化 |
|---|---|---|
| 组合键（单字符主键，`Control+a`） | `Input.dispatchKeyEvent` ×3：keyDown(`code`/`modifiers`/`vk`) → char(`text`) → keyUp | **新增**（当前 fallback 不带 modifiers，全选失效） |
| 组合键（特殊键主键，`Alt+F4`） | keyDown(`code`/`modifiers`/`vk`) → [char] → keyUp | VK 从 0 补全（F4 等） |
| 单特殊键（`Enter`/`ArrowDown`/`F5`...） | keyDown(`code`/`vk`) → [char（Enter/Tab）] → keyUp | VK/code 补全；Enter 后 sleep；其余键不再无脑 sleep |
| 文本逐字符（`hello`） | 复用 `_type_char`：每字符 keyDown→char→keyUp（CJK 走 `Input.insertText`） | **新增文本分支**（当前不支持） |

---

## 5. 涉及文件清单

| 文件 | 改动 | 对应改进 |
|---|---|---|
| `src/tree_walker/tools/actions.py` | `_action_send_keys` 重写（G1 回显 + G2 try/except error） | G1 / G2 |
| `src/tree_walker/tools/models.py` | `SendKeysParams.keys` 加 `min_length=1` + description | G5 |
| `src/tree_walker/browser/session.py` | 新增常量（`_KEY_ALIASES`/`_SPECIAL_KEYS`/`_MODIFIER_VK`/`_KEY_CODE_FOR_SPECIAL`）+ `_normalize_key`；扩充 `_KEY_VK_MAP`；重写 `send_keys` + 新增 `_send_combination`/`_send_combo_char_key`/`_send_single_special_key`；删死代码 | G3 / G4 |
| `tests/test_send_keys.py` | **新建**（三层：action / param / session） | G6 |
| `docs/Tools技术细节/04_动作清单与CDP映射.md` | 4.20 节同步更新（回显/异常处理/别名/文本分支/三路由），刷新过时行号（文档写 `actions.py:255-257` / `session.py:671-721`，实际 `actions.py:640-642` / `session.py:1164-1214`） | 文档同步（实施后） |

---

## 6. 实施后的验证

- 新增测试：`uv run python -m pytest tests/test_send_keys.py -x -v`
- 全量回归：`uv run python -m pytest tests/ -x -v`
- 覆盖率（CLAUDE.md 目标 >85%）：`uv run python -m pytest tests/ --cov=tree_walker.tools --cov=tree_walker.browser`
- 手测（聚焦与 browser-use 行为对齐的核心修正）：
  1. **Ctrl+A 全选**：聚焦一个有文本的输入框 → `send_keys {"keys":"Control+a"}` → 文本被全选（验证 G4 `_send_combo_char_key` 带 modifiers，全选语义正确）；
  2. **别名**：`send_keys {"keys":"return"}` 与 `{"keys":"Enter"}` 行为一致（均触发提交/char `\r`）；`{"keys":"up"}` 等价 `ArrowUp`；
  3. **特殊键 VK**：`{"keys":"F5"}` 刷新页面、`{"keys":"ArrowDown"}` 下移焦点（验证 VK 补全后浏览器响应，当前 vk=0 可能无响应）；
  4. **文本分支**：聚焦输入框 → `send_keys {"keys":"hello"}` → 输入框出现 `hello`（验证文本逐字符分支，当前完全不支持）；
  5. **回显**：成功 → `Sent keys 'Control+a'`；CDP 断开 → `Send keys failed: ...`；
  6. **参数校验**：`{"keys":""}` → Pydantic 校验失败、工具不执行；
  7. **`+` 歧义**：`{"keys":"a+b"}` → 走组合键分支（修饰 a 忽略、输入 b），确认长文本应改用 `input_text`。
