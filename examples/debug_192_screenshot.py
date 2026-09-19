"""issue #192 视觉验证 v2：量 select 位置 → 强滚 → clip 截图 Customer Groups 区域。"""
import asyncio
import base64
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

	async def rect():
		r = await send("Runtime.evaluate", {
			"expression": """(() => {
				const el = document.querySelector('select[name="customer_group_ids"]');
				const r = el.getBoundingClientRect();
				const scrollers = [];
				for (let p = el.parentElement; p; p = p.parentElement) {
					const cs = getComputedStyle(p);
					if ((cs.overflowY === 'auto' || cs.overflowY === 'scroll')) scrollers.push(p.tagName + '.' + String(p.className).slice(0, 40));
				}
				return JSON.stringify({top: r.top, left: r.left, w: r.width, h: r.height,
					scrollY: window.scrollY, docH: document.documentElement.scrollHeight,
					scrollers, bodyScroll: document.body.scrollTop});
			})()""",
			"returnByValue": True, "awaitPromise": True,
		})
		return json.loads(r.get("result", {}).get("value") or "null")

	print("before scroll:", await rect())

	# multi JS 三组（先写值再滚动）
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

	# 强滚：直接把 select 顶到视口上部（window.scrollBy + 元素绝对位置双保险）
	await send("Runtime.evaluate", {
		"expression": """(() => {
			const el = document.querySelector('select[name="customer_group_ids"]');
			const r = el.getBoundingClientRect();
			window.scrollBy({top: r.top - 150, behavior: 'instant'});
			el.scrollIntoView({block: 'center'});
		})()""",
		"returnByValue": True,
	})
	await asyncio.sleep(0.5)
	print("after scroll:", await rect())

	pos = await rect()
	if pos:
		# clip 截图：select 区域向上多留 60px（含标签），向下留 40px
		clip = {
			"x": max(0, pos["left"] - 20), "y": max(0, pos["top"] - 60),
			"width": pos["w"] + 260, "height": pos["h"] + 100, "scale": 1.5,
		}
		shot = await send("Page.captureScreenshot", {"format": "png", "clip": clip})
		with open("docs/bug-fix/192-clip.png", "wb") as f:
			f.write(base64.b64decode(shot["data"]))
		print("screenshot: docs/bug-fix/192-clip.png", clip)

	await send("Target.closeTarget", {"targetId": tid})
	await client.stop()


if __name__ == "__main__":
	asyncio.run(main())
