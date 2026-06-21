# extract 工具优化方案（分阶段）

> 参照 browser-use（`browser_use/tools/service.py:1020-1256` extract、`browser_use/tools/views.py:8-27` ExtractAction、`browser_use/tools/extraction/schema_utils.py` schema→Pydantic、`browser_use/agent/service.py:249-250,362-364` 接线）完善本项目 extract 工具。
> 相关现状文档：`docs/Tools技术细节/04_动作清单与CDP映射.md` 的 4.6 节；参考标杆：`browser-use/docs/Tools技术细节/06-动作详解-数据处理与文件.md` 的 16. extract 节。
> 同族先例：`docs/tools-optimize/screenshot.md`（screenshot 阶段一已落地，commit `b1fc97e` / issue #38 / PR #39）、`docs/tools-optimize/save_as_pdf.md`（save_as_pdf 阶段一已落地，commit `21153d8` / issue #40 / PR #41）——本方案在结构、错误处理、封装思路上全面对齐二者阶段一。

---

## 适用场景（什么时候会用到 extract）

`extract` 在 agent 工具集里的定位是**「用一次额外的（通常是低成本小模型的）LLM 调用，从当前页面文本中按需提炼信息，必要时直接产出结构化 JSON」**。它和其它「读」类工具分工明确：

| 工具 | 职责 | 与 extract 的区别 |
|---|---|---|
| `get_state`（每步自动） | 给 agent 看 DOM 快照，用于「看页面、决定点哪」 | 面向主模型「看 + 操作」，非为提炼优化 |
| `find_text` / `search_page` | 定位某段文字（为了滚动 / 高亮 / 点击） | 只找位置，不读懂数据 |
| `evaluate` | 跑 JS 拿原始返回值 | 拿原始数据，无语义提炼 |
| `read_file` | 读本地文件 | 管磁盘，不管页面 |
| **`extract`** | **语义提炼页面内容 → 自然语言摘要 或 结构化 JSON** | 唯一一个「读完 + 理解 + 结构化」的工具 |

典型场景：

1. **结构化数据抓取（schema 模式主战场）**：把商品列表页的「名称/价格/链接」抓成 JSON。定义 `extraction_schema = {type:object, properties:{title, price, url}}`，extract 直接吐 JSON——主 agent 不必把整页 DOM 塞进上下文再手工誊抄（省 token、不易错），抓完还能接 `write_file` 存盘或喂给下一步。
2. **从长页面 / 详情页挖关键信息**：「提取这篇文档的发布日期、作者、核心论点」。长页面全文丢给主模型很贵且易超上下文；extract 用一次聚焦调用（配合 `extract_page_max_chars` 截断）只回答案。文案页、合同页、规格表、新闻详情都适用。
3. **针对性问答式摘录**：「这页里关于退货政策的部分说了什么？」。`goal` 当提问、extract 当「读完回答」，比整页塞进主上下文再复述更干净。
4. **跨页 / 分页批量采集（依赖阶段二）**：抓搜索结果前 N 页全部条目。browser-use 的 `start_from_char` 分页 + `already_collected` 去重就是为这个设计的：抓一页 → 翻页 → 抓下一页、自动去重。这是 extract 的「重型」场景，阶段二 markdown 分块器落地后才完整。
5. **跨站信息搬运的前置步骤**：从 A 页 extract 出联系方式 / 订单号，再 `input_text` 填到 B 页——结构化输出正好当「数据载体」在工具间传递。

**什么时候不需要它**：纯导航 / 点击 / 表单任务，agent 看 `get_state` 就够；页面很短时主模型直接读 DOM 也行。extract 是为「长内容 + 要结构化 + 要省主上下文」这三件事存在的。

**可用性提示**：场景 1/2/3（结构化 + free-text 提炼）在**阶段一**落地后可用；场景 4（分页批量）要等**阶段二**的 markdown 提取 + 分块 + 去重。当前 master 上 extract 仍是未接通的桩（详见下文 Context），这些场景此刻都跑不起来。

