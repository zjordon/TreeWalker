"""Stage 3 翻译规则：在带 signal 的 ``ActionRecord`` 列表上做意图感知去噪。

替代旧 ``event_mapper`` 的 ``dedupe_uploads`` / ``dedupe_auto_navigates`` / ``denoise_steps``
三个事后函数。关键升级：规则看 ``ActionRecord.signals``（如 ``MODAL_OPENED``）做意图判断，
而非仅靠时间 gap 猜。设计见 ``docs/user_recording/redesign.md`` §3.3。

每条规则是「整表变换」：``Callable[[list[ActionRecord]], list[ActionRecord]]``，输入完整动作
列表、输出新列表。比 redesign 文档里的「``(actions, i) -> list`` 按位置」接口更简单且不易
出错（多条规则需改邻居——给前一步附信号、吸收前一步 fakepath——按位置迭代下标会漂移）。
``apply_rules`` 按顺序串接全部规则。
"""

from __future__ import annotations

import os
from collections.abc import Callable

from tree_walker.recorder.models import ActionRecord, Signal, SignalKind

Rule = Callable[[list[ActionRecord]], list[ActionRecord]]

# navigate 作为前置动作副作用时距前置动作的时间窗（秒）。strict < ：≥5s 视为地址栏式主动导航。
_NAV_GAP_S = 5.0
# upload_file 前置 fakepath input_text 的吸收时间窗（秒）。
_UPLOAD_GAP_S = 10.0
# 短时重复 click 折叠窗（秒，对齐旧 denoise_steps 的 click_gap_s）。
_CLICK_GAP_S = 0.5


def rule_navigation_signal(actions: list[ActionRecord]) -> list[ActionRecord]:
	"""导航信号关联（替代 ``dedupe_auto_navigates``，signal 化）。

	对每个 ``new_tab=False`` 的 navigate：
	- 首步（无前置）/ 前一步也是 navigate（连续导航）/ ``new_tab=True`` → 保留；
	- 紧邻前一步是**任意非 navigate 动作**且时间差 ``< 5s`` → 判为前置动作的副作用跳转：给前一步
	  附 ``NAVIGATION`` signal，**丢弃**此独立 navigate（回放前置动作会再次触发跳转，CDP
	  ``Page.navigate`` 不幂等会整页重载丢状态）。前置动作可以是 click（点链接）/ upload_file
	  （上传后跳发布页）/ submit 等——任何能触发跳转的动作；
	- 时间差 ≥5s → 视为地址栏式主动导航，保留。

	信号关联（而非 gap 猜测）让「前置动作导致的跳转」明确归属到该动作。
	"""
	out: list[ActionRecord] = []
	for action in actions:
		if action.action_name != "navigate" or action.params.get("new_tab"):
			out.append(action)
			continue
		if not out:
			out.append(action)  # 首步 navigate 保留
			continue
		prev = out[-1]
		if prev.action_name == "navigate":
			out.append(action)  # 连续 navigate 不连锁误丢
			continue
		ta, tb = prev.timestamp, action.timestamp
		if ta > 0 and tb > 0 and (tb - ta) < _NAV_GAP_S:
			prev.signals.append(Signal(
				kind=SignalKind.NAVIGATION,
				timestamp=action.timestamp,
				detail={"to_url": action.params.get("url", "")},
				source_action_ts=prev.timestamp,
			))
			continue  # 副作用跳转，丢弃独立 navigate
		out.append(action)
	return out


def rule_file_upload(actions: list[ActionRecord]) -> list[ActionRecord]:
	"""文件上传翻译（替代 ``dedupe_uploads``，signal 化）。

	人类操作：点上传按钮 → OS 弹框 → 选文件；重放：``upload_file(path)`` 直注 ``setFileInputFiles``。
	对每个 ``upload_file`` 向前吸收：
	- **fakepath input_text**（file input ``value=C:\\fakepath\\<名>`` 被 onInput 误录的噪声；当前
	  扩展 onInput 已跳过 file input，此为安全网）；跨非 input_text 或超 ``_UPLOAD_GAP_S`` 即停。
	- **紧邻前置 click**（上传触发按钮）——**仅当无 ``MODAL_OPENED`` signal**。对齐 Playwright/Selenium：
	  replay 时 ``upload_file`` 直注文件，不需先点上传按钮（点它会弹原生 picker 阻塞/冲突）；吸收后
	  手工录制与 agent 录制（upload_file 无前置 click）对齐。带 ``MODAL_OPENED`` 的 click 是弹窗/
	  编辑器开启器（如「选择封面」打开封面编辑器），replay 需先开编辑器，**保留**。详见
	  ``docs/user_recording/upload-record-replay-research.md``。

	upload_file 自身的身份（accept+xpath 签名，B 方案）由录制时存（不向被吸收步骤借）。
	"""
	out: list[ActionRecord] = []
	for action in actions:
		if action.action_name != "upload_file":
			out.append(action)
			continue
		u_base = os.path.basename(action.params.get("path") or "")
		while out:
			prev = out[-1]
			if prev.action_name != "input_text":
				break  # 前置非 input_text，停止 fakepath 吸收
			ta, tb = prev.timestamp, action.timestamp
			if ta > 0 and tb > 0 and (tb - ta) > _UPLOAD_GAP_S:
				break  # 超 gap 不吸收
			text = prev.params.get("text", "")
			if "fakepath" in text.lower() or (u_base and os.path.basename(text) == u_base):
				out.pop()
				continue
			break  # 普通 input_text 不吸收
		# 紧邻前置 click：上传触发器（无 modal 信号）吸收；编辑器/弹窗开启器（有 modal）保留
		if out and out[-1].action_name == "click" and not _has_modal(out[-1]):
			out.pop()
		out.append(action)
	return out


