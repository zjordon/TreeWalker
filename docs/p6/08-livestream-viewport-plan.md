# P6 后续实施计划：直播视口（A — `<BrowserView mode='livestream'>`）

> 状态：**已交付**（2026-08-14）。M1（BrowserSession screencast 原语 + 最新帧槽）+ M2（`/task/start` viewport_mode + `/task/screencast` SSE + 收尾）+ M3（前端 BrowserView 双 mode + RunView toggle/双订阅）+ **M4 真机 e2e 全绿**（runbook [`09`](09-livestream-viewport-e2e-runbook.md) 场景 H/I/J/K，用户 2026-08-14 确认）。全量 Python 2306 / 前端 vitest 62 过。⚠️ 真机暴露的三个问题与两轮投递方案迭代（v1 合并→v2 节流窗）见复盘 [`10-livestream-viewport-retrospective.md`](10-livestream-viewport-retrospective.md)——**可见性（startScreencast 只采可见 tab）与投递节奏（`_SC_DELIVERY_MIN_INTERVAL`）是本文写作时未预见的硬约束**。
> 范围：按 [`05`](05-followup-plan.md) §1.A / §5 建议切入点，把运行视图从「每步一帧截图流」升级为 **CDP `Page.startScreencast` 连续推流**，前端 `BrowserView mode` 由占位切到可用。
> 前置：本期建立在 P6 首期（`02`/`04`）已交付的 web live 控制台 + 已实施的 I3 元素高亮标注层（`06`）之上。**不改 shell 注册表结构**、**不改 EventBus 事件 schema**、**不改 SSE 桥接骨架**。
> 关联：issue #162 / [`05`](05-followup-plan.md) §1.A / [`04`](04-implementation-retrospective.md) §三·1（截图 race 教训）。

---

## 0. 范围回顾与前置事实

- **现状**：RunView 用截图流——后端 `step_end` 时 `asyncio.create_task(_capture_screenshot)`（web 层采集，每步一帧，`server.py:608-615`），前端 `liveReducer` 的 `screenshot` 分支收帧（`liveReducer.ts:69-72`），`BrowserView mode='screenshots'` 渲染最新帧（`BrowserView.tsx:20-25`）。`mode='livestream'` 仅占位文本（`BrowserView.tsx:26-28`）。
- **目标**：新增连续推流通道（~1-3 fps 可配），**走 agent/browser 侧 CDP screencast**（04 复盘明确：浏览器生命周期相关的采集优先 agent 侧，避开 web 层 fire-and-forget 与浏览器停机的 race）。
- **I3 高亮层复用**：`06` 已落地的元素高亮（`highlights: Highlight[]`，归一化 bbox ∈ [0,1]）**与 mode 无关**（`BrowserView.tsx:2` 注释 + `02` §3.4 已设计为留缝）——直播帧上直接复用，零返工。

---

## 1. 关键事实核实（CDP screencast 能力）★

调研 `cdp_use`（本项目的 CDP 客户端，`session.py:19` import）后确认 **screencast 全套 API 现成可用**，无需升级依赖：

| 能力 | 位置 | 说明 |
|---|---|---|
| `Page.startScreencast` | `cdp_use/cdp/page/library.py:766-779` | `await self.client.send.Page.startScreencast(params, session_id=...)`；`params` 透传 `send_raw`（`:774`），按 CDP 规范传 `{format, quality, maxWidth, maxHeight, everyNthFrame}`（TypedDict 不强校验） |
| `Page.stopScreencast` | `cdp_use/cdp/page/library.py:843-856` | `await self.client.send.Page.stopScreencast(None, session_id=...)` |
| `Page.screencastFrameAck` | `cdp_use/cdp/page/library.py:538-551` | 每帧 ack 维持连续流；`params={sessionId:<int>}` |
| `screencastFrame` 事件回调 | `cdp_use/cdp/page/registration.py:424-437` | `self.client.register.Page.screencastFrame(callback)`；callback 收 `(event, session_id)` |
| `screencastVisibilityChanged` 事件 | `registration.py:439-452` | 页面可见性变化（可选监听） |
| `ScreencastFrameEvent` 载荷 | `cdp_use/cdp/page/events.py:300-305` | `{data: str(base64), metadata: ScreencastFrameMetadata, sessionId: int}` |
| `ScreencastFrameMetadata` | `cdp_use/cdp/page/types.py:390-406` | `{offsetTop, pageScaleFactor, deviceWidth, deviceHeight, scrollOffsetX, scrollOffsetY, timestamp}` |

