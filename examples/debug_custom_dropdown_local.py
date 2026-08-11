"""本地验证 issue #160 修复：fixture 复刻 3 种自定义下拉拓扑，headless 跑探针 + 断言兜底成功。

3 种拓扑（fixture）：创作声明(input+同父 `<li>`)、分区(div+兄弟 `<div title>`)、portal(div+body
末尾 `[role=option]`，复刻抖音 Semi UI)。修复后 ``fetch_dropdown_options`` 闭态判型仍全 miss
（source=None，零回归），但 action 层兜底让 ``dropdown_options`` / ``select_dropdown`` 成功。

复用 ``debug_custom_dropdown_classify.test_trigger`` 的五步诊断；额外对修复判据 assert。

用法：uv run python examples/debug_custom_dropdown_local.py
"""

import asyncio
import http.server
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, f"{__file__}/../src")
sys.path.insert(0, f"{__file__}/..")  # 为了 import 同目录的 debug_custom_dropdown_classify

import debug_custom_dropdown_classify as probe
from tree_walker.browser.session import BrowserSession
from tree_walker.config import _fetch_ws_url
from tree_walker.tools.actions import Tools

ROOT = Path(__file__).resolve().parent.parent
FIX_DIR = ROOT / "docs" / "p5" / "fixtures"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
CDP_PORT = 9561


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
	def __init__(self, *a, **k):
		super().__init__(*a, directory=str(FIX_DIR), **k)

	def log_message(self, *a):
		pass


def _serve():
	httpd = socketserver.TCPServer(("127.0.0.1", 0), _QuietHandler)
	port = httpd.server_address[1]
	threading.Thread(target=httpd.serve_forever, daemon=True).start()
	return httpd, port


def _find_by_attr(smap, key, val):
	"""在 selector_map 里按 attributes[key]==val 找第一个 (idx, node)。"""
	for idx, node in smap.items():
		attrs = getattr(node, "attributes", {}) or {}
		if attrs.get(key) == val:
			return idx, node
	return None, None


async def main():
	httpd, port = _serve()
	url = f"http://127.0.0.1:{port}/custom-dropdown-fixture.html"
	print(f"• fixture: {url}")

	tmpdir = tempfile.mkdtemp(prefix="tw_dropdown_")
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
		print("✗ Chrome 没起来"); proc.kill(); httpd.shutdown(); sys.exit(1)
	print(f"• Chrome CDP: {ws_url}")

	browser = BrowserSession(ws_url=ws_url)
	await browser.start()
	tools = Tools()
	results = {}
	try:
		await browser.navigate(url)
		await asyncio.sleep(0.8)  # 等 DOM 就绪

		state = await browser.get_state(include_screenshot=False)
		smap = state.dom_state.selector_map if state.dom_state else {}
		print(f"• 页面: {state.url}  selector_map 条目: {len(smap)}")

		# 定位三个触发器（3 拓扑：创作声明 input+同父 li / 分区 div+兄弟 div[title] / portal div+body role=option）
		czsm_cands = probe.find_trigger_candidates(smap)
		czsm_idx = czsm_cands[0][0] if czsm_cands else None
		fq_idx, _ = _find_by_attr(smap, "id", "fq-trigger")
		portal_idx, _ = _find_by_attr(smap, "id", "portal-trigger")
		print(f"• czsm={czsm_idx}  fq={fq_idx}  portal={portal_idx}")

		if czsm_idx is not None:
			results["czsm"] = await probe.test_trigger(
				tools, browser, browser.current_session_id, czsm_idx, "含AI生成内容", "fixture/创作声明",
			)
		if fq_idx is not None:
			results["fq"] = await probe.test_trigger(
				tools, browser, browser.current_session_id, fq_idx, "娱乐", "fixture/分区",
			)
		if portal_idx is not None:
			results["portal"] = await probe.test_trigger(
				tools, browser, browser.current_session_id, portal_idx, "科技", "fixture/portal",
			)
	finally:
		await browser.stop()
		try:
			proc.terminate(); proc.wait(timeout=5)
		except Exception:
			proc.kill()
		httpd.shutdown()

	# ── 断言修复生效 ──
	print("\n" + "═" * 72)
	print("修复判据（每条拓扑都应满足）:")
	all_ok = True
	for name, r in results.items():
		if not r:
			continue
		checks = {
			"闭态判型仍 source=None（未动闭态 dispatcher，零回归）": r["src_closed"] is None,
			"dropdown_options 兜底成功（无 error）": not r["dropdown_options_error"],
			"select_dropdown 兜底成功（选中）": r["select_ok"],
		}
		print(f"\n  [{name}] <{r['tag']}>  dropdown_options_err={r['dropdown_options_error']!r}  select_ok={r['select_ok']}")
		for label, ok in checks.items():
			all_ok = all_ok and ok
			print(f"    {'✓' if ok else '✗'} {label}")

	print("\n" + ("✅ 修复生效：3 拓扑自定义下拉 select_dropdown 兜底成功。" if all_ok
	              else "⚠ 部分判据未达预期——看上面输出排查（fixture 形态/discovery/兜底逻辑）。"))
	return 0 if all_ok else 1


if __name__ == "__main__":
	raise SystemExit(asyncio.run(main()))
