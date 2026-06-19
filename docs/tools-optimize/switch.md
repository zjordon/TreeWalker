# switch_tab 工具完善方案

> 参照 browser-use 的 `switch` 实现，完善本项目 `switch_tab` 工具。
>
> 本文为**提案文档**（不改源码），给出差距分析、分级改进方案与可落地的代码片段，供后续实施直接参照。范围：**核心改进（switch_tab 工具本身）+ 点击打开新标签页自动切换**。

---

## 1. 背景与目标

参照对象：

- browser-use：`browser_use/tools/service.py` 的 `switch` 动作 + `browser_use/browser/session.py` 的 `on_SwitchTabEvent` 处理器 + `browser_use/tools/service.py` 的 `_detect_new_tab_opened`。
- 本项目：`src/tree_walker/tools/actions.py` 的 `_action_switch_tab` / `_action_click`、`src/tree_walker/browser/session.py` 的 `switch_tab` / `get_state`、`src/tree_walker/tools/models.py` 的 `SwitchTabParams`、`src/tree_walker/prompts/system_prompt.py` 的标签页渲染。

目标：

1. **统一回显风格**：切换成功后回显"切到了哪个标签页"，对齐 `navigate`/`click`/`go_back`。
2. **收紧匹配正确性**：后缀撞车时报错而非切错页；`tab_id` 非空校验。
3. **降低枚举开销**：标签页枚举从"全量 DOM 抓取"降为单 `Target.getTargets`。
4. **补齐 browser-use 对等能力**：点击打开新标签页时自动切换并回显。
5. **补齐测试**：新增 `tests/test_switch_tab.py`，并修正既有测试的错误参数名。

---

## 2. 现状对比

| 维度 | browser-use `switch` | TreeWalker `switch_tab`（当前） | 差距 |
|---|---|---|---|
| 参数校验 | `tab_id: str = Field(min_length=4, max_length=4)` | `tab_id: str` 无校验 | 无长度约束 |
| 后缀解析 | `endswith` + `is_target_valid` 跳过失效 target | `endswith` 取首个匹配 | 无冲突检测、不剔失效 target |
| 成功回显 | `Switched to tab #{tab_id}`（extracted + long_term） | 裸 `ActionResult()` | **无回显** |
| 标签页枚举 | 专用 `get_tabs()`，读 SessionManager 缓存（无 CDP） | `get_state()`（含全量 DOM） | **开销过大** |
| prompt 展示 | `Tab {id}: {url} - {title}` | `[{id}] {title}`（仅 >1 页时） | 无 URL |
| 失败语义 | 软降级 `Attempted to switch...` | `error=...not found` | — |
| `tab_id=None`/空 | 切到最近打开页（无页则建 about:blank） | 无此语义 | 缺"切到最新页" |
| 点击开新页 | `_detect_new_tab_opened` 自动切换 + 回显 | 无 | **功能性缺口** |
| 测试 | 有 | 无（且 multi_act 桩用错参数名） | 缺测试 |

---

## 3. 改进方案（分级）

### P0 核心 —— switch_tab 工具本身

#### G1｜成功回显（对齐 navigate/click/go_back）

`src/tree_walker/tools/actions.py` 的 `_action_switch_tab`（当前 `actions.py:599-606`）改为：

```python
async def _action_switch_tab(self, params: dict, browser: BrowserSession) -> ActionResult:
	tab_id_suffix = params["tab_id"]
	tabs = await browser.get_tabs()
	matches = [t for t in tabs if t.target_id.endswith(tab_id_suffix)]
	if not matches:
		return ActionResult(
			error=f"No tab ending with '{tab_id_suffix}'. "
			      f"Open tabs: {self._summarize_tabs(tabs)}",
		)
	if len(matches) > 1:  # G3 后缀冲突检测
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
```

新增静态 helper（放 `_describe_click` 同区，`actions.py:375` 附近），把标签页摘要成 `[{last4}] title - url` 短串，供 error 信息和回显复用：

```python
@staticmethod
def _summarize_tabs(tabs: list) -> str:
	items = []
	for t in tabs:
		title = (t.title or "").strip()[:40]
		url = (t.url or "").strip()[:60]
		items.append(f"[{t.target_id[-4:]}] {title} - {url}")
	return "; ".join(items)
```

