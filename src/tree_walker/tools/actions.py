"""Action execution engine — registers all browser actions and executes them."""

from __future__ import annotations

import asyncio
from typing import Any
import logging
import os

from tree_walker.agent.views import ActionResult
from tree_walker.browser.session import BrowserSession
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
        await browser.navigate(url)
        return ActionResult()

    async def _action_click(self, params: dict, browser: BrowserSession) -> ActionResult:
        entry, error = await self._get_element_by_index(params["index"], browser)
        if error:
            return error
        tag = entry.tag_name.upper()
        if tag == "SELECT":
            js_code = (
                "Array.from(document.querySelectorAll('select option'))"
                ".map(o => ({value: o.value, text: o.textContent.trim()}))"
            )
            options = await browser.execute_js(js_code)
            return ActionResult(extracted_content=str(options))
        await browser.highlight_element(entry.backend_node_id)
        await browser.click_element(entry.backend_node_id)
        return ActionResult()

    async def _action_input_text(self, params: dict, browser: BrowserSession) -> ActionResult:
        entry, error = await self._get_element_by_index(params["index"], browser)
        if error:
            return error
        await browser.highlight_element(entry.backend_node_id)
        await browser.click_element(entry.backend_node_id)
        await asyncio.sleep(0.1)
        await browser.type_text(params["text"], clear=params.get("clear", True))
        return ActionResult()

    async def _action_scroll(self, params: dict, browser: BrowserSession) -> ActionResult:
        await browser.scroll(params.get("direction", "down"), int(params.get("amount", 3)))
        return ActionResult()

    async def _action_search(self, params: dict, browser: BrowserSession) -> ActionResult:
        query = params["query"]
        url = f"https://www.google.com/search?q={query}"
        await browser.navigate(url)
        return ActionResult()

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
        await browser.send_keys(params["keys"])
        return ActionResult()

    async def _action_switch_tab(self, params: dict, browser: BrowserSession) -> ActionResult:
        tab_id_suffix = params["tab_id"]
        state = await browser.get_state(include_screenshot=False)
        for tab in state.tabs:
            if tab.target_id.endswith(tab_id_suffix):
                await browser.switch_tab(tab.target_id)
                return ActionResult()
        return ActionResult(error=f"Tab ending with '{tab_id_suffix}' not found")

    async def _action_close_tab(self, params: dict, browser: BrowserSession) -> ActionResult:
        tab_id = params.get("tab_id", "")
        if tab_id:
            state = await browser.get_state(include_screenshot=False)
            for tab in state.tabs:
                if tab.target_id.endswith(tab_id):
                    await browser.close_tab(tab.target_id)
                    return ActionResult()
            return ActionResult(error=f"Tab ending with '{tab_id}' not found")
        if browser.current_target_id:
            await browser.close_tab(browser.current_target_id)
        return ActionResult()

    async def _action_wait(self, params: dict, browser: BrowserSession) -> ActionResult:
        await asyncio.sleep(float(params.get("seconds", 3)))
        return ActionResult()

    async def _action_go_back(self, params: dict, browser: BrowserSession) -> ActionResult:
        await browser.go_back()
        return ActionResult()

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
            found = await browser.execute_js(
                f"window.find({repr(text)})"
            )
            if found:
                return ActionResult()
            return ActionResult(error=f"Text '{text}' not found on page")
        except Exception as e:
            return ActionResult(error=str(e))

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

        entry, error = await self._get_element_by_index(params["index"], browser)
        if error:
            return error

        # Check if the element itself is a file input
        tag = entry.tag_name.upper()
        attrs = entry.attributes
        is_file_input = tag == "INPUT" and attrs.get("type", "").lower() == "file"

        available_ids = (
            self._cached_browser_state.dom_state.file_input_backend_ids
            if self._cached_browser_state and self._cached_browser_state.dom_state
            else []
        )
        logger.info(
            "upload_file: index=%d, tag=%s, type=%s, backend_node_id=%s, "
            "is_file_input=%s, available_file_inputs=%s",
            params["index"], tag, attrs.get("type", ""), entry.backend_node_id,
            is_file_input, available_ids,
        )

        backend_id = entry.backend_node_id
        file_input_ids: list[int] = []

        # If not a file input, try to find one from DOM state
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

        try:
            await browser.highlight_element(backend_id)
            await browser.set_file_input(
                backend_node_id=backend_id,
                file_path=file_path,
                file_input_backend_ids=file_input_ids if not is_file_input else None,
            )
        except Exception as e:
            return ActionResult(error=f"File upload failed: {e}")
        return ActionResult(extracted_content=f"Uploaded {file_path}")

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
