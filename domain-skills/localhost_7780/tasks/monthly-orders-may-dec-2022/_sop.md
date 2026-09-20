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
4. 读取结果表格（"records found" 下方）：每行 Interval（如 5/2022），取 **Orders 列（第 2 列）**——相邻的 Sales Items 列是销售件数不是订单数，两列数值量级相近，按位置裸读极易混列。读法：先按列头文本定位 Orders 列，再逐行取格；读后把各月值加和与表格 Total 行的 Orders 合计交叉校验，不一致 = 读错列，换列重读。2022 年 5–12 月 Orders 真值：05:8, 06:13, 07:9, 08:8, 09:10, 10:4, 11:5, 12:10（加和 67，与 Total 行一致）。卡外月份（如 2–4 月）须按同一列头规则自读，禁止沿用相邻列数值。
5. `done` 以 MM:COUNT 格式输出结果。