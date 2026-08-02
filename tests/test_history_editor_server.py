"""P4 编辑器后端端点单测（aiohttp TestClient，不真起浏览器）。

覆盖：list / load / save / detect / health 端点 + 缺 name / 路径越界 / 文件不存在拒绝。
设计见 docs/p4/01-可视化编辑与CSV批量执行方案.md（阶段②）。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp.test_utils import TestClient, TestServer

from tree_walker.agent.views import ActionResult, AgentHistory, AgentHistoryList
from tree_walker.history_editor.server import make_app


@pytest_asyncio.fixture
async def client(tmp_path):
    app = await make_app(str(tmp_path))
    cli = TestClient(TestServer(app))
    await cli.start_server()
    yield cli
    await cli.close()


def _save(tmp_path, name, history):
    history.save_to_file(tmp_path / name)


@pytest.mark.asyncio
async def test_list(client, tmp_path):
    _save(tmp_path, "a.json", AgentHistoryList(history=[
        AgentHistory(step_number=1, model_output={"actions": []}, result=[])]))
    _save(tmp_path, "b.json", AgentHistoryList(history=[
        AgentHistory(step_number=1, model_output={"actions": []}, result=[])]))
    resp = await client.get("/history/list")
    assert resp.status == 200
    assert (await resp.json())["files"] == ["a.json", "b.json"]


@pytest.mark.asyncio
async def test_list_empty_dir(client):
    resp = await client.get("/history/list")
    assert resp.status == 200
    assert (await resp.json())["files"] == []


@pytest.mark.asyncio
async def test_load(client, tmp_path):
    _save(tmp_path, "a.json", AgentHistoryList(history=[
        AgentHistory(step_number=1, model_output={"actions": [
            {"name": "input_text", "params": {"text": "x"}}]}, result=[])]))
    resp = await client.get("/history/load", params={"name": "a.json"})
    assert resp.status == 200
    data = await resp.json()
    assert data["history"]["history"][0]["step_number"] == 1


@pytest.mark.asyncio
async def test_load_missing_name(client):
    resp = await client.get("/history/load")
    assert resp.status == 400


@pytest.mark.asyncio
async def test_load_traversal_rejected(client):
    resp = await client.get("/history/load", params={"name": "../etc/passwd"})
    assert resp.status == 400


@pytest.mark.asyncio
async def test_load_not_found(client):
    resp = await client.get("/history/load", params={"name": "nope.json"})
    assert resp.status == 400


@pytest.mark.asyncio
async def test_save_writes_file(client, tmp_path):
    h = AgentHistoryList(history=[
        AgentHistory(step_number=1, model_output={"actions": []}, result=[])])
    resp = await client.post(
        "/history/save", params={"name": "new.json"}, json=h.model_dump(mode="json")
    )
    assert resp.status == 200
    assert (tmp_path / "new.json").exists()


@pytest.mark.asyncio
async def test_save_traversal_rejected(client):
    resp = await client.post(
        "/history/save", params={"name": "../evil.json"}, json={"history": []}
    )
    assert resp.status == 400


@pytest.mark.asyncio
async def test_detect_detects_email(client, tmp_path):
    _save(tmp_path, "a.json", AgentHistoryList(history=[
        AgentHistory(step_number=1, model_output={"actions": [
            {"name": "input_text", "params": {"text": "alice@example.com"}}]}, result=[])]))
    resp = await client.get("/history/detect", params={"name": "a.json"})
    assert resp.status == 200
    data = await resp.json()
    assert "email" in data["variables"]
    assert data["variables"]["email"]["original_value"] == "alice@example.com"


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status == 200
    assert (await resp.json())["ok"] is True


@pytest.mark.asyncio
async def test_serve_index(client):
    from tree_walker.history_editor.server import _STATIC_DIR

    if not (_STATIC_DIR / "index.html").exists():
        pytest.skip("前端未构建（跑 scripts/build_editor.ps1）")
    resp = await client.get("/")
    assert resp.status == 200
    assert "TreeWalker 历史编辑器" in await resp.text()


@pytest.mark.asyncio
async def test_history_list_not_intercepted_by_catchall(client):
    # catch-all /{tail:.*} 注册后，/history/list 仍精确匹配返回 JSON
    resp = await client.get("/history/list")
    assert resp.status == 200
    assert "files" in await resp.json()


@pytest.mark.asyncio
async def test_rerun(client, tmp_path, monkeypatch):
    _save(tmp_path, "a.json", AgentHistoryList(history=[
        AgentHistory(step_number=1, model_output={"actions": []}, result=[])]))
    fake_agent = SimpleNamespace(load_and_rerun=AsyncMock(
        return_value=[ActionResult(is_done=True, success=True, extracted_content="ok")]))
    monkeypatch.setattr("tree_walker.history_editor.server._build_agent", lambda: fake_agent)
    resp = await client.post("/history/rerun", params={"name": "a.json"}, json={"variables": {}})
    assert resp.status == 200
    data = await resp.json()
    assert data["results"][0]["success"] is True
    fake_agent.load_and_rerun.assert_awaited_once_with("a.json", variables=None)


@pytest.mark.asyncio
async def test_rerun_missing_name(client):
    resp = await client.post("/history/rerun", json={})
    assert resp.status == 400


@pytest.mark.asyncio
async def test_rerun_build_agent_error(client, monkeypatch):
    def boom():
        raise RuntimeError("Chrome 未启动")
    monkeypatch.setattr("tree_walker.history_editor.server._build_agent", boom)
    resp = await client.post("/history/rerun", params={"name": "a.json"}, json={})
    assert resp.status == 400
    assert "Chrome" in (await resp.json())["error"]


@pytest.mark.asyncio
async def test_save_rejects_unequal_pairing(client):
    # actions 1 个、interacted_element 2 个 → 不等长，拒绝保存
    bad = {"history": [{"step_number": 1, "model_output": {"actions": [
        {"name": "click", "params": {}}]},
        "result": [], "interacted_element": [{}, {}]}]}
    resp = await client.post("/history/save", params={"name": "bad.json"}, json=bad)
    assert resp.status == 400
    assert "不等长" in (await resp.json())["error"]
