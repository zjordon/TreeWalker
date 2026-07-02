"""CLI entry point for the TUI interface and history rerun."""

from __future__ import annotations

import asyncio
import logging

import click

from tree_walker.config import load_settings

logger = logging.getLogger(__name__)


@click.command()
@click.option("--task", "-t", default=None, help="初始任务（可选，启动后自动填入输入框）")
@click.option("--debug", is_flag=True, default=False, help="调试模式（显示详细日志）")
@click.option(
	"--rerun",
	"rerun_path",
	default=None,
	type=click.Path(dir_okay=False),
	help="重放指定历史文件（相对 rerun_history_dir；不进入交互 TUI）",
)
@click.option(
	"--var",
	"variables",
	multiple=True,
	help="变量替换 key=value（可重复，配合 --rerun）",
)
def main(
	task: str | None,
	debug: bool,
	rerun_path: str | None,
	variables: tuple[str, ...],
) -> None:
	"""TreeWalker TUI 交互界面 / 历史重放."""
	settings = load_settings()

	# TUI mode: force enable observability for event stream
	settings.agent.enable_observability = True

	if debug:
		settings.tui.log_level = "DEBUG"

	if rerun_path:
		var_map: dict[str, str] = {}
		for kv in variables:
			key, _, val = kv.partition("=")
			if key:
				var_map[key] = val
		asyncio.run(_rerun(settings, rerun_path, var_map, debug))
	else:
		asyncio.run(_launch_tui(settings, task, debug))


async def _launch_tui(settings, initial_task: str | None, debug: bool) -> None:
	"""Create core components and launch the Textual app."""
	from tree_walker.browser.session import BrowserSession
	from tree_walker.llm.client import LLMClient
	from tree_walker.tui.app import TreeWalkerApp

	llm = LLMClient(settings.llm)
	browser = BrowserSession(settings.browser)
	app = TreeWalkerApp(
		llm=llm,
		browser=browser,
		agent_settings=settings.agent,
		sensitive_data=settings.agent.sensitive_data,
		initial_task=initial_task,
		debug=debug,
	)
	await app.run_async()


async def _rerun(settings, rerun_path: str, variables: dict[str, str], debug: bool) -> None:
	"""从历史文件重放，打印 AI 摘要。"""
	from tree_walker.agent import Agent
	from tree_walker.browser.session import BrowserSession
	from tree_walker.llm.client import LLMClient

	logging.basicConfig(level=logging.DEBUG if debug else logging.INFO)

	llm = LLMClient(settings.llm)
	browser = BrowserSession(settings.browser)
	agent = Agent(task="", llm=llm, browser=browser, settings=settings.agent)
	results = await agent.load_and_rerun(
		rerun_path,
		variables=variables or None,
		max_step_interval=5,
		delay_between_actions=1,
		summary_llm=llm,
	)

	if results and results[-1].is_done:
		summary = results[-1]
		print(f"Success: {summary.success}")
		print(summary.extracted_content)
	else:
		print("Rerun produced no summary")
