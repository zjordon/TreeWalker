"""Tests for P1a page_stats —— DOM 统计 + state 消息 [Page Stats] 渲染。

对齐方案：``docs/agent-loop-optimize/01-准备上下文对齐browser-use方案.md`` §5.1。

注意：``metrics.element_count`` 恒为 0、``metrics.iframe_count`` 仅在超限截断时赋值，
都不可用——故统计放在 ``DOMTreeSerializer._collect_page_stats``（持有 filtered_tree +
selector_map，唯一可靠位置）。
"""

from tree_walker.browser.serializer import DOMTreeSerializer
from tree_walker.browser.views import BrowserStateSummary, SerializedDOMState
from tree_walker.prompts.system_prompt import build_state_message

from tests.conftest import _make_node, _make_simplified_node


def _state(url: str = "https://example.com", title: str = "Ex", dom_text: str = "dom") -> BrowserStateSummary:
	return BrowserStateSummary(
		url=url,
		title=title,
		dom_state=SerializedDOMState(_root=None, selector_map={}, element_tree_text=dom_text),
	)


def _serializer(selector_map=None) -> DOMTreeSerializer:
	"""只测 _collect_page_stats：root_node 给占位，selector_map 手动注入。"""
	s = DOMTreeSerializer(root_node=_make_node())
	s._selector_map = selector_map or {}
	return s


class TestCollectPageStats:
	def test_counts_links_and_interactive_from_selector_map(self):
		a1 = _make_node(tag='a', backend_node_id=10)
		a2 = _make_node(tag='a', backend_node_id=11)
		btn = _make_node(tag='button', backend_node_id=12)
		s = _serializer(selector_map={10: a1, 11: a2, 12: btn})
		stats = s._collect_page_stats(root=None)
		assert stats['interactive'] == 3
		assert stats['links'] == 2
		assert stats['iframes'] == 0
		assert stats['skeleton'] is False

	def test_counts_iframes_in_tree(self):
		iframe_node = _make_node(tag='iframe', backend_node_id=20)
		child = _make_simplified_node(original_node=_make_node(tag='div'))
		root = _make_simplified_node(original_node=iframe_node, children=[child])
		s = _serializer(selector_map={})
		stats = s._collect_page_stats(root)
		assert stats['iframes'] == 1

	def test_nested_iframes_counted(self):
		inner_iframe = _make_node(tag='iframe', backend_node_id=22)
		outer_iframe = _make_node(tag='iframe', backend_node_id=21)
		root = _make_simplified_node(
			original_node=outer_iframe,
			children=[_make_simplified_node(original_node=inner_iframe)],
		)
		s = _serializer(selector_map={})
		stats = s._collect_page_stats(root)
		assert stats['iframes'] == 2

	def test_skeleton_true_when_skeleton_class_and_low_interactive(self):
		skel = _make_node(tag='div', attributes={'class': 'skeleton-loader'}, backend_node_id=30)
		root = _make_simplified_node(original_node=skel)
		s = _serializer(selector_map={})  # 0 interactive < 3
		stats = s._collect_page_stats(root)
		assert stats['skeleton'] is True

	def test_skeleton_false_when_many_interactive(self):
		skel = _make_node(tag='div', attributes={'class': 'loading-spinner'}, backend_node_id=30)
		root = _make_simplified_node(original_node=skel)
		smap = {1: _make_node(tag='a'), 2: _make_node(tag='a'), 3: _make_node(tag='a')}
		s = _serializer(selector_map=smap)  # 3 interactive，不满足 < 3
		stats = s._collect_page_stats(root)
		assert stats['skeleton'] is False

	def test_skeleton_false_when_no_skeleton_class(self):
		root = _make_simplified_node(
			original_node=_make_node(tag='div', attributes={'class': 'navbar'})
		)
		s = _serializer(selector_map={})
		stats = s._collect_page_stats(root)
		assert stats['skeleton'] is False

	def test_none_root_gives_zeros(self):
		s = _serializer(selector_map={})
		stats = s._collect_page_stats(root=None)
		assert stats == {'links': 0, 'interactive': 0, 'iframes': 0, 'skeleton': False}

	def test_placeholder_skeleton_class_patterns(self):
		# placeholder / spinner 也算骨架屏类
		for cls in ('placeholder-box', 'my-spinner', 'is-loading'):
			root = _make_simplified_node(
				original_node=_make_node(tag='div', attributes={'class': cls})
			)
			s = _serializer(selector_map={})
			assert s._collect_page_stats(root)['skeleton'] is True, cls


class TestSerializedDOMStateDefault:
	def test_default_page_stats_empty(self):
		st = SerializedDOMState(_root=None, selector_map={}, element_tree_text="")
		assert st.page_stats == {}


class TestPageStatsRendering:
	def test_renders_counts(self):
		msg = build_state_message(
			_state(),
			task="t",
			page_stats={'links': 5, 'interactive': 10, 'iframes': 1, 'skeleton': False},
		)
		assert "[Page Stats] links=5, interactive=10, iframes=1" in msg
		assert "SKELETON" not in msg

	def test_renders_skeleton_note(self):
		msg = build_state_message(
			_state(),
			task="t",
			page_stats={'links': 0, 'interactive': 1, 'iframes': 0, 'skeleton': True},
		)
		assert "SKELETON/LOADING (page may not be fully rendered)" in msg

	def test_no_section_when_none(self):
		msg = build_state_message(_state(), task="t", page_stats=None)
		assert "[Page Stats]" not in msg

	def test_no_section_when_empty(self):
		# 空页面 / EMPTY_DOM_STATE / enable_page_stats=False → page_stats={}
		msg = build_state_message(_state(), task="t", page_stats={})
		assert "[Page Stats]" not in msg

	def test_page_stats_after_page_title(self):
		msg = build_state_message(
			_state(),
			task="t",
			page_stats={'links': 1, 'interactive': 2, 'iframes': 0, 'skeleton': False},
		)
		assert msg.index("[Page Title]") < msg.index("[Page Stats]")
