"""诊断「选择封面」click 录制 locate 失败：findInteractiveAncestor 返回谁、在不在 selector_map。

回答三个问题（定位 step 10 click interacted=None 的根因）：
  1. 用户点封面区中心时，composedPath[0]（topElement）是谁？
  2. 复刻扩展 findInteractiveAncestor 的规则向上找，停在哪一层？是不是蒙层 filter-k_CjvJ
     而非真触发器 cover-Jg3T4p？
  3. findInteractiveAncestor 的返回值、cover-Jg3T4p 各自在不在后端 selector_map？

用法：Chrome 9222 停在「发布页」（已上传视频、能看到「选择封面」区域的编辑页），
      uv run python examples/debug_cover_click_target.py
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


# 复刻 action-recorder.ts 的 INTERACTIVE_SELECTOR + findInteractiveAncestor 规则，
# 并 dump 从 topElement 到 cover-Jg3T4p 的祖先链（tag/class/cursor/onclick属性/React onClick）。
JS = r"""
const INTERACTIVE = 'a[href],button,input,select,textarea,summary,label,[contenteditable],[role="button"],[role="link"],[role="textbox"],[role="menuitem"],[role="tab"],[role="checkbox"],[role="radio"],[role="switch"],[role="option"]';
function cls(el){return (el&&typeof el.className==='string')?el.className.slice(0,60):'';}
function reactOn(el){
  if(!el||el.nodeType!==1)return [];
  const k=Object.keys(el).find(k=>k.startsWith('__reactProps$'));
  if(!k)return [];
  const p=el[k]||{};
  return Object.keys(p).filter(x=>/^on/i.test(x));
}
function xp(el){
  if(!el||el.nodeType!==1)return '';
  const segs=[];
  let cur=el;
  while(cur&&cur.nodeType===1&&cur!==document.documentElement){
    const tag=cur.tagName.toLowerCase();
    const sib=[...cur.parentElement?cur.parentElement.children:[]].filter(x=>x.tagName===cur.tagName);
    const idx=sib.length>1?`[${sib.indexOf(cur)+1}]`:'';
    segs.unshift(`${tag}${idx}`);
    cur=cur.parentElement;
  }
  return 'html/'+segs.join('/');
}
// 复刻 findInteractiveAncestor（与 action-recorder.ts 一致：INTERACTIVE → cursor:pointer 限div → onclick属性）
function findAnc(el){
  let cur=el;
  while(cur&&cur!==document.body){
    try{
      if(cur.matches(INTERACTIVE))return cur;
      if(cur.tagName==='DIV'&&getComputedStyle(cur).cursor==='pointer')return cur;
      if(cur.onclick||cur.getAttribute('onclick')||cur.getAttribute('onmousedown'))return cur;
    }catch(e){}
    cur=cur.parentElement;
  }
  return el;
}
const covers=[...document.querySelectorAll('.cover-Jg3T4p')];
const out={coverCount:covers.length,covers:[]};
for(const cover of covers){
  const r=cover.getBoundingClientRect();
  const cx=r.left+r.width/2, cy=r.top+r.height/2;
  const top=document.elementFromPoint(cx,cy);
  const anc=findAnc(top);
  // 从 topElement 往上到 cover 的祖先链
  const chain=[];
  let c=top;
  for(let i=0;i<10&&c&&c!==document.body;i++){
    chain.push({tag:c.tagName.toLowerCase(),class:cls(c),cursor:getComputedStyle(c).cursor,
      onclickAttr:!!(c.getAttribute&&c.getAttribute('onclick')),reactOn:reactOn(c)});
    if(c===cover)break;
    c=c.parentElement;
  }
  out.covers.push({
    coverClass:cls(cover),coverReactOn:reactOn(cover),coverXpath:xp(cover),
    topElement:{tag:top?top.tagName.toLowerCase():null,class:cls(top)},
    ancestorResult:{tag:anc?anc.tagName.toLowerCase():null,class:cls(anc),
      isCover:anc===cover,reactOn:reactOn(anc),xpath:xp(anc)},
    chain,
  });
}
return out;
"""


async def main():
	settings = load_settings()
	if not settings.browser.ws_url:
		print("✗ Chrome 未以 --remote-debugging-port=9222 启动")
		return
	browser = BrowserSession(settings.browser)
	await browser.start()
	resp = await browser.client.send.Target.getTargets({})
	tid = select_http_target(resp.get("targetInfos", []), None)
	if tid and tid != browser.current_target_id:
		await browser.switch_tab(tid)
	sid = browser.current_session_id

	val, exc = await eval_js(browser, sid, JS)
	if exc:
		print("JS 执行失败:", exc)
		await browser.stop()
		return

	# 后端 selector_map：列出封面区附近的 cursor:pointer DIV，便于比对
	state = await browser.get_state(include_screenshot=False)
	selector_map = state.dom_state.selector_map if state and state.dom_state else {}
	cover_nodes = []
	for idx, node in selector_map.items():
		attrs = getattr(node, "attributes", None) or {}
		cl = attrs.get("class", "") or ""
		name = (getattr(node, "node_name", "") or "").upper()
		if "cover" in cl.lower() or "filter-k_CjvJ".lower() in cl.lower() or "upload-drag" in cl.lower():
			cover_nodes.append({"index": idx, "tag": name, "class": cl[:60],
			                    "xpath": getattr(node, "x_path", "")})

	print("=== 封面点击诊断 ===")
	print("\n[selector_map 中封面区元素]（cursor:pointer div 是否被后端收录）")
	for n in cover_nodes:
		print(f"  idx={n['index']:<6} {n['tag']:<6} class={n['class']}")
		print(f"      xpath={n['xpath']}")

	print(f"\n[页面 .cover-Jg3T4p 数量] {val['coverCount']}")
	for i, c in enumerate(val["covers"]):
		print(f"\n--- 封面区 #{i} ---")
		print(f"  cover-Jg3T4p: class={c['coverClass']} reactOn={c['coverReactOn']}")
		print(f"      xpath={c['coverXpath']}")
		print(f"  点击中心 topElement: <{c['topElement']['tag']}> class={c['topElement']['class']}")
		ar = c["ancestorResult"]
		mark = "✓ 命中 cover-Jg3T4p" if ar["isCover"] else "✗ 停在内部（非真触发器）"
		print(f"  findInteractiveAncestor 返回: <{ar['tag']}> class={ar['class']} reactOn={ar['reactOn']}  {mark}")
		print(f"      xpath={ar['xpath']}")
		print(f"  祖先链（topElement→cover，看为何停在中间）:")
		for j, link in enumerate(c["chain"]):
			print(f"    [{j}] <{link['tag']}> class={link['class']} cursor={link['cursor']} reactOn={link['reactOn']}")

	await browser.stop()


if __name__ == "__main__":
	asyncio.run(main())
