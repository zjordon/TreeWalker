"""issue #186-c2 形态②：零结果降级 nudge 的测试。

背景（docs/bug-fix/186-c2-reopen-analysis.md §2 P1）：C 轮 544——任务名
Selena vs 目录 Selene 拼写不一致，agent 按产品名精确过滤评论网格 0 结果
反复同策略（34 次 read_grid/search，25/29 步耗尽）。零结果是 soft-miss
（工具语义正确），不进 consecutive_failures——独立 ZeroResultStreakTracker
经 ActionResult.metadata['query_total'] 结构化旁路接收信号，同类查询
连续 2 次零结果注入降级指引。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tree_walker.agent.loop_detector import ZeroResultStreakTracker
from tree_walker.agent.views import ActionResult
from tree_walker.tools.actions import Tools


def _qr(total: int, desc: str = "query 'x'") -> ActionResult:
	"""带 query_total 旁路的成功 ActionResult（模拟查询类动作返回）。"""
	return ActionResult(
		extracted_content="echo", long_term_memory="m",
		metadata={"query_total": total, "query_desc": desc},
	)


def _plain() -> ActionResult:
	"""无 query_total 的普通动作结果（click 等）——不参与跟踪。"""
	return ActionResult(extracted_content="ok", long_term_memory="m")


class TestZeroResultStreakTracker:
	def test_below_threshold_no_nudge(self):
		t = ZeroResultStreakTracker()
		t.record("read_grid", {"filters": {"billing_name": "Selena"}}, _qr(0))
		assert t.peek_nudge() is None  # streak=1 < 2

	def test_two_consecutive_zero_results_nudge(self):
		"""C 轮 544 形态：同过滤两次零结果 → 降级文案含查询描述与换策略指引。"""
		t = ZeroResultStreakTracker()
		params = {"filters": {"name": "Selena Yoga Hoodie"}, "namespace": "review_grid"}
		t.record("read_grid", params, _qr(0))
		t.record("read_grid", params, _qr(0))
		cand = t.peek_nudge()
		assert cand is not None
		key, msg = cand
		assert "0 results 2 times" in msg
		assert "substring/partial filter" in msg
		assert "similarity" in msg
		assert "review_grid" in key

	def test_nonzero_result_resets_streak(self):
		t = ZeroResultStreakTracker()
		params = {"query": "WH12"}
		t.record("search_page", params, _qr(0))
		t.record("search_page", params, _qr(0))
		assert t.peek_nudge() is not None
		t.ack_nudge(t.peek_nudge()[0])
		t.record("search_page", params, _qr(5))  # 非零重置
		assert t.peek_nudge() is None
		t.record("search_page", params, _qr(0))
		assert t.peek_nudge() is None  # 重新从 1 计

	def test_changed_query_resets_streak(self):
		"""改查询即重置——544 的正确出路（换子串/换过滤）不应被旧 streak 拖累。"""
		t = ZeroResultStreakTracker()
		t.record("search_page", {"query": "Selena Yoga Hoodie"}, _qr(0))
		t.record("search_page", {"query": "Selena Yoga Hoodie"}, _qr(0))
		c = t.peek_nudge()
		assert c is not None
		t.ack_nudge(c[0])  # nudge 已送达（LLM 收到降级指引）
		# 换了查询（更短子串）——新键独立从 1 计
		t.record("search_page", {"query": "Selena"}, _qr(0))
		assert t.peek_nudge() is None  # 旧键已通知不重报、新键仅 streak=1

	def test_different_query_keys_independent(self):
		t = ZeroResultStreakTracker()
		t.record("find_elements", {"selector": ".btn"}, _qr(0, "selector '.btn'"))
		t.record("search_page", {"query": "x"}, _qr(0, "query 'x'"))
		assert t.peek_nudge() is None  # 各自 streak=1

	def test_non_query_actions_ignored(self):
		t = ZeroResultStreakTracker()
		t.record("click", {"index": 1}, _plain())
		t.record("wait", {"seconds": 1}, ActionResult())
		assert t.peek_nudge() is None

	def test_error_results_ignored(self):
		"""错误路径无 query_total（由 FailureStreakTracker 管）——两通道不串。"""
		t = ZeroResultStreakTracker()
		t.record("read_grid", {"filters": {"a": 1}}, ActionResult(error="boom"))
		assert t.peek_nudge() is None

	def test_debounce_notified_once_until_reset(self):
		"""去抖：同键只报一次——streak 冻结（agent 转做别的）不重复注入。"""
		t = ZeroResultStreakTracker()
		params = {"selector": ".missing"}
		for _ in range(5):
			t.record("find_elements", params, _qr(0, "selector '.missing'"))
		c1 = t.peek_nudge()
		assert c1 is not None
		t.ack_nudge(c1[0])
		assert t.peek_nudge() is None  # 已通知，streak 冻结不再报

	def test_peek_does_not_consume_until_acked(self):
		"""peek 只读——ack 前重复 peek 同一候选（LLM 失败步不 ack，下步重发）。"""
		t = ZeroResultStreakTracker()
		params = {"query": "zz"}
		t.record("search_page", params, _qr(0))
		t.record("search_page", params, _qr(0))
		c1 = t.peek_nudge()
		c2 = t.peek_nudge()
		assert c2 == c1
		t.ack_nudge(c1[0])
		assert t.peek_nudge() is None

	def test_empty_selector_or_query_ignored(self):
		t = ZeroResultStreakTracker()
		t.record("find_elements", {"selector": ""}, _qr(0))
		t.record("search_page", {"query": ""}, _qr(0))
		assert t.peek_nudge() is None


class TestQueryKeyNormalization:
	def test_read_grid_filters_order_insensitive(self):
		"""filters 键序不影响查询身份（dict 序列化 sort_keys）。"""
		k1 = ZeroResultStreakTracker._query_key(
			"read_grid", {"filters": {"a": 1, "b": 2}})
		k2 = ZeroResultStreakTracker._query_key(
			"read_grid", {"filters": {"b": 2, "a": 1}})
		assert k1[0] == k2[0]

	def test_read_grid_unfiltered_key(self):
		k = ZeroResultStreakTracker._query_key("read_grid", {})
		assert k is not None
		assert k[0].startswith("read_grid|")  # 空参归一为固定键（身份一致即可）

	def test_page_and_page_size_do_not_change_key(self):
		"""翻页/页大小不是查询身份——同一过滤翻页仍算同一查询。"""
		k1 = ZeroResultStreakTracker._query_key(
			"read_grid", {"filters": {"status": "pending"}, "page": 1})
		k2 = ZeroResultStreakTracker._query_key(
			"read_grid", {"filters": {"status": "pending"}, "page": 2})
		assert k1[0] == k2[0]


class TestQueryTotalMetadata:
	"""三个查询类动作的 metadata 管道（成功路径设置、错误路径不设）。"""

	@pytest.mark.asyncio
	async def test_read_grid_zero_and_nonzero_totals(self):
		from tests.test_read_grid import _FakeBrowser, _ui_result
		import json as _json
		# total_records=0
		browser = _FakeBrowser(ui_result=_ui_result(rows=[], total_records=0))
		r = await Tools().execute("read_grid", {"filters": {"name": "X"}}, browser)
		assert r.error is None
		assert r.metadata["query_total"] == 0
		assert "name" in r.metadata["query_desc"]
		# total_records=308 → query_total=308
		browser2 = _FakeBrowser(ui_result=_ui_result(rows=[{"a": 1}], total_records=308))
		r2 = await Tools().execute("read_grid", {}, browser2)
		assert r2.metadata["query_total"] == 308

	@pytest.mark.asyncio
	async def test_read_grid_error_path_no_metadata(self):
		"""三通道全灭 → ActionResult.error → 无 query_total（错误归 streak 通道）。"""
		from tests.test_read_grid import _FakeBrowser
		import json as _json
		browser = _FakeBrowser(evaluate_side_effects=[
			_json.dumps({"channel_error": "no-legacy-grid"}),
			_json.dumps({"channel_error": "no-table"}),
		])
		r = await Tools().execute("read_grid", {"filters": {"name": "X"}}, browser)
		assert r.error is not None
		assert (r.metadata or {}).get("query_total") is None

	@pytest.mark.asyncio
	async def test_find_elements_zero_sets_metadata(self):
		browser = MagicMock()
		browser.find_elements = AsyncMock(return_value={"total": 0, "results": []})
		r = await Tools().execute(
			"find_elements", {"selector": ".missing"}, browser)
		assert r.error is None
		assert r.metadata["query_total"] == 0
		assert ".missing" in r.metadata["query_desc"]

	@pytest.mark.asyncio
	async def test_find_elements_nonzero_sets_metadata(self):
		browser = MagicMock()
		browser.find_elements = AsyncMock(return_value={
			"total": 3, "results": [
				{"tag": "button", "text": "a"},
				{"tag": "button", "text": "b"},
				{"tag": "button", "text": "c"},
			]})
		r = await Tools().execute("find_elements", {"selector": ".btn"}, browser)
		assert r.metadata["query_total"] == 3

	@pytest.mark.asyncio
	async def test_find_elements_error_no_metadata(self):
		browser = MagicMock()
		browser.find_elements = AsyncMock(side_effect=RuntimeError("cdp down"))
		r = await Tools().execute("find_elements", {"selector": ".x"}, browser)
		assert r.error is not None
		assert (r.metadata or {}).get("query_total") is None

	@pytest.mark.asyncio
	async def test_search_page_zero_and_nonzero(self):
		browser = MagicMock()
		browser.search_page = AsyncMock(return_value={
			"total": 0, "matches": [], "attribute_matches": []})
		r = await Tools().execute("search_page", {"query": "zzz"}, browser)
		assert r.error is None
		assert r.metadata["query_total"] == 0
		browser.search_page = AsyncMock(return_value={
			"total": 2, "matches": [
				{"text": "zzz", "context": "", "path": ""},
				{"text": "zzz", "context": "", "path": ""},
			], "attribute_matches": []})
		r2 = await Tools().execute("search_page", {"query": "zzz"}, browser)
		assert r2.metadata["query_total"] == 2
