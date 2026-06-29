"""Enhanced DOM data models (browser-use style).

Model hierarchy:
  DOMRect → geometry
  EnhancedAXProperty / EnhancedAXNode → AX tree data
  EnhancedSnapshotNode → Snapshot layout data
  EnhancedDOMTreeNode → core merged node (DOM + AX + Snapshot)
  SimplifiedNode → filtered serialization node
  SerializedDOMState → final output
"""

from __future__ import annotations

import hashlib
import uuid as _uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Constants ──────────────────────────────────────────────────────────

DEFAULT_INCLUDE_ATTRIBUTES = [
	'title',
	'type',
	'checked',
	'id',
	'name',
	'role',
	'value',
	'placeholder',
	'data-date-format',
	'alt',
	'aria-label',
	'aria-expanded',
	'data-state',
	'aria-checked',
	'aria-valuemin',
	'aria-valuemax',
	'aria-valuenow',
	'aria-placeholder',
	'pattern',
	'min',
	'max',
	'minlength',
	'maxlength',
	'step',
	'accept',
	'multiple',
	'inputmode',
	'autocomplete',
	'aria-autocomplete',
	'list',
	'data-mask',
	'data-inputmask',
	'data-datepicker',
	'format',
	'expected_format',
	'contenteditable',
	'pseudo',
	'selected',
	'expanded',
	'pressed',
	'disabled',
	'invalid',
	'valuemin',
	'valuemax',
	'valuenow',
	'keyshortcuts',
	'haspopup',
	'multiselectable',
	'required',
	'valuetext',
	'level',
	'busy',
	'live',
	'ax_name',
]

STATIC_ATTRIBUTES = {
	'class',
	'id',
	'name',
	'type',
	'placeholder',
	'aria-label',
	'title',
	'role',
	'data-testid',
	'data-test',
	'data-cy',
	'data-selenium',
	'for',
	'required',
	'disabled',
	'readonly',
	'checked',
	'selected',
	'multiple',
	'accept',
	'href',
	'target',
	'rel',
	'aria-describedby',
	'aria-labelledby',
	'aria-controls',
	'aria-owns',
	'aria-live',
	'aria-atomic',
	'aria-busy',
	'aria-hidden',
	'aria-pressed',
	'aria-autocomplete',
	'aria-checked',
	'aria-selected',
	'list',
	'tabindex',
	'alt',
	'src',
	'lang',
	'itemscope',
	'itemtype',
	'itemprop',
	'pseudo',
	'aria-valuemin',
	'aria-valuemax',
	'aria-valuenow',
	'aria-placeholder',
}

DYNAMIC_CLASS_PATTERNS = frozenset(
	{
		'focus',
		'hover',
		'active',
		'selected',
		'disabled',
		'animation',
		'transition',
		'loading',
		'open',
		'closed',
		'expanded',
		'collapsed',
		'visible',
		'hidden',
		'pressed',
		'checked',
		'highlighted',
		'current',
		'entering',
		'leaving',
	}
)


# ── Enums ──────────────────────────────────────────────────────────────


class NodeType(int, Enum):
	"""DOM node types based on the DOM specification."""

	ELEMENT_NODE = 1
	ATTRIBUTE_NODE = 2
	TEXT_NODE = 3
	CDATA_SECTION_NODE = 4
	ENTITY_REFERENCE_NODE = 5
	ENTITY_NODE = 6
	PROCESSING_INSTRUCTION_NODE = 7
	COMMENT_NODE = 8
	DOCUMENT_NODE = 9
	DOCUMENT_TYPE_NODE = 10
	DOCUMENT_FRAGMENT_NODE = 11
	NOTATION_NODE = 12


class MatchLevel(Enum):
	"""Element matching strictness levels for history replay."""

	EXACT = 1
	STABLE = 2
	XPATH = 3
	AX_NAME = 4
	ATTRIBUTE = 5