---

## Context（为什么做这个改动）

当前 TreeWalker 的 `extract` 是一个**未接通的桩**，明显落后于刚做完阶段一优化的 `screenshot` / `save_as_pdf`，也落后于参照标杆 browser-use：

1. **LLM 路径死代码（断路止血）**：`_action_extract`（`actions.py:633-647`，4 空格）读 `getattr(self, "_extract_llm", None)`，但全仓库**无任何 `_extract_llm =` 赋值**——`Agent.__init__`（`agent.py:57`）构造 `Tools(truncation=..., allowed_upload_paths=...)` 时从不注入。结果每次运行都走 fallback 分支：返回 `page_text[:extract_fallback_max_chars]`（默认 2000 字符），**完全丢弃 `goal`**，用户意图对输出零影响。`LLMClient.extract`（`client.py:295-311`）从未被调用。
2. **死 import + 误导注释**：函数体 `from tree_walker.llm.client import LLMClient` 导入后未使用；注释 `# Simple extraction: return first 2000 chars with goal context` 与实际 fallback 行为（无 goal 上下文的裸截断）不符。
3. **`LLMClient.extract` 太弱**（`client.py:295-311`，4 空格）：单条 user message `f"{prompt}\n\n---\n{content}"`，无 system prompt、无结构化输出、无 schema 入参、`max_tokens=2048` 写死、返回纯 `str`。
4. **零结构化输出**：`ExtractParams`（`models.py:54-56`）只有 `goal: str`；无 schema 字段、无 JSON 输出能力。browser-use 的 `ExtractAction`（`views.py:8-27`）有 `query / extract_links / extract_images / start_from_char / output_schema / already_collected`，且 output_schema 经 schema→Pydantic + `output_format` 强约束产出 JSON。
5. **错误处理不分级**：`execute_js` 失败被静默吞掉（`actions.py:637 except Exception: page_text = ""` 无日志）；LLM 异常不被捕获，冒泡到 `Tools.execute` 通用 catch（`actions.py:181-183`）。兄弟 `_action_screenshot`（`actions.py:823-852`）是分级捕获，extract 没有。
6. **零测试**：`tests/` 下无 `test_extract.py`。

**参照标杆 browser-use 的做法**（`browser_use/tools/service.py:1020-1256`、参数模型 `views.py:8-27`、接线 `agent/service.py:249-250,362-364`）：`page_extraction_llm` 默认复用主 `llm`；`extraction_schema` 由 Agent 注入（对 LLM 隐藏）；JSON Schema → Pydantic → `output_format` 强约束；schema 非法则 try/except 降级 free-text；调用包 120s 超时；结果 ≥10000 字符落盘 + `include_extracted_content_only_once`。

**预期结果**：让 `extract` 的 LLM 路径真正接通（`_extract_llm` 默认复用主 llm）、`goal` 真正进 prompt、按 browser-use 模式经 Agent 注入 `extraction_schema` 实现结构化输出（Anthropic tool-use 强约束）、错误分级捕获、补齐单测覆盖率 ≥85%。

---

## 工程约束（实施时务必遵守）

- Windows + PowerShell；包用 `uv`，跑脚本/测试用 `uv run python ...`。测试命令 `uv run python -m pytest tests/ -x -v`。
- **缩进按文件**（已复核）：`actions.py` / `models.py` / `agent.py` / `config.py` / `llm/client.py` = **4 空格**；`tests/test_extract.py` = **TAB**（对齐 `tests/test_screenshot.py` / `tests/test_save_as_pdf.py`）。下文代码片段均按目标文件缩进给出。
- 改完跑相关单测 + 全量回归；覆盖率目标 >85%。
- 不主动 `git commit` / `git push`。
- `json` / `logging` / `Any` 已是 `client.py` 模块级 import（`client.py:5-8`）；`RateLimitError` / `APIError` 已 import（`client.py:10`）；`LLMClient` / `LLMSettings` / `AgentSettings` 已在 `agent.py:19` import；新增 `extract_llm: LLMSettings | None` 复用现有 `LLMSettings`（`config.py:91-97`），无需新 import。

