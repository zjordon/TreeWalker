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

> P1、P4、P5、P6 已完成；下一阶段主要方向 = **P7（WebArena 基准评测与短板反哺）**，远期布局 **P8（扩展化：浏览器扩展伴随形态，调研已完成）**；P2（进行中）、P3（备选）为既有项；P6 后续远期项（编排/定时/多 agent 等）见 `docs/p6/05` §3。

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

### P6 —— 交互前端从 TUI 迁往浏览器端（✅ 已完成）

**目标**：把 TUI（`tui/`）承载的功能迁移到浏览器端 web，提升复杂交付与交互的友好度。

> 全量交付见 PR #166（issue #162 关闭）：`tw-web` 一等 CLI（CDP 9223）承载 live agent 控制台（SSE 事件/日志/截图流 + 暂停/停止 + 录制轨迹）+ 流程库（编辑/重放/详情/CSV 批量）+ 技能面（分栏编辑 + 热更新）+ 直播视口（CDP screencast 连续推流）+ 设置面（注册表驱动）+ 侧栏「进行中」zone + 右 Context 面板 + ⌘K 命令面板 + 模型选择 + 任务历史（与 TUI 共享 `~/.treewalker/history.json`）。**TUI 并存保留**（原「迁移完下掉」决策修订，web 为主、TUI 为辅）。四批真机 e2e runbook 见 `docs/p6/03/07/09/12`（场景 A–R）。远期项（DOM 快照回放 / 编排 / 定时 / 多 agent）见 `docs/p6/05` §3 backlog。

- [x] **TUI 能力盘点与迁移规划**
  - `docs/p6/01` UI 框架提案（Flow 为中心的 IA + 注册表驱动 shell + 扩展插槽）
- [x] **浏览器端承接 TUI 能力**
  - `tw-web` CLI + web live 控制台；技能/设置走注册表模式（enabled 开关即出现，不改 shell）
  - 复杂交付（批量、实时进度、配置）统一走 web；TUI 并存保留

### P7 —— WebArena 基准评测与短板改进（🟡 进行中：首站点全量基线 + 失败归因已出）

**目标**：用 WebArena 标准 812 任务基准量化 TreeWalker 端到端浏览器操作能力，定位短板反哺改进。

> 评测工作空间是**独立项目** `D:\dev\git\z_jordon\evals\webarena`（不在本仓库，editable install 依赖 `tree_walker`）。
> 架构决策（**方案 A**）：TreeWalker 用原生 CDP 跑任务，WebArena 原版 evaluator **零改动复用**，
> 用 `CDPPageAdapter` 鸭子类型把 CDP 适配成 Playwright Page（`url`/`goto`/`content`/`evaluate` 四接口）+ worker 线程同步/异步桥接。
> 评测原理见工作空间 `docs/benchmark-principles.md`，首次跑通复盘见 `docs/setup-retrospective-2026-08-07.md`。
>
> **当前进度（2026-08-14）**：shopping_admin 站点 **184 任务全量跑完，总 SR = 34.8%**（GLM-5.2，
> 判分链路零异常、数字可信）；120 个失败任务逐个归因完成（9 类失败模式 + 5 个能力短板 + 改进建议）。
> 报告：`docs/shopping_admin-results-2026-08-14.md` / `docs/failure-analysis-2026-08-14.md`。

- [x] **smoke 链路打通**（10 任务子集，验证「链路通」而非跑分）
  - `CDPPageAdapter` + 同步/异步桥接（worker 线程跑 evaluator，`run_coroutine_threadsafe` + `wrap_future` 防死锁）
  - cookie 注入修复（CDP + localhost + domain 假成功坑，改用 `url` 参数）—— 首次 SR=0 的元凶
  - 4 个 setup bug 修复（pyproject 路径 / hatchling target / openai 2.x `openai.error` shim / cookie）+ 10 个 cdp_evaluator 单测全过
