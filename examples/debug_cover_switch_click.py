"""诊断：B 站封面"切换 tab"点击为何没录进 bili-2.json（step 5→6 之间的 click 丢失）。

假设：扩展 ``action-recorder.ts`` 的 ``onClick`` 把切换 tab 的 click 丢了——因为
``findInteractiveAncestor(raw)`` 返回 null（非可交互祖先、非 cursor:pointer div、无 onclick 属性）。
``onClick`` 对 ``findInteractiveAncestor`` 返回 null 的 click 直接 ``return``（不 emit），所以该 click
根本没进事件流 → 录制文件没有这一步 → 重放时 step 6(竖版上传) 与 step 5(横版上传) 目标同一 input，
被「冗余重试」跳过。

本探针连真实 Chrome（``--remote-debugging-port=9222``），在 B 站封面页：
  1. 静态：盘点疑似"切换 tab"元素（role=tab / 文案含 横/竖/截帧/封面 / class 含 tab|switch|cover），
     对每个**原样复刻**扩展的 ``findInteractiveAncestor``，报告 onClick 是否会捕获它。
  2. 动态：装一个 capture 阶段 click 监听（复刻 onClick 逻辑），把用户每次点击的 raw 目标 +
     findInteractiveAncestor 结果存进 ``window.__twClickProbe``；脚本轮询打印。
     → **请在脚本运行期间点击"切换封面 tab"**（横版/竖版），看该 click 是 emitted 还是 dropped。

用法：uv run python examples/debug_cover_switch_click.py [轮询秒数，默认 30]
"""

import asyncio
import sys

sys.path.insert(0, f"{__file__}/../src")

from tree_walker.browser.session import BrowserSession
from tree_walker.config import load_settings
from tree_walker.recorder.recorder import select_http_target


async def eval_js(browser, sid, js):
	r = await browser.client.send.Runtime.evaluate(
		{"expression": f"(()=>{{\n{js}\n}})()", "returnByValue": True, "awaitPromise": False},
		session_id=sid,
	)
	res = r.get("result", {})
	exc = r.get("exceptionDetails") or res.get("exceptionDetails")
	if exc:
		desc = exc.get("exception", {}).get("description", str(exc)) if isinstance(exc, dict) else str(exc)
		return None, f"JS异常: {desc}"
	return res.get("value"), None


# 原样复刻 action-recorder.ts 的 INTERACTIVE_SELECTOR + findInteractiveAncestor（不放宽、不收紧），
# 以判定扩展 onClick 对每个点击会 emit 还是 drop。
JS_SETUP = r"""
const INTERACTIVE_SELECTOR = [
  'a[href]','button','input','select','textarea','summary','label','[contenteditable]',
  '[role="button"]','[role="link"]','[role="textbox"]','[role="menuitem"]','[role="tab"]',
  '[role="checkbox"]','[role="radio"]','[role="switch"]','[role="option"]',
].join(',');
function xp(el){
  if(!el||el.nodeType!==1) return null;
  const p=[]; let c=el;
  while(c&&c.nodeType===1&&c!==document.documentElement){
    let i=1,s=c.previousElementSibling;
    while(s){ if(s.tagName===c.tagName)i++; s=s.previousElementSibling; }
    p.unshift(c.tagName.toLowerCase()+(i>1?`[${i}]`:'')); c=c.parentElement;
  }
  return '/'+p.join('/');
}
// 与 action-recorder.ts:findInteractiveAncestor 完全一致（含 cur.onclick DOM 属性 + 仅 DIV 查 cursor:pointer + data-tw-jsclick 标记）
function findInteractiveAncestor(el){
  let cur = el;
  while (cur && cur !== document.body) {
    try {
      if (cur.matches(INTERACTIVE_SELECTOR)) return cur;
      if (cur.tagName === 'DIV' && window.getComputedStyle(cur).cursor === 'pointer') return cur;
      if (cur.onclick || cur.getAttribute('onclick') || cur.getAttribute('onmousedown')) return cur;
      if (cur.hasAttribute && cur.hasAttribute('data-tw-jsclick')) return cur;
    } catch(e){}
    cur = cur.parentElement;
  }
  return null;
}
function desc(el){ return el ? {
  tag:el.tagName.toLowerCase(), class:(typeof el.className==='string'?el.className:'').slice(0,70),
  role:el.getAttribute('role')||'', text:(el.textContent||'').replace(/\s+/g,' ').trim().slice(0,30),
  cursor:window.getComputedStyle(el).cursor, xpath:xp(el),
  jsclick: !!(el.hasAttribute && el.hasAttribute('data-tw-jsclick')),
} : null; }

// 1) 静态盘点疑似切换 tab 元素
const items = [];
function add(el, why){
  if(!el||el.nodeType!==1) return;
  if(items.some(it=>it.el===el)) return;
  items.push({el, why});
}
document.querySelectorAll('[role="tab"]').forEach(el=>add(el,'role=tab'));
document.querySelectorAll('*').forEach(el=>{
  if(el.children.length>3) return;
  const t=(el.textContent||'').replace(/\s+/g,' ').trim();
  if(t.length>0 && t.length<14 && /横版|竖版|横|竖|截帧|裁剪|切换|封面|上传|视频封面/.test(t)) add(el,'text');
});
document.querySelectorAll('[class*="tab" i],[class*="switch" i],[class*="cover" i]').forEach(el=>{
  if(el.children.length<=4) add(el,'class');
});
const staticReport = items.slice(0, 40).map(({el, why})=>{
  const r = el.getBoundingClientRect();
  const chain = findInteractiveAncestor(el) ? null : (()=>{
    const ch=[]; let x=el.parentElement, n=0;
    while(x && n<10 && x!==document.body){
      const cs=window.getComputedStyle(x).cursor;
      ch.push(`<${x.tagName.toLowerCase()}> class="${(typeof x.className==='string'?x.className:'').slice(0,30)}" cur=${cs} role=${x.getAttribute('role')||''} onc=${(x.onclick||x.getAttribute('onclick'))?'Y':'n'} mdown=${x.getAttribute('onmousedown')?'Y':'n'}`);
      x=x.parentElement; n++;
    }
    return ch;
  })();
  return { why, self: desc(el),
    rect:[Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)],
    captures: !!findInteractiveAncestor(el),
    ancestor: desc(findInteractiveAncestor(el)),
    chain };
});

// 2) 装 capture 阶段 click 监听（复刻 onClick 决策）——只观察、不改动页面
if(!window.__twClickProbe){
  window.__twClickProbe = [];
  window.__twClickProbeN = 0;
  window.addEventListener('click', (e)=>{
    const raw = (e.composedPath&&e.composedPath()[0]) || e.target;
    const rec = {ts: Date.now()};
    if(!raw || raw.nodeType!==1){ rec.dropped='non-element'; window.__twClickProbe.push(rec); return; }
    rec.raw = desc(raw);
    if(raw.tagName==='INPUT' && (raw.getAttribute('type')||'').toLowerCase()==='file'){
      rec.dropped='file-input click (onClick L183 skip)'; window.__twClickProbe.push(rec); return;
    }
    const anc = findInteractiveAncestor(raw);
    if(!anc) rec.dropped='findInteractiveAncestor=null → onClick return, NO emit (丢!)';
    else rec.emitted = desc(anc);
    window.__twClickProbe.push(rec);
  }, {capture:true, passive:true});
}
return {url: location.href, staticReport};
"""


