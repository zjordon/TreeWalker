"""Tests for save_as_pdf: print_to_pdf CDP params, _action_save_as_pdf."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tree_walker.config import BrowserSettings
from tree_walker.tools.actions import Tools


# ── CDP client mock (mirrors test_screenshot._make_mock_cdp_client) ────


_NO_PDF_RETURN = object()  # sentinel: distinguish "unset" from explicit None/{}


def _make_mock_cdp_client(pdf_return=_NO_PDF_RETURN, pdf_side_effect=None):
	"""Create a mock CDPClient whose Page.printToPDF is controllable."""
	client = MagicMock()
	client.start = AsyncMock()
	client.stop = AsyncMock()
	client.send.Target.getTargets = AsyncMock(return_value={
		"targetInfos": [
			{"type": "page", "targetId": "test-target", "url": "about:blank", "title": ""}
		]
	})
	client.send.Target.attachToTarget = AsyncMock(return_value={"sessionId": "test-session"})
	client.send.Target.setAutoAttach = AsyncMock(return_value={})
	client.send.Page.enable = AsyncMock(return_value={})
	client.send.DOM.enable = AsyncMock(return_value={})
	client.send.Runtime.evaluate = AsyncMock(return_value={
		"result": {"value": '{"url": "https://example.com", "title": "Test"}'}
	})
	if pdf_side_effect is not None:
		client.send.Page.printToPDF = AsyncMock(side_effect=pdf_side_effect)
	else:
		rv = {"data": "AAAA"} if pdf_return is _NO_PDF_RETURN else pdf_return
		client.send.Page.printToPDF = AsyncMock(return_value=rv)
	return client


async def _start_session(client):
	"""Build a real BrowserSession bound to the mock CDP client."""
	from tree_walker.browser.session import BrowserSession
	settings = BrowserSettings(ws_url="ws://localhost:9222")
	with patch("tree_walker.browser.session.CDPClient", return_value=client):
		session = BrowserSession(settings=settings)
		await session.start()
	return session


def _captured_params(client):
	"""Return the CDP params dict passed to Page.printToPDF."""
	args, kwargs = client.send.Page.printToPDF.call_args
	return args[0]


# ── print_to_pdf: CDP params assembly ─────────────────────────────────


class TestPrintToPdfParams:
	@pytest.mark.asyncio
	async def test_default_params(self):
		"""Default call: portrait letter, printBackground, scale 1.0, preferCSSPageSize."""
		client = _make_mock_cdp_client()
		session = await _start_session(client)
		await session.print_to_pdf()
		assert _captured_params(client) == {
			"printBackground": True,
			"landscape": False,
			"scale": 1.0,
			"paperWidth": 8.5,
			"paperHeight": 11.0,
			"preferCSSPageSize": True,
		}

	@pytest.mark.asyncio
	async def test_a4_paper_dimensions(self):
		client = _make_mock_cdp_client()
		session = await _start_session(client)
		await session.print_to_pdf(paper_format="a4")
		params = _captured_params(client)
		assert params["paperWidth"] == 8.27
		assert params["paperHeight"] == 11.69

	@pytest.mark.asyncio
	async def test_legal_and_tabloid_dimensions(self):
		client = _make_mock_cdp_client()
		session = await _start_session(client)
		await session.print_to_pdf(paper_format="legal")
		legal = _captured_params(client)
		assert (legal["paperWidth"], legal["paperHeight"]) == (8.5, 14.0)
		await session.print_to_pdf(paper_format="tabloid")
		tabloid = _captured_params(client)
		assert (tabloid["paperWidth"], tabloid["paperHeight"]) == (11.0, 17.0)

	@pytest.mark.asyncio
	async def test_landscape_flag(self):
		client = _make_mock_cdp_client()
		session = await _start_session(client)
		await session.print_to_pdf(landscape=True)
		assert _captured_params(client)["landscape"] is True

	@pytest.mark.asyncio
	async def test_scale_passed_through(self):
		client = _make_mock_cdp_client()
		session = await _start_session(client)
		await session.print_to_pdf(scale=1.5)
		assert _captured_params(client)["scale"] == 1.5

	@pytest.mark.asyncio
	async def test_print_background_false(self):
		client = _make_mock_cdp_client()
		session = await _start_session(client)
		await session.print_to_pdf(print_background=False)
		assert _captured_params(client)["printBackground"] is False

	@pytest.mark.asyncio
	async def test_paper_format_case_insensitive(self):
		"""Uppercase paper format is accepted (lowercased internally)."""
		client = _make_mock_cdp_client()
		session = await _start_session(client)
		await session.print_to_pdf(paper_format="A4")
		params = _captured_params(client)
		assert params["paperWidth"] == 8.27

	@pytest.mark.asyncio
	async def test_prefer_css_page_size_always_true(self):
		"""preferCSSPageSize is hardcoded True (matches browser-use)."""
		client = _make_mock_cdp_client()
		session = await _start_session(client)
		await session.print_to_pdf()
		assert _captured_params(client)["preferCSSPageSize"] is True

	@pytest.mark.asyncio
	async def test_missing_data_raises_runtime_error(self):
		client = _make_mock_cdp_client(pdf_return={})
		session = await _start_session(client)
		with pytest.raises(RuntimeError):
			await session.print_to_pdf()

	@pytest.mark.asyncio
	async def test_non_dict_result_raises_runtime_error(self):
		client = _make_mock_cdp_client(pdf_return=None)
		session = await _start_session(client)
		with pytest.raises(RuntimeError):
			await session.print_to_pdf()

	@pytest.mark.asyncio
	async def test_cdp_exception_propagates(self):
		client = _make_mock_cdp_client(pdf_side_effect=RuntimeError("cdp boom"))
		session = await _start_session(client)
		with pytest.raises(RuntimeError, match="cdp boom"):
			await session.print_to_pdf()

	@pytest.mark.asyncio
	async def test_returns_decoded_bytes(self):
		# "AAAA" base64-decodes to 3 zero bytes
		client = _make_mock_cdp_client(pdf_return={"data": "AAAA"})
		session = await _start_session(client)
		data = await session.print_to_pdf()
		assert data == b"\x00\x00\x00"

	@pytest.mark.asyncio
	async def test_wait_settle_invokes_page_settle(self):
		client = _make_mock_cdp_client()
		session = await _start_session(client)
		session._wait_for_page_settle = AsyncMock()
		await session.print_to_pdf(wait_settle=True)
		session._wait_for_page_settle.assert_awaited_once()

	@pytest.mark.asyncio
	async def test_wait_settle_failure_does_not_block_pdf(self):
		"""If _wait_for_page_settle raises, the PDF still proceeds."""
		client = _make_mock_cdp_client()
		session = await _start_session(client)
		session._wait_for_page_settle = AsyncMock(side_effect=RuntimeError("settle boom"))
		data = await session.print_to_pdf(wait_settle=True)
		assert isinstance(data, bytes)
		client.send.Page.printToPDF.assert_awaited_once()


# ── _action_save_as_pdf: tool layer ───────────────────────────────────


def _make_mock_browser(print_to_pdf_return=b"\x00" * 100, print_to_pdf_side_effect=None):
	browser = MagicMock()
	if print_to_pdf_side_effect is not None:
		browser.print_to_pdf = AsyncMock(side_effect=print_to_pdf_side_effect)
	else:
		browser.print_to_pdf = AsyncMock(return_value=print_to_pdf_return)
	return browser


class TestActionSaveAsPdf:
	@pytest.mark.asyncio
	async def test_writes_bytes_and_echoes_count(self, tmp_path):
		data = b"%PDF-1.4 fake body"
		browser = _make_mock_browser(print_to_pdf_return=data)
		out = tmp_path / "report.pdf"
		result = await Tools().execute("save_as_pdf", {"path": str(out)}, browser)
		assert out.read_bytes() == data
		assert result.error is None
		assert "saved" in result.extracted_content.lower()
		assert str(len(data)) in result.extracted_content

	@pytest.mark.asyncio
	async def test_default_paper_format_in_meta(self, tmp_path):
		browser = _make_mock_browser(print_to_pdf_return=b"x" * 50)
		out = tmp_path / "a.pdf"
		result = await Tools().execute("save_as_pdf", {"path": str(out)}, browser)
		assert "paper=letter" in result.extracted_content

	@pytest.mark.asyncio
	async def test_landscape_echoed_in_meta(self, tmp_path):
		browser = _make_mock_browser(print_to_pdf_return=b"x" * 10)
		out = tmp_path / "a.pdf"
		result = await Tools().execute(
			"save_as_pdf", {"path": str(out), "landscape": True}, browser
		)
		assert "landscape" in result.extracted_content

	@pytest.mark.asyncio
	async def test_params_passthrough(self, tmp_path):
		browser = _make_mock_browser(print_to_pdf_return=b"data")
		out = tmp_path / "a.pdf"
		await Tools().execute(
			"save_as_pdf",
			{"path": str(out), "paper_format": "a4", "landscape": True, "scale": 1.5},
			browser,
		)
		browser.print_to_pdf.assert_awaited_once()
		kwargs = browser.print_to_pdf.call_args.kwargs
		assert kwargs["paper_format"] == "a4"
		assert kwargs["landscape"] is True
		assert kwargs["scale"] == 1.5
		assert kwargs["print_background"] is True  # default

	@pytest.mark.asyncio
	async def test_creates_parent_dirs(self, tmp_path):
		"""Path with a non-existent parent dir: parent is auto-created."""
		browser = _make_mock_browser(print_to_pdf_return=b"data")
		out = tmp_path / "sub" / "deep" / "out.pdf"
		result = await Tools().execute("save_as_pdf", {"path": str(out)}, browser)
		assert result.error is None
		assert out.exists()
		assert out.read_bytes() == b"data"

	@pytest.mark.asyncio
	async def test_cdp_failure_returns_error_result(self):
		browser = _make_mock_browser(print_to_pdf_side_effect=RuntimeError("cdp boom"))
		result = await Tools().execute("save_as_pdf", {"path": "x.pdf"}, browser)
		assert result.error is not None
		assert "Failed to generate PDF" in result.error
		assert result.extracted_content is None

	@pytest.mark.asyncio
	async def test_write_failure_returns_error_result(self, tmp_path):
		"""Path pointing at a directory → OSError surfaced as ActionResult.error."""
		browser = _make_mock_browser(print_to_pdf_return=b"data")
		result = await Tools().execute("save_as_pdf", {"path": str(tmp_path)}, browser)
		assert result.error is not None
		assert "Failed to save PDF" in result.error
