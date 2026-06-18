# navigate 工具完善方案：导航可靠性 + new_tab + 健康检查

> 参照 browser-use（`browser_use/tools/service.py:481-560` + `browser_use/browser/session.py:868-1079`）完善本项目的 `navigate` 动作。
> 相关文档：`docs/Tools技术细节/04_动作清单与CDP映射.md` 的 4.11 节（本项目现状）、`browser-use/docs/Tools技术细节/05-动作详解-浏览器交互.md` 的 2. navigate 节（参考标杆）。

## 背景（为什么改）

当前 TreeWalker 的 `navigate`（`src/tree_walker/tools/actions.py:206-211` + `src/tree_walker/browser/session.py:382-389`）存在三类问题：

1. **静默失败（严重正确性 bug）**：`Page.navigate` 的返回值 `NavigateReturns`（cdp-use `page/commands.py:238-247`）含 `errorText` 字段（CDP 文档："present if and only if navigation has failed"）。DNS 解析失败、连接被拒、超时、隧道失败等场景以 `errorText` 返回而非抛异常。当前实现完全丢弃返回值，agent 误以为导航成功，下一步 `get_state` 拿到旧/空页面。
2. **无网络错误友好提示**：即使失败（走 `Tools.execute` 通用兜底 `ActionResult(error=str(e))`），错误信息对 LLM 无指导意义，无法区分"域名错了"vs"网站挂了"。
3. **无 new_tab / 健康检查**：browser-use 支持 new_tab（本项目已有 `BrowserSession.create_tab` `session.py:1012-1017` 可复用），并在导航后检测空 DOM（SPA 未渲染 / 反爬 / 连接问题）自动重试 + reload。

附带两个问题：`navigate`（session.py:384）只清 `_previous_cached_selector_map`、不清 `_cached_selector_map`（与 `switch_tab` session.py:990-991 行为不一致，导航后旧 selector_map 残留，`_get_element_by_index` actions.py:166 可能命中过期元素）；`transitionType` 未设置（browser-use 用 `address_bar` 让历史/过渡语义正确）。

**已确认的决策（用户已选）：**
- 健康检查用**完整 get_state 对齐 browser-use**（接受每次导航多一次 DOM 采集的开销，换取与 browser-use 一致的 `_root is None / element_tree_text` 判定）。
- **增加 new_tab 参数**（复用 `BrowserSession.create_tab`：先开 about:blank 再 `Page.navigate` 以保留 errorText 检查）。
- errorText 检查 + 网络错误映射、`transitionType`、缓存修复、文档同步 一并纳入。

预期结果：navigate 能正确感知导航失败（不再静默）、对网络错误给出可操作提示、可在新标签页打开、对 SPA 未渲染/反爬空页自动重试重载，缓存与过渡语义修正，文档同步更新。

---

## 改动文件（共 4 个）

### 1. `src/tree_walker/tools/models.py`

**(a) 给 `NavigateParams`（第 6-9 行）增加 `new_tab` 字段**：

```python
class NavigateParams(BaseModel):
	model_config = ConfigDict(extra="forbid")
	url: str = Field(description="The URL to navigate to")
	new_tab: bool = Field(
		default=False,
		description="If True, open the URL in a new tab instead of navigating the current tab",
	)
```

- 向后兼容：`new_tab` 有默认值，旧调用 `{"url": "..."}` 仍通过校验并落到 `False`。
- `description` 必填：渲染器 `registry.py` 的 `get_action_descriptions_text` 在字段无 description 时降级显示 `new_tab: any`，写明 description 才能让 LLM 在系统提示词里看到选项（参照 `SearchParams.engine` 的处理）。

**(b) 微调 ACTION_DEFINITIONS 中 navigate 的 description（第 143 行）**：

```python
"navigate": (
	NavigateParams,
	"Navigate to a URL in the current tab, or open it in a new tab with new_tab=True",
	True,
),
```

`terminates_sequence=True` 不变。

> 检查 `tests/test_update_action_models.py` 是否断言 NavigateParams 字段集，若有则同步更新。

---

### 2. `src/tree_walker/browser/session.py`

