"""P7 探针：报表日期字段值丢失定位——从落值到提交，值在哪一环消失。

用户人工对照实验（2026-08-17）：同一页面手输同样日期 + 点 Show Report 出数据；
agent 的 input_text + 点按钮却查不到。本探针沿 agent 的确切机制逐步取证：

  phase=dump   From/To 输入框的 class/type/jQuery 事件/datepicker 部件实例
               ——确认 hasDatepicker 类触发 _force_set_value 路由（"datepicker"
               是 "hasDatepicker" 的子串），以及部件挂了什么监听
  phase=trace  focus → _force_set_value（agent 的真实赋值路径）→ 逐步读值：
               t0 赋值后 / t1 另一框获得焦点（触发本框 blur）后 / t2 完全失焦后
               ——钉出「值被谁在哪一步清掉」
  phase=submit 两条提交路径对照（提交后的 URL 查询串是铁证）：
               A) 同路径设值 → trusted click Show Report → 读 href/网格
               B) 同路径设值 → form.submit()          → 读 href/网格
               GET 提交的 URL 带不带 from/to，直接判别哪条路径真正带上了日期

用法：uv run python examples/p7_probe_datefield.py [--phase all] [--port 9223]
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tree_walker.browser.session import BrowserSession
from tree_walker.config import _fetch_ws_url

from p7_probe_bestsellers_ui import (
	BASE,
	DEFAULT_COOKIE_FILE,
	grid_state,
	inject_cookies,
	trusted_click,
)

JS_DUMP_FIELD = """
(function(){
	var f = document.getElementById('sales_report_from');
	if (!f) { return JSON.stringify({err: 'no from input'}); }
	var out = {cls: f.className, type: f.type, value: f.value};
	if (window.jQuery && jQuery._data){
		var ev = jQuery._data(f, 'events') || {};
		out.jqEvents = Object.keys(ev);
	}
	var btn = document.getElementById('filter_form_submit');
	if (btn){
		out.btnHtml = btn.outerHTML.slice(0, 300);
		if (window.jQuery && jQuery._data){
			var bev = jQuery._data(btn, 'events') || {};
			out.btnJqEvents = Object.keys(bev);
		}
	}
	// requirejs 部件初始化状态 + 失败的静态资源（部件死活的根因线索）
	try {
		out.btnModule = window.require ? require.defined('mage/backend/button') : 'no require';
		out.calendarModule = window.require ? require.defined('mage/calendar') : 'no require';
		out.definedCount = (require.s && require.s.contexts && require.s.contexts._)
			? Object.keys(require.s.contexts._.defined).length : -1;
	} catch (e) { out.requireErr = String(e); }
	out.failedRes = performance.getEntriesByType('resource')
		.filter(function(r){ return r.responseStatus && r.responseStatus >= 400; })
		.map(function(r){ return r.name.slice(-90) + ':' + r.responseStatus; });
	return JSON.stringify(out);
})()"""

JS_READ_VALUES = """
(function(){
	function v(id){
		var el = document.getElementById(id);
		return el ? el.value : 'NO_EL';
	}
	var f = document.getElementById('filter_form');
	var formFrom = (f && f.elements['from']) ? f.elements['from'].value : 'NO_FIELD';
	return JSON.stringify({from: v('sales_report_from'), to: v('sales_report_to'),
		form_from: formFrom, href: location.href.slice(0, 160)});
})()"""

JS_SET_BOTH = """
(function(){
	function set(id, val){
		var el = document.getElementById(id);
		if (!el) { return 'missing ' + id; }
		el.focus();
		var desc = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
		desc.set.call(el, val);
		el.dispatchEvent(new Event('input', {bubbles: true}));
		el.dispatchEvent(new Event('change', {bubbles: true}));
		return el.id + '=' + el.value;
	}
	return JSON.stringify([set('sales_report_from', '01/01/2022'),
		set('sales_report_to', '03/31/2022')]);
})()"""


async def run_dump(browser: BrowserSession) -> None:
	print("字段 dump:", await browser.evaluate(JS_DUMP_FIELD))


JS_DUMP_INITS = """
(function(){
	var inits = document.querySelectorAll('script[type="text/x-magento-init"]');
	var hits = [];
	for (var i = 0; i < inits.length; i++){
		var t = inits[i].textContent || '';
		if (t.indexOf('backendButton') >= 0 || t.indexOf('buttonId') >= 0
			|| t.indexOf('filter_form') >= 0){
			hits.push(t.slice(0, 220));
		}
	}
	var btn = document.getElementById('filter_form_submit');
	return JSON.stringify({totalInits: inits.length, hits: hits,
		btnOnclick: btn ? btn.getAttribute('onclick') : 'no btn'});
})()"""


async def run_inits(browser: BrowserSession) -> None:
	"""页面加载 8s 后 dump x-magento-init 按钮绑定脚本（等模块加载潮过去）。"""
	await browser.navigate(BASE, new_tab=False)
	await asyncio.sleep(8.0)
	print("x-magento-init dump:", await browser.evaluate(JS_DUMP_INITS))


JS_MODULE_STATE = """
(function(){
	try {
		return JSON.stringify({
			btn: require.defined('mage/backend/button'),
			cal: require.defined('mage/calendar'),
			n: Object.keys(require.s.contexts._.defined).length,
		});
	} catch (e) { return JSON.stringify({err: String(e)}); }
})()"""

# P12 watch：字段值 + 部件状态同框轮询——钉死「值被清」与「部件武装」的时刻关系
JS_WATCH = """
(function(){
	function v(id){
		var el = document.getElementById(id);
		return el ? el.value : null;
	}
	var f = document.getElementById('sales_report_from');
	var cls = f ? String(f.className) : '';
	var hasDp = cls.indexOf('datepicker') >= 0;
	var jq = [];
	if (f && window.jQuery && jQuery._data){
		var ev = jQuery._data(f, 'events') || {};
		jq = Object.keys(ev);
	}
	var popup = document.querySelector('.ui-datepicker, #calendar-popup, .datepicker');
	var popupVisible = popup ? (popup.offsetParent !== null) : false;
	var n = -1;
	try { n = Object.keys(require.s.contexts._.defined).length; } catch (e) {}
	return JSON.stringify({from: v('sales_report_from'), to: v('sales_report_to'),
		widget: hasDp, jqEvents: jq, popup: popupVisible, modules: n});
})()"""


async def run_watch(browser: BrowserSession) -> None:
	"""P12 终审：导航后每 0.5s 轮询 25s——同框记录字段值/部件类/事件/弹层/模块数。

	t≈5s 复刻 batch2_task1 Step 6 的输入时机（坐标点击聚焦 + force_set——从
	p7_probe_agentpath 导入同款助手），观测「值进 → 部件武装 → 值被清」的时刻关系。
	"""
	from p7_probe_agentpath import set_field_agent_style

	await browser.navigate(BASE, new_tab=False)
	t0 = asyncio.get_event_loop().time()
	for i in range(51):
		elapsed = asyncio.get_event_loop().time() - t0
		if i == 10:  # t≈5s：agent 式输入（batch2_task1 Step 6 的时机）
			print(">>> t≈5s 注入 agent 式输入（坐标点击聚焦 + force_set）")
			await set_field_agent_style(browser, "sales_report_from", "01/01/2022")
			await set_field_agent_style(browser, "sales_report_to", "03/31/2022")
			print(f"    输入完成于 t={asyncio.get_event_loop().time() - t0:5.1f}s")
		try:
			raw = await browser.evaluate(JS_WATCH)
			st = json.loads(raw) if isinstance(raw, str) else raw
			mark = ""
			if st.get("from") or st.get("to"):
				mark = " ←有值"
			if st.get("popup"):
				mark += " [弹层]"
			print(f"t={elapsed:5.1f}s  {json.dumps(st, ensure_ascii=False)}{mark}")
		except Exception as e:
			print(f"t={elapsed:5.1f}s  ERR {e}")
		await asyncio.sleep(0.5)


async def run_wait(browser: BrowserSession) -> None:
	"""加载后轮询 20s：mage 部件是否迟到初始化；初始化后立即点按钮验证。"""
	await browser.navigate(BASE, new_tab=False)
	for i in range(11):
		state = await browser.evaluate(JS_MODULE_STATE)
		print(f"t={i * 2:>2}s: {state}")
		if '"btn":true' in state:
			print("→ 部件已初始化！立即 JS click Show Report 验证")
			await browser.evaluate(JS_SET_BOTH)
			result = await browser.evaluate("""
