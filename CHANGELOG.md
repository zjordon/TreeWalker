# Changelog

本项目的所有重要变更都会记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

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

[0.1.0]: https://github.com/zjordon/TreeWalker/releases/tag/v0.1.0
