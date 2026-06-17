# search 工具完善方案：多引擎 + URL 编码

> 参照 browser-use（`browser_use/tools/service.py:442-479`）完善本项目的 `search` 动作。
> 相关文档：`docs/Tools技术细节/04_动作清单与CDP映射.md` 的 4.17 节（本项目现状）、`browser-use/docs/Tools技术细节/05-动作详解-浏览器交互.md` 的 1. search 节（参考标杆）。

## 背景（为什么改）

当前 TreeWalker 的 `search` 动作存在两个明确缺陷：

1. **不做 URL 编码** —— `_action_search`（`src/tree_walker/tools/actions.py:233-237`）直接 `f"https://www.google.com/search?q={query}"` 拼接。query 含空格、中文、`&`、`#` 时会让 URL 结构错乱（例如 query=`x&udm=14` 会注入多余参数）。这是 bug，不是风格问题。
2. **硬编码 Google** —— 无法切换搜索引擎。

参照 browser-use 的实现：用 `urllib.parse.quote_plus` 编码、支持多引擎、Google 用 `&udm=14` 屏蔽 AI Overview 拿纯结果页、返回 `extracted_content` 记录搜索动作。

**已确认的决策：**
- 支持 4 引擎：`baidu / google / bing / duckduckgo`
- **默认引擎 = baidu**（国内免翻墙、中文搜索质量好）
- 不引入全局环境变量配置，`engine` 仅作为工具参数（带默认值）

预期结果：search 支持按需切换 4 个引擎、URL 安全编码、向后兼容（旧调用只传 query 仍工作）、LLM 通过 schema enum 自发现引擎选项。

---

## 改动文件（共 3 个）

### 1. `src/tree_walker/tools/models.py`

**(a) 扩展 `SearchParams`（第 29-31 行）** —— 增加 `engine` 字段，用 `Literal` 枚举（比 browser-use 的 `str` 更严格，Pydantic 校验阶段即拒绝非法引擎）：

```python
class SearchParams(BaseModel):
	model_config = ConfigDict(extra="forbid")
	query: str = Field(description="Search query to type into the search engine")
	engine: Literal["baidu", "google", "bing", "duckduckgo"] = Field(
		default="baidu",
		description="Search engine: baidu (default, works in China), google, bing, or duckduckgo",
	)
```

- `Literal` 已在文件第 1 行导入（`from typing import Literal`），无需新增 import。
- 沿用 `ScrollParams.direction: Literal[...]`（第 26 行）的先例。
- **engine 必须带 description**：项目渲染器 `registry.py` 的 `get_action_descriptions_text` 在字段无 description 时会降级显示 `engine: any`，写明 description 才能让 LLM 在系统提示词里看到引擎说明。
- 向后兼容：`engine` 有默认值，旧调用 `{"query": "x"}` 仍通过校验并落到 `baidu`。

**(b) 微调 ACTION_DEFINITIONS 中 search 的 description**（约第 149 行），让顶层描述提示引擎可选：

```python
"search": (
	SearchParams,
	"Search the web via a search engine (baidu/google/bing/duckduckgo; default baidu). Navigates to the results page",
	True,
),
```

`terminates_sequence=True` 不变（搜索会切换页面，必须终止 multi_act 链中后续动作，与 navigate/switch_tab/go_back 一致）。

---

### 2. `src/tree_walker/tools/actions.py`

**(a) 新增模块级常量**（放在 `class Tools:` 之前，约第 108 行，与现有模块级 helpers 同区）：

```python
# ── Search engines ──────────────────────────────────────────────────

_SEARCH_ENGINE_URLS: dict[str, str] = {
	"baidu": "https://www.baidu.com/s?wd={query}",
	"google": "https://www.google.com/search?q={query}&udm=14",
	"bing": "https://www.bing.com/search?q={query}",
	"duckduckgo": "https://duckduckgo.com/?q={query}",
}
```

- **baidu** 用 `wd` 参数；**google** 加 `&udm=14`（强制纯网页结果、屏蔽 AI Overview）；bing/duckduckgo 用标准 `q`。
- 放模块级（而非内联）便于测试 import 参数化。下划线前缀表示内部约定，测试仍可触及（与现有 `_find_file_input_near_element` 等 helpers 同做法）。

**(b) 重写 `_action_search`（第 233-237 行）**：

