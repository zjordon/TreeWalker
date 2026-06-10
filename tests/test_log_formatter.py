"""Tests for tree_walker.agent.log_formatter."""

from __future__ import annotations

import logging

from tree_walker.agent.log_formatter import (
	BLUE,
	GREEN,
	MAGENTA,
	RED,
	RESET,
	format_action_params,
	log_response,
	log_step_completion,
)


# ── format_action_params ──────────────────────────────────────────────


class TestFormatActionParams:
	def test_empty_params(self):
		assert format_action_params({}) == ""

	def test_short_params(self):
		result = format_action_params({"index": 123})
		assert "index" in result
		assert "123" in result

	def test_long_params_truncated(self):
		long_val = "x" * 200
		result = format_action_params({"path": long_val}, max_length=150)
		assert len(result) < 200
		assert "..." in result

	def test_no_colorize(self):
		result = format_action_params({"key": "val"}, colorize=False)
		assert MAGENTA not in result
		assert RESET not in result
		assert result == "key: val"

	def test_colorize_includes_magenta(self):
		result = format_action_params({"key": "val"}, colorize=True)
		assert MAGENTA in result

	def test_multiple_params(self):
		result = format_action_params(
			{"index": 5, "text": "hello"},
			colorize=False,
		)
		assert "index" in result
		assert "text" in result


# ── log_response ──────────────────────────────────────────────────────


class TestLogResponse:
	def _make_logger(self) -> tuple[logging.Logger, list[logging.LogRecord]]:
		logger = logging.getLogger("test_log_response")
		logger.setLevel(logging.DEBUG)
		records: list[logging.LogRecord] = []
		handler = logging.Handler()
		handler.emit = lambda record: records.append(record)  # type: ignore[assignment]
		logger.addHandler(handler)
		return logger, records

	def test_success_eval_green(self):
		logger, records = self._make_logger()
		log_response("Success - clicked", "", "goal", "click", {"x": 1}, 1, logger=logger)
		msg = records[0].getMessage()
		assert "👍" in msg
		assert GREEN in msg

	def test_failure_eval_red(self):
		logger, records = self._make_logger()
		log_response("Failure occurred", "", "goal", "click", {}, 1, logger=logger)
		msg = records[0].getMessage()
		assert "⚠️" in msg
		assert RED in msg

	def test_neutral_eval_no_color(self):
		logger, records = self._make_logger()
		log_response("Page loaded", "", "goal", "click", {}, 1, logger=logger)
		msg = records[0].getMessage()
		assert "❔" in msg
		assert GREEN not in msg
		assert RED not in msg

	def test_four_lines_emitted(self):
		logger, records = self._make_logger()
		log_response(
			evaluation="ok",
			memory="some memory",
			next_goal="do something",
			action_name="click",
			action_params={"index": 5},
			step=3,
			logger=logger,
		)
		assert len(records) == 4  # eval + memory + goal + action

	def test_empty_fields_omitted(self):
		logger, records = self._make_logger()
		log_response("", "", "", "done", {}, 1, logger=logger)
		# Only action line emitted (no eval, memory, goal)
		assert len(records) == 1

	def test_info_level(self):
		logger, records = self._make_logger()
		log_response("ok", "mem", "goal", "done", {}, 1, logger=logger)
		for rec in records:
			assert rec.levelno == logging.INFO


# ── log_step_completion ───────────────────────────────────────────────


class TestLogStepCompletion:
	def _make_logger(self) -> tuple[logging.Logger, list[logging.LogRecord]]:
		logger = logging.getLogger("test_step_completion")
		logger.setLevel(logging.DEBUG)
		records: list[logging.LogRecord] = []
		handler = logging.Handler()
		handler.emit = lambda record: records.append(record)  # type: ignore[assignment]
		logger.addHandler(handler)
		return logger, records

	def test_success(self):
		logger, records = self._make_logger()
		log_step_completion(5, 3.45, ok_count=1, err_count=0, logger=logger)
		msg = records[0].getMessage()
		assert "✅" in msg
		assert GREEN in msg
		assert "OK=1" in msg
		assert "ERR" not in msg

	def test_with_errors(self):
		logger, records = self._make_logger()
		log_step_completion(5, 3.45, ok_count=0, err_count=2, logger=logger)
		msg = records[0].getMessage()
		assert "❌" in msg
		assert RED in msg
		assert "ERR=2" in msg

	def test_step_number_in_output(self):
		logger, records = self._make_logger()
		log_step_completion(42, 1.0, ok_count=1, err_count=0, logger=logger)
		msg = records[0].getMessage()
		assert "Step 42" in msg
