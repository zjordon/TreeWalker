"""HTML → 干净 markdown + 结构感知分块（供 extract 工具）。

纯函数（无 I/O、无 CDP），可直接单测。``extract_clean_markdown`` 走
``markdownify``；``chunk_markdown_by_structure`` 按行贪心打包（天然尊重表格行 /
段落 / 标题边界），支持表头延续，供 ``start_from_char`` 分页。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from markdownify import markdownify as _md

# markdownify 前要剥的结构噪声标签（script/style 等已在 html_source 剥除）。
_NOISE_STRIP = ["nav", "footer", "header", "form"]
# 折叠 3+ 连续换行为 2 个。
_WS_RE = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class MarkdownChunk:
    """单个 markdown 分块。

    ``start_index`` / ``end_index`` 索引**原始 markdown**；``content`` 可能因合成的
    表头延续而略长于 ``end_index - start_index``。
    """

    content: str
    start_index: int  # 在原始 markdown 中的偏移（inclusive）
    end_index: int    # exclusive


def extract_clean_markdown(
    html: str,
    *,
    extract_links: bool = True,
    extract_images: bool = True,
) -> str:
    """HTML → 干净 markdown。

    - link/image 门控在 markdownify **之前**用正则去标签（比清洗 markdown 更便宜、更稳）；
    - ``markdownify(heading_style="ATX", strip=_NOISE_STRIP)``；
    - 折叠多余空行、strip 首尾。
    - 空输入返回 ``""``。
    """
    if not html or not html.strip():
        return ""
    if not extract_links:
        # 去掉 <a> 的 href 与标签本身，保留链接文本
        html = re.sub(r"<a\b[^>]*>(.*?)</a>", r"\1", html, flags=re.I | re.S)
        html = re.sub(r"<a\b[^>]*/?>", "", html, flags=re.I)
    if not extract_images:
        html = re.sub(r"<img\b[^>]*>", "", html, flags=re.I)
    md = _md(html, heading_style="ATX", strip=_NOISE_STRIP)
    return _WS_RE.sub("\n\n", md).strip()


# ── 分块 ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Unit:
    start: int
    end: int
    text: str


def _line_offsets(lines: list[str]) -> list[int]:
    """``offs[k]`` = 第 k 行在原文中的起始偏移；``offs[-1]`` = 全文长度。"""
    offs = [0] * (len(lines) + 1)
    for i, ln in enumerate(lines):
        offs[i + 1] = offs[i] + len(ln)
    return offs


def _is_table_row(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.endswith("|") and s.count("|") >= 2


def _is_table_sep(line: str) -> bool:
    s = line.strip()
    if not (s.startswith("|") and s.endswith("|")):
        return False
    # 多列表格的分隔行 ``|---|---|`` 去掉首尾 ``|`` 后仍含列分隔 ``|``，故按列切分逐格校验。
    cells = s.strip("|").split("|")
    if not cells:
        return False
    for cell in cells:
        c = cell.strip()
        if not c or not all(ch in "-: " for ch in c) or "-" not in c:
            return False
    return True


def _build_units(md: str, max_chars: int) -> list[_Unit]:
    """按行切 unit（每行一个；超长行按 max_chars 硬切）。

    行级粒度让打包天然尊重表格行 / 段落 / 标题边界。返回有序、连续、覆盖全文的 unit 列表。
    """
    lines = md.splitlines(keepends=True)
    if not lines:
        return []
    offsets = _line_offsets(lines)
    units: list[_Unit] = []
    for i, ln in enumerate(lines):
        start = offsets[i]
        end = offsets[i + 1]
        if end - start <= max_chars:
            units.append(_Unit(start, end, ln))
        else:
            s = start
            while s < end:
                e = min(s + max_chars, end)
                units.append(_Unit(s, e, md[s:e]))
                s = e
    return units


def _pack_units(units: list[_Unit], max_chars: int) -> list[tuple[int, int, str]]:
    """贪心把相邻 unit 装进同一块，直到再加下一块会超 max_chars。

    反孤岛：当前块过小（``< max_chars/4``）时，即使略超预算也把下一个 unit 并入，
    避免 markdownify 把整页压成单行（如 HN 的 10K+ 行）时，前面那点 nav/header 被单独
    切成无用小块——会让 extract 的第一块（``start_from_char=0``）只拿到 nav 空壳（issue #86）。
    并入后该块 ``end_index - start_index`` 可能略超 max_chars（≤ 1.25×）。
    """
    if not units:
        return []
    min_chunk = max(max_chars // 4, 1)
    chunks: list[tuple[int, int, str]] = []
    cur_start = units[0].start
    cur_end = units[0].end
    cur_text = units[0].text
    for u in units[1:]:
        cur_size = cur_end - cur_start
        if cur_size + (u.end - u.start) <= max_chars:
            cur_end = u.end
            cur_text += u.text
        elif cur_size < min_chunk:
            # 当前块过小（孤岛）→ 并入下一块（允许略超 max_chars），避免无内容空壳块
            cur_end = u.end
            cur_text += u.text
        else:
            chunks.append((cur_start, cur_end, cur_text))
            cur_start, cur_end, cur_text = u.start, u.end, u.text
    chunks.append((cur_start, cur_end, cur_text))
    return chunks


def _last_table_header(text: str) -> str:
    """返回 text 中最后一个 ``| header |\\n| --- |\\n`` 表头对，找不到返回 ``""``。"""
    lines = [ln.rstrip("\n") for ln in text.splitlines()]
    last = ""
    for i in range(len(lines) - 1):
        if _is_table_row(lines[i]) and _is_table_sep(lines[i + 1]):
            last = lines[i] + "\n" + lines[i + 1] + "\n"
    return last


def _starts_with_table_row_no_header(text: str) -> bool:
    """text 以表格数据行开头、但开头不是「表头 + 分隔行」。"""
    lines = [ln.rstrip("\n") for ln in text.splitlines()]
    if not lines or not _is_table_row(lines[0]):
        return False
    if len(lines) >= 2 and _is_table_sep(lines[1]):
        return False  # 本身就是表头
    return True


def _apply_table_continuation(
    raw_chunks: list[tuple[int, int, str]],
) -> list[MarkdownChunk]:
    """表头延续：维护一个「当前表头」，当某块以数据行开头（无表头）时，把当前表头合成到
    其 ``content`` 顶部（``start_index`` 不变）。表头随每个含表头的块刷新，随表格结束清空。"""
    if not raw_chunks:
        return []
    result: list[MarkdownChunk] = []
    current_header = ""
    for start, end, text in raw_chunks:
        content = text
        own_header = _last_table_header(text)
        if own_header:
            current_header = own_header
        if current_header and not own_header and _starts_with_table_row_no_header(text):
            content = current_header + content
        # 块首非表格行 → 表格已结束，清空当前表头
        first_lines = [ln.rstrip("\n") for ln in text.splitlines()]
        if not first_lines or not _is_table_row(first_lines[0]):
            current_header = ""
        result.append(MarkdownChunk(content=content, start_index=start, end_index=end))
    return result


def chunk_markdown_by_structure(md: str, *, max_chars: int = 8000) -> list[MarkdownChunk]:
    """按结构分块，每块 ``end_index - start_index`` ≤ max_chars（``content`` 可能因合成表头略长）。

    策略：行级 unit → 贪心打包 → 表头延续。返回 ``start_index`` / ``end_index`` 单调递增、
    连续、覆盖全 md 的块；``md`` ≤ max_chars 时返回单个块；``md`` 为空返回 ``[]``。

    反孤岛例外：当某块过小（``< max_chars/4``）且其后是大块时，会把它并入下一块，此时该块
    ``end_index - start_index`` 可能略超 max_chars（≤ 1.25×）。避免 markdownify 把整页压成单行
    （如 HN 的 10K+ 行）时，开头那点 nav/header 被切成无内容空壳块（issue #86）。
    """
    if not md:
        return []
    total = len(md)
    if total <= max_chars:
        return [MarkdownChunk(md, 0, total)]
    units = _build_units(md, max_chars)
    raw_chunks = _pack_units(units, max_chars)
    return _apply_table_continuation(raw_chunks)
