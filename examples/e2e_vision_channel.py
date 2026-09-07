"""E2E 真机验收（issue #175 阶段二 / screenshot.md 验收信号）。

验证三件事（阶段二最终验收口径）：
  1. 回流契约：AGENT_USE_VISION=true + LLM_MODEL=glm-5.3-flash 时，每次 LLM 调用
     的消息里真的带 image block（包 agent.llm.get_action 记录，即**送 SDK 前**的
     最终形态，含 trim/过滤后）；
  2. 视觉有效性：页面答案**只存在于 canvas 像素**（DOM 文本通道理论上不可见），
     模型答对随机底色 = 图真的被"看见"了，不是 DOM 泄题；
  3. 截图入历史：<rerun_history_dir>/screenshots/step_NNN.png 落盘且为合法 PNG。

对照模式（--control）：同任务 use_vision=False 再跑一遍——文本通道答不出 canvas
底色（答对=1/5 瞎猜，报 INCONCLUSIVE）。两者对照 = 图片贡献的隔离证明。

用法（Chrome 需带调试端口，默认 9222；tw-web 的 9223 也可用 --port 指定）：
  uv run python examples/e2e_vision_channel.py
  uv run python examples/e2e_vision_channel.py --port 9223 --control
需 ZHIPU_API_KEY。约 2-4 次 LLM 调用/轮（glm-5.3-flash 低价位）。
"""

import argparse
import asyncio
import os
import pathlib
import random
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ── 测试页：答案只在渲染器像素里 ────────────────────────────────────────
# 隔离演进史（对照组三次答对驱动）：
#  v1 canvas + <script> fillStyle hex → 文本 agent 读源码拿 hex（疑）；
#  v2 同源 PNG（含逐像素抖动防字节解码）→ 对话 dump 破案：agent 用 evaluate
#     drawImage+getImageData **直接读像素**——canvas API 就是像素读取器，
#     带 evaluate 的文本 agent 可完整复刻"看颜色"，同源像素零隔离；
#  v3（本版）swatch 放**跨域端口**且只放行 <img> 加载（无 Origin 头且
#     Sec-Fetch-Dest=image/缺省，其余 403 + no-store）：
#     fetch/XHR 带 Origin → 403；drawImage 跨域无 ACAO → canvas 污染 →
#     getImageData 抛 SecurityError；导航/开窗到图片是 Dest=document → 403
#     （Sec-Fetch 是浏览器禁改头，页面无法伪造）。像素只剩渲染器可达
#     = 截图（视觉）专属。

COLORS = {
	"red": (220, 40, 40),
	"green": (40, 180, 80),
	"blue": (50, 100, 220),
	"orange": (240, 140, 30),
	"purple": (150, 60, 200),
}
ZH_NAMES = {"red": "红色", "green": "绿色", "blue": "蓝色", "orange": "橙色", "purple": "紫色"}


def _make_swatch_png(rgb: tuple[int, int, int]) -> bytes:
	"""纯色色块 + 逐像素 ±8 随机抖动（字节层面无字面颜色三元组；防御纵深，
	主隔离靠跨域+Sec-Fetch 门禁）。seed 随机，无跨跑可背性。"""
	import io
	import random as _random

	from PIL import Image

	rng = _random.Random()
	w, h = 640, 320
	img = Image.new("RGB", (w, h))
	px = img.load()
	for y in range(h):
		for x in range(w):
			px[x, y] = (
				max(0, min(255, rgb[0] + rng.randrange(-8, 9))),
				max(0, min(255, rgb[1] + rng.randrange(-8, 9))),
				max(0, min(255, rgb[2] + rng.randrange(-8, 9))),
			)
	buf = io.BytesIO()
	img.save(buf, format="PNG")
	return buf.getvalue()