async def main():
	poll_s = int(sys.argv[1]) if len(sys.argv) > 1 else 30
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
	print(f"✓ 连上 target={browser.current_target_id}")

	val, exc = await eval_js(browser, sid, JS_SETUP)
	if exc:
		await browser.stop(); print("探针失败:", exc); sys.exit(1)

	print(f"\nURL: {val['url']}")
	# 只看 tab/切换 相关候选（封面 slot tab 等），减少噪声
	tabish = [c for c in val["staticReport"]
		if c["why"] == "role=tab"
		or "封面（" in (c["self"]["text"] or "")
		or "推荐封面" in (c["self"]["text"] or "")
		or "空间封面" in (c["self"]["text"] or "")
		or "tab" in (c["self"]["class"] or "").lower()]
	print(f"\n=== 静态：切换 tab 候选（{len(tabish)} 个；captures=False = onClick 会丢该 click）===")
	for i, c in enumerate(tabish):
		flag = "✗ 丢" if not c["captures"] else "✓ 录"
		s = c["self"]
		print(f"  [{i}] {flag}  why={c['why']}  rect={c['rect']}")
		print(f"       self   <{s['tag']}> class={s['class']!r} role={s['role']!r} cursor={s['cursor']!r} jsclick={s['jsclick']} text={s['text']!r}")
		if c["captures"]:
			a = c["ancestor"]
			print(f"       anc    <{a['tag']}> class={a['class']!r} role={a['role']!r} cursor={a['cursor']!r} jsclick={a['jsclick']}")
		else:
			print(f"       anc    null —— 祖先链（tag/class cur role onc mdown）:")
			for ln in (c.get("chain") or []):
				print(f"              ↑ {ln}")

	print(f"\n=== 动态：现在请点击「切换封面 tab」(横/竖) —— 轮询 {poll_s}s ===")
	seen = 0
	for _ in range(poll_s):
		await asyncio.sleep(1)
		arr, e2 = await eval_js(browser, sid, "return window.__twClickProbe;")
		if e2 or not isinstance(arr, list):
			continue
		while seen < len(arr):
			rec = arr[seen]; seen += 1
			if "dropped" in rec:
				print(f"  [click] ✗ DROPPED: {rec['dropped']}")
				if rec.get("raw"):
					rr = rec["raw"]; print(f"           raw <{rr['tag']}> class={rr['class']!r} text={rr['text']!r} xpath={rr['xpath']}")
			else:
				em = rec.get("emitted", {})
				print(f"  [click] ✓ EMITTED → anc <{em.get('tag')}> class={em.get('class')!r} role={em.get('role')!r} text={em.get('text')!r}")
				print(f"           xpath={em.get('xpath')}")
	await browser.stop()
	print("\n✓ 探针结束。看上面你的切换-tab click 是 ✓EMITTED 还是 ✗DROPPED。")


if __name__ == "__main__":
	asyncio.run(main())
