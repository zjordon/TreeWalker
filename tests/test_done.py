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
CLAUDE.md. tmp_path for attachments/inline (二.B/二.D); variant B via
Tools(output_model=...) (二.E); downloads via _attach_downloads_to_done_results
(二.C).
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, ValidationError

from tree_walker.agent.step import StepPipeline, _attach_downloads_to_done_results
from tree_walker.agent.views import ActionResult, DownloadInfo
from tree_walker.tools.actions import Tools
from tree_walker.tools.models import DoneParams


async def _run(params: dict, tools: Tools | None = None):
	"""Drive done through the public Tools().execute entry point."""
	tools = tools or Tools()
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
		# 二.A: >100 字追加 " - N more characters" 后缀
		assert r.long_term_memory == f"Task completed: True - {'x' * 100} - 150 more characters"
		# extracted_content keeps the full text (no truncation)
		assert r.extracted_content == long_text

	@pytest.mark.asyncio
	async def test_short_text_no_more_chars_suffix(self):
		r = await _run({"text": "short summary"})
		assert "more characters" not in r.long_term_memory

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
	async def test_missing_text_defaults_to_honest_failure(self):
		# review5 #6：text/data 均缺失的 done（畸形归一化擦成 {} 的路径）默认
		# success=False——第四轮 #1 假成功修复的锚点（此前回退该修复零测试失败）。
		# 对照：显式空文本（下一测试）与变体 B 有 data（test_structured_
		# serializes_data 断言 success True）均维持 True。
		r = await _run({})
		assert r.success is False

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
			DoneParams(text="ok", not_a_real_field=1)  # type: ignore[call-arg]

	def test_files_to_display_defaults_empty(self):
		p = DoneParams(text="ok")
		assert p.files_to_display == []


# ── 二.B attachments (files_to_display → attachments) ──────────────


class TestDoneAttachments:
	@pytest.mark.asyncio
	async def test_files_to_display_attached_as_absolute(self, tmp_path):
		f = tmp_path / "a.txt"
		f.write_text("hi", encoding="utf-8")
		r = await _run({"text": "ok", "files_to_display": [str(f)]})
		assert r.attachments == [str(f)]
		assert "Attachments: a.txt" in r.extracted_content
		assert r.is_done is True

	@pytest.mark.asyncio
	async def test_relative_path_resolved_to_absolute(self, tmp_path, monkeypatch):
		f = tmp_path / "rel.txt"
		f.write_text("x", encoding="utf-8")
		monkeypatch.chdir(tmp_path)
		r = await _run({"text": "ok", "files_to_display": ["rel.txt"]})
		assert r.attachments == [str(f)]

	@pytest.mark.asyncio
	async def test_attachment_outside_allowlist_skipped(self, tmp_path, caplog):
		safe = tmp_path / "safe"
		safe.mkdir()
		inside = safe / "in.txt"
		inside.write_text("x", encoding="utf-8")
		outside = tmp_path / "out.txt"
		outside.write_text("x", encoding="utf-8")
		tools = Tools(allowed_read_paths=[str(safe)])
		with caplog.at_level("WARNING", logger="tree_walker.tools.actions"):
			r = await _run({"text": "ok", "files_to_display": [str(inside), str(outside)]}, tools)
		assert str(inside) in (r.attachments or [])
		assert str(outside) not in (r.attachments or [])
		assert any("allowed_read_paths" in rec.message for rec in caplog.records)
		assert r.is_done is True

	@pytest.mark.asyncio
	async def test_missing_attachment_skipped(self, tmp_path, caplog):
		missing = tmp_path / "nope.txt"
		with caplog.at_level("WARNING", logger="tree_walker.tools.actions"):
			r = await _run({"text": "ok", "files_to_display": [str(missing)]})
		assert r.attachments in (None, [])
		assert any("missing attachment" in rec.message for rec in caplog.records)
		assert r.is_done is True

	@pytest.mark.asyncio
	async def test_no_files_to_display_empty(self):
		r = await _run({"text": "ok"})
		assert r.attachments in (None, [])
		assert "Attachments:" not in (r.extracted_content or "")


# ── 二.C downloads auto-attach (pure helper) ───────────────────────


