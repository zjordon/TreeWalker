"""LLM-as-ranker matcher for task-level skill cards (docs/p7/03 §4; docs/p7/04 §四).

模板语义匹配（issue #182，docs/p7/04 §二）：匹配单元 = 操作模板。实体值（商品名/
日期/年份/名次/数值）是参数，永不构成拒绝理由；模板差异（动作动词/对象类型/参数
维度/输出形态）才是不命中判据。保守度沿 v2 不变：拿不准返回 null，且 ``confidence``
非 high/medium 在解析侧强制降档为未命中。输出经 ``LLMClient.structured_call`` 工具
强制返回；``match_kind``（本尊/同模板换实体）与 ``task_kind``（读型/操作型）驱动
注入头三档分级（docs/p7/04 §4.3）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from tree_walker.skills.task_loader import TaskCardMeta

logger = logging.getLogger(__name__)

__all__ = ["TaskSkillMatch", "match_task_skill", "build_task_skill_text"]

_MATCH_SYSTEM_PROMPT = (
    "You are a task-matching judge. You compare a user task against a catalog of "
    "recorded task skills and decide which recorded task follows the same operation "
    "template. Entity and slot values (names, dates, amounts, fields, types, direction) "
    "are irrelevant; operation family, object family, or filter dimension differences "
    "reject. Be conservative: a wrong match is worse than no match."
)

_MATCH_PROMPT_TEMPLATE = """You are a task-matching judge. Given a user task and a catalog of recorded task skills,
decide which recorded task follows the SAME OPERATION TEMPLATE as the user task.

Judge on two axes:
- ENTITY values are parameters, never matching criteria. Product names, customer names,
  dates, years, ranks, numbers, and statuses differ between instances of the same
  template. They NEVER justify rejection: "disable product X" matches a card that
  disables product Y.
- TEMPLATE identity is the criterion — all three must match:
  (a) operation family: count vs list vs read-one-field vs edit vs create vs delete vs
      generate-report vs notify;
  (b) object family: orders vs products vs customers vs reviews vs reports;
  (c) filter/sort dimension when one is applied: by status vs by keyword vs by date
      range vs by quantity — or no filter at all.
  Any of these differing = different template = null.
- SLOT values inside the same pattern are parameters too, NOT template identity:
  which field to read (customer name vs order ID vs date), which report type to
  generate (orders vs coupons vs shipping), which option type to add (size vs color),
  which direction an edit goes (increase vs reduce), which reviews a delete targets
  (all pending negative reviews of product X vs all reviews from reviewer Y), which
  quantifier picks the row (most, second-most, exactly 2), which extreme to pick (most
  recent vs oldest, top-1 vs top-5), amounts, dates, keywords. Substituting any slot
  value keeps the same template: "create a coupons report for May" matches a card that
  creates an orders report for another date range; "get the order ID of the newest
  pending order" matches a card that gets the customer name of the newest cancelled
  order.
- Dimension vs value: "quantity = 0" vs "quantity = 3" is the same template;
  "filter by quantity" vs "filter by price" is not. "top-1 in 2023" vs "top-5 in 2024"
  is the same template. But "count ALL reviews" vs "count reviews that MENTION a term"
  is NOT the same template — an added filter dimension (none vs keyword) changes the
  template.
- A composite task chaining two lookups that no single card performs is null — do not
  match a card covering only half of it.

Also classify the match: same_task = same template AND same entity values;
same_template = same template with different entity or slot values. And classify the
user task: read = asks to look up / compute / report a fact; operate = asks to change
site state.

