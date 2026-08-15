# TreeWalker 路线图

> 基于 2026-07-18 战略转折后的方向梳理。从手工 record-replay 折腾中沉淀的认知，
> 转向「提升 agent 自动探索成功率 + skill 注入」为主线。
> 详见知识库 `ai/agent/manual-vs-agent-recording.md`。
>
> **更新（2026-08-01）**：skill 注入（v0.12.0，#141/#142）+ DOM 快照抽取到 dom-snapshot（PR #144）均已落地；
> 下一阶段核心转为 P2「agent 自动探索可靠性提升」。

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

这些是已成熟或已落地的实现，下一阶段基本保留不动（重放端 / 录制端零改动；skill 注入仅余 skill 失效处理待办；dom-snapshot 接入仅余 e2e 真机验收 + 测试迁移技术债）：

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

### DOM 快照抽取到 dom-snapshot（PR #144 已交付）

把 `src/tree_walker/browser/` 的「三源采集 + 五步过滤 → element_tree_text」（~3453 行，5 文件）抽到公共库 dom-snapshot（`D:\dev\git\z_jordon\dom-snapshot`，v0.1.0），TreeWalker agent 运行时与 TreeForge 采集层共享同一份快照实现：

- [x] **M2 核心抽取**（dom-snapshot 侧）：5 文件迁移 + 3 耦合点（`dom.py`↔`serializer.py` 循环依赖抽 `interactive.py` 破环 / `views.py` DOM+聚合混合剥离 pydantic / `CDPClient` 硬依赖→`CDPLikeClient` Protocol）
- [x] **M3 TreeWalker 接入**（#143/#144）：加 `dom-snapshot>=0.1.0` 依赖；`session.py` / `__init__.py` / `views.py` 改走 dom_snapshot；iframe target 工具用 dom-snapshot public 名（`attach_to_iframe_target` / `build_frame_target_map`）；删本地 4 文件，`views.py` 精简为聚合/重放类型 + re-export shim
- [x] **验证**：2103 测试全过，覆盖率 88%；serializer/dom_building/paint_order 等测试现跑 dom-snapshot 代码全过，`element_tree_text` 行为不变
- [ ] **bilibili 端到端**：待手动真机验收（需浏览器）
- [ ] **技术债**：8 个测 dom-snapshot 内部的测试 repoint 保留在 TreeWalker，待 dom-snapshot 补齐 serializer/paint_order 测试后迁移；TreeForge 采集层接入（其 P2.2）待做

---

## 下一阶段计划

> P1、P4、P5 已完成；下一阶段主要方向 = **P6（TUI→浏览器端）** 与 **P7（WebArena 基准评测）**；P2（进行中）、P3（备选）为既有项。

### P2 —— agent 自动探索可靠性提升（🟡 进行中：P0 已交付，P1-P3 暂缓）

**目标**：提升 agent 探索本身的成功率，让"探索成功 → 自动录制 → 重放"的链路更可靠。

> 应用知识地图（[[browser-automation-knowledge-map]]）P0/P1 层的知识。
> 这些知识在 record-replay 折腾期间已沉淀，现在用在 agent 探索侧。
> 完整方案见 [`docs/p3/01-探索可靠性提升方案.md`](docs/p3/01-探索可靠性提升方案.md)（P0 全细节 + P1-P3 分级设计）。

- [x] **actionability 检查移植到探索端**（方案 P0，PR #147 / issue #145 已交付）
  - 重放端已有阶段 1-4 等待机制，探索端也该有（点击前 visible/stable/enabled 检查）
  - [[browser-wait-and-timing]] P1 层的应用
  - ⏳ bilibili/douyin 真机 e2e 待手动验收；技术债：8 个测 dom-snapshot 内部的测试待迁移
- [ ] **探索失败恢复**（方案 P1，暂缓——设计方向见 docs/p3）
  - [[state-aware-runtime]] 的状态机思路（候选/已提交边界）
  - 探索卡住时的回退/重试策略
- [ ] **探索多次尝试 + 选最优**（方案 P2，暂缓）
  - 接受"探索可以失败"，agent 跑 N 次挑最成功的一次存历史
  - 失败是常态，只要偶尔成功就有一条高质量回放文件
- [ ] **探索产物质量评估**（方案 P3，暂缓）
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

### P4 —— 录制-重放体验打磨（✅ 已完成）

**目标**：把已成熟的能力包装成好用的产品功能。跟踪：issue #149。

> **录制能力（含 TUI 录制入口整合、扩展录制、`record` CLI 子命令）整体迁往 TreeForge 工程实现**，不在本仓库 P4 范围。

