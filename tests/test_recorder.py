"""recorder 单元测试：事件 → 翻译管线 → 定位 → Recording → flatten → 落盘。

用 FakeBrowser（带 start/stop/get_state）+ monkeypatch DOMInteractedElement.load_from_enhanced_dom_tree，
聚焦 Recorder 的「事件→翻译→定位→落盘」逻辑，不依赖真 CDP/DOMInteractedElement 内部。
"""

from types import SimpleNamespace

import asyncio

import pytest

from tree_walker.agent.views import AgentHistoryList
from tree_walker.recorder import recorder as rec_mod
from tree_walker.recorder.models import SignalKind
from tree_walker.recorder.recorder import Recorder, select_http_target


class FakeBrowser:
	"""mock BrowserSession：只实现 start/stop/get_state。"""

	def __init__(self, selector_map=None, url="https://x.com", title="X", tabs=None):
		self._map = selector_map or {}
		self._url = url
		self._title = title
		self._tabs = tabs or []
		self.started = False
		self.stopped = False

	async def start(self):
		self.started = True

	async def stop(self):
		self.stopped = True

	async def get_state(self, include_screenshot=True):
		dom = SimpleNamespace(selector_map=self._map)
		return SimpleNamespace(url=self._url, title=self._title, dom_state=dom)

	async def get_tabs(self):
		return self._tabs


class _FakeProj:
	"""假 DOMInteractedElement 投影。"""

	def __init__(self, payload):
		self._payload = payload

	def to_dict(self):
		return self._payload


@pytest.fixture
def patch_projection(monkeypatch):
	"""patch load_from_enhanced_dom_tree 返回固定投影；记录被调用的 node。"""
	called = []

	def fake_load(node):
		called.append(node)
		return _FakeProj({"node_name": "BUTTON", "element_hash": 123, "stable_hash": 456, "x_path": "html/body/btn"})

	monkeypatch.setattr(rec_mod.DOMInteractedElement, "load_from_enhanced_dom_tree", staticmethod(fake_load))
	return called


@pytest.mark.asyncio
async def test_click_event_locates_and_fills_index(tmp_path, patch_projection):
	browser = FakeBrowser(selector_map={5: SimpleNamespace(xpath="html/body/btn")})
	rec = Recorder(browser, rerun_history_dir=str(tmp_path), registry_version="v1")
	await rec.start()
	assert browser.started

	action = await rec.handle_event({"type": "click", "xpath": "html/body/btn", "rect": None})
	assert action is not None
	assert action.action_name == "click"
	assert action.params["index"] == 5
	assert action.interacted_element[0]["element_hash"] == 123
	assert action.page_url == "https://x.com"
	assert len(patch_projection) == 1  # 投影被调用一次


@pytest.mark.asyncio
async def test_navigate_event_has_no_interacted_element(tmp_path):
	browser = FakeBrowser()
	rec = Recorder(browser, rerun_history_dir=str(tmp_path))
	await rec.start()
	action = await rec.handle_event({"type": "navigate", "params": {"url": "https://y.com"}})
	assert action is not None
	assert action.action_name == "navigate"
	assert "index" not in action.params
	assert not action.interacted_element  # 无 target → []（falsy）


@pytest.mark.asyncio
async def test_locate_failure_keeps_action_without_index(tmp_path, patch_projection, monkeypatch):
	monkeypatch.setattr(rec_mod, "_LOCATE_RETRY_DELAYS", (0.0, 0.0))  # 跳过重试 sleep 加速
	browser = FakeBrowser(selector_map={5: SimpleNamespace(xpath="html/body/other")})
	rec = Recorder(browser, rerun_history_dir=str(tmp_path))
	await rec.start()
	action = await rec.handle_event({"type": "click", "xpath": "html/body/missing"})
	assert action is not None
	# locate 失败 → 存语义线索（非 [None]），重放端重新定位（详见 semantic-clue-replay.md）
	assert action.interacted_element[0] is not None
	assert action.interacted_element[0]["_semantic_clue"] is True
	assert action.interacted_element[0]["xpath"] == "html/body/missing"
	assert "index" not in action.params
	assert len(patch_projection) == 0  # 定位失败，投影未调用


