# evaluate 工具优化方案（分阶段）

> 参照 browser-use（`browser_use/tools/service.py:1759-1867` evaluate 动作体、`:1869-1932` `_validate_and_fix_javascript`、参数模型自动生成）完善本项目 evaluate 工具。
> 相关现状文档：`docs/Tools技术细节/04_动作清单与CDP映射.md` 的 4.5 节；参考标杆：`browser-use/docs/Tools技术细节/06-动作详解-数据处理与文件.md` 的 19. evaluate 节。
> 同族先例：`docs/tools-optimize/find_elements.md`（commit `a34a1f9` / issue #46 / PR #47）、`docs/tools-optimize/search_page.md`（commit `e8e2aed` / issue #44 / PR #45）、`docs/tools-optimize/extract.md`（commit `a8148a4` / issue #42 / PR #43）——本方案在结构、错误分级、封装思路上全面对齐三者阶段一。

---

## 适用场景（什么时候会用到 evaluate）

`evaluate` 在 agent 工具集里的定位是 **「通用 JS 逃生口：一次 `Runtime.evaluate`、把任意 JavaScript 的返回值拿回 agent 上下文」**。它是唯一一个能让 agent 直接跑页面侧 JS 的工具——当 `find_elements` / `search_page` / `click` / `input_text` 等结构化工具都覆盖不到的「长尾需求」出现时，它兜底。它和其它「读 / 找 / 操作」类工具分工明确：

| 工具 | 职责 | 与 evaluate 的区别 |
|---|---|---|
| `find_elements` | 按 CSS 选择器查元素 → tag/text/attributes/children_count + 总数 | 确定性结构查询，零 LLM 成本；evaluate 是任意 JS，无选择器语义 |
| `search_page` | grep 式全文检索 → 上下文 / 元素路径 / 总数 | 基于文字检索；evaluate 可跑任意逻辑（含网络、计算、DOM 读写） |
| `find_text` | 定位文字并滚动 + 高亮 | 只找位置并驱动视口；evaluate 不滚动 / 不高亮 |
| `extract` | LLM 语义提炼页面内容 → 摘要或结构化 JSON | 走 LLM 理解；evaluate 是确定性代码，无理解、零 LLM 成本 |
| `get_state`（每步自动） | 给 agent 看 DOM 快照 | 面向「看 + 按 index 操作」；evaluate 拿任意 JS 返回值 |
| `screenshot` | 截图 | 视觉；evaluate 拿结构化数据 |
| **`evaluate`** | **跑任意浏览器 JS → 返回归一化结果字符串** | **唯一一个「执行任意 JS 并把返回值带回」的工具」** |

典型场景：

1. **取结构化工具拿不到的属性 / 计算值**：`return document.title`、`return document.querySelector('meta[name=csrf-token]')?.content`、`return JSON.stringify(window.__APP_STATE__)`——读 `find_elements` 不暴露的 JS 变量 / 计算属性 / 内联状态。
2. **跑 `fetch` / 异步读数据**：`(async()=>{const r=await fetch('/api/me');return JSON.stringify(await r.json())})()`——拿 XHR/fetch 接口数据（带登录态 cookie），比单独发请求更省事。
3. **补足原生工具不支持的交互**：hover 触发的菜单、拖拽排序、`scrollIntoView` 精确滚动、`dispatchEvent` 触发自定义事件——`click`/`scroll` 覆盖不到的 DOM mutation。
4. **探查 / 调试页面状态**：`return getSelection().toString()`、`return document.visibilityState`、`return localStorage.getItem('x')`——快速诊断当前页面侧状态。

**什么时候不需要它**：只要「按 CSS 选择器查元素 + 取属性 + 总数」就够时用 `find_elements`（确定性、零成本）；只要「按文字检索全文」时用 `search_page`；只要「语义理解 + 结构化抽取」时用 `extract`。`evaluate` 是「上面都不行、但一段 JS 能搞定」时的逃生口——**代价是结果体积自负、且不进 selector_map**（返回 DOM 节点会序列化成 `{}`，无法直接喂给 `click(index)`）。

**可用性提示**：阶段一落地后上述场景 1–4 全部可用（含 `await` 异步、`return` 结构化对象、`fetch`）；把 `click`/`input_text` 操作过的元素（按 index）作为参数传进 JS、或让 JS 返回可操作的元素句柄，属阶段二（需接入 selector_map + `Runtime.callFunctionOn`，browser-use 亦未做）。

---

## Context（为什么做这个改动）

当前 TreeWalker 的 `evaluate` 是一个**极简桩**（`actions.py:1256-1262`，4 空格），明显落后于刚做完阶段一优化的 `find_elements` / `search_page` / `extract` / `screenshot` / `save_as_pdf`，也落后于参照标杆 browser-use：

```python
async def _action_evaluate(self, params: dict, browser: BrowserSession) -> ActionResult:
    code = params["code"]
    try:
        result = await browser.execute_js(code)
        return ActionResult(extracted_content=str(result)[:self._truncation.eval_result_max_chars])
    except Exception as e:
        return ActionResult(error=str(e))
```

