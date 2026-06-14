# 2-LLM调用与超时保护

> 源码位置: `src/tree_walker/agent/step.py` 第 305-323 行
> LLM 封装: `src/tree_walker/llm/client.py` 第 165-260 行

## 📋 方法作用

通过 `asyncio.wait_for` 对 LLM 调用设置超时保护，调用 `_get_action_with_retry()` 完成实际的 LLM 交互。

## 🔄 主要逻辑流程

```
_get_next_action() (L305-323)
  │
  ├── 发射 ModelCallEvent (L306-312)
  │     └── {step, session_id, model_call_id, message_count}
  │
  ├── try:
  │     response = await asyncio.wait_for(
  │         self._get_action_with_retry(trimmed),   # 实际 LLM 调用链
  │         timeout=self.llm_timeout,               # 默认 120s
  │     )
  │
  └── except asyncio.TimeoutError:
        raise TimeoutError("LLM call timed out after {llm_timeout}s")
```

### _get_action_with_retry 内部流程

```
_get_action_with_retry(messages) (L363-408)
  │
  ├── 1. 第一次 LLM 调用                         (L378-382)
  │     └── llm.get_action(system_prompt, messages, tool_schema)
  │
  ├── 2. 动作有效性检查                           (L384)
  │     └── _is_valid_action(response) → action.name 非空?
  │     ├── valid → _validate_params_or_retry()    (L385)
  │     └── invalid ↓
  │
  ├── 3. 空动作重试                               (L387-404)
  │     ├── 追加澄清消息: "You forgot to return an action..."
  │     ├── 第二次 LLM 调用
  │     ├── valid → _validate_params_or_retry()
  │     └── 仍 invalid → fallback done             (L407-408)
  │
  └── 4. 兜底: return _FALLBACK_DONE_OUTPUT       (L408)
        └── {action: {name: "done", params: {text: "No action returned by LLM", success: False}}}
```

### LLMClient.get_action 内部流程

```
LLMClient.get_action(system_prompt, messages, tool_schema) (client.py L165-260)
  │
  ├── _shorten_urls_in_messages(messages)          (L173)
  ├── _filter_sensitive_in_messages(messages)      (L176-177)
  │
  ├── client.messages.create(                      (L180-187)
  │     model=self.model,
  │     tools=[tool_schema],
  │     tool_choice={"type": "tool", "name": "agent_response"},
  │   )
  │   └── RateLimitError/APIError → _try_switch_to_fallback() → 递归重试
  │
  ├── 响应解析降级链                               (L200-224)
  │   ├── 提取 tool_use block                     (L201-204)
  │   ├── 文本 JSON 解析                          (L207-216)
  │   └── 重试一次（追加提示）                      (L218-223)
  │
  ├── fallback done（空响应）                       (L225-238)
  │
  ├── URL 恢复                                    (L253-254)
  └── 敏感数据恢复                                 (L257-258)
```

## 🎯 设计亮点

1. **三重重试** — 空动作重试 + 参数校验重试 + fallback done，最大化 LLM 响应利用率
2. **超时保护** — `llm_timeout` 包裹整个重试链，防止无限等待
3. **Fallback LLM** — RateLimitError 时自动切换到备用模型（单向）
4. **响应解析降级** — tool_use → JSON → 重试 → fallback，每层都有兜底

## 🔗 与其他方法的协作

| 协作对象 | 调用位置 | 说明 |
|---------|---------|------|
| `LLMClient.get_action()` | step.py L378, L397 | 实际 LLM API 调用 |
| `_validate_params_or_retry()` | step.py L385, L404 | 参数校验 |
| `asyncio.wait_for()` | step.py L315-318 | 超时保护 |
