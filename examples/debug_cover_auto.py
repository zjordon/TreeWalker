"""自动诊断：CDP 点击「选择封面」看是否触发 click 事件 + 是否打开编辑器。

不需用户操作：dump DOM → 注入 capture 监听 → CDP click_element 点封面 → 读捕获 log +
对比 selector_map 变化（编辑器是否打开）。

用法：Chrome 9222 停在发布页，uv run python examples/debug_cover_auto.py
"""

import asyncio
import json
import sys

sys.path.insert(0, f"{__file__}/../src")

from dom_snapshot import build_dom_state
from tree_walker.browser.session import BrowserSession
from tree_walker.config import load_settings
from tree_walker.recorder.recorder import select_http_target

COVER_KEYWORDS = ['cover-jg3t4p', 'covercontrol', 'cover-tip', 'choosecover']


async def eval_js(browser, sid, js):
	r = await browser.client.send.Runtime.evaluate(
		{"expression": f"(() => {{\n{js}\n}})()", "returnByValue": True},
		session_id=sid,
	)
	res = r.get("result", {})
	exc = r.get("exceptionDetails") or res.get("exceptionDetails")
	if exc:
		return None, str(exc)
	return res.get("value"), None


JS_INJECT = """
window.__twClicks = [];
function cls(el){return (el&&typeof el.className==='string')?el.className.slice(0,80):'';}
function xp(el){
  if(!el||el.nodeType!==1)return null;const p=[];let c=el;
  while(c&&c.nodeType===1&&c!==document.documentElement){
    let i=1,s=c.previousElementSibling;while(s){if(s.tagName===c.tagName)i++;s=s.previousElementSibling;}
    p.unshift(c.tagName.toLowerCase()+(i>1?`[${i}]`:''));c=c.parentElement;}
  return '/'+p.join('/');
}
function rec(e){const t=e.target,cp=e.composedPath();
  window.__twClicks.push({type:e.type,
    target:t&&t.tagName?(t.tagName.toLowerCase()+(cls(t)?'.'+cls(t).split(' ').join('.'):'')):String(t),
    xpath:t&&t.nodeType===1?xp(t):null,
    cursor:t&&t.nodeType===1?getComputedStyle(t).cursor:null,
    cpTop:cp.slice(0,5).map(n=>n&&n.tagName?n.tagName.toLowerCase():String(n)).join('>')});}
['click','mousedown','mouseup','pointerdown','pointerup'].forEach(t=>window.addEventListener(t,rec,true));
return 'installed';
"""

JS_DUMP = """
const all=Array.from(document.querySelectorAll('*'));
const m=all.filter(el=>{if(el.children.length>4)return false;
  const t=(el.textContent||'').trim(),c=typeof el.className==='string'?el.className:'';
  return (t&&(t.includes('选择封面')||t.includes('更换封面'))&&t.length<20)||/cover-jg3t4p/i.test(c);});
return m.slice(0,10).map(el=>{const r=el.getBoundingClientRect();
  let pa=null,c=el.parentElement,n=0;
  while(c&&n<6){if(getComputedStyle(c).cursor==='pointer'){pa=c.tagName.toLowerCase()+'.'+(typeof c.className==='string'?c.className.slice(0,50):'');break;}c=c.parentElement;n++;}
  return{tag:el.tagName.toLowerCase(),class:cls(el),text:(el.textContent||'').trim().slice(0,20),
    cursor:getComputedStyle(el).cursor,onclick:!!(el.onclick||el.getAttribute('onclick')),
    size:Math.round(r.width)+'x'+Math.round(r.height),pointerAncestor:pa};});
"""

JS_READ = "return JSON.stringify(window.__twClicks||[]);"


