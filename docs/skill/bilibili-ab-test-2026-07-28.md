# Skill 注入 A/B 实测报告：B 站发视频（2026-07-28，有效）

> **TL;DR**：本次是 [07-27 报告](bilibili-ab-test-2026-07-27.md)（已作废）修正后的有效重跑。修了 host 不匹配（skill 目录 `www.bilibili.com` → `member.bilibili.com`）、加了 loader 日志、补了 `save_history`。**treatment 三轮每轮都确认注入了 skill（日志为证），方案 §九 判据达标（成功率 +33pp）**，封面维度差异最大（baseline 33% vs treatment 100%）。

> 关联方案：`docs/skill-injection-design.md` §九（验证路径）
> 关联 issue：#141
> 数据：`ab_result.json`（本次）、`ab_run_n3_valid.log`（全程日志，含 3 条 `skill loaded` 证据）
> 回放：`rerun-history/ab_baseline_{1,2,3}.json` + `ab_treatment_{1,2,3}.json`（6 个全存）

---

## 一、核心验证：skill 真注入了

loader 日志（`ab_run_n3_valid.log`）显示 treatment 组每轮首次访问 `member.bilibili.com` 时都加载了 skill：

```
treatment#1  INFO tree_walker.skills.loader: skill loaded: host=member.bilibili.com chars=7641 files=['_sop.md', 'selectors.md', 'quirks.md', 'api.md']
treatment#2  INFO tree_walker.skills.loader: skill loaded: host=member.bilibili.com chars=7641 files=[...]
treatment#3  INFO tree_walker.skills.loader: skill loaded: host=member.bilibili.com chars=7641 files=[...]
```

baseline 组开关关（调用点三元短路），三条都没有——符合预期。

这正是 07-27 作废报告缺的东西（那次 treatment 实际没注入，placebo）。本次用日志直接证明 skill 进入 state message。

## 二、测试设计

| 项 | 说明 |
|---|---|
| 任务 | B 站创作者中心发视频，暂存草稿（视频+封面+标题+分区+标签+简介+创作声明） |
| baseline | `enable_skill_injection=False`（默认关，不注入） |
| treatment | `enable_skill_injection=True`，skill 目录 `domain-skills/member.bilibili.com/`（四文件，已清理 www 首页残留） |
| 起点 | `member.bilibili.com/platform/home`（创作者中心，全程 member 子域） |
| 环境 | Chrome 150（9222）+ 智谱 LLM + 真实登录态 |
| 样本 | N=3/组 |

## 三、完整数据

### 每轮

| 轮 | steps | 耗时 | 封面 | judge success |
|---|---|---|---|---|
| baseline#1 | 43 | 600s | ✅（反复尝试才成功） | true |
| baseline#2 | 28 | 400s | ❌（"封面编辑器自定义文件处理，无法通过程序..."） | false |
| baseline#3 | 27 | 374s | ❌（"封面图片未能成功"） | true\* |
| treatment#1 | 20 | 267s | ✅ | true |
| treatment#2 | 21 | 278s | ✅（"已通过封面编辑器处理并确认"） | true |
| treatment#3 | 33 | 591s | ✅ | true |

\* baseline#3 封面失败却被 judge 判 success=true——judge 口径波动，故**封面成功率（客观口径）比 judge success 更可靠**。

### 汇总

| 指标 | baseline | treatment | 差异 |
|---|---|---|---|
| 成功率（judge） | 67% (2/3) | 100% (3/3) | **+33pp** |
| 封面成功率（客观） | **33% (1/3)** | **100% (3/3)** | **+67pp** |
| 平均步数 | 32.7 | 24.7 | −24% |
| 平均耗时 | 458s | 379s | −17% |

## 四、判据达成

| 判据（方案 §九） | 结果 | 达标 |
|---|---|---|
| 成功率 ≥ +20pp | +33pp（67%→100%） | **✓ 达标** |
| 步数 ≤ −30% | −24%（32.7→24.7） | ✗ 未达（接近） |

**方案 §九 判据在成功率维度达标。** 步数减少 24% 虽未到 30% 阈值，但方向明确。

## 五、关键结论

1. **封面是 skill 价值最大的点**：baseline 33% vs treatment 100%（+67pp）。baseline 卡在"B 站封面编辑器自定义文件处理机制，无法通过程序[注入]"——正是 skill `quirks.md` 讲的「隐藏 file input / 用 `upload_file` 直注、别点上传按钮」陷阱。treatment 用上 skill 知识稳定通过；baseline 摸索着失败（baseline#1 花 43 步反复试才成功，#2/#3 直接放弃）。
2. **步数明显减少**（−24%）：skill 让 agent 少走弯路（baseline 32.7 步 vs treatment 24.7 步）。
3. **整体成功率达标**（+33pp）：treatment 稳定 3/3，baseline 2/3（且其中一次 success=true 实为封面失败的宽松判定）。

## 六、局限（结论的边界）

1. **N=3 小样本**：3/3 vs 1/3、100% vs 33% 看着明显，但置信区间仍宽。封面维度要更稳的结论建议 N≥5。
2. **judge success 口径波动**：baseline#3 封面失败却判 success=true。本报告同时给 judge 口径和封面客观口径，后者更可靠。
3. **treatment#3 用了 33 步**（其余 20/21）：即使有 skill，封面处理阶段仍可能慢（等封面制作完成），不是每次都更快。
4. **任务难度**：B 站发草稿对当前智谱 LLM 主体流程不算难（baseline 也 2/3 done），skill 收益集中在封面等少数难点。换更难任务可能放大 skill 价值。

## 七、回放文件（6 个全存）

| 文件 | 内容 |
|---|---|
| `rerun-history/ab_baseline_1.json` | baseline#1：43 步反复尝试封面最终成功 |
| `rerun-history/ab_baseline_2.json` | baseline#2：封面失败（"封面编辑器自定义处理"）——**复盘无 skill 怎么卡封面**的最佳样本 |
| `rerun-history/ab_baseline_3.json` | baseline#3：封面失败 |
| `rerun-history/ab_treatment_{1,2,3}.json` | treatment 三轮：封面全成功（对照） |

用法：`Agent.load_and_rerun("ab_baseline_2.json")` 重放，或直接读 JSON 看 action 序列，对比 baseline vs treatment 在封面上传环节的行为差异。

## 八、与 07-27 报告的关系

[07-27 报告](bilibili-ab-test-2026-07-27.md)的结论已作废（host 不匹配 → treatment 没注入 → placebo）。本报告是修正后的有效结果。07-27 报告保留作为**「host 不匹配教训」的存档**（记录了精确 hostname 匹配机制下"skill 目录建错 host = 静默加载空"的陷阱，以及"验证注入用的 URL 必须 = 实际 agent 访问的 URL"的教训）。

本次相对 07-27 的修正：
1. skill 目录 `www.bilibili.com` → `member.bilibili.com`（agent 实际访问的 host）
2. `_sop.md` §1 改为从创作者中心进入；`selectors.md`/`api.md` 清理 www 首页残留
3. `SkillLoader` 加 INFO 日志（首次加载每个 host 打 `skill loaded` / `no directory`，便于确认注入 + 排查 host 不匹配）
4. A/B 脚本 `run_once` 加 `save_history`（每轮落盘回放）

## 九、副作用

本次在 B 站账号产生 6 个测试草稿（baseline 3 + treatment 3），累计约 16 个，需到草稿箱清理。
