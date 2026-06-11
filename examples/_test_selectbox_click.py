"""临时脚本：专门测试点击 selectBox-buZRzi 下拉框。"""
import asyncio
import sys

sys.path.insert(0, f"{__file__}/../src")

from browser_agent.browser.dom import build_dom_state
from browser_agent.browser.session import BrowserSession
from browser_agent.config import load_settings


async def main():
	settings = load_settings()
	browser = BrowserSession(settings.browser)
	await browser.start()

	dom_state, _ = await build_dom_state(
		client=browser.client,
		session_id=browser.current_session_id,
		config=browser._dom_collection_config,
		previous_selector_map=None,
	)

	# 找到 selectBox-buZRzi 元素
	target_bid = None
	target_entry = None
	for bid, entry in dom_state.selector_map.items():
		cls = (entry.attributes or {}).get('class', '')
		if 'selectBox-buZRzi' in cls:
			target_bid = bid
			target_entry = entry
			break

	if not target_entry:
		print("❌ 没有找到 selectBox-buZRzi")
		# 尝试找 selectText
		for bid, entry in dom_state.selector_map.items():
			cls = (entry.attributes or {}).get('class', '')
			if 'selectText-XSrMFZ' in cls:
				target_bid = bid
				target_entry = entry
				print(f"改为点击 selectText-XSrMFZ [{bid}]")
				break

	if not target_entry:
		print("❌ 也没找到 selectText-XSrMFZ")
		await browser.stop()
		return

	print(f"找到目标: [{target_bid}] class='{(target_entry.attributes or {}).get('class', '')}'")
	print(f"  坐标=({target_entry.x}, {target_entry.y}) 尺寸={target_entry.width}x{target_entry.height}")
	print(f"  is_clickable={target_entry.snapshot_node.is_clickable if target_entry.snapshot_node else 'N/A'}")
	print()
	print("⚠️  3秒后点击...")
	await asyncio.sleep(3)

	await browser.click_element(target_entry.backend_node_id)
	print("✅ 点击已执行")

	await asyncio.sleep(2)

	# 重新获取 DOM
	dom_state2, _ = await build_dom_state(
		client=browser.client,
		session_id=browser.current_session_id,
		config=browser._dom_collection_config,
		previous_selector_map=None,
	)
	diff = len(dom_state2.selector_map) - len(dom_state.selector_map)
	print(f"\n元素变化: {diff:+d} (旧={len(dom_state.selector_map)}, 新={len(dom_state2.selector_map)})")

	new_ids = set(dom_state2.selector_map.keys()) - set(dom_state.selector_map.keys())
	if new_ids:
		print(f"\n新增 {len(new_ids)} 个元素：")
		for nid in sorted(new_ids):
			ne = dom_state2.selector_map[nid]
			cls = (ne.attributes or {}).get('class', '')
			role = (ne.attributes or {}).get('role', '')
			tag = ne.tag_name
			text = (ne.node_value or '').strip()[:60]
			print(f'  [{nid}] <{tag}> class="{cls}" role="{role}" text="{text}"')
	else:
		print("⚠️ 没有新增元素，下拉框可能没有响应！")

	await browser.stop()


if __name__ == "__main__":
	asyncio.run(main())
