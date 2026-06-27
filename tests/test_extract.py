"""Tests for extract: _action_extract tool layer, Agent wiring, LLMClient.extract client layer."""
from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anthropic import RateLimitError

from tree_walker.agent.agent import Agent
from tree_walker.config import AgentSettings, FallbackLLMSettings, LLMSettings, TruncationSettings
from tree_walker.llm.client import LLMClient
from tree_walker.tools.actions import Tools


# ── tool-layer helpers ────────────────────────────────────────────────


def _make_mock_browser(
	html: str = "<html><body><p>some page text</p></body></html>",
	*,
	execute_js_return: str | None = None,
	execute_js_exc: Exception | None = None,
) -> MagicMock:
	"""Mock BrowserSession: get_page_html returns html; execute_js is the outerHTML fallback.

	- 默认 execute_js 返回备用 HTML（走降级路径时用）。
	- execute_js_exc 非空 → execute_js 抛异常（模拟降级源也失败）。
	"""
	browser = MagicMock()
	browser.get_page_html = AsyncMock(return_value=html)
	if execute_js_exc is not None:
		browser.execute_js = AsyncMock(side_effect=execute_js_exc)
	else:
		browser.execute_js = AsyncMock(
			return_value=execute_js_return if execute_js_return is not None
			else "<html><body><p>fallback html</p></body></html>"
		)
	return browser


def _make_mock_extract_llm(extract_return: str = "SUMMARY", extract_side_effect: Exception | None = None) -> MagicMock:
	"""Mock _extract_llm with a controllable .extract async method."""
	llm = MagicMock()
	if extract_side_effect is not None:
		llm.extract = AsyncMock(side_effect=extract_side_effect)
	else:
		llm.extract = AsyncMock(return_value=extract_return)
	return llm


def _make_tools(llm: object | None = None, schema: dict | None = None) -> Tools:
	"""Tools with injected _extract_llm / _extraction_schema (mirrors Agent wiring)."""
	tools = Tools()
	tools._extract_llm = llm  # None mirrors a fresh Tools() (getattr → None)
	tools._extraction_schema = schema
	return tools


_SCHEMA = {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}


# ── _action_extract: tool layer ───────────────────────────────────────


