"""把当前页面「发给模型的 DOM 树」原样写入文件（不含任何诊断信息）。

只 dump element_tree_text —— 即 [Page DOM] 段落里的那棵 `[index]<tag attr=val /> text` 文本树，
是模型定位/操作元素时实际看到的内容。不含 [Task] / [Current URL] / [Page Title] 等
build_state_message 拼接的其它段，也不打印任何摘要。

用法：
  1. Chrome 以 --remote-debugging-port=9222 启动，停在任意页面。
  2. uv run python examples/dump_model_page_view.py [输出文件路径]
     - 输出文件路径：默认 examples/_model_page_view_dump.txt
  3. 标准输出只打印写入的文件路径，DOM 树全在文件里。
"""

import asyncio
import os
import sys

sys.path.insert(0, f"{__file__}/../src")

from tree_walker.browser.session import BrowserSession
from tree_walker.config import load_settings

DEFAULT_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_model_page_view_dump.txt")


async def main():
	out_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT

	settings = load_settings()
	if not settings.browser.ws_url:
		print("Error: 无法连接 Chrome（确认以 --remote-debugging-port=9222 启动）")
		sys.exit(1)

	browser = BrowserSession(settings.browser)
	await browser.start()
	try:
		state = await browser.get_state(include_screenshot=False)
		# 模型在 [Page DOM] 段看到的 DOM 文本树（[index]<tag attr=val /> text）
		tree_text = state.dom_state.element_tree_text or ""
	finally:
		await browser.stop()

	with open(out_path, "w", encoding="utf-8") as f:
		f.write(tree_text)

	# 只在标准输出打印写入的文件路径，DOM 树全在文件里
	print(out_path)


if __name__ == "__main__":
	asyncio.run(main())
