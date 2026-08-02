"""Tests for tree_walker.agent.upload_identity (#151) — file-input 身份的站点无关信号采集与
线索构建，供重放精筛（``_match_file_upload_by_clue``）与 agent 端采集（``capture_upload_clue``）共用。

覆盖：``nonzero_rect`` / ``effective_clue_rect`` 优先级 / ``build_upload_clue`` 形状（含/不含
``trigger_affordance``）/ ``file_input_candidates`` kind 过滤 / ``capture_upload_clue`` best-effort。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tree_walker.agent.upload_identity import (
	build_upload_clue,
	capture_upload_clue,
	effective_clue_rect,
	file_input_candidates,
	nonzero_rect,
)


def test_nonzero_rect():
	assert nonzero_rect(None) is False
	assert nonzero_rect({"x": 0, "y": 0, "width": 0, "height": 0}) is False
	assert nonzero_rect({"x": 1, "y": 2, "width": 10, "height": 0}) is True  # width>0 即可
	assert nonzero_rect({"width": 5}) is True
	assert nonzero_rect("junk") is False
	assert nonzero_rect({"width": "abc"}) is False  # float() ValueError → False（覆盖 except）


def test_effective_clue_rect_priority():
	# 1) rect 非零 → 用 rect（即使 container 更大）
	r = {"x": 1, "y": 1, "width": 2, "height": 2}
	assert effective_clue_rect(
		{"rect": r, "container_rect": {"x": 9, "y": 9, "width": 9, "height": 9}}
	) == r
	# 2) rect 零/None、container 非零 → 用 container（隐藏 input 的位置信号）
	cr = {"x": 500, "y": 100, "width": 120, "height": 200}
	assert effective_clue_rect({"rect": None, "container_rect": cr}) == cr
	assert effective_clue_rect(
		{"rect": {"x": 0, "y": 0, "width": 0, "height": 0}, "container_rect": cr}
	) == cr
	# 3) rect/container 都零、trigger_affordance.rect 非零 → 用 affordance.rect
	aff = {"x": 7, "y": 7, "width": 3, "height": 3}
	assert effective_clue_rect({"rect": None, "trigger_affordance": {"rect": aff}}) == aff
	# 4) 全无 → 返回原 rect（可能 None），保留 legacy 退回 _nearest_idx
	assert effective_clue_rect({"rect": None}) is None
	assert effective_clue_rect({}) is None


def test_build_upload_clue_shape_without_affordance():
	node = SimpleNamespace(
		node_name="INPUT",
		attributes={"type": "file", "accept": "image/png"},
		xpath="/html/body/div/input[1]",
		snapshot_node=SimpleNamespace(bounds=None),
	)
	ctx = {"label_text": "", "aria_text": "", "region_text": "点击上传", "in_dialog": True,
	       "affordance_text": "", "container_rect": {"x": 10, "y": 10, "width": 5, "height": 5}}
	clue = build_upload_clue(node, ctx)
	assert clue["tag"] == "input"
	assert clue["accept"] == "image/png"
	assert clue["xpath"] == "/html/body/div/input[1]"
	assert clue["region_text"] == "点击上传"
	assert clue["in_dialog"] is True
	assert clue["rect"] is None
	assert clue["container_rect"] == {"x": 10, "y": 10, "width": 5, "height": 5}
	assert "trigger_affordance" not in clue  # affordance_text 空 → 不附


def test_build_upload_clue_with_affordance():
	node = SimpleNamespace(
		node_name="INPUT", attributes={"type": "file", "accept": "image/png"},
		xpath="/x", snapshot_node=SimpleNamespace(bounds=None),
	)
	ctx = {"label_text": "", "aria_text": "", "region_text": "", "in_dialog": False,
	       "affordance_text": "更换封面", "affordance_role": "button", "affordance_tag": "button",
	       "affordance_rect": {"x": 1, "y": 1, "width": 2, "height": 2}, "container_rect": None}
	clue = build_upload_clue(node, ctx)
	assert clue["trigger_affordance"] == {
		"text": "更换封面", "role": "button", "tag": "button",
		"rect": {"x": 1, "y": 1, "width": 2, "height": 2},
	}


def test_build_upload_clue_rect_coercion():
	"""_bounds_to_dict 三分支：dict 直通 / to_dict() / 属性兜底（覆盖 build_upload_clue 的 rect 归一）。"""
	ctx = {"region_text": "", "in_dialog": False, "affordance_text": "", "container_rect": None}
	# 1) bounds 是 dict → 直通
	n1 = SimpleNamespace(node_name="INPUT", attributes={"type": "file", "accept": "image/png"},
	                     xpath="/a", snapshot_node=SimpleNamespace(bounds={"x": 1, "y": 2, "width": 3, "height": 4}))
	assert build_upload_clue(n1, ctx)["rect"] == {"x": 1, "y": 2, "width": 3, "height": 4}
	# 2) bounds 有 to_dict() → 用其返回
	b2 = SimpleNamespace(to_dict=lambda: {"x": 5, "y": 6, "width": 7, "height": 8})
	n2 = SimpleNamespace(node_name="INPUT", attributes={"type": "file", "accept": "image/png"},
	                     xpath="/b", snapshot_node=SimpleNamespace(bounds=b2))
	assert build_upload_clue(n2, ctx)["rect"] == {"x": 5, "y": 6, "width": 7, "height": 8}
	# 3) bounds 只有属性、无 to_dict → 属性兜底
	b3 = SimpleNamespace(x=9, y=10, width=11, height=12)
	n3 = SimpleNamespace(node_name="INPUT", attributes={"type": "file", "accept": "image/png"},
	                     xpath="/c", snapshot_node=SimpleNamespace(bounds=b3))
	assert build_upload_clue(n3, ctx)["rect"] == {"x": 9, "y": 10, "width": 11, "height": 12}


@pytest.mark.asyncio
async def test_capture_upload_clue_returns_none_when_target_not_file_input():
	"""目标元素非 file input（JS ``this`` 校验 return null）→ eval 返 None → clue None。"""
	browser = AsyncMock()
	browser.eval_function_on_node = AsyncMock(return_value=None)
	div = SimpleNamespace(node_name="DIV", attributes={"type": "file"},  # node_name 非 INPUT
	                      backend_node_id=7, xpath="/x", snapshot_node=None)
	assert await capture_upload_clue(browser, {5: div}, backend_id=7) is None


def test_file_input_candidates_kind_filter():
	video = SimpleNamespace(node_name="INPUT", attributes={"type": "file", "accept": "video/mp4"})
	img = SimpleNamespace(node_name="INPUT", attributes={"type": "file", "accept": "image/png"})
	text = SimpleNamespace(node_name="INPUT", attributes={"type": "text"})
	div = SimpleNamespace(node_name="DIV", attributes={})
	sm = {1: video, 2: img, 3: text, 4: div}
	# accept_hint → kind=image：只收 image input
	assert [i for i, _ in file_input_candidates(sm, accept_hint="image/png")] == [2]
	# accept_hint → kind=video
	assert [i for i, _ in file_input_candidates(sm, accept_hint="video/mp4")] == [1]
	# 无 accept_hint、无 path → kind=None → 收所有 file input（不含 text/div）
	assert [i for i, _ in file_input_candidates(sm)] == [1, 2]
	# path 推断 kind（.png → image）
	assert [i for i, _ in file_input_candidates(sm, path="cover.png")] == [2]
	# 空 selector_map → []
	assert file_input_candidates({}) == []


@pytest.mark.asyncio
async def test_capture_upload_clue_builds_clue_for_resolved_input():
	"""命中 input 在 selector_map、eval_function_on_node 在该元素返回 ctx → 产出与手工录制同形的线索。"""
	img = SimpleNamespace(
		node_name="INPUT", attributes={"type": "file", "accept": "image/png"},
		backend_node_id=7, xpath="/x",
		snapshot_node=SimpleNamespace(bounds=None),
	)
	sm = {5: img}
	browser = AsyncMock()
	browser.eval_function_on_node = AsyncMock(return_value={
		"accept": "image/png", "region_text": "点击上传", "in_dialog": True,
		"container_rect": {"x": 10, "y": 10, "width": 5, "height": 5},
	})
	clue = await capture_upload_clue(browser, sm, backend_id=7)
	assert clue is not None
	assert clue["container_rect"]["x"] == 10
	assert clue["accept"] == "image/png"


@pytest.mark.asyncio
async def test_capture_upload_clue_returns_none_when_input_missing():
	"""命中 input 不在 selector_map → None（不调 eval_function_on_node）。"""
	browser = AsyncMock()
	browser.eval_function_on_node = AsyncMock()
	node = SimpleNamespace(
		node_name="INPUT", attributes={"type": "file", "accept": "image/png"},
		backend_node_id=99, xpath="/x", snapshot_node=None,
	)
	assert await capture_upload_clue(browser, {5: node}, backend_id=7) is None
	browser.eval_function_on_node.assert_not_called()


@pytest.mark.asyncio
async def test_capture_upload_clue_returns_none_on_probe_failure():
	"""eval_function_on_node 抛异常 / 返回非 dict → None（best-effort，不抛）。"""
	img = SimpleNamespace(
		node_name="INPUT", attributes={"type": "file", "accept": "image/png"},
		backend_node_id=7, xpath="/x", snapshot_node=None,
	)
	browser = AsyncMock()
	browser.eval_function_on_node = AsyncMock(side_effect=RuntimeError("cdp down"))
	assert await capture_upload_clue(browser, {5: img}, backend_id=7) is None
	browser.eval_function_on_node = AsyncMock(return_value=None)  # JS 返 null（非 file input 等）
	assert await capture_upload_clue(browser, {5: img}, backend_id=7) is None
