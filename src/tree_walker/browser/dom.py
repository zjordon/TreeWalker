"""通过三源并行 CDP 调用构建增强 DOM 树。

使用三个并行的 CDP 数据源：
1. DOM.getDocument(depth=-1, pierce=True)：完整的 DOM 树结构，包括 shadow DOM
2. DOMSnapshot.captureSnapshot：布局/可见性/坐标数据
3. Accessibility.getFullAXTree：语义角色、名称、状态属性

额外采集：
- JS 点击监听器检测（Runtime.evaluate + getEventListeners）
- 设备像素比（Page.getLayoutMetrics）

所有数据通过 backendNodeId 交叉引用，合并为 EnhancedDOMTreeNode 树。
"""

from __future__ import annotations

import asyncio
import logging
import time

from cdp_use import CDPClient

from tree_walker.browser.cdp_timeout import run_cdp_batch
from tree_walker.browser.views import (
	DOMCollectionConfig,
	DOMCollectionMetrics,
	DOMDegradationLevel,
	DOMRect,
	DOMSelectorMap,
	EnhancedAXNode,
	EnhancedAXProperty,
	EnhancedDOMTreeNode,
	EnhancedSnapshotNode,
	NodeType,
	SerializedDOMState,
)

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────

REQUIRED_COMPUTED_STYLES = [
	'display',
	'visibility',
	'opacity',
	'cursor',
	'pointer-events',
	'overflow',
	'overflow-x',
	'overflow-y',
	'position',
	'background-color',
]

EMPTY_DOM_STATE = SerializedDOMState(
	_root=None,
	selector_map={},
	element_tree_text="",
	file_input_backend_ids=[],
)

# ── Helpers ────────────────────────────────────────────────────────────

def _parse_attrs(raw: list | None) -> dict[str, str]:
	attrs: dict[str, str] = {}
	if not raw:
		return attrs
	for i in range(0, len(raw) - 1, 2):
		attrs[raw[i]] = raw[i + 1][:200]
	return attrs

