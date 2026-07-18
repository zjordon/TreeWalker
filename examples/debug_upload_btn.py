"""诊断：点「上传」按钮时，到底是怎么关联上 file input 的？

用真实数据回答一个悬而未决的争论：
  - 静态 file input（HTML 里写死那个，如 video/* 的 line 156）被 .click()？
  - 还是按钮 JS 动态新建了一个临时 file input（如 upload-btn-input-xxx, accept=image）？

三种 hook 同时观察，互为佐证：
  1. HTMLInputElement.prototype.click   → 哪个 input 被程序化 .click()（accept/class/xpath）
  2. MutationObserver                   → 有没有「新建」的 input[type=file] 进 DOM
  3. document capture 'change'          → 最终哪个 input 触发 change + 文件名

判定逻辑（不靠猜，看数据）：
  - change 触发的 input 在【点击前存量清单】里      → 静态 input 被用（用户理论成立）
  - change 触发的 input 在【MutationObserver 新建】里 → 动态新建（动态理论成立）

用法（交互式）：
  1. Chrome --remote-debugging-port=9222，停在上传界面（封面/视频上传均可）。
  2. uv run python examples/debug_upload_btn.py
  3. 看存量 file input 清单 + 上传按钮候选，确认点哪个按钮。
  4. 在浏览器点上传按钮 → 选文件 → 回到终端按回车。
  5. 脚本打印三条 hook 的捕获结果 + 明确结论。
"""

import asyncio
import json
import sys

sys.path.insert(0, f"{__file__}/../src")

from tree_walker.browser.session import BrowserSession
from tree_walker.config import load_settings
from tree_walker.recorder.recorder import select_http_target


async def eval_js(browser, sid, js):
	"""Runtime.evaluate + returnByValue，JS 包 IIFE。返回 (value, exc)。"""
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


# ── 点击前：存量 file input 清单 ──────────────────────────────────────
JS_INVENTORY = """
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
const inputs = Array.from(document.querySelectorAll('input[type="file"]'));
return inputs.map(inp=>{
  const r = inp.getBoundingClientRect();
  let pa=null, c=inp.parentElement, n=0;
  while(c&&n<8){
    const cs=getComputedStyle(c);
    if(cs.cursor==='pointer'||c.onclick||c.getAttribute('role')==='button'){
      pa = c.tagName.toLowerCase()+'.'+cls(c)+' text="'+(c.textContent||'').trim().slice(0,24)+'"'; break;
    }
    c=c.parentElement; n++;
  }
  // 这个 input 是不是 <label for> 关联的？（label[for=id] 点击会触发 input.click）
  let labelFor=null;
  if(inp.id){ const lbl=document.querySelector('label[for="'+CSS.escape(inp.id)+'"]'); if(lbl) labelFor=cls(lbl)+' "'+(lbl.textContent||'').trim().slice(0,20)+'"'; }
  // 它有没有被包在 <label> 里？
  let wrapLabel=null;
  let p2=inp.parentElement;
  while(p2&&p2!==document.body){ if(p2.tagName==='LABEL'){wrapLabel=cls(p2);break;} p2=p2.parentElement; }
  return {
    accept: inp.accept || '',
    class: cls(inp), id: inp.id, name: inp.name||'',
    xpath: xp(inp),
    size: Math.round(r.width)+'x'+Math.round(r.height),
    inDom: document.body.contains(inp),
    pointerAncestor: pa,
    labelFor: labelFor, wrapLabel: wrapLabel,
  };
});
"""

# ── 上传按钮候选（帮用户确认点哪个）──────────────────────────────────
JS_FIND_BTN = """
function cls(el){ return (el && typeof el.className==='string') ? el.className.slice(0,60) : ''; }
const all = Array.from(document.querySelectorAll('*'));
return all.filter(el=>{
  if(el.children.length>6) return false;
  const txt=(el.textContent||'').trim();
  const c=cls(el);
  return (txt.includes('上传')||/upload/i.test(c)) && txt.length<30;
}).slice(0,15).map(el=>{
  const r=el.getBoundingClientRect();
  return {
    tag: el.tagName.toLowerCase(), class: cls(el), text: (el.textContent||'').trim().slice(0,24),
    cursor: getComputedStyle(el).cursor, onclick: !!(el.onclick||el.getAttribute('onclick')),
    size: Math.round(r.width)+'x'+Math.round(r.height),
    xpath: window.getXPath(el),
  };
});
"""

