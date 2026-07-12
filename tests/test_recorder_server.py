"""server HTTP 集成测试：start → event → stop 端到端（mock browser + 投影）。"""

from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from tree_walker.recorder import recorder as rec_mod
from tree_walker.recorder.recorder import Recorder
from tree_walker.recorder.server import make_app


class _FakeBrowser:
	def __init__(self, selector_map=None):
		self._map = selector_map or {}
		self.stopped = False

	async def start(self):
		pass

	async def stop(self):
		self.stopped = True

	async def get_state(self, include_screenshot=True):
		return SimpleNamespace(
			url="https://x.com", title="X",
			dom_state=SimpleNamespace(selector_map=self._map),
		)


class _FakeProj:
	def to_dict(self):
		return {"node_name": "BUTTON", "element_hash": 1, "stable_hash": 2, "x_path": "html/body/btn"}


@pytest.mark.asyncio
async def test_server_start_event_stop(tmp_path, monkeypatch):
	monkeypatch.setattr(
		rec_mod.DOMInteractedElement, "load_from_enhanced_dom_tree",
		staticmethod(lambda node: _FakeProj()),
	)
	browser = _FakeBrowser(selector_map={5: SimpleNamespace(xpath="html/body/btn")})
	rec = Recorder(browser, rerun_history_dir=str(tmp_path), registry_version="v1")
	app = await make_app(rec, default_out="out.json")
	client = TestClient(TestServer(app))
	await client.start_server()
	try:
		resp = await client.get("/health")
		assert resp.status == 200

		resp = await client.post("/start")
		assert (await resp.json())["ok"] is True

		# click 事件 → 定位 + 填 index
		resp = await client.post("/event", json={"type": "click", "xpath": "html/body/btn"})
		assert (await resp.json())["step"] == 0

		# 不可映射事件被忽略（ok=False）
		resp = await client.post("/event", json={"type": "hover"})
		assert (await resp.json())["ok"] is False

		# stop → 落盘（初始 navigate + click + done = 3 步）
		resp = await client.post("/stop", json={"mark_done": True, "done_text": "done"})
		data = await resp.json()
		assert data["ok"] is True
		assert data["steps"] == 3
		assert browser.stopped
	finally:
		await client.close()


@pytest.mark.asyncio
async def test_server_event_invalid_json(tmp_path):
	rec = Recorder(_FakeBrowser(), rerun_history_dir=str(tmp_path))
	app = await make_app(rec)
	client = TestClient(TestServer(app))
	await client.start_server()
	try:
		resp = await client.post("/event", data="not json")
		assert resp.status == 400
	finally:
		await client.close()
