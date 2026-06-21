"""Tests for system prompt content — P0 Task Completion Rules."""

from tree_walker.prompts.system_prompt import build_state_message, build_system_prompt


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


class TestFileInputsSection:
    """[File Inputs] 段：多 file input 时给 LLM 元数据，帮其锁定 live input（抖音封面）。"""

    def _state(self, metas):
        from tree_walker.browser.views import (
            BrowserStateSummary,
            SerializedDOMState,
        )
        return BrowserStateSummary(
            dom_state=SerializedDOMState(
                _root=None,
                selector_map={},
                element_tree_text="",
                file_inputs_meta=metas,
            ),
        )

    def test_section_lists_each_input_when_multiple(self):
        from tree_walker.browser.views import FileInputInfo
        metas = [
            FileInputInfo(backend_node_id=10, accept="image/*", visible=False, upload_ancestor=True),
            FileInputInfo(backend_node_id=20, visible=True, upload_ancestor=True),
            FileInputInfo(backend_node_id=30, visible=False, upload_ancestor=False),
        ]
        msg = build_state_message(self._state(metas))
        assert "[File Inputs]" in msg
        assert "Multiple file inputs" in msg
        for bid in (10, 20, 30):
            assert f"[{bid}]" in msg
        assert "hidden" in msg  # 10 / 30
        assert "visible" in msg  # 20
        assert "upload-ancestor=yes" in msg  # 10 / 20
        assert "accept=image/*" in msg  # 10

    def test_no_section_when_one_or_fewer(self):
        from tree_walker.browser.views import FileInputInfo
        assert "[File Inputs]" not in build_state_message(
            self._state([FileInputInfo(backend_node_id=10)])
        )
        assert "[File Inputs]" not in build_state_message(self._state([]))