**增强 `navigate`（第 382-389 行）**：增加 `new_tab` 参数、读取 errorText、设置 transitionType、修正缓存清理。

```python
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
```

- **`transitionType: "address_bar"`** 对齐 browser-use，让浏览器历史/过渡按"地址栏输入"处理。
- **errorText 检查**：cdp-use 在 `Page.navigate` 协议级错误时（`data["error"]`）抛 `RuntimeError`（`cdp_use/client.py:317-321`）；而导航"软失败"（DNS/连接）走 `result["errorText"]`，此前被吞，现在显式抛出。
- **缓存修复**：新增 `self._cached_selector_map = None`，与 `switch_tab`（session.py:990-991）一致。
- **new_tab 两步走**：`create_tab("about:blank")` 内部 `Target.createTarget` + `switch_tab`，随后 `Page.navigate` 到真实 URL。两步是为了拿到 errorText + 走统一 settle（与 browser-use `_cdp_create_new_page('about:blank')` 后再 navigate 一致）。若直接 `create_tab(real_url)` 会丢 errorText（`Target.createTarget` 不返回导航错误）。

---

### 3. `src/tree_walker/tools/actions.py`

**(a) 新增模块级常量**（放在 `class Tools:` 之前，约第 108 行，与 `_SEARCH_ENGINE_URLS` 同区）：

```python
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
```

**(b) 重写 `_action_navigate`（第 206-211 行）**：

```python
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
```

**(c) 新增健康检查与错误映射 helper（紧随 `_action_navigate`）**：

```python
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
```

- **健康检查只对 http/https + 当前标签页触发**：`chrome://`、`about:`、`new_tab=True` 跳过（与 browser-use 一致）。
- **三阶段判定收严**：`_root is None 或 element_tree_text 为空` → 同上 → 仅 `_root is None`。
- **reload 用 `browser.navigate(url)`**（browser-use 是重新派发 `NavigateToUrlEvent`，本项目等价为重新调 navigate）。reload 异常被吞（等价 browser-use 的 `raise_if_any=False`）。
- **错误映射在 action 内做**：不依赖 `Tools.execute`（actions.py:154-156）的通用 `str(e)` 兜底，因为通用兜底对 LLM 无指导意义。映射覆盖 browser-use 全部 5 个错误码 + `net::` 兜底。
- **返回 `extracted_content` + `long_term_memory`**：回显 URL 让 LLM 确认导航结果（参照 browser-use 与本项目 search 优化）；不传 `success=True`（`ActionResult` 的 `validate_success_requires_done` 校验器 views.py:18-25 对非 done 动作拒绝）。

---

### 4. `tests/test_navigate.py`（新建）

参照 `tests/test_search_engines.py` 的 mock 模式（mock 整个 `browser.navigate` / `browser.get_state`，不碰 CDP 原语）。当前 navigate 无真实单元测试（仅 `test_multi_act.py:594-613` 测 terminates 守卫，用 fake tools 不跑真 handler）。

**测试用例清单**：

| 组 | 用例 | 断言要点 |
|---|---|---|
| errorText / 错误映射 | navigate 抛 `net::ERR_NAME_NOT_RESOLVED` | action 返回 error 含 "site unavailable"；get_state 未被调用 |
| | `ERR_CONNECTION_REFUSED` / `ERR_TIMED_OUT` / `ERR_TUNNEL_CONNECTION_FAILED` 各自映射 | 同上 |
| | 非网络错误（`RuntimeError("boom")`） | error 含 "Navigation failed: boom"，不含 "site unavailable" |
| new_tab | `new_tab=False`（默认） | `browser.navigate(url, new_tab=False)` |
| | `new_tab=True` | `browser.navigate(url, new_tab=True)`；跳过健康检查（get_state 不被调用） |
| | ActionResult 文案区分 | True → "Opened new tab"；False → "Navigated to" |
| URL 补全 | 无协议 url | 自动补 `https://`；已有 https 不重复补 |
| 健康检查-正常页 | get_state 返回非空 | 不重试、不 reload、返回成功 + extracted |
| 健康检查-空页重试 | 首次空、3s 后非空 | 仅 sleep 后一次 get_state，最终成功 |
| 健康检查-空页重载 | 首次空、重试仍空、reload 后非空 | `browser.navigate` 二次被调用，最终成功 |
| 健康检查-最终失败 | 三阶段后仍 `_root is None` | error 含 "empty content" |
| 健康检查-非 http 跳过 | url 为 chrome:// | 即使空也不触发健康检查 |
| Pydantic | `new_tab` 默认 False、extra 禁止 | `NavigateParams.model_validate` |
| | schema 含 new_tab 默认值 | `model_json_schema()["properties"]["new_tab"]["default"] is False` |

