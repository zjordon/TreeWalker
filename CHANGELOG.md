# Changelog

本项目的所有重要变更都会记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.8.0] - 2026-07-02

### Added

**历史重放（录制 / 重放 / 五级元素匹配 / 变量替换 / AI 摘要）—— 移植自 browser-use**（#98，#99）：把一次 Agent 运行录制为历史文件，之后用不同数据重放同一动作序列，重放不调决策 LLM，让一次昂贵的 LLM 决策成果可反复复用（批量回填表单、回归测试、自动验证成功）。复用已有底座（`DOMInteractedElement` / `compute_stable_hash` / 纯 dict action / `extract` 自包含），主要是「接线」而非从零造轮子。

- **数据模型 / 序列化**：`AgentHistory` 增 `interacted_element` / `metadata`；`save` / `load`；敏感数据脱敏（仅 input 类动作参数）；action 注册表版本号防漂移
- **录制改造**：`_finalize` 投影每步被交互元素 + 计时（`step_interval` = 上一步耗时）
- **五级元素匹配**：EXACT → STABLE → XPATH → AX_NAME → ATTRIBUTE（sha256 确定性，跨会话稳定）；同级多候选按「录制 bounds 中心就近」tie-break
- **重放执行器**：`rerun_history` / `_execute_history_step`；`extract` 重算；步间延迟（`max_step_interval` 封顶）；5 种跳过 / 重试（含菜单重打开）；SPA `wait_for_elements`
- **自动变量检测**（纯规则：属性 + 值模式两条策略）+ 精确整串替换
- **三层兜底 AI 摘要**（无截图适配：文本 + 执行统计判定，复用 `LLMClient.extract` 结构化输出）
- **重放文件根目录配置**：`AgentSettings.rerun_history_dir`（默认 `rerun-history`，env `AGENT_RERUN_HISTORY_DIR` 可覆盖）；`save_history` / `load_and_rerun` 只收相对路径，绝对路径 / `..` 越界一律 `ValueError`
- **集成入口**：编程 API + `examples/features/rerun_history.py` + CLI（`--rerun` / `--var`）+ TUI（`/rerun` 命令 +「录制」开关）
- **示例**：`rerun_history.py`、`douyin_upload_rerun.py`（手动替换映射）、`_debug_selectors.py`（排查「点错相似元素」）
- **设计文档**：`docs/rerun_history/`（README + 9 个专题，每节带 `文件:行号` 引用）
- **测试**：+72 用例；新模块覆盖率 87%

### Fixed

- **重放中途 done 截断**（#98）：外层循环不再因中途 `done` 截断（done 可能出现在录制中途，须忠实回放每一步）
- **抖音「点错合集下拉」**（#98）：同级多候选（哈希碰撞，如多个相似下拉触发器）此前取迭代顺序里靠前的错误元素，改为按录制 bounds 中心就近 tie-break
- **菜单重打开识别**（#98）：拓宽 `_is_menu_opener_step`（认 `aria-expanded` / `role=combobox` / 框架 class）+ 新增 `_is_option_element`（失败元素是 option 即触发重开，框架无关）
- **失败诊断增强**（#98）：列出同标签候选元素及其 `ax_name`，便于排查

## [0.7.0] - 2026-06-30

### Added

**示例库体系化移植自 browser-use**（覆盖入门 / 能力演示 / 进阶配置 / 用例 / 扩展机制，任务驱动调度内置动作）：

- **结构化输出示例**（#83）：examples/features/structured_output.py（output_model）、examples/use-cases/phone_price_comparison.py（跨站结构化对比）、examples/getting_started/data_extraction.py（任务驱动抽取）
- **浏览器能力演示示例**（#85）：multi_tab（navigate new_tab / switch_tab / close_tab）、save_as_pdf、scrolling_page
- **LLM 配置示例**（#87）：fallback_model（FallbackLLMSettings）、extraction_small_model（extract_llm）、fast_agent（flash 模式 + page_settle_timeout/wait_between_actions）
- **安全与扩展机制示例**（#89）：sensitive_data（扁平 sensitive_data 占位符往返）、custom_action（@tools.registry.action 注册范式）
- **文件落地示例**（#91）：download_file（下载文件作为 done 附件）、csv_generation（write_file 写 CSV，受 allowed_write_paths 约束）
- **并发多 agent 示例**（#93）：asyncio.gather 并发跑多个 agent
- **入门补全示例**（#95）：表单填写 / 多步任务

