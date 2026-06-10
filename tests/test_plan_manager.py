"""Unit tests for PlanManager."""
import pytest

from tree_walker.agent.plan_manager import PlanManager
from tree_walker.agent.views import AgentState, PlanItem


@pytest.fixture
def mgr():
    return PlanManager()


class TestRenderPlanDescription:
    def test_returns_none_for_none_plan(self, mgr):
        assert mgr.render_plan_description(None) is None

    def test_returns_none_for_empty_plan(self, mgr):
        assert mgr.render_plan_description([]) is None

    def test_renders_single_pending_step(self, mgr):
        plan = [PlanItem(text="Do something")]
        result = mgr.render_plan_description(plan)
        assert "[ ] 0: Do something" in result

    def test_renders_mixed_statuses(self, mgr):
        plan = [
            PlanItem(text="Step A", status="done"),
            PlanItem(text="Step B", status="current"),
            PlanItem(text="Step C", status="pending"),
            PlanItem(text="Step D", status="skipped"),
        ]
        result = mgr.render_plan_description(plan)
        assert "[x] 0: Step A" in result
        assert "[>] 1: Step B" in result
        assert "[ ] 2: Step C" in result
        assert "[-] 3: Step D" in result

    def test_unknown_status_defaults_to_pending(self, mgr):
        plan = [PlanItem(text="Weird", status="unknown")]
        result = mgr.render_plan_description(plan)
        assert "[ ] 0: Weird" in result


class TestUpdateFromModelOutput:
    def test_plan_update_creates_new_plan(self, mgr):
        state = AgentState()
        model_output = {"plan_update": ["Step A", "Step B", "Step C"]}
        mgr.update_from_model_output(state, model_output)
        assert state.plan is not None
        assert len(state.plan) == 3
        assert state.plan[0].text == "Step A"
        assert state.plan[0].status == "current"
        assert state.plan[1].status == "pending"
        assert state.plan[2].status == "pending"
        assert state.current_plan_item_index == 0

    def test_plan_update_resets_existing_plan(self, mgr):
        state = AgentState(
            plan=[PlanItem(text="Old", status="done")],
            current_plan_item_index=1,
        )
        model_output = {"plan_update": ["New A", "New B"]}
        mgr.update_from_model_output(state, model_output)
        assert len(state.plan) == 2
        assert state.plan[0].status == "current"
        assert state.current_plan_item_index == 0

    def test_plan_update_records_generation_step(self, mgr):
        state = AgentState(n_steps=5)
        model_output = {"plan_update": ["Step A"]}
        mgr.update_from_model_output(state, model_output)
        assert state.plan_generation_step == 5

    def test_plan_update_returns_early_no_path_b(self, mgr):
        state = AgentState(
            plan=[PlanItem(text="Step A", status="current")],
            current_plan_item_index=0,
        )
        model_output = {"plan_update": ["New"], "current_plan_item": 0}
        mgr.update_from_model_output(state, model_output)
        assert len(state.plan) == 1
        assert state.plan[0].text == "New"
        assert state.plan[0].status == "current"

    def test_current_plan_item_advances(self, mgr):
        state = AgentState(
            plan=[
                PlanItem(text="Step 0", status="current"),
                PlanItem(text="Step 1", status="pending"),
                PlanItem(text="Step 2", status="pending"),
            ],
            current_plan_item_index=0,
        )
        mgr.update_from_model_output(state, {"current_plan_item": 2})
        assert state.plan[0].status == "done"
        assert state.plan[1].status == "done"
        assert state.plan[2].status == "current"
        assert state.current_plan_item_index == 2

    def test_current_plan_item_clamps_high(self, mgr):
        state = AgentState(
            plan=[PlanItem(text="A"), PlanItem(text="B")],
            current_plan_item_index=0,
        )
        mgr.update_from_model_output(state, {"current_plan_item": 99})
        assert state.current_plan_item_index == 1

    def test_current_plan_item_clamps_negative(self, mgr):
        state = AgentState(
            plan=[PlanItem(text="A", status="current")],
            current_plan_item_index=0,
        )
        mgr.update_from_model_output(state, {"current_plan_item": -5})
        assert state.current_plan_item_index == 0

    def test_no_update_without_plan_fields(self, mgr):
        plan = [PlanItem(text="A", status="current")]
        state = AgentState(plan=plan, current_plan_item_index=0)
        mgr.update_from_model_output(state, {"next_goal": "do something"})
        assert state.plan == plan
        assert state.current_plan_item_index == 0

    def test_current_plan_item_ignored_when_no_plan(self, mgr):
        state = AgentState()
        mgr.update_from_model_output(state, {"current_plan_item": 1})
        assert state.plan is None


class TestBuildReplanNudge:
    def test_returns_none_below_threshold(self, mgr):
        assert mgr.build_replan_nudge(2, 3, [PlanItem(text="A")]) is None

    def test_returns_nudge_at_threshold(self, mgr):
        result = mgr.build_replan_nudge(3, 3, [PlanItem(text="A")])
        assert result is not None
        assert "plan" in result.lower()

    def test_returns_none_when_no_plan(self, mgr):
        assert mgr.build_replan_nudge(5, 3, None) is None


class TestBuildExplorationNudge:
    def test_returns_none_below_threshold(self, mgr):
        assert mgr.build_exploration_nudge(3, 5, None) is None

    def test_returns_nudge_at_threshold(self, mgr):
        result = mgr.build_exploration_nudge(5, 5, None)
        assert result is not None
        assert "plan" in result.lower()

    def test_returns_none_when_plan_exists(self, mgr):
        assert mgr.build_exploration_nudge(10, 5, [PlanItem(text="A")]) is None
