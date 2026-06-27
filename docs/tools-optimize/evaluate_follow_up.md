# evaluate 工具优化方案 · 阶段二（对齐 / 超越 browser-use 完整能力）

> 承接 `docs/tools-optimize/evaluate.md`。阶段一（任意 JS 执行 + type-aware 归一化 + JS 预处理 + 异常富化 + memory 分级 + 专用封装 + 分级错误 + 单测）已 100% 落地（锚点见该文「阶段一」）；本文件把 evaluate.md「阶段二（可选，独立）」一节列出的 6 项能力展开为可实施蓝图。
> 同族阶段二先例：`find_elements`（`8c96158`：穿透 shadow/iframe + offset + 几何/visible + first_only + 大结果落盘 + backend_node_id/click-by-id）、`search_page`（`3a95202`：offset 分页 + 大结果落盘 + 同源 iframe/shadow 遍历 + 属性检索）、`extract`（`586cd79`：markdown 提取 + 分块分页 + 去重 + 落盘 + 小模型默认化 + inner timeout）。本方案在「大结果落盘」「inner timeout」「DOM.resolveNode→callFunctionOn 元素句柄链」「attach-to-target 跨源 iframe」四处全面复用三者阶段二的成熟实现。

## 工程约束（实施时务必遵守）

- Windows + PowerShell；包用 `uv`，跑脚本 / 测试用 `uv run python ...`。测试命令 `uv run python -m pytest tests/ -x -v`。
- **缩进按文件**：`actions.py` / `models.py` / `browser/session.py` / `agent/views.py` = **4 空格**；`tests/test_evaluate.py` = **TAB**（对齐 `tests/test_find_elements.py` / `tests/test_search_page.py`）。下文 src 代码片段按 4 空格给出，测试用例按 evaluate.md §1.6 以 prose 描述（避免 TAB 在 markdown 里失真）。
- **registry 不校验 execute 路径**（params 是 raw dict）——见 memory `action-params-no-runtime-validation`。所有新增 `EvaluateParams` 字段的 Pydantic 约束只对 schema(LLM) + 直接构造生效，**handler 必须自己加运行时守卫**。
- `actions.py` 现有 import（`json` `os` `time` `Any` @ `:6-11`）够 二.A–二.E 用；**二.F 需新增 `import re`**（actions.py 顶部无 `re`）。
- 改完跑 `test_evaluate.py` + 全量回归；覆盖率目标 >85%。不主动 commit/push。

## 阶段二分期总览与依赖

| 子期 | 能力（对应 evaluate.md 阶段二项） | 风险 | 依赖 |
|---|---|---|---|
| **二.A** | 大结果落盘（item 6） | 低 | 独立 |
| **二.B** | per-call `await_promise`/`timeout_ms` + `user_gesture`（item 3 + item 4 易半） | 低 | 独立 |
| **二.C** | 结构化参数注入 `args`（item 1，引入 callFunctionOn 主路径） | 中 | 独立（二.D/二.E 依赖它） |
| **二.D** | 元素句柄往返 + selector_map（item 2，旗舰） | 高 | 二.C |
| **二.E** | iframe 执行上下文（item 4 难半） | 中高 | 二.C |
| **二.F** | 图片 / base64 结果通道（item 5，含 `ActionResult.metadata` 扩字段） | 中 | 独立 |

**推荐落地顺序**：A → B → C → D → E → F，每个独立成 PR、独立可回滚、独立通过全量回归。

---

## 二.A 大结果落盘（item 6）—— 低风险，最先做

镜像 `find_elements`（`actions.py:1122-1144`）/ `search_page` / `extract` 的分级落盘：结果 ≥ 阈值时写文件，`extracted_content` 只回「长度 + 路径 + 200 字预览」，`long_term_memory` 带路径。`OSError` 不失败只 warning。

### A.1 `config.py` 新增两个字段（`TruncationSettings`，紧邻 `:58` `find_elements_output_dir` 之后）

```python
    eval_save_threshold: int = 10000      # evaluate result >= this → write to file (mirrors extract/search_page/find_elements)
    eval_output_dir: str = "evaluate_output"  # dir for oversized JS results (env-config)
```

### A.2 `config.py` `load_settings` 新增 env 映射（紧邻 `:255` `search_page_output_dir` 之后，闭合 `)` 之前）

> 注意：兄弟实现里 `find_elements_save_threshold`/`find_elements_output_dir`（`:57-58`）**漏了 env 映射**——本方案给 evaluate 补齐，避免重蹈。

```python
            eval_save_threshold=int(os.environ.get("AGENT_EVAL_SAVE_THRESHOLD", "10000")),
            eval_output_dir=os.environ.get("AGENT_EVAL_OUTPUT_DIR", "evaluate_output"),
```

### A.3 `_action_evaluate` 改造（`actions.py:1653-1668`，4 空格）

