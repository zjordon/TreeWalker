"""自动点击探针：程序化点「上传视频」按钮，用三套 hook 看它怎么关联 file input。

非交互：自己定位按钮 → .click() → 读 hook。程序化点击能触发 React onClick handler，
hook（prototype.click / MutationObserver）在 handler 里同步触发，不依赖原生选文件器。

判定（数据驱动）：
  - __twFileClicks 命中的 input 是静态 video input   → 按钮用静态 input（用户理论）
  - __twAdded 有新建 + __twFileClicks 命中新 input   → 按钮动态新建（动态理论）

用法：uv run python examples/debug_upload_click.py
"""

import asyncio
import json
import sys

sys.path.insert(0, f"{__file__}/../src")

from tree_walker.browser.session import BrowserSession
from tree_walker.config import load_settings
from tree_walker.recorder.recorder import select_http_target


async def eval_js(browser, sid, js, await_promise=False):
	r = await browser.client.send.Runtime.evaluate(
		{"expression": f"(() => {{\n{js}\n}})()", "returnByValue": True, "awaitPromise": await_promise},
		session_id=sid,
	)
	res = r.get("result", {})
	exc = r.get("exceptionDetails") or res.get("exceptionDetails")
	if exc:
		desc = exc.get("exception", {}).get("description", str(exc)) if isinstance(exc, dict) else str(exc)
		return None, f"JS异常: {desc}"
	return res.get("value"), None


# 1. 注入 hook（含存量 input 快照，用于事后比对）
JS_INJECT = r"""
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
window.getXPath = xp;
window.__twStaticBefore = Array.from(document.querySelectorAll('input[type="file"]')).map(i=>({
  accept:i.accept||'', class:cls(i), id:i.id, xpath:xp(i),
}));
window.__twFileClicks=[]; window.__twChanges=[]; window.__twAdded=[]; window.__twErr=[];
const origClick = HTMLInputElement.prototype.click;
HTMLInputElement.prototype.click = function(){
  if(this.tagName==='INPUT' && this.type==='file'){
    const r=this.getBoundingClientRect();
    try{ window.__twFileClicks.push({
      accept:this.accept||'', class:cls(this), id:this.id, xpath:xp(this),
      size:Math.round(r.width)+'x'+Math.round(r.height), inDom:document.body.contains(this), ts:Date.now(),
    }); }catch(e){ window.__twErr.push('clickHook:'+e.message); }
  }
  return origClick.call(this);
};
const mo = new MutationObserver(muts=>{
  for(const m of muts) for(const nd of m.addedNodes){
    const scan=node=>{
      if(node&&node.tagName==='INPUT'&&node.type==='file'){
        try{ window.__twAdded.push({accept:node.accept||'',class:cls(node),id:node.id,xpath:xp(node),ts:Date.now()}); }catch(e){ window.__twErr.push('mo:'+e.message); }
      }
      if(node&&node.querySelectorAll){ node.querySelectorAll('input[type="file"]').forEach(scan); }
    };
    scan(nd);
  }
});
mo.observe(document.documentElement,{childList:true,subtree:true});
document.addEventListener('change',e=>{
  const t=e.target;
  if(t&&t.tagName==='INPUT'&&t.type==='file'){
    const f=t.files&&t.files[0];
    try{ window.__twChanges.push({accept:t.accept||'',class:cls(t),id:t.id,xpath:xp(t),files:t.files?t.files.length:0,fileName:f?f.name:null}); }catch(e){ window.__twErr.push('change:'+e.message); }
  }
},true);
return {staticBefore: window.__twStaticBefore};
"""

# 2. 定位「上传视频」按钮并 .click()
JS_CLICK = r"""
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
// 定位：含「上传视频」文本、cursor:pointer 的 <button>（或最接近的可点元素）
const cands = Array.from(document.querySelectorAll('button, [role=button], a, div')).filter(el=>{
  const t=(el.textContent||'').trim();
  return t==='上传视频' && getComputedStyle(el).cursor==='pointer';
});
let btn = cands.find(el=>el.tagName==='BUTTON') || cands[0];
if(!btn) return {clicked:false, reason:'未找到「上传视频」按钮'};
window.__twClickedTarget = btn.tagName.toLowerCase()+'.'+cls(btn)+' xpath='+xp(btn);
try{ btn.click(); }catch(e){ return {clicked:false, reason:'click抛错:'+e.message}; }
return {clicked:true, target:window.__twClickedTarget};
"""