def _start_page_server(rgb: tuple[int, int, int]) -> tuple[ThreadingHTTPServer, ThreadingHTTPServer, str]:
	"""HTML 主端口 + swatch 跨域端口（Sec-Fetch 门禁），返回 (html_srv, swatch_srv, url)。"""
	swatch = _make_swatch_png(rgb)

	class SwatchHandler(BaseHTTPRequestHandler):
		def do_GET(self):
			dest = self.headers.get("Sec-Fetch-Dest", "")
			if self.headers.get("Origin") or dest not in ("image", ""):
				# fetch/XHR（带 Origin）、导航/开窗（Dest=document）、其它一律拒
				self.send_response(403)
				self.send_header("Content-Length", "0")
				self.end_headers()
				return
			self.send_response(200)
			self.send_header("Content-Type", "image/png")
			self.send_header("Cache-Control", "no-store")  # 断缓存旁路：每次真实回源
			self.send_header("Content-Length", str(len(swatch)))
			self.end_headers()
			self.wfile.write(swatch)

		def log_message(self, *args):
			pass

	swatch_srv = ThreadingHTTPServer(("127.0.0.1", 0), SwatchHandler)
	threading.Thread(target=swatch_srv.serve_forever, daemon=True).start()
	swatch_url = f"http://127.0.0.1:{swatch_srv.server_address[1]}/swatch.png"

	html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Swatch Test</title></head>
<body style="font-family: sans-serif; margin: 24px;">
  <h1>Color Perception Test</h1>
  <p>Below is an image swatch with a single solid color.</p>
  <img src="{swatch_url}" width="640" height="320" alt="swatch" style="border:1px solid #999">
  <p>Options: red / green / blue / orange / purple</p>
  <button type="button">No-op button</button>
  <a href="#anchor">No-op link</a>
