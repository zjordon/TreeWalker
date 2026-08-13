# TreeWalker

基于 LLM 的浏览器自动化 Agent —— 通过自然语言驱动 Chrome 完成网页任务。

```
输入: "搜索Python教程并打开第一个结果"
  ↓  Sense → Think → Act
输出: ✅ 已在百度搜索"Python教程"并打开菜鸟教程页面
```

## 安装

需要 Python 3.12+，使用 [uv](https://docs.astral.sh/uv/) 安装：

```bash
git clone https://github.com/zjordon/TreeWalker.git
cd TreeWalker
uv sync
```

## 快速开始

**1. 启动 Chrome 远程调试**

```bash
# macOS
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222

# Windows
chrome.exe --remote-debugging-port=9222

# Linux
google-chrome --remote-debugging-port=9222
```

**2. 配置环境变量**

```bash
cp .env.example .env
# 编辑 .env，填入你的 LLM API Key
```

最小配置只需一行：

```
ZHIPU_API_KEY=your_api_key_here
```

**3. 启动界面**

TreeWalker 提供两个入口（都是 live agent 控制台）：`tw-web` 默认连 Chrome **9223**（web/重放路径约定端口，与 `serve_history_editor`/`csv_rerun` 一致），`tw-tui` 连 Chrome **9222**（config 默认）。按惯例分用两个端口，即可各开一个 Chrome 实例互不干扰。

**Web 前端**（`tw-web`，推荐——可视化实时步骤/截图/日志，流程库编辑/批量重放）：

```bash
cd web_ui && npm install && npm run build && cd ..   # 首次需构建前端
tw-web                                                # 开 http://127.0.0.1:8766/
```

**TUI**（`tw-tui`，终端）：

```bash
tw-tui
tw-tui -t "打开百度搜索今天的新闻"                     # 直接指定任务
```

> 完整 Web 功能见下方「Web 前端」章节。

## 编程接口

```python
import asyncio
from tree_walker import Agent, LLMClient, BrowserSession
from tree_walker.config import LLMSettings, BrowserSettings

async def main():
    llm = LLMClient(LLMSettings(api_key="your-key"))
    browser = BrowserSession(BrowserSettings(ws_url="ws://localhost:9222/..."))

    agent = Agent(
        task="在GitHub上搜索browser-use项目，找到star数最多的那个",
        llm=llm,
        browser=browser,
    )
    history = await agent.run()
    print(history.final_result())

asyncio.run(main())
```

## 支持的动作

| 动作 | 说明 |
|------|------|
| `navigate` | 导航到指定 URL |
| `click` | 点击元素 |
| `input_text` | 输入文本 |
| `scroll` | 滚动页面 |
| `search` | 搜索引擎搜索 |
| `extract` | 从页面提取信息 |
| `send_keys` | 发送键盘快捷键 |
| `switch_tab` / `close_tab` | 标签页管理 |
| `upload_file` | 上传文件 |
| `select_dropdown` | 下拉框选择 |
| `evaluate` | 执行 JavaScript |
| `read_file` / `write_file` | 读写本地文件 |
| `screenshot` / `save_as_pdf` | 截图 / 保存 PDF |
| `done` | 标记任务完成 |

## 截图

<!-- 在此处添加界面截图 -->
<!-- ![TreeWalker Web](docs/screenshots/web-demo.png) -->

> 截图占位 — 运行 `tw-web`（浏览器）或 `tw-tui`（终端）即可看到完整界面

## 架构

```
┌─────────────────────────────────────────────────┐
│                    TreeWalker                    │
│                                                  │
│  ┌──────────┐   ┌───────────┐   ┌────────────┐  │
│  │   TUI    │   │   Agent   │   │    LLM     │  │
│  │ (Textual)│──▶│  Loop     │──▶│  Client    │  │
│  └──────────┘   └─────┬─────┘   └────────────┘  │
│                       │                          │
│              ┌────────┴────────┐                 │
│              │  Step Pipeline  │                 │
│              │                 │                 │
│              │ 1. 准备上下文    │                 │
│              │ 2. LLM 决策     │                 │
│              │ 3. 执行动作     │                 │
│              │ 4. 后处理       │                 │
│              │ 5. 终结化       │                 │
│              └────────┬────────┘                 │
│                       │                          │
│              ┌────────┴────────┐                 │
│              │  BrowserSession │                 │
│              │  (CDP Protocol) │                 │
│              └────────┬────────┘                 │
│                       │                          │
└───────────────────────┼──────────────────────────┘
                        │
                        ▼
              ┌──────────────────┐
              │  Chrome Browser  │
              │  (Debug Mode)    │
              └──────────────────┘
```

Agent 在每一步循环中：**感知**页面 DOM → **思考**下一步动作 → **执行**浏览器操作，直到任务完成。

> 前端入口：TUI（`tw-tui`，终端）与 Web（`tw-web`，浏览器，推荐）——两者驱动同一个 Agent Loop。

## 高级特性

**消息压缩** — 长任务自动压缩历史消息，避免上下文溢出：

```
MESSAGE_COMPACTION_ENABLED=true
MESSAGE_COMPACTION_EVERY_N_STEPS=10
```

**计划系统** — Agent 自动制定执行计划并在卡住时重新规划：

```
AGENT_ENABLE_PLANNING=true
```

**Judge 评估** — 任务完成后自动评估质量：

```
AGENT_JUDGE_ENABLED=1
AGENT_JUDGE_MODEL=glm-5.1
```

**Fallback LLM** — 主模型限流时自动切换备用模型：

```
FALLBACK_LLM_MODEL=claude-sonnet-4-6
FALLBACK_LLM_API_KEY=sk-xxx
```

## 用户操作录制 → 历史重放

除了让 Agent 自动探索，也可以**录制你自己的真实操作**生成可重放的历史——路径稳定可靠，消除 LLM 决策随机带来的不稳定。

Chrome 扩展采集操作 → Python 后端翻译流水线（Signal 模型，意图感知去噪）+ 经 CDP 算指纹 → 落盘 `AgentHistory` → 用不同数据重放（重放时不再调 LLM 决策）。

**重放三路径降级链**（自动选择，对各类操作稳健）：

- **指纹匹配**：稳定可见元素（按钮、输入框等）走 `element_hash` 精准匹配。
- **语义线索回放**：点 submit/链接等触发跳转的操作，录制时元素虽消失，重放时凭语义线索（xpath/属性/位置）在稳定页面重新定位——利用重放的主动时序优势。
- **accept 兜底**：文件上传等隐藏 input 按 `accept` 类型解析。

**录制端去噪与健壮性**：连续输入合并、重复点击折叠、副作用导航吸收；自动跳过非可交互的噪声点击（点到段落空白等）；`get_state` 异常容错（跳转导致 CDP target 卸载时不中断）。

```bash
# 1. Chrome 以远程调试端口启动（建议用录制专用 profile，提前登录目标站点）
chrome --remote-debugging-port=9222 --user-data-dir=<录制 profile>

# 2. 启动录制后端（监听 http://127.0.0.1:8765）
uv run python examples/record_user_actions.py --out myflow.json

# 3. 加载 recording_extension/ 扩展 → 点「开始录制」→ 操作 → 「停止」
#    产物落 rerun-history/myflow.json

# 4. 重放
uv run python examples/replay.py myflow.json
```

换数据重放：

```python
await agent.load_and_rerun("myflow.json", variables={"email": "new@x.com"})
```

> 录制产物与 agent 自录同址（`rerun-history/`），指纹录制/重放同源（全对齐）。
> 完整设计、架构、方案演进与架构反思见 [docs/user_recording/](docs/user_recording/)：
> [`README.md`](docs/user_recording/README.md) · [`redesign.md`](docs/user_recording/redesign.md) · [`semantic-clue-replay.md`](docs/user_recording/semantic-clue-replay.md) · [`recorder-timing-solutions.md`](docs/user_recording/recorder-timing-solutions.md)

## Web 前端（tw-web）

`tw-web` 是浏览器端统一界面，承接 TUI 全部交互能力，更适合复杂交付与可视化。两个模式：

- **探索**（live agent 控制台）：输入任务 → 实时看 agent 的步骤时间线、浏览器截图、日志流；可暂停 / 停止；开「录制轨迹」把本次 `AgentHistory` 存盘成可重放流程。
- **流程库**：管理/编辑/重放已存历史（`rerun-history/*.json`）——可视化编辑动作、标注变量、单次试跑、CSV 批量、详情查看。

### 启动

首次需构建前端（生成 `history_editor/static/`）：
```bash
cd web_ui && npm install && npm run build && cd ..
# 或 ./scripts/build_editor.sh（mac/linux）/ .\scripts\build_editor.ps1（Windows）
```

启动（默认 http://127.0.0.1:8766/，连 Chrome 9223）：
```bash
tw-web
```

前端开发模式（热重载，免构建）：
```bash
tw-web                            # 终端1：后端 8766
cd web_ui && npm run dev          # 终端2：Vite 5173（proxy → 8766）
# 浏览器开 http://127.0.0.1:5173/
```

### 流程库：可视化编辑

浏览器打开 `tw-web` → 顶部「流程库」→ 左侧选历史文件 → 加载：

- **动作列表**（可拖拽重排）：每步的动作类型 / 目标元素 / 参数
- **编辑**：改 input 的 text、删除误录步
- **标注变量**：把任意 input 的 text 标为变量（指定变量名），补 `detect_variables` 自动检测识别不了的字段（商品名、订单备注等无规律值）
- **检测变量**：自动检测（email/phone/name 等有规律字段）∪ 人工标注
- **试跑**：真实起浏览器，每步 ✓/✗
- **详情**：元信息 + 每步动作/结果/状态摘要 master-detail
- **保存**：写回 `rerun-history/*.json`

> 完整方案见 [docs/p4/](docs/p4/)（编辑器）+ [docs/p6/](docs/p6/)（live 控制台 + 流程库 IA/实施/e2e/复盘）。

### CSV 批量重放

「流程库 → 重放」上传 CSV（变量名 = 列头，对齐「检测变量」），录制一次批量执行 N 次，步级实时进度可中止。或用 CLI / 编程接口：

```bash
# data.csv
email,name
alice@example.com,Alice
bob@example.com,Bob

# CLI 批量重放（examples/csv_rerun.py 默认连 Chrome 9223，见脚本注释；需 LLM key）
uv run python examples/csv_rerun.py myflow.json data.csv
```

编程接口：

```python
results = await agent.batch_rerun("myflow.json", "data.csv")
for r in results:
    print(f"行{r.row_index}: {'✓' if r.success else '✗'} {r.extracted_content or r.error}")
```

缺列变量用历史原值（宽容）；单行失败不中断批量。

## 配置

所有配置通过环境变量或 `.env` 文件设置，参见 [.env.example](.env.example) 获取完整配置项。

## 致谢

本项目的设计思路参考了 [browser-use](https://github.com/browser-use/browser-use)，感谢其开创性的工作。

## License

本项目采用 [CC BY-NC 4.0](LICENSE) 协议开源，仅供非商业用途。
