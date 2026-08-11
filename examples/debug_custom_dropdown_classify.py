"""诊断：B 站等自定义下拉为何走不通 agent 的 select_dropdown（issue #160 假设验证）。

背景（静态分析结论，待真机坐实）：
  P5 takeaways 说「执行侧多类型分发已就绪」，但对**非原生 / 非 ARIA** 的自定义下拉不成立。
  ``_action_select_dropdown``（actions.py:1501）→ ``set_dropdown_option`` →
  ``fetch_dropdown_options``（session.py:3560）按 **ARIA → custom-class → 子树 BFS**
  三类判型，B 站两种形态（创作声明 `<input>`+裸 `<li>`；分区 `<div>`+`<div title>` 兄弟+虚拟滚动）
  **三类全 miss** → 返回 ``source=None`` → action 报「not a recognized dropdown」→ agent 退裸 click
  → 不记 ``select_dropdown`` → 进不了 P5 变量链。

本探针对**真实** B 站投稿页（Chrome --remote-debugging-port=9223 + 已登录 profile）逐项坐实：
  1. 【闭态】action 层 ``dropdown_options`` 回显 —— 模型实际看到的（应是「not a recognized dropdown」）
  2. 【闭态】session 层 ``fetch_dropdown_options(bid)`` 的 source —— 逐判型结果（应是 None）
  3. 【开态】真 click 触发器展开后，``fetch_dropdown_options`` 是否仍 None（懒渲染 + 判型仍 miss）
  4. 【开态】中性 DOM 扫描：option 到底在不在 DOM（证伪「没 option」vs「有 option 但判型忽略」）
  5. 【开态】action 层 ``select_dropdown`` 回显 —— 模型选值时看到什么

只读为主；第 3–5 步会真 click 触发器展开下拉（结束后 Escape 收起，best-effort）。不改页面数据、不提交。

用法（Chrome 已以 --remote-debugging-port=9223 启动并停在 B 站投稿编辑页、已登录）：
  # A. 自动探测「创作声明」触发器（input[placeholder*=选择]）并测试
  uv run python examples/debug_custom_dropdown_classify.py

  # B. 指定「分区」触发器 index（先用 A 打印的候选列表找到分区触发器 idx），换分区目标值
  uv run python examples/debug_custom_dropdown_classify.py --index 2090 --value 娱乐

  # C. 一次测多个触发器（各自配值；缺省值按 input→创作声明 / div→分区 兜底）
  uv run python examples/debug_custom_dropdown_classify.py --index 455 2090
"""

import argparse
import asyncio
import sys

sys.path.insert(0, f"{__file__}/../src")

from tree_walker.browser.session import BrowserSession
from tree_walker.config import _fetch_ws_url
from tree_walker.tools.actions import Tools

CDP_HINT = "确认 Chrome 以 --remote-debugging-port=9223 启动并停在 B 站投稿编辑页（已登录）"

# 中性 option 扫描：不依赖 role/class，从触发器的**父节点**子树（覆盖「同级/兄弟」option）
# 里捞一切像 option 的节点，证伪/证实「option 在不在 DOM」。注意必须是 function(){...} 字面量
# （callFunctionOn 的 functionDeclaration 要求），与 _ARIA_OPTIONS_JS 同形。
JS_NEUTRAL_OPTIONS = r"""
function() {
	const root = (this.parentElement) || this;
	const SEL = 'li, [role="option"], [role="menuitem"], [data-value], .item, .option, [title]';
	const all = Array.from(root.querySelectorAll(SEL));
	const opts = [];
	const seen = new Set();
	for (const n of all) {
		if (seen.has(n)) continue;
		seen.add(n);
		const text = (n.textContent || '').replace(/\s+/g, ' ').trim();
		if (!text) continue;
		opts.push({
			tag: n.tagName.toLowerCase(),
			role: n.getAttribute('role') || '',
			title: n.getAttribute('title') || '',
			text: text.slice(0, 24),
		});
		if (opts.length >= 40) break;
	}
	return {
		parentTag: root.tagName.toLowerCase(),
		parentRole: (root.getAttribute && root.getAttribute('role')) || '',
		parentClass: (typeof root.className === 'string' ? root.className : '').slice(0, 60),
		optionCount: opts.length,
		options: opts,
	};
}
"""


