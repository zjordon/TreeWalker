"""诊断：封面编辑器 file_inputs_meta（agent/replay 同源数据）+ 自身 class/位置。

确认 replay 竖封面选错 input 的根因。get_state 的 file_inputs_meta 含 class_name(visible/
upload_ancestor)，正是 _action_upload_file 用来区分 primary(hidden-input) vs replace 的数据。
交叉 selector_map（按 backend_node_id）拿 bounds/xpath，识别哪个是竖封面 primary。

用法：Chrome --remote-debugging-port=9222，停在封面编辑器（弹框打开）。
      uv run python examples/debug_cover_meta.py
"""

import asyncio
import sys

sys.path.insert(0, f"{__file__}/../src")

from tree_walker.browser.session import BrowserSession
from tree_walker.config import load_settings


async def main():
	settings = load_settings()
	if not settings.browser.ws_url:
		print("✗ Chrome 未以 --remote-debugging-port=9222 启动"); sys.exit(1)
	browser = BrowserSession(settings.browser)
	await browser.start()
	try:
		state = await browser.get_state(include_screenshot=False)
	finally:
		await browser.stop()
	if not state or not state.dom_state:
		print("✗ get_state 无结果"); sys.exit(1)

	dom = state.dom_state
	smap = dom.selector_map
	meta = getattr(dom, "file_inputs_meta", []) or []
	# backend_id -> selector_map node（拿 bounds/xpath）
	by_bid = {}
	for idx, node in smap.items():
		bid = getattr(node, "backend_node_id", None)
		if bid is not None:
			by_bid[bid] = (idx, node)

	print(f"URL: {state.url}")
	print(f"selector_map={len(smap)} 节点  file_inputs_meta={len(meta)} 个\n")
	print(f"{'bid':>6} {'idx':>6} {'vis':>4} {'upl':>4} {'role':<16} {'accept':<22} {'own_cls':<28} bounds")
	print("-" * 120)
	for fi in meta:
		bid = fi.backend_node_id
		idx_node = by_bid.get(bid)
		idx = idx_node[0] if idx_node else "?"
		node = idx_node[1] if idx_node else None
		cls = (fi.class_name or "")
		role = "REPLACE" if "replace" in cls.lower() else ("primary" if ("hidden" in cls.lower() or "upload" in cls.lower()) else "?")
		bounds = ""
		if node is not None:
			b = getattr(node, "bounds", None)
			if b is not None:
				bounds = f"{getattr(b,'x',0):.0f},{getattr(b,'y',0):.0f} {getattr(b,'width',0):.0f}x{getattr(b,'height',0):.0f}"
		acc = (fi.accept or "")[:21]
		print(f"{bid:>6} {str(idx):>6} {str(fi.visible):>4} {str(fi.upload_ancestor):>4} {role:<16} {acc:<22} {cls[:27]:<28} {bounds}")

	# 汇总：live (visible+upload_ancestor) primary image inputs
	print("\n=== live(visible+upload_ancestor) 且 image accept 的 primary(非 replace) input ===")
	live_primary = []
	for fi in meta:
		cls = (fi.class_name or "").lower()
		if not fi.visible or not fi.upload_ancestor:
			continue
		if "image" not in (fi.accept or "").lower():
			continue
		if "replace" in cls:
			continue
		idx_node = by_bid.get(fi.backend_node_id)
		idx = idx_node[0] if idx_node else "?"
		live_primary.append((fi.backend_node_id, idx, cls))
		print(f"  bid={fi.backend_node_id} idx={idx} class={cls!r}")
	if not live_primary:
		print("  （无）")
	print(f"\n→ live primary image input 共 {len(live_primary)} 个（>1 则需横/竖消歧；==1 则唯一即目标）")

	# 对比 replay step12 选中 9020 的特征
	print("\n=== 对照：replay 日志 step12 选中 9020 ===")
	for fi in meta:
		if fi.backend_node_id == 9020:
			cls = (fi.class_name or "")
			print(f"  9020: visible={fi.visible} upload_ancestor={fi.upload_ancestor} class={cls!r} accept={fi.accept!r}")
			print(f"  → {'是 replace(替换) input' if 'replace' in cls.lower() else '非 replace'}")
			break
	else:
		print("  当前页面无 9020（不同会话，正常）——看上面 live primary 结构即可")


if __name__ == "__main__":
	asyncio.run(main())
