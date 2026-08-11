"""Example: 用 skill 加持的 agent 在抖音创作者中心发视频（暂存草稿），并录制成可重放历史。

对照 ``examples/skill/upload_bilibili_with_skill.py``，把目标站换成抖音创作者中心。
同样多启用两个开关：

  1. **skill 开关**（默认关闭，这里显式开启）
     - ``enable_skill_injection=True`` + ``skills_dir="domain-skills"``
     - agent 每步按当前页 host 读 ``domain-skills/<host>/{_sop,selectors,quirks}.md``，
       注入 state message 的 ``[Domain Skill]`` 段，给 LLM 喂该站点的领域知识。
     - 双层静默：目录/文件不存在时不报错、不注入（零行为变更）。所以没有 skill
       文件也能跑——但建议准备 ``domain-skills/creator.douyin.com/{selectors.md,quirks.md}``
       （抖音上传/封面/合集/自主声明流程坑多）才能真正看到 skill 的收益。

  2. **录制开关**（save_history 把成功探索存成可重放 JSON）
     - ``agent.run()`` 探索 → ``agent.save_history()`` 落盘 → ``agent.detect_variables()``
       检测可替换变量。产物可用 ``load_and_rerun`` 换数据重放（不调决策 LLM）。
     - 录制路径相对 ``rerun_history_dir``（默认 ``rerun-history/``）。

任务要点（写进 task 喂给 LLM）：
- 上传 1 个视频 + 横/竖各 1 张封面（共 3 个文件 → 都要进 ``allowed_upload_paths``）；
- 填作品描述（主/副标题）、加入合集、自主声明选「无需添加」；
- **只暂存为草稿，不直接发布**；存完回到「发布视频」界面即算完成，不要点「继续编辑」重复进编辑器。

Prerequisites:
1. ``uv sync``
2. Chrome 以远程调试端口启动（建议用录制专用 profile，提前登录抖音创作者中心）::

       chrome --remote-debugging-port=9223

3. 设置 ``ZHIPU_API_KEY`` 环境变量
4. （可选但推荐）手写 ``domain-skills/creator.douyin.com/{selectors.md, quirks.md}``

Usage:
    set ZHIPU_API_KEY=your_key
    uv run python examples/skill/upload_douyin_with_skill.py
"""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.config import AgentSettings, _fetch_ws_url, load_settings


async def main():
    settings = load_settings()

    if not settings.llm.api_key:
        print("Error: Set ZHIPU_API_KEY environment variable")
        sys.exit(1)
    ws_url = _fetch_ws_url("127.0.0.1", 9223)
    if not ws_url:
        print("Error: Chrome 未以 --remote-debugging-port=9223 启动")
        sys.exit(1)

    llm = LLMClient(settings.llm)
    browser = BrowserSession(ws_url=ws_url)

    # ── 任务素材（按需改成你自己的路径）──
    video_path = r"D:\Videos\test\final\2026-04-29-20-41-59.mp4"
    heng_cover_path = r"D:\dev\git\claude\skills-deom\ppt\browser-use\heng.png"
    shu_cover_path = r"D:\Videos\browser-harness\png\browser-harness-shu.png"

    task = (
        "帮我到抖音创作者中心发一个视频，信息如下，先暂存为草稿不要直接发布，"
        "发布完后回到发布视频界面就算完成了不要再点继续编辑进去重复编辑\n"
        "\n"
        "抖音创作者中心网址:https://creator.douyin.com/\n"
        f"我要发的视频在'{video_path}'\n"
        "作品描述中的主标题为：ai浏览器第五期-browse-use,副标题为:'browse-use体验及技术原理'\n"
        "添加合集到'AI浏览器'\n"
        "自主声明选择'无需添加自主声明'\n"
        f"横封面图片在'{heng_cover_path}'\n"
        f"竖封面图片在'{shu_cover_path}'\n"
    )

    agent_settings = AgentSettings(
        max_steps=settings.agent.max_steps,
        max_failures=settings.agent.max_failures,
        llm_timeout=settings.agent.llm_timeout,
        action_timeout=settings.agent.action_timeout,
        reconnect_timeout=settings.agent.reconnect_timeout,
        truncation=settings.agent.truncation,
        enable_planning=True,
        allowed_upload_paths=[video_path, heng_cover_path, shu_cover_path],
        # ── ① skill 开关（默认 False，这里开启）──
        enable_skill_injection=True,
        skills_dir="domain-skills",  # 读 domain-skills/<host>/{_sop,selectors,quirks}.md
        # ── ② 录制：历史落盘根目录（save_history 的相对路径基于此）──
        rerun_history_dir=settings.agent.rerun_history_dir,
    )

    agent = Agent(
        task=task,
        llm=llm,
        browser=browser,
        settings=agent_settings,
    )

    logging.basicConfig(level=logging.INFO)

    # ── ① 带 skill 加持的 agent 探索 ──
    print("=== 带 skill 加持的 agent 探索（抖音创作者中心）===")
    print(f"  skill 注入: {'已开启' if agent_settings.enable_skill_injection else '已关闭'}"
          f"（skills_dir={agent_settings.skills_dir}）")
    print("  提示: 若 domain-skills/creator.douyin.com/ 不存在，会静默不注入（零行为变更）")

    history = await agent.run()
    if history.is_done():
        print(f"\n✓ 任务完成: {history.final_result()}")
    else:
        print("\n任务未在 max_steps 内完成（仍会录制已执行步骤）")

    # ── ② 录制：把探索历史存成可重放 JSON ──
    print("\n=== 录制历史 ===")
    history_file = "douyin_upload_skill.json"
    agent.save_history(history_file)
    print(f"✓ 历史已保存: {agent_settings.rerun_history_dir}/{history_file}")

    # ── ③ 检测可替换变量（换数据重放时用）──
    print("\n=== 检测变量 ===")
    variables = agent.detect_variables()
    if variables:
        for name, info in variables.items():
            fmt = f" (format: {info.format})" if info.format else ""
            print(f"  • {name}: {info.original_value!r}{fmt}")
    else:
        print("  未检测到可替换变量")

    print("\n下一步——换数据重放（不调决策 LLM）：")
    print(f"  replay = Agent(task='', llm=llm, browser=browser, settings=agent_settings)")
    print(f"  await replay.load_and_rerun('{history_file}', variables={{...}}, summary_llm=llm)")
    print("  完整范例见 examples/features/rerun_history.py")


if __name__ == "__main__":
    asyncio.run(main())
