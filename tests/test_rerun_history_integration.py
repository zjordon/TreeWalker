"""历史重放——Agent 级集成测试（mock browser/tools）+ 变量检测分支覆盖。

补充 test_rerun_history.py：覆盖 rerun_history 主循环的各分支（跳过/extract 重算/
terminates/drift/匹配失败/菜单重打开/SPA 等待）、load_and_rerun、save_history、
detect_variables、摘要 Layer 2，以及 variable_detector 的属性/值模式分支。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tree_walker.agent.views import (
    AgentHistory,
    AgentHistoryList,
    ActionResult,
    StepMetadata,
)
from tree_walker.browser.views import DOMInteractedElement


# ── LLM 替身 ────────────────────────────────────────────────────────────


class _StructuredLLM:
    async def extract(self, *, prompt, content, output_schema=None, **kw):
        assert output_schema is not None
        return '{"summary":"ok","success":true,"completion_status":"complete"}'


class _TextOnlyLLM:
    """Layer 1（结构化）抛错 → 降级 Layer 2（文本）。"""

    async def extract(self, *, prompt, content, output_schema=None, **kw):
        if output_schema is not None:
            raise ValueError("structured output unsupported")
        return "plain text summary"


# ── 构造 mock Agent ─────────────────────────────────────────────────────


def _live_state(selector_map: dict, url: str = "http://example.com") -> SimpleNamespace:
    return SimpleNamespace(url=url, dom_state=SimpleNamespace(selector_map=selector_map))


def _make_agent(get_state, get_url="http://example.com", execute_results=None, llm=None):
    from tree_walker.agent.agent import Agent
    from tree_walker.config import AgentSettings, JudgeSettings

    browser = MagicMock()
    browser._settings = SimpleNamespace(wait_between_actions=0)
    browser.start = AsyncMock()
    browser.stop = AsyncMock()
    browser.navigate = AsyncMock()
    browser.get_state = AsyncMock(return_value=get_state)
    browser.get_current_url = AsyncMock(return_value=get_url)

    agent = Agent(
        task="do x", llm=MagicMock(), browser=browser,
        settings=AgentSettings(judge=JudgeSettings(enabled=False)),
    )

    calls: list[tuple] = []
    results = execute_results or {}

    async def fake_execute(name, params, br, st):
        calls.append((name, dict(params)))
        return results.get(name, ActionResult())

    agent.tools.execute = fake_execute
    agent.llm = llm or _StructuredLLM()
    return agent, browser, calls


def _elem(node) -> dict:
    return DOMInteractedElement.load_from_enhanced_dom_tree(node).to_dict()


# ── rerun_history 各分支 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skip_no_action_step(make_node):
    state = _live_state({7: make_node(tag="button", attributes={"id": "go"})})
    history = AgentHistoryList(history=[
        AgentHistory(step_number=0, model_output={"action": {"name": "", "params": {}}}, result=[]),
        AgentHistory(step_number=1,
                     model_output={"action": {"name": "click", "params": {"index": 3}}},
                     result=[], interacted_element=[_elem(make_node(tag="button", attributes={"id": "go"}))]),
    ])
    agent, _, calls = _make_agent(state)
    await agent.rerun_history(history, max_step_interval=0, delay_between_actions=0,
                              summary_llm=_StructuredLLM())
    assert [c[0] for c in calls] == ["click"]      # 第一步（无动作）被跳过


@pytest.mark.asyncio
async def test_skip_failures_skips_errored_step(make_node):
    live = make_node(tag="button", attributes={"id": "go"})
    state = _live_state({7: live})
    history = AgentHistoryList(history=[
        AgentHistory(step_number=0,
                     model_output={"action": {"name": "click", "params": {"index": 3}}},
                     result=[ActionResult(error="boom")],
                     interacted_element=[_elem(live)]),
    ])
    agent, _, calls = _make_agent(state)
    await agent.rerun_history(history, skip_failures=True, max_step_interval=0,
                              delay_between_actions=0, summary_llm=_StructuredLLM())
    assert calls == []


@pytest.mark.asyncio
async def test_redundant_retry_is_skipped(make_node):
    live = make_node(tag="button", attributes={"id": "go"})
    state = _live_state({7: live})
    elem = _elem(live)
    history = AgentHistoryList(history=[
        AgentHistory(step_number=0,
                     model_output={"action": {"name": "click", "params": {"index": 3}}},
                     result=[], interacted_element=[elem]),
        AgentHistory(step_number=1,
                     model_output={"action": {"name": "click", "params": {"index": 3}}},
                     result=[], interacted_element=[elem]),
    ])
    agent, _, calls = _make_agent(state)
    await agent.rerun_history(history, max_step_interval=0, delay_between_actions=0,
                              summary_llm=_StructuredLLM())
    assert len(calls) == 1      # 第二步（冗余重试）被跳过


@pytest.mark.asyncio
async def test_extract_is_reexecuted(make_node):
    state = _live_state({})
    history = AgentHistoryList(history=[
        AgentHistory(step_number=0,
                     model_output={"action": {"name": "extract", "params": {"query": "find ref"}}},
                     result=[], interacted_element=[None]),
    ])
    agent, _, calls = _make_agent(
        state, execute_results={"extract": ActionResult(extracted_content="REF123")}
    )
    results = await agent.rerun_history(history, max_step_interval=0,
                                        delay_between_actions=0, summary_llm=_StructuredLLM())
    assert calls[0] == ("extract", {"query": "find ref"})
    assert any(r.extracted_content == "REF123" for r in results)


@pytest.mark.asyncio
async def test_terminates_sequence_breaks(make_node):
    live = make_node(tag="button", attributes={"id": "go"})
    state = _live_state({7: live})
    history = AgentHistoryList(history=[
        AgentHistory(step_number=0,
                     model_output={"actions": [
                         {"name": "navigate", "params": {"url": "http://example.com"}},
                         {"name": "click", "params": {"index": 7}},
                     ]},
                     result=[], interacted_element=[None, _elem(live)]),
    ])
    agent, _, calls = _make_agent(state)
    await agent.rerun_history(history, max_step_interval=0, delay_between_actions=0,
                              summary_llm=_StructuredLLM())
    assert [c[0] for c in calls] == ["navigate"]   # navigate 终止序列，click 未执行


@pytest.mark.asyncio
async def test_drift_breaks_remaining(make_node):
    state = _live_state({7: make_node(tag="button", attributes={"id": "a"}),
                         8: make_node(tag="button", attributes={"id": "b"})},
                        url="http://before.com")
    history = AgentHistoryList(history=[
        AgentHistory(step_number=0,
                     model_output={"actions": [
                         {"name": "click", "params": {"index": 7}},
                         {"name": "click", "params": {"index": 8}},
                     ]},
                     result=[], interacted_element=[
                         _elem(make_node(tag="button", attributes={"id": "a"})),
                         _elem(make_node(tag="button", attributes={"id": "b"})),
                     ]),
    ])
    agent, _, calls = _make_agent(state, get_url="http://after.com")
    await agent.rerun_history(history, max_step_interval=0, delay_between_actions=0,
                              summary_llm=_StructuredLLM())
    assert [c[0] for c in calls] == ["click"]      # 漂移后第二个不执行


@pytest.mark.asyncio
async def test_match_failure_returns_error_after_retries(make_node):
    state = _live_state({7: make_node(tag="input", attributes={"name": "other"})})
    history = AgentHistoryList(history=[
        AgentHistory(step_number=0,
                     model_output={"action": {"name": "click", "params": {"index": 3}}},
                     result=[], interacted_element=[{"node_name": "INPUT", "attributes": {"name": "email"}}]),
    ])
    agent, _, _ = _make_agent(state)
    results = await agent.rerun_history(history, max_retries=0, max_step_interval=0,
                                        delay_between_actions=0, summary_llm=_StructuredLLM())
    assert any(r.error and "failed after" in (r.error or "") for r in results)


@pytest.mark.asyncio
async def test_menu_reopen_attempted(make_node):
    opener_live = make_node(tag="button", attributes={"aria-haspopup": "menu", "id": "m"})
    state = _live_state({5: opener_live})      # 当前页只有 opener，没有菜单项
    elem_opener = _elem(opener_live)
    elem_item = {"node_name": "A", "attributes": {"id": "item"}}   # 当前页没有 → 匹配失败
    history = AgentHistoryList(history=[
        AgentHistory(step_number=0,
                     model_output={"action": {"name": "click", "params": {"index": 5}}},
                     result=[], interacted_element=[elem_opener],
                     state_summary={"url": "http://example.com"}),
        AgentHistory(step_number=1,
                     model_output={"action": {"name": "click", "params": {"index": 2}}},
                     result=[], interacted_element=[elem_item]),
    ])
    agent, _, calls = _make_agent(state)
    results = await agent.rerun_history(history, max_retries=0, max_step_interval=0,
                                        delay_between_actions=0, summary_llm=_StructuredLLM())
    assert any(c[0] == "click" and c[1]["index"] == 5 for c in calls)   # opener 执行 + 重打开
    assert any(r.error for r in results)                                 # 菜单项最终失败


def test_is_option_element():
    from tree_walker.agent.rerun import RerunMixin
    rm = RerunMixin()

    def item(attrs):
        return AgentHistory(step_number=0, model_output={"action": {}}, result=[],
                            interacted_element=[{"node_name": "DIV", "attributes": attrs}])

    assert rm._is_option_element(item({"role": "option"})) is True
    assert rm._is_option_element(item({"class": "semi-select-option collection-option"})) is True
    assert rm._is_option_element(item({"class": "btn"})) is False
    assert rm._is_option_element(
        AgentHistory(step_number=0, model_output={"action": {}}, result=[])) is False
    assert rm._is_option_element(None) is False


def test_is_menu_opener_step_broadened():
    from tree_walker.agent.rerun import RerunMixin
    rm = RerunMixin()

    def item(attrs):
        return AgentHistory(step_number=0, model_output={"action": {}}, result=[],
                            interacted_element=[{"node_name": "DIV", "attributes": attrs}])

    # 旧启发式也认
    assert rm._is_menu_opener_step(item({"aria-haspopup": "true"})) is True
    # 拓宽后新认的（Semi / Ant / Element 等框架触发器 + ARIA）
    assert rm._is_menu_opener_step(item({"class": "semi-select-selection"})) is True
    assert rm._is_menu_opener_step(item({"class": "ant-select-selector"})) is True
    assert rm._is_menu_opener_step(item({"role": "combobox"})) is True
    assert rm._is_menu_opener_step(item({"aria-expanded": "false"})) is True
    # 非触发器
    assert rm._is_menu_opener_step(item({"class": "ordinary-div"})) is False
    assert rm._is_menu_opener_step(None) is False


@pytest.mark.asyncio
async def test_menu_reopen_on_option_not_found(make_node):
    # 上一步是 Semi Design 触发器（semi-select-selection，旧启发式不认），
    # 当前步要找 role=option 的下拉选项；下拉先关后开 → 菜单重打开应触发并救回。
    # 给 opener/option 各自一个 parent，使 x_path 不同（避免裸 div 误命中 XPATH 级）。
    opener_node = make_node(tag="div", attributes={"class": "semi-select-selection"},
                            parent=make_node(tag="form"))
    option_node = make_node(
        tag="div",
        attributes={"class": "semi-select-option collection-option", "role": "option"},
        parent=make_node(tag="body"),
    )
    history = AgentHistoryList(history=[
        AgentHistory(step_number=0,
                     model_output={"action": {"name": "click", "params": {"index": 5}}},
                     result=[], interacted_element=[_elem(opener_node)]),
        AgentHistory(step_number=1,
                     model_output={"action": {"name": "click", "params": {"index": 6}}},
                     result=[], interacted_element=[_elem(option_node)]),
    ])
    state_no_option = _live_state({100: opener_node})                       # 下拉已关：只有触发器
    state_with_option = _live_state({100: opener_node, 200: option_node})   # 下拉打开：选项出现
    # get_state 调用序：step0 → step1(失败) → 重开 step0 → step1 重试(成功)
    agent, browser, calls = _make_agent(state_no_option)
    browser.get_state = AsyncMock(side_effect=[
        state_no_option, state_no_option, state_no_option, state_with_option,
    ])
    results = await agent.rerun_history(history, max_step_interval=0, delay_between_actions=0,
                                        summary_llm=_StructuredLLM())
    # option 最终被点击
    assert any(c[0] == "click" and c[1].get("index") == 200 for c in calls)
    # opener 被点 2 次（主循环 step0 + 重开 step0）
    assert sum(1 for c in calls if c[0] == "click" and c[1].get("index") == 100) == 2
    # step1 没有报错（重开救回了）——末项是摘要，排除
    assert not any(r.error for r in results[:-1])


@pytest.mark.asyncio
async def test_wait_for_elements_polls(make_node):
    live = make_node(tag="input", attributes={"name": "email"})
    full = _live_state({0: live})
    empty = _live_state({})
    agent, browser, calls = _make_agent(full)
    browser.get_state = AsyncMock(side_effect=[empty, full, full])   # 第一次不足 → 轮询 → 足够
    history = AgentHistoryList(history=[
        AgentHistory(step_number=0,
                     model_output={"action": {"name": "input_text", "params": {"index": 0, "text": "x"}}},
                     result=[], interacted_element=[_elem(make_node(tag="input", attributes={"name": "email"}))]),
    ])
    await agent.rerun_history(history, wait_for_elements=True, max_step_interval=0,
                              delay_between_actions=0, summary_llm=_StructuredLLM())
    assert calls and calls[0][1]["index"] == 0


@pytest.mark.asyncio
async def test_replay_continues_past_mid_done(make_node):
    # done 可能出现在录制中途（agent 先 done、再因错误重试一步）。
    # 重放须回放每一步，不被中途的 done 截断后续步骤（回归：外层循环不再因 is_done break）。
    state = _live_state({})
    history = AgentHistoryList(history=[
        AgentHistory(step_number=0,
                     model_output={"action": {"name": "done", "params": {"text": "first done"}}},
                     result=[], interacted_element=[None]),
        AgentHistory(step_number=1,
                     model_output={"action": {"name": "done", "params": {"text": "final done"}}},
                     result=[], interacted_element=[None]),
    ])
    agent, _, calls = _make_agent(
        state, execute_results={"done": ActionResult(is_done=True)}
    )
    results = await agent.rerun_history(history, max_step_interval=0,
                                        delay_between_actions=0, summary_llm=_StructuredLLM())
    assert [c[0] for c in calls] == ["done", "done"]   # 两步都回放，未被首个 done 截断
    assert results[-1].is_done                          # 最后是摘要


@pytest.mark.asyncio
async def test_rerun_empty_history_yields_only_summary():
    agent, _, _ = _make_agent(_live_state({}))
    results = await agent.rerun_history(AgentHistoryList(), max_step_interval=0,
                                        summary_llm=_StructuredLLM())
    assert len(results) == 1 and results[0].is_done


# ── load_and_rerun / save_history / detect_variables ────────────────────


@pytest.mark.asyncio
async def test_load_and_rerun(tmp_path, make_node):
    live = make_node(tag="input", attributes={"name": "email"})
    state = _live_state({7: live})
    history = AgentHistoryList(history=[
        AgentHistory(step_number=0,
                     model_output={"action": {"name": "input_text", "params": {"index": 2, "text": "a@b.com"}}},
                     result=[], state_summary={"url": "http://example.com"},
                     interacted_element=[_elem(make_node(tag="input", attributes={"name": "email"}))]),
    ])
    # 历史写到 tmp_path/h.json（底层 save_to_file 接受绝对路径）；agent 的根目录重定向到 tmp_path，
    # load_and_rerun 用相对路径（新校验：只许相对）解析到同一文件
    history.save_to_file(tmp_path / "h.json")

    agent, _, calls = _make_agent(state)
    agent.rerun_history_dir = str(tmp_path)
    results = await agent.load_and_rerun("h.json", max_step_interval=0,
                                         delay_between_actions=0, summary_llm=_StructuredLLM())
    assert calls[0][0] == "input_text"
    assert calls[0][1]["index"] == 7               # 重定位
    assert results[-1].is_done


@pytest.mark.asyncio
async def test_agent_save_history_writes_registry_version(tmp_path):
    import json

    agent, _, _ = _make_agent(_live_state({}))
    agent.rerun_history_dir = str(tmp_path)   # 根目录重定向到 tmp_path；save 用相对路径
    agent.history.history.append(
        AgentHistory(step_number=0, model_output={"action": {"name": "click", "params": {}}},
                     result=[ActionResult()],
                     metadata=StepMetadata(step_start_time=0.0, step_end_time=1.0, step_number=0))
    )
    agent.save_history("out.json")
    data = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert data["action_registry_version"]
    assert len(data["history"]) == 1


def test_agent_detect_variables(make_node):
    agent, _, _ = _make_agent(_live_state({}))
    agent.history.history.append(
        AgentHistory(step_number=0,
                     model_output={"action": {"name": "input_text", "params": {"text": "a@b.com"}}},
                     result=[], interacted_element=[{"attributes": {"type": "email"}}])
    )
    assert "email" in agent.detect_variables()


# ── 摘要 Layer 2 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_summary_layer2_text_fallback():
    from tree_walker.agent.rerun import RerunMixin
    rm = RerunMixin()
    summary = await rm._generate_rerun_summary("do x", [ActionResult()], summary_llm=_TextOnlyLLM())
    assert summary.is_done
    assert summary.extracted_content == "plain text summary"
    assert summary.success is True


# ── 变量检测：属性分支 ─────────────────────────────────────────────────


def _detect_one(attrs=None, text="x"):
    from tree_walker.agent.variable_detector import detect_variables_in_history
    h = AgentHistoryList(history=[
        AgentHistory(step_number=0,
                     model_output={"action": {"name": "input_text", "params": {"text": text}}},
                     result=[],
                     interacted_element=[{"node_name": "INPUT", "attributes": attrs or {}}])
    ])
    return detect_variables_in_history(h)


@pytest.mark.parametrize("attrs,expected", [
    ({"type": "tel"}, "phone"),
    ({"type": "url"}, "url"),
    ({"type": "number"}, "number"),
    ({"name": "first_name"}, "first_name"),
    ({"name": "lastname"}, "last_name"),
    ({"name": "fullname"}, "full_name"),
    ({"name": "dob"}, "date"),
    ({"name": "street"}, "address"),
    ({"name": "billing_address"}, "billing_address"),
    ({"name": "shipping_address"}, "shipping_address"),
    ({"id": "city"}, "city"),
    ({"id": "state"}, "state"),
    ({"id": "country"}, "country"),
    ({"id": "zip"}, "zip_code"),
    ({"id": "company"}, "company"),
    ({"id": "message"}, "comment"),
    ({"name": "username"}, "name"),
])
def test_detect_attribute_branches(attrs, expected):
    assert expected in _detect_one(attrs)


# ── 变量检测：值模式分支 + 去重 ────────────────────────────────────────


@pytest.mark.parametrize("text,expected", [
    ("a@b.com", "email"),
    ("+1 (555) 123-4567", "phone"),
    ("2024-01-15", "date"),
    ("42", "number"),
    ("John", "first_name"),
    ("John Doe", "full_name"),
    ("John Jacob Jingleheimer", "name"),
])
def test_detect_value_pattern_branches(text, expected):
    assert expected in _detect_one(attrs={}, text=text)


def test_detect_unique_name_suffix():
    from tree_walker.agent.variable_detector import detect_variables_in_history
    h = AgentHistoryList(history=[
        AgentHistory(step_number=0,
                     model_output={"actions": [
                         {"name": "input_text", "params": {"text": "a@b.com"}},
                         {"name": "input_text", "params": {"text": "c@d.com"}},
                     ]},
                     result=[],
                     interacted_element=[{"attributes": {"type": "email"}},
                                         {"attributes": {"type": "email"}}])
    ])
    dv = detect_variables_in_history(h)
    assert "email" in dv and "email_2" in dv


# ── 重放文件根目录 + 路径校验（Agent 级）──────────────────────────────


def test_save_history_writes_under_root(tmp_path):
	# rerun_history_dir 重定向到临时目录；相对路径落到其下（含自动建子目录）
	agent, _, _ = _make_agent(_live_state({}))
	agent.rerun_history_dir = str(tmp_path)
	agent.save_history("sub/history.json")
	assert (tmp_path / "sub" / "history.json").is_file()


def test_save_history_rejects_absolute(tmp_path):
	agent, _, _ = _make_agent(_live_state({}))
	agent.rerun_history_dir = str(tmp_path)
	with pytest.raises(ValueError):
		agent.save_history("/abs/history.json")


def test_save_history_rejects_traversal(tmp_path):
	agent, _, _ = _make_agent(_live_state({}))
	agent.rerun_history_dir = str(tmp_path)
	with pytest.raises(ValueError):
		agent.save_history("../escape.json")


@pytest.mark.asyncio
async def test_load_and_rerun_rejects_absolute(tmp_path):
	agent, _, _ = _make_agent(_live_state({}))
	agent.rerun_history_dir = str(tmp_path)
	with pytest.raises(ValueError):
		await agent.load_and_rerun("/abs/history.json")