@pytest.mark.asyncio
async def test_unmappable_event_returns_none(tmp_path):
	browser = FakeBrowser()
	rec = Recorder(browser, rerun_history_dir=str(tmp_path))
	await rec.start()
	action = await rec.handle_event({"type": "hover", "xpath": "html/x"})
	assert action is None
	assert len(rec.recording.actions) == 0


@pytest.mark.asyncio
async def test_stop_writes_file_and_can_reload(tmp_path, patch_projection):
	browser = FakeBrowser(selector_map={5: SimpleNamespace(xpath="html/body/btn")})
	rec = Recorder(browser, rerun_history_dir=str(tmp_path), registry_version="v1")
	await rec.start()
	await rec.handle_event({"type": "click", "xpath": "html/body/btn"})

	path = await rec.stop(file_path="out.json", mark_done=True, done_text="完成")
	assert browser.stopped
	assert path == tmp_path / "out.json"

	loaded = AgentHistoryList.load_from_file(path)
	assert len(loaded.history) == 3  # 初始 navigate + click + done
	assert loaded.history[0].model_output["actions"][0]["name"] == "navigate"
	assert loaded.history[1].model_output["actions"][0]["name"] == "click"
	assert loaded.history[2].model_output["actions"][0]["name"] == "done"
	assert loaded.action_registry_version == "v1"


@pytest.mark.asyncio
async def test_stop_rejects_absolute_path(tmp_path):
	import pytest as _pytest
	browser = FakeBrowser()
	rec = Recorder(browser, rerun_history_dir=str(tmp_path))
	await rec.start()
	with _pytest.raises(ValueError):
		await rec.stop(file_path=str(tmp_path / "abs.json"))


@pytest.mark.asyncio
async def test_switch_tab_resolves_tab_id_from_url(tmp_path):
	# 扩展发目标 tab 的 url；后端解析成 CDP targetId 后4位
	tabs = [SimpleNamespace(url="https://a.com/page", target_id="ABCDEF1234")]
	browser = FakeBrowser(tabs=tabs)
	rec = Recorder(browser, rerun_history_dir=str(tmp_path))
	await rec.start()
	action = await rec.handle_event({"type": "switch_tab", "params": {"url": "https://a.com/page"}})
	assert action is not None
	assert action.params["tab_id"] == "1234"


@pytest.mark.asyncio
async def test_close_tab_unknown_url_yields_empty_tab_id(tmp_path):
	# url 在 tabs 里找不到 → tab_id 空（close_tab 空=关当前，合法）
	browser = FakeBrowser(tabs=[])
	rec = Recorder(browser, rerun_history_dir=str(tmp_path))
	await rec.start()
	action = await rec.handle_event({"type": "close_tab", "params": {"url": "https://none.com"}})
	assert action is not None
	assert action.params["tab_id"] == ""


@pytest.mark.asyncio
async def test_upload_file_records_signature_no_fingerprint(tmp_path, patch_projection):
	"""B 方案：upload_file 不 get_state 定位（导航竞态），存 accept+xpath 签名、无指纹、无 index。

	扩展 change 瞬间捕获 accept（真实 file input）+ xpath，后端原样落盘；重放端按 accept+xpath
	解析。selector_map 里有什么 input 都不影响录制——故故意塞个错位 image input 证明不被带偏。
	视频后不再追加 wait（阶段3 缺口6：upload wait 移至重放端可配置）。
	"""
	browser = FakeBrowser(selector_map={  # 发布页错位 image input（导航竞态残留）
		6: SimpleNamespace(node_name="INPUT",
		                   attributes={"type": "file", "accept": "image/png,image/jpeg"},
		                   xpath="html/body/div[3]/input", snapshot_node=None),
	})
	rec = Recorder(browser, rerun_history_dir=str(tmp_path))
	await rec.start()
	action = await rec.handle_event({
		"type": "upload_file", "xpath": "html/body/div[2]/input",
		"params": {"path": "video.mp4", "accept": "video/*"},
	})
	assert action is not None
	p = action.params
	assert "index" not in p                      # 不再录制时定位
	# issue #139：upload 存语义线索（非 [None]），重放端 _match_file_upload_by_clue 按 area_text 精筛。
	# 本例事件无 upload_ctx/rect → 线索仅 accept+xpath（area_text 等为空）。
	ie = action.interacted_element[0]
	assert ie is not None
	assert ie["_semantic_clue"] is True
	assert ie["kind"] == "file_upload"
	assert ie["accept"] == "video/*"
	assert ie["xpath"] == "html/body/div[2]/input"
	assert action.locate_miss is None            # 非定位失败，是设计如此
	# 签名落盘：accept + xpath（change 瞬间扩展捕获，未被 selector_map 的 image input 带偏）
	assert p["accept"] == "video/*"
	assert p["xpath"] == "html/body/div[2]/input"
	# 文件名拼约定目录 <rerun_history_dir>/uploads（basename 防误发路径）
	assert p["path"].replace("\\", "/").endswith("uploads/video.mp4")
	# 阶段3 缺口6：录制端不再注入 upload wait（改重放端可配置）。末尾 action 即 upload_file 本身。
	assert len(rec.recording.actions) == 1
	assert rec.recording.actions[-1].action_name == "upload_file"


