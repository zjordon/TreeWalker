"""Example: 重放——打开所有时序/等待/actionability/networkidle 参数（验证 #126 阶段 3）。

在 ``examples/replay.py`` 基础上，把阶段 1（#124）时序 + 阶段 2（#125）actionability +
阶段 3（#126）networkidle/upload_wait 全部「打开」，用来验证 #126 的两处改动：

  - 缺口2（networkidle 可选等待）：``rerun_wait_for_networkidle=True`` 让每步 ``get_state``
    前等 inflight 网络请求归零（+稳定窗口）。``NetworkIdleTracker`` 在 ``BrowserSession._connect``
    自动 ``Network.enable`` + 注册 4 个 CDP 回调（``requestWillBeSent``/``responseReceived``/
    ``loadingFinished``/``loadingFailed``），维护 inflight 集合；长连接（WebSocket/EventSource）
    按 ``responseReceived.type`` 过滤、不计入 pending；``is_idle`` = pending 空 AND 无活动 ≥
    稳定窗口。默认关 = 零行为变更；超时降级不抛错。详见
    ``docs/wait-and-timing/03-阶段3-networkidle开关与清理upload硬编码wait.md``。

  - 缺口6（清理录制端 upload 硬编码 wait）：录制端不再注入固定 ``wait`` 动作；改为重放端
    ``rerun_upload_wait_video`` / ``rerun_upload_wait_image`` 可配置（默认 5.0/3.0 = 原硬编码
    = 零行为变更）。重放时 upload_file 成功后按文件类型等待；失败/未知类型跳过。

本示例分两段验证：

  1. 离线自检（无需 Chrome）：直接构造 ``NetworkIdleTracker`` 喂 CDP 事件，打印 ``is_idle``
     转换 + 长连接过滤，验证缺口2 模块逻辑。
  2. 端到端重放（需 Chrome）：开启 networkidle + upload_wait 重放 history，验证 settings→agent
     接线 + 运行时（步间网络等待、upload 后按类型等待）。

前置（端到端段）：

    1. uv sync
    2. Chrome 以远程调试端口启动（建议用录制时的 profile，已登录目标站点）：
       chrome --remote-debugging-port=9222 --user-data-dir=<profile>
    3. 录制产物（``recorded.json`` 等）已落 ``rerun-history/``；建议挑 AJAX 重 / 含 upload
       的流程最能体现阶段3 收益。

运行：

    uv run python examples/replay_full_timing.py            # 仅离线自检（无 Chrome 也跑）
    uv run python examples/replay_full_timing.py myflow.json # + 端到端重放

验证点（运行时观察日志，logging=INFO）：

    - 缺口2（networkidle）：每步 ``get_state`` 前等网络空闲；AJAX 重的 SPA 不再快进操作。
      长连接（WebSocket/SSE）不无限等待（被过滤）；Network.enable 失败则降级（tracker disabled）。
    - 缺口6（upload_wait）：含 upload_file 的步骤，upload 成功后日志可见 ``upload 后等待 5.0s
      （video）``；失败不等待；未知扩展名不等待。老 recorded.json（带注入 wait 步）会「双等」
      （已知，建议重新录制净化历史）。
    - 阶段1/2（#124/#125）验证点仍成立（字段来自 AgentSettings；等目标元素；actionability）。

等价的 kwargs 写法（不走 AgentSettings，直接传给 load_and_rerun）：

    await agent.load_and_rerun(
        args.history_file,
        delay_between_actions=1.0,
        max_step_interval=10.0,
        wait_for_elements=True,
        wait_for_page_settle=True,
        rerun_actionability_check=True,
        wait_for_networkidle=True,        # 阶段3 缺口2
    )
"""

import argparse
import asyncio
import dataclasses
import logging
import sys

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.config import load_settings


# 阶段 1（#124）时序 + 阶段 2（#125）actionability + 阶段 3（#126）networkidle/upload_wait
# ——全部「打开」，验证 #126 实现。等价 env var：AGENT_RERUN_*（含 AGENT_RERUN_WAIT_FOR_NETWORKIDLE=true）。
# upload_wait 的 timeout/stability/poll 用默认（network_idle_*，BrowserSettings）。
FULL_TIMING = dict(
	rerun_delay_between_actions=1.0,   # 步间兜底延迟（秒）
	rerun_max_step_interval=10.0,      # step_interval 封顶（秒）——用户发呆不会真等那么久
	rerun_wait_for_elements=True,      # 阶段2：升级为「等目标元素匹配」（缺口5）
	rerun_wait_for_page_settle=True,   # get_state 前等 readyState（缺口1）
	rerun_actionability_check=True,    # 阶段2：visible+enabled 检查（actionability 阶段一）
	rerun_wait_for_networkidle=True,   # 阶段3：get_state 前等 networkidle（缺口2）
	rerun_upload_wait_video=5.0,       # 阶段3：重放端 upload 等待（缺口6，默认=原硬编码）
	rerun_upload_wait_image=3.0,
)


