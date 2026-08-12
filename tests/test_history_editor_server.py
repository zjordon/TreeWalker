"""P4 编辑器后端端点单测（aiohttp TestClient，不真起浏览器）。

覆盖：list / load / save / detect / health 端点 + 缺 name / 路径越界 / 文件不存在拒绝。
设计见 docs/p4/01-可视化编辑与CSV批量执行方案.md（阶段②）。
"""

from __future__ import annotations

import asyncio
import logging

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


# ── CSV 批量重放（issue #155）─────────────────────────────────────────────


def _strip_live_log_handlers():
    """摘除可能泄漏到 tree_walker logger 的 _SseLogHandler（测试隔离）。"""
    from tree_walker.history_editor.server import _SseLogHandler
    tw = logging.getLogger("tree_walker")
    for h in list(tw.handlers):
        if isinstance(h, _SseLogHandler):
            tw.removeHandler(h)


@pytest.fixture(autouse=True)
def _isolate_agent_tasks():
    """每个测试前后清空模块级 _BATCH_TASKS / _LIVE_TASKS + 摘除泄漏的日志 handler。"""
    from tree_walker.history_editor import server
    server._BATCH_TASKS.clear()
    server._LIVE_TASKS.clear()
    _strip_live_log_handlers()
    yield
    server._BATCH_TASKS.clear()
    server._LIVE_TASKS.clear()
    _strip_live_log_handlers()


def _csv_formdata(text):
    """把 CSV 文本包成 multipart FormData（field 名 'file'）。"""
    from aiohttp import FormData
    data = FormData()
    data.add_field("file", text.encode("utf-8"), filename="data.csv",
                   content_type="text/csv")
    return data


def _save_blank_history(tmp_path, name="a.json"):
    _save(tmp_path, name, AgentHistoryList(history=[
        AgentHistory(step_number=1, model_output={"actions": []}, result=[])]))


@pytest.mark.asyncio
async def test_batch_start_uploads_csv(client, tmp_path, monkeypatch):
    _save_blank_history(tmp_path)
    fake_agent = SimpleNamespace(
        batch_rerun=AsyncMock(return_value=[]), stop=lambda: None)
    monkeypatch.setattr("tree_walker.history_editor.server._build_agent", lambda: fake_agent)
    resp = await client.post(
        "/history/batch/start", params={"name": "a.json"},
        data=_csv_formdata("email\na@b.com\nc@d.com"))
    assert resp.status == 200
    j = await resp.json()
    assert "task_id" in j
    assert j["total_rows"] == 2


@pytest.mark.asyncio
async def test_batch_start_missing_name(client):
    resp = await client.post(
        "/history/batch/start", data=_csv_formdata("email\na@b.com"))
    assert resp.status == 400


@pytest.mark.asyncio
async def test_batch_start_missing_file(client, tmp_path):
    _save_blank_history(tmp_path)
    resp = await client.post("/history/batch/start", params={"name": "a.json"})
    assert resp.status == 400  # 无 multipart file part


@pytest.mark.asyncio
async def test_batch_start_traversal_rejected(client):
    resp = await client.post(
        "/history/batch/start", params={"name": "../evil.json"},
        data=_csv_formdata("email\na@b.com"))
    assert resp.status == 400


@pytest.mark.asyncio
async def test_batch_start_empty_csv(client, tmp_path, monkeypatch):
    _save_blank_history(tmp_path)
    fake_agent = SimpleNamespace(batch_rerun=AsyncMock(return_value=[]), stop=lambda: None)
    monkeypatch.setattr("tree_walker.history_editor.server._build_agent", lambda: fake_agent)
    resp = await client.post(
        "/history/batch/start", params={"name": "a.json"},
        data=_csv_formdata("email"))  # 只表头
    assert resp.status == 400
    assert "无数据行" in (await resp.json())["error"]


