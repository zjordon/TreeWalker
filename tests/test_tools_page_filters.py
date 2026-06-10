"""Tests for Tools.apply_page_filters."""

from tree_walker.tools.actions import Tools


class TestApplyPageFilters:
    def test_apply_sets_page_patterns(self):
        """apply_page_filters 为指定动作设置 page_patterns。"""
        tools = Tools()
        assert tools.registry.actions["click"].page_patterns is None
        tools.apply_page_filters({"click": ["*/special*"]})
        assert tools.registry.actions["click"].page_patterns == ["*/special*"]

    def test_apply_unknown_action_ignored(self):
        """未知动作名被忽略，不报错。"""
        tools = Tools()
        tools.apply_page_filters({"nonexistent": ["*"]})
        assert "nonexistent" not in tools.registry.actions

    def test_default_no_filtering(self):
        """默认不设置任何过滤，所有动作无 page_patterns。"""
        tools = Tools()
        for action in tools.registry.actions.values():
            assert action.page_patterns is None
