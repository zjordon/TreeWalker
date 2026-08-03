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

import asyncio
import csv
import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiohttp import web

from tree_walker.agent.rerun import resolve_rerun_path
from tree_walker.agent.variable_detector import (
	detect_variables_in_history,
	merge_variable_sources,
)
from tree_walker.agent.views import AgentHistoryList

logger = logging.getLogger(__name__)

_HISTORY_DIR_KEY = web.AppKey("history_dir", str)


@dataclass
class BatchTaskHandle:
	"""运行中的 CSV 批量重放任务句柄（issue #155）。单进程 web.run_app，模块级 dict 存。"""

	agent: Any                       # Agent 实例（cancel 调 agent.stop()）
	queue: asyncio.Queue             # 进度事件（worker put / SSE handler get）
	total_rows: int
	csv_path: Path                   # 上传的 CSV 临时文件（任务结束删除）
	task: asyncio.Task | None = None           # create_task 后回填
	final_event: dict | None = None            # 任务结束存最终事件（SSE 重连补发）


# task_id → handle。单进程单线程 asyncio，无需锁。单并发防同 Chrome 多 BrowserSession 抢 target。
_BATCH_TASKS: dict[str, BatchTaskHandle] = {}
_MAX_CONCURRENT_BATCH = 1


async def make_app(history_dir: str = "rerun-history") -> web.Application:
	"""构造编辑器 aiohttp Application。``history_dir`` 由调用方传入（便于测试）。"""
	app = web.Application(client_max_size=10 * 1024 * 1024)  # 默认 1MiB 不够 CSV 上传（issue #155）
	app[_HISTORY_DIR_KEY] = history_dir
	app.router.add_get("/history/list", _handle_list)
	app.router.add_get("/history/load", _handle_load)
	app.router.add_post("/history/save", _handle_save)
	app.router.add_get("/history/detect", _handle_detect)
	app.router.add_post("/history/rerun", _handle_rerun)
	# CSV 批量重放（issue #155）：必须在 catch-all 之前注册，否则被 SPA fallback 吞
	app.router.add_post("/history/batch/start", _handle_batch_start)
	app.router.add_get("/history/batch/progress", _handle_batch_progress)
	app.router.add_post("/history/batch/cancel", _handle_batch_cancel)
	app.router.add_get("/health", _handle_health)
	app.on_shutdown.append(_on_batch_shutdown)  # 进程退出时停所有批量任务、关浏览器
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


def _sse_event(event_type: str, data: dict) -> bytes:
	"""格式化一条 SSE 消息（`event: <type>\\ndata: <json>\\n\\n`）。"""
	payload = json.dumps(data, ensure_ascii=False)
	return f"event: {event_type}\ndata: {payload}\n\n".encode("utf-8")


async def _handle_batch_start(request: web.Request) -> web.Response:
	"""启动 CSV 批量重放：multipart 上传 CSV → 落盘 → 起 task → 返 {task_id, total_rows}。

	单并发（`_MAX_CONCURRENT_BATCH`）：同 Chrome 多 BrowserSession 抢 CDP target。
	"""
	name = request.query.get("name")
	if not name:
		return web.json_response({"error": "missing name"}, status=400)
	try:
		resolve_rerun_path(_dir(request), name)  # 越界校验
	except ValueError as e:
		return web.json_response({"error": str(e)}, status=400)

	# 清已结束 handle（防 dict 无限增长）+ 单并发检查
	for old_id, h in list(_BATCH_TASKS.items()):
		if h.final_event is not None:
			_BATCH_TASKS.pop(old_id, None)
	active = [h for h in _BATCH_TASKS.values() if h.final_event is None]
	if len(active) >= _MAX_CONCURRENT_BATCH:
		return web.json_response(
			{"error": "已有批量任务在运行，请先等待完成或中止"}, status=409)

	# 读 multipart 的 file part
	try:
		reader = await request.multipart()
		csv_part = None
		while True:
			part = await reader.next()
			if part is None:
				break
			if part.name == "file":
				csv_part = part
				break
	except Exception:
		return web.json_response({"error": "invalid multipart"}, status=400)
	if csv_part is None:
		return web.json_response({"error": "missing CSV file part 'file'"}, status=400)

	# 落盘到 rerun_history_dir 下唯一名（复用 resolve_rerun_path 校验）
	csv_name = f"batch_{uuid.uuid4().hex[:8]}.csv"
	try:
		csv_path = resolve_rerun_path(_dir(request), csv_name)
		with open(csv_path, "wb") as f:
			while True:
				chunk = await csv_part.read_chunk()
				if not chunk:
					break
				f.write(chunk)
	except Exception as e:
		return web.json_response({"error": f"CSV 写入失败: {e}"}, status=500)

	# 计数 + 空校验
	try:
		with open(csv_path, newline="", encoding="utf-8") as f:
			total_rows = sum(1 for _ in csv.DictReader(f))
	except Exception as e:
		csv_path.unlink(missing_ok=True)
		return web.json_response({"error": f"CSV 解析失败: {e}"}, status=400)
	if total_rows == 0:
		csv_path.unlink(missing_ok=True)
		return web.json_response({"error": "CSV 无数据行（只有表头或空）"}, status=400)

	# 建 agent + handle + 起 task（先存 dict 再 create_task，消除 cancel 竞态）
	try:
		agent = _build_agent()
	except Exception as e:
		csv_path.unlink(missing_ok=True)
		return web.json_response({"error": str(e)}, status=400)

	task_id = uuid.uuid4().hex[:8]
	queue: asyncio.Queue = asyncio.Queue()
	handle = BatchTaskHandle(
		agent=agent, queue=queue, total_rows=total_rows, csv_path=csv_path)
	_BATCH_TASKS[task_id] = handle

	async def run_batch() -> None:
		async def on_row(result):
			await queue.put({"type": "row", **result.model_dump(mode="json")})

		async def on_step(step_index, total, step_results):
			last = step_results[-1] if step_results else None
			await queue.put({
				"type": "step", "step_index": step_index, "total": total,
				"success": not any(r.error for r in step_results),
				"extracted_content": last.extracted_content if last else None,
				"error": next((r.error for r in step_results if r.error), None),
			})
		try:
			results = await agent.batch_rerun(name, csv_path, on_row=on_row, on_step=on_step)
			succeeded = sum(1 for r in results if r.success)
			final = {"type": "done", "total": len(results),
			         "succeeded": succeeded, "failed": len(results) - succeeded}
		except Exception as e:  # batch 整体崩溃（行级异常已在 batch_rerun 内兜住）
			logger.exception("批量任务 %s 崩溃", task_id)
			final = {"type": "done", "total": 0, "succeeded": 0,
			         "failed": 0, "error": str(e)}
		finally:
			handle.final_event = final
			await queue.put(final)
			csv_path.unlink(missing_ok=True)
			# 不立即 pop：保留 handle 供 SSE 重连补发 final_event；由下次 start 清理或 on_shutdown 回收。

	handle.task = asyncio.create_task(run_batch())
	return web.json_response({"task_id": task_id, "total_rows": total_rows})


