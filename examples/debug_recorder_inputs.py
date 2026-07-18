"""诊断脚本：查看抖音上传页「作品描述」区域的可输入元素，
排查录制时副标题 input_text 漏录的原因（元素类型 / shadow DOM / contenteditable）。

两个视角：
  - 原生 DOM（扩展 content script 看到的）：含当前 value、contenteditable、是否在 shadow DOM
  - TreeWalker selector_map（后端 locator 用的）：index + xpath

用法：
  1. Chrome 以 --remote-debugging-port=9222 启动，停在上传/编辑页（作品描述区）。
  2. uv run python examples/debug_recorder_inputs.py
"""

import asyncio
import json
import sys

sys.path.insert(0, f"{__file__}/../src")

from tree_walker.browser.session import BrowserSession
from tree_walker.config import load_settings
from tree_walker.recorder.recorder import select_http_target


async def main():
	settings = load_settings()
	if not settings.browser.ws_url:
		print("Error: 无法连接 Chrome（确认以 --remote-debugging-port=9222 启动）")
		sys.exit(1)

	browser = BrowserSession(settings.browser)
	await browser.start()

	# 切到 http page（避开 popup/扩展页）
	resp = await browser.client.send.Target.getTargets({})
	tid = select_http_target(resp.get("targetInfos", []), None)
	if tid and tid != browser.current_target_id:
		await browser.switch_tab(tid)

	state = await browser.get_state(include_screenshot=False)
	print(f"url   = {state.url}")
	print(f"title = {state.title}")

	# ── 原生 DOM 视角：所有可输入元素 + 当前 value（扩展看到的就是这些）──
	js = """
	(() => {
		const out = [];
		const skipTypes = ['file','hidden','submit','button','checkbox','radio','image','reset'];
		document.querySelectorAll('input, textarea, [contenteditable]').forEach((el) => {
			const tag = el.tagName.toLowerCase();
			const type = (el.getAttribute('type') || '').toLowerCase();
			if (tag === 'input' && skipTypes.includes(type)) return;
			const r = el.getBoundingClientRect();
			const root = el.getRootNode();
			const inShadow = root && root.nodeType === 11;  // DocumentFragment = shadow root
			out.push({
				tag, type,
				name: el.getAttribute('name') || '',
				id: el.id || '',
				placeholder: el.getAttribute('placeholder') || '',
				ariaLabel: el.getAttribute('aria-label') || '',
				contentEditable: el.isContentEditable,
				inShadow,
				value: ((tag === 'input' || tag === 'textarea') ? el.value : (el.textContent || '')).slice(0, 60),
				outerHtml: el.outerHTML.slice(0, 200),
				visible: r.width > 0 && r.height > 0,
				x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height),
			});
		});
		return JSON.stringify(out);
	})()
	"""
	result = await browser.client.send.Runtime.evaluate(
		{"expression": js, "returnByValue": True},
		session_id=browser.current_session_id,
	)
	inputs = json.loads(result["result"]["value"])

	print(f"\n原生 DOM 可输入元素 {len(inputs)} 个（扩展 content script 视角）：")
	print("-" * 92)
	for i, el in enumerate(inputs):
		ce = " [contenteditable]" if el["contentEditable"] else ""
		sh = " [⚠️shadow DOM]" if el["inShadow"] else ""
		vis = "" if el["visible"] else " [隐藏]"
		t = f" type={el['type']}" if el["type"] else ""
		print(f"  [{i}] <{el['tag']}{t}>{ce}{sh}{vis}")
		print(f"      name={el['name']!r} id={el['id']!r} placeholder={el['placeholder']!r} aria={el['ariaLabel']!r}")
		print(f"      value={el['value']!r}  @({el['x']},{el['y']}) {el['w']}x{el['h']}")
		print(f"      outerHTML={el['outerHtml']!r}")

	# ── TreeWalker selector_map 视角：可输入元素的 index + xpath ──
	print("\n" + "=" * 92)
	print("TreeWalker selector_map 里的可输入元素（后端 locator 用 index + xpath）")
	print("=" * 92)
	dom_state = state.dom_state
	cnt = 0
	for idx in sorted(dom_state.selector_map.keys()):
		n = dom_state.selector_map[idx]
		tag = (getattr(n, "tag_name", "") or "").upper()
		a = getattr(n, "attributes", None) or {}
		is_input_like = (
			(tag == "INPUT" and (a.get("type", "") or "").lower() not in ("file", "hidden", "submit", "button"))
			or tag == "TEXTAREA"
		)
		if not is_input_like:
			continue
		cnt += 1
		print(f"  [index {idx}] <{tag}> name={a.get('name', '')!r} placeholder={a.get('placeholder', '')!r} aria={a.get('aria-label', '')!r}")
		print(f"      xpath={getattr(n, 'xpath', '')!r}")
	print(f"  selector_map 里可输入元素 {cnt} 个")

	# ── selector_map 里 contenteditable 元素（确认副标题 div 在不在 map）──
	print("\n" + "=" * 92)
	print("selector_map 里含 contenteditable 属性的元素（副标题是 contenteditable div，看它在不在）")
	print("=" * 92)
	ce_hit = 0
	for idx in sorted(dom_state.selector_map.keys()):
		n = dom_state.selector_map[idx]
		a = getattr(n, "attributes", None) or {}
		if any(k.lower() == "contenteditable" for k in a.keys()):
			ce_hit += 1
			print(f"  [index {idx}] <{getattr(n, 'tag_name', '')}> contenteditable={a.get('contenteditable', '')!r}")
			print(f"      xpath={getattr(n, 'xpath', '')!r}")
	print(f"  命中 {ce_hit} 个")
	if ce_hit == 0:
		print("  ⚠️ TreeWalker 没把 contenteditable div 收进 selector_map —— 即使录到副标题，重放也定位不到。")
		print("     （需在 dom.py 的可交互元素判定里补 [contenteditable]，或录制时把 contenteditable 当特殊 input_text 处理）")

	await browser.stop()
	print("\n打印完成")


if __name__ == "__main__":
	asyncio.run(main())
