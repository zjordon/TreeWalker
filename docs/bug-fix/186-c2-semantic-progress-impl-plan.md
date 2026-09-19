# issue #186-c2 实施方案：零结果降级 nudge + 不可能子句止损 + 验证外部判据

- 日期：2026-09-19；分支 `fix/186-c2-semantic-progress`（自 master `f96e34a`）
- 依据：`docs/bug-fix/186-c2-reopen-analysis.md`（三新形态全是「工具成功但语义无进展」——streak/门禁的正交盲区）
- 范围：P1 零结果降级（机械层）+ P2 两条 prompt 规则；后置不做见 §6

## 0. 重开请求 → 改动映射

| 形态（C 轮任务） | 改动 | 层级 |
|---|---|---|
| ② 检索不降级（544：Selena≠Selene 精确过滤 0 结果 ×N，25/29 步耗尽） | **A** `ZeroResultStreakTracker` + 查询总计 metadata 管道 + nudge 注入 | P1 机械 |
| ① 不可能子句不止损（698：运行时造属性选项，基础字段陪葬） | **B** Task Completion Rules 第 8 条 | P2 prompt |
| ③ 自洽验证（700/701：JS 注入后同通道回读自证） | **C** 第 3 条强化（外部判据） | P2 prompt |

## 1. A：零结果降级 nudge

### A1 信号管道——三个查询类动作补 `query_total` metadata

零结果不是工具失败（soft-miss 语义既有且正确），不进 `consecutive_failures`/`FailureStreakTracker`；走 `ActionResult.metadata` 结构化旁路（`__str__` 不渲染 metadata，零 token 成本）：

| 动作 | 零结果既有回显 | metadata 补充（成功路径一律设置） |
|---|---|---|
| `read_grid` | `total=0` / `0 rows returned` | `{"query_total": <total_records 或行数>, "query_desc": "<namespace> filter/search 摘要>"}` |
| `find_elements` | `No elements found matching "..."` | `{"query_total": <total>, "query_desc": "selector=<selector>"}` |
| `search_page` | `No matches for '...'` | `{"query_total": <total+attr_total>, "query_desc": "query=<query>"}` |

- 只在 `result.error is None` 的成功路径设置（错误已由 streak 通道管）；
- `read_grid` 的 total 口径：`total_records`（三通道回落后可能为 None）→ 用 `rows_returned` 行数兜底；DOM/legacy 通道无 total 时行数即代理。

### A2 跟踪器（`loop_detector.py` 追加，与 `FailureStreakTracker` 并列）

```python
class ZeroResultStreakTracker:
    """issue #186-c2 形态②：同一精确查询连续零结果跟踪——检索降级 nudge 源。

    与 FailureStreakTracker 同构但信号相反：那边看工具失败，这里看工具
    「成功但查询空手而归」（C 轮 544：精确名过滤 0 结果 ×N 不换策略）。
    零结果是 soft-miss（正确的工具语义），不进 consecutive_failures。
    查询身份 = 动作名 + 归一化查询键（read_grid: namespace+search+filters；
    find_elements: selector；search_page: query）——改查询即重置。
    通知去抖与 peek/ack 语义同 FailureStreakTracker（review2 #5 教训）。
    """

    NUDGE_AT = 2
```

- `record(name, params, result)`：从 `result.metadata` 读 `query_total`（无该键的动作直接返回）；`>0` 清零该键 streak、`=0` 自增；键内含 `query_desc` 供文案引用；
- `peek_nudge() -> tuple[key, str] | None`（只读）+ `ack_nudge(key)`——与 streak 的 peek/ack 同构，`_prepare_context` 暂存、`_step` 在 LLM 响应取得后提交；
- 去抖：同一键只报一次（streak 冻结不重复注入）；
- 文案：
  `Exact-match query returned 0 results twice (query: <query_desc>). The name may be misspelled or partially different — switch to a substring/partial filter, list candidate rows unfiltered, or browse the catalog and match by similarity.`

### A3 接线（`agent.py` / `step.py`）

