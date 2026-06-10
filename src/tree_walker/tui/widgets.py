"""Custom Textual widgets for the TUI interface."""

from __future__ import annotations

from typing import Literal

from textual import events
from textual.message import Message
from textual.widgets import RichLog, TextArea


class AgentLog(RichLog):
	"""Auto-scrolling log panel for agent output."""

	DEFAULT_CSS = """
	AgentLog { height: 1fr; }
	"""


class MultilineInput(TextArea):
	"""Multi-line text input: Enter submits, Shift+Enter inserts newline."""

	class Submitted(Message):
		def __init__(self, text: str, input: MultilineInput) -> None:
			super().__init__()
			self.text = text
			self.input = input

	class HistoryNavigation(Message):
		def __init__(self, direction: Literal["up", "down"], input: MultilineInput) -> None:
			super().__init__()
			self.direction = direction
			self.input = input

	DEFAULT_CSS = """
	MultilineInput {
		background: transparent;
		border: none;
		padding: 0;
		min-height: 3;
		max-height: 10;
		overflow-y: hidden;
		scrollbar-size: 0 0;
	}
	MultilineInput .text-area--cursor-line {
		background: transparent;
	}
	MultilineInput:focus {
		border: none;
	}
	"""

	def __init__(self, placeholder: str = "", **kwargs):
		super().__init__(
			placeholder=placeholder,
			soft_wrap=True,
			show_line_numbers=False,
			tab_behavior="focus",
			**kwargs,
		)

	def _on_key(self, event: events.Key) -> None:
		if event.key == "enter":
			event.prevent_default()
			event.stop()
			self._submit()
		elif event.key in ("shift+enter", "ctrl+enter"):
			event.prevent_default()
			event.stop()
			self._replace_via_keyboard("\n", *self.selection)
		else:
			super()._on_key(event)

	def _submit(self) -> None:
		text = self.text
		if text.strip():
			self.post_message(self.Submitted(text, self))
		self.text = ""
		self.cursor = (0, 0)

	def _update_height(self) -> None:
		line_count = self.document.line_count
		height = max(3, min(line_count + 1, 10))
		self.styles.height = height

	def on_mount(self) -> None:
		self._update_height()

	def on_text_area_changed(self) -> None:
		self._update_height()

	def action_cursor_up(self, select: bool = False) -> None:
		row, col = self.cursor
		if row == 0 and col == 0:
			self.post_message(self.HistoryNavigation("up", self))
		else:
			super().action_cursor_up(select)

	def action_cursor_down(self, select: bool = False) -> None:
		row, col = self.cursor
		last_row = self.document.line_count - 1
		last_col = len(self.document.get_line(last_row))
		if row == last_row and col >= last_col:
			self.post_message(self.HistoryNavigation("down", self))
		else:
			super().action_cursor_down(select)
