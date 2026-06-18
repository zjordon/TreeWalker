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

- **主要逻辑**（[actions.py:203-217](../../src/tree_walker/tools/actions.py)）：

  ```python
  async def _action_click(self, params: dict, browser: BrowserSession) -> ActionResult:
      entry, error = await self._get_element_by_index(params["index"], browser)
      if error:
          return error
      tag = entry.tag_name.upper()
      if tag == "SELECT":
          js_code = (
              "Array.from(document.querySelectorAll('select option'))"
              ".map(o => ({value: o.value, text: o.textContent.trim()}))"
          )
          options = await browser.execute_js(js_code)
          return ActionResult(extracted_content=str(options))
      await browser.highlight_element(entry.backend_node_id)
      await browser.click_element(entry.backend_node_id)
      return ActionResult()
  ```

  特殊分支：点击 `<select>` 时不真正点击，而是返回所有 option 让 LLM 接下来用 `select_dropdown`。

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `DOM.scrollIntoViewIfNeeded` | `{backendNodeId}` | session.py:511 |
  | `DOM.getContentQuads` | `{backendNodeId}` | session.py:439 |
  | `DOM.getBoxModel` (fallback) | `{backendNodeId}` | session.py:459 |
  | `DOM.resolveNode` + `Runtime.callFunctionOn` (fallback) | `getBoundingClientRect()` | session.py:478, 483 |
  | `Input.dispatchMouseEvent` × 2 | `{type, x, y, button:left, clickCount:1}` | session.py:413 |
  | `Overlay.highlightNode` (可选) | `{highlightConfig, backendNodeId}` | highlight.py:35 |

- **注意事项**：坐标计算用三层 fallback 链（详见 4.3.1）；点击坐标为元素几何中心 `(x+w/2, y+h/2)`。

---

### 4.2 `close_tab`

- **description**：`Close a browser tab` / 关闭浏览器标签页
- **terminates_sequence**：False
- **Pydantic 参数**：

  | 字段 | 类型 | 默认 | 描述 |
  |---|---|---|---|
  | `tab_id` | `str` | `""` | Tab ID to close. Empty string closes current tab |

- **主要逻辑**（[actions.py:268-279](../../src/tree_walker/tools/actions.py)）：

  ```python
  async def _action_close_tab(self, params: dict, browser: BrowserSession) -> ActionResult:
      tab_id = params.get("tab_id", "")
      if tab_id:
          state = await browser.get_state(include_screenshot=False)
          for tab in state.tabs:
              if tab.target_id.endswith(tab_id):
                  await browser.close_tab(tab.target_id)
                  return ActionResult()
          return ActionResult(error=f"Tab ending with '{tab_id}' not found")
      if browser.current_target_id:
          await browser.close_tab(browser.current_target_id)
      return ActionResult()
  ```

  `tab_id` 是 target ID 的后缀匹配（通常是末 4 个字符），方便 LLM 短输入。

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `Target.closeTarget` | `{targetId}` | session.py:766 |
  | `Target.getTargets` (如关闭当前 tab) | `{}` | session.py:768 |
  | `Target.activateTarget` + `Target.attachToTarget` (切换到其他 tab) | `{targetId, flatten:True}` | session.py:755-756 |

- **注意事项**：关闭当前 tab 时，自动切换到第一个剩余的 page target，避免 `current_target_id` 失效。

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

- **主要逻辑**（[actions.py:285-287](../../src/tree_walker/tools/actions.py)）：

  ```python
  async def _action_go_back(self, params: dict, browser: BrowserSession) -> ActionResult:
      await browser.go_back()
      return ActionResult()
  ```

  委托给 `BrowserSession.go_back()`（[session.py:389-401](../../src/tree_walker/browser/session.py)）：

  ```python
  async def go_back(self) -> None:
      history = await self.client.send.Page.getNavigationHistory({}, ...)
      idx = history.get("currentIndex", 0)
      entries = history.get("entries", [])
      if idx > 0 and entries:
          await self.client.send.Page.navigateToHistoryEntry(
              {"entryId": entries[idx - 1]["id"]}, ...
          )
          await asyncio.sleep(0.3)
  ```

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `Page.getNavigationHistory` | `{}` | session.py:391 |
  | `Page.navigateToHistoryEntry` | `{entryId}` | session.py:397 |

- **注意事项**：仅当 `currentIndex > 0` 才后退；后退后硬编码 `sleep(0.3)` 等待页面渲染。

