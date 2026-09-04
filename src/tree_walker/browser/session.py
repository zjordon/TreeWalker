"""Browser session management via cdp-use CDP WebSocket client."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
from collections import deque
from typing import Any, Literal

from cdp_use import CDPClient

from tree_walker.browser.circuit_breaker import CircuitBreaker
from dom_snapshot import (
    attach_to_iframe_target,
    build_frame_target_map,
    build_dom_state,
)
from tree_walker.browser.highlight import HighlightManager
from tree_walker.browser.network_idle import NetworkIdleTracker
from tree_walker.config import BrowserSettings
from dom_snapshot import (
    DOMCollectionConfig,
    DOMDegradationLevel,
    DOMRect,
    DOMSelectorMap,
)
from tree_walker.browser.views import (
    BrowserEvent,
    BrowserStateSummary,
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


# XPath alphabet constants for case-insensitive contains() (XPath 1.0 has no
# native case-insensitive match): translate(., LOWER, UPPER) upper-cases both
# haystack and needle so contains() compares case-insensitively. Used by
# _text_queries when case_sensitive=False (G10).
_XPATH_LOWER = "'abcdefghijklmnopqrstuvwxyz'"
_XPATH_UPPER = "'ABCDEFGHIJKLMNOPQRSTUVWXYZ'"

# Cap on how many matches find_text fetches per XPath query; the G9 visibility
# probe and G8 nth selection operate on this batch. Large result sets are
# truncated — the true total is still reported in the echo.
_FIND_TEXT_CAP = 50


def _text_queries(text: str, case_sensitive: bool) -> list[tuple[str, str]]:
    """Build the 3-query XPath chain for find_text.

    case_sensitive=False wraps both the haystack and the needle in
    translate(., _XPATH_LOWER, _XPATH_UPPER) so contains() matches
    case-insensitively (XPath 1.0 has no native case-insensitive contains).
    The needle is still run through _xpath_string_literal first, so
    quote-safety and case-folding are orthogonal.
    """
    lit = _xpath_string_literal(text)
    needle = lit if case_sensitive else f"translate({lit}, {_XPATH_LOWER}, {_XPATH_UPPER})"
    wrap = (lambda e: e) if case_sensitive else (
        lambda e: f"translate({e}, {_XPATH_LOWER}, {_XPATH_UPPER})"
    )
    return [
        ("xpath-text", f"//*[contains({wrap('text()')}, {needle})]"),
        ("xpath-content", f"//*[contains({wrap('.')}, {needle})]"),
        ("xpath-attr", f"//*[@*[contains({wrap('.')}, {needle})]]"),
    ]


# ── search_page (grep-style page text search) ────────────────────────

# JS body for search_page: a TreeWalker over text nodes builds an offset-
# indexed text buffer, then a g-flag RegExp.exec loop collects matches with
# context + element path, returning {matches, total, has_more} (or {error}).
# Mirrors browser-use _SEARCH_PAGE_JS_BODY (service.py:181-255). User values
# are injected as `var` declarations by _build_search_page_js (never
# f-string-interpolated into the expression), so this body only references
# those vars (PATTERN/IS_REGEX/CASE_SENSITIVE/CONTEXT_CHARS/CSS_SCOPE/
# MAX_RESULTS/OFFSET/SEARCH_ATTRIBUTES) by name. Stored raw so JS backslashes
# survive verbatim.
_SEARCH_PAGE_JS_BODY = r"""
    function _getPath(el) {
        if (!el || el === document.body) return '';
        var parts = [];
        while (el && el !== document.body) {
            var tag = (el.tagName || '').toLowerCase();
            if (!tag) break;
            var part = tag;
            if (el.id) {
                part += '#' + el.id;
            } else if (el.className && typeof el.className === 'string') {
                var cls = el.className.trim().split(/\s+/).slice(0, 2).join('.');
                if (cls) part += '.' + cls;
            }
            parts.unshift(part);
            el = el.parentElement;
        }
        return parts.join(' > ');
    }
    function _origin(node) {
        // 标记非顶层文档来源：ShadowRoot(nodeType=11) → shadow DOM；其它(getRootNode≠document) → iframe
        try {
            var r = node.getRootNode ? node.getRootNode() : null;
            if (r && r !== document) {
                return r.nodeType === 11 ? ' (in shadow DOM)' : ' (in iframe)';
            }
        } catch (_) {}
        return '';
    }
    function _collectText(root) {
        var wt = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        var n;
        while ((n = wt.nextNode())) {
            var t = n.textContent;
            if (t && t.trim()) {
                nodeOffsets.push({offset: fullText.length, length: t.length, node: n});
                fullText += t;
            }
        }
        // 穿透：开放 shadow root + 同源 iframe contentDocument（TreeWalker 不跨 shadow / 文档边界，需手动递归）
        var we = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
        var el;
        while ((el = we.nextNode())) {
            if (el.shadowRoot) {
                try { _collectText(el.shadowRoot); } catch (_) {}      // closed shadow: shadowRoot=null，自然跳过
            }
            if (el.tagName === 'IFRAME') {
                try {
                    var cd = el.contentDocument;                       // 同源可读；跨源抛 SecurityError → catch 跳过（阶段三）
                    if (cd && cd.body) _collectText(cd.body);
                } catch (_) {}
            }
        }
    }
    try {
        var scope = CSS_SCOPE ? document.querySelector(CSS_SCOPE) : document.body;
        if (!scope) {
            return {error: 'CSS scope selector not found: ' + CSS_SCOPE, matches: [], total: 0};
        }
        var fullText = '';
        var nodeOffsets = [];
        _collectText(scope);
        var flags = CASE_SENSITIVE ? 'g' : 'gi';
        var re;
        try {
            if (IS_REGEX) {
                re = new RegExp(PATTERN, flags);
            } else {
                re = new RegExp(PATTERN.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), flags);
            }
        } catch (e) {
            return {error: 'Invalid regex pattern: ' + (e && e.message ? e.message : e), matches: [], total: 0};
        }
        var matches = [];
        var total = 0;
        var match;
        while ((match = re.exec(fullText)) !== null) {
            total++;
            // offset 窗口：累计全部 total，只存 [OFFSET, OFFSET+MAX_RESULTS) 区间（保持 early-bail 性能）
            if (total - 1 >= OFFSET && matches.length < MAX_RESULTS) {
                var start = Math.max(0, match.index - CONTEXT_CHARS);
                var end = Math.min(fullText.length, match.index + match[0].length + CONTEXT_CHARS);
                var context = fullText.slice(start, end);
                var elementPath = '';
                for (var i = 0; i < nodeOffsets.length; i++) {
                    var no = nodeOffsets[i];
                    if (no.offset <= match.index && no.offset + no.length > match.index) {
                        elementPath = _getPath(no.node.parentElement) + _origin(no.node);
                        break;
                    }
                }
                matches.push({
                    match_text: match[0],
                    context: (start > 0 ? '...' : '') + context + (end < fullText.length ? '...' : ''),
                    element_path: elementPath,
                    char_position: match.index
                });
            }
            if (match[0].length === 0) re.lastIndex++;
        }
        var attribute_matches = [];
        var attribute_total = 0;
        if (SEARCH_ATTRIBUTES) {
            // 非全局 RegExp 副本做 .test，规避全局正则 lastIndex 漂移
            var reAttr = new RegExp(re.source, CASE_SENSITIVE ? '' : 'i');
            var we = document.createTreeWalker(scope, NodeFilter.SHOW_ELEMENT);
            var el;
            while ((el = we.nextNode())) {
                var attrs = el.attributes;
                if (!attrs) continue;
                for (var a = 0; a < attrs.length; a++) {
                    var av = attrs[a].value;
                    if (av && reAttr.test(av)) {
                        attribute_total++;
                        if (attribute_matches.length < MAX_RESULTS) {
                            attribute_matches.push({
                                attribute: attrs[a].name,
                                value: av,
                                element_path: _getPath(el) + _origin(el)
                            });
                        }
                    }
                }
            }
        }
        return {
            matches: matches,
            total: total,
            offset: OFFSET,
            has_more: (OFFSET + matches.length) < total,
            attribute_matches: attribute_matches,
            attribute_total: attribute_total
        };
    } catch (e) {
        return {error: String((e && e.message) || e), matches: [], total: 0};
    }
"""


def _build_search_page_js(
    pattern: str,
    regex: bool,
    case_sensitive: bool,
    context_chars: int,
    css_scope: str | None,
    max_results: int,
    offset: int,
    search_attributes: bool,
) -> str:
    """Build the search_page IIFE expression.

    Each user value is serialized via ``json.dumps`` into a ``var``
    declaration and the body references those vars by name — the body is a
    constant string with NO f-string interpolation of user text. This is the
    safe-injection pattern (mirrors browser-use service.py:301-318) and
    matches the project's existing _find_text_js_fallback (json.dumps at
    session.py:1899).
    """
    params_js = (
        f"var PATTERN = {json.dumps(pattern)};\n"
        f"var IS_REGEX = {json.dumps(regex)};\n"
        f"var CASE_SENSITIVE = {json.dumps(case_sensitive)};\n"
        f"var CONTEXT_CHARS = {json.dumps(context_chars)};\n"
        f"var CSS_SCOPE = {json.dumps(css_scope)};\n"
        f"var MAX_RESULTS = {json.dumps(max_results)};\n"
        f"var OFFSET = {json.dumps(offset)};\n"
        f"var SEARCH_ATTRIBUTES = {json.dumps(search_attributes)};\n"
    )
    return "(function() {\n" + params_js + _SEARCH_PAGE_JS_BODY + "\n})()"


# ── find_elements (CSS selector element query) ──────────────────────

# JS body for find_elements: querySelectorAll + per-element extraction
# (tag, text, attributes, children_count), returning {elements, total,
# showing} (or {error}). Mirrors browser-use _FIND_ELEMENTS_JS_BODY
# (service.py:257-298). User values are injected as `var` declarations by
# _build_find_elements_js (never f-string-interpolated), so this body only
# references SELECTOR/ATTRIBUTES/MAX_RESULTS/INCLUDE_TEXT by name. Stored
# raw so JS backslashes survive verbatim.
_FIND_ELEMENTS_JS_BODY = r"""
    function _origin(node) {
        // 标记非顶层文档来源：ShadowRoot(nodeType=11) → shadow DOM；其它(getRootNode≠document) → iframe
        try {
            var r = node.getRootNode ? node.getRootNode() : null;
            if (r && r !== document) {
                return r.nodeType === 11 ? ' (in shadow DOM)' : ' (in iframe)';
            }
        } catch (_) {}
        return '';
    }
    function _collectAll(root, out) {
        // 穿透：开放 shadow root + 同源 iframe contentDocument
        // （TreeWalker 不跨 shadow / 文档边界，需手动递归；镜像 _SEARCH_PAGE_JS_BODY._collectText）
        var we = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
        var el;
        while ((el = we.nextNode())) {
            out.push(el);
            if (el.shadowRoot) {
                try { _collectAll(el.shadowRoot, out); } catch (_) {}      // closed shadow: shadowRoot=null，自然跳过
            }
            if (el.tagName === 'IFRAME') {
                try {
                    var cd = el.contentDocument;                           // 同源可读；跨源抛 SecurityError → catch 跳过
                    if (cd && cd.body) _collectAll(cd.body, out);
                } catch (_) {}
            }
        }
    }
    function _isVisible(el) {
        // 可信可见性：祖先链 display/visibility/opacity + 自身非零尺寸
        // （修复阶段一 offsetParent !== null 的浅检测）
        var node = el;
        while (node && node.nodeType === 1) {
            var cs = document.defaultView.getComputedStyle(node);
            if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') {
                return false;
            }
            node = node.parentElement;
        }
        var r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    }
    try {
        try {
            // 选择器合法性先校验一次（invalid selector 会让下面的 matches() 抛 SyntaxError）
            document.querySelector(SELECTOR);
        } catch (e) {
            return {error: 'Invalid CSS selector: ' + (e && e.message ? e.message : e), elements: [], total: 0};
        }
        // 收集顶层文档 + 所有开放 shadow / 同源 iframe 内的元素
        var all = [];
        _collectAll(document.documentElement, all);
        var total = 0;
        var limit = FIRST_ONLY ? 1 : MAX_RESULTS;
        var results = [];
        for (var k = 0; k < all.length; k++) {
            if (all[k].matches(SELECTOR)) {
                // offset 窗口：累计全部 total，只存 [OFFSET, OFFSET+limit) 区间（保持 early-bail 性能）
                if (total >= OFFSET && results.length < limit) {
                    var el = all[k];
                    var item = {index: total, tag: el.tagName.toLowerCase(), origin: _origin(el)};
                    if (INCLUDE_TEXT) {
                        var text = (el.textContent || '').trim();
                        item.text = text.length > 300 ? text.slice(0, 300) + '...' : text;
                    }
                    if (ATTRIBUTES && ATTRIBUTES.length > 0) {
                        item.attrs = {};
                        for (var j = 0; j < ATTRIBUTES.length; j++) {
                            var attrName = ATTRIBUTES[j];
                            var val;
                            // src/href: use the resolved DOM property (absolute URL),
                            // not getAttribute (raw authored value, often relative).
                            if ((attrName === 'src' || attrName === 'href')
                                && typeof el[attrName] === 'string' && el[attrName] !== '') {
                                val = el[attrName];
                            } else {
                                val = el.getAttribute(attrName);
                            }
                            if (val !== null) {
                                item.attrs[attrName] = val.length > 500 ? val.slice(0, 500) + '...' : val;
                            }
                        }
                    }
                    item.children_count = el.children.length;
                    if (INCLUDE_GEOMETRY) {
                        var rect = el.getBoundingClientRect();
                        item.rect = {x: rect.left, y: rect.top, w: rect.width, h: rect.height};
                        item.visible = _isVisible(el);
                    }
                    results.push(item);
                }
                total++;
            }
        }
        return {
            elements: results,
            total: total,
            showing: results.length,
            offset: OFFSET,
            has_more: (OFFSET + results.length) < total
        };
    } catch (e) {
        return {error: 'find_elements error: ' + (e && e.message ? e.message : e), elements: [], total: 0};
    }
