"""新录制管线 CDP 只读冒烟测试（不导航、不切 flag、不落盘）。

连真实 Chrome（remote-debugging-port），对当前页跑一遍新管线的核心路径：
get_state → translate_event → locate_by_ref → DOMInteractedElement 指纹 → apply_rules → flatten，
验证重设计后 CDP 定位/指纹路径仍能产出有效 AgentHistoryList。

用法：Chrome 以 --remote-debugging-port=9222 启动后
      uv run python examples/smoke_pipeline_cdp.py
"""

import asyncio
import sys
import time

sys.path.insert(0, f"{__file__}/../src")

from tree_walker.browser.session import BrowserSession
from tree_walker.browser.views import DOMInteractedElement
from tree_walker.config import _fetch_ws_url
from tree_walker.recorder.flatten import flatten
from tree_walker.recorder.locator import locate_by_ref
from tree_walker.recorder.models import Recording
from tree_walker.recorder.rules import apply_rules
from tree_walker.recorder.translation import translate_event


async def main() -> int:
	ws_url = _fetch_ws_url("localhost", 9222)
	if not ws_url:
		print("✗ 9222 无 debug Chrome")
		return 1

	browser = BrowserSession(ws_url=ws_url)
	await browser.start()
	try:
		state = await browser.get_state(include_screenshot=False)
		if not state or not state.dom_state:
			print("✗ get_state 无结果")
			return 1
		smap = state.dom_state.selector_map
		print(f"✓ get_state: url={state.url[:60]}  selector_map={len(smap)} 个节点")

		if not smap:
			print("✗ selector_map 空（页面无可交互元素？）")
			return 1

		# 取第一个可交互节点，模拟用户点它
		idx, node = next(iter(smap.items()))
		xpath = getattr(node, "xpath", "")
		print(f"✓ 选节点 index={idx} tag={getattr(node,'node_name','')} xpath={xpath[:50]}")

		# Stage1：事件 → ActionRecord（追加进 Recording）
		rec = Recording()
		ts_ms = int(time.time() * 1000)
		action = translate_event(
			{"type": "click", "xpath": xpath, "tag": (getattr(node, "node_name", "") or "").lower(), "ts": ts_ms},
			rec,
		)
		assert action is not None and len(rec.actions) == 1
		print(f"✓ translate_event: action={rec.actions[0].action_name}")

		# 实时 locate + 指纹（handle_event 的核心，对真实 DOM）
		located = locate_by_ref(rec.actions[0].element_ref.to_ref_dict(), smap)
		if located is None:
			print(f"✗ locate_by_ref 未命中 xpath={xpath}（xpath 与 selector_map 失配）")
			return 1
		loc_idx, loc_node = located
		proj = DOMInteractedElement.load_from_enhanced_dom_tree(loc_node).to_dict()
		rec.actions[0].params["index"] = loc_idx
		rec.actions[0].interacted_element = [proj]
		print(f"✓ locate 命中 index={loc_idx}  指纹 element_hash={proj.get('element_hash')} stable_hash={proj.get('stable_hash')}")

		# Stage3 + flatten
		rec.actions = apply_rules(rec.actions)
		hist = flatten(rec)
		s = hist.history[0]
		print(f"✓ flatten: step0={s.model_output['actions'][0]['name']} params={s.model_output['actions'][0]['params']}")
		print(f"  interacted_element[0] keys={sorted((s.interacted_element[0] or {}).keys())[:6]}")
		print("\n✅ 新管线 CDP 路径畅通：translate → locate → 指纹 → rules → flatten 全部通过。")
		return 0
	finally:
		await browser.stop()


if __name__ == "__main__":
	sys.exit(asyncio.run(main()))
