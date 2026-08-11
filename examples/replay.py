"""Example: 重放录制好的历史文件（只重放，不录制、不变量替换）。

前置：

    1. uv sync
    2. Chrome 以远程调试端口启动（建议用录制时的 profile，已登录目标站点）：

       chrome --remote-debugging-port=9223 --user-data-dir=<profile>

    3. 录制产物（``recorded.json`` 等）已落 ``rerun-history/``

运行：

    # 默认重放 rerun-history/recorded.json
    uv run python examples/replay.py

    # 指定文件名（相对 rerun-history/，绝对路径会被拒绝）
    uv run python examples/replay.py myflow.json

录制见 ``examples/record_user_actions.py``；重放机制详见 ``docs/user_recording/README.md``。
"""

import argparse
import asyncio
import logging
import sys

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.config import _fetch_ws_url, load_settings


async def main() -> None:
	parser = argparse.ArgumentParser(description="重放录制好的历史文件（只重放）")
	parser.add_argument(
		"history_file",
		nargs="?",
		default="recorded.json",
		help="重放文件名（相对 rerun-history/），默认 recorded.json",
	)
	args = parser.parse_args()

	settings = load_settings()
	ws_url = _fetch_ws_url("127.0.0.1", 9223)
	if not ws_url:
		print("✗ Chrome 未以 --remote-debugging-port=9223 启动")
		sys.exit(1)

	# 重放本身不调决策 LLM；LLMClient 仅用于结尾 AI 摘要（失败有 fallback，不阻塞）
	llm = LLMClient(settings.llm)
	browser = BrowserSession(ws_url=ws_url)
	logging.basicConfig(level=logging.INFO)

	agent = Agent(task="", llm=llm, browser=browser, settings=settings.agent)
	print(f"=== 重放 {args.history_file} ===")
	results = await agent.load_and_rerun(args.history_file)

	print(f"\n✓ 重放完成（{len(results)} 条结果）")
	summary = results[-1] if results else None
	if summary and summary.is_done:
		print(f"  Success: {summary.success}")
		if summary.extracted_content:
			print(f"  {summary.extracted_content}")


if __name__ == "__main__":
	asyncio.run(main())
