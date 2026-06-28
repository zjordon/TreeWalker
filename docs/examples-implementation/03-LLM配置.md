# Issue #3 · LLM 配置（fallback / 抽取小模型 / flash 模式）

> Epic: browser-use examples 移植 ｜ 上游方案: [`../browser-use-examples移植方案.md`](browser-use-examples移植方案.md) §5.7 / §5.8
> 实施时：创建下方"目标文件"列出的 `.py`（完整代码已给出），逐个 `uv run` 实跑。

## Issue 正文（建 issue 时用）

**标题**：`feat(examples): LLM 配置（fallback/抽取小模型/flash 模式）—— 移植自 browser-use`

**正文**：
移植 browser-use 中"调 LLM 侧参数"的示例，全部用 TreeWalker 的 `LLMSettings` / `AgentSettings` / `BrowserSettings` 实现：
- **fallback 模型**：`LLMSettings(fallback=FallbackLLMSettings(...))`，主模型限流/出错时自动回退（仅限 Anthropic 兼容后端）。
- **抽取专用小模型**：`AgentSettings(extract_llm=LLMSettings(...))`，`extract` 工具用更便宜的模型。
- **flash 快速模式**：`LLMSettings(output_mode="flash")` + `BrowserSettings(wait_between_actions=, page_settle_timeout=)`。browser-use 原版还用了 `extend_system_message=SPEED_OPTIMIZATION_PROMPT`，**TreeWalker 无提示词扩展点 → 丢弃该部分**（见上游方案 §6）。

任务清单：
- [ ] `examples/features/fallback_model.py` ← `features/fallback_model.py`
- [ ] `examples/features/extraction_small_model.py` ← `features/small_model_for_extraction.py`
- [ ] `examples/getting_started/fast_agent.py` ← `getting_started/05_fast_agent.py`（仅保留 flash + 时延）

DoD：三个文件均可 `uv run` 实跑，分别体现 fallback 接线 / 抽取小模型 / flash 模式。

## 目标文件

| 目标 | 来源（browser-use） |
|---|---|
| `examples/features/fallback_model.py` | `examples/features/fallback_model.py` |
| `examples/features/extraction_small_model.py` | `examples/features/small_model_for_extraction.py` |
| `examples/getting_started/fast_agent.py` | `examples/getting_started/05_fast_agent.py` |

## 通用前置

见 [`README.md`](README.md)「通用前置条件」。本 issue 三个示例都用 `dataclasses.replace` 在 `load_settings()` 基础上覆盖个别字段，避免硬编码 key/base_url。

## 实施配方

### 3.1 `examples/features/fallback_model.py`

**完整代码**（直接保存为 `examples/features/fallback_model.py`）：

```python
"""Example: fallback 模型。移植自 browser-use/examples/features/fallback_model.py。

主模型限流/出错时自动回退到更便宜/更快的模型。
TreeWalker 用 LLMSettings(fallback=FallbackLLMSettings(...))；仅限 Anthropic 兼容后端
（不可像 browser-use 那样回退到不同 provider 的 langchain 模型）。
也可全用 env 配置：FALLBACK_LLM_MODEL / FALLBACK_LLM_API_KEY 等。
前置：uv sync / chrome --remote-debugging-port=9222 / $env:ZHIPU_API_KEY="..."
运行：uv run python examples/features/fallback_model.py
"""
import asyncio
import logging
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.config import FallbackLLMSettings, load_settings


TASK = "Go to https://news.ycombinator.com/ and list the top 3 story titles."


async def main() -> None:
	logging.basicConfig(level=logging.INFO)
	settings = load_settings()
	if not settings.llm.api_key:
		print("Error: 请设置 ZHIPU_API_KEY 环境变量")
		sys.exit(1)
	if not settings.browser.ws_url:
		print("Error: 请先用 chrome --remote-debugging-port=9222 启动 Chrome")
		sys.exit(1)

	# 主模型不变，挂一个 fallback（按需改成你可用的便宜模型）
	llm_settings = replace(
		settings.llm,
		fallback=FallbackLLMSettings(
			model="glm-4-flash",
			api_key=settings.llm.api_key,
			base_url=settings.llm.base_url,
		),
	)
	llm = LLMClient(llm_settings)
	browser = BrowserSession(settings.browser)
	agent = Agent(task=TASK, llm=llm, browser=browser, settings=settings.agent)
	history = await agent.run()

	if history.is_done():
		print(history.final_result())


if __name__ == "__main__":
	asyncio.run(main())
```

**适配注意点**：`FallbackLLMSettings` 从 `tree_walker.config` 导入（`tree_walker` 顶层 `__init__` 未导出它）。`fallback` 仅对"Anthropic 兼容"后端生效。

### 3.2 `examples/features/extraction_small_model.py`

**完整代码**（直接保存为 `examples/features/extraction_small_model.py`）：

