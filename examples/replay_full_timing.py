"""Example: 重放——打开所有新的时序/等待参数（验证 #124 阶段 1 实现）。

在 ``examples/replay.py`` 基础上，把阶段 1 新增的 4 个时序参数全部「打开」，
用来验证 #124 的三处改动：

  - 缺口3（录制间隔回放）：``flatten`` 现在把相邻 action 的 timestamp 差填进
    ``step_interval``——用户录制的历史会按真实操作节奏等待（被 ``max_step_interval``
    封顶），而非固定兜底延迟。此行为对录制历史**默认开启**，无开关。
  - 缺口4（时序参数可配置）：4 个参数提升为 ``AgentSettings`` 字段，可用 env var
    （``AGENT_RERUN_*``）覆盖，不再被 CLI/TUI 硬编码成 1/5。
  - 缺口1（page-settle）：``get_state`` 前可选等 ``document.readyState`` 到 complete，
    复用 screenshot/save_as_pdf 的 ``wait_settle`` 范式（失败只 warning 不阻断）。

前置：

    1. uv sync
    2. Chrome 以远程调试端口启动（建议用录制时的 profile，已登录目标站点）：

       chrome --remote-debugging-port=9222 --user-data-dir=<profile>

    3. 录制产物（``recorded.json`` 等）已落 ``rerun-history/``

运行：

    uv run python examples/replay_full_timing.py
    uv run python examples/replay_full_timing.py myflow.json

验证点（运行时观察）：

    - 缺口4：开头打印的 4 个字段来自 AgentSettings（非硬编码）；也可设 env var
      AGENT_RERUN_DELAY_BETWEEN_ACTIONS / AGENT_RERUN_MAX_STEP_INTERVAL /
      AGENT_RERUN_WAIT_FOR_ELEMENTS=true / AGENT_RERUN_WAIT_FOR_PAGE_SETTLE=true 覆盖。
    - 缺口3：用户录制重放时步间延迟跟随录制节奏（封顶内），日志不再恒为固定 1s。
    - 缺口1：``wait_for_page_settle=True`` 时每步 get_state 前等 readyState，
      日志可见 ``Pre-get_state wait_settle``；settle 异常只 warning 不阻断重放。

等价的 kwargs 写法（不走 AgentSettings，直接传给 load_and_rerun）：

    await agent.load_and_rerun(
        args.history_file,
        delay_between_actions=1.0,
        max_step_interval=10.0,
        wait_for_elements=True,
        wait_for_page_settle=True,
    )
"""

import argparse
import asyncio
import dataclasses
import logging
import sys

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.config import load_settings


# 阶段 1 新增的 4 个时序参数——这里全部「打开」（与默认关/兜底相反），验证 #124 实现。
# 等价 env var（load_settings 也会读）：AGENT_RERUN_DELAY_BETWEEN_ACTIONS、
# AGENT_RERUN_MAX_STEP_INTERVAL、AGENT_RERUN_WAIT_FOR_ELEMENTS、AGENT_RERUN_WAIT_FOR_PAGE_SETTLE。
FULL_TIMING = dict(
	rerun_delay_between_actions=1.0,   # 步间兜底延迟（秒）
	rerun_max_step_interval=10.0,      # step_interval 封顶（秒）——用户发呆不会真等那么久
	rerun_wait_for_elements=True,      # 等元素数量（既有粗粒度等待）
	rerun_wait_for_page_settle=True,   # get_state 前等 readyState（缺口1）
)


async def main() -> None:
	parser = argparse.ArgumentParser(description="重放——打开所有时序/等待参数（验证 #124）")
	parser.add_argument(
		"history_file",
		nargs="?",
		default="recorded.json",
		help="重放文件名（相对 rerun-history/），默认 recorded.json",
	)
	args = parser.parse_args()

	settings = load_settings()
	if not settings.browser.ws_url:
		print("✗ Chrome 未以 --remote-debugging-port=9222 启动")
		sys.exit(1)

	# 用 dataclasses.replace 在 load_settings() 基础上覆盖 4 个时序字段（验证缺口4：
	# AgentSettings 可配置）。不传 kwargs 给 load_and_rerun → rerun_history 的 None
	# 哨兵会回落到这些字段（验证 None→self 归一化）。
	agent_settings = dataclasses.replace(settings.agent, **FULL_TIMING)

	print("=== 时序配置（全部开启）===")
	print(f"  delay_between_actions = {agent_settings.rerun_delay_between_actions}s")
	print(f"  max_step_interval     = {agent_settings.rerun_max_step_interval}s")
	print(f"  wait_for_elements     = {agent_settings.rerun_wait_for_elements}")
	print(f"  wait_for_page_settle  = {agent_settings.rerun_wait_for_page_settle}")

	# 重放本身不调决策 LLM；LLMClient 仅用于结尾 AI 摘要（失败有 fallback，不阻塞）
	llm = LLMClient(settings.llm)
	browser = BrowserSession(settings.browser)
	logging.basicConfig(level=logging.INFO)

	agent = Agent(task="", llm=llm, browser=browser, settings=agent_settings)
	print(f"\n=== 重放 {args.history_file}（不传时序 kwargs → 走 AgentSettings）===")
	# 注意：不传 delay_between_actions / max_step_interval / wait_for_elements /
	# wait_for_page_settle —— 它们为 None 时由 rerun_history 回落到 agent_settings 的值。
	results = await agent.load_and_rerun(args.history_file)

	print(f"\n✓ 重放完成（{len(results)} 条结果）")
	summary = results[-1] if results else None
	if summary and summary.is_done:
		print(f"  Success: {summary.success}")
		if summary.extracted_content:
			print(f"  {summary.extracted_content}")


if __name__ == "__main__":
	asyncio.run(main())
