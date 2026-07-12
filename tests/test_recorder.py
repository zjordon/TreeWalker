"""recorder 单元测试：事件 → 定位 → 拼接 → 落盘。

用 FakeBrowser（带 start/stop/get_state）+ monkeypatch DOMInteractedElement.load_from_enhanced_dom_tree，
聚焦 Recorder 的「事件→定位→拼接→落盘」逻辑，不依赖真 CDP/DOMInteractedElement 内部。
"""

from types import SimpleNamespace

import pytest

from tree_walker.agent.views import AgentHistoryList
from tree_walker.recorder import recorder as rec_mod
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

	step = await rec.handle_event({"type": "click", "xpath": "html/body/btn", "rect": None})
	assert step is not None
	action = step.model_output["actions"][0]
	assert action["name"] == "click"
	assert action["params"]["index"] == 5
	assert step.interacted_element[0]["element_hash"] == 123
	assert step.state_summary["url"] == "https://x.com"
	assert len(patch_projection) == 1  # 投影被调用一次


@pytest.mark.asyncio
async def test_navigate_event_has_no_interacted_element(tmp_path):
	browser = FakeBrowser()
	rec = Recorder(browser, rerun_history_dir=str(tmp_path))
	await rec.start()
	step = await rec.handle_event({"type": "navigate", "params": {"url": "https://y.com"}})
	assert step is not None
	assert step.model_output["actions"][0]["name"] == "navigate"
	assert "index" not in step.model_output["actions"][0]["params"]
	assert step.interacted_element is None  # 无 target


@pytest.mark.asyncio
async def test_locate_failure_keeps_action_without_index(tmp_path, patch_projection):
	browser = FakeBrowser(selector_map={5: SimpleNamespace(xpath="html/body/other")})
	rec = Recorder(browser, rerun_history_dir=str(tmp_path))
	await rec.start()
	step = await rec.handle_event({"type": "click", "xpath": "html/body/missing"})
	assert step is not None
	assert step.interacted_element == [None]
	assert "index" not in step.model_output["actions"][0]["params"]
	assert len(patch_projection) == 0  # 定位失败，投影未调用


@pytest.mark.asyncio
async def test_unmappable_event_returns_none(tmp_path):
	browser = FakeBrowser()
	rec = Recorder(browser, rerun_history_dir=str(tmp_path))
	await rec.start()
	step = await rec.handle_event({"type": "hover", "xpath": "html/x"})
	assert step is None
	assert len(rec.history.history) == 0


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
	assert len(loaded.history) == 2  # click + done
	assert loaded.history[0].model_output["actions"][0]["name"] == "click"
	assert loaded.history[1].model_output["actions"][0]["name"] == "done"
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
	step = await rec.handle_event({"type": "switch_tab", "params": {"url": "https://a.com/page"}})
	assert step is not None
	assert step.model_output["actions"][0]["params"]["tab_id"] == "1234"


@pytest.mark.asyncio
async def test_close_tab_unknown_url_yields_empty_tab_id(tmp_path):
	# url 在 tabs 里找不到 → tab_id 空（close_tab 空=关当前，合法）
	browser = FakeBrowser(tabs=[])
	rec = Recorder(browser, rerun_history_dir=str(tmp_path))
	await rec.start()
	step = await rec.handle_event({"type": "close_tab", "params": {"url": "https://none.com"}})
	assert step is not None
	assert step.model_output["actions"][0]["params"]["tab_id"] == ""


@pytest.mark.asyncio
async def test_upload_file_resolves_to_upload_dir(tmp_path, patch_projection):
	browser = FakeBrowser(selector_map={5: SimpleNamespace(xpath="html/body/input")})
	rec = Recorder(browser, rerun_history_dir=str(tmp_path))
	await rec.start()
	step = await rec.handle_event({
		"type": "upload_file", "xpath": "html/body/input", "params": {"path": "video.mp4"},
	})
	assert step is not None
	p = step.model_output["actions"][0]["params"]
	assert p["index"] == 5  # upload_file 需 index，locate 命中
	# 文件名拼约定目录 <rerun_history_dir>/uploads（basename 防误发路径）
	assert p["path"].replace("\\", "/").endswith("uploads/video.mp4")


@pytest.mark.asyncio
async def test_stop_applies_denoise_to_history(tmp_path, patch_projection):
	browser = FakeBrowser(selector_map={5: SimpleNamespace(xpath="html/body/inp")})
	rec = Recorder(browser, rerun_history_dir=str(tmp_path), registry_version="v1")
	await rec.start()
	# 相邻同 index 两条 input_text → denoise 合一
	await rec.handle_event({"type": "input_text", "xpath": "html/body/inp", "params": {"text": "a"}, "ts": 1})
	await rec.handle_event({"type": "input_text", "xpath": "html/body/inp", "params": {"text": "ab"}, "ts": 2})

	path = await rec.stop(file_path="out.json")
	loaded = AgentHistoryList.load_from_file(path)
	assert len(loaded.history) == 1  # 合并后 1 步（无 done）
	assert loaded.history[0].model_output["actions"][0]["params"]["text"] == "ab"  # 取最终值


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
