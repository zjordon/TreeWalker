"""精准确认 cover-Jg3T4p 在 light DOM / shadow DOM / 还是 CDP-only。

跑 4 个探测：① light DOM querySelectorAll；② 穿透 shadow 的深度搜索；
③ 找到它的宿主链；④ 它若存在，扩展 xpathFor vs CDP xpath 是否一致。
"""

import asyncio
import json
import sys

sys.path.insert(0, f"{__file__}/../src")

from tree_walker.browser.session import BrowserSession
from tree_walker.config import _fetch_ws_url

PROBE = r"""
(function () {
  function deepFind(root, sel) {
    let m = root.querySelector(sel);
    if (m) return {node: m, inShadow: false, hostChain: []};
    function walk(r, chain) {
      for (const el of r.querySelectorAll('*')) {
        if (el.shadowRoot) {
          const hit = el.shadowRoot.querySelector(sel);
          if (hit) return {node: hit, inShadow: true, hostChain: chain.concat(el.tagName + '.' + (el.className||'').toString().slice(0,40))};
          const deeper = walk(el.shadowRoot, chain.concat(el.tagName + '.' + (el.className||'').toString().slice(0,40)));
          if (deeper) return deeper;
        }
      }
      return null;
    }
    return walk(root, []);
  }
  function xpathFor(element) {
    const parts = []; let cur = element;
    while (cur && cur.nodeType === 1) {
      const tag = cur.tagName.toLowerCase();
      if (tag === 'html') { parts.unshift('html'); break; }
      const parent = cur.parentElement;
      const sibs = parent ? Array.from(parent.children).filter(s => s.tagName === cur.tagName) : [];
      const idx = sibs.length > 1 ? '[' + (sibs.indexOf(cur)+1) + ']' : '';
      parts.unshift(tag + idx);
      cur = cur.parentElement;
    }
    return '/' + parts.join('/');
  }
  const light = document.querySelectorAll('.cover-Jg3T4p').length;
  const found = deepFind(document, '.cover-Jg3T4p');
  return {
    lightDOM_count: light,
    deepFound: !!found,
    inShadow: found ? found.inShadow : null,
    hostChain: found ? found.hostChain : null,
    xpathFor_onNode: found ? xpathFor(found.node) : null,
    computed_cursor: found ? window.getComputedStyle(found.node).cursor : null,
  };
})()
"""


async def main() -> int:
	ws = _fetch_ws_url("localhost", 9222)
	br = BrowserSession(ws_url=ws)
	await br.start()
	try:
		r = await br.client.send.Runtime.evaluate(
			{"expression": PROBE, "returnByValue": True}, session_id=br.current_session_id,
		)
		exc = r.get("result", {}).get("exceptionDetails")
		if exc:
			print("JS exception:", json.dumps(exc, ensure_ascii=False)[:300])
			return 1
		print(json.dumps(r.get("result", {}).get("result", {}).get("value"), ensure_ascii=False, indent=2))

		# 对比 CDP xpath
		st = await br.get_state(include_screenshot=False)
		print("\n=== CDP selector_map cover-Jg3T4p xpath ===")
		for idx, n in st.dom_state.selector_map.items():
			a = getattr(n, "attributes", {}) or {}
			if "cover-Jg3T4p" in a.get("class", ""):
				print(f"  idx={idx} xpath={getattr(n,'xpath','')}")
		return 0
	finally:
		await br.stop()


if __name__ == "__main__":
	sys.exit(asyncio.run(main()))
