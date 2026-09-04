"""Tests for P1b recent_events —— CDP dialog 事件采集 + state 消息 [Recent Events] 渲染。

对齐方案：``docs/agent-loop-optimize/01-准备上下文对齐browser-use方案.md`` §5.2。

范围说明：首期只接 ``dialog``（``Page.javascriptDialogOpening``）。download 由
``consume_completed_downloads`` → ``[Downloads]`` 段覆盖；cdp_use 单回调机制
（``registry._handlers[method] = callback`` 覆盖式）下不能与 download tracking 双注册
``Browser.downloadWillBegin``，故 recent_events 不监听 download。
"""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

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
		"""P7 form_interaction 建议3 起 dialog 回调由 _connect 无条件注册（自动处理），
		enable_recent_events 不再决定注册与否——只保留给未来的其他事件类型。"""
		mock = _make_mock_cdp_client()
		with patch("tree_walker.browser.session.CDPClient", return_value=mock):
			s = BrowserSession(ws_url="ws://fake")
			await s.start(enable_recent_events=False)
		assert s._enable_recent_events is False
		# 行为变化（有意）：回调仍然注册（自动处理 always-on）
		mock.register.Page.javascriptDialogOpening.assert_called_once()


class TestDialogAutoHandle:
	"""P7 form_interaction 建议3：dialog 自动处理（493 挂死样本的对症修复）。"""

	def _session(self) -> BrowserSession:
		s = BrowserSession(ws_url="ws://fake")
		s.client = MagicMock()
		s.client.send.Page.handleJavaScriptDialog = AsyncMock(return_value={})
		return s

	@pytest.mark.asyncio
	async def test_confirm_dialog_auto_dismissed(self):
		s = self._session()
		s._auto_dialog_enabled = True
		s._loop = asyncio.get_running_loop()
		await s._setup_event_tracking()
		cb = s.client.register.Page.javascriptDialogOpening.call_args[0][0]

		cb({"message": "Are you sure?", "type": "confirm"}, session_id="sid-9")
		# 让 call_soon_threadsafe 调度的任务在 loop 上跑完
		await asyncio.sleep(0.05)

		# confirm → dismiss（不替用户确认危险操作）
		s.client.send.Page.handleJavaScriptDialog.assert_awaited_once_with(
			{"accept": False, "promptText": ""}, session_id="sid-9",
		)
		# 处理动作记入 recent_events（对 LLM 可见）
		events = s.consume_recent_events()
		assert len(events) == 1
		assert "auto-dismissed" in events[0].message

	@pytest.mark.asyncio
	async def test_beforeunload_auto_accepted(self):
		"""beforeunload → accept：放行 agent 自己发起的导航（dismiss 会把导航拦回去）。"""
		s = self._session()
		s._auto_dialog_enabled = True
		s._loop = asyncio.get_running_loop()
		await s._setup_event_tracking()
		cb = s.client.register.Page.javascriptDialogOpening.call_args[0][0]

		cb({"message": "Leave site?", "type": "beforeunload"})
		await asyncio.sleep(0.05)

		s.client.send.Page.handleJavaScriptDialog.assert_awaited_once_with(
			{"accept": True, "promptText": ""}, session_id=None,
		)
		assert "auto-accepted" in s.consume_recent_events()[0].message

	@pytest.mark.asyncio
	async def test_disabled_flag_skips_handling(self):
		"""BROWSER_AUTO_HANDLE_JS_DIALOG=false 时只记录、不处理。"""
		s = self._session()
		s._auto_dialog_enabled = False
		s._loop = asyncio.get_running_loop()
		await s._setup_event_tracking()
		cb = s.client.register.Page.javascriptDialogOpening.call_args[0][0]

		cb({"message": "hi", "type": "alert"})
		await asyncio.sleep(0.05)

		s.client.send.Page.handleJavaScriptDialog.assert_not_awaited()
		assert len(s.consume_recent_events()) == 1

	@pytest.mark.asyncio
	async def test_handle_failure_is_soft(self):
		"""handleJavaScriptDialog 失败（dialog 已被页面侧关闭等）不抛。"""
		s = self._session()
		s._auto_dialog_enabled = True
		s._loop = asyncio.get_running_loop()
		s.client.send.Page.handleJavaScriptDialog = AsyncMock(
			side_effect=RuntimeError("No dialog is showing"),
		)
		await s._setup_event_tracking()
		cb = s.client.register.Page.javascriptDialogOpening.call_args[0][0]

		cb({"message": "x", "type": "alert"})
		await asyncio.sleep(0.05)  # 不应抛异常

	@pytest.mark.asyncio
	async def test_no_loop_schedules_nothing(self):
		"""_loop 未设（极端时序）时回调安全返回，只记录。"""
		s = self._session()
		s._auto_dialog_enabled = True
		assert s._loop is None
		await s._setup_event_tracking()
		cb = s.client.register.Page.javascriptDialogOpening.call_args[0][0]

		cb({"message": "x", "type": "alert"})
		await asyncio.sleep(0.05)

		s.client.send.Page.handleJavaScriptDialog.assert_not_awaited()
		assert len(s.consume_recent_events()) == 1

	@pytest.mark.asyncio
	async def test_start_setup_failure_degrades_gracefully(self):
		"""dialog 注册失败（_connect 内）不能拖垮启动——降级为不处理，start 仍成功。"""
		mock = _make_mock_cdp_client()
		mock.register.Page.javascriptDialogOpening.side_effect = RuntimeError("boom")
		with patch("tree_walker.browser.session.CDPClient", return_value=mock):
			s = BrowserSession(ws_url="ws://fake")
			await s.start(enable_recent_events=True)  # 不应抛
		assert s._enable_recent_events is True  # flag 照设；仅注册降级


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
