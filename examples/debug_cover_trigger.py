"""诊断：在当前抖音 post 页找「选择封面」触发器，看它为何录制时 locate 失败。

连真实 Chrome，get_state，搜 selector_map + 全 DOM 里 cover/封面 相关元素，
打印其 xpath / class / 属性 / bounds / 是否进 selector_map / is_interactive 命中哪条规则。

用法：uv run python examples/debug_cover_trigger.py
"""

import asyncio
import sys

sys.path.insert(0, f"{__file__}/../src")

from tree_walker.browser.session import BrowserSession
from tree_walker.config import _fetch_ws_url


async def main() -> int:
	ws_url = _fetch_ws_url("localhost", 9222)
	browser = BrowserSession(ws_url=ws_url)
	await browser.start()
	try:
		state = await browser.get_state(include_screenshot=False)
		print(f"url: {state.url}")
		smap = state.dom_state.selector_map
		print(f"selector_map: {len(smap)} 节点")

		# 1. 在 selector_map 里找 cover/封面 相关
		print("\n=== selector_map 里 cover/封面 相关节点 ===")
		hits = 0
		for idx, node in smap.items():
			attrs = getattr(node, "attributes", {}) or {}
			cls = attrs.get("class", "")
			ax = getattr(node, "ax_name", None) or ""
			text = (getattr(node, "text", None) or "")
			blob = f"{cls} {ax} {text}".lower()
			if "cover" in blob or "封面" in blob or "选择封" in blob:
				hits += 1
				print(f"  [map idx={idx}] tag={getattr(node,'node_name','')} class={cls[:50]!r}")
				print(f"      xpath={getattr(node,'xpath','')}")
				print(f"      attrs={{name:{attrs.get('name')!r}, id:{attrs.get('id')!r}, aria-label:{attrs.get('aria-label')!r}, role:{attrs.get('role')!r}}}")
				print(f"      is_interactive={getattr(node,'is_interactive',None)} cursor? attrs has cursor-pointer? class has 'pointer'? {('cursor' in str(attrs))}")
		if hits == 0:
			print("  （selector_map 里没有 cover/封面 节点！）")

		# 2. 在全 DOM（dom_tree 字符串）里找 选择封面 文本
		print("\n=== 全 DOM 树里 '选择封面' / 'cover' 文本出现 ===")
		# dom_state 可能有 dom_tree 或所有节点列表；尝试遍历 selector_map 外的节点
		all_nodes = getattr(state.dom_state, "all_nodes", None) or []
		if not all_nodes:
			# 退而求其次：看 dom_tree 文本
			dt = getattr(state.dom_state, "dom_tree", "") or ""
			for kw in ["选择封面", "cover"]:
				if kw in dt:
					# 找上下文
					i = dt.find(kw)
					print(f"  '{kw}' 出现在 dom_tree: ...{dt[max(0,i-60):i+40]!r}...")
		return 0
	finally:
		await browser.stop()


if __name__ == "__main__":
	sys.exit(asyncio.run(main()))
