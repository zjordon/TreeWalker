from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, field_validator, model_validator


class _LocatorParamsMixin(BaseModel):
	"""P8：index/element_id 二选一（exactly-one）校验的公共基类。

	GLM 偶发裸发动作（click 不带 index，8/17 两跑合计 7 次）——两字段都可选时
	Pydantic 校验放行，要到执行层守卫才报错，白烧一整步。子类挂上本 mixin 后，
	step 层 `_validate_params_or_retry` 会在步内拦截并带错误信息重试 LLM，
	不再消耗步数预算。（validator 必须在类体内定义——pydantic v2 按类创建时
	收集，事后 setattr 不注册。）
	"""

	@model_validator(mode="after")
	def _check_exactly_one_locator(self) -> "_LocatorParamsMixin":
		# 子类（ClickParams/InputTextParams）保证 index/element_id 字段存在
		if (self.index is None) == (self.element_id is None):  # type: ignore[attr-defined]
			raise ValueError(
				"provide exactly one of `index` or `element_id` "
				"(currently both missing or both given)"
			)
		return self


class NavigateParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(description="The URL to navigate to")
    new_tab: bool = Field(
        default=False,
        description="If True, open the URL in a new tab instead of navigating the current tab",
    )


class ClickParams(_LocatorParamsMixin):
    model_config = ConfigDict(extra="forbid")
    index: int | None = Field(
        default=None,
        description="ID of the element to click, shown in brackets in the DOM tree. "
        "Provide exactly one of index / element_id.",
    )
    element_id: int | None = Field(
        default=None,
        description="Stable backend node id from find_elements(return_node_ids=True); an alternative "
        "to index (same resolution path). Provide exactly one of index / element_id.",
    )


class InputTextParams(_LocatorParamsMixin):
    model_config = ConfigDict(extra="forbid")
    index: int | None = Field(
        default=None,
        description="ID of the element to type into, shown in brackets in the DOM tree. "
        "Provide exactly one of index / element_id.",
    )
    element_id: int | None = Field(
        default=None,
        description="Stable backend node id from find_elements(return_node_ids=True); an alternative "
        "to index (same resolution path). Provide exactly one of index / element_id.",
    )
    text: str = Field(description="Text to type into the element")
    clear: bool = Field(default=True, description="Whether to clear existing text first")


class ScrollParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: int = Field(
        default=3,
        ge=1,
        le=10,
        description=(
            "Number of viewport-heights to scroll (1-10). Check the scroll info on "
            "scrollable elements in the DOM tree (e.g. '3.4 pages below') to judge "
            "how much remains before scrolling."
        ),
    )
    direction: Literal["up", "down"] = Field(
        default="down",
        description="Scroll direction: 'down' (default) or 'up'.",
    )


class SearchParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(description="Search query to type into the search engine")
    engine: Literal["baidu", "google", "bing", "duckduckgo"] = Field(
        default="baidu",
        description="Search engine: baidu (default, works in China), google, bing, or duckduckgo",
    )


class ExtractParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(
        description=(
            "What information to extract from the current page. Be specific: name the "
            "fields/items and any filtering criteria. (Equivalent to browser-use `query`.)"
        )
    )
    extract_links: bool = Field(
        default=True,
        description="If True, preserve <a href> URLs in the source markdown. Set False for text-only extraction.",
    )
    extract_images: bool = Field(
        default=True,
        description="If True, preserve <img src> URLs in the source markdown.",
    )
    start_from_char: int = Field(
        default=0,
        ge=0,
        description=(
            "Character offset to resume extraction from (for paginating large pages). "
            "Default 0 = start at the beginning. Use the offset reported by a previous "
            "truncated extract call to continue."
        ),
    )
    already_collected: list[str] | None = Field(
        default=None,
        description=(
            "Items already extracted (dedupe across pages/chunks). Pass prior results "
            "(as text) and the model will skip exact duplicates. Optional."
        ),
    )

    @field_validator("already_collected")
    @classmethod
    def _drop_empty_items(cls, v):
        if v is not None:
            v = [item for item in v if item and item.strip()]
        return v or None


class SendKeysParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keys: str = Field(
        min_length=1,
        description=(
            "Key combination or text to send. "
            "Combos use '+': 'Control+a', 'Shift+T', 'Alt+F4'. "
            "Named keys: 'Enter', 'Tab', 'Escape', 'ArrowUp', 'F5', etc. "
            "Plain text (e.g. 'hello') is typed character by character."
        ),
    )


class SwitchTabParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tab_id: str = Field(min_length=1, description="Tab ID (last 4 characters) to switch to")


class CloseTabParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tab_id: str = Field(
        default="",
        description="Tab ID (last 4 characters) to close. Empty string closes the current tab.",
    )


class WaitParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seconds: int = Field(default=3, ge=1, le=30, description="Seconds to wait")


class GoBackParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FindElementsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selector: str = Field(
        description='CSS selector to query elements (e.g. "table tr", "a.link", "div.product")'
    )
    attributes: list[str] | None = Field(
        default=None,
        description='Specific attributes to extract (e.g. ["href", "src", "class"]). '
        'If not set, returns tag and text only. src/href are resolved to absolute URLs.',
    )
    max_results: int = Field(
        default=50, ge=1, le=200,
        description="Maximum elements to return (total count is always reported even when truncated).",
    )
    offset: int = Field(
        default=0, ge=0,
        description="0-based index of the first element to return (for paginating large result sets; "
        "total is always the full count across all roots including shadow DOM / same-origin iframes).",
    )
    include_text: bool = Field(default=True, description="Include text content of each element")
    first_only: bool = Field(
        default=False,
        description="Return only the first matching element; total still reports the full count so you know there are more.",
    )
    include_geometry: bool = Field(
        default=False,
        description="Add per-element getBoundingClientRect() {x,y,w,h} and a stable visibility flag "
        "(checks ancestor display/visibility/opacity + non-zero size). Default off to avoid overhead.",
    )
    return_node_ids: bool = Field(
        default=False,
        description="Return stable backend node ids usable directly as click/input_text `index` "
        "(uses DOM.performSearch — heavier, one CDP round-trip per element; no text). "
        "Offset applies to the document-order match list.",
    )


class FindTextParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, description="Text to search for on the page")
    nth: int = Field(
        default=1,
        ge=1,
        description=(
            "Which match to scroll to, 1-based (default: 1st). The echo reports "
            "total/visible counts so the caller can increment to navigate matches."
        ),
    )
    case_sensitive: bool = Field(
        default=False,
        description="Case-sensitive match (default: case-insensitive, like Ctrl+F). Aligns with search_page.",
    )
    highlight: Literal["box", "selection", "none"] = Field(
        default="box",
        description=(
            "Highlight style: box=element outline (default), selection=native blue "
            "text selection (best-effort, Chromium-only), none=off."
        ),
    )


class ScreenshotClipParams(BaseModel):
    """Viewport rectangle for a clipped screenshot, in CSS pixels."""
    model_config = ConfigDict(extra="forbid")
    x: float = Field(default=0.0, ge=0.0, description="Left offset in CSS pixels")
    y: float = Field(default=0.0, ge=0.0, description="Top offset in CSS pixels")
    width: float = Field(gt=0.0, description="Rectangle width in CSS pixels")
    height: float = Field(gt=0.0, description="Rectangle height in CSS pixels")


class ScreenshotParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["png", "jpeg", "webp"] = Field(
        default="png",
        description="Image format. 'jpeg' supports quality; 'png' is lossless.",
    )
    quality: int | None = Field(default=None, ge=0, le=100, description="0-100, only effective when format='jpeg'.")
    clip: ScreenshotClipParams | None = Field(default=None, description="Optional viewport rect {x,y,width,height} (CSS px).")
    full_page: bool = Field(default=False, description="Capture the full scrollable page instead of the viewport.")
    save_path: str = Field(default="", description="Optional file path to save the screenshot bytes to disk.")


class SaveAsPdfParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(description="File path to save the PDF (parent dirs auto-created).")
    paper_format: Literal["letter", "legal", "a4", "a3", "tabloid"] = Field(
        default="letter", description="Paper size."
    )
    landscape: bool = Field(default=False, description="Landscape orientation.")
    print_background: bool = Field(default=True, description="Include background graphics/colors.")
    scale: float = Field(default=1.0, ge=0.1, le=2.0, description="Render scale (0.1-2.0).")


class DropdownOptionsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int = Field(description="ID of the select element, shown in brackets in the DOM tree")


class SelectDropdownParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int = Field(description="ID of the select element, shown in brackets in the DOM tree")
    value: str = Field(description="Option value to select")


class UploadFileParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int = Field(description="ID of the file input element (or its labeled upload area / dropzone), shown in brackets in the DOM tree")
    path: str = Field(description="Path to the file to upload")


class WriteFileParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(description="File path to write to (parent directories are auto-created).")
    content: str = Field(description="Text content to write (UTF-8 by default; see encoding).")
    append: bool = Field(
        default=False,
        description="If True, append to the end of an existing file instead of overwriting it. "
        "Default False overwrites the entire file.",
    )
    trailing_newline: bool = Field(
        default=True,
        description="If True (default), ensure the written content ends with exactly one newline "
        "(no-op if it already does).",
    )
    leading_newline: bool = Field(
        default=False,
        description="If True, prepend a newline before the content (useful when appending to a "
        "file that lacks a trailing newline).",
    )
    encoding: str | None = Field(
        default=None,
        description="Text encoding to write with (default UTF-8). Set e.g. 'latin-1' or 'cp936' "
        "for legacy files; the byte-count echo reflects this encoding.",
    )
    newline: str | None = Field(
        default="",
        description="Python open() newline translation mode (default '' = no translation; "
        "\\n/\\r\\n written as-is). Set '\\r\\n' to force CRLF output, None to translate \\n "
        "to the OS native line ending (\\r\\n on Windows). Distinct from "
        "trailing_newline/leading_newline, which only add/remove a \\n in the content.",
    )


class ReadFileParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(description="Path to a local file to read: UTF-8 text by default (see encoding), or PDF/DOCX for text extraction.")
    encoding: str | None = Field(
        default=None,
        description="Text encoding to decode with (default UTF-8). Set e.g. 'latin-1' or 'cp936' "
        "for legacy files.",
    )
    newline: str | None = Field(
        default="",
        description="Python open() newline mode (default '' = no translation, preserves \\r\\n "
        "byte-for-byte). Set None for universal-newline (collapses \\r\\n / \\r to \\n); other "
        "values do not translate on a full-file read.",
    )
    offset: int = Field(
        default=0, ge=0,
        description="0-based character offset to start reading at (for paginating files larger "
        "than read_file_max_chars; pair with the truncation footer's 'use offset=N to continue').",
    )
    limit: int | None = Field(
        default=None, ge=1,
        description="Max characters to return from this read (default: read_file_max_chars). "
        "Use with offset to page through very large files.",
    )


class ReplaceFileParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(description="Path to an existing local file to edit in place.")
    old: str = Field(
        min_length=1,
        description="Text to find. A literal substring by default (case-sensitive); set "
        "regex=True to treat it as a Python regular expression. Must be non-empty.",
    )
    new: str = Field(
        description="Replacement text (literal; may be empty to delete matches). In regex mode "
        "this is an re.sub replacement template and supports backreferences (\\1, \\g<name>); "
        "escape backslashes for literal paths.",
    )
    encoding: str | None = Field(
        default=None,
        description="Text encoding to read/write with (default UTF-8). Set e.g. 'latin-1' or "
        "'cp936' for legacy files.",
    )
    newline: str | None = Field(
        default="",
        description="Python open() newline mode (default '' = no translation, preserves original "
        "line endings byte-for-byte). Set None for universal-newline translation on read.",
    )
    regex: bool = Field(
        default=False,
        description="When True, treat 'old' as a Python regular expression (re.sub semantics, "
        "including backreference expansion \\1 / \\g<name> in 'new'; escape backslashes for "
        "literal paths). When False (default), 'old' is a literal substring.",
    )
    case_sensitive: bool = Field(
        default=True,
        description="When True (default), match case-sensitively. When False, match "
        "case-insensitively regardless of regex mode. Note: defaults to True (unlike "
        "search_page's False) to preserve replace_file's historical case-sensitive behavior.",
    )
    count: int | None = Field(
        default=None,
        ge=1,
        description="Maximum number of occurrences to replace, from the top of the file. "
        "None (default) replaces all; a positive integer replaces only the first N "
        "(or fewer if the file has fewer matches).",
    )
    expected_count: int | None = Field(
        default=None,
        ge=0,
        description="If set, the file must contain exactly this many matches for the operation "
        "to proceed; on mismatch the file is left UNCHANGED and an error is returned (typo-guard "
        "against 0 or unexpectedly-many replacements). Compared against the TOTAL match count, "
        "before the 'count' limit is applied.",
    )
    backup: bool = Field(
        default=False,
        description="When True, copy the original (pre-edit) file to <path>.bak before replacing "
        "(shutil.copy2 metadata retained). Default False; .bak is overwritten if it exists.",
    )


class EvaluateParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(description=(
        "JavaScript to execute in the page. The code runs as a SCRIPT BODY — "
        "a top-level `return` is a SyntaxError unless wrapped, so use an IIFE: "
        "((function(){try{...}catch(e){return 'Error: '+e.message}})()). "
        "Contrast: with `args`/`elements` the code IS wrapped as "
        "function(...a){ ... } and then MUST `return`. Use ONLY browser APIs "
        "(document, window, fetch); NO Node.js APIs. Return a primitive or a "
        "JSON-serializable object/array. Keep output small."
    ))
    # ── 阶段二（二.B）：per-call 执行控制 ──
    await_promise: bool = Field(
        default=True,
        description=(
            "Await a returned Promise (default True; needed for await fetch(...)). "
            "Set False for fire-and-forget / strictly synchronous code."
        ),
    )
    timeout_ms: int | None = Field(
        default=None,
        ge=1,
        le=300000,
        description=(
            "Per-call CDP execution timeout in ms, clamped to [1, 300000]. Default None → "
            "30000 (project default). Larger for long fetches, smaller to fail fast. "
            "Only applies when no args/elements are given."
        ),
    )
    user_gesture: bool = Field(
        default=False,
        description=(
            "Run as a user gesture — required by some APIs (fullscreen, certain clipboard / "
            "pointer-lock calls). No-op for most code."
        ),
    )
    # ── 阶段二（二.C）：结构化参数注入（消除 f-string 注入面） ──
    args: list[Any] | None = Field(
        default=None,
        description=(
            "Optional JSON arguments injected as a[0], a[1], ... Your code is wrapped as "
            "function(...a){ ... } so it MUST `return` a value. Eliminates string-concat "
            "injection: pass values as JSON, reference them as a[i]. "
            "Example: args=['.btn'] with code `return document.querySelector(a[0]).disabled`."
        ),
    )
    # ── 阶段二（二.D）：元素句柄往返 ──
    elements: list[int] | None = Field(
        default=None,
        description=(
            "Backend node ids (index/element_id from get_state or find_elements(return_node_ids=True)) "
            "of elements to inject as handles e[0], e[1], ... When present, code is wrapped as "
            "function(...a, ...e){ ... } (JSON args first, element handles last) and MUST `return`. "
            "Lets JS act on the exact node click/input_text operate on, without re-querying. "
            "Example: elements=[42] with code `return e[0].value`."
        ),
    )
    return_element_ids: bool = Field(
        default=False,
        description=(
            "If True, a returned DOM node is resolved to its backend node id (usable as "
            "`index`/`element_id` for click/input_text) and reported. Expects the code to "
            "`return` a single element (e.g. `return document.querySelector('form')`). Only the "
            "first returned node is resolved; non-node returns fall back to normal normalization."
        ),
    )
    # ── 阶段二（二.E）：iframe 执行上下文 ──
    frame: int | None = Field(
        default=None,
        description=(
            "Backend node id of an iframe element to execute inside (cross-origin safe). "
            "Default None → top document. When set, the call runs in that iframe's context "
            "(attached via Target.attachToTarget). Use when the parent cannot reach a "
            "cross-origin iframe's document. Same-origin iframes do NOT need this — just "
            "reference `iframe.contentDocument` in your code."
        ),
    )
    # ── 阶段二（二.F）：图片 / base64 结果通道 ──
    extract_images: bool = Field(
        default=False,
        description=(
            "If True, scan the result text for `data:image/...;base64,...` URIs, collect them "
            "into ActionResult.metadata['images'], and replace each in the returned text with a "
            "short placeholder ([image 1], [image 2], ...) to avoid bloating context. Default False."
        ),
    )


class SearchPageParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(
        min_length=1,
        description="Text or regex pattern to search for within the current page",
    )
    regex: bool = Field(default=False, description="Treat query as a regex (default: literal text match).")
    case_sensitive: bool = Field(default=False, description="Case-sensitive match (default: case-insensitive).")
    context_chars: int = Field(default=150, ge=0, description="Characters of surrounding context per match.")
    css_scope: str | None = Field(
        default=None,
        description="CSS selector to limit search scope (e.g. 'div#main'). Selector not matching anything is an error.",
    )
    max_results: int = Field(
        default=25, ge=1, le=200,
        description="Maximum matches to return (total count is always reported even when truncated).",
    )
    offset: int = Field(
        default=0, ge=0,
        description="0-based index of the first match to return (for paginating large result sets; total is always the full count).",
    )
    search_attributes: bool = Field(
        default=False,
        description="Also search element attribute values (href / value / data-* etc). Returns a separate attribute_matches list; offset applies to text matches only.",
    )


class ReadGridParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    namespace: str | None = Field(
        default=None,
        description=(
            "Grid namespace, e.g. 'sales_order_grid' / 'product_listing'. Omit to "
            "auto-detect from the current page (first non-notification UI-component "
            "grid data source)."
        ),
    )
    filters: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Grid filters applied server-side, e.g. {'status':'complete'} or "
            "{'qty':{'from':2,'to':3}}. Replaces current filters (leftover bookmark "
            "filters are cleared first). Pass {} to read unfiltered."
        ),
    )
    search: str | None = Field(
        default=None,
        description="Fulltext keyword for the grid's search box (replaces current).",
    )
    sorting: str | None = Field(
        default=None,
        description=(
            "'<field> <asc|desc>', e.g. 'created_at desc'. REQUIRED for top-N / "
            "latest / max queries — grid row order is otherwise NOT guaranteed."
        ),
    )
    page_size: int = Field(
        default=200, ge=1, le=2000,
        description="Rows per page for this read (server-side paging; use 1000+ to read all).",
    )
    page: int = Field(default=1, ge=1, description="1-based page number to read.")
    fields: list[str] | None = Field(
        default=None,
        description=(
            "Row fields to return, e.g. ['entity_id','increment_id','created_at','status']. "
            "Omit for all grid columns."
        ),
    )
    fresh: bool = Field(
        default=True,
        description=(
            "True (default): clear leftover server-side bookmark filters/search before "
            "applying the given params — grids inherit filter state from previous "
            "sessions. False: apply on top of the current state."
        ),
    )


class DoneParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(
        min_length=1,
        description=(
            "Final message to the user. ONLY report data you directly observed in "
            "page state, tool outputs, or screenshots during this session. Do NOT "
            "use training knowledge to fill gaps — if information was not found on "
            "the page, say so explicitly. Do NOT claim completion of steps from "
            "compacted_memory or prior session summaries unless you explicitly "
            "verified them yourself. If uncertain whether a prior step completed, "
            "say so explicitly. Must be non-empty."
        ),
    )
    success: bool = Field(
        default=True,
        description=(
            "Whether the task was completed successfully. Set to False if any "
            "stated requirement was unmet, the page did not contain the expected "
            "data, or a step could not be verified. Leave True only when every "
            "requirement was directly confirmed this session."
        ),
    )
    files_to_display: list[str] = Field(
        default_factory=list,
        description=(
            "Absolute file paths to attach to the final result (downloads, saved "
            "reports, screenshots). Each must exist and be under an allowed read "
            "path; invalid paths are skipped with a warning. Shown as a short "
            "manifest in the summary."
        ),
    )


def make_structured_done_params(output_model: type[BaseModel]) -> type[BaseModel]:
    """Build the variant-B done param model (二.E 结构化输出).

    ``data: output_model`` is required; ``success``/``files_to_display`` are kept
    for the handler but hidden from the LLM schema by the registry (mirrors
    browser-use ``StructuredOutputAction[T]`` + ``_hide_internal_fields_from_schema``).
    """
    return create_model(
        "StructuredDoneParams",
        data=(output_model, Field(..., description="Structured final output.")),
        success=(bool, Field(default=True)),
        files_to_display=(list[str], Field(default_factory=list)),
        __config__=ConfigDict(extra="forbid"),
    )


