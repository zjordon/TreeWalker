"""Example: 多步任务（搜索 → 进结果 → 抽取 → 汇报）。移植自 browser-use/examples/getting_started/04_multi_step_task.py。

演示多步推理：搜索某主题，打开第一个结果，抽取一段定义，汇报来源 URL 与定义。
前置：uv sync / chrome --remote-debugging-port=9222 / $env:ZHIPU_API_KEY="..."
运行：uv run python examples/getting_started/multi_step_task.py
"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.config import load_settings


TASK = (
	"Go to https://www.google.com and search for 'what is browser automation'. "
	"Open the first result, extract a one-paragraph definition of browser automation "
	"from that page, then tell me the source URL and the definition."
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
