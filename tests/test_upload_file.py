"""Tests for upload_file: element lookup, path validation, success echo,
target-resolution (single input / page-selected / honest dialog error),
accept-attribute soft check, and error mapping.

Covers the action layer (Tools._action_upload_file), mirroring tests/test_input_text.py:
- success echo: returns 'Uploaded 'name' to [TAG] ...' in extracted_content +
  long_term_memory
- target resolution — when the indexed element is not an <input type='file'>:
  * exactly one file input on the page -> use it directly, note 'only file input'
  * several file inputs -> click the target and capture Page.fileChooserOpened's
    backendNodeId (browser.discover_file_input_via_click); upload there, note
    'the file input the page opened'
  * several inputs but no chooser fired (custom dialog) -> honest, actionable
    error guiding the agent to drive the dialog (issue #34 Bug 2: never guess
    among indistinguishable hidden inputs)
- accept soft check: extension not matching the input's accept appends a ℹ️ Note
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
from tree_walker.tools.actions import Tools, _file_matches_accept, _find_upload_label_near


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


def _make_browser(
	*, set_side_effect=None, discover_return: int | None = None,
) -> MagicMock:
	"""Stub BrowserSession for action-layer tests (does NOT touch CDP).

	discover_return seeds browser.discover_file_input_via_click's return value
	(None by default -> the multi-input 'no chooser' honest-error path).
	"""
	bs = MagicMock()
	bs.current_session_id = "sid"
	bs.current_target_id = "tid"
	bs.highlight_element = AsyncMock()
	if set_side_effect is not None:
		bs.set_file_input = AsyncMock(side_effect=set_side_effect)
	else:
		bs.set_file_input = AsyncMock()
	bs.discover_file_input_via_click = AsyncMock(return_value=discover_return)
	bs.get_state = AsyncMock(return_value=_make_state({}))
	return bs


def _node(
	*, bid: int, name: str, attributes: dict[str, str] | None = None,
	node_value: str = "", children=None, parent=None,
) -> EnhancedDOMTreeNode:
	"""Build a tree-shaped EnhancedDOMTreeNode (tag_name derives from node_name).

	Used for _find_upload_label_near tests: the helper walks children_nodes /
	parent_node, which _make_entry leaves empty.
	"""
	n = EnhancedDOMTreeNode(
		node_id=bid,
		backend_node_id=bid,
		node_type=NodeType.ELEMENT_NODE,
		node_name=name,
		node_value=node_value,
		attributes=attributes or {},
		parent_node=parent,
		children_nodes=list(children) if children else None,
	)
	return n


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


# ── Target resolution: single / page-selected / honest dialog error ───────────


class TestUploadFileResolution:
	@pytest.mark.asyncio
	async def test_single_file_input_used_directly_no_discover(self, tmp_upload):
		# Exactly one file input on the page -> use it directly; discover NOT called.
		btn = _make_entry(tag="BUTTON", backend_node_id=3)
		fin = _make_entry(backend_node_id=9, attributes={"type": "file"})
		state = _make_state({3: btn}, file_input_backend_ids=[9])
		state.dom_state.selector_map[9] = fin
		browser = _make_browser()

		result = await Tools().execute(
			"upload_file", {"index": 3, "path": tmp_upload}, browser, browser_state=state,
		)

		assert result.error is None
		assert "[BUTTON]" in result.extracted_content
		assert "only file input on the page" in result.extracted_content
		assert "backendNodeId=9" in result.extracted_content
		browser.discover_file_input_via_click.assert_not_awaited()
		browser.set_file_input.assert_awaited_once()
		assert browser.set_file_input.call_args.kwargs["backend_node_id"] == 9
		assert browser.set_file_input.call_args.kwargs["file_input_backend_ids"] == [9]

	@pytest.mark.asyncio
	async def test_no_file_inputs_returns_error(self, tmp_upload):
		btn = _make_entry(tag="BUTTON", backend_node_id=3)
		state = _make_state({3: btn}, file_input_backend_ids=[])
		browser = _make_browser()

		result = await Tools().execute(
			"upload_file", {"index": 3, "path": tmp_upload}, browser, browser_state=state,
		)

		assert result.error is not None and "no file input found" in result.error
		browser.set_file_input.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_single_input_unresolvable_skips_accept_note(self, tmp_upload):
		# file input backend id not in selector_map -> accept lookup misses,
		# resolution note still present, no accept note.
		btn = _make_entry(tag="BUTTON", backend_node_id=3)
		state = _make_state({3: btn}, file_input_backend_ids=[9])  # 9 not in selector_map
		result = await Tools().execute(
			"upload_file", {"index": 3, "path": tmp_upload}, _make_browser(), browser_state=state,
		)
		assert result.error is None
		assert "only file input on the page" in result.extracted_content
		assert "accept=" not in result.extracted_content


# ── Multi-input discovery (issue #34 Bug 2) ───────────────────────────────────


class TestUploadFileDiscover:
	"""When several (indistinguishable) file inputs exist, upload_file does not
	guess: it clicks the target and uploads to whatever input the page opens
	(Page.fileChooserOpened). No chooser -> honest, actionable error."""

	@pytest.mark.asyncio
	async def test_multi_input_discover_hit_uses_discovered(self, tmp_upload):
		btn = _make_entry(tag="BUTTON", backend_node_id=3)
		state = _make_state({3: btn}, file_input_backend_ids=[10, 9])
		# page wiring reveals input 9 (e.g. the vertical cover slot)
		browser = _make_browser(discover_return=9)

		result = await Tools().execute(
			"upload_file", {"index": 3, "path": tmp_upload}, browser, browser_state=state,
		)

		assert result.error is None
		browser.discover_file_input_via_click.assert_awaited_once_with(3)
		browser.set_file_input.assert_awaited_once()
		assert browser.set_file_input.call_args.kwargs["backend_node_id"] == 9
		assert "the file input the page opened" in result.extracted_content
		assert "backendNodeId=9" in result.extracted_content

	@pytest.mark.asyncio
	async def test_multi_input_discover_miss_returns_honest_error(self, tmp_upload):
		# Custom dialog opened (no chooser) -> do NOT guess; guide the agent.
		btn = _make_entry(tag="BUTTON", backend_node_id=3)
		state = _make_state({3: btn}, file_input_backend_ids=[10, 9])
		browser = _make_browser(discover_return=None)

		result = await Tools().execute(
			"upload_file", {"index": 3, "path": tmp_upload}, browser, browser_state=state,
		)

		assert result.error is not None
		assert "custom upload dialog" in result.error
		assert "upload_file again" in result.error  # actionable guidance
		browser.set_file_input.assert_not_awaited()
		browser.discover_file_input_via_click.assert_awaited_once_with(3)

	@pytest.mark.asyncio
	async def test_multi_input_discover_clips_guessing_on_miss(self, tmp_upload):
		# Regression guard for Bug 2: on a discover miss, we must NOT fall back to
		# file_input_ids[0] (the old behavior that always picked the first input).
		btn = _make_entry(tag="BUTTON", backend_node_id=3)
		state = _make_state({3: btn}, file_input_backend_ids=[10, 9])
		browser = _make_browser(discover_return=None)

		result = await Tools().execute(
			"upload_file", {"index": 3, "path": tmp_upload}, browser, browser_state=state,
		)

		assert result.error is not None
		browser.set_file_input.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_multi_input_clicks_upload_label_not_the_target(self, tmp_upload):
		# issue #34 root mechanism: clicking a <label> (native semantics) fires
		# Page.fileChooserOpened; clicking the drag-area/div/button does not. So
		# discover must be called with the nearby <label>'s bid, not the target's.
		label = _node(bid=77, name="LABEL",
		              attributes={"class": "upload-btn-xyz"}, node_value="选择文件")
		drag = _node(bid=3, name="DIV", attributes={"class": "semi-upload-drag-area"})
		container = _node(bid=200, name="DIV", children=[drag, label])
		drag.parent_node = container
		label.parent_node = container
		state = _make_state({3: drag}, file_input_backend_ids=[10, 9])
		browser = _make_browser(discover_return=9)

		result = await Tools().execute(
			"upload_file", {"index": 3, "path": tmp_upload}, browser, browser_state=state,
		)

		assert result.error is None
		# discover called with the LABEL bid (77), not the drag-area bid (3)
		browser.discover_file_input_via_click.assert_awaited_once_with(77)
		browser.set_file_input.assert_awaited_once()
		assert browser.set_file_input.call_args.kwargs["backend_node_id"] == 9

	@pytest.mark.asyncio
	async def test_file_input_target_among_many_warns_but_uploads(self, tmp_upload):
		# Several file inputs + the agent targeted one directly -> still upload to it
		# (trust the agent's index), but attach a soft warning: cover editors like
		# Douyin have unrelated inputs (收藏封面 favorite-cover); if the site reacts
		# wrongly the agent retries on the correct visible area. Hard-refusing caused
		# 0% success on Douyin (no <label>, so "click visible button" never worked).
		hidden = _make_entry(backend_node_id=7, attributes={"type": "file"})
		state = _make_state({7: hidden}, file_input_backend_ids=[7, 8, 9])
		browser = _make_browser()

		result = await Tools().execute(
			"upload_file", {"index": 7, "path": tmp_upload}, browser, browser_state=state,
		)

		# Upload still happens, to the agent-specified input.
		assert result.error is None
		browser.set_file_input.assert_awaited_once()
		assert browser.set_file_input.call_args.kwargs["backend_node_id"] == 7
		browser.discover_file_input_via_click.assert_not_awaited()
		# ...but with a soft warning about multiple file inputs.
		assert result.extracted_content is not None
		assert "file inputs" in result.extracted_content
		assert "⚠️" in result.extracted_content

	@pytest.mark.asyncio
	async def test_file_input_target_when_only_one_sets_directly(self, tmp_upload):
		# Single file input on the page + agent targeted it -> direct set (normal
		# site; the multi-input guard must not regress the common case).
		entry = _make_entry(backend_node_id=7, attributes={"type": "file"})
		state = _make_state({7: entry}, file_input_backend_ids=[7])
		browser = _make_browser()

		result = await Tools().execute(
			"upload_file", {"index": 7, "path": tmp_upload}, browser, browser_state=state,
		)

		assert result.error is None
		browser.set_file_input.assert_awaited_once()
		assert browser.set_file_input.call_args.kwargs["backend_node_id"] == 7
		browser.discover_file_input_via_click.assert_not_awaited()


# ── _find_upload_label_near predicate ─────────────────────────────────────────


class TestFindUploadLabelNear:
	"""Direct unit tests for _find_upload_label_near: locating the <label> upload
	trigger whose native click fires Page.fileChooserOpened (issue #34)."""

	def test_finds_sibling_upload_label_by_class(self):
		label = _node(bid=77, name="LABEL", attributes={"class": "upload-btn-PdfuUv"})
		drag = _node(bid=3, name="DIV", attributes={"class": "semi-upload-drag-area"})
		container = _node(bid=200, name="DIV", children=[drag, label])
		drag.parent_node = container
		label.parent_node = container
		assert _find_upload_label_near(drag) == 77

	def test_finds_upload_label_by_text(self):
		label = _node(bid=77, name="LABEL", node_value="选择文件")
		drag = _node(bid=3, name="DIV")
		container = _node(bid=200, name="DIV", children=[drag, label])
		drag.parent_node = container
		label.parent_node = container
		assert _find_upload_label_near(drag) == 77

	def test_returns_node_bid_when_target_is_the_label(self):
		label = _node(bid=77, name="LABEL", attributes={"class": "upload-btn"})
		assert _find_upload_label_near(label) == 77

	def test_finds_label_deeper_in_subtree(self):
		label = _node(bid=77, name="LABEL", attributes={"class": "upload"})
		inner = _node(bid=50, name="SPAN", children=[label])
		label.parent_node = inner
		drag = _node(bid=3, name="DIV")
		container = _node(bid=200, name="DIV", children=[drag, inner])
		drag.parent_node = container
		inner.parent_node = container
		assert _find_upload_label_near(drag) == 77

	def test_no_label_returns_none(self):
		drag = _node(bid=3, name="DIV")
		container = _node(bid=200, name="DIV", children=[drag])
		drag.parent_node = container
		assert _find_upload_label_near(drag) is None

	def test_ignores_unrelated_plain_label(self):
		# A <label> without upload class/text is not an upload trigger.
		plain = _node(bid=77, name="LABEL", attributes={"class": "form-field"},
		              node_value="用户名")
		drag = _node(bid=3, name="DIV")
		container = _node(bid=200, name="DIV", children=[drag, plain])
		drag.parent_node = container
		plain.parent_node = container
		assert _find_upload_label_near(drag) is None


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
		# neutral informational wording — explicitly NOT alarmist ("may reject"
		# is gone) so the agent isn't nudged into retrying on another index.
		assert "may reject" not in result.extracted_content
		assert "do not enforce accept" in result.extracted_content

	@pytest.mark.asyncio
	async def test_no_accept_attr_no_note(self, tmp_upload):
		entry = _make_entry(attributes={"type": "file"})
		state = _make_state({1: entry})
		result = await Tools().execute(
			"upload_file", {"index": 1, "path": tmp_upload}, _make_browser(), browser_state=state,
		)
		assert "accept=" not in result.extracted_content

	@pytest.mark.asyncio
	async def test_resolution_and_accept_both_notes(self, tmp_path):
		# non-file-input (single) + accept mismatch -> resolution note + accept note
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
		assert "only file input on the page" in result.extracted_content
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


# ── accept never blocks the upload ────────────────────────────────────────────


class TestAcceptNeverBlocks:
	"""The accept soft-check NEVER blocks the upload: on mismatch the file is still
	set on the input and no error is returned — only an informational note."""

	@pytest.mark.asyncio
	async def test_mismatch_still_calls_set_file_input(self, tmp_path):
		p = tmp_path / "notes.txt"
		p.write_bytes(b"x")
		entry = _make_entry(attributes={"type": "file", "accept": ".pdf"})
		state = _make_state({1: entry})
		browser = _make_browser()
		result = await Tools().execute(
			"upload_file", {"index": 1, "path": str(p)}, browser, browser_state=state,
		)
		assert result.error is None
		browser.set_file_input.assert_awaited_once()
		assert browser.set_file_input.call_args.kwargs["backend_node_id"] == entry.backend_node_id
		assert "do not enforce accept" in result.extracted_content
