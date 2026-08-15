"""P4 编辑器后端端点单测（aiohttp TestClient，不真起浏览器）。

覆盖：list / load / save / detect / health 端点 + 缺 name / 路径越界 / 文件不存在拒绝。
设计见 docs/p4/01-可视化编辑与CSV批量执行方案.md（阶段②）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import pytest
import pytest_asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp.test_utils import TestClient, TestServer

from tree_walker.agent.views import ActionResult, AgentHistory, AgentHistoryList
from tree_walker.web.server import make_app


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
    from tree_walker.web.server import _STATIC_DIR

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
    monkeypatch.setattr("tree_walker.web.server._build_agent", lambda: fake_agent)
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
    monkeypatch.setattr("tree_walker.web.server._build_agent", boom)
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
    from tree_walker.web.server import _SseLogHandler
    tw = logging.getLogger("tree_walker")
    for h in list(tw.handlers):
        if isinstance(h, _SseLogHandler):
            tw.removeHandler(h)


@pytest.fixture(autouse=True)
def _isolate_agent_tasks():
    """每个测试前后清空模块级 _BATCH_TASKS / _LIVE_TASKS + 摘除泄漏的日志 handler。"""
    from tree_walker.web import server
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
    monkeypatch.setattr("tree_walker.web.server._build_agent", lambda: fake_agent)
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
    monkeypatch.setattr("tree_walker.web.server._build_agent", lambda: fake_agent)
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
    monkeypatch.setattr("tree_walker.web.server._build_agent", lambda: fake_agent)
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
    monkeypatch.setattr("tree_walker.web.server._build_agent", lambda: fake_agent)
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


# ── Settings 设置面（T2 C M3）───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_settings_get_defaults_and_masking(client, monkeypatch):
    # 清掉本机可能存在的同名 env，验证注册表默认值 + API key 脱敏
    for env in ("LLM_MODEL", "ZHIPU_API_KEY", "AGENT_MAX_STEPS", "LLM_OUTPUT_MODE",
                "AGENT_ENABLE_SKILL_INJECTION"):
        monkeypatch.delenv(env, raising=False)
    resp = await client.get("/settings/get")
    assert resp.status == 200
    data = await resp.json()
    assert data["applies"] == "new_tasks"
    fields = {f["env"]: f for f in data["fields"]}
    assert fields["LLM_MODEL"]["value"] == "glm-5.1"
    assert fields["LLM_MODEL"]["section"] == "llm"
    assert fields["AGENT_MAX_STEPS"]["value"] == "100"
    assert fields["AGENT_MAX_STEPS"]["type"] == "int"
    assert fields["LLM_OUTPUT_MODE"]["choices"] == ["standard", "flash", "thinking"]
    # skill 注入默认 false = env 层默认（config.py），web live 由 _build_agent 强制开
    assert fields["AGENT_ENABLE_SKILL_INJECTION"]["value"] == "false"
    # 敏感：未设 → 空值不掩码；设了 → **** + 尾 4 位；sensitive 标志供前端区分「敏感未设置」
    assert fields["ZHIPU_API_KEY"]["masked"] is False
    assert fields["ZHIPU_API_KEY"]["sensitive"] is True
    assert fields["LLM_MODEL"]["sensitive"] is False
    monkeypatch.setenv("ZHIPU_API_KEY", "sk-abcd1234efgh")
    resp = await client.get("/settings/get")
    fields = {f["env"]: f for f in (await resp.json())["fields"]}
    assert fields["ZHIPU_API_KEY"]["value"] == "****efgh"
    assert fields["ZHIPU_API_KEY"]["masked"] is True


@pytest.mark.asyncio
async def test_settings_set_whitelist_and_validation(client, monkeypatch):
    # 注册表外 env（含进程级 CDP_PORT）→ 400，防任意 env 注入
    resp = await client.post("/settings/set", json={"CDP_PORT": "9224"})
    assert resp.status == 400
    # 非法 int / bool / enum → 400
    assert (await client.post("/settings/set", json={"AGENT_MAX_STEPS": "abc"})).status == 400
    assert (await client.post("/settings/set", json={"AGENT_MAX_STEPS": "1.5"})).status == 400
    assert (await client.post("/settings/set", json={"AGENT_ENABLE_PLANNING": "yes"})).status == 400
    assert (await client.post("/settings/set", json={"LLM_OUTPUT_MODE": "turbo"})).status == 400
    # 非法 JSON / 非对象 body → 400
    assert (await client.post("/settings/set", data="not json")).status == 400
    assert (await client.post("/settings/set", json=[1, 2])).status == 400
    # 合法 set → 进程内存 override（os.environ）
    monkeypatch.delenv("AGENT_MAX_STEPS", raising=False)
    monkeypatch.delenv("AGENT_ENABLE_PLANNING", raising=False)
    resp = await client.post("/settings/set", json={
        "AGENT_MAX_STEPS": "7", "AGENT_ENABLE_PLANNING": "false", "AGENT_RERUN_DELAY_BETWEEN_ACTIONS": "2"})
    assert resp.status == 200
    body = await resp.json()
    assert body["ok"] is True
    assert body["applies"] == "new_tasks"
    assert body["applied"] == ["AGENT_ENABLE_PLANNING", "AGENT_MAX_STEPS",
                               "AGENT_RERUN_DELAY_BETWEEN_ACTIONS"]
    assert os.environ["AGENT_MAX_STEPS"] == "7"
    assert os.environ["AGENT_ENABLE_PLANNING"] == "false"
    assert os.environ["AGENT_RERUN_DELAY_BETWEEN_ACTIONS"] == "2"
    # bool 值规范化：JSON true → "true"
    resp = await client.post("/settings/set", json={"AGENT_ENABLE_PLANNING": True})
    assert resp.status == 200
    assert os.environ["AGENT_ENABLE_PLANNING"] == "true"


@pytest.mark.asyncio
async def test_settings_set_sensitive_skip_and_atomicity(client, monkeypatch):
    # 敏感字段：空值 / **** 掩码串回传 → 跳过不写（防掩码覆盖真值）
    monkeypatch.setenv("ZHIPU_API_KEY", "sk-real-key-9999")
    resp = await client.post("/settings/set", json={"ZHIPU_API_KEY": "****9999"})
    assert resp.status == 200
    assert os.environ["ZHIPU_API_KEY"] == "sk-real-key-9999"  # 未被掩码串覆盖
    resp = await client.post("/settings/set", json={"ZHIPU_API_KEY": ""})
    assert resp.status == 200
    assert os.environ["ZHIPU_API_KEY"] == "sk-real-key-9999"
    # 真实敏感值正常写入
    resp = await client.post("/settings/set", json={"ZHIPU_API_KEY": "sk-new-key-1111"})
    assert resp.status == 200
    assert os.environ["ZHIPU_API_KEY"] == "sk-new-key-1111"
    # 原子性：一项合法 + 一项非法（enum 越界）→ 整体 400，合法项也不落盘
    monkeypatch.delenv("AGENT_MAX_FAILURES", raising=False)
    resp = await client.post("/settings/set", json={"AGENT_MAX_FAILURES": "9", "LLM_OUTPUT_MODE": "turbo"})
    assert resp.status == 400
    assert "AGENT_MAX_FAILURES" not in os.environ


@pytest.mark.asyncio
async def test_settings_set_affects_new_agent(client, monkeypatch):
    """set 后新任务的 settings 生效——load_settings 每任务现读 env（plan §8.1 生效范围）。"""
    built = {}

    def fake_build_agent(*, task="", model=None):
        from tree_walker.config import load_settings
        built["settings"] = load_settings()
        return _live_agent()

    monkeypatch.setattr("tree_walker.web.server._build_agent", fake_build_agent)
    monkeypatch.delenv("AGENT_MAX_STEPS", raising=False)
    resp = await client.post("/settings/set", json={"AGENT_MAX_STEPS": "3"})
    assert resp.status == 200
    resp = await client.post("/task/start", json={"task": "x"})
    assert resp.status == 200
    assert built["settings"].agent.max_steps == 3


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
        "tree_walker.web.server._build_agent", lambda *, task="", model=None: _live_agent())
    resp = await client.post("/task/start", json={"task": "帮我搜索猫"})
    assert resp.status == 200
    assert "task_id" in (await resp.json())


@pytest.mark.asyncio
async def test_task_start_missing_task(client):
    resp = await client.post("/task/start", json={})
    assert resp.status == 400


@pytest.mark.asyncio
async def test_task_start_model_passed_to_build_agent(client, monkeypatch):
    """T2 I5（M6）：/task/start 带 model → 透传 _build_agent；缺省 None（跟随设置默认）。"""
    captured = {}

    def fake_build(*, task="", model=None):
        captured["model"] = model
        return _live_agent()

    monkeypatch.setattr("tree_walker.web.server._build_agent", fake_build)
    resp = await client.post("/task/start", json={"task": "x", "model": "glm-test"})
    assert resp.status == 200
    assert captured["model"] == "glm-test"
    resp = await client.post("/task/start", json={"task": "y"})  # 缺省 → None
    assert resp.status == 200
    assert captured["model"] is None


def test_build_agent_model_overrides_llm_settings(monkeypatch):
    """T2 I5（M6）：model override 只作用于本次构造的 settings.llm.model；缺省不动。

    monkeypatch Agent/BrowserSession/LLMClient + load_settings（免真 Chrome/LLM）。
    """
    from tree_walker.config import Settings
    from tree_walker.web.server import _build_agent

    settings = Settings()
    settings.browser.ws_url = "ws://fake"  # 过 ws_url 非空检查
    monkeypatch.setattr("tree_walker.config.load_settings", lambda: settings)
    monkeypatch.setattr("tree_walker.Agent", lambda **kw: SimpleNamespace(**kw))
    monkeypatch.setattr("tree_walker.BrowserSession", lambda s: object())
    monkeypatch.setattr("tree_walker.LLMClient", lambda s: object())

    agent = _build_agent(task="x", model="glm-custom")
    assert settings.llm.model == "glm-custom"  # 本次 override 生效
    assert agent.task == "x"

    settings2 = Settings()
    settings2.browser.ws_url = "ws://fake"
    monkeypatch.setattr("tree_walker.config.load_settings", lambda: settings2)
    _build_agent(task="y")  # 缺省：不改 model
    assert settings2.llm.model == "glm-5.1"


@pytest.mark.asyncio
async def test_task_start_invalid_json(client):
    resp = await client.post("/task/start", data="not json")
    assert resp.status == 400


@pytest.mark.asyncio
async def test_task_start_build_agent_error(client, monkeypatch):
    def boom(*, task="", model=None):
        raise RuntimeError("Chrome 未启动")
    monkeypatch.setattr("tree_walker.web.server._build_agent", boom)
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
        "tree_walker.web.server._build_agent",
        lambda *, task="", model=None: _live_agent(run=slow_run))
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
        "tree_walker.web.server._build_agent",
        lambda *, task="", model=None: _live_agent(run=slow_run))
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
        "tree_walker.web.server._build_agent",
        lambda *, task="", model=None: _live_agent(run=emitting_run, bus=bus))
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
        "tree_walker.web.server._build_agent", lambda *, task="", model=None: agent)
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
        "tree_walker.web.server._build_agent", lambda *, task="", model=None: agent)
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
        "tree_walker.web.server._build_agent",
        lambda *, task="", model=None: _live_agent(run=boom_run))
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
        "tree_walker.web.server._build_agent",
        lambda *, task="", model=None: _live_agent(run=quick))
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
        "tree_walker.web.server._build_agent", lambda *, task="", model=None: agent)
    r = await client.post("/task/start", json={"task": "x", "record": True})
    task_id = (await r.json())["task_id"]

    resp = await client.get("/task/events", params={"task_id": task_id})
    data_lines = await _sse_data_lines(resp)
    # #164：success 取 agent 真实终态——该用例的假 agent 无 done 结果 → False（未完成）
    assert '"success": false' in data_lines[-1]
    assert '"saved"' not in data_lines[-1]


# ── 任务历史（T2 I6 M5，与 TUI 共享）────────────────────────────────────────


def test_task_history_file_matches_tui():
    """与 TUI 同一路径（tui/app.py HISTORY_FILE）——两端任务历史互通的前提。"""
    from pathlib import Path

    from tree_walker.tui.app import HISTORY_FILE
    from tree_walker.web import server

    assert server._TASK_HISTORY_FILE == HISTORY_FILE == Path.home() / ".treewalker" / "history.json"


@pytest.mark.asyncio
async def test_task_history_roundtrip_and_dedupe(client, monkeypatch, tmp_path):
    from tree_walker.web import server

    hist_file = tmp_path / "history.json"
    monkeypatch.setattr(server, "_TASK_HISTORY_FILE", hist_file)

    # 缺失文件 → []（TUI _load_history 同款容错）
    resp = await client.get("/task/history")
    assert resp.status == 200
    assert (await resp.json())["tasks"] == []

    # append → 落盘 + 读回（格式与 TUI 一致：list[str] JSON）
    resp = await client.post("/task/history", json={"task": "搜索猫"})
    assert resp.status == 200
    assert (await resp.json())["count"] == 1
    resp = await client.get("/task/history")
    assert (await resp.json())["tasks"] == ["搜索猫"]
    assert json.loads(hist_file.read_text(encoding="utf-8")) == ["搜索猫"]

    # 连续去重（TUI 同款：task == hist[-1] 不重复记）
    await client.post("/task/history", json={"task": "搜索猫"})
    resp = await client.get("/task/history")
    assert (await resp.json())["tasks"] == ["搜索猫"]

    # 交替任务不丢
    await client.post("/task/history", json={"task": "上传视频"})
    resp = await client.get("/task/history")
    assert (await resp.json())["tasks"] == ["搜索猫", "上传视频"]


@pytest.mark.asyncio
async def test_task_history_corrupt_cap_and_validation(client, monkeypatch, tmp_path):
    from tree_walker.web import server

    hist_file = tmp_path / "history.json"
    monkeypatch.setattr(server, "_TASK_HISTORY_FILE", hist_file)

    # 损坏 JSON → []，且下次 append 重写为合法文件
    hist_file.write_text("{broken", encoding="utf-8")
    assert (await (await client.get("/task/history")).json())["tasks"] == []
    await client.post("/task/history", json={"task": "x"})
    assert json.loads(hist_file.read_text(encoding="utf-8")) == ["x"]

    # 非字符串成员 → [] 容错
    hist_file.write_text('["a", 1]', encoding="utf-8")
    assert (await (await client.get("/task/history")).json())["tasks"] == []

    # 100 截断：预写 100 条 → append 1 → 仍 100 且最老被挤掉（TUI 同款 [-100:]）
    hist_file.write_text(json.dumps([f"t{i}" for i in range(100)]), encoding="utf-8")
    await client.post("/task/history", json={"task": "new"})
    data = json.loads(hist_file.read_text(encoding="utf-8"))
    assert len(data) == 100
    assert data[0] == "t1" and data[-1] == "new"

    # 非法 body：缺 task / 非对象 / 非 JSON → 400
    assert (await client.post("/task/history", json={})).status == 400
    assert (await client.post("/task/history", json={"task": "  "})).status == 400
    assert (await client.post("/task/history", json=[1])).status == 400
    assert (await client.post("/task/history", data="not json")).status == 400


@pytest.mark.asyncio
async def test_task_list_running_then_done(client, monkeypatch):
	"""T2 H（M2）：/task/list 透出 task 文本、phase（running→done）、success、saved。"""
	hold = asyncio.Event()
	# #164 后 success 取 agent 真实终态（末步 done 结果）——成功用例须带 done(success=True)
	hist = AgentHistoryList(history=[AgentHistory(
		step_number=1, model_output={"actions": []},
		result=[ActionResult(is_done=True, success=True)])])

	async def slow_run(keep_alive=False):
		await hold.wait()
		return AgentHistoryList()

	monkeypatch.setattr(
		"tree_walker.web.server._build_agent",
		lambda *, task="", model=None: _live_agent(run=slow_run, history=hist))
	resp = await client.post("/task/start", json={"task": "帮我搜索猫"})
	task_id = (await resp.json())["task_id"]

	resp = await client.get("/task/list")
	assert resp.status == 200
	tasks = (await resp.json())["tasks"]
	assert len(tasks) == 1
	assert tasks[0]["task_id"] == task_id
	assert tasks[0]["task"] == "帮我搜索猫"
	assert tasks[0]["phase"] == "running"
	assert tasks[0]["success"] is None

	hold.set()  # 放行 → 任务完成
	await asyncio.sleep(0.05)  # 等 run_live finally 写 final_event

	resp = await client.get("/task/list")
	tasks = (await resp.json())["tasks"]
	assert tasks[0]["phase"] == "done"
	assert tasks[0]["success"] is True


@pytest.mark.asyncio
async def test_task_final_success_reflects_done_result(client, monkeypatch):
	"""issue #164：done(success=False)（max_steps 封顶的诚实上报）→ /task/list 不再误报成功。"""
	hist = AgentHistoryList(history=[AgentHistory(
		step_number=1, model_output={"actions": []},
		result=[ActionResult(is_done=True, success=False, extracted_content="任务部分完成")])])
	monkeypatch.setattr(
		"tree_walker.web.server._build_agent",
		lambda *, task="", model=None: _live_agent(history=hist))
	resp = await client.post("/task/start", json={"task": "x"})
	assert resp.status == 200
	await asyncio.sleep(0.05)  # 等 run_live finally 写 final_event

	resp = await client.get("/task/list")
	tasks = (await resp.json())["tasks"]
	assert tasks[0]["phase"] == "done"
	assert tasks[0]["success"] is False