@pytest.mark.asyncio
async def test_upload_file_no_wait_injected(tmp_path, patch_projection):
	"""阶段3 缺口6：image upload 后不再注入 wait 动作（改重放端 rerun_upload_wait_image）。

	无论 video/image，录制端只落一条 upload_file；等待由重放端按类型可配置决定。
	"""
	browser = FakeBrowser(selector_map={})
	rec = Recorder(browser, rerun_history_dir=str(tmp_path))
	await rec.start()
	action = await rec.handle_event({
		"type": "upload_file", "xpath": "html/body/cov/input",
		"params": {"path": "cover.png", "accept": "image/png,image/jpeg"},
	})
	assert action is not None
	assert action.action_name == "upload_file"
	assert action.params["accept"] == "image/png,image/jpeg"
	assert action.params["xpath"] == "html/body/cov/input"
	# 仅一条 action（不再追加 wait）
	assert len(rec.recording.actions) == 1
	assert rec.recording.actions[-1].action_name == "upload_file"


@pytest.mark.asyncio
async def test_upload_file_missing_accept_defaults_empty(tmp_path, patch_projection):
	"""扩展未带 accept（旧扩展）→ accept 签名空串，重放端退回按 path 扩展名解析。不报错。"""
	browser = FakeBrowser(selector_map={})
	rec = Recorder(browser, rerun_history_dir=str(tmp_path))
	await rec.start()
	action = await rec.handle_event({
		"type": "upload_file", "xpath": "html/body/x/input", "params": {"path": "v.mp4"},
	})
	assert action is not None
	assert action.params["accept"] == ""
	assert action.params["xpath"] == "html/body/x/input"


@pytest.mark.asyncio
async def test_upload_file_stores_semantic_clue_with_ctx(tmp_path, patch_projection):
	"""issue #139：扩展 change 瞬间捕获 upload_ctx（封装组件 drag-area 文案 + 活动 step tab）+ rect
	→ 录制端存进 _semantic_clue（kind=file_upload），重放端 _match_file_upload_by_clue 据 area_text
	在多个同 accept file input 里精筛。"""
	browser = FakeBrowser(selector_map={})
	rec = Recorder(browser, rerun_history_dir=str(tmp_path))
	await rec.start()
	action = await rec.handle_event({
		"type": "upload_file", "xpath": "html/body/div[12]/div[2]/input",
		"rect": {"x": 10, "y": 20, "width": 100, "height": 60},
		"params": {"path": "heng.png", "accept": "image/png,image/jpeg"},
		"upload_ctx": {
			"area_text": "点击上传文件或拖拽文件到这里",
			"nearby_text": "设置横封面",
			"upload_ancestor_class": "semi-upload upload-BvM5FF",
		},
	})
	assert action is not None
	ie = action.interacted_element[0]
	assert ie is not None
	assert ie["_semantic_clue"] is True
	assert ie["kind"] == "file_upload"
	assert ie["area_text"] == "点击上传文件或拖拽文件到这里"
	assert ie["nearby_text"] == "设置横封面"
	assert ie["upload_ancestor_class"] == "semi-upload upload-BvM5FF"
	assert ie["rect"] == {"x": 10, "y": 20, "width": 100, "height": 60}
	assert ie["accept"] == "image/png,image/jpeg"
	# accept+xpath 仍落 params（老重放路径 _resolve_file_input_by_accept 的 xpath_hint 兜底）
	assert action.params["accept"] == "image/png,image/jpeg"
	assert action.params["xpath"] == "html/body/div[12]/div[2]/input"


