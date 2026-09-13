"""Tests for ActionResult success semantics — P1."""

import pytest

from tree_walker.agent.views import ActionResult


class TestActionResultSuccessSemantics:
    def test_default_success_is_none(self):
        r = ActionResult()
        assert r.success is None

    def test_success_true_requires_is_done(self):
        with pytest.raises(ValueError):
            ActionResult(success=True, is_done=False)

    def test_success_true_without_is_done(self):
        with pytest.raises(ValueError):
            ActionResult(success=True)

    def test_success_false_allowed_without_is_done(self):
        r = ActionResult(success=False)
        assert r.success is False

    def test_done_with_success_true(self):
        r = ActionResult(is_done=True, success=True)
        assert r.is_done is True
        assert r.success is True

    def test_done_with_success_false(self):
        r = ActionResult(is_done=True, success=False)
        assert r.success is False

    def test_error_without_success(self):
        r = ActionResult(error="something failed")
        assert r.success is None
        assert r.error == "something failed"

    def test_success_none_with_extracted_content(self):
        r = ActionResult(extracted_content="some data")
        assert r.success is None
        assert r.extracted_content == "some data"

    def test_str_shows_ok_when_no_special_fields(self):
        r = ActionResult()
        assert str(r) == "OK"

    def test_str_shows_done_when_is_done(self):
        r = ActionResult(is_done=True, success=True)
        assert "DONE" in str(r)

    def test_str_shows_error(self):
        r = ActionResult(error="boom")
        assert "ERROR: boom" in str(r)

    def test_str_shows_extracted(self):
        r = ActionResult(extracted_content="data")
        assert "EXTRACTED: data" in str(r)

    def test_str_marks_display_truncation_when_over_cap(self):
        """issue #185 现象③：extracted_content 超过 display_max_chars 时必须带
        显式截断标记——静默截断曾让 read_file 每块尾 1000 字符对 LLM 永不可见。"""
        cap = ActionResult.display_max_chars
        r = ActionResult(extracted_content="a" * (cap + 10))
        s = str(r)
        assert f"showing {cap} of {cap + 10} chars" in s
        assert "[...display truncated" in s

    def test_str_no_marker_when_within_cap(self):
        cap = ActionResult.display_max_chars
        r = ActionResult(extracted_content="a" * cap)  # 恰好等于上限：不截断
        assert "[...display truncated" not in str(r)
