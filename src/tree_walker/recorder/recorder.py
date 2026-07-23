"""录制核心：扩展事件 → 翻译管线 → CDP 算指纹 → Recording → flatten → 落盘。

架构（见 ``docs/user_recording/redesign.md`` / ``redesign-impl-plan.md``）：

    扩展事件 → handle_event
                 ├─ Stage1 translate_event（映射 + 连续 input 聚合 + 状态机）
                 ├─ 实时 locate + 指纹投影（modal DOM 活着时算，存 ActionRecord）
                 └─ → Recording.actions
    /signal → attach_signal（modal/dropdown 信号附到最近动作）
    stop → Stage3 apply_rules（signal 感知去噪）→ flatten → AgentHistoryList → 落盘

``Recorder`` 连接用户浏览器（remote-debugging-port CDP）。指纹（``element_hash``/``stable_hash``）
由 TreeWalker 自身代码算（``DOMInteractedElement.load_from_enhanced_dom_tree``），录制侧与重放侧
同源 → EXACT/STABLE 匹配天然有效。

**定位 + 指纹在事件到达时实时做**（而非落盘时），因为 modal 打开时 DOM 是活的——stop 时
modal 已关、DOM 里没了，落盘再 locate 必失败。结果存进 ``ActionRecord.interacted_element`` 与
``params['index']``，``flatten`` 纯 reshape 不再二次定位。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any

from tree_walker.agent.rerun import resolve_rerun_path
from tree_walker.agent.views import AgentHistory, AgentHistoryList, StepMetadata
from tree_walker.browser.session import BrowserSession
from tree_walker.browser.views import DOMInteractedElement
from tree_walker.recorder.event_mapper import needs_target
from tree_walker.recorder.flatten import flatten
from tree_walker.recorder.locator import locate_by_ref
from tree_walker.recorder.models import ActionRecord, Recording, RecordingState, SignalKind, signal_from_payload
from tree_walker.recorder.rules import apply_rules
from tree_walker.recorder.translation import translate_event

logger = logging.getLogger(__name__)

# /signal 来的副作用信号附到最近动作的时间窗（秒）；超窗视为陈旧，丢弃。
_SIGNAL_WINDOW_S = 2.0
# 首次定位失败后，重试前等页面渲染的延时（秒），递增。SPA 元素常在事件后几百 ms 才出现
# （video file input 瞬态、modal 关闭后才渲染的按钮等）；录到无指纹噪声步多为 get_state
# 抢在元素渲染前/页面过渡态——等几档重新 get_state + 定位救回。两档兼顾「渲染慢」与
# 「modal/动画收尾」。
_LOCATE_RETRY_DELAYS = (0.6, 1.5)
# stop() 等待 in-flight handle_event 释放锁的上限（秒）。正常事件 ≤ get_state + 重试 ≈ 数秒；
# 设宽裕上限：若 get_state 卡死（页面/CDP 无响应），stop 超时后强制落盘（末步可能不完整，
# 但至少有产物，避免 stop 永久挂起）。
_STOP_LOCK_TIMEOUT_S = 15.0


def select_http_target(
	target_infos: list[dict[str, Any]],
	event_url: str | None = None,
) -> str | None:
	"""从 CDP ``Target.getTargets`` 的 targetInfos 里选应操作的 http page target。

	跳过 ``chrome-extension://``、``chrome://``、``devtools://`` 等内部页——点开始录制时
	``BrowserSession._connect`` 可能误 attach 到刚弹出的扩展 popup。优先 url 匹配
	``event_url`` 的；否则返回第一个 http page。返回 ``targetId`` 或 ``None``。
	"""
	def _is_http(url: str) -> bool:
		return url.startswith("http://") or url.startswith("https://")

	http_pages = [
		t for t in target_infos
		if t.get("type") == "page" and _is_http(t.get("url", ""))
	]
	if not http_pages:
		return None
	if event_url:
		def _norm(u: str) -> str:
			return u.rstrip("/").split("#", 1)[0]
		want = _norm(event_url)
		for t in http_pages:
			if _norm(t.get("url", "")) == want:
				return t.get("targetId")
	return http_pages[0].get("targetId")


class Recorder:
	"""录制用户操作 → ``AgentHistory``（经 ``Recording`` 内部模型）。

	依赖注入 ``browser``（``BrowserSession`` 或任何带 ``start``/``stop``/``get_state`` 的对象，
	便于测试 mock）。
	"""

	def __init__(
		self,
		browser: BrowserSession,
		rerun_history_dir: str,
		registry_version: str | None = None,
		upload_dir: str = "",
	) -> None:
		self.browser = browser
		self.rerun_history_dir = rerun_history_dir
		# upload_file 约定目录：空 → <rerun_history_dir>/uploads（扩展只能拿文件名）
		self.upload_dir = upload_dir or os.path.join(rerun_history_dir, "uploads")
		self.registry_version = registry_version
		# 内部模型：事件 → ActionRecord 累积于此（承载 signal + 状态机）
		self.recording = Recording(state=RecordingState())
		# 落盘格式：stop 时 flatten 赋值；server.py 的 /stop 响应读 len(self.history.history)
		self.history: AgentHistoryList = AgentHistoryList()
		self._recording = False
		# 串行化 start/handle_event/stop/attach_signal：aiohttp 并发处理 /event 与 /stop，不加锁则
		# 末步事件还在 await get_state/重试时 /stop 就跑 flatten，落盘半成品 action（issue #136 关联）。
		self._lock = asyncio.Lock()

	async def start(self) -> None:
		"""开始录制：连浏览器，初始化空录制（加锁，避免与 in-flight 事件/stop 竞态）。"""
		async with self._lock:
			await self.browser.start()
			await self._disable_file_chooser_intercept()  # 让原生文件选择框正常弹出
			self._recording = True
			self.recording = Recording(state=RecordingState())
			self.history = AgentHistoryList()
		logger.info("录制开始")

	async def handle_event(self, event: dict[str, Any]) -> Any:
		"""处理一条扩展事件 → ``ActionRecord`` 追加（加锁串行；实际逻辑见 ``_handle_event_impl``）。"""
		# 加锁串行（与 stop/attach_signal）：aiohttp 并发处理 /event 与 /stop，不加锁则末步事件还在
		# await get_state/重试 sleep 时 /stop 就 flatten 落盘了半成品 action（click {} + null
		# interacted + 空 url）。这**不是异常**（三重兜底本会存语义线索），是 stop 没等事件跑完就
		# 落盘——故历次"扩大异常捕获"均无效。根因 = 并发竞态（issue #136 关联）。
		async with self._lock:
			if not self._recording:
				return None
			return await self._handle_event_impl(event)

	async def _handle_event_impl(self, event: dict[str, Any]) -> Any:
		"""handle_event 实际逻辑（调用方已持 ``self._lock``，且 ``self._recording`` 为 True）。

		实时做定位 + 指纹投影（modal DOM 活着时），结果填进 ``ActionRecord.params['index']``
		与 ``interacted_element``。连续同框 ``input_text`` 由 ``translate_event`` 聚合（取最终值，
		返回 None 不追加）。返回追加的 ``ActionRecord`` 或 None。

		target 动作（click/input_text/select_dropdown）三重兜底确保永不以
		``interacted_element=null/[]`` 落盘被回放当噪声步跳过（issue #129）：①定位未命中/异常 →
		``_store_semantic_clue``；②外层 except 捕获 translate_event / 定位块逃逸（含 CancelledError）
		→ 存语义线索；③末尾保底——任何路径下 interacted 仍空则强制存。语义线索供重放端重新定位。
		"""
		action: ActionRecord | None = None
		state: Any = None
		retried = False
		pending_exc: BaseException | None = None
		appended_at = len(self.recording.actions)  # translate_event 前；它抛时 action 变量为 None 但
		#                                      recording.actions[-1] 是它已 append 的残缺 target，靠此定位兜底
		try:
			# Stage 1：事件 → ActionRecord（含聚合判定 + 状态机更新）。聚合/不可映射 → None。
			# translate_event 纳入外层 try：其自身异常（页面卸载瞬间的残缺事件等）也能被兜底，
			# 避免已 append 的 target 动作以 interacted=null 落盘被回放跳过（issue #129 补全）。
			action = translate_event(event, self.recording)
			if action is None:
				logger.debug("忽略事件（不可映射或已聚合进前一步）: %s", event.get("type"))
				return None

			raw_params = event.get("params") or {}

			# tab 动作：扩展发目标 tab 的 url，后端解析成 CDP targetId 后4位（重放侧期望格式）
			if action.action_name in ("switch_tab", "close_tab"):
				action.params["tab_id"] = await self._resolve_tab_id(raw_params.get("url"))

			# upload_file：扩展只发文件名（浏览器安全限制），拼约定目录
			if action.action_name == "upload_file":
				action.params["path"] = self._resolve_upload_path(
					action.params.get("path") or raw_params.get("path") or "",
				)

			# 下面 _ensure_target + get_state + 定位 + 回填若因动作触发跳转（submit/链接/「暂存离开」）
			# 致 CDP target 卸载/切换而抛 BaseException（CancelledError 等不被内层 except Exception 抓）
			# → 由本层 except BaseException 兜底；末尾保底再补一刀。三重保证 target 动作永不以
			# interacted=null 落盘（issue #129）。_ensure_target 纳入：submit 跳转时其
			# Target.getTargets/switch_tab 在 target 切换瞬间会抛 BaseException（httpbin 表单 submit
			# 实测复现，httpbin-5.json.json idx13）。
			located = None
			await self._ensure_target(event.get("url"))
			try:
				state = await self.browser.get_state(include_screenshot=False)
			except Exception as e:
				# get_state 可能因动作触发跳转（submit/链接）致 CDP target 卸载/切换而抛异常。
				# 容错为 None，让后续 locate loop 走"失败存语义线索"路径（重放端重新定位）。
				logger.warning("get_state 失败（%s）——用空 selector_map，locate 将存语义线索", e)
				state = None
			selector_map = state.dom_state.selector_map if state and state.dom_state else {}

			# 实时定位 + 指纹投影（modal 打开时 DOM 活着，此时算才准）。
			# upload_file 不算指纹（导航竞态 + Semi-UI 上传后重建 input，录制端算指纹结构性不可达——
			# 见 docs/user_recording/recorder-timing-solutions.md）：存 accept+xpath 签名，重放端按 accept
			# 解析（_resolve_file_input_by_accept）。click/input/select_dropdown 走 locate_by_ref
			# （xpath→属性→RECT），支持重试（modal 后渲染的按钮等，首次 get_state 抢前）。
			if action.action_name == "upload_file":
				action.params["accept"] = action.params.get("accept") or ""
				action.params["xpath"] = event.get("xpath") or ""
				locate = None
			elif needs_target(action.action_name):
				ref_dict = action.element_ref.to_ref_dict() if action.element_ref is not None else {}
				locate = lambda sm: locate_by_ref(ref_dict, sm)
			else:
				locate = None

			if locate is not None:
				located = locate(selector_map)
				if located is None:
					# 重试：等页面渲染/动画收尾，重新 get_state + 定位
					for delay in _LOCATE_RETRY_DELAYS:
						await asyncio.sleep(delay)
						try:
							state = await self.browser.get_state(include_screenshot=False)
						except Exception as e:
							logger.debug("retry get_state 失败: %s", e)
							state = None
						selector_map = state.dom_state.selector_map if state and state.dom_state else {}
						located = locate(selector_map)
						retried = True
						if located is not None:
							break
				if located is None:
					logger.warning(
						"定位失败 action=%s xpath=%s tag=%s rect=%s%s（存语义线索，重放端重新定位；记 locate_miss）",
						action.action_name, event.get("xpath"), event.get("tag"), event.get("rect"),
						"（重试后仍失败）" if retried else "",
					)
					self._store_semantic_clue(action, event, retried, len(selector_map))
				else:
					index, node = located
					action.params["index"] = index
					ie = DOMInteractedElement.load_from_enhanced_dom_tree(node).to_dict()
					# 扩展捕获的点击瞬间文字（ground truth）——重放端 TEXT 级优先按它定位（issue #136）。
					# text 为主、ax_name 兜底：get_state 在动作后跑，状态依赖元素的名称/类可能已是动作后状态。
					_evt_text = event.get("text")
					if _evt_text:
						ie["text"] = _evt_text
					action.interacted_element = [ie]
			else:
				# 无定位（upload_file 不 get_state 定位 / navigate 等无 target 动作）。
				# upload_file 置 [None]（有目标但重放端解析）；其余置 []（无 target）。
				action.interacted_element = [None] if action.action_name == "upload_file" else []
		except BaseException as e:
			# 任何逃逸（translate_event 阶段 / _ensure_target / 定位块 / CancelledError 等 BaseException）：
			# 记下异常待重抛，target 动作在此兜底存语义线索；末尾保底再校验一次确保 interacted 非空。
			# upload_file/navigate 等非 locate_by_ref 动作不在此覆盖（保持各自原值）。
			pending_exc = e
			if action is not None and action.action_name in ("click", "input_text", "select_dropdown"):
				logger.warning("handle_event 异常 action=%s（%s）：兜底存语义线索后重抛", action.action_name, e)
				self._store_semantic_clue(action, event, True, 0)

		# 统一兜底（issue #129 补全）：本事件 append 的 target 动作若经任何逃逸路径仍未回填
		# interacted_element → 强制存语义线索。确保 click/input_text/select_dropdown 永不以
		# interacted=null/[] 落盘被回放当噪声步跳过。this_action：正常路径 = action 变量；
		# translate_event 抛（action 变量为 None，但它已 append 残缺 target）= recording.actions[-1]。
		# upload_file 保持 [None]（重放端按 accept 解析）；navigate/scroll 等无 target 动作保持 []。
		this_action = action if action is not None else (
			self.recording.actions[-1] if len(self.recording.actions) > appended_at else None
		)
		if this_action is not None and this_action.action_name in ("click", "input_text", "select_dropdown"):
			ie = this_action.interacted_element
			if ie is None or (isinstance(ie, list) and not ie):
				self._store_semantic_clue(this_action, event, True, 0)
				logger.warning(
					"兜底存语义线索 action=%s（逃逸未回填 interacted；pending=%s）",
					this_action.action_name, pending_exc,
				)

		if this_action is not None:
			this_action.page_url = state.url if state else ""
			this_action.page_title = state.title if state else ""
		# 异常仍要传播——不吞 asyncio.CancelledError / 系统退出。但 action 已带语义线索（target 动作），
		# 即使 _handle_event 因异常返 500，该步落盘也可被回放重新定位（issue #129）。
		if pending_exc is not None:
			raise pending_exc
		# translate_event 已把 action 追加进 recording.actions；此处填的字段原地生效
		logger.info("录进步 %d: %s", len(self.recording.actions) - 1, this_action.action_name if this_action else "?")
		return this_action

	def _store_semantic_clue(
		self,
		action: ActionRecord,
		event: dict[str, Any],
		retried: bool,
		selector_map_size: int,
	) -> None:
		"""target 动作（click/input_text/select_dropdown）定位未命中或异常时，存语义线索 + 诊断。

		语义线索 = 扩展在事件瞬间握住的 e.target 特征（xpath/tag/attr/rect = locate_by_ref 的输入）；
		重放端据其在稳定的新页面重新定位（见 docs/user_recording/semantic-clue-replay.md）。这是 target
		动作定位失败的统一兜底——无论 locate 三级未命中，还是 get_state/locate 块抛异常（含
		CancelledError 等 BaseException，内层 except Exception 抓不到，issue #129），都走这里，保证
		action 不以默认 interacted=null 落盘被回放跳过。
		"""
		base = {
			"xpath": event.get("xpath"),
			"tag": event.get("tag"),
			"name": event.get("name"),
			"id": event.get("id"),
			"ariaLabel": event.get("ariaLabel"),
			"role": event.get("role"),
			"rect": event.get("rect"),
			"text": event.get("text"),
		}
		action.interacted_element = [{"_semantic_clue": True, **base}]
		action.locate_miss = {
			**base,
			"path": action.params.get("path") if action.action_name == "upload_file" else None,
			"selector_map_size": selector_map_size,
			"retried": retried,
		}

	async def attach_signal(self, payload: dict[str, Any]) -> bool:
		"""把扩展 SideEffectObserver 的信号（modal_opened/dropdown_opened）附到最近动作。

		仅当录制中、有动作、且信号时间距最近动作 ≤ ``_SIGNAL_WINDOW_S`` 时附加（防陈旧信号
		误附）。``MODAL_OPENED`` 同时更新 ``state.pending_modal``。返回是否成功附加。

		**async + 加锁**：与 handle_event/stop 串行——避免并发读取/改 ``recording.actions[-1]``
		时 last 漂移（aiohttp 并发处理 /signal 与 /event）。调用方（server）须 ``await``。
		"""
		async with self._lock:
			if not self._recording or not self.recording.actions:
				return False
			sig = signal_from_payload(payload)
			if sig is None:
				return False
			last = self.recording.actions[-1]
			if sig.timestamp > 0 and last.timestamp > 0 and abs(sig.timestamp - last.timestamp) > _SIGNAL_WINDOW_S:
				return False
			last.signals.append(sig)
			if sig.kind == SignalKind.MODAL_OPENED:
				self.recording.state.pending_modal = sig.detail.get("selector")
			logger.info("附信号 %s 到步 %d", sig.kind.value, len(self.recording.actions) - 1)
			return True

	async def _ensure_target(self, event_url: str | None) -> None:
		"""确保 BrowserSession 指向用户操作的 http page target（非 popup/扩展页）。

		扩展事件带 ``url``（用户操作页的真实 url）；按它在 CDP targets 里找匹配的 http page，
		必要时 ``switch_tab``。点开始录制时若 BrowserSession attach 到了扩展 popup，这里修正。
		"""
		client = getattr(self.browser, "client", None)
		if client is None:
			return  # mock browser（测试）无 client
		try:
			resp = await client.send.Target.getTargets({})
		except Exception as e:
			logger.warning("Target.getTargets 失败: %s", e)
			return
		tid = select_http_target(resp.get("targetInfos", []), event_url)
		cur = getattr(self.browser, "current_target_id", None)
		if tid and tid != cur:
			try:
				await self.browser.switch_tab(tid)
				await self._disable_file_chooser_intercept()  # switch_tab 会 re-enable，再关掉
			except Exception as e:
				logger.warning("switch_tab(%s) 失败: %s", tid, e)

	async def _disable_file_chooser_intercept(self) -> None:
		"""禁用 CDP file-chooser intercept，让原生文件选择框正常弹出。

		BrowserSession 默认启用 intercept（agent 上传用，会抑制原生 picker）。录制用户操作时
		必须关掉——否则用户点上传按钮弹不出选文件框。``switch_tab`` 会 re-enable，故每次切
		target 后也要再关。
		"""
		client = getattr(self.browser, "client", None)
		sid = getattr(self.browser, "current_session_id", None)
		if client is None or sid is None:
			return
		try:
			await client.send.Page.setInterceptFileChooserDialog({"enabled": False}, session_id=sid)
			setattr(self.browser, "_file_chooser_intercept_enabled", False)
		except Exception as e:
			logger.debug("禁用 file-chooser intercept 失败（旧版 Chrome 可能不支持）: %s", e)

	async def _resolve_tab_id(self, url: str | None) -> str:
		"""把目标 tab 的 url 解析成 CDP targetId 后4位（switch_tab/close_tab 用）。

		扩展 ``chrome.tabs`` 事件给的是 Chrome tabId，重放侧要的是 CDP targetId 后4位；
		这里用 url 在 CDP targets 里匹配 page，取 ``target_id[-4:]``。解析失败返回空串
		（close_tab 空=关当前 tab，合法）。mock browser 无 ``get_tabs`` 时返回空。
		"""
		if not url:
			return ""
		get_tabs = getattr(self.browser, "get_tabs", None)
		if get_tabs is None:
			return ""
		try:
			tabs = await get_tabs()
		except Exception as e:
			logger.warning("get_tabs 失败: %s", e)
			return ""
		want = url.rstrip("/").split("#", 1)[0]
		for t in tabs:
			turl = (getattr(t, "url", "") or "").rstrip("/").split("#", 1)[0]
			if turl and turl == want:
				tid = getattr(t, "target_id", "") or ""
				return tid[-4:]
		return ""

	def _resolve_upload_path(self, filename: str) -> str:
		"""upload_file 的文件名拼约定目录（扩展只能拿文件名，``basename`` 防误发含路径）。"""
		name = os.path.basename(filename or "")
		return os.path.join(self.upload_dir, name) if name else ""

	def _prepend_initial_navigation(self) -> None:
		"""落盘前在 history[0] 插一条起始页 navigate（仿 Browser-BC 的 load 事件）。

		用 flatten 后 ``history[0].state_summary.url``。**不在 start 时插**：start 时 attach
		的可能是 Chrome new-tab-page 等非用户页。首步已是 navigate（扩展 navigation-recorder
		发的）→ 不补，避免回放重复导航。插入后重排 step_number。无 history 或 url 为空则跳过。
		"""
		if not self.history.history:
			return
		# 首步已是 navigate（扩展 navigation-recorder 发的）→ 不补，避免回放重复导航
		first_acts = (self.history.history[0].model_output or {}).get("actions") or []
		if first_acts and first_acts[0].get("name") == "navigate":
			return
		first_url = (self.history.history[0].state_summary or {}).get("url")
		if not first_url:
			return
		now = time.time()
		self.history.history.insert(0, AgentHistory(
			step_number=0,
			model_output={"actions": [{"name": "navigate", "params": {"url": first_url, "new_tab": False}}]},
			result=[],
			state_summary={"url": first_url, "title": "", "duration": 0.0},
			interacted_element=None,
			metadata=StepMetadata(step_start_time=now, step_end_time=now, step_number=0),
		))
		for i, s in enumerate(self.history.history):
			s.step_number = i
			if s.metadata is not None:
				s.metadata.step_number = i

	async def stop(
		self,
		file_path: str = "recorded.json",
		mark_done: bool = False,
		done_text: str = "",
		success: bool = True,
	) -> Path:
		"""停止录制：跑翻译规则 → flatten 成 ``AgentHistoryList`` → 可选补 ``done`` → 落盘。

		``file_path`` 必须相对（``resolve_rerun_path`` 拒绝绝对路径 / ``..`` 越界）。
		"""
		self._recording = False  # 先置 False 拒新事件（新事件取不到锁后见此即返 None）
		# 等 in-flight handle_event 跑完再 flatten/落盘（修末步录不全竞态，issue #136 关联）。
		# 有上限（_STOP_LOCK_TIMEOUT_S）：get_state 卡死时不让 stop 永久挂起——超时强制落盘。
		try:
			await asyncio.wait_for(self._lock.acquire(), timeout=_STOP_LOCK_TIMEOUT_S)
			_locked = True
		except asyncio.TimeoutError:
			_locked = False
			logger.warning(
				"stop 等待 in-flight 事件超时（%ss）——疑 get_state 卡住，强制落盘（末步可能不完整）",
				_STOP_LOCK_TIMEOUT_S,
			)
		try:
			# Stage 3：signal 感知去噪（导航关联 / upload 去噪 / click 折叠 / input·scroll 合并）
			self.recording.actions = apply_rules(self.recording.actions)
			# 落盘 reshape：Recording → AgentHistoryList（纯映射，不再二次定位）
			self.history = flatten(self.recording)
			self._prepend_initial_navigation()  # 起始页 navigate 作 history[0]
			if mark_done:
				now = time.time()
				next_step = len(self.history.history)  # flatten + 初始 navigate 后的下一个序号（避免跳号）
				self.history.history.append(AgentHistory(
					step_number=next_step,
					model_output={"actions": [{"name": "done", "params": {"text": done_text, "success": success}}]},
					result=[],
					state_summary=None,
					interacted_element=[None],
					metadata=StepMetadata(step_start_time=now, step_end_time=now, step_number=next_step),
				))
			path = resolve_rerun_path(self.rerun_history_dir, file_path)
			self.history.save_to_file(path, action_registry_version=self.registry_version)
		finally:
			if _locked:
				self._lock.release()
		await self.browser.stop()
		logger.info("录制结束，落盘 %s（%d 步）", path, len(self.history.history))
		return path
