# B 站标题输入框清空失效：根因分析与修复方案

## Context

用户报告：在 B 站投稿页（`https://member.bilibili.com/platform/upload/video/frame`）使用 `input_text` Tool 修改"稿件标题"输入框时，工具声称已清空旧内容，但实际**旧文本原封不动**，新输入的文字被追加在旧文本末尾。

通过 `examples/debug_bilibili_title_clear.py` 在真实页面上对比了 4 种清空策略，**精确复现**了追加问题并定位了根因。

## 复现证据

调试脚本 Step 5 模拟真实 `input_text` 流程（旧文本 → 当前实现的清空 → 逐字符输入新文本），最终读取到的 input value 是：

```
TREEWALKER_OLD_TEXT_你好世界NEW_TEXT
```

旧文本 `TREEWALKER_OLD_TEXT_你好世界` 完整保留，新文本 `NEW_TEXT` 被追加在后面。这就是用户看到的现象。

## 目标元素

```html
<input class="input-val" type="text" placeholder="请输入稿件标题" maxlength="80">
```

| 维度 | 取值 |
|---|---|
| 标签 | `<input type="text">`（普通 input，非 textarea/contenteditable） |
| Shadow DOM | 否 |
| React Fiber | 否 |
| Vue 痕迹 | 自身无；父级容器 `div.video-title` 有 `__vue__` |
| TreeWalker selector_map | **存在**，索引 `[398]`，bid=398 |

**关键结论**：元素本身完全标准、TreeWalker 也看得见它，问题不在 TreeWalker 的 DOM 采集层。

## 四种清空策略对比

每种策略独立测试：先用 JS native setter 填入 `TREEWALKER_OLD_TEXT_你好世界` → 执行清空 → 读取 `activeElement.value`。

| 策略 | 实现 | 结果 |
|---|---|---|
| **A. CDP `Ctrl+A + Backspace`** | `Input.dispatchKeyEvent` 四次（当前 `type_text(clear=True)` 的全部逻辑） | ❌ 失败，value 仍为旧文本 |
| **B. 原生 setter + dispatch input/change** | `Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, '')` + `dispatchEvent(new Event('input'))` | ✅ 成功 |
| **C. `execCommand(selectAll) + delete`** | `document.execCommand('selectAll'); document.execCommand('delete')` | ✅ 成功 |
| **D. CDP `Home + Shift+End + Delete`** | `Input.dispatchKeyEvent` 六次（替代按键序列） | ❌ 失败，value 仍为旧文本 |

## 根因

`BrowserSession.type_text(clear=True)`（`src/tree_walker/browser/session.py:564-578`）的清空路径**完全依赖 CDP `Input.dispatchKeyEvent` 派发 `Ctrl+A` + `Backspace`**：

```python
# src/tree_walker/browser/session.py:566-577
if clear:
    await self.client.send.Input.dispatchKeyEvent(
        {"type": "keyDown", "key": "a", "code": "KeyA", "modifiers": 2},
        session_id=sid,
    )
    await self.client.send.Input.dispatchKeyEvent(
        {"type": "keyUp", "key": "a", "code": "KeyA", "modifiers": 2},
        session_id=sid,
    )
    await self.client.send.Input.dispatchKeyEvent(
        {"type": "keyDown", "key": "Backspace", "code": "Backspace"},
        session_id=sid,
    )
    await self.client.send.Input.dispatchKeyEvent(
        {"type": "keyUp", "key": "Backspace", "code": "Backspace"},
        session_id=sid,
    )
```

B 站标题 `<input>` 的事件链上挂了 keydown 拦截器（页面 Vue 编译产物），**把 `Ctrl+A` 和 `Backspace` 吞掉或绕过了 input 的标准选区逻辑**，导致 CDP 按键虽然发出去，但选区/删除都没真正发生。

策略 D 同样使用 CDP 按键（`Home/Shift+End/Delete`），也失败 —— 进一步印证是 **CDP 按键路径被组件拦截**，而不是 `Ctrl+A` 这个特定组合的问题。

而策略 B/C 之所以成功，是因为它们走的是 **JS API 直接操作 DOM value**，不经过 input 元素的事件链 —— 组件拦截不到。

## 修复方案（完整复刻 browser-use 的两层防线）

### browser-use 参考实现

browser-use 在同一个 B 站标题输入框上工作正常。它的鲁棒性来自**两层防线**：

1. **多层清空策略** —— `browser-use/browser_use/browser/watchdogs/default_action_watchdog.py:1380-1530` 的 `_clear_text_field`：
   - **策略 1（最优先）**：JS `this.select() + this.value=""` + dispatch input/change
   - **策略 2（fallback）**：鼠标三击全选 + Delete 键
   - **策略 3（last resort）**：`Ctrl/Cmd+A + Backspace` —— 这恰是 TreeWalker 现在**唯一**使用的路径

2. **拼接检测兜底** —— 同文件 `:1994-2045` 在 `_input_text_element_node_impl` 末尾：
   - 输入完毕回读 `actual_value`
   - 如果 `actual_value != text` 且 `len(actual_value) > len(text)` 且 `actual_value.endswith(text)` 或 `startswith(text)`，判定为"旧文本未被清掉、新文本被追加"
   - 用 **native setter 强制覆盖**：`Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, text)` + dispatch input/change