**两个直接可套用的既有模式（无需发明）**：
- **「发 CDP 命令」**：`take_screenshot`（`session.py:1813-1865`，`send.Page.captureScreenshot(params, session_id=self.current_session_id)`@1854）——`start/stopScreencast` 同形。
- **「注册 CDP 事件回调」**：`_setup_download_tracking`（`session.py:1596-1645`，`send.Browser.setDownloadBehavior`@1616 + `register.Browser.downloadWillBegin`@1638 / `downloadProgress`@1639）——screencast 注册同形。

**两个必须正视的约束**：
1. **回调线程模型**：cdp_use 的 CDP 事件回调在 **websocket 读线程**触发（`session.py:1656` 注释明示「回调在 websocket 读线程触发」；`record_event` 因此用锁@1671）。→ screencast 帧回调里**不能直接 `await`/直接操作 asyncio 对象**，必须 `loop.call_soon_threadsafe` 移交 loop 线程（§3.2）。
2. **单回调覆盖式注册**：cdp_use registry 对同一 method 只保留一个回调（`session.py:1653-1655` 注释「`registry._handlers[method]` 覆盖式，不能双注册」）。`Page.screencastFrame` 当前无人注册 → 本期独占，无冲突；但**不可与他处重复注册**。

**生命周期落点（已核实）**：
- `agent.run` 在 step 循环前先 `await self.browser.start(...)`（`agent.py:246`）→ `BrowserSession.start()`（`session.py:1351-1370`）里 `_connect()`@1360 后装配 download/event tracking（1361-1370）。**这是「会话就绪」的天然 hook**：在此处自动起 screencast（§3.1）。
- 收尾：`agent.run(keep_alive=True)` 不关浏览器（`04` 问题 1 修复），由 `run_live` finally 调 `browser.stop()`（`server.py:646-651`）；`BrowserSession.stop()` 在 `session.py:1683`。**`stop_screencast` 挂在 `stop()`**（§3.5）。

---

## 2. 架构总览

```
┌──────────────────────────────────────────────────────────────────┐
│ 前端 SPA（web_ui）                                                │
│  RunView：.bar 加 [📡直播 / 📷截图] mode 切换（默认 screenshots）   │
│   screenshots 模式：现有 /task/events 单流（含 screenshot 事件）   │
│   livestream 模式：/task/events（步骤/日志/控制）                  │
│                 + /task/screencast（连续帧，独立 EventSource）     │
│  <BrowserView>：双 mode 共用渲染（<img> + I3 高亮层），mode 仅徽标 │
└───────────────┬──────────────────────────────────────────────────┘
                │ REST + SSE（同源 / Vite proxy）
┌───────────────▼──────────────────────────────────────────────────┐
│ 后端 aiohttp（web/server.py）                          │
│  /task/start 增 viewport_mode（screenshots|livestream）：          │
│    livestream → 给 browser 设 screencast sink（run 前设，会话就绪  │
│      自动 startScreencast）；并【门控】on_step_end 不再调度截图采集 │
│    screenshots → 现状不变（_capture_screenshot 每步一帧）          │
│  新增 GET /task/screencast（SSE）：只推【最新帧】（_LatestFrameSlot）│
│  run_live finally / _on_batch_shutdown：先 stop_screencast 再 stop │
└───────────────┬──────────────────────────────────────────────────┘
                │ CDP（browser 侧自动起停，agent 零改动）
┌───────────────▼──────────────────────────────────────────────────┐
│ BrowserSession（browser/session.py）—— 新增两个方法 + 一个 hook    │
│  start_screencast(on_frame, *, format, quality, max_width,        │
│                    every_nth_frame)：register.screencastFrame +    │
│                    send.Page.startScreencast（仿 take_screenshot） │
│  stop_screencast()：send.Page.stopScreencast（幂等）               │
│  start()@1360 后：若已设 sink → _start_screencast（会话就绪自动起）│
│  stop()@1683：stop_screencast（收尾）                              │
└──────────────────────────────────────────────────────────────────┘
```

