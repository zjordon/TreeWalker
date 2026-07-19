# 03-阶段3：networkidle 等待（默认关）+ 清理录制端 upload 硬编码 wait

> **关联**：#126（本期实现 issue）｜ #123（等待机制完善总览）｜ 前置 #124（阶段1）/ #125（阶段2，已落地）｜ 后续 #127（阶段4）
> **状态**：待实施
> **体裁**：带代码的实施计划（基于现状代码，含"现状 → 改后"代码块、逐文件改动清单、测试与验证）
> **对照源**：已逐行核验真实代码（2026-07-19，master @ `e15b4a3`）
> **范围**：缺口 2（可选 networkidle 等待，默认关）+ 缺口 6（清理录制端 `upload_file` 硬编码 wait 注入，改为重放端可配置）。两者正交、可独立开关、可分拆合并（缺口 6 可独立先合）。
> **总原则**：复用 cdp_use 既有 `Network` 域 + `_wait_for_page_settle` 的 §8 轮询骨架；新开关默认关 → 零行为变更；超时永远降级，永不引入新失败；录制端注入移除前必须验证重放端等价覆盖（不破坏抖音封面上传案例）。

---

## Context（为什么做）

阶段 2 让重放获得了"等目标元素 + visible/enabled actionability"。但两类残留时序盲区：

1. **缺口 2 — 没有 networkidle 等待**：`_wait_for_page_settle`（`session.py:1786-1817`）只轮询 `document.readyState`，SPA 常年 `complete` → 对 XHR/Fetch 驱动的页面变化零感知。典型失败：点"加载更多" → 后台发 AJAX → 重放立即 `get_state` → DOM 还没更新 → 下一步定位失败。`rerun_wait_for_elements` / `actionability` 都依赖 `get_state` 拿到变化后的 DOM，但它们自己不等 AJAX 完成。阶段 1 文档"边界与风险"已明示 readyState 在 SPA 收益小，阶段 3 正是 SPA 时序的兜底解药。

2. **缺口 6 — 录制端硬编码 upload wait**：`recorder.py:243-255` 在每个 `upload_file` 后注入一条 `ActionRecord(action_name="wait", seconds=N)`（视频 5s/图片 3s）。这是"录制时决策、写死秒数、不可配置"的反模式：① 5s/3s 是经验值，慢网/大文件不够、快网小图浪费；② 录制语义被污染（"用户做了 3 个动作"落盘成 4 个）；③ 与阶段 1-3 的"等待可配置 + 时机由重放端决定"哲学冲突。

参考 `knowledge-garden/ai/agent/browser-wait-and-timing.md`（**注：位于 sibling 仓库 `D:\dev\git\z_jordon\knowledge-garden\`，非 TreeWalker 内**）：§6 networkidle = pending 请求数归零 + 500ms 无新请求；长连接（WebSocket/SSE/long polling）必须过滤或用"pending 稳定"启发式；部署默认关，元素等待是默认。§8：所有条件等待的本质 = `deadline + poll + 乐观早退 + 悲观降级`，0.3s poll 合理。**复用 `_wait_for_page_settle` 已实现的 §8 骨架**，不新造轮子。

> ⚠️ 参考文档**未提** Playwright networkidle 语义、`Network.responseReceived`、`Page.lifecycleEvent`——本期引用范围以 §6/§8 为准，不外延。

---

## 现状精确锚点（已核验真实代码 @ `e15b4a3`）

### 锚点 A：CDP foundation（cdp_use，零库工作）

`cdp_use/cdp/registry.py:14-34` — `EventRegistry.register` 是**单回调覆盖式**（`self._handlers[method] = callback`，后写胜出，非列表追加）：

```python
# registry.py（精简）
def register(self, method, callback):
    self._handlers[method] = callback        # last-wins，非 append
```

回调签名统一 `(event: dict, session_id: str | None = None)`；分发支持同步或 async（`inspect.isawaitable` 判定）。

**Network 域全部 API 就绪**（无任何库改造）：
- `client.send.Network.enable(params, session_id=...)` —— `EnableParameters` 是 `TypedDict(total=False)`，`{}` 合法。
- `client.register.Network.requestWillBeSent(cb)` / `loadingFinished(cb)` / `loadingFailed(cb)` / `responseReceived(cb)` / `dataReceived(cb)`。

**事件字段关键事实**（`cdp_use/cdp/network/events.py`，决定长连接过滤的依据）：

| 事件 | `requestId` | `type: ResourceType` |
|---|---|---|
| `RequestWillBeSentEvent` | 必填 | **`NotRequired`** —— 不能在此过滤 |
| `ResponseReceivedEvent` | 必填 | **必填** —— 分类依据 |
| `LoadingFailedEvent` | 必填 | **必填** |
| `LoadingFinishedEvent` | 必填 | 无 type 字段 |

→ **长连接过滤必须以 `ResponseReceived.type` / `LoadingFailed.type` 为准**，不能在 `requestWillBeSent` 时过滤（type 可能缺失）。

**仓库现状**：全仓库 grep `Network.enable` / `network_idle` / `networkidle`（src 内）→ 0 命中。Greenfield。既有 register 调用 4 处（method 名不同，单回调覆盖式下零冲突）：`Page.fileChooserOpened`（session.py:1300）、`Browser.downloadWillBegin`/`downloadProgress`（1452-1453）、`Page.javascriptDialogOpening`（1482）。CDPClient 构造 3 处（session.py:1182/1212/1395）后都跟一次 `_connect()`，故 Network.enable + 注册放 `_connect` 内最自然。

### 锚点 B：Hook 点 + `get_state` 现状 `session.py:1528-1540` / `rerun.py:497-510`

```python
# session.py:1528-1540（get_state；阶段1 已加 wait_settle）
async def get_state(self, include_screenshot: bool = True, wait_settle: bool = False) -> BrowserStateSummary:
    if wait_settle:
        try:
            await self._wait_for_page_settle()
        except Exception as e:
            logger.warning("Pre-get_state wait_settle failed: %s", e)
    sid = self.current_session_id
    ...
