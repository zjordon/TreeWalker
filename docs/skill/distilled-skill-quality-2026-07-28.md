# 蒸馏版 skill 质量分析：手写 vs TreeForge 蒸馏（2026-07-28）

> ⚠️ **N=5 校正（2026-07-28）**：后续 N=5 测试 treatment 步数反比 baseline **少** 24%（20.4 vs 26.8），与本文 N=3 的 +37% 方向相反、互相抵消。**本文"质量问题导致 treatment 多走步"的步数归因结论已被推翻**——N=3 的步数差是随机噪声。蒸馏版的静态质量问题（下文 Q1-Q7 对比确认）确实存在，但**与 A/B 步数差不相关**。详见 [N=5 报告](bilibili-ab-test-n5-2026-07-28.md)。本文静态对比部分（Q1-Q7）仍然有效，仅"步数归因"作废。

> **TL;DR**：蒸馏版 treatment 步数比 baseline 多 37%（30.7 vs 22.3），经逐文件对比判定——**不是纯随机**。蒸馏版缺失手写版反复强调的「`upload_file` 直注」省步指导（单点最大问题），足以解释 treatment 绝对多走的 ~6 步；baseline 自身波动（32.7↔22.3）放大了"+37%"的相对值。质量问题 + baseline 波动叠加。