before（`:1663-1668`）：
```python
        limit = self._truncation.eval_result_max_chars
        memory = _eval_long_term_memory(text)
        return ActionResult(
            extracted_content=text[:limit],
            long_term_memory=memory[:limit],
        )
```
after：
```python
        tr = self._truncation
        # 大结果分级落盘（镜像 _action_find_elements:1122-1144 / _action_search_page / _action_extract；
        # OSError 不失败只 warning）
        saved_to = None
        if len(text) >= tr.eval_save_threshold:
            try:
                os.makedirs(tr.eval_output_dir, exist_ok=True)
                fpath = os.path.join(tr.eval_output_dir, f"evaluate_{int(time.time() * 1000)}.txt")
                with open(fpath, "w", encoding="utf-8", newline="") as f:
                    f.write(text)
                saved_to = fpath
            except OSError as e:
                logger.warning("evaluate: save to file failed: %s", e)
        limit = tr.eval_result_max_chars
        if saved_to:
            visible = (f"Evaluate result ({len(text)} chars) saved to {saved_to}. "
                       f"Preview: {text[:200]}...").strip()
            memory = f"JavaScript executed successfully, result saved: {saved_to}"
        else:
            visible = text[:limit]
            memory = _eval_long_term_memory(text)
        return ActionResult(extracted_content=visible, long_term_memory=memory)
```
> `os` / `time` 已在 `actions.py:10-11` import，无需新增。

### A.4 测试（`TestEvaluateAction` 追加，TAB）

- `test_oversize_result_spilled_to_file`：monkeypatch `tr.eval_save_threshold=10`、`tr.eval_output_dir=tmp_path`；`browser.evaluate` 返回 300 字符 → 文件落盘；`extracted_content == "Evaluate result (300 chars) saved to <fpath>. Preview: ..."`、`long_term_memory` 以 fpath 结尾。
- `test_small_result_not_spilled`：默认阈值；短结果 → 无文件；`extracted_content == text[:limit]`、走 `_eval_long_term_memory`。
- `test_save_failure_is_soft`：monkeypatch `open` 抛 `OSError` → `logger.warning` 命中、回落到正常回显（非 error）。

---

## 二.B per-call `await_promise` / `timeout_ms` + `user_gesture`（item 3 + item 4 易半）—— 低风险

把阶段一硬编码的 `awaitPromise=True` / `timeout=30000` 暴露为参数，并顺手加 `userGesture`（item 4 里最易、最高频的一块：fullscreen / 部分 clipboard / pointer-lock 需要用户激活）。

### B.1 `EvaluateParams` 加三字段（`models.py:295-303`，`code` 之后，4 空格）

```python
    await_promise: bool = Field(
        default=True,
        description=(
            "Await a returned Promise (default True; needed for await fetch(...)). "
            "Set False for fire-and-forget / strictly synchronous code."
        ),
    )
    timeout_ms: int | None = Field(
        default=None,
        ge=1,
        le=300000,
        description=(
            "Per-call CDP execution timeout in ms, clamped to [1, 300000]. Default None → "
            "30000 (project default). Larger for long fetches, smaller to fail fast."
        ),
    )
    user_gesture: bool = Field(
        default=False,
        description=(
            "Run as a user gesture — required by some APIs (fullscreen, certain clipboard / "
            "pointer-lock calls). No-op for most code."
        ),
    )
```

### B.2 `BrowserSession.evaluate` 扩签名 + 条件传参（`session.py:2618-2653`，4 空格）

after（替换 `:2618` 签名 + `:2638-2647` 的 `Runtime.evaluate` 入参；异常/归一化分支不动）：
```python
    async def evaluate(
        self,
        code: str,
        *,
        await_promise: bool = True,
        timeout_ms: int | None = None,
        user_gesture: bool = False,
    ) -> str:
        """... (docstring 末尾补一句：await_promise/timeout_ms/user_gesture 透传) ..."""
        validated_code = _validate_and_fix_javascript(code)
        result = await self.client.send.Runtime.evaluate(
            {
                "expression": validated_code,
                "returnByValue": True,
                "awaitPromise": await_promise,
                "userGesture": user_gesture,
                "timeout": timeout_ms if timeout_ms is not None else 30000,
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
> `userGesture` 无条件传（默认 False 无副作用）；`timeout` 在 `timeout_ms is None` 时回退 30000，**保持阶段一行为不变**（零回归）。

### B.3 `_action_evaluate` 取参 + 运行时守卫 + 透传（`actions.py:1653-1668`）

`code = params["code"]` 之后插入：
```python
        await_promise = params.get("await_promise", True)
        timeout_ms = params.get("timeout_ms")
        user_gesture = params.get("user_gesture", False)
        # 运行时守卫：registry 不校验 execute 路径（params 是 raw dict）
        if timeout_ms is not None and not (1 <= timeout_ms <= 300000):
            return ActionResult(
                error=f"Evaluate failed: timeout_ms must be in [1, 300000], got {timeout_ms}",
            )