class TestActionExtract:
	@pytest.mark.asyncio
	async def test_freetext_path_no_schema(self):
		"""No schema → extract called with output_schema=None, result echoed."""
		llm = _make_mock_extract_llm(extract_return="the summary")
		browser = _make_mock_browser()
		result = await _make_tools(llm=llm, schema=None).execute("extract", {"query": "summarize"}, browser)
		assert result.error is None
		assert result.extracted_content == "the summary"
		llm.extract.assert_awaited_once()
		assert llm.extract.call_args.kwargs["output_schema"] is None

	@pytest.mark.asyncio
	async def test_structured_path_with_schema(self):
		"""Schema injected → extract receives it; result echoed verbatim."""
		llm = _make_mock_extract_llm(extract_return='{"title": "X"}')
		browser = _make_mock_browser()
		tools = _make_tools(llm=llm, schema=_SCHEMA)
		result = await tools.execute("extract", {"query": "get title"}, browser)
		assert result.error is None
		assert result.extracted_content == '{"title": "X"}'
		assert llm.extract.call_args.kwargs["output_schema"] == _SCHEMA

	@pytest.mark.asyncio
	async def test_structured_small_result_returns_pure_json(self):
		"""Schema + single chunk + small result → extracted_content is pure JSON (no hint, no save)."""
		llm = _make_mock_extract_llm(extract_return='{"title": "X"}')
		browser = _make_mock_browser()
		result = await _make_tools(llm=llm, schema=_SCHEMA).execute("extract", {"query": "x"}, browser)
		assert result.error is None
		assert result.extracted_content == '{"title": "X"}'
		assert result.long_term_memory is None

	@pytest.mark.asyncio
	async def test_empty_page_returns_placeholder(self):
		"""Empty HTML source (both get_page_html and fallback) → '(empty page)', extract never called."""
		llm = _make_mock_extract_llm()
		browser = _make_mock_browser(html="", execute_js_return="")
		result = await _make_tools(llm=llm).execute("extract", {"query": "x"}, browser)
		assert result.extracted_content == "(empty page)"
		llm.extract.assert_not_called()

	@pytest.mark.asyncio
	async def test_both_html_sources_fail_warns_and_empty(self, caplog):
		"""get_page_html empty + execute_js raises → logger.warning + '(empty page)'."""
		llm = _make_mock_extract_llm()
		browser = _make_mock_browser(html="", execute_js_exc=RuntimeError("cdp down"))
		with caplog.at_level(logging.WARNING):
			result = await _make_tools(llm=llm).execute("extract", {"query": "x"}, browser)
		assert result.extracted_content == "(empty page)"
		assert "HTML source failed" in caplog.text
		llm.extract.assert_not_called()

	@pytest.mark.asyncio
	async def test_cdp_empty_falls_back_to_execute_js(self):
		"""get_page_html returns '' but execute_js returns HTML → extraction proceeds from fallback."""
		llm = _make_mock_extract_llm(extract_return="ok")
		browser = _make_mock_browser(html="", execute_js_return="<html><body><p>fallback content</p></body></html>")
		result = await _make_tools(llm=llm).execute("extract", {"query": "x"}, browser)
		assert result.error is None
		assert result.extracted_content == "ok"
		llm.extract.assert_awaited_once()

	@pytest.mark.asyncio
	async def test_llm_exception_returns_error(self):
		"""extract raising → ActionResult(error=...), not bubbling to Tools.execute generic catch."""
		llm = _make_mock_extract_llm(extract_side_effect=RuntimeError("llm boom"))
		browser = _make_mock_browser()
		result = await _make_tools(llm=llm).execute("extract", {"query": "x"}, browser)
		assert result.error is not None
		assert "Extract failed" in result.error
		assert result.extracted_content is None

	@pytest.mark.asyncio
	async def test_inner_timeout_returns_error(self):
		"""extract raising asyncio.TimeoutError → graded 'Extract timed out' error."""
		llm = _make_mock_extract_llm(extract_side_effect=asyncio.TimeoutError())
		browser = _make_mock_browser()
		result = await _make_tools(llm=llm).execute("extract", {"query": "x"}, browser)
		assert result.error is not None
		assert "timed out" in result.error

	@pytest.mark.asyncio
	async def test_no_llm_truncates_markdown(self):
		"""_extract_llm unset → degrade to truncated markdown snippet at the offset."""
		browser = _make_mock_browser(html="<p>" + "z" * 200 + "</p>")
		tools = _make_tools(llm=None)
		tools._truncation = TruncationSettings(extract_fallback_max_chars=50)
		result = await tools.execute("extract", {"query": "x"}, browser)
		assert result.error is None
		assert len(result.extracted_content) == 50

	@pytest.mark.asyncio
	async def test_query_and_chunk_budget_passed_through(self):
		"""query (positional) + chunk budget (max_content_chars) forwarded to llm.extract."""
		llm = _make_mock_extract_llm()
		browser = _make_mock_browser()
		await _make_tools(llm=llm).execute("extract", {"query": "find prices"}, browser)
		args, kwargs = llm.extract.call_args
		assert args[0] == "find prices"  # positional query
		assert kwargs["max_content_chars"] == tools_default_chunk_max()

	@pytest.mark.asyncio
	async def test_markdown_source_used_not_innertext(self):
		"""get_page_html HTML → markdown fed to llm.extract (links preserved as markdown)."""
		llm = _make_mock_extract_llm()
		browser = _make_mock_browser(html='<html><body><p>Hello <a href="http://x.example">click</a></p></body></html>')
		await _make_tools(llm=llm).execute("extract", {"query": "x"}, browser)
		content = llm.extract.call_args.args[1]
		assert "Hello" in content
		assert "http://x.example" in content

	@pytest.mark.asyncio
	async def test_extract_links_false_strips_links(self):
		"""extract_links=False → no URL in the markdown fed to llm.extract (text kept)."""
		llm = _make_mock_extract_llm()
		browser = _make_mock_browser(html='<html><body><p>Hi <a href="http://x.example">click</a></p></body></html>')
		await _make_tools(llm=llm).execute("extract", {"query": "x", "extract_links": False}, browser)
		content = llm.extract.call_args.args[1]
		assert "http://x.example" not in content
		assert "click" in content

	@pytest.mark.asyncio
	async def test_already_collected_threaded_to_llm(self):
		"""already_collected param forwarded to llm.extract."""
		llm = _make_mock_extract_llm()
		browser = _make_mock_browser()
		await _make_tools(llm=llm).execute("extract", {"query": "x", "already_collected": ["dup item"]}, browser)
		assert llm.extract.call_args.kwargs["already_collected"] == ["dup item"]

	@pytest.mark.asyncio
	async def test_pagination_emits_offset_hint(self, tmp_path):
		"""Page markdown > chunk budget → free-text result carries a start_from_char hint up front."""
		llm = _make_mock_extract_llm(extract_return="chunk-summary")
		browser = _make_mock_browser(html="<html><body><p>" + "word " * 30 + "</p></body></html>")
		tools = _make_tools(llm=llm)
		tools._truncation = TruncationSettings(
			extract_chunk_max_chars=20, extract_save_threshold=10_000_000, extract_output_dir=str(tmp_path),
		)
		result = await tools.execute("extract", {"query": "x"}, browser)
		# threshold huge → not saved → hint must be up front in extracted_content (free-text)
		assert result.extracted_content.startswith("[chunk")
		assert "start_from_char=" in result.extracted_content
		assert "chunk-summary" in result.extracted_content

	@pytest.mark.asyncio
	async def test_structured_pagination_keeps_json_pure(self, tmp_path):
		"""Schema + pagination + small result → extracted_content pure JSON; hint in long_term_memory."""
		llm = _make_mock_extract_llm(extract_return='{"title": "X"}')
		browser = _make_mock_browser(html="<html><body><p>" + "word " * 30 + "</p></body></html>")
		tools = _make_tools(llm=llm, schema=_SCHEMA)
		tools._truncation = TruncationSettings(
			extract_chunk_max_chars=20, extract_save_threshold=10_000_000, extract_output_dir=str(tmp_path),
		)
		result = await tools.execute("extract", {"query": "x"}, browser)
		assert result.extracted_content == '{"title": "X"}'  # pure JSON, no hint
		assert result.long_term_memory is not None
		assert "start_from_char=" in result.long_term_memory

	@pytest.mark.asyncio
	async def test_result_above_threshold_saved_to_file(self, tmp_path):
		"""Single chunk, result >= threshold → saved to file; visible shows 'saved to'."""
		big = '{"title": "' + "X" * 60 + '"}'
		llm = _make_mock_extract_llm(extract_return=big)
		browser = _make_mock_browser()
		tools = _make_tools(llm=llm, schema=_SCHEMA)
		tools._truncation = TruncationSettings(extract_save_threshold=10, extract_output_dir=str(tmp_path))
		result = await tools.execute("extract", {"query": "x"}, browser)
		assert "saved to" in result.extracted_content
		assert result.long_term_memory is not None and "saved" in result.long_term_memory
		written = list(tmp_path.glob("extract_*.json"))
		assert len(written) == 1
		assert written[0].read_text(encoding="utf-8") == big

	@pytest.mark.asyncio
	async def test_start_from_char_past_end(self):
		"""start_from_char beyond the page → friendly 'no more content' result, extract not called."""
		llm = _make_mock_extract_llm()
		browser = _make_mock_browser()
		result = await _make_tools(llm=llm).execute("extract", {"query": "x", "start_from_char": 9_999_999}, browser)
		assert "no more content" in (result.extracted_content or "")
		llm.extract.assert_not_called()


