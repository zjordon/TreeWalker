"""Tests for evaluate: arbitrary user JS via a single Runtime.evaluate.

Covers:
- action layer: a primitive result is echoed into extracted_content with a
  compact long_term_memory; a long result is truncated to eval_result_max_chars
  and summarized in memory; a raised evaluate returns error="Evaluate failed:
  ..."; the action does NOT call get_state and forwards code verbatim
- param model: ``EvaluateParams.code`` is required, forbids extra fields, and
  accepts arbitrary (long / special-char) code (no max_length)
- session layer: ``BrowserSession.evaluate`` runs one ``Runtime.evaluate`` with
  ``returnByValue=True`` / ``awaitPromise=True`` / ``timeout=30000``; preprocesses
  the code (fix ``\"``); normalizes dict->JSON and undefined/null/bool to JS
  literals; ``exceptionDetails`` raises a ``RuntimeError`` carrying text +
  description + code snippet; ``wasThrown`` raises
- pure functions: ``_validate_and_fix_javascript`` applies all 6 regex fixes
  and is idempotent on clean code; ``_normalize_eval_result`` covers every value
  type; ``_format_eval_exception`` surfaces the description + code snippet
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from tree_walker.browser.session import (
	BrowserSession,
	_format_eval_exception,
	_normalize_eval_result,
	_validate_and_fix_javascript,
)
from tree_walker.tools.actions import Tools, _eval_long_term_memory
from tree_walker.tools.models import EvaluateParams


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_browser(
	*, eval_return: str | None = None, eval_raises: Exception | None = None,
) -> MagicMock:
	"""Stub BrowserSession: evaluate returns a normalized string (or raises).

	get_state is an AsyncMock purely so we can assert it is NOT awaited.
	"""
	bs = MagicMock()
	if eval_raises:
		bs.evaluate = AsyncMock(side_effect=eval_raises)
	else:
		bs.evaluate = AsyncMock(return_value=eval_return if eval_return is not None else "ok")
	bs.get_state = AsyncMock()
	return bs


# ── action layer ─────────────────────────────────────────────────────────────


class TestEvaluateAction:
	@pytest.mark.asyncio
	async def test_primitive_result_echoed(self):
		browser = _make_browser(eval_return="hello")

		result = await Tools().execute("evaluate", {"code": "return 'hi'"}, browser)

		assert result.error is None
		assert result.is_done is False
		assert result.extracted_content == "hello"
		# short result echoed verbatim into long_term_memory
		assert result.long_term_memory == "hello"
		browser.evaluate.assert_awaited_once_with("return 'hi'")

	@pytest.mark.asyncio
	async def test_long_result_truncated_and_summarized(self):
		# eval_result_max_chars defaults to 2000 (TruncationSettings)
		browser = _make_browser(eval_return="x" * 3000)

		result = await Tools().execute("evaluate", {"code": "return big"}, browser)

		assert result.error is None
		assert result.extracted_content == "x" * 2000  # truncated
		# long result collapses to a length-only summary, NOT echoed verbatim
		assert result.long_term_memory == "JavaScript executed successfully, result length: 3000 characters."

	@pytest.mark.asyncio
	async def test_js_exception_is_hard_error(self, caplog):
		browser = _make_browser(eval_raises=RuntimeError("JavaScript execution error: boom"))

		with caplog.at_level(logging.WARNING, logger="tree_walker.tools.actions"):
			result = await Tools().execute("evaluate", {"code": "throw 1"}, browser)

		assert result.error == "Evaluate failed: JavaScript execution error: boom"
		assert result.extracted_content is None
		assert result.is_done is False
		assert any(
			"evaluate(" in r.getMessage() and "failed" in r.getMessage() for r in caplog.records
		)

	@pytest.mark.asyncio
	async def test_does_not_call_get_state(self):
		browser = _make_browser()

		await Tools().execute("evaluate", {"code": "return 1"}, browser)

		browser.get_state.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_forwards_code_verbatim(self):
		# preprocessing lives in the session layer; the action passes code through
		browser = _make_browser()

		await Tools().execute("evaluate", {"code": "return 1+1"}, browser)

		browser.evaluate.assert_awaited_once_with("return 1+1")


# ── param model ──────────────────────────────────────────────────────────────


class TestEvaluateParams:
	def test_code_required(self):
		with pytest.raises(ValidationError):
			EvaluateParams()

	def test_extra_field_forbidden(self):
		with pytest.raises(ValidationError):
			EvaluateParams(code="x", timeout=5000)

	def test_accepts_arbitrary_code(self):
		# no max_length: long / special-char code passes
		long_code = "return " + "1+" * 1000 + "1"
		p = EvaluateParams(code=long_code)
		assert p.code == long_code
		special = 'const s="a\\"b\\nc"; return s;'
		assert EvaluateParams(code=special).code == special


# ── session layer ────────────────────────────────────────────────────────────


class TestEvaluateSession:
	def _make_session(self, *, eval_return: dict | None = None) -> tuple[BrowserSession, MagicMock]:
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid-1"
		client = MagicMock()
		client.send.Runtime.evaluate = AsyncMock(
			return_value=eval_return if eval_return is not None else {"result": {"value": "ok"}},
		)
		s.client = client
		return s, client

	@pytest.mark.asyncio
	async def test_runs_one_runtime_evaluate_with_flags(self):
		s, client = self._make_session(eval_return={"result": {"value": "hello"}})

		text = await s.evaluate("return 'hi'")

		assert text == "hello"
		client.send.Runtime.evaluate.assert_awaited_once()
		params = client.send.Runtime.evaluate.await_args.args[0]
		assert params["returnByValue"] is True
		assert params["awaitPromise"] is True
		assert params["timeout"] == 30000
		assert client.send.Runtime.evaluate.await_args.kwargs == {"session_id": "sid-1"}

	@pytest.mark.asyncio
	async def test_preprocessing_applied(self):
		# input has \"; the expression sent to CDP has it undone to "
		s, client = self._make_session(eval_return={"result": {"value": "x"}})

		await s.evaluate('a\\"b')

		params = client.send.Runtime.evaluate.await_args.args[0]
		assert params["expression"] == 'a"b'

	@pytest.mark.asyncio
	async def test_normalize_dict_to_json(self):
		s, _ = self._make_session(eval_return={"result": {"value": {"a": 1, "b": [1, 2]}}})

		assert await s.evaluate("x") == '{"a": 1, "b": [1, 2]}'

	@pytest.mark.asyncio
	async def test_normalize_undefined(self):
		# CDP omits `value` when the expression returned undefined
		s, _ = self._make_session(eval_return={"result": {"type": "undefined"}})

		assert await s.evaluate("x") == "undefined"

	@pytest.mark.asyncio
	async def test_normalize_primitive(self):
		s, _ = self._make_session(eval_return={"result": {"value": 42}})

		assert await s.evaluate("x") == "42"

	@pytest.mark.asyncio
	async def test_exception_details_raises_rich_error(self):
		s, _ = self._make_session(
			eval_return={
				"exceptionDetails": {
					"text": "ReferenceError: x is not defined",
					"exception": {"description": "ReferenceError: x is not defined\n    at foo (1:1)"},
				},
			},
		)

		with pytest.raises(RuntimeError) as ei:
			await s.evaluate("x")
		msg = str(ei.value)
		assert "ReferenceError: x is not defined" in msg
		assert "at foo (1:1)" in msg  # description (with stack) surfaced
		assert "Validated code" in msg

	@pytest.mark.asyncio
	async def test_was_thrown_raises(self):
		s, _ = self._make_session(eval_return={"result": {"wasThrown": True}})

		with pytest.raises(RuntimeError, match="wasThrown"):
			await s.evaluate("x")


# ── pure functions (preprocessor / normalizer / exception formatter) ─────────


class TestEvaluatePreprocessorAndNormalizer:
	# --- _validate_and_fix_javascript ---

	def test_fix_double_escaped_quotes(self):
		assert _validate_and_fix_javascript('a\\"b') == 'a"b'

	def test_fix_over_escaped_regex(self):
		# literal \\d -> \d ; \\[ -> \[
		assert _validate_and_fix_javascript(r'pattern = \\d+') == r'pattern = \d+'
		assert _validate_and_fix_javascript(r're = "\\["') == r're = "\["'

	def test_fix_queryselector_mixed_quotes(self):
		assert _validate_and_fix_javascript('document.querySelector("a.link")') == 'document.querySelector(`a.link`)'
		assert _validate_and_fix_javascript('document.querySelectorAll("div.x")') == 'document.querySelectorAll(`div.x`)'

	def test_fix_document_evaluate_quotes(self):
		assert _validate_and_fix_javascript('document.evaluate("//div", document)') == 'document.evaluate(`//div`, document)'

	def test_fix_closest_and_matches_quotes(self):
		assert _validate_and_fix_javascript('el.closest(".btn")') == 'el.closest(`.btn`)'
		assert _validate_and_fix_javascript('el.matches(".active")') == 'el.matches(`.active`)'

	def test_idempotent_on_clean_code(self):
		clean = "return 1 + 2"
		assert _validate_and_fix_javascript(clean) == clean

	# --- _normalize_eval_result ---

	def test_normalize_dict(self):
		assert _normalize_eval_result({"value": {"a": 1}}) == '{"a": 1}'

	def test_normalize_list(self):
		assert _normalize_eval_result({"value": [1, 2, 3]}) == '[1, 2, 3]'

	def test_normalize_undefined(self):
		assert _normalize_eval_result({"type": "undefined"}) == "undefined"
		assert _normalize_eval_result({}) == "undefined"

	def test_normalize_null(self):
		assert _normalize_eval_result({"value": None}) == "null"

	def test_normalize_bool(self):
		assert _normalize_eval_result({"value": True}) == "true"
		assert _normalize_eval_result({"value": False}) == "false"

	def test_normalize_number_and_str(self):
		assert _normalize_eval_result({"value": 42}) == "42"
		assert _normalize_eval_result({"value": 3.14}) == "3.14"
		assert _normalize_eval_result({"value": "hi"}) == "hi"

	# --- _format_eval_exception ---

	def test_format_exception_surfaces_description_and_snippet(self):
		msg = _format_eval_exception(
			{"text": "Error: boom", "exception": {"description": "Error: boom\n    at x"}},
			"return bad",
		)
		assert "JavaScript execution error: Error: boom" in msg
		assert "at x" in msg
		assert "Validated code" in msg
		assert "return bad" in msg

	def test_format_exception_omits_duplicate_description(self):
		# description == text -> not duplicated as a separate block
		msg = _format_eval_exception({"text": "E", "exception": {"description": "E"}}, "c")
		assert msg == "JavaScript execution error: E\nValidated code (after quote fixing):\nc"

	def test_format_exception_truncates_long_code(self):
		long_code = "x" * 1000
		msg = _format_eval_exception({"text": "E"}, long_code)
		assert "..." in msg
		assert "x" * 600 not in msg  # snippet capped at 500

	# --- _eval_long_term_memory ---

	def test_memory_echoes_short_result(self):
		assert _eval_long_term_memory("short") == "short"

	def test_memory_summarizes_long_result(self):
		long_text = "y" * 500
		assert _eval_long_term_memory(long_text) == (
			f"JavaScript executed successfully, result length: {len(long_text)} characters."
		)