**复用要点**：
- 推流帧经独立 SSE 通道 → **现有 `/task/events` SSE handler（`server.py:672-711`）零改动**，e2e 已验证的步骤/日志流零回归风险（践行 `06` 的「零回归」纪律）。
- 前端 `subscribeTaskEvents`（`api.ts:111-125`）不动；新增 `subscribeTaskFrames` 平行函数。
- I3 高亮层、`LiveState.screenshot` 字段、`liveReducer` 的 `screenshot` 分支**全部复用**——livestream 帧也写进 `state.screenshot`，`BrowserView` 渲染路径不变。

---

## 3. 后端设计

> `server.py` / `session.py` 源码用 **TAB 缩进**，编辑时务必对齐（`indentation-tabs-vs-spaces` 记忆）。

### 3.1 BrowserSession：screencast 原语（仿 `take_screenshot` + `_setup_download_tracking`）

在 `BrowserSession`（`session.py`）新增，紧邻 `take_screenshot`（:1813）：

```python
async def start_screencast(
	self, on_frame, *, format: str = "jpeg", quality: int = 60,
	max_width: int | None = None, every_nth_frame: int = 4,
) -> None:
	"""启动 CDP Page.startScreencast 连续推流（直播视口）。

	``on_frame``：每帧回调，签名 (event, cdp_session_id)——event 含
	{data(base64), metadata, sessionId}（cdp_use events.py:300-305）。回调在
	CDP WS 读线程触发（session.py:1656），实现方须用 loop.call_soon_threadsafe
	移交 loop 线程后再操作 asyncio 对象（见 server._on_screencast_frame）。
	幂等：重复调用先 stop 再 start。
	"""
	if self.client is None:
		raise RuntimeError("screencast 需先 start() 连接 CDP")
	await self.stop_screencast()  # 幂等清旧
	self._screencast_on_frame = on_frame
	self.client.register.Page.screencastFrame(on_frame)  # 单回调覆盖式（§1 约束2）
	params: dict = {"format": format, "quality": int(quality)}
	if max_width is not None:
		params["maxWidth"] = int(max_width)  # 只限宽，保宽高比（高亮层百分比依赖）
	params["everyNthFrame"] = int(every_nth_frame)        # 源头限速：默认 ~2-3fps
	await self.client.send.Page.startScreencast(
		params, session_id=self.current_session_id)
	self._screencast_on = True

async def stop_screencast(self) -> None:
	"""停止推流。幂等（未起 / client 已断 → no-op）。"""
	self._screencast_on = False
	if getattr(self, "client", None) is None:
		return
	try:
		await self.client.send.Page.stopScreencast(None, session_id=self.current_session_id)
	except Exception as e:
		logger.debug("stopScreencast no-op/failed: %s", e)  # 收尾路径，失败 warning 以下
```

> - **只设 `maxWidth`、不设 `maxHeight`**：CDP 按比例缩放保宽高比 → 帧与 CSS 视口同宽高比 → I3 归一化 bbox 百分比对齐成立（与 `06` §4.4 截图流同一策略）。两维都设可能改宽高比致高亮错位。
> - **`__init__`** 加 `self._screencast_on = False`、`self._screencast_on_frame = None` 初值（仿其它 `_xxx` 状态字段）。
> - **与 agent 自身的 `captureScreenshot` 不冲突**（独立 CDP 方法，Chrome 可同时跑）。

### 3.2 会话就绪自动起 + 最新帧槽（agent 侧、零 race）

**① 自动起（`BrowserSession.start()`，`session.py:1351-1370`）**：`_connect()`@1360 之后、download/event tracking 同段，加：

```python
# 直播视口：若 run 前已设 sink，会话就绪即起推流（browser 侧自动，零 race）
if self._screencast_sink is not None:
	try:
		await self._start_screencast(self._screencast_sink)
	except Exception as e:
		logger.warning("screencast 启动失败（降级为无直播）: %s", e)
```

并加一个 **run 前设 sink 的 setter**（server 在 `_build_agent` 后、`agent.run` 前调）：

```python
def configure_screencast(self, on_frame, **kw) -> None:
	"""run 前声明「我要直播」——存 sink+参数，真正 startScreencast 延到会话就绪。"""
	self._screencast_sink = (on_frame, kw)
```

> `start()` 里据此 `(on_frame, kw)` 调 `start_screencast(on_frame, **kw)`。这样**起推流发生在 browser 必活时**（`_connect` 刚成功），彻底规避 `04` 问题 1 那类「采集跑输 browser.stop」的 race。