- [x] **shopping_admin 全量基线**（184 任务，2026-08-08~14 断点续跑）
  - 总 SR **34.8%**（GPT-4 论文参考 ~40-45%，GLM-5.2 达到合理区间）；判分 0 异常
  - 按 eval_type：string_match **53.4%** / program_html **17.2%** / url_match **14.3%**（三种 string_match 判分方式 50-56% 一致，无系统偏差）
  - 关键规律：**步数越多 SR 越低**——10-19 步 61.5% vs 30 步撞上限 7.1%；53% 任务落在 20-29 步「差一点没做完」
  - 判分链路 4 个工程修复（cookie url 参数 / 答案提取 / fuzzy_match 改 GLM / sys.path）
- [x] **失败任务归因分析**（120 个，LLM 分类 + 关键词回落，`analyze_failures.py`）
  - 查询类死于**数据获取与解读**（66%）、操作类死于**表单交互**（26%+20%），两类都受步数预算挤压（各 20%）
  - 五个具体短板：①分页数据不完整就下结论 ②多轮聚合超步数预算 ③Magento 表单对 CDP 输入不友好且无提交兜底 ④异步渲染网格没等/没滚误判无数据 ⑤REST API 探测 401 浪费步数
- [ ] **短板反哺改进**（本阶段的真正产出；按投入产出排序，来自失败归因）
  - 数据完整性自检规则（汇报前核对 grid total vs 提取行数，prompt 层改动，预计影响 ~15% 失败）
  - 批量提取优先策略（聚合型任务优先一次 JS 遍历+客户端聚合，~15%）
  - `submit_form` 原子动作（requestSubmit + change 事件序列，绕开模拟点击保存不可靠，~18%）
  - 异步渲染等待策略（Magento grid 类页面等 Knockout 渲染完成再提取）
  - 步数预算感知（剩余 <1/3 且任务未过半 → 切激进批量策略，避免 REST 探测烧步数）
- [ ] **扩展到全量 812 任务**
  - 补齐 shopping / gitlab / reddit（+可选 map / wikipedia）Docker 镜像与 cookie，断点续跑
  - 预计 ~1 周 + $300–500 LLM 费用；按站点 / eval_type 分组出 SR（`analyze_results.py` 已就绪）
- [ ] **模型交叉实验**（分离「架构差」与「模型差」）
  - 至少三组：TreeWalker+GLM / TreeWalker+Claude / browser-use+Claude
  - 只有同模型对比（TreeWalker vs browser-use，都接 Claude）才能把差距归因到 agent 架构本身
