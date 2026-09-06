# 查询疑似欺诈订单（Suspected Fraud）

1. 进入订单列表页：从后台侧边栏点击 `Sales`（`a`，位于 `li id=menu-magento-sales-sales` 下），再点击展开的 `Orders` 链接，或直接 `navigate("http://localhost:7780/admin/sales/order/")`。页头出现 "Orders" 标题和 "Create New Order" 按钮即为到位。
2. 点击工具栏的 `Filters` 按钮（`button` 可见文本 "Filters"，与 "Default View" / "Columns" / "Export" 同排），展开筛选面板。
3. 在筛选面板中找到 Status 下拉（`select name=status`，位于 "Bill-to Name" / "Ship-to Name" 输入框之后、"Braintree Transaction Source" 输入框之前），执行 `select_dropdown(index, "Suspected Fraud")`。无需先点击打开下拉。
4. 点击面板底部的 `Apply Filters` 按钮（`button` 可见文本 "Apply Filters"，旁边有 "Cancel"）。
5. 等待网格刷新：页面显示 "Active filters: Status: Suspected Fraud"，"records found" 数量更新。读取结果表格行（ID / Bill-to Name / Grand Total / Status 列）；若显示 "We couldn't find any records." 则表示没有疑似欺诈订单。
6. 如结果超过一页，用底部分页控件翻页收集所有记录，完成后 `done(text, success)`。