# issue #197 实施方案：畸形动作梯子韧性——形状定向澄清 + 第二次形状澄清去图 + 预算扩容 + 畸形日志直出

- 日期：2026-09-21；分支 `fix/197-vision-malformed-action-ladder`（自 master `3c31178`）
- 依据：`docs/bug-fix/197-vision-missing-name-action-analysis.md`（勘误：`?` = 缺 name 键的畸形 dict，非字面问号；双梯二连判死机制；页面聚集证据）
- 范围：本仓 4 项改动（A 日志 / B 定向澄清 / C 降级去图 / D 预算）；真机 10 任务重跑在 evals 仓（§6）；评测口径豁免另案（分析 §4.3，**先修复后重跑再谈口径**）
- 验收（issue 原文映射）："注入 '?' 响应回放：降级重试后任务继续" → §5 新增单测；"连续 2 次 '?' 不再判死" → 外梯第 3 次调用（去图重试）救回路径。**偏差声明**：issue 验收句里的"换参（温度/种子）"不采纳（分析 §4.1——client 全程不设采样参数，加升温是盲钉），以"去图降级"替代

## 0. 现象 → 改动映射

| 问题（分析文档） | 改动 | 模块 |
|---|---|---|
| `'?'` 占位符与字面问号同屏，issue 作者误诊 | **A** `describe_action_entry`：畸形条目日志形状直出（脱敏） | action_shape.py + llm/client.py |
| 澄清文案"forgot to return an action"与实际错误（缺 name 键）不匹配 → 原样再吐 | **B** `_invalid_action_feedback(response)`：按实际畸形给病灶 + 正确形状示例 | agent/step.py |
| 巨型图+DOM 压垮视觉模型工具调用，重试带同款上下文无效 | **C** `get_action(drop_images=True)`：**第二次形状澄清起去图**（复用 `_strip_image_blocks`，对齐 fallback 切换先例 client.py:414） | llm/client.py + agent/step.py |
| 二连即 fallback done，30 步预算只用 2 步 | **D** 外梯重试 1→2 次；内梯 `_PARAM_VALIDATION_MAX_RETRIES` 2→3 | agent/step.py |
| 回归 + 验收 | **E** 单测更新/新增 + 真机 10 任务重跑 | tests + evals 仓 |

设计总原则（一句话记住）：**形状澄清会升级（第二次起去图），参数反馈不升级（模型修参需要看页面）；死刑保留但触发条件升为"定向+降级后仍无效"**。

---

## 1. A：畸形动作日志形状直出

### A1 `action_shape.py` 新增 `describe_action_entry`（与 `name_of` 同位，进 `__all__`）

```python
def describe_action_entry(entry: Any) -> str:
	"""日志用动作条目描述（issue #197）——畸形形状直出，弃用占位符。

	client 的 multi_act 诊断日志原用 ``a.get("name", "?")``：缺 name 键的
	dict 与字面 ``"?"`` 名字显示成同一个 ``'?'``，#197 误诊（"模型吐问号"）
	即源于此。脱敏约定与模块头一致：只记键名/类型，不记值（畸形条目的
	params/值可能含已还原的敏感真值）。
	"""
	if isinstance(entry, dict):
		name = entry.get("name")
		if isinstance(name, str) and name:
			return name
		keys = ",".join(sorted(str(k) for k in entry)) or "empty"
		return f"<dict:{keys}>"
	return f"<non-dict:{type(entry).__name__}>"
```

### A2 `client.py:552-557` multi_act 日志替换

```python
if isinstance(raw_action, list):
	names = [describe_action_entry(a) for a in raw_action]
	logger.info(
		"multi_act: LLM emitted list with %d action(s): %s",
		len(raw_action), names,
	)
```

变化点：缺键 dict 显示 `<dict:params>`、空 dict 显示 `<dict:empty>`、null 名字显示 `<dict:name,params>`、标量条目显示 `<non-dict:str>` 且**不再被 `if isinstance(a, dict)` 滤出名单**（V 轮 `['done','?']` 中段畸形此后一眼可辨）。import 挂到既有 `from tree_walker.action_shape import ...` 行。

---

## 2. B：形状定向澄清（内外梯共用）

### B1 `step.py:72` 常量改函数