```
`browser.evaluate(code)` 改为：
```python
            text = await browser.evaluate(
                code,
                await_promise=await_promise,
                timeout_ms=timeout_ms,
                user_gesture=user_gesture,
            )
```

### B.4 测试

- **TestEvaluateParams**：`test_await_promise_default_true`；`test_user_gesture_default_false`；`test_timeout_ms_optional`；`test_timeout_ms_rejects_out_of_bounds`（0 与 300001 → `ValidationError`；1 与 300000 通过）。
- **TestEvaluateAction**：`test_runtime_guard_rejects_bad_timeout`（dict `timeout_ms=0` → `ActionResult.error`、`browser.evaluate` 未被 await）；`test_forwards_flags`（dict 各字段 → `browser.evaluate` 以对应 kwargs 被 await）。
- **TestEvaluateSession**：`test_await_promise_false_passed`；`test_user_gesture_true_passed`；`test_custom_timeout_used`（`timeout_ms=5000` → 入参 `timeout=5000`）；`test_default_timeout_30000`（`timeout_ms=None` → 入参 `timeout=30000`）。

---

## 二.C 结构化参数注入 `args`（item 1）—— 中风险，安全核心

**动机**：消除 f-string / JSON.parse 拼源码的注入面——args 经 CDP marshaling（`{"value": ...}`）原生传进 JS。本子期引入 **callFunctionOn 主路径**（二.D/二.E 在其上扩展）。

### 设计决策（与 browser-use / 本项目约定的关系）

- browser-use 的 evaluate 也只吃单串，**args 是超越**（evaluate.md 阶段二原话：「需在 session 层加 callFunctionOn 路径，把用户 JS 包成 `function(...args){ ... }`，args 经 json.dumps 注入」）。
- **call host 选 document**（`this=document`）：本项目**全程用 session/target 隔离，不用 `executionContextId`**（核验：代码库无该用法）；callFunctionOn 需要一个 host objectId，最低风险、最贴合既有 `DOM.resolveNode`+`callFunctionOn` 模式（`_js_click:1973-1986`）的做法是用 `DOM.getDocument`+`DOM.resolveNode` 解析 document——只用 `client.send.*`，无需事件订阅。`this=document` 无害（agent 代码本就能用 `document`）。
- **契约**：当 `args` 给定时，用户 `code` 被 `function(...a){ ... }` 包裹、**必须 `return`**；无 args 时行为不变（走 `Runtime.evaluate`，表达式完成值）。与阶段一 IIFE+return 引导一致。
- **限制**：`callFunctionOn` 无 `timeout` 参数 → args 路径上 `timeout_ms` 被忽略（`await_promise` 仍生效）。诚实标注。

### C.1 `EvaluateParams` 加 `args`（`models.py`，二.B 三字段之后，4 空格）

```python
    args: list[Any] | None = Field(
        default=None,
        description=(
            "Optional JSON arguments injected as a[0], a[1], ... Your code is wrapped as "
            "function(...a){ ... } so it MUST `return` a value. Eliminates string-concat "
            "injection: pass values as JSON, reference them as a[i]. "
            "Example: args=['.btn'] with code `return document.querySelector(a[0]).disabled`."
        ),
    )
