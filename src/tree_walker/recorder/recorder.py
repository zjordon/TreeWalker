"""录制核心：扩展事件 → CDP 算指纹 → 拼 AgentHistory → 落盘。

``Recorder`` 连接用户浏览器（remote-debugging-port CDP），每收到一条扩展事件就：
``get_state`` 拉当前页 → ``locator`` 按 xpath 在 ``selector_map`` 定位节点 →
``DOMInteractedElement.load_from_enhanced_dom_tree`` 算指纹投影 → ``event_mapper`` 映射成
action → 拼一条 ``AgentHistory`` 追加。停止时经 ``resolve_rerun_path`` 落盘到 ``rerun-history/``。

指纹（``element_hash``/``stable_hash``）由 TreeWalker 自身代码算（``load_from_enhanced_dom_tree``
调 ``hash(node)`` / ``node.compute_stable_hash()``），录制侧与重放侧同源 → EXACT/STABLE
匹配天然有效（这就是「全对齐」路线的核心）。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from tree_walker.agent.rerun import resolve_rerun_path
from tree_walker.agent.views import AgentHistory, AgentHistoryList, StepMetadata
from tree_walker.browser.session import BrowserSession
from tree_walker.browser.views import DOMInteractedElement
from tree_walker.recorder.event_mapper import map_event, needs_target
from tree_walker.recorder.locator import locate_by_ref, locate_by_xpath

logger = logging.getLogger(__name__)


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
	"""录制用户操作 → ``AgentHistory``。

	依赖注入 ``browser``（``BrowserSession`` 或任何带 ``start``/``stop``/``get_state`` 的对象，
	便于测试 mock）。
	"""

	def __init__(
		self,
		browser: BrowserSession,
		rerun_history_dir: str,
		registry_version: str | None = None,
	) -> None:
		self.browser = browser
		self.rerun_history_dir = rerun_history_dir
		self.registry_version = registry_version
		self.history: AgentHistoryList = AgentHistoryList()
		self._step = 0
		self._recording = False

	async def start(self) -> None:
		"""开始录制：连浏览器，初始化空历史。"""
		await self.browser.start()
		await self._disable_file_chooser_intercept()  # 让原生文件选择框正常弹出
		self._recording = True
		self.history = AgentHistoryList()
		self._step = 0
		logger.info("录制开始")

	async def handle_event(self, event: dict[str, Any]) -> AgentHistory | None:
		"""处理一条扩展事件，拼一条 ``AgentHistory`` 追加；不可映射事件返回 None。"""
		if not self._recording:
			return None
		mapped = map_event(event)
		if mapped is None:
			logger.debug("忽略不可映射事件: %s", event.get("type"))
			return None
		action_name, params = mapped

		# 确保 BrowserSession 指向用户操作的 http page（而非 popup/扩展页）
		await self._ensure_target(event.get("url"))

		state = await self.browser.get_state(include_screenshot=False)
		selector_map = state.dom_state.selector_map if state and state.dom_state else {}

		interacted: list[dict[str, Any] | None]
		if needs_target(action_name):
			located = locate_by_ref(
				{
					"xpath": event.get("xpath"),
					"rect": event.get("rect"),
					"tag": event.get("tag"),
					"id": event.get("id"),
					"name": event.get("name"),
					"ariaLabel": event.get("ariaLabel"),
					"role": event.get("role"),
				},
				selector_map,
			)
			if located is None:
				logger.warning(
					"定位失败 action=%s xpath=%s（该步 interacted_element 置空）",
					action_name, event.get("xpath"),
				)
				interacted = [None]
			else:
				index, node = located
				params = {**params, "index": index}
				interacted = [DOMInteractedElement.load_from_enhanced_dom_tree(node).to_dict()]
		else:
			interacted = []

		now = time.time()
		step = AgentHistory(
			step_number=self._step,
			model_output={"actions": [{"name": action_name, "params": params}]},
			result=[],
			state_summary={
				"url": state.url if state else "",
				"title": state.title if state else "",
				"duration": 0.0,
			},
			interacted_element=interacted if interacted else None,
			metadata=StepMetadata(step_start_time=now, step_end_time=now, step_number=self._step),
		)
		self.history.history.append(step)
		self._step += 1
		logger.info("录进步 %d: %s", step.step_number, action_name)
		return step

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

	async def stop(
		self,
		file_path: str = "recorded.json",
		mark_done: bool = False,
		done_text: str = "",
		success: bool = True,
	) -> Path:
		"""停止录制，可选补一条 ``done``，落盘到 ``rerun_history_dir/file_path``。

		``file_path`` 必须相对（``resolve_rerun_path`` 拒绝绝对路径 / ``..`` 越界）。
		"""
		self._recording = False
		if mark_done:
			now = time.time()
			self.history.history.append(AgentHistory(
				step_number=self._step,
				model_output={"actions": [{"name": "done", "params": {"text": done_text, "success": success}}]},
				result=[],
				state_summary=None,
				interacted_element=[None],
				metadata=StepMetadata(step_start_time=now, step_end_time=now, step_number=self._step),
			))
		path = resolve_rerun_path(self.rerun_history_dir, file_path)
		self.history.save_to_file(path, action_registry_version=self.registry_version)
		await self.browser.stop()
		logger.info("录制结束，落盘 %s（%d 步）", path, len(self.history.history))
		return path
