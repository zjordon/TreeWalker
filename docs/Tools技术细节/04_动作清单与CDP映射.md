# 04 动作清单与 CDP 映射

> 本章是 Tools 子系统的核心参考手册，逐一剖析 24 个注册 action 的：暴露给 LLM 的 name、description、Pydantic 参数、主要逻辑、对应的 CDP（Chrome DevTools Protocol）调用。文末附 CDP 调用总览矩阵与关键操作详解。

---

## 4.1 总览表

24 个 action 按 `ACTION_DEFINITIONS` 字典字母序排列。"终止序列" 列对应 `terminates_sequence` 字段，标记为 True 的 action 会切换页面上下文（导航/重载/切 Tab/执行 JS）。

| # | Action | 终止 | 主要参数 | 涉及 CDP 域 | 一句话职责 |
|---|---|---|---|---|---|
| 1 | [click](#41-click) | 否 | `index \| element_id, ...` | DOM + Input + Overlay | 点击元素（index 或 element_id=backend id），含 SELECT 分支 |
| 2 | [close_tab](#42-close_tab) | 否 | `tab_id: str=""` | Target | 关闭 Tab，必要时切换其他 |
| 3 | [done](#43-done) | 否 | `text, success` | (无 CDP) | 任务终止信号 |
| 4 | [dropdown_options](#44-dropdown_options) | 否 | `index: int` | Runtime (JS) | 读取 select 所有 option |
| 5 | [evaluate](#45-evaluate) | 是 | `code: str` | Runtime | 执行任意 JS，返回结果 |
| 6 | [extract](#46-extract) | 否 | `query, extract_links?, extract_images?, start_from_char?, already_collected?` | CDP (DOM.getDocument) + LLM | 页面转 markdown → LLM 抽取/结构化（分页 + 去重 + 大结果落盘） |
| 7 | [find_elements](#47-find_elements) | 否 | `selector, attributes?, max_results?, offset?, include_text?, first_only?, include_geometry?, return_node_ids?` | Runtime (JS) / DOM.performSearch | CSS 选择器查询元素（穿透 shadow/iframe；tag/text/attrs/children_count + 可选几何/visible + 总数；`return_node_ids` 回 backendNodeId） |
| 8 | [find_text](#48-find_text) | 否 | `text: str` | DOM + Overlay (+ Runtime) | CDP performSearch 滚动到文本并高亮 |
| 9 | [go_back](#49-go_back) | 是 | (无) | Page | 浏览器后退 |
| 10 | [input_text](#410-input_text) | 否 | `index \| element_id, text, clear` | DOM + Input + Runtime | 点击+输入文本+触发框架事件（index 或 element_id=backend id） |
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

  **file-input 守卫（issue #34, Bug 1）**：点击 `<input type='file'>` 时**不派发真实点击**，直接返回 error 引导 LLM 改用 `upload_file(index=…, path=…)`——因为 click file input 只会弹原生文件框（即便会话已开 `Page.setInterceptFileChooserDialog` 拦截不弹框，click 仍对上传无用且可能触发页面 JS 混乱）。只拦 input 本身，外层 label/按钮不受影响（走 upload_file 的目标替换路径）。

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

- **description**：`Signal that the task is complete and stop the agent. Must be the only action in the step. Provide a final summary of what was accomplished; set success=False if any requirement was unmet or could not be verified.` / 表示任务完成并停止 agent，附总结
- **terminates_sequence**：False（注意：本身不"终止序列"，但通过 `is_done=True` 触发 Agent Loop 退出）
- **Pydantic 参数**：

  | 字段 | 类型 | 默认 | 描述 |
  |---|---|---|---|
  | `text` | `str` | (必填，`min_length=1`) | Final message to the user. ONLY report data you directly observed in page state, tool outputs, or screenshots during this session.（含 anti-hallucination：禁止用训练知识补缺口、禁止引用压缩记忆里未亲验的步骤、不确定要明说；必须非空） |
  | `success` | `bool` | `True` | Whether the task was completed successfully. 任何需求未满足 / 页面无预期数据 / 步骤无法核验 → 设 False |

- **主要逻辑**（[actions.py:1418-1433](../../src/tree_walker/tools/actions.py)）：

  ```python
  async def _action_done(self, params: dict, browser: BrowserSession) -> ActionResult:
      success = params.get("success", True)
      text = (params.get("text") or "").strip()
      if not text:
          # done 必须终止（is_done=True 才退出循环），空 text 不能走 soft-miss
          # （会变非终止循环）。兜底默认值保证终止 + 让退化情形在日志可见。
          text = "(no summary provided)"
          logger.warning("done called with empty text; substituting default summary")
      memory = f"Task completed: {success} - {text[:100]}"
      logger.info(memory)
      return ActionResult(
          is_done=True,
          success=success,
          extracted_content=text,
          long_term_memory=memory,
      )
  ```

- **CDP 调用清单**：无（纯状态信号，`browser` 形参未用）

- **注意事项**：
  - `text` 字段描述强调"只能报告直接观察到的数据" + anti-hallucination，防止 LLM 编造结果；schema 层 `min_length=1` + handler 层 `text.strip()` 运行时守卫双层（`Tools.execute` 路径不经 param_model 校验）
  - 双写回显：`extracted_content == text`（全文）、`long_term_memory == "Task completed: {success} - {text[:100]}"`（一行摘要，>100 字截断）、`logger.info(memory)`
  - 空 / 纯空白 / 缺失 `text`：warn + 兜底 `"(no summary provided)"`，仍 `is_done=True`（done 必须终止，不走 soft-miss）
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

- **主要逻辑**（`_action_dropdown_options` 为多类型 dispatcher；格式化 helper `_format_options_result` + 回显 helper `_describe_dropdown` 在 actions.py）：

  action 层做廉价预分类，按下拉类型分发到不同 session 方法，所有类型共用 `_format_options_result` 的 json 编码 + 序号 + `select_dropdown` 用法提示 + 短/长回显（`source` 折进 `long_term_memory` 作诊断通道）：
  - **native `<select>`**：复用 `fetch_select_options(backend_node_id)`（`DOM.resolveNode` + `Runtime.callFunctionOn`），精确绑定到 index 指定的那个 select（修掉原 `querySelectorAll('select option')` 全页扫描 bug）；`source=native`，long_term_memory 无 `via` 后缀（P0 字节一致）。
  - **combobox**（`_is_autocomplete_field` + `aria-controls`/`aria-owns`）：调 `expand_and_fetch_combobox_options` —— 真实 `click_element` 展开 → `sleep 0.5s` 等懒加载 → `getElementById(aria-controls)` 读 listbox → `finally` 强制 `send_keys("Escape")`+`blur()` 收起；`source=combobox`（**实验性**）。
  - **其余**：委托 session 轻量 dispatcher `fetch_dropdown_options`，顺序试 `_fetch_aria_options`（`[role=option]`/`[role=menuitem"]`）→ `_fetch_custom_class_options`（`.dropdown`/`.ui` 下 `.item/.option/[data-value]`）→ `search_children_for_dropdowns`（BFS 子树 depth 4）；首个命中返回（`source=aria`/`custom`/`child-depth-N`），全未命中返回友好 error。

  ```python
  async def _action_dropdown_options(self, params, browser):
      index = params["index"]
      entry, error = await self._get_element_by_index(index, browser)
      if error:
          return error
      tag = (getattr(entry, "tag_name", "") or "").upper()
      backend_id = getattr(entry, "backend_node_id", None)
      is_combo, _ = self._is_autocomplete_field(entry)
      attrs = getattr(entry, "attributes", {}) or {}
      try:
          if tag == "SELECT":                      # native：P0 路径零改动
              raw = await browser.fetch_select_options(backend_id)
              return self._format_options_result(raw, entry, index, "native")
          if is_combo and (attrs.get("aria-controls") or attrs.get("aria-owns")):  # combobox
              raw = await browser.expand_and_fetch_combobox_options(backend_id)
              return self._format_options_result(raw, entry, index, "combobox")
          dispatched = await browser.fetch_dropdown_options(backend_id)            # aria/custom/子树
          if dispatched["source"] is None:
              return ActionResult(error=f"Index {index} is a [{tag}], not a recognized dropdown ...")
          return self._format_options_result(dispatched["options"], entry, index, dispatched["source"])
      except Exception as e:
          return ActionResult(error=f"Failed to read dropdown options: {e}")
  ```

- **CDP 调用清单**（所有 reader 均为 `DOM.resolveNode({backendNodeId})` → `Runtime.callFunctionOn({objectId, functionDeclaration, returnByValue})`，无新 CDP 域）：

  | 下拉类型 | session 方法 | functionDeclaration 核心 | 选项上限 |
  |---|---|---|---|
  | native `<select>` | `fetch_select_options` | `Array.from(this.options).map(...)` | — |
  | ARIA menu/listbox | `_fetch_aria_options` | `querySelectorAll('[role="menuitem"],[role="option"]')` | 200 |
  | custom class | `_fetch_custom_class_options` | `.dropdown/.ui` 下 `.item,.option,[data-value]` | 200 |
  | combobox | `expand_and_fetch_combobox_options` | `getElementById(aria-controls)` 下 `[role=option],li` + 展开/收起 | 200 |
  | 子树搜索 | `search_children_for_dropdowns` | BFS depth 4，classify + readAria/readCustom | 200 |

  combobox 另调用 `click_element`（展开，经 `Input.dispatchMouseEvent`/JS 回退）与 `send_keys("Escape")`（`Input.dispatchKeyEvent`，finally 强制收起）。

- **注意事项**：
  - 支持 native `<select>` / ARIA menu·listbox / custom class（Semantic UI 等）/ combobox（`aria-controls` 独立 listbox）/ 子树搜索（depth 4）；非任何已知类型返回友好 error，提示用 click 手动展开（设计规格见 `docs/tools-optimize/dropdown_options_follow_up.md`）。
  - combobox 为**实验性**：browser-use 自身跳过了全部 combobox/ARIA 测试；本实现用固定 `sleep 0.5s` + `finally` 强制收起 + 200 选项上限，框架差异（React Portal / Material）靠手测验收。
  - 输出每行 `序号: text=<json>, value=<json> (selected)`，json 编码保证含引号/特殊字符的文本可被 LLM 精确复制到 `select_dropdown(index=N, value=...)`；`long_term_memory` 为 `Got N options from [TAG] {label} at index N [via <SOURCE>]`。
  - 成功回显 short/long 分离：`extracted_content` 为紧凑列表（靠 `ActionResult.__str__` 的 500 字符截断自然兜底，工具只读幂等可重调），`long_term_memory` 为简短摘要（带选项数 + 类型 source）。
  - 空选项诊断（G5 进阶）：非 native 类型空选项时追加类型诊断（如「Listbox found but no [role=option] children」）；native 空沿用 P0 仅 hint。

---

### 4.5 `evaluate`

- **description**：执行任意 JS 并返回归一化结果（详见 `ACTION_DEFINITIONS["evaluate"]`，`models.py`）；对齐 browser-use `service.py:1759-1867`
- **terminates_sequence**：True（执行任意 JS 可能改变页面状态）
- **Pydantic 参数**（`EvaluateParams`，`models.py:183-194`）：

  | 字段 | 类型 | 描述 |
  |---|---|---|
  | `code` | `str` | JavaScript 源码（建议包 IIFE + try-catch、仅用浏览器 API、控制输出体积） |

  > 阶段一保持 `code` 唯一参数（对齐 browser-use）；`args` / 元素句柄往返 / per-call `await_promise`/`timeout` 属阶段二（见 `docs/tools-optimize/evaluate.md`）。

- **主要逻辑**：
  - **action 层**（`_action_evaluate`，[actions.py:1272](../../src/tree_walker/tools/actions.py)）：两层分流——`browser.evaluate` 抛异常 → `ActionResult(error="Evaluate failed: ...")` + `logger.warning`（硬错误）；成功 → 归一化文本截断到 `eval_result_max_chars` 进 `extracted_content`、`_eval_long_term_memory`（短≤200 回显 / 长塌缩为长度摘要，`actions.py:130`）进 `long_term_memory`。
  - **session 层**（`BrowserSession.evaluate`，[session.py:2106](../../src/tree_walker/browser/session.py)）：**不复用 `execute_js`**（evaluate 要完整 result dict 做 null/undefined 区分 + 归一化 + 异常富化）——`_validate_and_fix_javascript`（6 条 regex 预处理，`session.py:260`，移植 browser-use `service.py:1869-1932`）→ 单次 `Runtime.evaluate` → `_normalize_eval_result`（`session.py:295`）/ `_format_eval_exception`（`session.py:319`）。

    ```python
    async def evaluate(self, code: str) -> str:
        validated_code = _validate_and_fix_javascript(code)
        result = await self.client.send.Runtime.evaluate(
            {"expression": validated_code, "returnByValue": True,
             "awaitPromise": True, "timeout": 30000},
            session_id=self.current_session_id,
        )
        if result.get("exceptionDetails"):
            raise RuntimeError(_format_eval_exception(result["exceptionDetails"], validated_code))
        result_data = result.get("result", {})
        if result_data.get("wasThrown"):
            raise RuntimeError("JavaScript execution failed (wasThrown=true)")
        return _normalize_eval_result(result_data)
    ```

- **结果归一化**（`_normalize_eval_result`，对齐 browser-use `service.py:1807-1819`，bool/null 取 JS 字面量）：`value` 键缺失→`"undefined"`；`dict`/`list`→`json.dumps(ensure_ascii=False)`（告别旧版 `str(result)` 的 Python repr）；`bool`→`"true"`/`"false"`；`None`→`"null"`；`int`/`float`/`str`→`str()`。
- **异常富化**（`_format_eval_exception`，适度超越 browser-use）：`exceptionDetails.text` + `exception.description`（含栈，browser-use 丢弃）+ `validated_code[:500]` 片段 → `RuntimeError`，action 包装为 `"Evaluate failed: ..."`。
- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `Runtime.evaluate` | `{expression: validated_code, returnByValue:True, awaitPromise:True, timeout:30000}` | `BrowserSession.evaluate` → session.py:2106 |

- **注意事项**：
  - `execute_js`（`session.py:2090`）**原样不动**，仍服务 `extract` / `search_page` / `find_elements` / scroll；evaluate 走专用 `BrowserSession.evaluate`，零回归。
  - 超时硬编码 30 秒；JS 异常 / `wasThrown` → `RuntimeError` → `ActionResult(error="Evaluate failed: ...")` + `logger.warning`。
  - `extracted_content` 截断到 `eval_result_max_chars`（默认 2000，env `AGENT_TRUNCATE_EVAL_RESULT`）；`long_term_memory` 短结果回显、长结果塌缩为长度摘要。
  - 不移植 browser-use 的图片提取（`metadata['images']`）/`include_extracted_content_only_once`——本项目 `ActionResult` 无对应字段。
  - 完整方案见 `docs/tools-optimize/evaluate.md`；测试见 `tests/test_evaluate.py`。

---

### 4.6 `extract`

- **description**：`Extract specific information from the current page as clean markdown (via an LLM). Paginate large pages with start_from_char; dedupe across calls with already_collected.` / 把当前页面转成干净 markdown 后，用一次（专用小模型的）LLM 调用按需提炼/结构化抽取；长页面可分页、跨页可去重
- **terminates_sequence**：False
- **Pydantic 参数**（[models.py `ExtractParams`](../../src/tree_walker/tools/models.py)）：

  | 字段 | 类型 | 默认 | 描述 |
  |---|---|---|---|
  | `query` | `str` | （必填） | 要抽取的信息（≡ browser-use `query`；阶段二由 `goal` 重命名而来） |
  | `extract_links` | `bool` | `True` | 源 markdown 是否保留 `<a href>` URL（False=纯文本抽取） |
  | `extract_images` | `bool` | `True` | 源 markdown 是否保留 `<img src>` URL |
  | `start_from_char` | `int` (`ge=0`) | `0` | 分页续抽的字符偏移（用上一次被截断的 extract 回传的 offset 继续） |
  | `already_collected` | `list[str] \| None` | `None` | 已抽取项（跨页/分块去重），去空项后拼进 prompt 让模型跳过精确重复 |

- **封装分层**：
  - **session 层**（[session.py `BrowserSession.get_page_html`](../../src/tree_walker/browser/session.py)）：单次 `DOM.getDocument(depth=-1, pierce=True)`，经 [html_source.py](../../src/tree_walker/browser/html_source.py) 的 `document_body_to_html` 重建干净 HTML（递归 `children`/`shadowRoots`/`contentDocument`，剥 script/style/template/HEAD，门控 `<a href>`/`<img src>`）；失败返回 `""`。跨源 iframe `contentDocument=None` 不可达。
  - **纯函数层**（[extract_markdown.py](../../src/tree_walker/tools/extract_markdown.py)）：`extract_clean_markdown`（`markdownify` + 去噪 + 折叠空行）、`chunk_markdown_by_structure`（按行贪心打包、表头延续、`MarkdownChunk`）。
  - **action 层**（[actions.py `_action_extract`](../../src/tree_walker/tools/actions.py)）：编排 —— 取 markdown → 按 `extract_chunk_max_chars` 分块取 `start_from_char` 所在单块 → 调 `llm.extract`（带 `already_collected`/`call_timeout`）→ 大结果落盘 → 回写分页进度。

- **主要逻辑**（[actions.py](../../src/tree_walker/tools/actions.py)）：

  ```python
  async def _action_extract(self, params: dict, browser: BrowserSession) -> ActionResult:
      query = params["query"]; tr = self._truncation; schema = getattr(self, "_extraction_schema", None)
      # 1) 源：CDP HTML → markdown（取代 document.body.innerText）；空则降级 execute_js outerHTML
      html_text = await browser.get_page_html(extract_links=..., extract_images=...)
      if not html_text:
          try: html_text = await browser.execute_js("document.documentElement.outerHTML") or ""
          except Exception as e: logger.warning("extract: HTML source failed: %s", e); html_text = ""
      if not html_text: return ActionResult(extracted_content="(empty page)")
      md = extract_clean_markdown(html_text, extract_links=..., extract_images=...)
      if not md.strip(): return ActionResult(extracted_content="(empty page)")

      llm = getattr(self, "_extract_llm", None)
      if llm is None:  # 脱离 Agent 直接用 Tools() → 降级截断 markdown
          return ActionResult(extracted_content=md[params["start_from_char"]:...+tr.extract_fallback_max_chars] or "(no content at offset)")

      # 2) 分页：取 start_from_char 所在单块（一次只抽一块）
      chunks = chunk_markdown_by_structure(md, max_chars=tr.extract_chunk_max_chars)
      if params["start_from_char"] >= chunks[-1].end_index:
          return ActionResult(extracted_content="(no more content at this offset; extraction complete)")
      ...  # 定位 target 块、块内子切片 chunk_content

      # 3) 抽取（去重 + 内层超时）；TimeoutError → "Extract timed out"，其它异常 → "Extract failed"
      try:
          result = await llm.extract(query, chunk_content, max_content_chars=tr.extract_chunk_max_chars,
                                     output_schema=schema, already_collected=params["already_collected"],
                                     call_timeout=tr.extract_call_timeout or None)
      except TimeoutError as e: return ActionResult(error=f"Extract timed out: {e}")
      except Exception as e: return ActionResult(error=f"Extract failed: {e}")

      # 4) 分页进度提示（必须落在 __str__ 的 500 字窗口内可见）
      # 5) 大结果（>= extract_save_threshold）落盘到 extract_output/extract_<ms>.{json|md}
      # 结构化结果保持 JSON 纯净（提示走 long_term_memory）；free-text 提示前置（防 500 字截断）
      return ActionResult(extracted_content=visible, long_term_memory=mem or None)
  ```

  二次调用 LLM 抽取。`_extract_llm` / `_extraction_schema` 由 `Agent.__init__` 接线注入（默认 `_extract_llm` 复用主 llm；`extraction_schema` 来自 `AgentSettings`）。**源 = CDP HTML → markdown**（不再是 `document.body.innerText`），保留表格/链接/图片语义。长页面按 `extract_chunk_max_chars` 分块，一次只抽 `start_from_char` 所在块，并把「下一块 offset」作为提示回传，agent 据此 `start_from_char=` 续抽。`already_collected` 拼进 prompt 去重。结果 ≥ `extract_save_threshold`（默认 10000）落盘到 `extract_output/`、回显「saved to {path}」。LLM 异常分级（`TimeoutError`/其它 → `ActionResult(error=...)`），不冒泡通用 catch。

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `DOM.getDocument` | `{depth:-1, pierce:true}` | session.py `get_page_html`（经 `html_source.document_body_to_html`） |
  | `Runtime.evaluate`（降级） | `{expression: "document.documentElement.outerHTML"}` | session.py `execute_js` |
  | `messages.create`（LLM） | free-text 补全 / 结构化 tool-use（`extract_result` 工具、`input_schema=output_schema`、`tool_choice` 强制） | [client.py](../../src/tree_walker/llm/client.py) |

- **注意事项**：
  - **字段重命名**：阶段二 `goal` → `query`（≡ browser-use）。破坏式但纯 LLM-facing（schema 经 `model_json_schema()` 自动传播），无持久化状态要迁移；`extraction_schema` 正交不受影响。
  - **markdown 源**：CDP `DOM.getDocument` 树重建 HTML（穿透 shadow DOM + 同源 iframe），`execute_js outerHTML` 仅作降级后备；跨源 iframe 不可达。
  - `_extract_llm` 默认复用主 `llm`；可经 `AgentSettings.extract_llm` 或 env `AGENT_EXTRACT_MODEL/_API_KEY/_BASE_URL/_MAX_TOKENS` 配专用小模型（未设则复用主 llm）。
  - `extraction_schema` 经 `AgentSettings.extraction_schema` 注入、对 LLM 隐藏（不进 `ExtractParams`）；schema 非法（非 object / 无 properties）自动降级 free-text。
  - **分页进度可见性**：LLM 经 `ActionResult.__str__` 只看 `extracted_content` 前 500 字 → 提示必须前置（free-text）或走 `long_term_memory`（结构化，保 JSON 纯净）。
  - **inner timeout**：`extract_call_timeout`（env `AGENT_EXTRACT_CALL_TIMEOUT`，默认 0=关）；开启时经 `asyncio.wait_for(asyncio.to_thread(messages.create))` 计时，且必须 `< action_timeout`（否则外层先取消），分块多时建议配套调大 `AGENT_ACTION_TIMEOUT`。
  - **配置**：`TruncationSettings` 新增 `extract_chunk_max_chars`/`extract_save_threshold`/`extract_output_dir`/`extract_call_timeout`（均有 env）。

---

### 4.7 `find_elements`

- **description**：`Query DOM elements by CSS selector (zero LLM cost, instant). Returns matching elements with tag, text, and attributes...` / 按 CSS 选择器查询元素（瞬时、零 LLM 成本），穿透开放 shadow / 同源 iframe，返回 tag / text / 指定 attributes / children_count（可选几何 + visible）+ 总数；`return_node_ids=True` 另走 `DOM.performSearch` 回稳定 backendNodeId（可直接喂 click/input_text）。
- **terminates_sequence**：False
- **Pydantic 参数**（[models.py `FindElementsParams`](../../src/tree_walker/tools/models.py)）：

  | 字段 | 类型 | 默认 | 描述 |
  |---|---|---|---|
  | `selector` | `str` | （必填） | CSS selector to query elements（如 `"table tr"`、`"a.link"`、`"div.product"`） |
  | `attributes` | `list[str] \| None` | `None` | 指定要提取的属性（如 `["href","src","class"]`）；不传则只返回 tag + text；`src`/`href` 解析为绝对 URL |
  | `max_results` | `int` (`ge=1, le=200`) | `50` | 返回元素上限（`total` 始终回真实命中数，即使被截断） |
  | `offset` | `int` (`ge=0`) | `0` | 返回元素的起始下标（分页）；`total` 始终是全量命中数（含 shadow / 同源 iframe） |
  | `include_text` | `bool` | `True` | 是否包含元素文本 |
  | `first_only` | `bool` | `False` | 只返回首个匹配（`total` 仍回全量） |
  | `include_geometry` | `bool` | `False` | 附带每元素 `getBoundingClientRect()` + 可信 `visible`（祖先链 display/visibility/opacity + 非零尺寸） |
  | `return_node_ids` | `bool` | `False` | 走 `DOM.performSearch` 回稳定 backendNodeId（可直接作 click/input_text 的 `index`/`element_id`；较重，无 text） |

- **封装分层**：
  - **session 层（JS 路径）**（[session.py `_build_find_elements_js` / `BrowserSession.find_elements`](../../src/tree_walker/browser/session.py)）：单次 `Runtime.evaluate` 执行 IIFE：`_collectAll` 递归收集顶层文档 + **开放 shadow root** + **同源 iframe contentDocument**（镜像 `_SEARCH_PAGE_JS_BODY._collectText`；closed shadow / 跨源 iframe 跳过），逐元素 `matches(SELECTOR)` 命中后取 `{index(全局序), tag, text?, attrs?, children_count, origin?, rect?, visible?}`，`offset` 窗口分页，返回 `{elements, total, showing, offset, has_more}`；`src`/`href` 走 DOM 属性（`el.href`）拿绝对 URL、其余走 `getAttribute`，null 属性跳过；text 截 300、attr 值截 500。选择器合法性先 `querySelector` 校验一次（invalid → `{error}`）。JS 层 `{error:...}` / 空返回 → `RuntimeError`，由 action 捕获。
  - **session 层（node_ids 路径）**（[session.py `BrowserSession.find_elements_node_ids`](../../src/tree_walker/browser/session.py)）：复用 `find_text` 的 `DOM.performSearch`（query=CSS 选择器，`includeUserAgentShadowDOM`）→ `getSearchResults`（offset 窗口）→ 逐 `nodeId` `describeNode` 取 `backendNodeId` + `nodeName`；`finally` 里 `discardSearchResults` 清理。返回 `{node_ids:[{backend_id, tag}], total, showing, offset, has_more}`。较重（每元素一次 describeNode 往返）。
  - **action 层**（[actions.py `_action_find_elements` / `_format_find_results` / `_format_node_id_results`](../../src/tree_walker/tools/actions.py)）：`return_node_ids` 选 session 方法 + 格式化器，其余三层分流不变 —— 硬错误（`RuntimeError`）→ `ActionResult(error="Find elements failed: ...")` + `logger.warning`；软 miss（`total==0`）→ `extracted_content == long_term_memory == 'No elements found matching "..."'`；命中 → 格式化文本进 `extracted_content`、紧凑摘要 `'Found N element(s) matching "..."'` 进 `long_term_memory`（node_ids 路径附 `(node ids)`）。大结果（≥ `TruncationSettings.find_elements_save_threshold`）落盘到 `find_elements_output/`，`extracted_content` 回预览 + 路径（镜像 search_page/extract）。

- **主要逻辑**（[actions.py](../../src/tree_walker/tools/actions.py)）：

  ```python
  async def _action_find_elements(self, params: dict, browser: BrowserSession) -> ActionResult:
      selector = params["selector"]
      max_results = params.get("max_results", 50)
      offset = params.get("offset", 0)
      return_node_ids = params.get("return_node_ids", False)
      try:
          if return_node_ids:
              data = await browser.find_elements_node_ids(selector, max_results=max_results, offset=offset)
              formatter = _format_node_id_results
          else:
              data = await browser.find_elements(
                  selector, attributes=params.get("attributes"), max_results=max_results, offset=offset,
                  include_text=params.get("include_text", True), first_only=params.get("first_only", False),
                  include_geometry=params.get("include_geometry", False),
              )
              formatter = _format_find_results
      except Exception as e:
          logger.warning("find_elements(%r) failed: %s", selector, e)
          return ActionResult(error=f"Find elements failed: {e}")
      total = data.get("total", 0)
      if total == 0:
          msg = f'No elements found matching "{selector}"'
          return ActionResult(extracted_content=msg, long_term_memory=msg)
      formatted = formatter(data, selector)
      # ... 大结果落盘（镜像 _action_search_page） ...
      return ActionResult(extracted_content=visible, long_term_memory=memory)
  ```

  返回每个匹配元素的全局 index、tag、text（截断 300 字符）、指定 attributes（值截断 500）、children_count、可选 `origin`（`(in shadow DOM)` / `(in iframe)`）、可选 `rect`+`visible`；并回显真实命中总数 `total` + offset-aware 尾注 `... showing A–B of N total elements. Call again with offset=...`。`return_node_ids=True` 返回 `[backend_id] <tag>`，标注「pass as index= or element_id= to click/input_text」。

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `Runtime.evaluate` | `{expression, returnByValue, awaitPromise, timeout:30000}` | session.py（`execute_js`，JS 路径） |
  | `DOM.performSearch` | `{query, includeUserAgentShadowDOM}` | session.py（`find_elements_node_ids`，node_ids 路径） |
  | `DOM.getSearchResults` | `{searchId, fromIndex, toIndex}` | 同上 |
  | `DOM.describeNode` | `{nodeId}` → `backendNodeId` | 同上 |
  | `DOM.discardSearchResults` | `{searchId}`（finally 清理） | 同上 |

- **错误分级**：非法 CSS 选择器（`querySelector` 抛 `DOMException`）→ JS try/catch 捕获 → `{error}` → session `RuntimeError` → action 硬错误 `Find elements failed: ...`；零命中（`total==0`）不是错误，走软回显；JS 执行异常（`exceptionDetails`）由 `execute_js` 翻译成 `RuntimeError("JS error: ...")` 上抛；node_ids 路径 CDP 异常（performSearch/describeNode 失败）同样上抛 → 硬错误；`discardSearchResults` 异常被 finally 吞掉不影响结果。落盘 `OSError` 不失败，仅 `logger.warning` + 回退 inline。
- **安全**：用户值（selector / attributes / max_results / offset / include_text / first_only / include_geometry）经 `json.dumps` 注入成 `var` 声明（绝不 f-string 拼用户串），含 `"` / `\` / 中文的选择器安全转义；node_ids 路径的 selector 作为 `DOM.performSearch` 的 `query`（CDP 原生参数，非 JS 拼接）。
- **限制**：穿透**开放** shadow root + **同源** iframe（镜像 search_page 阶段二）；**closed** shadow root（`shadowRoot=null`）与**跨源** iframe（`contentDocument` 抛 `SecurityError`）跳过——属阶段三。`element_id`/index 经 selector_map 解析，仅交互元素可解析为可点击目标。
- **阶段二（find_elements_follow_up.md）**：P2-A 落盘 + first_only；P2-B 穿透 shadow/iframe + offset + 几何/visible；P2-C `return_node_ids`（performSearch 链）+ click/input_text `element_id`。

---

### 4.8 `find_text`

- **description**：`Scroll to and highlight text on the page` / 滚动到并高亮页面上的文本
- **terminates_sequence**：False
- **Pydantic 参数**：

  | 字段 | 类型 | 描述 |
  |---|---|---|
  | `text` | `str` (`min_length=1`) | Text to search for on the page |

- **主要逻辑**（[actions.py:787](../../src/tree_walker/tools/actions.py)，薄包装委托 session 层 `find_text`）：

  ```python
  async def _action_find_text(self, params: dict, browser: BrowserSession) -> ActionResult:
      text = params["text"]
      try:
          info = await browser.find_text(text)
      except Exception as e:
          logger.warning("find_text(%r) failed: %s", text, e)
          return ActionResult(error=f"Find text failed: {e}")
      if not info.get("found"):
          # 软回显：「文本不在页面上」是可操作信息而非工具失败（对齐 browser-use + search_page）
          msg = f"Text '{text}' not found on page"
          return ActionResult(extracted_content=msg, long_term_memory=msg)
      method, tag = info.get("method"), info.get("tag")
      if tag:
          memory = f"Scrolled to text '{text}' into view (found in <{tag}>, via {method})"
      else:
          memory = f"Scrolled to text '{text}' into view (via {method})"
      return ActionResult(extracted_content=memory, long_term_memory=memory)
  ```

  委托给 `BrowserSession.find_text()`（[session.py:1501](../../src/tree_walker/browser/session.py)）：CDP `DOM.performSearch` **三段 XPath 查询链**（直接文本 `contains(text(), L)` → 全文本内容 `contains(., L)` → 属性值 `@*[contains(., L)]`），每段命中即 `DOM.scrollIntoViewIfNeeded` 滚入视口；三段全空走 JS TreeWalker 兜底（经 `execute_js` → `Runtime.evaluate`）。参照 browser-use `default_action_watchdog.py:2682-2774` 的 `on_ScrollToTextEvent`（**实际代码**，非其文档 §11 描述），并修复其 4 个 bug：① XPath 引号转义 `_xpath_string_literal`（[session.py:33](../../src/tree_walker/browser/session.py)——browser-use 用 f-string 直插，text 含 `"` 即语法错误）；② `discardSearchResults` 放 `try/finally`（命中也清理，修 searchId 泄漏）；③ `performSearch` 传 `includeUserAgentShadowDOM:True`（穿透 Shadow DOM）；④ 不调无用的 `getDocument`（`performSearch` 自带全文检索）。命中后经 `DOM.describeNode` 取 `backendNodeId` → `highlight_element`（`Overlay.highlightNode`）兑现 highlight，best-effort。

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `DOM.performSearch` | `{query, includeUserAgentShadowDOM:True}` → `{searchId, resultCount}` | session.py:1531（`find_text`） |
  | `DOM.getSearchResults` | `{searchId, fromIndex:0, toIndex:1}` → `{nodeIds}` | session.py:1538 |
  | `DOM.scrollIntoViewIfNeeded` | `{nodeId}` | session.py:1546 |
  | `DOM.discardSearchResults` | `{searchId}`（finally，含命中路径） | session.py:1559 |
  | `DOM.describeNode` | `{nodeId}` → `{node:{backendNodeId, nodeName}}`（nodeId→backendNodeId） | session.py:1604（`_highlight_search_node`） |
  | `Overlay.highlightNode` | `{highlightConfig, backendNodeId}`（经 `highlight_element`，best-effort） | highlight.py:35 |
  | `Runtime.evaluate` | TreeWalker 兜底 JS（XPath 全空时，经 `execute_js`） | session.py:1593 |

- **注意事项**：
  - 已弃用非标准 `window.find()`：改用标准 CDP `DOM.performSearch` 三段 XPath，覆盖直接文本 / 跨元素分裂文本 / 属性文本 / Shadow DOM。
  - **未找到软回显**（非 error）：返回 `extracted_content="Text '...' not found on page"`，对齐 browser-use + `search_page`；仅 CDP 层异常返回 `error="Find text failed: ..."`。
  - **引号安全**：`_xpath_string_literal` 把任意 text（含 `"`/`'`）转成 XPath 安全字面量（双引号 / 单引号 / `concat()`）；browser-use 实现遇到含引号文本会崩。
  - **nodeId 与 backendNodeId**：`performSearch`/`getSearchResults` 返回前端 `nodeId`（`scrollIntoViewIfNeeded` 直接吃）；`highlight_element` 需 `backendNodeId`，经 `DOM.describeNode` 转换。
  - 高亮落在元素框层级（大容器命中时框较粗）；精确文本高亮（原生选区）留待后续。
  - `text` 加 `min_length=1`：空串会让 `contains(text(), "")` 命中全部元素，无意义。

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

- **description**：`Read content from a local UTF-8 text file.` / 读取本地 UTF-8 文本文件内容
- **terminates_sequence**：False
- **Pydantic 参数**（[models.py:186-188](../../src/tree_walker/tools/models.py)）：

  | 字段 | 类型 | 描述 |
  |---|---|---|
  | `path` | `str` | Path to a local text file to read（UTF-8 by default; see encoding） |
  | `encoding` | `str \| None` | Text encoding to decode with（默认 `None`→UTF-8；设 `latin-1`/`cp936` 读遗留文件） |
  | `newline` | `str \| None` | Python `open()` newline 模式（默认 `""` 不翻译、保留 `\r\n`；`None` 启用 universal-newline 把 `\r\n` 压成 `\n`） |

- **主要逻辑**（[actions.py:1277-1323](../../src/tree_walker/tools/actions.py)）：

  ```python
  async def _action_read_file(self, params: dict, browser: BrowserSession) -> ActionResult:
      path = params["path"]
      try:
          # newline=""：读时不翻译 \r\n -> \n，原 LF/CRLF 行尾字节级保持（对齐 replace_file / write_file）
          with open(path, "r", encoding="utf-8", newline="") as f:
              content = f.read()
      except FileNotFoundError:
          return ActionResult(error=f"File not found: {path}")
      except UnicodeDecodeError as e:
          logger.warning("read_file(%r) decode failed: %s", path, e)
          return ActionResult(error=f"Failed to decode {path} as UTF-8: {e}")
      except OSError as e:
          logger.warning("read_file(%r) failed: %s", path, e)
          return ActionResult(error=f"Failed to read file {path}: {e}")
      total_chars = len(content)
      total_bytes = len(content.encode("utf-8"))
      max_chars = self._truncation.read_file_max_chars
      if not content:
          # soft-miss：空文件不是错误，如实回报"文件为空"（避免 __str__ 兜底成 "OK" 的歧义）
          msg = f"{path} is empty (0 bytes)"
          logger.info(msg)
          return ActionResult(extracted_content=msg, long_term_memory=msg)
      if total_chars > max_chars:
          # 截断必须告知（read_file 独有；browser-use 文本不限长仅截 memory，无此问题）
          extracted = (
              content[:max_chars]
              + f"\n[...truncated: showing {max_chars} of {total_chars} chars ({total_bytes} bytes total)]"
          )
          memory = f"Read {path} ({total_chars} chars, {total_bytes} bytes; truncated to first {max_chars} chars)"
      else:
          extracted = content
          memory = f"Read {path} ({total_chars} chars, {total_bytes} bytes)"
      logger.info(memory)
      return ActionResult(extracted_content=extracted, long_term_memory=memory)
  ```

  UTF-8 文本读取，`newline=""` 保证 Windows 下 `\r\n` 不被压成 `\n`；内容超 `read_file_max_chars`（默认 5000）时**显式截断并告知**，避免 LLM 把截断处误当文件结尾。

- **返回 / 回显**：
  - 正常（未截断）：`extracted_content` 为文件原文；`long_term_memory` 形如 `Read <path> (<N> chars, <M> bytes)`。
  - 截断：`extracted_content` = 前 `max_chars` 字符 + `\n[...truncated: showing N of M chars (B bytes total)]`；`long_term_memory` 带 `truncated` 标记——LLM 据此知道还有更多内容。
  - 空文件（soft-miss）：`extracted_content == long_term_memory == "<path> is empty (0 bytes)"`——文件存在但为空，不是错误、也不是 `ActionResult.__str__` 兜底的 `"OK"`。
  - 错误分级（均带 `logger.warning`，不冒泡到 `Tools.execute` 通用 catch）：`File not found` / `Failed to decode ... as UTF-8`（文件非 UTF-8）/ `Failed to read file ...`（权限/目录/IO）。

- **CDP 调用清单**：无（纯本地 fs）

- **注意事项**：`encoding` 参数（默认 `None`→UTF-8）支持 latin-1/cp936 等遗留编码；`newline` 参数（默认 `""` 不翻译、保留 `\r\n`；`None` 启用 universal-newline）控制行尾翻译；非法编码名 → `ActionResult(error="Unknown encoding ...")`（`LookupError` 兜底）；decode 失败文案 `Failed to decode {path} as {enc}`（反映真实编码）；字节数用 `len(content.encode(enc))`（CJK 准确，与 `os.path.getsize` 一致）；`read_file_max_chars` 默认 5000，env `AGENT_TRUNCATE_READ_FILE` 可覆盖。图片/PDF/DOCX 等富文档、`offset`/`limit` 分页仍不支持。阶段二详见 `docs/tools-optimize/write_file_follow_up.md` 2.B/2.D。

---

### 4.13 `replace_file`

- **description**：`Replace every occurrence of an exact substring (old) with new text inside an existing local file, in place. Literal match, NOT a regex; case-sensitive; all non-overlapping occurrences are replaced. old must be non-empty and must already exist in the file (zero matches returns 'no occurrences' rather than silently succeeding). Prefer this over write_file for small edits to a large file you have already read.` / 替换本地文件中所有匹配的字面量子串（就地编辑）
- **terminates_sequence**：False
- **Pydantic 参数**（[models.py:191-200](../../src/tree_walker/tools/models.py)）：

  | 字段 | 类型 | 描述 |
  |---|---|---|
  | `path` | `str` | Path to an existing local file to edit in place |
  | `old` | `str` (`min_length=1`) | Exact text to find（字面量子串，非正则；大小写敏感；非空） |
  | `new` | `str` | Replacement text（可为空以删除匹配） |
  | `encoding` | `str \| None` | Text encoding to read/write with（默认 `None`→UTF-8；设 `latin-1`/`cp936` 处理遗留文件） |
  | `newline` | `str \| None` | Python `open()` newline 模式（默认 `""` 不翻译、行尾字节保真） |

- **主要逻辑**（[actions.py:1286-1329](../../src/tree_walker/tools/actions.py)）：

  ```python
  async def _action_replace_file(self, params: dict, browser: BrowserSession) -> ActionResult:
      path = params["path"]
      old = params["old"]
      new = params["new"]
      if not old:
          # min_length=1 覆盖 schema；此运行时守卫兜底 execute 路径（registry 不校验 params）
          return ActionResult(error="replace_file 'old' must be a non-empty string")
      try:
          # newline=""：读写均不翻译 \r\n <-> \n，原 LF/CRLF 行尾字节级保持
          with open(path, "r", encoding="utf-8", newline="") as f:
              content = f.read()
          count = content.count(old)
          if count == 0:
              # soft-miss：未命中不是失败，如实回报"文件未改"（修正静默成功缺陷）
              msg = f"No occurrences of {old!r} found in {path}; file unchanged"
              logger.info(msg)
              return ActionResult(extracted_content=msg, long_term_memory=msg)
          content = content.replace(old, new)
          with open(path, "w", encoding="utf-8", newline="") as f:
              f.write(content)
      except FileNotFoundError:
          return ActionResult(error=f"File not found: {path}")
      except UnicodeDecodeError as e:
          logger.warning("replace_file(%r) decode failed: %s", path, e)
          return ActionResult(error=f"Failed to decode {path} as UTF-8: {e}")
      except OSError as e:
          logger.warning("replace_file(%r) failed: %s", path, e)
          return ActionResult(error=f"Failed to replace text in {path}: {e}")
      final_bytes = len(content.encode("utf-8"))
      memory = (
          f"Replaced {count} occurrence{'s' if count != 1 else ''} of {old!r} with {new!r} "
          f"in {path} ({final_bytes} bytes)"
      )
      logger.info(memory)
      return ActionResult(extracted_content=memory, long_term_memory=memory)
  ```

  字面量 `str.replace`，**全局替换**所有非重叠匹配；读写 `newline=""` 保证 Windows 下 `\r\n`/`\n` 不互相翻译。

- **返回 / 回显**：
  - 命中：`extracted_content == long_term_memory`，形如 `Replaced N occurrence(s) of '<old>' with '<new>' in <path> (<bytes> bytes)`（含替换次数与最终字节数）。
  - 零匹配（soft-miss）：`No occurrences of '<old>' found in <path>; file unchanged`——不假装成功、文件不重写（修正 browser-use 的静默成功缺陷）。
  - 错误分级（均带 `logger.warning`，不冒泡到 `Tools.execute` 通用 catch）：`File not found` / `Failed to decode ... as UTF-8`（文件非 UTF-8）/ `Failed to replace text in ...`（权限/目录/IO）。

- **CDP 调用清单**：无（纯本地 fs）

- **注意事项**：全局替换所有非重叠匹配；大小写敏感；纯字面量（非正则）；`old` 非空（schema `min_length=1` + handler 运行时守卫双层）；不保留备份；**原子写**（阶段二）：写 `path+".tmp"` 再 `os.replace(tmp, path)`，进程崩溃不留半个文件、失败清残留 tmp；`encoding`/`newline` 参数（默认 UTF-8 / `""`）；非法编码名 → `Unknown encoding ...`（`LookupError` 兜底）；decode 失败文案 `Failed to decode {path} as {enc}`；写路径受 `allowed_write_paths` 白名单约束（镜像 `allowed_upload_paths`，越界 → `File path not in allowed write paths`）。阶段二详见 `docs/tools-optimize/write_file_follow_up.md` 2.A/2.B/2.C/2.D。

---

### 4.14 `save_as_pdf`

- **description**：`Save the current page as a PDF. Supports paper_format (letter/legal/a4/a3/tabloid), landscape, scale (0.1-2.0), print_background.` / 将当前页面保存为 PDF
- **terminates_sequence**：False
- **Pydantic 参数**（`SaveAsPdfParams`，`extra="forbid"`）：

  | 字段 | 类型 | 默认 | 描述 |
  |---|---|---|---|
  | `path` | `str` | （必填） | File path to save the PDF（父目录自动创建） |
  | `paper_format` | `Literal["letter","legal","a4","a3","tabloid"]` | `letter` | 纸张尺寸 |
  | `landscape` | `bool` | `False` | 横向打印 |
  | `print_background` | `bool` | `True` | 包含背景图形和颜色 |
  | `scale` | `float` | `1.0` | 渲染缩放（0.1-2.0） |

- **主要逻辑**（[actions.py:854](../../src/tree_walker/tools/actions.py)）：action 层只负责解包参数、委托 `BrowserSession.print_to_pdf`、写盘与回显；CDP 调用收敛进 session 封装（与 `screenshot`→`take_screenshot` 一致）。

  ```python
  async def _action_save_as_pdf(self, params: dict, browser: BrowserSession) -> ActionResult:
      path: str = params["path"]
      paper_format: str = params.get("paper_format", "letter")
      landscape: bool = params.get("landscape", False)
      print_background: bool = params.get("print_background", True)
      scale: float = params.get("scale", 1.0)

      try:
          pdf_bytes = await browser.print_to_pdf(
              paper_format=paper_format, landscape=landscape,
              print_background=print_background, scale=scale,
          )
      except Exception as e:
          logger.warning("save_as_pdf action failed: %s", e)
          return ActionResult(error=f"Failed to generate PDF: {e}")

      try:
          os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
          with open(path, "wb") as f:
              f.write(pdf_bytes)
      except OSError as e:
          return ActionResult(error=f"Failed to save PDF to {path}: {e}")

      meta = f"paper={paper_format}, {len(pdf_bytes)} bytes"
      if landscape:
          meta += ", landscape"
      return ActionResult(extracted_content=f"PDF saved to {path} ({meta})")
  ```

- **Session 封装**（[session.py:766](../../src/tree_walker/browser/session.py) `print_to_pdf`）：纸张英寸查表（letter 8.5×11 / legal 8.5×14 / a4 8.27×11.69 / a3 11.69×16.54 / tabloid 11×17）→ 组装 CDP params → 调 `Page.printToPDF` → base64 解码返回 bytes。`RuntimeError` on no data；CDP 异常 `logger.warning` 后 re-raise。形状对齐 `take_screenshot`。

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `Page.printToPDF` | `{printBackground, landscape, scale, paperWidth, paperHeight, preferCSSPageSize: True}` | session.py:810 |

- **注意事项**：
  - `preferCSSPageSize=True` 硬编码（与 browser-use 一致）——带 `@page` CSS 的页面会覆盖所选纸张。
  - `print_background=True` 保留背景色；CDP 返回的 `data` 是 base64 字符串，`print_to_pdf` 内部解码为 bytes。
  - 写盘前 `os.makedirs` 父目录；CDP 失败与写盘 `OSError` 分级捕获，各自返回明确 `ActionResult(error=...)`。
  - 详细优化方案见 [`docs/tools-optimize/save_as_pdf.md`](../tools-optimize/save_as_pdf.md)。

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

- **description**：`Search page text for a pattern (like grep). Zero LLM cost, instant. Returns matches with surrounding context, element path, and a total count; paginate large result sets with offset. Traverses same-origin iframes and open shadow roots. Set regex=True for regex patterns; use css_scope to search within a section; search_attributes=True to also match href/value/etc. Read-only — does not scroll or highlight (use find_text for that).` / grep 式页面文本搜索：零 LLM 成本、瞬时返回带上下文与元素路径的匹配及总数；offset 分页；穿透同源 iframe / 开放 shadow DOM；可选属性检索；只读不滚动
- **terminates_sequence**：False
- **Pydantic 参数**：

  | 字段 | 类型 | 描述 |
  |---|---|---|
  | `query` | `str`（`min_length=1`） | Text or regex pattern to search for |
  | `regex` | `bool`（默认 `False`） | Treat query as a regex（默认按字面量匹配，自动转义元字符） |
  | `case_sensitive` | `bool`（默认 `False`） | Case-sensitive match（默认大小写不敏感） |
  | `context_chars` | `int`（默认 `150`，`ge=0`） | 每条匹配的上下文字符数 |
  | `css_scope` | `str \| None`（默认 `None`） | CSS 选择器限定搜索范围；未命中报错 |
  | `max_results` | `int`（默认 `25`，`ge=1, le=200`） | 返回匹配上限（`total` 始终报真实总数） |
  | `offset` | `int`（默认 `0`，`ge=0`） | 阶段二：返回的首条匹配的 0-based 偏移（翻页游标；`total` 始终是全量计数） |
  | `search_attributes` | `bool`（默认 `False`） | 阶段二：同时检索元素属性值（href / value / data-*），返回独立的 `attribute_matches` 列表 |

- **主要逻辑**（action 层 [actions.py `_action_search_page`](../../src/tree_walker/tools/actions.py) + session 层 [session.py `BrowserSession.search_page`](../../src/tree_walker/browser/session.py)）：

  action 层是薄编排 + 分级错误：硬错误（CDP 失败 / 非法 regex / `css_scope` 未命中）返回 `ActionResult(error="Search page failed: ...")`；软 miss（`total==0` 且无属性命中）返回 `extracted_content == long_term_memory == "No matches for '...'"`（非 error，对齐 `find_text`）；命中则 `extracted_content` 为格式化清单、`long_term_memory` 为紧凑摘要 `Searched page for "...": N match(es) found.`。阶段二新增：`offset` / `search_attributes` 透传；格式化结果 `len >= search_page_save_threshold`（默认 10000）时分级落盘到 `search_page_output_dir/search_page_<ts>.txt`，返回 preview + 路径（镜像 `extract`）。

  ```python
  async def _action_search_page(self, params: dict, browser: BrowserSession) -> ActionResult:
      query = params["query"]
      try:
          data = await browser.search_page(
              query,
              regex=params.get("regex", False),
              case_sensitive=params.get("case_sensitive", False),
              context_chars=params.get("context_chars", 150),
              css_scope=params.get("css_scope"),
              max_results=params.get("max_results", 25),
              offset=params.get("offset", 0),
              search_attributes=params.get("search_attributes", False),
          )
      except Exception as e:
          logger.warning("search_page(%r) failed: %s", query, e)
          return ActionResult(error=f"Search page failed: {e}")
      total = data.get("total", 0)
      attr_total = data.get("attribute_total", 0)
      if total == 0 and not attr_total:
          msg = f"No matches for '{query}'"
          return ActionResult(extracted_content=msg, long_term_memory=msg)
      formatted = _format_search_results(data, query)
      # 大结果分级落盘（镜像 _action_extract；OSError 不失败只 warning）
      tr = self._truncation
      saved_to = None
      if len(formatted) >= tr.search_page_save_threshold:
          try:
              os.makedirs(tr.search_page_output_dir, exist_ok=True)
              fpath = os.path.join(tr.search_page_output_dir, f"search_page_{int(time.time() * 1000)}.txt")
              with open(fpath, "w", encoding="utf-8", newline="") as f:
                  f.write(formatted)
              saved_to = fpath
          except OSError as e:
              logger.warning("search_page: save to file failed: %s", e)
      visible = (f"Search results ({len(formatted)} chars) saved to {saved_to}. "
                 f"Preview: {formatted[:200]}...").strip() if saved_to else formatted
      memory = f'Searched page for "{query}": {total} match{"es" if total != 1 else ""} found.'
      if attr_total:
          memory += f' (+{attr_total} attribute match{"es" if attr_total != 1 else ""})'
      if saved_to:
          memory += f" Results saved: {saved_to}"
      return ActionResult(extracted_content=visible, long_term_memory=memory)
  ```

  session 层 `BrowserSession.search_page` 组装一个 TreeWalker-TextNodes IIFE（移植自 browser-use `service.py:181-255`）：把范围内所有文本节点拼成带 `{offset, length, node}` 偏移索引的大字符串，用 `g`-flag `RegExp.exec` 循环（含零宽匹配保护）收集匹配，每条回填 `{match_text, context, element_path, char_position}`，返回 `{matches, total, has_more}`。用户值经 `json.dumps` 注入成 `var` 声明（绝不 f-string 拼用户串）；JS 层 `{error:...}` / null 翻译成 `RuntimeError` 上抛。阶段二新增（超越 browser-use）：`_collectText` 递归穿透**开放 shadow root**（`el.shadowRoot`）与**同源 iframe**（`iframe.contentDocument`），跨源 iframe 抛 `SecurityError` 被 `catch` 跳过；`_origin` 在 `element_path` 上标 `(in shadow DOM)` / `(in iframe)`；`offset` 把 `matches.push` 窗口从 `[0, MAX_RESULTS)` 改成 `[OFFSET, OFFSET+MAX_RESULTS)`（仍累计全部 `total`，`has_more` = 当前页之后还有更多）；`search_attributes=True` 时用**非全局** `RegExp` 副本扫元素属性值，回填独立的 `{attribute, value, element_path}` 进 `attribute_matches` / `attribute_total`。

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `Runtime.evaluate` | `{expression, returnByValue: True, awaitPromise: True, timeout: 30000}`（经 `BrowserSession.execute_js`） | session.py `search_page` → `execute_js` |

- **注意事项**：
  - 与 `find_text` 的分工：`find_text` 用 `DOM.performSearch` 链路**滚动 + 高亮**首条匹配到视口；`search_page` 是**只读**的 grep 式全文检索，返回带上下文 / 元素路径 / 总数的匹配清单，不滚动、不高亮。
  - 局限：覆盖**同源 iframe + 开放 shadow DOM** 内的文本（阶段二，超越 browser-use）；**closed shadow root**（`shadowRoot=null`）与**跨源 iframe**（抛 `SecurityError`）不穿透——跨源需 `Target.attachToTarget` 多 session evaluate，列阶段三。

---

### 4.19 `select_dropdown`

- **description**：`Select an option in a dropdown element` / 在下拉元素中选择一个选项
- **terminates_sequence**：False
- **Pydantic 参数**：

  | 字段 | 类型 | 描述 |
  |---|---|---|
  | `index` | `int` | ID of the select element |
  | `value` | `str` | Option value to select |

- **主要逻辑**（[actions.py:912](../../src/tree_walker/tools/actions.py)）：

  action 层瘦壳：先 tag 校验（非 `<select>` 早退报错），再委托 session 层 `set_select_option(backend_node_id, value)`（`DOM.resolveNode` + `Runtime.callFunctionOn`），精确绑定到 index 指定的那个 `<select>`，修掉此前 `document.querySelectorAll('select')[0]` 全页写第一个 select、与 index 无关的范围 bug。成功回显 message；选项未命中或框架拦截时软回显可用选项供 LLM 自纠（对齐 dropdown_options 的 try/except 软降级 + short/long 分离规范）。

  ```python
  async def _action_select_dropdown(self, params: dict, browser: BrowserSession) -> ActionResult:
      index = params["index"]
      entry, error = await self._get_element_by_index(index, browser)
      if error:
          return error
      # tag 校验：非 <select> 早退报错
      tag = (getattr(entry, "tag_name", "") or "").upper()
      if tag != "SELECT":
          return ActionResult(error=f"Index {index} is a [{tag}] element, not a <select>. ...")
      backend_id = getattr(entry, "backend_node_id", None)
      value = params["value"]
      try:
          result = await browser.set_select_option(backend_id, value)
      except Exception as e:
          return ActionResult(error=f"Failed to select option: {e}")
      desc = self._describe_dropdown(entry, index)
      if result.get("success"):
          message = result.get("message", f"Selected option: {value}")
          return ActionResult(extracted_content=message, long_term_memory=f"Selected {json.dumps(value)} in {desc}")
      # 选项未命中 / 框架拦截（点击回退也失败）→ 软回显可用选项供 LLM 自纠
      available = result.get("availableOptions") or []
      if available:
          lines = [f"{i}: text={json.dumps(o.get('text', ''))}, value={json.dumps(o.get('value', ''))}" for i, o in enumerate(available)]
          extracted = "\n".join(lines) + "\n" + f"Use the value in select_dropdown(index={index}, value=...)"
          return ActionResult(extracted_content=extracted, long_term_memory=f"Couldn't select {json.dumps(value)} in {desc} (not an available option)")
      return ActionResult(error=result.get("error", f"Failed to select option: {value}"))
  ```

  > session 层 `set_select_option`（[session.py:1731](../../src/tree_walker/browser/session.py)）移植 browser-use `on_SelectDropdownOptionEvent`（`default_action_watchdog.py:3241-3695`）的 native `<select>` 完整选择链：focus → 三方式设值（`element.value`/`option.selected`/`selectedIndex`）→ dispatch `input`+`change`+`blur` → 读回 `element.value` 验证框架回退 → 回退时点击回退（`mousedown`/`click-on-option`/`mouseup`/`change`）。匹配策略：`option.text` 或 `option.value`，大小写不敏感精确匹配（参数名仍是 `value`，但传 text 也能命中）。

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `DOM.resolveNode` | `{backendNodeId}` | session.py:1731（`set_select_option`） |
  | `Runtime.callFunctionOn` | `{objectId, functionDeclaration: _SELECT_OPTION_JS, arguments:[{value}], returnByValue:True}` | 同上 |
  | `Runtime.callFunctionOn`（回退） | `{objectId, functionDeclaration: _SELECT_OPTION_CLICK_FALLBACK_JS, arguments:[{value:optionIndex}], returnByValue:True}`（仅 `selectionReverted` 时） | 同上 |

- **注意事项**：
  - 仅支持 native `<select>`；非 select 下拉（ARIA menu/listbox、custom class、combobox）走 tag 校验 error，提示用 click 手动展开（P1 蓝图见 [`docs/tools-optimize/select_dropdown.md`](../tools-optimize/select_dropdown.md)）。
  - 框架兼容：`input`+`change`+`blur` 三事件 + 三方式设值 + 读回验证 + 点击回退，覆盖 Vue/React/Svelte 等拦截程序化赋值的场景。
  - 选项未命中不抛错，软回显可用选项（json 编码）+ `select_dropdown(index=N, value=...)` 用法提示。

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

      # 3. 非 file input 时确定正确的 file input（issue #34 Bug 2）
      tag, attrs = entry.tag_name.upper(), entry.attributes
      is_file_input = tag == "INPUT" and attrs.get("type", "").lower() == "file"
      backend_id = entry.backend_node_id
      file_input_ids: list[int] = []
      upload_note = ""
      if not is_file_input:
          file_input_ids = list(self._cached_browser_state.dom_state.file_input_backend_ids)
          if not file_input_ids:
              return ActionResult(error="...no file input found on page")
          if len(file_input_ids) == 1:
              backend_id = file_input_ids[0]            # 唯一 input，无歧义，直接用
              upload_note = "  ℹ️ ...only file input on the page..."
          else:
              # 多个隐藏/不可区分 input（如抖音横/竖封面槽）→ 让页面揭示关联：
              # 点击目标，捕获 Page.fileChooserOpened.backendNodeId（拦截已开，不弹原生框）
              discovered = await browser.discover_file_input_via_click(entry.backend_node_id)
              if discovered is None:
                  return ActionResult(error="...clicking opened no file chooser; likely a "
                      "custom upload dialog — drive it, then upload_file again")
              backend_id = discovered
              upload_note = "  ℹ️ ...the file input the page opened..."

      # 4. 高亮 + 上传（共用 try，highlight best-effort，对齐 _action_click）
      try:
          await browser.highlight_element(backend_id)
          await browser.set_file_input(backend_node_id=backend_id, file_path=file_path, ...)
      except Exception as e:
          return ActionResult(error=f"File upload failed: {e}")

      # 5. 成功回显 + 目标来源说明（直选/页面选中）+ accept 软校验
      memory = self._describe_upload(entry, params["index"], file_path)
      if upload_note:
          memory += upload_note   # "only file input" / "the file input the page opened"
      fin_entry = entry if is_file_input else self._find_node_by_backend_id(backend_id, ...)
      accept_attr = (getattr(fin_entry, "attributes", {}) or {}).get("accept") if fin_entry else None
      if accept_attr and not _file_matches_accept(file_path, accept_attr):
          memory += (f"  ℹ️ Note: file extension does not match this input's "
                     f"accept={accept_attr!r}. Uploaded successfully regardless — "
                     f"browsers do not enforce accept (advisory only). No retry needed.")
      return ActionResult(extracted_content=memory, long_term_memory=memory)
  ```

- **CDP 调用清单**：

  | CDP 命令 | 主要参数 | 行号 |
  |---|---|---|
  | `DOM.setFileInputFiles` | `{backendNodeId, files:[file_path]}`（单文件列表） | session.py:1848 |
  | `DOM.getDocument` (shadow DOM 兜底) | `{depth:-1, pierce:True}` | session.py |

- **注意事项**：
  - `_allowed_upload_paths` 是白名单，未配置时不限制（生产环境强烈建议配置）
  - 不直接调用系统文件选择器，避免阻塞；click 落到 file input 也由 `Page.setInterceptFileChooserDialog` 拦截（见 4.1 click / §CDP 映射表），原生框不再弹出
  - 非 file input 目标的解析（issue #34 Bug 2）：**1 个 input→直接用；多个→`discover_file_input_via_click` 点击目标捕获 `Page.fileChooserOpened.backendNodeId`（页面真正关联的 input）；未命中（自定义弹窗）→ 诚实 error 引导 agent 驱动弹窗**（[actions.py](../../src/tree_walker/tools/actions.py) + [session.py](../../src/tree_walker/browser/session.py)）。**不再用启发式猜测**——实证证明抖音的隐藏 input 无任何可区分的客户端信号（自身/容器/LCA/坐标全失效），旧"取最近"恒选首个=Bug 2 根因
  - 成功回显 `Uploaded 'name' to [TAG] {label} at index N`（`_describe_upload`），写入 `extracted_content` + `long_term_memory`
  - 多 input 场景：`discover_file_input_via_click` 点击目标（拦截已开，不弹原生框），命中→上传到页面选中的 input + `ℹ️` 回显；未命中→**不瞎猜**，返回诚实 error 引导进弹窗（避免静默误传到错误槽位）
  - 解析 file input 的 `accept`，扩展名不符时追加 `ℹ️ Note`（`_file_matches_accept`；**中性 informational，明示"已成功/勿重试"，软校验不阻断上传**——避免诱导 LLM 换 index 重传）

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

- **description**：`Write UTF-8 text to a local file (parent directories are auto-created). Default is overwrite... Set append=True to add... trailing_newline (default True)... Prefer replace_file for in-place edits...` / 写 UTF-8 文本到本地文件（父目录自动创建），默认覆盖；`append=True` 追加；小范围改动用 `replace_file`（详见 `ACTION_DEFINITIONS["write_file"]`，`models.py`）；对齐 browser-use `service.py:1682-1711`
- **terminates_sequence**：False
- **Pydantic 参数**（`WriteFileParams`，`models.py`，`extra="forbid"`）：

  | 字段 | 类型 | 默认 | 描述 |
  |---|---|---|---|
  | `path` | `str` | （必填） | File path to write to（父目录自动创建） |
  | `content` | `str` | （必填） | Text content to write（UTF-8） |
  | `append` | `bool` | `False` | True 追加到既有文件末尾（文件不存在则自动创建）；默认 False 整体覆盖 |
  | `trailing_newline` | `bool` | `True` | True（默认）确保内容以恰好一个换行结尾（已有则不变） |
  | `leading_newline` | `bool` | `False` | True 在内容前补一个换行（追加到缺尾换行的文件时分隔新旧内容） |
  | `encoding` | `str \| None` | `None` | Text encoding to write with（默认 `None`→UTF-8；设 `latin-1`/`cp936` 写遗留文件；回显字节数随之） |
  | `newline` | `str \| None` | `""` | Python `open()` newline 模式（默认 `""` 不翻译、`\n`/`\r\n` 原样；`"\r\n"` 强制 CRLF；`None` 翻译为 OS 原生行尾）。与 `trailing_newline`/`leading_newline`（内容层补 `\n`）正交 |

- **主要逻辑**（[actions.py:1243-1275](../../src/tree_walker/tools/actions.py)）：action 层做换行簿记 → `OSError` 分级捕获 → 字节数回显。无 session 封装（纯本地文件操作，不涉及 CDP）。

  ```python
  async def _action_write_file(self, params: dict, browser: BrowserSession) -> ActionResult:
      path = params["path"]
      content = params["content"]
      append = params.get("append", False)
      trailing_newline = params.get("trailing_newline", True)
      leading_newline = params.get("leading_newline", False)
      # 换行簿记在 action 层；trailing 用守卫式（幂等、不双换行、不破坏 CRLF）
      if leading_newline:
          content = "\n" + content
      if trailing_newline and not content.endswith("\n"):
          content = content + "\n"
      mode = "a" if append else "w"
      try:
          os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
          # newline="" 关闭 Windows 文本模式 \n→\r\n 翻译，保证写出字节 == 字节数回显
          with open(path, mode, encoding="utf-8", newline="") as f:
              f.write(content)
      except OSError as e:
          logger.warning("write_file(%r) failed: %s", path, e)
          return ActionResult(error=f"Failed to write file {path}: {e}")
      written = len(content.encode("utf-8"))
      action_word = "Appended" if append else "Wrote"
      memory = f"{action_word} {written} bytes to {path}"
      logger.info(memory)
      return ActionResult(extracted_content=memory, long_term_memory=memory)
  ```

- **CDP 调用清单**：无（纯本地文件操作）

- **注意事项**：
  - **分级错误**：`OSError`（含 `PermissionError`/`IsADirectoryError`——path 指向目录等）→ `ActionResult(error="Failed to write file {path}: {e}")` + `logger.warning`，不冒泡到 `Tools.execute` 通用 catch（对齐 `save_as_pdf:980-985`）。
  - **追加模式沿用 Python `'a'` 自动创建**：append 到不存在的文件直接创建（与 browser-use `append_file` "要求文件已存在"相反，见 `docs/tools-optimize/write_file.md` "关键差异"第 5 条）。
  - **`trailing_newline` 守卫式**：`not content.endswith("\n")`，幂等不双换行；CRLF（`"foo\r\n".endswith("\n")` 为 True）天然不被破坏。与 browser-use 无条件 `content += '\n'` 不同。
  - **`newline=""` 写盘**：关闭 Windows `\n→\r\n` 翻译，跨平台一致 LF 行尾，且回显字节数（`len(content.encode("utf-8"))`，换行簿记**之后**计算）== 磁盘真实大小。
  - 成功回显 `Wrote|Appended N bytes to {path}`，同时写 `extracted_content` + `long_term_memory`（对齐 navigate/click/find_elements/evaluate 主流约定）；`success` 保持 None（`ActionResult` 校验器拒绝非 done 的 `success=True`）。
  - 自动创建父目录（`makedirs(exist_ok=True)`）；编码默认 UTF-8，可经 `encoding` 参数覆盖（非默认时回显追加 `(encoding: <enc>)`）；非法编码名 → `Unknown encoding ...`（`LookupError` 兜底）。
  - **原子写**（阶段二）：overwrite 写 `path+".tmp"` 再 `os.replace(tmp, path)`，崩溃不留半个文件、失败清残留 tmp；**append 保持 `open(path,"a")` 直接追加**（O(1)、非崩溃安全）。
  - **`newline` 翻译控制**（阶段二）：默认 `""` 不翻译；`"\r\n"` 强制 CRLF；`None` 翻译为 OS 原生行尾。与 `trailing_newline`/`leading_newline`（内容层）正交。
  - **`allowed_write_paths` 白名单**（阶段二）：`Tools(allowed_write_paths=[...])` / env `AGENT_ALLOWED_WRITE_PATHS` 约束写路径（前缀匹配，镜像 `allowed_upload_paths`），越界 → `File path not in allowed write paths`；`None` 全放行。
  - 完整方案见 `docs/tools-optimize/write_file.md`（阶段一）与 `docs/tools-optimize/write_file_follow_up.md`（阶段二）；测试见 `tests/test_write_file.py`。

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
| `resolveNode` | `{backendNodeId}` | click, input_text (坐标方法 3 / 遮挡检查 / JS 回退 / SELECT option), select_dropdown | session.py:580, 721, 769, 1265, 1731 |
| `getDocument` | `{depth:-1, pierce:True}` | upload_file (shadow DOM) | session.py:807 |
| `setFileInputFiles` | `{backendNodeId, files:[path]}` | upload_file | session.py:861 |

#### `Page.*` 域

| CDP 命令 | 参数 | 用于 action | 行号 |
|---|---|---|---|
| `navigate` | `{url}` | navigate, search | session.py:383 |
| `getNavigationHistory` | `{}` | go_back | session.py:391 |
| `navigateToHistoryEntry` | `{entryId}` | go_back | session.py:397 |
| `captureScreenshot` | `{format:png}` | screenshot | session.py:372 |
| `printToPDF` | `{printBackground, landscape, scale, paperWidth, paperHeight, preferCSSPageSize:True}` | save_as_pdf（经 `BrowserSession.print_to_pdf` 封装） | session.py:810 |
| `getLayoutMetrics` | `{}` | scroll, click（`_get_viewport_size` 取视口用于 quad 选择 + 中心裁剪） | session.py:642, 728 |
| `setInterceptFileChooserDialog` | `{enabled:True}` | 会话初始化（`_connect`/`switch_tab`）：拦截原生文件框，click file input 不再弹 OS 选择器（issue #34） | session.py（`_enable_file_chooser_intercept`） |
| `fileChooserOpened` *(事件)* | `{frameId, mode, backendNodeId?}` | 拦截已抑制原生框；`_on_file_chooser_opened` 记日志 **并** 存 `_last_file_chooser`，供 `discover_file_input_via_click` 读取（upload_file 多 input 时点 dropzone 揭示页面关联的 input，issue #34 Bug 2） | session.py（`_on_file_chooser_opened` / `discover_file_input_via_click`） |

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
| `evaluate` | `{expression, returnByValue, awaitPromise, timeout:30000}` | evaluate, find_elements, find_text, dropdown_options, search_page, extract | session.py:785, 612 |
| `callFunctionOn` | `{objectId, functionDeclaration, returnByValue[, arguments]}` | click（坐标方法 3 / 遮挡检查 `elementFromPoint` / JS 回退 `this.click()` / SELECT `this.options`）、select_dropdown（native 选择链 + 点击回退） | session.py:587, 728, 776, 1272, 1731 |

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
