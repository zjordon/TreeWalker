"""诊断脚本：验证"自主声明"下拉框是否被正确识别并能成功点击。

使用方法：
1. 在 Chrome 中打开抖音创作者中心上传页面（确保"自主声明"区域可见）
2. 确保 Chrome 以 --remote-debugging-port=9222 启动
3. 运行: python examples/debug_declaration_dropdown.py
"""

import asyncio
import sys

sys.path.insert(0, f"{__file__}/../src")

from dom_snapshot import build_dom_state
from tree_walker.browser.session import BrowserSession
from tree_walker.config import load_settings

# 自主声明相关 class 关键词（从实际 HTML 中提取的哈希化 class 名）
DECLARATION_CLASS_KEYWORDS = [
	'wrapper-MLZdnB', 'labelWrapper-p6osJm', 'title-cnbkZe',
	'controlWrapper-Kt_9Xm', 'selectBox-buZRzi', 'selectText-XSrMFZ',
	'chevron-euwXR1',
]

# 文本匹配关键词
DECLARATION_TEXT_KEYWORDS = ['自主声明', '请选择']


def _match_class(cls: str, keywords: list[str]) -> bool:
	cls_lower = cls.lower()
	return any(kw.lower() in cls_lower for kw in keywords)


def _match_text(text: str, keywords: list[str]) -> bool:
	return any(kw in text for kw in keywords)