底层 `execute_js`（`session.py:2004-2018`，4 空格）也是个共用最小原语：

```python
async def execute_js(self, code: str) -> Any:
    """Execute JavaScript and return the result value."""
    result = await self.client.send.Runtime.evaluate(
        {
            "expression": code,
            "returnByValue": True,
            "awaitPromise": True,
            "timeout": 30000,
        },
        session_id=self.current_session_id,
    )
    if "exceptionDetails" in result:
        err = result["exceptionDetails"]
        raise RuntimeError(f"JS error: {err.get('text', err)}")
    return result.get("result", {}).get("value")
```

主要问题：

1. **返回值 `str(result)` 裸 dump**：`actions.py:1260` 把 Python 对象直接 `str()`——dict 变 `{'a': 1}`（单引号）、bool 变 `True`/`False`、`None` 变 `None`，全是 Python 字面量混入，对 LLM 极不友好（非 JSON、需反向解析）。
2. **`null` 与 `undefined` 塌缩**：`execute_js:2018` `result.get("result", {}).get("value")`——JS 返回 `undefined`（CDP 不带 `value` 键）和返回 `null`（`value: None`）都塌缩成 Python `None`，action 再 `str(None)` = `"None"`，agent 无法区分「没返回」与「显式返回 null」。
3. **异常只取 `text`**：`execute_js:2015-2017` 只 `err.get('text', err)`，丢弃 `exceptionDetails.exception.description`（含完整消息 + 栈）、`exceptionDetails.stackTrace`、`lineNumber`/`columnNumber`、以及 `wasThrown` 兜底标志——多语句 code 报错时定位困难。
4. **无 JS 预处理**：LLM 经常吐出 `\"` 双转义、过度转义的 regex（`\\d`）、`querySelector("a[href=\"x\"]")` 混合引号等会被 V8 直接拒的语法——标杆 browser-use 有 `_validate_and_fix_javascript`（`service.py:1869-1932`）做 6 条 regex 修复，本项目零预处理，这类低级语法错误直接变硬错误。
5. **错误不分级**：`actions.py:1261-1262` `except Exception as e: return ActionResult(error=str(e))`——无 `logger.warning`（失败在日志里隐形）、无工具前缀（错误文本是裸 `"JS error: ..."`，无 `"Evaluate failed: ..."` 前缀）、与 `_action_find_elements:868-874` / `_action_search_page:1275-1280` 的分级约定不一致。
6. **从不设 `long_term_memory`**：与 `find_elements` / `search_page` 的回显约定不一致——evaluate 的结果完全不进跨会话 memory，长结果撑 `extracted_content`、短结果也无摘要。
7. **无 session 层封装**：action 内联直连 `execute_js`（共用原语），而 `find_elements` / `search_page` 已各自有专用 `BrowserSession.<method>`。共用 `execute_js` 还带来一个副作用：**evaluate 的改进（归一化 / 异常富化）没法做进 `execute_js` 而不污染 `extract` / `search_page` / `find_elements` / scroll 等所有调用方**。
8. **截断阈值偏紧且只截 `extracted_content`**：`eval_result_max_chars` 默认 2000（`config.py:48`），相对 browser-use 的 20000 偏紧；且只截 `extracted_content`、不管 `long_term_memory`。
9. **描述对 LLM 无指引**：`ACTION_DEFINITIONS["evaluate"]`（`models.py:282-286`）描述仅为 `"Execute JavaScript code in the browser and return the result"`——不教 LLM 包 IIFE / try-catch、只用浏览器 API（禁 Node.js）、控制输出体积。
10. **零测试**：`tests/` 下无 `test_evaluate.py`（兄弟工具都有：`test_find_elements.py` / `test_search_page.py` / `test_extract.py` 等）。

**参照标杆 browser-use 的做法**（`service.py:1759-1867` 动作体、`:1869-1932` 预处理）：先用 `_validate_and_fix_javascript`（6 条 regex：修 `\"` 双转义、过度转义 regex、`document.evaluate`/`querySelector(All)`/`.closest()`/`.matches()` 混合引号→模板字面量）修常见 LLM 语法坑；单次 `Runtime.evaluate`（`returnByValue=True, awaitPromise=True`）；结果按类型归一化（`value` 缺失→`"undefined"`、dict/list→`json.dumps(ensure_ascii=False)`、primitive→`str()`）；异常从 `exceptionDetails.text` 取 headline + 附 validated code 片段；`wasThrown` 兜底；结果截 20000、memory 门限 10000（短回显 / 长塌缩为长度摘要）；外层 try/except 兜 CDP 失败。

**预期结果**：把 `evaluate` 升级到 browser-use 同级的「任意 JS 执行 + type-aware 结果归一化 + JS 预处理 + 异常富化 + memory 分级」，同时按本项目约定**封装进专用 `BrowserSession.evaluate`（不复用 `execute_js`，零回归）**、**错误分级捕获**、**补齐单测覆盖率 ≥85%**，并同步修正现状文档 §4.5。