**② 最新帧槽 `_LatestFrameSlot`**（server.py 新增小类，保证「只留最新帧、不堆积、不让帧挤占步骤事件）：

```python
class _LatestFrameSlot:
	"""单槽最新帧缓冲：覆盖式存最新帧 + asyncio.Event 唤醒消费者。
	线程安全靠调用方在 loop 线程操作（set 经 call_soon_threadsafe 进 loop）。"""
	def __init__(self):
		self._frame = None
		self._evt = asyncio.Event()
	def set(self, frame: dict) -> None:     # loop 线程调
		self._frame = frame
		self._evt.set()
	async def wait(self) -> None:           # SSE 消费者 await
		await self._evt.wait()
	def take(self) -> dict | None:           # 取走并清（只返最新一帧）
		f, self._frame = self._frame, None
		self._evt.clear()
		return f
```

**③ 跨线程入队回调**（`_handle_task_start` 里定义，捕获 loop）：

```python
loop = asyncio.get_running_loop()
def _on_frame(event, _cdp_sid, _loop=loop, _slot=handle.frame_slot, _client_ref=agent.browser):
	# CDP WS 读线程触发 → call_soon_threadsafe 移交 loop 线程
	data = event.get("data")
	meta = event.get("metadata") or {}
	_loop.call_soon_threadsafe(_slot.set, {
		"type": "screencast",
		"data": f"data:image/jpeg;base64,{data}" if data else None,
		"width": meta.get("deviceWidth"), "height": meta.get("deviceHeight"),
		"scale": meta.get("pageScaleFactor"),
	})
	# ack 维持连续流（每帧一个，丢到 loop 线程发，不在 WS 线程 await）
	sid = event.get("sessionId")
	if sid is not None:
		_loop.call_soon_threadsafe(
			lambda: asyncio.ensure_future(
				_client_ref.client.send.Page.screencastFrameAck(
					{"sessionId": sid}, session_id=_client_ref.current_session_id)))
```

> - `data` 走 base64 data URL（与截图流 `server.py:546` 同形，前端 `<img>` 直吃）。
> - **`sessionId` 命名陷阱**：帧事件里的 `sessionId`（int）是**screencast 会话 id**（给 ack 用），与 CDP 调用的 `session_id=`（**target** 会话 id，`current_session_id`）是两回事——别混（§7 风险）。

### 3.3 `/task/start`：`viewport_mode` + sink 注入 + 截图门控

`_handle_task_start`（`server.py:569-669`）改三处：

**① 读 mode**（`:577-580` 附近）：
```python
vp_mode = body.get("viewport_mode", "screenshots") if isinstance(body, dict) else "screenshots"
if vp_mode not in ("screenshots", "livestream"):
	vp_mode = "screenshots"
```

**② livestream 注入 sink**（`agent = _build_agent(...)`@592 之后）：
```python
if vp_mode == "livestream":
	# run 前声明直播 sink；真正 startScreencast 延到 browser 会话就绪（§3.2①）
	agent.browser.configure_screencast(_on_frame, format="jpeg", quality=60,
	                                   max_width=1280, every_nth_frame=4)
```
> `_on_frame`、`loop`、`handle.frame_slot` 须在注入前就绪（见 §3.2③；`LiveTaskHandle` 加 `frame_slot` 字段，§3.4）。

**③ 截图采集门控**（`on_step_end`@608-615）：
```python
def on_step_end(event) -> None:
	if vp_mode != "screenshots":   # 直播模式不让每步截图采集与推流争带宽/重叠
		return
	logger.debug("screenshot: scheduled step=%d", event.step)
	t = asyncio.create_task(_capture_screenshot(agent, event.step, queue))
	handle.capture_tasks.add(t); t.add_done_callback(handle.capture_tasks.discard)
```

> **mode 互斥**：screenshots 走原每步采集；livestream 走连续推流。二选一，**不共存**（§8 决策 2）。默认 `screenshots` 保现状、保 e2e 回归。

### 3.4 新增 `GET /task/screencast`（最新帧 SSE，独立通道）

**`LiveTaskHandle` 加字段**（`server.py:67-77`）：
```python
frame_slot: Any = None   # _LatestFrameSlot（livestream 模式才有；screenshots 为 None）
```

