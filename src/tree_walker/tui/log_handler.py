"""Bridge Python logging to Textual RichLog widget."""

from __future__ import annotations

import logging

from textual.widgets import RichLog


class RichLogHandler(logging.Handler):
	"""Write Python log records to a Textual RichLog in real time.

	RichLog natively renders ANSI escape codes, so logs produced by
	``log_formatter.py`` (colored eval/action/completion lines) display
	correctly without any conversion.
	"""

	def __init__(
		self,
		rich_log: RichLog,
		level: int = logging.NOTSET,
	) -> None:
		super().__init__(level)
		self._rich_log = rich_log

	def emit(self, record: logging.LogRecord) -> None:
		try:
			msg = self.format(record)
			self._rich_log.write(msg)
		except Exception:
			self.handleError(record)
