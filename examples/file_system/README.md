# examples/file_system

从 [browser-use](https://github.com/browser-use/browser-use) 的 `examples/file_system/` 移植而来，演示 TreeWalker 的**本地文件工具**（`read_file` / `write_file` / `replace_file`）与浏览器自动化结合使用。

## 与 browser-use 原版的关键差异

| 维度 | browser-use | TreeWalker |
|---|---|---|
| 文件层 | 内存 `FileSystem`，挂载到 `file_system_path` | 无内存层，直接写**真实文件**到绝对路径 |
| 写沙箱 | `file_system_path`（相对路径，虚拟根） | `allowed_write_paths` 白名单（前缀匹配，gate `write_file` / `replace_file`，**不** gate `read_file`） |
| 追加写 | 独立的 `append_file` 工具 | `write_file` 的 `append=True` 参数 |
| LLM | OpenAI（`ChatOpenAI`） | 智谱 GLM（`LLMClient` + `ZHIPU_API_KEY`） |
| 浏览器 | 内置 | 需 Chrome `--remote-debugging-port=9222`（`BrowserSession`） |

> 沙箱实现见 `src/tree_walker/config.py`（`AgentSettings.allowed_write_paths` / `_load_allowed_write_paths`）与 `src/tree_walker/agent/agent.py`（`Tools(..., allowed_write_paths=...)`）。

## 示例

| 文件 | 场景 | 练习的工具 |
|---|---|---|
| `file_system.py` | 抓取博客标题 → 写文件 → 追加首句 → 读回校验 | `write_file`（write + append）/ `read_file` |
| `alphabet_earnings.py` | 打开 PDF → 取 3 个数据点 → 写文件 → 读回 | `write_file` / `read_file` |
| `excel_sheet.py` | 查股价 → 生成 CSV → 读回 | `write_file` / `read_file` |

每个示例会自建一个同级的 `*_workspace/` 目录作为写沙箱（`allowed_write_paths` 指向它），运行结束按回车后清理。

## 前置条件

1. `uv sync`
2. 启动 Chrome 远程调试：`chrome --remote-debugging-port=9222`
3. 设置 API Key：`$env:ZHIPU_API_KEY = "your_key"`

## 运行

```powershell
uv run python examples/file_system/file_system.py
uv run python examples/file_system/alphabet_earnings.py
uv run python examples/file_system/excel_sheet.py
```

> 注：`alphabet_earnings.py` 依赖 Chrome 内置 PDF 阅读器把 PDF 文本暴露给 DOM；若取不到文本，可改用 `extract` 工具或换一个 HTML 报告页。本目录示例仅为演示文件工具链路，未在 CI 中运行（依赖真实浏览器 + 网络 + API Key）。
