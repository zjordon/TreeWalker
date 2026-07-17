"""诊断 httpbin 表单：submit button / p 在 selector_map 里吗？xpath/bounds 怎么对不上。

录制 click p[6]/button 定位失败（locate_by_ref 三道防线全 miss）。本脚本连当前 Chrome，
get_state 后 dump selector_map 里的 BUTTON/P/LABEL/INPUT/TEXTAREA，并用 JS 算页面 button 的
xpath（扩展 xpathFor 风格）+ bounds 对比。

用法：Chrome --remote-debugging-port=9222，停在 httpbin.org/forms/post（或当前页）。
      uv run python examples/debug_httpbin.py
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


JS = r"""
function cls(el){ return (el && typeof el.className==='string') ? el.className : ''; }
function xp(el){
  if(!el||el.nodeType!==1) return null;
  const p=[]; let c=el;
  while(c&&c.nodeType===1&&c!==document.documentElement){
    let i=1,s=c.previousElementSibling;
    while(s){ if(s.tagName===c.tagName)i++; s=s.previousElementSibling; }
    p.unshift(c.tagName.toLowerCase()+(i>1?`[${i}]`:'')); c=c.parentElement;
  }
  return '/'+['html',...p].join('/');
}
// 页面所有 button + p 的(xpath, bounds, text)
const btns = Array.from(document.querySelectorAll('button')).map(b=>{
  const r=b.getBoundingClientRect();
  return {tag:'BUTTON', xpath:xp(b), text:(b.textContent||'').trim().slice(0,15),
          bounds:[Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)],
          attrs:{type:b.type, name:b.name||'', id:b.id||'', 'aria-label':b.getAttribute('aria-label')||''}};
});
const ps = Array.from(document.querySelectorAll('form p')).map((p,n)=>{
  const r=p.getBoundingClientRect();
  return {n, tag:'P', xpath:xp(p), text:(p.textContent||'').trim().slice(0,20),
          bounds:[Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)]};
});
return {url:location.href, btns, ps};
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

	# JS dump 页面 button/p
	val, exc = await eval_js(browser, sid, JS)
	# selector_map dump button/p/label/input
	state = await browser.get_state(include_screenshot=False)
	await browser.stop()
	if exc:
		print("JS 失败:", exc); return
	print(f"URL: {val['url']}\n")

	print("=== 页面 button（JS）===")
	for b in val["btns"]:
		print(f"  {b['tag']} xpath={b['xpath']} bounds={b['bounds']} text={b['text']!r} attrs={b['attrs']}")
	print("\n=== 页面 form p（JS）===")
	for p in val["ps"]:
		print(f"  p[{p['n']}] xpath={p['xpath']} bounds={p['bounds']} text={p['text']!r}")

	# selector_map 里的可交互元素
	smap = state.dom_state.selector_map if state and state.dom_state else {}
	print(f"\n=== selector_map 里 BUTTON/P/LABEL/INPUT/TEXTAREA（共 {len(smap)} 节点）===")
	for idx, node in smap.items():
		name = (getattr(node, "node_name", "") or "").upper()
		if name in ("BUTTON", "P", "LABEL", "INPUT", "TEXTAREA"):
			attrs = getattr(node, "attributes", {}) or {}
			snap = getattr(node, "snapshot_node", None)
			b = getattr(snap, "bounds", None) if snap else None
			bounds = None
			if b is not None:
				try:
					bounds = [round(float(getattr(b, "x", 0))), round(float(getattr(b, "y", 0))),
					          round(float(getattr(b, "width", 0))), round(float(getattr(b, "height", 0)))]
				except Exception:
					bounds = None
			print(f"  idx={idx} {name} xpath={getattr(node,'xpath','')} bounds={bounds} type={attrs.get('type','')} name={attrs.get('name','')!r}")

	print("\n=== 结论 ===")
	btn_in_map = [idx for idx, n in smap.items() if (getattr(n, "node_name", "") or "").upper() == "BUTTON"]
	p_in_map = [idx for idx, n in smap.items() if (getattr(n, "node_name", "") or "").upper() == "P"]
	print(f"selector_map 里 BUTTON: {btn_in_map if btn_in_map else '❌ 无（button 没进 selector_map！）'}")
	print(f"selector_map 里 P: {p_in_map if p_in_map else '无（p 非可交互，预期不在）'}")


if __name__ == "__main__":
	asyncio.run(main())
