# go_back 工具完善方案：缓存修正 + 健康检查 + 结果回显

> 参照 browser-use（`browser_use/tools/service.py:562-574` + `browser_use/browser/watchdogs/default_action_watchdog.py:2334-2362`）完善本项目的 `go_back` 动作。
> 相关文档：`docs/Tools技术细节/04_动作清单与CDP映射.md` 的 4.9 节（本项目现状）、`browser-use/docs/Tools技术细节/05-动作详解-浏览器交互.md` 的 3. go_back 节（参考标杆）。

## 背景（为什么改）

当前 TreeWalker 的 `go_back`（`src/tree_walker/tools/actions.py:387-389` + `src/tree_walker/browser/session.py:410-422`）已经做了两件对的事——后退后调 `_wait_for_page_settle()`（轮询 `document.readyState`，不再是硬编码 sleep）、有 `idx > 0` 边界判断——但对照刚被完善的 `navigate`（`actions.py:223-298`）仍存在四类问题：

1. **不清缓存（正确性 bug）**：`go_back` 改变了页面，却没有像 `navigate`（`session.py:396-397`）和 `switch_tab`（`session.py:1009-1010`）那样清空 `_cached_selector_map` / `_previous_cached_selector_map`。残留的旧 selector_map 会让下一步 `get_state` 的"新元素检测"把上一页的元素误判为本次新增，`_get_element_by_index`（`actions.py:179-194`）可能命中已失效的 backendNodeId。
2. **无历史可退时静默成功（语义缺陷）**：`idx <= 0` 时 `session.go_back` 直接 return，`_action_go_back` 返回空 `ActionResult()`（显示 `OK`）。LLM 误以为"已后退成功"，实际什么都没发生——这是 browser-use 也有的缺陷（`default_action_watchdog.py:2349` 静默 return，外层 `service.py:572` 照常返回 `extracted_content='Navigated back'`）。
3. **无结果回显**：成功后退返回空 OK，LLM 不知道退到了哪一页。browser-use 同样只回显固定字符串 `'Navigated back'`，目标 URL `entries[current_index - 1]["url"]` 仅写进日志（`default_action_watchdog.py:2361`），不进 ActionResult。
4. **无错误处理**：`Page.getNavigationHistory` / `Page.navigateToHistoryEntry` 抛错会裸冒泡到 `Tools.execute` 通用兜底（`actions.py:171-173`），被打成 `ActionResult(error=str(e))`，丢失"后退失败"语义。

附带问题：`docs/Tools技术细节/04_动作清单与CDP映射.md` 的 4.9 节注意事项仍写"后退后硬编码 `sleep(0.3)` 等待页面渲染"，已与实现（`_wait_for_page_settle()`）脱节（navigate 方案的"附带文档同步"节也已点名此条需修正）。

**已确认的决策（用户已选）：**
- 健康检查用**轻量检查**：后退后用 `_dom_appears_empty`（`actions.py:242-252`）检测空 DOM，空则等一次 `_NAVIGATE_EMPTY_RETRY_WAIT`（3s，复用现有常量）重查；仍空只 `logger.warning` 不硬失败。理由：`go_back` 没有"reload 同 URL"的干净机制——重新 `navigate(url)` 会新增一条历史项而非"再后退一次"，`Page.reload` 又是本项目尚未封装的新 CDP 调用，轻量检查既覆盖 SPA 异步未渲染，又避免把一次合法后退误判成失败。
- 无历史可退返回**明确 error**（不静默成功），让 LLM 停止无意义的重试。
- 回显后退目标 URL、缓存清理、try/except 友好映射一并纳入。

预期结果：`go_back` 清缓存避免新元素误判；无历史可退时明确告知 LLM；成功后退回显目标 URL；SPA 未渲染给一次重试机会；CDP 异常映射为友好 error。

---

## 改动文件（共 3 个）

### 1. `src/tree_walker/browser/session.py`

