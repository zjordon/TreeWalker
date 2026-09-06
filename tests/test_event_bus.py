"""Tests for EventBus subscriber isolation / circuit-breaking / close semantics.

EventBus 是 issue #173 修复链的一环（PR #174 review2-4）：订阅者异常不得穿透
emit/close 杀死 agent 主流程，死订阅者熔断而非刷屏，close 汇总按订阅计数、
按名字去重。此前这些测试住在 test_step_malformed_action.py（review4 #9）。
"""

from __future__ import annotations

import logging

from tree_walker.observability.event_bus import EventBus
from tree_walker.observability.events import StepStartEvent


def _event() -> StepStartEvent:
    return StepStartEvent(step=1, session_id="s")


class TestSubscriberIsolation:
    def test_bad_subscriber_does_not_break_emit_or_others(self):
        bus = EventBus()
        received: list[str] = []

        def bad(event):
            raise RuntimeError("disk full")

        def good(event):
            received.append(type(event).__name__)

        bus.subscribe("*", bad)
        bus.subscribe("*", good)
        bus.emit(_event())  # 不抛
        assert received == ["StepStartEvent"]

    def test_typed_and_wildcard_both_reached(self):
        bus = EventBus()
        seen: list[str] = []

        bus.subscribe("step_start", lambda e: seen.append("typed"))
        bus.subscribe("*", lambda e: seen.append("wildcard"))
        bus.emit(_event())
        assert seen == ["typed", "wildcard"]


class TestCircuitBreaking:
    def test_disable_after_n_failures(self, caplog):
        # review3 #6：死订阅者连续失败达上限后熔断——不再被调用（不刷屏），
        # 其他订阅者不受影响
        bus = EventBus()
        calls = {"bad": 0, "good": 0}

        def bad(event):
            calls["bad"] += 1
            raise RuntimeError("disk full")

        def good(event):
            calls["good"] += 1

        bus.subscribe("*", bad)
        bus.subscribe("*", good)
        with caplog.at_level(logging.ERROR):
            for _ in range(10):
                bus.emit(_event())
        assert calls["bad"] == bus._MAX_SUBSCRIPTION_FAILURES  # 熔断后不再调用
        assert calls["good"] == 10
        assert any("DISABLED" in r.getMessage() for r in caplog.records)

    def test_same_handler_multi_subscribe_counts_independently(self):
        # review4 #6：同一 handler 订阅具名事件 + '*' 是两条独立投递路径——
        # 各自计数各自熔断（共享计数曾 2 个事件即触发 disable-after-3）
        bus = EventBus()
        calls = []

        def h(event):
            calls.append(1)
            raise RuntimeError("disk full")

        bus.subscribe("step_start", h)
        bus.subscribe("*", h)
        for _ in range(bus._MAX_SUBSCRIPTION_FAILURES):
            bus.emit(_event())  # 每事件 2 次投递，各自失败 1 次
        assert len(calls) == 2 * bus._MAX_SUBSCRIPTION_FAILURES
        bus.emit(_event())  # 两条路径均已熔断
        assert len(calls) == 2 * bus._MAX_SUBSCRIPTION_FAILURES

    def test_handler_recovery_resets_failure_count(self):
        # 失败后恢复 → 连续计数清零（不因历史偶发失败被熔断）
        bus = EventBus()
        state = {"fail": True}
        calls = []

        def flaky(event):
            calls.append(1)
            if state["fail"]:
                raise RuntimeError("transient")

        bus.subscribe("*", flaky)
        for _ in range(2):
            bus.emit(_event())  # 2 次失败（未达上限）
        state["fail"] = False
        for _ in range(5):
            bus.emit(_event())  # 恢复
        state["fail"] = True
        for _ in range(bus._MAX_SUBSCRIPTION_FAILURES + 2):
            bus.emit(_event())  # 重新连续失败才熔断
        assert len(calls) == 2 + 5 + bus._MAX_SUBSCRIPTION_FAILURES


class TestCloseIsolation:
    def test_close_callback_failure_does_not_propagate(self):
        # review3 #2：close 回调（JsonlRecorder flush/close）失败不得穿出
        # run() finally 的 _finalize_session 替换掉 return self.history
        bus = EventBus()

        def bad_close():
            raise OSError("disk full during flush")

        closed = []
        bus.on_close(bad_close)
        bus.on_close(lambda: closed.append(1))
        bus.close()  # 不抛
        assert closed == [1]  # 后续回调照常执行

    def test_close_summary_lists_disabled_subscriptions(self, caplog):
        # review4 #9：汇总分支此前零覆盖。disabled 路径：名字去重后列出
        bus = EventBus()

        def dead(event):
            raise RuntimeError("permanent")

        def dead2(event):
            raise RuntimeError("permanent-2")

        bus.subscribe("*", dead)
        bus.subscribe("step_start", dead)  # 同名第二路订阅
        bus.subscribe("step_start", dead2)
        with caplog.at_level(logging.ERROR):
            for _ in range(bus._MAX_SUBSCRIPTION_FAILURES):
                bus.emit(_event())
        with caplog.at_level(logging.WARNING):
            bus.close()
        summary = [r.getMessage() for r in caplog.records if "event bus close" in r.getMessage()]
        assert summary, "close 汇总 warning 应存在"
        # 3 路订阅全部熔断；名字去重后按序各出现一次
        assert "3 subscription(s) disabled" in summary[0]
        import re

        names = [n.strip() for n in re.search(r"disabled \[(.*?)\]", summary[0]).group(1).split(",")]
        assert len(names) == 2  # 3 路订阅熔断、同名第二路去重为一
        assert any(n.endswith(".dead") for n in names)
        assert any(n.endswith(".dead2") for n in names)

    def test_close_summary_reports_failing_only(self, caplog):
        # 仅失败计数非零（未熔断）也汇总——偶发失败显形为 incomplete 提示
        bus = EventBus()
        state = {"fail": True}

        def flaky(event):
            if state["fail"]:
                state["fail"] = False  # 只失败一次
                raise RuntimeError("transient")

        bus.subscribe("*", flaky)
        with caplog.at_level(logging.ERROR):
            bus.emit(_event())
        with caplog.at_level(logging.WARNING):
            bus.close()
        summary = [r.getMessage() for r in caplog.records if "event bus close" in r.getMessage()]
        assert summary
        assert "0 subscription(s) disabled" in summary[0]
        assert "1 subscription(s) " in summary[0]  # 单位统一为订阅路径

    def test_close_summary_does_not_double_count_disabled(self, caplog):
        # review5 #9：熔断订阅的 failures 停在上限且从不重置——汇总的 failing
        # 集合排除已 disabled 的订阅，同一 handler 不被报成两个问题
        bus = EventBus()

        def dead(event):
            raise RuntimeError("permanent")

        bus.subscribe("*", dead)
        with caplog.at_level(logging.ERROR):
            for _ in range(bus._MAX_SUBSCRIPTION_FAILURES):
                bus.emit(_event())
        with caplog.at_level(logging.WARNING):
            bus.close()
        summary = [r.getMessage() for r in caplog.records if "event bus close" in r.getMessage()]
        assert summary
        assert "1 subscription(s) disabled" in summary[0]
        assert "0 subscription(s) \nwith recent failures" not in summary[0]
        assert ", 0 subscription(s)" in summary[0]  # disabled 的不再进 failing 计数

    def test_close_summary_silent_when_healthy(self, caplog):
        bus = EventBus()
        bus.subscribe("*", lambda e: None)
        with caplog.at_level(logging.WARNING):
            bus.close()
        assert not [r for r in caplog.records if "event bus close" in r.getMessage()]