---

## 与 browser-use 的关键差异（有意为之，不照搬）

1. **结构化输出机制 = Anthropic tool-use 强约束，不移植 provider-agnostic `output_format`。** browser-use 用 `BaseChatModel.ainvoke(..., output_format=PydanticModel)` 抽象（多 provider）。TreeWalker 是 **Anthropic SDK 专属**（`client.py:10`），复用 `get_action`（`client.py:180-204`）已确立的 tool-use 模式：定义工具 `extract_result`、`input_schema=output_schema`、`tool_choice={"type":"tool","name":"extract_result"}`，从 `tool_use` block 的 `.input` 取 JSON。**无需移植 `schema_dict_to_pydantic_model`**——Anthropic 工具 input_schema 直接吃 JSON Schema。
2. **schema 校验只做最低限度，不转 Pydantic。** 只校验「dict 且 `type=="object"` 且有 `properties`」，否则 `logger.warning` + 降级 free-text（对齐 browser-use `service.py:1058-1060` 的 try/except 降级）。不做 `$ref`/`allOf` 等关键字展开（Anthropic 原生支持嵌套 JSON Schema）。
3. **保留 `goal` 字段名，不重命名为 browser-use 的 `query`。** 向后兼容已有 schema/description/可能的 prompt 引用——对齐 `save_as_pdf.md` 保留 `path` 而非 browser-use `file_name` 的决策。`goal` ≡ `query`，仅在 docstring 注明。
4. **`extraction_schema` 对 LLM 隐藏，经 Agent 注入而非放进 ExtractParams。** 完全对齐 browser-use（`output_schema` 用 `SkipJsonSchema`，由 `extraction_schema` 注入）。TreeWalker 无 `SkipJsonSchema` 等价物，但 schema 根本不进 param model（存 `Tools._extraction_schema`），所以 LLM 工具 schema 里自然看不到——等效隐藏。
5. **阶段一输入源仍是 `document.body.innerText`**（CDP-native，`execute_js`，低风险）。browser-use 的 markdown 提取（DOMSnapshot→HTMLSerializer→markdownify→清洗）+ 结构化分块 + 表头延续 + `start_from_char` 分页 属**阶段二**（需新增 `markdownify` 依赖 + HTMLSerializer）。
6. **不加内层超时**（用户已选）。browser-use 包 120s；本项目 screenshot/save_as_pdf 刻意不加（依赖 `action_timeout` 默认 30s）。**对齐兄弟工具，不加 `asyncio.wait_for`**；在风险表标注 30s 默认值对抽取类 LLM 调用可能偏紧，需调大 `AGENT_ACTION_TIMEOUT`。
7. **阶段一不做结果大小分级落盘。** browser-use ≥10000 字符 `file_system.save_extracted_content` + `include_extracted_content_only_once`。TreeWalker **无 FileSystem 沙箱**（`save_as_pdf` 决策已确立全路径直写），属阶段二。阶段一结构化结果以 JSON 字符串进 `ActionResult.extracted_content`（与 free-text 返回 `str` 类型一致，不改 `ActionResult`）。
8. **阶段一不做 `already_collected` 去重 / `extract_links` / `extract_images`。** 这些依赖 markdown 提取 + 分块器，属阶段二。
9. **`_extract_llm` 默认复用主 `llm`**（对齐 browser-use `page_extraction_llm = llm` 默认，`agent/service.py:249-250`）；新增可选 `AgentSettings.extract_llm: LLMSettings | None` 供专用小模型，未配置则复用主 `llm`。

---

