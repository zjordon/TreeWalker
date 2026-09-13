"""issue #185 分析探针：确认 CDP Runtime.evaluate 编译期 SyntaxError 的 exceptionDetails 形状。

背景：_syntax_repair_candidates / "Unexpected end of input" 提示都拿
exceptionDetails.text 匹配；三份 B 轮日志显示 text 只有 "Uncaught"，
真正的 "SyntaxError: ..." 在 exception.description 里。本探针只读不改页面：
对一个空白 about:blank 新标签跑三条已知会编译失败的代码，打印原始
exceptionDetails 的 text 与 description 字段。

用法：uv run python examples/debug_issue185_cdp_shape.py
"""
import asyncio
import json

from cdp_use import CDPClient


async def main() -> None:
	import urllib.request
	with urllib.request.urlopen("http://127.0.0.1:9223/json/version") as r:
		ws_url = json.load(r)["webSocketDebuggerUrl"]
	client = CDPClient(ws_url)
	await client.start()
	target = await client.send.Target.createTarget({"url": "about:blank"})
	tid = target["targetId"]
	sess = await client.send.Target.attachToTarget({"targetId": tid, "flatten": True})
	sid = sess["sessionId"]

	async def send(method, params):
		return await client.send_raw(method, params, session_id=sid)

	probes = [
		("bare return", "return 1"),
		("unclosed brace", "((function(){var x=1;"),
		("extra brace", "((function(){return 1}})()"),
	]
	for name, code in probes:
		result = await send("Runtime.evaluate", {
			"expression": code, "returnByValue": True, "awaitPromise": True,
		})
		exc = result.get("exceptionDetails")
		print(f"\n=== {name}: {code!r}")
		if not exc:
			print("  no exceptionDetails:", json.dumps(result)[:200])
			continue
		text = exc.get("text")
		desc = exc.get("exception", {}).get("description")
		print(f"  text        = {text!r}")
		print(f"  description = {desc!r}")
		print(f"  'Illegal return statement' in text ? {'Illegal return statement' in str(text)}")
		print(f"  'Unexpected end of input' in text ? {'Unexpected end of input' in str(text)}")

	await send("Target.closeTarget", {"targetId": tid})
	await client.stop()


if __name__ == "__main__":
	asyncio.run(main())
