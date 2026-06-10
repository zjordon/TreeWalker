"""Tests for decision attribution prompt."""
from tree_walker.observability.decision_prompt import get_decision_attribution_prompt


def test_returns_string():
    result = get_decision_attribution_prompt()
    assert isinstance(result, str)
    assert len(result) > 0


def test_contains_required_sections():
    result = get_decision_attribution_prompt()
    assert "目标" in result
    assert "候选" in result
    assert "选择" in result
    assert "原因" in result
    assert "预期" in result
