"""issue #192 Save 点击效应诊断：multi 三组写入稳定后，点 Save 会不会把选中集拍回单值。

已知事实（前两轮诊断）：
- 不点 Save：三组在 t+3s 内稳定（无异步拍回，裸原生 select）；
- 完整探针：回读三组 assert 过，但 Save 后 URL 仍 /new/（保存没发生），
  用户在表单页只看到 General 高亮。
假设：Save click → Magento 表单验证/序列化链把 element.value（multiple 下
=第一个选中 "1"=General）写回 select → 三组被拍回 General，且验证失败
（Actions 区 simple_action 缺失，quirks 卡第 3 条）阻止提交 → 停在 /new/。

用法：uv run python examples/debug_192_save_click_effect.py
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

	# 1) 填 name（与验收探针完全一致）
	suffix = "svtest1"
	await send("Runtime.evaluate", {
		"expression": (
			"(() => { const el = document.querySelector('input[name=\"name\"]');"
			" const set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;"
			" set.call(el, 'debug192-" + suffix + "');"
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

	async def snapshot(label):
		st = await send("Runtime.evaluate", {
			"expression": """(() => {
				const sel = document.querySelector('select[name="customer_group_ids"]');
				const name = document.querySelector('input[name="name"]');
				const errs = Array.from(document.querySelectorAll('.message-error, .mage-error'))
					.map(e => (e.textContent || '').trim()).filter(Boolean).slice(0, 5);
				return JSON.stringify({
					selected: sel ? Array.from(sel.options).filter(o => o.selected).map(o => o.value) : null,
					nameValue: name ? name.value : null,
					errors: errs,
					href: location.href,
				});
			})()""",
			"returnByValue": True, "awaitPromise": True,
		})
		print(f"--- {label}: {st.get('result', {}).get('value')}")

	await snapshot("Save 点击前")

	# 3) Save click（先择一再点，与修复后的验收探针一致）
	await send("Runtime.evaluate", {
		"expression": "(document.querySelector('#save') || document.querySelector('button[title=\"Save\"]'))?.click()",
		"returnByValue": True,
	})
	for delay, label in ((0.5, "Save+0.5s"), (2.0, "Save+2s"), (4.0, "Save+4s")):
		await asyncio.sleep(delay if label == "Save+0.5s" else delay - 0.5)
		await snapshot(label)

	await send("Target.closeTarget", {"targetId": tid})
	await client.stop()


if __name__ == "__main__":
	asyncio.run(main())
