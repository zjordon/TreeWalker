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
    _delimiter_scan,
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
        # issue #185-c2：失衡样本额外追加第 3 个「补全+插 catch」组合候选
        code = "(function(){try{var b=1;if(b){return 'x';}return 'not found';})()"
        cands = _syntax_repair_candidates(code, "Uncaught SyntaxError: Missing catch or finally after try")
        assert len(cands) == 3  # 形态① + 形态② + c2 组合候选
        rebuilt = cands[1]
        assert rebuilt.endswith("}catch(e){return 'Error: '+e.message}})()")

    def test_other_errors_return_empty(self):
        assert _syntax_repair_candidates("var x=;", "SyntaxError: Unexpected token ;") == []
        assert _syntax_repair_candidates("code", "") == []


# ── 建议5：evaluate 自愈（session 层） ─────────────────────────────────


class TestEvaluateSelfHeal:
    # 桩一律用真实 CDP 形状：编译期 SyntaxError 的 text 恒为 "Uncaught"、语义只在
    # exception.description（issue #185 教训——早期桩把语义写进 text，自愈匹配源
    # 跟着桩走，真机从未生效）。见 examples/debug_issue185_cdp_shape.py 实测。

    @pytest.mark.asyncio
    async def test_illegal_return_retried_with_iife_and_succeeds(self):
        bs = _make_session()
        bs.client.send.Runtime.evaluate = AsyncMock(side_effect=[
            {"exceptionDetails": {"text": "Uncaught", "exception": {
                "description": "SyntaxError: Illegal return statement"}}},
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
            {"exceptionDetails": {"text": "Uncaught", "exception": {
                "description": "SyntaxError: Illegal return statement"}}},
            {"exceptionDetails": {"text": "Uncaught", "exception": {
                "description": "SyntaxError: Illegal return statement"}}},
        ])
        with pytest.raises(RuntimeError, match="Illegal return statement"):
            await bs.evaluate("return 1")

    @pytest.mark.asyncio
    async def test_eof_error_gets_imbalance_hint(self):
        """issue #185-c2（review3 #1/#5/#6 修订）：失衡提示需扫描确证 + 真机可达
        路径——输入的补全候选在真机也必败（`var x=(1;)` 的 `(1;)` 仍是语法错），
        侧桩按调用次序给真实形状，断言的抛错路径与真机一致。"""
        bs = _make_session()
        bs.client.send.Runtime.evaluate = AsyncMock(side_effect=[
            {"exceptionDetails": {"text": "Uncaught", "exception": {
                "description": "SyntaxError: Unexpected end of input"}}},
            {"exceptionDetails": {"text": "Uncaught", "exception": {
                "description": "SyntaxError: Unexpected token ';'"}}},  # 补全候选仍败
        ])
        with pytest.raises(RuntimeError, match="unbalanced braces/parens"):
            await bs.evaluate("var x=(1;")

    @pytest.mark.asyncio
    async def test_token_error_without_imbalance_gets_no_imbalance_hint(self):
        """review3 #1：token 错误但扫描无失衡（如多余分号）——不得给失衡断言
        （事实性误导），原错误照常上抛。"""
        bs = _make_session()
        bs.client.send.Runtime.evaluate = AsyncMock(return_value={
            "exceptionDetails": {
                "text": "Uncaught",
                "exception": {"description": "SyntaxError: Unexpected token ';'"},
            },
        })
        with pytest.raises(RuntimeError) as ei:
            await bs.evaluate("var a=1;;")
        assert "unbalanced braces/parens" not in str(ei.value)

    @pytest.mark.asyncio
    async def test_token_error_with_imbalance_hint_real_machine_path(self):
        """review3 #6：删除候选在真机也必败的输入（`};var x=;` 的候选
        `;var x=;` 仍是语法错）——带失衡提示的抛错路径真机可达（对照旧用例
        `"}}"` 的空串候选在真机会"自愈成功"返回 undefined，断言路径不可达）。"""
        bs = _make_session()
        bs.client.send.Runtime.evaluate = AsyncMock(side_effect=[
            {"exceptionDetails": {"text": "Uncaught", "exception": {
                "description": "SyntaxError: Unexpected token '}'"}}},
            {"exceptionDetails": {"text": "Uncaught", "exception": {
                "description": "SyntaxError: Unexpected token ';'"}}},  # 删除候选仍败
        ])
        with pytest.raises(RuntimeError, match="unbalanced braces/parens"):
            await bs.evaluate("};var x=;")

    @pytest.mark.asyncio
    async def test_eof_missing_closer_candidate_heals(self):
        """c2 端到端：EOF 缺闭合（C 轮 549 形态 `((function(){…})()`）——首个候选
        补全闭合，重试成功。"""
        bs = _make_session()
        bs.client.send.Runtime.evaluate = AsyncMock(side_effect=[
            {"exceptionDetails": {"text": "Uncaught", "exception": {
                "description": "SyntaxError: Unexpected end of input"}}},
            {"result": {"value": "ok"}},
        ])
        out = await bs.evaluate("((function(){return 'ok'})()")
        assert out == "ok"
        retry_expr = bs.client.send.Runtime.evaluate.call_args_list[1][0][0]["expression"]
        assert retry_expr == "((function(){return 'ok'})())"

    @pytest.mark.asyncio
    async def test_extra_closer_candidate_heals(self):
        """c2 端到端：多余闭合（C 轮 699 形态）——删首个错位闭合符后重试成功。"""
        bs = _make_session()
        bs.client.send.Runtime.evaluate = AsyncMock(side_effect=[
            {"exceptionDetails": {"text": "Uncaught", "exception": {
                "description": "SyntaxError: Unexpected token '}'"}}},
            {"result": {"value": "not found"}},
        ])
        out = await bs.evaluate(
            "((function(){if(1){return 'x'}return 'not found'}})())")
        assert out == "not found"
        # review3 #4：单一错位的删除结果唯一确定——精确等值断言（负向子串拦不住
        # "删错位置"的回归）
        retry_expr = bs.client.send.Runtime.evaluate.call_args_list[1][0][0]["expression"]
        assert retry_expr == "((function(){if(1){return 'x'}return 'not found'})())"

    @pytest.mark.asyncio
    async def test_missing_catch_second_candidate_succeeds(self):
        bs = _make_session()
        bs.client.send.Runtime.evaluate = AsyncMock(side_effect=[
            {"exceptionDetails": {"text": "Uncaught", "exception": {
                "description": "SyntaxError: Missing catch or finally after try"}}},
            {"exceptionDetails": {"text": "Uncaught", "exception": {
                "description": "SyntaxError: Missing catch or finally after try"}}},  # 候选①仍失败
            {"result": {"value": "ok"}},  # 候选②成功
        ])
        out = await bs.evaluate("(function(){try{return 'n';})()")
        assert out == "ok"

    @pytest.mark.asyncio
    async def test_runtime_promise_rejection_still_matched_via_text(self):
        """运行期 promise 拒绝的语义在 text（非 description）——错误消息仍含语义。

        真实形态（task_64 step9）：text = "Uncaught (in promise) SyntaxError: ..."
        该形态无自愈候选（不得重跑），错误经 _format_eval_exception 原样上抛。
        """
        bs = _make_session()
        bs.client.send.Runtime.evaluate = AsyncMock(return_value={
            "exceptionDetails": {
                "text": "Uncaught (in promise) SyntaxError: Unexpected token '<', "
                        "\"<!doctype \"... is not valid JSON"},
        })
        with pytest.raises(RuntimeError, match="Unexpected token"):
            await bs.evaluate("fetch('/x').then(r=>r.json())")
        # 语义在 text 的运行期错误不触发自愈重试
        assert bs.client.send.Runtime.evaluate.await_count == 1

    @pytest.mark.asyncio
    async def test_runtime_inner_eval_syntax_error_does_not_self_heal(self):
        """review 修正：代码内部 eval/new Function 抛出的运行期 SyntaxError
        （description 为 "Uncaught ..." 前缀 + 全量堆栈）不得触发自愈——外层代码
        可能已有副作用（fetch/点击），重跑不安全。仅编译期形态（description 以
        "SyntaxError:" 开头）参与自愈匹配。
        """
        bs = _make_session()
        bs.client.send.Runtime.evaluate = AsyncMock(return_value={
            "exceptionDetails": {
                "text": "Uncaught",
                "exception": {
                    "description": "Uncaught SyntaxError: Illegal return statement\n"
                                   "    at eval (<anonymous>:1:1)\n"
                                   "    at <anonymous>:2:1",
                },
            },
        })
        with pytest.raises(RuntimeError, match="Illegal return statement"):
            await bs.evaluate("fetch('/x'); eval('return 1')")
        # 未发生自愈重试（只有首次调用），错误原样上抛
        assert bs.client.send.Runtime.evaluate.await_count == 1


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


