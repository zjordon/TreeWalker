"""P5 验证（agent 路径）：select_dropdown.value 经手工标注后，变量合并 + 替换是否命中。

用真 agent history（rerun-history/agent_history.json，department select）做夹具：
注入一条手工变量 → merge_variable_sources → 模拟 CSV 替换 → 确认 params.value 被换。
证明：agent 路径下「手工标注 select_dropdown」的变量合并 + 替换侧已通，**无需改检测/模型**。

（重放侧 set_select_option 按 text OR value 匹配，已被 agent_history 的 result 回显
 `Selected option: Technical Support (value: support)` 证明，本脚本不重复测。）

uv run python examples/p5_select_manual_var_verify.py
"""

from __future__ import annotations

import copy
from pathlib import Path

from tree_walker.agent.views import AgentHistoryList, ManualVariableBinding
from tree_walker.agent.variable_detector import detect_variables_in_history, merge_variable_sources
from tree_walker.agent.rerun import _substitute_in_dict

ROOT = Path(__file__).resolve().parent.parent
HIST = ROOT / "rerun-history" / "agent_history.json"


def main() -> int:
	history = AgentHistoryList.model_validate_json(HIST.read_text(encoding="utf-8"))

	# 定位 select_dropdown 步（agent_history.json 里是 department = "Technical Support"）
	sel_step = sel_ai = sel_value = None
	for it in history.history:
		acts = (it.model_output or {}).get("actions") or []
		for ai, a in enumerate(acts):
			if a.get("name") == "select_dropdown":
				sel_step, sel_ai, sel_value = it.step_number, ai, (a.get("params") or {}).get("value")
		if sel_step is not None:
			break
	if sel_step is None:
		print("✗ agent_history.json 里没找到 select_dropdown 步")
		return 1
	print(f"• 找到 select_dropdown：step={sel_step} action={sel_ai} value={sel_value!r}")

	# 注入手工变量（模拟编辑器「标注为变量」，field=value）
	history.add_manual_variable(ManualVariableBinding(
		name="department", step_number=sel_step, action_index=sel_ai,
		field="value", original_value=sel_value,
	))

	# 合并变量源 —— CSV 列头 = detect ∪ manual
	detected = detect_variables_in_history(history)
	merged = merge_variable_sources(detected, history.manual_variables)
	manual_only = sorted(set(merged) - set(detected))
	print(f"• detect 自动检出: {sorted(detected.keys()) or '（无）'}")
	print(f"• 手工补充列: {manual_only}")
	if "department" not in merged:
		print("✗ 手工变量未被纳入合并集！")
		return 1
	print(f"  department.original_value = {merged['department'].original_value!r}")

	# 模拟 CSV 替换：Technical Support → Sales
	replacements = {merged["department"].original_value: "Sales"}
	target = next(it for it in history.history if it.step_number == sel_step)
	act = (target.model_output or {})["actions"][sel_ai]
	before = copy.deepcopy(act["params"])
	n = _substitute_in_dict(act["params"], replacements)
	print(f"• _substitute_in_dict 替换次数={n}")
	print(f"  before: {before}")
	print(f"  after : {act['params']}")
	ok = act["params"].get("value") == "Sales"
	print(f"\n{'✓ 通过' if ok else '✗ 失败'}：手工标注 select_dropdown.value "
	      f"{'替换命中，agent 路径替换侧已通' if ok else '替换未命中'}。")
	return 0 if ok else 1


if __name__ == "__main__":
	raise SystemExit(main())
