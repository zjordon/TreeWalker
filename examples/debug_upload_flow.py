"""抖音实证（issue #34 Bug 2 修复验证）：用新的 upload_file 流程上传"设置竖封面"。

验证修复后的行为（应取代旧的"永远塞进第一个 input 11504"）：
  - click 发现：点 dropzone，捕获 fileChooserOpened.backendNodeId；
  - 命中 → 上传到页面真正关联的 input（正确）；
  - 未命中（抖音顶层 dropzone 开自定义封面弹窗）→ 返回诚实 error，引导 agent
    进弹窗点"上传图片"，**不再静默误传**。

副作用：会点击"设置竖封面"打开封面弹窗（可手动关闭）；未命中时不传任何文件。

用法：uv run python examples/debug_upload_flow.py
"""

import asyncio
import logging
import sys

sys.path.insert(0, f"{__file__}/../src")

from tree_walker.browser.session import BrowserSession
from tree_walker.config import load_settings
from tree_walker.tools.actions import Tools

logging.basicConfig(level=logging.INFO,
					format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

VERTICAL_COVER = r"D:\dev\git\claude\skills-deom\ppt\browser-use\竖封面.png"


def _node_text(node) -> str:
	"""简版：本节点 + 浅层子节点的 aria-label/title/class/node_value。"""
	parts = []

	def walk(n, depth):
		if n is None or depth < 0:
			return
		a = getattr(n, "attributes", None) or {}
		for k in ("aria-label", "title", "class", "name"):
			v = a.get(k)
			if v:
				parts.append(str(v))
		nv = (getattr(n, "node_value", "") or "").strip()
		if nv:
			parts.append(nv)
		for c in (getattr(n, "children_nodes", None) or []):
			walk(c, depth - 1)

	walk(node, 2)
	return " ".join(parts)


def _find_vertical_cover_dropzone(selector_map):
	"""返回 selector_map 里文本含'竖'+'封面'的可交互元素 index。"""
	cands = []
	for idx, node in selector_map.items():
		t = _node_text(node)
		if "封面" in t and "竖" in t:
			cands.append((idx, t.strip()[:40]))
	return cands


async def main():
	settings = load_settings()
	if not settings.browser.ws_url:
		print("Error: 无法连接 Chrome（--remote-debugging-port=9222？）")
		sys.exit(1)

	print("=" * 92)
	print("抖音 upload_file 新流程实证（设置竖封面）")
	print("=" * 92)

	browser = BrowserSession(settings.browser)
	await browser.start()
	print(f"file-chooser 拦截已启用: {browser._file_chooser_intercept_enabled}")

	print("\n[1] 获取页面状态，定位'设置竖封面' dropzone ...")
	state = await browser.get_state()
	fids = list(state.dom_state.file_input_backend_ids)
	print(f"  file_input_backend_ids = {fids}")
	dzs = _find_vertical_cover_dropzone(state.dom_state.selector_map)
	if not dzs:
		print("  ⚠️ 没找到含'竖'+'封面'的 dropzone。确认页面停在封面选择区。")
		await browser.stop(); return
	for idx, t in dzs:
		print(f"    index={idx}  text={t!r}")
	idx = dzs[0][0]
	entry = state.dom_state.selector_map[idx]
	print(f"\n  选定 index={idx} (bid={entry.backend_node_id}) 作为上传目标")

	# [2] 直接调 discover，看点击是否触发 chooser
	print("\n[2] discover_file_input_via_click(竖 dropzone) ...")
	discovered = await browser.discover_file_input_via_click(entry.backend_node_id)
	print(f"  → discovered backendNodeId = {discovered}"
		  + ("（命中：页面关联的 input）" if discovered is not None
			 else "（未命中：顶层 dropzone 开自定义弹窗，符合预期）"))

	# [3] 真实跑 upload_file（新流程），看 ActionResult
	print(f"\n[3] 用新流程 upload_file(index={idx}, 竖封面.png) ...")
	tools = Tools(allowed_upload_paths=[VERTICAL_COVER])
	# 用最新状态（上一步 click 可能改变了 DOM）
	state2 = await browser.get_state()
	dz2 = _find_vertical_cover_dropzone(state2.dom_state.selector_map)
	idx2 = dz2[0][0] if dz2 else idx
	result = await tools.execute(
		"upload_file", {"index": idx2, "path": VERTICAL_COVER}, browser, browser_state=state2,
	)

	print("\n" + "=" * 92)
	print("[4] 结果")
	print("=" * 92)
	if result.error:
		print("  ActionResult.error =")
		print(f"    {result.error}")
		if "custom upload dialog" in result.error:
			print("\n  ✅ 修复生效：未瞎猜/未误传到 11504，而是诚实引导 agent 进弹窗。")
			print("     （旧逻辑会静默把 竖封面.png 塞进 file_input_ids[0] → 抖音报「不支持的图片格式」→ 反复重传）")
	else:
		print("  ActionResult.extracted_content =")
		print(f"    {result.extracted_content}")
		print("\n  ✅ 上传成功（命中页面关联的 input）。")

	await browser.stop()


if __name__ == "__main__":
	asyncio.run(main())
