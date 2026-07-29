# TreeWalker 路线图

> 基于 2026-07-18 战略转折后的方向梳理。从手工 record-replay 折腾中沉淀的认知，
> 转向「提升 agent 自动探索成功率 + skill 注入」为主线。
> 详见知识库 `ai/agent/manual-vs-agent-recording.md`。

---

## 战略转折（2026-07-18）

经过 TreeWalker 手工 record-replay 的深度折腾（抖音上传、B 站发视频等场景），得到判断：

**手工录制（直接当回放文件）的困难是"无限"的**——要适配整个 Web，每换站点/组件库都冒新坑。
**agent 录制的困难是"有限"的**——只要完成这次任务，且天然拥有手工录制要费力模拟的性质
（指纹同源 / 主动执行无时序劣势 / 无翻译层）。

转向后的主线：**提升 agent 自动探索成功率**，而不是继续打磨手工录制。
手工录制降为「简单场景的快路径」备选，agent 探索 + 重放成为「复杂场景的可靠路径」。

详见 `docs/user_recording/` 下的折腾记录和知识库 `manual-vs-agent-recording.md`。

---

## 已成熟的能力（资产，不再大改）

这些是折腾期间积累的成熟实现，下一阶段保留不动：

### 重放端（`src/tree_walker/agent/rerun.py`）—— 零改动

- [x] **五级元素匹配**（EXACT → STABLE → XPATH → AX_NAME → ATTRIBUTE → CLASS）
- [x] **stable_hash / ax_name 指纹**（跨会话稳定性，[[browser-accessibility-tree]]）
- [x] **等待机制阶段 1-4**（actionability 检查 + networkidle + 等目标元素 + step_interval 语义清理）
- [x] **变量替换**（`detect_variables` + `_substitute_variables_in_history`，[[browser-variable-substitution]]）
- [x] **extract 重算**（换数据后页面内容变，旧提取无意义）
- [x] **semantic_clue 兜底**（issue #129 三重兜底，定位失败时重放端重新定位）
- [x] **菜单重打开**（SPA modal 特化处理）
- [x] **三层摘要降级**（结构化 → 文本 → 纯计数）

重放端是 record-replay 折腾期间的最大资产，**无论录制方式怎样都保留**。

### 录制端（`src/tree_walker/recorder/` + `recording_extension/`）—— 保留作为简单场景快路径

- [x] MV3 扩展（WXT）采集层：10 类 DOM 事件
- [x] signal 模型 + 四阶段翻译管线（[[recorder-redesign-signal-translation]]）
- [x] upload_file 特殊处理（accept + xpath，重放端 `_resolve_file_input_by_accept`）
- [x] findInteractiveAncestor（对齐后端 is_interactive）

---

## 下一阶段计划

### P1 —— skill 注入机制（核心，对接 TreeForge）

**目标**：agent 探索时按域名读取 `domain-skills/<host>/` 的 skill 文件，注入上下文，提升探索准确率。

> 这是战略转折后的**核心方向**——不改 agent 逻辑，给 agent 喂知识。
> skill 来自 TreeForge 蒸馏（人工录制 → 蒸馏）或人工手写。
> skill 定位为「给 LLM 看的上下文提示」，不是「给 CDP 直接执行的结构化 selector 库」。

- [ ] **domain-skills 目录约定**
  - [ ] 项目根 `domain-skills/<host>/{_sop.md, selectors.md, quirks.md}`（三文件，api.md 已弃用）
  - [ ] `.gitignore` 处理（skill 是知识资产，可考虑提交或用户本地维护）
- [ ] **注入机制**（`src/tree_walker/agent/` 或 `browser/session.py`）
  - [ ] `goto_url` / `get_browser_state_summary` 时，按 host 读对应 skill 文件
  - [ ] 拼进 agent 的系统 prompt 或用户消息上下文
  - [ ] 注入时机：导航到新域名时注入，避免一次性灌所有 skill
  - [ ] skill 不存在时静默跳过（不强制要求每个站点都有）
  - [ ] 默认关闭开关 `enable_skill_injection`（仿 `enable_observability`），env `AGENT_ENABLE_SKILL_INJECTION`；未开启零行为变更
- [ ] **A/B 实测验证**（与 TreeForge P1 联动）
  - [ ] 手写一个真实站点 skill（推荐 B 站上传，基于 record-replay 已积累的知识）
  - [ ] 无 skill vs 有 skill 各跑 N 次，对比成功率/步数/耗时
  - [ ] 判据：成功率提升 ≥ 20pp 或步数减少 ≥ 30%
- [ ] **skill 失效处理**
  - [ ] skill 过时（站点改版）的检测（selector 失效标记）
  - [ ] skill 信息冲突时的优先级（agent 当前观察 vs skill 建议）

### P2 —— agent 自动探索可靠性提升

**目标**：提升 agent 探索本身的成功率，让"探索成功 → 自动录制 → 重放"的链路更可靠。

