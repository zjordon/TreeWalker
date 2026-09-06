"""Tests for task-level skill loading (docs/p7/03-task-skill-loading-design.md v2).

Five groups:
  1. TaskSkillLoader — catalog scan / bad-card skip / cache / card_text order
  2. match_task_skill — null 一等答案 / low 降档 / 未知 slug / 调用失败重试
  3. build_state_message — [Task Skill] 渲染与位置（[Task] 后、[Domain Skill] 前）
  4. Agent 接线 — _match_task_skill / 默认关 / env 开关
  5. LLMClient.structured_call — tool 强制路径 / text 兜底
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tree_walker.browser.views import BrowserStateSummary, SerializedDOMState
from tree_walker.prompts.system_prompt import build_state_message
from tree_walker.skills.loader import SkillLoader
from tree_walker.skills.task_loader import TaskCardMeta, TaskSkillLoader
from tree_walker.skills.task_matcher import (
    TaskSkillMatch,
    build_task_skill_text,
    match_task_skill,
)


# ── helpers ─────────────────────────────────────────────────────────────


def _write_card(
    root: Path,
    host_key: str,
    slug: str,
    *,
    description: str | None = "do the thing",
    keywords: tuple[str, ...] = (),
    distilled_at: str = "",
    sop: str | None = "sop body",
    selectors: str | None = None,
    quirks: str | None = None,
    meta_text: str | None = None,
) -> None:
    """Write one task card under ``root/<host_key>/tasks/<slug>/``."""
    d = Path(root) / host_key / "tasks" / slug
    d.mkdir(parents=True, exist_ok=True)
    if meta_text is not None:
        (d / "_task.json").write_text(meta_text, encoding="utf-8")
    else:
        (d / "_task.json").write_text(
            json.dumps(
                {
                    "slug": slug,
                    "task_description": description,
                    "task_keywords": list(keywords),
                    "distilled_at": distilled_at,
                    "source_traces": [],
                }
            ),
            encoding="utf-8",
        )
    for name, content in (("_sop.md", sop), ("selectors.md", selectors), ("quirks.md", quirks)):
        if content is not None:
            (d / name).write_text(content, encoding="utf-8")


def _loader(tmp_path: Path) -> TaskSkillLoader:
    return TaskSkillLoader(SkillLoader(tmp_path))


def _state(url: str = "https://example.com") -> BrowserStateSummary:
    return BrowserStateSummary(
        url=url,
        title="Ex",
        dom_state=SerializedDOMState(_root=None, selector_map={}, element_tree_text="dom"),
    )


class FakeMatchLLM:
    """structured_call 替身：可编程返回 dict / 抛异常 / 记录调用次数。"""

    def __init__(self, results: list) -> None:
        self._results = list(results)
        self.calls: list[dict] = []

    async def structured_call(self, **kwargs) -> dict | None:
        self.calls.append(kwargs)
        item = self._results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _catalog(*slugs: str) -> list[TaskCardMeta]:
    return [
        TaskCardMeta(slug=s, description=f"desc of {s}", keywords=("kw",), distilled_at="2026-09-01")
        for s in slugs
    ]


# ── 1. TaskSkillLoader ──────────────────────────────────────────────────


class TestTaskSkillCatalog:
    def test_finds_and_parses_cards(self, tmp_path):
        _write_card(tmp_path, "localhost_7780", "slug-a", keywords=("数量", "count"), distilled_at="2026-08-31T12:00:00+00:00")
        _write_card(tmp_path, "localhost_7780", "slug-b")
        catalog = _loader(tmp_path).catalog("localhost_7780")
        assert [c.slug for c in catalog] == ["slug-a", "slug-b"]
        assert catalog[0].description == "do the thing"
        assert catalog[0].keywords == ("数量", "count")
        assert catalog[0].distilled_at == "2026-08-31T12:00:00+00:00"

    def test_missing_tasks_dir_returns_empty(self, tmp_path):
        (tmp_path / "localhost_7780").mkdir(parents=True)  # host 目录在、无 tasks/
        assert _loader(tmp_path).catalog("localhost_7780") == []

    def test_missing_host_dir_returns_empty(self, tmp_path):
        assert _loader(tmp_path).catalog("localhost_7780") == []

    def test_none_host_returns_empty(self, tmp_path):
        assert _loader(tmp_path).catalog(None) == []

    def test_bad_json_skipped_others_intact(self, tmp_path):
        _write_card(tmp_path, "h", "good")
        _write_card(tmp_path, "h", "bad", meta_text="{not json")
        catalog = _loader(tmp_path).catalog("h")
        assert [c.slug for c in catalog] == ["good"]

    def test_card_without_description_skipped(self, tmp_path):
        # 无描述 = 无检索锚点（如模板模式产物）——跳过
        _write_card(tmp_path, "h", "nodesc", meta_text=json.dumps({"slug": "nodesc"}))
        _write_card(tmp_path, "h", "ok")
        assert [c.slug for c in _loader(tmp_path).catalog("h")] == ["ok"]

    def test_slug_falls_back_to_dir_name(self, tmp_path):
        _write_card(tmp_path, "h", "dir-name", meta_text=json.dumps({"task_description": "d"}))
        catalog = _loader(tmp_path).catalog("h")
        assert catalog[0].slug == "dir-name"

    def test_keywords_string_tolerated_as_single_keyword(self, tmp_path):
        # 契约是 str[]，但手改/异端导出可能给字符串——按单个关键词容错，
        # 不逐字符迭代产出垃圾单字
        meta_text = json.dumps({"task_description": "d", "task_keywords": "count, quantity"})
        _write_card(tmp_path, "h", "s", meta_text=meta_text)
        catalog = _loader(tmp_path).catalog("h")
        assert catalog[0].keywords == ("count, quantity",)
        assert "count, quantity" in catalog[0].catalog_line()

    def test_zero_cards_with_dir_logs_warning(self, tmp_path, caplog):
        # 目录存在却零卡（全坏迁移）= 严重静默失效，按 docs/p7/03 §2.1 用 warning 级
        import logging

        _write_card(tmp_path, "h", "bad", meta_text="{broken")
        with caplog.at_level(logging.WARNING):
            _loader(tmp_path).catalog("h")
        assert any(
            "0 cards" in r.getMessage() and r.levelno == logging.WARNING
            for r in caplog.records
        )

    def test_zero_cards_without_dir_stays_info(self, tmp_path, caplog):
        # 无 tasks/ 目录 = 正常态（大多数 host 无任务卡），不告警
        import logging

        with caplog.at_level(logging.WARNING):
            _loader(tmp_path).catalog("nowhere")
        assert caplog.records == []

    def test_catalog_is_cached_per_host(self, tmp_path):
        _write_card(tmp_path, "h", "slug-a")
        loader = _loader(tmp_path)
        first = loader.catalog("h")
        # 盘上新增卡不影响已缓存 host（进程内缓存，docs/p7/03 §五）
        _write_card(tmp_path, "h", "slug-b")
        assert loader.catalog("h") is first

    def test_catalog_line_renders_keywords(self):
        card = TaskCardMeta(slug="s", description="count things", keywords=("数量", "count"))
        line = card.catalog_line()
        assert line == "- `s` — count things | keywords: 数量, count"

    def test_catalog_line_without_keywords(self):
        card = TaskCardMeta(slug="s", description="count things")
        assert card.catalog_line() == "- `s` — count things"

    def test_newest_distilled_at(self):
        catalog = _catalog("a", "b")  # distilled_at = 2026-09-01
        newer = TaskCardMeta(slug="c", description="d", distilled_at="2026-09-05T00:00:00+00:00")
        assert TaskSkillLoader.newest_distilled_at(catalog + [newer]) == "2026-09-05T00:00:00+00:00"
        assert TaskSkillLoader.newest_distilled_at([]) == ""


class TestTaskCardText:
    def test_fixed_read_order(self, tmp_path):
        _write_card(
            tmp_path, "h", "s",
            sop="# SOP\nstep1", selectors="SEL", quirks="QUIRK",
        )
        loader = _loader(tmp_path)
        card = loader.catalog("h")[0]
        text = loader.card_text(card)
        assert text.index("SOP") < text.index("SEL") < text.index("QUIRK")

    def test_empty_and_missing_files_skipped(self, tmp_path):
        _write_card(tmp_path, "h", "s", sop="", selectors=None, quirks="only quirks")
        loader = _loader(tmp_path)
        text = loader.card_text(loader.catalog("h")[0])
        assert text == "only quirks"

    def test_all_missing_returns_empty(self, tmp_path):
        _write_card(tmp_path, "h", "s", sop=None, meta_text=json.dumps({"task_description": "d"}))
        loader = _loader(tmp_path)
        assert loader.card_text(loader.catalog("h")[0]) == ""


# ── 2. match_task_skill ─────────────────────────────────────────────────


class TestMatchTaskSkill:
    @pytest.mark.asyncio
    async def test_match_returns_slug(self):
        llm = FakeMatchLLM([{"match": "slug-a", "confidence": "high", "reason": "same op"}])
        m = await match_task_skill("count pending", _catalog("slug-a", "slug-b"), llm)
        assert m.slug == "slug-a"
        assert m.downgraded is False

    @pytest.mark.asyncio
    async def test_null_match_is_first_class(self):
        llm = FakeMatchLLM([{"match": None, "confidence": "high", "reason": "variant"}])
        m = await match_task_skill("count approved", _catalog("slug-a"), llm)
        assert m.slug is None
        assert m.downgraded is False

    @pytest.mark.asyncio
    async def test_empty_and_literal_null_strings_treated_as_null(self):
        for raw in ("", "null", "none", "NULL"):
            llm = FakeMatchLLM([{"match": raw, "confidence": "medium", "reason": "r"}])
            m = await match_task_skill("t", _catalog("slug-a"), llm)
            assert m.slug is None, f"raw={raw!r}"

    @pytest.mark.asyncio
    async def test_low_confidence_downgraded_to_null(self):
        llm = FakeMatchLLM([{"match": "slug-a", "confidence": "low", "reason": "unsure"}])
        m = await match_task_skill("t", _catalog("slug-a"), llm)
        assert m.slug is None
        assert m.downgraded is True

    @pytest.mark.asyncio
    async def test_non_enum_confidence_downgraded_to_null(self):
        # schema 外值（数字；structured_call 的 text 兜底路径不做 schema 校验）
        # 不得绕过降档守卫——白名单只认 high/medium
        llm = FakeMatchLLM([{"match": "slug-a", "confidence": 0.3, "reason": "r"}])
        m = await match_task_skill("t", _catalog("slug-a"), llm)
        assert m.slug is None
        assert m.downgraded is True

    @pytest.mark.asyncio
    async def test_missing_confidence_downgraded_to_null(self):
        llm = FakeMatchLLM([{"match": "slug-a", "reason": "r"}])
        m = await match_task_skill("t", _catalog("slug-a"), llm)
        assert m.slug is None
        assert m.downgraded is True

    @pytest.mark.asyncio
    async def test_unknown_slug_rejected(self):
        llm = FakeMatchLLM([{"match": "ghost", "confidence": "high", "reason": "r"}])
        m = await match_task_skill("t", _catalog("slug-a"), llm)
        assert m.slug is None
        assert "unknown slug" in m.reason

    @pytest.mark.asyncio
    async def test_unparseable_output_degrades_to_null(self):
        llm = FakeMatchLLM([None])
        m = await match_task_skill("t", _catalog("slug-a"), llm)
        assert m.slug is None
        assert m.reason == "unparseable output"

    @pytest.mark.asyncio
    async def test_call_failure_retries_once_then_null(self):
        llm = FakeMatchLLM([RuntimeError("boom"), RuntimeError("boom2")])
        m = await match_task_skill("t", _catalog("slug-a"), llm)
        assert m.slug is None
        assert "call failed" in m.reason
        assert len(llm.calls) == 2

    @pytest.mark.asyncio
    async def test_call_failure_then_success(self):
        llm = FakeMatchLLM([RuntimeError("transient"), {"match": "slug-a", "confidence": "medium", "reason": "ok"}])
        m = await match_task_skill("t", _catalog("slug-a"), llm)
        assert m.slug == "slug-a"
        assert len(llm.calls) == 2

    @pytest.mark.asyncio
    async def test_prompt_contains_task_and_catalog(self):
        llm = FakeMatchLLM([{"match": None, "confidence": "high", "reason": "r"}])
        await match_task_skill("THE TASK TEXT", _catalog("slug-a"), llm)
        prompt = llm.calls[0]["user_prompt"]
        assert "THE TASK TEXT" in prompt
        assert "`slug-a`" in prompt
        assert "ESSENTIALLY THE SAME" in prompt
        assert "worse than no match" in llm.calls[0]["system_prompt"]


class TestBuildTaskSkillText:
    def test_header_and_body_composed(self):
        text = build_task_skill_text("my-slug", "CARD BODY")
        assert "my-slug" in text
        assert "PROVEN flow" in text
        assert "always re-read the current value from the page" in text  # 易变值声明
        assert "CARD BODY" in text

    def test_empty_body_header_only(self):
        text = build_task_skill_text("my-slug", "")
        assert "PROVEN flow" in text
        assert text.strip() != ""


# ── 3. build_state_message rendering ────────────────────────────────────


class TestTaskSkillRendering:
    def test_task_skill_between_task_and_domain_skill(self):
        msg = build_state_message(
            browser_state=_state(),
            task="the task",
            task_skill_description="TASK_SKILL_TEXT",
            skill_description="DOMAIN_SKILL_TEXT",
            sensitive_description="SECRET_TEXT",
        )
        assert msg.index("[Task]") < msg.index("[Task Skill]")
        assert msg.index("[Task Skill]") < msg.index("[Domain Skill]")
        assert msg.index("[Domain Skill]") < msg.index("[Available Secrets]")
        assert "TASK_SKILL_TEXT" in msg

    def test_without_task_skill_no_section(self):
        msg = build_state_message(
            browser_state=_state(), task="t", task_skill_description=None
        )
        assert "[Task Skill]" not in msg

    def test_task_skill_alone_still_renders(self):
        msg = build_state_message(
            browser_state=_state(), task="t", task_skill_description="SOLO"
        )
        assert "[Task Skill]" in msg
        assert "[Domain Skill]" not in msg


# ── 4. Agent wiring ─────────────────────────────────────────────────────


def _agent(
    tmp_path: Path,
    *,
    enable: bool = True,
    url: str = "http://localhost:7780/admin/",
    task: str = "the task",
):
    from tree_walker.agent.agent import Agent
    from tree_walker.config import AgentSettings

    browser = MagicMock()
    browser.get_current_url = AsyncMock(return_value=url)
    browser._settings = MagicMock(wait_between_actions=0.0)
    return Agent(
        task=task,
        llm=MagicMock(),
        browser=browser,
        settings=AgentSettings(
            enable_skill_injection=False,
            enable_task_skill_injection=enable,
            skills_dir=str(tmp_path),
        ),
    )


class TestAgentTaskSkillWiring:
    @pytest.mark.asyncio
    async def test_match_hit_loads_text(self, tmp_path, monkeypatch):
        _write_card(tmp_path, "localhost_7780", "slug-a", sop="SOP BODY")
        agent = _agent(tmp_path)
        monkeypatch.setattr(
            "tree_walker.agent.agent.match_task_skill",
            AsyncMock(return_value=TaskSkillMatch(slug="slug-a", confidence="high", reason="ok")),
        )
        await agent._match_task_skill()
        assert agent._task_skill_text is not None
        assert "slug-a" in agent._task_skill_text
        assert "SOP BODY" in agent._task_skill_text
        assert agent._task_skill_slug == "slug-a"  # obs 事件用（step.py SkillActiveEvent）

    @pytest.mark.asyncio
    async def test_blank_task_early_return(self, tmp_path, monkeypatch):
        # v2 §4.5：无任务文本 = 显式降级路径，不发起匹配调用
        _write_card(tmp_path, "localhost_7780", "slug-a")
        agent = _agent(tmp_path, task="   ")
        called = AsyncMock()
        monkeypatch.setattr("tree_walker.agent.agent.match_task_skill", called)
        await agent._match_task_skill()
        called.assert_not_called()
        assert agent._task_skill_text is None

    @pytest.mark.asyncio
    async def test_preferred_url_wins_over_stale_page(self, tmp_path, monkeypatch):
        # Page.navigate 应答可早于新文档 commit——preferred_url（导航目标）必须
        # 优先于当前页读数（残页），否则串行多 host 会话读错 host 的 catalog
        _write_card(tmp_path, "right-host", "slug-a")
        agent = _agent(tmp_path, url="http://stale-host/old")
        mocked = AsyncMock(
            return_value=TaskSkillMatch(slug="slug-a", confidence="high", reason="ok")
        )
        monkeypatch.setattr("tree_walker.agent.agent.match_task_skill", mocked)
        await agent._match_task_skill(preferred_url="http://right-host/x")
        mocked.assert_called_once()
        catalog_arg = mocked.call_args.args[1]
        assert [c.slug for c in catalog_arg] == ["slug-a"]

    @pytest.mark.asyncio
    async def test_no_match_leaves_text_none(self, tmp_path, monkeypatch):
        _write_card(tmp_path, "localhost_7780", "slug-a")
        agent = _agent(tmp_path)
        monkeypatch.setattr(
            "tree_walker.agent.agent.match_task_skill",
            AsyncMock(return_value=TaskSkillMatch(slug=None, confidence="high", reason="variant")),
        )
        await agent._match_task_skill()
        assert agent._task_skill_text is None

    @pytest.mark.asyncio
    async def test_empty_catalog_skips_matcher(self, tmp_path, monkeypatch):
        agent = _agent(tmp_path)  # 无卡
        called = AsyncMock()
        monkeypatch.setattr("tree_walker.agent.agent.match_task_skill", called)
        await agent._match_task_skill()
        called.assert_not_called()
        assert agent._task_skill_text is None

    @pytest.mark.asyncio
    async def test_no_host_skips_everything(self, monkeypatch):
        agent = _agent(Path("."), url="about:blank")
        called = AsyncMock()
        monkeypatch.setattr("tree_walker.agent.agent.match_task_skill", called)
        await agent._match_task_skill()
        called.assert_not_called()

    @pytest.mark.asyncio
    async def test_match_exception_propagates_to_run_hook_guard(self, tmp_path, monkeypatch):
        # 方法级异常由 run() 的 try/except 兜住（docs/p7/03 §4.5）；这里验证异常确实
        # 从方法冒出（供 hook 捕获），hook 语义由 test_run_hook_guards_exception 验证。
        _write_card(tmp_path, "localhost_7780", "slug-a")
        agent = _agent(tmp_path)
        monkeypatch.setattr(
            "tree_walker.agent.agent.match_task_skill",
            AsyncMock(side_effect=RuntimeError("llm down")),
        )
        with pytest.raises(RuntimeError):
            await agent._match_task_skill()

    def test_gate_method_off_blocks_injection(self, tmp_path):
        # 门控在 _current_task_skill_text（step.py 每步调用）：开关关 → 文本在也不注入。
        # 直接驱动生产方法，而非在测试里复刻三元（同义反复测不出回归）。
        agent = _agent(tmp_path, enable=False)
        agent._task_skill_text = "TEXT PRESENT"
        assert agent._current_task_skill_text() is None

    def test_gate_method_on_with_text(self, tmp_path):
        agent = _agent(tmp_path, enable=True)
        agent._task_skill_text = "TEXT PRESENT"
        assert agent._current_task_skill_text() == "TEXT PRESENT"

    def test_gate_method_on_without_text(self, tmp_path):
        agent = _agent(tmp_path, enable=True)
        assert agent._current_task_skill_text() is None

    def test_end_to_end_rendering(self, tmp_path):
        agent = _agent(tmp_path)
        agent._task_skill_text = "HEADER + CARD"
        msg = build_state_message(
            browser_state=_state(), task="t", task_skill_description=agent._task_skill_text
        )
        assert "[Task Skill]" in msg
        assert "HEADER + CARD" in msg


class TestTaskSkillSwitches:
    def test_default_off(self):
        from tree_walker.config import AgentSettings

        assert AgentSettings().enable_task_skill_injection is False
        assert AgentSettings().task_skill_llm is None

    def test_env_switch_parsing(self, monkeypatch):
        from tree_walker.config import load_settings

        for val, expected in [
            ("true", True),
            ("True", True),
            ("TRUE", True),
            ("1", False),  # 沿 enable_skill_injection 的严格布尔口径
            ("yes", False),
            ("", False),
            ("false", False),
        ]:
            monkeypatch.setenv("AGENT_ENABLE_TASK_SKILL_INJECTION", val)
            assert load_settings().agent.enable_task_skill_injection is expected, f"value={val!r}"

    def test_env_default_off_when_unset(self, monkeypatch):
        from tree_walker.config import load_settings

        monkeypatch.delenv("AGENT_ENABLE_TASK_SKILL_INJECTION", raising=False)
        assert load_settings().agent.enable_task_skill_injection is False

    def test_dedicated_llm_from_env(self, monkeypatch):
        from tree_walker.config import load_settings

        monkeypatch.setenv("AGENT_TASK_SKILL_MODEL", "glm-5.1-flash")
        settings = load_settings().agent
        assert settings.task_skill_llm is not None
        assert settings.task_skill_llm.model == "glm-5.1-flash"

    def test_no_dedicated_llm_by_default(self, monkeypatch):
        from tree_walker.config import load_settings

        monkeypatch.delenv("AGENT_TASK_SKILL_MODEL", raising=False)
        assert load_settings().agent.task_skill_llm is None

    def test_agent_reuses_main_llm_without_dedicated(self, tmp_path):
        agent = _agent(tmp_path)
        assert agent._task_skill_llm is agent.llm


# ── 5. LLMClient.structured_call ────────────────────────────────────────


def _llm_client() -> "LLMClient":
    from tree_walker.config import LLMSettings
    from tree_walker.llm.client import LLMClient

    return LLMClient(LLMSettings(api_key="test-key", model="test-model"))


def _tool_response(payload: dict):
    return SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", name="structured_result", input=payload)]
    )


class TestStructuredCall:
    @pytest.mark.asyncio
    async def test_tool_forced_path_returns_dict(self, monkeypatch):
        client = _llm_client()
        inner = MagicMock()
        inner.messages.create = MagicMock(return_value=_tool_response({"match": "s", "confidence": "high", "reason": "r"}))
        client.client = inner
        result = await client.structured_call(
            system_prompt="sys", user_prompt="usr", output_schema={"type": "object"}
        )
        assert result == {"match": "s", "confidence": "high", "reason": "r"}
        # tool_choice 强制 + 单 user message
        kwargs = inner.messages.create.call_args.kwargs
        assert kwargs["tool_choice"] == {"type": "tool", "name": "structured_result"}
        assert kwargs["messages"][0]["content"] == "usr"

    @pytest.mark.asyncio
    async def test_max_tokens_defaults_to_client_setting(self):
        # AGENT_TASK_SKILL_MAX_TOKENS 等专用配置落在 client.max_tokens——缺省必须
        # 回落它（否则配置变死旋钮；thinking 模型思考 token 也计入 max_tokens）
        client = _llm_client()
        client.max_tokens = 7777
        inner = MagicMock()
        inner.messages.create = MagicMock(return_value=_tool_response({"x": 1}))
        client.client = inner
        await client.structured_call(
            system_prompt="s", user_prompt="u", output_schema={"type": "object"}
        )
        assert inner.messages.create.call_args.kwargs["max_tokens"] == 7777

    @pytest.mark.asyncio
    async def test_explicit_max_tokens_wins(self):
        client = _llm_client()
        client.max_tokens = 7777
        inner = MagicMock()
        inner.messages.create = MagicMock(return_value=_tool_response({"x": 1}))
        client.client = inner
        await client.structured_call(
            system_prompt="s", user_prompt="u", output_schema={"type": "object"}, max_tokens=99
        )
        assert inner.messages.create.call_args.kwargs["max_tokens"] == 99

    @pytest.mark.asyncio
    async def test_text_fallback_parses_fenced_json(self, monkeypatch):
        client = _llm_client()
        text_block = SimpleNamespace(type="text", text='```json\n{"match": null}\n```')
        inner = MagicMock()
        inner.messages.create = MagicMock(return_value=SimpleNamespace(content=[text_block]))
        client.client = inner
        result = await client.structured_call(
            system_prompt="sys", user_prompt="usr", output_schema={"type": "object"}
        )
        assert result == {"match": None}

    @pytest.mark.asyncio
    async def test_text_fallback_unparseable_returns_none(self):
        client = _llm_client()
        text_block = SimpleNamespace(type="text", text="no json here")
        inner = MagicMock()
        inner.messages.create = MagicMock(return_value=SimpleNamespace(content=[text_block]))
        client.client = inner
        result = await client.structured_call(
            system_prompt="sys", user_prompt="usr", output_schema={"type": "object"}
        )
        assert result is None
