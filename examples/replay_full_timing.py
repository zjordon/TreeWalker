"""Example: 重放——打开所有时序/等待/actionability 参数（验证 #125 阶段 2 实现）。

在 ``examples/replay.py`` 基础上，把阶段 1（#124）的 4 个时序参数 + 阶段 2（#125）的
actionability 开关全部「打开」，用来验证 #125 的两处改动：

  - 缺口5（等目标元素）：``wait_for_elements`` 语义从「等 selector_map 元素总数」升级为
    「等本步所有需定位 action 的目标元素能定位到」。``_wait_for_target_elements`` 用
    ``_match_element_index``（六级）/``locate_by_ref``（语义线索）轮询，直到目标在当前页
    selector_map 里能定位再执行（all-or-nothing，超时降级）。复用现有匹配逻辑，不新造轮子。
  - actionability 阶段一（visible + enabled）：动作定位成功后、``_exec_one`` 前查目标元素
    ``is_visible`` + ``disabled``，不满足则轮询等待（poll 期间用历史指纹重解析漂移 index）。
    默认关 + 超时降级（不抛错）→ 永不引入新失败。白名单 click/input_text/select_dropdown；
    upload_file 的隐藏 file input 三层保护（白名单 / hist_elem=None / _is_file_input）不误杀。

``wait_for_elements=True`` 现在既是阶段1 的粗粒度等待入口，也触发缺口5 的等目标元素
（语义升级，开关不变）。同时保留 #124 阶段 1 的缺口1/3/4 验证。

前置：

    1. uv sync
    2. Chrome 以远程调试端口启动（建议用录制时的 profile，已登录目标站点）：

       chrome --remote-debugging-port=9222 --user-data-dir=<profile>

    3. 录制产物（``recorded.json`` 等）已落 ``rerun-history/``；
       建议挑一段 SPA / 动态渲染流程（元素延迟出现 / transition 中点击 / disabled→enabled）
       最能体现阶段2 收益。

运行：

    uv run python examples/replay_full_timing.py
    uv run python examples/replay_full_timing.py myflow.json

验证点（运行时观察日志，logging=INFO）：

    - 缺口5（等目标元素）：目标元素延迟出现的步骤不再快进——``_wait_for_target_elements``
      轮询直到目标定位成功（或超时降级照原样执行），日志可见定位重试。
    - actionability：对 click/input_text/select_dropdown 在执行前查 visible+enabled；
      元素 transition 中/disabled 会等待，可见后执行；poll 期间 index 漂移时日志可见
      ``actionability 等待后 index 漂移 ...→...``；超时降级照原样执行（不引入新失败）。
    - upload_file 不被误杀：隐藏 file input 跳过 visible 检查，照常按 accept 解析上传。
    - 阶段1（#124）验证点仍成立：开头打印字段来自 AgentSettings（非硬编码）；步间延迟
      跟随录制节奏（封顶内）；``wait_for_page_settle`` 每步 get_state 前等 readyState。

等价的 kwargs 写法（不走 AgentSettings，直接传给 load_and_rerun）：

    await agent.load_and_rerun(
        args.history_file,
        delay_between_actions=1.0,
        max_step_interval=10.0,
        wait_for_elements=True,
        wait_for_page_settle=True,
        rerun_actionability_check=True,
    )
"""

import argparse
import asyncio
import dataclasses
import logging
import sys

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.config import load_settings


# 阶段 1（#124）4 个时序参数 + 阶段 2（#125）actionability 开关——这里全部「打开」，
# 验证 #125 实现。等价 env var：AGENT_RERUN_*（含 AGENT_RERUN_ACTIONABILITY_CHECK=true）。
# actionability 的 timeout/poll 用默认 2.0/0.3（也可 env 覆盖）。
FULL_TIMING = dict(
	rerun_delay_between_actions=1.0,   # 步间兜底延迟（秒）
	rerun_max_step_interval=10.0,      # step_interval 封顶（秒）——用户发呆不会真等那么久
	rerun_wait_for_elements=True,      # 阶段2：升级为「等目标元素匹配」（缺口5）
	rerun_wait_for_page_settle=True,   # get_state 前等 readyState（缺口1）
	rerun_actionability_check=True,    # 阶段2：visible+enabled 检查（actionability 阶段一）
)


async def main() -> None:
	parser = argparse.ArgumentParser(description="重放——打开所有时序/等待/actionability 参数（验证 #125）")
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

	# 用 dataclasses.replace 在 load_settings() 基础上覆盖时序/actionability 字段（验证可配置）。
	# 不传 kwargs 给 load_and_rerun → rerun_history 的 None 哨兵回落到这些字段。
	agent_settings = dataclasses.replace(settings.agent, **FULL_TIMING)

	print("=== 时序 + actionability 配置（全部开启）===")
	print(f"  delay_between_actions   = {agent_settings.rerun_delay_between_actions}s")
	print(f"  max_step_interval       = {agent_settings.rerun_max_step_interval}s")
	print(f"  wait_for_elements       = {agent_settings.rerun_wait_for_elements}  (阶段2: 等目标元素)")
	print(f"  wait_for_page_settle    = {agent_settings.rerun_wait_for_page_settle}")
	print(f"  actionability_check     = {agent_settings.rerun_actionability_check}  (阶段2: visible+enabled)")
	print(f"  actionability_timeout   = {agent_settings.rerun_actionability_timeout}s")
	print(f"  actionability_poll      = {agent_settings.rerun_actionability_poll}s")

	# 重放本身不调决策 LLM；LLMClient 仅用于结尾 AI 摘要（失败有 fallback，不阻塞）
	llm = LLMClient(settings.llm)
	browser = BrowserSession(settings.browser)
	logging.basicConfig(level=logging.INFO)

	agent = Agent(task="", llm=llm, browser=browser, settings=agent_settings)
	print(f"\n=== 重放 {args.history_file}（不传时序 kwargs → 走 AgentSettings）===")
	# 不传任何时序/actionability kwargs —— None 时由 rerun_history 回落到 agent_settings 的值。
	results = await agent.load_and_rerun(args.history_file)

	print(f"\n✓ 重放完成（{len(results)} 条结果）")
	summary = results[-1] if results else None
	if summary and summary.is_done:
		print(f"  Success: {summary.success}")
		if summary.extracted_content:
			print(f"  {summary.extracted_content}")


if __name__ == "__main__":
	asyncio.run(main())
