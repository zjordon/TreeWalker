"""诊断脚本：find_elements(return_node_ids) 的 id 与 selector_map 的 index 是否自洽。

背景（pingkai.cn/contact 表单实跑 20 步复盘）：
  回放 step 1 里 ``find_elements(selector="form select", return_node_ids=True)`` 返回
  职位/咨询类别两个 ``<select>`` 的 backend_id = [6] / [7]；但同页 selector_map（dump
  element_tree_text 可见）里这两个 select 的交互 index = [47] / [48]。模型 step 2 据此发
  ``dropdown_options index:6`` → 报 ``Element 6 not found in DOM state``，被带进 evaluate
  兔子洞。
  源码两边都声明用 backendNodeId（find_elements_node_ids session.py:3285 取
  ``node.get("backendNodeId")``；selector_map key = serializer.py:919 的 backend_node_id）。
  同一节点不该有两个 backendNodeId —— 本脚本坐实分歧到底出自哪一侧。

判据：
  对**同一次 get_state**，从两条路径收集所有 select/input 的 id，再对每个 id 调
  ``DOM.describeNode({backendNodeId})`` + ``DOM.getOuterHTML`` 反查它指向的真实节点：
    - 若 selector_map 的 [47] 与 find_elements 的 [6] 反查出**相同 outerHTML** → 同节点、
      却两个 id → find_elements 取 backendNodeId 的环节有 bug（最可能：performSearch 返回
      的 nodeId 经 describeNode 映射错了，或指向了 portal/wrapper 副本）。
    - 若 outerHTML 不同 → 是两个不同节点 → 页面本就有多份 select（如 Radix 重挂载/portal）。
  input 作为对照组：两边都是 [2..5]，应反查为同节点，验证方法本身可信。

用法：
  1. Chrome 以 --remote-debugging-port=9222 启动，停在 https://pingkai.cn/contact
     （停在别处也行，脚本枚举当前页的 form 控件；pingkai 页最有对照意义）。
  2. uv run python examples/debug_find_elements_id_mismatch.py
  只读、无副作用（不点击 / 不滚动 / 不 set 文件）。
"""

import asyncio
import sys

sys.path.insert(0, f"{__file__}/../src")

from tree_walker.browser.session import BrowserSession
from tree_walker.config import load_settings

# 要对照的 CSS 选择器（与回放 step 0/1 一致）
SELECTORS = ("form select", "form input", "form textarea")
# 每条路径最多枚举多少个（足够覆盖表单）
MAX_RESULTS = 50


def _attrs_to_dict(flat):
	"""CDP describeNode 的 attributes 是扁平 [k1,v1,k2,v2,...]，转 dict 方便阅读。"""
	out = {}
	if not flat:
		return out
	for i in range(0, len(flat) - 1, 2):
		out[flat[i]] = flat[i + 1]
	return out


async def _inspect(client, sid, bid):
	"""用 backendNodeId 反查节点身份：describeNode + getOuterHTML。

	返回 dict：输入 bid、CDP 回报的 backendNodeId/nodeId、nodeName、属性、option 文本、
	outerHTML 片段。任一步失败就地记录错误，不让整轮诊断崩。
	"""
	rec = {"input_bid": bid, "returned_bid": None, "nodeId": None,
		"nodeName": None, "attrs": {}, "outerHTML": None, "error": None}
	try:
		desc = await client.send.DOM.describeNode(
			{"backendNodeId": bid, "depth": 1}, session_id=sid,
		)
		node = (desc or {}).get("node", {}) or {}
		rec["returned_bid"] = node.get("backendNodeId")
		rec["nodeId"] = node.get("nodeId")
		rec["nodeName"] = node.get("nodeName")
		rec["attrs"] = _attrs_to_dict(node.get("attributes"))
		rec["childNodeCount"] = node.get("childNodeCount")
	except Exception as e:  # noqa: BLE001
		rec["error"] = f"describeNode failed: {e!r}"
		return rec
	# outerHTML 需要 nodeId；顺带读出 option 文本，区分属性为空的两个 select
	nid = rec["nodeId"]
	if nid is None:
		return rec
	try:
		r = await client.send.DOM.getOuterHTML({"nodeId": nid}, session_id=sid)
		rec["outerHTML"] = (r.get("outerHTML") or "")[:300]
	except Exception as e:  # noqa: BLE001
		rec["error"] = f"getOuterHTML failed: {e!r}"
	return rec


