"""issue #186 现象②：done(success=True) 不确定标记门禁的测试。

背景（docs/bug-fix/186-behavior-discipline-analysis.md §2）：task_64 step16
memory 明写 ``Emma Davis=1?`` + 60 行未读缺口，问号无新证据蒸发后仍
done(success=True) 收题（DB 真值该名字恰好是被漏计的那个，正确答案含两个
客户名）。门禁只把 agent 自己写下的疑虑当真：扫描 evaluation/memory/done
文本的未消解标记（词尾 ``?`` / 不确定关键词），命中则给一次「补验证或诚实
降级」的步内重试，每 run 封顶 2 次。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from tree_walker.agent.step import StepPipeline, scan_uncertainty_markers
from tree_walker.agent.views import AgentState
from tree_walker.tools.actions import Tools


# ── scan_uncertainty_markers（纯函数） ───────────────────────────────────


class TestScanUncertaintyMarkers:
    def test_token_trailing_question(self):
        # task_64 实形：悬而未决的计数值
        hits = scan_uncertainty_markers("Emma Davis=1?", "tally: Lisa Green=2?")
        assert "Davis=1?" in hits
        assert "Green=2?" in hits

    def test_bare_name_question_mark(self):
        hits = scan_uncertainty_markers("status unknown? proceeding")
        assert "unknown?" in hits

    def test_keywords_word_match(self):
        hits = scan_uncertainty_markers("a ~60-row gap remains unverified")
        assert "gap" in hits and "unverified" in hits
        assert scan_uncertainty_markers("the message was pending review") == ["pending"]

    def test_keyword_substring_not_matched(self):
        # 整词匹配：schema 里的 "data_gap_field" 不算 gap 命中
        assert scan_uncertainty_markers("field data_gap_field not present") == []

    def test_url_query_question_mark_not_a_hit(self):
        assert scan_uncertainty_markers("see http://x.com/a?b=1&c=2? for details") == []

    def test_clean_text_zero_hits(self):
        assert scan_uncertainty_markers(
            "All 308 rows read and counted; answer verified via filtered re-read.",
            "Lisa Green has 3 orders (confirmed).",
        ) == []

    def test_sentence_question_counts_as_uncertainty(self):
        # 整句疑问（"Is this right?"）按设计命中——自评里的疑问句本身就是未消解状态
        assert scan_uncertainty_markers("Saved. Is this right?") == ["right?"]

    def test_paren_quote_punct_wrapped_token_q(self):
        """review2 #3：排除式前瞻覆盖括号/引号/句读包裹的悬置值——尾前瞻
        (?=\\s|$) 会全部漏检。"""
        hits = scan_uncertainty_markers("(Emma Davis=1?)")
        assert "Davis=1?" in hits
        assert scan_uncertainty_markers("tally: 3?.") == ["3?"]
        assert scan_uncertainty_markers('"1?", noted') == ["1?"]

    def test_negated_keywords_not_uncertainty(self):
        """review2 #4：断言完整性的否定语境不误报——白耗门禁预算还可能把完整
        run 诱导成诚实失败收题。"""
        assert scan_uncertainty_markers("Nothing missing; all rows read.") == []
        assert scan_uncertainty_markers("No gap remains in the tally.") == []
        assert scan_uncertainty_markers("There are no unread rows left.") == []

    def test_negation_window_is_short(self):
        # 否定词距命中 >25 字符（隔了别的句子）→ 仍算疑虑
        assert scan_uncertainty_markers(
            "no issues earlier; however a data gap appears later in the file"
        ) == ["gap"]

    def test_second_unnegated_occurrence_counts(self):
        # 首个出现被否定、后续出现未否定（距否定词 >25 字符）→ 仍命中
        assert scan_uncertainty_markers(
            "no gap here. Many steps later we discover a new gap in the data"
        ) == ["gap"]

    def test_contraction_negation_suppresses(self):
        """review3 #1：n't 死分支修复——isn't/wasn't 缩写否定须抑制关键词，
        带前置 \\b 的 n't 在词内永不匹配。"""
        assert scan_uncertainty_markers("The tally isn't missing anything.") == []
        assert scan_uncertainty_markers("there wasn't a gap in the data.") == []

    def test_not_prefixed_keyword_immune_to_window(self):
        """review3 #2："not sure/not verified" 自带否定词，不被前一从句的
        否定词跨从句误杀——门禁零命中静默失效。"""
        assert scan_uncertainty_markers(
            "No gap found, but not sure the filter was correct"
        ) == ["not sure"]
        assert scan_uncertainty_markers(
            "no issues found, still not verified end to end"
        ) == ["not verified"]

    def test_dedupe_and_cap_three(self):
        text = "a=1? b=2? c=3? d=4? e=5? plus unknown and unread"
        hits = scan_uncertainty_markers(text)
        assert len(hits) == 3
        assert len(set(hits)) == 3

    def test_empty_and_none_safe(self):
        assert scan_uncertainty_markers("", None, "") == []  # type: ignore[arg-type]


