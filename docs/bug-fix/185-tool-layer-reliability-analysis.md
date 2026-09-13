# issue #185 分析：工具层可靠性五现象

- 日期：2026-09-13（B 轮证据 2026-09-09/10，v0.17.0 @ 8ddd92b）
- 证据：`evals 仓 results/self_overestimate/B/`（本地 `D:\dev\git\z_jordon\evals\webarena\results\self_overestimate\B\`）
- 复现探针：`examples/debug_issue185_cdp_shape.py`（CDP exceptionDetails 形状实证，2026-09-13 Chrome 152 实跑）

## 0. 结论速览

| 现象 | 直接根因 | 置信度 | 修复锚点 |
|---|---|---|---|
| ① evaluate SyntaxError 连环 | **A（我们的 bug）**：语法自愈与截断提示拿 `exceptionDetails.text`（值恒为 `"Uncaught"`）匹配，真正的 `SyntaxError: ...` 在 `exception.description` 里——**自愈从未生效**；**B**：所谓「传输截断」实为 GLM 中长代码（300–800 字符）括号失衡/混用 IIFE 风格 | A 已实证（live probe + 日志）；B 强证据（反例排除截断） | `session.py` evaluate 错误处理一处匹配源 |
| ② read_grid 无分组计数 | 功能缺失；LLM 上下文 tally 不可靠（还叠加现象③的双截断链静默丢字符） | 确定 | `ReadGridParams` + `_action_read_grid` |
| ③ read_file 预览 5000 过小 | **双截断链**：5000 字符窗口之外还有 `ActionResult.__str__` 的 `display_max_chars=4000` 静默截断——footer 谎报 "showing 5000 of N"，每块尾 1000 字符 LLM 永远看不见 | 确定（代码链） | `_window_and_echo` footer + `views.py:48` |
| ④ read_grid legacy 网格无提示 | legacy/DOM 通道返回 0 行或 fields 名不匹配时逐 cell 被丢、返回空对象行，**无任何 note 说明不兼容** | 确定（机制），task_112 step6 具体通道未逐帧复演 | `_LEGACY_GRID_READ_JS` / `_action_read_grid` |
| ⑤ screenshot 媒体页报错不明 | `asyncio.wait_for` 超时抛 `TimeoutError`，`str()` 为**空串** → agent 收到字面量 `"Screenshot failed: "`；裸图片文档等不到合成器新帧必然超时（与 P6 screencast 最小化零帧同因） | 确定（日志 + 代码） | `take_screenshot` 异常分层 |

---

## 1. 现象① evaluate「长代码传输截断」——两个根因，其中一个是我们的死代码 bug

### 1.1 全量盘点（B 轮 46 任务）

以 `tree_walker.tools.actions` 的 WARNING 行为口径，evaluate 共 **52 次硬失败**，其中编译期 SyntaxError 约 40 次：

| 错误 | 次数 | 涉及任务数 | 说明 |
|---|---|---|---|
| `Unexpected end of input` | 18 | 11 | 括号失衡（有未闭合定界符到 EOF） |
| `Illegal return statement` | 16 | 12 | 顶层裸 return——**自愈候选本应 100% 修好** |
| `Missing catch or finally after try` | 4 | — | **自愈候选同样覆盖** |
| `Unexpected token '}'` / `')'` | 3 | — | 多余闭合符 |
| `Unexpected token '<' ... is not valid JSON` | 6 | — | 运行期 `r.json()` 失败（fetch 回 HTML），非编译错，不在本现象范围 |

### 1.2 根因 A（P0，已实证）：语法自愈从未生效

`session.py:evaluate`（3582-3613）在 `exceptionDetails` 出现后做三件事：自愈候选试跑（`_syntax_repair_candidates`）、格式化错误、以及 `Unexpected end of input` 时追加「code looks truncated」提示。三者共同的前提是**从 `err_text = exc.get("text")` 里能匹配到错误语义**：

```python
err_text = str(exc.get("text", ""))
...
for candidate in _syntax_repair_candidates(validated_code, err_text):   # 匹配 "Illegal return statement"
...
if "Unexpected end of input" in err_text:                               # 截断提示
```

**真实 CDP 形状**（`examples/debug_issue185_cdp_shape.py` 于 Chrome 152 实测，与三份任务日志一致）：

| 场景 | `exceptionDetails.text` | `exception.description` |
|---|---|---|
| 编译期 SyntaxError | `'Uncaught'` | `'SyntaxError: Illegal return statement'` 等 |
| promise 拒绝（运行期） | `'Uncaught (in promise) SyntaxError: ...'`（**带**语义） | 同文 |

即：**编译期 SyntaxError 的语义只在 `description` 里，`text` 恒为 `"Uncaught"`** → 两个子串匹配永不命中：

- 16 次裸 return + 4 次 missing catch **全部**本应被 `_syntax_repair_candidates` 确定性修复（IIFE 包裹 / 补 catch，2026-09-05 PR #170 已交付），实际一次都没跑过；
- 18 次 `Unexpected end of input` 一次也没收到过截断提示——这直接对应 issue 里「错误信息未引导修正」。

**为什么单测没拦住**：`tests/test_p7_form_interaction_fixes.py` 造的桩是 `{"text": "Uncaught SyntaxError: Illegal return statement"}`——真实 Chrome 对编译期错误**从不**产出这种形状。单测编码了一个不存在的契约。

日志侧证：`_format_eval_exception` 的输出首行是 `text`、次行是 `description`，三份日志全部呈 `JavaScript execution error: Uncaught` + 下一行 `SyntaxError: ...` 的两行形态——`text` 就是不含语义。

### 1.3 根因 B：「传输截断」证伪——是 GLM 括号失衡，不是传输丢字符

「传输中被截断」这一表述源自 agent 在日志里的自我叙述（task_64 step11 "code got mangled/truncated in transport"、task_112 step16 "evaluate JS got truncated"），issue 沿用了该归因。代码链与日志反例都不支持"传输截断"：

1. **代码链无截断点**：SDK 解析 tool_input → `params["code"]` → `_validate_and_fix_javascript`（纯正则替换，只缩短 `\"`→`"` 一类，不删块）→ CDP `expression` 整串下发。`_truncate_actions` 只截**动作条数**不截参数。
2. **反例（截断不可能产生多余 token）**：task_112 step16 的代码全文仅 296 字符、完整可见：以 `((function(){` 开头、以 `}})()` 结尾——**多一个 `}`、少一个 `)`**（双括号 IIFE 前缀配了单括号后缀再手滑多敲一个闭括号）。这是典型的生成侧括号计数失败，任何字符串截断都不会**新增** `}`。
3. **正例（长代码并非必挂）**：task_64 step9 约 600 字符的 fetch 聚合 IIFE 编译通过（失败是运行期 `r.json()`），step12 的单括号 IIFE（约 500 字符）同样编译通过。
4. **错误形态分布吻合生成错误**：EOF-未闭合（漏写闭合）、多余 `}`（多写闭合）、裸 return（忘了包裹）、缺 catch（写了 try 丢 catch）——四个方向都齐，截断只能解释其中一个方向。

结论：GLM 在 300–800 字符量级的嵌套 JS 生成中括号/结构可靠性退化，`Unexpected end of input` 是"漏写闭合"而非"字符串被砍"。agent 归因"transport"是它看不到自己原始输出的误判，并被 issue 采纳。

### 1.4 残余不确定性

18 处 `Unexpected end of input` 的完整代码不可见（`_format_eval_exception` 只回显 500 字符、step echo 只回显 120 字符），**不能 100% 排除** GLM 端点在 tool_use 参数序列化侧存在真截断。鉴别手段（若需坐实）：开 `save_conversation`（`_save_conversation` 已落 LLM 原始 model_output 全文）或 observability jsonl 复跑一轮，比对 LLM 发出的 code 与 CDP 收到的 code。本分析的修复方案对两种成因同样有效，不阻塞。

### 1.5 修复方案

**P0（一处改动，修复死代码）**：`evaluate` 错误处理里的匹配源从 `exc.get("text")` 换成 `text + description` 拼接（运行期 promise 拒绝时语义在 text 里，编译期在 description 里——两者取并集）：

```python
err_text = str(exc.get("text", "")) + " " + str(
    exc.get("exception", {}).get("description") or "")
