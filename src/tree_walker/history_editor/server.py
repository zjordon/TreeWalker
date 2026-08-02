"""P4 可视化编辑器后端——独立 aiohttp 服务。

操作 ``rerun_history_dir`` 下的 AgentHistoryList JSON，供浏览器 SPA 编辑器调用。
**独立于 ``recorder/server.py``**：录制将迁 TreeForge，而编辑器操作的是重放端资产
（history），故自成一个轻量服务，不 import / 不依赖 ``tree_walker.recorder``。

端点：

- ``GET /history/list``              列出可用历史（rerun_history_dir 下 *.json）
- ``GET /history/load?name=``        加载历史 JSON
- ``POST /history/save?name=``       保存历史 JSON（body: history dict）
- ``GET /history/detect?name=``      返回 detect ∪ manual 变量
- ``POST /history/rerun?name=``      试跑（body: {variables}），调 Agent.load_and_rerun
- ``GET /health``                    健康检查

路径校验复用 ``rerun.resolve_rerun_path``（拒绝绝对路径 / ``..`` 越界）。``/history/rerun``
会真实起浏览器（BrowserSession），需 Chrome 远程调试端口。
"""

from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web

from tree_walker.agent.rerun import resolve_rerun_path
from tree_walker.agent.variable_detector import (
	detect_variables_in_history,
	merge_variable_sources,
)
from tree_walker.agent.views import AgentHistoryList

logger = logging.getLogger(__name__)

_HISTORY_DIR_KEY = web.AppKey("history_dir", str)


async def make_app(history_dir: str = "rerun-history") -> web.Application:
	"""构造编辑器 aiohttp Application。``history_dir`` 由调用方传入（便于测试）。"""
	app = web.Application()
	app[_HISTORY_DIR_KEY] = history_dir
	app.router.add_get("/history/list", _handle_list)
	app.router.add_get("/history/load", _handle_load)
	app.router.add_post("/history/save", _handle_save)
	app.router.add_get("/history/detect", _handle_detect)
	app.router.add_post("/history/rerun", _handle_rerun)
	app.router.add_get("/health", _handle_health)
	# 前端 SPA（构建产物在 _STATIC_DIR）：/assets/* 静态资源 + GET / 入口 + catch-all 回退
	if (_STATIC_DIR / "assets").is_dir():
		app.router.add_static("/assets", str(_STATIC_DIR / "assets"))
	app.router.add_get("/", _serve_index)
	app.router.add_get("/{tail:.*}", _serve_index)  # SPA fallback（最后注册，/history/* 精确优先）
	return app


def _dir(request: web.Request) -> str:
	return request.app[_HISTORY_DIR_KEY]


async def _handle_list(request: web.Request) -> web.Response:
	root = Path(_dir(request))
	files = sorted(p.name for p in root.glob("*.json")) if root.is_dir() else []
	return web.json_response({"files": files})


async def _handle_load(request: web.Request) -> web.Response:
	name = request.query.get("name")
	if not name:
		return web.json_response({"error": "missing name"}, status=400)
	try:
		path = resolve_rerun_path(_dir(request), name)
		history = AgentHistoryList.load_from_file(path)
	except (ValueError, FileNotFoundError) as e:
		return web.json_response({"error": str(e)}, status=400)
	return web.json_response({"history": history.model_dump(mode="json")})


async def _handle_save(request: web.Request) -> web.Response:
	name = request.query.get("name")
	if not name:
		return web.json_response({"error": "missing name"}, status=400)
	try:
		body = await request.json()
	except Exception:
		return web.json_response({"error": "invalid json"}, status=400)
	try:
		path = resolve_rerun_path(_dir(request), name)
		history = AgentHistoryList.load_from_dict(body)
		_assert_pairing(history)  # 防御：前端 bug 致不等长时拒绝
		history.save_to_file(path)
	except Exception as e:  # 越界 / pydantic 校验失败 / 等长断言 / IO
		return web.json_response({"error": str(e)}, status=400)
	return web.json_response({"ok": True, "path": str(path)})


