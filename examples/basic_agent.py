"""Example: Run the browser agent on a simple search task.

Prerequisites:
1. Install dependencies:  uv sync
2. Start Chrome with remote debugging:
   chrome --remote-debugging-port=9222
3. Set ZHIPU_API_KEY environment variable

Usage:
    ZHIPU_API_KEY=your_key python examples/basic_agent.py
"""

import asyncio
import logging
import sys

sys.path.insert(0, f"{__file__}/../src")

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.config import load_settings


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

    agent = Agent(
        task="帮我到'https://www.google.com/'搜索与'浏览器自动化'相关的信息然后获取前三条的标题告诉我",
        llm=llm,
        browser=browser,
        settings=settings.agent,
    )

    logging.basicConfig(level=logging.INFO)

    history = await agent.run()

    if history.is_done():
        print(f"\nTask completed: {history.final_result()}")
    else:
        print("\nTask did not complete within max steps")


if __name__ == "__main__":
    asyncio.run(main())