## 阶段一：接通 LLM + 结构化输出（Agent 注入 schema）+ 分级错误 + 测试（优先做，风险低）

### 1.1 `AgentSettings` 扩展（`config.py:60-79`，4 空格）

新增两字段（程序化设置，**不走 env**——schema dict 不适合 env var；与 `sensitive_data` / `action_page_filters` 同为 dict 类程序化设置一致）：

before（`:60-79` 节选尾部）：
```python
    action_page_filters: dict[str, list[str]] | None = None
    allowed_upload_paths: list[str] | None = None
    judge: JudgeSettings = field(default_factory=JudgeSettings)
```
after：
```python
    action_page_filters: dict[str, list[str]] | None = None
    allowed_upload_paths: list[str] | None = None
    judge: JudgeSettings = field(default_factory=JudgeSettings)
    # extract 工具：专用 LLM（None=复用主 llm）+ 结构化抽取的 JSON Schema（None=free-text）
    extract_llm: "LLMSettings | None" = None
    extraction_schema: dict | None = None
```
> `extract_llm` 用字符串注解 `"LLMSettings | None"` 规避 dataclass 求值顺序（`LLMSettings` 定义在 `:91`，晚于 `AgentSettings`）；`config.py` 顶部已有 `from __future__ import annotations` 则可直接写裸类型——实施时按文件实际 header 决定，二选一。

### 1.2 `Agent.__init__` 接线 `_extract_llm` + `_extraction_schema`（`agent.py:57` 之后，4 空格）

before（`:57`）：
```python
        self.tools = tools or Tools(truncation=_settings.truncation, allowed_upload_paths=_settings.allowed_upload_paths)
        if _settings.action_page_filters:
            self.tools.apply_page_filters(_settings.action_page_filters)
```
after：
```python
        self.tools = tools or Tools(truncation=_settings.truncation, allowed_upload_paths=_settings.allowed_upload_paths)
        if _settings.action_page_filters:
            self.tools.apply_page_filters(_settings.action_page_filters)

        # extract 工具接线（对齐 browser-use：page_extraction_llm 默认=主 llm；extraction_schema 注入）
        if _settings.extract_llm is not None:
            self.tools._extract_llm = LLMClient(_settings.extract_llm)
        else:
            self.tools._extract_llm = self.llm
        self.tools._extraction_schema = _settings.extraction_schema
```
> `LLMClient` 已在 `agent.py:20` import。接线放在 `self.tools = ...` 之后，无论 tools 是默认构造还是外部传入都生效。TUI 链路 `cli.py:37-46 → app.py:198-202` 已把 `settings.agent` 透传给 `Agent(settings=...)`，故 `settings.agent.extraction_schema` / `extract_llm` 自动可达，无需改 cli/TUI。

### 1.3 `LLMClient.extract` 升级（`client.py:295-311`，4 空格）

加 `output_schema` 入参 + 结构化路径（`extract_result` 工具 + tool_choice 强约束 + 解析 tool_use `.input` → `json.dumps`）+ schema 最低校验降级 + free-text 路径保留（goal 进 prompt）。复用 `get_action`（`client.py:180-204`）的 tool-use 模式与 fallback 切换。