def filter_dynamic_classes(class_str: str | None) -> str:
	"""Remove dynamic state classes, keep semantic/identifying ones."""
	if not class_str:
		return ''
	classes = class_str.split()
	stable = [c for c in classes if not any(pattern in c.lower() for pattern in DYNAMIC_CLASS_PATTERNS)]
	return ' '.join(sorted(stable))


# ── Geometry ───────────────────────────────────────────────────────────


@dataclass(slots=True)
class DOMRect:
	x: float
	y: float
	width: float
	height: float

	def to_dict(self) -> dict[str, Any]:
		return {'x': self.x, 'y': self.y, 'width': self.width, 'height': self.height}

	def __json__(self) -> dict:
		return self.to_dict()


# ── AX tree models ─────────────────────────────────────────────────────


@dataclass(slots=True)
class EnhancedAXProperty:
	"""Single AX property with name and value."""

	name: str
	value: str | bool | None


@dataclass(slots=True)
class EnhancedAXNode:
	"""Enhanced accessibility tree node."""

	ax_node_id: str
	ignored: bool
	role: str | None
	name: str | None
	description: str | None
	properties: list[EnhancedAXProperty] | None
	child_ids: list[str] | None


# ── Snapshot models ────────────────────────────────────────────────────


@dataclass(slots=True)
class EnhancedSnapshotNode:
	"""Snapshot data from DOMSnapshot.captureSnapshot."""

	is_clickable: bool | None
	cursor_style: str | None
	bounds: DOMRect | None
	clientRects: DOMRect | None
	scrollRects: DOMRect | None
	computed_styles: dict[str, str] | None
	paint_order: int | None
	stacking_contexts: int | None


# ── Core DOM node ──────────────────────────────────────────────────────


