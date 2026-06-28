"""Example: 下载文件（track_downloads）。移植自 browser-use/examples/features/download_file.py。

browser-use 用 Browser(downloads_path=...) 指定下载目录；TreeWalker 不暴露该参数——
track_downloads=True 时，CDP 的 allow 模式把下载落到用户主目录的 Downloads（即 Chrome 默认下载目录）；
可用环境变量 DOWNLOADS_PATH 指定其它目录。
开启 track_downloads=True 后，已下载文件作为 done 的附件回传；
配合 display_files_in_done_text=True 可把附件内容内联进 final_result()。
前置：uv sync / chrome --remote-debugging-port=9222 / $env:ZHIPU_API_KEY="..."
运行：uv run python examples/features/download_file.py
（下载到的文件在用户主目录的 Downloads 里查找，例如 C:/Users/<you>/Downloads；
可用 $env:DOWNLOADS_PATH="..." 改到别处）
"""
import asyncio
import logging
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.config import load_settings


# 一个稳定可下载的小文件（公共资源）；按需替换为别的可下载直链
TASK = (
	"Go to https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf "
	"and download the PDF file."
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

	agent_settings = replace(
		settings.agent,
		track_downloads=True,            # 跟踪下载 → 作为 done 附件回传
		display_files_in_done_text=True, # 把附件内容内联进 final_result()
	)
	llm = LLMClient(settings.llm)
	browser = BrowserSession(settings.browser)
	agent = Agent(task=TASK, llm=llm, browser=browser, settings=agent_settings)
	history = await agent.run()

	print("done:", history.is_done())
	result = history.final_result()
	if result:
		print("\n--- final_result ---")
		print(result)


if __name__ == "__main__":
	asyncio.run(main())
