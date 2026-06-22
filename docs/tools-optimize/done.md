# done 工具优化方案（分阶段）

> 参照 browser-use（`browser_use/tools/service.py:1934-2020` `_register_done_action` + 变体 A `done` 动作体 `:1979-2016`、`browser_use/tools/views.py:89-101` `DoneAction`、`browser_use/agent/views.py:307-349` `ActionResult`）完善本项目 done 工具。
> 相关现状文档：`docs/Tools技术细节/04_动作清单与CDP映射.md` 的 4.3 节（注：该节所引 `actions.py:477-482` 行号已 stale，实际为 `:1418-1423`，实现阶段一并修订）；参考标杆：`browser-use/docs/Tools技术细节/06-动作详解-数据处理与文件.md` 的 23. done 节。
> 同族先例：`docs/tools-optimize/read_file.md`（commit `fc088bc` / issue #54 / PR #55）、`docs/tools-optimize/replace_file.md`（commit `f7d038b` / issue #52 / PR #53）、`docs/tools-optimize/write_file.md`（commit `5983f61` / issue #50 / PR #51，**回显/双写规范的最直接对标**）、`docs/tools-optimize/search_page.md`（`c5db7db`，soft-miss 模式来源——done 因"必须终止"有意偏离）、`docs/tools-optimize/evaluate.md`（`9a60e9d`）。本方案在回显规范上全面对齐 file trio 阶段一；done 是唯一"设置 `is_done=True` 终止 agent 循环"的动作，故空 `text` 处理有意偏离同族 soft-miss（详见差异 #3）。

---

## 适用场景（什么时候会用到 done）

**定位**：done 是本项目**唯一的任务完成出口**——它设置 `is_done=True`，`StepPipeline.step()` 检测到 `any(r.is_done)` 即提前返回（[`step.py:103`](../../src/tree_walker/agent/step.py)），主循环退出。done 不触碰浏览器（`browser` 形参未用），是纯状态信号 + 汇报。

| 工具 / 机制 | 职责 | 与 done 的区别 |
|---|---|---|
| `done`（动作） | 标记完成 + 终止循环 + 汇报总结 | 唯一终止出口 |
| `evaluate` | 页面内执行 JS | 不终止；产出走 `extracted_content` |
| `extract` / `search_page` | 读页面文本 | 不终止；只读 |
| `_force_done_on_last_step` / `_force_done_after_failure`（机制） | step 层在 max_steps / 连续失败时强制 LLM 调 done（切 done-only schema） | 不是工具，是 [`step.py:250-283`](../../src/tree_walker/agent/step.py) 的注入 |
| `_FALLBACK_DONE_OUTPUT`（机制） | LLM 无动作 / 参数非法兜底成 `done(success=False)` | 不是工具，是 [`step.py:33-38`](../../src/tree_walker/agent/step.py) 的合成输出 |

**典型场景**：

1. 任务全部完成 → LLM 主动 `done(success=True)` + 总结。
2. 到达 `max_steps - 1` → `_force_done_on_last_step` 注入 → done。
3. 连续失败达 `max_failures` → `_force_done_after_failure` 注入 → `done(success=False)`。
4. 不可能继续（CAPTCHA / 登录失败）→ LLM 主动 `done(success=False)`。
5. LLM 返回空 / 参数非法且重试用尽 → `_FALLBACK_DONE_OUTPUT` 合成 `done(success=False)` 保证循环一定终止。

**什么时候不需要它**：

- 任务尚未完成 → 继续用 `click` / `input_text` / `navigate` 等；done 是单步单动作（[`step.py:540-548`](../../src/tree_walker/agent/step.py)），不能与其它动作同发。
- 只想暂存中间结果 → `evaluate` / `extract` / `write_file`，不终止。

**可用性提示**：阶段一覆盖 `text`/`success` description 富化（anti-hallucination）、`long_term_memory` 双写回显 + `logger.info`、空 `text` 运行时守卫（warn + 兜底默认值，保证终止）、`min_length=1`、全量单测；阶段二再补结构化输出（`output_model` / `data`）、`files_to_display` / `attachments`、自动附加 downloads（见末尾）。

---

## Context（为什么做这个改动）

当前实现（[`src/tree_walker/tools/actions.py:1418-1423`](../../src/tree_walker/tools/actions.py)）：