# ── 注入三套 hook（必须在用户点击前注入）────────────────────────────
JS_INJECT = """
window.__twFileClicks = [];   // HTMLInputElement.prototype.click 捕获
window.__twChanges = [];      // capture change 捕获
window.__twAdded = [];        // MutationObserver 新建 file input 捕获
window.__twUserClicks = [];   // 用户实际点的元素

// 1. Hook HTMLInputElement.prototype.click —— 捕获「程序化点哪个 file input」
const origClick = HTMLInputElement.prototype.click;
HTMLInputElement.prototype.click = function(){
  if(this.tagName==='INPUT' && this.type==='file'){
    const r=this.getBoundingClientRect();
    window.__twFileClicks.push({
      accept: this.accept||'', class: (typeof this.className==='string'?this.className.slice(0,80):''),
      id: this.id, xpath: window.getXPath(this),
      size: Math.round(r.width)+'x'+Math.round(r.height),
      inDom: document.body.contains(this), ts: Date.now(),
    });
  }
  return origClick.call(this);
};

// 2. MutationObserver —— 捕获「新建的 file input」
const mo = new MutationObserver(muts=>{
  for(const m of muts){
    for(const nd of m.addedNodes){
      const scan=(node)=>{
        if(node && node.tagName==='INPUT' && node.type==='file'){
          window.__twAdded.push({
            accept: node.accept||'', class: (typeof node.className==='string'?node.className.slice(0,80):''),
            id: node.id, xpath: window.getXPath(node), ts: Date.now(),
          });
        }
        if(node && node.querySelectorAll){ node.querySelectorAll('input[type="file"]').forEach(scan); }
      };
      scan(nd);
    }
  }
});
mo.observe(document.documentElement, {childList:true, subtree:true});
window.__twMO = mo;

// 3. capture change —— 捕获「最终哪个 file input 真触发了 change」
document.addEventListener('change', e=>{
  const t=e.target;
  if(t && t.tagName==='INPUT' && t.type==='file'){
    const f = t.files && t.files[0];
    window.__twChanges.push({
      accept: t.accept||'', class: (typeof t.className==='string'?t.className.slice(0,80):''),
      id: t.id, xpath: window.getXPath(t),
      files: t.files?t.files.length:0, fileName: f?f.name:null, ts: Date.now(),
    });
  }
}, true);

// 4. capture 用户点击 —— 看用户实际点了什么（按钮 vs input 本身）
document.addEventListener('click', e=>{
  const t=e.target;
  window.__twUserClicks.push({
    target: t && t.tagName ? t.tagName.toLowerCase()+(t.className&&typeof t.className==='string'?'.'+t.className.split(' ').join('.'):'') : String(t),
    xpath: t && t.nodeType===1 ? window.getXPath(t) : null,
    ts: Date.now(),
  });
}, true);

return 'installed';
"""

JS_READ = """
return JSON.stringify({
  added: window.__twAdded||[],
  clicks: window.__twFileClicks||[],
  changes: window.__twChanges||[],
  user: window.__twUserClicks||[],
});
"""


