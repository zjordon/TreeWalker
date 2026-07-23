"""locator 单元测试：xpath 归一化 + selector_map 定位 + 多候选就近。"""

from types import SimpleNamespace

from tree_walker.recorder.locator import locate_by_ref, locate_by_xpath, normalize_text, normalize_xpath


def _node(xpath: str, bounds=None):
	"""构造 mock EnhancedDOMTreeNode：有 ``xpath`` 属性 + ``snapshot_node.bounds``。"""
	snap = SimpleNamespace(bounds=bounds) if bounds is not None else None
	return SimpleNamespace(xpath=xpath, snapshot_node=snap)


def _rect(x, y, w, h):
	return {"x": x, "y": y, "width": w, "height": h}


def test_normalize_xpath_strips_leading_slash():
	assert normalize_xpath("/html/body/form") == "html/body/form"
	assert normalize_xpath("html/body/form") == "html/body/form"
	assert normalize_xpath(None) == ""
	assert normalize_xpath("  /html  ") == "html"


def test_locate_single_match():
	selector_map = {5: _node("html/body/form/input[1]")}
	assert locate_by_xpath("/html/body/form/input[1]", selector_map) == (5, selector_map[5])


def test_locate_normalizes_slash_format():
	# 扩展发 /html/...（Browser-BC xpathFor 风格），selector_map 节点是 html/...（TreeWalker）
	selector_map = {7: _node("html/body/button")}
	assert locate_by_xpath("/html/body/button", selector_map) == (7, selector_map[7])


def test_locate_no_match_returns_none():
	selector_map = {5: _node("html/body/form")}
	assert locate_by_xpath("html/body/missing", selector_map) is None


def test_locate_empty_xpath_returns_none():
	assert locate_by_xpath("", {1: _node("html")}) is None
	assert locate_by_xpath(None, {1: _node("html")}) is None


def test_locate_multiple_candidates_without_rect():
	a = _node("html/body/li", _rect(0, 0, 10, 10))
	b = _node("html/body/li", _rect(500, 500, 10, 10))
	selector_map = {1: a, 2: b}
	# 无 rect 提示，返回首个（迭代序）；只要能返回其中一个即可
	idx, _ = locate_by_xpath("html/body/li", selector_map)
	assert idx in (1, 2)


def test_locate_multiple_candidates_picks_nearest_by_rect():
	a = _node("html/body/li", _rect(0, 0, 10, 10))      # 中心 (5,5)
	b = _node("html/body/li", _rect(500, 500, 10, 10))  # 中心 (505,505)
	selector_map = {1: a, 2: b}
	# rect 提示在 (490,490) 附近 → 应选 b(idx=2)
	idx, _ = locate_by_xpath("html/body/li", selector_map, rect=_rect(488, 488, 4, 4))
	assert idx == 2


def test_locate_ignores_nodes_with_empty_xpath():
	selector_map = {1: _node(""), 2: _node("html/body/a")}
	assert locate_by_xpath("html/body/a", selector_map) == (2, selector_map[2])


# ── locate_by_ref：多级定位（xpath → ATTRIBUTE 兜底）────────────────────


def _ref_node(xpath: str, tag: str = "DIV", attrs: dict | None = None):
	"""mock EnhancedDOMTreeNode：有 xpath + node_name + attributes（locate_by_ref 用）。"""
	return SimpleNamespace(xpath=xpath, node_name=tag, attributes=attrs or {}, snapshot_node=None)


def test_locate_by_ref_xpath_takes_priority():
	node = _ref_node("html/body/btn", "BUTTON", {})
	sm = {5: node}
	r = locate_by_ref({"xpath": "/html/body/btn", "tag": "button", "name": "x"}, sm)
	assert r == (5, node)


def test_locate_by_ref_attribute_fallback_by_name():
	# xpath 不匹配（深嵌套 modal 路径差异），用 input 的 name 属性兜底
	node = _ref_node("html/body/div[99]/input", "INPUT", {"name": "video-title"})
	sm = {42: node}
	r = locate_by_ref(
		{"xpath": "/html/body/div[1]/input", "tag": "input", "name": "video-title"},
		sm,
	)
	assert r == (42, node)


def test_locate_by_ref_attribute_fallback_by_aria_label():
	node = _ref_node("html/x", "BUTTON", {"aria-label": "提交"})
	sm = {7: node}
	r = locate_by_ref({"xpath": "/html/missing", "tag": "button", "ariaLabel": "提交"}, sm)
	assert r == (7, node)


def test_locate_by_ref_tag_must_match():
	# name 值相同但 tag 不同（button vs input），不匹配
	node = _ref_node("html/x", "INPUT", {"name": "q"})
	sm = {1: node}
	r = locate_by_ref({"xpath": "/html/y", "tag": "button", "name": "q"}, sm)
	assert r is None


def test_locate_by_ref_returns_none_when_nothing_matches():
	node = _ref_node("html/x", "DIV", {"name": "other"})
	sm = {1: node}
	r = locate_by_ref({"xpath": "/html/y", "tag": "button", "name": "z"}, sm)
	assert r is None


# ── locate_by_ref Level 3：RECT（位置）兜底 ──────────────────────────────


def _ref_node_b(xpath, tag, bounds):
	"""带 bounds 的 ref 节点（Level 3 RECT 用）。"""
	return SimpleNamespace(xpath=xpath, node_name=tag, attributes={}, snapshot_node=SimpleNamespace(bounds=bounds))


def test_locate_by_ref_rect_fallback_picks_matching_sized_node():
	# xpath/属性都失配，节点 bounds 与点击 rect 高度重合（IoU≈1）→ RECT 兜底命中
	node = _ref_node_b("html/x", "DIV", _rect(100, 100, 160, 120))
	sm = {9: node}
	r = locate_by_ref(
		{"xpath": "/html/missing", "tag": "div", "rect": _rect(102, 102, 156, 116)},  # IoU 高
		sm,
	)
	assert r == (9, node)


