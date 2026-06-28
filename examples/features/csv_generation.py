"""Example: 生成 CSV。移植自 browser-use/examples/features/csv_file_generation.py。

browser-use 用内置 agent file system + 自动 CSV 规范化（agent.file_system.get_file）；
TreeWalker 没有 agent file system，改为让 agent 用 write_file 工具把结构化数据
写成 CSV 到一个工作区目录（受 allowed_write_paths 白名单约束）。
仿现有 examples/file_system/file_system.py 的 workspace 模式。
前置：uv sync / chrome --remote-debugging-port=9222 / $env:ZHIPU_API_KEY="..."
运行：uv run python examples/features/csv_generation.py
"""
import asyncio
import logging
import shutil
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.config import load_settings

SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE = SCRIPT_DIR / "csv_workspace"   # 受 allowed_write_paths 约束的工作区，每次新建、退出清理
WORKSPACE.mkdir(parents=True, exist_ok=True)
TARGET = WORKSPACE / "top_cities.csv"

TASK = f"""Go to https://en.wikipedia.org/wiki/List_of_largest_cities and collect the top 10 cities by population.
Then use the write_file tool to save a CSV (columns: rank,city,population) to: {TARGET}
After saving, read the file back with read_file to verify it looks correct, then tell me the file path.""".strip()


async def main() -> None:
	logging.basicConfig(level=logging.INFO)
	settings = load_settings()
	if not settings.llm.api_key:
		print("Error: 请设置 ZHIPU_API_KEY 环境变量")
		sys.exit(1)
	if not settings.browser.ws_url:
		print("Error: 请先用 chrome --remote-debugging-port=9222 启动 Chrome")
		sys.exit(1)

	# write_file/replace_file 受 allowed_write_paths 白名单约束（前缀匹配）
	agent_settings = replace(settings.agent, allowed_write_paths=[str(WORKSPACE)])
	llm = LLMClient(settings.llm)
	browser = BrowserSession(settings.browser)
	agent = Agent(task=TASK, llm=llm, browser=browser, settings=agent_settings)
	history = await agent.run()

	print("done:", history.is_done())
	if TARGET.exists():
		print(f"\n--- {TARGET} ---")
		print(TARGET.read_text(encoding="utf-8")[:1000])

	input(f"\n按 Enter 清理工作区 {WORKSPACE} ...")
	shutil.rmtree(WORKSPACE)


if __name__ == "__main__":
	asyncio.run(main())
