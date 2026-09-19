"""issue #193 真机探针（只读取数）：Orders 报表页的 footer 捕获 + totals-check。

背景：read_grid 新增合计行捕获（tfoot 全部 + tbody 首格为 Total 的行，挪出
rows 进 footer）与 Python 侧 column_sums 交叉校验。本探针在 107 场景
（Period=Month / 5/1/22–12/31/22 / Complete）验证三件事：

  1. 报表页 read_grid 走哪条通道（预期 dom_table——报表不是 uiRegistry 网格）；
  2. 合计行落 tfoot 还是 tbody（方案留白的唯一待真机确认项，实现两类都接住，
     此项只影响对输出的解读）；
  3. totals-check 行为：Orders 列加和 67 == footer 67（错列/漏行时输出 ✗ 与
     重读指引）。

用法：uv run python examples/debug_read_grid_totals.py [--port 9223]
（P7 红线：验证用 9223，别碰 9222）
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tree_walker.browser.session import BrowserSession
from tree_walker.config import _fetch_ws_url
from tree_walker.tools.actions import _DOM_TABLE_READ_JS, Tools

REPORT_URL = "http://localhost:7780/admin/reports/report_sales/sales/"

# 107 场景筛选（与 C 轮任务一致：5–12 月 Complete 订单按月计数）。报表筛选
# 是 GET /filter/<base64(querystring)> 整页导航——直接构造，不动表单。
FILTER_QUERY = urlencode({
	"period_type": "month",
	"from": "5/1/22",
	"to": "12/31/22",
	"show_order_statuses": "specified",
	"order_statuses[]": "complete",
})

# 合计行落点诊断（独立于 read_grid，直接看 DOM）：最大表的 tfoot/tbody 形态。
# 注意：无 args 调用走 Runtime.evaluate 表达式路径——顶层不得 return（区别于
# 带 args 被包成 function(...a){ BODY } 的 read_grid 通道模板）。
JS_TOTAL_LOCATION = """
(async function(){
	try {
		var tables = document.querySelectorAll('table');
		var best = null, bestRows = 0;
		for (var i = 0; i < tables.length; i++) {
			var n = tables[i].querySelectorAll('tbody tr').length;
			if (n > bestRows) { bestRows = n; best = tables[i]; }
		}
		if (!best) { return JSON.stringify({error: 'no-table', url: location.href}); }
		var heads = [];
		var ths = best.querySelectorAll('thead th');
		for (var j = 0; j < ths.length; j++) { heads.push((ths[j].innerText || '').trim()); }
		var tfootRows = [];
		var fts = best.querySelectorAll('tfoot tr');
		for (var k = 0; k < fts.length; k++) {
			tfootRows.push((fts[k].innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 120));
		}
		var tbodyTotalRows = [];
		var trs = best.querySelectorAll('tbody tr');
		for (var m = 0; m < trs.length; m++) {
			var td0 = trs[m].querySelector('td');
			var first = td0 ? (td0.innerText || '').trim().toLowerCase() : '';
			if (first === 'total' || first === 'totals' || first === 'grand total') {
				tbodyTotalRows.push((trs[m].innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 120));
			}
		}
		return JSON.stringify({
			url: location.href.slice(-60),
			records_found: (document.body.innerText.match(/([0-9]+) records? found/) || [null, null])[1],
			tbody_tr: trs.length,
			headers: heads,
			tfoot_tr: fts.length,
			tfoot_rows: tfootRows,
			tbody_total_rows: tbodyTotalRows,
			total_in: (fts.length ? 'tfoot' : (tbodyTotalRows.length ? 'tbody' : 'none'))
		});
	} catch (e) { return JSON.stringify({error: String(e)}); }
})()
"""


def _b64_filter_url() -> str:
	import base64
	return REPORT_URL + "filter/" + base64.b64encode(FILTER_QUERY.encode()).decode() + "/"


async def main() -> int:
	ap = argparse.ArgumentParser(description="read_grid 合计行捕获/totals-check 真机探针")
	ap.add_argument("--port", type=int, default=9223)
	args = ap.parse_args()

	logging.basicConfig(level=logging.WARNING)
	ws_url = _fetch_ws_url("localhost", args.port)
	if not ws_url:
		print(f"✗ {args.port} 端口无 debug Chrome")
		return 1
	browser = BrowserSession(ws_url=ws_url)
	await browser.start()
	try:
		# 登录态：报表页未登录会跳 login——导航后看 URL 即知
		url = _b64_filter_url()
		print(f"[1] navigate: {url}")
		await browser.navigate(url)
		await asyncio.sleep(4)
		state = await browser.get_state(include_screenshot=False)
		if "admin/admin" in state.url:  # 登录页
			print("✗ 未登录（跳到 login）——先注入/登录 admin 再跑")
			return 1

		print("\n[2] 合计行落点诊断（独立于 read_grid）")
		raw = await browser.evaluate(JS_TOTAL_LOCATION)
		print(json.dumps(json.loads(raw), ensure_ascii=False, indent=1))

		print("\n[3] Tools().execute('read_grid')——通道 + totals-check 回显")
		result = await Tools().execute("read_grid", {}, browser)
		if result.error:
			print(f"✗ read_grid error: {result.error}")
			return 1
		print(result.extracted_content)
		print(f"\n  long_term_memory: {result.long_term_memory}")

		print("\n[4] DOM 通道原始返回（footer/rows 全量，核对 Python 侧对账输入）")
		raw2 = await browser.evaluate(_DOM_TABLE_READ_JS, args=[{
			"namespace": None, "filters": None, "search": None, "sorting": None,
			"paging": {"pageSize": 200, "current": 1}, "fields": None,
			"fresh": True, "waitMs": 8000,
		}], await_promise=True)
		parsed = json.loads(raw2) if isinstance(raw2, str) else raw2
		print(json.dumps({
			"channel": parsed.get("channel"),
			"rows": parsed.get("rows"),
			"footer": parsed.get("footer"),
			"headers": parsed.get("headers"),
		}, ensure_ascii=False, indent=1))

		print("\n[5] 判读：[2] 的 total_in 回答 tfoot/tbody 落点；[3] 应含"
			" 'Orders: sum 67 == footer 67'（出厂真值 8/13/9/8/10/4/5/10）")
		return 0
	finally:
		await browser.stop()


if __name__ == "__main__":
	sys.exit(asyncio.run(main()))
