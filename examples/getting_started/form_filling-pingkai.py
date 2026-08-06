"""Example: 表单填写（多字段 + 提交）。移植自 browser-use/examples/getting_started/02_form_filling.py。

在 httpbin 表单页填多项并提交。演示任务驱动的表单填写：
agent 会用 input_text 链式填多个字段，再 click 提交。
前置：uv sync / chrome --remote-debugging-port=9222 / $env:ZHIPU_API_KEY="..."
运行：uv run python examples/getting_started/form_filling.py
"""
import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

# ── 临时调试：每步 dump 模型看到的 DOM 树（issue #157）──────────────────────
# 排查完把 DUMP_STEP_DOM 改成 False 即关（_maybe_dump_step_dom 见 step.py）。
# dump 文件落在脚本同目录的 _dump_form_pingkai/step_NN.txt。
DUMP_STEP_DOM = False
if DUMP_STEP_DOM:
	os.environ["AGENT_DEBUG_DUMP_DIR"] = str(
		Path(__file__).resolve().parent / "_dump_form_pingkai"
	)

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.config import AgentSettings, load_settings


TASK = (
	"访问 https://pingkai.cn/contact . 填写表单，信息如下: "
	"姓名='张敬树', 手机号='13055201008', "
	" 职位选择'架构师'，其它不用填也不用提交."
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

	llm = LLMClient(settings.llm)
	browser = BrowserSession(settings.browser)
	agent_settings = AgentSettings(
		max_steps=settings.agent.max_steps,
		max_failures=settings.agent.max_failures,
		llm_timeout=settings.agent.llm_timeout,
		action_timeout=settings.agent.action_timeout,
		reconnect_timeout=settings.agent.reconnect_timeout,
		truncation=settings.agent.truncation,
		enable_planning=True,
		# ── ① skill 开关（默认 False，这里开启）──
		enable_skill_injection=True,
		skills_dir="domain-skills",  # 读 domain-skills/<host>/{_sop,selectors,quirks}.md
		# ── ② 录制：历史落盘根目录（save_history 的相对路径基于此）──
		rerun_history_dir=settings.agent.rerun_history_dir,
	)
	agent = Agent(task=TASK, llm=llm, browser=browser, settings=agent_settings)
	history = await agent.run()

	if history.is_done():
		print(history.final_result())
	else:
		print("\n任务未在 max_steps 内完成（仍会录制已执行步骤）")
	# ── ② 录制：把探索历史存成可重放 JSON ──
	print("\n=== 录制历史 ===")
	history_file = "form_filling-pingkai.json"
	agent.save_history(history_file)
	print(f"✓ 历史已保存: {agent_settings.rerun_history_dir}/{history_file}")

if __name__ == "__main__":
	asyncio.run(main())
