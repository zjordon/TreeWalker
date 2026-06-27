"""Tests for read_file: UTF-8 text read, Windows newline byte-fidelity, truncation
echo with footer, empty-file soft-miss, tiered error mapping (NotFound / decode /
dir-perm), char+byte echo.

Mirrors tests/test_replace_file.py: Tools().execute(...) entry point, tmp_path for
FS isolation, newline="" byte-exact helpers, TAB indentation per CLAUDE.md.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from tree_walker.tools.actions import Tools
from tree_walker.tools.models import ReadFileParams


async def _run(params: dict):
	"""Drive read_file through the public Tools().execute entry point."""
	tools = Tools()
	return await tools.execute("read_file", params, MagicMock())


def _read(path) -> str:
	# newline="" reads exact disk bytes (no universal-newline \r\n -> \n munging),
	# matching the action's newline="" read so assertions are byte-exact.
	with open(path, "r", encoding="utf-8", newline="") as f:
		return f.read()


def _seed(path, text: str) -> None:
	"""Write a setup file with exact bytes (newline="")."""
	with open(path, "w", encoding="utf-8", newline="") as f:
		f.write(text)


def _max_chars() -> int:
	"""Read the configured truncation threshold (default 5000, env-overridable)."""
	return Tools()._truncation.read_file_max_chars


# ── Basic read ─────────────────────────────────────────────────────


class TestReadFileBasic:
	@pytest.mark.asyncio
	async def test_reads_text_content(self, tmp_path):
		p = tmp_path / "f.txt"
		_seed(p, "hello world")
		r = await _run({"path": str(p)})
		assert r.error is None
		assert r.extracted_content == "hello world"

	@pytest.mark.asyncio
	async def test_success_is_none_is_done_false(self, tmp_path):
		p = tmp_path / "f.txt"
		_seed(p, "abc")
		r = await _run({"path": str(p)})
		assert r.success is None
		assert r.is_done is False

	@pytest.mark.asyncio
	async def test_cjk_round_trip(self, tmp_path):
		p = tmp_path / "cjk.txt"
		_seed(p, "你好\n")
		r = await _run({"path": str(p)})
		assert r.error is None
		assert r.extracted_content == "你好\n"


# ── Windows newline preservation (core bug fix) ────────────────────


class TestReadFileNewline:
	@pytest.mark.asyncio
	async def test_lf_file_preserved(self, tmp_path):
		# Bug being fixed: bare open(r) translates \r\n -> \n on Windows.
		# newline="" read keeps LF as LF (no spurious \r).
		p = tmp_path / "lf.txt"
		_seed(p, "a\nb")
		r = await _run({"path": str(p)})
		assert r.error is None
		assert r.extracted_content == "a\nb"
		assert "\r" not in r.extracted_content

	@pytest.mark.asyncio
	async def test_crlf_file_preserved(self, tmp_path):
		# newline="" read keeps \r\n intact (bare open(r) would collapse to \n).
		p = tmp_path / "crlf.txt"
		_seed(p, "a\r\nb")
		r = await _run({"path": str(p)})
		assert r.error is None
		assert "\r\n" in r.extracted_content
		assert r.extracted_content == "a\r\nb"

	@pytest.mark.asyncio
	async def test_crlf_literal_round_trip(self, tmp_path):
		# Multiple CRLF lines read back byte-for-byte; downstream replace_file can
		# then match a literal \r\n in `old`.
		p = tmp_path / "crlf.txt"
		_seed(p, "x\r\ny\r\nz")
		r = await _run({"path": str(p)})
		assert r.error is None
		assert r.extracted_content == "x\r\ny\r\nz"


# ── Truncation echo (read_file-unique, intentional超越 browser-use) ──


class TestReadFileTruncation:
	@pytest.mark.asyncio
	async def test_over_max_chars_gets_footer(self, tmp_path):
		n = _max_chars()
		total = n + 1000
		p = tmp_path / "big.txt"
		_seed(p, "x" * total)
		r = await _run({"path": str(p)})
		assert r.error is None
		# footer announces the truncation so the LLM knows there is more
		assert f"[...truncated: showing {n} of {total} chars" in r.extracted_content
		assert f"{total} bytes total)" in r.extracted_content
		# prefix is exactly the first n chars
		assert r.extracted_content.startswith("x" * n)

	@pytest.mark.asyncio
	async def test_memory_mentions_truncated(self, tmp_path):
		n = _max_chars()
		p = tmp_path / "big.txt"
		_seed(p, "x" * (n + 1000))
		r = await _run({"path": str(p)})
		assert "truncated" in r.long_term_memory
		assert str(p) in r.long_term_memory

	@pytest.mark.asyncio
	async def test_at_exactly_max_chars_no_footer(self, tmp_path):
		# exactly at the threshold -> not strictly greater -> no truncation
		n = _max_chars()
		p = tmp_path / "exact.txt"
		_seed(p, "y" * n)
		r = await _run({"path": str(p)})
		assert r.error is None
		assert "[...truncated]" not in r.extracted_content
		assert "truncated" not in r.long_term_memory
		assert r.extracted_content == "y" * n


# ── Empty file soft-miss (corrects the "OK" ambiguity) ─────────────


class TestReadFileEmpty:
	@pytest.mark.asyncio
	async def test_empty_file_is_soft_miss(self, tmp_path):
		p = tmp_path / "empty.txt"
		_seed(p, "")
		r = await _run({"path": str(p)})
		assert r.error is None
		expected = f"{str(p)} is empty (0 bytes)"
		assert r.extracted_content == expected
		assert r.long_term_memory == expected

	@pytest.mark.asyncio
	async def test_empty_file_not_ok(self, tmp_path):
		# previously empty file -> extracted_content="" -> __str__ collapsed to "OK";
		# now it carries an explicit soft-miss message instead.
		p = tmp_path / "empty.txt"
		_seed(p, "")
		r = await _run({"path": str(p)})
		assert "EXTRACTED:" in str(r)
		assert str(r) != "OK"


# ── Error mapping ──────────────────────────────────────────────────


class TestReadFileErrorMapping:
	@pytest.mark.asyncio
	async def test_file_not_found(self, tmp_path):
		p = tmp_path / "nope.txt"
		r = await _run({"path": str(p)})
		assert r.error is not None
		assert "File not found" in r.error
		assert str(p) in r.error

	@pytest.mark.asyncio
	async def test_path_is_directory_returns_oserror(self, tmp_path):
		# path points at an existing directory -> open(dir, "r") raises an OSError
		# subclass (IsADirectoryError on POSIX, PermissionError on Windows).
		r = await _run({"path": str(tmp_path)})
		assert r.error is not None
		assert "Failed to read file" in r.error

	@pytest.mark.asyncio
	async def test_non_utf8_file_returns_decode_error(self, tmp_path):
		# GBK-encoded bytes are invalid UTF-8 -> UnicodeDecodeError branch.
		p = tmp_path / "gbk.txt"
		with open(p, "wb") as f:
			f.write("你好".encode("gbk"))
		r = await _run({"path": str(p)})
		assert r.error is not None
		# 阶段二：decode 文案反映实际编码（默认 utf-8，小写）
		assert "utf-8" in r.error.lower()

	@pytest.mark.asyncio
	async def test_error_has_no_extracted_content(self, tmp_path):
		p = tmp_path / "nope.txt"
		r = await _run({"path": str(p)})
		assert r.error is not None
		assert r.extracted_content is None


# ── Echo (char + byte counts) ──────────────────────────────────────


class TestReadFileEcho:
	@pytest.mark.asyncio
	async def test_memory_has_char_and_byte_counts(self, tmp_path):
		p = tmp_path / "f.txt"
		_seed(p, "hello")  # 5 chars, 5 bytes
		r = await _run({"path": str(p)})
		assert str(p) in r.long_term_memory
		assert "(5 chars, 5 bytes)" in r.long_term_memory

	@pytest.mark.asyncio
	async def test_byte_count_matches_disk(self, tmp_path):
		# echo'd byte count must equal real on-disk size.
		p = tmp_path / "f.txt"
		_seed(p, "hello world")
		r = await _run({"path": str(p)})
		on_disk = os.path.getsize(p)
		assert f"{on_disk} bytes" in r.long_term_memory

	@pytest.mark.asyncio
	async def test_cjk_byte_count_accurate(self, tmp_path):
		# "你好\n" = 3 chars, 7 bytes (each CJK = 3 bytes + 1 for \n)
		p = tmp_path / "cjk.txt"
		_seed(p, "你好\n")
		r = await _run({"path": str(p)})
		assert "(3 chars, 7 bytes)" in r.long_term_memory
		assert os.path.getsize(p) == 7

	@pytest.mark.asyncio
	async def test_extracted_equals_file_content(self, tmp_path):
		p = tmp_path / "f.txt"
		_seed(p, "line1\nline2")
		r = await _run({"path": str(p)})
		assert r.extracted_content == "line1\nline2"
		assert r.long_term_memory is not None

	@pytest.mark.asyncio
	async def test_long_term_memory_set(self, tmp_path):
		# long_term_memory lets the agent recall it already read this file.
		p = tmp_path / "f.txt"
		_seed(p, "abc")
		r = await _run({"path": str(p)})
		assert r.long_term_memory is not None
		assert "Read" in r.long_term_memory


# ── Params validation ──────────────────────────────────────────────


class TestReadFileParamsValidation:
	def test_accepts_path(self):
		m = ReadFileParams(path="x")
		assert m.path == "x"

	def test_extra_field_forbidden(self):
		# offset/limit are now real fields (阶段二 二.A); test extra-forbid with an unknown field.
		with pytest.raises(ValidationError):
			ReadFileParams(path="x", bogus=1)

	def test_path_required(self):
		with pytest.raises(ValidationError):
			ReadFileParams()


# ── 阶段二：encoding 参数 ──────────────────────────────────────────


class TestReadFileEncoding:
	@pytest.mark.asyncio
	async def test_latin1_file_read(self, tmp_path):
		p = tmp_path / "f.txt"
		with open(p, "wb") as f:
			f.write("café".encode("latin-1"))
		r = await _run({"path": str(p), "encoding": "latin-1"})
		assert r.error is None
		assert r.extracted_content == "café"

	@pytest.mark.asyncio
	async def test_cp936_file_read(self, tmp_path):
		p = tmp_path / "f.txt"
		with open(p, "wb") as f:
			f.write("你好".encode("cp936"))
		r = await _run({"path": str(p), "encoding": "cp936"})
		assert r.error is None
		assert r.extracted_content == "你好"

	@pytest.mark.asyncio
	async def test_decode_error_mentions_encoding(self, tmp_path):
		# 0x80/0xff are legal latin-1 but invalid utf-8 -> decode error names utf-8.
		p = tmp_path / "f.txt"
		with open(p, "wb") as f:
			f.write(b"\x80\xff")
		r = await _run({"path": str(p)})  # default utf-8
		assert r.error is not None
		assert "utf-8" in r.error.lower()

	@pytest.mark.asyncio
	async def test_unknown_encoding_returns_lookup_error(self, tmp_path):
		p = tmp_path / "f.txt"
		_seed(p, "abc")
		r = await _run({"path": str(p), "encoding": "no-such-codec"})
		assert r.error is not None
		assert "Unknown encoding" in r.error


# ── 阶段二：newline 翻译控制 ───────────────────────────────────────


class TestReadFileNewlineMode:
	@pytest.mark.asyncio
	async def test_universal_newline_collapses_crlf(self, tmp_path):
		# newline=None enables universal-newline translation: \r\n -> \n
		p = tmp_path / "f.txt"
		_seed(p, "a\r\nb")
		r = await _run({"path": str(p), "newline": None})
		assert r.error is None
		assert r.extracted_content == "a\nb"

	@pytest.mark.asyncio
	async def test_default_newline_preserves_crlf(self, tmp_path):
		# default newline="" keeps CRLF byte-for-byte (regression guard)
		p = tmp_path / "f.txt"
		_seed(p, "a\r\nb")
		r = await _run({"path": str(p)})
		assert r.error is None
		assert r.extracted_content == "a\r\nb"


# ── 阶段二 二.A：offset/limit 字符级分页 ────────────────────────────


class TestReadFileOffsetLimit:
	@pytest.mark.asyncio
	async def test_offset_reads_from_offset(self, tmp_path):
		p = tmp_path / "f.txt"
		_seed(p, "abcdef")
		r = await _run({"path": str(p), "offset": 2})
		assert r.error is None
		# window = read_file_max_chars (limit unset) > len; offset=2 -> "cdef", final page
		assert r.extracted_content == "cdef"
		assert "final page" in r.long_term_memory

	@pytest.mark.asyncio
	async def test_limit_caps_window(self, tmp_path):
		p = tmp_path / "f.txt"
		_seed(p, "abcdefghij")  # 10 chars
		r = await _run({"path": str(p), "limit": 3})
		assert r.error is None
		assert r.extracted_content.startswith("abc")
		assert "[...truncated" in r.extracted_content

	@pytest.mark.asyncio
	async def test_offset_past_end_soft_miss(self, tmp_path):
		p = tmp_path / "f.txt"
		_seed(p, "abc")
		r = await _run({"path": str(p), "offset": 5})
		assert r.error is None
		assert "past end" in r.extracted_content

	@pytest.mark.asyncio
	async def test_default_offset_zero_is_phase1_behavior(self, tmp_path):
		p = tmp_path / "f.txt"
		_seed(p, "abc")
		r = await _run({"path": str(p)})
		assert r.error is None
		assert r.extracted_content == "abc"
		assert r.long_term_memory == f"Read {str(p)} (3 chars, 3 bytes)"

	@pytest.mark.asyncio
	async def test_truncated_footer_has_continue_hint(self, tmp_path):
		n = _max_chars()
		p = tmp_path / "big.txt"
		_seed(p, "x" * (n + 100))
		r = await _run({"path": str(p)})
		# phase-1 substring preserved (backward compat) + new continue hint
		assert f"[...truncated: showing {n} of {n + 100} chars" in r.extracted_content
		assert "use offset=" in r.extracted_content
		assert "to continue]" in r.extracted_content

	@pytest.mark.asyncio
	async def test_offset_limit_pages(self, tmp_path):
		n = _max_chars()
		p = tmp_path / "big.txt"
		_seed(p, "y" * (n + 200))
		r = await _run({"path": str(p), "offset": n, "limit": 100})
		assert r.error is None
		# window=100 from offset n; next continue offset should be n+100
		assert f"use offset={n + 100} to continue" in r.extracted_content

	@pytest.mark.asyncio
	async def test_offset_preserves_crlf(self, tmp_path):
		p = tmp_path / "crlf.txt"
		_seed(p, "x\r\ny\r\nz")
		r = await _run({"path": str(p), "offset": 1})
		assert r.error is None
		assert "\r\n" in r.extracted_content


# ── 阶段二 二.B：二进制嗅探 ────────────────────────────────────────


class TestReadFileBinarySniff:
	@pytest.mark.asyncio
	async def test_exe_rejected_as_binary(self, tmp_path):
		p = tmp_path / "evil.exe"
		with open(p, "wb") as f:
			f.write(b"MZ\x90\x00" + b"\x00" * 32)
		r = await _run({"path": str(p)})
		assert r.error is not None
		assert "binary" in r.error.lower()

	@pytest.mark.asyncio
	async def test_gzip_rejected_as_binary(self, tmp_path):
		p = tmp_path / "a.gz"
		with open(p, "wb") as f:
			f.write(b"\x1f\x8b" + b"\x00" * 16)
		r = await _run({"path": str(p)})
		assert "binary" in r.error.lower()

	@pytest.mark.asyncio
	async def test_plain_zip_non_docx_rejected_as_binary(self, tmp_path):
		p = tmp_path / "a.zip"
		with open(p, "wb") as f:
			f.write(b"PK\x03\x04" + b"\x00" * 16)
		r = await _run({"path": str(p)})
		assert "binary" in r.error.lower()

	@pytest.mark.asyncio
	async def test_png_routes_to_image_prompt(self, tmp_path):
		p = tmp_path / "pic.png"
		with open(p, "wb") as f:
			f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
		r = await _run({"path": str(p)})
		assert r.error is None
		assert "image" in r.extracted_content.lower()
		assert "vision" in r.extracted_content.lower()

	@pytest.mark.asyncio
	async def test_utf8_text_not_sniffed_as_binary(self, tmp_path):
		# plain text has no magic header -> text path -> normal read
		p = tmp_path / "f.txt"
		_seed(p, "hello world")
		r = await _run({"path": str(p)})
		assert r.error is None
		assert r.extracted_content == "hello world"

	@pytest.mark.asyncio
	async def test_gbk_text_still_decode_error(self, tmp_path):
		# GBK bytes have no magic header -> text path -> UnicodeDecodeError (regression)
		p = tmp_path / "gbk.txt"
		with open(p, "wb") as f:
			f.write("你好".encode("gbk"))
		r = await _run({"path": str(p)})
		assert r.error is not None
		assert "utf-8" in r.error.lower()


# ── 阶段二 二.C：allowed_read_paths 白名单 ──────────────────────────


class TestReadFileReadWhitelist:
	@pytest.mark.asyncio
	async def test_blocked_outside_whitelist(self, tmp_path):
		inside = tmp_path / "inside"
		inside.mkdir()
		outside = tmp_path / "outside.txt"
		_seed(outside, "hi")
		tools = Tools(allowed_read_paths=[str(inside)])
		r = await tools.execute("read_file", {"path": str(outside)}, MagicMock())
		assert r.error is not None
		assert "allowed read paths" in r.error

	@pytest.mark.asyncio
	async def test_allowed_inside_whitelist(self, tmp_path):
		p = tmp_path / "f.txt"
		_seed(p, "hi")
		tools = Tools(allowed_read_paths=[str(tmp_path)])
		r = await tools.execute("read_file", {"path": str(p)}, MagicMock())
		assert r.error is None
		assert r.extracted_content == "hi"

	@pytest.mark.asyncio
	async def test_none_whitelist_allows_anywhere(self, tmp_path):
		p = tmp_path / "f.txt"
		_seed(p, "hi")
		# default Tools() -> _allowed_read_paths is None -> no restriction (现状)
		r = await _run({"path": str(p)})
		assert r.error is None
		assert r.extracted_content == "hi"


# ── 阶段二 二.D：富文档（PDF/DOCX/图片） ───────────────────────────


class TestReadFileRichDocs:
	@pytest.mark.asyncio
	async def test_image_returns_actionable_prompt(self, tmp_path):
		p = tmp_path / "pic.png"
		with open(p, "wb") as f:
			f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
		r = await _run({"path": str(p)})
		assert r.error is None
		assert "image" in r.extracted_content.lower()
		assert "vision" in r.extracted_content.lower()
		assert r.metadata is None  # 不堆 base64 死数据（vision 通道未接线）

	@pytest.mark.asyncio
	async def test_pdf_text_extraction(self, tmp_path, monkeypatch):
		pdf = pytest.importorskip("pypdf")
		p = tmp_path / "doc.pdf"
		p.write_bytes(b"%PDF-1.4\n")  # real magic so sniff routes to pdf
		page = MagicMock()
		page.extract_text.return_value = "page body text"
		reader = MagicMock()
		reader.pages = [page, page]
		monkeypatch.setattr(pdf, "PdfReader", lambda path: reader)
		r = await _run({"path": str(p)})
		assert r.error is None
		assert "page body text" in r.extracted_content
		assert "--- page 1/2 ---" in r.extracted_content

	@pytest.mark.asyncio
	async def test_pdf_without_dep_returns_install_hint(self, tmp_path, monkeypatch):
		# simulate the 'docs' extra not installed
		monkeypatch.setitem(sys.modules, "pypdf", None)
		p = tmp_path / "doc.pdf"
		p.write_bytes(b"%PDF-1.4\n")
		r = await _run({"path": str(p)})
		assert r.error is not None
		assert "docs" in r.error

	@pytest.mark.asyncio
	async def test_docx_text_extraction(self, tmp_path, monkeypatch):
		docx = pytest.importorskip("docx")
		p = tmp_path / "doc.docx"
		p.write_bytes(b"PK\x03\x04" + b"\x00" * 16)  # zip magic + .docx -> docx path
		para1, para2, empty = MagicMock(), MagicMock(), MagicMock()
		para1.text = "first line"
		para2.text = "second line"
		empty.text = ""  # filtered out (p.text.strip() falsy)
		doc = MagicMock()
		doc.paragraphs = [para1, empty, para2]
		monkeypatch.setattr(docx, "Document", lambda path: doc)
		r = await _run({"path": str(p)})
		assert r.error is None
		assert "first line" in r.extracted_content
		assert "second line" in r.extracted_content

	@pytest.mark.asyncio
	async def test_docx_without_dep_returns_install_hint(self, tmp_path, monkeypatch):
		monkeypatch.setitem(sys.modules, "docx", None)
		p = tmp_path / "doc.docx"
		p.write_bytes(b"PK\x03\x04" + b"\x00" * 16)
		r = await _run({"path": str(p)})
		assert r.error is not None
		assert "docs" in r.error
