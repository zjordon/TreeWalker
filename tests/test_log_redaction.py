"""Tests for ``tree_walker.agent.step._redact_params_for_log`` (P1-2).

The helper mirrors ``_redact_history_data`` (views.py) but is non-mutating and
scoped to a single action's params, so terminal logs don't leak real secret
values that ``_restore_sensitive_in_output`` already put back into ``action_params``.
"""

from __future__ import annotations

from tree_walker.agent.step import _redact_params_for_log
from tree_walker.agent.views import _SENSITIVE_ACTION_FIELDS


# ── StepPipeline._sensitive_map_for_log property ───────────────────────


class _StubAgent:
	"""最小 stub：暴露 ``_sensitive_map``，供 property fget 直接调用。"""

	def __init__(self, sensitive_map):
		self._sensitive_map = sensitive_map


class TestSensitiveMapForLog:
	"""``_sensitive_map_for_log`` 把 Agent 的 ``{real: placeholder}`` 反转为
	``{placeholder: real}``（``redact_sensitive_string`` 要求的方向）。"""

	def test_inverts_map_orientation(self):
		from tree_walker.agent.step import StepPipeline

		stub = _StubAgent({"real_val": "pw"})
		assert StepPipeline._sensitive_map_for_log.fget(stub) == {"pw": "real_val"}

	def test_none_map_returns_none(self):
		from tree_walker.agent.step import StepPipeline

		assert StepPipeline._sensitive_map_for_log.fget(_StubAgent(None)) is None

	def test_empty_map_returns_none(self):
		from tree_walker.agent.step import StepPipeline

		assert StepPipeline._sensitive_map_for_log.fget(_StubAgent({})) is None

	def test_missing_attr_returns_none(self):
		"""Agent 未设置 ``_sensitive_map``（无 sensitive 配置）→ None。"""

		from tree_walker.agent.step import StepPipeline

		class _Bare:
			pass

		assert StepPipeline._sensitive_map_for_log.fget(_Bare()) is None

	def test_multiple_entries_all_inverted(self):
		from tree_walker.agent.step import StepPipeline

		stub = _StubAgent({"r1": "p1", "r2": "p2"})
		assert StepPipeline._sensitive_map_for_log.fget(stub) == {"p1": "r1", "p2": "r2"}


# ── sensitive fields per action ────────────────────────────────────────


class TestSensitiveFieldRedaction:
	"""已知敏感字段（input_text.text / search.query / extract.query）被脱敏。"""

	def test_input_text_text_redacted(self):
		out = _redact_params_for_log(
			"input_text", {"index": 1, "text": "secret123"}, {"pw": "secret123"},
		)
		assert out["text"] == "<secret>pw</secret>"
		assert out["index"] == 1  # 非敏感字段不变

	def test_search_query_redacted(self):
		out = _redact_params_for_log(
			"search", {"query": "my-secret"}, {"q": "my-secret"},
		)
		assert out["query"] == "<secret>q</secret>"

	def test_extract_query_redacted(self):
		out = _redact_params_for_log(
			"extract", {"query": "my-secret"}, {"q": "my-secret"},
		)
		assert out["query"] == "<secret>q</secret>"

	def test_only_matching_secret_redacted(self):
		"""field 值不等于任何 secret 时原样返回（无误伤）。"""
		out = _redact_params_for_log(
			"input_text", {"text": "harmless"}, {"pw": "secret123"},
		)
		assert out["text"] == "harmless"


# ── non-mutating ───────────────────────────────────────────────────────


class TestNonMutating:
	"""脱敏返回副本，原始 params 不被改动（动作仍拿真值执行）。"""

	def test_original_params_unchanged(self):
		params = {"index": 1, "text": "secret123"}
		_ = _redact_params_for_log("input_text", params, {"pw": "secret123"})
		assert params["text"] == "secret123"
		assert params["index"] == 1

	def test_returns_distinct_object(self):
		params = {"index": 1}
		out = _redact_params_for_log("click", params, None)
		assert out is not params
		assert out == params


# ── no-op paths ─────────────────────────────────────────────────────────


class TestNoOpPaths:
	"""无 sensitive 配置 / 非敏感动作 / 空字段集 → 原样返回副本。"""

	def test_none_sensitive_map(self):
		out = _redact_params_for_log("input_text", {"text": "x"}, None)
		assert out == {"text": "x"}

	def test_empty_sensitive_map(self):
		out = _redact_params_for_log("input_text", {"text": "x"}, {})
		assert out == {"text": "x"}

	def test_action_not_in_sensitive_fields(self):
		"""click/scroll/navigate 等不在 _SENSITIVE_ACTION_FIELDS → 原样。"""
		out = _redact_params_for_log(
			"click", {"index": 5, "x": "anything"}, {"pw": "anything"},
		)
		assert out == {"index": 5, "x": "anything"}

	def test_field_value_not_str(self):
		"""敏感字段值非 str（如 int）→ 不脱敏、不崩。"""
		out = _redact_params_for_log("input_text", {"text": 12345}, {"pw": "12345"})
		assert out["text"] == 12345


# ── edge cases ─────────────────────────────────────────────────────────


class TestEdgeCases:
	def test_non_dict_params_returns_empty(self):
		assert _redact_params_for_log("click", None, {"pw": "x"}) == {}
		assert _redact_params_for_log("click", "not-a-dict", {"pw": "x"}) == {}

	def test_empty_params(self):
		assert _redact_params_for_log("input_text", {}, {"pw": "x"}) == {}

	def test_multiple_secrets_longest_first(self):
		"""短秘密是长秘密前缀时，按长度降序匹配避免部分泄露（redact_sensitive_string 行为）。"""
		# "pass" 是 "pass123" 的前缀；若先匹配 "pass" 会把 "pass123" 拆成 "<secret>...</secret>123"
		params = {"text": "pass123"}
		out = _redact_params_for_log(
			"input_text", params, {"short": "pass", "long": "pass123"},
		)
		# 长秘密优先 → 整体替换为 <secret>long</secret>，不留 "123" 尾巴
		assert out["text"] == "<secret>long</secret>"

	def test_sensitive_action_fields_registry(self):
		"""_SENSITIVE_ACTION_FIELDS 覆盖三个已知敏感动作（防回归：动作名拼写漂移）。"""
		assert set(_SENSITIVE_ACTION_FIELDS.keys()) == {"input_text", "search", "extract"}
		assert _SENSITIVE_ACTION_FIELDS["input_text"] == ("text",)
		assert _SENSITIVE_ACTION_FIELDS["search"] == ("query",)
		assert _SENSITIVE_ACTION_FIELDS["extract"] == ("query",)
