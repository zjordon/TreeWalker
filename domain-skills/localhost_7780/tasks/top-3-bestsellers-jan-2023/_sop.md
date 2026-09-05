# 查询 2023 年 1 月 top-3 畅销产品

Magento admin（http://localhost:7780/admin/admin/dashboard/）流程：

1. 在左侧菜单点击 `Reports`（li id=menu-magento-reports-report 内的 `<a>`，可见文本 "Reports"），展开报表菜单。
2. 在展开的子菜单中点击可见文本为 "Bestsellers" 的 `<a>`，进入 Bestsellers Report 页面（/admin/reports/report_sales/bestsellers/）。
3. 设置 Period 为 Month：对 `select id=sales_report_period_type name=period_type`（title=Period，选项 Day|Month|Year）使用 `select_dropdown(index, "Month")`，无需先点击。
4. 设置日期范围：向 `input id=sales_report_from name=from`（placeholder=mm/dd/yyyy）`input_text` 输入 `1/1/23`；向 `input id=sales_report_to name=to` 输入 `1/31/23`。
5. 点击 `button id=filter_form_submit`（title=Show Report，可见文本 "Show Report"）提交筛选。页面会带筛选参数重新加载。
6. 结果表格按 Order Quantity 降序列出产品（列头：Interval / Product / Price / Order Quantity）。表格中数量列的值可能为空（见 quirks），按行顺序取前 3 行的 Product 名称即为答案（示例数据中前 3 为 Hawkeye Yoga Short-32-Blue、Impulse Duffle、Overnight Duffle）。必要时 `scroll` 查看全部行。
7. 用 `done(text, success)` 返回 top-3 产品名称。