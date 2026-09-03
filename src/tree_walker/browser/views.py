"""TreeWalker browser-level aggregate state types.

DOM 核心 dataclass（EnhancedDOMTreeNode / SerializedDOMState / NodeType / ...）已抽取到
公共库 dom-snapshot（ROADMAP P1）。本模块只保留 TreeWalker 自有的类型：

- 聚合状态模型（TabInfo / BrowserEvent / BrowserStateSummary，Pydantic）
- 重放侧类型（MatchLevel / DOMInteractedElement）

为兼容既有 ``from tree_walker.browser.views import <DOM 类型>`` 的引用，DOM 公共类型
在此 re-export 自 dom_snapshot（迁移期 shim；新代码应直接 ``from dom_snapshot import ...``）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

# DOM 公共类型 re-export 自 dom-snapshot（迁移期 shim，保持既有 import 路径可用）
from dom_snapshot import (  # noqa: F401
	DEFAULT_INCLUDE_ATTRIBUTES,
	DYNAMIC_CLASS_PATTERNS,
	STATIC_ATTRIBUTES,
	DOMCollectionConfig,
	DOMCollectionMetrics,
	DOMDegradationLevel,
	DOMRect,
	DOMSelectorMap,
	EnhancedAXNode,
	EnhancedAXProperty,
	EnhancedDOMTreeNode,
	EnhancedSnapshotNode,
	FileInputInfo,
	NodeType,
	PropagatingBounds,
	SerializedDOMState,
	SimplifiedNode,
	filter_dynamic_classes,
)


class MatchLevel(Enum):
	"""Element matching strictness levels for history replay."""

	EXACT = 1
	STABLE = 2
	XPATH = 3
	AX_NAME = 4
	ATTRIBUTE = 5


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


class BrowserEvent(BaseModel):
	"""P1b：最近浏览器事件。

	首期仅采集 ``dialog``（alert/confirm/prompt/beforeunload，由
	``Page.javascriptDialogOpening`` 触发）。``download`` 由
	``consume_completed_downloads`` → ``[Downloads]`` 段覆盖——cdp_use 单回调机制
	（``registry._handlers[method] = callback`` 覆盖式）下不能与 download tracking
	双注册 ``Browser.downloadWillBegin``，故不入此列表。type 字段保留完整枚举供未来扩展。
	"""

	type: Literal["navigation", "dialog", "download", "network_error", "console_error"]
	message: str
	timestamp: float


class BrowserStateSummary(BaseModel):
	url: str = ""
	title: str = ""
	tabs: list[TabInfo] = Field(default_factory=list)
	dom_state: SerializedDOMState | None = None
	screenshot: bytes | None = None
	# P7 tool_layer B2：UI 网格元信息（namespace/total_records/sorting/active_filters）。
	# 非网格页为 None；由 session.get_state → _read_grid_meta 产出。
	grid_meta: dict[str, Any] | None = None
	recent_events: list[BrowserEvent] = Field(default_factory=list)  # P1b：每步 consume

	class Config:
		arbitrary_types_allowed = True