# ── issue #185-c2：定界符平衡修复候选（纯函数边界） ─────────────────────


class TestBalanceRepairCandidates:
    """C 轮 22/22 编译失败全为定界符失衡；18 处主导形态原无候选——本轮解除
    后置（docs/bug-fix/185-c2-balance-repair-impl-plan.md）。"""

    def test_balanced_code_eof_branch_no_candidates(self):
        # 已平衡代码不得产生补全候选（防误改）
        assert _syntax_repair_candidates(
            "(function(){return 1})()", "SyntaxError: Unexpected end of input",
        ) == []

    def test_non_closer_token_error_no_candidates(self):
        # "Unexpected token ;" 这类真语法错（非多余闭合）——扫描找不到错位闭合
        assert _syntax_repair_candidates("var x=;", "SyntaxError: Unexpected token ;") == []

    def test_eof_completion_canonical_order(self):
        # 栈 (( → 补 ))（栈序逆置）；附最小补 ) 候选
        cands = _syntax_repair_candidates(
            "((function(){var a=1;", "SyntaxError: Unexpected end of input")
        assert cands[0] == "((function(){var a=1;}))"
        assert cands[1] == "((function(){var a=1;)"

    def test_extra_closer_two_candidates(self):
        # 双错位：候选①删首个错位（仍剩一个），候选②再删到平衡
        cands = _syntax_repair_candidates(
            "(function(){return 1}}})", "SyntaxError: Unexpected token '}'")
        assert cands == [
            "(function(){return 1}})",
            "(function(){return 1})",
        ]

    def test_single_extra_closer_one_candidate(self):
        # 单错位：删除后即平衡，不再产第二候选
        cands = _syntax_repair_candidates(
            "(function(){return 1}})", "SyntaxError: Unexpected token '}'")
        assert cands == ["(function(){return 1})"]

    def test_bare_return_plus_imbalance_composed_candidate(self):
        # C 轮 698/700 形态：裸 return 叠加失衡——组合候选（补全+包裹）在后
        cands = _syntax_repair_candidates(
            "return document.title;", "SyntaxError: Illegal return statement")
        assert cands[0] == "(()=>{\nreturn document.title;\n})()"  # 平衡主路径在前
        # 叠加失衡样本：return x + 缺闭合
        cands2 = _syntax_repair_candidates(
            "return ((function(){var a=1;", "SyntaxError: Illegal return statement")
        assert cands2[1].startswith("(()=>{\nreturn ((function(){var a=1;")
        assert cands2[1].endswith("}))\n})()")

    def test_missing_catch_plus_imbalance_composed_candidate(self):
        # C 轮 698 形态：缺 catch 叠加失衡
        cands = _syntax_repair_candidates(
            "(function(){try{var b=1;", "SyntaxError: Missing catch or finally after try")
        assert cands and cands[-1].endswith("catch(e){return 'Error: '+e.message}})")

    def test_regex_bracket_limitation_tolerated(self):
        """已知局限：regex 字面量内的 [] 被误计——候选允许无效，由编译试跑
        兜底（此处只断言不崩溃且产出候选）。"""
        cands = _syntax_repair_candidates(
            "(function(){return 'a'.match(/[((/)", "SyntaxError: Unexpected end of input")
        assert cands  # 误计产生的补全候选存在；合法性交 CDP 重试把关

    def test_line_comment_skips_to_eol_not_eof(self):
        """review3 #2：多行代码中行注释只跳到行尾——直接 break 会把注释后
        各行的可执行定界符整体跳过（EOF 分支因 stack 为空漏修）。"""
        from tree_walker.browser.session import _delimiter_scan
        code = "(function(){ // open modal\nvar a=1;\n})("
        stack, extra = _delimiter_scan(code)
        assert stack == ["("] and extra < 0

    def test_regex_escaped_slash_not_line_comment(self):
        """review5 #3：/https?:\/\//g 的被转义 / 与收尾 / 相邻不得误判为行
        注释——否则正则之后代码整体退出扫描，自愈候选与失衡提示三路全灭。"""
        from tree_walker.browser.session import _delimiter_scan
        bs92 = chr(92)
        balanced = (
            "(function(){var m='x'.match(/https?:" + bs92 + "/" + bs92
            + "//g);return m})()"
        )
        assert _delimiter_scan(balanced) == ([], -1)
        missing = (
            "((function(){var m='x'.match(/https?:" + bs92 + "/" + bs92
            + "//g);return m})()"
        )
        stack, extra = _delimiter_scan(missing)
        assert stack == ["("] and extra < 0

    def test_deletion_veto_on_position_mismatch(self):
        """review5 #1：regex 幻影闭合符（/[)]/g 的 )）使扫描错位报在 regex 内
        部——CDP 出错位置（探针实证精确指向真实错位 token）与扫描分叉时放弃
        删除类候选：删 regex 内字符会得到语法合法但语义已变的代码（空字符类
        no-op），绕过编译试跑把关。"""
        code = "(function(){var s='x'.replace(/[)]/g,'');return s}})()"
        phantom_stack, phantom_extra = _delimiter_scan(code)
        assert 0 <= phantom_extra != 50  # 扫描报幻影位，CDP 实测报 50（真错位）
        assert _syntax_repair_candidates(
            code, "SyntaxError: Unexpected token '}'", err_offset=50) == []
        # err_offset 缺失（多行/字段缺席）→ 保守跳过验证，候选照旧
        assert _syntax_repair_candidates(
            code, "SyntaxError: Unexpected token '}'") != []

    def test_deletion_candidates_with_matching_position(self):
        code = "(function(){return 1}})()"
        assert _delimiter_scan(code) == (["("], 21)  # 扫描错位=21，CDP 实测同为 21
        assert _syntax_repair_candidates(
            code, "SyntaxError: Unexpected token '}'", err_offset=21,
        ) == ["(function(){return 1})()"]  # 删下标 21 的第二个 }，调用括号保留


