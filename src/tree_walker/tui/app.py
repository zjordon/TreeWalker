"""Textual TUI application for TreeWalker."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, HorizontalGroup, VerticalScroll
from textual.widgets import Footer, Header, RichLog, Static

from tree_walker.agent.agent import Agent
from tree_walker.browser.session import BrowserSession
from tree_walker.llm.client import LLMClient
from tree_walker.tui.log_handler import RichLogHandler
from tree_walker.tui.widgets import AgentLog, MultilineInput

logger = logging.getLogger(__name__)

HISTORY_FILE = Path.home() / ".treewalker" / "history.json"

TW_LOGO = r"""
  _____                    _                          _
 |_   _| __ __ _ _ __  ___| |__  _ __ ___   __ _ _ __| | __
   | || '__/ _` | '_ \/ __| '_ \| '_ ` _ \ / _` | '__| |/ /
   | || | | (_| | | | \__ \ | | | | | | | | (_| | |  |   <
   |_||_|  \__,_|_| |_|___/_| |_|_| |_| |_|\__,_|_|  |_|\_\
"""


class TreeWalkerApp(App):
	"""Interactive TUI for browser automation agent."""

	CSS = """
	#welcome-panel {
		height: 1fr;
		padding: 2 4;
		content-align: center middle;
	}
	#welcome-panel #logo { text-style: bold; color: $accent; }
	#two-column-container { height: 1fr; layout: horizontal; }
	#two-column-container > VerticalScroll { width: 1fr; }
	#task-input-container {
		dock: bottom;
		height: auto;
		padding: 0 1;
		border: solid $accent;
	}
	#task-input-container HorizontalGroup { height: auto; }
	#task-input { width: 3fr; }
	#file-paths-input { width: 1fr; border-left: solid $accent; }
	"""

	BINDINGS = [
		Binding("ctrl+q", "quit", "退出", priority=True),
		Binding("ctrl+c", "request_pause", "暂停/恢复"),
		Binding("ctrl+l", "clear_log", "清空日志"),
	]

	def __init__(
		self,
		llm: LLMClient,
		browser: BrowserSession,
		agent_settings=None,
		sensitive_data: dict[str, str] | None = None,
		initial_task: str | None = None,
		debug: bool = False,
	) -> None:
		super().__init__()
		self._llm = llm
		self._browser = browser
		self._agent_settings = agent_settings
		self._sensitive_data = sensitive_data
		self._initial_task = initial_task
		self._debug = debug
		self._agent: Agent | None = None
		self._task_history: list[str] = []
		self._history_index = 0

	def compose(self) -> ComposeResult:
		yield Header()
		yield Container(
			Static(TW_LOGO, id="logo"),
			Static("快捷键: Ctrl+Q 退出 | Ctrl+C 暂停/恢复 | Ctrl+L 清屏", id="shortcuts"),
			Static("在下方输入任务开始", id="hint"),
			id="welcome-panel",
		)
		with Container(id="two-column-container"):
			with VerticalScroll(id="main-output-column"):
				yield AgentLog(id="main-log", highlight=True, markup=True)
			with VerticalScroll(id="events-column"):
				yield RichLog(id="events-log", highlight=True, markup=True)
		with Container(id="task-input-container"):
			with HorizontalGroup():
				yield MultilineInput(
					placeholder="What would you like me to do on the web?",
					id="task-input",
				)
				yield MultilineInput(
					placeholder="File paths (one per line, optional)",
					id="file-paths-input",
				)
		yield Footer()

	def on_mount(self) -> None:
		self._setup_logging()
		self._load_history()
		self.query_one("#two-column-container").display = False
		if self._initial_task:
			self.query_one("#task-input", MultilineInput).text = self._initial_task

	async def on_multiline_input_submitted(self, event: MultilineInput.Submitted) -> None:
		"""Handle Enter key in task input."""
		if event.input.id != "task-input":
			return
		task = event.text.strip()
		if not task:
			return

		# Parse and validate file paths
		file_paths_input = self.query_one("#file-paths-input", MultilineInput)
		raw_paths = [p.strip() for p in file_paths_input.text.splitlines() if p.strip()]
		invalid_paths = [p for p in raw_paths if not os.path.exists(p)]
		if invalid_paths:
			self._log("以下文件路径不存在，任务未提交:")
			for p in invalid_paths:
				self._log(f"  - {p}")
			return
		file_paths = raw_paths if raw_paths else None

		# Save to history
		if not self._task_history or task != self._task_history[-1]:
			self._save_history(task)
		self._history_index = len(self._task_history)

		event.input.text = ""
		file_paths_input.text = ""
		self._run_task(task, available_file_paths=file_paths)

	async def on_multiline_input_history_navigation(
		self, event: MultilineInput.HistoryNavigation
	) -> None:
		"""Handle up/down arrow history navigation."""
		if event.input.id != "task-input":
			return
		if not self._task_history:
			return
		if event.direction == "up" and self._history_index > 0:
			self._history_index -= 1
		elif event.direction == "down" and self._history_index < len(self._task_history) - 1:
			self._history_index += 1
		else:
			return
		event.input.text = self._task_history[self._history_index]

	def _setup_logging(self) -> None:
		"""Redirect tree_walker.* logs to the RichLog widget."""
		main_log = self.query_one("#main-log", AgentLog)
		handler = RichLogHandler(main_log)
		handler.setFormatter(logging.Formatter("%(message)s"))

		root = logging.getLogger()
		root.handlers.clear()
		root.addHandler(handler)

		logging.getLogger("tree_walker").setLevel(logging.INFO)

		for lib in ("httpx", "anthropic", "cdp_use", "websockets"):
			logging.getLogger(lib).setLevel(logging.ERROR)

	def _setup_event_bridge(self) -> None:
		"""Connect Agent EventBus to the right-column events log."""
		if not self._agent._obs_bus:
			return
		from tree_walker.tui.event_bridge import EventBridge

		events_log = self.query_one("#events-log", RichLog)
		bridge = EventBridge(events_log)
		self._agent._obs_bus.subscribe("*", bridge.handle)

	def _log(self, msg: str) -> None:
		"""Write a message to the main log area."""
		self.query_one("#main-log", RichLog).write(msg)

	def _run_task(
		self, task: str, available_file_paths: list[str] | None = None
	) -> None:
		"""Create Agent and run in a background worker."""
		self._switch_to_working_view()

		from tree_walker.config import AgentSettings

		settings = self._agent_settings or AgentSettings()

		self._agent = Agent(
			task=task,
			llm=self._llm,
			browser=self._browser,
			settings=settings,
			sensitive_data=self._sensitive_data,
		)
		# Skip signal handler in TUI mode (use Textual key bindings instead)
		self._agent._setup_signal_handler = lambda: None
		self._setup_event_bridge()
		self.run_worker(self._agent_worker, name="agent_task")

	async def _agent_worker(self) -> None:
		"""Run the agent without blocking the UI."""
		try:
			await self._agent.run(keep_alive=True)
		except Exception as e:
			logger.error("Agent error: %s", e)
			self._log(f"Agent error: {e}")
		finally:
			self.query_one("#task-input", MultilineInput).focus()

	# ── Key binding actions ────────────────────────────────────────────

	def action_request_pause(self) -> None:
		"""Toggle agent pause/resume."""
		if not self._agent:
			return
		if self._agent.state.paused:
			self._agent.resume()
			self._log("[green]Agent 已恢复[/]")
		else:
			self._agent.pause()
			self._log("[yellow]Agent 已暂停 (Ctrl+C 恢复, 再次 Ctrl+C 停止)[/]")

	def action_clear_log(self) -> None:
		"""Clear both log areas."""
		self.query_one("#main-log", RichLog).clear()
		self.query_one("#events-log", RichLog).clear()

	# ── Welcome panel ─────────────────────────────────────────────────

	def _switch_to_working_view(self) -> None:
		"""Hide welcome panel, show the working two-column view."""
		welcome = self.query_one("#welcome-panel")
		if welcome.display:
			welcome.display = False
			self.query_one("#two-column-container").display = True

	# ── Command history persistence ───────────────────────────────────

	def _load_history(self) -> None:
		"""Load command history from ~/.treewalker/history.json."""
		if HISTORY_FILE.exists():
			try:
				self._task_history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
			except (json.JSONDecodeError, OSError):
				self._task_history = []
		self._history_index = len(self._task_history)

	def _save_history(self, task: str) -> None:
		"""Append task to history and persist to disk (max 100 entries)."""
		self._task_history.append(task)
		self._task_history = self._task_history[-100:]
		self._history_index = len(self._task_history)
		try:
			HISTORY_FILE.parent.mkdir(exist_ok=True)
			HISTORY_FILE.write_text(
				json.dumps(self._task_history, ensure_ascii=False, indent=2),
				encoding="utf-8",
			)
		except OSError as e:
			logger.warning("Failed to save history: %s", e)

	# ── Lifecycle ─────────────────────────────────────────────────────

	async def on_exit(self) -> None:
		"""Clean up browser connection on app exit."""
		if self._agent and self._agent.state and not self._agent.state.stopped:
			self._agent.stop()
		try:
			await self._browser.stop()
		except Exception as e:
			logger.warning("Failed to stop browser on exit: %s", e)
