"""Tests for CDP data processing functions in dom.py."""

from __future__ import annotations

from dom_snapshot.collector import (
	_build_ax_lookup,
	_build_enhanced_ax_node,
	_build_snapshot_lookup,
	_collect_file_inputs,
	_parse_attrs,
	EMPTY_DOM_STATE,
)
from tree_walker.browser.views import (
	DOMCollectionConfig,
	DOMDegradationLevel,
	DOMRect,
	EnhancedDOMTreeNode,
	EnhancedSnapshotNode,
	NodeType,
)
from tests.conftest import _make_node, _make_snapshot_node


# ── TestParseAttrs ───────────────────────────────────────────────────────


class TestParseAttrs:
	def test_alternating_array_to_dict(self):
		result = _parse_attrs(['id', 'test', 'class', 'btn'])
		assert result == {'id': 'test', 'class': 'btn'}

	def test_truncation_at_200(self):
		long_val = 'x' * 250
		result = _parse_attrs(['data-long', long_val])
		assert len(result['data-long']) == 200

	def test_none_input(self):
		result = _parse_attrs(None)
		assert result == {}


# ── TestBuildSnapshotLookup ──────────────────────────────────────────────


class TestBuildSnapshotLookup:
	def _make_snapshot(self, *, bounds=None, is_clickable=None, styles=None, paint_orders=None, dpr=1.0):
		"""Helper to build a minimal CDP snapshot dict for testing."""
		snapshot = {
			'documents': [{
				'nodes': {
					'backendNodeId': [1, 2, 3],
				},
				'layout': {
					'nodeIndex': [0, 1, 2],
					'bounds': bounds or [[0, 0, 100, 100], [10, 10, 50, 50], [0, 0, 0, 0]],
					'styles': styles or [[0, 1], [2, 3], [4, 5]],
					'paintOrders': paint_orders or [1, 2, 3],
					'clientRects': [[0, 0, 100, 100], [10, 10, 50, 50], [0, 0, 0, 0]],
					'scrollRects': [[0, 0, 100, 100], [10, 10, 50, 50], [0, 0, 0, 0]],
				},
			}],
			'strings': ['block', 'visible', 'auto', '1', 'none', 'visible'],
		}
		if is_clickable is not None:
			snapshot['documents'][0]['nodes']['isClickable'] = {'index': is_clickable}
		return snapshot

	def test_bounds_dpr_conversion(self):
		snapshot = self._make_snapshot(bounds=[[0, 0, 200, 200], [10, 10, 100, 50], [0, 0, 0, 0]])
		lookup = _build_snapshot_lookup(snapshot, device_pixel_ratio=2.0)
		assert lookup[1].bounds is not None
		assert lookup[1].bounds.x == 0.0
		assert lookup[1].bounds.y == 0.0
		assert lookup[1].bounds.width == 100.0
		assert lookup[1].bounds.height == 100.0

	def test_is_clickable_sparse_format(self):
		snapshot = self._make_snapshot(is_clickable=[2])
		lookup = _build_snapshot_lookup(snapshot, device_pixel_ratio=1.0)
		assert lookup[1].is_clickable is None
		assert lookup[2].is_clickable is None
		assert lookup[3].is_clickable is True

	def test_layout_index_first_occurrence(self):
		"""When two layout entries reference the same nodeIndex, the first one wins."""
		snapshot = {
			'documents': [{
				'nodes': {
					'backendNodeId': [1, 2],
				},
				'layout': {
					'nodeIndex': [0, 1, 1],
					'bounds': [[0, 0, 100, 100], [10, 10, 50, 50], [99, 99, 10, 10]],
					'styles': [[0, 1], [2, 3], [4, 5]],
					'paintOrders': [1, 2, 3],
					'clientRects': [[0, 0, 100, 100], [10, 10, 50, 50], [99, 99, 10, 10]],
					'scrollRects': [[0, 0, 100, 100], [10, 10, 50, 50], [99, 99, 10, 10]],
				},
			}],
			'strings': ['block', 'visible', 'auto', '1', 'none', 'visible'],
		}
		lookup = _build_snapshot_lookup(snapshot, device_pixel_ratio=1.0)
		# nodeIndex=1 appears at layout index 1 and 2; first (10,10,50,50) wins
		assert lookup[2].bounds is not None
		assert lookup[2].bounds.x == 10.0
		assert lookup[2].bounds.y == 10.0

	def test_computed_styles_resolved(self):
		# REQUIRED_COMPUTED_STYLES has 10 entries; provide matching strings
		strings = ['flex', 'hidden', '0.5', 'pointer', 'auto', 'auto', 'auto', 'absolute', 'red', 'red']
		styles = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9]]
		snapshot = self._make_snapshot(styles=styles)
		snapshot['strings'] = strings
		lookup = _build_snapshot_lookup(snapshot, device_pixel_ratio=1.0)
		assert lookup[1].computed_styles is not None
		assert lookup[1].computed_styles['display'] == 'flex'
		assert lookup[1].computed_styles['visibility'] == 'hidden'
		assert lookup[1].computed_styles['opacity'] == '0.5'
		assert lookup[1].computed_styles['cursor'] == 'pointer'


