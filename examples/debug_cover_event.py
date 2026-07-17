"""诊断脚本：为什么「选择封面」click 没被扩展 onClick 收到。

排查抖音发布页点「选择封面」打开封面编辑器的 click 是否触发 window capture 的 click 事件、
target 是什么，以确认扩展 onClick 漏录的根因（mousedown 触发？iframe？React 合成事件未冒泡？
还是 onClick 收到了但 findInteractiveAncestor 录错？）。

用法（交互式）：
  1. Chrome --remote-debugging-port=9222，停在抖音发布页（含「选择封面」/ cover-Jg3T4p 区域）。
  2. uv run python examples/debug_cover_event.py
  3. 看 DOM dump（「选择封面」相关元素的 cursor/onclick/祖先）后，在浏览器点「选择封面」。
  4. 回到终端按回车 → 脚本输出 click/mousedown/pointerdown 捕获结果 + 结论。
"""

import asyncio
import json
import sys

sys.path.insert(0, f"{__file__}/../src")

from tree_walker.browser.session import BrowserSession
from tree_walker.config import load_settings
from tree_walker.recorder.recorder import select_http_target


async def eval_js(browser, sid, js):
	"""Runtime.evaluate + returnByValue，JS 包 IIFE。返回 (value, exc)。"""
	r = await browser.client.send.Runtime.evaluate(
		{"expression": f"(() => {{\n{js}\n}})()", "returnByValue": True},
		session_id=sid,
	)
	res = r.get("result", {})
	exc = r.get("exceptionDetails") or res.get("exceptionDetails")
	if exc:
		desc = exc.get("exception", {}).get("description", str(exc)) if isinstance(exc, dict) else str(exc)
		return None, f"JS异常: {desc}"
	return res.get("value"), None


# 注入 window.getXPath + window.__twClicks + capture 阶段 click/mousedown/pointer 监听
JS_INJECT = """
window.getXPath = function(el) {
  if (!el || el.nodeType !== 1) return null;
  const parts = [];
  let cur = el;
  while (cur && cur.nodeType === 1 && cur !== document.documentElement) {
    let idx = 1, sib = cur.previousElementSibling;
    while (sib) { if (sib.tagName === cur.tagName) idx++; sib = sib.previousElementSibling; }
    parts.unshift(cur.tagName.toLowerCase() + (idx > 1 ? `[${idx}]` : ''));
    cur = cur.parentElement;
  }
  return '/' + parts.join('/');
};
window.__twClicks = [];
function cls(el) {
  if (!el || typeof el.className !== 'string') return '';
  return el.className.slice(0, 80);
}
function rec(e) {
  const t = e.target;
  const cp = e.composedPath();
  window.__twClicks.push({
    type: e.type,
    target: t && t.tagName ? (t.tagName.toLowerCase() + (cls(t) ? '.' + cls(t).split(' ').join('.') : '')) : String(t),
    targetXpath: t && t.nodeType === 1 ? window.getXPath(t) : null,
    cursor: t && t.nodeType === 1 ? getComputedStyle(t).cursor : null,
    cpTop: cp.slice(0, 6).map(n => (n && n.tagName ? n.tagName.toLowerCase() + (cls(n) ? '.' + cls(n).split(' ').join('.') : '') : String(n))).join(' > '),
    ts: Date.now(),
  });
}
['click', 'mousedown', 'mouseup', 'pointerdown', 'pointerup', 'dblclick'].forEach(t =>
  window.addEventListener(t, rec, true),
);
return 'installed';
"""

# dump「选择封面」相关元素（结构、cursor、onclick、祖先 cursor:pointer）
JS_DUMP = """
const all = Array.from(document.querySelectorAll('*'));
const matches = all.filter(el => {
  if (el.children.length > 4) return false;
  const txt = (el.textContent || '').trim();
  const cls = typeof el.className === 'string' ? el.className : '';
  return (txt && (txt.includes('选择封面') || txt.includes('更换封面')) && txt.length < 20)
      || /cover-jg3t4p/i.test(cls);
});
return matches.slice(0, 15).map(el => {
  const r = el.getBoundingClientRect();
  let pa = null;
  let c = el.parentElement, n = 0;
  while (c && n < 6) {
    if (getComputedStyle(c).cursor === 'pointer') { pa = c.tagName.toLowerCase() + '.' + (typeof c.className === 'string' ? c.className.slice(0, 60) : ''); break; }
    c = c.parentElement; n++;
  }
  return {
    tag: el.tagName.toLowerCase(),
    class: cls(el),
    text: (el.textContent || '').trim().slice(0, 20),
    cursor: getComputedStyle(el).cursor,
    onclick: !!(el.onclick || el.getAttribute('onclick')),
    size: Math.round(r.width) + 'x' + Math.round(r.height),
    xpath: window.getXPath(el),
    pointerAncestor: pa,
  };
});
"""

