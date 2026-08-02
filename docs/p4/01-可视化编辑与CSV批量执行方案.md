# P4 可视化编辑 + CSV 批量执行方案（issue #149 子任务 1+2）

> 状态：方案（已入库，待评审；代码实现待文档确认后进行）
> 范围：ROADMAP P4 / issue #149 剩余两项——①录制产物可视化编辑（含人工标注变量）②CSV 批量执行。两者都围绕**变量替换**，合并为一份方案。
> 承接：业界调研 `D:\dev\git\z_jordon\knowledge-garden\ai\agent\browser-variable-substitution.md`（三层模型 + 四建议）；底层 `detect_variables` / `load_and_rerun(variables=)` 已就绪。
> 关联：ROADMAP.md P4；issue #149；`docs/rerun_history/05-变量检测与替换.md`；`examples/features/rerun_history.py`。

## Context（为什么做）

issue #149 剩余两项。知识库把变量替换拆成业界三层模型，并给了 TreeWalker 四条建议——本方案正是其中两条的落地：

- **可视化编辑 ← 建议 3「自动检测 + 人工标注混合」**：业界（Playwright codegen / Selenium IDE / UiPath）在"哪些值该参数化"这步普遍**人工**；TreeWalker 的 `detect_variables` 自动检测是**领先做法**，但有两个边界——只识别有规律字段（email/phone/name/zip）、精确整串匹配漏子串。所以走「自动检测 + 人工补充」，而不是追求 100% 自动。
- **CSV 批量 ← 建议 4「数据源扩展到 CSV/表格」**：`for row in read_csv: load_and_rerun(file, variables=row)`，把"录制一次回放一次"升级成"录制一次批量执行 N 次"。

另两条建议用于框定边界：机制层（`variables=dict`）已对齐 Selenium `${var}` / UiPath In argument，**别折腾**；替换语义（只改 action 值、不动元素标识）**已踩对**。

## 业界经验锚点（来自知识库）

| 层 | 业界共识 | TreeWalker 现状 | 本方案作用 |
|---|---|---|---|
| 机制层：占位符 + 数据源 | `${var}` + CSV/Excel（Selenium）/ In argument + Queue（UiPath） | `variables=dict` 注入，值**不存进**录制文件 | 已对齐，不动 |
| 识别层：哪些值参数化 | 普遍**人工**（Playwright 不提取、Selenium 人工改） | `detect_variables` **自动检测**（领先但有边界） | 补「人工标注」入口 |
| 替换语义：值 vs 元素标识 | 只替换输入值，不动定位器 | 只改 `action.params`，不碰 `interacted_element` | 已对齐，编辑器须守住 |
| 何时替换 | 回放前一次性（简单） vs 运行时逐动作（批量更干净） | 回放前 `deepcopy` + `_substitute_variables_in_history` | 保持一次性（每行独立 deepcopy） |

**关键边界（自动检测固有的，本方案不试图消除）**：
1. 业务字段（商品名、订单备注、自定义字段）无规律，`detect_variables` 识别不了 → 人工标注补。
2. 精确整串匹配：`"alice"` 不替换 `"alice wang"` 里的 alice → 人工标注可绕过（直接指定 original_value）。

## 现状精确锚点（已核对真实代码）

