# 方案：history_editor 批量重放 UI（CSV 上传 + SSE 行级进度 + 协作式中止）

> 关联 issue：[#155](https://github.com/zjordon/TreeWalker/issues/155)
> 背景命令：`uv run python examples/csv_rerun.py <history.json> <variables.csv>`（现有 CLI 批量重放）
> 关联文档：`docs/rerun_history/04-重放执行器.md`、`05-变量检测与替换.md`、`09-重放文件根目录与路径校验.md`；后端批量方法 `Agent.batch_rerun`（`src/tree_walker/agent/rerun.py:302`）。

## 背景（为什么做这件事）

`history_editor_ui` 当前只能**单次试跑**一个回放文件：`POST /history/rerun`（`src/tree_walker/history_editor/server.py:147`）同步阻塞调 `Agent.load_and_rerun`，跑完一次性返回 `list[ActionResult]`，前端 `RunPanel` 事后展示。批量场景（同一流程跑多组变量）在 Web UI 里**完全用不上**——尽管后端能力其实已就绪：

- `Agent.batch_rerun(history_file, csv_path) -> list[BatchRowResult]`（`rerun.py:302-352`）**已实现**：CSV 列头=变量名（detect ∪ manual 并集），每行一组变量，逐行串行调 `load_and_rerun`，单行异常不中断、记入该行 `error`。返回 `BatchRowResult`（`src/tree_walker/agent/views.py:327-335`：`row_index/variables/success/n_steps/extracted_content/error`）。
- `VariablePanel.exportCsv`（`history_editor_ui/src/components/VariablePanel.tsx:13-22`）已导出**变量名表头 CSV 模板**——与 `batch_rerun` 列头约定天然对接（用户填值即可）。

即 #149 的「CSV 批量重放」只落地到后端方法 + CLI（`examples/csv_rerun.py`），**未接到 Web UI**。#155 要把它接上，并补齐两个 Web 端缺失的能力：

1. **实时进度**：`batch_rerun`/`rerun_history` 都是**阻塞返回 `list`**、无进度回调。批量 N 行 = N 次起浏览器，同步 HTTP 会挂很久（浏览器/vite proxy 超时风险、体验差）。→ SSE 是必需而非锦上添花。
2. **可中止**：后端无任务句柄、无取消信号、`server.py` 无 cancel 端点。→ 需 task 句柄 + 协作式取消。

**Scope（已与用户确认）**：一步到位 SSE（非先 MVP 同步）；进度粒度=**行级**（每行开始/完成推送，非步级）；CSV=**multipart 上传**；cancel=**协作式 `agent.stop()`**（非 `task.cancel()`）；回放文件**复用 `state.loadedName`**（Toolbar 已加载的那个，不另放下拉）。

> 缩进：项目用 **tab**，所有改动须与周边代码一致（CLAUDE.md）。包管理用 `uv`，跑测试用 `uv run python -m pytest tests/ -x -v`；前端在 `history_editor_ui/` 下用 `npx vitest run` / `npx tsc --noEmit` / `npm run build`。

---

## 方案总览

1. **后端 `rerun.py`**：`rerun_history` finally 加 `state.stopped` 守卫（取消时跳过 summary、立即关浏览器）；`batch_rerun` 加可选 `on_row` 回调 + 行级 `state.stopped` 检查（向后兼容，CLI 零回归）。
2. **后端 `server.py`**：新增 3 端点（start multipart / progress SSE / cancel）+ 模块级 task 句柄存储（单并发）+ `on_shutdown` 清理 + `client_max_size` 调大。
3. **前端**：新增 `BatchRunPanel` 组件（挂底部）+ reducer `batch` 状态机 + `api.ts` 三函数（multipart 启动 / EventSource 订阅 / cancel）。
4. **测试**：后端（rerun 取消即时性 + 端点 + SSE）、前端（reducer batch action + 组件）。

---

## 改动点

### 1. `src/tree_walker/agent/rerun.py` — finally 守卫（取消即时性，最关键）

`rerun_history` finally（`rerun.py:454-457`）当前无条件 `await self._generate_rerun_summary(...)`，而 `_generate_rerun_summary` 内部用 `asyncio.wait_for(..., timeout=120)` 调 LLM（`rerun.py:1538/1545`）。取消（`state.stopped=True`）→ 步循环 `rerun.py:414 break` → finally 先跑 summary → **卡 ~120s** 才到 `browser.stop()`。

改 `rerun.py:454-457`：

```python
finally:
    if not self.state.stopped:                      # 新增守卫
        summary = await self._generate_rerun_summary(self.task, results, summary_llm)
        results.append(summary)
    else:                                           # 取消快速路径
        results.append(ActionResult(is_done=True, success=False,
                                    extracted_content="Rerun cancelled by user."))
    await self.browser.stop()
```

正常路径（`state.stopped=False`）行为完全不变；步级取消点 `rerun.py:414` 已存在，无需新增。该改动顺带改善 CLI Ctrl+C 体验（原本也卡 120s）。

### 2. `rerun.py` — `batch_rerun` 加流式 + 可取消（`rerun.py:302-352`）

- 顶部 typing import 补 `Callable, Awaitable`（`rerun.py:22` 已 import `Any` 等）。
- 签名加可选参数 `on_row: Callable[[BatchRowResult], Awaitable[None]] | None = None`。
- 行循环（`rerun.py:328`）顶部加 `if self.state.stopped: break`；每行 `results.append(result)` 后 `if on_row is not None: await on_row(result)`，再查一次 `if self.state.stopped: break`（覆盖取消信号在行执行中到达）。
- `except Exception`（`rerun.py:345`）不动——不捕获 `CancelledError`（协作式取消不依赖它，靠 `state.stopped` 检查点）。
- **向后兼容**：CLI `examples/csv_rerun.py` 不传 `on_row`，CLI 场景无人调 `agent.stop()` 故 `state.stopped` 恒 False → 两个检查点 no-op，行为零变化；现有 `tests/test_batch_rerun.py` 全部不传 `on_row`，不受影响。

`on_row` 签名：`Callable[[BatchRowResult], Awaitable[None]]`——参数为刚完成的行结果，`Awaitable[None]` 便于队列满时自然背压（当前用无界 `asyncio.Queue`，`put` 立即返回但语义安全）。

### 3. `src/tree_walker/history_editor/server.py` — 3 端点 + task 存储

**模块级**（紧跟 `_HISTORY_DIR_KEY` `server.py:36`）：

```python
@dataclass
class BatchTaskHandle:
    agent: Any            # Agent 实例（cancel 调 agent.stop()）
    queue: asyncio.Queue  # 进度事件队列（worker put / SSE handler get）
    total_rows: int
    csv_path: Path        # 上传的 CSV 临时文件（完成后删）
    task: asyncio.Task | None = None     # create_task 后回填
    final_event: dict | None = None      # 任务结束存最终事件（支持 SSE 重连补发）

_BATCH_TASKS: dict[str, BatchTaskHandle] = {}   # 单进程 web.run_app，单线程 asyncio，无需锁
```

**端点**（注册在 catch-all `server.py:53` **之前**，否则被 SPA fallback 吞——`test_history_list_not_intercepted_by_catchall` `tests/test_history_editor_server.py:130-134` 已验证模式）：

- **`POST /history/batch/start?name=`**：multipart 读 `file` part → `resolve_rerun_path`（`rerun.py:187`，拒绝对路径/`..`越界）校验 `name` → 写 `rerun_history_dir/batch_<uuid8>.csv` → `csv.DictReader` 计数（0 行→400）→ `_build_agent()`（`server.py:130`）→ 建 handle **先存 `_BATCH_TASKS` 再 `asyncio.create_task`**（消除 cancel 在 task 已起但 handle 未存时到达的竞态）→ 返 `{task_id, total_rows}`。**单并发**：已有运行中任务（`final_event is None`）→ 409（每任务独立 Agent+BrowserSession 连同一 Chrome CDP，多任务并发抢 target，本地单用户工具限 1）。
- **`GET /history/batch/progress?task_id=`**：`web.StreamResponse(content_type="text/event-stream", Cache-Control:no-cache, X-Accel-Buffering:no)` → `prepare` → 循环 `await asyncio.wait_for(handle.queue.get(), 15)`（超时发 `: keepalive\n\n` SSE 注释防代理空闲断连）→ `write(event:row/done)` → done 后 `break`+`write_eof`。客户端断开（`ConnectionResetError`/`CancelledError`）handler 退出但**任务继续**（SSE 与任务解耦，用户可刷新重连）；`handle.final_event is not None and queue.empty()` 时直接补发最终事件。unknown task_id → 404。
- **`POST /history/batch/cancel?task_id=`**：`handle.agent.stop()`（协作式，设 `state.stopped=True`），返 `{ok:true}`。仿 recorder `POST /stop`（`src/tree_walker/recorder/server.py:40,77` 直接调对象方法）。unknown → 404。

**`make_app`**（`server.py:39`）：`web.Application(client_max_size=10*1024*1024)`（默认 1MiB，CSV 可能超）；`app.on_shutdown.append(...)`——遍历 `_BATCH_TASKS` 调 `agent.stop()` + `await asyncio.wait_for(gather(*tasks, return_exceptions=True), 10)` + 超时则 `task.cancel()`（`web.run_app` `server.py:202` 收 SIGINT/SIGTERM 自动调 on_shutdown）。

**worker `run_batch`**（`create_task` 的协程）：

```python
async def on_row(result):
    await queue.put({"type": "row", **result.model_dump(mode="json")})
try:
    results = await agent.batch_rerun(name, csv_path, on_row=on_row)
    final = {"type": "done", "total": len(results),
             "succeeded": sum(r.success for r in results),
             "failed": len(results) - sum(r.success for r in results)}
except Exception as e:
    final = {"type": "done", "total": 0, "succeeded": 0, "failed": 0, "error": str(e)}
finally:
    handle.final_event = final
    await queue.put(final)
    csv_path.unlink(missing_ok=True)     # 防临时文件泄漏
    _BATCH_TASKS.pop(task_id, None)
```

### SSE 消息契约（前后端约定）

```
event: row
data: {"row_index":0,"variables":{"email":"a@b.com"},"success":true,"n_steps":5,"extracted_content":"...","error":null}

event: done
data: {"total":10,"succeeded":8,"failed":2}        # 异常: {"total":0,"succeeded":0,"failed":0,"error":"..."}
```

每条以 `\n\n` 结尾。**不用 `event: error`**——EventSource 原生 `onerror` 也叫 error（无 data，= 连接断开），与服务端错误（有 data）冲突，前端无法可靠区分。服务端错误统一走 `event: done` + `error` 字段，彻底消除歧义。

### 4. 前端 `history_editor_ui/src/`

- **`types.ts`**：`BatchRowProgress`（镜像后端 `BatchRowResult`）+ `BatchState`（`phase: idle|starting|running|done|cancelled|error`, `taskId`, `totalRows`, `rows: BatchRowProgress[]`, `error`）；`EditorState` 加 `batch: BatchState`（`types.ts:54-62`）。
- **`reducer.ts`**：`initialState.batch`（`reducer.ts:8-16`）+ action 联合（`reducer.ts:18-36`）追加 `BATCH_START`（清 rows）/`BATCH_STARTED`（taskId+totalRows）/`BATCH_ROW`（**追加 rows = 进度核心**）/`BATCH_DONE`（带 error→phase error）/`BATCH_CANCEL`/`BATCH_ERROR`/`BATCH_RESET`；`LOAD`（`reducer.ts:40-49`）一并重置 batch（换文件不留旧批量结果）。
- **`api.ts`**：
    - `startBatch(name, file)`：`new FormData()` + `fd.append("file", file)` + POST `?name=`（**不设 Content-Type**，浏览器自动加 `multipart/form-data; boundary=...`，手设会丢 boundary）。
    - `cancelBatch(taskId)`：POST `?task_id=`。
    - `subscribeBatchProgress(taskId, onRow, onDone)`：`new EventSource`（**EventSource 只支持 GET → progress 端点必须 GET**）监听 `row`/`done`，done 后 `es.close()`；**不监听 `onerror`**（原生 error = 断连自动重连，服务端错误走 done）。
- **`components/BatchRunPanel.tsx`**（新）：`<input type=file accept=.csv>` + 开始/中止/重置按钮 + `{rows.length}/{totalRows} 行（N 成功）` 进度 + 复用 `ul.var-list`（`App.css:64`）渲染每行（`✓/✗ 行N (n步)` + error/extracted_content 截断，仿 `RunPanel.tsx:18-26`）。开始按钮 `disabled={!file || !state.loadedName || running}`。
- **`App.tsx`**：挂 `<BatchRunPanel/>` 于 `App.tsx:77` `<RunPanel/>` 之后；`onStartBatch`/`onCancelBatch`/`onResetBatch` useCallback（仿 `onRun` `App.tsx:48-58`）+ `useEffect([state.batch.taskId])` 订阅 EventSource（cleanup `es.close()`；**依赖只写 taskId**，避免每行 row 事件触发重渲染重建连接）。

### 复用点

- 回放文件：`state.loadedName`（Toolbar 已加载，用户已定不复用下拉）。
- CSV 模板：`VariablePanel.exportCsv`（`VariablePanel.tsx:13-22`）。
- 样式：`.panel/.bar/button.error/ul.var-list/.status/.muted/.error-text`（`App.css`）—— 无需新 CSS。
- 取消：`Agent.stop()`（`src/tree_walker/agent/agent.py:296-298`）。
- 测试 mock：`_build_agent` monkeypatch（`test_history_editor_server.py:138-148`）、`SimpleNamespace` 调 `RerunMixin.batch_rerun`（`test_batch_rerun.py:28-30`）、create_task+cancel（`test_recorder.py:363-371`）。

---

## 测试

- **后端 `rerun.py`**（扩 `tests/test_batch_rerun.py` + 新建 rerun_history 取消测试）：`on_row` 每行触发收到 `BatchRowResult`；`state.stopped=True` 中断行循环后续行不跑；finally 守卫取消时 `_generate_rerun_summary` 未调 + `browser.stop()` 调到 + 返回末尾是 "cancelled" ActionResult。
- **后端 `server.py`**（扩 `tests/test_history_editor_server.py`）：start multipart 上传成功+`total_rows`、缺 name/file/越界/空 CSV→400、单并发→409、SSE 读到 `row`+`done`、cancel 后 summary 未调且响应 <2s、unknown task_id→404。
- **前端**（`history_editor_ui/src/__tests__/`）：reducer batch action（`BATCH_ROW` 追加、`BATCH_DONE` 带 error→error phase）；`BatchRunPanel` 按钮 disabled/开始回调（仿 `ActionList.test.tsx`）。

---

## 验证（端到端）

1. 后端：`uv run python -m pytest tests/test_batch_rerun.py tests/test_history_editor_server.py -x -v`
2. 前端：`history_editor_ui` 下 `npx vitest run` + `npx tsc --noEmit` + `npm run build`
3. 真机 e2e：起 history_editor server（8766）+ `npm run dev`（5173 proxy `/history`→8766）→ 加载一个 rerun JSON → `exportCsv` 导模板填几行 → BatchRunPanel 上传 → 看 SSE 行级进度逐行出现 → 中途中止（浏览器应**秒关**，不卡 120s）→ 已完成行结果保留。
4. prod：`npm run build` → aiohttp `history_editor/static` 同源托管（`server.py:49-53`）→ 同样验证（无 proxy，SSE 直连）。

---

## 关键风险

- finally summary 卡 120s → 改动点 1 守卫解决。
- catch-all 吞端点 → catch-all 前注册（已有测试验证）。
- EventSource 只 GET → progress 端点用 GET，start/cancel 用 POST。
- FormData 别设 Content-Type → 浏览器加 boundary。
- `client_max_size` 默认 1MiB → 调 10MB。
- 协作式取消（`agent.stop`）不用 `task.cancel`（`CancelledError` 不被行循环 `except Exception` 捕获会透出，靠 finally 兜 `browser.stop`）。
- 单并发（`_MAX_CONCURRENT_BATCH=1`）防同 Chrome 多 target 竞争。
- CSV 临时文件 `finally: unlink` 防泄漏。
- 先存 dict 再 create_task，消除 cancel 竞态。

---

## 改动顺序（依赖驱动）

1. `rerun.py` finally 守卫（3 行，独立可测）。
2. `rerun.py` `batch_rerun` 加 `on_row` + 行级 `state.stopped`。
3. `rerun.py` 测试。
4. `server.py` task 存储 + `on_shutdown` + `client_max_size`。
5. `server.py` 3 端点 + 注册。
6. `server.py` 测试。
7. 前端 types/reducer/api/BatchRunPanel/App。
8. 前端测试。
9. 真机联调。
