"""P5 live e2e：agent history 的 select_dropdown 经「手工标注 + 变量替换」live 重放，是否真选中替换项。

夹具 = rerun-history/agent_history.json（browser-use reference-number-form，department select）。
裁成单步（navigate 由 _rerun_initial_navigation 用 state_summary.url 完成；只重放 select 动作，避开 submit 跳转），
注入手工变量 department=「Technical Support」，load_and_rerun(variables={"department":"General Information"})，
on_step 抓 select 步 ActionResult，断言回显含替换项。
设 agent.state.stopped=True 走 issue #155 快速路径，跳过结尾 summary LLM。

uv run python examples/p5_select_e2e_live.py
"""

from __future__ import annotations

import asyncio
import copy
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
CDP_PORT = 9556
FORM_URL = "https://browser-use.github.io/stress-tests/challenges/reference-number-form.html"
SRC_HIST = ROOT / "rerun-history" / "agent_history.json"
E2E_NAME = "p5_select_e2e.json"
SUBSTITUTE = "General Information"  # 表单 department 合法 option（≠ Technical Support，无特殊字符稳妥）


async def main() -> int:
	from tree_walker import Agent, BrowserSession, LLMClient
	from tree_walker.config import load_settings, _fetch_ws_url
	from tree_walker.agent.views import AgentHistoryList, ManualVariableBinding

	# ── 启 headless Chrome ──
	tmpdir = tempfile.mkdtemp(prefix="tw_e2e_")
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
		print("✗ Chrome 没起来（拿不到 ws_url）")
		proc.kill()
		return 1
	print(f"• Chrome CDP: {ws_url}")

	settings = load_settings()
	llm = LLMClient(settings.llm)  # 重放本身不调 LLM；仅构造满足 Agent 形参
	browser = BrowserSession(ws_url=ws_url)
	agent = Agent(task=FORM_URL, llm=llm, browser=browser, settings=settings.agent)

	# ── 裁 history：只留 select 步 ──
	full = AgentHistoryList.load_from_file(SRC_HIST)
	sel_item = next(it for it in full.history
	                if any(a.get("name") == "select_dropdown" for a in (it.model_output.get("actions") or [])))
	acts = sel_item.model_output.get("actions") or []
	ai = next(i for i, a in enumerate(acts) if a.get("name") == "select_dropdown")
	orig_value = acts[ai]["params"]["value"]
	print(f"• 原始 select：step={sel_item.step_number} value={orig_value!r}")

	new_item = sel_item.model_copy(deep=True)
	new_item.model_output["actions"] = [new_item.model_output["actions"][ai]]
	if "action" in new_item.model_output:
		new_item.model_output["action"] = new_item.model_output["actions"][0]
	new_item.interacted_element = [sel_item.interacted_element[ai]]  # 与单 action 对齐
	new_item.result = []
	new_item.step_number = 0
	reg_ver = agent.tools.registry.registry_version
	trimmed = AgentHistoryList(history=[new_item], action_registry_version=reg_ver)
	trimmed.manual_variables = [ManualVariableBinding(
		name="department", step_number=0, action_index=0, field="value", original_value=orig_value,
	)]
	out_path = ROOT / "rerun-history" / E2E_NAME
	trimmed.save_to_file(out_path, action_registry_version=reg_ver)
	print(f"• 裁后 history → {out_path.name}（手工变量 department={orig_value!r}）")
	print(f"• 替换目标：department → {SUBSTITUTE!r}\n")

	captured: list = []

	async def on_step(i: int, total: int, step_results: list) -> None:
		captured.extend(step_results)
		if i == total:
			agent.state.stopped = True  # 跳过结尾 summary LLM（issue #155 快速路径）

	try:
		await agent.load_and_rerun(E2E_NAME, variables={"department": SUBSTITUTE}, on_step=on_step)
	finally:
		try:
			proc.terminate()
			proc.wait(timeout=5)
		except Exception:
			proc.kill()

	# ── 断言 select 步结果 ──
	print("=== 重放 captured 结果 ===")
	for r in captured:
		print(f"  · {(r.extracted_content or r.error or '')[:140]}")
	sel_result = next((r for r in captured if "Selected option" in (r.extracted_content or "")), None)
	print(f"\n替换前 value: {orig_value!r}")
	print(f"替换后 value: {SUBSTITUTE!r}")
	if sel_result and SUBSTITUTE in (sel_result.extracted_content or ""):
		print(f"✓ LIVE E2E 通过：select 步回显 «{sel_result.extracted_content}»")
		return 0
	print(f"✗ LIVE E2E 失败：select 步未选中替换项（sel_result={sel_result})")
	return 1


if __name__ == "__main__":
	raise SystemExit(asyncio.run(main()))
