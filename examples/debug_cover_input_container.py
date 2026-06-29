"""诊断脚本：排查抖音创作者中心封面上传「不支持的文件格式」根因。

背景（issue #34 / #36 已修，仍残留；本脚本为新建 issue 取证）：
  - Fix B（中文文件名 -> ASCII 临时名，session.py:3659）已生效，但用户确认
    **与文件名无关**（英文名同样报错）-> 当前「不支持格式」是新根因，不是 #36
    的 ASCII 文件名正则。
  - 症状：文件**传完后**才出现「不支持的文件格式」提示（不是弹窗、也不是
    「set 到失活 input 的无反应」）-> 文件确实到达了抖音处理逻辑、但被某个
    校验拒。
  - 用户 DOM 发现：「上传封面」触发器与 `<input type=file>` 在**并列的两个
    div** 下（非父子容器）。本脚本验证该假设：并列结构是否让 agent /
    set_file_input 把文件 set 到了**错误关联的 input**，从而被该 input 的
    校验拒。

分阶段（用环境变量控制副作用）：
  阶段 1（只读，默认跑）：枚举全部 file input + 全部上传触发器，打印各自
    **父级容器链**与**最近公共祖先(LCA)**，量化「并列 div」结构，并对照
    Fix A 派生的 file_input_backend_ids / file_inputs_meta 是否准确。
  阶段 2（有副作用，默认关）：逐个 input 跑 DOM.setFileInputFiles(测试图)，
    检测反应（「不支持」提示 / 预览图 / 收藏框 / 无反应），定位是**哪个
    input 触发「不支持格式」**。**不点完成/发布**。

用法：
  1. Chrome 以 --remote-debugging-port=9222 启动，停在抖音「封面选择/编辑」区
     （尚未进入会触发发布的步骤）。
  2. 只读分析（安全）：
       uv run python examples/debug_cover_input_container.py
  3. 追加「逐个 set 复现」（有副作用，真的上传一张图，不发布）：
       $env:ENABLE_SET_TEST="1"; uv run python examples/debug_cover_input_container.py
     把 TEST_IMAGE 改成你本机一张合法 PNG（建议英文名，排除文件名因素）。
"""

import asyncio
import os
import sys

sys.path.insert(0, f"{__file__}/../src")

from tree_walker.browser.dom import ClickableElementDetector, build_dom_state
from tree_walker.browser.session import BrowserSession
from tree_walker.config import load_settings

# ── 开关 ────────────────────────────────────────────────────────────────
# 阶段 2：逐个 input 跑 setFileInputFiles（有副作用）。不点完成/发布。
ENABLE_SET_TEST = os.environ.get("ENABLE_SET_TEST") == "1"

# 阶段 2 用的测试图（合法 PNG；建议英文名以排除文件名因素）。改成你本机路径。
TEST_IMAGE = r"D:\dev\git\claude\skills-deom\ppt\browser-use\竖封面.png"

# ── 关键词 ───────────────────────────────────────────────────────────────
TRIGGER_TEXT_KW = (
	"上传封面", "上传图片", "选择文件", "选择图片", "本地上传",
	"更换封面", "设置封面", "设置竖封面", "设置横封面", "上传",
)
# 上传容器/触发器 class 痕迹（Semi-UI Upload 等）
UPLOAD_CLASS_KW = (
	"upload", "drag-area", "drag_area", "dropzone",
	"semi-upload", "picker", "cover",
)


def _is_format_error(text: str) -> bool:
	"""是否为「不支持的图片格式」类提示。"""
	return "不支持" in text or ("格式" in text and ("支持" in text or "图片" in text))


# ── DOM 树遍历与容器关系分析 ─────────────────────────────────────────────

def _walk(root):
	"""遍历原始 EnhancedDOMTreeNode 树，yield 每个节点（含隐藏节点）。"""
	def go(n):
		yield n
		for c in (getattr(n, "children_and_shadow_roots", None) or []):
			yield from go(c)
	yield from go(root)


def _build_parent_maps(root):
	"""建 {bid: node} 与 {bid: parent_bid}（基于原始 DOM 树，含隐藏节点）。"""
	bid_to_node, bid_to_parent = {}, {}

	def go(n, parent_bid):
		bid = getattr(n, "backend_node_id", None)
		if bid is not None:
			bid_to_node[bid] = n
			bid_to_parent[bid] = parent_bid
		for c in (getattr(n, "children_and_shadow_roots", None) or []):
			go(c, bid)

	go(root, None)
	return bid_to_node, bid_to_parent


