# Skill 注入 A/B 实测报告：B 站发视频（2026-07-27）

> ⚠️ **结论已作废（2026-07-27 更正）**
>
> A/B 期间 skill 目录建在 `domain-skills/www.bilibili.com/`，但示例 task 实际访问 `member.bilibili.com/platform/home`。加载机制是**精确 hostname 匹配**（`extract_host` → 完整 hostname 作目录名，无 eTLD+1 聚合），`member.bilibili.com ≠ www.bilibili.com` → **treatment 组全程未加载到 skill**，与 baseline 同状态。
>
> 因此下文「封面 60% vs 100%」「skill 在封面维度的收益」等结论**不成立**——那是 10 轮"无 skill"运行的随机噪声，不是 skill 效果。数据本身真实，可当作"无 skill 基线"参考。
>
> **根因**：冒烟脚本 `_smoke_skill_injection.py` 用 `www.bilibili.com` 的 URL 验证注入（匹配 → 假阳性），掩盖了"实际 agent 访问的 host 加载不到"的事实。A/B 前未用真实 task URL 验证注入。
>
> **修复（已执行，方向 1）**：skill 目录移至 `domain-skills/member.bilibili.com/`；冒烟脚本 URL 改为 `member.bilibili.com`。修复后需重跑 A/B 才能得到有效结论。

> 关联方案：`docs/skill-injection-design.md` §九（验证路径）
> 关联 issue：#141
> 数据来源：`examples/skill/ab_test_bilibili.py`（脚本）+ `ab_result.json` / `ab_result_n1.json`
> 回放文件：`rerun-history/ab_baseline_1.json`、`rerun-history/ab_treatment_1.json`

---

## 一、测试目标

验证 skill 注入机制（按 host 读 `domain-skills/<host>/` 注入 state message 的 `[Domain Skill]` 段）是否提升 agent 探索成功率。对应方案 §九的 A/B 判据：

- **成功率提升 ≥ 20pp**，或
- **步数减少 ≥ 30%**

## 二、测试设计

| 项 | 说明 |
|---|---|
| 任务 | 到 B 站创作者中心发视频，暂存为草稿（视频 + 封面 + 标题 + 分区 + 标签 + 简介 + 创作声明） |
| baseline | `enable_skill_injection=False`（默认关，即使 `domain-skills/` 在也不注入） |
| treatment | `enable_skill_injection=True` + `skills_dir="domain-skills"`（`domain-skills/www.bilibili.com/` 四文件就位） |
| skill 内容 | `_sop.md`（投稿流程）/ `selectors.md` / `quirks.md`（隐藏 file input、contenteditable、span 假按钮等陷阱）/ `api.md` |
| 环境 | Chrome 150（远程调试 9222）+ 智谱 LLM + 真实 B 站登录态 |
| 素材 | 视频 `D:\Videos\test\final\2026-04-29-20-41-59.mp4`、封面 `...\横封面.png`（均真实存在） |
| 样本 | 三次跑合计：baseline 5 轮、treatment 5 轮（N=1 × 2 + N=3 × 1） |

## 三、完整数据（每轮）

### baseline（skill off）

| 轮次 | 来源 | steps | 耗时 | done | judge success | 封面 |
|---|---|---|---|---|---|---|
| b-1 | N=1 第1次 | 20 | 183.5s | ✓ | true | ❌ 失败 |
| b-2 | N=3 #1 | 25 | 297.4s | ✓ | true | ✅ |
| b-3 | N=3 #2 | 21 | 265.7s | ✓ | true | ✅ |
| b-4 | N=3 #3 | 25 | 312.8s | ✓ | true | ✅ |
| b-5 | N=1 重跑 | 25 | 391.5s | ✓ | **false** | ❌ 失败 |

### treatment（skill on）

| 轮次 | 来源 | steps | 耗时 | done | judge success | 封面 |
|---|---|---|---|---|---|---|
| t-1 | N=1 第1次 | 19 | 155.1s | ✓ | true | ✅ |
| t-2 | N=3 #1 | 17 | 206.0s | ✓ | true | ✅ |
| t-3 | N=3 #2 | 36 | 443.4s | ✓ | true | ✅ |
| t-4 | N=3 #3 | 21 | 282.6s | ✓ | true | ✅ |
| t-5 | N=1 重跑 | 19 | 229.6s | ✓ | true | ✅ |

### 汇总指标（5 轮/组）

| 指标 | baseline | treatment | 差异 |
|---|---|---|---|
| 整体成功率（judge `success`） | 4/5 = 80% | 5/5 = 100% | +20pp |
| **封面成功率（客观，final_result）** | **3/5 = 60%** | **5/5 = 100%** | **+40pp** |
| 平均步数 | 23.2 | 22.4 | −3.4% |
| 平均耗时 | 290.2s | 263.3s | −9.2% |

## 四、判据达成

| 判据（方案 §九） | 结果 | 是否达标 |
|---|---|---|
| 成功率 ≥ +20pp | +20pp（80%→100%，judge 口径） | 边界达标（口径不稳，见下） |
| 步数 ≤ −30% | −3.4% | ✗ 未达 |

