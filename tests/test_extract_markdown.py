"""Tests for tools.extract_markdown: clean markdown + structure-aware chunking."""
from __future__ import annotations

from tree_walker.tools.extract_markdown import (
	MarkdownChunk,
	chunk_markdown_by_structure,
	extract_clean_markdown,
)


class TestExtractCleanMarkdown:
	def test_empty(self):
		assert extract_clean_markdown("") == ""
		assert extract_clean_markdown("   ") == ""

	def test_basic_html_to_markdown(self):
		md = extract_clean_markdown("<h1>Title</h1><p>Hello world</p>")
		assert "Title" in md
		assert "Hello world" in md

	def test_extract_links_true_keeps_url(self):
		md = extract_clean_markdown('<p>Hi <a href="http://x.example">click</a></p>', extract_links=True)
		assert "click" in md
		assert "http://x.example" in md

	def test_extract_links_false_strips_url_keeps_text(self):
		md = extract_clean_markdown('<p>Hi <a href="http://x.example">click</a></p>', extract_links=False)
		assert "click" in md
		assert "http://x.example" not in md

	def test_extract_images_true_keeps_src(self):
		md = extract_clean_markdown('<img src="pic.png" alt="alt">', extract_images=True)
		assert "pic.png" in md

	def test_extract_images_false_strips_src(self):
		md = extract_clean_markdown('<img src="pic.png" alt="alt">', extract_images=False)
		assert "pic.png" not in md

	def test_collapses_excess_whitespace(self):
		md = extract_clean_markdown("<p>a</p><p>b</p><p>c</p>")
		assert "\n\n\n" not in md


class TestChunkMarkdownByStructure:
	def test_empty_returns_empty(self):
		assert chunk_markdown_by_structure("") == []

	def test_small_input_single_chunk(self):
		md = "# H1\nbody text"
		chunks = chunk_markdown_by_structure(md, max_chars=1000)
		assert len(chunks) == 1
		assert isinstance(chunks[0], MarkdownChunk)
		assert chunks[0].start_index == 0
		assert chunks[0].end_index == len(md)
		assert chunks[0].content == md

	def test_offsets_contiguous_and_cover_all(self):
		md = "# A\naa aa aa\n\n# B\nbb bb bb\n\n# C\ncc " + "x" * 60
		chunks = chunk_markdown_by_structure(md, max_chars=20)
		assert chunks[0].start_index == 0
		assert chunks[-1].end_index == len(md)
		for a, b in zip(chunks, chunks[1:]):
			assert a.end_index == b.start_index  # contiguous
		for c in chunks:
			assert c.end_index - c.start_index <= 20  # within budget

	def test_over_budget_splits_into_multiple(self):
		md = "# A\n" + "a" * 15 + "\n# B\n" + "b" * 15 + "\n# C\n" + "c" * 15
		chunks = chunk_markdown_by_structure(md, max_chars=20)
		assert len(chunks) > 1

	def test_long_line_hard_split_by_chars(self):
		md = "x" * 100  # single long line, no newlines
		chunks = chunk_markdown_by_structure(md, max_chars=30)
		assert len(chunks) >= 3
		assert chunks[0].start_index == 0
		assert chunks[-1].end_index == 100
		# reassembling the char ranges reproduces the original
		reassembled = "".join(md[c.start_index:c.end_index] for c in chunks)
		assert reassembled == md

	def test_table_header_continuation_across_chunks(self):
		header = "| Name | Price |\n|---|---|\n"
		md = header + "| a | 1 |\n" * 9
		chunks = chunk_markdown_by_structure(md, max_chars=30)
		assert len(chunks) >= 3
		# first chunk is the header itself
		assert chunks[0].content.startswith("| Name | Price |")
		# every subsequent table chunk repeats the header (continuation carries forward)
		for c in chunks[1:]:
			assert c.content.startswith("| Name | Price |"), repr(c.content[:50])

	def test_table_header_cleared_after_table_ends(self):
		header = "| H |\n|---|\n"
		rows = "| 1 |\n" * 3
		after = "plain paragraph with no pipes here at all\n"
		md = header + rows + after
		chunks = chunk_markdown_by_structure(md, max_chars=20)
		# the trailing non-table chunk must NOT carry a synthetic header
		assert not chunks[-1].content.startswith("| H |")
		assert chunks[-1].end_index == len(md)

	def test_giant_line_after_short_content_not_orphaned(self):
		# markdownify 把表格密集页（如 HN）压成单行时，前面那点 nav/header 不能被
		# 单独切成无内容空壳块（否则 extract 第一块 start_from_char=0 只拿到 nav）。issue #86。
		nav = "# Nav\nlink link link\n"
		giant = "STORY " * 4000  # ~24K 单行
		md = nav + giant
		chunks = chunk_markdown_by_structure(md, max_chars=8000)
		assert chunks[0].end_index > len(nav), "chunk0 被孤岛成 nav 空壳"
		assert "STORY" in chunks[0].content

	def test_orphan_merge_preserves_contiguity_and_coverage(self):
		# 反孤岛并入后仍须连续覆盖全文（可重组还原原文）
		nav = "nav\n"
		giant = "x" * 20000
		md = nav + giant
		chunks = chunk_markdown_by_structure(md, max_chars=8000)
		assert chunks[0].start_index == 0
		assert chunks[-1].end_index == len(md)
		for a, b in zip(chunks, chunks[1:]):
			assert a.end_index == b.start_index  # contiguous
		reassembled = "".join(md[c.start_index:c.end_index] for c in chunks)
		assert reassembled == md

	def test_hn_like_short_nav_plus_giant_line(self):
		# 复刻真实 HN：极短 nav + 单条 10K+ 内容行；第一块必须含正文故事
		nav = "Hacker News\n| new | past | comments |\n"
		stories = "".join(f"[Story {i}]({i} points) " for i in range(1, 1001))  # ~25K 单行
		md = nav + stories
		chunks = chunk_markdown_by_structure(md, max_chars=8000)
		assert "Story 1" in chunks[0].content
		assert "Story 2" in chunks[0].content
