"""Tests for the multi-engine search action (baidu/google/bing/duckduckgo).

Covers:
- URL building per engine (baidu uses ``wd``, google appends ``&udm=14``, etc.)
- URL encoding via ``urllib.parse.quote_plus`` (spaces -> ``+``, CJK -> ``%XX``,
  ``&`` / ``=`` / ``#`` escaped so they cannot break the URL structure)
- Backward compatibility (callers passing only ``{query: ...}`` still work)
- Result shape (extracted_content + long_term_memory, no success/is_done)
- Pydantic validation (Literal rejects invalid engine; extra keys rejected)
- Direct-call error path (invalid engine -> ``ActionResult(error=...)``,
  navigation must not fire)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from tree_walker.tools.actions import Tools, _SEARCH_ENGINE_URLS
from tree_walker.tools.models import SearchParams


# ── Shared fixture ────────────────────────────────────────────────────────────


def _make_browser():
    """A stub BrowserSession whose ``.navigate`` captures the URL via AsyncMock.

    ``_action_search`` only calls ``browser.navigate(url)``, so mocking the
    whole method is the correct isolation boundary (no CDP primitives needed).
    """
    bs = MagicMock()
    bs.navigate = AsyncMock()
    return bs


# ── URL building per engine ──────────────────────────────────────────────────


class TestSearchUrlBuilding:
    @pytest.mark.asyncio
    async def test_default_engine_is_baidu(self):
        tools = Tools()
        browser = _make_browser()

        result = await tools.execute("search", {"query": "python"}, browser)

        browser.navigate.assert_awaited_once()
        assert browser.navigate.call_args[0][0] == "https://www.baidu.com/s?wd=python"
        assert result.error is None
        assert "Baidu" in result.extracted_content

    @pytest.mark.asyncio
    async def test_explicit_baidu_uses_wd_param(self):
        tools = Tools()
        browser = _make_browser()

        await tools.execute("search", {"query": "cats", "engine": "baidu"}, browser)

        assert browser.navigate.call_args[0][0] == "https://www.baidu.com/s?wd=cats"

    @pytest.mark.asyncio
    async def test_google_has_udm14(self):
        tools = Tools()
        browser = _make_browser()

        await tools.execute("search", {"query": "cats", "engine": "google"}, browser)

        assert browser.navigate.call_args[0][0] == "https://www.google.com/search?q=cats&udm=14"

    @pytest.mark.asyncio
    async def test_bing_url(self):
        tools = Tools()
        browser = _make_browser()

        await tools.execute("search", {"query": "dogs", "engine": "bing"}, browser)

        assert browser.navigate.call_args[0][0] == "https://www.bing.com/search?q=dogs"

    @pytest.mark.asyncio
    async def test_duckduckgo_url(self):
        tools = Tools()
        browser = _make_browser()

        await tools.execute("search", {"query": "rust", "engine": "duckduckgo"}, browser)

        assert browser.navigate.call_args[0][0] == "https://duckduckgo.com/?q=rust"


# ── URL encoding ─────────────────────────────────────────────────────────────


class TestSearchUrlEncoding:
    @pytest.mark.asyncio
    async def test_spaces_become_plus_not_percent20(self):
        tools = Tools()
        browser = _make_browser()

        await tools.execute("search", {"query": "a b"}, browser)

        url = browser.navigate.call_args[0][0]
        assert "a+b" in url
        assert "%20" not in url  # quote_plus, not quote

    @pytest.mark.asyncio
    async def test_chinese_encoded_as_percent_utf8(self):
        tools = Tools()
        browser = _make_browser()

        await tools.execute("search", {"query": "你好"}, browser)

        # 你好 -> UTF-8 bytes E4 BD A0 E5 A5 BD
        assert "%E4%BD%A0%E5%A5%BD" in browser.navigate.call_args[0][0]

    @pytest.mark.asyncio
    async def test_special_chars_escaped_no_url_breakage(self):
        """Raw ``&`` / ``=`` / ``#`` in the query must not break URL structure."""
        tools = Tools()
        browser = _make_browser()

        await tools.execute("search", {"query": "a&b=c#d"}, browser)

        url = browser.navigate.call_args[0][0]
        # Default engine is baidu; payload fully encoded as `wd=a%26b%3Dc%23d`.
        assert "wd=a%26b%3Dc%23d" in url

    @pytest.mark.asyncio
    async def test_ampersand_injection_does_not_duplicate_param(self):
        """``query='x&udm=14'`` must not inject a second ``udm`` for google."""
        tools = Tools()
        browser = _make_browser()

        await tools.execute("search", {"query": "x&udm=14", "engine": "google"}, browser)

        url = browser.navigate.call_args[0][0]
        # Exactly one literal ``udm=14`` (the template one); the injected one
        # is percent-encoded as ``%26udm%3D14``.
        assert url.count("udm=14") == 1
        assert "%26udm%3D14" in url


