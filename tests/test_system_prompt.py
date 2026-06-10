"""Tests for system prompt content — P0 Task Completion Rules."""

from tree_walker.prompts.system_prompt import build_system_prompt


def _default_action_descriptions() -> str:
    from tree_walker.tools.registry import ActionRegistry
    registry = ActionRegistry()
    return registry.get_action_descriptions_text()


class TestTaskCompletionRules:
    def test_prompt_contains_task_completion_rules_section(self):
        prompt = build_system_prompt(
            action_descriptions=_default_action_descriptions(),
            task="Test task",
        )
        assert "## Task Completion Rules" in prompt

    def test_prompt_contains_done_success_true_verification(self):
        prompt = build_system_prompt(
            action_descriptions=_default_action_descriptions(),
            task="Test task",
        )
        assert "done(success=true)" in prompt

    def test_prompt_contains_verification_checklist(self):
        prompt = build_system_prompt(
            action_descriptions=_default_action_descriptions(),
            task="Test task",
        )
        assert "Re-read the user's original task" in prompt
        assert "Check each requirement" in prompt
        assert "Verify actions actually completed" in prompt
        assert "Verify data sources" in prompt
        assert "Check for blocking errors" in prompt
        assert "Any unmet requirement" in prompt

    def test_prompt_contains_success_false_guidance(self):
        prompt = build_system_prompt(
            action_descriptions=_default_action_descriptions(),
            task="Test task",
        )
        assert "done(success=false)" in prompt
        assert "Never claim success prematurely" in prompt

    def test_prompt_contains_forced_done_triggers(self):
        prompt = build_system_prompt(
            action_descriptions=_default_action_descriptions(),
            task="Test task",
        )
        assert "max_steps" in prompt
        assert "ABSOLUTELY IMPOSSIBLE" in prompt


class TestDoneParamsDescription:
    def test_done_params_text_field_description(self):
        from tree_walker.tools.models import DoneParams
        field_info = DoneParams.model_fields["text"]
        assert "directly observed" in field_info.description
