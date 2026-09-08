# issue #176 修复方案：未知动作名整步连坐——响应字段误发为动作 / 兄弟动作陪葬 / 校验梯子不对称

> 关联：issue #176（触发样本来自 PR #175/177 视觉验收会话第二轮，2026-09-07）。
> 姊妹问题：#173 / PR #174（畸形 **params** 归一化 + `_finalize` 兜底，8 轮 review）——
> 本 issue 是同一模型行为家族的另一变体：**动作名层**的畸形（well-formed 但未注册）。

---

## Context（为什么修）

### 死亡链（2026-09-07 实测日志，已固化进 issue #176）

```
multi_act: LLM emitted list with 3 action(s): ['plan_update', 'click', 'wait']
Invalid params for 'plan_update': Unknown action 'plan_update' — retrying (1/2)
multi_act: LLM emitted single action '?' (schema allows up to 5)
LLM returned empty action during param validation retry
→ fallback done(success=False)「No action returned by LLM」任务在 Step 10 提前终结
```

**关键读图**：第三行的 `'?'` 不是字面名——它是 `client.py` 打日志时 `a.get("name", "?")`
的**缺省值**：重试响应的动作 dict **没有 name 键**。即重试非但没修正，还退化成了
无名字动作。

### 三个根因环（逐环核实过源码）

1. **响应字段被误发为动作名**：`plan_update` / `current_plan_item` 是
   `agent_response` 的 **schema 字段**（`registry.py:200-209`，`enable_planning=True`
   时注入），`plan_manager.py:30` 的提示词又写「A) plan_update: replace the entire
   plan」——模型偶发把它当动作名塞进 `actions[]`。动作本身 shape 完全合法
   （`{"name": "plan_update", "params": {...}}`），`normalize_actions_list`
   （`action_shape.py:79`）按 #173 策略表视其为**正常命名动作**（#173 只处理
   shape 畸形与 name 类型畸形，不查注册表）。
2. **批量连坐**：同批 `['plan_update', 'click', 'wait']` 的 `action` 镜像 =
   `actions[0]`（`client.py:428`）。头部未知名使 `_validate_action_params`
   （`step.py:982`）对**整步**报 `Unknown action` 进重试梯子——2 个合法动作陪葬。
   全量 diff 视角：本可执行 `click`+`wait` 的一步被整步废弃。
3. **梯子不对称（真正的死因）**：外梯（`step.py:913-937`）对"无效动作"的处理是
   **澄清重试**（附提示让模型重发）；而参数校验内梯（`step.py:968-970`）对重试响应
   的"无效动作"是 **立即 fallback done**（`if not self._is_valid_action(response):
   return _fallback_done_output()`）——一次都没有二次机会，`_PARAM_VALIDATION_MAX_RETRIES=2`
   只用掉 1 次就终止任务。

### 与 #173 既有决策的边界（不能踩的线）

- **review7 #1**：多元素列表的畸形条目"**原样保留**"，头部畸形 → 镜像过不了
  `_is_valid_action` → 走澄清重试（模型可重发整个列表，不硬终止丢有效尾部）。
- **review6 #2**：中段畸形在**执行时**得到可见 `Unknown action` 错误（不合成 done
  造成静默截断/重放分叉）。
- **本修复针对的形态与上述正交**：#173 处理的是 **shape 畸形**（非 dict / name 类型
  非法 / params 非 dict），本修复处理的是 **shape 合法但名字未注册**（模型幻觉/
  字段混淆）。丢弃未注册名**不违反**"原样保留"决策——那条决策保护的是 shape
  畸形（消费方 `isinstance` 跳过 / 执行时可见错误），未注册名在执行路径的归宿
  本来就是 `Unknown action` 失败计 failure（`step.py:1006-1013` 的既有语义），
  丢弃只是把这个必然失败提前并免掉连坐。

---

## 修复设计（三层，P0 → P2）

### P0-A：未注册名归一化丢弃——「丢名留批」+ 镜像刷新（核心修复）