> 应用知识地图（[[browser-automation-knowledge-map]]）P0/P1 层的知识。
> 这些知识在 record-replay 折腾期间已沉淀，现在用在 agent 探索侧。

- [ ] **actionability 检查移植到探索端**
  - 重放端已有阶段 1-4 等待机制，探索端也该有（点击前 visible/stable/enabled 检查）
  - [[browser-wait-and-timing]] P1 层的应用
- [ ] **探索失败恢复**
  - [[state-aware-runtime]] 的状态机思路（候选/已提交边界）
  - 探索卡住时的回退/重试策略
- [ ] **探索多次尝试 + 选最优**
  - 接受"探索可以失败"，agent 跑 N 次挑最成功的一次存历史
  - 失败是常态，只要偶尔成功就有一条高质量回放文件
- [ ] **探索产物质量评估**
  - 探索成功后评估这条 AgentHistory 的质量（步数、是否有 retry 痕迹、是否走了弯路）
  - 低质量历史不存或标记

### P3 —— 手工录制优化（备选，按需）

**目标**：手工录制降为简单场景快路径后的性能优化。

> 战略转折后手工录制不再是主线，但对**简单流程**（纯表单/搜索/导航）仍有价值。
> 按 `docs/user_recording/recorder-performance-optimization.md` 优化，作为 agent 探索的补充。

- [ ] **阶段 1：剥离 LLM 文本**（`build_dom_state` 加 `skip_llm_text` 参数）
  - record-replay 的 `handle_event` 只用 selector_map，不用 element_tree_text/page_stats
  - 最低风险，立即收益
- [ ] **阶段 2：快慢路径折中**（核心性能优化）
  - 普通动作（click/input）走快路径只存轻量线索，不调 get_state
  - modal/file upload 等关键动作走慢路径实时算指纹
  - 停止时批量补指纹
- [ ] **阶段 3（可选）：selector_map 缓存**

### P4 —— 录制-重放体验打磨

**目标**：把已成熟的能力包装成好用的产品功能。

- [ ] **TUI 录制入口整合**
  - 当前 TUI 的 record-switch 录的是 agent 探索，扩展录制走 popup
  - 整合两种录制方式的入口
- [ ] **录制产物可视化编辑**
  - 录制完显示动作列表（每步：动作类型 + 目标元素描述 + 参数）
  - 支持删除误录步、合并/拆分步、改 input 的 text（手动指定变量）
  - [[browser-variable-substitution]] 建议的"自动检测 + 人工标注"混合
- [ ] **CSV 批量执行**
  - 录制一次喂 100 行数据跑 100 次（`for row in csv: load_and_rerun(variables=row)`）
  - [[browser-variable-substitution]] 的批量执行场景
- [ ] **`tree-walker record` CLI 子命令**（当前录制入口是 examples 脚本）

---

## 明确不做（战略转折后放弃）

- **手工录制作为复杂场景的主路径** —— 困难无限，已战略转折到 agent 录制
- **录制端继续换钥匙**（D1 → area_text → 下一个）—— `recorder-timing-solutions.md` 已论证治标不治本
- **砍掉 stable_hash 只存 xpath+attribute** —— 会重蹈 `cover-upload-fix-plan-v2.md` §二的 xpath 不可靠坑
- **重放端大改** —— 已成熟，零改动原则

---

## 里程碑速查

| 阶段 | 交付物 | 状态 | 备注 |
|---|---|---|---|
| 重放端成熟 | 五级匹配 + 等待机制 + 变量替换 | ✅ | 资产，零改动 |
| 录制端成熟 | signal 模型 + 翻译管线 + 10 类采集 | ✅ | 备选路径资产 |
| **P1** | **skill 注入机制 + A/B 验证** | ⏳ | **核心，对接 TreeForge** |
| P2 | agent 探索可靠性提升 | ⏳ | 知识地图应用 |
| P3 | 手工录制性能优化 | ⏳ | 备选，按需 |
| P4 | 录制-重放体验打磨 | ⏳ | 产品化 |

---

## 与 TreeForge 的协作关系

TreeForge（`D:\dev\git\z_jordon\treeforge`）是配套项目，产出 skill 文件供 TreeWalker 消费：

```
TreeForge：人工录制 → 蒸馏 → domain-skills/<host>/*.md
    ↓ 文件注入（TreeWalker P1 落地）
TreeWalker：agent 探索（带 skill 加持）→ AgentHistory → 重放
```

- **TreeForge P1**（手写 skill + A/B 验证）和 **TreeWalker P1**（skill 注入机制）是同一件事的两端
- 两个项目的 P1 验收标准一致：「有 skill 的 agent 探索成功率显著高于无 skill」
- TreeWalker 的 `domain-skills/` 目录约定要和 TreeForge 的 `adapters/treewalker_adapter.py` 输出路径对齐

详见 TreeForge `ROADMAP.md`。
