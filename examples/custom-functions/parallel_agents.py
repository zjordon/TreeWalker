"""Example: 并发多 agent（asyncio.gather）。移植自 browser-use/examples/custom-functions/parallel_agents.py。

形态 A（本文件）：多个 BrowserSession 连同一个 Chrome，共享浏览器上下文/标签页空间。
适合演示并发写法，但任务间可能互相干扰（标签切换/焦点）。

【强隔离方案（形态 B）】如需互不干扰，应为每个并发 agent 启动独立的 Chrome
（各自独立的 --remote-debugging-port，例如 9222/9223/9224），再用各自的
BrowserSettings(ws_url=...) / cdp_port 连接。TreeWalker 不启动浏览器、没有
browser-use 的 user_data_dir，无法靠配置实现多实例隔离。
前置：uv sync / chrome --remote-debugging-port=9222 / $env:ZHIPU_API_KEY="..."
运行：uv run python examples/custom-functions/parallel_agents.py
"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.config import load_settings


TASKS = [
	"Go to https://news.ycombinator.com/ and return only the top story title.",
	"Go to https://www.github.com/trending and return only the top repository name.",
	"Go to https://quotes.toscrape.com/ and return only the first quote and its author.",
]


async def run_one(idx: int, task: str, settings) -> None:
	# 每个 agent 用独立 BrowserSession（连同一个 Chrome）
	browser = BrowserSession(settings.browser)
	agent = Agent(
		task=task,
		llm=LLMClient(settings.llm),
		browser=browser,
		settings=settings.agent,
	)
	history = await agent.run()
	done = history.is_done()
	result = history.final_result() or "(no result)"
	print(f"[{idx}] done={done} -> {result}")


async def main() -> None:
	logging.basicConfig(level=logging.INFO)
	settings = load_settings()
	if not settings.llm.api_key:
		print("Error: 请设置 ZHIPU_API_KEY 环境变量")
		sys.exit(1)
	if not settings.browser.ws_url:
		print("Error: 请先用 chrome --remote-debugging-port=9222 启动 Chrome")
		sys.exit(1)

	await asyncio.gather(*(run_one(i, t, settings) for i, t in enumerate(TASKS)))


if __name__ == "__main__":
	asyncio.run(main())
