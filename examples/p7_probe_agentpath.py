r"""P7 终审：完整复刻 agent 输入路径 + 三个修复候选对照。

用户观察（决定性）：多次看到点击 Show Report 时日期框是空的——本质推测：
日期控件只认真实按键（keyup 同步内部状态），input_text 的 _force_set_value
程序赋值它不认账，交互（blur/日历关闭）时把字段重置为空。

本探针逐字面复刻 agent 路径（CDP 坐标点击输入框 → _clear_text_field →
_force_set_value → 坐标点击 Show Report），并在【点击后立即】读字段值——
若为空即证实用户判断。三个修复候选同场对照：
  A) agent 原路径（预期：点击瞬间字段被清，提交空/无反应）
  B) 修复1：真实逐键输入（type_text）→ 点击
  C) 修复2：force_set 后补发合成 keyup → 点击
  D) 修复3：force_set 后调用部件 API（$(el).datepicker('setDate')）→ 点击

用法：uv run python examples/p7_probe_agentpath.py
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tree_walker.browser.session import BrowserSession
from tree_walker.config import _fetch_ws_url

from p7_probe_bestsellers_ui import BASE, DEFAULT_COOKIE_FILE, grid_state, inject_cookies, trusted_click

JS_READ_VALUES = """
(function(){
	function v(id){
		var el = document.getElementById(id);
		return el ? el.value : 'NO_EL';
	}
	return JSON.stringify({from: v('sales_report_from'), to: v('sales_report_to'),
		href: location.href.slice(0, 100)});
})()"""

JS_RECT_BY_ID = """
(function(){
	var el = document.getElementById(ID);
	if (!el) { return JSON.stringify({found: false}); }
	el.scrollIntoView({block: 'center'});
	var r = el.getBoundingClientRect();
	return JSON.stringify({found: true, x: r.x, y: r.y, w: r.width, h: r.height});
})()"""


async def rect_of(browser: BrowserSession, element_id: str) -> dict:
	return json.loads(
		await browser.evaluate(JS_RECT_BY_ID.replace("ID", json.dumps(element_id)))
	)


async def click(browser: BrowserSession, element_id: str, settle: float = 0.5) -> None:
	r = await rect_of(browser, element_id)
	await asyncio.sleep(settle)
	await trusted_click(browser, r)


async def set_field_agent_style(browser: BrowserSession, element_id: str, value: str) -> None:
	"""逐字面复刻 _action_input_text：坐标点击聚焦 → _clear_text_field → _force_set_value。"""
	await click(browser, element_id)
	await browser._clear_text_field()
	await browser._force_set_value(value)


async def run_variant(browser: BrowserSession, name: str, setter) -> None:
	await browser.navigate(BASE, new_tab=False)
	await asyncio.sleep(8)  # 等部件加载潮（~6s 全局函数就绪）
	await setter()
	pre = json.loads(await browser.evaluate(JS_READ_VALUES))
	await click(browser, "filter_form_submit")
	await asyncio.sleep(3.0)
	post = json.loads(await browser.evaluate(JS_READ_VALUES))
	st = await grid_state(browser)
	print(f"[{name}]")
	print(f"  点击前字段: {pre}")
	print(f"  点击后: {post}")
	print(f"  网格: rows={st['rows']} first={st['first'][:40]!r}")
	print(f"  判定: {'✓ 提交成功' if '/filter/' in post['href'] else '✗ 未提交/空提交'}")


def setter_agent(browser, eid, val):
	async def _s():
		await set_field_agent_style(browser, eid, val)
	return _s


async def main() -> int:
	ws_url = _fetch_ws_url("localhost", 9223)
	if not ws_url:
		print("✗ 9223 无 debug Chrome")
		return 1
	browser = BrowserSession(ws_url=ws_url)
	await browser.start()
	try:
		await inject_cookies(browser, DEFAULT_COOKIE_FILE)

		# A) agent 原路径
		async def variant_a():
			await set_field_agent_style(browser, "sales_report_from", "01/01/2022")
			await set_field_agent_style(browser, "sales_report_to", "03/31/2022")
		await run_variant(browser, "A agent 原路径（坐标点击聚焦 + force_set）", variant_a)

		# B) 修复1：真实逐键输入
		async def variant_b():
			await click(browser, "sales_report_from")
			await browser.type_text("01/01/2022", clear=True)
			await click(browser, "sales_report_to")
			await browser.type_text("03/31/2022", clear=True)
		await run_variant(browser, "B 修复1：真实逐键输入", variant_b)

		# C) 修复2：force_set 后补合成 keyup
		async def variant_c():
			for eid, val in (("sales_report_from", "01/01/2022"), ("sales_report_to", "03/31/2022")):
				await set_field_agent_style(browser, eid, val)
				await browser.evaluate(f"""
(function(){{
	var el = document.getElementById({json.dumps(eid)});
	el.dispatchEvent(new KeyboardEvent('keydown', {{bubbles: true, key: '0'}}));
	el.dispatchEvent(new KeyboardEvent('keyup', {{bubbles: true, key: '0'}}));
}})()""")
		await run_variant(browser, "C 修复2：force_set + 合成 keyup", variant_c)

		# D) 修复3：部件 API setDate
		async def variant_d():
			for eid, val in (("sales_report_from", "01/01/2022"), ("sales_report_to", "03/31/2022")):
				await set_field_agent_style(browser, eid, val)
				await browser.evaluate(f"""
(function(){{
	var el = document.getElementById({json.dumps(eid)});
	var w = window.jQuery ? jQuery(el) : null;
	if (w && w.datepicker){{
		w.datepicker('setDate', {json.dumps(val)});
		return 'setDate ok';
	}}
	return 'no datepicker widget';
}})()""")
		await run_variant(browser, "D 修复3：datepicker('setDate')", variant_d)
		return 0
	finally:
		await browser.stop()


if __name__ == "__main__":
	sys.exit(asyncio.run(main()))