```python
async def _action_search(self, params: dict, browser: BrowserSession) -> ActionResult:
	import urllib.parse

	query = params["query"]
	engine = params.get("engine", "baidu")
	encoded_query = urllib.parse.quote_plus(query)
	url = _SEARCH_ENGINE_URLS[engine].format(query=encoded_query)
	await browser.navigate(url)
	memory = f"Searched {engine.title()} for '{query}'"
	return ActionResult(extracted_content=memory, long_term_memory=memory)
```

- `urllib.parse.quote_plus`：空格转 `+`（不是 `%20`），中文转 UTF-8 百分号编码，`&`/`#` 转义 —— 彻底解决注入与拼接问题。
- `engine = params.get("engine", "baidu")`：用 `.get` 兜底直接调用路径（测试/程序化调用可能传不带 engine 的扁平 dict），默认值与模型 `default="baidu"` 对齐。Agent 正常路径里 Pydantic 已填充默认值。
- **无需运行时 engine 校验分支**（与 browser-use 的 `if engine not in ...` 不同）：Agent 路径下 `SearchParams.model_validate()`（`step.py:472-477`）已用 Literal 拒绝非法引擎；直接调用路径下非法 engine 触发 `KeyError`，被 `Tools.execute` 的 `except Exception`（`actions.py:144-146`）捕获并返回 `ActionResult(error=...)`。这与 `_action_click`/`_action_navigate` 直接 `params["index"]`/`params["url"]` 不做防御的风格一致。
- 复用 `browser.navigate(url)`，它内部已做清缓存 + `Page.navigate` + `_wait_for_page_settle()`（`session.py:382-389`），无需在 search 里重复等待逻辑。
- 返回 `extracted_content`（进每步 LLM 上下文）+ `long_term_memory`（进跨步压缩记忆），遵循 browser-use 模式。不传 `success=True`（`ActionResult` 的 `model_validator` 对非 done 动作拒绝）。
- `import urllib.parse` 函数内 lazy import，参照 `_action_extract` 第 248 行的 `from tree_walker.llm.client import LLMClient` 先例（也可提到模块顶部，二选一）。

---

### 3. `tests/test_search_engines.py`（新建）

当前 search 无任何真实单元测试（仅 `test_multi_act.py:615` 测 terminates 守卫，用 fake tools 不跑真 handler）。新建测试文件，参照 `test_input_text_clear.py:19-38` 的 mock 模式。

**Mock 边界**：`_action_search` 只调 `browser.navigate(url)`，所以直接 mock 整个 `browser.navigate = AsyncMock()`、断言 `call_args[0][0]` 拿到的 URL，是最干净的隔离（不需 mock CDP 原语）。

**测试用例清单**（分组）：

| 组 | 用例 | 断言要点 |
|---|---|---|
| URL 构建 | 默认引擎是 baidu | `https://www.baidu.com/s?wd=python`，extracted 含 "Baidu" |
| | google 带有 `&udm=14` | `https://www.google.com/search?q=cats&udm=14` |
| | bing / duckduckgo URL 正确 | 各自模板 |
| URL 编码 | 空格转 `+` 非 `%20` | query="a b" → 含 `a+b`，不含 `%20` |
| | 中文 UTF-8 百分号编码 | query="你好" → 含 `%E4%BD%A0%E5%A5%BD` |
| | 特殊字符转义（注入防护） | query="a&b=c#d" → baidu 默认下含 `wd=a%26b%3Dc%23d`，URL 结构不被破坏 |
| 向后兼容 | 只传 query 不传 engine | 仍工作、默认百度、无 error |
| 结果形态 | 返回 extracted_content + long_term_memory | 无 `success=True`、无 `is_done` |
| | memory 字符串格式 | google+"cats" → `Searched Google for 'cats'` |
| Pydantic 校验 | 非法 engine 被 Literal 拒绝 | `SearchParams.model_validate({..."engine":"yahoo"})` 抛 `ValidationError` |
| | extra 字段被拒 | `{"query":"x","foo":1}` 抛 ValidationError（extra="forbid"） |
| | JSON schema 含 enum+default | `model_json_schema()["properties"]["engine"]` 的 `enum`/`default` 正确 |
| 直接调用错误路径 | 直接传非法 engine 返回 error 不抛 | `tools.execute("search", {"query":"x","engine":"yahoo"}, browser)` → `.error` 非空，且 `navigate` 未被调用 |

代表性骨架（端到端走真实调度器 `Tools().execute`）：

