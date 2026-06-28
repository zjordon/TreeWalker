# browser-use examples 移植 —— 实施方案索引

> 上游 triage / 总方案：[`browser-use-examples移植方案.md`](browser-use-examples移植方案.md)
>
> 本目录每个文档 = **一个 issue 的可直接执行实施方案**。实施时：打开对应文档 → 按其"目标文件"创建 `.py`（代码已完整给出）→ `uv run` 实跑验证。

## 总览

把 browser-use examples 中"TreeWalker 已有能力可覆盖"的部分移植成本项目 examples，共拆为 **7 个主题 issue**（每个 issue = 一个 PR，可独立 review、可并行）：

| Issue | 文档 | 主题 | example 数 | 裁决 |
|---|---|---|---|---|
| #1 | [`01-结构化输出.md`](01-结构化输出.md) | `output_model` 结构化输出 | 3 | ✅ |
| #2 | [`02-浏览器能力演示.md`](02-浏览器能力演示.md) | 多标签 / 存 PDF / 滚动 | 3 | ✅ |
| #3 | [`03-LLM配置.md`](03-LLM配置.md) | fallback / 抽取小模型 / flash 模式 | 3 | ✅ / ⚠️ |
| #4 | [`04-安全与扩展机制.md`](04-安全与扩展机制.md) | 敏感数据（扁平）/ 自定义动作注册 | 2 | ⚠️ |
| #5 | [`05-文件落地.md`](05-文件落地.md) | 下载文件 / 生成 CSV | 2 | ⚠️ |
| #6 | [`06-并发.md`](06-并发.md) | `asyncio.gather` 多 agent | 1 | ⚠️ |
| #7 | [`07-入门补全.md`](07-入门补全.md) | 表单填写 / 多步任务 | 2 | ✅ |

共 **16 个 example**。不可移植项（云 / 多 LLM provider / 浏览器自启 / 域名黑白名单 / 提示词扩展点 / 历史复跑 / MCP / vision 等）不在本批，见上游方案 §6。

## 目录结构（对齐 browser-use）

example **按 browser-use 的类别目录**存放在 `examples/` 下，**不放根目录**。权威映射（实施时以此为准）：

| Issue | 类别目录 | 文件 | browser-use 来源 |
|---|---|---|---|
| #1 | `features/` | `structured_output.py` | `features/custom_output.py` |
| #1 | `use-cases/` | `phone_price_comparison.py` | `use-cases/phone_comparison.py` |
| #1 | `getting_started/` | `data_extraction.py` | `getting_started/03_data_extraction.py` |
| #2 | `features/` | `multi_tab.py` | `features/multi_tab.py` |
| #2 | `features/` | `save_as_pdf.py` | `features/save_as_pdf.py` |
| #2 | `features/` | `scrolling_page.py` | `features/scrolling_page.py` |
| #3 | `features/` | `fallback_model.py` | `features/fallback_model.py` |
| #3 | `features/` | `extraction_small_model.py` | `features/small_model_for_extraction.py` |
| #3 | `getting_started/` | `fast_agent.py` | `getting_started/05_fast_agent.py` |
| #4 | `features/` | `sensitive_data.py` | `features/sensitive_data.py` |
| #4 | `custom-functions/` | `custom_action.py` | `custom-functions/file_upload.py` |
| #5 | `features/` | `download_file.py` | `features/download_file.py` |
| #5 | `features/` | `csv_generation.py` | `features/csv_file_generation.py` |
| #6 | `custom-functions/` | `parallel_agents.py` | `custom-functions/parallel_agents.py` |
| #7 | `getting_started/` | `form_filling.py` | `getting_started/02_form_filling.py` |
| #7 | `getting_started/` | `multi_step_task.py` | `getting_started/04_multi_step_task.py` |

> 注：`custom-functions/` 目录名带连字符，仅作为脚本目录（不作为 Python 包 import），`uv run python examples/custom-functions/xxx.py` 可正常运行。

## 通用前置条件（所有 issue 共用）

1. `uv sync`
2. 启动 Chrome：`chrome --remote-debugging-port=9222`
3. 设置 key：`$env:ZHIPU_API_KEY = "你的key"`（PowerShell）
4. 运行：`uv run python examples/<类别>/<xxx>.py`

## 实施约定（所有 issue 共用）

- **按 browser-use 类别目录存放**：`examples/features/`、`examples/use-cases/`、`examples/getting_started/`、`examples/custom-functions/`（见上映射表），与 browser-use 结构对齐。
- **`sys.path` 索引**：子目录下的 example 用 `parents[2]` 定位 `src/`（`sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))`）；根目录 example 才用 `parents[1]`。仿现有 `examples/file_system/file_system.py`。
- **每个 example 自包含**：复制通用范式的前置检查（`sys.path.insert` + API key / Chrome 9222 检查），**不抽公共 helper** —— 保证可读、可独立运行。
- **每个 example 必须 `uv run` 实跑通过**才算完成（DoD）。
- **不改 `src/`**、**不为本批示例补 TreeWalker 能力**（缺能力项已在上游方案 §6 单列，属独立功能开发）。

## 进度看板

- [x] #1 结构化输出
- [ ] #2 浏览器能力演示
- [ ] #3 LLM 配置
- [ ] #4 安全与扩展机制
- [ ] #5 文件落地
- [ ] #6 并发
- [ ] #7 入门补全