def tools_default_chunk_max() -> int:
	return TruncationSettings().extract_chunk_max_chars


# ── Agent.__init__: wiring layer ──────────────────────────────────────


def _make_agent(llm: object, settings: AgentSettings) -> Agent:
	"""Agent with mocked browser (mirrors test_agent_pause_resume._make_agent)."""
	browser = MagicMock()
	browser.start = AsyncMock()
	browser.stop = AsyncMock()
	browser.navigate = AsyncMock()
	return Agent(task="t", llm=llm, browser=browser, settings=settings)


class TestExtractWiring:
	def test_default_reuses_main_llm(self):
		"""No extract_llm configured → tools._extract_llm is the main llm (browser-use parity)."""
		llm = MagicMock()
		agent = _make_agent(llm=llm, settings=AgentSettings())
		assert agent.tools._extract_llm is llm
		assert agent.tools._extraction_schema is None

	def test_dedicated_extract_llm(self):
		"""extract_llm configured → a dedicated LLMClient is built, NOT the main llm."""
		llm = MagicMock()
		settings = AgentSettings(extract_llm=LLMSettings(model="glm-flash", api_key="k"))
		agent = _make_agent(llm=llm, settings=settings)
		assert isinstance(agent.tools._extract_llm, LLMClient)
		assert agent.tools._extract_llm is not llm
		assert agent.tools._extract_llm.model == "glm-flash"

	def test_schema_injected(self):
		"""extraction_schema on settings → propagates to tools._extraction_schema."""
		agent = _make_agent(llm=MagicMock(), settings=AgentSettings(extraction_schema=_SCHEMA))
		assert agent.tools._extraction_schema == _SCHEMA


# ── LLMClient.extract: client layer ───────────────────────────────────


