"""Load ``domain-skills/<host>/*.md`` and render into injectable text.

Fixed read order: SOP (skeleton) -> SELECTORS (bulk) -> QUIRKS -> API, matching
TreeForge ``init-plan.md`` section 5 output shape. Missing files / missing host
dir are silent (skill is an optional enhancement). Per-host cache avoids
per-step IO: the agent calls ``load_for_host`` every step, but only the first
call per host reads disk.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# (filename, section header) in fixed render order — skeleton first, bulk next.
_SKILL_FILES: tuple[tuple[str, str], ...] = (
    ("_sop.md", "[SOP]"),
    ("selectors.md", "[SELECTORS]"),
    ("quirks.md", "[QUIRKS]"),
)

__all__ = ["SkillLoader"]


class SkillLoader:
    """Load and cache per-host skill text.

    The constructor only stores the directory path — no IO, no dir-existence
    check — so it is safe to instantiate unconditionally (the agent builds it
    even when skill injection is disabled). ``load_for_host`` does the first
    read and caches; subsequent calls for the same host hit the cache.
    """

    def __init__(self, skills_dir: str | Path) -> None:
        self._dir = Path(skills_dir)
        self._cache: dict[str, str] = {}

    def load_for_host(self, host: str | None) -> str:
        """Return rendered skill text for ``host`` (empty string if none).

        Empty string (not None) so callers can treat it uniformly; the agent
        wraps it into ``str | None`` at the injection boundary.
        """
        if not host:
            return ""
        if host in self._cache:
            return self._cache[host]
        host_dir = self._dir / host
        if not host_dir.is_dir():
            # 首次访问该 host 且无对应目录——打日志便于排查 host 不匹配
            # （如 agent 访问 member.bilibili.com 但 skill 放在 www.bilibili.com）
            logger.info("skill: no directory for host=%s (looked at %s)", host, host_dir)
            self._cache[host] = ""
            return ""
        parts: list[str] = []
        loaded: list[str] = []
        for filename, header in _SKILL_FILES:
            path = host_dir / filename
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError):
                continue
            if not text:
                continue
            parts.append(header)
            parts.append(text)
            parts.append("")
            loaded.append(filename)
        rendered = "\n".join(parts).strip()
        self._cache[host] = rendered
        if rendered:
            logger.info("skill loaded: host=%s chars=%d files=%s", host, len(rendered), loaded)
        else:
            logger.info("skill empty: host=%s (all files blank/missing)", host)
        return rendered

    def invalidate(self, host: str | None = None) -> None:
        """Drop cached text for one host (``host=None`` clears all)."""
        if host is None:
            self._cache.clear()
        else:
            self._cache.pop(host, None)
