"""Example: 重放——actionability 阶段二/三 + step_interval 语义清理（验证 #127 阶段 4）。

在 ``examples/replay_full_timing.py`` 基础上，把阶段 4（#127）的三块改动全部「打开 / 自检」，
用来验证 #127 的实现：

  - actionability 阶段二 receives-events：
      * L1 静态遮挡（paint_order）+ L2 pointer-events:none——零开销静态判定，读快照
        ``computed_styles`` / ``ignored_by_paint_order``，由 ``rerun_actionability_receives_events``
        开关管辖（默认 True，但总开关 ``rerun_actionability_check`` 默认关 → 整体零行为变更）。
      * L3 运行时遮挡（``elementFromPoint``）——复用 ``session._is_element_occluded``，async +
        CDP 开销，默认关（``rerun_actionability_runtime_occlusion``）。``_wait_until`` predicate
        是同步的，故 L3 在 ``_wait_for_actionability`` 自带 deadline 循环里内联 await。
      * 遗留收编：``aria-disabled="true"`` 现在也被 ``_is_actionable`` 判为不可交互。

  - actionability 阶段三 stable（可选 / 默认关 / 优先级最低）：
      两次取 rect 比（复用 ``session.get_element_coordinates`` 三级 fallback），动画/重排中元素
      位置漂移时等稳定。``rerun_actionability_stable`` 默认关；定点单元素，不影响整体性能。

  - 缺口 7 step_interval 语义清理（改法 ② 新增 ``user_pause_seconds``）：
      ``StepMetadata.step_interval`` = 上一步耗时（含 LLM，agent 自录路径填充）；
      ``StepMetadata.user_pause_seconds`` = 相邻用户操作真实停顿（recorder 路径填充）。
      ``_compute_step_delay`` 优先级：``user_pause_seconds``（不封顶，忠实还原）>
      ``step_interval``（封顶防 LLM 空等）> ``delay_between_actions`` 兜底。
      ``agent/step.py`` 零改动 → agent 自录路径零回归；旧 ``AgentHistory.json`` 无新字段 →
      pydantic 默认 None → 向后兼容。

本示例分两段验证：

  1. 离线自检（无需 Chrome）：直接调 ``_is_actionable`` / ``_compute_step_delay`` /
     ``_is_rect_stable`` + 旧 JSON 反序列化，打印各分支结果，验证阶段 4 模块逻辑。
  2. 端到端重放（需 Chrome）：开启 receives_events + runtime_occlusion + stable 重放 history，
     验证 settings→agent 接线 + 运行时（目标元素 receives-events/stable 等待）。

前置（端到端段）：

    1. uv sync
    2. Chrome 以远程调试端口启动（建议用录制时的 profile，已登录目标站点）：
       chrome --remote-debugging-port=9222 --user-data-dir=<profile>
    3. 录制产物（``recorded.json`` 等）已落 ``rerun-history/``；建议挑有点击 / 含遮挡或动画
       元素的流程最能体现阶段 4 收益。

运行：

    uv run python examples/replay_actionability_stage4.py            # 仅离线自检（无 Chrome 也跑）
    uv run python examples/replay_actionability_stage4.py myflow.json # + 端到端重放

验证点（运行时观察日志，logging=INFO）：

    - 阶段二 receives-events：含 click 的步骤，目标元素若 ``pointer-events:none`` 或被 paint_order
      完全覆盖 → actionability 等待（降级不抛错）；开 L3 后 ``elementFromPoint`` 运行时遮挡判定。
    - 阶段三 stable：动画中元素 → 等 rect 稳定；定点单元素 ``get_element_coordinates`` 两次。
    - 缺口 7：录制回放日志可见 ``user_pause=...s``（忠实还原，不封顶）；agent 自录回放仍见
      ``saved step_interval=...s``（封顶）。老 recorded.json（无 ``user_pause_seconds``）→ 回落
      ``step_interval``，行为与改动前一致（向后兼容）。

等价的 kwargs 写法（不走 AgentSettings，直接传给 load_and_rerun）：

    await agent.load_and_rerun(
        args.history_file,
        delay_between_actions=1.0,
        max_step_interval=10.0,
        wait_for_elements=True,
        wait_for_page_settle=True,
        rerun_actionability_check=True,
        rerun_actionability_receives_events=True,    # 阶段4 阶段二 L1+L2
        rerun_actionability_runtime_occlusion=True,  # 阶段4 阶段二 L3
        rerun_actionability_stable=True,             # 阶段4 阶段三
    )
"""

import argparse
import asyncio
import dataclasses
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.agent.rerun import RerunMixin, _is_actionable
from tree_walker.agent.views import AgentHistory, StepMetadata
from tree_walker.browser.views import DOMRect
from tree_walker.config import load_settings


