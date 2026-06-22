"""Tests for done: terminal action echo, empty-text guard, param validation.

Covers:
- action layer: done sets is_done=True; success echoes (True/False);
  extracted_content carries the summary; long_term_memory is the compact
  'Task completed: {success} - {text[:100]}' line; logger.info is called;
  empty/whitespace text triggers warn + default-substitute (termination
  preserved, no soft-prompt); browser is unused (done touches no session)
- param model: DoneParams.text is required, min_length=1, forbids extra;
  success defaults True. Model-level tests are SYNC (the execute path does
  NOT validate, so the handler adds its own runtime guard).

Tools().execute(...) entry point, MagicMock() browser, TAB indentation per
CLAUDE.md. No tmp_path (done has no filesystem surface).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from tree_walker.tools.actions import Tools
from tree_walker.tools.models import DoneParams


async def _run(params: dict):
	"""Drive done through the public Tools().execute entry point."""
	tools = Tools()
	return await tools.execute("done", params, MagicMock())


# ── Basic terminal semantics ───────────────────────────────────────


class TestDoneBasic:
	@pytest.mark.asyncio
	async def test_done_sets_is_done_true(self):
		r = await _run({"text": "finished"})
		assert r.is_done is True

	@pytest.mark.asyncio
	async def test_done_success_default_true(self):
		r = await _run({"text": "finished"})
		assert r.success is True

	@pytest.mark.asyncio
	async def test_done_success_false_echoes(self):
		r = await _run({"text": "could not find X", "success": False})
		assert r.is_done is True
		assert r.success is False

	@pytest.mark.asyncio
	async def test_done_no_error(self):
		r = await _run({"text": "finished"})
		assert r.error is None


# ── Echo: extracted_content + long_term_memory ─────────────────────


class TestDoneEcho:
	@pytest.mark.asyncio
	async def test_extracted_content_is_text(self):
		r = await _run({"text": "The price was 42 USD"})
		assert r.extracted_content == "The price was 42 USD"

	@pytest.mark.asyncio
	async def test_long_term_memory_compact_success(self):
		r = await _run({"text": "done"})
		assert r.long_term_memory == "Task completed: True - done"

	@pytest.mark.asyncio
	async def test_long_term_memory_compact_failure(self):
		r = await _run({"text": "nope", "success": False})
		assert r.long_term_memory == "Task completed: False - nope"

	@pytest.mark.asyncio
	async def test_long_term_memory_truncates_at_100_chars(self):
		long_text = "x" * 250
		r = await _run({"text": long_text})
		assert r.long_term_memory == f"Task completed: True - {'x' * 100}"
		# extracted_content keeps the full text (no truncation)
		assert r.extracted_content == long_text

	@pytest.mark.asyncio
	async def test_logger_info_emits_memory(self, caplog):
		with caplog.at_level("INFO", logger="tree_walker.tools.actions"):
			await _run({"text": "finished"})
		assert any("Task completed: True - finished" in rec.message for rec in caplog.records)

	@pytest.mark.asyncio
	async def test_browser_not_used(self):
		"""done touches no BrowserSession method — pass a fresh mock, assert no calls."""
		browser = MagicMock()
		tools = Tools()
		await tools.execute("done", {"text": "done"}, browser)
		# No BrowserSession method should have been awaited/called.
		assert not browser.method_calls


# ── Empty / whitespace text runtime guard ──────────────────────────


class TestDoneEmptyText:
	@pytest.mark.asyncio
	async def test_empty_text_still_terminates(self):
		r = await _run({"text": ""})
		assert r.is_done is True  # termination preserved

	@pytest.mark.asyncio
	async def test_empty_text_uses_default_summary(self):
		r = await _run({"text": ""})
		assert r.extracted_content == "(no summary provided)"

	@pytest.mark.asyncio
	async def test_whitespace_text_treated_as_empty(self):
		r = await _run({"text": "   \t  "})
		assert r.extracted_content == "(no summary provided)"
		assert r.is_done is True

	@pytest.mark.asyncio
	async def test_missing_text_key_treated_as_empty(self):
		r = await _run({})
		assert r.extracted_content == "(no summary provided)"
		assert r.is_done is True

	@pytest.mark.asyncio
	async def test_empty_text_warns(self, caplog):
		with caplog.at_level("WARNING", logger="tree_walker.tools.actions"):
			await _run({"text": ""})
		assert any("empty text" in rec.message for rec in caplog.records)

	@pytest.mark.asyncio
	async def test_empty_text_memory_uses_default(self):
		r = await _run({"text": ""})
		assert r.long_term_memory == "Task completed: True - (no summary provided)"


# ── Params model validation (SYNC, direct Pydantic) ───────────────


class TestDoneParamsValidation:
	def test_text_required(self):
		with pytest.raises(ValidationError):
			DoneParams()  # type: ignore[call-arg]

	def test_text_min_length_rejects_empty(self):
		with pytest.raises(ValidationError):
			DoneParams(text="")

	def test_text_accepts_nonempty(self):
		p = DoneParams(text="ok")
		assert p.text == "ok"
		assert p.success is True  # default

	def test_success_can_be_false(self):
		p = DoneParams(text="ok", success=False)
		assert p.success is False

	def test_extra_forbidden(self):
		with pytest.raises(ValidationError):
			DoneParams(text="ok", files_to_display=[])  # type: ignore[call-arg]