class ClickableElementDetector:
	"""14 条规则决策瀑布，检测 DOM 元素是否可交互。

	规则按信号强度排序，命中即短路返回。
	"""

	@staticmethod
	def is_interactive(node: EnhancedDOMTreeNode) -> bool:
		def has_form_control_descendant(element: EnhancedDOMTreeNode, max_depth: int = 2) -> bool:
			if max_depth <= 0:
				return False
			for child in element.children_and_shadow_roots:
				if child.node_type != NodeType.ELEMENT_NODE:
					continue
				if child.tag_name in {'input', 'select', 'textarea'}:
					return True
				if has_form_control_descendant(child, max_depth=max_depth - 1):
					return True
			return False

		# 规则 1: 节点类型守卫 — 只有 ELEMENT_NODE 才可能交互
		if node.node_type != NodeType.ELEMENT_NODE:
			return False

		# 规则 2: html/body 排除 — 文档结构元素不是交互目标
		if node.tag_name in {'html', 'body'}:
			return False

		# 规则 3: JS 点击监听器 — 最强信号（Vue @click, React onClick, Angular (click), 原生 addEventListener）
		if node.has_js_click_listener:
			return True

		# 规则 4: IFRAME/FRAME — 大尺寸 iframe 可能有可滚动内容
		if node.tag_name in {'iframe', 'frame'}:
			if node.snapshot_node and node.snapshot_node.bounds:
				if node.snapshot_node.bounds.width > 100 and node.snapshot_node.bounds.height > 100:
					return True

		# 规则 5: Label 处理 — 避免双重激活
		if node.tag_name == 'label':
			if node.attributes and node.attributes.get('for'):
				return False
			if has_form_control_descendant(node, max_depth=2):
				return True
			# 其他 label 继续后续规则

		# 规则 6: Span 包装器 — 检测包裹表单控件的 span
		if node.tag_name == 'span':
			if has_form_control_descendant(node, max_depth=2):
				return True
			# 其他 span 继续后续规则

		# 规则 7: 搜索元素检测 — class/id/data-* 含搜索关键词
		if node.attributes:
			search_indicators = {
				'search', 'magnify', 'glass', 'lookup', 'find',
				'query', 'search-icon', 'search-btn', 'search-button', 'searchbox',
			}
			class_list = node.attributes.get('class', '').lower().split()
			if any(indicator in ' '.join(class_list) for indicator in search_indicators):
				return True
			element_id = node.attributes.get('id', '').lower()
			if any(indicator in element_id for indicator in search_indicators):
				return True
			for attr_name, attr_value in node.attributes.items():
				if attr_name.startswith('data-') and any(indicator in attr_value.lower() for indicator in search_indicators):
					return True

		# 规则 8: AX 属性检查 — 可访问性树属性
		if node.ax_node and node.ax_node.properties:
			for prop in node.ax_node.properties:
				try:
					if prop.name == 'disabled' and prop.value:
						return False
					if prop.name == 'hidden' and prop.value:
						return False
					if prop.name in ('focusable', 'editable', 'settable') and prop.value:
						return True
					if prop.name in ('checked', 'expanded', 'pressed', 'selected'):
						return True
					if prop.name in ('required', 'autocomplete') and prop.value:
						return True
					if prop.name == 'keyshortcuts' and prop.value:
						return True
				except (AttributeError, ValueError):
					continue

		# 规则 9: 交互标签 — 原生 HTML 交互元素
		interactive_tags = {
			'button', 'input', 'select', 'textarea', 'a',
			'details', 'summary', 'option', 'optgroup',
		}
		if node.tag_name in interactive_tags:
			return True

		# 规则 10: 交互 HTML 属性 — 内联事件处理器和 tabindex
		if node.attributes:
			interactive_attributes = {
				'onclick', 'onmousedown', 'onmouseup', 'onkeydown', 'onkeyup', 'tabindex',
			}
			if any(attr in node.attributes for attr in interactive_attributes):
				return True

			# 规则 11: ARIA role（HTML 属性）
			if 'role' in node.attributes:
				interactive_roles = {
					'button', 'link', 'menuitem', 'option', 'radio', 'checkbox',
					'tab', 'textbox', 'combobox', 'slider', 'spinbutton',
					'search', 'searchbox', 'row', 'cell', 'gridcell',
				}
				if node.attributes['role'] in interactive_roles:
					return True

		# 规则 12: AX 树 role
		if node.ax_node and node.ax_node.role:
			interactive_ax_roles = {
				'button', 'link', 'menuitem', 'option', 'radio', 'checkbox',
				'tab', 'textbox', 'combobox', 'slider', 'spinbutton',
				'listbox', 'search', 'searchbox', 'row', 'cell', 'gridcell',
			}
			if node.ax_node.role in interactive_ax_roles:
				return True

		# 规则 13: 图标尺寸元素 — 10-50px + 有交互属性
		if (
			node.snapshot_node
			and node.snapshot_node.bounds
			and 10 <= node.snapshot_node.bounds.width <= 50
			and 10 <= node.snapshot_node.bounds.height <= 50
		):
			if node.attributes:
				icon_attributes = {'class', 'role', 'onclick', 'data-action', 'aria-label'}
				if any(attr in node.attributes for attr in icon_attributes):
					return True

		# 规则 14: cursor: pointer — 最终兜底
		if node.snapshot_node and node.snapshot_node.cursor_style and node.snapshot_node.cursor_style == 'pointer':
			return True

		return False


def is_interactive(node: EnhancedDOMTreeNode) -> bool:
	"""公开的交互检测函数，供 DOMTreeSerializer 使用。"""
	return ClickableElementDetector.is_interactive(node)

# ── Device pixel ratio ──────────────────────────────────────────────────

async def _get_viewport_ratio(client: CDPClient, session_id: str | None = None) -> float:
	"""通过 Page.getLayoutMetrics 获取设备像素比。"""
	try:
		metrics = await client.send.Page.getLayoutMetrics({}, session_id=session_id)
		visual = metrics.get('visualViewport', {})
		css_visual = metrics.get('cssVisualViewport', {})
		css_width = css_visual.get('clientWidth', 0)
		device_width = visual.get('clientWidth', css_width)
		if css_width > 0:
			return device_width / css_width
	except Exception as e:
		logger.debug("Viewport ratio detection failed: %s", e)
	return 1.0

# ── AX tree lookup ──────────────────────────────────────────────────────

def _build_enhanced_ax_node(ax_node: dict) -> EnhancedAXNode:
	"""将 CDP AX 节点转换为 EnhancedAXNode。"""
	properties: list[EnhancedAXProperty] | None = None
	if ax_node.get('properties'):
		properties = []
		for prop in ax_node['properties']:
			try:
				properties.append(EnhancedAXProperty(
					name=prop['name'],
					value=prop.get('value', {}).get('value', None),
				))
			except (KeyError, ValueError):
				pass
	return EnhancedAXNode(
		ax_node_id=ax_node['nodeId'],
		ignored=ax_node.get('ignored', False),
		role=ax_node.get('role', {}).get('value', None),
		name=ax_node.get('name', {}).get('value', None),
		description=ax_node.get('description', {}).get('value', None),
		properties=properties,
		child_ids=ax_node.get('childIds'),
	)

