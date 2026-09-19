"""issue #192 真机验收探针：<select multiple> 的 values 一次设全，替换语义修复。

场景（699 Cart Price Rule 客户组）：新规则页的 customer_group_ids 是
<select multiple>。旧链路逐次单选最后只剩一组；本探针走生产 multi JS
（_SELECT_OPTION_MULTI_JS，import 复用防分叉）一次设 General/Wholesale/Retailer
三组，回读选中集，填 name 后点 Save，等待整页跳转。

DB 硬校验（脚本尾部打印，需自行执行）：
  docker exec <magento-mysql-container> mysql -u<magento> -p<magento> \
    -e "SELECT * FROM magento2.sales_rule_customer_group ORDER BY rule_id DESC LIMIT 6;"

前置：Chrome 9223 已开且 admin 已登录（P7 评测环境惯例）。
用法：uv run python examples/debug_192_select_multiple.py
"""
import asyncio
import json

from cdp_use import CDPClient

from tree_walker.browser.session import _SELECT_OPTION_MULTI_JS

BASE = "http://localhost:7780/admin"
NEW_RULE_URL = f"{BASE}/sales_rule/promo_quote/new/"


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

	await asyncio.sleep(3.0)  # 等 KO 表单渲染（登录失效会停在 login 页，下方断言兜底）

	# 登录态守卫：name 字段缺失 → 多半被重定向到登录页
	has_name = await send("Runtime.evaluate", {
		"expression": "!!document.querySelector('input[name=\"name\"]')",
		"returnByValue": True,
	})
	if not has_name.get("result", {}).get("value"):
		page_url = (await send("Runtime.evaluate", {
			"expression": "location.href", "returnByValue": True,
		})).get("result", {}).get("value")
		raise SystemExit(f"name 字段不在 DOM（当前 URL: {page_url}）——admin 未登录或页面未就绪")

	# 1) 填 name（评测规则名带随机后缀，便于事后 DB 定位这条规则）
	suffix = (await send("Runtime.evaluate", {
		"expression": "Date.now().toString(36)", "returnByValue": True,
	})).get("result", {}).get("value")
	await send("Runtime.evaluate", {
		"expression": (
			"(() => { const el = document.querySelector('input[name=\"name\"]');"
			" const set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;"
			" set.call(el, 'debug192-' + " + json.dumps(suffix) + ");"
			" el.dispatchEvent(new Event('input', {bubbles: true}));"
			" el.dispatchEvent(new Event('change', {bubbles: true})); })()"
		),
		"returnByValue": True,
	})

	# 2) 生产 multi JS：一次设三组（callFunctionOn + arguments JSON 数组，镜像
	#    set_select_option_multi 的真实调用形状；objectId 经 evaluate 取 select 本身）
	obj = await send("Runtime.evaluate", {
		"expression": "document.querySelector('select[name=\"customer_group_ids\"]')",
	})
	object_id = obj["result"]["objectId"]
	call = await send("Runtime.callFunctionOn", {
		"objectId": object_id,
		"functionDeclaration": _SELECT_OPTION_MULTI_JS,
		"arguments": [{"value": ["General", "Wholesale", "Retailer"]}],
		"returnByValue": True,
	})
	result = call.get("result", {}).get("value", {})
	print("multi JS result:", json.dumps(result, ensure_ascii=False))
	assert result.get("success"), f"multi 选择失败: {result.get('error')}"

	# 3) 回读选中集（外部判据：读 DOM，不信任写通道的返回值）
	readback = await send("Runtime.evaluate", {
		"expression": (
			"Array.from(document.querySelector('select[name=\"customer_group_ids\"]').options)"
			".filter(o => o.selected).map(o => o.value)"
		),
		"returnByValue": True,
	})
	selected = readback.get("result", {}).get("value")
	print(f"readback selected values: {selected}")
	assert selected == ["1", "2", "3"], f"回读非三组: {selected}"

	# 4) Save（整页跳转回列表页）
	await send("Runtime.evaluate", {
		"expression": "document.querySelector('#save')?.click() || document.querySelector('button[title=\"Save\"]')?.click()",
		"returnByValue": True,
	})
	await asyncio.sleep(4.0)
	after = (await send("Runtime.evaluate", {
		"expression": "location.href", "returnByValue": True,
	})).get("result", {}).get("value")
	print(f"after-save URL: {after}")
	print(f"rule name: debug192-{suffix}")
	print("\nDB 硬校验（自行执行，应看到 rule 对应三行 customer_group_id 1/2/3）：")
	print("  docker exec <mysql-container> mysql -u<magento-user> -p<magento-pass> \\")
	print("    -e \"SELECT * FROM magento2.sales_rule_customer_group ORDER BY row_id DESC LIMIT 6;\"")

	await send("Target.closeTarget", {"targetId": tid})
	await client.stop()


if __name__ == "__main__":
	asyncio.run(main())
