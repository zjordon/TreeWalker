"""DOM 序列化管线：将 EnhancedDOMTreeNode 树转换为 SimplifiedNode 树。

五步管线架构：
  Step 1: 创建简化树 (_create_simplified_tree) — 过滤无用节点
  Step 2: 绘制顺序过滤 (PaintOrderRemover) — 遮挡元素标记
  Step 3: 树优化 (_optimize_tree) — 后序遍历剪枝
  Step 4: 包围盒过滤 (_apply_bounding_box_filtering) — 传播型元素子元素排除
  Step 5: 分配交互索引 (_assign_interactive_indices) — 为可交互元素编号
"""

from __future__ import annotations

import logging
import time
from typing import Any

from tree_walker.browser.views import (
    DOMRect,
    DOMSelectorMap,
    EnhancedDOMTreeNode,
    NodeType,
    PropagatingBounds,
    SerializedDOMState,
    SimplifiedNode,
)

logger = logging.getLogger(__name__)

# 纯元数据/脚本标签，在简化树创建阶段直接丢弃
DISABLED_ELEMENTS = frozenset({'style', 'script', 'head', 'meta', 'link', 'title'})

# SVG 装饰性子元素（<svg> 本身保留，以折叠形式显示）
SVG_ELEMENTS = frozenset({
    'path', 'rect', 'g', 'circle', 'ellipse', 'line',
    'polyline', 'polygon', 'use', 'defs', 'clipPath',
    'mask', 'pattern', 'image', 'text', 'tspan',
})


