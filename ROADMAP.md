# TreeWalker 路线图

> 基于 2026-07-18 战略转折后的方向梳理。从手工 record-replay 折腾中沉淀的认知，
> 转向「提升 agent 自动探索成功率 + skill 注入」为主线。
> 详见知识库 `ai/agent/manual-vs-agent-recording.md`。
>
> **更新（2026-07-29）**：skill 注入机制已落地（v0.12.0，#141/#142）；下一阶段核心转为把 DOM 快照能力
> 抽取到公共库 dom-snapshot（`D:\dev\git\z_jordon\dom-snapshot`），让 TreeWalker + TreeForge 共享同一份快照实现。

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

## 已交付的能力（资产）

这些是已成熟或已落地的实现，下一阶段基本保留不动（重放端 / 录制端零改动；skill 注入仅余 skill 失效处理待办）：

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

### skill 注入机制（v0.12.0 已交付，#141/#142）

agent 探索时按 host 读 `domain-skills/<host>/` 注入上下文（默认关闭，env `AGENT_ENABLE_SKILL_INJECTION`）：

- [x] `extract_host`（url_utils.py）+ `SkillLoader`（按 host 读 `_sop`/`selectors`/`quirks`，per-host 缓存 + invalidate）
- [x] `Agent._build_skill_description` + 调用点三元门控（仿 `enable_sensitive_description`）
- [x] `build_state_message` 加 `[Domain Skill]` 渲染段；loader INFO 日志
- [x] skill 三文件约定（`api.md` 弃用）；B 站 skill（`domain-skills/member.bilibili.com/`）已入库
- [x] A/B 验证：精简版系统性 100% vs 原始版 80% —— skill 内容应「少而精」（只留 DOM 看不出的决策指导）
- [ ] **待办（skill 失效处理）**：skill 过时检测（selector 失效标记）+ 信息冲突优先级（agent 当前观察 vs skill 建议）

---

## 下一阶段计划

### P1 —— DOM 快照抽取到 dom-snapshot（下一阶段核心，跨工程）

**目标**：把 TreeWalker `src/tree_walker/browser/` 里「三源采集 + 五步过滤 → element_tree_text」这块（约 3453 行，5 文件）
抽成独立公共库 dom-snapshot（`D:\dev\git\z_jordon\dom-snapshot`），让 TreeWalker agent 运行时和 TreeForge 采集层共享同一份快照实现，消除格式漂移。

> 这块是 TreeWalker 最重的浏览器侧逻辑，agent 探索 / 录制 / 重放共用。
> 抽取是**纯重组、不改逻辑**，目标是 byte-for-byte 一致的 element_tree_text。
> 方案来源：treeforge `docs/p2/README.md` 第 3.1 节（P2.1）；dom-snapshot 自身 ROADMAP 已细化到 M1-M4。

抽取的 5 个文件：

| 源文件（TreeWalker） | 目标（dom-snapshot） |
|---|---|
| `browser/views.py`（DOM 部分） | `dom_snapshot/models.py` |
| `browser/cdp_timeout.py` | `dom_snapshot/cdp_timeout.py` |
| `browser/paint_order.py` | `dom_snapshot/paint_order.py` |
| `browser/dom.py` | `dom_snapshot/collector.py` |
| `browser/serializer.py` | `dom_snapshot/serializer.py` |

- [ ] **M2 核心抽取**（dom-snapshot 侧）：迁 5 文件 + 处理 3 个耦合点
  - `dom.py` ↔ `serializer.py` 循环依赖（抽 `interactive.py` 破环）
  - `views.py` DOM 核心 dataclass 与聚合状态模型混合（剥离 pydantic，拆 `models.py`）
  - `CDPClient` 硬依赖 → `CDPLikeClient` Protocol（鸭子类型解耦 cdp-use）
- [ ] **M3 TreeWalker 接入**（本仓库侧）
  - [ ] dom-snapshot 发版 0.1.0；本仓库 `pyproject.toml` 加 `dom-snapshot>=0.1.0`（本地 `uv pip install -e ../dom-snapshot` 开发）
  - [ ] 改 import：`session.py` 用 `from dom_snapshot import build_dom_state`；`step.py` / `system_prompt.py` / `tools/actions.py` / `recorder/recorder.py` 的 DOM 类型从 `dom_snapshot` 取，聚合状态类型留本地
  - [ ] 处理 `_attach_to_iframe_target` / `_build_frame_target_map`（提为 public 或 session 端重实现）
  - [ ] 删本地 5 文件；`browser/views.py` 只留聚合状态模型
- [ ] **验收**：全量测试通过 + agent 端到端跑 bilibili 投稿，快照与抽取前一致；**回滚**：revert import + 恢复本地 5 文件

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
| **P1** | **DOM 快照抽取到 dom-snapshot** | ⏳ | **下一阶段核心，跨工程** |
| skill 注入 | 注入机制 + 三元门控 + B 站 skill | ✅ | v0.12.0 已交付（#141/#142）；skill 失效处理待办 |
| P2 | agent 探索可靠性提升 | ⏳ | 知识地图应用 |
| P3 | 手工录制性能优化 | ⏳ | 备选，按需 |
| P4 | 录制-重放体验打磨 | ⏳ | 产品化 |

---

## 与 TreeForge 的协作关系

TreeForge（`D:\dev\git\z_jordon\treeforge`）是配套项目，产出 skill 文件供 TreeWalker 消费；
dom-snapshot（`D:\dev\git\z_jordon\dom-snapshot`）是两者的公共 DOM 快照库：

```
TreeForge：人工录制 → 蒸馏 → domain-skills/<host>/*.md
    ↓ 文件注入（TreeWalker skill 注入机制，v0.12.0 已落地）
TreeWalker：agent 探索（带 skill 加持）→ AgentHistory → 重放
```

```
dom-snapshot（公共库，下一阶段抽取）
  ├─ TreeWalker agent 运行时
  └─ TreeForge 采集层
  共享「三源采集 + 五步过滤」DOM 快照实现
```

- **skill 链路（已打通）**：TreeForge 蒸馏 skill + TreeWalker 注入（#142）两端已对齐，验收标准一致——「有 skill 的 agent 探索成功率显著高于无 skill」（A/B：精简版 100% vs 原始版 80%）；`domain-skills/` 目录约定已与 TreeForge 的 `adapters/treewalker_adapter.py` 输出路径对齐
- **快照链路（下一阶段）**：TreeWalker 把 DOM 快照逻辑抽到 dom-snapshot，TreeForge 采集层后续（其 P2.2）接入同一库

详见各项目 `ROADMAP.md`。