# Mapping: action name → (param model, description, terminates_sequence)
ACTION_DEFINITIONS: dict[str, tuple[type[BaseModel], str, bool]] = {
    "navigate": (
        NavigateParams,
        "Navigate to a URL in the current tab, or open it in a new tab with new_tab=True",
        True,
    ),
    "click": (
        ClickParams,
        "Click an element by its ID from the DOM state. Use index (from the DOM tree) "
        "or element_id (a backend node id from find_elements with return_node_ids=True).",
        False,
    ),
    "input_text": (
        InputTextParams,
        "Type text into an input element identified by index (from the DOM tree) or "
        "element_id (a backend node id from find_elements with return_node_ids=True).",
        False,
    ),
    "scroll": (ScrollParams, "Scroll the page up or down by a number of increments", False),
    "search": (
        SearchParams,
        "Search the web via a search engine (baidu/google/bing/duckduckgo; default baidu). Navigates to the results page",
        True,
    ),
    "extract": (
        ExtractParams,
        "Extract specific information from the current page as clean markdown (via an LLM). "
        "Paginate large pages with start_from_char; dedupe across calls with already_collected.",
        False,
    ),
    "send_keys": (SendKeysParams, "Send keyboard shortcuts or key combinations", False),
    "switch_tab": (SwitchTabParams, "Switch to a different browser tab by tab ID", True),
    "close_tab": (CloseTabParams, "Close a browser tab", False),
    "wait": (WaitParams, "Wait for a specified number of seconds", False),
    "go_back": (GoBackParams, "Navigate back to the previous page in history", True),
    "find_elements": (
        FindElementsParams,
        "Query DOM elements by CSS selector (zero LLM cost, instant). Returns "
        "matching elements with tag, text, and attributes. Use "
        "attributes=['href','src'] to extract specific attributes (src/href "
        "resolve to absolute URLs). max_results caps the returned list; the total "
        "count is always reported. Use to explore page structure, count items, "
        "get links/attributes.",
        False,
    ),
    "find_text": (FindTextParams, "Scroll to and highlight the nth visible match of text on the page", False),
    "screenshot": (
        ScreenshotParams,
        "Take a screenshot with optional format, quality (jpeg), clip region, "
        "or full page. Saves to save_path if given.",
        False,
    ),
    "save_as_pdf": (
        SaveAsPdfParams,
        "Save the current page as a PDF. Supports paper_format (letter/legal/a4/a3/tabloid), "
        "landscape, scale (0.1-2.0), print_background.",
        False,
    ),
    "dropdown_options": (
        DropdownOptionsParams,
        "Get all options from a dropdown element: native <select>, role=combobox, "
        "role=listbox, or custom dropdown",
        False,
    ),
    "select_dropdown": (
        SelectDropdownParams,
        "Select an option in a dropdown element (native <select>, role=combobox, "
        "role=listbox, or custom dropdown). Pass the dropdown's index — do not "
        "click it first",
        False,
    ),
    "upload_file": (UploadFileParams, "Upload a file to a file input element. Do NOT click the input or an upload button first — upload_file sets the file directly without opening the OS file picker", False),
    "write_file": (
        WriteFileParams,
        "Write UTF-8 text to a local file (parent directories are "
        "auto-created). Default is overwrite: the file's previous content is "
        "fully replaced. Set append=True to add to the end of an existing "
        "file instead (it is created if missing). trailing_newline (default "
        "True) ensures the content ends with exactly one newline — no-op if "
        "it already does; set leading_newline=True only when appending to a "
        "file you know lacks a trailing newline, to separate the new content. "
        "Prefer replace_file for in-place edits to a small region of a large "
        "file you have already read.",
        False,
    ),
    "read_file": (ReadFileParams, "Read content from a local text (UTF-8) or PDF/DOCX file; paginate large files with offset/limit.", False),
    "replace_file": (
        ReplaceFileParams,
        "Replace occurrences of text inside an existing local file, in place. By default "
        "performs a case-sensitive literal substring replace of every non-overlapping "
        "occurrence (phase-1 behavior preserved). Set regex=True to treat 'old' as a Python "
        "regex (then 'new' supports backreferences), or case_sensitive=False for "
        "case-insensitive matching. count limits replacements to the first N (default: all). "
        "expected_count guards against typos: if the file does not contain exactly that many "
        "matches it is left unchanged and an error is returned. backup=True first copies the "
        "original to <path>.bak. old must be non-empty; zero matches returns 'no occurrences' "
        "rather than silently succeeding. Prefer this over write_file for small edits to a "
        "large file you have already read.",
        False,
    ),
    "evaluate": (
        EvaluateParams,
        "Execute arbitrary JavaScript in the page and return the result. Wrap in "
        "an IIFE with try-catch so errors become return values; use only browser "
        "APIs (no Node.js). Supports async (await / fetch). Result is normalized "
        "to a string (objects -> JSON). Escape hatch when find_elements / "
        "search_page / click cannot express the need. Avoid backslash escapes in "
        "the code (they get mangled in transport): for newlines/tabs inside regex "
        "use String.fromCharCode(10)/String.fromCharCode(9) instead of \\n/\\t.",
        True,
    ),
    "search_page": (
        SearchPageParams,
        (
            "Search page text for a pattern (like grep). Zero LLM cost, instant. "
            "Returns matches with surrounding context, element path, and a total count; "
            "paginate large result sets with offset. "
            "Traverses same-origin iframes and open shadow roots. "
            "Set regex=True for regex patterns; use css_scope to search within a section; "
            "search_attributes=True to also match href/value/etc. "
            "Read-only — does not scroll or highlight (use find_text for that)."
        ),
        False,
    ),
    "read_grid": (
        ReadGridParams,
        (
            "Read structured rows from the page's data grid (KO/UI-component grids, "
            "legacy grids, or plain HTML tables) — bypasses row-render freezes and "
            "returns JSON rows plus metadata (total_records, sorting, active filters). "
            "Pass sorting='field desc' for top-N/latest queries — never assume row "
            "order. Read-only data channel: does NOT update the page UI (filter chips) "
            "— tasks graded on visible filter state must use the Filters panel."
        ),
        True,
    ),
    "done": (
        DoneParams,
        "Signal that the task is complete and stop the agent. Must be the only action "
        "in the step. Provide a final summary of what was accomplished; set success=False "
        "if any requirement was unmet or could not be verified.",
        False,
    ),
}
