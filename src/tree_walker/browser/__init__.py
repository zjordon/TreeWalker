"""Browser subsystem — CDP session + aggregate state types.

DOM 快照能力（``build_dom_state`` / ``SerializedDOMState`` / ``EnhancedDOMTreeNode`` / ...）
已由公共库 dom-snapshot 提供（ROADMAP P1）。此处 re-export 仅为兼容既有
``from tree_walker.browser import ...`` 的引用；新代码请直接 ``from dom_snapshot import ...``。
"""

from dom_snapshot import build_dom_state  # noqa: F401
from dom_snapshot import (  # noqa: F401
	DEFAULT_INCLUDE_ATTRIBUTES,
	DOMCollectionConfig,
	DOMCollectionMetrics,
	DOMDegradationLevel,
	DOMRect,
	DOMSelectorMap,
	EnhancedAXNode,
	EnhancedDOMTreeNode,
	EnhancedSnapshotNode,
	FileInputInfo,
	NodeType,
	SerializedDOMState,
	SimplifiedNode,
	filter_dynamic_classes,
)
from tree_walker.browser.session import BrowserSession
from tree_walker.browser.views import (
	BrowserEvent,
	BrowserStateSummary,
	DOMInteractedElement,
	MatchLevel,
	TabInfo,
)

__all__ = [
	"BrowserSession",
	"BrowserEvent",
	"BrowserStateSummary",
	"DOMCollectionConfig",
	"DOMCollectionMetrics",
	"DOMDegradationLevel",
	"DOMInteractedElement",
	"DOMRect",
	"DOMSelectorMap",
	"DEFAULT_INCLUDE_ATTRIBUTES",
	"EnhancedAXNode",
	"EnhancedDOMTreeNode",
	"EnhancedSnapshotNode",
	"FileInputInfo",
	"MatchLevel",
	"NodeType",
	"SerializedDOMState",
	"SimplifiedNode",
	"TabInfo",
	"build_dom_state",
	"filter_dynamic_classes",
]