def _short(s, n=60):
	s = (s or "").strip()
	return s if len(s) <= n else s[:n] + "..."


def _node_text(node, depth=2):
	"""本节点 + 浅层子节点的 aria-label/title/placeholder/name/文本。"""
	parts = []

	def go(n, d):
		if n is None or d < 0:
			return
		a = getattr(n, "attributes", None) or {}
		for k in ("aria-label", "title", "placeholder", "name"):
			v = a.get(k)
			if v:
				parts.append(str(v))
		nv = (getattr(n, "node_value", "") or "").strip()
		if nv:
			parts.append(nv)
		for c in (getattr(n, "children_and_shadow_roots", None) or []):
			go(c, d - 1)

	go(node, depth)
	return " ".join(parts)


def _chain(bid, bid_to_node, bid_to_parent, max_up=14):
	"""从 bid 往上的容器链 [(bid, tag, class)]；遇到 upload 容器即停。"""
	chain = []
	cur, steps = bid, 0
	while cur is not None and steps <= max_up:
		n = bid_to_node.get(cur)
		if n is None:
			break
		a = getattr(n, "attributes", None) or {}
		cls = a.get("class", "")
		tag = (getattr(n, "tag_name", "") or "").lower()
		chain.append((cur, tag, cls))
		if cur != bid and any(kw in cls.lower() for kw in UPLOAD_CLASS_KW):
			break
		cur = bid_to_parent.get(cur)
		steps += 1
	return chain


def _lca(a, b, bid_to_parent):
	"""两节点的最近公共祖先 bid（含自身）。"""
	anc, cur = set(), a
	while cur is not None:
		anc.add(cur)
		cur = bid_to_parent.get(cur)
	cur = b
	while cur is not None:
		if cur in anc:
			return cur
		cur = bid_to_parent.get(cur)
	return None


def _fmt_chain(chain):
	return " < ".join(f"<{t}>#{b}{'.' + c if c else ''}" for (b, t, c) in chain)


# ── 阶段 1：只读结构分析 ────────────────────────────────────────────────

