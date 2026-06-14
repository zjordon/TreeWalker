"""临时脚本：检查自主声明元素的 paint order 状态。"""
import asyncio
import sys

sys.path.insert(0, f"{__file__}/../src")

from tree_walker.browser.dom import build_dom_state
from tree_walker.browser.session import BrowserSession
from tree_walker.config import load_settings

DECLARATION_CLASSES = [
	'selectBox-buZRzi', 'selectText-XSrMFZ', 'wrapper-MLZdnB',
	'title-cnbkZe', 'controlWrapper-Kt_9Xm', 'chevron-euwXR1',
	'labelWrapper-p6osJm',
]


async def main():
	settings = load_settings()
	browser = BrowserSession(settings.browser)
	await browser.start()

	dom_state, _ = await build_dom_state(
		client=browser.client,
		session_id=browser.current_session_id,
		config=browser._dom_collection_config,
		previous_selector_map=None,
	)

	print(f"selector_map 大小={len(dom_state.selector_map)}")

	if dom_state._root is None:
		print("DOM 树为空")
		await browser.stop()
		return

	# 遍历 SimplifiedNode 树找自主声明相关元素
	def find_decl_nodes(node, depth=0, results=None):
		if results is None:
			results = []
		orig = node.original_node
		cls = (orig.attributes or {}).get('class', '') if orig.attributes else ''
		if any(kw in cls for kw in DECLARATION_CLASSES):
			results.append((depth, node))
		for child in node.children:
			find_decl_nodes(child, depth + 1, results)
		return results

	nodes = find_decl_nodes(dom_state._root)
	print(f"\n找到 {len(nodes)} 个自主声明相关 SimplifiedNode：\n")

	for depth, node in nodes:
		orig = node.original_node
		cls = (orig.attributes or {}).get('class', '')
		tag = orig.tag_name
		bid = orig.backend_node_id
		print(f"{'  ' * depth}[depth={depth}] <{tag}> class=\"{cls}\" bid={bid}")
		print(f"{'  ' * depth}  excluded_by_parent={node.excluded_by_parent}")
		print(f"{'  ' * depth}  ignored_by_paint_order={node.ignored_by_paint_order}")
		print(f"{'  ' * depth}  is_interactive={node.is_interactive}")
		print(f"{'  ' * depth}  highlight_index={node.highlight_index}")
		print(f"{'  ' * depth}  is_visible={orig.is_visible}")
		snap = orig.snapshot_node
		if snap:
			print(f"{'  ' * depth}  snapshot.paint_order={snap.paint_order}")
			if snap.bounds:
				print(f"{'  ' * depth}  snapshot.bounds={snap.bounds.width:.0f}x{snap.bounds.height:.0f} at ({snap.bounds.x:.0f},{snap.bounds.y:.0f})")
		print()

	await browser.stop()


if __name__ == "__main__":
	asyncio.run(main())