```

生效后无需新逻辑即可回收：16+4 次确定性自愈 + 18 次截断提示。**同步修单测桩形状**（用真实 CDP 形状 `{"text": "Uncaught", "exception": {"description": "SyntaxError: ..."}}` 造用例，防契约再漂移）。

**P1（自愈候选扩展 + 提示强化）**：
- `Unexpected token '}'` / `Unexpected end of input` 增加定界符平衡修复候选（字符串字面量感知的栈计数；只补/删**尾部**定界符，按序试跑——语法错误无副作用，试错安全，与既有设计哲学一致）；
- 截断提示文案升级为行动指令：'code has unbalanced braces/parens (V8: Unexpected end of input) — wrap in ONE IIFE, avoid nested template literals, or split into several small evaluate calls / pass data via args=JSON'。

**P2（本地语法预检）**：issue 请求项 1 的"本地 parse"若不引第三方解析器，等价手段就是在浏览器里 `new Function(code)` 编译探测——与直接执行报编译错完全等价，无增益。真正的增益在 P0/P1 的"结构化错误 + 确定性修复"，建议不做独立预检层。

**Schema 微调（可选）**：`EvaluateParams.code` 描述已讲清 IIFE 规则；可补一句 'keep code < ~300 chars; longer code loses brace balance'（B 轮经验阈值）。

---

## 2. 现象② read_grid 无分组计数

### 2.1 机制

task_64（数 308 行订单中 billing_name 恰好出现 2 次的客户）的完整挣扎链：

1. `read_grid` 全量拉取 → 12.8KB 存文件（该行为正确）；
2. `read_file` 分 3 块读 + 上下文手工 tally → 计数自相矛盾（Emma Davis 被数成 1，实际 2）、块边界切漏；
3. 四次 evaluate fetch 聚合全败（现象①的 SyntaxError + endpoint 回 HTML）；
4. 最终靠 `read_grid` + `billing_name` 过滤逐候选复核（B 轮 step17/18）——**决定性方法存在且便宜，缺的只是原生聚合**。

叠加因素：块尾 [4000,5000) 字符被 `display_max_chars` 静默吞掉（见现象③），tally 的输入本身就不完整——相邻同名行即使不在块边界也可能落在每块的不可见尾段。

### 2.2 group_count 设计

- `ReadGridParams` 增加 `group_count: str | None`（字段名，如 `billing_name`）；
- `_action_read_grid` 在取回 rows 后（`fields` 自动并集该字段）做 `collections.Counter` 聚合，回显 `group_count: {value: count, ...}`（按 count 降序，Top 50 + "…and K more"）+ `rows_counted`；
- 大于一页的场景按现有 `total_records` 提示翻页（首期不做自动翻全量，回显里写明 counted/total）；
- 计数发生在 Python 侧，不经 LLM——确定性，正是把聚合从上下文 tally 中解救出来的点。

测试：正常聚合、Top-N 截断、字段缺失（报结构化错误）、与 filters/sorting 组合、rows 为空。

---

## 3. 现象③ read_file 预览 5000——比 issue 陈述更深：双截断链 + footer 谎报

### 3.1 代码链

```
read_file 窗口:  actions.py:2247  window = limit if (limit < max_chars) else max_chars   ← limit>5000 被钳回 5000
                 （task_64 step6 实发 limit=6500 → 实得 5000）
