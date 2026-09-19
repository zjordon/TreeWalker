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


# ── D：group_count 聚合（issue #185 现象②） ──────────────────────────────────


class TestReadGridGroupCount:
	@pytest.mark.asyncio
	async def test_exact_counts_with_duplicates(self):
		"""task_64 场景：同名行分散出现，Python 侧计数必须精确（Emma Davis 漏计类）。"""
		rows = [
			{"billing_name": "Lisa Green", "entity_id": "1"},
			{"billing_name": "Emma Davis", "entity_id": "2"},
			{"billing_name": "Katie Wong", "entity_id": "3"},
			{"billing_name": "Emma Davis", "entity_id": "4"},
			{"billing_name": "Katie Wong", "entity_id": "5"},
			{"billing_name": "Katie Wong", "entity_id": "6"},
		]
		browser = _FakeBrowser(ui_result=_ui_result(rows=rows, total_records=6))
		result = await Tools().execute("read_grid", {"group_count": "billing_name"}, browser)
		assert not result.error
		vis = result.extracted_content
		assert "group_count[billing_name] over 6 rows" in vis
		assert '"Katie Wong": 3' in vis
		assert '"Emma Davis": 2' in vis
		assert '"Lisa Green": 1' in vis
		# 计数行置于 meta 摘要之前（结论优先）
		assert vis.index("group_count[") < vis.index("read_grid [")
		# total==rows_read → 无未读全警告
		assert "counted" not in vis

	@pytest.mark.asyncio
	async def test_partial_total_warns(self):
		"""total_records > rows_read → 显式提示计数未覆盖全量。"""
		rows = [{"billing_name": "A"}, {"billing_name": "A"}, {"billing_name": "A"}]
		browser = _FakeBrowser(ui_result=_ui_result(rows=rows, total_records=308))
		result = await Tools().execute("read_grid", {"group_count": "billing_name"}, browser)
		assert "counted 3 of total 308" in result.extracted_content

	@pytest.mark.asyncio
	async def test_missing_field_all_missing_warns(self):
		"""字段不在行里 → 全 (missing) 折叠 + 字段名核查提示（与 E 的表头语义联动）。"""
		rows = [{"ID": "1"}, {"ID": "2"}]
		browser = _FakeBrowser(ui_result=_ui_result(rows=rows, total_records=2))
		result = await Tools().execute("read_grid", {"group_count": "review_id"}, browser)
		assert '"(missing)": 2' in result.extracted_content
		assert "field not present in returned rows" in result.extracted_content

	@pytest.mark.asyncio
	async def test_blank_and_none_values_fold_to_missing(self):
		rows = [
			{"billing_name": "A"},
			{"billing_name": ""},
			{"billing_name": None},
			{"no_such_field": 1},
		]
		browser = _FakeBrowser(ui_result=_ui_result(rows=rows, total_records=4))
		result = await Tools().execute("read_grid", {"group_count": "billing_name"}, browser)
		assert '"(missing)": 3' in result.extracted_content
		assert '"A": 1' in result.extracted_content

	@pytest.mark.asyncio
	async def test_top50_cap_marks_omission(self):
		rows = [{"billing_name": f"n{i:03d}", "entity_id": str(i)} for i in range(60)]
		browser = _FakeBrowser(ui_result=_ui_result(rows=rows, total_records=60))
		result = await Tools().execute("read_grid", {"group_count": "billing_name"}, browser)
		assert "+10 more values omitted" in result.extracted_content

	@pytest.mark.asyncio
	async def test_group_count_visible_even_when_saved_to_file(self, tmp_path):
		"""大结果落盘分支：计数行仍须可见（读得了数据不能丢结论）。"""
		rows = [{"billing_name": "X", "big": "y" * 40} for _ in range(200)]
		browser = _FakeBrowser(ui_result=_ui_result(rows=rows, total_records=200))
		tools = Tools(truncation=TruncationSettings(
			eval_save_threshold=2000, eval_output_dir=str(tmp_path),
		))
		result = await tools.execute("read_grid", {"group_count": "billing_name"}, browser)
		assert "saved to" in result.extracted_content
		assert '"X": 200' in result.extracted_content
		assert "group_count(billing_name)" in result.long_term_memory

	@pytest.mark.asyncio
	async def test_group_count_param_validation(self):
		result = await Tools().execute("read_grid", {"group_count": 42}, _FakeBrowser())
		assert result.error and "group_count must be a non-empty string" in result.error
		result = await Tools().execute("read_grid", {"group_count": "   "}, _FakeBrowser())
		assert result.error and "group_count must be a non-empty string" in result.error

	@pytest.mark.asyncio
	async def test_group_count_strips_surrounding_whitespace(self):
		"""review 修正：" billing_name "（LLM 输出常见）须归一化后正常计数，
		而非整表落 "(missing)"。"""
		rows = [
			{"billing_name": "Emma Davis"},
			{"billing_name": "Emma Davis"},
			{"billing_name": "Lisa Green"},
		]
		browser = _FakeBrowser(ui_result=_ui_result(rows=rows, total_records=3))
		result = await Tools().execute("read_grid", {"group_count": " billing_name "}, browser)
		assert not result.error
		assert "group_count[billing_name] over 3 rows" in result.extracted_content
		assert '"Emma Davis": 2' in result.extracted_content
		assert "(missing)" not in result.extracted_content

	@pytest.mark.asyncio
	async def test_group_count_legacy_channel_page_local_note(self):
		"""review 修正：legacy/dom 不回传 total_records，未读全警示永不触发——
		须补 page-local 提示，防止单页计数被当全局精确值。"""
		legacy = {
			"channel": "legacy_ajax", "namespace": "reviewGrid",
			"rows": [{"ID": "1"}, {"ID": "1"}, {"ID": "2"}], "rows_returned": 3,
			"headers": ["ID"],
			"applied": None, "active_before": None, "partial": False,
		}
		browser = _FakeBrowser(evaluate_side_effects=[json.dumps(legacy)])
		result = await Tools().execute("read_grid", {"group_count": "ID"}, browser)
		assert not result.error
		assert '"1": 2' in result.extracted_content
		assert "page-local counts" in result.extracted_content

	@pytest.mark.asyncio
	async def test_group_count_uiregistry_full_read_no_page_local_note(self):
		"""uiregistry 全量读（rows==total）既无未读全警示也无 page-local 提示。"""
		rows = [{"billing_name": "X"}]
		browser = _FakeBrowser(ui_result=_ui_result(rows=rows, total_records=1))
		result = await Tools().execute("read_grid", {"group_count": "billing_name"}, browser)
		assert not result.error
		assert "page-local" not in result.extracted_content
		assert "counted" not in result.extracted_content


