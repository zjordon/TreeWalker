# issue #186 实施方案：行为纪律两修复——连败止损 nudge / done 数据完整性门禁

- 日期：2026-09-13；依据 `docs/bug-fix/186-behavior-discipline-analysis.md`（根因与证据链见彼处）
- 目标：一个 PR 覆盖 issue 前两个请求项；第三项（自评校准审计）后置另案
- 设计原则：**软干预**——两个机制都只注入信息/多给一次重试，不硬拦截动作（与 loop detector 哲学一致；`consecutive_failures` 的终止语义为 browser-use 对齐产物，**不动**）

## 0. 请求项 → 改动映射

| issue 请求项 | 改动 | 涉及文件 |
|---|---|---|
| 1. 连败止损（同工具连败 2 次触发干预） | **F**：`FailureStreakTracker` + 执行点记录 + 分级 nudge 注入 | loop_detector.py / agent.py / step.py |
| 2. self-verify 纳入数据完整性 | **G**：done(success=True) 不确定标记门禁（一次澄清重试）+ **P**：prompt 第 7 条完整性检查 + DoneParams 描述 | step.py / views.py / system_prompt.py / models.py |
| 3.（可选）自评校准审计 | 后置不做（§7） | — |

---

## 1. F：FailureStreakTracker——失败感知的连败止损

### F1 新类（`loop_detector.py` 追加；`ActionLoopDetector` 保持成功无关不动）

```python
class FailureStreakTracker:
	"""issue #186 现象①：同动作跨步连败跟踪——失败感知的止损 nudge 源。

	与 ActionLoopDetector 互补而非替代：后者成功无关（阈值 ≥5 是为容忍翻页类
	正当重复），这里只看失败——失败是强得多的信号，阈值可以很低（2）。
	per-action-name 独立计数，该动作成功即清零；done 豁免（失败自进澄清梯）。
	通知去抖：同一阈值档只提醒一次（streak 冻结时不再每步重复注入），达
	更高档位或 streak 增长后再次提醒。
	"""

	NUDGE_AT = 2
	ESCALATE_AT = 4
	_EXEMPT = frozenset({"done"})

	def __init__(self) -> None:
		self._streaks: dict[str, int] = {}
		self._notified_at: dict[str, int] = {}

	def record(self, name: str, failed: bool) -> None:
		if name in self._EXEMPT:
			return
		if failed:
			self._streaks[name] = self._streaks.get(name, 0) + 1
		else:
			self._streaks.pop(name, None)
			self._notified_at.pop(name, None)

	def nudge(self) -> str | None:
		if not self._streaks:
			return None
		name, streak = max(self._streaks.items(), key=lambda kv: kv[1])
		if streak < self.NUDGE_AT:
			return None
		notified = self._notified_at.get(name, 0)
		# 去抖：每档只报一次——2 首报（notified<2），3 不重报（notified>=2 且
		# streak<4），4 升级报（notified<4 且 streak>=4），5+ 不重报（notified>=4）
		if notified >= self.ESCALATE_AT:
			return None
		if notified >= self.NUDGE_AT and streak < self.ESCALATE_AT:
			return None
		self._notified_at[name] = streak
		if streak >= self.ESCALATE_AT:
			return (f"⚠️ You have failed '{name}' {streak} times in a row. "
				"Strongly consider declaring this sub-goal unreachable: complete "
				"or verify the task's actual deliverable, or finish with an honest "
				"partial result (done with success=false, describing what was "
				"accomplished and what is missing).")
		return (f"⚠️ You have failed '{name}' {streak} times in a row. Stop "
			"retrying or inventing workarounds for this approach. Re-read the "
			"original task and switch to a different approach that directly "
			"advances the task's final goal — also ask whether the failing "
			"sub-goal is required by the task at all.")
```

### F2 实例化（`agent.py`，`self.loop_detector = ActionLoopDetector()` 旁）

```python
		# issue #186 现象①：失败感知连败跟踪（与 loop_detector 互补，见其类注释）
		self.failure_streak = FailureStreakTracker()
```

`step.py` mixin 类型标注区（L144 `loop_detector: ActionLoopDetector` 旁）同步加 `failure_streak: FailureStreakTracker`。

### F3 记录点（`step.py` `_execute_actions`，`results.append(result)` 之后）

```python
			# issue #186 现象①：连败跟踪——多动作步内的失败也计（task_374 形态：
			# [close_tab OK, screenshot 失败] 步，consecutive_failures 不计这种）；
			# 该动作成功即清零；done 豁免。跳过的动作（序列截断）不执行不记录。
			self.failure_streak.record(action_name, bool(result.error))
```

