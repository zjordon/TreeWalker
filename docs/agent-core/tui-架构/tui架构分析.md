# TUI 架构分析

> TUI 框架: Textual (Python)
> 入口文件: `src/tree_walker/cli.py` → `TreeWalkerApp` (app.py)

## 1. 初始化全流程

TUI 应用启动分为 4 个阶段：

```
click CLI 入口 (cli.py)
  │
  ├── 阶段 1: CLI 参数解析                      (cli.py L15-28)
  │     ├── @click.option("--task", "-t")       初始任务（可选）
  │     ├── @click.option("--debug")            调试模式
  │     ├── load_settings()                     加载环境变量配置
  │     └── 强制 enable_observability = True    TUI 模式需要事件流
  │
  ├── 阶段 2: 配置加载                          (cli.py L20-26)
  │     ├── Settings.agent / .llm / .browser
  │     └── debug → TUISettings.log_level = "DEBUG"
  │
  ├── 阶段 3: 核心组件预初始化                   (cli.py L31-46)
  │     ├── LLMClient(settings.llm)             LLM 客户端
  │     ├── BrowserSession(settings.browser)     浏览器会话（未连接）
  │     └── TreeWalkerApp(llm, browser, ...)     Textual App 实例
  │
  └── 阶段 4: 应用挂载                           (cli.py L47)
        └── await app.run_async()                Textual 事件循环启动
              ├── on_mount() → _setup_logging()
              ├── on_mount() → _load_history()
              └── compose() → Widget 树构建
```

## 2. Widget 树与布局结构

```
TreeWalkerApp (App)
  ├── Header                                    # 顶部标题栏
  ├── Container#welcome-panel (初始可见)          # 欢迎面板
  │   ├── Static#logo                           ASCII Logo
  │   ├── Static#shortcuts                      快捷键提示
  │   └── Static#hint                           "在下方输入任务开始"
  │
  ├── Container#two-column-container (初始隐藏)   # 工作面板
  │   ├── VerticalScroll#main-output-column     # 左列: 主日志
  │   │   └── AgentLog#main-log                 # Agent 输出日志
  │   └── VerticalScroll#events-column          # 右列: 事件流
  │       └── RichLog#events-log                # 可观测性事件
  │
  ├── Container#task-input-container (底部停靠)   # 输入区域
  │   └── HorizontalGroup
  │       ├── MultilineInput#task-input (3fr)    # 任务输入框
  │       └── MultilineInput#file-paths-input (1fr) # 文件路径
  │
  └── Footer                                    # 底部快捷键栏
```

**视图切换逻辑**：

```
初始状态:
  welcome-panel → visible
  two-column-container → hidden

_switch_to_working_view() (提交任务后):
  welcome-panel → hidden
  two-column-container → visible
```

**布局 CSS**：
- `#two-column-container` — `layout: horizontal; height: 1fr` (水平双列)
- `#task-input-container` — `dock: bottom; height: auto` (底部停靠)
- `#task-input` — `width: 3fr` (3/4 宽度)
- `#file-paths-input` — `width: 1fr; border-left` (1/4 宽度)

## 3. 日志/事件管道

### 3.1 主日志管道

```
Python logging (tree_walker.*)
     │
     ▼
RichLogHandler (log_handler.py)
  │   └── emit() → self._rich_log.write(msg)
  │
  ▼
AgentLog#main-log (Textual RichLog Widget)
  └── 原生渲染 ANSI 颜色代码
```

**初始化** (app.py L159-172)：
```python
def _setup_logging(self):
    main_log = self.query_one("#main-log", AgentLog)
    handler = RichLogHandler(main_log)
    handler.setFormatter(logging.Formatter("%(message)s"))

    root = logging.getLogger()
    root.handlers.clear()          # 清除所有已有 handler
    root.addHandler(handler)        # 只保留 TUI handler

    logging.getLogger("tree_walker").setLevel(logging.INFO)
    for lib in ("httpx", "anthropic", "cdp_use", "websockets"):
        logging.getLogger(lib).setLevel(logging.ERROR)
```

### 3.2 事件流管道

```
Agent._obs_bus (EventBus)
     │
     ├── subscribe("*", MetricsAggregator)
     ├── subscribe("*", JsonlRecorder)
     ├── subscribe("*", AnomalyDetector)
     │
     └── subscribe("*", EventBridge.handle)        ← TUI 订阅
           │
           ▼
EventBridge (event_bridge.py)
  │   └── handle(event) → events_log.write(f"[{color}][{ts}] {text}[/]")
  │
  ▼
RichLog#events-log (右侧面板)
```

