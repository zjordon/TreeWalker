"""Stateless plan management: rendering, updating, and nudge generation."""
from __future__ import annotations

import logging

from tree_walker.agent.views import AgentState, PlanItem

logger = logging.getLogger(__name__)

_MARKERS = {'done': '[x]', 'current': '[>]', 'pending': '[ ]', 'skipped': '[-]'}


class PlanManager:
    """Stateless service for plan rendering, update, and nudge logic."""

    def render_plan_description(self, plan: list[PlanItem] | None) -> str | None:
        """Render the current plan as a text description for LLM context."""
        if not plan:
            return None
        lines = []
        for i, step in enumerate(plan):
            marker = _MARKERS.get(step.status, '[ ]')
            lines.append(f'{marker} {i}: {step.text}')
        return '\n'.join(lines)

    def update_from_model_output(self, state: AgentState, model_output: dict) -> None:
        """Update plan state from LLM output.

        Two mutually exclusive paths:
          A) plan_update: replace the entire plan
          B) current_plan_item: advance the current step index
        """
        # Path A: full plan replacement
        if 'plan_update' in model_output and model_output['plan_update'] is not None:
            steps = model_output['plan_update']
            state.plan = [PlanItem(text=text) for text in steps]
            state.current_plan_item_index = 0
            state.plan_generation_step = state.n_steps
            if state.plan:
                state.plan[0].status = 'current'
            logger.info(
                'Plan %s with %d steps',
                'updated' if state.plan_generation_step else 'created',
                len(state.plan),
            )
            return

        # Path B: advance current step index
        if 'current_plan_item' in model_output and model_output['current_plan_item'] is not None and state.plan is not None:
            new_idx = model_output['current_plan_item']
            new_idx = max(0, min(new_idx, len(state.plan) - 1))
            old_idx = state.current_plan_item_index

            for i in range(old_idx, new_idx):
                if i < len(state.plan) and state.plan[i].status in ('current', 'pending'):
                    state.plan[i].status = 'done'

            if new_idx < len(state.plan):
                state.plan[new_idx].status = 'current'

            state.current_plan_item_index = new_idx

    def build_replan_nudge(
        self,
        consecutive_failures: int,
        threshold: int,
        plan: list[PlanItem] | None,
    ) -> str | None:
        """Return a re-plan prompt when consecutive failures exceed threshold."""
        if not plan or consecutive_failures < threshold:
            return None
        return (
            "You have failed multiple consecutive times. The current plan may not be working. "
            "Consider revising the plan by providing a new plan_update with adjusted steps."
        )

    def build_exploration_nudge(
        self,
        n_steps: int,
        threshold: int,
        plan: list[PlanItem] | None,
    ) -> str | None:
        """Return a plan-creation prompt when exploring without a plan."""
        if plan is not None or n_steps < threshold:
            return None
        return (
            "You have been exploring for several steps without a structured plan. "
            "Consider breaking down the task into clear steps by providing a plan_update."
        )