---

## 工程约束（实施时务必遵守）

- Windows + PowerShell；包用 `uv`，跑脚本 / 测试用 `uv run python ...`。测试命令 `uv run python -m pytest tests/ -x -v`。
- **缩进按文件**（已复核）：`actions.py` / `models.py` / `browser/session.py` = **4 空格**；`tests/test_evaluate.py` = **TAB**（对齐 `tests/test_find_elements.py` / `tests/test_search_page.py`）。下文代码片段均按目标文件缩进给出。
- 改完跑相关单测 + 全量回归；覆盖率目标 >85%。
- 不主动 `git commit` / `git push`。
- **`re` 需在 `session.py` 顶部新增 import**（现状 `session.py:3-14` 的 import 区无 `re`；`_validate_and_fix_javascript` 依赖它）；其余 `json`（`session.py:7`）/ `logging`（`:8`）/ `Any`（`:14`）在 `session.py` 已是模块级 import；`Field` / `ConfigDict` 已在 `models.py` import（`SearchPageParams:188-204` / `EvaluateParams:183-185` 已用同款）；`logger`（`logging`）已在 `actions.py:19` 存在。新增代码除 `re` 外无需新 import。

---

## 与 browser-use 的关键差异（有意为之，不照搬）

1. **封装进专用 `BrowserSession.evaluate`，不复用 `execute_js`。** browser-use 在 `evaluate` action 内联 `cdp_client.send.Runtime.evaluate`。TreeWalker 按 `find_elements:2171` / `search_page:2135` 的先例把 CDP 调用收进 session 方法——但**与二者不同**：`find_elements`/`search_page` 复用 `execute_js`（只要结构化 dict + `{error}` 契约），而 `evaluate` 做**自己的 `Runtime.evaluate`**，因为它需要完整 `result` dict 做 `null`/`undefined` 区分、type-aware 归一化、`exceptionDetails` 富化、`wasThrown` 兜底——`execute_js` 把这些全丢了。专用方法 = `execute_js` 原样不动，零回归（`extract`/`search_page`/`find_elements`/scroll 不受影响）。
2. **保留 `timeout: 30000`（沿用 `execute_js` 既有约定）。** browser-use 不传 `timeout`（`service.py:1773-1776`）。TreeWalker 对齐 `execute_js:2011` 既有行为，防 LLM 写出死循环 / 长 `fetch` 挂死整个 action。
3. **`bool` → `"true"`/`"false"`、`null` → `"null"`，刻意取 JS 字面量（非 browser-use 的 Python 风格）。** browser-use 对 primitive 一律 `str(value)`——bool 变 `"True"`、null 变 `"None"`，是 Python repr 泄漏。本次优化的核心目的就是告别 `str(result)` 的 Python repr，故 bool/None 取 JS 字面量；dict/list 仍走 `json.dumps`（与 browser-use 一致）；str 不加引号（与 browser-use 一致）。
4. **异常富化适度超越 browser-use：除 `text` 外，附 `exceptionDetails.exception.description`。** browser-use 只取 `exception.get("text", ...)`（`service.py:1781`），丢弃 `exception.description`（含完整错误消息 + 调用栈，通常是调试最有用的字段）。本方案取 `text` 作 headline，若 `description` 存在且 ≠ text 则附上（截断 500），末尾附 `validated_code[:500]` 片段——给 LLM 更足的定位信息。
5. **不移植图片提取（`metadata['images']`）。** browser-use 扫结果文本里的 `data:image/...;base64,...` 抽进 `metadata`（`service.py:1821-1836`）。本项目 `ActionResult`（`agent/views.py:8-37`）**无 `metadata` 字段**，且截图有专用 `screenshot` 工具；evaluate 主用于结构化数据。抽出来无处安放，故不移植。
6. **不移植 `include_extracted_content_only_once` + memory 门限 10000。** 本项目 `ActionResult` 无此字段。改用既有双字段约定：`extracted_content`（截断到 `eval_result_max_chars`）+ `long_term_memory`（`_eval_long_term_memory`：短结果 ≤200 回显、长结果塌缩为 `"JavaScript executed successfully, result length: N characters."`）。memory 回显阈值取 200（browser-use 是 10000）——本项目跨步 memory 更保守，避免大 JSON 反复灌进上下文。
7. **截断阈值用项目既有的 `eval_result_max_chars`（默认 2000），不用 browser-use 硬编码 20000。** 对齐项目上下文节约约定；需更大阈值可调 env `AGENT_TRUNCATE_EVAL_RESULT`（`config.py:48` / `:237`）。不改默认值（避免 scope 蔓延 / 回归）。
8. **错误分级用 `RuntimeError` + action try/except + `logger.warning`，browser-use 在 action 内联查 `exceptionDetails`。** session 方法把 JS 异常 / `wasThrown` 翻译成 `RuntimeError(富化消息)` 上抛；action 层 `logger.warning("evaluate(%r) failed: ...", code[:120], e)` + `ActionResult(error=f"Evaluate failed: {e}")`，对齐 `_action_find_elements:868-874` / `_action_search_page:1275-1280`。evaluate 无「软 miss」（任何不抛异常的 JS 执行都是合法结果），故无 `total==0` 分支——实为两层分流（硬错误 / 命中）。
9. **`EvaluateParams` 保持 `code: str` 唯一字段，阶段一不加 `args`/`awaitPromise`/`returnByValue`/`timeout` 参数。** 完全对齐 browser-use 参数面（自动生成，仅 `code`）。结构化参数注入（`args` + `Runtime.callFunctionOn`）、per-call `await_promise`/`timeout_ms`、元素句柄往返属阶段二。