@pytest.mark.asyncio
async def test_batch_concurrent_rejected(client, tmp_path, monkeypatch):
    _save_blank_history(tmp_path)
    hold = asyncio.Event()

    async def slow_batch(hf, csv_p, *, on_row=None, **kw):
        await hold.wait()
        return []

    fake_agent = SimpleNamespace(batch_rerun=slow_batch, stop=lambda: None)
    monkeypatch.setattr("tree_walker.history_editor.server._build_agent", lambda: fake_agent)
    resp1 = await client.post(
        "/history/batch/start", params={"name": "a.json"},
        data=_csv_formdata("email\na@b.com"))
    assert resp1.status == 200
    resp2 = await client.post(  # 已有运行中 → 409
        "/history/batch/start", params={"name": "a.json"},
        data=_csv_formdata("email\nc@d.com"))
    assert resp2.status == 409
    hold.set()  # 放行第一个，避免任务泄漏


@pytest.mark.asyncio
async def test_batch_progress_sse(client, tmp_path, monkeypatch):
    from tree_walker.agent.views import BatchRowResult
    _save_blank_history(tmp_path)

    async def fake_batch(hf, csv_p, *, on_row=None, **kw):
        r0 = BatchRowResult(row_index=0, success=True, n_steps=3)
        r1 = BatchRowResult(row_index=1, success=False, error="boom")
        if on_row:
            await on_row(r0)
            await on_row(r1)
        return [r0, r1]

    fake_agent = SimpleNamespace(batch_rerun=fake_batch, stop=lambda: None)
    monkeypatch.setattr("tree_walker.history_editor.server._build_agent", lambda: fake_agent)
    resp = await client.post(
        "/history/batch/start", params={"name": "a.json"},
        data=_csv_formdata("email\na@b.com\nc@d.com"))
    task_id = (await resp.json())["task_id"]

    resp = await client.get("/history/batch/progress", params={"task_id": task_id})
    assert resp.headers["Content-Type"] == "text/event-stream"
    events = []
    async for raw in resp.content:
        line = raw.decode().strip()
        if line.startswith("event: "):
            events.append(line[7:])
    assert events.count("row") == 2
    assert "done" in events


@pytest.mark.asyncio
async def test_batch_progress_unknown_task(client):
    resp = await client.get("/history/batch/progress", params={"task_id": "nope"})
    assert resp.status == 404


@pytest.mark.asyncio
async def test_batch_cancel_unknown_task(client):
    resp = await client.post("/history/batch/cancel", params={"task_id": "nope"})
    assert resp.status == 404


# ── Live agent 探索任务（P6 M1）─────────────────────────────────────────────


def _live_agent(*, run=None, bus=None, history=None, browser=None):
    """构造 live task 假 agent：run / _obs_bus / pause·resume·stop / save_history / browser。"""
    from tree_walker.observability import EventBus as _Bus
    return SimpleNamespace(
        run=run or AsyncMock(return_value=AgentHistoryList()),
        _obs_bus=bus if bus is not None else _Bus(),
        pause=lambda: None,
        resume=lambda: None,
        stop=lambda: None,
        _setup_signal_handler=lambda: None,
        save_history=lambda *a, **k: None,
        history=history if history is not None else AgentHistoryList(),
        browser=browser,
    )


@pytest.mark.asyncio
async def test_task_start_returns_task_id(client, monkeypatch):
    monkeypatch.setattr(
        "tree_walker.history_editor.server._build_agent", lambda *, task="": _live_agent())
    resp = await client.post("/task/start", json={"task": "帮我搜索猫"})
    assert resp.status == 200
    assert "task_id" in (await resp.json())


@pytest.mark.asyncio
async def test_task_start_missing_task(client):
    resp = await client.post("/task/start", json={})
    assert resp.status == 400


@pytest.mark.asyncio
async def test_task_start_invalid_json(client):
    resp = await client.post("/task/start", data="not json")
    assert resp.status == 400


@pytest.mark.asyncio
async def test_task_start_build_agent_error(client, monkeypatch):
    def boom(*, task=""):
        raise RuntimeError("Chrome 未启动")
    monkeypatch.setattr("tree_walker.history_editor.server._build_agent", boom)
    resp = await client.post("/task/start", json={"task": "x"})
    assert resp.status == 400
    assert "Chrome" in (await resp.json())["error"]


