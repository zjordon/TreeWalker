# 任务：查询 2022 年 5–12 月每月完成(Complete)订单数量（MM:COUNT 格式）

Magento Admin (http://localhost:7780/admin/admin/dashboard/) 上用 Orders Report 按月统计。

## 步骤

1. 在左侧菜单点击 `Reports`（`<a>` 可见文本 "Reports"，位于 li#menu-magento-reports-report 下），展开后点击子项 `Orders`（可见文本 "Orders"），进入 Orders Report 页面（http://localhost:7780/admin/reports/report_sales/sales/）。
2. 设置筛选条件（表单 `form#filter_form`）：
   - Period：`select id=sales_report_period_type name=period_type`，用 `select_dropdown(index, "Month")`。
   - From：`input id=sales_report_from name=from placeholder=mm/dd/yyyy`，用 `input_text(index, "5/1/22")`（日期格式 mm/dd/yy）。
   - To：`input id=sales_report_to name=to`，输入 `12/31/22`。
   - Order Status：先 `select_dropdown` `select id=sales_report_show_order_statuses name=show_order_statuses` 设为 `Specified`，此时才会渲染出多选 `select id=sales_report_order_statuses name=order_statuses[]`，再对它 `select_dropdown(index, "Complete")`。
3. 点击 `button id=filter_form_submit title="Show Report"`（可见文本 "Show Report"）。页面整页导航到 `/admin/reports/report_sales/sales/filter/<base64>/`。
4. 读取结果表格（"records found" 下方）：每行 Interval（如 5/2022）+ Orders 列即该月完成订单数。2022年5–12月结果：05:25, 06:13, 07:28, 08:18, 09:10, 10:11, 11:15, 12:10（总计 67）。
5. `done` 以 MM:COUNT 格式输出结果。