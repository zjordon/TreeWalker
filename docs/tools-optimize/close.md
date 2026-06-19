# close_tab 工具完善方案

> 参照 browser-use 的 `close` 实现，完善本项目 `close_tab` 工具。
>
> 本文为**提案文档**（不改源码），给出差距分析、分级改进方案与可落地的代码片段，供后续实施直接参照。范围：**核心改进（close_tab 工具本身）+ 关闭最后一个标签页的健壮性**。

---

## 1. 背景与目标

参照对象：

- browser-use：`browser_use/tools/service.py` 的 `close` 动作 + `browser_use/browser/session.py` 的 `on_CloseTabEvent` 处理器。
- 本项目：`src/tree_walker/tools/actions.py` 的 `_action_close_tab`、`src/tree_walker/browser/session.py` 的 `close_tab` / `get_tabs` / `switch_tab`、`src/tree_walker/tools/models.py` 的 `CloseTabParams`。
- 对齐基准：`switch_tab`（PR #21，`docs/tools-optimize/switch.md`）已建立的标签页类动作新规范——成功回显、轻量 `get_tabs` 枚举、后缀冲突检测、未命中列出页表、补测试。

目标：

1. **统一回显风格**：关闭成功后回显"关了哪个标签页"，对齐 `navigate`/`click`/`go_back`/`switch_tab`。
2. **收紧匹配正确性**：后缀撞车时报错而非关错页；未命中时列出现有标签页供 LLM 重选。
3. **降低枚举开销**：标签页枚举从"全量 DOM 抓取（`get_state`）"降为单 `Target.getTargets`（`get_tabs`）。
4. **补齐 browser-use 对等能力**：对已关闭/失效的 target ID 软降级（关闭是幂等操作）。
5. **补齐测试**：新增 `tests/test_close_tab.py`，对齐 `tests/test_switch_tab.py` 的分层结构。

---

## 2. 现状对比

| 维度 | browser-use `close` | TreeWalker `close_tab`（当前） | 差距 |
|---|---|---|---|
| 成功回显 | `Closed tab #{tab_id}`（extracted + long_term）+ `logger.info` | 裸 `ActionResult()`，无回显、无日志 | **无回显** |
| 标签页枚举 | `get_target_id_from_tab_id`（读 SessionManager 缓存，无 CDP） | `get_state(include_screenshot=False)`（含全量 DOM） | **开销过大** |
| 后缀匹配 | `endswith` + `is_target_valid` 取首个 valid | `endswith` 取首个匹配 | **关错页风险**（无冲突检测） |
| 未命中提示 | （action 层不枚举） | `error=Tab ending with '...' not found`，不列现有页 | 不利于 LLM 恢复 |
| 失效 target 处理 | catch 异常 → 软成功 `closed (was already closed or invalid)` | 无，异常直接外抛 | **缺软降级** |
| 空 `tab_id` 语义 | 无（`min_length=4, max_length=4` 必填） | `""` = 关当前页（保留） | 项目自有语义 |
| 关当前页后状态 | — | 自动切到首个剩余 page；**无剩余页则 `current_target_id`/`current_session_id` 指向已死页** | **状态悬挂**（P1） |
| 测试 | 有 | **无** | 缺测试（覆盖率缺口） |

---

## 3. 改进方案（分级）

### P0 核心 —— close_tab 工具本身

#### G1｜成功回显（对齐 navigate/click/go_back/switch_tab）

关闭成功后回显 `Closed tab [{id}] {title} ({url})`，写入 `extracted_content` + `long_term_memory`，并 `logger.info`，让 LLM 从 result 确认关了哪个页。

#### G2｜轻量 `BrowserSession.get_tabs()` 取代全量 `get_state`

`get_tabs()`（`session.py:1244-1262`）已在 `switch_tab` 改造中抽出，单 `Target.getTargets`、只返回 `type == "page"`，不抓 DOM/截图。`close_tab` 改调它，省掉每次关闭的全量 DOM 抓取。

> 收益：close_tab 的枚举从 `Runtime.evaluate`（取 url/title）+ 全量 DOM（`getDOM`）+ `Target.getTargets` 三类调用，降为单 `Target.getTargets`。