---

### 4.10 `input_text`

- **description**：`Type text into an input element identified by ID` / 向指定 ID 的输入框输入文本
- **terminates_sequence**：False
- **Pydantic 参数**：

  | 字段 | 类型 | 默认 | 描述 |
  |---|---|---|---|
  | `index` | `int` | (必填) | ID of the element to type into |
  | `text` | `str` | (必填) | Text to type into the element |
  | `clear` | `bool` | `True` | Whether to clear existing text first |

- **主要逻辑**（[actions.py:219-227](../../src/tree_walker/tools/actions.py)）：

  ```python
  async def _action_input_text(self, params: dict, browser: BrowserSession) -> ActionResult:
      entry, error = await self._get_element_by_index(params["index"], browser)
      if error:
          return error
      await browser.highlight_element(entry.backend_node_id)
      await browser.click_element(entry.backend_node_id)  # 聚焦元素
      await asyncio.sleep(0.1)
      await browser.type_text(params["text"], clear=params.get("clear", True))
      return ActionResult()
  ```

  先点击聚焦元素，再用 `type_text` 逐字符输入。

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `DOM.scrollIntoViewIfNeeded` | `{backendNodeId}` | session.py:511 |
  | `DOM.getContentQuads` (坐标) | `{backendNodeId}` | session.py:439 |
  | `Input.dispatchMouseEvent` × 2 | `{mousePressed/mouseReleased, x, y}` | session.py:413 |
  | `Input.dispatchKeyEvent` (Ctrl+A 模拟 clear) | `{keyDown/keyUp, key:a, modifiers:2}` | session.py:539-546 |
  | `Input.dispatchKeyEvent` (Backspace 清空) | `{keyDown/keyUp, key:Backspace}` | session.py:547-554 |
  | `Input.dispatchKeyEvent` × 3 (每字符) | `{keyDown/char/keyUp, key, code, windowsVirtualKeyCode}` | session.py:575-602 |
  | `Runtime.evaluate` (框架事件) | `{expression: _trigger_framework_events JS}` | session.py:612 |

- **注意事项**：
  - 非 ASCII 字符（CJK）只发 `char` 事件，跳过 keyDown/keyUp（[session.py:572-590](../../src/tree_walker/browser/session.py)）
  - 输入完成后通过 `_trigger_framework_events` 触发 Vue/React 兼容事件，详见 4.3.2
  - 每字符间隔 1ms 避免事件丢失

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

- **description**：`Scroll the page up or down by a number of increments` / 上下滚动页面若干增量
- **terminates_sequence**：False
- **Pydantic 参数**：

  | 字段 | 类型 | 默认 | 描述 |
  |---|---|---|---|
  | `amount` | `int` | `3` | Number of scroll increments (page heights) |
  | `direction` | `Literal["up","down"]` | `"down"` | Scroll direction |

- **主要逻辑**（[actions.py:229-231](../../src/tree_walker/tools/actions.py)）：

  ```python
  async def _action_scroll(self, params: dict, browser: BrowserSession) -> ActionResult:
      await browser.scroll(params.get("direction", "down"), int(params.get("amount", 3)))
      return ActionResult()
  ```

  委托给 `BrowserSession.scroll()`（[session.py:725-747](../../src/tree_walker/browser/session.py)）：

  ```python
  async def scroll(self, direction="down", amount=3) -> None:
      sid = self.current_session_id
      metrics = await self.client.send.Page.getLayoutMetrics({}, session_id=sid)
      viewport = metrics.get("cssVisualViewport", {})
      viewport_height = viewport.get("clientHeight", 800)
      delta = amount * viewport_height
      if direction == "up":
          delta = -delta
      await self.client.send.Input.dispatchMouseEvent({
          "type": "mouseWheel",
          "x": viewport.get("clientWidth", 1280) / 2,
          "y": viewport.get("clientHeight", 800) / 2,
          "deltaX": 0,
          "deltaY": delta,
      }, session_id=sid)
      await asyncio.sleep(0.3)
  ```

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `Page.getLayoutMetrics` | `{}` | session.py:728 |
  | `Input.dispatchMouseEvent` | `{type:mouseWheel, x, y, deltaX:0, deltaY}` | session.py:737 |

- **注意事项**：滚动量单位是**视口高度**而非像素；鼠标位置固定在视口中心。

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
  | `keys` | `str` | e.g. 'Enter', 'Control+a', 'Tab' |

