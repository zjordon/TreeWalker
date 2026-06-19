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
    amount: int = Field(default=3, description="Number of scroll increments (page heights)")
    direction: Literal["up", "down"] = Field(default="down")


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
    keys: str = Field(description="Key combination to send, e.g. 'Enter', 'Control+a', 'Tab'")


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
    selector: str = Field(description="CSS selector to find elements on the page")


class FindTextParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(description="Text to search for on the page")


class ScreenshotParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    save_path: str = Field(default="", description="Optional file path to save the screenshot")


class SaveAsPdfParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(description="File path to save the PDF")


class DropdownOptionsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int = Field(description="ID of the select element, shown in brackets in the DOM tree")


class SelectDropdownParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int = Field(description="ID of the select element, shown in brackets in the DOM tree")
    value: str = Field(description="Option value to select")


class UploadFileParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int = Field(description="ID of the file input element, shown in brackets in the DOM tree")
    path: str = Field(description="Path to the file to upload")


class WriteFileParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(description="File path to write to")
    content: str = Field(description="Content to write")


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
    code: str = Field(description="JavaScript code to execute in the browser")


class SearchPageParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(description="Text to search for within the current page")


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
        "Find elements on the page using a CSS selector",
        False,
    ),
    "find_text": (FindTextParams, "Scroll to and highlight text on the page", False),
    "screenshot": (
        ScreenshotParams,
        "Take a screenshot of the current viewport",
        False,
    ),
    "save_as_pdf": (SaveAsPdfParams, "Save the current page as a PDF file", False),
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
    "upload_file": (UploadFileParams, "Upload a file to a file input element", False),
    "write_file": (WriteFileParams, "Write content to a local file", False),
    "read_file": (ReadFileParams, "Read content from a local file", False),
    "replace_file": (ReplaceFileParams, "Replace text within a local file", False),
    "evaluate": (
        EvaluateParams,
        "Execute JavaScript code in the browser and return the result",
        True,
    ),
    "search_page": (
        SearchPageParams,
        "Search for text within the current page content",
        False,
    ),
    "done": (DoneParams, "Signal that the task is complete with a summary", False),
}