#### G3｜后缀冲突检测

收集全部 `endswith` 匹配，`len(matches) > 1` 时**报错**（**比 browser-use 取首个 valid 匹配更严**），并提示 LLM "用更多字符或完整 target_id"，避免关错页。复用 `switch_tab` 已建立的 `Tools._summarize_tabs`（`actions.py:649-657`）渲染匹配列表。

#### G4｜未命中列出现有页

未命中时 error 追加 `_summarize_tabs(tabs)`，对齐 `switch_tab`，便于 LLM 重选。

#### G5｜失效 target 软降级（对齐 browser-use `service.py:1011-1018`）

`browser.close_tab(target_id)` 包 `try/except`：命中异常（target 已被外部关闭/失效，或 `Target.closeTarget` 返回错误）→ warning 日志 + **软成功**回显 `Tab [{id}] {title} ({url}) was already closed or invalid`（**不是 error**——关闭是幂等操作，目标不存在即视为已达成）。browser-use 的 `on_CloseTabEvent`（`session.py:1125-1138`）同样在底层 catch 并 `debug` 日志 `Target may already be closed`。

#### G6｜空 `tab_id` = 关当前页（保留语义 + 补回显）

保留 `default=""` → 关 `current_target_id` 的既有行为；补回显（从 `get_tabs()` 快照取当前页 title/url）。`current_target_id` 为空时明确 `error="No current tab to close"`。

**统一后的 `_action_close_tab`（替换 `actions.py:659-670`）：**

```python
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
```

#### G7｜`CloseTabParams` 描述补全（`models.py:57-59`）

`default=""` 不变（保留空=关当前页），仅补文档说明后缀约定（与 `SwitchTabParams` 描述对齐）：

```python
class CloseTabParams(BaseModel):
	model_config = ConfigDict(extra="forbid")
	tab_id: str = Field(
		default="",
		description="Tab ID (last 4 characters) to close. Empty string closes the current tab.",
	)
```

> browser-use 用 `min_length=4, max_length=4` 强约束且不支持空值。本方案保留 `default=""`（空=关当前页是本项目自有便利语义），靠 G3 保证正确性，不加 `min_length`。

#### G8｜新增 `tests/test_close_tab.py`（对齐 `tests/test_switch_tab.py` 分层结构）

> CLAUDE.md 要求：改功能必须同步加测试、覆盖率 >85%。参照 `tests/test_switch_tab.py` 的既有模式：action 层用 `_make_browser` 桩（`MagicMock` + `AsyncMock`，经 `Tools().execute` 调用）；param model 层校验 `ValidationError`；session 层用 `BrowserSession.__new__` 绕过 `__init__` + `MagicMock` client。所有 async 测试显式标 `@pytest.mark.asyncio`（项目无 `asyncio_mode=auto`）。

| 测试类 | 用例 |
|---|---|
| `TestCloseTabAction` | ① 指定 tab_id 成功回显含 `Closed tab [1234]` + title + url、`extracted_content == long_term_memory`、`close_tab` 收到完整 target_id、**`get_state` 未被 await**；② 完整 target_id 也命中；③ 空 `tab_id` 关 `browser.current_target_id`、回显含 `Closed tab`；④ 未命中 error 列出现有页、`close_tab` 未被 await、`get_tabs` 被 await；⑤ 两页撞后缀 → conflict error、`close_tab` 未被 await；⑥ `close_tab` 抛异常 → 软成功回显 `was already closed or invalid`（**非 error**） |
| `TestCloseTabParams` | 空 `tab_id` **被接受**（区别于 `SwitchTabParams` 的 `min_length=1`）；非空接受；`extra="forbid"` 拒绝未知字段 |
| `TestCloseTabSession` | 关当前页 → `closeTarget` + `getTargets` + `switch_tab`，`current_target_id`/`current_session_id` 更新；关非当前页 → 仅 `closeTarget`，`current_*` 不变 |

action 层桩（复用 `test_switch_tab.py` 的 `_make_browser` 模式）：