def _assert_pairing(history: AgentHistoryList) -> None:
	"""防御性校验：每步 actions 与 interacted_element 等长（重放定位不变量）。

	前端拖拽/编辑若破坏此不变量，在此拒绝保存。``interacted_element`` 为 None（老格式）
	时跳过——None 在重放端按"无元素线索"处理，不破坏按位对应。
	"""
	for h in history.history:
		actions = (h.model_output or {}).get("actions") or []
		ie = h.interacted_element
		if ie is not None and len(ie) != len(actions):
			raise ValueError(
				f"step {h.step_number}: actions({len(actions)}) 与 interacted_element"
				f"({len(ie)}) 不等长，破坏重放定位不变量"
			)


async def _handle_detect(request: web.Request) -> web.Response:
	name = request.query.get("name")
	if not name:
		return web.json_response({"error": "missing name"}, status=400)
	try:
		path = resolve_rerun_path(_dir(request), name)
		history = AgentHistoryList.load_from_file(path)
	except (ValueError, FileNotFoundError) as e:
		return web.json_response({"error": str(e)}, status=400)
	merged = merge_variable_sources(
		detect_variables_in_history(history), history.manual_variables
	)
	return web.json_response({
		"variables": {k: v.model_dump(mode="json") for k, v in merged.items()},
	})


def _build_agent():
	"""构造试跑用 Agent（真实起浏览器，需 Chrome 远程调试端口）。

	独立函数便于测试 monkeypatch。``load_and_rerun`` 读 ``settings.agent.rerun_history_dir``
	（与 list/load 一致，除非 ``--history-dir`` 覆盖默认）。
	"""
	from tree_walker import Agent, BrowserSession, LLMClient
	from tree_walker.config import load_settings

	settings = load_settings()
	if not settings.browser.ws_url:
		raise RuntimeError("Chrome 未以 --remote-debugging-port 启动（settings.browser.ws_url 为空）")
	llm = LLMClient(settings.llm)
	browser = BrowserSession(settings.browser)
	return Agent(task="", llm=llm, browser=browser, settings=settings.agent)


async def _handle_rerun(request: web.Request) -> web.Response:
	"""试跑：调 Agent.load_and_rerun(name, variables)，返回每步 ActionResult。

	真实起浏览器（BrowserSession）；变量替换走 detect ∪ manual（load_and_rerun 内部）。
	"""
	name = request.query.get("name")
	if not name:
		return web.json_response({"error": "missing name"}, status=400)
	try:
		body = await request.json()
	except Exception:
		body = {}
	try:
		agent = _build_agent()
		results = await agent.load_and_rerun(name, variables=body.get("variables") or None)
		return web.json_response({"results": [r.model_dump(mode="json") for r in results]})
	except Exception as e:
		return web.json_response({"error": str(e)}, status=400)


async def _handle_health(request: web.Request) -> web.Response:
	return web.json_response({"ok": True})


_STATIC_DIR = Path(__file__).parent / "static"


async def _serve_index(request: web.Request) -> web.Response:
	"""托管前端 SPA 入口（构建产物；未构建时提示）。

	SPA fallback：未知路径（非 /history/*、非 /assets/*）回 index.html，让前端路由接管。
	"""
	path = _STATIC_DIR / "index.html"
	if not path.exists():
		return web.json_response(
			{"error": "前端未构建。跑 scripts/build_editor.ps1（prod）或 cd history_editor_ui && npm run dev（dev）"},
			status=404,
		)
	return web.FileResponse(path)


def run_server(
	host: str = "127.0.0.1",
	port: int = 8766,
	history_dir: str | None = None,
) -> None:
	"""阻塞运行编辑器 HTTP 服务。默认端口 8766（避开录制后端 8765）。"""
	if history_dir is None:
		from tree_walker.config import load_settings

		try:
			history_dir = load_settings().agent.rerun_history_dir
		except Exception:
			history_dir = "rerun-history"
	logger.info("编辑器后端 history_dir=%s, http://%s:%s", history_dir, host, port)
	web.run_app(make_app(history_dir), host=host, port=port)
