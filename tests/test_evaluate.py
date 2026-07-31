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
from tree_walker.config import TruncationSettings
from tree_walker.tools.actions import Tools, _eval_long_term_memory, _extract_data_images
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
		browser.evaluate.assert_awaited_once()
		# code is forwarded verbatim as the first positional arg
		assert browser.evaluate.await_args.args == ("return 'hi'",)

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

		browser.evaluate.assert_awaited_once()
		assert browser.evaluate.await_args.args == ("return 1+1",)

	# ── 二.A: 大结果落盘 ──

	@pytest.mark.asyncio
	async def test_oversize_result_spilled_to_file(self, tmp_path):
		tools = Tools(truncation=TruncationSettings(eval_save_threshold=10, eval_output_dir=str(tmp_path)))
		browser = _make_browser(eval_return="z" * 300)

		result = await tools.execute("evaluate", {"code": "return big"}, browser)

		assert result.error is None
		files = list(tmp_path.iterdir())
		assert len(files) == 1
		assert files[0].read_text(encoding="utf-8") == "z" * 300
		assert result.extracted_content.startswith("Evaluate result (300 chars) saved to")
		assert "Preview:" in result.extracted_content
		assert result.long_term_memory.startswith("JavaScript executed successfully, result saved:")

	@pytest.mark.asyncio
	async def test_small_result_not_spilled(self, tmp_path):
		tools = Tools(truncation=TruncationSettings(eval_save_threshold=10000, eval_output_dir=str(tmp_path)))
		browser = _make_browser(eval_return="tiny")

		result = await tools.execute("evaluate", {"code": "return x"}, browser)

		assert list(tmp_path.iterdir()) == []  # below threshold → no spill
		assert result.extracted_content == "tiny"
		assert result.long_term_memory == "tiny"

	@pytest.mark.asyncio
	async def test_save_failure_is_soft(self, tmp_path, monkeypatch, caplog):
		tools = Tools(truncation=TruncationSettings(eval_save_threshold=10, eval_output_dir=str(tmp_path)))
		browser = _make_browser(eval_return="z" * 300)

		def _raise(*a, **k):
			raise OSError("disk full")
		monkeypatch.setattr("tree_walker.tools.actions.os.makedirs", _raise)

		with caplog.at_level(logging.WARNING, logger="tree_walker.tools.actions"):
			result = await tools.execute("evaluate", {"code": "return big"}, browser)

		assert result.error is None  # OSError is a soft warning, not a hard error
		assert result.extracted_content == "z" * 300  # fell back to normal echo
		assert any("save to file failed" in r.getMessage() for r in caplog.records)

	# ── 二.B: per-call 控制 ──

	@pytest.mark.asyncio
	async def test_runtime_guard_rejects_bad_timeout(self):
		browser = _make_browser()

		result = await Tools().execute("evaluate", {"code": "return 1", "timeout_ms": 0}, browser)

		assert result.error == "Evaluate failed: timeout_ms must be in [1, 300000], got 0"
		browser.evaluate.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_forwards_flags_to_session(self):
		browser = _make_browser(eval_return="ok")

		await Tools().execute("evaluate", {
			"code": "return 1",
			"await_promise": False,
			"timeout_ms": 5000,
			"user_gesture": True,
		}, browser)

		kwargs = browser.evaluate.await_args.kwargs
		assert kwargs["await_promise"] is False
		assert kwargs["timeout_ms"] == 5000
		assert kwargs["user_gesture"] is True

	# ── 二.C: args ──

	@pytest.mark.asyncio
	async def test_args_not_serializable_guard(self):
		browser = _make_browser()

		result = await Tools().execute("evaluate", {"code": "return 1", "args": [object()]}, browser)

		assert result.error.startswith("Evaluate failed: args not JSON-serializable")
		browser.evaluate.assert_not_awaited()

	# ── 二.D: 元素句柄 ──

	@pytest.mark.asyncio
	async def test_elements_guard_rejects_non_int(self):
		browser = _make_browser()

		result = await Tools().execute("evaluate", {"code": "return 1", "elements": ["x"]}, browser)

		assert result.error == "Evaluate failed: elements must be a list of ints (backend node ids)"
		browser.evaluate.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_return_element_marker_surfaces_index(self):
		browser = _make_browser(eval_return="backendNodeId:42")

		result = await Tools().execute(
			"evaluate", {"code": "return el", "return_element_ids": True}, browser,
		)

		assert result.error is None
		assert "42" in result.extracted_content
		assert "index/element_id" in result.extracted_content
		assert result.long_term_memory == "evaluate returned element index 42"

	# ── 二.F: 图片通道 ──

	@pytest.mark.asyncio
	async def test_extract_images_populates_metadata(self):
		uri = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="
		browser = _make_browser(eval_return="before " + uri + " after")

		result = await Tools().execute(
			"evaluate", {"code": "return x", "extract_images": True}, browser,
		)

		assert result.metadata == {"images": [uri]}
		assert "[image 1]" in result.extracted_content
		assert uri not in result.extracted_content

	@pytest.mark.asyncio
	async def test_extract_images_default_false(self):
		uri = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="
		browser = _make_browser(eval_return=uri)

		result = await Tools().execute("evaluate", {"code": "return x"}, browser)

		assert result.metadata is None
		assert uri in result.extracted_content  # untouched


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

	# ── 二.B–二.F: 阶段二新参数 ──

	def test_phase2_defaults(self):
		p = EvaluateParams(code="x")
		assert p.await_promise is True
		assert p.timeout_ms is None
		assert p.user_gesture is False
		assert p.args is None
		assert p.elements is None
		assert p.return_element_ids is False
		assert p.frame is None
		assert p.extract_images is False

	def test_timeout_ms_rejects_out_of_bounds(self):
		with pytest.raises(ValidationError):
			EvaluateParams(code="x", timeout_ms=0)
		with pytest.raises(ValidationError):
			EvaluateParams(code="x", timeout_ms=300001)
		# boundaries are inclusive
		assert EvaluateParams(code="x", timeout_ms=1).timeout_ms == 1
		assert EvaluateParams(code="x", timeout_ms=300000).timeout_ms == 300000

	def test_args_accepts_list_of_json(self):
		p = EvaluateParams(code="x", args=[1, "s", {"k": [2, 3]}, None, True])
		assert p.args == [1, "s", {"k": [2, 3]}, None, True]

	def test_elements_accepts_list_of_int(self):
		p = EvaluateParams(code="x", elements=[10, 20, 30])
		assert p.elements == [10, 20, 30]


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

	# ── 二.B: per-call 控制透传 ──

	@pytest.mark.asyncio
	async def test_await_promise_false_passed(self):
		s, client = self._make_session(eval_return={"result": {"value": "x"}})

		await s.evaluate("return 1", await_promise=False)

		assert client.send.Runtime.evaluate.await_args.args[0]["awaitPromise"] is False

	@pytest.mark.asyncio
	async def test_user_gesture_true_passed(self):
		s, client = self._make_session(eval_return={"result": {"value": "x"}})

		await s.evaluate("return 1", user_gesture=True)

		assert client.send.Runtime.evaluate.await_args.args[0]["userGesture"] is True

	@pytest.mark.asyncio
	async def test_custom_timeout_used(self):
		s, client = self._make_session(eval_return={"result": {"value": "x"}})

		await s.evaluate("return 1", timeout_ms=5000)

		assert client.send.Runtime.evaluate.await_args.args[0]["timeout"] == 5000

	# callFunctionOn-path helper (二.C / 二.D)
	def _make_callfn_session(
		self,
		*,
		callfn_return: dict | None = None,
		host_oid: str = "doc-oid",
		elem_oids: list[str] | None = None,
	) -> tuple[BrowserSession, MagicMock]:
		"""Stub session for the callFunctionOn path: DOM.getDocument + DOM.resolveNode
		(element handles first, then the document host) + Runtime.callFunctionOn."""
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid-1"
		client = MagicMock()
		client.send.DOM.getDocument = AsyncMock(return_value={"root": {"nodeId": 1}})
		oids = (elem_oids or []) + [host_oid]
		client.send.DOM.resolveNode = AsyncMock(
			side_effect=[{"object": {"objectId": o}} for o in oids],
		)
		client.send.Runtime.callFunctionOn = AsyncMock(
			return_value=callfn_return if callfn_return is not None else {"result": {"value": "ok"}},
		)
		# a real AsyncMock (never awaited on the callFunctionOn path) so assert_not_awaited works
		client.send.Runtime.evaluate = AsyncMock()
		s.client = client
		return s, client

	# ── 二.C: args → callFunctionOn（document host，CDP marshaling） ──

	@pytest.mark.asyncio
	async def test_args_uses_call_function_on(self):
		s, client = self._make_callfn_session(callfn_return={"result": {"value": 3}})

		text = await s.evaluate("return a[0]+a[1]", args=[1, "x"])

		assert text == "3"
		client.send.Runtime.evaluate.assert_not_awaited()
		client.send.Runtime.callFunctionOn.assert_awaited_once()
		params = client.send.Runtime.callFunctionOn.await_args.args[0]
		assert params["arguments"] == [{"value": 1}, {"value": "x"}]
		assert params["functionDeclaration"].startswith("function(...a){")
		assert params["returnByValue"] is True
		assert client.send.Runtime.callFunctionOn.await_args.kwargs == {"session_id": "sid-1"}

	@pytest.mark.asyncio
	async def test_no_args_uses_runtime_evaluate(self):
		# regression: no args → original Runtime.evaluate path, NOT callFunctionOn
		s, client = self._make_session(eval_return={"result": {"value": "ok"}})

		await s.evaluate("return 1")

		client.send.Runtime.evaluate.assert_awaited_once()
		# callFunctionOn must not even be an awaited AsyncMock here (client is a plain MagicMock
		# attribute until set); assert the args path was not taken by checking evaluate was used
		assert client.send.Runtime.evaluate.await_args.kwargs == {"session_id": "sid-1"}

	@pytest.mark.asyncio
	async def test_args_exception_details_raises_rich_error(self):
		s, _ = self._make_callfn_session(callfn_return={
			"exceptionDetails": {
				"text": "TypeError: bad",
				"exception": {"description": "TypeError: bad\n    at x"},
			},
		})

		with pytest.raises(RuntimeError) as ei:
			await s.evaluate("return a[0]", args=[1])

		msg = str(ei.value)
		assert "TypeError: bad" in msg
		assert "at x" in msg
		assert "Validated code" in msg

	# ── 二.D: 元素句柄 IN + OUT ──

	@pytest.mark.asyncio
	async def test_elements_resolved_to_object_ids(self):
		s, client = self._make_callfn_session(
			callfn_return={"result": {"value": "v"}}, elem_oids=["e1", "e2"],
		)

		await s.evaluate("return e[0].value", elements=[10, 20])

		params = client.send.Runtime.callFunctionOn.await_args.args[0]
		assert params["arguments"] == [{"objectId": "e1"}, {"objectId": "e2"}]
		assert params["functionDeclaration"].startswith("function(...a, ...e){")

	@pytest.mark.asyncio
	async def test_args_then_elements_order(self):
		s, client = self._make_callfn_session(
			callfn_return={"result": {"value": 1}}, elem_oids=["e1"],
		)

		await s.evaluate("return f(a[0], e[0])", args=[1], elements=[2])

		params = client.send.Runtime.callFunctionOn.await_args.args[0]
		# JSON args first, element handles last
		assert params["arguments"] == [{"value": 1}, {"objectId": "e1"}]

	@pytest.mark.asyncio
	async def test_return_element_id_resolves_node(self):
		s, client = self._make_callfn_session(callfn_return={
			"result": {"type": "object", "subtype": "node", "objectId": "node-oid"},
		})
		client.send.DOM.describeNode = AsyncMock(return_value={"node": {"backendNodeId": 5}})

		text = await s.evaluate(
			"return document.querySelector('form')", args=[1], return_element_ids=True,
		)

		assert text == "backendNodeId:5"
		params = client.send.Runtime.callFunctionOn.await_args.args[0]
		assert params["returnByValue"] is False
		client.send.DOM.describeNode.assert_awaited_once()
		assert client.send.DOM.describeNode.await_args.args[0] == {"objectId": "node-oid"}

	@pytest.mark.asyncio
	async def test_return_element_id_non_node_falls_back(self):
		s, client = self._make_callfn_session(
			callfn_return={"result": {"value": 42}}, elem_oids=["e1"],
		)
		client.send.DOM.describeNode = AsyncMock()

		text = await s.evaluate("return e[0].value", elements=[2], return_element_ids=True)

		assert text == "42"
		client.send.DOM.describeNode.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_return_element_id_uses_runtime_evaluate_when_no_inputs(self):
		s, client = self._make_session(eval_return={
			"result": {"type": "object", "subtype": "node", "objectId": "n"},
		})
		client.send.DOM.describeNode = AsyncMock(return_value={"node": {"backendNodeId": 7}})

		text = await s.evaluate("return document.querySelector('a')", return_element_ids=True)

		assert text == "backendNodeId:7"
		assert client.send.Runtime.evaluate.await_args.args[0]["returnByValue"] is False

	# ── 二.E: iframe 执行上下文 ──

	@pytest.mark.asyncio
	async def test_frame_attaches_to_iframe_target(self, monkeypatch):
		s, client = self._make_session(eval_return={"result": {"value": "iframe-title"}})
		client.send.DOM.resolveNode = AsyncMock(return_value={"object": {"objectId": "ifr-oid"}})
		client.send.DOM.describeNode = AsyncMock(return_value={"node": {"frameId": "FRAME_A"}})
		import tree_walker.browser.session as sess_mod
		monkeypatch.setattr(
			sess_mod, "build_frame_target_map",
			AsyncMock(return_value=({"FRAME_A": "TGT_A"}, {})),
		)
		attach = AsyncMock(return_value="iframe-sid")
		monkeypatch.setattr(sess_mod, "attach_to_iframe_target", attach)

		text = await s.evaluate("return document.title", frame=99)

		assert text == "iframe-title"
		# the eval ran in the iframe session, not the base one
		assert client.send.Runtime.evaluate.await_args.kwargs == {"session_id": "iframe-sid"}
		attach.assert_awaited_once_with(s.client, "TGT_A")

	@pytest.mark.asyncio
	async def test_frame_missing_target_is_error(self, monkeypatch):
		s, client = self._make_session()
		client.send.DOM.resolveNode = AsyncMock(return_value={"object": {"objectId": "ifr-oid"}})
		client.send.DOM.describeNode = AsyncMock(return_value={"node": {"frameId": "FRAME_X"}})
		import tree_walker.browser.session as sess_mod
		monkeypatch.setattr(sess_mod, "build_frame_target_map", AsyncMock(return_value=({}, {})))
		monkeypatch.setattr(sess_mod, "attach_to_iframe_target", AsyncMock())

		with pytest.raises(RuntimeError, match="could not resolve iframe target"):
			await s.evaluate("return 1", frame=99)


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


# ── _extract_data_images (阶段二 二.F) ────────────────────────────────────────


class TestExtractDataImages:
	def test_finds_single_data_uri(self):
		uri = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="
		_text, images = _extract_data_images("pre " + uri + " post")
		assert images == [uri]

	def test_replaces_with_placeholder(self):
		uri = "data:image/jpeg;base64,/9j/4AAQ=="
		text, _images = _extract_data_images(uri)
		assert text == "[image 1]"

	def test_multiple_images_numbered(self):
		u1 = "data:image/png;base64,AAAA"
		u2 = "data:image/gif;base64,BBBB"
		text, images = _extract_data_images(u1 + " " + u2)
		assert images == [u1, u2]
		assert text == "[image 1] [image 2]"

	def test_no_image_unchanged(self):
		s = "just plain text, no images here"
		text, images = _extract_data_images(s)
		assert text == s
		assert images == []

	def test_preserves_surrounding_text(self):
		# realistic: data URIs are delimited by quotes / whitespace, not bare letters
		uri = "data:image/png;base64,iVBORw0KGgo="
		text, images = _extract_data_images("pre " + uri + " post")
		assert text == "pre [image 1] post"
		assert images == [uri]