# ── Backward compatibility & result shape ────────────────────────────────────


class TestSearchBackwardCompat:
    @pytest.mark.asyncio
    async def test_only_query_still_works(self):
        """Old callers that pass only ``{query: ...}`` must keep working."""
        tools = Tools()
        browser = _make_browser()

        result = await tools.execute("search", {"query": "legacy"}, browser)

        assert result.error is None
        assert browser.navigate.call_args[0][0].startswith("https://www.baidu.com/s?wd=")


class TestSearchResultShape:
    @pytest.mark.asyncio
    async def test_returns_extracted_and_long_term_memory(self):
        tools = Tools()
        browser = _make_browser()

        result = await tools.execute("search", {"query": "python"}, browser)

        assert result.extracted_content is not None
        assert result.long_term_memory is not None
        # Non-done action must never set success=True (ActionResult validator).
        assert result.success is None
        assert result.is_done is False

    @pytest.mark.asyncio
    async def test_memory_string_format(self):
        tools = Tools()
        browser = _make_browser()

        result = await tools.execute("search", {"query": "cats", "engine": "google"}, browser)

        assert result.extracted_content == "Searched Google for 'cats'"


# ── Pydantic validation ──────────────────────────────────────────────────────


class TestSearchEngineValidation:
    def test_invalid_engine_rejected_by_pydantic(self):
        with pytest.raises(ValidationError):
            SearchParams.model_validate({"query": "x", "engine": "yahoo"})

    def test_extra_keys_rejected(self):
        with pytest.raises(ValidationError):
            SearchParams.model_validate({"query": "x", "foo": 1})

    def test_query_required(self):
        with pytest.raises(ValidationError):
            SearchParams.model_validate({"engine": "google"})

    def test_engine_defaults_to_baidu(self):
        params = SearchParams.model_validate({"query": "x"})
        assert params.engine == "baidu"

    def test_schema_has_enum_and_default(self):
        schema = SearchParams.model_json_schema()
        eng = schema["properties"]["engine"]
        assert eng["enum"] == ["baidu", "google", "bing", "duckduckgo"]
        assert eng["default"] == "baidu"

    def test_engine_url_templates_constant(self):
        """Sanity-check the module-level constant holds all four engines."""
        assert set(_SEARCH_ENGINE_URLS.keys()) == {"baidu", "google", "bing", "duckduckgo"}
        assert "{query}" in _SEARCH_ENGINE_URLS["baidu"]
        assert "udm=14" in _SEARCH_ENGINE_URLS["google"]


# ── Direct-call error path ───────────────────────────────────────────────────


class TestSearchDirectCallInvalidEngine:
    @pytest.mark.asyncio
    async def test_direct_invalid_engine_returns_error_not_raises(self):
        """``Tools.execute`` doesn't re-validate (validation is in step.py), so
        an invalid engine hits a ``KeyError`` in the handler, which
        ``Tools.execute``'s ``except`` wraps into ``ActionResult(error=...)``.
        Navigation must not fire."""
        tools = Tools()
        browser = _make_browser()

        result = await tools.execute("search", {"query": "x", "engine": "yahoo"}, browser)

        assert result.error is not None
        browser.navigate.assert_not_called()