---

## 阶段一：任意 JS 执行 + type-aware 结果归一化 + JS 预处理 + 异常富化 + memory 分级 + 封装 + 分级错误 + 测试（优先做，风险低）

### 1.1 `EvaluateParams` 保持 `code: str`，精修描述（`models.py:183-185`，4 空格）

阶段一**不加新参数**（对齐 browser-use，差异 §9）。仅精修 `Field` 描述，把 browser-use docstring（`service.py:1760`）里对 LLM 的关键指引落进 schema：

before：
```python
class EvaluateParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(description="JavaScript code to execute in the browser")
```

after：
```python
class EvaluateParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(description=(
        "JavaScript to execute in the page. Best practice: wrap in an IIFE "
        "((function(){try{...}catch(e){return 'Error: '+e.message}})()) so a "
        "thrown error becomes a return value, not a tool failure. Use ONLY "
        "browser APIs (document, window, fetch); NO Node.js APIs. Return a "
        "primitive or a JSON-serializable object/array. Keep output small."
    ))
```
> 描述移植自 browser-use `service.py:1760` 的关键约束（IIFE + try-catch + 仅浏览器 API + 限输出）。`Field` / `ConfigDict` 已 import。不加 `max_length`——JS 长度难以预设合理上界，靠 `eval_result_max_chars` 截输出即可。

### 1.2 `BrowserSession.evaluate(code)` 封装（`session.py`，紧邻 `execute_js:2004` / `search_page:2135`，4 空格）

新增四段：3 个模块级纯函数（预处理 / 归一化 / 异常富化）+ 1 个 session 方法。预处理与归一化直接移植 browser-use `service.py:1869-1932` / `:1807-1819`。

**(a) 模块级 `_validate_and_fix_javascript(code) -> str`**（移植 browser-use `service.py:1869-1932`，6 条 regex；放 `session.py` 模块级，紧邻 `_build_find_elements_js:227` / `_walk_for_file_inputs:250`）：
```python
def _validate_and_fix_javascript(code: str) -> str:
    """Fix common LLM JavaScript quoting/escaping mistakes before evaluation.

    Mirrors browser-use ``_validate_and_fix_javascript`` (service.py:1869-1932).
    Pure regex cleanup; never interpolates user values into JS.
    """
    # 1: undo double-escaped quotes (\" -> "), common when LLM emits JSON-stringified JS
    fixed = re.sub(r'\\"', '"', code)
    # 2: undo over-escaped regex classes (\\d -> \d, \\[ -> \[)
    fixed = re.sub(r'\\\\([dDsSwWbBnrtfv])', r'\\\1', fixed)
    fixed = re.sub(r'\\\\([.*+?^${}()|[\]])', r'\\\1', fixed)
    # 3-6: mixed-quote selectors -> template literals (querySelector / evaluate / closest / matches)
    fixed = re.sub(
        r'document\.evaluate\s*\(\s*"([^"]*)"\s*,',
        lambda m: f'document.evaluate(`{m.group(1)}`,',
        fixed,
    )
    fixed = re.sub(
        r'(querySelector(?:All)?)\s*\(\s*"([^"]*)"\s*\)',
        lambda m: f'{m.group(1)}(`{m.group(2)}`)',
        fixed,
    )
    fixed = re.sub(
        r'\.closest\s*\(\s*"([^"]*)"\s*\)',
        lambda m: f'.closest(`{m.group(1)}`)',
        fixed,
    )
    fixed = re.sub(
        r'\.matches\s*\(\s*"([^"]*)"\s*\)',
        lambda m: f'.matches(`{m.group(1)}`)',
        fixed,
    )
    return fixed
```
> 逐条移植 browser-use `service.py:1875-1917`（差异：browser-use 用具名内部函数 + `re.sub`，这里用 lambda 等价表达，逻辑一致；browser-use 末尾的 `changes_made` 调试日志省略）。需 `import re`（工程约束已注明）。getAttribute 不修（browser-use `:1919-1920` 同款决定，属性名罕有混合引号）。

