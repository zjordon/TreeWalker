"""Example: 数据抽取（任务驱动）。移植自 browser-use/examples/getting_started/03_data_extraction.py。

让 agent 抓取稳定测试站（quotes.toscrape.com）的名言与作者，纯任务驱动、无需 output_model。
前置：uv sync / chrome --remote-debugging-port=9222 / $env:ZHIPU_API_KEY="..."
运行：uv run python examples/getting_started/data_extraction.py
"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.config import load_settings


TASK = (
	"Go to https://quotes.toscrape.com/ and extract the first 5 quotes "
	"with their text and author. Return them as a plain list."
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