before：
```python
    async def extract(self, prompt: str, content: str, *, max_content_chars: int = 8000) -> str:
        """Secondary LLM call for page data extraction."""
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                messages=[
                    {"role": "user", "content": f"{prompt}\n\n---\n{content[:max_content_chars]}"},
                ],
            )
        except (RateLimitError, APIError) as e:
            if self._try_switch_to_fallback(e):
                return await self.extract(prompt, content, max_content_chars=max_content_chars)
            raise

        text_parts = [b.text for b in response.content if hasattr(b, "text")]
        return "\n".join(text_parts)
```
after：
```python
    async def extract(
        self,
        prompt: str,
        content: str,
        *,
        max_content_chars: int = 8000,
        output_schema: dict[str, Any] | None = None,
    ) -> str:
        """Secondary LLM call for page data extraction.

        When ``output_schema`` is a JSON Schema dict (top-level type=object with
        properties), forces structured output via an Anthropic tool and returns
        the validated JSON as a string. Otherwise returns free-text extraction.
        """
        # 最低限度校验 schema；不可用则降级 free-text（对齐 browser-use try/except 降级）
        if output_schema is not None and (
            not isinstance(output_schema, dict)
            or output_schema.get("type") != "object"
            or not output_schema.get("properties")
        ):
            logger.warning("Invalid output_schema, falling back to free-text extraction")
            output_schema = None

        content = content[:max_content_chars]

        if output_schema is not None:
            system_prompt = (
                "You are an expert at extracting structured data from a webpage. "
                "Extract exactly what the query asks for and return it via the "
                "extract_result tool, conforming strictly to the provided JSON Schema. "
                "Omit fields you cannot find rather than guessing."
            )
            tool = {
                "name": "extract_result",
                "description": "Structured extraction result conforming to the given schema.",
                "input_schema": output_schema,
            }
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=2048,
                    system=system_prompt,
                    messages=[{"role": "user", "content": f"{prompt}\n\n---\n{content}"}],
                    tools=[tool],
                    tool_choice={"type": "tool", "name": "extract_result"},
                )
            except (RateLimitError, APIError) as e:
                if self._try_switch_to_fallback(e):
                    return await self.extract(
                        prompt, content, max_content_chars=max_content_chars, output_schema=output_schema,
                    )
                raise
            for block in response.content:
                if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "extract_result":
                    if isinstance(block.input, dict):
                        return json.dumps(block.input, ensure_ascii=False)
            # 模型未用工具 → 同一响应里取 text 兜底
            logger.warning("LLM did not use extract_result tool; falling back to free-text")
            text_parts = [b.text for b in response.content if hasattr(b, "text")]
            return "\n".join(text_parts)

        # free-text 路径（goal 进 user message）
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                messages=[
                    {"role": "user", "content": f"{prompt}\n\n---\n{content}"},
                ],
            )
        except (RateLimitError, APIError) as e:
            if self._try_switch_to_fallback(e):
                return await self.extract(
                    prompt, content, max_content_chars=max_content_chars, output_schema=output_schema,
                )
            raise
        text_parts = [b.text for b in response.content if hasattr(b, "text")]
        return "\n".join(text_parts)
```
> 递归重试时把 `output_schema` 原样回传（已降级为 None 的情况递归也是 None，行为正确）。`json` / `Any` / `RateLimitError` / `APIError` 均已模块级 import。返回类型保持 `str`（结构化路径返回 JSON 字符串），与 `ActionResult.extracted_content: str | None` 契合。

### 1.4 `_action_extract` 重写（`actions.py:633-647`，4 空格）

删死 import；`execute_js` 失败 `logger.warning`（不再静默）；空页 → `"(empty page)"`；无 llm → 截断兜底（保留显式降级）；调 `llm.extract(goal, page_text, max_content_chars=..., output_schema=schema)`；LLM 异常 → 分级 `ActionResult(error=...)`（不再冒泡通用 catch）。