- [x] **录制产物可视化编辑**（#149/#150 已交付；#153 修复多 action 步骤的变量标注）
  - 录制完显示动作列表（每步：动作类型 + 目标元素描述 + 参数）
  - 支持删除误录步、合并/拆分步、改 input 的 text（手动指定变量）
  - [[browser-variable-substitution]] 建议的"自动检测 + 人工标注"混合
- [x] **CSV 批量执行**（#149 后端 `batch_rerun` + #155 Web UI：SSE 步级实时进度 + 协作式中止）
  - 录制一次喂 100 行数据跑 100 次（`for row in csv: load_and_rerun(variables=row)`）
  - [[browser-variable-substitution]] 的批量执行场景

### P5 —— 变量识别扩展到选择类动作（✅ 已完成）

**目标**：让下拉框、单选/复选等「选择即值」的动作也能被识别为变量、按行参数化替换。

> 原生 `<select>` 在 PR #157/#159 已通过 agent 路径解决（零 src 改动，靠 `select_dropdown` + 手工标注 + 变量替换）。P5 续（issue #160 / PR #161）补了自定义下拉（非原生 / 非 ARIA）的 agent 录制重放支持——`select_dropdown`/`dropdown_options` 闭态判型 miss 时兜底「open→discover→read/write」（真实 CDP click 选中），覆盖 B站（无 role `<li>`/`<div title>`）+ 抖音 Semi UI（portal `[role=option]` + 虚拟化）。另修了手工变量按 `(step/action/field)` 位置替换（避免同 `original_value` 撞 key）。方案 + 真机偏差见 [`docs/p5/06`](docs/p5/06-custom-dropdown-support-plan.md)。

- [x] **选择类动作变量识别**
  - 扩展 detect 到选择类动作，把「选中选项的值/标签」作为可替换变量
  - [[browser-variable-substitution]] 变量场景从「键入值」扩展到「选择值」
- [x] **自定义下拉支持（issue #160 / PR #161）**
  - `select_dropdown`/`dropdown_options` 闭态 miss → open→discover→read/write 兜底
  - 真实 CDP click option 选中（Semi UI 等 React 受控下拉只认 trusted click）
  - 精确→包含匹配（解「合集名 共N个作品」精确 miss）+ 虚拟化 scroll-until-found
  - B站/抖音 domain skill → `select_dropdown`；全量 2224 测过、真机 B站+抖音验证
- [x] **手工变量位置替换（PR #161）**
  - 按 `(step_number, action_index, field)` 位置精确替换，避免同 `original_value` 撞 key

### P6 —— 交互前端从 TUI 迁往浏览器端

**目标**：把 TUI（`tui/`）承载的功能迁移到浏览器端 web，提升复杂交付与交互的友好度。

> 当前 TUI（`tui/app.py`）对复杂工作流（多步编排、批量重放、结果可视化、配置）不够友好。`web_ui` 已验证浏览器端形态（可视化编辑 #149/#153 + CSV 批量重放 #155）。后续把 TUI 能力迁到 web，统一交互入口。

- [ ] **TUI 能力盘点与迁移规划**
  - 盘点 TUI 现有功能（运行 agent / 录制 / 重放 / 配置），确定迁 web 的范围与优先级
- [ ] **浏览器端承接 TUI 能力**
  - 扩展 web_ui 或新建 web 入口，承接 TUI 的运行/配置等交互
  - 复杂交付（多任务编排、批量、实时进度）统一走 web

### P7 —— WebArena 基准评测与短板改进（🟡 进行中：harness 已通 + 首站点基线已出）

**目标**：用 WebArena 标准 812 任务基准量化 TreeWalker 端到端浏览器操作能力，定位短板反哺改进。

> 评测工作空间是**独立项目** `D:\dev\git\z_jordon\evals\webarena`（不在本仓库，editable install 依赖 `tree_walker`）。
> 架构决策（**方案 A**）：TreeWalker 用原生 CDP 跑任务，WebArena 原版 evaluator **零改动复用**，
> 用 `CDPPageAdapter` 鸭子类型把 CDP 适配成 Playwright Page（`url`/`goto`/`content`/`evaluate` 四接口）+ worker 线程同步/异步桥接。
> 评测原理见工作空间 `docs/benchmark-principles.md`，首次跑通复盘见 `docs/setup-retrospective-2026-08-07.md`。
>
> **当前进度**：smoke harness 已建（`smoke_test.py`/`runner.py`/`cdp_evaluator.py`/`task_selector.py`/`analyze_results.py`），
> smoke 链路已验证通过（Task 0 端到端成功，2026-08-07）；shopping_admin 站点已跑出基线 **SR = 50%（28/56）**。

