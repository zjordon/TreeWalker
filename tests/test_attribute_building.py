"""Tests for the 8-step attribute string building pipeline in _build_attributes_string."""

from tree_walker.browser.views import DEFAULT_INCLUDE_ATTRIBUTES
from dom_snapshot.serializer import _build_attributes_string
from tests.conftest import _make_node, _make_ax_node, _make_ax_property


# ── Step 1: HTML attribute whitelist filtering ──────────────────────────


class TestStep1WhitelistFiltering:
	"""Only attributes present in include_attributes are kept."""

	def test_only_whitelisted(self):
		node = _make_node(
			tag='div',
			attributes={'title': 'Hello', 'data-custom': 'ignored'},
		)
		result = _build_attributes_string(node, DEFAULT_INCLUDE_ATTRIBUTES)
		assert 'title=Hello' in result
		assert 'data-custom' not in result

	def test_empty_attributes(self):
		node = _make_node(tag='div', attributes={})
		result = _build_attributes_string(node, DEFAULT_INCLUDE_ATTRIBUTES)
		assert result == ''


# ── Step 2a: Date/time input format hints ───────────────────────────────


class TestStep2DateTimeFormats:
	"""HTML5 date/time inputs get format and placeholder hints."""

	def test_date_input(self):
		node = _make_node(
			tag='input',
			attributes={'type': 'date'},
		)
		result = _build_attributes_string(node, DEFAULT_INCLUDE_ATTRIBUTES)
		assert 'format=YYYY-MM-DD' in result
		assert 'placeholder=YYYY-MM-DD' in result

	def test_time_input(self):
		node = _make_node(
			tag='input',
			attributes={'type': 'time'},
		)
		result = _build_attributes_string(node, DEFAULT_INCLUDE_ATTRIBUTES)
		assert 'format=HH:MM' in result

	def test_datetime_local(self):
		node = _make_node(
			tag='input',
			attributes={'type': 'datetime-local'},
		)
		result = _build_attributes_string(node, DEFAULT_INCLUDE_ATTRIBUTES)
		assert 'format=YYYY-MM-DDTHH:MM' in result

	def test_month(self):
		node = _make_node(
			tag='input',
			attributes={'type': 'month'},
		)
		result = _build_attributes_string(node, DEFAULT_INCLUDE_ATTRIBUTES)
		assert 'format=YYYY-MM' in result

	def test_week(self):
		node = _make_node(
			tag='input',
			attributes={'type': 'week'},
		)
		result = _build_attributes_string(node, DEFAULT_INCLUDE_ATTRIBUTES)
		assert 'format=YYYY-W##' in result


# ── Step 2b: Tel input placeholder ──────────────────────────────────────


class TestStep2bTelInput:
	"""Tel input without pattern gets a default placeholder."""

	def test_tel_placeholder(self):
		node = _make_node(
			tag='input',
			attributes={'type': 'tel'},
		)
		result = _build_attributes_string(node, DEFAULT_INCLUDE_ATTRIBUTES)
		assert 'placeholder=123-456-7890' in result


# ── Step 2c: Datepicker detection ────────────────────────────────────────


class TestStep2cDatepickerDetection:
	"""Text inputs with datepicker libraries get format hints."""

	def test_uib_datepicker_popup(self):
		node = _make_node(
			tag='input',
			attributes={'type': 'text', 'uib-datepicker-popup': 'dd/MM/yyyy'},
		)
		result = _build_attributes_string(node, DEFAULT_INCLUDE_ATTRIBUTES)
		assert 'expected_format=dd/MM/yyyy' in result

	def test_jquery_datepicker_class(self):
		node = _make_node(
			tag='input',
			attributes={
				'type': 'text',
				'class': 'datepicker',
				'data-date-format': 'mm/dd/yy',
			},
		)
		result = _build_attributes_string(node, DEFAULT_INCLUDE_ATTRIBUTES)
		assert 'format=mm/dd/yy' in result

	def test_jquery_datepicker_default(self):
		node = _make_node(
			tag='input',
			attributes={
				'type': 'text',
				'class': 'datepicker',
			},
		)
		result = _build_attributes_string(node, DEFAULT_INCLUDE_ATTRIBUTES)
		assert 'format=mm/dd/yyyy' in result


# ── Step 3: Password field protection ───────────────────────────────────


class TestStep3PasswordProtection:
	"""Password field values and valuetext are stripped."""

	def test_password_strips_value(self):
		ax = _make_ax_node(
			properties=[_make_ax_property('value', 'secret')],
		)
		node = _make_node(
			tag='input',
			attributes={'type': 'password', 'value': 'secret'},
			ax_node=ax,
		)
		result = _build_attributes_string(node, DEFAULT_INCLUDE_ATTRIBUTES)
		assert 'secret' not in result

	def test_password_strips_ax_valuetext(self):
		ax = _make_ax_node(
			properties=[_make_ax_property('valuetext', 'secret')],
		)
		node = _make_node(
			tag='input',
			attributes={'type': 'password'},
			ax_node=ax,
		)
		result = _build_attributes_string(node, DEFAULT_INCLUDE_ATTRIBUTES)
		assert 'secret' not in result


# ── Step 4: AX property merge ───────────────────────────────────────────


