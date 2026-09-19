"""issue #192 Save 绕行验证：#save 按钮绑定死（click 静默 no-op）→ form.requestSubmit()。

流程：填 name → multi JS 三组 → requestSubmit → 观察是否提交（URL 跳列表页）/
被拦（错误消息）。若提交成功，随后可用 persist_check 回读编辑页选中集。

用法：uv run python examples/debug_192_request_submit.py
"""
import asyncio
import json

from cdp_use import CDPClient

from tree_walker.browser.session import _SELECT_OPTION_MULTI_JS

NEW_RULE_URL = "http://localhost:7780/admin/sales_rule/promo_quote/new/"


async def main() -> None:
	import urllib.request
	with urllib.request.urlopen("http://127.0.0.1:9223/json/version") as r:
		ws_url = json.load(r)["webSocketDebuggerUrl"]
	client = CDPClient(ws_url)
	await client.start()
	target = await client.send.Target.createTarget({"url": NEW_RULE_URL})
	tid = target["targetId"]
	sess = await client.send.Target.attachToTarget({"targetId": tid, "flatten": True})
	sid = sess["sessionId"]

	async def send(method, params):
		return await client.send_raw(method, params, session_id=sid)

	await asyncio.sleep(3.0)

	# 1) 填 name
	await send("Runtime.evaluate", {
		"expression": (
			"(() => { const el = document.querySelector('input[name=\"name\"]');"
			" const set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;"
			" set.call(el, 'debug192-rs1');"
			" el.dispatchEvent(new Event('input', {bubbles: true}));"
			" el.dispatchEvent(new Event('change', {bubbles: true})); })()"
		),
		"returnByValue": True,
	})

	# 2) multi JS 三组
	obj = await send("Runtime.evaluate", {
		"expression": "document.querySelector('select[name=\"customer_group_ids\"]')",
	})
	call = await send("Runtime.callFunctionOn", {
		"objectId": obj["result"]["objectId"],
		"functionDeclaration": _SELECT_OPTION_MULTI_JS,
		"arguments": [{"value": ["General", "Wholesale", "Retailer"]}],
		"returnByValue": True,
	})
	print("multi JS:", json.dumps(call.get("result", {}).get("value", {}), ensure_ascii=False))

	# 3) form.requestSubmit()（绕过死按钮；会触发 jQuery varienForm 的 submit/验证链）
	await send("Runtime.evaluate", {
		"expression": "document.querySelector('#edit_form')?.requestSubmit()",
		"returnByValue": True,
	})

	async def snapshot(label):
		st = await send("Runtime.evaluate", {
			"expression": """(() => {
				const sel = document.querySelector('select[name="customer_group_ids"]');
				return JSON.stringify({
					href: location.href,
					selected: sel ? Array.from(sel.options).filter(o => o.selected).map(o => o.value) : null,
					errs: Array.from(document.querySelectorAll('.message-error, .mage-error'))
						.map(e => (e.textContent || '').trim()).filter(Boolean).slice(0, 3),
				});
			})()""",
			"returnByValue": True, "awaitPromise": True,
		})
		print(f"--- {label}: {st.get('result', {}).get('value')}")

	for delay, label in ((1.0, "rs+1s"), (2.0, "rs+3s"), (2.0, "rs+5s")):
		await asyncio.sleep(delay)
		await snapshot(label)

	await send("Target.closeTarget", {"targetId": tid})
	await client.stop()


if __name__ == "__main__":
	asyncio.run(main())
