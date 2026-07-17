"""dump 封面区 DOM 结构 + React 事件 handler，找「打开编辑器」的真正触发器。

读 element.__reactProps$ 找 onClick/onMouseDown 等 React 绑定，确认 cover-Jg3T4p / filter-k_CjvJ
及子元素上谁绑了打开编辑器的事件。

用法：Chrome 9222 停在发布页，uv run python examples/debug_cover_struct.py
"""

import asyncio
import json
import sys

sys.path.insert(0, f"{__file__}/../src")

from tree_walker.browser.session import BrowserSession
from tree_walker.config import load_settings
from tree_walker.recorder.recorder import select_http_target


async def eval_js(browser, sid, js):
	r = await browser.client.send.Runtime.evaluate(
		{"expression": f"(()=>{{\n{js}\n}})()", "returnByValue": True}, session_id=sid,
	)
	res = r.get("result", {})
	exc = r.get("exceptionDetails") or res.get("exceptionDetails")
	if exc:
		return None, str(exc)
	return res.get("value"), None


JS = """
function reactOn(el){
  if(!el||el.nodeType!==1)return null;
  const k=Object.keys(el).find(k=>k.startsWith('__reactProps$'));
  if(!k)return null;
  const p=el[k]||{};
  return Object.keys(p).filter(x=>/^on/i.test(x));
}
function cls(el){return (el&&typeof el.className==='string')?el.className.slice(0,50):'';}
const cover=document.querySelector('.cover-Jg3T4p');
const filter=document.querySelector('.filter-k_CjvJ');
const out={};
if(cover){
  out.cover={class:cls(cover),cursor:getComputedStyle(cover).cursor,reactOn:reactOn(cover),
    children:[...cover.children].map(c=>({tag:c.tagName.toLowerCase(),class:cls(c),cursor:getComputedStyle(c).cursor,reactOn:reactOn(c)}))};
}
if(filter){
  out.filter={class:cls(filter),cursor:getComputedStyle(filter).cursor,reactOn:reactOn(filter),
    parentClass:cls(filter.parentElement),parentReactOn:reactOn(filter.parentElement)};
}
// 整个封面区域所有 cursor:pointer 元素的 React handler
const region=cover||filter;
if(region){
  const all=[region,...region.querySelectorAll('*')].filter(el=>getComputedStyle(el).cursor==='pointer'||reactOn(el));
  out.pointerAndReact=all.slice(0,15).map(el=>({tag:el.tagName.toLowerCase(),class:cls(el),cursor:getComputedStyle(el).cursor,reactOn:reactOn(el)}));
}
return out;
"""


async def main():
	settings = load_settings()
	browser = BrowserSession(settings.browser)
	await browser.start()
	resp = await browser.client.send.Target.getTargets({})
	tid = select_http_target(resp.get("targetInfos", []), None)
	if tid and tid != browser.current_target_id:
		await browser.switch_tab(tid)
	sid = browser.current_session_id

	val, exc = await eval_js(browser, sid, JS)
	if exc:
		print("dump 失败:", exc)
		await browser.stop()
		return
	print(json.dumps(val, indent=2, ensure_ascii=False))
	await browser.stop()


if __name__ == "__main__":
	asyncio.run(main())