class DOMTreeSerializer:
    """将 EnhancedDOMTreeNode 树序列化为 SimplifiedNode 树供 LLM 消费。"""

    # 会将自身包围盒"传播"给所有后代的元素
    # role=None 表示不检查 role，仅匹配标签名
    PROPAGATING_ELEMENTS: list[dict[str, str | None]] = [
        {'tag': 'a', 'role': None},
        {'tag': 'button', 'role': None},
        {'tag': 'div', 'role': 'button'},
        {'tag': 'div', 'role': 'combobox'},
        {'tag': 'span', 'role': 'button'},
        {'tag': 'span', 'role': 'combobox'},
        {'tag': 'input', 'role': 'combobox'},
    ]

    DEFAULT_CONTAINMENT_THRESHOLD = 0.99

    def __init__(
        self,
        root_node: EnhancedDOMTreeNode,
        previous_cached_state: SerializedDOMState | None = None,
        enable_bbox_filtering: bool = True,
        containment_threshold: float | None = None,
        paint_order_filtering: bool = True,
        session_id: str | None = None,
    ):
        self.root_node = root_node
        self._selector_map: DOMSelectorMap = {}
        self._previous_cached_selector_map = (
            previous_cached_state.selector_map if previous_cached_state else None
        )
        self.timing_info: dict[str, float] = {}
        self._clickable_cache: dict[int, bool] = {}
        self.enable_bbox_filtering = enable_bbox_filtering
        self.containment_threshold = containment_threshold or self.DEFAULT_CONTAINMENT_THRESHOLD
        self.paint_order_filtering = paint_order_filtering
        self.session_id = session_id

    # ── 管线入口 ─────────────────────────────────────────────────────

    def serialize_accessible_elements(self) -> tuple[SerializedDOMState, dict[str, float]]:
        """五步管线入口，返回 (SerializedDOMState, timing_info)。"""
        start_total = time.time()

        # 重置状态
        self._selector_map = {}
        self._clickable_cache = {}

        # Step 1: 创建简化树
        start = time.time()
        simplified_tree = self._create_simplified_tree(self.root_node)
        self.timing_info['create_simplified_tree'] = time.time() - start

        # Step 2: 绘制顺序过滤
        if self.paint_order_filtering and simplified_tree:
            start = time.time()
            from tree_walker.browser.paint_order import PaintOrderRemover
            PaintOrderRemover(simplified_tree).calculate_paint_order()
            self.timing_info['paint_order_filtering'] = time.time() - start

        # Step 3: 树优化
        start = time.time()
        optimized_tree = self._optimize_tree(simplified_tree)
        self.timing_info['optimize_tree'] = time.time() - start

        # Step 4: 包围盒过滤
        if self.enable_bbox_filtering and optimized_tree:
            start = time.time()
            filtered_tree = self._apply_bounding_box_filtering(optimized_tree)
            self.timing_info['bbox_filtering'] = time.time() - start
        else:
            filtered_tree = optimized_tree

        # Step 5: 分配交互索引
        start = time.time()
        self._assign_interactive_indices_and_mark_new_nodes(filtered_tree)
        self.timing_info['assign_interactive_indices'] = time.time() - start

        # 生成文本输出
        from tree_walker.browser.views import DEFAULT_INCLUDE_ATTRIBUTES

        element_tree_text = self.serialize_tree(filtered_tree, DEFAULT_INCLUDE_ATTRIBUTES)

        self.timing_info['serialize_accessible_elements_total'] = time.time() - start_total

        return (
            SerializedDOMState(
                _root=filtered_tree,
                selector_map=self._selector_map,
                element_tree_text=element_tree_text,
            ),
            self.timing_info,
        )

    # ── Step 1: 创建简化树 ──────────────────────────────────────────

    def _create_simplified_tree(self, node: EnhancedDOMTreeNode, depth: int = 0) -> SimplifiedNode | None:
        """将 EnhancedDOMTreeNode 递归转换为 SimplifiedNode 树。

        四种节点类型处理：
        - DOCUMENT_NODE → 取第一个有效子节点作为根
        - DOCUMENT_FRAGMENT_NODE → 始终保留（Shadow DOM）
        - ELEMENT_NODE → 过滤禁用/SVG/排除标记后，按可见性保留
        - TEXT_NODE → 可见 + 非空 + len > 1 时保留
        """
        # ── DOCUMENT_NODE: 透传，取第一个有效子节点 ──
        if node.node_type == NodeType.DOCUMENT_NODE:
            for child in node.children_and_shadow_roots:
                simplified_child = self._create_simplified_tree(child, depth + 1)
                if simplified_child:
                    return simplified_child
            return None

        # ── DOCUMENT_FRAGMENT_NODE: Shadow DOM 片段始终保留 ──
        if node.node_type == NodeType.DOCUMENT_FRAGMENT_NODE:
            simplified = SimplifiedNode(original_node=node, children=[])
            for child in node.children_and_shadow_roots:
                simplified_child = self._create_simplified_tree(child, depth + 1)
                if simplified_child:
                    simplified.children.append(simplified_child)
            return simplified if simplified.children else SimplifiedNode(original_node=node, children=[])

        # ── ELEMENT_NODE: 主要过滤逻辑 ──
        if node.node_type == NodeType.ELEMENT_NODE:
            return self._process_element_node(node, depth)

        # ── TEXT_NODE: 条件保留 ──
        if node.node_type == NodeType.TEXT_NODE:
            is_visible = node.snapshot_node is not None and node.is_visible
            if (
                is_visible
                and node.node_value
                and node.node_value.strip()
                and len(node.node_value.strip()) > 1
            ):
                return SimplifiedNode(original_node=node, children=[])
            return None

        return None

    def _process_element_node(self, node: EnhancedDOMTreeNode, depth: int) -> SimplifiedNode | None:
        """处理 ELEMENT_NODE 类型的简化树创建。"""
        tag_lower = node.node_name.lower()

        # 跳过禁用元素 (script/style/head/meta/link/title)
        if tag_lower in DISABLED_ELEMENTS:
            return None

        # 跳过 SVG 子元素 (path/rect/g/circle 等)
        if tag_lower in SVG_ELEMENTS:
            return None

        # 排除标记检查
        attributes = node.attributes or {}
        if self._is_excluded(attributes):
            return None

        # IFRAME/FRAME 特殊处理
        if node.node_name in ('IFRAME', 'FRAME'):
            return self._process_iframe(node, depth)

        # ── 可见性判定 ──
        is_visible = node.is_visible
        is_scrollable = node.is_actually_scrollable
        has_shadow_content = bool(node.children_and_shadow_roots)
        is_shadow_host = any(
            child.node_type == NodeType.DOCUMENT_FRAGMENT_NODE
            and child.shadow_root_type != 'user-agent'
            for child in node.children_and_shadow_roots
        )

        # 强制可见：带 aria-* 或 pseudo 属性的元素
        if not is_visible and node.attributes:
            if any(attr.startswith(('aria-', 'pseudo')) for attr in node.attributes):
                is_visible = True

        # 强制可见：隐藏的 file input (Bootstrap opacity:0 模式)
        is_file_input = (
            tag_lower == 'input'
            and node.attributes
            and node.attributes.get('type') == 'file'
        )
        if not is_visible and is_file_input:
            is_visible = True

        # 保留条件：可见 / 可滚动 / 有子内容 / shadow 宿主
        if is_visible or is_scrollable or has_shadow_content or is_shadow_host:
            simplified = SimplifiedNode(
                original_node=node,
                children=[],
                is_shadow_host=is_shadow_host,
            )

            # 递归处理所有子节点（包括 shadow roots，跳过 UA 内部 shadow）
            for child in node.children_and_shadow_roots:
                if (child.node_type == NodeType.DOCUMENT_FRAGMENT_NODE
                        and child.shadow_root_type == 'user-agent'):
                    continue
                simplified_child = self._create_simplified_tree(child, depth + 1)
                if simplified_child:
                    simplified.children.append(simplified_child)

            # 复合控件处理
            self._add_compound_components(simplified, node)

            # Shadow 宿主始终保留
            if is_shadow_host and simplified.children:
                return simplified

            # 有意义的节点保留
            if is_visible or is_scrollable or simplified.children:
                return simplified

        return None

    def _is_excluded(self, attributes: dict[str, str]) -> bool:
        """检查元素是否被排除标记 (data-browser-use-exclude)。"""
        exclude_attr = None
        if self.session_id:
            exclude_attr = attributes.get(f'data-browser-use-exclude-{self.session_id}')
        if not exclude_attr:
            exclude_attr = attributes.get('data-browser-use-exclude')
        return isinstance(exclude_attr, str) and exclude_attr.lower() == 'true'

    def _process_iframe(self, node: EnhancedDOMTreeNode, depth: int) -> SimplifiedNode | None:
        """处理 IFRAME/FRAME 节点，递归处理其 contentDocument。"""
        if not node.content_document:
            return None
        simplified = SimplifiedNode(original_node=node, children=[])
        for child in node.content_document.children_nodes or []:
            simplified_child = self._create_simplified_tree(child, depth + 1)
            if simplified_child is not None:
                simplified.children.append(simplified_child)
        return simplified

    # ── 复合控件处理 ──────────────────────────────────────────────────

    def _add_compound_components(self, simplified: SimplifiedNode, node: EnhancedDOMTreeNode) -> None:
        """为复合控件添加虚拟子组件信息，帮助 LLM 理解控件结构。"""
        if node.tag_name not in ('input', 'select', 'details', 'audio', 'video'):
            return

        if node.tag_name == 'input':
            input_type = (node.attributes or {}).get('type', '')
            if input_type not in ('date', 'time', 'datetime-local', 'month', 'week', 'range', 'number', 'color', 'file'):
                return
            if input_type in ('date', 'time', 'datetime-local', 'month', 'week'):
                # 日期/时间输入通过 placeholder/format 属性展示格式，不需要虚拟子组件
                return
        elif not node.ax_node or not node.ax_node.child_ids:
            return

        element_type = node.tag_name
        input_type = (node.attributes or {}).get('type', '') if node.attributes else ''

        if element_type == 'input':
            self._add_input_compound(simplified, node, input_type)
        elif element_type == 'select':
            self._add_select_compound(simplified, node)
        elif element_type == 'details':
            node._compound_children.extend([
                {'role': 'button', 'name': 'Toggle Disclosure', 'valuemin': None, 'valuemax': None, 'valuenow': None},
                {'role': 'region', 'name': 'Content Area', 'valuemin': None, 'valuemax': None, 'valuenow': None},
            ])
            simplified.is_compound_component = True
        elif element_type in ('audio', 'video'):
            components = [
                {'role': 'button', 'name': 'Play/Pause', 'valuemin': None, 'valuemax': None, 'valuenow': None},
                {'role': 'slider', 'name': 'Progress', 'valuemin': 0, 'valuemax': 100, 'valuenow': None},
                {'role': 'button', 'name': 'Mute', 'valuemin': None, 'valuemax': None, 'valuenow': None},
                {'role': 'slider', 'name': 'Volume', 'valuemin': 0, 'valuemax': 100, 'valuenow': None},
            ]
            if element_type == 'video':
                components.append({'role': 'button', 'name': 'Fullscreen', 'valuemin': None, 'valuemax': None, 'valuenow': None})
            node._compound_children.extend(components)
            simplified.is_compound_component = True

    def _add_input_compound(
        self, simplified: SimplifiedNode, node: EnhancedDOMTreeNode, input_type: str,
    ) -> None:
        """处理 input 类型的复合控件。"""
        attrs = node.attributes or {}

        if input_type == 'range':
            min_val = attrs.get('min', '0')
            max_val = attrs.get('max', '100')
            node._compound_children.append({
                'role': 'slider',
                'name': 'Value',
                'valuemin': _safe_parse_number(min_val, 0.0),
                'valuemax': _safe_parse_number(max_val, 100.0),
                'valuenow': None,
            })
            simplified.is_compound_component = True

        elif input_type == 'number':
            min_val = attrs.get('min')
            max_val = attrs.get('max')
            node._compound_children.extend([
                {'role': 'button', 'name': 'Increment', 'valuemin': None, 'valuemax': None, 'valuenow': None},
                {'role': 'button', 'name': 'Decrement', 'valuemin': None, 'valuemax': None, 'valuenow': None},
                {
                    'role': 'textbox', 'name': 'Value',
                    'valuemin': _safe_parse_optional_number(min_val),
                    'valuemax': _safe_parse_optional_number(max_val),
                    'valuenow': None,
                },
            ])
            simplified.is_compound_component = True

        elif input_type == 'color':
            node._compound_children.extend([
                {'role': 'textbox', 'name': 'Hex Value', 'valuemin': None, 'valuemax': None, 'valuenow': None},
                {'role': 'button', 'name': 'Color Picker', 'valuemin': None, 'valuemax': None, 'valuenow': None},
            ])
            simplified.is_compound_component = True

        elif input_type == 'file':
            current_value = 'None'
            if node.ax_node and node.ax_node.properties:
                for prop in node.ax_node.properties:
                    if prop.name == 'valuetext' and prop.value:
                        val = str(prop.value).strip()
                        if val and val.lower() not in ('', 'no file chosen', 'no file selected'):
                            current_value = val
                        break
                    elif prop.name == 'value' and prop.value:
                        val = str(prop.value).strip()
                        if val:
                            if '\\' in val:
                                current_value = val.rsplit('\\', 1)[-1]
                            elif '/' in val:
                                current_value = val.rsplit('/', 1)[-1]
                            else:
                                current_value = val
                        break

            multiple = 'multiple' in attrs
            node._compound_children.extend([
                {'role': 'button', 'name': 'Browse Files', 'valuemin': None, 'valuemax': None, 'valuenow': None},
                {
                    'role': 'textbox',
                    'name': f'{"Files" if multiple else "File"} Selected',
                    'valuemin': None,
                    'valuemax': None,
                    'valuenow': current_value,
                },
            ])
            simplified.is_compound_component = True

    def _add_select_compound(self, simplified: SimplifiedNode, node: EnhancedDOMTreeNode) -> None:
        """处理 select 复合控件。"""
        components: list[dict[str, Any]] = [
            {'role': 'button', 'name': 'Dropdown Toggle', 'valuemin': None, 'valuemax': None, 'valuenow': None},
        ]
        options_info = self._extract_select_options(node)
        if options_info:
            opt_component: dict[str, Any] = {
                'role': 'listbox',
                'name': 'Options',
                'valuemin': None,
                'valuemax': None,
                'valuenow': None,
                'options_count': options_info['count'],
                'first_options': options_info['first_options'],
            }
            if options_info['format_hint']:
                opt_component['format_hint'] = options_info['format_hint']
            components.append(opt_component)
        else:
            components.append({'role': 'listbox', 'name': 'Options', 'valuemin': None, 'valuemax': None, 'valuenow': None})

        node._compound_children.extend(components)
        simplified.is_compound_component = True

    def _extract_select_options(self, select_node: EnhancedDOMTreeNode) -> dict[str, Any] | None:
        """提取 select 元素的选项信息。"""
        if not select_node.children:
            return None

        options: list[dict[str, str]] = []
        option_values: list[str] = []

        def extract_recursive(n: EnhancedDOMTreeNode) -> None:
            if n.tag_name == 'option':
                text = ''
                value = ''
                if n.attributes and 'value' in n.attributes:
                    value = str(n.attributes['value']).strip()
                for child in n.children:
                    if child.node_type == NodeType.TEXT_NODE and child.node_value:
                        text += child.node_value.strip() + ' '
                text = text.strip()
                if not value and text:
                    value = text
                if text or value:
                    options.append({'text': text, 'value': value})
                    option_values.append(value)
            elif n.tag_name == 'optgroup':
                for child in n.children:
                    extract_recursive(child)
            else:
                for child in n.children:
                    extract_recursive(child)

        for child in select_node.children:
            extract_recursive(child)

        if not options:
            return None

        first_options = []
        for opt in options[:4]:
            display = opt['text'] or opt['value']
            if display:
                first_options.append(display[:30] + ('...' if len(display) > 30 else ''))

        if len(options) > 4:
            first_options.append(f'... {len(options) - 4} more options...')

        format_hint = None
        if len(option_values) >= 2:
            vals = [v for v in option_values[:5] if v]
            if vals:
                if all(v.isdigit() for v in vals):
                    format_hint = 'numeric'
                elif all(len(v) == 2 and v.isupper() for v in vals):
                    format_hint = 'country/state codes'
                elif all('/' in v or '-' in v for v in vals):
                    format_hint = 'date/path format'
                elif any('@' in v for v in vals):
                    format_hint = 'email addresses'

        return {'count': len(options), 'first_options': first_options, 'format_hint': format_hint}

    # ── Step 3: 树优化 ──────────────────────────────────────────────

    def _optimize_tree(self, node: SimplifiedNode | None) -> SimplifiedNode | None:
        """后序遍历剪枝：清除子节点被剪除后变成无意义叶节点的中间容器。"""
        if not node:
            return None

        optimized_children = []
        for child in node.children:
            optimized = self._optimize_tree(child)
            if optimized:
                optimized_children.append(optimized)
        node.children = optimized_children

        is_visible = node.original_node.snapshot_node is not None and node.original_node.is_visible
        is_file_input = (
            node.original_node.tag_name == 'input'
            and node.original_node.attributes
            and node.original_node.attributes.get('type') == 'file'
        )

        if (
            is_visible
            or node.original_node.is_actually_scrollable
            or node.original_node.node_type == NodeType.TEXT_NODE
            or node.children
            or is_file_input
        ):
            return node
        return None

    # ── Step 4: 包围盒过滤 ──────────────────────────────────────────

    def _apply_bounding_box_filtering(self, node: SimplifiedNode | None) -> SimplifiedNode | None:
        """过滤被交互父元素包围盒完全包含的子元素。

        传播型元素（<a>、<button> 等）会将自身包围盒传播给所有后代。
        当后代的包围盒 ≥99% 位于传播型祖先内部时，标记为 excluded_by_parent。
        """
        if not node:
            return None

        self._filter_tree_recursive(node, active_bounds=None, depth=0)

        excluded_count = self._count_excluded_nodes(node)
        if excluded_count > 0:
            logger.debug("BBox filtering excluded %d nodes", excluded_count)

        return node

    def _filter_tree_recursive(
        self,
        node: SimplifiedNode,
        active_bounds: PropagatingBounds | None,
        depth: int,
    ) -> None:
        """递归过滤：包围盒从传播型祖先向所有后代传播，直到被新的传播型元素覆盖。"""
        # 排除判定：如果当前节点被活跃包围盒包含
        if active_bounds and self._should_exclude_child(node, active_bounds):
            node.excluded_by_parent = True

        # 传播检测：当前节点是否启动新的包围盒传播（即使已被排除也检测）
        new_bounds = None
        tag = node.original_node.tag_name
        role = node.original_node.attributes.get('role') if node.original_node.attributes else None
        if self._is_propagating_element({'tag': tag, 'role': role}):
            if node.original_node.snapshot_node and node.original_node.snapshot_node.bounds:
                new_bounds = PropagatingBounds(
                    tag=tag,
                    bounds=node.original_node.snapshot_node.bounds,
                    node_id=node.original_node.node_id,
                    depth=depth,
                )

        # 向子节点传播：使用新的包围盒（如果有），否则继承父级的
        propagate_bounds = new_bounds if new_bounds else active_bounds
        for child in node.children:
            self._filter_tree_recursive(child, propagate_bounds, depth + 1)

    def _should_exclude_child(self, node: SimplifiedNode, active_bounds: PropagatingBounds) -> bool:
        """判定子节点是否应被排除。采用"先检查包含，再检查例外"的两段式逻辑。"""
        # 文本节点永不排除
        if node.original_node.node_type == NodeType.TEXT_NODE:
            return False

        # 无 bounds 数据，无法判定空间关系
        if not node.original_node.snapshot_node or not node.original_node.snapshot_node.bounds:
            return False

        child_bounds = node.original_node.snapshot_node.bounds

        # 空间包含检查：99% 阈值
        if not self._is_contained(child_bounds, active_bounds.bounds, self.containment_threshold):
            return False

        # ── 例外规则：以下情况即使满足包含条件也不排除 ──

        child_tag = node.original_node.tag_name
        child_role = node.original_node.attributes.get('role') if node.original_node.attributes else None
        child_attrs = {'tag': child_tag, 'role': child_role}

        # 1. 表单元素需要独立交互
        if child_tag in ('input', 'select', 'textarea', 'label'):
            return False

        # 2. 传播型元素本身（如嵌套按钮）
        if self._is_propagating_element(child_attrs):
            return False

        # 3. 显式 onclick 处理器
        if node.original_node.attributes and 'onclick' in node.original_node.attributes:
            return False

        # 4. 非空 aria-label（语义上标注为独立交互目标）
        if node.original_node.attributes:
            aria_label = node.original_node.attributes.get('aria-label')
            if aria_label and aria_label.strip():
                return False

        # 5. 交互 role
        if node.original_node.attributes:
            role = node.original_node.attributes.get('role')
            if role in ('button', 'link', 'checkbox', 'radio', 'tab', 'menuitem', 'option'):
                return False

        return True

    def _is_contained(self, child: DOMRect, parent: DOMRect, threshold: float) -> bool:
        """检查子元素包围盒被父元素包含的比例是否 ≥ threshold。"""
        x_overlap = max(0, min(child.x + child.width, parent.x + parent.width) - max(child.x, parent.x))
        y_overlap = max(0, min(child.y + child.height, parent.y + parent.height) - max(child.y, parent.y))

        intersection_area = x_overlap * y_overlap
        child_area = child.width * child.height

        if child_area == 0:
            return False

        return (intersection_area / child_area) >= threshold

    def _is_propagating_element(self, attributes: dict[str, str | None]) -> bool:
        """检查元素是否匹配传播型元素列表。role=None 表示不检查 role。"""
        for pattern in self.PROPAGATING_ELEMENTS:
            match = True
            for key in ('tag', 'role'):
                pattern_val = pattern.get(key)
                if pattern_val is not None and pattern_val != attributes.get(key):
                    match = False
                    break
            if match:
                return True
        return False

    def _count_excluded_nodes(self, node: SimplifiedNode, count: int = 0) -> int:
        """统计被排除的节点数量（调试用）。"""
        if node.excluded_by_parent:
            count += 1
        for child in node.children:
            count = self._count_excluded_nodes(child, count)
        return count

    # ── Step 5: 分配交互索引 ────────────────────────────────────────

    def _is_interactive_cached(self, node: EnhancedDOMTreeNode) -> bool:
        """带缓存的 is_interactive 检测，避免重复计算。"""
        from tree_walker.browser.dom import is_interactive

        if node.node_id not in self._clickable_cache:
            start = time.time()
            result = is_interactive(node)
            elapsed = time.time() - start
            self.timing_info.setdefault('clickable_detection_time', 0.0)
            self.timing_info['clickable_detection_time'] += elapsed
            self._clickable_cache[node.node_id] = result
        return self._clickable_cache[node.node_id]

    def _is_inside_shadow_dom(self, node: SimplifiedNode) -> bool:
        """向上遍历父节点链判断是否在 shadow DOM 内。"""
        current = node.original_node.parent_node
        while current is not None:
            if current.node_type == NodeType.DOCUMENT_FRAGMENT_NODE and current.shadow_root_type is not None:
                return True
            current = current.parent_node
        return False

    def _has_interactive_descendants(self, node: SimplifiedNode) -> bool:
        """检查节点是否有交互后代（不含自身）。"""
        for child in node.children:
            if self._is_interactive_cached(child.original_node):
                return True
            if self._has_interactive_descendants(child):
                return True
        return False

    def _assign_interactive_indices_and_mark_new_nodes(self, node: SimplifiedNode | None) -> None:
        """遍历简化树，为交互元素分配索引并标记新元素。"""
        if not node:
            return

        if not node.excluded_by_parent:
            is_interactive_assign = self._is_interactive_cached(node.original_node)
            is_visible = node.original_node.snapshot_node is not None and node.original_node.is_visible
            is_scrollable = node.original_node.is_actually_scrollable

            is_file_input = (
                node.original_node.tag_name == 'input'
                and node.original_node.attributes
                and node.original_node.attributes.get('type') == 'file'
            )

            is_shadow_dom_element = (
                is_interactive_assign
                and node.original_node.snapshot_node is None
                and node.original_node.tag_name in ('input', 'button', 'select', 'textarea', 'a')
                and self._is_inside_shadow_dom(node)
            )

            # 有 JS click listener 的元素绕过 paint order 过滤
            # 因为点击基于 DOM 坐标，不依赖视觉层叠
            bypass_paint_order = node.original_node.has_js_click_listener

            should_make_interactive = False

            if is_scrollable and not node.ignored_by_paint_order:
                # 下拉容器始终可交互
                attrs = node.original_node.attributes or {}
                role = attrs.get('role', '').lower()
                tag = node.original_node.tag_name
                class_attr = attrs.get('class', '').lower()
                class_list = class_attr.split() if class_attr else []

                is_dropdown = (
                    role in ('listbox', 'menu', 'combobox', 'menubar', 'tree', 'grid')
                    or tag == 'select'
                    or 'dropdown' in class_list
                    or 'dropdown-menu' in class_list
                    or 'select-menu' in class_list
                )
                if is_dropdown:
                    should_make_interactive = True
                elif not self._has_interactive_descendants(node):
                    should_make_interactive = True

            elif is_interactive_assign and (is_visible or is_file_input or is_shadow_dom_element):
                if bypass_paint_order or not node.ignored_by_paint_order:
                    should_make_interactive = True

            if should_make_interactive:
                node.is_interactive = True
                node.highlight_index = node.original_node.backend_node_id
                self._selector_map[node.highlight_index] = node.original_node

                if node.is_compound_component:
                    node.is_new = True
                elif self._previous_cached_selector_map:
                    if node.original_node.backend_node_id not in self._previous_cached_selector_map:
                        node.is_new = True

        for child in node.children:
            self._assign_interactive_indices_and_mark_new_nodes(child)

    # ── 文本输出 ─────────────────────────────────────────────────────

    @staticmethod
    def serialize_tree(
        node: SimplifiedNode | None,
        include_attributes: list[str],
        depth: int = 0,
    ) -> str:
        """将 SimplifiedNode 树序列化为 LLM 可读的缩进文本。"""
        if not node:
            return ''

        # 被排除的节点：跳过自身，但处理子节点
        if node.excluded_by_parent:
            parts: list[str] = []
            for child in node.children:
                text = DOMTreeSerializer.serialize_tree(child, include_attributes, depth)
                if text:
                    parts.append(text)
            return '\n'.join(parts)

        parts = []
        indent = '\t' * depth
        next_depth = depth

        if node.original_node.node_type == NodeType.ELEMENT_NODE:
            if not node.should_display:
                for child in node.children:
                    text = DOMTreeSerializer.serialize_tree(child, include_attributes, depth)
                    if text:
                        parts.append(text)
                return '\n'.join(parts)

            tag = node.original_node.tag_name

            # SVG: 折叠显示
            if tag == 'svg':
                shadow_prefix = _shadow_prefix(node)
                line = f'{indent}{shadow_prefix}'
                if node.is_interactive:
                    line += f'{"*" if node.is_new else ""}[{node.highlight_index}]'
                line += '<svg'
                svg_attrs = _build_attributes_string(node.original_node, include_attributes)
                if svg_attrs:
                    line += f' {svg_attrs}'
                line += ' /> <!-- SVG content collapsed -->'
                parts.append(line)
                return '\n'.join(parts)

            # 交互 / 可滚动 / iframe 元素
            is_any_scrollable = (
                node.original_node.is_actually_scrollable
                or node.original_node.is_scrollable
            )
            should_show_scroll = node.original_node.should_show_scroll_info
            if (
                node.is_interactive
                or is_any_scrollable
                or node.original_node.node_name.upper() in ('IFRAME', 'FRAME')
            ):
                next_depth += 1
                attr_str = _build_attributes_string(node.original_node, include_attributes)

                # 复合组件信息
                if node.original_node._compound_children:
                    compound_parts: list[str] = []
                    for ci in node.original_node._compound_children:
                        items: list[str] = []
                        if ci.get('name'):
                            items.append(f'name={ci["name"]}')
                        if ci.get('role'):
                            items.append(f'role={ci["role"]}')
                        if ci.get('valuemin') is not None:
                            items.append(f'min={ci["valuemin"]}')
                        if ci.get('valuemax') is not None:
                            items.append(f'max={ci["valuemax"]}')
                        if ci.get('valuenow') is not None:
                            items.append(f'current={ci["valuenow"]}')
                        if ci.get('options_count') is not None:
                            items.append(f'count={ci["options_count"]}')
                        if ci.get('first_options'):
                            items.append(f'options={"|".join(ci["first_options"][:4])}')
                        if ci.get('format_hint'):
                            items.append(f'format={ci["format_hint"]}')
                        if items:
                            compound_parts.append(f'({",".join(items)})')
                    if compound_parts:
                        compound_attr = f'compound_components={",".join(compound_parts)}'
                        attr_str = f'{attr_str} {compound_attr}' if attr_str else compound_attr

                shadow_pf = _shadow_prefix(node)

                if should_show_scroll and not node.is_interactive:
                    # 可滚动但不可交互
                    line = f'{indent}{shadow_pf}|scroll element|<{tag}'
                elif node.is_interactive:
                    # 可交互（可能同时可滚动）
                    new_pf = '*' if node.is_new else ''
                    scroll_pf = '|scroll element[' if should_show_scroll else '['
                    line = f'{indent}{shadow_pf}{new_pf}{scroll_pf}{node.highlight_index}]<{tag}'
                elif node.original_node.node_name.upper() in ('IFRAME', 'FRAME'):
                    line = f'{indent}{shadow_pf}|{node.original_node.node_name}|<{tag}'
                else:
                    line = f'{indent}{shadow_pf}<{tag}'

                if attr_str:
                    line += f' {attr_str}'
                line += ' />'

                # 滚动信息文本
                if should_show_scroll:
                    scroll_info_text = node.original_node.get_scroll_info_text()
                    if scroll_info_text:
                        line += f' ({scroll_info_text})'

                parts.append(line)

        elif node.original_node.node_type == NodeType.DOCUMENT_FRAGMENT_NODE:
            # Shadow DOM 边界
            sr_type = node.original_node.shadow_root_type
            if sr_type and sr_type.lower() == 'closed':
                parts.append(f'{indent}Closed Shadow')
            else:
                parts.append(f'{indent}Open Shadow')
            next_depth += 1
            for child in node.children:
                text = DOMTreeSerializer.serialize_tree(child, include_attributes, next_depth)
                if text:
                    parts.append(text)
            if node.children:
                parts.append(f'{indent}Shadow End')

        elif node.original_node.node_type == NodeType.TEXT_NODE:
            is_visible = node.original_node.snapshot_node is not None and node.original_node.is_visible
            if (
                is_visible
                and node.original_node.node_value
                and node.original_node.node_value.strip()
                and len(node.original_node.node_value.strip()) > 1
            ):
                parts.append(f'{indent}{node.original_node.node_value.strip()}')

        # 非 DOCUMENT_FRAGMENT_NODE 的子节点
        if node.original_node.node_type != NodeType.DOCUMENT_FRAGMENT_NODE:
            for child in node.children:
                text = DOMTreeSerializer.serialize_tree(child, include_attributes, next_depth)
                if text:
                    parts.append(text)

            # iframe 隐藏内容提示
            if (
                node.original_node.node_type == NodeType.ELEMENT_NODE
                and node.original_node.tag_name
                and node.original_node.node_name.upper() in ('IFRAME', 'FRAME')
            ):
                if node.original_node.hidden_elements_info:
                    hidden = node.original_node.hidden_elements_info
                    parts.append(f'{indent}... ({len(hidden)} more elements below - scroll to reveal):')
                    for elem in hidden:
                        parts.append(f'{indent}    <{elem["tag"]}> "{elem["text"]}" ~{elem["pages"]} pages down')
                elif node.original_node.has_hidden_content:
                    parts.append(f'{indent}... (more content below viewport - scroll to reveal)')

        return '\n'.join(parts)


