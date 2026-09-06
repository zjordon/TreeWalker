# 查询 2022 年最畅销（销量第一）商品 — localhost Magento Admin

入口：`http://localhost:7780/admin/admin/dashboard/`（Admin 后台）。

**步骤 1：打开 Bestsellers 报表**
1. 在左侧主菜单点击 `Reports`（`li#menu-magento-reports-report` 下的 `<a>`，可见文本 "Reports"）。
2. 在展开的子菜单中点击可见文本为 "Bestsellers" 的 `<a>`，进入 Bestsellers Report 页面（`/admin/reports/report_sales/bestsellers/`）。

**步骤 2：设置筛选条件（按年、2022 全年）**
1. Period 下拉框：`<select id=sales_report_period_type name=period_type>`（选项 Day/Month/Year）。用 `select_dropdown(index, "Year")` 直接选择，无需先点击。
2. From 日期输入框：`<input id=sales_report_from name=from>`，`input_text` 填入 `1/1/22`（格式 mm/dd/yy）。
3. To 日期输入框：`<input id=sales_report_to name=to>`，填入 `12/31/22`。
4. 点击 "Show Report" 按钮：`<button id=filter_form_submit type=button>`。提交后页面跳转到带 filter 参数的 URL（base64 编码的查询串），表格重新渲染。

**步骤 3：读取结果**
- 提交后结果表格按 "Order Quantity" 降序排列；第一行数据行即为 top-1（本例为 2022 年 "Quest Lumaflex™ Band"，$19.00）。
- 若数量列在首屏 DOM 中为空，`scroll(1~5, down)` 向下滚动后表格内容会完整呈现。
- 读出第一行 Product 列的名称即可 `done("2022 年最畅销商品为 Quest Lumaflex™ Band", success=true)`。

注意：Dashboard 首页也有一个 "Bestsellers" 标签页（`a#grid_tab_ordered_products`），那是仪表盘小部件，不是本任务要用的报表；必须走 Reports → Bestsellers 菜单。