"""Tests for screenshot: take_screenshot CDP params, _action_screenshot, resize helper."""
from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tree_walker.config import BrowserSettings
from tree_walker.tools.actions import Tools


# ── CDP client mock (mirrors test_highlight._make_mock_cdp_client) ─────


_NO_CAPTURE_RETURN = object()  # sentinel: distinguish "unset" from explicit None/{}


def _make_mock_cdp_client(capture_return=_NO_CAPTURE_RETURN, capture_side_effect=None):
	"""Create a mock CDPClient whose Page.captureScreenshot is controllable."""
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
	if capture_side_effect is not None:
		client.send.Page.captureScreenshot = AsyncMock(side_effect=capture_side_effect)
	else:
		rv = {"data": "iVBORw0KGgo="} if capture_return is _NO_CAPTURE_RETURN else capture_return
		client.send.Page.captureScreenshot = AsyncMock(return_value=rv)
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
	"""Return the CDP params dict passed to Page.captureScreenshot."""
	args, kwargs = client.send.Page.captureScreenshot.call_args
	return args[0]


# ── take_screenshot: CDP params assembly ──────────────────────────────


class TestTakeScreenshotParams:
	@pytest.mark.asyncio
	async def test_default_is_png_no_extras(self):
		"""Default call mirrors legacy behaviour: only format=png."""
		client = _make_mock_cdp_client()
		session = await _start_session(client)
		await session.take_screenshot()
		assert _captured_params(client) == {"format": "png"}

	@pytest.mark.asyncio
	async def test_full_page_sets_captureBeyondViewport(self):
		client = _make_mock_cdp_client()
		session = await _start_session(client)
		await session.take_screenshot(full_page=True)
		assert _captured_params(client)["captureBeyondViewport"] is True

	@pytest.mark.asyncio
	async def test_jpeg_quality_passed(self):
		client = _make_mock_cdp_client()
		session = await _start_session(client)
		await session.take_screenshot(format="jpeg", quality=80)
		assert _captured_params(client)["quality"] == 80

	@pytest.mark.asyncio
	async def test_png_quality_ignored(self):
		"""quality must NOT be sent to CDP when format != jpeg (CDP constraint)."""
		client = _make_mock_cdp_client()
		session = await _start_session(client)
		await session.take_screenshot(format="png", quality=80)
		assert "quality" not in _captured_params(client)

	@pytest.mark.asyncio
	async def test_webp_quality_ignored(self):
		client = _make_mock_cdp_client()
		session = await _start_session(client)
		await session.take_screenshot(format="webp", quality=80)
		assert "quality" not in _captured_params(client)

	@pytest.mark.asyncio
	async def test_clip_has_scale_one(self):
		client = _make_mock_cdp_client()
		session = await _start_session(client)
		await session.take_screenshot(clip={"x": 1, "y": 2, "width": 3, "height": 4})
		clip = _captured_params(client)["clip"]
		assert clip["scale"] == 1
		assert clip["x"] == 1
		assert clip["width"] == 3

	@pytest.mark.asyncio
	async def test_missing_data_raises_runtime_error(self):
		client = _make_mock_cdp_client(capture_return={})
		session = await _start_session(client)
		with pytest.raises(RuntimeError):
			await session.take_screenshot()

	@pytest.mark.asyncio
	async def test_non_dict_result_raises_runtime_error(self):
		client = _make_mock_cdp_client(capture_return=None)
		session = await _start_session(client)
		with pytest.raises(RuntimeError):
			await session.take_screenshot()

	@pytest.mark.asyncio
	async def test_cdp_exception_propagates(self):
		client = _make_mock_cdp_client(capture_side_effect=RuntimeError("cdp boom"))
		session = await _start_session(client)
		with pytest.raises(RuntimeError, match="cdp boom"):
			await session.take_screenshot()

	@pytest.mark.asyncio
	async def test_returns_decoded_bytes(self):
		# "AAAA" base64-decodes to 3 zero bytes
		client = _make_mock_cdp_client(capture_return={"data": "AAAA"})
		session = await _start_session(client)
		data = await session.take_screenshot()
		assert data == b"\x00\x00\x00"

	@pytest.mark.asyncio
	async def test_wait_settle_invokes_page_settle(self):
		client = _make_mock_cdp_client()
		session = await _start_session(client)
		session._wait_for_page_settle = AsyncMock()
		await session.take_screenshot(wait_settle=True)
		session._wait_for_page_settle.assert_awaited_once()

	@pytest.mark.asyncio
	async def test_wait_settle_failure_does_not_block_screenshot(self):
		"""If _wait_for_page_settle raises, the screenshot still proceeds."""
		client = _make_mock_cdp_client()
		session = await _start_session(client)
		session._wait_for_page_settle = AsyncMock(side_effect=RuntimeError("settle boom"))
		data = await session.take_screenshot(wait_settle=True)
		assert isinstance(data, bytes)  # captured despite settle failure
		client.send.Page.captureScreenshot.assert_awaited_once()

	@pytest.mark.asyncio
	async def test_get_state_screenshot_failure_degrades_to_none(self):
		"""get_state must not crash when take_screenshot raises; logs and sets None."""
		from tree_walker.browser.session import BrowserSession
		from tree_walker.browser.views import SerializedDOMState
		client = _make_mock_cdp_client(capture_side_effect=RuntimeError("snap boom"))
		settings = BrowserSettings(ws_url="ws://localhost:9222")
		with patch("tree_walker.browser.session.CDPClient", return_value=client):
			session = BrowserSession(settings=settings)
			await session.start()
		empty_state = SerializedDOMState(
			_root=None, selector_map={}, element_tree_text="", file_input_backend_ids=[],
		)
		with patch("tree_walker.browser.session.build_dom_state", return_value=(empty_state, MagicMock())):
			state = await session.get_state(include_screenshot=True)
		assert state.screenshot is None

	@pytest.mark.asyncio
	async def test_get_state_wait_settle_invokes_page_settle(self):
		"""get_state(wait_settle=True) 在读 DOM 前调用 _wait_for_page_settle。"""
		from tree_walker.browser.views import SerializedDOMState
		client = _make_mock_cdp_client()
		session = await _start_session(client)
		session._wait_for_page_settle = AsyncMock()
		empty_state = SerializedDOMState(
			_root=None, selector_map={}, element_tree_text="", file_input_backend_ids=[],
		)
		with patch("tree_walker.browser.session.build_dom_state", return_value=(empty_state, MagicMock())):
			await session.get_state(include_screenshot=False, wait_settle=True)
		session._wait_for_page_settle.assert_awaited_once()

	@pytest.mark.asyncio
	async def test_get_state_wait_settle_false_skips(self):
		"""默认 wait_settle=False 不触发 settle（零行为变更保证）。"""
		from tree_walker.browser.views import SerializedDOMState
		client = _make_mock_cdp_client()
		session = await _start_session(client)
		session._wait_for_page_settle = AsyncMock()
		empty_state = SerializedDOMState(
			_root=None, selector_map={}, element_tree_text="", file_input_backend_ids=[],
		)
		with patch("tree_walker.browser.session.build_dom_state", return_value=(empty_state, MagicMock())):
			await session.get_state(include_screenshot=False, wait_settle=False)
		session._wait_for_page_settle.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_get_state_wait_settle_failure_does_not_block(self):
		"""_wait_for_page_settle 抛异常时 get_state 仍正常返回（仿 screenshot 容错）。"""
		from tree_walker.browser.views import SerializedDOMState
		client = _make_mock_cdp_client()
		session = await _start_session(client)
		session._wait_for_page_settle = AsyncMock(side_effect=RuntimeError("settle boom"))
		empty_state = SerializedDOMState(
			_root=None, selector_map={}, element_tree_text="", file_input_backend_ids=[],
		)
		with patch("tree_walker.browser.session.build_dom_state", return_value=(empty_state, MagicMock())):
			state = await session.get_state(include_screenshot=False, wait_settle=True)
		assert state is not None