(function(){
	var btn = document.getElementById('filter_form_submit');
	if (!btn) { return 'no btn'; }
	btn.click();
	return 'clicked';
})()""")
			print("click 结果:", result)
			await asyncio.sleep(3.0)
			print("点击后:", await browser.evaluate(JS_READ_VALUES))
			st = await grid_state(browser)
			print(f"网格: rows={st['rows']} first={st['first'][:50]!r}")
			return
		await asyncio.sleep(2.0)
	print("✗ 20s 内部件始终未初始化——静态初始化真的没跑，不是慢")


async def run_trace(browser: BrowserSession) -> None:
	# t0：focus from → _force_set_value（agent input_text 的真实赋值调用）
	await browser.evaluate("document.getElementById('sales_report_from').focus()")
	await browser._force_set_value("01/01/2022")
	print("t0 (from 赋值后, 仍聚焦):", await browser.evaluate(JS_READ_VALUES))

	# t1：focus to（from 失焦）→ 给 to 赋值
	await browser.evaluate("document.getElementById('sales_report_to').focus()")
	await browser._force_set_value("03/31/2022")
	print("t1 (to 赋值后, from 已 blur):", await browser.evaluate(JS_READ_VALUES))

	# t2：焦点移走（to 失焦）
	await browser.evaluate("document.body.focus()")
	print("t2 (全部失焦后):", await browser.evaluate(JS_READ_VALUES))


async def run_submit(browser: BrowserSession, mode: str) -> None:
	await browser.navigate(BASE, new_tab=False)
	await asyncio.sleep(2.0)
	print(f"[{mode}] 设值:", await browser.evaluate(JS_SET_BOTH))
	print(f"[{mode}] 设值后:", await browser.evaluate(JS_READ_VALUES))
	if mode == "click":
		# 与 agent 一致：真实 CDP 坐标点击 Show Report
		rect = json.loads(await browser.evaluate("""
