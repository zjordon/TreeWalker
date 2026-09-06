# Quirks — localhost

- 日期输入框：点击后页面会注入一个日期选择控件（`refunded_2` 阶段 From 旁出现新的 `<button>` 且 From input 的 DOM 节点 index 变化）。直接用 `input_text(index, "1/1/23")` 写入并继续即可，不要与弹出的日历按钮交互；若 index 失效，重新读取 DOM 找 `id=sales_report_from`。
- 提交 "Show Report" 后是整页导航到带 base64 filter 参数的 URL，表格结果在新页面 DOM 中，需等待加载后再读取/滚动。
- Q1 范围（今天 3/15/2023）：From=1/1/23，To=3/31/23（季度末，不是今天）。