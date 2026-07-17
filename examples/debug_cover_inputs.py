"""诊断：封面编辑器里 6 个 file input 各自身份（横/竖/decoy）+ xpath + 容器宽高比。

重放竖封面时 shu.png 传到了错误 input（页面显示横封面图）。怀疑：replay 时 DOM 漂移致
录制的竖封面 xpath（div[3]/input[1]）匹配不上任何 live input → _resolve_file_input_by_accept
fallback 到 candidates[0]（错误的 decoy/横-replace input）。

连真实 Chrome（封面编辑器弹框已打开），dump 每个 input[type=file]：
  - accept / xpath（全路径）/ 自身 bounds / 是否可见
  - 最近可见祖先（上传槽位）的 宽×高 + 宽高比 + 文本 → 据宽高比判横(>1)/竖(<1)
然后比对录制文件 douyin_redesign5.json 里 heng/div[2]、shu/div[3] 的 xpath 是否能在 live 里命中。

用法：Chrome --remote-debugging-port=9222，停在封面编辑器（竖封面弹框打开）。
      uv run python examples/debug_cover_inputs.py
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
function cls(el){ return (el && typeof el.className==='string') ? el.className.slice(0,50) : ''; }
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
return inputs.map((inp, n)=>{
  const r = inp.getBoundingClientRect();
  // 最近可见、有尺寸的祖先（上传槽位）
  let slot=null;
  let c = inp.parentElement, depth=0;
  while(c && depth<12){
    const cs=getComputedStyle(c); const cr=c.getBoundingClientRect();
    const vis = cs.display!=='none' && cs.visibility!=='hidden' && cs.opacity!=='0' && cr.width>0 && cr.height>0;
    if(vis && (cr.width>=80 || cr.height>=80)){
      const aspect = cr.width/cr.height;
      slot = {
        depth, tag:c.tagName.toLowerCase(), class:cls(c),
        w:Math.round(cr.width), h:Math.round(cr.height),
        aspect:+aspect.toFixed(2),
        orient: aspect>1.15?'横':(aspect<0.87?'竖':'方'),
        text:(c.textContent||'').trim().slice(0,24),
      };
      break;
    }
    c=c.parentElement; depth++;
  }
  return {
    n, accept:(inp.accept||'').slice(0,30),
    xpath:xp(inp),
    own:[Math.round(r.width)+'x'+Math.round(r.height)],
    visible: r.width>0 && r.height>0 && getComputedStyle(inp).visibility!=='hidden',
    slot,
  };
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

	val, exc = await eval_js(browser, sid, JS)
	await browser.stop()
	if exc:
		print("失败:", exc); sys.exit(1)

	print(f"\nURL 上下文已连。共 {len(val)} 个 input[type=file]：\n")
	for el in val:
		orient = el["slot"]["orient"] if el["slot"] else "?"
		print(f"[{el['n']}] orient={orient}  accept={el['accept']!r}  visible={el['visible']}  own={el['own']}")
		print(f"     xpath={el['xpath']}")
		if el["slot"]:
			s = el["slot"]
			print(f"     槽位 @depth{s['depth']} <{s['tag']}>.{s['class']!r} {s['w']}x{s['h']} aspect={s['aspect']} text={s['text']!r}")
		else:
			print(f"     槽位=未找到可见祖先")
		print()

	# 比对录制 xpath（douyin_redesign5.json）
	print("=" * 70)
	print("比对录制 douyin_redesign5.json 的 xpath（横 div[2] / 竖 div[3]）：")
	recorded = {
		"heng(横)": "/html/body/div[12]/div/div[2]/div/div/div/div/div[2]/div[2]/div[1]/div[4]/div/div[2]/div[2]/input[1]",
		"shu(竖)":  "/html/body/div[12]/div/div[2]/div/div/div/div/div[2]/div[2]/div[1]/div[4]/div/div[2]/div[3]/input[1]",
	}

	def norm(x):
		return (x or "").strip().lstrip("/")

	for label, rx in recorded.items():
		want = norm(rx)
		hits = [el["n"] for el in val if norm(el["xpath"]) == want]
		suffix = rx.rsplit("/", 2)[-2]  # div[2] or div[3]
		# 也看 live xpath 的末段 div[N]/input
		print(f"\n{label} 录制末段={suffix}")
		print(f"  全路径精确匹配 live: {hits if hits else '❌ 无（xpath 漂移！）'}")
		# 末段 div[N] 命中
		tail_hits = [(el["n"], el["xpath"].rsplit("/", 2)[-2], el['slot']['orient'] if el['slot'] else '?') for el in val]
		print(f"  live 各 input 末段 div[N]: {tail_hits}")


if __name__ == "__main__":
	asyncio.run(main())