**重写 `go_back`（第 410-422 行）**：清缓存、返回后退目标 URL（或 `None` 表示无历史）。

```python
async def go_back(self) -> str | None:
	"""Navigate to the previous page in history.

	Returns the URL of the previous entry, or ``None`` if there is no
	previous entry to go back to (caller should treat ``None`` as "nothing
	happened"). Clears the selector_map caches — like ``navigate`` and
	``switch_tab`` — because going back changes the page.
	"""
	# 后退会改变页面，两层 selector_map 缓存都要清（与 navigate:396-397 / switch_tab:1009-1010 一致）
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
```

- **返回类型 `str | None`**：后退目标 URL 取自 `getNavigationHistory` 已返回的 `entries[idx-1]["url"]`（browser-use `default_action_watchdog.py:2361` 证实该字段存在），**零额外 CDP 调用**即可回显。无历史返回 `None`，与 `navigate` 返回 `target_id | None` 的风格一致。
- **缓存清理**：新增两行，与 `navigate`、`switch_tab` 对齐。清在取历史之前（与 `navigate` 清在 `Page.navigate` 之前一致），保证操作期间无人读到旧 map。
- **边界判断 `idx <= 0`**：保留原 `idx > 0` 语义，但改为返回 `None` 而非静默 return，让上层能区分"真的后退了"和"无事可做"。
- **`_wait_for_page_settle()` 保留**：已是 `readyState` 轮询，不动。
- **签名变更兼容性**：唯一调用方是 `_action_go_back`（`actions.py:387`），无其他调用点；返回值由忽略改为接收，向后兼容。

---

### 2. `src/tree_walker/tools/actions.py`

**(a) 重写 `_action_go_back`（第 387-389 行）**：

```python
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
```

**(b) 新增轻量健康检查 helper（紧随 `_action_go_back`，复用 `_dom_appears_empty`）**：

```python
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
```

- **try/except 包裹整个后退**：CDP 异常映射为 `Failed to go back: {e}`（不复用 `_map_navigation_error`——后退失败并非"站点不可达"，语义不同，直述原始信息更诚实）。
- **无历史 → 明确 error**：`target_url is None` 时返回 `error="No previous page in history to go back to"`，符合项目反静默失败惯例（与 `_navigate_health_check` 抛 `RuntimeError` 一致）。
- **回显 `extracted_content` + `long_term_memory`**：让 LLM 从 result 确认后退目的地（参照 navigate 与 search 优化）；不传 `success=True`（`ActionResult.validate_success_requires_done` 校验器 `views.py:18-25` 对非 done 动作拒绝）。
- **健康检查复用 `_dom_appears_empty`（`actions.py:242-252`）与现有常量 `_NAVIGATE_EMPTY_RETRY_WAIT`（`actions.py:123`）**：无需新增任何模块常量。
- **非 http(s) 跳过健康检查**：`chrome://`、`about:` 等历史项不触发（与 navigate 一致，避免对浏览器内置页误报）。

---

### 3. `tests/test_go_back.py`（新建）

参照 `tests/test_navigate.py` 的 mock 模式（mock 整个 `browser.go_back` / `browser.get_state`，不碰 CDP 原语）。session 层用例参照 `tests/test_multi_act.py:865-938`（`TestWaitForPageSettle`，`BrowserSession.__new__` 构造半初始化 session）。当前 go_back 无行为级单元测试（仅 `test_multi_act.py:676-693` 测 terminates 守卫，用 fake tools 不跑真 handler）。

**测试用例清单**：

