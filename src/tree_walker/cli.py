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
		summary_llm=llm,
	)

	if results and results[-1].is_done:
		summary = results[-1]
		print(f"Success: {summary.success}")
		print(summary.extracted_content)
	else:
		print("Rerun produced no summary")


@click.command()
@click.option("--host", default="127.0.0.1", help="web 服务监听地址")
@click.option("--port", type=int, default=8766, help="web 服务端口（默认 8766）")
@click.option(
	"--cdp-port",
	type=int,
	default=9223,
	help="Chrome 远程调试端口：live 任务/试跑/批量连这个端口（默认 9223，与 web/重放路径 "
	     "serve_web/csv_rerun 一致；tw-tui 走 config 默认 9222）",
)
@click.option(
	"--history-dir",
	default=None,
	help="历史 JSON 根目录（默认 settings.agent.rerun_history_dir）",
)
def web(host: str, port: int, cdp_port: int, history_dir: str | None) -> None:
	"""启动 TreeWalker web 前端（P6：live agent 控制台 + 流程库 编辑/重放/详情）。

	浏览器端统一入口，承接 TUI 能力（运行/重放/录制）。先构建前端
	（``cd web_ui && npm run build``），再跑本命令；浏览器开 http://<host>:<port>/ 。
	Chrome 需以 ``--remote-debugging-port=<cdp-port>`` 启动。
	"""
	import os

	from tree_walker.web.server import run_server

	logging.basicConfig(level=logging.INFO)
	# _build_agent 经 load_settings 读 CDP_PORT 连 Chrome；显式置默认 9223（web/重放路径约定端口）
	os.environ["CDP_PORT"] = str(cdp_port)
	print(f"TreeWalker web: http://{host}:{port}/  (Chrome CDP 端口={cdp_port})")
	run_server(host=host, port=port, history_dir=history_dir)
