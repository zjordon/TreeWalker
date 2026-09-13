r"""P7 探针：报表 Order Quantity 列快照空值根因（issue #184 现象③，全程只读）。

三对照定位「快照里 qty 列空、其他列有值」的根因层：

  1. 原始 DOM：qty 单元格 outerHTML（序列化前的真身）——
     DOM 有值而快照空 = 序列化层丢文本（TreeWalker/dom-snapshot bug）；
     DOM 本身空     = 服务端渲染为空（fixture/聚合数据问题，与快照无关）；
  2. uiRegistry：网格若有 mui 组件，ds.data.items 里有没有 qty（只读，不 reload）；
  3. records found 计数与 tfoot 合计行的原始 HTML（快照里这两处数值同样缺失/存在，
     与 qty 列交叉印证服务端渲染行为）。

用法（Chrome 9223 停在 bestsellers 报表页——与 debug_model_page_view 同一实例）：
  uv run python examples/p7_probe_report_qty_column.py
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, f"{__file__}/../src")

# 固定连 9223（P7 验证端口；必须在 import tree_walker.* 前设置，防 .env 的 9222 生效）
os.environ["CDP_PORT"] = "9223"

from tree_walker.browser.session import BrowserSession  # noqa: E402
from tree_walker.config import load_settings  # noqa: E402

# ── 1. 原始 DOM（决定性证据）────────────────────────────────────────────
_DOM_PROBE_JS = """
JSON.stringify((function(){
	var table = document.querySelector('.admin__data-grid-wrap table.data-grid')
	         || document.querySelector('table.data-grid');
	if (!table) { return {error: 'no grid table found'}; }
	var heads = Array.from(table.querySelectorAll('thead th')).map(function(th){
		return (th.innerText || '').trim();
	});
	var qi = -1;
	for (var i = 0; i < heads.length; i++) { if (/order quantity|qty/i.test(heads[i])) { qi = i; break; } }
	var out = {
		columns: heads,
		qty_col_index: qi,
		rows: Array.from(table.querySelectorAll('tbody tr')).slice(0, 6).map(function(tr){
			var cells = Array.from(tr.children).map(function(td){ return (td.innerText || '').trim(); });
			var qh = (qi >= 0 && tr.children[qi]) ? tr.children[qi].outerHTML.slice(0, 300) : null;
			return {cells: cells, qty_cell_html: qh};
		}),
		tfoot_html: table.querySelector('tfoot') ? table.querySelector('tfoot').outerHTML.slice(0, 500) : null
	};
	var rf = document.querySelector('.admin__data-grid-header');
	out.grid_header_text = rf ? (rf.innerText || '').trim().slice(0, 200) : null;
	return out;
})())
"""

# ── 2. uiRegistry（只读存在性检查，不 reload）───────────────────────────
_REGISTRY_PROBE_JS = """
(async function(){
	if (typeof require !== 'function') { return JSON.stringify({uiregistry: false, reason: 'no require()'}); }
	try {
		var reg = await new Promise(function(r){ require(['uiRegistry'], r); });
		var comps = [];
		reg.get(function(c){
			if (c && c.name) {
				var tag = c.name;
				try { if (c.data && c.data.items instanceof Array) { tag += ' [items=' + c.data.items.length + ']'; } } catch (e) {}
				comps.push(tag);
			}
			return false;
		});
		await new Promise(function(r){ setTimeout(r, 400); });
		return JSON.stringify({uiregistry: true, components: comps.slice(0, 40)});
	} catch (e) { return JSON.stringify({uiregistry: false, reason: String(e).slice(0, 120)}); }
})()
"""


async def main() -> int:
	settings = load_settings()
	browser = BrowserSession(settings.browser)
	await browser.start()
	sid = browser.current_session_id

	url = await browser.get_current_url()
	print(f"[probe] 当前页: {url}")
	if "bestsellers" not in url and "report" not in url:
		print("[probe] ⚠️ 当前页不是报表页——先把 Chrome 9223 停在 bestsellers 报表页")

	print("\n===== 1. 原始 DOM（qty 单元格 outerHTML）=====")
	r = await browser.client.send.Runtime.evaluate(
		{"expression": _DOM_PROBE_JS, "returnByValue": True}, session_id=sid
	)
	dom = json.loads(r["result"]["value"])
	print(json.dumps(dom, ensure_ascii=False, indent=2)[:4000])

	print("\n===== 2. uiRegistry 组件（只读）=====")
	r2 = await browser.client.send.Runtime.evaluate(
		{"expression": _REGISTRY_PROBE_JS, "returnByValue": True, "awaitPromise": True},
		session_id=sid,
	)
	print(r2["result"]["value"][:2000])

	await browser.stop()
	return 0


if __name__ == "__main__":
	sys.exit(asyncio.run(main()))