# ── _gate_uncertain_success_done（管线门禁，鸭子类型桩） ──────────────────


def _make_pipeline(
    llm_side_effect: Any = None,
    *,
    gate_enabled: bool = True,
    done_gate_uses: int = 0,
    llm_timeout: float = 120,
) -> StepPipeline:
    """免构造管线：只挂门禁方法触碰的属性（沿 test_p7 _make_session 模式）。"""
    p = StepPipeline.__new__(StepPipeline)
    p.state = AgentState()
    p.state.done_gate_uses = done_gate_uses
    p._enable_done_gate = gate_enabled
    p.llm_timeout = llm_timeout
    p.tools = Tools()
    p.llm = AsyncMock()
    if llm_side_effect is not None:
        p.llm.get_action = AsyncMock(side_effect=llm_side_effect)
    p._system_prompt = "sys"
    p._tool_schema = {}
    return p


def _done_response(text: str = "answer", success: Any = True, **brain: str) -> dict[str, Any]:
    resp: dict[str, Any] = {
        "action": {"name": "done", "params": {"text": text, "success": success}},
        "evaluation_previous_goal": "",
        "memory": "",
    }
    resp.update(brain)
    return resp


def _other_response() -> dict[str, Any]:
    return {"action": {"name": "wait", "params": {"seconds": 1}}}


