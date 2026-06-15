"""Example: multi_act — chained browser actions in a single agent step.

Demonstrates the Phase 1-4 multi_act implementation
(docs/multi_act实现方案.md). Key behaviors to watch for:

- LLM emits multiple actions in one step (up to ``max_actions_per_step``)
- Five guards stop the sequence safely when needed:
  #1 done as single action / #2 is_done / #3 error
  #4 static terminates_sequence (navigate/search/switch_tab/go_back/evaluate)
  #5 runtime URL or target_id drift (e.g. click on <a> navigating)
- Exception triage: InterruptedError and connection errors propagate;
  other exceptions become ActionResult(error) and end the sequence.

Three scenarios (pick via CLI arg):

    python examples/multi_act_demo.py form      # 多字段表单（推荐入门）
    python examples/multi_act_demo.py scroll    # 连续滚动
    python examples/multi_act_demo.py chain     # navigate + click（演示 guard #4）

Comparison mode runs the same task twice (max_actions_per_step=1 vs 5)
and prints a step-count delta:

    python examples/multi_act_demo.py form --compare

Prerequisites:
1. uv sync
2. chrome --remote-debugging-port=9222
3. $env:ZHIPU_API_KEY = "your_key"
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass
from typing import Any

sys.path.insert(0, f"{__file__}/../src")

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.agent.views import AgentHistoryList
from tree_walker.config import AgentSettings, load_settings

logger = logging.getLogger(__name__)


# ── Scenarios ──────────────────────────────────────────────────────────


@dataclass
class Scenario:
	name: str
	task: str
	notes: str


SCENARIOS: dict[str, Scenario] = {
	"form": Scenario(
		name="Multi-field form fill",
		task=(
			"Open https://httpbin.org/forms/post "
			"and fill the form: custname='Alice', custtel='555-1234', "
			"custemail='alice@example.com'. Then submit the form. "
			"Use multiple input_text actions in one step if possible."
		),
		notes=(
			"Expected: agent chains 3 input_text + 1 click in 1-2 steps "
			"with max_actions_per_step=5 (vs 4-5 steps with =1)."
		),
	),
	"scroll": Scenario(
		name="Sequential scrolling",
		task=(
			"Open https://en.wikipedia.org/wiki/Python_(programming_language) "
			"and scroll down 3 times to read more of the article, "
			"then summarize the first paragraph you saw."
		),
		notes=(
			"Expected: agent chains 3 scroll actions in 1 step "
			"(vs 3 separate steps with max_actions_per_step=1)."
		),
	),
	"chain": Scenario(
		name="Page-change termination (guard #4)",
		task=(
			"First navigate to https://en.wikipedia.org/wiki/AsyncJS, "
			"then click the 'JavaScript' link in the first paragraph. "
			"Do not chain them — but if you do, the runtime guard will "
			"correctly skip actions after navigate."
		),
		notes=(
			"Expected: if LLM emits [navigate, click] in one step, "
			"guard #4 stops after navigate; click is deferred to the next step."
		),
	),
}


# ── Runner ─────────────────────────────────────────────────────────────


def _action_name(model_output: dict[str, Any]) -> str:
	"""Pull a human-readable action name(s) from a model_output entry."""
	actions = model_output.get("actions") or [model_output.get("action", {})]
	names = [a.get("name", "?") for a in actions if isinstance(a, dict)]
	return " + ".join(names) if names else "?"


def _print_step_breakdown(history: AgentHistoryList) -> None:
	"""Print per-step action summary to show multi_act in action."""
	print("\nStep-by-step action sequence:")
	print("-" * 60)
	for h in history.history:
		actions_str = _action_name(h.model_output)
		result_summary = []
		for r in h.result:
			if r.is_done:
				result_summary.append(f"DONE(success={r.success})")
			elif r.error:
				result_summary.append(f"ERR({r.error[:30]})")
			else:
				result_summary.append("OK")
		n_actions = len(h.model_output.get("actions") or [h.model_output.get("action", {})])
		print(
			f"  step {h.step_number:>2}: [{n_actions} action(s)] "
			f"{actions_str:<40} → {' | '.join(result_summary)}"
		)
	print("-" * 60)


def _print_summary(label: str, history: AgentHistoryList) -> None:
	"""Print high-level stats for one run."""
	total_actions = sum(
		len(h.model_output.get("actions") or [h.model_output.get("action", {})])
		for h in history.history
	)
	multi_action_steps = sum(
		1 for h in history.history
		if len(h.model_output.get("actions") or [h.model_output.get("action", {})]) > 1
	)
	print(f"\n{'=' * 60}")
	print(f"  {label}")
	print(f"{'=' * 60}")
	print(f"  Total steps:           {len(history.history)}")
	print(f"  Total actions emitted: {total_actions}")
	print(f"  Multi-action steps:    {multi_action_steps}")
	print(f"  Task completed:        {history.is_done()}")
	if history.final_result():
		excerpt = history.final_result()[:200].replace("\n", " ")
		print(f"  Final result:          {excerpt}...")
	print(f"{'=' * 60}")
	_print_step_breakdown(history)


async def _run_once(
	scenario: Scenario,
	max_actions: int,
	settings: Any,
) -> AgentHistoryList:
	"""Run the scenario once with the given max_actions_per_step."""
	agent_settings = AgentSettings(
		max_steps=settings.agent.max_steps,
		max_failures=settings.agent.max_failures,
		max_actions_per_step=max_actions,
		llm_timeout=settings.agent.llm_timeout,
		action_timeout=settings.agent.action_timeout,
		reconnect_timeout=settings.agent.reconnect_timeout,
	)
	llm = LLMClient(settings.llm)
	browser = BrowserSession(settings.browser)
	agent = Agent(
		task=scenario.task,
		llm=llm,
		browser=browser,
		settings=agent_settings,
	)
	print(f"\n▶ Running scenario '{scenario.name}' with max_actions_per_step={max_actions}...")
	print(f"  Task: {scenario.task[:120]}...")
	history = await agent.run()
	_print_summary(f"max_actions_per_step={max_actions}", history)
	return history


# ── Main ───────────────────────────────────────────────────────────────


async def main() -> None:
	parser = argparse.ArgumentParser(description="multi_act demo")
	parser.add_argument(
		"scenario",
		choices=list(SCENARIOS.keys()),
		help="Scenario to run.",
	)
	parser.add_argument(
		"--compare",
		action="store_true",
		help="Run the scenario twice (max=1 then max=5) and compare step counts.",
	)
	parser.add_argument(
		"--max-actions",
		type=int,
		default=5,
		help="max_actions_per_step value (default: 5; ignored when --compare).",
	)
	parser.add_argument(
		"--verbose",
		action="store_true",
		help="Enable DEBUG logging from tree_walker.",
	)
	args = parser.parse_args()

	logging.basicConfig(
		level=logging.DEBUG if args.verbose else logging.INFO,
		format="%(asctime)s %(name)s %(levelname)s %(message)s",
	)

	settings = load_settings()
	if not settings.llm.api_key:
		print("Error: Set ZHIPU_API_KEY environment variable")
		sys.exit(1)
	if not settings.browser.ws_url:
		print(
			"Error: Cannot connect to Chrome. "
			"Is it running with --remote-debugging-port=9222?"
		)
		sys.exit(1)

	scenario = SCENARIOS[args.scenario]
	print(f"\nscenario: {scenario.name}")
	print(f"notes:    {scenario.notes}")

	if args.compare:
		h1 = await _run_once(scenario, max_actions=1, settings=settings)
		h5 = await _run_once(scenario, max_actions=5, settings=settings)
		delta = len(h1.history) - len(h5.history)
		print(f"\n{'=' * 60}")
		print(f"  COMPARISON: max=1 → {len(h1.history)} steps, max=5 → {len(h5.history)} steps")
		print(f"  Step reduction: {delta} steps ({delta * 100 // max(len(h1.history), 1)}%)")
		print(f"{'=' * 60}")
	else:
		await _run_once(scenario, max_actions=args.max_actions, settings=settings)


if __name__ == "__main__":
	asyncio.run(main())
