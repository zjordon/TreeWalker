# issue #194 分析：API 限流重试消耗步数预算，整任务被烧死

- 日期：2026-09-19；分析基于 B 轮轨迹 `evals/webarena/results/self_overestimate/B_20260915/task_550.log`（208 行）
- issue 原文主张："Step 16 起连续 Rate limit…16/17/18/19 步全部计步但零进展，任务以空答案耗尽…死于基建而非能力"

## 1. 现象勘误（对照真实日志）

task_550 时间线：

| 阶段 | 步 | 发生什么 |
|---|---|---|
| 开局 | 0, 1 | 限流失败 ×2（`(1/5)`/`(2/5)`），每步计一步 |
| 恢复 | 2–10 | 9 个成功步：read_grid 定位 configurable 产品 1732 → 进 Edit Configurations 向导到 Step 2，计划/memory 完整（终局 `steps=10` 即此） |
| 二次窗口 | 11–14 | 限流失败 ×4（`(1/5)`–`(4/5)`） |
| 短暂恢复 | 15 | 成功（evaluate 触发 Create New Value），连败清零 |
| 击杀串 | 16–20 | 限流失败 ×5 → `(5/5)` → `Max consecutive failures (5) reached` → run() 直接 break |

终局：`score=0.00 done=False steps=10 用时=218.8s`。对 issue 的三处修正：

1. **致死机制是 `max_failures=5` 连败上限，不是 max_steps 耗尽**。共 11 个限流失败步（0,1,11,12,13,14,16,17,18,19,20），`state.n_steps` 终值 21，评测 `--max-steps 30`——步数预算还剩 9 步，任务被连败止损**提前杀死**，且死时零交接：无 done、无最终答案、无 judge。
2. **击杀串全程只有 20 秒**（19:45:44→19:46:04）。限流窗口实测只有几十秒——step 2 在失败后 22s 成功、step 15 在失败后 12s 成功。只要每次限流失败后真退避 10–20s，任务大概率存活。
3. "16/17/18/19 步"实际是 16–20 共 5 步（第 5 步才触发上限）。

波及面：B 轮 23 任务、C 轮 31 任务中**仅此一例**（C 轮 0 命中）。低频，但击中即死（100% 致死率），且死的恰好是四次尝试中走得最远的一次——"死于基建而非能力"成立。

## 2. 完整因果链（六环）

**① SDK 层**：`Anthropic()` 构造未传 `max_retries`（`client.py:67`）→ 默认 2 次重试、亚秒级退避（日志 `Retrying in 0.43s/0.78s`）。持续 429 下 ~2-4s 内 3 连发全灭 → 抛 `RateLimitError`。

**② client 层无退避**：`get_action` 的 `except (RateLimitError, APIError)`（`client.py:258`）只有一条出路 `_try_switch_to_fallback`；评测环境没配 fallback model → 返回 False → re-raise。没有任何 client 层重试/退避。

**③ step 层把基建失败当能力失败**：`_handle_step_error` Branch 3（`step.py:1814-1833`）一律 `consecutive_failures += 1`。文案 `"Rate limit reached. Waiting before retry."`（`step.py:1968`，照搬 browser-use 的 formatter）——**Branch 3 里没有任何 sleep，下一次循环立即开始**，还附带重付一次全量 DOM 快照（~1.7-2.5s）。结果：20 秒内向已 429 的端点打了 15 个请求。"Waiting" 是谎言。

**④ 步数预算被烧**：`finally: n_steps += 1`（`step.py:259`，review2 #1 特意保证"吞异常不得跳过"）→ 每个限流失败步烧 1 步。30 步预算被烧 11 步。

**⑤ 连败止损误杀**：`run()` 顶部 `consecutive_failures >= max_failures` → break（`agent.py:313-318`）。限流 5 连发与"模型连续 incompetence"走同一条死刑路径。

**⑥ 交接缺失**：`_force_done_after_failure`（done-only schema，给模型最后一次总结机会，`step.py:728`）**实际不可达**——计数到 5 的两条路径（Branch 3 异常、`_step` 内 `step.py:228` return True）都在下一次 `_prepare_context` 之前终止了 run。死亡形态 = 空答案 + `err=—`，runner 记账与能力失败不可区分。

## 3. 计步口径（重要发现）

两个"步数"不是同一个数：

- **`state.n_steps`**（agent 内部预算，`run()` while 条件）：限流失败步**计**（终值 21）——这是被烧的预算。
- **`len(history.history)`**（runner 报表用，`runner.py:499`）：`_finalize` 只在 `model_output is not None` 时追加历史（`step.py:1619`），限流步 model_output 为 None → **不计**（终值 10）——所以评测报表的 steps 指标其实已经对限流免疫，被烧的纯粹是 agent 内部的 max_steps 预算。

## 4. 次要伤害

- 限流错误文案进 `state.last_result` → 下一步成功时模型把它当上一动作结果读（误导性上下文）。
- 每个限流重试步重付全量 DOM 快照（1.7-2.5s）+ `_prepare_context` 全流程。
- 限流等待同样在吃 runner 的 600s 任务超时（C 轮 550 用了 587.9s/600s，另一印证）。
- 限流源：provider/proxy 侧间歇 429（大上下文步与小步都中过，非纯 TPM），对 agent 而言均为外部基建。

## 5. 修复方向（三层设计空间）

| 层 | 改法 | 效果 | 短板 |
|---|---|---|---|
| L1 SDK | `Anthropic(max_retries=N)` | 一行改动 | SDK 退避短，盖不住几十秒窗口；等待照吃 `llm_timeout=120s` 和任务墙钟 |
| **L2 client** | `get_action` 捕获 429/网络错误后**真退避重试**（指数 + 尊重 retry-after，总预算封顶） | 限流根本不升级为 step 失败 → 步数/连败**零污染**，天然满足 issue 验收 | 长窗口仍会漏到上层；等待吃预算 |
| **L3 step/run** | Branch 3 拆出 **infra 失败类**：不进 `consecutive_failures`、不烧 `n_steps`、真退避；单列 **infra 预算**（连续次数上限）防 livelock；预算耗尽 graceful 终止 | 兜住 L2 漏掉的长窗口；能力止损信号不再被污染 | 改动面较大，`n_steps += 1` 的 finally 语义要重设计（livelock 防护必须由 infra 预算接管） |

选型：**L2 为主 + L3 兜底**（L2 吃掉几十秒级瞬态窗口=本次事故形态；L3 保证漏网时不误烧预算/不误触止损）。实施细节见 `194-rate-limit-infra-wait-impl-plan.md`。

## 6. 跨仓边界（不在本仓范围）

- "infra 等待不计入任务超时"：`task_timeout=600` + 硬看门狗在 evals 仓库 `smoke_test.py`——需 runner 侧读取 agent 上报的 infra 等待量再延长 soft deadline，另案。
- runner `err` 字段的 infra 分类记账（区分"死于基建"与"死于能力"）：同上，evals 仓另案。