class TestGateUncertainSuccessDone:
    @pytest.mark.asyncio
    async def test_marker_in_memory_triggers_one_retry_with_feedback(self):
        retried = _done_response(text="verified answer")  # 重试响应干净
        pipe = _make_pipeline(llm_side_effect=[retried])
        dirty = _done_response(memory="Tally: Emma Davis=1? and a ~60-row gap")
        out = await pipe._gate_uncertain_success_done(dirty, [{"role": "user", "content": "m"}])
        assert pipe.llm.get_action.await_count == 1
        feedback = pipe.llm.get_action.call_args.kwargs["messages"][-1]["content"]
        assert "unresolved uncertainty" in feedback
        assert "Davis=1?" in feedback or "gap" in feedback  # 引用 agent 自己的标记
        assert out is retried
        assert pipe.state.done_gate_uses == 1

    @pytest.mark.asyncio
    async def test_marker_in_evaluation_also_scanned(self):
        retried = _done_response(text="ok")
        pipe = _make_pipeline(llm_side_effect=[retried])
        dirty = _done_response(evaluation_previous_goal="Partially — count unverified")
        out = await pipe._gate_uncertain_success_done(dirty, [])
        assert pipe.llm.get_action.await_count == 1
        assert out is retried

    @pytest.mark.asyncio
    async def test_retry_still_dirty_passes_through_single_shot(self):
        # 单次性：重试响应仍是带标记的 done(success=True) → 照样放行（agent 坚持）
        still_dirty = _done_response(text="same answer", memory="Emma Davis=1?")
        pipe = _make_pipeline(llm_side_effect=[still_dirty])
        dirty = _done_response(memory="Emma Davis=1?")
        out = await pipe._gate_uncertain_success_done(dirty, [])
        assert pipe.llm.get_action.await_count == 1  # 没有第二次重试
        assert out is still_dirty

    @pytest.mark.asyncio
    async def test_retry_invalid_params_falls_back_to_original(self):
        bad = {"action": {"name": "done", "params": {}}}  # 缺 text → 形状校验失败
        pipe = _make_pipeline(llm_side_effect=[bad])
        dirty = _done_response(memory="x is unknown")
        out = await pipe._gate_uncertain_success_done(dirty, [])
        assert out is dirty

    @pytest.mark.asyncio
    async def test_retry_invalid_action_falls_back_to_original(self):
        pipe = _make_pipeline(llm_side_effect=[{"no_action": True}])
        dirty = _done_response(memory="x is unknown")
        out = await pipe._gate_uncertain_success_done(dirty, [])
        assert out is dirty

    @pytest.mark.asyncio
    async def test_success_false_done_not_gated(self):
        pipe = _make_pipeline()
        resp = _done_response(text="partial", success=False, memory="Emma Davis=1?")
        out = await pipe._gate_uncertain_success_done(resp, [])
        assert out is resp
        pipe.llm.get_action.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_done_action_passthrough(self):
        pipe = _make_pipeline()
        resp = _other_response()
        out = await pipe._gate_uncertain_success_done(resp, [])
        assert out is resp
        pipe.llm.get_action.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_per_run_cap_reached_short_circuits(self):
        pipe = _make_pipeline(done_gate_uses=2)
        dirty = _done_response(memory="Emma Davis=1?")
        out = await pipe._gate_uncertain_success_done(dirty, [])
        assert out is dirty
        pipe.llm.get_action.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_gate_disabled_via_flag(self):
        pipe = _make_pipeline(gate_enabled=False)
        dirty = _done_response(memory="Emma Davis=1?")
        out = await pipe._gate_uncertain_success_done(dirty, [])
        assert out is dirty
        pipe.llm.get_action.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_retry_llm_failure_passes_through_original(self):
        """review 修正：软干预不得把已握有合法 done 响应的步变成失败步——重试
        调用抛 API/网络异常时放行原响应，异常不上抛；review4 #2：预算回滚，
        瞬时抖动不消耗每 run 仅 2 次的额度。"""
        pipe = _make_pipeline(llm_side_effect=RuntimeError("api down"))
        dirty = _done_response(memory="Emma Davis=1?")
        out = await pipe._gate_uncertain_success_done(dirty, [])
        assert out is dirty
        assert pipe.state.done_gate_uses == 0  # 失败放行回滚预算
        # 预算未耗 → 下一次 dirty done 仍会被拦
        retried = _done_response(text="verified")
        pipe2 = _make_pipeline(llm_side_effect=[retried])
        dirty2 = _done_response(memory="Lisa Green=2?")
        out2 = await pipe2._gate_uncertain_success_done(dirty2, [])
        assert pipe2.llm.get_action.await_count == 1

    @pytest.mark.asyncio
    async def test_retry_interrupted_error_propagates(self):
        """用户停止信号（InterruptedError）不得被软干预吞掉。"""
        pipe = _make_pipeline(llm_side_effect=InterruptedError())
        dirty = _done_response(memory="Emma Davis=1?")
        with pytest.raises(InterruptedError):
            await pipe._gate_uncertain_success_done(dirty, [])

    @pytest.mark.asyncio
    async def test_retry_timeout_passes_through_original(self):
        """review2 #1：门禁重试有自身小超时——内层 TimeoutError（Exception 形态）
        落在兜底里放行原响应，不上抛把合法 done 步变成失败步。"""
        import asyncio

        async def hang(**kwargs):
            await asyncio.sleep(999)

        pipe = _make_pipeline(llm_side_effect=hang, llm_timeout=0.05)
        dirty = _done_response(memory="Emma Davis=1?")
        out = await pipe._gate_uncertain_success_done(dirty, [])
        assert out is dirty

    @pytest.mark.asyncio
    async def test_retry_cancelled_error_propagates(self):
        """review4 #1：取消必须原样传播——3.12+ 外层 wait_for 基于 asyncio.timeout，
        吞掉取消后 __aexit__ 仍抛 TimeoutError（"放行"不成立）；外部强制
        task.cancel() 被吞后也不会在后续 await 自动再触发。"""
        import asyncio

        pipe = _make_pipeline(llm_side_effect=asyncio.CancelledError())
        dirty = _done_response(memory="Emma Davis=1?")
        with pytest.raises(asyncio.CancelledError):
            await pipe._gate_uncertain_success_done(dirty, [])

    @pytest.mark.asyncio
    async def test_done_text_token_question_not_gated(self):
        """review4 #3：text 是对外交付物——复述任务问句（"Q: … $50?"）的词尾 ?
        不是 agent 的疑虑，不得误伤完整正确的答案。"""
        pipe = _make_pipeline()
        resp = _done_response(
            text="Q: Which items cost more than $50? A: Widget ($60) — verified.",
            memory="All items read and compared.",
        )
        out = await pipe._gate_uncertain_success_done(resp, [])
        assert out is resp
        pipe.llm.get_action.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_done_text_keyword_still_gated(self):
        """review4 #3 另一面：text 中的不确定关键词（非问句复述）仍触发门禁。"""
        retried = _done_response(text="cleaned answer")
        pipe = _make_pipeline(llm_side_effect=[retried])
        dirty = _done_response(
            text="Answer: Veronica Costello (third value still unverified)",
            memory="Counts finalized.",
        )
        out = await pipe._gate_uncertain_success_done(dirty, [])
        assert pipe.llm.get_action.await_count == 1
        assert out is retried

    @pytest.mark.asyncio
    async def test_string_success_true_still_gated(self):
        """review2 #2：pydantic lax 放行 "success": "true" 但不回写原 dict——
        门禁须按执行侧语义判为成功并拦截。"""
        retried = _done_response(text="verified")
        pipe = _make_pipeline(llm_side_effect=[retried])
        dirty = _done_response(memory="Emma Davis=1?")
        dirty["action"]["params"]["success"] = "true"
        out = await pipe._gate_uncertain_success_done(dirty, [])
        assert pipe.llm.get_action.await_count == 1
        assert out is retried

    @pytest.mark.asyncio
    async def test_string_success_false_not_gated(self):
        pipe = _make_pipeline()
        resp = _done_response(text="partial")
        resp["action"]["params"]["success"] = "false"
        resp["memory"] = "Emma Davis=1?"
        out = await pipe._gate_uncertain_success_done(resp, [])
        assert out is resp
        pipe.llm.get_action.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_clean_success_done_untouched(self):
        pipe = _make_pipeline()
        clean = _done_response(
            memory="All 308 rows counted via group_count; verified by filtered re-read.",
        )
        out = await pipe._gate_uncertain_success_done(clean, [])
        assert out is clean
        pipe.llm.get_action.assert_not_awaited()