def _mock_tool_use_block(name: str, input_dict: dict) -> MagicMock:
	block = MagicMock()
	block.type = "tool_use"
	block.name = name
	block.input = input_dict
	return block


def _mock_text_block(text: str) -> MagicMock:
	block = MagicMock()
	block.type = "text"
	block.text = text
	return block


def _make_client(fallback: bool = False) -> LLMClient:
	if fallback:
		settings = LLMSettings(
			model="main", api_key="k",
			fallback=FallbackLLMSettings(model="fb", api_key="fbk"),
		)
	else:
		settings = LLMSettings(api_key="test-key")
	return LLMClient(settings)


class TestExtractClient:
	@pytest.mark.asyncio
	async def test_structured_returns_json(self):
		"""Valid schema → tool_use forced; tool_use.input returned as JSON string."""
		client = _make_client()
		response = MagicMock()
		response.content = [_mock_tool_use_block("extract_result", {"title": "Hello"})]
		with patch.object(client.client.messages, "create", return_value=response) as mocked:
			result = await client.extract("get title", "page text", output_schema=_SCHEMA)
		assert json.loads(result) == {"title": "Hello"}
		kwargs = mocked.call_args.kwargs
		assert kwargs["tool_choice"] == {"type": "tool", "name": "extract_result"}
		assert kwargs["tools"][0]["name"] == "extract_result"
		assert kwargs["tools"][0]["input_schema"] == _SCHEMA

	@pytest.mark.asyncio
	async def test_structured_model_skips_tool_falls_back_to_text(self, caplog):
		"""Schema set but model returns only text → degrade to text from same response."""
		client = _make_client()
		response = MagicMock()
		response.content = [_mock_text_block("just text")]
		with patch.object(client.client.messages, "create", return_value=response):
			with caplog.at_level(logging.WARNING):
				result = await client.extract("get title", "page", output_schema=_SCHEMA)
		assert result == "just text"

	@pytest.mark.asyncio
	async def test_invalid_schema_falls_back_to_freetext(self, caplog):
		"""Schema missing properties (not an object) → free-text branch, no tools."""
		client = _make_client()
		response = MagicMock()
		response.content = [_mock_text_block("summary")]
		with patch.object(client.client.messages, "create", return_value=response) as mocked:
			with caplog.at_level(logging.WARNING):
				result = await client.extract("q", "page", output_schema={"type": "string"})
		assert result == "summary"
		kwargs = mocked.call_args.kwargs
		assert "tools" not in kwargs
		assert "tool_choice" not in kwargs

	@pytest.mark.asyncio
	async def test_non_dict_schema_falls_back_to_freetext(self):
		"""Non-dict schema → free-text branch."""
		client = _make_client()
		response = MagicMock()
		response.content = [_mock_text_block("summary")]
		with patch.object(client.client.messages, "create", return_value=response) as mocked:
			result = await client.extract("q", "page", output_schema="not a schema")
		assert result == "summary"
		assert "tools" not in mocked.call_args.kwargs

	@pytest.mark.asyncio
	async def test_freetext_path_no_tools(self):
		"""output_schema=None → plain completion, no tools/tool_choice."""
		client = _make_client()
		response = MagicMock()
		response.content = [_mock_text_block("the summary")]
		with patch.object(client.client.messages, "create", return_value=response) as mocked:
			result = await client.extract("summarize", "page text")
		assert result == "the summary"
		kwargs = mocked.call_args.kwargs
		assert "tools" not in kwargs
		assert "tool_choice" not in kwargs

	@pytest.mark.asyncio
	async def test_content_truncated_to_max_chars(self):
		"""content is sliced to max_content_chars before being sent."""
		client = _make_client()
		long = "x" * 500
		response = MagicMock()
		response.content = [_mock_text_block("ok")]
		with patch.object(client.client.messages, "create", return_value=response) as m:
			await client.extract("q", long, max_content_chars=100)
		sent = m.call_args.kwargs["messages"][0]["content"]
		assert "x" * 100 in sent
		assert "x" * 101 not in sent

	@pytest.mark.asyncio
	async def test_fallback_on_rate_limit(self):
		"""First call RateLimitError → switch to fallback, retry once, succeed."""
		client = _make_client(fallback=True)
		response = MagicMock()
		response.content = [_mock_tool_use_block("extract_result", {"title": "X"})]
		calls = {"n": 0}

		def side_effect(*a, **k):
			calls["n"] += 1
			if calls["n"] == 1:
				raise RateLimitError(message="rl", response=MagicMock(status_code=429), body=None)
			return response

		with patch.object(client.client.messages, "create", side_effect=side_effect), \
				patch.object(client._fallback_client.messages, "create", side_effect=side_effect):
			result = await client.extract("q", "page", output_schema=_SCHEMA)
		assert json.loads(result) == {"title": "X"}
		assert client._using_fallback is True
		assert calls["n"] == 2

	@pytest.mark.asyncio
	async def test_structured_no_fallback_raises(self):
		"""Structured path, no fallback → RateLimitError propagates (re-raise branch)."""
		client = _make_client()
		with patch.object(client.client.messages, "create", side_effect=RateLimitError(
			message="rl", response=MagicMock(status_code=429), body=None,
		)):
			with pytest.raises(RateLimitError):
				await client.extract("q", "page", output_schema=_SCHEMA)

	@pytest.mark.asyncio
	async def test_freetext_fallback_on_rate_limit(self):
		"""Free-text path, fallback configured → retry once on RateLimitError, succeed."""
		client = _make_client(fallback=True)
		response = MagicMock()
		response.content = [_mock_text_block("recovered")]
		calls = {"n": 0}

		def side_effect(*a, **k):
			calls["n"] += 1
			if calls["n"] == 1:
				raise RateLimitError(message="rl", response=MagicMock(status_code=429), body=None)
			return response

		with patch.object(client.client.messages, "create", side_effect=side_effect), \
				patch.object(client._fallback_client.messages, "create", side_effect=side_effect):
			result = await client.extract("q", "page")  # no schema → free-text path
		assert result == "recovered"
		assert client._using_fallback is True
		assert calls["n"] == 2

	@pytest.mark.asyncio
	async def test_no_fallback_raises(self):
		"""No fallback configured → RateLimitError propagates (caller surfaces as ActionResult.error)."""
		client = _make_client()
		with patch.object(client.client.messages, "create", side_effect=RateLimitError(
			message="rl", response=MagicMock(status_code=429), body=None,
		)):
			with pytest.raises(RateLimitError):
				await client.extract("q", "page")

	@pytest.mark.asyncio
	async def test_already_collected_threaded_into_prompt(self):
		"""already_collected → appended to user message as a skip-list."""
		client = _make_client()
		response = MagicMock()
		response.content = [_mock_text_block("ok")]
		with patch.object(client.client.messages, "create", return_value=response) as m:
			await client.extract("q", "page", already_collected=["item A", "item B"])
		sent = m.call_args.kwargs["messages"][0]["content"]
		assert "item A" in sent and "item B" in sent
		assert "already collected" in sent.lower()

	@pytest.mark.asyncio
	async def test_no_already_collected_omits_block(self):
		"""already_collected=None → no skip-list block in user message."""
		client = _make_client()
		response = MagicMock()
		response.content = [_mock_text_block("ok")]
		with patch.object(client.client.messages, "create", return_value=response) as m:
			await client.extract("q", "page")
		sent = m.call_args.kwargs["messages"][0]["content"]
		assert "already collected" not in sent.lower()

	@pytest.mark.asyncio
	async def test_call_timeout_raises_timeouterror(self):
		"""call_timeout set + slow (sync) create → asyncio.TimeoutError propagates (not swallowed).

		messages.create is a blocking sync call; the timeout path runs it via asyncio.to_thread.
		"""
		import time
		client = _make_client()

		def slow(*a, **k):
			time.sleep(0.3)  # sync blocking; runs in a worker thread under to_thread
			return MagicMock()

		with patch.object(client.client.messages, "create", side_effect=slow):
			with pytest.raises(asyncio.TimeoutError):
				await client.extract("q", "page", call_timeout=0.01)

	@pytest.mark.asyncio
	async def test_fallback_forwards_already_collected(self):
		"""RateLimit → switch to fallback + retry; retried call must carry already_collected."""
		client = _make_client(fallback=True)
		response = MagicMock()
		response.content = [_mock_tool_use_block("extract_result", {"title": "X"})]
		seen = []

		def side_effect(*a, **k):
			seen.append(k)
			if len(seen) == 1:
				raise RateLimitError(message="rl", response=MagicMock(status_code=429), body=None)
			return response

		with patch.object(client.client.messages, "create", side_effect=side_effect), \
				patch.object(client._fallback_client.messages, "create", side_effect=side_effect):
			await client.extract("q", "page", output_schema=_SCHEMA, already_collected=["dup"])
		assert client._using_fallback is True
		assert len(seen) == 2
		# the retried (fallback) call's user message carries the dedupe list
		assert "dup" in seen[1]["messages"][0]["content"]