@pytest.mark.asyncio
async def test_task_final_success_false_when_no_done(client, monkeypatch):
	"""issue #164 语义补充：无 done 结果（如用户中止）→ success False（未完成）。"""
	monkeypatch.setattr(
		"tree_walker.web.server._build_agent",
		lambda *, task="", model=None: _live_agent())  # 空 history，无 done
	resp = await client.post("/task/start", json={"task": "x"})
	assert resp.status == 200
	await asyncio.sleep(0.05)
	resp = await client.get("/task/list")
	tasks = (await resp.json())["tasks"]
	assert tasks[0]["phase"] == "done"
	assert tasks[0]["success"] is False


@pytest.mark.asyncio
async def test_task_list_records_saved_and_paused(client, monkeypatch):
	"""T2 H（M2）：录制落库文件名透出；paused 从 agent.state.paused 推导（mock 无 state → running）。"""
	monkeypatch.setattr(
		"tree_walker.web.server._build_agent", lambda *, task="", model=None: _live_agent())
	resp = await client.post("/task/start", json={"task": "x", "record": True})
	task_id = (await resp.json())["task_id"]
	await asyncio.sleep(0.05)  # 等 run 完成（run 是即返的 mock）

	# done + saved：直接改 final_event 模拟录制落库（不依赖真 save_history 路径）
	from tree_walker.web import server
	server._LIVE_TASKS[task_id].final_event = {"type": "done", "success": True, "saved": "202608151030.json"}
	resp = await client.get("/task/list")
	tasks = (await resp.json())["tasks"]
	assert tasks[0]["saved"] == "202608151030.json"

	# paused：植入带 state.paused 的 handle（仿 skills 测试的 SimpleNamespace 植入模式）
	server._LIVE_TASKS.clear()
	server._LIVE_TASKS["t-paused"] = server.LiveTaskHandle(
		agent=SimpleNamespace(state=SimpleNamespace(paused=True), stop=lambda: None),
		queue=asyncio.Queue(), task_text="暂停中的任务")
	resp = await client.get("/task/list")
	tasks = (await resp.json())["tasks"]
	assert tasks[0]["phase"] == "paused"
	assert tasks[0]["task"] == "暂停中的任务"