**归属**：`action_shape.py`（叶子模块，靠参数注入名字集合，不引入 registry 依赖）。

1. `normalize_actions_list` / `normalize_model_output` 增可选参
   `known_names: Collection[str] | None = None`：
   - `known_names is None` → 行为与现在**完全一致**（client choke point
     `client.py:422` 无 registry，照旧不传；幂等归一化不受影响）；
   - 传入时：live 上下文下，shape 合法但 `name_of(a) not in known_names` 的条目
     **从列表移除** + WARNING（记被专名字与位置，可归因）；
   - **响应字段名特判**：`plan_update` / `current_plan_item`（以及 thinking 模式
     下的 `thinking`）命中时降为 INFO——良性混淆（其语义本就在响应体字段里，
     `plan_manager.py:34` 照常消费，模型意图不丢）；
   - **丢光的兜底**：若全部条目被丢，**恢复原列表原样返回**（不合成动作、不
     返回空列表）——落回既有外梯澄清重试路径，与 review7 #1 的"模型可重发"
     语义一致；绝不在归一化层合成 done（review6 #2）。
2. **镜像刷新**：`normalize_model_output` 既有的"刷新镜像"步骤（`action =
   actions[0]`）在丢弃后自然指向第一个幸存动作——头部 `plan_update` 被丢后
   镜像变为 `click`，`_validate_action_params` 直接通过，本步执行 `click`+`wait`，
   连坐消失。
3. **step 侧接线**：`_get_next_action` 的管线入口归一化调用处（`step.py` 的
   `normalize_model_output(response)`，review7 #6 位置）传入
   `known_names=frozenset(self.tools.registry.actions)`。注意 `registry.actions`
   是全量注册表（page 过滤只影响 schema 暴露不影响按名查找，`step.py:1006`
   既有语义），`_validate_action_params` 的 `Unknown action` 检查保留作背带
   （注入/旁路 LLM 绕过归一化时兜底）。

### P0-B：校验内梯对称化——无效动作也给澄清重试，不再一次死刑

**归属**：`step.py` `_validate_params_or_retry`（`:939-980`）。

内梯 `:968-970` 对重试响应"无效动作"的 `return _fallback_done_output()` 改为与
外梯同款处理：附**澄清消息**（复用 `:918-925` 的措辞）再重试一次，仍无效才
fallback done。语义对齐"外梯无效动作=可澄清重试"，死因链第 3 环（`'?'` 无名
动作一次死刑）闭合。**注意防递归**：澄清重试只在内梯自己的 attempt 预算内
（`_PARAM_VALIDATION_MAX_RETRIES=2` 不变，一次 Invalid-params + 一次
Invalid-action 共用预算），总 LLM 调用次数有界（外梯 2 + 内梯 2 + 内梯内澄清 1
封顶，仍受 `llm_timeout` 总闸约束）。

### P1-C：schema / prompt 澄清——降低发生率（治本但非治all）

1. `registry.py:204` `plan_update` 字段 description 追加一句：
   `"This is a RESPONSE FIELD — never use it as an action name in action/actions."`
   （`current_plan_item` 同理）。
2. `plan_manager.py:30` 的「A) plan_update: replace the entire plan」补
   `(response field, not an action)`。

发生率下降 = P0 路径的触发频度下降，两者互补不互替。

### 不做（明确出界）

- **不**在归一化层做"近似名纠错"（`plan_update`→? / 编辑距离匹配）——静默改写
  模型意图，风险大于收益；
- **不**改 `history` 上下文策略（重放侧未注册名本就由消费方跳过，无连坐问题）；
- **不**动 review7 定下的 shape 畸形"原样保留"策略（见 Context 边界节）。

---

## 工程约束（实施时务必遵守）

- Windows + PowerShell；包用 `uv`，跑测试 `uv run python -m pytest tests/ -x -v`。
- **缩进按文件**：`action_shape.py` / `agent/*.py` / `llm/client.py` = 4 空格；
  `tests/` 大多 TAB（新文件对齐所在Sibling文件）。