- `agent.py`：`self.zero_result_streak = ZeroResultStreakTracker()` + `self._pending_zero_result_nudge = None`；
- `step.py` `_execute_actions`：`failure_streak.record` 旁并列 `self.zero_result_streak.record(action_name, action_params, result)`；
- `_prepare_context`：`failure_streak.peek_nudge()` 旁并列 peek，非空则并入 `nudge`（同一注入通道，`_pending_zero_result_nudge` 暂存）；
- `_step`：`_ack_pending_streak_nudge()` 扩为同时 ack zero-result（或并列调用 `_ack_pending_zero_result_nudge`）；
- mixin 类型标注区同步两个新属性；`_LadderAgent` 类桩补齐（issue #186 首轮教训：绕过 `__init__` 的真实 StepPipeline 桩需补新属性）。

## 2. B+C：Task Completion Rules 两条（`system_prompt.py`）

第 3 条（既有）强化为：

```
3. **Verify actions actually completed** — did the page confirm the form was \
submitted / the file was downloaded? Verification must use a channel \
INDEPENDENT of the write: re-enter/reload the page and read back, check a \
URL/ID change, or server state. Reading back values you just injected via \
the same JS/evaluate channel proves nothing (self-certification).
```

追加第 8 条：

```
8. **Unattainable values** — if a required value does not exist in the system \
(e.g. an attribute option/enum value that was never defined) and creating it \
requires metadata administration, abandon that clause: complete the gradeable \
base fields first, and report the unmet clause honestly in done(success=false) \
describing what was accomplished and what was impossible.
```

（B 轮 698 对照组实证：早放弃该子句、保住基础字段得 1.0。）

## 3. 实施顺序与提交切分

单 PR 两批（用户授权提交时）：

1. **A**（tracker + metadata 管道 + 接线 + 单测）——独立收益，零 prompt 依赖；
2. **B+C**（两条 prompt + system_prompt 断言）。

末尾全量 `uv run python -m pytest tests/ -x -v --cov`（>85%）。

## 4. 测试与验收清单

- [ ] `ZeroResultStreakTracker` 单测：同类查询 2 次零结果 → peek 返回候选；非零重置；不同查询键互不干扰；去抖（同键 streak 冻结不重报）；peek 不消费（ack 前同候选可重取）
- [ ] metadata 管道：read_grid total=0 / find_elements No elements / search_page No matches 三路径 `query_total` 落位；错误路径不设；`read_grid` 三通道 total 缺失时行数兜底
- [ ] step 级：连续两次 read_grid 同过滤 0 结果 → 下一步 state message 含降级文案；改过滤条件 → streak 重置无 nudge；LLM 失败步不 ack → 首报重发
- [ ] `_LadderAgent` 桩补齐后既有测试全绿
- [ ] system_prompt 断言：第 3 条含 INDEPENDENT/self-certification、第 8 条 Unattainable values 存在
- [ ] 全量回归 + 覆盖率 >85%
- [ ] （下轮评测复查，非门槛）544 型任务第 2 次零结果后策略切换；698 型早放弃保基础字段

## 5. 风险与行为变更声明

1. **新 nudge 注入**：同类查询 2 次零结果时 LLM 上下文新增文案——软干预不拦动作；正确检索确实无数据的任务（验证"系统里没有 X"类问题）可能收到一次不适用提示，文案已含"may be"措辞且仅报一次；
2. metadata 字段新增：`__str__` 不渲染、零 token 影响；
3. prompt 两条新增：所有任务共享（评测口径变化即本意）。

## 6. 后置不做（沿重开分析 §3）

| 项 | 理由 |
|---|---|
| 语义无进展环机械检测（同目标 evaluate 成功×N + 自评"未持久化"） | 自评文本信号弱、误报面大；B/C 的 prompt 规则先观测一轮 |
| done 门禁扩展"自洽验证"识别 | 无可靠机械信号；C 的 prompt 条款已对准 |
| streak/零结果阈值配置化 | 先常数；有需求再提 |

## 7. 缩进与规范

- `loop_detector.py`/`agent.py`/`step.py`：4 空格（agent/* 现状）；`actions.py`：tab；`system_prompt.py`：行尾 `\` 续行（文件现状）；
- 测试反斜杠一律 `chr(92)`（issue #185-c2 教训）；新增代码缩进随所在文件；
- 不主动 commit/push（CLAUDE.md）。