| 组 | 用例 | 断言要点 |
|---|---|---|
| 错误映射 | `browser.go_back` 抛 `RuntimeError("boom")` | action 返回 `error="Failed to go back: boom"`；`get_state` 未被调用 |
| 无历史 | `browser.go_back` 返回 `None` | `error="No previous page in history to go back to"`；`get_state` 未被调用 |
| 成功回显 | `browser.go_back` 返回 `"https://a.com"` + 非空 state | `extracted_content`/`long_term_memory` 均为 `Navigated back to https://a.com`；`error is None` |
| 健康检查-正常页 | 非空 state | 不 sleep、不重查、返回成功 |
| 健康检查-空后恢复 | 首次空、3s 后非空 | sleep 一次（`_NAVIGATE_EMPTY_RETRY_WAIT`）；最终成功回显 |
| 健康检查-持续空 | 两次都空 | **不**报 error（成功回显）；`get_state` 被调用两次；仅 warning |
| 健康检查-非 http 跳过 | `state.url` 为 `chrome://settings` 且空 | 不触发重试，`get_state` 仅一次 |
| session-返回 URL | `getNavigationHistory` 含 2 条目，currentIndex=1 | `navigateToHistoryEntry` 传入 `entries[0]["id"]`；返回 `entries[0]["url"]` |
| session-无历史 | currentIndex=0 | 返回 `None`；`navigateToHistoryEntry` 未被调用 |
| session-清缓存 | 预置非空 `_cached_selector_map` | 调用后两层缓存均为 `None`（对齐 navigate） |

代表性骨架：

```python
"""Tests for go_back: cache clearing, no-history error, health check, URL echo."""
from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock
import pytest
from tree_walker.tools.actions import Tools
from tree_walker.browser.session import BrowserSession
from tree_walker.browser.views import BrowserStateSummary, SerializedDOMState


def _state(empty: bool, url: str = "https://example.com") -> BrowserStateSummary:
	ds = SerializedDOMState(
		_root=None if empty else object(),  # _root 非 None 即视为已渲染
		selector_map={},
		element_tree_text="" if empty else "<body>content</body>",
	)
	return BrowserStateSummary(url=url, title="", dom_state=ds)


def _browser(go_back_return="https://a.com", go_back_side_effect=None, states=None):
	bs = MagicMock()
	if go_back_side_effect:
		bs.go_back = AsyncMock(side_effect=go_back_side_effect)
	else:
		bs.go_back = AsyncMock(return_value=go_back_return)
	queue = list(states or [])
	async def fake_get_state(include_screenshot=True):
		return queue.pop(0) if queue else _state(empty=False)
	bs.get_state = fake_get_state
	return bs


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch):
	# 把健康检查的 3s 等待 patch 为 0，避免测试睡 3s
	monkeypatch.setattr("tree_walker.tools.actions._NAVIGATE_EMPTY_RETRY_WAIT", 0.0)


class TestGoBackErrorMapping:
	@pytest.mark.asyncio
	async def test_exception_maps_to_friendly_error(self):
		bs = _browser(go_back_side_effect=RuntimeError("boom"))
		result = await Tools().execute("go_back", {}, bs)
		assert result.error == "Failed to go back: boom"


class TestGoBackNoHistory:
	@pytest.mark.asyncio
	async def test_none_return_is_explicit_error(self):
		bs = _browser(go_back_return=None)
		result = await Tools().execute("go_back", {}, bs)
		assert result.error == "No previous page in history to go back to"
		bs.get_state.assert_not_called()  # 无历史不进健康检查


class TestGoBackSuccess:
	@pytest.mark.asyncio
	async def test_echoes_back_target_url(self):
		bs = _browser(go_back_return="https://a.com", states=[_state(empty=False)])
		result = await Tools().execute("go_back", {}, bs)
		assert result.error is None
		assert result.extracted_content == "Navigated back to https://a.com"
		assert result.long_term_memory == "Navigated back to https://a.com"


class TestGoBackHealthCheck:
	@pytest.mark.asyncio
	async def test_persistent_empty_does_not_fail_hard(self):
		bs = _browser(go_back_return="https://a.com",
		              states=[_state(empty=True), _state(empty=True)])
		result = await Tools().execute("go_back", {}, bs)
		assert result.error is None  # 轻量策略：不硬失败
		assert result.extracted_content == "Navigated back to https://a.com"
		assert bs.get_state.await_count == 2  # 初检 + 重查

	@pytest.mark.asyncio
	async def test_non_http_target_skips_check(self):
		bs = _browser(go_back_return="chrome://settings",
		              states=[_state(empty=True, url="chrome://settings")])
		result = await Tools().execute("go_back", {}, bs)
		assert result.error is None
		assert bs.get_state.await_count == 1  # 不重试


class TestGoBackSession:
	def _make_session(self, history):
		s = BrowserSession.__new__(BrowserSession)  # 绕过 __init__（不需 ws_url）
		s._settings = MagicMock(page_settle_timeout=0.0, page_settle_poll_interval=0.0)
		s.current_session_id = "sid"
		client = MagicMock()
		client.send.Page.getNavigationHistory = AsyncMock(return_value=history)
		client.send.Page.navigateToHistoryEntry = AsyncMock(return_value={})
		client.send.Runtime.evaluate = AsyncMock(
			return_value={"result": {"value": "complete"}})  # _wait_for_page_settle 用
		s.client = client
		s._cached_selector_map = {"old": 1}
		s._previous_cached_selector_map = {"old": 1}
		return s, client

	@pytest.mark.asyncio
	async def test_returns_previous_url_and_navigates(self):
		hist = {"currentIndex": 1, "entries": [
			{"id": 11, "url": "https://prev.com"}, {"id": 22, "url": "https://cur.com"}]}
		s, client = self._make_session(hist)
		url = await s.go_back()
		assert url == "https://prev.com"
		client.send.Page.navigateToHistoryEntry.assert_awaited_once_with(
			{"entryId": 11}, session_id="sid")

	@pytest.mark.asyncio
	async def test_no_history_returns_none(self):
		s, client = self._make_session({"currentIndex": 0, "entries": [{"id": 1, "url": "u"}]})
		assert await s.go_back() is None
		client.send.Page.navigateToHistoryEntry.assert_not_called()

	@pytest.mark.asyncio
	async def test_clears_selector_map_caches(self):
		hist = {"currentIndex": 1, "entries": [{"id": 1, "url": "u"}, {"id": 2, "url": "v"}]}
		s, _ = self._make_session(hist)
		await s.go_back()
		assert s._cached_selector_map is None
		assert s._previous_cached_selector_map is None
```