### Fixed

- **upload_file 抖音封面「不支持的文件格式」**（#96，#97）：4 个 accept 相同的 file input 唯一可区分的 class 被序列化白名单丢弃，模型盲选 ~50% 失败（选到 replace 触发「不支持的图片格式」）；三层修复——序列化保留 file input class + [File Inputs] 段输出角色 + 选到 replace 且确属 semi-upload 双 input 时软纠正到 primary hidden-input（不硬拒绝，避免 #36 的 0% 回归）；+13 用例
- **output_model 变体 B 参数校验**（#83）：output_model 给定时 done 应校验 StructuredDoneParams(data)，原先 _validate_action_params 误用静态 DoneParams(text)，导致 LLM 正确发出的 done(data=...) 被判非法、重试耗尽后 FAILED；改用注册表变体 B param_model，与执行路径共用 Tools._flatten_params；+6 回归测试
- **extract chunker 反孤岛**（#86，随 #87）：markdownify 把表格密集页（HN）压成单条 10K+ 行，_pack_units 无法把前面 nav 小块并入巨行，chunk0 只剩 240 字 nav 空壳喂给小模型；当前块 < max_chars/4 时并入下一块（略超 ≤1.25×）；+3 单测
- **下载跟踪缺 downloadPath**（#90，随 #91）：track_downloads 此前从未真正跑通，_setup_download_tracking 缺必填 downloadPath（CDP -32602）；显式 start(downloads_path=) > DOWNLOADS_PATH env > ~/Downloads，目录不存在则创建；+3 覆盖

## [0.6.0] - 2026-06-27

### Added

**工具优化阶段二**（对齐 / 超越 browser-use 完整能力：分页 / 落盘 / 白名单 / 句柄往返 / 结构化输出）：

- **extract 工具优化阶段二**（#67）：markdown 提取 + 分块分页 + 去重 + 大结果落盘 + 小模型默认化 + query 重命名 + inner timeout
- **search_page 工具优化阶段二**（#69）：offset 分页 + 大结果落盘 + 同源 iframe / 开放 shadow DOM 遍历 + 属性检索
- **find_elements 工具优化阶段二**（#71）：穿透 shadow/iframe + offset + 几何/visible + first_only + 大结果落盘 + backend_node_id/click-by-id
- **evaluate 工具优化阶段二**（#73）：args 注入 + 元素句柄往返 + per-call 超时 + userGesture/iframe + 图片通道 + 大结果落盘
- **write_file 工具优化阶段二**（#75）：原子写 + encoding 参数 + allowed_write_paths 白名单 + newline 翻译控制
- **replace_file 工具优化阶段二**（#76）：阶段二 + file_system 示例
- **read_file 工具优化阶段二**（#79）：offset/limit 分页 + 二进制嗅探 + allowed_read_paths 白名单 + 富文档 PDF/DOCX
- **done 工具优化阶段二**（#81）：files_to_display/attachments + downloads 自动附加 + 内联开关 + 结构化输出（output_model / 泛型 registry）

**P1 进阶**（find_text / dropdown_options / select_dropdown 读侧 G 系列 + 写侧 native 完整链）：

- **find_text P1 进阶**（#61）：G8-G11 能力
- **dropdown_options P1 进阶**（#63）：G7 读侧 + G9 + G5 进阶
- **select_dropdown P1 进阶**（#65）：ARIA/custom/combobox/子树 + 懒加载 + click 降级

## [0.5.0] - 2026-06-24

### Added

**工具优化阶段一**（统一为各工具接通 LLM / 封装 / 错误分级捕获 / 结构化回显 + 单测）：

