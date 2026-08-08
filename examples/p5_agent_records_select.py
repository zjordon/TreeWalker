"""确认：agent 探索原生 <select> 页面时，自动产出 ``select_dropdown`` 动作（而非 click）。

自包含：内置 ``http.server`` 托管 ``docs/p5/fixtures/native-select-fixture.html`` + 自启 headless Chrome。
给 agent 一个自然任务（从 country/province/city 三个原生下拉各选一项，不提交），跑完扫 history，
打印所有 ``select_dropdown`` 动作并断言至少一个——确认 agent 用 select_dropdown 记录原生 select。

注：nginx 未安装；但「谁来托管页面」与本确认无关（agent 只看到一个 HTTP 页），故用内置 http.server。
前置：``ZHIPU_API_KEY`` 已设（.env）。

uv run python examples/p5_agent_records_select.py
"""

from __future__ import annotations

import asyncio
import http.server
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT / "docs" / "p5" / "fixtures"
FIXTURE_FILE = "native-select-fixture.html"
OUT_NAME = "p5_agent_records_select.json"

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
CDP_PORT = 9557


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
	def __init__(self, *a, **k):
		super().__init__(*a, directory=str(FIXTURES_DIR), **k)

	def log_message(self, *a):
		pass


def _start_static_server() -> tuple[socketserver.TCPServer, int]:
	httpd = socketserver.TCPServer(("127.0.0.1", 0), _QuietHandler)
	port = httpd.server_address[1]
	threading.Thread(target=httpd.serve_forever, daemon=True).start()
	return httpd, port


async def main() -> int:
	from tree_walker import Agent, BrowserSession, LLMClient
	from tree_walker.config import load_settings, _fetch_ws_url

	httpd, port = _start_static_server()
	url = f"http://127.0.0.1:{port}/{FIXTURE_FILE}"
	print(f"• fixture: {url}")

	tmpdir = tempfile.mkdtemp(prefix="tw_agent_")
	proc = subprocess.Popen(
		[CHROME, f"--remote-debugging-port={CDP_PORT}", f"--user-data-dir={tmpdir}",
		 "--no-first-run", "--no-default-browser-check", "--headless=new",
		 "--disable-gpu", "--window-size=1024,900", "about:blank"],
		stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
	)
	ws_url = None
	for _ in range(40):
		ws_url = _fetch_ws_url("127.0.0.1", CDP_PORT)
		if ws_url:
			break
		time.sleep(0.25)
	if not ws_url:
		print("✗ Chrome 没起来")
		proc.kill()
		httpd.shutdown()
		return 1
	print(f"• Chrome CDP: {ws_url}")

	settings = load_settings()
	if not settings.llm.api_key:
		print("✗ 请设 ZHIPU_API_KEY")
		proc.kill()
		httpd.shutdown()
		return 1
	llm = LLMClient(settings.llm)
	browser = BrowserSession(ws_url=ws_url)

	task = (
		f"Open {url} in the browser. It is a test form with several native dropdown menus. "
		f"From the 'country' dropdown choose '美国', from the 'province' dropdown choose '广东省', "
		f"and from the 'city' dropdown choose '北京'. Do NOT submit the form. "
		f"Once those three are selected, report done."
	)
	agent = Agent(task=task, llm=llm, browser=browser, settings=settings.agent)
	agent.max_steps = 15  # 3 select + nav + done ≈ 数步；限上限防跑飞
	print(f"• task: {task}\n")

	try:
		history = await agent.run()
	finally:
		try:
			proc.terminate()
			proc.wait(timeout=5)
		except Exception:
			proc.kill()
		httpd.shutdown()

	# ── 扫 history：找 select_dropdown 动作 ──
	selects: list[dict] = []
	all_names: list[str] = []
	for it in history.history:
		for a in (it.model_output or {}).get("actions") or []:
			name = a.get("name")
			all_names.append(name)
			if name == "select_dropdown":
				selects.append(a.get("params") or {})

	print("=== agent 产出的动作序列 ===")
	print("  " + " -> ".join(all_names))
	print("\n=== select_dropdown 动作 ===")
	for s in selects:
		print(f"  · value={s.get('value')!r}  (index={s.get('index')})")
	if not selects:
		print("  (无)")

	out_path = ROOT / "rerun-history" / OUT_NAME
	history.save_to_file(out_path)
	print(f"\n• history 落盘: {out_path}")

	want = {"美国", "广东省", "北京"}
	got = {str(s.get("value")) for s in selects}
	hit = want & got
	print(f"\n期望 select 值 {sorted(want)}")
	print(f"实际 select 值 {sorted(got)}")
	if selects:
		print(f"\n✓ 确认：agent 对原生 <select> 产出了 {len(selects)} 个 select_dropdown 动作"
		      f"（命中 {sorted(hit)}），未退化为 click。")
		return 0
	print("\n✗ agent 未产出 select_dropdown（可能用 click 或未完成）；看上面动作序列排查。")
	return 1


if __name__ == "__main__":
	raise SystemExit(asyncio.run(main()))
