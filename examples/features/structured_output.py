"""Example: 结构化输出（output_model）。移植自 browser-use/examples/features/custom_output.py。

让 agent 抓取 Hacker News 前 5 条，并以 Pydantic 模型结构化返回。
与 browser-use 的唯一差异：参数名 output_model_schema → output_model。

前置：uv sync / chrome --remote-debugging-port=9222 / $env:ZHIPU_API_KEY="..."
运行：uv run python examples/features/structured_output.py
"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from pydantic import BaseModel

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.config import load_settings


class Post(BaseModel):
	post_title: str
	post_url: str
	num_comments: int
	hours_since_post: int


class Posts(BaseModel):
	posts: list[Post]


TASK = "Go to https://news.ycombinator.com/show and give me the first 5 posts."


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
		output_model=Posts,   # ← browser-use 是 output_model_schema=Posts
	)
	history = await agent.run()

	result = history.final_result()
	if not result:
		print("No result")
		return
	try:
		parsed = Posts.model_validate_json(result)
	except ValueError:
		# final_result() 可能是非 JSON 字符串（agent 未能产出合法结构化输出、success=False 兜底）。
		# 直接展示原始结果，而不是抛 traceback（Pydantic ValidationError 是 ValueError 子类）。
		print(f"Agent did not return valid {Posts.__name__} JSON:")
		print(result)
		return
	for p in parsed.posts:
		print(f"- {p.post_title}\n  {p.post_url}  (comments={p.num_comments}, {p.hours_since_post}h ago)")


if __name__ == "__main__":
	asyncio.run(main())
