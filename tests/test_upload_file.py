"""Tests for upload_file: element lookup, path validation, success echo,
target-substitution note, accept-attribute soft check, and error mapping.

Covers the action layer (Tools._action_upload_file), mirroring tests/test_input_text.py:
- success echo: returns 'Uploaded 'name' to [TAG] ...' in extracted_content +
  long_term_memory (mirrors navigate/go_back/click/input_text style)
- target substitution: when the indexed element is not an <input type='file'>,
  the echo describes the picked element and appends a ⚠️ Note that the upload
  went to the nearest file input instead (no longer silent)
- accept soft check: extension not matching the input's accept appends a ⚠️ Note
  (non-blocking; covers extension / wildcard MIME / full MIME branches)
- error mapping: set_file_input raising -> friendly 'File upload failed:'
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from tree_walker.browser.views import (
	BrowserStateSummary,
	EnhancedDOMTreeNode,
	NodeType,
	SerializedDOMState,
)
from tree_walker.tools.actions import Tools, _file_matches_accept


# ── Shared helpers ────────────────────────────────────────────────────────────


def _make_entry(
	*,
	tag: str = "INPUT",
	backend_node_id: int = 42,
	attributes: dict[str, str] | None = None,
	node_value: str = "",
) -> EnhancedDOMTreeNode:
	"""A minimal EnhancedDOMTreeNode for selector_map entries."""
	return EnhancedDOMTreeNode(
		node_id=backend_node_id,
		backend_node_id=backend_node_id,
		node_type=NodeType.ELEMENT_NODE,
		node_name=tag.upper(),
		node_value=node_value,
		attributes=attributes or {},
	)


def _make_state(
	selector_map: dict[int, EnhancedDOMTreeNode],
	*,
	file_input_backend_ids: list[int] | None = None,
) -> BrowserStateSummary:
	"""Build a BrowserStateSummary with the given selector_map + file inputs."""
	return BrowserStateSummary(
		url="https://example.com",
		title="",
		dom_state=SerializedDOMState(
			_root=None,
			selector_map=selector_map,
			element_tree_text="",
			file_input_backend_ids=file_input_backend_ids or [],
		),
	)


def _make_browser(*, set_side_effect=None) -> MagicMock:
	"""Stub BrowserSession for action-layer tests (does NOT touch CDP)."""
	bs = MagicMock()
	bs.current_session_id = "sid"
	bs.current_target_id = "tid"
	bs.highlight_element = AsyncMock()
	if set_side_effect is not None:
		bs.set_file_input = AsyncMock(side_effect=set_side_effect)
	else:
		bs.set_file_input = AsyncMock()
	bs.get_state = AsyncMock(return_value=_make_state({}))
	return bs


@pytest.fixture
def tmp_upload(tmp_path):
	"""A real non-empty temp file that passes os.path.isfile / getsize checks."""
	p = tmp_path / "sample.png"
	p.write_bytes(b"png-bytes")
	return str(p)


# ── Element lookup ────────────────────────────────────────────────────────────


class TestUploadFileElementLookup:
	@pytest.mark.asyncio
	async def test_file_input_index_calls_highlight_and_set(self, tmp_upload):
		entry = _make_entry(backend_node_id=7, attributes={"type": "file"})
		state = _make_state({5: entry})
		browser = _make_browser()

		result = await Tools().execute(
			"upload_file", {"index": 5, "path": tmp_upload}, browser, browser_state=state,
		)

		assert result.error is None
		browser.highlight_element.assert_awaited_once_with(7)
		browser.set_file_input.assert_awaited_once()
		assert browser.set_file_input.call_args.kwargs["backend_node_id"] == 7
		# direct file input: no candidate list forwarded to the session layer
		assert browser.set_file_input.call_args.kwargs["file_input_backend_ids"] is None

	@pytest.mark.asyncio
	async def test_missing_index_returns_error_without_upload(self, tmp_upload):
		state = _make_state({})  # index 5 absent
		browser = _make_browser()

		result = await Tools().execute(
			"upload_file", {"index": 5, "path": tmp_upload}, browser, browser_state=state,
		)

		assert result.error is not None and "not found" in result.error
		browser.highlight_element.assert_not_awaited()
		browser.set_file_input.assert_not_awaited()


# ── Path validation ───────────────────────────────────────────────────────────


class TestUploadFilePathValidation:
	@pytest.mark.asyncio
	async def test_file_not_found(self):
		entry = _make_entry(attributes={"type": "file"})
		state = _make_state({1: entry})
		browser = _make_browser()

		result = await Tools().execute(
			"upload_file", {"index": 1, "path": r"Z:\nope\missing.png"},
			browser, browser_state=state,
		)

		assert result.error is not None and result.error.startswith("File not found")
		browser.set_file_input.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_empty_file(self, tmp_path):
		p = tmp_path / "empty.png"
		p.write_bytes(b"")  # 0 bytes
		entry = _make_entry(attributes={"type": "file"})
		state = _make_state({1: entry})
		browser = _make_browser()

		result = await Tools().execute(
			"upload_file", {"index": 1, "path": str(p)}, browser, browser_state=state,
		)

		assert result.error is not None and result.error.startswith("File is empty")
		browser.set_file_input.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_path_not_in_allowed(self, tmp_upload, tmp_path):
		entry = _make_entry(attributes={"type": "file"})
		state = _make_state({1: entry})
		browser = _make_browser()
		tools = Tools(allowed_upload_paths=[str(tmp_path / "elsewhere")])

		result = await tools.execute(
			"upload_file", {"index": 1, "path": tmp_upload}, browser, browser_state=state,
		)

		assert result.error is not None and "not in allowed upload paths" in result.error
		browser.set_file_input.assert_not_awaited()


# ── Success echo ──────────────────────────────────────────────────────────────


class TestUploadFileEcho:
	@pytest.mark.asyncio
	async def test_echo_includes_basename_and_label(self, tmp_upload):
		entry = _make_entry(attributes={"type": "file", "aria-label": "avatar"})
		state = _make_state({5: entry})
		browser = _make_browser()

		result = await Tools().execute(
			"upload_file", {"index": 5, "path": tmp_upload}, browser, browser_state=state,
		)

		assert result.error is None
		bn = os.path.basename(tmp_upload)
		assert result.extracted_content == f"Uploaded {bn!r} to [INPUT] 'avatar' at index 5"
		assert result.extracted_content == result.long_term_memory
		assert result.success is None and result.is_done is False

	@pytest.mark.asyncio
	async def test_echo_bare_no_attrs(self, tmp_upload):
		entry = _make_entry(attributes={"type": "file"})
		state = _make_state({5: entry})
		browser = _make_browser()

		result = await Tools().execute(
			"upload_file", {"index": 5, "path": tmp_upload}, browser, browser_state=state,
		)

		bn = os.path.basename(tmp_upload)
		assert result.extracted_content == f"Uploaded {bn!r} to [INPUT] at index 5"

	@pytest.mark.asyncio
	async def test_echo_uses_basename_not_full_path(self, tmp_path):
		nested = tmp_path / "deep" / "dir"
		nested.mkdir(parents=True)
		p = nested / "report.pdf"
		p.write_bytes(b"%PDF-1.4")
		entry = _make_entry(attributes={"type": "file"})
		state = _make_state({2: entry})
		browser = _make_browser()

		result = await Tools().execute(
			"upload_file", {"index": 2, "path": str(p)}, browser, browser_state=state,
		)

		assert result.extracted_content == "Uploaded 'report.pdf' to [INPUT] at index 2"
		assert "deep" not in result.extracted_content
		assert "dir" not in result.extracted_content

	@pytest.mark.asyncio
	async def test_echo_uses_name_when_no_aria_label(self, tmp_upload):
		entry = _make_entry(attributes={"type": "file", "name": "avatar_file"})
		state = _make_state({5: entry})
		result = await Tools().execute(
			"upload_file", {"index": 5, "path": tmp_upload}, _make_browser(), browser_state=state,
		)
		bn = os.path.basename(tmp_upload)
		assert result.extracted_content == f"Uploaded {bn!r} to [INPUT] 'avatar_file' at index 5"

	@pytest.mark.asyncio
	async def test_echo_uses_node_value(self, tmp_upload):
		entry = _make_entry(attributes={"type": "file"}, node_value="Choose file")
		state = _make_state({5: entry})
		result = await Tools().execute(
			"upload_file", {"index": 5, "path": tmp_upload}, _make_browser(), browser_state=state,
		)
		bn = os.path.basename(tmp_upload)
		assert result.extracted_content == f"Uploaded {bn!r} to [INPUT] 'Choose file' at index 5"


# ── Target substitution ───────────────────────────────────────────────────────


class TestUploadFileSubstitution:
	@pytest.mark.asyncio
	async def test_non_file_input_appends_substitution_note(self, tmp_upload):
		btn = _make_entry(tag="BUTTON", backend_node_id=3)
		fin = _make_entry(backend_node_id=9, attributes={"type": "file"})
		state = _make_state({3: btn}, file_input_backend_ids=[9])
		state.dom_state.selector_map[9] = fin  # resolvable for accept lookup
		browser = _make_browser()

		result = await Tools().execute(
			"upload_file", {"index": 3, "path": tmp_upload}, browser, browser_state=state,
		)

		assert result.error is None
		# echo describes the picked BUTTON, plus a substitution note
		assert "[BUTTON]" in result.extracted_content
		assert "not an <input type='file'>" in result.extracted_content
		assert "index 3" in result.extracted_content
		# upload went to the resolved file input (9), not the button (3)
		browser.set_file_input.assert_awaited_once()
		assert browser.set_file_input.call_args.kwargs["backend_node_id"] == 9
		assert browser.set_file_input.call_args.kwargs["file_input_backend_ids"] == [9]

	@pytest.mark.asyncio
	async def test_non_file_input_no_candidates_returns_error(self, tmp_upload):
		btn = _make_entry(tag="BUTTON", backend_node_id=3)
		state = _make_state({3: btn}, file_input_backend_ids=[])
		browser = _make_browser()

		result = await Tools().execute(
			"upload_file", {"index": 3, "path": tmp_upload}, browser, browser_state=state,
		)

		assert result.error is not None and "no file input found" in result.error
		browser.set_file_input.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_substitution_unresolvable_target_skips_accept_note(self, tmp_upload):
		# file input backend id is NOT in the interactive selector_map (e.g. hidden):
		# _find_node_by_backend_id returns None -> no accept note, substitution note remains
		btn = _make_entry(tag="BUTTON", backend_node_id=3)
		state = _make_state({3: btn}, file_input_backend_ids=[9])  # 9 not in selector_map
		result = await Tools().execute(
			"upload_file", {"index": 3, "path": tmp_upload}, _make_browser(), browser_state=state,
		)
		assert result.error is None
		assert "not an <input type='file'>" in result.extracted_content
		assert "accept=" not in result.extracted_content


# ── accept soft check ─────────────────────────────────────────────────────────


class TestUploadFileAccept:
	@pytest.mark.asyncio
	async def test_accept_ext_match_no_note(self, tmp_upload):
		entry = _make_entry(attributes={"type": "file", "accept": ".png"})
		state = _make_state({1: entry})
		result = await Tools().execute(
			"upload_file", {"index": 1, "path": tmp_upload}, _make_browser(), browser_state=state,
		)
		assert result.error is None
		assert "accept=" not in result.extracted_content

	@pytest.mark.asyncio
	async def test_accept_full_mime_match_no_note(self, tmp_upload):
		entry = _make_entry(attributes={"type": "file", "accept": "image/png"})
		state = _make_state({1: entry})
		result = await Tools().execute(
			"upload_file", {"index": 1, "path": tmp_upload}, _make_browser(), browser_state=state,
		)
		assert "accept=" not in result.extracted_content

	@pytest.mark.asyncio
	async def test_accept_wildcard_match_no_note(self, tmp_upload):
		entry = _make_entry(attributes={"type": "file", "accept": "image/*"})
		state = _make_state({1: entry})
		result = await Tools().execute(
			"upload_file", {"index": 1, "path": tmp_upload}, _make_browser(), browser_state=state,
		)
		assert "accept=" not in result.extracted_content

	@pytest.mark.asyncio
	async def test_accept_ext_mismatch_appends_note(self, tmp_path):
		p = tmp_path / "notes.txt"
		p.write_bytes(b"x")
		entry = _make_entry(attributes={"type": "file", "accept": ".pdf"})
		state = _make_state({1: entry})
		result = await Tools().execute(
			"upload_file", {"index": 1, "path": str(p)}, _make_browser(), browser_state=state,
		)
		assert result.error is None
		assert "accept='.pdf'" in result.extracted_content
		assert "may reject" in result.extracted_content

	@pytest.mark.asyncio
	async def test_no_accept_attr_no_note(self, tmp_upload):
		entry = _make_entry(attributes={"type": "file"})
		state = _make_state({1: entry})
		result = await Tools().execute(
			"upload_file", {"index": 1, "path": tmp_upload}, _make_browser(), browser_state=state,
		)
		assert "accept=" not in result.extracted_content

	@pytest.mark.asyncio
	async def test_substitution_and_accept_both_notes(self, tmp_path):
		p = tmp_path / "notes.txt"
		p.write_bytes(b"x")
		btn = _make_entry(tag="BUTTON", backend_node_id=3)
		fin = _make_entry(
			backend_node_id=9, attributes={"type": "file", "accept": ".pdf"},
		)
		state = _make_state({3: btn}, file_input_backend_ids=[9])
		state.dom_state.selector_map[9] = fin
		result = await Tools().execute(
			"upload_file", {"index": 3, "path": str(p)}, _make_browser(), browser_state=state,
		)
		assert result.error is None
		assert "not an <input type='file'>" in result.extracted_content
		assert "accept='.pdf'" in result.extracted_content


# ── Error mapping ─────────────────────────────────────────────────────────────


class TestUploadFileErrorMapping:
	@pytest.mark.asyncio
	async def test_set_file_input_raises_maps_to_error(self, tmp_upload):
		entry = _make_entry(attributes={"type": "file"})
		state = _make_state({1: entry})
		browser = _make_browser(set_side_effect=RuntimeError("CDP down"))

		result = await Tools().execute(
			"upload_file", {"index": 1, "path": tmp_upload}, browser, browser_state=state,
		)

		assert result.error is not None and result.error.startswith("File upload failed:")
		assert "CDP down" in result.error


# ── _file_matches_accept predicate ────────────────────────────────────────────


class TestFileMatchesAccept:
	"""Direct unit tests for the _file_matches_accept predicate.

	The action layer short-circuits on a falsy accept (``if accept_attr and ...``),
	so the empty/None-accept and empty-token branches are exercised directly here.
	"""

	def test_none_accept_allows_anything(self):
		assert _file_matches_accept("notes.txt", None) is True

	def test_empty_or_blank_accept_allows_anything(self):
		assert _file_matches_accept("notes.txt", "") is True
		assert _file_matches_accept("notes.txt", "   ") is True

	def test_empty_tokens_between_commas_are_skipped(self):
		# trailing / blank tokens are skipped; .png still matches
		assert _file_matches_accept("photo.png", ".pdf,,  ,.png") is True

	def test_no_match_returns_false(self):
		assert _file_matches_accept("notes.txt", ".png,.jpg") is False