Rules:
- Surface wording may differ (synonyms, language).
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
            "description": "Matched card slug, or null when no card shares the task's operation template.",
        },
        "match_kind": {
            "type": ["string", "null"],
            "description": (
                "same_task = same template AND same entity values; "
                "same_template = same template with different entity values; null when no match."
            ),
        },
        "task_kind": {
            "type": ["string", "null"],
            "description": "Whether the user task reads a fact from the site (read) or changes site state (operate).",
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

# 命中卡注入头·本尊档（docs/p7/03 附录 B 原文；末句 = 易变值声明，针对答案固化实测）。
_TASK_SKILL_HEADER_SAME_TASK = (
    "A recorded task matching your current goal was found (slug: {slug}). It describes a\n"
    "PROVEN flow for essentially this task — follow it as guidance. The live page is the\n"
    "source of truth: if any step no longer matches reality, adapt and explore on your own.\n"
    "Concrete values in this card (counts, amounts, dates, names) are snapshots from the\n"
    "recording session — always re-read the current value from the page."
)

# 命中卡注入头·同模板换实体档（docs/p7/04 §4.3）：卡为同一任务类型的另一实例录制。
# 实体替换警示比 v2 易变值声明句更强（本档下所有具体值都错，不止易变值）。
_TASK_SKILL_HEADER_SAME_TEMPLATE = (
    "A recorded task following the SAME operation template as your current goal was found\n"
    "(slug: {slug}). It was recorded for a DIFFERENT instance of this task type: every\n"
    "concrete value in it (product names, dates, counts, amounts) belongs to that instance\n"
    "and is wrong for your task. Follow its step sequence and navigation path as guidance,\n"
    "substituting your task's own entities. The live page is the source of truth: if any\n"
    "step no longer matches reality, adapt and explore on your own."
)

# 读型加严段（docs/p7/04 §4.3 第三档）：无论本尊/变体都追加——答案固化对回放
# parrot 风险最大，本尊读型更需要这句。
_TASK_SKILL_READ_APPENDIX = (
    "Your task asks you to READ a fact from the site: this card shows the PATH to that\n"
    "fact, never the fact itself. Do not report any value taken from this card — compute\n"
    "the answer from the live page."
)

# 匹配调用单次超时（docs/p7/03 §4.3：对齐 extract 的 call_timeout 模式；超时=降级 null）。
_MATCH_CALL_TIMEOUT_S = 15.0

# 模型可能用字面量表示「无命中」——统一按 None 处理。
_NULL_SLUG_LITERALS = {"", "null", "none"}


@dataclass(frozen=True)
class TaskSkillMatch:
    """匹配结果（``slug=None`` 即未命中；``downgraded`` 标记 low-confidence 降档）。

    ``match_kind``：``same_task``（同模板同实体=本尊）/ ``same_template``（同模板
    换实体=变体），驱动注入头分级；``task_kind``：``read`` / ``operate`` / None，
    读型触发答案现取加严段。两者保守缺省（same_task / None）——缺失或乱值归一化
    后与 v2 单档行为一致（docs/p7/04 §4.2）。
    """

    slug: str | None
    confidence: str | None
    reason: str
    downgraded: bool = False
    match_kind: str = "same_task"
    task_kind: str | None = None


def build_task_skill_text(
    slug: str,
    card_text: str,
    *,
    match_kind: str = "same_task",
    task_kind: str | None = None,
) -> str:
    """Compose the injected ``[Task Skill]`` content: tiered header + card body.

    三档（docs/p7/04 §4.3）：本尊（``same_task``）沿用 v2 头；同模板换实体
    （``same_template``）用实体替换警示头；``task_kind == "read"`` 无论哪档都
    追加读型加严段（答案必须页面现取，禁止报告卡内值）。
    """
    if match_kind == "same_template":
        header = _TASK_SKILL_HEADER_SAME_TEMPLATE.format(slug=slug)
    else:
        header = _TASK_SKILL_HEADER_SAME_TASK.format(slug=slug)
    if task_kind == "read":
        header = f"{header}\n{_TASK_SKILL_READ_APPENDIX}"
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
    # 分级字段归一化（docs/p7/04 §4.2）：白名单 + 保守缺省——match_kind 乱值/缺失
    # 一律回 same_task（分级是增强不是门控，缺省 = v2 单档行为，回放路径零扰动）；
    # task_kind 乱值 → None（不触发读型加严段）。
    match_kind = str(result.get("match_kind") or "").strip().lower()
    if match_kind not in ("same_task", "same_template"):
        match_kind = "same_task"
    task_kind = str(result.get("task_kind") or "").strip().lower()
    if task_kind not in ("read", "operate"):
        task_kind = None
    if slug.lower() in _NULL_SLUG_LITERALS:
        return TaskSkillMatch(slug=None, confidence=confidence, reason=reason)
    if slug not in known_slugs:
        logger.warning(
            "task-skill: matched slug %r not in catalog — treating as no match", slug
        )
        return TaskSkillMatch(slug=None, confidence=confidence, reason=f"unknown slug: {slug}")
    if confidence not in ("high", "medium"):
        # 白名单而非黑名单：非 high/medium（含 "low"、缺失、数字等 schema 外值——
        # text 兜底路径不做 schema 校验）一律降档为未命中，防绕过降档守卫。
        return TaskSkillMatch(slug=None, confidence=confidence, reason=reason, downgraded=True)
    return TaskSkillMatch(
        slug=slug,
        confidence=confidence,
        reason=reason,
        match_kind=match_kind,
        task_kind=task_kind,
    )
