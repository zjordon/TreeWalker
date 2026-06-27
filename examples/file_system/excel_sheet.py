"""Example: build a CSV from web data (ported from browser-use).

Adapted from browser-use/examples/file_system/excel_sheet.py.

The agent looks up the current stock prices of Meta and Amazon, creates a local
CSV file (company, stock_price) via write_file, then reads it back to verify.

Adaptation notes (vs the browser-use original)
----------------------------------------------
- The original only asks the LLM to "make a CSV file"; we additionally pin an
  absolute output path, sandbox writes to the workspace dir via
  allowed_write_paths, and have the agent read the file back to confirm.

Prerequisites: same as file_system.py (uv sync, Chrome on :9222, ZHIPU_API_KEY).

Usage
-----
    uv run python examples/file_system/excel_sheet.py
"""
from __future__ import annotations

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
WORKSPACE = SCRIPT_DIR / "excel_sheet_workspace"
WORKSPACE.mkdir(parents=True, exist_ok=True)

TARGET = WORKSPACE / "stock_prices.csv"

TASK = f"""
Find the current stock prices of Meta and Amazon (search the web or visit a
finance site).

Then create a CSV file at: {TARGET}  with two columns — "company" and
"stock_price" — and one row per company (use the write_file tool).

Finally, read the file back with read_file and tell me its content.
""".strip()


async def main() -> None:
	logging.basicConfig(
		level=logging.INFO,
		format="%(asctime)s %(levelname)s %(name)s: %(message)s",
	)
	settings = load_settings()
	if not settings.llm.api_key:
		print("Error: Set ZHIPU_API_KEY environment variable")
		sys.exit(1)
	if not settings.browser.ws_url:
		print("Error: Cannot connect to Chrome. Run: chrome --remote-debugging-port=9222")
		sys.exit(1)

	llm = LLMClient(settings.llm)
	browser = BrowserSession(settings.browser)
	agent_settings = replace(settings.agent, allowed_write_paths=[str(WORKSPACE)])

	agent = Agent(task=TASK, llm=llm, browser=browser, settings=agent_settings)
	history = await agent.run()

	print(f"\nTask completed: {history.is_done()}")
	if history.final_result():
		print(f"Final result: {history.final_result()}")

	if TARGET.exists():
		print(f"\n--- {TARGET} ---")
		print(TARGET.read_text(encoding="utf-8"))

	input(f"\nPress Enter to clean up the workspace at {WORKSPACE} ...")
	shutil.rmtree(WORKSPACE)


if __name__ == "__main__":
	asyncio.run(main())