```

```python
# rerun.py:497-510（_execute_history_step 头；networkidle 注入点）
async def _execute_history_step(
    self, item, delay, ai_step_llm, wait_for_elements, wait_for_page_settle,
    rerun_actionability_check,
) -> list[ActionResult]:
    await asyncio.sleep(delay)
    state = await self.browser.get_state(
        include_screenshot=False, wait_settle=wait_for_page_settle
    )
    if wait_for_elements:
        state = await self._wait_for_target_elements(state, item, timeout=15.0)
    ...
```

### 锚点 C：`_wait_for_page_settle` §8 模板 `session.py:1786-1817`（networkidle wait 直接镜像此结构）

```python
async def _wait_for_page_settle(self, timeout=None, poll_interval=None) -> None:
    if self.client is None or self.current_session_id is None:
        return
    timeout = timeout if timeout is not None else self._settings.page_settle_timeout
    poll_interval = poll_interval if poll_interval is not None else self._settings.page_settle_poll_interval
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = await self.client.send.Runtime.evaluate(
                {"expression": "document.readyState", "returnByValue": True},
                session_id=self.current_session_id,
            )
            if result.get("result", {}).get("value", "") == "complete":
                return                                  # 乐观早退
        except Exception:
            pass                                        # CDP hiccup，下轮重试
        await asyncio.sleep(poll_interval)
    # 超时静默降级（不抛错）
```

### 锚点 D：配置链路 12 touchpoints（阶段1 范本 `rerun_wait_for_page_settle`，逐点克隆）

| # | 锚点 | 角色 |
|---|---|---|
| 1 | `config.py:129` | AgentSettings 字段 `rerun_wait_for_page_settle: bool = False` |
| 2 | `config.py:354` | `load_settings` env 注入 |
| 3 | `agent.py:83` | `__init__` 拆 `self.rerun_wait_for_page_settle` |
| 4 | `rerun.py:238` | RerunMixin 类型注解 |
| 5 | `rerun.py:307` | `rerun_history` 签名 `wait_for_page_settle: bool \| None = None` |
| 6 | `rerun.py:327-328` | None 哨兵归一化 |
| **7** | **`rerun.py:367-369`** | **`rerun_history` 调 `_rerun_step_with_retries(..., wait_for_page_settle, ...)`** |
| 8 | `rerun.py:416` | `_rerun_step_with_retries` 参数 |
| 9 | `rerun.py:426` | 调 `_execute_history_step` |
| 10 | `rerun.py:442-444` | 调 `_reexecute_menu_opener` |
| 11 | `rerun.py:503` | `_execute_history_step` 参数 |
| 12 | `rerun.py:508-510` | 消费：`get_state(wait_settle=...)` |
| 13 | `rerun.py:965` | `_reexecute_menu_opener` 参数 |
| 14 | `rerun.py:971-972` | 调 `_execute_history_step` |

> 共 12 个独立锚点（#1-#6 + #8-#14 + **#7**）。`BrowserSettings` 调参（`page_settle_timeout`/`poll_interval`）走 `self._settings`，不进 RerunMixin 管线。

### 锚点 E：录制端 upload 注入（缺口 6 清理对象） `recorder.py:50-65, 241-255`

```python
# recorder.py:50-55（常量 + 扩展名集 —— 与 rerun.py:187-188 逐字符相同）
_UPLOAD_WAIT_SECONDS = {"video": 5, "image": 3}
_VIDEO_EXTS = frozenset({"mp4","mov","avi","mkv","webm","flv","wmv","m4v","ts","3gp","mpeg","mpg"})
_IMAGE_EXTS = frozenset({"png","jpg","jpeg","gif","bmp","webp","tif","tiff","svg","heic"})

# recorder.py:58-65
def _file_kind(path: str) -> str | None: ...

# recorder.py:241-255（pending_exc 守卫必须保留；注入块是清理对象）
if pending_exc is not None:
    raise pending_exc                                # ← 保留
if action.action_name == "upload_file":             # ← 整块删
    seconds = _UPLOAD_WAIT_SECONDS.get(_file_kind(action.params.get("path", "")), 3)
    self.recording.actions.append(ActionRecord(action_name="wait", params={"seconds": seconds}, ...))