@pytest.mark.asyncio
async def test_task_start_clears_finished_handle(client, monkeypatch):
    # 启动新任务时，已结束的 live handle 被清理（不立即 pop、留给下次 start 回收）
    async def quick(keep_alive=False):
        return AgentHistoryList()

    monkeypatch.setattr(
        "tree_walker.web.server._build_agent",
        lambda *, task="", model=None: _live_agent(run=quick))
    from tree_walker.web import server

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
    from tree_walker.web.server import _SseLogHandler
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
        "tree_walker.web.server._build_agent",
        lambda *, task="", model=None: _live_agent(run=logging_run))
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
        "tree_walker.web.server._build_agent",
        lambda *, task="", model=None: _live_agent(run=stepping_run, bus=bus, browser=browser))
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
        "tree_walker.web.server._build_agent",
        lambda *, task="", model=None: _live_agent(run=stepping_run, bus=bus, browser=browser))
    resp = await client.post("/task/start", json={"task": "x"})
    task_id = (await resp.json())["task_id"]

    resp = await client.get("/task/events", params={"task_id": task_id})
    async for _ in resp.content:
        pass
    assert seen.get("target") == (1280, 800)


# ── 直播视口（P6 后续 A）viewport_mode / screencast SSE ────────────────────


@pytest.mark.asyncio
async def test_task_start_livestream_configures_screencast(client, monkeypatch):
    """viewport_mode=livestream → browser.configure_screencast 被调，且 step_end 不采集截图（mode 互斥）。"""
    from tree_walker.observability import EventBus
    from tree_walker.observability.events import StepEndEvent

    bus = EventBus()
    configured = []
    browser = SimpleNamespace(
        configure_screencast=lambda cb, **k: configured.append((cb, k)),
        take_screenshot=AsyncMock(return_value=b"\x89PNG"),
    )

    async def stepping_run(keep_alive=False):
        bus.emit(StepEndEvent(step=1, session_id="t", duration_seconds=0.1,
                              is_done=True, consecutive_failures=0))
        return AgentHistoryList()

    monkeypatch.setattr(
        "tree_walker.web.server._build_agent",
        lambda *, task="", model=None: _live_agent(run=stepping_run, bus=bus, browser=browser))
    resp = await client.post("/task/start", json={"task": "x", "viewport_mode": "livestream"})
    assert resp.status == 200
    task_id = (await resp.json())["task_id"]

    resp = await client.get("/task/events", params={"task_id": task_id})
    async for _ in resp.content:
        pass

    assert len(configured) == 1                       # 配了 screencast sink
    _cb, kw = configured[0]
    assert callable(_cb)                              # 帧回调已注入
    assert kw["format"] == "jpeg"
    assert kw["max_width"] == 1280
    assert kw["every_nth_frame"] == 2
    assert browser.take_screenshot.await_count == 0   # livestream 不走每步截图