def analyze_structure(dom_state, bid_to_node, bid_to_parent):
	print("\n" + "=" * 92)
	print("[阶段 1] DOM 结构分析（只读）")
	print("=" * 92)

	# 1a. 枚举全部 file input（遍历原始树，含隐藏的）
	inputs = []
	for n in _walk(dom_state._root.original_node):
		a = getattr(n, "attributes", None) or {}
		if (getattr(n, "tag_name", "") or "").upper() == "INPUT" \
				and (a.get("type", "") or "").lower() == "file":
			inputs.append(n)

	# 对照 Fix A 派生
	derived_ids = list(dom_state.file_input_backend_ids)
	derived_meta = list(getattr(dom_state, "file_inputs_meta", []) or [])
	derived_meta_ids = [getattr(m, "backend_node_id", None) for m in derived_meta]
	walked_ids = [getattr(n, "backend_node_id", None) for n in inputs]

	print(f"\n[1a] file input 总数: {len(inputs)}")
	print(f"     遍历原始树 backendNodeIds : {walked_ids}")
	print(f"     Fix A file_input_backend_ids: {derived_ids}")
	print(f"     Fix A file_inputs_meta.ids    : {derived_meta_ids}")
	miss = set(walked_ids) - set(derived_ids)
	extra = set(derived_ids) - set(walked_ids)
	if miss:
		print(f"     ⚠️ 遍历到但 Fix A 没派生的 input: {miss}（Fix A 可能漏了这些 -> 诱饵判定失准）")
	if extra:
		print(f"     ⚠️ Fix A 派生但遍历没找到的 input: {extra}（可能已 React 重建/瞬态）")
	if not miss and not extra:
		print(f"     ✅ Fix A 派生与实际遍历一致")

	print(f"\n[1b] 每个 file input 详情 + 父级容器链：")
	for i, n in enumerate(inputs):
		a = getattr(n, "attributes", None) or {}
		bid = getattr(n, "backend_node_id", None)
		cls = a.get("class", "")
		accept = a.get("accept", "")
		vis = getattr(n, "is_visible", None)
		chain = _chain(bid, bid_to_node, bid_to_parent)
		print(f"\n  input#{i} bid={bid}")
		print(f"    accept = {accept!r}")
		print(f"    class  = {_short(cls)!r}")
		print(f"    is_visible(dom 判定) = {vis}")
		print(f"    父级容器链: {_fmt_chain(chain)}")

	# 1c. 枚举上传触发器（可交互 + 文本/class 命中）
	triggers = []
	for bid, n in bid_to_node.items():
		try:
			interactive = ClickableElementDetector.is_interactive(n)
		except Exception:
			interactive = False
		if not interactive:
			continue
		cls = ((getattr(n, "attributes", None) or {}).get("class", "") or "").lower()
		text = _node_text(n)
		tag = (getattr(n, "tag_name", "") or "").lower()
		if any(kw in text for kw in TRIGGER_TEXT_KW) or any(kw in cls for kw in UPLOAD_CLASS_KW):
			triggers.append((bid, tag, cls, text))

	print(f"\n[1c] 上传触发器（可交互 + 文本/class 命中）共 {len(triggers)} 个：")
	for bid, tag, cls, text in triggers:
		chain = _chain(bid, bid_to_node, bid_to_parent)
		print(f"\n  触发器 bid={bid} <{tag}>")
		print(f"    text  = {_short(text)!r}")
		print(f"    class = {_short(cls)!r}")
		print(f"    父级容器链: {_fmt_chain(chain)}")

	# 1d. LCA 表：触发器 × input —— 量化「并列 div」
	if inputs and triggers:
		print(f"\n[1d] 触发器 × input 最近公共祖先(LCA) —— 量化「并列 div」结构：")
		print("     LCA 不含 upload 关键词 = 触发器与该 input 在上传容器之外才汇合 = 并列/不同上传子树")
		for t_bid, t_tag, t_cls, t_text in triggers:
			for i, fi in enumerate(inputs):
				fi_bid = getattr(fi, "backend_node_id", None)
				lca = _lca(t_bid, fi_bid, bid_to_parent)
				lca_n = bid_to_node.get(lca)
				lca_cls = ((getattr(lca_n, "attributes", None) or {}).get("class", "")) if lca_n else ""
				lca_tag = (getattr(lca_n, "tag_name", "") or "").lower() if lca_n else "?"
				lca_upload = any(kw in lca_cls.lower() for kw in UPLOAD_CLASS_KW)
				marker = "  (✓ 同一上传容器)" if lca_upload else "  (✗ 并列/外层汇合)"
				print(f"    触发器 {t_bid} ↔ input#{i}({fi_bid})  "
					  f"LCA=<{lca_tag}>#{lca} cls={_short(lca_cls, 40)!r}{marker}")

	return inputs, triggers


# ── 阶段 2：逐个 setFileInputFiles 复现「不支持格式」 ───────────────────