#### G2｜轻量 `BrowserSession.get_tabs()`

`src/tree_walker/browser/session.py` 抽出现有 `get_state` 内的标签页抓取逻辑（`session.py:358-369`）为独立方法：

```python
async def get_tabs(self) -> list[TabInfo]:
	"""List page targets only — 1 个 CDP 调用（Target.getTargets），不抓 DOM/截图。"""
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
```

`get_state`（`session.py:358-369`）内部改为 `tabs = await self.get_tabs()`（DRY，行为不变）。`_action_switch_tab` / `_action_close_tab` 改调 `get_tabs()` 而非 `get_state(include_screenshot=False)`，省掉每次切换/关闭的全量 DOM 抓取。

> 收益：switch_tab / close_tab 的枚举从 `Runtime.evaluate`（取 url/title）+ 全量 DOM（`getDOM`）+ `Target.getTargets` 三类调用，降为单 `Target.getTargets`。

#### G3｜后缀冲突检测

已并入 G1 的 `if len(matches) > 1` 分支。**比 browser-use 更严**：browser-use 取首个 valid 匹配即切换，本方案在后缀撞车时直接报错，并提示 LLM "用更多字符或完整 target_id"，避免切错页。

#### G4｜`tab_id` 非空校验

`src/tree_walker/tools/models.py` 的 `SwitchTabParams`（`models.py:52-54`）：

```python
class SwitchTabParams(BaseModel):
	model_config = ConfigDict(extra="forbid")
	tab_id: str = Field(min_length=1, description="Tab ID (last 4 characters) to switch to")
```

> browser-use 用 `min_length=4, max_length=4` 强约束。本方案采用 `min_length=1`（保留接受完整 target_id 的灵活性），靠 G3 保证正确性。若想完全对齐 browser-use，可收紧到 `min_length=4, max_length=4` 作为备选。

#### G5｜prompt 标签列表显示 URL

`src/tree_walker/prompts/system_prompt.py:134-136`：

```python
for tab in browser_state.tabs:
	marker = " (active)" if tab.target_id == current_target_id else ""
	parts.append(f"  [{tab.target_id[-4:]}] {tab.title} - {tab.url}{marker}")
```

对齐 browser-use `Tab {id}: {url} - {title}`。保留 `len(tabs) > 1` 才展示的阈值（避免单页噪声）。

#### G6｜新增 `tests/test_switch_tab.py`

> CLAUDE.md 要求：改功能必须同步加测试、覆盖率 >85%。参照 `tests/test_navigate.py`（action 层 `_make_browser` / `_make_state`）+ `tests/test_click.py:220-264`（session 层 `BrowserSession.__new__` + `MagicMock` client + `AsyncMock`）的既有模式。

- **action 层**：
  - 成功回显含 `Switched to tab`，且 `extracted_content == long_term_memory`；
  - 未命中返回 error，且 error 中列出现有标签页（验证 `_summarize_tabs`）；
  - 两个 tab 后缀撞车返回 conflict error；
  - 验证调用的是 `browser.get_tabs()` 而非 `get_state()`（`assert browser.get_tabs.awaited`）。
- **param model**：`tab_id=""` 触发 `ValidationError`（min_length=1）；`extra="forbid"` 拒绝未知字段。
- **session 层**：`switch_tab` 按序发出 `Target.activateTarget` → `Target.attachToTarget(flatten=True)`，更新 `current_target_id` / `current_session_id`，并清空两层 selector_map 缓存；`get_tabs` 只返回 `type=="page"`。
- **修复既有 bug**：`tests/test_multi_act.py:663` 的桩参数 `{"target_id": "abc"}` 改为 `{"tab_id": "abcd"}`（修正错误参数名，否则该桩无法捕获真实的 param-model 不匹配）。

### P1 增强 —— 点击打开新标签页自动切换

#### G7｜`_detect_new_tab_opened`（对齐 browser-use `service.py:608-633`）

`src/tree_walker/tools/actions.py` 新增 helper（注意：browser-use 这段逻辑在它项目里是模块级函数，本方案挂到 `Tools` 实例上，与 `_describe_click` 同风格）：