**端点**（`make_app`@84-115 的 `/task/*` 块加一条，**catch-all 之前**）：
```python
app.router.add_get("/task/screencast", _handle_task_screencast)
```

**handler**（仿 `_handle_task_events`@672-711 的 StreamResponse 套路，但只消费最新帧）：
```python
async def _handle_task_screencast(request: web.Request) -> web.StreamResponse:
	task_id = request.query.get("task_id")
	handle = _LIVE_TASKS.get(task_id)
	if handle is None or handle.frame_slot is None:
		return web.json_response({"error": "no livestream for this task"}, status=404)
	resp = web.StreamResponse(status=200, headers={
		"Content-Type": "text/event-stream", "Cache-Control": "no-cache",
		"Connection": "keep-alive", "X-Accel-Buffering": "no",
	})
	await resp.prepare(request)
	try:
		while True:
			try:
				await asyncio.wait_for(handle.frame_slot.wait(), timeout=15.0)
			except asyncio.TimeoutError:
				await resp.write(b": keepalive\n\n")   # 防空闲断连
				# 任务已结束且无新帧 → 收尾
				if handle.final_event is not None:
					break
				continue
			frame = handle.frame_slot.take()
			if frame is not None:
				payload = {k: v for k, v in frame.items() if k != "type"}
				await resp.write(_sse_event("screencast", payload))
			if handle.final_event is not None and handle.frame_slot._frame is None:
				break   # done 且帧已flush
	except (ConnectionResetError, asyncio.CancelledError):
		pass   # 前端关页/切走：任务继续（SSE 与任务解耦，同 /task/events）
	finally:
		try: await resp.write_eof()
		except Exception: pass
	return resp
```

> - **只推最新帧**：`wait()` 唤醒 → `take()` 取最新（中间帧被覆盖丢弃）→ 慢消费者绝不堆积、绝不挤占步骤流。
> - **前端关流即停推带宽**：EventSource 关闭 → handler 走 `ConnectionResetError` 退出（RunView 不可见时关流，§4.2；§8 决策 3 的「前端不可见不推」由前端关 EventSource 实现）。
> - `_sse_event`（`server.py:308`）复用。

### 3.5 生命周期收尾：`stop_screencast` 先于 `browser.stop`

**① `run_live` finally**（`server.py:634-666`）：在 drain 截图采集（:636-644）之后、`browser.stop()`（:646-651）**之前**加：
```python
# 直播模式：先停推流再关浏览器（顺序与截图 drain 同理：CDP 会话还活着才能干净 stop）
_ss = getattr(getattr(agent, "browser", None), "stop_screencast", None)
if _ss is not None:
	try: await _ss()
	except Exception as e: logger.warning("stop_screencast 失败: %s", e)
```

**② `_on_batch_shutdown`**（`server.py:473-492`）：进程退出对 live handle 调 `agent.stop()` 后，browser 的 `stop()`（:1683）会兜底 `stop_screencast`（§3.1 的 `stop()` 内置）——进程级退出无需额外改，但 `BrowserSession.stop()`@1683 须调一次 `stop_screencast`（幂等，见下）。

**③ `BrowserSession.stop()`@1683**：开头加幂等停推流（双保险：即便 server 没显式调，关浏览器也停）：
```python
try:
	if getattr(self, "_screencast_on", False):
		await self.stop_screencast()
except Exception as e:
	logger.debug("stop() 内 stop_screencast no-op: %s", e)
```

---

## 4. 前端设计

> web_ui 源码用 **TAB 缩进**，编辑时务必对齐。

### 4.1 `BrowserView`：双 mode 共用渲染，mode 降为徽标

现 `BrowserView.tsx:20-28` 按 mode 分叉（livestream 走占位）。**改为两 mode 共用 `<img>` 渲染**——因为 screenshots 与 livestream 都喂 `state.screenshot`（同一 data URL 字段），渲染路径本就一致：

```tsx
{mode === "livestream" && (
	<span className="mode-badge live">● 直播</span>   // 仅徽标差异
)}
{screenshot ? (
	<img src={screenshot} alt="browser" />
) : (
	<div className="muted browser-placeholder">
		{mode === "livestream" ? "等待直播帧…" : "等待截图…"}
	</div>
)}
{screenshot && highlights.map((h) => (   /* I3 高亮层——两 mode 共用，零改动 */
	<div key={h.index} className="hl-box" style={{left: pct(h.bbox.left), /*…*/}}>
		<span className="hl-label">{h.index}</span>
	</div>
))}
```