注意挂在 `results.append` 后、Guard #2 `break` 前——每条**实际执行**的动作恰记录一次。

### F4 nudge 注入（`step.py` `_prepare_context`，loop nudge 同位 L316-323）

```python
		nudge = self.loop_detector.get_nudge_message()
		streak_nudge = self.failure_streak.nudge()
		if streak_nudge:
			logger.info("Failure-streak nudge injected (%s)", streak_nudge[:80])
			nudge = "\n\n".join(x for x in (nudge, streak_nudge) if x)
```

state message 每步重建 → nudge 天然自清，无需额外状态。

### F 验收

- 单测（`tests/test_loop_detector.py` 追加 `TestFailureStreakTracker`）：跨步累计、多动作步失败计入、成功清零（含 notified 状态复位）、done 豁免、per-name 独立、nudge 分档（<2 None / 2 首报 / 3 不重报 / 4 升级 / 冻结不重复注入）；
- step 级：fake tools 连续两次 screenshot 抛错 → 下一步 state message 含止损文案；第三次成功后 nudge 消失；
- 回放口径（非门槛）：task_374 重放应在第 2 次 screenshot 失败后收到 nudge。

---

## 2. G：done(success=True) 数据完整性门禁

### G1 标记扫描纯函数（`step.py` 模块级）

```python
_URL_RE = re.compile(r"https?://\S+")
# 词尾 ?：贴数字/标识符的悬而未决值（"Emma Davis=1?" "=2?" "Davis?"）；
# token 不含空格 → 整句疑问（"Is this right?"）也会命中——自评里的疑问句本身
# 就是未消解状态，可接受；(?<![\w?]) 排除 "???" 连问与 token 中段
_TOKEN_Q_RE = re.compile(r"(?<![\w?])[\w.\-=]{1,64}\?(?=\s|$)")
_UNCERTAIN_KEYWORDS = (
	"unknown", "unverified", "unread", "gap", "missing", "pending",
	"partial", "uncertain", "unclear", "not sure", "not verified",
	"not confirmed", "needs verification", "to verify", "to check",
)

def scan_uncertainty_markers(*texts: str) -> list[str]:
	"""issue #186 现象②：扫 evaluation/memory/done 文本中未消解的不确定标记。

	先剥 URL（query string 的 ? 不是疑虑）；关键词按整词匹配（\\b）。返回命中
	样本（去重、保序、封顶 3 个）供反馈消息引用。
	"""
```

实现要点：`_TOKEN_Q_RE` 无捕获组（findall 取全匹配）；关键词 `re.search(rf"\b{re.escape(kw)}\b", low)`；命中列表去重封顶 3。

### G2 门禁方法（`step.py`，与 `_validate_params_or_retry` 梯子同构）

```python
	_DONE_GATE_MAX_PER_RUN = 2

	async def _gate_uncertain_success_done(
		self, response: dict[str, Any], messages: list[dict[str, Any]],
	) -> dict[str, Any]:
		"""issue #186 现象②：done(success=True) 携带未消解不确定标记时，给一次
		「补验证或诚实降级」的步内重试。

		- 只拦 success 为 True 的 done（缺省=True 一并拦）；success=False 的诚实
		  收题必须畅通；honest-done（带外标记）跳过；
		- 每 run 封顶 2 次（AgentState.done_gate_uses 计数），防「每步重发带
		  标记的 done」死循环；单次调用只重试一次；
		- 重试响应过一次 `_validate_action_params`（纯形状校验，不再进 LLM 梯）；
		  无效或无动作则放行原响应。
		"""
```

触发消息（引用命中标记，让 agent 看到自己写的疑虑）：

```
Your own evaluation/memory contains unresolved uncertainty (matched: `Emma Davis=1?`,
`unread`). You are about to call done(success=true) on incomplete data. Either
(a) verify the missing pieces with tools first, or (b) call done(success=false)
describing exactly what was accomplished and what remains unverified. Do NOT
restate done(success=true) while the same markers remain unresolved.
```

### G3 状态字段（`views.py` `AgentState` 追加）

```python
	# issue #186 现象②：done(success=True) 不确定标记门禁的累计触发次数（每 run
	# 封顶 2，防重发循环；0 = 未触发）
	done_gate_uses: int = 0
```

### G4 挂载点（`step.py` `_get_action_with_retry` 两个有效动作返回点 L946/L961）

```python
		if self._is_valid_action(response):
			return await self._gate_uncertain_success_done(
				await self._validate_params_or_retry(response, messages), messages)
```