class TestDoneDownloads:
	def test_attach_downloads_to_done_result(self):
		r = ActionResult(is_done=True, success=True, extracted_content="d")
		_attach_downloads_to_done_results(
			[r], [DownloadInfo(filename="a.csv", url="u", path="/tmp/a.csv")],
		)
		assert r.attachments == ["/tmp/a.csv"]

	def test_attach_downloads_skips_non_done(self):
		done = ActionResult(is_done=True, success=True, extracted_content="d")
		other = ActionResult(extracted_content="x")
		_attach_downloads_to_done_results(
			[other, done], [DownloadInfo(filename="a.csv", url="u", path="/tmp/a.csv")],
		)
		assert done.attachments == ["/tmp/a.csv"]
		assert other.attachments is None

	def test_attach_downloads_dedup(self):
		r = ActionResult(is_done=True, success=True, extracted_content="d",
		                 attachments=["/tmp/a.csv"])
		_attach_downloads_to_done_results(
			[r],
			[DownloadInfo(filename="a.csv", url="u", path="/tmp/a.csv"),
			 DownloadInfo(filename="b.csv", url="v", path="/tmp/b.csv")],
		)
		assert r.attachments == ["/tmp/a.csv", "/tmp/b.csv"]

	def test_attach_downloads_skips_missing_path(self):
		r = ActionResult(is_done=True, success=True, extracted_content="d")
		_attach_downloads_to_done_results(
			[r], [DownloadInfo(filename="a", url="u", path=None)],
		)
		assert r.attachments is None


# ── 二.D display_files_in_done_text inline switch ──────────────────


class TestDoneInlineAttachments:
	@pytest.mark.asyncio
	async def test_inline_off_by_default(self, tmp_path):
		f = tmp_path / "a.txt"
		f.write_text("hello", encoding="utf-8")
		r = await _run({"text": "ok", "files_to_display": [str(f)]})
		assert "--- " not in (r.extracted_content or "")

	@pytest.mark.asyncio
	async def test_inline_on_embeds_content(self, tmp_path):
		f = tmp_path / "a.txt"
		f.write_text("hello world", encoding="utf-8")
		tools = Tools(display_files_in_done_text=True)
		r = await _run({"text": "ok", "files_to_display": [str(f)]}, tools)
		assert f"--- {f} ---" in r.extracted_content
		assert "hello world" in r.extracted_content

	@pytest.mark.asyncio
	async def test_inline_caps_large_file(self, tmp_path):
		f = tmp_path / "big.txt"
		f.write_text("y" * 5000, encoding="utf-8")
		tools = Tools(display_files_in_done_text=True)
		tools._truncation.done_attachment_max_chars = 100
		r = await _run({"text": "ok", "files_to_display": [str(f)]}, tools)
		assert "y" * 100 in r.extracted_content
		assert "y" * 101 not in r.extracted_content

	@pytest.mark.asyncio
	async def test_inline_read_failure_skipped(self, tmp_path, caplog, monkeypatch):
		f = tmp_path / "a.txt"
		f.write_text("hi", encoding="utf-8")
		tools = Tools(display_files_in_done_text=True)
		import builtins
		real_open = builtins.open

		def _boom(path, *a, **k):
			if str(path) == str(f):
				raise OSError("boom")
			return real_open(path, *a, **k)

		monkeypatch.setattr(builtins, "open", _boom)
		with caplog.at_level("WARNING", logger="tree_walker.tools.actions"):
			r = await _run({"text": "ok", "files_to_display": [str(f)]}, tools)
		assert r.is_done is True
		assert any("skip inline read" in rec.message for rec in caplog.records)


# ── 二.E structured output (output_model / variant B) ──────────────


class _StructOut(BaseModel):
	name: str
	count: int = 0