**(b) 模块级 `_normalize_eval_result(result_data: dict) -> str`**（移植 browser-use `service.py:1807-1819`，bool/None 取 JS 字面量——差异 §3）：
```python
def _normalize_eval_result(result_data: dict) -> str:
    """Normalize a Runtime.evaluate result value to an LLM-friendly string.

    Mirrors browser-use (service.py:1807-1819) with one fix: bool/null are
    rendered as JS literals (``true``/``false``/``null``), not Python
    ``True``/``None``, so output never carries Python repr semantics.
    """
    if "value" not in result_data:
        # CDP omits `value` when the expression returned `undefined`.
        return "undefined"
    value = result_data["value"]
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, bool):  # must precede int: bool is a subclass of int
        return "true" if value else "false"
    if value is None:
        return "null"
    # int / float / str
    return str(value)
```
> dict/list 走 `json.dumps(ensure_ascii=False)`（告别 `str({'a':1})` 单引号 repr，本次最大正确性收益）；`bool` 判断**必须在 `int` 之前**（Python `bool` 是 `int` 子类，`isinstance(True,int)` 为真）；str 走 `str()` 不加引号（`hello` 而非 `"hello"`，对齐 browser-use）。

**(c) 模块级 `_format_eval_exception(exception: dict, validated_code: str) -> str`**（移植 browser-use `service.py:1784-1792` + 适度超越——差异 §4）：
```python
def _format_eval_exception(exception: dict, validated_code: str) -> str:
    """Build a debugging-rich error message from CDP exceptionDetails.

    Mirrors browser-use (service.py:1784-1792) and goes one step further:
    also surface ``exception.description`` (full message + stack), which
    browser-use discards. Truncated to keep the message bounded.
    """
    text = exception.get("text", "Unknown error")
    parts = [f"JavaScript execution error: {text}"]
    description = exception.get("exception", {}).get("description")
    if description and description != text:
        parts.append(str(description)[:500])
    snippet = validated_code[:500] + ("..." if len(validated_code) > 500 else "")
    parts.append(f"Validated code (after quote fixing):\n{snippet}")
    return "\n".join(parts)
```
> `exception.get("exception", {}).get("description")` 防 `exception` 键缺失；description 截 500 防超长栈撑爆错误文本。

**(d) session 方法 `evaluate`**（紧邻 `execute_js:2004` / `search_page:2135`，4 空格）：
```python
async def evaluate(self, code: str) -> str:
    """Execute arbitrary user JavaScript and return a normalized result string.

    Mirrors browser-use ``evaluate`` (service.py:1759-1867): preprocess the
    code (fix common LLM quoting mistakes), run a single ``Runtime.evaluate``
    with ``returnByValue=True, awaitPromise=True`` (+ ``timeout`` per project
    convention), then normalize the value to an LLM-friendly string.

    Raises ``RuntimeError`` (with a debugging-rich message) on a JS exception
    (``exceptionDetails``) or the legacy ``wasThrown`` flag — the action layer
    maps this to a hard ``ActionResult(error=...)``.

    Limitations (same as browser-use): top-document execution context; a
    returned DOM node serializes to ``{}`` (no element-handle round-trip —
    that needs ``Runtime.callFunctionOn`` + selector_map, see 阶段二).
    """
    validated_code = _validate_and_fix_javascript(code)
    result = await self.client.send.Runtime.evaluate(
        {
            "expression": validated_code,
            "returnByValue": True,
            "awaitPromise": True,
            "timeout": 30000,
        },
        session_id=self.current_session_id,
    )
    if result.get("exceptionDetails"):
        raise RuntimeError(_format_eval_exception(result["exceptionDetails"], validated_code))
    result_data = result.get("result", {})
    if result_data.get("wasThrown"):
        raise RuntimeError("JavaScript execution failed (wasThrown=true)")
    return _normalize_eval_result(result_data)
```
> **不复用 `execute_js`**（差异 §1）：直连 `Runtime.evaluate` 拿完整 `result` dict。`returnByValue=True` + `awaitPromise=True` 与 `execute_js` 一致（awaitPromise 对非 Promise 是 no-op，可无条件开）；`timeout: 30000` 沿用既有约定（差异 §2）。异常路径走 `_format_eval_exception` 富化（差异 §4）；`wasThrown` 兜底（browser-use `:1798-1801`）；成功路径走 `_normalize_eval_result`。

### 1.3 `_eval_long_term_memory` helper（`actions.py` 模块级，紧邻 `_format_search_results:64`，4 空格）

移植 browser-use `service.py:1847-1853` 的 memory 门限精神，阈值改保守（差异 §6）：
```python
_EVAL_MEMORY_ECHO_MAX = 200


def _eval_long_term_memory(text: str) -> str:
    """Build a compact long_term_memory for an evaluate result.

    Short results are echoed verbatim (so ``return document.title`` is
    remembered across steps); long results collapse to a length-only summary
    to avoid bloating cross-step memory. Mirrors browser-use
    service.py:1847-1853 (their threshold is 10000; ours is tighter).
    """
    if len(text) <= _EVAL_MEMORY_ECHO_MAX:
        return text
    return f"JavaScript executed successfully, result length: {len(text)} characters."
```
> 阈值 200：跨步 `long_term_memory` 反复重带，大 JSON（如 `window.__APP_STATE__`）不该逐步灌进上下文；短结果（标题 / 属性值 / 计数）回显有用。对齐 browser-use「短回显 / 长塌缩」精神。

