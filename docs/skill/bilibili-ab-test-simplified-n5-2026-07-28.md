# 精简版 skill A/B（treatment×5，2026-07-28）

> **TL;DR**：精简版 skill（1793 字符，只留 DOM 看不出的决策指导）跑 treatment×5——**100% 成功、零异常、22 步**，是三版 skill（手写/蒸馏/精简）中表现最好的。支持"精简 = 少困扰 LLM = 更稳定"的判断，是对 [精简重构方案](skill-simplification-plan.md) 的正向反馈。

> 关联：
> - [skill-simplification-plan.md](skill-simplification-plan.md)（精简方案：对照 model-input，删 56% 冗余）
> - [bilibili-ab-test-handwritten-n5-2026-07-28.md](bilibili-ab-test-handwritten-n5-2026-07-28.md)（手写版 N=5 + 四次横向对比）
> - [bilibili-ab-test-n5-2026-07-28.md](bilibili-ab-test-n5-2026-07-28.md)（蒸馏版 N=5）
>
> 数据：`ab_result.json`（精简版 treatment×5）、`ab_run_n5_simplified.log`、`ab_result_handwritten_n5.json`（手写版 N=5 备份）
>
> 注：本次按需求**只跑 treatment（skill on）×5，不跑 baseline**（baseline 历史数据已有：手写/蒸馏 N=5 baseline 都在 60–80%）。脚本是 `ab_test_bilibili.py --n 5 --only-treatment`。

---

## 一、精简版 skill 简介

- **1793 字符**（手写版 7013 的 26%，蒸馏版 5052 的 36%）
- 三文件结构（`_sop` / `selectors` / `quirks`，删了 `api.md`）
- 内容只留 **DOM 看不出的决策指导**：
  - `upload_file` 直注的原因（点击弹 OS 文件框）
  - 多个 file input 靠 `visible` 标志 + `accept` 区分（不靠 `name`，decoy 没有 name）
  - **DOM 数据冲突澄清**（顶部 index 对照 vs 底部 `[File Inputs]` 块的 visible 标志可能不一致，以顶部为准）
  - 标题框时序（封面阶段不在 DOM）
  - file input 动态新增、提交后整页跳转
- 删了所有 DOM 已有的属性抄录（placeholder/name/accept/tag/可见文本）和与 `[File Inputs]` 块重复的内容

## 二、treatment×5 数据

| 轮 | steps | 耗时 | 封面 | success |
|---|---|---|---|---|
| treatment#1 | 25 | 370s | ✅ | true |
| treatment#2 | 30 | 395s | ✅ | true |
| treatment#3 | 20 | 369s | ✅ | true |
| treatment#4 | 17 | 178s | ✅ | true |
| treatment#5 | 18 | 213s | ✅ | true |

**汇总：成功率 100% (5/5)，平均 22.0 步，305s，封面 5/5，零异常。**

## 三、三版 treatment 横向对比（都是 N=5）

| skill 版本 | 字符数 | 成功率 | 平均步数 | 异常轮 | 平均耗时 |
|---|---|---|---|---|---|
| 手写版 | 7013 | 80% (4/5) | 27.0 | t#3 失败 | 355s |
| 蒸馏版 | 5052 | 80% (4/5) | 20.4 | t#1 失败 + t#2 异常慢(30min) | 651s |
| **精简版** | **1793** | **100% (5/5)** | **22.0** | **零异常** | **305s** |

## 四、关键观察

1. **精简版是三者中最好的**：成功率最高（100%）、零异常、耗时最稳（178–395s，无 30 分钟异常慢轮）。
2. **支持精简方向**：手写版（复杂 7013 字符）和蒸馏版（含质量问题）各有失败/异常；精简版（只留 DOM 看不出的）反而最稳——**冗余信息会困扰 LLM、增加决策开销，精简后 LLM 专注关键指导，表现更稳定**。这和 [精简方案](skill-simplification-plan.md) 的"只写 DOM 看不出的"原则一致。
3. **步数适中**（22.0）：比手写版（27.0）少；蒸馏版（20.4）看似更少，但那是被 t#1 失败的 9 步（异常早退）拉低的，不是真实效率。精简版的 22.0 是"零异常下的真实步数"。

## 五、caution（结论的边界）

1. **N=5 小样本**：100% vs 80% 只是 1 轮差异，统计上不显著。精简版"零异常"也可能部分是运气。要强结论需 N≥10 或多轮重复。
2. **没跑 baseline**（本次只 treatment）：无法算"成功率提升 +20pp"判据；但 treatment 稳定 100% 本身是好结果。
3. **不能据此断言"精简版一定优于手写版"**：样本量不够。只能说"精简版这次表现最好，且符合精简原则的预期，是积极信号"。

## 六、结论：对精简重构方案的正向反馈

精简版（删 56% 冗余、只留 DOM 看不出的决策指导）在 treatment×5 中表现优于未精简的手写版和蒸馏版——**支持"skill 内容应少而精"的设计原则**：

- skill 不是越多越好，**冗余的 DOM 属性抄录会困扰 LLM**（增加 token、分散注意力、甚至引入和 DOM 冲突的描述）。
- 真正有价值的是 **DOM 看不出的元决策**（动作类型、时序坑、多候选选择、DOM 自身冲突的澄清）。
- 这验证了 [精简方案](skill-simplification-plan.md) 的对照方法论（对照 model-input，删 DOM 已有的、留 DOM 看不出的）。

后续若要强结论，建议：N≥10 重复精简版 treatment，并补 baseline 横向对比（精简 treatment vs baseline 成功率）。

## 七、副作用

本轮 5 个草稿，账号累计约 72 个测试草稿，需到草稿箱清理。
