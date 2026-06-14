# Phase 2: LLM决策 (_get_next_action)

> 源码位置: `src/tree_walker/agent/step.py` 第 292-451 行

## 📋 Phase 概述

`_get_next_action()` 是 Think 阶段的核心方法。将修剪后的消息发送给 LLM，获取动作决策。包含超时保护、空动作重试、参数校验重试三重保障机制。

## 🔄 主要逻辑流程

```
_get_next_action(browser_state, state_message) → model_output
  │
  ├── 1. 消息修剪与压缩                     (L298, L86-87) → [详细文档](1-消息修剪与压缩.md)
  │     ├── _trim_messages()
  │     └── maybe_compact()
  │
  ├── 2. LLM调用与超时保护                   (L305-323) → [详细文档](2-LLM调用与超时保护.md)
  │     ├── asyncio.wait_for(timeout=llm_timeout)
  │     └── _get_action_with_retry(trimmed)
  │
  ├── 3. 参数校验重试链                      (L363-477) → [详细文档](3-参数校验重试链.md)
  │     ├── _get_action_with_retry()
  │     │     ├── LLM 调用 → 空动作？ → 重试一次
  │     │     └── _validate_params_or_retry()
  │     │           ├── Pydantic 校验
  │     │           └── 失败 → 重试最多 2 次
  │     └── 最终兜底: _FALLBACK_DONE_OUTPUT
  │
  └── 4. 结果记录与日志                      (L325-360) → [详细文档](4-结果记录与日志.md)
        ├── 发射 ModelResultEvent
        ├── 追加 assistant 消息
        └── 结构化日志 (log_response)
```

## 📊 数据流图

```
messages (完整列表)
     │
     ▼
┌─────────────┐    ┌─────────────────┐
│_trim_msgs() │───▶│ _get_action_    │
│ + compact() │    │ with_retry()    │
└─────────────┘    └───────┬─────────┘
                           │
                           ▼
                   ┌──────────────┐
                   │  LLMClient   │
                   │  get_action()│
                   └──────┬───────┘
                          │
                          ▼
                   ┌──────────────┐
                   │ model_output │
                   │ {evaluation, │
                   │  memory,     │
                   │  next_goal,  │
                   │  action}     │
                   └──────────────┘
```

## 💡 总结

| 子步骤 | 源码行号 | 主要操作 | 输出 |
|--------|---------|---------|------|
| 消息修剪与压缩 | L298, L86-87 | trim + compact | trimmed messages |
| LLM调用与超时保护 | L305-323 | wait_for + retry | raw response |
| 参数校验重试链 | L363-477 | validate + retry | validated response |
| 结果记录与日志 | L325-360 | event + message + log | model_output |
