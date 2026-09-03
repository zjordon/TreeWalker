"""P7 探针（只读）：验证 plan-tool-layer-grid-and-sorting.md 的三条技术前提。

背景：2026-08-27 工具层方案（evals workspace docs/plan-tool-layer-grid-and-sorting.md）
提议 read_grid 动作走 mui/index/render POST 通道 + uiRegistry 回落。但 8/20 与 8/26
两轮轨迹里 agent 的 mui POST 全部失败（返回 HTML/Invalid Form Key），成功案例全是
uiRegistry data source。本探针用**无状态 fetch + 只读 uiRegistry 读取**（不做
ds.set——那会写服务端书签，污染共享 admin 视图）逐项验证：

  A. sales_order_grid：form_key 来源 / uiRegistry data source 的字段面
     （data、totalRecords、params.pageSize、sorting）/ grid 组件的 sorting 属性 /
     mui render 四种请求变体（含 sorting 参数）/ DOM 行基线
  B. product_listing：mui render + search 参数（quirks.md 通道 1 的原始形态）
  C. review 网格（legacy ExtJS）：reviewGridJsObject 的 url 与 AJAX 通道

用法：uv run python examples/p7_probe_grid_channels.py [--port 9223]
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

# JS 无反斜杠，避开 evaluate 转义链路（沿用 p7_probe_themes_grid 的约定）

# 登录态检查 + 必要时登录（Magento 登录表单是普通 POST 表单，无 KO）
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

# A1/A2：form_key + uiRegistry 组件枚举 + data source / grid 组件字段面（只读）
JS_REGISTRY_READ = """
(async function(){
  var out = {};
  out.url = location.href;
  var fkInput = document.querySelector('input[name=form_key]');
  out.form_key_input = fkInput ? 'present(len=' + fkInput.value.length + ')' : null;
  out.window_FORM_KEY = (typeof window.FORM_KEY === 'string') ? 'present(len=' + window.FORM_KEY.length + ')' : null;
  try {
    var reg = await new Promise(function(r){ require(['uiRegistry'], r); });
    var names = await new Promise(function(resolve){
      var acc = [];
      reg.get(function(comp){ acc = Object.keys(comp); return; });
      setTimeout(function(){ resolve(acc); }, 600);
    });
    out.registry_names = names.filter(function(n){ return /grid|listing|data_source/i.test(n); }).slice(0, 30);
    var dsName = names.filter(function(n){ return /_data_source$/.test(n); })[0];
    var gridName = names.filter(function(n){ return /^[a-z_]+\\.[a-z_]+$/.test(n) && !/_data_source|_storage/.test(n); })[0];
    if (dsName) {
      var ds = reg.get(dsName);
      out.ds = { name: dsName,
        keys: Object.keys(ds).slice(0, 40),
        data_type: (ds.data instanceof Array) ? 'array[' + ds.data.length + ']' : typeof ds.data,
        totalRecords: (typeof ds.totalRecords !== 'undefined') ? ds.totalRecords : null,
        params_keys: ds.params ? Object.keys(ds.params).slice(0, 20) : null,
        params_paging: ds.params && ds.params.paging ? JSON.stringify(ds.params.paging) : null,
        params_sorting: ds.params && ds.params.sorting ? JSON.stringify(ds.params.sorting) : null };
      if (ds.data instanceof Array && ds.data.length) {
        out.ds.first_item_keys = Object.keys(ds.data[0]).slice(0, 25);
        out.ds.first_items = ds.data.slice(0, 5).map(function(it){
          return { id: it.entity_id || it.id || null, incr: it.increment_id || null,
                   created: it.created_at || null, status: it.status || null,
                   name: it.name || it.billing_name || null };
        });
      }
    }
    if (gridName) {
      var g = reg.get(gridName);
      out.grid = { name: gridName,
        sorting: (typeof g.sorting !== 'undefined') ? JSON.stringify(g.sorting).slice(0, 200) : null,
        keys_sample: Object.keys(g).slice(0, 40) };
    }
  } catch (e) { out.registry_error = 'Error: ' + e.message; }
  return JSON.stringify(out);
})()
"""

# A3：mui/index/render 四种变体（全部无状态 fetch，不写书签）
JS_MUI_PROBE = """
(async function(){
  var out = {};
  var fk = (document.querySelector('input[name=form_key]') || {}).value || window.FORM_KEY || '';
  var base = '/admin/mui/index/render/';
  async function call(label, method, params, headers) {
    try {
      var opt = { method: method, headers: Object.assign({'Content-Type':'application/x-www-form-urlencoded'}, headers||{}), credentials:'same-origin' };
      if (method === 'POST') { opt.body = new URLSearchParams(params).toString(); }
      var r = await fetch(base + (method==='GET' ? '?' + new URLSearchParams(params).toString() : ''), opt);
      var t = await r.text();
      var j = null; try { j = JSON.parse(t); } catch(e) {}
      var rows = null;
      if (j) {
        var cand = (j instanceof Array) ? j : (j.rows || j.data || j.items || null);
        if (cand && cand instanceof Array) rows = cand.length;
        if (rows === null) { rows = 'no-array(keys=' + Object.keys(j).slice(0,8).join(',') + ')'; }
      }
      out[label] = { status: r.status, ctype: (r.headers.get('content-type')||'').slice(0,40),
                     json: !!j, rows: rows, head: t.slice(0, 160) };
    } catch (e) { out[label] = { error: e.message }; }
  }
  await call('v1_post_min', 'POST', { form_key: fk, namespace: '%%NS%%' });
  await call('v2_post_xhr', 'POST', Object.assign({ form_key: fk, namespace: '%%NS%%', isAjax: 1 },
      { 'paging[pageSize]': 10, 'paging[current]': 1 }), { 'X-Requested-With': 'XMLHttpRequest' });
  await call('v3_post_sort', 'POST', Object.assign({ form_key: fk, namespace: '%%NS%%', isAjax: 1 },
      { 'paging[pageSize]': 5, 'paging[current]': 1, 'sorting[%%SORTFIELD%%]': 'desc', 'dir': 'desc' }),
      { 'X-Requested-With': 'XMLHttpRequest' });
  await call('v4_get', 'GET', { form_key: fk, namespace: '%%NS%%', isAjax: 1 }, { 'X-Requested-With': 'XMLHttpRequest' });
  return JSON.stringify(out);
})()
"""

# B：product_listing 的 mui search 形态（quirks.md 通道 1 原始配方）
JS_MUI_SEARCH = """
(async function(){
  var fk = (document.querySelector('input[name=form_key]') || {}).value || window.FORM_KEY || '';
  var r = await fetch('/admin/mui/index/render/', { method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-Requested-With': 'XMLHttpRequest' },
    body: new URLSearchParams({ form_key: fk, namespace: 'product_listing', search: '%%KW%%', isAjax: 1 }).toString(),
    credentials: 'same-origin' });
  var t = await r.text(); var j = null; try { j = JSON.parse(t); } catch(e) {}
  var rows = null, first = null;
  if (j) { var cand = (j instanceof Array) ? j : (j.rows || j.data || j.items || null);
    if (cand && cand.length) { rows = cand.length; first = { id: cand[0].entity_id, name: (cand[0].name||'').slice(0,40) }; }
    else { rows = 'no-array(keys=' + Object.keys(j).slice(0,8).join(',') + ')'; } }
  return JSON.stringify({ status: r.status, json: !!j, rows: rows, first: first, head: t.slice(0,160) });
})()
"""

# C：legacy 评论网格（ExtJS）：reviewGridJsObject 的 url + AJAX GET
JS_REVIEW_GRID = """
(async function(){
  var out = { hasObj: typeof window.reviewGridJsObject !== 'undefined' };
  if (!out.hasObj) return JSON.stringify(out);
  var g = window.reviewGridJsObject;
  out.obj_keys = Object.keys(g).slice(0, 25);
  out.gridId = g.gridId || null;
  out.url = g.url || null;
  var fk = (document.querySelector('input[name=form_key]') || {}).value || window.FORM_KEY || '';
  var u = g.url || (location.pathname.replace(/index.*$/, '') + 'grid');
  try {
    var sep = u.indexOf('?') >= 0 ? '&' : '?';
    var r = await fetch(u + sep + 'isAjax=true&limit=20&form_key=' + encodeURIComponent(fk),
      { headers: { 'X-Requested-With': 'XMLHttpRequest' }, credentials: 'same-origin' });
    var t = await r.text();
    var doc = new DOMParser().parseFromString(t, 'text/html');
    var trs = doc.querySelectorAll('tbody tr');
    out.ajax = { status: r.status, rows_in_html: trs.length,
      first_row: trs[0] ? (trs[0].innerText || '').trim().slice(0, 120) : null,
      len: t.length };
  } catch (e) { out.ajax = { error: e.message }; }
  return JSON.stringify(out);
})()
"""

JS_DOM_ROWS = """
(function(){
  var rows = document.querySelectorAll('.admin__data-grid-wrap table tbody tr');
  var first = rows[0] ? (rows[0].innerText || '').trim().slice(0, 100) : null;
  return JSON.stringify({ dom_rows: rows.length, first_row: first,
    info: (document.querySelector('.admin__data-grid-info')||{}).textContent || null });
})()
"""


async def eval_js(browser: BrowserSession, code: str, timeout_ms: int | None = None) -> dict:
	"""跑一段返回 JSON 字符串的 JS 并解析；失败时把原文截断带回。"""
	res = await browser.evaluate(code, timeout_ms=timeout_ms)
	raw = res if isinstance(res, str) else json.dumps(res)
	try:
		parsed = json.loads(raw) if isinstance(raw, str) else raw
		return parsed if isinstance(parsed, dict) else {"_raw": str(raw)[:400]}
	except (TypeError, ValueError):
		return {"_unparsed": str(raw)[:400]}


async def _eval(browser: BrowserSession, code: str) -> str:
	"""登录检查等非 JSON 返回的便捷封装。"""
	res = await browser.evaluate(code)
	return res if isinstance(res, str) else json.dumps(res)


async def ensure_admin(browser: BrowserSession) -> None:
	await browser.navigate("http://localhost:7780/admin/sales/order/")
	await asyncio.sleep(3)
	res = await _eval(browser, JS_LOGIN_CHECK)
	if "submitted" in str(res):
		print("[login] submitted, waiting…")
		await asyncio.sleep(6)


def show(title: str, data: dict) -> None:
	print(f"\n===== {title} =====")
	print(json.dumps(data, ensure_ascii=False, indent=1, default=str)[:2400])


async def main() -> int:
	ap = argparse.ArgumentParser(description="KO 网格数据通道可行性探针（只读）")
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
		await ensure_admin(browser)

		# ── A. sales_order_grid ──
		show("A1/A2 sales_order_grid: registry 只读字段面", await eval_js(browser, JS_REGISTRY_READ))
		show("A3 sales_order_grid: DOM 行基线", await eval_js(browser, JS_DOM_ROWS))
		mui_order = JS_MUI_PROBE.replace("%%NS%%", "sales_order_grid").replace("%%SORTFIELD%%", "created_at")
		show("A4 sales_order_grid: mui render 四变体", await eval_js(browser, mui_order, 45000))

		# ── B. product_listing ──
		await browser.navigate("http://localhost:7780/admin/catalog/product/index/")
		await asyncio.sleep(6)
		show("B1 product_listing: registry 只读字段面", await eval_js(browser, JS_REGISTRY_READ))
		show("B2 product_listing: mui search='Ingrid'", await eval_js(browser, JS_MUI_SEARCH.replace("%%KW%%", "Ingrid"), 45000))

		# ── C. review legacy 网格 ──
		await browser.navigate("http://localhost:7780/admin/review/product/index/")
		await asyncio.sleep(4)
		show("C1 review grid: legacy AJAX 通道", await eval_js(browser, JS_REVIEW_GRID, 30000))

		return 0
	finally:
		await browser.stop()


if __name__ == "__main__":
	sys.exit(asyncio.run(main()))