def _build_ax_lookup(ax_tree: dict) -> dict[int, dict]:
	"""从 Accessibility.getFullAXTree 构建 backendDOMNodeId -> AX 原始节点查找表。"""
	lookup: dict[int, dict] = {}
	for node in ax_tree.get('nodes', []):
		bid = node.get('backendDOMNodeId')
		if bid is not None:
			lookup[bid] = node
	return lookup

async def _get_ax_tree_for_all_frames(
	client: CDPClient,
	session_id: str | None = None,
) -> dict:
	"""为页面中所有 frame（主 frame + iframe）分别获取 AX 树并合并。

	Accessibility.getFullAXTree() 默认只返回主 frame 的无障碍树。
	对于包含 iframe 的页面，需要显式为每个 frame 单独请求 AX 树，
	然后合并为一个统一的节点列表。
	"""
	# Step 1: 获取 frame 层级树
	frame_tree = await client.send.Page.getFrameTree({}, session_id=session_id)

	# Step 2: 递归收集所有 frame ID
	def _collect_frame_ids(node: dict) -> list[str]:
		ids = [node['frame']['id']]
		for child in node.get('childFrames', []):
			ids.extend(_collect_frame_ids(child))
		return ids

	all_frame_ids = _collect_frame_ids(frame_tree['frameTree'])

	# Step 3: 对每个 frame 并行调用 getFullAXTree
	coros = [
		client.send.Accessibility.getFullAXTree(
			{'frameId': fid}, session_id=session_id,
		)
		for fid in all_frame_ids
	]
	ax_trees = await asyncio.gather(*coros)

	# Step 4: 合并所有 AX 节点
	merged: list[dict] = []
	for tree in ax_trees:
		merged.extend(tree.get('nodes', []))

	logger.debug("AX tree: %d frames, %d total nodes", len(all_frame_ids), len(merged))
	return {'nodes': merged}

# ── JS click listener detection ────────────────────────────────────────

async def _detect_js_click_listeners(
	client: CDPClient,
	session_id: str | None = None,
) -> set[int]:
	"""通过 getEventListeners 检测绑定了 JS 点击监听器的元素，返回 backendNodeId 集合。"""
	try:
		js_result = await client.send.Runtime.evaluate(
			{
				'expression': """
(() => {
	if (typeof getEventListeners !== 'function') return null;
	const all = document.querySelectorAll('*');
	if (all.length > 10000) return null;
	const matched = [];
	for (const el of all) {
		try {
			const ls = getEventListeners(el);
			if (ls.click || ls.mousedown || ls.mouseup || ls.pointerdown || ls.pointerup) {
				matched.push(el);
			}
		} catch (e) {}
	}
	return matched;
})()
""",
				'includeCommandLineAPI': True,
				'returnByValue': False,
			},
			session_id=session_id,
		)

		object_id = js_result.get('result', {}).get('objectId')
		if not object_id:
			return set()

		# 获取数组中每个元素的对象引用
		array_props = await client.send.Runtime.getProperties(
			{'objectId': object_id, 'ownProperties': True},
			session_id=session_id,
		)

		element_object_ids: list[str] = []
		for prop in array_props.get('result', []):
			name = prop.get('name', '')
			if name.isdigit():
				oid = prop.get('value', {}).get('objectId')
				if oid:
					element_object_ids.append(oid)

		# 并行解析 backendNodeId
		async def _get_backend_id(oid: str) -> int | None:
			try:
				info = await client.send.DOM.describeNode(
					{'objectId': oid}, session_id=session_id,
				)
				return info.get('node', {}).get('backendNodeId')
			except Exception:
				return None

		ids = await asyncio.gather(*[_get_backend_id(oid) for oid in element_object_ids])
		result = {bid for bid in ids if bid is not None}

		# 释放数组对象引用
		try:
			await client.send.Runtime.releaseObject(
				{'objectId': object_id}, session_id=session_id,
			)
		except Exception:
			pass

		logger.debug("Detected %d elements with JS click listeners", len(result))
		return result
	except Exception as e:
		logger.debug("JS click listener detection failed: %s", e)
		return set()

# ── Snapshot lookup ────────────────────────────────────────────────────