代表性骨架：

```python
"""Tests for navigate: errorText mapping, new_tab, health check."""
from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock
import pytest
from pydantic import ValidationError
from tree_walker.tools.actions import Tools
from tree_walker.tools.models import NavigateParams
from tree_walker.browser.views import BrowserStateSummary, SerializedDOMState


def _state(empty: bool) -> BrowserStateSummary:
	ds = SerializedDOMState(
		_root=None, selector_map={},
		element_tree_text="" if empty else "<body>content</body>",
	)
	return BrowserStateSummary(url="https://example.com", title="", dom_state=ds)


def _browser(navigate_side_effect=None, states=None):
	bs = MagicMock()
	bs.navigate = AsyncMock(side_effect=navigate_side_effect) if navigate_side_effect else AsyncMock()
	queue = list(states or [])
	async def fake_get_state(include_screenshot=True):
		return queue.pop(0) if queue else _state(empty=False)
	bs.get_state = fake_get_state
	return bs


class TestNavigateErrorMapping:
	@pytest.mark.asyncio
	async def test_dns_error_maps_to_site_unavailable(self):
		bs = _browser(navigate_side_effect=RuntimeError("Navigation failed: net::ERR_NAME_NOT_RESOLVED"))
		result = await Tools().execute("navigate", {"url": "x.invalid"}, bs)
		assert "site unavailable" in result.error


class TestNavigateNewTab:
	@pytest.mark.asyncio
	async def test_new_tab_skips_health_check(self):
		bs = _browser(states=[_state(empty=True)])  # 即便空也不应触发
		result = await Tools().execute("navigate", {"url": "https://x.com", "new_tab": True}, bs)
		bs.navigate.assert_awaited_once_with("https://x.com", new_tab=True)
		assert "Opened new tab" in result.extracted_content


class TestNavigateHealthCheck:
	@pytest.mark.asyncio
	async def test_empty_then_reload_then_success(self):
		bs = _browser(states=[_state(True), _state(True), _state(False)])
		result = await Tools().execute("navigate", {"url": "https://x.com"}, bs)
		assert result.error is None
		assert bs.navigate.await_count == 2  # 初次 + reload


class TestNavigateParams:
	def test_new_tab_default_false(self):
		assert NavigateParams.model_validate({"url": "x"}).new_tab is False

	def test_extra_forbidden(self):
		with pytest.raises(ValidationError):
			NavigateParams.model_validate({"url": "x", "foo": 1})
```

> 加速：健康检查的 `asyncio.sleep(3.0/5.0)` 在测试里 monkeypatch `tree_walker.tools.actions.asyncio.sleep` 为 `AsyncMock()`，或将 `_NAVIGATE_EMPTY_RETRY_WAIT` / `_NAVIGATE_EMPTY_RELOAD_WAIT` patch 为 0。

---

## 附带文档同步（建议本次一并做）

更新 `docs/Tools技术细节/04_动作清单与CDP映射.md` 的 4.11 navigate 节：
- Pydantic 参数表加 `new_tab`；
- 主要逻辑改为新实现（errorText 检查、健康检查三阶段、new_tab、错误映射）；
- CDP 调用清单加 `transitionType: address_bar`；
- 注意事项修正「导航后硬编码 `sleep(0.5)`」为 `_wait_for_page_settle()`（该描述已与实现脱节，第 399 行 go_back 的 `sleep(0.3)` 同样需修正）。

