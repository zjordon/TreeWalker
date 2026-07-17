"""验证 RECT 兜底（locate_by_ref Level 3）能否救回「选择封面」无指纹问题。

模拟录制：用一个**故意失配的 xpath** + cover 的真实 rect，跑 locate_by_ref，
确认 Level 3（位置兜底）能命中 selector_map 里的 cover 节点（或其内含的可交互节点）。
"""

import asyncio
import sys

sys.path.insert(0, f"{__file__}/../src")

from tree_walker.browser.session import BrowserSession
from tree_walker.config import _fetch_ws_url
from tree_walker.recorder.locator import locate_by_ref


async def main() -> int:
	ws = _fetch_ws_url("localhost", 9222)
	br = BrowserSession(ws_url=ws)
	await br.start()
	try:
		st = await br.get_state(include_screenshot=False)
		smap = st.dom_state.selector_map
		print(f"selector_map: {len(smap)} 节点")

		for idx, node in smap.items():
			attrs = getattr(node, "attributes", {}) or {}
			if "cover-Jg3T4p" not in attrs.get("class", ""):
				continue
			snap = getattr(node, "snapshot_node", None)
			b = getattr(snap, "bounds", None) if snap else None
			if not b:
				continue
			rect = {"x": float(b.x), "y": float(b.y), "width": float(b.width), "height": float(b.height)}
			# 模拟录制：xpath 故意失配（html/body/WRONG），只靠 rect
			ref = {"xpath": "/html/body/WRONG", "tag": "div", "rect": rect}
			r = locate_by_ref(ref, smap)
			print(f"\ncover idx={idx} rect={rect}")
			if r is None:
				print("  ❌ Level 3 仍未命中（rect 中心不在任何可交互节点内？）")
			else:
				ridx, rnode = r
				rattrs = getattr(rnode, "attributes", {}) or {}
				print(f"  ✅ Level 3 命中 idx={ridx} tag={getattr(rnode,'node_name','')} class={rattrs.get('class','')[:40]!r}")
		return 0
	finally:
		await br.stop()


if __name__ == "__main__":
	sys.exit(asyncio.run(main()))
