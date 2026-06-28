"""Example: flash 快速模式。移植自 browser-use/examples/getting_started/05_fast_agent.py（仅保留可移植部分）。

browser-use 原版三件套：flash_mode + 时延(minimum_wait_page_load_time/wait_between_actions)
+ extend_system_message(SPEED_OPTIMIZATION_PROMPT)。
TreeWalker 对应：output_mode='flash' + BrowserSettings(page_settle_timeout/wait_between_actions)。
「extend_system_message」TreeWalker 无此扩展点 → 丢弃（见上游方案 §6）。
前置：uv sync / chrome --remote-debugging-port=9222 / $env:ZHIPU_API_KEY="..."
运行：uv run python examples/getting_started/fast_agent.py
"""
import asyncio
import logging
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.config import load_settings


TASK = "Go to https://news.ycombinator.com/ and tell me the top 3 story titles."


async def main() -> None:
	logging.basicConfig(level=logging.INFO)
	settings = load_settings()
	if not settings.llm.api_key:
		print("Error: 请设置 ZHIPU_API_KEY 环境变量")
		sys.exit(1)
	if not settings.browser.ws_url:
		print("Error: 请先用 chrome --remote-debugging-port=9222 启动 Chrome")
		sys.exit(1)

	# flash 模式 + 收紧页面等待/动作间隔
	llm_settings = replace(settings.llm, output_mode="flash")
	browser_settings = replace(
		settings.browser,
		wait_between_actions=0.1,
		page_settle_timeout=0.5,
	)
	llm = LLMClient(llm_settings)
	browser = BrowserSession(browser_settings)
	agent = Agent(task=TASK, llm=llm, browser=browser, settings=settings.agent)
	history = await agent.run()

	if history.is_done():
		print(history.final_result())


if __name__ == "__main__":
	asyncio.run(main())