# ── 模块级辅助函数 ──────────────────────────────────────────────────


def _safe_parse_number(value_str: str, default: float) -> float:
    try:
        return float(value_str)
    except (ValueError, TypeError):
        return default


def _safe_parse_optional_number(value_str: str | None) -> float | None:
    if not value_str:
        return None
    try:
        return float(value_str)
    except (ValueError, TypeError):
        return None


def _shadow_prefix(node: SimplifiedNode) -> str:
    """为 shadow 宿主节点生成前缀标识。"""
    if not node.is_shadow_host:
        return ''
    has_closed = any(
        c.original_node.node_type == NodeType.DOCUMENT_FRAGMENT_NODE
        and c.original_node.shadow_root_type
        and c.original_node.shadow_root_type.lower() == 'closed'
        for c in node.children
    )
    return '|SHADOW(closed)|' if has_closed else '|SHADOW(open)|'


def _build_attributes_string(
    node: EnhancedDOMTreeNode,
    include_attributes: list[str],
    text: str = '',
) -> str:
    """构建元素的属性字符串，用于 LLM 文本输出。

    8 步处理流程：
    1. HTML 属性白名单过滤
    2. input type 特殊处理（日期/时间/tel/text；file 保留 class）
    3. 密码字段保护
    4. AX 属性合并
    5. 表单当前值
    6. 值去重
    7. 冗余移除
    8. 格式化输出
    """
    attrs_to_include: dict[str, str] = {}

    # ── Step 1: HTML 属性白名单过滤 ──
    if node.attributes:
        for key in include_attributes:
            if key in node.attributes:
                val = str(node.attributes[key]).strip()
                if val:
                    attrs_to_include[key] = val

    # ── Step 2: 日期/时间输入格式提示 ──
    if node.tag_name == 'input' and node.attributes:
        input_type = node.attributes.get('type', '').lower()

        # HTML5 原生日期/时间输入：添加 format 和 placeholder
        if input_type in ('date', 'time', 'datetime-local', 'month', 'week'):
            format_map = {
                'date': 'YYYY-MM-DD',
                'time': 'HH:MM',
                'datetime-local': 'YYYY-MM-DDTHH:MM',
                'month': 'YYYY-MM',
                'week': 'YYYY-W##',
            }
            attrs_to_include['format'] = format_map[input_type]
            if 'placeholder' in include_attributes and 'placeholder' not in attrs_to_include:
                placeholder_map = {
                    'date': 'YYYY-MM-DD',
                    'time': 'HH:MM',
                    'datetime-local': 'YYYY-MM-DDTHH:MM',
                    'month': 'YYYY-MM',
                    'week': 'YYYY-W##',
                }
                attrs_to_include['placeholder'] = placeholder_map[input_type]
        # Tel 输入：无 pattern 时添加格式提示
        elif input_type == 'tel' and 'pattern' not in attrs_to_include:
            if 'placeholder' in include_attributes and 'placeholder' not in attrs_to_include:
                attrs_to_include['placeholder'] = '123-456-7890'
        # text 空类型输入：检测 jQuery/AngularJS 日期选择器
        elif input_type in ('text', ''):
            class_attr = node.attributes.get('class', '').lower()
            # AngularJS UI Bootstrap datepicker
            if 'uib-datepicker-popup' in node.attributes:
                date_format = node.attributes.get('uib-datepicker-popup', '')
                if date_format:
                    attrs_to_include['expected_format'] = date_format
                    attrs_to_include['format'] = date_format
            # jQuery/Bootstrap datepickers
            elif any(
                ind in class_attr
                for ind in ('datepicker', 'datetimepicker', 'daterangepicker')
            ):
                date_format = node.attributes.get('data-date-format', '')
                if date_format:
                    attrs_to_include['placeholder'] = date_format
                    attrs_to_include['format'] = date_format
                else:
                    attrs_to_include['placeholder'] = 'mm/dd/yyyy'
                    attrs_to_include['format'] = 'mm/dd/yyyy'
            # data-datepicker 属性检测
            elif 'data-datepicker' in node.attributes:
                date_format = node.attributes.get('data-date-format', '')
                if date_format:
                    attrs_to_include['placeholder'] = date_format
                    attrs_to_include['format'] = date_format
                else:
                    attrs_to_include['placeholder'] = 'mm/dd/yyyy'
                    attrs_to_include['format'] = 'mm/dd/yyyy'
        # file input：保留 class —— 抖音封面有多个 accept 完全相同的 file input，
        # 唯一区分信号是 class（semi-upload-hidden-input=初次上传 / -replace=替换）。
        # class 不在 DEFAULT_INCLUDE_ATTRIBUTES 白名单，需同时加入本次调用的
        # include_attributes 局部副本，否则 Step 6 ordered_keys / Step 8 格式化
        # 循环（都按 include_attributes 驱动）会跳过它（issue #96）。
        # 注意：这是 input-type 外层 if/elif 链的分支（与 date/tel/text 同级，8 空格），
        # 不能放进 text 分支内部（否则 type=file 不进 text 分支、永不触发）。
        elif input_type == 'file':
            cls = node.attributes.get('class', '').strip()
            if cls:
                if 'class' not in include_attributes:
                    include_attributes = [*include_attributes, 'class']
                attrs_to_include['class'] = cls

    # ── Step 3: 密码字段保护 ──
    is_password = (
        node.tag_name == 'input'
        and node.attributes
        and node.attributes.get('type', '').lower() == 'password'
    )
    value_props = {'value', 'valuetext'}

    # ── Step 4: AX 属性合并 ──
    if node.ax_node and node.ax_node.properties:
        for prop in node.ax_node.properties:
            if prop.name in include_attributes and prop.value is not None:
                if is_password and prop.name in value_props:
                    continue
                if isinstance(prop.value, bool):
                    attrs_to_include[prop.name] = str(prop.value).lower()
                else:
                    val = str(prop.value).strip()
                    if val:
                        attrs_to_include[prop.name] = val

    # ── Step 5: 表单当前值（AX 树优先） ──
    if node.tag_name in ('input', 'textarea', 'select'):
        if is_password:
            attrs_to_include.pop('value', None)
        elif node.ax_node and node.ax_node.properties:
            for prop in node.ax_node.properties:
                if prop.name == 'valuetext' and prop.value:
                    attrs_to_include['value'] = str(prop.value).strip()
                    break
                elif prop.name == 'value' and prop.value:
                    attrs_to_include['value'] = str(prop.value).strip()
                    break

    if not attrs_to_include:
        return ''

    # ── Step 6: 值去重 ──
    ordered_keys = [key for key in include_attributes if key in attrs_to_include]
    if len(ordered_keys) > 1:
        keys_to_remove: set[str] = set()
        seen_values: dict[str, str] = {}
        protected_attrs = {'format', 'expected_format', 'placeholder', 'value', 'aria-label', 'title'}

        for key in ordered_keys:
            val = attrs_to_include[key]
            if len(val) > 5:
                if val in seen_values and key not in protected_attrs:
                    keys_to_remove.add(key)
                else:
                    seen_values[val] = key

        for key in keys_to_remove:
            del attrs_to_include[key]

    # ── Step 7: 冗余移除 ──
    # role 与标签名相同
    if node.ax_node and node.ax_node.role:
        if node.node_name == node.ax_node.role:
            attrs_to_include.pop('role', None)

    # type 与标签名相同
    if 'type' in attrs_to_include and attrs_to_include['type'].lower() == node.node_name.lower():
        del attrs_to_include['type']

    # invalid=false 不显示
    if 'invalid' in attrs_to_include and attrs_to_include['invalid'].lower() == 'false':
        del attrs_to_include['invalid']

    # 布尔属性为假值不显示
    if 'required' in attrs_to_include and attrs_to_include['required'].lower() in ('false', '0', 'no'):
        del attrs_to_include['required']

    # aria-expanded 与 expanded 重复（优先保留 AX 树的 expanded）
    if 'expanded' in attrs_to_include and 'aria-expanded' in attrs_to_include:
        del attrs_to_include['aria-expanded']

    # aria-label/placeholder/title 与文本内容相同则移除
    for attr in ('aria-label', 'placeholder', 'title'):
        if attrs_to_include.get(attr, '').strip().lower() == text.strip().lower():
            attrs_to_include.pop(attr, None)

    # ── Step 8: 格式化输出 ──
    formatted: list[str] = []
    for key in include_attributes:
        if key not in attrs_to_include:
            continue
        val = attrs_to_include[key][:100]
        if not val:
            formatted.append(f"{key}=''")
        else:
            formatted.append(f'{key}={val}')

    return ' '.join(formatted)
