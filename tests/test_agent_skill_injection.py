"""Tests for skill injection — extract_host / [Domain Skill] rendering /
agent wiring / enable switch.

Four groups per docs/skill-injection-design.md §8:
  1. extract_host
  2. build_state_message [Domain Skill] rendering
  3. Agent._build_skill_description (extract_host -> loader)
  4. enable switch gating (default off, opt-in)
"""

from __future__ import annotations

from unittest.mock import MagicMock

from tree_walker.browser.url_utils import extract_host
from tree_walker.browser.views import BrowserStateSummary, SerializedDOMState
from tree_walker.prompts.system_prompt import build_state_message


def _state(url: str = "https://example.com", title: str = "Ex") -> BrowserStateSummary:
    """Minimal real BrowserStateSummary (tabs/recent_events/file_inputs default empty)."""
    return BrowserStateSummary(
        url=url,
        title=title,
        dom_state=SerializedDOMState(_root=None, selector_map={}, element_tree_text="dom"),
    )


class TestExtractHost:
    def test_normal_https_url(self):
        assert extract_host("https://www.bilibili.com/video/BV123") == "www.bilibili.com"

    def test_http_url(self):
        assert extract_host("http://example.com/path?q=1") == "example.com"

    def test_schemeless_url_with_path(self):
        # 无 scheme 但像域名 —— // 兜底解析
        assert extract_host("www.bilibili.com/video/1") == "www.bilibili.com"

    def test_empty_or_none(self):
        assert extract_host("") is None
        assert extract_host(None) is None

    def test_invalid_string_returns_none(self):
        assert extract_host("not a url at all") is None

    def test_uppercase_host_normalized(self):
        # urlparse 把 hostname 规范化为小写
        assert extract_host("https://EXAMPLE.com/") == "example.com"

    def test_parse_error_returns_none(self, monkeypatch):
        # urlparse 抛异常 -> 防御性返回 None
        from tree_walker.browser import url_utils

        def boom(url):
            raise ValueError("parse error")

        monkeypatch.setattr(url_utils, "urlparse", boom)
        assert url_utils.extract_host("https://x.com") is None


class TestDomainSkillRendering:
    """build_state_message 的 [Domain Skill] 段渲染。"""

    def test_with_skill_renders_section(self):
        msg = build_state_message(
            browser_state=_state(),
            task="do something",
            skill_description="[SOP]\nupload flow",
        )
        assert "[Domain Skill]" in msg
        assert "upload flow" in msg

    def test_without_skill_no_section(self):
        msg = build_state_message(
            browser_state=_state(),
            task="do something",
            skill_description=None,
        )
        assert "[Domain Skill]" not in msg

    def test_skill_after_task_before_secrets(self):
        msg = build_state_message(
            browser_state=_state(),
            task="the task",
            skill_description="SKILL_TEXT",
            sensitive_description="SECRET_TEXT",
        )
        assert msg.index("[Task]") < msg.index("[Domain Skill]")
        assert msg.index("[Domain Skill]") < msg.index("[Available Secrets]")


class TestBuildSkillDescription:
    """Agent._build_skill_description：extract_host -> loader.load_for_host。"""

    def _agent(self, skills_dir, enable: bool = True):
        from tree_walker.agent.agent import Agent
        from tree_walker.config import AgentSettings

        return Agent(
            task="t",
            llm=MagicMock(),
            browser=MagicMock(),
            settings=AgentSettings(enable_skill_injection=enable, skills_dir=str(skills_dir)),
        )

    @staticmethod
    def _write_skill(host_dir, name, content):
        from pathlib import Path

        p = Path(host_dir)
        p.mkdir(parents=True, exist_ok=True)
        (p / name).write_text(content, encoding="utf-8")

    def test_matching_host_returns_text(self, tmp_path):
        self._write_skill(tmp_path / "www.bilibili.com", "_sop.md", "bilisop")

        agent = self._agent(tmp_path)
        text = agent._build_skill_description("https://www.bilibili.com/v/1")
        assert text is not None
        assert "bilisop" in text

    def test_non_matching_host_returns_none(self, tmp_path):
        self._write_skill(tmp_path / "www.bilibili.com", "_sop.md", "bilisop")

        agent = self._agent(tmp_path)
        assert agent._build_skill_description("https://other.com/") is None

    def test_loader_missing_returns_none(self, tmp_path):
        # skills_dir 不存在 —— loader 静默返回空 -> None
        agent = self._agent(tmp_path / "no-such-dir")
        assert agent._build_skill_description("https://www.bilibili.com/") is None

    def test_invalid_url_returns_none(self, tmp_path):
        agent = self._agent(tmp_path)
        assert agent._build_skill_description("not a url") is None
        assert agent._build_skill_description("") is None
        assert agent._build_skill_description(None) is None

    def test_different_hosts_get_different_skills(self, tmp_path):
        for host, content in [("a.com", "AAA"), ("b.com", "BBB")]:
            self._write_skill(tmp_path / host, "_sop.md", content)

        agent = self._agent(tmp_path)
        assert "AAA" in agent._build_skill_description("https://a.com/")
        assert "BBB" in agent._build_skill_description("https://b.com/")


