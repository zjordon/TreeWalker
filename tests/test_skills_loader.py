"""Tests for SkillLoader — read domain-skills/<host>/*.md, render + cache.

Mirrors the loader contract in docs/skill-injection-design.md §8: fixed render
order, silent on missing files/host, per-host cache, invalidate. Does NOT test
the enable switch (that is an agent-layer concern, see test_agent_skill_injection).
"""

from __future__ import annotations

from tree_walker.skills import loader as loader_module
from tree_walker.skills.loader import SkillLoader


def _write_skill(host_dir, name: str, content: str) -> None:
    """Write a skill file under a host dir (host_dir is a pathlib.Path)."""
    host_dir.mkdir(parents=True, exist_ok=True)
    (host_dir / name).write_text(content, encoding="utf-8")


class TestRepoRootFallback:
    """P7 form_interaction 建议4 补丁：相对 skills_dir 在 CWD 下不存在时，回退到
    tree_walker 包所在仓库根下的同名目录（评测 runner 的 CWD 是独立工作空间，
    evals/webarena 下没有 domain-skills；editable 安装时包路径即指向本仓库）。"""

    def test_fallback_to_package_repo_root(self, tmp_path, monkeypatch):
        # 伪造仓库结构：<repo>/domain-skills/<host>/_sop.md + src/tree_walker/skills/loader.py
        repo = tmp_path / "fakerepo"
        _write_skill(repo / "domain-skills" / "h.com", "_sop.md", "sop-text")
        fake_loader_file = repo / "src" / "tree_walker" / "skills" / "loader.py"
        fake_loader_file.parent.mkdir(parents=True)

        # CWD 切到一个没有 domain-skills 的目录
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        monkeypatch.setattr(loader_module, "__file__", str(fake_loader_file))

        text = SkillLoader("domain-skills").load_for_host("h.com")
        assert "sop-text" in text

    def test_cwd_hit_takes_precedence(self, tmp_path, monkeypatch):
        # CWD 下存在 domain-skills 时优先 CWD（原行为不变）
        _write_skill(tmp_path / "domain-skills" / "h.com", "_sop.md", "cwd-text")
        monkeypatch.chdir(tmp_path)

        text = SkillLoader("domain-skills").load_for_host("h.com")
        assert "cwd-text" in text

    def test_absolute_path_bypasses_fallback(self, tmp_path, monkeypatch):
        # 绝对路径不存在时保持原行为（不回退、无 skill），绝不指向别处
        repo = tmp_path / "fakerepo"
        _write_skill(repo / "domain-skills" / "h.com", "_sop.md", "sop-text")
        monkeypatch.setattr(
            loader_module, "__file__",
            str(repo / "src" / "tree_walker" / "skills" / "loader.py"),
        )

        missing = tmp_path / "no-such-dir"
        assert SkillLoader(missing).load_for_host("h.com") == ""


