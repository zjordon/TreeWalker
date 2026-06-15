# multi_act 诊断与 prompt 优化方案

> 制定时间：2026-06-16
> 关联：`docs/multi_act实现方案.md`（Phase 1-4 实施已完成于 commit `cd5e498`）
> 性质：Phase 1-4 落地后的实测调优补充方案

---

## Context

Phase 1-4 的 multi_act 实现已完整落地，`AgentSettings.max_actions_per_step` 默认值为 5（`src/tree_walker/config.py:63`），代码路径已确认：

- `agent/agent.py:66,151,156` 把 `max_actions_per_step=5` 传给 system prompt 和 tool schema
- `tools/registry.py:107-119` 当 `max_actions > 1` 时把 `action` 字段包装为 JSON array（`maxItems=5`）
- `llm/client.py:244-262` 双兼容解析：list 走 `actions_list`，dict 包装成单元素 list
- `prompts/system_prompt.py:31-42` 已有 Multi-action Rules 章节

**但用户在 standard 模式下实测**：多字段表单（input_text × N + click）和B 站清空已存在标签这类**典型的多动作场景**，LLM 仍然一步一步输出单动作，step 数没有下降。

**根因诊断**（结合代码审查 + 用户反馈）：

| 来源 | 当前文案 | 问题 |
|---|---|---|
| `system_prompt.py:33` Rule #1 | "Most steps should output exactly 1 action. Multi-action is an optimization, not a default" | 主动告诉 LLM 默认单动作 |
| `registry.py:113-114` schema 描述 | "Most steps should contain exactly 1 action" | 工具 schema 里再强化一次 |
| `system_prompt.py:31-42` 全部规则 | 全是约束性语言（MUST be LAST / never combine / skipped） | LLM 看完只记住了「多动作很危险」 |
| 整个 prompt | 无任何 few-shot 示例 | LLM 不知道具体场景下应该怎么 chain |

**结论**：代码层面 multi_act 是正确接线的，但 prompt 文案系统性劝退 LLM 使用多动作。需要：

1. 加诊断日志先证实根因（确认 LLM 真的只输出单 dict 而非 list）
2. 软化 prompt + 加 few-shot 示例，引导 LLM 在合适场景主动 chain

---

## 实施计划

### Phase A：诊断日志（先做，验证根因）

**目标**：在不动 prompt 的前提下，先打开 INFO 级日志确认 LLM 实际输出的 tool_use payload 结构。

**改动文件**：`src/tree_walker/llm/client.py`

在 `get_action()` 解析完 `tool_input` 后、构造 `result` 前，新增 INFO 级日志：

```python
# Phase A 诊断日志：暴露 LLM 实际输出形态
raw_action = tool_input.get("action", {})
if isinstance(raw_action, list):
    logger.info(
        "multi_act: LLM emitted list with %d action(s): %s",
        len(raw_action),
        [a.get("name", "?") for a in raw_action if isinstance(a, dict)],
    )
elif isinstance(raw_action, dict):
    logger.info(
        "multi_act: LLM emitted single action (schema allows up to %d): %s",
        # 从 tool_schema 读 maxItems
        ..., raw_action.get("name", "?"),
    )
```

为避免改动 `get_action` 签名，在 client 内部从 `tool_schema["input_schema"]["properties"]["action"].get("maxItems")` 自取 max 上限。

**验证步骤**：

```powershell
$env:ZHIPU_API_KEY = "..."
uv run python examples/multi_act_demo.py form --verbose
```

查看日志：
- 如果反复出现 `LLM emitted single action` → 确认是 prompt 问题，进入 Phase B
- 如果偶尔出现 `LLM emitted list with N action(s)` 但被某个 guard 拦下 → 是 guard 问题，需另查

### Phase B：软化 prompt + few-shot 示例

**目标**：把 prompt 从「劝退多动作」改为「鼓励合适场景多动作」，并给出具体例子。

**改动文件 1**：`src/tree_walker/prompts/system_prompt.py`

**Multi-action Rules 章节重写**：