before：
```python
    async def _action_extract(self, params: dict, browser: BrowserSession) -> ActionResult:
        goal = params["goal"]
        try:
            page_text = await browser.execute_js("document.body.innerText")
        except Exception:
            page_text = ""
        if not page_text:
            return ActionResult(extracted_content="(empty page)")
        # Simple extraction: return first 2000 chars with goal context
        from tree_walker.llm.client import LLMClient
        llm = getattr(self, "_extract_llm", None)
        if llm:
            result = await llm.extract(goal, page_text[:self._truncation.extract_page_max_chars])
            return ActionResult(extracted_content=result)
        return ActionResult(extracted_content=page_text[:self._truncation.extract_fallback_max_chars])
```
after：
```python
    async def _action_extract(self, params: dict, browser: BrowserSession) -> ActionResult:
        goal = params["goal"]
        try:
            page_text = await browser.execute_js("document.body.innerText")
        except Exception as e:
            logger.warning("extract: document.body.innerText failed: %s", e)
            page_text = ""
        if not page_text:
            return ActionResult(extracted_content="(empty page)")

        llm = getattr(self, "_extract_llm", None)
        if llm is None:
            # 未接 LLM（如脱离 Agent 直接用 Tools）——显式降级为截断原文
            return ActionResult(extracted_content=page_text[:self._truncation.extract_fallback_max_chars])

        schema = getattr(self, "_extraction_schema", None)
        try:
            result = await llm.extract(
                goal,
                page_text,
                max_content_chars=self._truncation.extract_page_max_chars,
                output_schema=schema,
            )
        except Exception as e:
            logger.warning("extract: LLM call failed: %s", e)
            return ActionResult(error=f"Extract failed: {e}")
        return ActionResult(extracted_content=result)
```
> 生产环境经 1.2 接线后 `_extract_llm` 必非 None，截断兜底仅供测试 / 非 Agent 直接用 `Tools()` 时安全网。`_extract_llm` / `_extraction_schema` 用 `getattr` 防御性读取，不改 `Tools.__init__`（`actions.py:154-159`），最小改动面。

### 1.5 `ExtractParams` 不改（`models.py:54-56`，4 空格）

阶段一 **`ExtractParams` 保持 `goal: str` 唯一字段**。结构化 schema 经 `Tools._extraction_schema` 注入、对 LLM 隐藏（不进 param model），完全对齐 browser-use 隐藏 `output_schema` 的做法。`goal` 不重命名（见差异 §3）。

### 1.6 新增 `tests/test_extract.py`（TAB 缩进，对齐 `tests/test_screenshot.py` / `tests/test_save_as_pdf.py`）

镜像兄弟测试的 mock 工厂 + `Tools().execute("extract", {...}, browser)` 模式：

- **工具层（`_action_extract`）**——`_make_mock_browser()` 仅设 `browser.execute_js = AsyncMock(return_value=...)`；`_make_mock_extract_llm()` 返回带 `extract = AsyncMock(return_value=...)` 的 MagicMock，注入 `tools._extract_llm`：
  - free-text 路径：`_extraction_schema=None` → `extract` 以 `output_schema=None` 被调用，`extracted_content == mock 返回值`；
  - 结构化路径：`tools._extraction_schema = {"type":"object","properties":{"title":{"type":"string"}}}` → `extract` 以该 schema 被调用，回显 `extract` 返回值；
  - 空页（`execute_js` 返回 `""`）→ `extracted_content == "(empty page)"`，`extract` 未被调用；
  - `execute_js` 抛异常 → `logger.warning` 被调用 + `extracted_content == "(empty page)"`（不再静默）；
  - `extract` 抛异常 → `ActionResult(error=f"Extract failed: ...")`（不冒泡通用 catch）；
  - `_extract_llm=None` → 截断兜底，`extracted_content == page_text[:fallback_max]`。
- **接线层（`Agent.__init__`）**——构造 `Agent(task=..., llm=fake_llm, browser=fake_browser, settings=AgentSettings(...))`：
  - 默认：`tools._extract_llm is fake_llm` 且 `tools._extraction_schema is None`；
  - 设 `extract_llm=LLMSettings(model="glm-flash", api_key="x")` → `isinstance(tools._extract_llm, LLMClient)` 且模型名正确（不复用主 llm）；
  - 设 `extraction_schema={...}` → `tools._extraction_schema == {...}`。
