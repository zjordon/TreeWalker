"""验证修复后的 type_text 能否成功创建B站标签。

使用方法：
1. 在 Chrome 中打开B站投稿页面，确保标签输入区域可见
2. 确保 Chrome 以 --remote-debugging-port=9222 启动
3. 运行: python examples/debug_bilibili_tag_interact.py
"""

import asyncio
import logging
import sys

sys.path.insert(0, f"{__file__}/../src")

from tree_walker.browser.session import BrowserSession
from tree_walker.config import load_settings

logging.basicConfig(
	level=logging.INFO,
	format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


async def get_tag_count(browser: BrowserSession) -> int:
	result = await browser.execute_js("""
		(function() {
			var container = document.getElementById('tag-container');
			if (!container) return 0;
			var tags = container.querySelectorAll('.tag-item, .peanut-tag, [class*="tag-text"]');
			return tags.length;
		})()
	""")
	return int(result) if result else 0


async def main():
	settings = load_settings()

	if not settings.browser.ws_url:
		print("Error: Cannot connect to Chrome.")
		sys.exit(1)

	browser = BrowserSession(settings.browser)
	await browser.start()
	sid = browser.current_session_id

	print("=" * 80)
	print("验证修复：type_text 逐字符输入 + framework events → B站标签创建")
	print("=" * 80)

	# 查找标签输入框
	state = await browser.get_state(include_screenshot=False)
	tag_input = None
	for idx, elem in state.dom_state.selector_map.items():
		attrs = getattr(elem, 'attributes', {}) or {}
		placeholder = attrs.get('placeholder', '')
		if '标签' in placeholder and '回车' in placeholder and getattr(elem, 'tag_name', '') == 'input':
			tag_input = elem
			print(f"找到标签输入框: index=[{idx}] bid={elem.backend_node_id}")
			break

	if not tag_input:
		print("未找到标签输入框！")
		await browser.stop()
		return

	initial_count = await get_tag_count(browser)
	print(f"初始标签数: {initial_count}")

	# ── Test: click + type_text (逐字符) + send_keys Enter ──
	print("\n" + "-" * 60)
	print("[Test] click_element → type_text (逐字符 + framework events) → send_keys Enter")
	print("-" * 60)

	# 先用 JS 清空输入框
	await browser.execute_js("""
		(function() {
			var input = document.querySelector('input[placeholder*="标签"]');
			if (input) { input.value = ''; input.focus(); }
		})()
	""")
	await asyncio.sleep(0.2)

	# 点击聚焦
	await browser.click_element(tag_input.backend_node_id)
	await asyncio.sleep(0.3)

	# 用新的 type_text（逐字符 + framework events）
	await browser.type_text("Python教程", clear=True)
	await asyncio.sleep(0.5)  # 等待 Vue setTimeout 触发

	# 验证输入值
	value = await browser.execute_js("document.querySelector('input[placeholder*=\"标签\"]')?.value || ''")
	print(f"  type_text 后 value=\"{value}\"")

	# 检查 Vue 组件内部状态
	vue_content = await browser.execute_js("""
		(function() {
			var container = document.getElementById('tag-container');
			if (!container?.__vue__) return 'no vue';
			return 'custom_input_content="' + container.__vue__.custom_input_content + '"';
		})()
	""")
	print(f"  Vue custom_input_content: {vue_content}")

	# 按 Enter
	await browser.send_keys("Enter")
	await asyncio.sleep(1.0)

	# 验证结果
	value_after = await browser.execute_js("document.querySelector('input[placeholder*=\"标签\"]')?.value || ''")
	count_after = await get_tag_count(browser)
	print(f"  Enter 后 value=\"{value_after}\"")
	print(f"  DOM 标签数: {initial_count} → {count_after}")

	# 检查 Vue submitTags（这才是真正的标签列表）
	submit_tags = await browser.execute_js("""
		(function() {
			var container = document.getElementById('tag-container');
			if (!container?.__vue__) return [];
			return container.__vue__.submitTags;
		})()
	""")
	print(f"  Vue submitTags: {submit_tags}")

	success = "Python教程" in str(submit_tags)
	print(f"\n  {'✅ 成功！标签已创建' if success else '❌ 失败，标签未创建'}")

	print(f"\n最终标签数: {count_after}")
	await browser.stop()


if __name__ == "__main__":
	asyncio.run(main())