def rule_merge_inputs(actions: list[ActionRecord]) -> list[ActionRecord]:
	"""合并相邻同 xpath 的 ``input_text`` → 留最后值。

	Stage 1 ``translate_event`` 已对连续同框输入聚合（取最终值），本规则是安全网：吸收掉
	经其它规则（如 navigation_signal 丢弃中间 navigate）后重新相邻的同框输入。
	"""
	out: list[ActionRecord] = []
	for action in actions:
		if action.action_name != "input_text":
			out.append(action)
			continue
		last = out[-1] if out else None
		if last is not None and last.action_name == "input_text" and _same_xpath(last, action):
			out[-1] = action  # 取最终值
			continue
		out.append(action)
	return out


def rule_redundant_click(actions: list[ActionRecord]) -> list[ActionRecord]:
	"""折叠短时重复 click（同 index、``_CLICK_GAP_S`` 内）→ 留最后一条。

	打错重打、双击误录。基于 index（实时 locate 已填，对齐旧 ``denoise_steps`` 的 index 判定）；
	index 缺失（locate 失败的噪声 click）不折叠——保留待重放侧的噪声步跳过逻辑处理。
	"""
	out: list[ActionRecord] = []
	for action in actions:
		if action.action_name != "click":
			out.append(action)
			continue
		last = out[-1] if out else None
		if last is not None and last.action_name == "click" and _same_index(last, action):
			ta, tb = last.timestamp, action.timestamp
			if ta <= 0 or tb <= 0 or abs(tb - ta) <= _CLICK_GAP_S:
				out[-1] = action  # 短时重复，留最后一条
				continue
		out.append(action)
	return out


def rule_merge_scrolls(actions: list[ActionRecord]) -> list[ActionRecord]:
	"""合并相邻同方向 ``scroll`` → amount 求和 clamp 1-10。"""
	out: list[ActionRecord] = []
	for action in actions:
		if action.action_name != "scroll":
			out.append(action)
			continue
		last = out[-1] if out else None
		if (
			last is not None
			and last.action_name == "scroll"
			and last.params.get("direction") == action.params.get("direction")
		):
			total = max(1, min(10, int(last.params.get("amount", 1)) + int(action.params.get("amount", 1))))
			last.params["amount"] = total
			continue
		out.append(action)
	return out


def _same_xpath(a: ActionRecord, b: ActionRecord) -> bool:
	"""两动作的 element_ref.xpath 是否都存在且相等。"""
	ax = a.element_ref.xpath if a.element_ref is not None else None
	bx = b.element_ref.xpath if b.element_ref is not None else None
	return bool(ax) and ax == bx


def _has_modal(action: ActionRecord) -> bool:
	"""动作是否带 ``MODAL_OPENED`` signal（编辑器/弹窗开启器标记）。"""
	return any(s.kind == SignalKind.MODAL_OPENED for s in action.signals)


def _same_index(a: ActionRecord, b: ActionRecord) -> bool:
	"""两动作 params['index'] 是否都存在且相等。"""
	ai = a.params.get("index")
	bi = b.params.get("index")
	return ai is not None and ai == bi


# 顺序敏感：navigation_signal 先跑（丢弃副作用 navigate + 附信号），再 upload/merge/fold/scroll。
DEFAULT_RULES: list[Rule] = [
	rule_navigation_signal,
	rule_file_upload,
	rule_merge_inputs,
	rule_redundant_click,
	rule_merge_scrolls,
]


def apply_rules(actions: list[ActionRecord], rules: list[Rule] | None = None) -> list[ActionRecord]:
	"""按顺序串接全部翻译规则（默认 ``DEFAULT_RULES``）。"""
	chain = rules if rules is not None else DEFAULT_RULES
	result = list(actions)
	for rule in chain:
		result = rule(result)
	return result


__all__ = [
	"Rule",
	"DEFAULT_RULES",
	"apply_rules",
	"rule_navigation_signal",
	"rule_file_upload",
	"rule_merge_inputs",
	"rule_redundant_click",
	"rule_merge_scrolls",
]