# ── TestBuildAXLookup ────────────────────────────────────────────────────


class TestBuildAXLookup:
	def test_backend_node_id_mapping(self):
		ax_tree = {
			'nodes': [
				{'nodeId': 'ax1', 'backendDOMNodeId': 42, 'role': {'value': 'button'}},
				{'nodeId': 'ax2', 'role': {'value': 'link'}},
				{'nodeId': 'ax3', 'backendDOMNodeId': 99, 'role': {'value': 'textbox'}},
			],
		}
		lookup = _build_ax_lookup(ax_tree)
		assert 42 in lookup
		assert 99 in lookup
		assert len(lookup) == 2


# ── TestBuildEnhancedAXNode ──────────────────────────────────────────────


class TestBuildEnhancedAXNode:
	def test_property_extraction(self):
		ax_raw = {
			'nodeId': 'ax1',
			'ignored': False,
			'role': {'value': 'button'},
			'name': {'value': 'Submit'},
			'properties': [
				{'name': 'focusable', 'value': {'value': True, 'type': 'boolean'}},
				{'name': 'expanded', 'value': {'value': False, 'type': 'boolean'}},
			],
		}
		node = _build_enhanced_ax_node(ax_raw)
		assert node.role == 'button'
		assert node.name == 'Submit'
		assert len(node.properties) == 2
		assert node.properties[0].name == 'focusable'

	def test_missing_properties(self):
		ax_raw = {
			'nodeId': 'ax2',
			'ignored': False,
			'role': {'value': 'link'},
			'name': {'value': 'Home'},
		}
		node = _build_enhanced_ax_node(ax_raw)
		assert node.role == 'link'
		assert node.properties is None


# ── TestCollectFileInputs ────────────────────────────────────────────────