async def main():
	settings = load_settings()

	if not settings.browser.ws_url:
		print("Error: Cannot connect to Chrome.")
		sys.exit(1)

	print("=" * 80)
	print("自主声明下拉框诊断工具")
	print("=" * 80)

	browser = BrowserSession(settings.browser)
	await browser.start()

	# ── [1] 获取 DOM 状态 ──────────────────────────────────────────────
	print("\n[1] 获取 DOM 状态...")
	dom_state, metrics = await build_dom_state(
		client=browser.client,
		session_id=browser.current_session_id,
		config=browser._dom_collection_config,
		previous_selector_map=None,
	)
	print(f"selector_map 大小={len(dom_state.selector_map)}")

	# ── [2] 搜索自主声明相关元素 ───────────────────────────────────────
	print("\n[2] 在 selector_map 中搜索自主声明相关元素...")
	declaration_entries = []
	for bid, entry in dom_state.selector_map.items():
		cls = (entry.attributes or {}).get('class', '')
		text = entry.node_value or ''

		if _match_class(cls, DECLARATION_CLASS_KEYWORDS) or _match_text(text, DECLARATION_TEXT_KEYWORDS):
			declaration_entries.append((bid, entry))

	if not declaration_entries:
		print("❌ selector_map 中没有找到任何自主声明相关元素！")
		print("   尝试扩大搜索范围：搜索包含 text 内容的所有元素...")
		# 打印所有有文本的元素，帮助人工定位
		print("\n   所有可见文本元素（前 50 个）：")
		count = 0
		for bid, entry in dom_state.selector_map.items():
			text = (entry.node_value or '').strip()
			if text and len(text) < 100:
				cls = (entry.attributes or {}).get('class', '')
				tag = entry.tag_name
				print(f"     [{bid}] <{tag}> class=\"{cls}\" text=\"{text[:60]}\"")
				count += 1
				if count >= 50:
					print(f"     ... (还有更多)")
					break
		await browser.stop()
		return

	print(f"找到 {len(declaration_entries)} 个自主声明相关元素：")
	for bid, entry in declaration_entries:
		cls = (entry.attributes or {}).get('class', 'N/A')
		role = (entry.attributes or {}).get('role', 'N/A')
		tag = entry.tag_name
		text = (entry.node_value or '').strip()[:50]
		print(f"  [{bid}] <{tag}> class=\"{cls}\" role=\"{role}\"")
		print(f"    text: \"{text}\"")
		print(f"    点击坐标 (x,y): ({entry.x}, {entry.y})")
		print(f"    尺寸 (w,h): {entry.width}x{entry.height}")
		print(f"    可见: {entry.is_visible}")
		snap = entry.snapshot_node
		if snap:
			print(f"    snapshot.is_clickable={snap.is_clickable}")
			if snap.bounds:
				print(f"    snapshot.bounds: x={snap.bounds.x}, y={snap.bounds.y}, w={snap.bounds.width}, h={snap.bounds.height}")
			cursor = snap.computed_styles.get('cursor', '') if snap.computed_styles else ''
			pointer_events = snap.computed_styles.get('pointer-events', '') if snap.computed_styles else ''
			print(f"    cursor={cursor}, pointer-events={pointer_events}")
		print()

	# ── [3] 搜索下拉框元素 ────────────────────────────────────────────
	print("[3] 查找下拉框触发元素 (selectBox)...")
	dropdown_entries = []
	for bid, entry in dom_state.selector_map.items():
		cls = (entry.attributes or {}).get('class', '').lower()
		role = (entry.attributes or {}).get('role', '').lower()
		tag = entry.tag_name.lower()
		aria_haspopup = (entry.attributes or {}).get('aria-haspopup', '').lower()

		is_dropdown = (
			role in ('combobox', 'listbox', 'dropdown')
			or 'selectbox' in cls
			or 'select-text' in cls
			or 'chevron' in cls
			or aria_haspopup in ('listbox', 'true')
		)
		if is_dropdown:
			dropdown_entries.append((bid, entry))

	if dropdown_entries:
		print(f"找到 {len(dropdown_entries)} 个下拉框元素：")
		for bid, entry in dropdown_entries:
			cls = (entry.attributes or {}).get('class', 'N/A')
			role = (entry.attributes or {}).get('role', 'N/A')
			tag = entry.tag_name
			text = (entry.node_value or '').strip()[:50]
			print(f"  [{bid}] <{tag}> class=\"{cls}\" role=\"{role}\" text=\"{text}\"")
			print(f"    点击坐标=({entry.x}, {entry.y}), 尺寸={entry.width}x{entry.height}")
			print(f"    可见={entry.is_visible}")
			print()
	else:
		print("未找到典型的下拉框元素。")
		print("尝试在自主声明区域附近搜索所有可交互元素...")

	# ── [4] 在自主声明元素附近找可点击元素 ─────────────────────────────
	print("\n[4] 搜索自主声明区域附近的所有可点击元素...")
	# 找到自主声明区域的边界
	if declaration_entries:
		min_y = min(e.y for _, e in declaration_entries if e.y)
		max_y = max(e.y + e.height for _, e in declaration_entries if e.y and e.height)
		# 扩大范围 ±100px
		search_min_y = max(0, min_y - 100)
		search_max_y = max_y + 100

		nearby_clickable = []
		for bid, entry in dom_state.selector_map.items():
			if not entry.is_visible:
				continue
			if entry.y is None or entry.height is None:
				continue
			entry_bottom = entry.y + entry.height
			if entry.y >= search_min_y and entry_bottom <= search_max_y:
				nearby_clickable.append((bid, entry))

		if nearby_clickable:
			print(f"在自主声明区域 Y=[{search_min_y:.0f}, {search_max_y:.0f}] 范围内找到 {len(nearby_clickable)} 个可见元素：")
			for bid, entry in nearby_clickable:
				cls = (entry.attributes or {}).get('class', '')
				role = (entry.attributes or {}).get('role', '')
				tag = entry.tag_name
				text = (entry.node_value or '').strip()[:40]
				snap = entry.snapshot_node
				clickable_str = ""
				if snap:
					clickable_str = f" is_clickable={snap.is_clickable}"
				print(f"  [{bid}] <{tag}> class=\"{cls}\" role=\"{role}\" text=\"{text}\"")
				print(f"    pos=({entry.x}, {entry.y}) size={entry.width}x{entry.height}{clickable_str}")
				print()
		else:
			print("附近没有找到可见元素")

	# ── [5] 测试点击下拉框 ─────────────────────────────────────────────
	# 优先找自主声明相关的下拉框，其次找任意下拉框
	DO_CLICK = True
	click_target = None

	if DO_CLICK:
		# 尝试找自主声明区域内的下拉框
		if dropdown_entries and declaration_entries:
			decl_y_values = [e.y for _, e in declaration_entries if e.y]
			if decl_y_values:
				decl_center_y = sum(decl_y_values) / len(decl_y_values)
				# 找最近的下拉框
				for bid, entry in dropdown_entries:
					if entry.y and abs(entry.y - decl_center_y) < 200:
						click_target = (bid, entry)
						break

		# 如果没找到，尝试第一个下拉框
		if not click_target and dropdown_entries:
			click_target = dropdown_entries[0]

		# 如果还没找到，尝试自主声明区域内最像下拉框的元素
		if not click_target and nearby_clickable:
			for bid, entry in nearby_clickable:
				cls = (entry.attributes or {}).get('class', '').lower()
				role = (entry.attributes or {}).get('role', '').lower()
				if 'select' in cls or 'dropdown' in cls or 'picker' in cls or 'trigger' in cls:
					click_target = (bid, entry)
					break
			# 还没找到就用区域内第一个有文本的元素
			if not click_target:
				for bid, entry in nearby_clickable:
					text = (entry.node_value or '').strip()
					if text:
						click_target = (bid, entry)
						break

		if click_target:
			bid, entry = click_target
			cls = (entry.attributes or {}).get('class', '')
			tag = entry.tag_name
			text = (entry.node_value or '').strip()[:50]
			print(f"\n[5] 测试点击 [{bid}] <{tag}> class=\"{cls}\" text=\"{text}\"")
			print(f"    坐标=({entry.x}, {entry.y})")
			print("    ⚠️  即将执行点击，3秒后执行...")
			await asyncio.sleep(3)

			await browser.click_element(entry.backend_node_id)
			print("    ✅ 点击已执行")

			# 等待下拉选项加载
			print("    等待 2 秒，观察下拉选项是否出现...")
			await asyncio.sleep(2)

			# ── [6] 重新获取 DOM 状态 ────────────────────────────────────
			print("\n[6] 重新获取 DOM 状态，检查下拉选项是否出现...")
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
				print(f"    ✅ 元素增加了 {diff} 个（可能有下拉选项出现）")
			elif diff < 0:
				print(f"    元素减少了 {abs(diff)} 个")
			else:
				print(f"    ⚠️ 元素数量没有变化（下拉框可能没有响应）")

			# 搜索新增元素
			new_ids = set(dom_state2.selector_map.keys()) - set(dom_state.selector_map.keys())
			if new_ids:
				print(f"\n    新增的 {len(new_ids)} 个元素：")
				for nid in sorted(new_ids):
					ne = dom_state2.selector_map[nid]
					cls = (ne.attributes or {}).get('class', '')
					role = (ne.attributes or {}).get('role', '')
					tag = ne.tag_name
					text = ''
					if ne.node_value and ne.node_value.strip():
						text = f' text="{ne.node_value.strip()[:50]}"'
					print(f'      [{nid}] <{tag}> class="{cls}" role="{role}"{text}')
		else:
			print("\n[5] ❌ 没有找到可以点击的下拉框目标元素！")
			print("    请检查页面上'自主声明'区域是否可见，以及下拉框是否已展开。")

	await browser.stop()
	print("\n" + "=" * 80)
	print("测试完成")


if __name__ == "__main__":
	asyncio.run(main())