> 对比对象
> - **手写版**：`D:\temp\tree-walker\skill\manual\`（基于 TreeWalker 真实 DOM 验证）
> - **蒸馏版**：`domain-skills/member.bilibili.com/`（TreeForge 蒸馏产出，当前在用）
>
> 触发：[07-28 有效 A/B 报告](bilibili-ab-test-2026-07-28.md) 后，蒸馏版 N=3 显示 treatment 步数反增。

---

## 一、A/B 数据回顾（质疑来源）

| 指标 | 手写版 treatment | 蒸馏版 treatment | 蒸馏版 baseline |
|---|---|---|---|
| 平均步数 | 24.7 | **30.7（+6 步）** | 22.3 |
| 成功率 | 100% | 100% | 100% |
| 封面成功率 | 100% | 100% | 100% |

两组 treatment 都 100% 成功——差异在**效率**不在成败。问题：蒸馏版 treatment 多走 6 步，是 skill 内容质量问题，还是 baseline 随机？

## 二、逐文件对比

| 文件 | 手写版字符 | 蒸馏版字符 | 差 | 核心结构差异 |
|---|---|---|---|---|
| `_sop.md` | 2648 | 2680 | +1% | 蒸馏版按 capacity 切两段；手写版线性 6 步 |
| `selectors.md` | 4617 | 3099 | −33% | 蒸馏版多导航 selector；手写版多"旧 CSS 对照"表 |
| `quirks.md` | 4529 | 2784 | **−38%** | 手写版 9 条陷阱；蒸馏版只 4 类，**漏 upload_file 直注** |
| `api.md` | 1442 | 549 | −62% | 蒸馏版缺 success 页 URL |
| **合计** | **13236** | **9112** | **−31%** | 蒸馏版总量少 1/3 |

## 三、蒸馏版质量问题（按"致多步"影响排序）

### Q1【最关键】完全缺失「upload_file 直注」省步指导

手写版在 `_sop`/`quirks`/`selectors` **三个文件反复强调**：

> `**用 upload_file 直接注入文件，不要点击上传按钮**`（点击会弹 OS 文件选择框，TreeWalker 无法驱动）

蒸馏版**零提及** `upload_file`，只写"选择 input type=file...上传视频文件"（动词模糊，LLM 易解读为"模拟用户上传"）。

**致多步机制**：agent 失去硬约束 → 先尝试点击上传区 → 触发 OS 选择框失败 → 重试 → 回到直注。每次投稿多 2-5 步，量级对应 treatment +6 步。

### Q2 视频 file input selector 三文件内部矛盾

- `_sop.md`：选 `multiple=multiple`，**排除 `name=buploader`**
- `selectors.md`：`accept` 含 `.mp4`，`multiple=multiple`
- `quirks.md`：upload 阶段是 `multiple=multiple` 无 name；**publish 阶段才出现 `name=buploader`**

而手写版（真实 DOM 验证）：视频框就是 `name=buploader` + accept 含 `.mp4`，选 visible=True 的那个。蒸馏版三文件互相打架且都和实际矛盾，LLM 在不同阶段切换策略反复试错。

### Q3 缺「选 visible=True 防 decoy」

手写版：`TreeWalker DOM 有 [File Inputs] 段，选 visible=True 的那个（隐藏的常是 decoy，upload 报成功但页面不变）`。蒸馏版无此条 → agent 撞 decoy 后页面无反应再换试。

### Q4 缺「标题框时序」

手写版：标题 input 在封面编辑阶段不在 DOM，先完成封面再填标题。蒸馏版无 → agent 在封面阶段找不到标题框，wait/retry 多走 1-3 步。

### Q5-Q7 次要

- Q5：动词"上传"非命令式"直注"，未指向程序化工具调用
- Q6：按 capacity 切两段，LLM 读同类信息要在段间跳转，增加解析开销
- Q7：`api.md` 缺 `/platform/upload/video/success` URL，提交后无法靠 URL 确认成功

## 四、关键省步指导覆盖矩阵

| 陷阱 | 手写 | 蒸馏 |
|---|---|---|
| **用 upload_file 直注封面/视频** | ✅ 三文件强调 | ❌ **缺** |
| **选 visible=True 防 decoy** | ✅ | ❌ **缺** |
| **标题框时序（封面阶段不在 DOM）** | ✅ | ❌ **缺** |
| 简介 contenteditable div 非 textarea | ✅ | ✅ |
| 立即投稿/存草稿是 span 非 button | ✅ | ✅ |
| 同名 file input 靠 accept 区分 | ✅ | ⚠️ 用 multiple 区分（更模糊） |

5 项关键省步指导，蒸馏版**丢 3 项**。

## 五、蒸馏版的优势（公正）

1. **多了 `navigate-to-video-upload` capacity**：从创作者中心首页 → `id=nav_upload_btn` → "视频投稿" span 的完整导航（手写版假设直接从投稿页起）。
2. **发现 `id=nav_upload_btn` 稳定 id**（手写版未提）。
3. 结构紧凑，无手写版"与旧 CSS selector 对照"的冗余元说明。
4. `quirks.md` 提到"视频投稿是 span，其他投稿类型是 a"的细节（手写版无）。

## 六、定量分解：质量问题 vs 随机

- **绝对步数差**（手写 treatment 24.7 → 蒸馏 treatment 30.7 = **+6 步**）：两次 treatment 都 100% 成功、同 task 同 agent，绝对差可信。主要由 **Q1（upload_file 直注缺失）** 解释，Q2/Q3/Q4 加成。
- **相对值 +37%**：之所以夸张，是因为蒸馏版这次 baseline 只有 22.3 步（手写版那次 baseline 32.7 步）。baseline 自身波动 10.4 步（≈46%），抬高了相对差。即使蒸馏 treatment 和手写一样（24.7），在 22.3 的 baseline 下也会显示"+10%"。

**结论：不是纯随机。蒸馏版缺 `upload_file` 直注这条核心省步指导，足以让 treatment 绝对多走 ~6 步；baseline 这次恰好强，放大成 +37%。两者叠加。**

## 七、修复建议（最高 ROI，按优先级）

1. `_sop.md` + `quirks.md` + `selectors.md` 三处补回"**必须用 `upload_file` 直注、不能点击**"硬约束（最大单项收益）
2. `quirks.md` 补"选 visible=True 的那个，隐藏的是 decoy"
3. 统一视频上传框 selector 到 `name=buploader` + accept 含 `.mp4`（消解 Q2 内部矛盾）
4. `quirks.md` 补标题框时序（封面阶段不在 DOM）
5. `api.md` 补 `/platform/upload/video/success` URL

## 八、待验证（N=5）

本分析基于 N=3。下一步跑 **N=5（baseline 5 + treatment 5）** 用更大样本验证：
- 若 treatment 步数仍稳定高于 baseline → 进一步确认质量问题主导
- 若 baseline 再次大幅波动 → 量化 baseline 噪声幅度，校准"skill 真实效应"的检测门槛

N=5 结果将补充进 `bilibili-ab-test-2026-07-28.md` 或新建报告。
