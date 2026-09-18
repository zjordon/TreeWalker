"""issue #185-c2 review5 #1 探针：编译期 SyntaxError 的 exceptionDetails 位置字段。

删除类候选的幻影闭合符风险需要 CDP 出错位置（lineNumber/columnNumber）
交叉验证——先实证字段是否存在、坐标系如何（0/1-based、指向哪个字符）。

用法：uv run python examples/debug_c2_exc_position_shape.py（默认探 9333 临时
headless Chrome；也可改回 9223 常驻实例）
"""
import asyncio
import json
import os
import urllib.request

from cdp_use import CDPClient

PORT = int(os.environ.get("PROBE_PORT", "9333"))


async def main() -> None:
	with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version") as r:
		ws_url = json.load(r)["webSocketDebuggerUrl"]
	client = CDPClient(ws_url)
	await client.start()
	target = await client.send.Target.createTarget({"url": "about:blank"})
	tid = target["targetId"]
	sess = await client.send.Target.attachToTarget({"targetId": tid, "flatten": True})
	sid = sess["sessionId"]

	async def send(method, params):
		return await client.send_raw(method, params, session_id=sid)

	# 多余闭合形态：错位 } 在下标 20 处（(function(){return 1}})()）
	probes = [
		("extra-closer", "(function(){return 1}})()"),
		("eof-missing", "((function(){var a=1;"),
		("regex-then-extra", "(function(){var s='x'.replace(/[)]/g,'');return s}})()"),
	]
	for name, code in probes:
		result = await send("Runtime.evaluate", {
			"expression": code, "returnByValue": True, "awaitPromise": True,
		})
		exc = result.get("exceptionDetails") or {}
		line = exc.get("lineNumber")
		col = exc.get("columnNumber")
		desc = exc.get("exception", {}).get("description")
		print(f"\n=== {name}: {code!r}")
		print(f"  lineNumber={line!r} columnNumber={col!r}")
		print(f"  desc={desc!r}")
		# 探测指向：列号处字符
		if isinstance(line, int) and isinstance(col, int) and line == 0:
			lo, hi = max(0, col - 2), min(len(code), col + 3)
			print(f"  code[{col}]={code[col] if col < len(code) else '<eof>'!r}  ctx={code[lo:hi]!r}")

	await send("Target.closeTarget", {"targetId": tid})
	await client.stop()


if __name__ == "__main__":
	asyncio.run(main())