**整体结论：步数判据明确未达；成功率判据仅靠 judge 口径勉强踩线，不可靠。** 但"封面成功率"浮现了一个更强的信号（见下）。

## 五、关键发现：封面是 skill 价值最可能显现的点

三次跑合并后，最稳定的差异不在整体成功率或步数，而在**封面上传**：

- **baseline 封面成功率 60%**（5 次挂 2 次：b-1、b-5）
- **treatment 封面成功率 100%**（5 次全过）

失败模式高度一致——两次 baseline 封面失败的 `final_result` 都描述：

> `upload_file 能将文件设置到 DOM 的 file input 上，但 B 站的 R[eact 组件没有识别/未触发 change]`

而 skill 文件恰好有针对性指导（`_sop.md`：「用 `upload_file` 直接注入文件，不要点击上传按钮」；`quirks.md`：「隐藏 file input」陷阱）。treatment 组在这个具体难点上表现稳定，提示 **skill 对封面这类易错细节有稳定性帮助**。

## 六、原因分析与局限

### 为什么整体成功率/步数没显著差异

1. **任务对当前 LLM 不够难**：无 skill 的 agent 在"发草稿"主体流程上也能 5/5 `done`（视频/标题/分区/标签/简介都填对了）——智谱 LLM 看页面 DOM 就能处理。skill 的边际收益只在"LLM 搞不定的难点"上显现，而封面是少数这样的点之一。
2. **LLM 可能没充分参考 `[Domain Skill]` 段**：注入机制已验证工作（冒烟脚本确认 `[Domain Skill]` 真的进了 state message），但"注入了"≠"LLM 决策时用上了"。t-3 用了 36 步（异常多），提示即使有 skill 也可能走弯路。

### 局限（结论不能外推）

1. **样本小**：5 轮/组。封面 60% vs 100% 看起来明显，但 5 次里 2 次失败的置信区间很宽，不足以判定真实差异。
2. **judge success 口径波动**：b-1 和 b-5 都是封面失败，但 b-1 被判 `success=true`、b-5 被判 `success=false`——judge 判定本身不稳定，导致"整体成功率"指标不可靠。封面是更客观的口径。
3. **封面失败可能含时序随机性**：B 站 React 组件不识别 `setFileInputFiles` 可能与页面加载时序、组件挂载状态有关，未必完全由 skill 知识决定。
4. **未直接验证 LLM 是否读了 skill**：需要读回放日志看 agent 的 action 是否反映了 skill 内容（如是否真的"没点上传按钮而是 upload_file"）。

## 七、回放文件

| 文件 | 内容 |
|---|---|
| `rerun-history/ab_baseline_1.json` | b-5：一次 baseline 封面失败的完整探索（可复盘 agent 怎么卡在封面） |
| `rerun-history/ab_treatment_1.json` | t-5：封面成功的完整探索（对照） |

> 注：b-1 / N=3 各轮的回放**未落盘**（A/B 脚本早期版本没接 `save_history`，已丢失）。脚本现已修复（`examples/skill/ab_test_bilibili.py` 的 `run_once` 加了 `history_file` 参数），后续每次跑都会存 `ab_{group}_{n}.json`。

用法：

```python
# 直接读 JSON 看每步动作/结果（不跑浏览器）
import json
h = json.load(open("rerun-history/ab_baseline_1.json", encoding="utf-8"))

# load_and_rerun 重放（按录好的动作驱动浏览器，不调决策 LLM）
agent = Agent(task="", llm=llm, browser=browser, settings=AgentSettings())
await agent.load_and_rerun("ab_baseline_1.json")
```

## 八、结论与下一步

### 结论

在「B 站发草稿」这个任务 + 当前智谱 LLM 组合下：

- **整体成功率/步数**：skill 注入无显著改善（步数 −3.4% 远未达 −30% 判据；整体成功率 +20pp 靠 judge 口径踩线，不可靠）。
- **封面上传**：浮现 treatment 100% vs baseline 60% 的模式，是 skill 价值最可能显现的点，但 5 次样本不足以下定论。

即：**方案 §九的判据在本任务/本 LLM 下未稳健达标**，但封面维度的信号值得专项验证。

### 下一步建议（按方案 §九第 5 步「不达标则分析原因、调整后再验」）

1. **围绕封面做专项 A/B（N≥10）**：把任务缩到"上传视频 + 封面"（省时间），专测封面成功率，确认 60% vs 100% 是否真实。
2. **验证 LLM 是否真用 skill**：读 `ab_baseline_1.json` / `ab_treatment_1.json`，对比两组 agent 在封面上传环节的 action 序列，看 treatment 是否按 skill 指导操作（upload_file 而非点按钮）。
3. **换更难任务**：找一个无 skill 时 LLM 会失败的场景（如抖音上传、B 站某易错路径），skill 的整体收益才看得出来。
4. **固化 judge 口径**：当前 judge 对"封面失败算不算 success"判定波动，影响成功率指标可靠性。可改用客观口径（final_result 含「封面」成功/失败关键词）。

## 九、副作用

本次测试在 B 站账号共产生约 10 个测试草稿（N=1×2 + N=3×6 + N=1 重跑×2），需到创作者中心草稿箱手动清理。