```python
def _invalid_action_feedback(response: Any) -> str:
	"""issue #197：无效动作的形状定向澄清（内外梯共用）。

	泛化文案（"forgot to return an action"）与视觉模型主犯错形态——动作
	dict 缺 name 键——不匹配：模型自认已返回动作，纠错信息为零，重试原样
	再吐（V 轮二连判死 10 任务主因）。按实际畸形给一句话病灶 + 正确形状。
	"""
	action = response.get("action") if isinstance(response, dict) else None
	if not isinstance(response, dict):
		problem = "your response was not a JSON object"
	elif not isinstance(action, dict):
		problem = "your response contained no action object"
	elif "name" not in action:
		problem = "your action object is missing the required 'name' key"
	elif not (isinstance(action.get("name"), str) and action["name"].strip()):
		problem = "your action's 'name' must be a non-empty string"
	else:
		problem = "your action object was not usable"
	return (
		f"Your previous response could not be used: {problem}. Every action "
		'MUST be an object like {"name": "click", "params": {"index": 5}}, '
		"where 'name' is one of the action names in the tool schema and "
		"'params' is an object. Respond again with the agent_response tool, "
		"including your evaluation, memory, next goal, and action."
	)
```

- 外梯（step.py:1025）与内梯 else 分支（step.py:1187）的 `_INVALID_ACTION_CLARIFICATION` 引用点全部换成本函数调用（传**当次的畸形响应**）。
- 外梯 warning 日志顺手带上形状描述（可观测性闭环）：`"LLM returned empty action (%s), retrying with clarification (%d/%d)%s", describe_action_entry(action), attempt+1, N, ", text-only (screenshot dropped)" if 降级 else ""`。

## 3. C：第二次形状澄清起去图

### C1 `client.py` `get_action` 新参

```python
async def get_action(
	self, system_prompt, messages, tool_schema, *,
	_no_action_retry_used=False, _text_retry_count=0,
	drop_images: bool = False,
) -> dict[str, Any]:
```

入口（url 缩短前）：

```python
if drop_images:
	# issue #197：梯子降级重试——巨型截图+巨型 DOM 间歇压垮视觉模型的
	# 工具调用形状（V 轮 19 行畸形 vs C 轮 2 行），文本口径同任务可通过
	# （task_108 C=1.0）。原地滤图与 _shorten_urls_in_messages 同约定；
	# 影响域=当步 state 消息（TYPE_STATE 每步重建、全局唯一）。
	_strip_image_blocks(messages)
```

三处内部递归（client.py:416/482/508）透传 `drop_images=drop_images`（416 处 fallback 切换自身已按需滤图，透传幂等无害）。

### C2 梯子侧的升级规则

- **外梯**：`drop_images=(attempt >= 1)`——第一次澄清带图（模型可能只需提示），第二次去图（同款上下文已证明失败一次）。
- **内梯**：else 分支（形状澄清）维护 `invalid_action_seen` 计数，`drop_images=(invalid_action_seen >= 2)`——第一次形状澄清带图；**参数反馈分支恒不去图**（模型修 index/url 类参数需要页面视觉，且参数连败不在 #197 证据链内）。

不新增配置开关（KISS；若真机重跑显示去图重试有副作用再谈开关）。

## 4. D：预算扩容（死刑保留）

### D1 常量（step.py:67 同位）

```python
_PARAM_VALIDATION_MAX_RETRIES = 3   # #176 P0-B 引入；#197 2→3（视觉模型形状退化二连脆死）
_INVALID_ACTION_MAX_RETRIES = 2     # #197 新增：外梯澄清重试次数（原硬编码 1 次）
```

### D2 外梯重构（step.py:992-1039，直线代码 → 循环）

```python
async def _get_action_with_retry(self, messages):
	"""Call LLM; shape-targeted clarification retries; fallback done.

	#197：重试 1→2 次（第二次去图降级）。死刑保留（调用次数有界是
	#176 的设计），触发条件升为「定向澄清 + 降级重试后仍无效」。
	"""
	response = ...（初调，现行为不变）
	if self._is_valid_action(response):
		return ...（现行为不变）
	for attempt in range(_INVALID_ACTION_MAX_RETRIES):
		degrade = attempt >= 1
		logger.warning(...)
		retry_messages = list(messages) + [{
			"role": "user",
			"content": _invalid_action_feedback(response),
		}]
		response = self._normalize_llm_response(await self.llm.get_action(
			system_prompt=self._system_prompt,
			messages=retry_messages,
			tool_schema=self._tool_schema,
			drop_images=degrade,
		))
		if self._is_valid_action(response):
			return ...（现行为不变）
	logger.warning("LLM still returned empty action after %d retries, using fallback done", _INVALID_ACTION_MAX_RETRIES)
	return _fallback_done_output()
```

### D3 内梯改动（step.py:1168 循环体内）

- 预算随 D1 常量自动 2→3；
- else 分支 feedback 换 `_invalid_action_feedback(response)` + `invalid_action_seen` 计数 + `drop_images`；
- 尾部 fallback 与 "proceeding anyway" 语义不变；
- 更新 docstring 与 `#176 P0-B` 相关注释里的预算表述（"外梯 2 + 内梯 2" → "外梯 3 + 内梯 4 次调用封顶"）。

### D4 成本上界（声明，非改动）