@pytest.mark.asyncio
async def test_task_start_screenshots_mode_no_screencast(client, monkeypatch):
    """默认 screenshots 模式 → 不配 screencast，仍每步截图（回归：现状不变）。"""
    from tree_walker.observability import EventBus
    from tree_walker.observability.events import StepEndEvent

    bus = EventBus()
    configured = []
    browser = SimpleNamespace(
        configure_screencast=lambda *a, **k: configured.append(k),
        take_screenshot=AsyncMock(return_value=b"\x89PNG"),
    )

    async def stepping_run(keep_alive=False):
        bus.emit(StepEndEvent(step=1, session_id="t", duration_seconds=0.1,
                              is_done=True, consecutive_failures=0))
        return AgentHistoryList()

    monkeypatch.setattr(
        "tree_walker.web.server._build_agent",
        lambda *, task="", model=None: _live_agent(run=stepping_run, bus=bus, browser=browser))
    resp = await client.post("/task/start", json={"task": "x"})  # 不带 viewport_mode
    task_id = (await resp.json())["task_id"]

    resp = await client.get("/task/events", params={"task_id": task_id})
    async for _ in resp.content:
        pass

    assert configured == []                           # screenshots 不配 screencast
    assert browser.take_screenshot.await_count >= 1   # 仍每步截图（现状不变）