class TestStep4AXPropertyMerge:
	"""AX tree properties are merged into the attribute dict."""

	def test_ax_property_added(self):
		ax = _make_ax_node(
			properties=[_make_ax_property('expanded', 'true')],
		)
		node = _make_node(tag='div', ax_node=ax)
		result = _build_attributes_string(node, DEFAULT_INCLUDE_ATTRIBUTES)
		assert 'expanded=true' in result

	def test_ax_boolean_formatting(self):
		ax = _make_ax_node(
			properties=[_make_ax_property('expanded', True)],
		)
		node = _make_node(tag='div', ax_node=ax)
		result = _build_attributes_string(node, DEFAULT_INCLUDE_ATTRIBUTES)
		assert 'expanded=true' in result
		assert 'expanded=True' not in result


# ── Step 5: Form current value ──────────────────────────────────────────


class TestStep5FormValue:
	"""AX valuetext takes priority over AX value for form elements."""

	def test_ax_valuetext_overrides(self):
		ax = _make_ax_node(
			properties=[
				_make_ax_property('valuetext', 'new'),
				_make_ax_property('value', 'old'),
			],
		)
		node = _make_node(
			tag='input',
			attributes={'type': 'text', 'value': 'old'},
			ax_node=ax,
		)
		result = _build_attributes_string(node, DEFAULT_INCLUDE_ATTRIBUTES)
		assert 'value=new' in result

	def test_ax_value_as_fallback(self):
		ax = _make_ax_node(
			properties=[_make_ax_property('value', 'current')],
		)
		node = _make_node(
			tag='input',
			attributes={'type': 'text'},
			ax_node=ax,
		)
		result = _build_attributes_string(node, DEFAULT_INCLUDE_ATTRIBUTES)
		assert 'value=current' in result


# ── Step 6: Value dedup ────────────────────────────────────────────────


class TestStep6ValueDedup:
	"""Duplicate long values (>5 chars) are removed for non-protected attrs."""

	def test_duplicate_removed(self):
		"""When two non-protected attrs share a long value, the later one is removed."""
		long_val = 'duplicate long value text here'
		ax = _make_ax_node(
			properties=[
				_make_ax_property('name', long_val),
				_make_ax_property('role', long_val),
			],
		)
		node = _make_node(tag='div', ax_node=ax)
		result = _build_attributes_string(node, DEFAULT_INCLUDE_ATTRIBUTES)
		# Both 'name' and 'role' are non-protected; the second duplicate is removed
		assert 'name=' in result
		assert 'role=' not in result

	def test_duplicate_non_protected_removed(self):
		"""Non-protected attr removed when it duplicates a protected attr's long value."""
		long_val = 'this is a very long value indeed'
		ax = _make_ax_node(
			properties=[
				_make_ax_property('title', long_val),
				_make_ax_property('role', long_val),
			],
		)
		node = _make_node(tag='div', ax_node=ax)
		result = _build_attributes_string(node, DEFAULT_INCLUDE_ATTRIBUTES)
		assert 'title=' in result
		# 'role' is not in the protected set and shares the same long value
		assert 'role=' not in result


# ── Step 7: Redundancy removal ──────────────────────────────────────────


class TestStep7RedundancyRemoval:
	"""Redundant attribute values are removed from output."""

	def test_role_equals_tagname(self):
		"""role matching tag name (compared via node_name == ax_role) is removed."""
		# node_name is tag.upper(), ax role is 'BUTTON' -> match -> role removed
		ax = _make_ax_node(role='DIV')
		node = _make_node(
			tag='div',
			attributes={'role': 'div'},
			ax_node=ax,
		)
		result = _build_attributes_string(node, DEFAULT_INCLUDE_ATTRIBUTES)
		assert 'role=' not in result

	def test_type_equals_tagname(self):
		node = _make_node(
			tag='select',
			attributes={'type': 'select'},
		)
		result = _build_attributes_string(node, DEFAULT_INCLUDE_ATTRIBUTES)
		assert 'type=' not in result

	def test_invalid_false_removed(self):
		ax = _make_ax_node(
			properties=[_make_ax_property('invalid', 'false')],
		)
		node = _make_node(tag='input', ax_node=ax)
		result = _build_attributes_string(node, DEFAULT_INCLUDE_ATTRIBUTES)
		assert 'invalid' not in result

	def test_required_false_removed(self):
		ax = _make_ax_node(
			properties=[_make_ax_property('required', 'false')],
		)
		node = _make_node(tag='input', ax_node=ax)
		result = _build_attributes_string(node, DEFAULT_INCLUDE_ATTRIBUTES)
		assert 'required' not in result


# ── Step 8: Formatting ─────────────────────────────────────────────────


class TestStep8Formatting:
	"""Output formatting: truncation and empty value display."""

	def test_value_truncation(self):
		long_value = 'x' * 150
		node = _make_node(
			tag='input',
			attributes={'type': 'text', 'value': long_value},
		)
		result = _build_attributes_string(node, DEFAULT_INCLUDE_ATTRIBUTES)
		# Value should be truncated to 100 chars
		assert f'value={"x" * 100}' in result
		assert len(result.split('value=')[1].split(' ')[0]) == 100

	def test_empty_value(self):
		node = _make_node(
			tag='input',
			attributes={'type': 'text', 'value': '  '},
		)
		# 'value' stripped to empty -> removed in step 1 (val.strip() is falsy)
		# Use a different approach: set via AX property with empty after strip
		ax = _make_ax_node(
			properties=[_make_ax_property('title', '')],
		)
		node2 = _make_node(
			tag='div',
			attributes={'title': ''},
			ax_node=ax,
		)
		result = _build_attributes_string(node2, DEFAULT_INCLUDE_ATTRIBUTES)
		# Empty string after strip -> not included (val is falsy in step 1)
		assert result == ''
