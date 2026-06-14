"""Example: Upload a video to Bilibili Creator Center as draft.

Prerequisites:
1. Install dependencies:  uv sync
2. Start Chrome with remote debugging:
   chrome --remote-debugging-port=9222
3. Set ZHIPU_API_KEY environment variable

Usage:
    set ZHIPU_API_KEY=your_key
    python examples/upload_file_bilibili.py
"""

import asyncio
import logging
import sys

sys.path.insert(0, f"{__file__}/../src")

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.config import AgentSettings, load_settings


async def main():
	settings = load_settings()

	if not settings.llm.api_key:
		print("Error: Set ZHIPU_API_KEY environment variable")
		sys.exit(1)

	if not settings.browser.ws_url:
		print("Error: Cannot connect to Chrome. Is it running with --remote-debugging-port=9222?")
		sys.exit(1)

	llm = LLMClient(settings.llm)
	browser = BrowserSession(settings.browser)

	task = (
		"帮我到B站创作者中心发一个视频，信息如下，先暂存为草稿不要直接发布\n"
		"\n"
		"B站创作者中心网址:https://member.bilibili.com/platform/home\n"
		"我要发的视频在'D:\\Videos\\test\\final\\2026-04-29-20-41-59.mp4'\n"
		"封面图片在'D:\\dev\\git\\claude\\skills-deom\\ppt\\browser-use\\横封面.png'\n"
		"标题为：ai浏览器第五期-browse-use\n"
		"创作声明:个人观点，仅供参考\n"
		"分区:科技数码\n"
		"标签:浏览器Agent\n"
		"简介：ai浏览器第五期-browse-use"
	)

	agent_settings = AgentSettings(
		max_steps=settings.agent.max_steps,
		max_failures=settings.agent.max_failures,
		llm_timeout=settings.agent.llm_timeout,
		action_timeout=settings.agent.action_timeout,
		reconnect_timeout=settings.agent.reconnect_timeout,
		truncation=settings.agent.truncation,
		enable_planning=True,
		allowed_upload_paths=[
			r"D:\Videos\test\final\2026-04-29-20-41-59.mp4",
			r"D:\dev\git\claude\skills-deom\ppt\browser-use\横封面.png",
		],
	)

	agent = Agent(
		task=task,
		llm=llm,
		browser=browser,
		settings=agent_settings,
	)

	# 只输出 tool schema 相关的 debug 日志，其它模块保持 INFO 级别
	logging.basicConfig(level=logging.INFO)
	logging.getLogger("tree_walker.agent.step").setLevel(logging.DEBUG)

	history = await agent.run()

	if history.is_done():
		print(f"\nTask completed: {history.final_result()}")
	else:
		print("\nTask did not complete within max steps")


if __name__ == "__main__":
	asyncio.run(main())