def _build_snapshot_lookup(snapshot: dict, device_pixel_ratio: float = 1.0) -> dict[int, EnhancedSnapshotNode]:
	"""从 DOMSnapshot 数据构建 backendNodeId -> EnhancedSnapshotNode 查找表。"""
	lookup: dict[int, EnhancedSnapshotNode] = {}
	if not snapshot.get('documents'):
		return lookup

	strings = snapshot.get('strings', [])
	for doc in snapshot['documents']:
		nodes = doc.get('nodes', {})
		layout = doc.get('layout', {})

		backend_ids = nodes.get('backendNodeId', [])
		layout_node_indices = layout.get('nodeIndex', [])
		layout_bounds = layout.get('bounds', [])
		layout_styles = layout.get('styles', [])
		layout_paint_orders = layout.get('paintOrders', [])
		layout_client_rects = layout.get('clientRects', [])
		layout_scroll_rects = layout.get('scrollRects', [])

		# isClickable 稀疏数据集化（O(1) 查找）
		is_clickable_data = nodes.get('isClickable')
		is_clickable_set: set[int] = set(is_clickable_data['index']) if is_clickable_data else set()

		# 预构建布局索引映射（首次出现优先）
		layout_map: dict[int, int] = {}
		for li, ni in enumerate(layout_node_indices):
			if ni not in layout_map:
				layout_map[ni] = li

		for i, bid in enumerate(backend_ids):
			is_clickable: bool | None = None
			bounds: DOMRect | None = None
			client_rects: DOMRect | None = None
			scroll_rects: DOMRect | None = None
			computed_styles: dict[str, str] = {}
			paint_order_val: int | None = None

			if i in is_clickable_set:
				is_clickable = True
			if i in layout_map:
				li = layout_map[i]
				if li < len(layout_bounds):
					b = layout_bounds[li]
					if len(b) >= 4:
						bounds = DOMRect(
							b[0] / device_pixel_ratio,
							b[1] / device_pixel_ratio,
							b[2] / device_pixel_ratio,
							b[3] / device_pixel_ratio,
						)
				if li < len(layout_client_rects):
					cr = layout_client_rects[li]
					if cr and len(cr) >= 4:
						client_rects = DOMRect(cr[0], cr[1], cr[2], cr[3])
				if li < len(layout_scroll_rects):
					sr = layout_scroll_rects[li]
					if sr and len(sr) >= 4:
						scroll_rects = DOMRect(sr[0], sr[1], sr[2], sr[3])
				if li < len(layout_styles):
					sidx_list = layout_styles[li]
					for si, sidx in enumerate(sidx_list):
						if si < len(REQUIRED_COMPUTED_STYLES) and 0 <= sidx < len(strings):
							computed_styles[REQUIRED_COMPUTED_STYLES[si]] = strings[sidx]
				if li < len(layout_paint_orders):
					paint_order_val = layout_paint_orders[li]

			cursor_style = computed_styles.get('cursor') if computed_styles else None
			lookup[bid] = EnhancedSnapshotNode(
				is_clickable=is_clickable,
				cursor_style=cursor_style,
				bounds=bounds,
				clientRects=client_rects,
				scrollRects=scroll_rects,
				computed_styles=computed_styles if computed_styles else None,
				paint_order=paint_order_val,
				stacking_contexts=None,
			)

	return lookup

# ── File input scan from DOM tree ─────────────────────────────────────

def _collect_file_inputs(node: dict) -> list[int]:
	"""遍历 DOM.getDocument 树查找 file input 的 backendNodeId（包括 shadow DOM）。"""
	results: list[int] = []
	if node.get('nodeType') == 1 and node.get('nodeName', '').upper() == 'INPUT':
		attrs = _parse_attrs(node.get('attributes'))
		if attrs.get('type', '').lower() == 'file':
			bid = node.get('backendNodeId')
			if bid is not None:
				results.append(bid)
	for child in node.get('children', []):
		results.extend(_collect_file_inputs(child))
	for shadow in node.get('shadowRoots', []):
		results.extend(_collect_file_inputs(shadow))
	cd = node.get('contentDocument')
	if cd:
		results.extend(_collect_file_inputs(cd))
	return results

# ── Cross-origin iframe helpers ───────────────────────────────────────

async def _build_frame_target_map(client: CDPClient) -> tuple[dict[str, str], dict[str, str]]:
	"""构建 frameId→targetId 和 url→targetId 映射，用于跨源 iframe target 解析。"""
	try:
		targets_result = await client.send.Target.getTargets({})
		frame_to_target: dict[str, str] = {}
		url_to_target: dict[str, str] = {}
		for t in targets_result.get('targetInfos', []):
			if t.get('type') == 'iframe':
				parent_frame_id = t.get('parentFrameId')
				if parent_frame_id:
					frame_to_target[parent_frame_id] = t['targetId']
				url = t.get('url', '')
				if url:
					url_base = url.split('?')[0].rstrip('/')
					if url_base:
						url_to_target[url_base] = t['targetId']
		logger.debug("Frame target map: %d frames, %d URLs", len(frame_to_target), len(url_to_target))
		return frame_to_target, url_to_target
	except Exception as e:
		logger.debug("Failed to build frame target map: %s", e)
		return {}, {}