```python
"""Example: 抽取专用小模型。移植自 browser-use/examples/features/small_model_for_extraction.py。

让 extract 工具用更便宜/更快的模型（browser-use 的 page_extraction_llm）。
TreeWalker 用 AgentSettings(extract_llm=LLMSettings(...))；为 None 时复用主 LLM。
也可用 env：AGENT_EXTRACT_MODEL / AGENT_EXTRACT_API_KEY 等。
前置：uv sync / chrome --remote-debugging-port=9222 / $env:ZHIPU_API_KEY="..."
运行：uv run python examples/features/extraction_small_model.py
"""
import asyncio
import logging
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.config import LLMSettings, load_settings


TASK = (
	"Go to https://news.ycombinator.com/ and use the extract action to get the "
	"titles and points of the top 5 stories."
)


async def main() -> None:
	logging.basicConfig(level=logging.INFO)
	settings = load_settings()
	if not settings.llm.api_key:
		print("Error: 请设置 ZHIPU_API_KEY 环境变量")
		sys.exit(1)
	if not settings.browser.ws_url:
		print("Error: 请先用 chrome --remote-debugging-port=9222 启动 Chrome")
		sys.exit(1)

	# extract 工具换用小模型（按需改成你可用的便宜模型）
	agent_settings = replace(
		settings.agent,
		extract_llm=LLMSettings(
			model="glm-4-flash",
			api_key=settings.llm.api_key,
			base_url=settings.llm.base_url,
		),
	)
	llm = LLMClient(settings.llm)
	browser = BrowserSession(settings.browser)
	agent = Agent(task=TASK, llm=llm, browser=browser, settings=agent_settings)
	history = await agent.run()

	if history.is_done():
		print(history.final_result())


if __name__ == "__main__":
	asyncio.run(main())
```

**适配注意点**：`extract_llm` 是 `AgentSettings` 字段（`config.py`）。任务里明确要求"use the extract action"，确保走的是 extract 工具（从而用上小模型）。

### 3.3 `examples/getting_started/fast_agent.py`

**完整代码**（直接保存为 `examples/getting_started/fast_agent.py`）：

```python
"""Example: flash 快速模式。移植自 browser-use/examples/getting_started/05_fast_agent.py（仅保留可移植部分）。

browser-use 原版三件套：flash_mode + 时延(minimum_wait_page_load_time/wait_between_actions)
+ extend_system_message(SPEED_OPTIMIZATION_PROMPT)。
TreeWalker 对应：output_mode='flash' + BrowserSettings(page_settle_timeout/wait_between_actions)。
「extend_system_message」TreeWalker 无此扩展点 → 丢弃（见上游方案 §6）。
前置：uv sync / chrome --remote-debugging-port=9222 / $env:ZHIPU_API_KEY="..."
运行：uv run python examples/getting_started/fast_agent.py
"""
import asyncio
import logging
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.config import load_settings


TASK = "Go to https://news.ycombinator.com/ and tell me the top 3 story titles."


async def main() -> None:
	logging.basicConfig(level=logging.INFO)
	settings = load_settings()
	if not settings.llm.api_key:
		print("Error: 请设置 ZHIPU_API_KEY 环境变量")
		sys.exit(1)
	if not settings.browser.ws_url:
		print("Error: 请先用 chrome --remote-debugging-port=9222 启动 Chrome")
		sys.exit(1)

	# flash 模式 + 收紧页面等待/动作间隔
	llm_settings = replace(settings.llm, output_mode="flash")
	browser_settings = replace(
		settings.browser,
		wait_between_actions=0.1,
		page_settle_timeout=0.5,
	)
	llm = LLMClient(llm_settings)
	browser = BrowserSession(browser_settings)
	agent = Agent(task=TASK, llm=llm, browser=browser, settings=settings.agent)
	history = await agent.run()

	if history.is_done():
		print(history.final_result())


if __name__ == "__main__":
	asyncio.run(main())
```

**适配注意点**：`output_mode` 必须是 `"standard"|"flash"|"thinking"` 之一（`config.py:load_settings` 会校验）。`page_settle_timeout` 过小可能导致页面没加载完就抓取——若抓取不稳，回调大到 1.0~2.0。

## 验收标准（DoD）

- 三个 `.py` 文件分别存在于 `examples/features/`（fallback_model / extraction_small_model）与 `examples/getting_started/`（fast_agent）下。
- `fallback_model.py`：能跑通（fallback 仅在主模型出错时触发，正常情况下看不到回退日志，接线正确即可）。
- `extraction_small_model.py`：运行后输出 extract 到的故事标题/分数。
- `fast_agent.py`：运行走 flash 模式并输出结果。

## 验证方式

```
uv run python examples/features/fallback_model.py
uv run python examples/features/extraction_small_model.py
uv run python examples/getting_started/fast_agent.py
```

## 风险 / caveat

- `glm-4-flash` 等小模型名需是你 key 可用的模型；不可用时换成你账号下可用的便宜模型。
- fallback 仅在主模型限流/出错时才触发，正常验证看不到效果——这是预期行为，不是 bug。
- `fast_agent.py` 主动丢弃了 browser-use 的 `extend_system_message` 部分（TreeWalker 不支持）；若日后 TreeWalker 增加提示词扩展点，可再补回。本 issue 不涉及 `src/` 改动。