"""


def _build_find_elements_js(
    selector: str,
    attributes: list[str] | None,
    max_results: int,
    include_text: bool,
    first_only: bool,
    offset: int,
    include_geometry: bool,
) -> str:
    """Build the find_elements IIFE expression.

    Each user value is serialized via ``json.dumps`` into a ``var``
    declaration and the body references those vars by name — the body is a
    constant string with NO f-string interpolation of user text. This is the
    safe-injection pattern (mirrors browser-use service.py:321-334) and
    matches the project's ``_build_search_page_js`` above.
    """
    params_js = (
        f"var SELECTOR = {json.dumps(selector)};\n"
        f"var ATTRIBUTES = {json.dumps(attributes)};\n"
        f"var MAX_RESULTS = {json.dumps(max_results)};\n"
        f"var OFFSET = {json.dumps(offset)};\n"
        f"var INCLUDE_TEXT = {json.dumps(include_text)};\n"
        f"var FIRST_ONLY = {json.dumps(first_only)};\n"
        f"var INCLUDE_GEOMETRY = {json.dumps(include_geometry)};\n"
    )
    return "(function() {\n" + params_js + _FIND_ELEMENTS_JS_BODY + "\n})()"


# ── evaluate (arbitrary user JS execution) ──────────────────────────

# Pre/post handlers for BrowserSession.evaluate. The preprocessor fixes common
# LLM JavaScript quoting/escaping mistakes; the normalizer renders the CDP
# return value as an LLM-friendly string (JSON for objects, JS literals for
# bool/null/undefined); the exception formatter enriches CDP exceptionDetails.
# Mirrors browser-use service.py:1759-1932.


def _validate_and_fix_javascript(code: str) -> str:
    """Fix common LLM JavaScript quoting/escaping mistakes before evaluation.

    Mirrors browser-use ``_validate_and_fix_javascript`` (service.py:1869-1932).
    Pure regex cleanup; never interpolates user values into JS.
    """
    # 1: undo double-escaped quotes (\" -> "), common when LLM emits JSON-stringified JS
    fixed = re.sub(r'\\"', '"', code)
    # 2: undo over-escaped regex classes (\\d -> \d, \\[ -> \[)
    fixed = re.sub(r'\\\\([dDsSwWbBnrtfv])', r'\\\1', fixed)
    fixed = re.sub(r'\\\\([.*+?^${}()|[\]])', r'\\\1', fixed)
    # 3-6: mixed-quote selectors -> template literals (evaluate / querySelector / closest / matches)
    fixed = re.sub(
        r'document\.evaluate\s*\(\s*"([^"]*)"\s*,',
        lambda m: f'document.evaluate(`{m.group(1)}`,',
        fixed,
    )
    fixed = re.sub(
        r'(querySelector(?:All)?)\s*\(\s*"([^"]*)"\s*\)',
        lambda m: f'{m.group(1)}(`{m.group(2)}`)',
        fixed,
    )
    fixed = re.sub(
        r'\.closest\s*\(\s*"([^"]*)"\s*\)',
        lambda m: f'.closest(`{m.group(1)}`)',
        fixed,
    )
    fixed = re.sub(
        r'\.matches\s*\(\s*"([^"]*)"\s*\)',
        lambda m: f'.matches(`{m.group(1)}`)',
        fixed,
    )
    # 7（R5，P7 task1 附一）：LLM 在 JSON 里少写一层反斜杠时，`\n` 被 JSON 解析
    # 成真实换行进入正则字面量——`.replace(/\n/g, ...)` 实际变成
    # `.replace(/<换行>/g, ...)` → SyntaxError "Invalid regular expression:
    # missing /"。把单字符正则类里的裸控制字符转义回字面量形式（保守：只处理
    # /\n/ /\t/ /\r/（含 flags），不动其他结构）。
    fixed = re.sub(
        r"/([\n\t\r])/([a-z]*)",
        lambda m: "/" + {"\n": "\\n", "\t": "\\t", "\r": "\\r"}[m.group(1)] + "/" + m.group(2),
        fixed,
    )
    return fixed


def _normalize_eval_result(result_data: dict) -> str:
    """Normalize a Runtime.evaluate result value to an LLM-friendly string.

    Mirrors browser-use (service.py:1807-1819) with one fix: bool/null are
    rendered as JS literals (``true``/``false``/``null``), not Python
    ``True``/``None``, so output never carries Python repr semantics.
    """
    if "value" not in result_data:
        # CDP omits `value` when the expression returned `undefined`.
        return "undefined"
    value = result_data["value"]
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, bool):  # must precede int: bool is a subclass of int
        return "true" if value else "false"
    if value is None:
        return "null"
    # int / float / str
    return str(value)


def _syntax_repair_candidates(code: str, err_text: str) -> list[str]:
    """P7 form_interaction 建议5：按已知 SyntaxError 生成确定性修复候选（按序试跑）。

    2026-08-23 复盘（batch1 task-505 step 12 等，同类错误批次内 8 处）：
    - ``Illegal return statement``：LLM 写了顶层裸 return——Runtime.evaluate 按
      脚本体执行（Playwright 字符串路径实测同样拒绝，惯例是调用方自包）。包一层
      IIFE 即合法。注意与 ``args``/``elements`` 模式相反：那边代码被包进
      ``function(...a){...}``，裸 return 合法且必须——schema 描述已明示两种模式差异。
    - ``Missing catch or finally after try``：LLM 写了 try 没 catch（常伴随少一个
      闭合大括号）。真实失败样本呈两种形态：①括号均衡、仅缺 catch（catch 插在最后
      一个 ``}`` 之前可修）；②连函数闭合括号也缺（去掉尾部 ``})()`` 再补
      ``}catch(...){...}})()`` 可修）。两个候选按序试跑，语法错误无副作用，试错安全。

    其余错误返回空列表（不自愈，原样抛出）。
    """
    if "Illegal return statement" in err_text:
        return ["(()=>{\n" + code + "\n})()"]
    if "Missing catch or finally after try" in err_text:
        catch = "catch(e){return 'Error: '+e.message}"
        candidates: list[str] = []
        # 形态①：括号均衡、仅缺 catch —— 插在最后一个 } 之前
        idx = code.rfind("}")
        if idx > 0:
            candidates.append(code[:idx] + catch + code[idx:])
        # 形态②：连函数闭合括号也缺 —— 去掉尾部 })() 重建闭合
        if code.endswith("})()"):
            candidates.append(code[:-4] + "}" + catch + "})()")
        return candidates
    return []


def _format_eval_exception(exception: dict, validated_code: str) -> str:
    """Build a debugging-rich error message from CDP exceptionDetails.

    Mirrors browser-use (service.py:1784-1792) and goes one step further:
    also surface ``exception.description`` (full message + stack), which
    browser-use discards. Truncated to keep the message bounded.
    """
    text = exception.get("text", "Unknown error")
    parts = [f"JavaScript execution error: {text}"]
    description = exception.get("exception", {}).get("description")
    if description and description != text:
        parts.append(str(description)[:500])
    snippet = validated_code[:500] + ("..." if len(validated_code) > 500 else "")
    parts.append(f"Validated code (after quote fixing):\n{snippet}")
    return "\n".join(parts)


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

# ARIA menu/listbox options reader (ported from browser-use default_action_watchdog.py
# type='aria'). Returns null when the element is not aria-shaped (no listbox/menu/
# menubar/tree/grid role AND no [role=option]/[role=menuitem] descendants) so the
# dispatcher can try the next type; returns a list (possibly empty) when it IS
# aria-shaped. Options hard-capped to 200 (D4); a synthetic truncation row is
# appended when there are more.
_ARIA_OPTIONS_JS = """
function() {
    const root = this;
    const role = root.getAttribute('role');
    const isAriaContainer = ['listbox', 'menu', 'menubar', 'tree', 'grid'].indexOf(role) !== -1;
    const hasAriaOptions = !!root.querySelector('[role="option"],[role="menuitem"]');
    if (!isAriaContainer && !hasAriaOptions) return null;
    const all = Array.from(root.querySelectorAll('[role="menuitem"],[role="option"]'));
    const capped = all.slice(0, 200);
    const mapped = capped.map(function(n) {
        return {
            text: (n.textContent || '').trim(),
            value: n.getAttribute('data-value') || n.getAttribute('value') || (n.textContent || '').trim(),
            selected: n.getAttribute('aria-selected') === 'true' || n.classList.contains('selected') || n.classList.contains('active'),
        };
    });
    if (all.length > 200) {
        mapped.push({text: '... (showing 200 of ' + all.length + ', use scroll/search_page for more)', value: '', selected: false});
    }
    return mapped;
}
"""

# Custom-class dropdown options reader (ported from browser-use type='custom':
# Semantic UI / Foundation / etc.). Returns null when the element is not
# custom-shaped (no 'dropdown'/'ui' class) so the dispatcher can try the next
# type; a list (possibly empty) when it IS custom-shaped. Options hard-capped
# to 200 (D4); a synthetic truncation row is appended when there are more.
_CUSTOM_CLASS_OPTIONS_JS = """
function() {
    const root = this;
    if (!(root.classList.contains('dropdown') || root.classList.contains('ui'))) {
        return null;
    }
    const all = Array.from(root.querySelectorAll('.item, .option, [data-value]'));
    const capped = all.slice(0, 200);
    const mapped = capped.map(function(n) {
        return {
            text: (n.textContent || '').trim(),
            value: n.getAttribute('data-value') || n.getAttribute('value') || (n.textContent || '').trim(),
            selected: n.classList.contains('selected') || n.classList.contains('active'),
        };
    });
    if (all.length > 200) {
        mapped.push({text: '... (showing 200 of ' + all.length + ', use scroll/search_page for more)', value: '', selected: false});
    }
    return mapped;
}
"""

# Combobox options reader: uses getElementById(aria-controls/aria-owns) to locate
# the standalone listbox (React Portal-friendly — the listbox often renders outside
# the combobox's subtree, so querySelectorAll from the combobox would miss it).
# Returns {options, listboxFound, error}. Options hard-capped to 200 (D4).
_COMBOBOX_OPTIONS_JS = """
function() {
    const combo = this;
    const controlsId = combo.getAttribute('aria-controls') || combo.getAttribute('aria-owns');
    if (!controlsId) return {options: [], listboxFound: false, error: 'no aria-controls/aria-owns'};
    const listbox = document.getElementById(controlsId);
    if (!listbox) return {options: [], listboxFound: false, error: 'listbox not found'};
    const all = Array.from(listbox.querySelectorAll('[role="option"], li'));
    const capped = all.slice(0, 200);
    const mapped = capped.map(function(n) {
        return {
            text: (n.textContent || '').trim(),
            value: n.getAttribute('data-value') || n.getAttribute('value') || (n.textContent || '').trim(),
            selected: n.getAttribute('aria-selected') === 'true' || n.classList.contains('selected'),
        };
    });
    if (all.length > 200) {
        mapped.push({text: '... (showing 200 of ' + all.length + ', use scroll/search_page for more)', value: '', selected: false});
    }
    return {options: mapped, listboxFound: true};
}
"""

# Subtree search: when the target itself matches no known dropdown type, BFS up
# to maxDepth levels for a dropdown-shaped descendant and read its options in
# place (avoids a second resolveNode round-trip). The start node itself is NOT
# re-classified (depth 0 is skipped) — the dispatcher already tried aria/custom
# on it; only descendants are probed. Returns {options, source} where source is
# "child-depth-N" on hit, or {options: [], source: null} on miss.
_SUBTREE_SEARCH_JS = """
function(maxDepth) {
    const start = this;
    function readAria(el) {
        const ns = Array.from(el.querySelectorAll('[role="menuitem"],[role="option"]')).slice(0, 200);
        return ns.map(function(n) {
            return {text: (n.textContent||'').trim(), value: n.getAttribute('data-value')||n.getAttribute('value')||(n.textContent||'').trim(), selected: n.getAttribute('aria-selected')==='true'||n.classList.contains('selected')};
        });
    }
    function readCustom(el) {
        const ns = Array.from(el.querySelectorAll('.item, .option, [data-value]')).slice(0, 200);
        return ns.map(function(n) {
            return {text: (n.textContent||'').trim(), value: n.getAttribute('data-value')||n.getAttribute('value')||(n.textContent||'').trim(), selected: n.classList.contains('selected')||n.classList.contains('active')};
        });
    }
    function classify(el) {
        if (el.querySelector('[role="option"],[role="menuitem"]')) return 'aria';
        if ((el.classList.contains('dropdown') || el.classList.contains('ui')) && el.querySelector('.item,.option,[data-value]')) return 'custom';
        return null;
    }
    var queue = [[start, 0]];
    while (queue.length) {
        var pair = queue.shift(); var el = pair[0]; var d = pair[1];
        if (d > 0) {
            var t = classify(el);
            if (t === 'aria') return {options: readAria(el), source: 'child-depth-' + d};
            if (t === 'custom') return {options: readCustom(el), source: 'child-depth-' + d};
        }
        if (d < maxDepth) {
            for (var i = 0; i < el.children.length; i++) queue.push([el.children[i], d + 1]);
        }
    }
    return {options: [], source: null};
}
"""

# ARIA menu/listbox 写脚本（写侧对应 _ARIA_OPTIONS_JS）。匹配段镜像 reader（text/value
# 大小写不敏感精确）；写段：单选清兄弟 aria-selected/selected/active → 选目标 → 真实
# item.click() + MouseEvent → 读回验证（div 无 element.value，改查 aria-selected/classList
# 「是否粘住」）。返回与 _SELECT_OPTION_JS 同形 dict（省略 selectionReverted —— D2）。
_SET_ARIA_JS = """
function(targetText) {
    const root = this;
    const role = root.getAttribute('role');
    const isAriaContainer = ['listbox', 'menu', 'menubar', 'tree', 'grid'].indexOf(role) !== -1;
    const hasAriaOptions = !!root.querySelector('[role="option"],[role="menuitem"]');
    if (!isAriaContainer && !hasAriaOptions) {
        return { success: false, error: 'Element is not an ARIA listbox/menu' };
    }
    const all = Array.from(root.querySelectorAll('[role="menuitem"],[role="option"]'));
    const availableOptions = all.map(function(n) {
        return { text: (n.textContent || '').trim(), value: n.getAttribute('data-value') || n.getAttribute('value') || (n.textContent || '').trim() };
    });
    const targetLower = (targetText || '').toLowerCase();
    for (const item of all) {
        const textLower = (item.textContent || '').trim().toLowerCase();
        const valLower = (item.getAttribute('data-value') || item.getAttribute('value') || '').toLowerCase();
        if (textLower === targetLower || valLower === targetLower) {
            root.dispatchEvent(new Event('focus', { bubbles: true, cancelable: true }));
            all.forEach(function(o) {
                o.setAttribute('aria-selected', 'false');
                o.classList.remove('selected');
                o.classList.remove('active');
            });
            item.setAttribute('aria-selected', 'true');
            item.classList.add('selected');
            item.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
            item.click();
            item.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
            root.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
            const stuck = item.getAttribute('aria-selected') === 'true' || item.classList.contains('selected') || item.classList.contains('active');
            const chosenValue = item.getAttribute('data-value') || item.getAttribute('value') || (item.textContent || '').trim();
            if (!stuck) {
                return {
                    success: false,
                    error: 'Selection was set but not retained. The dropdown may require a different interaction.',
                    targetOption: { text: (item.textContent || '').trim(), value: chosenValue },
                    availableOptions: availableOptions,
                };
            }
            return {
                success: true,
                message: `Selected option: ${(item.textContent || '').trim()} (value: ${chosenValue})`,
                value: chosenValue,
            };
        }
    }
    return {
        success: false,
        error: `Option with text or value '${targetText}' not found in ARIA listbox/menu`,
        availableOptions: availableOptions,
    };
}
"""

# custom-class 下拉写脚本（写侧对应 _CUSTOM_CLASS_OPTIONS_JS，Semantic UI / Foundation）。
# 非 custom-shaped（无 dropdown/ui class）返回 error；命中后 toggle selected/active +
# 真实 click + change + 读回验证（classList「是否粘住」）。返回与 _SELECT_OPTION_JS 同形。
_SET_CUSTOM_JS = """
function(targetText) {
    const root = this;
    if (!(root.classList.contains('dropdown') || root.classList.contains('ui'))) {
        return { success: false, error: 'Element is not a custom-class dropdown' };
    }
    const all = Array.from(root.querySelectorAll('.item, .option, [data-value]'));
    const availableOptions = all.map(function(n) {
        return { text: (n.textContent || '').trim(), value: n.getAttribute('data-value') || n.getAttribute('value') || (n.textContent || '').trim() };
    });
    const targetLower = (targetText || '').toLowerCase();
    for (const item of all) {
        const textLower = (item.textContent || '').trim().toLowerCase();
        const valLower = (item.getAttribute('data-value') || item.getAttribute('value') || '').toLowerCase();
        if (textLower === targetLower || valLower === targetLower) {
            root.dispatchEvent(new Event('focus', { bubbles: true, cancelable: true }));
            all.forEach(function(o) { o.classList.remove('selected'); o.classList.remove('active'); });
            item.classList.add('selected');
            item.classList.add('active');
            item.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
            item.click();
            item.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
            root.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
            const stuck = item.classList.contains('selected') || item.classList.contains('active');
            const chosenValue = item.getAttribute('data-value') || item.getAttribute('value') || (item.textContent || '').trim();
            if (!stuck) {
                return {
                    success: false,
                    error: 'Selection was set but not retained. The dropdown may require a different interaction.',
                    targetOption: { text: (item.textContent || '').trim(), value: chosenValue },
                    availableOptions: availableOptions,
                };
            }
            return {
                success: true,
                message: `Selected option: ${(item.textContent || '').trim()} (value: ${chosenValue})`,
                value: chosenValue,
            };
        }
    }
    return {
        success: false,
        error: `Option with text or value '${targetText}' not found in custom dropdown`,
        availableOptions: availableOptions,
    };
}
"""

# combobox listbox 定位 JS：解析 aria-controls/aria-owns 的 listbox 为 RemoteObject
# （returnByValue=False 由 caller 设）。命中返回节点本身（CDP 序列化为 RemoteObject，带
# objectId），null 则 result 无 objectId。caller 据此判 listboxFound 并取 objectId 跑 setter。
_COMBOBOX_LISTBOX_ID_JS = """
function() {
    const combo = this;
    const controlsId = combo.getAttribute('aria-controls') || combo.getAttribute('aria-owns');
    if (!controlsId) return null;
    return document.getElementById(controlsId);
}
"""

# combobox listbox 写脚本（写侧对应 _COMBOBOX_OPTIONS_JS）。跑在 listbox 对象上（由 caller
# 经 _COMBOBOX_LISTBOX_ID_JS 解析后传入 objectId）。单选清兄弟 aria-selected → 选目标 →
# 真实 click → 读回验证（aria-selected「是否粘住」）。返回与 _SELECT_OPTION_JS 同形。
_SET_COMBOBOX_OPTION_JS = """
function(targetText) {
    const listbox = this;
    const all = Array.from(listbox.querySelectorAll('[role="option"], li'));
    const availableOptions = all.map(function(n) {
        return { text: (n.textContent || '').trim(), value: n.getAttribute('data-value') || n.getAttribute('value') || (n.textContent || '').trim() };
    });
    if (!all.length) {
        return { success: false, error: 'combobox listbox has no [role=option] or li', availableOptions: [] };
    }
    const targetLower = (targetText || '').toLowerCase();
    for (const item of all) {
        const textLower = (item.textContent || '').trim().toLowerCase();
        const valLower = (item.getAttribute('data-value') || item.getAttribute('value') || '').toLowerCase();
        if (textLower === targetLower || valLower === targetLower) {
            all.forEach(function(o) { o.setAttribute('aria-selected', 'false'); });
            item.setAttribute('aria-selected', 'true');
            item.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
            item.click();
            item.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
            const stuck = item.getAttribute('aria-selected') === 'true';
            const chosenValue = item.getAttribute('data-value') || item.getAttribute('value') || (item.textContent || '').trim();
            if (!stuck) {
                return {
                    success: false,
                    error: 'Combobox selection was set but not retained.',
                    targetOption: { text: (item.textContent || '').trim(), value: chosenValue },
                    availableOptions: availableOptions,
                };
            }
            return {
                success: true,
                message: `Selected option: ${(item.textContent || '').trim()} (value: ${chosenValue})`,
                value: chosenValue,
            };
        }
    }
    return {
        success: false,
        error: `Option with text or value '${targetText}' not found in combobox listbox`,
        availableOptions: availableOptions,
    };
}
"""

# 子树定位 JS（写侧对应 _SUBTREE_SEARCH_JS）。同 BFS+classify，但返回首个命中后代
# 的节点 + 类型（returnByValue=False 由 caller 设，节点以 RemoteObject 存活），供 Python
# 写 flow 按类型 callFunctionOn 对应 setter。depth 0（start 自身）跳过。
_SUBTREE_LOCATE_JS = """
function(maxDepth) {
    const start = this;
    function classify(el) {
        if (el.querySelector('[role="option"],[role="menuitem"]')) return 'aria';
        if ((el.classList.contains('dropdown') || el.classList.contains('ui')) && el.querySelector('.item,.option,[data-value]')) return 'custom';
        return null;
    }
    var queue = [[start, 0]];
    while (queue.length) {
        var pair = queue.shift(); var el = pair[0]; var d = pair[1];
        if (d > 0) {
            var t = classify(el);
            if (t) return { found: true, type: t, node: el, depth: d };
        }
        if (d < maxDepth) {
            for (var i = 0; i < el.children.length; i++) queue.push([el.children[i], d + 1]);
        }
    }
    return { found: false, type: null, node: null };
}
"""

# ── 自定义下拉（非原生 / 非 ARIA-classified）open→discover→read/write ────────
# issue #160：B 站（无 role 的 <li>/<article>/<div title>）、抖音 Semi UI（role=option 但
# list teleport 到 .semi-portal、无 aria-controls）这类下拉，闭态判型器全 miss。这组 JS
# 是「开后发现 list → 按 role/text 读或选」flow 的零件，镜像 combobox flow 的形状。

# 真正能展开的点击目标：从触发器父节点向上爬，取 4 层内「最外层」select-ish 祖先（regex
# select|dropdown|combobox|picker 或 role=combobox 或 data-component），否则返回 this。
# 取最外层是为了 B 站创作声明命中 .bcc-select（真正展开者）而非 .bcc-select-input-wrap
# （点 inner input 不展开）。returnByValue=False 由 caller 设，节点以 RemoteObject 存活。
_EFFECTIVE_CLICK_TARGET_JS = """
function() {
    let node = this.parentElement;
    let best = null;
    for (let i = 0; i < 4 && node; i++, node = node.parentElement) {
        const cls = (typeof node.className === 'string') ? node.className : '';
        const role = node.getAttribute ? node.getAttribute('role') : null;
        if (/(select|dropdown|combobox|picker)/i.test(cls) || role === 'combobox'
            || (node.dataset && node.dataset.component)) {
            best = node;
        }
    }
    return best || this;
}
"""

# 发现 list 节点（跑在触发器上）。三档优先级，option-like 选择器统一为
# [role=option],[role=menuitem],li,[data-value],.item,.option,[title]：
#   1. aria-controls/owns → getElementById（真 combobox 保险）
#   2. 祖先作用域子树搜：trigger.parentElement → 最近 select-ish wrapper → wrapper.parentElement，
#      各作用域找 [role=listbox/menu] 或「≥2 个 option-like 子节点」的容器
#      （覆盖 in-subtree 如 B 站分区、组件根兄弟分支如 B 站创作声明 .bcc-select）
#   3. 全局可见 option-list 就近：document 里可见的 list，按到触发器 rect 的边距距离取最近
#      （覆盖 portal-to-body 如抖音 .semi-portal；isOptionList 过滤已隐藏的残留 portal）
# 返回 list 节点（RemoteObject 存活）或 null（无 objectId）。returnByValue=False 由 caller 设。
_CUSTOM_LISTBOX_DISCOVER_JS = """
function() {
    const trigger = this;
    const OPTION_SEL = '[role="option"],[role="menuitem"],li,[data-value],.item,.option,[title]';
    const ROLE_CONT = '[role="listbox"],[role="menu"],[role="menubar"],[role="tree"],[role="grid"]';
    const GEN_CONT = ROLE_CONT + ',ul,[class*="list"],[class*="option"],[class*="menu"],[class*="portal"],[class*="popover"],[class*="dropdown"]';

    function visible(el) {
        const cs = getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 || r.height > 0;
    }
    function isOptionList(el) {
        if (!el || !visible(el)) return false;
        return el.querySelectorAll(OPTION_SEL).length >= 2;
    }
    function searchScope(root) {
        if (!root) return null;
        const roleFirst = Array.from(root.querySelectorAll(ROLE_CONT)).filter(isOptionList);
        if (roleFirst.length) return roleFirst[0];
        if (root !== trigger && isOptionList(root)) return root;
        const generic = Array.from(root.querySelectorAll(GEN_CONT)).filter(isOptionList);
        return generic.length ? generic[0] : null;
    }

    const cid = trigger.getAttribute('aria-controls') || trigger.getAttribute('aria-owns');
    if (cid) { const lb = document.getElementById(cid); if (lb && isOptionList(lb)) return lb; }

    const scopes = [];
    if (trigger.parentElement) scopes.push(trigger.parentElement);
    const wrapper = trigger.parentElement
        ? trigger.parentElement.closest('[class*="select"],[class*="dropdown"],[class*="combobox"],[class*="picker"],[data-component]')
        : null;
    if (wrapper && scopes.indexOf(wrapper) === -1) scopes.push(wrapper);
    if (wrapper && wrapper.parentElement && scopes.indexOf(wrapper.parentElement) === -1)
        scopes.push(wrapper.parentElement);
    for (const sc of scopes) { const f = searchScope(sc); if (f) return f; }

    const trect = trigger.getBoundingClientRect();
    function edgeDist(r) {
        const dx = Math.max(trect.left - r.right, r.left - trect.right, 0);
        const dy = Math.max(trect.top - r.bottom, r.top - trect.bottom, 0);
        return Math.hypot(dx, dy);
    }
    function pickFrom(sel) {
        let best = null, bestD = Infinity;
        const cands = document.querySelectorAll(sel);
        for (let i = 0; i < cands.length; i++) {
            const c = cands[i];
            if (!isOptionList(c)) continue;
            const d = edgeDist(c.getBoundingClientRect());
            if (d < bestD) { bestD = d; best = c; }
        }
        return best;
    }
    return pickFrom(ROLE_CONT) || pickFrom(GEN_CONT);
}
"""

# 自定义下拉开态读脚本（跑在发现的 list 节点上）。有 [role=option]/[role=menuitem] 用之
# （抖音），否则用通用选择器（B 站 <div title>/<article>/<li>）。返回 [{text,value,selected}]，
# 200 上限 + 截断行，与 _ARIA_OPTIONS_JS 同形（直接喂 _format_options_result）。
_CUSTOM_OPEN_OPTIONS_JS = """
function() {
    const root = this;
    const GEN_SEL = '[role="option"],[role="menuitem"],li,[data-value],.item,.option,[title]';
    function readOne(n) {
        const text = (n.textContent || '').replace(/\\s+/g, ' ').trim();
        const value = n.getAttribute('data-value') || n.getAttribute('value')
                      || n.getAttribute('title') || text;
        const selected = n.getAttribute('aria-selected') === 'true'
                         || n.classList.contains('selected')
                         || n.classList.contains('active')
                         || n.classList.contains('checked');
        return { text: text, value: value, selected: selected };
    }
    const roleOpts = root.querySelectorAll('[role="option"],[role="menuitem"]');
    const pool = roleOpts.length ? roleOpts : root.querySelectorAll(GEN_SEL);
    const all = Array.from(pool);
    const total = all.length;
    const capped = all.slice(0, 200);
    const mapped = capped.map(readOne);
    if (total > 200) {
        mapped.push({ text: '... (showing 200 of ' + total + ', use scroll/search_page for more)', value: '', selected: false });
    }
    return mapped;
}
"""

# 自定义下拉：在发现的 list 节点上找匹配 option（精确→包含），返回 option 节点
# （returnByValue=False → RemoteObject，caller 解析 backendNodeId 后用 click_element 真实点击）。
# 真实 CDP click 比 JS 合成 click 可靠——Semi UI 等框架的 React 选中/收起 handler 只认真实点击事件。
_CUSTOM_FIND_OPTION_JS = """
function(targetText) {
    const root = this;
    const GEN_SEL = '[role="option"],[role="menuitem"],li,[data-value],.item,.option,[title]';
    function textOf(n) { return (n.textContent || '').replace(/\\s+/g, ' ').trim(); }
    function valueOf(n) {
        return n.getAttribute('data-value') || n.getAttribute('value') || n.getAttribute('title') || '';
    }
    const roleOpts = root.querySelectorAll('[role="option"],[role="menuitem"]');
    const all = roleOpts.length ? roleOpts : root.querySelectorAll(GEN_SEL);
    const tl = (targetText || '').toLowerCase();
    for (const n of all) {
        if (textOf(n).toLowerCase() === tl || valueOf(n).toLowerCase() === tl) return n;
    }
    if (tl.length >= 2) {
        for (const n of all) {
            if (textOf(n).toLowerCase().indexOf(tl) !== -1) return n;
        }
    }
    return null;
}
"""

# 虚拟化滚动（跑在发现的 list 节点上）：scrollTop += clientHeight，返回是否真的滚了。
# 用于关联热点等虚拟列表的 scroll-until-found。
_SCROLL_LISTBOX_JS = """
function() {
    const el = this;
    if (el.scrollHeight <= el.clientHeight + 2) return false;
    const before = el.scrollTop;
    el.scrollTop = before + el.clientHeight;
    if (el.scrollTop === before) return false;
    el.dispatchEvent(new Event('scroll', { bubbles: true }));
    return true;
}
"""

# 虚拟化 scroll-until-found 的最大翻页数（10 页 × ~0.12s ≈ 1.2s 预算）
_CUSTOM_SCROLL_CAP = 10


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
        # P1b：最近浏览器事件（首期仅 dialog）。CDP 回调在 websocket 读线程触发，
        # deque + 锁保证线程安全；maxlen=20 自动丢弃溢出。get_state 每步 consume。
        self._recent_events: deque[BrowserEvent] = deque(maxlen=20)
        self._recent_events_lock = threading.Lock()
        self._enable_recent_events: bool = False
        # P7 form_interaction 建议3（493 挂死样本）：JS dialog 自动处理。挂起的
        # alert/confirm/prompt/beforeunload 会阻塞页面 JS——Runtime.evaluate 冻结，
        # agent 循环随之挂死到任务超时。_connect 注册 javascriptDialogOpening 回调，
        # 事件到来即在事件循环上发 Page.handleJavaScriptDialog（beforeunload=accept
        # 放行导航，其余 dismiss），并把处理动作记入 recent_events 告知 LLM。
        self._auto_dialog_enabled: bool = _settings.auto_handle_js_dialog
        self._loop: asyncio.AbstractEventLoop | None = None
        self._dialog_tasks: set[asyncio.Task] = set()
        # 阶段3：networkidle 追踪（always-on；wait 由 get_state(wait_networkidle=...) 显式触发）。
        # tracker 维护 inflight 请求集合；Network.enable + 回调注册在 _connect 内完成。
        self._network_idle_tracker = NetworkIdleTracker(
            timeout=_settings.network_idle_timeout,
            stability_window=_settings.network_idle_stability_window,
            poll_interval=_settings.network_idle_poll_interval,
        )
        # 直播视口（P6 后续 A）：run 前 configure_screencast 设 sink，start() 会话
        # 就绪自动 startScreencast，stop() 收尾。帧回调在 CDP WS 读线程触发。
        self._screencast_sink: tuple | None = None  # (on_frame, kwargs)
        self._screencast_on_frame = None             # 当前注册的帧回调
        self._screencast_on: bool = False            # 是否正在推流

    async def start(
        self,
        *,
        track_downloads: bool = False,
        downloads_path: str | None = None,
        enable_recent_events: bool = False,
    ) -> None:
        """Connect to the browser via CDP WebSocket.

        ``downloads_path`` overrides where tracked downloads land; see
        ``_setup_download_tracking``. ``enable_recent_events`` turns on
        ``[Recent Events]`` capture (P1b，首期仅 dialog).
        """
        self.client = CDPClient(self.ws_url)
        await self._connect()
        # 直播视口（P6 后续 A）：run 前若已 configure_screencast，会话就绪即起推流
        # （browser 侧自动、browser 必活时采集——零 race，践行 04 复盘 §三·1）
        if self._screencast_sink is not None:
            _sc_cb, _sc_kw = self._screencast_sink
            try:
                await self.start_screencast(_sc_cb, **_sc_kw)
            except Exception as e:
                logger.warning("screencast 启动失败（降级为无直播）: %s", e)
        if track_downloads:
            await self._setup_download_tracking(downloads_path)
        self._enable_recent_events = enable_recent_events
        # P7 form_interaction 建议3：dialog 回调注册移入 _connect（无条件、单点、
        # 重连覆盖）——这里不再重复注册。enable_recent_events 仅保留给未来其他事件类型。

    async def _connect(self) -> None:
        """Perform CDP connection, target discovery, and session setup.

        若 ``client.start()`` 握手失败，按当前 ws_url 重新发现一次（Chrome 重启后旧
        ws_url 失效，见 ``_rediscover_ws_url``），拿得到不同的新 ws_url 就重建 client
        重试；否则抛原异常。
        """
        # 阶段3：新会话清旧 inflight 残留（inflight 是 per-session 的）。
        self._network_idle_tracker.reset()
        # P7 form_interaction 建议3：记录事件循环，供 ws 读线程的 dialog 回调把
        # 自动处理移交回 loop 线程（call_soon_threadsafe + create_task）。
        self._loop = asyncio.get_running_loop()
        try:
            await self.client.start()
        except Exception as connect_err:
            new_url = await asyncio.to_thread(self._rediscover_ws_url)
            if new_url and new_url != self.ws_url:
                logger.warning(
                    "CDP 握手失败（%s），Chrome 疑似重启；重新发现 ws_url 重试：%s → %s",
                    connect_err, self.ws_url, new_url,
                )
                self.ws_url = new_url
                self.client = CDPClient(self.ws_url)
                await self.client.start()
            else:
                raise

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
        # P7 form_interaction 建议3：dialog 回调 always-on（自动处理 + 记录），
        # 不再依赖 enable_recent_events——挂起的 dialog 会冻结整个 agent 循环。
        # 注册幂等（cdp_use 单回调覆盖式），重连/重复调用安全；失败降级为不处理。
        try:
            await self._setup_event_tracking()
        except Exception as e:
            logger.warning("dialog handler registration failed (degrading): %s", e)
        # 阶段3：启用 Network 域 + 注册空闲追踪回调（always-on；wait 由 get_state 显式触发）。
        # 失败 → tracker 降级 disabled，wait_until_idle 即时返回（对齐 recent_events 降级）。
        try:
            await self.client.send.Network.enable({}, session_id=self.current_session_id)
            self._network_idle_tracker.register(self.client, self.current_session_id)
        except Exception as e:
            logger.warning("Network.enable / tracker register failed (degrading): %s", e)

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

    def _rediscover_ws_url(self) -> str | None:
        """从当前 ws_url 解析 host:port，重新 GET ``/json/version`` 拿最新 ws_url。

        Chrome 远程调试的 ws_url = ``ws://host:port/devtools/browser/<UUID>``，UUID 是
        进程级的——Chrome 一重启就变，旧 ws_url 握手会 HTTP 404（后端启动时缓存的
        ws_url，若点开始录制前 Chrome 已重启即触发）。这里按 host:port 重新发现当前
        ws_url 供 ``_connect`` 自愈。ws_url 解析不出 host/port（非标准形态）或请求失败
        时返回 None，由调用方按原连接异常抛出。
        """
        try:
            from urllib.parse import urlparse

            from tree_walker.config import _fetch_ws_url

            parsed = urlparse(self.ws_url)
            host, port = parsed.hostname, parsed.port
            if not host or not port:
                return None
            return _fetch_ws_url(host, port)
        except Exception as e:
            logger.debug("重新发现 ws_url 失败: %s", e)
            return None

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

    async def _setup_download_tracking(self, downloads_path: str | None = None) -> None:
        """Enable CDP download events and register callbacks.

        CDP ``Browser.setDownloadBehavior`` with ``behavior="allow"`` requires a
        ``downloadPath`` — the ``default`` behavior emits no download events, so
        it cannot be used for tracking. The path is resolved as: explicit
        ``downloads_path`` arg > ``DOWNLOADS_PATH`` env > the user's OS
        ``Downloads`` dir (Chrome's default download location). The dir is created
        if missing; a creation failure is logged but does not abort startup.
        """
        download_path = (
            downloads_path
            or os.environ.get("DOWNLOADS_PATH")
            or os.path.join(os.path.expanduser("~"), "Downloads")
        )
        try:
            os.makedirs(download_path, exist_ok=True)
        except OSError as e:
            logger.warning("Could not create download dir %s: %s", download_path, e)

        await self.client.send.Browser.setDownloadBehavior(
            {"behavior": "allow", "eventsEnabled": True, "downloadPath": download_path},
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

    # ── P1b：recent_events（最近浏览器事件，首期仅 dialog）──────────────

    async def _setup_event_tracking(self) -> None:
        """注册 CDP 事件回调，把浏览器事件灌入 ``_recent_events`` 队列。

        首期只接 ``Page.javascriptDialogOpening``（alert/confirm/prompt/beforeunload）。
        download 由 ``_setup_download_tracking`` → ``[Downloads]`` 覆盖；cdp_use 单回调
        机制（``registry._handlers[method] = callback`` 覆盖式）下不能双注册
        ``Browser.downloadWillBegin``，故这里不监听 download。

        P7 form_interaction 建议3：本方法由 ``_connect`` **无条件**调用（重连也覆盖）——
        dialog 回调除了记录事件，还负责自动处理：挂起的 JS dialog 会阻塞页面 JS
        （Runtime.evaluate 冻结，agent 循环挂死，WebArena task 493 样本）。事件本体
        **无条件记录**（自动处理必须让 LLM 看见）；``enable_recent_events`` 只保留给
        未来的其他事件类型。回调在 websocket 读线程触发 → ``record_event`` 用锁保证
        线程安全；自动处理经 ``call_soon_threadsafe`` 移交事件循环线程。
        """
        def _on_javascript_dialog(event: dict, session_id: str | None = None) -> None:
            # event: {url, message, type: alert/confirm/prompt/beforeunload}
            message = event.get("message", "") or event.get("url", "")
            dialog_type = event.get("type", "alert")
            accept = dialog_type == "beforeunload"
            action = "auto-accepted" if accept else "auto-dismissed"
            # 记录事件（无条件——自动处理对 LLM 必须可见）
            prefix = f"[{dialog_type}] {message}" if message else f"[{dialog_type}]"
            self.record_event(BrowserEvent(
                type="dialog",
                message=f"{prefix} ({action})",
                timestamp=time.time(),
            ))
            # 调度自动处理（ws 读线程 → 事件循环线程）
            loop = self._loop
            if not self._auto_dialog_enabled or loop is None:
                return

            def _spawn() -> None:
                task = loop.create_task(self._auto_handle_dialog(accept, session_id))
                self._dialog_tasks.add(task)
                task.add_done_callback(self._dialog_tasks.discard)

            try:
                loop.call_soon_threadsafe(_spawn)
            except RuntimeError:
                logger.debug("dialog auto-handle scheduling skipped (loop closed)")

        self.client.register.Page.javascriptDialogOpening(_on_javascript_dialog)
        logger.info(
            "recent_events tracking enabled (dialog; auto_handle=%s)", self._auto_dialog_enabled,
        )

    async def _auto_handle_dialog(self, accept: bool, session_id: str | None) -> None:
        """自动处理挂起的 JS dialog（beforeunload→accept 放行导航，其余→dismiss）。

        dismiss 策略最安全：confirm 型弹窗被取消而非确认（不会替用户点头危险操作）；
        alert 型 accept/dismiss 等价。失败仅 debug 记录（dialog 可能已被页面侧关闭）。
        """
        try:
            await self.client.send.Page.handleJavaScriptDialog(
                {"accept": accept, "promptText": ""},
                session_id=session_id or self.current_session_id,
            )
            logger.info("JS dialog auto-handled (accept=%s)", accept)
        except Exception as e:
            logger.debug("handleJavaScriptDialog failed (already closed?): %s", e)

    def record_event(self, event: BrowserEvent) -> None:
        """线程安全地追加一个浏览器事件（CDP 回调线程调用）。deque maxlen 自动溢出。"""
        with self._recent_events_lock:
            self._recent_events.append(event)

    def consume_recent_events(self) -> list[BrowserEvent]:
        """返回并清空近期事件缓冲（get_state 每步调用，避免重复出现）。"""
        with self._recent_events_lock:
            events = list(self._recent_events)
            self._recent_events.clear()
            return events

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
        # 直播视口：先停推流再断 CDP（CDP 会话还活着才能干净 stop；幂等 no-op）
        try:
            if self._screencast_on:
                await self.stop_screencast()
        except Exception as e:
            logger.debug("stop() 内 stop_screencast no-op: %s", e)
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

    async def get_state(
        self, include_screenshot: bool = True, wait_settle: bool = False,
        wait_networkidle: bool = False,
    ) -> BrowserStateSummary:
        """Get full browser state: URL, title, tabs, DOM, optional screenshot.

        wait_settle: poll document.readyState to 'complete' before reading state
        (mirrors screenshot/save_as_pdf). 失败只打 warning，不阻断 get_state。
        wait_networkidle: 阶段3——等 inflight 网络请求归零（+稳定窗口）再读 state。
        默认关；开启时排在 wait_settle 之后（先 readyState 后 AJAX）。失败只 warning。
        """
        if wait_settle:
            try:
                await self._wait_for_page_settle()
            except Exception as e:
                logger.warning("Pre-get_state wait_settle failed: %s", e)
        if wait_networkidle:
            try:
                await self._network_idle_tracker.wait_until_idle()
            except Exception as e:
                logger.warning("Pre-get_state wait_networkidle failed: %s", e)
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
            from dom_snapshot import EMPTY_DOM_STATE
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
                from dom_snapshot import EMPTY_DOM_STATE
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

        # P7 tool_layer B2：网格元信息（UI 网格的 total/sorting/活动过滤）——
        # 读的是组件数据层，行渲染冻结不影响；非网格页按 URL 缓存跳过（零重复成本）。
        grid_meta = await self._read_grid_meta(url)

        return BrowserStateSummary(
            url=url,
            title=title,
            tabs=tabs,
            dom_state=dom_state,
            screenshot=screenshot,
            grid_meta=grid_meta,  # P7 tool_layer B2
            recent_events=self.consume_recent_events(),  # P1b：每步 consume，避免重复
        )

    # ── 直播视口（CDP Page.startScreencast 连续推流，P6 后续 A）──────────

    def configure_screencast(self, on_frame, **kwargs) -> None:
        """声明「run 前要直播」——存回调 + 参数，真正 ``startScreencast`` 延到
        ``start()`` 会话就绪（browser 侧自动起，零 race）。

        ``on_frame`` 签名 ``(event, cdp_session_id)``，在 CDP WS 读线程触发（见
        ``_setup_event_tracking`` 注释），实现方须用 ``loop.call_soon_threadsafe``
        移交 loop 线程后再操作 asyncio 对象。``kwargs`` 透传给 ``start_screencast``。
        """
        self._screencast_sink = (on_frame, kwargs)

    async def start_screencast(
        self,
        on_frame,
        *,
        format: str = "jpeg",
        quality: int = 60,
        max_width: int | None = None,
        every_nth_frame: int = 4,
    ) -> None:
        """启动 CDP ``Page.startScreencast`` 连续推流（直播视口）。

        仿 ``_setup_download_tracking``（register + send）与 ``take_screenshot``
        （``send.Page.*`` + ``current_session_id``）。``on_frame`` 每帧回调，载荷
        ``{data(base64), metadata, sessionId}``（cdp_use ``events.py:300-305``）；回调
        在 CDP WS 读线程触发，调用方须线程安全移交 loop。幂等：重复调用先 stop 再 start。
        ``max_width`` 只限宽以保宽高比（高亮层归一化 bbox 百分比依赖）。
        """
        if self.client is None:
            raise RuntimeError("screencast 需先 start() 连接 CDP")
        if self._screencast_on:
            await self.stop_screencast()
        self._screencast_on_frame = on_frame
        self.client.register.Page.screencastFrame(on_frame)  # 单回调覆盖式注册
        params: dict = {"format": format, "quality": int(quality)}
        if max_width is not None:
            params["maxWidth"] = int(max_width)
        params["everyNthFrame"] = int(every_nth_frame)
        await self.client.send.Page.startScreencast(
            params, session_id=self.current_session_id,
        )
        self._screencast_on = True
        logger.info(
            "screencast: startScreencast 成功（session=%s, format=%s, everyNthFrame=%s, maxWidth=%s）",
            self.current_session_id, format, every_nth_frame, max_width,
        )

    async def stop_screencast(self) -> None:
        """停止推流。幂等：未起推流 / client 已断 → no-op（不发 CDP）。"""
        if not self._screencast_on:
            return
        self._screencast_on = False
        client = self.client
        if client is None:
            return
        try:
            await client.send.Page.stopScreencast(None, session_id=self.current_session_id)
        except Exception as e:
            logger.debug("stopScreencast failed (ignored): %s", e)

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

    async def print_to_pdf(
        self,
        paper_format: str = "letter",
        landscape: bool = False,
        print_background: bool = True,
        scale: float = 1.0,
        wait_settle: bool = False,
    ) -> bytes:
        """Render the current page to PDF bytes via CDP Page.printToPDF.

        Args:
            paper_format: 'letter' | 'legal' | 'a4' | 'a3' | 'tabloid'.
            landscape: landscape orientation.
            print_background: include background graphics/colors.
            scale: render scale (0.1-2.0).
            wait_settle: poll document.readyState to 'complete' before printing.

        Raises:
            RuntimeError: if CDP returns no 'data' field.
        """
        paper_sizes = {  # 英寸 (width, height)
            "letter": (8.5, 11.0),
            "legal": (8.5, 14.0),
            "a4": (8.27, 11.69),
            "a3": (11.69, 16.54),
            "tabloid": (11.0, 17.0),
        }
        paper_width, paper_height = paper_sizes.get(paper_format.lower(), (8.5, 11.0))

        if wait_settle:
            try:
                await self._wait_for_page_settle()
            except Exception as e:
                logger.warning("Pre-pdf wait_settle failed: %s", e)

        params: dict = {
            "printBackground": print_background,
            "landscape": landscape,
            "scale": scale,
            "paperWidth": paper_width,
            "paperHeight": paper_height,
            "preferCSSPageSize": True,
        }
        try:
            result = await self.client.send.Page.printToPDF(
                params,
                session_id=self.current_session_id,
            )
        except Exception as e:
            logger.warning("Page.printToPDF failed: %s", e)
            raise

        if not isinstance(result, dict) or "data" not in result:
            raise RuntimeError("printToPDF failed - no data returned")
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

    # B3-1（P7 02 批次三）：页面级 settle 探测 JS——readyState + requirejs 模块数。
    # JS 无反斜杠（避开 evaluate 转义链路）。无 requirejs 的页面直接就绪（零等待）。
    _PAGE_SETTLE_JS = """
(function(){
	if (document.readyState !== 'complete') { return JSON.stringify({ready: false, stage: 'readyState', n: 0}); }
	try {
		var ctx = (window.require && require.s && require.s.contexts) ? require.s.contexts._ : null;
		if (!ctx) { return JSON.stringify({ready: true, stage: 'no-requirejs', n: 0}); }
		var n = Object.keys(ctx.defined || {}).length;
		return JSON.stringify({ready: false, stage: 'requirejs', n: n});
	} catch (e) { return JSON.stringify({ready: true, stage: 'error', n: 0}); }
})()"""

    async def wait_for_page_settle(
        self,
        timeout: float = 10.0,
        poll: float = 0.5,
        stable_polls: int = 4,
    ) -> dict:
        """等页面 JS 就绪：requirejs 模块数连续 ``stable_polls`` 次 poll 不变，或无 requirejs。

        B3-1（P7 02 批次三）：元素级 actionability 检查不了部件初始化（不动元素几何），
        requirejs 页面的部件武装要 ~6-14s；窗口内输入的值会被迟到部件清掉
        （P12 输入侧失败）。降级原则同 actionability：超时/异常都安全返回，不阻断导航。

        P7 form_interaction 建议1（docs/p7/form_interaction/01-failure-analysis.md 发现1）：
        settle 结束后调用 ``_kick_frozen_data_grid``——窗口不可见（最小化/完全遮挡）时
        合成器不产帧，KO 数据网格行保持未渲染模板（记录数可见、行文本全空），一次丢弃式
        截图强制产帧即可解锁（7 正例的"screenshot kick"）。诊断键 ``grid_kick`` 并入返回值。

        Returns:
            诊断 dict：{ready, stage, n, waited}（timeout 到点时 ready=False 仍放行），
            可能附 {grid_kick, grid_rows, grid_rendered}。
        """
        result = await self._settle_poll(timeout, poll, stable_polls)
        kick = await self._kick_frozen_data_grid()
        if kick:
            result = {**result, **kick}
        return result

    async def _settle_poll(
        self,
        timeout: float = 10.0,
        poll: float = 0.5,
        stable_polls: int = 4,
    ) -> dict:
        """``wait_for_page_settle`` 的轮询主体（拆出以便 settle 后挂 grid kick）。"""
        start = time.monotonic()
        last_n: int | None = None
        stable = 0
        try:
            while True:
                raw = await self.evaluate(self._PAGE_SETTLE_JS)
                try:
                    st = json.loads(raw) if isinstance(raw, str) else {}
                except (TypeError, ValueError):
                    st = {}
                if st.get("ready"):
                    return {**st, "waited": round(time.monotonic() - start, 2)}
                n = int(st.get("n", 0) or 0)
                if n == last_n:
                    stable += 1
                    if stable >= stable_polls:
                        return {"ready": True, "stage": st.get("stage"), "n": n,
                                "waited": round(time.monotonic() - start, 2)}
                else:
                    stable = 0
                last_n = n
                if time.monotonic() - start >= timeout:
                    return {"ready": False, "stage": st.get("stage"), "n": n, "timeout": True,
                            "waited": round(time.monotonic() - start, 2)}
                await asyncio.sleep(poll)
        except Exception as e:
            return {"ready": False, "error": str(e)[:120],
                    "waited": round(time.monotonic() - start, 2)}

    # P7 form_interaction 建议1：KO 数据网格"行渲染冻结"检测。记录数文本可见但
    # tbody 行 innerText/textContent 全空 = 模板未绑定（窗口不产帧时的典型状态）。
    _GRID_EMPTY_ROWS_JS = """
(function(){
    var rows = document.querySelectorAll('.admin__data-grid tbody tr, table.data-grid tbody tr');
    if (!rows.length) { return JSON.stringify({grid: false}); }
    for (var i = 0; i < rows.length; i++) {
        var t = (rows[i].innerText || rows[i].textContent || '').trim();
        if (t) { return JSON.stringify({grid: true, empty: false, rows: rows.length}); }
    }
    return JSON.stringify({grid: true, empty: true, rows: rows.length});
})()"""

    async def _kick_frozen_data_grid(self) -> dict | None:
        """检测数据网格行渲染冻结，并用一次丢弃式截图强制产帧（"screenshot kick"）。

        WebArena form_interaction 批次的实证：窗口最小化/遮挡 → 合成器不产帧 →
        KO 网格行保持空模板；``Page.captureScreenshot`` 强制合成一帧后行随即渲染
        （454/196/493/543/546/551/504 七任务截图即渲染，464/503 未截图永不渲染）。
        无网格/行有文本的页面零开销（一次 evaluate 即返回）。永不 raise。
        """
        try:
            raw = await self.evaluate(self._GRID_EMPTY_ROWS_JS)
            try:
                st = json.loads(raw) if isinstance(raw, str) else {}
            except (TypeError, ValueError):
                return None
            if not st.get("grid") or not st.get("empty"):
                return None
            # kick：整视口低质量 jpeg，字节直接丢弃（只借"强制产一帧"的副作用）
            await self.take_screenshot(format="jpeg", quality=20)
            await asyncio.sleep(0.3)
            raw2 = await self.evaluate(self._GRID_EMPTY_ROWS_JS)
            try:
                st2 = json.loads(raw2) if isinstance(raw2, str) else {}
            except (TypeError, ValueError):
                st2 = {}
            rendered = bool(st2.get("grid") and not st2.get("empty"))
            logger.info(
                "data-grid render kick: rows=%s rendered_after=%s (frozen KO grid, forced one frame)",
                st.get("rows"), rendered,
            )
            return {"grid_kick": True, "grid_rows": st.get("rows"), "grid_rendered": rendered}
        except Exception as e:
            logger.debug("data-grid render kick skipped: %s", e)
            return None

    # ── UI 网格结构化读取 / 元信息（P7 tool_layer B1+B2，2026-08-28）─────────
    #
    # 背景（docs/p7/tool_layer/01-feasibility-and-impl-plan.md）：Magento KO 网格
    # 行渲染冻结 + LLM 手写 JS 语法税 + 行序无保证。实证（探针
    # examples/p7_probe_grid_channels.py）：mui/index/render 在本环境返回脚手架
    # HTML 不可用；可靠通道是 uiRegistry data source（ds.data.items 行数组 +
    # ds.params.{filters,paging,sorting} 活动状态 + ds.data.totalRecords）。
    # JS 无反斜杠（避开 evaluate 转义链路）；args 走 callFunctionOn JSON 编组。

    # B1：结构化读网格。args 模式下代码被包成非异步 function(...a){ BODY }，
    # 故 BODY 必须 return 一个 Promise（IIFE），由 awaitPromise 兜住。
    _GRID_READ_JS = """
return (async function(){
    var p = a[0];
    try {
        if (typeof require !== 'function') { return JSON.stringify({channel_error: 'no-requirejs'}); }
        var reg = await new Promise(function(r){ require(['uiRegistry'], r); });
        var names = await new Promise(function(resolve){
            var acc = [];
            try { reg.get(function(c){ if (c && c.name) { acc.push(c.name); } return false; }); }
            catch (e) {}
            setTimeout(function(){ resolve(acc); }, 300);
        });
        var real = names.filter(function(n){ return n.indexOf('notification_area') !== 0; });
        var ns = p.namespace || null;
        if (!ns) {
            for (var i = 0; i < real.length; i++) {
                var n = real[i];
                if (n.slice(-12) === '_data_source' && n.indexOf('.') === n.lastIndexOf('.')) {
                    ns = n.split('.')[0]; break;
                }
            }
        }
        if (!ns) { return JSON.stringify({channel_error: 'no-grid', candidates: real.slice(0, 10)}); }
        var ds = await new Promise(function(resolve){
            var done = false;
            reg.get(ns + '.' + ns + '_data_source', function(c){ done = true; resolve(c); });
            setTimeout(function(){ if (!done) { resolve(null); } }, 1500);
        });
        if (!ds || !ds.data) { return JSON.stringify({channel_error: 'no-grid', namespace: ns}); }
        function realFilters(f) {
            var out = {};
            f = f || {};
            for (var k in f) {
                if (k !== 'placeholder' && Object.prototype.hasOwnProperty.call(f, k)) { out[k] = f[k]; }
            }
            return out;
        }
        var activeBefore = {
            filters: realFilters(ds.params && ds.params.filters),
            search: (ds.params && ds.params.search) || '',
            sorting: (ds.params && ds.params.sorting)
                ? JSON.parse(JSON.stringify(ds.params.sorting)) : null
        };
        if (p.fresh) {
            ds.set('params.filters', {placeholder: false});
            ds.set('params.search', '');
        }
        if (p.filters) { ds.set('params.filters', Object.assign({placeholder: false}, p.filters)); }
        if (typeof p.search === 'string' && p.search) { ds.set('params.search', p.search); }
        if (p.sorting) { ds.set('params.sorting', p.sorting); }
        if (p.paging) { ds.set('params.paging', p.paging); }
        var reloaded = false;
        try { if (typeof ds.reload === 'function') { ds.reload(); reloaded = true; } } catch (e) {}
        try { ds.set('params.t', Date.now()); reloaded = true; } catch (e) {}
        var stable = !reloaded;
        if (reloaded) {
            var last = '', stableCount = 0, t0 = Date.now();
            while (Date.now() - t0 < (p.waitMs || 8000)) {
                await new Promise(function(r){ setTimeout(r, 250); });
                var d0 = ds.data || {};
                var it0 = (d0.items instanceof Array) ? d0.items : [];
                var cur = [d0.totalRecords || 0, it0.length,
                    it0.length ? JSON.stringify(it0[0].entity_id || it0[0].id || '') : ''].join(':');
                if (cur === last) { stableCount++; if (stableCount >= 2) { stable = true; break; } }
                else { stableCount = 0; last = cur; }
            }
        }
        var d = ds.data || {};
        var items = (d.items instanceof Array) ? d.items : [];
        var rows = items;
        if (p.fields && p.fields.length) {
            rows = items.map(function(it){
                var r2 = {};
                for (var j = 0; j < p.fields.length; j++) {
                    var k = p.fields[j];
                    if (it && Object.prototype.hasOwnProperty.call(it, k)) { r2[k] = it[k]; }
                }
                return r2;
            });
        }
        return JSON.stringify({
            channel: 'uiregistry', namespace: ns, rows: rows,
            total_records: d.totalRecords,
            applied: {
                filters: realFilters(ds.params && ds.params.filters),
                search: (ds.params && ds.params.search) || '',
                sorting: (ds.params && ds.params.sorting) || null,
                paging: (ds.params && ds.params.paging) || null
            },
            active_before: activeBefore, partial: !stable
        });
    } catch (e) { return JSON.stringify({channel_error: 'js-error: ' + e.message}); }
})()
"""

    async def read_ui_grid(self, payload: dict, timeout_ms: int | None = None) -> dict:
        """B1：UI 组件网格（uiRegistry）结构化读取。

        ``payload`` 键见 ``tools.models.ReadGridParams``（namespace/filters/search/
        sorting/paging/fields/fresh，另可带 waitMs 覆盖稳定轮询上限）。
        成功返回 ``{channel, namespace, rows, total_records, applied, active_before,
        partial}``；通道不可用返回 ``{channel_error}``（no-requirejs / no-grid /
        js-error / evaluate-failed / unparseable），由调用方决定回落。
        """
        try:
            raw = await self.evaluate(
                self._GRID_READ_JS, args=[payload], await_promise=True, timeout_ms=timeout_ms,
            )
        except Exception as e:
            return {"channel_error": f"evaluate-failed: {e}"}
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            return parsed if isinstance(parsed, dict) else {"channel_error": "unexpected-result"}
        except (TypeError, ValueError):
            return {"channel_error": f"unparseable: {str(raw)[:120]}"}

    # B2：快照网格元信息（get_state 附带）。只读、不 reload——报告「现在」的
    # total/sorting/活动过滤；排序与首行值一起回，供上层核对声明与实际一致。
    _GRID_META_JS = """
(async function(){
    try {
        var wrap = document.querySelectorAll('.admin__data-grid-wrap, table.data-grid');
        if (wrap.length === 0) { return ''; }
        if (typeof require !== 'function') { return ''; }
        var reg = await new Promise(function(r){ require(['uiRegistry'], r); });
        var names = await new Promise(function(resolve){
            var acc = [];
            try { reg.get(function(c){ if (c && c.name) { acc.push(c.name); } return false; }); }
            catch (e) {}
            setTimeout(function(){ resolve(acc); }, 300);
        });
        var dsName = null;
        for (var i = 0; i < names.length; i++) {
            var n = names[i];
            if (n.indexOf('notification_area') !== 0 && n.slice(-12) === '_data_source'
                && n.indexOf('.') === n.lastIndexOf('.')) { dsName = n; break; }
        }
        if (!dsName) { return ''; }
        var ds = await new Promise(function(resolve){
            var done = false;
            reg.get(dsName, function(c){ done = true; resolve(c); });
            setTimeout(function(){ if (!done) { resolve(null); } }, 1200);
        });
        if (!ds || !ds.data) { return ''; }
        var d = ds.data, prm = ds.params || {};
        var filters = {}, f = prm.filters || {};
        for (var k in f) {
            if (k !== 'placeholder' && Object.prototype.hasOwnProperty.call(f, k)) { filters[k] = f[k]; }
        }
        var sorting = prm.sorting || null;
        var first = (d.items instanceof Array && d.items.length) ? d.items[0] : null;
        var firstVal = null;
        if (first && sorting && sorting.field && Object.prototype.hasOwnProperty.call(first, sorting.field)) {
            firstVal = String(first[sorting.field]).slice(0, 40);
        }
        return JSON.stringify({
            namespace: dsName.split('.')[0],
            rows_loaded: (d.items instanceof Array) ? d.items.length : 0,
            total_records: (typeof d.totalRecords !== 'undefined') ? d.totalRecords : null,
            page: prm.paging ? prm.paging.current : null,
            page_size: prm.paging ? prm.paging.pageSize : null,
            sorting: sorting,
            first_sorted_value: firstVal,
            active_filters: filters,
            active_search: prm.search || ''
        });
    } catch (e) { return ''; }
})()"""

    async def _read_grid_meta(self, url: str) -> dict | None:
        """B2：读当前页网格元信息；非网格页按 URL 缓存（跳过重复探测）。

        失败一律 None（元信息是增强不是依赖）。网格页每步重读——total/过滤随
        动作变化正是要暴露的信息。
        """
        no_grid_urls = getattr(self, "_grid_no_grid_urls", None)
        if no_grid_urls is None:
            no_grid_urls = self._grid_no_grid_urls = set()
        if url in no_grid_urls:
            return None
        try:
            raw = await self.evaluate(self._GRID_META_JS, timeout_ms=8000)
        except Exception as e:
            logger.debug("grid meta read failed: %s", e)
            return None
        text = raw if isinstance(raw, str) else ""
        if not text.strip():
            no_grid_urls.add(url)
            return None
        try:
            meta = json.loads(text)
            return meta if isinstance(meta, dict) else None
        except (TypeError, ValueError):
            return None

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

        Triggers InputEvent('input') (primary for React/Vue v-model), a
        'change' Event, and a deferred Event('input') for Vue reactivity.

        P7 form_interaction 建议2（docs/p7/form_interaction/01-failure-analysis.md
        发现3）：打字路径现在也派发 'change'。Knockout 的 value 绑定默认只监听
        'change'——漏发会让 KO 表单（Magento admin 商品/价格规则表单）Save 时提交
        空值/表单被清空（695/700/702/542 四任务实证）；_force_set_value 与
        _clear_text_field 本就派发 change，打字路径补齐后行为一致。'blur' 仍不派发
        （会触发下拉收起、tag-input 清值等副作用）。

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

                        // change — P7 form_interaction 建议2：KO value 绑定默认听
                        // change；不派发则 KO 表单提交时读到空值。
                        try {
                            el.dispatchEvent(new Event('change', {bubbles: true}));
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

    async def get_page_html(
        self, *, extract_links: bool = True, extract_images: bool = True
    ) -> str:
        """Fetch the current page body as clean HTML via a single CDP ``DOM.getDocument``.

        ``depth=-1, pierce=True`` 的结果天然含 shadow DOM 与同源 iframe 的 ``contentDocument``；
        经 ``html_source.document_body_to_html`` 重建为 markdownify 友好的干净 HTML（剥
        script/style/template/HEAD，门控 ``<a href>`` / ``<img src>``）。失败返回 ``""``，
        调用方（``_action_extract``）降级到 ``execute_js outerHTML``。跨源 iframe 不可达。
        """
        try:
            doc = await self.client.send.DOM.getDocument(
                {"depth": -1, "pierce": True}, session_id=self.current_session_id,
            )
            from tree_walker.browser.html_source import document_body_to_html
            return document_body_to_html(
                (doc or {}).get("root", {}),
                extract_links=extract_links,
                extract_images=extract_images,
            )
        except Exception as e:
            logger.warning("get_page_html: DOM.getDocument failed: %s", e)
            return ""

    async def evaluate(
        self,
        code: str,
        *,
        args: list | None = None,
        elements: list[int] | None = None,
        await_promise: bool = True,
        timeout_ms: int | None = None,
        user_gesture: bool = False,
        return_element_ids: bool = False,
        frame: int | None = None,
    ) -> str:
        """Execute arbitrary user JavaScript and return a normalized result string.

        阶段一: preprocess the code (fix common LLM quoting mistakes), run a single
        ``Runtime.evaluate`` with ``returnByValue=True, awaitPromise=True`` (+ ``timeout``
        per project convention), then normalize the value to an LLM-friendly string. Raises
        ``RuntimeError`` (debugging-rich) on a JS exception (``exceptionDetails``) or the
        legacy ``wasThrown`` flag.

        阶段二:
        - 二.B: ``await_promise`` / ``timeout_ms`` / ``user_gesture`` are forwarded.
          ``timeout_ms`` only applies on the no-inputs ``Runtime.evaluate`` path.
        - 二.C: ``args`` switches to ``Runtime.callFunctionOn`` with the document as host
          (``this = document``) so arguments are CDP-marshaled — no string splicing, no
          injection surface. The code is wrapped as ``function(...a){ ... }`` and MUST
          ``return``. (Project uses session/target isolation, not ``executionContextId``.)
        - 二.D: ``elements`` are resolved via ``DOM.resolveNode`` to RemoteObject handles
          passed after the JSON args (signature ``function(...a, ...e)``).
          ``return_element_ids`` runs the call with ``returnByValue=False`` and, if the
          result is a DOM node, resolves it back to a ``backendNodeId`` via
          ``DOM.describeNode`` (returned as ``"backendNodeId:<id>"``; == the click index).
        - 二.E: ``frame`` attaches to that iframe's target and runs the call in its session.

        Limitations: ``callFunctionOn`` has no ``timeout``, so ``timeout_ms`` is ignored
        when ``args``/``elements`` are given. A returned node not in the current
        selector_map needs a ``get_state`` refresh before it can be clicked.

        Does NOT reuse ``execute_js``: evaluate needs the full ``result`` dict for
        null/undefined distinction, type-aware normalization, and exception enrichment,
        which the shared ``execute_js`` discards.
        """
        validated_code = _validate_and_fix_javascript(code)
        sid = self.current_session_id
        # 二.E: 在（通常跨源）iframe 上下文内执行
        if frame is not None:
            resolve_ifr = await self.client.send.DOM.resolveNode(
                {"backendNodeId": frame}, session_id=self.current_session_id,
            )
            desc_ifr = await self.client.send.DOM.describeNode(
                {"objectId": resolve_ifr["object"]["objectId"]}, session_id=self.current_session_id,
            )
            frame_id = desc_ifr.get("node", {}).get("frameId")
            frame_target_map, _ = await build_frame_target_map(self.client)
            target_id = frame_target_map.get(frame_id) if frame_id else None
            if not target_id:
                raise RuntimeError(
                    f"Evaluate failed: could not resolve iframe target for frame {frame_id!r}",
                )
            attached = await attach_to_iframe_target(self.client, target_id)
            if not attached:
                raise RuntimeError("Evaluate failed: could not attach to iframe target")
            sid = attached
        # 二.D IN: 解析元素句柄（DOM.resolveNode，cf _js_click:1974-1977）
        element_oids: list[str] = []
        for bid in (elements or []):
            r = await self.client.send.DOM.resolveNode({"backendNodeId": bid}, session_id=sid)
            element_oids.append(r["object"]["objectId"])
        use_call_fn = bool(args or elements)
        if use_call_fn:
            # call host = document（this=document）：DOM.getDocument + DOM.resolveNode，
            # 无需 executionContextId / 事件订阅，与 _js_click 同姿势。
            doc = await self.client.send.DOM.getDocument({"depth": 0}, session_id=sid)
            host = await self.client.send.DOM.resolveNode(
                {"nodeId": doc["root"]["nodeId"]}, session_id=sid,
            )
            # JSON args 在前、元素句柄在后，故签名 function(...a, ...e)
            arguments = [{"value": a} for a in (args or [])] + [{"objectId": o} for o in element_oids]
            params_str = "...a, ...e" if element_oids else "...a"
            func_decl = f"function({params_str}){{\n" + validated_code + "\n}"
            result = await self.client.send.Runtime.callFunctionOn(
                {
                    "objectId": host["object"]["objectId"],
                    "functionDeclaration": func_decl,
                    "arguments": arguments,
                    "returnByValue": not return_element_ids,
                    "awaitPromise": await_promise,
                    "userGesture": user_gesture,
                },
                session_id=sid,
            )
        else:
            result = await self.client.send.Runtime.evaluate(
                {
                    "expression": validated_code,
                    "returnByValue": not return_element_ids,
                    "awaitPromise": await_promise,
                    "userGesture": user_gesture,
                    "timeout": timeout_ms if timeout_ms is not None else 30000,
                },
                session_id=sid,
            )
        if result.get("exceptionDetails"):
            exc = result["exceptionDetails"]
            err_text = str(exc.get("text", ""))
            # P7 form_interaction 建议5：已知 SyntaxError 的确定性自愈（仅无输入路径——
            # args/elements 模式代码在函数体内，裸 return 合法，语法错误形态不同）。
            # 候选按序试跑（语法错误无副作用）；全部失败则抛原错误（附截断提示）。
            if not use_call_fn:
                for candidate in _syntax_repair_candidates(validated_code, err_text):
                    retry = await self.client.send.Runtime.evaluate(
                        {
                            "expression": candidate,
                            "returnByValue": not return_element_ids,
                            "awaitPromise": await_promise,
                            "userGesture": user_gesture,
                            "timeout": timeout_ms if timeout_ms is not None else 30000,
                        },
                        session_id=sid,
                    )
                    if (not retry.get("exceptionDetails")
                            and not retry.get("result", {}).get("wasThrown")):
                        logger.info(
                            "evaluate syntax self-heal applied (%s → retry succeeded)",
                            err_text.splitlines()[0][:80],
                        )
                        result = retry
                        break
            if result.get("exceptionDetails"):
                msg = _format_eval_exception(result["exceptionDetails"], validated_code)
                if "Unexpected end of input" in err_text:
                    msg += ("\n⚠️ The code looks truncated — split it into shorter "
                            "evaluate calls.")
                raise RuntimeError(msg)
        result_data = result.get("result", {})
        if result_data.get("wasThrown"):
            raise RuntimeError("JavaScript execution failed (wasThrown=true)")
        # 二.D OUT: 返回的 DOM 节点 → backendNodeId（== 可操作的 index/element_id）
        if (return_element_ids
                and result_data.get("type") == "object"
                and result_data.get("subtype") == "node"
                and "objectId" in result_data):
            desc = await self.client.send.DOM.describeNode(
                {"objectId": result_data["objectId"]}, session_id=sid,
            )
            bid = desc.get("node", {}).get("backendNodeId")
            if bid is not None:
                return f"backendNodeId:{bid}"
        return _normalize_eval_result(result_data)

    async def find_text(
        self,
        text: str,
        *,
        nth: int = 1,
        case_sensitive: bool = False,
        highlight: Literal["box", "selection", "none"] = "box",
    ) -> dict:
        """Find text on the page, scroll the nth visible match into view, highlight it.

        Extends the P0 3-query XPath chain (``DOM.performSearch``) + JS
        TreeWalker fallback with four P1 capabilities (see
        find_text_follow_up.md): (G8) ``nth`` selects which match — stateless,
        re-searches each call, no cross-call session state; (G9) visibility-
        priority filtering (default on, only probes when >1 match, so single-
        match behavior is unchanged from P0); (G10) case-insensitive by
        default via XPath ``translate()``; (G11) ``highlight`` mode.

        Returns a dict. ``found=False`` covers both "text absent"
        (``method="none"``) and "text present but nth exceeds the visible
        count" (``reason="nth_exceeds"``); never raises on these — the action
        layer builds the echo. Unexpected CDP errors propagate to it.
        """
        sid = self.current_session_id
        queries = _text_queries(text, case_sensitive)
        for method, query in queries:
            search_id: str | None = None
            try:
                search = await self.client.send.DOM.performSearch(
                    {"query": query, "includeUserAgentShadowDOM": True},
                    session_id=sid,
                )
                search_id = search.get("searchId")
                total = search.get("resultCount", 0)
                if total <= 0:
                    continue
                # G8: fetch a capped batch (shared with the G9 visibility probe).
                results = await self.client.send.DOM.getSearchResults(
                    {"searchId": search_id, "fromIndex": 0, "toIndex": min(total, _FIND_TEXT_CAP)},
                    session_id=sid,
                )
                node_ids = results.get("nodeIds", [])
                if not node_ids:
                    continue
                # G9: visibility-priority (only probes when >1 match).
                visible_ids = await self._visible_node_ids(node_ids, sid)
                # G8: nth within the visible matches of THIS winning query
                # (don't fall through to the next query — keeps method/total
                # reporting clean and predictable).
                if nth > len(visible_ids):
                    return {
                        "found": False,
                        "reason": "nth_exceeds",
                        "method": method,
                        "requested_nth": nth,
                        "visible_total": len(visible_ids),
                        "total": total,
                    }
                node_id = visible_ids[nth - 1]
                await self.client.send.DOM.scrollIntoViewIfNeeded(
                    {"nodeId": node_id}, session_id=sid,
                )
                tag = await self._highlight_search_node(
                    node_id, text, nth, case_sensitive, highlight,
                )
                return {
                    "found": True,
                    "method": method,
                    "tag": tag,
                    "match_index": nth,
                    "visible_total": len(visible_ids),
                    "total": total,
                    "highlight": highlight,
                }
            except Exception as e:
                logger.debug("find_text query %s failed: %s", query, e)
                continue
            finally:
                # Bug fix: browser-use puts discardSearchResults after `break`,
                # so the winning query leaks its searchId. finally runs on
                # return/continue/raise — the nth_exceeds return also cleans up.
                if search_id is not None:
                    try:
                        await self.client.send.DOM.discardSearchResults(
                            {"searchId": search_id}, session_id=sid,
                        )
                    except Exception:
                        pass
        if await self._find_text_js_fallback(text, case_sensitive):
            return {"found": True, "method": "js-treewalker", "tag": None}
        return {"found": False, "method": "none", "tag": None}

    async def _find_text_js_fallback(self, text: str, case_sensitive: bool = False) -> bool:
        """TreeWalker over text nodes under document.body; scrollIntoView the
        first match's parentElement. ``text`` is injected via ``json.dumps``
        (JS-safe; browser-use f-string-injects and breaks on quotes). Only
        runs when all three XPath queries miss. ``case_sensitive=False``
        lowercases both sides (aligns with the XPath translate() path, G10)."""
        needle_js = json.dumps(text)
        if case_sensitive:
            cond = "t.includes(needle)"
        else:
            cond = "t.toLowerCase().includes(needle.toLowerCase())"
        js = (
            "(() => {"
            f"  const needle = {needle_js};"
            "  const walker = document.createTreeWalker("
            "    document.body, NodeFilter.SHOW_TEXT, null, false);"
            "  let node;"
            "  while ((node = walker.nextNode())) {"
            "    const t = node.nodeValue || '';"
            f"    if ({cond} && t.trim()) {{"
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

    async def _highlight_search_node(
        self,
        node_id: int,
        text: str,
        nth: int,
        case_sensitive: bool,
        highlight: Literal["box", "selection", "none"],
    ) -> str | None:
        """Highlight the matched node per ``highlight`` mode (G11). Always
        describes first to get the tag for the echo. ``box``=Overlay element
        box (default); ``selection``=native blue text selection via
        window.find (best-effort, Chromium-only); ``none``=skip. Returns the
        tag or None."""
        try:
            desc = await self.client.send.DOM.describeNode(
                {"nodeId": node_id}, session_id=self.current_session_id,
            )
            node = desc.get("node") or {}
            backend_id = node.get("backendNodeId")
            tag = (node.get("nodeName") or "").lower() or None
        except Exception:
            backend_id = None
            tag = None
        if highlight == "box":
            if backend_id:
                try:
                    await self.highlight_element(backend_id)
                except Exception:
                    pass
        elif highlight == "selection":
            await self._select_text_via_window_find(text, nth, case_sensitive)
        # "none": no highlight.
        return tag

    async def _select_text_via_window_find(
        self, text: str, nth: int, case_sensitive: bool,
    ) -> None:
        """Create a native browser text selection (blue highlight) at the nth
        occurrence of ``text`` via ``window.find`` (G11, best-effort). The
        element was already scrolled into view by the caller, so this only
        affects the visual selection. ``window.find`` is non-standard
        (Chromium-only, may drift); failure is silent. ``case_sensitive`` maps
        to window.find's 2nd arg; looped nth times to reach the nth match."""
        needle_js = json.dumps(text)
        js = (
            "(() => {"
            f"  const needle = {needle_js};"
            f"  const caseSensitive = {'true' if case_sensitive else 'false'};"
            f"  const n = {int(nth)};"
            "  let ok = false;"
            "  for (let i = 0; i < n; i++) {"
            "    ok = window.find(needle, caseSensitive, false, false, false, false, false);"
            "    if (!ok) break;"
            "  }"
            "  return ok;"
            "})()"
        )
        try:
            await self.execute_js(js)
        except Exception as e:
            logger.debug("find_text window.find selection failed: %s", e)

    async def _visible_node_ids(self, node_ids: list[int], sid: str) -> list[int]:
        """Filter nodeIds to visible ones (G9), in DOM order. Returns the
        visible subset; if none are visible, degrades to ``[node_ids[0]]``
        (best-effort — scroll somewhere rather than fail). Skips the probe for
        a single nodeId (single-match path is unchanged from P0). Visibility =
        non-zero bounding rect + not visibility:hidden/display:none (NOT
        offsetParent, which misclassifies position:fixed as hidden). Any CDP
        error -> treat all as visible (don't block the search)."""
        if len(node_ids) <= 1:
            return list(node_ids)
        try:
            visible_flags = await self._probe_visibility(node_ids, sid)
        except Exception as e:
            logger.debug("find_text visibility probe failed: %s", e)
            return list(node_ids)
        visible = [nid for nid, ok in zip(node_ids, visible_flags) if ok]
        if not visible:
            # All hidden (e.g. collapsed region) — degrade to first, don't fail.
            return [node_ids[0]]
        return visible

    async def _probe_visibility(self, node_ids: list[int], sid: str) -> list[bool]:
        """Batch visibility probe (G9, path A): resolve each nodeId to an
        objectId, then ONE ``Runtime.callFunctionOn`` over all of them
        returns a ``bool[]`` in the same order. Caller handles errors /
        degradation."""
        # Resolve each nodeId -> objectId (caller caps node_ids at _FIND_TEXT_CAP).
        object_ids: list[str] = []
        for nid in node_ids:
            resolved = await self.client.send.DOM.resolveNode(
                {"nodeId": nid}, session_id=sid,
            )
            object_ids.append((resolved.get("object") or {}).get("objectId"))
        # One callFunctionOn: this = first element, the rest as arguments.
        decl = (
            "function(...rest) {"
            "  const els = [this].concat(rest);"
            "  const vis = (el) => {"
            "    if (!el) return false;"
            "    const r = el.getBoundingClientRect();"
            "    const s = getComputedStyle(el);"
            "    return r.width > 0 && r.height > 0"
            "      && s.visibility !== 'hidden' && s.display !== 'none';"
            "  };"
            "  return els.map(vis);"
            "}"
        )
        res = await self.client.send.Runtime.callFunctionOn(
            {
                "functionDeclaration": decl,
                "objectId": object_ids[0],
                "arguments": [{"objectId": oid} for oid in object_ids[1:]],
                "returnByValue": True,
            },
            session_id=sid,
        )
        value = (res.get("result") or {}).get("value")
        if not isinstance(value, list) or len(value) != len(node_ids):
            raise RuntimeError(f"visibility probe unexpected shape: {value!r}")
        return [bool(v) for v in value]

    async def search_page(
        self,
        pattern: str,
        *,
        regex: bool = False,
        case_sensitive: bool = False,
        context_chars: int = 150,
        css_scope: str | None = None,
        max_results: int = 25,
        offset: int = 0,
        search_attributes: bool = False,
    ) -> dict:
        """Grep-style page text search via a single Runtime.evaluate.

        Mirrors browser-use ``search_page`` (``service.py:1260-1295`` + JS body
        ``:181-255``): a TreeWalker over text nodes builds an offset-indexed
        text buffer, a ``g``-flag RegExp exec loop collects matches with context
        + element path, and ``{matches, total, has_more}`` is returned.

        Raises ``RuntimeError`` on a JS exception (via ``execute_js``), on a
        null return, or when the JS layer reports ``{error: ...}`` (invalid
        regex / css_scope not found) — the action layer maps these to a hard
        ``ActionResult(error=...)``. A clean miss returns ``total=0`` and never
        raises; the action layer builds the soft echo.

        Phase 2 additions (surpass browser-use): ``offset`` paginates the match
        window (total is always the full count); ``search_attributes`` adds a
        separate ``attribute_matches`` scan; the text TreeWalker recurses into
        open shadow roots (``el.shadowRoot``) and same-origin iframes
        (``iframe.contentDocument``) so text inside Web Components / embedded
        documents is also indexed.

        Limitations: closed shadow roots are not pierced (``shadowRoot`` is
        null); cross-origin iframes raise ``SecurityError`` and are skipped
        (cross-origin traversal → Phase 3, needs ``Target.attachToTarget``).
        """
        js = _build_search_page_js(
            pattern, regex, case_sensitive, context_chars, css_scope,
            max_results, offset, search_attributes,
        )
        data = await self.execute_js(js)  # returnByValue=True -> dict; RuntimeError on exceptionDetails
        if data is None:
            raise RuntimeError("search_page returned no result")
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(f"search_page: {data['error']}")
        return data

    async def find_elements(
        self,
        selector: str,
        *,
        attributes: list[str] | None = None,
        max_results: int = 50,
        offset: int = 0,
        include_text: bool = True,
        first_only: bool = False,
        include_geometry: bool = False,
    ) -> dict:
        """Query DOM elements by CSS selector via a single Runtime.evaluate.

        Builds on browser-use ``find_elements`` (``service.py:1297-1330`` + JS
        body ``:257-298``) and extends it (Phase 2): per-element extraction
        (tag, text, attributes, children_count, optional geometry+visible),
        piercing **open shadow roots** and **same-origin iframes** (closed
        shadow roots and cross-origin iframes are skipped — ``shadowRoot`` is
        null / ``contentDocument`` raises). ``src``/``href`` resolve to absolute
        URLs. Returns ``{elements, total, showing, offset, has_more}``; the
        ``offset`` window paginates the document-order match list and ``total``
        is the full count across all pierced roots.

        Raises ``RuntimeError`` on a JS exception (via ``execute_js``), on a
        null return, or when the JS layer reports ``{error: ...}`` (invalid
        CSS selector) — the action layer maps these to a hard
        ``ActionResult(error=...)``. A clean miss returns ``total=0`` and
        never raises; the action layer builds the soft echo.
        """
        js = _build_find_elements_js(
            selector, attributes, max_results, include_text, first_only, offset, include_geometry,
        )
        data = await self.execute_js(js)  # returnByValue=True -> dict; RuntimeError on exceptionDetails
        if data is None:
            raise RuntimeError("find_elements returned no result")
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(f"find_elements: {data['error']}")
        return data

    async def find_elements_node_ids(
        self,
        selector: str,
        *,
        max_results: int = 50,
        offset: int = 0,
        include_user_agent_shadow: bool = True,
    ) -> dict:
        """Resolve CSS-selected elements to stable backendNodeIds via DOM.performSearch.

        Reuses the find_text chain (``DOM.performSearch`` → ``getSearchResults``
        → ``describeNode`` → ``backendNodeId``; session.py find_text). CDP's
        ``performSearch`` accepts a CSS selector directly as ``query`` (same as
        XPath), so ``selector`` needs no conversion. Returns
        ``{node_ids, total, showing, offset, has_more}`` where each entry is
        ``{backend_id, tag}`` and ``backend_id`` is a backendNodeId usable
        directly as the ``index``/``element_id`` of click/input_text
        (index===backend_id in this system; interactive elements are in
        selector_map).

        Heavier than find_elements (one describeNode round-trip per element);
        use only when stable ids are needed for subsequent interaction. No text
        is returned — pair with find_elements (without return_node_ids) for
        text/attributes. ``includeUserAgentShadowDOM`` mirrors find_text.
        """
        sid = self.current_session_id
        search = await self.client.send.DOM.performSearch(
            {"query": selector, "includeUserAgentShadowDOM": include_user_agent_shadow},
            session_id=sid,
        )
        search_id = search.get("searchId")
        total = search.get("resultCount", 0)
        try:
            if total <= 0:
                return {"node_ids": [], "total": 0, "showing": 0, "offset": offset, "has_more": False}
            to_index = min(total, offset + max_results)
            results = await self.client.send.DOM.getSearchResults(
                {"searchId": search_id, "fromIndex": offset, "toIndex": to_index},
                session_id=sid,
            )
            node_ids = results.get("nodeIds", [])
            out: list[dict] = []
            for nid in node_ids:
                desc = await self.client.send.DOM.describeNode({"nodeId": nid}, session_id=sid)
                node = desc.get("node", {}) or {}
                bid = node.get("backendNodeId")
                if bid is None:
                    continue
                tag = (node.get("nodeName") or node.get("localName") or "?").lower()
                out.append({"backend_id": bid, "tag": tag})
            return {
                "node_ids": out,
                "total": total,
                "showing": len(out),
                "offset": offset,
                "has_more": (offset + len(out)) < total,
            }
        finally:
            if search_id is not None:
                try:
                    await self.client.send.DOM.discardSearchResults(
                        {"searchId": search_id}, session_id=sid,
                    )
                except Exception:
                    pass

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

        # G11 懒加载重试：select 有 option 但全部为空（text 与 value 都空白）→ option
        # 多半异步填充。focus() + sleep 1.0s + 重跑 _SELECT_OPTION_JS 一次（仅 native，
        # 镜像 browser-use default_action_watchdog.py:3509-3547）。重试先于点击回退
        # （懒加载是比框架回退更廉价的假设）。全空谓词：success=False 且 availableOptions
        # 非空列表且每项 text/value 都空白。
        avail = selection.get("availableOptions") or []
        all_empty = (
            not selection.get("success")
            and isinstance(avail, list)
            and len(avail) > 0
            and all(
                not (o.get("text") or "").strip() and not (o.get("value") or "").strip()
                for o in avail
            )
        )
        if all_empty:
            await self.client.send.Runtime.callFunctionOn(
                {
                    "objectId": object_id,
                    "functionDeclaration": "function(){ try{ this.focus(); } catch(e){} }",
                    "returnByValue": True,
                },
                session_id=self.current_session_id,
            )
            await asyncio.sleep(1.0)
            retry = await self.client.send.Runtime.callFunctionOn(
                {
                    "objectId": object_id,
                    "functionDeclaration": _SELECT_OPTION_JS,
                    "arguments": [{"value": value}],
                    "returnByValue": True,
                },
                session_id=self.current_session_id,
            )
            selection = retry.get("result", {}).get("value", {}) or {}

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

    async def _call_setter_on_node(
        self, backend_node_id: int, function_declaration: str, value: str,
    ) -> dict:
        """resolveNode + callFunctionOn(setter JS, value) -> dict。set_aria_option /
        set_custom_option 共用，省去三处 15 行 resolveNode+callFunctionOn 样板。返回
        setter 原始 dict（success/message/value/availableOptions/error）。CDP/JS 异常
        上抛（caller 友好包装）。镜像 fetch_select_options 的 resolveNode+callFunctionOn 形状。"""
        resolve = await self.client.send.DOM.resolveNode(
            {"backendNodeId": backend_node_id},
            session_id=self.current_session_id,
        )
        object_id = resolve["object"]["objectId"]
        result = await self.client.send.Runtime.callFunctionOn(
            {
                "objectId": object_id,
                "functionDeclaration": function_declaration,
                "arguments": [{"value": value}],
                "returnByValue": True,
            },
            session_id=self.current_session_id,
        )
        return result.get("result", {}).get("value", {}) or {}

    async def eval_function_on_node(
        self, backend_node_id: int, function_declaration: str,
    ) -> Any:
        """在 backendNodeId 绑定的元素上跑 ``function_declaration``（``this`` = 该元素），返回其
        ``return`` 值（``returnByValue=True``）。resolveNode + callFunctionOn，无入参——镜像
        ``_call_setter_on_node`` 但不带 value。CDP/JS 异常上抛（caller 用 try/except 兜底）。

        供 ``upload_identity.capture_upload_clue`` 在【目标 file input 自身】提取身份上下文
        （region_text/in_dialog/container_rect/affordance），避开 ``_upload_input_contexts`` 的候选
        计数对齐（坑③：``selector_map`` 的 file input 数 ≠ ``document.querySelectorAll`` 时对齐失败返
        ``{}``）——抖音二次上传（裁剪弹窗重传）正是触发此坑。"""
        resolve = await self.client.send.DOM.resolveNode(
            {"backendNodeId": backend_node_id},
            session_id=self.current_session_id,
        )
        object_id = resolve["object"]["objectId"]
        result = await self.client.send.Runtime.callFunctionOn(
            {
                "objectId": object_id,
                "functionDeclaration": function_declaration,
                "returnByValue": True,
            },
            session_id=self.current_session_id,
        )
        return result.get("result", {}).get("value")

    async def set_aria_option(self, backend_node_id: int, value: str) -> dict:
        """在 ARIA menu/listbox（backendNodeId 绑定）中选 option（_fetch_aria_options 的
        写侧对应）。返回与 set_select_option 同形 dict。CDP/JS 异常上抛（caller 包装）。"""
        return await self._call_setter_on_node(backend_node_id, _SET_ARIA_JS, value)

    async def set_custom_option(self, backend_node_id: int, value: str) -> dict:
        """在 custom-class 下拉（Semantic UI 等，backendNodeId 绑定）中选 option
        （_fetch_custom_class_options 的写侧对应）。返回与 set_select_option 同形 dict。"""
        return await self._call_setter_on_node(backend_node_id, _SET_CUSTOM_JS, value)

    async def set_dropdown_option(self, backend_node_id: int, value: str) -> dict:
        """写侧 dispatcher（镜像 fetch_dropdown_options）。复用读侧 dispatcher 做分类
        （同一份 JS 判型，读写零漂移 —— D1），再按 source 路由到对应 setter。返回 setter
        dict + 'source'（'aria'|'custom'|'child-depth-N'|None）；source=None 表示非任何已知
        下拉类型（action 层据此返友好 error）。CDP/JS 异常上抛（caller 包装）。

        与读侧对称：combobox 不进此 dispatcher（需真实 click/Escape，由 action 层 Python
        预分类直调 set_combobox_option）。"""
        classified = await self.fetch_dropdown_options(backend_node_id)
        source = classified["source"]
        if source == "aria":
            result = await self.set_aria_option(backend_node_id, value)
        elif source == "custom":
            result = await self.set_custom_option(backend_node_id, value)
        elif source is not None and str(source).startswith("child-depth-"):
            result = await self._set_subtree_option(backend_node_id, value)
        else:
            return {"success": False, "source": None, "error": "not a recognized dropdown"}
        result["source"] = source
        return result

    async def _fetch_aria_options(self, backend_node_id: int) -> list[dict] | None:
        """Read options of an ARIA menu/listbox scoped to backendNodeId.

        Mirrors fetch_select_options' resolveNode + callFunctionOn shape (only
        the JS differs). Returns None when the element is not aria-shaped (lets
        the dispatcher try the next type); a list (possibly empty) when it IS
        aria-shaped. Capped to 200 options. Raises on CDP/JS error (caller wraps).
        """
        resolve = await self.client.send.DOM.resolveNode(
            {"backendNodeId": backend_node_id},
            session_id=self.current_session_id,
        )
        object_id = resolve["object"]["objectId"]
        result = await self.client.send.Runtime.callFunctionOn(
            {
                "objectId": object_id,
                "functionDeclaration": _ARIA_OPTIONS_JS,
                "returnByValue": True,
            },
            session_id=self.current_session_id,
        )
        value = result.get("result", {}).get("value")
        return value  # None | list[dict]

    async def _fetch_custom_class_options(self, backend_node_id: int) -> list[dict] | None:
        """Read options of a custom-class dropdown (Semantic UI etc.) scoped to
        backendNodeId. None = not custom-shaped; list = is custom (possibly
        empty). Capped to 200 (D4). Raises on CDP/JS error (caller wraps).
        """
        resolve = await self.client.send.DOM.resolveNode(
            {"backendNodeId": backend_node_id},
            session_id=self.current_session_id,
        )
        object_id = resolve["object"]["objectId"]
        result = await self.client.send.Runtime.callFunctionOn(
            {
                "objectId": object_id,
                "functionDeclaration": _CUSTOM_CLASS_OPTIONS_JS,
                "returnByValue": True,
            },
            session_id=self.current_session_id,
        )
        value = result.get("result", {}).get("value")
        return value  # None | list[dict]

    async def fetch_dropdown_options(self, backend_node_id: int) -> dict:
        """Dispatcher: try each non-native dropdown type in turn, return the
        first hit. Returns {"options": list[dict], "source": str | None};
        source None means the element is not a recognized non-native dropdown
        (true negative). Raises on CDP/JS error (caller wraps with a friendly
        message).

        Tries ARIA -> custom-class -> subtree search (depth 4); first hit wins.
        """
        aria = await self._fetch_aria_options(backend_node_id)
        if aria is not None:
            return {"options": aria, "source": "aria"}
        custom = await self._fetch_custom_class_options(backend_node_id)
        if custom is not None:
            return {"options": custom, "source": "custom"}
        found = await self.search_children_for_dropdowns(backend_node_id)
        if found["options"]:
            return {"options": found["options"], "source": found["source"]}
        return {"options": [], "source": None}

    async def _collapse_combobox(self, object_id: str | None) -> None:
        """强制收起已展开的 combobox（Escape + blur）。load-bearing：残留展开的遮罩
        会拦截后续 click。best-effort（失败仅 debug 日志）—— 读/写 combobox flow 的
        finally 共用（D6 行为等价重构）。"""
        try:
            await self.send_keys("Escape")
            if object_id is not None:
                await self.client.send.Runtime.callFunctionOn(
                    {
                        "objectId": object_id,
                        "functionDeclaration": "function(){ try{ this.blur(); } catch(e){} }",
                        "returnByValue": True,
                    },
                    session_id=self.current_session_id,
                )
        except Exception as e:
            logger.debug("combobox collapse failed: %s", e)

    async def _collapse_custom_dropdown(self, trigger_backend_id: int) -> None:
        """收起自定义下拉。先 Escape（多数框架有效）；若仍开着（抖音 Semi UI 不认 Escape、
        也不认合成 body-click），真实 click 触发器 toggle 收起——Semi 这种 React 受控下拉只在
        trusted outside / 再点触发器时收起。load-bearing：dropdown_options/select_dropdown 跑完
        若留下拉开着，agent 下一步会看到选项就去 click（老习惯），绕开 select_dropdown。best-effort。"""
        try:
            await self.send_keys("Escape")
        except Exception as e:
            logger.debug("custom dropdown Escape failed: %s", e)
        if await self._custom_dropdown_still_open():
            try:
                await self.click_element(trigger_backend_id)   # 再点触发器 → toggle 收起
            except Exception as e:
                logger.debug("custom dropdown toggle-close failed: %s", e)

    async def _custom_dropdown_still_open(self) -> bool:
        """是否还有展开的自定义下拉：Semi 的 .semi-popover-wrapper-show，或任何可见的
        [role=listbox]（非零 rect）。best-effort（异常返 False）。"""
        try:
            r = await self.client.send.Runtime.evaluate(
                {"expression": "!!document.querySelector('.semi-popover-wrapper-show') "
                               "|| Array.from(document.querySelectorAll('[role=\"listbox\"]'))"
                               ".some(function(lb){ var r=lb.getBoundingClientRect(); "
                               "return r.width>0 && r.height>0; })",
                 "returnByValue": True},
                session_id=self.current_session_id,
            )
            return bool((r.get("result", {}) or {}).get("value"))
        except Exception as e:
            logger.debug("custom dropdown still-open check failed: %s", e)
            return False

    async def expand_and_fetch_combobox_options(self, backend_node_id: int) -> list[dict]:
        """Expand a combobox (real click), read its aria-controls listbox, then
        ALWAYS collapse (finally). Python flow — a combobox needs a real click +
        Escape (await on session methods), not a single callFunctionOn. Options
        capped to 200 (D4). Raises RuntimeError when the listbox isn't found, or
        re-raises CDP/JS errors; collapse still runs in finally (D3 — a combobox
        left expanded overlays the page and breaks subsequent clicks).

        Uses getElementById(aria-controls) (not subtree querySelectorAll) so a
        React Portal-rendered listbox (attached at document.body) is still found.
        """
        # 1. 展开（真实 click，复用 click_element：scrollIntoView + occlusion + JS 回退）
        await self.click_element(backend_node_id)
        # 2. 固定等懒加载（D4；poll-until-stable 推迟到 P1c-2，按真实 flake 反馈再开）
        await asyncio.sleep(0.5)
        # 3. 读（一次 callFunctionOn）
        object_id = None
        try:
            resolve = await self.client.send.DOM.resolveNode(
                {"backendNodeId": backend_node_id},
                session_id=self.current_session_id,
            )
            object_id = resolve["object"]["objectId"]
            result = await self.client.send.Runtime.callFunctionOn(
                {
                    "objectId": object_id,
                    "functionDeclaration": _COMBOBOX_OPTIONS_JS,
                    "returnByValue": True,
                },
                session_id=self.current_session_id,
            )
            payload = result.get("result", {}).get("value") or {}
            if not payload.get("listboxFound"):
                raise RuntimeError(
                    "combobox listbox not found: " + str(payload.get("error", ""))
                )
            return payload.get("options", [])
        finally:
            # 4. 强制收起（即便第 3 步抛错也收起 —— D3 load-bearing，非装饰；抽到
            # _collapse_combobox 供读/写 combobox flow 共用，行为等价）
            await self._collapse_combobox(object_id)

    async def set_combobox_option(self, backend_node_id: int, value: str) -> dict:
        """在 combobox 的 aria-controls listbox 中选 option。Python flow 镜像
        expand_and_fetch_combobox_options：展开（真实 click）→ settle → 解析 listbox
        objectId（_COMBOBOX_LISTBOX_ID_JS，returnByValue=False）→ 在 listbox 上写
        （_SET_COMBOBOX_OPTION_JS）→ finally 强制收起。NOTE：browser-use 缺 combobox 写侧
        （已知不一致），此为从读 flow 自撰（D4）。返回与 set_select_option 同形 dict。
        CDP/JS 异常上抛；收起仍于 finally 跑。"""
        await self.click_element(backend_node_id)
        await asyncio.sleep(0.5)
        combo_object_id = None
        try:
            combo_resolve = await self.client.send.DOM.resolveNode(
                {"backendNodeId": backend_node_id},
                session_id=self.current_session_id,
            )
            combo_object_id = combo_resolve["object"]["objectId"]
            # 定位 listbox（returnByValue=False 取 RemoteObject）
            lb = await self.client.send.Runtime.callFunctionOn(
                {
                    "objectId": combo_object_id,
                    "functionDeclaration": _COMBOBOX_LISTBOX_ID_JS,
                    "returnByValue": False,
                },
                session_id=self.current_session_id,
            )
            lb_result = lb.get("result", {}) or {}
            listbox_object_id = lb_result.get("objectId")
            if not listbox_object_id:
                return {
                    "success": False,
                    "error": "combobox listbox not found (no aria-controls/aria-owns target)",
                    "availableOptions": [],
                }
            # 在 listbox 上写
            result = await self.client.send.Runtime.callFunctionOn(
                {
                    "objectId": listbox_object_id,
                    "functionDeclaration": _SET_COMBOBOX_OPTION_JS,
                    "arguments": [{"value": value}],
                    "returnByValue": True,
                },
                session_id=self.current_session_id,
            )
            return result.get("result", {}).get("value", {}) or {}
        finally:
            await self._collapse_combobox(combo_object_id)

    async def _effective_click_bid(
        self, trigger_object_id: str, fallback_bid: int,
    ) -> int:
        """解析真正能展开的点击目标 backendNodeId：触发器 4 层内最外层 select-ish 祖先
        （_EFFECTIVE_CLICK_TARGET_JS，如 .bcc-select / .semi-select），否则原触发器。
        best-effort：任一步失败回退 fallback_bid。用于自定义下拉 open 步——坐标 click 须落在
        持有展开 handler 的组件 chrome 上（创作声明：点 input 不展开，点 .bcc-select 才展开）。"""
        try:
            r = await self.client.send.Runtime.callFunctionOn(
                {
                    "objectId": trigger_object_id,
                    "functionDeclaration": _EFFECTIVE_CLICK_TARGET_JS,
                    "returnByValue": False,
                },
                session_id=self.current_session_id,
            )
            oid = (r.get("result", {}) or {}).get("objectId")
            if not oid:
                return fallback_bid
            desc = await self.client.send.DOM.describeNode(
                {"objectId": oid}, session_id=self.current_session_id,
            )
            bid = (desc.get("node", {}) or {}).get("backendNodeId")
            return int(bid) if bid else fallback_bid
        except Exception as e:
            logger.debug("effective click target resolve failed: %s", e)
            return fallback_bid

    async def _open_and_discover_listbox(
        self, backend_node_id: int,
    ) -> tuple[str | None, str | None]:
        """展开自定义下拉并发现其 option list。返回 (listbox_object_id, trigger_object_id)；
        listbox 未发现则 (None, trigger_object_id)。collapse 由 caller 在 finally 负责。

        两次 click 尝试（容错）：先点 effective target（组件根，解决创作声明 input 不展开），
        若没发现 list 再点原触发器（兜底只能点自身的触发器）。同一目标只点一次。"""
        resolve = await self.client.send.DOM.resolveNode(
            {"backendNodeId": backend_node_id}, session_id=self.current_session_id,
        )
        trigger_object_id = resolve["object"]["objectId"]
        click_bid = await self._effective_click_bid(trigger_object_id, backend_node_id)
        attempts = [click_bid] if click_bid == backend_node_id else [click_bid, backend_node_id]
        listbox_object_id = None
        for bid in attempts:
            await self.click_element(bid)
            await asyncio.sleep(0.5)
            lb = await self.client.send.Runtime.callFunctionOn(
                {
                    "objectId": trigger_object_id,
                    "functionDeclaration": _CUSTOM_LISTBOX_DISCOVER_JS,
                    "returnByValue": False,
                },
                session_id=self.current_session_id,
            )
            listbox_object_id = (lb.get("result", {}) or {}).get("objectId")
            if listbox_object_id:
                break
        return listbox_object_id, trigger_object_id

    async def expand_and_fetch_custom_options(self, backend_node_id: int) -> list[dict]:
        """开自定义下拉、读其 options，finally 收起（镜像 expand_and_fetch_combobox_options）。
        flow：_open_and_discover_listbox → _read_custom_options。listbox 未发现抛 RuntimeError；
        CDP/JS 异常上抛；收起于 finally（残留展开的遮罩会挡后续 click）。"""
        trigger_object_id = None
        try:
            listbox_object_id, trigger_object_id = await self._open_and_discover_listbox(backend_node_id)
            if not listbox_object_id:
                raise RuntimeError("custom dropdown listbox not found after opening")
            return await self._read_custom_options(listbox_object_id)
        finally:
            await self._collapse_custom_dropdown(backend_node_id)

    async def set_custom_dropdown_option(self, backend_node_id: int, value: str) -> dict:
        """开 + 发现 list + 找匹配 option + **真实 CDP click** option + readback，finally 收起
        （镜像 set_combobox_option，但选中用 click_element 而非 JS 合成 click——Semi UI 等 React
        框架的选中/收起 handler 只认真实点击）。含虚拟化 scroll-until-found：option 不在当前视口时
        滚 list 再找（≤_CUSTOM_SCROLL_CAP）。返回与 set_select_option 同形 setter dict。CDP/JS
        异常上抛；收起于 finally。"""
        trigger_object_id = None
        try:
            listbox_object_id, trigger_object_id = await self._open_and_discover_listbox(backend_node_id)
            if not listbox_object_id:
                return {"success": False, "error": "custom dropdown listbox not found after opening",
                        "availableOptions": []}
            option_object_id = await self._find_option_object_id(listbox_object_id, value)
            if not option_object_id:
                return {"success": False,
                        "error": f"Option with text or value '{value}' not found in custom dropdown",
                        "availableOptions": await self._read_custom_options(listbox_object_id)}
            option_bid = await self._backend_id_of_object(option_object_id)
            if option_bid is None:
                return {"success": False, "error": "custom dropdown option could not be resolved",
                        "availableOptions": await self._read_custom_options(listbox_object_id)}
            await self.click_element(option_bid)          # 真实 click：最可靠的选中交互
            await asyncio.sleep(0.3)
            # 真实 click 命中匹配 option 即视为选中（Semi/B 站均如此）。不作硬性 readback：
            # option 是否脱离/打 selected 标记因框架而异，硬查会误报「not retained」
            # （如 B 站分区：值已变但 option div 仍连着、无 selected class）。
            return {"success": True, "message": f"Selected option: {value}", "value": value}
        finally:
            await self._collapse_custom_dropdown(backend_node_id)

    async def _read_custom_options(self, listbox_object_id: str) -> list[dict]:
        """在发现的 list 节点上读 options（_CUSTOM_OPEN_OPTIONS_JS）。返回 [{text,value,selected}]。"""
        result = await self.client.send.Runtime.callFunctionOn(
            {"objectId": listbox_object_id, "functionDeclaration": _CUSTOM_OPEN_OPTIONS_JS,
             "returnByValue": True},
            session_id=self.current_session_id,
        )
        return result.get("result", {}).get("value") or []

    async def _find_option_object_id(self, listbox_object_id: str, value: str) -> str | None:
        """在发现的 list 上找匹配 option（_CUSTOM_FIND_OPTION_JS，精确→包含）。含虚拟化
        scroll-until-found：没命中就滚一页再找（≤_CUSTOM_SCROLL_CAP）。返回 option objectId 或 None。"""
        for _ in range(_CUSTOM_SCROLL_CAP + 1):
            r = await self.client.send.Runtime.callFunctionOn(
                {"objectId": listbox_object_id, "functionDeclaration": _CUSTOM_FIND_OPTION_JS,
                 "arguments": [{"value": value}], "returnByValue": False},
                session_id=self.current_session_id,
            )
            oid = (r.get("result", {}) or {}).get("objectId")
            if oid:
                return oid
            if not await self._scroll_listbox(listbox_object_id):
                return None
            await asyncio.sleep(0.12)
        return None

    async def _backend_id_of_object(self, object_id: str) -> int | None:
        """remote objectId → backendNodeId（DOM.describeNode）。best-effort：失败返 None。"""
        try:
            desc = await self.client.send.DOM.describeNode(
                {"objectId": object_id}, session_id=self.current_session_id,
            )
            bid = (desc.get("node", {}) or {}).get("backendNodeId")
            return int(bid) if bid else None
        except Exception as e:
            logger.debug("backend_id_of_object failed: %s", e)
            return None

    async def _scroll_listbox(self, listbox_object_id: str) -> bool:
        """滚发现的 listbox 下一页（_SCROLL_LISTBOX_JS）。返回是否真滚了（仍可滚且没到底），
        否则（不可滚/到底/异常）False。best-effort（异常仅 debug 日志）。"""
        try:
            r = await self.client.send.Runtime.callFunctionOn(
                {
                    "objectId": listbox_object_id,
                    "functionDeclaration": _SCROLL_LISTBOX_JS,
                    "returnByValue": True,
                },
                session_id=self.current_session_id,
            )
            return bool((r.get("result", {}) or {}).get("value"))
        except Exception as e:
            logger.debug("listbox scroll failed: %s", e)
            return False

    async def search_children_for_dropdowns(self, backend_node_id: int, max_depth: int = 4) -> dict:
        """BFS the subtree (max_depth levels) for a dropdown-shaped descendant
        and read its options in place. JS-side recursion (the Python-side
        serialized tree may be pruned, so Python can't walk it reliably). The
        start node itself is skipped (depth 0). Returns
        {"options": list[dict], "source": "child-depth-N" | None}. Raises on
        CDP/JS error (caller wraps).
        """
        resolve = await self.client.send.DOM.resolveNode(
            {"backendNodeId": backend_node_id},
            session_id=self.current_session_id,
        )
        object_id = resolve["object"]["objectId"]
        result = await self.client.send.Runtime.callFunctionOn(
            {
                "objectId": object_id,
                "functionDeclaration": _SUBTREE_SEARCH_JS,
                "arguments": [{"value": max_depth}],
                "returnByValue": True,
            },
            session_id=self.current_session_id,
        )
        value = result.get("result", {}).get("value") or {"options": [], "source": None}
        return value

    async def _call_setter_on_object(
        self, object_id: str, function_declaration: str, value: str,
    ) -> dict:
        """同 _call_setter_on_node 但直接接 remote objectId（子树/combobox 子代经 JS
        locator 解析，只有 objectId 无 backendNodeId）。返回 setter dict。CDP/JS 异常上抛。"""
        result = await self.client.send.Runtime.callFunctionOn(
            {
                "objectId": object_id,
                "functionDeclaration": function_declaration,
                "arguments": [{"value": value}],
                "returnByValue": True,
            },
            session_id=self.current_session_id,
        )
        return result.get("result", {}).get("value", {}) or {}

    async def _set_subtree_option(self, backend_node_id: int, value: str) -> dict:
        """定位子代下拉（BFS，镜像 search_children_for_dropdowns 但返回子代 objectId+类型），
        按类型调 _SET_ARIA_JS / _SET_CUSTOM_JS。被 set_dropdown_option 的 child-depth-N
        分支调用（D5 两阶段编排：JS returnByValue 会剥离对象身份，故 locator 先取子代
        RemoteObject 再写）。返回 setter dict（无 source —— dispatcher 补）。CDP/JS 异常上抛。"""
        resolve = await self.client.send.DOM.resolveNode(
            {"backendNodeId": backend_node_id}, session_id=self.current_session_id,
        )
        parent_object_id = resolve["object"]["objectId"]
        located = await self.client.send.Runtime.callFunctionOn(
            {
                "objectId": parent_object_id,
                "functionDeclaration": _SUBTREE_LOCATE_JS,
                "arguments": [{"value": 4}],
                "returnByValue": False,
            },
            session_id=self.current_session_id,
        )
        payload = located.get("result", {}) or {}
        if not payload.get("found"):
            return {"success": False, "error": "subtree child dropdown vanished between read and write"}
        setter = _SET_ARIA_JS if payload.get("type") == "aria" else _SET_CUSTOM_JS
        # CDP-shape：returnByValue=False 下嵌套节点 objectId 兼容顶层与 node.objectId 两种
        child_object_id = payload.get("objectId") or (payload.get("node") or {}).get("objectId")
        if not child_object_id:
            return {"success": False, "error": "could not resolve subtree child objectId"}
        return await self._call_setter_on_object(child_object_id, setter, value)

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