| 维度 | 现状 | 文件:行 |
|---|---|---|
| 自动检测 | 纯规则无 LLM，模块级纯函数；只检 `text`/`query` 两字段；HTML type + 属性关键词 + 值正则三策略；去重 + 冲突后缀 | variable_detector.py:14-15,27-49 |
| `DetectedVariable` 结构 | `name/original_value/type="string"/format`，**无 text/url/file 类型区分** | views.py:226-232 |
| 替换机制 | 精确整串 `if value in replacements`，递归 dict/list，只改 `action.params`，`deepcopy` 不动原对象 | rerun.py:65-80,1457-1482 |
| `load_and_rerun` | `(history_file, variables=None, **kwargs)`，variables→`{original_value:new_value}`→deepcopy→替换 | rerun.py:273-293 |
| 单步结构 | `AgentHistory(step_number, model_output={"actions":[{"name","params"}]}, result, state_summary, interacted_element, metadata, screenshot_path=None)` | views.py:106-121 |
| 动作参数位置 | `model_output["actions"][i]["params"]`：input_text `{text,index,clear}` / click `{index}` / navigate `{url}` / done `{text,success}` | views.py:8-13,36-39,92-98 |
| 目标元素线索 | `interacted_element[i]`：node_name/attributes/xpath/element_hash/stable_hash/ax_name/bounds | rerun-history 示例 JSON |
| `AgentHistoryList` | `(history, action_registry_version)`；`save_to_file`(model_dump+脱敏+json) / `load_from_file` / `load_from_dict` | views.py:173-223,195-208 |
| **mutation API** | **零**（无 remove/edit/merge；仅 plan_manager 有无关的 update_from_model_output） | grep 全空 |
| 路径约定 | 相对 `rerun_history_dir`（默认 `"rerun-history"`），`resolve_rerun_path` 拒绝绝对/越界 | config.py:114; rerun.py:180-200 |
| TUI 布局 | Textual：Header/Footer + 双栏（AgentLog + events RichLog）+ 任务输入框 + record-switch | tui/app.py:117-142 |
| TUI 数据 widget | **无 DataTable/ListView**（全 RichLog 文本流）；未用多 Screen | tui/widgets.py:12-17,20-106 |
| TUI 拿 history | `_agent_worker` 跑完 `agent.run()`→`self._agent.history` 在内存（`_maybe_save_recording` 落盘），无展示入口 | tui/app.py:257-284 |
| CSV/批量代码 | **零**（src/ 命中仅"next batch"分页语境） | session.py; actions.py |
| 编辑 history 代码 | **零**（仅只读 save/load + deepcopy 替换，不改原对象/盘文件） | views.py:195; rerun.py:1457 |
| 重放单步定位 | 五级匹配 `_match_element_index`（TEXT→EXACT→STABLE→XPATH→AX_NAME→ATTRIBUTE→CLASS），命中写回 `params["index"]` | rerun.py:525-710,765-853 |
| input_text 重放 | 直接用 params.text（替换后），**text 不参与定位** | rerun.py:567,607 |

## 关键设计决策（推荐 + 备选 + 理由）

### D1. 可视化编辑载体：本地 HTTP 服务 + 浏览器 SPA（仿 Playwright Trace Viewer，已选）
- **选定**：新建独立编辑器后端（aiohttp `/history/*` 端点）+ 托管 React SPA，浏览器开 `http://127.0.0.1:<port>/editor`。动作列表表格 + 侧栏编辑表单（改 text / 标变量 / 删步 / 重排）+「detect 建议」「试跑 load_and_rerun」按钮。
- **业界调研依据**（2026-08-02，覆盖 Selenium IDE / Chrome Recorder / UiPath / testRigor / mabl / BrowserStack / Playwright / Cypress）：
  - **TUI 无先例**——业界录制器编辑 UI 全是图形（5 类：扩展 popup / DevTools 面板 / 桌面应用 / Web 应用 / IDE 扩展）；IDE 扩展这档甚至无人做表格编辑（产出皆代码）。
  - **本地 HTTP + 浏览器（形态 4b）= Playwright Trace Viewer 模式**，与 TreeWalker「本地 JSON + Python 后端」同构，适配度最高（扩展 popup 次之；DevTools/桌面/IDE 低）。
  - Cypress Studio 警示：in-app 编辑器「自负盈亏难」（两次重构）→ 编辑器应独立、薄、可演进。
- **关键约束**：编辑器后端**独立于录制 server**——录制（含 `recorder/server.py`）要迁 TreeForge；编辑器操作的是 TreeWalker 的 history（重放端资产），自成一个轻量服务（新建 `history_editor/server.py`），不依附 `recorder/server.py`。
- **复用**：aiohttp（项目已用）；`AgentHistoryList.load/save`、`detect_variables_in_history`、`load_and_rerun` 作端点后端；前端复用扩展的 WXT/React 工具链（已在仓库）。