@pytest.mark.asyncio
async def test_stop_applies_aggregation_to_history(tmp_path, patch_projection):
	"""相邻同 xpath input_text 经 Stage1 聚合取最终值，flatten 后落盘。"""
	browser = FakeBrowser(selector_map={5: SimpleNamespace(xpath="html/body/inp")})
	rec = Recorder(browser, rerun_history_dir=str(tmp_path), registry_version="v1")
	await rec.start()
	# 同 xpath 连续 input（ts ms，0.001s 间隔 < 1.5s gap）→ Stage1 聚合
	await rec.handle_event({"type": "input_text", "xpath": "html/body/inp", "params": {"text": "a"}, "ts": 1})
	a2 = await rec.handle_event({"type": "input_text", "xpath": "html/body/inp", "params": {"text": "ab"}, "ts": 2})
	assert a2 is None  # 聚合进前一步，不新增
	assert len(rec.recording.actions) == 1

	path = await rec.stop(file_path="out.json")
	loaded = AgentHistoryList.load_from_file(path)
	assert len(loaded.history) == 2  # 初始 navigate + 聚合后的 input_text
	assert loaded.history[0].model_output["actions"][0]["name"] == "navigate"
	assert loaded.history[1].model_output["actions"][0]["params"]["text"] == "ab"  # 取最终值


@pytest.mark.asyncio
async def test_attach_signal_attaches_to_last_action(tmp_path, patch_projection):
	"""扩展 SideEffectObserver 的 modal 信号经 attach_signal 附到最近动作。"""
	browser = FakeBrowser(selector_map={5: SimpleNamespace(xpath="html/body/btn")})
	rec = Recorder(browser, rerun_history_dir=str(tmp_path))
	await rec.start()
	await rec.handle_event({"type": "click", "xpath": "html/body/btn", "ts": 1000})  # ts→1.0s

	# 信号 ts=1500ms → 1.5s，距动作 0.5s < 2s 窗口 → 附加
	assert await rec.attach_signal({"type": "modal_opened", "selector": ".editor", "ts": 1500}) is True
	last = rec.recording.actions[-1]
	assert len(last.signals) == 1
	assert last.signals[0].kind is SignalKind.MODAL_OPENED
	assert rec.recording.state.pending_modal == ".editor"

	# 超窗（ts=5000ms → 5.0s，距动作 4.0s > 2s）→ 拒绝
	assert await rec.attach_signal({"type": "modal_opened", "selector": ".late", "ts": 5000}) is False


@pytest.mark.asyncio
async def test_attach_signal_unknown_type_rejected(tmp_path, patch_projection):
	browser = FakeBrowser(selector_map={5: SimpleNamespace(xpath="html/body/btn")})
	rec = Recorder(browser, rerun_history_dir=str(tmp_path))
	await rec.start()
	await rec.handle_event({"type": "click", "xpath": "html/body/btn", "ts": 1000})
	assert await rec.attach_signal({"type": "mystery", "ts": 1100}) is False


@pytest.mark.asyncio
async def test_stop_waits_for_inflight_event(tmp_path, patch_projection, monkeypatch):
	"""stop() 加锁等 in-flight handle_event 跑完再 flatten——修末步偶发录不全竞态（issue #136 关联）。

	无锁时：事件卡在 get_state，/stop 并发跑 flatten → 落盘 click {} + null interacted（半成品，
	正是 douyin_redesign12 step20 的现象）。加锁后：stop 等事件持锁跑完 → 该步完整。
	"""
	monkeypatch.setattr(rec_mod, "_LOCATE_RETRY_DELAYS", (0.0, 0.0))
	entered = asyncio.Event()
	release = asyncio.Event()

	class _HangingBrowser(FakeBrowser):
		async def get_state(self, include_screenshot=True):
			entered.set()
			await release.wait()  # 模拟 get_state 慢：事件处理中途 /stop 到达
			return await super().get_state(include_screenshot)

	browser = _HangingBrowser(selector_map={5: SimpleNamespace(xpath="html/body/btn")})
	rec = Recorder(browser, rerun_history_dir=str(tmp_path))
	await rec.start()

	# 事件先跑（卡在 get_state 持锁），再调 stop——stop 必须等锁，不能抢先 flatten
	event_task = asyncio.create_task(
		rec.handle_event({"type": "click", "xpath": "html/body/btn", "rect": None, "text": "X"})
	)
	await entered.wait()         # 事件已进入 get_state（持锁 hang 住）
	stop_task = asyncio.create_task(rec.stop(file_path="out.json"))
	await asyncio.sleep(0.05)    # 让 stop 排到等锁队列（验证它没抢先落盘）
	release.set()                # 放行事件
	await stop_task
	await event_task

	# history[0]=起始 navigate（prepend），history[1]=click——须完整：interacted 非 null（未被截断）
	assert len(rec.history.history) >= 2
	click_step = rec.history.history[1]
	assert click_step.model_output["actions"][0]["name"] == "click"
	assert click_step.interacted_element[0] is not None
	assert click_step.interacted_element[0].get("text") == "X"


