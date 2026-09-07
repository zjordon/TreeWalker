"""Example: Run the agent with vision mode ON and skill injection OFF.

视觉模式组合示例（screenshot.md 阶段二 / issue #175）：
  - use_vision=True           —— 每步 state 消息附降采样截图（Anthropic image block）
  - LLM_MODEL=glm-5.3-flash   —— 视觉门要求模型在已知视觉名单内（文本模型收图
                                  不报错只静默致盲，名单判定在客户端）
  - enable_skill_injection=False —— 关闭站点级 [Domain Skill] 注入（任务级
                                  enable_task_skill_injection 本就默认关）
  - use_vision 默认 off 是评测红线：默认关闭时行为与纯文本完全一致

Prerequisites:
1. Install dependencies:  uv sync
2. Start Chrome with remote debugging:
   chrome --remote-debugging-port=9222
3. Set ZHIPU_API_KEY environment variable

Usage:
    set ZHIPU_API_KEY=your_key
    uv run python examples/vision_mode.py
"""

import asyncio
import logging
import sys
from dataclasses import replace

sys.path.insert(0, f"{__file__}/../src")

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.config import AgentSettings, load_settings, model_supports_vision


async def main():
    settings = load_settings()

    if not settings.llm.api_key:
        print("Error: Set ZHIPU_API_KEY environment variable")
        sys.exit(1)

    if not settings.browser.ws_url:
        print("Error: Cannot connect to Chrome. Is it running with --remote-debugging-port=9222?")
        sys.exit(1)

    # 视觉门要求视觉模型：显式切到 glm-5.3-flash（GLM-5 系首个原生多模态）。
    # 若 env LLM_MODEL 已配成其它视觉模型（claude-* / glm-*v*）也可，这里统一
    # 覆盖保证示例开箱即跑；model_supports_vision 兜一道校验。
    llm_settings = replace(settings.llm, model="glm-5.3-flash")
    if not model_supports_vision(llm_settings.model):
        print(f"Error: model '{llm_settings.model}' is not in the vision model list")
        sys.exit(1)

    llm = LLMClient(llm_settings)
    browser = BrowserSession(settings.browser)

    # 视觉受益型任务：答案在渲染层（logo 颜色/布局），DOM 文本只能转述
    task = (
        "打开 https://www.bilibili.com/ 并观察首页顶部区域，"
        "描述顶部导航/logo 的主色调是什么颜色、整体布局分几块，"
        "最后用 done 动作把观察结果写在 extracted_content 里。"
    )

    agent_settings = AgentSettings(
        max_steps=settings.agent.max_steps,
        max_failures=settings.agent.max_failures,
        llm_timeout=settings.agent.llm_timeout,
        action_timeout=settings.agent.action_timeout,
        reconnect_timeout=settings.agent.reconnect_timeout,
        truncation=settings.agent.truncation,
        enable_planning=True,
        # ── 本示例的两个主角 ──
        use_vision=True,                      # 每步带降采样截图（默认 False=评测红线）
        enable_skill_injection=False,         # 关闭站点级 skill 注入（默认 True）
        # 截图降采样目标 (w, h)。直连构造不走 load_settings 的模型自适应默认，
        # 显式给 (1400, 850)（对齐 browser-use）；None = 不缩放（原图可能很大）。
        llm_screenshot_size=(1400, 850),
    )

    agent = Agent(
        task=task,
        llm=llm,
        browser=browser,
        settings=agent_settings,
    )

    logging.basicConfig(level=logging.INFO)

    history = await agent.run()

    if history.is_done():
        print(f"\nTask completed: {history.final_result()}")
    else:
        print("\nTask did not complete within max steps")


if __name__ == "__main__":
    asyncio.run(main())