### 1.4 `_action_evaluate` 重写（`actions.py:1256-1262`，4 空格）

before：见上文 Context 节引文。

after：
```python
async def _action_evaluate(self, params: dict, browser: BrowserSession) -> ActionResult:
    code = params["code"]
    try:
        text = await browser.evaluate(code)
    except Exception as e:
        # Hard error: JS exception / wasThrown / CDP failure — surface an
        # evaluate-specific error rather than the generic Tools.execute
        # fallback. Aligns with _action_find_elements / _action_search_page.
        logger.warning("evaluate(%r) failed: %s", code[:120], e)
        return ActionResult(error=f"Evaluate failed: {e}")
    limit = self._truncation.eval_result_max_chars
    memory = _eval_long_term_memory(text)
    return ActionResult(
        extracted_content=text[:limit],
        long_term_memory=memory[:limit],
    )
```
> 两层分流（对齐 `_action_find_elements:868-874`，但无软 miss——evaluate 任何不抛异常的执行都是合法结果）：硬错误（`RuntimeError`）→ `ActionResult(error="Evaluate failed: ...")` + `logger.warning`；命中 → 归一化文本截断进 `extracted_content`、`_eval_long_term_memory` 摘要进 `long_term_memory`。不再 `str(result)` 裸 dump、不再裸 `str(e)`。

### 1.5 `ACTION_DEFINITIONS["evaluate"]` 描述更新（`models.py:282-286`，4 空格）

before：
```python
"evaluate": (
    EvaluateParams,
    "Execute JavaScript code in the browser and return the result",
    True,
),
```

after：
```python
"evaluate": (
    EvaluateParams,
    "Execute arbitrary JavaScript in the page and return the result. Wrap in "
    "an IIFE with try-catch so errors become return values; use only browser "
    "APIs (no Node.js). Supports async (await / fetch). Result is normalized "
    "to a string (objects -> JSON). Escape hatch when find_elements / "
    "search_page / click cannot express the need.",
    True,
),
```
> 描述对齐 browser-use docstring（`service.py:1760`）精神，让 LLM 知道：包 IIFE + try-catch、仅浏览器 API、支持 async、结果是字符串、是「其它工具搞不定时」的逃生口。`terminates_sequence=True` 不变（任意 JS 可能改变页面状态）。

### 1.6 新增 `tests/test_evaluate.py`（TAB 缩进，对齐 `tests/test_find_elements.py` / `tests/test_search_page.py`）

测试分四层（动作层 / 参数层 / 会话层 / 预处理+归一化纯函数层），覆盖正常路径 + 关键边界：

**TestEvaluateAction**（mock `BrowserSession`，驱动 `Tools().execute("evaluate", {...}, browser)`，断言返回 `ActionResult`；`terminates_sequence` 工具断言 `browser.get_state.assert_not_awaited()`）：
- `test_primitive_result_echoed`：`browser.evaluate` 返回 `"hello"` → `extracted_content == "hello"`、`long_term_memory == "hello"`（短结果回显）。
- `test_long_result_truncated_and_summarized`：返回 3000 字符串 → `extracted_content` 截到 `eval_result_max_chars`、`long_term_memory == "JavaScript executed successfully, result length: 3000 characters."`。
- `test_js_exception_is_hard_error`：`browser.evaluate` 抛 `RuntimeError("JavaScript execution error: ReferenceError: foo is not defined\n...")` → `error == "Evaluate failed: ..."`、`is_done is False`、`logger.warning` 命中。
- `test_does_not_call_get_state`：断言 `browser.get_state.assert_not_awaited()`。
- `test_forwards_code_verbatim`：传 `code="return 1+1"` → 以该串调 `browser.evaluate`（预处理在 session 层，action 透传原 code）。

**TestEvaluateParams**（Pydantic 校验）：
- `test_code_required`：缺 `code` → `ValidationError`。
- `test_forbids_extra_fields`：未知字段被 `extra="forbid"` 拒。
- `test_accepts_arbitrary_code`：超长 / 含特殊字符的 `code` 通过（无 `max_length`）。

**TestEvaluateSession**（直接驱动 `BrowserSession.evaluate`，mock `client.send.Runtime.evaluate`）：
- `test_runs_one_runtime_evaluate_with_flags`：入参含 `returnByValue=True` / `awaitPromise=True` / `timeout: 30000` / `expression`；调一次。
- `test_preprocessing_applied`：`code` 含 `\"` → `expression` 里已是 `"`（验证 `_validate_and_fix_javascript` 在 session 层生效）。
- `test_normalize_dict_to_json`：`result.result.value={"a":1}` → 返回 `'{"a": 1}'`（JSON 双引号，非 `str()` 的 `{'a': 1}` 单引号 repr）。
- `test_normalize_undefined_vs_null`：`value` 缺失 → `"undefined"`；`value=None` → `"null"`；`value=True` → `"true"`；`value=False` → `"false"`。
- `test_normalize_primitive`：`value=42` → `"42"`；`value="hi"` → `"hi"`（无引号）。
- `test_exception_details_raises_rich_error`：`result.exceptionDetails={text:"ReferenceError: x is not defined", exception:{description:"ReferenceError: x is not defined\n    at ..."}}` → 抛 `RuntimeError`，消息含 `text`、`description`、`Validated code`。
- `test_was_thrown_raises`：`result.result.wasThrown=True` → 抛 `RuntimeError("JavaScript execution failed (wasThrown=true)")`。

