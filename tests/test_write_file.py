"""Tests for write_file: overwrite, append, newline bookkeeping,
OSError mapping, success echo, UTF-8 round-trip, param validation.

Mirrors tests/test_save_as_pdf.py: Tools().execute(...) entry point,
tmp_path for FS isolation, TAB indentation per CLAUDE.md.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from tree_walker.tools.actions import Tools
from tree_walker.tools.models import WriteFileParams


async def _run(params: dict):
	"""Drive write_file through the public Tools().execute entry point."""
	tools = Tools()
	return await tools.execute("write_file", params, MagicMock())


def _read(path) -> str:
	# newline="" reads exact disk bytes (no universal-newline \r\n -> \n munging),
	# matching the action's newline="" write so assertions are byte-exact.
	with open(path, "r", encoding="utf-8", newline="") as f:
		return f.read()


def _seed(path, text: str) -> None:
	"""Write a setup file with LF line endings (newline=""), matching the action."""
	with open(path, "w", encoding="utf-8", newline="") as f:
		f.write(text)


# ── Overwrite ───────────────────────────────────────────────────────


class TestWriteFileOverwrite:
	@pytest.mark.asyncio
	async def test_default_overwrites_existing_file(self, tmp_path):
		p = tmp_path / "f.txt"
		_seed(p, "old content\n")
		r = await _run({"path": str(p), "content": "new"})
		assert r.error is None
		assert _read(p) == "new\n"

	@pytest.mark.asyncio
	async def test_creates_new_file(self, tmp_path):
		p = tmp_path / "fresh.txt"
		r = await _run({"path": str(p), "content": "hello"})
		assert r.error is None
		assert p.exists()
		assert _read(p) == "hello\n"

	@pytest.mark.asyncio
	async def test_creates_parent_directories(self, tmp_path):
		p = tmp_path / "sub" / "deep" / "out.txt"
		r = await _run({"path": str(p), "content": "x"})
		assert r.error is None
		assert p.exists()
		assert _read(p) == "x\n"

	@pytest.mark.asyncio
	async def test_overwrite_replaces_partial_content(self, tmp_path):
		p = tmp_path / "f.txt"
		_seed(p, "hello world\n")
		await _run({"path": str(p), "content": "bye"})
		assert _read(p) == "bye\n"


# ── Append ──────────────────────────────────────────────────────────


class TestWriteFileAppend:
	@pytest.mark.asyncio
	async def test_append_to_existing_file(self, tmp_path):
		p = tmp_path / "log.txt"
		_seed(p, "a\n")
		r = await _run({"path": str(p), "content": "b", "append": True})
		assert r.error is None
		assert _read(p) == "a\nb\n"

	@pytest.mark.asyncio
	async def test_append_to_nonexistent_file_creates_it(self, tmp_path):
		# Python 'a' mode auto-creates — deliberately diverges from browser-use
		# (whose append requires the file to exist). See proposal "关键差异" #5.
		p = tmp_path / "new.log"
		r = await _run({"path": str(p), "content": "first", "append": True})
		assert r.error is None
		assert p.exists()
		assert _read(p) == "first\n"

	@pytest.mark.asyncio
	async def test_append_does_not_overwrite(self, tmp_path):
		p = tmp_path / "keep.txt"
		_seed(p, "keep me")
		await _run({"path": str(p), "content": "more", "append": True, "leading_newline": True})
		# "keep me" + leading \n + "more" + trailing \n
		assert _read(p) == "keep me\nmore\n"


# ── Trailing newline ────────────────────────────────────────────────


class TestTrailingNewline:
	@pytest.mark.asyncio
	async def test_trailing_newline_default_appends_one(self, tmp_path):
		p = tmp_path / "f.txt"
		await _run({"path": str(p), "content": "hi"})
		assert _read(p) == "hi\n"

	@pytest.mark.asyncio
	async def test_trailing_newline_idempotent_on_existing(self, tmp_path):
		# Guarded form (not browser-use's unconditional += '\n'): no double newline
		p = tmp_path / "f.txt"
		await _run({"path": str(p), "content": "hi\n"})
		assert _read(p) == "hi\n"

	@pytest.mark.asyncio
	async def test_trailing_newline_false_no_append(self, tmp_path):
		p = tmp_path / "f.txt"
		await _run({"path": str(p), "content": "hi", "trailing_newline": False})
		assert _read(p) == "hi"

	@pytest.mark.asyncio
	async def test_trailing_newline_preserves_crlf(self, tmp_path):
		# "foo\r\n".endswith("\n") is True → guard leaves CRLF intact
		p = tmp_path / "f.txt"
		await _run({"path": str(p), "content": "hi\r\n"})
		assert _read(p) == "hi\r\n"


# ── Leading newline ─────────────────────────────────────────────────


class TestLeadingNewline:
	@pytest.mark.asyncio
	async def test_leading_newline_prepends(self, tmp_path):
		p = tmp_path / "f.txt"
		await _run({"path": str(p), "content": "x", "leading_newline": True})
		assert _read(p) == "\nx\n"

	@pytest.mark.asyncio
	async def test_leading_newline_false_default(self, tmp_path):
		p = tmp_path / "f.txt"
		await _run({"path": str(p), "content": "x"})
		assert _read(p) == "x\n"

	@pytest.mark.asyncio
	async def test_leading_plus_trailing(self, tmp_path):
		p = tmp_path / "f.txt"
		await _run({"path": str(p), "content": "x", "leading_newline": True, "trailing_newline": True})
		assert _read(p) == "\nx\n"


# ── Empty content ───────────────────────────────────────────────────


class TestEmptyContent:
	@pytest.mark.asyncio
	async def test_empty_content_overwrite_truncates(self, tmp_path):
		p = tmp_path / "f.txt"
		_seed(p, "data")
		await _run({"path": str(p), "content": "", "trailing_newline": False})
		assert _read(p) == ""

	@pytest.mark.asyncio
	async def test_empty_content_with_trailing_newline(self, tmp_path):
		p = tmp_path / "f.txt"
		await _run({"path": str(p), "content": ""})
		assert _read(p) == "\n"

	@pytest.mark.asyncio
	async def test_empty_content_append(self, tmp_path):
		p = tmp_path / "f.txt"
		_seed(p, "x\n")
		await _run({"path": str(p), "content": "", "append": True, "trailing_newline": False})
		assert _read(p) == "x\n"


# ── Error mapping ───────────────────────────────────────────────────


class TestWriteFileErrorMapping:
	@pytest.mark.asyncio
	async def test_write_to_directory_returns_error(self, tmp_path):
		# path points at an existing directory → open(dir, "w") raises an OSError subclass
		r = await _run({"path": str(tmp_path), "content": "x"})
		assert r.error is not None
		assert r.extracted_content is None

	@pytest.mark.asyncio
	async def test_error_message_includes_path(self, tmp_path):
		r = await _run({"path": str(tmp_path), "content": "x"})
		assert r.error is not None
		assert "Failed to write file" in r.error
		assert str(tmp_path) in r.error


# ── Echo ────────────────────────────────────────────────────────────


class TestWriteFileEcho:
	@pytest.mark.asyncio
	async def test_extracted_content_includes_byte_count(self, tmp_path):
		p = tmp_path / "f.txt"
		r = await _run({"path": str(p), "content": "hi"})  # -> "hi\n" = 3 bytes
		assert "3 bytes" in r.extracted_content

	@pytest.mark.asyncio
	async def test_extracted_content_includes_path(self, tmp_path):
		p = tmp_path / "f.txt"
		r = await _run({"path": str(p), "content": "hi"})
		assert str(p) in r.extracted_content

	@pytest.mark.asyncio
	async def test_append_uses_Appended_word(self, tmp_path):
		p = tmp_path / "f.txt"
		_seed(p, "a\n")
		r = await _run({"path": str(p), "content": "b", "append": True})
		assert r.extracted_content.startswith("Appended ")
		assert "Wrote" not in r.extracted_content

	@pytest.mark.asyncio
	async def test_overwrite_uses_Wrote_word(self, tmp_path):
		p = tmp_path / "f.txt"
		r = await _run({"path": str(p), "content": "b"})
		assert r.extracted_content.startswith("Wrote ")

	@pytest.mark.asyncio
	async def test_long_term_memory_equals_extracted_content(self, tmp_path):
		p = tmp_path / "f.txt"
		r = await _run({"path": str(p), "content": "hi"})
		assert r.long_term_memory == r.extracted_content

	@pytest.mark.asyncio
	async def test_byte_count_after_newline_bookkeeping(self, tmp_path):
		# echo'd byte count must equal real on-disk size (incl. the appended \n)
		p = tmp_path / "f.txt"
		await _run({"path": str(p), "content": "hi"})  # default trailing -> "hi\n"
		assert os.path.getsize(p) == 3

	@pytest.mark.asyncio
	async def test_success_is_none(self, tmp_path):
		p = tmp_path / "f.txt"
		r = await _run({"path": str(p), "content": "hi"})
		assert r.success is None
		assert r.is_done is False

	@pytest.mark.asyncio
	async def test_cjk_byte_count_accurate(self, tmp_path):
		p = tmp_path / "f.txt"
		r = await _run({"path": str(p), "content": "你好"})  # -> "你好\n" = 7 bytes
		on_disk = os.path.getsize(p)
		assert f"{on_disk} bytes" in r.extracted_content
		assert on_disk == len("你好\n".encode("utf-8")) == 7


# ── UTF-8 round-trip ────────────────────────────────────────────────


class TestWriteFileUtf8RoundTrip:
	@pytest.mark.asyncio
	async def test_cjk_content_round_trips(self, tmp_path):
		p = tmp_path / "f.txt"
		await _run({"path": str(p), "content": "你好"})
		assert _read(p) == "你好\n"

	@pytest.mark.asyncio
	async def test_emoji_content_round_trips(self, tmp_path):
		p = tmp_path / "f.txt"
		await _run({"path": str(p), "content": "hi 🎉", "trailing_newline": False})
		assert _read(p) == "hi 🎉"

	@pytest.mark.asyncio
	async def test_mixed_ascii_cjk(self, tmp_path):
		p = tmp_path / "f.txt"
		await _run({"path": str(p), "content": "abc你好", "trailing_newline": False})
		assert _read(p) == "abc你好"
		assert os.path.getsize(p) == len("abc你好".encode("utf-8"))


# ── Params validation ───────────────────────────────────────────────


class TestWriteFileParamsValidation:
	def test_defaults(self):
		m = WriteFileParams(path="x", content="y")
		assert m.append is False
		assert m.trailing_newline is True
		assert m.leading_newline is False

	def test_extra_field_forbidden(self):
		with pytest.raises(ValidationError):
			WriteFileParams(path="x", content="y", oops=1)

	def test_path_required(self):
		with pytest.raises(ValidationError):
			WriteFileParams(content="y")

	def test_content_required(self):
		with pytest.raises(ValidationError):
			WriteFileParams(path="x")

	def test_explicit_append_true(self):
		m = WriteFileParams(path="x", content="y", append=True)
		assert m.append is True

	def test_explicit_newline_flags(self):
		m = WriteFileParams(path="x", content="y", trailing_newline=False, leading_newline=True)
		assert m.trailing_newline is False
		assert m.leading_newline is True

	def test_encoding_and_newline_defaults(self):
		m = WriteFileParams(path="x", content="y")
		assert m.encoding is None
		assert m.newline == ""


# ── 阶段二：原子写（overwrite tmp + os.replace）────────────────────


class TestWriteFileAtomicWrite:
	@pytest.mark.asyncio
	async def test_overwrite_uses_tmp_then_replace(self, tmp_path, monkeypatch):
		# overwrite 应走 tmp + os.replace：替换发生前 target 仍是旧内容、tmp 已存在。
		p = tmp_path / "f.txt"
		_seed(p, "original\n")
		seen = {}
		real_replace = os.replace

		def spy_replace(src, dst):
			seen["tmp_exists_before_replace"] = os.path.exists(src)
			seen["target_before_replace"] = _read(p)
			return real_replace(src, dst)

		monkeypatch.setattr("tree_walker.tools.actions.os.replace", spy_replace)
		r = await _run({"path": str(p), "content": "new"})
		assert r.error is None
		assert seen["tmp_exists_before_replace"] is True
		assert seen["target_before_replace"] == "original\n"
		assert _read(p) == "new\n"
		# 替换完成后 tmp 已消失
		assert not os.path.exists(str(p) + ".tmp")

	@pytest.mark.asyncio
	async def test_overwrite_replace_failure_keeps_original_and_cleans_tmp(self, tmp_path, monkeypatch):
		# os.replace 抛错 → target 完好、无残留 tmp、返回 error。
		p = tmp_path / "f.txt"
		_seed(p, "keep me\n")

		def boom(src, dst):
			raise OSError("replace denied")

		monkeypatch.setattr("tree_walker.tools.actions.os.replace", boom)
		r = await _run({"path": str(p), "content": "new"})
		assert r.error is not None
		assert "Failed to write file" in r.error
		# 原文件不被改写（原子性核心）
		assert _read(p) == "keep me\n"
		# 无残留 tmp
		assert not os.path.exists(str(p) + ".tmp")

	@pytest.mark.asyncio
	async def test_append_does_not_use_tmp(self, tmp_path, monkeypatch):
		# append 保持直接写，不产生 .tmp、不调用 os.replace。
		p = tmp_path / "log.txt"
		_seed(p, "a\n")

		def boom(src, dst):
			raise AssertionError("append must not call os.replace")

		monkeypatch.setattr("tree_walker.tools.actions.os.replace", boom)
		r = await _run({"path": str(p), "content": "b", "append": True})
		assert r.error is None
		assert _read(p) == "a\nb\n"
		assert not os.path.exists(str(p) + ".tmp")


# ── 阶段二：encoding 参数 ──────────────────────────────────────────


class TestWriteFileEncoding:
	@pytest.mark.asyncio
	async def test_latin1_write_round_trips(self, tmp_path):
		p = tmp_path / "f.txt"
		r = await _run({"path": str(p), "content": "café", "encoding": "latin-1", "trailing_newline": False})
		assert r.error is None
		assert p.read_bytes() == "café".encode("latin-1")

	@pytest.mark.asyncio
	async def test_cp936_write_round_trips(self, tmp_path):
		p = tmp_path / "f.txt"
		r = await _run({"path": str(p), "content": "你好", "encoding": "cp936", "trailing_newline": False})
		assert r.error is None
		assert p.read_bytes() == "你好".encode("cp936")

	@pytest.mark.asyncio
	async def test_non_default_encoding_in_echo(self, tmp_path):
		p = tmp_path / "f.txt"
		r = await _run({"path": str(p), "content": "abc", "encoding": "latin-1"})
		assert "(encoding: latin-1)" in r.extracted_content

	@pytest.mark.asyncio
	async def test_default_encoding_not_in_echo(self, tmp_path):
		p = tmp_path / "f.txt"
		r = await _run({"path": str(p), "content": "abc"})
		assert "(encoding:" not in r.extracted_content

	@pytest.mark.asyncio
	async def test_byte_count_uses_encoding(self, tmp_path):
		# "café" is 4 bytes in latin-1, 5 in utf-8
		p = tmp_path / "f.txt"
		r = await _run({"path": str(p), "content": "café", "encoding": "latin-1", "trailing_newline": False})
		assert "4 bytes" in r.extracted_content
		assert os.path.getsize(p) == 4

	@pytest.mark.asyncio
	async def test_unknown_encoding_returns_lookup_error(self, tmp_path):
		p = tmp_path / "f.txt"
		r = await _run({"path": str(p), "content": "x", "encoding": "no-such-codec"})
		assert r.error is not None
		assert "Unknown encoding" in r.error
		assert "no-such-codec" in r.error
		# 失败不留 tmp
		assert not os.path.exists(str(p) + ".tmp")


# ── 阶段二：newline 翻译控制 ───────────────────────────────────────


class TestWriteFileNewlineMode:
	@pytest.mark.asyncio
	async def test_force_crlf_output(self, tmp_path):
		p = tmp_path / "f.txt"
		await _run({"path": str(p), "content": "a\nb", "newline": "\r\n", "trailing_newline": False})
		assert p.read_bytes() == b"a\r\nb"

	@pytest.mark.asyncio
	async def test_default_newline_no_translation(self, tmp_path):
		p = tmp_path / "f.txt"
		await _run({"path": str(p), "content": "a\nb", "trailing_newline": False})
		assert p.read_bytes() == b"a\nb"


# ── 阶段二：allowed_write_paths 白名单 ─────────────────────────────


class TestWriteFileWhitelist:
	@pytest.mark.asyncio
	async def test_blocked_outside_whitelist(self, tmp_path):
		tools = Tools(allowed_write_paths=[str(tmp_path / "safe")])
		outside = tmp_path / "elsewhere" / "x.txt"
		r = await tools.execute("write_file", {"path": str(outside), "content": "x"}, MagicMock())
		assert r.error is not None
		assert "not in allowed write paths" in r.error
		# fail fast：连父目录都不应被创建
		assert not outside.exists()
		assert not (tmp_path / "elsewhere").exists()

	@pytest.mark.asyncio
	async def test_allowed_inside_whitelist(self, tmp_path):
		safe = tmp_path / "safe"
		tools = Tools(allowed_write_paths=[str(safe)])
		target = safe / "x.txt"
		r = await tools.execute("write_file", {"path": str(target), "content": "x"}, MagicMock())
		assert r.error is None
		assert target.exists()

	@pytest.mark.asyncio
	async def test_none_whitelist_allows_anywhere(self, tmp_path):
		# default Tools() → no whitelist → unrestricted write
		tools = Tools()
		outside = tmp_path / "anywhere" / "x.txt"
		r = await tools.execute("write_file", {"path": str(outside), "content": "x"}, MagicMock())
		assert r.error is None
		assert outside.exists()
