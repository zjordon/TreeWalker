"""Tests for Schema switch force-done — P2."""

from tree_walker.tools.registry import ActionRegistry
from tree_walker.tools.actions import Tools


def _make_registry_with_all_actions() -> ActionRegistry:
    tools = Tools()
    return tools.registry


class TestIncludeActionsFilter:
    def test_include_actions_done_only(self):
        """get_tool_schema(include_actions=["done"]) only contains done."""
        registry = _make_registry_with_all_actions()
        schema = registry.get_tool_schema(include_actions=["done"])
        action_enum = (
            schema["input_schema"]["properties"]["action"]
            ["properties"]["name"]["enum"]
        )
        assert action_enum == ["done"]

    def test_include_actions_none_returns_all(self):
        """include_actions=None returns all actions."""
        registry = _make_registry_with_all_actions()
        schema = registry.get_tool_schema(include_actions=None)
        action_enum = (
            schema["input_schema"]["properties"]["action"]
            ["properties"]["name"]["enum"]
        )
        assert "done" in action_enum
        assert "click" in action_enum
        assert "navigate" in action_enum
        assert len(action_enum) > 5

    def test_include_actions_with_subset(self):
        """include_actions can filter to an arbitrary subset."""
        registry = _make_registry_with_all_actions()
        schema = registry.get_tool_schema(include_actions=["done", "click"])
        action_enum = (
            schema["input_schema"]["properties"]["action"]
            ["properties"]["name"]["enum"]
        )
        assert action_enum == ["click", "done"]

    def test_include_actions_with_flash_mode(self):
        """include_actions works with flash output_mode."""
        registry = _make_registry_with_all_actions()
        schema = registry.get_tool_schema(
            include_actions=["done"],
            output_mode="flash",
        )
        action_enum = (
            schema["input_schema"]["properties"]["action"]
            ["properties"]["name"]["enum"]
        )
        assert action_enum == ["done"]

    def test_include_actions_with_planning(self):
        """include_actions is compatible with enable_planning."""
        registry = _make_registry_with_all_actions()
        schema = registry.get_tool_schema(
            include_actions=["done"],
            enable_planning=True,
        )
        action_enum = (
            schema["input_schema"]["properties"]["action"]
            ["properties"]["name"]["enum"]
        )
        assert action_enum == ["done"]
        assert "plan_update" in schema["input_schema"]["properties"]

    def test_include_actions_nonexistent_action_ignored(self):
        """Actions not in registry are silently ignored."""
        registry = _make_registry_with_all_actions()
        schema = registry.get_tool_schema(include_actions=["done", "nonexistent"])
        action_enum = (
            schema["input_schema"]["properties"]["action"]
            ["properties"]["name"]["enum"]
        )
        assert action_enum == ["done"]

    def test_include_actions_empty_list_returns_nothing(self):
        """Empty include_actions list produces empty enum."""
        registry = _make_registry_with_all_actions()
        schema = registry.get_tool_schema(include_actions=[])
        action_enum = (
            schema["input_schema"]["properties"]["action"]
            ["properties"]["name"]["enum"]
        )
        assert action_enum == []
