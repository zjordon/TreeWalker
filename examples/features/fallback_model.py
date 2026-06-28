"""Example: fallback 模型。移植自 browser-use/examples/features/fallback_model.py。

主模型限流/出错时自动回退到更便宜/更快的模型。
TreeWalker 用 LLMSettings(fallback=FallbackLLMSettings(...))；仅限 Anthropic 兼容后端
（不可像 browser-use 那样回退到不同 provider 的 langchain 模型）。
也可全用 env 配置：FALLBACK_LLM_MODEL / FALLBACK_LLM_API_KEY 等。
前置：uv sync / chrome --remote-debugging-port=9222 / $env:ZHIPU_API_KEY="..."
运行：uv run python examples/features/fallback_model.py
"""
import asyncio
import logging
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.config import FallbackLLMSettings, load_settings


TASK = "Go to https://news.ycombinator.com/ and list the top 3 story titles."


async def main() -> None:
	logging.basicConfig(level=logging.INFO)
	settings = load_settings()
	if not settings.llm.api_key:
		print("Error: 请设置 ZHIPU_API_KEY 环境变量")
		sys.exit(1)
	if not settings.browser.ws_url:
		print("Error: 请先用 chrome --remote-debugging-port=9222 启动 Chrome")
		sys.exit(1)

	# 主模型不变，挂一个 fallback（按需改成你可用的便宜模型）
	llm_settings = replace(
		settings.llm,
		fallback=FallbackLLMSettings(
			model="glm-4-flash",
			api_key=settings.llm.api_key,
			base_url=settings.llm.base_url,
		),
	)
	llm = LLMClient(llm_settings)
	browser = BrowserSession(settings.browser)
	agent = Agent(task=TASK, llm=llm, browser=browser, settings=settings.agent)
	history = await agent.run()

	if history.is_done():
		print(history.final_result())


if __name__ == "__main__":
	asyncio.run(main())