**事件格式化** (event_bridge.py)：

| 事件类型 | 颜色 | 示例输出 |
|---------|------|---------|
| step_start | cyan | `Step 5 开始` |
| model_call | blue | `Step 5 → LLM 调用 (消息: 12)` |
| model_result | green | `Step 5 ← LLM: click` |
| tool_call | magenta | `Step 5 执行: click({"index": 42})` |
| tool_result | yellow | `Step 5 结果: ✓ (1.23s)` |
| step_end | cyan | `Step 5 结束 (2.45s)` |
| anomaly | red bold | `⚠ [warning] Action repeated 5 times` |
| session_end | green bold | `会话结束: Task completed successfully` |

## 4. 控制流 — TUI 到 Agent

```
用户交互                       TUI 处理                          Agent 方法
─────────────────────────────────────────────────────────────────────────
输入任务 + Enter    →  on_multiline_input_submitted  →  _run_task()
                     │  ├── 验证文件路径
                     │  ├── 保存历史
                     │  ├── 创建 Agent 实例
                     │  ├── 跳过 signal handler (TUI 自管理)
                     │  └── run_worker(_agent_worker)  →  agent.run(keep_alive=True)

Ctrl+C              →  action_request_pause           →  agent.pause() / agent.resume()
                     │  └── toggle: paused ↔ running

Ctrl+Q              →  Binding "quit"                  →  app.exit → on_exit()
                     │                                    └── agent.stop() + browser.stop()

Ctrl+L              →  action_clear_log                →  RichLog.clear()

↑/↓ 箭头            →  on_multiline_input_history_navigation → 浏览历史记录
```

**关键设计**：
- `agent._setup_signal_handler = lambda: None` — TUI 模式跳过 SIGINT handler，改用 Textual 键绑定
- `agent.run(keep_alive=True)` — 任务间保持浏览器连接
- `run_worker()` — Agent 在 Textual 的后台 worker 中运行，不阻塞 UI

## 5. 错误处理与恢复

| 场景 | TUI 处理 | Agent 处理 |
|------|---------|-----------|
| Agent 异常 | `_agent_worker` try/except → 日志到 main-log | 由 `_handle_step_error` 处理 |
| 浏览器断开 | 无特殊处理（Agent 内部重连） | BrowserSession.reconnect() |
| 文件路径不存在 | 提交前校验 → 拒绝提交 | — |
| App 退出 | `on_exit()` → stop agent + stop browser | Agent.stop() + browser.stop() |

**Agent 异常捕获** (app.py L210-218)：
```python
async def _agent_worker(self):
    try:
        await self._agent.run(keep_alive=True)
    except Exception as e:
        logger.error("Agent error: %s", e)
        self._log(f"Agent error: {e}")
    finally:
        self.query_one("#task-input", MultilineInput).focus()  # 恢复输入焦点
```

## 6. 线程/协程模型

```
Textual 事件循环 (主协程)
  │
  ├── UI 渲染/事件分发 (Textual 框架)
  │
  ├── Agent Worker (async worker)
  │     └── agent.run() → _step() → LLM 调用 + 浏览器操作
  │           └── 全部 async，与 UI 共享事件循环
  │
  └── EventBus 回调 (同步，在 Agent worker 中执行)
        ├── MetricsAggregator.handle()
        ├── JsonlRecorder.handle()
        ├── AnomalyDetector.handle()
        └── EventBridge.handle() → RichLog.write()
```

**关键点**：
- Textual 和 Agent **共享同一个 asyncio 事件循环**，没有多线程
- `run_worker()` 是 Textual 的内置异步 worker 机制
- EventBus 回调是**同步的**，在 Agent worker 的执行上下文中直接调用
- `RichLog.write()` 是线程安全的，可从任何协程调用

**UI 响应性保证**：
- LLM 调用 (`await client.messages.create()`) 是 async 的，不阻塞 UI
- CDP 调用 (`await client.send.*`) 是 async 的，不阻塞 UI
- 动作超时 (`asyncio.wait_for`) 防止单个动作卡死 UI

---

## 📊 总结对比表

| 组件 | 职责 | 线程模型 | 通信方式 |
|------|------|---------|---------|
| TreeWalkerApp | UI 框架 + 任务管理 | 主协程 | Textual Messages |
| RichLogHandler | 日志 → UI 桥接 | 主协程 | Python logging |
| EventBridge | EventBus → UI 桥接 | Agent worker | EventBus subscribe |
| Agent Worker | Agent 执行 | async worker | 共享事件循环 |
| MultilineInput | 任务输入 | 主协程 | Textual Messages |
