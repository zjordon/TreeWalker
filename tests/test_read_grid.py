"""Tests for read_grid（P7 tool_layer B1/B2/B3，2026-08-28）。

Covers:
- 通道梯：uiRegistry 主通道成功 / 回落 legacy AJAX / 回落 DOM 表格 / 全灭报错
- 参数守卫：filters/page_size/fields/namespace 的类型防御（registry 不校验
  execute 路径）
- sorting 解析：'created_at desc' → {field, direction}；单 token 默认 asc；
  非法 direction 归一为 asc
- 元信息摘要：ns/rows/total/sorted 标签、active_before 残留提示、partial 提示
- 大结果落盘：超过 eval_save_threshold 写 grid_*.json（镜像 evaluate 分级）
- B2：build_state_message 的 [Grid] 渲染（排序/首行值/残留过滤警告/无排序警告）
- B3：_read_page_messages 读页面消息浮层
- session.read_ui_grid：evaluate 异常/不可解析 → channel_error dict 而非 raise

背景：docs/p7/tool_layer/01-feasibility-and-impl-plan.md（探针实证 mui 通道
不可用，uiRegistry ds.data.items 为主通道）。
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from tree_walker.config import TruncationSettings
from tree_walker.tools.actions import Tools


# ── helpers ───────────────────────────────────────────────────────────────────


def _ui_result(**over):
	"""uiRegistry 通道的典型成功返回（sales_order_grid 两行）。"""
	base = {
		"channel": "uiregistry",
		"namespace": "sales_order_grid",
		"rows": [
			{"entity_id": "299", "created_at": "2023-05-31", "status": "complete"},
			{"entity_id": "298", "created_at": "2023-05-30", "status": "complete"},
		],
		"total_records": 308,
		"applied": {
			"filters": {},
			"search": "",
			"sorting": {"field": "created_at", "direction": "desc"},
			"paging": {"pageSize": 2, "current": 1},
		},
		"active_before": {"filters": {}, "search": "", "sorting": None},
		"partial": False,
	}
	base.update(over)
	return base


class _FakeBrowser:
	"""read_ui_grid 走 AsyncMock（可查 payload）；evaluate 只服务回落通道。"""

	def __init__(self, ui_result=None, evaluate_side_effects=None):
		self.read_ui_grid = AsyncMock(
			return_value=ui_result if ui_result is not None else {"channel_error": "no-requirejs"},
		)
		self.evaluate = AsyncMock(side_effect=evaluate_side_effects)


# ── B1：通道梯 ────────────────────────────────────────────────────────────────


class TestReadGridChannels:
	@pytest.mark.asyncio
	async def test_uiregistry_success_and_payload(self):
		browser = _FakeBrowser(ui_result=_ui_result())
		result = await Tools().execute("read_grid", {"sorting": "created_at desc", "page_size": 2}, browser)
		assert not result.error
		# 传给 read_ui_grid 的 payload：sorting 解析 + paging + fresh 默认 True
		payload = browser.read_ui_grid.call_args[0][0]
		assert payload["sorting"] == {"field": "created_at", "direction": "desc"}
		assert payload["paging"] == {"pageSize": 2, "current": 1}
		assert payload["fresh"] is True
		assert payload["filters"] is None
		# 摘要标签
		assert "ns=sales_order_grid" in result.extracted_content
		assert "rows=2" in result.extracted_content
		assert "total=308" in result.extracted_content
		assert "sorted=created_at desc" in result.extracted_content

	@pytest.mark.asyncio
	async def test_falls_back_to_legacy_ajax(self):
		legacy = {
			"channel": "legacy_ajax", "namespace": "reviewGrid",
			"rows": [{"ID": "353", "Review": "Bad!"}],
			"rows_returned": 1, "info": "353 records found",
			"applied": {"sorting": None, "page_size": 200, "page": 1},
			"active_before": None, "partial": False,
		}
		browser = _FakeBrowser(evaluate_side_effects=[json.dumps(legacy)])
		result = await Tools().execute("read_grid", {"filters": {"status": "complete"}}, browser)
		assert not result.error
		assert "legacy_ajax" in result.extracted_content
		# evaluate 收到的是 legacy 通道 JS（含 GridJsObject 探测）
		js = browser.evaluate.call_args[0][0]
		assert "GridJsObject" in js
		# legacy 不支持 filters → 显式 note
		assert "filters/search not applied" in result.extracted_content

	@pytest.mark.asyncio
	async def test_falls_back_to_dom_table(self):
		dom = {
			"channel": "dom_table", "namespace": None,
			"rows": [{"Name": "A", "Qty": "3"}], "rows_returned": 1,
			"applied": None, "active_before": None, "partial": False,
			"note": "DOM channel: current-page visible rows only",
		}
		browser = _FakeBrowser(evaluate_side_effects=[
			json.dumps({"channel_error": "no-legacy-grid"}),
			json.dumps(dom),
		])
		result = await Tools().execute("read_grid", {}, browser)
		assert not result.error
		assert "dom_table" in result.extracted_content
		assert browser.evaluate.await_count == 2

	@pytest.mark.asyncio
	async def test_all_channels_dead_returns_error(self):
		browser = _FakeBrowser(evaluate_side_effects=[
			json.dumps({"channel_error": "no-legacy-grid"}),
			json.dumps({"channel_error": "no-table"}),
		])
		result = await Tools().execute("read_grid", {}, browser)
		assert result.error and "read_grid failed" in result.error


# ── B1：参数守卫与解析 ────────────────────────────────────────────────────────


class TestReadGridParams:
	@pytest.mark.asyncio
	async def test_filters_must_be_dict(self):
		result = await Tools().execute("read_grid", {"filters": ["status"]}, _FakeBrowser())
		assert result.error and "filters must be an object" in result.error

	@pytest.mark.asyncio
	async def test_page_size_must_be_int(self):
		result = await Tools().execute("read_grid", {"page_size": "big"}, _FakeBrowser())
		assert result.error and "integers" in result.error

	@pytest.mark.asyncio
	async def test_fields_must_be_str_list(self):
		result = await Tools().execute("read_grid", {"fields": [1, 2]}, _FakeBrowser())
		assert result.error and "fields must be a list" in result.error

	@pytest.mark.asyncio
	async def test_sorting_single_token_defaults_asc(self):
		browser = _FakeBrowser(ui_result=_ui_result())
		await Tools().execute("read_grid", {"sorting": "qty"}, browser)
		payload = browser.read_ui_grid.call_args[0][0]
		assert payload["sorting"] == {"field": "qty", "direction": "asc"}

	@pytest.mark.asyncio
	async def test_sorting_bad_direction_normalized(self):
		browser = _FakeBrowser(ui_result=_ui_result())
		await Tools().execute("read_grid", {"sorting": "created_at DESCENDING"}, browser)
		payload = browser.read_ui_grid.call_args[0][0]
		assert payload["sorting"]["direction"] == "asc"

	@pytest.mark.asyncio
	async def test_page_size_clamped(self):
		browser = _FakeBrowser(ui_result=_ui_result())
		await Tools().execute("read_grid", {"page_size": 99999}, browser)
		payload = browser.read_ui_grid.call_args[0][0]
		assert payload["paging"]["pageSize"] == 2000

	@pytest.mark.asyncio
	async def test_fresh_false_passthrough(self):
		browser = _FakeBrowser(ui_result=_ui_result())
		await Tools().execute("read_grid", {"fresh": False}, browser)
		payload = browser.read_ui_grid.call_args[0][0]
		assert payload["fresh"] is False


# ── B1：摘要、残留提示、落盘 ─────────────────────────────────────────────────


class TestReadGridSummary:
	@pytest.mark.asyncio
	async def test_leftover_state_noted(self):
		browser = _FakeBrowser(ui_result=_ui_result(
			active_before={"filters": {"status": "complete"}, "search": "", "sorting": None},
		))
		result = await Tools().execute("read_grid", {}, browser)
		assert "cleared leftover grid state" in result.extracted_content
		assert "status" in result.extracted_content

	@pytest.mark.asyncio
	async def test_partial_flag_noted(self):
		browser = _FakeBrowser(ui_result=_ui_result(partial=True))
		result = await Tools().execute("read_grid", {}, browser)
		assert "partial" in result.extracted_content

	@pytest.mark.asyncio
	async def test_large_result_spilled_to_file(self, tmp_path):
		rows = [{"entity_id": str(i), "name": f"p{i}", "sku": f"SKU-{i}"} for i in range(500)]
		browser = _FakeBrowser(ui_result=_ui_result(rows=rows, total_records=2040))
		tools = Tools(truncation=TruncationSettings(
			eval_save_threshold=2000, eval_output_dir=str(tmp_path),
		))
		result = await tools.execute("read_grid", {}, browser)
		assert not result.error
		assert "saved to" in result.extracted_content
		saved = list(tmp_path.glob("grid_*.json"))
		assert len(saved) == 1
		data = json.loads(saved[0].read_text(encoding="utf-8"))
		assert data["total_records"] == 2040
		assert "saved=" in result.long_term_memory


# ── B2：[Grid] 元信息渲染 ─────────────────────────────────────────────────────


class TestGridMetaRendering:
	def _msg(self, grid_meta):
		from tree_walker.browser.views import BrowserStateSummary
		from tree_walker.prompts.system_prompt import build_state_message
		state = BrowserStateSummary(url="http://x/admin/sales/order/", title="Orders")
		return build_state_message(state, task="t", grid_meta=grid_meta)

	def test_grid_line_with_sorting_and_first_row(self):
		msg = self._msg({
			"namespace": "sales_order_grid", "rows_loaded": 153, "total_records": 153,
			"page": 1, "page_size": 200,
			"sorting": {"field": "created_at", "direction": "desc"},
			"first_sorted_value": "2023-05-19 08:11:51",
			"active_filters": {}, "active_search": "",
		})
		assert "[Grid] sales_order_grid" in msg
		assert "rows 153 of 153" in msg
		assert "sorted: created_at desc" in msg
		assert "first row: 2023-05-19" in msg

	def test_no_sorting_warns_against_assumption(self):
		msg = self._msg({
			"namespace": "product_listing", "rows_loaded": 32, "total_records": 2040,
			"sorting": None, "active_filters": {}, "active_search": "",
		})
		assert "do NOT assume any row order" in msg

	def test_leftover_filters_warned(self):
		msg = self._msg({
			"namespace": "sales_order_grid", "rows_loaded": 153, "total_records": 153,
			"sorting": {"field": "created_at", "direction": "desc"},
			"active_filters": {"status": "complete"}, "active_search": "",
		})
		assert "leftover from a previous session" in msg
		assert "status" in msg

	def test_none_grid_meta_renders_nothing(self):
		assert "[Grid]" not in self._msg(None)


# ── B3：页面消息读取 ──────────────────────────────────────────────────────────


class TestPageMessages:
	@pytest.mark.asyncio
	async def test_read_page_messages_success(self):
		browser = _FakeBrowser()
		browser.evaluate = AsyncMock(return_value="SUCCESS: You saved the product.")
		msg = await Tools()._read_page_messages(browser)
		assert msg == "SUCCESS: You saved the product."

	@pytest.mark.asyncio
	async def test_read_page_messages_empty(self):
		browser = _FakeBrowser()
		browser.evaluate = AsyncMock(return_value="")
		assert await Tools()._read_page_messages(browser) == ""

	@pytest.mark.asyncio
	async def test_read_page_messages_swallows_errors(self):
		browser = _FakeBrowser()
		browser.evaluate = AsyncMock(side_effect=RuntimeError("cdp down"))
		assert await Tools()._read_page_messages(browser) == ""


# ── session.read_ui_grid：异常路径 ────────────────────────────────────────────


class TestReadUiGridSession:
	def _session(self, evaluate):
		from tree_walker.browser.session import BrowserSession
		bs = BrowserSession.__new__(BrowserSession)
		bs.evaluate = evaluate
		return bs

	@pytest.mark.asyncio
	async def test_evaluate_failure_returns_channel_error(self):
		async def boom(code, **kw):
			raise RuntimeError("page navigated")
		result = await self._session(boom).read_ui_grid({})
		assert "channel_error" in result and "evaluate-failed" in result["channel_error"]

	@pytest.mark.asyncio
	async def test_unparseable_result_returns_channel_error(self):
		async def raw(code, **kw):
			return "not-json{{"
		result = await self._session(raw).read_ui_grid({})
		assert "unparseable" in result["channel_error"]

	@pytest.mark.asyncio
	async def test_dict_result_passthrough(self):
		async def ok(code, **kw):
			return json.dumps({"channel": "uiregistry", "rows": []})
		result = await self._session(ok).read_ui_grid({"fresh": True})
		assert result["channel"] == "uiregistry"
