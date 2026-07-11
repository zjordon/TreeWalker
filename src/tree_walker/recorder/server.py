"""录制后端 HTTP 服务（aiohttp）。

接收 Chrome 扩展的事件，转发给 ``Recorder``。无状态、MV3 友好（SW 按需唤醒，规避
WebSocket 长连接在 SW 休眠时断开的麻烦）。

端点：

- ``POST /start``  开始录制（连浏览器）
- ``POST /event``  接收一条扩展事件 → ``Recorder.handle_event``
- ``POST /stop``   停止录制，落盘（可选补 ``done``）
- ``GET  /health`` 健康检查

请求体均为 JSON；事件线索格式见 ``docs/user_recording/README.md`` §6.2。
"""

from __future__ import annotations

import logging

from aiohttp import web

from tree_walker.recorder.recorder import Recorder

logger = logging.getLogger(__name__)

# aiohttp 3.x 推荐 AppKey（类型安全 + 消除 NotAppKeyWarning）
_RECORDER_KEY = web.AppKey("recorder", Recorder)
_DEFAULT_OUT_KEY = web.AppKey("default_out", str)


async def make_app(recorder: Recorder, default_out: str = "recorded.json") -> web.Application:
	"""构造 aiohttp Application；``recorder`` 存入 ``app["recorder"]`` 供 handler 取用。"""
	app = web.Application()
	app[_RECORDER_KEY] = recorder
	app[_DEFAULT_OUT_KEY] = default_out
	app.router.add_post("/start", _handle_start)
	app.router.add_post("/event", _handle_event)
	app.router.add_post("/stop", _handle_stop)
	app.router.add_get("/health", _handle_health)
	return app


async def _handle_start(request: web.Request) -> web.Response:
	rec: Recorder = request.app[_RECORDER_KEY]
	await rec.start()
	logger.info("/start 录制已开始")
	return web.json_response({"ok": True})


async def _handle_event(request: web.Request) -> web.Response:
	rec: Recorder = request.app[_RECORDER_KEY]
	try:
		event = await request.json()
	except Exception:
		return web.json_response({"ok": False, "error": "invalid json"}, status=400)
	step = await rec.handle_event(event)
	return web.json_response({"ok": step is not None, "step": step.step_number if step else None})


async def _handle_stop(request: web.Request) -> web.Response:
	rec: Recorder = request.app[_RECORDER_KEY]
	try:
		body = await request.json()
	except Exception:
		body = {}
	path = await rec.stop(
		file_path=body.get("file_path") or request.app[_DEFAULT_OUT_KEY],
		mark_done=body.get("mark_done", False),
		done_text=body.get("done_text", ""),
		success=body.get("success", True),
	)
	return web.json_response({"ok": True, "path": str(path), "steps": len(rec.history.history)})


async def _handle_health(request: web.Request) -> web.Response:
	return web.json_response({"ok": True})


def run_server(
	recorder: Recorder,
	host: str = "127.0.0.1",
	port: int = 8765,
	default_out: str = "recorded.json",
) -> None:
	"""阻塞运行 HTTP 服务。``web.run_app`` 接受 ``make_app`` 返回的 coroutine。"""
	web.run_app(make_app(recorder, default_out), host=host, port=port)