> `mode` 保留为 prop（徽标 + 占位文案 + RunView 据此决定订阅源），**不再决定渲染结构**。高亮层逻辑（`:29-43`）原样复用。

### 4.2 `RunView`：mode 切换 toggle + 双订阅 + 不可见关流

**① mode 本地 state + toggle**（`RunView.tsx`，`useReducer` 旁加或并进 `FIELD`）：
```tsx
const [vpMode, setVpMode] = useState<"screenshots" | "livestream">("screenshots");
// .bar 内：
<select value={vpMode} onChange={(e) => setVpMode(e.target.value as any)} disabled={running}>
	<option value="screenshots">📷 截图（每步）</option>
	<option value="livestream">📡 直播（连续）</option>
</select>
```
> mode 仅在「新任务」时生效（`onStart` 传给 `/task/start`）；**运行中切换 = 下个任务生效**（mid-run 切换本期不做，§7）。

**② `startTask` 带 mode**（`onStart`，`RunView.tsx:20-24`）：
```tsx
const { task_id } = await api.startTask(state.task, fps, state.record, vpMode);
```

**③ 帧订阅（仅 livestream）**——新增第二个 EventSource，回调 dispatch 成 `screencast` 事件：
```tsx
useEffect(() => {
	if (!state.taskId || vpMode !== "livestream") return;
	const es = api.subscribeTaskFrames(state.taskId, (frame) =>
		dispatch({ type: "EVENT", event: { type: "screencast", ...frame } }));
	return () => es.close();   // 切走/换 mode/任务结束 → 关流 = 后端停推带宽
}, [state.taskId, vpMode]);
```

**④ `BrowserView` 传 mode**（`RunView.tsx:119`）：
```tsx
<BrowserView mode={vpMode} screenshot={state.screenshot} highlights={state.highlights} />
```

> 既有 SSE 订阅（`RunView.tsx:32-37`）不动——livestream 模式下步骤/日志/控制仍走 `/task/events`，帧走 `/task/screencast`，两流并行。

### 4.3 `liveReducer`：`screencast` 事件复用 `screenshot` 字段

`liveReducer.ts:69-72` 现有 `screenshot` 分支把 `e.data` 存进 `state.screenshot`。**新增 `screencast` 走同一落点**（帧也是 data URL）：
```ts
if (e.type === "screenshot" || e.type === "screencast") {
	const data = e.data as string | undefined;
	return { ...state, screenshot: data ?? state.screenshot };
}
```
> 零新字段、零 `LiveState` 改动（`types.ts:124` 的 `screenshot` 复用）。`RESET`/`STARTING` 已清 `screenshot`（`liveReducer.ts:42`），livestream 连跑也正确清帧。

### 4.4 `api.ts`：`subscribeTaskFrames` + `startTask` 增参

**`startTask`**（`api.ts:92-104`）加可选第四参：
```ts
export async function startTask(
	task: string, filePaths?: string[], record?: boolean,
	viewportMode?: "screenshots" | "livestream",
): Promise<{ task_id: string }> {
	const r = await fetch(`${TASK}/start`, {
		method: "POST", headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ task, file_paths: filePaths, record, viewport_mode: viewportMode }),
	});
	/* … */
}
```

**新增 `subscribeTaskFrames`**（平行于 `subscribeTaskEvents`@111-125）：
```ts
export function subscribeTaskFrames(taskId: string, onFrame: (f: { data: string }) => void): EventSource {
	const es = new EventSource(`${TASK}/screencast?task_id=${encodeURIComponent(taskId)}`);
	es.addEventListener("screencast", (ev: MessageEvent) => onFrame(JSON.parse(ev.data)));
	// 不监听 onerror（断连自动重连，同 batch/task 模式）
	return es;
}
```
> `TASK_EVENT_TYPES`（`api.ts:106-109`）**不加 `screencast`**——帧走独立 EventSource，不进主事件流（避免主 `subscribeTaskEvents` 的 listener 收到帧）。

---

## 5. 分阶段交付

