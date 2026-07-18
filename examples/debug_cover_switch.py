"""交互诊断：封面编辑器横/竖 step 切换机制 + 封面上传 input 是否随切换变化。

dump：当前 step(设置横/竖封面)、step 元素(可点的)、所有 input 的归属区(semi-upload 祖先 class /
容器 class / 区文本)、以及「设置竖封面」切换按钮。等用户点切换后再 dump 对比——重点看封面上传
input(上传封面区 primary)切换前后 xpath/semiClass 是否变了（变=每次切 step 新建 input；不变=复用）。

用法：Chrome --remote-debugging-port=9222，停在封面编辑器。
      uv run python examples/debug_cover_switch.py
      → 看 Phase1 dump → 在浏览器点「设置竖封面」切到竖 → 回终端按回车 → 看 Phase2 对比
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
function cls(el){ return (el && typeof el.className==='string') ? el.className : ''; }
function xp(el){
  if(!el||el.nodeType!==1) return null;
  const p=[]; let c=el;
  while(c&&c.nodeType!==1) c=c.parentElement;
  while(c&&c.nodeType===1&&c!==document.documentElement){
    let i=1,s=c.previousElementSibling;
    while(s){ if(s.tagName===c.tagName)i++; s=s.previousElementSibling; }
    p.unshift(c.tagName.toLowerCase()+(i>1?`[${i}]`:'')); c=c.parentElement;
  }
  return '/'+p.join('/');
}
const stepEls = Array.from(document.querySelectorAll('[class*="step-"]')).filter(el=>{
  const t=(el.textContent||'').trim();
  return t==='设置横封面'||t==='设置竖封面';
});
const steps = stepEls.map(el=>({text:(el.textContent||'').trim(), active:/active/i.test(cls(el)),
  tag:el.tagName.toLowerCase(), xpath:xp(el), cursor:getComputedStyle(el).cursor}));
const activeStep = (steps.find(s=>s.active)||{}).text;
const inputs = Array.from(document.querySelectorAll('input[type="file"]')).map((inp,n)=>{
  let semiClass=null, containerCls=null, areaText=null;
  let c=inp.parentElement,d=0;
  while(c&&d<10){
    const cl=cls(c);
    if(semiClass===null && /semi-upload/i.test(cl)) semiClass=cl.slice(0,42);
    if(/container-XzaV9h|list-Ldrppp|presetList/i.test(cl) && !containerCls) containerCls=cl.slice(0,36);
    c=c.parentElement;d++;
  }
  c=inp.parentElement;d=0;
  while(c&&d<6){
    const t=(c.textContent||'').trim();
    if(t.length<30 && (t.includes('点击上传文件')||t.includes('上传封面')||t.includes('点击上传新'))){areaText=t;break;}
    c=c.parentElement;d++;
  }
  const ic=cls(inp);
  return {n, accept:(inp.accept||'').slice(0,14),
    role:/replace/i.test(ic)?'REPLACE':(/hidden-input/i.test(ic)?'primary':'?'),
    semi:semiClass||'-', cont:containerCls||'-', text:areaText||'-', xpath:xp(inp)};
});
const switchBtns = Array.from(document.querySelectorAll('button,[role=button],span,div')).filter(el=>{
  const t=(el.textContent||'').trim(); return t==='设置竖封面'||t==='设置横封面';
}).slice(0,6).map(el=>({tag:el.tagName.toLowerCase(),text:(el.textContent||'').trim(),
  cursor:getComputedStyle(el).cursor, xpath:xp(el), cls:cls(el).slice(0,30)}));
return {activeStep, steps, inputs, switchBtns};
"""


def show(val, tag):
	print(f"\n========== {tag} ==========")
	print(f"当前 step = {val['activeStep']!r}")
	print(f"step 元素: {[(s['text'], s['active'], s['cursor']) for s in val['steps']]}")
	if val["switchBtns"]:
		print(f"切换按钮候选: {[(b['text'], b['tag'], b['cursor']) for b in val['switchBtns']]}")
	print(f"{'n':>2} {'role':<8} {'semi-upload 祖先':<30} {'text':<22} xpath 末段")
	for el in val["inputs"]:
		tail = el["xpath"].rsplit("/", 3)[-2:] if el["xpath"] else "?"
		tail = "/".join(tail)
		print(f"{el['n']:>2} {el['role']:<8} {el['semi'][:29]:<30} {el['text'][:21]:<22} ...{tail}")
	# 封面 primary 标识
	covers = [el for el in val["inputs"] if "上传文件" in el["text"] or "container-XzaV9h" in el["cont"]]
	cover_primary = [el for el in covers if el["role"] == "primary"]
	print(f"→ 封面上传区 primary input: n={[el['n'] for el in cover_primary]}  xpath={cover_primary[0]['xpath'] if cover_primary else '无'}")


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

	val, exc = await eval_js(browser, sid, JS)
	if exc:
		print("失败:", exc); await browser.stop(); return
	show(val, "Phase 1：当前状态")
	phase1_cover = next((el["xpath"] for el in val["inputs"]
	                     if el["role"] == "primary" and ("上传文件" in el["text"] or "container-XzaV9h" in el["cont"])), None)

	print("\n>>> 现在在浏览器点「设置竖封面」切换到竖 step，然后回这里按回车 <<<")
	try:
		input()
	except EOFError:
		pass

	val, exc = await eval_js(browser, sid, JS)
	await browser.stop()
	if exc:
		print("失败:", exc); return
	show(val, "Phase 2：切换后状态")
	phase2_cover = next((el["xpath"] for el in val["inputs"]
	                     if el["role"] == "primary" and ("上传文件" in el["text"] or "container-XzaV9h" in el["cont"])), None)

	print("\n" + "=" * 60)
	print("切换前后对比")
	print("=" * 60)
	print(f"封面 primary input xpath:")
	print(f"  Phase1: {phase1_cover}")
	print(f"  Phase2: {phase2_cover}")
	if phase1_cover and phase2_cover:
		print(f"  → {'同一个 input（横竖复用，靠 step 区分目标）' if phase1_cover == phase2_cover else '不同 input（每次切 step 换新 input）'}")
	else:
		print(f"  → 某个阶段没找到封面上传 primary（可能 step 切换后该 input 隐藏/结构变）")


if __name__ == "__main__":
	asyncio.run(main())