- **client 层（`LLMClient.extract`）**——`patch.object(LLMClient, "client")` 或注入假 `Anthropic`，断言 `messages.create` 入参 + 解析返回：
  - 结构化：`output_schema={...}` → `messages.create` 带 `tools=[{name:"extract_result", input_schema:...}]` + `tool_choice={"type":"tool","name":"extract_result"}`；响应 `content=[MagicMock(type="tool_use", name="extract_result", input={"title":"X"})]` → 返回 `'{"title": "X"}'`；
  - 模型未用工具（响应只有 text block）→ 降级返回 text；
  - 非法 schema（非 dict / 缺 type / 缺 properties）→ `logger.warning` + 走 free-text 分支（无 tools）；
  - free-text：`output_schema=None` → 无 `tools`/`tool_choice`，返回 text；
  - `RateLimitError` → 触发 `_try_switch_to_fallback`，有 fallback 则切模型重试一次（对齐 `get_action` 的 fallback 测试模式）。
- 异步测试逐个标 `@pytest.mark.asyncio`（项目无全局 `asyncio_mode`）；client 层可用 `pytest` 的 `monkeypatch` 注入假 SDK 响应对象（`.content` 为 block 列表）。

### 1.7 阶段一文件清单

| 文件 | 改动 | 锚点 |
|---|---|---|
| `src/tree_walker/config.py` | `AgentSettings` 加 `extract_llm` + `extraction_schema` | `:60-79`，**4 空格** |
| `src/tree_walker/agent/agent.py` | `Agent.__init__` 接线 `_extract_llm` / `_extraction_schema` | `:57` 之后，**4 空格** |
| `src/tree_walker/llm/client.py` | `extract` 加结构化路径（tool-use 强约束） | `:295-311`，**4 空格** |
| `src/tree_walker/tools/actions.py` | 重写 `_action_extract`（删死 import、分级错误、传 schema） | `:633-647`，**4 空格** |
| `tests/test_extract.py` | 新建（工具层 / 接线层 / client 层） | **TAB** 缩进 |
| `docs/Tools技术细节/04_动作清单与CDP映射.md` | 更新 4.6 节（接线说明 + 结构化路径 + 行号修正为 `actions.py:633`） | §4.6 |

### 1.8 阶段一测试计划

```powershell
uv run python -m pytest tests/test_extract.py -x -v
uv run python -m pytest tests/ -x -v
uv run python -m pytest tests/test_extract.py --cov=tree_walker.tools.actions --cov=tree_walker.llm.client --cov=tree_walker.agent.agent --cov-report=term-missing
```

---

## 阶段二（可选，独立，对齐 browser-use 完整能力）

阶段一交付「接通 + 结构化 + 分级错误 + 测试」；以下按需再开，不在阶段一交付：

- **markdown 提取**：移植 `extract_clean_markdown`（DOMSnapshot→HTMLSerializer→`markdownify`→清洗 JSON blob）。需新增 `markdownify` 依赖 + 复用项目已有 DOM 序列化（`browser/serializer.py`、`browser.get_state()` 的 `dom_state`）。新增 `extract_links` / `extract_images` 参数。
- **结构化分块 + 分页**：移植 `chunk_markdown_by_structure`（max 100000 字符、表头延续、`start_from_char` 偏移）+ `MarkdownChunk` 数据类，支持长页面分多次抽取。
- **`already_collected` 去重**：跨页抽取时跳过已收集项（需把 `already_collected` 透传进 ExtractParams + prompt）。
- **结果大小分级落盘**：≥10000 字符写文件 + `include_extracted_content_only_once`（需先建轻量文件输出约定，或复用 `write_file` 路径）。
- **专用小模型默认化**：把 `extract_llm` 接入 `load_settings()`（env `AGENT_EXTRACT_MODEL` 等），让低成本模型成为默认而非程序化可选。
- **`goal` → `query` 重命名**：若决定全面对齐 browser-use 字段名（breaking，需同步 prompt 与 schema 文档）。
- **inner timeout**：若 30s `action_timeout` 实测偏紧，给 `extract` 加 LLM 专用 `asyncio.wait_for`（参考 browser-use 120s）。

---

