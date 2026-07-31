"""Tests for DOMTreeSerializer._add_compound_components — compound component generation."""

import pytest

from dom_snapshot.serializer import DOMTreeSerializer
from tests.conftest import (
	_make_ax_node,
	_make_ax_property,
	_make_node,
	_make_simplified_node,
	_make_text_node,
)


def _make_serializer():
	"""Create a DOMTreeSerializer with a dummy root node."""
	return DOMTreeSerializer(root_node=_make_node(tag='body'))


# ── TestInputRange ──────────────────────────────────────────────────────


class TestInputRange:
	def test_slider_with_min_max(self):
		node = _make_node(tag='input', attributes={'type': 'range', 'min': '0', 'max': '100'})
		simplified = _make_simplified_node(original_node=node)
		serializer = _make_serializer()
		serializer._add_compound_components(simplified, node)

		assert simplified.is_compound_component
		assert len(node._compound_children) == 1
		child = node._compound_children[0]
		assert child['role'] == 'slider'
		assert child['name'] == 'Value'
		assert child['valuemin'] == 0.0
		assert child['valuemax'] == 100.0

	def test_slider_default_min_max(self):
		node = _make_node(tag='input', attributes={'type': 'range'})
		simplified = _make_simplified_node(original_node=node)
		serializer = _make_serializer()
		serializer._add_compound_components(simplified, node)

		assert simplified.is_compound_component
		child = node._compound_children[0]
		assert child['role'] == 'slider'
		assert child['valuemin'] == 0.0
		assert child['valuemax'] == 100.0


# ── TestInputNumber ─────────────────────────────────────────────────────


class TestInputNumber:
	def test_number_compound(self):
		node = _make_node(tag='input', attributes={'type': 'number'})
		simplified = _make_simplified_node(original_node=node)
		serializer = _make_serializer()
		serializer._add_compound_components(simplified, node)

		assert simplified.is_compound_component
		assert len(node._compound_children) == 3
		assert node._compound_children[0]['role'] == 'button'
		assert node._compound_children[0]['name'] == 'Increment'
		assert node._compound_children[1]['role'] == 'button'
		assert node._compound_children[1]['name'] == 'Decrement'
		assert node._compound_children[2]['role'] == 'textbox'
		assert node._compound_children[2]['name'] == 'Value'

	def test_number_with_min_max(self):
		node = _make_node(tag='input', attributes={'type': 'number', 'min': '10', 'max': '50'})
		simplified = _make_simplified_node(original_node=node)
		serializer = _make_serializer()
		serializer._add_compound_components(simplified, node)

		assert simplified.is_compound_component
		value_child = node._compound_children[2]
		assert value_child['valuemin'] == 10.0
		assert value_child['valuemax'] == 50.0


# ── TestInputColor ──────────────────────────────────────────────────────


class TestInputColor:
	def test_color_compound(self):
		node = _make_node(tag='input', attributes={'type': 'color'})
		simplified = _make_simplified_node(original_node=node)
		serializer = _make_serializer()
		serializer._add_compound_components(simplified, node)

		assert simplified.is_compound_component
		assert len(node._compound_children) == 2
		assert node._compound_children[0]['role'] == 'textbox'
		assert node._compound_children[0]['name'] == 'Hex Value'
		assert node._compound_children[1]['role'] == 'button'
		assert node._compound_children[1]['name'] == 'Color Picker'


# ── TestInputFile ───────────────────────────────────────────────────────


class TestInputFile:
	def test_file_default(self):
		node = _make_node(tag='input', attributes={'type': 'file'})
		simplified = _make_simplified_node(original_node=node)
		serializer = _make_serializer()
		serializer._add_compound_components(simplified, node)

		assert simplified.is_compound_component
		assert len(node._compound_children) == 2
		assert node._compound_children[0]['role'] == 'button'
		assert node._compound_children[0]['name'] == 'Browse Files'
		assert node._compound_children[1]['role'] == 'textbox'
		assert node._compound_children[1]['name'] == 'File Selected'
		assert node._compound_children[1]['valuenow'] == 'None'

	def test_file_with_ax_valuetext(self):
		ax = _make_ax_node(properties=[_make_ax_property('valuetext', 'document.pdf')])
		node = _make_node(tag='input', attributes={'type': 'file'}, ax_node=ax)
		simplified = _make_simplified_node(original_node=node)
		serializer = _make_serializer()
		serializer._add_compound_components(simplified, node)

		assert node._compound_children[1]['valuenow'] == 'document.pdf'

	def test_file_multiple(self):
		node = _make_node(tag='input', attributes={'type': 'file', 'multiple': ''})
		simplified = _make_simplified_node(original_node=node)
		serializer = _make_serializer()
		serializer._add_compound_components(simplified, node)

		assert node._compound_children[1]['name'] == 'Files Selected'


# ── TestSelectCompound ──────────────────────────────────────────────────


def _make_select_with_options(options, *, ax_child_ids=None):
	"""Helper: create a select node with <option> children containing text nodes."""
	option_nodes = []
	for i, (val, label) in enumerate(options, start=101):
		option_nodes.append(
			_make_node(
				tag='option',
				node_id=i,
				backend_node_id=i,
				attributes={'value': val},
				children=[_make_text_node(label, node_id=i + 100, backend_node_id=i + 100)],
			)
		)
	ax = _make_ax_node(child_ids=ax_child_ids or ['ax-opt-1'])
	return _make_node(tag='select', node_id=50, backend_node_id=50, children=option_nodes, ax_node=ax)