class TestEnableSwitch:
    """开关门控：默认关，开关关时不注入（即使文件在），开关开时注入。

    门控在调用点（step.py _prepare_context 三元），方法本体不感知开关
    （无内守卫，严格镜像 _build_sensitive_description）。这里复刻调用点三元
    验证门控语义，并端到端验证渲染。
    """

    def test_default_is_off(self):
        from tree_walker.config import AgentSettings

        assert AgentSettings().enable_skill_injection is False

    def test_construct_arg_enables(self):
        from tree_walker.config import AgentSettings

        assert AgentSettings(enable_skill_injection=True).enable_skill_injection is True

    def test_env_only_true_enables(self, monkeypatch):
        from tree_walker.config import load_settings

        for val, expected in [
            ("true", True),
            ("True", True),
            ("TRUE", True),
            ("1", False),
            ("yes", False),
            ("on", False),
            ("False", False),
            ("", False),
        ]:
            monkeypatch.setenv("AGENT_ENABLE_SKILL_INJECTION", val)
            assert load_settings().agent.enable_skill_injection is expected, f"value={val!r}"

    @staticmethod
    def _write_skill(host_dir, name, content):
        from pathlib import Path

        p = Path(host_dir)
        p.mkdir(parents=True, exist_ok=True)
        (p / name).write_text(content, encoding="utf-8")

    def test_off_with_skill_present_returns_none_at_call_site(self, tmp_path):
        # 开关关 + skill 文件存在 + loader 可加载 -> 调用点传 None
        self._write_skill(tmp_path / "www.bilibili.com", "_sop.md", "bilisop")

        from tree_walker.agent.agent import Agent
        from tree_walker.config import AgentSettings

        agent = Agent(
            task="t",
            llm=MagicMock(),
            browser=MagicMock(),
            settings=AgentSettings(enable_skill_injection=False, skills_dir=str(tmp_path)),
        )
        url = "https://www.bilibili.com/"
        # 复刻 step.py 调用点三元
        skill_desc = (
            agent._build_skill_description(url)
            if agent._enable_skill_injection
            else None
        )
        assert skill_desc is None  # 开关关 -> None
        # 证明是开关拦截，不是 loader 坏：loader 本身可加载
        assert agent._skill_loader.load_for_host("www.bilibili.com") != ""

    def test_off_does_not_call_loader(self, tmp_path):
        # 开关关 -> 调用点短路，load_for_host 0 次调用（零 IO）
        self._write_skill(tmp_path / "www.bilibili.com", "_sop.md", "bilisop")

        from tree_walker.agent.agent import Agent
        from tree_walker.config import AgentSettings

        agent = Agent(
            task="t",
            llm=MagicMock(),
            browser=MagicMock(),
            settings=AgentSettings(enable_skill_injection=False, skills_dir=str(tmp_path)),
        )
        agent._skill_loader.load_for_host = MagicMock(return_value="SHOULD_NOT_BE_USED")

        url = "https://www.bilibili.com/"
        skill_desc = (
            agent._build_skill_description(url)
            if agent._enable_skill_injection
            else None
        )
        assert skill_desc is None
        agent._skill_loader.load_for_host.assert_not_called()  # 0 次

    def test_on_with_skill_present_injects_end_to_end(self, tmp_path):
        # 开关开 + skill 文件存在 -> 注入，state message 含 [Domain Skill]
        self._write_skill(tmp_path / "www.bilibili.com", "_sop.md", "bilisop")

        from tree_walker.agent.agent import Agent
        from tree_walker.config import AgentSettings

        agent = Agent(
            task="t",
            llm=MagicMock(),
            browser=MagicMock(),
            settings=AgentSettings(enable_skill_injection=True, skills_dir=str(tmp_path)),
        )
        url = "https://www.bilibili.com/"
        skill_desc = (
            agent._build_skill_description(url)
            if agent._enable_skill_injection
            else None
        )
        assert skill_desc is not None
        assert "bilisop" in skill_desc

        msg = build_state_message(browser_state=_state(), task="t", skill_description=skill_desc)
        assert "[Domain Skill]" in msg

    def test_on_with_skill_missing_returns_none(self, tmp_path):
        # 开关开但 host 目录不存在 -> 静默 None（双层静默第 a 层）
        from tree_walker.agent.agent import Agent
        from tree_walker.config import AgentSettings

        agent = Agent(
            task="t",
            llm=MagicMock(),
            browser=MagicMock(),
            settings=AgentSettings(enable_skill_injection=True, skills_dir=str(tmp_path)),
        )
        skill_desc = (
            agent._build_skill_description("https://www.bilibili.com/")
            if agent._enable_skill_injection
            else None
        )
        assert skill_desc is None