## 风险与回归点

| 风险 | 影响 | 缓解 |
|---|---|---|
| `_extract_llm` 默认复用主 llm | 抽取调用与主循环共享同一 client（速率限制、fallback 状态联动） | 对齐 browser-use 默认；需隔离时配 `extract_llm`（阶段二可默认化） |
| `extraction_schema` 直接做工具 input_schema | 复杂/嵌套 schema 若含 Anthropic 不支持的关键字可能 400 | 最低限度校验降级 free-text；Anthropic 原生支持嵌套 object/array，常见 schema 无虞；阶段二可加更严校验 |
| 不加内层超时 | 抽取类 LLM 调用挂起时受 `action_timeout`（默认 30s）限制，可能偏紧 | 文档标注需调大 `AGENT_ACTION_TIMEOUT`；阶段二可加专用 timeout |
| 结构化结果以 JSON 字符串进 `extracted_content` | `ActionResult.__str__` 截断到 `display_max_chars`（500） | 与 free-text 一致；大结果阶段二落盘 |
| `_extract_llm` / `_extraction_schema` 用 getattr 不进 `Tools.__init__` | 类型不可静态推断 | 防御性读取，最小改动面；接线测试覆盖 |
| 改 `extract` 签名加 `output_schema` | 旧调用方不传该参 | 有默认值 `None`，向后兼容；free-text 行为不变 |

---

## 验证方法

1. **单测全绿 + 覆盖率 ≥85%**（命令见 1.8）。
2. **接线冒烟**（需真实浏览器 ws + LLM key）：
   ```python
   from tree_walker.config import AgentSettings, LLMSettings
   schema = {"type":"object","properties":{"title":{"type":"string"},"price":{"type":"string"}},"required":["title"]}
   settings = AgentSettings(extraction_schema=schema)
   agent = Agent(task="...", llm=llm, browser=browser, settings=settings)
   # 任务中触发 extract → 结构化 JSON 进 extracted_content
   ```
   确认：`tools._extract_llm is agent.llm`；`tools._extraction_schema == schema`；extract 返回合法 JSON 字符串。
3. **free-text 冒烟**：`AgentSettings()`（不设 schema）→ extract 返回自然语言摘要，`goal` 实际影响输出（对比旧版丢弃 goal）。
4. **回归对照**：`v0.4.0..master` 范围内 extract 此前零测试、零真实调用，阶段一建立基线；全量 `tests/` 回归无破坏。

---

## 验收 checklist（阶段一）

- [ ] `AgentSettings` 含 `extract_llm: LLMSettings | None = None` + `extraction_schema: dict | None = None`
- [ ] `Agent.__init__` 接线：默认 `tools._extract_llm = self.llm`；配 `extract_llm` 则构造专用 `LLMClient`；`tools._extraction_schema = _settings.extraction_schema`
- [ ] `LLMClient.extract` 支持 `output_schema`：结构化路径用 `extract_result` 工具 + `tool_choice` 强约束 + 解析 `tool_use.input` → JSON 字符串；非法 schema 降级 free-text；保留 fallback 切换
- [ ] `_action_extract` 删死 import、`execute_js` 失败 `logger.warning`、LLM 异常分级 `ActionResult(error=...)`、传 `output_schema=getattr(self,"_extraction_schema",None)`
- [ ] `tests/test_extract.py` 覆盖工具层（free-text/结构化/空页/execute_js 失败/LLM 异常/无 llm 兜底）+ 接线层（默认复用主 llm、专用 extract_llm、schema 注入）+ client 层（tool_use 解析/未用工具降级/非法 schema 降级/free-text/RateLimit fallback），全绿
- [ ] 全量回归 `uv run python -m pytest tests/ -x -v` 通过，覆盖率 >85%
- [ ] `docs/Tools技术细节/04_动作清单与CDP映射.md` 4.6 节同步更新（接线说明 + 结构化路径 + 行号修正）
