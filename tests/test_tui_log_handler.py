"""Tests for tree_walker.tui.log_handler."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from tree_walker.tui.log_handler import RichLogHandler


class TestRichLogHandler:
	def _make_handler(self, level: int = logging.NOTSET) -> tuple[RichLogHandler, MagicMock]:
		mock_log = MagicMock()
		handler = RichLogHandler(mock_log, level=level)
		handler.setFormatter(logging.Formatter("%(message)s"))
		return handler, mock_log

	def test_emit_writes_formatted_message(self):
		handler, mock_log = self._make_handler()
		logger = logging.getLogger("test_rich_log")
		logger.addHandler(handler)
		logger.setLevel(logging.DEBUG)

		logger.info("hello world")
		mock_log.write.assert_called_once_with("hello world")

	def test_emit_preserves_ansi_colors(self):
		handler, mock_log = self._make_handler()
		logger = logging.getLogger("test_ansi")
		logger.addHandler(handler)
		logger.setLevel(logging.DEBUG)

		logger.info("\033[32mgreen text\033[0m")
		mock_log.write.assert_called_once_with("\033[32mgreen text\033[0m")

	def test_level_filtering(self):
		handler, mock_log = self._make_handler(level=logging.WARNING)
		logger = logging.getLogger("test_level")
		logger.addHandler(handler)
		logger.setLevel(logging.DEBUG)

		logger.info("should be ignored")
		logger.warning("should appear")

		assert mock_log.write.call_count == 1
		mock_log.write.assert_called_with("should appear")

	def test_emit_on_error_calls_handle_error(self):
		handler, mock_log = self._make_handler()
		mock_log.write.side_effect = RuntimeError("boom")
		handler.handleError = MagicMock()

		record = logging.LogRecord(
			name="test", level=logging.INFO, pathname="", lineno=0,
			msg="test", args=(), exc_info=None,
		)
		handler.emit(record)
		handler.handleError.assert_called_once_with(record)

	def test_custom_formatter(self):
		handler, mock_log = self._make_handler()
		handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
		logger = logging.getLogger("test_fmt")
		logger.addHandler(handler)
		logger.setLevel(logging.DEBUG)

		logger.warning("watch out")
		mock_log.write.assert_called_with("[WARNING] watch out")

	def test_multiple_records_in_order(self):
		handler, mock_log = self._make_handler()
		logger = logging.getLogger("test_order")
		logger.addHandler(handler)
		logger.setLevel(logging.DEBUG)

		logger.info("first")
		logger.info("second")
		logger.info("third")

		calls = [c.args[0] for c in mock_log.write.call_args_list]
		assert calls == ["first", "second", "third"]