class TestDeletionPositionCrossValidation:
    """review5 #1：删除类候选以 CDP exceptionDetails 出错位置交叉验证——
    单行载荷的 lineNumber/columnNumber 即 0-based 字符偏移且精确指向错位
    token（examples/debug_c2_exc_position_shape.py 于 Chrome 153 实证）。"""

    @pytest.mark.asyncio
    async def test_veto_on_position_mismatch_no_retry(self):
        """regex 幻影场景：CDP 报 50（真错位），扫描报 ~31（/[)]/ 内的 )）
        ——分叉即放弃删除候选（无重试），错误照常上抛并附失衡提示。"""
        bs = _make_session()
        bs.client.send.Runtime.evaluate = AsyncMock(return_value={
            "exceptionDetails": {
                "text": "Uncaught",
                "exception": {"description": "SyntaxError: Unexpected token '}'"},
                "lineNumber": 0, "columnNumber": 50,
            },
        })
        with pytest.raises(RuntimeError, match="unbalanced braces/parens"):
            await bs.evaluate("(function(){var s='x'.replace(/[)]/g,'');return s}})()")
        assert bs.client.send.Runtime.evaluate.await_count == 1  # 无删除重试

    @pytest.mark.asyncio
    async def test_heal_with_matching_position(self):
        """位置一致（扫描错位=CDP 列偏移=21）——删除候选放行并自愈成功。"""
        bs = _make_session()
        bs.client.send.Runtime.evaluate = AsyncMock(side_effect=[
            {"exceptionDetails": {
                "text": "Uncaught",
                "exception": {"description": "SyntaxError: Unexpected token '}'"},
                "lineNumber": 0, "columnNumber": 21}},
            {"result": {"value": "1"}},
        ])
        out = await bs.evaluate("(function(){return 1}})()")
        assert out == "1"

    @pytest.mark.asyncio
    async def test_position_absent_still_tries_deletion(self):
        """exceptionDetails 无位置字段（旧桩/协议变体）——保守跳过验证，
        删除候选照旧生成（兼容既有行为）。"""
        bs = _make_session()
        bs.client.send.Runtime.evaluate = AsyncMock(side_effect=[
            {"exceptionDetails": {"text": "Uncaught", "exception": {
                "description": "SyntaxError: Unexpected token '}'"}}},
            {"result": {"value": "1"}},
        ])
        out = await bs.evaluate("(function(){return 1}})()")
        assert out == "1"
        assert bs.client.send.Runtime.evaluate.await_count == 2
