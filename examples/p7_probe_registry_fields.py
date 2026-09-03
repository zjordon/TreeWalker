"""P7 探针补丁：uiRegistry 组件枚举与字段面（p7_probe_grid_channels 的追加验证）。

修正点：枚举应收集 `c.name`（组件名），不是 Object.keys(组件)。另外直接按已知
名字读 sales_order_grid / product_listing 的 data source 与 grid 组件，dump：
data 形状 / totalRecords / params(paging,sorting) / grid.sorting —— 这是
plan-tool-layer-grid-and-sorting.md §2.2「快照网格元信息」的字段依据。

只读：不做 ds.set（防书签污染）。用法：
  uv run python examples/p7_probe_registry_fields.py [--port 9223]
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tree_walker.browser.session import BrowserSession
from tree_walker.config import _fetch_ws_url

JS_LOGIN_CHECK = """
(async function(){
  if (document.getElementById('username')) {
    var u = document.getElementById('username'), p = document.getElementById('login');
    u.value = 'admin'; p.value = 'admin1234';
    u.dispatchEvent(new Event('input', {bubbles:true}));
    p.dispatchEvent(new Event('input', {bubbles:true}));
    document.getElementById('login-form').submit();
    return 'submitted';
  }
  return 'already-logged-in:' + location.href.slice(0, 80);
})()
"""

# 枚举组件名（c.name）+ 按名字直读 ds / grid 组件的字段面
JS_REGISTRY_FIELDS = """
(async function(){
  var out = { url: location.href };
  try {
    var reg = await new Promise(function(r){ require(['uiRegistry'], r); });
    var names = [];
    try { reg.get(function(c){ if (c && c.name) { names.push(c.name); } return false; }); } catch(e) { out.enum_error = e.message; }
    out.all_names = names.slice(0, 40);
    var main = names.filter(function(n){ return n.indexOf('notification_area') !== 0; })[0].split('.')[0];
    out.main_ns = main;
    var targets = names.filter(function(n){ return n.indexOf(main + '.') === 0; })
      .filter(function(n){ return /_data_source$|_data_source_storage$/.test(n) || /^[a-z_]+\\.[a-z_]+$/.test(n); })
      .slice(0, 6);
    out.targets = targets;
    out.comps = [];
    for (var i = 0; i < targets.length; i++) {
      try {
        var c = await new Promise(function(resolve){ reg.get(targets[i], resolve); setTimeout(function(){ resolve(null); }, 1500); });
        if (!c) { out.comps.push({ name: targets[i], timeout: true }); continue; }
        var info = { name: targets[i], type: c.constructor.name || null };
        if (c.data !== undefined) {
          info.data = (c.data instanceof Array) ? 'array[' + c.data.length + ']'
            : (c.data && typeof c.data === 'object') ? 'obj(' + Object.keys(c.data).slice(0,6).join(',') + ')' : String(c.data).slice(0,40);
          if (c.data instanceof Array && c.data.length) { info.first_item_keys = Object.keys(c.data[0]).slice(0, 22); }
        }
        if (c.totalRecords !== undefined) { info.totalRecords = c.totalRecords; }
        if (c.params) {
          info.params_keys = Object.keys(c.params).slice(0, 16);
          if (c.params.paging) { info.params_paging = JSON.stringify(c.params.paging).slice(0, 200); }
          if (c.params.sorting) { info.params_sorting = JSON.stringify(c.params.sorting).slice(0, 200); }
        }
        if (c.sorting !== undefined) { info.sorting = JSON.stringify(c.sorting).slice(0, 200); }
        if (c.applied !== undefined) { info.applied = JSON.stringify(c.applied).slice(0, 160); }
        out.comps.push(info);
      } catch (e) { out.comps.push({ name: targets[i], error: e.message }); }
    }
  } catch (e) { out.error = e.message; }
  return JSON.stringify(out);
})()
"""

# 当前 data source 前 5 行（看实际行序，验证 128 的乱序问题 + 书签过滤残留）
JS_FIRST_ROWS = """
(async function(){
  var out = {};
  try {
    var reg = await new Promise(function(r){ require(['uiRegistry'], r); });
    var names = [];
    reg.get(function(c){ if (c && c.name) { names.push(c.name); } return false; });
    var main = names.filter(function(n){ return n.indexOf('notification_area') !== 0; })[0].split('.')[0];
    var dsName = names.filter(function(n){ return n === main + '.' + main + '_data_source'; })[0];
    if (!dsName) { return JSON.stringify({ no_ds: true, names: names.slice(0,10) }); }
    var ds = await new Promise(function(resolve){ reg.get(dsName, resolve); setTimeout(function(){ resolve(null); }, 1500); });
    if (!ds || !ds.data) { return JSON.stringify({ ds: dsName, no_data: true }); }
    out.ds = dsName;
    out.data_keys = Object.keys(ds.data).slice(0, 8);
    out.totalRecords = ds.data.totalRecords !== undefined ? ds.data.totalRecords : null;
    out.showTotalRecords = ds.data.showTotalRecords !== undefined ? String(ds.data.showTotalRecords).slice(0, 40) : null;
    out.params_filters = ds.params && ds.params.filters ? JSON.stringify(ds.params.filters).slice(0, 200) : null;
    var items = ds.data.items;
    if (items instanceof Array) {
      out.n = items.length;
      out.rows = items.slice(0, 6).map(function(it){
        return { id: it.entity_id || null, incr: it.increment_id || null,
                 created: it.created_at || null, status: it.status || null };
      });
    }
  } catch (e) { out.error = e.message; }
  return JSON.stringify(out);
})()
"""


async def eval_js(browser: BrowserSession, code: str) -> dict:
	res = await browser.evaluate(code, timeout_ms=25000)
	try:
		parsed = json.loads(res) if isinstance(res, str) else res
		return parsed if isinstance(parsed, dict) else {"_raw": str(res)[:400]}
	except (TypeError, ValueError):
		return {"_unparsed": str(res)[:400]}


def show(title: str, data: dict) -> None:
	print(f"\n===== {title} =====")
	print(json.dumps(data, ensure_ascii=False, indent=1, default=str)[:6500])


async def main() -> int:
	ap = argparse.ArgumentParser()
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
		await browser.navigate("http://localhost:7780/admin/sales/order/")
		await asyncio.sleep(3)
		res = await browser.evaluate(JS_LOGIN_CHECK)
		if "submitted" in str(res):
			print("[login] submitted…")
			await asyncio.sleep(6)
		show("sales_order_grid: registry 字段面", await eval_js(browser, JS_REGISTRY_FIELDS))
		show("sales_order_grid: ds.data.items 前 6 行 + totalRecords + filters", await eval_js(browser, JS_FIRST_ROWS))

		await browser.navigate("http://localhost:7780/admin/catalog/product/index/")
		await asyncio.sleep(6)
		show("product_listing: registry 字段面", await eval_js(browser, JS_REGISTRY_FIELDS))
		show("product_listing: ds.data.items 前 4 行", await eval_js(browser, JS_FIRST_ROWS))
		return 0
	finally:
		await browser.stop()


if __name__ == "__main__":
	sys.exit(asyncio.run(main()))
