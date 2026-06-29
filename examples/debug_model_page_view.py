"""诊断脚本：打印抖音封面页「发给模型的最终页面结构」。

目的：确认 file input 在 LLM 眼里是怎么呈现的，对照发现：
  - element_tree_text 里 file input 的标记是 [index]（selector_map 的 key，
    模型用它调 upload_file(index=...)）；
  - 但 system_prompt.py:183 的 [File Inputs] 段用的是 [backend_node_id]，
    不是 index —— 两者数值不同时，模型会被误导拿 backend_node_id 当 index。
  - 隐藏 file input 是否被「强制可见」并分配了 index（serializer.py:216-223）；
  - 触发器（上传/封面）与 file input 在序列化树里的相对位置（验证并列 div）。

忠实复现 TreeWalker 每一步发给 LLM 的 user message（页面结构部分）。

用法：
  1. Chrome 以 --remote-debugging-port=9222 启动，停在抖音「封面选择/编辑」区。
  2. uv run python examples/debug_model_page_view.py
  3. 完整输出另存到 examples/_model_page_view.txt（便于编辑器里搜索 file input 行）。
"""

import asyncio
import os
import sys

sys.path.insert(0, f"{__file__}/../src")

from tree_walker.browser.session import BrowserSession
from tree_walker.config import load_settings
from tree_walker.prompts.system_prompt import build_state_message

# element_tree_text 里搜索 file-input / 上传触发器相关行的关键词
TREE_KW = (
	"type=\"file\"", "type='file'",
	"上传", "封面", "选择文件", "选择图片", "本地上传", "更换封面", "设置封面",
	"drag", "upload", "dropzone", "picker",
)


def _short(s, n=80):
	s = (s or "").strip()
	return s if len(s) <= n else s[:n] + "..."


async def main():
	settings = load_settings()
	if not settings.browser.ws_url:
		print("Error: 无法连接 Chrome（确认以 --remote-debugging-port=9222 启动并停在抖音封面区）")
		sys.exit(1)

	print("=" * 92)
	print("抖音封面页 —— 发给模型的最终页面结构")
	print("=" * 92)

	browser = BrowserSession(settings.browser)
	await browser.start()

	print("\n[0] 获取页面状态 get_state(include_screenshot=False) ...")
	state = await browser.get_state(include_screenshot=False)
	dom_state = state.dom_state
	tree_text = dom_state.element_tree_text or ""
	print(f"  url = {state.url}")
	print(f"  title = {state.title}")
	print(f"  selector_map 大小 = {len(dom_state.selector_map)}")
	print(f"  element_tree_text 长度 = {len(tree_text)} 字符 / {len(tree_text.splitlines())} 行")
	print(f"  file_input_backend_ids = {list(dom_state.file_input_backend_ids)}")
	print(f"  file_inputs_meta 数量 = {len(dom_state.file_inputs_meta)}")

	# [1] file input index 对照表（核心：index vs backend_node_id 是否一致）
	print("\n" + "=" * 92)
	print("[1] file input 在 selector_map 中的 index 对照（模型用 [index] 调 upload_file）")
	print("=" * 92)
	file_inputs = []
	for idx in sorted(dom_state.selector_map.keys()):
		n = dom_state.selector_map[idx]
		a = getattr(n, "attributes", None) or {}
		tag = (getattr(n, "tag_name", "") or "").upper()
		if tag == "INPUT" and (a.get("type", "") or "").lower() == "file":
			file_inputs.append((idx, n))
			bid = getattr(n, "backend_node_id", None)
			mismatch = "" if idx == bid else f"   ⚠️ index({idx}) != backend_node_id({bid})"
			print(f"\n  [index {idx}] backend_node_id={bid}{mismatch}")
			print(f"    accept = {a.get('accept', '')!r}")
			print(f"    class  = {_short(a.get('class', ''))!r}")
			print(f"    is_visible(dom 判定) = {getattr(n, 'is_visible', None)}")
	if not file_inputs:
		print("  ⚠️ selector_map 里没有任何 file input")
		print("     （隐藏 input 未被强制可见？或 file_inputs_meta 派生与 selector_map 脱节？）")
	else:
		print(f"\n  共 {len(file_inputs)} 个 file input 出现在模型可见的 selector_map 里")
		print("  注意：element_tree_text 标记 [index]，而下方 [File Inputs] 段标记 [backend_node_id]；")
		print("        若两者不同 → 模型可能拿 backend_node_id 当 index，打到错误元素。")

	# [2] element_tree_text 中 file-input / 触发器 相关行（看触发器与 input 相对位置）
	print("\n" + "=" * 92)
	print("[2] element_tree_text 中 file-input / 上传触发器 相关行（验证触发器与 input 是否并列）")
	print("=" * 92)
	hits = []
	for ln, line in enumerate(tree_text.splitlines(), 1):
		low = line.lower()
		if any(kw.lower() in low for kw in TREE_KW):
			hits.append((ln, line))
	if not hits:
		print("  （未命中关键词；file input 可能不在 element_tree_text 里，或关键词不全）")
	for ln, line in hits[:200]:
		print(f"  L{ln}: {line.rstrip()}")
	if len(hits) > 200:
		print(f"  ... (还有 {len(hits) - 200} 行命中，见 _model_page_view.txt)")

	# [3] 完整 element_tree_text（LLM 看到的页面结构主体）
	print("\n" + "=" * 92)
	print("[3] [Page DOM] element_tree_text 完整内容（LLM 看到的页面结构）")
	print("=" * 92)
	print(tree_text if tree_text else "(empty)")

	# [4] 发给 LLM 的完整 state message（含 [File Inputs] 段，若有）
	print("\n" + "=" * 92)
	print("[4] 发给 LLM 的完整 state message（build_state_message，含 [File Inputs] 段）")
	print("=" * 92)
	msg = build_state_message(state, task="(debug) 上传抖音封面")
	print(msg)

	# 另存完整输出到文件（element_tree_text 可能很长，便于编辑器搜索）
	out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_model_page_view.txt")
	with open(out_path, "w", encoding="utf-8") as f:
		f.write("=" * 92 + "\n[1] file input index 对照\n" + "=" * 92 + "\n")
		for idx, n in file_inputs:
			a = getattr(n, "attributes", None) or {}
			f.write(f"[index {idx}] backend_node_id={getattr(n,'backend_node_id',None)} "
					f"accept={a.get('accept','')!r} visible={getattr(n,'is_visible',None)}\n")
		f.write("\n" + "=" * 92 + "\n[Page DOM] element_tree_text\n" + "=" * 92 + "\n")
		f.write(tree_text)
		f.write("\n\n" + "=" * 92 + "\n[State Message]\n" + "=" * 92 + "\n")
		f.write(msg)
	print("\n" + "=" * 92)
	print(f"完整输出另存: {out_path}")

	await browser.stop()
	print("打印完成")


if __name__ == "__main__":
	asyncio.run(main())