async def demo_networkidle_tracker() -> None:
	"""离线自检 NetworkIdleTracker（无需 Chrome）——验证缺口2 模块逻辑。

	直接喂数据事件 dict 给 CDP 回调，打印 is_idle 转换 + 长连接过滤，直观展示：
	pending 归零 + 稳定窗口 → 空闲；WebSocket 不阻塞（被过滤）；XHR long-poll 靠超时降级。
	"""
	from unittest.mock import MagicMock

	from tree_walker.browser.network_idle import NetworkIdleTracker

	print("=== 缺口2 离线自检：NetworkIdleTracker（无需 Chrome）===")
	t = NetworkIdleTracker(stability_window=0.2, poll_interval=0.05, timeout=1.0)
	t.register(MagicMock(), "sid")
	print(f"  register 后 enabled              = {t.enabled}")

	t._on_request_will_be_sent({"requestId": "r1"})
	print(f"  requestWillBeSent(r1)    is_idle = {t.is_idle()}   ← pending 非空")
	t._on_loading_finished({"requestId": "r1"})
	print(f"  loadingFinished(r1)      is_idle = {t.is_idle()}   ← 刚 finish，lull < window")
	await asyncio.sleep(0.25)
	print(f"  等 0.25s 后              is_idle = {t.is_idle()}   ← lull ≥ window → 空闲")

	# 长连接过滤：WebSocket 不收 loadingFinished，但被过滤 → 不阻塞 idle 判定
	t._on_request_will_be_sent({"requestId": "ws1"})
	t._on_response_received({"requestId": "ws1", "type": "WebSocket"})
	await asyncio.sleep(0.25)
	print(f"  WebSocket（无 finish）   is_idle = {t.is_idle()}   ← 长连接被过滤，仍空闲")

	# XHR long-poll 不过滤 → pending 不空 → 不空闲（靠 deadline 兜底）
	t._on_request_will_be_sent({"requestId": "x1"})
	t._on_response_received({"requestId": "x1", "type": "XHR"})
	print(f"  XHR long-poll（无 finish) is_idle = {t.is_idle()}   ← 不过滤，pending 非空")

	ok = await t.wait_until_idle(timeout=0.3)
	print(f"  wait_until_idle(timeout=0.3)    = {ok}   ← XHR 未完成 → 超时降级返 False")
	print("  （阶段3 缺口2 模块行为正常）\n")


async def main() -> None:
	# 1) 离线自检（总是跑，无需 Chrome）——验证缺口2 NetworkIdleTracker 模块
	await demo_networkidle_tracker()

	# 2) 端到端重放（需 Chrome）——验证 networkidle 集成 + 缺口6 upload_wait 接线
	parser = argparse.ArgumentParser(
		description="重放——打开所有时序/等待/networkidle 参数（验证 #126 阶段3）",
	)
	parser.add_argument(
		"history_file",
		nargs="?",
		default="recorded.json",
		help="重放文件名（相对 rerun-history/），默认 recorded.json",
	)
	args = parser.parse_args()

	settings = load_settings()
	if not settings.browser.ws_url:
		print("✗ Chrome 未以 --remote-debugging-port=9222 启动——跳过端到端重放段（离线自检已完成）")
		return

	# 用 dataclasses.replace 在 load_settings() 基础上覆盖时序/actionability/networkidle/upload_wait
	# 字段（验证可配置）。不传 kwargs 给 load_and_rerun → rerun_history 的 None 哨兵回落到这些字段。
	agent_settings = dataclasses.replace(settings.agent, **FULL_TIMING)
	bits = settings.browser

	print("=== 时序 + actionability + networkidle + upload_wait 配置（全部开启）===")
	print(f"  delay_between_actions   = {agent_settings.rerun_delay_between_actions}s")
	print(f"  max_step_interval       = {agent_settings.rerun_max_step_interval}s")
	print(f"  wait_for_elements       = {agent_settings.rerun_wait_for_elements}  (阶段2: 等目标元素)")
	print(f"  wait_for_page_settle    = {agent_settings.rerun_wait_for_page_settle}")
	print(f"  actionability_check     = {agent_settings.rerun_actionability_check}  (阶段2: visible+enabled)")
	print(f"  wait_for_networkidle    = {agent_settings.rerun_wait_for_networkidle}  (阶段3 缺口2)")
	print(f"  upload_wait_video       = {agent_settings.rerun_upload_wait_video}s  (阶段3 缺口6)")
	print(f"  upload_wait_image       = {agent_settings.rerun_upload_wait_image}s")
	print(f"  [Browser] network_idle timeout/stability/poll = "
	      f"{bits.network_idle_timeout}/{bits.network_idle_stability_window}/{bits.network_idle_poll_interval}s")

	# 重放本身不调决策 LLM；LLMClient 仅用于结尾 AI 摘要（失败有 fallback，不阻塞）
	llm = LLMClient(settings.llm)
	browser = BrowserSession(settings.browser)
	logging.basicConfig(level=logging.INFO)

	agent = Agent(task="", llm=llm, browser=browser, settings=agent_settings)

	# 接线验证：settings → Agent.__init__ → self.rerun_*（验证 agent.py 接线）
	print("\n=== Agent 接线验证（settings → self.rerun_*）===")
	print(f"  agent.rerun_wait_for_networkidle = {agent.rerun_wait_for_networkidle}")
	print(f"  agent.rerun_upload_wait_video    = {agent.rerun_upload_wait_video}")
	print(f"  agent.rerun_upload_wait_image    = {agent.rerun_upload_wait_image}")

	print(f"\n=== 重放 {args.history_file}（不传时序 kwargs → 走 AgentSettings）===")
	# 不传任何时序 kwargs —— None 时由 rerun_history 回落到 agent_settings 的值。
	results = await agent.load_and_rerun(args.history_file)

	print(f"\n✓ 重放完成（{len(results)} 条结果）")
	summary = results[-1] if results else None
	if summary and summary.is_done:
		print(f"  Success: {summary.success}")
		if summary.extracted_content:
			print(f"  {summary.extracted_content}")


if __name__ == "__main__":
	asyncio.run(main())
