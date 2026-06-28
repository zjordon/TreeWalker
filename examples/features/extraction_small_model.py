"""Example: 抽取专用小模型。移植自 browser-use/examples/features/small_model_for_extraction.py。

让 extract 工具用更便宜/更快的模型（browser-use 的 page_extraction_llm）。
TreeWalker 用 AgentSettings(extract_llm=LLMSettings(...))；为 None 时复用主 LLM。
也可用 env：AGENT_EXTRACT_MODEL / AGENT_EXTRACT_API_KEY 等。
前置：uv sync / chrome --remote-debugging-port=9222 / $env:ZHIPU_API_KEY="..."
运行：uv run python examples/features/extraction_small_model.py
"""
import asyncio
import logging
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.config import LLMSettings, load_settings


TASK = (
	"Go to https://news.ycombinator.com/ and use the extract action to get the "
	"titles and points of the top 5 stories."
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

	# extract 工具换用小模型（按需改成你可用的便宜模型）
	agent_settings = replace(
		settings.agent,
		extract_llm=LLMSettings(
			model="glm-4-flash",
			api_key=settings.llm.api_key,
			base_url=settings.llm.base_url,
		),
	)
	llm = LLMClient(settings.llm)
	browser = BrowserSession(settings.browser)
	agent = Agent(task=TASK, llm=llm, browser=browser, settings=agent_settings)
	history = await agent.run()

	if history.is_done():
		print(history.final_result())


if __name__ == "__main__":
	asyncio.run(main())
