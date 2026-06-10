"""Tests for ActionRegistry page-URL filtering."""

from pydantic import BaseModel

from tree_walker.tools.registry import ActionRegistry, RegisteredAction


class _DummyParams(BaseModel):
    pass


def _make_registry(**actions: list[str] | None) -> ActionRegistry:
    """创建带 page_patterns 的 registry。"""
    registry = ActionRegistry()
    for name, patterns in actions.items():
        registry.actions[name] = RegisteredAction(
            name=name,
            description=f"{name} action",
            param_model=_DummyParams,
            handler=lambda params, browser: None,
            page_patterns=patterns,
        )
    return registry


class TestActionAvailable:
    def test_no_patterns_always_available(self):
        """page_patterns=None 时始终可用。"""
        registry = _make_registry(click=None)
        assert registry._action_available("click", "https://example.com/page") is True

    def test_matching_pattern_available(self):
        """URL 匹配 glob 模式时可用。"""
        registry = _make_registry(upload=["*/upload*"])
        assert registry._action_available("upload", "https://example.com/upload/form") is True

    def test_non_matching_pattern_unavailable(self):
        """URL 不匹配 glob 模式时不可用。"""
        registry = _make_registry(upload=["*/upload*"])
        assert registry._action_available("upload", "https://example.com/home") is False

    def test_none_url_no_filtering(self):
        """page_url=None 时不做过滤。"""
        registry = _make_registry(upload=["*/upload*"])
        assert registry._action_available("upload", None) is True

    def test_multiple_patterns_any_match(self):
        """多个模式只要有一个匹配即可。"""
        registry = _make_registry(pdf=["*.gov.cn/*", "*/documents/*"])
        assert registry._action_available("pdf", "https://example.gov.cn/report") is True
        assert registry._action_available("pdf", "https://example.com/documents/contract") is True
        assert registry._action_available("pdf", "https://example.com/home") is False


class TestSchemaFiltering:
    def test_get_tool_schema_filters_by_url(self):
        """get_tool_schema 只包含当前 URL 可用的动作。"""
        registry = _make_registry(click=None, upload=["*/upload*"])
        schema = registry.get_tool_schema(page_url="https://example.com/home")
        action_enum = schema["input_schema"]["properties"]["action"]["properties"]["name"]["enum"]
        assert "click" in action_enum
        assert "upload" not in action_enum

    def test_get_tool_schema_no_url_includes_all(self):
        """page_url=None 时包含所有动作。"""
        registry = _make_registry(click=None, upload=["*/upload*"])
        schema = registry.get_tool_schema(page_url=None)
        action_enum = schema["input_schema"]["properties"]["action"]["properties"]["name"]["enum"]
        assert "click" in action_enum
        assert "upload" in action_enum

    def test_get_action_descriptions_text_filters_by_url(self):
        """get_action_descriptions_text 只包含当前 URL 可用的动作。"""
        registry = _make_registry(click=None, upload=["*/upload*"])
        text = registry.get_action_descriptions_text(page_url="https://example.com/home")
        assert "click" in text
        assert "upload" not in text

    def test_get_tool_schema_matching_url_includes_filtered(self):
        """URL 匹配时包含被过滤的动作。"""
        registry = _make_registry(click=None, upload=["*/upload*"])
        schema = registry.get_tool_schema(page_url="https://example.com/upload/form")
        action_enum = schema["input_schema"]["properties"]["action"]["properties"]["name"]["enum"]
        assert "click" in action_enum
        assert "upload" in action_enum
