"""自动诊断：点「设置竖封面」切 step，对比切换前后封面上传 input 是否变化。

回答核心问题：横/竖是复用同一个上传 input（靠 step 区分目标），还是每次切 step 换新 input？
脚本自己 JS click「设置竖封面」→ 等 → dump 对比 → 切回横（恢复）。

用法：Chrome --remote-debugging-port=9222，停在封面编辑器（当前在「设置横封面」step）。
      uv run python examples/debug_cover_switch_auto.py
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


JS_DUMP = r"""
function cls(el){ return (el && typeof el.className==='string') ? el.className : ''; }
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
const stepEls = Array.from(document.querySelectorAll('[class*="step-"]')).filter(el=>{
  const t=(el.textContent||'').trim(); return t.includes('设置横封面')||t.includes('设置竖封面');
});
const activeStep = (stepEls.find(el=>/active/i.test(cls(el)))||{}).text;
const inputs = Array.from(document.querySelectorAll('input[type="file"]')).map((inp,n)=>{
  let semiClass=null, areaText=null;
  let c=inp.parentElement,d=0;
  while(c&&d<10){ const cl=cls(c);
    if(semiClass===null && /semi-upload/i.test(cl)) semiClass=cl.slice(0,42);
    c=c.parentElement;d++;
  }
  c=inp.parentElement;d=0;
  while(c&&d<6){const t=(c.textContent||'').trim();
    if(t.length<30 && (t.includes('点击上传文件')||t.includes('上传封面')||t.includes('点击上传新'))){areaText=t;break;}
    c=c.parentElement;d++;
  }
  const ic=cls(inp);
  return {n, role:/replace/i.test(ic)?'REPLACE':(/hidden-input/i.test(ic)?'primary':'?'),
    semi:semiClass||'-', text:areaText||'-', xpath:xp(inp)};
});
return {activeStep, inputs};
"""

JS_CLICK_VERTICAL = r"""
const btn = Array.from(document.querySelectorAll('button,[role=button],[class*="step-"]')).find(el=>{
  const t=(el.textContent||'').trim(); return t==='设置竖封面';
});
if(!btn) return {clicked:false, reason:'未找到「设置竖封面」元素'};
btn.click();
return {clicked:true, tag:btn.tagName.toLowerCase()};
"""

JS_CLICK_HORIZONTAL = r"""
const btn = Array.from(document.querySelectorAll('[class*="step-"]')).find(el=>{
  const t=(el.textContent||'').trim(); return t==='设置横封面';
});
if(btn){ btn.click(); return {clicked:true}; }
return {clicked:false};
"""


def cover_primary(val):
	for el in val["inputs"]:
		if el["role"] == "primary" and "上传文件" in el["text"]:
			return el
	return None


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

	v1, exc = await eval_js(browser, sid, JS_DUMP)
	if exc:
		print("Phase1 失败:", exc); await browser.stop(); return
	print(f"Phase1 step={v1.get('activeStep')!r}  封面 primary xpath={(cover_primary(v1) or {}).get('xpath')}")

	# 切到竖
	cv, exc = await eval_js(browser, sid, JS_CLICK_VERTICAL)
	if exc or not cv.get("clicked"):
		print("点击「设置竖封面」失败:", exc or cv); await browser.stop(); return
	print(f"已点击「设置竖封面」(<{cv['tag']}>)")
	await asyncio.sleep(1.5)

	v2, exc = await eval_js(browser, sid, JS_DUMP)
	if exc:
		print("Phase2 失败:", exc); await browser.stop(); return
	print(f"Phase2 step={v2.get('activeStep')!r}  封面 primary xpath={(cover_primary(v2) or {}).get('xpath')}")

	# 切回横（恢复）
	await eval_js(browser, sid, JS_CLICK_HORIZONTAL)
	await browser.stop()

	cp1, cp2 = cover_primary(v1), cover_primary(v2)
	print("\n" + "=" * 60)
	print(f"Phase1 input 数={len(v1['inputs'])}  Phase2 input 数={len(v2['inputs'])}")
	print(f"封面 primary input:")
	print(f"  Phase1(横): {cp1['xpath'] if cp1 else '无'}")
	print(f"  Phase2(竖): {cp2['xpath'] if cp2 else '无'}")
	if cp1 and cp2:
		same = cp1["xpath"] == cp2["xpath"]
		print(f"  → {'✅ 同一个 input（横竖复用，上传目标靠当前 step 决定）' if same else '⚠️ 不同 xpath（切 step 换了 input 或结构变）'}")
		print(f"\n  结论：replay 上传封面应锁定「上传封面区 primary」(text=点击上传文件或拖拽文件到这里)，")
		print(f"        横/竖由前置 click 切 step 保证，upload 本身不分横竖。")
	else:
		print(f"  → 某阶段缺封面 primary；Phase2 input 列表:")
		for el in v2["inputs"]:
			print(f"     n={el['n']} {el['role']} {el['text'][:18]} {el['xpath']}")


if __name__ == "__main__":
	asyncio.run(main())