```

测试锚点：`tests/test_recorder.py:171-202`（视频→5s）、`205-219`（图片→3s）。

### 锚点 F：rerun.py 已有 video/image 扩展名集 `rerun.py:187-188`（**复用，不新造**）

```python
# rerun.py:187-188（_resolve_file_input_by_accept 已用；与 recorder.py:54-55 逐字符相同）
_UPLOAD_VIDEO_EXTS = frozenset({"mp4","mov","avi","mkv","webm","flv","wmv","m4v","ts","3gp","mpeg","mpg"})
_UPLOAD_IMAGE_EXTS = frozenset({"png","jpg","jpeg","gif","bmp","webp","tif","tiff","svg","heic"})
```

→ 重放端 upload wait 的"按类型给秒数"直接复用这两组既存集合，新增一个模块级 `_upload_file_kind(path)` 即可，**零扩展名重复**。recorder.py 的 `_VIDEO_EXTS`/`_IMAGE_EXTS`/`_file_kind` 可整体删除。

### 锚点 G：upload_file 重放侧动作循环 `rerun.py:609-628` + `_verify_upload` 不阻隔下一步

`_verify_upload`（`actions.py:863-913`，`upload_verify_wait_s=1.5`）的轮询全在 `upload_file` 动作**内部**完成；动作返回后动作循环立即推进。**录制端注入的 `wait(5s)` 是 upload 与下一步之间唯一的墙钟间隔**——移除前必须有等价替代，否则回归抖音封面渲染案例（封面 canvas 在 `DOM.setFileInputFiles` 后 0.6-1.5s 才出现，且渲染本身不发 XHR → networkidle 也抓不到）。

---

## 方案

### 共用基础设施 1：`NetworkIdleTracker`（新文件 `src/tree_walker/browser/network_idle.py`）

镜像 `circuit_breaker.py` 风格：小模块、`from __future__ import annotations`、纯 Python 状态机、`time.monotonic()` 计时、自身不做 I/O（所有 CDP 交互由 `BrowserSession` 通过注册回调喂数据）。

```python
# src/tree_walker/browser/network_idle.py（新增，~140 行）
"""CDP Network 域 inflight 请求追踪器（可选 networkidle 等待）。

镜像 _wait_for_page_settle 的 §8 模式（deadline + poll + 乐观早退 + 悲观降级）：
调用方在自己的 deadline 循环里 poll is_idle()；本类只维护 inflight 集合 +
last-activity 时间戳，由 CDP 回调喂数据。

线程安全：cdp_use 回调分发线程模型未明示；threading.Lock 无论单线程(asyncio)
还是独立 ws 读线程都安全（临界区极短，set.add/discard + monotonic，不阻塞 loop）。
"""
from __future__ import annotations
import asyncio, logging, threading, time
from typing import Callable

logger = logging.getLogger(__name__)

# 长连接 ResourceType（CDP 字符串枚举，取自 ResponseReceived.type —— 必填）。
# 这俩会让 pending 永不归零：WebSocket 握手后不收 loadingFinished；EventSource 长流不结束。
# 不含 Fetch/XHR long-poll：type 不可区分，靠严格 deadline 兜底（对齐 Playwright networkidle 已知限制）。
_LONG_CONNECTION_TYPES = frozenset({"WebSocket", "EventSource"})