参数校验先行（形状合法的 done 才进语义门禁）；门禁重试的响应经 `_validate_action_params` 一次形状校验后直接返回。

---

## 3. P：prompt 两处

### P1 system_prompt.py「Before calling done(success=true)」清单追加第 7 条（L63-72）

```
7. **Data completeness** — answers and counts must be derived from COMPLETE data.
   If your evaluation or memory still contains unresolved markers (a value with
   `?`, an unread gap, a partial tally, an unverified assumption), resolve them
   with tools first, or call done(success=false) stating exactly what is missing.
   Verifying only a shortlist derived from partial data does NOT establish
   completeness.
```

### P2 `DoneParams.success` 描述（`models.py`）追加一句

```
... If any part of your data is incomplete or still marked uncertain
(a value with `?`, an unread gap, a partial tally), verify it first or set
success=False.
```

---

## 4. 实施顺序与提交切分

单 PR 三批（用户授权提交时）：

1. **F**（tracker + 接线 + 单测/step 级测试）——独立收益最大，零 prompt 依赖；
2. **G**（扫描函数 + 门禁 + 状态字段 + 测试）；
3. **P**（两处 prompt 文案 + system_prompt 测试断言）——放最后，F/G 测试不依赖它。

每批后跑对应测试文件，末尾全量 `uv run python -m pytest tests/ -x -v` + 覆盖率 >85%。

## 5. 测试与验收清单

- [ ] `tests/test_loop_detector.py`：`TestFailureStreakTracker` 全组（§1.F 验收列出的 7 类用例）
- [ ] step 级连败 nudge：连续失败 2 次 → 下一 state message 含文案；第 3 次成功 → nudge 消失（沿 `test_step_malformed_action.py` 等 fake 管线风格）
- [ ] `scan_uncertainty_markers` 纯函数：词尾?（`=1?`/`Davis?`）、关键词整词、URL 剥离（`http://x/?a=1?` 不误报）、干净文本零命中、封顶去重
- [ ] 门禁 step 级：done(success=True)+memory 带 `Emma Davis=1?` → LLM 重试一次、反馈含命中标记；重试响应干净 → 用重试响应；重试仍脏 → 放行（单次性）；`success=False` 不触发；honest-done 跳过；每 run 2 次封顶后不再拦
- [ ] `tests/test_system_prompt.py`：第 7 条存在性断言
- [ ] 全量回归 + 覆盖率 >85%
- [ ] 回放口径（非门槛）：task_374 第 2 次 screenshot 失败步后 nudge 注入；task_64 旧轨迹 step 19 的 done 被打回一次
- [ ] 真机：B 轮任务子集复跑无回归（nudge 不改变成功任务轨迹）

## 6. 风险与行为变更声明（评测口径）

1. **新增 nudge 注入**（连败 ≥2 时）：LLM 上下文新增文案——这是本修复的目的，非副作用；成功任务通常不触发（B 轮 11/46 曾触发，其中 10 个的根因已被 #185 消除，预期触发率显著降低）；
2. **done 可能多一次 LLM 调用**：仅在 success=True 且自评带标记时，每 run 封顶 2 次；
3. **误报面**：词尾 `?` 会命中自评中的整句疑问、`partial` 命中 "partial success"——两者语义上确属未消解状态，命中合理；反馈消息明示出路（验证或降级），非硬拦；
4. 无 default-off 开关需求（软干预；若评测需要口径隔离，可用环境变量 `AGENT_DONE_GATE=0` 关门禁——实现时顺手加，默认开）。

## 7. 后置不做

| 项 | 理由 |
|---|---|
| 自评校准整体审计（请求项 3，反向案例 183/201/247/491） | 涉及 Judge 语义与任务完成定义对齐，独立成案 |
| "手段升级"模式检测（失败→转 evaluate 发明 workaround） | 语义过糊误报率高；streak nudge 文案已覆盖其行为后果 |
| streak 阈值配置化（settings/env） | 阈值 2/4 有明确证据支撑，先常数；需要时再提 |
| `consecutive_failures` 语义修订（多动作步不计/重置） | browser-use 对齐产物，动它影响终止语义——失败感知已由 F 独立承接 |

## 8. 缩进与规范

- `loop_detector.py` / `step.py` / `agent.py` / `views.py` / `system_prompt.py` / `models.py`：**tab**；
- `tests/test_loop_detector.py` 以打开实测为准（memory：缩进 per-file 混用）；新测试文件随所在既有文件风格；
- 不主动 commit/push（CLAUDE.md）；分析报告与本方案随分支走。