async def _attach_to_iframe_target(client: CDPClient, target_id: str) -> str | None:
	"""附加到 iframe target 并返回 session_id，失败返回 None。"""
	try:
		result = await client.send.Target.attachToTarget(
			{'targetId': target_id, 'flatten': True},
		)
		return result.get('sessionId')
	except Exception as e:
		logger.debug("Failed to attach to iframe target %s: %s", target_id, e)
		return None


# ── Main ───────────────────────────────────────────────────────────────

async def _collect_cdp_sources(
	client: CDPClient,
	session_id: str | None,
	config: DOMCollectionConfig,
) -> tuple[
	dict | None, dict | None, dict | None, float,
	DOMDegradationLevel, DOMCollectionMetrics,
]:
	"""采集三源 CDP 数据，带两阶段超时重试和降级决策。

	返回 (snapshot, dom_tree, ax_tree, dpr, degradation_level, metrics)。
	"""
	metrics = DOMCollectionMetrics()

	# 创建 CDP 调用工厂（用于两阶段重试时重新创建协程）
	def _snapshot_factory():
		return client.send.DOMSnapshot.captureSnapshot(
			{
				'computedStyles': REQUIRED_COMPUTED_STYLES,
				'includeDOMRects': True,
				'includePaintOrder': True,
			},
			session_id=session_id,
		)

	def _dom_tree_factory():
		return client.send.DOM.getDocument(
			{'depth': -1, 'pierce': True},
			session_id=session_id,
		)

	def _ax_tree_factory():
		return _get_ax_tree_for_all_frames(client, session_id)

	def _dpr_factory():
		return _get_viewport_ratio(client, session_id)

	start = time.monotonic()
	batch = await run_cdp_batch(
		{
			'snapshot': _snapshot_factory,
			'dom_tree': _dom_tree_factory,
			'ax_tree': _ax_tree_factory,
			'dpr': _dpr_factory,
		},
		first_timeout=config.cdp_first_timeout,
		retry_timeout=config.cdp_retry_timeout,
	)
	metrics.total_ms = (time.monotonic() - start) * 1000

	# 填充 source_statuses
	for name, result in batch.sources.items():
		metrics.source_statuses[name] = result.status.value

	# 提取结果
	snapshot = batch.get('snapshot')
	dom_tree = batch.get('dom_tree')
	ax_tree = batch.get('ax_tree')
	dpr = batch.get('dpr', 1.0)

	# 降级决策
	if dom_tree is None:
		degradation = DOMDegradationLevel.FAILED
	elif snapshot is None:
		degradation = DOMDegradationLevel.MINIMAL
	elif ax_tree is None:
		degradation = DOMDegradationLevel.PARTIAL
	else:
		degradation = DOMDegradationLevel.FULL

	metrics.degradation_level = degradation

	if degradation != DOMDegradationLevel.FULL:
		logger.warning(
			"DOM collection degraded to %s (failed: %s)",
			degradation.value, ", ".join(batch.failed_names),
		)

	# iframe 数量限制
	if snapshot and snapshot.get('documents') and len(snapshot['documents']) > config.max_iframes:
		original_count = len(snapshot['documents'])
		snapshot['documents'] = snapshot['documents'][:config.max_iframes]
		logger.warning(
			"Truncated snapshot documents from %d to %d (max_iframes=%d)",
			original_count, config.max_iframes, config.max_iframes,
		)
		metrics.iframe_count = original_count

	return snapshot, dom_tree, ax_tree, dpr, degradation, metrics


