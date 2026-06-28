"""Example: 表单填写（多字段 + 提交）。移植自 browser-use/examples/getting_started/02_form_filling.py。

在 httpbin 表单页填多项并提交。演示任务驱动的表单填写：
agent 会用 input_text 链式填多个字段，再 click 提交。
前置：uv sync / chrome --remote-debugging-port=9222 / $env:ZHIPU_API_KEY="..."
运行：uv run python examples/getting_started/form_filling.py
"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.config import load_settings


TASK = (
	"Go to https://httpbin.org/forms/post . Fill the form with: "
	"custname='John Doe', custtel='555-1234', custemail='john@example.com', "
	"and select size='large'. Then submit the form and tell me what the response page shows."
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
