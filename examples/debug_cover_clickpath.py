"""模拟录制 click 路径：elementFromPoint(cover 中心) → findInteractiveAncestor → xpathFor →
在 selector_map 里查得到吗？确认为何录制时 locate 失败。

对每个 cover-Jg3T4p 取其中心点 elementFromPoint（= 用户点击命中的 raw 元素），
跑扩展的 findInteractiveAncestor + xpathFor，再与 CDP selector_map 比对。
"""

import asyncio
import json
import sys

sys.path.insert(0, f"{__file__}/../src")

from tree_walker.browser.session import BrowserSession
from tree_walker.config import _fetch_ws_url

PROBE = r"""
(function(){
  const INTERACTIVE = 'a[href],button,input,select,textarea,summary,label,[contenteditable],[role="button"],[role="link"],[role="textbox"],[role="menuitem"],[role="tab"],[role="checkbox"],[role="radio"],[role="switch"],[role="option"]';
  function findIA(el){
    let cur = el;
    while (cur && cur !== document.body) {
      try {
        if (cur.matches(INTERACTIVE)) return cur;
        if (cur.tagName === 'DIV' && window.getComputedStyle(cur).cursor === 'pointer') return cur;
        if (cur.onclick || cur.getAttribute('onclick') || cur.getAttribute('onmousedown')) return cur;
      } catch(e){}
      cur = cur.parentElement;
    }
    return el;
  }
  function xpathFor(el){
    const p=[]; let c=el;
    while(c && c.nodeType===1){ const t=c.tagName.toLowerCase(); if(t==='html'){p.unshift('html');break;}
      const par=c.parentElement; const s=par?Array.from(par.children).filter(x=>x.tagName===c.tagName):[];
      p.unshift(t+(s.length>1?('['+(s.indexOf(c)+1)+']'):'')); c=par; }
    return '/'+p.join('/');
  }
  const out = [];
  document.querySelectorAll('.cover-Jg3T4p').forEach(cover => {
    const r = cover.getBoundingClientRect();
    const raw = document.elementFromPoint(r.x + r.width/2, r.y + r.height/2);
    const ia = raw ? findIA(raw) : null;
    out.push({
      cover_rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)},
      raw_at_center: raw ? (raw.tagName + '.' + (raw.className||'').toString().slice(0,40)) : null,
      raw_cursor: raw ? window.getComputedStyle(raw).cursor : null,
      findIA: ia ? (ia.tagName + '.' + (ia.className||'').toString().slice(0,40)) : null,
      findIA_cursor: ia ? window.getComputedStyle(ia).cursor : null,
      findIA_xpath: ia ? xpathFor(ia) : null,
      findIA_is_cover: ia === cover,
    });
  });
  return JSON.stringify(out);
})
"""


async def main() -> int:
	ws = _fetch_ws_url("localhost", 9222)
	br = BrowserSession(ws_url=ws)
	await br.start()
	try:
		r = await br.client.send.Runtime.evaluate(
			{"expression": PROBE, "returnByValue": True}, session_id=br.current_session_id,
		)
		val = r.get("result", {}).get("result", {}).get("value")
		print("=== 模拟 click cover-Jg3T4p 的录制路径 ===")
		for item in json.loads(val):
			print(json.dumps(item, ensure_ascii=False, indent=2))
			print()

		st = await br.get_state(include_screenshot=False)
		print("=== CDP selector_map 含的 cover 相关 xpath ===")
		for idx, n in st.dom_state.selector_map.items():
			a = getattr(n, "attributes", {}) or {}
			if "cover" in a.get("class", ""):
				print(f"  idx={idx} {getattr(n,'xpath','')}")
		return 0
	finally:
		await br.stop()


if __name__ == "__main__":
	sys.exit(asyncio.run(main()))