TreeWalker 当前缺这两层中的每一层，所以一旦按键被拦截就束手无策。

### 适配 TreeWalker

TreeWalker 的 `BrowserSession` 不保留 element 的 `objectId`，只能操作 `document.activeElement`，所以全部走 `Runtime.evaluate` 而不是 `Runtime.callFunctionOn`。

### 改造 1：新增 `_clear_text_field`（三层清空策略）

文件 `src/tree_walker/browser/session.py`，紧邻 `type_text`（约 line 595 之后）新增：

```python
async def _clear_text_field(self) -> bool:
    """三层清空策略，对应 browser-use _clear_text_field。

    Returns: True 表示某一层成功（activeElement.value/textContent 为空）。
    """
    sid = self.current_session_id

    # Strategy 1: JS select() + value='' (覆盖 input/textarea/contenteditable)
    try:
        result = await self.client.send.Runtime.evaluate({
            "expression": """
            (function() {
                var el = document.activeElement;
                if (!el || el === document.body) return {cleared: false, error: 'no active'};
                el.focus();
                if (el.isContentEditable) {
                    var sel = window.getSelection();
                    var range = document.createRange();
                    range.selectNodeContents(el);
                    sel.removeAllRanges();
                    sel.addRange(range);
                    el.textContent = '';
                    el.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'deleteContent'}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    return {cleared: true, method: 'contenteditable', final: el.textContent};
                }
                if (el.value !== undefined) {
                    try { el.select(); } catch(e) {}
                    el.value = '';
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    return {cleared: true, method: 'value', final: el.value};
                }
                return {cleared: false, error: 'unsupported element'};
            })()
            """,
            "returnByValue": True,
        }, session_id=sid)
        info = (result.get('result') or {}).get('value') or {}
        final = (info.get('final') or '').strip() if isinstance(info.get('final'), str) else ''
        if info.get('cleared') and not final:
            return True
    except Exception as e:
        logger.debug('_clear_text_field strategy 1 failed: %s', e)

    # Strategy 2: Triple-click + Delete
    try:
        coord_result = await self.client.send.Runtime.evaluate({
            "expression": """(function(){
                var el = document.activeElement;
                if (!el || el === document.body) return null;
                var r = el.getBoundingClientRect();
                return JSON.stringify({x: r.x + r.width/2, y: r.y + r.height/2});
            })()""",
            "returnByValue": True,
        }, session_id=sid)
        import json
        coord_json = (coord_result.get('result') or {}).get('value')
        if coord_json:
            c = json.loads(coord_json)
            await self.client.send.Input.dispatchMouseEvent({
                "type": "mousePressed", "x": c['x'], "y": c['y'],
                "button": "left", "clickCount": 3,
            }, session_id=sid)
            await self.client.send.Input.dispatchMouseEvent({
                "type": "mouseReleased", "x": c['x'], "y": c['y'],
                "button": "left", "clickCount": 3,
            }, session_id=sid)
            await self.client.send.Input.dispatchKeyEvent(
                {"type": "keyDown", "key": "Delete", "code": "Delete"}, session_id=sid)
            await self.client.send.Input.dispatchKeyEvent(
                {"type": "keyUp", "key": "Delete", "code": "Delete"}, session_id=sid)
            if await self._read_active_text() == '':
                return True
    except Exception as e:
        logger.debug('_clear_text_field strategy 2 failed: %s', e)

    # Strategy 3: Ctrl+A + Backspace (last resort, 保留原有逻辑)
    try:
        await self.client.send.Input.dispatchKeyEvent(
            {"type": "keyDown", "key": "a", "code": "KeyA", "modifiers": 2}, session_id=sid)
        await self.client.send.Input.dispatchKeyEvent(
            {"type": "keyUp", "key": "a", "code": "KeyA", "modifiers": 2}, session_id=sid)
        await self.client.send.Input.dispatchKeyEvent(
            {"type": "keyDown", "key": "Backspace", "code": "Backspace"}, session_id=sid)
        await self.client.send.Input.dispatchKeyEvent(
            {"type": "keyUp", "key": "Backspace", "code": "Backspace"}, session_id=sid)
        return await self._read_active_text() == ''
    except Exception as e:
        logger.debug('_clear_text_field strategy 3 failed: %s', e)
        return False
```

### 改造 2：新增辅助方法 `_read_active_text` 和 `_force_set_value`

同文件，紧邻 `_clear_text_field`：

- `_read_active_text() -> str`：返回 `document.activeElement.value || textContent`；无 activeElement 时返回 `''`
- `_force_set_value(text: str) -> None`：用 native setter 强制覆盖（input/textarea 用 `HTMLInputElement.prototype.value.set`，contenteditable 用 `textContent`），dispatch `input` + `change` 事件。
  - **关于 dispatch `change`**：拼接兜底是"最后一搏"，需要让框架确认 value 变化；不会触发"标签输入框 blur 清空"副作用（issue #1 修复的关切），因为该路径只在已经出错的兜底场景触发，元素本身仍处于 focused 状态，不会发 blur。