@pytest.mark.asyncio
async def test_task_screencast_streams_latest_frame(client, monkeypatch):
    """/task/screencast 推最新帧；run 期间推入帧槽的帧被消费为 screencast 事件。"""
    from tree_walker.web.server import _LIVE_TASKS

    async def streaming_run(keep_alive=False):
        # 单槽：只有一个 live handle；run 期间往帧槽推一帧（模拟 CDP screencast 回调）
        handle = next(iter(_LIVE_TASKS.values()))
        handle.frame_slot.set({"type": "screencast",
                               "data": "data:image/jpeg;base64,AAA",
                               "width": 1280, "height": 800})
        return AgentHistoryList()

    browser = SimpleNamespace(configure_screencast=lambda *a, **k: None)
    monkeypatch.setattr(
        "tree_walker.web.server._build_agent",
        lambda *, task="", model=None: _live_agent(run=streaming_run, browser=browser))
    resp = await client.post("/task/start", json={"task": "x", "viewport_mode": "livestream"})
    task_id = (await resp.json())["task_id"]

    resp = await client.get("/task/screencast", params={"task_id": task_id})
    assert resp.headers["Content-Type"] == "text/event-stream"
    events = []
    async for raw in resp.content:
        s = raw.decode().strip()
        if s.startswith("event: screencast"):
            events.append(s)
    assert events  # 收到至少一帧


