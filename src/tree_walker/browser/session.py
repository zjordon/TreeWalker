"""Browser session management via cdp-use CDP WebSocket client."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Any

from cdp_use import CDPClient

from tree_walker.browser.circuit_breaker import CircuitBreaker
from tree_walker.browser.dom import build_dom_state
from tree_walker.browser.highlight import HighlightManager
from tree_walker.config import BrowserSettings
from tree_walker.browser.views import (
    BrowserStateSummary,
    DOMCollectionConfig,
    DOMDegradationLevel,
    DOMRect,
    DOMSelectorMap,
    TabInfo,
)

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────


def _walk_for_file_inputs(node: dict) -> list[int]:
    """Recursively walk a CDP DOM Node tree to find file input backendNodeIds."""
    results: list[int] = []

    node_name = node.get("nodeName", "").upper()
    if node_name == "INPUT":
        attrs_list = node.get("attributes", [])
        attrs = {}
        for i in range(0, len(attrs_list) - 1, 2):
            attrs[attrs_list[i]] = attrs_list[i + 1]
        if attrs.get("type", "").lower() == "file":
            bid = node.get("backendNodeId")
            if bid is not None:
                results.append(bid)

    for child in node.get("children", []):
        results.extend(_walk_for_file_inputs(child))

    for shadow_root in node.get("shadowRoots", []):
        results.extend(_walk_for_file_inputs(shadow_root))

    content_doc = node.get("contentDocument")
    if content_doc:
        results.extend(_walk_for_file_inputs(content_doc))

    return results


# Virtual key codes for send_keys
_KEY_VK_MAP: dict[str, int] = {
    "enter": 13, "tab": 9, "escape": 27, "backspace": 8,
    "arrowup": 38, "arrowdown": 40, "arrowleft": 37, "arrowright": 39,
}

# Char text for keys that need it
_KEY_CHAR_TEXT: dict[str, str] = {
    "enter": "\r",
    "tab": "\t",
}


def _get_char_modifiers_and_vk(char: str) -> tuple[int, int, str]:
    """Return (modifiers, windowsVirtualKeyCode, base_key) for a character."""
    shift_chars: dict[str, tuple[str, int]] = {
        "!": ("1", 49), "@": ("2", 50), "#": ("3", 51), "$": ("4", 52),
        "%": ("5", 53), "^": ("6", 54), "&": ("7", 55), "*": ("8", 56),
        "(": ("9", 57), ")": ("0", 48), "_": ("-", 189), "+": ("=", 187),
        "{": ("[", 219), "}": ("]", 221), "|": ("\\", 220),
        ":": (";", 186), '"': ("'", 222), "<": (",", 188),
        ">": (".", 190), "?": ("/", 191), "~": ("`", 192),
    }
    if char in shift_chars:
        base_key, vk = shift_chars[char]
        return (8, vk, base_key)
    if char.isupper():
        return (8, ord(char), char.lower())
    if char.islower():
        return (0, ord(char.upper()), char)
    if char.isdigit():
        return (0, ord(char), char)
    no_shift: dict[str, int] = {
        " ": 32, "-": 189, "=": 187, "[": 219, "]": 221,
        "\\": 220, ";": 186, "'": 222, ",": 188, ".": 190,
        "/": 191, "`": 192,
    }
    if char in no_shift:
        return (0, no_shift[char], char)
    return (0, ord(char.upper()) if char.isalpha() else ord(char), char)


def _get_key_code_for_char(char: str) -> str:
    """Return the DOM key code string for a character."""
    key_codes: dict[str, str] = {
        " ": "Space", ".": "Period", ",": "Comma", "-": "Minus",
        "@": "Digit2", "!": "Digit1", "?": "Slash", ":": "Semicolon",
        ";": "Semicolon", "(": "Digit9", ")": "Digit0",
        "[": "BracketLeft", "]": "BracketRight",
        "/": "Slash", "\\": "Backslash", "=": "Equal",
        "+": "Equal", "*": "Digit8", "&": "Digit7",
        "%": "Digit5", "$": "Digit4", "#": "Digit3",
        "^": "Digit6", "~": "Backquote", "`": "Backquote",
        "'": "Quote", '"': "Quote", "_": "Minus",
        "{": "BracketLeft", "}": "BracketRight", "|": "Backslash",
        "<": "Comma", ">": "Period",
    }
    if char.isdigit():
        return f"Digit{char}"
    if char.isalpha():
        return f"Key{char.upper()}"
    return key_codes.get(char, f"Key{char.upper()}")


class BrowserSession:
    """Manages browser connection and provides high-level page operations."""

    def __init__(self, settings: BrowserSettings | None = None, *, ws_url: str | None = None) -> None:
        _settings = settings or BrowserSettings()
        self.ws_url = ws_url or _settings.ws_url or ""
        if not self.ws_url:
            raise ValueError("BrowserSession requires a ws_url via settings or keyword argument")
        self._settings = _settings
        self.client: CDPClient | None = None
        self.current_target_id: str | None = None
        self.current_session_id: str | None = None
        self._completed_downloads: list[dict] = []
        self._pending_downloads: dict[str, str] = {}
        # 三层 DOM 缓存
        self._cached_selector_map: DOMSelectorMap | None = None
        self._previous_cached_selector_map: DOMSelectorMap | None = None
        # DOM 管线健壮性
        self._dom_circuit_breaker = CircuitBreaker(
            failure_threshold=_settings.circuit_breaker_threshold,
            recovery_timeout=_settings.circuit_breaker_recovery_s,
        )
        self._dom_collection_config = DOMCollectionConfig(
            cdp_first_timeout=_settings.cdp_first_timeout,
            cdp_retry_timeout=_settings.cdp_retry_timeout,
            max_iframes=_settings.max_iframes,
            heavy_page_element_threshold=_settings.heavy_page_element_threshold,
        )
        self._highlight = HighlightManager(
            settings=_settings.highlight,
            execute_js=self.execute_js,
            client=None,
            session_id=None,
        )
        self._highlight_settings = _settings.highlight

    async def start(self, *, track_downloads: bool = False) -> None:
        """Connect to the browser via CDP WebSocket."""
        self.client = CDPClient(self.ws_url)
        await self._connect()
        if track_downloads:
            await self._setup_download_tracking()

    async def _connect(self) -> None:
        """Perform CDP connection, target discovery, and session setup."""
        await self.client.start()

        targets = await self.client.send.Target.getTargets({})
        for t in targets.get("targetInfos", []):
            if t.get("type") == "page":
                self.current_target_id = t["targetId"]
                result = await self.client.send.Target.attachToTarget(
                    {"targetId": self.current_target_id, "flatten": True},
                )
                self.current_session_id = result["sessionId"]
                break

        if not self.current_session_id:
            raise RuntimeError("No page target found. Is Chrome running with --remote-debugging-port?")

        await self.client.send.Page.enable({}, session_id=self.current_session_id)
        await self.client.send.DOM.enable({}, session_id=self.current_session_id)

        # 自动发现 iframe target，确保跨源 iframe 可被 Target.getTargets 发现
        try:
            await self.client.send.Target.setAutoAttach(
                {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True},
                session_id=self.current_session_id,
            )
        except Exception:
            pass

        logger.info("Browser connected: target=%s", self.current_target_id)

        # Wire up highlight manager with live CDP client
        self._highlight._client = self.client
        self._highlight._session_id = self.current_session_id

    @property
    def is_connected(self) -> bool:
        """Whether the browser session has an active CDP client."""
        return self.client is not None

    async def reconnect(self) -> bool:
        """Attempt to reconnect to the browser. Returns True on success."""
        try:
            if self.client:
                await self.client.stop()
            self.client = CDPClient(self.ws_url)
            self.current_target_id = None
            self.current_session_id = None
            self._cached_selector_map = None
            self._previous_cached_selector_map = None
            self._dom_circuit_breaker.reset()
            await self._connect()
            self._highlight._client = self.client
            self._highlight._session_id = self.current_session_id
            return True
        except Exception as e:
            logger.warning("Reconnect failed: %s", e)
            self.client = None
            return False

    async def _setup_download_tracking(self) -> None:
        """Enable CDP download events and register callbacks."""
        await self.client.send.Browser.setDownloadBehavior(
            {"behavior": "allow", "eventsEnabled": True},
            session_id=self.current_session_id,
        )

        def _on_download_begin(event: dict, session_id: str | None = None) -> None:
            guid = event.get("guid", "")
            filename = event.get("suggestedFilename", "unknown")
            self._pending_downloads[guid] = filename
            logger.info("Download started: %s", filename)

        async def _on_download_progress(event: dict, session_id: str | None = None) -> None:
            if event.get("state") == "completed":
                guid = event.get("guid", "")
                filename = self._pending_downloads.pop(guid, "unknown")
                self._completed_downloads.append({
                    "filename": filename,
                    "url": event.get("url", ""),
                    "path": event.get("filePath"),
                })
                logger.info("Download completed: %s", filename)

        self.client.register.Browser.downloadWillBegin(_on_download_begin)
        self.client.register.Browser.downloadProgress(_on_download_progress)

    def consume_completed_downloads(self) -> list[dict]:
        """Return and clear completed downloads buffer."""
        downloads = list(self._completed_downloads)
        self._completed_downloads.clear()
        return downloads

    async def stop(self) -> None:
        """Disconnect from the browser."""
        self._cached_selector_map = None
        self._previous_cached_selector_map = None
        if self.client:
            await self.client.stop()
            self.client = None
            logger.info("Browser disconnected")

    # ── State ──────────────────────────────────────────────────────────

    async def get_current_url(self) -> str:
        """Lightweight URL fetch — avoids full state rebuild."""
        try:
            result = await self.client.send.Runtime.evaluate(
                {"expression": "location.href", "returnByValue": True},
                session_id=self.current_session_id,
            )
            return result.get("result", {}).get("value", "")
        except Exception:
            return ""

    async def get_state(self, include_screenshot: bool = True) -> BrowserStateSummary:
        """Get full browser state: URL, title, tabs, DOM, optional screenshot."""
        sid = self.current_session_id

        # 轮转缓存：当前 → 前一步（用于新元素检测）
        self._previous_cached_selector_map = self._cached_selector_map

        url = ""
        title = ""
        try:
            result = await self.client.send.Runtime.evaluate(
                {
                    "expression": "JSON.stringify({url: location.href, title: document.title})",
                    "returnByValue": True,
                },
                session_id=sid,
            )
            import json
            info = json.loads(result["result"]["value"])
            url = info.get("url", "")
            title = info.get("title", "")
        except Exception:
            pass

        tabs: list[TabInfo] = []
        try:
            targets = await self.client.send.Target.getTargets({})
            for t in targets.get("targetInfos", []):
                if t.get("type") == "page":
                    tabs.append(TabInfo(
                        target_id=t["targetId"],
                        url=t.get("url", ""),
                        title=t.get("title", ""),
                    ))
        except Exception:
            pass

        dom_state = None
        if self._dom_circuit_breaker.is_open:
            logger.warning("DOM circuit breaker is open; returning empty DOM state")
            from tree_walker.browser.dom import EMPTY_DOM_STATE
            dom_state = EMPTY_DOM_STATE
        else:
            try:
                dom_state, dom_metrics = await build_dom_state(
                    self.client, session_id=sid,
                    previous_selector_map=self._previous_cached_selector_map,
                    config=self._dom_collection_config,
                )
                if dom_metrics.degradation_level == DOMDegradationLevel.FAILED:
                    self._dom_circuit_breaker.record_failure()
                else:
                    self._dom_circuit_breaker.record_success()
            except Exception as e:
                logger.error("build_dom_state raised: %s", e)
                self._dom_circuit_breaker.record_failure()
                from tree_walker.browser.dom import EMPTY_DOM_STATE
                dom_state = EMPTY_DOM_STATE
        self._cached_selector_map = dom_state.selector_map if dom_state else None

        # Remove JS-injected highlights before screenshot
        if self._highlight_settings.enabled and self._highlight_settings.debug_mode:
            try:
                await self._highlight.remove_highlights()
            except Exception:
                pass

        screenshot: bytes | None = None
        if include_screenshot:
            try:
                screenshot = await self.take_screenshot()
            except Exception:
                pass

        # Re-inject debug highlights after screenshot (visible in browser, not in screenshot)
        if self._highlight_settings.enabled and self._highlight_settings.debug_mode and self._cached_selector_map:
            try:
                await self._highlight.add_debug_highlights(self._cached_selector_map)
            except Exception:
                pass

        return BrowserStateSummary(
            url=url,
            title=title,
            tabs=tabs,
            dom_state=dom_state,
            screenshot=screenshot,
        )

    async def take_screenshot(self) -> bytes:
        """Capture a PNG screenshot of the current viewport."""
        result = await self.client.send.Page.captureScreenshot(
            {"format": "png"},
            session_id=self.current_session_id,
        )
        return base64.b64decode(result["data"])

    # ── Navigation ─────────────────────────────────────────────────────

    async def navigate(self, url: str, new_tab: bool = False) -> str | None:
        """Navigate to URL. If new_tab=True, open a new tab first and navigate there.

        Raises ``RuntimeError`` with the CDP errorText if navigation fails (e.g.
        net::ERR_NAME_NOT_RESOLVED). Returns the new tab's target_id when
        new_tab=True, else None.
        """
        if new_tab:
            # 先开空白标签页（create_tab 已切换到它），再走 Page.navigate 以保留 errorText 检查
            target_id = await self.create_tab("about:blank")
        else:
            target_id = None

        # 导航会改变页面，两层 selector_map 缓存都要清（与 switch_tab 一致）
        self._cached_selector_map = None
        self._previous_cached_selector_map = None

        result = await self.client.send.Page.navigate(
            {"url": url, "transitionType": "address_bar"},
            session_id=self.current_session_id,
        )
        # errorText 仅在导航失败时存在（CDP：present if and only if navigation has failed）
        error_text = result.get("errorText") if isinstance(result, dict) else None
        if error_text:
            raise RuntimeError(f"Navigation failed: {error_text}")
        await self._wait_for_page_settle()
        return target_id

    async def go_back(self) -> None:
        """Navigate to the previous page in history."""
        history = await self.client.send.Page.getNavigationHistory(
            {}, session_id=self.current_session_id,
        )
        idx = history.get("currentIndex", 0)
        entries = history.get("entries", [])
        if idx > 0 and entries:
            await self.client.send.Page.navigateToHistoryEntry(
                {"entryId": entries[idx - 1]["id"]},
                session_id=self.current_session_id,
            )
            await self._wait_for_page_settle()

    async def _wait_for_page_settle(
        self,
        timeout: float | None = None,
        poll_interval: float | None = None,
    ) -> None:
        """Poll document.readyState until 'complete' or timeout.

        Replaces hard-coded ``asyncio.sleep`` after navigation actions. Falls
        through silently on timeout — caller proceeds with whatever state the
        page reached. Returns immediately if the CDP client is unavailable.
        """
        if self.client is None or self.current_session_id is None:
            return
        timeout = timeout if timeout is not None else self._settings.page_settle_timeout
        poll_interval = (
            poll_interval if poll_interval is not None
            else self._settings.page_settle_poll_interval
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                result = await self.client.send.Runtime.evaluate(
                    {"expression": "document.readyState", "returnByValue": True},
                    session_id=self.current_session_id,
                )
                state = result.get("result", {}).get("value", "")
                if state == "complete":
                    return
            except Exception:
                # CDP hiccup — retry on next poll rather than aborting
                pass
            await asyncio.sleep(poll_interval)

    # ── Element interaction ────────────────────────────────────────────

    async def highlight_element(self, backend_node_id: int) -> None:
        """Highlight an element for visual feedback (non-blocking, non-critical)."""
        await self._highlight.highlight_element(backend_node_id)

    async def click_at(self, x: float, y: float) -> None:
        """Click at viewport coordinates."""
        sid = self.current_session_id
        for evt_type in ("mousePressed", "mouseReleased"):
            await self.client.send.Input.dispatchMouseEvent(
                {
                    "type": evt_type,
                    "x": x,
                    "y": y,
                    "button": "left",
                    "clickCount": 1,
                },
                session_id=sid,
            )
        await asyncio.sleep(0.3)
        if self._highlight_settings.enabled and self._highlight_settings.click_feedback_enabled:
            await self._highlight.highlight_click_point(x, y)

    async def get_element_coordinates(self, backend_node_id: int) -> DOMRect | None:
        """Get real-time viewport coordinates for an element via CDP.

        Three-tier fallback chain (same as browser-use):
        1. DOM.getContentQuads — best for inline/complex layouts
        2. DOM.getBoxModel — fallback using box model content
        3. JS getBoundingClientRect() via DOM.resolveNode + Runtime.callFunctionOn
        """
        sid = self.current_session_id

        # Method 1: DOM.getContentQuads
        try:
            result = await self.client.send.DOM.getContentQuads(
                {"backendNodeId": backend_node_id},
                session_id=sid,
            )
            quads = result.get("quads", [])
            if quads:
                quad = quads[0]
                if len(quad) >= 8:
                    xs = [quad[i] for i in range(0, 8, 2)]
                    ys = [quad[i] for i in range(1, 8, 2)]
                    return DOMRect(
                        x=min(xs), y=min(ys),
                        width=max(xs) - min(xs),
                        height=max(ys) - min(ys),
                    )
        except Exception:
            pass

        # Method 2: DOM.getBoxModel
        try:
            result = await self.client.send.DOM.getBoxModel(
                {"backendNodeId": backend_node_id},
                session_id=sid,
            )
            model = result.get("model", {})
            content = model.get("content", [])
            if len(content) >= 8:
                xs = [content[i] for i in range(0, 8, 2)]
                ys = [content[i] for i in range(1, 8, 2)]
                return DOMRect(
                    x=min(xs), y=min(ys),
                    width=max(xs) - min(xs),
                    height=max(ys) - min(ys),
                )
        except Exception:
            pass

        # Method 3: JS getBoundingClientRect()
        try:
            resolve = await self.client.send.DOM.resolveNode(
                {"backendNodeId": backend_node_id},
                session_id=sid,
            )
            object_id = resolve["object"]["objectId"]
            js_result = await self.client.send.Runtime.callFunctionOn(
                {
                    "objectId": object_id,
                    "functionDeclaration": """
                    function() {
                        const rect = this.getBoundingClientRect();
                        return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
                    }
                    """,
                    "returnByValue": True,
                },
                session_id=sid,
            )
            rect = js_result.get("result", {}).get("value")
            if rect and rect.get("width", 0) > 0 and rect.get("height", 0) > 0:
                return DOMRect(
                    x=rect["x"], y=rect["y"],
                    width=rect["width"], height=rect["height"],
                )
        except Exception:
            pass

        return None

    async def click_element(self, backend_node_id: int) -> None:
        """Click an element using real-time CDP coordinates."""
        # Scroll into view first
        try:
            await self.client.send.DOM.scrollIntoViewIfNeeded(
                {"backendNodeId": backend_node_id},
                session_id=self.current_session_id,
            )
            await asyncio.sleep(0.05)
        except Exception:
            pass

        rect = await self.get_element_coordinates(backend_node_id)
        if rect:
            x = int(rect.x + rect.width / 2)
            y = int(rect.y + rect.height / 2)
            await self.click_at(x, y)
        else:
            logger.warning(
                "Could not get coordinates for backendNodeId=%d, skipping click",
                backend_node_id,
            )

    async def type_text(self, text: str, clear: bool = False) -> None:
        """Type text into the currently focused element character by character.

        Uses CDP keyDown → char → keyUp for each character to ensure
        framework event listeners (Vue v-model, React onChange) are triggered.
        After typing, dispatches framework-compatible events via JS.

        When clear=True, uses _clear_text_field (three-layer strategy) and
        runs a concatenation check at the end — if the field ended up with
        OLD+NEW text (clear silently failed), force-overwrites via native
        setter. Mirrors browser-use's _input_text_element_node_impl.
        """
        sid = self.current_session_id
        if clear:
            await self._clear_text_field()

        for char in text:
            await self._type_char(char, sid)
            await asyncio.sleep(0.001)

        await asyncio.sleep(0.05)
        await self._trigger_framework_events()

        # Concatenation guard: if clear was requested but the field still
        # contains the old text + new text (silent clear failure on a
        # site that swallows Ctrl+A / select), force-overwrite via native
        # setter. This is the last line of defense.
        #
        # _read_active_text and _force_set_value are both non-critical
        # (they catch their own exceptions), so no outer try/except needed.
        if clear:
            actual = await self._read_active_text()
            if (
                isinstance(actual, str)
                and actual != text
                and len(actual) > len(text)
                and (actual.endswith(text) or actual.startswith(text))
            ):
                logger.info(
                    "Concatenation detected (%r), force-overwriting via native setter",
                    actual,
                )
                await self._force_set_value(text)

    async def _read_active_text(self) -> str:
        """Read activeElement.value (input/textarea) or textContent (contenteditable)."""
        try:
            result = await self.client.send.Runtime.evaluate(
                {
                    "expression": """(function() {
                        var el = document.activeElement;
                        if (!el || el === document.body) return '';
                        if (el.value !== undefined) return el.value;
                        return el.textContent || '';
                    })()""",
                    "returnByValue": True,
                },
                session_id=self.current_session_id,
            )
            value = (result.get("result") or {}).get("value")
            return value if isinstance(value, str) else ""
        except Exception as e:
            logger.debug("_read_active_text failed: %s", e)
            return ""

    async def _force_set_value(self, text: str) -> None:
        """Force-overwrite activeElement value via native setter.

        For input/textarea: uses Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,
        'value').set to bypass React/Vue tracking.
        For contenteditable: assigns textContent directly.
        Dispatches input + change so frameworks notice the change.

        Note: we DO dispatch 'change' here — unlike _trigger_framework_events
        which intentionally omits it. This path only runs when concatenation
        was detected (silent clear failure), and the element is still focused,
        so there's no blur-related side effect to worry about.
        """
        try:
            escaped = json.dumps(text)
            await self.client.send.Runtime.evaluate(
                {
                    "expression": f"""
                    (function() {{
                        var el = document.activeElement;
                        if (!el || el === document.body) return;
                        var tag = el.tagName.toLowerCase();
                        if (tag === 'input' || tag === 'textarea') {{
                            var proto = tag === 'input'
                                ? HTMLInputElement.prototype
                                : HTMLTextAreaElement.prototype;
                            var desc = Object.getOwnPropertyDescriptor(proto, 'value');
                            if (desc && desc.set) {{
                                desc.set.call(el, {escaped});
                            }} else {{
                                el.value = {escaped};
                            }}
                            el.dispatchEvent(new Event('input', {{bubbles: true}}));
                            el.dispatchEvent(new Event('change', {{bubbles: true}}));
                        }} else if (el.isContentEditable) {{
                            el.textContent = {escaped};
                            el.dispatchEvent(new InputEvent('input', {{
                                bubbles: true, inputType: 'insertText'
                            }}));
                            el.dispatchEvent(new Event('change', {{bubbles: true}}));
                        }}
                    }})()
                    """,
                    "returnByValue": True,
                },
                session_id=self.current_session_id,
            )
        except Exception as e:
            logger.debug("_force_set_value failed: %s", e)

    async def _clear_text_field(self) -> bool:
        """Three-layer clear strategy, mirrors browser-use _clear_text_field.

        Returns True if activeElement.value/textContent is empty after some layer.
        Strategy 1: JS select() + value='' (covers input/textarea/contenteditable).
        Strategy 2: Triple-click + Delete (mouse-based fallback).
        Strategy 3: Ctrl+A + Backspace (keyboard last resort).
        """
        sid = self.current_session_id

        # Strategy 1: JS select() + value=''
        try:
            result = await self.client.send.Runtime.evaluate(
                {
                    "expression": """
                    (function() {
                        var el = document.activeElement;
                        if (!el || el === document.body) return {cleared: false, error: 'no active'};
                        el.focus();
                        if (el.isContentEditable) {
                            var sel = window.getSelection();
                            var range = document.createRange();
                            range.selectNodeContents(el);
                            sel.removeAllRanges();
                            sel.addRange(range);
                            el.textContent = '';
                            el.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'deleteContent'}));
                            el.dispatchEvent(new Event('change', {bubbles: true}));
                            return {cleared: true, method: 'contenteditable', final: el.textContent};
                        }
                        if (el.value !== undefined) {
                            try { el.select(); } catch(e) {}
                            el.value = '';
                            el.dispatchEvent(new Event('input', {bubbles: true}));
                            el.dispatchEvent(new Event('change', {bubbles: true}));
                            return {cleared: true, method: 'value', final: el.value};
                        }
                        return {cleared: false, error: 'unsupported element'};
                    })()
                    """,
                    "returnByValue": True,
                },
                session_id=sid,
            )
            info_raw = (result.get("result") or {}).get("value")
            info = info_raw if isinstance(info_raw, dict) else {}
            final_raw = info.get("final")
            final = final_raw.strip() if isinstance(final_raw, str) else ""
            if info.get("cleared") and not final:
                return True
        except Exception as e:
            logger.debug("_clear_text_field strategy 1 failed: %s", e)

        # Strategy 2: Triple-click + Delete
        try:
            coord_result = await self.client.send.Runtime.evaluate(
                {
                    "expression": """(function() {
                        var el = document.activeElement;
                        if (!el || el === document.body) return null;
                        var r = el.getBoundingClientRect();
                        return JSON.stringify({x: r.x + r.width/2, y: r.y + r.height/2});
                    })()""",
                    "returnByValue": True,
                },
                session_id=sid,
            )
            coord_json = (coord_result.get("result") or {}).get("value")
            if coord_json:
                c = json.loads(coord_json)
                await self.client.send.Input.dispatchMouseEvent(
                    {
                        "type": "mousePressed",
                        "x": c["x"],
                        "y": c["y"],
                        "button": "left",
                        "clickCount": 3,
                    },
                    session_id=sid,
                )
                await self.client.send.Input.dispatchMouseEvent(
                    {
                        "type": "mouseReleased",
                        "x": c["x"],
                        "y": c["y"],
                        "button": "left",
                        "clickCount": 3,
                    },
                    session_id=sid,
                )
                await self.client.send.Input.dispatchKeyEvent(
                    {"type": "keyDown", "key": "Delete", "code": "Delete"},
                    session_id=sid,
                )
                await self.client.send.Input.dispatchKeyEvent(
                    {"type": "keyUp", "key": "Delete", "code": "Delete"},
                    session_id=sid,
                )
                if await self._read_active_text() == "":
                    return True
        except Exception as e:
            logger.debug("_clear_text_field strategy 2 failed: %s", e)

        # Strategy 3: Ctrl+A + Backspace (last resort)
        try:
            await self.client.send.Input.dispatchKeyEvent(
                {"type": "keyDown", "key": "a", "code": "KeyA", "modifiers": 2},
                session_id=sid,
            )
            await self.client.send.Input.dispatchKeyEvent(
                {"type": "keyUp", "key": "a", "code": "KeyA", "modifiers": 2},
                session_id=sid,
            )
            await self.client.send.Input.dispatchKeyEvent(
                {"type": "keyDown", "key": "Backspace", "code": "Backspace"},
                session_id=sid,
            )
            await self.client.send.Input.dispatchKeyEvent(
                {"type": "keyUp", "key": "Backspace", "code": "Backspace"},
                session_id=sid,
            )
            return await self._read_active_text() == ""
        except Exception as e:
            logger.debug("_clear_text_field strategy 3 failed: %s", e)
            return False

    async def _type_char(self, char: str, sid: str | None = None) -> None:
        """Send a single character as keyDown → char → keyUp."""
        if sid is None:
            sid = self.current_session_id
        modifiers, vk_code, base_key = _get_char_modifiers_and_vk(char)
        key_code = _get_key_code_for_char(base_key)

        # For non-ASCII characters (CJK, etc.), skip keyDown/keyUp and
        # use insertText for the char event only
        is_ascii = ord(char) < 128

        if is_ascii:
            await self.client.send.Input.dispatchKeyEvent(
                {
                    "type": "keyDown",
                    "key": base_key,
                    "code": key_code,
                    "modifiers": modifiers,
                    "windowsVirtualKeyCode": vk_code,
                },
                session_id=sid,
            )
            await asyncio.sleep(0.005)

        await self.client.send.Input.dispatchKeyEvent(
            {"type": "char", "text": char, "key": char},
            session_id=sid,
        )

        if is_ascii:
            await self.client.send.Input.dispatchKeyEvent(
                {
                    "type": "keyUp",
                    "key": base_key,
                    "code": key_code,
                    "modifiers": modifiers,
                    "windowsVirtualKeyCode": vk_code,
                },
                session_id=sid,
            )

    async def _trigger_framework_events(self) -> None:
        """Dispatch framework-compatible DOM events on the focused element.

        Triggers InputEvent('input') (primary for React/Vue v-model) and a
        deferred Event('input') for Vue reactivity. We intentionally do NOT
        dispatch 'change' or 'blur' — those can trigger framework side
        effects (e.g. a tag-input clearing its value on blur) that wipe
        the value we just typed.

        Best-effort — failures are logged but do not raise.
        """
        try:
            await self.client.send.Runtime.evaluate(
                {
                    "expression": """
                    (function() {
                        var el = document.activeElement;
                        if (!el || el === document.body) return false;

                        el.focus();

                        // InputEvent — primary for React/Vue v-model
                        try {
                            el.dispatchEvent(new InputEvent('input', {
                                bubbles: true,
                                cancelable: true,
                                data: el.value,
                                inputType: 'insertText'
                            }));
                        } catch(e) {}

                        // Vue reactivity trigger — check element AND ancestors
                        var hasVue = el.__vue__ || el._vnode || el.__vueParentComponent__;
                        if (!hasVue) {
                            var p = el.parentElement;
                            while (p && p !== document.body) {
                                if (p.__vue__ || p._vnode || p.__vueParentComponent__) {
                                    hasVue = true;
                                    break;
                                }
                                p = p.parentElement;
                            }
                        }
                        if (hasVue) {
                            try {
                                setTimeout(function() {
                                    el.dispatchEvent(new Event('input', {bubbles: true}));
                                }, 0);
                            } catch(e) {}
                        }

                        return true;
                    })()
                    """,
                    "returnByValue": True,
                },
                session_id=self.current_session_id,
            )
        except Exception as e:
            logger.debug("Framework event trigger failed (non-critical): %s", e)

    async def send_keys(self, keys: str) -> None:
        """Send key combinations like 'Enter', 'Control+a', etc."""
        sid = self.current_session_id
        key_map = {
            "enter": ("Enter", "Enter"),
            "tab": ("Tab", "Tab"),
            "escape": ("Escape", "Escape"),
            "backspace": ("Backspace", "Backspace"),
        }
        modifier_map = {"control": 2, "alt": 1, "shift": 8, "meta": 4}

        parts = keys.replace("+", "+").split("+")
        modifiers = 0
        main_key = parts[-1].strip()

        for part in parts[:-1]:
            mod = modifier_map.get(part.strip().lower(), 0)
            modifiers |= mod

        mapped = key_map.get(main_key.lower(), (main_key, f"Key{main_key.upper()}" if len(main_key) == 1 else main_key))

        await self.client.send.Input.dispatchKeyEvent(
            {
                "type": "keyDown",
                "key": mapped[0],
                "code": mapped[1],
                "modifiers": modifiers,
                "windowsVirtualKeyCode": _KEY_VK_MAP.get(mapped[0].lower(), 0),
            },
            session_id=sid,
        )

        # Enter and Tab need a char event for proper event handling
        char_text = _KEY_CHAR_TEXT.get(mapped[0].lower())
        if char_text:
            await self.client.send.Input.dispatchKeyEvent(
                {"type": "char", "text": char_text, "key": mapped[0]},
                session_id=sid,
            )

        await self.client.send.Input.dispatchKeyEvent(
            {
                "type": "keyUp",
                "key": mapped[0],
                "code": mapped[1],
                "modifiers": modifiers,
                "windowsVirtualKeyCode": _KEY_VK_MAP.get(mapped[0].lower(), 0),
            },
            session_id=sid,
        )
        await asyncio.sleep(0.1)

    # ── Scrolling ──────────────────────────────────────────────────────

    async def scroll(self, direction: str = "down", amount: int = 3) -> None:
        """Scroll the page by a number of viewport heights."""
        sid = self.current_session_id
        metrics = await self.client.send.Page.getLayoutMetrics(
            {}, session_id=sid,
        )
        viewport = metrics.get("cssVisualViewport", {})
        viewport_height = viewport.get("clientHeight", 800)
        delta = amount * viewport_height
        if direction == "up":
            delta = -delta

        await self.client.send.Input.dispatchMouseEvent(
            {
                "type": "mouseWheel",
                "x": viewport.get("clientWidth", 1280) / 2,
                "y": viewport.get("clientHeight", 800) / 2,
                "deltaX": 0,
                "deltaY": delta,
            },
            session_id=sid,
        )
        await asyncio.sleep(0.3)

    # ── Tabs ───────────────────────────────────────────────────────────

    async def switch_tab(self, target_id: str) -> None:
        """Switch to a different tab by target ID."""
        self._cached_selector_map = None
        self._previous_cached_selector_map = None
        await self.client.send.Target.activateTarget({"targetId": target_id})
        result = await self.client.send.Target.attachToTarget(
            {"targetId": target_id, "flatten": True},
        )
        self.current_target_id = target_id
        self.current_session_id = result["sessionId"]
        logger.info("Switched to tab: %s", target_id)
        await self._wait_for_page_settle()

    async def close_tab(self, target_id: str) -> None:
        """Close a tab. If it's the current tab, switch to another."""
        was_current = target_id == self.current_target_id
        await self.client.send.Target.closeTarget({"targetId": target_id})
        if was_current:
            targets = await self.client.send.Target.getTargets({})
            for t in targets.get("targetInfos", []):
                if t.get("type") == "page" and t["targetId"] != target_id:
                    await self.switch_tab(t["targetId"])
                    return

    async def create_tab(self, url: str = "about:blank") -> str:
        """Create a new tab and return its target ID."""
        result = await self.client.send.Target.createTarget({"url": url})
        target_id = result["targetId"]
        await self.switch_tab(target_id)
        return target_id

    # ── JavaScript execution ───────────────────────────────────────────

    async def execute_js(self, code: str) -> Any:
        """Execute JavaScript and return the result value."""
        result = await self.client.send.Runtime.evaluate(
            {
                "expression": code,
                "returnByValue": True,
                "awaitPromise": True,
                "timeout": 30000,
            },
            session_id=self.current_session_id,
        )
        if "exceptionDetails" in result:
            err = result["exceptionDetails"]
            raise RuntimeError(f"JS error: {err.get('text', err)}")
        return result.get("result", {}).get("value")

    # ── File operations (via CDP) ──────────────────────────────────────

    async def find_file_inputs_in_shadow_dom(self) -> list[int]:
        """Find all file input backendNodeIds including those inside shadow DOM.

        Uses DOM.getDocument with pierce=True to traverse into shadow roots.
        """
        try:
            result = await self.client.send.DOM.getDocument(
                {"depth": -1, "pierce": True},
                session_id=self.current_session_id,
            )
            return _walk_for_file_inputs(result.get("root", {}))
        except Exception as e:
            logger.warning("DOM.getDocument(pierce) failed: %s", e)
            return []

    async def _log_shadow_dom_file_inputs(self) -> list[int]:
        """Debug: find and log all shadow DOM file inputs."""
        ids = await self.find_file_inputs_in_shadow_dom()
        logger.info("Shadow DOM file inputs: count=%d, ids=%s", len(ids), ids)
        return ids

    async def set_file_input(
        self,
        backend_node_id: int | None,
        file_path: str,
        file_input_backend_ids: list[int] | None = None,
    ) -> None:
        """Set files on a file input element via CDP DOM.setFileInputFiles.

        Uses backendNodeId to directly set files without opening the OS file chooser.
        Falls back to file_input_backend_ids, then shadow DOM search.
        """
        target_id = backend_node_id

        logger.info(
            "set_file_input: backend_node_id=%s, file_input_backend_ids=%s, file=%s",
            backend_node_id, file_input_backend_ids, file_path,
        )

        # If the provided backendNodeId might not be a file input,
        # use the first available file input backend ID from the DOM state
        if target_id is None and file_input_backend_ids:
            target_id = file_input_backend_ids[0]

        # Last resort: search shadow DOM for file inputs
        if target_id is None:
            shadow_ids = await self.find_file_inputs_in_shadow_dom()
            if shadow_ids:
                target_id = shadow_ids[0]
                logger.info(
                    "Found file input in shadow DOM: backendNodeId=%d", target_id,
                )

        if target_id is None:
            raise RuntimeError(
                "No file input element found. "
                "Ensure the page has an <input type='file'> element."
            )

        logger.info("DOM.setFileInputFiles: backendNodeId=%d, file=%s", target_id, file_path)
        await self.client.send.DOM.setFileInputFiles(
            {"backendNodeId": target_id, "files": [file_path]},
            session_id=self.current_session_id,
        )
