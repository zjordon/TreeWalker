"""issue #198 关联探针：New Product 页属性集控件「首击点错」结构取证。

背景（c2-fail-analysis §1.1，2026-09-26/27）：
  C2 轮 694~698 五任务 Step1 全部 ``Clicked [DIV]`` 后快照无展开列表，升级 JS 螺旋
  20 步烧尽。playwright 实测：真实点击 ``.action-select`` 本体 1.2s 内 8 选项全可见；
  点外层 ``.action-select-wrap`` / ``[data-index]`` 容器是**无操作**。怀疑模型视角
  （element_tree_text）里触发器与无效容器无差别呈现（class 不进快照、无 * 标记），
  首击点退化为盲猜。本脚本把「模型看到的 index」与「真实 DOM（含 class）」对上。

判据：
  A. 关闭态快照里 Attribute Set 区域有哪些 [index]、长什么样（对照 C2 模型所见）；
  B. 每个 index 经 CDP describeNode/getOuterHTML 反查真实节点 —— 谁是
     ``.action-select`` 本体、谁是 wrap / ``data-index`` 容器 / 隐藏 input / 选项；
  C. 关闭态选项 DOM 计数（li / label / .action-select-list 多口径）——核实
     「选项随展开渲染」的说法，并解释用户 dump（_model_page_view.txt，展开态）里
     选项在树里的形态；
  D.（--click 才跑）合成 click ``.action-select`` → 快照条目差 + Attribute Set 区域
     树文本对比（基线轮「+10 条目/ax+22」的快照层复现）。

用法：
  Chrome 9223 停在任意已登录 admin 页（脚本自己导航到 New Product 页）：
    uv run python examples/debug_198_attribute_set_click.py [--click]

默认只读（不点击、不改表单）；--click 会展开一次下拉再收起（不保存，无 DB 副作用）。
"""

import argparse
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tree_walker.browser.session import BrowserSession
from tree_walker.config import _fetch_ws_url

NEW_PRODUCT_URL = "http://localhost:7780/admin/catalog/product/new/set/4/type/simple/"
# 区域定位锚：Attribute Set 标签行 → 之后的字段标签（Product Name）之前的所有行
START_ANCHOR = "Attribute Set"
END_ANCHOR = "Product Name"
TREE_MAX_LINES = 30  # 从锚点行起最多取多少行（区域裁剪上限）


def _slice_attr_set_region(tree_text: str) -> list[str]:
	"""从 element_tree_text 里裁出 Attribute Set 控件区域（锚点行到下一字段标签行）。"""
	lines = tree_text.splitlines()
	start = None
	for i, ln in enumerate(lines):
		if START_ANCHOR in ln:
			start = i
			break
	if start is None:
		return []
	out = []
	for ln in lines[start : start + TREE_MAX_LINES]:
		if out and END_ANCHOR in ln:
			break
		out.append(ln)
	return out


def _extract_indexes(region_lines: list[str]) -> list[int]:
	idxs = []
	for ln in region_lines:
		m = re.match(r"\s*\[(\d+)\]", ln)
		if m:
			idxs.append(int(m.group(1)))
	return idxs


def _attrs_to_dict(flat):
	out = {}
	if not flat:
		return out
	for i in range(0, len(flat) - 1, 2):
		out[flat[i]] = flat[i + 1]
	return out


async def _describe(client, sid, bid: int) -> dict:
	rec = {"bid": bid, "nodeName": None, "attrs": {}, "outerHTML": None, "error": None}
	try:
		desc = await client.send.DOM.describeNode({"backendNodeId": bid, "depth": 1}, session_id=sid)
		node = (desc or {}).get("node", {}) or {}
		rec["nodeName"] = node.get("nodeName")
		rec["attrs"] = _attrs_to_dict(node.get("attributes"))
		nid = node.get("nodeId")
		if nid is not None:
			r = await client.send.DOM.getOuterHTML({"nodeId": nid}, session_id=sid)
			rec["outerHTML"] = (r.get("outerHTML") or "").replace("\n", " ")[:260]
	except Exception as e:  # noqa: BLE001
		rec["error"] = repr(e)
	return rec


