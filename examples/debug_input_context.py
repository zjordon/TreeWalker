"""探针 3：每个 file input 的 DOM 容器归属 —— 容器层带不带 横/竖 标签？

结论将决定修复方向：
  - 若每个 input 的祖先容器（class 或子树文本）含 横/竖 → 可用「结构归属」区分
    （input 跟随其容器的方向），修复就是把方向信号从「input 自身文本」改为
    「input 所在容器（更深的祖先 + class + 兄弟可见文本）」。
  - 若 input 容器全无方向标签（dropzone 与 input 在 DOM 中彻底分离）→ 结构法也
    无效，需依赖坐标（但隐藏 input 塌缩 (0,0)）或可见代理元素的坐标。

用法：uv run python examples/debug_input_context.py
"""

import asyncio
import logging
import re
import sys

sys.path.insert(0, f"{__file__}/../src")

from tree_walker.browser.dom import build_dom_state
from tree_walker.browser.session import BrowserSession
from tree_walker.browser.views import EnhancedDOMTreeNode, NodeType
from tree_walker.config import load_settings
from tree_walker.tools.actions import _is_file_input_node

logging.basicConfig(
	level=logging.INFO,
	format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

_ORIENT_KW = {"横": "landscape", "landscape": "landscape", "宽": "landscape",
			  "竖": "portrait", "portrait": "portrait", "高": "portrait"}


def _cls(node: EnhancedDOMTreeNode) -> str:
	return ((node.attributes or {}).get("class") or "")[:50]


def _node_text(node: EnhancedDOMTreeNode) -> str:
	return (node.node_value or "").strip()


def gather_all_file_inputs(node, out):
	if node is None:
		return out
	if _is_file_input_node(node):
		out.append(node)
	for c in (node.children_nodes or []):
		gather_all_file_inputs(c, out)
	for sr in (node.shadow_roots or []):
		gather_all_file_inputs(sr, out)
	if node.content_document is not None:
		gather_all_file_inputs(node.content_document, out)
	return out


def subtree_text(node: EnhancedDOMTreeNode, max_nodes: int = 200) -> str:
	"""收集子树可见文本（截断），用于判断容器里有没有「横/竖/封面」label。"""
	parts = []
	count = [0]

	def walk(n):
		if n is None or count[0] > max_nodes:
			return
		v = _node_text(n)
		if v:
			parts.append(v)
			count[0] += 1
		for c in (n.children_nodes or []):
			walk(c)
		for sr in (n.shadow_roots or []):
			walk(sr)

	walk(node)
	return " ".join(parts)


def container_orientation(node: EnhancedDOMTreeNode) -> tuple[str | None, str]:
	"""沿祖先链向上找：哪个祖先的 (class + 子树文本) 含 横/竖 关键词。
	返回 (方向, 证据描述)。深度上限 8。"""
	cur = node.parent_node
	depth = 0
	while cur is not None and depth < 8:
		if cur.node_type == NodeType.ELEMENT_NODE:
			cls = (cur.attributes or {}).get("class") or ""
			txt = subtree_text(cur, max_nodes=120)
			hay = f"{cls} {txt}"
			for kw, orient in _ORIENT_KW.items():
				if kw in hay:
					evidence = f"depth={depth} <{cur.node_name}> class={cls[:30]!r} 文本含「{kw}」"
					return orient, evidence
		cur = cur.parent_node
		depth += 1
	return None, "(8 层祖先内未发现 横/竖 关键词)"


async def main():
	settings = load_settings()
	if not settings.browser.ws_url:
		print("Error: 无法连接 Chrome。是否已用 --remote-debugging-port=9222 启动？")
		sys.exit(1)

	print("=" * 92)
	print("file input 的 DOM 容器归属（容器层是否带 横/竖 标签）")
	print("=" * 92)

	browser = BrowserSession(settings.browser)
	await browser.start()

	dom_state, _ = await build_dom_state(
		client=browser.client,
		session_id=browser.current_session_id,
		config=browser._dom_collection_config,
		previous_selector_map=None,
	)
	root = dom_state._root.original_node if dom_state._root else None
	if root is None:
		print("Error: DOM 树为空。")
		await browser.stop()
		return

	all_fis = gather_all_file_inputs(root, [])
	print(f"\nfile input 总数 = {len(all_fis)}\n")

	for i, node in enumerate(all_fis, 1):
		accept = (node.attributes or {}).get("accept")
		orient, evidence = container_orientation(node)
		tag = f"方向={orient}" if orient else "方向=❓无信号"
		print(f"── file input #{i}  bid={node.backend_node_id}  accept={accept!r}")
		print(f"   容器判定: {tag}")
		print(f"   证据: {evidence}")
		# 打印祖先链（tag/class，含横竖标记）
		print("   祖先链:")
		cur = node.parent_node
		d = 0
		while cur is not None and d < 8:
			if cur.node_type == NodeType.ELEMENT_NODE:
				cls = _cls(cur)
				hay = f"{cls} {subtree_text(cur, max_nodes=60)}"
				marks = [kw for kw in _ORIENT_KW if kw in hay]
				mark = f"  ⟶ 含{marks}" if marks else ""
				print(f"     d={d} <{cur.node_name}> class={cls!r}{mark}")
			cur = cur.parent_node
			d += 1
		print()

	# 汇总
	print("=" * 92)
	print("汇总")
	print("=" * 92)
	labeled = {}
	unlabeled = []
	for node in all_fis:
		orient, _ = container_orientation(node)
		if orient:
			labeled.setdefault(orient, []).append(node.backend_node_id)
		else:
			unlabeled.append(node.backend_node_id)
	for orient, bids in labeled.items():
		print(f"  {orient}: {bids}")
	if unlabeled:
		print(f"  ❓无方向信号: {unlabeled}")
		print("  → 这些 input 的容器 8 层内无 横/竖 文本，结构法对它们无效。")
	if labeled.get("portrait") and labeled.get("landscape"):
		print("\n  ✅ 横/竖 input 各自有可区分的容器方向标签 —— 结构归属法可行。")
		print("  → 修复：判定 input 方向时，读「祖先容器 class + 子树可见文本」而非 input 自身文本。")

	await browser.stop()


if __name__ == "__main__":
	asyncio.run(main())
