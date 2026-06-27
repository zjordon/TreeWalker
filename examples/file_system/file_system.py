"""Example: local file read/write round-trip (ported from browser-use).

Adapted from browser-use/examples/file_system/file_system.py.

The agent navigates to a blog post, writes the article title to a local file,
appends the first sentence, then reads the file back to verify. Exercises
TreeWalker's local file tools: write_file (write + append) and read_file.

How this differs from the browser-use original
----------------------------------------------
- browser-use sandboxes file ops behind an in-memory FileSystem mounted at
  ``file_system_path``; the agent uses *relative* filenames. TreeWalker has no
  such layer — it writes *real* files to an absolute path, and gates writes
  through the ``allowed_write_paths`` whitelist (see config.AgentSettings). This
  example creates a workspace dir, points the agent at an absolute file path
  inside it, and restricts writes to that dir via ``allowed_write_paths``.
- browser-use exposes a separate ``append_file`` tool; TreeWalker folds
  appending into ``write_file`` via its ``append=True`` parameter.

Prerequisites
-------------
1. uv sync
2. Start Chrome with remote debugging:
   chrome --remote-debugging-port=9222
3. Set the API key:
   $env:ZHIPU_API_KEY = "your_key"

Usage
-----
    uv run python examples/file_system/file_system.py
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
# Workspace the agent is allowed to write into (TreeWalker's analog of
# browser-use's file_system_path). Created fresh each run; cleaned on exit.
WORKSPACE = SCRIPT_DIR / "file_system_workspace"
WORKSPACE.mkdir(parents=True, exist_ok=True)

TARGET = WORKSPACE / "data.md"

TASK = f"""
Go to https://mertunsall.github.io/posts/post1.html

1. Save the article's title to the file at: {TARGET}  (use the write_file tool)
2. Use write_file with append=True to add the first sentence of the article
   to the SAME file
3. Use read_file to read the file back and confirm the content looks correct
4. Tell me the file's final content

NOTE: the whole page is visible in the browser state — do NOT use the extract tool.
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
	# Sandbox writes to the workspace dir (prefix match). Only write_file /
	# replace_file are gated; read_file is not, so the agent can read anywhere.
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
