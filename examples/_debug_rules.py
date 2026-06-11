"""诊断脚本：逐条检查 ClickableElementDetector 14 条规则对自主声明下拉框的匹配结果。"""
import asyncio
import sys

sys.path.insert(0, f"{__file__}/../src")

from browser_agent.browser.dom import build_dom_state, _detect_js_click_listeners
from browser_agent.browser.session import BrowserSession
from browser_agent.browser.views import NodeType
from browser_agent.config import load_settings

# 目标元素 class 列表
TARGET_CLASSES = [
	'selectBox-buZRzi',    # 下拉框本体
	'selectText-XSrMFZ',   # 下拉框文本
	'wrapper-MLZdnB',      # section 容器
	'title-cnbkZe',        # "自主声明" 标题
	'controlWrapper-Kt_9Xm',  # 控件容器
	'chevron-euwXR1',      # 箭头图标
	'labelWrapper-p6osJm', # 标签容器
]


def check_rule_1(node):
	"""规则 1: 节点类型守卫"""
	if node.node_type != NodeType.ELEMENT_NODE:
		return False, "SKIP (非 ELEMENT_NODE)"
	return True, "PASS (是 ELEMENT_NODE)"


def check_rule_2(node):
	"""规则 2: html/body 排除"""
	if node.tag_name in {'html', 'body'}:
		return False, "MATCH → return False (html/body)"
	return True, "PASS (不是 html/body)"


def check_rule_3(node):
	"""规则 3: JS 点击监听器"""
	if node.has_js_click_listener:
		return None, "MATCH → return True (有 JS click listener)"
	return True, "SKIP (无 JS click listener)"


def check_rule_4(node):
	"""规则 4: IFRAME/FRAME"""
	if node.tag_name in {'iframe', 'frame'}:
		if node.snapshot_node and node.snapshot_node.bounds:
			b = node.snapshot_node.bounds
			if b.width > 100 and b.height > 100:
				return None, "MATCH → return True (大尺寸 iframe)"
	return True, "SKIP (不是 iframe)"


def check_rule_5(node):
	"""规则 5: Label 处理"""
	if node.tag_name == 'label':
		if node.attributes and node.attributes.get('for'):
			return False, "MATCH → return False (label[for])"
	return True, "SKIP (不是 label)"


def check_rule_9(node):
	"""规则 9: 交互标签"""
	interactive_tags = {'button', 'input', 'select', 'textarea', 'a', 'details', 'summary', 'option', 'optgroup'}
	if node.tag_name in interactive_tags:
		return None, f"MATCH → return True (interactive tag: {node.tag_name})"
	return True, f"SKIP (tag={node.tag_name}, 不在交互标签中)"


def check_rule_10(node):
	"""规则 10: 交互 HTML 属性"""
	if node.attributes:
		interactive_attributes = {'onclick', 'onmousedown', 'onmouseup', 'onkeydown', 'onkeyup', 'tabindex'}
		matched = [a for a in interactive_attributes if a in node.attributes]
		if matched:
			return None, f"MATCH → return True (有交互属性: {matched})"
	return True, "SKIP (无交互属性)"


def check_rule_11(node):
	"""规则 11: ARIA role"""
	if node.attributes and 'role' in node.attributes:
		interactive_roles = {'button', 'link', 'menuitem', 'option', 'radio', 'checkbox', 'tab', 'textbox', 'combobox', 'slider', 'spinbutton', 'search', 'searchbox', 'row', 'cell', 'gridcell'}
		role = node.attributes['role']
		if role in interactive_roles:
			return None, f"MATCH → return True (role={role})"
		else:
			return True, f"SKIP (role={role}, 不在交互 role 中)"
	return True, "SKIP (无 role 属性)"


def check_rule_12(node):
	"""规则 12: AX 树 role"""
	if node.ax_node and node.ax_node.role:
		interactive_ax_roles = {'button', 'link', 'menuitem', 'option', 'radio', 'checkbox', 'tab', 'textbox', 'combobox', 'slider', 'spinbutton', 'listbox', 'search', 'searchbox', 'row', 'cell', 'gridcell'}
		role = node.ax_node.role
		if role in interactive_ax_roles:
			return None, f"MATCH → return True (ax_role={role})"
		else:
			return True, f"SKIP (ax_role={role}, 不在交互 ax_role 中)"
	return True, "SKIP (无 ax_node 或无 role)"


def check_rule_13(node):
	"""规则 13: 图标尺寸元素"""
	if node.snapshot_node and node.snapshot_node.bounds:
		b = node.snapshot_node.bounds
		if 10 <= b.width <= 50 and 10 <= b.height <= 50:
			if node.attributes:
				icon_attributes = {'class', 'role', 'onclick', 'data-action', 'aria-label'}
				matched = [a for a in icon_attributes if a in node.attributes]
				if matched:
					return None, f"MATCH → return True (图标尺寸 {b.width:.0f}x{b.height:.0f}, 有属性: {matched})"
			return True, f"SKIP (图标尺寸 {b.width:.0f}x{b.height:.0f} 但无图标属性)"
	return True, f"SKIP (尺寸不在 10-50px 范围)"


def check_rule_14(node):
	"""规则 14: cursor: pointer"""
	if node.snapshot_node and node.snapshot_node.cursor_style:
		cursor = node.snapshot_node.cursor_style
		if cursor == 'pointer':
			return None, f"MATCH → return True (cursor_style={cursor})"
		else:
			return True, f"SKIP (cursor_style={cursor})"
	return True, "SKIP (无 cursor_style)"


