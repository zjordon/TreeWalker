"""只读探针：当前上传界面有哪些 file input、上传按钮长啥样（零副作用，不点击）。

连真实 Chrome，盘点：
  - 所有 input[type=file]：accept/class/xpath/size/pointer祖先/label关联
  - 「上传」按钮候选：cursor/onclick/text/xpath
  - 是否有 <label for> 关联（label 点击会触发 input.click）

用法：uv run python examples/debug_upload_probe.py
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
		desc = exc.get("exception", {}).get("description", str(exc)) if isinstance(exc, dict) else str(exc)
		return None, f"JS异常: {desc}"
	return res.get("value"), None


JS = r"""
function cls(el){ return (el && typeof el.className==='string') ? el.className.slice(0,80) : ''; }
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
const inputs = Array.from(document.querySelectorAll('input[type="file"]'));
const fileInv = inputs.map(inp=>{
  const r = inp.getBoundingClientRect();
  let pa=null, c=inp.parentElement, n=0;
  while(c&&n<8){
    const cs=getComputedStyle(c);
    if(cs.cursor==='pointer'||c.onclick||c.getAttribute('role')==='button'){
      pa = c.tagName.toLowerCase()+'.'+cls(c)+' text="'+(c.textContent||'').trim().slice(0,24)+'"'; break;
    }
    c=c.parentElement; n++;
  }
  let labelFor=null;
  if(inp.id){ try{ const lbl=document.querySelector('label[for="'+CSS.escape(inp.id)+'"]'); if(lbl) labelFor=cls(lbl)+' "'+(lbl.textContent||'').trim().slice(0,20)+'"'; }catch(e){} }
  let wrapLabel=null; let p2=inp.parentElement;
  while(p2&&p2!==document.body){ if(p2.tagName==='LABEL'){wrapLabel=cls(p2);break;} p2=p2.parentElement; }
  return {
    accept: inp.accept||'', class: cls(inp), id: inp.id, name: inp.name||'',
    xpath: xp(inp), size: Math.round(r.width)+'x'+Math.round(r.height),
    inDom: document.body.contains(inp), pointerAncestor: pa, labelFor: labelFor, wrapLabel: wrapLabel,
  };
});
// 所有 label 元素（for 关联 input）
const labels = Array.from(document.querySelectorAll('label')).map(l=>{
  const r=l.getBoundingClientRect();
  return {forAttr:l.getAttribute('for'), class:cls(l), text:(l.textContent||'').trim().slice(0,24),
          cursor:getComputedStyle(l).cursor, size:Math.round(r.width)+'x'+Math.round(r.height), xpath:xp(l)};
}).filter(l=>l.forAttr||l.cursor==='pointer'||/upload|cover|封面|上传/i.test(l.text+ ' '+l.class));
// 上传按钮候选
const btns = Array.from(document.querySelectorAll('*')).filter(el=>{
  if(el.children.length>6) return false;
  const txt=(el.textContent||'').trim(); const c=cls(el);
  return (txt.includes('上传')||/upload/i.test(c)||txt.includes('选择封面')||txt.includes('更换')) && txt.length<30;
}).slice(0,15).map(el=>{
  const r=el.getBoundingClientRect();
  return {tag:el.tagName.toLowerCase(), class:cls(el), text:(el.textContent||'').trim().slice(0,24),
          cursor:getComputedStyle(el).cursor, onclick:!!(el.onclick||el.getAttribute('onclick')),
          size:Math.round(r.width)+'x'+Math.round(r.height), xpath:xp(el)};
});
return {url:location.href, fileInputs:fileInv, labels:labels.slice(0,12), buttons:btns};
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
	print(f"✓ target={browser.current_target_id}")

	val, exc = await eval_js(browser, sid, JS)
	await browser.stop()
	if exc:
		print("探针失败:", exc); sys.exit(1)

	print(f"\nURL: {val['url']}")
	print(f"\n=== 存量 file input：{len(val['fileInputs'])} 个 ===")
	for i, el in enumerate(val["fileInputs"]):
		print(f"  [{i}] accept={el['accept']!r}")
		print(f"      class={el['class']!r}  id={el['id']!r}  name={el['name']!r}  size={el['size']}  inDom={el['inDom']}")
		print(f"      xpath={el['xpath']}")
		print(f"      pointerAncestor={el['pointerAncestor']}")
		print(f"      labelFor={el['labelFor']}  wrapLabel={el['wrapLabel']}")
	if not val["fileInputs"]:
		print("  （0 个 —— 可能 hook 注入前页面上本就没有静态 file input，全靠点击时动态建）")

	print(f"\n=== 关联 label：{len(val['labels'])} 个 ===")
	for l in val["labels"]:
		print(f"  <label for={l['forAttr']!r}> class={l['class']!r} text={l['text']!r} cursor={l['cursor']} {l['size']}")

	print(f"\n=== 上传按钮候选：{len(val['buttons'])} 个 ===")
	for i, el in enumerate(val["buttons"]):
		print(f"  [{i}] <{el['tag']}> class={el['class']!r} text={el['text']!r} cursor={el['cursor']} onclick={el['onclick']} {el['size']}")
		print(f"       xpath={el['xpath']}")


if __name__ == "__main__":
	asyncio.run(main())