> 加速：`_fast_retry` fixture 把 `_NAVIGATE_EMPTY_RETRY_WAIT` patch 为 0；session 层用例把 `page_settle_timeout` 设 0 让 `_wait_for_page_settle` 立即返回。
> 回归：`tests/test_multi_act.py:676-693` 的 `test_guard4_go_back_terminates` 用 fake tools，不受 `_action_go_back` 改动影响，应继续通过。

---

## 附带文档同步（建议本次一并做）

更新 `docs/Tools技术细节/04_动作清单与CDP映射.md` 的 4.9 go_back 节：
- 主要逻辑改为新实现（`session.go_back` 返回 URL / `None` 并清缓存；`_action_go_back` 的 try/except、无历史 error、回显、健康检查）；
- CDP 调用清单不变（仍为 `Page.getNavigationHistory` + `Page.navigateToHistoryEntry`），但补注 session 层现在还清 `_cached_selector_map`；
- 注意事项修正「后退后硬编码 `sleep(0.3)` 等待页面渲染」为 `_wait_for_page_settle()`（描述已与实现脱节），并补「无历史可退返回明确 error」「成功后退回显目标 URL」「SPA 未渲染轻量重试」三条。

---

## 技术决策说明（要点）

- **健康检查用轻量策略（用户已选）**：空 → 等一次 `_NAVIGATE_EMPTY_RETRY_WAIT(3s)` → 仍空仅 `warning` 不硬失败。`go_back` 无"reload 同 URL"的干净机制（重新 `navigate` 会新增历史项），故不照搬 navigate 三阶段。复用现有 `_dom_appears_empty` 与常量，零新增依赖。
- **无历史返回明确 error（用户已选）**：`target_url is None` → `error="No previous page in history to go back to"`。避免 LLM 误以为已后退而反复调用（browser-use 此处静默成功是已知缺陷）。
- **session 层返回 URL、action 层做策略**：`BrowserSession.go_back` 成功返回目标 URL / 失败抛异常 / 无历史返回 `None`，保持纯粹；`_action_go_back` 负责把三种情况映射为对 LLM 友好的 `ActionResult`（对齐 navigate 的分层）。
- **回显 URL 取自历史项**：`getNavigationHistory` 已返回 `entries[idx-1]["url"]`，回显零额外 CDP 调用（browser-use 同样有此字段，但只写日志不回显）。
- **错误映射不复用 `_map_navigation_error`**：后退失败语义不是"站点不可达"，直述 `Failed to go back: {e}` 更诚实；navigate 的 5 个网络错误码标记对历史导航不适用。
- **缓存清理对齐 navigate/switch_tab**：新增 `self._cached_selector_map = None` + `self._previous_cached_selector_map = None`，避免旧 selector_map 残留导致新元素误判。

