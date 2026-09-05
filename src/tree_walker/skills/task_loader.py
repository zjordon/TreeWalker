"""Scan ``domain-skills/<host_key>/tasks/*/_task.json`` for task-level skill cards.

TreeForge P4 双产物的任务级卡片（契约见 docs/p7/03-task-skill-loading-design.md
§2）：候选域 = 当前 host_key（``extract_host_with_port`` 端口限定形态），catalog
（slug + description + keywords）一次喂给 LLM 匹配器，命中才读三件套注入
``[Task Skill]``。缺目录 / 坏卡静默跳过（skill 是可选增强，安全降级 = 现状）；
路径解析复用 ``SkillLoader._resolve_dir`` 的 repo-root 回退——评测 runner 以
独立工作空间为 CWD 时相对 ``domain-skills`` 不存在（2026-08-24 踩坑）。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from tree_walker.skills.loader import SkillLoader

logger = logging.getLogger(__name__)

# 命中卡三件套读序（与站点级 loader._SKILL_FILES 一致：流程骨架在前）。
_TASK_CARD_FILES: tuple[str, ...] = ("_sop.md", "selectors.md", "quirks.md")

__all__ = ["TaskCardMeta", "TaskSkillLoader"]


@dataclass(frozen=True)
class TaskCardMeta:
    """一张任务卡的检索元数据（``_task.json``，TreeForge P4 S6 契约）。"""

    slug: str
    description: str
    keywords: tuple[str, ...] = ()
    distilled_at: str = ""
    card_dir: Path = field(default_factory=Path)

    def catalog_line(self) -> str:
        """渲染进匹配 prompt 的一行 catalog 条目（docs/p7/03 附录 B 格式）。"""
        kw = ", ".join(self.keywords)
        suffix = f" | keywords: {kw}" if kw else ""
        return f"- `{self.slug}` — {self.description}{suffix}"


class TaskSkillLoader:
    """Load and cache per-host task-skill catalogs and matched card text.

    构造零 IO（挂在 agent 已持有的 ``SkillLoader`` 实例上，复用其根目录解析）；
    首次 ``catalog()`` / ``card_text()`` 才读盘并按 host_key 进程内缓存——每任务 /
    每 run 新建 Agent，缓存生命周期足够（docs/p7/03 §五：不做 mtime / 磁盘缓存）。
    """

    def __init__(self, skill_loader: SkillLoader) -> None:
        self._skill_loader = skill_loader
        self._catalog_cache: dict[str, list[TaskCardMeta]] = {}

    def catalog(self, host_key: str | None) -> list[TaskCardMeta]:
        """Return the task-card catalog for ``host_key`` (empty list if none)."""
        if not host_key:
            return []
        if host_key in self._catalog_cache:
            return self._catalog_cache[host_key]
        tasks_dir = self._skill_loader._resolve_dir() / host_key / "tasks"
        cards: list[TaskCardMeta] = []
        if tasks_dir.is_dir():
            for meta_path in sorted(tasks_dir.glob("*/_task.json")):
                card = self._parse_card(meta_path)
                if card is not None:
                    cards.append(card)
        # 大声日志（docs/p7/03 §2.1 防再犯）：catalog 空是显式可见的状态，
        # 不是埋在降级语义里的静默零命中。
        logger.info("task-skill catalog: %d cards (host_key=%s)", len(cards), host_key)
        self._catalog_cache[host_key] = cards
        return cards

    @staticmethod
    def _parse_card(meta_path: Path) -> TaskCardMeta | None:
        """解析单张 ``_task.json``；坏卡跳过不废整个 catalog（warning）。"""
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            logger.warning("task-skill: skip unparseable card %s (%s)", meta_path, e)
            return None
        if not isinstance(data, dict):
            logger.warning("task-skill: skip non-dict card %s", meta_path)
            return None
        slug = str(data.get("slug") or "").strip() or meta_path.parent.name
        description = str(data.get("task_description") or "").strip()
        if not description:
            # 无描述 = 无检索锚点，卡不可匹配——跳过（如 TreeForge 模板模式产物）。
            logger.warning("task-skill: skip card without description %s", meta_path)
            return None
        keywords = tuple(
            str(k) for k in (data.get("task_keywords") or []) if str(k).strip()
        )
        return TaskCardMeta(
            slug=slug,
            description=description,
            keywords=keywords,
            distilled_at=str(data.get("distilled_at") or ""),
            card_dir=meta_path.parent,
        )

    @staticmethod
    def newest_distilled_at(catalog: list[TaskCardMeta]) -> str:
        """Catalog 内最新 ``distilled_at``（ISO 字符串字典序可比）。

        手工迁移的过期探针（docs/p7/03 S0a/S0b）：S0b 落地前，本值与 treeforge
        侧产物时间差一眼可查——treeforge 重蒸后忘了拷，这里不会跟着走。
        """
        return max((c.distilled_at for c in catalog if c.distilled_at), default="")

    def card_text(self, card: TaskCardMeta) -> str:
        """Render a matched card's three files in fixed order (empty if none)."""
        parts: list[str] = []
        for filename in _TASK_CARD_FILES:
            path = card.card_dir / filename
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError):
                continue
            if text:
                parts.append(text)
        return "\n\n".join(parts)
