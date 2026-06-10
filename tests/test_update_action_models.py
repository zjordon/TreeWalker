"""Tests for _update_action_models_for_page."""

from unittest.mock import MagicMock

from tree_walker.tools.actions import Tools


def _make_agent_with_filters(filters: dict[str, list[str]] | None = None):
    """Create a minimal mock agent for testing _update_action_models_for_page."""
    from tree_walker.agent.agent import Agent
    from tree_walker.agent.views import AgentState
    from tree_walker.config import AgentSettings
    from tree_walker.tools.actions import Tools

    tools = Tools()
    if filters:
        tools.apply_page_filters(filters)

    agent = MagicMock(spec=Agent)
    agent.tools = tools
    agent.state = AgentState()
    agent._safe_task = "test task"
    agent._enable_planning = False
    agent._enable_decision_attribution = False
    agent._output_mode = "standard"
    agent._tool_schema = tools.registry.get_tool_schema(enable_planning=False)
    agent._system_prompt = "old prompt"

    # Bind the real method
    from tree_walker.agent.step import StepPipeline
    agent._update_action_models_for_page = StepPipeline._update_action_models_for_page.__get__(agent)

    return agent


class TestUpdateActionModels:
    def test_filters_schema_by_url(self):
        """After filtering, tool_schema only includes matching actions."""
        agent = _make_agent_with_filters({"upload_file": ["*/upload*"]})

        agent._update_action_models_for_page("https://example.com/home")

        action_enum = agent._tool_schema["input_schema"]["properties"]["action"]["properties"]["name"]["enum"]
        assert "upload_file" not in action_enum
        assert "click" in action_enum

    def test_includes_filtered_on_matching_url(self):
        """URL matching includes the filtered action."""
        agent = _make_agent_with_filters({"upload_file": ["*/upload*"]})

        agent._update_action_models_for_page("https://example.com/upload/form")

        action_enum = agent._tool_schema["input_schema"]["properties"]["action"]["properties"]["name"]["enum"]
        assert "upload_file" in action_enum
        assert "click" in action_enum

    def test_no_filters_includes_all(self):
        """With no filter rules, all actions are available."""
        agent = _make_agent_with_filters(None)

        agent._update_action_models_for_page("https://example.com/home")

        action_enum = agent._tool_schema["input_schema"]["properties"]["action"]["properties"]["name"]["enum"]
        assert "upload_file" in action_enum
        assert "click" in action_enum

    def test_system_prompt_updated(self):
        """_system_prompt is updated."""
        agent = _make_agent_with_filters({"upload_file": ["*/upload*"]})
        old_prompt = agent._system_prompt

        agent._update_action_models_for_page("https://example.com/home")

        assert agent._system_prompt != old_prompt
        assert "upload_file" not in agent._system_prompt
        assert "click" in agent._system_prompt
