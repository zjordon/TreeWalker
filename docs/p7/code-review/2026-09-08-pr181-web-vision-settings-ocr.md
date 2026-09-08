# Code Review（OCR 第一轮）：PR #181（feat/179-web-vision-settings）

- **日期**：2026-09-08
- **工具**：OpenCodeReview（OCR）`review --audience agent --from master --to HEAD`，29m14s，~340 万 input token
- **目标**：`feat/179-web-vision-settings`（issue #179：设置面补视觉通道与任务级 skill 参数，含 size 校验）
- **范围**：7 文件、5 条意见（全部 low 严重度）
- **处置**：5 条全部核实成立并当场修复（skill 流程 Step 3 自主修复），0 条按误报丢弃

## Findings 与处置

### 1. 前端 `SettingFieldDTO.type` union 缺 `"size"` — 已修

- **位置**：`web_ui/src/api.ts:228`（后端 `server.py:333` 新增 type="size" 字段）
- **问题**：镜像契约过期——运行时安全（SettingsShell 落默认文本输入，恰是 WxH 字符串的正确控件），但未来任何对 `f.type` 的穷尽分支会静默漏处理。
- **修复**：union 加 `| "size"`（纯加宽，无穷尽消费方，tsc 语义安全；web_ui 未装 node_modules 未跑 tsc，以 grep 穷尽性核查替代）。

### 2. 测试泄漏 `AGENT_USE_VISION=true` — 已修

- **位置**：`tests/test_web_server.py`（test_settings_vision_affects_load_settings）
- **问题**：`/settings/set` handler 直接写 `os.environ`，monkeypatch 只回收自己 set/delenv 的键——非默认值留在环境里，同会话后续测试 `load_settings()` 不 delenv 就静默开视觉（评测红线 flag）。
- **修复**：测试末尾显式 `os.environ.pop("AGENT_USE_VISION"/"AGENT_LLM_SCREENSHOT_SIZE", None)` + 注释说明缘由。

### 3. `_is_valid_action` 非 dict 响应 AttributeError（docstring 承诺不存在）— 已修

- **位置**：`src/tree_walker/agent/step.py:1079`（`_normalize_llm_response` docstring step.py:912 承诺非 dict 透传后"判假进澄清梯"）
- **问题**：`_is_valid_action` 无 isinstance 守卫，`response.get("action")` 对注入/旁路 LLM 返回的 list/str/None 抛 AttributeError——声称的优雅降级不存在，改动前后同样崩。
- **修复**：入口加 `if not isinstance(response, dict): return False`，兑现透传承诺；新增 `test_nondict_response_clarified_not_crash`（外梯澄清 → 恢复合法，断言澄清措辞与结果）。

### 4. 全弃兜底"落外梯"docstring 不实 — 已修

- **位置**：`src/tree_walker/action_shape.py:96`、`tests/test_unknown_action_name.py:12,209`
- **问题**：`_drop_unregistered_actions` 丢光时保留的列表每个条目必有合法字符串名 → 镜像过 `_is_valid_action` → 路由进 `_validate_params_or_retry`（内梯），模型收到 "Unknown action" 参数反馈——非外梯 `_INVALID_ACTION_CLARIFICATION`。PR #178 自己的 step 级测试（`test_all_unknown_batch_kept_for_clarification_retry` 断言首次重试含 "Unknown action"）记录的才是实际流程。
- **修复**：三处措辞"外梯澄清重试"→"内梯（参数校验重试）的『Unknown action』反馈重试"，与本 PR 370-371 行已有正确注释对齐。
- **注**：PR #178 round1 归档沿用过同样错误表述（"→ 外层澄清梯子"），以本归档更正为准。

### 5. 死断言 — 已修（简化处置）

- **位置**：`tests/test_unknown_action_name.py`（test_budget_bound_worst_case）
- **问题**：`inner_calls <= N` 逻辑蕴含 `inner_calls <= N+1`，第二行断言永不独立失败。
- **修复**：删除死断言行 + 删注释"（更不超 +1）"。（OCR 建议的替换 `await_count <= 1+N` 与上行逻辑等价，同样是死重写，故取直接删除。）

## 验证

`uv run python -m pytest tests/test_unknown_action_name.py tests/test_web_server.py tests/test_step_malformed_action.py -q` → **156 passed, 1 skipped**（skip 预存在）。

改动清单：`web_ui/src/api.ts`、`tests/test_web_server.py`、`src/tree_walker/agent/step.py`、`src/tree_walker/action_shape.py`、`tests/test_unknown_action_name.py`（含新增 1 测试）。