@pytest.mark.asyncio
async def test_locate_retries_when_element_appears_later(tmp_path, patch_projection, monkeypatch):
	"""定位失败时重试 get_state：元素第一次不在 selector_map（SPA 未渲染），重试后出现 → 捕获指纹。

	对应「暂存离开」按钮场景：modal 关闭后按钮才渲染，首次 get_state 抢在前面 → 重试救回。
	"""
	monkeypatch.setattr(rec_mod, "_LOCATE_RETRY_DELAYS", (0.0, 0.0))  # 跳过 sleep 加速
	state_seq = [
		{},  # 首次：元素未渲染
		{5: SimpleNamespace(xpath="html/body/btn")},  # 重试后：元素出现
	]
	calls = {"n": 0}

	class LateBrowser(FakeBrowser):
		async def get_state(self, include_screenshot=True):
			sm = state_seq[min(calls["n"], len(state_seq) - 1)]
			calls["n"] += 1
			dom = SimpleNamespace(selector_map=sm)
			return SimpleNamespace(url="https://x.com", title="X", dom_state=dom)

	rec = Recorder(LateBrowser(), rerun_history_dir=str(tmp_path))
	await rec.start()
	action = await rec.handle_event({"type": "click", "xpath": "html/body/btn"})
	assert action is not None
	assert action.params["index"] == 5      # 重试后定位成功
	assert action.locate_miss is None
	assert action.interacted_element[0]["element_hash"] == 123
	assert calls["n"] == 2                   # get_state 调了两次（初试 + 重试）


@pytest.mark.asyncio
async def test_locate_retry_exhausted_records_miss(tmp_path, patch_projection, monkeypatch):
	"""重试后仍找不到 → 记 locate_miss（retried=True），存语义线索。"""
	monkeypatch.setattr(rec_mod, "_LOCATE_RETRY_DELAYS", (0.0, 0.0))
	browser = FakeBrowser(selector_map={5: SimpleNamespace(xpath="html/body/other")})
	rec = Recorder(browser, rerun_history_dir=str(tmp_path))
	await rec.start()
	action = await rec.handle_event({"type": "click", "xpath": "html/body/missing"})
	assert action is not None
	assert action.interacted_element[0] is not None   # 存语义线索
	assert action.interacted_element[0]["_semantic_clue"] is True
	assert action.locate_miss is not None
	assert action.locate_miss["retried"] is True


@pytest.mark.asyncio
async def test_locate_get_state_failure_records_semantic_clue(tmp_path, patch_projection, monkeypatch):
	"""get_state 抛异常（submit 跳转致 CDP target 卸载）→ 容错为空 selector_map，locate 失败存语义线索。

	修复 submit click 录制时 get_state 异常致 handle_event 中断、interacted 以默认 None 落盘（重放
	skip、submit 没点）。容错后即使 get_state 异常也存语义线索，重放端可重新定位。"""
	monkeypatch.setattr(rec_mod, "_LOCATE_RETRY_DELAYS", (0.0, 0.0))

	class FailGetStateBrowser(FakeBrowser):
		async def get_state(self, include_screenshot=True):
			raise RuntimeError("target closed")

	rec = Recorder(FailGetStateBrowser(), rerun_history_dir=str(tmp_path))
	await rec.start()
	action = await rec.handle_event({"type": "click", "xpath": "html/body/form/button", "tag": "button"})
	assert action is not None
	# get_state 异常 → 容错（不中断）→ locate 失败存语义线索（非默认 None）
	assert action.interacted_element[0] is not None
	assert action.interacted_element[0]["_semantic_clue"] is True
	assert action.interacted_element[0]["xpath"] == "html/body/form/button"