def check_all_rules(node):
	"""逐条检查所有规则，返回结果列表。"""
	results = []

	# Rule 1
	cont, msg = check_rule_1(node)
	results.append(("Rule 1: 节点类型", cont, msg))
	if not cont:
		return results

	# Rule 2
	cont, msg = check_rule_2(node)
	results.append(("Rule 2: html/body", cont, msg))
	if not cont:
		return results

	# Rule 3
	cont, msg = check_rule_3(node)
	results.append(("Rule 3: JS click listener", cont, msg))
	if cont is None:
		return results

	# Rule 4
	cont, msg = check_rule_4(node)
	results.append(("Rule 4: IFRAME/FRAME", cont, msg))
	if cont is None:
		return results

	# Rule 5
	cont, msg = check_rule_5(node)
	results.append(("Rule 5: Label", cont, msg))
	if not cont:
		return results

	# Rule 6: span wrapper — 跳过，需要检查子元素
	results.append(("Rule 6: Span wrapper", True, "SKIP (需要检查子元素,这里简化)"))

	# Rule 7: search
	results.append(("Rule 7: 搜索元素", True, "SKIP (class 不含搜索关键词)"))

	# Rule 8: AX 属性
	has_ax_match = False
	if node.ax_node and node.ax_node.properties:
		for prop in node.ax_node.properties:
			try:
				if prop.name == 'disabled' and prop.value:
					results.append(("Rule 8: AX 属性", False, f"MATCH → return False (disabled={prop.value})"))
					return results
				if prop.name == 'hidden' and prop.value:
					results.append(("Rule 8: AX 属性", False, f"MATCH → return False (hidden={prop.value})"))
					return results
				if prop.name in ('focusable', 'editable', 'settable') and prop.value:
					has_ax_match = True
				if prop.name in ('checked', 'expanded', 'pressed', 'selected'):
					has_ax_match = True
				if prop.name in ('required', 'autocomplete') and prop.value:
					has_ax_match = True
				if prop.name == 'keyshortcuts' and prop.value:
					has_ax_match = True
			except (AttributeError, ValueError):
				continue
	if has_ax_match:
		results.append(("Rule 8: AX 属性", None, "MATCH → return True (有交互 AX 属性)"))
		return results
	results.append(("Rule 8: AX 属性", True, "SKIP (无交互 AX 属性)"))

	# Rule 9
	cont, msg = check_rule_9(node)
	results.append(("Rule 9: 交互标签", cont, msg))
	if cont is None:
		return results

	# Rule 10
	cont, msg = check_rule_10(node)
	results.append(("Rule 10: 交互属性", cont, msg))
	if cont is None:
		return results

	# Rule 11
	cont, msg = check_rule_11(node)
	results.append(("Rule 11: ARIA role", cont, msg))
	if cont is None:
		return results

	# Rule 12
	cont, msg = check_rule_12(node)
	results.append(("Rule 12: AX 树 role", cont, msg))
	if cont is None:
		return results

	# Rule 13
	cont, msg = check_rule_13(node)
	results.append(("Rule 13: 图标尺寸", cont, msg))
	if cont is None:
		return results

	# Rule 14
	cont, msg = check_rule_14(node)
	results.append(("Rule 14: cursor:pointer", cont, msg))
	if cont is None:
		return results

	# 没有任何规则命中
	results.append(("最终结果", False, "NO MATCH → return False"))
	return results


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

	print("=" * 80)
	print("ClickableElementDetector 14 条规则逐条检查")
	print("=" * 80)

	# 获取 JS click listeners
	click_ids = await _detect_js_click_listeners(browser.client, browser.current_session_id)
	print(f"\nJS click listeners 总数: {len(click_ids)}")

	# 找到目标元素
	target_nodes = {}
	for bid, entry in dom_state.selector_map.items():
		cls = (entry.attributes or {}).get('class', '')
		for target_cls in TARGET_CLASSES:
			if target_cls in cls:
				target_nodes.setdefault(target_cls, []).append((bid, entry))

	# 也检查 JS click listener
	print("\n" + "-" * 80)
	print("检查 JS click listener 是否命中目标元素:")
	for target_cls in TARGET_CLASSES:
		nodes = target_nodes.get(target_cls, [])
		for bid, node in nodes:
			has_listener = node.backend_node_id in click_ids
			print(f"  {target_cls} [{bid}]: has_js_click_listener={has_listener}")

	# 对每个目标元素逐条检查
	for target_cls in TARGET_CLASSES:
		nodes = target_nodes.get(target_cls, [])
		for bid, node in nodes:
			print(f"\n{'=' * 80}")
			print(f"元素: [{bid}] <{node.tag_name}> class=\"{(node.attributes or {}).get('class', '')}\"")
			print(f"  尺寸: {node.width}x{node.height}, 可见: {node.is_visible}")
			snap = node.snapshot_node
			if snap:
				print(f"  snapshot.is_clickable={snap.is_clickable}")
				print(f"  snapshot.cursor_style={snap.cursor_style}")
				if snap.bounds:
					print(f"  snapshot.bounds: {snap.bounds.width:.0f}x{snap.bounds.height:.0f}")
			ax = node.ax_node
			if ax:
				print(f"  ax_node.role={ax.role}")
				if ax.properties:
					props = {p.name: p.value for p in ax.properties}
					print(f"  ax_node.properties={props}")
			else:
				print(f"  ax_node=None")
			print(f"  has_js_click_listener={node.has_js_click_listener}")
			print()

			results = check_all_rules(node)
			print("  规则检查结果:")
			for rule_name, cont, msg in results:
				if cont is None:
					status = "✅ 命中"
				elif not cont:
					status = "❌ 排除"
				else:
					status = "⏭️  跳过"
				print(f"    {status} {rule_name}: {msg}")

	await browser.stop()
	print(f"\n{'=' * 80}")
	print("检查完成")


if __name__ == "__main__":
	asyncio.run(main())