async def _handle_batch_progress(request: web.Request) -> web.StreamResponse:
	"""SSE 行级进度流。EventSource 只支持 GET → 此端点必须 GET（issue #155）。"""
	task_id = request.query.get("task_id")
	if not task_id:
		return web.json_response({"error": "missing task_id"}, status=400)
	handle = _BATCH_TASKS.get(task_id)
	if handle is None:
		return web.json_response({"error": "unknown task_id"}, status=404)

	resp = web.StreamResponse(status=200, headers={
		"Content-Type": "text/event-stream",
		"Cache-Control": "no-cache",
		"Connection": "keep-alive",
		"X-Accel-Buffering": "no",  # 防代理缓冲（prod 同源无 nginx，加上无害）
	})
	await resp.prepare(request)
	try:
		# 任务在客户端连入前就完成且队列已空 → 补发最终事件
		if handle.final_event is not None and handle.queue.empty():
			await resp.write(_sse_event("done", handle.final_event))
			return resp
		while True:
			try:
				event = await asyncio.wait_for(handle.queue.get(), timeout=15.0)
			except asyncio.TimeoutError:
				await resp.write(b": keepalive\n\n")  # SSE 注释，客户端忽略；防空闲断连
				continue
			await resp.write(_sse_event(event["type"], event))
			if event["type"] == "done":
				break
	except (ConnectionResetError, asyncio.CancelledError):
		pass  # 客户端断开：任务继续（SSE 与任务解耦），handler 退出
	finally:
		try:
			await resp.write_eof()
		except Exception:
			pass
	return resp


async def _handle_batch_cancel(request: web.Request) -> web.Response:
	"""协作式中止：agent.stop() 设 state.stopped=True，重放循环在行/步边界退出（issue #155）。"""
	task_id = request.query.get("task_id")
	if not task_id:
		return web.json_response({"error": "missing task_id"}, status=400)
	handle = _BATCH_TASKS.get(task_id)
	if handle is None:
		return web.json_response({"error": "unknown task_id"}, status=404)
	handle.agent.stop()  # 协作式（非 task.cancel，避免丢 finally 的 browser.stop）
	return web.json_response({"ok": True})


async def _on_batch_shutdown(app: web.Application) -> None:
	"""进程退出（SIGINT/SIGTERM）：停所有批量任务、关浏览器、超时强 cancel。"""
	for h in list(_BATCH_TASKS.values()):
		h.agent.stop()
	tasks = [h.task for h in _BATCH_TASKS.values() if h.task and not h.task.done()]
	if tasks:
		try:
			await asyncio.wait_for(
				asyncio.gather(*tasks, return_exceptions=True), timeout=10.0)
		except asyncio.TimeoutError:
			for h in _BATCH_TASKS.values():
				if h.task and not h.task.done():
					h.task.cancel()
			await asyncio.gather(
				*[h.task for h in _BATCH_TASKS.values() if h.task],
				return_exceptions=True)
	_BATCH_TASKS.clear()


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
