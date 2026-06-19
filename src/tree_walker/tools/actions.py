"""Action execution engine — registers all browser actions and executes them."""

from __future__ import annotations

import asyncio
from typing import Any
import logging
import mimetypes
import os

from tree_walker.agent.views import ActionResult
from tree_walker.browser.session import BrowserSession, _requires_direct_value_assignment
from tree_walker.browser.views import BrowserStateSummary, EnhancedDOMTreeNode, SerializedDOMState
from tree_walker.config import TruncationSettings
from tree_walker.tools.models import ACTION_DEFINITIONS
from tree_walker.tools.registry import ActionRegistry

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────


def _is_file_input_node(node: EnhancedDOMTreeNode) -> bool:
    return node.tag_name.upper() == "INPUT" and node.attributes.get("type", "").lower() == "file"


def _find_file_input_in_descendants(
    node: EnhancedDOMTreeNode, depth: int,
) -> EnhancedDOMTreeNode | None:
    if depth < 0:
        return None
    if _is_file_input_node(node):
        return node
    for child in node.children:
        result = _find_file_input_in_descendants(child, depth - 1)
        if result:
            return result
    return None


def _find_file_input_near_element(
    target: EnhancedDOMTreeNode,
    max_height: int = 3,
    max_descendant_depth: int = 3,
) -> EnhancedDOMTreeNode | None:
    """Walk DOM tree (parent/children/siblings) to find the nearest file input."""
    current: EnhancedDOMTreeNode | None = target
    for _ in range(max_height + 1):
        if current is None:
            break
        if _is_file_input_node(current):
            return current
        result = _find_file_input_in_descendants(current, max_descendant_depth)
        if result:
            return result
        if current.parent:
            for sibling in current.parent.children:
                if sibling is current:
                    continue
                if _is_file_input_node(sibling):
                    return sibling
                result = _find_file_input_in_descendants(sibling, max_descendant_depth)
                if result:
                    return result
        current = current.parent
    return None


def _pick_nearest_file_input(
    target: Any,
    file_input_ids: list[int],
    dom_state: SerializedDOMState | None,
) -> int | None:
    """Pick the file input backendNodeId nearest to the target element.

    First tries DOM tree traversal, then falls back to coordinate distance.
    """
    if not file_input_ids:
        return None
    if len(file_input_ids) == 1:
        return file_input_ids[0]

    # Try DOM tree traversal first
    found = _find_file_input_near_element(target)
    if found and found.backend_node_id in file_input_ids:
        return found.backend_node_id

    # Fallback: coordinate distance
    id_to_pos: dict[int, tuple[int, int]] = {}
    if dom_state:
        for elem in dom_state.selector_map.values():
            if elem.backend_node_id is not None and elem.backend_node_id in file_input_ids:
                id_to_pos[elem.backend_node_id] = (elem.x, elem.y)

    if not id_to_pos:
        return file_input_ids[0]

    tx, ty = target.x, target.y
    best_id: int | None = None
    best_dist = float("inf")
    for fid, (fx, fy) in id_to_pos.items():
        dist = (tx - fx) ** 2 + (ty - fy) ** 2
        if dist < best_dist:
            best_dist = dist
            best_id = fid

    return best_id if best_id is not None else file_input_ids[0]


def _file_matches_accept(file_path: str, accept: str | None) -> bool:
    """True if file_path is acceptable under the input's ``accept`` attribute.

    Parses the HTML accept attribute (comma-separated tokens, each a file
    extension like ``.png``, a wildcard MIME like ``image/*``, or a full MIME
    like ``application/pdf``) and matches the file's extension / guessed MIME.
    Empty/missing accept means "no restriction" (True).

    Uses stdlib mimetypes to map the extension to a MIME, so wildcard and
    full-MIME tokens work without a hard-coded table. Used by
    Tools._action_upload_file to emit a soft ⚠️ Note on mismatch — it never
    blocks the upload (CDP / browser-use do not either).
    """
    accept = (accept or "").strip()
    if not accept:
        return True
    file_ext = os.path.splitext(file_path)[1].lower()
    guessed_mime, _ = mimetypes.guess_type(file_path)
    for token in accept.split(","):
        token = token.strip().lower()
        if not token:
            continue
        if token.startswith("."):
            if file_ext == token:
                return True
        elif token.endswith("/*"):
            prefix = token[:-1]  # "image/"
            if guessed_mime and guessed_mime.startswith(prefix):
                return True
        else:
            if guessed_mime == token:
                return True
    return False


# ── Search engines ──────────────────────────────────────────────────

_SEARCH_ENGINE_URLS: dict[str, str] = {
    "baidu": "https://www.baidu.com/s?wd={query}",
    "google": "https://www.google.com/search?q={query}&udm=14",
    "bing": "https://www.bing.com/search?q={query}",
    "duckduckgo": "https://duckduckgo.com/?q={query}",
}


# ── Navigate health check / error mapping (mirrors browser-use) ──────

# 等待/重试时长（秒），参照 browser_use/tools/service.py:501-523
_NAVIGATE_EMPTY_RETRY_WAIT = 3.0   # 首次发现空 DOM 后等待重查
_NAVIGATE_EMPTY_RELOAD_WAIT = 5.0  # reload 后等待

