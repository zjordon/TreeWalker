"""P7 探针（微测试）：el.click() 能否触发 Show Report（修复路径验证）。

终局对照已证：同页同值，filterFormSubmit() 直调 → 提交 ✓；CDP 坐标点击 → 无反应。
本测试验证 JS el.click()（会触发 onclick 属性赋值的处理器）→ 预期提交 ✓。
若是，则 TreeWalker 的 click actionability 只需「点击后无 DOM 变化 → 降级 JS click」
（现机制仅在 occluded 检测时降级）即可修复此类页面。

用法：uv run python examples/p7_probe_elclick.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tree_walker.browser.session import BrowserSession
from tree_walker.config import _fetch_ws_url

from p7_probe_bestsellers_ui import BASE, DEFAULT_COOKIE_FILE, inject_cookies


async def main() -> int:
	ws_url = _fetch_ws_url("localhost", 9223)
	if not ws_url:
		print("✗ 9223 无 debug Chrome")
		return 1
	browser = BrowserSession(ws_url=ws_url)
	await browser.start()
	try:
		await inject_cookies(browser, DEFAULT_COOKIE_FILE)
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
	var btn = document.getElementById('filter_form_submit');
	btn.click();
	return 'clicked, onclick=' + typeof btn.onclick;
})()""")
		await asyncio.sleep(3.0)
		href = await browser.evaluate("location.href")
		print(f"el.click() 提交: {'✓ YES' if '/filter/' in href else '✗ no'}  href={href[:100]}")
		return 0
	finally:
		await browser.stop()


if __name__ == "__main__":
	sys.exit(asyncio.run(main()))