```

### C.2 `BrowserSession.evaluate` 扩 `args` 分支（`session.py`，在二.B 改造基础上）

把方法体替换为（签名加 `args: list | None = None`）：
```python
    async def evaluate(
        self,
        code: str,
        *,
        args: list | None = None,
        await_promise: bool = True,
        timeout_ms: int | None = None,
        user_gesture: bool = False,
    ) -> str:
        """... (docstring 补：args 给定时走 callFunctionOn，CDP marshaling，无注入面) ..."""
        validated_code = _validate_and_fix_javascript(code)
        if args:
            sid = self.current_session_id
            # call host = document（this=document）。本项目用 session/target 隔离而非
            # executionContextId；DOM.getDocument + DOM.resolveNode 是既有无事件订阅姿势。
            doc = await self.client.send.DOM.getDocument({"depth": 0}, session_id=sid)
            resolve = await self.client.send.DOM.resolveNode(
                {"nodeId": doc["root"]["nodeId"]},
                session_id=sid,
            )
            host_object_id = resolve["object"]["objectId"]
            function_decl = "function(...a){\n" + validated_code + "\n}"
            result = await self.client.send.Runtime.callFunctionOn(
                {
                    "objectId": host_object_id,
                    "functionDeclaration": function_decl,
                    "arguments": [{"value": a} for a in args],
                    "returnByValue": True,
                    "awaitPromise": await_promise,
                    "userGesture": user_gesture,
                },
                session_id=sid,
            )
        else:
            result = await self.client.send.Runtime.evaluate(
                {
                    "expression": validated_code,
                    "returnByValue": True,
                    "awaitPromise": await_promise,
                    "userGesture": user_gesture,
                    "timeout": timeout_ms if timeout_ms is not None else 30000,
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
> 异常富化（`_format_eval_exception`）与归一化（`_normalize_eval_result`）**对两个 CDP 返回通用**——callFunctionOn 与 Runtime.evaluate 的 result 形状一致，零改动复用。

### C.3 `_action_evaluate` 取 `args` + JSON 序列化守卫 + 透传（`actions.py`）

二.B 守卫块之后插入：
```python
        args = params.get("args")
        if args is not None:
            try:
                json.dumps(args)  # 进 CDP 前先验可序列化
            except (TypeError, ValueError) as e:
                return ActionResult(error=f"Evaluate failed: args not JSON-serializable: {e}")
```
`browser.evaluate(...)` 调用补 `args=args,`。

### C.4 测试

- **TestEvaluateParams**：`test_args_optional`；`test_args_accepts_list_of_json`。
- **TestEvaluateAction**：`test_args_forwarded`（dict `args=[1,2]` → `browser.evaluate(args=[1,2])`）；`test_args_not_serializable_guard`（`args=[object()]` → `ActionResult.error`、`browser.evaluate` 未被 await）。
- **TestEvaluateSession**：
  - `test_args_uses_call_function_on`：`args=[1,'x']` → `DOM.getDocument` + `DOM.resolveNode` + `Runtime.callFunctionOn` 各 1 次、`Runtime.evaluate` **未调用**；`arguments==[{'value':1},{'value':'x'}]`；`functionDeclaration` 以 `"function(...a){"` 开头。
  - `test_no_args_uses_runtime_evaluate`（回归）：`args=None` → 走 Runtime.evaluate。
  - `test_args_exception_details_raises_rich_error`（callFunctionOn 抛 exceptionDetails → `RuntimeError`）。
  - `test_args_normalize_dict`（callFunctionOn 返回 `{value:{'a':1}}` → `'{"a": 1}'`）。

---

## 二.D 元素句柄往返 + selector_map（item 2）—— 高风险，旗舰

让 evaluate 与结构化工具组合：**IN**——把 `click`/`input_text` 操作过的元素（按 `backendNodeId`）作句柄注入 JS；**OUT**——把 JS 返回的 DOM 节点解析回可操作的 index。这是 evaluate 阶段二最高价值、最大改动面的一项，依赖二.C 的 callFunctionOn 主路径。

### 关键事实（核验自代码库）

- `index === backend_node_id`（`serializer.py:722-725`：`highlight_index = node.original_node.backend_node_id`；`_get_element_by_index:335-350` 直接 `selector_map.get(index)`）。所以一个 backendNodeId 本身就是 click/input_text 的 `index`/`element_id`。
- `DOM.resolveNode` 把 backendNodeId → objectId，`Runtime.callFunctionOn` 的 `arguments` 接 `{"objectId": ...}`——这条链 `_js_click:1973-1986`、`fetch_select_options`、`set_select_option` 用了 10+ 次，是成熟基建。
- `selector_map` 由 `get_state` 构建，evaluate 只读不改 → OUT 方向解析出的 index 若不在当前 selector_map，需 `get_state` 刷新后才能 click（诚实限制）。

### D.1 `EvaluateParams` 加两字段（`models.py`，`args` 之后，4 空格）

```python
    elements: list[int] | None = Field(
        default=None,
        description=(
            "Backend node ids (index/element_id from get_state or find_elements(return_node_ids=True)) "
            "of elements to inject as handles e[0], e[1], ... When present, code is wrapped as "
            "function(...a, ...e){ ... } (JSON args first, element handles last) and MUST `return`. "
            "Lets JS act on the exact node click/input_text operate on, without re-querying. "
            "Example: elements=[42] with code `return e[0].value`."
        ),
    )
    return_element_ids: bool = Field(
        default=False,
        description=(
            "If True, a returned DOM node is resolved to its backend node id (usable as "
            "`index`/`element_id` for click/input_text) and reported. Expects the code to "
            "`return` a single element (e.g. `return document.querySelector('form')`). Only the "
            "first returned node is resolved; non-node returns fall back to normal normalization."
        ),
    )
```

### D.2 `BrowserSession.evaluate` 扩 `elements` + `return_element_ids`（`session.py`，在二.C 基础上）

> 思路：`elements` 各经 `DOM.resolveNode` → objectId，作 `{"objectId":...}` 追加在 JSON args 之后（签名 `function(...a, ...e)`）。OUT 方向：当 `return_element_ids` 时整个调用走 `returnByValue=False`；若返回是节点（`type=='object' && subtype=='node'`）则 `DOM.describeNode{objectId}` → backendNodeId，回标记串 `"backendNodeId:<id>"`；否则回落归一化。

替换方法体（签名加 `elements: list[int] | None = None`、`return_element_ids: bool = False`）：
```python
    async def evaluate(
        self,
        code: str,
        *,
        args: list | None = None,
        elements: list[int] | None = None,
        await_promise: bool = True,
        timeout_ms: int | None = None,
        user_gesture: bool = False,
        return_element_ids: bool = False,
    ) -> str:
        """... (docstring 补：elements 经 resolveNode 注入为 e[i]；return_element_ids 把返回节点解析回 backendNodeId) ..."""
        validated_code = _validate_and_fix_javascript(code)
        sid = self.current_session_id
        # IN: 解析元素句柄（DOM.resolveNode，cf _js_click:1974-1977）
        element_oids: list[str] = []
        for bid in (elements or []):
            r = await self.client.send.DOM.resolveNode({"backendNodeId": bid}, session_id=sid)
            element_oids.append(r["object"]["objectId"])
        use_call_fn = bool(args or elements)
        if use_call_fn:
            doc = await self.client.send.DOM.getDocument({"depth": 0}, session_id=sid)
            host = await self.client.send.DOM.resolveNode(
                {"nodeId": doc["root"]["nodeId"]}, session_id=sid,
            )
            # JSON args 在前、元素句柄在后，故签名 function(...a, ...e)
            arguments = [{"value": a} for a in (args or [])] + [{"objectId": o} for o in element_oids]
            func_decl = "function(...a, ...e){\n" + validated_code + "\n}"
            result = await self.client.send.Runtime.callFunctionOn(
                {
                    "objectId": host["object"]["objectId"],
                    "functionDeclaration": func_decl,
                    "arguments": arguments,
                    "returnByValue": not return_element_ids,
                    "awaitPromise": await_promise,
                    "userGesture": user_gesture,
                },
                session_id=sid,
            )
        else:
            result = await self.client.send.Runtime.evaluate(
                {
                    "expression": validated_code,
                    "returnByValue": not return_element_ids,
                    "awaitPromise": await_promise,
                    "userGesture": user_gesture,
                    "timeout": timeout_ms if timeout_ms is not None else 30000,
                },
                session_id=sid,
            )
        if result.get("exceptionDetails"):
            raise RuntimeError(_format_eval_exception(result["exceptionDetails"], validated_code))
        result_data = result.get("result", {})
        if result_data.get("wasThrown"):
            raise RuntimeError("JavaScript execution failed (wasThrown=true)")
        # OUT: 返回的 DOM 节点 → backendNodeId（可作 index/element_id）
        if (return_element_ids
                and result_data.get("type") == "object"
                and result_data.get("subtype") == "node"
                and "objectId" in result_data):
            desc = await self.client.send.DOM.describeNode(
                {"objectId": result_data["objectId"]}, session_id=sid,
            )
            bid = desc.get("node", {}).get("backendNodeId")
            if bid is not None:
                return f"backendNodeId:{bid}"
        return _normalize_eval_result(result_data)
```

### D.3 `_action_evaluate` 取 `elements`/`return_element_ids` + 守卫 + 处理 OUT 标记（`actions.py`）

二.C 守卫块之后插入：
```python
        elements = params.get("elements")
        return_element_ids = params.get("return_element_ids", False)
        if elements is not None and (
            not isinstance(elements, list) or not all(isinstance(i, int) for i in elements)
        ):
            return ActionResult(
                error="Evaluate failed: elements must be a list of ints (backend node ids)",
            )
```
`browser.evaluate(...)` 调用补 `elements=elements, return_element_ids=return_element_ids,`。

拿到 `text` 后、二.A 落盘逻辑**之前**插入 OUT 标记处理（节点 index 不该被当大文本落盘）：
```python
        if text.startswith("backendNodeId:"):
            bid = text.split(":", 1)[1]
            visible = (f"Returned element backend node id: {bid} "
                       f"(usable as index/element_id for click/input_text; "
                       f"if not in current selector_map, call get_state to refresh)")
            return ActionResult(extracted_content=visible,
                                long_term_memory=f"evaluate returned element index {bid}")
```

### D.4 测试

- **TestEvaluateParams**：`test_elements_optional`；`test_elements_list_of_int`；`test_return_element_ids_default_false`。
- **TestEvaluateAction**：`test_elements_guard_rejects_non_int`（`elements=['x']` → error、`browser.evaluate` 未调用）；`test_return_element_marker_surfaces_index`（`browser.evaluate` 返回 `"backendNodeId:42"` → `extracted_content` 含 `"42"` 与 `"index/element_id"`）；`test_elements_forwarded`。
- **TestEvaluateSession**：
  - `test_elements_resolved_to_object_ids`：`elements=[10,20]` → 2× `DOM.resolveNode{backendNodeId}`；callFunctionOn `arguments` 末尾是两个 `{"objectId":...}`；`functionDeclaration` 以 `"function(...a, ...e){"` 开头。
  - `test_args_then_elements_order`：`args=[1]`、`elements=[2]` → `arguments==[{'value':1},{'objectId':...}]`。
  - `test_return_element_id_resolves_node`：`return_element_ids=True`、返回节点 → 入参 `returnByValue=False`；`DOM.describeNode{objectId}` 被调；返回 `"backendNodeId:5"`。
  - `test_return_element_id_non_node_falls_back`：`return_element_ids=True`、返回 `value=42` → `"42"`。
  - `test_return_element_id_uses_runtime_evaluate_when_no_inputs`：`return_element_ids=True`、无 args/elements → 走 `Runtime.evaluate` 且 `returnByValue=False`。

---

## 二.E iframe 执行上下文（item 4 难半）—— 中高风险

`user_gesture` 已在二.B 落地。本子期解决「在（尤其跨源）iframe 内执行」：同源 iframe 无需特殊上下文（agent 代码里 `iframe.contentDocument` 即可），难点是**跨源 iframe**（`contentDocument` 抛 SecurityError）——复用既有 `_build_frame_target_map` + `_attach_to_iframe_target`（`dom.py:537-570`）。

### E.1 `EvaluateParams` 加 `frame`（`models.py`，`return_element_ids` 之后，4 空格）

```python
    frame: int | None = Field(
        default=None,
        description=(
            "Backend node id of an iframe element to execute inside (cross-origin safe). "
            "Default None → top document. When set, the call runs in that iframe's context "
            "(attached via Target.attachToTarget). Use when the parent cannot reach a "
            "cross-origin iframe's document. Same-origin iframes do NOT need this — just "
            "reference `iframe.contentDocument` in your code."
        ),
    )
```

### E.2 `BrowserSession.evaluate` 扩 `frame`（`session.py`，在二.D 基础上）

> 思路：`frame` 给定时，解析 iframe 元素 → `DOM.describeNode` 取 `frameId` → `_build_frame_target_map` 查 targetId → `_attach_to_target` 拿 iframe `sessionId`；其后所有 `DOM.getDocument`/`resolveNode`/`Runtime.*` 调用改用该 `sessionId`，使执行落在 iframe 上下文。

需在 session.py 顶部新增 import：`from tree_walker.browser.dom import _build_frame_target_map, _attach_to_iframe_target`（核验二者为 dom.py 模块级）。

方法体开头（`validated_code = ...` 之后）插入：
```python
        sid = self.current_session_id
        if frame is not None:
            # 跨源 iframe：attach 到其 target 取独立 sessionId（cf dom.py:537-570）
            resolve_ifr = await self.client.send.DOM.resolveNode(
                {"backendNodeId": frame}, session_id=sid,
            )
            desc_ifr = await self.client.send.DOM.describeNode(
                {"objectId": resolve_ifr["object"]["objectId"]}, session_id=sid,
            )
            frame_id = desc_ifr.get("node", {}).get("frameId")
            frame_target_map, _ = await _build_frame_target_map(self.client)
            target_id = frame_target_map.get(frame_id) if frame_id else None
            if not target_id:
                raise RuntimeError(
                    f"Evaluate failed: could not resolve iframe target for frame {frame_id!r}",
                )
            attached = await _attach_to_iframe_target(self.client, target_id)
            if not attached:
                raise RuntimeError("Evaluate failed: could not attach to iframe target")
            sid = attached
```
> 随后把二.D 方法体里所有 `session_id=sid`（注意 D 版本里 `sid` 已是局部变量）以及 `host`/`element_oids` 的解析、`callFunctionOn`/`Runtime.evaluate`/`DOM.describeNode`（OUT）全部用此 `sid`——即 iframe sessionId。`current_session_id` 仅作为 attach 调用的基 session。

### E.3 风险与待验证项（本子期最需真机验证）

- `DOM.describeNode` 返回节点是否带 `frameId`（iframe / contentDocument 节点通常带；需真机确认字段名）。
- attach 生命周期：`_attach_to_iframe_target` 创建的 session 是否需要 `Target.detachFromTarget` 清理（避免 session 泄漏）；若 dom.py 现有调用方未清理，evaluate 应保持一致（先对齐现状，后续统一治理）。
- **建议**：本子期独立 PR，且必须在真实跨源 iframe 页面冒烟通过后再合并（单测只能 mock，覆盖不了 attach 真实语义）。

### E.4 测试

- **TestEvaluateParams**：`test_frame_optional`。
- **TestEvaluateAction**：`test_frame_forwarded`。
- **TestEvaluateSession**（monkeypatch `_build_frame_target_map` / `_attach_to_iframe_target`）：
  - `test_frame_attaches_to_iframe_target`：`frame=99` → `DOM.resolveNode{backendNodeId:99}` + `DOM.describeNode` + `_build_frame_target_map` + `_attach_to_iframe_target`；最终 `Runtime.evaluate`/`callFunctionOn` 的 `session_id` 为 iframe sessionId（≠ 基 sid）。
  - `test_frame_missing_target_is_error`：`frameId` 不在 map → `RuntimeError("...could not resolve iframe target...")`。
  - `test_no_frame_uses_top_session`（回归）：`frame=None` → 用 `current_session_id`，不 attach。

---

## 二.F 图片 / base64 结果通道（item 5）—— 含 `ActionResult.metadata` 扩字段

让 evaluate 能把结果里的 `data:image/...;base64,...` 抽进结构化字段、避免 base64 撑爆上下文（对标 browser-use `service.py:1821-1836`）。本项目 `ActionResult` 当前无该字段（`views.py:8-37`）——本子期给共享类型加 **通用 `metadata` 字段**（非图片专用，便于后续工具复用）。

### F.1 `ActionResult` 加 `metadata`（`agent/views.py:8-37`，4 空格）

在 `judgement` 之后加：
```python
    metadata: dict[str, Any] | None = None
```
> `__str__` **不改**（不 dump metadata，保持 `display_max_chars=500` 的有界显示）。`Any` 已在 `views.py:3` import。该字段为通用容器，其它工具（截图 / 文件）后续可复用。

### F.2 `EvaluateParams` 加 `extract_images`（`models.py`，`frame` 之后，4 空格）

```python
    extract_images: bool = Field(
        default=False,
        description=(
            "If True, scan the result text for `data:image/...;base64,...` URIs, collect them "
            "into ActionResult.metadata['images'], and replace each in the returned text with a "
            "short placeholder ([image 1], [image 2], ...) to avoid bloating context. Default False."
        ),
    )
```

### F.3 新增纯函数 `_extract_data_images`（`actions.py` 模块级，紧邻 `_eval_long_term_memory:194`）

> **`actions.py` 顶部需新增 `import re`**（现状无；见工程约束）。

```python
_DATA_IMAGE_RE = re.compile(r"data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=]+")


def _extract_data_images(text: str) -> tuple[str, list[str]]:
    """Split ``data:image/...;base64,...`` URIs out of ``text``.

    Returns ``(text_with_placeholders, images)``: each URI is replaced by
    ``[image N]`` and ``images`` lists the extracted data URIs in order.
    Mirrors browser-use service.py:1821-1836, adapted to a free-text scan.
    """
    images: list[str] = []

    def _repl(m: re.Match) -> str:
        images.append(m.group(0))
        return f"[image {len(images)}]"

    return _DATA_IMAGE_RE.sub(_repl, text), images
```

### F.4 `_action_evaluate` 取 `extract_images` + 套用（`actions.py`）

二.D 守卫块之后取参：`extract_images = params.get("extract_images", False)`。
拿到 `text` 后（OUT 标记处理之后、二.A 落盘之前）插入：
```python
        metadata = None
        if extract_images:
            text, images = _extract_data_images(text)
            if images:
                metadata = {"images": images}
```
最后所有 `return ActionResult(...)` 补 `metadata=metadata`（二.D OUT 标记那条 early-return 保持 `metadata=None`）。

### F.5 测试

- **TestExtractDataImages**（新纯函数 class，无需 mock）：`test_finds_single_data_uri`；`test_replaces_with_placeholder`（→ `[image 1]`）；`test_multiple_images_numbered`；`test_no_image_unchanged`；`test_preserves_surrounding_text`。
- **TestEvaluateAction**：`test_extract_images_populates_metadata`（结果含 `data:image/png;base64,...` → `metadata["images"]` 非空、`extracted_content` 含 `[image 1]` 且不含原始 base64）；`test_extract_images_default_false`（`extract_images` 缺省 → `metadata is None`、base64 原样留在 `extracted_content`）。

---

## 阶段二文件清单（汇总）

| 文件 | 改动 | 子期 | 缩进 |
|---|---|---|---|
| `src/tree_walker/config.py` | `TruncationSettings` 加 `eval_save_threshold`/`eval_output_dir`；`load_settings` 加两条 env | A | 4 空格 |
| `src/tree_walker/agent/views.py` | `ActionResult` 加 `metadata: dict[str,Any] \| None = None` | F | 4 空格 |
| `src/tree_walker/tools/models.py` | `EvaluateParams` 加 `await_promise`/`timeout_ms`/`user_gesture`/`args`/`elements`/`return_element_ids`/`frame`/`extract_images` | B–F | 4 空格 |
| `src/tree_walker/browser/session.py` | `evaluate` 扩签名 + callFunctionOn 主路径 + elements/return_element_ids/frame 分支；顶部 import `_build_frame_target_map`/`_attach_to_iframe_target` | C–E | 4 空格 |
| `src/tree_walker/tools/actions.py` | 顶部 `import re`（F）；新增 `_extract_data_images`（F）；`_action_evaluate` 取全部新参 + 运行时守卫 + OUT 标记处理 + 落盘 + metadata | A–F | 4 空格 |
| `tests/test_evaluate.py` | 追加各子期用例（约 +25） | A–F | TAB |
| `docs/Tools技术细节/04_动作清单与CDP映射.md` | §4.5 同步阶段二新增参数与 callFunctionOn 路径 | 全 | — |

## 测试计划

```powershell
# 单文件（含阶段二新增）
uv run python -m pytest tests/test_evaluate.py -x -v
# 覆盖率
uv run python -m pytest tests/test_evaluate.py --cov=tree_walker.tools.actions --cov=tree_walker.browser.session --cov-report=term-missing
# 全量回归（execute_js 调用方 + 兄弟工具 + ActionResult 消费方）
uv run python -m pytest tests/ -x -v
```

## 风险与回归点

| 风险 | 影响 | 缓解 |
|---|---|---|
| callFunctionOn 主路径回归无 args 场景 | 改动 evaluate 核心路径 | `args=None` 严格走原 `Runtime.evaluate`（二.C `test_no_args_uses_runtime_evaluate`）；全量回归 |
| `this=document` 改变 agent 代码语义 | 极少数依赖 `this` 的代码 | 仅 args/elements 路径生效；契约写明「包裹为 function、须 return」；描述引导用显式变量 |
| callFunctionOn 无 `timeout` | args/elements 路径无法 per-call 超时 | 文档标注；靠 `await_promise` + action 整体预算兜底；无 args 路径仍支持 timeout_ms |
| OUT 方向解析的 index 不在 selector_map | agent 拿到 index 却 click 不到 | 返回文本显式提示「call get_state to refresh」；selector_map 只读不改 |
| `frame` 的 frameId 字段 / attach 生命周期 | 二.E 真机行为不确定 | 独立 PR + 真实跨源 iframe 冒烟后再合并；单测仅 mock |
| `ActionResult.metadata` 新增影响消费方 | 其它工具/序列化未预期该字段 | `__str__` 不 dump；默认 None；全量回归覆盖 ActionResult 消费方 |
| `import re` 进 actions.py | 极小依赖膨胀 | stdlib，零成本 |
| registry 不校验 execute 路径 | 非法参数绕过 Pydantic | handler 对 `timeout_ms`/`args`/`elements` 各加运行时守卫（见各子期） |

## 验证方法

1. **单测全绿 + 覆盖率 ≥85%**（`test_evaluate.py` + `--cov`）。
2. **全量回归**：`pytest tests/ -x -v`，确认 `execute_js` 调用方 + `find_elements`/`search_page`/`extract`/`click` 等兄弟 + `ActionResult` 消费方无 regression。
3. **落盘冒烟**（二.A）：构造 >10000 字结果 → 文件生成于 `evaluate_output/`、`extracted_content` 为预览、`long_term_memory` 带路径。
4. **args 冒烟**（二.C）：`evaluate({code:"return a[0]+a[1]", args:[1,2]})` → `"3"`（验证 callFunctionOn marshaling）。
5. **元素句柄冒烟**（二.D）：先 `find_elements(..., return_node_ids=True)` 拿 backendNodeId N → `evaluate({code:"return e[0].value", elements:[N]})`；再 `evaluate({code:"return document.querySelector('form')", return_element_ids:true})` → 回 index、后续 `click(index)` 成功。
6. **iframe 冒烟**（二.E）：真实跨源 iframe 页面 → `evaluate({code:"return document.title", frame:<iframe bid>})` 返回 iframe 标题（而非父页）。
7. **图片通道冒烟**（二.F）：返回含 base64 → `metadata["images"]` 命中、`extracted_content` 仅留 `[image 1]`。
8. **回归对照**：与 `find_elements`（`8c96158`）、`search_page`（`3a95202`）、`extract`（`586cd79`）的落盘 / inner-timeout / 元素句柄约定逐条比对一致。

## 验收 checklist（按子阶段，各独立可合并）

- **二.A**：[ ] `eval_save_threshold`/`eval_output_dir`（dataclass + env） [ ] `_action_evaluate` 落盘分支 [ ] 3 用例
- **二.B**：[ ] `await_promise`/`timeout_ms`/`user_gesture` 三参 + 守卫 [ ] session 透传 [ ] ~10 用例
- **二.C**：[ ] `args` 参 + callFunctionOn 主路径（document host） [ ] JSON 守卫 [ ] ~7 用例
- **二.D**：[ ] `elements`/`return_element_ids` [ ] resolveNode→objectId 注入 + OUT describeNode 解析 [ ] OUT 标记处理 [ ] ~8 用例
- **二.E**：[ ] `frame` 参 + attach-to-target [ ] 真机跨源 iframe 冒烟 [ ] ~4 用例
- **二.F**：[ ] `ActionResult.metadata` [ ] `extract_images` + `_extract_data_images` + `import re` [ ] ~7 用例
- **全期**：[ ] 全量 `pytest tests/ -x -v` 无 regression [ ] §4.5 同步 [ ] 缩进按文件（src 4 空格 / tests TAB）