- **extract 工具优化阶段一**（#42）：接通 LLM + 结构化输出 + 错误分级捕获
- **search_page 工具优化阶段一**（#44）：grep 式全文检索 + 封装 + 错误分级捕获
- **find_elements 工具优化阶段一**（#46）：CSS 选择器元素查询 + 封装 + 错误分级捕获
- **evaluate 工具优化阶段一**（#48）：任意 JS 执行 + type-aware 结果归一化 + 封装 + 分级错误
- **write_file 工具优化阶段一**（#50）：追加/换行控制 + 分级错误 + 字节回显
- **replace_file 工具优化阶段一**（#52）：换行修复 + 分级错误 + 软提示 + 计数回显
- **read_file 工具优化阶段一**（#54）：换行修复 + 分级错误 + 截断回显 + 空文件软提示
- **done 工具优化阶段一**（#56）：description 富化 + long_term_memory 回显 + 空 text 守卫
- **judge 复核器补全页面证据 + token 控制/截断保尾**（#58）

## [0.4.0] - 2026-06-21

### Added

- **switch_tab 工具完善**（#21）：成功回显 + 后缀冲突检测 + 轻量枚举 + 点击开新页自动切换
- **close_tab 工具完善**（#23）：成功回显 + 后缀冲突检测 + 轻量枚举 + 失效 target 软降级
- **scroll 工具完善**（#25）：成功回显 + 当轮到底提示 + 参数校验 + 异常软降级
- **send_keys 工具完善**（#27）：成功回显 + 异常处理 + 别名归一化 + 完整特殊键映射 + 文本逐字符分支 + 参数校验
- **find_text 工具完善**（#29）：CDP 文本搜索替换 window.find + 成功回显 + 软未找到回显 + 兑现 highlight + 修复 browser-use 4 个 bug
- **dropdown_options 工具完善**（#31）：修复范围 bug + tag 校验 + 成功回显 + json 编码输出
- **select_dropdown 工具完善**（#33）：修复范围 bug + tag 校验 + native 完整选择链 + 读回验证 + 点击回退 + 选项未命中软回显
- **screenshot 工具优化阶段一**（#39）：参数化 + 降采样 + 断路止血
- **save_as_pdf 工具优化阶段一**（#41）：参数化 + BrowserSession 封装 + 错误分级捕获

### Fixed

- **upload_file 抖音封面上传修复**（#35）：CDP label-gesture 上传链 + 多 file input 软警告
- **upload_file 抖音中文封面被拒 + Bilibili 封面假成功**（#37）：ASCII 临时名 + 延迟清理 + file-input 元数据

### Docs

- 新增 / 同步各工具完善方案文档：send_keys、find_text、dropdown_options、select_dropdown
- 修改基础示例提示词

## [0.3.0] - 2026-06-19

### Added

- **search：支持多搜索引擎**（#8）：baidu / google / bing / duckduckgo 四引擎可选
- **navigate 工具完善**（#10）：errorText 检查 + new_tab 新标签页导航 + 健康检查
- **go_back 工具完善**（#12）：历史缓存修正 + 健康检查 + 结果回显
- **click 工具完善**（#14）：坐标失败纠错 + 遮挡回退 + 视口对齐 + 结果回显
- **input_text 工具完善**（#16）：成功回显 + 聚焦失败纠错 + 值校验反馈 + 日期时间直写 + autocomplete 延迟
- **upload_file 工具完善**（#18）：成功回显 + 目标替换提示 + accept 类型软校验

### Fixed

- 可观测性测试中 `ActionResult(success=True)` 触发的 Pydantic 校验错误

### Docs

- 新增各工具完善方案文档：search 多引擎、navigate、go_back、click、input_text
- 修改 upload_file 示例提示词

## [0.2.0] - 2026-06-17

### Added

