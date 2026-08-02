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
	file_inputs_meta: list | None = None,
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
			file_inputs_meta=file_inputs_meta or [],
		),
	)


def _make_browser(
	*, set_side_effect=None, discover_return: int | None = None,
	execute_js_side_effect=None,
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
	if execute_js_side_effect is None:
		bs.execute_js = AsyncMock(return_value='{"canvases": 0, "imgPreviews": 0}')
	else:
		bs.execute_js = AsyncMock(side_effect=execute_js_side_effect)
	# #151：upload 线索采集在目标元素自身提取上下文（eval_function_on_node）。默认返 None →
	# 既有 verify/echo 测试 capture 静默无线索（不消费 execute_js mock 序列）；采集测试覆写此值。
	bs.eval_function_on_node = AsyncMock(return_value=None)
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

		result = await Tools(upload_verify_enabled=False).execute(
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

		result = await Tools(upload_verify_enabled=False).execute(
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

		result = await Tools(upload_verify_enabled=False).execute(
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

		result = await Tools(upload_verify_enabled=False).execute(
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

		result = await Tools(upload_verify_enabled=False).execute(
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

		result = await Tools(upload_verify_enabled=False).execute(
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

		result = await Tools(upload_verify_enabled=False).execute(
			"upload_file", {"index": 2, "path": str(p)}, browser, browser_state=state,
		)

		assert result.extracted_content == "Uploaded 'report.pdf' to [INPUT] at index 2"
		assert "deep" not in result.extracted_content
		assert "dir" not in result.extracted_content

	@pytest.mark.asyncio
	async def test_echo_uses_name_when_no_aria_label(self, tmp_upload):
		entry = _make_entry(attributes={"type": "file", "name": "avatar_file"})
		state = _make_state({5: entry})
		result = await Tools(upload_verify_enabled=False).execute(
			"upload_file", {"index": 5, "path": tmp_upload}, _make_browser(), browser_state=state,
		)
		bn = os.path.basename(tmp_upload)
		assert result.extracted_content == f"Uploaded {bn!r} to [INPUT] 'avatar_file' at index 5"

	@pytest.mark.asyncio
	async def test_echo_uses_node_value(self, tmp_upload):
		entry = _make_entry(attributes={"type": "file"}, node_value="Choose file")
		state = _make_state({5: entry})
		result = await Tools(upload_verify_enabled=False).execute(
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

		result = await Tools(upload_verify_enabled=False).execute(
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

		result = await Tools(upload_verify_enabled=False).execute(
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
		result = await Tools(upload_verify_enabled=False).execute(
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

		result = await Tools(upload_verify_enabled=False).execute(
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

		result = await Tools(upload_verify_enabled=False).execute(
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

		result = await Tools(upload_verify_enabled=False).execute(
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

		result = await Tools(upload_verify_enabled=False).execute(
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

		result = await Tools(upload_verify_enabled=False).execute(
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
	async def test_file_input_target_among_many_names_live_candidates(self, tmp_upload):
		# 多 file input 时，软警告应点名「可见 + upload 容器内」的候选 input，
		# 让 agent 在命中隐藏诱饵后改试正确入口（抖音封面编辑器场景）。
		from tree_walker.browser.views import FileInputInfo
		hidden = _make_entry(backend_node_id=7, attributes={"type": "file"})
		metas = [
			FileInputInfo(backend_node_id=7, visible=False, upload_ancestor=False),  # agent 选的诱饵
			FileInputInfo(backend_node_id=8, visible=True, upload_ancestor=True),    # live 候选
			FileInputInfo(backend_node_id=9, visible=False, upload_ancestor=True),   # 非可见，不进候选
		]
		state = _make_state(
			{7: hidden}, file_input_backend_ids=[7, 8, 9], file_inputs_meta=metas,
		)
		browser = _make_browser()

		result = await Tools(upload_verify_enabled=False).execute(
			"upload_file", {"index": 7, "path": tmp_upload}, browser, browser_state=state,
		)

		assert result.error is None
		# 仍信任 agent 指定的 index（契约不变）
		assert browser.set_file_input.call_args.kwargs["backend_node_id"] == 7
		# 但点名了 live 候选 8（可见 + upload 容器），9 不在候选列表
		assert "Likely-live candidates" in result.extracted_content
		assert "[8]" in result.extracted_content

	@pytest.mark.asyncio
	async def test_replace_input_corrected_to_primary_hidden(self, tmp_upload):
		# Fix C (#96)：抖音 Semi-UI 双 input，agent 选了 replace(替换) → 软纠正到 primary
		# hidden-input(初次上传)。三条件：replace class + primary 候选 + ≥2 upload 祖先。
		from tree_walker.browser.views import FileInputInfo
		replace = _make_entry(
			backend_node_id=7,
			attributes={"type": "file", "class": "semi-upload-hidden-input-replace"},
		)
		primary = _make_entry(
			backend_node_id=8,
			attributes={"type": "file", "class": "semi-upload-hidden-input"},
		)
		metas = [
			FileInputInfo(backend_node_id=7, visible=False, upload_ancestor=True,
						  class_name="semi-upload-hidden-input-replace"),
			FileInputInfo(backend_node_id=8, visible=False, upload_ancestor=True,
						  class_name="semi-upload-hidden-input"),
		]
		state = _make_state(
			{7: replace, 8: primary}, file_input_backend_ids=[7, 8], file_inputs_meta=metas,
		)
		browser = _make_browser()

		result = await Tools(upload_verify_enabled=False).execute(
			"upload_file", {"index": 7, "path": tmp_upload}, browser, browser_state=state,
		)

		assert result.error is None
		# 纠正到 primary hidden-input（8），不是 agent 选的 replace（7）
		assert browser.set_file_input.call_args.kwargs["backend_node_id"] == 8
		assert "Auto-switched" in result.extracted_content

	@pytest.mark.asyncio
	async def test_replace_input_not_corrected_when_single_upload_ancestor(self, tmp_upload):
		# 仅 1 个 upload 祖先（普通"替换头像"单 input 场景）→ 不满足 ≥2 护栏，不纠正。
		from tree_walker.browser.views import FileInputInfo
		replace = _make_entry(
			backend_node_id=7,
			attributes={"type": "file", "class": "semi-upload-hidden-input-replace"},
		)
		primary = _make_entry(
			backend_node_id=8,
			attributes={"type": "file", "class": "semi-upload-hidden-input"},
		)
		metas = [
			FileInputInfo(backend_node_id=7, visible=False, upload_ancestor=True,
						  class_name="semi-upload-hidden-input-replace"),
			FileInputInfo(backend_node_id=8, visible=False, upload_ancestor=False,
						  class_name="semi-upload-hidden-input"),
		]
		state = _make_state(
			{7: replace, 8: primary}, file_input_backend_ids=[7, 8], file_inputs_meta=metas,
		)
		browser = _make_browser()

		result = await Tools(upload_verify_enabled=False).execute(
			"upload_file", {"index": 7, "path": tmp_upload}, browser, browser_state=state,
		)

		assert result.error is None
		# upload_ancestor_count = 1 < 2 → 不纠正，保持 agent 选的 7
		assert browser.set_file_input.call_args.kwargs["backend_node_id"] == 7

	@pytest.mark.asyncio
	async def test_replace_input_not_corrected_when_no_primary_candidate(self, tmp_upload):
		# replace 但无 primary hidden-input 候选 → 不纠正，保持 agent 选的 + 软警告。
		from tree_walker.browser.views import FileInputInfo
		replace_a = _make_entry(
			backend_node_id=7,
			attributes={"type": "file", "class": "semi-upload-hidden-input-replace"},
		)
		replace_b = _make_entry(
			backend_node_id=8,
			attributes={"type": "file", "class": "semi-upload-hidden-input-replace"},
		)
		metas = [
			FileInputInfo(backend_node_id=7, visible=False, upload_ancestor=True,
						  class_name="semi-upload-hidden-input-replace"),
			FileInputInfo(backend_node_id=8, visible=False, upload_ancestor=True,
						  class_name="semi-upload-hidden-input-replace"),
		]
		state = _make_state(
			{7: replace_a, 8: replace_b}, file_input_backend_ids=[7, 8], file_inputs_meta=metas,
		)
		browser = _make_browser()

		result = await Tools(upload_verify_enabled=False).execute(
			"upload_file", {"index": 7, "path": tmp_upload}, browser, browser_state=state,
		)

		assert result.error is None
		# 无 primary 候选 → 不纠正，保持 7
		assert browser.set_file_input.call_args.kwargs["backend_node_id"] == 7

	@pytest.mark.asyncio
	async def test_primary_input_not_corrected(self, tmp_upload):
		# agent 选的本身就是 primary hidden-input（非 replace）→ 不纠正。
		from tree_walker.browser.views import FileInputInfo
		primary = _make_entry(
			backend_node_id=8,
			attributes={"type": "file", "class": "semi-upload-hidden-input"},
		)
		replace = _make_entry(
			backend_node_id=7,
			attributes={"type": "file", "class": "semi-upload-hidden-input-replace"},
		)
		metas = [
			FileInputInfo(backend_node_id=7, visible=False, upload_ancestor=True,
						  class_name="semi-upload-hidden-input-replace"),
			FileInputInfo(backend_node_id=8, visible=False, upload_ancestor=True,
						  class_name="semi-upload-hidden-input"),
		]
		state = _make_state(
			{7: replace, 8: primary}, file_input_backend_ids=[7, 8], file_inputs_meta=metas,
		)
		browser = _make_browser()

		result = await Tools(upload_verify_enabled=False).execute(
			"upload_file", {"index": 8, "path": tmp_upload}, browser, browser_state=state,
		)

		assert result.error is None
		# primary 不纠正，保持 8
		assert browser.set_file_input.call_args.kwargs["backend_node_id"] == 8

	@pytest.mark.asyncio
	async def test_file_input_target_when_only_one_sets_directly(self, tmp_upload):
		# Single file input on the page + agent targeted it -> direct set (normal
		# site; the multi-input guard must not regress the common case).
		entry = _make_entry(backend_node_id=7, attributes={"type": "file"})
		state = _make_state({7: entry}, file_input_backend_ids=[7])
		browser = _make_browser()

		result = await Tools(upload_verify_enabled=False).execute(
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
		result = await Tools(upload_verify_enabled=False).execute(
			"upload_file", {"index": 1, "path": tmp_upload}, _make_browser(), browser_state=state,
		)
		assert result.error is None
		assert "accept=" not in result.extracted_content

	@pytest.mark.asyncio
	async def test_accept_full_mime_match_no_note(self, tmp_upload):
		entry = _make_entry(attributes={"type": "file", "accept": "image/png"})
		state = _make_state({1: entry})
		result = await Tools(upload_verify_enabled=False).execute(
			"upload_file", {"index": 1, "path": tmp_upload}, _make_browser(), browser_state=state,
		)
		assert "accept=" not in result.extracted_content

	@pytest.mark.asyncio
	async def test_accept_wildcard_match_no_note(self, tmp_upload):
		entry = _make_entry(attributes={"type": "file", "accept": "image/*"})
		state = _make_state({1: entry})
		result = await Tools(upload_verify_enabled=False).execute(
			"upload_file", {"index": 1, "path": tmp_upload}, _make_browser(), browser_state=state,
		)
		assert "accept=" not in result.extracted_content

	@pytest.mark.asyncio
	async def test_accept_ext_mismatch_appends_note(self, tmp_path):
		p = tmp_path / "notes.txt"
		p.write_bytes(b"x")
		entry = _make_entry(attributes={"type": "file", "accept": ".pdf"})
		state = _make_state({1: entry})
		result = await Tools(upload_verify_enabled=False).execute(
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
		result = await Tools(upload_verify_enabled=False).execute(
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
		result = await Tools(upload_verify_enabled=False).execute(
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

		result = await Tools(upload_verify_enabled=False).execute(
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
		result = await Tools(upload_verify_enabled=False).execute(
			"upload_file", {"index": 1, "path": str(p)}, browser, browser_state=state,
		)
		assert result.error is None
		browser.set_file_input.assert_awaited_once()
		assert browser.set_file_input.call_args.kwargs["backend_node_id"] == entry.backend_node_id
		assert "do not enforce accept" in result.extracted_content


# ── P1 三次修订：upload_file 页面级验证（canvas/img 探针 + inconclusive 引导）─────


class TestUploadVerification:
	"""upload_file 上传后页面级验证：canvas/img 预览探测 + inconclusive 引导文案。

	参考 docs/agent-loop-optimize/上传失败诊断-P1对文件上传影响分析.md 三次修订 §4。
	"""

	@pytest.mark.asyncio
	async def test_verify_success_canvas_appeared(self, tmp_upload):
		entry = _make_entry(attributes={"type": "file"})
		state = _make_state({5: entry})
		browser = _make_browser(execute_js_side_effect=[
			'{"canvases": 0, "imgPreviews": 0}',   # before
			'{"canvases": 1, "imgPreviews": 0}',   # after — canvas preview appeared
		])
		result = await Tools().execute(
			"upload_file", {"index": 5, "path": tmp_upload}, browser, browser_state=state,
		)
		assert result.error is None
		assert "✅ Upload verified" in result.extracted_content
		assert "new <canvas> preview appeared" in result.extracted_content
		assert "count 0→1" in result.extracted_content

	@pytest.mark.asyncio
	async def test_verify_success_img_preview_appeared(self, tmp_upload):
		entry = _make_entry(attributes={"type": "file"})
		state = _make_state({5: entry})
		browser = _make_browser(execute_js_side_effect=[
			'{"canvases": 0, "imgPreviews": 0}',
			'{"canvases": 0, "imgPreviews": 1}',   # img preview appeared
		])
		result = await Tools().execute(
			"upload_file", {"index": 5, "path": tmp_upload}, browser, browser_state=state,
		)
		assert result.error is None
		assert "✅ Upload verified" in result.extracted_content
		assert "new <img> preview appeared" in result.extracted_content

	@pytest.mark.asyncio
	async def test_verify_inconclusive_no_signal(self, tmp_upload):
		entry = _make_entry(attributes={"type": "file"})
		state = _make_state({5: entry})
		# Polling runs all attempts → bound to 2 polls (0.2s) for test speed; lambda 恒返 0
		# 避免列表耗尽。无 delta → advisory。
		browser = _make_browser(execute_js_side_effect=lambda *a, **k: '{"canvases": 0, "imgPreviews": 0, "bgPreviews": 0}')
		result = await Tools(upload_verify_wait_s=0.2, upload_verify_interval_s=0.1).execute(
			"upload_file", {"index": 5, "path": tmp_upload}, browser, browser_state=state,
		)
		assert result.error is None
		# Advisory must carry the three keywords that break the screenshot loop.
		assert "Do NOT conclude" in result.extracted_content
		assert "placeholder" in result.extracted_content
		assert "screenshot" in result.extracted_content
		assert "✅" not in result.extracted_content

	@pytest.mark.asyncio
	async def test_verify_exception_does_not_block(self, tmp_upload):
		entry = _make_entry(attributes={"type": "file"})
		state = _make_state({5: entry})

		def raise_each(*a, **kw):
			raise RuntimeError("cdp eval blew up")

		browser = _make_browser(execute_js_side_effect=raise_each)
		result = await Tools().execute(
			"upload_file", {"index": 5, "path": tmp_upload}, browser, browser_state=state,
		)
		# Verification raised but upload itself succeeded; advisory path taken.
		assert result.error is None
		assert "Uploaded" in result.extracted_content
		assert "Do NOT conclude" in result.extracted_content

	@pytest.mark.asyncio
	async def test_verify_disabled_no_evidence(self, tmp_upload):
		entry = _make_entry(attributes={"type": "file"})
		state = _make_state({5: entry})
		browser = _make_browser()  # default execute_js, but should never be called
		result = await Tools(upload_verify_enabled=False).execute(
			"upload_file", {"index": 5, "path": tmp_upload}, browser, browser_state=state,
		)
		assert result.error is None
		assert "✅" not in result.extracted_content
		assert "Do NOT conclude" not in result.extracted_content
		# Disabled → no probe at all.
		browser.execute_js.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_verify_probe_failure_treated_inconclusive(self, tmp_upload):
		entry = _make_entry(attributes={"type": "file"})
		state = _make_state({5: entry})
		browser = _make_browser(execute_js_side_effect=[
			'not-json',   # before-probe returns garbage → _probe returns None
		])
		result = await Tools().execute(
			"upload_file", {"index": 5, "path": tmp_upload}, browser, browser_state=state,
		)
		assert result.error is None
		# before=None → advisory, no crash, no second probe needed.
		assert "Do NOT conclude" in result.extracted_content

	@pytest.mark.asyncio
	async def test_verify_after_probe_fails_returns_advisory(self, tmp_upload):
		"""before-probe OK but after-probes raise (e.g. page navigated) → advisory.

		Polling means after-probe is called multiple times → use a function that raises
		on every poll (not a single-error list, which would exhaust).
		"""
		entry = _make_entry(attributes={"type": "file"})
		state = _make_state({5: entry})
		calls = {"n": 0}

		def before_ok_then_raise(*a, **k):
			if calls["n"] == 0:
				calls["n"] += 1
				return '{"canvases": 0, "imgPreviews": 0, "bgPreviews": 0}'
			raise RuntimeError("page navigated during wait")

		browser = _make_browser(execute_js_side_effect=before_ok_then_raise)
		result = await Tools(upload_verify_wait_s=0.2, upload_verify_interval_s=0.1).execute(
			"upload_file", {"index": 5, "path": tmp_upload}, browser, browser_state=state,
		)
		assert result.error is None
		# All after-probes None → advisory, upload still counts as set.
		assert "Do NOT conclude" in result.extracted_content

	@pytest.mark.asyncio
	async def test_verify_probe_dict_return_handled(self, tmp_upload):
		"""execute_js returning a parsed dict (not JSON string) is also accepted."""
		entry = _make_entry(attributes={"type": "file"})
		state = _make_state({5: entry})
		browser = _make_browser(execute_js_side_effect=[
			{"canvases": 0, "imgPreviews": 0},  # dict before
			{"canvases": 1, "imgPreviews": 0},  # dict after — canvas appeared
		])
		result = await Tools().execute(
			"upload_file", {"index": 5, "path": tmp_upload}, browser, browser_state=state,
		)
		assert result.error is None
		assert "✅ Upload verified" in result.extracted_content

	@pytest.mark.asyncio
	async def test_verify_success_bg_image_appeared(self, tmp_upload):
		"""background-image preview delta → ✅ (四次修订扩的信号)."""
		entry = _make_entry(attributes={"type": "file"})
		state = _make_state({5: entry})
		browser = _make_browser(execute_js_side_effect=[
			'{"canvases": 0, "imgPreviews": 0, "bgPreviews": 0}',
			'{"canvases": 0, "imgPreviews": 0, "bgPreviews": 1}',  # bg-image appeared
		])
		result = await Tools().execute(
			"upload_file", {"index": 5, "path": tmp_upload}, browser, browser_state=state,
		)
		assert result.error is None
		assert "✅ Upload verified" in result.extracted_content
		assert "background-image preview appeared" in result.extracted_content

	@pytest.mark.asyncio
	async def test_verify_polling_early_exit(self, tmp_upload):
		"""Poll 1 no delta, poll 2 delta → ✅ and stops polling (no further probe)."""
		entry = _make_entry(attributes={"type": "file"})
		state = _make_state({5: entry})
		browser = _make_browser(execute_js_side_effect=[
			'{"canvases": 0, "imgPreviews": 0, "bgPreviews": 0}',  # before
			'{"canvases": 0, "imgPreviews": 0, "bgPreviews": 0}',  # poll 1: no delta
			'{"canvases": 1, "imgPreviews": 0, "bgPreviews": 0}',  # poll 2: delta → ✅
		])
		result = await Tools(upload_verify_wait_s=1.0, upload_verify_interval_s=0.1).execute(
			"upload_file", {"index": 5, "path": tmp_upload}, browser, browser_state=state,
		)
		assert result.error is None
		assert "✅ Upload verified" in result.extracted_content
		assert "new <canvas>" in result.extracted_content
		# before-probe + 2 polls = 3 execute_js calls; early-exit means no 4th.
		assert browser.execute_js.call_count == 3


class TestUploadClueCapture:
	"""#151：upload_file 在多 file input 页（需消歧）采集语义线索 → ActionResult.metadata['upload_clue']，
	与手工录制 _store_upload_clue 同形 → agent 录制历史带 _semantic_clue，重放走 _match_file_upload_by_clue。"""

	@pytest.mark.asyncio
	async def test_captures_clue_with_container_rect_multi_input(self, tmp_upload):
		entry7 = _make_entry(backend_node_id=7, attributes={"type": "file", "accept": "image/png"})
		entry8 = _make_entry(backend_node_id=8, attributes={"type": "file", "accept": "image/png"})
		state = _make_state({5: entry7, 6: entry8}, file_input_backend_ids=[7, 8])
		# #151：capture 在目标元素自身提取上下文（eval_function_on_node, this=该 input）
		browser = _make_browser()
		browser.eval_function_on_node = AsyncMock(return_value={
			"accept": "image/png", "region_text": "点击上传", "in_dialog": True,
			"container_rect": {"x": 10, "y": 100, "width": 200, "height": 120},
		})
		result = await Tools(upload_verify_enabled=False).execute(
			"upload_file", {"index": 5, "path": tmp_upload}, browser, browser_state=state,
		)
		assert result.error is None
		assert result.metadata and result.metadata.get("upload_clue")
		clue = result.metadata["upload_clue"]
		assert clue["container_rect"]["x"] == 10  # 命中 backend_id=7 的元素 ctx
		assert clue["accept"] == "image/png"
		assert clue["in_dialog"] is True

	@pytest.mark.asyncio
	async def test_capture_failure_does_not_block_upload(self, tmp_upload):
		entry7 = _make_entry(backend_node_id=7, attributes={"type": "file", "accept": "image/png"})
		entry8 = _make_entry(backend_node_id=8, attributes={"type": "file", "accept": "image/png"})
		state = _make_state({5: entry7, 6: entry8}, file_input_backend_ids=[7, 8])
		# eval_function_on_node 抛异常 → capture 吞掉返 None → 无线索；上传照常成功
		browser = _make_browser()
		browser.eval_function_on_node = AsyncMock(side_effect=RuntimeError("cdp down"))
		result = await Tools(upload_verify_enabled=False).execute(
			"upload_file", {"index": 5, "path": tmp_upload}, browser, browser_state=state,
		)
		assert result.error is None
		assert not (result.metadata and result.metadata.get("upload_clue"))
		browser.set_file_input.assert_awaited_once()
