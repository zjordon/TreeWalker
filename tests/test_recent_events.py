"""Tests for P1b recent_events —— CDP dialog 事件采集 + state 消息 [Recent Events] 渲染。

对齐方案：``docs/agent-loop-optimize/01-准备上下文对齐browser-use方案.md`` §5.2。

范围说明：首期只接 ``dialog``（``Page.javascriptDialogOpening``）。download 由
``consume_completed_downloads`` → ``[Downloads]`` 段覆盖；cdp_use 单回调机制
（``registry._handlers[method] = callback`` 覆盖式）下不能与 download tracking 双注册
``Browser.downloadWillBegin``，故 recent_events 不监听 download。
"""

import pytest
from unittest.mock import MagicMock, patch

from tree_walker.browser.session import BrowserSession
from tree_walker.browser.views import BrowserEvent, BrowserStateSummary, SerializedDOMState
from tree_walker.prompts.system_prompt import build_state_message

from tests.test_browser_session import _make_mock_cdp_client


def _event(message: str = "m", type_: str = "dialog", ts: float = 1.0) -> BrowserEvent:
	return BrowserEvent(type=type_, message=message, timestamp=ts)


def _state(events=None, dom_text: str = "dom") -> BrowserStateSummary:
	return BrowserStateSummary(
		url="https://example.com",
		title="Ex",
		dom_state=SerializedDOMState(_root=None, selector_map={}, element_tree_text=dom_text),
		recent_events=events or [],
	)


class TestBrowserEvent:
	def test_construction(self):
		ev = _event("[alert] hi", ts=1.5)
		assert ev.type == "dialog"
		assert ev.message == "[alert] hi"
		assert ev.timestamp == 1.5


class TestBrowserStateSummaryDefault:
	def test_recent_events_default_empty(self):
		assert BrowserStateSummary().recent_events == []


class TestRecordAndConsume:
	def test_record_consume_clears(self):
		s = BrowserSession(ws_url="ws://fake")
		s.record_event(_event("m1"))
		s.record_event(_event("m2"))
		events = s.consume_recent_events()
		assert [e.message for e in events] == ["m1", "m2"]
		# 二次 consume 为空（已清）
		assert s.consume_recent_events() == []

	def test_consume_empty(self):
		s = BrowserSession(ws_url="ws://fake")
		assert s.consume_recent_events() == []

	def test_deque_maxlen_drops_oldest(self):
		"""deque(maxlen=20) 自动丢弃溢出（最早的先丢）。"""
		s = BrowserSession(ws_url="ws://fake")
		for i in range(25):
			s.record_event(_event(f"m{i}", ts=float(i)))
		events = s.consume_recent_events()
		assert len(events) == 20
		assert events[0].message == "m5"  # m0..m4 被丢
		assert events[-1].message == "m24"


class TestSetupEventTracking:
	@pytest.mark.asyncio
	async def test_registers_dialog_callback(self):
		s = BrowserSession(ws_url="ws://fake")
		s.client = MagicMock()
		await s._setup_event_tracking()
		s.client.register.Page.javascriptDialogOpening.assert_called_once()

	@pytest.mark.asyncio
	async def test_dialog_callback_records_event(self):
		s = BrowserSession(ws_url="ws://fake")
		s.client = MagicMock()
		await s._setup_event_tracking()
		cb = s.client.register.Page.javascriptDialogOpening.call_args[0][0]
		# 模拟 CDP javascriptDialogOpening 事件（alert/confirm/prompt/beforeunload）
		cb({"message": "Are you sure?", "type": "confirm"})
		events = s.consume_recent_events()
		assert len(events) == 1
		assert events[0].type == "dialog"
		assert "[confirm]" in events[0].message
		assert "Are you sure?" in events[0].message

	@pytest.mark.asyncio
	async def test_dialog_callback_empty_message_falls_back_to_url(self):
		s = BrowserSession(ws_url="ws://fake")
		s.client = MagicMock()
		await s._setup_event_tracking()
		cb = s.client.register.Page.javascriptDialogOpening.call_args[0][0]
		cb({"url": "https://x.com", "type": "beforeunload"})  # 无 message
		events = s.consume_recent_events()
		assert len(events) == 1
		assert "beforeunload" in events[0].message


class TestStartIntegration:
	@pytest.mark.asyncio
	async def test_start_enabled_calls_setup(self):
		mock = _make_mock_cdp_client()
		with patch("tree_walker.browser.session.CDPClient", return_value=mock):
			s = BrowserSession(ws_url="ws://fake")
			await s.start(enable_recent_events=True)
		assert s._enable_recent_events is True
		mock.register.Page.javascriptDialogOpening.assert_called_once()

	@pytest.mark.asyncio
	async def test_start_disabled_does_not_setup(self):
		mock = _make_mock_cdp_client()
		with patch("tree_walker.browser.session.CDPClient", return_value=mock):
			s = BrowserSession(ws_url="ws://fake")
			await s.start(enable_recent_events=False)
		assert s._enable_recent_events is False
		mock.register.Page.javascriptDialogOpening.assert_not_called()

	@pytest.mark.asyncio
	async def test_start_setup_failure_degrades_gracefully(self):
		"""事件采集注册失败不能拖垮启动——降级为关闭。"""
		mock = _make_mock_cdp_client()
		mock.register.Page.javascriptDialogOpening.side_effect = RuntimeError("boom")
		with patch("tree_walker.browser.session.CDPClient", return_value=mock):
			s = BrowserSession(ws_url="ws://fake")
			await s.start(enable_recent_events=True)  # 不应抛
		assert s._enable_recent_events is False


class TestRecentEventsRendering:
	def test_renders_events(self):
		msg = build_state_message(
			_state(events=[_event("[alert] hi", ts=1.0), _event("[confirm] ok", ts=2.0)]),
			task="t",
		)
		assert "[Recent Events]" in msg
		assert "dialog: [alert] hi" in msg
		assert "dialog: [confirm] ok" in msg

	def test_no_section_when_empty(self):
		"""无事件（或 enable_recent_events=False → 队列空）不渲染。"""
		msg = build_state_message(_state(events=[]), task="t")
		assert "[Recent Events]" not in msg

	def test_capped_to_five_newest_first(self):
		"""最多 5 条、倒序（最新在前）。"""
		events = [_event(f"m{i}", ts=float(i)) for i in range(10)]
		msg = build_state_message(_state(events=events), task="t")
		assert "[Recent Events]" in msg
		assert "m9" in msg and "m5" in msg  # 最近 5 条（m5..m9）
		assert "m4" not in msg  # 超出 5 条被截
		assert msg.index("m9") < msg.index("m5")  # 倒序：最新在前

	def test_recent_events_before_page_dom(self):
		msg = build_state_message(_state(events=[_event("hi")]), task="t")
		assert msg.index("[Recent Events]") < msg.index("[Page DOM]")
