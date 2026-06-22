from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class NavigateParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(description="The URL to navigate to")
    new_tab: bool = Field(
        default=False,
        description="If True, open the URL in a new tab instead of navigating the current tab",
    )


class ClickParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int = Field(description="ID of the element to click, shown in brackets in the DOM tree")


class InputTextParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int = Field(description="ID of the element to type into, shown in brackets in the DOM tree")
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
    goal: str = Field(description="What information to extract from the current page")


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
    include_text: bool = Field(default=True, description="Include text content of each element")


class FindTextParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, description="Text to search for on the page")


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
    content: str = Field(description="Text content to write (UTF-8).")
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


class ReadFileParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(description="File path to read")


class ReplaceFileParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(description="File path")
    old: str = Field(description="Text to find and replace")
    new: str = Field(description="Replacement text")


class EvaluateParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(description=(
        "JavaScript to execute in the page. Best practice: wrap in an IIFE "
        "((function(){try{...}catch(e){return 'Error: '+e.message}})()) so a "
        "thrown error becomes a return value, not a tool failure. Use ONLY "
        "browser APIs (document, window, fetch); NO Node.js APIs. Return a "
        "primitive or a JSON-serializable object/array. Keep output small."
    ))


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


class DoneParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(description=(
        "Final summary. ONLY report data you directly observed in page state, "
        "tool outputs, or screenshots during this session."
    ))
    success: bool = Field(default=True, description="Whether the task was completed successfully")


# Mapping: action name → (param model, description, terminates_sequence)
ACTION_DEFINITIONS: dict[str, tuple[type[BaseModel], str, bool]] = {
    "navigate": (
        NavigateParams,
        "Navigate to a URL in the current tab, or open it in a new tab with new_tab=True",
        True,
    ),
    "click": (ClickParams, "Click an element by its ID from the DOM state", False),
    "input_text": (
        InputTextParams,
        "Type text into an input element identified by ID",
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
        "Extract specific information from the current page content",
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
    "find_text": (FindTextParams, "Scroll to and highlight text on the page", False),
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
        "Get all options from a select dropdown element",
        False,
    ),
    "select_dropdown": (
        SelectDropdownParams,
        "Select an option in a dropdown element",
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
    "read_file": (ReadFileParams, "Read content from a local file", False),
    "replace_file": (ReplaceFileParams, "Replace text within a local file", False),
    "evaluate": (
        EvaluateParams,
        "Execute arbitrary JavaScript in the page and return the result. Wrap in "
        "an IIFE with try-catch so errors become return values; use only browser "
        "APIs (no Node.js). Supports async (await / fetch). Result is normalized "
        "to a string (objects -> JSON). Escape hatch when find_elements / "
        "search_page / click cannot express the need.",
        True,
    ),
    "search_page": (
        SearchPageParams,
        (
            "Search page text for a pattern (like grep). Zero LLM cost, instant. "
            "Returns matches with surrounding context, element path, and a total count. "
            "Set regex=True for regex patterns; use css_scope to search within a section. "
            "Read-only — does not scroll or highlight (use find_text for that)."
        ),
        False,
    ),
    "done": (DoneParams, "Signal that the task is complete with a summary", False),
}
