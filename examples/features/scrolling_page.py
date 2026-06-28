"""Example: 滚动长页面。移植自 browser-use/examples/features/scrolling_page.py。

让 agent 在一个长页面上向下滚动并抓取底部内容，演示 scroll 动作。
前置：uv sync / chrome --remote-debugging-port=9222 / $env:ZHIPU_API_KEY="..."
运行：uv run python examples/features/scrolling_page.py
"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.config import load_settings


TASK = (
	"Go to https://news.ycombinator.com/ , scroll down to the bottom of the page "
	"using the scroll action, then report the title of the last item on the page."
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

	if history.is_done():
		print(history.final_result())


if __name__ == "__main__":
	asyncio.run(main())