def test_locate_by_ref_rect_fallback_picks_size_match_not_tiny_icon():
	# 点击 cover(160×120) 触发器，中心落在其内 18×18 svg 图标上——
	# IoU 选 cover（bounds≈点击 rect），不选 svg（IoU 极低）。这是「选择封面」场景的核心。
	cover = _ref_node_b("html/cover", "DIV", _rect(100, 100, 160, 120))
	svg = _ref_node_b("html/svg", "SVG", _rect(150, 130, 18, 18))  # 在 cover 内
	sm = {1: cover, 2: svg}
	# ref rect = cover（findInteractiveAncestor 返回的触发器），中心 (180,160) 落在 svg 区
	r = locate_by_ref({"xpath": "/html/missing", "tag": "div", "rect": _rect(100, 100, 160, 120)}, sm)
	assert r == (1, cover)


def test_locate_by_ref_rect_fallback_rejects_huge_ancestor():
	# 点击 rect 只占 root 极小比例（IoU < 0.1）→ 不认 root（避免误匹配整页容器）
	root = _ref_node_b("html/root", "DIV", _rect(0, 0, 1000, 800))
	sm = {1: root}
	# 小点击 (100,100,50,50) 落在 root 内但 IoU≈0.003；root 中心远 → None
	r = locate_by_ref({"xpath": "/html/missing", "tag": "div", "rect": _rect(100, 100, 50, 50)}, sm)
	assert r is None


def test_locate_by_ref_rect_fallback_nearest_when_no_high_iou():
	# 无节点含点击中心（IoU 通道空）→ 就近（阈值 150px 内）
	a = _ref_node_b("html/a", "DIV", _rect(0, 0, 10, 10))     # 中心 (5,5)
	b = _ref_node_b("html/b", "DIV", _rect(100, 100, 10, 10))  # 中心 (105,105)
	sm = {1: a, 2: b}
	# 点击中心 (110,110)：不在任何节点内，距 b(105,105) ≈ 7px < 150 → 选 b
	r = locate_by_ref({"xpath": "/html/missing", "tag": "div", "rect": _rect(108, 108, 4, 4)}, sm)
	assert r == (2, b)


def test_locate_by_ref_rect_fallback_threshold_exceeded():
	# 最近节点也超过 150px → 返回 None（不乱点远处元素）
	a = _ref_node_b("html/a", "DIV", _rect(0, 0, 10, 10))  # 中心 (5,5)
	sm = {1: a}
	# 点击中心 (300,300)：距 a ≈ 417px > 150 → None
	r = locate_by_ref({"xpath": "/html/missing", "tag": "div", "rect": _rect(298, 298, 4, 4)}, sm)
	assert r is None


def test_locate_by_ref_rect_no_rect_returns_none():
	# ref 无 rect → Level 3 不触发（保持旧行为，不引入误匹配）
	node = _ref_node("html/x", "DIV", {"name": "other"})
	sm = {1: node}
	r = locate_by_ref({"xpath": "/html/y", "tag": "div"}, sm)
	assert r is None


# ── normalize_text + locate_by_ref Level 0：TEXT ─────────────────────────


def test_normalize_text_strips_all_whitespace():
	# 移除全部空白（不只折叠）：抖音 tab 每字独立 span → "设\n置\n竖\n封\n面" 须变 "设置竖封面"
	assert normalize_text("  设置\n竖  封面  ") == "设置竖封面"
	assert normalize_text("设 置 竖 封 面") == "设置竖封面"
	assert normalize_text(None) == ""
	assert normalize_text("") == ""


def test_normalize_text_truncates_to_120():
	assert normalize_text("a" * 200) == "a" * 120


def _text_ref_node(xpath, tag="DIV", text="", attrs=None):
	"""带 get_all_children_text 的 mock 节点（TEXT 级用）。"""
	return SimpleNamespace(
		xpath=xpath, node_name=tag, attributes=attrs or {},
		snapshot_node=None, get_all_children_text=lambda: text,
	)


def test_locate_by_ref_text_matches_correct_tab():
	# cover step tab：两个同 class 的 DIV，仅靠文字（设置横/竖封面）区分（issue #136）
	h_tab = _text_ref_node("html/body/div[1]/div[1]", "DIV", "设置横封面", {"class": "step-dXVbPX step-active-AWDV7U"})
	v_tab = _text_ref_node("html/body/div[1]/div[2]", "DIV", "设置竖封面", {"class": "step-dXVbPX"})
	sm = {1: h_tab, 2: v_tab}
	# xpath 故意失配（瞬时漂移），靠 text 命中设置竖封面
	r = locate_by_ref({"xpath": "/html/missing", "tag": "div", "text": "设置竖封面"}, sm)
	assert r == (2, v_tab)


def test_locate_by_ref_text_tag_must_match():
	# 文字相同但 tag 不同（div vs button）→ TEXT 不命中；无 xpath/属性/rect 兜底 → None
	btn = _text_ref_node("html/btn", "BUTTON", "设置竖封面")
	sm = {1: btn}
	r = locate_by_ref({"xpath": "/html/x", "tag": "div", "text": "设置竖封面"}, sm)
	assert r is None


def test_locate_by_ref_no_text_falls_back_to_xpath():
	# 无 text → TEXT 级跳过，走 xpath（向后兼容）
	node = _ref_node("html/body/btn", "BUTTON", {})
	sm = {5: node}
	r = locate_by_ref({"xpath": "/html/body/btn", "tag": "button"}, sm)
	assert r == (5, node)