class TestCollectFileInputs:
	def test_finds_file_inputs(self):
		dom_tree = {
			'nodeType': 1,
			'nodeName': 'HTML',
			'backendNodeId': 1,
			'children': [
				{
					'nodeType': 1,
					'nodeName': 'BODY',
					'backendNodeId': 2,
					'children': [
						{
							'nodeType': 1,
							'nodeName': 'INPUT',
							'backendNodeId': 3,
							'attributes': ['type', 'file', 'name', 'upload'],
						},
					],
				},
			],
		}
		result = _collect_file_inputs(dom_tree)
		assert [fi.backend_node_id for fi in result] == [3]
		# 无 snapshot_lookup 时 visible 保守为 True；无 accept / 无 upload 祖先
		assert result[0].visible is True
		assert result[0].accept == ""
		assert result[0].upload_ancestor is False

	def test_includes_shadow_dom(self):
		dom_tree = {
			'nodeType': 1,
			'nodeName': 'DIV',
			'backendNodeId': 1,
			'shadowRoots': [
				{
					'nodeType': 1,
					'nodeName': '#document-fragment',
					'backendNodeId': 10,
					'children': [
						{
							'nodeType': 1,
							'nodeName': 'INPUT',
							'backendNodeId': 11,
							'attributes': ['type', 'file'],
						},
					],
				},
			],
		}
		result = _collect_file_inputs(dom_tree)
		assert 11 in [fi.backend_node_id for fi in result]

	def test_includes_content_document(self):
		dom_tree = {
			'nodeType': 1,
			'nodeName': 'HTML',
			'backendNodeId': 1,
			'children': [
				{
					'nodeType': 1,
					'nodeName': 'BODY',
					'backendNodeId': 2,
					'children': [
						{
							'nodeType': 1,
							'nodeName': 'IFRAME',
							'backendNodeId': 3,
							'contentDocument': {
								'nodeType': 1,
								'nodeName': 'HTML',
								'backendNodeId': 4,
								'children': [
									{
										'nodeType': 1,
										'nodeName': 'BODY',
										'backendNodeId': 5,
										'children': [
											{
												'nodeType': 1,
												'nodeName': 'INPUT',
												'backendNodeId': 6,
												'attributes': ['type', 'file'],
											},
										],
									},
								],
							},
						},
					],
				},
			],
		}
		result = _collect_file_inputs(dom_tree)
		assert 6 in [fi.backend_node_id for fi in result]

	def test_collects_accept_and_upload_ancestor(self):
		# 父 DIV class 含 semi-upload → input 的 upload_ancestor=True；accept 被记录
		dom_tree = {
			'nodeType': 1,
			'nodeName': 'DIV',
			'backendNodeId': 1,
			'attributes': ['class', 'semi-upload semi-upload-choose'],
			'children': [
				{
					'nodeType': 1,
					'nodeName': 'INPUT',
					'backendNodeId': 7,
					'attributes': ['type', 'file', 'accept', 'image/png,image/jpeg'],
				},
			],
		}
		result = _collect_file_inputs(dom_tree)
		assert len(result) == 1
		fi = result[0]
		assert fi.backend_node_id == 7
		assert fi.accept == 'image/png,image/jpeg'
		assert fi.upload_ancestor is True

	def test_collects_class_name(self):
		# input 的 class 被记录到 class_name（区分 hidden-input 初次上传 vs -replace 替换，#96）
		dom_tree = {
			'nodeType': 1,
			'nodeName': 'DIV',
			'backendNodeId': 1,
			'children': [
				{
					'nodeType': 1,
					'nodeName': 'INPUT',
					'backendNodeId': 7,
					'attributes': ['type', 'file', 'class', 'semi-upload-hidden-input'],
				},
			],
		}
		result = _collect_file_inputs(dom_tree)
		assert len(result) == 1
		assert result[0].class_name == 'semi-upload-hidden-input'

	def test_class_name_empty_when_absent(self):
		dom_tree = {
			'nodeType': 1,
			'nodeName': 'INPUT',
			'backendNodeId': 8,
			'attributes': ['type', 'file'],
		}
		result = _collect_file_inputs(dom_tree)
		assert result[0].class_name == ""

	def test_visible_false_when_display_none(self):
		from types import SimpleNamespace
		dom_tree = {
			'nodeType': 1,
			'nodeName': 'INPUT',
			'backendNodeId': 9,
			'attributes': ['type', 'file'],
		}
		snapshot_lookup = {9: SimpleNamespace(computed_styles={'display': 'none'})}
		result = _collect_file_inputs(dom_tree, snapshot_lookup=snapshot_lookup)
		assert result[0].visible is False

	def test_visible_true_when_styles_ok(self):
		from types import SimpleNamespace
		dom_tree = {
			'nodeType': 1,
			'nodeName': 'INPUT',
			'backendNodeId': 9,
			'attributes': ['type', 'file'],
		}
		snapshot_lookup = {
			9: SimpleNamespace(computed_styles={'display': 'block', 'visibility': 'visible', 'opacity': '1'}),
		}
		result = _collect_file_inputs(dom_tree, snapshot_lookup=snapshot_lookup)
		assert result[0].visible is True

	def test_visible_branches(self):
		from types import SimpleNamespace
		base = {
			'nodeType': 1,
			'nodeName': 'INPUT',
			'backendNodeId': 9,
			'attributes': ['type', 'file'],
		}
		# visibility:hidden
		r = _collect_file_inputs(base, snapshot_lookup={9: SimpleNamespace(computed_styles={'visibility': 'hidden'})})
		assert r[0].visible is False
		# opacity:0
		r = _collect_file_inputs(base, snapshot_lookup={9: SimpleNamespace(computed_styles={'opacity': '0'})})
		assert r[0].visible is False
		# opacity 非数字（except 分支）→ 保守视为可见
		r = _collect_file_inputs(base, snapshot_lookup={9: SimpleNamespace(computed_styles={'opacity': ''})})
		assert r[0].visible is True
		# bid 不在 snapshot_lookup（snap is None）→ 保守视为可见
		r = _collect_file_inputs(base, snapshot_lookup={999: SimpleNamespace(computed_styles={'display': 'none'})})
		assert r[0].visible is True


