"""Debug: dump select/option elements on the current page + detect hash collisions.

用于排查抖音重放 Step 5「点错下拉」问题：怀疑页面上多个相似的下拉触发器
（<div class="semi-select-selection">）的 element_hash / stable_hash 碰撞，导致
重放按哈希匹配时选错了对象（点到了左边的「合集」而非「请选择合集」）。

== 用法 ==
1. 在 Chrome（9222 调试端口）里手工导航到「有问题的页面」——即抖音发布视频表单页，
   让「请选择合集」下拉可见（不需要打开它，触发器本身在表单里就能抓到）。
2. uv run python examples/features/_debug_selectors.py

输出：每个 select/option 元素的 index/class/role/ax_name/位置/element_hash/stable_hash，
并自动标出哈希碰撞。对照录制里 Step 4 的元素
（hash=8521187606673460889, bounds≈x614/y657）即可看出重放会命中哪一个。
"""

import asyncio
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tree_walker import BrowserSession
from tree_walker.browser.views import DOMInteractedElement
from tree_walker.config import load_settings

# 录制里 Step 4 的「请选择合集」触发器（来自 douyin_upload_history.json）
RECORDED_HASH = 8521187606673460889
RECORDED_BOUNDS = {"x": 614.7, "y": 657.8, "width": 390.4, "height": 30.4}


def _center(bounds: dict | None) -> tuple[float, float] | None:
    if not bounds:
        return None
    return (bounds.get("x", 0) + bounds.get("width", 0) / 2,
            bounds.get("y", 0) + bounds.get("height", 0) / 2)


async def main() -> None:
	settings = load_settings()
	browser = BrowserSession(settings.browser)
	await browser.start()
	try:
		state = await browser.get_state(include_screenshot=False)
		sm = state.dom_state.selector_map if state and state.dom_state else {}
		print(f"URL: {state.url}")
		print(f"selector_map 元素数: {len(sm)}")
		print(f"录制 Step4 参考: hash={RECORDED_HASH} bounds={RECORDED_BOUNDS}\n")

		rows: list[dict] = []
		for idx, e in sorted(sm.items()):
			attrs = e.attributes or {}
			cls = attrs.get("class", "") or ""
			role = attrs.get("role", "") or ""
			# 只看下拉触发器（semi-select*）+ 选项（role=option / *option）
			if not ("select" in cls.lower() or role == "option" or "option" in cls.lower()):
				continue
			proj = DOMInteractedElement.load_from_enhanced_dom_tree(e).to_dict()
			rows.append({
				"index": idx,
				"tag": e.node_name,
				"class": cls,
				"role": role,
				"ax_name": proj.get("ax_name"),
				"bounds": proj.get("bounds"),
				"element_hash": proj.get("element_hash"),
				"stable_hash": proj.get("stable_hash"),
				"xpath": proj.get("x_path"),
			})

		print(f"=== select / option 相关元素（{len(rows)} 个）===")
		for r in rows:
			c = _center(r["bounds"])
			cstr = f"center=({c[0]:.0f},{c[1]:.0f})" if c else "center=None"
			match_flag = "  ← 与录制 hash 一致" if r["element_hash"] == RECORDED_HASH else ""
			print(f"[{r['index']}] <{r['tag']}> class={r['class']!r} role={r['role']!r}{match_flag}")
			print(f"    ax_name={r['ax_name']!r}  {cstr}")
			print(f"    element_hash={r['element_hash']}  stable_hash={r['stable_hash']}")
			print(f"    xpath={r['xpath']}")

		# 碰撞检测
		for field in ("element_hash", "stable_hash"):
			print(f"\n=== {field} 碰撞检测 ===")
			by = defaultdict(list)
			for r in rows:
				by[r[field]].append(r["index"])
			collisions = {h: idxs for h, idxs in by.items() if len(idxs) > 1}
			if collisions:
				for h, idxs in collisions.items():
					print(f"  {field}={h} 命中多个元素: indexes={idxs}  ← 碰撞！重放取第一个，可能点错")
			else:
				print("  无碰撞")

		# 录制 hash 的命中情况
		print(f"\n=== 录制 hash {RECORDED_HASH} 在当前页的命中 ===")
		hits = [r["index"] for r in rows if r["element_hash"] == RECORDED_HASH]
		print(f"  命中元素 indexes: {hits}")
		if len(hits) > 1:
			rec_c = _center(RECORDED_BOUNDS)
			print(f"  录制位置 center≈({rec_c[0]:.0f},{rec_c[1]:.0f})；按位置最近的才是正确目标：")
			for r in rows:
				if r["index"] in hits:
					c = _center(r["bounds"])
					d = ((c[0] - rec_c[0]) ** 2 + (c[1] - rec_c[1]) ** 2) ** 0.5 if c else float("inf")
					print(f"    [{r['index']}] center=({c[0]:.0f},{c[1]:.0f}) 距录制位置={d:.0f}px  ax_name={r['ax_name']!r}")
	finally:
		await browser.stop()


if __name__ == "__main__":
	asyncio.run(main())