@dataclass
class EnhancedDOMTreeNode:
	"""Enhanced DOM tree node combining data from DOM, AX, and Snapshot trees."""

	# DOM node data
	node_id: int
	backend_node_id: int
	node_type: NodeType
	node_name: str
	node_value: str
	attributes: dict[str, str]
	is_scrollable: bool | None = None
	is_visible: bool | None = None
	absolute_position: DOMRect | None = None

	# Frame management
	target_id: str = ''
	frame_id: str | None = None
	session_id: str | None = None
	content_document: EnhancedDOMTreeNode | None = None

	# Shadow DOM
	shadow_root_type: str | None = None
	shadow_roots: list[EnhancedDOMTreeNode] | None = None

	# Tree navigation
	parent_node: EnhancedDOMTreeNode | None = None
	children_nodes: list[EnhancedDOMTreeNode] | None = None

	# AX data
	ax_node: EnhancedAXNode | None = None

	# Snapshot data
	snapshot_node: EnhancedSnapshotNode | None = None

	# Additional fields
	has_js_click_listener: bool = False
	_compound_children: list[dict[str, Any]] = field(default_factory=list)
	hidden_elements_info: list[dict[str, Any]] = field(default_factory=list)
	has_hidden_content: bool = False
	uuid: str = field(default_factory=lambda: _uuid.uuid4().hex)

	# ── Convenience properties ────────────────────────────────────────

	@property
	def parent(self) -> EnhancedDOMTreeNode | None:
		return self.parent_node

	@property
	def children(self) -> list[EnhancedDOMTreeNode]:
		return self.children_nodes or []

	@property
	def children_and_shadow_roots(self) -> list[EnhancedDOMTreeNode]:
		children = list(self.children_nodes) if self.children_nodes else []
		if self.shadow_roots:
			children.extend(self.shadow_roots)
		return children

	@property
	def tag_name(self) -> str:
		return self.node_name.lower()

	@property
	def x(self) -> int:
		"""Center X coordinate for click operations."""
		if self.snapshot_node:
			r = self.snapshot_node.bounds or self.snapshot_node.clientRects
			if r:
				return int(r.x + r.width / 2)
		return 0

	@property
	def y(self) -> int:
		"""Center Y coordinate for click operations."""
		if self.snapshot_node:
			r = self.snapshot_node.bounds or self.snapshot_node.clientRects
			if r:
				return int(r.y + r.height / 2)
		return 0

	@property
	def width(self) -> int:
		if self.snapshot_node and self.snapshot_node.bounds:
			return int(self.snapshot_node.bounds.width)
		return 0

	@property
	def height(self) -> int:
		if self.snapshot_node and self.snapshot_node.bounds:
			return int(self.snapshot_node.bounds.height)
		return 0

	# ── XPath ─────────────────────────────────────────────────────────

	@property
	def xpath(self) -> str:
		"""Generate XPath for this DOM node, stopping at shadow boundaries or iframes."""
		segments = []
		current = self
		while current and (
			current.node_type == NodeType.ELEMENT_NODE
			or current.node_type == NodeType.DOCUMENT_FRAGMENT_NODE
		):
			if current.node_type == NodeType.DOCUMENT_FRAGMENT_NODE:
				current = current.parent_node
				continue
			if current.parent_node and current.parent_node.node_name.lower() == 'iframe':
				break
			pos = self._get_element_position(current)
			tag = current.node_name.lower()
			idx = f'[{pos}]' if pos > 0 else ''
			segments.insert(0, f'{tag}{idx}')
			current = current.parent_node
		return '/'.join(segments)

	def _get_element_position(self, element: EnhancedDOMTreeNode) -> int:
		if not element.parent_node or not element.parent_node.children_nodes:
			return 0
		same_tag = [
			c
			for c in element.parent_node.children_nodes
			if c.node_type == NodeType.ELEMENT_NODE and c.node_name.lower() == element.node_name.lower()
		]
		if len(same_tag) <= 1:
			return 0
		try:
			return same_tag.index(element) + 1
		except ValueError:
			return 0

	# ── Text collection ───────────────────────────────────────────────

	def get_all_children_text(self, max_depth: int = -1) -> str:
		text_parts = []

		def collect(node: EnhancedDOMTreeNode, depth: int) -> None:
			if max_depth != -1 and depth > max_depth:
				return
			if node.node_type == NodeType.TEXT_NODE:
				text_parts.append(node.node_value)
			elif node.node_type == NodeType.ELEMENT_NODE:
				for child in node.children:
					collect(child, depth + 1)

		collect(self, 0)
		return '\n'.join(text_parts).strip()

	def llm_representation(self, max_text_length: int = 100) -> str:
		text = self.get_all_children_text()
		if text and len(text) > max_text_length:
			text = text[:max_text_length]
		return f'<{self.tag_name}>{text or ""}'

	def get_meaningful_text_for_llm(self) -> str:
		if self.attributes:
			for attr in ['value', 'aria-label', 'title', 'placeholder', 'alt']:
				if attr in self.attributes and self.attributes[attr]:
					return self.attributes[attr]
		return self.get_all_children_text().strip()

	# ── Scroll detection ──────────────────────────────────────────────

	@property
	def is_actually_scrollable(self) -> bool:
		if self.is_scrollable:
			return True
		if not self.snapshot_node:
			return False
		scroll = self.snapshot_node.scrollRects
		client = self.snapshot_node.clientRects
		if scroll and client:
			v = scroll.height > client.height + 1
			h = scroll.width > client.width + 1
			if v or h:
				if self.snapshot_node.computed_styles:
					styles = self.snapshot_node.computed_styles
					overflow = styles.get('overflow', 'visible').lower()
					ox = styles.get('overflow-x', overflow).lower()
					oy = styles.get('overflow-y', overflow).lower()
					return (
						overflow in ['auto', 'scroll', 'overlay']
						or ox in ['auto', 'scroll', 'overlay']
						or oy in ['auto', 'scroll', 'overlay']
					)
				return self.tag_name in {'div', 'main', 'section', 'article', 'aside', 'body', 'html'}
		return False

	@property
	def scroll_info(self) -> dict[str, Any] | None:
		if not self.is_actually_scrollable or not self.snapshot_node:
			return None
		scroll = self.snapshot_node.scrollRects
		client = self.snapshot_node.clientRects
		if not scroll or not client:
			return None

		scroll_top = scroll.y
		scroll_left = scroll.x
		scrollable_height = scroll.height
		scrollable_width = scroll.width
		visible_height = client.height
		visible_width = client.width

		content_above = max(0, scroll_top)
		content_below = max(0, scrollable_height - visible_height - scroll_top)
		content_left = max(0, scroll_left)
		content_right = max(0, scrollable_width - visible_width - scroll_left)

		v_pct = 0.0
		h_pct = 0.0
		if scrollable_height > visible_height:
			max_top = scrollable_height - visible_height
			v_pct = (scroll_top / max_top) * 100 if max_top > 0 else 0
		if scrollable_width > visible_width:
			max_left = scrollable_width - visible_width
			h_pct = (scroll_left / max_left) * 100 if max_left > 0 else 0

		pages_above = content_above / visible_height if visible_height > 0 else 0
		pages_below = content_below / visible_height if visible_height > 0 else 0
		total_pages = scrollable_height / visible_height if visible_height > 0 else 1

		return {
			'scroll_top': scroll_top,
			'scroll_left': scroll_left,
			'scrollable_height': scrollable_height,
			'scrollable_width': scrollable_width,
			'visible_height': visible_height,
			'visible_width': visible_width,
			'content_above': content_above,
			'content_below': content_below,
			'content_left': content_left,
			'content_right': content_right,
			'vertical_scroll_percentage': round(v_pct, 1),
			'horizontal_scroll_percentage': round(h_pct, 1),
			'pages_above': round(pages_above, 1),
			'pages_below': round(pages_below, 1),
			'total_pages': round(total_pages, 1),
			'can_scroll_up': content_above > 0,
			'can_scroll_down': content_below > 0,
			'can_scroll_left': content_left > 0,
			'can_scroll_right': content_right > 0,
		}

	# ── Scroll info for LLM ───────────────────────────────────────────

	@property
	def should_show_scroll_info(self) -> bool:
		"""Whether scroll info should be displayed for this element."""
		if not self.is_actually_scrollable:
			return False
		info = self.scroll_info
		if not info:
			return False
		return (
			info.get('can_scroll_up', False)
			or info.get('can_scroll_down', False)
			or info.get('can_scroll_left', False)
			or info.get('can_scroll_right', False)
		)

	def get_scroll_info_text(self) -> str | None:
		"""Formatted scroll info string for LLM consumption."""
		info = self.scroll_info
		if not info:
			return None
		parts: list[str] = []
		v_pct = info.get('vertical_scroll_percentage', 0)
		pages_below = info.get('pages_below', 0)
		pages_above = info.get('pages_above', 0)
		total_pages = info.get('total_pages', 1)

		if info.get('can_scroll_up') or info.get('can_scroll_down'):
			parts.append(f'scroll: {v_pct:.0f}%')
			if pages_below > 0:
				parts.append(f'{pages_below:.1f} pages below')
			if pages_above > 0:
				parts.append(f'{pages_above:.1f} pages above')
			parts.append(f'total: {total_pages:.1f} pages')

		return ', '.join(parts) if parts else None

	# ── Hash ──────────────────────────────────────────────────────────

	@property
	def element_hash(self) -> int:
		return hash(self)

	def compute_stable_hash(self) -> int:
		path = self._get_parent_branch_path()
		path_str = '/'.join(path)
		filtered: dict[str, str] = {}
		for k, v in self.attributes.items():
			if k not in STATIC_ATTRIBUTES:
				continue
			if k == 'class':
				v = filter_dynamic_classes(v)
				if not v:
					continue
			filtered[k] = v
		attrs_str = ''.join(f'{k}={v}' for k, v in sorted(filtered.items()))
		ax_name = ''
		if self.ax_node and self.ax_node.name:
			ax_name = f'|ax_name={self.ax_node.name}'
		combined = f'{path_str}|{attrs_str}{ax_name}'
		return int(hashlib.sha256(combined.encode()).hexdigest()[:16], 16)

	def __hash__(self) -> int:
		path = self._get_parent_branch_path()
		path_str = '/'.join(path)
		attrs_str = ''.join(
			f'{k}={v}' for k, v in sorted((k, v) for k, v in self.attributes.items() if k in STATIC_ATTRIBUTES)
		)
		ax_name = ''
		if self.ax_node and self.ax_node.name:
			ax_name = f'|ax_name={self.ax_node.name}'
		combined = f'{path_str}|{attrs_str}{ax_name}'
		return int(hashlib.sha256(combined.encode()).hexdigest()[:16], 16)

	def _get_parent_branch_path(self) -> list[str]:
		parents: list[EnhancedDOMTreeNode] = []
		current: EnhancedDOMTreeNode | None = self
		while current is not None:
			if current.node_type == NodeType.ELEMENT_NODE:
				parents.append(current)
			current = current.parent_node
		parents.reverse()
		return [p.tag_name for p in parents]

	# ── Serialization ─────────────────────────────────────────────────

	def __json__(self) -> dict:
		return {
			'node_id': self.node_id,
			'backend_node_id': self.backend_node_id,
			'node_type': self.node_type.name,
			'node_name': self.node_name,
			'node_value': self.node_value,
			'is_visible': self.is_visible,
			'attributes': self.attributes,
			'is_scrollable': self.is_scrollable,
			'session_id': self.session_id,
			'target_id': self.target_id,
			'frame_id': self.frame_id,
			'content_document': self.content_document.__json__() if self.content_document else None,
			'shadow_root_type': self.shadow_root_type,
			'ax_node': asdict(self.ax_node) if self.ax_node else None,
			'snapshot_node': asdict(self.snapshot_node) if self.snapshot_node else None,
			'shadow_roots': [r.__json__() for r in self.shadow_roots] if self.shadow_roots else [],
			'children_nodes': [c.__json__() for c in self.children_nodes] if self.children_nodes else [],
		}

	def __repr__(self) -> str:
		attrs = ', '.join(f'{k}={v}' for k, v in self.attributes.items())
		return f'<{self.tag_name} {attrs} num_children={len(self.children_nodes or [])}>'

	def __str__(self) -> str:
		fid = self.frame_id[-4:] if self.frame_id else '?'
		return f'[<{self.tag_name}>#{fid}:{self.backend_node_id}]'