async def probe_each_input(browser, inputs):
	print("\n" + "=" * 92)
	print("[阶段 2] 逐个 input 跑 DOM.setFileInputFiles，复现「不支持格式」")
	print("=" * 92)
	cls_filter = os.environ.get("SET_CLASS_FILTER", "").lower()
	if cls_filter:
		inputs = [
			fi for fi in inputs
			if cls_filter in ((getattr(fi, "attributes", None) or {}).get("class", "") or "").lower()
		]
		print(f"  SET_CLASS_FILTER={cls_filter!r}: 只测 bid={[getattr(fi, 'backend_node_id', None) for fi in inputs]}")
	if not os.path.isfile(TEST_IMAGE):
		print(f"  ❌ TEST_IMAGE 不存在: {TEST_IMAGE}（改成本机合法 PNG 后重跑）")
		return
	if os.path.getsize(TEST_IMAGE) == 0:
		print(f"  ❌ TEST_IMAGE 为空文件: {TEST_IMAGE}")
		return

	baseline, _ = await build_dom_state(
		client=browser.client, session_id=browser.current_session_id,
		config=browser._dom_collection_config, previous_selector_map=None,
	)
	base_size = len(baseline.selector_map)

	print(f"\n  baseline selector_map 大小 = {base_size}")
	print(f"  测试图 = {TEST_IMAGE}（{'英文名' if os.path.basename(TEST_IMAGE).isascii() else '中文名'}）")
	print("  ⚠️  即将逐个上传（不点完成/发布），5 秒后开始，Ctrl+C 可中止 ...")
	await asyncio.sleep(5)

	for i, fi in enumerate(inputs):
		bid = getattr(fi, "backend_node_id", None)
		a = getattr(fi, "attributes", None) or {}
		accept = a.get("accept", "")
		print(f"\n  --- input#{i} bid={bid} class={_short(a.get('class', ''))!r} accept={accept!r} ---")
		try:
			await browser.client.send.DOM.setFileInputFiles(
				{"backendNodeId": bid, "files": [TEST_IMAGE]},
				session_id=browser.current_session_id,
			)
		except Exception as e:
			print(f"    setFileInputFiles 抛异常: {e}")
			continue
		await asyncio.sleep(2.5)
		ds2, _ = await build_dom_state(
			client=browser.client, session_id=browser.current_session_id,
			config=browser._dom_collection_config, previous_selector_map=None,
		)
		err, preview, modal = [], [], []
		for nbid, n in ds2.selector_map.items():
			nv = (getattr(n, "node_value", "") or "").strip()
			na = getattr(n, "attributes", None) or {}
			ncls = (na.get("class", "") or "").lower()
			ntag = (getattr(n, "tag_name", "") or "").lower()
			if nv and _is_format_error(nv):
				err.append(_short(nv, 40))
			if ntag == "img" and ("blob" in (na.get("src", "") or "")
								  or "preview" in ncls or "thumbnail" in ncls):
				preview.append(nbid)
			if "modal" in ncls or "collection" in ncls or "收藏" in nv:
				modal.append(_short(ncls, 30))
		delta = len(ds2.selector_map) - base_size
		if err:
			verdict = "❌ 传完后报「不支持格式」  <- 复现症状：抖音处理了但被校验拒"
		elif preview:
			verdict = "✅ 出现预览图（上传成功）"
		elif modal:
			verdict = "⚠️ 弹出收藏/模态框（打到了「收藏封面」input）"
		elif delta == 0:
			verdict = "· 无反应（隐藏诱饵 input，setFileInputFiles 假成功）"
		else:
			verdict = f"? 有变化(Δ={delta})但无明显信号"
		print(f"    → {verdict}")
		if err:
			print(f"    错误文本: {err}")
		if preview:
			print(f"    预览图 bid: {preview}")
		if modal:
			print(f"    模态框: {modal}")


async def main():
	settings = load_settings()
	if not settings.browser.ws_url:
		print("Error: 无法连接 Chrome（确认以 --remote-debugging-port=9222 启动并停在抖音封面区）")
		sys.exit(1)

	print("=" * 92)
	print("抖音封面 input 容器结构 + setFileInputFiles 反应诊断")
	print(f"阶段 2(set 复现): {'ON (有副作用)' if ENABLE_SET_TEST else 'OFF (仅只读分析)'}")
	print("=" * 92)

	browser = BrowserSession(settings.browser)
	await browser.start()

	print("\n[0] 获取 DOM 状态 ...")
	dom_state, _ = await build_dom_state(
		client=browser.client, session_id=browser.current_session_id,
		config=browser._dom_collection_config, previous_selector_map=None,
	)
	if dom_state._root is None or dom_state._root.original_node is None:
		print("Error: DOM 树为空（页面是否停在封面区？）")
		await browser.stop()
		return

	bid_to_node, bid_to_parent = _build_parent_maps(dom_state._root.original_node)

	if ENABLE_SET_TEST:
		# 阶段 2：跳过冗长 LCA 表，直接枚举 file input 逐个 set 实测反应
		inputs = [
			n for n in _walk(dom_state._root.original_node)
			if (getattr(n, "tag_name", "") or "").upper() == "INPUT"
			and ((getattr(n, "attributes", None) or {}).get("type", "") or "").lower() == "file"
		]
		print(f"\n[阶段 2] 枚举到 {len(inputs)} 个 file input: "
			  f"{[getattr(n, 'backend_node_id', None) for n in inputs]}")
		await probe_each_input(browser, inputs)
	else:
		inputs, triggers = analyze_structure(dom_state, bid_to_node, bid_to_parent)
		print("\n" + "-" * 92)
		print("[提示] 只读分析完成。要复现「不支持格式」、定位是哪个 input 触发，开阶段 2：")
		print('       $env:ENABLE_SET_TEST="1"; uv run python examples/debug_cover_input_container.py')
		print("       （有副作用：逐个 input 真上传一张图，不点完成/发布；先把 TEST_IMAGE 改成本机 PNG）")

	await browser.stop()
	print("\n" + "=" * 92)
	print("诊断结束")


if __name__ == "__main__":
	asyncio.run(main())