### D2. 人工标注变量的存储：`AgentHistoryList` 内嵌元数据（推荐）
- **推荐**：`AgentHistoryList` 加 `manual_variables: list[ManualVariableBinding]` 字段（与 history 同生命周期、同文件落盘）。标注存的是"step i / action j / field → 变量名"的**元数据**，不是占位符值——与"变量值不存进录制文件"原则不冲突。
- **备选**：侧车文件 `<history>.vars.json`。
- **理由**：同生命周期避免文件失配；重放 `load_from_file` 后自动带标注，无需额外加载。代价：扩展模型 + 老 JSON 兼容（缺字段补 `[]`）。

### D3. 标注 → 替换路径：与 `detect_variables` 同构合并
- 标注产出 `{name: original_value}`（与 `DetectedVariable` 同构），重放时与自动检测的 `{name: original_value}` **并集合并**，再按 `variables[name]` 取 new_value 组成 `{original_value: new_value}` 喂 `_substitute_in_dict`。
- 好处：人工标注天然绕过"精确整串匹配漏子串"——直接指定 original_value，不依赖规则命中。

### D4. CSV 批量入口：`batch_rerun` 封装 + examples（必做），CLI 子命令可选
- **必做**：`Agent.batch_rerun(history_file, csv_path)` 封装（读 CSV → 逐行 `load_and_rerun(variables=row)` → 汇总）+ `examples/csv_rerun.py`（仿 `rerun_history.py`）。
- **可选**：编辑器 SPA 内「批量跑」按钮（上传 CSV → 调后端 `batch_rerun` 端点，配合编辑器标注完直接批量跑）。
- **可选（需另行决策）**：CLI `csv-rerun` 子命令——但这要求把 `cli.py` 从单命令改 `click.group`（见已废弃的 record CLI 方案）。**本期不强求**，避免又一次 CLI 改造；CLI 入口留待确有必要时再做。
- **理由**：与"录制迁 TreeForge、CLI 不改 group"的当前取向一致；核心能力（封装 + examples）不依赖 CLI。

### D5. CSV 执行模式：串行（推荐）
- **推荐**：N 行串行，共享一个 `BrowserSession`（每次 `load_and_rerun` 自带 start/stop）。
- **备选**：并发（需 N 个 BrowserSession + N 个 Agent，当前单例不共享）。
- **理由**：串行简单稳定，批量场景（批量注册/回填）对吞吐不敏感；并发留作后续优化。

### D6. CSV 列名约定：变量名 = 列头
- 列头 = `detect_variables.name` ∪ `manual_variables.name`（如 `email,phone,product_name`）。
- 同名冲突（`email_2`，来自 `_ensure_unique_name`）：列头即用去重后名字，CSV 行按列头注入。
- **未标注/未检测的值无法列化** → 须先在编辑器标注（这是 D2 的价值闭环）。

## 子任务 1：录制产物可视化编辑

### 能力清单
1. **展示动作列表**：每步一行——`step` / 动作类型 / 目标元素描述（`interacted_element[i].node_name` + `attributes.placeholder|aria-label|id` + `ax_name`）/ 参数（input_text 的 text、click 的 index…）。
2. **编辑操作**：
   - 删除误录步（`remove_step`）
   - 合并/拆分步（`merge_steps`；split 可选，首版可不做）
   - 改 input 的 text（`update_action_params`，**安全**：text 不参与定位）
3. **人工标注变量**：在任意 input_text 步上标"这个 text 是变量"，指定变量名 → 写入 `manual_variables`。

### 文件改动（按依赖序）