# ── Type aliases ────────────────────────────────────────────────────────

DOMSelectorMap = dict[int, EnhancedDOMTreeNode]


# ── Serialization tree ─────────────────────────────────────────────────


@dataclass(slots=True)
class SimplifiedNode:
	"""Simplified tree node for optimization."""

	original_node: EnhancedDOMTreeNode
	children: list[SimplifiedNode]
	should_display: bool = True
	is_interactive: bool = False
	is_new: bool = False
	ignored_by_paint_order: bool = False
	excluded_by_parent: bool = False
	is_shadow_host: bool = False
	is_compound_component: bool = False
	highlight_index: int | None = None

	def _clean_original_node_json(self, node_json: dict) -> dict:
		node_json.pop('children_nodes', None)
		node_json.pop('shadow_roots', None)
		if node_json.get('content_document'):
			node_json['content_document'] = self._clean_original_node_json(node_json['content_document'])
		return node_json

	def __json__(self) -> dict:
		original_json = self.original_node.__json__()
		cleaned = self._clean_original_node_json(original_json)
		return {
			'should_display': self.should_display,
			'is_interactive': self.is_interactive,
			'ignored_by_paint_order': self.ignored_by_paint_order,
			'excluded_by_parent': self.excluded_by_parent,
			'highlight_index': self.highlight_index,
			'original_node': cleaned,
			'children': [c.__json__() for c in self.children],
		}


