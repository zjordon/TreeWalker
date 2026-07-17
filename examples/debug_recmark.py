"""诊断 D1：file input 打 data-tw-recmark 标记后，get_state 的 selector_map 能否读到它。

D1 凭标记定位 node 算指纹，前提：file input 在 selector_map 且其 node.attributes 含 data-tw-recmark。
本脚本实测：JS 给第一个 file input 打标记 → get_state → 遍历 selector_map 查标记。

用法：Chrome --remote-debugging-port=9222，停在有 file input 的页面（上传页/封面编辑器）。
      uv run python examples/debug_recmark.py
"""

import asyncio
import sys

sys.path.insert(0, f"{__file__}/../src")

from tree_walker.browser.session import BrowserSession
from tree_walker.config import load_settings
from tree_walker.recorder.recorder import select_http_target


async def eval_js(browser, sid, js):
	r = await browser.client.send.Runtime.evaluate(
		{"expression": f"(() => {{\n{js}\n}})()", "returnByValue": True},
		session_id=sid,
	)
	res = r.get("result", {})
	exc = r.get("exceptionDetails") or res.get("exceptionDetails")
	if exc:
		return None, f"JS异常: {exc.get('exception', {}).get('description', exc) if isinstance(exc, dict) else exc}"
	return res.get("value"), None


JS_MARK = r"""
const inp = document.querySelector('input[type="file"]');
if (!inp) return {marked: false, reason: '页无 input[type=file]'};
inp.setAttribute('data-tw-recmark', 'tw-test-123');
return {marked: true, accept: (inp.accept||'').slice(0,20), tag: inp.tagName};
"""


async def main():
	settings = load_settings()
	if not settings.browser.ws_url:
		print("✗ Chrome 未以 --remote-debugging-port=9222 启动"); sys.exit(1)
	browser = BrowserSession(settings.browser)
	await browser.start()
	resp = await browser.client.send.Target.getTargets({})
	tid = select_http_target(resp.get("targetInfos", []), None)
	if tid and tid != browser.current_target_id:
		await browser.switch_tab(tid)
	sid = browser.current_session_id

	# 1. 打标记
	m, exc = await eval_js(browser, sid, JS_MARK)
	if exc:
		print("打标记失败:", exc); await browser.stop(); return
	if not m.get("marked"):
		print("✗", m.get("reason")); await browser.stop(); return
	print(f"✓ 已给 file input({m['accept']}) 打标记 data-tw-recmark=tw-test-123")

	# 2. get_state
	state = await browser.get_state(include_screenshot=False)
	smap = state.dom_state.selector_map if state and state.dom_state else {}
	print(f"get_state: selector_map={len(smap)} 节点")

	# 3. 遍历 selector_map 查 file input + 标记
	file_inputs_in_map = 0
	marked_node = None
	for idx, node in smap.items():
		attrs = getattr(node, "attributes", {}) or {}
		is_file = (getattr(node, "node_name", "") or "").upper() == "INPUT" and attrs.get("type", "").lower() == "file"
		if is_file:
			file_inputs_in_map += 1
			has_mark = attrs.get("data-tw-recmark")
			print(f"  [map idx={idx}] file input accept={(attrs.get('accept','') or '')[:20]!r} data-tw-recmark={has_mark!r} class={(attrs.get('class','') or '')[:25]!r}")
			if has_mark == "tw-test-123":
				marked_node = idx

	print(f"\nselector_map 里 file input: {file_inputs_in_map} 个")
	if file_inputs_in_map == 0:
		print("❌ file input 不在 selector_map！这就是 D1 凭标记找不到 node 的原因——")
		print("   file input 虽在 file_inputs_meta，但没进 selector_map（serializer 未分配 highlight_index）。")
	if marked_node is not None:
		print(f"✅ 标记在 selector_map 可见（idx={marked_node}）——D1 凭标记能定位。")
		print("   那录制时找不到说明可能是时序（get_state 抓时标记还没打/页面已变）。")
	else:
		print("❌ 标记在 selector_map 的 file input 里读不到——node.attributes 没含 data-tw-recmark。")
		print("   可能：CDP snapshot 没捕获动态属性 / serializer 过滤了 data-* / file input 不在 map。")

	await browser.stop()


if __name__ == "__main__":
	asyncio.run(main())