**`src/tree_walker/agent/views.py`（TAB 缩进）**
- 新增模型 `ManualVariableBinding(name, step_number, action_index, field="text", original_value)`。
- `AgentHistoryList` 加字段 `manual_variables: list[ManualVariableBinding] = []`（老 JSON 兼容：`load_from_dict` 缺键补 `[]`）。
- 新增 mutation API（**核心风险点：每个都要维护 `actions↔interacted_element` 等长按位对应**）：
  - `remove_step(step_number)`：删整步（不破坏其他步等长；⚠️ 删 click 步可能断后续定位链，UI 须提示）
  - `update_action_params(step_number, action_index, field, value)`：改 params[field]（text 安全）
  - `merge_steps(step_a, step_b)`：b 的 actions + interacted_element 追加到 a（顺序保持，等长自然维持）
  - `add_manual_variable(binding)` / `remove_manual_variable(name)`
- `save_to_file` 回写时显式传 `action_registry_version`（参考 `save_history` rerun.py:264，避免版本号丢失）。

**`src/tree_walker/agent/variable_detector.py` / `rerun.py`（TAB）**
- `load_and_rerun` 合并逻辑：把 `history.manual_variables` 的 `{name: original_value}` 与 `detect_variables_in_history` 的结果并集，再按传入 `variables` 组成 `{original_value: new_value}` 喂 `_substitute_variables_in_history`（现有替换路径零改动）。
- 新增纯函数 `merge_variable_sources(detected, manual) -> dict[str,str]`（name→original_value），便于单测。

**`src/tree_walker/history_editor/server.py`（新增，独立服务，TAB）**
- 独立 aiohttp 应用（**不复用 `recorder/server.py`**，因录制将迁 TreeForge）：端点 `/history/list`、`/history/load?name=`、`/history/save?name=`、`/history/detect?name=`、`/history/rerun?name=&variables=`。
- 内部复用 `AgentHistoryList.load_from_file/save_to_file`、`detect_variables_in_history`、`Agent.load_and_rerun`；路径校验复用 `resolve_rerun_path`（拒绝绝对/越界）。
- 静态托管前端 SPA 构建产物（`web.static` + SPA fallback）。
- 启动入口：`examples/serve_history_editor.py`（仿 `record_user_actions.py` 起 `run_app`；或后续按 record CLI 教训评估是否进 CLI）。

**`history_editor_ui/`（新增前端，React + Vite）**
- 动作列表表格 + 侧栏编辑表单（改 text / 标变量 / 删步 / 重排）+「detect 建议」「试跑」「保存」按钮；复用 `recording_extension/` 的 WXT/React 工具链经验。
- 构建产物输出到后端静态目录。

**`tests/test_history_editor_server.py`（新增）**
- 后端端点单测：aiohttp `TestClient(TestServer(app))`（仿 `test_recorder_server.py` 的 `_FakeBrowser` mock 模式）—— list/load/save/detect 端点；rerun 端点 mock `load_and_rerun`；路径越界拒绝。
- mutation API 单测放 `tests/test_history_edit.py`：remove/update/merge 后 `len(actions)==len(interacted_element)` 不变式；manual_variables 增删；save→load 往返不丢字段。
- 合并函数单测：detect ∪ manual，同名冲突。

### 风险与守卫
- **等长不变量**（最大风险）：`actions` 与 `interacted_element` 必须等长按位对应，否则重放 `_execute_history_step` `interacted[i]` 越界/错位。每个 mutation API 内部断言 + 单测守卫。
- **删 click 步**：可能破坏后续定位链（下拉打开、导航前置）。UI 标注风险 + 重放"菜单重打开"兜底（rerun.py:459-476）依赖 `previous_item`。
- **改 text 零风险**：text 不参与五级匹配。
- **编辑器与录制 server 解耦**：录制（`recorder/server.py`）将迁 TreeForge；编辑器后端须独立（`history_editor/server.py`），不 import / 不依赖 `recorder/`，否则迁移时被拖走。
- **前端构建流水线**：新增 React SPA 构建（WXT 工具链已在仓库，照搬）；aiohttp 静态托管注意 SPA 路由 fallback。

## 子任务 2：CSV 批量执行

### 能力清单
1. 读 CSV（列头 = 变量名）。
2. 逐行 `load_and_rerun(history_file, variables=row)`（串行）。
3. 结果汇总：每行 `success` / `completion_status` + 关键截图路径 + 失败步。

### 文件改动