@dataclass
class PropagatingBounds:
	"""Track bounds that propagate from parent elements to filter children."""

	tag: str
	bounds: DOMRect
	node_id: int
	depth: int


# ── Final output ────────────────────────────────────────────────────────


@dataclass
class FileInputInfo:
	"""单个 <input type=file> 的元数据：帮 LLM 在多 input 页面锁定正确的上传入口。

	抖音封面编辑器有多个 file input，多数是隐藏「诱饵」（无 handler）。
	accept / visible / upload_ancestor 让 LLM 优先选可见且在 upload 容器内的 live input。
	class_name 让 LLM 区分同 accept 的多个 input（如 semi-upload-hidden-input 初次上传
	vs -replace 替换，issue #96）。
	"""

	backend_node_id: int
	accept: str = ""
	visible: bool = True
	upload_ancestor: bool = False
	class_name: str = ""


@dataclass
class SerializedDOMState:
	"""Final serialized DOM state for LLM consumption."""

	_root: SimplifiedNode | None
	selector_map: DOMSelectorMap
	element_tree_text: str
	file_input_backend_ids: list[int] = field(default_factory=list)
	file_inputs_meta: list[FileInputInfo] = field(default_factory=list)

	def llm_representation(self, include_attributes: list[str] | None = None) -> str:
		if not self._root:
			return 'Empty DOM tree (you might have to wait for the page to load)'
		return self.element_tree_text


