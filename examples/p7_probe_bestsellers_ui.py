"""P7 UI 探针（自包含）：bestsellers 报表筛选链路排查（Chrome 9223 专用实例）。

背景：docs/p7/01-task1-trajectory-anatomy.md 附二记录的环境谜团——bestsellers 页
的 Show Report 按钮在全新浏览器会话里没有任何监听，所有复刻 agent 操作的路径
（JS 设值/事件序列/trusted click/URL 直参数）都无法让网格出数据。本探针把当时
的排查手段合并成一个可复用工具：

  phase=dump    表单/按钮/jQuery 事件/委托事件 dump（为什么按钮无监听）
  phase=click   hook XHR/fetch/导航 → JS 设值+富事件 → trusted click → 观察
  phase=url     URL 直参数枚举（store_ids × 日期格式）
  phase=all     依次全部（默认）

用法：uv run python examples/p7_probe_bestsellers_ui.py [--phase all] [--port 9223]
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tree_walker.browser.session import BrowserSession
from tree_walker.config import _fetch_ws_url

DEFAULT_COOKIE_FILE = Path(
	r"D:\dev\git\z_jordon\evals\webarena\webarena_repo\.auth\shopping_admin_state.json"
)
BASE = "http://localhost:7780/admin/reports/report_sales/bestsellers/"

JS_GRID_STATE = """
(function(){
	var rows = document.querySelectorAll('.admin__data-grid-wrap table tbody tr');
	var info = document.querySelector('.admin__data-grid-info');
	var pager = document.querySelector('.admin__data-grid-pager');
	return JSON.stringify({
		rows: rows.length,
		first: rows.length ? (rows[0].innerText || '').trim().slice(0, 80) : '',
		info: info ? info.textContent.trim() : '',
		pager: pager ? pager.textContent.trim() : ''
	});
})()"""

JS_SET_DATES = """
(function(){
	var f = document.getElementById('sales_report_from');
	var t = document.getElementById('sales_report_to');
	if (!f || !t) { return JSON.stringify({ok: false, err: 'date inputs not found'}); }
	function setv(el, v){
		el.value = v;
		var evs = ['input', 'change', 'keyup', 'blur'];
		for (var i = 0; i < evs.length; i++){ el.dispatchEvent(new Event(evs[i], {bubbles: true})); }
	}
	setv(f, FROM); setv(t, TO);
	function vstate(el){
		var wrap = el.closest('.admin__field');
		var err = '';
		if (wrap){
			var e = wrap.querySelector('.admin__field-error, ._error');
			if (e) { err = e.textContent.trim(); }
		}
		return {value: el.value, invalid: el.getAttribute('aria-invalid') === 'true', err: err};
	}
	return JSON.stringify({ok: true, from: vstate(f), to: vstate(t)});
})()"""

JS_FIND_BTN_RECT = """
(function(){
	var btns = document.querySelectorAll('button');
	var btn = null;
	for (var i = 0; i < btns.length; i++){
		if ((btns[i].textContent || '').trim() === 'Show Report'){ btn = btns[i]; break; }
	}
	if (!btn) { return JSON.stringify({found: false}); }
	var ev = (window.jQuery && jQuery._data) ? (jQuery._data(btn, 'events') || {}) : {};
	var evk = [];
	for (var k in ev){ evk.push(k + 'x' + ev[k].length); }
	var f = document.getElementById('filter_form');
	var fstate = null;
	if (f){
		fstate = {action: f.action.slice(0, 60),
			from: f.elements['from'] ? f.elements['from'].value : null,
			to: f.elements['to'] ? f.elements['to'].value : null};
	}
	btn.scrollIntoView({block: 'center'});
	var r = btn.getBoundingClientRect();
	return JSON.stringify({found: true, x: r.x, y: r.y, w: r.width, h: r.height,
		id: btn.id, events: evk, form: fstate});
})()"""

JS_HOOK = """
(function(){
	window.__reqs = [];
	window.__nav = location.href;
	var xo = XMLHttpRequest.prototype.open;
	XMLHttpRequest.prototype.open = function(m, u){
		window.__reqs.push(m + ' ' + String(u).slice(0, 120));
		return xo.apply(this, arguments);
	};
	if (window.fetch){
		var ff = window.fetch;
		window.fetch = function(){
			window.__reqs.push('FETCH ' + String(arguments[0]).slice(0, 120));
			return ff.apply(this, arguments);
		};
	}
	var de = (window.jQuery && jQuery._data) ? (jQuery._data(document, 'events') || {}) : {};
	var dk = [];
	for (var k in de){ dk.push(k + 'x' + de[k].length); }
	return JSON.stringify({docEvents: dk, jquery: window.jQuery ? jQuery.fn.jquery : null});
})()"""

JS_REPORT = """
(function(){
	return JSON.stringify({reqs: (window.__reqs || []).slice(0, 15),
		nav_changed: window.__nav !== location.href, href: location.href.slice(0, 120)});
})()"""


async def inject_cookies(browser: BrowserSession, cookie_file: Path) -> int:
	"""Playwright storage-state → CDP Network.setCookie（url 参数绑定作用域）。

	⚠️ localhost 的 domain cookie 假成功坑：Network.setCookie 必须用 url 参数，
	domain="localhost" 会返回 success:true 但 cookie 不进 jar。
	"""
	cookies = json.loads(cookie_file.read_text(encoding="utf-8")).get("cookies", [])
	sid = browser.current_session_id
	n_ok = 0
	for c in cookies:
		scheme = "https" if c.get("secure") else "http"
		domain = c.get("domain", "") or ""
		path = c.get("path", "/") or "/"
		params = {
			"name": c["name"], "value": c["value"], "path": path,
			"secure": c.get("secure", False), "httpOnly": c.get("httpOnly", False),
			"sameSite": {"Strict": "Strict", "Lax": "Lax", "None": "None"}.get(
				c.get("sameSite", "Lax"), "Lax"),
			"url": c.get("url") or f"{scheme}://{domain or 'localhost'}{path}",
		}
		expires = c.get("expires", -1)
		if isinstance(expires, (int, float)) and expires > 0:
			params["expires"] = expires
		result = await browser.client.send.Network.setCookie(params, session_id=sid)
		if result.get("success", True):
			n_ok += 1
	print(f"cookie 注入 {n_ok}/{len(cookies)}")
	return n_ok


async def grid_state(browser: BrowserSession) -> dict:
	return json.loads(await browser.evaluate(JS_GRID_STATE))


async def trusted_click(browser: BrowserSession, rect: dict) -> None:
	"""CDP Input.dispatchMouseEvent 打元素中心——trusted click。"""
	sid = browser.current_session_id
	cx, cy = rect["x"] + rect["w"] / 2, rect["y"] + rect["h"] / 2
	for typ in ("mousePressed", "mouseReleased"):
		await browser.client.send.Input.dispatchMouseEvent(
			{"type": typ, "x": cx, "y": cy, "button": "left", "clickCount": 1},
			session_id=sid,
		)


async def phase_dump(browser: BrowserSession) -> None:
	print("hook:", await browser.evaluate(JS_HOOK))
	print("按钮/表单:", await browser.evaluate(JS_FIND_BTN_RECT))


async def phase_click(browser: BrowserSession, date_from: str, date_to: str) -> None:
	print("设值:", await browser.evaluate(
		"var FROM = " + json.dumps(date_from) + ";\nvar TO = " + json.dumps(date_to) + ";\n" + JS_SET_DATES
	))
	rect = json.loads(await browser.evaluate(JS_FIND_BTN_RECT))
	print(f"rect: {rect}")
	await asyncio.sleep(0.5)
	await trusted_click(browser, rect)
	print("→ trusted click 已发")
	await asyncio.sleep(4.0)
	print(f"点击后: {await browser.evaluate(JS_REPORT)}")
	print(f"网格: {await grid_state(browser)}")


async def phase_url(browser: BrowserSession) -> None:
	candidates = [
		{"store_ids": "", "period_type": "day", "from": "01/01/2022", "to": "03/31/2022"},
		{"store_ids": "0", "period_type": "day", "from": "01/01/2022", "to": "03/31/2022"},
		{"store_ids": "1", "period_type": "day", "from": "01/01/2022", "to": "03/31/2022"},
		{"store_ids": "0", "period_type": "day", "from": "2022-01-01", "to": "2022-03-31"},
	]
	for c in candidates:
		await browser.navigate(BASE + "?" + urlencode(c), new_tab=False)
		await asyncio.sleep(3.0)
		st = await grid_state(browser)
		mark = "✓✓✓" if st["rows"] > 1 else "  "
		print(f"{mark} {c} → rows={st['rows']} {st['first'][:40]!r}")


async def main() -> int:
	ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
	ap.add_argument("--phase", choices=["dump", "click", "url", "all"], default="all")
	ap.add_argument("--port", type=int, default=9223)
	ap.add_argument("--cookie-file", type=Path, default=DEFAULT_COOKIE_FILE)
	ap.add_argument("--date-from", default="01/01/2022")
	ap.add_argument("--date-to", default="03/31/2022")
	args = ap.parse_args()

	ws_url = _fetch_ws_url("localhost", args.port)
	if not ws_url:
		print(f"✗ {args.port} 端口无 debug Chrome")
		return 1
	browser = BrowserSession(ws_url=ws_url)
	await browser.start()
	try:
		await inject_cookies(browser, args.cookie_file)
		phases = [args.phase] if args.phase != "all" else ["dump", "click", "url"]
		for phase in phases:
			print(f"\n=== phase={phase} ===")
			await browser.navigate(BASE, new_tab=False)
			await asyncio.sleep(2.0)
			if phase == "dump":
				await phase_dump(browser)
			elif phase == "click":
				await phase_click(browser, args.date_from, args.date_to)
			elif phase == "url":
				await phase_url(browser)
		return 0
	finally:
		await browser.stop()


if __name__ == "__main__":
	sys.exit(asyncio.run(main()))
