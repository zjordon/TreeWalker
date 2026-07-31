"""诊断脚本：分析B站视频上传页面"标题"输入框清空失败问题。

背景：调用 input_text 动作时，clear=True 会先发 Ctrl+A + Backspace，
但对于B站的标题输入框，已存在的文本并没有被清掉，导致新文字被追加
在旧文字后面。本脚本对比多种清空策略，定位失败原因并给出可用方案。

使用方法：
1. 在 Chrome 中打开B站投稿页面
   https://member.bilibili.com/platform/upload/video/frame
2. 让标题输入框处于可见状态，最好先手动填一些文字进去便于观察
3. 确保 Chrome 以 --remote-debugging-port=9222 启动
4. 运行: uv run python examples/debug_bilibili_title_clear.py
"""

import asyncio
import logging
import sys

sys.path.insert(0, f"{__file__}/../src")

from dom_snapshot import build_dom_state
from tree_walker.browser.session import BrowserSession
from tree_walker.browser.views import NodeType
from tree_walker.config import load_settings

logging.basicConfig(
	level=logging.INFO,
	format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# 标题相关关键词
TITLE_KEYWORDS = [
	'title', '标题',
	'title-input', 'title_input', 'titleInput',
	'video-title', 'video_title',
	'upload-title', 'name',
]


def find_title_elements(node, depth=0, results=None):
	"""递归搜索 DOM 树中标题相关的元素。"""
	if results is None:
		results = []

	if hasattr(node, 'node_type') and node.node_type != NodeType.ELEMENT_NODE:
		for child in getattr(node, 'children_and_shadow_roots', []):
			find_title_elements(child, depth + 1, results)
		return results

	attrs = getattr(node, 'attributes', {}) or {}
	tag_name = getattr(node, 'tag_name', '').lower()

	cls = attrs.get('class', '').lower()
	placeholder = attrs.get('placeholder', '').lower()
	role = attrs.get('role', '').lower()
	aria_label = attrs.get('aria-label', '').lower()
	name_attr = attrs.get('name', '').lower()
	id_attr = attrs.get('id', '').lower()
	all_text = f"{cls} {placeholder} {role} {aria_label} {name_attr} {id_attr}"

	hit = any(kw in all_text for kw in TITLE_KEYWORDS)
	if not hit and tag_name == 'input':
		input_type = attrs.get('type', '').lower()
		if input_type in ('text', 'search', '', 'textarea') and ('title' in all_text or '标题' in all_text):
			hit = True

	if hit:
		results.append((depth, node))

	for child in getattr(node, 'children_and_shadow_roots', []):
		find_title_elements(child, depth + 1, results)

	return results


# ─────────────────────────── JS 探测 / 操作片段 ───────────────────────────

JS_LOCATE_TITLE = r"""
(function() {
	var findings = [];

	// 多种选择器策略，覆盖 B 站投稿页常见 DOM
	var selectors = [
		'input[placeholder*="标题"]',
		'input[placeholder*="title" i]',
		'textarea[placeholder*="标题"]',
		'textarea[placeholder*="title" i]',
		'[class*="title-input"]',
		'[class*="title_input"]',
		'[class*="titleInput"]',
		'[class*="upload-title"]',
		'[class*="video-title"]',
		'[class*="title"] input',
		'[class*="title"] textarea',
		'[class*="title"] [contenteditable]',
		'[data-module="title"] input',
		'input[name*="title" i]',
		'#title',
	];

	var seen = new Set();
	for (var i = 0; i < selectors.length; i++) {
		var els;
		try { els = document.querySelectorAll(selectors[i]); } catch(e) { continue; }
		els.forEach(function(el) {
			if (seen.has(el)) return;
			seen.add(el);
			var rect = el.getBoundingClientRect();
			findings.push({
				strategy: selectors[i],
				tag: el.tagName.toLowerCase(),
				type: el.type || '',
				name: el.name || '',
				id: el.id || '',
				class: (el.className || '').toString().slice(0, 120),
				placeholder: el.placeholder || el.getAttribute('placeholder') || '',
				contenteditable: el.contentEditable,
				maxlength: el.maxLength,
				value: (el.value || '').slice(0, 60),
				textContent: (el.textContent || '').slice(0, 60),
				visible: rect.width > 0 && rect.height > 0,
				rect: Math.round(rect.width) + 'x' + Math.round(rect.height),
				inShadowDOM: el.getRootNode() !== document,
				isFocused: document.activeElement === el,
				reactFiber: !!(el._reactRootContainer || el.__reactInternalInstance || el.__reactFiber),
				vue: !!(el.__vue__ || el.__vueParentComponent__)
			});
		});
	}

	return JSON.stringify(findings, null, 2);
})()
"""

def build_fill_title_js(selector_hint: str = '') -> str:
	"""构造"填入旧文本"的 JS（不依赖 CDP 参数传递）。"""
	hint = selector_hint or 'input[placeholder*="标题"], textarea[placeholder*="标题"]'
	return (
		r"""
		(function() {
			var selectorHint = """ + repr(hint) + r""";
			var el = null;
			if (selectorHint) {
				try { el = document.querySelector(selectorHint); } catch(e) {}
			}
			if (!el) {
				var candidates = document.querySelectorAll('input[placeholder*="标题"], textarea[placeholder*="标题"], input[placeholder*="title" i], textarea[placeholder*="title" i]');
				for (var i = 0; i < candidates.length; i++) {
					var r = candidates[i].getBoundingClientRect();
					if (r.width > 0 && r.height > 0) { el = candidates[i]; break; }
				}
			}
			if (!el) return JSON.stringify({ok: false, reason: 'not found'});

			el.focus();
			var oldVal;
			if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
				oldVal = el.value;
				// 使用 native setter + input event，让 React/Vue 正确感知
				var proto = el.tagName === 'INPUT' ? HTMLInputElement.prototype : HTMLTextAreaElement.prototype;
				var setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
				setter.call(el, 'TREEWALKER_OLD_TEXT_你好世界');
				el.dispatchEvent(new Event('input', {bubbles: true}));
			} else {
				oldVal = el.textContent;
				el.textContent = 'TREEWALKER_OLD_TEXT_你好世界';
				el.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText'}));
			}
			return JSON.stringify({ok: true, tag: el.tagName.toLowerCase(), oldVal: oldVal});
		})()
		"""
	)

# 关键诊断：每种清空策略都尝试一次，返回清空后的值，判断是否真的清掉了
JS_PROBE_CLEAR_STRATEGIES = r"""
(function() {
	// 由 Python 端通过 Runtime.evaluate 多次调用，每次执行一种策略。
	// 这里仅返回当前输入框的基本信息；具体清空动作由 Python 端的
	// 不同 expression 完成，以便隔离每种策略的影响。
	var el = document.activeElement;
	if (!el || el === document.body) {
		return JSON.stringify({ok: false, reason: 'no active element'});
	}
	var tag = el.tagName.toLowerCase();
	var info = {
		ok: true,
		tag: tag,
		type: el.type || '',
		value: '',
		textContent: '',
		isContenteditable: el.isContentEditable,
		readOnly: el.readOnly,
		disabled: el.disabled,
	};
	if (tag === 'input' || tag === 'textarea') {
		info.value = el.value;
	} else {
		info.textContent = el.textContent;
	}
	return JSON.stringify(info);
})()
"""


# ─────────────────────────── 清空策略实现 ───────────────────────────
# 每个 expression 都设计成：先 focus，再执行清空，最后通过 returnByValue
# 返回 JSON。注意：执行前需要保证输入框里已经有旧文本。

STRATEGY_CTRL_A_BACKSPACE = r"""
(function() {
	// 复刻当前 TreeWalker type_text(clear=True) 的清空逻辑
	var el = document.activeElement;
	if (!el || el === document.body) return JSON.stringify({ok: false});
	el.focus();
	return JSON.stringify({ok: true, note: '请改用 CDP Input.dispatchKeyEvent 触发 Ctrl+A + Backspace'});
})()
"""

STRATEGY_NATIVE_SETTER = r"""
(function() {
	// 直接通过原生 setter 清空 value，并触发 input/change 事件
	var el = document.activeElement;
	if (!el || el === document.body) return JSON.stringify({ok: false, reason: 'no active'});
	el.focus();
	var tag = el.tagName.toLowerCase();
	if (tag === 'input' || tag === 'textarea') {
		var proto = tag === 'input' ? HTMLInputElement.prototype : HTMLTextAreaElement.prototype;
		var setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
		setter.call(el, '');
		el.dispatchEvent(new Event('input', {bubbles: true}));
		el.dispatchEvent(new Event('change', {bubbles: true}));
	} else if (el.isContentEditable) {
		el.textContent = '';
		el.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'deleteContent'}));
	}
	return JSON.stringify({ok: true, after: tag === 'input' || tag === 'textarea' ? el.value : el.textContent});
})()
"""

STRATEGY_SELECT_ALL_CMD = r"""
(function() {
	// 使用 document.execCommand('selectAll') + execCommand('delete')
	var el = document.activeElement;
	if (!el || el === document.body) return JSON.stringify({ok: false, reason: 'no active'});
	el.focus();
	try { document.execCommand('selectAll', false); } catch(e) {}
	try { document.execCommand('delete', false); } catch(e) {}
	var tag = el.tagName.toLowerCase();
	return JSON.stringify({ok: true, after: tag === 'input' || tag === 'textarea' ? el.value : el.textContent});
})()
"""

STRATEGY_HOME_SHIFT_END_DELETE = r"""
(function() {
	// Home → Shift+End → Delete 的序列（依赖 CDP 按键，这里只是占位提示）
	var el = document.activeElement;
	if (!el || el === document.body) return JSON.stringify({ok: false, reason: 'no active'});
	el.focus();
	return JSON.stringify({ok: true, note: '请改用 CDP Input.dispatchKeyEvent 触发 Home+Shift+End+Delete'});
})()
"""


async def fill_title_via_js(browser, selector_hint: str = '') -> dict:
	"""通过 JS 给标题填入一段旧文本，便于后续测试清空。"""
	raw = await browser.execute_js(build_fill_title_js(selector_hint))
	try:
		import json
		return json.loads(raw)
	except Exception:
		return {'ok': False, 'raw': raw}


async def read_active_value(browser) -> dict:
	"""读取当前 activeElement 的 value/textContent。"""
	raw = await browser.execute_js(JS_PROBE_CLEAR_STRATEGIES)
	try:
		import json
		return json.loads(raw)
	except Exception:
		return {'ok': False, 'raw': raw}


async def apply_cdp_clear_ctrl_a_backspace(browser):
	"""完全复刻当前 type_text(clear=True) 的清空逻辑。"""
	sid = browser.current_session_id
	await browser.client.send.Input.dispatchKeyEvent(
		{"type": "keyDown", "key": "a", "code": "KeyA", "modifiers": 2},
		session_id=sid,
	)
	await browser.client.send.Input.dispatchKeyEvent(
		{"type": "keyUp", "key": "a", "code": "KeyA", "modifiers": 2},
		session_id=sid,
	)
	await browser.client.send.Input.dispatchKeyEvent(
		{"type": "keyDown", "key": "Backspace", "code": "Backspace"},
		session_id=sid,
	)
	await browser.client.send.Input.dispatchKeyEvent(
		{"type": "keyUp", "key": "Backspace", "code": "Backspace"},
		session_id=sid,
	)


async def apply_cdp_clear_home_shift_end_delete(browser):
	"""替代清空策略：Home → Shift+End → Delete。"""
	sid = browser.current_session_id

	async def press(key, code, modifiers=0):
		await browser.client.send.Input.dispatchKeyEvent(
			{"type": "keyDown", "key": key, "code": code, "modifiers": modifiers, "windowsVirtualKeyCode": 0},
			session_id=sid,
		)
		await browser.client.send.Input.dispatchKeyEvent(
			{"type": "keyUp", "key": key, "code": code, "modifiers": 0},
			session_id=sid,
		)

	await press("Home", "Home")
	await press("End", "End", modifiers=8)  # shift=8
	await press("Delete", "Delete")


async def type_text_via_cdp(browser, text: str):
	"""复刻 BrowserSession._type_char 的逐字符输入。"""
	sid = browser.current_session_id
	for char in text:
		is_ascii = ord(char) < 128
		if is_ascii:
			await browser.client.send.Input.dispatchKeyEvent(
				{"type": "keyDown", "key": char, "code": f"Key{char.upper()}"},
				session_id=sid,
			)
		await browser.client.send.Input.dispatchKeyEvent(
			{"type": "char", "text": char, "key": char},
			session_id=sid,
		)
		if is_ascii:
			await browser.client.send.Input.dispatchKeyEvent(
				{"type": "keyUp", "key": char, "code": f"Key{char.upper()}"},
				session_id=sid,
			)
		await asyncio.sleep(0.005)


async def run_strategy_and_report(browser, name: str, strategy_js: str | None, cdp_fn=None):
	"""对一种清空策略：先填旧文本 → 读旧值 → 执行清空 → 读新值。"""
	print(f"\n──── 策略：{name} ────")

	# 1) 填入旧文本
	fill = await fill_title_via_js(browser, selector_hint='input[placeholder*="标题"], textarea[placeholder*="标题"]')
	if not fill.get('ok'):
		print(f"  [跳过] 无法定位标题输入框：{fill}")
		return None
	print(f"  填入旧文本后：tag={fill.get('tag')}, 旧值={fill.get('oldVal')!r}")

	# 2) 让标题输入框获得焦点（fill 已经 focus，这里再确认）
	await browser.execute_js(
		r"""
		(function() {
			var els = document.querySelectorAll('input[placeholder*="标题"], textarea[placeholder*="标题"]');
			for (var i = 0; i < els.length; i++) {
				var r = els[i].getBoundingClientRect();
				if (r.width > 0 && r.height > 0) { els[i].focus(); return true; }
			}
			return false;
		})()
		"""
	)
	await asyncio.sleep(0.1)

	# 3) 读清空前的值
	before = await read_active_value(browser)
	print(f"  清空前 activeElement: {before}")

	# 4) 执行清空
	if cdp_fn is not None:
		await cdp_fn(browser)
	elif strategy_js:
		raw = await browser.execute_js(strategy_js)
		print(f"  策略 JS 返回: {raw}")
	await asyncio.sleep(0.2)

	# 5) 读清空后的值
	after = await read_active_value(browser)
	print(f"  清空后 activeElement: {after}")

	cleared = False
	if after.get('ok'):
		val = after.get('value', '') or after.get('textContent', '')
		cleared = (val == '')
	print(f"  >>> 是否真正清空: {'是' if cleared else '否（文本仍存在，新输入将被追加）'}")
	return {'strategy': name, 'cleared': cleared, 'before': before, 'after': after}


async def main():
	settings = load_settings()

	if not settings.browser.ws_url:
		print("Error: Cannot connect to Chrome. Is it running with --remote-debugging-port=9222?")
		sys.exit(1)

	print("=" * 80)
	print("B站标题输入框清空问题诊断工具")
	print("=" * 80)

	browser = BrowserSession(settings.browser)
	await browser.start()

	sid = browser.current_session_id

	# ── Step 1: 用 JS 定位标题输入框 ──
	print("\n[1] 定位标题输入框...")
	js_findings = await browser.execute_js(JS_LOCATE_TITLE)
	print(f"JS 查找到的候选元素：\n{js_findings}\n")

	# ── Step 2: 用 CDP 获取完整 DOM 状态 ──
	print("\n[2] 获取 CDP DOM 状态...")
	dom_state, metrics = await build_dom_state(
		client=browser.client,
		session_id=sid,
		config=browser._dom_collection_config,
		previous_selector_map=None,
	)
	print(f"selector_map 大小={len(dom_state.selector_map)}")

	if dom_state._root is None:
		print("Error: DOM 树为空")
		await browser.stop()
		return

	# ── Step 3: 在 selector_map 中找标题元素 ──
	print("\n[3] 在 selector_map 中查找标题相关元素...")
	title_in_map = []
	for idx, elem in dom_state.selector_map.items():
		attrs = getattr(elem, 'attributes', {}) or {}
		all_text = (
			attrs.get('class', '') + ' ' +
			attrs.get('placeholder', '') + ' ' +
			attrs.get('name', '') + ' ' +
			attrs.get('id', '') + ' ' +
			attrs.get('aria-label', '')
		).lower()
		if any(kw in all_text for kw in TITLE_KEYWORDS):
			title_in_map.append({
				'index': idx,
				'tag': getattr(elem, 'tag_name', '').lower(),
				'class': attrs.get('class', ''),
				'placeholder': attrs.get('placeholder', ''),
				'backend_node_id': elem.backend_node_id,
			})

	if title_in_map:
		print(f"找到 {len(title_in_map)} 个标题相关可交互元素：")
		for item in title_in_map:
			print(f"  索引 [{item['index']}] <{item['tag']}> class=\"{item['class']}\" "
				  f"placeholder=\"{item['placeholder']}\" bid={item['backend_node_id']}")
	else:
		print("⚠️ 标题输入框未出现在 selector_map 中——可能 TreeWalker 当前根本访问不到它")

	# ── Step 4: 对比多种清空策略 ──
	print("\n[4] 对比多种清空策略（每种策略独立测试，先填旧文本再清空）...")
	results = []

	# 策略 A：复刻当前 type_text 的 Ctrl+A + Backspace
	results.append(await run_strategy_and_report(
		browser,
		name='A. CDP Ctrl+A + Backspace（当前实现）',
		strategy_js=None,
		cdp_fn=apply_cdp_clear_ctrl_a_backspace,
	))

	# 策略 B：原生 setter + input/change 事件
	results.append(await run_strategy_and_report(
		browser,
		name='B. 原生 setter 清空 + dispatch input/change',
		strategy_js=STRATEGY_NATIVE_SETTER,
	))

	# 策略 C：document.execCommand('selectAll') + delete
	results.append(await run_strategy_and_report(
		browser,
		name='C. execCommand(selectAll) + delete',
		strategy_js=STRATEGY_SELECT_ALL_CMD,
	))

	# 策略 D：CDP Home → Shift+End → Delete
	results.append(await run_strategy_and_report(
		browser,
		name='D. CDP Home + Shift+End + Delete',
		strategy_js=None,
		cdp_fn=apply_cdp_clear_home_shift_end_delete,
	))

	# ── Step 5: 模拟真实 input_text 流程，验证追加问题 ──
	print("\n[5] 模拟真实 input_text 流程：先填旧文本，再用 CDP Ctrl+A 清空 + 输入新文本")
	# 先 JS 填旧文本
	await fill_title_via_js(browser, selector_hint='input[placeholder*="标题"], textarea[placeholder*="标题"]')
	await asyncio.sleep(0.2)
	await browser.execute_js(
		r"""(function(){
			var els = document.querySelectorAll('input[placeholder*="标题"], textarea[placeholder*="标题"]');
			for (var i = 0; i < els.length; i++) {
				var r = els[i].getBoundingClientRect();
				if (r.width > 0 && r.height > 0) { els[i].focus(); return; }
			}
		})()"""
	)
	await asyncio.sleep(0.1)

	# 当前实现的清空 + 输入
	await apply_cdp_clear_ctrl_a_backspace(browser)
	await type_text_via_cdp(browser, 'NEW_TEXT')
	await asyncio.sleep(0.3)

	after_real = await read_active_value(browser)
	print(f"  模拟 input_text 后 activeElement 值: {after_real}")
	if after_real.get('ok'):
		v = after_real.get('value', '') or after_real.get('textContent', '')
		if v == 'NEW_TEXT':
			print("  >>> 行为正常：清空生效，只有新文本")
		elif v.endswith('NEW_TEXT'):
			print(f"  >>> 复现追加问题！最终值={v!r}，旧文本未被清空，新文本被追加")
		else:
			print(f"  >>> 异常状态：最终值={v!r}")

	# ── Step 6: 通过真实 BrowserSession.type_text 验证修复 ──
	print("\n[6] 通过 BrowserSession.type_text(clear=True) 验证修复后的行为（核心回归点）...")
	# 先用 JS 填入旧文本
	await fill_title_via_js(browser, selector_hint='input[placeholder*="标题"], textarea[placeholder*="标题"]')
	await asyncio.sleep(0.2)
	# 让标题获得焦点
	await browser.execute_js(
		r"""(function(){
			var els = document.querySelectorAll('input[placeholder*="标题"], textarea[placeholder*="标题"]');
			for (var i = 0; i < els.length; i++) {
				var r = els[i].getBoundingClientRect();
				if (r.width > 0 && r.height > 0) { els[i].focus(); return; }
			}
		})()"""
	)
	await asyncio.sleep(0.1)

	# 直接调用真实 type_text（不是脚本里的 apply_cdp_*）
	await browser.type_text("FRESH_NEW_TEXT", clear=True)
	await asyncio.sleep(0.3)

	after_fix = await read_active_value(browser)
	print(f"  type_text(clear=True) 后 activeElement 值: {after_fix}")
	if after_fix.get('ok'):
		v = after_fix.get('value', '') or after_fix.get('textContent', '')
		if v == 'FRESH_NEW_TEXT':
			print("  >>> ✅ 修复成功：旧文本被清空，新文本正确写入")
		elif v.endswith('FRESH_NEW_TEXT') or 'FRESH_NEW_TEXT' in v:
			print(f"  >>> ❌ 仍未修复：最终值={v!r}")
		else:
			print(f"  >>> ⚠️ 异常状态：最终值={v!r}")

	# ── Step 7: 总结 ──
	print("\n" + "=" * 80)
	print("诊断总结：")
	print(f"  selector_map 中标题相关元素: {len(title_in_map)} 个")
	print()
	print("  各清空策略效果：")
	for r in results:
		if r is None:
			continue
		status = '✅ 清空成功' if r['cleared'] else '❌ 清空失败（追加问题原因）'
		print(f"    {r['strategy']:50s} → {status}")

	print()
	print("  建议排查方向：")
	print("    1. 若 A 失败但 B/C 成功：当前 Ctrl+A+Backspace 路径被组件拦截，")
	print("       需要在 type_text 中加入 native setter 兜底。")
	print("    2. 若 A/B/C 都失败：组件可能是 contenteditable 或自定义渲染，")
	print("       需要专门处理 contenteditable 路径或调整 focus 时机。")
	print("    3. 若策略 A 的 after.value 仍含旧文本但 _trigger_framework_events")
	print("       之前已被调用，说明 keydown 在 selectAll 阶段就被 preventDefault。")

	await browser.stop()


if __name__ == "__main__":
	asyncio.run(main())
