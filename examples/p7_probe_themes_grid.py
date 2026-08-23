"""P7 探针（默认只读）：system_design_theme 网格「渲染 0 行」归因（task 374 墙）。

背景（env_issue/374 基线 374.log）：Step 3 即进入正确的 Themes 网格页，之后每步
自述 "grid 0 rows"；agent 自行 fetch mui JSON 却拿得到 theme_id=1 "Magento Blank"
——服务端有数据、快照管线全保真（1702 entries / 766 ax_nodes），但网格 tbody 在
DOM 里是空的。本探针对**当前停留的页面**做三方对照，定位空行发生在哪一层：

  A. agent 视角   get_state → element_tree_text 里有没有主题行 / 空态文案
  B. 浏览器真值   raw DOM：tbody 行数、knockout 组件上下文（ko.dataFor 的 rows）、
                  x-magento-init 模板、requirejs 已定义模块数、性能时间线里的
                  mui/grid 请求、空态文案（"couldn't find any records"）
  C. 自动判定     「服务端 0 条」「组件活着但空集」「组件未实例化」「快照丢行」
                  「当前会话未复现」五种结论 + 证据表

默认全程只读：不导航、不点击、不注入 cookie、不 reload（保住现场）。
--reload 可选：注册 Runtime.exceptionThrown + Log.entryAdded 后 Page.reload 一次，
抓初始化期 JS 异常并重采 B（注意：会离开当前页面状态）。

用法：uv run python examples/p7_probe_themes_grid.py [--port 9223] [--reload]
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

# element_tree_text 关键词（agent 视角有没有「行」和「空态文案」）
TREE_KW = ("Magento Blank", "Luma", "records", "couldn", "Theme", "theme")

# 浏览器真值采集（单次 evaluate，JSON 返回；JS 无反斜杠，避开 evaluate 转义链路）
JS_DOM_TRUTH = """
(function(){
	function q(s){ try { return document.querySelectorAll(s).length; } catch(e){ return -1; } }
	function t(s){ var el = document.querySelector(s); return el ? (el.textContent || '').trim().slice(0, 120) : null; }
	var out = {};
	out.url = location.href;
	out.title = document.title;
	out.readyState = document.readyState;
	// 网格结构
	out.gridWrap = q('.admin__data-grid-wrap');
	out.gridTable = q('.admin__data-grid-wrap table');
	out.tbodyRows = q('.admin__data-grid-wrap table tbody tr');
	out.koRows = q('tr[data-repeat-index]');
	out.allTbodyRows = q('tbody tr');
	var first = document.querySelector('.admin__data-grid-wrap table tbody tr');
	out.firstRow = first ? (first.innerText || '').trim().slice(0, 100) : null;
	out.infoText = t('.admin__data-grid-info');
	// 空态 / 主题名是否出现在可见文本里
	var bodyText = (document.body.innerText || '');
	out.emptyMsgInText = bodyText.indexOf('couldn') >= 0 && bodyText.indexOf('find any records') >= 0;
	out.textHasBlank = bodyText.indexOf('Magento Blank') >= 0;
	out.textHasLuma = bodyText.indexOf('Luma') >= 0;
	// knockout / requirejs 初始化状态
	out.templates = q('template');
	out.dataBind = q('[data-bind]');
	out.magentoInit = q('script[type="text/x-magento-init"]');
	out.iframes = q('iframe');
	out.loadingMask = q('.admin__data-grid-loading-mask');
	out.jquery = window.jQuery ? window.jQuery.fn.jquery : null;
	out.koType = typeof window.ko;
	try {
		out.requireDefined = (window.require && require.s && require.s.contexts && require.s.contexts._)
			? Object.keys(require.s.contexts._.defined || {}).length : -1;
	} catch (e) { out.requireDefined = -1; }
	// knockout 绑定上下文：网格表格挂在哪个组件上、组件认为有几行
	try {
		var tbl = document.querySelector('.admin__data-grid-wrap table') || document.querySelector('table');
		if (window.ko && tbl && ko.dataFor) {
			var ctx = ko.dataFor(tbl);
			if (ctx && typeof ctx === 'object') {
				out.koCtxName = String(ctx.name || '').slice(0, 80);
				var rows = ctx.rows;
				if (rows && typeof rows === 'function') { rows = rows(); }
				out.koCtxRows = Array.isArray(rows) ? rows.length : (rows ? String(rows).slice(0, 40) : null);
				if (Array.isArray(rows) && rows.length && typeof rows[0] === 'object') {
					out.koFirstRow = JSON.stringify(rows[0]).slice(0, 200);
				}
			} else { out.koCtxName = 'no-context'; }
		} else if (window.ko) {
			out.koCtxName = 'no-table';
		}
	} catch (e) { out.koCtxErr = String(e).slice(0, 120); }
	// x-magento-init 里声明网格组件的脚本（组件没实例化时这里是唯一线索）
	try {
		var inits = document.querySelectorAll('script[type="text/x-magento-init"]');
		var found = [];
		for (var i = 0; i < inits.length; i++){
			var c = inits[i].textContent || '';
			if (c.indexOf('theme') >= 0 || c.indexOf('Theme') >= 0){
				found.push(c.slice(0, 160));
			}
		}
		out.themeInitScripts = found.slice(0, 3);
	} catch (e) {}
	// 网格数据请求是否发起过（性能资源时间线，只读）
	try {
		var res = performance.getEntriesByType('resource');
		var hits = [];
		for (var i = 0; i < res.length; i++){
			var n = res[i].name;
			if (n.indexOf('mui') >= 0 || n.indexOf('render') >= 0 || n.indexOf('grid') >= 0){
				hits.push({u: n.slice(-90), size: res[i].transferSize, dur: Math.round(res[i].duration)});
			}
		}
		out.gridResources = hits.slice(-8);
		out.resTotal = res.length;
	} catch (e) {}
	return JSON.stringify(out);
})()"""


async def phase_agent_view(browser: BrowserSession) -> list[str]:
	"""A：走 agent 同款 get_state，扫 element_tree_text。"""
	print("\n" + "=" * 92)
	print("[A] agent 视角：get_state → element_tree_text")
	print("=" * 92)
	state = await browser.get_state(include_screenshot=False)
	dom_state = state.dom_state
	tree_text = dom_state.element_tree_text or ""
	print(f"  url = {state.url}")
	print(f"  title = {state.title}")
	print(f"  selector_map 大小 = {len(dom_state.selector_map)}")
	print(f"  element_tree_text = {len(tree_text)} 字符 / {len(tree_text.splitlines())} 行")
	hits: list[tuple[int, str]] = []
	for ln, line in enumerate(tree_text.splitlines(), 1):
		if any(kw.lower() in line.lower() for kw in TREE_KW):
			hits.append((ln, line.rstrip()))
	if not hits:
		print("  ⚠️ 关键词全未命中（无主题行、无空态文案）")
	for ln, line in hits[:40]:
		print(f"  L{ln}: {line[:150]}")
	if len(hits) > 40:
		print(f"  ...（还有 {len(hits) - 40} 行命中）")
	return [line for _, line in hits]


async def phase_dom_truth(browser: BrowserSession) -> dict:
	"""B：raw DOM 真值采集。"""
	print("\n" + "=" * 92)
	print("[B] 浏览器真值：raw DOM / knockout 组件 / requirejs / 性能时间线")
	print("=" * 92)
	dom = json.loads(await browser.evaluate(JS_DOM_TRUTH))
	for k, v in dom.items():
		print(f"  {k} = {v}")
	return dom


def phase_verdict(dom: dict, tree_hits: list[str]) -> None:
	"""C：三方对照自动判定。"""
	print("\n" + "=" * 92)
	print("[C] 判定")
	print("=" * 92)
	rows = dom.get("tbodyRows", -1)
	tree_has_theme = any(("blank" in h.lower() or "luma" in h.lower()) for h in tree_hits)
	if rows < 0:
		print("  ⚠️ 找不到 .admin__data-grid-wrap table——当前页可能不是 Themes 网格（看 [B] url）")
		return
	if rows > 0:
		if tree_has_theme:
			print("  ✓ DOM 有行、快照也有主题名 → 当前会话能看见（基线问题未复现：会话/时序相关）")
		else:
			print("  ⚠️ DOM 有行但 element_tree_text 无主题名 → dom-snapshot 管线丢行（快照层 bug）")
		return
	# rows == 0：往下分死因层
	if dom.get("koType") != "object":
		print("  ✗ knockout 未加载（requirejs/页面初始化失败层）")
		return
	ko_rows = dom.get("koCtxRows")
	if ko_rows == 0:
		print("  ✗ 网格组件活着但 rows=0 → 数据提供层拿到空集（mui 响应为空/参数不对）——")
		print("    与基线里 agent 自行 fetch mui 拿到数据矛盾：查两者请求差异（form_key/namespace/bookmark）")
	elif dom.get("koCtxName") in ("no-context", "no-table", "", None):
		print("  ✗ knockout 加载了但表格没有绑定组件 → 网格组件未实例化")
		print("    （x-magento-init 未执行或执行期异常；配合 --reload 抓初始化期 JS 异常）")
	else:
		print(f"  ✗ tbody 空；组件={dom.get('koCtxName')!r} rows={ko_rows!r} —— 按上方证据人工判")
	info = dom.get("infoText") or ""
	if "records" in info:
		print(f"  佐证：服务端渲染的计数文案 = {info!r}（含 'records' → 服务端自己认为 0 条）")


async def phase_reload(browser: BrowserSession) -> None:
	"""可选：带 JS 异常捕获重载一次，重采真值。会离开当前页面状态。"""
	print("\n" + "=" * 92)
	print("[--reload] 注册异常捕获 → Page.reload → 等 8s → 重采")
	print("=" * 92)
	events: list[str] = []

	def _on_exception(event: dict, session_id: str | None = None) -> None:
		details = event.get("exceptionDetails", {}) or {}
		events.append(f"EXCEPTION: {str(details)[:300]}")

	def _on_log(event: dict, session_id: str | None = None) -> None:
		entry = event.get("entry", {}) or {}
		if entry.get("level") in ("error", "warning"):
			events.append(f"LOG[{entry.get('level')}]: {str(entry.get('text', ''))[:200]} {str(entry.get('url', ''))[-80:]}")

	browser.client.register.Runtime.exceptionThrown(_on_exception)
	browser.client.register.Log.entryAdded(_on_log)
	await browser.client.send.Runtime.enable({}, session_id=browser.current_session_id)
	await browser.client.send.Log.enable({}, session_id=browser.current_session_id)
	await browser.client.send.Page.reload({}, session_id=browser.current_session_id)
	await asyncio.sleep(8.0)
	print(f"  捕获 {len(events)} 条异常/错误日志：")
	for e in events[:20]:
		print(f"    {e}")
	if not events:
		print("    （无——初始化期没有 JS 异常）")
	await phase_dom_truth(browser)


async def main() -> int:
	ap = argparse.ArgumentParser(description="Themes 网格 0 行归因探针（默认只读）")
	ap.add_argument("--port", type=int, default=9223)
	ap.add_argument("--reload", action="store_true", help="重载页面抓初始化期 JS 异常（离开当前状态）")
	args = ap.parse_args()

	logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

	ws_url = _fetch_ws_url("localhost", args.port)
	if not ws_url:
		print(f"✗ {args.port} 端口无 debug Chrome")
		return 1
	browser = BrowserSession(ws_url=ws_url)
	await browser.start()
	try:
		tree_hits = await phase_agent_view(browser)
		dom = await phase_dom_truth(browser)
		phase_verdict(dom, tree_hits)
		if args.reload:
			await phase_reload(browser)
		return 0
	finally:
		await browser.stop()


if __name__ == "__main__":
	sys.exit(asyncio.run(main()))