- **主要逻辑**（[actions.py:255-257](../../src/tree_walker/tools/actions.py)）：

  ```python
  async def _action_send_keys(self, params: dict, browser: BrowserSession) -> ActionResult:
      await browser.send_keys(params["keys"])
      return ActionResult()
  ```

  委托给 `BrowserSession.send_keys()`（[session.py:671-721](../../src/tree_walker/browser/session.py)），支持以下组合：

  | 输入 | 解析为 |
  |---|---|
  | `"Enter"` | Enter 键 |
  | `"Control+a"` | Ctrl + A |
  | `"Shift+T"` | Shift + T |
  | `"Alt+F4"` | Alt + F4 |
  | `"Escape"` | Esc 键 |

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `Input.dispatchKeyEvent` (keyDown) | `{type:keyDown, key, code, modifiers, windowsVirtualKeyCode}` | session.py:692 |
  | `Input.dispatchKeyEvent` (char, 仅 Enter/Tab) | `{type:char, text, key}` | session.py:706 |
  | `Input.dispatchKeyEvent` (keyUp) | `{type:keyUp, key, code, modifiers, windowsVirtualKeyCode}` | session.py:711 |

- **注意事项**：
  - 修饰符映射：control=2, alt=1, shift=8, meta=4
  - Enter/Tab 额外发 char 事件，否则 React 表单提交不触发
  - 完成后 `sleep(0.1)`

---

### 4.21 `switch_tab`

- **description**：`Switch to a different browser tab by tab ID` / 按 Tab ID 切换标签页
- **terminates_sequence**：True
- **Pydantic 参数**：

  | 字段 | 类型 | 描述 |
  |---|---|---|
  | `tab_id` | `str` | Tab ID (last 4 characters) to switch to |

- **主要逻辑**（[actions.py:259-266](../../src/tree_walker/tools/actions.py)）：

  ```python
  async def _action_switch_tab(self, params: dict, browser: BrowserSession) -> ActionResult:
      tab_id_suffix = params["tab_id"]
      state = await browser.get_state(include_screenshot=False)
      for tab in state.tabs:
          if tab.target_id.endswith(tab_id_suffix):
              await browser.switch_tab(tab.target_id)
              return ActionResult()
      return ActionResult(error=f"Tab ending with '{tab_id_suffix}' not found")
  ```

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `Target.activateTarget` | `{targetId}` | session.py:755 |
  | `Target.attachToTarget` | `{targetId, flatten:True}` | session.py:756 |

- **注意事项**：切换后 `current_target_id` 和 `current_session_id` 都会更新；同时清空缓存。

---

### 4.22 `upload_file`

- **description**：`Upload a file to a file input element` / 向文件输入元素上传文件
- **terminates_sequence**：False
- **Pydantic 参数**：

  | 字段 | 类型 | 描述 |
  |---|---|---|
  | `index` | `int` | ID of the file input element |
  | `path` | `str` | Path to the file to upload |

- **主要逻辑**（[actions.py:359-421](../../src/tree_walker/tools/actions.py)）：

  ```python
  async def _action_upload_file(self, params: dict, browser: BrowserSession) -> ActionResult:
      file_path = params["path"]
      allowed = self._allowed_upload_paths
      if allowed:
          if not any(file_path.startswith(p) for p in allowed):
              return ActionResult(error=f"File path not in allowed upload paths: {file_path}")
      if not os.path.isfile(file_path):
          return ActionResult(error=f"File not found: {file_path}")
      if os.path.getsize(file_path) == 0:
          return ActionResult(error=f"File is empty: {file_path}")

      entry, error = await self._get_element_by_index(params["index"], browser)
      if error:
          return error

      tag = entry.tag_name.upper()
      attrs = entry.attributes
      is_file_input = tag == "INPUT" and attrs.get("type", "").lower() == "file"

      # ... (查 DOM 缓存中的 file_input_backend_ids)
      backend_id = entry.backend_node_id
      file_input_ids: list[int] = []

      if not is_file_input:
          # 从 DOM 状态找最近 file input
          file_input_ids = list(self._cached_browser_state.dom_state.file_input_backend_ids)
          if not file_input_ids:
              return ActionResult(error="Element is not a file input and no file input found on page")
          backend_id = _pick_nearest_file_input(entry, file_input_ids, ...)

      try:
          await browser.highlight_element(backend_id)
          await browser.set_file_input(backend_node_id=backend_id, file_path=file_path, ...)
      except Exception as e:
          return ActionResult(error=f"File upload failed: {e}")
      return ActionResult(extracted_content=f"Uploaded {file_path}")
  ```

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `DOM.setFileInputFiles` | `{backendNodeId, files:[file_path]}` | session.py:861 |
  | `DOM.getDocument` (shadow DOM 兜底) | `{depth:-1, pierce:True}` | session.py:807 |

