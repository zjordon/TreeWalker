"""诊断：对比扩展 xpathFor 与 CDP xpath，模拟 findInteractiveAncestor，
确认为何「选择封面」(cover-Jg3T4p) 录制时 locate 失败。

连真实 Chrome，找 cover-Jg3T4p 元素，分别用 CDP xpath 和注入页面的 xpathFor 计算，
看是否一致；并模拟 findInteractiveAncestor 看返回的是 cover-Jg3T4p 还是其内部子元素。
"""

import asyncio
import sys

sys.path.insert(0, f"{__file__}/../src")

from tree_walker.browser.session import BrowserSession
from tree_walker.config import _fetch_ws_url

# 注入页面的 xpathFor（复制自 recording_extension/capture/selector.ts）
XPATHFOR_JS = r"""
(xpathFor = function(element) {
  const parts = [];
  let current = element;
  while (current && current.nodeType === 1) {
    const tag = current.tagName.toLowerCase();
    if (tag === 'html') { parts.unshift('html'); break; }
    const parent = current.parentElement;
    const siblings = parent ? Array.from(parent.children).filter(s => s.tagName === current.tagName) : [];
    const index = siblings.length > 1 ? `[${siblings.indexOf(current) + 1}]` : '';
    parts.unshift(`${tag}${index}`);
    current = current.parentElement;
  }
  return `/${parts.join('/')}`;
})
"""

# 模拟 findInteractiveAncestor（复制 action-recorder.ts 逻辑）
FIA_JS = r"""
(findIA = function(el) {
  const INTERACTIVE = 'a[href],button,input,select,textarea,summary,label,[contenteditable],[role="button"],[role="link"],[role="textbox"],[role="menuitem"],[role="tab"],[role="checkbox"],[role="radio"],[role="switch"],[role="option"]';
  let cur = el;
  while (cur && cur !== document.body) {
    try {
      if (cur.matches(INTERACTIVE)) return cur;
      if (cur.tagName === 'DIV' && window.getComputedStyle(cur).cursor === 'pointer') return cur;
      if (cur.onclick || cur.getAttribute('onclick') || cur.getAttribute('onmousedown')) return cur;
    } catch (e) {}
    cur = cur.parentElement;
  }
  return el;
})
"""


async def main() -> int:
	ws_url = _fetch_ws_url("localhost", 9222)
	browser = BrowserSession(ws_url=ws_url)
	await browser.start()
	try:
		client = browser.client
		sid = browser.current_session_id
		# 注入两个函数到页面
		await client.send.Runtime.evaluate({"expression": XPATHFOR_JS, "returnByValue": False}, session_id=sid)
		await client.send.Runtime.evaluate({"expression": FIA_JS, "returnByValue": False}, session_id=sid)

		# 对每个 cover-Jg3T4p：取 CDP xpath vs xpathFor vs findIA 结果
		expr = r"""
		(() => {
		  const out = [];
		  document.querySelectorAll('.cover-Jg3T4p').forEach(el => {
		    const computed = window.getComputedStyle(el).cursor;
		    const rect = el.getBoundingClientRect();
		    // 模拟点其中心，找 raw=中心元素的 findIA
		    const raw = document.elementFromPoint(rect.x + rect.width/2, rect.y + rect.height/2);
		    out.push({
		      cdp_xpath_hint: el.className,
		      xpathFor: xpathFor(el),
		      computed_cursor: computed,
		      rect: {x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height)},
		      center_raw_tag: raw ? raw.tagName + '.' + (raw.className||'').toString().slice(0,30) : null,
		      findIA_onCover: findIA(el) ? (findIA(el).tagName + '.' + (findIA(el).className||'').toString().slice(0,30)) : null,
		      findIA_onCenterRaw: raw ? (findIA(raw) ? findIA(raw).tagName + '.' + (findIA(raw).className||'').toString().slice(0,30) : null) : null,
		    });
		  });
		  return out;
		})()
		"""
		resp = await client.send.Runtime.evaluate({"expression": expr, "returnByValue": True}, session_id=sid)
		val = resp.get("result", {}).get("result", {}).get("value", [])
		print(f"找到 {len(val)} 个 .cover-Jg3T4p 元素：\n")
		for v in val:
			print(f"  class={v['cdp_xpath_hint']}")
			print(f"  xpathFor(扩展) = {v['xpathFor']}")
			print(f"  computed cursor = {v['computed_cursor']}")
			print(f"  rect = {v['rect']}")
			print(f"  中心 elementFromPoint = {v['center_raw_tag']}")
			print(f"  findIA(点 cover 本身) = {v['findIA_onCover']}")
			print(f"  findIA(点中心 raw)   = {v['findIA_onCenterRaw']}")
			print()

		# 对比 CDP selector_map 里 cover-Jg3T4p 的 xpath
		state = await browser.get_state(include_screenshot=False)
		print("=== CDP selector_map 里 cover-Jg3T4p 的 xpath ===")
		for idx, node in state.dom_state.selector_map.items():
			attrs = getattr(node, "attributes", {}) or {}
			if "cover-Jg3T4p" in attrs.get("class", ""):
				print(f"  [map idx={idx}] CDP xpath = {getattr(node, 'xpath', '')}")
		return 0
	finally:
		await browser.stop()


if __name__ == "__main__":
	sys.exit(asyncio.run(main()))
