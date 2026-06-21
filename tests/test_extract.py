"""Tests for extract: _action_extract tool layer, Agent wiring, LLMClient.extract client layer."""
from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anthropic import RateLimitError

from tree_walker.agent.agent import Agent
from tree_walker.config import AgentSettings, FallbackLLMSettings, LLMSettings
from tree_walker.llm.client import LLMClient
from tree_walker.tools.actions import Tools


# ── tool-layer helpers ────────────────────────────────────────────────


def _make_mock_browser(inner_text: str = "some page text", raise_exc: Exception | None = None) -> MagicMock:
	"""Mock BrowserSession whose execute_js returns inner_text (or raises)."""
	browser = MagicMock()
	if raise_exc is not None:
		browser.execute_js = AsyncMock(side_effect=raise_exc)
	else:
		browser.execute_js = AsyncMock(return_value=inner_text)
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
		browser = _make_mock_browser(inner_text="page body")
		result = await _make_tools(llm=llm, schema=None).execute("extract", {"goal": "summarize"}, browser)
		assert result.error is None
		assert result.extracted_content == "the summary"
		llm.extract.assert_awaited_once()
		assert llm.extract.call_args.kwargs["output_schema"] is None

	@pytest.mark.asyncio
	async def test_structured_path_with_schema(self):
		"""Schema injected → extract receives it; result echoed verbatim."""
		llm = _make_mock_extract_llm(extract_return='{"title": "X"}')
		browser = _make_mock_browser(inner_text="page body")
		tools = _make_tools(llm=llm, schema=_SCHEMA)
		result = await tools.execute("extract", {"goal": "get title"}, browser)
		assert result.error is None
		assert result.extracted_content == '{"title": "X"}'
		assert llm.extract.call_args.kwargs["output_schema"] == _SCHEMA

	@pytest.mark.asyncio
	async def test_empty_page_returns_placeholder(self):
		"""Empty innerText → '(empty page)', extract never called."""
		llm = _make_mock_extract_llm()
		browser = _make_mock_browser(inner_text="")
		result = await _make_tools(llm=llm).execute("extract", {"goal": "x"}, browser)
		assert result.extracted_content == "(empty page)"
		llm.extract.assert_not_called()

	@pytest.mark.asyncio
	async def test_execute_js_failure_warns_and_treats_empty(self, caplog):
		"""execute_js raising → logger.warning + '(empty page)' (no longer silent)."""
		llm = _make_mock_extract_llm()
		browser = _make_mock_browser(raise_exc=RuntimeError("cdp down"))
		with caplog.at_level(logging.WARNING):
			result = await _make_tools(llm=llm).execute("extract", {"goal": "x"}, browser)
		assert result.extracted_content == "(empty page)"
		assert "document.body.innerText failed" in caplog.text
		llm.extract.assert_not_called()

	@pytest.mark.asyncio
	async def test_llm_exception_returns_error(self):
		"""extract raising → ActionResult(error=...), not bubbling to Tools.execute generic catch."""
		llm = _make_mock_extract_llm(extract_side_effect=RuntimeError("llm boom"))
		browser = _make_mock_browser(inner_text="page body")
		result = await _make_tools(llm=llm).execute("extract", {"goal": "x"}, browser)
		assert result.error is not None
		assert "Extract failed" in result.error
		assert result.extracted_content is None

	@pytest.mark.asyncio
	async def test_no_llm_truncates_raw_text(self):
		"""_extract_llm unset (fresh Tools / no Agent wiring) → degrade to truncated innerText."""
		browser = _make_mock_browser(inner_text="0123456789" * 500)  # 5000 chars
		tools = _make_tools(llm=None)  # mirrors a bare Tools() with no injection
		# shrink the fallback cap so the truncation is observable
		from tree_walker.config import TruncationSettings
		tools._truncation = TruncationSettings(extract_fallback_max_chars=50)
		result = await tools.execute("extract", {"goal": "x"}, browser)
		assert result.error is None
		assert len(result.extracted_content) == 50

	@pytest.mark.asyncio
	async def test_goal_and_max_chars_passed_through(self):
		"""goal + extract_page_max_chars are forwarded to llm.extract."""
		llm = _make_mock_extract_llm()
		browser = _make_mock_browser(inner_text="page body")
		await _make_tools(llm=llm).execute("extract", {"goal": "find prices"}, browser)
		args, kwargs = llm.extract.call_args
		assert args[0] == "find prices"  # positional goal
		assert kwargs["max_content_chars"] == tools_default_page_max()


def tools_default_page_max() -> int:
	from tree_walker.config import TruncationSettings
	return TruncationSettings().extract_page_max_chars


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
