# Changelog

本项目的所有重要变更都会记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

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

[0.5.0]: https://github.com/zjordon/TreeWalker/releases/tag/v0.5.0
[0.4.0]: https://github.com/zjordon/TreeWalker/releases/tag/v0.4.0
[0.3.0]: https://github.com/zjordon/TreeWalker/releases/tag/v0.3.0
[0.2.0]: https://github.com/zjordon/TreeWalker/releases/tag/v0.2.0
[0.1.0]: https://github.com/zjordon/TreeWalker/releases/tag/v0.1.0