# 网络错误码 → 触发 "site unavailable" 友好提示（参照 browser-use service.py:544-557）
_NAVIGATE_NET_ERROR_MARKERS = (
    "ERR_NAME_NOT_RESOLVED",
    "ERR_INTERNET_DISCONNECTED",
    "ERR_CONNECTION_REFUSED",
    "ERR_TIMED_OUT",
    "ERR_TUNNEL_CONNECTION_FAILED",
    "net::",
)


class Tools:
    """Action registry + execution engine.

    Each browser action is registered as a closure bound to this instance.
    The registry produces the Anthropic tool schema used by the LLM client.
    """

    def __init__(self, truncation: TruncationSettings | None = None, allowed_upload_paths: list[str] | None = None) -> None:
        self.registry = ActionRegistry()
        self._register_all()
        self._cached_browser_state: BrowserStateSummary | None = None
        self._truncation = truncation or TruncationSettings()
        self._allowed_upload_paths = allowed_upload_paths

    # ── Public API ─────────────────────────────────────────────────────

    async def execute(
        self,
        action_name: str,
        params: dict,
        browser: BrowserSession,
        browser_state: BrowserStateSummary | None = None,
    ) -> ActionResult:
        """Execute a single action by name with given parameters."""
        if action_name not in self.registry.actions:
            return ActionResult(error=f"Unknown action: {action_name}")

        params = self._flatten_params(params, action_name)

        self._cached_browser_state = browser_state
        try:
            registered = self.registry.actions[action_name]
            result = await registered.handler(params, browser)
            return self._normalize(result)
        except Exception as e:
            logger.exception("Action %s failed", action_name)
            return ActionResult(error=str(e))
        finally:
            self._cached_browser_state = None

    # ── Element lookup ──────────────────────────────────────────────────

    async def _get_element_by_index(
        self, index: int, browser: BrowserSession,
    ) -> tuple[Any | None, ActionResult | None]:
        """Look up element by index, using cached state when available."""
        if self._cached_browser_state and self._cached_browser_state.dom_state:
            entry = self._cached_browser_state.dom_state.selector_map.get(index)
            if entry:
                return entry, None

        state = await browser.get_state(include_screenshot=False)
        if not state.dom_state:
            return None, ActionResult(error="No DOM state available")
        entry = state.dom_state.selector_map.get(index)
        if not entry:
            return None, ActionResult(error=f"Element {index} not found in DOM state")
        return entry, None

    # ── Registration ───────────────────────────────────────────────────

    def _register_all(self) -> None:
        for name, (param_model, description, terminates) in ACTION_DEFINITIONS.items():
            handler = getattr(self, f"_action_{name}", None)
            if handler is None:
                logger.debug("No handler for action %s, skipping", name)
                continue
            self.registry.action(
                name=name,
                description=description,
                param_model=param_model,
                terminates=terminates,
            )(handler)

    def apply_page_filters(self, filters: dict[str, list[str]]) -> None:
        """Apply page-pattern filters to specific actions.

        Each key is an action name; the value is a list of glob patterns.
        Unknown action names are silently ignored.
        """
        for name, patterns in filters.items():
            if name in self.registry.actions:
                self.registry.actions[name].page_patterns = patterns

    # ── Action handlers ────────────────────────────────────────────────

    async def _action_navigate(self, params: dict, browser: BrowserSession) -> ActionResult:
        url = params["url"]
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        new_tab = params.get("new_tab", False)

        try:
            await browser.navigate(url, new_tab=new_tab)
            # 健康检查：仅当前标签页 + http(s) URL（chrome://、about:、new_tab=True 跳过）
            if not new_tab:
                await self._navigate_health_check(url, browser)
            memory = (
                f"Opened new tab with URL {url}" if new_tab else f"Navigated to {url}"
            )
            logger.info(memory)
            return ActionResult(extracted_content=memory, long_term_memory=memory)
        except Exception as e:
            return self._map_navigation_error(url, e)

    @staticmethod
    def _dom_appears_empty(state: BrowserStateSummary) -> bool:
        """判定页面是否为空（镜像 browser-use 的 _page_appears_empty）。

        SerializedDOMState.llm_representation() 在 _root is None 时返回非空占位符，所以必须
        单独检查 _root is None（不能只看 element_tree_text）。
        """
        ds = state.dom_state
        if ds is None:
            return True
        return ds._root is None or not ds.element_tree_text.strip()

    async def _navigate_health_check(self, url: str, browser: BrowserSession) -> None:
        """导航后检测空 DOM，参照 browser_use/tools/service.py:493-523 三阶段重试。

        仅 http(s) URL + 当前标签页触发；三阶段判定逐渐收严。
        """
        state = await browser.get_state(include_screenshot=False)
        url_is_http = state.url.lower().startswith(("http://", "https://"))
        if not (url_is_http and self._dom_appears_empty(state)):
            return

        logger.warning(
            "Empty DOM after navigating to %s, waiting %.0fs and rechecking",
            url, _NAVIGATE_EMPTY_RETRY_WAIT,
        )
        await asyncio.sleep(_NAVIGATE_EMPTY_RETRY_WAIT)
        state = await browser.get_state(include_screenshot=False)
        if not (state.url.lower().startswith(("http://", "https://")) and self._dom_appears_empty(state)):
            return

        logger.warning("Still empty after %.0fs, reloading %s", _NAVIGATE_EMPTY_RETRY_WAIT, url)
        # reload：重新 navigate，异常吞掉（避免健康检查的二次失败中断"初次导航已成功"的外层路径）
        try:
            await browser.navigate(url, new_tab=False)
        except Exception as reload_err:
            logger.warning("Reload during health check failed: %s", reload_err)
        await asyncio.sleep(_NAVIGATE_EMPTY_RELOAD_WAIT)

        state = await browser.get_state(include_screenshot=False)
        if state.url.lower().startswith(("http://", "https://")):
            ds = state.dom_state
            if ds is None or ds._root is None:
                raise RuntimeError(
                    f"Page loaded but returned empty content for {url}. "
                    f"The page may require JavaScript that failed to render, use anti-bot measures, "
                    f"or have a connection issue (e.g. tunnel/proxy error). Try a different URL or approach."
                )

    @staticmethod
    def _map_navigation_error(url: str, e: Exception) -> ActionResult:
        """把导航异常映射为对 LLM 友好的 ActionResult.error（参照 browser-use service.py:534-560）。"""
        error_msg = str(e)
        if any(marker in error_msg for marker in _NAVIGATE_NET_ERROR_MARKERS):
            logger.warning("Navigation to %s failed - site unavailable: %s", url, error_msg)
            return ActionResult(error=f"Navigation failed - site unavailable: {url}")
        return ActionResult(error=f"Navigation failed: {error_msg}")

    async def _action_click(self, params: dict, browser: BrowserSession) -> ActionResult:
        # 1. 元素查找（保持原逻辑）
        entry, error = await self._get_element_by_index(params["index"], browser)
        if error:
            return error

        backend_id = entry.backend_node_id

        # 2. SELECT 分支：精确查"指定 index 的那个 select"，不再全页 querySelectorAll
        if entry.tag_name.upper() == "SELECT":
            try:
                options = await browser.fetch_select_options(backend_id)
            except Exception as e:
                return ActionResult(error=f"Failed to read select options: {e}")
            return ActionResult(extracted_content=str(options))

        # 3. 普通点击：highlight -> click_element，映射 bool 信号
        tabs_before = tuple(t.target_id for t in await browser.get_tabs())  # G7 新页检测快照
        try:
            await browser.highlight_element(backend_id)
            clicked = await browser.click_element(backend_id)
        except Exception as e:
            # CDP 异常（连接断开、target 消失等）——友好映射，不让 LLM 看裸堆栈
            return ActionResult(error=f"Click failed: {e}")

        if not clicked:
            # 坐标拿不到 + JS 回退也失败 —— 明确告知 LLM，不再静默成功
            return ActionResult(
                error=(
                    f"Could not click element {params['index']} "
                    f"(no coordinates and JS click fallback failed; "
                    f"the element may be detached, hidden, or in a cross-origin iframe)"
                ),
            )

        # 4. 成功回显（对齐 navigate/go_back 风格）+ 新标签页检测（G7）
        memory = self._describe_click(entry, params["index"])
        memory += await self._detect_new_tab_opened(browser, tabs_before)
        logger.info(memory)
        return ActionResult(extracted_content=memory, long_term_memory=memory)

    @staticmethod
    def _describe_click(entry: Any, index: int) -> str:
        """Build a human-readable click echo, mirroring navigate/go_back style.

        Prefers an identifying attribute the LLM can also see in the DOM tree
        (aria-label/placeholder/title/alt/value), then node_value, then just the
        tag. Bounded to ~60 chars per field so the echo fits the LLM context.
        """
        tag = entry.tag_name.upper()
        attrs = getattr(entry, "attributes", {}) or {}
        for key in ("aria-label", "placeholder", "title", "alt", "value"):
            v = attrs.get(key)
            if v:
                v = v.strip()
                if len(v) > 60:
                    v = v[:60] + "..."
                return f"Clicked [{tag}] {v!r} at index {index}"
        node_value = (getattr(entry, "node_value", "") or "").strip()
        if node_value:
            if len(node_value) > 60:
                node_value = node_value[:60] + "..."
            return f"Clicked [{tag}] {node_value!r} at index {index}"
        return f"Clicked [{tag}] at index {index}"

    async def _detect_new_tab_opened(
        self, browser: BrowserSession, tabs_before: tuple[str, ...],
    ) -> str:
        """点击若打开了新标签页，自动切过去并返回给 LLM 的提示串。

        对齐 browser-use _detect_new_tab_opened：click 前快照 target_id 集合，
        click 后 diff，命中新页则 switch_tab 并回显；切换失败软降级为提示。
        常态点击（未开新页）返回空串，不影响原有回显。
        """
        try:
            await asyncio.sleep(0.05)  # 等 Target.attachedToTarget 事件传播
            tabs_after = await browser.get_tabs()
            new_tabs = [t for t in tabs_after if t.target_id not in tabs_before]
            if not new_tabs:
                return ""
            new_tab = new_tabs[0]
            new_id = new_tab.target_id[-4:]
            try:
                await browser.switch_tab(new_tab.target_id)
                return (f"  ℹ️ Click opened a new tab [{new_id}] {new_tab.title}; "
                        f"auto-switched to it.")
            except Exception:
                return (f"  ℹ️ Click opened a new tab [{new_id}] {new_tab.title}; "
                        f"use switch_tab to focus it.")
        except Exception:
            return ""

    @staticmethod
    def _describe_input(entry: Any, index: int, text: str) -> str:
        """Build a human-readable input echo, mirroring _describe_click /
        navigate / go_back style.

        Prefers an identifying attribute the LLM can also see in the DOM tree
        (aria-label/placeholder/title), then node_value, then just the tag.
        Both the label and the typed text are bounded to ~60 chars so the echo
        fits the LLM context. Skips 'value'/'alt' (unlike _describe_click) since
        for an input the typed text itself is what matters.
        """
        shown = text if len(text) <= 60 else text[:60] + "..."
        tag = entry.tag_name.upper()
        attrs = getattr(entry, "attributes", {}) or {}
        for key in ("aria-label", "placeholder", "title"):
            v = attrs.get(key)
            if v:
                v = v.strip()
                if len(v) > 60:
                    v = v[:60] + "..."
                return f"Typed {shown!r} into [{tag}] {v!r} at index {index}"
        node_value = (getattr(entry, "node_value", "") or "").strip()
        if node_value:
            if len(node_value) > 60:
                node_value = node_value[:60] + "..."
            return f"Typed {shown!r} into [{tag}] {node_value!r} at index {index}"
        return f"Typed {shown!r} into [{tag}] at index {index}"

    @staticmethod
    def _describe_upload(entry: Any, index: int, file_path: str) -> str:
        """Build a human-readable upload echo, mirroring _describe_click /
        _describe_input / navigate / go_back style.

        Shows the uploaded file's basename (not the full path — which is long
        and noisy) plus an identifying attribute the LLM can also see in the
        DOM tree (aria-label/title/name/placeholder), then node_value, then
        just the tag. Skips 'value'/'alt': a file input's value is the
        browser-faked 'C:\\fakepath\\<name>' (not useful) and alt is irrelevant
        here. Bounded to ~60 chars per field so the echo fits the LLM context.
        """
        shown = os.path.basename(file_path)
        if len(shown) > 60:
            shown = shown[:60] + "..."
        tag = entry.tag_name.upper()
        attrs = getattr(entry, "attributes", {}) or {}
        for key in ("aria-label", "title", "name", "placeholder"):
            v = attrs.get(key)
            if v:
                v = v.strip()
                if len(v) > 60:
                    v = v[:60] + "..."
                return f"Uploaded {shown!r} to [{tag}] {v!r} at index {index}"
        node_value = (getattr(entry, "node_value", "") or "").strip()
        if node_value:
            if len(node_value) > 60:
                node_value = node_value[:60] + "..."
            return f"Uploaded {shown!r} to [{tag}] {node_value!r} at index {index}"
        return f"Uploaded {shown!r} to [{tag}] at index {index}"

    @staticmethod
    def _find_node_by_backend_id(
        backend_node_id: int | None,
        dom_state: SerializedDOMState | None,
    ) -> EnhancedDOMTreeNode | None:
        """Look up the DOM node whose backend_node_id matches in the cached
        selector_map. Returns None if not found (e.g. the file input is hidden
        and excluded from the interactive selector_map). Used by
        _action_upload_file to read the resolved file input's ``accept``
        attribute for soft validation.
        """
        if not dom_state or backend_node_id is None:
            return None
        for node in dom_state.selector_map.values():
            if getattr(node, "backend_node_id", None) == backend_node_id:
                return node
        return None

    @staticmethod
    def _is_autocomplete_field(entry: Any) -> tuple[bool, bool]:
        """Detect combobox/autocomplete fields. Returns (is_combo, needs_js_wait).

        Mirrors browser-use tools/service.py:404-417. ``is_combo`` is True for any
        combobox-shaped field (drives the LLM hint); ``needs_js_wait`` is True only
        for the JS-driven subset (role=combobox or non-none aria-autocomplete) whose
        dropdowns populate asynchronously and need a ~0.4s settle before the next
        click — native <datalist> (list attr) and loose aria-haspopup render
        synchronously and are excluded from the wait.
        """
        attrs = getattr(entry, "attributes", {}) or {}
        if attrs.get("role") == "combobox":
            return True, True
        aria_ac = attrs.get("aria-autocomplete", "")
        if aria_ac and aria_ac != "none":
            return True, True
        if attrs.get("list"):
            return True, False  # native <datalist>: instant, no wait
        haspopup = attrs.get("aria-haspopup", "")
        if haspopup and haspopup != "false" and (attrs.get("aria-controls") or attrs.get("aria-owns")):
            return True, False
        return False, False

    async def _action_input_text(self, params: dict, browser: BrowserSession) -> ActionResult:
        entry, error = await self._get_element_by_index(params["index"], browser)
        if error:
            return error
        backend_id = entry.backend_node_id
        text = params["text"]
        clear = params.get("clear", True)

        # 1. Focus: highlight -> click_element, mapping the bool signal
        #    (mirrors _action_click — no silent success on focus failure).
        try:
            await browser.highlight_element(backend_id)
            clicked = await browser.click_element(backend_id)
        except Exception as e:
            return ActionResult(error=f"Input focus failed: {e}")
        if not clicked:
            return ActionResult(
                error=(
                    f"Could not focus element {params['index']} for input "
                    f"(no coordinates and JS click fallback failed; "
                    f"the element may be detached, hidden, or in a cross-origin iframe)"
                ),
            )
        await asyncio.sleep(0.1)  # retain the original focus settle

        # 2. Type: date/time/special inputs reject per-char key events, so
        #    assign directly via the native setter (_force_set_value) instead
        #    of _type_char. Mirrors browser-use _requires_direct_value_assignment
        #    / _set_value_directly.
        try:
            if _requires_direct_value_assignment(entry):
                if clear:
                    await browser._clear_text_field()
                await browser._force_set_value(text)
            else:
                await browser.type_text(text, clear=clear)
        except Exception as e:
            return ActionResult(error=f"Failed to type text into element {params['index']}: {e}")

        # 3. autocomplete/combobox: sleep ~0.4s on the JS-driven subset so the
        #    dropdown populates before the next action (browser-use service.py:812-819).
        is_combo, needs_js_wait = self._is_autocomplete_field(entry)
        if needs_js_wait:
            await asyncio.sleep(0.4)

        # 4. Value verification: read back activeElement and append a ⚠️ Note when
        #    it differs from the intended text (browser-use service.py:804-810).
        #    _read_active_text swallows exceptions and returns "" — safe to call.
        memory = self._describe_input(entry, params["index"], text)
        actual = await browser._read_active_text()
        if actual and actual != text:
            memory += (
                f"  ⚠️ Note: the field's actual value {actual!r} differs from "
                f"the intended {text!r}. The site may have reformatted, truncated, "
                f"or rejected the input — re-observe before continuing."
            )
        if is_combo:
            memory += (
                "  💡 autocomplete field — select from the JS-populated dropdown "
                "if applicable instead of typing the full value."
            )
        logger.info(memory)
        return ActionResult(extracted_content=memory, long_term_memory=memory)

    async def _action_scroll(self, params: dict, browser: BrowserSession) -> ActionResult:
        direction = params.get("direction", "down")
        amount = int(params.get("amount", 3))
        try:
            # G5: scroll 现在返回 {vertical_percentage, at_edge}（G2 位置读取）
            position = await browser.scroll(direction, amount)
        except Exception as e:
            # G4: scroll 非幂等，CDP 失败=没滚，必须报 error（区别于 close_tab 的软成功）
            logger.warning("scroll(%s, %d) failed: %s", direction, amount, e)
            return ActionResult(error=f"Scroll failed: {e}")
        # G1 + G2: 回显方向/量 + 当前位置；已到边界则当轮提示
        memory = f"Scrolled {direction} {amount} viewport-heights"
        pct = position.get("vertical_percentage")
        if pct is not None:
            memory += f" ({pct}% down)"
        if position.get("at_edge"):
            memory += f" (already at {direction}, no further content)"
        logger.info(memory)
        return ActionResult(extracted_content=memory, long_term_memory=memory)

    async def _action_search(self, params: dict, browser: BrowserSession) -> ActionResult:
        import urllib.parse

        query = params["query"]
        engine = params.get("engine", "baidu")
        encoded_query = urllib.parse.quote_plus(query)
        url = _SEARCH_ENGINE_URLS[engine].format(query=encoded_query)
        await browser.navigate(url)
        memory = f"Searched {engine.title()} for '{query}'"
        return ActionResult(extracted_content=memory, long_term_memory=memory)

    async def _action_extract(self, params: dict, browser: BrowserSession) -> ActionResult:
        goal = params["goal"]
        try:
            page_text = await browser.execute_js("document.body.innerText")
        except Exception:
            page_text = ""
        if not page_text:
            return ActionResult(extracted_content="(empty page)")
        # Simple extraction: return first 2000 chars with goal context
        from tree_walker.llm.client import LLMClient
        llm = getattr(self, "_extract_llm", None)
        if llm:
            result = await llm.extract(goal, page_text[:self._truncation.extract_page_max_chars])
            return ActionResult(extracted_content=result)
        return ActionResult(extracted_content=page_text[:self._truncation.extract_fallback_max_chars])

    async def _action_send_keys(self, params: dict, browser: BrowserSession) -> ActionResult:
        keys = params["keys"]
        try:
            await browser.send_keys(keys)
        except Exception as e:
            # send_keys is NOT idempotent (a key can submit a form / trigger
            # navigation), so a CDP failure must surface as error — mirrors the
            # scroll pattern, unlike close_tab's soft-success degradation.
            logger.warning("send_keys(%r) failed: %s", keys, e)
            return ActionResult(error=f"Send keys failed: {e}")
        memory = f"Sent keys '{keys}'"
        logger.info(memory)
        return ActionResult(extracted_content=memory, long_term_memory=memory)

    async def _action_switch_tab(self, params: dict, browser: BrowserSession) -> ActionResult:
        tab_id_suffix = params["tab_id"]
        tabs = await browser.get_tabs()
        matches = [t for t in tabs if t.target_id.endswith(tab_id_suffix)]
        if not matches:
            return ActionResult(
                error=f"No tab ending with '{tab_id_suffix}'. "
                      f"Open tabs: {self._summarize_tabs(tabs)}",
            )
        if len(matches) > 1:  # 后缀撞车：切错页风险，要求更长后缀/完整 target_id
            return ActionResult(
                error=f"Multiple tabs match '{tab_id_suffix}' ({len(matches)}). "
                      f"Use more characters or the full target_id. "
                      f"Matches: {self._summarize_tabs(matches)}",
            )
        target = matches[0]
        await browser.switch_tab(target.target_id)
        memory = f"Switched to tab [{tab_id_suffix}] {target.title} ({target.url})"
        logger.info(memory)
        return ActionResult(extracted_content=memory, long_term_memory=memory)

    @staticmethod
    def _summarize_tabs(tabs: list) -> str:
        """把标签页列表摘要成短串，供 switch_tab 的 error/回显复用。"""
        items = []
        for t in tabs:
            title = (t.title or "").strip()[:40]
            url = (t.url or "").strip()[:60]
            items.append(f"[{t.target_id[-4:]}] {title} - {url}")
        return "; ".join(items)

    async def _action_close_tab(self, params: dict, browser: BrowserSession) -> ActionResult:
        tab_id_suffix = params.get("tab_id", "")
        tabs = await browser.get_tabs()  # G2: 轻量枚举，取代 get_state(include_screenshot=False)
        if tab_id_suffix:
            # 指定 tab_id：后缀匹配 + 冲突检测（G3）+ 未命中列出（G4）
            matches = [t for t in tabs if t.target_id.endswith(tab_id_suffix)]
            if not matches:
                return ActionResult(
                    error=f"No tab ending with '{tab_id_suffix}'. "
                          f"Open tabs: {self._summarize_tabs(tabs)}",
                )
            if len(matches) > 1:  # 后缀撞车：关错页风险，要求更长后缀/完整 target_id
                return ActionResult(
                    error=f"Multiple tabs match '{tab_id_suffix}' ({len(matches)}). "
                          f"Use more characters or the full target_id. "
                          f"Matches: {self._summarize_tabs(matches)}",
                )
            target = matches[0]
            target_id, id_echo, title, url = (
                target.target_id, tab_id_suffix, target.title, target.url,
            )
        else:
            # G6: 空 tab_id = 关当前页（保留原语义，补回显）
            if not browser.current_target_id:
                return ActionResult(error="No current tab to close")
            target_id = browser.current_target_id
            cur = next((t for t in tabs if t.target_id == target_id), None)
            id_echo = target_id[-4:]
            title = cur.title if cur else ""
            url = cur.url if cur else ""
        # 关闭（G1 回显 + G5 软降级，对齐 browser-use service.py:1011-1018）
        try:
            await browser.close_tab(target_id)
        except Exception as e:
            logger.warning("close_tab(%s) failed: %s", target_id, e)
            memory = f"Tab [{id_echo}] {title} ({url}) was already closed or invalid"
            return ActionResult(extracted_content=memory, long_term_memory=memory)
        memory = f"Closed tab [{id_echo}] {title} ({url})"
        logger.info(memory)
        return ActionResult(extracted_content=memory, long_term_memory=memory)

    async def _action_wait(self, params: dict, browser: BrowserSession) -> ActionResult:
        await asyncio.sleep(float(params.get("seconds", 3)))
        return ActionResult()

    async def _action_go_back(self, params: dict, browser: BrowserSession) -> ActionResult:
        try:
            target_url = await browser.go_back()
        except Exception as e:
            return ActionResult(error=f"Failed to go back: {e}")

        if target_url is None:
            # 无历史可退（currentIndex <= 0）——明确告知，避免 LLM 误以为已后退
            return ActionResult(error="No previous page in history to go back to")

        # 轻量健康检查：SPA 后退未渲染给一次重试机会（仍空仅 warning，不硬失败）
        await self._go_back_health_check(target_url, browser)

        memory = f"Navigated back to {target_url}"
        logger.info(memory)
        return ActionResult(extracted_content=memory, long_term_memory=memory)

    async def _go_back_health_check(self, target_url: str | None, browser: BrowserSession) -> None:
        """后退后轻量空 DOM 检测（用户选：轻量，不 reload、不硬失败）。

        与 _navigate_health_check 的差异：go_back 没有"reload 同 URL"的干净机制
        （重新 navigate 会新增历史项），故仅做一次重试等待，持续空只 warning 不抛错，
        交由 LLM 下一轮 get_state 自行感知。仅当后退目标为 http(s) 时触发。
        """
        state = await browser.get_state(include_screenshot=False)
        url_is_http = state.url.lower().startswith(("http://", "https://"))
        if not (url_is_http and self._dom_appears_empty(state)):
            return

        logger.warning(
            "Empty DOM after going back to %s, waiting %.0fs and rechecking",
            target_url, _NAVIGATE_EMPTY_RETRY_WAIT,
        )
        await asyncio.sleep(_NAVIGATE_EMPTY_RETRY_WAIT)
        state = await browser.get_state(include_screenshot=False)
        if state.url.lower().startswith(("http://", "https://")) and self._dom_appears_empty(state):
            logger.warning(
                "Still empty after going back to %s; SPA may still be rendering. "
                "Not failing hard (no clean reload for history navigation).",
                target_url,
            )

    async def _action_find_elements(self, params: dict, browser: BrowserSession) -> ActionResult:
        selector = params["selector"]
        js_code = (
            f"Array.from(document.querySelectorAll({repr(selector)}))"
            ".map((e, i) => ({"
            "index: i, tag: e.tagName, text: (e.textContent || '').substring(0, 100).trim(),"
            "href: e.href || '', visible: e.offsetParent !== null"
            "}))"
        )
        try:
            result = await browser.execute_js(js_code)
            return ActionResult(extracted_content=str(result))
        except Exception as e:
            return ActionResult(error=str(e))

    async def _action_find_text(self, params: dict, browser: BrowserSession) -> ActionResult:
        text = params["text"]
        try:
            info = await browser.find_text(text)
        except Exception as e:
            # CDP layer failure (connection drop / DOM domain error) = tool
            # execution failure; surface a find_text-specific error rather
            # than the generic Tools.execute fallback.
            logger.warning("find_text(%r) failed: %s", text, e)
            return ActionResult(error=f"Find text failed: {e}")
        if not info.get("found"):
            # Soft echo: "text not on the page" is actionable info (the LLM
            # can scroll / switch tab / accept the text is absent), not a tool
            # failure — aligns with browser-use and the sibling search_page
            # action (both return extracted_content, not error, on a miss).
            msg = f"Text '{text}' not found on page"
            logger.info(msg)
            return ActionResult(extracted_content=msg, long_term_memory=msg)
        method = info.get("method")
        tag = info.get("tag")
        if tag:
            memory = f"Scrolled to text '{text}' into view (found in <{tag}>, via {method})"
        else:
            memory = f"Scrolled to text '{text}' into view (via {method})"
        logger.info(memory)
        return ActionResult(extracted_content=memory, long_term_memory=memory)

    async def _action_screenshot(self, params: dict, browser: BrowserSession) -> ActionResult:
        screenshot_bytes = await browser.take_screenshot()
        save_path = params.get("save_path", "")
        if save_path:
            with open(save_path, "wb") as f:
                f.write(screenshot_bytes)
            return ActionResult(extracted_content=f"Screenshot saved to {save_path}")
        return ActionResult()

    async def _action_save_as_pdf(self, params: dict, browser: BrowserSession) -> ActionResult:
        import base64
        result = await browser.client.send.Page.printToPDF(
            {"printBackground": True},
            session_id=browser.current_session_id,
        )
        pdf_data = base64.b64decode(result["data"])
        path = params["path"]
        with open(path, "wb") as f:
            f.write(pdf_data)
        return ActionResult(extracted_content=f"PDF saved to {path}")

    async def _action_dropdown_options(self, params: dict, browser: BrowserSession) -> ActionResult:
        entry, error = await self._get_element_by_index(params["index"], browser)
        if error:
            return error
        js_code = (
            "Array.from(document.querySelectorAll('select option'))"
            ".map(o => ({value: o.value, text: o.textContent.trim(), selected: o.selected}))"
        )
        options = await browser.execute_js(js_code)
        return ActionResult(extracted_content=str(options))

    async def _action_select_dropdown(self, params: dict, browser: BrowserSession) -> ActionResult:
        entry, error = await self._get_element_by_index(params["index"], browser)
        if error:
            return error
        value = params["value"]
        await browser.execute_js(
            f"document.querySelectorAll('select')[0].value = {repr(value)}; "
            f"document.querySelectorAll('select')[0].dispatchEvent(new Event('change'))"
        )
        return ActionResult()

    async def _action_upload_file(self, params: dict, browser: BrowserSession) -> ActionResult:
        file_path = params["path"]

        # 1. 路径白名单 + 文件存在/非空校验（保持原逻辑）
        allowed = self._allowed_upload_paths
        if allowed:
            if not any(file_path.startswith(p) for p in allowed):
                return ActionResult(
                    error=f"File path not in allowed upload paths: {file_path}",
                )
        if not os.path.isfile(file_path):
            return ActionResult(error=f"File not found: {file_path}")
        if os.path.getsize(file_path) == 0:
            return ActionResult(error=f"File is empty: {file_path}")

        # 2. 元素查找（保持原逻辑）
        entry, error = await self._get_element_by_index(params["index"], browser)
        if error:
            return error

        # 3. 判定目标是否本身 file input；非 file input 时定位最近 file input
        tag = entry.tag_name.upper()
        attrs = entry.attributes
        is_file_input = tag == "INPUT" and attrs.get("type", "").lower() == "file"

        backend_id = entry.backend_node_id
        file_input_ids: list[int] = []
        if not is_file_input:
            if self._cached_browser_state and self._cached_browser_state.dom_state:
                file_input_ids = list(
                    self._cached_browser_state.dom_state.file_input_backend_ids,
                )
            if not file_input_ids:
                return ActionResult(
                    error="Element is not a file input and no file input found on page",
                )
            # Pick the file input nearest to the target element
            backend_id = _pick_nearest_file_input(
                entry, file_input_ids,
                self._cached_browser_state.dom_state if self._cached_browser_state else None,
            )

        logger.info(
            "upload_file: index=%d, tag=%s, type=%s, backend_node_id=%s, "
            "is_file_input=%s, resolved_backend_id=%s, available_file_inputs=%s",
            params["index"], tag, attrs.get("type", ""), entry.backend_node_id,
            is_file_input, backend_id, file_input_ids,
        )

        # 4. 高亮 + 上传（对齐 _action_click：共用 try，统一映射；highlight best-effort）
        try:
            await browser.highlight_element(backend_id)
            await browser.set_file_input(
                backend_node_id=backend_id,
                file_path=file_path,
                file_input_backend_ids=file_input_ids if not is_file_input else None,
            )
        except Exception as e:
            return ActionResult(error=f"File upload failed: {e}")

        # 5. 成功回显（G1）+ 目标替换提示（G2）+ accept 软校验（G3）
        memory = self._describe_upload(entry, params["index"], file_path)
        if not is_file_input:
            memory += (
                f"  ⚠️ Note: index {params['index']} is not an <input type='file'>; "
                f"uploaded to the nearest file input on the page instead."
            )

        if is_file_input:
            file_input_entry = entry
        else:
            file_input_entry = self._find_node_by_backend_id(
                backend_id,
                self._cached_browser_state.dom_state if self._cached_browser_state else None,
            )
        accept_attr = None
        if file_input_entry is not None:
            accept_attr = (getattr(file_input_entry, "attributes", {}) or {}).get("accept")
        if accept_attr and not _file_matches_accept(file_path, accept_attr):
            memory += (
                f"  ⚠️ Note: the file extension does not match this input's "
                f"accept={accept_attr!r} — the site may reject the upload."
            )

        logger.info(memory)
        return ActionResult(extracted_content=memory, long_term_memory=memory)

    async def _action_write_file(self, params: dict, browser: BrowserSession) -> ActionResult:
        path = params["path"]
        content = params["content"]
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return ActionResult(extracted_content=f"Written to {path}")

    async def _action_read_file(self, params: dict, browser: BrowserSession) -> ActionResult:
        path = params["path"]
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            return ActionResult(extracted_content=content[:self._truncation.read_file_max_chars])
        except FileNotFoundError:
            return ActionResult(error=f"File not found: {path}")

    async def _action_replace_file(self, params: dict, browser: BrowserSession) -> ActionResult:
        path = params["path"]
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            content = content.replace(params["old"], params["new"])
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return ActionResult()
        except FileNotFoundError:
            return ActionResult(error=f"File not found: {path}")

    async def _action_evaluate(self, params: dict, browser: BrowserSession) -> ActionResult:
        code = params["code"]
        try:
            result = await browser.execute_js(code)
            return ActionResult(extracted_content=str(result)[:self._truncation.eval_result_max_chars])
        except Exception as e:
            return ActionResult(error=str(e))

    async def _action_search_page(self, params: dict, browser: BrowserSession) -> ActionResult:
        query = params["query"]
        js_code = (
            "(function() {"
            "  const body = document.body.innerText;"
            f"  const lines = body.split('\\n').filter(l => l.toLowerCase().includes({repr(query.lower())}));"
            "  return lines.slice(0, 20).join('\\n');"
            "})()"
        )
        try:
            result = await browser.execute_js(js_code)
            if result:
                return ActionResult(extracted_content=str(result))
            return ActionResult(extracted_content=f"No matches for '{query}'")
        except Exception as e:
            return ActionResult(error=str(e))

    async def _action_done(self, params: dict, browser: BrowserSession) -> ActionResult:
        return ActionResult(
            is_done=True,
            success=params.get("success", True),
            extracted_content=params.get("text", ""),
        )

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _flatten_params(params: dict, action_name: str) -> dict:
        """Handle nested params like {"click": {"index": 5}} -> {"index": 5}."""
        if not params:
            return params
        if action_name in params and isinstance(params[action_name], dict):
            return params[action_name]
        # Check for any single nested dict value (common LLM pattern)
        dict_vals = {k: v for k, v in params.items() if isinstance(v, dict)}
        if len(dict_vals) == 1 and len(params) == 1:
            return list(dict_vals.values())[0]
        return params

    @staticmethod
    def _normalize(result: ActionResult | str | None) -> ActionResult:
        if isinstance(result, ActionResult):
            return result
        if isinstance(result, str):
            return ActionResult(extracted_content=result)
        return ActionResult()
