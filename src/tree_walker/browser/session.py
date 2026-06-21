"""Browser session management via cdp-use CDP WebSocket client."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import secrets
import shutil
import tempfile
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


def _xpath_string_literal(text: str) -> str:
    """Build an XPath 1.0 string literal safe for contains().

    XPath 1.0 string literals cannot escape a quote inside their own
    delimiter, so: no double-quote -> wrap in "..."; no single-quote ->
    wrap in '...'; both present -> splice with concat(..., '"', ...).
    Mirrors browser-use's find_text intent but fixes its f-string injection
    bug (default_action_watchdog.py injects text into "..." verbatim, which
    breaks when the text contains a double-quote).
    """
    if '"' not in text:
        return f'"{text}"'
    if "'" not in text:
        return f"'{text}'"
    parts = text.split('"')
    return "concat(" + ", '\"', ".join(f'"{p}"' for p in parts) + ")"


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
    "enter": 13, "tab": 9, "escape": 27, "backspace": 8, "delete": 46,
    "arrowup": 38, "arrowdown": 40, "arrowleft": 37, "arrowright": 39,
    "pageup": 33, "pagedown": 34, "home": 36, "end": 35,
    **{f"f{i}": 0x70 + (i - 1) for i in range(1, 13)},  # F1=0x70 ... F12=0x7B
}

# Char text for keys that need it
_KEY_CHAR_TEXT: dict[str, str] = {
    "enter": "\r",
    "tab": "\t",
}

# Key aliases → canonical DOM key names (matched case-insensitively via .lower()).
# Mirrors browser-use default_action_watchdog.py key_aliases: lets callers write
# 'ctrl'/'control', 'return', 'esc', 'cmd', 'up'/'arrowup', 'pgup', 'f5', etc.
_KEY_ALIASES: dict[str, str] = {
    "ctrl": "Control", "control": "Control",
    "alt": "Alt", "option": "Alt",
    "meta": "Meta", "cmd": "Meta", "command": "Meta",
    "shift": "Shift",
    "enter": "Enter", "return": "Enter",
    "esc": "Escape", "escape": "Escape",
    "backspace": "Backspace",
    "delete": "Delete", "del": "Delete",
    "tab": "Tab",
    "space": " ",
    "up": "ArrowUp", "arrowup": "ArrowUp",
    "down": "ArrowDown", "arrowdown": "ArrowDown",
    "left": "ArrowLeft", "arrowleft": "ArrowLeft",
    "right": "ArrowRight", "arrowright": "ArrowRight",
    "pageup": "PageUp", "pgup": "PageUp",
    "pagedown": "PageDown", "pgdn": "PageDown",
    "home": "Home", "end": "End",
    **{f"f{i}": f"F{i}" for i in range(1, 13)},
}

# Keys dispatched as discrete keyDown/char/keyUp events (vs. typed char-by-char).
# A normalized key in this set routes to the "special key" branch; otherwise the
# input is treated as plain text and fed through _type_char.
_SPECIAL_KEYS: frozenset[str] = frozenset({
    "Enter", "Tab", "Delete", "Backspace", "Escape",
    "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
    "PageUp", "PageDown", "Home", "End",
    "Control", "Alt", "Meta", "Shift",
}) | {f"F{i}" for i in range(1, 13)}

# Modifier bitmask for Input.dispatchKeyEvent.modifiers (CDP spec):
# Alt=1, Control=2, Meta=4, Shift=8.
_MODIFIER_VK: dict[str, int] = {"alt": 1, "control": 2, "meta": 4, "shift": 8}

# DOM `code` strings for multi-char special keys (single chars use _get_key_code_for_char).
_KEY_CODE_FOR_SPECIAL: dict[str, str] = {
    "enter": "Enter", "tab": "Tab", "escape": "Escape",
    "backspace": "Backspace", "delete": "Delete",
    "arrowup": "ArrowUp", "arrowdown": "ArrowDown",
    "arrowleft": "ArrowLeft", "arrowright": "ArrowRight",
    "pageup": "PageUp", "pagedown": "PageDown",
    "home": "Home", "end": "End",
    "control": "ControlLeft", "alt": "AltLeft",
    "shift": "ShiftLeft", "meta": "MetaLeft",
    **{f"f{i}": f"F{i}" for i in range(1, 13)},
}


def _normalize_key(raw: str) -> str:
    """Normalize a single key token: alias → canonical DOM key name.

    Case-insensitive (table is queried with .lower()). Single chars and unknown
    names are returned unchanged so the caller can route via _SPECIAL_KEYS.
    Mirrors browser-use default_action_watchdog.py alias normalization.
    """
    return _KEY_ALIASES.get(raw.lower(), raw)


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


# ── Direct-value-assignment detection (date/time/special inputs) ─────────────
# These inputs reject per-character key events and must be set via the native
# value setter (_force_set_value) instead of _type_char. Mirrors browser-use
# default_action_watchdog.py:1589-1639 (_requires_direct_value_assignment).
_DIRECT_VALUE_INPUT_TYPES: frozenset[str] = frozenset(
	{"date", "time", "datetime-local", "month", "week", "color", "range"}
)
# jQuery / Bootstrap datepicker class markers on <input type="text"|''>.
_DATEPICKER_CLASS_MARKERS: tuple[str, ...] = (
	"datepicker",
	"daterangepicker",
	"datetimepicker",
	"bootstrap-datepicker",
)
# datepicker data-* attributes on <input type="text"|''>.
_DATEPICKER_DATA_ATTRS: tuple[str, ...] = (
	"data-datepicker",
	"data-date-format",
	"data-provide",
)


def _requires_direct_value_assignment(entry: Any) -> bool:
	"""True if the element won't accept per-character key events and must be
	set via a direct value assignment (native setter).

	Mirrors browser-use default_action_watchdog.py:1589-1639:
	  - <input type> in {date, time, datetime-local, month, week, color, range}
	    (HTML5 compound inputs that require ISO/hex formatted values)
	  - <input type='text'|''> whose class contains a known datepicker marker
	  - <input type='text'|''> carrying a known datepicker data-* attribute

	Used by Tools._action_input_text to route date/special inputs to
	BrowserSession._force_set_value instead of per-char typing.
	"""
	tag = (getattr(entry, "tag_name", "") or "").lower()
	if tag != "input":
		return False
	attrs = getattr(entry, "attributes", {}) or {}
	itype = (attrs.get("type", "") or "").lower()
	if itype in _DIRECT_VALUE_INPUT_TYPES:
		return True
	if itype in ("", "text"):
		cls = (attrs.get("class", "") or "").lower()
		if any(marker in cls for marker in _DATEPICKER_CLASS_MARKERS):
			return True
		if any(attrs.get(attr) for attr in _DATEPICKER_DATA_ATTRS):
			return True
	return False


# Native <select> selection script (ported from browser-use
# default_action_watchdog.py:3273-3344). Matches option.text OR option.value
# (case-insensitive, exact); on hit: focus -> set value three ways
# (element.value / option.selected / element.selectedIndex) -> dispatch
# input+change+blur -> read back element.value to detect framework reversion.
# Returns availableOptions on miss/revert so the action layer can echo them
# back to the LLM for self-correction.
_SELECT_OPTION_JS = """
function(targetText) {
    const element = this;
    if (!element || element.tagName.toLowerCase() !== 'select') {
        return { success: false, error: 'Element is not a <select>' };
    }
    const targetLower = (targetText || '').toLowerCase();
    const options = Array.from(element.options);
    for (const option of options) {
        const textLower = (option.text || '').trim().toLowerCase();
        const valueLower = (option.value || '').toLowerCase();
        if (textLower === targetLower || valueLower === targetLower) {
            const expectedValue = option.value;
            element.focus();
            element.value = expectedValue;
            option.selected = true;
            element.selectedIndex = option.index;
            element.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
            element.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
            element.blur();
            if (element.value !== expectedValue) {
                return {
                    success: false,
                    error: 'Selection was set but reverted by page framework. The dropdown may require clicking.',
                    selectionReverted: true,
                    targetOption: { text: (option.text || '').trim(), value: expectedValue, index: option.index },
                    availableOptions: options.map(o => ({ text: (o.text || '').trim(), value: o.value })),
                };
            }
            return {
                success: true,
                message: `Selected option: ${(option.text || '').trim()} (value: ${option.value})`,
                value: option.value,
            };
        }
    }
    return {
        success: false,
        error: `Option with text or value '${targetText}' not found in select element`,
        availableOptions: options.map(o => ({ text: (o.text || '').trim(), value: o.value })),
    };
}
"""

# Click fallback script (ported from browser-use default_action_watchdog.py:3556-3607).
# Run only when selectionReverted=true: simulates a full gesture
# mousedown/click-on-option/mouseup/change to bypass frameworks that intercept
# programmatic value assignment.
_SELECT_OPTION_CLICK_FALLBACK_JS = """
function(optionIndex) {
    const select = this;
    if (!select || select.tagName.toLowerCase() !== 'select') return { success: false, error: 'Not a select element' };
    const option = select.options[optionIndex];
    if (!option) return { success: false, error: `Option not found at index ${optionIndex}` };
    select.focus();
    select.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
    select.selectedIndex = optionIndex;
    option.selected = true;
    option.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
    select.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
    select.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
    select.blur();
    if (select.value === option.value || select.selectedIndex === optionIndex) {
        return { success: true, message: `Selected via click fallback: ${(option.text || '').trim()}`, value: option.value };
    }
    return { success: false, error: 'Click fallback also failed - framework may block all programmatic selection', finalValue: select.value, expectedValue: option.value };
}
"""


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
        # Last intercepted Page.fileChooserOpened event (set by
        # _on_file_chooser_opened, read by discover_file_input_via_click so
        # upload_file can learn which <input type=file> a clicked dropzone is
        # wired to). None when no chooser has fired since the last clear.
        self._last_file_chooser: dict | None = None
        # Whether Page.setInterceptFileChooserDialog is confirmed on for the
        # current session. discover_file_input_via_click must NOT click when
        # this is False — without interception a click on a file input pops the
        # blocking native OS dialog (the Bug-1 regression).
        self._file_chooser_intercept_enabled: bool = False
        # 非 ASCII 文件名上传时复制的 ASCII 临时副本路径。path 形式 setFileInputFiles
        # 创建的是路径背书 File，浏览器会惰性读盘——临时副本必须存活到 session 结束，
        # stop() 时统一清理（见 set_file_input / stop）。
        self._upload_temp_paths: list[str] = []

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

        # Intercept the OS file-chooser dialog on this session: clicking an
        # <input type='file'> (or an upload button proxying one) then emits a
        # Page.fileChooserOpened event instead of popping the native picker,
        # which would block automation. Uploads themselves go through
        # DOM.setFileInputFiles (set_file_input), independent of this. Best-effort
        # (older Chrome without the command degrades silently — mirrors setAutoAttach).
        await self._enable_file_chooser_intercept()

        logger.info("Browser connected: target=%s", self.current_target_id)

        # Wire up highlight manager with live CDP client
        self._highlight._client = self.client
        self._highlight._session_id = self.current_session_id

    async def _enable_file_chooser_intercept(self) -> None:
        """Enable OS file-chooser interception on the current CDP session.

        With interception on, a click that would open the native file picker
        (an <input type='file'>, or a button whose handler calls input.click())
        instead emits ``Page.fileChooserOpened`` — the modal OS dialog is never
        shown, so automation cannot get stuck behind it. Real uploads use
        ``DOM.setFileInputFiles`` (``set_file_input``), which is independent of
        this and unaffected. Best-effort: an older Chrome lacking the command
        degrades silently (the upload path still works without interception).

        Per-session setting, so every new session (_connect / switch_tab) must
        re-enable it — otherwise tab switches lose native-dialog suppression
        (the Bug-1 regression source). The event handler registration is
        client-level and idempotent (overwrites the same handler).
        """
        try:
            await self.client.send.Page.setInterceptFileChooserDialog(
                {"enabled": True},
                session_id=self.current_session_id,
            )
            self.client.register.Page.fileChooserOpened(self._on_file_chooser_opened)
            self._file_chooser_intercept_enabled = True
        except Exception as e:
            logger.debug("setInterceptFileChooserDialog unavailable/failed: %s", e)

    def _on_file_chooser_opened(self, event: dict, session_id: str | None = None) -> None:
        """Handle an intercepted file-chooser event (log + record).

        Interception already suppresses the native dialog; this records the
        event so ``discover_file_input_via_click`` can learn which
        ``<input type='file'>`` a clicked dropzone/button is wired to
        (upload_file uses this to pick the right input among several
        indistinguishable ones — issue #34 Bug 2). ``backendNodeId`` is present
        only for choosers opened via an ``<input type='file'>``.
        """
        backend_node_id = event.get("backendNodeId")
        logger.info(
            "Native file chooser intercepted (suppressed): mode=%s, "
            "backendNodeId=%s, frameId=%s, session=%s",
            event.get("mode"),
            backend_node_id,
            event.get("frameId"),
            session_id,
        )
        self._last_file_chooser = {
            "backendNodeId": backend_node_id,
            "mode": event.get("mode"),
            "frameId": event.get("frameId"),
            "session_id": session_id,
            "ts": time.time(),
        }

    async def discover_file_input_via_click(
        self, backend_node_id: int, timeout: float = 2.5,
    ) -> int | None:
        """Click an element and capture which ``<input type='file'>`` opens.

        Used by upload_file when the target is a dropzone/button (not a file
        input) among several indistinguishable file inputs (e.g. Douyin's
        horizontal vs vertical cover slots — hidden, no orientation label,
        coordinates collapsed to (0,0)). Instead of guessing, let the page's
        own wiring reveal the associated input: clicking it fires
        ``Page.fileChooserOpened`` carrying the input's backendNodeId. With
        file-chooser interception on (Bug-1 fix) the native OS dialog is never
        shown, so this never blocks automation.

        Returns the discovered backendNodeId, or None when:

        * interception is off — clicking would pop the blocking native dialog,
          so we refuse to click (Bug-1 guard); or
        * no chooser fired within ``timeout`` — the click opened a custom
          upload dialog (common on Douyin/Bilibili cover editors) or did
          nothing; upload_file then surfaces an honest, actionable error
          instead of mis-uploading to a guessed input.
        """
        if not self._file_chooser_intercept_enabled:
            logger.debug(
                "discover_file_input_via_click: interception not enabled, "
                "refusing to click (would pop native dialog)",
            )
            return None
        self._last_file_chooser = None
        try:
            await self.click_element(backend_node_id)
        except Exception as e:
            logger.debug("discover_file_input_via_click: click failed: %s", e)
            return None
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._last_file_chooser is not None:
                bid = self._last_file_chooser.get("backendNodeId")
                logger.info(
                    "discover_file_input_via_click: click on backendNodeId=%d "
                    "opened file input backendNodeId=%s",
                    backend_node_id, bid,
                )
                return bid
            await asyncio.sleep(0.05)
        logger.info(
            "discover_file_input_via_click: click on backendNodeId=%d opened no "
            "file chooser within %.1fs (custom dialog?)",
            backend_node_id, timeout,
        )
        return None

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
        # 清理本 session 为非 ASCII 文件名上传复制的 ASCII 临时副本。延迟到现在才删，
        # 因为浏览器按路径惰性读盘，传完即删会导致假成功（Fix B 回归，issue #36）。
        for p in getattr(self, "_upload_temp_paths", []):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                logger.warning("failed to remove temp upload file %r", p)
        self._upload_temp_paths.clear()
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

        tabs = await self.get_tabs()

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
            except Exception as e:
                logger.warning("get_state: take_screenshot failed: %s", e)
                screenshot = None

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

    async def take_screenshot(
        self,
        format: str = "png",
        quality: int | None = None,
        clip: dict | None = None,
        full_page: bool = False,
        wait_settle: bool = False,
    ) -> bytes:
        """Capture a screenshot of the current viewport.

        Args:
            format: 'png' | 'jpeg' | 'webp'.
            quality: 0-100, only effective when format == 'jpeg' (CDP constraint).
            clip: optional rect {'x','y','width','height'} in CSS px (scale forced to 1).
            full_page: capture the full scrollable page (captureBeyondViewport=True).
            wait_settle: poll document.readyState to 'complete' before capturing.

        Raises:
            RuntimeError: if CDP returns no 'data' field.
        """
        if wait_settle:
            try:
                await self._wait_for_page_settle()
            except Exception as e:
                logger.warning("Pre-screenshot wait_settle failed: %s", e)

        params: dict = {"format": format}
        if full_page:
            params["captureBeyondViewport"] = True
        if quality is not None and format == "jpeg":
            params["quality"] = int(quality)
        if clip is not None:
            params["clip"] = {
                "x": clip.get("x", 0.0),
                "y": clip.get("y", 0.0),
                "width": clip.get("width", 0.0),
                "height": clip.get("height", 0.0),
                "scale": 1,
            }

        try:
            result = await self.client.send.Page.captureScreenshot(
                params,
                session_id=self.current_session_id,
            )
        except Exception as e:
            logger.warning("Page.captureScreenshot failed: %s", e)
            raise

        if not isinstance(result, dict) or "data" not in result:
            raise RuntimeError("Screenshot failed - no data returned")

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

    async def go_back(self) -> str | None:
        """Navigate to the previous page in history.

        Returns the URL of the previous entry, or ``None`` if there is no
        previous entry to go back to (caller should treat ``None`` as "nothing
        happened"). Clears the selector_map caches — like ``navigate`` and
        ``switch_tab`` — because going back changes the page.
        """
        # 后退会改变页面，两层 selector_map 缓存都要清（与 navigate / switch_tab 一致）
        self._cached_selector_map = None
        self._previous_cached_selector_map = None

        history = await self.client.send.Page.getNavigationHistory(
            {}, session_id=self.current_session_id,
        )
        idx = history.get("currentIndex", 0)
        entries = history.get("entries", [])
        if idx <= 0 or not entries:
            return None  # 无历史可退——返回 None 由 action 层给出明确反馈
        prev = entries[idx - 1]
        await self.client.send.Page.navigateToHistoryEntry(
            {"entryId": prev["id"]},
            session_id=self.current_session_id,
        )
        await self._wait_for_page_settle()
        return prev.get("url")

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
        """Click at viewport coordinates.

        Mouse sequence mirrors browser-use default_action_watchdog.py:902-955:
        mouseMoved -> mousePressed -> mouseReleased. The leading mouseMoved is
        required by hover menus, mousemove listeners, and anti-bot heuristics
        that only fire on an explicit move event (not press/release alone).
        """
        sid = self.current_session_id
        # 1) mouseMoved — 触发 hover 状态 / mousemove 监听器 / 反爬检测
        await self.client.send.Input.dispatchMouseEvent(
            {"type": "mouseMoved", "x": x, "y": y},
            session_id=sid,
        )
        await asyncio.sleep(0.05)  # 对齐 browser-use moved->pressed 间隔
        # 2) mousePressed
        await self.client.send.Input.dispatchMouseEvent(
            {
                "type": "mousePressed",
                "x": x,
                "y": y,
                "button": "left",
                "clickCount": 1,
            },
            session_id=sid,
        )
        await asyncio.sleep(0.08)  # 对齐 browser-use pressed->released 间隔
        # 3) mouseReleased
        await self.client.send.Input.dispatchMouseEvent(
            {
                "type": "mouseReleased",
                "x": x,
                "y": y,
                "button": "left",
                "clickCount": 1,
            },
            session_id=sid,
        )
        await asyncio.sleep(0.3)  # 保留原有等待，让点击反馈动画 / SPA 局部更新有机会
        if self._highlight_settings.enabled and self._highlight_settings.click_feedback_enabled:
            await self._highlight.highlight_click_point(x, y)

    async def get_element_coordinates(
        self, backend_node_id: int, viewport: tuple[int, int] | None = None,
    ) -> DOMRect | None:
        """Get real-time viewport coordinates for an element via CDP.

        Three-tier fallback chain (same as browser-use):
        1. DOM.getContentQuads — best for inline/complex layouts; picks the
           quad with the largest intersection with the viewport (mirrors
           browser-use _click_element_node_impl:829-864).
        2. DOM.getBoxModel — fallback using box model content.
        3. JS getBoundingClientRect() via DOM.resolveNode + Runtime.callFunctionOn.

        ``viewport`` is an optional (width, height); callers that already fetched
        it (e.g. click_element) pass it in to avoid a duplicate Page.getLayoutMetrics
        round-trip. If omitted it is fetched here.
        """
        sid = self.current_session_id
        if viewport is None:
            viewport = await self._get_viewport_size()

        # Method 1: DOM.getContentQuads — 取与视口交集最大的 quad 的外接矩形
        try:
            result = await self.client.send.DOM.getContentQuads(
                {"backendNodeId": backend_node_id},
                session_id=sid,
            )
            best = self._best_quad_rect(result.get("quads", []), viewport)
            if best:
                return best
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

    @staticmethod
    def _best_quad_rect(
        quads: list, viewport: tuple[int, int] | None,
    ) -> DOMRect | None:
        """From a list of quads (each 8 floats: x1,y1,...,x4,y4), return the
        bounding DOMRect of the quad with the largest intersection with the
        viewport. Falls back to the first quad when viewport is unknown or
        empty. Mirrors browser-use _click_element_node_impl:829-864 (simplified
        to bounding-box intersection instead of exact polygon clipping).
        """
        rects: list[DOMRect] = []
        for quad in quads:
            if len(quad) < 8:
                continue
            xs = [quad[i] for i in range(0, 8, 2)]
            ys = [quad[i] for i in range(1, 8, 2)]
            rects.append(DOMRect(
                x=min(xs), y=min(ys),
                width=max(xs) - min(xs),
                height=max(ys) - min(ys),
            ))
        if not rects:
            return None
        if not viewport or viewport[0] <= 0 or viewport[1] <= 0:
            return rects[0]
        vw, vh = viewport

        def _area(r: DOMRect) -> float:
            iw = max(0.0, min(r.x + r.width, vw) - max(r.x, 0.0))
            ih = max(0.0, min(r.y + r.height, vh) - max(r.y, 0.0))
            return iw * ih

        return max(rects, key=_area)

    async def _get_viewport_size(self) -> tuple[int, int] | None:
        """Viewport (clientWidth, clientHeight) via Page.getLayoutMetrics.

        Used for quad selection (pick the most-visible quad) and center clipping.
        Returns None on any CDP error so callers degrade gracefully (first quad
        / no clip).
        """
        try:
            result = await self.client.send.Page.getLayoutMetrics(
                {}, session_id=self.current_session_id,
            )
            lv = result.get("layoutViewport", {})
            w, h = int(lv.get("clientWidth", 0)), int(lv.get("clientHeight", 0))
            return (w, h) if w > 0 and h > 0 else None
        except Exception:
            return None

    async def click_element(self, backend_node_id: int) -> bool:
        """Click an element using real-time CDP coordinates.

        Returns True if a mouse-event sequence was dispatched (either at the
        element's center or via JS fallback). Returns False if coordinates
        could not be obtained AND JS fallback also failed — the caller (action
        layer) should treat False as "click did not happen" and surface a
        friendly error to the LLM.

        Robustness chain (mirrors browser-use _click_element_node_impl):
        1. scrollIntoViewIfNeeded (best-effort, failures swallowed)
        2. get_element_coordinates -> center, clipped to viewport
        3. click_at unless _is_element_occluded reports the click point is
           covered by another element
        4. JS fallback this.click() when step 2/3 are skipped (no coords or
           occluded)
        """
        # 1. Scroll into view first (best-effort)
        try:
            await self.client.send.DOM.scrollIntoViewIfNeeded(
                {"backendNodeId": backend_node_id},
                session_id=self.current_session_id,
            )
            await asyncio.sleep(0.05)
        except Exception:
            pass

        viewport = await self._get_viewport_size()
        rect = await self.get_element_coordinates(backend_node_id, viewport=viewport)
        if rect:
            x = int(rect.x + rect.width / 2)
            y = int(rect.y + rect.height / 2)
            # 裁剪到视口（对齐 browser-use _click_element_node_impl:866-872）
            if viewport:
                x = max(0, min(viewport[0] - 1, x))
                y = max(0, min(viewport[1] - 1, y))
            if not await self._is_element_occluded(backend_node_id, x, y):
                await self.click_at(x, y)
                return True
            logger.info(
                "Element backendNodeId=%d is occluded at (%d,%d), using JS click fallback",
                backend_node_id, x, y,
            )

        # 2. 坐标拿不到 OR 被遮挡 -> JS click 回退
        if await self._js_click(backend_node_id):
            return True

        logger.warning(
            "Could not click backendNodeId=%d (no coordinates and JS fallback failed)",
            backend_node_id,
        )
        return False

    async def _is_element_occluded(
        self, backend_node_id: int, x: int, y: int,
    ) -> bool:
        """Check if the element is occluded at (x, y) by another element.

        Uses document.elementFromPoint to find the topmost element at the click
        point, then walks up its ancestor chain looking for the target (covers
        <label> wrapping <input>, aria-labelledby pairs, and other "visible
        target is an ancestor of the hit element" patterns that browser-use
        also handles in _check_element_occlusion:573-700).

        Best-effort: any JS/CDP error returns False (treat as not occluded so
        the geometric click proceeds — JS fallback would also fail if the page
        were truly broken). x/y are passed as arguments, not string
        interpolation, to avoid injection.
        """
        try:
            resolve = await self.client.send.DOM.resolveNode(
                {"backendNodeId": backend_node_id},
                session_id=self.current_session_id,
            )
            object_id = resolve["object"]["objectId"]
            result = await self.client.send.Runtime.callFunctionOn(
                {
                    "objectId": object_id,
                    "functionDeclaration": """
                    function(x, y) {
                        var hit = document.elementFromPoint(x, y);
                        if (!hit) return true;  // 视口外或被遮挡
                        var cur = hit;
                        while (cur) {
                            if (cur === this) return false;  // 命中目标或其祖先 -> 未遮挡
                            cur = cur.parentElement;
                        }
                        return true;  // elementFromPoint 命中的是无关元素 -> 被遮挡
                    }
                    """,
                    "arguments": [{"value": x}, {"value": y}],
                    "returnByValue": True,
                },
                session_id=self.current_session_id,
            )
            return bool(result.get("result", {}).get("value"))
        except Exception as e:
            logger.debug("_is_element_occluded failed (treating as not occluded): %s", e)
            return False

    async def _js_click(self, backend_node_id: int) -> bool:
        """JS fallback click via DOM.resolveNode + Runtime.callFunctionOn.

        Used when geometric click is impossible (no coordinates) or when the
        element is occluded. Calls this.click() directly, bypassing the mouse
        event pipeline. Mirrors browser-use _click_element_node_impl:957-992.

        Returns True if the JS click dispatched without error, False on any
        failure (DOM.resolveNode miss, JS exception, transport glitch).
        """
        try:
            resolve = await self.client.send.DOM.resolveNode(
                {"backendNodeId": backend_node_id},
                session_id=self.current_session_id,
            )
            object_id = resolve["object"]["objectId"]
            await self.client.send.Runtime.callFunctionOn(
                {
                    "objectId": object_id,
                    "functionDeclaration": "function() { this.click(); }",
                    "returnByValue": True,
                },
                session_id=self.current_session_id,
            )
            return True
        except Exception as e:
            logger.debug("_js_click failed: %s", e)
            return False

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
        """Send keys: a combination ('Control+a'), a single special key
        ('Enter'/'ArrowUp'/'F5'), or plain text typed char-by-char ('hello').

        Three routes mirror browser-use default_action_watchdog.on_SendKeysEvent:
          - '+' present      → combination (modifiers bitmask + main key)
          - named special key → keyDown/char/keyUp
          - otherwise        → text, reusing _type_char per character
        Aliases (ctrl/return/esc/up/f5/...) are normalized via _normalize_key.
        """
        sid = self.current_session_id
        if "+" in keys:
            await self._send_combination(keys, sid)
            return
        normalized = _normalize_key(keys)
        if normalized in _SPECIAL_KEYS:
            await self._send_single_special_key(normalized, sid)
        else:
            # Plain text: type each char via _type_char (handles keyDown→char→keyUp
            # and CJK insertText). Iterate the normalized string so an alias that
            # maps to a printable char (e.g. 'space' -> ' ') types correctly; for
            # ordinary text normalized == keys. Small inter-char delay keeps pace
            # ~browser-use.
            for ch in normalized:
                await self._type_char(ch, sid=sid)
                await asyncio.sleep(0.005)

    async def _send_combination(self, keys: str, sid: str) -> None:
        """Dispatch a modifier+key combination ('Control+a', 'Alt+F4', ...)."""
        parts = [p.strip() for p in keys.split("+")]
        modifiers = 0
        for part in parts[:-1]:
            norm = _normalize_key(part)
            vk = _MODIFIER_VK.get(norm.lower())
            if vk is None:
                # Unknown modifier → soft-degrade (warn + skip); the combination
                # space is unbounded, so we never hard-fail on a bad modifier.
                logger.warning("send_keys: ignoring unknown modifier '%s' in '%s'", part, keys)
                continue
            modifiers |= vk

        main = _normalize_key(parts[-1])
        if len(main) == 1 and main not in _SPECIAL_KEYS:
            # Single-char main key (Control+a) MUST go through the key event path
            # carrying `modifiers`, otherwise Ctrl+A select-all would be dropped.
            await self._send_combo_char_key(main, sid, modifiers)
        else:
            await self._send_single_special_key(main, sid, modifiers=modifiers)

    async def _send_combo_char_key(self, char: str, sid: str, modifiers: int) -> None:
        """Dispatch a single-char main key under modifiers (e.g. the 'a' in Ctrl+a).

        Reuses _get_char_modifiers_and_vk / _get_key_code_for_char so Shift state
        and code/vk match the existing per-char typing path.
        """
        char_mod, char_vk, base = _get_char_modifiers_and_vk(char)
        code = _get_key_code_for_char(base)
        total_mod = modifiers | char_mod
        await self.client.send.Input.dispatchKeyEvent(
            {
                "type": "keyDown", "key": base, "code": code,
                "modifiers": total_mod, "windowsVirtualKeyCode": char_vk,
            },
            session_id=sid,
        )
        await self.client.send.Input.dispatchKeyEvent(
            {"type": "char", "text": char, "key": char},
            session_id=sid,
        )
        await self.client.send.Input.dispatchKeyEvent(
            {
                "type": "keyUp", "key": base, "code": code,
                "modifiers": total_mod, "windowsVirtualKeyCode": char_vk,
            },
            session_id=sid,
        )

    async def _send_single_special_key(
        self, key: str, sid: str, *, modifiers: int = 0,
    ) -> None:
        """Dispatch a named special key as keyDown → (char) → keyUp."""
        key_lower = key.lower()
        code = _KEY_CODE_FOR_SPECIAL.get(key_lower, key)
        vk = _KEY_VK_MAP.get(key_lower, 0)

        await self.client.send.Input.dispatchKeyEvent(
            {
                "type": "keyDown", "key": key, "code": code,
                "modifiers": modifiers, "windowsVirtualKeyCode": vk,
            },
            session_id=sid,
        )
        # Enter/Tab need a char event for proper event handling (React form submit)
        char_text = _KEY_CHAR_TEXT.get(key_lower)
        if char_text:
            await self.client.send.Input.dispatchKeyEvent(
                {"type": "char", "text": char_text, "key": key},
                session_id=sid,
            )
        await self.client.send.Input.dispatchKeyEvent(
            {
                "type": "keyUp", "key": key, "code": code,
                "modifiers": modifiers, "windowsVirtualKeyCode": vk,
            },
            session_id=sid,
        )
        if key_lower == "enter":
            await asyncio.sleep(0.1)  # let any navigation triggered by Enter settle

    # ── Scrolling ──────────────────────────────────────────────────────

    async def scroll(
        self, direction: str = "down", amount: int = 3,
    ) -> dict:
        """Scroll the page by a number of viewport heights.

        Returns ``{vertical_percentage, at_edge}`` from a post-scroll
        ``Runtime.evaluate`` so the action layer can echo the current position
        and signal "already at edge" in the same turn, avoiding wasted scroll
        rounds when the LLM scrolls past the bottom. Degrades to
        ``{vertical_percentage: None, at_edge: False}`` on read failure without
        affecting the scroll itself.
        """
        sid = self.current_session_id
        metrics = await self.client.send.Page.getLayoutMetrics(
            {}, session_id=sid,
        )
        viewport = metrics.get("cssVisualViewport", {})
        viewport_height = viewport.get("clientHeight", 1000)  # fallback 对齐 browser-use
        viewport_width = viewport.get("clientWidth", 1280)
        delta = amount * viewport_height
        if direction == "up":
            delta = -delta

        await self.client.send.Input.dispatchMouseEvent(
            {
                "type": "mouseWheel",
                "x": viewport_width / 2,
                "y": viewport_height / 2,
                "deltaX": 0,
                "deltaY": delta,
            },
            session_id=sid,
        )
        # 滚动动画 + 懒加载触发的渲染窗口；不用 _wait_for_page_settle——
        # 它轮询 readyState，而 scroll 不改 readyState，会立即返回等于无等待。
        await asyncio.sleep(0.2)

        # G2: 一次 Runtime.evaluate 读当前位置 + 判边界
        # （documentElement/body 取 max，兼容 quirks 模式滚动挂在 body 的页面）
        position = {"vertical_percentage": None, "at_edge": False}
        try:
            result = await self.client.send.Runtime.evaluate(
                {
                    "expression": (
                        "(() => {"
                        "  const d = document.documentElement, b = document.body;"
                        "  const sy = Math.max(d.scrollTop || 0, b ? b.scrollTop || 0 : 0);"
                        "  const sh = Math.max(d.scrollHeight || 0, b ? b.scrollHeight || 0 : 0);"
                        "  const ch = d.clientHeight || window.innerHeight || 0;"
                        "  const max = sh - ch;"
                        "  const pct = max > 0 ? (sy / max) * 100 : 100;"
                        "  return JSON.stringify({ sy, sh, ch, pct });"
                        "})()"
                    ),
                    "returnByValue": True,
                },
                session_id=sid,
            )
            val = json.loads(result.get("result", {}).get("value") or "{}")
            sy, sh, ch = val.get("sy", 0), val.get("sh", 0), val.get("ch", 0)
            max_top = sh - ch
            position["vertical_percentage"] = (
                round((sy / max_top) * 100, 1) if max_top > 0 else 100.0
            )
            if direction == "down":
                position["at_edge"] = sy + ch >= sh - 1
            else:
                position["at_edge"] = sy <= 1
        except Exception:
            # 位置读取失败不影响滚动本身——回显退化为基础文案
            pass
        return position

    # ── Tabs ───────────────────────────────────────────────────────────

    async def get_tabs(self) -> list[TabInfo]:
        """List page targets only — 1 个 CDP 调用（Target.getTargets），不抓 DOM/截图。

        抽自 get_state 的标签页抓取逻辑，供 switch_tab / close_tab 等只需标签页列表、
        无需 DOM/截图的路径调用，避免全量 get_state 开销。
        """
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
        return tabs

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
        # File-chooser interception is per-session: re-enable on the new tab so
        # the native picker stays suppressed after switching (regression guard).
        await self._enable_file_chooser_intercept()
        logger.info("Switched to tab: %s", target_id)
        await self._wait_for_page_settle()

    async def close_tab(self, target_id: str) -> None:
        """Close a tab. If it's the current tab, switch to another; create about:blank if none."""
        was_current = target_id == self.current_target_id
        await self.client.send.Target.closeTarget({"targetId": target_id})
        if was_current:
            targets = await self.client.send.Target.getTargets({})
            for t in targets.get("targetInfos", []):
                if t.get("type") == "page" and t["targetId"] != target_id:
                    await self.switch_tab(t["targetId"])
                    return
            await self.create_tab("about:blank")  # G9: 无其他 page，避免 current_* 悬挂

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

    async def find_text(self, text: str) -> dict:
        """Find text on the page and scroll the first match into view.

        Mirrors browser-use ``on_ScrollToTextEvent``
        (default_action_watchdog.py:2682-2774) — a 3-query XPath chain via
        ``DOM.performSearch`` with a JS TreeWalker fallback — with four bug
        fixes: (1) XPath-safe string literal via ``_xpath_string_literal``
        (browser-use f-string-injects and breaks on ``"``); (2)
        ``discardSearchResults`` in a ``finally`` (browser-use skips it on
        the winning query, leaking the searchId); (3)
        ``includeUserAgentShadowDOM`` pierce (browser-use omits it); (4) no
        unused ``DOM.getDocument`` (``performSearch`` already searches the
        whole document).

        Returns ``{found, method, tag}`` where ``method`` is one of
        ``xpath-text`` / ``xpath-content`` / ``xpath-attr`` /
        ``js-treewalker`` / ``none``. A clean miss returns ``found=False``
        and never raises; the action layer builds the soft echo. Unexpected
        CDP errors propagate to the action layer's try/except.
        """
        sid = self.current_session_id
        lit = _xpath_string_literal(text)
        queries = [
            ("xpath-text", f"//*[contains(text(), {lit})]"),
            ("xpath-content", f"//*[contains(., {lit})]"),
            ("xpath-attr", f"//*[@*[contains(., {lit})]]"),
        ]
        for method, query in queries:
            search_id: str | None = None
            try:
                search = await self.client.send.DOM.performSearch(
                    {"query": query, "includeUserAgentShadowDOM": True},
                    session_id=sid,
                )
                search_id = search.get("searchId")
                if search.get("resultCount", 0) <= 0:
                    continue
                results = await self.client.send.DOM.getSearchResults(
                    {"searchId": search_id, "fromIndex": 0, "toIndex": 1},
                    session_id=sid,
                )
                node_ids = results.get("nodeIds", [])
                if not node_ids:
                    continue
                node_id = node_ids[0]
                await self.client.send.DOM.scrollIntoViewIfNeeded(
                    {"nodeId": node_id}, session_id=sid,
                )
                tag = await self._highlight_search_node(node_id)
                return {"found": True, "method": method, "tag": tag}
            except Exception as e:
                logger.debug("find_text query %s failed: %s", query, e)
                continue
            finally:
                # Bug fix: browser-use puts this after `break`, so the winning
                # query leaks its searchId. finally runs on return/continue/raise.
                if search_id is not None:
                    try:
                        await self.client.send.DOM.discardSearchResults(
                            {"searchId": search_id}, session_id=sid,
                        )
                    except Exception:
                        pass
        if await self._find_text_js_fallback(text):
            return {"found": True, "method": "js-treewalker", "tag": None}
        return {"found": False, "method": "none", "tag": None}

    async def _find_text_js_fallback(self, text: str) -> bool:
        """TreeWalker over text nodes under document.body; scrollIntoView the
        first match's parentElement. ``text`` is injected via ``json.dumps``
        (JS-safe; browser-use f-string-injects and breaks on quotes). Only
        runs when all three XPath queries miss."""
        js = (
            "(() => {"
            f"  const needle = {json.dumps(text)};"
            "  const walker = document.createTreeWalker("
            "    document.body, NodeFilter.SHOW_TEXT, null, false);"
            "  let node;"
            "  while ((node = walker.nextNode())) {"
            "    const t = node.nodeValue || '';"
            "    if (t.includes(needle) && t.trim()) {"
            "      if (node.parentElement) {"
            "        node.parentElement.scrollIntoView("
            "          { behavior: 'smooth', block: 'center' });"
            "      }"
            "      return true;"
            "    }"
            "  }"
            "  return false;"
            "})()"
        )
        try:
            return bool(await self.execute_js(js))
        except Exception as e:
            logger.debug("find_text JS fallback failed: %s", e)
            return False

    async def _highlight_search_node(self, node_id: int) -> str | None:
        """Convert a performSearch nodeId to backendNodeId and highlight the
        element box (visual feedback, best-effort — honors the "highlight"
        promise in find_text's description). Returns the lowercased tag name
        for the action echo, or None if describe/highlight failed."""
        try:
            desc = await self.client.send.DOM.describeNode(
                {"nodeId": node_id}, session_id=self.current_session_id,
            )
            node = desc.get("node", {})
            backend_id = node.get("backendNodeId")
            tag = (node.get("nodeName") or "").lower() or None
            if backend_id:
                await self.highlight_element(backend_id)
            return tag
        except Exception:
            return None

    async def fetch_select_options(self, backend_node_id: int) -> list[dict]:
        """Read all options of the <select> identified by backendNodeId.

        Uses DOM.resolveNode + Runtime.callFunctionOn to scope the query to the
        specific select element (NOT document.querySelectorAll('select option'),
        which scans every select on the page — the bug fixed in the click SELECT
        branch; dropdown_options / select_dropdown still have it, to be fixed
        separately).

        Returns a list of {value, text, selected} dicts. Raises on CDP/JS error
        (caller wraps with a friendly message).
        """
        resolve = await self.client.send.DOM.resolveNode(
            {"backendNodeId": backend_node_id},
            session_id=self.current_session_id,
        )
        object_id = resolve["object"]["objectId"]
        result = await self.client.send.Runtime.callFunctionOn(
            {
                "objectId": object_id,
                "functionDeclaration": """
                function() {
                    return Array.from(this.options).map(function(o) {
                        return {
                            value: o.value,
                            text: (o.textContent || '').trim(),
                            selected: o.selected,
                        };
                    });
                }
                """,
                "returnByValue": True,
            },
            session_id=self.current_session_id,
        )
        value = result.get("result", {}).get("value")
        return value if isinstance(value, list) else []

    async def set_select_option(self, backend_node_id: int, value: str) -> dict:
        """Set the option of the <select> identified by backendNodeId.

        Mirrors browser-use on_SelectDropdownOptionEvent (default_action_watchdog.py
        3241-3695): DOM.resolveNode + Runtime.callFunctionOn running the native
        <select> selection script — focus -> set value three ways (element.value /
        option.selected / element.selectedIndex) -> dispatch input+change+blur ->
        read back element.value to detect framework reversion. On reversion, runs
        a click fallback (mousedown/click-on-option/mouseup/change). Matches an
        option by text OR value, case-insensitively (exact).

        Scoped to the target select via backendNodeId (NOT querySelectorAll('select')[0],
        which writes the first select on the page regardless of index — the bug fixed
        here). Returns a dict shaped like browser-use's:
          success: True|False, message?, value?, selectionReverted?, availableOptions?, error?
        Raises on CDP/JS error (caller wraps with a friendly message).
        """
        resolve = await self.client.send.DOM.resolveNode(
            {"backendNodeId": backend_node_id},
            session_id=self.current_session_id,
        )
        object_id = resolve["object"]["objectId"]

        result = await self.client.send.Runtime.callFunctionOn(
            {
                "objectId": object_id,
                "functionDeclaration": _SELECT_OPTION_JS,
                "arguments": [{"value": value}],
                "returnByValue": True,
            },
            session_id=self.current_session_id,
        )
        selection = result.get("result", {}).get("value", {}) or {}

        # Framework reverted the programmatic value set -> click fallback
        # (mirrors browser-use default_action_watchdog.py:3550-3617).
        if selection.get("selectionReverted"):
            option_index = selection.get("targetOption", {}).get("index", 0)
            fallback = await self.client.send.Runtime.callFunctionOn(
                {
                    "objectId": object_id,
                    "functionDeclaration": _SELECT_OPTION_CLICK_FALLBACK_JS,
                    "arguments": [{"value": option_index}],
                    "returnByValue": True,
                },
                session_id=self.current_session_id,
            )
            fb = fallback.get("result", {}).get("value", {}) or {}
            if fb.get("success"):
                return {"success": True, "message": fb.get("message"), "value": fb.get("value", value)}
            # Fallback also failed -> fall through and return the original
            # structured error (carries availableOptions for the action layer
            # to echo back to the LLM).
        return selection

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

        # 部分站点（如抖音封面编辑器）的文件名校验是 ASCII-only 正则，中文文件名
        # （横封面.png）会被误判为「不支持的图片格式」（已实证：文件本身是合法 PNG、
        # 传输链 json.dumps 无损，唯独文件名含 CJK 被前端拒）。文件名含非 ASCII 时，
        # 复制成 ASCII 临时名（保留扩展名）再上传，传完清理；纯 ASCII 路径直接透传。
        upload_path = file_path
        tmp_path: str | None = None
        base_name = os.path.basename(file_path)
        if not base_name.isascii():
            _stem, ext = os.path.splitext(base_name)
            tmp_path = os.path.join(
                tempfile.gettempdir(), f"tw_upload_{secrets.token_hex(6)}{ext}",
            )
            shutil.copy2(file_path, tmp_path)
            upload_path = tmp_path
            # path 形式 setFileInputFiles 创建路径背书 File，页面惰性读盘（onchange→预览→
            # 确认），临时副本在 CDP 返回后必须仍存活。这里只登记、延迟到 stop() 清理——
            # 绝不能在 await 后立即删（页面读时文件已没 = 假成功，Fix B 回归，issue #36）。
            # 登记放在 await 之前，保证 CDP 抛异常时也已记录、stop() 仍会清理。
            self._upload_temp_paths.append(tmp_path)
            logger.info(
                "upload non-ASCII filename %r -> ASCII temp %r",
                base_name, os.path.basename(tmp_path),
            )

        logger.info("DOM.setFileInputFiles: backendNodeId=%d, file=%s", target_id, upload_path)
        await self.client.send.DOM.setFileInputFiles(
            {"backendNodeId": target_id, "files": [upload_path]},
            session_id=self.current_session_id,
        )
