"""诊断：封面上传区结构——input 归属（上传封面 vs 参考图）+ 当前 step（横/竖）。

修正之前误判：semi-upload-hidden-input 全局有多组（参考图 list-Ldrppp + 上传封面 container-XzaV9h），
不能按 DOM 顺序分横竖。真结构（conver-dialog.html）：封面上传区**只有一个**（container-XzaV9h
「上传封面」），横/竖靠 step 切换（设置横封面 active ↔ 设置竖封面）复用同一 input。

本脚本 dump 每个 input 所属上传区文本/class，区分「上传封面」与「参考图」，并报当前 step。

用法：Chrome --remote-debugging-port=9222，停在封面编辑器。
      uv run python examples/debug_cover_area.py
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
// 当前 step（设置横封面 / 设置竖封面）
const stepEls = Array.from(document.querySelectorAll('[class*="step-"]')).filter(el=>{
  const t=(el.textContent||'').trim(); return t.includes('设置横封面')||t.includes('设置竖封面');
});
const steps = stepEls.map(el=>({text:(el.textContent||'').trim(), active:/active/i.test(cls(el)), class:cls(el).slice(0,40)}));
const activeStep = steps.find(s=>s.active);

const inputs = Array.from(document.querySelectorAll('input[type="file"]'));
const list = inputs.map((inp,n)=>{
  // 祖先里的上传区 class（区分 container-XzaV9h=封面 / list-Ldrppp=presetList 参考图）
  let areaClass=null, areaText=null;
  let c=inp.parentElement, d=0;
  while(c && d<10){
    const cl=cls(c);
    if(/container-XzaV9h|upload-ZOJTUA|list-Ldrppp|presetList|upload-tips|semi-upload\b/i.test(cl)){
      if(!areaClass) areaClass=cl.slice(0,45);
    }
    c=c.parentElement; d++;
  }
  // 祖先里短文本（上传封面 / 点击上传文件 / 参考图...）
  c=inp.parentElement; d=0;
  while(c && d<6){
    const t=(c.textContent||'').trim();
    if(t.length>0 && t.length<30 && (t.includes('上传封面')||t.includes('点击上传')||t.includes('上传文件'))){
      areaText=t; break;
    }
    c=c.parentElement; d++;
  }
  const r=inp.getBoundingClientRect();
  const ic = cls(inp);
  return {n, accept:(inp.accept||'').slice(0,18),
          role: /replace/i.test(ic)?'REPLACE':(/hidden-input/i.test(ic)?'primary':'?'),
          area: areaClass||'?', areaText: areaText||'',
          vis: r.width>0&&r.height>0};
});
return {activeStep: activeStep?activeStep.text:'(无 active step)', steps, inputs: list};
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

	val, exc = await eval_js(browser, sid, JS)
	await browser.stop()
	if exc:
		print("失败:", exc); sys.exit(1)

	print(f"当前 step = {val['activeStep']!r}")
	print(f"所有 step: {[(s['text'], s['active']) for s in val['steps']]}")
	print(f"\n{'n':>2} {'role':<8} {'vis':>4} {'area':<28} {'areaText':<22} accept")
	print("-" * 100)
	for el in val["inputs"]:
		print(f"{el['n']:>2} {el['role']:<8} {str(el['vis']):>4} {el['area']:<28} {el['areaText']:<22} {el['accept']}")
	# 标注哪个是封面上传区
	covers = [el for el in val["inputs"] if "container-XzaV9h" in el["area"] or "上传封面" in el["areaText"]]
	print(f"\n→ 封面上传区(container-XzaV9h/上传封面) input: {[el['n'] for el in covers]}")
	print(f"  其中 primary(非 replace): {[el['n'] for el in covers if el['role']=='primary']}")


if __name__ == "__main__":
	asyncio.run(main())