## 已知限制（本次不处理，留作未来选项）

- **轻量健康检查比 navigate 弱**：不 reload、不硬失败，持续空仅 warning。SPA 后退完全未渲染的极端场景靠 LLM 下一轮 `get_state` 自行感知。如需更强可未来引入 `Page.reload` 封装或 `Page.lifecycleEvent` 监听（见 navigate 方案已知限制同条）。
- **`_wait_for_page_settle` 仅轮询 `readyState`**：对 SPA 异步渲染不如 browser-use 的 `Page.lifecycleEvent`（networkIdle/load）精准，但轻量健康检查的一次重试已覆盖主要"未渲染"场景。
- **回显 URL 取自历史项、非后退后实测**：极少数情况下后退目标 URL 在加载期被重定向，回显值可能与最终落地 URL 不一致（navigate 的 `_navigate_health_check` 也只比对 `state.url` 是否 http，未校验一致）。如需强一致可在健康检查后用 `state.url` 覆盖回显——本次不做，避免增加耦合。
- **`go_forward` 未一并实现**：browser-use 的 `on_GoForwardEvent`（`default_action_watchdog.py:2364-2390`）与 go_back 几乎逐行对称，本方案的可复用结构（session 返回 / 健康检查 / 回显）可直接套用；但本项目 24 个 action 暂无 `go_forward`（不在本次范围）。
- **未引入新模块常量**：复用 `_NAVIGATE_EMPTY_RETRY_WAIT`；若未来需要独立调参可再加 `_GO_BACK_EMPTY_RETRY_WAIT`。

---

## 验证步骤

1. **跑新增单元测试**（CLAUDE.md 要求改动后必须跑）：
   ```powershell
   uv run python -m pytest tests/test_go_back.py -x -v
   ```
2. **回归 go_back 相关既有测试**（terminates 守卫、_wait_for_page_settle、缓存相关）：
   ```powershell
   uv run python -m pytest tests/test_multi_act.py -x -v
   ```
3. **全量测试 + 覆盖率**（项目目标 >85%）：
   ```powershell
   uv run python -m pytest tests/ -x -v
   uv run python -m pytest tests/ --cov=tree_walker.tools --cov=tree_walker.browser --cov-report=term-missing
   ```
4. **手动验证无历史可退路径**（可选，连真实浏览器）：
   ```powershell
   uv run python -c "import asyncio; from tree_walker.browser.session import BrowserSession; s=BrowserSession(ws_url='http://localhost:9222'); asyncio.run(s.start()); print(asyncio.run(s.go_back()))"
   ```
   预期：新开空白页直接 `go_back` 返回 `None`（此前也是 `None`，但 action 层此前静默成功，现改为 `error="No previous page..."`）；先 `navigate` 两次再 `go_back` 应返回上一页 URL 并清缓存。