async def main():
	settings = load_settings()
	browser = BrowserSession(settings.browser)
	await browser.start()
	resp = await browser.client.send.Target.getTargets({})
	tid = select_http_target(resp.get("targetInfos", []), None)
	if tid and tid != browser.current_target_id:
		await browser.switch_tab(tid)
	sid = browser.current_session_id
	print(f"✓ 连页面 target={browser.current_target_id}")

	# 1. dump DOM
	val, _ = await eval_js(browser, sid, JS_DUMP)
	print("\n=== 「选择封面」相关 DOM ===")
	covers = val or []
	for i, el in enumerate(covers):
		print(f"[{i}] <{el['tag']}> class={el['class']!r} text={el['text']!r} cursor={el['cursor']} onclick={el['onclick']} size={el['size']} pointerAncestor={el['pointerAncestor']}")

	# 2. 注入监听
	_, exc = await eval_js(browser, sid, JS_INJECT)
	print("\n注入监听:", exc or "✓ installed")
	if exc:
		await browser.stop(); return

	# 3. build_dom_state 找封面元素（selector_map）
	dom_state, _ = await build_dom_state(
		client=browser.client, session_id=sid,
		config=browser._dom_collection_config, previous_selector_map=None,
	)
	before_count = len(dom_state.selector_map)
	cover_entry = None
	for bid, entry in dom_state.selector_map.items():
		cls = (entry.attributes or {}).get('class', '').lower()
		if any(kw in cls for kw in COVER_KEYWORDS):
			cover_entry = (bid, entry)
			break
	if not cover_entry:
		print("\n❌ selector_map 没找到封面元素（cover-jg3t4p 等）")
		await browser.stop(); return
	bid, entry = cover_entry
	print(f"\n✓ selector_map 封面元素 [{bid}] <{entry.tag_name}> class={(entry.attributes or {}).get('class', '')!r}")
	print(f"  bounds=({entry.x},{entry.y}) {entry.width}x{entry.height} visible={entry.is_visible}")

	# 4. CDP click_element 点封面
	print(f"\n>>> CDP click_element 点击封面 [{bid}]...")
	try:
		ok = await browser.click_element(entry.backend_node_id)
		print(f"  click_element 返回: {ok}")
	except Exception as e:
		print(f"  click_element 异常: {e}")
	await asyncio.sleep(2.0)

	# 5. 读捕获 log
	val, _ = await eval_js(browser, sid, JS_READ)
	logs = json.loads(val) if val else []
	print(f"\n=== 捕获 {len(logs)} 条事件 ===")
	for ev in logs[-15:]:
		print(f"  [{ev['type']}] target={ev['target']} cursor={ev['cursor']}")
		print(f"       xpath={ev['xpath']}")
		print(f"       cpTop={ev['cpTop']}")

	# 6. 编辑器是否打开（selector_map 变化）
	dom_state2, _ = await build_dom_state(
		client=browser.client, session_id=sid,
		config=browser._dom_collection_config, previous_selector_map=None,
	)
	diff = len(dom_state2.selector_map) - before_count
	print(f"\n=== 编辑器是否打开 ===")
	print(f"  selector_map: {before_count} → {len(dom_state2.selector_map)} (diff={diff})")
	clicks = [e for e in logs if e["type"] == "click"]
	mousedowns = [e for e in logs if e["type"] == "mousedown"]
	print(f"  click 事件: {len(clicks)}；mousedown 事件: {len(mousedowns)}")

	print("\n=== 结论 ===")
	if not clicks and mousedowns:
		print("⚠️ 点封面只触发 mousedown/pointerdown，没派发 click 事件——抖音用 mousedown 触发！")
		print("   扩展 onClick（监听 click）收不到。需补 mousedown 监听。")
	elif clicks:
		c = clicks[-1]
		print(f"✅ 点封面触发了 click（target={c['target']} cursor={c['cursor']}）")
		if diff > 50:
			print(f"   且编辑器打开了（selector_map +{diff}）→ CDP click 能打开编辑器。")
			print("   → 用户真实点击也应触发 click。扩展漏录在 onClick 内部逻辑（findInteractiveAncestor/A2/locate）。")
		else:
			print(f"   但编辑器没打开（selector_map diff={diff}）→ CDP click 没触发 React 打开。可能点位偏或 React 需真实手势。")
	else:
		print("❌ 点封面没捕获任何事件——CDP 点击可能没命中或事件被拦截。")

	await browser.stop()


if __name__ == "__main__":
	asyncio.run(main())
