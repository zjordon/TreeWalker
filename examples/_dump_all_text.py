"""临时脚本：打印页面上所有有文本的元素。"""
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
	print(f"selector_map 大小={len(dom_state.selector_map)}")
	print()
	for bid, entry in dom_state.selector_map.items():
		text = (entry.node_value or "").strip()
		cls = (entry.attributes or {}).get("class", "")
		role = (entry.attributes or {}).get("role", "")
		tag = entry.tag_name
		if text and len(text) < 200:
			print(f"[{bid}] <{tag}> class='{cls}' role='{role}' text='{text[:80]}'")
	await browser.stop()


if __name__ == "__main__":
	asyncio.run(main())
