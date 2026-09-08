# Code Review（第一轮）：PR #178（fix/176-unknown-action-name）

- **日期**：2026-09-08
- **目标**：`fix/176-unknown-action-name` 分支（issue #176：未知动作名整步连坐——未注册名归一化丢弃 + 内梯澄清重试对称化）
- **范围**：PR #178 全量 diff（分支 vs master，low 档：单遍、仅 hunk 内运行时正确性、上限 4 条）
- **方法**：低档单遍
- **验证结论**：**无发现**（0 条 finding）

## 覆盖范围

- `src/tree_walker/action_shape.py` — `_drop_unregistered_actions`、`known_names` plumbing
- `src/tree_walker/agent/plan_manager.py` — 仅注释
- `src/tree_walker/agent/step.py` — `_normalize_llm_response`、`_get_action_with_retry`、`_validate_params_or_retry` P0-B 梯子
- `src/tree_walker/tools/registry.py` — 仅 description 字符串
- docs 文件与 `tests/test_unknown_action_name.py` 按 low 档规则跳过

## 检查过的可疑点（均不成立）

1. **P0-B 梯子**：budget、`param_error` 刷新、invalid-action 澄清分支、循环后两个出口（fallback done vs "proceeding anyway"）内部自洽。
2. **`name_of(response.get("action"))`** 安全替换了旧的 `response["action"]["name"]` 索引（缺 key/非 dict 不崩）。
3. **`frozenset(self.tools.registry.actions)`** 正确产出动作名 key 集合。
4. **`_drop_unregistered_actions` 全弃兜底**（keep 列表原样 → 外层澄清梯子）与**双重归一化幂等**成立，含 `actions[0]` 镜像刷新。

## 结论

Hunk 内未发现运行时正确性问题。如需覆盖跨 hunk 交互/状态机语义（连坐策略与重试预算的耦合、history 记录形态），可上 high 档再跑一轮。