@pytest.mark.asyncio
async def test_task_start_concurrent_with_live_rejected(client, monkeypatch):
    hold = asyncio.Event()

    async def slow_run(keep_alive=False):
        await hold.wait()
        return AgentHistoryList()

    monkeypatch.setattr(
        "tree_walker.history_editor.server._build_agent",
        lambda *, task="": _live_agent(run=slow_run))
    r1 = await client.post("/task/start", json={"task": "a"})
    assert r1.status == 200
    r2 = await client.post("/task/start", json={"task": "b"})  # 已有 live → 409
    assert r2.status == 409
    hold.set()  # 放行第一个，避免任务泄漏


@pytest.mark.asyncio
async def test_batch_rejected_when_live_active(client, tmp_path, monkeypatch):
    # live 在跑 → batch start 应 409（共享单槽，§8 决策 1）
    hold = asyncio.Event()

    async def slow_run(keep_alive=False):
        await hold.wait()
        return AgentHistoryList()

    monkeypatch.setattr(
        "tree_walker.history_editor.server._build_agent",
        lambda *, task="": _live_agent(run=slow_run))
    _save_blank_history(tmp_path)
    r_live = await client.post("/task/start", json={"task": "a"})
    assert r_live.status == 200
    r_batch = await client.post(
        "/history/batch/start", params={"name": "a.json"},
        data=_csv_formdata("email\na@b.com"))
    assert r_batch.status == 409
    hold.set()


@pytest.mark.asyncio
async def test_task_events_sse(client, monkeypatch):
    from tree_walker.observability import EventBus
    from tree_walker.observability.events import StepStartEvent, StepEndEvent

    bus = EventBus()

    async def emitting_run(keep_alive=False):
        bus.emit(StepStartEvent(step=1, session_id="t"))
        bus.emit(StepEndEvent(step=1, session_id="t", duration_seconds=0.1,
                              is_done=True, consecutive_failures=0))
        return AgentHistoryList()

    monkeypatch.setattr(
        "tree_walker.history_editor.server._build_agent",
        lambda *, task="": _live_agent(run=emitting_run, bus=bus))
    resp = await client.post("/task/start", json={"task": "x"})
    task_id = (await resp.json())["task_id"]

    resp = await client.get("/task/events", params={"task_id": task_id})
    assert resp.headers["Content-Type"] == "text/event-stream"
    events = []
    async for raw in resp.content:
        line = raw.decode().strip()
        if line.startswith("event: "):
            events.append(line[7:])
    assert "step_start" in events
    assert "step_end" in events
    assert "done" in events


@pytest.mark.asyncio
async def test_task_events_missing_task_id(client):
    resp = await client.get("/task/events")
    assert resp.status == 400


@pytest.mark.asyncio
async def test_task_events_unknown_task(client):
    resp = await client.get("/task/events", params={"task_id": "nope"})
    assert resp.status == 404


@pytest.mark.asyncio
async def test_task_control_pause_resume_stop(client, monkeypatch):
    calls = []
    agent = _live_agent()
    agent.pause = lambda: calls.append("pause")
    agent.resume = lambda: calls.append("resume")
    agent.stop = lambda: calls.append("stop")
    monkeypatch.setattr(
        "tree_walker.history_editor.server._build_agent", lambda *, task="": agent)
    r = await client.post("/task/start", json={"task": "x"})
    task_id = (await r.json())["task_id"]

    assert (await client.post("/task/pause", params={"task_id": task_id})).status == 200
    assert (await client.post("/task/resume", params={"task_id": task_id})).status == 200
    assert (await client.post("/task/stop", params={"task_id": task_id})).status == 200
    assert calls == ["pause", "resume", "stop"]


@pytest.mark.asyncio
async def test_task_control_missing_and_unknown_task(client):
    assert (await client.post("/task/pause")).status == 400  # 缺 task_id
    assert (await client.post("/task/stop", params={"task_id": "nope"})).status == 404