async def eval_js(browser, sid, js):
	"""页面上跑自由 JS（returnByValue）。返回 (value, error)。"""
	r = await browser.client.send.Runtime.evaluate(
		{"expression": f"(()=>{{\n{js}\n}})()", "returnByValue": True, "awaitPromise": False},
		session_id=sid,
	)
	res = r.get("result", {})
	exc = r.get("exceptionDetails") or res.get("exceptionDetails")
	if exc:
		desc = exc.get("exception", {}).get("description", str(exc)) if isinstance(exc, dict) else str(exc)
		return None, f"JS异常: {desc}"
	return res.get("value"), None


async def eval_on_node(browser, sid, backend_node_id, js):
	"""把 JS 跑在 backendNodeId 对应节点上（this=该节点，镜像 _fetch_aria_options）。"""
	resolve = await browser.client.send.DOM.resolveNode(
		{"backendNodeId": backend_node_id}, session_id=sid,
	)
	object_id = resolve["object"]["objectId"]
	r = await browser.client.send.Runtime.callFunctionOn(
		{"objectId": object_id, "functionDeclaration": js, "returnByValue": True},
		session_id=sid,
	)
	res = r.get("result", {})
	exc = r.get("exceptionDetails") or res.get("exceptionDetails")
	if exc:
		desc = exc.get("exception", {}).get("description", str(exc)) if isinstance(exc, dict) else str(exc)
		return None, f"JS异常: {desc}"
	return res.get("value"), None


async def attach_bili(browser):
	"""多 tab 时 attach 到 bilibili 那个 page（否则可能连到别的站，比如本地 admin）。返回是否找到。"""
	resp = await browser.client.send.Target.getTargets({})
	pages = [t for t in resp.get("targetInfos", []) if t.get("type") == "page"]
	print("打开的 page 标签：")
	for t in pages:
		print("  -", (t.get("url") or "")[:80])
	bili = next((t for t in pages if "bilibili.com" in (t.get("url") or "")), None)
	if not pages:
		return False
	tid = (bili or pages[0]).get("targetId")
	if tid and tid != browser.current_target_id:
		await browser.switch_tab(tid)
	return bili is not None


def describe_node(node):
	"""selector_map 节点一句话描述。"""
	tag = (getattr(node, "tag_name", "") or "").upper()
	bid = getattr(node, "backend_node_id", None)
	attrs = getattr(node, "attributes", {}) or {}
	bits = []
	for k in ("placeholder", "title", "role", "aria-controls", "aria-owns", "type", "id"):
		if attrs.get(k):
			bits.append(f'{k}={attrs[k]!r}')
	cls = attrs.get("class")
	if cls:
		bits.append(f'class={(cls or "")[:40]!r}')
	return f"<{tag}> idx-bid={bid} {' '.join(bits) if bits else '(no notable attrs)'}"


def find_trigger_candidates(smap):
	"""自动探测疑似下拉触发器：input[placeholder*=选择/创作声明] 等。返回 [(idx, node)]。"""
	out = []
	for idx, node in smap.items():
		tag = (getattr(node, "tag_name", "") or "").upper()
		attrs = getattr(node, "attributes", {}) or {}
		ph = (attrs.get("placeholder") or "")
		if tag == "INPUT" and ("选择" in ph or "创作声明" in ph or "请选择" in ph):
			out.append((idx, node))
	return out


