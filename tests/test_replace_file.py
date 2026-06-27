"""Tests for replace_file: global literal replace, Windows newline preservation,
soft-miss on zero matches, empty-old rejection, tiered error mapping, count echo.

Mirrors tests/test_write_file.py: Tools().execute(...) entry point, tmp_path for
FS isolation, TAB indentation per CLAUDE.md.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from tree_walker.tools.actions import Tools
from tree_walker.tools.models import ReplaceFileParams


async def _run(params: dict):
	"""Drive replace_file through the public Tools().execute entry point."""
	tools = Tools()
	return await tools.execute("replace_file", params, MagicMock())


def _read(path) -> str:
	# newline="" reads exact disk bytes (no universal-newline \r\n -> \n munging),
	# matching the action's newline="" write so assertions are byte-exact.
	with open(path, "r", encoding="utf-8", newline="") as f:
		return f.read()


def _seed(path, text: str) -> None:
	"""Write a setup file with exact bytes (newline="")."""
	with open(path, "w", encoding="utf-8", newline="") as f:
		f.write(text)


# ── Basic replacement ──────────────────────────────────────────────


class TestReplaceFileBasic:
	@pytest.mark.asyncio
	async def test_single_match_replaced(self, tmp_path):
		p = tmp_path / "f.txt"
		_seed(p, "foo baz")
		r = await _run({"path": str(p), "old": "foo", "new": "bar"})
		assert r.error is None
		assert _read(p) == "bar baz"

	@pytest.mark.asyncio
	async def test_all_occurrences_replaced(self, tmp_path):
		p = tmp_path / "f.txt"
		_seed(p, "a a a")
		r = await _run({"path": str(p), "old": "a", "new": "b"})
		assert r.error is None
		# global replace: every non-overlapping occurrence
		assert _read(p) == "b b b"

	@pytest.mark.asyncio
	async def test_new_can_delete_match(self, tmp_path):
		# new="" is a legal "delete" operation (only old is forbidden from being empty)
		p = tmp_path / "f.txt"
		_seed(p, "aXbXc")
		r = await _run({"path": str(p), "old": "X", "new": ""})
		assert r.error is None
		assert _read(p) == "abc"

	@pytest.mark.asyncio
	async def test_case_sensitive(self, tmp_path):
		p = tmp_path / "f.txt"
		_seed(p, "Foo foo")
		r = await _run({"path": str(p), "old": "Foo", "new": "X"})
		assert r.error is None
		# lowercase "foo" is untouched
		assert _read(p) == "X foo"

	@pytest.mark.asyncio
	async def test_non_overlapping_count(self, tmp_path):
		p = tmp_path / "f.txt"
		_seed(p, "aaaa")
		r = await _run({"path": str(p), "old": "aa", "new": "b"})
		assert r.error is None
		# str.replace semantics: "aaaa".replace("aa","b") == "bb" (2 non-overlapping)
		assert _read(p) == "bb"
		assert "2 occurrences" in r.extracted_content

	@pytest.mark.asyncio
	async def test_multiline_old_matches_across_lines(self, tmp_path):
		p = tmp_path / "f.txt"
		_seed(p, "line1\nOLD\nline3")
		r = await _run({"path": str(p), "old": "OLD", "new": "NEW"})
		assert r.error is None
		assert _read(p) == "line1\nNEW\nline3"


# ── Windows newline preservation (core bug fix) ────────────────────


class TestReplaceFileNewline:
	@pytest.mark.asyncio
	async def test_lf_file_preserved(self, tmp_path):
		# Bug being fixed: bare open(r)/open(w) would translate \r\n<->\n on Windows.
		# newline="" read/write keeps LF as LF.
		p = tmp_path / "f.txt"
		_seed(p, "a\nb")
		r = await _run({"path": str(p), "old": "a", "new": "X"})
		assert r.error is None
		assert _read(p) == "X\nb"

	@pytest.mark.asyncio
	async def test_crlf_file_preserved(self, tmp_path):
		# newline="" read keeps \r\n, newline="" write doesn't re-translate -> CRLF stays.
		p = tmp_path / "f.txt"
		_seed(p, "a\r\nb")
		r = await _run({"path": str(p), "old": "a", "new": "X"})
		assert r.error is None
		assert _read(p) == "X\r\nb"

	@pytest.mark.asyncio
	async def test_crlf_literal_in_old_matches(self, tmp_path):
		# old contains a literal \r\n. With bare open(r) the read would collapse \r\n
		# to \n and the match would silently fail; newline="" keeps it intact.
		p = tmp_path / "f.txt"
		_seed(p, "x\r\ny\r\nz")
		r = await _run({"path": str(p), "old": "x\r\n", "new": "w\r\n"})
		assert r.error is None
		assert _read(p) == "w\r\ny\r\nz"


# ── Soft miss (corrects browser-use's silent-success defect) ───────


class TestReplaceFileSoftMiss:
	@pytest.mark.asyncio
	async def test_zero_matches_returns_soft_miss_not_error(self, tmp_path):
		p = tmp_path / "f.txt"
		_seed(p, "hello world")
		r = await _run({"path": str(p), "old": "xyz", "new": "abc"})
		assert r.error is None
		assert r.extracted_content is not None
		assert "No occurrences" in r.extracted_content

	@pytest.mark.asyncio
	async def test_soft_miss_file_unchanged(self, tmp_path):
		p = tmp_path / "f.txt"
		_seed(p, "hello world")
		await _run({"path": str(p), "old": "xyz", "new": "abc"})
		# zero matches -> file is not rewritten
		assert _read(p) == "hello world"

	@pytest.mark.asyncio
	async def test_soft_miss_double_writes_memory(self, tmp_path):
		p = tmp_path / "f.txt"
		_seed(p, "abc")
		r = await _run({"path": str(p), "old": "xyz", "new": "q"})
		assert r.extracted_content == r.long_term_memory

	@pytest.mark.asyncio
	async def test_soft_miss_old_repr_in_message(self, tmp_path):
		# {old!r} -> 'xyz' (quoted), so the LLM sees exactly what was searched.
		p = tmp_path / "f.txt"
		_seed(p, "abc")
		r = await _run({"path": str(p), "old": "xyz", "new": "q"})
		assert "'xyz'" in r.extracted_content


# ── Error mapping ──────────────────────────────────────────────────


class TestReplaceFileErrorMapping:
	@pytest.mark.asyncio
	async def test_file_not_found(self, tmp_path):
		p = tmp_path / "nope.txt"
		r = await _run({"path": str(p), "old": "a", "new": "b"})
		assert r.error is not None
		assert "File not found" in r.error
		assert str(p) in r.error

	@pytest.mark.asyncio
	async def test_path_is_directory_returns_oserror(self, tmp_path):
		# path points at an existing directory -> open(dir, "r") raises an OSError
		# subclass (IsADirectoryError on POSIX, PermissionError on Windows).
		r = await _run({"path": str(tmp_path), "old": "a", "new": "b"})
		assert r.error is not None
		assert "Failed to replace text" in r.error

	@pytest.mark.asyncio
	async def test_non_utf8_file_returns_decode_error(self, tmp_path):
		# GBK-encoded bytes are invalid UTF-8 -> UnicodeDecodeError branch.
		p = tmp_path / "gbk.txt"
		with open(p, "wb") as f:
			f.write("你好".encode("gbk"))
		r = await _run({"path": str(p), "old": "你", "new": "x"})
		assert r.error is not None
		# 阶段二：decode 文案反映实际编码（默认 utf-8，小写）
		assert "utf-8" in r.error.lower()

	@pytest.mark.asyncio
	async def test_empty_old_rejected_at_execute(self, tmp_path):
		# registry does not validate params at runtime; the handler's own guard
		# must reject empty old on the execute path (not silently inflate the file).
		p = tmp_path / "f.txt"
		_seed(p, "abc")
		r = await _run({"path": str(p), "old": "", "new": "x"})
		assert r.error is not None
		assert "non-empty" in r.error
		# file untouched (str.replace("", "x") would otherwise have bloated it)
		assert _read(p) == "abc"


# ── Echo ───────────────────────────────────────────────────────────


class TestReplaceFileEcho:
	@pytest.mark.asyncio
	async def test_hit_echo_includes_count(self, tmp_path):
		p = tmp_path / "f.txt"
		_seed(p, "a a a")
		r = await _run({"path": str(p), "old": "a", "new": "b"})
		assert "3 occurrences" in r.extracted_content

	@pytest.mark.asyncio
	async def test_single_match_singular(self, tmp_path):
		p = tmp_path / "f.txt"
		_seed(p, "foo bar baz")
		r = await _run({"path": str(p), "old": "bar", "new": "X"})
		assert "1 occurrence" in r.extracted_content
		# singular form: no trailing 's'
		assert "1 occurrences" not in r.extracted_content

	@pytest.mark.asyncio
	async def test_hit_echo_includes_path_and_bytes(self, tmp_path):
		p = tmp_path / "f.txt"
		_seed(p, "hello")
		r = await _run({"path": str(p), "old": "hello", "new": "hi"})
		assert str(p) in r.extracted_content
		assert "bytes" in r.extracted_content

	@pytest.mark.asyncio
	async def test_hit_long_term_memory_equals_extracted_content(self, tmp_path):
		p = tmp_path / "f.txt"
		_seed(p, "abc")
		r = await _run({"path": str(p), "old": "a", "new": "X"})
		assert r.long_term_memory == r.extracted_content

	@pytest.mark.asyncio
	async def test_hit_success_is_none(self, tmp_path):
		p = tmp_path / "f.txt"
		_seed(p, "abc")
		r = await _run({"path": str(p), "old": "a", "new": "X"})
		assert r.success is None
		assert r.is_done is False

	@pytest.mark.asyncio
	async def test_echo_byte_count_matches_disk(self, tmp_path):
		# echo'd byte count must equal real on-disk size after replacement.
		p = tmp_path / "f.txt"
		_seed(p, "abcabc")
		r = await _run({"path": str(p), "old": "abc", "new": "XY"})  # -> "XYXY" = 4 bytes
		on_disk = os.path.getsize(p)
		assert f"{on_disk} bytes" in r.extracted_content
		assert on_disk == 4


# ── UTF-8 round-trip ───────────────────────────────────────────────


class TestReplaceFileUtf8:
	@pytest.mark.asyncio
	async def test_cjk_replace_round_trips(self, tmp_path):
		p = tmp_path / "f.txt"
		_seed(p, "你好世界")
		r = await _run({"path": str(p), "old": "好世", "new": "天地"})
		assert r.error is None
		assert _read(p) == "你天地界"

	@pytest.mark.asyncio
	async def test_emoji_replace_round_trips(self, tmp_path):
		p = tmp_path / "f.txt"
		_seed(p, "hi 🎉 bye")
		r = await _run({"path": str(p), "old": "🎉", "new": "🚀"})
		assert r.error is None
		assert _read(p) == "hi 🚀 bye"


# ── Params validation ──────────────────────────────────────────────


class TestReplaceFileParamsValidation:
	def test_old_empty_rejected(self):
		# min_length=1 rejects empty old at schema/construction time
		with pytest.raises(ValidationError):
			ReplaceFileParams(path="x", old="", new="y")

	def test_new_empty_allowed(self):
		# new="" is valid (delete semantics)
		m = ReplaceFileParams(path="x", old="a", new="")
		assert m.new == ""

	def test_extra_field_forbidden(self):
		with pytest.raises(ValidationError):
			ReplaceFileParams(path="x", old="a", new="b", oops=1)

	def test_path_required(self):
		with pytest.raises(ValidationError):
			ReplaceFileParams(old="a", new="b")

	def test_old_required(self):
		with pytest.raises(ValidationError):
			ReplaceFileParams(path="x", new="b")

	def test_new_required(self):
		with pytest.raises(ValidationError):
			ReplaceFileParams(path="x", old="a")


# ── 阶段二：原子写（tmp + os.replace）──────────────────────────────


class TestReplaceFileAtomicWrite:
	@pytest.mark.asyncio
	async def test_replace_uses_tmp_then_replace(self, tmp_path, monkeypatch):
		# 替换发生前 target 仍是旧内容、tmp 已存在。
		p = tmp_path / "f.txt"
		_seed(p, "foo")
		seen = {}
		real_replace = os.replace

		def spy_replace(src, dst):
			seen["tmp_before"] = os.path.exists(src)
			seen["target_before"] = _read(p)
			return real_replace(src, dst)

		monkeypatch.setattr("tree_walker.tools.actions.os.replace", spy_replace)
		r = await _run({"path": str(p), "old": "foo", "new": "bar"})
		assert r.error is None
		assert seen["tmp_before"] is True
		assert seen["target_before"] == "foo"
		assert _read(p) == "bar"
		assert not os.path.exists(str(p) + ".tmp")

	@pytest.mark.asyncio
	async def test_replace_failure_keeps_original_and_cleans_tmp(self, tmp_path, monkeypatch):
		# os.replace 抛错 → target 完好、无残留 tmp、返回 error。
		p = tmp_path / "f.txt"
		_seed(p, "foo bar")

		def boom(src, dst):
			raise OSError("replace denied")

		monkeypatch.setattr("tree_walker.tools.actions.os.replace", boom)
		r = await _run({"path": str(p), "old": "foo", "new": "baz"})
		assert r.error is not None
		assert "Failed to replace text" in r.error
		# 原文件不被改写（原子性核心）
		assert _read(p) == "foo bar"
		# 无残留 tmp
		assert not os.path.exists(str(p) + ".tmp")


# ── 阶段二：encoding 参数 ──────────────────────────────────────────


class TestReplaceFileEncoding:
	@pytest.mark.asyncio
	async def test_latin1_file_replace(self, tmp_path):
		p = tmp_path / "f.txt"
		with open(p, "wb") as f:
			f.write("café".encode("latin-1"))
		r = await _run({"path": str(p), "old": "é", "new": "x", "encoding": "latin-1"})
		assert r.error is None
		with open(p, "rb") as f:
			assert f.read() == "cafx".encode("latin-1")

	@pytest.mark.asyncio
	async def test_unknown_encoding_returns_lookup_error(self, tmp_path):
		p = tmp_path / "f.txt"
		_seed(p, "abc")
		r = await _run({"path": str(p), "old": "a", "new": "b", "encoding": "no-such-codec"})
		assert r.error is not None
		assert "Unknown encoding" in r.error


# ── 阶段二：allowed_write_paths 白名单 ─────────────────────────────


class TestReplaceFileWhitelist:
	@pytest.mark.asyncio
	async def test_blocked_outside_whitelist(self, tmp_path):
		tools = Tools(allowed_write_paths=[str(tmp_path / "safe")])
		outside = tmp_path / "outside.txt"
		_seed(outside, "abc")  # 文件已存在（直接在 tmp_path 下）
		r = await tools.execute("replace_file", {"path": str(outside), "old": "a", "new": "b"}, MagicMock())
		assert r.error is not None
		assert "not in allowed write paths" in r.error
		# 文件未被改动
		assert _read(outside) == "abc"