</body></html>""".encode("utf-8")

	class HtmlHandler(BaseHTTPRequestHandler):
		def do_GET(self):
			self.send_response(200)
			self.send_header("Content-Type", "text/html; charset=utf-8")
			self.send_header("Content-Length", str(len(html)))
			self.end_headers()
			self.wfile.write(html)

		def log_message(self, *args):
			pass

	html_srv = ThreadingHTTPServer(("127.0.0.1", 0), HtmlHandler)
	threading.Thread(target=html_srv.serve_forever, daemon=True).start()
	url = f"http://127.0.0.1:{html_srv.server_address[1]}/"
	return html_srv, swatch_srv, url


# ── 一次验收跑（vision 开或关）──────────────────────────────────────────


async def run_once(truth: str, url: str, port: int, use_vision: bool) -> dict:
	from tree_walker.agent import Agent
	from tree_walker.browser.session import BrowserSession
	from tree_walker.config import (
		AgentSettings,
		BrowserSettings,
		JudgeSettings,
		LLMSettings,
		_fetch_ws_url,
	)
	from tree_walker.llm.client import LLMClient

	# BrowserSession 不自动发现端口——从 CDP /json/version 取 ws_url（同 load_settings）
	ws_url = _fetch_ws_url("localhost", port)
	if not ws_url:
		raise RuntimeError(
			f"无法从 http://localhost:{port}/json/version 获取 ws_url —— "
			f"Chrome 需以 --remote-debugging-port={port} 启动后重试"
		)

	out_dir = tempfile.mkdtemp(prefix="tw_vision_e2e_")
	agent_settings = AgentSettings(
		use_vision=use_vision,
		llm_screenshot_size=(1400, 850),  # 直连构造不走 load_settings 自适应，显式给
		max_steps=3,
		judge=JudgeSettings(enabled=False),  # 验收只要 done 文本，省 judge 调用
		rerun_history_dir=out_dir,
		save_conversation_path=str(pathlib.Path(out_dir) / "conversation"),  # 诊断：看它怎么答的
	)
	llm = LLMClient(LLMSettings(model="glm-5.3-flash", api_key=os.environ["ZHIPU_API_KEY"]))
	browser = BrowserSession(BrowserSettings(ws_url=ws_url))

	task = (
		f"打开 {url} 并观察页面。页面上有一张纯色图片（swatch），"
		f"图片的纯色主色是什么颜色？"
		f"用 done 动作回答，extracted_content 只写颜色英文名"
		f"（red/green/blue/orange/purple 五选一）。"
	)
	agent = Agent(task=task, llm=llm, browser=browser, settings=agent_settings)

	# 回流契约探针：记录每次 LLM 调用（trim/过滤后、送 SDK 前）是否带 image block
	calls: list[dict] = []
	orig_get_action = agent.llm.get_action

	async def recording_get_action(system_prompt, messages, tool_schema, **kw):
		t0 = time.perf_counter()
		has_image = any(
			isinstance(m.get("content"), list)
			and any(isinstance(b, dict) and b.get("type") == "image" for b in m["content"])
			for m in messages
		)
		try:
			return await orig_get_action(system_prompt, messages, tool_schema, **kw)
		finally:
			calls.append({"has_image": has_image, "elapsed": time.perf_counter() - t0})

	agent.llm.get_action = recording_get_action

	try:
		history = await asyncio.wait_for(agent.run(), timeout=240)
	finally:
		try:
			await agent.browser.stop()
		except Exception:
			pass

	# done 文本
	answer = ""
	for entry in reversed(history.history):
		for r in entry.result:
			if r.is_done and r.extracted_content:
				answer = r.extracted_content.strip()
				break
		if answer:
			break

	# 截图产物
	shots = sorted(pathlib.Path(out_dir, "screenshots").glob("step_*.png")) if pathlib.Path(out_dir, "screenshots").exists() else []
	shots_valid = all(p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and p.stat().st_size > 1000 for p in shots)

	return {
		"calls": calls,
		"answer": answer,
		"shots": [str(p) for p in shots],
		"shots_valid": shots_valid,
		"conv_dir": str(pathlib.Path(out_dir) / "conversation"),
	}


def answer_matches(answer: str, truth: str) -> bool:
	a = answer.lower()
	return truth in a or ZH_NAMES[truth] in answer


def main() -> int:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--port", type=int, default=9222, help="Chrome 调试端口（默认 9222）")
	parser.add_argument("--control", action="store_true", help="追加 use_vision=False 对照跑")
	args = parser.parse_args()

	import os
	if not os.environ.get("ZHIPU_API_KEY"):
		print("ZHIPU_API_KEY not set")
		return 1

	truth = random.choice(list(COLORS))
	html_srv, swatch_srv, url = _start_page_server(COLORS[truth])
	print(f"ground truth color: {truth}  (page: {url})")
	print("── vision run (use_vision=True, glm-5.3-flash) ──")
	result = asyncio.run(run_once(truth, url, args.port, use_vision=True))
	html_srv.shutdown()
	swatch_srv.shutdown()

	ok = True
	for i, c in enumerate(result["calls"]):
		print(f"  LLM call #{i}: image_in_request={c['has_image']} elapsed={c['elapsed']:.1f}s")
	if not result["calls"] or not all(c["has_image"] for c in result["calls"]):
		print("  ✗ FAIL: 存在不带 image block 的 LLM 调用（回流契约不满足）")
		ok = False
	print(f"  answer: {result['answer']!r}")
	if not answer_matches(result["answer"], truth):
		print(f"  ✗ FAIL: 答案未命中 {truth}")
		ok = False
	print(f"  screenshots: {len(result['shots'])} files, valid_png={result['shots_valid']}")
	if not result["shots"] or not result["shots_valid"]:
		print("  ✗ FAIL: 截图未落盘或非合法 PNG")
		ok = False
	print(f"  conversation dump: {result['conv_dir']}")

	verdict = "PASS ✅" if ok else "FAIL ❌"
	print(f"\nvision run: {verdict}  (truth={truth})")

	# 对照跑：文本通道应答不出 swatch 颜色（跨域门禁后像素只剩视觉可达）
	if args.control and ok:
		print("\n── control run (use_vision=False) ──")
		truth2 = random.choice([c for c in COLORS if c != truth])
		html_srv2, swatch_srv2, url2 = _start_page_server(COLORS[truth2])
		print(f"ground truth color: {truth2}  (page: {url2})")
		ctrl = asyncio.run(run_once(truth2, url2, args.port, use_vision=False))
		html_srv2.shutdown()
		swatch_srv2.shutdown()
		for i, c in enumerate(ctrl["calls"]):
			print(f"  LLM call #{i}: image_in_request={c['has_image']} elapsed={c['elapsed']:.1f}s")
		print(f"  conversation dump: {ctrl['conv_dir']}")
		if any(c["has_image"] for c in ctrl["calls"]):
			print("  ✗ FAIL: use_vision=False 却带了 image block")
		elif ctrl["shots"]:
			print("  ✗ FAIL: use_vision=False 却落了截图")
		elif answer_matches(ctrl["answer"], truth2):
			print(f"  answer: {ctrl['answer']!r} → INCONCLUSIVE（瞎猜 1/5 或新旁路，看 dump）")
		else:
			print(f"  answer: {ctrl['answer']!r} → control PASS（文本通道够不到像素，图即增益）")

	return 0 if ok else 2


if __name__ == "__main__":
	sys.exit(main())