**`src/tree_walker/agent/rerun.py`（TAB）**
- 新增 `async def batch_rerun(self, history_file, csv_path, **kwargs) -> list[BatchRowResult]`：
  - 用 `csv.DictReader` 读（列头即变量名）。
  - 校验：列头 ⊇ `merge_variable_sources(detect, manual)` 的 name 集（缺列 → warning 跳过该行或报错，决策见下）。
  - 逐行 `await self.load_and_rerun(history_file, variables=dict(row), **kwargs)`。
  - 每行用 `RerunSummaryAction` + `_generate_rerun_summary`（rerun.py:1486，复用）产 `BatchRowResult(row_index, success, summary, screenshot_path, error)`。
- 新增模型 `BatchRowResult`（views.py）。

**`examples/csv_rerun.py`（新增）**
- 仿 `examples/features/rerun_history.py`：构造 agent → `agent.batch_rerun("demo.json", "data.csv")` → 打印汇总表。

**`tests/test_batch_rerun.py`（新增）**
- mock `load_and_rerun`（AsyncMock）+ 临时 CSV → 断言每行 variables 注入正确、汇总结构对、缺列处理。

### 待定细节
- **列缺失策略**：CSV 缺某变量列 → (a) 该变量用 history 原值（跳过替换）或 (b) 整行报错。推荐 (a)（宽容，缺列=用原值），文档标注。

## 复用的现成构件（勿重写）

| 构件 | 路径 | 用途 |
|---|---|---|
| `AgentHistoryList.load_from_file` / `save_to_file` | views.py:211/195 | 编辑器读写（save 补 registry_version） |
| `resolve_rerun_path` | rerun.py:180-200 | 路径校验 |
| `detect_variables_in_history` | variable_detector.py:27 | 自动检测 + CSV 列名发现 |
| `_substitute_in_dict` / `_substitute_variables_in_history` | rerun.py:65/1457 | 替换（标注/CSV 共用） |
| `Agent.detect_variables` / `load_and_rerun` | rerun.py:267/273 | CSV 单行执行 API |
| `RerunSummaryAction` + `_generate_rerun_summary` | views.py:235 / rerun.py:1486 | CSV 每行结果汇总 |
| aiohttp `web.run_app` / `TestClient(TestServer)` | recorder/server.py:96; test_recorder_server.py | 编辑器后端服务 + 端点测试模式 |
| WXT + React 工具链 | recording_extension/wxt.config.ts | 前端 SPA 构建（照搬配置） |
| `examples/features/rerun_history.py` | examples/features/ | CSV 批量模板（line 79-86 换 data） |

## 验证

1. `uv run python -m pytest tests/test_history_edit.py tests/test_batch_rerun.py -v`
2. `uv run python -m pytest tests/ -x`（全量回归；尤其 rerun_history / variable_detector / views 序列化）
3. `uv run python -m pytest tests/ --cov=tree_walker.agent.views --cov=tree_walker.agent.variable_detector --cov-report=term-missing`（覆盖率 ≥85%）
4. 真机 e2e（手动）：
   - 可视化编辑：`uv run python examples/serve_history_editor.py` → 浏览器开 `http://127.0.0.1:<port>/editor` → 选一段含 input 的 history → 删一步 / 改 text / 标注变量 → 保存 → 点「试跑」（或 `tw-tui --rerun <file> --var name=新值`）验证替换生效。
   - CSV 批量：编辑标注变量后 → 喂 3 行 CSV `examples/csv_rerun.py`（或编辑器「批量跑」按钮）→ 验证 3 次重放分别用各行值 + 汇总正确。

## 不在本期范围

- `record` CLI 子命令 / TUI 录制入口整合 → 已移至 TreeForge（见 ROADMAP P4 注）
- CLI `csv-rerun` 子命令（需 `cli.py` 改 group）→ 留待确有必要
- 并发批量、运行时逐动作替换（知识库提到的更干净方案）→ 后续优化
- `DetectedVariable` 的 text/url/file 类型区分 → 当前 `type` 恒 "string"，CSV 不区分；若 upload_file 要批量，再扩展
