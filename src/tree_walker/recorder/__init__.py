"""用户操作录制 → 历史重放。

Chrome 扩展采集用户真实操作（事件 + 元素 xpath 线索），本后端通过 remote-debugging-port
直连浏览器 CDP，复用 ``_build_enhanced_dom_tree`` + ``compute_stable_hash`` 算指纹，拼成
``AgentHistory`` 落盘到 ``rerun-history/``，供现有 ``load_and_rerun`` 重放。

详见 ``docs/user_recording/README.md``。
"""

from tree_walker.recorder.event_mapper import coalesce_inputs, map_event, needs_target
from tree_walker.recorder.locator import locate_by_ref, locate_by_xpath, normalize_xpath

__all__ = [
	"coalesce_inputs",
	"locate_by_ref",
	"locate_by_xpath",
	"map_event",
	"needs_target",
	"normalize_xpath",
]