async def _build_enhanced_dom_tree(
	client: CDPClient,
	session_id: str | None = None,
	viewport_threshold: int | None = 1000,
	*,
	iframe_depth: int = 0,
	max_iframe_depth: int = 5,
	_frame_target_map: dict[str, str] | None = None,
	_url_target_map: dict[str, str] | None = None,
	_initial_frame_offset: DOMRect | None = None,
	_config: DOMCollectionConfig | None = None,
) -> tuple[EnhancedDOMTreeNode | None, list[int], DOMCollectionMetrics]:
	"""构建增强 DOM 树（不含序列化）。递归处理跨源 iframe。

	返回 (EnhancedDOMTreeNode | None, file_input_backend_ids, metrics)。
	"""
	config = _config or DOMCollectionConfig()

	# Phase 1: JS 点击监听器检测（用 timeout 包装防挂起）
	js_click_ids: set[int] = set()
	try:
		js_click_ids = await asyncio.wait_for(
			_detect_js_click_listeners(client, session_id),
			timeout=5.0,
		)
	except asyncio.TimeoutError:
		logger.warning("JS listener detection timed out after 5s")
	except Exception as e:
		logger.debug("JS listener detection skipped: %s", e)

	# Phase 2: 两阶段超时 + 降级 CDP 采集
	snapshot, dom_tree, ax_tree, device_pixel_ratio, degradation, metrics = \
		await _collect_cdp_sources(client, session_id, config)

	if degradation == DOMDegradationLevel.FAILED:
		logger.error("DOM tree CDP call failed; returning empty state")
		return None, [], metrics

	# 构建查找表（降级模式下部分可能为空）
	snapshot_lookup = _build_snapshot_lookup(snapshot, device_pixel_ratio) if snapshot else {}
	ax_lookup = _build_ax_lookup(ax_tree) if ax_tree else {}

	root = dom_tree.get('root', {})
	file_input_backend_ids = _collect_file_inputs(root)
	logger.info(
		"DOM state: degradation=%s, snapshot_entries=%d, ax_nodes=%d, js_listeners=%d, file_inputs=%d, dpr=%.2f",
		degradation.value, len(snapshot_lookup), len(ax_lookup), len(js_click_ids),
		len(file_input_backend_ids), device_pixel_ratio,
	)

	# ── 构建增强 DOM 树 ─────────────────────────────────────────────
	enhanced_node_lookup: dict[int, EnhancedDOMTreeNode] = {}

	def _is_element_visible_according_to_all_parents(
		enode: EnhancedDOMTreeNode,
		frames: list[EnhancedDOMTreeNode],
	) -> bool:
		"""CSS 可见性 + 视口交集检查。

		先检查 CSS 可见性（display/visibility/opacity），再反向遍历 html_frames
		检查元素与每个帧视口的交叉关系。viewport_threshold 控制视口下方余量。
		"""
		if not enode.snapshot_node:
			# MINIMAL 降级：没有 snapshot 数据，假设可见
			if degradation == DOMDegradationLevel.MINIMAL:
				return True
			# Shadow DOM 元素可能缺少 snapshot 数据，不判定为不可见
			return enode.shadow_root_type is not None

		styles = enode.snapshot_node.computed_styles or {}
		if styles.get('display', '').lower() == 'none':
			return False
		if styles.get('visibility', '').lower() == 'hidden':
			return False
		try:
			if float(styles.get('opacity', '1')) <= 0:
				return False
		except (ValueError, TypeError):
			pass

		current_bounds = enode.snapshot_node.bounds
		if not current_bounds:
			return False

		# viewport_threshold=None 时跳过视口检查
		if viewport_threshold is None:
			return True

		# 反向遍历 html_frames（从最内层到最外层）
		for frame in reversed(frames):
			# IFRAME/FRAME 帧：加上 iframe 位置偏移
			if (
				frame.node_type == NodeType.ELEMENT_NODE
				and frame.node_name.upper() in ('IFRAME', 'FRAME')
				and frame.snapshot_node
				and frame.snapshot_node.bounds
			):
				iframe_bounds = frame.snapshot_node.bounds
				current_bounds.x += iframe_bounds.x
				current_bounds.y += iframe_bounds.y

			# HTML 帧：检查视口交叉
			if (
				frame.node_type == NodeType.ELEMENT_NODE
				and frame.node_name == 'HTML'
				and frame.snapshot_node
				and frame.snapshot_node.scrollRects
				and frame.snapshot_node.clientRects
			):
				viewport_right = frame.snapshot_node.clientRects.width
				viewport_bottom = frame.snapshot_node.clientRects.height

				adjusted_x = current_bounds.x - frame.snapshot_node.scrollRects.x
				adjusted_y = current_bounds.y - frame.snapshot_node.scrollRects.y

				frame_intersects = (
					adjusted_x < viewport_right
					and adjusted_x + current_bounds.width > 0
					and adjusted_y < viewport_bottom + viewport_threshold
					and adjusted_y + current_bounds.height > -viewport_threshold
				)

				if not frame_intersects:
					return False

				current_bounds.x -= frame.snapshot_node.scrollRects.x
				current_bounds.y -= frame.snapshot_node.scrollRects.y

		return True


	async def _construct_enhanced_node(
		node: dict,
		html_frames: list[EnhancedDOMTreeNode],
		total_frame_offset: DOMRect,
	) -> EnhancedDOMTreeNode:
		"""递归构建增强 DOM 树节点，三源融合。

		通过 backendNodeId 交叉引用 DOM 树 + AX 树 + Snapshot 数据。
		支持跨源 iframe：当 contentDocument 为 None 时，通过 CDP Target API
		附加到独立 target 递归构建 DOM 树。
		"""
		# 备忘录：避免重复处理同一节点
		nid = node.get('nodeId', 0)
		if nid in enhanced_node_lookup:
			return enhanced_node_lookup[nid]

		# 深拷贝偏移量，防止分支间共享可变状态
		frame_offset = DOMRect(
			total_frame_offset.x, total_frame_offset.y,
			total_frame_offset.width, total_frame_offset.height,
		)

		backend_id = node.get('backendNodeId', 0)
		node_type_val = node.get('nodeType', 1)

		# ── 三源数据查询 ──────────────────────────────────────────

		# 源2: Snapshot — 布局/可见性/坐标
		snapshot_data = snapshot_lookup.get(backend_id)

		# 源3: AX tree — 语义角色/名称/状态
		ax_raw = ax_lookup.get(backend_id)
		enhanced_ax = _build_enhanced_ax_node(ax_raw) if ax_raw else None

		# 属性解析（CDP 交替数组 → dict）
		attributes = _parse_attrs(node.get('attributes'))

		# Shadow root 类型
		shadow_root_type = node.get('shadowRootType')

		# 计算 absolute_position（bounds + iframe 偏移）
		absolute_position = None
		if snapshot_data and snapshot_data.bounds:
			absolute_position = DOMRect(
				x=snapshot_data.bounds.x + frame_offset.x,
				y=snapshot_data.bounds.y + frame_offset.y,
				width=snapshot_data.bounds.width,
				height=snapshot_data.bounds.height,
			)

		# ── 构建 EnhancedDOMTreeNode ──────────────────────────────
		dom_tree_node = EnhancedDOMTreeNode(
			node_id=nid,
			backend_node_id=backend_id,
			node_type=NodeType(node_type_val),
			node_name=node.get('nodeName', ''),
			node_value=node.get('nodeValue', ''),
			attributes=attributes,
			is_scrollable=node.get('isScrollable'),
			frame_id=node.get('frameId'),
			session_id=session_id,
			shadow_root_type=shadow_root_type,
			snapshot_node=snapshot_data,
			ax_node=enhanced_ax,
			has_js_click_listener=backend_id in js_click_ids,
			absolute_position=absolute_position,
			is_visible=None,  # 子树构建后计算
		)

		enhanced_node_lookup[nid] = dom_tree_node

		# 设置 parent_node（从缓存中按 parentId 查找）
		parent_id = node.get('parentId')
		if parent_id and parent_id in enhanced_node_lookup:
			dom_tree_node.parent_node = enhanced_node_lookup[parent_id]

		# ── html_frames 追踪和 iframe 偏移累积 ────────────────────
		updated_frames = list(html_frames)

		# HTML frame 滚动校正
		if (
			node_type_val == NodeType.ELEMENT_NODE.value
			and node.get('nodeName') == 'HTML'
			and node.get('frameId') is not None
		):
			updated_frames.append(dom_tree_node)
			if snapshot_data and snapshot_data.scrollRects:
				frame_offset.x -= snapshot_data.scrollRects.x
				frame_offset.y -= snapshot_data.scrollRects.y

		# IFRAME/FRAME 位置偏移
		if (
			node.get('nodeName', '').upper() in ('IFRAME', 'FRAME')
			and snapshot_data
			and snapshot_data.bounds
		):
			updated_frames.append(dom_tree_node)
			frame_offset.x += snapshot_data.bounds.x
			frame_offset.y += snapshot_data.bounds.y

		# ── 递归处理子结构 ────────────────────────────────────────

		# contentDocument（同源 iframe 内部文档）
		if node.get('contentDocument'):
			dom_tree_node.content_document = await _construct_enhanced_node(
				node['contentDocument'], updated_frames, frame_offset,
			)
			dom_tree_node.content_document.parent_node = dom_tree_node

		# 跨源 iframe：contentDocument 为 None，通过 CDP Target API 递归构建
		elif (
			node.get('nodeName', '').upper() in ('IFRAME', 'FRAME')
			and iframe_depth < max_iframe_depth
		):
			# 尺寸检查：≥ 50x50 像素
			should_process = False
			if snapshot_data and snapshot_data.bounds:
				w = snapshot_data.bounds.width
				h = snapshot_data.bounds.height
				should_process = w >= 50 and h >= 50

			if should_process:
				# 解析 target ID：优先 frameId 查找，回退 src URL 匹配
				iframe_target_id = None
				frame_id = node.get('frameId')
				if frame_id and _frame_target_map and frame_id in _frame_target_map:
					iframe_target_id = _frame_target_map[frame_id]
				if not iframe_target_id and attributes and _url_target_map:
					src = attributes.get('src', '')
					if src:
						src_base = src.split('?')[0].rstrip('/')
						iframe_target_id = _url_target_map.get(src_base)

				if iframe_target_id:
					try:
						iframe_sid = await _attach_to_iframe_target(client, iframe_target_id)
						if iframe_sid:
							try:
								iframe_root, _, _iframe_metrics = await _build_enhanced_dom_tree(
									client, iframe_sid, viewport_threshold,
									iframe_depth=iframe_depth + 1,
									max_iframe_depth=max_iframe_depth,
									_frame_target_map=_frame_target_map,
									_url_target_map=_url_target_map,
									_initial_frame_offset=frame_offset,
									_config=config,
								)
								if iframe_root:
									dom_tree_node.content_document = iframe_root
									iframe_root.parent_node = dom_tree_node
									dom_tree_node.target_id = iframe_target_id
							finally:
								try:
									await client.send.Target.detachFromTarget(
										{'sessionId': iframe_sid},
									)
								except Exception:
									pass
					except Exception as e:
						logger.debug(
							"Cross-origin iframe failed (target=%s): %s",
							iframe_target_id, e,
						)

		# shadowRoots（Shadow DOM 子树）
		if node.get('shadowRoots'):
			dom_tree_node.shadow_roots = []
			for shadow_root in node['shadowRoots']:
				sr_node = await _construct_enhanced_node(
					shadow_root, updated_frames, frame_offset,
				)
				sr_node.parent_node = dom_tree_node
				dom_tree_node.shadow_roots.append(sr_node)

		# children（常规子节点，过滤已在 shadow_roots 中的节点）
		if node.get('children'):
			dom_tree_node.children_nodes = []
			shadow_root_node_ids: set[int] = set()
			if node.get('shadowRoots'):
				for sr in node['shadowRoots']:
					shadow_root_node_ids.add(sr.get('nodeId', 0))

			for child in node['children']:
				if child.get('nodeId', 0) in shadow_root_node_ids:
					continue
				child_node = await _construct_enhanced_node(
					child, updated_frames, frame_offset,
				)
				dom_tree_node.children_nodes.append(child_node)

		# ── 可见性计算 ────────────────────────────────────────────
		dom_tree_node.is_visible = _is_element_visible_according_to_all_parents(
			dom_tree_node, updated_frames,
		)

		return dom_tree_node

	# 从 DOM 根节点开始构建
	initial_offset = _initial_frame_offset or DOMRect(0.0, 0.0, 0.0, 0.0)
	tree_root = await _construct_enhanced_node(root, [], initial_offset)

	return tree_root, file_input_backend_ids, metrics