@pytest.mark.asyncio
async def test_task_record_saves_history(client, monkeypatch):
    saved = {}

    def fake_save(name, *a, **k):
        saved["name"] = name

    hist = AgentHistoryList(history=[
        AgentHistory(step_number=1, model_output={"actions": []}, result=[])])
    agent = _live_agent(history=hist)

    async def quick_run(keep_alive=False):
        return AgentHistoryList()

    agent.run = quick_run
    agent.save_history = fake_save
    monkeypatch.setattr(
        "tree_walker.history_editor.server._build_agent", lambda *, task="": agent)
    r = await client.post("/task/start", json={"task": "x", "record": True})
    task_id = (await r.json())["task_id"]

    resp = await client.get("/task/events", params={"task_id": task_id})
    async for _ in resp.content:  # 消费到 done，确保 run_live 跑完
        pass
    assert "name" in saved  # save_history 被调用
    assert saved["name"].endswith(".json")


async def _sse_data_lines(resp):
    """收集 SSE 响应里的 data: 行内容（去前缀）。"""
    out = []
    async for raw in resp.content:
        s = raw.decode().strip()
        if s.startswith("data: "):
            out.append(s[6:])
    return out


@pytest.mark.asyncio
async def test_task_events_run_error(client, monkeypatch):
    # agent.run 抛异常 → SSE done 携带 success:false + error
    async def boom_run(keep_alive=False):
        raise RuntimeError("agent 炸了")

    monkeypatch.setattr(
        "tree_walker.history_editor.server._build_agent",
        lambda *, task="": _live_agent(run=boom_run))
    resp = await client.post("/task/start", json={"task": "x"})
    task_id = (await resp.json())["task_id"]

    resp = await client.get("/task/events", params={"task_id": task_id})
    data_lines = await _sse_data_lines(resp)
    assert data_lines
    assert '"success": false' in data_lines[-1]
    assert "agent 炸了" in data_lines[-1]


@pytest.mark.asyncio
async def test_task_events_replay_after_done(client, monkeypatch):
    # 任务在 SSE 二次连入前已完成 + 队列已空 → 连入即补发 done（replay 分支）
    async def quick(keep_alive=False):
        return AgentHistoryList()

    monkeypatch.setattr(
        "tree_walker.history_editor.server._build_agent",
        lambda *, task="": _live_agent(run=quick))
    resp = await client.post("/task/start", json={"task": "x"})
    task_id = (await resp.json())["task_id"]

    first = await client.get("/task/events", params={"task_id": task_id})
    async for _ in first.content:  # 第一次消费 done，队列清空、final_event 置位
        pass

    second = await client.get("/task/events", params={"task_id": task_id})
    events = []
    async for raw in second.content:
        line = raw.decode().strip()
        if line.startswith("event: "):
            events.append(line[7:])
    assert events == ["done"]  # 走 replay 分支，只补发 done


@pytest.mark.asyncio
async def test_task_record_save_failure(client, monkeypatch):
    # record=True 但 save_history 抛异常 → done 仍 success、不崩、无 saved
    def bad_save(*a, **k):
        raise RuntimeError("盘满了")

    hist = AgentHistoryList(history=[
        AgentHistory(step_number=1, model_output={"actions": []}, result=[])])

    async def quick(keep_alive=False):
        return AgentHistoryList()

    agent = _live_agent(run=quick, history=hist)
    agent.save_history = bad_save
    monkeypatch.setattr(
        "tree_walker.history_editor.server._build_agent", lambda *, task="": agent)
    r = await client.post("/task/start", json={"task": "x", "record": True})
    task_id = (await r.json())["task_id"]

    resp = await client.get("/task/events", params={"task_id": task_id})
    data_lines = await _sse_data_lines(resp)
    assert '"success": true' in data_lines[-1]
    assert '"saved"' not in data_lines[-1]


@pytest.mark.asyncio
async def test_task_start_clears_finished_handle(client, monkeypatch):
    # 启动新任务时，已结束的 live handle 被清理（不立即 pop、留给下次 start 回收）
    async def quick(keep_alive=False):
        return AgentHistoryList()

    monkeypatch.setattr(
        "tree_walker.history_editor.server._build_agent",
        lambda *, task="": _live_agent(run=quick))
    from tree_walker.history_editor import server

    r1 = await client.post("/task/start", json={"task": "a"})
    task1 = (await r1.json())["task_id"]
    resp = await client.get("/task/events", params={"task_id": task1})
    async for _ in resp.content:  # 让第一个完成
        pass
    assert task1 in server._LIVE_TASKS  # 仍在（不立即 pop）
    assert server._LIVE_TASKS[task1].final_event is not None

    r2 = await client.post("/task/start", json={"task": "b"})  # 启动第二个 → 清理第一个
    assert r2.status == 200
    assert task1 not in server._LIVE_TASKS


