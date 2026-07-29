# 蒸馏版 skill A/B 实测（N=5，2026-07-28）

> **TL;DR**：N=5 **反转了 N=3 的步数结论**。treatment 步数从 N=3 的 +37%（比 baseline 多）翻转到 N=5 的 −24%（比 baseline 少），两次方向相反、互相抵消 → **A/B 步数差是随机噪声**。蒸馏版 skill 的静态质量问题（缺 `upload_file` 直注等）确实存在，但**与 A/B 步数差不相关**——A/B 步数不是检测 skill 内容质量的可靠指标。

> 关联：
> - [distilled-skill-quality-2026-07-28.md](distilled-skill-quality-2026-07-28.md)（静态质量分析，其"步数归因"结论被本报告校正；静态对比 Q1-Q7 仍有效）
> - [bilibili-ab-test-2026-07-28.md](bilibili-ab-test-2026-07-28.md)（手写版 N=3，判据达标）
>
> 数据：`ab_result.json`（N=5 蒸馏）、`ab_run_n5_distilled.log`、回放 `ab_baseline_{1..5}.json` + `ab_treatment_{1..5}.json`

---

## 一、N=5 数据

### baseline（skill off）

| 轮 | steps | 耗时 | 封面 | success |
|---|---|---|---|---|
| baseline#1 | 37 | 497s | ✅ | true |
| baseline#2 | 32 | 410s | ❌（"React 组件..."） | true\* |
| baseline#3 | 21 | 239s | ❌ | **false** |
| baseline#4 | 20 | 259s | ✅ | true |
| baseline#5 | 24 | 347s | ❌（"尝试多次未成功"） | true\* |

\* 封面失败但 judge 判 success=true（口径宽松）。

### treatment（蒸馏 skill on，每轮都 `skill loaded` 确认注入）

| 轮 | steps | 耗时 | 封面 | success |
|---|---|---|---|---|
| treatment#1 | **9** | 221s | — | **false（done=false，异常早退）** |
| treatment#2 | 18 | **1827s（30 分钟，异常慢）** | ✅ | true |
| treatment#3 | 24 | 462s | ？（final 未提封面） | true |
| treatment#4 | 24 | 353s | ✅ | true |
| treatment#5 | 27 | 395s | ✅ | true |

### 汇总

| 指标 | baseline | treatment | 差异 |
|---|---|---|---|
| 成功率（judge） | 80% (4/5) | 80% (4/5) | +0pp |
| 平均步数 | 26.8 | **20.4** | **−24%** |
| 平均耗时 | 350.5s | 651.5s† | — |
| 封面成功率（客观） | 2/5 = 40% | 成功轮基本 ✅ | — |

† treatment 耗时被 t#2 的 1827s 拉高；排除 t#2 后 treatment 平均约 330s，与 baseline 相当。

**判据（方案 §九：成功率 ≥ +20pp 或 步数 ≤ −30%）**：成功率 +0pp ✗；步数 −24% ✗（接近未达）。**双未达。**

## 二、关键反转（N=3 vs N=5）

| 指标 | N=3 蒸馏 | N=5 蒸馏 | 波动幅度 |
|---|---|---|---|
| baseline 步数 | 22.3 | 26.8 | ±4.5 |
| **treatment 步数** | **30.7（+37%）** | **20.4（−24%）** | **±10.3** |
| 成功率 | 100% / 100% | 80% / 80% | — |

**treatment 步数从 N=3 的"比 baseline多 37%"翻转到 N=5 的"比 baseline 少 24%"。** 两次方向相反。如果"蒸馏版缺 upload_file 直注"真会稳定导致多走步，N=5 不该反转。所以 **N=3 那个"+6 步"是抽样噪声，不是质量问题确定性的效应**。

## 三、异常信号（treatment 不稳定）

N=5 的 treatment 5 轮里出现 **2 个异常**，baseline 5 轮零异常：

- **treatment#1**：只跑 9 步、`done=false`、`final_result` 为空——异常早退（可能 LLM 决策错误或卡住后放弃）。回放在 `ab_treatment_1.json`。
- **treatment#2**：耗时 **1827s（30 分钟）**——异常慢（可能某步反复重试）。

baseline 5 轮都 `done=true`，耗时 238–497s（稳定）。treatment 出现 2/5 异常 vs baseline 0/5，**可能**暗示 skill 引入了某种不稳定（LLM 偶尔被 skill 内容带偏），但 N=5 不足以确认，也可能纯巧合。需读 `ab_treatment_1.json` 复盘 t#1 才能判断是否 skill 误导。

## 四、最终判断：质量问题 vs 随机（两层面分开）

**两个层面都成立，但互不蕴含：**

### 层面 A：A/B 步数差 → 随机
N=3 +37% 与 N=5 −24% 抵消，treatment 步数无稳定方向。在整体步数这个动态指标上，**检测不出蒸馏版的质量问题**——被 LLM 行为的 run-to-run 随机性（有时自己摸索到 `upload_file` 直注，有时走弯路）淹没。

### 层面 B：skill 内容质量 → 蒸馏版确实有问题（静态事实）
[distilled-skill-quality 报告](distilled-skill-quality-2026-07-28.md) 的静态对比确认：蒸馏版缺 `upload_file` 直注、visible=True 防 decoy、标题框时序这 **3/5 关键省步指导**，视频 file input selector 还三文件内部矛盾。**这是真的**，只是 A/B 步数反映不出来。

**结论**：你最初问的"质量问题还是随机"——**两者都有，但不相关**。静态对比能确认质量问题存在；A/B 步数差是随机的，不能用来佐证或推翻质量问题。

## 五、方法论洞察（本次最大价值）

**A/B 整体步数/成功率不是检测 skill 内容质量的可靠指标**——LLM 行为随机性 > skill 内容差异。要评判蒸馏版 vs 手写版 skill 质量，更可靠的是：

1. **静态对比**（关键省步指导的覆盖率、selector 准确性、内部一致性）
2. **回放分析**（读 `ab_*.json` 看 agent 在具体环节是否按 skill 指导操作，如封面上传是否用了 `upload_file` 直注）
3. **专项指标**（如"封面上传环节的步数/重试次数"，而非整体步数）

而不是整体成功率/步数——它被 LLM 随机性主导，样本量不足以稳定区分。

## 六、校正说明

本报告校正了 [distilled-skill-quality-2026-07-28.md](distilled-skill-quality-2026-07-28.md) 里基于 N=3 下的过强结论"蒸馏版缺 upload_file 直注 → treatment 绝对多走 ~6 步，质量问题主导"。N=5 显示该步数归因不成立（随机噪声）。**distilled 报告的静态对比部分（Q1-Q7 质量问题清单）仍然有效**，仅"步数归因"作废。

## 七、下一步建议

1. **复盘 t#1**：读 `ab_treatment_1.json` 看 9 步失败原因，判断是否 skill 内容误导（这是 treatment 不稳定信号的唯一硬证据来源）。
2. **修蒸馏版静态质量问题**（缺 upload_file 直注等 3 项）——为 skill 内容准确性而修，**但别指望它稳定改善 A/B 步数**。
3. **改用更可靠的评估方式**：回放分析 / 专项指标（封面上传环节），而非整体 A/B 步数。

## 八、副作用

本轮 10 个草稿，账号累计约 42 个测试草稿，需到草稿箱清理。