async def main():
	settings = load_settings()
	if not settings.browser.ws_url:
		print("✗ Chrome 未以 --remote-debugging-port=9222 启动")
		sys.exit(1)

	browser = BrowserSession(settings.browser)
	await browser.start()
	resp = await browser.client.send.Target.getTargets({})
	tid = select_http_target(resp.get("targetInfos", []), None)
	if tid and tid != browser.current_target_id:
		await browser.switch_tab(tid)
	sid = browser.current_session_id
	print(f"✓ 已连页面 target={browser.current_target_id}  sid={sid}")

	# 1. 存量 file input 清单
	val, exc = await eval_js(browser, sid, JS_INVENTORY)
	print("\n=== 点击前 · 存量 file input 清单（HTML 里本就有的）===")
	inventory = []
	if exc:
		print("  dump 失败:", exc)
	elif not val:
		print("  当前页 0 个 file input —— 确认在上传界面。")
	else:
		inventory = val
		for i, el in enumerate(val):
			print(f"  [{i}] accept={el['accept']!r} class={el['class']!r} id={el['id']!r} size={el['size']} inDom={el['inDom']}")
			print(f"       name={el['name']!r}  xpath={el['xpath']}")
			print(f"       pointerAncestor={el['pointerAncestor']}  labelFor={el['labelFor']}  wrapLabel={el['wrapLabel']}")

	# 2. 上传按钮候选
	val, exc = await eval_js(browser, sid, JS_FIND_BTN)
	print("\n=== 上传按钮候选（cursor:pointer / 含「上传」文本）===")
	if exc:
		print("  查找失败:", exc)
	elif not val:
		print("  没找到明显的「上传」按钮候选。")
	else:
		for i, el in enumerate(val):
			print(f"  [{i}] <{el['tag']}> class={el['class']!r} text={el['text']!r} cursor={el['cursor']} onclick={el['onclick']} {el['size']}")
			print(f"       xpath={el['xpath']}")

	# 3. 注入 hook
	_, exc = await eval_js(browser, sid, JS_INJECT)
	if exc:
		print("\n注入 hook 失败:", exc)
		await browser.stop()
		return
	print("\n✓ 已注入三套 hook（.click / MutationObserver / change）+ 用户点击捕获")

	# 4. 等用户操作
	print("\n>>> 现在在浏览器点【上传按钮】→ 选个文件 → 完成后回这里按回车 <<<")
	try:
		input()
	except EOFError:
		pass

	# 5. 读回
	val, exc = await eval_js(browser, sid, JS_READ)
	data = json.loads(val) if not exc and val else {"added": [], "clicks": [], "changes": [], "user": []}

	print("\n=== 捕获结果 ===")
	print(f"[MutationObserver] 新建 file input: {len(data['added'])} 个")
	for a in data["added"]:
		print(f"  ✨ NEW accept={a['accept']!r} class={a['class']!r} id={a['id']!r}")
		print(f"        xpath={a['xpath']}")

	print(f"\n[prototype.click] 被程序化 .click() 的 file input: {len(data['clicks'])} 次")
	for c in data["clicks"]:
		print(f"  → .click() accept={c['accept']!r} class={c['class']!r} id={c['id']!r} size={c['size']} inDom={c['inDom']}")
		print(f"        xpath={c['xpath']}")

	print(f"\n[change] 真正触发 change 的 file input: {len(data['changes'])} 个")
	change_cls = None
	for c in data["changes"]:
		change_cls = c["class"]
		print(f"  ✅ CHANGE accept={c['accept']!r} class={c['class']!r} id={c['id']!r}")
		print(f"        files={c['files']} fileName={c['fileName']!r}")
		print(f"        xpath={c['xpath']}")

	print(f"\n[用户点击] 捕获 {len(data['user'])} 次 click")
	for u in data["user"][-5:]:
		print(f"  · {u['target']}  xpath={u['xpath']}")

	# 6. 结论判定（纯数据驱动）
	print("\n" + "=" * 60)
	print("结论（按 change 触发的 input 归属判定，不靠猜）")
	print("=" * 60)
	if not data["changes"]:
		print("❌ 没捕获到 change —— 文件可能没选成功，或点击在 iframe 里。重试或检查页面。")
		await browser.stop()
		return

	chg = data["changes"][-1]
	in_static = any(
		c["xpath"] == chg["xpath"] and (c.get("accept") or "") == (chg.get("accept") or "")
		for c in inventory
	) if inventory else False
	in_added = any(a["xpath"] == chg["xpath"] for a in data["added"])

	print(f"change 的 input: class={chg['class']!r} accept={chg['accept']!r}")
	print(f"  在点击前存量清单里?  → {'是 ✅（静态 input 被用）' if in_static else '否'}")
	print(f"  在 MutationObserver 新建里? → {'是 ✅（动态新建的 input）' if in_added else '否'}")
	print()
	if in_added and not in_static:
		print("▶▶ 判定：上传按钮【动态新建】了临时 file input 来触发上传。")
		print("   扩展 onFileChange 捕获的正是这个动态 input（accept/class 与静态不同）。")
		print("   这解释了为何手工录制录到 upload-btn-input-xxx 而非 HTML 静态 input。")
	elif in_static and not in_added:
		print("▶▶ 判定：上传按钮用的就是【HTML 静态 file input】。")
		print("   静态 input 被程序化 .click() 并触发了 change。")
		print("   → 那么手工录制录错 input 的原因不在「按钮动态新建」，而在别处：")
		print("     · 扩展 onFileChange 的 e.target 是不是被别的 input 抢了？")
		print("     · 后端 _locate_upload_file 在 selector_map 里 accept 过滤是否找错？")
		print("   需进一步看 [prototype.click] 命中的 input vs change 的 input 是否一致。")
	elif in_added and in_static:
		print("▶▶ 判定：xpath 同时命中存量和新加（罕见，可能 xpath 计算含动态段）。")
		print("   以 accept+class 为准进一步人工核对。")
	else:
		# 既不在存量也不在新加 —— 可能 change 的 input 在 hook 注入后才进 DOM（hook 漏了），或 xpath 不稳
		print("▶▶ 判定：change 的 input 既不在存量清单，也没被 MutationObserver 捕到。")
		print("   可能：hook 注入时机晚于该 input 创建，或 xpath 漂移。看 class/accept 人工比对：")
		print(f"   change input class={chg['class']!r} accept={chg['accept']!r}")
		if inventory:
			print("   存量清单 class/accept：")
			for c in inventory:
				print(f"     · class={c['class']!r} accept={c['accept']!r}")

	# 额外：.click() 命中与 change 是否同一个
	if data["clicks"]:
		last_click = data["clicks"][-1]
		same = last_click["xpath"] == chg["xpath"]
		print(f"\n[佐证] .click() 最后命中的 input 与 change 的 input xpath 是否一致? → {'一致' if same else '不一致'}")
		if not same:
			print(f"   .click()→ class={last_click['class']!r} accept={last_click['accept']!r}")
			print(f"   change → class={chg['class']!r} accept={chg['accept']!r}")
			print("   不一致说明：.click() 触发的 input 和真正收文件的 input 不是同一个（按钮逻辑复杂）。")

	await browser.stop()


if __name__ == "__main__":
	asyncio.run(main())
