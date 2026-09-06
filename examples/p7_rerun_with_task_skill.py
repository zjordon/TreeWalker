r"""P7 轨迹重跑·任务级 skill 注入版（口径 C 冒烟，docs/p7/03）。

在 ``p7_rerun_webarena_task.py`` 之上做两件事：
1. 进程内预设 ``AGENT_ENABLE_TASK_SKILL_INJECTION=true`` 再跑原脚本——免得每次
   测试手设 env；
2. 自动追加 ``--no-cookie`` 跳过 cookie 注入——手工测试前已在 Chrome 登录，注入
   评测侧（可能过期的）storage_state 反而会顶掉手登会话。
其余参数 / 行为与原脚本完全一致（argv 透传给原脚本的 argparse）。

⚠️ 口径提醒（docs/p7/03 §八红线）：本脚本跑出的是**口径 C（with task knowledge）**
的数字——任务级 skill 对自主探索口径等价泄露参考轨迹，其 SR 禁止与主口径（A）/
站点口径（B）或外部 leaderboard 混合对比。只用于产品能力验证 / 检索层冒烟。

用法（Chrome 以 --remote-debugging-port=9223 启动后）：
  uv run python examples/p7_rerun_with_task_skill.py --task-id 0
  uv run python examples/p7_rerun_with_task_skill.py --task-id 0 --log-file out-c.log

预期日志（三条齐 = 检索层活 + 匹配判定 + 实际装载）：
  [INFO] tree_walker.skills.task_loader: task-skill catalog: 44 cards (host_key=localhost_7780)
  [INFO] tree_walker.agent.agent: task-skill-match: {..., "match": "<slug>", ...}
  [INFO] tree_walker.agent.agent: task-skill hit: slug=<slug> chars=N
"""

import asyncio
import os
import sys
from pathlib import Path

# 必须在跑 base.main() 之前设 env——load_settings() 是调用时读 env（非 import 时）。
os.environ["AGENT_ENABLE_TASK_SKILL_INJECTION"] = "true"

# examples/ 不是包：把本目录塞进 sys.path 才能 import 原脚本
#（tree_walker 的 src 路径由原脚本自身在模块级插入， import 它即完成引导）。
sys.path.insert(0, str(Path(__file__).resolve().parent))

import p7_rerun_webarena_task as base  # noqa: E402

# 跳过 cookie 注入（store_true 重复传无害）；需保证测试前 Chrome 已手动登录。
sys.argv.append("--no-cookie")


async def main() -> int:
	print("[口径 C] AGENT_ENABLE_TASK_SKILL_INJECTION=true——任务级 skill 注入已开")
	print("[口径 C] 已跳过 cookie 注入——请确保 Chrome 已手动登录目标站")
	print("[口径 C] ⚠️ 本运行的 SR 属产品口径，禁止与主口径/站点口径/外部榜对比（docs/p7/03 §八）")
	return await base.main()


if __name__ == "__main__":
	sys.exit(asyncio.run(main()))
