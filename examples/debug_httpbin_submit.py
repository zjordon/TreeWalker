"""诊断：httpbin submit button 的 findInteractiveAncestor 模拟 + matches 检查。

改动后 onClick 对 findInteractiveAncestor 返回 null 的 click 跳过。怀疑 submit 被误跳过。
本脚本静态查 submit button：是否 matches INTERACTIVE_SELECTOR、findInteractiveAncestor 模拟返回什么。

用法：Chrome --remote-debugging-port=9222，停在 httpbin.org/forms/post。
      uv run python examples/debug_httpbin_submit.py
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
		return None, f"JS异常: {exc.get('exception', {}).get('description', exc) if isinstance(exc, dict) else exc}"
	return res.get("value"), None


JS = r"""
const SELECTOR = ['a[href]','button','input','select','textarea','summary','label','[contenteditable]',
  '[role=button]','[role=link]','[role=textbox]','[role=menuitem]','[role=tab]','[role=checkbox]',
  '[role=radio]','[role=switch]','[role=option]'].join(',');
function xp(el){
  if(!el||el.nodeType!==1) return null;
  const p=[]; let c=el;
  while(c&&c.nodeType===1&&c!==document.documentElement){
    let i=1,s=c.previousElementSibling;
    while(s){ if(s.tagName===c.tagName)i++; s=s.previousElementSibling; }
    p.unshift(c.tagName.toLowerCase()+(i>1?`[${i}]`:'')); c=c.parentElement;
  }
  return '/'+['html',...p].join('/');
}
// 模拟 findInteractiveAncestor（改动后回退 null）
function fia(el){
  let cur = el, steps = [];
  while (cur && cur !== document.body) {
    steps.push(cur.tagName + (cur.matches(SELECTOR)?'(matches)':''));
    try {
      if (cur.matches(SELECTOR)) return {returned: cur.tagName, xpath: xp(cur), steps};
      if (cur.tagName === 'DIV' && getComputedStyle(cur).cursor === 'pointer')
        return {returned: 'DIV-pointer', xpath: xp(cur), steps};
      if (cur.onclick || cur.getAttribute('onclick') || cur.getAttribute('onmousedown'))
        return {returned: 'onclick', xpath: xp(cur), steps};
    } catch(e) {}
    cur = cur.parentElement;
  }
  return {returned: null, steps};  // 回退 null（改动后跳过）
}
const btn = document.querySelector('button[type=submit]') || document.querySelector('form button');
if (!btn) return {error: '未找到 submit button'};
return {
  btnTag: btn.tagName,
  btnType: btn.type,
  btnMatchesButton: btn.matches('button'),
  btnMatchesSelector: btn.matches(SELECTOR),
  btnXpath: xp(btn),
  btnParentTag: btn.parentElement?.tagName,
  fia: fia(btn),
};
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
		print(exc); return
	if val.get("error"):
		print(val["error"]); return
	print(f"submit button: <{val['btnTag']} type={val['btnType']}>")
	print(f"  matches 'button': {val['btnMatchesButton']}")
	print(f"  matches INTERACTIVE_SELECTOR: {val['btnMatchesSelector']}")
	print(f"  xpath: {val['btnXpath']}")
	print(f"  parent: <{val['btnParentTag']}>")
	fia = val["fia"]
	print(f"\nfindInteractiveAncestor(submit) 模拟:")
	print(f"  returned: {fia['returned']}")
	print(f"  steps: {' -> '.join(fia['steps'])}")
	if fia["returned"] and "BUTTON" in str(fia["returned"]).upper():
		print(f"\n✅ findInteractiveAncestor 返回 button ——submit 不该被 onClick 跳过。")
		print(f"   那 step13 submit 无 xpath 是别的原因（emit 中断/content 卸载？）。")
	else:
		print(f"\n❌ findInteractiveAncestor 返回 null ——submit 被 onClick 跳过！这就是 bug。")


if __name__ == "__main__":
	asyncio.run(main())
