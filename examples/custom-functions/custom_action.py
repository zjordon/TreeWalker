"""Example: 自定义动作注册范式。移植自 browser-use/examples/custom-functions/file_upload.py（取其注册范式；内置 upload_file 已存在，故用 count_words 演示）。

TreeWalker 注册自定义动作的标准范式：
- Tools() 先注册默认 22 个动作；
- @tools.registry.action(name=, description=, param_model=, terminates=) 注册新动作；
- handler 签名固定 (params: dict, browser: BrowserSession) -> ActionResult|str|None
  （按位置注入，非 browser-use 的按名注入）；
- 无 per-decorator domains=；按页过滤改用 tools.apply_page_filters({动作名:[glob]})。
前置：uv sync / chrome --remote-debugging-port=9222 / $env:ZHIPU_API_KEY="..."
运行：uv run python examples/custom-functions/custom_action.py
"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from pydantic import BaseModel, Field

from tree_walker import Agent, ActionResult, BrowserSession, LLMClient, Tools
from tree_walker.config import load_settings


class CountParams(BaseModel):
	text: str = Field(..., description="要统计单词数的文本")


def build_tools() -> Tools:
	tools = Tools()   # 先注册默认动作（参数全可省，见 tools/actions.py:357）

	@tools.registry.action(
		name="count_words",
		description="Count the number of words in the given text and return the result.",
		param_model=CountParams,
		terminates=False,
	)
	async def _count_words(params: dict, browser: BrowserSession):
		# 注意签名：TreeWalker 按位置注入 (params, browser)，不是按名注入
		n = len(params["text"].split())
		return ActionResult(extracted_content=f"word count = {n}")

	return tools


TASK = (
	"Use the count_words action to count the words in "
	"'hello world from tree walker' and tell me the result."
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
		tools=build_tools(),   # 传入自定义 Tools
	)
	history = await agent.run()

	if history.is_done():
		print(history.final_result())


if __name__ == "__main__":
	asyncio.run(main())