class TestDoneStructuredOutput:
	@pytest.mark.asyncio
	async def test_structured_serializes_data(self):
		tools = Tools(output_model=_StructOut)
		r = await _run({"data": {"name": "a", "count": 3}}, tools)
		assert r.is_done is True
		assert r.success is True
		assert json.loads(r.extracted_content) == {"name": "a", "count": 3}
		assert r.metadata == {"structured_output": {"name": "a", "count": 3}}

	@pytest.mark.asyncio
	async def test_structured_invalid_data_falls_back(self):
		tools = Tools(output_model=_StructOut)
		r = await _run({"data": {"count": 5}}, tools)  # missing required 'name'
		assert r.is_done is True
		assert r.success is False
		assert "invalid structured output" in r.extracted_content

	@pytest.mark.asyncio
	async def test_structured_missing_data_key_falls_back(self):
		tools = Tools(output_model=_StructOut)
		r = await _run({}, tools)  # no 'data' key → KeyError path
		assert r.is_done is True
		assert r.success is False

	def test_structured_descriptions_hide_internal_fields(self):
		tools = Tools(output_model=_StructOut)
		text = tools.registry.get_action_descriptions_text()
		done_line = next(ln for ln in text.splitlines() if ln.startswith("- **done**"))
		params_part = done_line.split("(", 1)[1].split(")", 1)[0]
		assert "data" in params_part
		assert "success" not in params_part
		assert "files_to_display" not in params_part

	def test_variant_a_descriptions_show_text_success_files(self):
		tools = Tools()
		text = tools.registry.get_action_descriptions_text()
		done_line = next(ln for ln in text.splitlines() if ln.startswith("- **done**"))
		params_part = done_line.split("(", 1)[1].split(")", 1)[0]
		assert "text" in params_part
		assert "success" in params_part
		assert "files_to_display" in params_part

	@pytest.mark.asyncio
	async def test_structured_done_with_attachments(self, tmp_path):
		f = tmp_path / "a.txt"
		f.write_text("x", encoding="utf-8")
		tools = Tools(output_model=_StructOut)
		r = await _run({"data": {"name": "a"}, "files_to_display": [str(f)]}, tools)
		assert r.attachments == [str(f)]
		# 变体 B extracted_content 保持纯 JSON（不追加清单 / 不内联）
		assert r.extracted_content.lstrip().startswith("{")
		assert "Attachments:" not in r.extracted_content


# ── 二.E variant-B done param validation (step.py regression) ──────


class _FakeStep:
	"""Minimal step exposing only .tools — all _validate_action_params reads."""

	def __init__(self, tools):
		self.tools = tools


class TestDoneStructuredParamValidation:
	"""StepPipeline._validate_action_params must use the registry's variant-B
	param_model (StructuredDoneParams), not the static DoneParams.

	Regression: with output_model set, the LLM correctly emitted done(data=...),
	but validation used the standard DoneParams (text required, data forbidden)
	→ 'text: Field required; data: Extra inputs are not permitted' on every
	retry → 'proceeding anyway' → handler saw a plain string → task failed.
	"""

	def _validate(self, tools, params):
		response = {"action": {"name": "done", "params": params}}
		return StepPipeline._validate_action_params(_FakeStep(tools), response)

	def test_variant_b_valid_data_passes(self):
		err = self._validate(Tools(output_model=_StructOut), {"data": {"name": "a", "count": 3}})
		assert err is None

	def test_variant_b_string_data_reports_data_not_text(self):
		# LLM emitting data as a plain string → actionable 'data' error, not the
		# old contradictory 'text: Field required; data: Extra inputs are not permitted'.
		err = self._validate(Tools(output_model=_StructOut), {"data": "plain string"})
		assert err is not None
		assert "data" in err
		assert "text" not in err

	def test_variant_b_missing_data_reports_data_required(self):
		err = self._validate(Tools(output_model=_StructOut), {})
		assert err is not None
		assert "data" in err

	def test_variant_a_text_passes(self):
		err = self._validate(Tools(), {"text": "ok"})
		assert err is None

	def test_variant_a_data_rejected(self):
		# Without output_model, 'data' is extra → standard DoneParams rejects it.
		err = self._validate(Tools(), {"data": "something"})
		assert err is not None
		assert "text" in err

	def test_unknown_action_gets_retry_feedback(self):
		# PR #174 review3 #3：未注册名进澄清-重试梯子（畸形强转名字得到
		# 「Unknown action」反馈重发，而非落执行失败计 failure）——原行为返 None。
		response = {"action": {"name": "bogus_action", "params": {}}}
		err = StepPipeline._validate_action_params(_FakeStep(Tools()), response)
		assert err is not None
		assert "Unknown action" in err
