"""诊断脚本：contenteditable div 的输入到底触发什么事件（input/beforeinput/keydown）。

排查副标题（contenteditable）输入没被扩展录到的根因。

用法：
  1. Chrome 以 --remote-debugging-port=9222 启动，停在抖音上传页（作品描述区）。
  2. uv run python examples/debug_ce_input.py
"""

import asyncio
import json
import sys

sys.path.insert(0, f"{__file__}/../src")

from tree_walker.browser.session import BrowserSession
from tree_walker.config import load_settings
from tree_walker.recorder.recorder import select_http_target


async def eval_js(browser, sid, js):
	"""Runtime.evaluate + returnByValue，JS 包 IIFE（避免全局污染/重声明）。返回 (value, exc)。"""
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


async def main():
	settings = load_settings()
	browser = BrowserSession(settings.browser)
	await browser.start()
	resp = await browser.client.send.Target.getTargets({})
	tid = select_http_target(resp.get("targetInfos", []), None)
	if tid and tid != browser.current_target_id:
		await browser.switch_tab(tid)
	sid = browser.current_session_id

	# 1. 注入 input / beforeinput / keydown 监听（capture，window）
	_, exc = await eval_js(browser, sid, """
window.__tw = {input: [], beforeinput: [], keydown: []};
function logArr(e) {
	const t = e.target;
	const cp = e.composedPath();
	return {
		target: t.tagName + (t.isContentEditable ? '[ce]' : ''),
		cp0: cp[0] ? cp[0].tagName : null,
		value: ((t.tagName==='INPUT'||t.tagName==='TEXTAREA') ? t.value : (t.textContent||'')).slice(0,40),
	};
}
window.addEventListener('input', e => window.__tw.input.push(logArr(e)), true);
window.addEventListener('beforeinput', e => window.__tw.beforeinput.push(logArr(e)), true);
window.addEventListener('keydown', e => window.__tw.keydown.push({key: e.key, target: e.target.tagName}), true);
return 'installed';
""")
	if exc:
		print("注入监听失败:", exc)
		await browser.stop()
		return

	# 2. 找 contenteditable div 并聚焦
	val, exc = await eval_js(browser, sid, """
const div = document.querySelector('[contenteditable]');
if (!div) return 'NO_CE_DIV';
const attr = div.getAttribute('contenteditable');
div.focus();
return 'found: tag=' + div.tagName + ' ce_attr=' + JSON.stringify(attr) + ' isCE=' + div.isContentEditable;
""")
	print("focus:", val if not exc else exc)
	if exc or not (val or "").startswith("found:"):
		await browser.stop()
		return

	# 3. CDP 模拟输入
	try:
		await browser.client.send.Input.insertText({"text": "调试测试文本"}, session_id=sid)
		print("Input.insertText 已发送")
	except Exception as e:
		print("Input.insertText 失败:", e)
	await asyncio.sleep(0.6)

	# 4. 读事件 log
	val, exc = await eval_js(browser, sid, "return JSON.stringify(window.__tw);")
	logs = json.loads(val) if not exc and val else {"input": [], "beforeinput": [], "keydown": []}
	print("\n=== 事件捕获结果 ===")
	print(f"input       事件数: {len(logs['input'])}   {logs['input'][-3:]}")
	print(f"beforeinput 事件数: {len(logs['beforeinput'])}   {logs['beforeinput'][-3:]}")
	print(f"keydown     事件数: {len(logs['keydown'])}   {logs['keydown'][-3:]}")

	val, _ = await eval_js(browser, sid, "return document.querySelector('[contenteditable]')?.textContent?.slice(0,60);")
	print(f"\ndiv 最终 textContent: {val!r}")

	print("\n=== 结论判断 ===")
	if logs["input"]:
		print("✅ input 事件触发了 —— 扩展（共享 window）应能收到。若仍漏录，问题在扩展处理或未重载新版。")
	elif logs["beforeinput"]:
		print("⚠️ 只有 beforeinput，没有 input —— contenteditable 输入不触发 input 事件！")
		print("   扩展需改监听 beforeinput，或 keydown + 周期读 textContent。")
	elif logs["keydown"]:
		print("⚠️ 只有 keydown —— 富文本组件吞掉了 input/beforeinput。")
		print("   扩展需用 keydown 监听 + 周期/focus 出时读 textContent。")
	else:
		print("❌ 三种事件都没触发 —— insertText 可能没生效，或 div 没 focus 成功。")

	await browser.stop()


if __name__ == "__main__":
	asyncio.run(main())