### 改造 3：改造 `type_text`，接入新清空 + 拼接兜底

文件 `src/tree_walker/browser/session.py:564-595`：

1. 把现有 `if clear: ... Ctrl+A+Backspace`（line 572-588）替换为 `if clear: await self._clear_text_field()`
2. 在 `_trigger_framework_events()` 之后追加：

```python
# 拼接检测兜底（仅 clear=True 时）
if clear:
    try:
        actual = await self._read_active_text()
        if (
            isinstance(actual, str)
            and actual != text
            and len(actual) > len(text)
            and (actual.endswith(text) or actual.startswith(text))
        ):
            logger.info('Concatenation detected (%r), force-overwriting via native setter', actual)
            await self._force_set_value(text)
    except Exception as e:
        logger.debug('Concatenation check failed (non-critical): %s', e)
```

### 不在本 issue 范围

- macOS 的 Cmd+A 适配（browser-use 用 `platform.system()` 区分；TreeWalker 当前硬编码 Ctrl）
- date/time/color/range 输入类型的直接赋值特化（browser-use `_requires_direct_value_assignment`）
- contenteditable 首字符丢失 bug 修复（browser-use `default_action_watchdog.py:1842-1844`）
- watchdog 层抽象（TreeWalker 暂不需要）

## 影响范围

- ✅ 修复 B 站标题输入框追加问题
- ✅ 不影响现有标签输入流程（`B站标签输入修复与input_text_Tool说明.md` 已验证的路径仍走 `_trigger_framework_events`，无回归）
- ⚠️ `_trigger_framework_events`（`session.py:638-`）的"只发 `input`、不发 `change`/`blur`"约定**保持不变** —— 否则会再次触发 B 站 Vue 的 blur 副作用清空 input value（详见上一份文档）。**只有**新增的 `_force_set_value`（拼接兜底）允许 dispatch `change`，因为它的触发条件比 `_trigger_framework_events` 严格得多（只在检测到拼接时）。

## 验证清单

修复后需通过的验证：

- [ ] 新增 `tests/test_input_text_clear.py`，覆盖以下 case：
  - [ ] `test_clear_strategy1_js_success`：策略 1 命中时立即返回 True，不进入策略 2/3
  - [ ] `test_clear_strategy2_triple_click_fallback`：策略 1 失败 → 策略 2 三击 → 回读为空 → 返回 True
  - [ ] `test_clear_strategy3_keyboard_last_resort`：策略 1/2 失败 → 策略 3 走 Ctrl+A+Backspace
  - [ ] `test_clear_handles_contenteditable`：activeElement 是 contenteditable 时走 textContent 分支
  - [ ] `test_read_active_text_returns_value_or_textcontent`：input/div/无 active 三种场景
  - [ ] `test_force_set_value_uses_native_setter`：JS 表达式包含 `HTMLInputElement.prototype` 和 `dispatchEvent(new Event('input'`
  - [ ] `test_type_text_concatenation_triggers_force_set`：clear=True、回读 `OLDNEW`、text=`NEW` → `_force_set_value` 被调用
  - [ ] `test_type_text_no_concatenation_when_match`：回读 == text → `_force_set_value` 不被调用
  - [ ] `test_type_text_concatenation_check_non_critical`：`_read_active_text` 抛异常时，`type_text` 不应抛
- [ ] 修改 `tests/test_input_text_framework.py:158-165` 的 `test_type_text_clear_first`：不再断言固定 7 次 `dispatchKeyEvent`（因为现在优先走 JS 路径）；改为断言 `_clear_text_field` 被调用，且最终 value 为空
- [ ] 回归 `uv run python -m pytest tests/ -x -v` 全部通过
- [ ] 覆盖率仍 > 85%
- [ ] 重新跑 `examples/debug_bilibili_title_clear.py`：
  - [ ] 策略 A 在新代码路径下应输出 ✅（实际上会走 `_clear_text_field` 的策略 1 直接命中）
  - [ ] Step 5 模拟真实 `input_text`：最终 value 为 `NEW_TEXT`，不再包含 `TREEWALKER_OLD_TEXT_你好世界`

## 调试脚本

`examples/debug_bilibili_title_clear.py`（本分析的产生过程）

调试输出节选：

```
──── 策略：A. CDP Ctrl+A + Backspace（当前实现） ────
  清空后 activeElement: {'value': 'TREEWALKER_OLD_TEXT_你好世界', ...}
  >>> 是否真正清空: 否

──── 策略：B. 原生 setter 清空 + dispatch input/change ────
  清空后 activeElement: {'value': '', ...}
  >>> 是否真正清空: 是

──── 策略：C. execCommand(selectAll) + delete ────
  清空后 activeElement: {'value': '', ...}
  >>> 是否真正清空: 是

──── 策略：D. CDP Home + Shift+End + Delete ────
  清空后 activeElement: {'value': 'TREEWALKER_OLD_TEXT_你好世界', ...}
  >>> 是否真正清空: 否
```