# ── E：legacy/DOM 不兼容诊断（issue #185 现象④） ──────────────────────────────


class TestReadGridLegacyDiagnostics:
	@pytest.mark.asyncio
	async def test_fields_header_mismatch_all_empty_rows_noted(self):
		"""task_112 step6 形态：fields 请求名与 legacy 显示名表头不匹配 → 全空
		对象行 + note 附可用表头名单（免试止损）。"""
		legacy = {
			"channel": "legacy_ajax", "namespace": "reviewGrid",
			"rows": [{}, {}, {}], "rows_returned": 3,
			"headers": ["ID", "Created", "Status", "Title"],
			"applied": None, "active_before": None, "partial": False,
		}
		browser = _FakeBrowser(evaluate_side_effects=[json.dumps(legacy)])
		result = await Tools().execute(
			"read_grid", {"fields": ["review_id", "sku"]}, browser)
		assert not result.error
		assert "came back EMPTY" in result.extracted_content
		assert "display-name headers" in result.extracted_content
		assert "'ID'" in result.extracted_content  # 可用表头名单进 note

	@pytest.mark.asyncio
	async def test_zero_rows_with_filters_noted(self):
		"""review 修正：legacy/dom 从不应用 filters/search——被忽略的条件不可能导致
		0 行，note 须明说"非 filters 之故"，不得建议 retry without filters。"""
		legacy = {
			"channel": "legacy_ajax", "namespace": "reviewGrid",
			"rows": [], "rows_returned": 0, "headers": ["ID"],
			"applied": None, "active_before": None, "partial": False,
		}
		browser = _FakeBrowser(evaluate_side_effects=[json.dumps(legacy)])
		result = await Tools().execute(
			"read_grid", {"filters": {"status": "pending"}}, browser)
		assert not result.error
		assert "0 rows returned" in result.extracted_content
		assert "NOT" in result.extracted_content and "not the cause" in result.extracted_content
		assert "retry without filters" not in result.extracted_content

	@pytest.mark.asyncio
	async def test_zero_rows_without_filters_stale_response_hint(self):
		legacy = {
			"channel": "legacy_ajax", "namespace": "reviewGrid",
			"rows": [], "rows_returned": 0, "headers": ["ID"],
			"applied": None, "active_before": None, "partial": False,
		}
		browser = _FakeBrowser(evaluate_side_effects=[json.dumps(legacy)])
		result = await Tools().execute("read_grid", {}, browser)
		assert not result.error
		assert "0 rows returned" in result.extracted_content
		assert "stale/empty response" in result.extracted_content

	@pytest.mark.asyncio
	async def test_uiregistry_all_empty_rows_from_field_mismatch_noted(self):
		"""review 修正：主通道 uiregistry 按 data-source 字段名 hasOwnProperty 过滤
		fields——猜错字段名同样得到全空对象行，须同样给 note（措辞区分字段名体系）。"""
		browser = _FakeBrowser(ui_result=_ui_result(
			rows=[{}, {}, {}], total_records=3,
		))
		result = await Tools().execute(
			"read_grid", {"fields": ["no_such_field"]}, browser)
		assert not result.error
		assert "came back EMPTY" in result.extracted_content
		assert "data-source field names" in result.extracted_content

	@pytest.mark.asyncio
	async def test_healthy_legacy_read_no_diagnostic_note(self):
		legacy = {
			"channel": "legacy_ajax", "namespace": "reviewGrid",
			"rows": [{"ID": "353", "Title": "Bad!"}], "rows_returned": 1,
			"headers": ["ID", "Title"],
			"applied": None, "active_before": None, "partial": False,
		}
		browser = _FakeBrowser(evaluate_side_effects=[json.dumps(legacy)])
		result = await Tools().execute("read_grid", {}, browser)
		assert not result.error
		assert "came back EMPTY" not in result.extracted_content
		assert "0 rows returned" not in result.extracted_content

	@pytest.mark.asyncio
	async def test_uiregistry_zero_rows_not_misdiagnosed(self):
		"""uiRegistry 主通道 0 行通常是真过滤结果（有 total_records 佐证）——不套
		legacy 诊断，避免误报。"""
		browser = _FakeBrowser(ui_result=_ui_result(rows=[], total_records=0))
		result = await Tools().execute(
			"read_grid", {"filters": {"status": "nope"}}, browser)
		assert not result.error
		assert "0 rows returned" not in result.extracted_content


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


