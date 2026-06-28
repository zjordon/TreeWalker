# browser-use examples → TreeWalker 移植方案

> 目的：判断 `D:\dev\git\z_jordon\browser-use\examples` 下哪些示例可以（直接或稍作适配）移植到本项目 TreeWalker，哪些因为 TreeWalker 不具备相应能力而无法移植，并给出可执行的移植配方，供后续逐个落地参考。
>
> 本文档只做"方案 / triage / 配方"，**不**创建 `examples/*.py`，也**不**改 TreeWalker 源码。后续实现时按本文档逐个落地。
>
> 源码核实版本：master @ `2f40d71`（v0.6.0）。

---

## 0. 速读结论

- **可直接移植（✅）**：约 20 个，集中在 `getting_started/`、`features/`、`use-cases/` 中"纯任务驱动"或只用到 TreeWalker 已有能力（结构化输出、多标签、save_as_pdf、scroll、fallback 模型、抽取小模型等）的示例。
- **需适配（⚠️）**：约 25 个，主要是自定义动作（API 形态不同）、敏感数据（仅扁平可用）、下载 / CSV（落地方式不同）、并发（有 caveat）等。
- **不可移植（❌）**：约 60+ 个，集中在 browser-use 专属能力：云 API、多 LLM provider（langchain）、浏览器自启动 / profile / 录制、域名黑白名单、提示词扩展点、历史复跑、MCP、vision、watchdog 事件钩子、外部集成（Gmail/Slack/Discord/1Password）等。

