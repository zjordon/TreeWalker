"""诊断脚本：验证封面区域是否被正确识别并能成功点击。

使用方法：
1. 在 Chrome 中打开抖音创作者中心上传页面（确保封面区域可见）
2. 确保 Chrome 以 --remote-debugging-port=9222 启动
3. 运行: python examples/debug_cover_click.py
"""

import asyncio
import sys

sys.path.insert(0, f"{__file__}/../src")

from dom_snapshot import build_dom_state
from tree_walker.browser.session import BrowserSession
from tree_walker.config import load_settings

# 封面相关 class 关键词
COVER_KEYWORDS = ['cover-jg3t4p', 'title-wa45xd', 'covercontrol-cjlzqc', 'filter-k_cjvj', 'cover-tip']


async def main():
	settings = load_settings()

	if not settings.browser.ws_url:
		print("Error: Cannot connect to Chrome.")
		sys.exit(1)

	print("=" * 80)
	print("封面点击验证工具")
	print("=" * 80)

	browser = BrowserSession(settings.browser)
	await browser.start()

	# [1] 获取 DOM 状态
	print("\n[1] 获取 DOM 状态...")
	dom_state, metrics = await build_dom_state(
		client=browser.client,
		session_id=browser.current_session_id,
		config=browser._dom_collection_config,
		previous_selector_map=None,
	)
	print(f"selector_map 大小={len(dom_state.selector_map)}")

	# [2] 在 selector_map 中搜索封面相关元素
	print("\n[2] 在 selector_map 中搜索封面相关元素...")
	cover_entries = []
	for bid, entry in dom_state.selector_map.items():
		cls = (entry.attributes or {}).get('class', '').lower()
		if any(kw in cls for kw in COVER_KEYWORDS):
			cover_entries.append((bid, entry))

	if not cover_entries:
		print("❌ selector_map 中没有找到任何封面相关元素！")
		await browser.stop()
		return

	print(f"找到 {len(cover_entries)} 个封面相关元素：")
	for bid, entry in cover_entries:
		cls = (entry.attributes or {}).get('class', 'N/A')
		tag = entry.tag_name
		print(f"  [{bid}] <{tag}> class=\"{cls}\"")
		print(f"    点击坐标 (x,y): ({entry.x}, {entry.y})")
		print(f"    尺寸 (w,h): {entry.width}x{entry.height}")
		print(f"    可见: {entry.is_visible}")
		# 打印原始 snapshot 数据
		snap = entry.snapshot_node
		if snap:
			print(f"    snapshot.bounds: x={snap.bounds.x}, y={snap.bounds.y}, w={snap.bounds.width}, h={snap.bounds.height}" if snap.bounds else "    snapshot.bounds: None")
			print(f"    snapshot.clientRects: {snap.clientRects}")
		else:
			print(f"    snapshot_node: None")
		print()

	# [3] 找到 cover-Jg3T4p 元素（可点击的封面触发区域）
	print("[3] 查找可点击的封面触发区域 (cover-Jg3T4p)...")
	clickable_covers = []
	for bid, entry in cover_entries:
		cls = (entry.attributes or {}).get('class', '').lower()
		if 'cover-jg3t4p' in cls:
			clickable_covers.append((bid, entry))

	if not clickable_covers:
		print("❌ 没有找到 cover-Jg3T4p 元素！")
		await browser.stop()
		return

	print(f"找到 {len(clickable_covers)} 个可点击封面区域：")
	for i, (bid, entry) in enumerate(clickable_covers):
		label = "横封面" if i == 0 else "竖封面"
		print(f"  [{bid}] {label}: 点击坐标=({entry.x}, {entry.y}), 尺寸={entry.width}x{entry.height}")

	# [4] 测试点击第一个封面（横封面）
	DO_CLICK = True
	if DO_CLICK and clickable_covers:
		bid, entry = clickable_covers[0]
		print(f"\n[4] 测试点击横封面 [{bid}]，坐标=({entry.x}, {entry.y})...")
		print("    ⚠️  即将执行点击，3秒后执行...")
		await asyncio.sleep(3)

		await browser.click_element(entry.backend_node_id)
		print("    ✅ 点击已执行")

		# 等待可能的弹窗加载
		print("    等待 2 秒，观察页面变化...")
		await asyncio.sleep(2)

		# 重新获取 DOM 状态，看看页面是否变化
		print("\n[5] 重新获取 DOM 状态，检查页面是否变化...")
		dom_state2, _ = await build_dom_state(
			client=browser.client,
			session_id=browser.current_session_id,
			config=browser._dom_collection_config,
			previous_selector_map=None,
		)
		print(f"    新 selector_map 大小={len(dom_state2.selector_map)}")
		print(f"    旧 selector_map 大小={len(dom_state.selector_map)}")
		diff = len(dom_state2.selector_map) - len(dom_state.selector_map)
		if diff > 0:
			print(f"    ✅ 元素增加了 {diff} 个（可能有弹窗出现）")
		elif diff < 0:
			print(f"    元素减少了 {abs(diff)} 个")
		else:
			print(f"    元素数量没有变化")

		# 搜索新增元素中是否有弹窗/对话框相关内容
		new_ids = set(dom_state2.selector_map.keys()) - set(dom_state.selector_map.keys())
		if new_ids:
			print(f"\n    新增的 {len(new_ids)} 个元素中，前 20 个：")
			count = 0
			for nid in sorted(new_ids):
				if count >= 20:
					print(f"    ... (还有 {len(new_ids) - 20} 个)")
					break
				ne = dom_state2.selector_map[nid]
				cls = (ne.attributes or {}).get('class', '')
				role = (ne.attributes or {}).get('role', '')
				tag = ne.tag_name
				text = ''
				if ne.node_value and ne.node_value.strip():
					text = f' text="{ne.node_value.strip()[:30]}"'
				print(f'      [{nid}] <{tag}> class="{cls}" role="{role}"{text}')
				count += 1

	await browser.stop()
	print("\n" + "=" * 80)
	print("测试完成")


if __name__ == "__main__":
	asyncio.run(main())
