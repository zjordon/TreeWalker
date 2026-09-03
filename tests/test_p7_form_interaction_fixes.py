"""P7 form_interaction 修复的 session 层测试。

来源：docs/p7/form_interaction/01-failure-analysis.md
- 建议1（发现1）：settle 后 KO 数据网格"行渲染冻结"检测 + 丢弃式截图强制产帧。
- 建议5（发现6）：evaluate 已知 SyntaxError 的确定性自愈（Illegal return → 包 IIFE；
  Missing catch → 候选修复重试；截断 → 报错附提示）。
"""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from tree_walker.browser.session import (
    BrowserSession,
    _syntax_repair_candidates,
)


def _make_session() -> BrowserSession:
    """构造免连接的 BrowserSession（client 全 mock，session_id 固定）。"""
    bs = BrowserSession.__new__(BrowserSession)
    bs.client = MagicMock()
    bs.current_session_id = "sid1"
    bs.current_target_id = "t1"
    bs.client.send.Runtime.evaluate = AsyncMock(return_value={"result": {"value": ""}})
    bs.client.send.Page.captureScreenshot = AsyncMock(
        return_value={"data": base64.b64encode(b"x").decode()},
    )
    return bs


# ── 建议5：_syntax_repair_candidates（纯函数） ──────────────────────────


class TestSyntaxRepairCandidates:
    def test_illegal_return_wraps_iife(self):
        code = "return document.querySelector('select').value"
        cands = _syntax_repair_candidates(code, "Uncaught SyntaxError: Illegal return statement")
        assert cands == ["(()=>{\n" + code + "\n})()"]

    def test_missing_catch_balanced_shape_inserts_before_last_brace(self):
        # 形态①：括号均衡、仅缺 catch（505 旧样本形态）
        code = "(async()=>{try{var x=1;return x;}})()"
        cands = _syntax_repair_candidates(code, "Uncaught SyntaxError: Missing catch or finally after try")
        assert cands
        first = cands[0]
        # catch 插在最后一个 } 之前 → try{...}catch(e){...}})()
        assert first.endswith("catch(e){return 'Error: '+e.message}})()")

    def test_missing_catch_missing_brace_shape_rebuilds_suffix(self):
        # 形态②：连函数闭合括号也缺（504 s13 样本：...return 'not found';})()）
        code = "(function(){try{var b=1;if(b){return 'x';}return 'not found';})()"
        cands = _syntax_repair_candidates(code, "Uncaught SyntaxError: Missing catch or finally after try")
        assert len(cands) == 2
        rebuilt = cands[1]
        assert rebuilt.endswith("}catch(e){return 'Error: '+e.message}})()")

    def test_other_errors_return_empty(self):
        assert _syntax_repair_candidates("var x=;", "SyntaxError: Unexpected token ;") == []
        assert _syntax_repair_candidates("code", "") == []


# ── 建议5：evaluate 自愈（session 层） ─────────────────────────────────


class TestEvaluateSelfHeal:
    @pytest.mark.asyncio
    async def test_illegal_return_retried_with_iife_and_succeeds(self):
        bs = _make_session()
        bs.client.send.Runtime.evaluate = AsyncMock(side_effect=[
            {"exceptionDetails": {"text": "Uncaught SyntaxError: Illegal return statement"}},
            {"result": {"value": "1"}},
        ])
        out = await bs.evaluate("return document.title")
        assert out == "1"
        # 重试确实用了 IIFE 包裹的表达式
        retry_expr = bs.client.send.Runtime.evaluate.call_args_list[1][0][0]["expression"]
        assert retry_expr.startswith("(()=>{")

    @pytest.mark.asyncio
    async def test_retry_also_failing_raises_original(self):
        bs = _make_session()
        bs.client.send.Runtime.evaluate = AsyncMock(side_effect=[
            {"exceptionDetails": {"text": "Uncaught SyntaxError: Illegal return statement"}},
            {"exceptionDetails": {"text": "Uncaught SyntaxError: Illegal return statement"}},
        ])
        with pytest.raises(RuntimeError, match="Illegal return statement"):
            await bs.evaluate("return 1")

    @pytest.mark.asyncio
    async def test_truncation_error_gets_hint(self):
        bs = _make_session()
        bs.client.send.Runtime.evaluate = AsyncMock(return_value={
            "exceptionDetails": {"text": "Uncaught SyntaxError: Unexpected end of input"},
        })
        with pytest.raises(RuntimeError, match="truncated"):
            await bs.evaluate("var a = 1;")

    @pytest.mark.asyncio
    async def test_missing_catch_second_candidate_succeeds(self):
        bs = _make_session()
        bs.client.send.Runtime.evaluate = AsyncMock(side_effect=[
            {"exceptionDetails": {"text": "Uncaught SyntaxError: Missing catch or finally after try"}},
            {"exceptionDetails": {"text": "Uncaught SyntaxError: Missing catch or finally after try"}},  # 候选①仍失败
            {"result": {"value": "ok"}},  # 候选②成功
        ])
        out = await bs.evaluate("(function(){try{return 'n';})()")
        assert out == "ok"


# ── 建议1：数据网格行渲染冻结 kick（session 层） ────────────────────────


def _grid_state(grid: bool, empty: bool, rows: int = 6) -> str:
    import json as _json
    return _json.dumps({"grid": grid, "empty": empty, "rows": rows})


class TestKickFrozenDataGrid:
    @pytest.mark.asyncio
    async def test_frozen_grid_kicks_and_reports(self):
        bs = _make_session()
        bs.client.send.Runtime.evaluate = AsyncMock(side_effect=[
            {"result": {"value": _grid_state(True, True)}},   # 检测：行全空
            {"result": {"value": _grid_state(True, False)}},  # kick 后复查：有文本
        ])
        out = await bs._kick_frozen_data_grid()
        assert out == {"grid_kick": True, "grid_rows": 6, "grid_rendered": True}
        bs.client.send.Page.captureScreenshot.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_grid_with_text_no_kick(self):
        bs = _make_session()
        bs.client.send.Runtime.evaluate = AsyncMock(
            return_value={"result": {"value": _grid_state(True, False)}},
        )
        assert await bs._kick_frozen_data_grid() is None
        bs.client.send.Page.captureScreenshot.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_grid_no_kick(self):
        bs = _make_session()
        bs.client.send.Runtime.evaluate = AsyncMock(
            return_value={"result": {"value": _grid_state(False, False, 0)}},
        )
        assert await bs._kick_frozen_data_grid() is None
        bs.client.send.Page.captureScreenshot.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_evaluate_failure_never_raises(self):
        bs = _make_session()
        bs.client.send.Runtime.evaluate = AsyncMock(side_effect=RuntimeError("cdp down"))
        assert await bs._kick_frozen_data_grid() is None

    @pytest.mark.asyncio
    async def test_wait_for_page_settle_merges_kick_diagnostics(self, monkeypatch):
        bs = _make_session()
        monkeypatch.setattr(
            BrowserSession, "_settle_poll",
            AsyncMock(return_value={"ready": True, "stage": "no-requirejs", "n": 0, "waited": 0.1}),
        )
        monkeypatch.setattr(
            BrowserSession, "_kick_frozen_data_grid",
            AsyncMock(return_value={"grid_kick": True, "grid_rows": 7, "grid_rendered": True}),
        )
        out = await bs.wait_for_page_settle()
        assert out["ready"] is True
        assert out["grid_kick"] is True
        assert out["grid_rows"] == 7