最坏 +2 次全上下文调用/步（外梯 +1、内梯 +1），视觉单调用 10-50s → 最坏 ≈ +100s/事故；V 轮 19 行事故/轮，可接受。死刑仍保证有界。

---

## 5. E：测试（`tests/test_unknown_action_name.py` 为主战场 + `test_llm_client.py`）

### E1 既有用例更新（预算/文案联动）

| 用例 | 改动 |
|---|---|
| `test_invalid_action_gets_clarification_before_death`（:293） | responses 补第 4 个仍畸形响应（预算 3）；:311 断言 `"forgot to return an action"` → `"missing the required 'name' key"` |
| `test_clarification_recovery_returns_valid_action`（:314） | 不变（第 3 调恢复，预算内） |
| `test_budget_bound_worst_case`（:341） | responses 补至 1+3 个；断言式已用符号，自动跟随 |
| `test_invalid_after_clarification_falls_back`（:367） | `[{}, {}]` → `[{}, {}, {}]`；await_count 2→3 |
| `test_nondict_response_clarified_not_crash`（:375） | :386 断言 → `"no action object"`（非 dict 响应的定向分支） |
| 其余（`test_invalid_then_clarified_then_valid` 等） | 不变（第一次重试即恢复，预算扩容不触碰） |

### E2 新增用例

**A 日志**（`test_step_malformed_action.py` 的 `TestNormalizeActionsList` 附近或独立类）：
1. `describe_action_entry`：合法名直出 / `{"params":...}` → `<dict:params>` / `{}` → `<dict:empty>` / `{"name": None}` → `<dict:name,params>` / `"click"` → `<non-dict:str>` / `None` → `<non-dict:NoneType>`；
2. caplog：multi_act 日志行含 `<dict:...>`（经 client 真实 get_action 路径，_make_tool_use_response 桩注入缺 name 键动作）。

**B 定向澄清**：
3. 缺 name 键响应 → 重试消息含 `"missing the required 'name' key"` 与 `'"name"'` 形状示例；
4. 非 dict 响应 → 含 `"no action object"`。

**C 降级去图**：
5. 外梯：`[缺键, 缺键, 合法]` → 第 3 调恢复；`await_args_list[1].kwargs.get("drop_images")` 为 False/缺省、`[2]` 为 True；任务不判死（结果 action.name == "click"）；
6. 外梯死刑：`[缺键, 缺键, 缺键]` → await_count == 3，fallback done（success=False），末调 drop_images=True；
7. 内梯：`[click 缺参, 缺键, 缺键, 合法]` → 第 4 调恢复不判死；第 2 次形状澄清（总第 4 调）drop_images=True，**第 1 次参数反馈（总第 2 调）drop_images 恒 False**；
8. client 层：`get_action(drop_images=True)` 收到的 messages 无 image block（test_llm_client.py 现有 fake create 桩框架；断言 create 收到的 messages 内容块全非 image，且**调用方的原 messages 列表对象共享 dict 的滤图副作用符合约定**——不断言原列表未变）。

**验收映射**：用例 5 = issue 验收句"连续 2 次 '?' 不再判死（降级重试后任务继续）"。

### E3 全量回归

`uv run python -m pytest tests/ -x -v`（覆盖率 ≥85% 红线）；重点连带：`test_step_malformed_action.py`、`test_done_uncertainty_gate.py`（外梯出口语义未动）、`test_llm_client.py`（get_action 签名向后兼容，默认值零行为变化）。

---

## 6. 真机验收（evals 仓，修复合并后）

1. V 轮口径（glm-5.3-flash + use_vision=true）定向重跑 10 任务：108/109/200/492/495/544/545/549/696/782；
2. 判据：
   - 硬判据：无任何任务再以 "No action returned by LLM" 终结（grep final_result）；
   - 过程判据：日志中 `<dict:` 畸形行仍可能出现（模型侧不可修），但其后应看到 `(2/2), text-only` 降级重试行且多数恢复；
   - 结果判据：对照 C 轮分数看恢复程度（108 C=1.0 应可恢复；仅统计参考，SR 提升量以全量重跑为准）；
3. 文本口径回归抽样：B/C 轮曾出 `?` 的任务（C task_2、B 对应任务）跑 2-3 个确认零回归。

## 7. 明确不做（分析 §4 已论证）

- 换采样参数（温度/种子）重试——盲钉；
- 单字符/问号黑名单——建立在误诊上；
- 缺 name 键 dict 改判 honest-done——把"间歇可救"降成"必死"，与 5 个救回案例相悖；
- 评测口径豁免——顺序上后置于修复+重跑（否则无法验证修复效果）；
- `name: null` 即死分支（honest-done）的放宽——V 轮零观测（显示形态是 `[None]` 不是 `['?']`），无证据不动。