**最重要的一个结论**：browser-use 的 `Agent(task, llm=ChatXxx(...))` 会**自动启动浏览器**；TreeWalker **只连接一个已经用 `--remote-debugging-port=9222` 启动的 Chrome**，且 LLM 用 `LLMClient(LLMSettings(...))`（Anthropic 兼容协议，默认智谱 GLM）。**所有示例都要先过这一层改写**，详见 [§2 通用移植范式](#2-通用移植范式所有示例都要套)。

---

## 1. 两者的根本差异（决定能否移植的根因）

| 维度 | browser-use | TreeWalker | 对移植的影响 |
|---|---|---|---|
| 浏览器 | `Browser`/`BrowserSession`/`BrowserProfile`，可自启、headless、profile、录制、下载目录 | `BrowserSession(BrowserSettings(ws_url, cdp_host, cdp_port, ...))`，**只连接已启动的 Chrome（CDP）** | 所有"配置浏览器"类示例都要改写或不可移植 |
| LLM | langchain `ChatOpenAI/ChatAnthropic/...`，任意 provider | `LLMClient(LLMSettings(...))`，仅 Anthropic 兼容协议（改 `base_url` 换后端） | `models/*` 多数不可移植 |
| Agent 构造 | `Agent(task, llm, browser_session=..., ...)`，浏览器可选 | `Agent(task, llm: LLMClient, browser: BrowserSession, tools=None, settings=None, sensitive_data=None, output_model=None)`，**browser 必填** | 通用改写 |
| 结构化输出 | `output_model_schema=Model` | `output_model=Model` | 仅换参数名 |
| 敏感数据 | `{'x_name':'my_x_name'}`（扁平）或 `{'domain':{...}}`（按域嵌套） | `{'<占位符>':'<真实值>'}`（**仅扁平**，约定与 browser-use 扁平形式一致） | 扁平可移植，嵌套不可移植 |
| 系统提示词 | `extend_system_message=` / `override_system_message=` | **无扩展点**（`build_system_prompt` 固定模板） | 不可移植 |
| 历史 / 复跑 | `save_history` / `load_and_rerun` / `add_new_task`；`history.errors()/model_actions()/model_thoughts()` | **无**；`AgentHistoryList` 仅 `final_result()/is_done()/is_successful()` | 不可移植 |
| Judge | `use_judge=True, judge_llm=, ground_truth=`；`history.is_judged()/judgement()` | 内置 `JudgeSettings(enabled, model, ...)`（默认开，无 `ground_truth` 入参） | API 不同，不可直接移植 |
| 自定义动作 | `@controller.action(description=, domains=)`，按名注入参数 | `@tools.registry.action(name=, description=, param_model=, terminates=)`，handler 签名 `(params: dict, browser: BrowserSession)`；按页过滤走全局 `apply_page_filters` | 需适配 |
| 域名控制 | `allowed_domains` / `prohibited_domains` | **无** | 不可移植 |
| 录制 / 云 / 沙箱 | `record_video_dir`、Cloud API、`@sandbox` | **无** | 不可移植 |

---

## 2. 通用移植范式（所有示例都要套）

browser-use 原始（自动起浏览器）：

```python
from browser_use import Agent
from browser_use.llm import ChatOpenAI

llm = ChatOpenAI(model='gpt-4.1-mini')
agent = Agent(task='...', llm=llm)
await agent.run()
```

TreeWalker 等价（外部先开 Chrome 9222，连接之）：

```python
import asyncio
import logging
import sys
from pathlib import Path

# example 按类别子目录(features/ 等)存放 → parents[2]；放 examples/ 根则用 parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.config import load_settings


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    settings = load_settings()
    # 前置检查：API key + Chrome 已用 9222 启动
    if not settings.llm.api_key:
        print("Error: 请设置 ZHIPU_API_KEY 环境变量"); sys.exit(1)
    if not settings.browser.ws_url:
        print("Error: 请先用 chrome --remote-debugging-port=9222 启动 Chrome"); sys.exit(1)

    llm = LLMClient(settings.llm)               # Anthropic 兼容 LLM
    browser = BrowserSession(settings.browser)   # 连接到已启动的 Chrome（CDP）

    agent = Agent(
        task="...",          # 任务文本
        llm=llm,
        browser=browser,
        settings=settings.agent,
    )
    history = await agent.run()

    if history.is_done():
        print(history.final_result())


if __name__ == "__main__":
    asyncio.run(main())
```

> 前置条件（所有示例通用）：
> 1. `uv sync`
> 2. 启动 Chrome：`chrome --remote-debugging-port=9222`
> 3. `$env:ZHIPU_API_KEY = "你的key"`（PowerShell）
> 4. `uv run python examples/<xxx>.py`
>
> 该模板源自既有 `examples/basic_agent.py`、`examples/file_system/file_system.py`，可直接复用。

---

## 3. API 能力映射总表

| browser-use 用法 | TreeWalker 对应 | 状态 |
|---|---|---|
| `Agent(task, llm=ChatXxx(...))`（自动起浏览器） | `Agent(task, llm=LLMClient(...), browser=BrowserSession(...))` + 外部开 Chrome 9222 | 需改写（通用） |
| `output_model_schema=Posts` | `output_model=Posts` | ✅ 换名 |
| `sensitive_data={'x_name':'my_x_name'}`（扁平，key=占位符 value=真实值） | `sensitive_data={'<x_name>':'my_x_name'}`（约定一致，key=占位符 value=真实值） | ✅ 直接可用 |
| `sensitive_data={'domain':{...}}`（按域嵌套） | 无 | ❌ 不支持 |
| `flash_mode=True` | `LLMSettings(output_mode='flash')` | ✅ 换写法 |
| `use_thinking=False` | `LLMSettings(output_mode='standard')`（或 `thinking`） | ✅ 换写法 |
| `planner_llm=...` | `settings.enable_planning=True`（用主 LLM，无独立 planner） | ⚠️ 部分 |
| `BrowserProfile(minimum_wait_page_load_time=, wait_between_actions=)` | `BrowserSettings(page_settle_timeout=, wait_between_actions=)` | ⚠️ 部分 |
| `max_actions_per_step=` | `AgentSettings.max_actions_per_step` | ✅ |
| `@controller.action(description=, domains=)` | `@tools.registry.action(name=, description=, param_model=, terminates=)` + handler `(params, browser)` + `tools.apply_page_filters({动作名:[glob]})` | ⚠️ 适配 |
| `Browser(headless=)` / `from_system_chrome()` / `record_video_dir=` / `downloads_path=` / `allowed_domains=` / `prohibited_domains=` / `export_storage_state()` / `cross_origin_iframes=` | 无（CDP 连接、无启动 / 录制 / 域名白名单 / cookie 导出；下载走 `track_downloads`） | ❌ 不支持 |
| `use_judge=True, judge_llm=, ground_truth=` | 内置 `JudgeSettings(enabled, model)`（无 `ground_truth`、无 `history.is_judged()`） | ❌ API 不同 |
| `extend_system_message=` / `override_system_message=` | 无 | ❌ 不支持 |
| `agent.save_history()` / `load_and_rerun()` / `add_new_task()` | 无 | ❌ 不支持 |
| `history.errors()` / `model_actions()` / `model_thoughts()` | 无（仅 `final_result()` / `is_done()` / `is_successful()`） | ⚠️ 部分 |
| `initial_actions=[...]` | 无 | ❌ 不支持 |
| 多 LLM provider（langchain ChatX） | 仅 Anthropic 兼容（改 `LLMSettings.base_url`） | ⚠️ 多数不支持 |
| MCP（`skills.py`） | 无 | ❌ 不支持 |
| Cloud API（`cloud/*`、`sandbox/*`、`ChatBrowserUse`） | 无 | ❌ 无关 |
| `download_file`（`Browser(downloads_path=)`） | `AgentSettings(track_downloads=True)` → 下载文件作为 done 附件回传（落地路径由 Chrome 决定） | ⚠️ 适配 |
| `save_as_pdf`（任务驱动） | 内置 `save_as_pdf` 动作 | ✅ |
| 多标签（`navigate(new_tab=True)` / `switch_tab` / `close_tab`） | 同名动作（`browser/session.py`） | ✅ |
| 并发（`asyncio.gather` 多 agent） | 可创建多个 `BrowserSession`/`Agent` + `asyncio.gather`（共享同一 Chrome/CDP，并发需注意） | ⚠️ 适配 |

---

## 4. 全量 triage（按 browser-use 目录）

图例：✅ 可直接移植　⚠️ 需适配　❌ 不可移植（括号内为根因，详见 [§6](#6-不可移植项根因分组)）

### 根目录
| 示例 | 裁决 | 说明 |
|---|---|---|
| `simple.py` | ❌ | 用 `ChatBrowserUse`（云），无关 |
| `demo_mode_example.py` | ⚠️ | TreeWalker 有 highlight（`HighlightSettings`）但无 `demo_mode` 开关 |

### apps/
| 示例 | 裁决 | 说明 |
|---|---|---|
| `ad-use/ad_generator.py` | ❌ | 依赖 vision + Gemini 图像生成（缺 vision） |
| `msg-use/login.py`、`scheduler.py` | ⚠️ | 任务驱动可移植，但登录需自备凭据（扁平 sensitive_data） |
| `news-use/news_monitor.py` | ✅ | 纯任务驱动 |

### browser/
| 示例 | 裁决 | 说明 |
|---|---|---|
| `using_cdp.py` | ✅ | TreeWalker 本就是 CDP 原生 → 做成"连接方式"示例（见 [§5.9](#59-连接方式-browserusing_cdppy)） |
| `real_browser.py` | ⚠️ | 目标可达（连已开 Chrome 即用真实 profile），但 `Browser.from_system_chrome` API 不存在 |
| `playwright_integration.py` | ❌ | TreeWalker 用 cdp-use，非 playwright |
| `save_cookies.py` | ❌ | 无 `export_storage_state` |
| `parallel_browser.py` | ❌ | 无 `user_data_dir` / 浏览器自启 |
| `custom_headers.py` | ❌ | 依赖 `BaseWatchdog` + 事件总线（无 watchdog/钩子） |
| `cloud_browser.py` | ❌ | 云 |

### cloud/（5 个）
| 示例 | 裁决 | 说明 |
|---|---|---|
| `01_basic_task.py` … `05_search_api.py` | ❌ | browser-use 云 API，与本项目无关 |

### custom-functions/
| 示例 | 裁决 | 说明 |
|---|---|---|
| `file_upload.py` | ⚠️ | 改写为"自定义动作注册"示例（内置 `upload_file` 已存在）；配方见 [§5.6](#56-自定义动作注册-custom-functionsfile_uploadpy) |
| `action_filters.py` | ⚠️ | per-decorator `domains=` 无；改用全局 `tools.apply_page_filters` |
| `2fa.py`、`onepassword_2fa.py` | ⚠️ | 扁平 sensitive_data 可用；但内置 TOTP 生成无 → 需自写动作算验证码 |
| `actor_use.py`、`advanced_search.py`、`notification.py`、`cua.py` | ⚠️ | 自定义动作示例，改 API 即可 |
| `save_to_file_hugging_face.py` | ⚠️ | 用 `write_file` + `allowed_write_paths` 替代 agent 内置文件系统 |
| `parallel_agents.py` | ⚠️ | `asyncio.gather` 多会话，并发有 caveat（见 [§5.10](#510-并发-custom-functionsparallel_agentspy)） |

### features/
| 示例 | 裁决 | 说明 |
|---|---|---|
| `custom_output.py` | ✅ | `output_model=`；配方见 [§5.1](#51-结构化输出-featurescustom_outputpy) |
| `multi_tab.py` | ✅ | 纯任务驱动；配方见 [§5.2](#52-多标签-featuresmulti_tabpy) |
| `save_as_pdf.py` | ✅ | 内置 `save_as_pdf`；配方见 [§5.3](#53-存-pdf-featuressave_as_pdfpy) |
| `fallback_model.py` | ✅ | `LLMSettings(fallback=...)`；配方见 [§5.7](#57-fallback-模型-featuresfallback_modelpy) |
| `small_model_for_extraction.py` | ✅ | `AgentSettings(extract_llm=...)`；配方见 [§5.8](#58-抽取专用小模型-featuressmall_model_for_extractionpy) |
| `scrolling_page.py` | ✅ | 内置 `scroll` |
| `download_file.py` | ⚠️ | `track_downloads=True` + done 附件（无 `downloads_path`）；配方见 [§5.4](#54-下载文件-featuresdownload_filepy) |
| `csv_file_generation.py` | ⚠️ | 用 `write_file` 写 CSV（无 `agent.file_system` / 自动规范化） |
| `sensitive_data.py` | ⚠️ | 仅扁平可用；配方见 [§5.5](#55-敏感数据扁平-featuressensitive_datapy) |
| `process_agent_output.py` | ⚠️ | 历史 API 不同（无 `errors/model_actions/model_thoughts`），只能取 `final_result()` |
| `stop_externally.py` | ⚠️ | 有 pause/resume + Ctrl-C + stdin 控制，但 API 不同 |
| `parallel_agents.py` | ⚠️ | 共享会话并发，caveat 同上 |
| `judge_trace.py` | ❌ | judge API 不同（无 `ground_truth`/`history.is_judged()`） |
| `follow_up_task.py`、`follow_up_tasks.py` | ❌ | 无 `add_new_task` / keep_alive 续跑链 |
| `rerun_history.py` | ❌ | 无 `save_history`/`load_and_rerun` |
| `initial_actions.py` | ❌ | 无 `initial_actions` |
| `custom_system_prompt.py` | ❌ | 无 `extend/override_system_message` |
| `blocked_domains.py`、`restrict_urls.py`、`large_blocklist.py` | ❌ | 无域名黑白名单 |
| `secure.py` | ❌ | Azure OpenAI（多 LLM）+ 域名白名单 |
| `video_recording.py` | ❌ | 无 `record_video_dir` |
| `add_image_context.py` | ❌ | 无 vision 透传入参 |

### file_system/
| 示例 | 裁决 | 说明 |
|---|---|---|
| `file_system.py`、`alphabet_earnings.py`、`excel_sheet.py` | ✅ 已完成 | TreeWalker 已有 `examples/file_system/{file_system,alphabet_earnings,excel_sheet}.py` |

### getting_started/
| 示例 | 裁决 | 说明 |
|---|---|---|
| `01_basic_search.py` | ❌ 跳过 | 与现有 `examples/basic_agent.py` 重复 |
| `02_form_filling.py` | ✅ | 纯任务驱动（httpbin 表单） |
| `03_data_extraction.py` | ✅ | 纯任务驱动（抽取） |
| `04_multi_step_task.py` | ✅ | 纯任务驱动（多步） |
| `05_fast_agent.py` | ⚠️ | `output_mode='flash'` + 时延可移植；`extend_system_message` 丢弃 |

### integrations/
| 示例 | 裁决 | 说明 |
|---|---|---|
| `gmail_2fa_integration.py`、`agentmail/*` | ⚠️ 超核心范围 | 自定义动作部分可适配，但需外部 OAuth/服务端，重 |
| `slack/*`、`discord/*` | ⚠️ 超核心范围 | 需外部 bot + Web 服务端，重 |

### models/
| 示例 | 裁决 | 说明 |
|---|---|---|
| 绝大多数（`gpt-*`、`gemini*`、`claude-*`、`aws`、`azure_openai`、`ollama`、`qwen`、`langchain/*` 等） | ❌ | langchain 多 provider；TreeWalker 仅 Anthropic 兼容 |
| 指向 Anthropic 兼容端点的（如自建/代理） | ⚠️ | 可用 `LLMSettings(base_url=...)` 适配 |

### observability/
| 示例 | 裁决 | 说明 |
|---|---|---|
| `openLLMetry.py` | ❌ | Traceloop SDK；TreeWalker 有自己的 EventBus/可观测性（`enable_observability`），API 不同 |

### sandbox/
| 示例 | 裁决 | 说明 |
|---|---|---|
| `example.py`、`structured_output.py` | ❌ | 云沙箱 |

### ui/
| 示例 | 裁决 | 说明 |
|---|---|---|
| `command_line.py` | ❌ | TreeWalker 自带 TUI（`src/tree_walker/tui/app.py`） |
| `streamlit_demo.py`、`gradio_demo.py` | ⚠️ 超核心范围 | 可重包装，但重 |

### use-cases/
| 示例 | 裁决 | 说明 |
|---|---|---|
| `phone_comparison.py` | ✅ | `output_model` + CDP 跨站抽取；配方见 [§5.11](#511-真实世界结构化对比-use-casesphone_comparisonpy) |
| `shopping.py`、`buy_groceries.py`、`check_appointment.py`、`find_influencer_profiles.py`、`pcpartpicker.py` | ✅ | 纯任务驱动 |
| `extract_pdf_content.py` | ✅ | navigate 到 PDF / `save_as_pdf` / `read_file`（支持 PDF） |
| `apply_to_job.py` | ⚠️ | 自定义上传动作 + `allowed_upload_paths`；iframe 有支持但无 `cross_origin_iframes` 开关 |
| `captcha.py` | ❌ | 需 vision |
| `onepassword.py` | ❌ | 1Password 集成 |

---

## 5. 推荐移植清单 + 详细配方

> 以下 9 个（+ 连接方式 + 并发共 11 个）覆盖最常见能力，且**不与现有 examples 重复**。每个配方给出 browser-use 原始片段 → TreeWalker 改写片段 → 适配注意点。所有片段都假设已套用 [§2 通用范式](#2-通用移植范式所有示例都要套) 的连接/前置检查。

### 5.1 结构化输出 (`features/custom_output.py`)

**browser-use 原始：**
```python
agent = Agent(task='Go to hackernews show hn and give me the first 5 posts', llm=model, output_model_schema=Posts)
history = await agent.run()
parsed = Posts.model_validate_json(history.final_result())
```

**TreeWalker 改写：**
```python
agent = Agent(
    task="Go to https://news.ycombinator.com/show and give me the first 5 posts",
    llm=llm, browser=browser, settings=settings.agent,
    output_model=Posts,        # 仅参数名不同：output_model_schema → output_model
)
history = await agent.run()
result = history.final_result()
if result:
    parsed = Posts.model_validate_json(result)
    for p in parsed.posts:
        print(p.post_title, p.post_url)
```

**注意点**：`output_model` 给定时，TreeWalker 的 `done` 工具切到"变体 B"（LLM 只填 `data`，`success`/`files_to_display` 对 LLM 隐藏，见 `tools/registry.py`）。`final_result()` 返回该结构对应的 JSON 字符串，可直接 `model_validate_json`。

### 5.2 多标签 (`features/multi_tab.py`)

**browser-use 原始：**
```python
agent = Agent(task='open 3 tabs with elon musk, sam altman, and steve jobs, then go back to the first and stop', llm=llm)
```

**TreeWalker 改写：** 几乎只改连接方式，任务文本不变：
```python
agent = Agent(
    task="Open 3 tabs with Elon Musk, Sam Altman, and Steve Jobs, then go back to the first and stop",
    llm=llm, browser=browser, settings=settings.agent,
)
history = await agent.run()
```

**注意点**：TreeWalker 有 `navigate(url, new_tab=True)`、`switch_tab(target_id)`、`close_tab(target_id)` 三个动作（`browser/session.py:2545`），agent 会按任务自行调度。

### 5.3 存 PDF (`features/save_as_pdf.py`)

**browser-use 原始：** 任务驱动，调用内置 save-as-pdf。

**TreeWalker 改写：**
```python
agent = Agent(
    task="Go to https://en.wikipedia.org/wiki/Browser_automation and use save_as_pdf "
         "to save the page as a PDF to C:/tmp/browser_automation.pdf",
    llm=llm, browser=browser, settings=settings.agent,
)
history = await agent.run()
```

**注意点**：内置 `save_as_pdf` 动作支持 `path/paper_format/landscape/scale`。经核实，`save_as_pdf` 写盘路径**不受** `allowed_write_paths` 约束（白名单只作用于 `write_file`/`replace_file`，`read_file` 受 `allowed_read_paths` 约束）。

### 5.4 下载文件 (`features/download_file.py`)

**browser-use 原始：**
```python
browser = Browser(downloads_path='~/Downloads/tmp')
agent = Agent(task='...', llm=llm, browser=browser)
```

**TreeWalker 改写：**
```python
from dataclasses import replace

agent_settings = replace(settings.agent, track_downloads=True)   # 开启下载跟踪
agent = Agent(
    task="Go to <含下载链接的页面> and download <目标文件>",
    llm=llm, browser=browser, settings=agent_settings,
)
history = await agent.run()
```

**注意点**：TreeWalker **没有** `downloads_path` —— 下载文件落地到 **Chrome 自身的下载目录**，无法在 TreeWalker 侧指定。开启 `track_downloads=True` 后，已下载文件会作为 `done` 的附件回传；配合 `AgentSettings(display_files_in_done_text=True)` 可把附件内容内联进 `final_result()`。

### 5.5 敏感数据（扁平）(`features/sensitive_data.py`)

**browser-use 原始（简单形式）：**
```python
sensitive_data = {'x_name': 'my_x_name', 'x_password': 'my_x_password'}
agent = Agent(task='Go to ... and put <x_name>/<x_password> in the fields', llm=llm, sensitive_data=sensitive_data)
```

**TreeWalker 改写：**
```python
# 约定与 browser-use 扁平形式一致：key=占位符，value=真实值
sensitive_data = {"<x_name>": "my_x_name", "<x_password>": "my_x_password"}
agent = Agent(
    task="Go to https://httpbin.org/forms/post and put <x_name>/<x_password> in the relevant fields",
    llm=llm, browser=browser, settings=settings.agent,
    sensitive_data=sensitive_data,
)
history = await agent.run()
```

**注意点（机制，已核实 `agent.py:87` + `llm/client.py:125`）**：
- 字典约定 = `{占位符: 真实值}`，**与 browser-use 一致，无需翻转**。
- 发往 LLM 前，真实值被替换为占位符（LLM 看不到真实值）；LLM 输出动作里的占位符会在执行前还原成真实值。
- **仅扁平形式可用**；browser-use 的"按域嵌套" `{'httpbin.org': {...}}` **不支持**（`Agent.sensitive_data` 是 `dict[str,str]`）。

### 5.6 自定义动作注册 (`custom-functions/file_upload.py`)

**browser-use 原始：**
```python
tools = Tools()

@tools.action(description='Upload a file')
async def upload_file(file_path: str, browser_session: BrowserSession):
    ...  # 按名注入参数，含 domains= 可按域过滤

agent = Agent(task='...', llm=llm, tools=tools)
```

**TreeWalker 改写（以一个通用"回显"动作为例，演示注册形态）：**
```python
from pydantic import BaseModel, Field
from tree_walker import Agent, ActionResult, BrowserSession, LLMClient, Tools

class EchoParams(BaseModel):
    message: str = Field(..., description="要回显的文本")

def build_tools() -> Tools:
    tools = Tools()          # 先注册默认 22 个动作（参数全可省，见 tools/actions.py:357）

    @tools.registry.action(
        name="echo",                                   # 必填：动作名
        description="回显一段文本，用于演示自定义动作注册。",
        param_model=EchoParams,                        # 必填：参数 Pydantic 模型
        terminates=False,
    )
    async def _echo(params: dict, browser: BrowserSession):
        # 注意签名：TreeWalker 按位置注入 (params: dict, browser)，不是按名注入
        return ActionResult(extracted_content=f"echo: {params['message']}")

    return tools

agent = Agent(
    task="...", llm=llm, browser=browser, settings=settings.agent,
    tools=build_tools(),     # 传入自定义 Tools
)
history = await agent.run()
```

**注意点**：
- handler 签名固定为 `async def fn(params: dict, browser: BrowserSession) -> ActionResult | str | None`（`tools/actions.py:395`）—— **不是** browser-use 的按名注入。
- TreeWalker **没有** per-decorator 的 `domains=`；按页过滤改用全局：`tools.apply_page_filters({"动作名": ["*.example.com"]})`，或经 `AgentSettings.action_page_filters`（env `AGENT_ACTION_PAGE_FILTERS`，JSON）。
- 内置 `upload_file` 已存在且受 `allowed_upload_paths` 白名单约束，无需为上传另写动作；本配方侧重"如何注册自定义动作"的范式。

### 5.7 fallback 模型 (`features/fallback_model.py`)

**browser-use 原始：** `Agent(..., fallback_model=...)`。

**TreeWalker 改写：**
```python
from tree_walker.config import LLMSettings, FallbackLLMSettings

llm_settings = LLMSettings(
    model="glm-5.1",
    api_key=settings.llm.api_key,
    base_url=settings.llm.base_url,
    fallback=FallbackLLMSettings(            # 主模型限流/出错时自动回退
        model="glm-4-flash",
        api_key=settings.llm.api_key,
        base_url=settings.llm.base_url,
    ),
)
llm = LLMClient(llm_settings)
agent = Agent(task="...", llm=llm, browser=browser, settings=settings.agent)
```

**注意点**：fallback 仅限"Anthropic 兼容"后端；不可像 browser-use 那样回退到不同 provider 的 langchain 模型。也可全用 env 配置（`FALLBACK_LLM_MODEL` 等，见 `config.py:load_settings`）。

### 5.8 抽取专用小模型 (`features/small_model_for_extraction.py`)

**browser-use 原始：** `Agent(..., page_extraction_llm=...)`。

**TreeWalker 改写：**
```python
from dataclasses import replace
from tree_walker.config import LLMSettings

agent_settings = replace(
    settings.agent,
    extract_llm=LLMSettings(               # extract 工具用更便宜/更快的模型
        model="glm-4-flash",
        api_key=settings.llm.api_key,
        base_url=settings.llm.base_url,
    ),
)
agent = Agent(task="...", llm=llm, browser=browser, settings=agent_settings)
```

**注意点**：`extract_llm=None` 时复用主 LLM。也可经 env（`AGENT_EXTRACT_MODEL` 等）。`extract` 工具还支持 `AgentSettings.extraction_schema`（结构化抽取的 JSON Schema）。

### 5.9 连接方式 (`browser/using_cdp.py`)

**browser-use 原始：**
```python
browser_session = BrowserSession(browser_profile=BrowserProfile(cdp_url='http://localhost:9222', is_local=True))
agent = Agent(task='...', llm=llm, browser_session=browser_session)
```

**TreeWalker 改写：** 这正是 TreeWalker 的默认模型，可做成"连接方式"示例，展示几种连接参数：
```python
from tree_walker.config import BrowserSettings

# 方式 A：自动从 http://<host>:<port>/json/version 取 ws_url（load_settings 的默认行为）
settings = load_settings()                       # 读 CDP_HOST/CDP_PORT/CDP_WS_URL

# 方式 B：显式给定 ws_url（已知 webSocketDebuggerUrl 时）
browser = BrowserSession(BrowserSettings(
    ws_url="ws://127.0.0.1:9222/devtools/browser/<id>",
    cdp_host="127.0.0.1", cdp_port=9222,
))

agent = Agent(task="Visit https://duckduckgo.com and search for 'browser-use founders'",
              llm=llm, browser=browser, settings=settings.agent)
history = await agent.run()
```

**注意点**：`load_settings()` 已内置"从 CDP 端点拉取 ws_url"的逻辑（`config.py:_fetch_ws_url`），通常只需 `BrowserSession(settings.browser)`。`BrowserSession` 用完无需手动 `kill()`（browser-use 的 `browser_session.kill()` 在 TreeWalker 无对应；TreeWalker 不拥有浏览器进程）。

### 5.10 并发 (`custom-functions/parallel_agents.py`)

**TreeWalker 改写（多 agent 共享同一 Chrome）：**
```python
import asyncio

async def run_one(task: str, settings) -> None:
    # 每个 agent 用独立 BrowserSession（连同一个 Chrome）
    browser = BrowserSession(settings.browser)
    agent = Agent(task=task, llm=LLMClient(settings.llm), browser=browser, settings=settings.agent)
    history = await agent.run()
    print(task, "=>", history.is_done())

await asyncio.gather(
    run_one("task A ...", settings),
    run_one("task B ...", settings),
    run_one("task C ...", settings),
)
```

**注意点 / caveat（重要）**：TreeWalker 不内置并发原语；多个 `BrowserSession` 连同一个 Chrome/CDP 时，**共享同一个浏览器上下文与标签页空间**，互相可能干扰（如标签切换、焦点）。如需强隔离，应给每个并发实例起**独立的 Chrome（独立 `--remote-debugging-port`）**再各自连，而不是用 browser-use 的 `user_data_dir`（TreeWalker 不启动浏览器）。

### 5.11 真实世界结构化对比 (`use-cases/phone_comparison.py`)

**TreeWalker 改写（跨站抽取 + 结构化输出）：**
```python
class PhonePrice(BaseModel):
    site: str
    price: str
    url: str

class PriceComparison(BaseModel):
    model_name: str
    prices: list[PhonePrice]

agent = Agent(
    task="Compare the price of <某型号手机> across 3 shopping sites and return structured data",
    llm=llm, browser=browser, settings=settings.agent,
    output_model=PriceComparison,
)
history = await agent.run()
parsed = PriceComparison.model_validate_json(history.final_result())
```

**注意点**：本质是 [§5.1 结构化输出](#51-结构化输出-featurescustom_outputpy) + 多站导航的组合；CDP 连接即可，无需额外配置。

---

## 6. 不可移植项根因分组

按"TreeWalker 缺哪个能力"归类，便于日后补能力时回填对应示例：

| 缺失能力 | 受影响示例 |
|---|---|
| 浏览器自启动 / profile / 录制 / 下载目录 / cookie 导出 / 域名黑白名单 / `cross_origin_iframes` 开关 | `browser/real_browser`、`save_cookies`、`parallel_browser`、`playwright_integration`、`features/video_recording`、`features/blocked_domains`、`restrict_urls`、`large_blocklist`、`secure`、`features/download_file`(部分)、`use-cases/apply_to_job`(部分) |
| 历史 / 复跑（`save_history`/`load_and_rerun`/`add_new_task`）+ 历史方法少 | `features/rerun_history`、`follow_up_task`、`follow_up_tasks`、`process_agent_output`(部分) |
| 提示词扩展点（`extend/override_system_message`） | `features/custom_system_prompt`、`getting_started/05_fast_agent`(部分) |
| MCP | `models/skills` |
| vision 透传 | `features/add_image_context`、`use-cases/captcha`、`apps/ad-use/ad_generator` |
| 云 / 沙箱 / 外部集成 | `cloud/*`、`sandbox/*`、`observability/openLLMetry`、`integrations/*`、`ui/*` |
| 多 LLM provider（langchain） | `models/*`（绝大多数） |
| Judge API 不同（无 `ground_truth`/`history.is_judged()`） | `features/judge_trace` |
| 初始动作（`initial_actions`） | `features/initial_actions` |
| watchdog / 事件钩子（`BaseWatchdog`/`on_step_*`） | `browser/custom_headers` |
| 内置 TOTP（`bu_2fa_code`） | `custom-functions/2fa`、`onepassword_2fa`（可自写动作替代） |

---

## 7. 后续步骤建议

> ✅ **已细化落地**：下面的分批已拆成 **7 个主题 issue 的可直接执行方案文档**，就放在本目录（`examples-implementation/`，含完整 `.py` 代码 + DoD + 验证方式），并附索引 [`README.md`](README.md)。后续实施时直接打开对应文档照做即可，无需再设计。

1. **第一批落地（高价值、低风险，均为 ✅）**：`features/custom_output`、`features/multi_tab`、`features/save_as_pdf`、`features/fallback_model`、`features/small_model_for_extraction`、`use-cases/phone_comparison`，外加 `getting_started/{02,03,04}`。
2. **第二批（⚠️ 适配）**：`features/sensitive_data`（扁平）、`features/download_file`、`custom-functions/file_upload`（自定义动作范式）、`custom-functions/parallel_agents`（并发）。
3. 每个示例落地后：套 [§2 通用范式](#2-通用移植范式所有示例都要套)，`uv run python examples/<xxx>.py` 实跑验证；与既有 `examples/basic_agent.py`、`examples/file_system/*`、`examples/multi_act_demo.py`、`examples/upload_file*.py` 风格保持一致（含 `sys.path.insert` 前置、API key 与 Chrome 9222 前置检查）。
4. **不在本方案范围**：不为"不可移植"项在 TreeWalker 补能力（如 `extend_system_message`、域名白名单、历史复跑）—— 那些是独立的功能开发，本文档仅记录缺口。
