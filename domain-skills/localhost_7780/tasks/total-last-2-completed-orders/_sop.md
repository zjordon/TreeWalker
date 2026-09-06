# 获取最近 2 笔已完成订单的支付总额（localhost Magento 后台）

步骤 1：从 Dashboard（`http://localhost:7780/admin/admin/dashboard/`）左侧菜单点击 `Sales`（可见文本 "Sales" 的 `<a>`）展开销售菜单。

步骤 2：点击展开菜单中的 `Orders`（可见文本 "Orders" 的 `<a>`），进入订单列表页 `http://localhost:7780/admin/sales/order/`。

步骤 3：点击表格上方的 `Filters` 按钮（`<button>` 可见文本 "Filters"）展开筛选面板。面板中包含 Purchase Date / Grand Total / Purchase Point / ID / Bill-to Name / Ship-to Name / Status 等筛选字段。

步骤 4：在 `Status` 下拉框（`<select name=status>`，选项含 Canceled|Closed|Complete|Suspected Fraud 等 12 项）中选择 `Complete`。直接调用 `select_dropdown(index, "Complete")` 即可，无需先点击下拉框。

步骤 5：点击 `Apply Filters` 按钮（`<button>` 可见文本 "Apply Filters"）。列表将刷新并只显示状态为 Complete 的订单（如 000000230 Ava Brown $93.40、000000256 Adam Garcia $89.00）。

步骤 6：列表默认按 Purchase Date 降序排列，取前 2 行订单的 `Grand Total (Purchased)` 列金额相加即为答案（本例 $93.40 + $89.00 = $182.40）。若筛选后结果超过一页，注意分页控件（"of 2" 页码）确认前 2 笔在第 1 页。完成后 `done(text, success)` 报告总额。