"""Example: 敏感数据（扁平 sensitive_data）。移植自 browser-use/examples/features/sensitive_data.py（仅简单/扁平形式）。

字典约定与 browser-use 扁平形式一致：key=占位符，value=真实值，无需翻转。
机制：发往 LLM 前，真实值被替换为占位符（LLM 看不到真实值）；
LLM 输出动作里的占位符会在执行前还原成真实值。
注意：browser-use 的“按域嵌套” {'domain':{...}} TreeWalker 不支持。
前置：uv sync / chrome --remote-debugging-port=9222 / $env:ZHIPU_API_KEY="..."
运行：uv run python examples/features/sensitive_data.py
"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.config import load_settings


# key=占位符（任务里用 <x_name> 引用），value=真实值
SENSITIVE_DATA = {
	"<x_name>": "my_x_name",
	"<x_password>": "my_x_password",
}

TASK = (
	"Go to https://httpbin.org/forms/post and fill the custname field with <x_name> "
	"and put <x_password> into the comment field, then submit the form."
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
	agent = Agent(
		task=TASK,
		llm=llm,
		browser=browser,
		settings=settings.agent,
		sensitive_data=SENSITIVE_DATA,
	)
	history = await agent.run()

	if history.is_done():
		print(history.final_result())


if __name__ == "__main__":
	asyncio.run(main())
