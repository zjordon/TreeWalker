# 蒸馏精简版 skill A/B（treatment×5，2026-07-28）

> **TL;DR**：蒸馏精简版 skill（TreeForge 蒸馏产出，2936 字符）treatment×5——**100% 成功、零异常、23.2 步**，和手写精简版持平。四版横向对比：**两个精简版都 100%（10 轮全成功），两个原始版都 80%（各有失败/异常）**。验证了"精简系统性更稳" + "TreeForge 蒸馏产出的 skill 可用"。

> 关联：
> - [skill-simplification-plan.md](skill-simplification-plan.md)（精简方案）
> - [bilibili-ab-test-simplified-n5-2026-07-28.md](bilibili-ab-test-simplified-n5-2026-07-28.md)（手写精简版 N=5）
> - [bilibili-ab-test-handwritten-n5-2026-07-28.md](bilibili-ab-test-handwritten-n5-2026-07-28.md)（手写版 N=5 + 四次总结）
> - [bilibili-ab-test-n5-2026-07-28.md](bilibili-ab-test-n5-2026-07-28.md)（蒸馏版 N=5）
> - [distilled-skill-quality-2026-07-28.md](distilled-skill-quality-2026-07-28.md)（蒸馏静态质量分析）
>
> 数据：`ab_result.json`（蒸馏精简版）、`ab_run_n5_distilled_simplified.log`、`ab_result_simplified_n5.json`（手写精简版备份）
>
> 注：本次按需求**只跑 treatment×5，不跑 baseline**（`ab_test_bilibili.py --n 5 --only-treatment`）。

---

## 一、蒸馏精简版 skill 简介

- **2936 字符**（TreeForge 蒸馏产出，用户手动放入 `domain-skills/member.bilibili.com/`）
- 三文件（`_sop` / `selectors` / `quirks`）
- TreeForge 蒸馏格式：capacity 切分（`upload-video-content`）、步骤编号、中英双语标题、selectors 表格
- 内容遵循精简原则，覆盖了 DOM 看不出的关键指导：
  - 多 file input 靠 `accept` 区分（不靠 name）
  - 封面 input 条件渲染（publish 阶段不在 DOM，要点"封面设置"触发 upload-conver 阶段）
  - 标签需 Enter 键事件
  - 全程 SPA 无 URL 变化，靠 DOM 内容判阶段
  - hidden file input 必须 `upload_file` 直注（click 触发 OS 弹窗）

## 二、treatment×5 数据

| 轮 | steps | 耗时 | 封面 | success |
|---|---|---|---|---|
| treatment#1 | 18 | 195s | ✅ | true |
| treatment#2 | 23 | 262s | ✅ | true |
| treatment#3 | 20 | 217s | ✅ | true |
| treatment#4 | 32 | 361s | ✅ | true |
| treatment#5 | 23 | 261s | ✅ | true |

**汇总：成功率 100% (5/5)，平均 23.2 步，259s，封面 5/5，零异常。**

## 三、四版 treatment×5 横向对比（核心表）

| skill 版本 | 字符数 | 来源 | 成功率 | 平均步数 | 异常轮 | 平均耗时 |
|---|---|---|---|---|---|---|
| 原始手写版 | 7013 | 人工 | 80% (4/5) | 27.0 | t#3 失败 | 355s |
| 原始蒸馏版 | 5052 | TreeForge | 80% (4/5) | 20.4 | t#1 失败 + t#2 异常慢 | 651s |
| 手写精简版 | 1793 | 人工（精简） | **100% (5/5)** | 22.0 | 零异常 | 305s |
| **蒸馏精简版** | **2936** | **TreeForge（精简）** | **100% (5/5)** | **23.2** | **零异常** | **259s** |

## 四、关键结论

1. **蒸馏精简版 = 手写精简版效果**：都 100% + 零异常，步数接近（23.2 vs 22.0），耗时相当（蒸馏版还略快 259s vs 305s）。两个精简版**无显著差异**。
2. **"精简 = 更稳"系统性验证**：两个精简版都 100%（**10 轮全成功**），两个原始版都 80%（各有失败/异常）。10 轮精简 vs 10 轮原始的对比比单次更可信——**精简（只留 DOM 看不出的决策指导）系统性提升了稳定性**。
3. **TreeForge 蒸馏产出的 skill 可用**：蒸馏精简版（TreeForge 自动产出）达到了手写精简版的水平——意味着 **TreeForge 蒸馏 + 精简 能产出可用的 skill，不依赖人工手写**。这对 TreeForge P1（蒸馏产出 skill）是关键验证。
4. **字符数在精简范围内不敏感**：手写精简 1793、蒸馏精简 2936，差 1143 字符但都 100%——只要遵循"只留 DOM 看不出的"原则，字数小范围波动不影响效果。

## 五、caution

- **N=5 小样本**：两个精简版各 100%（5/5），单看统计功效有限。但"精简 10 轮全成功 vs 原始 10 轮各 1 失败"这个模式比单次可信。
- **没跑 baseline**：无法算判据对比；但 treatment 稳定 100% 本身是好结果。

## 六、整个 skill 验证历程的最终结论

经过手写 → 蒸馏 → 手写精简 → 蒸馏精简 四版 + 多轮 A/B，最终结论：

1. **skill 注入机制代码就绪**：默认关闭、调用点门控、loader 日志、save_history、`--only-treatment` 都工作正常。
2. **skill 内容应"少而精"**：只留 DOM 看不出的决策指导（动作类型、时序坑、多候选选择、DOM 冲突澄清），删所有 DOM 已有的属性抄录。精简版系统性优于未精简版。
3. **TreeForge 蒸馏 + 精简 = 可用 skill**：蒸馏精简版达手写精简版水平，自动化产出可行。
4. **评估方法论**：用成功率（不用步数——步数是噪声），同 N 横向对比，N≥5；精简与否看"10 轮全成功 vs 有失败"这种模式，而非单次。
5. **skill 格式**：三文件（`_sop`/`selectors`/`quirks`，`api.md` 已弃用）。

## 七、副作用

本轮 5 个草稿，账号累计约 82 个测试草稿，需到草稿箱清理。
