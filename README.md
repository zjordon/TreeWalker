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

**3. 启动 TUI**

```bash
tw-tui
```

或直接指定任务：

```bash
tw-tui -t "打开百度搜索今天的新闻"
```

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

<!-- 在此处添加 TUI 界面截图 -->
<!-- ![TreeWalker TUI](docs/screenshots/tui-demo.png) -->

> 截图占位 — 运行 `tw-tui` 即可看到完整 TUI 界面

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

## 配置

所有配置通过环境变量或 `.env` 文件设置，参见 [.env.example](.env.example) 获取完整配置项。

## 致谢

本项目的设计思路参考了 [browser-use](https://github.com/browser-use/browser-use)，感谢其开创性的工作。

## License

本项目采用 [CC BY-NC 4.0](LICENSE) 协议开源，仅供非商业用途。
