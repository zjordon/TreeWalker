# 04 动作清单与 CDP 映射

> 本章是 Tools 子系统的核心参考手册，逐一剖析 24 个注册 action 的：暴露给 LLM 的 name、description、Pydantic 参数、主要逻辑、对应的 CDP（Chrome DevTools Protocol）调用。文末附 CDP 调用总览矩阵与关键操作详解。

---

## 4.1 总览表

24 个 action 按 `ACTION_DEFINITIONS` 字典字母序排列。"终止序列" 列对应 `terminates_sequence` 字段，标记为 True 的 action 会切换页面上下文（导航/重载/切 Tab/执行 JS）。

| # | Action | 终止 | 主要参数 | 涉及 CDP 域 | 一句话职责 |
|---|---|---|---|---|---|
| 1 | [click](#41-click) | 否 | `index: int` | DOM + Input + Overlay | 点击元素，含 SELECT 分支 |
| 2 | [close_tab](#42-close_tab) | 否 | `tab_id: str=""` | Target | 关闭 Tab，必要时切换其他 |
| 3 | [done](#43-done) | 否 | `text, success` | (无 CDP) | 任务终止信号 |
| 4 | [dropdown_options](#44-dropdown_options) | 否 | `index: int` | Runtime (JS) | 读取 select 所有 option |
| 5 | [evaluate](#45-evaluate) | 是 | `code: str` | Runtime | 执行任意 JS，返回结果 |
| 6 | [extract](#46-extract) | 否 | `goal: str` | Runtime + LLM | 二次 LLM 抽取页面信息 |
| 7 | [find_elements](#47-find_elements) | 否 | `selector: str` | Runtime (JS) | CSS 选择器查找元素 |
| 8 | [find_text](#48-find_text) | 否 | `text: str` | Runtime (JS) | window.find() 滚动到文本 |
| 9 | [go_back](#49-go_back) | 是 | (无) | Page | 浏览器后退 |
| 10 | [input_text](#410-input_text) | 否 | `index, text, clear` | DOM + Input + Runtime | 点击+输入文本+触发框架事件 |
| 11 | [navigate](#411-navigate) | 是 | `url: str, new_tab?: bool` | Page + Target | 导航到 URL（支持 new_tab、健康检查、错误映射） |
| 12 | [read_file](#412-read_file) | 否 | `path: str` | (本地 fs) | 同步读本地文件 |
| 13 | [replace_file](#413-replace_file) | 否 | `path, old, new` | (本地 fs) | 字符串替换并写回 |
| 14 | [save_as_pdf](#414-save_as_pdf) | 否 | `path: str` | Page | 打印页面为 PDF |
| 15 | [screenshot](#415-screenshot) | 否 | `save_path: str=""` | Page | 截图当前视口 |
| 16 | [scroll](#416-scroll) | 否 | `amount, direction` | Page + Input | 鼠标滚轮翻页 |
| 17 | [search](#417-search) | 是 | `query: str` | Page | 跳转 Google 搜索结果 |
| 18 | [search_page](#418-search_page) | 否 | `query: str` | Runtime (JS) | 在页面正文搜索文本 |
| 19 | [select_dropdown](#419-select_dropdown) | 否 | `index, value` | Runtime (JS) | 设置 select 的 value |
| 20 | [send_keys](#420-send_keys) | 否 | `keys: str` | Input | 发送组合键 |
| 21 | [switch_tab](#421-switch_tab) | 是 | `tab_id: str` | Target | 切换到指定 Tab |
| 22 | [upload_file](#422-upload_file) | 否 | `index, path` | DOM | 设置文件输入 |
| 23 | [wait](#423-wait) | 否 | `seconds: int` | (asyncio.sleep) | 等待 N 秒 |
| 24 | [write_file](#424-write_file) | 否 | `path, content` | (本地 fs) | 同步写本地文件 |

---

## 4.2 Action 详解（按字母序）

每个 action 的章节格式：

- **name**：暴露给 LLM 的工具名
- **description**：英文原文 + 中文翻译
- **Pydantic 参数**：字段 + 默认值 + 描述
- **主要逻辑**：核心代码片段 + 说明
- **CDP 调用清单**：表格（CDP 命令 / 参数 / 行号）
- **注意事项**：边界情况、特殊处理

---

### 4.1 `click`

- **description**：`Click an element by its ID from the DOM state` / 通过 DOM 状态中的元素 ID 点击元素
- **terminates_sequence**：False
- **Pydantic 参数**：

  | 字段 | 类型 | 描述 |
  |---|---|---|
  | `index` | `int` | ID of the element to click, shown in brackets in the DOM tree |

- **主要逻辑**（[actions.py:300-338](../../src/tree_walker/tools/actions.py)，回显 helper `_describe_click` 在 [actions.py:339-360](../../src/tree_walker/tools/actions.py)）：

  ```python
  async def _action_click(self, params: dict, browser: BrowserSession) -> ActionResult:
      entry, error = await self._get_element_by_index(params["index"], browser)
      if error:
          return error
      backend_id = entry.backend_node_id
      # SELECT 分支：精确查"指定 index 的那个 select"，不再全页 querySelectorAll
      if entry.tag_name.upper() == "SELECT":
          try:
              options = await browser.fetch_select_options(backend_id)
          except Exception as e:
              return ActionResult(error=f"Failed to read select options: {e}")
          return ActionResult(extracted_content=str(options))
      # 普通点击：highlight -> click_element，映射 bool 信号
      try:
          await browser.highlight_element(backend_id)
          clicked = await browser.click_element(backend_id)
      except Exception as e:
          return ActionResult(error=f"Click failed: {e}")
      if not clicked:
          return ActionResult(
              error=(
                  f"Could not click element {params['index']} "
                  f"(no coordinates and JS click fallback failed; "
                  f"the element may be detached, hidden, or in a cross-origin iframe)"
              ),
          )
      # 成功回显（对齐 navigate/go_back 风格）
      memory = self._describe_click(entry, params["index"])
      logger.info(memory)
      return ActionResult(extracted_content=memory, long_term_memory=memory)
  ```

  特殊分支：点击 `<select>` 时不真正点击，而是经 `fetch_select_options(backend_id)` **精确读取该 select 的所有 option**（旧实现用 `document.querySelectorAll('select option')` 扫描全页 select，多 select 页面会返回错误 option），让 LLM 接下来用 `select_dropdown`。普通点击成功时回显 `Clicked [TAG] {text} at index N`（`_describe_click` 优先取 aria-label/placeholder/title/alt/value，再取 node_value，再退化为 `[TAG] at index N`）。

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `DOM.scrollIntoViewIfNeeded` | `{backendNodeId}` | session.py:670 |
  | `Page.getLayoutMetrics` | `{}` | session.py:642 |
  | `DOM.getContentQuads` | `{backendNodeId}` | session.py:545（取与视口交集最大的 quad） |
  | `DOM.getBoxModel` (fallback) | `{backendNodeId}` | session.py:562 |
  | `DOM.resolveNode` + `Runtime.callFunctionOn` (fallback) | `getBoundingClientRect()` | session.py:580, 587 |
  | `DOM.resolveNode` + `Runtime.callFunctionOn`（遮挡检查） | `document.elementFromPoint(x,y)` 祖先链 | session.py:721, 728（`_is_element_occluded`） |
  | `DOM.resolveNode` + `Runtime.callFunctionOn`（JS 回退 / SELECT） | `this.click()` / `this.options` | session.py:769, 776（`_js_click`）、session.py:1265, 1272（`fetch_select_options`） |
  | `Input.dispatchMouseEvent` × 3 | `{type, x, y[, button:left, clickCount:1]}` | session.py:485-513（mouseMoved → mousePressed → mouseReleased） |
  | `Overlay.highlightNode` (可选) | `{highlightConfig, backendNodeId}` | highlight.py:35 |

- **注意事项**：
  - 鼠标序列为 `mouseMoved → mousePressed → mouseReleased`（对齐 browser-use `default_action_watchdog.py:902-955`）；前置 `mouseMoved` 触发 hover 菜单 / mousemove 监听器 / 反爬检测。
  - 坐标计算用三层 fallback 链（详见 4.3.1）；Method 1 取**与视口交集最大的 quad**（不再取 `quads[0]`）；几何中心裁剪到 `[0, viewport-1]`。
  - 点击点被遮挡（固定 header/footer、弹窗、`<label>` 包 `<input>` 等）时跳过几何点击，回退 `this.click()`；坐标拿不到也走 JS 回退。**坐标拿不到 + JS 回退均失败 → 明确 `ActionResult(error=...)`，不再静默成功**。
  - 成功回显 `Clicked [TAG] {text} at index N`（对齐 navigate/go_back）。

---

### 4.2 `close_tab`

- **description**：`Close a browser tab` / 关闭浏览器标签页
- **terminates_sequence**：False
- **Pydantic 参数**：

  | 字段 | 类型 | 默认 | 描述 |
  |---|---|---|---|
  | `tab_id` | `str` | `""` | Tab ID (last 4 characters) to close. Empty string closes the current tab |

- **主要逻辑**（[actions.py:659-698](../../src/tree_walker/tools/actions.py)）：

  ```python
  async def _action_close_tab(self, params: dict, browser: BrowserSession) -> ActionResult:
      tab_id_suffix = params.get("tab_id", "")
      tabs = await browser.get_tabs()  # 轻量枚举（单 Target.getTargets），不再 get_state
      if tab_id_suffix:
          matches = [t for t in tabs if t.target_id.endswith(tab_id_suffix)]
          if not matches:
              return ActionResult(error=f"No tab ending with '{tab_id_suffix}'. "
                                        f"Open tabs: {self._summarize_tabs(tabs)}")
          if len(matches) > 1:  # 后缀撞车：关错页风险，要求更长后缀/完整 target_id
              return ActionResult(error=f"Multiple tabs match '{tab_id_suffix}' ...")
          target = matches[0]
          target_id, id_echo, title, url = (target.target_id, tab_id_suffix, target.title, target.url)
      else:  # 空 tab_id = 关当前页
          if not browser.current_target_id:
              return ActionResult(error="No current tab to close")
          target_id = browser.current_target_id
          ...
      try:
          await browser.close_tab(target_id)
      except Exception as e:  # 失效 target 软降级（关闭幂等），对齐 browser-use
          logger.warning("close_tab(%s) failed: %s", target_id, e)
          memory = f"Tab [{id_echo}] {title} ({url}) was already closed or invalid"
          return ActionResult(extracted_content=memory, long_term_memory=memory)
      memory = f"Closed tab [{id_echo}] {title} ({url})"
      logger.info(memory)
      return ActionResult(extracted_content=memory, long_term_memory=memory)
  ```

  四路径：① 指定 `tab_id` 经轻量 `get_tabs()`（[session.py:1244](../../src/tree_walker/browser/session.py)）后缀匹配，撞车报错（`len(matches) > 1`，**比 browser-use 取首个匹配更严**），未命中 error 列出现有标签页（复用 `_summarize_tabs`）；② 空 `tab_id` 关 `current_target_id`（保留原"空=关当前页"语义，补回显）；③ 关闭成功回显 `Closed tab [{id}] {title} ({url})`（写入 `extracted_content` + `long_term_memory`，对齐 navigate/click/go_back/switch_tab）；④ `browser.close_tab` 抛异常（target 已被外部关闭/失效）→ 软成功回显 `was already closed or invalid`（非 error，对齐 browser-use `service.py:1011-1018`）。

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `Target.getTargets`（经 `get_tabs`，枚举标签页） | `{}` | session.py:1252 |
  | `Target.closeTarget` | `{targetId}` | session.py:1280 |
  | `Target.getTargets` (关闭当前 tab 后找剩余页) | `{}` | session.py:1282 |
  | `Target.activateTarget` + `Target.attachToTarget` (切到其他 tab，经 `switch_tab`) | `{targetId, flatten:True}` | session.py:1268-1269 |
  | `Target.createTarget` (无剩余 page 时建 about:blank 兜底，经 `create_tab`) | `{url:"about:blank"}` | session.py:1290 |

- **注意事项**：
  - 成功回显 `Closed tab [{id}] {title} ({url})`（对齐 navigate/click/go_back/switch_tab），title/url 取自 `get_tabs()` 快照，零额外 CDP 调用。
  - 后缀撞车直接报错（`len(matches) > 1`），不取首个匹配，避免关错页；未命中时 error 列出现有标签页（`_summarize_tabs`）便于 LLM 重选（对齐 switch_tab）。
  - 失效 target 软降级：`browser.close_tab` 抛异常时返回软成功回显（关闭幂等），不外抛 error，对齐 browser-use。
  - 关闭当前 tab 时，自动切换到第一个剩余的 page target（经 `switch_tab`，已清两层 selector_map 缓存）；**无其他 page 时建 `about:blank` 兜底**（G9），避免 `current_target_id`/`current_session_id` 悬挂指向已死页。
  - 保留 `tab_id` 默认空（空=关当前页，本项目自有便利语义，区别于 browser-use 的 `min_length=4,max_length=4`）；`terminates_sequence` 保持 False（对齐 browser-use；关当前页时 auto-switch 路径已清缓存）。

---

### 4.3 `done`

- **description**：`Signal that the task is complete with a summary` / 表示任务完成，附总结
- **terminates_sequence**：False（注意：本身不"终止序列"，但通过 `is_done=True` 触发 Agent Loop 退出）
- **Pydantic 参数**：

  | 字段 | 类型 | 默认 | 描述 |
  |---|---|---|---|
  | `text` | `str` | (必填) | Final summary. ONLY report data you directly observed in page state, tool outputs, or screenshots during this session. |
  | `success` | `bool` | `True` | Whether the task was completed successfully |

- **主要逻辑**（[actions.py:477-482](../../src/tree_walker/tools/actions.py)）：

  ```python
  async def _action_done(self, params: dict, browser: BrowserSession) -> ActionResult:
      return ActionResult(
          is_done=True,
          success=params.get("success", True),
          extracted_content=params.get("text", ""),
      )
  ```

- **CDP 调用清单**：无（纯状态信号）

- **注意事项**：
  - `text` 字段的描述强调"只能报告直接观察到的数据"，防止 LLM 编造结果
  - `_post_process` 通过 `any(r.is_done for r in results)` 检测并触发主循环退出
  - 在循环检测中豁免（`_LOOP_EXEMPT_ACTIONS`），即使连续 done 也不会触发循环警告

---

### 4.4 `dropdown_options`

- **description**：`Get all options from a select dropdown element` / 获取 select 下拉元素的所有选项
- **terminates_sequence**：False
- **Pydantic 参数**：

  | 字段 | 类型 | 描述 |
  |---|---|---|
  | `index` | `int` | ID of the select element, shown in brackets in the DOM tree |

- **主要逻辑**（[actions.py:337-346](../../src/tree_walker/tools/actions.py)）：

  ```python
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
  ```

  注意：JS 实际查询的是**页面上所有 select**，而非指定 index 的那一个。这是一个已知限制，LLM 需要根据上下文判断。

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `Runtime.evaluate` | `{expression, returnByValue:True, awaitPromise:True, timeout:30000}` | session.py:785 |

- **注意事项**：返回的 `options` 是 Python 字符串形式（`str(list)`），LLM 需要自行解析。

---

### 4.5 `evaluate`

- **description**：`Execute JavaScript code in the browser and return the result` / 在浏览器中执行 JavaScript 并返回结果
- **terminates_sequence**：True（执行任意 JS 可能改变页面状态）
- **Pydantic 参数**：

  | 字段 | 类型 | 描述 |
  |---|---|---|
  | `code` | `str` | JavaScript code to execute in the browser |

- **主要逻辑**（[actions.py:452-458](../../src/tree_walker/tools/actions.py)）：

  ```python
  async def _action_evaluate(self, params: dict, browser: BrowserSession) -> ActionResult:
      code = params["code"]
      try:
          result = await browser.execute_js(code)
          return ActionResult(extracted_content=str(result)[:self._truncation.eval_result_max_chars])
      except Exception as e:
          return ActionResult(error=str(e))
  ```

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `Runtime.evaluate` | `{expression: code, returnByValue:True, awaitPromise:True, timeout:30000}` | session.py:785 |

- **注意事项**：
  - 超时硬编码 30 秒（`session.py:790`）
  - JS 抛异常时返回 `RuntimeError`，被包装为 `ActionResult(error=...)`
  - 结果字符串截断到 `eval_result_max_chars`，避免上下文爆炸

---

### 4.6 `extract`

- **description**：`Extract specific information from the current page content` / 从当前页面内容中抽取指定信息
- **terminates_sequence**：False
- **Pydantic 参数**：

  | 字段 | 类型 | 描述 |
  |---|---|---|
  | `goal` | `str` | What information to extract from the current page |

- **主要逻辑**（[actions.py:239-253](../../src/tree_walker/tools/actions.py)）：

  ```python
  async def _action_extract(self, params: dict, browser: BrowserSession) -> ActionResult:
      goal = params["goal"]
      try:
          page_text = await browser.execute_js("document.body.innerText")
      except Exception:
          page_text = ""
      if not page_text:
          return ActionResult(extracted_content="(empty page)")
      from tree_walker.llm.client import LLMClient
      llm = getattr(self, "_extract_llm", None)
      if llm:
          result = await llm.extract(goal, page_text[:self._truncation.extract_page_max_chars])
          return ActionResult(extracted_content=result)
      return ActionResult(extracted_content=page_text[:self._truncation.extract_fallback_max_chars])
  ```

  二次调用 LLM 进行抽取（注入的 `_extract_llm` 通常是低成本小模型）。如未配置则降级返回前 N 个字符。

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `Runtime.evaluate` | `{expression: "document.body.innerText"}` | session.py:785 (经 execute_js) |

  二次 LLM 调用走 Anthropic `messages.create`（[client.py:262-278](../../src/tree_walker/llm/client.py)）。

- **注意事项**：`_extract_llm` 默认未设置，需要在 `Tools` 构造后手动注入；否则走 fallback 路径。

---

### 4.7 `find_elements`

- **description**：`Find elements on the page using a CSS selector` / 使用 CSS 选择器查找页面元素
- **terminates_sequence**：False
- **Pydantic 参数**：

  | 字段 | 类型 | 描述 |
  |---|---|---|
  | `selector` | `str` | CSS selector to find elements on the page |

- **主要逻辑**（[actions.py:289-302](../../src/tree_walker/tools/actions.py)）：

  ```python
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
  ```

  返回每个匹配元素的索引、tag、文本（截断 100 字符）、href、是否可见。

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `Runtime.evaluate` | `{expression: js_code}` | session.py:785 |

- **注意事项**：`repr(selector)` 会用 Python 字符串字面量包装，避免 JS 注入风险。

---

### 4.8 `find_text`

- **description**：`Scroll to and highlight text on the page` / 滚动到并高亮页面上的文本
- **terminates_sequence**：False
- **Pydantic 参数**：

  | 字段 | 类型 | 描述 |
  |---|---|---|
  | `text` | `str` | Text to search for on the page |

- **主要逻辑**（[actions.py:304-314](../../src/tree_walker/tools/actions.py)）：

  ```python
  async def _action_find_text(self, params: dict, browser: BrowserSession) -> ActionResult:
      text = params["text"]
      try:
          found = await browser.execute_js(f"window.find({repr(text)})")
          if found:
              return ActionResult()
          return ActionResult(error=f"Text '{text}' not found on page")
      except Exception as e:
          return ActionResult(error=str(e))
  ```

  利用浏览器原生的 `window.find()` 文本搜索（Ctrl+F 等价）。

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `Runtime.evaluate` | `{expression: "window.find('...')"}` | session.py:785 |

- **注意事项**：`window.find()` 是非标准 API，Chrome 支持；某些浏览器版本行为略有差异。

---

### 4.9 `go_back`

- **description**：`Navigate back to the previous page in history` / 浏览器后退到上一页
- **terminates_sequence**：True
- **Pydantic 参数**：无

- **主要逻辑**（[actions.py:387-402](../../src/tree_walker/tools/actions.py)）：

  ```python
  async def _action_go_back(self, params: dict, browser: BrowserSession) -> ActionResult:
      try:
          target_url = await browser.go_back()
      except Exception as e:
          return ActionResult(error=f"Failed to go back: {e}")
      if target_url is None:                                        # 无历史可退
          return ActionResult(error="No previous page in history to go back to")
      await self._go_back_health_check(target_url, browser)         # 轻量空 DOM 检测
      memory = f"Navigated back to {target_url}"
      logger.info(memory)
      return ActionResult(extracted_content=memory, long_term_memory=memory)
  ```

  `_go_back_health_check`（[actions.py:404](../../src/tree_walker/tools/actions.py)）仅当后退目标为 http(s) 时触发，复用 `_dom_appears_empty`：空则等一次 `_NAVIGATE_EMPTY_RETRY_WAIT`(3s) 重查，仍空只 `logger.warning` 不硬失败（go_back 无"reload 同 URL"的干净机制）。

  委托给 `BrowserSession.go_back()`（[session.py:410-435](../../src/tree_walker/browser/session.py)）：

  ```python
  async def go_back(self) -> str | None:
      self._cached_selector_map = None          # 清两层缓存（与 navigate / switch_tab 一致）
      self._previous_cached_selector_map = None
      history = await self.client.send.Page.getNavigationHistory({}, ...)
      idx = history.get("currentIndex", 0)
      entries = history.get("entries", [])
      if idx <= 0 or not entries:
          return None                           # 无历史可退
      prev = entries[idx - 1]
      await self.client.send.Page.navigateToHistoryEntry({"entryId": prev["id"]}, ...)
      await self._wait_for_page_settle()        # 轮询 readyState，不再是硬编码 sleep
      return prev.get("url")                    # 回显后退目标 URL（零额外 CDP 调用）
  ```

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `Page.getNavigationHistory` | `{}` | session.py:422 |
  | `Page.navigateToHistoryEntry` | `{entryId}` | session.py:430 |
  | `Runtime.evaluate`（经 `_wait_for_page_settle`） | `{expression: document.readyState}` | session.py:459 |

  > session 层在取历史前清空 `_cached_selector_map` / `_previous_cached_selector_map`（对齐 navigate / switch_tab），避免旧 selector_map 残留导致新元素检测误判。

- **注意事项**：
  - `idx <= 0`（无历史可退）时 `go_back` 返回 `None`，action 层转为明确 `error="No previous page in history to go back to"`（不再静默成功，避免 LLM 误以为已后退）。
  - 后退后用 `_wait_for_page_settle()` 轮询 `document.readyState`（**不再是硬编码 `sleep(0.3)`**）。
  - 成功后退回显 `Navigated back to {url}`（URL 取自 `entries[idx-1]["url"]`，零额外 CDP 调用）到 `extracted_content` + `long_term_memory`。
  - 轻量健康检查：SPA 未渲染时空→等一次 3s→仍空仅 warning 不硬失败；非 http(s) 目标（chrome:// 等）跳过。

---

### 4.10 `input_text`

- **description**：`Type text into an input element identified by ID` / 向指定 ID 的输入框输入文本
- **terminates_sequence**：False
- **Pydantic 参数**：

  | 字段 | 类型 | 默认 | 描述 |
  |---|---|---|---|
  | `index` | `int` | (必填) | ID of the element to type into, shown in brackets in the DOM tree |
  | `text` | `str` | (必填) | Text to type into the element |
  | `clear` | `bool` | `True` | Whether to clear existing text first |

- **主要逻辑**（[actions.py:415](../../src/tree_walker/tools/actions.py) 的 `_action_input_text` + [`_describe_input`](../../src/tree_walker/tools/actions.py)/`_is_autocomplete_field`（actions.py:364/392）；session 层判定 `_requires_direct_value_assignment` 在 [session.py:147](../../src/tree_walker/browser/session.py)）：

  ```python
  async def _action_input_text(self, params, browser):
      entry, error = await self._get_element_by_index(params["index"], browser)
      if error:
          return error
      text, clear = params["text"], params.get("clear", True)
      # 1. 聚焦：highlight -> click_element，映射 bool（对齐 _action_click）
      try:
          await browser.highlight_element(entry.backend_node_id)
          clicked = await browser.click_element(entry.backend_node_id)
      except Exception as e:
          return ActionResult(error=f"Input focus failed: {e}")
      if not clicked:
          return ActionResult(error=f"Could not focus element {params['index']} ...")
      await asyncio.sleep(0.1)
      # 2. 输入：date/time 等特殊输入走直写，否则逐字符 type_text
      try:
          if _requires_direct_value_assignment(entry):
              if clear:
                  await browser._clear_text_field()
              await browser._force_set_value(text)
          else:
              await browser.type_text(text, clear=clear)
      except Exception as e:
          return ActionResult(error=f"Failed to type text into element {params['index']}: {e}")
      # 3. autocomplete/combobox：JS 驱动型睡 0.4s 等下拉
      is_combo, needs_js_wait = self._is_autocomplete_field(entry)
      if needs_js_wait:
          await asyncio.sleep(0.4)
      # 4. 值校验 + 成功回显
      memory = self._describe_input(entry, params["index"], text)
      actual = await browser._read_active_text()
      if actual and actual != text:
          memory += "  ⚠️ Note: the field's actual value '...' differs from ..."
      if is_combo:
          memory += "  💡 autocomplete field — select from the dropdown ..."
      logger.info(memory)
      return ActionResult(extracted_content=memory, long_term_memory=memory)
  ```

  四阶段：① 点击聚焦（`click_element` 返回 `bool`，失败/异常 → 明确 `error`，对齐 click，不再静默成功）→ ② 输入（date/time 等特殊输入走 `_force_set_value` 直写，否则逐字符 `type_text`）→ ③ combobox 检测（JS 驱动型睡 0.4s 等下拉）→ ④ 读回值校验（不匹配追加 `⚠️ Note`）+ 回显 `Typed '...' into [TAG] ...`。

- **CDP 调用清单**（聚焦/清空/逐字符/框架事件/值读回，均委托 session 层方法）：

  | CDP 命令 | 主要参数 | 委托方法 / 行号 |
  |---|---|---|
  | `DOM.scrollIntoViewIfNeeded` + `getContentQuads` + `Input.dispatchMouseEvent`×3 | 聚焦点击（mouseMoved→mousePressed→mouseReleased） | `click_element` session.py:702（内部与 4.1 click 共用，详见 4.3.1）|
  | `Input.dispatchKeyEvent` (clear 三层策略) | Ctrl+A / Delete / Backspace 等 | `_clear_text_field` session.py:947 |
  | `Input.dispatchKeyEvent` × 3 (每字符) | `{keyDown/char/keyUp, key, code, windowsVirtualKeyCode}` | `_type_char` session.py:1073 |
  | `Runtime.evaluate` (框架事件) | `{expression: _trigger_framework_events JS}` | session.py:1114 |
  | `Runtime.evaluate` (值读回) | 读 `activeElement.value`/`textContent` | `_read_active_text` session.py:876 |
  | `Runtime.evaluate` (date 直写) | React 原生 setter + input/change | `_force_set_value` session.py:897（date/time 路径）|

- **注意事项**：
  - **成功回显** `Typed '...' into [TAG] {aria-label|placeholder|title|node_value} at index N`（`_describe_input`，对齐 click/navigate/go_back，text 与 label 各截断 60 字）；不设 `success=True`（`ActionResult` 校验器对非 done 动作拒绝）。
  - **聚焦失败 → 明确 error**：`click_element` 返回 `False`（坐标拿不到 + JS 回退失败）或抛异常 → `ActionResult(error=...)`，不再静默成功。
  - **值校验**：输入后读回 `activeElement.value`（或 `textContent`），与预期不符时追加 `⚠️ Note: the field's actual value '...' differs ...`，提示 LLM 重新观察（读回为空说明读失败，静默不打扰）。
  - **date/time/特殊输入走直写**：`_requires_direct_value_assignment` 检测 `<input type=date|time|datetime-local|month|week|color|range>` 与 jQuery/Bootstrap 日期选择器（class 含 `datepicker`/`daterangepicker`/`datetimepicker`/`bootstrap-datepicker` 或有 `data-datepicker`/`data-date-format`/`data-provide` 属性），改用 `_force_set_value`（React 原生 setter + input/change，对齐 browser-use `_set_value_directly`）而非逐字符——这类输入拒收 per-char key 事件，逐字符只会留下残值。
  - **autocomplete/combobox**：`_is_autocomplete_field` 检测后给 LLM 追加 `💡 autocomplete field` 提示；JS 驱动型（`role=combobox` 或非 `none` 的 `aria-autocomplete`）额外睡 0.4s 等下拉填充，原生 `<datalist>`（`list` 属性）与松散 `aria-haspopup`+`aria-controls` 即时渲染不等待（对齐 browser-use service.py:404-417）。
  - 非 ASCII 字符（CJK）只发 `char` 事件，跳过 keyDown/keyUp（`_type_char` session.py:1073）；每字符间隔 1ms 避免事件丢失。
  - 输入完成后 `_trigger_framework_events` 触发 Vue/React 兼容事件，详见 4.3.2；`type_text` 末尾还有拼接自愈（读回发现 OLD+NEW 拼接则 `_force_set_value` 强写，对齐 browser-use auto-retry）。

---

### 4.11 `navigate`

- **description**：`Navigate to a URL in the current tab, or open it in a new tab with new_tab=True` / 在当前标签页导航到 URL，或用 new_tab=True 在新标签页打开
- **terminates_sequence**：True
- **Pydantic 参数**：

  | 字段 | 类型 | 默认 | 描述 |
  |---|---|---|---|
  | `url` | `str` | (必填) | The URL to navigate to |
  | `new_tab` | `bool` | `False` | If True, open the URL in a new tab instead of navigating the current tab |

- **主要逻辑**（[actions.py:223-241](../../src/tree_walker/tools/actions.py)）：

  ```python
  async def _action_navigate(self, params: dict, browser: BrowserSession) -> ActionResult:
      url = params["url"]
      if not url.startswith(("http://", "https://")):
          url = "https://" + url
      new_tab = params.get("new_tab", False)
      try:
          await browser.navigate(url, new_tab=new_tab)
          if not new_tab:                       # 仅当前标签页 + http(s) 触发健康检查
              await self._navigate_health_check(url, browser)
          memory = f"Opened new tab with URL {url}" if new_tab else f"Navigated to {url}"
          return ActionResult(extracted_content=memory, long_term_memory=memory)
      except Exception as e:
          return self._map_navigation_error(url, e)
  ```

  自动补全 `https://` 协议；导航成功后回显 URL（`extracted_content` + `long_term_memory`），让 LLM 从 result 确认目的地。

- **健康检查 `_navigate_health_check`**（[actions.py:254-290](../../src/tree_walker/tools/actions.py)，仅 `new_tab=False` 且 URL 为 http(s) 时触发，三阶段判定逐渐收严）：

  1. `get_state(include_screenshot=False)` 取状态，若 `_dom_appears_empty`（`dom_state._root is None` 或 `element_tree_text` 为空）→ 等 `_NAVIGATE_EMPTY_RETRY_WAIT`(3s) 重查；
  2. 仍空 → 重新 `browser.navigate(url)`（reload，异常吞掉）+ 等 `_NAVIGATE_EMPTY_RELOAD_WAIT`(5s)；
  3. 仍 `_root is None` → 抛 `RuntimeError("Page loaded but returned empty content ...")`，覆盖 SPA 未渲染 / 反爬 / 隧道代理异常等空页场景。

- **错误映射 `_map_navigation_error`**（[actions.py:292-298](../../src/tree_walker/tools/actions.py)）：异常信息命中 `_NAVIGATE_NET_ERROR_MARKERS`（`ERR_NAME_NOT_RESOLVED` / `ERR_CONNECTION_REFUSED` / `ERR_TIMED_OUT` / `ERR_TUNNEL_CONNECTION_FAILED` / `ERR_INTERNET_DISCONNECTED` / `net::`）→ `ActionResult(error="Navigation failed - site unavailable: {url}")`；其余异常保留原始信息。

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `Page.navigate` | `{url, transitionType:'address_bar'}` | session.py:396 |
  | `Target.createTarget` (new_tab=True) | `{url:'about:blank'}` | session.py:1033（经 `create_tab`） |

  导航失败时 `Page.navigate` 返回值含 `errorText`（CDP："present iff navigation has failed"），由 `BrowserSession.navigate`（[session.py:382-415](../../src/tree_walker/browser/session.py)）检测并抛 `RuntimeError`。

- **注意事项**：
  - `transitionType:'address_bar'` 模拟地址栏输入，保证历史/过渡语义正确（对齐 browser-use）。
  - 导航后由 `_wait_for_page_settle()`（[session.py:424](../../src/tree_walker/browser/session.py)，轮询 `document.readyState`）等待，**不再是硬编码 `sleep`**。
  - `navigate` 同时清空 `_cached_selector_map` 与 `_previous_cached_selector_map`（与 `switch_tab` 一致），避免旧 selector_map 残留。
  - `new_tab=True` 复用 `create_tab`：先开 `about:blank` 新标签页（`Target.createTarget` + `switch_tab`），再 `Page.navigate` 到真实 URL——两步走是为了保留 `errorText` 检查与统一 settle。

---

### 4.12 `read_file`

- **description**：`Read content from a local file` / 读取本地文件内容
- **terminates_sequence**：False
- **Pydantic 参数**：

  | 字段 | 类型 | 描述 |
  |---|---|---|
  | `path` | `str` | File path to read |

- **主要逻辑**（[actions.py:431-438](../../src/tree_walker/tools/actions.py)）：

  ```python
  async def _action_read_file(self, params: dict, browser: BrowserSession) -> ActionResult:
      path = params["path"]
      try:
          with open(path, "r", encoding="utf-8") as f:
              content = f.read()
          return ActionResult(extracted_content=content[:self._truncation.read_file_max_chars])
      except FileNotFoundError:
          return ActionResult(error=f"File not found: {path}")
  ```

- **CDP 调用清单**：无（同步本地文件操作）

- **注意事项**：内容截断到 `read_file_max_chars`；只处理 `FileNotFoundError`，其他异常会被外层 `execute` 兜底捕获。

---

### 4.13 `replace_file`

- **description**：`Replace text within a local file` / 替换本地文件中的文本
- **terminates_sequence**：False
- **Pydantic 参数**：

  | 字段 | 类型 | 描述 |
  |---|---|---|
  | `path` | `str` | File path |
  | `old` | `str` | Text to find and replace |
  | `new` | `str` | Replacement text |

- **主要逻辑**（[actions.py:440-450](../../src/tree_walker/tools/actions.py)）：

  ```python
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
  ```

  简单的 `str.replace`，**全局替换**所有匹配。

- **CDP 调用清单**：无

- **注意事项**：替换所有匹配项；不保留备份；非原子操作（写到一半异常会导致数据损坏）。

---

### 4.14 `save_as_pdf`

- **description**：`Save the current page as a PDF file` / 将当前页面保存为 PDF
- **terminates_sequence**：False
- **Pydantic 参数**：

  | 字段 | 类型 | 描述 |
  |---|---|---|
  | `path` | `str` | File path to save the PDF |

- **主要逻辑**（[actions.py:325-335](../../src/tree_walker/tools/actions.py)）：

  ```python
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
  ```

  注意：这里直接调底层 CDP，而非通过 `BrowserSession` 封装方法（PDF 没有专门封装）。

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `Page.printToPDF` | `{printBackground: True}` | actions.py:327 |

- **注意事项**：`printBackground=True` 保留背景色；返回的 `data` 是 base64 字符串，需手动解码为字节再写文件。

---

### 4.15 `screenshot`

- **description**：`Take a screenshot of the current viewport` / 截图当前视口
- **terminates_sequence**：False
- **Pydantic 参数**：

  | 字段 | 类型 | 默认 | 描述 |
  |---|---|---|---|
  | `save_path` | `str` | `""` | Optional file path to save the screenshot |

- **主要逻辑**（[actions.py:316-323](../../src/tree_walker/tools/actions.py)）：

  ```python
  async def _action_screenshot(self, params: dict, browser: BrowserSession) -> ActionResult:
      screenshot_bytes = await browser.take_screenshot()
      save_path = params.get("save_path", "")
      if save_path:
          with open(save_path, "wb") as f:
              f.write(screenshot_bytes)
          return ActionResult(extracted_content=f"Screenshot saved to {save_path}")
      return ActionResult()
  ```

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `Page.captureScreenshot` | `{format: "png"}` | session.py:372 |

- **注意事项**：未指定 `save_path` 时仅执行截图但不保存（用于触发浏览器渲染）；图片格式固定 PNG。

---

### 4.16 `scroll`

- **description**：`Scroll the page up or down by a number of viewport-heights` / 上下滚动页面若干视口高度
- **terminates_sequence**：False
- **Pydantic 参数**：

  | 字段 | 类型 | 默认 | 描述 |
  |---|---|---|---|
  | `amount` | `int`（`ge=1, le=10`） | `3` | 滚动视口高度数；参照 DOM 树可滚动元素的滚动信息（如 `3.4 pages below`）判断剩余量 |
  | `direction` | `Literal["up","down"]` | `"down"` | 滚动方向：`down`（默认）或 `up` |

- **主要逻辑**（[actions.py:593-610](../../src/tree_walker/tools/actions.py)）：成功回显 + 当轮位置/边界提示 + try/except 软降级。

  ```python
  async def _action_scroll(self, params: dict, browser: BrowserSession) -> ActionResult:
      direction = params.get("direction", "down")
      amount = int(params.get("amount", 3))
      try:
          position = await browser.scroll(direction, amount)  # {vertical_percentage, at_edge}
      except Exception as e:
          logger.warning("scroll(%s, %d) failed: %s", direction, amount, e)
          return ActionResult(error=f"Scroll failed: {e}")  # scroll 非幂等 → error
      memory = f"Scrolled {direction} {amount} viewport-heights"
      if position.get("vertical_percentage") is not None:
          memory += f" ({position['vertical_percentage']}% down)"
      if position.get("at_edge"):
          memory += f" (already at {direction}, no further content)"
      logger.info(memory)
      return ActionResult(extracted_content=memory, long_term_memory=memory)
  ```

  委托给 `BrowserSession.scroll()`（[session.py:1218-1289](../../src/tree_walker/browser/session.py)）：返回 `{vertical_percentage, at_edge}`，末尾追加 1 次 `Runtime.evaluate` 读 scrollY/scrollHeight/clientHeight 判边界（documentElement/body 取 max 兼容 quirks 模式）；读取失败退化为 `{None, False}` 不影响滚动。

  ```python
  async def scroll(self, direction="down", amount=3) -> dict:
      sid = self.current_session_id
      metrics = await self.client.send.Page.getLayoutMetrics({}, session_id=sid)
      viewport = metrics.get("cssVisualViewport", {})
      viewport_height = viewport.get("clientHeight", 1000)  # fallback 对齐 browser-use
      delta = amount * viewport_height
      if direction == "up":
          delta = -delta
      await self.client.send.Input.dispatchMouseEvent({
          "type": "mouseWheel",
          "x": viewport.get("clientWidth", 1280) / 2,
          "y": viewport_height / 2,
          "deltaX": 0,
          "deltaY": delta,
      }, session_id=sid)
      await asyncio.sleep(0.2)  # 不用 _wait_for_page_settle（轮询 readyState，scroll 不改）
      position = {"vertical_percentage": None, "at_edge": False}
      try:
          result = await self.client.send.Runtime.evaluate({
              "expression": "(()=>{const d=document.documentElement,b=document.body;const sy=Math.max(d.scrollTop||0,b?b.scrollTop||0:0);const sh=Math.max(d.scrollHeight||0,b?b.scrollHeight||0:0);const ch=d.clientHeight||window.innerHeight||0;const m=sh-ch;return JSON.stringify({sy,sh,ch,pct:m>0?(sy/m)*100:100});})()",
              "returnByValue": True,
          }, session_id=sid)
          val = json.loads(result.get("result", {}).get("value") or "{}")
          sy, sh, ch = val.get("sy", 0), val.get("sh", 0), val.get("ch", 0)
          max_top = sh - ch
          position["vertical_percentage"] = round((sy / max_top) * 100, 1) if max_top > 0 else 100.0
          position["at_edge"] = (sy + ch >= sh - 1) if direction == "down" else (sy <= 1)
      except Exception:
          pass
      return position
  ```

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `Page.getLayoutMetrics` | `{}` | session.py:1221 |
  | `Input.dispatchMouseEvent` | `{type:mouseWheel, x, y, deltaX:0, deltaY}` | session.py:1233 |
  | `Runtime.evaluate`（G2 新增，读 scrollY/scrollHeight/clientHeight 判边界） | `{expression, returnByValue:True}` | session.py:1252 |

- **注意事项**：
  - 滚动量单位是**视口高度**而非像素；鼠标位置固定在视口中心。
  - **当轮到底提示**：`scroll` 返回 `{vertical_percentage, at_edge}`，action 层据此回显百分比并在已到边界时追加 `(already at ...)`，比"完全靠 DOM 层 `pages_below` 下一轮告知"早一轮；边界完整信号（pages_below/total_pages）仍由 DOM 滚动信息层（`browser/views.py` 的 `get_scroll_info_text`）提供，action 层不重复。
  - **异常处理**：scroll 非幂等，CDP 失败 → `ActionResult(error="Scroll failed: ...")`（区别于幂等的 close_tab 软成功）。
  - **等待机制**：用固定 `sleep(0.2)`，不用 `_wait_for_page_settle`（它轮询 readyState，而 scroll 不改 readyState）。
  - **G2 局限**：虚拟滚动列表 / SPA 路由切换 / 主滚动在 iframe 内时 `at_edge` 可能误判，仅作提示非权威，DOM 层兜底；元素内滚动（`index` 参数）与平滑滚动留待 P1。

---

### 4.17 `search`

- **description**：`Search the web using a search engine (navigates to search results)` / 用搜索引擎搜索（跳转到搜索结果页）
- **terminates_sequence**：True
- **Pydantic 参数**：

  | 字段 | 类型 | 描述 |
  |---|---|---|
  | `query` | `str` | Search query to type into the search engine |

- **主要逻辑**（[actions.py:233-237](../../src/tree_walker/tools/actions.py)）：

  ```python
  async def _action_search(self, params: dict, browser: BrowserSession) -> ActionResult:
      query = params["query"]
      url = f"https://www.google.com/search?q={query}"
      await browser.navigate(url)
      return ActionResult()
  ```

  简单的 URL 拼接，**不做 URL 编码**（query 含特殊字符时可能出错）。

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `Page.navigate` | `{url}` | session.py:383 |

- **注意事项**：搜索引擎硬编码 Google；如需其他引擎（百度、Bing），LLM 应直接 `navigate` 到对应搜索 URL。

---

### 4.18 `search_page`

- **description**：`Search for text within the current page content` / 在当前页面内容中搜索文本
- **terminates_sequence**：False
- **Pydantic 参数**：

  | 字段 | 类型 | 描述 |
  |---|---|---|
  | `query` | `str` | Text to search within the current page |

- **主要逻辑**（[actions.py:460-475](../../src/tree_walker/tools/actions.py)）：

  ```python
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
  ```

  返回最多 20 行匹配，大小写不敏感。

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `Runtime.evaluate` | `{expression: js_code}` | session.py:785 |

- **注意事项**：与 `find_text` 区别：`find_text` 用浏览器内置 `window.find()` 视觉滚动+高亮；`search_page` 是文本搜索返回匹配行。

---

### 4.19 `select_dropdown`

- **description**：`Select an option in a dropdown element` / 在下拉元素中选择一个选项
- **terminates_sequence**：False
- **Pydantic 参数**：

  | 字段 | 类型 | 描述 |
  |---|---|---|
  | `index` | `int` | ID of the select element |
  | `value` | `str` | Option value to select |

- **主要逻辑**（[actions.py:348-357](../../src/tree_walker/tools/actions.py)）：

  ```python
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
  ```

  注意：`entry` 仅做了存在性校验，实际操作的是**页面上第一个 select**（`querySelectorAll('select')[0]`），可能与 `index` 不一致。这是已知限制。

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `Runtime.evaluate` | `{expression: "select.value=...; dispatchEvent('change')"}` | session.py:785 |

- **注意事项**：必须手动 dispatch `change` 事件，否则 Vue/React 框架不会响应。

---

### 4.20 `send_keys`

- **description**：`Send keyboard shortcuts or key combinations` / 发送键盘快捷键或组合键
- **terminates_sequence**：False
- **Pydantic 参数**：

  | 字段 | 类型 | 描述 |
  |---|---|---|
  | `keys` | `str`（`min_length=1`） | 组合键/命名键/纯文本。组合用 `+`：`Control+a`、`Shift+T`、`Alt+F4`；命名键：`Enter`、`Tab`、`Escape`、`ArrowUp`、`F5` 等；纯文本（如 `hello`）逐字符输入 |

- **主要逻辑**（[actions.py:640-653](../../src/tree_walker/tools/actions.py)）：成功回显 + try/except 软降级（非幂等 → error）。

  ```python
  async def _action_send_keys(self, params: dict, browser: BrowserSession) -> ActionResult:
      keys = params["keys"]
      try:
          await browser.send_keys(keys)
      except Exception as e:
          # send_keys 非幂等（按键可能提交表单/触发导航），CDP 失败即报 error
          logger.warning("send_keys(%r) failed: %s", keys, e)
          return ActionResult(error=f"Send keys failed: {e}")
      memory = f"Sent keys '{keys}'"
      logger.info(memory)
      return ActionResult(extracted_content=memory, long_term_memory=memory)
  ```

  委托给 `BrowserSession.send_keys()`（[session.py:1227-1337](../../src/tree_walker/browser/session.py)），按"组合键 / 单特殊键 / 文本逐字符"三条路由派发。别名归一化（`_normalize_key`，[session.py:127](../../src/tree_walker/browser/session.py)）大小写不敏感：

  | 输入 | 解析为 | 路由 |
  |---|---|---|
  | `"Control+a"` | Ctrl+A（带 modifiers） | 组合键：单字符主键经 `_send_combo_char_key`（保留 Ctrl 位掩码，全选生效） |
  | `"Shift+T"` | Shift+T | 组合键：keyDown `key='t'`（base 小写）+ Shift，char `text='T'` |
  | `"Alt+F4"` | Alt+F4 | 组合键：特殊键主键经 `_send_single_special_key` |
  | `"Enter"` | Enter（keyDown/char `\r`/keyUp） | 单特殊键 |
  | `"up"` / `"ArrowUp"` | ArrowUp（vk=38） | 别名归一化 → 单特殊键 |
  | `"return"` | Enter | 别名 → 单特殊键 |
  | `"F5"` | F5（vk=0x74） | 单特殊键 |
  | `"space"` | 空格 `" "`（code=Space, vk=32） | 文本/char 分支（经 `_type_char`） |
  | `"hello"` | 逐字符 h/e/l/l/o | 文本分支（复用 `_type_char`） |

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `Input.dispatchKeyEvent`（keyDown/char/keyUp，单特殊键） | `{type, key, code, modifiers, windowsVirtualKeyCode}` + char `{type:char, text, key}` | `_send_single_special_key` session.py:1304 |
  | `Input.dispatchKeyEvent`（keyDown/char/keyUp，组合键单字符主键） | 同上，主键事件带 modifiers | `_send_combo_char_key` session.py:1276 |
  | 文本分支（复用 `_type_char`） | 每字符 keyDown/char/keyUp；CJK 走 `Input.insertText` | `_type_char` session.py:1062 |

- **注意事项**：
  - **修饰符位掩码**（`_MODIFIER_VK`，[session.py:111](../../src/tree_walker/browser/session.py)）：alt=1, control=2, meta=4, shift=8；只在主键事件里带 `modifiers`，浏览器据此设 ctrlKey/altKey 状态（CDP 设计如此，对齐 `_type_char`/`input_text`）。
  - **别名归一化**：ctrl/control、return、esc、cmd/command、up/down/left/right、pageup/pagedown、home/end、del、space→`" "`、F1-F12 等大小写不敏感（`_KEY_ALIASES`，[session.py:78](../../src/tree_walker/browser/session.py)）。
  - **特殊键覆盖**：`_KEY_VK_MAP`（[session.py:62](../../src/tree_walker/browser/session.py)）补全 Arrow/Home/End/PageUp/PageDown/Delete/F1-F12 的 windowsVirtualKeyCode（旧实现除 enter/tab/escape/backspace/arrow 外 vk=0）。
  - **组合键单字符主键**（核心修正）：`Control+a` 的 `a` 经 `_send_combo_char_key` 带 modifiers，否则 fallback 文本分支会丢 modifiers 导致 Ctrl+A 全选失效。
  - **文本分支**：纯文本逐字符复用 `_type_char`；`space` 归一化为 `" "` 后也走此分支（对齐 browser-use，其 special_keys 不含空格）。
  - **Enter/Tab 补 char**：Enter 发 `\r`、Tab 发 `\t`，否则 React 表单提交不触发；仅 Enter 后 `sleep(0.1)` 等可能导航（其余键不 sleep）。
  - **未知修饰键软降级**：组合键中未知修饰键（如 `Foo+a`）warning + 跳过，不硬失败（组合键空间无限）。
  - **异常处理**：send_keys 非幂等，CDP 失败 → `ActionResult(error="Send keys failed: ...")`（对齐 scroll，区别于幂等的 close_tab 软成功）。
  - **`+` 文本歧义**：`+` 既是修饰键分隔符也是合法字符，`"a+b"` 被当组合键（修饰 a 忽略、主键 b）；长文本应改用 `input_text`。

---

### 4.21 `switch_tab`

- **description**：`Switch to a different browser tab by tab ID` / 按 Tab ID 切换标签页
- **terminates_sequence**：True
- **Pydantic 参数**：

  | 字段 | 类型 | 描述 |
  |---|---|---|
  | `tab_id` | `str` (`min_length=1`) | Tab ID (last 4 characters) to switch to |

- **主要逻辑**（[actions.py:628-648](../../src/tree_walker/tools/actions.py)，helper `_summarize_tabs` 在 [actions.py:650](../../src/tree_walker/tools/actions.py)）：

  ```python
  async def _action_switch_tab(self, params: dict, browser: BrowserSession) -> ActionResult:
      tab_id_suffix = params["tab_id"]
      tabs = await browser.get_tabs()
      matches = [t for t in tabs if t.target_id.endswith(tab_id_suffix)]
      if not matches:
          return ActionResult(error=f"No tab ending with '{tab_id_suffix}'. Open tabs: {self._summarize_tabs(tabs)}")
      if len(matches) > 1:  # 后缀撞车：要求更长后缀/完整 target_id
          return ActionResult(error=f"Multiple tabs match '{tab_id_suffix}' ({len(matches)}). Use more characters or the full target_id. Matches: {self._summarize_tabs(matches)}")
      target = matches[0]
      await browser.switch_tab(target.target_id)
      memory = f"Switched to tab [{tab_id_suffix}] {target.title} ({target.url})"
      logger.info(memory)
      return ActionResult(extracted_content=memory, long_term_memory=memory)
  ```

  用轻量 `browser.get_tabs()`（单 `Target.getTargets`，[session.py:1244](../../src/tree_walker/browser/session.py)）枚举标签页，不再为读列表调全量 `get_state`。匹配改为收集全部 `matches`：空 → error 列出现有标签页；多于一个 → error 提示用更长后缀/完整 target_id（**比 browser-use 取首个匹配更严**，避免切错页）；唯一 → 切换并回显。

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `Target.getTargets`（经 `get_tabs`） | `{}` | session.py:1249 |
  | `Target.activateTarget` | `{targetId}` | session.py:1268 |
  | `Target.attachToTarget` | `{targetId, flatten:True}` | session.py:1269 |

- **注意事项**：
  - 成功回显 `Switched to tab [{id}] title (url)`（对齐 navigate/click/go_back），写入 `extracted_content` + `long_term_memory`；title/url 取自匹配到的 `TabInfo`，零额外 CDP 调用。
  - 后缀撞车直接报错（`len(matches) > 1`），不取首个匹配，避免切错页；未命中时 error 列出现有标签页（`_summarize_tabs`）便于 LLM 重选。
  - `tab_id` 校验 `min_length=1`（保留接受完整 `target_id` 的灵活性；browser-use 用 `min_length=4, max_length=4` 强约束）。
  - 切换后 `current_target_id` / `current_session_id` 都会更新，并清空两层 selector_map 缓存（对齐 navigate / go_back）。

---

### 4.22 `upload_file`

- **description**：`Upload a file to a file input element` / 向文件输入元素上传文件
- **terminates_sequence**：False
- **Pydantic 参数**：

  | 字段 | 类型 | 描述 |
  |---|---|---|
  | `index` | `int` | ID of the file input element |
  | `path` | `str` | Path to the file to upload |

- **主要逻辑**（[actions.py:737](../../src/tree_walker/tools/actions.py)）：

  ```python
  async def _action_upload_file(self, params: dict, browser: BrowserSession) -> ActionResult:
      file_path = params["path"]
      # 1. 路径白名单 + 文件存在/非空校验
      if self._allowed_upload_paths and not any(
          file_path.startswith(p) for p in self._allowed_upload_paths
      ):
          return ActionResult(error=f"File path not in allowed upload paths: {file_path}")
      if not os.path.isfile(file_path):
          return ActionResult(error=f"File not found: {file_path}")
      if os.path.getsize(file_path) == 0:
          return ActionResult(error=f"File is empty: {file_path}")

      # 2. 元素查找
      entry, error = await self._get_element_by_index(params["index"], browser)
      if error:
          return error

      # 3. 非 file input 时定位最近 file input
      tag, attrs = entry.tag_name.upper(), entry.attributes
      is_file_input = tag == "INPUT" and attrs.get("type", "").lower() == "file"
      backend_id = entry.backend_node_id
      file_input_ids: list[int] = []
      if not is_file_input:
          file_input_ids = list(self._cached_browser_state.dom_state.file_input_backend_ids)
          if not file_input_ids:
              return ActionResult(error="Element is not a file input and no file input found on page")
          backend_id = _pick_nearest_file_input(entry, file_input_ids, ...)

      # 4. 高亮 + 上传（共用 try，highlight best-effort，对齐 _action_click）
      try:
          await browser.highlight_element(backend_id)
          await browser.set_file_input(backend_node_id=backend_id, file_path=file_path, ...)
      except Exception as e:
          return ActionResult(error=f"File upload failed: {e}")

      # 5. 成功回显（G1）+ 目标替换提示（G2）+ accept 软校验（G3）
      memory = self._describe_upload(entry, params["index"], file_path)
      if not is_file_input:
          memory += (f"  ⚠️ Note: index {params['index']} is not an <input type='file'>; "
                     f"uploaded to the nearest file input on the page instead.")
      fin_entry = entry if is_file_input else self._find_node_by_backend_id(backend_id, ...)
      accept_attr = (getattr(fin_entry, "attributes", {}) or {}).get("accept") if fin_entry else None
      if accept_attr and not _file_matches_accept(file_path, accept_attr):
          memory += (f"  ⚠️ Note: the file extension does not match this input's "
                     f"accept={accept_attr!r} — the site may reject the upload.")
      return ActionResult(extracted_content=memory, long_term_memory=memory)
  ```

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `DOM.setFileInputFiles` | `{backendNodeId, files:[file_path]}`（单文件列表） | session.py:1365 |
  | `DOM.getDocument` (shadow DOM 兜底) | `{depth:-1, pierce:True}` | session.py:1344 |

- **注意事项**：
  - `_allowed_upload_paths` 是白名单，未配置时不限制（生产环境强烈建议配置）
  - 不直接调用系统文件选择器，避免阻塞
  - `_pick_nearest_file_input` 用 DOM 树遍历 + 坐标距离双策略找最近的 file input（[actions.py:70-108](../../src/tree_walker/tools/actions.py)）
  - 成功回显 `Uploaded 'name' to [TAG] {label} at index N`（`_describe_upload`，[actions.py:428](../../src/tree_walker/tools/actions.py)），写入 `extracted_content` + `long_term_memory`
  - 非 file input 时自动上传到最近 file input，回显追加 `⚠️ Note` 告知 LLM 实际目标被替换（不再静默）
  - 解析 file input 的 `accept`，扩展名不符时追加 `⚠️ Note`（`_file_matches_accept`，[actions.py:111](../../src/tree_walker/tools/actions.py)；软校验，不阻断上传）

---

### 4.23 `wait`

- **description**：`Wait for a specified number of seconds` / 等待指定秒数
- **terminates_sequence**：False
- **Pydantic 参数**：

  | 字段 | 类型 | 默认 | 描述 |
  |---|---|---|---|
  | `seconds` | `int` | `3` | Seconds to wait (ge=1, le=30) |

- **主要逻辑**（[actions.py:281-283](../../src/tree_walker/tools/actions.py)）：

  ```python
  async def _action_wait(self, params: dict, browser: BrowserSession) -> ActionResult:
      await asyncio.sleep(float(params.get("seconds", 3)))
      return ActionResult()
  ```

- **CDP 调用清单**：无（纯 asyncio.sleep）

- **注意事项**：
  - 范围 1-30 秒（Pydantic 强制约束）
  - 在循环检测中豁免（`_LOOP_EXEMPT_ACTIONS`），即使连续多次 wait 也不会触发警告

---

### 4.24 `write_file`

- **description**：`Write content to a local file` / 写入本地文件
- **terminates_sequence**：False
- **Pydantic 参数**：

  | 字段 | 类型 | 描述 |
  |---|---|---|
  | `path` | `str` | File path to write to |
  | `content` | `str` | Content to write |

- **主要逻辑**（[actions.py:423-429](../../src/tree_walker/tools/actions.py)）：

  ```python
  async def _action_write_file(self, params: dict, browser: BrowserSession) -> ActionResult:
      path = params["path"]
      content = params["content"]
      os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
      with open(path, "w", encoding="utf-8") as f:
          f.write(content)
      return ActionResult(extracted_content=f"Written to {path}")
  ```

- **CDP 调用清单**：无

- **注意事项**：
  - 自动创建父目录（`makedirs(exist_ok=True)`）
  - 编码固定 UTF-8
  - 整体覆盖（非追加）

---

## 4.3 关键 CDP 操作详解

### 4.3.1 鼠标点击坐标计算（三层 fallback）

源码：[session.py:518-599](../../src/tree_walker/browser/session.py)（`get_element_coordinates`）、[session.py:601-632](../../src/tree_walker/browser/session.py)（`_best_quad_rect`）、[session.py:651-703](../../src/tree_walker/browser/session.py)（`click_element`）

```python
async def get_element_coordinates(
    self, backend_node_id: int, viewport: tuple[int, int] | None = None,
) -> DOMRect | None:
    """Three-tier fallback chain (same as browser-use):
    1. DOM.getContentQuads — best for inline/complex layouts（取与视口交集最大的 quad）
    2. DOM.getBoxModel — fallback using box model content
    3. JS getBoundingClientRect() via DOM.resolveNode + Runtime.callFunctionOn
    """
    sid = self.current_session_id
    if viewport is None:
        viewport = await self._get_viewport_size()  # Page.getLayoutMetrics

    # Method 1: DOM.getContentQuads — 取与视口交集最大的 quad 的外接矩形
    try:
        result = await self.client.send.DOM.getContentQuads(
            {"backendNodeId": backend_node_id}, session_id=sid,
        )
        best = self._best_quad_rect(result.get("quads", []), viewport)
        if best:
            return best
    except Exception:
        pass

    # Method 2: DOM.getBoxModel
    try:
        result = await self.client.send.DOM.getBoxModel(...)
        # ... 同样的 8 坐标解析逻辑
    except Exception:
        pass

    # Method 3: JS getBoundingClientRect()
    try:
        resolve = await self.client.send.DOM.resolveNode(
            {"backendNodeId": backend_node_id}, session_id=sid,
        )
        object_id = resolve["object"]["objectId"]
        js_result = await self.client.send.Runtime.callFunctionOn({
            "objectId": object_id,
            "functionDeclaration": """
            function() {
                const rect = this.getBoundingClientRect();
                return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
            }
            """,
            "returnByValue": True,
        }, session_id=sid)
        rect = js_result.get("result", {}).get("value")
        if rect and rect.get("width", 0) > 0 and rect.get("height", 0) > 0:
            return DOMRect(...)
    except Exception:
        pass

    return None
```

最终点击坐标与回退链（[session.py:651-703](../../src/tree_walker/browser/session.py)，`click_element` 返回 `bool`）：

```python
viewport = await self._get_viewport_size()
rect = await self.get_element_coordinates(backend_node_id, viewport=viewport)
if rect:
    x = int(rect.x + rect.width / 2)
    y = int(rect.y + rect.height / 2)
    # 裁剪到视口 [0, viewport-1]
    if viewport:
        x = max(0, min(viewport[0] - 1, x))
        y = max(0, min(viewport[1] - 1, y))
    if not await self._is_element_occluded(backend_node_id, x, y):
        await self.click_at(x, y)
        return True  # 几何点击成功
# 坐标拿不到 OR 被遮挡 -> JS click 回退（this.click()）
if await self._js_click(backend_node_id):
    return True
return False  # 坐标拿不到 + JS 回退也失败 -> action 层明确报错
```

拿到坐标后经 `_is_element_occluded`（`document.elementFromPoint` 祖先链）判遮挡，被遮挡或 `rect is None` 走 `_js_click` 回退；最终 `bool` 信号上浮到 action 层（`False` → `ActionResult(error=...)`，不再静默成功）。

**为什么三层 fallback**：

- `DOM.getContentQuads` 对 CSS transform / position:absolute 等复杂布局最准确，但对某些 display:none 元素返回空 quad；多 quad 元素（跨行 inline、CSS transform 拆分）用 `_best_quad_rect` 选与视口交集最大者
- `DOM.getBoxModel` 用 layout 信息，对隐藏元素也能给出尺寸
- JS `getBoundingClientRect()` 是 web 标准方法，但需要先 `DOM.resolveNode` 拿到 objectId

### 4.3.2 文本输入与 Vue/React 框架事件

源码：[session.py:530-669](../../src/tree_walker/browser/session.py)

**逐字符输入**（`_type_char`，session.py:563-602）：

```python
async def _type_char(self, char: str, sid=None) -> None:
    if sid is None:
        sid = self.current_session_id
    modifiers, vk_code, base_key = _get_char_modifiers_and_vk(char)
    key_code = _get_key_code_for_char(base_key)
    is_ascii = ord(char) < 128

    if is_ascii:
        await self.client.send.Input.dispatchKeyEvent({
            "type": "keyDown", "key": base_key, "code": key_code,
            "modifiers": modifiers, "windowsVirtualKeyCode": vk_code,
        }, session_id=sid)
        await asyncio.sleep(0.005)

    await self.client.send.Input.dispatchKeyEvent(
        {"type": "char", "text": char, "key": char}, session_id=sid,
    )

    if is_ascii:
        await self.client.send.Input.dispatchKeyEvent({
            "type": "keyUp", "key": base_key, "code": key_code,
            "modifiers": modifiers, "windowsVirtualKeyCode": vk_code,
        }, session_id=sid)
```

**关键差异**：非 ASCII 字符（CJK）**只发 char 事件**，跳过 keyDown/keyUp。这是因为 CJK 字符没有对应的物理键码。

**框架事件触发**（`_trigger_framework_events`，session.py:604-669）：

完成逐字符输入后，通过 `Runtime.evaluate` 注入 JS：

```javascript
(function() {
    var el = document.activeElement;
    if (!el || el === document.body) return false;

    el.focus();

    // InputEvent — primary for React/Vue v-model
    el.dispatchEvent(new InputEvent('input', {
        bubbles: true, cancelable: true,
        data: el.value, inputType: 'insertText'
    }));

    // Change event
    el.dispatchEvent(new Event('change', {bubbles: true}));

    // Blur event
    el.dispatchEvent(new Event('blur', {bubbles: true}));

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
        setTimeout(function() {
            el.dispatchEvent(new Event('input', {bubbles: true}));
        }, 0);
    }

    return true;
})()
```

**为什么需要这段 JS**：

- React `onChange` 监听 input 事件，但 CDP `Input.dispatchKeyEvent` 在某些情况下不触发完整的 DOM 事件流
- Vue `v-model` 双向绑定同样依赖 input 事件，并且 Vue 有自己的异步更新队列，`setTimeout(0)` 确保下一轮事件循环再触发
- 通过检测 `el.__vue__` / `el._vnode` / `el.__vueParentComponent__` 兼容 Vue 2 和 Vue 3

### 4.3.3 鼠标滚轮事件

源码：[session.py:725-747](../../src/tree_walker/browser/session.py)（已在 4.16 完整列出）

**关键参数**：

- `type: "mouseWheel"` — 这是 CDP 特有的事件类型，与 `mouseMoved` / `mousePressed` 同级
- `deltaX: 0` — 水平不滚
- `deltaY: amount * viewport_height` — 垂直滚动 N 个视口高度
- 坐标 `(clientWidth/2, clientHeight/2)` — 鼠标位置在视口中心，影响哪些元素接收 wheel 事件

### 4.3.4 文件上传 backendNodeId 路由

源码：[session.py:822-864](../../src/tree_walker/browser/session.py)

```python
async def set_file_input(
    self, backend_node_id: int | None, file_path: str,
    file_input_backend_ids: list[int] | None = None,
) -> None:
    target_id = backend_node_id

    # 如果传入的不是 file input，用列表中第一个
    if target_id is None and file_input_backend_ids:
        target_id = file_input_backend_ids[0]

    # 最后兜底：搜索 shadow DOM
    if target_id is None:
        shadow_ids = await self.find_file_inputs_in_shadow_dom()
        if shadow_ids:
            target_id = shadow_ids[0]

    if target_id is None:
        raise RuntimeError("No file input element found. ...")

    await self.client.send.DOM.setFileInputFiles(
        {"backendNodeId": target_id, "files": [file_path]},
        session_id=self.current_session_id,
    )
```

**关键设计**：

- 直接用 `backendNodeId` + `DOM.setFileInputFiles` 设置文件，**绕过 OS 文件选择器**
- 这种方式不需要浏览器窗口可见，也不需要 GUI 交互
- 支持隐藏在 shadow DOM 中的 file input（通过 `find_file_inputs_in_shadow_dom` 查找）

---

## 4.4 CDP 调用总览矩阵

### 按 CDP 域分组

#### `Input.*` 域

| CDP 命令 | 参数 | 用于 action | 行号 |
|---|---|---|---|
| `dispatchMouseEvent(mouseMoved)` | `{x, y}` | click, input_text（mouseMoved → mousePressed → mouseReleased 序列前置） | session.py:485 |
| `dispatchMouseEvent(mousePressed)` | `{x, y, button:left, clickCount:1}` | click, input_text | session.py:498 |
| `dispatchMouseEvent(mouseReleased)` | 同上 | click, input_text | session.py:511 |
| `dispatchMouseEvent(mouseWheel)` | `{x, y, deltaX:0, deltaY}` | scroll | session.py:737 |
| `dispatchKeyEvent(keyDown)` | `{key, code, modifiers, windowsVirtualKeyCode}` | input_text, send_keys | session.py:539, 575, 1312 |
| `dispatchKeyEvent(char)` | `{text, key}` | input_text, send_keys | session.py:587, 1322 |
| `dispatchKeyEvent(keyUp)` | `{key, code, modifiers, windowsVirtualKeyCode}` | input_text, send_keys | session.py:593, 1326 |

#### `DOM.*` 域

| CDP 命令 | 参数 | 用于 action | 行号 |
|---|---|---|---|
| `scrollIntoViewIfNeeded` | `{backendNodeId}` | click, input_text | session.py:670 |
| `getContentQuads` | `{backendNodeId}` | click, input_text (坐标方法 1，取最大交集 quad) | session.py:545 |
| `getBoxModel` | `{backendNodeId}` | click, input_text (坐标方法 2) | session.py:562 |
| `resolveNode` | `{backendNodeId}` | click, input_text (坐标方法 3 / 遮挡检查 / JS 回退 / SELECT option) | session.py:580, 721, 769, 1265 |
| `getDocument` | `{depth:-1, pierce:True}` | upload_file (shadow DOM) | session.py:807 |
| `setFileInputFiles` | `{backendNodeId, files:[path]}` | upload_file | session.py:861 |

#### `Page.*` 域

| CDP 命令 | 参数 | 用于 action | 行号 |
|---|---|---|---|
| `navigate` | `{url}` | navigate, search | session.py:383 |
| `getNavigationHistory` | `{}` | go_back | session.py:391 |
| `navigateToHistoryEntry` | `{entryId}` | go_back | session.py:397 |
| `captureScreenshot` | `{format:png}` | screenshot | session.py:372 |
| `printToPDF` | `{printBackground:True}` | save_as_pdf | actions.py:327 |
| `getLayoutMetrics` | `{}` | scroll, click（`_get_viewport_size` 取视口用于 quad 选择 + 中心裁剪） | session.py:642, 728 |

#### `Target.*` 域

| CDP 命令 | 参数 | 用于 action | 行号 |
|---|---|---|---|
| `activateTarget` | `{targetId}` | switch_tab, close_tab | session.py:755 |
| `attachToTarget` | `{targetId, flatten:True}` | switch_tab | session.py:756 |
| `closeTarget` | `{targetId}` | close_tab | session.py:766 |
| `getTargets` | `{}` | close_tab | session.py:768 |
| `createTarget` | `{url}` | create_tab (未对外暴露为 action) | session.py:776 |

#### `Runtime.*` 域

| CDP 命令 | 参数 | 用于 action | 行号 |
|---|---|---|---|
| `evaluate` | `{expression, returnByValue, awaitPromise, timeout:30000}` | evaluate, find_elements, find_text, dropdown_options, select_dropdown, search_page, extract | session.py:785, 612 |
| `callFunctionOn` | `{objectId, functionDeclaration, returnByValue[, arguments]}` | click（坐标方法 3 / 遮挡检查 `elementFromPoint` / JS 回退 `this.click()` / SELECT `this.options`） | session.py:587, 728, 776, 1272 |

#### `Overlay.*` 域（可选视觉反馈）

| CDP 命令 | 参数 | 用于 action | 行号 |
|---|---|---|---|
| `highlightNode` | `{highlightConfig, backendNodeId}` | click, input_text (前置) | highlight.py:35 |
| `hideHighlight` | `{}` | (内部清理) | highlight.py:162 |

### 调用频次统计（粗略估算）

| Action | 平均 CDP 命令数 | 说明 |
|---|---|---|
| `click` | 5-10 | scrollIntoView + getLayoutMetrics + 坐标 1-3 方法（取最大交集 quad）+ 遮挡检查 + 3 个 mouse 事件 + 高亮（被遮挡或无坐标时改走 JS 回退，命令数减少） |
| `input_text` | 10+ | click + clear（4 个 keyEvent）+ 每字符 3 个 keyEvent + 框架事件 |
| `navigate` | 1 | 单 Page.navigate |
| `scroll` | 2 | getLayoutMetrics + mouseWheel |
| `evaluate` | 1 | 单 Runtime.evaluate |
| `screenshot` | 1 | 单 Page.captureScreenshot |
| `upload_file` | 1-3 | setFileInputFiles (+ 可能 getDocument) |

---

## 下一步阅读

→ [05_output_mode与JSON样例](05_output_mode与JSON样例.md)：看 Anthropic tool schema 在三种模式下的真实 JSON 长什么样

← [返回 03 Agent Loop 交互](03_Agent_Loop交互序列图.md) | [返回 README](README.md)