# ── _action_screenshot: tool layer ────────────────────────────────────


def _make_mock_browser(take_screenshot_return=b"\x89PNG\r\n\x1a\n", take_screenshot_side_effect=None):
	browser = MagicMock()
	if take_screenshot_side_effect is not None:
		browser.take_screenshot = AsyncMock(side_effect=take_screenshot_side_effect)
	else:
		browser.take_screenshot = AsyncMock(return_value=take_screenshot_return)
	return browser


class TestActionScreenshot:
	@pytest.mark.asyncio
	async def test_save_path_writes_bytes(self, tmp_path):
		data = b"\x89PNG\r\n\x1a\nfakepng"
		browser = _make_mock_browser(take_screenshot_return=data)
		out = tmp_path / "shot.png"
		result = await Tools().execute("screenshot", {"save_path": str(out)}, browser)
		assert out.read_bytes() == data
		assert result.error is None
		assert "saved" in result.extracted_content
		assert str(len(data)) in result.extracted_content

	@pytest.mark.asyncio
	async def test_no_save_path_returns_readable_meta(self):
		browser = _make_mock_browser(take_screenshot_return=b"x" * 100)
		result = await Tools().execute("screenshot", {}, browser)
		assert result.error is None
		assert "captured" in result.extracted_content
		assert "100" in result.extracted_content  # byte count echoed

	@pytest.mark.asyncio
	async def test_no_save_path_meta_includes_full_page(self):
		browser = _make_mock_browser(take_screenshot_return=b"x" * 10)
		result = await Tools().execute("screenshot", {"full_page": True}, browser)
		assert "full_page" in result.extracted_content

	@pytest.mark.asyncio
	async def test_failure_returns_error_result(self):
		browser = _make_mock_browser(take_screenshot_side_effect=RuntimeError("boom"))
		result = await Tools().execute("screenshot", {}, browser)
		assert result.error is not None
		assert "Screenshot failed" in result.error
		# exception must not escape the action
		assert result.extracted_content is None

	@pytest.mark.asyncio
	async def test_params_passthrough_clip_and_full_page(self):
		browser = _make_mock_browser(take_screenshot_return=b"data")
		clip = {"x": 0, "y": 0, "width": 100, "height": 100}
		await Tools().execute("screenshot", {"clip": clip, "full_page": True}, browser)
		browser.take_screenshot.assert_awaited_once()
		kwargs = browser.take_screenshot.call_args.kwargs
		assert kwargs["clip"] == clip
		assert kwargs["full_page"] is True
		assert kwargs["wait_settle"] is True  # wait_settle follows full_page

	@pytest.mark.asyncio
	async def test_viewport_shot_does_not_wait_settle(self):
		"""Viewport quick-shots must stay fast: wait_settle only on full_page."""
		browser = _make_mock_browser(take_screenshot_return=b"data")
		await Tools().execute("screenshot", {"full_page": False}, browser)
		kwargs = browser.take_screenshot.call_args.kwargs
		assert kwargs["wait_settle"] is False

	@pytest.mark.asyncio
	async def test_save_failure_returns_error(self, tmp_path):
		"""save_path pointing at a directory → OSError surfaced as ActionResult.error."""
		browser = _make_mock_browser(take_screenshot_return=b"data")
		result = await Tools().execute("screenshot", {"save_path": str(tmp_path)}, browser)
		assert result.error is not None
		assert "Failed to save" in result.error