```python
async def _action_done(self, params: dict, browser: BrowserSession) -> ActionResult:
    return ActionResult(
        is_done=True,
        success=params.get("success", True),
        extracted_content=params.get("text", ""),
    )
```

参数模型（[`src/tree_walker/tools/models.py:232-238`](../../src/tree_walker/tools/models.py)）：

```python
class DoneParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(description=(
        "Final summary. ONLY report data you directly observed in page state, "
        "tool outputs, or screenshots during this session."
    ))
    success: bool = Field(default=True, description="Whether the task was completed successfully")
```

注册项（[`src/tree_walker/tools/models.py:347`](../../src/tree_walker/tools/models.py)）：`"done": (DoneParams, "Signal that the task is complete with a summary", False)`

**主要问题**：

1. **不设 `long_term_memory`** —— done 是同族里唯一不双写的；`extracted_content` 只进 `final_result()`（[`views.py:86-91`](../../src/tree_walker/agent/views.py)），agent 在记忆压缩里没有"任务完成"摘要一行，与 write_file / replace_file / read_file 全部双写不一致。
2. **无 `logger.info` 回显** —— 同族每个成功路径都 `logger.info(memory)`，done 静默。
3. **空 `text` 语义模糊 + 无运行时守卫** —— `text=""` → `extracted_content=""`，`ActionResult.__str__`（[`views.py:27-37`](../../src/tree_walker/agent/views.py)）对 falsy `extracted_content` 只渲染 `DONE (success=...)`，无摘要回显；更关键的是 `Tools.execute` 路径**不经 param_model 校验**（项目约定：execute 路径不校验，handler 需自加运行时守卫），LLM 即便传 `""`/`"   "` 也会照单全收。
4. **`description` 过简** —— `text` 描述未含 anti-hallucination 指令；browser-use 的 `DoneAction.text` 明确禁止用训练知识补缺口、禁止引用压缩记忆里未亲验的步骤、不确定要明说。`success` 描述未指导何时该设 `False`。
5. **无 `min_length=1`** —— 与 `replace_file.old`、`search_page.query` 不一致。
6. **零测试** —— `tests/` 下无 `test_done.py`（仅 `test_force_done_schema.py` 测 registry 的 `include_actions` 过滤，未覆盖 `_action_done`）。

**参照标杆 browser-use 的做法**：变体 A（`DoneAction`，`service.py:1979-2016`）设 `is_done=True` + `extracted_content=user_message`（全文）+ `long_term_memory=f'Task completed: {success} - {text[:100]}'`（>100 字附 ` - N more characters`）+ `attachments`（绝对路径）；`success=True` 必须配 `is_done=True` 由 `ActionResult` 校验器强制（同构于本项目 [`views.py:18-25`](../../src/tree_walker/agent/views.py)）；`text` 描述含强 anti-hallucination 指令（"Do NOT use training knowledge to fill gaps… Do NOT claim completion of steps from compacted_memory… unless you explicitly verified them yourself"）。

**预期结果**：done 与 file trio 阶段一**回显同构**——`long_term_memory` 双写 + `logger.info`、`text`/`success` description 富化、`min_length=1` + 运行时守卫、全量单测；并尊重 done 独有的"必须终止"约束（空 `text` 不走 soft-miss，改 warn + 兜底默认值）。

---

## 工程约束（实施时务必遵守）

- Windows + PowerShell；包用 uv，跑脚本 / 测试用 `uv run python ...`。测试命令 `uv run python -m pytest tests/ -x -v`。
- **缩进按文件**（已复核）：`src/tree_walker/tools/models.py`、`src/tree_walker/tools/actions.py` = **4 空格**；`tests/test_done.py` = **TAB**（对齐 `tests/test_replace_file.py`）。
- 改完跑相关单测 + 全量回归；覆盖率目标 >85%。
- 不主动 `git commit` / `git push`。
- `logger` 在 `actions.py` 已是模块级 import（[`actions.py:19`](../../src/tree_walker/tools/actions.py)）；`Field` / `ConfigDict` 已在 `models.py` import。重写 `_action_done` 无需新 import。

---

## 与 browser-use 的关键差异（有意为之，不照搬）

