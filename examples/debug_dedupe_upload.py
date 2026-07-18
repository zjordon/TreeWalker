"""验证新翻译管线（rules.apply_rules）不会误吸收「打开编辑器」的 click。

构造封面录制序列：简介输入 → 点「选择封面」(打开编辑器) → upload_file(封面)。
跑 apply_rules，确认「选择封面」click 保留——新管线（rule_file_upload）只吸收 fakepath
input_text，前置 click 一律保留（带 modal_opened signal 时更明确是编辑器触发器）。

用法：uv run python examples/debug_dedupe_upload.py
"""

import sys

sys.path.insert(0, f"{__file__}/../src")

from tree_walker.recorder.models import ActionRecord, ElementRef, Signal, SignalKind
from tree_walker.recorder.rules import apply_rules


def _action(name, params, xpath, ts, index=None, signals=None):
	p = dict(params)
	if index is not None:
		p["index"] = index
	return ActionRecord(
		action_name=name,
		params=p,
		element_ref=ElementRef(xpath=xpath),
		timestamp=ts,
		signals=list(signals or []),
	)


# 「选择封面」click 打开编辑器 → 带 modal_opened signal（SideEffectObserver 检测到）
cover_modal = [Signal(SignalKind.MODAL_OPENED, 1.1, {"selector": ".cover-editor"})]
actions = [
	_action("input_text", {"text": "简介", "clear": True}, "html/inp", 0.0, index=5),
	_action("click", {}, "html/cover", 1.0, index=200, signals=cover_modal),
	_action("upload_file", {"path": "heng.png"}, "html/file", 2.0, index=300),
]

print("=== apply_rules 前（原始）===")
for i, a in enumerate(actions):
	print(f"  [{i}] {a.action_name} index={a.params.get('index')} signals={[s.kind.value for s in a.signals]}")

out = apply_rules(actions)
print("\n=== apply_rules 后 ===")
for i, a in enumerate(out):
	print(f"  [{i}] {a.action_name} index={a.params.get('index')} signals={[s.kind.value for s in a.signals]}")

cover_click_kept = any(a.action_name == "click" for a in out)
print("\n=== 结论 ===")
if cover_click_kept:
	print("✅ 「选择封面」click 保留了——rule_file_upload 没吸收（前置 click 一律保留）。")
else:
	print("❌ 「选择封面」click 被吸收了！（不应发生）")