# ── issue #193：footer（合计行）捕获 + column_sums 交叉校验 ──────────────────


class TestFooterTotals:
	"""193 方向 2：有 Total/合计行时逐列求和并与 footer 对账（算术代码化）。

	JS 不真跑（evaluate mock 回 channel dict，footer 是 JS 返回体字段）——
	Python 侧逻辑全可测；JS 行为（tfoot/tbody 落点、Total 行剔除）由结构
	断言 + examples/debug_read_grid_totals.py 真机探针把关。
	背景：C107 断言 "total 67 matching" 实加 130（假校验）；C111 算出 175
	未与 Total 行比对。
	"""

	def _dom_result(self, rows, footer, **over):
		base = {
			"channel": "dom_table", "namespace": None,
			"rows": rows, "rows_returned": len(rows),
			"headers": list({k for r in rows for k in r}),
			"footer": footer,
			"applied": None, "active_before": None, "partial": False,
		}
		base.update(over)
		return base

	@pytest.mark.asyncio
	async def test_footer_match_note(self):
		"""107 场景（读对列）：Orders 8 列值加和 67 == footer 67，无 ✗。"""
		rows = [
			{"Interval": "5/2022", "Orders": "8", "Sales Items": "25"},
			{"Interval": "6/2022", "Orders": "13", "Sales Items": "34"},
			{"Interval": "7/2022", "Orders": "9", "Sales Items": "28"},
			{"Interval": "8/2022", "Orders": "8", "Sales Items": "18"},
			{"Interval": "9/2022", "Orders": "10", "Sales Items": "31"},
			{"Interval": "10/2022", "Orders": "4", "Sales Items": "11"},
			{"Interval": "11/2022", "Orders": "5", "Sales Items": "15"},
			{"Interval": "12/2022", "Orders": "10", "Sales Items": "37"},
		]
		footer = [{"Interval": "Total", "Orders": "67", "Sales Items": "199"}]
		browser = _FakeBrowser(evaluate_side_effects=[
			json.dumps({"channel_error": "no-legacy-grid"}),
			json.dumps(self._dom_result(rows, footer)),
		])
		result = await Tools().execute("read_grid", {}, browser)
		assert not result.error
		assert "totals-check:" in result.extracted_content
		assert "Orders: sum 67 == footer 67" in result.extracted_content
		assert "✗" not in result.extracted_content
		assert "totals-ok" in result.long_term_memory

	@pytest.mark.asyncio
	async def test_footer_mismatch_note(self):
		"""错列/漏行形态：rows 加和 130 ≠ footer 67 → ✗ + 重读指引。"""
		rows = [
			{"Interval": "5/2022", "Orders": "25"},
			{"Interval": "6/2022", "Orders": "28"},
			{"Interval": "7/2022", "Orders": "18"},
			{"Interval": "8/2022", "Orders": "11"},
			{"Interval": "9/2022", "Orders": "15"},
			{"Interval": "10/2022", "Orders": "10"},
			{"Interval": "11/2022", "Orders": "13"},
			{"Interval": "12/2022", "Orders": "10"},
		]
		footer = [{"Interval": "Total", "Orders": "67"}]
		browser = _FakeBrowser(evaluate_side_effects=[
			json.dumps({"channel_error": "no-legacy-grid"}),
			json.dumps(self._dom_result(rows, footer)),
		])
		result = await Tools().execute("read_grid", {}, browser)
		assert not result.error
		assert "Orders: sum 130 ≠ footer 67 ✗" in result.extracted_content
		# review#4 后指引含分页出路（完整文案断言在
		# test_mismatch_guidance_mentions_pagination）
		assert "re-check the column binding" in result.extracted_content
		assert "totals-mismatch" in result.long_term_memory

	@pytest.mark.asyncio
	async def test_no_footer_no_totals_line(self):
		"""uiregistry 通道无 footer → 零 totals-check（零回归锚点）。"""
		browser = _FakeBrowser(ui_result=_ui_result())
		result = await Tools().execute("read_grid", {}, browser)
		assert not result.error
		assert "totals-check" not in result.extracted_content
		assert "totals-" not in result.long_term_memory

	@pytest.mark.asyncio
	async def test_currency_and_thousands_parsing(self):
		"""'$1,234.56' + '8' vs footer '$1,242.56'——货币/千分位两端剥离、
		内部逗号整体移除（strip 只削两端）。"""
		rows = [{"Product": "A", "Price": "$1,234.56"}, {"Product": "B", "Price": "8"}]
		footer = [{"Product": "Total", "Price": "$1,242.56"}]
		browser = _FakeBrowser(evaluate_side_effects=[
			json.dumps({"channel_error": "no-legacy-grid"}),
			json.dumps(self._dom_result(rows, footer)),
		])
		result = await Tools().execute("read_grid", {}, browser)
		assert not result.error
		assert "Price: sum 1242.56 == footer 1242.56" in result.extracted_content

	@pytest.mark.asyncio
	async def test_non_numeric_column_excluded(self):
		"""列内混非数值（名字/N-A）→ 整列不参与求和；数值列照常对账。"""
		rows = [
			{"Name": "Ida Pant", "Qty": "4"},
			{"Name": "Duffle", "Qty": "3"},
		]
		footer = [{"Name": "Total", "Qty": "7"}]
		browser = _FakeBrowser(evaluate_side_effects=[
			json.dumps({"channel_error": "no-legacy-grid"}),
			json.dumps(self._dom_result(rows, footer)),
		])
		result = await Tools().execute("read_grid", {}, browser)
		assert not result.error
		assert "Qty: sum 7 == footer 7" in result.extracted_content
		assert "Name: sum" not in result.extracted_content

	@pytest.mark.asyncio
	async def test_empty_cells_skipped(self):
		"""列内空格跳过仍求和；mismatch 时回显 skipped 计数（漏行信号）。"""
		rows = [
			{"Interval": "5/2022", "Orders": "8"},
			{"Interval": "6/2022", "Orders": ""},  # 空单元格：跳过但计数
			{"Interval": "7/2022", "Orders": "9"},
		]
		footer = [{"Interval": "Total", "Orders": "30"}]  # 8+9=17 ≠ 30
		browser = _FakeBrowser(evaluate_side_effects=[
			json.dumps({"channel_error": "no-legacy-grid"}),
			json.dumps(self._dom_result(rows, footer)),
		])
		result = await Tools().execute("read_grid", {}, browser)
		assert not result.error
		assert "Orders: sum 17 ≠ footer 30 ✗ (1 empty cells skipped)" in result.extracted_content

	@pytest.mark.asyncio
	async def test_multiple_footer_rows(self):
		"""tfoot + tbody 双合计行（异常形态）：每行都比，输出稳定不炸。"""
		rows = [{"Interval": "5/2022", "Orders": "8"}]
		footer = [
			{"Interval": "Total", "Orders": "8"},
			{"Interval": "Grand Total", "Orders": "8"},
		]
		browser = _FakeBrowser(evaluate_side_effects=[
			json.dumps({"channel_error": "no-legacy-grid"}),
			json.dumps(self._dom_result(rows, footer)),
		])
		result = await Tools().execute("read_grid", {}, browser)
		assert not result.error
		assert result.extracted_content.count("Orders: sum 8 == footer 8") == 2
		assert "✗" not in result.extracted_content

	@pytest.mark.asyncio
	async def test_footer_all_non_numeric_no_line(self):
		"""footer 无任何可比数值格 → 不产 totals-check（无噪声）。"""
		rows = [{"Name": "A", "Note": "ok"}]
		footer = [{"Name": "Total", "Note": "—"}]
		browser = _FakeBrowser(evaluate_side_effects=[
			json.dumps({"channel_error": "no-legacy-grid"}),
			json.dumps(self._dom_result(rows, footer)),
		])
		result = await Tools().execute("read_grid", {}, browser)
		assert not result.error
		assert "totals-check" not in result.extracted_content

	def test_js_templates_capture_footer(self):
		"""结构断言（先例：test_falls_back_to_legacy_ajax 断言 JS 含
		GridJsObject）：两模板接共享核心、含 tfoot 捕获与 Total 行剔除。"""
		from tree_walker.tools.actions import (
			_DOM_TABLE_READ_JS, _LEGACY_GRID_READ_JS, _TABLE_ROWS_CORE_JS,
		)
		for js in (_LEGACY_GRID_READ_JS, _DOM_TABLE_READ_JS):
			assert "_gridReadTable(" in js
			assert "footer.push" in js
		for frag in ("tfoot tr", "_gridIsTotalLabel", "querySelector('td,th')",
		             "colSpan"):
			# review#2 colspan 折算 / review#3 首格判定取 td,th
			assert frag in _TABLE_ROWS_CORE_JS
		# 无反斜杠家规（模板注释 ：463-464）——本改动不引入
		assert "\\" not in _TABLE_ROWS_CORE_JS

	@pytest.mark.asyncio
	async def test_footer_non_numeric_cell_skipped(self):
		"""footer 里与求和列同键的格非数值（'—'/'n/a'）→ 该列不比对不误报。"""
		rows = [{"Interval": "5/2022", "Orders": "8"}]
		footer = [{"Interval": "Total", "Orders": "n/a"}]
		browser = _FakeBrowser(evaluate_side_effects=[
			json.dumps({"channel_error": "no-legacy-grid"}),
			json.dumps(self._dom_result(rows, footer)),
		])
		result = await Tools().execute("read_grid", {}, browser)
		assert not result.error
		assert "totals-check" not in result.extracted_content
		assert "✗" not in result.extracted_content

	# ── review-issue-193-1 修复的回归用例 ────────────────────────────────

	@pytest.mark.asyncio
	async def test_subtotal_rows_not_used_as_base(self):
		"""review#1：分组小计/Subtotal/Tax 行不作为全列和基准——与全列和
		必然不等，全当基准=稳定假 ✗；只有 Total/Grand Total 行参与比对。"""
		rows = [
			{"Interval": "5/2022", "Orders": "8"},
			{"Interval": "6/2022", "Orders": "13"},
		]
		footer = [
			{"Interval": "Subtotal (Q2)", "Orders": "21"},
			{"Interval": "Tax", "Orders": "0"},
			{"Interval": "Total", "Orders": "21"},
		]
		browser = _FakeBrowser(evaluate_side_effects=[
			json.dumps({"channel_error": "no-legacy-grid"}),
			json.dumps(self._dom_result(rows, footer)),
		])
		result = await Tools().execute("read_grid", {}, browser)
		assert not result.error
		# 只有 Total 行被比对（一次 ==），Subtotal/Tax 行不产生比对项
		assert "Orders: sum 21 == footer 21" in result.extracted_content
		assert "✗" not in result.extracted_content
		assert "totals-ok" in result.long_term_memory

	@pytest.mark.asyncio
	async def test_unlabeled_single_footer_row_still_checked(self):
		"""review#1 退化分支：fields 过滤会把标签格滤掉——单行无标签 footer
		仍须校验（单行=基准），多行无标签保守全跳过。"""
		rows = [{"Orders": "8"}, {"Orders": "13"}]
		footer = [{"Orders": "21"}]  # fields=['Orders'] 后标签格被滤掉
		browser = _FakeBrowser(evaluate_side_effects=[
			json.dumps({"channel_error": "no-legacy-grid"}),
			json.dumps(self._dom_result(rows, footer)),
		])
		result = await Tools().execute("read_grid", {}, browser)
		assert not result.error
		assert "Orders: sum 21 == footer 21" in result.extracted_content

	@pytest.mark.asyncio
	async def test_mismatch_guidance_mentions_pagination(self):
		"""review#4：legacy/dom 通道 page-local——多页表可见行加和 ≠ 全量
		footer 结构性必然，mismatch 指引必须给分页出路。"""
		rows = [{"Interval": "5/2022", "Orders": "25"}]
		footer = [{"Interval": "Total", "Orders": "67"}]
		browser = _FakeBrowser(evaluate_side_effects=[
			json.dumps({"channel_error": "no-legacy-grid"}),
			json.dumps(self._dom_result(rows, footer)),
		])
		result = await Tools().execute("read_grid", {}, browser)
		assert not result.error
		assert "paginated table" in result.extracted_content
		assert "page-local" in result.extracted_content

	@pytest.mark.asyncio
	async def test_rounding_accumulation_tolerance(self):
		"""review#5：各行显示值舍入到分、footer 按未舍入值求和再舍入——
		|Σround−round(Σ)| 可达 ~n×半分钱，固定半分钱容差会假 ✗；容差按
		参与求和的行数缩放（0.005×(n+1)）。"""
		rows = [
			{"Interval": "r1", "Amount": "0.125"},
			{"Interval": "r2", "Amount": "0.125"},
			{"Interval": "r3", "Amount": "0.125"},
		]  # Σ=0.375，footer 舍入显示 0.38：|差|=0.005，旧固定容差判 ✗
		footer = [{"Interval": "Total", "Amount": "0.38"}]
		browser = _FakeBrowser(evaluate_side_effects=[
			json.dumps({"channel_error": "no-legacy-grid"}),
			json.dumps(self._dom_result(rows, footer)),
		])
		result = await Tools().execute("read_grid", {}, browser)
		assert not result.error
		assert "Amount: sum 0.375 == footer 0.38" in result.extracted_content
		assert "✗" not in result.extracted_content

	def test_parse_grid_number_thousands_and_decimal_comma(self):
		"""review#7：含逗号只接受标准千分位；欧陆小数逗号（'12,50'/
		'1.234,56'）裸去逗号会解析成 1250/1.23456——rows 与 footer 同解析器
		还可能自洽 totals-ok（100× 失真值被自信验证），保守拒识返回 None。"""
		from tree_walker.tools.actions import _parse_grid_number
		assert _parse_grid_number("$1,234.56") == 1234.56
		assert _parse_grid_number("1,234,567") == 1234567.0
		assert _parse_grid_number("12,50") is None
		assert _parse_grid_number("1.234,56") is None
		assert _parse_grid_number("12,50 €") is None
		assert _parse_grid_number("(1,234)") is None  # 会计负数不认
		assert _parse_grid_number("1.234.567") is None  # 多点格式不认
