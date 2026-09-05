# Generate Refund Report for Q1 (Magento admin @ localhost:7780)

目标：在后台 Reports → Refunds 页面设置日期范围 1/1/23 ~ 3/31/23 并生成报表。

## Step 1: 打开 Refunds 报表页
从 Dashboard (`/admin/admin/dashboard/`) 左侧菜单点击 `Reports`（`<a>` 可见文本 "Reports"，位于菜单 `li id=menu-magento-reports-report`），随后点击 `Refunds`（`<a>` 可见文本 "Refunds"）。进入 URL `http://localhost:7780/admin/reports/report_sales/refunded/`，页面标题 "Refunds Report"。

## Step 2: 填写日期范围
表单 `form id=filter_form` 内：
- `input id=sales_report_from name=from`（label "From"，placeholder mm/dd/yyyy）：`input_text(index, "1/1/23", clear=true)`
- `input id=sales_report_to name=to`（label "To"，placeholder mm/dd/yyyy）：`input_text(index, "3/31/23", clear=true)`

其他字段保持默认（Date Used=Order Created，Period=Day，Order Status=Any，Empty Rows=No）。

## Step 3: 提交
点击 `button id=filter_form_submit`（title="Show Report"，可见文本 "Show Report"）。页面导航到带 base64 filter 参数的 URL（`/admin/reports/report_sales/refunded/filter/cmVwb3J0.../`）——这是整页导航，不是 AJAX。

## Step 4: 读取结果
提交后 `scroll(amount, direction)` 向下翻页查看表格（列：Interval / Refunded Orders / Total Refunded / Online Refunds / Offline Refunds）。如显示 "We couldn't find any records." 则该区间无退款。之后 `done(text, success)`。