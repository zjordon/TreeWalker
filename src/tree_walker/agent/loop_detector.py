"""Soft loop detection — nudges the LLM when repeated actions are detected.

Two detection dimensions (aligned to browser-use's ``ActionLoopDetector``):
  1. Action repetition — per-action-type semantic hash in a sliding window.
  2. Page stagnation   — 3-dim page fingerprint (url + element_count + dom text hash).

Soft only: never blocks actions. Returns a nudge string that gets injected into
the LLM context for the next step.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from dataclasses import dataclass


def _normalize_action_for_hash(name: str, params: dict) -> str:
    """Normalize action params for similarity hashing.

    Adapted to TreeWalker's action vocabulary (NOT a copy of browser-use's
    action names/params). Same logical action → same normalized string:
      - search     : keyword order / case / punctuation agnostic (sorted token set)
      - click      : by element identity (``index``, falling back to ``element_id``)
      - input_text : by element identity + normalized text (different text ⇒ different action)
      - navigate   : by ``url`` (``new_tab`` ignored — same URL still signals a loop)
      - scroll     : by ``direction`` (``amount`` ignored)
      - <default>  : action name + sorted non-None params

    Known limitations (documented in docs/loop-detector-optimize/01-...md §4.1.6):
      - click: ``index`` and ``element_id`` are treated interchangeably; if the LLM
        alternates between them for the same element, the hashes may differ.
    """

    def _element_id() -> str:
        idx = params.get("index")
        return str(idx if idx is not None else params.get("element_id"))

    if name == "search":
        query = str(params.get("query", ""))
        tokens = sorted(set(re.sub(r"[^\w\s]", " ", query.lower()).split()))
        engine = params.get("engine", "baidu")
        return f"search|{engine}|{'|'.join(tokens)}"
    if name == "click":
        return f"click|{_element_id()}"
    if name == "input_text":
        text = str(params.get("text", "")).strip().lower()
        return f"input_text|{_element_id()}|{text}"
    if name == "navigate":
        return f"navigate|{params.get('url', '')}"
    if name == "scroll":
        return f"scroll|{params.get('direction', 'down')}"
    # Default: action name + sorted non-None params
    filtered = {k: v for k, v in sorted(params.items()) if v is not None}
    return f"{name}|{json.dumps(filtered, sort_keys=True, default=str)}"


def compute_action_hash(name: str, params: dict) -> str:
    """Stable 12-char hash (sha256[:12], 48-bit) — mirrors browser-use ``compute_action_hash``."""
    normalized = _normalize_action_for_hash(name, params)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class PageFingerprint:
    """Lightweight fingerprint of a page state: ``url`` + ``element_count`` + dom text hash."""

    url: str
    element_count: int
    text_hash: str  # first 16 chars of sha256 of the DOM text representation

    @staticmethod
    def from_state(url: str, dom_text: str, element_count: int) -> PageFingerprint:
        text_hash = hashlib.sha256(dom_text.encode("utf-8", errors="replace")).hexdigest()[:16]
        return PageFingerprint(url=url, element_count=element_count, text_hash=text_hash)


class ActionLoopDetector:
    """Tracks action repetition and page stagnation to detect stuck loops.

    Does not block actions. Instead, returns nudge messages that get injected
    into the LLM context for the next step.
    """

    def __init__(self, window_size: int = 20) -> None:
        self.recent_actions: deque[str] = deque(maxlen=window_size)
        self.recent_page_fingerprints: deque[PageFingerprint] = deque(maxlen=5)
        self.max_repetition_count: int = 0
        self.most_repeated_hash: str | None = None
        self.consecutive_stagnant_pages: int = 0

    def record_action(self, name: str, params: dict) -> None:
        """Record an action (already filtered for exempt actions) and update repetition stats."""
        self.recent_actions.append(compute_action_hash(name, params))
        self._update_repetition_stats()

    def record_page_state(self, url: str, dom_text: str, element_count: int) -> None:
        """Record the current page fingerprint and update the stagnation counter."""
        fp = PageFingerprint.from_state(url, dom_text, element_count)
        if self.recent_page_fingerprints and self.recent_page_fingerprints[-1] == fp:
            self.consecutive_stagnant_pages += 1
        else:
            self.consecutive_stagnant_pages = 0
        self.recent_page_fingerprints.append(fp)

    def _update_repetition_stats(self) -> None:
        """Recompute ``max_repetition_count`` / ``most_repeated_hash`` from the current window."""
        if not self.recent_actions:
            self.max_repetition_count = 0
            self.most_repeated_hash = None
            return
        counts: dict[str, int] = {}
        for h in self.recent_actions:
            counts[h] = counts.get(h, 0) + 1
        self.most_repeated_hash = max(counts, key=lambda g: counts[g])
        self.max_repetition_count = counts[self.most_repeated_hash]

    def get_nudge_message(self) -> str | None:
        """Return an escalating nudge from action repetition and/or page stagnation, or None."""
        # min-3 guard (more conservative than browser-use; harmless since the >=5 threshold dominates)
        if len(self.recent_actions) < 3 and self.consecutive_stagnant_pages < 5:
            return None

        messages: list[str] = []
        n = len(self.recent_actions)
        if self.max_repetition_count >= 12:
            messages.append(
                f"Heads up: you have repeated a similar action {self.max_repetition_count} times "
                f"in the last {n} actions. "
                "If you are making progress with each repetition, keep going. "
                "If not, a different approach might get you there faster."
            )
        elif self.max_repetition_count >= 8:
            messages.append(
                f"Heads up: you have repeated a similar action {self.max_repetition_count} times "
                f"in the last {n} actions. "
                "Are you still making progress with each attempt? "
                "If so, carry on. Otherwise, it might be worth trying a different approach."
            )
        elif self.max_repetition_count >= 5:
            messages.append(
                f"Heads up: you have repeated a similar action {self.max_repetition_count} times "
                f"in the last {n} actions. "
                "If this is intentional and making progress, carry on. "
                "If not, it might be worth reconsidering your approach."
            )

        if self.consecutive_stagnant_pages >= 5:
            messages.append(
                f"The page content has not changed across {self.consecutive_stagnant_pages} consecutive actions. "
                "Your actions might not be having the intended effect. "
                "It could be worth trying a different element or approach."
            )

        if messages:
            return "\n\n".join(messages)
        return None


class FailureStreakTracker:
    """issue #186 现象①：同动作跨步连败跟踪——失败感知的止损 nudge 源。

    与 ``ActionLoopDetector`` 互补而非替代：后者成功无关（repetition 阈值 ≥5
    是为容忍翻页类正当重复），这里只看失败——失败是强得多的信号，阈值可以很低
    （2）。task_374 形态（4 次相同参数 screenshot 失败 + 原地发明 document.write
    workaround）两层既有机制全空转：``consecutive_failures`` 多动作步失败不计且
    被重置，loop detector 4 < 5 且页面指纹在变。

    语义：per-action-name 独立计数，该动作成功即清零（含通知状态）；
    ``done`` 豁免（失败自进澄清梯）。通知去抖：每档只报一次——streak 2 首报、
    3 不重报、4 升级报、5+ 不重报；streak 冻结（agent 转做别的）时不重复注入。
    """

    NUDGE_AT = 2
    ESCALATE_AT = 4
    _EXEMPT = frozenset({"done"})

    def __init__(self) -> None:
        self._streaks: dict[str, int] = {}
        self._notified_at: dict[str, int] = {}

    def record(self, name: str, failed: bool) -> None:
        """Record one executed action's outcome; success clears that action's streak."""
        if name in self._EXEMPT:
            return
        if failed:
            self._streaks[name] = self._streaks.get(name, 0) + 1
        else:
            self._streaks.pop(name, None)
            self._notified_at.pop(name, None)

    def peek_nudge(self) -> tuple[str, int, str] | None:
        """（只读）返回当前应注入的止损候选 ``(name, streak, message)`` 或 None。

        review2 #5：查询不落档——_prepare_context 构建状态消息时 peek 只暂存，
        LLM 响应确实取得后由调用方 ``ack_nudge`` 提交；否则 LLM 调用失败/超时
        （输出未送达模型）时首报会被去抖永久吞掉，tier-2 止损在该动作上静默
        丢失。遍历所有达阈动作（streak 降序）取第一个通过自身去抖判定的——
        只看 max 会把新达阈值动作的首报饿死在抑制档动作后面（review #1）。
        """
        candidates = sorted(
            ((name, s) for name, s in self._streaks.items() if s >= self.NUDGE_AT),
            key=lambda kv: -kv[1],
        )
        for name, streak in candidates:
            notified = self._notified_at.get(name, 0)
            # 去抖：每档只报一次——2 首报（notified<2），3 不重报（notified>=2 且
            # streak<4），4 升级报（notified<4 且 streak>=4），5+ 不重报（notified>=4）
            if notified >= self.ESCALATE_AT:
                continue
            if notified >= self.NUDGE_AT and streak < self.ESCALATE_AT:
                continue
            if streak >= self.ESCALATE_AT:
                return (name, streak, (
                    f"⚠️ You have failed '{name}' {streak} times in a row. "
                    "Strongly consider declaring this sub-goal unreachable: complete "
                    "or verify the task's actual deliverable, or finish with an honest "
                    "partial result (done with success=false, describing what was "
                    "accomplished and what is missing)."
                ))
            return (name, streak, (
                f"⚠️ You have failed '{name}' {streak} times in a row. Stop retrying "
                "or inventing workarounds for this approach. Re-read the original "
                "task and switch to a different approach that directly advances the "
                "task's final goal — also ask whether the failing sub-goal is "
                "required by the task at all."
            ))
        return None

    def ack_nudge(self, name: str, streak: int) -> None:
        """提交档位（与 ``peek_nudge`` 配对的写侧）——状态消息确认进入本轮
        对话（LLM 响应取得）后调用。"""
        self._notified_at[name] = streak

    def nudge(self) -> str | None:
        """peek + ack 的便捷组合（单测与简单场景用；step 侧用 peek/ack 分离版）。"""
        candidate = self.peek_nudge()
        if candidate is None:
            return None
        name, streak, message = candidate
        self.ack_nudge(name, streak)
        return message


class ZeroResultStreakTracker:
    """issue #186-c2 形态②：同一精确查询连续零结果跟踪——检索降级 nudge 源。

    与 ``FailureStreakTracker`` 同构但信号相反：那边看工具失败，这里看工具
    「成功但查询空手而归」（C 轮 544：任务名 Selena vs 目录 Selene 拼写不一致，
    精确过滤 0 结果 ×N 不换策略，25/29 步耗尽）。零结果是 soft-miss（正确的
    工具语义），不进 ``consecutive_failures``/``FailureStreakTracker``。

    信号经 ``ActionResult.metadata['query_total']`` 结构化旁路（三个查询类
    动作的成功路径设置；``__str__`` 不渲染 metadata，零 token 成本）。查询
    身份 = 动作名 + 归一化查询键（read_grid: namespace+search+filters；
    find_elements: selector；search_page: query）——改查询即重置。
    通知去抖与 peek/ack 语义同 ``FailureStreakTracker``（查询不消费：
    LLM 失败步不 ack，下步重发）。
    """

    NUDGE_AT = 2

    def __init__(self) -> None:
        # key -> streak 计数；key 形如 "read_grid|ns=sales_order_grid|search=WH12"
        self._streaks: dict[str, int] = {}
        # key -> 是否已通知（去抖：同键只报一次，非零结果重置）
        self._notified: set[str] = set()
        # key -> query_desc（文案引用）
        self._descs: dict[str, str] = {}

    @staticmethod
    def _query_key(name: str, params: dict) -> tuple[str, str] | None:
        """归一化查询身份——返回 (key, query_desc) 或 None（非查询类动作）。

        review7 #3：按值真值判定部件是否参与键（旧 ``endswith("=")`` 后缀启发
        在值以 = 结尾时误丢部件，使不同查询坍缩同键）；review7 #10：json 用
        模块顶导入（本模块已有）。
        """
        if name == "read_grid":
            ns = params.get("namespace") or ""
            search = params.get("search") or ""
            filters = params.get("filters") or {}
            parts = []
            if ns:
                parts.append(f"ns={ns}")
            if search:
                parts.append(f"search={search}")
            if filters:
                try:
                    parts.append(f"filters={json.dumps(filters, sort_keys=True, ensure_ascii=False)}")
                except (TypeError, ValueError):
                    parts.append(f"filters={sorted(filters.items(), key=str)}")
            key = f"read_grid|{'|'.join(parts) or 'default'}"
            desc_bits = []
            if filters:
                desc_bits.append(f"filters={filters}")
            if search:
                desc_bits.append(f"search='{search}'")
            return key, "read_grid " + (" ".join(desc_bits) or "(unfiltered)")
        if name == "find_elements":
            selector = str(params.get("selector") or "")
            if not selector:
                return None
            return f"find_elements|{selector}", f"selector '{selector}'"
        if name == "search_page":
            query = str(params.get("query") or "")
            if not query:
                return None
            return f"search_page|{query}", f"query '{query}'"
        return None

    def record(self, name: str, params: dict, result) -> None:
        """从 ActionResult.metadata 读 query_total 并更新 streak（无信号直接返回）。"""
        metadata = getattr(result, "metadata", None) or {}
        total = metadata.get("query_total")
        if not isinstance(total, int):
            return  # 非查询类动作 / 错误路径（无 query_total）不参与
        ident = self._query_key(name, params or {})
        if ident is None:
            return
        key, desc = ident
        if total > 0:
            self._streaks.pop(key, None)
            self._notified.discard(key)
            self._descs.pop(key, None)
        else:
            self._streaks[key] = self._streaks.get(key, 0) + 1
            self._descs[key] = desc

    def peek_nudge(self) -> tuple[str, str] | None:
        """（只读）返回已达阈值且未通知的 (key, message)；查询不消费——
        ack 前重复 peek 返回同一候选（LLM 失败步不 ack，下步重发）。"""
        for key, streak in self._streaks.items():
            if streak >= self.NUDGE_AT and key not in self._notified:
                desc = self._descs.get(key, key)
                return key, (
                    f"Exact-match query returned 0 results {streak} times in a row "
                    f"({desc}). The name may be misspelled or partially different — "
                    "switch to a substring/partial filter, list candidate rows "
                    "unfiltered, or browse the catalog and match by similarity."
                )
        return None

    def ack_nudge(self, key: str) -> None:
        """提交通知（与 peek 配对）——LLM 响应取得后由 step 侧调用。"""
        self._notified.add(key)

    def nudge(self) -> str | None:
        """peek + ack 便捷组合（单测与简单场景用）。"""
        candidate = self.peek_nudge()
        if candidate is None:
            return None
        key, message = candidate
        self.ack_nudge(key)
        return message