| 里程碑 | 内容 | 验收 |
|---|---|---|
| **M1** BrowserSession screencast 原语 + 最新帧槽 | `start_screencast/stop_screencast/configure_screencast`（session.py）+ `start()`@1360 自动起 + `stop()`@1683 收尾 + `_LatestFrameSlot`（server.py） | 单测：mock `client.send.Page.*` + `register.Page.screencastFrame`，断言 startScreencast 传参（maxWidth/everyNthFrame）、幂等 stop、`_on_frame` 经 call_soon_threadsafe 设槽 + ack；`_LatestFrameSlot` 覆盖只留最新 |
| **M2** server 接线 | `/task/start` 增 `viewport_mode` + sink 注入 + 截图门控；`/task/screencast` SSE 端点；`LiveTaskHandle.frame_slot`；run_live finally + shutdown 先 stop_screencast | 单测：仿 `test_web_server.py`，mock agent/browser——livestream 模式断言 `configure_screencast` 被调、`on_step_end` 不调度截图；screenshots 模式断言**现状不变（回归）**；SSE handler 只推最新帧、keepalive、客户端断开不崩 |
| **M3** 前端 | `BrowserView` 双 mode 共渲染 + 徽标；`RunView` mode toggle + 双订阅 + 不可见关流；`liveReducer` screencast 复用 screenshot；`api.ts` `subscribeTaskFrames` + `startTask` 增参 | vitest：reducer `screencast`→screenshot；`subscribeTaskFrames` mock EventSource；`BrowserView` 两 mode 均渲染 img+高亮；`RunView` mode 切换/关流 |
| **M4** 真机 e2e + 回归 | 抖音/B站真跑 livestream 任务验收 | 直播帧连续刷新（~2-3fps）、I3 高亮框对齐元素、暂停/停止干净停推流；**screenshots 模式 e2e 零回归**（场景 A/B/C 仍绿） |

> **并行/先 de-risk**：**M1 先行**（CDP screencast + 跨线程是本期唯一新机制/最大未知，纯 mock 可单测，仿 `06` 把 LLMClient 风险点前置的思路）；M2 依赖 M1；M3 可在 M2 端点定形后并行。

---

## 6. 测试策略

- **后端**（`uv run python -m pytest tests/ -x -v`，覆盖率 >85%）：
  - `BrowserSession.start_screencast/stop_screencast`：monkeypatch 假 `CDPClient`（`send.Page.startScreencast/stopScreencast/screencastFrameAck` + `register.Page.screencastFrame` 记录回调），断言：①传参含 `maxWidth`/`everyNthFrame`/`format=jpeg`；②重复 start 先 stop；③stop 幂等（未起不抛）；④`configure_screencast` + 模拟 `start()` 后自动调 startScreencast；⑤`stop()` 内置停推流。
  - `_LatestFrameSlot`：连续 `set` 三次 → `take` 只返第三次（覆盖）；`wait` 被 `set` 唤醒、`take` 后 `Event` 清。
  - `_on_frame` 跨线程：monkeypatch `loop.call_soon_threadsafe`，断言回调里**不直接触 asyncio 对象**、ack 用 `call_soon_threadsafe`+`ensure_future` 安排。
  - `_handle_task_start`：livestream → `configure_screencast` 被调 + `on_step_end` 早返（不调度截图）；screenshots → **现有截图调度路径不变（回归断言）**。
  - `_handle_task_screencast`：mock `frame_slot`，断言只推最新帧、keepalive 写出、`ConnectionResetError` 不抛。
- **前端**（vitest）：
  - `liveReducer.test.ts` 加 `screencast` case（→ `screenshot` 更新）+ screenshots 回归 case。
  - `BrowserView` 两 mode 均渲染 `<img>` + 高亮框（mock screenshot + highlights）。
  - `RunView` mode toggle、livestream 下 `subscribeTaskFrames` 起第二个 EventSource、卸载时 `close()`。
  - `api.test`：`startTask` 透传 `viewport_mode`；`subscribeTaskFrames` 监听 `screencast`。
- **真机 e2e**：抖音/B站各一，livestream 模式看连续帧 + 高亮对齐 + 暂停/停止；screenshots 模式重跑 `03` 场景 A/B/C 零回归。

---

## 7. 风险与对策