(function(){
	var btns = document.querySelectorAll('button');
	var btn = null;
	for (var i = 0; i < btns.length; i++){
		if ((btns[i].textContent || '').trim() === 'Show Report'){ btn = btns[i]; break; }
	}
	if (!btn) { return JSON.stringify({found: false}); }
	btn.scrollIntoView({block: 'center'});
	var r = btn.getBoundingClientRect();
	return JSON.stringify({found: true, x: r.x, y: r.y, w: r.width, h: r.height});
})()"""))
		print(f"[click] rect: {rect}")
		await asyncio.sleep(0.5)
		if rect.get("found"):
			await trusted_click(browser, rect)
	else:
		await browser.evaluate("document.getElementById('filter_form').submit()")
	await asyncio.sleep(3.5)
	print(f"[{mode}] 提交后:", await browser.evaluate(JS_READ_VALUES))
	st = await grid_state(browser)
	print(f"[{mode}] 网格: rows={st['rows']} first={st['first'][:50]!r}")


async def main() -> int:
	ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
	ap.add_argument("--phase", choices=["dump", "inits", "trace", "submit", "wait", "watch", "all"], default="all")
	ap.add_argument("--port", type=int, default=9223)
	ap.add_argument("--cookie-file", type=Path, default=DEFAULT_COOKIE_FILE)
	args = ap.parse_args()

	ws_url = _fetch_ws_url("localhost", args.port)
	if not ws_url:
		print(f"✗ {args.port} 端口无 debug Chrome")
		return 1
	browser = BrowserSession(ws_url=ws_url)
	await browser.start()
	try:
		await inject_cookies(browser, args.cookie_file)
		await browser.navigate(BASE, new_tab=False)
		await asyncio.sleep(2.0)

		if args.phase in ("dump", "all"):
			print("\n=== phase=dump ===")
			await run_dump(browser)
		if args.phase in ("inits", "all"):
			print("\n=== phase=inits ===")
			await run_inits(browser)
		if args.phase in ("trace", "all"):
			print("\n=== phase=trace ===")
			await run_trace(browser)
		if args.phase in ("wait", "all"):
			print("\n=== phase=wait ===")
			await run_wait(browser)
		if args.phase in ("watch", "all"):
			print("\n=== phase=watch（P12 终审：字段值 × 部件状态同框轮询）===")
			await run_watch(browser)
		if args.phase in ("submit", "all"):
			print("\n=== phase=submit A（trusted click）===")
			await run_submit(browser, "click")
			print("\n=== phase=submit B（form.submit）===")
			await run_submit(browser, "js")
		return 0
	finally:
		await browser.stop()


if __name__ == "__main__":
	sys.exit(asyncio.run(main()))