class _BaseExcError(BaseException):
	"""非 Exception 的异常（模拟 asyncio.CancelledError 这类）——内层 except Exception 抓不到。"""


@pytest.mark.asyncio
async def test_locate_block_base_exception_stores_semantic_clue(tmp_path, monkeypatch):
	"""get_state 抛 BaseException（非 Exception，如 CancelledError）→ 内层 except Exception 抓不到，
	外层兜底仍保证 click 带语义线索（handle_event 重抛异常，但 action 已 append 且带线索）。

	复现 issue #129：触发跳转的 click（如「暂存离开」）录制时 get_state 在卸载的 target 上抛
	非 Exception，旧代码让 action 以默认 interacted=null 落盘 → 回放被当噪声步跳过。
	"""
	monkeypatch.setattr(rec_mod, "_LOCATE_RETRY_DELAYS", (0.0, 0.0))

	class TeardownBrowser(FakeBrowser):
		async def get_state(self, include_screenshot=True):
			raise _BaseExcError("target detached")

	rec = Recorder(TeardownBrowser(), rerun_history_dir=str(tmp_path))
	await rec.start()
	# handle_event 重抛（不吞取消/系统退出），但 action 已 append 且兜底存了语义线索
	with pytest.raises(_BaseExcError):
		await rec.handle_event({"type": "click", "xpath": "html/body/leave", "tag": "button"})
	action = rec.recording.actions[-1]
	assert action.action_name == "click"
	assert action.interacted_element is not None            # 非默认 None
	assert action.interacted_element[0]["_semantic_clue"] is True
	assert action.interacted_element[0]["xpath"] == "html/body/leave"
	assert action.locate_miss is not None
	assert action.locate_miss["retried"] is True


@pytest.mark.asyncio
async def test_nav_click_base_exception_survives_to_flatten(tmp_path, monkeypatch):
	"""端到端：触发跳转的 click 抛 BaseException → 兜底存线索 → stop 落盘后该步带语义线索
	（非默认 null，flatten 不归一为 None）→ 回放 _skip_reason 不会当噪声步跳过。issue #129 回归。"""
	monkeypatch.setattr(rec_mod, "_LOCATE_RETRY_DELAYS", (0.0, 0.0))

	class TeardownBrowser(FakeBrowser):
		async def get_state(self, include_screenshot=True):
			raise _BaseExcError("target detached")

	rec = Recorder(TeardownBrowser(), rerun_history_dir=str(tmp_path), registry_version="v1")
	await rec.start()
	with pytest.raises(_BaseExcError):
		await rec.handle_event({"type": "click", "xpath": "html/body/leave", "tag": "button"})

	path = await rec.stop(file_path="out.json", mark_done=True, done_text="完成")
	loaded = AgentHistoryList.load_from_file(path)
	click_step = next(
		s for s in loaded.history
		if (s.model_output or {}).get("actions", [{}])[0].get("name") == "click"
	)
	# 非默认 null：flatten 的 `interacted if interacted else None` 保留 truthy [{...}]
	assert click_step.interacted_element is not None
	assert click_step.interacted_element[0]["_semantic_clue"] is True