```python
"""Tests for the multi-engine search action (baidu/google/bing/duckduckgo)."""
from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock
import pytest
from pydantic import ValidationError
from tree_walker.tools.actions import Tools, _SEARCH_ENGINE_URLS
from tree_walker.tools.models import SearchParams


def _make_browser():
	bs = MagicMock()
	bs.navigate = AsyncMock()
	return bs


class TestSearchUrlBuilding:
	@pytest.mark.asyncio
	async def test_default_engine_is_baidu(self):
		tools = Tools()
		browser = _make_browser()
		result = await tools.execute("search", {"query": "python"}, browser)
		browser.navigate.assert_awaited_once()
		assert browser.navigate.call_args[0][0] == "https://www.baidu.com/s?wd=python"
		assert result.error is None
		assert "Baidu" in result.extracted_content

	@pytest.mark.asyncio
	async def test_google_has_udm14(self):
		tools, browser = Tools(), _make_browser()
		await tools.execute("search", {"query": "cats", "engine": "google"}, browser)
		assert browser.navigate.call_args[0][0] == "https://www.google.com/search?q=cats&udm=14"
	# ... bing / duckduckgo 同理


class TestSearchUrlEncoding:
	@pytest.mark.asyncio
	async def test_spaces_become_plus(self):
		tools, browser = Tools(), _make_browser()
		await tools.execute("search", {"query": "a b"}, browser)
		url = browser.navigate.call_args[0][0]
		assert "a+b" in url and "%20" not in url

	@pytest.mark.asyncio
	async def test_chinese_encoded(self):
		tools, browser = Tools(), _make_browser()
		await tools.execute("search", {"query": "你好"}, browser)
		assert "%E4%BD%A0%E5%A5%BD" in browser.navigate.call_args[0][0]


class TestSearchEngineValidation:
	def test_invalid_engine_rejected(self):
		with pytest.raises(ValidationError):
			SearchParams.model_validate({"query": "x", "engine": "yahoo"})

	def test_schema_has_enum_and_default(self):
		eng = SearchParams.model_json_schema()["properties"]["engine"]
		assert eng["enum"] == ["baidu", "google", "bing", "duckduckgo"]
		assert eng["default"] == "baidu"
```

---

## 技术决策说明（要点）

- **engine 用 Literal 而非 str**：browser-use 用 `str` 是缺陷（运行时才校验）。Literal 让 Pydantic 在 `step.py` 校验阶段就拒绝非法引擎，handler 无需 `if engine not in ...` 分支，且 enum 自动流入 LLM tool schema。
- **quote_plus 而非 quote**：空格转 `+` 符合搜索引擎对 query 参数的惯例，`%20` 在某些引擎解析异常。
- **不加运行时 engine 校验**：遵循项目既有风格（`_action_click`/`_action_navigate` 都直接取 params 不防御），非法值由 Pydantic + `Tools.execute` 的 except 兜底。
- **不加全局环境变量**：保持简单，engine 作为工具参数已满足按需切换。

## 已知限制（本次不处理，留作未来选项）

- **结果页异步渲染**：`_wait_for_page_settle()` 靠 `readyState=complete` 判定，可能在搜索结果 XHR 渲染完之前就返回。这是 navigate/go_back 共有的既有行为，下一步 `get_state()` 会捕获最终 DOM，无需特殊处理。
- **空 query**：`query: str` 无 `min_length` 约束，空串会导航到空结果页（不崩溃）。未来可加 `Field(min_length=1, ...)`。
- **duckduckgo/bing 可能遇验证码**：高频自动化可能触发。baidu 在国内 IP 表现最稳，故设为默认。

---

## 验证步骤

1. **跑单元测试**（CLAUDE.md 要求改动后必须跑）：
   ```powershell
   uv run python -m pytest tests/test_search_engines.py -x -v
   ```
2. **回归现有 search 相关测试**（确保 terminates 守卫未破坏）：
   ```powershell
   uv run python -m pytest tests/test_multi_act.py -x -v
   ```
3. **全量测试 + 覆盖率**（项目目标 >85%）：
   ```powershell
   uv run python -m pytest tests/ -x -v
   uv run python -m pytest tests/ --cov=tree_walker.tools --cov-report=term-missing
   ```
4. **手动验证 URL 编码正确性**（可选，确认 quote_plus 行为）：
   ```powershell
   uv run python -c "import urllib.parse; print(urllib.parse.quote_plus('你好 a&b'))"
   ```
   预期输出：`%E4%BD%A0%E5%A5%BD+a%26b`