1. **不移植结构化输出变体（`output_model` / `data`）。** browser-use 按 `Tools(output_model=...)` 是否提供分派变体 A（自由文本 `DoneAction`）/ 变体 B（`StructuredOutputAction[T]`，`Generic`，schema 用 `_hide_internal_fields_from_schema` 隐藏 `success`/`files_to_display`，`data` 走用户 Pydantic 模型，`model_dump(mode='json')` 序列化）。本项目 `ACTION_DEFINITIONS` 是固定 `dict[str, tuple]` + 具体 `param_model`，结构化输出需改注册 / schema 机制（影响 [`registry.py:59-194`](../../src/tree_walker/tools/registry.py)），留阶段二。
2. **不移植 `files_to_display` / `attachments`。** browser-use 注入 `file_system` 解析附件（变体 A 还可把文件内容内联进 `extracted_content` 的 `Attachments:` 段），变体 B 自动附加 `browser_session.downloaded_files`。本项目 handler 签名是 `(self, params, browser)`，无 `file_system` 注入，done 当前也不接触下载追踪；留阶段二。
3. **空 `text` 不走 soft-miss（有意偏离同族）。** file trio 对"软失败"返回 `extracted_content + long_term_memory` 且 `is_done=False`（不终止）；但 done **必须** `is_done=True` 才能退出循环（[`step.py:103`](../../src/tree_walker/agent/step.py)），soft-miss 会变成非终止循环、且 done 是单步单动作（[`step.py:540-548`](../../src/tree_walker/agent/step.py)），下一轮只会再调 done，烧预算无收益。故 done 对空 `text` 采用 **warn + 兜底默认值 `"(no summary provided)"`**，保证终止同时让退化情形在日志可见（与 `_FALLBACK_DONE_OUTPUT` 选安全默认值 + 终止的思路一致）。
4. **不移植 ` - N more characters` 后缀。** browser-use 对 >100 字 `text` 在 `long_term_memory` 追加 ` - {N} more characters`。本项目 `extracted_content` 已带全文，memory 仅作一行摘要，后缀边际价值低，留阶段二。
5. **校验双层（对齐 `replace_file.old`）。** `min_length=1` 在 schema（LLM 可见 + 直接构造 + `_validate_params_or_retry` 重试路径生效）；handler 内 `if not text.strip()` 运行时守卫（`Tools.execute` 路径不校验）。

---

## 阶段一：description 富化 + long_term_memory 回显 + 空 text 守卫 + 测试（优先做，风险低）

### 1.1 `DoneParams` 富化（`models.py:232-238`，4 空格）