@pytest.mark.asyncio
async def test_task_screencast_404_for_screenshots_task(client, monkeypatch):
    """screenshots 任务 frame_slot 为 None → /task/screencast 404。"""
    monkeypatch.setattr(
        "tree_walker.web.server._build_agent",
        lambda *, task="", model=None: _live_agent())
    resp = await client.post("/task/start", json={"task": "x"})  # screenshots
    task_id = (await resp.json())["task_id"]
    resp = await client.get("/task/events", params={"task_id": task_id})  # 喝完让 run 收尾
    async for _ in resp.content:
        pass
    resp = await client.get("/task/screencast", params={"task_id": task_id})
    assert resp.status == 404
    assert "no livestream" in (await resp.json())["error"]


@pytest.mark.asyncio
async def test_task_screencast_missing_and_unknown_task(client):
    assert (await client.get("/task/screencast")).status == 400               # 缺 task_id
    assert (await client.get("/task/screencast", params={"task_id": "nope"})).status == 404


@pytest.mark.asyncio
async def test_task_events_include_skill_active(client, monkeypatch):
    # P6 后续 I1：SkillActiveEvent 经 "*" 订阅 → SSE 流（前端 chip 数据源）
    from tree_walker.observability import EventBus
    from tree_walker.observability.events import SkillActiveEvent

    bus = EventBus()

    async def run_with_skill(keep_alive=False):
        bus.emit(SkillActiveEvent(step=1, session_id="t", host="member.bilibili.com",
                                  skill_loaded=True, char_count=120))
        return AgentHistoryList()

    monkeypatch.setattr(
        "tree_walker.web.server._build_agent",
        lambda *, task="", model=None: _live_agent(run=run_with_skill, bus=bus))
    resp = await client.post("/task/start", json={"task": "上传视频"})
    task_id = (await resp.json())["task_id"]

    resp = await client.get("/task/events", params={"task_id": task_id})
    data_lines = await _sse_data_lines(resp)
    skill_line = next((l for l in data_lines if '"skill_active"' in l), None)
    assert skill_line is not None
    assert '"host": "member.bilibili.com"' in skill_line
    assert '"skill_loaded": true' in skill_line