# ── 日志事件化 + 截图推帧（P6 M2）───────────────────────────────────────────


@pytest.mark.asyncio
async def test_sse_log_handler_enqueues():
    from tree_walker.history_editor.server import _SseLogHandler
    q: asyncio.Queue = asyncio.Queue()
    h = _SseLogHandler(q)
    log = logging.getLogger("tree_walker.agent.unit_test")
    log.addHandler(h)
    try:
        log.warning("hello %s", "world")
    finally:
        log.removeHandler(h)
    item = q.get_nowait()
    assert item["type"] == "log"
    assert item["level"] == "WARNING"
    assert item["msg"] == "hello world"
    assert item["logger"] == "tree_walker.agent.unit_test"


@pytest.mark.asyncio
async def test_task_events_include_logs(client, monkeypatch):
    log = logging.getLogger("tree_walker.agent.integration_test")

    async def logging_run(keep_alive=False):
        log.info("探索中…")
        return AgentHistoryList()

    monkeypatch.setattr(
        "tree_walker.history_editor.server._build_agent",
        lambda *, task="": _live_agent(run=logging_run))
    resp = await client.post("/task/start", json={"task": "x"})
    task_id = (await resp.json())["task_id"]

    resp = await client.get("/task/events", params={"task_id": task_id})
    events = []
    async for raw in resp.content:
        s = raw.decode().strip()
        if s.startswith("event: "):
            events.append(s[7:])
    assert "log" in events


@pytest.mark.asyncio
async def test_task_events_include_screenshot(client, monkeypatch):
    from tree_walker.observability import EventBus
    from tree_walker.observability.events import StepEndEvent

    bus = EventBus()
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    take = AsyncMock(return_value=png)
    browser = SimpleNamespace(take_screenshot=take)

    async def stepping_run(keep_alive=False):
        bus.emit(StepEndEvent(step=1, session_id="t", duration_seconds=0.1,
                              is_done=True, consecutive_failures=0))
        return AgentHistoryList()

    monkeypatch.setattr(
        "tree_walker.history_editor.server._build_agent",
        lambda *, task="": _live_agent(run=stepping_run, bus=bus, browser=browser))
    resp = await client.post("/task/start", json={"task": "x"})
    task_id = (await resp.json())["task_id"]

    resp = await client.get("/task/events", params={"task_id": task_id})
    shots = []
    async for raw in resp.content:
        s = raw.decode().strip()
        if s.startswith("event: screenshot"):
            shots.append(s)
    assert shots  # 至少一帧
    assert take.await_count >= 1


@pytest.mark.asyncio
async def test_screenshot_downsampled_via_resize(client, monkeypatch):
    # monkeypatch resize_screenshot_bytes → 断言被按 _SCREENSHOT_TARGET 调用（降采样路径）
    from tree_walker.observability import EventBus
    from tree_walker.observability.events import StepEndEvent
    from tree_walker.browser import image_utils

    seen = {}

    def spy(data, target):
        seen["target"] = target
        return data

    monkeypatch.setattr(image_utils, "resize_screenshot_bytes", spy)

    bus = EventBus()
    browser = SimpleNamespace(take_screenshot=AsyncMock(return_value=b"\x89PNG"))

    async def stepping_run(keep_alive=False):
        bus.emit(StepEndEvent(step=1, session_id="t", duration_seconds=0.1,
                              is_done=True, consecutive_failures=0))
        return AgentHistoryList()

    monkeypatch.setattr(
        "tree_walker.history_editor.server._build_agent",
        lambda *, task="": _live_agent(run=stepping_run, bus=bus, browser=browser))
    resp = await client.post("/task/start", json={"task": "x"})
    task_id = (await resp.json())["task_id"]

    resp = await client.get("/task/events", params={"task_id": task_id})
    async for _ in resp.content:
        pass
    assert seen.get("target") == (1280, 800)