| 风险 | 对策 |
|---|---|
| **CDP 回调线程模型**（WS 读线程触发，session.py:1656）→ 直接操作 asyncio 对象竞态 | `_on_frame` 用 `loop.call_soon_threadsafe` 移交 loop 线程；ack 用 `ensure_future` 安排；实现期在 `_on_frame` 里只做「捕获 + call_soon_threadsafe」，零 asyncio 直接触碰 |
| **`sessionId` 双义混淆**（帧事件 `sessionId`=screencast 会话 id 给 ack；CDP `session_id=`=target 会话 id） | ack 用 `event["sessionId"]`，CDP 调用统一用 `current_session_id`；注释标明；单测分别断言两者取值不同 |
| **帧洪水挤占步骤/日志流** | 独立 `/task/screencast` 通道 + `_LatestFrameSlot` 只留最新帧——帧与步骤事件物理隔离、慢消费者不堆积；源头 `everyNthFrame` 限速 |
| **`startScreencast` 与 agent `captureScreenshot` 抢资源/抖动** | 独立 CDP 方法、Chrome 可并行；mode 互斥（livestream 时关掉每步截图采集，§3.3③）减少同帧两路采 |
| **agent 切 tab / 新 target 后画面停**（screencast 绑当前 target） | MVP 接受：画面停在末帧；后续低优先监听 target 切换（`current_target_id` 变化，step.py:294/980/1059）重启 screencast（记为 future，不本期） |
| **livestream 高亮框在连续帧上「停留」**（tool_call 设框、step_start 清，期间帧持续刷新→框看似钉住） | MVP 接受（与 screenshots 模式同语义）；后续可加「框 N 帧后淡出」或按帧 URL 一致性过滤 |
| **mid-run 切 mode** | 不支持：mode 在 `/task/start` 时定，切换须新任务（避免运行中改 CDP 推流状态机）；UI toggle `disabled={running}` |
| **`maxWidth`/两维限尺改宽高比致高亮错位** | 只设 `maxWidth`（CDP 保比缩放）；不设 `maxHeight`；单测断言 params 无 `maxHeight` |
| **Pillow 缺失致 jpeg 质量参数无效** | `format=jpeg` 由 Chrome 端编码（不依赖 Pillow）；前端降采样无（帧已源头限尺）。与截图流的 `resize_screenshot_bytes` 路径解耦 |
| **浏览器停机 race 复现**（04 问题 1 同类） | 推流起于 `start()` 会话就绪（browser 必活）、停于 `run_live` finally 早于 `browser.stop()`（§3.5）——agent 侧生命周期，规避 fire-and-forget race |

---

## 8. 已定决策（建议方案，2026-08-13）

1. **推流通道**：**独立 `GET /task/screencast` SSE + `_LatestFrameSlot` 最新帧**。理由：物理隔离帧与步骤/日志流，现有 `/task/events`（e2e 已验证）零改动零回归；慢消费者不堆积。（备选：复用 `/task/events` 同队 + 帧合并——更省一个连接，但改 SSE 主循环有回归风险，不作首选。）
2. **与截图流关系**：**mode 互斥**（`viewport_mode` 二选一，默认 `screenshots` 保现状），**不共存**。理由：避免两路采集争带宽/同帧重叠；前端 `BrowserView mode` 已为此留缝（`02` §3.4）。
3. **起停时机**：**browser 侧自动**——run 前设 sink（`configure_screencast`），`BrowserSession.start()` 会话就绪自动 `startScreencast`，`stop()` 收尾。理由：践行 `04` §三·1「采集优先 agent 侧、browser 必活时采集」，零 race。
4. **帧率/带宽**：**源头 `everyNthFrame`（默认 ≈2-3fps）+ 只设 `maxWidth=1280` + 最新帧覆盖 + 前端不可见关 EventSource**。理由：源头限速最省；最新帧槽杜绝堆积；关流即停推。
5. **高亮层**：**复用 I3 归一化 bbox**（帧与视口同宽高比 → 百分比对齐），两 mode 共用渲染。已知局限（连续帧框停留）MVP 接受。
6. **ack**：**每帧 `screencastFrameAck`**（经 `call_soon_threadsafe`+`ensure_future` 在 loop 线程发），维持连续流。

> 以上均为建议方案；如某条想另选（如推流复用 `/task/events` 同队、或允许 livestream 与截图共存），在实施前提出即可调整。本期 **mid-run 切 mode、多 target 跟推、框淡出** 均不 做（记 future）。