---

## 技术决策说明（要点）

- **健康检查用完整 get_state（用户已选）**：判定标准与 browser-use 完全一致（`dom_state._root is None 或 element_tree_text 为空`），代价是每次导航多一次 DOM 采集（约 50-200ms+）。如未来发现开销过大，可降级为轻量 `execute_js("document.body.innerText.length")` 探针——见「已知限制」。
- **errorText 来自 `Page.navigate` 返回值而非异常**：cdp-use 在协议级错误抛 `RuntimeError`，但 DNS/连接等"软失败"以 `result["errorText"]` 返回。两层都覆盖：errorText 显式检查 + 异常进 `_map_navigation_error`。
- **session 层抛异常、action 层做映射**：`BrowserSession.navigate` 成功返回 / 失败抛 `RuntimeError`（带 errorText），`_action_navigate` 的 `_map_navigation_error` 转成对 LLM 友好的 `ActionResult.error`。session 层保持纯粹，action 层负责策略。
- **new_tab 两步走（about:blank → Page.navigate）**：直接 `create_tab(real_url)` 会丢 errorText 检查；先 about:blank 再 navigate 既保留 errorText 又复用 settle，与 browser-use 一致。
- **缓存修复**：`navigate` 新增 `self._cached_selector_map = None`，与 `switch_tab` 对齐。
- **健康检查 reload 不抛异常**：等价 browser-use `raise_if_any=False`，避免二次失败中断"初次导航已成功"的外层路径。
- **返回 extracted_content 回显 URL**：让 LLM 从 result 确认导航目的地（此前返回空 ActionResult）。

## 已知限制（本次不处理，留作未来选项）

- **健康检查额外 DOM 采集开销**：每次导航多一次 get_state。如成瓶颈，可换轻量 `execute_js` 探针。
- **健康检查最坏耗时**：3s（重试）+ 导航 + 5s（重载）≈ 10s+，仅发生在确实渲染异常的页面。可 patch `_NAVIGATE_EMPTY_RETRY_WAIT` / `_NAVIGATE_EMPTY_RELOAD_WAIT` 调小，或未来提到 `BrowserSettings`。
- **Page.navigate 生命周期等待**：本项目用 `_wait_for_page_settle()`（轮询 readyState），browser-use 用 `Page.lifecycleEvent`（networkIdle/load/DOMContentLoaded）。前者对 SPA 异步渲染不如后者精准，但健康检查三阶段重试已覆盖主要的"未渲染"场景。如需更精准可后续引入 lifecycle 事件监听。
- **`_wait_for_page_settle` 超时静默**：未改动其行为（超时不抛、不返回状态），仅健康检查在上层补判。
- **new_tab 不复用已有 about:blank 标签页**：browser-use 优先复用现有空白 tab 避免堆积；本项目 `create_tab` 每次新建。如标签页堆积成问题可后续加复用逻辑。

---

## 验证步骤

1. **跑新增单元测试**（CLAUDE.md 要求改动后必须跑）：
   ```powershell
   uv run python -m pytest tests/test_navigate.py -x -v
   ```
2. **回归 navigate 相关既有测试**（terminates 守卫、_wait_for_page_settle、schema 完整性）：
   ```powershell
   uv run python -m pytest tests/test_multi_act.py tests/test_update_action_models.py -x -v
   ```
3. **全量测试 + 覆盖率**（项目目标 >85%）：
   ```powershell
   uv run python -m pytest tests/ -x -v
   uv run python -m pytest tests/ --cov=tree_walker.tools --cov=tree_walker.browser --cov-report=term-missing
   ```
4. **手动验证 errorText 路径**（可选，连真实浏览器）：
   ```powershell
   uv run python -c "import asyncio; from tree_walker.browser.session import BrowserSession; s=BrowserSession(ws_url='http://localhost:9222'); asyncio.run(s.start()); print(asyncio.run(s.navigate('https://this-domain-does-not-exist.invalid')))"
   ```
   预期：抛 `RuntimeError: Navigation failed: net::ERR_NAME_NOT_RESOLVED`（此前静默返回 None）。