# ── Skills 技能面（P6 后续 B）───────────────────────────────────────────────


@pytest_asyncio.fixture
async def skills_client(tmp_path):
    """client + 独立临时 skills_dir（避免读到仓库真 domain-skills）。yield (cli, skills_root)。"""
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    app = await make_app(str(tmp_path / "hist"), str(skills_root))
    cli = TestClient(TestServer(app))
    await cli.start_server()
    yield cli, skills_root
    await cli.close()


@pytest.mark.asyncio
async def test_skills_list_empty(skills_client):
    cli, _ = skills_client
    resp = await cli.get("/skills/list")
    assert resp.status == 200
    assert (await resp.json())["hosts"] == []


@pytest.mark.asyncio
async def test_skills_list_hosts(skills_client):
    cli, root = skills_client
    (root / "member.bilibili.com").mkdir()
    (root / "creator.douyin.com").mkdir()
    (root / "not-a-dir.txt").write_text("x", encoding="utf-8")
    resp = await cli.get("/skills/list")
    assert resp.status == 200
    assert (await resp.json())["hosts"] == ["creator.douyin.com", "member.bilibili.com"]


@pytest.mark.asyncio
async def test_skills_get_reads_three_files(skills_client):
    cli, root = skills_client
    host = root / "member.bilibili.com"
    host.mkdir()
    (host / "_sop.md").write_text("# sop", encoding="utf-8")
    (host / "selectors.md").write_text("| 元素 | 怎么找 |", encoding="utf-8")
    (host / "quirks.md").write_text("1. quirk", encoding="utf-8")
    resp = await cli.get("/skills/get", params={"host": "member.bilibili.com"})
    assert resp.status == 200
    data = await resp.json()
    assert data["host"] == "member.bilibili.com"
    files = data["files"]
    assert files["_sop.md"] == "# sop"
    assert files["selectors.md"] == "| 元素 | 怎么找 |"
    assert files["quirks.md"] == "1. quirk"


