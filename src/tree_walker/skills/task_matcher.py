"""LLM-as-ranker matcher for task-level skill cards (docs/p7/03 §4).

保守匹配（误命中比未命中更糟——蓝图原话）：只有「本质同一操作」才命中，近邻
变体默认不命中，拿不准返回 null。输出经 ``LLMClient.structured_call`` 工具强制
返回（无自由文本 JSON + 解析重试的脆弱约定）；``confidence == "low"`` 在解析侧
强制降档为未命中——把「拿不准返回 null」从 prompt 自觉变成解析器强制。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from tree_walker.skills.task_loader import TaskCardMeta

logger = logging.getLogger(__name__)

__all__ = ["TaskSkillMatch", "match_task_skill", "build_task_skill_text"]

_MATCH_SYSTEM_PROMPT = (
    "You are a task-matching judge. You compare a user task against a catalog of "
    "recorded task skills and decide whether one is essentially the same operation. "
    "Be conservative: a wrong match is worse than no match."
)

_MATCH_PROMPT_TEMPLATE = """You are a task-matching judge. Given a user task and a catalog of recorded task skills,
decide which recorded task is ESSENTIALLY THE SAME operation as the user task.

Rules:
- Match ONLY if a recorded task has the same goal on the same kind of target object
  (e.g. "count products with 0 quantity" matches a card describing exactly that).
  Surface wording may differ (synonyms, language).
- "Similar but different" is NOT a match: different filter dimension (by SKU vs by name),
  different object (orders vs invoices), different output (count vs list vs detail).
- When in doubt, return null — a wrong match is worse than no match; the agent will
  explore fine on its own.

User task:
{task}

Catalog (same site):
{catalog}"""

_MATCH_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "match": {
            "type": ["string", "null"],
            "description": "Matched card slug, or null when no card is essentially the same task.",
        },
        "confidence": {
            "type": "string",
            "enum": ["high", "medium", "low"],
            "description": "How certain the match (or non-match) is.",
        },
        "reason": {
            "type": "string",
            "description": "One-sentence justification.",
        },
    },
    "required": ["match", "confidence", "reason"],
}

# 命中卡注入头声明（docs/p7/03 附录 B；末句 = 易变值声明，针对答案固化实测）。
_TASK_SKILL_HEADER_TEMPLATE = (
    "A recorded task matching your current goal was found (slug: {slug}). It describes a\n"
    "PROVEN flow for essentially this task — follow it as guidance. The live page is the\n"
    "source of truth: if any step no longer matches reality, adapt and explore on your own.\n"
    "Concrete values in this card (counts, amounts, dates, names) are snapshots from the\n"
    "recording session — always re-read the current value from the page."
)

# 匹配调用单次超时（docs/p7/03 §4.3：对齐 extract 的 call_timeout 模式；超时=降级 null）。
_MATCH_CALL_TIMEOUT_S = 15.0

# 模型可能用字面量表示「无命中」——统一按 None 处理。
_NULL_SLUG_LITERALS = {"", "null", "none"}


@dataclass(frozen=True)
class TaskSkillMatch:
    """匹配结果（``slug=None`` 即未命中；``downgraded`` 标记 low-confidence 降档）。"""

    slug: str | None
    confidence: str | None
    reason: str
    downgraded: bool = False


def build_task_skill_text(slug: str, card_text: str) -> str:
    """Compose the injected ``[Task Skill]`` content: header statement + card body."""
    header = _TASK_SKILL_HEADER_TEMPLATE.format(slug=slug)
    if not card_text:
        return header
    return f"{header}\n\n{card_text}"


async def match_task_skill(
    task_text: str,
    catalog: list[TaskCardMeta],
    llm,
    *,
    call_timeout_s: float = _MATCH_CALL_TIMEOUT_S,
) -> TaskSkillMatch:
    """Match ``task_text`` against ``catalog`` via one conservative LLM call.

    任何失败（API 异常重试一次后仍失败 / 超时 / 输出不可解析 / slug 不在 catalog）
    都返回 ``slug=None``——未命中的代价只是回落探索，不得阻断 agent。
    """
    known_slugs = {c.slug for c in catalog}
    prompt = _MATCH_PROMPT_TEMPLATE.format(
        task=task_text.strip(),
        catalog="\n".join(c.catalog_line() for c in catalog),
    )
    result = None
    for attempt in (1, 2):  # API 失败一次重试（docs/p7/03 §4.3）
        try:
            result = await llm.structured_call(
                system_prompt=_MATCH_SYSTEM_PROMPT,
                user_prompt=prompt,
                output_schema=_MATCH_OUTPUT_SCHEMA,
                call_timeout=call_timeout_s,
            )
            break
        except Exception as e:
            logger.warning("task-skill match call failed (attempt %d): %s", attempt, e)
            if attempt == 2:
                return TaskSkillMatch(slug=None, confidence=None, reason=f"call failed: {e}")
    if not isinstance(result, dict):
        return TaskSkillMatch(slug=None, confidence=None, reason="unparseable output")

    raw_slug = result.get("match")
    slug = str(raw_slug).strip() if raw_slug else ""
    confidence = str(result.get("confidence") or "").strip().lower() or None
    reason = str(result.get("reason") or "").strip()
    if slug.lower() in _NULL_SLUG_LITERALS:
        return TaskSkillMatch(slug=None, confidence=confidence, reason=reason)
    if slug not in known_slugs:
        logger.warning(
            "task-skill: matched slug %r not in catalog — treating as no match", slug
        )
        return TaskSkillMatch(slug=None, confidence=confidence, reason=f"unknown slug: {slug}")
    if confidence == "low":
        return TaskSkillMatch(slug=None, confidence=confidence, reason=reason, downgraded=True)
    return TaskSkillMatch(slug=slug, confidence=confidence, reason=reason)