# 3. 读 hook 结果
JS_READ = r"""
return JSON.stringify({
  staticBefore: window.__twStaticBefore||[],
  added: window.__twAdded||[],
  clicks: window.__twFileClicks||[],
  changes: window.__twChanges||[],
  err: window.__twErr||[],
  clickedTarget: window.__twClickedTarget||null,
});
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

	# 1. 注入 hook
	val, exc = await eval_js(browser, sid, JS_INJECT)
	if exc:
		print("注入失败:", exc); await browser.stop(); return
	print(f"\n✓ hook 已注入。点击前存量 file input：{len(val['staticBefore'])} 个")
	for s in val["staticBefore"]:
		print(f"   · accept={s['accept'][:40]!r} class={s['class']!r} id={s['id']!r}")
		print(f"     xpath={s['xpath']}")

	# 2. 程序化点按钮
	val, exc = await eval_js(browser, sid, JS_CLICK)
	if exc:
		print("点击失败:", exc); await browser.stop(); return
	if not val.get("clicked"):
		print(f"\n✗ {val.get('reason')}"); await browser.stop(); return
	print(f"\n✓ 已程序化点击按钮：{val['target']}")

	# 3. 等一下（handler 可能把 input.click 放进微任务/setTimeout）
	await asyncio.sleep(0.6)

	# 4. 读结果
	val, exc = await eval_js(browser, sid, JS_READ)
	await browser.stop()
	if exc:
		print("读结果失败:", exc); return
	data = json.loads(val)

	if data["err"]:
		print("\n[hook 错误]", data["err"])

	print("\n=== 捕获结果 ===")
	print(f"[MutationObserver] 点击后新建 file input：{len(data['added'])} 个")
	for a in data["added"]:
		print(f"  ✨ NEW accept={a['accept']!r} class={a['class']!r} id={a['id']!r}")
		print(f"        xpath={a['xpath']}")

	print(f"\n[prototype.click] 被程序化 .click() 的 file input：{len(data['clicks'])} 次")
	for c in data["clicks"]:
		print(f"  → .click() accept={c['accept'][:40]!r} class={c['class']!r} id={c['id']!r} size={c['size']} inDom={c['inDom']}")
		print(f"        xpath={c['xpath']}")

	print(f"\n[change] 触发 change：{len(data['changes'])} 个（程序化点击无真实文件，通常为 0）")
	for c in data["changes"]:
		print(f"  CHANGE accept={c['accept']!r} class={c['class']!r} files={c['files']} {c['fileName']!r}")

	# 5. 判定
	print("\n" + "=" * 60)
	static_xpaths = {s["xpath"] for s in data["staticBefore"]}
	clicks = data["clicks"]
	added = data["added"]
	if not clicks:
		print("▶▶ 按钮的 .click() hook 没捕到任何 file input.click —— 可能 handler 用了")
		print("   别的方式打开选文件器（如 <label> 包裹、或 CDP 之外的途径），或点击被用户手势校验拦了。")
		if added:
			print(f"   但 MutationObserver 捕到 {len(added)} 个新建 input：")
			for a in added:
				print(f"     ✨ {a['class']!r} accept={a['accept']!r}")
	else:
		clicked = clicks[-1]
		is_static = clicked["xpath"] in static_xpaths
		is_added = any(a["xpath"] == clicked["xpath"] for a in added)
		print(f".click() 命中的 input：class={clicked['class']!r} accept={clicked['accept'][:30]!r} inDom={clicked['inDom']}")
		print(f"  它在【点击前存量清单】里? → {'是 ✅' if is_static else '否'}")
		print(f"  它在【MutationObserver 新建】里? → {'是 ✅' if is_added else '否'}")
		print()
		if is_static and not is_added:
			print("▶▶ 判定：「上传视频」按钮用的就是【HTML 静态 file input】（accept=video/*）。")
			print("   程序化 .click() 直接打在静态 input 上，没有新建。")
			print("   ★ 用户判断成立：静态 input 才是真正被用的那个。")
			print()
			print("   那么扩展手工录制录到 upload-btn-input-UY_qeY(image) 的原因，")
			print("   不在「按钮动态新建」，而需重新排查（可能录制时是另一个页面状态/另一个按钮）：")
			print("     · upload-btn-input-UY_qeY 是不是来自【封面编辑器里的图片上传】，而非视频上传按钮？")
			print("     · 录制 douyin_redesign3.json 时点的是哪个按钮、在哪个页面？")
		elif is_added and not is_static:
			print("▶▶ 判定：按钮【动态新建】了临时 file input 来触发上传（动态理论成立）。")
			print(f"   新建 input：class={clicked['class']!r} accept={clicked['accept']!r}")
			print("   扩展 onFileChange 捕获的正是这个动态 input。")
		else:
			print("▶▶ 判定：xpath 既匹配存量又匹配新建（罕见），按 class/accept 人工核对：")
			print(f"   clicked class={clicked['class']!r} accept={clicked['accept']!r}")


if __name__ == "__main__":
	asyncio.run(main())
