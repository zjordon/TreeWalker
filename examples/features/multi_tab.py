"""Example: 多标签。移植自 browser-use/examples/features/multi_tab.py。

开 3 个标签页分别搜索，再切回第一个停止。TreeWalker 有
navigate(new_tab=True) / switch_tab / close_tab 动作，agent 按任务自行调度。
前置：uv sync / chrome --remote-debugging-port=9222 / $env:ZHIPU_API_KEY="..."
运行：uv run python examples/features/multi_tab.py
"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.config import load_settings


TASK = (
	"Open 3 search tabs on https://www.google.com for 'Elon Musk', 'Sam Altman' "
	"and 'Steve Jobs', then switch back to the first tab and stop."
)


async def main() -> None:
	logging.basicConfig(level=logging.INFO)
	settings = load_settings()
	if not settings.llm.api_key:
		print("Error: 请设置 ZHIPU_API_KEY 环境变量")
		sys.exit(1)
	if not settings.browser.ws_url:
		print("Error: 请先用 chrome --remote-debugging-port=9222 启动 Chrome")
		sys.exit(1)

	llm = LLMClient(settings.llm)
	browser = BrowserSession(settings.browser)
	agent = Agent(task=TASK, llm=llm, browser=browser, settings=settings.agent)
	history = await agent.run()

	print("done:", history.is_done())


if __name__ == "__main__":
	asyncio.run(main())