- **multi_act：一次执行多个浏览器动作**（#4）
  - Phase 1 核心循环：单次决策返回多个 action，顺序执行
  - Phase 2 静态守卫层：动作序列前置校验（非法 action、索引越界等）
  - Phase 3 运行时守卫层 + `_wait_for_page_settle`：动作间页面稳定等待
  - Phase 4 异常分流 + 失败语义 + `wait_between_actions`：单动作失败不影响后续，可配置动作间延时
  - prompt 软化 + 诊断日志，引导 LLM 真正使用多动作（#5）
  - `examples/multi_act_demo.py` 集成 demo

### Fixed

- **input_text 无法操作 Vue/React 控制的输入框**（#1）：逐字符 keyDown → char → keyUp 输入 + `_trigger_framework_events` 触发框架事件
- `_trigger_framework_events` 移除 `change`/`blur` 派发，避免触发框架副作用（如标签输入框 blur 时清空 value）
- **B 站标题输入框清空失效，旧文本未被清除导致追加**（#6）：完整复刻 browser-use 两层防线
  - `_clear_text_field` 三层清空策略：JS `select()+value=""` → 三击+Delete → Ctrl+A+Backspace
  - `type_text` 末尾拼接检测兜底：回读值 `endswith`/`startswith` 新文本且更长时，用 native setter 强制覆盖
  - 新增 22 个单元测试，新代码 100% 覆盖

### Docs

- multi_act 实现方案、browser-use vs TreeWalker 工具子系统对比评估、#6 修复方案文档
- CLAUDE.md 补充 Git 提交规则（不主动提交）
- README 增加致谢

## [0.1.0] - 2026-06-10

### Added

- Agent 核心 Sense-Think-Act 循环，通过 CDP 直连控制 Chrome 浏览器
- 5 阶段 Step Pipeline：准备上下文 → LLM 决策 → 执行动作 → 后处理 → 终结化
- 17 种浏览器动作（navigate、click、input_text、scroll、search、extract、send_keys、switch_tab、close_tab、wait、go_back、find_elements、find_text、screenshot、save_as_pdf、upload_file、evaluate）
- Textual TUI 交互界面，支持实时日志、暂停/恢复（Ctrl+C）、清屏（Ctrl+L）
- 命令历史持久化（`~/.treewalker/history.json`）
- 消息自动压缩（Message Compaction），支持超长任务不溢出
- 计划系统（Plan Manager），自动规划→执行→重规划
- Judge 评估器，任务完成后自动判断质量
- Fallback LLM 支持，主模型限流时自动切换备用模型
- 动作循环检测（Action Loop Detector），防止 Agent 重复无效操作
- 浏览器连接熔断器（Circuit Breaker），自动处理 CDP 断连
- 可观测性系统（EventBus + Metrics + JsonlRecorder + AnomalyDetector）
- 敏感数据过滤，API Key 等以占位符形式发送给 LLM
- 文件操作工具（read_file、write_file、replace_file、upload_file）
- 页面元素高亮反馈（交互高亮 + 点击反馈）
- Shadow DOM 文件上传支持
- 下拉框选项获取与选择（dropdown_options、select_dropdown）
- 下载追踪
- URL 缩短与还原，减少 LLM token 消耗
- Action Page Filters，按 URL 模式过滤可用动作
- 文本截断阈值可配置（extract_page、read_file、eval_result、display）
- 环境变量 + `.env` 文件集中配置
- CLI 入口 `tw-tui`，支持 `--task` 和 `--debug` 选项
- 编程接口（`Agent`、`LLMClient`、`BrowserSession` 可独立使用）
- 498 项单元测试

[0.7.0]: https://github.com/zjordon/TreeWalker/releases/tag/v0.7.0
[0.6.0]: https://github.com/zjordon/TreeWalker/releases/tag/v0.6.0
[0.5.0]: https://github.com/zjordon/TreeWalker/releases/tag/v0.5.0
[0.4.0]: https://github.com/zjordon/TreeWalker/releases/tag/v0.4.0
[0.3.0]: https://github.com/zjordon/TreeWalker/releases/tag/v0.3.0
[0.2.0]: https://github.com/zjordon/TreeWalker/releases/tag/v0.2.0
[0.1.0]: https://github.com/zjordon/TreeWalker/releases/tag/v0.1.0