# 阶段 1/2/3 时序 + 阶段 4 actionability 阶段二/三——全部「打开」，验证 #127 实现。
# 等价 env var：AGENT_RERUN_ACTIONABILITY_RECEIVES_EVENTS / _RUNTIME_OCCLUSION / _STABLE 等。
FULL_TIMING = dict(
	rerun_delay_between_actions=1.0,
	rerun_max_step_interval=10.0,
	rerun_wait_for_elements=True,
	rerun_wait_for_page_settle=True,
	rerun_actionability_check=True,                 # 总开关（阶段2）
	rerun_actionability_receives_events=True,       # 阶段4 阶段二 L1+L2（零开销静态）
	rerun_actionability_runtime_occlusion=True,     # 阶段4 阶段二 L3（运行时 elementFromPoint）
	rerun_actionability_stable=True,                # 阶段4 阶段三（两次 rect 比）
	rerun_actionability_stable_interval=0.1,
	rerun_actionability_stable_tolerance=1.0,
	rerun_wait_for_networkidle=True,                # 阶段3
	rerun_upload_wait_video=5.0,
	rerun_upload_wait_image=3.0,
)


async def demo_actionability_receives_events() -> None:
	"""离线自检 _is_actionable 阶段二（L1/L2）+ 遗留 aria-disabled（无需 Chrome）。"""
	print("=== 阶段二 receives-events + 遗留 aria-disabled 离线自检（无需 Chrome）===")
	base = dict(is_visible=True, ax_node=None, attributes={})

	# 零回归基线：check_receives_events=False（默认）= 阶段一一字不差
	plain = SimpleNamespace(snapshot_node=None, **base)
	print(f"  普通 visible+enabled                actionable = {_is_actionable(plain)}   ← 基线放过")

	# L2 pointer-events:none —— 默认不查（零回归），check=True 才阻断
	pe = SimpleNamespace(
		snapshot_node=SimpleNamespace(computed_styles={"pointer-events": "none"}), **base)
	print(f"  pointer-events:none  check=False    actionable = {_is_actionable(pe)}   ← 默认不查（零回归）")
	print(f"  pointer-events:none  check=True     actionable = "
	      f"{_is_actionable(pe, check_receives_events=True)}   ← L2 阻断")

	# L1 paint_order 静态遮挡
	po = SimpleNamespace(snapshot_node=None, ignored_by_paint_order=True, **base)
	print(f"  ignored_by_paint_order check=True   actionable = "
	      f"{_is_actionable(po, check_receives_events=True)}   ← L1 阻断")

	# snapshot_node 缺失 → 保守放过（不引入新失败）
	no_snap = SimpleNamespace(snapshot_node=None, **base)
	print(f"  snapshot_node=None check=True        actionable = "
	      f"{_is_actionable(no_snap, check_receives_events=True)}   ← 保守放过")

	# 遗留收编：aria-disabled="true"
	aria = SimpleNamespace(snapshot_node=None,
		**{**base, "attributes": {"aria-disabled": "true"}})
	print(f"  aria-disabled=true                  actionable = {_is_actionable(aria)}   ← 遗留收编阻断")
	print()


def demo_step_interval_semantics() -> None:
	"""离线自检 缺口7：user_pause_seconds 优先级 + 向后兼容（无需 Chrome）。"""
	print("=== 缺口7 step_interval 语义清理离线自检（无需 Chrome）===")
	rm = RerunMixin()

	def _hist(**md_kw):
		return AgentHistory(
			step_number=1, model_output={"action": {}}, result=[],
			metadata=StepMetadata(step_start_time=0.0, step_end_time=1.0, step_number=1, **md_kw),
		)

	# 旧录制格式（仅 step_interval=30，含 LLM）→ 封顶 5（向后兼容，agent 自录路径）
	old = rm._compute_step_delay(
		_hist(step_interval=30.0), delay_between_actions=2.0, max_step_interval=5.0)
	print(f"  旧格式 step_interval=30  max=5       delay = {old}   ← 封顶（向后兼容）")

	# 新录制格式（user_pause=30 真实停顿）→ 优先且不封顶 30
	new = rm._compute_step_delay(
		_hist(user_pause_seconds=30.0, step_interval=3.0), 2.0, 5.0)
	print(f"  新格式 user_pause=30 step_int=3 max=5 delay = {new}   ← user_pause 优先，不封顶")

	# 仅 user_pause_seconds
	up = rm._compute_step_delay(_hist(user_pause_seconds=7.0), 2.0, 5.0)
	print(f"  仅 user_pause=7                      delay = {up}   ← 不封顶")

	# 两者皆无 → delay_between_actions 兜底
	none = rm._compute_step_delay(_hist(), 2.0, 5.0)
	print(f"  都无                                 delay = {none}   ← delay_between_actions 兜底")

	# 向后兼容：旧 JSON（无 user_pause_seconds 字段）反序列化 → 默认 None
	md = StepMetadata(step_start_time=0.0, step_end_time=1.0, step_number=1, step_interval=2.5)
	print(f"  旧 JSON 反序列化 user_pause_seconds  = {md.user_pause_seconds}   ← None（向后兼容），"
	      f"step_interval={md.step_interval} 仍可读")
	print()