# ── 配置默认值 ──────────────────────────────────────────────────────────


class TestGateConfig:
    def test_default_on(self):
        from tree_walker.config import AgentSettings
        assert AgentSettings().done_uncertainty_gate is True

    def test_state_field_defaults_zero(self):
        assert AgentState().done_gate_uses == 0

    def test_env_parsing_accepts_numeric_and_string_idioms(self, monkeypatch):
        """review 修正：AGENT_DONE_GATE 兼容 =0/=1 数字习语与 false/no/off 字符串
        习语——只认 "true" 会让写 =1 的操作者静默关门禁。"""
        from tree_walker.config import load_settings

        # load_settings 会探测 localhost:9222（~4s/次）——mock 掉与本测无关的
        # ws_url 发现，7 个断言才不拖慢套件
        monkeypatch.setattr("tree_walker.config._fetch_ws_url", lambda *a, **k: "ws://test")

        def _flag(env_val: str) -> bool:
            monkeypatch.setenv("AGENT_DONE_GATE", env_val)
            return load_settings().agent.done_uncertainty_gate

        assert _flag("1") is True
        assert _flag("true") is True
        assert _flag("TRUE") is True
        assert _flag("0") is False
        assert _flag("false") is False
        assert _flag("no") is False
        assert _flag("off") is False