**TestEvaluatePreprocessorAndNormalizer**（纯函数，无需 mock）：
- `test_fix_double_escaped_quotes`：`_validate_and_fix_javascript('a\\"b')` → `'a"b'`。
- `test_fix_over_escaped_regex`：含 `\\d` / `\\[` → 还原为 `\d` / `\[`。
- `test_fix_queryselector_mixed_quotes`：`querySelector("a[href=\"x\"]")` → ``querySelector(`a[href="x"]`)``。
- `test_fix_document_evaluate_quotes`：同上 for `document.evaluate("...", ...)`。
- `test_fix_closest_and_matches_quotes`：同上 for `.closest(...)` / `.matches(...)`。
- `test_idempotent_on_clean_code`：无上述模式的 code 原样返回。
- `test_normalize_*`：复用会话层归一化断言（纯函数直调 `_normalize_eval_result`）。

> 对齐 `tests/test_search_page.py` / `tests/test_find_elements.py` 的 `_make_browser` mock 工厂与四层 class 结构；异步测试逐个标 `@pytest.mark.asyncio`（项目无全局 `asyncio_mode`）。

### 1.7 阶段一文件清单

| 文件 | 改动 | 锚点 |
|---|---|---|
| `src/tree_walker/tools/models.py` | `EvaluateParams.code` 描述精修；`ACTION_DEFINITIONS["evaluate"]` 描述更新 | `:183-185` / `:282-286`，**4 空格** |
| `src/tree_walker/browser/session.py` | 顶部加 `import re`；新增 `_validate_and_fix_javascript` + `_normalize_eval_result` + `_format_eval_exception` + `BrowserSession.evaluate` | 模块级 helper 紧邻 `_build_find_elements_js:227` / `_walk_for_file_inputs:250`；方法紧邻 `execute_js:2004` / `search_page:2135`，**4 空格** |
| `src/tree_walker/tools/actions.py` | 新增 `_eval_long_term_memory` + `_EVAL_MEMORY_ECHO_MAX`；重写 `_action_evaluate` | helper 紧邻 `_format_search_results:64`；action `:1256-1262`，**4 空格** |
| `tests/test_evaluate.py` | 新建（4 个 class，~20 用例） | **TAB** 缩进，对齐 `tests/test_find_elements.py` |
| `docs/Tools技术细节/04_动作清单与CDP映射.md` | §4.5 同步：预处理、type-aware 归一化、异常富化、memory 分级、专用 `BrowserSession.evaluate` 封装、行号修正（`actions.py:1256`） | §4.5 |

### 1.8 阶段一测试计划

```powershell
# 单文件
uv run python -m pytest tests/test_evaluate.py -x -v
# 覆盖率
uv run python -m pytest tests/test_evaluate.py --cov=tree_walker.tools.actions --cov=tree_walker.browser.session --cov-report=term-missing
# 全量回归（确保 extract / search_page / find_elements 等兄弟工具 + execute_js 调用方无 regression）
uv run python -m pytest tests/ -x -v
```

---

## 阶段二（可选，独立，对齐 / 超越 browser-use 完整能力）

- **结构化参数注入 `args` + `Runtime.callFunctionOn`**：让 agent 传 JSON 参数进 JS（而非 f-string 拼源码），消除注入面。需在 session 层加 `callFunctionOn` 路径（把用户 JS 包成 `function(...args){ ... }`，args 经 `json.dumps` 注入）。browser-use evaluate 未做（它的 evaluate 也只吃单串），属超越。
- **元素句柄往返 + 接入 `selector_map`**：让 JS 能接收 `click`/`input_text` 操作过的元素（按 index / `backendNodeId` → `DOM.resolveNode` → `objectId` → `callFunctionOn` 的 `arguments`），并能把返回的 DOM 节点映射回可操作的 index——使 evaluate 能与结构化工具组合。改动面大（需扩 `EvaluateParams` + session 双向桥接），独立成期。
- **per-call `await_promise` / `timeout_ms`**：阶段一 `awaitPromise`/`timeout` 硬编码；阶段二暴露为参数（带边界校验），支持 fire-and-forget 与长脚本。
- **`userGesture` / iframe 执行上下文**：部分 API（fullscreen、某些 clipboard）需用户激活 → `userGesture: True`；iframe 内执行需选 `executionContextId`。browser-use 未做。
- **图片 / base64 结果通道**：若需让 evaluate 回传截图数据，得先扩 `ActionResult`（加 `metadata` 或专用图片字段）——当前无消费者（差异 §5），阶段二按需。
- **大结果落盘**：命中超长结果（如整页 `outerHTML`）时写文件、`extracted_content` 只回摘要（对齐 `TruncationSettings` 思路，需轻量文件输出约定）。

