"""issue #192 现象诊断：multi JS 三组写入后，选中集何时/被谁拍回单值。

用户实测：探针同步回读断言过（或挂？——本探针同时验证），但页面上只
剩 General 高亮。两候选机制：
  A. KO/表单组件在 change 后异步把 element.value（multiple 下=第一个选中项
     "1"=General）写回 select → 拍回单值；
  B. 原生 select 被 chosen/select2 等组件替代渲染（原生 display:none），
     组件 UI 与原生状态脱节。
分时回读（JS 内部同步值 / +0.5s / +1.5s / +3s）+ 组件包装检查。

用法：uv run python examples/debug_192_async_revert_probe.py
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

	# 1) 组件包装检查：原生 select 是否被隐藏（chosen/select2 替代渲染）？
	env = await send("Runtime.evaluate", {
		"expression": """(() => {
			const el = document.querySelector('select[name="customer_group_ids"]');
			if (!el) return JSON.stringify({found: false});
			const cs = getComputedStyle(el);
			const parentClasses = el.parentElement ? el.parentElement.className : '';
			const prevSibling = el.previousElementSibling;
			return JSON.stringify({
				found: true,
				display: cs.display, visibility: cs.visibility,
				width: el.offsetWidth, height: el.offsetHeight,
				multiple: el.multiple, size: el.size,
				parentClasses: String(parentClasses).slice(0, 120),
				prevSiblingTag: prevSibling ? prevSibling.tagName + '.' + String(prevSibling.className).slice(0, 80) : null,
				aroundChosen: !!el.closest('.chosen-container, .select2-container, .admin__action-multiselect'),
				mageInit: el.getAttribute('data-mage-init') || el.getAttribute('data-role') || null,
				optionTexts: Array.from(el.options).map(o => o.value + '=' + o.text.trim()),
			});
		})()""",
		"returnByValue": True, "awaitPromise": True,
	})
	print("=== select 环境 ===")
	print(env.get("result", {}).get("value"))

	# 2) 跑生产 multi JS（三组一次设全）
	obj = await send("Runtime.evaluate", {
		"expression": "document.querySelector('select[name=\"customer_group_ids\"]')",
	})
	object_id = obj.get("result", {}).get("objectId")
	if not object_id:
		raise SystemExit("customer_group_ids select 不在 DOM——表单未就绪")
	call = await send("Runtime.callFunctionOn", {
		"objectId": object_id,
		"functionDeclaration": _SELECT_OPTION_MULTI_JS,
		"arguments": [{"value": ["General", "Wholesale", "Retailer"]}],
		"returnByValue": True,
	})
	print("\n=== multi JS 同步返回 ===")
	print(json.dumps(call.get("result", {}).get("value", {}), ensure_ascii=False))

	# 3) 分时回读（外部判据）
	readback_js = ("Array.from(document.querySelector('select[name=\"customer_group_ids\"]').options)"
		".filter(o => o.selected).map(o => o.value)")
	for delay in (0.0, 0.5, 1.5, 3.0):
		if delay:
			await asyncio.sleep(delay)
		rb = await send("Runtime.evaluate", {
			"expression": readback_js, "returnByValue": True,
		})
		print(f"t+{delay}s selected values: {rb.get('result', {}).get('value')}")

	# 4) change 事件监听计数 + change 后 element.value 观察（区分「组件监听 change 拍回」）
	after = await send("Runtime.evaluate", {
		"expression": """(() => {
			const el = document.querySelector('select[name="customer_group_ids"]');
			return JSON.stringify({
				valueGetter: el.value,
				selectedOptions: Array.from(el.selectedOptions).map(o => o.value),
			});
		})()""",
		"returnByValue": True, "awaitPromise": True,
	})
	print("\n=== 终态 ===")
	print(after.get("result", {}).get("value"))

	await send("Target.closeTarget", {"targetId": tid})
	await client.stop()


if __name__ == "__main__":
	asyncio.run(main())
