"""Example: 把网页存成 PDF。移植自 browser-use/examples/features/save_as_pdf.py。

任务驱动，让 agent 调用内置 save_as_pdf 动作把页面存为 PDF。
注意：save_as_pdf 写盘路径不受 allowed_write_paths 约束
（白名单只作用于 write_file/replace_file/read_file）。
前置：uv sync / chrome --remote-debugging-port=9222 / $env:ZHIPU_API_KEY="..."
运行：uv run python examples/features/save_as_pdf.py
"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.config import load_settings


OUTPUT = "C:/tmp/browser_automation.pdf"   # 按需改成你的输出路径
TASK = (
	"Go to https://en.wikipedia.org/wiki/Browser_automation and use the save_as_pdf "
	f"action to save the whole page as a PDF to {OUTPUT}."
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

	print("done:", history.is_done(), "| pdf:", OUTPUT)


if __name__ == "__main__":
	asyncio.run(main())
