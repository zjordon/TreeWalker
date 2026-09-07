"""Example: Upload a video to Douyin Creator Center as draft — vision ON, skill OFF.

examples/upload_file.py 的对照变体（screenshot.md 阶段二 / issue #175）：
  - use_vision=True + glm-5.3-flash  —— 每步带降采样截图（视觉门要求视觉模型）
  - enable_skill_injection=False     —— 关闭 creator.douyin.com 的站点级 skill
                                        （库里有这条 skill，关掉才有对照意义）
对照矩阵（同一抖音上传任务）：
  | 示例                        | 视觉 | skill |
  | upload_file.py（原版）       | ✗    | ✓     |
  | upload_file_vision.py（本）  | ✓    | ✗     |
看点：视觉能否补上领域知识的缺（封面选择/自主声明/合集等页面交互的视觉线索）。

Prerequisites:
1. Install dependencies:  uv sync
2. Start Chrome with remote debugging:
   chrome --remote-debugging-port=9222
3. Set ZHIPU_API_KEY environment variable

Usage:
    set ZHIPU_API_KEY=your_key
    uv run python examples/upload_file_vision.py
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

    # 视觉门要求模型在已知视觉名单内（文本模型收图不报错只静默致盲）——
    # 显式切到 glm-5.3-flash，保证示例开箱即跑
    llm_settings = replace(settings.llm, model="glm-5.3-flash")
    if not model_supports_vision(llm_settings.model):
        print(f"Error: model '{llm_settings.model}' is not in the vision model list")
        sys.exit(1)

    llm = LLMClient(llm_settings)
    browser = BrowserSession(settings.browser)

    task = (
        "帮我到抖音创作者中心发一个视频，信息如下，先暂存为草稿不要直接发布，发布完后回到发布视频界面就算完成了不要再点继续编辑进去重复编辑\n"
        "\n"
        "抖音创作者中心网址:https://creator.douyin.com/\n"
        "我要发的视频在'D:\\Videos\\test\\final\\2026-04-29-20-41-59.mp4'\n"
        "作品描述中的主标题为：ai浏览器第五期-browse-use,副标题为:'browse-use体验及技术原理'\n"
        "添加合集到'AI浏览器合集'\n"
        "自主声明选择'无需添加自主声明'\n"
        "横封面图片在'D:\\dev\\git\\claude\\skills-deom\\ppt\\browser-use\\heng.png'\n"
        "竖封面图片在'D:\\dev\\git\\claude\\skills-deom\\ppt\\browser-use\\shu.png'"
    )

    agent_settings = AgentSettings(
        max_steps=settings.agent.max_steps,
        max_failures=settings.agent.max_failures,
        llm_timeout=settings.agent.llm_timeout,
        action_timeout=settings.agent.action_timeout,
        reconnect_timeout=settings.agent.reconnect_timeout,
        truncation=settings.agent.truncation,
        enable_planning=True,
        allowed_upload_paths=[
            r"D:\Videos\test\final\2026-04-29-20-41-59.mp4",
            r"D:\dev\git\claude\skills-deom\ppt\browser-use\heng.png",
            r"D:\dev\git\claude\skills-deom\ppt\browser-use\shu.png",
        ],
        # ── 本示例的两个主角 ──
        use_vision=True,                      # 每步带降采样截图（默认 False=评测红线）
        enable_skill_injection=False,         # 关闭站点级 skill 注入（默认 True）
        # 截图降采样目标 (w, h)。直连构造不走 load_settings 的模型自适应默认，
        # 显式给 (1400, 850)（对齐 browser-use）；None = 不缩放（原图可能很大）。
        llm_screenshot_size=(1400, 850),
        # 上传验证：透传 env 开关（AGENT_UPLOAD_VERIFY_ENABLED / _WAIT_S / _INTERVAL_S），
        # 否则 AgentSettings 走 dataclass 默认值，env 不生效（四次修订修复）。
        upload_verify_enabled=settings.agent.upload_verify_enabled,
        upload_verify_wait_s=settings.agent.upload_verify_wait_s,
        upload_verify_interval_s=settings.agent.upload_verify_interval_s,
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