JS_READ = "return JSON.stringify(window.__twClicks || []);"


async def main():
	settings = load_settings()
	if not settings.browser.ws_url:
		print("✗ Chrome 未以 --remote-debugging-port=9222 启动")
		sys.exit(1)

	browser = BrowserSession(settings.browser)
	await browser.start()
	resp = await browser.client.send.Target.getTargets({})
	tid = select_http_target(resp.get("targetInfos", []), None)
	if tid and tid != browser.current_target_id:
		await browser.switch_tab(tid)
	sid = browser.current_session_id
	print(f"✓ 已连页面 target={browser.current_target_id}")

	# 1. 注入 capture 监听
	_, exc = await eval_js(browser, sid, JS_INJECT)
	if exc:
		print("注入监听失败:", exc)
		await browser.stop()
		return
	print("✓ 已注入 capture 监听（click/mousedown/mouseup/pointerdown/pointerup/dblclick）")

	# 2. dump「选择封面」相关元素
	val, exc = await eval_js(browser, sid, JS_DUMP)
	print("\n=== 「选择封面」相关 DOM ===")
	if exc:
		print("dump 失败:", exc)
	elif not val:
		print("当前页未找到「选择封面」/ cover-Jg3T4p 元素——确认页面在发布页（post/video）。")
	else:
		for i, el in enumerate(val):
			print(f"[{i}] <{el['tag']}> class={el['class']!r} text={el['text']!r}")
			print(f"    cursor={el['cursor']}  onclick={el['onclick']}  size={el['size']}")
			print(f"    xpath={el['xpath']}")
			print(f"    pointerAncestor={el['pointerAncestor']}")

	# 3. 等用户点「选择封面」
	print("\n>>> 现在在浏览器点「选择封面」，然后回到这里按回车 <<<")
	try:
		input()
	except EOFError:
		pass

	# 4. 读 click log
	val, exc = await eval_js(browser, sid, JS_READ)
	logs = json.loads(val) if not exc and val else []
	print(f"\n=== 捕获到 {len(logs)} 条事件 ===")
	for ev in logs[-20:]:
		print(f"[{ev['type']}] target={ev['target']}  cursor={ev['cursor']}")
		print(f"    xpath={ev['targetXpath']}")
		print(f"    cpTop={ev['cpTop']}")

	# 5. 结论
	print("\n=== 结论判断 ===")
	clicks = [e for e in logs if e["type"] == "click"]
	mousedowns = [e for e in logs if e["type"] == "mousedown"]
	pointerdowns = [e for e in logs if e["type"] == "pointerdown"]
	if clicks:
		c = clicks[-1]
		print(f"✅ window capture 收到了 click！target={c['target']} cursor={c['cursor']}")
		print(f"   xpath={c['targetXpath']}")
		print(f"   → onClick 应能收到。若 recorded.json 仍漏录，查扩展 onClick 内逻辑：")
		print(f"     · A2 排除（raw 是否 file input）？")
		print(f"     · findInteractiveAncestor 录到谁（内部 div 还是 cursor:pointer 触发器）？")
		print(f"     · 后端 locate 是否定位失败导致 click 被丢弃？")
	elif mousedowns or pointerdowns:
		who = "mousedown" if mousedowns else "pointerdown"
		print(f"⚠️ 没有 click，但有 {who} —— 抖音用 {who} 触发，没派发 click 事件！")
		print(f"   扩展 onClick（监听 click）自然收不到。需改监听 {who}（或补 {who} 作为 click 源）。")
	else:
		print("❌ click/mousedown/pointerdown 都没捕获 —— 点击可能在 iframe，或事件被更早的 capture 监听器 stopImmediatePropagation。")
		print("   需排查 iframe / 事件拦截。")

	await browser.stop()


if __name__ == "__main__":
	asyncio.run(main())
