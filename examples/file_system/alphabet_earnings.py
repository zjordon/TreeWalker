"""Example: extract web data into a local file (ported from browser-use).

Adapted from browser-use/examples/file_system/alphabet_earnings.py.

The agent opens an Alphabet earnings-release PDF (rendered by Chrome's built-in
viewer), picks 3 interesting data points, saves them to a local file via
write_file, then reads the file back.

Adaptation notes (vs the browser-use original)
----------------------------------------------
- The original saves a ``.pdf``; TreeWalker's write_file emits plain text, so we
  save to ``.md`` instead (write_file is a text tool, not a binary/PDF encoder).
- The data is READ from the browser (Chrome's PDF viewer), not from read_file
  (which only reads local files). Whether the PDF text reaches the agent depends
  on Chrome's PDF viewer exposing the text to the DOM; if it does not, switch to
  the extract tool or an HTML report page.
- Writes are sandboxed to the workspace dir via allowed_write_paths.

Prerequisites: same as file_system.py (uv sync, Chrome on :9222, ZHIPU_API_KEY).

Usage
-----
    uv run python examples/file_system/alphabet_earnings.py
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
WORKSPACE = SCRIPT_DIR / "alphabet_earnings_workspace"
WORKSPACE.mkdir(parents=True, exist_ok=True)

TARGET = WORKSPACE / "alphabet_earnings.md"

TASK = f"""
Go to https://abc.xyz/assets/cc/27/3ada14014efbadd7a58472f1f3f4/2025q2-alphabet-earnings-release.pdf

Read the earnings release shown in the browser and pick 3 interesting data points.
Save those 3 data points to the file at: {TARGET}  (use the write_file tool)
Then read the file back with read_file and tell me its content.
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
	await agent.run()

	if TARGET.exists():
		print(f"\n--- {TARGET} ---")
		print(TARGET.read_text(encoding="utf-8"))

	input(f"\nPress Enter to clean up the workspace at {WORKSPACE} ...")
	shutil.rmtree(WORKSPACE)


if __name__ == "__main__":
	asyncio.run(main())