@pytest.mark.asyncio
async def test_ensure_target_exception_stores_semantic_clue(tmp_path, monkeypatch):
	"""_ensure_target 抛 BaseException（submit 跳转时 Target.getTargets/switch_tab 在 target 切换瞬间
	抛 CancelledError）→ 外层兜底保证 click 仍存语义线索。

	_ensure_target 在 get_state 之前调用、且早于 locate。submit 触发跳转时它可能先抛异常；旧代码
	（_ensure_target 在外层 try 之外）会让 action 以默认 null 落盘 → 回放跳过。复现 httpbin-5.json.json
	idx13（httpbin 表单 Submit）。注意：异常仍 re-raise（不吞取消），但 action 已带线索落盘。
	"""
	monkeypatch.setattr(rec_mod, "_LOCATE_RETRY_DELAYS", (0.0, 0.0))

	async def boom_ensure_target(self, url):
		raise _BaseExcError("target switching")

	monkeypatch.setattr(Recorder, "_ensure_target", boom_ensure_target)
	browser = FakeBrowser(selector_map={})  # _ensure_target 先抛，get_state 不会被调到
	rec = Recorder(browser, rerun_history_dir=str(tmp_path))
	await rec.start()
	with pytest.raises(_BaseExcError):
		await rec.handle_event({"type": "click", "xpath": "html/body/form/button", "tag": "button"})
	action = rec.recording.actions[-1]
	assert action.action_name == "click"
	assert action.interacted_element is not None            # 非默认 None
	assert action.interacted_element[0]["_semantic_clue"] is True
	assert action.interacted_element[0]["xpath"] == "html/body/form/button"
	assert action.locate_miss is not None


@pytest.mark.asyncio
async def test_translate_event_exception_keeps_click_semantic_clue(tmp_path, patch_projection, monkeypatch):
	"""translate_event 阶段抛 BaseException（页面卸载瞬间的残缺事件 / 状态机异常）→ 它已 append
	残缺 click（interacted=null）后抛，此时 action 变量为 None 但 recording.actions[-1] 是残缺
	target。统一兜底用 appended_at 定位它、强制存语义线索 → 重放不再当噪声步跳过。

	回归 douyin_redesign10.json step 20：发布 click 录制残缺（params={}/interacted=null/url=空），
	重放被 _skip_reason 跳过、最后一步没执行。旧代码 translate_event 在 try 外、抛则逃逸不兜底。"""
	monkeypatch.setattr(rec_mod, "_LOCATE_RETRY_DELAYS", (0.0, 0.0))
	real_translate = rec_mod.translate_event

	def boom(event, recording):
		action = real_translate(event, recording)  # 真实映射 click（已 append 到 recording.actions）
		if action is not None:
			raise _BaseExcError("translate_event phase crash")
		return action
	monkeypatch.setattr(rec_mod, "translate_event", boom)

	rec = Recorder(FakeBrowser(selector_map={}), rerun_history_dir=str(tmp_path))
	await rec.start()
	with pytest.raises(_BaseExcError):
		await rec.handle_event({"type": "click", "xpath": "html/body/publish", "tag": "button"})
	action = rec.recording.actions[-1]
	assert action.action_name == "click"
	assert action.interacted_element is not None            # 非默认 null（统一兜底用 appended_at 救回）
	assert action.interacted_element[0]["_semantic_clue"] is True
	assert action.interacted_element[0]["xpath"] == "html/body/publish"


# ── select_http_target：target 选择纯函数 ───────────────────────────────


def test_select_http_target_skips_extension_pages():
	infos = [
		{"type": "page", "url": "chrome-extension://abc/popup.html", "targetId": "ext"},
		{"type": "page", "url": "https://example.com/foo", "targetId": "http1"},
	]
	assert select_http_target(infos) == "http1"  # 无 url → 第一个 http page
	assert select_http_target(infos, "https://example.com/foo") == "http1"  # url 匹配


def test_select_http_target_returns_none_if_no_http():
	infos = [
		{"type": "page", "url": "chrome-extension://abc/popup.html", "targetId": "ext"},
		{"type": "page", "url": "devtools://devtools/bundled/devtools_app.html", "targetId": "dt"},
	]
	assert select_http_target(infos) is None


def test_select_http_target_normalizes_trailing_slash_and_hash():
	infos = [{"type": "page", "url": "https://example.com/page", "targetId": "t1"}]
	assert select_http_target(infos, "https://example.com/page/") == "t1"
	assert select_http_target(infos, "https://example.com/page#sec") == "t1"


def test_select_http_target_fallback_first_when_url_mismatch():
	infos = [
		{"type": "page", "url": "https://a.com/", "targetId": "a"},
		{"type": "page", "url": "https://b.com/", "targetId": "b"},
	]
	assert select_http_target(infos, "https://nomatch.com/") == "a"  # 不匹配 → 第一个 http page