```python
def _make_browser(tabs: list[TabInfo], *, current_target_id: str | None = "AAA1111234") -> MagicMock:
	bs = MagicMock()
	bs.get_tabs = AsyncMock(return_value=tabs)
	bs.close_tab = AsyncMock()
	bs.get_state = AsyncMock()  # 仅用于断言未被调用
	bs.current_target_id = current_target_id
	return bs
```

### P1 增强 —— 关闭最后一个标签页的健壮性

#### G9｜`BrowserSession.close_tab` 兜底 about:blank（`session.py:1277-1286`）

当前关当前页且无其他 page target 时，`current_target_id`/`current_session_id` 悬挂指向已死页，下一个 action 对失效 session_id 发 CDP 调用会失败。补 about:blank 兜底（对齐 browser-use `on_SwitchTabEvent` 无页则建 about:blank 的思路）：

```python
async def close_tab(self, target_id: str) -> None:
	"""Close a tab. If it's the current tab, switch to another; create about:blank if none."""
	was_current = target_id == self.current_target_id
	await self.client.send.Target.closeTarget({"targetId": target_id})
	if was_current:
		targets = await self.client.send.Target.getTargets({})
		for t in targets.get("targetInfos", []):
			if t.get("type") == "page" and t["targetId"] != target_id:
				await self.switch_tab(t["targetId"])  # 已清两层缓存 + 更新 current_*
				return
		await self.create_tab("about:blank")  # G9: 无其他 page，避免 current_* 悬挂
```

> `switch_tab`（`session.py:1264-1275`）/`create_tab`（`session.py:1288-1293`）都清两层 selector_map 缓存并更新 `current_*`，故 auto-switch 路径无需额外动缓存。G9 测试（关最后一个页）断言 `create_tab("about:blank")` 被调用。

### 决策备注

- **`terminates_sequence` 保持 `False`**（对齐 browser-use `close` 的 `terminates_sequence=False`；关当前页时页面上下文虽变，但 auto-switch 路径已清缓存）。不强行改成 True。
- **`default=""` 保留**（空=关当前页是本项目自有便利语义，区别于 browser-use 的 `min_length=4`）。G7 仅补描述，不加 `min_length`。

---

## 4. CDP 调用清单（变更后）

| Action | CDP 命令 | 变化 |
|---|---|---|
| `close_tab` | `Target.getTargets`（枚举，经 `get_tabs`）+ `Target.closeTarget` [+ `switch_tab` 的 `Target.activateTarget` + `Target.attachToTarget(flatten)`，关当前页时] [+ P1 `Target.createTarget`，关最后一个页时] | 枚举从全量 DOM 降为单 `getTargets`；失败软降级不抛 |

---

## 5. 涉及文件清单

| 文件 | 改动 | 对应改进 |
|---|---|---|
| `src/tree_walker/tools/actions.py` | `_action_close_tab` 重写（G1–G6） | G1 / G2 / G3 / G4 / G5 / G6 |
| `src/tree_walker/tools/models.py` | `CloseTabParams.tab_id` 补描述（`default=""` 不变） | G7 |
| `src/tree_walker/browser/session.py` | `close_tab` 补 about:blank 兜底 | G9（P1） |
| `tests/test_close_tab.py` | **新建** | G8 |
| `docs/Tools技术细节/04_动作清单与CDP映射.md` | 4.2 节同步更新回显 / `get_tabs` / 冲突检测 / 软降级描述并刷新过时行号（实施后） | 文档同步 |

---

## 6. 实施后的验证

- 新增/相关测试：`uv run python -m pytest tests/test_close_tab.py -x -v`
- 全量回归：`uv run python -m pytest tests/ -x -v`
- 覆盖率（CLAUDE.md 目标 >85%）：`uv run python -m pytest tests/ --cov=tree_walker.tools --cov=tree_walker.browser`
- 手测：打开多个标签页 → `close_tab {"tab_id": "xxxx"}` 关指定页，确认回显含 `Closed tab [xxxx] title (url)`；后缀撞车时确认 error 列出匹配页；关当前页（`{"tab_id": ""}`）后确认已自动切到剩余页；关最后一个页（P1）后确认新开了 about:blank。