async def test_trigger(tools, browser, sid, idx, value, label):
	"""对一个触发器 index 跑完整五步诊断。"""
	print("\n" + "═" * 72)
	print(f"▼ 测试触发器 [{label}] index={idx}  目标值={value!r}")
	print("─" * 72)

	# 取节点（用一次 get_state 拿 selector_map，复用作 action 层 browser_state）
	state = await browser.get_state(include_screenshot=False)
	smap = state.dom_state.selector_map if state.dom_state else {}
	node = smap.get(idx)
	if not node:
		print(f"  ✗ index {idx} 不在 selector_map（页面变了？先用候选列表确认 idx）")
		return None
	bid = getattr(node, "backend_node_id", None)
	tag = (getattr(node, "tag_name", "") or "").upper()
	print(f"  节点: {describe_node(node)}")

	# ── 1. 闭态：action 层 dropdown_options 回显（模型读选项时看到的）──
	r1 = await tools.execute("dropdown_options", {"index": idx}, browser, browser_state=state)
	print("\n  [1 闭态] dropdown_options 回显:")
	if r1.error:
		print(f"    ✗ error: {r1.error}")
	else:
		print(f"    ✓ memory: {r1.long_term_memory}")
		print(f"      extracted: {(r1.extracted_content or '')[:200]}")

	# ── 2. 闭态：session 层 fetch_dropdown_options 逐判型 source ──
	try:
		cls_closed = await browser.fetch_dropdown_options(bid)
		src_closed = cls_closed.get("source")
		n_closed = len(cls_closed.get("options") or [])
	except Exception as e:  # noqa: BLE001
		src_closed, n_closed = f"异常({e!r})", 0
	print(f"\n  [2 闭态] fetch_dropdown_options → source={src_closed!r}  options={n_closed}")
	print(f"          （aria→custom→子树BFS 全 miss 才是 None；这正是假设要坐实的）")

	# ── 3. 真展开触发器，再读 ──
	print("\n  [3 开态] 真实 click 触发器展开下拉 ...")
	try:
		await browser.click_element(bid)
	except Exception as e:  # noqa: BLE001
		print(f"    ⚠ click_element 失败: {e!r}")
	await asyncio.sleep(0.8)  # 等懒渲染

	state2 = await browser.get_state(include_screenshot=False)
	try:
		cls_open = await browser.fetch_dropdown_options(bid)
		src_open = cls_open.get("source")
		n_open = len(cls_open.get("options") or [])
	except Exception as e:  # noqa: BLE001
		src_open, n_open = f"异常({e!r})", 0
	print(f"    fetch_dropdown_options(开态) → source={src_open!r}  options={n_open}")
	if src_open is None and src_closed is None:
		print("    → 闭/开两态都 None：判型器对这形态确实不识别（即便 option 已在 DOM）")

	# ── 4. 中性 DOM 扫描：option 到底在不在 DOM ──
	neutral, err = await eval_on_node(browser, sid, bid, JS_NEUTRAL_OPTIONS)
	print("\n  [4 开态] 中性 DOM 扫描（不依赖 role/class，从触发器父节点子树捞 option）:")
	if err:
		print(f"    ⚠ {err}")
	elif neutral:
		print(f"    父 <{neutral.get('parentTag')}> role={neutral.get('parentRole')!r} "
		      f"class={neutral.get('parentClass')!r}")
		print(f"    optionCount={neutral.get('optionCount')}  ← >0 表示 option 确实在 DOM，"
		      f"只是 fetch 判型忽略它们")
		for o in (neutral.get("options") or [])[:12]:
			print(f"      · <{o['tag']}> role={o['role']!r} title={o['title']!r} text={o['text']!r}")

	# ── 5. action 层 select_dropdown 回显（模型选值时看到的）──
	r5 = await tools.execute(
		"select_dropdown", {"index": idx, "value": value}, browser, browser_state=state2,
	)
	print(f"\n  [5 开态] select_dropdown(index={idx}, value={value!r}) 回显:")
	if r5.error:
		print(f"    ✗ error: {r5.error}")
	else:
		print(f"    ✓ memory: {r5.long_term_memory}")
		print(f"      extracted: {(r5.extracted_content or '')[:200]}")

	# ── 收起（best-effort，别把页面留在展开态）──
	try:
		await browser.send_keys("Escape")
	except Exception:  # noqa: BLE001
		pass
	await asyncio.sleep(0.3)

	return {
		"tag": tag,
		"src_closed": src_closed,
		"options_closed": n_closed,
		"src_open": src_open,
		"options_open": n_open,
		"neutral_option_count": (neutral or {}).get("optionCount") if not err else None,
		"dropdown_options_error": r1.error,
		"select_error": r5.error,
		"select_ok": r5.error is None,
	}