# ── resize_screenshot_bytes: downscaling helper ───────────────────────


class TestResizeScreenshotBytes:
	@staticmethod
	def _png_bytes(w, h):
		pytest.importorskip("PIL")
		from PIL import Image
		buf = io.BytesIO()
		Image.new("RGB", (w, h), (255, 0, 0)).save(buf, format="PNG")
		return buf.getvalue()

	def test_no_target_is_noop(self):
		from tree_walker.browser.image_utils import resize_screenshot_bytes
		b = self._png_bytes(2000, 1000)
		assert resize_screenshot_bytes(b, None) is b

	def test_empty_data_is_noop(self):
		from tree_walker.browser.image_utils import resize_screenshot_bytes
		assert resize_screenshot_bytes(b"", (1400, 850)) == b""

	def test_already_small_is_idempotent(self):
		from tree_walker.browser.image_utils import resize_screenshot_bytes
		b = self._png_bytes(100, 100)
		assert resize_screenshot_bytes(b, (1400, 850)) is b

	def test_downscales_within_target(self):
		from tree_walker.browser.image_utils import resize_screenshot_bytes
		from PIL import Image
		b = self._png_bytes(2000, 1000)
		out = resize_screenshot_bytes(b, (1400, 850))
		# scale = min(1400/2000, 850/1000) = 0.7 → 1400x700, width binds
		img = Image.open(io.BytesIO(out))
		assert img.size == (1400, 700)

	def test_downscaled_output_is_png(self):
		from tree_walker.browser.image_utils import resize_screenshot_bytes
		b = self._png_bytes(2000, 1000)
		out = resize_screenshot_bytes(b, (1400, 850))
		assert out[:8] == b"\x89PNG\r\n\x1a\n"  # PNG signature

	def test_invalid_bytes_returns_original(self):
		from tree_walker.browser.image_utils import resize_screenshot_bytes
		bad = b"not an image"
		assert resize_screenshot_bytes(bad, (1400, 850)) is bad

	def test_resize_operation_failure_returns_original(self):
		"""If PIL resize/save raises, the original bytes are returned (never raises)."""
		pytest.importorskip("PIL")
		from PIL import Image
		from tree_walker.browser import image_utils
		b = self._png_bytes(2000, 1000)
		fake_img = MagicMock()
		fake_img.size = (2000, 1000)
		fake_img.resize.side_effect = RuntimeError("resize boom")
		with patch.object(Image, "open", return_value=fake_img):
			assert image_utils.resize_screenshot_bytes(b, (1400, 850)) is b

	def test_target_below_floor_is_noop(self):
		from tree_walker.browser.image_utils import resize_screenshot_bytes
		b = self._png_bytes(2000, 1000)
		assert resize_screenshot_bytes(b, (50, 50)) is b

	def test_pillow_missing_is_noop(self):
		"""When Pillow is unavailable, resize degrades to returning the input."""
		from tree_walker.browser import image_utils
		b = self._png_bytes(2000, 1000)
		with patch.object(image_utils, "_PILLOW_AVAILABLE", False):
			assert image_utils.resize_screenshot_bytes(b, (1400, 850)) is b

	def test_is_resize_available_returns_bool(self):
		from tree_walker.browser.image_utils import is_resize_available
		assert isinstance(is_resize_available(), bool)