async def demo_stable_check() -> None:
	"""离线自检 阶段三 stable：_is_rect_stable 两次 rect 比（无需 Chrome）。"""
	print("=== 阶段三 stable 离线自检（无需 Chrome）===")
	rm = RerunMixin()
	rm.browser = MagicMock()

	stable = DOMRect(1.0, 2.0, 10.0, 20.0)
	rm.browser.get_element_coordinates = AsyncMock(return_value=stable)
	ok = await rm._is_rect_stable(5, interval=0.0, tolerance=1.0)
	print(f"  两次 rect 相同                       stable = {ok}   ← 稳定")

	rm.browser.get_element_coordinates = AsyncMock(side_effect=[
		DOMRect(1.0, 2.0, 10.0, 20.0), DOMRect(50.0, 2.0, 10.0, 20.0)])
	drift = await rm._is_rect_stable(5, interval=0.0, tolerance=1.0)
	print(f"  rect 漂移（x 1→50）                  stable = {drift}   ← 不稳定")

	rm.browser.get_element_coordinates = AsyncMock(return_value=None)
	nocoord = await rm._is_rect_stable(5)
	print(f"  拿不到坐标                           stable = {nocoord}   ← 保守判不稳")
	print()


async def main() -> None:
	# 1) 离线自检（总是跑，无需 Chrome）——验证阶段 4 模块逻辑
	await demo_actionability_receives_events()
	demo_step_interval_semantics()
	await demo_stable_check()

	# 2) 端到端重放（需 Chrome）——验证 receives-events/stable 集成 + 接线
	parser = argparse.ArgumentParser(
		description="重放——actionability 阶段二/三 + step_interval 清理（验证 #127 阶段4）",
	)
	parser.add_argument(
		"history_file", nargs="?", default="recorded.json",
		help="重放文件名（相对 rerun-history/），默认 recorded.json",
	)
	args = parser.parse_args()

	settings = load_settings()
	if not settings.browser.ws_url:
		print("✗ Chrome 未以 --remote-debugging-port=9222 启动——跳过端到端重放段（离线自检已完成）")
		return

	# 用 dataclasses.replace 在 load_settings() 基础上覆盖阶段 4 字段（验证可配置）。
	agent_settings = dataclasses.replace(settings.agent, **FULL_TIMING)

	print("=== actionability 阶段二/三 + 时序配置（全部开启）===")
	print(f"  actionability_check           = {agent_settings.rerun_actionability_check}  (总开关)")
	print(f"  receives_events (L1+L2)       = {agent_settings.rerun_actionability_receives_events}  (阶段4 阶段二)")
	print(f"  runtime_occlusion (L3)        = {agent_settings.rerun_actionability_runtime_occlusion}  (阶段4 阶段二)")
	print(f"  stable                        = {agent_settings.rerun_actionability_stable}  (阶段4 阶段三)")
	print(f"  stable_interval/tolerance     = "
	      f"{agent_settings.rerun_actionability_stable_interval}/"
	      f"{agent_settings.rerun_actionability_stable_tolerance}")

	# 重放本身不调决策 LLM；LLMClient 仅用于结尾 AI 摘要（失败有 fallback，不阻塞）
	llm = LLMClient(settings.llm)
	browser = BrowserSession(settings.browser)
	logging.basicConfig(level=logging.INFO)

	agent = Agent(task="", llm=llm, browser=browser, settings=agent_settings)

	# 接线验证：settings → Agent.__init__ → self.rerun_*（验证 agent.py 接线）
	print("\n=== Agent 接线验证（settings → self.rerun_*）===")
	print(f"  agent.rerun_actionability_receives_events   = {agent.rerun_actionability_receives_events}")
	print(f"  agent.rerun_actionability_runtime_occlusion = {agent.rerun_actionability_runtime_occlusion}")
	print(f"  agent.rerun_actionability_stable            = {agent.rerun_actionability_stable}")
	print(f"  agent.rerun_actionability_stable_interval   = {agent.rerun_actionability_stable_interval}")
	print(f"  agent.rerun_actionability_stable_tolerance  = {agent.rerun_actionability_stable_tolerance}")

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