`before:` 见 [Context](#context为什么做这个改动) 节。

`after:`

```python
class DoneParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(
        min_length=1,
        description=(
            "Final message to the user. ONLY report data you directly observed in "
            "page state, tool outputs, or screenshots during this session. Do NOT "
            "use training knowledge to fill gaps — if information was not found on "
            "the page, say so explicitly. Do NOT claim completion of steps from "
            "compacted_memory or prior session summaries unless you explicitly "
            "verified them yourself. If uncertain whether a prior step completed, "
            "say so explicitly. Must be non-empty."
        ),
    )
    success: bool = Field(
        default=True,
        description=(
            "Whether the task was completed successfully. Set to False if any "
            "stated requirement was unmet, the page did not contain the expected "
            "data, or a step could not be verified. Leave True only when every "
            "requirement was directly confirmed this session."
        ),
    )
```

> anti-hallucination 文案取自 browser-use `DoneAction.text`（按其 §23），"page state" 对齐本项目 system prompt 既有术语；"Must be non-empty." 锚定 `min_length=1`（schema 路径，LLM 可见）。`success` 描述补"何时 False"指引（与 [`system_prompt.py:47-67`](../../src/tree_walker/prompts/system_prompt.py) 的 before-calling-done checklist 呼应）。`extra="forbid"` 保留（与全文件其它 params 一致）。

### 1.2 `_action_done` 重写（`actions.py:1418-1423`，4 空格）

`before:` 见 [Context](#context为什么做这个改动) 节。

`after:`

```python
async def _action_done(self, params: dict, browser: BrowserSession) -> ActionResult:
    success = params.get("success", True)
    text = (params.get("text") or "").strip()
    if not text:
        # done 必须终止（is_done=True 才退出循环，step.py:103），空 text 不能走
        # soft-miss（会变非终止循环）。兜底默认值保证终止 + 让退化情形在日志可见。
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

**阶段一关键决策（压测确认）**：

| 决策点 | 结论 | 理由 |
|---|---|---|
| `long_term_memory` 格式 | `f"Task completed: {success} - {text[:100]}"` | 对齐 browser-use 变体 A；一行摘要，与 file trio 双写一致；`extracted_content` 已带全文，memory 仅作压缩友好的一行 |
| `text[:100]` 截断 | ✅ 截到 100 字符 | 对齐 browser-use；memory 是摘要不是全文，截断降低压缩成本 |
| ` - N more characters` 后缀 | ❌ 不加（留阶段二） | `extracted_content` 已带全文，后缀边际价值低；阶段一对齐 file trio（不加） |
| 空 / 纯空白 `text` 处理 | warn + 兜底 `"(no summary provided)"`，仍 `is_done=True` | done 必须终止，不能 soft-miss（差异 #3）；与 `_FALLBACK_DONE_OUTPUT` 思路一致 |
| `text.strip()` 用于判空 + 回显 | ✅ | 纯空白 `"   "` 视同空（`min_length=1` 不拦空白）；回显剔除首尾空白 |
| `success` 取值 | `params.get("success", True)` 保持现状 | 保留默认 True 语义；不强转类型（与原实现一致，最小改动） |
| 是否校验 `success=True` 须 `is_done=True` | 无需额外处理 | 恒设 `is_done=True`，`ActionResult` 校验器（[`views.py:18-25`](../../src/tree_walker/agent/views.py)）自然通过 |
| 错误冒泡通用 catch | ❌ 无错误路径 | done 无 IO / 无 CDP，无异常源；不会落到 `Tools.execute` 通用 catch（[`actions.py:260-262`](../../src/tree_walker/tools/actions.py)） |

### 1.3 `ACTION_DEFINITIONS["done"]` description 更新（`models.py:347`，4 空格）

`before:`

```python
    "done": (DoneParams, "Signal that the task is complete with a summary", False),
```

`after:`

```python
    "done": (
        DoneParams,
        "Signal that the task is complete and stop the agent. Must be the only action "
        "in the step. Provide a final summary of what was accomplished; set success=False "
        "if any requirement was unmet or could not be verified.",
        False,
    ),
```

> `terminates_sequence` 保持 `False`（对齐 browser-use：done 不靠 `terminates_sequence` 终止，靠 `is_done` 检查，[`step.py:103`](../../src/tree_walker/agent/step.py)）。"Must be the only action in the step" 在 schema 层重申 system prompt 规则 + [`step.py:540-548`](../../src/tree_walker/agent/step.py) 的多动作守卫。动作描述保持简短（anti-hallucination 全文在字段描述里，与 browser-use 一致）。

### 1.4 新增 `tests/test_done.py`（TAB 缩进，对齐 `tests/test_replace_file.py`）

文件头 + 入口（done 无 FS，**无 `tmp_path` / `_seed` / `_read`**）：

```python
"""Tests for done: terminal action echo, empty-text guard, param validation.

Covers:
- action layer: done sets is_done=True; success echoes (True/False);
  extracted_content carries the summary; long_term_memory is the compact
  'Task completed: {success} - {text[:100]}' line; logger.info is called;
  empty/whitespace text triggers warn + default-substitute (termination
  preserved, no soft-prompt); browser is unused (done touches no session)
- param model: DoneParams.text is required, min_length=1, forbids extra;
  success defaults True. Model-level tests are SYNC (the execute path does
  NOT validate, so the handler adds its own runtime guard).

Tools().execute(...) entry point, MagicMock() browser, TAB indentation per
CLAUDE.md. No tmp_path (done has no filesystem surface).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from tree_walker.tools.actions import Tools
from tree_walker.tools.models import DoneParams


async def _run(params: dict):
	"""Drive done through the public Tools().execute entry point."""
	tools = Tools()
	return await tools.execute("done", params, MagicMock())
```

**测试类**（每组一个 class，`@pytest.mark.asyncio async def`；ParamsValidation 同步无 mark）：

- **TestDoneBasic**
    - `test_done_sets_is_done_true` —— `is_done is True`。
    - `test_done_success_default_true` —— 不传 success → `success is True`。
    - `test_done_success_false_echoes` —— `success=False` → `is_done is True`、`success is False`。
    - `test_done_no_error` —— `error is None`。
- **TestDoneEcho**
    - `test_extracted_content_is_text` —— `extracted_content == text`。
    - `test_long_term_memory_compact_success` —— memory == `"Task completed: True - done"`。
    - `test_long_term_memory_compact_failure` —— `success=False` → `"Task completed: False - nope"`。
    - `test_long_term_memory_truncates_at_100_chars` —— 250 字符 text → memory 含前 100 字符；`extracted_content` 保留全文。
    - `test_logger_info_emits_memory`（`caplog`）—— INFO 记录含 memory 行。
    - `test_browser_not_used` —— `MagicMock()` browser 无任何方法调用（固化 done 不碰 session）。
- **TestDoneEmptyText**
    - `test_empty_text_still_terminates` —— `text=""` → `is_done is True`（终止保证）。
    - `test_empty_text_uses_default_summary` —— `extracted_content == "(no summary provided)"`。
    - `test_whitespace_text_treated_as_empty` —— `text="   \t  "` → 兜底默认值 + `is_done is True`。
    - `test_missing_text_key_treated_as_empty` —— `_run({})` → 兜底默认值。
    - `test_empty_text_warns`（`caplog`）—— WARNING 含 `"empty text"`。
    - `test_empty_text_memory_uses_default` —— memory == `"Task completed: True - (no summary provided)"`。
- **TestDoneParamsValidation**（同步，无 asyncio mark）
    - `test_text_required` —— `DoneParams()` 抛 `ValidationError`。
    - `test_text_min_length_rejects_empty` —— `DoneParams(text="")` 抛 `ValidationError`。
    - `test_text_accepts_nonempty` —— `DoneParams(text="ok")` OK，`success is True`。
    - `test_success_can_be_false` —— `success=False` 可设。
    - `test_extra_forbidden` —— `DoneParams(text="ok", files_to_display=[])` 抛 `ValidationError`。

### 1.5 阶段一文件清单

| 文件 | 改动 | 锚点 |
|---|---|---|
| `src/tree_walker/tools/models.py` | 富化 `DoneParams.text`（`min_length=1` + anti-hallucination）与 `success` 描述；更新 `ACTION_DEFINITIONS["done"]` description | `:232-238`、`:347` |
| `src/tree_walker/tools/actions.py` | 重写 `_action_done`：`long_term_memory` 双写回显 + `logger.info` + 空 text 运行时守卫 | `:1418-1423` |
| `tests/test_done.py` | 新增（4 个测试类，TAB 缩进） | 新文件 |
| `docs/Tools技术细节/04_动作清单与CDP映射.md` | 修订 4.3 节 stale 行号 `actions.py:477-482` → `:1418-1423`（+同步新行为描述） | 4.3 节 |

### 1.6 阶段一测试计划

```powershell
uv run python -m pytest tests/test_done.py -x -v
uv run python -m pytest tests/test_done.py --cov=tree_walker.tools.actions --cov-report=term-missing
uv run python -m pytest tests/test_force_done_schema.py tests/test_judge.py tests/test_action_result_semantics.py tests/test_action_registry.py -x -v
uv run python -m pytest tests/ -x -v
```

---

## 阶段二（可选，独立，对齐 / 超越 browser-use 完整能力）

- **结构化输出（`output_model` / `data`）** —— browser-use 变体 B：`Tools(output_model=SomePydanticModel)` 时用 `StructuredOutputAction[T]`，schema 隐藏 `success`/`files_to_display`，`data` 走用户模型，`model_dump(mode='json')` 序列化进 `extracted_content`。本项目需把 `ACTION_DEFINITIONS` 的固定 `param_model` 升级为条件 / `Generic` 注册，改动面大。
- **`files_to_display` / `attachments`** —— 新增 `files_to_display: list[str]` 字段，handler 解析为绝对路径塞进 `attachments`（本项目 `ActionResult` 暂无 `attachments` 字段，需先扩 [`views.py:8-37`](../../src/tree_walker/agent/views.py)）。
- **自动附加 downloads** —— 把会话下载（`AgentState.downloaded_files`）在 done 时自动并入 `attachments`（对齐 browser-use 变体 B 的 `browser_session.downloaded_files`）。
- **` - N more characters` 后缀** —— `len(text) > 100` 时在 `long_term_memory` 追加（对齐 browser-use 变体 A）。
- **`display_files_in_done_text` 开关** —— 控制是否把附件内容内联进 `extracted_content` 的 `Attachments:` 段。

---

## 风险与回归点

| 风险 | 影响 | 缓解 |
|---|---|---|
| `min_length=1` 误伤 `_FALLBACK_DONE_OUTPUT` | 无 | 兜底输出在 [`step.py:414,447`](../../src/tree_walker/agent/step.py) 直接 `return dict(...)`，**绕过** `_validate_action_params`；且其 `text="No action returned by LLM"` 非空 |
| `min_length=1` 触发参数重试死循环 | 低 | `_validate_params_or_retry` 有硬上限 `_PARAM_VALIDATION_MAX_RETRIES`，用尽后"proceeding anyway"（不抛），再由 handler 运行时守卫兜底——保证终止 |
| 空 / 纯空白 `text` 行为变化 | 低（预期改进） | 旧：`extracted_content=""`；新：`"(no summary provided)"` + WARNING。是反退化改进，`TestDoneEmptyText` 固化 |
| force-done 切 done-only schema 受 description 变化影响 | 低 | 同一 `DoneParams` 模型，更丰富的描述在 last-step / failure-limit 时机反而更好；无结构变化 |
| 新增 `long_term_memory` 影响 Judge | 无 | Judge 经 `_serialize_history` 调 `str(r)`（[`views.py:27-37`](../../src/tree_walker/agent/views.py)），`__str__` 只渲染 error/extracted_content/is_done，**不含** `long_term_memory`；`final_result()` 仍读 `extracted_content`（不变） |
| `success` 非布尔经 `execute` 直传 | 低（既有行为） | `ActionResult.success: bool | None` 由 Pydantic 兜底；handler 不强转（与原实现一致，最小改动） |

---

## 验证方法

1. **单测三连 + 回归**（见 1.6）：单文件、覆盖率、force-done/judge/registry 相关回归、全量。
2. **动作层冒烟**（PowerShell，走 `Tools().execute`）：
    - 正常完成（单行）：
      ```
      uv run python -c "import asyncio; from unittest.mock import MagicMock; from tree_walker.tools.actions import Tools; r = asyncio.run(Tools().execute('done', {'text': 'found 42 USD', 'success': True}, MagicMock())); print('is_done=', r.is_done, 'success=', r.success, 'extracted=', r.extracted_content, 'memory=', r.long_term_memory)"
      ```
      期望：`is_done= True success= True extracted= found 42 USD memory= Task completed: True - found 42 USD`
    - 空 `text` 守卫（单行）：
      ```
      uv run python -c "import asyncio,logging; logging.basicConfig(level=logging.INFO); from unittest.mock import MagicMock; from tree_walker.tools.actions import Tools; r = asyncio.run(Tools().execute('done', {'text': ''}, MagicMock())); print('is_done=', r.is_done, 'extracted=', r.extracted_content, 'memory=', r.long_term_memory)"
      ```
      期望：一条含 `empty text` 的 WARNING，再 `is_done= True extracted= (no summary provided) memory= Task completed: True - (no summary provided)`
3. **回归对照**：回显形态与 write_file / replace_file / read_file 一致（双写 + `logger.info`）；空 `text` 因"必须终止"有意偏离 soft-miss（warn + 兜底），与 `_FALLBACK_DONE_OUTPUT` 思路一致。

---

## 验收 checklist（阶段一）

- [ ] `DoneParams.text` 加 `min_length=1` + anti-hallucination 描述；`success` 描述富化
- [ ] `ACTION_DEFINITIONS["done"]` description 更新（含"only action in the step"），`terminates_sequence` 保持 `False`
- [ ] `_action_done` 双写 `long_term_memory`（`Task completed: {success} - {text[:100]}`）+ `logger.info`
- [ ] 空 / 纯空白 / 缺失 `text` → warn + 兜底 `"(no summary provided)"`，仍 `is_done=True`
- [ ] `tests/test_done.py` 新增，4 个测试类全过（TAB 缩进）
- [ ] 覆盖率 >85%，`uv run python -m pytest tests/ -x -v` 全量回归无破
- [ ] 修订 `04_动作清单与CDP映射.md` 4.3 节 stale 行号
