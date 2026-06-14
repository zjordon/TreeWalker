"""诊断脚本：分析抖音创作者中心封面选择区域的 DOM 检测问题。

使用方法：
1. 在 Chrome 中打开抖音创作者中心上传页面（确保封面区域可见）
2. 确保 Chrome 以 --remote-debugging-port=9222 启动
3. 运行: python examples/debug_cover.py
"""

import asyncio
import logging
import sys

sys.path.insert(0, f"{__file__}/../src")

from tree_walker.browser.dom import (
	ClickableElementDetector,
	build_dom_state,
	_detect_js_click_listeners,
)
from tree_walker.browser.session import BrowserSession
from tree_walker.browser.views import NodeType
from tree_walker.config import BrowserSettings, load_settings

logging.basicConfig(
	level=logging.INFO,
	format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# 封面相关 class 关键词
COVER_KEYWORDS = ['cover-jg3t4p', 'title-wa45xd', 'covercontrol-cjlzqc', 'filter-k_cjvj', 'background-opvtev']


def find_cover_elements(node, depth=0, results=None):
	"""递归搜索 DOM 树中封面相关的元素。"""
	if results is None:
		results = []
	if node.node_type != NodeType.ELEMENT_NODE:
		children = getattr(node, 'children_and_shadow_roots', [])
		if callable(children):
			children = children()
		for child in children:
			find_cover_elements(child, depth + 1, results)
		return results

	cls = (node.attributes or {}).get('class', '').lower()
	if any(kw in cls for kw in COVER_KEYWORDS):
		results.append((depth, node))

	for child in node.children_and_shadow_roots:
		find_cover_elements(child, depth + 1, results)
	return results


def print_node_detail(node, depth):
	"""打印节点的详细信息。"""
	indent = "  " * depth
	cls = (node.attributes or {}).get('class', 'N/A')
	tag = node.tag_name
	bid = node.backend_node_id
	print(f"{indent}[depth={depth}] <{tag}> class=\"{cls}\" bid={bid}")
	print(f"{indent}  is_visible={node.is_visible}")
	print(f"{indent}  has_js_click_listener={node.has_js_click_listener}")

	snap = node.snapshot_node
	if snap:
		print(f"{indent}  snapshot.cursor_style={snap.cursor_style}")
		print(f"{indent}  snapshot.is_clickable={snap.is_clickable}")
		print(f"{indent}  snapshot.paint_order={snap.paint_order}")
		if snap.bounds:
			print(f"{indent}  snapshot.bounds={snap.bounds.width}x{snap.bounds.height} at ({snap.bounds.x},{snap.bounds.y})")
		if snap.computed_styles:
			relevant_styles = {k: v for k, v in snap.computed_styles.items() if k in ('cursor', 'pointer-events', 'display', 'visibility', 'opacity')}
			print(f"{indent}  computed_styles(relevant)={relevant_styles}")
	else:
		print(f"{indent}  snapshot_node=None")

	ax = node.ax_node
	if ax:
		print(f"{indent}  ax_node.role={ax.role}")
		print(f"{indent}  ax_node.name={ax.name}")
		if ax.properties:
			props = {p.name: p.value for p in ax.properties}
			print(f"{indent}  ax_node.properties={props}")
	else:
		print(f"{indent}  ax_node=None")

	is_interactive = ClickableElementDetector.is_interactive(node)
	print(f"{indent}  is_interactive={is_interactive}")
	print()
	return is_interactive


async def main():
	settings = load_settings()

	if not settings.browser.ws_url:
		print("Error: Cannot connect to Chrome. Is it running with --remote-debugging-port=9222?")
		sys.exit(1)

	print("=" * 80)
	print("封面元素 DOM 诊断工具")
	print("=" * 80)

	# 连接浏览器
	browser = BrowserSession(settings.browser)
	await browser.start()

	print("\n[1] 获取 DOM 状态...")
	dom_state, metrics = await build_dom_state(
		client=browser.client,
		session_id=browser.current_session_id,
		config=browser._dom_collection_config,
		previous_selector_map=None,
	)
	print(f"DOM 状态获取完成: selector_map 大小={len(dom_state.selector_map)}")

	if dom_state._root is None:
		print("Error: DOM 树为空")
		await browser.stop()
		return

	# 找原始 EnhancedDOMTreeNode 的根
	print("\n[2] 搜索封面相关元素...")
	# 遍历原始 EnhancedDOMTreeNode 树
	cover_elements = find_cover_elements(dom_state._root.original_node)

	if not cover_elements:
		print("未找到任何封面相关元素！尝试在序列化后的树中搜索...")
		# 也搜索序列化树
		_serial_find_cover(dom_state._root)
	else:
		print(f"找到 {len(cover_elements)} 个封面相关元素：\n")

	any_interactive = False
	for depth, node in cover_elements:
		is_int = print_node_detail(node, depth)
		if is_int:
			any_interactive = True

	print("\n" + "=" * 80)
	if any_interactive:
		print("结论：至少有一个封面元素被检测为可交互。")
	else:
		print("结论：所有封面元素均未被检测为可交互！")
		print("这意味着 is_interactive() 的 14 条规则全部失败。")
		print("请查看上面的详细信息来定位具体是哪条规则的问题。")

	# 额外检查：JS click listener 数量
	print("\n[3] 检查 JS 点击监听器...")
	click_ids = await _detect_js_click_listeners(browser.client, browser.current_session_id)
	print(f"检测到 {len(click_ids)} 个元素有 JS 点击监听器")

	# 检查封面元素是否在监听器列表中
	for depth, node in cover_elements:
		if node.backend_node_id in click_ids:
			print(f"  ✅ <{node.tag_name}> class=\"{(node.attributes or {}).get('class', '')}\" bid={node.backend_node_id} 在监听器列表中")
		else:
			print(f"  ❌ <{node.tag_name}> class=\"{(node.attributes or {}).get('class', '')}\" bid={node.backend_node_id} 不在监听器列表中")

	await browser.stop()


def _serial_find_cover(node):
	"""在 SimplifiedNode 树中搜索封面相关元素。"""
	from tree_walker.browser.views import SimplifiedNode

	def _walk(n, depth=0):
		orig = n.original_node
		if orig.attributes:
			cls = orig.attributes.get('class', '').lower()
			if any(kw in cls for kw in COVER_KEYWORDS):
				print(f"  [序列化树] depth={depth} <{orig.tag_name}> class=\"{orig.attributes.get('class', '')}\" bid={orig.backend_node_id}")
				print(f"    interactive={n.is_interactive} highlight_index={n.highlight_index}")
				print(f"    ignored_by_paint_order={n.ignored_by_paint_order} excluded_by_parent={n.excluded_by_parent}")
		for child in n.children:
			_walk(child, depth + 1)

	_walk(node)


if __name__ == "__main__":
	asyncio.run(main())