```python
async def _detect_new_tab_opened(
	self, browser: BrowserSession, tabs_before: tuple[str, ...],
) -> str:
	"""点击若打开了新标签页，自动切过去并返回给 LLM 的提示串。"""
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
```

集成进 `_action_click`（`actions.py:336-373`）：在 `click_element` 之前快照 `tabs_before`，成功后追加提示：

```python
# 3. 普通点击
tabs_before = tuple(t.target_id for t in await browser.get_tabs())  # G7 快照
try:
	await browser.highlight_element(backend_id)
	clicked = await browser.click_element(backend_id)
except Exception as e:
	return ActionResult(error=f"Click failed: {e}")
if not clicked:
	return ActionResult(error=(...))  # 保持原文案

# 4. 成功回显 + 新标签页检测
memory = self._describe_click(entry, params["index"])
memory += await self._detect_new_tab_opened(browser, tabs_before)  # G7
logger.info(memory)
return ActionResult(extracted_content=memory, long_term_memory=memory)
```

> SELECT 分支（`actions.py:345-350`）不参与（不产生新页）。

**G7 成本说明**：每次 click 额外 2× `Target.getTargets`。降本建议（实施时择一）：

- (a) 加配置开关 `auto_switch_on_new_tab`（默认开），可关；
- (b) 仅当被点元素疑似开新页时才检测（JS 读 `target==='_blank'` / `rel=opener` 或 `window.open` 调用），常态点击免开销。

> 本提案不在此处强行二选一，留作实施备注。

#### G8（仅讨论，不强制）｜`tab_id=""` 切到最近打开页

browser-use 的 `SwitchTabEvent(None)` 切到最近打开页、无页则建 about:blank。这与本项目 `close_tab` 的"空 = 关当前页"语义易混。**本方案不改 `SwitchTabParams`（保持必填），G8 仅作为可选/后续讨论，避免语义冲突。**

---

## 4. CDP 调用清单（变更后）

| Action | CDP 命令 | 变化 |
|---|---|---|
| `switch_tab` | `Target.getTargets`（枚举，经 `get_tabs`）+ `Target.activateTarget` + `Target.attachToTarget(flatten)` | 枚举从全量 DOM 降为单 `getTargets` |
| `click`（命中开新页链接） | 原有点击序列 + 2× `Target.getTargets` + 可能的 `activateTarget` / `attachToTarget` | 新增自动切换路径 |
| `close_tab` | `Target.getTargets`（经 `get_tabs`）+ `Target.closeTarget` [+ 切页] | 枚举同样降为 `get_tabs` |

---

## 5. 涉及文件清单

| 文件 | 改动 | 对应改进 |
|---|---|---|
| `src/tree_walker/tools/actions.py` | `_action_switch_tab` 重写；`_action_click` 接入新页检测；新增 `_summarize_tabs` / `_detect_new_tab_opened` | G1 / G3 / G7 |
| `src/tree_walker/tools/models.py` | `SwitchTabParams.tab_id` 加 `min_length=1` | G4 |
| `src/tree_walker/browser/session.py` | 新增 `get_tabs()`；`get_state` 改调它 | G2 |
| `src/tree_walker/prompts/system_prompt.py` | 标签页行追加 ` - {url}` | G5 |
| `tests/test_switch_tab.py` | **新建** | G6 |
| `tests/test_multi_act.py` | 修正 `:663` 参数名 | G6 |
| `docs/Tools技术细节/04_动作清单与CDP映射.md` | 4.21 节同步更新回显 / `get_tabs` / 冲突检测描述并刷新过时行号（实施后） | 文档同步 |

---

## 6. 实施后的验证

- 新增/相关测试：`uv run python -m pytest tests/test_switch_tab.py -x -v`
- 全量回归：`uv run python -m pytest tests/ -x -v`
- 覆盖率（CLAUDE.md 目标 >85%）：`uv run python -m pytest tests/ --cov=tree_walker.tools --cov=tree_walker.browser`
- 手测：打开含 `target="_blank"` 链接的页面 → `click` → 确认回显含"auto-switched to new tab"，且后续状态已在新页。
