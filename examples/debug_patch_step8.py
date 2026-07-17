"""把 douyin_redesign2.json 的 step8（误匹配的 svg）指纹替换成 cover-Jg3T4p 的真实指纹，
模拟「IoU rect 兜底修正后」的录制产物，输出 douyin_redesign3.json 供重放验证下游步骤。

选 cover 节点：含点击 svg(562.7,542.8) 的那个 cover-Jg3T4p。
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, f"{__file__}/../src")

from tree_walker.browser.session import BrowserSession
from tree_walker.browser.views import DOMInteractedElement
from tree_walker.config import _fetch_ws_url

SVG_X, SVG_Y = 562.7, 542.8  # 录制的 step8 svg 中心


async def main() -> int:
	ws = _fetch_ws_url("localhost", 9222)
	br = BrowserSession(ws_url=ws)
	await br.start()
	try:
		st = await br.get_state(include_screenshot=False)
		# 选面积最大的 cover-Jg3T4p（视频封面触发器，160×120；另一个 90×120 是推荐封面）
		chosen = None
		best_area = 0
		for idx, node in st.dom_state.selector_map.items():
			a = getattr(node, "attributes", {}) or {}
			if "cover-Jg3T4p" not in a.get("class", ""):
				continue
			b = getattr(getattr(node, "snapshot_node", None), "bounds", None)
			if not b:
				continue
			area = b.width * b.height
			if area > best_area:
				best_area, chosen = area, (idx, node)
		if chosen is None:
			print("✗ 未找到 cover-Jg3T4p（页面不在 post 页？）")
			return 1
		idx, node = chosen
		proj = DOMInteractedElement.load_from_enhanced_dom_tree(node).to_dict()
		print(f"✓ 用 cover-Jg3T4p idx={idx} hash={proj.get('element_hash')} 作 step8 指纹")

		src = Path("rerun-history/douyin_redesign2.json")
		data = json.loads(src.read_text(encoding="utf-8"))
		step8 = data["history"][8]
		step8["interacted_element"] = [proj]
		step8["model_output"]["actions"][0]["params"]["index"] = idx
		# 清掉 locate_miss（已补上指纹）
		ss = step8.get("state_summary") or {}
		ss.pop("_locate_miss", None)
		step8["state_summary"] = ss
		out = Path("rerun-history/douyin_redesign3.json")
		out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
		print(f"✓ 写出 {out}")
		return 0
	finally:
		await br.stop()


if __name__ == "__main__":
	sys.exit(asyncio.run(main()))
