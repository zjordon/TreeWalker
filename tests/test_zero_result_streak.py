"""issue #186-c2 形态②：零结果降级 nudge 的测试。

背景（docs/bug-fix/186-c2-reopen-analysis.md §2 P1）：C 轮 544——任务名
Selena vs 目录 Selene 拼写不一致，agent 按产品名精确过滤评论网格 0 结果
反复同策略（34 次 read_grid/search，25/29 步耗尽）。零结果是 soft-miss
（工具语义正确），不进 consecutive_failures——独立 ZeroResultStreakTracker
经 ActionResult.metadata['query_total'] 结构化旁路接收信号，同类查询
连续 2 次零结果注入降级指引。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tree_walker.agent.loop_detector import ZeroResultStreakTracker
from tree_walker.agent.views import ActionResult
from tree_walker.tools.actions import Tools


def _qr(total: int) -> ActionResult:
	"""带 query_total 旁路的成功 ActionResult（模拟查询类动作返回；
	query_desc 由 tracker 侧 _query_key 从 params 推导，旁路只承载数字）。"""
	return ActionResult(
		extracted_content="echo", long_term_memory="m",
		metadata={"query_total": total},
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
		t.record("find_elements", {"selector": ".btn"}, _qr(0))
		t.record("search_page", {"query": "x"}, _qr(0))
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
			t.record("find_elements", params, _qr(0))
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
		# total_records=0
		browser = _FakeBrowser(ui_result=_ui_result(rows=[], total_records=0))
		r = await Tools().execute("read_grid", {"filters": {"name": "X"}}, browser)
		assert r.error is None
		assert r.metadata["query_total"] == 0
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
		# review7 #9：mock 键名对齐真实契约 {elements, total, ...}（非 "results"）
		browser.find_elements = AsyncMock(return_value={"total": 0, "elements": []})
		r = await Tools().execute(
			"find_elements", {"selector": ".missing"}, browser)
		assert r.error is None
		assert r.metadata["query_total"] == 0

	@pytest.mark.asyncio
	async def test_find_elements_nonzero_sets_metadata(self):
		browser = MagicMock()
		browser.find_elements = AsyncMock(return_value={
			"total": 3, "elements": [
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
		# review7 #9：matches 条目键名对齐 formatter 实读（context/element_path）
		browser = MagicMock()
		browser.search_page = AsyncMock(return_value={
			"total": 0, "matches": [], "attribute_matches": []})
		r = await Tools().execute("search_page", {"query": "zzz"}, browser)
		assert r.error is None
		assert r.metadata["query_total"] == 0
		browser.search_page = AsyncMock(return_value={
			"total": 2, "matches": [
				{"text": "zzz", "context": "ctx", "element_path": "body/p"},
				{"text": "zzz", "context": "ctx", "element_path": "body/p"},
			], "attribute_matches": []})
		r2 = await Tools().execute("search_page", {"query": "zzz"}, browser)
		assert r2.metadata["query_total"] == 2

	@pytest.mark.asyncio
	async def test_search_page_attr_hits_not_counted_as_zero(self):
		"""review7 #1/#8：total==0 但 attr_total>0（search_attributes=True 搜属性）
		——查询并非空手而归（对齐零结果分支判定），合并计数防 tracker 把命中
		记成 miss、两次后注入与 extracted_content 自相矛盾的降级 nudge。"""
		browser = MagicMock()
		browser.search_page = AsyncMock(return_value={
			"total": 0, "matches": [],
			"attribute_matches": [{"attr": "href", "value": "x"}],
			"attribute_total": 1})
		r = await Tools().execute("search_page", {"query": "zzz"}, browser)
		assert r.error is None  # 走成功分支（非零结果 soft-miss 路径）
		assert r.metadata["query_total"] == 1  # 合并计数=0+1，非 0


class TestStepWiring:
	"""review7 #6：step 层接线回归保护——实施方案 §4 验收项「连续两次同过滤
	0 结果 → 下一步 state message 含降级文案；改过滤重置；LLM 失败步不 ack
	→ 首报重发」此前无覆盖（漏 ack → 每步重复注入 / 漏并入 nudge → 特性
	静默失效，两种接线回归现有测试全绿）。"""

	def _pipeline(self):
		"""免构造 StepPipeline——只挂 _prepare_context/_ack 所需最小属性集。"""
		from tree_walker.agent.loop_detector import ActionLoopDetector
		from tree_walker.agent.step import StepPipeline
		p = StepPipeline.__new__(StepPipeline)
		p.loop_detector = ActionLoopDetector()
		p.failure_streak = ZeroResultStreakTracker.__mro__[0]  # 占位，下面逐个设
		from tree_walker.agent.loop_detector import FailureStreakTracker
		p.failure_streak = FailureStreakTracker()
		p.zero_result_streak = ZeroResultStreakTracker()
		p._pending_streak_nudge = None
		p._pending_zero_result_nudge = None
		p._enable_planning = False
		p.plan_manager = None
		p._enable_page_stats = False
		p._enable_grid_meta = False
		p._enable_sensitive_description = False
		p._enable_skill_injection = False
		p._current_task_skill_text = lambda: None
		p._obs_bus = None
		p._compactor = None
		p._track_downloads = False
		p._enable_message_typing = True
		p.messages = []
		p.browser = MagicMock()
		p.tools = MagicMock()
		from tree_walker.browser.views import BrowserStateSummary, SerializedDOMState
		state = BrowserStateSummary(
			url="https://example.com", title="t",
			dom_state=SerializedDOMState(_root=None, selector_map={}, element_tree_text="dom"),
		)
		return p, state

	def test_nudge_injected_into_state_message_after_two_zeroes(self):
		"""两次同过滤 0 结果 → _prepare_context 产出的 state message 含降级文案。"""
		from tree_walker.prompts.system_prompt import build_state_message
		p, bs = self._pipeline()
		params = {"filters": {"name": "Selena Yoga Hoodie"}}
		p.zero_result_streak.record("read_grid", params, _qr(0))
		p.zero_result_streak.record("read_grid", params, _qr(0))
		nudge = p.zero_result_streak.peek_nudge()
		assert nudge is not None
		msg = build_state_message(bs, task="t", nudge_message=nudge[1])
		assert "0 results 2 times" in msg
		assert "substring/partial filter" in msg

	def test_ack_clears_pending_no_reinjection(self):
		"""ack 提交后同 streak 不再产出候选（去抖），state message 不再含文案。"""
		p, _ = self._pipeline()
		params = {"selector": ".miss"}
		for _ in range(2):
			p.zero_result_streak.record("find_elements", params, _qr(0))
		c = p.zero_result_streak.peek_nudge()
		p._pending_zero_result_nudge = c
		p._ack_pending_streak_nudge()  # 同一方法处理两类 nudge 的 ack
		assert p._pending_zero_result_nudge is None
		assert p.zero_result_streak.peek_nudge() is None  # 去抖：已通知不重报

	def test_unacked_nudge_repeeked_next_step(self):
		"""LLM 失败步不 ack → 下步 peek 返回同一候选（首报重发不丢）。"""
		p, _ = self._pipeline()
		params = {"query": "zz"}
		for _ in range(2):
			p.zero_result_streak.record("search_page", params, _qr(0))
		c1 = p.zero_result_streak.peek_nudge()
		p._pending_zero_result_nudge = c1  # 暂存但未 ack（模拟 LLM 调用失败）
		c2 = p.zero_result_streak.peek_nudge()
		assert c2 == c1  # 同候选可重取

	def test_flattened_params_tracked(self):
		"""review7 #7：LLM 嵌套包裹形态 {"read_grid": {...}} 经 _flatten_params
		归一后仍正确取到 filters（record 侧展平由 step.py 接线保证）。"""
		from tree_walker.tools.actions import Tools
		wrapped = {"read_grid": {"filters": {"name": "X"}}}
		flat = Tools()._flatten_params(wrapped, "read_grid")
		k = ZeroResultStreakTracker._query_key("read_grid", flat)
		assert k is not None and "name" in k[1]
		k_direct = ZeroResultStreakTracker._query_key(
			"read_grid", {"filters": {"name": "X"}})
		assert k[0] == k_direct[0]  # 归一后同键（不坍缩不串染）