class TestSelectCompound:
	def test_select_basic(self):
		ax = _make_ax_node(child_ids=['ax-opt-1'])
		node = _make_node(tag='select', ax_node=ax)
		simplified = _make_simplified_node(original_node=node)
		serializer = _make_serializer()
		serializer._add_compound_components(simplified, node)

		assert simplified.is_compound_component
		assert len(node._compound_children) == 2
		assert node._compound_children[0]['role'] == 'button'
		assert node._compound_children[0]['name'] == 'Dropdown Toggle'
		assert node._compound_children[1]['role'] == 'listbox'

	def test_select_with_options(self):
		node = _make_select_with_options([
			('1', 'Option A'),
			('2', 'Option B'),
		])
		simplified = _make_simplified_node(original_node=node)
		serializer = _make_serializer()
		serializer._add_compound_components(simplified, node)

		opt_component = node._compound_children[1]
		assert opt_component['role'] == 'listbox'
		assert opt_component['options_count'] == 2
		assert 'Option A' in opt_component['first_options']
		assert 'Option B' in opt_component['first_options']

	def test_select_more_than_4_options(self):
		options = [(str(i), f'Opt {i}') for i in range(1, 7)]
		node = _make_select_with_options(options)
		simplified = _make_simplified_node(original_node=node)
		serializer = _make_serializer()
		serializer._add_compound_components(simplified, node)

		opt_component = node._compound_children[1]
		assert opt_component['options_count'] == 6
		assert len(opt_component['first_options']) == 5  # 4 options + "... 2 more options..."
		assert '... 2 more options...' in opt_component['first_options'][-1]

	def test_select_format_hints_numeric(self):
		options = [(str(i), f'Item {i}') for i in range(10, 16)]
		node = _make_select_with_options(options)
		simplified = _make_simplified_node(original_node=node)
		serializer = _make_serializer()
		serializer._add_compound_components(simplified, node)

		opt_component = node._compound_children[1]
		assert opt_component['format_hint'] == 'numeric'


# ── TestDetailsCompound ─────────────────────────────────────────────────


class TestDetailsCompound:
	def test_details(self):
		ax = _make_ax_node(child_ids=['ax-det-1'])
		node = _make_node(tag='details', ax_node=ax)
		simplified = _make_simplified_node(original_node=node)
		serializer = _make_serializer()
		serializer._add_compound_components(simplified, node)

		assert simplified.is_compound_component
		assert len(node._compound_children) == 2
		assert node._compound_children[0]['role'] == 'button'
		assert node._compound_children[0]['name'] == 'Toggle Disclosure'
		assert node._compound_children[1]['role'] == 'region'
		assert node._compound_children[1]['name'] == 'Content Area'


# ── TestAudioVideoCompound ──────────────────────────────────────────────


class TestAudioVideoCompound:
	def test_audio(self):
		ax = _make_ax_node(child_ids=['ax-aud-1'])
		node = _make_node(tag='audio', ax_node=ax)
		simplified = _make_simplified_node(original_node=node)
		serializer = _make_serializer()
		serializer._add_compound_components(simplified, node)

		assert simplified.is_compound_component
		assert len(node._compound_children) == 4
		assert node._compound_children[0]['role'] == 'button'
		assert node._compound_children[0]['name'] == 'Play/Pause'
		assert node._compound_children[1]['role'] == 'slider'
		assert node._compound_children[1]['name'] == 'Progress'
		assert node._compound_children[2]['role'] == 'button'
		assert node._compound_children[2]['name'] == 'Mute'
		assert node._compound_children[3]['role'] == 'slider'
		assert node._compound_children[3]['name'] == 'Volume'

	def test_video(self):
		ax = _make_ax_node(child_ids=['ax-vid-1'])
		node = _make_node(tag='video', ax_node=ax)
		simplified = _make_simplified_node(original_node=node)
		serializer = _make_serializer()
		serializer._add_compound_components(simplified, node)

		assert simplified.is_compound_component
		assert len(node._compound_children) == 5
		assert node._compound_children[4]['role'] == 'button'
		assert node._compound_children[4]['name'] == 'Fullscreen'


# ── TestDateTimeNoCompound ──────────────────────────────────────────────


class TestDateTimeNoCompound:
	@pytest.mark.parametrize('input_type', ['date', 'time', 'datetime-local', 'month', 'week'])
	def test_date_inputs_no_compound(self, input_type):
		node = _make_node(tag='input', attributes={'type': input_type})
		simplified = _make_simplified_node(original_node=node)
		serializer = _make_serializer()
		serializer._add_compound_components(simplified, node)

		assert not simplified.is_compound_component
		assert len(node._compound_children) == 0


# ── TestNonCompoundTags ─────────────────────────────────────────────────


class TestNonCompoundTags:
	def test_plain_div_no_compound(self):
		node = _make_node(tag='div')
		simplified = _make_simplified_node(original_node=node)
		serializer = _make_serializer()
		serializer._add_compound_components(simplified, node)

		assert not simplified.is_compound_component
		assert len(node._compound_children) == 0
