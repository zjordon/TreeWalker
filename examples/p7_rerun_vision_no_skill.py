r"""P7 轨迹重跑·视觉开 + skill 关版（issue #197 真机验收用）。

在 ``p7_rerun_webarena_task.py`` 之上进程内预设三组 env 再跑原脚本：
  AGENT_USE_VISION=true                     视觉模式（每步截图入 LLM）
  AGENT_ENABLE_SKILL_INJECTION=false        站点级 skill 关
  AGENT_ENABLE_TASK_SKILL_INJECTION=false   任务级 skill 关（默认本就 off，显式压死）

用途：#197 视觉畸形动作梯子的真机验收——V 轮死于「缺 name 键动作二连判死」
的 10 任务（108/109/200/492/495/544/545/549/696/782）定向重跑。skill 全关是
为隔离变量：梯子行为（澄清/降级/死刑）与 skill 注入无关，关掉少一路噪声。
其余参数/行为与原脚本完全一致（argv 透传；登录由使用者在浏览器侧手动完成）。

跑前检查（脚本内已硬校验，对齐 evals 仓 smoke_test 的视觉口径守门）：
  LLM_MODEL 必须在视觉名单（claude-* / glm-*v* / glm-5.3-flash）——名单外
  模型 + use_vision=true 时视觉门**静默关闭**，整跑退化为贴 vision 标签的
  纯文本轮（PR #177 P0 发现：端点对文本模型+图不报错）。

#197 修复的观测点（正常跑不一定出现——畸形动作是间歇的）：
  multi_act: LLM emitted list with 1 action(s): ['<dict:params>']   ← 形状直出（原 ['?']）
  LLM returned empty action (<dict:params>), retrying with clarification (1/2)
  ... (2/2) — text-only (screenshot dropped)                        ← 降级去图重试
  硬判据：final_result 不再是 "No action returned by LLM"。

用法（Chrome 以 --remote-debugging-port=9223 启动并手动登录目标站后）：
  uv run python examples/p7_rerun_vision_no_skill.py --task-id 108
  uv run python examples/p7_rerun_vision_no_skill.py --task-id 108 --log-file v108.log
  # 批量（PowerShell）：108,109,200 | ForEach-Object { uv run python examples/p7_rerun_vision_no_skill.py --task-id $_ --log-file "v$_.log" }
"""

import asyncio
import os
import sys
from pathlib import Path

# 必须在 load_settings() 之前设 env（调用时读 env，非 import 时）。
os.environ["AGENT_USE_VISION"] = "true"
os.environ["AGENT_ENABLE_SKILL_INJECTION"] = "false"
os.environ["AGENT_ENABLE_TASK_SKILL_INJECTION"] = "false"

# examples/ 不是包：把本目录塞进 sys.path 才能 import 原脚本
#（tree_walker 的 src 路径由原脚本自身在模块级插入，import 它即完成引导）。
sys.path.insert(0, str(Path(__file__).resolve().parent))

import p7_rerun_webarena_task as base  # noqa: E402


def _validate_vision_settings() -> str | None:
	"""视觉口径守门：模型不在名单 → 返回错误文案（调用方打印并退出）。

	对齐 evals 仓 smoke_test._validate_vision_settings 的语义：静默退化成
	纯文本轮的跑批是红线事故形态（结果文件零异常信号），必须在起跑前拦。
	"""
	from tree_walker.config import load_settings, model_supports_vision
	settings = load_settings()
	if not settings.agent.use_vision:
		return "AGENT_USE_VISION=true 未生效（settings.agent.use_vision 仍为 false）"
	if not model_supports_vision(settings.llm.model):
		return (
			f"视觉口径无效：use_vision=true 但模型 {settings.llm.model!r} 不在视觉名单"
			"（claude-* / glm-*v* / glm-5.3-flash）——视觉门会静默关闭，整跑退化为"
			"纯文本。设 LLM_MODEL 为名单内模型后重跑"
		)
	return None


async def main() -> int:
	print("[口径] 视觉开（AGENT_USE_VISION=true）+ skill 全关（站点级/任务级注入均 off）")
	print("[注意] 不注入 cookie——请确保 Chrome 已手动登录目标站（Magento admin 等）")
	error = _validate_vision_settings()
	if error:
		print(f"✗ {error}")
		return 1
	print("[口径] 视觉校验通过——梯子行为（澄清/降级/死刑）与 skill 无关，#197 验收口径")
	return await base.main()


if __name__ == "__main__":
	sys.exit(asyncio.run(main()))
