"""Browser subsystem — CDP session, DOM, and state types."""

from tree_walker.browser.dom import build_dom_state
from tree_walker.browser.session import BrowserSession
from tree_walker.browser.views import (
	BrowserStateSummary,
	DOMCollectionConfig,
	DOMCollectionMetrics,
	DOMDegradationLevel,
	DOMRect,
	DOMSelectorMap,
	EnhancedAXNode,
	EnhancedDOMTreeNode,
	EnhancedSnapshotNode,
	NodeType,
	SerializedDOMState,
	SimplifiedNode,
	TabInfo,
)

__all__ = [
	"BrowserSession",
	"BrowserStateSummary",
	"DOMCollectionConfig",
	"DOMCollectionMetrics",
	"DOMDegradationLevel",
	"DOMRect",
	"DOMSelectorMap",
	"EnhancedAXNode",
	"EnhancedDOMTreeNode",
	"EnhancedSnapshotNode",
	"NodeType",
	"SerializedDOMState",
	"SimplifiedNode",
	"TabInfo",
	"build_dom_state",
]
