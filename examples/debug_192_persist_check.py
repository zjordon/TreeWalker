"""issue #192 持久化核查：用户已跑过验收探针（Save 过一条 debug192-<suffix> 规则）。
进列表页找该规则 → 开编辑页回读 customer_group_ids 实际持久化的选中集。
判定：保存链路是否丢组（原生三组 → DB 单组）。

用法：uv run python examples/debug_192_persist_check.py
"""
import asyncio
import json

from cdp_use import CDPClient

LIST_URL = "http://localhost:7780/admin/sales_rule/promo_quote/"


async def main() -> None:
	import urllib.request
	with urllib.request.urlopen("http://127.0.0.1:9223/json/version") as r:
		ws_url = json.load(r)["webSocketDebuggerUrl"]
	client = CDPClient(ws_url)
	await client.start()
	target = await client.send.Target.createTarget({"url": LIST_URL})
	tid = target["targetId"]
	sess = await client.send.Target.attachToTarget({"targetId": tid, "flatten": True})
	sid = sess["sessionId"]

	async def send(method, params):
		return await client.send_raw(method, params, session_id=sid)

	await asyncio.sleep(3.0)

	# 1) 列表页找 debug192- 规则行的编辑链接
	found = await send("Runtime.evaluate", {
		"expression": """(() => {
			const rows = Array.from(document.querySelectorAll('tr'));
			for (const tr of rows) {
				const text = tr.textContent || '';
				if (text.includes('debug192-')) {
					const link = tr.querySelector('a[href*="edit"]');
					return JSON.stringify({
						name: (text.match(/debug192-[a-z0-9]+/) || ['?'])[0],
						editHref: link ? link.getAttribute('href') : null,
					});
				}
			}
			return JSON.stringify(null);
		})()""",
		"returnByValue": True, "awaitPromise": True,
	})
	row = json.loads(found.get("result", {}).get("value") or "null")
	print("列表页命中:", row)
	if not row or not row.get("editHref"):
		raise SystemExit("列表页没找到 debug192- 规则（可能上次 Save 未成功）")

	# 2) 开编辑页回读持久化选中
	edit_url = row["editHref"]
	if edit_url.startswith("/"):
		edit_url = "http://localhost:7780" + edit_url
	print(f"打开编辑页: {edit_url}")
	await send("Page.navigate", {"url": edit_url})
	await asyncio.sleep(3.0)

	state = await send("Runtime.evaluate", {
		"expression": """(() => {
			const el = document.querySelector('select[name="customer_group_ids"]');
			if (!el) return JSON.stringify({found: false});
			return JSON.stringify({
				found: true,
				selected: Array.from(el.options).filter(o => o.selected).map(o => o.value + '=' + o.text.trim()),
			});
		})()""",
		"returnByValue": True, "awaitPromise": True,
	})
	print("编辑页持久化选中:", state.get("result", {}).get("value"))

	await send("Target.closeTarget", {"targetId": tid})
	await client.stop()


if __name__ == "__main__":
	asyncio.run(main())