class TestLoadForHost:
    def test_three_files_rendered_in_fixed_order(self, tmp_path):
        host_dir = tmp_path / "www.bilibili.com"
        _write_skill(host_dir, "_sop.md", "upload flow")
        _write_skill(host_dir, "selectors.md", "#upload-btn")
        _write_skill(host_dir, "quirks.md", "wait modal")

        text = SkillLoader(tmp_path).load_for_host("www.bilibili.com")

        assert "[SOP]" in text and "upload flow" in text
        assert "[SELECTORS]" in text and "#upload-btn" in text
        assert "[QUIRKS]" in text and "wait modal" in text
        # 固定顺序：SOP -> SELECTORS -> QUIRKS
        assert text.index("[SOP]") < text.index("[SELECTORS]") < text.index("[QUIRKS]")

    def test_partial_files_only_existing_rendered(self, tmp_path):
        host_dir = tmp_path / "example.com"
        _write_skill(host_dir, "_sop.md", "sop text")
        _write_skill(host_dir, "quirks.md", "quirks text")

        text = SkillLoader(tmp_path).load_for_host("example.com")

        assert "[SOP]" in text and "[QUIRKS]" in text
        assert "[SELECTORS]" not in text

    def test_fixed_order_regardless_of_fs_write_order(self, tmp_path):
        host_dir = tmp_path / "example.com"
        # 故意乱序写入
        _write_skill(host_dir, "quirks.md", "quirks text")
        _write_skill(host_dir, "_sop.md", "sop text")
        _write_skill(host_dir, "selectors.md", "selectors text")

        text = SkillLoader(tmp_path).load_for_host("example.com")
        assert text.index("[SOP]") < text.index("[SELECTORS]") < text.index("[QUIRKS]")

    def test_missing_host_dir_returns_empty(self, tmp_path):
        assert SkillLoader(tmp_path).load_for_host("no.such.host") == ""

    def test_missing_skills_root_returns_empty(self, tmp_path):
        # 根目录本身不存在 —— 构造不报错，加载返回空串
        loader = SkillLoader(tmp_path / "does-not-exist")
        assert loader.load_for_host("example.com") == ""

    def test_none_or_empty_host_returns_empty(self, tmp_path):
        loader = SkillLoader(tmp_path)
        assert loader.load_for_host(None) == ""
        assert loader.load_for_host("") == ""

    def test_blank_content_file_skipped(self, tmp_path):
        host_dir = tmp_path / "example.com"
        _write_skill(host_dir, "_sop.md", "sop text")
        _write_skill(host_dir, "selectors.md", "   \n\n  ")  # 仅空白

        text = SkillLoader(tmp_path).load_for_host("example.com")
        assert "[SOP]" in text
        assert "[SELECTORS]" not in text  # 空内容被跳过

    def test_non_utf8_file_skipped(self, tmp_path):
        host_dir = tmp_path / "example.com"
        host_dir.mkdir(parents=True)
        (host_dir / "_sop.md").write_text("good sop", encoding="utf-8")
        (host_dir / "selectors.md").write_bytes(b"\xff\xfe\x00binary garbage")  # 非 UTF-8

        text = SkillLoader(tmp_path).load_for_host("example.com")
        assert "[SOP]" in text and "good sop" in text
        assert "[SELECTORS]" not in text  # 坏编码文件被跳过（UnicodeDecodeError）

    def test_second_call_hits_cache_no_reread(self, tmp_path):
        host_dir = tmp_path / "example.com"
        _write_skill(host_dir, "_sop.md", "original")

        loader = SkillLoader(tmp_path)
        first = loader.load_for_host("example.com")
        assert "original" in first

        # 改盘后第二次调用 —— 缓存命中，不重读
        _write_skill(host_dir, "_sop.md", "changed-on-disk")
        second = loader.load_for_host("example.com")
        assert second == first
        assert "changed-on-disk" not in second

    def test_multiple_hosts_cached_independently(self, tmp_path):
        _write_skill(tmp_path / "a.com", "_sop.md", "A sop")
        _write_skill(tmp_path / "b.com", "_sop.md", "B sop")

        loader = SkillLoader(tmp_path)
        a = loader.load_for_host("a.com")
        b = loader.load_for_host("b.com")
        assert "A sop" in a and "B sop" not in a
        assert "B sop" in b and "A sop" not in b


class TestInvalidate:
    def test_invalidate_single_host_rereads_on_next_call(self, tmp_path):
        host_dir = tmp_path / "example.com"
        _write_skill(host_dir, "_sop.md", "v1")

        loader = SkillLoader(tmp_path)
        assert "v1" in loader.load_for_host("example.com")

        _write_skill(host_dir, "_sop.md", "v2")
        loader.invalidate("example.com")
        assert "v2" in loader.load_for_host("example.com")

    def test_invalidate_all_clears_cache(self, tmp_path):
        _write_skill(tmp_path / "a.com", "_sop.md", "A")
        _write_skill(tmp_path / "b.com", "_sop.md", "B")

        loader = SkillLoader(tmp_path)
        loader.load_for_host("a.com")
        loader.load_for_host("b.com")
        assert loader._cache

        loader.invalidate()
        assert loader._cache == {}

    def test_invalidate_one_host_leaves_others(self, tmp_path):
        _write_skill(tmp_path / "a.com", "_sop.md", "A")
        _write_skill(tmp_path / "b.com", "_sop.md", "B")

        loader = SkillLoader(tmp_path)
        loader.load_for_host("a.com")
        loader.load_for_host("b.com")
        loader.invalidate("a.com")
        assert "a.com" not in loader._cache
        assert "b.com" in loader._cache

    def test_invalidate_missing_host_is_noop(self, tmp_path):
        loader = SkillLoader(tmp_path)
        loader.invalidate("never-loaded")  # 不报错


class TestConstructorSafety:
    def test_constructor_no_io_on_nonexistent_dir(self, tmp_path):
        # 默认关闭时 agent 也建 loader —— 构造必须零副作用、不报错
        SkillLoader(tmp_path / "never-created")

    def test_constructor_accepts_str_or_path(self, tmp_path):
        SkillLoader(str(tmp_path))  # str 路径也接受