- **注意事项**：
  - `_allowed_upload_paths` 是白名单，未配置时不限制（生产环境强烈建议配置）
  - 不直接调用系统文件选择器，避免阻塞
  - `_pick_nearest_file_input` 用 DOM 树遍历 + 坐标距离双策略找最近的 file input（[actions.py:69-107](../../src/tree_walker/tools/actions.py)）

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

源码：[session.py:427-505](../../src/tree_walker/browser/session.py)

```python
async def get_element_coordinates(self, backend_node_id: int) -> DOMRect | None:
    """Three-tier fallback chain (same as browser-use):
    1. DOM.getContentQuads — best for inline/complex layouts
    2. DOM.getBoxModel — fallback using box model content
    3. JS getBoundingClientRect() via DOM.resolveNode + Runtime.callFunctionOn
    """
    sid = self.current_session_id

    # Method 1: DOM.getContentQuads
    try:
        result = await self.client.send.DOM.getContentQuads(
            {"backendNodeId": backend_node_id}, session_id=sid,
        )
        quads = result.get("quads", [])
        if quads:
            quad = quads[0]
            if len(quad) >= 8:
                xs = [quad[i] for i in range(0, 8, 2)]
                ys = [quad[i] for i in range(1, 8, 2)]
                return DOMRect(x=min(xs), y=min(xs), width=max(xs)-min(xs), height=max(ys)-min(ys))
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

最终点击坐标（[session.py:521-522](../../src/tree_walker/browser/session.py)）：

```python
x = int(rect.x + rect.width / 2)
y = int(rect.y + rect.height / 2)
await self.click_at(x, y)
```

**为什么三层 fallback**：

- `DOM.getContentQuads` 对 CSS transform / position:absolute 等复杂布局最准确，但对某些 display:none 元素返回空 quad
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
| `dispatchMouseEvent(mousePressed)` | `{x, y, button:left, clickCount:1}` | click, input_text | session.py:413 |
| `dispatchMouseEvent(mouseReleased)` | 同上 | click, input_text | session.py:413 |
| `dispatchMouseEvent(mouseWheel)` | `{x, y, deltaX:0, deltaY}` | scroll | session.py:737 |
| `dispatchKeyEvent(keyDown)` | `{key, code, modifiers, windowsVirtualKeyCode}` | input_text, send_keys | session.py:539, 575, 692 |
| `dispatchKeyEvent(char)` | `{text, key}` | input_text, send_keys | session.py:587, 706 |
| `dispatchKeyEvent(keyUp)` | `{key, code, modifiers, windowsVirtualKeyCode}` | input_text, send_keys | session.py:593, 711 |

#### `DOM.*` 域

| CDP 命令 | 参数 | 用于 action | 行号 |
|---|---|---|---|
| `scrollIntoViewIfNeeded` | `{backendNodeId}` | click, input_text | session.py:511 |
| `getContentQuads` | `{backendNodeId}` | click, input_text (坐标方法 1) | session.py:439 |
| `getBoxModel` | `{backendNodeId}` | click, input_text (坐标方法 2) | session.py:459 |
| `resolveNode` | `{backendNodeId}` | click, input_text (坐标方法 3) | session.py:478 |
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
| `getLayoutMetrics` | `{}` | scroll | session.py:728 |

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
| `callFunctionOn` | `{objectId, functionDeclaration, returnByValue}` | click (坐标方法 3 内部) | session.py:483 |

#### `Overlay.*` 域（可选视觉反馈）

| CDP 命令 | 参数 | 用于 action | 行号 |
|---|---|---|---|
| `highlightNode` | `{highlightConfig, backendNodeId}` | click, input_text (前置) | highlight.py:35 |
| `hideHighlight` | `{}` | (内部清理) | highlight.py:162 |

### 调用频次统计（粗略估算）

| Action | 平均 CDP 命令数 | 说明 |
|---|---|---|
| `click` | 4-8 | scrollIntoView + 坐标 1-3 方法 + 2 个 mouse 事件 + 高亮 |
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