def test_select_http_target_ignores_non_page_types():
	infos = [
		{"type": "browser", "url": "", "targetId": "brow"},
		{"type": "background_page", "url": "chrome-extension://x/background.html", "targetId": "bg"},
		{"type": "page", "url": "https://x.com", "targetId": "ok"},
	]
	assert select_http_target(infos) == "ok"


# ── recorder.start 不插初始 navigate（落盘前才插）──


@pytest.mark.asyncio
async def test_start_does_not_insert_initial_navigate(tmp_path):
	"""初始 navigate 在 stop 落盘前插（_prepend_initial_navigation），start 不插。"""
	rec = Recorder(FakeBrowser(), rerun_history_dir=str(tmp_path))
	await rec.start()
	assert len(rec.recording.actions) == 0


@pytest.mark.asyncio
async def test_stop_prepends_initial_navigation(tmp_path, patch_projection):
	"""stop 落盘前用 flatten 后 history[0].state_summary.url 插起始页 navigate 作 history[0]。"""
	browser = FakeBrowser(selector_map={5: SimpleNamespace(xpath="html/body/btn")}, url="https://start.page")
	rec = Recorder(browser, rerun_history_dir=str(tmp_path))
	await rec.start()
	await rec.handle_event({"type": "click", "xpath": "html/body/btn"})
	await rec.stop(file_path="out.json")
	# 初始 navigate（用 click 的 state_summary.url）+ click
	assert len(rec.history.history) == 2
	assert rec.history.history[0].model_output["actions"][0]["name"] == "navigate"
	assert rec.history.history[0].model_output["actions"][0]["params"]["url"] == "https://start.page"
	assert rec.history.history[1].model_output["actions"][0]["name"] == "click"


@pytest.mark.asyncio
async def test_stop_done_step_number_continuous(tmp_path, patch_projection):
	"""stop 追加的 done 用 flatten+初始navigate 后的 len 作 step_number，不跳号。"""
	browser = FakeBrowser(selector_map={5: SimpleNamespace(xpath="html/body/btn")}, url="https://x.com")
	rec = Recorder(browser, rerun_history_dir=str(tmp_path))
	await rec.start()
	await rec.handle_event({"type": "click", "xpath": "html/body/btn"})
	await rec.stop(file_path="out.json", mark_done=True, done_text="完成")
	nums = [s.step_number for s in rec.history.history]
	assert nums == list(range(len(nums)))  # 0..N-1 连续，无跳号


@pytest.mark.asyncio
async def test_click_event_stores_extension_text(tmp_path, patch_projection):
	"""扩展 click 事件带的 text（点击瞬间 ground truth）存进 interacted_element（issue #136）。"""
	browser = FakeBrowser(selector_map={5: SimpleNamespace(xpath="html/body/btn")})
	rec = Recorder(browser, rerun_history_dir=str(tmp_path))
	await rec.start()
	action = await rec.handle_event({
		"type": "click", "xpath": "html/body/btn", "rect": None, "text": "设置竖封面",
	})
	assert action.interacted_element[0]["text"] == "设置竖封面"


@pytest.mark.asyncio
async def test_click_event_omits_text_when_absent(tmp_path, patch_projection):
	"""无 text（旧协议/无文字元素）→ 不写入 text 字段，向后兼容。"""
	browser = FakeBrowser(selector_map={5: SimpleNamespace(xpath="html/body/btn")})
	rec = Recorder(browser, rerun_history_dir=str(tmp_path))
	await rec.start()
	action = await rec.handle_event({"type": "click", "xpath": "html/body/btn", "rect": None})
	assert "text" not in action.interacted_element[0]


@pytest.mark.asyncio
async def test_locate_failure_stores_text_in_semantic_clue(tmp_path, patch_projection, monkeypatch):
	"""定位失败存语义线索时也带上 text（重放端 TEXT 级重新定位，issue #136）。"""
	monkeypatch.setattr(rec_mod, "_LOCATE_RETRY_DELAYS", (0.0, 0.0))
	browser = FakeBrowser(selector_map={5: SimpleNamespace(xpath="html/body/other")})
	rec = Recorder(browser, rerun_history_dir=str(tmp_path))
	await rec.start()
	action = await rec.handle_event({
		"type": "click", "xpath": "html/body/missing", "text": "设置竖封面",
	})
	clue = action.interacted_element[0]
	assert clue["_semantic_clue"] is True
	assert clue["text"] == "设置竖封面"
