"""CLI entry point for the TUI interface."""

from __future__ import annotations

import asyncio
import logging

import click

from tree_walker.config import load_settings

logger = logging.getLogger(__name__)


@click.command()
@click.option("--task", "-t", default=None, help="初始任务（可选，启动后自动填入输入框）")
@click.option("--debug", is_flag=True, default=False, help="调试模式（显示详细日志）")
def main(task: str | None, debug: bool) -> None:
	"""TreeWalker TUI 交互界面."""
	settings = load_settings()

	# TUI mode: force enable observability for event stream
	settings.agent.enable_observability = True

	if debug:
		settings.tui.log_level = "DEBUG"

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