@pytest.mark.asyncio
async def test_skills_get_missing_host_returns_empty(skills_client):
    # host 目录不存在 → 200 + 三文件皆空（与 loader「缺失即空」一致，供编辑器新建）
    cli, _ = skills_client
    resp = await cli.get("/skills/get", params={"host": "nope.example.com"})
    assert resp.status == 200
    files = (await resp.json())["files"]
    assert files == {"_sop.md": "", "selectors.md": "", "quirks.md": ""}


@pytest.mark.asyncio
async def test_skills_get_traversal_rejected(skills_client):
    cli, _ = skills_client
    resp = await cli.get("/skills/get", params={"host": ".."})
    assert resp.status == 400


@pytest.mark.asyncio
async def test_skills_get_rejects_nested_host(skills_client):
    # host 含路径分隔 → 400（host 必须单段）
    cli, _ = skills_client
    resp = await cli.get("/skills/get", params={"host": "a/b"})
    assert resp.status == 400


@pytest.mark.asyncio
async def test_skills_put_writes_file(skills_client):
    cli, root = skills_client
    (root / "member.bilibili.com").mkdir()
    resp = await cli.post(
        "/skills/put", params={"host": "member.bilibili.com", "file": "_sop.md"},
        json={"content": "# new sop"})
    assert resp.status == 200
    assert (await resp.json())["ok"] is True
    assert (root / "member.bilibili.com" / "_sop.md").read_text(encoding="utf-8") == "# new sop"


@pytest.mark.asyncio
async def test_skills_put_creates_new_host_dir(skills_client):
    cli, root = skills_client
    resp = await cli.post(
        "/skills/put", params={"host": "new.example.com", "file": "selectors.md"},
        json={"content": "x"})
    assert resp.status == 200
    assert (root / "new.example.com" / "selectors.md").read_text(encoding="utf-8") == "x"


@pytest.mark.asyncio
async def test_skills_put_traversal_rejected(skills_client):
    cli, _ = skills_client
    resp = await cli.post(
        "/skills/put", params={"host": "..", "file": "_sop.md"}, json={"content": "x"})
    assert resp.status == 400


@pytest.mark.asyncio
async def test_skills_put_bad_file_rejected(skills_client):
    cli, _ = skills_client
    resp = await cli.post(
        "/skills/put", params={"host": "h.example.com", "file": "evil.md"},
        json={"content": "x"})
    assert resp.status == 400


@pytest.mark.asyncio
async def test_skills_put_missing_content(skills_client):
    cli, _ = skills_client
    resp = await cli.post(
        "/skills/put", params={"host": "h.example.com", "file": "_sop.md"}, json={})
    assert resp.status == 400


@pytest.mark.asyncio
async def test_skills_put_invalidates_live_loader(skills_client):
    # /skills/put 写盘后，正在跑的 live agent 的 _skill_loader.invalidate(host) 应被调用
    cli, _ = skills_client
    from tree_walker.web import server

    class _FakeLoader:
        def __init__(self):
            self.invalidated = []

        def invalidate(self, host=None):
            self.invalidated.append(host)

    loader = _FakeLoader()
    # 植入一个 live handle（autouse fixture 已清空；agent 带 _skill_loader + stop/task 供 on_shutdown）
    server._LIVE_TASKS["t-running"] = SimpleNamespace(
        agent=SimpleNamespace(_skill_loader=loader, stop=lambda: None), task=None)
    resp = await cli.post(
        "/skills/put", params={"host": "member.bilibili.com", "file": "quirks.md"},
        json={"content": "1. new"})
    assert resp.status == 200
    assert loader.invalidated == ["member.bilibili.com"]


@pytest.mark.asyncio
async def test_skills_put_skips_loader_when_absent(skills_client):
    # live agent 无 _skill_loader 属性（如 mock）→ getattr 防 AttributeError，不崩
    cli, _ = skills_client
    from tree_walker.web import server

    server._LIVE_TASKS["t-bare"] = SimpleNamespace(
        agent=SimpleNamespace(stop=lambda: None), task=None)  # 无 _skill_loader
    resp = await cli.post(
        "/skills/put", params={"host": "h.example.com", "file": "_sop.md"},
        json={"content": "x"})
    assert resp.status == 200