async def main() -> int:
	ap = argparse.ArgumentParser(description="属性集控件首击点错结构取证（默认只读）")
	ap.add_argument("--port", type=int, default=9223)
	ap.add_argument("--click", action="store_true", help="末尾做一次 .action-select 合成点击实验")
	ap.add_argument("--timeline", action="store_true",
		help="真实坐标点击实验：点 Default 文本 div（模拟 C2 首击）后按时间梯度抓快照，"
		"再点第二次验证 toggle 反转；区分「渲染慢」vs「开了又关」")
	ap.add_argument("--stay", action="store_true",
		help="不导航：对当前页做区域裁剪+index 反查（用于核对用户手动 dump 的页面状态）")
	ap.add_argument("--verify", action="store_true",
		help="修复后冒烟（#205）：真页走 Tools().execute('click') 全链——①有效点击展开→"
		"无 no-visible-effect 误报；②toggle 第二击（关闭也是页面变化）→无警告；"
		"③P0：移除节点后 click_element 返回 False（修复前为 True）")
	ap.add_argument("--render", action="store_true",
		help="节点替换悬空假说实验：navigate 后高频监测属性集 text div 的 backendNodeId "
		"是否漂移（KO 重渲染替换节点→旧 id 悬空）；若漂移，用旧 id 走 click_element "
		"验证「detached 节点静默成功零效果」")
	args = ap.parse_args()

	ws_url = _fetch_ws_url("localhost", args.port)
	if not ws_url:
		print(f"✗ {args.port} 端口无 debug Chrome")
		return 1
	browser = BrowserSession(ws_url=ws_url)
	await browser.start()
	try:
		sid = browser.current_session_id
		client = browser.client

		# R1#2：await 一元优先级高于三元——`await sleep(2) if cond else sleep(0.3)`
		# 的 else 分支协程从未被 await（无等待 + RuntimeWarning），展开为显式分支。
		if not args.stay:
			await browser.navigate(NEW_PRODUCT_URL)
			await asyncio.sleep(2.0)
		else:
			await asyncio.sleep(0.3)

		# ── A. 关闭态 agent 视图 ──
		state = await browser.get_state(include_screenshot=False)
		tree_text = state.dom_state.element_tree_text or ""
		title = getattr(state, "title", "") or ""
		print(f"[A] 页面: {state.url}\n    标题: {title}")
		if "Login" in title or "login" in (state.url or ""):
			print("    ⚠ 疑似未登录（被重定向到登录页）——后续结论不可信")
		print(f"[A] 快照总条目: {len(state.dom_state.selector_map or {})}")
		region = _slice_attr_set_region(tree_text)
		print("\n[A] Attribute Set 区域（模型所见，关闭态）：")
		for ln in region:
			print(f"    {ln}")

		# ── B. index ↔ 真实 DOM 映射 ──
		idxs = _extract_indexes(region)
		print(f"\n[B] 区域内 {len(idxs)} 个 index 反查真实节点：")
		for bid in idxs:
			rec = await _describe(client, sid, bid)
			if rec["error"]:
				print(f"    [{bid}] ⚠ {rec['error']}")
				continue
			attrs = rec["attrs"]
			keep = {k: v for k, v in attrs.items() if k in ("class", "id", "data-index", "type", "for", "role", "aria-label", "style")}
			print(f"    [{bid}] <{rec['nodeName']}> {keep or '(no attrs)'}")
			print(f"        outerHTML: {rec['outerHTML']}")

		# ── C. 关闭态选项 DOM 多口径计数 ──
		counts_js = (
			"JSON.stringify({"
			" li: document.querySelectorAll('.action-select-list li').length,"
			" listUl: document.querySelectorAll('ul.action-select-list').length,"
			" labels: document.querySelectorAll('.action-select-list label').length,"
			" anyLi: document.querySelectorAll('li[data-opt], li[data-role]').length,"
			" wrap: document.querySelectorAll('.action-select-wrap').length,"
			" sel: document.querySelectorAll('.action-select').length,"
			" selTag: Array.from(document.querySelectorAll('.action-select')).map(e=>e.tagName).join(','),"
			" selText: (document.querySelector('.action-select')||{}).textContent || '',"
			" dataIdx: document.querySelectorAll('[data-index]').length,"
			" listVisible: (()=>{const u=document.querySelector('.action-select-list');"
			"  if(!u) return 'no-el'; const cs=getComputedStyle(u);"
			"  return cs.display+'/'+cs.visibility+'/'+u.offsetHeight;})()"
			"})"
		)
		try:
			raw = await browser.execute_js(counts_js)
			print(f"\n[C] 关闭态 DOM 计数：{raw}")
		except Exception as e:  # noqa: BLE001
			print(f"\n[C] ⚠ 计数 evaluate 失败：{e!r}")

		# ── D. 点击实验（可选） ──
		if args.verify:
			# #205 修复冒烟：走 _action_click 全链（G9 判定+指纹检测+文案）
			from tree_walker.tools.actions import Tools

			await browser.navigate(NEW_PRODUCT_URL)
			await asyncio.sleep(2.0)
			state = await browser.get_state(include_screenshot=False)
			# 属性集本体 index：element_tree 区域里第一个 div（.action-select）
			region = _slice_attr_set_region(state.dom_state.element_tree_text or "")
			idxs = _extract_indexes(region)
			target = idxs[0] if idxs else None
			print(f"\n[D-verify] 目标 index={target}（.action-select 本体，关闭态）")
			for ln in region:
				print(f"    {ln}")
			rec = await _describe(client, sid, target)
			print(f"[D-verify] target 反查：<{rec['nodeName']}> {rec['attrs']}")
			print(f"[D-verify] target outerHTML: {rec['outerHTML']}")
			tools = Tools()

			r1 = await tools.execute("click", {"index": target}, browser, browser_state=state)
			warn1 = "no visible effect" in (r1.extracted_content or "")
			alive = await browser.execute_js(
				"JSON.stringify({active: !!document.querySelector('.action-select._active'),"
				" els: document.querySelectorAll('*').length,"
				" html: document.documentElement.outerHTML.length})")
			print(f"[D-verify] ① 有效点击（应展开）：error={r1.error}, 无效果警告={warn1}（期望 False）")
			print(f"[D-verify] ① 点击后 DOM 状态={alive}")
			print(f"[D-verify] ① 回显全文: {r1.extracted_content}")

			state2 = await browser.get_state(include_screenshot=False)
			r2 = await tools.execute("click", {"index": target}, browser, browser_state=state2)
			warn2 = "no visible effect" in (r2.extracted_content or "")
			print(f"[D-verify] ② toggle 第二击（关闭也是变化）：error={r2.error}, 无效果警告={warn2}（期望 False）")

			# 分辨实验：全链无效时，三路对照定位失效环节 + 重复采样检验间歇性
			for attempt in range(3):
				el_click = await browser.execute_js(
					"(()=>{const el=document.querySelector('.action-select');"
					"if(!el) return 'no-el'; el.click();"
					"return 'active='+el.classList.contains('_active');})()")
				await asyncio.sleep(0.5)
				disp = await browser.execute_js(
					"(()=>{const el=document.querySelector('.action-select');"
					"if(!el) return 'no-el'; el.dispatchEvent(new MouseEvent('click',{bubbles:true}));"
					"return 'active='+el.classList.contains('_active');})()")
				await asyncio.sleep(0.5)
				doc2 = await client.send.DOM.getDocument({"depth": 0}, session_id=sid)
				q2 = await client.send.DOM.querySelector(
					{"nodeId": doc2["root"]["nodeId"], "selector": ".action-select"}, session_id=sid)
				d2 = await client.send.DOM.describeNode({"nodeId": q2["nodeId"]}, session_id=sid)
				ok3 = await browser.click_element(d2["node"]["backendNodeId"])
				await asyncio.sleep(0.5)
				st = await browser.execute_js(
					"JSON.stringify({active: !!document.querySelector('.action-select._active')})")
				print(f"[D-verify] 对照#{attempt}: el.click()={el_click}, dispatchEvent={disp}, "
					f"直连click_element={ok3}, 此刻active={st}")
				hit = await browser.execute_js(
					"JSON.stringify((()=>{const el=document.querySelector('.action-select');"
					"if(!el) return null; const r=el.getBoundingClientRect();"
					"const x=r.x+r.width/2, y=r.y+r.height/2;"
					"const hit=document.elementFromPoint(x,y); if(!hit) return {x,y,who:null};"
					"return {x:Math.round(x), y:Math.round(y), who:hit.tagName+'.'+hit.className,"
					"inside: !!hit.closest('.action-select')};})())")
				print(f"[D-verify]   中心点命中: {hit}")
				# 若被任何一路打开，点 body 收起再进入下一轮
				if '"active":true' in (st or ""):
					await browser.execute_js("document.body.click();")
					await asyncio.sleep(0.5)

			# P0：移除目标节点 → click_element 返回 False（修复前 True）
			doc = await client.send.DOM.getDocument({"depth": 0}, session_id=sid)
			q = await client.send.DOM.querySelector(
				{"nodeId": doc["root"]["nodeId"], "selector": ".action-select"}, session_id=sid)
			d = await client.send.DOM.describeNode({"nodeId": q["nodeId"]}, session_id=sid)
			bid = d["node"]["backendNodeId"]
			await browser.execute_js(
				"(()=>{const el=document.querySelector('.action-select');"
				"if(el){el.remove();} return 'removed';})()")
			ok = await browser.click_element(bid)
			print(f"[D-verify] ③ P0 detached：click_element 返回={ok}（期望 False；修复前 True）")
			await browser.navigate(NEW_PRODUCT_URL)
			print("[D-verify] 已重新导航恢复现场")

		if args.render:
			# 节点替换悬空假说：C2「Clicked 成功但页面不变」的一种机制是目标节点被
			# KO 重渲染替换（旧 backendNodeId 悬空）——click_element 的 JS fallback
			# 对 detached 节点调 this.click() 会「成功返回 True 但零页面效果」。
			print("\n[D-render] navigate 后高频监测属性集 text div 的 backendNodeId（0.5s×24）：")
			await browser.navigate(NEW_PRODUCT_URL)

			async def bid_of(selector: str):
				doc = await client.send.DOM.getDocument({"depth": 0}, session_id=sid)
				q = await client.send.DOM.querySelector(
					{"nodeId": doc["root"]["nodeId"], "selector": selector}, session_id=sid)
				nid = q.get("nodeId")
				if not nid:
					return None
				d = await client.send.DOM.describeNode({"nodeId": nid}, session_id=sid)
				return d["node"].get("backendNodeId")
			seen = {}
			stable_bid = None
			for i in range(24):
				await asyncio.sleep(0.5)
				try:
					bid = await bid_of(".admin__action-multiselect-text")
				except Exception as e:  # noqa: BLE001
					bid = f"err:{e!r:.60}"
				seen.setdefault(bid, []).append(i * 0.5)
				if i % 4 == 0 or len(seen) > 1:
					print(f"    t={i*0.5:4.1f}s  bid={bid}")
				if isinstance(bid, int):
					stable_bid = bid

			print(f"[D-render] bid 时间线：{ {k: (v[0], v[-1]) for k, v in seen.items()} }")
			# R1#6：唯一键不是有效 bid（querySelector 未命中→None、持续 CDP 异常→err
			# 字符串）时是「零有效观测」，不能当「无漂移」的决定性否定结论输出。
			if len(seen) == 1 and isinstance(next(iter(seen)), int):
				print("[D-render] ✓ 12s 内 bid 无漂移——节点替换假说不成立（本机本时段）")
			elif len(seen) == 1:
				print(f"[D-render] ⚠ 12s 内未取得任何有效 bid（唯一 key={next(iter(seen))!r}）"
					"——选择器未命中/持续异常，不能据此否定假说")
			else:
				# R2#5：混合键时间线（前段 querySelector 未命中→None / 瞬时异常→err
				# 字符串，之后才取到有效 int bid）首键可能是无效键——先过滤有效
				# int 键再下结论，≥2 个有效 bid 才是真漂移。
				valid_bids = [k for k in seen if isinstance(k, int)]
				if len(valid_bids) >= 2:
					print(f"[D-render] ⚠ bid 漂移！id 序列={valid_bids} —— 用旧 id 点击验证 detached 行为")
				else:
					print(f"[D-render] ⚠ 仅 {len(valid_bids)} 个有效 bid（其余为未命中/异常键 "
						f"{[k for k in seen if not isinstance(k, int)]!r}）——前段无有效观测，不能据此判定漂移")

			# detached 点击验证：即便 bid 没漂移，也验证「悬空 id」的行为——
			# 构造一个已移除节点的 bid：先抓当前 bid，再强制重渲染（打开再关闭下拉会
			# 重渲染列表而非本体，故用直接移除法）：克隆场景过于侵入，改为验证
			# resolveNode 对已移除节点的行为（先移除再点）。
			if stable_bid is not None:
				cur_bid = await bid_of(".admin__action-multiselect-text")
				print(f"\n[D-render] detached 验证：当前 bid={cur_bid}，先移除节点再 click_element(旧 id)")
				await browser.execute_js(
					"(()=>{const el=document.querySelector('.admin__action-multiselect-text');"
					"if(el){el.remove();} return 'removed';})()")
				ok = await browser.click_element(cur_bid)
				cnt = await browser.execute_js(
					"JSON.stringify({active: !!document.querySelector('.action-select._active')})")
				print(f"[D-render] click_element(已移除节点 id) 返回={ok}（True=静默成功）, 下拉 active={cnt}")
				print("[D-render] （页面留在此实验态；重新导航可恢复）")

		if args.timeline:
			# 用 TreeWalker 生产同款真实坐标点击（click_element）复现 C2 首击：
			# 目标 = .admin__action-multiselect-text（显示 Default 文本的子 div，模型眼中"Default 控件"）
			probe_js = (
				"JSON.stringify((()=>{const el=document.querySelector('.action-select');"
				"if(!el) return null; const r=el.getBoundingClientRect();"
				"return {text:(el.textContent||'').trim().slice(0,30), x:r.x+r.width/2, y:r.y+r.height/2};})())"
			)
			info = await browser.execute_js(probe_js)
			print(f"\n[D-timeline] 点击目标（.action-select 本体）: {info}")

			# 反查该元素的 backendNodeId（click_element 的入参口径）
			doc = await client.send.DOM.getDocument({"depth": 0}, session_id=sid)
			root_id = doc["root"]["nodeId"]
			q = await client.send.DOM.querySelector(
				{"nodeId": root_id, "selector": ".action-select"}, session_id=sid)
			desc = await client.send.DOM.describeNode({"nodeId": q["nodeId"]}, session_id=sid)
			bid = desc["node"]["backendNodeId"]
			print(f"[D-timeline] backendNodeId={bid}（.action-select）")

			state_js = (
				"JSON.stringify((()=>{"
				"const ul=document.querySelector('.action-select-list');"
				"const sel=document.querySelector('.action-select');"
				"return {li: document.querySelectorAll('.action-select-list li').length,"
				" active: sel? sel.classList.contains('_active') : null,"
				" hasInput: !!document.querySelector('.action-select-wrap input, .admin__action-multiselect-wrap input'),"
				" ulInDom: !!ul};})())"
			)

			async def snap_label(tag: str) -> None:
				cnt = await browser.execute_js(state_js)
				st = await browser.get_state(include_screenshot=False)
				print(f"    [{tag}] li/active 状态={cnt}  快照条目={len(st.dom_state.selector_map or {})}")

			print("[D-timeline] 基线（点击前）:")
			await snap_label("t=-0")
			ok = await browser.click_element(bid)
			print(f"[D-timeline] 第 1 次真实坐标点击 click_element 返回={ok}")
			for delay in (0.3, 0.6, 1.0, 1.5, 2.5):
				await asyncio.sleep(delay)
				await snap_label(f"t+{delay}s")
			print("[D-timeline] 等待 5s 观察是否自动关闭:")
			await asyncio.sleep(5.0)
			await snap_label("t+7.5s")
			ok2 = await browser.click_element(bid)
			print(f"[D-timeline] 第 2 次点击同目标（toggle 预期反转）返回={ok2}")
			await asyncio.sleep(1.5)
			await snap_label("t2+1.5s")
			# 收尾：若列表仍开着，点 body 收起
			await browser.execute_js("document.body.click();")
			await asyncio.sleep(0.8)
			print("[D-timeline] 已点 body 收起（现场恢复）")

		if args.click:
			# R1#5：--verify/--render 等前置实验块可能已变更页面（--render 移除节点
			# 不恢复、--verify 结尾重新 navigate）——基线按当前页重取，否则
			# 条目差/差异行对照失真。
			state = await browser.get_state(include_screenshot=False)
			region = _slice_attr_set_region(state.dom_state.element_tree_text or "")
			n_before = len(state.dom_state.selector_map or {})
			region_before = list(region)
			await browser.execute_js(
				"const el=document.querySelector('.action-select'); if(el){el.dispatchEvent(new MouseEvent('click',{bubbles:true}));}"
			)
			await asyncio.sleep(1.5)
			state2 = await browser.get_state(include_screenshot=False)
			n_after = len(state2.dom_state.selector_map or {})
			region2 = _slice_attr_set_region(state2.dom_state.element_tree_text or "")
			print(f"\n[D] 合成 click .action-select 后：快照条目 {n_before} → {n_after}（差 {n_after - n_before}）")
			print("[D] 展开后 Attribute Set 区域（差异行打 *）：")
			bset = {ln.strip() for ln in region_before}
			for ln in region2:
				mark = " " if ln.strip() in bset else "*"
				print(f"   {mark} {ln}")
			# 收起（点 body），恢复现场
			await browser.execute_js("document.body.click();")
			await asyncio.sleep(0.8)
			print("[D] 已点 body 收起（现场恢复，未保存任何表单改动）")

		return 0
	finally:
		await browser.stop()


if __name__ == "__main__":
	sys.exit(asyncio.run(main()))