footer 谎报:     actions.py:2260  "showing {shown=5000} of {total} chars ... use offset={end} to continue"
LLM 实见:        views.py:48      EXTRACTED: {extracted_content[:display_max_chars=4000]}   ← 静默截断，无省略号
```

后果：**每读一"5000 字符"的块，块尾 1000 字符对 LLM 永远不可见**，而 footer 与 memory 都声称展示了 5000。agent 按块的语义边界（5000）规划 tally，实际可见窗口是 4000——这是 task_64 计数漂移的直接推手之一，且 agent 无从得知（不像 footer 截断有续读指令）。

### 3.2 修复方案

1. **P0：让 `__str__` 截断可见**——`views.py:48` 截断时追加 `[...display truncated at 4000 of {len} chars]`（一行改动，消除静默丢数据）；
2. **P0：footer 与显示上限对齐**——`_window_and_echo` 的窗口/提示统一取 `min(read_file_max_chars, display_max_chars)`，或窗口维持 5000 但 footer 如实报 "LLM-visible 4000"；杜绝两个数字打架；
3. **P1：结构化摘要模式**——JSON 文件（可 `json.loads` 时）提供 `summary=True`：返回顶层键、行数、每字段基数/样例，替代整文件分块读；这同时是现象② group_count 的通用化兜底；
4. 显式 `limit` 允许放宽到硬顶（如 20000）但受 display 上限约束并在 footer 说明——保留上下文保护语义，把"agent 明示要更多"从钳制改为受控放行。

测试：footer/显示一致性的契约用例、空文件/越界 offset（已有）、summary 模式正常+边界（非法 JSON、空数组、超大基数字段）。

---

## 4. 现象④ read_grid 对 legacy 网格无明确提示

### 4.1 机制

task_112 step6：`read_grid namespace=reviewGrid fields=[review_id, sku, ...]` 返回 **OK**（非 error），但行数据不可用，echo 无任何解释。两条回落通道都能造成"成功但无数据"：

- **legacy 通道**（`_LEGACY_GRID_READ_JS`）：Magento legacy 网格（reviewGrid 等）走 `GridJsObject` AJAX，回 HTML 后按 `thead th` 文本做键。请求的 `fields=['review_id','sku',...]` 与 legacy 表头（"ID"、"SKU" 等显示名）**大小写/命名不匹配** → `p.fields.indexOf(key) < 0` 把每个 cell 都 `continue` → 返回 N 个**空对象行**（或 0 行），`rows_returned` 照报，channel 标 `legacy_ajax`，无 note；
- **DOM 表格兜底**：有 note（"current-page visible rows only"）但不说明 fields 不匹配。

agent 无法区分「网格不支持」「用法错误」「真没数据」，只能继续试（issue 指出的行为）。

### 4.2 修复方案

1. legacy/DOM 通道在 `rows_returned>0 但所有行都为空对象`（fields 全被过滤）时，回显 note：`requested fields {fields} not found in headers {heads} — legacy grids use display-name headers; retry without fields or with the listed header names`；
2. legacy 通道 `rows_returned=0` 且页面 `.admin__data-grid` 可见行 >0 时，note 明示 `legacy channel returned 0 rows via AJAX — filters/search not supported on legacy grids; use UI filters or DOM channel`；
3. `read_grid` 的 error 文案（通道全败时）已较明确；补一档「成功但疑似不兼容」的软提示即可，不引入新错误通道。

测试：fields 不匹配→空对象行+note、0 行+页面有行→note、正常 legacy 读取不受影响。

---

## 5. 现象⑤ screenshot 媒体页报错不明——TimeoutError 空消息

### 5.1 机制

task_374：导航到裸 `.jpeg` URL 后三次 screenshot 全部在**恰好 10.00s** 失败，错误消息为**空**：

```
Page.captureScreenshot failed:            ← session.py:2050, %s 渲染 TimeoutError 的 str() = ""
screenshot action failed:                 ← actions.py:1730, 同样空
→ ActionResult(error="Screenshot failed: ")  ← agent 收到的全部信息
```

- `take_screenshot` 用 `asyncio.wait_for(coro, timeout=screenshot_timeout=10.0)` 兜"等不到合成器新帧"的无限挂（session.py:2038-2048 注释：最小化/遮挡窗口等不来帧，P6 screencast 零帧同因）。`asyncio.TimeoutError` 的 `str()` 是空串——于是日志、ActionResult、LLM 三层全部只见冒号不见内容；
- 裸图片文档（image document）无常规渲染循环/无 DOM，`captureScreenshot` 等不到新帧 → 必然超时。task_375 同窗口期交叉验证：同一会话在普通 admin 页 screenshot 成功、在主题预览图 URL 上同样 10s 空消息失败——排除"会话级能力损坏"，坐实"页面类型相关"；
- agent 拿到零信息错误 → 判为可重试故障 → 3 次重试 + 发明 `document.write` 包 HTML（同样失败——被改写的文档仍不产生新帧）+ fetch base64"验证"字节，为一个非目标烧 6 步（issue 陈述属实）。

### 5.2 修复方案

1. **P0：超时异常分层**——`take_screenshot` 里 `except asyncio.TimeoutError` 单独捕获并给满信息消息：`timed out after {t}s waiting for a frame — happens on raw media documents (image/video URLs have no page DOM to screenshot) or when the window is minimized/occluded; navigate to an HTML page wrapping the media, or report/save the media URL directly instead of screenshotting`；其余异常照旧。`_action_screenshot` 的 error 前缀保留；
2. **P1：前置提示**——`navigate` 已能探测 "Empty DOM after navigating"（task_374 日志可见，actions.py 的空 DOM 等待链），在该分支对非 HTML 文档（URL 扩展名/`document.contentType` 以 `image/`、`video/` 开头）在 navigate 回显里直接附一句 `media document — screenshot will not work here`，把止损提前 6 步；
3. 不改超时本身（10s 兜底是 P6 既定防挂策略），只改"失败时的信息量"。

测试：TimeoutError → 专用文案（mock wait_for 抛超时）；普通异常不回归；文案包含 media/minimized 两种成因与出路。

---

## 6. 优先级建议（供排期决策，本轮未动代码）

| 序 | 改动 | 量级 | 预期收益（按 B 轮口径） |
|---|---|---|---|
| 1 | 现象① P0：err_text 匹配源修一行 + 单测桩改真实形状 | ~5 行 + 测试 | 52 次 evaluate 失败中约 20 次直接消失，其余获得引导文案 |
| 2 | 现象⑤ P0：TimeoutError 分层文案 | ~10 行 + 测试 | 消除"空错误"整类止损盲区（媒体页/最小化共用） |
| 3 | 现象③ P0：`__str__` 截断可见 + footer 对齐 | ~10 行 + 测试 | 消除每块 1000 字符静默丢失；tally 类任务输入完整化 |
| 4 | 现象② group_count | 中 | 数数类任务从 20 步降到位数级；Emma Davis 类漏计归零 |
| 5 | 现象④ legacy note | 小 | 免试止损 |
| 6 | 现象① P1：平衡修复候选 + 提示强化 / 现象③ P1：JSON summary 模式 / 现象⑤ P1：navigate 前置提示 | 中 | 在 1–5 之上再压残余 |

另有跨条目观察：单测桩与真实 CDP 契约漂移（现象①的教训）值得在 review 清单里固化为"桩必须取自真实协议回包"。

## 附：证据文件索引

- B 轮日志：`results/self_overestimate/B/task_112.log`（step15-17 SyntaxError 三连、step6 read_grid 无提示）、`task_64.log`（step9-12 evaluate 四连败、step14-16 分块 tally、step17/18 过滤复核）、`task_374.log`（10s 空消息三连 + document.write + base64）、`task_375.log`（同会话成败交叉验证）
- 全量统计：B 轮 46 任务，evaluate WARNING 失败 52 次（编译期 SyntaxError 约 40：EOF 18 / 裸 return 16 / 缺 catch 4 / 多余 token 3；运行期 JSON 解析 6；其他 3）
- CDP 形状探针：`examples/debug_issue185_cdp_shape.py`（2026-09-13 Chrome 152.0.7977.84 实跑：三型编译错误 text 均为 `'Uncaught'`、语义仅在 description）