---

## 风险与回归点

| 风险 | 影响 | 缓解 |
|---|---|---|
| 预处理误改合法 JS | 极端情况下把用户有意写的 `\"` / 模板字面量改坏 | 6 条 regex 都是 browser-use `:1869-1932` 实战验证的高置信模式，仅修典型 LLM 坑；`test_idempotent_on_clean_code` + 各 fix 用例覆盖；万一改坏，错误消息里的 `validated_code` 片段可追溯 |
| `bool`/`null` 归一化改变输出格式 | 旧消费者若依赖 `"True"`/`"None"` 会断 | `extracted_content` 本就是给 LLM 读的自由文本，无程序化消费者；差异 §3 显式标注；测试 `test_normalize_undefined_vs_null` 覆盖 |
| 改 `execute_js` 影响其它调用方 | `extract`/`search_page`/`find_elements`/scroll 行为变 | **`execute_js` 原样不动**（差异 §1），evaluate 走专用 `BrowserSession.evaluate`；全量回归覆盖 |
| `eval_result_max_chars` 默认 2000 偏紧 | 大 JSON 结果被截 | 阈值可调（env `AGENT_TRUNCATE_EVAL_RESULT`）；不改默认避免回归；`long_term_memory` 给长度摘要兜底 |
| 返回 DOM 节点序列化为 `{}` | agent 拿不到可用元素 | 诚实标注限制（差异 §1 / session docstring）；阶段二元素句柄往返解决；引导用 `find_elements` 取结构化字段 |
| 用户 JS 注入 / 恶意代码 | evaluate 本就是任意 JS 逃生口 | 设计如此（`terminates_sequence=True`）；预处理只修语法不消毒语义；安全边界由 agent 调用方负责 |
| `import re` 新增 | 极小依赖膨胀 | `re` 是 stdlib，零成本；放在 `session.py` 既有 import 区 |
| 异常消息变长（含 description + 代码片段） | `ActionResult.error` 体积增大 | description 截 500、code 片段截 500；`ActionResult.__str__` 还有 `display_max_chars=500` 兜底 |

---

## 验证方法

1. **单测全绿 + 覆盖率 ≥85%**：`uv run python -m pytest tests/test_evaluate.py -x -v` + `--cov` 查看 `actions` / `session` 覆盖率。
2. **全量回归**：`uv run python -m pytest tests/ -x -v`，确认 `test_extract.py` / `test_search_page.py` / `test_find_elements.py` 等兄弟工具 + `execute_js` 调用方无 regression。
3. **会话层冒烟**（mock CDP 即可，或接真实浏览器）：
   ```python
   # uv run python -c "..."
   text = await browser.evaluate("(function(){try{return JSON.stringify({a:1,b:[1,2]})}catch(e){return 'Error: '+e.message}})()")
   assert text == '{"a": 1, "b": [1, 2]}'   # JSON 双引号，非 Python repr
   ```
4. **动作层冒烟**：
   ```python
   r = await Tools().execute("evaluate", {"code": "return document.title"}, browser)
   assert r.error is None and r.extracted_content
   ```
5. **预处理冒烟**：用含 `\"` / `querySelector("a[href=\"x\"]")` / `\\d` 的 code 跑 `_validate_and_fix_javascript`，确认生成合法 JS。
6. **异常富化冒烟**：`evaluate("throw new Error('boom')")` → `error` 含 `"Evaluate failed: ..."` + `boom` + `Validated code`。
7. **回归对照**：与 `find_elements`（commit `a34a1f9`）、`search_page`（`e8e2aed`）、`extract`（`a8148a4`）的错误分级 / `long_term_memory` / `logger.warning` 约定逐条比对一致。

---

## 验收 checklist（阶段一）

- [ ] `EvaluateParams.code` 描述精修（`models.py`）
- [ ] `ACTION_DEFINITIONS["evaluate"]` 描述更新（`models.py`）
- [ ] `session.py` 顶部加 `import re`
- [ ] `_validate_and_fix_javascript` + `_normalize_eval_result` + `_format_eval_exception` + `BrowserSession.evaluate` 落地（`session.py`）
- [ ] `_eval_long_term_memory` + `_EVAL_MEMORY_ECHO_MAX` helper 落地（`actions.py`）
- [ ] `_action_evaluate` 重写为两层分流（硬错误 / 命中）+ `logger.warning` + 双字段回显（`actions.py`）
- [ ] `tests/test_evaluate.py` 新建，4 class ~20 用例全绿，覆盖率 ≥85%
- [ ] 全量 `pytest tests/ -x -v` 无 regression
- [ ] `docs/Tools技术细节/04_动作清单与CDP映射.md` §4.5 同步（预处理、归一化、异常富化、memory 分级、专用封装、行号修正）
- [ ] 缩进按文件：src = 4 空格、tests = TAB
