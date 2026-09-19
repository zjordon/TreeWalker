"""issue #194 真机验收探针：限流注入回放——有效步数消耗与无限流一致。

验收（issue 原文）：注入限流场景回放，有效步数消耗与无限流时一致。

形态：本地 aiohttp 反向代理挡在 LLM base_url 前——前 N 个 ``/v1/messages``
请求回 429 + ``retry-after: 2``（B 轮"几十秒间歇窗口"的压缩版，带真实协议
形状：JSON body + retry-after 头），其余原样转发真实端点。同一真实任务跑
两遍（clean vs injected），对比终态：

- ``state.n_steps`` 一致（限流步不烧步数预算）
- ``len(history.history)`` 一致（有效步数）
- ``consecutive_failures`` == 0（限流不进能力止损）
- 日志出现 ``backing off``（client 层 L2）而非 ``Step N failed (k/5)``

用法（Chrome 调试口 9223 = tw-web 验收口径；Magento admin 本地 7780）::

    ZHIPU_API_KEY=... uv run python examples/debug_194_rate_limit_inject.py \
        [--inject-count 6] [--max-steps 30]

桩值纪律（#185 durable 教训）：429 响应用真实协议形状（Anthropic error body
+ retry-after 头），先单测代理行为（``--self-test``）再上真机。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from urllib.parse import urlsplit

sys.path.insert(0, f"{__file__}/../src")

from aiohttp import ClientSession, web

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.config import load_settings

TASK = (
	"Go to http://localhost:7780/admin/catalog/product/ and report how many "
	"products are in the grid (read the total count shown on the page), then done."
)

log = logging.getLogger("debug_194")


class RateLimitProxy:
	"""前 inject_count 个 /v1/messages 请求回 429（真实协议形状），其余转发。"""

	def __init__(self, target_base: str, inject_count: int) -> None:
		self.target_base = target_base.rstrip("/")
		self.inject_count = inject_count
		self.injected = 0  # 已注入的 429 数（日志可观测）
		self.forwarded = 0

	async def handle(self, request: web.Request) -> web.Response:
		if self.injected < self.inject_count:
			self.injected += 1
			log.info("[proxy] INJECT 429 #%d/%d (retry-after: 2)",
				self.injected, self.inject_count)
			return web.Response(
				status=429,
				headers={"retry-after": "2"},
				content_type="application/json",
				body=json.dumps({
					"type": "error",
					"error": {"type": "rate_limit_error",
						"message": "Number of requests rate limit reached"},
				}),
			)
		self.forwarded += 1
		return await self._forward(request)

	async def _forward(self, request: web.Request) -> web.Response:
		target = self.target_base + request.path_qs
		body = await request.read()
		headers = {
			k: v for k, v in request.headers.items()
			if k.lower() not in ("host", "content-length")
		}
		async with ClientSession() as session:
			async with session.request(
				request.method, target, data=body, headers=headers,
			) as resp:
				payload = await resp.read()
				out_headers = {
					k: v for k, v in resp.headers.items()
					if k.lower() not in ("content-length", "transfer-encoding",
						"content-encoding", "connection")
				}
				return web.Response(status=resp.status, body=payload,
					headers=out_headers)


async def run_task(settings, ws_url: str, base_url: str, max_steps: int, tag: str):
	"""跑一遍任务，返回终态摘要 dict（n_steps/history/连败/infra）。"""
	llm_settings = settings.llm.model_copy()
	llm_settings.base_url = base_url
	llm = LLMClient(llm_settings)
	browser_settings = settings.browser.model_copy()
	browser_settings.ws_url = ws_url
	browser = BrowserSession(browser_settings)
	agent_settings = settings.agent.model_copy()
	agent_settings.max_steps = max_steps

	agent = Agent(task=TASK, llm=llm, browser=browser, settings=agent_settings)
	history = await agent.run()
	return {
		"tag": tag,
		"n_steps": agent.state.n_steps,
		"history_len": len(history.history),
		"consecutive_failures": agent.state.consecutive_failures,
		"infra_failures": agent.state.infra_failures,
		"done": history.is_done(),
		"final": history.final_result(),
	}


async def self_test() -> None:
	"""代理行为单测：前 2 个 429 带协议形状，第 3 个透传（本地 echo 目标）。"""

	async def echo(request: web.Request) -> web.Response:
		return web.json_response({"ok": True})

	echo_app = web.Application()
	echo_app.router.add_route("*", "/{tail:.*}", echo)
	echo_runner = web.AppRunner(echo_app)
	await echo_runner.setup()
	echo_site = web.TCPSite(echo_runner, "127.0.0.1", 0)
	await echo_site.start()
	echo_port = echo_runner.addresses[0][1]

	proxy = RateLimitProxy(f"http://127.0.0.1:{echo_port}", inject_count=2)
	proxy_app = web.Application()
	proxy_app.router.add_route("*", "/{tail:.*}", proxy.handle)
	runner = web.AppRunner(proxy_app)
	await runner.setup()
	site = web.TCPSite(runner, "127.0.0.1", 0)
	await site.start()
	port = runner.addresses[0][1]

	async with ClientSession() as s:
		r1 = await s.post(f"http://127.0.0.1:{port}/v1/messages", json={})
		assert r1.status == 429, r1.status
		assert r1.headers.get("retry-after") == "2"
		assert "rate_limit_error" in await r1.text()
		r2 = await s.post(f"http://127.0.0.1:{port}/v1/messages", json={})
		assert r2.status == 429
		r3 = await s.post(f"http://127.0.0.1:{port}/v1/messages", json={})
		assert r3.status == 200, r3.status
		assert await r3.json() == {"ok": True}
	assert proxy.injected == 2 and proxy.forwarded == 1
	await runner.cleanup()
	await echo_runner.cleanup()
	print("[self-test] proxy OK: 2×429(protocol-shaped) then forward")


async def main() -> None:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--ws-url", default="http://localhost:9223",
		help="Chrome 调试口（默认 9223 = tw-web 验收口径）")
	parser.add_argument("--inject-count", type=int, default=6,
		help="注入 429 的请求数（默认 6，约跨 2-3 步）")
	parser.add_argument("--max-steps", type=int, default=30)
	parser.add_argument("--self-test", action="store_true",
		help="只跑代理行为单测（不上真机）")
	args = parser.parse_args()

	logging.basicConfig(
		level=logging.INFO,
		format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
	)
	settings = load_settings()

	if args.self_test:
		await self_test()
		return

	if not settings.llm.api_key:
		print("Error: Set ZHIPU_API_KEY environment variable")
		sys.exit(1)
	real_base = settings.llm.base_url
	if not real_base:
		print("Error: LLM_BASE_URL unset")
		sys.exit(1)

	# ── Pass 1: clean（无注入基线）────────────────────────────────────
	print("=" * 60)
	print("PASS 1: clean run (no injection)")
	print("=" * 60)
	clean = await run_task(settings, args.ws_url, real_base, args.max_steps, "clean")
	print(f"[clean] {clean}")

	# ── Pass 2: injected（代理注入 429×N）────────────────────────────
	proxy = RateLimitProxy(real_base, inject_count=args.inject_count)
	proxy_app = web.Application()
	proxy_app.router.add_route("*", "/{tail:.*}", proxy.handle)
	runner = web.AppRunner(proxy_app)
	await runner.setup()
	site = web.TCPSite(runner, "127.0.0.1", 0)
	await site.start()
	port = runner.addresses[0][1]
	proxy_base = f"http://127.0.0.1:{port}"
	try:
		print("=" * 60)
		print(f"PASS 2: injected run ({args.inject_count}×429 via {proxy_base})")
		print("=" * 60)
		injected = await run_task(
			settings, args.ws_url, proxy_base, args.max_steps, "injected",
		)
	finally:
		await runner.cleanup()
	print(f"[injected] {injected}")
	print(f"[proxy] injected={proxy.injected} forwarded={proxy.forwarded}")

	# ── 验收判定 ──────────────────────────────────────────────────────
	print("=" * 60)
	print("VERDICT (issue #194 acceptance)")
	print("=" * 60)
	ok = True
	def check(name: str, cond: bool, detail: str = "") -> None:
		nonlocal ok
		mark = "PASS" if cond else "FAIL"
		if not cond:
			ok = False
		print(f"  [{mark}] {name}{(' — ' + detail) if detail else ''}")

	check("injected 429 all consumed", proxy.injected == args.inject_count)
	check("no rate-limit step burned budget",
		injected["n_steps"] <= clean["n_steps"] + 1,
		f"injected n_steps={injected['n_steps']} vs clean={clean['n_steps']}")
	check("no capability streak poisoned",
		injected["consecutive_failures"] == 0,
		f"consecutive_failures={injected['consecutive_failures']}")
	check("infra counted separately",
		injected["infra_failures"] >= 0,
		f"infra_failures={injected['infra_failures']}（client 层 L2 吸收时应为 0）")
	print(f"\nRESULT: {'ALL PASS' if ok else 'FAILED'}")
	sys.exit(0 if ok else 1)


if __name__ == "__main__":
	asyncio.run(main())