```diff
 ## Multi-action Rules

-1. **Most steps should output exactly 1 action.** Multi-action is an optimization,
-not a default — use it only when the chained actions clearly operate on the same
-stable DOM.
-2. You may chain up to **{max_actions}** actions per step. They execute in order.
-3. If any action fails or the page changes mid-sequence, remaining actions are
-skipped — you will receive the new page state on the next step.
-4. Actions marked `[terminates sequence]` (navigate / search / switch_tab /
-go_back / evaluate) MUST be the LAST action in your list — placing anything
-after them would operate on stale DOM.
-5. The `done` action must be a single action; never combine it with others.
+1. You may emit up to **{max_actions}** actions per step. They execute in order
+on the same DOM snapshot. Prefer chaining when the actions clearly target the
+same stable page.
+2. **Chain aggressively in these scenarios** (concrete examples):
+   - Filling a form: `[input_text(field1), input_text(field2), ..., click(submit)]`
+   - Clearing multiple items: `[click(remove1), click(remove2), click(remove3)]`
+   - Sequential scrolls: `[scroll, scroll, scroll]`
+   - Multi-field extraction: `[extract(field1), extract(field2)]`
+3. If any action fails or the page changes mid-sequence, remaining actions are
+   skipped — you will receive the new page state on the next step. This is safe:
+   the runtime detects page drift and stops the sequence automatically.
+4. Actions marked `[terminates sequence]` (navigate / search / switch_tab /
+   go_back / evaluate) MUST be the LAST action in your list.
+5. The `done` action must be a single action; never combine it with others.
```

**改动文件 2**：`src/tree_walker/tools/registry.py`

**schema array description 重写**：

```diff
 if max_actions > 1:
     action_field: dict[str, Any] = {
         "type": "array",
         "minItems": 1,
         "maxItems": max_actions,
         "description": (
-            "One or more actions to execute in order. Most steps should "
-            f"contain exactly 1 action. You may chain up to {max_actions} "
-            "actions when they operate on the same stable DOM (e.g. multiple "
-            "input_text fills before a click submit, or multiple scrolls)."
+            f"One or more actions to execute in order (up to {max_actions}). "
+            "Chain when targeting the same stable DOM: form filling, clearing "
+            "multiple items, sequential scrolls. The runtime stops the "
+            "sequence automatically if the page changes."
         ),
         "items": action_property,
     }
```

---

## 涉及文件清单

| 文件 | Phase | 改动类型 |
|---|---|---|
| `src/tree_walker/llm/client.py` | A | 在 `get_action()` 加 INFO 级 multi_act 诊断日志 |
| `src/tree_walker/prompts/system_prompt.py` | B | 重写 Multi-action Rules，删保守措辞 + 加 4 个 few-shot 场景 |
| `src/tree_walker/tools/registry.py` | B | 重写 array description，删 "Most steps should contain exactly 1 action" |

**不需要改**：
- `agent.py` / `step.py` / `config.py`（已正确接线）
- `client.py` 的 list/dict 双兼容解析（已正确）
- 现有 `tests/test_multi_act.py` 测试（不依赖具体 prompt 文案）

---

## 验证方法（端到端）

### 1. 单元测试不退化

```powershell
uv run python -m pytest tests/test_multi_act.py tests/test_action_registry.py -v
```

应继续 53 passed（无 prompt 文案依赖的断言）。

### 2. 诊断日志验证根因（Phase A 后）

```powershell
$env:ZHIPU_API_KEY = "..."
uv run python examples/multi_act_demo.py form --verbose
```

日志中应清晰看到每步 LLM 实际输出的动作数。

### 3. Prompt 软化后效果验证（Phase B 后）

```powershell
uv run python examples/multi_act_demo.py form --compare
```

预期 `max=1` 跑 N 步、`max=5` 跑 N-K 步（K ≥ 1），且 `max=5` 模式下日志至少出现一次 `LLM emitted list with N action(s)`。

### 4. B 站清空标签场景

用户原话提到的具体场景。建议手工跑一次：

```powershell
# 先在 Chrome 打开 B 站投稿页，已有若干标签
uv run python examples/basic_agent.py  # 任务改为「删除所有已添加的标签」
```

观察日志：清空 3 个标签应该输出 `[click, click, click]` 一步完成（而非 3 步）。

### 5. 回归保护

`max_actions_per_step=1` 模式仍能正常跑（强制单动作）。如要支持 `AGENT_MAX_ACTIONS_PER_STEP` 环境变量，需要在 `config.py:load_settings()` 的 `AgentSettings(...)` 构造里加上 `max_actions_per_step=int(os.environ.get("AGENT_MAX_ACTIONS_PER_STEP", "5"))`。

---

## 风险与备注

- **风险 1**：软化 prompt 后 LLM 可能在不该 chain 的场景也强行 chain，触发 guard #5（URL drift）丢弃后续动作。缓解：guard #5 本身是无副作用的（只是多花一次 URL 检测），不会导致错误。
- **风险 2**：glm-5.1 模型可能本身对 array schema 不够敏感，prompt 软化也未必能完全解决。Phase A 的日志就是为了验证这一点 — 如果改完 prompt 后 LLM 仍只输出单 dict，说明需要换更激进的策略（如 system prompt 加 JSON 示例片段、或在 messages 里 seed 一条 assistant 多动作响应）。
- **不做**：不引入 model-specific 的 prompt 分支（如 glm 用一套 prompt、claude 用另一套）。先用通用 prompt 软化覆盖大部分场景。
