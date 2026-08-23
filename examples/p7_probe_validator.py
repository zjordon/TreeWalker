"""P7 探针（最后一公里）：jQuery validate 的 errorList——到底哪个字段在拦提交。

已定位：点击→filterFormSubmit→valid() false→静默不提交。本探针设值后读取
validate().errorList 的具体字段与错误消息，并对照两种日期格式（4 位年
'01/01/2022' vs Magento 自序列化的短格式 '1/1/22'——/filter/ 页预填的就是后者
且能通过）。

用法：uv run python examples/p7_probe_validator.py
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tree_walker.browser.session import BrowserSession
from tree_walker.config import _fetch_ws_url

from p7_probe_bestsellers_ui import BASE, DEFAULT_COOKIE_FILE, inject_cookies


async def check(browser: BrowserSession, from_val: str, to_val: str) -> None:
	"""设值 → valid() → errorList → 尝试提交。"""
	await browser.evaluate("""
(function(){
	function set(id, val){
		var el = document.getElementById(id);
		if (!el) { return 'missing ' + id; }
		var desc = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
		desc.set.call(el, val);
		el.dispatchEvent(new Event('input', {bubbles: true}));
		el.dispatchEvent(new Event('change', {bubbles: true}));
	}
	set('sales_report_from', FROM);
	set('sales_report_to', TO);
})()""".replace("FROM", json.dumps(from_val)).replace("TO", json.dumps(to_val)))
	res = await browser.evaluate("""
(function(){
	var validator = jQuery('#filter_form').validate();
	var ok = validator.form();
	var errs = (validator.errorList || []).map(function(e){
		return (e.element && (e.element.name || e.element.id)) + ' ← ' + e.message;
	});
	return JSON.stringify({valid: ok, errors: errs});
})()""")
	print(f"  from={from_val!r} to={to_val!r} → {res}")
	try:
		await browser.evaluate("window.filterFormSubmit()")
	except Exception as e:
		print(f"  filterFormSubmit 异常: {e}")
	await asyncio.sleep(2.5)
	href = await browser.evaluate("location.href")
	submitted = "/filter/" in href
	print(f"  提交: {'✓ YES' if submitted else '✗ no'}  href={href[:100]}")


async def main() -> int:
	ws_url = _fetch_ws_url("localhost", 9223)
	if not ws_url:
		print("✗ 9223 无 debug Chrome")
		return 1
	browser = BrowserSession(ws_url=ws_url)
	await browser.start()
	try:
		await inject_cookies(browser, DEFAULT_COOKIE_FILE)

		print("实验 1：agent 用的 4 位年格式")
		await browser.navigate(BASE, new_tab=False)
		await asyncio.sleep(8)
		await check(browser, "01/01/2022", "03/31/2022")

		print("\n实验 2：Magento 短格式（/filter/ 页预填的那种）")
		await browser.navigate(BASE, new_tab=False)
		await asyncio.sleep(8)
		await check(browser, "1/1/22", "3/31/22")

		print("\n实验 3：同样条件但用 trusted click（早前死点击的最终对照）")
		await browser.navigate(BASE, new_tab=False)
		await asyncio.sleep(8)
		await browser.evaluate("""
(function(){
	function set(id, val){
		var el = document.getElementById(id);
		var desc = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
		desc.set.call(el, val);
		el.dispatchEvent(new Event('input', {bubbles: true}));
		el.dispatchEvent(new Event('change', {bubbles: true}));
	}
	set('sales_report_from', '01/01/2022');
	set('sales_report_to', '03/31/2022');
})()""")
		rect = json.loads(await browser.evaluate("""
(function(){
	var btns = document.querySelectorAll('button');
	var btn = null;
	for (var i = 0; i < btns.length; i++){
		if ((btns[i].textContent || '').trim() === 'Show Report'){ btn = btns[i]; break; }
	}
	btn.scrollIntoView({block: 'center'});
	var r = btn.getBoundingClientRect();
	return JSON.stringify({x: r.x, y: r.y, w: r.width, h: r.height});
})()"""))
		from p7_probe_bestsellers_ui import trusted_click
		await asyncio.sleep(0.5)
		await trusted_click(browser, rect)
		await asyncio.sleep(3.0)
		href = await browser.evaluate("location.href")
		print(f"  trusted click 提交: {'✓ YES' if '/filter/' in href else '✗ no'}  href={href[:100]}")
		return 0
	finally:
		await browser.stop()


if __name__ == "__main__":
	sys.exit(asyncio.run(main()))