- 改完跑相关单测 + 全量回归；覆盖率目标 >85%。
- 不主动 `git commit` / `git push`。

---

## 文件清单与锚点

| 文件 | 改动 | 锚点 |
|---|---|---|
| `src/tree_walker/action_shape.py` | `known_names` 参数 + 未注册名丢弃 + 响应字段名特判 + 丢光兜底 | `normalize_actions_list`（`:79`）/ `normalize_model_output`（`:195`） |
| `src/tree_walker/agent/step.py` | 管线入口传 `known_names`；内梯澄清重试对称化 | `normalize_model_output(response)` 调用处 / `_validate_params_or_retry:968-970` |
| `src/tree_walker/tools/registry.py` | `plan_update`/`current_plan_item` description 澄清 | `:200-209` |
| `src/tree_walker/agent/plan_manager.py` | 提示词补「response field, not an action」 | `:30` |
| `tests/`（`test_action_shape.py` 或新文件） | 见测试计划 | — |

---

## 测试计划

**normalize 层（known_names 注入）**：
- 头部 `plan_update` + `[click, wait]` 合法尾 → 丢头留尾，镜像刷新为 `click`；
- `plan_update` 命中响应字段特判（INFO 非 WARNING）；
- 未注册幻觉名（如 `scroll_to_moon`）→ 丢弃 + WARNING；
- **全部未知** → 列表原样保留（落外梯澄清，不合成、不空列表）；
- `known_names=None` → 与现行为逐例一致（回归 #173 全部用例不动）；
- 幂等：二次归一化不重复丢/不变形。

**step 层（梯子）**：
- 响应镜像为未知名但 actions 含合法批 → `_get_next_action` 不进重试梯、
  直接执行合法批（mock get_action 一次调用即断言）；
- 内梯：首次 Invalid-params → 重试响应无 name 动作 → **澄清重试**（第二次
  重试）→ 仍无效 → 才 fallback done（断言 get_action 调用次数）；
- 内梯：重试响应无 name 动作 → 澄清后恢复合法 → 正常返回（不死刑）；
- 预算有界：内梯总调用次数不超 `_PARAM_VALIDATION_MAX_RETRIES` + 1。

**回归**：`test_action_shape` / `test_llm_client`（#173 系列）/ `test_vision_channel`
全绿；全量套件绿。

---

## 风险与回归点

| 风险 | 缓解 |
|---|---|
| 丢弃未注册名静默吞掉模型真实意图（幻觉名可能"接近"某个真动作） | WARNING 记名可归因；意图的下一次表达窗口仍在（下步 state 消息带 Previous Results）；不做近似纠错是显式决策 |
| known_names 与 page 过滤的语义混淆 | 名字集合恒为全量注册表（与 `_validate_action_params:1006` 同源），page 过滤只影响 schema 暴露——方案已写死，测试覆盖 |
| 内梯澄清重试放大 LLM 调用次数 | 共用 attempt 预算 + `llm_timeout` 总闸；测试断言调用次数上界 |
| `plan_update` 误发但响应体**未**带该字段（模型只发了动作名） | 丢弃即丢意图——接受：该"动作"本不可执行，且 INFO 日志可查；发生率由 P1-C 压低 |

---

## 验收 checklist

- [ ] 死亡链复现用例（`['plan_update','click','wait']` 批 + 重试吐无名动作）走通：
      本步执行 `click`+`wait`，任务不提前终结；
- [ ] `known_names=None` 时 #173 全部既有用例零变化；
- [ ] 内梯无效动作两击才死刑（调用次数断言）；
- [ ] registry/plan_manager 澄清文案生效（schema dump 断言）；
- [ ] 全量测试绿 + 相关模块覆盖 >85%；
- [ ] 真机（可选）：`uv run python examples/upload_file_vision.py` 复跑采样，
      观察原死亡链位置是否改为 WARNING+继续。
