"""Example: 录制 Agent 历史并换数据重放（移植自 browser-use rerun_history.py）。

工作流：录制(run + save_history) → 检测变量(detect_variables) → 换数据重放(load_and_rerun)
→ 打印 AI 摘要。重放不调决策 LLM，按录好的动作驱动浏览器；extract 动作走 LLM 在当前页重算。

Prerequisites:
1. uv sync
2. chrome --remote-debugging-port=9222
3. 设置 ZHIPU_API_KEY

Usage:
    ZHIPU_API_KEY=your_key uv run python examples/features/rerun_history.py
"""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.config import load_settings


async def main():
	settings = load_settings()
	if not settings.llm.api_key:
		print("Error: Set ZHIPU_API_KEY environment variable")
		sys.exit(1)
	if not settings.browser.ws_url:
		print("Error: Chrome 未以 --remote-debugging-port=9222 启动")
		sys.exit(1)

	llm = LLMClient(settings.llm)
	browser = BrowserSession(settings.browser)
	logging.basicConfig(level=logging.INFO)

	task = (
		"到 https://browser-use.github.io/stress-tests/challenges/"
		"reference-number-form.html 用示例数据填写表单、提交并提取 reference number"
	)

	# ── 1. 录制 ──
	print("=== 录制 ===")
	settings.agent.max_steps = 10   # max_steps 在 settings 里设，不在 run() 里
	agent = Agent(task=task, llm=llm, browser=browser, settings=settings.agent)
	await agent.run()
	agent.save_history("agent_history.json")
	print("✓ 历史已保存到 agent_history.json")

	# ── 2. 检测变量 ──
	print("\n=== 检测变量 ===")
	variables = agent.detect_variables()
	if variables:
		for name, info in variables.items():
			fmt = f" (format: {info.format})" if info.format else ""
			print(f"  • {name}: {info.original_value!r}{fmt}")
	else:
		print("  未检测到可替换变量")

	# ── 3. 换数据重放 ──
	if variables:
		new_values: dict[str, str] = {}
		if "email" in variables:
			new_values["email"] = "jane.smith@example.com"
		if "full_name" in variables:
			new_values["full_name"] = "Jane Smith"
		if "first_name" in variables:
			new_values["first_name"] = "Jane"
		if "date" in variables:
			new_values["date"] = "1995-05-15"

		if new_values:
			print("\n=== 重放（替换数据）===")
			for k, v in new_values.items():
				print(f"  • {k}: {variables[k].original_value!r} → {v!r}")

			replay_agent = Agent(task="", llm=llm, browser=browser, settings=settings.agent)
			results = await replay_agent.load_and_rerun(
				"agent_history.json",
				variables=new_values,
				max_step_interval=5,
				delay_between_actions=1,
				summary_llm=llm,
			)

			if results and results[-1].is_done:
				summary = results[-1]
				print("\n📊 AI 摘要:")
				print(f"  Success: {summary.success}")
				print(f"  {summary.extracted_content}")
			print("✓ 重放完成")


if __name__ == "__main__":
	asyncio.run(main())
