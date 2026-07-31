"""诊断脚本：分析B站视频上传页面标签输入框的 Shadow DOM 和交互问题。

使用方法：
1. 在 Chrome 中打开B站投稿页面（https://member.bilibili.com/platform/upload/video/frame）
2. 确保已填写基本信息，页面中可以看到标签输入区域
3. 确保 Chrome 以 --remote-debugging-port=9222 启动
4. 运行: python examples/debug_bilibili_tag.py
"""

import asyncio
import logging
import sys

sys.path.insert(0, f"{__file__}/../src")

from dom_snapshot import build_dom_state
from tree_walker.browser.session import BrowserSession
from tree_walker.browser.views import NodeType
from tree_walker.config import BrowserSettings, load_settings

logging.basicConfig(
	level=logging.INFO,
	format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# 标签相关关键词
TAG_KEYWORDS = [
	'tag', 'label', '标签',
	'tag-input', 'taginput', 'tag-container',
	'bili-tag', 'upload-tag',
]


def find_tag_elements(node, depth=0, results=None):
	"""递归搜索 DOM 树中标签相关的元素。"""
	if results is None:
		results = []

	# 跳过非元素节点
	if hasattr(node, 'node_type') and node.node_type != NodeType.ELEMENT_NODE:
		children = getattr(node, 'children_and_shadow_roots', [])
		for child in children:
			find_tag_elements(child, depth + 1, results)
		return results

	# 检查当前元素
	tag_name = getattr(node, 'tag_name', '').lower()
	attrs = getattr(node, 'attributes', {}) or {}

	cls = attrs.get('class', '').lower()
	placeholder = attrs.get('placeholder', '').lower()
	role = attrs.get('role', '').lower()
	aria_label = attrs.get('aria-label', '').lower()
	data_attrs = {k: v for k, v in attrs.items() if k.startswith('data-')}
	data_str = str(data_attrs).lower()

	all_text = f"{cls} {placeholder} {role} {aria_label} {data_str}"

	if any(kw in all_text for kw in TAG_KEYWORDS):
		results.append((depth, node))
	elif tag_name == 'input' and attrs.get('type', '').lower() in ('text', 'search', ''):
		# 也收集所有文本输入框
		if 'tag' in all_text or '标签' in all_text:
			results.append((depth, node))

	# 递归子节点
	children = getattr(node, 'children_and_shadow_roots', [])
	for child in children:
		find_tag_elements(child, depth + 1, results)

	return results


def find_shadow_hosts(node, depth=0, results=None):
	"""找出所有包含 Shadow DOM 的宿主元素。"""
	if results is None:
		results = []

	shadow_roots = getattr(node, 'shadow_roots', None)
	if shadow_roots:
		attrs = getattr(node, 'attributes', {}) or {}
		tag_name = getattr(node, 'tag_name', '')
		bid = getattr(node, 'backend_node_id', None)
		results.append({
			'depth': depth,
			'tag': tag_name,
			'class': attrs.get('class', ''),
			'id': attrs.get('id', ''),
			'backend_node_id': bid,
			'shadow_children_count': len(shadow_roots),
		})
		# 继续深入 shadow root
		for sr in shadow_roots:
			find_shadow_hosts(sr, depth + 1, results)

	children = getattr(node, 'children_and_shadow_roots', [])
	for child in children:
		find_shadow_hosts(child, depth + 1, results)

	return results


def print_node_detail(node, depth):
	"""打印节点的详细信息。"""
	indent = "  " * depth
	attrs = getattr(node, 'attributes', {}) or {}
	tag_name = getattr(node, 'tag_name', '?')
	bid = getattr(node, 'backend_node_id', '?')

	print(f"{indent}[depth={depth}] <{tag_name}> class=\"{attrs.get('class', 'N/A')}\" id=\"{attrs.get('id', 'N/A')}\" bid={bid}")
	print(f"{indent}  is_visible={getattr(node, 'is_visible', '?')}")

	# 打印关键属性
	for key in ('type', 'placeholder', 'contenteditable', 'role', 'aria-label', 'accept'):
		val = attrs.get(key)
		if val is not None:
			print(f"{indent}  {key}=\"{val}\"")

	# 快照信息
	snap = getattr(node, 'snapshot_node', None)
	if snap:
		print(f"{indent}  snapshot.is_clickable={getattr(snap, 'is_clickable', '?')}")
		if snap.bounds:
			print(f"{indent}  snapshot.bounds={snap.bounds.width}x{snap.bounds.height} at ({snap.bounds.x},{snap.bounds.y})")
	else:
		print(f"{indent}  snapshot_node=None")

	# Shadow root 信息
	shadow_roots = getattr(node, 'shadow_roots', None)
	if shadow_roots:
		print(f"{indent}  🔒 shadow_roots={len(shadow_roots)} 个 Shadow DOM 子树")
		for i, sr in enumerate(shadow_roots):
			sr_mode = getattr(sr, 'attributes', {}).get('mode', '?')
			sr_children = len(getattr(sr, 'children_and_shadow_roots', []))
			print(f"{indent}    Shadow Root #{i}: mode={sr_mode}, children={sr_children}")

	# 检查是否在序列化树中有索引
	highlight_index = getattr(node, 'highlight_index', None)
	print(f"{indent}  highlight_index={highlight_index}")
	print()
	return highlight_index is not None


async def main():
	settings = load_settings()

	if not settings.browser.ws_url:
		print("Error: Cannot connect to Chrome. Is it running with --remote-debugging-port=9222?")
		sys.exit(1)

	print("=" * 80)
	print("B站标签输入框 DOM 诊断工具")
	print("=" * 80)

	# 连接浏览器
	browser = BrowserSession(settings.browser)
	await browser.start()

	sid = browser.current_session_id

	# ── Step 1: 用 JS 检查页面上的标签输入区域 ──
	print("\n[1] 使用 JS 检查标签输入区域...")

	# 检查常规 DOM 中的标签输入框
	js_result = await browser.execute_js("""
		(function() {
			var results = [];

			// 查找所有 input 元素
			var inputs = document.querySelectorAll('input');
			inputs.forEach(function(inp) {
				var info = {
					tag: 'input',
					type: inp.type,
					placeholder: inp.placeholder,
					className: inp.className,
					id: inp.id,
					name: inp.name,
					value: inp.value,
					visible: inp.offsetParent !== null,
					rect: inp.getBoundingClientRect().width + 'x' + inp.getBoundingClientRect().height,
					accept: inp.accept || ''
				};
				var text = (inp.placeholder + ' ' + inp.className + ' ' + inp.id + ' ' + inp.name).toLowerCase();
				if (text.indexOf('tag') >= 0 || text.indexOf('标签') >= 0) {
					info.isTagRelated = true;
				}
				results.push(info);
			});

			// 查找 contenteditable 元素
			var editables = document.querySelectorAll('[contenteditable="true"], [contenteditable="plaintext-only"]');
			editables.forEach(function(el) {
				var info = {
					tag: el.tagName.toLowerCase(),
					contenteditable: true,
					className: el.className,
					id: el.id,
					visible: el.offsetParent !== null,
					rect: el.getBoundingClientRect().width + 'x' + el.getBoundingClientRect().height,
					text: el.textContent.substring(0, 100)
				};
				var text = (el.className + ' ' + el.id + ' ' + el.textContent).toLowerCase();
				if (text.indexOf('tag') >= 0 || text.indexOf('标签') >= 0) {
					info.isTagRelated = true;
				}
				results.push(info);
			});

			// 查找所有 Shadow DOM 宿主
			var allElements = document.querySelectorAll('*');
			allElements.forEach(function(el) {
				if (el.shadowRoot) {
					results.push({
						tag: el.tagName.toLowerCase(),
						isShadowHost: true,
						className: el.className,
						id: el.id,
						shadowMode: 'open'
					});
				}
			});

			// 只返回标签相关的结果
			var tagResults = results.filter(function(r) {
				return r.isTagRelated || r.isShadowHost ||
					(r.type === 'text' && r.visible);
			});

			return JSON.stringify(tagResults, null, 2);
		})()
	""")
	print(f"JS 查找到的标签相关元素：\n{js_result}\n")

	# ── Step 2: 用 CDP 获取完整 DOM 状态（包含 Shadow DOM） ──
	print("\n[2] 获取 CDP DOM 状态（含 Shadow DOM）...")
	dom_state, metrics = await build_dom_state(
		client=browser.client,
		session_id=sid,
		config=browser._dom_collection_config,
		previous_selector_map=None,
	)
	print(f"DOM 状态获取完成: selector_map 大小={len(dom_state.selector_map)}")

	if dom_state._root is None:
		print("Error: DOM 树为空")
		await browser.stop()
		return

	# ── Step 3: 搜索标签相关元素 ──
	print("\n[3] 搜索标签相关的 DOM 元素...")
	tag_elements = find_tag_elements(dom_state._root.original_node)

	if not tag_elements:
		print("未在 DOM 树中找到标签相关元素！")
		print("尝试更广泛地搜索所有文本输入框...")
		# 退而求其次，找所有 input[type=text] 和 contenteditable
	else:
		print(f"找到 {len(tag_elements)} 个标签相关元素：\n")

	for depth, node in tag_elements:
		print_node_detail(node, depth)

	# ── Step 4: 查找所有 Shadow DOM 宿主 ──
	print("\n[4] 查找所有 Shadow DOM 宿主元素...")
	shadow_hosts = find_shadow_hosts(dom_state._root.original_node)
	if shadow_hosts:
		print(f"找到 {len(shadow_hosts)} 个 Shadow DOM 宿主：\n")
		for host in shadow_hosts:
			indent = "  " * host['depth']
			print(f"{indent}<{host['tag']}> class=\"{host['class']}\" id=\"{host['id']}\" bid={host['backend_node_id']} "
				  f"(shadow_children={host['shadow_children_count']})")
	else:
		print("未找到 Shadow DOM 宿主元素")

	# ── Step 5: 检查标签输入框是否出现在 selector_map 中 ──
	print("\n[5] 检查标签相关元素是否出现在可交互 selector_map 中...")
	tag_in_map = []
	for idx, elem in dom_state.selector_map.items():
		attrs = getattr(elem, 'attributes', {}) or {}
		tag_name = getattr(elem, 'tag_name', '').lower()
		all_text = (
			attrs.get('class', '') + ' ' +
			attrs.get('placeholder', '') + ' ' +
			attrs.get('role', '') + ' ' +
			attrs.get('aria-label', '') + ' ' +
			attrs.get('id', '')
		).lower()

		if any(kw in all_text for kw in TAG_KEYWORDS):
			tag_in_map.append({
				'index': idx,
				'tag': tag_name,
				'class': attrs.get('class', ''),
				'placeholder': attrs.get('placeholder', ''),
				'backend_node_id': elem.backend_node_id,
				'x': getattr(elem, 'x', 0),
				'y': getattr(elem, 'y', 0),
			})

	if tag_in_map:
		print(f"找到 {len(tag_in_map)} 个标签相关的可交互元素：")
		for item in tag_in_map:
			print(f"  索引 [{item['index']}] <{item['tag']}> class=\"{item['class']}\" "
				  f"placeholder=\"{item['placeholder']}\" bid={item['backend_node_id']} "
				  f"pos=({item['x']}, {item['y']})")
	else:
		print("标签相关元素未出现在 selector_map 中！")
		print("这意味着 TreeWalker 无法通过 click/input_text 交互")

	# ── Step 6: 尝试通过 JS 直接操作标签输入框 ──
	print("\n[6] 尝试通过 JS 定位并操作标签输入框...")

	# 尝试多种选择器策略
	js_probe = await browser.execute_js("""
		(function() {
			var findings = [];

			// 策略1: 常规 DOM 查询
			var selectors = [
				'input[placeholder*="标签"]',
				'input[placeholder*="tag"]',
				'input[placeholder*="Tag"]',
				'[class*="tag"] input',
				'[class*="tag-input"]',
				'[class*="TagInput"]',
				'[class*="bili-tag"]',
				'[data-module="tag"] input',
				'[class*="tag"] [contenteditable]',
				'[class*="tag"] textarea',
			];

			for (var i = 0; i < selectors.length; i++) {
				try {
					var els = document.querySelectorAll(selectors[i]);
					if (els.length > 0) {
						els.forEach(function(el) {
							findings.push({
								strategy: 'selector: ' + selectors[i],
								tag: el.tagName.toLowerCase(),
								class: el.className,
								id: el.id,
								placeholder: el.placeholder || '',
								type: el.type || '',
								contenteditable: el.contentEditable,
								rect: el.getBoundingClientRect().width + 'x' + el.getBoundingClientRect().height,
								inShadowDOM: el.getRootNode() !== document
							});
						});
					}
				} catch(e) {}
			}

			// 策略2: 遍历 Shadow DOM
			function searchShadowDOM(root) {
				var walker = document.createTreeWalker(
					root,
					NodeFilter.SHOW_ELEMENT,
					null
				);
				var node;
				while (node = walker.nextNode()) {
					if (node.shadowRoot) {
						searchShadowDOM(node.shadowRoot);
					}
					var text = (
						(node.getAttribute('class') || '') + ' ' +
						(node.getAttribute('placeholder') || '') + ' ' +
						(node.getAttribute('id') || '') + ' ' +
						(node.textContent || '').substring(0, 50)
					).toLowerCase();
					if (text.indexOf('tag') >= 0 || text.indexOf('标签') >= 0) {
						findings.push({
							strategy: 'shadow-walk',
							tag: node.tagName.toLowerCase(),
							class: node.getAttribute('class') || '',
							id: node.getAttribute('id') || '',
							placeholder: node.getAttribute('placeholder') || '',
							type: node.getAttribute('type') || '',
							contenteditable: node.contentEditable,
							inShadowDOM: node.getRootNode() !== document,
							parentHost: node.getRootNode().host
								? node.getRootNode().host.tagName + '.' + node.getRootNode().host.className
								: ''
						});
					}
				}
			}
			searchShadowDOM(document);

			return JSON.stringify(findings, null, 2);
		})()
	""")
	print(f"JS 探测结果：\n{js_probe}\n")

	# ── Step 7: 打印 element_tree_text 中与标签相关的部分 ──
	print("\n[7] 检查 DOM 文本树中的标签相关内容...")
	tree_text = dom_state.element_tree_text or ""
	for line in tree_text.split("\n"):
		if any(kw in line.lower() for kw in ['tag', '标签']):
			print(f"  {line}")

	# ── Step 8: 总结 ──
	print("\n" + "=" * 80)
	print("诊断总结：")
	print(f"  - selector_map 中标签相关元素: {len(tag_in_map)} 个")
	print(f"  - DOM 树中标签相关节点: {len(tag_elements)} 个")
	print(f"  - Shadow DOM 宿主: {len(shadow_hosts)} 个")

	if not tag_in_map and tag_elements:
		print("\n问题定位：标签元素存在于 DOM 中但未出现在 selector_map，")
		print("说明元素未被 ClickableElementDetector 检测为可交互。")
		print("可能原因：")
		print("  1. 元素在 Shadow DOM 内部，未被序列化到 element_tree_text")
		print("  2. 元素被判定为不可见 / paint_order 过低")
		print("  3. 元素不符合 is_interactive() 的任何规则")
	elif not tag_in_map and not tag_elements:
		print("\n问题定位：标签元素完全未出现在 DOM 树中。")
		print("可能原因：")
		print("  1. 标签输入区域使用自定义组件，内部元素在 Shadow DOM 中")
		print("  2. DOM 采集深度不够，未到达标签区域")

	await browser.stop()


if __name__ == "__main__":
	asyncio.run(main())