def _fmt_inspect(rec):
	bid = rec["input_bid"]
	rb = rec["returned_bid"]
	node_id = rec["nodeId"]
	name = rec["nodeName"] or "?"
	err = f"  ⚠ {rec['error']}" if rec.get("error") else ""
	attrs = rec.get("attrs") or {}
	attr_s = " ".join(f'{k}={v!r}' for k, v in attrs.items()) or "(no attrs)"
	outer = (rec.get("outerHTML") or "").replace("\n", " ").strip()
	mismatch = ""
	if rb is not None and rb != bid:
		mismatch = f"  ⚠⚠ returned backendNodeId={rb} != input {bid}（CDP 对这个 id 给出了不同的 backendNodeId）"
	return (
		f"  [{bid}] <{name}> nodeId={node_id}{mismatch}\n"
		f"    attrs: {attr_s}{err}\n"
		f"    outerHTML: {outer}"
	)


async def main():
	settings = load_settings()
	if not settings.browser.ws_url:
		print("Error: 无法连接 Chrome（确认以 --remote-debugging-port=9222 启动）")
		sys.exit(1)

	browser = BrowserSession(settings.browser)
	await browser.start()
	try:
		sid = browser.current_session_id
		client = browser.client

		# ── 路径 A：selector_map（get_state 的产物，即模型 [Page DOM] 看到的）──
		state = await browser.get_state(include_screenshot=False)
		dom = state.dom_state
		smap = dom.selector_map if dom else {}
		url = state.url
		title = getattr(state, "title", "") or ""

		print(f"页面: {url}\n标题: {title}\n")
		print(f"selector_map 总条目: {len(smap)}")
		print(f"element_tree_text 里 select/input 的 index（模型实际可用）：")

		map_by_tag = {"SELECT": [], "INPUT": [], "TEXTAREA": []}
		for idx, node in smap.items():
			tag = (getattr(node, "tag_name", "") or "").upper()
			if tag not in map_by_tag:
				continue
			bid = getattr(node, "backend_node_id", None)
			attrs = getattr(node, "attributes", {}) or {}
			# 自洽性检查：selector_map 的 key(idx) 应 == 节点的 backend_node_id
			idx_eq_bid = (idx == bid)
			flag = "" if idx_eq_bid else "  ⚠ idx != backend_node_id（selector_map key 不是 backendNodeId?）"
			print(f"  idx=[{idx}] tag={tag} backend_node_id={bid} attrs={dict(attrs) or '{}'}{flag}")
			map_by_tag[tag].append({"source": "selector_map", "idx": idx, "bid": bid, "tag": tag, "attrs": attrs})

		# ── 路径 B：find_elements(return_node_ids) ──
		print("\n" + "─" * 70)
		print("find_elements_node_ids 返回（每条 selector 各跑一次）：")
		fe_by_tag = {"SELECT": [], "INPUT": [], "TEXTAREA": []}
		for sel in SELECTORS:
			r = await browser.find_elements_node_ids(sel, max_results=MAX_RESULTS)
			ids = (r or {}).get("node_ids", []) or []
			total = (r or {}).get("total", 0)
			print(f'  selector={sel!r}  total={total}  returned={len(ids)}')
			for el in ids:
				bid = el.get("backend_id")
				tag = (el.get("tag") or "?").upper()
				print(f"    backend_id=[{bid}] <{tag}>")
				if tag in fe_by_tag:
					fe_by_tag[tag].append({"source": "find_elements", "selector": sel, "bid": bid, "tag": tag})

		# ── 对照：select / input 两路径的 id 集合 ──
		print("\n" + "─" * 70)
		print("id 集合对照（selector_map vs find_elements）：")
		for tag in ("SELECT", "INPUT", "TEXTAREA"):
			map_ids = sorted({r["bid"] for r in map_by_tag[tag] if r["bid"] is not None})
			fe_ids = sorted({r["bid"] for r in fe_by_tag[tag] if r["bid"] is not None})
			only_map = sorted(set(map_ids) - set(fe_ids))
			only_fe = sorted(set(fe_ids) - set(map_ids))
			common = sorted(set(map_ids) & set(fe_ids))
			ok = "✅ 一致" if not (only_map or only_fe) else "❌ 分歧"
			print(f"\n  [{tag}] {ok}")
			print(f"    selector_map ids : {map_ids}")
			print(f"    find_elements ids: {fe_ids}")
			if common:
				print(f"    两边都有         : {common}")
			if only_map:
				print(f"    仅 selector_map  : {only_map}  ← 这些 id 模型用得了，find_elements 没给")
			if only_fe:
				print(f"    仅 find_elements : {only_fe}  ← find_elements 给了这些，但 selector_map 没有 → 喂给 click 会 'not found'")

		# ── 反查：对所有出现过的 id 跑 describeNode + getOuterHTML，判断是否同节点 ──
		print("\n" + "─" * 70)
		print("反查每个 id 指向的真实节点（按 outerHTML 聚类即可判断是否同一节点）：")
		all_ids = []
		for tag in ("SELECT", "INPUT", "TEXTAREA"):
			for r in map_by_tag[tag]:
				if r["bid"] is not None:
					all_ids.append(("selector_map", tag, r["bid"]))
			for r in fe_by_tag[tag]:
				if r["bid"] is not None:
					all_ids.append(("find_elements", tag, r["bid"]))
		# 去重（同一 bid 可能两路径都出现）
		seen = {}
		for src, tag, bid in all_ids:
			seen.setdefault(bid, []).append((src, tag))

		inspections = {}
		for bid in sorted(seen):
			rec = await _inspect(client, sid, bid)
			inspections[bid] = rec
			srcs = "+".join(f"{s}/{t}" for s, t in seen[bid])
			print(f"\n[{bid}]  (来自: {srcs})")
			print(_fmt_inspect(rec))

		# ── 判读 ──
		print("\n" + "─" * 70)
		print("判读：")
		for tag in ("SELECT", "INPUT", "TEXTAREA"):
			map_ids = [r["bid"] for r in map_by_tag[tag] if r["bid"] is not None]
			fe_ids = [r["bid"] for r in fe_by_tag[tag] if r["bid"] is not None]
			only_fe = sorted(set(fe_ids) - set(map_ids))
			if not only_fe:
				print(f"  [{tag}] find_elements 的 id 全在 selector_map 内，无分歧。")
				continue
			# 把 find_elements 独有的 id，与 selector_map 的 id 按 outerHTML 配对
			print(f"  [{tag}] find_elements 独有 id {only_fe}，按 outerHTML 找 selector_map 里的同节点：")
			for fe_bid in only_fe:
				fe_outer = (inspections.get(fe_bid) or {}).get("outerHTML") or ""
				hits = [m_bid for m_bid in map_ids
					if (inspections.get(m_bid) or {}).get("outerHTML") == fe_outer and fe_outer]
				if hits:
					print(f"    find_elements [{fe_bid}] 的 outerHTML == selector_map {hits} → "
						f"**同一节点却两个 backendNodeId** → find_elements 取 id 环节有 bug")
				else:
					print(f"    find_elements [{fe_bid}] 在 selector_map 里找不到同 outerHTML 的节点 → "
						f"**是不同节点**（页面可能有多份 {tag}，如 Radix 重挂载/portal）")
	finally:
		await browser.stop()


if __name__ == "__main__":
	asyncio.run(main())
