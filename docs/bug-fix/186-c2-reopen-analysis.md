# issue #186 重开分析（C 轮）：三个新形态全是「工具成功但语义无进展」——现有 streak 机制的正交盲区

- 日期：2026-09-18；证据：C 轮全量（2026-09-16，@ `9ffe21c` **已含 #186 全部修复**）`results/self_overestimate/C_20260916/`，任务 698/544/700/701
- 前置：`docs/bug-fix/186-behavior-discipline-analysis.md`（首轮）——FailureStreakTracker 只感知**工具失败**（result.error），done 门禁只拦截**自评带疑虑标记**的收题

## 0. 结论速览

三个新形态的共同点：**工具调用全部成功（streak 不触发）、自评无 "?"（门禁不拦）**，但任务在语义层面零进展——这是 #186 首轮机制的正交盲区，不是其失效。

| 形态 | 任务 | 日志实证 | 现有机制为何不响 |
|---|---|---|---|
| ① 不可能子句不止损 | 698 | 13:48 自评明知 `oid=null…option_21 still empty`、`This evaluate is final fix attempt`，仍在用 evaluate 运行时注入 `optiontext[value][option_21][0]='uni-size'` 硬造属性选项；基础字段（name/price/qty/color）早已可判分 | evaluate 工具成功返回字符串 → streak 清零；页面在变（表单/保存往返）→ 停滞检测不响 |
| ② 精确检索不降级 | 544 | 任务名 **Selena** Yoga Hoodie vs 目录 **Selene**（拼写不一致）；按产品名精确过滤评论网格 0 结果，34 次 read_grid/search 里反复同策略，25/29 步耗尽；16 行候选集在眼前 | read_grid 成功（total=0 也是"成功"）→ streak 不触发 |
| ③ 自洽验证 | 700/701 | `evaluate returned "by_fixed|Fixed amount discount|10"` ——agent 用 JS **注入**字段值后用同一 evaluate **回读**自证；且 `previous click call had missing params`（保存点击可能根本没执行） | 验证通道与写值通道相同 → 回读必然成功；自评无不确定标记 → 门禁不拦 |

## 1. 与首轮机制的边界

首轮解决的是「**失败可感知**」（工具连败→nudge）与「**疑虑可拦截**」（自评带 ?→打回）。本轮三形态是「**成功但无效**」：

- ① 是**语义无进展环**：目标值需系统级元数据（属性选项）而运行时 DOM 注入不可能持久化——每次"成功"的 evaluate 都在原地踏步；
- ② 是**策略不降级**：精确匹配 0 结果是明确的负信号，正确响应是放宽（子串/候选集/相似度），agent 却把 0 结果当作"再试一次"的理由；
- ③ 是**验证缺乏外部判据**：自证保存成功 ≠ 保存真的发生（更 ≠ 符合任务意图）——与首轮 task_64 的"循环验证"同源但更深一层（连数据都不缺，缺的是独立证据通道）。

## 2. 修复方向（按可机械化程度排序）

**P1-可机械化：零结果降级 nudge（形态②）**
- 检测：同一（类）查询连续 **2 次** total=0——read_grid 的 `total=0` / find_elements 的 `total=0` / search 无结果，归一化键=工具名+过滤字段；
- 注入文案（与 streak nudge 同通道）：`Exact-match query returned 0 results twice (filter: <field>=<value>). The name may be misspelled or partially different — switch to substring/partial filter, list the candidate rows, or browse the catalog and match by similarity.`；
- 实现载体：扩展 FailureStreakTracker 为通用 ProgressSignalTracker 或并列小跟踪器（record_zero_result/reset_on_nonzero），零结果**不是工具失败**，不进 `consecutive_failures`。

**P2-半机械化：不可能子句止损（形态①）**
- 弱信号可测：同目标下"evaluate 成功但随后自评/回读仍报未持久化"重复 ≥2（需读自评文本，可靠度中）——先不做机械层；
- 主战场在 **prompt/skill 层**（issue 建议原文可直接落）：Task Completion Rules 或 shop-admin SOP 增加——「目标值在系统内不存在且需创建元数据（如属性选项/枚举值）时，放弃该子句并如实上报：完成可判分的基础字段，done 描述中说明未满足项」；B 轮 698 对照组（早放弃保基础字段得 1.0）是该规则的实证。
- 与 #185 的 hint 联动：698 的 evaluate 失败已会收到平衡修复/引导——止损规则补的是"修复成功也没用"的场景。

**P2-prompt：验证外部判据（形态③）**
- Task Completion Rules 第 3 条（Verify actions actually completed）强化：「验证保存类操作必须使用**独立于写值通道**的证据：重进编辑页/重载后回读、URL/ID 变化、或服务端状态；用同一 JS 注入通道回读自证无效」；
- done 门禁不扩展（无可靠机械信号识别"自洽"）。

## 3. 不做 / 后置

| 项 | 理由 |
|---|---|
| 「手段升级」模式检测（首轮已后置） | 仍是同一判断；①的 skill 规则覆盖其主要后果 |
| 语义无进展环的机械检测（同目标 evaluate 成功×N + 自评未持久化关键词） | 自评文本信号弱、误报面大；先用 skill 规则 + 零结果检测观测一轮 |
| done 门禁扩展"自洽验证"识别 | 无可靠信号；prompt 条款已对准 |

## 4. 验收清单

- [ ] 零结果降级：单测（2 次 total=0 → nudge 文案含换策略指引；非零结果重置；不同字段不误触）；回放口径 544 应在第 2 次 0 结果后收到降级指引
- [ ] prompt/skill 规则落位（§2 P2 两条）+ system_prompt 断言
- [ ] 全量回归 + 覆盖率 >85%
- [ ] 下一轮评测复查：544 型（拼写不一致）任务策略切换；698 型（不存在属性值）任务早放弃保基础字段