@dataclass
class DOMInteractedElement:
	"""Snapshot of a DOM element at interaction time."""

	node_id: int
	backend_node_id: int
	frame_id: str | None
	node_type: NodeType
	node_value: str
	node_name: str
	attributes: dict[str, str] | None
	bounds: DOMRect | None
	x_path: str
	element_hash: int
	stable_hash: int | None = None
	ax_name: str | None = None

	def to_dict(self) -> dict[str, Any]:
		return {
			'node_id': self.node_id,
			'backend_node_id': self.backend_node_id,
			'frame_id': self.frame_id,
			'node_type': self.node_type.value,
			'node_value': self.node_value,
			'node_name': self.node_name,
			'attributes': self.attributes,
			'x_path': self.x_path,
			'element_hash': self.element_hash,
			'stable_hash': self.stable_hash,
			'bounds': self.bounds.to_dict() if self.bounds else None,
			'ax_name': self.ax_name,
		}

	@classmethod
	def load_from_enhanced_dom_tree(cls, node: EnhancedDOMTreeNode) -> DOMInteractedElement:
		ax_name = node.ax_node.name if node.ax_node and node.ax_node.name else None
		return cls(
			node_id=node.node_id,
			backend_node_id=node.backend_node_id,
			frame_id=node.frame_id,
			node_type=node.node_type,
			node_value=node.node_value,
			node_name=node.node_name,
			attributes=node.attributes,
			bounds=node.snapshot_node.bounds if node.snapshot_node else None,
			x_path=node.xpath,
			element_hash=hash(node),
			stable_hash=node.compute_stable_hash(),
			ax_name=ax_name,
		)


# ── Browser-level models (Pydantic) ────────────────────────────────────


class TabInfo(BaseModel):
	target_id: str
	url: str
	title: str


class BrowserStateSummary(BaseModel):
	url: str = ""
	title: str = ""
	tabs: list[TabInfo] = Field(default_factory=list)
	dom_state: SerializedDOMState | None = None
	screenshot: bytes | None = None

	class Config:
		arbitrary_types_allowed = True


# ── DOM collection robustness types ────────────────────────────────────

class DOMDegradationLevel(Enum):
	"""Degradation level after CDP batch collection."""
	FULL = "full"       # all sources available
	PARTIAL = "partial" # AX tree missing; snapshot + DOM tree available
	MINIMAL = "minimal" # snapshot missing; DOM tree only (no layout/visibility/bounds)
	FAILED = "failed"   # DOM tree itself failed; return EMPTY_DOM_STATE


@dataclass
class DOMCollectionConfig:
	"""Configurable parameters for DOM collection robustness."""
	cdp_first_timeout: float = 10.0
	cdp_retry_timeout: float = 2.0
	max_iframes: int = 100
	heavy_page_element_threshold: int = 10000


@dataclass
class DOMCollectionMetrics:
	"""Metrics from a single DOM collection pass."""
	degradation_level: DOMDegradationLevel = DOMDegradationLevel.FULL
	source_statuses: dict[str, str] = field(default_factory=dict)
	total_ms: float = 0.0
	iframe_count: int = 0
	element_count: int = 0
