# Quirks — localhost

- **条件渲染依赖**：`select id=sales_report_order_statuses`（多选订单状态）只有在把 `select id=sales_report_show_order_statuses` 设为 `Specified` 之后才出现在 DOM 中（见 sales 阶段）。默认的 dashboard_1 阶段快照里没有该元素，不要提前找它。
- **表格单元格空洞**：结果表中部分行的 Orders 列 `<td>` 为空（如 5/2022、7/2022 行，订单数 25/28 显示在相邻 td），即数值列并非每行都填在固定 td —— 解析行时按非空数字 td 取 Orders 数，不能假设列索引固定。
- **提交是整页导航**：点击 Show Report 后 URL 变为 `/reports/report_sales/sales/filter/<base64>/`，需等页面加载后重新读 DOM，不要立即查询旧快照。