# ── TestEmptyDomState ────────────────────────────────────────────────────


class TestEmptyDomState:
	def test_empty_dom_state_fields(self):
		assert EMPTY_DOM_STATE._root is None
		assert EMPTY_DOM_STATE.selector_map == {}
		assert EMPTY_DOM_STATE.element_tree_text == ""


# ── TestDomDegradationLevel ──────────────────────────────────────────────


class TestDomDegradationLevel:
	def test_degradation_level_values(self):
		assert DOMDegradationLevel.FULL.value == 'full'
		assert DOMDegradationLevel.PARTIAL.value == 'partial'
		assert DOMDegradationLevel.MINIMAL.value == 'minimal'
		assert DOMDegradationLevel.FAILED.value == 'failed'

	def test_collection_config_defaults(self):
		config = DOMCollectionConfig()
		assert config.cdp_first_timeout == 10.0
		assert config.cdp_retry_timeout == 2.0
		assert config.max_iframes == 100
		assert config.heavy_page_element_threshold == 10000


# ── TestVisibilityLogic ──────────────────────────────────────────────────


def _is_css_visible(node: EnhancedDOMTreeNode) -> bool:
	"""Replicate the CSS visibility check from dom.py for testing."""
	if not node.snapshot_node:
		return False
	styles = node.snapshot_node.computed_styles or {}
	if styles.get('display', '').lower() == 'none':
		return False
	if styles.get('visibility', '').lower() == 'hidden':
		return False
	try:
		if float(styles.get('opacity', '1')) <= 0:
			return False
	except (ValueError, TypeError):
		pass
	return True


class TestVisibilityLogic:
	def test_display_none_invisible(self):
		node = _make_node(
			tag='div',
			snapshot_node=_make_snapshot_node(computed_styles={'display': 'none', 'visibility': 'visible'}),
		)
		assert not _is_css_visible(node)

	def test_visibility_hidden_invisible(self):
		node = _make_node(
			tag='div',
			snapshot_node=_make_snapshot_node(computed_styles={'display': 'block', 'visibility': 'hidden'}),
		)
		assert not _is_css_visible(node)

	def test_opacity_zero_invisible(self):
		node = _make_node(
			tag='div',
			snapshot_node=_make_snapshot_node(computed_styles={'display': 'block', 'visibility': 'visible', 'opacity': '0'}),
		)
		assert not _is_css_visible(node)

	def test_visible_with_normal_styles(self):
		node = _make_node(
			tag='div',
			snapshot_node=_make_snapshot_node(computed_styles={'display': 'block', 'visibility': 'visible', 'opacity': '1'}),
		)
		assert _is_css_visible(node)