- [ ] **WebArena-Hard 258 子集**
  - 从 [ServiceNow/webarena-verified](https://github.com/ServiceNow/webarena-verified) 取 hard task_id 列表跑分，提升区分度
- [ ] **判分对齐清理**（已知技术债）
  - trajectory 转换仍是简化的（program_html 依赖中间步骤页面快照——SR 17.2% 偏低的部分原因；完整保存每步页面状态可提升可信度）
  - beartype 全局 monkey-patch（smoke 可接受，长期跑分建议精细化）
  - ~~exact_match 答案提取~~（已修，14 任务不再误判）/ ~~fuzzy_match 判分器~~（已改 GLM）

### P8 —— TreeWalker 扩展化：浏览器扩展伴随形态（💡 规划中：调研已完成）

**目标**：把 TreeWalker 的 agent 能力搬进浏览器扩展，以「伴随形态」触达 C 端用户——在用户**正在用的真实浏览器**里（已登录、已有书签）做副驾驶，与平台形态（CDP + Python，批量自动化 / 开发者场景）**双形态并存**，共用同一套 CDP 内核。

> 调研完成（2026-08-12，知识库 `ai/agent/browser-agent-extension-migration-research.md`）。
> 背景判断：独立「AI 浏览器壳」品类已被证伪（OpenAI Atlas 关停），但浏览器 Agent **能力层**被三巨头集体吸收（Google 原生内置 / Anthropic / OpenAI 扩展）——TreeWalker 只做能力层不碰壳子，扩展是绕不开的 C 端形态。
>
> **关键技术事实**：`chrome.debugger` 本质就是把 CDP 暴露给扩展用——与 TreeWalker 的 cdp-use **同一个协议**，只是连接对象从「自己启动的浏览器实例」换成「用户正在用的真实浏览器」。

**架构选型（已定）：模式 C —— CDP 网关桥 + Native Messaging**

```
Chrome 扩展（Web Store 分发）                TreeWalker Python 进程（本地）
  Side Panel UI（对话）                        Agent / AgentState / Tools
  Service Worker                               MessageManager / LoopDetector
    · chrome.debugger.attach/sendCommand  ←→   录制-重放 / Skill 注入
    · CDP 命令透传（= cdp-use 等价物）  native messaging（stdin/stdout）
                                              唯一改动：CDP 命令发往扩展桥
                    ↘ chrome.debugger 透传
                      用户真实浏览器（已登录、书签密码全保留）
```

- 核心判断：**Python agent loop（25 个动作闭包 / act 引擎 / MessageManager 压缩 / LoopDetector / 录制-重放 / skill 注入）是核心资产，不为上扩展用 JS 重写**——模式 C 只换「CDP 命令发往哪里」这一层连接，agent 一行不改
- 对比弃选：模式 A 纯扩展（agent loop 全重写 JS，投资回报最差）/ 模式 B 混合 remote-port（用户须特殊参数启动 Chrome，UX 致命）
- 附带好处：迭代快的部分（agent loop / prompt / 动作空间）全留 Python 端即改即生效；扩展只承载稳定的 CDP 桥 + UI，规避 Web Store 审核慢
- 已知限制：chrome.debugger attach 后浏览器顶部有「正在调试此标签页」黄条（行业通病，Anthropic/OpenAI 扩展同样有），UI 需提前引导；MV3 service worker 30s 休眠对模式 C 无影响（loop 在 Python 端）
- 开源参考：**WebBrain**（最完整纯扩展 agent，ax tree 同源 + token 压缩同类，强烈精读）/ **BrowserBee**（playwright-crx + 任务记忆=录制-重放同理念）/ **chrome-cdp-skill**（最薄的 CDP 网关，桥部分直接参考）

- [ ] **最小可行验证**：最小扩展跑通 `chrome.debugger.attach` + `sendCommand('Page.captureScreenshot')` / `DOM.getDocument`（「扩展拿到 CDP 数据」跑通，剩下就是接 Python 桥）
- [ ] **Native Messaging 桥**：注册 host（JSON 配置 + 可执行入口），扩展 ↔ Python 进程 stdin/stdout 通信；cdp-use 连接层改造支持「桥模式」
- [ ] **Side Panel UI**：对话界面 + 任务展示 + 高危操作确认（参考 Anthropic 安全模型：站点级权限 / 分类屏蔽 / 敏感操作前确认——prompt injection 是浏览器 agent 核心威胁）
- [ ] **Web Store 打包分发**（Python 端可打包安装，降低「要装 Python」摩擦）

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
| P6 | TUI → 浏览器端 | ✅ | PR #166 全量交付（#162 关闭）：tw-web live 控制台 + 流程库 + 技能面 + 直播视口 + 设置面 + T2 批次；TUI 并存保留；e2e A–R 全绿 |
| P7 | WebArena 基准评测与短板改进 | 🟡 | shopping_admin 184 全量 **SR=34.8%**（GLM-5.2，判分 0 异常）+ 120 失败归因（5 短板）；短板反哺 / 全量 812 / 模型交叉待做 |
| P8 | 扩展化：浏览器扩展伴随形态 | 💡 | 调研完成（2026-08-12）：模式 C（chrome.debugger=CDP + Native Messaging 桥，Python agent 零重写）；最小验证 / 桥 / UI / 上架待做 |

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
