r"""P7 复刻：batch1_task1.log Step 3 五动作序列在当前页面原样重放（生产代码路径）。

用户已把 bestsellers 报表页开在 9223（人工在场观察）。本脚本【不导航、不注入
cookie】，只附加到当前页，按日志 Step 3 的动作序列用 TreeWalker 生产 Tools 动作
重放（与 agent 同一执行路径）：
  1. select_dropdown  Period=Month
  2. input_text       From=01/01/2022 (clear=True)
  3. input_text       To=03/31/2022   (clear=True)
  4. select_dropdown  Empty Rows=No
  5. click            Show Report

索引按元素 id 从 get_state() selector_map 现场解析（日志里的 2023/2024/2025/2041
是当时快照索引，跨加载不通用）。每步打印回读信号；点击后报告关键判别信号——
回显格式（提交成功服务端会把 01/01/2022 重格式化为 1/1/22）与 URL /filter/ 段。

用法：uv run python examples/p7_replay_batch1_step3.py
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tree_walker import BrowserSession
from tree_walker.tools.actions import Tools

PROBE = """(function(){
	var from = document.getElementById('sales_report_from');
	var to = document.getElementById('sales_report_to');
	var tbody = document.querySelector('.admin__data-grid-wrap tbody, table tbody');
	var bodyTxt = document.body.innerText;
	var widget = from ? (from.className.indexOf('datepicker') >= 0) : null;
	var fnType = 'n/a';
	var modCount = -1;
	try {
		fnType = typeof window.filterFormSubmit;
		modCount = (require.s && require.s.contexts._) ? Object.keys(require.s.contexts._.defined).length : -1;
	} catch (e) {}
	return JSON.stringify({
		fromValue: from ? from.value : null,
		toValue: to ? to.value : null,
		fromInvalid: from ? from.getAttribute('aria-invalid') : null,
		toInvalid: to ? to.getAttribute('aria-invalid') : null,
		widgetOnField: widget,
		filterFormSubmit: fnType,
		modules: modCount,
		url: location.href.slice(0, 130),
		rowCount: tbody ? tbody.querySelectorAll('tr').length : 0,
		firstRow: tbody && tbody.querySelector('tr') ? tbody.querySelector('tr').innerText.slice(0, 70) : '',
		emptyMsg: bodyTxt.indexOf('find any records') >= 0 || bodyTxt.indexOf("can't find records") >= 0,
	});
})()"""


async def probe(browser, tag):
	raw = await browser.evaluate(PROBE)
	st = json.loads(raw) if isinstance(raw, str) else raw
	print(f"  [{tag}] {json.dumps(st, ensure_ascii=False)}")
	return st or {}


async def find_indexes(browser):
	"""按 id 从当前快照解析五动作的目标索引（对齐 zcode test_treewalker_date.py）。"""
	state = await browser.get_state(include_screenshot=False)
	smap = state.dom_state.selector_map
	want = {}
	for idx, entry in smap.items():
		attrs = getattr(entry, "attributes", {}) or {}
		eid = attrs.get("id", "")
		if eid == "sales_report_from":
			want["from"] = idx
		elif eid == "sales_report_to":
			want["to"] = idx
		elif eid == "filter_form_submit":
			want["btn"] = idx
		elif eid.startswith("sales_report_period"):
			want["period"] = idx
		elif eid.startswith("sales_report_show_em"):
			want["empty"] = idx
	return want


async def main() -> int:
	from tree_walker.config import _fetch_ws_url

	import argparse
	ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
	ap.add_argument("--navigate", action="store_true",
		help="从当前（空白）页用生产 _action_navigate 导航到报表页后再执行（复刻 8/17 全序列）")
	ap.add_argument("--llm-gap", type=float, default=15.0,
		help="导航后模拟 LLM 决策间隔的秒数（日志 Step 2→3 实际 ~16s，默认 15）")
	args = ap.parse_args()

	ws_url = _fetch_ws_url("localhost", 9223)
	if not ws_url:
		print("✗ 9223 无 debug Chrome")
		return 1
	browser = BrowserSession(ws_url=ws_url)
	await browser.start()
	try:
		href = await browser.evaluate("location.href")
		print(f"当前页面: {href[:110]}")

		tools = Tools()

		if args.navigate:
			# 复刻日志 Step 2：生产 _action_navigate 导航
			r = await tools._action_navigate(
				{"url": "http://localhost:7780/admin/reports/report_sales/bestsellers/"}, browser)
			print(f"0/5 _action_navigate → {r.extracted_content or r.error}")
			await probe(browser, "导航后即刻")
			print(f"（模拟 LLM 间隔 {args.llm_gap}s……）")
			await asyncio.sleep(args.llm_gap)
			await probe(browser, f"导航后 +{args.llm_gap:.0f}s")
		elif "bestsellers" not in href:
			print("⚠️ 当前标签不在 bestsellers 报表页——请切到目标标签或加 --navigate 重跑")
			return 1
		idx = await find_indexes(browser)
		print(f"解析索引: {idx}")
		missing = [k for k in ("from", "to", "period", "empty", "btn") if k not in idx]
		if missing:
			print(f"⚠️ 快照缺元素: {missing}（zcode 发现过 headless 下按钮不进快照）")

		await probe(browser, "执行前")

		# ── Step 3 五动作，按日志顺序，生产代码路径 ──
		if "period" in idx:
			r = await tools._action_select_dropdown({"index": idx["period"], "value": "Month"}, browser)
			print(f"1/5 select_dropdown Period=Month → {r.extracted_content or r.error}")
		if "from" in idx:
			r = await tools._action_input_text(
				{"index": idx["from"], "text": "01/01/2022", "clear": True}, browser)
			print(f"2/5 input_text From=01/01/2022 → {r.extracted_content or r.error}")
		if "to" in idx:
			r = await tools._action_input_text(
				{"index": idx["to"], "text": "03/31/2022", "clear": True}, browser)
			print(f"3/5 input_text To=03/31/2022 → {r.extracted_content or r.error}")
		if "empty" in idx:
			r = await tools._action_select_dropdown({"index": idx["empty"], "value": "No"}, browser)
			print(f"4/5 select_dropdown EmptyRows=No → {r.extracted_content or r.error}")
		await probe(browser, "提交前")
		if "btn" in idx:
			r = await tools._action_click({"index": idx["btn"]}, browser)
			print(f"5/5 click Show Report → {r.extracted_content or r.error}")
		else:
			print("5/5 ⚠️ 按钮不在快照——无法用生产 click（偏离复刻，此处停住）")
			return 1

		await asyncio.sleep(3.0)
		st = await probe(browser, "提交后")
		submitted = "/filter/" in st.get("url", "")
		echoed = st.get("fromValue") in ("1/1/22", "01/01/22") or st.get("toValue") in ("3/31/22", "03/31/22")
		has_rows = st.get("rowCount", 0) > 1 and not st.get("emptyMsg")
		print(f"\n=== 判定 ===")
		print(f"URL 带 /filter/ 段 : {'✓' if submitted else '✗'}（提交发生与否）")
		print(f"日期被服务端重格式化: {'✓' if echoed else '✗'}（回显信号）")
		print(f"表格有数据行       : {'✓' if has_rows else '✗'}（rowCount={st.get('rowCount')}）")
		return 0
	finally:
		await browser.stop()


if __name__ == "__main__":
	sys.exit(asyncio.run(main()))