class NetworkIdleTracker:
    """经 CDP Network 域回调追踪 inflight 请求。

    生命周期：每 BrowserSession 一个；reconnect/switch_tab 时 reset()；
    register(client, session_id) 挂 4 个回调（cdp_use 单回调覆盖式下幂等）。
    """

    def __init__(
        self,
        timeout: float = 5.0,
        stability_window: float = 0.5,
        poll_interval: float = 0.1,
        now_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.timeout = timeout
        self.stability_window = stability_window
        self.poll_interval = poll_interval
        self._now = now_fn
        self._lock = threading.Lock()
        self._inflight: set[str] = set()            # requestId 集合（全类型）
        self._long_conn_ids: set[str] = set()       # 被判长连接的子集
        self._last_activity: float = self._now()
        self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def register(self, client, session_id: str | None) -> None:
        """挂 4 个 CDP 回调。幂等（cdp_use 覆盖式）。每次 _connect/switch_tab 可重调。"""
        try:
            client.register.Network.requestWillBeSent(self._on_request_will_be_sent)
            client.register.Network.responseReceived(self._on_response_received)   # type 分类依据
            client.register.Network.loadingFinished(self._on_loading_finished)
            client.register.Network.loadingFailed(self._on_loading_failed)
            self._enabled = True
        except Exception as e:
            logger.warning("NetworkIdleTracker register failed (degrading to off): %s", e)
            self._enabled = False

    def reset(self) -> None:
        """新会话清旧 inflight 残留（inflight 是 per-session 的）。"""
        with self._lock:
            self._inflight.clear()
            self._long_conn_ids.clear()
            self._last_activity = self._now()

    # ── CDP 回调（可能 ws 读线程）──────────────────────────────
    def _on_request_will_be_sent(self, event: dict, session_id: str | None = None) -> None:
        rid = event.get("requestId")
        if not rid:
            return
        # event['type'] 此处 NotRequired —— 不分类，等 responseReceived 补 type。
        with self._lock:
            self._inflight.add(rid)                 # set.add 幂等：redirect 复用 requestId 不膨胀
            self._last_activity = self._now()

    def _on_response_received(self, event: dict, session_id: str | None = None) -> None:
        rid = event.get("requestId")
        rtype = event.get("type") or ""
        if not rid:
            return
        with self._lock:
            if rtype in _LONG_CONNECTION_TYPES:
                self._long_conn_ids.add(rid)        # 标记长连接，is_idle 时从 pending 剔除
            self._last_activity = self._now()

    def _on_loading_finished(self, event: dict, session_id: str | None = None) -> None:
        self._retire(event.get("requestId"))

    def _on_loading_failed(self, event: dict, session_id: str | None = None) -> None:
        self._retire(event.get("requestId"))        # 失败也算结束（释放 pending）

    def _retire(self, rid: str | None) -> None:
        if not rid:
            return
        with self._lock:
            self._inflight.discard(rid)             # discard 幂等：钳零，永不负
            self._long_conn_ids.discard(rid)
            self._last_activity = self._now()

    # ── Idle 判定 + 等待（asyncio 线程）──────────────────────
    def is_idle(self) -> bool:
        """(inflight − 长连接) 为空 AND 无活动 ≥ stability_window。严格（镜像 Playwright）。"""
        return self._idle_locked(self.stability_window)

    def _idle_locked(self, sw: float) -> bool:
        with self._lock:
            if not self._enabled:
                return True                         # 降级=即时 idle
            if self._inflight - self._long_conn_ids:
                return False
            return (self._now() - self._last_activity) >= sw

    async def wait_until_idle(
        self, timeout: float | None = None,
        stability_window: float | None = None, poll_interval: float | None = None,
    ) -> bool:
        """poll is_idle() 直到 true 或 deadline。返回 True=已空闲，False=超时降级（不抛错）。
        镜像 _wait_for_page_settle 的循环结构。"""
        if not self._enabled:
            return True
        timeout = self.timeout if timeout is None else timeout
        sw = self.stability_window if stability_window is None else stability_window
        poll = self.poll_interval if poll_interval is None else poll_interval
        deadline = self._now() + timeout
        while self._now() < deadline:
            if self._idle_locked(sw):               # 乐观早退（多数页面已静默）
                return True
            await asyncio.sleep(poll)
        return self._idle_locked(sw)                # 末次判定
```

**关键点**：① 回调线程安全（`_lock`）；② `requestId` 去重（redirect 复用，`set.add` 幂等）；③ pending 钳零（`set.discard` 幂等）；④ 长连接过滤以 `responseReceived.type` 为准（`requestWillBeSent.type` 是 NotRequired）；⑤ `is_idle` 要求 `pending_short==0 AND lull≥stability_window`（**both**，非 activity-lull-only——两波请求节奏下 lull 启发式会误判）；⑥ 乐观早退 + 悲观降级；⑦ Network.enable/register 失败 → `_enabled=False` → `is_idle`/`wait_until_idle` 即时返回 True（等同关闭，对齐 `recent_events` setup 失败降级 session.py:1190-1193）。

### 共用基础设施 2：BrowserSession 集成

**网络追踪 always-on（轻量），等待由 `get_state(wait_networkidle=...)` 显式触发（默认关）**——拆分对齐 `page_settle_timeout`（tuning on BrowserSettings）vs `rerun_wait_for_page_settle`（on/off on AgentSettings）。

```python
# session.py __init__ 末尾（紧邻 _recent_events 块，~1117-1167 区域）新增 1 字段
self._network_idle_tracker = NetworkIdleTracker(
    timeout=_settings.network_idle_timeout,
    stability_window=_settings.network_idle_stability_window,
    poll_interval=_settings.network_idle_poll_interval,
)
```

```python
# session.py _connect 内，Page.enable/DOM.enable（1230-1231）紧邻后新增
await self.client.send.Page.enable({}, session_id=self.current_session_id)
await self.client.send.DOM.enable({}, session_id=self.current_session_id)
# 阶段3：启用 Network 域 + 注册空闲追踪回调（always-on；wait 由 get_state 显式触发）。
# 失败 → tracker 降级 disabled，wait_until_idle 即时返回（对齐 recent_events 降级）。
try:
    await self.client.send.Network.enable({}, session_id=self.current_session_id)
    self._network_idle_tracker.register(self.client, self.current_session_id)
except Exception as e:
    logger.warning("Network.enable / tracker register failed (degrading): %s", e)
```

```python
# session.py _connect 入口处（target 发现之前）新增 reset —— 清旧 session inflight
async def _connect(self) -> None:
    self._network_idle_tracker.reset()
    ...
```

> **为何放 `_connect` 而非新 `_setup_network_idle_tracking()`**：① `Network.enable` 是 **per-session** 命令（仿 Page.enable/DOM.enable 在 _connect），而 `_setup_download_tracking` 用 `Browser.setDownloadBehavior`（browser-level，start 时调一次即可）；② `_connect` 覆盖 `start`/`reconnect`/`switch_tab` 三场景一次到位；③ `register` 单回调覆盖式下幂等，重复注册无副作用。

```python
# session.py:1528-1540（get_state 加 wait_networkidle 参数；wait_settle 之后）
async def get_state(
    self, include_screenshot: bool = True, wait_settle: bool = False,
    wait_networkidle: bool = False,            # ← 新增
) -> BrowserStateSummary:
    if wait_settle:
        try:
            await self._wait_for_page_settle()
        except Exception as e:
            logger.warning("Pre-get_state wait_settle failed: %s", e)
    if wait_networkidle:                        # ← 新增块（page_settle 之后）
        try:
            await self._network_idle_tracker.wait_until_idle()
        except Exception as e:
            logger.warning("Pre-get_state wait_networkidle failed: %s", e)
    sid = self.current_session_id
    ...
```

> **顺序决策（page_settle → networkidle）**：readyState 是粗粒度容器信号（页面"加载完"），networkidle 是细粒度数据信号（AJAX"打完"）。先等容器稳定再等数据，避免 readyState 未 complete 时 networkidle 被早早判 idle。两者都乐观早退，常态零等待。

### 12-touchpoint rerun wiring（`wait_for_networkidle`，照锚点 D 逐点克隆）

每处都在 `wait_for_page_settle` 同位加 `wait_for_networkidle`：

```python
# rerun.py:238 后（RerunMixin 注解）
rerun_wait_for_networkidle: bool                 # ← 新增

# rerun.py:307（rerun_history 签名）
wait_for_networkidle: bool | None = None,        # ← 新增

# rerun.py:327-328 后（None 哨兵归一化）
if wait_for_networkidle is None:
    wait_for_networkidle = self.rerun_wait_for_networkidle

# rerun.py:367-369（**易漏点**：rerun_history 调 _rerun_step_with_retries）
step_results = await self._rerun_step_with_retries(
    item, step_delay, max_retries, previous_item, ai_step_llm,
    wait_for_elements, wait_for_page_settle, rerun_actionability_check,
    wait_for_networkidle,                         # ← 新增
)

# rerun.py:416 / 503 / 965（_rerun_step_with_retries / _execute_history_step / _reexecute_menu_opener 参数）
wait_for_networkidle: bool,                       # ← 各加 1 个

# rerun.py:426 / 971-972（调 _execute_history_step）
wait_for_networkidle=wait_for_networkidle,        # ← 透传

# rerun.py:442-444（调 _reexecute_menu_opener）
wait_for_networkidle,                             # ← 透传

# rerun.py:508-510（消费）
state = await self.browser.get_state(
    include_screenshot=False,
    wait_settle=wait_for_page_settle,
    wait_networkidle=wait_for_networkidle,        # ← 新增
)
```

### 配置

**on/off 在 AgentSettings（仿 `rerun_wait_for_page_settle`）**：

```python
# config.py:129 后
rerun_wait_for_networkidle: bool = False      # get_state 前等 networkidle（缺口 2）；默认关 = 零行为变更
                                              # 开启条件：页面变化由 AJAX 驱动（readyState 常年 complete 的 SPA）

# config.py:354 后（load_settings env）
rerun_wait_for_networkidle=os.environ.get("AGENT_RERUN_WAIT_FOR_NETWORKIDLE", "").lower() == "true",

# agent.py:83 后
self.rerun_wait_for_networkidle = _settings.rerun_wait_for_networkidle
```

**tuning 在 BrowserSettings（仿 `page_settle_*`，config.py:187-189）**：

```python
# config.py BrowserSettings，page_settle_* 紧邻后
network_idle_timeout: float = 5.0             # 单步 networkidle 等待上限（秒）；AJAX 常 <2s，慢网/大文件 5s 兜底
network_idle_stability_window: float = 0.5    # "无新请求 N 秒"判 idle（秒）；knowledge-garden §6 推荐；Playwright 0.5
network_idle_poll_interval: float = 0.1       # wait_until_idle 轮询间隔（秒）；对齐 page_settle_poll；Playwright ~0.1
```

> tuning 不暴露 env（仅 dataclass 默认），与 `page_settle_*` 一致。CLI/TUI 不动（None 哨兵接管）。

### 缺口 6：移除录制端注入 + 重放端可配置 wait（用户已选此方案）

**两步**：① 删 `recorder.py` 注入 + 死常量；② 在 `_execute_history_step` 动作循环内、`upload_file` 的 `_exec_one` 后加可配置 sleep，复用 rerun.py 既存扩展名集。

```python
# recorder.py 删除（50-55 常量 + 54-55 扩展名集 + 58-65 _file_kind；243-255 注入块）
# —— _VIDEO_EXTS/_IMAGE_EXTS 与 rerun.py:187-188 逐字符相同，删除无信息丢失。
# —— 241-242 的 pending_exc 守卫【必须保留】。
# 删后 handle_event 尾部直接：
if pending_exc is not None:
    raise pending_exc
logger.info("录进步 %d: %s", len(self.recording.actions) - 1, action.action_name)
return action
```

```python
# config.py AgentSettings 新增 2 字段（默认 = 原硬编码 = 零行为变更）
rerun_upload_wait_video: float = 5.0          # 重放端 upload_file 后等待（秒）；替代原录制端注入
rerun_upload_wait_image: float = 3.0          # 默认 = 原 _UPLOAD_WAIT_SECONDS，旧录制（无注入）与新录制零差异

# config.py load_settings env
rerun_upload_wait_video=float(os.environ.get("AGENT_RERUN_UPLOAD_WAIT_VIDEO", "5.0")),
rerun_upload_wait_image=float(os.environ.get("AGENT_RERUN_UPLOAD_WAIT_IMAGE", "3.0")),

# agent.py __init__ 拆 2 字段
self.rerun_upload_wait_video = _settings.rerun_upload_wait_video
self.rerun_upload_wait_image = _settings.rerun_upload_wait_image

# rerun.py RerunMixin 注解 + rerun_history 签名 + None 哨兵（数值型 None 哨兵回落 self）
```

```python
# rerun.py 模块级新增（复用既存 _UPLOAD_VIDEO_EXTS/_UPLOAD_IMAGE_EXTS，零扩展名重复）
def _upload_file_kind(path: str) -> str | None:
    ext = os.path.splitext(path or "")[1].lower().lstrip(".")
    if ext in _UPLOAD_VIDEO_EXTS: return "video"
    if ext in _UPLOAD_IMAGE_EXTS: return "image"
    return None
```

```python
# rerun.py 动作循环（~609-628），_exec_one 后、guard 前插入
result = await self._exec_one(name, params, state)
results.append(result)
# 阶段3 缺口6：upload_file 成功后可配置等待（替代原录制端硬编码注入）。
# 仅成功时等（result.error 跳过）——比原"独立 wait 步无条件睡"更合理：失败时 step 会重试/break，
# 不等可避免 retry×sleep 叠加浪费。语义仍覆盖"upload 与下一动作之间"的等待窗口。
if name == "upload_file" and not result.error:
    kind = _upload_file_kind(params.get("path", ""))
    wait_s = (self.rerun_upload_wait_video if kind == "video"
              else self.rerun_upload_wait_image if kind == "image"
              else 0.0)                          # 未知类型不等（原 .get(kind,3) 太武断）
    if wait_s > 0:
        logger.info("upload 后等待 %.1fs（%s）", wait_s, kind or "unknown")
        await asyncio.sleep(wait_s)
last = results[-1]
if last.is_done or last.error: break
```

> **未选方案的差异**（备查）：若选"纯移除依赖 networkidle"——删注入、不加 `rerun_upload_wait_*`；风险：抖音封面 canvas 渲染不发 XHR → networkidle 即时判 idle → 回归。若选"缺口 6 延后"——本期只做缺口 2，recorder 注入保留，零风险但录制语义仍污染。

---

## 关键决策

| # | 决策 | 理由 |
|---|---|---|
| D-1 | 新建 `NetworkIdleTracker` 独立模块（仿 `circuit_breaker.py`） | 状态机自含、可单测、不污染 `session.py`（已 3000+ 行） |
| D-2 | tracker **always-on**，wait 由 `get_state(wait_networkidle=...)` 显式触发 | Network.enable 开销可忽略（回调仅 set.add/discard）；翻转开关无需重连；对齐 `page_settle` 的"tuning on BrowserSettings / on-off on AgentSettings"分层 |
| D-3 | Network.enable + 注册放 `_connect` 内（非新 setup 函数） | Network.enable 是 per-session（仿 Page.enable）；`_connect` 覆盖 start/reconnect/switch_tab 三场景一次到位 |
| D-4 | 长连接过滤集 = `{WebSocket, EventSource}`；以 `ResponseReceived.type` 为准 | type 在 responseReceived 必填、在 requestWillBeSent 是 NotRequired；WebSocket 不收 loadingFinished、EventSource 长流不结束 |
| D-5 | Fetch/XHR long-poll **不**进过滤集 | type 不可区分；靠严格 deadline 兜底（对齐 Playwright networkidle 已知限制）；用户可调 `network_idle_timeout` 或回退 page_settle/element-wait |
| D-6 | `is_idle` 要求 `pending_short==0 AND lull≥stability_window`（**both**） | activity-lull 启发式在"两波请求"节奏误判；Playwright 也是 strict |
| D-7 | pending 用 `set[requestId]` 而非计数器 | 自动去重（redirect 复用 requestId）；`discard` 幂等钳零，永不负 |
| D-8 | `get_state` 内 `wait_settle` → `wait_networkidle` 串行 | readyState 粗粒度容器信号先稳定，networkidle 细粒度数据信号后判；都乐观早退 |
| D-9 | tracker `_enabled=False` 时 `is_idle` 直接返 True | 降级=等同关闭；对齐 `recent_events` setup 失败降级 |
| D-10 | 缺口 6：删录制注入 + 重放端 `rerun_upload_wait_{video,image}` 可配置（用户已选） | 默认值 = 原硬编码 = 零差异；录制语义净化；用户可调/关 |
| D-11 | upload wait 仅 `not result.error` 时睡（非无条件） | 比原"独立 wait 步无条件睡"更合理；失败时 step 重试/break，不等避免 retry×sleep 叠加浪费 |
| D-12 | 未知文件类型 `wait_s=0`（非默认 3s） | 原 `.get(kind,3)` 太武断；用户配 `rerun_upload_wait_*` 通常已给正确值，未知→保守不等 |
| D-13 | `_upload_file_kind` 复用 rerun.py 既存 `_UPLOAD_VIDEO_EXTS/_UPLOAD_IMAGE_EXTS` | 两处扩展名集逐字符相同；零重复；recorder.py 副本可整体删 |
| D-14 | tracker `reset()` 在 `_connect` 入口显式调 | 清旧 session inflight 残留，避免新会话误判 idle |

---

## 改动清单（逐文件）

| 文件 | 改动 |
|---|---|
| `src/tree_walker/browser/network_idle.py` | **新增**：`NetworkIdleTracker` 类 + `_LONG_CONNECTION_TYPES`（~140 行，仿 `circuit_breaker.py`） |
| `src/tree_walker/browser/session.py` | ① `__init__` 末加 `self._network_idle_tracker = NetworkIdleTracker(...)`；② `_connect` 入口 `reset()`；③ Page/DOM.enable 后 `Network.enable` + `register`（try/except 降级）；④ `get_state` 加 `wait_networkidle` 参数 + 等待块（wait_settle 之后） |
| `src/tree_walker/agent/rerun.py` | ① 模块级 `_upload_file_kind`（复用既存扩展名集）；② RerunMixin 注解 `rerun_wait_for_networkidle` + `rerun_upload_wait_video/image`；③ `rerun_history` 签名 + None 哨兵；④ 12 touchpoints 透传 `wait_for_networkidle`；⑤ 动作循环内 upload 后可配置 sleep |
| `src/tree_walker/config.py` | ① `AgentSettings` 加 `rerun_wait_for_networkidle` + `rerun_upload_wait_{video,image}`；② `BrowserSettings` 加 `network_idle_{timeout,stability_window,poll_interval}`；③ `load_settings` 加 env |
| `src/tree_walker/agent/agent.py` | `__init__` 拆 3 个新 `self.rerun_*` |
| `src/tree_walker/recorder/recorder.py` | 删 `_UPLOAD_WAIT_SECONDS`/`_VIDEO_EXTS`/`_IMAGE_EXTS`/`_file_kind`/注入块（**241-242 pending_exc 守卫保留**） |
| `src/tree_walker/cli.py` / `tui/app.py` | **不改**（None 哨兵接管） |
| `src/tree_walker/tools/actions.py` | **不改**（`_verify_upload` 独立，与本期正交） |

**复用**：cdp_use `Network` 域全部 API、`_wait_for_page_settle` §8 骨架、阶段1 的 12 处配置接线范式、`circuit_breaker.py` 模块风格、`_setup_download_tracking`/`_setup_event_tracking` 的 register+回调模式、rerun.py 既存 `_UPLOAD_VIDEO_EXTS/_UPLOAD_IMAGE_EXTS`、`_rerun_step_with_retries` 失败兜底。

---

## 测试策略

**`tests/test_network_idle_tracker.py` 新增**（纯单测，无需 BrowserSession）：
- **基础计数**：requestWillBeSent → is_idle False；后跟 loadingFinished/loadingFailed（满足 window）→ True；responseReceived 后 loadingFinished（type=XHR）→ True。
- **长连接过滤**：WebSocket/EventSource（responseReceived.type）无 loadingFinished → True（被过滤）；XHR long-poll 无 loadingFinished → False（不过滤，靠 deadline）；先 XHR 后 responseReceived 补 type=WebSocket → 正确过滤。
- **稳定窗口**：pending==0 但 lull<window → False；lull≥window → True；window 内来新请求 → 重置 lull。
- **边界**：redirect（同 requestId 多次 requestWillBeSent）→ set 不膨胀；重复 loadingFinished（discard 幂等）→ 不负；`requestWillBeSent.type` 缺失 → 不崩。
- **降级**：`_enabled=False` → is_idle True / wait_until_idle 即时 True；wait_until_idle 超时返 False（不抛）。

**`tests/test_browser_session.py` 新增**（integration，复用既有 fake client 模式）：
- `get_state(wait_networkidle=True)` 触发 `tracker.wait_until_idle`（mock tracker）；Network.enable 在 `_connect` 被发送；4 回调注册到正确 method 名；Network.enable 抛异常 → tracker 降级 + `get_state` 不抛；`wait_settle=True, wait_networkidle=True` 串行（先 page_settle 后 networkidle）；reconnect 后 tracker reset 被调。

**`tests/test_rerun_history.py` 新增**：
- 默认关零行为变更（`wait_for_networkidle=False` → `get_state` 不传 wait_networkidle）；开 → 传入；env `AGENT_RERUN_WAIT_FOR_NETWORKIDLE=true` 生效；`_reexecute_menu_opener` 透传。
- 缺口 6：upload 成功后 sleep 配置值被消费（mock asyncio.sleep）；默认 5.0/3.0 与原硬编码等价；未知类型 wait_s=0；upload 失败（result.error）跳过 sleep。

**`tests/test_recorder.py` 改动**：
- `test_upload_file_records_signature_no_fingerprint`（171-202）：删 `assert ... wait, seconds=5`，改为断言末尾 action 即 upload_file 本身。
- `test_upload_file_wait_seconds_image`（205-219）：改写为"upload 后无 wait 注入"或删除。
- 新增 `test_upload_file_no_wait_injected`（断言 action 数=1）。

**`tests/test_config.py`**：AgentSettings 默认（False/5.0/3.0）+ env；BrowserSettings 默认（5.0/0.5/0.1）。

---

## 验证

1. **文档体裁**：与 `01-阶段1`/`02-阶段2` 一致（关联块/Context/现状锚点/方案/决策表/改动清单/测试/风险表/核对清单）。
2. **行号锚点**：全部对照 master `e15b4a3` 核验（实施时若 master 前进需重核）。
3. **纯文档零代码**：本方案文档不改任何 `.py`；代码改动留给 #126 实现 PR。
4. 后续实现 PR 的验证：`uv run python -m pytest tests/ -x -v` 全过、覆盖率 ≥85%、默认配置（`rerun_wait_for_networkidle=False` + `rerun_upload_wait_*=5.0/3.0`）重放零回归、长连接场景不无限等待。

---

## 边界与风险

| 项 | 说明 |
|---|---|
| **长连接误判** | WebSocket/SSE 已过滤；XHR long-poll/Fetch streaming type=XHR/Fetch 不在过滤集 → 等到 `network_idle_timeout` 超时降级。**已知限制，对齐 Playwright networkidle**；应对：调高 timeout / 用 page_settle / element-wait |
| **SPA 客户端渲染盲点** | 抖音封面上传：`DOM.setFileInputFiles` 后 canvas 渲染**不发 XHR** → networkidle 即时判 idle → 下一步失败。**这就是缺口 6 不能纯移除、必须保留可配置 upload wait 兜底的原因** |
| **Network.enable 开销** | 每请求最多 4 事件；回调极轻（set.add/discard + monotonic），可忽略。debug 日志全开会爆——用 `logger.debug` 不打默认 INFO |
| **单回调覆盖** | cdp_use `EventRegistry` last-wins。本期注册的 4 method 当前仓库无其他注册（grep 确认），零冲突。**未来若 recent_events 监听 Network 域需合并回调**——TODO |
| **多 session / iframe** | `Target.setAutoAttach` 让 iframe 子 session 也收 Network 事件；tracker 是 BrowserSession 级单例 → iframe 请求计入 pending。**语义合理**（用户视觉"网络空闲"含 iframe）；需隔离则未来按 session_id 分桶（本期不做） |
| **等待串行叠加** | 最坏 page_settle(2.0)+networkidle(5.0)+element-wait(15.0)+actionability(2.0)=24s/步；乐观早退使常态≈0；默认全关 = 零开销 |
| **默认关零回归** | `rerun_wait_for_networkidle=False` → `get_state(wait_networkidle=False)` → tracker.wait_until_idle 不调 → 与阶段2 完全一致。tracker always-on 仅多 4 回调（轻量），不改返回值 |
| **缺口 6 老历史"双等"** | 老 recorded.json（带注入 wait 步）重放：upload 步后等 5/3s（新机制）→ 下一步是旧 wait 步（再 sleep 5/3s）→ **双等**。新录制（无注入）只等一次。**缓解**：文档明示 + 推荐重新录制（净化后历史更干净）；不做"检测下一步是否注入 wait 则跳过"（脆弱，不推荐） |
| **upload 失败不 sleep** | D-11：`result.error` 跳过 sleep。比原"独立 wait 步无条件睡"更合理；用户可设 `rerun_upload_wait_*=0` 全关 |
| **`_reexecute_menu_opener` 路径** | 菜单重打开也吃 wait_for_networkidle（透传）；菜单展开后的 AJAX 也要等，语义合理 |

---

## 落地核对清单（实现 #126 时逐项核对）

- [ ] **新模块** `network_idle.py`：`NetworkIdleTracker` + `_LONG_CONNECTION_TYPES`
- [ ] **session.py**：`__init__` tracker 字段；`_connect` `reset()` + Network.enable/register（try/except）；`get_state` `wait_networkidle` 参数 + 块（wait_settle 之后）
- [ ] **rerun.py 12 touchpoints**：注解 / `rerun_history` 签名+哨兵 / **`rerun.py:367-369` 调 `_rerun_step_with_retries`** / `_rerun_step_with_retries` 签名 / 调 `_execute_history_step` / 调 `_reexecute_menu_opener` / `_execute_history_step` 签名 / `get_state` 消费 / `_reexecute_menu_opener` 签名+调
- [ ] **缺口 6 rerun.py**：`_upload_file_kind`（复用既存扩展名集）；动作循环 upload 后可配置 sleep（`not result.error`）
- [ ] **config.py**：AgentSettings 3 字段（False/5.0/3.0）；BrowserSettings 3 字段（5.0/0.5/0.1）；load_settings env
- [ ] **agent.py**：`__init__` 拆 3 字段
- [ ] **recorder.py**：删 `_UPLOAD_WAIT_SECONDS`/`_VIDEO_EXTS`/`_IMAGE_EXTS`/`_file_kind`/注入块（**241-242 守卫保留**）
- [ ] **测试**：tracker 单测（~16）+ browser_session（~6）+ rerun_history（~7）+ recorder（~3）+ config（~2）
- [ ] **回归**：`uv run python -m pytest tests/ -x -v` 全过；默认配置重放零回归；老 recorded.json"双等"行为已文档说明