async def main():
	ap = argparse.ArgumentParser()
	ap.add_argument("--index", type=int, nargs="*", default=None,
	                help="手动指定触发器 index（可多个）；不给则自动探测 input[placeholder*=选择]")
	ap.add_argument("--value", type=str, default=None,
	                help="select_dropdown 的目标值；不给则 input 触发器→「内容无需标注」/ div 触发器→「娱乐」")
	args = ap.parse_args()

	ws_url = _fetch_ws_url("127.0.0.1", 9223)
	if not ws_url:
		print(f"✗ 无法连接 Chrome（{CDP_HINT}）"); sys.exit(1)
	browser = BrowserSession(ws_url=ws_url)
	await browser.start()
	tools = Tools()
	try:
		if not await attach_bili(browser):
			print(f"⚠ 没找到 bilibili tab —— {CDP_HINT}")
		sid = browser.current_session_id
		state = await browser.get_state(include_screenshot=False)
		smap = state.dom_state.selector_map if state.dom_state else {}
		print(f"页面: {state.url}")
		if "bilibili" not in (state.url or "") and "member" not in (state.url or ""):
			print(f"⚠ URL 看着不像 B 站投稿页，确认停对了页面再跑（{CDP_HINT}）")
		print(f"selector_map 总条目: {len(smap)}")

		# 决定要测的触发器列表 [(idx, value, label)]
		targets = []
		if args.index:
			for ix in args.index:
				node = smap.get(ix)
				tag = (getattr(node, "tag_name", "") or "").upper() if node else "?"
				val = args.value or ("娱乐" if tag == "DIV" else "内容无需标注")
				targets.append((ix, val, f"手动/{tag}"))
		else:
			cands = find_trigger_candidates(smap)
			if not cands:
				print("\n✗ 没自动探测到 input[placeholder*=选择] 触发器。")
				print("  → 用 --index <idx> 指定（候选见下），或确认页面已展开到含下拉的表单段。")
			for ix, node in cands:
				targets.append((ix, args.value or "内容无需标注", "自动/创作声明"))

		# 打印一批候选触发器，方便用户找「分区」等非 input 触发器的 index
		print("\n── 候选触发器（input[placeholder*=选择] + 带 title/role 的 div）──")
		shown = 0
		for ix, node in smap.items():
			tag = (getattr(node, "tag_name", "") or "").upper()
			attrs = getattr(node, "attributes", {}) or {}
			ph = attrs.get("placeholder") or ""
			hit = (tag == "INPUT" and ("选择" in ph or "创作" in ph)) or \
			      (tag in ("DIV", "UL", "LI") and (attrs.get("title") or attrs.get("role")))
			if hit and shown < 25:
				print(f"  [{ix}] {describe_node(node)}")
				shown += 1
		if shown == 0:
			print("  (无)")

		for ix, val, label in targets:
			await test_trigger(tools, browser, sid, ix, val, label)

		print("\n" + "═" * 72)
		print("判读要点：")
		print("  • [2] source=None 且 [4] optionCount>0 → 坐实「判型器漏认 + option 其实在 DOM」")
		print("  • [1]/[5] 回显「not a recognized dropdown」→ 坐实「agent 退裸 click、不记 select_dropdown」")
		print("  • [3] 开态仍 None → 即便先展开，现有读侧也不认（需补 open-then-read + 新判型）")
	finally:
		await browser.stop()


if __name__ == "__main__":
	asyncio.run(main())