async def build_dom_state(
	client: CDPClient,
	session_id: str | None = None,
	viewport_threshold: int | None = 1000,
	previous_selector_map: DOMSelectorMap | None = None,
	config: DOMCollectionConfig | None = None,
) -> tuple[SerializedDOMState, DOMCollectionMetrics]:
	"""通过三源并行 CDP 调用构建增强 DOM 状态。

	支持跨源 iframe 内容获取：通过 CDP Target API 将跨源 iframe 映射到
	独立 target，递归构建 DOM 树并合并。

	三源数据：
	1. DOM.getDocument — DOM 树结构（权威的父子关系、shadow DOM）
	2. DOMSnapshot.captureSnapshot — 布局/可见性/坐标数据
	3. Accessibility.getFullAXTree — 语义角色、名称、状态属性

	额外采集：
	- JS 点击监听器检测（getEventListeners）
	- 设备像素比（Page.getLayoutMetrics）
	"""
	frame_target_map, url_target_map = await _build_frame_target_map(client)

	tree_root, file_input_ids, metrics = await _build_enhanced_dom_tree(
		client, session_id, viewport_threshold,
		_frame_target_map=frame_target_map,
		_url_target_map=url_target_map,
		_config=config,
	)

	if not tree_root:
		metrics.degradation_level = DOMDegradationLevel.FAILED
		return EMPTY_DOM_STATE, metrics

	from tree_walker.browser.serializer import DOMTreeSerializer

	previous_state = SerializedDOMState(
		_root=None,
		selector_map=previous_selector_map or {},
		element_tree_text="",
		file_input_backend_ids=[],
	) if previous_selector_map else None

	serializer = DOMTreeSerializer(
		root_node=tree_root,
		previous_cached_state=previous_state,
		session_id=session_id,
	)
	serialized_state, _timing = serializer.serialize_accessible_elements()
	serialized_state.file_input_backend_ids = file_input_ids

	return serialized_state, metrics