- [x] **smoke 链路打通**（10 任务子集，验证「链路通」而非跑分）
  - `CDPPageAdapter` + 同步/异步桥接（worker 线程跑 evaluator，`run_coroutine_threadsafe` + `wrap_future` 防死锁）
  - cookie 注入修复（CDP + localhost + domain 假成功坑，改用 `url` 参数）—— 首次 SR=0 的元凶
  - 4 个 setup bug 修复（pyproject 路径 / hatchling target / openai 2.x `openai.error` shim / cookie）+ 10 个 cdp_evaluator 单测全过
- [ ] **全量 812 任务跑分**
  - 去掉 task_selector 的 smoke 过滤，让 smoke_test 读全量任务列表（含断点续跑 resume）
  - 覆盖全部 5 站点（shopping_admin 已通；补 shopping / gitlab / reddit / map / wikipedia 镜像与 cookie）
  - 预计 ~1 周 + $300–500 LLM 费用；按站点 / eval_type 分组出 SR（`analyze_results.py` 已就绪）
- [ ] **模型交叉实验**（分离「架构差」与「模型差」）
  - 至少三组：TreeWalker+GLM / TreeWalker+Claude / browser-use+Claude
  - 只有同模型对比（TreeWalker vs browser-use，都接 Claude）才能把差距归因到 agent 架构本身
- [ ] **WebArena-Hard 258 子集**
  - 从 [ServiceNow/webarena-verified](https://github.com/ServiceNow/webarena-verified) 取 hard task_id 列表跑分，提升区分度
- [ ] **短板定位与反哺改进**（本阶段的真正产出）
  - 拉「TreeWalker 失败但 browser-use 成功」的任务逐条看 history，归因到具体能力缺口
  - 命中 P2（探索可靠性）/ 重放端 / DOM 快照 / 动作能力等改进点，回流成本 ROADMAP 条目
- [ ] **判分对齐清理**（已知技术债）
  - trajectory 转换是简化的（program_html 依赖中间步骤页面快照，目前只 string_match/url_match 准）
  - exact_match 45 任务的答案提取对齐（`runner.extract_concise_answer`：长汇报 → 精简答案）
  - beartype 全局 monkey-patch（smoke 可接受，长期跑分建议精细化）

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
| **P1** | **DOM 快照抽取到 dom-snapshot** | ✅ | PR #144 已交付（#143）；e2e 待真机 + 测试迁移技术债 |
| skill 注入 | 注入机制 + 三元门控 + B 站 skill | ✅ | v0.12.0 已交付（#141/#142）；skill 失效处理待办 |
| P2 | agent 探索可靠性提升 | 🟡 | P0 actionability 已交付（#147）；P1-P3 暂缓（见 docs/p3）；e2e 待真机 |
| P3 | 手工录制性能优化 | ⏳ | 备选，按需 |
| P4 | 录制-重放体验打磨 | ✅ | #149/#150 可视化编辑 + #153 多 action 变量 + #155 CSV 批量 Web UI（步级实时+中止） |
| P5 | 变量识别扩展到选择类动作 | ✅ | 原生 select #157/#159 + 自定义下拉 #160/PR#161（B站/抖音真机验证）|
| P6 | TUI → 浏览器端 | ⏳ | 复杂交互迁 web，统一交互入口 |
| P7 | WebArena 基准评测与短板改进 | 🟡 | smoke 通 + shopping_admin 基线 SR=50%；全量 812 + 模型交叉 + 短板反哺待做 |

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
dom-snapshot（公共库，TreeWalker 侧已接入 / TreeForge 侧待 P2.2）
  ├─ TreeWalker agent 运行时 ✅
  └─ TreeForge 采集层 ⏳
  共享「三源采集 + 五步过滤」DOM 快照实现
```

- **skill 链路（已打通）**：TreeForge 蒸馏 skill + TreeWalker 注入（#142）两端已对齐，验收标准一致——「有 skill 的 agent 探索成功率显著高于无 skill」（A/B：精简版 100% vs 原始版 80%）；`domain-skills/` 目录约定已与 TreeForge 的 `adapters/treewalker_adapter.py` 输出路径对齐
- **快照链路（TreeWalker 侧已打通）**：TreeWalker 已接入 dom-snapshot（PR #144），DOM 快照逻辑共享；TreeForge 采集层待其 P2.2 接入同一库

详见各项目 `ROADMAP.md`。
